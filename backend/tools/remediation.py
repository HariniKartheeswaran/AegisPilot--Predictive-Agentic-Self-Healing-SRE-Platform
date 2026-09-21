"""Remediation tools — plan construction + mode-aware executor.

Modes (env ``REMEDIATION_MODE``):
  - ``simulate`` (default for local/tests): ordered runbook steps, every step
    tagged ``simulated=True``. Never claims to have touched prod.
  - ``kubernetes``: real in-cluster (or kubeconfig) mutations against allowlisted
    Deployments in ``K8S_NAMESPACE``. Steps tagged ``simulated=False``.

Honesty rule: the mode is explicit. Kubernetes mode fails loudly if the API
call fails — it does not silently fall back to simulation.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.models import RemediationPlan

log = logging.getLogger("aegisops.remediation")

# The reversible action catalog with intrinsic risk. `rollback` is the standard
# fix for a bad-deploy regression and is fully reversible.
ACTION_CATALOG: dict[str, dict] = {
    "rollback": {"risk": "low", "reversible": True, "destructive": True,
                 "desc": "Redeploy the previous known-good version"},
    "scale_out": {"risk": "low", "reversible": True, "destructive": False,
                  "desc": "Add replicas to absorb load"},
    "restart": {"risk": "medium", "reversible": True, "destructive": True,
                "desc": "Rolling restart of the service pods"},
    "flag_off": {"risk": "low", "reversible": True, "destructive": False,
                 "desc": "Disable the offending feature flag"},
}

# Deployments the war-room SA is allowed to remediate (scrape tier + war room).
_DEFAULT_ALLOWLIST = (
    "checkout-svc,cart-svc,payments-svc,"
    "aegis-warroom-blue,aegis-warroom-green"
)


def _mode() -> str:
    return (os.getenv("REMEDIATION_MODE") or "simulate").strip().lower()


def _namespace() -> str:
    return (os.getenv("K8S_NAMESPACE") or "aegispilot").strip()


def _allowlist() -> set[str]:
    raw = os.getenv("K8S_REMEDIATE_DEPLOYMENTS") or _DEFAULT_ALLOWLIST
    return {x.strip() for x in raw.split(",") if x.strip()}


def _deployment_for(service: str) -> str:
    """Map incident service name → Deployment name.

    Scrape services use the same name (checkout-svc). War-room incidents map to
    the active slot Deployment when K8S_ACTIVE_SLOT is set.
    """
    name = service.strip()
    if name in ("aegis-warroom", "warroom", "aegis-warroom-svc"):
        slot = (os.getenv("K8S_ACTIVE_SLOT") or "").strip()
        if slot in ("blue", "green"):
            return f"aegis-warroom-{slot}"
        # Prefer green if both exist; caller still must be on allowlist.
        return os.getenv("K8S_GREEN_DEPLOYMENT", "aegis-warroom-green")
    return name


def build_plan(
    action: str, service: str, rollback_target: Optional[str], rationale: str
) -> RemediationPlan:
    meta = ACTION_CATALOG.get(action, ACTION_CATALOG["rollback"])
    if action == "rollback" and rollback_target:
        target = f"{service} -> {rollback_target}"
    else:
        target = service
    return RemediationPlan(
        action=action, target=target, risk=meta["risk"],
        reversible=meta["reversible"], rollback_target=rollback_target,
        rationale=rationale,
        # Destructive actions must pass the human gate.
        requires_approval=meta["destructive"],
    )


@dataclass
class ExecStep:
    label: str
    ok: bool
    simulated: bool = True
    detail: str = ""


@dataclass
class ExecResult:
    ok: bool
    action: str
    target: str
    steps: list[ExecStep] = field(default_factory=list)
    simulated: bool = True


def _load_apps_v1():
    """Load AppsV1Api using in-cluster config, else local kubeconfig."""
    from kubernetes import client, config
    from kubernetes.config.config_exception import ConfigException

    try:
        config.load_incluster_config()
        log.info("kubernetes client: in-cluster config")
    except ConfigException:
        config.load_kube_config()
        log.info("kubernetes client: kubeconfig")
    return client.AppsV1Api()


def _patch_container_env(apps, namespace: str, deploy_name: str, updates: dict[str, str]) -> str:
    """Merge env vars onto the first container of a Deployment (strategic merge)."""
    dep = apps.read_namespaced_deployment(deploy_name, namespace)
    containers = dep.spec.template.spec.containers or []
    if not containers:
        raise RuntimeError(f"deployment/{deploy_name} has no containers")
    c0 = containers[0]
    existing = {e.name: e.value for e in (c0.env or []) if e.name}
    existing.update(updates)
    env_list = [{"name": k, "value": str(v)} for k, v in existing.items()]
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "aegispilot.io/remediated-at": datetime.now(timezone.utc).isoformat(),
                    }
                },
                "spec": {
                    "containers": [
                        {"name": c0.name, "env": env_list}
                    ]
                },
            }
        }
    }
    apps.patch_namespaced_deployment(deploy_name, namespace, body)
    return f"patched env {sorted(updates.keys())} on deployment/{deploy_name}"


def _scale(apps, namespace: str, deploy_name: str, replicas: int) -> str:
    body = {"spec": {"replicas": replicas}}
    apps.patch_namespaced_deployment(deploy_name, namespace, body)
    return f"scaled deployment/{deploy_name} to replicas={replicas}"


def _rollout_restart(apps, namespace: str, deploy_name: str) -> str:
    stamp = datetime.now(timezone.utc).isoformat()
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": stamp,
                    }
                }
            }
        }
    }
    apps.patch_namespaced_deployment(deploy_name, namespace, body)
    return f"rollout restart deployment/{deploy_name} at {stamp}"


def _wait_rollout(apps, namespace: str, deploy_name: str, timeout_s: float = 90.0) -> str:
    """Poll until updated replicas are available (best-effort readiness)."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        dep = apps.read_namespaced_deployment(deploy_name, namespace)
        status = dep.status
        desired = dep.spec.replicas or 0
        available = status.available_replicas or 0
        updated = status.updated_replicas or 0
        if desired == 0 or (available >= desired and updated >= desired):
            return f"rollout ready deployment/{deploy_name} available={available}/{desired}"
        time.sleep(2.0)
    raise TimeoutError(f"rollout timed out for deployment/{deploy_name}")


