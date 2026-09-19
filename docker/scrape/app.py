from __future__ import annotations

import logging
import os
import random
import time

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "checkout-svc")
VERSION = os.getenv("SERVICE_VERSION", "v1.0.0")
PORT = int(os.getenv("PORT", "8080"))
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.0"))
LATENCY_MS = int(os.getenv("LATENCY_MS", "20"))
FAIL_READY = os.getenv("FAIL_READY", "false").lower() in ("1", "true", "yes")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s service=%(service)s version=%(version)s %(message)s",
)
log = logging.getLogger("scrape")


class _Ctx(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.service = SERVICE_NAME
        record.version = VERSION
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
UP.labels(service=SERVICE_NAME, version=VERSION).set(1)
READY.labels(service=SERVICE_NAME, version=VERSION).set(0 if FAIL_READY else 1)


def _observe(path: str, code: int, started: float) -> None:
    elapsed = time.perf_counter() - started
    REQUESTS.labels(SERVICE_NAME, VERSION, "GET", path, str(code)).inc()
    LATENCY.labels(SERVICE_NAME, VERSION, path).observe(elapsed)
    if code >= 500:
        ERRORS.labels(SERVICE_NAME, VERSION, path).inc()
        log.error("request_failed path=%s code=%s latency_ms=%.1f", path, code, elapsed * 1000)
    else:
        log.info("request_ok path=%s code=%s latency_ms=%.1f", path, code, elapsed * 1000)


@app.get("/health")
def health() -> dict:
    started = time.perf_counter()
    body = {"status": "ok", "service": SERVICE_NAME, "version": VERSION}
    _observe("/health", 200, started)
    return body


@app.get("/ready")
def ready() -> Response:
    started = time.perf_counter()
    if FAIL_READY:
        READY.labels(service=SERVICE_NAME, version=VERSION).set(0)
        _observe("/ready", 503, started)
        return Response(
            content='{"status":"not_ready","service":"%s","version":"%s"}' % (SERVICE_NAME, VERSION),
            status_code=503,
            media_type="application/json",
        )
    READY.labels(service=SERVICE_NAME, version=VERSION).set(1)
    _observe("/ready", 200, started)
    return Response(
        content='{"status":"ready","service":"%s","version":"%s"}' % (SERVICE_NAME, VERSION),
        status_code=200,
        media_type="application/json",
    )


@app.get("/api/work")
def work() -> Response:
    started = time.perf_counter()
    delay = max(0, LATENCY_MS) / 1000.0
    if delay:
        time.sleep(delay)
    if random.random() < max(0.0, min(1.0, ERROR_RATE)):
        _observe("/api/work", 500, started)
        return Response(
            content='{"status":"error","service":"%s","version":"%s"}' % (SERVICE_NAME, VERSION),
            status_code=500,
            media_type="application/json",
        )
    _observe("/api/work", 200, started)
    return Response(
        content='{"status":"ok","service":"%s","version":"%s"}' % (SERVICE_NAME, VERSION),
        status_code=200,
        media_type="application/json",
    )


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def main() -> None:
    import uvicorn

    log.info("starting scrape service port=%s error_rate=%s latency_ms=%s", PORT, ERROR_RATE, LATENCY_MS)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
