"""Live Fire — real K8s/Prom/Loki incident, not seed HikariCP fixtures.

When REMEDIATION_MODE=kubernetes and PROMETHEUS_URL are set, Fire:
  1. Records the current scrape version as the rollback target
  2. Patches ERROR_RATE + a new SERVICE_VERSION on the target Deployment
  3. Publishes the alert immediately (UI spinner must not wait on rollout/load)
  4. Background: wait rollout, generate /api/work load, ingest live logs
"""
from __future__ import annotations

import logging
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


def _wait_ready(apps, ns: str, deploy: str, timeout_s: float = 45.0) -> None:
    from backend.tools.remediation import _wait_rollout

    _wait_rollout(apps, ns, deploy, timeout_s=timeout_s)


def clear_live_inject(service: str) -> None:
    """Drop injected ERROR_RATE so Prom/AM stop storming after reject/timeout."""
    if service not in _SCRAPE:
        return
    try:
        apps, _ = _load_apps()
        ns = get_settings().k8s_namespace
        _patch_env(
            apps,
            ns,
            service,
            {"ERROR_RATE": "0.0", "FAIL_READY": "false", "LATENCY_MS": "25"},
        )
        log.info("cleared live inject on %s", service)
    except Exception:  # noqa: BLE001
        log.exception("clear_live_inject failed for %s", service)


def _work_urls(service: str) -> list[str]:
    """Probe targets for /api/work load generation.

    Cleartext HTTP is intentional: scrape pods expose :8080 only inside the
    cluster / NodePort lab — there is no TLS sidecar on those demo services.
    """
    ns = get_settings().k8s_namespace
    # Cluster-internal + short-name + NodePort (lab IP). Scheme is cleartext by design.
    scheme = "http"  # NOSONAR python:S5332 — in-cluster scrape has no TLS
    urls = [
        f"{scheme}://{service}.{ns}.svc.cluster.local:8080/api/work",
        f"{scheme}://{service}:8080/api/work",
    ]
    port = _NODEPORTS.get(service)
    if port:
        urls.append(f"{scheme}://13.207.225.219:{port}/api/work")
    return urls


def _pick_work_url(service: str) -> str | None:
    """Probe once; reuse the first reachable URL (avoids 3× timeout per burst)."""
    with httpx.Client(timeout=0.6) as client:
        for url in _work_urls(service):
            try:
                client.get(url)
                return url
            except Exception:
                continue
    return None


def _generate_load(service: str, bursts: int = 24) -> dict[str, int]:
    """Hit /api/work so Prom sees 5xx under ERROR_RATE (fast, single URL)."""
    url = _pick_work_url(service)
    if not url:
        return {"ok": 0, "err": 0, "total": 0}

    ok = err = 0
    with httpx.Client(timeout=0.8) as client:
        for _ in range(bursts):
            try:
                r = client.get(url)
                if r.status_code >= 500:
                    err += 1
                else:
                    ok += 1
            except Exception:
                break
    return {"ok": ok, "err": err, "total": ok + err}


def fetch_live_log_lines(service: str, limit: int = 40) -> list[LogLine]:
    """Prefer Loki, then Kubernetes pod logs."""
    lines = _logs_from_loki(service, limit=limit)
    if lines:
        return lines
    return _logs_from_k8s(service, limit=limit)


def _classify_log_level(msg: str) -> str:
    if re.search(r"\berror\b|request_failed|\b5\d\d\b", msg, re.I):
        return "ERROR"
    if re.search(r"\bwarn", msg, re.I):
        return "WARN"
    return "INFO"


def _loki_base_url() -> str:
    s = get_settings()
    base = (
        getattr(s, "loki_url", None)
        or __import__("os").environ.get("LOKI_URL")
        or ""
    ).strip()
    if base:
        return base
    if (s.grafana_url or "").strip():
        host = s.grafana_url.rstrip("/").rsplit(":", 1)[0]
        return f"{host}:3100"
    return ""


