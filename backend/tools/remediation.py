"""Remediation tools — plan construction + mode-aware executor.

Modes (env ``REMEDIATION_MODE``):
  - ``simulate`` (default for local/tests): ordered runbook steps, every step
    tagged ``simulated=True``. Never claims to have touched prod.
  - ``kubernetes``: real in-cluster (or kubeconfig) mutations against allowlisted
    Deployments in ``K8S_NAMESPACE``. Steps tagged ``simulated=False``.
  - ``docker``: real HTTP ``/admin/fault`` against Compose scrape services
    (same images as K8s). Steps tagged ``simulated=False``.

Honesty rule: the mode is explicit. Kubernetes/docker modes fail loudly if the
API call fails — they do not silently fall back to simulation.
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


def _active_warroom_slot() -> str:
    """Resolve the live traffic slot for aegis-warroom.

    Source of truth is the Service selector (what promote actually switches).
    ``K8S_ACTIVE_SLOT`` is only a fallback when the API is unreachable (local
    tests / bootstrap). This avoids stale ConfigMap values after promote.
    """
    svc_name = (os.getenv("K8S_ACTIVE_SERVICE") or "aegis-warroom").strip()
    ns = _namespace()
    try:
        from kubernetes import client, config
        from kubernetes.config.config_exception import ConfigException

        try:
            config.load_incluster_config()
        except ConfigException:
            config.load_kube_config()
        core = client.CoreV1Api()
        svc = core.read_namespaced_service(svc_name, ns)
        slot = ((svc.spec.selector or {}).get("slot") or "").strip()
        if slot in ("blue", "green"):
            return slot
        log.warning(
            "service/%s selector.slot=%r; falling back to K8S_ACTIVE_SLOT",
            svc_name, slot,
        )
    except Exception as exc:  # noqa: BLE001 — prefer env fallback over hard fail here
        log.warning("could not read active slot from service/%s: %s", svc_name, exc)

    env_slot = (os.getenv("K8S_ACTIVE_SLOT") or "").strip()
    if env_slot in ("blue", "green"):
        return env_slot
    return "green"


def _deployment_for(service: str) -> str:
    """Map incident service name → Deployment name.

    Scrape services use the same name (checkout-svc). War-room incidents map to
    the Deployment that currently receives traffic (Service selector slot).
    """
    name = service.strip()
    if name in ("aegis-warroom", "warroom", "aegis-warroom-svc"):
        slot = _active_warroom_slot()
        if slot == "blue":
            return os.getenv("K8S_BLUE_DEPLOYMENT", "aegis-warroom-blue")
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


def _scrape_base(service: str) -> str:
    env_key = {
        "checkout-svc": "SCRAPE_URL_CHECKOUT",
        "cart-svc": "SCRAPE_URL_CART",
        "payments-svc": "SCRAPE_URL_PAYMENTS",
    }.get(service, "")
    if env_key:
        explicit = (os.getenv(env_key) or "").strip().rstrip("/")
        if explicit:
            return explicit
    return f"http://{service}:8080"


async def _execute_docker(plan: RemediationPlan, service: str) -> ExecResult:
    """Heal a Compose scrape service via /admin/fault (shared scrape image)."""
    import httpx

    steps: list[ExecStep] = []
    target = service if service in _allowlist() else ""
    if not target:
        # Map war-room slot names are k8s-only; docker remediates scrape tier.
        if service in ("checkout-svc", "cart-svc", "payments-svc"):
            target = service
        else:
            steps.append(ExecStep(label="Allowlist check", ok=False,
                                  detail=f"{service} is not a docker scrape target"))
            return ExecResult(ok=False, action=plan.action, target=service,
                              steps=steps, simulated=False)

    base = _scrape_base(target)

    def add(label: str, ok: bool, detail: str = "") -> None:
        steps.append(ExecStep(label=label, ok=ok, detail=detail))

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if plan.action == "rollback":
                ver = plan.rollback_target or "v1.0.0"
                add("Select rollback target", True, f"Known-good {ver}")
                r = await client.post(
                    f"{base}/admin/fault",
                    json={
                        "error_rate": 0.0,
                        "latency_ms": 20,
                        "fail_ready": False,
                        "service_version": ver,
                    },
                )
                r.raise_for_status()
                add("Clear injected fault", True, f"POST {base}/admin/fault")
                add("Restore service version", True, ver)
            elif plan.action == "restart":
                r = await client.post(
                    f"{base}/admin/fault",
                    json={"error_rate": 0.0, "fail_ready": False},
                )
                r.raise_for_status()
                add("Clear fault / soft restart", True, f"POST {base}/admin/fault")
            else:
                # scale_out / flag_off: clear fault as the practical docker heal
                r = await client.post(
                    f"{base}/admin/fault",
                    json={"error_rate": 0.0, "fail_ready": False},
                )
                r.raise_for_status()
                add(f"Execute {plan.action}", True, f"Cleared fault on {target}")

            health = await client.get(f"{base}/health")
            health.raise_for_status()
            add("Verify health", True, f"GET {base}/health → {health.status_code}")
    except Exception as exc:
        add("Docker scrape API error", False, str(exc))
        return ExecResult(ok=False, action=plan.action, target=target,
                          steps=steps, simulated=False)

    return ExecResult(
        ok=all(s.ok for s in steps), action=plan.action,
        target=target, steps=steps, simulated=False,
    )


async def execute_remediation(plan: RemediationPlan, service: str) -> ExecResult:
    """Run the plan under ``REMEDIATION_MODE`` (simulate | kubernetes | docker)."""
    mode = _mode()
    if mode in ("kubernetes", "k8s"):
        return await _execute_kubernetes(plan, service)
    if mode == "docker":
        return await _execute_docker(plan, service)
    if mode not in ("simulate", "simulation", "sim", ""):
        log.warning("unknown REMEDIATION_MODE=%r; falling back to simulate", mode)
    return await _execute_simulate(plan, service)
