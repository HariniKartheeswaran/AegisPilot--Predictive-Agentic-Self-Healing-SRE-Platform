
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.agents.base import BaseAgent, Deps, RunContext
from backend.guardrails import ApprovalGate
from backend.models import Incident, IncidentStatus
from backend.services.gemini import GenResult


def make_context():
    incident = Incident(
        id="inc-test",
        status=IncidentStatus.DETECTED,
        detected_at=1000,
    )

    storage = MagicMock()
    gemini = MagicMock()
    hub = MagicMock()
    hub.publish = AsyncMock()
    gate = MagicMock(spec=ApprovalGate)

    deps = Deps(
        storage=storage,
        gemini=gemini,
        hub=hub,
        gate=gate,
    )

    return RunContext(incident, deps)


@pytest.mark.anyio
async def test_emit_publishes_stream_event():
    ctx = make_context()

    await ctx.emit(
        "reasoning",
        agent="Triage",
        step="model",
        text="Investigating incident",
    )

    ctx.deps.hub.publish.assert_awaited_once()

    event = ctx.deps.hub.publish.call_args.args[0]
    assert event.type == "reasoning"
    assert event.incident_id == "inc-test"
    assert event.agent == "Triage"
    assert event.payload["step"] == "model"
    assert event.payload["text"] == "Investigating incident"


def test_persist_step_delegates_to_storage():
    ctx = make_context()
    step = MagicMock()

    ctx._persist_step(step)

    ctx.deps.storage.add_audit_step.assert_called_once_with(step)


@pytest.mark.anyio
async def test_think_with_text_response():
    ctx = make_context()

    result = GenResult(
        text="The database connection pool is exhausted.",
        tokens=25,
        latency_ms=120,
        model="gemini-test",
        attempts=1,
    )
    ctx.deps.gemini.generate = AsyncMock(return_value=result)

    value, returned = await ctx.think(
        agent="Diagnosis",
        step="root_cause",
        prompt="Analyze the incident",
    )

    assert value == result.text
    assert returned is result

    ctx.deps.gemini.generate.assert_awaited_once_with(
        "Analyze the incident",
        system=None,
        response_json=False,
        temperature=0.3,
        model=None,
    )

    ctx.deps.storage.add_audit_step.assert_called_once()
    audit = ctx.deps.storage.add_audit_step.call_args.args[0]
    assert audit.agent == "Diagnosis"
    assert audit.step == "root_cause"
    assert audit.input == "Analyze the incident"
    assert audit.reasoning == result.text
    assert audit.tokens == 25
    assert audit.latency_ms == 120


@pytest.mark.anyio
async def test_think_with_json_response_uses_reasoning_field():
    ctx = make_context()

    result = GenResult(
        text='{"reasoning": "Pool exhaustion caused the errors.", "cause": "database"}',
        tokens=30,
        latency_ms=150,
        model="gemini-test",
        attempts=2,
    )
    result.json = MagicMock(
        return_value={
            "reasoning": "Pool exhaustion caused the errors.",
            "cause": "database",
        }
    )
    ctx.deps.gemini.generate = AsyncMock(return_value=result)

    value, _ = await ctx.think(
        agent="Diagnosis",
        step="analysis",
        prompt="Analyze",
        response_json=True,
        temperature=0.2,
        system="You are an SRE.",
        model="custom-model",
    )

    assert value["cause"] == "database"
    assert value["reasoning"] == "Pool exhaustion caused the errors."

    ctx.deps.gemini.generate.assert_awaited_once_with(
        "Analyze",
        system="You are an SRE.",
        response_json=True,
        temperature=0.2,
        model="custom-model",
    )

    audit = ctx.deps.storage.add_audit_step.call_args.args[0]
    assert audit.reasoning == "Pool exhaustion caused the errors."
    assert audit.output == '{"reasoning": "Pool exhaustion caused the errors.", "cause": "database"}'


@pytest.mark.anyio
async def test_think_handles_malformed_json():
    ctx = make_context()

    result = GenResult(
        text="not valid json",
        tokens=10,
        latency_ms=80,
        model="gemini-test",
    )
    result.json = MagicMock(side_effect=ValueError("invalid JSON"))
    ctx.deps.gemini.generate = AsyncMock(return_value=result)

    value, _ = await ctx.think(
        agent="Diagnosis",
        step="analysis",
        prompt="Analyze",
        response_json=True,
    )

    assert value == {
        "_parse_error": True,
        "raw": "not valid json",
    }

    audit = ctx.deps.storage.add_audit_step.call_args.args[0]
    assert audit.reasoning == "not valid json"


