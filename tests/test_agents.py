"""Unit tests for ADK specialized agent construction."""

from unittest.mock import MagicMock, patch

from backend.adk import agents
from backend.adk.agents import build_agents


def test_model_uses_default_gemini_model():
    """Verify _model uses the configured default Gemini model."""
    fake_settings = MagicMock()
    fake_settings.gemini_model = "gemini-test-flash"

    with patch("backend.adk.agents.get_settings", return_value=fake_settings), \
         patch("backend.adk.agents.RetryGemini") as mock_retry:
        agents._model()

    mock_retry.assert_called_once_with(model="gemini-test-flash")


def test_model_uses_explicit_model_id():
    """Verify _model respects an explicitly supplied model ID."""
    with patch("backend.adk.agents.RetryGemini") as mock_retry:
        agents._model("gemini-explicit")

    mock_retry.assert_called_once_with(model="gemini-explicit")


def test_agent_constructs_llm_agent_with_expected_configuration():
    """Verify _agent passes configuration, tools, and callbacks to LlmAgent."""
    rc = MagicMock()
    capture = {}

    fake_model = MagicMock()
    fake_callbacks = {
        "before_model_callback": MagicMock(),
        "after_model_callback": MagicMock(),
    }

    with patch("backend.adk.agents._model", return_value=fake_model), \
         patch(
             "backend.adk.agents.make_callbacks",
             return_value=fake_callbacks,
         ), \
         patch("backend.adk.agents.LlmAgent") as mock_agent:

        result = agents._agent(
            "TestAgent",
            "test instruction",
            ["tool1", "tool2"],
            rc,
            capture,
        )

    assert result == mock_agent.return_value

    mock_agent.assert_called_once_with(
        name="TestAgent",
        model=fake_model,
        instruction="test instruction",
        tools=["tool1", "tool2"],
        **fake_callbacks,
    )


def test_build_agents_creates_all_six_agents():
    """Verify the complete specialized-agent set is constructed."""
    rc = MagicMock()
    capture = {}

    fake_agents = {
        "Triage": MagicMock(name="Triage"),
        "Diagnosis": MagicMock(name="Diagnosis"),
        "Correlation": MagicMock(name="Correlation"),
        "Memory": MagicMock(name="Memory"),
        "Remediation": MagicMock(name="Remediation"),
        "Comms": MagicMock(name="Comms"),
    }

    with patch(
        "backend.adk.agents._agent",
        side_effect=list(fake_agents.values()),
    ) as mock_agent, \
         patch("backend.adk.agents.get_settings") as mock_settings:

        mock_settings.return_value.gemini_model_pro = "gemini-test-pro"

        result = build_agents(rc, capture)

    assert set(result.keys()) == {
        "Triage",
        "Diagnosis",
        "Correlation",
        "Memory",
        "Remediation",
        "Comms",
    }
    assert len(result) == 6
    assert mock_agent.call_count == 6


def test_build_agents_uses_correct_tools_for_each_agent():
    """Verify each specialized agent receives its corresponding tool set."""
    rc = MagicMock()
    capture = {}

    tool_sets = {
        "triage": ["triage-tool"],
        "diagnosis": ["diagnosis-tool"],
        "correlation": ["correlation-tool"],
        "memory": ["memory-tool"],
        "remediation": ["remediation-tool"],
    }

    with patch(
        "backend.adk.agents._agent",
        side_effect=[
            MagicMock(name="Triage"),
            MagicMock(name="Diagnosis"),
            MagicMock(name="Correlation"),
            MagicMock(name="Memory"),
            MagicMock(name="Remediation"),
            MagicMock(name="Comms"),
        ],
    ) as mock_agent, \
         patch(
             "backend.adk.agents.TL.triage_tools",
             return_value=tool_sets["triage"],
         ) as triage_tools, \
         patch(
             "backend.adk.agents.TL.diagnosis_tools",
             return_value=tool_sets["diagnosis"],
         ) as diagnosis_tools, \
         patch(
             "backend.adk.agents.TL.correlation_tools",
             return_value=tool_sets["correlation"],
         ) as correlation_tools, \
         patch(
             "backend.adk.agents.TL.memory_tools",
             return_value=tool_sets["memory"],
         ) as memory_tools, \
         patch(
             "backend.adk.agents.TL.remediation_tools",
             return_value=tool_sets["remediation"],
         ) as remediation_tools, \
         patch("backend.adk.agents.get_settings") as mock_settings:

        mock_settings.return_value.gemini_model_pro = "gemini-test-pro"

        build_agents(rc, capture)

    triage_tools.assert_called_once_with(rc)
    diagnosis_tools.assert_called_once_with(rc)
    correlation_tools.assert_called_once_with(rc)
    memory_tools.assert_called_once_with(rc)
    remediation_tools.assert_called_once_with(rc)

    calls = mock_agent.call_args_list

    assert calls[0].args[0] == "Triage"
    assert calls[0].args[2] == tool_sets["triage"]

    assert calls[1].args[0] == "Diagnosis"
    assert calls[1].args[2] == tool_sets["diagnosis"]

    assert calls[2].args[0] == "Correlation"
    assert calls[2].args[2] == tool_sets["correlation"]

    assert calls[3].args[0] == "Memory"
    assert calls[3].args[2] == tool_sets["memory"]

    assert calls[4].args[0] == "Remediation"
    assert calls[4].args[2] == tool_sets["remediation"]

    assert calls[5].args[0] == "Comms"
    assert calls[5].args[2] == []


def test_build_agents_uses_pro_model_for_comms():
    """Verify only the Comms agent receives the configured Pro model ID."""
    rc = MagicMock()
    capture = {}

    with patch(
        "backend.adk.agents._agent",
        side_effect=[MagicMock() for _ in range(6)],
    ) as mock_agent, \
         patch("backend.adk.agents.get_settings") as mock_settings:

        mock_settings.return_value.gemini_model_pro = "gemini-test-pro"

        build_agents(rc, capture)

    calls = mock_agent.call_args_list

    # First five agents use the default model argument.
    for call in calls[:5]:
        assert call.kwargs.get("model_id") is None

    # Comms explicitly receives the Pro model.
    assert calls[5].kwargs["model_id"] == "gemini-test-pro"