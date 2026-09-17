"""ADK callbacks that bridge every agent step to SSE + the audit trail.

Same observability guarantee as the local path (spec §7 "every decision
observable"), but sourced from real ADK lifecycle hooks:
  * before/after MODEL  → stream each reasoning turn with token + latency badges
  * after TOOL          → stream each tool call AND capture its return value so
                          the orchestrator can build authoritative findings from
                          the real tool outputs (not the model's paraphrase).

A callback set is bound per-agent to the incident's RunContext and a shared
`capture` dict.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from google.adk.models import LlmRequest, LlmResponse
from google.adk.tools.base_tool import BaseTool

from backend.agents.base import RunContext
from backend.config import get_settings
from backend.models import AuditStep

_MODEL_ID = get_settings().gemini_model


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    parts = getattr(content, "parts", None) or []
    return "".join(p.text for p in parts if getattr(p, "text", None))


def _tokens_of(resp: LlmResponse) -> int:
    usage = getattr(resp, "usage_metadata", None)
    if usage is not None:
        return int(getattr(usage, "total_token_count", 0) or 0)
    return 0


def make_callbacks(rc: RunContext, agent_name: str, capture: dict[str, Any],
                   model_id: str | None = None) -> dict:
    """Build the before/after model + tool callbacks for one LlmAgent."""
    model_label = model_id or _MODEL_ID
    # Per-agent stack of model-call start times (calls are sequential).
    timing: dict[str, float] = {}

    async def before_model(callback_context, llm_request: LlmRequest):  # noqa: ANN001
        timing["t0"] = time.perf_counter()
        await rc.emit("reasoning_start", agent=agent_name, step="model")
        return None  # don't short-circuit; let the real call proceed

    async def after_model(callback_context, llm_response: LlmResponse):  # noqa: ANN001
        latency_ms = int((time.perf_counter() - timing.get("t0", time.perf_counter())) * 1000)
        text = _text_of(getattr(llm_response, "content", None))
        tokens = _tokens_of(llm_response)
        # A pure function-call turn has no text — skip the reasoning line for it;
        # the tool_call event will represent that step instead.
        if text.strip():
            rc.deps.storage.add_audit_step(
                AuditStep(
                    incident_id=rc.incident.id, agent=agent_name, step="reasoning",
                    reasoning=text[:4000], output=text[:4000],
                    tokens=tokens, latency_ms=latency_ms,
                )
            )
            await rc.emit(
                "reasoning", agent=agent_name, step="model", text=text,
                tokens=tokens, latency_ms=latency_ms, attempts=1, model=model_label,
            )
        return None

    async def after_tool(
        tool: BaseTool, args: dict[str, Any], tool_context, tool_response: Any  # noqa: ANN001
    ):
        # Capture the REAL tool output so findings are grounded in it.
        capture[tool.name] = tool_response
        out_str = (
            tool_response if isinstance(tool_response, str)
            else json.dumps(tool_response, default=str)
        )
        rc.deps.storage.add_audit_step(
            AuditStep(
                incident_id=rc.incident.id, agent=agent_name, step=f"tool:{tool.name}",
                tool_call=json.dumps(args, default=str)[:2000], output=out_str[:4000],
            )
        )
        await rc.emit(
            "tool_call", agent=agent_name, tool=tool.name,
            detail=json.dumps(args, default=str), output=out_str[:1500],
        )
        return None  # keep the tool's real return value

    return {
        "before_model_callback": before_model,
        "after_model_callback": after_model,
        "after_tool_callback": after_tool,
    }
