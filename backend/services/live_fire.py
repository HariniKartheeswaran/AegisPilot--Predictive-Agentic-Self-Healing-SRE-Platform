"""Live Fire — real incident injection for Kubernetes or Docker Compose.

When live mode is on (REMEDIATION_MODE=kubernetes|docker and PROMETHEUS_URL set):
  1. Records the current scrape version as the rollback target
  2. Spikes ERROR_RATE + a new SERVICE_VERSION (K8s patch-env or /admin/fault)
  3. Generates /api/work load so Prometheus 5xx series move
  4. Ingests live logs (Loki / K8s / scrape /admin/logs)
  5. Returns an Alert whose error_rate matches the injected fault

This is not the HikariCP seed demo path — seed scenarios only run when live
mode is off.
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any

import httpx

from backend.config import get_settings
from backend.models import Alert, Deploy, LogLine, now_ms
from backend.services.storage import StorageService

log = logging.getLogger("aegisops.live_fire")

_SCRAPE = ("checkout-svc", "cart-svc", "payments-svc")
_NODEPORTS = {
    "checkout-svc": 30081,
    "cart-svc": 30082,
    "payments-svc": 30083,
}


def _mode() -> str:
    return (get_settings().remediation_mode or "").strip().lower()


def live_mode_enabled() -> bool:
    s = get_settings()
    mode = _mode()
    has_prom = bool((s.prometheus_url or "").strip())
    if not has_prom:
        return False
    return mode in ("kubernetes", "k8s", "docker")


def docker_mode() -> bool:
    return _mode() == "docker"


def _scrape_base(service: str) -> str:
    """Base URL for a scrape service (Compose DNS or explicit override)."""
    s = get_settings()
    overrides = {
        "checkout-svc": getattr(s, "scrape_url_checkout", "") or os.environ.get("SCRAPE_URL_CHECKOUT", ""),
        "cart-svc": getattr(s, "scrape_url_cart", "") or os.environ.get("SCRAPE_URL_CART", ""),
        "payments-svc": getattr(s, "scrape_url_payments", "") or os.environ.get("SCRAPE_URL_PAYMENTS", ""),
    }
    explicit = (overrides.get(service) or "").strip().rstrip("/")
    if explicit:
        return explicit
    # Default Compose service DNS (same names as K8s Deployments).
    return f"http://{service}:8080"


def _env_val(containers: list, name: str, default: str = "") -> str:
    if not containers:
        return default
    for e in containers[0].env or []:
        if e.name == name:
            return str(e.value if e.value is not None else default)
    return default


def _load_apps():
    from kubernetes import client, config
    from kubernetes.config.config_exception import ConfigException

    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config()
    return client.AppsV1Api(), client.CoreV1Api()


def _patch_env(apps, ns: str, deploy: str, updates: dict[str, str]) -> None:
    from backend.tools.remediation import _patch_container_env

    _patch_container_env(apps, ns, deploy, updates)


def _wait_ready(apps, ns: str, deploy: str, timeout_s: float = 90.0) -> None:
    from backend.tools.remediation import _wait_rollout

    _wait_rollout(apps, ns, deploy, timeout_s=timeout_s)


def _generate_load(service: str, bursts: int = 80) -> dict[str, int]:
    """Hit scrape /api/work so Prom sees 5xx under ERROR_RATE."""
    urls: list[str] = []
    if docker_mode():
        urls.append(f"{_scrape_base(service)}/api/work")
    else:
        ns = get_settings().k8s_namespace
        urls.extend(
            [
                f"http://{service}.{ns}.svc.cluster.local:8080/api/work",
                f"http://{service}:8080/api/work",
            ]
        )
        port = _NODEPORTS.get(service)
        if port:
            urls.append(f"http://13.207.225.219:{port}/api/work")

    ok = err = 0
    with httpx.Client(timeout=3.0) as client:
        for _ in range(bursts):
            hit = False
            for url in urls:
                try:
                    r = client.get(url)
                    if r.status_code >= 500:
                        err += 1
                    else:
                        ok += 1
                    hit = True
                    break
                except Exception:
                    continue
            if not hit:
                break
    return {"ok": ok, "err": err, "total": ok + err}


def _set_docker_fault(
    service: str,
    *,
    error_rate: float,
    service_version: str,
    latency_ms: int = 40,
    fail_ready: bool = False,
) -> dict[str, Any]:
    url = f"{_scrape_base(service)}/admin/fault"
    body = {
        "error_rate": error_rate,
        "service_version": service_version,
        "latency_ms": latency_ms,
        "fail_ready": fail_ready,
    }
    with httpx.Client(timeout=8.0) as client:
        r = client.post(url, json=body)
        r.raise_for_status()
        return r.json()


def _get_docker_fault(service: str) -> dict[str, Any]:
    url = f"{_scrape_base(service)}/admin/fault"
    with httpx.Client(timeout=5.0) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()


def fetch_live_log_lines(service: str, limit: int = 80) -> list[LogLine]:
    """Prefer Loki, then scrape /admin/logs (Docker), then Kubernetes pod logs."""
    lines = _logs_from_loki(service, limit=limit)
    if lines:
        return lines
    if docker_mode():
        lines = _logs_from_scrape_admin(service, limit=limit)
        if lines:
            return lines
    return _logs_from_k8s(service, limit=limit)


def _logs_from_scrape_admin(service: str, limit: int = 80) -> list[LogLine]:
    url = f"{_scrape_base(service)}/admin/logs"
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(url, params={"limit": str(limit)})
            r.raise_for_status()
            rows = r.json()
    except Exception as exc:
        log.warning("scrape admin logs failed: %s", exc)
        return []

    out: list[LogLine] = []
    for i, row in enumerate(rows or []):
        ts_ms = int(row.get("ts") or now_ms())
        msg = str(row.get("message") or "")
        level = str(row.get("level") or "INFO")
        out.append(
            LogLine(
                id=f"log_docker_{service}_{ts_ms}_{i}",
                service=service,
                ts=ts_ms,
                level=level,
                message=msg.strip()[:500],
            )
        )
    return out[-limit:]


def _logs_from_loki(service: str, limit: int = 80) -> list[LogLine]:
    s = get_settings()
    base = (
        getattr(s, "loki_url", None)
        or __import__("os").environ.get("LOKI_URL")
        or ""
    ).strip()
    if not base and (s.grafana_url or "").strip():
        # Same observability host, Loki on 3100
        host = s.grafana_url.rstrip("/").rsplit(":", 1)[0]
        base = f"{host}:3100"
    if not base:
        return []
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - 15 * 60 * 10**9
    query = '{app="%s"}' % service
    url = f"{base.rstrip('/')}/loki/api/v1/query_range"
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(
                url,
                params={
                    "query": query,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": str(limit),
                    "direction": "backward",
                },
            )
            r.raise_for_status()
            body = r.json()
    except Exception as exc:
        log.warning("loki log fetch failed: %s", exc)
        return []

    out: list[LogLine] = []
    for stream in (body.get("data") or {}).get("result") or []:
        for ts_ns, msg in stream.get("values") or []:
            try:
                ts_ms = int(int(ts_ns) / 1_000_000)
            except (TypeError, ValueError):
                ts_ms = now_ms()
            level = "ERROR" if re.search(r"\berror\b|request_failed|\b5\d\d\b", msg, re.I) else (
                "WARN" if re.search(r"\bwarn", msg, re.I) else "INFO"
            )
            out.append(
                LogLine(
                    id=f"log_live_{service}_{ts_ms}_{len(out)}",
                    service=service,
                    ts=ts_ms,
                    level=level,
                    message=msg.strip()[:500],
                )
            )
    out.sort(key=lambda x: x.ts)
    return out[-limit:]


def _logs_from_k8s(service: str, limit: int = 80) -> list[LogLine]:
    try:
        _, core = _load_apps()
        ns = get_settings().k8s_namespace
        pods = core.list_namespaced_pod(ns, label_selector=f"app={service}")
        if not pods.items:
            return []
        pod = pods.items[0].metadata.name
        raw = core.read_namespaced_pod_log(pod, ns, tail_lines=limit, timestamps=True)
    except Exception as exc:
        log.warning("k8s log fetch failed: %s", exc)
        return []

    out: list[LogLine] = []
    for i, line in enumerate((raw or "").splitlines()):
        if not line.strip():
            continue
        # "2026-09-24T10:00:00.000000000Z message..."
        msg = line
        ts_ms = now_ms() - (limit - i) * 1000
        m = re.match(r"^(\S+)\s+(.*)$", line)
        if m:
            try:
                dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                ts_ms = int(dt.timestamp() * 1000)
                msg = m.group(2)
            except Exception:
                pass
        level = "ERROR" if re.search(r"request_failed|\berror\b|\b5\d\d\b", msg, re.I) else "INFO"
        out.append(
            LogLine(
                id=f"log_k8s_{service}_{ts_ms}_{i}",
                service=service,
                ts=ts_ms,
                level=level,
                message=msg.strip()[:500],
            )
        )
    return out[-limit:]


def ingest_live_logs(storage: StorageService, service: str) -> int:
    lines = fetch_live_log_lines(service)
    for lg in lines:
        storage.add_log(lg)
    return len(lines)


def _prepare_k8s_fire(
    storage: StorageService,
    service: str,
    error_rate: float,
) -> dict[str, Any]:
    s = get_settings()
    ns = s.k8s_namespace
    apps, _ = _load_apps()
    dep = apps.read_namespaced_deployment(service, ns)
    containers = dep.spec.template.spec.containers or []
    good_ver = _env_val(containers, "SERVICE_VERSION", "v1.0.0")
    bad_ver = f"v-live-{int(time.time()) % 100000}"

    _patch_env(
        apps,
        ns,
        service,
        {
            "ERROR_RATE": str(error_rate),
            "SERVICE_VERSION": bad_ver,
            "FAIL_READY": "false",
            "LATENCY_MS": "40",
        },
    )
    _wait_ready(apps, ns, service, timeout_s=60.0)

    time.sleep(2.0)
    load = _generate_load(service, bursts=60)
    time.sleep(1.0)
    load2 = _generate_load(service, bursts=30)
    load = {
        "ok": load["ok"] + load2["ok"],
        "err": load["err"] + load2["err"],
        "total": load["total"] + load2["total"],
    }

    n_logs = ingest_live_logs(storage, service)
    return {"good_ver": good_ver, "bad_ver": bad_ver, "load": load, "n_logs": n_logs}


def _prepare_docker_fire(
    storage: StorageService,
    service: str,
    error_rate: float,
) -> dict[str, Any]:
    current = _get_docker_fault(service)
    good_ver = str(current.get("service_version") or "v1.0.0")
    # Keep a stable rollback target across repeated fires in one session.
    if good_ver.startswith("v-live-"):
        good_ver = "v1.0.0"
    bad_ver = f"v-live-{int(time.time()) % 100000}"

    _set_docker_fault(
        service,
        error_rate=error_rate,
        service_version=bad_ver,
        latency_ms=40,
        fail_ready=False,
    )

    time.sleep(0.5)
    load = _generate_load(service, bursts=60)
    time.sleep(0.5)
    load2 = _generate_load(service, bursts=30)
    load = {
        "ok": load["ok"] + load2["ok"],
        "err": load["err"] + load2["err"],
        "total": load["total"] + load2["total"],
    }

    n_logs = ingest_live_logs(storage, service)
    return {"good_ver": good_ver, "bad_ver": bad_ver, "load": load, "n_logs": n_logs}


def prepare_live_fire(
    storage: StorageService,
    service: str = "checkout-svc",
    error_rate: float = 0.42,
) -> dict[str, Any]:
    """Mutate the live scrape service and return alert payload fields."""
    if service not in _SCRAPE:
        service = "checkout-svc"

    if docker_mode():
        result = _prepare_docker_fire(storage, service, error_rate)
        mode_tag = "docker"
    else:
        result = _prepare_k8s_fire(storage, service, error_rate)
        mode_tag = "kubernetes"

    good_ver = result["good_ver"]
    bad_ver = result["bad_ver"]
    load = result["load"]
    n_logs = result["n_logs"]

    t = now_ms()
    storage.add_deploy(
        Deploy(
            id=f"dep_live_{service}_{t}",
            service=service,
            version=bad_ver,
            deployed_at=t - 2 * 60_000,
            deployed_by=f"aegispilot-live-fire-{mode_tag}",
            commit_sha=uuid.uuid4().hex[:7],
            rollback_target=good_ver,
        )
    )

    measured = 0.0
    if load["total"]:
        measured = 100.0 * load["err"] / load["total"]
    rate_str = f"{measured:.0f}%" if load["total"] else f"{int(error_rate * 100)}%"

    alert = Alert(
        alert="HighErrorRate",
        service=service,
        error_rate=rate_str,
        metadata={
            "live_fire": True,
            "live_mode": mode_tag,
            "snapshot_source_hint": "prometheus",
            "injected_error_rate": error_rate,
            "load": load,
            "logs_ingested": n_logs,
            "bad_version": bad_ver,
            "rollback_target": good_ver,
        },
    )
    log.info(
        "live fire ready mode=%s service=%s bad=%s good=%s load=%s logs=%d",
        mode_tag, service, bad_ver, good_ver, load, n_logs,
    )
    return {"alert": alert, "scenario": "live", "service": service, "load": load}


def build_live_alert(storage: StorageService) -> Alert:
    return prepare_live_fire(storage)["alert"]
