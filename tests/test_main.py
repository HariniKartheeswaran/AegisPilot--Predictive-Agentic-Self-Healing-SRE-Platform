"""Tests for FastAPI route handlers in backend.main."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from backend.main import (
    Decision,
    approve,
    audit,
    demo_fire,
    grafana,
    health,
    incident,
    incidents,
    rca,
    registry,
    reject,
)
from backend.models import Alert


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


@pytest.mark.asyncio
async def test_demo_fire_live_mode():
    alert = Alert(
        alert="HighErrorRate",
        service="checkout-svc",
        error_rate="42%",
        metadata={"live_fire": True},
    )
    bus = SimpleNamespace(publish=AsyncMock())
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(storage=object(), bus=bus))
    )
    prepared = {"alert": alert, "load": {"ok": 1, "err": 1, "total": 2}}

    with (
        patch("backend.services.live_fire.live_mode_enabled", return_value=True),
        patch(
            "backend.services.live_fire.prepare_live_fire",
            return_value=prepared,
        ),
    ):
        result = await demo_fire(request)

    assert result["live"] is True
    assert result["scenario"] == "live"
    assert result["service"] == "checkout-svc"
    bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_demo_fire_seed_scenario():
    bus = SimpleNamespace(publish=AsyncMock())
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(storage=object(), bus=bus))
    )
    sc = SimpleNamespace(
        key="hikaricp",
        alert={
            "alert": "HighErrorRate",
            "service": "checkout-svc",
            "error_rate": "35%",
        },
    )
    with (
        patch("backend.services.live_fire.live_mode_enabled", return_value=False),
        patch("backend.main.next_scenario", return_value=sc),
    ):
        result = await demo_fire(request)

    assert result["accepted"] is True
    assert result["scenario"] == "hikaricp"
    bus.publish.assert_awaited_once()
@pytest.mark.asyncio
async def test_post_alert_publishes():
    from backend.main import post_alert

    bus = SimpleNamespace(publish=AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=bus)))
    alert = Alert(alert="HighErrorRate", service="cart-svc", error_rate="20%")
    out = await post_alert(alert, request)
    assert out["accepted"] is True
    bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_deploy():
    from backend.main import record_deploy

    storage = MagicMock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(storage=storage)))
    out = await record_deploy(
        request,
        service="checkout-svc",
        version="v1.2.3",
        commit_sha="abcdef123456",
        deployed_by="jenkins",
        rollback_target="v1.2.2",
    )
    assert out["accepted"] is True
    assert out["version"] == "v1.2.3"
    storage.add_deploy.assert_called_once()


@pytest.mark.asyncio
async def test_pubsub_push_acks():
    from backend.main import pubsub_push
    import base64
    import json

    alert = {"alert": "HighErrorRate", "service": "checkout-svc", "error_rate": "10%"}
    raw = base64.b64encode(json.dumps(alert).encode()).decode()
    orchestrator = SimpleNamespace(handle_alert=AsyncMock())
    request = SimpleNamespace(
        json=AsyncMock(return_value={"message": {"data": raw}}),
        app=SimpleNamespace(state=SimpleNamespace(orchestrator=orchestrator)),
    )
    resp = await pubsub_push(request)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_custom_incident_ingests_logs(tmp_path, monkeypatch):
    from backend.main import custom_incident

    monkeypatch.setattr("backend.main.SEED_DIR", tmp_path / "seed")
    storage = MagicMock()
    bus = SimpleNamespace(publish=AsyncMock())
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(storage=storage, bus=bus))
    )
    out = await custom_incident(
        request,
        service="checkout-svc",
        alert="HighErrorRate",
        error_rate="50%",
        logs="ERROR pool exhausted\nWARN slow query\nok path",
        deploy_version="v9.9.9",
        rollback_target="v9.9.8",
        image=None,
    )
    assert out["accepted"] is True
    assert out["logs_ingested"] == 3
    assert out["deploy"] is True
    assert storage.add_log.call_count == 3
    storage.add_deploy.assert_called_once()
