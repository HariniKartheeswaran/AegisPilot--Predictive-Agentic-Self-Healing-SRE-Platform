"""Live Prom snapshot helper — unit tests (no cluster required)."""
from __future__ import annotations

from backend.models import Alert
from backend.services import live_snapshot as ls
from backend.services.live_snapshot import ensure_alert_snapshot, explore_url


def test_explore_url_contains_grafana_and_query(monkeypatch):
    monkeypatch.delenv("GRAFANA_DASHBOARD_PATH", raising=False)
    from backend.config import get_settings

    get_settings.cache_clear()
    url = explore_url("checkout-svc", grafana_base="http://3.111.113.151:3000")
    assert url.startswith("http://3.111.113.151:3000/d/ad6nckx/aegispilot-dashboard")
    assert "var-service=checkout-svc" in url
    assert "from=now-1h" in url
    get_settings.cache_clear()


def test_dashboard_path_default_and_override(monkeypatch):
    monkeypatch.delenv("GRAFANA_DASHBOARD_PATH", raising=False)
    from backend.config import get_settings

    get_settings.cache_clear()
    assert ls._dashboard_path() == ls.DEFAULT_DASHBOARD_PATH
    monkeypatch.setenv("GRAFANA_DASHBOARD_PATH", "/d/custom/board")
    get_settings.cache_clear()
    assert ls._dashboard_path() == "/d/custom/board"
    get_settings.cache_clear()


def test_sel_and_up_query():
    assert 'service="checkout-svc"' in ls._sel("checkout-svc")
    assert "aegis-.*" in ls._sel("other")
    assert 'service="cart-svc"' in ls._up_query("cart-svc")
    assert "aegis-.*" in ls._up_query("other")


def test_is_seed_snapshot_path():
    assert ls._is_seed_snapshot_path("backend/seed/grafana_checkout_spike.png")
    assert not ls._is_seed_snapshot_path("/tmp/live.png")
    assert not ls._is_seed_snapshot_path(None)


def test_attach_explore_only():
    alert = Alert(alert="HighErrorRate", service="checkout-svc")
    out = ls._attach_explore_only(alert, "http://g:3000")
    assert "grafana_explore_url" in out.metadata


def test_ensure_clears_seed_when_live_prom_unreachable(monkeypatch):
    """Live Prom configured but capture fails → drop demo seed PNG (no fake art)."""
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
    assert out.grafana_snapshot is None
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


def test_query_prom_panels_delegates(monkeypatch):
    calls = []

    def _qr(prom, q):
        calls.append(q)
        return [(1, 1.0)]

    monkeypatch.setattr(ls, "_query_range", _qr)
    out = ls._query_prom_panels("http://p", "checkout-svc")
    assert len(out) == 5
    assert len(calls) == 5