async def _execute_kubernetes(plan: RemediationPlan, service: str) -> ExecResult:
    steps: list[ExecStep] = []
    deploy = _deployment_for(service)
    ns = _namespace()
    allowed = _allowlist()

    def add(label: str, ok: bool, detail: str = "") -> None:
        steps.append(ExecStep(label=label, ok=ok, simulated=False, detail=detail))

    if deploy not in allowed:
        add("Authorize target", False,
            f"deployment/{deploy} not in K8S_REMEDIATE_DEPLOYMENTS allowlist")
        return ExecResult(ok=False, action=plan.action, target=deploy, steps=steps, simulated=False)

    try:
        apps = await asyncio.to_thread(_load_apps_v1)
    except Exception as exc:
        add("Connect to Kubernetes API", False, str(exc))
        return ExecResult(ok=False, action=plan.action, target=deploy, steps=steps, simulated=False)

    add("Authorize target", True, f"namespace={ns} deployment/{deploy}")

    try:
        if plan.action == "rollback":
            target_ver = plan.rollback_target or "v1.0.0"
            add("Select rollback target", True, f"known-good {target_ver}")
            detail = await asyncio.to_thread(
                _patch_container_env, apps, ns, deploy,
                {
                    "ERROR_RATE": "0.0",
                    "FAIL_READY": "false",
                    "SERVICE_VERSION": target_ver,
                    "LATENCY_MS": "25",
                },
            )
            add("Apply known-good config", True, detail)
            ready = await asyncio.to_thread(_wait_rollout, apps, ns, deploy)
            add("Verify rollout", True, ready)

        elif plan.action == "scale_out":
            dep = await asyncio.to_thread(apps.read_namespaced_deployment, deploy, ns)
            current = dep.spec.replicas or 1
            desired = current + 1
            detail = await asyncio.to_thread(_scale, apps, ns, deploy, desired)
            add("Scale out", True, detail)
            ready = await asyncio.to_thread(_wait_rollout, apps, ns, deploy)
            add("Verify rollout", True, ready)

        elif plan.action == "restart":
            # Clear injected failure then restart so metrics recover.
            await asyncio.to_thread(
                _patch_container_env, apps, ns, deploy,
                {"ERROR_RATE": "0.0", "FAIL_READY": "false"},
            )
            detail = await asyncio.to_thread(_rollout_restart, apps, ns, deploy)
            add("Rolling restart", True, detail)
            ready = await asyncio.to_thread(_wait_rollout, apps, ns, deploy)
            add("Verify rollout", True, ready)

        elif plan.action == "flag_off":
            detail = await asyncio.to_thread(
                _patch_container_env, apps, ns, deploy,
                {"FAIL_READY": "false", "ERROR_RATE": "0.0"},
            )
            add("Disable failure flags", True, detail)
            ready = await asyncio.to_thread(_wait_rollout, apps, ns, deploy)
            add("Verify rollout", True, ready)

        else:
            add(f"Execute {plan.action}", False, f"unsupported action {plan.action}")
            return ExecResult(ok=False, action=plan.action, target=deploy, steps=steps, simulated=False)

    except Exception as exc:
        log.exception("kubernetes remediation failed")
        add("Kubernetes API error", False, str(exc))
        return ExecResult(ok=False, action=plan.action, target=deploy, steps=steps, simulated=False)

    return ExecResult(
        ok=all(s.ok for s in steps), action=plan.action,
        target=deploy, steps=steps, simulated=False,
    )


async def _execute_simulate(plan: RemediationPlan, service: str) -> ExecResult:
    steps: list[ExecStep] = []

    async def do(label: str, detail: str = "") -> None:
        await asyncio.sleep(0.35)
        steps.append(ExecStep(label=label, ok=True, detail=detail))

    if plan.action == "rollback":
        await do("Freeze deploys", f"Locked deploy pipeline for {service}")
        await do("Select rollback target", f"Known-good {plan.rollback_target}")
        await do("Drain traffic from bad pods", "Cordoned pods running v2.4.1")
        await do("Deploy known-good version", f"Rolling out {plan.rollback_target}")
        await do("Verify health", "5xx back under SLO; p99 recovering")
        await do("Unfreeze deploys", "Pipeline unlocked")
    else:
        await do(f"Execute {plan.action}", plan.target)
        await do("Verify health", "Signals recovering within SLO")

    return ExecResult(
        ok=all(s.ok for s in steps), action=plan.action,
        target=plan.target, steps=steps, simulated=True,
    )


async def execute_remediation(plan: RemediationPlan, service: str) -> ExecResult:
    """Run the plan under ``REMEDIATION_MODE`` (simulate | kubernetes)."""
    mode = _mode()
    if mode in ("kubernetes", "k8s"):
        return await _execute_kubernetes(plan, service)
    if mode not in ("simulate", "simulation", "sim", ""):
        log.warning("unknown REMEDIATION_MODE=%r; falling back to simulate", mode)
    return await _execute_simulate(plan, service)
