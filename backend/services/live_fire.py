"""Live Fire — real K8s/Prom/Loki incident, not seed HikariCP fixtures.

When REMEDIATION_MODE=kubernetes and PROMETHEUS_URL are set, Fire:
  1. Records the current scrape version as the rollback target
  2. Patches ERROR_RATE + a new SERVICE_VERSION on the target Deployment
  3. Generates /api/work load so Prometheus 5xx series move
  4. Ingests live pod/Loki logs into storage (replacing demo noise for the window)
  5. Returns an Alert whose error_rate matches the injected fault
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

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


def live_mode_enabled() -> bool:
    s = get_settings()
    return (
        (s.remediation_mode or "").strip().lower() == "kubernetes"
        and bool((s.prometheus_url or "").strip())
    )


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
    """Hit in-cluster Service /api/work so Prom sees 5xx under ERROR_RATE."""
    urls = [
        f"http://{service}.{get_settings().k8s_namespace}.svc.cluster.local:8080/api/work",
        f"http://{service}:8080/api/work",
    ]
    # NodePort fallback (warroom may resolve DNS differently)
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


def fetch_live_log_lines(service: str, limit: int = 80) -> list[LogLine]:
    """Prefer Loki, then Kubernetes pod logs."""
    lines = _logs_from_loki(service, limit=limit)
    if lines:
        return lines
    return _logs_from_k8s(service, limit=limit)


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


def prepare_live_fire(
    storage: StorageService,
    service: str = "checkout-svc",
    error_rate: float = 0.42,
) -> dict[str, Any]:
    """Mutate the live scrape Deployment and return alert payload fields."""
    if service not in _SCRAPE:
        service = "checkout-svc"

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
    _wait_ready(apps, ns, service, timeout_s=90.0)

    # Let Prom scrape at least one interval, then generate 5xx traffic.
    time.sleep(3.0)
    load = _generate_load(service, bursts=100)
    time.sleep(2.0)
    load2 = _generate_load(service, bursts=40)
    load = {
        "ok": load["ok"] + load2["ok"],
        "err": load["err"] + load2["err"],
        "total": load["total"] + load2["total"],
    }

    n_logs = ingest_live_logs(storage, service)

    t = now_ms()
    storage.add_deploy(
        Deploy(
            id=f"dep_live_{service}_{t}",
            service=service,
            version=bad_ver,
            deployed_at=t - 2 * 60_000,
            deployed_by="aegispilot-live-fire",
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
            "snapshot_source_hint": "prometheus",
            "injected_error_rate": error_rate,
            "load": load,
            "logs_ingested": n_logs,
            "bad_version": bad_ver,
            "rollback_target": good_ver,
        },
    )
    log.info(
        "live fire ready service=%s bad=%s good=%s load=%s logs=%d",
        service, bad_ver, good_ver, load, n_logs,
    )
    return {"alert": alert, "scenario": "live", "service": service, "load": load}


def build_live_alert(storage: StorageService) -> Alert:
    return prepare_live_fire(storage)["alert"]
