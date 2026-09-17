"""Shared agent runtime contract.

Every specialized agent subclasses `BaseAgent` and implements `execute`. The
`RunContext` gives each agent the exact same, small toolkit for the three things
an agent does:

  1. think()  — one retry-wrapped Gemini call, auto-streamed + auto-audited
  2. tool()   — record a real tool invocation (streamed + audited)
  3. remember(): write a durable finding onto the incident

Because thinking and tool calls are funneled through here, EVERY step lands in
both the audit_log (durable) and the SSE stream (live) with token + latency
badges — that's spec §7 "every decision observable" for free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from backend.guardrails import ApprovalGate
from backend.models import (
    AuditStep,
    Incident,
    IncidentStatus,
    LEGAL_TRANSITIONS,
    StreamEvent,
)
from backend.services.gemini import GeminiService, GenResult
from backend.services.storage import StorageService
from backend.services.stream import StreamHub


@dataclass
class Deps:
    """Injected services. Swapping impls (Firestore/PubSub) touches only this."""

    storage: StorageService
    gemini: GeminiService
    hub: StreamHub
    gate: ApprovalGate


class RunContext:
    """Per-incident execution context threaded through every agent."""

    def __init__(self, incident: Incident, deps: Deps) -> None:
        self.incident = incident
        self.deps = deps

    # ---------------------------------------------------------------- stream
    async def emit(
        self, event_type: str, *, agent: Optional[str] = None, **payload: Any
    ) -> None:
        await self.deps.hub.publish(
            StreamEvent(
                type=event_type,
                incident_id=self.incident.id,
                agent=agent,
                payload=payload,
            )
        )

    # ---------------------------------------------------------------- audit
    def _persist_step(self, step: AuditStep) -> None:
        self.deps.storage.add_audit_step(step)

    # --------------------------------------------------------------- thinking
    async def think(
        self,
        agent: str,
        step: str,
        prompt: str,
        *,
        system: Optional[str] = None,
        response_json: bool = False,
        temperature: float = 0.3,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
    ) -> tuple[Any, GenResult]:
        """One observable Gemini call.

        Returns (value, GenResult) where value is a parsed dict when
        response_json=True, else the raw text. Streams a `reasoning` event and
        writes an audit row with real token + latency numbers.
        """
        await self.emit("reasoning_start", agent=agent, step=step)
        if image_path:
            result = await self.deps.gemini.generate_vision(
                prompt, image_path, system=system,
                response_json=response_json, temperature=temperature,
            )
        else:
            result = await self.deps.gemini.generate(
                prompt, system=system,
                response_json=response_json, temperature=temperature, model=model,
            )

        value: Any = result.text
        reasoning_text = result.text
        if response_json:
            try:
                value = result.json()
                # Prefer a model-authored "reasoning" field for the live panel.
                reasoning_text = value.get("reasoning") or json.dumps(value, indent=2)
            except Exception:  # noqa: BLE001 — malformed JSON shouldn't kill the run
                value = {"_parse_error": True, "raw": result.text}
                reasoning_text = result.text

        self._persist_step(
            AuditStep(
                incident_id=self.incident.id, agent=agent, step=step,
                input=prompt[:2000], reasoning=reasoning_text[:4000],
                output=json.dumps(value)[:4000] if response_json else result.text[:4000],
                tokens=result.tokens, latency_ms=result.latency_ms,
            )
        )
        await self.emit(
            "reasoning", agent=agent, step=step, text=reasoning_text,
            tokens=result.tokens, latency_ms=result.latency_ms,
            attempts=result.attempts, model=result.model,
        )
        return value, result

    # ---------------------------------------------------------------- tools
    async def tool(
        self, agent: str, tool_name: str, detail: str, output: Any
    ) -> None:
        """Record a real tool invocation (deterministic, non-LLM work)."""
        out_str = output if isinstance(output, str) else json.dumps(output, default=str)
        self._persist_step(
            AuditStep(
                incident_id=self.incident.id, agent=agent, step=f"tool:{tool_name}",
                tool_call=detail[:2000], output=out_str[:4000],
            )
        )
        await self.emit(
            "tool_call", agent=agent, tool=tool_name, detail=detail, output=out_str[:1500]
        )

    # ------------------------------------------------------------- findings
    def remember(self, key: str, value: Any) -> None:
        """Promote a fact into the incident's shared working memory."""
        self.incident.findings[key] = value
        self.deps.storage.save_incident(self.incident)

    # ----------------------------------------------------------- transitions
    async def transition(self, new_status: IncidentStatus) -> None:
        current = self.incident.status
        if new_status not in LEGAL_TRANSITIONS.get(current, set()):
            raise ValueError(f"Illegal transition {current} -> {new_status}")
        self.incident.status = new_status
        self.deps.storage.save_incident(self.incident)
        await self.emit("state_change", **{"from": current.value, "to": new_status.value})


class BaseAgent:
    """Base for the six specialized agents. Subclasses set the metadata and
    implement `execute`. The wrapper handles activation/teardown streaming and
    error isolation so one agent's failure surfaces cleanly in the UI."""

    name: str = "Agent"
    version: str = "1.0.0"
    allowed_tools: list[str] = []
    scope: str = ""
    # Short human label shown when the node lights up in the graph.
    headline: str = ""

    async def run(self, ctx: RunContext) -> None:
        await ctx.emit("agent_start", agent=self.name, headline=self.headline,
                       tools=self.allowed_tools)
        try:
            await self.execute(ctx)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the demo
            await ctx.emit("agent_error", agent=self.name, error=str(exc))
            raise
        await ctx.emit("agent_end", agent=self.name)

    async def execute(self, ctx: RunContext) -> None:  # pragma: no cover - abstract
        raise NotImplementedError
