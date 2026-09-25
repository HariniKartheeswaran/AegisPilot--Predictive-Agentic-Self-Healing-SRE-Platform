from __future__ import annotations

import logging
import os
import random
import time
from collections import deque
from typing import Any

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

SERVICE_NAME = os.getenv("SERVICE_NAME", "checkout-svc")
VERSION = os.getenv("SERVICE_VERSION", "v1.0.0")
PORT = int(os.getenv("PORT", "8080"))

# Mutable fault state — /admin/fault updates these without a pod restart so
# Docker Compose Fire and Kubernetes patch-env both drive the same metrics path.
_fault: dict[str, Any] = {
    "error_rate": float(os.getenv("ERROR_RATE", "0.0")),
    "latency_ms": int(os.getenv("LATENCY_MS", "20")),
    "fail_ready": os.getenv("FAIL_READY", "false").lower() in ("1", "true", "yes"),
    "version": VERSION,
}

_recent_logs: deque[dict[str, Any]] = deque(maxlen=200)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s service=%(service)s version=%(version)s %(message)s",
)
log = logging.getLogger("scrape")


class _Ctx(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.service = SERVICE_NAME
        record.version = _fault["version"]
        return True


log.addFilter(_Ctx())

REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "version", "method", "path", "code"],
)
ERRORS = Counter(
    "http_errors_total",
    "Total HTTP 5xx responses",
    ["service", "version", "path"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds",
    ["service", "version", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
UP = Gauge("service_up", "1 if process is serving", ["service", "version"])
READY = Gauge("service_ready", "1 if readiness passes", ["service", "version"])

app = FastAPI(title=SERVICE_NAME)
UP.labels(service=SERVICE_NAME, version=_fault["version"]).set(1)
READY.labels(service=SERVICE_NAME, version=_fault["version"]).set(
    0 if _fault["fail_ready"] else 1
)


def _version() -> str:
    return str(_fault["version"])


def _remember(level: str, message: str) -> None:
    _recent_logs.append(
        {
            "ts": int(time.time() * 1000),
            "level": level,
            "service": SERVICE_NAME,
            "version": _version(),
            "message": message[:500],
        }
    )


def _observe(path: str, code: int, started: float) -> None:
    elapsed = time.perf_counter() - started
    ver = _version()
    REQUESTS.labels(SERVICE_NAME, ver, "GET", path, str(code)).inc()
    LATENCY.labels(SERVICE_NAME, ver, path).observe(elapsed)
    if code >= 500:
        ERRORS.labels(SERVICE_NAME, ver, path).inc()
        msg = f"request_failed path={path} code={code} latency_ms={elapsed * 1000:.1f}"
        log.error(msg)
        _remember("ERROR", msg)
    else:
        msg = f"request_ok path={path} code={code} latency_ms={elapsed * 1000:.1f}"
        log.info(msg)
        _remember("INFO", msg)


class FaultBody(BaseModel):
    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_ms: int | None = Field(default=None, ge=0)
    fail_ready: bool | None = None
    service_version: str | None = None


@app.get("/health")
def health() -> dict:
    started = time.perf_counter()
    body = {"status": "ok", "service": SERVICE_NAME, "version": _version()}
    _observe("/health", 200, started)
    return body


@app.get("/ready")
def ready() -> Response:
    started = time.perf_counter()
    ver = _version()
    if _fault["fail_ready"]:
        READY.labels(service=SERVICE_NAME, version=ver).set(0)
        _observe("/ready", 503, started)
        return Response(
            content='{"status":"not_ready","service":"%s","version":"%s"}' % (SERVICE_NAME, ver),
            status_code=503,
            media_type="application/json",
        )
    READY.labels(service=SERVICE_NAME, version=ver).set(1)
    _observe("/ready", 200, started)
    return Response(
        content='{"status":"ready","service":"%s","version":"%s"}' % (SERVICE_NAME, ver),
        status_code=200,
        media_type="application/json",
    )


@app.get("/api/work")
def work() -> Response:
    started = time.perf_counter()
    ver = _version()
    delay = max(0, int(_fault["latency_ms"])) / 1000.0
    if delay:
        time.sleep(delay)
    rate = float(_fault["error_rate"])
    if random.random() < max(0.0, min(1.0, rate)):
        _observe("/api/work", 500, started)
        return Response(
            content='{"status":"error","service":"%s","version":"%s"}' % (SERVICE_NAME, ver),
            status_code=500,
            media_type="application/json",
        )
    _observe("/api/work", 200, started)
    return Response(
        content='{"status":"ok","service":"%s","version":"%s"}' % (SERVICE_NAME, ver),
        status_code=200,
        media_type="application/json",
    )


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/fault")
def get_fault() -> dict:
    return {
        "service": SERVICE_NAME,
        "error_rate": _fault["error_rate"],
        "latency_ms": _fault["latency_ms"],
        "fail_ready": _fault["fail_ready"],
        "service_version": _fault["version"],
    }


@app.post("/admin/fault")
def set_fault(body: FaultBody) -> dict:
    """Runtime fault injector used by Docker Compose live Fire / remediation."""
    if body.error_rate is not None:
        _fault["error_rate"] = float(body.error_rate)
    if body.latency_ms is not None:
        _fault["latency_ms"] = int(body.latency_ms)
    if body.fail_ready is not None:
        _fault["fail_ready"] = bool(body.fail_ready)
        READY.labels(service=SERVICE_NAME, version=_version()).set(
            0 if _fault["fail_ready"] else 1
        )
    if body.service_version is not None and body.service_version.strip():
        _fault["version"] = body.service_version.strip()
        UP.labels(service=SERVICE_NAME, version=_fault["version"]).set(1)
        READY.labels(service=SERVICE_NAME, version=_fault["version"]).set(
            0 if _fault["fail_ready"] else 1
        )
    msg = (
        f"fault_updated error_rate={_fault['error_rate']} "
        f"version={_fault['version']} latency_ms={_fault['latency_ms']}"
    )
    log.warning(msg)
    _remember("WARN", msg)
    return get_fault()


@app.get("/admin/logs")
def admin_logs(limit: int = 80) -> list[dict[str, Any]]:
    n = max(1, min(200, int(limit)))
    return list(_recent_logs)[-n:]


def main() -> None:
    import uvicorn

    log.info(
        "starting scrape service port=%s error_rate=%s latency_ms=%s",
        PORT,
        _fault["error_rate"],
        _fault["latency_ms"],
    )
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
