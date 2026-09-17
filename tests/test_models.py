"""Unit tests for models and state machine validation."""
import pytest
from backend.models import (
    Alert,
    Deploy,
    Incident,
    IncidentStatus,
    LEGAL_TRANSITIONS,
    LogLine,
    RemediationPlan,
    Severity,
    now_ms,
)


def test_state_machine_legal_forward_transitions():
    """Verify standard happy-path progression through the legal state machine."""
    current = IncidentStatus.DETECTED
    assert IncidentStatus.TRIAGED in LEGAL_TRANSITIONS[current]

    current = IncidentStatus.TRIAGED
    assert IncidentStatus.DIAGNOSED in LEGAL_TRANSITIONS[current]

    current = IncidentStatus.DIAGNOSED
    assert IncidentStatus.CORRELATED in LEGAL_TRANSITIONS[current]

    current = IncidentStatus.CORRELATED
    assert IncidentStatus.AWAITING_APPROVAL in LEGAL_TRANSITIONS[current]

    current = IncidentStatus.AWAITING_APPROVAL
    assert IncidentStatus.REMEDIATING in LEGAL_TRANSITIONS[current]
    assert IncidentStatus.REJECTED in LEGAL_TRANSITIONS[current]

    current = IncidentStatus.REMEDIATING
    assert IncidentStatus.RESOLVED in LEGAL_TRANSITIONS[current]


def test_state_machine_illegal_transition_rejections():
    """Verify that jumping states or skipping approval is strictly illegal."""
    # DETECTED cannot jump directly to REMEDIATING (skipping triage, diagnosis, approval)
    assert IncidentStatus.REMEDIATING not in LEGAL_TRANSITIONS[IncidentStatus.DETECTED]
    assert IncidentStatus.RESOLVED not in LEGAL_TRANSITIONS[IncidentStatus.DETECTED]

    # TRIAGED cannot jump directly to RESOLVED
    assert IncidentStatus.RESOLVED not in LEGAL_TRANSITIONS[IncidentStatus.TRIAGED]

    # Terminal states have no forward transitions
    assert len(LEGAL_TRANSITIONS[IncidentStatus.RESOLVED]) == 0
    assert len(LEGAL_TRANSITIONS[IncidentStatus.REJECTED]) == 0


def test_models_instantiation_and_serialization():
    """Test model creation and defaults."""
    alert = Alert(alert="HighLatency", service="checkout-svc", error_rate="15%")
    assert alert.service == "checkout-svc"
    assert alert.error_rate == "15%"

    deploy = Deploy(
        id="dep_test_1",
        service="checkout-svc",
        version="v1.0.1",
        deployed_at=now_ms(),
        deployed_by="tester",
        commit_sha="abcdef1",
        rollback_target="v1.0.0",
    )
    assert deploy.rollback_target == "v1.0.0"

    plan = RemediationPlan(
        action="rollback",
        target="checkout-svc -> v1.0.0",
        risk="low",
        reversible=True,
        requires_approval=True,
    )
    assert plan.action == "rollback"
    assert plan.requires_approval is True

    inc = Incident(service="checkout-svc", alert=alert)
    assert inc.status == IncidentStatus.DETECTED
    assert inc.id.startswith("inc_")
