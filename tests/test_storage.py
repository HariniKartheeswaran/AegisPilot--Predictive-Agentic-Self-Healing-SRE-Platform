"""Unit tests for SQLiteStorage service operations."""
import pytest
from backend.models import (
    Alert,
    AuditStep,
    Deploy,
    Incident,
    IncidentStatus,
    LogLine,
    RemediationPlan,
    Severity,
    now_ms,
)


def test_storage_incidents_crud(isolated_storage):
    """Verify Incident creation, retrieval, updates, and listings."""
    storage = isolated_storage
    alert = Alert(alert="HighErrorRate", service="checkout-svc", error_rate="12%")
    inc = Incident(
        id="inc_crud_1",
        service="checkout-svc",
        status=IncidentStatus.DETECTED,
        severity=Severity.SEV2,
        alert=alert,
    )
    storage.save_incident(inc)

    retrieved = storage.get_incident("inc_crud_1")
    assert retrieved is not None
    assert retrieved.id == "inc_crud_1"
    assert retrieved.status == IncidentStatus.DETECTED
    assert retrieved.severity == Severity.SEV2
    assert retrieved.alert.service == "checkout-svc"

    # Update incident state
    retrieved.status = IncidentStatus.TRIAGED
    retrieved.remediation_plan = RemediationPlan(
        action="rollback", target="checkout-svc", risk="low", reversible=True
    )
    storage.save_incident(retrieved)

    updated = storage.get_incident("inc_crud_1")
    assert updated.status == IncidentStatus.TRIAGED
    assert updated.remediation_plan.action == "rollback"


def test_storage_deploys_and_logs(isolated_storage):
    """Verify Deploy and LogLine persistence and querying."""
    storage = isolated_storage
    ts = now_ms()

    deploy = Deploy(
        id="dep_s1",
        service="order-svc",
        version="v1.2.0",
        deployed_at=ts,
        deployed_by="ci-test",
        commit_sha="c123456",
        rollback_target="v1.1.0",
    )
    storage.add_deploy(deploy)

    deploys = storage.deploys_for_service("order-svc")
    assert len(deploys) == 1
    assert deploys[0].version == "v1.2.0"

    log1 = LogLine(id="l1", service="order-svc", ts=ts, level="ERROR", message="Database connection refused")
    storage.add_log(log1)

    logs = storage.logs_for_service("order-svc")
    assert len(logs) == 1
    assert logs[0].level == "ERROR"
    assert "Database connection refused" in logs[0].message
