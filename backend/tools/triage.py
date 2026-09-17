"""Triage tools — deterministic severity + service-topology reasoning.

Real functions: no model call here. The Triage agent uses these to ground its
LLM judgement in hard signals (error rate, service tier, dependency fan-out).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# A small but real service dependency graph. `tier` drives severity; the edges
# drive blast-radius reasoning.
SERVICE_CATALOG: dict[str, dict] = {
    "checkout-svc": {"tier": 0, "upstreams": ["web-bff"], "downstreams": ["payments-svc", "inventory-svc", "cart-svc"], "traffic_share": 0.40},
    "payments-svc": {"tier": 0, "upstreams": ["checkout-svc"], "downstreams": ["ledger-svc"], "traffic_share": 0.35},
    "cart-svc": {"tier": 1, "upstreams": ["web-bff", "checkout-svc"], "downstreams": ["redis"], "traffic_share": 0.25},
    "inventory-svc": {"tier": 1, "upstreams": ["checkout-svc"], "downstreams": ["warehouse-db"], "traffic_share": 0.15},
    "web-bff": {"tier": 0, "upstreams": ["cdn"], "downstreams": ["checkout-svc", "cart-svc"], "traffic_share": 1.0},
}


@dataclass
class ServiceInfo:
    service: str
    tier: int
    upstreams: list[str]
    downstreams: list[str]
    traffic_share: float
    known: bool


def resolve_service(service: str) -> ServiceInfo:
    meta = SERVICE_CATALOG.get(service)
    if meta is None:
        # Unknown service: assume a mid-tier leaf so we never crash on new names.
        return ServiceInfo(service, tier=2, upstreams=[], downstreams=[], traffic_share=0.05, known=False)
    return ServiceInfo(
        service=service, tier=meta["tier"], upstreams=list(meta["upstreams"]),
        downstreams=list(meta["downstreams"]), traffic_share=meta["traffic_share"], known=True,
    )


def parse_error_rate(error_rate: Optional[str]) -> float:
    """'42%' -> 42.0. Tolerant of None / bare numbers."""
    if not error_rate:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)", str(error_rate))
    return float(m.group(1)) if m else 0.0


def classify_severity(error_rate_pct: float, tier: int) -> str:
    """SEV mapping grounded in error rate + how central the service is.

    Tier-0 (checkout/payments) at 40% errors is a company-down SEV1. The same
    rate on a tier-2 leaf is a SEV3. This is the real rubric, not a guess.
    """
    if tier == 0:
        if error_rate_pct >= 25: return "SEV1"
        if error_rate_pct >= 8:  return "SEV2"
        if error_rate_pct >= 2:  return "SEV3"
        return "SEV4"
    if tier == 1:
        if error_rate_pct >= 40: return "SEV1"
        if error_rate_pct >= 15: return "SEV2"
        if error_rate_pct >= 4:  return "SEV3"
        return "SEV4"
    # tier 2+
    if error_rate_pct >= 60: return "SEV2"
    if error_rate_pct >= 20: return "SEV3"
    return "SEV4"


def estimate_blast_radius(info: ServiceInfo, error_rate_pct: float) -> str:
    affected = round(info.traffic_share * (error_rate_pct / 100.0) * 100, 1)
    downstream = ", ".join(info.downstreams) or "none"
    return (
        f"~{affected}% of total traffic impacted "
        f"({error_rate_pct:.0f}% of {info.service}'s {int(info.traffic_share*100)}% share). "
        f"At-risk downstreams: {downstream}."
    )


def oncall_for(service: str) -> str:
    """Deterministic on-call routing by service tier."""
    tier = resolve_service(service).tier
    return {0: "#oncall-payments-critical", 1: "#oncall-commerce", 2: "#oncall-platform"}.get(tier, "#oncall-platform")
