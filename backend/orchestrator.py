"""The Orchestrator — owns the incident lifecycle and routes between agents.

It is deliberately thin: it constructs the incident, advances the state machine
between detection phases, and hands the shared RunContext to each specialized
agent in turn. The agents do the reasoning; the orchestrator owns the *when* and
the *state*. The Remediation agent owns the approval-gate transitions itself
(AWAITING_APPROVAL -> REMEDIATING/REJECTED) because only it knows the gate
outcome.

This mirrors an ADK orchestrator-with-sub-agents topology: one coordinator, six
scoped workers, explicit hand-offs — not one monolithic prompt.
"""
from __future__ import annotations

import logging

from backend.agents.base import Deps, RunContext
from backend.agents.comms import CommsAgent
from backend.agents.correlation import CorrelationAgent
from backend.agents.diagnosis import DiagnosisAgent
from backend.agents.memory import MemoryAgent
from backend.agents.remediation import RemediationAgent
from backend.agents.triage import TriageAgent
from backend.models import Alert, Incident, IncidentStatus

log = logging.getLogger("aegisops.orchestrator")


class Orchestrator:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        # Instantiated once; each is stateless across incidents (state lives on
        # the RunContext/Incident), so reuse is safe.
        self.triage = TriageAgent()
        self.diagnosis = DiagnosisAgent()
        self.correlation = CorrelationAgent()
        self.memory = MemoryAgent()
        self.remediation = RemediationAgent()
        self.comms = CommsAgent()

    async def handle_alert(self, alert: Alert) -> Incident:
        # Keep the demo scenario temporally fresh (bad deploy ~12 min ago) so
        # Correlation confidence reflects a just-shipped regression.
        from backend.seed.seed_data import refresh_demo_timeline
        refresh_demo_timeline(self.deps.storage)

        incident = Incident(status=IncidentStatus.DETECTED, service=alert.service, alert=alert)
        self.deps.storage.save_incident(incident)
        ctx = RunContext(incident, self.deps)
        await ctx.emit(
            "incident_created", service=alert.service,
            alert=alert.model_dump(), status=incident.status.value,
        )

        try:
            # --- Detection phases: orchestrator advances the state machine ---
            await self.triage.run(ctx)
            await ctx.transition(IncidentStatus.TRIAGED)

            await self.diagnosis.run(ctx)
            await ctx.transition(IncidentStatus.DIAGNOSED)

            await self.correlation.run(ctx)
            await ctx.transition(IncidentStatus.CORRELATED)

            # Memory informs the decision but does not move the state machine.
            await self.memory.run(ctx)

            # --- Action phase: Remediation owns the approval-gate transitions ---
            await self.remediation.run(ctx)

            # --- Comms always runs (resolved OR rejected) to close the loop ---
            await self.comms.run(ctx)

            # Closed-loop learning: a resolved incident becomes institutional memory.
            from backend.tools.memory import learn_incident
            learn_incident(self.deps.storage, incident)

            await ctx.emit("done", status=incident.status.value)
        except Exception as exc:  # noqa: BLE001 — never let the demo hard-crash
            log.exception("incident %s failed", incident.id)
            # Best-effort move to FAILED so the UI shows a clean terminal state.
            try:
                incident.status = IncidentStatus.FAILED
                self.deps.storage.save_incident(incident)
                await ctx.emit("agent_error", agent="orchestrator", error=str(exc))
                await ctx.emit("done", status=incident.status.value)
            except Exception:  # noqa: BLE001
                pass
        return incident
