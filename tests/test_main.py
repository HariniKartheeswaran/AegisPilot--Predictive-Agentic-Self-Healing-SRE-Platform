"""Tests for FastAPI route handlers in backend.main."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend.main import (
    Decision,
    approve,
    audit,
    grafana,
    health,
    incident,
    incidents,
    rca,
    registry,
    reject,
)

class FakeOrchestrator:
    pass


@pytest.mark.asyncio
async def test_health_returns_application_configuration():
    settings = SimpleNamespace(
        gemini_model="gemini-test",
        gemini_model_pro="gemini-pro-test",
        use_vertex=False,
        has_gemini_key=True,
        google_cloud_project="test-project",
        vertex_location="us-central1",
        google_cloud_location="us-central1",
        backend="local",
        has_slack=False,
        has_prometheus=False,
        grafana_url="",
    )

    app_state = SimpleNamespace(
        settings=settings,
        orchestrator=FakeOrchestrator(),
    )

    request = SimpleNamespace(
        app=SimpleNamespace(state=app_state)
    )

    result = await health(request)

    assert result == {
        "status": "ok",
        "orchestrator": "FakeOrchestrator",
        "model": "gemini-test",
        "model_pro": "gemini-pro-test",
        "auth": "ai-studio-key",
        "vertex": False,
        "project": "test-project",
        "vertex_location": None,
        "compute_location": "us-central1",
        "backend": "local",
        "slack_configured": False,
        "prometheus_configured": False,
        "grafana_url": None,
    }

@pytest.mark.asyncio
async def test_registry_returns_registered_agents():
    agents = [
        Mock(model_dump=lambda: {"name": "TriageAgent"}),
        Mock(model_dump=lambda: {"name": "DiagnosisAgent"}),
    ]

    storage = SimpleNamespace(
        list_agents=lambda: agents,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    result = await registry(request)

    assert result == [
        {"name": "TriageAgent"},
        {"name": "DiagnosisAgent"},
    ]


@pytest.mark.asyncio
async def test_incidents_returns_saved_incidents():
    saved = [
        Mock(model_dump=lambda: {"id": "inc-1", "status": "NEW"}),
        Mock(model_dump=lambda: {"id": "inc-2", "status": "RESOLVED"}),
    ]

    storage = SimpleNamespace(
        list_incidents=lambda: saved,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    result = await incidents(request)

    assert result == [
        {"id": "inc-1", "status": "NEW"},
        {"id": "inc-2", "status": "RESOLVED"},
    ]


@pytest.mark.asyncio
async def test_incident_returns_incident_when_found():
    saved = Mock(
        model_dump=lambda: {
            "id": "inc-123",
            "status": "RESOLVED",
        }
    )

    storage = SimpleNamespace(
        get_incident=lambda incident_id: saved,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    result = await incident("inc-123", request)

    assert result == {
        "id": "inc-123",
        "status": "RESOLVED",
    }


@pytest.mark.asyncio
async def test_incident_raises_404_when_not_found():
    storage = SimpleNamespace(
        get_incident=lambda incident_id: None,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await incident("missing", request)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "incident not found"


@pytest.mark.asyncio
async def test_audit_returns_incident_audit_records():
    records = [
        Mock(model_dump=lambda: {"event": "created"}),
        Mock(model_dump=lambda: {"event": "triage_completed"}),
    ]

    storage = SimpleNamespace(
        audit_for_incident=lambda incident_id: records,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    result = await audit("inc-123", request)

    assert result == [
        {"event": "created"},
        {"event": "triage_completed"},
    ]


@pytest.mark.asyncio
async def test_rca_returns_document_when_incident_found():
    saved = Mock(
        rca_doc="Root cause: downstream timeout",
        findings={"comms": {"ticket": {"id": "INC-123"}}},
    )

    storage = SimpleNamespace(
        get_incident=lambda incident_id: saved,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    result = await rca("inc-123", request)

    assert result == {
    "rca": "Root cause: downstream timeout",
    "findings": {"ticket": {"id": "INC-123"}},
    }

@pytest.mark.asyncio
async def test_rca_raises_404_when_incident_not_found():
    storage = SimpleNamespace(
        get_incident=lambda incident_id: None,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await rca("missing", request)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "incident not found"

@pytest.mark.asyncio
async def test_approve_resolves_open_gate():
    gate = SimpleNamespace(
        resolve=lambda incident_id, decision: True,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(gate=gate)
        )
    )

    decision = Decision(
        approver="on-call-engineer",
        note="Approved for rollback",
    )

    result = await approve("inc-123", decision, request)

    assert result == {
        "resolved": True,
        "approved": True,
    }


@pytest.mark.asyncio
async def test_approve_raises_409_when_no_gate_exists():
    gate = SimpleNamespace(
        resolve=lambda incident_id, decision: False,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(gate=gate)
        )
    )

    decision = Decision()

    with pytest.raises(HTTPException) as exc_info:
        await approve("inc-123", decision, request)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "no open approval gate for this incident"


@pytest.mark.asyncio
async def test_reject_resolves_open_gate():
    gate = SimpleNamespace(
        resolve=lambda incident_id, decision: True,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(gate=gate)
        )
    )

    decision = Decision(
        approver="on-call-engineer",
        note="Reject rollback",
    )

    result = await reject("inc-123", decision, request)

    assert result == {
        "resolved": True,
        "approved": False,
    }


@pytest.mark.asyncio
async def test_reject_raises_409_when_no_gate_exists():
    gate = SimpleNamespace(
        resolve=lambda incident_id, decision: False,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(gate=gate)
        )
    )

    decision = Decision()

    with pytest.raises(HTTPException) as exc_info:
        await reject("inc-123", decision, request)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "no open approval gate for this incident"

@pytest.mark.asyncio
async def test_grafana_raises_404_when_incident_has_no_snapshot():
    incident_obj = SimpleNamespace(
        alert=SimpleNamespace(grafana_snapshot=None)
    )

    storage = SimpleNamespace(
        get_incident=lambda incident_id: incident_obj,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await grafana("inc-123", request)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "no snapshot for this incident"


@pytest.mark.asyncio
async def test_grafana_raises_404_when_snapshot_file_missing(tmp_path):
    incident_obj = SimpleNamespace(
        alert=SimpleNamespace(
            grafana_snapshot=str(tmp_path / "missing.png")
        )
    )

    storage = SimpleNamespace(
        get_incident=lambda incident_id: incident_obj,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await grafana("inc-123", request)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "snapshot not available"


@pytest.mark.asyncio
async def test_grafana_returns_existing_png_snapshot(tmp_path):
    snapshot = tmp_path / "dashboard.png"
    snapshot.write_bytes(b"fake png content")

    incident_obj = SimpleNamespace(
        alert=SimpleNamespace(
            grafana_snapshot=str(snapshot)
        )
    )

    storage = SimpleNamespace(
        get_incident=lambda incident_id: incident_obj,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(storage=storage)
        )
    )

    result = await grafana("inc-123", request)

    assert result.path == snapshot
    assert result.media_type == "image/png"