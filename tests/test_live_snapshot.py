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


def test_error_rate_query_known_service():
    from backend.services.live_snapshot import _error_rate_query

    query = _error_rate_query("checkout-svc")

    assert 'service="checkout-svc"' in query
    assert 'code=~"5.."' in query
    assert "http_requests_total" in query
    assert "clamp_min" in query


def test_error_rate_query_unknown_service():
    from backend.services.live_snapshot import _error_rate_query

    query = _error_rate_query("unknown-svc")

    assert 'service="unknown-svc"' in query
    assert "http_requests_total" in query


def test_up_query_known_service():
    from backend.services.live_snapshot import _up_query

    query = _up_query("cart-svc")

    assert query == 'up{service="cart-svc"}'


def test_up_query_unknown_service_uses_fallback():
    from backend.services.live_snapshot import _up_query

    query = _up_query("unknown-svc")

    assert query == 'up{job=~"aegis-.*"}'


def test_query_range_success(monkeypatch):
    import backend.services.live_snapshot as live_snapshot

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "1"],
                                [1015, "0.5"],
                                [1030, "invalid"],
                                [1045, "2"],
                            ]
                        }
                    ]
                },
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def get(self, url, params):
            assert url == "http://prometheus:9090/api/v1/query_range"
            assert params["query"] == "up"
            assert params["step"] == "15s"
            return FakeResponse()

    monkeypatch.setattr(
        live_snapshot.httpx,
        "Client",
        lambda timeout: FakeClient(),
    )

    points = live_snapshot._query_range(
        "http://prometheus:9090",
        "up",
    )

    assert points == [
        (1000.0, 1.0),
        (1015.0, 0.5),
        (1045.0, 2.0),
    ]


def test_query_range_returns_empty_for_no_series(monkeypatch):
    import backend.services.live_snapshot as live_snapshot

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "success",
                "data": {
                    "result": [],
                },
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def get(self, url, params):
            return FakeResponse()

    monkeypatch.setattr(
        live_snapshot.httpx,
        "Client",
        lambda timeout: FakeClient(),
    )

    points = live_snapshot._query_range(
        "http://prometheus:9090/",
        "up",
    )

    assert points == []


def test_query_range_raises_for_unsuccessful_prometheus_response(monkeypatch):
    import backend.services.live_snapshot as live_snapshot

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "error",
                "error": "simulated failure",
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def get(self, url, params):
            return FakeResponse()

    monkeypatch.setattr(
        live_snapshot.httpx,
        "Client",
        lambda timeout: FakeClient(),
    )

    import pytest

    with pytest.raises(RuntimeError, match="prom query failed"):
        live_snapshot._query_range(
            "http://prometheus:9090",
            "up",
        )


def test_render_png_creates_local_file(tmp_path):
    from backend.services.live_snapshot import _render_png

    output = tmp_path / "snapshot.png"

    result = _render_png(
        service="checkout-svc",
        up_pts=[(1000.0, 1.0), (1015.0, 1.0)],
        err_pts=[(1000.0, 0.0), (1015.0, 2.5)],
        out=output,
    )

    assert result == output
    assert output.exists()
    assert output.stat().st_size > 0


def test_render_png_handles_empty_series(tmp_path):
    from backend.services.live_snapshot import _render_png

    output = tmp_path / "empty-snapshot.png"

    result = _render_png(
        service="checkout-svc",
        up_pts=[],
        err_pts=[],
        out=output,
    )

    assert result == output
    assert output.exists()
    assert output.stat().st_size > 0


def test_capture_live_snapshot_returns_none_without_prometheus(monkeypatch):
    import backend.services.live_snapshot as live_snapshot
    from backend.config import get_settings

    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    monkeypatch.delenv("GRAFANA_URL", raising=False)
    get_settings.cache_clear()

    result = live_snapshot.capture_live_snapshot("checkout-svc")

    assert result is None

    get_settings.cache_clear()


