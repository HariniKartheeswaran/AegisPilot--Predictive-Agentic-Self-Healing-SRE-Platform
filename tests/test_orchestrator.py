
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.agents.base import Deps
from backend.models import Alert, IncidentStatus
from backend.orchestrator import Orchestrator


def make_alert():
    return Alert(
        alert="High error rate detected",
        service="payment-api",
        error_rate="25%",
    )


def make_deps():
    storage = Mock()
    storage.save_incident = Mock()

    hub = Mock()
    hub.publish = AsyncMock()

    return Deps(
        storage=storage,
        gemini=Mock(),
        hub=hub,
        gate=Mock(),
    )

@pytest.mark.asyncio
async def test_handle_alert_runs_full_incident_lifecycle():
    deps = make_deps()
    orchestrator = Orchestrator(deps)

    call_order = []

    async def triage(ctx):
        call_order.append("triage")

    async def diagnosis(ctx):
        call_order.append("diagnosis")

    async def correlation(ctx):
        call_order.append("correlation")

    async def memory(ctx):
        call_order.append("memory")

    async def remediation(ctx):
        call_order.append("remediation")

    async def comms(ctx):
        call_order.append("comms")

    orchestrator.triage.run = triage
    orchestrator.diagnosis.run = diagnosis
    orchestrator.correlation.run = correlation
    orchestrator.memory.run = memory
    orchestrator.remediation.run = remediation
    orchestrator.comms.run = comms

    with patch("backend.seed.seed_data.refresh_demo_timeline") as refresh, \
         patch("backend.tools.memory.learn_incident") as learn:

        incident = await orchestrator.handle_alert(make_alert())

    refresh.assert_called_once_with(deps.storage)

    assert call_order == [
        "triage",
        "diagnosis",
        "correlation",
        "memory",
        "remediation",
        "comms",
    ]

    assert incident.service == "payment-api"
    assert incident.status != IncidentStatus.FAILED
    assert deps.storage.save_incident.called
    learn.assert_called_once_with(deps.storage, incident)


@pytest.mark.asyncio
async def test_handle_alert_creates_detected_incident():
    deps = make_deps()
    orchestrator = Orchestrator(deps)

    orchestrator.triage.run = AsyncMock()
    orchestrator.diagnosis.run = AsyncMock()
    orchestrator.correlation.run = AsyncMock()
    orchestrator.memory.run = AsyncMock()
    orchestrator.remediation.run = AsyncMock()
    orchestrator.comms.run = AsyncMock()

    with patch("backend.seed.seed_data.refresh_demo_timeline"), \
         patch("backend.orchestrator.learn_incident", create=True):

        incident = await orchestrator.handle_alert(make_alert())

    saved_incident = deps.storage.save_incident.call_args_list[0].args[0]

    assert saved_incident.service == "payment-api"
    assert saved_incident.alert.service == "payment-api"
    assert incident is saved_incident

@pytest.mark.asyncio
async def test_handle_alert_moves_through_detection_states():
    deps = make_deps()
    orchestrator = Orchestrator(deps)

    orchestrator.triage.run = AsyncMock()
    orchestrator.diagnosis.run = AsyncMock()
    orchestrator.correlation.run = AsyncMock()
    orchestrator.memory.run = AsyncMock()
    orchestrator.remediation.run = AsyncMock()
    orchestrator.comms.run = AsyncMock()

    with patch("backend.seed.seed_data.refresh_demo_timeline"), \
         patch("backend.orchestrator.learn_incident", create=True):

        incident = await orchestrator.handle_alert(make_alert())

    assert incident.status != IncidentStatus.FAILED

    orchestrator.triage.run.assert_awaited_once()
    orchestrator.diagnosis.run.assert_awaited_once()
    orchestrator.correlation.run.assert_awaited_once()
    orchestrator.memory.run.assert_awaited_once()
    orchestrator.remediation.run.assert_awaited_once()
    orchestrator.comms.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_alert_marks_incident_failed_when_agent_raises():
    deps = make_deps()
    orchestrator = Orchestrator(deps)

    orchestrator.triage.run = AsyncMock(
        side_effect=RuntimeError("triage failed")
    )

    with patch("backend.seed.seed_data.refresh_demo_timeline"):

        incident = await orchestrator.handle_alert(make_alert())

    assert incident.status == IncidentStatus.FAILED

    saved_incident = deps.storage.save_incident.call_args_list[-1].args[0]
    assert saved_incident.status == IncidentStatus.FAILED


@pytest.mark.asyncio
async def test_handle_alert_stops_pipeline_after_failure():
    deps = make_deps()
    orchestrator = Orchestrator(deps)

    orchestrator.triage.run = AsyncMock(
        side_effect=RuntimeError("triage failed")
    )
    orchestrator.diagnosis.run = AsyncMock()
    orchestrator.correlation.run = AsyncMock()
    orchestrator.memory.run = AsyncMock()
    orchestrator.remediation.run = AsyncMock()
    orchestrator.comms.run = AsyncMock()

    with patch("backend.seed.seed_data.refresh_demo_timeline"):

        incident = await orchestrator.handle_alert(make_alert())

    assert incident.status == IncidentStatus.FAILED

    orchestrator.triage.run.assert_awaited_once()
    orchestrator.diagnosis.run.assert_not_awaited()
    orchestrator.correlation.run.assert_not_awaited()
    orchestrator.memory.run.assert_not_awaited()
    orchestrator.remediation.run.assert_not_awaited()
    orchestrator.comms.run.assert_not_awaited()

@pytest.mark.asyncio
async def test_handle_alert_swallows_failure_in_failure_handling():
    deps = make_deps()
    orchestrator = Orchestrator(deps)

    orchestrator.triage.run = AsyncMock(
        side_effect=RuntimeError("triage failed")
    )

    emit = AsyncMock(
        side_effect=[None, RuntimeError("event bus unavailable")]
    )

    with patch("backend.seed.seed_data.refresh_demo_timeline"), \
         patch.object(
             __import__(
                 "backend.agents.base",
                 fromlist=["RunContext"],
             ).RunContext,
             "emit",
             emit,
         ):

        incident = await orchestrator.handle_alert(make_alert())

    assert incident.status == IncidentStatus.FAILED
    assert emit.await_count == 2
