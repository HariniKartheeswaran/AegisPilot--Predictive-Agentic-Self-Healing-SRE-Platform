"""Unit tests for Remediation tools covering plans and executor."""
import asyncio
import os

import pytest

from backend.tools.remediation import (
    ACTION_CATALOG,
    build_plan,
    execute_remediation,
)


def test_action_catalog_properties():
    """Verify standard actions define proper risk, reversibility, and destructiveness."""
    assert ACTION_CATALOG["rollback"]["reversible"] is True
    assert ACTION_CATALOG["rollback"]["destructive"] is True

    assert ACTION_CATALOG["scale_out"]["reversible"] is True
    assert ACTION_CATALOG["scale_out"]["destructive"] is False

    assert ACTION_CATALOG["restart"]["destructive"] is True


def test_build_plan_rollback_with_target():
    """Verify rollback plan sets correct target and enforces human approval."""
    plan = build_plan(
        action="rollback",
        service="checkout-svc",
        rollback_target="v2.4.0",
        rationale="Reverting bad deploy v2.4.1",
    )
    assert plan.action == "rollback"
    assert plan.target == "checkout-svc -> v2.4.0"
    assert plan.rollback_target == "v2.4.0"
    assert plan.requires_approval is True
    assert plan.reversible is True


def test_build_plan_non_destructive():
    """Verify non-destructive plans like scale_out do not enforce destructive gate."""
    plan = build_plan(
        action="scale_out",
        service="payments-svc",
        rollback_target=None,
        rationale="Adding replicas to absorb traffic surge",
    )
    assert plan.action == "scale_out"
    assert plan.target == "payments-svc"
    assert plan.requires_approval is False


def test_execute_remediation_simulation(monkeypatch):
    """Verify executor runs ordered simulation steps and tags output correctly."""
    monkeypatch.setenv("REMEDIATION_MODE", "simulate")

    async def _run():
        plan = build_plan(
            action="rollback",
            service="checkout-svc",
            rollback_target="v1.0.0",
            rationale="Rollback bad version",
        )
        result = await execute_remediation(plan, "checkout-svc")
        assert result.ok is True
        assert result.simulated is True
        assert result.action == "rollback"
        assert len(result.steps) == 6
        labels = [s.label for s in result.steps]
        assert "Freeze deploys" in labels[0]
        assert "Select rollback target" in labels[1]
        assert "Verify health" in labels[4]

    asyncio.run(_run())


def test_kubernetes_mode_rejects_unknown_target(monkeypatch):
    """Kubernetes mode must refuse deployments outside the allowlist."""
    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("K8S_REMEDIATE_DEPLOYMENTS", "checkout-svc")

    async def _run():
        plan = build_plan(
            action="restart",
            service="not-a-real-svc",
            rollback_target=None,
            rationale="should fail authz",
        )
        result = await execute_remediation(plan, "not-a-real-svc")
        assert result.ok is False
        assert result.simulated is False
        assert any("allowlist" in (s.detail or "") for s in result.steps)

    asyncio.run(_run())
