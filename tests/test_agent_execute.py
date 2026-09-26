"""Execute-path coverage for classic agents (Triage, Diagnosis, etc.)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agents.base import Deps, RunContext
from backend.agents.diagnosis import DiagnosisAgent
from backend.agents.memory import MemoryAgent
from backend.agents.triage import TriageAgent
from backend.guardrails import ApprovalGate
from backend.models import Alert, Incident, IncidentStatus, LogLine, Severity
from backend.services.gemini import GenResult


def _ctx(alert: Alert | None = None) -> RunContext:
    incident = Incident(
        id="inc-agent",
        status=IncidentStatus.DETECTED,
        detected_at=1000,
        alert=alert
        or Alert(alert="HighErrorRate", service="checkout-svc", error_rate="42%"),
        service="checkout-svc",
    )
    hub = MagicMock()
    hub.publish = AsyncMock()
    deps = Deps(
        storage=MagicMock(),
        gemini=MagicMock(),
        hub=hub,
        gate=MagicMock(spec=ApprovalGate),
    )
    return RunContext(incident, deps)


def _json_result(payload: dict) -> GenResult:
    text = json.dumps(payload)
    result = GenResult(
        text=text, tokens=10, latency_ms=5, model="test", attempts=1
    )
    result.json = MagicMock(return_value=payload)
    return result


@pytest.mark.anyio
async def test_triage_agent_sets_severity():
    ctx = _ctx()
    ctx.deps.gemini.generate = AsyncMock(
        return_value=_json_result(
            {
                "severity": "SEV2",
                "blast_radius": "checkout + cart",
                "oncall": "platform",
                "reasoning": "elevated error rate on tier-0",
            }
        )
    )
    await TriageAgent().execute(ctx)
    # Rubric is the floor — tier-0 + high error rate must stay SEV1 even if model softens.
    assert ctx.incident.severity == Severity.SEV1
    assert ctx.incident.service == "checkout-svc"
    ctx.deps.storage.save_incident.assert_called()


@pytest.mark.anyio
async def test_diagnosis_agent_without_snapshot():
    ctx = _ctx()
    logs = [
        LogLine(
            id="1",
            service="checkout-svc",
            ts=1,
            level="ERROR",
            message="connection pool exhausted",
        )
    ]
    ctx.deps.storage.logs_for_service.return_value = logs
    ctx.deps.gemini.generate = AsyncMock(
        return_value=_json_result(
            {
                "summary": "pool exhausted",
                "root_cause_hint": "db pool",
                "reasoning": "errors dominate",
            }
        )
    )
    await DiagnosisAgent().execute(ctx)
    assert ctx.incident.fingerprint
    assert "diagnosis" in ctx.incident.findings or ctx.deps.storage.save_incident.called


@pytest.mark.anyio
async def test_memory_agent_no_match():
    from unittest.mock import patch

    from backend.tools import memory as M

    ctx = _ctx()
    ctx.incident.fingerprint = "fp_test"
    with patch.object(M, "search_memory", return_value=[]):
        await MemoryAgent().execute(ctx)
    assert ctx.incident.findings.get("memory", {}).get("match") is None


@pytest.mark.anyio
async def test_remediation_agent_approved_simulate(monkeypatch):
    from backend.agents.remediation import RemediationAgent
    from backend.guardrails import ApprovalDecision
    from backend.models import IncidentStatus

    monkeypatch.setenv("REMEDIATION_MODE", "simulate")
    ctx = _ctx()
    ctx.incident.status = IncidentStatus.CORRELATED
    ctx.incident.findings = {
        "correlation": {
            "probable_cause": "bad deploy",
            "confidence": 0.9,
            "suspect": {"service": "checkout-svc", "version": "v2", "rollback_target": "v1.0.0"},
        },
        "memory": {"match": None, "recommendation": "none"},
    }
    ctx.deps.gemini.generate = AsyncMock(
        return_value=_json_result(
            {
                "action": "rollback",
                "rationale": "revert bad deploy",
                "reasoning": "correlation points to deploy",
            }
        )
    )
    ctx.deps.gate.open_gate = MagicMock()
    ctx.deps.gate.wait_for = AsyncMock(
        return_value=ApprovalDecision(approved=True, approver="saketh", note="ok")
    )

    await RemediationAgent().execute(ctx)
    assert ctx.incident.status == IncidentStatus.RESOLVED
    assert ctx.incident.approved_by == "saketh"
    assert "execution" in ctx.incident.findings


@pytest.mark.anyio
async def test_comms_agent_writes_rca(tmp_path, monkeypatch):
    from backend.agents.comms import CommsAgent
    from backend.models import IncidentStatus, Severity
    from backend.tools import comms as CT

    monkeypatch.setattr(CT, "TICKETS_DIR", tmp_path / "tickets")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    from backend.config import get_settings

    get_settings.cache_clear()

    ctx = _ctx()
    ctx.incident.status = IncidentStatus.RESOLVED
    ctx.incident.severity = Severity.SEV2
    ctx.incident.probable_cause = "bad deploy"
    ctx.incident.findings = {
        "triage": {"severity": "SEV2"},
        "diagnosis": {"summary": "errors", "primary_symptom": "5xx"},
        "correlation": {"probable_cause": "deploy", "confidence": 0.8, "suspect": {}},
        "memory": {"recommendation": "rollback"},
        "remediation": {"action": "rollback"},
        "execution": {"ok": True, "simulated": True},
    }
    ctx.deps.gemini.generate = AsyncMock(
        return_value=GenResult(
            text="# RCA\n\nAll good after rollback.",
            tokens=20,
            latency_ms=10,
            model="pro",
            attempts=1,
        )
    )

    await CommsAgent().execute(ctx)
    assert ctx.incident.rca_doc.startswith("# RCA")
    assert "comms" in ctx.incident.findings


@pytest.mark.anyio
async def test_correlation_agent_with_deploys():
    from backend.agents.correlation import CorrelationAgent
    from backend.models import Deploy

    ctx = _ctx()
    ctx.incident.status = IncidentStatus.DIAGNOSED
    ctx.incident.findings = {
        "diagnosis": {"summary": "pool", "primary_symptom": "db_pool_exhaustion"}
    }
    ctx.deps.storage.deploys_for_service.return_value = [
        Deploy(
            id="d1",
            service="checkout-svc",
            version="v2.0.0",
            deployed_at=ctx.incident.detected_at - 5 * 60_000,
            deployed_by="ci",
            commit_sha="abc1234",
            rollback_target="v1.0.0",
        )
    ]
    # deploys_in_window may use different storage API
    from unittest.mock import patch

    from backend.tools import correlation as C

    dep = Deploy(
        id="d1",
        service="checkout-svc",
        version="v2.0.0",
        deployed_at=ctx.incident.detected_at - 5 * 60_000,
        deployed_by="ci",
        commit_sha="abc1234",
        rollback_target="v1.0.0",
    )
    ctx.deps.gemini.generate = AsyncMock(
        return_value=_json_result(
            {
                "probable_cause": "Bad deploy v2.0.0",
                "confidence": 0.85,
                "reasoning": "shipped 5m before",
            }
        )
    )
    with patch.object(C, "deploys_in_window", return_value=[dep]):
        await CorrelationAgent().execute(ctx)
    assert ctx.incident.probable_cause
    assert ctx.incident.confidence > 0
