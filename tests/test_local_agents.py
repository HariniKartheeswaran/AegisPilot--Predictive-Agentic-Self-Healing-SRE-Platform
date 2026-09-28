"""Execute-path unit tests for local agents (Gemini mocked)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.base import Deps, RunContext
from backend.agents.comms import CommsAgent
from backend.agents.correlation import CorrelationAgent
from backend.agents.diagnosis import DiagnosisAgent
from backend.agents.memory import MemoryAgent
from backend.agents.remediation import RemediationAgent
from backend.agents.triage import TriageAgent
from backend.guardrails import ApprovalDecision, ApprovalGate
from backend.models import Alert, Deploy, Incident, IncidentMemory, IncidentStatus, LogLine, Severity, now_ms
from backend.tools.memory import MemoryMatch
from backend.tools.remediation import ExecResult, ExecStep


def _ctx(incident: Incident) -> RunContext:
    storage = MagicMock()
    hub = MagicMock()
    hub.publish = AsyncMock()
    gate = MagicMock(spec=ApprovalGate)
    gate.wait_for = AsyncMock(
        return_value=ApprovalDecision(approved=True, approver="tester", note="ok")
    )
    return RunContext(
        incident,
        Deps(storage=storage, gemini=MagicMock(), hub=hub, gate=gate),
    )


def _incident(**kwargs) -> Incident:
    alert = kwargs.pop("alert", Alert(alert="HighErrorRate", service="checkout-svc", error_rate="42%"))
    inc = Incident(
        id="inc-agent-test",
        status=IncidentStatus.DETECTED,
        detected_at=now_ms(),
        alert=alert,
        service=alert.service,
    )
    for k, v in kwargs.items():
        setattr(inc, k, v)
    return inc


@pytest.mark.asyncio
async def test_triage_agent_sets_severity():
    ctx = _ctx(_incident())
    ctx.think = AsyncMock(
        return_value=(
            {
                "severity": "SEV1",
                "service": "checkout-svc",
                "blast_radius": "wide",
                "oncall": "#oncall",
                "reasoning": "tier-0 spike",
            },
            MagicMock(),
        )
    )
    ctx.tool = AsyncMock()
    await TriageAgent().execute(ctx)
    assert ctx.incident.severity == Severity.SEV1
    assert "triage" in ctx.incident.findings


@pytest.mark.asyncio
async def test_diagnosis_agent_without_snapshot(monkeypatch):
    ctx = _ctx(_incident())
    logs = [
        LogLine(id="1", service="checkout-svc", ts=1, level="ERROR", message="pool exhausted"),
        LogLine(id="2", service="checkout-svc", ts=2, level="INFO", message="ok"),
    ]
    monkeypatch.setattr("backend.tools.diagnosis.fetch_logs", lambda storage, svc: logs)
    ctx.think = AsyncMock(
        return_value=(
            {"summary": "pool exhausted", "primary_symptom": "HikariCP", "reasoning": "x"},
            MagicMock(),
        )
    )
    ctx.tool = AsyncMock()
    await DiagnosisAgent().execute(ctx)
    assert ctx.incident.fingerprint
    assert ctx.incident.findings["diagnosis"]["summary"] == "pool exhausted"


@pytest.mark.asyncio
async def test_correlation_agent_with_deploy(monkeypatch):
    t = now_ms()
    ctx = _ctx(_incident())
    ctx.incident.findings["diagnosis"] = {"summary": "errors", "primary_symptom": "5xx"}
    dep = Deploy(
        id="d1",
        service="checkout-svc",
        version="v2",
        deployed_at=t - 60_000,
        deployed_by="dave@corp.dev",
        commit_sha="abc",
        rollback_target="v1",
    )
    monkeypatch.setattr(
        "backend.tools.correlation.deploys_in_window", lambda *a, **k: [dep]
    )
    ctx.think = AsyncMock(
        return_value=(
            {"probable_cause": "bad deploy v2", "confidence": 0.9, "reasoning": "timing"},
            MagicMock(),
        )
    )
    ctx.tool = AsyncMock()
    await CorrelationAgent().execute(ctx)
    assert ctx.incident.probable_cause
    assert ctx.incident.findings["correlation"]["suspect"]["version"] == "v2"


@pytest.mark.asyncio
async def test_memory_agent_no_prior(monkeypatch):
    ctx = _ctx(_incident(fingerprint="fp-novel"))
    monkeypatch.setattr("backend.tools.memory.search_memory", lambda *a, **k: [])
    ctx.tool = AsyncMock()
    ctx.emit = AsyncMock()
    await MemoryAgent().execute(ctx)
    assert ctx.incident.findings["memory"]["match"] is None


@pytest.mark.asyncio
async def test_memory_agent_strong_prior(monkeypatch):
    ctx = _ctx(_incident(fingerprint="fp-known"))
    mem = IncidentMemory(
        fingerprint_id="fpid",
        fingerprint="fp-known",
        embedding=[0.1] * 8,
        past_incident_ids=["inc-old"],
        typical_cause="bad deploy",
        typical_fix="rollback",
        avg_resolution_minutes=4.0,
    )
    monkeypatch.setattr(
        "backend.tools.memory.search_memory",
        lambda *a, **k: [MemoryMatch(memory=mem, similarity=0.91)],
    )
    ctx.think = AsyncMock(
        return_value=(
            {"recommendation": "Seen 1x via rollback", "reasoning": "match"},
            MagicMock(),
        )
    )
    ctx.tool = AsyncMock()
    ctx.emit = AsyncMock()
    await MemoryAgent().execute(ctx)
    assert ctx.incident.findings["memory"]["match"]["typical_fix"] == "rollback"


@pytest.mark.asyncio
async def test_remediation_agent_auto_approves_non_destructive(monkeypatch):
    ctx = _ctx(_incident())
    ctx.incident.findings = {
        "correlation": {"probable_cause": "load", "confidence": 0.5, "suspect": {}},
        "memory": {"match": None, "recommendation": "none"},
    }
    ctx.think = AsyncMock(
        return_value=({"action": "scale_out", "rationale": "add capacity", "reasoning": "x"}, MagicMock())
    )
    ctx.tool = AsyncMock()
    ctx.transition = AsyncMock()
    ctx.emit = AsyncMock()
    monkeypatch.setattr(
        "backend.tools.remediation.execute_remediation",
        AsyncMock(
            return_value=ExecResult(
                ok=True,
                action="scale_out",
                target="checkout-svc",
                steps=[ExecStep(label="Scale out", ok=True, detail="ok")],
                simulated=True,
            )
        ),
    )
    await RemediationAgent().execute(ctx)
    assert ctx.incident.remediation_plan is not None
    assert ctx.incident.findings["execution"]["ok"] is True


@pytest.mark.asyncio
async def test_diagnosis_agent_with_snapshot(monkeypatch, tmp_path):
    snap = tmp_path / "dash.png"
    snap.write_bytes(b"\x89PNG\r\n\x1a\n")
    alert = Alert(
        alert="HighErrorRate",
        service="checkout-svc",
        error_rate="42%",
        grafana_snapshot=str(snap),
        metadata={"grafana_explore_url": "http://g/d/x"},
    )
    ctx = _ctx(_incident(alert=alert))
    monkeypatch.setattr("backend.tools.diagnosis.fetch_logs", lambda storage, svc: [])
    ctx.think = AsyncMock(
        side_effect=[
            (
                {
                    "confirmed": True,
                    "observation": "error spike",
                    "annotation": "5xx up",
                    "reasoning": "yes",
                },
                MagicMock(),
            ),
            (
                {"summary": "confirmed spike", "primary_symptom": "5xx", "reasoning": "x"},
                MagicMock(),
            ),
        ]
    )
    ctx.tool = AsyncMock()
    ctx.emit = AsyncMock()
    await DiagnosisAgent().execute(ctx)
    assert ctx.incident.findings["diagnosis"]["vision"]["confirmed"] is True
    ctx.emit.assert_any_await(
        "vision_result",
        agent="Diagnosis",
        image_url="/api/incidents/inc-agent-test/grafana",
        confirmed=True,
        observation="error spike",
        annotation="5xx up",
        explore_url="http://g/d/x",
    )


@pytest.mark.asyncio
async def test_remediation_agent_rejected(monkeypatch):
    ctx = _ctx(_incident())
    ctx.incident.findings = {
        "correlation": {
            "probable_cause": "bad deploy",
            "confidence": 0.9,
            "suspect": {"rollback_target": "v1"},
        },
        "memory": {"match": {"typical_fix": "rollback"}, "recommendation": "rollback"},
    }
    ctx.think = AsyncMock(
        return_value=({"action": "rollback", "rationale": "revert", "reasoning": "x"}, MagicMock())
    )
    ctx.tool = AsyncMock()
    ctx.transition = AsyncMock()
    ctx.emit = AsyncMock()
    ctx.deps.gate.wait_for = AsyncMock(
        return_value=ApprovalDecision(approved=False, approver="boss", note="no")
    )
    monkeypatch.setattr(
        "backend.services.live_fire.live_mode_enabled", lambda: False
    )
    await RemediationAgent().execute(ctx)
    ctx.transition.assert_awaited_with(IncidentStatus.REJECTED)


@pytest.mark.asyncio
async def test_comms_agent_writes_rca(monkeypatch, tmp_path):
    ctx = _ctx(_incident())
    ctx.incident.status = IncidentStatus.RESOLVED
    ctx.incident.severity = Severity.SEV2
    ctx.incident.findings = {
        "triage": {},
        "diagnosis": {"summary": "errors"},
        "correlation": {"probable_cause": "deploy"},
        "memory": {"recommendation": "rollback"},
        "remediation": {"action": "rollback"},
        "execution": {"ok": True},
    }
    ctx.think = AsyncMock(return_value=("# RCA\n\nAll good.", MagicMock()))
    ctx.tool = AsyncMock()
    monkeypatch.setattr(
        "backend.tools.comms.file_ticket",
        lambda inc, summary: MagicMock(id="T-1", url="http://t", path=str(tmp_path / "t.md")),
    )
    monkeypatch.setattr(
        "backend.services.slack.post_incident",
        AsyncMock(return_value=MagicMock(delivered=False, channel="console", detail="no webhook")),
    )
    await CommsAgent().execute(ctx)
    assert ctx.incident.rca_doc.startswith("# RCA")
