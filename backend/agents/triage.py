"""Triage Agent — classify severity, service, blast radius, on-call routing.

Pattern reference for the other agents:
  1. Run deterministic tools to gather hard signals.
  2. Stream each tool call via ctx.tool(...).
  3. Ask Gemini (via ctx.think, response_json) to synthesize a judgement,
     grounded in those signals.
  4. Reconcile model output with the deterministic rubric, promote durable
     facts with ctx.remember(...), and set incident columns.
"""
from __future__ import annotations

from backend.agents.base import BaseAgent, RunContext
from backend.models import Severity
from backend.tools import triage as T

SYSTEM = (
    "You are the Triage agent of an autonomous SRE system. You classify a "
    "production incident's severity (SEV1 highest .. SEV4 lowest), confirm the "
    "affected service, and describe blast radius crisply. You are given hard "
    "signals from deterministic tools; trust them and explain your reasoning "
    "in one or two tight sentences. Respond ONLY with JSON."
)


class TriageAgent(BaseAgent):
    name = "Triage"
    version = "1.0.0"
    allowed_tools = ["severity_classifier", "service_resolver"]
    scope = "Classify severity, affected service, blast radius, on-call routing"
    headline = "Classifying severity & blast radius"

    async def execute(self, ctx: RunContext) -> None:
        alert = ctx.incident.alert
        assert alert is not None

        # 1. Deterministic signals -------------------------------------------
        info = T.resolve_service(alert.service)
        await ctx.tool(
            self.name, "service_resolver",
            f"resolve_service({alert.service})",
            {"tier": info.tier, "downstreams": info.downstreams,
             "traffic_share": info.traffic_share, "known": info.known},
        )

        err = T.parse_error_rate(alert.error_rate)
        rubric_sev = T.classify_severity(err, info.tier)
        blast = T.estimate_blast_radius(info, err)
        oncall = T.oncall_for(alert.service)
        await ctx.tool(
            self.name, "severity_classifier",
            f"classify_severity(error_rate={err}%, tier={info.tier})",
            {"rubric_severity": rubric_sev, "blast_radius": blast, "oncall": oncall},
        )

        # 2. Gemini synthesis, grounded in the signals -----------------------
        prompt = f"""Incident alert: {alert.alert} on service '{alert.service}'.
Measured error rate: {err}%.
Service tier: {info.tier} (0 = most critical). Traffic share: {int(info.traffic_share*100)}%.
Downstream dependencies at risk: {', '.join(info.downstreams) or 'none'}.
Deterministic rubric severity: {rubric_sev}.
Blast radius estimate: {blast}

Return JSON:
{{"severity": "SEV1|SEV2|SEV3|SEV4",
  "service": "{alert.service}",
  "blast_radius": "one-sentence blast radius",
  "oncall": "{oncall}",
  "reasoning": "1-2 sentences justifying the severity"}}"""

        decision, _ = await ctx.think(
            self.name, "classify", prompt, system=SYSTEM, response_json=True
        )

        # 3. Reconcile: the rubric is the floor — never let the model soften a
        #    tier-0 SEV1 below the deterministic classification.
        model_sev = str(decision.get("severity", rubric_sev)).upper()
        final_sev = model_sev if _sev_rank(model_sev) <= _sev_rank(rubric_sev) else rubric_sev

        ctx.incident.severity = Severity(final_sev)
        ctx.incident.service = alert.service
        ctx.incident.blast_radius = decision.get("blast_radius", blast)
        ctx.remember("triage", {
            "severity": final_sev, "service": alert.service,
            "tier": info.tier, "blast_radius": ctx.incident.blast_radius,
            "oncall": decision.get("oncall", oncall), "error_rate_pct": err,
            "reasoning": decision.get("reasoning", ""),
        })
        ctx.deps.storage.save_incident(ctx.incident)
        await ctx.emit(
            "triage_result", agent=self.name, severity=final_sev,
            blast_radius=ctx.incident.blast_radius, oncall=decision.get("oncall", oncall),
        )


def _sev_rank(sev: str) -> int:
    # Lower rank = more severe, so min() picks the worse severity.
    return {"SEV1": 1, "SEV2": 2, "SEV3": 3, "SEV4": 4}.get(sev.upper(), 4)
