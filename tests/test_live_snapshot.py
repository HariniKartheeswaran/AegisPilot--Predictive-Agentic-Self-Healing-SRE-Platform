"""Live Prom snapshot helper — unit tests (no cluster required)."""
from __future__ import annotations

from backend.services.live_snapshot import explore_url, ensure_alert_snapshot
from backend.models import Alert


def test_explore_url_contains_grafana_and_query():
    url = explore_url("checkout-svc", grafana_base="http://3.111.113.151:3000")
    assert url.startswith("http://3.111.113.151:3000/explore")
    assert "aegis-" in url or "up" in url


def test_ensure_skips_when_snapshot_already_set(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://example.invalid:9090")
    monkeypatch.setenv("GRAFANA_URL", "http://3.111.113.151:3000")
    from backend.config import get_settings

    get_settings.cache_clear()
    alert = Alert(
        alert="HighErrorRate",
        service="checkout-svc",
        grafana_snapshot="backend/seed/grafana_checkout_spike.png",
    )
    out = ensure_alert_snapshot(alert)
    assert out.grafana_snapshot == "backend/seed/grafana_checkout_spike.png"
    assert "grafana_explore_url" in out.metadata
    get_settings.cache_clear()


def test_ensure_noop_without_prometheus(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    monkeypatch.delenv("GRAFANA_URL", raising=False)
    from backend.config import get_settings

    get_settings.cache_clear()
    alert = Alert(alert="HighErrorRate", service="checkout-svc")
    out = ensure_alert_snapshot(alert)
    assert out.grafana_snapshot is None
    get_settings.cache_clear()
