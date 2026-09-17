"""Remediation Agent — propose a fix, gate on a human, then execute (simulated).

This agent owns the governance handshake (spec §2). It never touches infra until
a human resolves the approval gate: it transitions the incident to
AWAITING_APPROVAL, physically opens the asyncio gate, emits `approval_required`,
and *blocks* on `gate.wait_for`. Only on an explicit human "yes" does it move to
REMEDIATING and run the ordered, clearly-simulated executor.

The chosen action is grounded in the Correlation + Memory findings — for a
bad-deploy regression the correct, reversible fix is a rollback to the suspect's
known-good target, and we prefer whatever fix history says worked before.
"""
from __future__ import annotations

from backend.agents.base import BaseAgent, RunContext
from backend.guardrails import ApprovalDecision
from backend.models import IncidentStatus, now_ms
from backend.tools import remediation as R

SYSTEM = (
    "You are the Remediation agent of an autonomous SRE system. Given the "
    "correlated root cause and any historical fix, choose ONE reversible action "
    "from the catalog and justify it. For a bad-deploy regression the correct "
    "action is 'rollback' to the known-good version. Respond ONLY with JSON."
)


class RemediationAgent(BaseAgent):
    name = "Remediation"
    version = "1.0.0"
    allowed_tools = ["remediation_planner", "approval_gate", "executor"]
    scope = "Propose a reversible fix, gate on human approval, execute (simulated)"
    headline = "Proposing a fix (human gate)"

    async def execute(self, ctx: RunContext) -> None:
        service = ctx.incident.service
        correlation = ctx.incident.findings.get("correlation", {})
        memory = ctx.incident.findings.get("memory", {})
        suspect = correlation.get("suspect") or {}
        rollback_target = suspect.get("rollback_target")
        mem_match = memory.get("match") or {}
        typical_fix = mem_match.get("typical_fix")

        # 1. Decide the action, grounded in correlation + memory -------------
        prompt = f"""Incident on '{service}'.
Probable cause: {correlation.get('probable_cause', 'unknown')} (confidence {correlation.get('confidence', 0.0)}).
Suspected bad deploy: {suspect.get('service')} {suspect.get('version')} -> rollback target {rollback_target}.
Memory recommendation: {memory.get('recommendation', 'none')}.
Historically effective fix for this fingerprint: {typical_fix or 'none recorded'}.

Action catalog: rollback (redeploy last known-good), scale_out (add replicas),
restart (rolling pod restart), flag_off (disable feature flag).
Prefer the historically-effective fix when one is recorded.

Return JSON:
{{"action": "rollback|scale_out|restart|flag_off",
  "rationale": "one crisp sentence on why this action resolves the incident",
  "reasoning": "1 sentence"}}"""

        decision, _ = await ctx.think(
            self.name, "plan", prompt, system=SYSTEM, response_json=True
        )
        action = str(decision.get("action", "rollback")).strip().lower()
        # Prefer a proven historical fix over the model's pick when we have one.
        if typical_fix and typical_fix.strip().lower() in R.ACTION_CATALOG:
            action = typical_fix.strip().lower()
        if action not in R.ACTION_CATALOG:
            action = "rollback"

        rationale = decision.get(
            "rationale", f"{action} the offending change on {service}."
        )

        # 2. Build the concrete plan -----------------------------------------
        plan = R.build_plan(action, service, rollback_target, rationale)
        await ctx.tool(
            self.name, "remediation_planner",
            f"build_plan(action={action}, service={service}, target={rollback_target})",
            plan.model_dump(),
        )
        ctx.incident.remediation_plan = plan
        ctx.deps.storage.save_incident(ctx.incident)
        ctx.remember("remediation", {
            "action": plan.action,
            "target": plan.target,
            "risk": plan.risk,
            "reversible": plan.reversible,
            "requires_approval": plan.requires_approval,
            "rationale": plan.rationale,
            "reasoning": decision.get("reasoning", ""),
        })

        # 3. Governance handshake --------------------------------------------
        #    The state machine forces every remediation through AWAITING_APPROVAL;
        #    only destructive plans actually block on a human.
        await ctx.transition(IncidentStatus.AWAITING_APPROVAL)

        if plan.requires_approval:
            # Physically open the gate and BLOCK until a human resolves it.
            ctx.deps.gate.open_gate(ctx.incident.id)
            await ctx.tool(
                self.name, "approval_gate",
                f"open_gate({ctx.incident.id}) — destructive '{action}' needs a human yes",
                {"requires_approval": True, "risk": plan.risk},
            )
            await ctx.emit("approval_required", agent=self.name, plan=plan.model_dump())
            gate_decision = await ctx.deps.gate.wait_for(ctx.incident.id)
        else:
            # Non-destructive → policy auto-approves; no human blocked.
            gate_decision = ApprovalDecision(
                approved=True, approver="auto-policy",
                note="non-destructive action; no human gate required",
            )

        # 4a. Rejected / timed-out → close as REJECTED, do NOT touch infra ---
        if not gate_decision.approved:
            await ctx.emit("rejected", agent=self.name, approver=gate_decision.approver)
            await ctx.transition(IncidentStatus.REJECTED)
            return

        # 4b. Approved → execute the plan (clearly simulated) ----------------
        ctx.incident.approved_by = gate_decision.approver
        ctx.deps.storage.save_incident(ctx.incident)
        await ctx.emit("approved", agent=self.name, approver=gate_decision.approver)
        await ctx.transition(IncidentStatus.REMEDIATING)

        result = await R.execute_remediation(plan, service)
        for step in result.steps:
            # Each ordered step lands on the live timeline, tagged simulated.
            await ctx.emit(
                "exec_step", agent=self.name, label=step.label,
                ok=step.ok, simulated=step.simulated, detail=step.detail,
            )
            await ctx.tool(
                self.name, "executor", f"step: {step.label}",
                {"ok": step.ok, "simulated": step.simulated, "detail": step.detail},
            )

        # Stamp resolution time and promote the execution record.
        ctx.incident.resolved_at = now_ms()
        ctx.deps.storage.save_incident(ctx.incident)
        resolution_minutes = round(
            (ctx.incident.resolved_at - ctx.incident.detected_at) / 60000.0, 1
        )
        ctx.remember("execution", {
            "ok": result.ok,
            "action": result.action,
            "target": result.target,
            "simulated": result.simulated,
            "approved_by": gate_decision.approver,
            "steps": [
                {"label": s.label, "ok": s.ok, "simulated": s.simulated, "detail": s.detail}
                for s in result.steps
            ],
            "resolution_minutes": resolution_minutes,
        })

        await ctx.transition(IncidentStatus.RESOLVED)
        await ctx.emit("resolved", agent=self.name, resolution_minutes=resolution_minutes)
