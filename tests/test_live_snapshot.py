"""Live Prom snapshot helper — unit tests (no cluster required)."""
from __future__ import annotations

from backend.services.live_snapshot import explore_url, ensure_alert_snapshot
from backend.models import Alert


def test_explore_url_contains_grafana_and_query():
    url = explore_url("checkout-svc", grafana_base="http://3.111.113.151:3000")
    # Live board (same URL teammates open), filtered to the alerted service
    assert url.startswith("http://3.111.113.151:3000/d/ad6nckx/aegispilot-dashboard")
    assert "var-service=checkout-svc" in url
    assert "from=now-1h" in url


def test_ensure_keeps_seed_when_live_prom_unreachable(monkeypatch):
    """Seed demo PNG is only kept when live capture fails."""
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


def test_ensure_replaces_seed_with_live(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.example:9090")
    monkeypatch.setenv("GRAFANA_URL", "http://3.111.113.151:3000")
    from backend.config import get_settings

    get_settings.cache_clear()

    def _fake_capture(service: str):
        return {
            "grafana_snapshot": f"/tmp/aegis_snapshots/{service}_live.png",
            "grafana_explore_url": "http://3.111.113.151:3000/explore?x=1",
            "grafana_snapshot_b64": "aGVsbG8=",
            "source": "prometheus",
        }

    monkeypatch.setattr(
        "backend.services.live_snapshot.capture_live_snapshot", _fake_capture
    )
    alert = Alert(
        alert="HighErrorRate",
        service="checkout-svc",
        grafana_snapshot="backend/seed/grafana_checkout_spike.png",
    )
    out = ensure_alert_snapshot(alert)
    assert out.grafana_snapshot == "/tmp/aegis_snapshots/checkout-svc_live.png"
    assert out.metadata.get("snapshot_source") == "prometheus"
    assert out.metadata.get("grafana_snapshot_b64") == "aGVsbG8="
    assert "seed_snapshot_replaced" in out.metadata
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