def test_capture_live_snapshot_success(monkeypatch, tmp_path):
    import backend.services.live_snapshot as live_snapshot

    class FakeSettings:
        prometheus_url = "http://prometheus:9090"
        grafana_url = "http://grafana:3000"

    monkeypatch.setattr(
        live_snapshot,
        "get_settings",
        lambda: FakeSettings(),
    )

    def fake_query_range(prom_url, query, minutes=60, step="15s"):
        if query == live_snapshot._up_query("checkout-svc"):
            return [(1000.0, 1.0)]
        return [(1000.0, 0.0)]

    rendered = {}

    def fake_render_png(service, up_pts, err_pts, out):
        rendered["service"] = service
        rendered["up_pts"] = up_pts
        rendered["err_pts"] = err_pts
        rendered["out"] = out
        return out

    monkeypatch.setattr(
        live_snapshot,
        "_query_range",
        fake_query_range,
    )
    monkeypatch.setattr(
        live_snapshot,
        "_render_png",
        fake_render_png,
    )
    monkeypatch.setattr(
        live_snapshot,
        "SEED_DIR",
        tmp_path / "backend" / "seed",
    )

    result = live_snapshot.capture_live_snapshot("checkout-svc")

    assert result is not None
    assert result["source"] == "prometheus"
    assert result["grafana_snapshot"].startswith("backend/seed/live/")
    assert result["grafana_explore_url"].startswith(
        "http://grafana:3000/explore"
    )
    assert rendered["service"] == "checkout-svc"
    assert rendered["up_pts"] == [(1000.0, 1.0)]
    assert rendered["err_pts"] == [(1000.0, 0.0)]


def test_capture_live_snapshot_falls_back_when_up_series_empty(monkeypatch):
    import backend.services.live_snapshot as live_snapshot

    class FakeSettings:
        prometheus_url = "http://prometheus:9090"
        grafana_url = ""

    monkeypatch.setattr(
        live_snapshot,
        "get_settings",
        lambda: FakeSettings(),
    )

    queries = []

    def fake_query_range(prom_url, query, minutes=60, step="15s"):
        queries.append(query)

        if len(queries) == 1:
            return []

        if query == live_snapshot._error_rate_query("checkout-svc"):
            return [(1000.0, 1.0)]

        return [(1000.0, 1.0)]

    monkeypatch.setattr(
        live_snapshot,
        "_query_range",
        fake_query_range,
    )
    monkeypatch.setattr(
        live_snapshot,
        "_render_png",
        lambda service, up_pts, err_pts, out: out,
    )

    result = live_snapshot.capture_live_snapshot("checkout-svc")

    assert result is not None
    assert len(queries) == 3
    assert queries[2] == 'up{job=~"aegis-.*"}'
    assert result["grafana_explore_url"] == ""


def test_capture_live_snapshot_returns_none_when_query_fails(monkeypatch):
    import backend.services.live_snapshot as live_snapshot

    class FakeSettings:
        prometheus_url = "http://prometheus:9090"
        grafana_url = ""

    monkeypatch.setattr(
        live_snapshot,
        "get_settings",
        lambda: FakeSettings(),
    )

    def failing_query_range(*args, **kwargs):
        raise RuntimeError("simulated Prometheus failure")

    monkeypatch.setattr(
        live_snapshot,
        "_query_range",
        failing_query_range,
    )

    result = live_snapshot.capture_live_snapshot("checkout-svc")

    assert result is None


def test_ensure_alert_snapshot_attaches_captured_snapshot(monkeypatch):
    import backend.services.live_snapshot as live_snapshot
    from backend.models import Alert

    captured = {
        "grafana_snapshot": "backend/seed/live/checkout.png",
        "grafana_explore_url": "http://grafana:3000/explore",
        "source": "prometheus",
    }

    monkeypatch.setattr(
        live_snapshot,
        "capture_live_snapshot",
        lambda service: captured,
    )

    alert = Alert(
        alert="HighErrorRate",
        service="checkout-svc",
    )

    result = live_snapshot.ensure_alert_snapshot(alert)

    assert result.grafana_snapshot == "backend/seed/live/checkout.png"
    assert result.metadata["grafana_explore_url"] == (
        "http://grafana:3000/explore"
    )
    assert result.metadata["snapshot_source"] == "prometheus"


def test_ensure_alert_snapshot_preserves_alert_when_capture_fails(monkeypatch):
    import backend.services.live_snapshot as live_snapshot
    from backend.models import Alert

    monkeypatch.setattr(
        live_snapshot,
        "capture_live_snapshot",
        lambda service: None,
    )

    alert = Alert(
        alert="HighErrorRate",
        service="checkout-svc",
    )

    result = live_snapshot.ensure_alert_snapshot(alert)

    assert result is alert
    assert result.grafana_snapshot is None
