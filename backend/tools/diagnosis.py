"""Diagnosis tools — log fetching + deterministic log-line classification.

The classifier is a real rule engine over the log text: it buckets each line
into an operational class so the agent (and the UI) can show *what kind* of
failure this is, and so we can compute a fingerprint. The Gemini vision read of
the Grafana image happens in the agent via ctx.think(image_path=...).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from backend.models import LogLine
from backend.services.storage import StorageService

# Ordered rules: first match wins. Each is a real operational signature.
_CLASS_RULES: list[tuple[str, re.Pattern]] = [
    ("oom_killed", re.compile(r"oomkilled|out of memory|oom-?kill|exit code 137|killed.*memory", re.I)),
    ("memory_leak", re.compile(r"memory leak|heap (usage|growth)|old gen|gc pause|rss (growth|climbing)", re.I)),
    ("db_pool_exhaustion", re.compile(r"pool.*(exhaust|not available)|connection pool|hikari", re.I)),
    ("thread_pool_saturation", re.compile(r"thread pool|worker pool|queue depth|no available (threads|workers)|saturat", re.I)),
    ("downstream_timeout", re.compile(r"(504|gateway timeout|downstream|upstream).*(timeout|failed)|timeout acquiring", re.I)),
    ("high_latency", re.compile(r"p99|p95 latency|latency (spike|breach)|slow (query|response)|response time", re.I)),
    ("circuit_breaker", re.compile(r"circuit breaker", re.I)),
    ("null_pointer", re.compile(r"nullpointer|npe|segfault|undefined is not", re.I)),
    ("slo_breach", re.compile(r"slo|error rate .* exceeds|5xx (error )?rate|latency slo", re.I)),
    ("connection_leak", re.compile(r"connection leak", re.I)),
    ("autoscale", re.compile(r"autoscal|added \d+ pods|scaled|restarted pod|pod restart", re.I)),
    ("server_error", re.compile(r"\b5\d\d\b|internal server error|unhandled exception", re.I)),
    ("healthcheck", re.compile(r"healthcheck|degraded", re.I)),
]


def classify_log_line(message: str, level: str) -> str:
    for name, pattern in _CLASS_RULES:
        if pattern.search(message):
            return name
    if level.upper() in ("ERROR", "FATAL"):
        return "error_other"
    if level.upper() == "WARN":
        return "warning_other"
    return "info"


def fetch_logs(storage: StorageService, service: str, limit: int = 200) -> list[LogLine]:
    return storage.logs_for_service(service, limit=limit)


@dataclass
class LogAnalysis:
    total: int
    error_count: int
    warn_count: int
    class_counts: dict[str, int]
    dominant_class: str
    signature_terms: list[str]


def analyze_logs(logs: list[LogLine]) -> tuple[list[LogLine], LogAnalysis]:
    """Classify every line in place and summarize the distribution."""
    counts: Counter[str] = Counter()
    errors = warns = 0
    for lg in logs:
        lg.log_class = classify_log_line(lg.message, lg.level)
        counts[lg.log_class] += 1
        if lg.level.upper() in ("ERROR", "FATAL"):
            errors += 1
        elif lg.level.upper() == "WARN":
            warns += 1

    # Dominant *operational* class (ignore info/warning noise for the headline).
    meaningful = {k: v for k, v in counts.items() if k not in ("info", "warning_other")}
    dominant = max(meaningful, key=meaningful.get) if meaningful else "info"

    # Fingerprint terms: the distinct meaningful classes, stable-sorted.
    signature_terms = sorted(meaningful.keys())

    analysis = LogAnalysis(
        total=len(logs), error_count=errors, warn_count=warns,
        class_counts=dict(counts), dominant_class=dominant,
        signature_terms=signature_terms,
    )
    return logs, analysis


def build_fingerprint(service: str, analysis: LogAnalysis) -> str:
    """Stable incident fingerprint for the Memory agent's similarity search."""
    terms = " ".join(analysis.signature_terms)
    return f"{service} {analysis.dominant_class} {terms}".strip()
