"""Phase-3 proof: full incident reading/writing against REAL Firestore.

Runs the ADK orchestrator with BACKEND=cloud (FirestoreStorage), auto-approves
the gate, then RE-READS the incident + audit trail from a FRESH Firestore client
to prove the data actually persisted in managed Firestore (not just in memory).

Run:  python scripts/validate_firestore.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings  # noqa: E402

get_settings().apply_google_env()

from backend.adk.orchestrator import AdkOrchestrator  # noqa: E402
from backend.agents.base import Deps  # noqa: E402
from backend.guardrails import ApprovalDecision, ApprovalGate  # noqa: E402
from backend.models import Alert, IncidentStatus  # noqa: E402
from backend.seed.generate_grafana import generate as gen_grafana  # noqa: E402
from backend.seed.seed_data import seed_all  # noqa: E402
from backend.services.firestore_storage import FirestoreStorage  # noqa: E402
from backend.services.gemini import gemini  # noqa: E402
from backend.services.stream import StreamHub  # noqa: E402


async def _auto_approve(gate: ApprovalGate, storage: FirestoreStorage) -> None:
    for _ in range(600):
        await asyncio.sleep(0.1)
        for inc in storage.list_incidents():
            if gate.is_open(inc.id):
                gate.resolve(inc.id, ApprovalDecision(approved=True, approver="validator"))
                return


async def main() -> int:
    s = get_settings()
    print(f"BACKEND=cloud (Firestore)  project={s.google_cloud_project}  model={s.gemini_model}")
    store = FirestoreStorage(s.google_cloud_project)
    store.init_schema()
    seed_all(store, s.gemini_model)
    gen_grafana()

    deps = Deps(storage=store, gemini=gemini, hub=StreamHub(), gate=ApprovalGate())
    orch = AdkOrchestrator(deps)
    alert = Alert(alert="HighErrorRate", service="checkout-svc", error_rate="42%",
                  grafana_snapshot="backend/seed/grafana_checkout_spike.png")

    approver = asyncio.create_task(_auto_approve(deps.gate, store))
    incident = await orch.handle_alert(alert)
    await approver

    # Re-read from a FRESH client → proves real persistence in Firestore.
    fresh = FirestoreStorage(s.google_cloud_project)
    inc = fresh.get_incident(incident.id)
    audit = fresh.audit_for_incident(incident.id)

    checks = {
        "incident persisted in Firestore": inc is not None,
        "status == RESOLVED": inc and inc.status == IncidentStatus.RESOLVED,
        "severity persisted": inc and inc.severity is not None,
        "probable_cause persisted": bool(inc and inc.probable_cause),
        "remediation_plan persisted": inc and inc.remediation_plan is not None,
        "approved_by persisted": inc and inc.approved_by == "validator",
        "rca_doc persisted": inc and len(inc.rca_doc or "") > 50,
        "findings persisted (comms ticket)": bool(inc and (inc.findings.get("comms") or {}).get("ticket")),
        "audit trail persisted (>8)": len(audit) > 8,
    }
    print("\n=== Phase 3: real Firestore persistence validation ===")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if inc:
        print(f"\n  doc id      : {inc.id}")
        print(f"  status      : {inc.status.value}  severity {inc.severity.value if inc.severity else None}")
        print(f"  probable    : {inc.probable_cause}")
        print(f"  audit docs  : {len(audit)}")
    passed = all(checks.values())
    print(f"\n{'ALL CHECKS PASSED [OK]' if passed else 'SOME CHECKS FAILED [X]'}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
