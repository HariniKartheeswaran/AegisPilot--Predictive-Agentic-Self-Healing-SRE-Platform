"""Unit tests for Remediation tools covering plans and executor."""
import asyncio
import os

import pytest

from backend.tools.remediation import (
    ACTION_CATALOG,
    _deployment_for,
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


def test_warroom_deployment_follows_active_slot(monkeypatch):
    """War-room incidents map to the active blue/green Deployment."""
    import backend.tools.remediation as rem

    monkeypatch.setattr(rem, "_active_warroom_slot", lambda: "blue")
    assert _deployment_for("aegis-warroom") == "aegis-warroom-blue"
    monkeypatch.setattr(rem, "_active_warroom_slot", lambda: "green")
    assert _deployment_for("aegis-warroom") == "aegis-warroom-green"
    assert _deployment_for("checkout-svc") == "checkout-svc"


def test_docker_mode_rollback_clears_fault(monkeypatch):
    """Docker remediation POSTs /admin/fault and verifies health."""
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("SCRAPE_URL_CHECKOUT", "http://checkout:8080")

    fault_resp = MagicMock(status_code=200)
    fault_resp.raise_for_status = MagicMock()
    health_resp = MagicMock(status_code=200)
    health_resp.raise_for_status = MagicMock()

    client = AsyncMock()
    client.post = AsyncMock(return_value=fault_resp)
    client.get = AsyncMock(return_value=health_resp)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None

    async def _run():
        plan = build_plan(
            action="rollback",
            service="checkout-svc",
            rollback_target="v1.0.0",
            rationale="heal docker scrape",
        )
        with patch("httpx.AsyncClient", return_value=client):
            result = await execute_remediation(plan, "checkout-svc")
        assert result.ok is True
        assert result.simulated is False
        assert client.post.await_count >= 1
        body = client.post.await_args.kwargs["json"]
        assert body["error_rate"] == 0.0
        assert body["service_version"] == "v1.0.0"

    asyncio.run(_run())


def test_docker_mode_rejects_non_scrape_target(monkeypatch):
    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("K8S_REMEDIATE_DEPLOYMENTS", "checkout-svc")

    async def _run():
        plan = build_plan(
            action="restart",
            service="random-svc",
            rollback_target=None,
            rationale="should fail",
        )
        result = await execute_remediation(plan, "random-svc")
        assert result.ok is False
        assert result.simulated is False

    asyncio.run(_run())
