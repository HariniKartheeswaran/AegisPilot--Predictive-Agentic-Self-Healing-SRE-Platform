"""Remediation tools — plan construction + a CLEANLY SIMULATED executor.

Honesty rule (spec §12): we do not have a real cluster to roll back, so the
executor is an explicit simulation — it runs real, ordered steps with real
state changes to the incident, and every returned step is tagged
`simulated=True`. It never pretends to have touched prod infra.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from backend.models import RemediationPlan

# The reversible action catalog with intrinsic risk. `rollback` is the standard
# fix for a bad-deploy regression and is fully reversible.
ACTION_CATALOG: dict[str, dict] = {
    "rollback": {"risk": "low", "reversible": True, "destructive": True,
                 "desc": "Redeploy the previous known-good version"},
    "scale_out": {"risk": "low", "reversible": True, "destructive": False,
                  "desc": "Add replicas to absorb load"},
    "restart": {"risk": "medium", "reversible": True, "destructive": True,
                "desc": "Rolling restart of the service pods"},
    "flag_off": {"risk": "low", "reversible": True, "destructive": False,
                 "desc": "Disable the offending feature flag"},
}


def build_plan(
    action: str, service: str, rollback_target: Optional[str], rationale: str
) -> RemediationPlan:
    meta = ACTION_CATALOG.get(action, ACTION_CATALOG["rollback"])
    if action == "rollback" and rollback_target:
        target = f"{service} -> {rollback_target}"
    else:
        target = service
    return RemediationPlan(
        action=action, target=target, risk=meta["risk"],
        reversible=meta["reversible"], rollback_target=rollback_target,
        rationale=rationale,
        # Destructive actions must pass the human gate.
        requires_approval=meta["destructive"],
    )


@dataclass
class ExecStep:
    label: str
    ok: bool
    simulated: bool = True
    detail: str = ""


@dataclass
class ExecResult:
    ok: bool
    action: str
    target: str
    steps: list[ExecStep] = field(default_factory=list)
    simulated: bool = True


async def execute_remediation(plan: RemediationPlan, service: str) -> ExecResult:
    """Run the plan as an explicit, ordered simulation.

    Each step mirrors what a real runbook would do (drain, deploy, verify) so
    the demo shows a believable execution timeline — clearly labelled simulated.
    """
    steps: list[ExecStep] = []

    async def do(label: str, detail: str = "") -> None:
        await asyncio.sleep(0.35)  # visible pacing for the live timeline
        steps.append(ExecStep(label=label, ok=True, detail=detail))

    if plan.action == "rollback":
        await do("Freeze deploys", f"Locked deploy pipeline for {service}")
        await do("Select rollback target", f"Known-good {plan.rollback_target}")
        await do("Drain traffic from bad pods", "Cordoned pods running v2.4.1")
        await do("Deploy known-good version", f"Rolling out {plan.rollback_target}")
        await do("Verify health", "5xx back under SLO; p99 recovering")
        await do("Unfreeze deploys", "Pipeline unlocked")
    else:
        await do(f"Execute {plan.action}", plan.target)
        await do("Verify health", "Signals recovering within SLO")

    return ExecResult(
        ok=all(s.ok for s in steps), action=plan.action,
        target=plan.target, steps=steps, simulated=True,
    )
