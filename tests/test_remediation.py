"""Unit tests for Remediation tools covering plans and executor."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import backend.tools.remediation as rem
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

def _make_plan(action, rollback_target=None):
    return build_plan(
        action=action,
        service="checkout-svc",
        rollback_target=rollback_target,
        rationale="Unit test remediation execution",
    )


def _mock_kubernetes_executor(monkeypatch):
    """Mock Kubernetes dependencies used by _execute_kubernetes."""
    apps = Mock()

    monkeypatch.setattr(rem, "_load_apps_v1", lambda: apps)
    monkeypatch.setattr(
        rem,
        "_wait_rollout",
        lambda apps, namespace, deployment: (
            f"rollout ready deployment/{deployment}"
        ),
    )

    return apps


def test_kubernetes_rollback_success(monkeypatch):
    """Rollback should patch configuration and verify the rollout."""
    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("K8S_REMEDIATE_DEPLOYMENTS", "checkout-svc")

    apps = _mock_kubernetes_executor(monkeypatch)

    monkeypatch.setattr(
        rem,
        "_patch_container_env",
        lambda apps, namespace, deployment, updates: (
            f"patched {deployment}: {sorted(updates)}"
        ),
    )

    async def _run():
        result = await execute_remediation(
            _make_plan("rollback", "v1.0.0"),
            "checkout-svc",
        )

        assert result.ok is True
        assert result.simulated is False
        assert result.action == "rollback"
        assert len(result.steps) == 4
        assert result.steps[0].label == "Authorize target"
        assert result.steps[1].label == "Select rollback target"
        assert result.steps[2].label == "Apply known-good config"
        assert result.steps[3].label == "Verify rollout"

    asyncio.run(_run())


def test_kubernetes_scale_out_success(monkeypatch):
    """Scale-out should increase the current replica count by one."""
    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("K8S_REMEDIATE_DEPLOYMENTS", "checkout-svc")

    apps = _mock_kubernetes_executor(monkeypatch)

    apps.read_namespaced_deployment.return_value = SimpleNamespace(
        spec=SimpleNamespace(replicas=2)
    )

    monkeypatch.setattr(
        rem,
        "_scale",
        lambda apps, namespace, deployment, replicas: (
            f"scaled {deployment} to {replicas}"
        ),
    )

    async def _run():
        result = await execute_remediation(
            _make_plan("scale_out"),
            "checkout-svc",
        )

        assert result.ok is True
        assert result.simulated is False
        assert result.action == "scale_out"
        assert result.steps[1].label == "Scale out"
        assert "3" in result.steps[1].detail
        assert result.steps[2].label == "Verify rollout"

    asyncio.run(_run())


def test_kubernetes_restart_success(monkeypatch):
    """Restart should clear failure flags and restart the deployment."""
    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("K8S_REMEDIATE_DEPLOYMENTS", "checkout-svc")

    _mock_kubernetes_executor(monkeypatch)

    patch_calls = []

    def fake_patch(apps, namespace, deployment, updates):
        patch_calls.append(updates)
        return "failure flags cleared"

    monkeypatch.setattr(rem, "_patch_container_env", fake_patch)
    monkeypatch.setattr(
        rem,
        "_rollout_restart",
        lambda apps, namespace, deployment: "rolling restart completed",
    )

    async def _run():
        result = await execute_remediation(
            _make_plan("restart"),
            "checkout-svc",
        )

        assert result.ok is True
        assert result.simulated is False
        assert result.action == "restart"
        assert result.steps[1].label == "Rolling restart"
        assert result.steps[2].label == "Verify rollout"

        assert patch_calls == [
            {
                "ERROR_RATE": "0.0",
                "FAIL_READY": "false",
            }
        ]

    asyncio.run(_run())


def test_kubernetes_flag_off_success(monkeypatch):
    """Flag-off should disable failure flags and verify the rollout."""
    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("K8S_REMEDIATE_DEPLOYMENTS", "checkout-svc")

    _mock_kubernetes_executor(monkeypatch)

    patch_calls = []

    def fake_patch(apps, namespace, deployment, updates):
        patch_calls.append(updates)
        return "flags disabled"

    monkeypatch.setattr(rem, "_patch_container_env", fake_patch)

    async def _run():
        result = await execute_remediation(
            _make_plan("flag_off"),
            "checkout-svc",
        )

        assert result.ok is True
        assert result.simulated is False
        assert result.action == "flag_off"
        assert result.steps[1].label == "Disable failure flags"
        assert result.steps[2].label == "Verify rollout"

        assert patch_calls == [
            {
                "FAIL_READY": "false",
                "ERROR_RATE": "0.0",
            }
        ]

    asyncio.run(_run())


def test_kubernetes_connection_failure(monkeypatch):
    """Kubernetes connection failure must return a failed result."""
    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("K8S_REMEDIATE_DEPLOYMENTS", "checkout-svc")

    def fail_load():
        raise RuntimeError("Kubernetes API unavailable")

    monkeypatch.setattr(rem, "_load_apps_v1", fail_load)

    async def _run():
        result = await execute_remediation(
            _make_plan("restart"),
            "checkout-svc",
        )

        assert result.ok is False
        assert result.simulated is False
        assert result.steps[0].label == "Connect to Kubernetes API"
        assert "Kubernetes API unavailable" in result.steps[0].detail

    asyncio.run(_run())


def test_kubernetes_unsupported_action(monkeypatch):
    """Unsupported actions must fail instead of being silently executed."""
    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("K8S_REMEDIATE_DEPLOYMENTS", "checkout-svc")

    _mock_kubernetes_executor(monkeypatch)

    plan = Mock()
    plan.action = "unsupported_action"
    plan.rollback_target = None

    async def _run():
        result = await rem._execute_kubernetes(plan, "checkout-svc")

        assert result.ok is False
        assert result.simulated is False
        assert result.steps[-1].label == "Execute unsupported_action"
        assert "unsupported action" in result.steps[-1].detail

    asyncio.run(_run())

def test_patch_container_env_merges_existing_variables():
    """Verify deployment environment variables are merged safely using a fake API."""
    import backend.tools.remediation as rem
    from types import SimpleNamespace

    class FakeAppsApi:
        def __init__(self):
            self.patched_deployment = None
            self.patch_body = None

        def read_namespaced_deployment(self, name, namespace):
            return SimpleNamespace(
                spec=SimpleNamespace(
                    template=SimpleNamespace(
                        spec=SimpleNamespace(
                            containers=[
                                SimpleNamespace(
                                    name="warroom",
                                    env=[
                                        SimpleNamespace(
                                            name="EXISTING_VAR",
                                            value="old-value",
                                        ),
                                    ],
                                )
                            ]
                        )
                    )
                )
            )

        def patch_namespaced_deployment(self, name, namespace, body):
            self.patched_deployment = (name, namespace)
            self.patch_body = body

    apps = FakeAppsApi()

    result = rem._patch_container_env(
        apps=apps,
        namespace="aegispilot",
        deploy_name="aegis-warroom-green",
        updates={
            "ERROR_RATE": "0.0",
            "FAIL_READY": "false",
        },
    )

    assert result == (
        "patched env ['ERROR_RATE', 'FAIL_READY'] "
        "on deployment/aegis-warroom-green"
    )

    assert apps.patched_deployment == (
        "aegis-warroom-green",
        "aegispilot",
    )

    env_list = apps.patch_body["spec"]["template"]["spec"]["containers"][0]["env"]

    env_dict = {
        item["name"]: item["value"]
        for item in env_list
    }

    assert env_dict["EXISTING_VAR"] == "old-value"
    assert env_dict["ERROR_RATE"] == "0.0"
    assert env_dict["FAIL_READY"] == "false"

def test_scale_patches_requested_replica_count():
    import backend.tools.remediation as rem

    class FakeAppsApi:
        def __init__(self):
            self.calls = []

        def patch_namespaced_deployment(
            self, name, namespace, body
        ):
            self.calls.append((name, namespace, body))

    apps = FakeAppsApi()

    result = rem._scale(
        apps,
        "aegispilot",
        "checkout-svc",
        3,
    )

    assert result == (
        "scaled deployment/checkout-svc to replicas=3"
    )

    assert apps.calls == [
        (
            "checkout-svc",
            "aegispilot",
            {"spec": {"replicas": 3}},
        )
    ]

def test_rollout_restart_patches_restart_annotation():
    class FakeAppsApi:
        def __init__(self):
            self.patch_calls = []

        def patch_namespaced_deployment(
            self,
            name,
            namespace,
            body,
        ):
            self.patch_calls.append(
                {
                    "name": name,
                    "namespace": namespace,
                    "body": body,
                }
            )

    apps = FakeAppsApi()

    result = rem._rollout_restart(
        apps=apps,
        namespace="aegispilot",
        deploy_name="checkout-svc",
    )

    assert result.startswith(
        "rollout restart deployment/checkout-svc at "
    )

    assert len(apps.patch_calls) == 1

    patch = apps.patch_calls[0]

    assert patch["name"] == "checkout-svc"
    assert patch["namespace"] == "aegispilot"

    annotations = patch["body"]["spec"]["template"]["metadata"][
        "annotations"
    ]

    assert "kubectl.kubernetes.io/restartedAt" in annotations

    restart_timestamp = annotations[
        "kubectl.kubernetes.io/restartedAt"
    ]

    assert isinstance(restart_timestamp, str)
    assert restart_timestamp

def test_wait_rollout_returns_when_deployment_is_ready():
    class FakeAppsApi:
        def read_namespaced_deployment(
            self,
            name,
            namespace,
        ):
            return SimpleNamespace(
                spec=SimpleNamespace(replicas=2),
                status=SimpleNamespace(
                    available_replicas=2,
                    updated_replicas=2,
                ),
            )

    apps = FakeAppsApi()

    result = rem._wait_rollout(
        apps=apps,
        namespace="aegispilot",
        deploy_name="checkout-svc",
        timeout_s=5,
    )

    assert result == (
        "rollout ready deployment/checkout-svc "
        "available=2/2"
    )

def test_patch_container_env_raises_when_no_containers():
    class FakeAppsApi:
        def read_namespaced_deployment(
            self,
            name,
            namespace,
        ):
            return SimpleNamespace(
                spec=SimpleNamespace(
                    template=SimpleNamespace(
                        spec=SimpleNamespace(
                            containers=[]
                        )
                    )
                )
            )

    apps = FakeAppsApi()

    with pytest.raises(
        RuntimeError,
        match="no containers",
    ):
        rem._patch_container_env(
            apps=apps,
            namespace="aegispilot",
            deploy_name="checkout-svc",
            updates={"ERROR_RATE": "0.0"},
        )