@pytest.mark.anyio
async def test_think_with_image_uses_generate_vision():
    ctx = make_context()

    result = GenResult(
        text="Grafana shows elevated latency.",
        tokens=20,
        latency_ms=100,
        model="gemini-vision",
    )
    ctx.deps.gemini.generate_vision = AsyncMock(return_value=result)

    value, returned = await ctx.think(
        agent="Diagnosis",
        step="visual_analysis",
        prompt="Analyze dashboard",
        image_path="grafana.png",
        system="Analyze carefully",
        response_json=False,
        temperature=0.1,
    )

    assert value == result.text
    assert returned is result

    ctx.deps.gemini.generate_vision.assert_awaited_once_with(
        "Analyze dashboard",
        "grafana.png",
        system="Analyze carefully",
        response_json=False,
        temperature=0.1,
    )
    ctx.deps.gemini.generate.assert_not_called()


@pytest.mark.anyio
async def test_tool_records_string_output():
    ctx = make_context()

    await ctx.tool(
        agent="Triage",
        tool_name="fetch_logs",
        detail="service=payment-api",
        output="log output",
    )

    ctx.deps.storage.add_audit_step.assert_called_once()

    audit = ctx.deps.storage.add_audit_step.call_args.args[0]
    assert audit.agent == "Triage"
    assert audit.step == "tool:fetch_logs"
    assert audit.tool_call == "service=payment-api"
    assert audit.output == "log output"


@pytest.mark.anyio
async def test_tool_serializes_non_string_output():
    ctx = make_context()

    await ctx.tool(
        agent="Diagnosis",
        tool_name="analyze_logs",
        detail="limit=100",
        output={"dominant_class": "db_pool_exhaustion"},
    )

    audit = ctx.deps.storage.add_audit_step.call_args.args[0]
    assert '"dominant_class": "db_pool_exhaustion"' in audit.output


def test_remember_updates_incident_and_persists():
    ctx = make_context()

    ctx.remember("probable_cause", "database connection pool exhausted")

    assert ctx.incident.findings["probable_cause"] == (
        "database connection pool exhausted"
    )
    ctx.deps.storage.save_incident.assert_called_once_with(ctx.incident)


@pytest.mark.anyio
async def test_transition_allows_legal_transition():
    ctx = make_context()

    await ctx.transition(IncidentStatus.TRIAGED)

    assert ctx.incident.status == IncidentStatus.TRIAGED
    ctx.deps.storage.save_incident.assert_called_once_with(ctx.incident)

    event = ctx.deps.hub.publish.call_args.args[0]
    assert event.type == "state_change"
    assert event.payload["from"] == "DETECTED"
    assert event.payload["to"] == "TRIAGED"


@pytest.mark.anyio
async def test_transition_rejects_illegal_transition():
    ctx = make_context()

    with pytest.raises(ValueError, match="Illegal transition"):
        await ctx.transition(IncidentStatus.RESOLVED)

    assert ctx.incident.status == IncidentStatus.DETECTED
    ctx.deps.storage.save_incident.assert_not_called()


@pytest.mark.anyio
async def test_base_agent_run_success():
    ctx = make_context()

    class TestAgent(BaseAgent):
        name = "TestAgent"
        headline = "Testing"
        allowed_tools = ["test_tool"]

        async def execute(self, ctx):
            ctx.remember("executed", True)

    agent = TestAgent()

    await agent.run(ctx)

    events = [
        call.args[0]
        for call in ctx.deps.hub.publish.await_args_list
    ]

    assert [event.type for event in events] == [
        "agent_start",
        "agent_end",
    ]
    assert events[0].agent == "TestAgent"
    assert events[0].payload["headline"] == "Testing"
    assert events[0].payload["tools"] == ["test_tool"]
    assert ctx.incident.findings["executed"] is True


@pytest.mark.anyio
async def test_base_agent_run_emits_error_and_reraises():
    ctx = make_context()

    class FailingAgent(BaseAgent):
        name = "FailingAgent"

        async def execute(self, ctx):
            raise RuntimeError("agent failed")

    agent = FailingAgent()

    with pytest.raises(RuntimeError, match="agent failed"):
        await agent.run(ctx)

    events = [
        call.args[0]
        for call in ctx.deps.hub.publish.await_args_list
    ]

    assert [event.type for event in events] == [
        "agent_start",
        "agent_error",
    ]
    assert events[1].payload["error"] == "agent failed"


@pytest.mark.anyio
async def test_base_agent_execute_is_abstract_behavior():
    ctx = make_context()
    agent = BaseAgent()

    with pytest.raises(NotImplementedError):
        await agent.execute(ctx)
