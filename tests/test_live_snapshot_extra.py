"""Extra live_snapshot coverage — query/render/capture paths."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services import live_snapshot as ls


def test_sel_and_queries():
    assert 'service="checkout-svc"' in ls._sel("checkout-svc")
    assert "aegis" in ls._sel("aegis-warroom")
    assert "5.." in ls._error_rate_query("checkout-svc")
    assert 'service="checkout-svc"' in ls._up_query("checkout-svc")
    assert "aegis" in ls._up_query("other")


def test_query_range_averages_series():
    body = {
        "status": "success",
        "data": {
            "result": [
                {"values": [[1000.0, "1"], [1001.0, "3"]]},
                {"values": [[1000.0, "1"], [1001.0, "1"]]},
            ]
        },
    }
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = body
    client = MagicMock()
    client.__enter__.return_value.get.return_value = resp

    with patch("backend.services.live_snapshot.httpx.Client", return_value=client):
        pts = ls._query_range("http://prom:9090", "up", minutes=5)
    assert pts[0] == (1000.0, 1.0)
    assert pts[1] == (1001.0, 2.0)


def test_query_range_empty_and_failure():
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"status": "success", "data": {"result": []}}
    client = MagicMock()
    client.__enter__.return_value.get.return_value = resp
    with patch("backend.services.live_snapshot.httpx.Client", return_value=client):
        assert ls._query_range("http://prom:9090", "up") == []

    resp.json.return_value = {"status": "error"}
    with patch("backend.services.live_snapshot.httpx.Client", return_value=client):
        with pytest.raises(RuntimeError):
            ls._query_range("http://prom:9090", "up")


def test_render_dashboard_png(tmp_path):
    pts = [(1_700_000_000.0, 1.0), (1_700_000_015.0, 2.0)]
    out = tmp_path / "dash.png"
    path = ls._render_dashboard_png("checkout-svc", pts, pts, pts, pts, pts, out)
    assert path.exists()
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_empty_series_shows_placeholder(tmp_path):
    out = tmp_path / "empty.png"
    ls._render_dashboard_png("cart-svc", [], [], [], [], [], out)
    assert out.exists()


def test_try_grafana_render_success_and_reject():
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    ok = MagicMock(status_code=200, content=png)
    bad = MagicMock(status_code=500, content=b"nope")
    client = MagicMock()
    client.__enter__.return_value.get.side_effect = [ok, bad]

    with (
        patch("backend.services.live_snapshot.httpx.Client", return_value=client),
        patch.object(
            ls,
            "get_settings",
            return_value=MagicMock(grafana_dashboard_path="/d/ad6nckx/aegispilot-dashboard"),
        ),
    ):
        assert ls._try_grafana_render("checkout-svc", "http://g:3000", "tok") == png
        assert ls._try_grafana_render("checkout-svc", "http://g:3000", "tok") is None


def test_capture_live_snapshot_prometheus_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090")
    monkeypatch.setenv("GRAFANA_URL", "http://grafana:3000")
    monkeypatch.delenv("GRAFANA_TOKEN", raising=False)
    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(ls, "SNAP_DIR", tmp_path)

    pts = [(1_700_000_000.0, 1.0), (1_700_000_030.0, 2.0)]
    with patch.object(ls, "_query_range", return_value=pts):
        out = ls.capture_live_snapshot("checkout-svc")
    assert out is not None
    assert out["source"] == "prometheus"
    assert Path(out["grafana_snapshot"]).exists()
    assert out["grafana_snapshot_b64"]
    get_settings.cache_clear()


def test_capture_live_snapshot_grafana_render(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090")
    monkeypatch.setenv("GRAFANA_URL", "http://grafana:3000")
    monkeypatch.setenv("GRAFANA_TOKEN", "secret")
    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(ls, "SNAP_DIR", tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"rendered"

    with patch.object(ls, "_try_grafana_render", return_value=png):
        out = ls.capture_live_snapshot("checkout-svc")
    assert out["source"] == "grafana-render"
    assert Path(out["grafana_snapshot"]).read_bytes() == png
    get_settings.cache_clear()


def test_capture_returns_none_without_prom(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "")
    from backend.config import get_settings

    get_settings.cache_clear()
    assert ls.capture_live_snapshot("checkout-svc") is None
    get_settings.cache_clear()
