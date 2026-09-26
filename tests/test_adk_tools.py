"""ADK tool wrappers — exercise the closed-over FunctionTool callables."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agents.base import Deps, RunContext
from backend.adk import tools as TL
from backend.guardrails import ApprovalGate
from backend.models import Alert, Deploy, Incident, IncidentStatus, LogLine


def _rc() -> RunContext:
    incident = Incident(
        id="inc-adk",
        status=IncidentStatus.DETECTED,
        detected_at=1_700_000_000_000,
        alert=Alert(alert="HighErrorRate", service="checkout-svc", error_rate="35%"),
        service="checkout-svc",
        fingerprint="fp_checkout",
    )
    deps = Deps(
        storage=MagicMock(),
        gemini=MagicMock(),
        hub=MagicMock(),
        gate=MagicMock(spec=ApprovalGate),
    )
    return RunContext(incident, deps)


def _call(tool):
    """Invoke the underlying function regardless of FunctionTool wrapping."""
    fn = getattr(tool, "func", None) or getattr(tool, "function", None) or tool
    return fn()


def test_triage_tools_resolve():
    out = _call(TL.triage_tools(_rc())[0])
    assert out["service"] == "checkout-svc"
    assert "rubric_severity" in out
    assert out["error_rate_pct"] >= 30


def test_diagnosis_tools_classify():
    rc = _rc()
    rc.deps.storage.logs_for_service.return_value = [
        LogLine(
            id="1",
            service="checkout-svc",
            ts=1,
            level="ERROR",
            message="connection pool exhausted",
        )
    ]
    out = _call(TL.diagnosis_tools(rc)[0])
    assert out["dominant_class"]
    assert out["fingerprint"]
    assert rc.incident.fingerprint == out["fingerprint"]


def test_correlation_tools_rank_deploys():
    rc = _rc()
    dep = Deploy(
        id="d1",
        service="checkout-svc",
        version="v9",
        deployed_at=rc.incident.detected_at - 120_000,
        deployed_by="ci",
        commit_sha="deadbee",
        rollback_target="v8",
    )
    with patch(
        "backend.adk.tools.C.deploys_in_window",
        return_value=[dep],
    ):
        out = _call(TL.correlation_tools(rc)[0])
    assert out["top_suspect"]["version"] == "v9"


def test_memory_tools_search():
    rc = _rc()
    with patch("backend.adk.tools.M.search_memory", return_value=[]):
        out = _call(TL.memory_tools(rc)[0])
    assert out["fingerprint"] == "fp_checkout"
    assert out["matches"] == []


def test_remediation_tools_plan():
    rc = _rc()
    tool = TL.remediation_tools(rc)[0]
    fn = getattr(tool, "func", None) or getattr(tool, "function", None)
    out = fn(action="rollback", rollback_target="v1.0.0", rationale="heal")
    assert out["action"] == "rollback"
    assert out["requires_approval"] is True
