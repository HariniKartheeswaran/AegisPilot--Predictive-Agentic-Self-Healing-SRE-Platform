from unittest.mock import MagicMock

from backend.models import LogLine
from backend.tools.diagnosis import (
    LogAnalysis,
    analyze_logs,
    build_fingerprint,
    classify_log_line,
    fetch_logs,
)


def test_classify_log_line_matches_operational_rules():
    cases = [
        ("OOMKilled container", "ERROR", "oom_killed"),
        ("memory leak detected", "ERROR", "memory_leak"),
        ("connection pool exhausted", "ERROR", "db_pool_exhaustion"),
        ("thread pool saturated", "WARN", "thread_pool_saturation"),
        ("downstream request failed", "ERROR", "downstream_timeout"),
        ("p99 latency spike", "WARN", "high_latency"),
        ("circuit breaker opened", "ERROR", "circuit_breaker"),
        ("NullPointerException", "ERROR", "null_pointer"),
        ("SLO error rate exceeds threshold", "ERROR", "slo_breach"),
        ("connection leak detected", "ERROR", "connection_leak"),
        ("autoscaling added 3 pods", "INFO", "autoscale"),
        ("HTTP 500 internal server error", "ERROR", "server_error"),
        ("healthcheck degraded", "WARN", "healthcheck"),
    ]

    for message, level, expected in cases:
        assert classify_log_line(message, level) == expected


def test_classify_log_line_uses_level_fallbacks():
    assert classify_log_line("something unexpected", "ERROR") == "error_other"
    assert classify_log_line("something unexpected", "FATAL") == "error_other"
    assert classify_log_line("something unexpected", "WARN") == "warning_other"
    assert classify_log_line("something unexpected", "INFO") == "info"


def test_classify_log_line_is_case_insensitive():
    assert classify_log_line("OUT OF MEMORY", "error") == "oom_killed"
    assert classify_log_line("Circuit Breaker OPEN", "error") == "circuit_breaker"


def test_fetch_logs_delegates_to_storage():
    storage = MagicMock()
    expected = [
        LogLine(
            id="log-1",
            service="payment-api",
            ts=1000,
            level="ERROR",
            message="connection pool exhausted",
        )
    ]
    storage.logs_for_service.return_value = expected

    result = fetch_logs(storage, "payment-api", limit=50)

    assert result == expected
    storage.logs_for_service.assert_called_once_with("payment-api", limit=50)


def test_analyze_logs_classifies_and_summarizes():
    logs = [
        LogLine(
            id="1",
            service="payment-api",
            ts=1000,
            level="ERROR",
            message="connection pool exhausted",
        ),
        LogLine(
            id="2",
            service="payment-api",
            ts=1001,
            level="ERROR",
            message="connection pool unavailable",
        ),
        LogLine(
            id="3",
            service="payment-api",
            ts=1002,
            level="WARN",
            message="healthcheck degraded",
        ),
        LogLine(
            id="4",
            service="payment-api",
            ts=1003,
            level="INFO",
            message="request completed",
        ),
    ]

    result_logs, analysis = analyze_logs(logs)

    assert result_logs is logs
    assert analysis.total == 4
    assert analysis.error_count == 2
    assert analysis.warn_count == 1
    assert analysis.class_counts["db_pool_exhaustion"] == 2
    assert analysis.class_counts["healthcheck"] == 1
    assert analysis.class_counts["info"] == 1
    assert analysis.dominant_class == "db_pool_exhaustion"
    assert analysis.signature_terms == [
        "db_pool_exhaustion",
        "healthcheck",
    ]

    assert logs[0].log_class == "db_pool_exhaustion"
    assert logs[2].log_class == "healthcheck"
    assert logs[3].log_class == "info"


def test_analyze_logs_ignores_info_and_warning_noise_for_dominant_class():
    logs = [
        LogLine(
            id="1",
            service="api",
            ts=1,
            level="INFO",
            message="request completed",
        ),
        LogLine(
            id="2",
            service="api",
            ts=2,
            level="WARN",
            message="something unexpected",
        ),
    ]

    _, analysis = analyze_logs(logs)

    assert analysis.dominant_class == "info"
    assert analysis.signature_terms == []


def test_analyze_logs_counts_fatal_as_error():
    logs = [
        LogLine(
            id="1",
            service="api",
            ts=1,
            level="FATAL",
            message="catastrophic failure",
        ),
        LogLine(
            id="2",
            service="api",
            ts=2,
            level="WARN",
            message="degraded",
        ),
    ]

    _, analysis = analyze_logs(logs)

    assert analysis.error_count == 1
    assert analysis.warn_count == 1


def test_analyze_logs_empty_input():
    logs, analysis = analyze_logs([])

    assert logs == []
    assert analysis == LogAnalysis(
        total=0,
        error_count=0,
        warn_count=0,
        class_counts={},
        dominant_class="info",
        signature_terms=[],
    )


def test_build_fingerprint_creates_stable_string():
    analysis = LogAnalysis(
        total=5,
        error_count=3,
        warn_count=1,
        class_counts={
            "db_pool_exhaustion": 3,
            "healthcheck": 1,
            "info": 1,
        },
        dominant_class="db_pool_exhaustion",
        signature_terms=["db_pool_exhaustion", "healthcheck"],
    )

    assert (
        build_fingerprint("payment-api", analysis)
        == "payment-api db_pool_exhaustion db_pool_exhaustion healthcheck"
    )


def test_build_fingerprint_handles_empty_terms():
    analysis = LogAnalysis(
        total=0,
        error_count=0,
        warn_count=0,
        class_counts={},
        dominant_class="info",
        signature_terms=[],
    )

    assert build_fingerprint("payment-api", analysis) == "payment-api info"
