"""Unit tests for the ADK orchestrator."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.adk.orchestrator import AdkOrchestrator, _sev_rank
from backend.adk.agents import build_agents
from backend.agents.base import RunContext
from backend.models import Alert, Incident, IncidentStatus, Severity
from backend.guardrails import ApprovalDecision, scrub_pii
from backend.tools import remediation as R

def test_severity_rank_orders_sev1_before_sev4():
    assert _sev_rank("SEV1") == 1
    assert _sev_rank("SEV2") == 2
    assert _sev_rank("SEV3") == 3
    assert _sev_rank("SEV4") == 4


def test_severity_rank_is_case_insensitive():
    assert _sev_rank("sev1") == 1
    assert _sev_rank("SeV2") == 2


def test_severity_rank_defaults_unknown_severity_to_sev4():
    assert _sev_rank("unknown") == 4
    assert _sev_rank("") == 4
    assert _sev_rank(None) == 4


def test_json_parses_valid_json():
    orchestrator = object.__new__(AdkOrchestrator)

    with patch(
        "backend.adk.orchestrator._extract_json",
        return_value={"severity": "SEV2"},
    ) as extract:

        result = orchestrator._json('{"severity": "SEV2"}')

    assert result == {"severity": "SEV2"}
    extract.assert_called_once_with('{"severity": "SEV2"}')


def test_json_returns_empty_dict_when_parsing_fails():
    orchestrator = object.__new__(AdkOrchestrator)

    with patch(
        "backend.adk.orchestrator._extract_json",
        side_effect=ValueError("invalid JSON"),
    ):

        result = orchestrator._json("not valid json")

    assert result == {}

def test_run_agent_collects_text_from_runner_events():
    orchestrator = object.__new__(AdkOrchestrator)

    session_service = MagicMock()
    session_service.create_session = AsyncMock(
        return_value=SimpleNamespace(id="session-1")
    )

    event_1 = SimpleNamespace(
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(text="First part"),
                SimpleNamespace(text="Second part"),
            ]
        )
    )
    event_2 = SimpleNamespace(
        content=SimpleNamespace(
            parts=[SimpleNamespace(text="Final answer")]
        )
    )

    runner = MagicMock()

    async def fake_run_async(**kwargs):
        yield event_1
        yield event_2

    runner.run_async = fake_run_async

    with patch(
        "backend.adk.orchestrator.InMemorySessionService",
        return_value=session_service,
    ), patch(
        "backend.adk.orchestrator.Runner",
        return_value=runner,
    ), patch(
        "backend.adk.orchestrator.types.Content",
        return_value="mock-content",
    ):
        result = asyncio.run(
            orchestrator._run_agent(
                agent=MagicMock(),
                incident_id="incident-1",
                message_parts=["hello"],
            )
        )

    assert result == "Final answer"
    session_service.create_session.assert_awaited_once_with(
        app_name="aegisops",
        user_id="incident-1",
        session_id="incident-1",
    )


def test_run_agent_ignores_events_without_text():
    orchestrator = object.__new__(AdkOrchestrator)

    session_service = MagicMock()
    session_service.create_session = AsyncMock(
        return_value=SimpleNamespace(id="session-1")
    )

    empty_event = SimpleNamespace(
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(text=None),
                SimpleNamespace(),
            ]
        )
    )

    final_event = SimpleNamespace(
        content=SimpleNamespace(
            parts=[SimpleNamespace(text="Final answer")]
        )
    )

    runner = MagicMock()

    async def fake_run_async(**kwargs):
        yield empty_event
        yield final_event

    runner.run_async = fake_run_async

    with patch(
        "backend.adk.orchestrator.InMemorySessionService",
        return_value=session_service,
    ), patch(
        "backend.adk.orchestrator.Runner",
        return_value=runner,
    ), patch(
        "backend.adk.orchestrator.types.Content",
        return_value="mock-content",
    ):
        result = asyncio.run(
            orchestrator._run_agent(
                agent=MagicMock(),
                incident_id="incident-1",
                message_parts=["hello"],
            )
        )

    assert result == "Final answer"

def test_handle_alert_marks_incident_failed_when_agent_raises():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(
        storage=storage,
    )
    orchestrator.deps = deps

    alert = Alert(
        alert="High error rate detected",
        service="payment-api",
        error_rate="25%",
    )

    emitted_events = []

    async def fake_emit(self, event_type, **kwargs):
        emitted_events.append((event_type, kwargs))

    async def failing_triage(*args, **kwargs):
        raise RuntimeError("triage failed")

    with patch(
    "backend.seed.seed_data.refresh_demo_timeline"
    ) as refresh_timeline, patch(
        "backend.adk.orchestrator.build_agents",
        return_value={"Triage": MagicMock()},
    ), patch.object(
        RunContext,
        "emit",
        new=fake_emit,
    ), patch.object(
        orchestrator,
        "_triage",
        new=failing_triage,
    ):
        result = asyncio.run(orchestrator.handle_alert(alert))

    refresh_timeline.assert_called_once_with(storage)

    assert result.status == IncidentStatus.FAILED
    assert result.service == "payment-api"

    event_names = [event[0] for event in emitted_events]

    assert "incident_created" in event_names
    assert "agent_error" in event_names
    assert "done" in event_names

    error_event = next(
        event for event in emitted_events
        if event[0] == "agent_error"
    )

    assert error_event[1]["agent"] == "adk-orchestrator"
    assert error_event[1]["error"] == "triage failed"

    done_event = next(
        event for event in emitted_events
        if event[0] == "done"
    )

    assert done_event[1]["status"] == IncidentStatus.FAILED.value

def test_handle_alert_transitions_to_triaged_after_successful_triage():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    alert = Alert(
        alert="High error rate detected",
        service="payment-api",
        error_rate="25%",
    )

    emitted_events = []

    async def fake_emit(self, event_type, **kwargs):
        emitted_events.append((event_type, kwargs))

    async def successful_triage(*args, **kwargs):
        return None

    with patch(
        "backend.seed.seed_data.refresh_demo_timeline"
    ), patch(
        "backend.adk.orchestrator.build_agents",
        return_value={"Triage": MagicMock()},
    ), patch.object(
        RunContext,
        "emit",
        new=fake_emit,
    ), patch.object(
        orchestrator,
        "_triage",
        new=successful_triage,
    ), patch.object(
        orchestrator,
        "_diagnosis",
        new=AsyncMock(side_effect=RuntimeError("stop after triage")),
    ):
        result = asyncio.run(orchestrator.handle_alert(alert))

    assert result.status == IncidentStatus.FAILED

    state_changes = [
        event for event in emitted_events
        if event[0] == "state_change"
    ]

    assert state_changes[0][1] == {
        "from": IncidentStatus.DETECTED.value,
        "to": IncidentStatus.TRIAGED.value,
    }

def test_handle_alert_transitions_to_diagnosed_after_successful_diagnosis():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    alert = Alert(
        alert="High error rate detected",
        service="payment-api",
        error_rate="25%",
    )

    emitted_events = []

    async def fake_emit(self, event_type, **kwargs):
        emitted_events.append((event_type, kwargs))

    async def successful_triage(*args, **kwargs):
        return None

    async def successful_diagnosis(*args, **kwargs):
        return None

    with patch(
        "backend.seed.seed_data.refresh_demo_timeline"
    ), patch(
        "backend.adk.orchestrator.build_agents",
        return_value={
            "Triage": MagicMock(),
            "Diagnosis": MagicMock(),
        },
    ), patch.object(
        RunContext,
        "emit",
        new=fake_emit,
    ), patch.object(
        orchestrator,
        "_triage",
        new=successful_triage,
    ), patch.object(
        orchestrator,
        "_diagnosis",
        new=successful_diagnosis,
    ), patch.object(
        orchestrator,
        "_correlation",
        new=AsyncMock(side_effect=RuntimeError("stop after diagnosis")),
    ):
        result = asyncio.run(orchestrator.handle_alert(alert))

    assert result.status == IncidentStatus.FAILED

    state_changes = [
        event for event in emitted_events
        if event[0] == "state_change"
    ]

    assert state_changes[0][1] == {
        "from": IncidentStatus.DETECTED.value,
        "to": IncidentStatus.TRIAGED.value,
    }

    assert state_changes[1][1] == {
        "from": IncidentStatus.TRIAGED.value,
        "to": IncidentStatus.DIAGNOSED.value,
    }

def test_handle_alert_transitions_to_correlated_after_successful_correlation():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    alert = Alert(
        alert="High error rate detected",
        service="payment-api",
        error_rate="25%",
    )

    emitted_events = []

    async def fake_emit(self, event_type, **kwargs):
        emitted_events.append((event_type, kwargs))

    async def successful_triage(*args, **kwargs):
        return None

    async def successful_diagnosis(*args, **kwargs):
        return None

    async def successful_correlation(*args, **kwargs):
        return None

    with patch(
        "backend.seed.seed_data.refresh_demo_timeline"
    ), patch(
        "backend.adk.orchestrator.build_agents",
        return_value={
            "Triage": MagicMock(),
            "Diagnosis": MagicMock(),
            "Correlation": MagicMock(),
        },
    ), patch.object(
        RunContext,
        "emit",
        new=fake_emit,
    ), patch.object(
        orchestrator,
        "_triage",
        new=successful_triage,
    ), patch.object(
        orchestrator,
        "_diagnosis",
        new=successful_diagnosis,
    ), patch.object(
        orchestrator,
        "_correlation",
        new=successful_correlation,
    ), patch.object(
        orchestrator,
        "_memory",
        new=AsyncMock(side_effect=RuntimeError("stop after correlation")),
    ):
        result = asyncio.run(orchestrator.handle_alert(alert))

    assert result.status == IncidentStatus.FAILED

    state_changes = [
        event for event in emitted_events
        if event[0] == "state_change"
    ]

    assert state_changes[0][1] == {
        "from": IncidentStatus.DETECTED.value,
        "to": IncidentStatus.TRIAGED.value,
    }

    assert state_changes[1][1] == {
        "from": IncidentStatus.TRIAGED.value,
        "to": IncidentStatus.DIAGNOSED.value,
    }

    assert state_changes[2][1] == {
        "from": IncidentStatus.DIAGNOSED.value,
        "to": IncidentStatus.CORRELATED.value,
    }

def test_triage_uses_rubric_when_model_severity_is_softer():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()

    incident = Incident(
        status=IncidentStatus.DETECTED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps
    rc = RunContext(incident, deps)

    capture = {
        "resolve_service_and_severity": {
            "rubric_severity": "SEV2",
            "blast_radius": "multi-service",
            "tier": "critical",
            "oncall": "payments-oncall",
            "error_rate_pct": 25,
        }
    }

    emitted_events = []

    async def fake_emit(self, event_type, **kwargs):
        emitted_events.append((event_type, kwargs))

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value='{"severity": "SEV3", "blast_radius": "single-service"}'
        ),
    ), patch.object(
        RunContext,
        "emit",
        new=fake_emit,
    ):
        asyncio.run(
            orchestrator._triage(
                rc,
                MagicMock(),
                capture,
            )
        )

    # Model proposed SEV3, but deterministic rubric says SEV2.
    # The orchestrator must not soften the severity.
    assert incident.severity == Severity.SEV2

    assert incident.service == "payment-api"
    assert incident.blast_radius == "single-service"

    assert incident.findings["triage"] == {
        "severity": "SEV2",
        "service": "payment-api",
        "tier": "critical",
        "blast_radius": "single-service",
        "oncall": "payments-oncall",
        "error_rate_pct": 25,
        "reasoning": "",
    }

    event_names = [event[0] for event in emitted_events]

    assert event_names == [
        "agent_start",
        "triage_result",
        "agent_end",
    ]

def test_triage_keeps_model_severity_when_more_severe_than_rubric():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.DETECTED,
        service="payment-api",
        alert=Alert(
            alert="Critical failure",
            service="payment-api",
            error_rate="50%",
        ),
    )

    rc = RunContext(incident, deps)

    capture = {
        "resolve_service_and_severity": {
            "rubric_severity": "SEV3",
            "blast_radius": "single-service",
            "tier": "standard",
            "oncall": "payments-oncall",
            "error_rate_pct": 50,
        }
    }

    emitted_events = []

    async def fake_emit(self, event_type, **kwargs):
        emitted_events.append((event_type, kwargs))

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value='{"severity": "SEV1", "blast_radius": "multi-service", "oncall": "critical-oncall"}'
        ),
    ), patch.object(
        RunContext,
        "emit",
        new=fake_emit,
    ):
        asyncio.run(
            orchestrator._triage(
                rc,
                MagicMock(),
                capture,
            )
        )

    assert incident.severity == Severity.SEV1
    assert incident.service == "payment-api"
    assert incident.blast_radius == "multi-service"

    assert incident.findings["triage"]["oncall"] == "critical-oncall"

def test_triage_falls_back_to_rubric_for_invalid_model_severity():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.DETECTED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    rc = RunContext(incident, deps)

    capture = {
        "resolve_service_and_severity": {
            "rubric_severity": "SEV2",
            "blast_radius": "single-service",
            "tier": "critical",
            "oncall": "payments-oncall",
            "error_rate_pct": 25,
        }
    }

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value='{"severity": "INVALID", "reasoning": "unclear"}'
        ),
    ), patch.object(
        RunContext,
        "emit",
        new=AsyncMock(),
    ):
        asyncio.run(
            orchestrator._triage(
                rc,
                MagicMock(),
                capture,
            )
        )

    assert incident.severity == Severity.SEV2
    assert incident.findings["triage"]["severity"] == "SEV2"

def test_diagnosis_without_grafana_snapshot():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.TRIAGED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
            grafana_snapshot=None,
        ),
    )

    rc = RunContext(incident, deps)
    capture = {}

    emitted_events = []

    async def fake_emit(self, event_type, **kwargs):
        emitted_events.append((event_type, kwargs))

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value='{"root_cause": "database connection exhaustion", '
                         '"confidence": 0.85, '
                         '"reasoning": "connection pool is saturated"}'
        ),
    ) as run_agent, patch.object(
        RunContext,
        "emit",
        new=fake_emit,
    ):
        asyncio.run(
            orchestrator._diagnosis(
                rc,
                MagicMock(),
                capture,
                incident.alert,
            )
        )

    run_agent.assert_awaited_once()

    assert "diagnosis" in incident.findings

    diagnosis = incident.findings["diagnosis"]

    assert diagnosis["summary"] == ""
    assert diagnosis["primary_symptom"] == ""
    assert diagnosis["dominant_class"] is None
    assert diagnosis["class_counts"] == {}
    assert diagnosis["error_count"] == 0
    assert diagnosis["vision"] == {
         "confirmed": None,
         "observation": "",
         "annotation": "",
         "has_image": False,
    }
    assert diagnosis["top_log_lines"] == []

    event_names = [event[0] for event in emitted_events]

    assert event_names[0] == "agent_start"
    assert "vision_result" not in event_names
    assert event_names[-1] == "agent_end"


def test_diagnosis_attaches_existing_grafana_snapshot(tmp_path):
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    snapshot = tmp_path / "grafana.png"
    snapshot.write_bytes(b"fake-png-data")

    incident = Incident(
        status=IncidentStatus.TRIAGED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
            grafana_snapshot=str(snapshot),
        ),
    )

    rc = RunContext(incident, deps)
    capture = {}

    emitted_events = []

    async def fake_emit(self, event_type, **kwargs):
        emitted_events.append((event_type, kwargs))

    mock_part = MagicMock()

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value=(
                '{"summary": "Database failure", '
                '"primary_symptom": "Connection errors", '
                '"vision_confirmed": true, '
                '"vision_observation": "Error spike visible", '
                '"vision_annotation": "High error region"}'
            )
        ),
    ) as run_agent, patch.object(
        RunContext,
        "emit",
        new=fake_emit,
    ), patch(
        "backend.adk.orchestrator.types.Part.from_bytes",
        return_value=mock_part,
    ) as from_bytes:

        asyncio.run(
            orchestrator._diagnosis(
                rc,
                MagicMock(),
                capture,
                incident.alert,
            )
        )

    from_bytes.assert_called_once_with(
        data=b"fake-png-data",
        mime_type="image/png",
    )

    run_agent.assert_awaited_once()

    message_parts = run_agent.await_args.args[2]

    assert len(message_parts) == 2
    assert message_parts[1] is mock_part

    diagnosis = incident.findings["diagnosis"]

    assert diagnosis["summary"] == "Database failure"
    assert diagnosis["primary_symptom"] == "Connection errors"
    assert diagnosis["vision"] == {
        "confirmed": True,
        "observation": "Error spike visible",
        "annotation": "High error region",
        "has_image": True,
    }

    vision_events = [
        event for event in emitted_events
        if event[0] == "vision_result"
    ]

    assert len(vision_events) == 1
    assert vision_events[0][1]["confirmed"] is True
    assert vision_events[0][1]["observation"] == "Error spike visible"
    assert vision_events[0][1]["annotation"] == "High error region"

def test_correlation_builds_suspect_from_top_recent_deployment():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.DIAGNOSED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    incident.findings["diagnosis"] = {
        "summary": "Database connection exhaustion",
        "primary_symptom": "Connection errors",
    }

    rc = RunContext(incident, deps)

    capture = {
        "query_recent_deploys": {
            "ranked": [
                {
                    "service": "payment-api",
                    "version": "v2.4.1",
                    "deployed_by": "jenkins",
                    "commit_sha": "abc123",
                    "minutes_before": 12,
                    "rollback_target": "v2.4.0",
                    "proximity_score": 0.8,
                }
            ],
            "top_suspect": {
                "service": "payment-api",
                "version": "v2.4.1",
                "deployed_by": "jenkins",
                "commit_sha": "abc123",
                "minutes_before": 12,
                "rollback_target": "v2.4.0",
                "proximity_score": 0.8,
            },
        }
    }

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value=(
                '{"confidence": 0.95, '
                '"probable_cause": "Bad payment-api deployment", '
                '"reasoning": "Incident started shortly after deployment"}'
            )
        ),
    ), patch.object(
        RunContext,
        "emit",
        new=AsyncMock(),
    ):
        asyncio.run(
            orchestrator._correlation(
                rc,
                MagicMock(),
                capture,
            )
        )

    # 0.8 proximity + 0.15 allowance = 0.95,
    # which matches the model confidence here.
    assert incident.confidence == 0.95

    assert incident.probable_cause == "Bad payment-api deployment"

    correlation = incident.findings["correlation"]

    assert correlation["confidence"] == 0.95
    assert correlation["probable_cause"] == "Bad payment-api deployment"

    assert correlation["suspect"] == {
        "service": "payment-api",
        "version": "v2.4.1",
        "deployed_by": "jenkins",
        "commit_sha": "abc123",
        "minutes_before": 12,
        "rollback_target": "v2.4.0",
    }

    assert correlation["ranked"] == capture["query_recent_deploys"]["ranked"]
    assert correlation["reasoning"] == (
        "Incident started shortly after deployment"
    )

def test_correlation_limits_confidence_when_no_recent_deployment():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.DIAGNOSED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    incident.findings["diagnosis"] = {
        "summary": "Unknown failure",
        "primary_symptom": "High error rate",
    }

    rc = RunContext(incident, deps)

    capture = {
        "query_recent_deploys": {
            "ranked": [],
            "top_suspect": None,
        }
    }

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value=(
                '{"confidence": 0.9, '
                '"probable_cause": "External dependency failure", '
                '"reasoning": "No deployment matches the incident window"}'
            )
        ),
    ), patch.object(
        RunContext,
        "emit",
        new=AsyncMock(),
    ):
        asyncio.run(
            orchestrator._correlation(
                rc,
                MagicMock(),
                capture,
            )
        )

    # Without a recent deployment, confidence is capped at 0.2.
    assert incident.confidence == 0.2

    assert incident.probable_cause == "External dependency failure"

    correlation = incident.findings["correlation"]

    assert correlation["confidence"] == 0.2
    assert correlation["suspect"] is None
    assert correlation["ranked"] == []
    assert correlation["reasoning"] == (
        "No deployment matches the incident window"
    )

def test_memory_uses_matching_past_incident():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.CORRELATED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    rc = RunContext(incident, deps)

    capture = {
        "search_incident_memory": {
            "matches": [
                {
                    "similarity": 0.82,
                    "typical_cause": "Bad deployment",
                    "typical_fix": "Rollback",
                    "avg_resolution_minutes": 12,
                    "past_incident_ids": ["inc-001", "inc-002"],
                    "times_seen": 2,
                }
            ]
        }
    }

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value='{"recommendation": "Rollback the deployment"}'
        ),
    ), patch.object(
        RunContext,
        "emit",
        new=AsyncMock(),
    ):
        asyncio.run(
            orchestrator._memory(
                rc,
                MagicMock(),
                capture,
            )
        )

    memory = incident.findings["memory"]

    assert memory["match"] == {
        "similarity": 0.82,
        "typical_cause": "Bad deployment",
        "typical_fix": "Rollback",
        "avg_resolution_minutes": 12,
        "past_incident_ids": ["inc-001", "inc-002"],
        "times_seen": 2,
    }

    assert memory["recommendation"] == "Rollback the deployment"

def test_memory_ignores_match_below_similarity_threshold():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    deps = SimpleNamespace(storage=storage)
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.CORRELATED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    rc = RunContext(incident, deps)

    capture = {
        "search_incident_memory": {
            "matches": [
                {
                    "similarity": 0.25,
                    "typical_cause": "Old unrelated issue",
                    "typical_fix": "Restart",
                    "avg_resolution_minutes": 30,
                    "past_incident_ids": ["inc-old"],
                    "times_seen": 1,
                }
            ]
        }
    }

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(
            return_value='{"recommendation": "Investigate current symptoms"}'
        ),
    ), patch.object(
        RunContext,
        "emit",
        new=AsyncMock(),
    ):
        asyncio.run(
            orchestrator._memory(
                rc,
                MagicMock(),
                capture,
            )
        )

    memory = incident.findings["memory"]

    assert memory["match"] is None
    assert memory["recommendation"] == "Investigate current symptoms"

def test_remediation_rejected_by_approval_gate():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    gate = MagicMock()
    gate.wait_for = AsyncMock(
        return_value=ApprovalDecision(
            approved=False,
            approver="oncall-user",
            note="Not approved",
        )
    )

    deps = SimpleNamespace(
        storage=storage,
        gate=gate,
    )
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.CORRELATED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    rc = RunContext(incident, deps)

    capture = {
        "propose_remediation": {
            "action": "rollback",
            "target": "payment-api",
            "risk": "high",
            "reversible": True,
            "requires_approval": True,
            "rationale": "Rollback the suspected deployment",
        }
    }

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(return_value='{"reasoning": "rollback recommended"}'),
    ), patch.object(
        RunContext,
        "emit",
        new=AsyncMock(),
    ), patch.object(
        R,
        "execute_remediation",
        new_callable=AsyncMock,
    ) as mock_execute:
        asyncio.run(
            orchestrator._remediation(
                rc,
                MagicMock(),
                capture,
            )
        )

    assert incident.status == IncidentStatus.REJECTED
    assert incident.remediation_plan.action == "rollback"
    assert incident.approved_by is None

    gate.open_gate.assert_called_once_with(incident.id)
    gate.wait_for.assert_awaited_once_with(incident.id)
    mock_execute.assert_not_awaited()

def test_remediation_approved_executes_and_resolves():
    orchestrator = object.__new__(AdkOrchestrator)

    storage = MagicMock()
    gate = MagicMock()
    gate.wait_for = AsyncMock(
        return_value=ApprovalDecision(
            approved=True,
            approver="oncall-user",
            note="Approved",
        )
    )

    deps = SimpleNamespace(
        storage=storage,
        gate=gate,
    )
    orchestrator.deps = deps

    incident = Incident(
        status=IncidentStatus.CORRELATED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    rc = RunContext(incident, deps)

    capture = {
        "propose_remediation": {
            "action": "rollback",
            "target": "payment-api",
            "risk": "high",
            "reversible": True,
            "requires_approval": True,
            "rollback_target": "v2.4.0",
            "rationale": "Rollback the suspected deployment",
        }
    }

    execution_result = R.ExecResult(
        ok=True,
        action="rollback",
        target="payment-api",
        simulated=True,
        steps=[
            R.ExecStep(
                label="Freeze deploys",
                ok=True,
                simulated=True,
                detail="Deploy pipeline frozen",
            ),
            R.ExecStep(
                label="Verify health",
                ok=True,
                simulated=True,
                detail="Health recovered",
            ),
        ],
    )

    with patch.object(
        orchestrator,
        "_run_agent",
        new=AsyncMock(return_value='{"reasoning": "rollback recommended"}'),
    ), patch.object(
        RunContext,
        "emit",
        new=AsyncMock(),
    ), patch.object(
        R,
        "execute_remediation",
        new=AsyncMock(return_value=execution_result),
    ) as mock_execute:
        asyncio.run(
            orchestrator._remediation(
                rc,
                MagicMock(),
                capture,
            )
        )

    assert incident.status == IncidentStatus.RESOLVED
    assert incident.approved_by == "oncall-user"
    assert incident.remediation_plan.action == "rollback"

    mock_execute.assert_awaited_once_with(
        incident.remediation_plan,
        "payment-api",
    )

    execution = incident.findings["execution"]
    assert execution["ok"] is True
    assert execution["action"] == "rollback"
    assert execution["target"] == "payment-api"
    assert execution["simulated"] is True
    assert len(execution["steps"]) == 2
