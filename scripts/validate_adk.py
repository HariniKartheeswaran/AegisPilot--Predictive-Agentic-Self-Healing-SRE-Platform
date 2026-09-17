"""Phase-1 proof: full incident through the REAL google-adk orchestrator.

Runs AdkOrchestrator (LlmAgent + Runner + RetryGemini + callbacks) end-to-end
against real Vertex AI, auto-approving the gate, and asserts DETECTED->RESOLVED
with all findings populated. This is the "runs end to end" gate for Phase 1.

Run:  python scripts/validate_adk.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
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
from backend.services.gemini import gemini  # noqa: E402
from backend.services.storage import SQLiteStorage  # noqa: E402
from backend.services.stream import StreamHub  # noqa: E402


async def _auto_approve(gate: ApprovalGate, storage: SQLiteStorage) -> None:
    for _ in range(600):  # up to ~60s (real model calls take time)
        await asyncio.sleep(0.1)
        for inc in storage.list_incidents():
            if gate.is_open(inc.id):
                gate.resolve(inc.id, ApprovalDecision(approved=True, approver="validator"))
                return


async def main() -> int:
    s = get_settings()
    print(f"orchestrator=ADK  vertex={s.use_vertex}  model={s.gemini_model}  loc={s.vertex_location}")
    tmp = Path(tempfile.mkdtemp()) / "adk.db"
    storage = SQLiteStorage(tmp)
    storage.init_schema()
    seed_all(storage, s.gemini_model)
    gen_grafana()

    deps = Deps(storage=storage, gemini=gemini, hub=StreamHub(), gate=ApprovalGate())
    orch = AdkOrchestrator(deps)
    alert = Alert(alert="HighErrorRate", service="checkout-svc", error_rate="42%",
                  grafana_snapshot="backend/seed/grafana_checkout_spike.png")

    approver = asyncio.create_task(_auto_approve(deps.gate, storage))
    incident = await orch.handle_alert(alert)
    await approver

    inc = storage.get_incident(incident.id)
    f = inc.findings
    checks = {
        "status == RESOLVED": inc.status == IncidentStatus.RESOLVED,
        "severity set": inc.severity is not None,
        "fingerprint set": bool(inc.fingerprint),
        "probable_cause set": bool(inc.probable_cause),
        "remediation_plan set": inc.remediation_plan is not None,
        "approved_by == validator": inc.approved_by == "validator",
        "resolved_at set": inc.resolved_at is not None,
        "rca_doc present": len(inc.rca_doc or "") > 50,
        "diagnosis vision": bool(f.get("diagnosis", {}).get("vision", {}).get("observation")),
        "correlation suspect": bool((f.get("correlation") or {}).get("suspect")),
        "memory match": (f.get("memory") or {}).get("match") is not None,
        "execution steps": len((f.get("execution") or {}).get("steps", [])) > 0,
        "comms ticket": bool((f.get("comms") or {}).get("ticket", {}).get("id")),
        "audit trail > 8": len(storage.audit_for_incident(inc.id)) > 8,
    }
    print("\n=== Phase 1: real google-adk full-incident validation ===")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  final status : {inc.status.value}")
    print(f"  severity     : {inc.severity.value if inc.severity else None}")
    print(f"  probable     : {inc.probable_cause}")
    print(f"  confidence   : {inc.confidence}")
    print(f"  RCA chars    : {len(inc.rca_doc or '')}")
    print(f"  audit steps  : {len(storage.audit_for_incident(inc.id))}")
    passed = all(checks.values())
    print(f"\n{'ALL CHECKS PASSED [OK]' if passed else 'SOME CHECKS FAILED [X]'}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
