"""Pydantic schemas + the incident state machine.

These mirror the SQLite tables in services/storage.py (which in turn mirror the
future Firestore collections). Keeping them as typed models means every hand-off
between agents is validated, not a loose dict.
"""
from __future__ import annotations

import enum
import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #
class IncidentStatus(str, enum.Enum):
    DETECTED = "DETECTED"
    TRIAGED = "TRIAGED"
    DIAGNOSED = "DIAGNOSED"
    CORRELATED = "CORRELATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATING = "REMEDIATING"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"  # human declined the proposed remediation
    FAILED = "FAILED"      # unrecoverable pipeline error


# Legal forward transitions. The orchestrator refuses any move not in this map,
# so a buggy agent can never skip the approval gate.
LEGAL_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.DETECTED: {IncidentStatus.TRIAGED, IncidentStatus.FAILED},
    IncidentStatus.TRIAGED: {IncidentStatus.DIAGNOSED, IncidentStatus.FAILED},
    IncidentStatus.DIAGNOSED: {IncidentStatus.CORRELATED, IncidentStatus.FAILED},
    IncidentStatus.CORRELATED: {IncidentStatus.AWAITING_APPROVAL, IncidentStatus.FAILED},
    IncidentStatus.AWAITING_APPROVAL: {
        IncidentStatus.REMEDIATING,
        IncidentStatus.REJECTED,
        IncidentStatus.FAILED,
    },
    IncidentStatus.REMEDIATING: {IncidentStatus.RESOLVED, IncidentStatus.FAILED},
    IncidentStatus.RESOLVED: set(),
    IncidentStatus.REJECTED: set(),
    IncidentStatus.FAILED: set(),
}


class Severity(str, enum.Enum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


# --------------------------------------------------------------------------- #
# Inbound alert (what lands on the event bus)
# --------------------------------------------------------------------------- #
class Alert(BaseModel):
    alert: str
    service: str
    error_rate: Optional[str] = None
    grafana_snapshot: Optional[str] = None  # local image path
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Persisted rows
# --------------------------------------------------------------------------- #
class Deploy(BaseModel):
    id: str
    service: str
    version: str
    deployed_at: int  # ms epoch
    deployed_by: str
    commit_sha: str
    rollback_target: Optional[str] = None


class LogLine(BaseModel):
    id: str
    service: str
    ts: int
    level: str
    message: str
    log_class: Optional[str] = None


class IncidentMemory(BaseModel):
    fingerprint_id: str
    fingerprint: str
    embedding: list[float]
    past_incident_ids: list[str]
    typical_cause: str
    typical_fix: str
    avg_resolution_minutes: float


class AgentRegistryEntry(BaseModel):
    id: str
    name: str
    version: str
    model: str
    allowed_tools: list[str]
    scope: str
    status: str = "healthy"


class AuditStep(BaseModel):
    id: str = Field(default_factory=lambda: _uid("audit"))
    incident_id: str
    agent: str
    step: str
    input: str = ""
    reasoning: str = ""
    tool_call: str = ""
    output: str = ""
    tokens: int = 0
    latency_ms: int = 0
    ts: int = Field(default_factory=now_ms)


class RemediationPlan(BaseModel):
    action: str              # e.g. "rollback"
    target: str              # e.g. "checkout-svc -> v2.4.0"
    risk: str                # low | medium | high
    reversible: bool
    rollback_target: Optional[str] = None
    rationale: str = ""
    requires_approval: bool = True


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: _uid("inc"))
    status: IncidentStatus = IncidentStatus.DETECTED
    severity: Optional[Severity] = None
    service: str = ""
    blast_radius: Optional[str] = None
    detected_at: int = Field(default_factory=now_ms)
    probable_cause: Optional[str] = None
    confidence: Optional[float] = None
    remediation_plan: Optional[RemediationPlan] = None
    approved_by: Optional[str] = None
    resolved_at: Optional[int] = None
    rca_doc: Optional[str] = None
    fingerprint: Optional[str] = None

    # Live working memory shared across agents for this run. Not persisted as a
    # column — the durable facts get promoted to their own columns above.
    findings: dict[str, Any] = Field(default_factory=dict)
    alert: Optional[Alert] = None


# --------------------------------------------------------------------------- #
# Real-time stream event (SSE payload to the war-room UI)
# --------------------------------------------------------------------------- #
class StreamEvent(BaseModel):
    type: str                 # incident_created | agent_start | reasoning | tool_call
                              # | agent_end | state_change | approval_required
                              # | approved | rejected | resolved | error | done
    incident_id: str
    agent: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: int = Field(default_factory=now_ms)
