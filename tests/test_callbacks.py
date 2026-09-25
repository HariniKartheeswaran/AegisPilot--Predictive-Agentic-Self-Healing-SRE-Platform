"""Unit tests for ADK lifecycle callbacks."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.adk.callbacks import _text_of, _tokens_of, make_callbacks
from backend.models import AuditStep


def test_text_of_returns_empty_string_for_none():
    assert _text_of(None) == ""


def test_text_of_extracts_text_from_parts():
    content = SimpleNamespace(
        parts=[
            SimpleNamespace(text="Hello "),
            SimpleNamespace(text="world"),
        ]
    )

    assert _text_of(content) == "Hello world"


def test_text_of_ignores_parts_without_text():
    content = SimpleNamespace(
        parts=[
            SimpleNamespace(text="Hello"),
            SimpleNamespace(text=None),
            SimpleNamespace(function_call={"name": "test"}),
        ]
    )

    assert _text_of(content) == "Hello"


def test_tokens_of_returns_total_token_count():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(total_token_count=42)
    )

    assert _tokens_of(response) == 42


def test_tokens_of_returns_zero_when_usage_metadata_missing():
    response = SimpleNamespace(usage_metadata=None)

    assert _tokens_of(response) == 0


def test_make_callbacks_returns_all_callbacks():
    rc = MagicMock()
    capture = {}

    callbacks = make_callbacks(rc, "Triage", capture)

    assert set(callbacks) == {
        "before_model_callback",
        "after_model_callback",
        "after_tool_callback",
    }

    assert callable(callbacks["before_model_callback"])
    assert callable(callbacks["after_model_callback"])
    assert callable(callbacks["after_tool_callback"])


@pytest.mark.anyio
async def test_before_model_emits_reasoning_start():
    rc = MagicMock()
    rc.emit = AsyncMock()
    capture = {}

    callbacks = make_callbacks(rc, "Triage", capture)

    await callbacks["before_model_callback"](
        MagicMock(),
        MagicMock(),
    )

    rc.emit.assert_awaited_once_with(
        "reasoning_start",
        agent="Triage",
        step="model",
    )


@pytest.mark.anyio
async def test_after_model_records_and_emits_text_response():
    rc = MagicMock()
    rc.incident.id = "inc-123"
    rc.emit = AsyncMock()
    rc.deps.storage.add_audit_step = MagicMock()
    capture = {}

    callbacks = make_callbacks(
        rc,
        "Diagnosis",
        capture,
        model_id="gemini-test-pro",
    )

    response = SimpleNamespace(
        content=SimpleNamespace(
            parts=[SimpleNamespace(text="CPU usage increased to 95%.")]
        ),
        usage_metadata=SimpleNamespace(total_token_count=25),
    )

    with patch(
        "backend.adk.callbacks.time.perf_counter",
        side_effect=[100.0, 100.0, 100.125],
    ):
        await callbacks["before_model_callback"](
            MagicMock(),
            MagicMock(),
        )
        await callbacks["after_model_callback"](
            MagicMock(),
            response,
        )

    rc.deps.storage.add_audit_step.assert_called_once()

    audit_step = rc.deps.storage.add_audit_step.call_args.args[0]

    assert isinstance(audit_step, AuditStep)
    assert audit_step.incident_id == "inc-123"
    assert audit_step.agent == "Diagnosis"
    assert audit_step.step == "reasoning"
    assert audit_step.reasoning == "CPU usage increased to 95%."
    assert audit_step.output == "CPU usage increased to 95%."
    assert audit_step.tokens == 25
    assert isinstance(audit_step.latency_ms, int)
    assert audit_step.latency_ms >= 0

    rc.emit.assert_any_await(
        "reasoning",
        agent="Diagnosis",
        step="model",
        text="CPU usage increased to 95%.",
        tokens=25,
        latency_ms=audit_step.latency_ms,
        attempts=1,
        model="gemini-test-pro",
    )


@pytest.mark.anyio
async def test_after_model_skips_empty_text_response():
    rc = MagicMock()
    rc.incident.id = "inc-123"
    rc.emit = AsyncMock()
    rc.deps.storage.add_audit_step = MagicMock()
    capture = {}

    callbacks = make_callbacks(rc, "Triage", capture)

    response = SimpleNamespace(
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(
                    function_call={"name": "resolve_service_and_severity"}
                )
            ]
        ),
        usage_metadata=SimpleNamespace(total_token_count=10),
    )

    await callbacks["before_model_callback"](
        MagicMock(),
        MagicMock(),
   )
    await callbacks["after_model_callback"](
        MagicMock(),
        response,
)
    rc.deps.storage.add_audit_step.assert_not_called()

    # Only before_model's reasoning_start event should exist.
    assert rc.emit.await_count == 1
    rc.emit.assert_awaited_once_with(
        "reasoning_start",
        agent="Triage",
        step="model",
    )


@pytest.mark.anyio
async def test_after_tool_captures_output_and_records_audit():
    rc = MagicMock()
    rc.incident.id = "inc-456"
    rc.emit = AsyncMock()
    rc.deps.storage.add_audit_step = MagicMock()
    capture = {}

    callbacks = make_callbacks(rc, "Triage", capture)

    tool = MagicMock()
    tool.name = "resolve_service_and_severity"

    args = {
        "service": "checkout-svc",
        "error_rate": 15,
    }

    tool_response = {
        "severity": "SEV2",
        "blast_radius": "checkout-svc",
    }

    await callbacks["after_tool_callback"](
        tool,
        args,
        MagicMock(),
        tool_response,
    )

    assert capture[tool.name] == tool_response

    rc.deps.storage.add_audit_step.assert_called_once()

    audit_step = rc.deps.storage.add_audit_step.call_args.args[0]

    assert isinstance(audit_step, AuditStep)
    assert audit_step.incident_id == "inc-456"
    assert audit_step.agent == "Triage"
    assert audit_step.step == "tool:resolve_service_and_severity"
    assert json.loads(audit_step.tool_call) == args
    assert json.loads(audit_step.output) == tool_response

    rc.emit.assert_awaited_once_with(
        "tool_call",
        agent="Triage",
        tool="resolve_service_and_severity",
        detail=json.dumps(args, default=str),
        output=json.dumps(tool_response, default=str)[:1500],
    )


@pytest.mark.anyio
async def test_after_tool_handles_string_output():
    rc = MagicMock()
    rc.incident.id = "inc-789"
    rc.emit = AsyncMock()
    rc.deps.storage.add_audit_step = MagicMock()
    capture = {}

    callbacks = make_callbacks(rc, "Memory", capture)

    tool = MagicMock()
    tool.name = "search_incident_memory"

    args = {"fingerprint": "abc123"}
    tool_response = "No strong prior incident found."

    await callbacks["after_tool_callback"](
        tool,
        args,
        MagicMock(),
        tool_response,
    )

    assert capture[tool.name] == tool_response

    audit_step = rc.deps.storage.add_audit_step.call_args.args[0]

    assert audit_step.output == tool_response

    rc.emit.assert_awaited_once_with(
        "tool_call",
        agent="Memory",
        tool="search_incident_memory",
        detail=json.dumps(args, default=str),
        output=tool_response,
    )