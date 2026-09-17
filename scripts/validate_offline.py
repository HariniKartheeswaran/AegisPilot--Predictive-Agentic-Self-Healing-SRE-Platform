"""Offline end-to-end validation of the whole AegisPilot pipeline.

Runs the real Orchestrator + all six real agents + approval gate + storage +
stream hub, with Gemini swapped for a deterministic fake so it needs NO API key.
This proves every hand-off, state transition, finding, and the human-in-the-loop
gate are wired correctly. The real system uses real Gemini; only the model text
is faked here so CI/dev can validate without spending quota.

Run:  python scripts/validate_offline.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Ensure repo root on path when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.base import Deps  # noqa: E402
from backend.guardrails import ApprovalDecision, ApprovalGate  # noqa: E402
from backend.models import Alert, IncidentStatus  # noqa: E402
from backend.orchestrator import Orchestrator  # noqa: E402
from backend.seed.generate_grafana import generate as gen_grafana  # noqa: E402
from backend.seed.seed_data import seed_all  # noqa: E402
from backend.services.gemini import GenResult  # noqa: E402
from backend.services.storage import SQLiteStorage  # noqa: E402
from backend.services.stream import StreamHub  # noqa: E402


class FakeGemini:
    """Deterministic stand-in. Routes on marker substrings present in each
    agent's prompt to return contract-shaped JSON (or markdown for the RCA)."""

    model = "fake-flash"

    def _text_for(self, prompt: str, response_json: bool) -> str:
        p = prompt
        if not response_json:
            # Comms RCA (prose).
            return "# RCA — checkout-svc HighErrorRate\n\n## Summary\nSimulated RCA for validation.\n"
        if '"severity"' in p:
            return ('{"severity":"SEV1","service":"checkout-svc",'
                    '"blast_radius":"~16% of traffic; payments at risk",'
                    '"oncall":"#oncall-payments-critical",'
                    '"reasoning":"Tier-0 service at 42% errors."}')
        if '"confirmed"' in p:
            return ('{"confirmed":true,"observation":"5xx panel spikes to ~42%, p99 ~1.1s",'
                    '"annotation":"5xx + latency spike post-deploy",'
                    '"reasoning":"Panels confirm the regression."}')
        if '"primary_symptom"' in p:
            return ('{"summary":"DB connection pool exhaustion cascading to payments timeouts.",'
                    '"primary_symptom":"db_pool_exhaustion","reasoning":"Dominant log class."}')
        if '"probable_cause"' in p:
            return ('{"probable_cause":"Bad deploy checkout-svc v2.4.1 exhausted the DB pool",'
                    '"confidence":0.9,"reasoning":"Shipped ~12m before detection."}')
        if '"recommendation"' in p:
            return ('{"recommendation":"Seen 2x, resolved in ~4m via rollback.",'
                    '"reasoning":"Fingerprint matches a known bad-deploy pattern."}')
        if '"action"' in p:
            return ('{"action":"rollback","rationale":"Roll back to the known-good version.",'
                    '"reasoning":"Reverses the regression."}')
        return '{}'

    async def generate(self, prompt, *, system=None, temperature=0.3, response_json=False, model=None):
        text = self._text_for(prompt, response_json)
        return GenResult(text=text, tokens=len(text) // 4, latency_ms=12, model=model or self.model)

    async def generate_vision(self, prompt, image_path, *, system=None, temperature=0.2,
                              response_json=False, model=None):
        text = self._text_for(prompt, response_json)
        return GenResult(text=text, tokens=len(text) // 4, latency_ms=20, model=model or self.model)


async def _auto_approve(gate: ApprovalGate, storage: SQLiteStorage) -> None:
    """Simulate the human clicking Approve once the gate opens."""
    for _ in range(200):  # up to ~20s
        await asyncio.sleep(0.1)
        for inc in storage.list_incidents():
            if gate.is_open(inc.id):
                gate.resolve(inc.id, ApprovalDecision(approved=True, approver="validator"))
                return


async def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "validate.db"
    storage = SQLiteStorage(tmp)
    storage.init_schema()
    seed_all(storage, "fake-flash")
    gen_grafana()  # ensure the snapshot exists for the vision step

    deps = Deps(storage=storage, gemini=FakeGemini(), hub=StreamHub(), gate=ApprovalGate())
    orch = Orchestrator(deps)

    alert = Alert(alert="HighErrorRate", service="checkout-svc", error_rate="42%",
                  grafana_snapshot="backend/seed/grafana_checkout_spike.png")

    approver = asyncio.create_task(_auto_approve(deps.gate, storage))
    incident = await orch.handle_alert(alert)
    await approver

    inc = storage.get_incident(incident.id)
    audit = storage.audit_for_incident(incident.id)
    f = inc.findings

    checks = {
        "status == RESOLVED": inc.status == IncidentStatus.RESOLVED,
        "severity set": inc.severity is not None,
        "fingerprint set": bool(inc.fingerprint),
        "probable_cause set": bool(inc.probable_cause),
        "confidence in [0,1]": inc.confidence is not None and 0 <= inc.confidence <= 1,
        "remediation_plan set": inc.remediation_plan is not None,
        "approved_by set": inc.approved_by == "validator",
        "resolved_at set": inc.resolved_at is not None,
        "rca_doc present": bool(inc.rca_doc),
        "triage finding": "triage" in f,
        "diagnosis+vision finding": bool(f.get("diagnosis", {}).get("vision")),
        "correlation suspect": bool((f.get("correlation") or {}).get("suspect")),
        "memory match": (f.get("memory") or {}).get("match") is not None,
        "execution steps": len((f.get("execution") or {}).get("steps", [])) > 0,
        "comms ticket": bool((f.get("comms") or {}).get("ticket", {}).get("id")),
        "audit trail non-empty": len(audit) > 5,
    }

    print("\n=== AegisPilot offline pipeline validation ===")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  audit steps recorded: {len(audit)}")
    print(f"  final status: {inc.status.value}")
    print(f"  probable cause: {inc.probable_cause}")
    print(f"  RCA chars: {len(inc.rca_doc or '')}")

    passed = all(checks.values())
    print(f"\n{'ALL CHECKS PASSED [OK]' if passed else 'SOME CHECKS FAILED [X]'}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