def _parse_loki_streams(service: str, body: dict, limit: int) -> list[LogLine]:
    out: list[LogLine] = []
    for stream in (body.get("data") or {}).get("result") or []:
        for ts_ns, msg in stream.get("values") or []:
            try:
                ts_ms = int(int(ts_ns) / 1_000_000)
            except (TypeError, ValueError):
                ts_ms = now_ms()
            out.append(
                LogLine(
                    id=f"log_live_{service}_{ts_ms}_{len(out)}",
                    service=service,
                    ts=ts_ms,
                    level=_classify_log_level(msg),
                    message=msg.strip()[:500],
                )
            )
    out.sort(key=lambda x: x.ts)
    return out[-limit:]


def _logs_from_loki(service: str, limit: int = 40) -> list[LogLine]:
    base = _loki_base_url()
    if not base:
        return []
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - 15 * 60 * 10**9
    query = '{app="%s"}' % service
    url = f"{base.rstrip('/')}/loki/api/v1/query_range"
    try:
        with httpx.Client(timeout=4.0) as client:
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
    return _parse_loki_streams(service, body, limit)

def _logs_from_k8s(service: str, limit: int = 40) -> list[LogLine]:
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
        level = _classify_log_level(msg)
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
    *,
    wait_ready: bool = False,
    generate_load: bool = False,
) -> dict[str, Any]:
    """Patch the scrape Deployment and return an alert immediately.

    By default does **not** block on rollout/load — callers should schedule
    ``warm_live_metrics`` in the background so `/api/demo/fire` returns fast.
    """
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

    load: dict[str, int] = {"ok": 0, "err": 0, "total": 0}
    n_logs = 0
    if wait_ready:
        _wait_ready(apps, ns, service, timeout_s=45.0)
    if generate_load:
        time.sleep(1.0)
        load = _generate_load(service, bursts=24)
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
        "live fire patched service=%s bad=%s good=%s load=%s logs=%d wait=%s",
        service, bad_ver, good_ver, load, n_logs, wait_ready,
    )
    return {"alert": alert, "scenario": "live", "service": service, "load": load}


def _current_error_rate(service: str) -> float:
    """Read injected ERROR_RATE from the live Deployment (0 if healed)."""
    try:
        apps, _ = _load_apps()
        ns = get_settings().k8s_namespace
        dep = apps.read_namespaced_deployment(service, ns)
        containers = dep.spec.template.spec.containers or []
        raw = _env_val(containers, "ERROR_RATE", "0")
        return float(raw)
    except Exception:  # noqa: BLE001
        return 0.0


def _merge_load(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {
        "ok": a["ok"] + b["ok"],
        "err": a["err"] + b["err"],
        "total": a["total"] + b["total"],
    }


def _sustain_load(service: str, load: dict[str, int], sustain_s: float) -> dict[str, int]:
    deadline = time.monotonic() + max(0.0, sustain_s)
    while time.monotonic() < deadline:
        if _current_error_rate(service) <= 0.01:
            log.info("live fire sustain stop — ERROR_RATE cleared on %s", service)
            break
        time.sleep(3.0)
        load = _merge_load(load, _generate_load(service, bursts=12))
    return load


def warm_live_metrics(
    storage: StorageService,
    service: str = "checkout-svc",
    bursts: int = 24,
    sustain_s: float = 90.0,
) -> dict[str, int]:
    """Background: wait for rollout, then keep /api/work hot so Prom can fire.

    A single short burst decays out of rate(...[1m]) before rules' ``for``
    windows elapse — sustain load ~90s so Warning/Critical can go Pending→Firing
    while agents run. Stops early once remediation clears ERROR_RATE.
    """
    if service not in _SCRAPE:
        service = "checkout-svc"
    try:
        apps, _ = _load_apps()
        ns = get_settings().k8s_namespace
        try:
            _wait_ready(apps, ns, service, timeout_s=45.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("live fire rollout wait: %s (continuing load)", exc)
        time.sleep(1.0)
        load = _generate_load(service, bursts=bursts)
        n_logs = ingest_live_logs(storage, service)
        load = _sustain_load(service, load, sustain_s)
        log.info("live fire warmed service=%s load=%s logs=%d", service, load, n_logs)
        return load
    except Exception:  # noqa: BLE001
        log.exception("warm_live_metrics failed for %s", service)
        return {"ok": 0, "err": 0, "total": 0}


def build_live_alert(storage: StorageService) -> Alert:
    return prepare_live_fire(storage)["alert"]
