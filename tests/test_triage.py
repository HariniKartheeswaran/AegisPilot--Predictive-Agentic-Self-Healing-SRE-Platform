"""Unit tests for Triage tools covering normal, boundary, and edge cases."""
import pytest
from backend.tools.triage import (
    SERVICE_CATALOG,
    classify_severity,
    estimate_blast_radius,
    oncall_for,
    parse_error_rate,
    resolve_service,
)


def test_resolve_known_service():
    """Verify known services resolve with correct tiers and dependency trees."""
    info = resolve_service("checkout-svc")
    assert info.known is True
    assert info.tier == 0
    assert "payments-svc" in info.downstreams
    assert info.traffic_share == 0.40


def test_resolve_unknown_service_fallback():
    """Verify unknown services fall back safely to a default leaf tier without crashing."""
    info = resolve_service("new-microservice-xyz")
    assert info.known is False
    assert info.tier == 2
    assert info.upstreams == []
    assert info.downstreams == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("42%", 42.0),
        ("5.5%", 5.5),
        ("10", 10.0),
        ("error rate is 8.2%", 8.2),
        ("", 0.0),
        (None, 0.0),
        ("zero errors", 0.0),
    ],
)
def test_parse_error_rate(raw, expected):
    """Verify parsing handles diverse string inputs and edge formats."""
    assert parse_error_rate(raw) == expected


def test_classify_severity_tier_0_boundaries():
    """Verify Tier 0 boundary thresholds."""
    assert classify_severity(25.0, tier=0) == "SEV1"
    assert classify_severity(24.9, tier=0) == "SEV2"
    assert classify_severity(8.0, tier=0) == "SEV2"
    assert classify_severity(7.9, tier=0) == "SEV3"
    assert classify_severity(2.0, tier=0) == "SEV3"
    assert classify_severity(1.9, tier=0) == "SEV4"
    assert classify_severity(0.0, tier=0) == "SEV4"


def test_classify_severity_tier_1_boundaries():
    """Verify Tier 1 boundary thresholds."""
    assert classify_severity(40.0, tier=1) == "SEV1"
    assert classify_severity(39.9, tier=1) == "SEV2"
    assert classify_severity(15.0, tier=1) == "SEV2"
    assert classify_severity(14.9, tier=1) == "SEV3"
    assert classify_severity(4.0, tier=1) == "SEV3"
    assert classify_severity(3.9, tier=1) == "SEV4"


def test_classify_severity_tier_2_boundaries():
    """Verify Tier 2 boundary thresholds."""
    assert classify_severity(60.0, tier=2) == "SEV2"
    assert classify_severity(59.9, tier=2) == "SEV3"
    assert classify_severity(20.0, tier=2) == "SEV3"
    assert classify_severity(19.9, tier=2) == "SEV4"


def test_estimate_blast_radius():
    """Verify blast radius calculation with known service."""
    info = resolve_service("checkout-svc")
    blast = estimate_blast_radius(info, 50.0)
    assert "20.0%" in blast
    assert "checkout-svc" in blast
    assert "payments-svc" in blast


def test_oncall_routing():
    """Verify on-call channel mapping."""
    assert oncall_for("checkout-svc") == "#oncall-payments-critical"
    assert oncall_for("cart-svc") == "#oncall-commerce"
    assert oncall_for("unknown-service") == "#oncall-platform"
