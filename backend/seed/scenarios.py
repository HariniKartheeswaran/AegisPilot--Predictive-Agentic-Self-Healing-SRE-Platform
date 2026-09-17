"""Demo scenario catalog — three distinct, realistic incidents that rotate.

Each scenario is a self-contained, time-relative fixture set: a recent "bad
deploy" (~12 min before the alert), a log stream with a matching failure
signature, a past-incident memory with an aligned fingerprint, an alert payload,
and its own Grafana snapshot. Because every agent reads dynamically by service,
the whole six-agent pipeline works for any scenario with no code changes.

Rotating these makes the demo feel like a real on-call tool, not a one-trick
button — every fire is a different service, symptom, dashboard, and RCA.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Callable

from backend.models import Deploy, IncidentMemory, LogLine, now_ms
from backend.services.embedding import embed_text
from backend.services.storage import StorageService

MIN = 60_000
DAY = 24 * 60 * MIN


@dataclass
class Scenario:
    key: str
    alert: dict[str, Any]
    deploys: Callable[[], list[Deploy]]
    logs: Callable[[], list[LogLine]]
    memory: Callable[[], IncidentMemory]


def _logs(service: str, lines: list[tuple[int, str, str]]) -> list[LogLine]:
    t = now_ms()
    return [
        LogLine(id=f"log_{service}_{i:03d}", service=service, ts=t - m * MIN, level=lvl, message=msg)
        for i, (m, lvl, msg) in enumerate(lines)
    ]


# --------------------------------------------------------------------------- #
# 1. checkout-svc — bad deploy → DB pool exhaustion + downstream timeouts
# --------------------------------------------------------------------------- #
def _checkout_deploys() -> list[Deploy]:
    t = now_ms()
    return [
        Deploy(id="dep_c241", service="checkout-svc", version="v2.4.1", deployed_at=t - 12 * MIN,
               deployed_by="alice@corp.dev", commit_sha="9f3ac21", rollback_target="v2.4.0"),
        Deploy(id="dep_c240", service="checkout-svc", version="v2.4.0", deployed_at=t - 3 * DAY,
               deployed_by="bob@corp.dev", commit_sha="1122ff0", rollback_target="v2.3.9"),
        Deploy(id="dep_c239", service="checkout-svc", version="v2.3.9", deployed_at=t - 9 * DAY,
               deployed_by="alice@corp.dev", commit_sha="aa5be31", rollback_target="v2.3.8"),
    ]


def _checkout_logs() -> list[LogLine]:
    return _logs("checkout-svc", [
        (14, "INFO", "checkout-svc v2.4.1 rollout complete, 6/6 pods ready"),
        (11, "WARN", "HikariPool-1 - Connection is not available, request queued (active=50, idle=0)"),
        (10, "ERROR", "com.corp.checkout.OrderService - Timeout acquiring DB connection after 30000ms"),
        (10, "ERROR", "Downstream call payments-svc failed: 504 Gateway Timeout (upstream 30s)"),
        (9, "ERROR", "HikariPool-1 - Connection pool exhausted (active=50, max=50, pending=137)"),
        (9, "ERROR", "Unhandled exception in POST /api/checkout: NullPointerException at PricingResolver:88"),
        (8, "ERROR", "HTTP 500 returned for POST /api/checkout (trace_id=b1f9...c2, user a.k@example.com)"),
        (7, "WARN", "Circuit breaker 'payments' transitioned CLOSED -> OPEN after 20 failures"),
        (6, "ERROR", "5xx error rate 41.8% over last 60s exceeds SLO 5%"),
        (3, "ERROR", "HTTP 500 returned for POST /api/checkout (trace_id=99ad...71)"),
        (1, "ERROR", "checkout-svc healthcheck degraded: 5xx=42% p99=1180ms"),
    ])


def _checkout_memory() -> IncidentMemory:
    fp = ("checkout-svc db_pool_exhaustion downstream_timeout 5xx surge after deploy "
          "connection pool exhausted payments timeout")
    return IncidentMemory(
        fingerprint_id="fp_checkout_deploy_5xx", fingerprint=fp, embedding=embed_text(fp),
        past_incident_ids=["inc_2024_0418", "inc_2024_0902"],
        typical_cause="Regression in a fresh checkout-svc deploy exhausting the DB connection "
                      "pool, cascading into payments timeouts.",
        typical_fix="Roll back checkout-svc to the previous known-good version.",
        avg_resolution_minutes=4.0)


# --------------------------------------------------------------------------- #
# 2. cart-svc — bad deploy → memory leak → OOMKilled pods
# --------------------------------------------------------------------------- #
def _cart_deploys() -> list[Deploy]:
    t = now_ms()
    return [
        Deploy(id="dep_cart181", service="cart-svc", version="v1.8.1", deployed_at=t - 11 * MIN,
               deployed_by="carol@corp.dev", commit_sha="77aa910", rollback_target="v1.8.0"),
        Deploy(id="dep_cart180", service="cart-svc", version="v1.8.0", deployed_at=t - 5 * DAY,
               deployed_by="dave@corp.dev", commit_sha="34bc001", rollback_target="v1.7.9"),
        Deploy(id="dep_cart179", service="cart-svc", version="v1.7.9", deployed_at=t - 12 * DAY,
               deployed_by="carol@corp.dev", commit_sha="90de112", rollback_target="v1.7.8"),
    ]


def _cart_logs() -> list[LogLine]:
    return _logs("cart-svc", [
        (14, "INFO", "cart-svc v1.8.1 rollout complete, 8/8 pods ready"),
        (11, "WARN", "cart-svc-7f9c container memory usage 92% of 512Mi limit"),
        (10, "ERROR", "java.lang.OutOfMemoryError: Java heap space in CartAggregator.merge()"),
        (9, "WARN", "GC pause 2.3s (old gen 98% occupied after full collection)"),
        (8, "ERROR", "Pod cart-svc-7f9c OOMKilled (exit code 137), restarting"),
        (7, "WARN", "RSS growth ~40MB/min observed since the v1.8.1 deploy (memory leak suspected)"),
        (6, "ERROR", "Pod cart-svc-3a2b OOMKilled (exit code 137)"),
        (5, "INFO", "Kubelet restarted pod cart-svc-3a2b (3rd restart in 10m)"),
        (4, "ERROR", "cart-svc healthcheck degraded: 4/8 pods CrashLoopBackOff"),
        (2, "ERROR", "Heap usage climbing; leak in cart aggregation cache after deploy"),
        (1, "WARN", "cart-svc error rate 18% (pods restarting)"),
    ])


def _cart_memory() -> IncidentMemory:
    fp = "cart-svc oom_killed memory_leak heap out of memory pods restart after deploy leak"
    return IncidentMemory(
        fingerprint_id="fp_cart_oom", fingerprint=fp, embedding=embed_text(fp),
        past_incident_ids=["inc_2024_0611", "inc_2024_0733"],
        typical_cause="Memory leak introduced in a fresh cart-svc deploy causing OOMKilled pods.",
        typical_fix="Roll back cart-svc to the previous known-good version.",
        avg_resolution_minutes=6.0)


# --------------------------------------------------------------------------- #
# 3. payments-svc — bad deploy → latency blow-out + thread-pool saturation
# --------------------------------------------------------------------------- #
def _payments_deploys() -> list[Deploy]:
    t = now_ms()
    return [
        Deploy(id="dep_pay553", service="payments-svc", version="v5.5.3", deployed_at=t - 13 * MIN,
               deployed_by="dave@corp.dev", commit_sha="c0ffee1", rollback_target="v5.5.2"),
        Deploy(id="dep_pay552", service="payments-svc", version="v5.5.2", deployed_at=t - 4 * DAY,
               deployed_by="erin@corp.dev", commit_sha="b0bcaf3", rollback_target="v5.5.1"),
        Deploy(id="dep_pay551", service="payments-svc", version="v5.5.1", deployed_at=t - 10 * DAY,
               deployed_by="dave@corp.dev", commit_sha="1dec0de", rollback_target="v5.5.0"),
    ]


def _payments_logs() -> list[LogLine]:
    return _logs("payments-svc", [
        (14, "INFO", "payments-svc v5.5.3 rollout complete, 10/10 pods ready"),
        (11, "WARN", "payments-svc p99 latency 1400ms (baseline 180ms) after deploy"),
        (10, "ERROR", "Slow query: SELECT * FROM ledger WHERE ... took 3200ms (missing index)"),
        (9, "WARN", "Thread pool 'payment-exec' saturated: queue depth 240, no available workers"),
        (8, "ERROR", "Downstream ledger-svc call timeout after 5000ms"),
        (7, "WARN", "p95 latency 900ms exceeds latency SLO 300ms"),
        (6, "ERROR", "Request timeout on POST /api/charge (trace_id=88ac...d1)"),
        (4, "WARN", "Connection pool wait time 1800ms to ledger-svc"),
        (2, "ERROR", "payments-svc latency SLO breach: p99 1.5s sustained"),
        (1, "WARN", "payments-svc error rate 9% (timeouts)"),
    ])


def _payments_memory() -> IncidentMemory:
    fp = ("payments-svc high_latency thread_pool_saturation downstream_timeout ledger slow query "
          "after deploy latency slo breach")
    return IncidentMemory(
        fingerprint_id="fp_payments_latency", fingerprint=fp, embedding=embed_text(fp),
        past_incident_ids=["inc_2024_0521", "inc_2024_0815"],
        typical_cause="A payments-svc deploy regressed a query, saturating the thread pool and "
                      "timing out downstream ledger calls.",
        typical_fix="Roll back payments-svc to the previous known-good version.",
        avg_resolution_minutes=5.0)


SCENARIOS: list[Scenario] = [
    Scenario(
        key="checkout",
        alert={"alert": "HighErrorRate", "service": "checkout-svc", "error_rate": "42%",
               "grafana_snapshot": "backend/seed/grafana_checkout_spike.png"},
        deploys=_checkout_deploys, logs=_checkout_logs, memory=_checkout_memory),
    Scenario(
        key="cart",
        alert={"alert": "OOMKilled", "service": "cart-svc", "error_rate": "18%",
               "grafana_snapshot": "backend/seed/grafana_cart_oom.png"},
        deploys=_cart_deploys, logs=_cart_logs, memory=_cart_memory),
    Scenario(
        key="payments",
        alert={"alert": "HighLatency", "service": "payments-svc", "error_rate": "9%",
               "grafana_snapshot": "backend/seed/grafana_payments_latency.png"},
        deploys=_payments_deploys, logs=_payments_logs, memory=_payments_memory),
]

BY_KEY = {s.key: s for s in SCENARIOS}
# Round-robin cursor so consecutive fires cycle checkout → cart → payments.
_cycle = itertools.cycle(SCENARIOS)


def next_scenario() -> Scenario:
    return next(_cycle)


def all_memories() -> list[IncidentMemory]:
    return [s.memory() for s in SCENARIOS]


def refresh_all_timelines(storage: StorageService) -> None:
    """Re-stamp every scenario's deploys + logs relative to NOW so each bad
    deploy stays ~12 min before its alert (keeps Correlation confidence honest)."""
    for s in SCENARIOS:
        for d in s.deploys():
            storage.add_deploy(d)
        for lg in s.logs():
            storage.add_log(lg)
