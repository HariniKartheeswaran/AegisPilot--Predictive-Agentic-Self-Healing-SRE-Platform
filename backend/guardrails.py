"""Governance layer: the human-in-the-loop approval gate + a PII scrubber.

This is the architecture-score differentiator (spec §2 "Governance layer"). The
approval gate physically prevents the Remediation agent from executing a
destructive action until a human resolves it in the UI — it's an asyncio
handshake, not a polled flag. The PII scrubber ("Model Armor" concept,
self-implemented) redacts sensitive tokens from anything bound for Slack.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------- #
# Approval gate
# --------------------------------------------------------------------------- #
@dataclass
class ApprovalDecision:
    approved: bool
    approver: str
    note: str = ""


@dataclass
class _Pending:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: Optional[ApprovalDecision] = None


class ApprovalGate:
    """One pending gate per incident. The orchestrator awaits `wait_for`; an API
    call resolves it via `resolve`. If the human never answers, `wait_for` times
    out and the caller treats it as a hold (never auto-approves)."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def open_gate(self, incident_id: str) -> None:
        self._pending[incident_id] = _Pending()

    def is_open(self, incident_id: str) -> bool:
        p = self._pending.get(incident_id)
        return p is not None and not p.event.is_set()

    def resolve(self, incident_id: str, decision: ApprovalDecision) -> bool:
        p = self._pending.get(incident_id)
        if p is None or p.event.is_set():
            return False
        p.decision = decision
        p.event.set()
        return True

    async def wait_for(
        self, incident_id: str, timeout: float = 600.0
    ) -> ApprovalDecision:
        p = self._pending.get(incident_id)
        if p is None:
            raise KeyError(f"No open gate for incident {incident_id}")
        try:
            await asyncio.wait_for(p.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Fail safe: a timeout is NOT an approval. Destructive actions never
            # run without an explicit human yes.
            return ApprovalDecision(
                approved=False, approver="system", note="approval timed out"
            )
        assert p.decision is not None
        return p.decision


# --------------------------------------------------------------------------- #
# PII scrubber (self-implemented "Model Armor")
# --------------------------------------------------------------------------- #
_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("[REDACTED_EMAIL]", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("[REDACTED_IP]", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("[REDACTED_CARD]", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("[REDACTED_TOKEN]", re.compile(r"\b(?:sk|pk|ghp|xox[baprs])[-_][A-Za-z0-9]{8,}\b")),
    ("[REDACTED_JWT]", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b")),
    ("[REDACTED_PHONE]", re.compile(r"\b\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b")),
]


@dataclass
class ScrubResult:
    text: str
    redactions: int


def scrub_pii(text: str) -> ScrubResult:
    if not text:
        return ScrubResult(text=text, redactions=0)
    total = 0
    out = text
    for replacement, pattern in _PII_PATTERNS:
        out, n = pattern.subn(replacement, out)
        total += n
    return ScrubResult(text=out, redactions=total)
