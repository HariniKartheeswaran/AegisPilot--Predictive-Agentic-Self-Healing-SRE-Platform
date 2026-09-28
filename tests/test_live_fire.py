"""Unit tests for live Fire helpers (no cluster required)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services import live_fire as lf


def test_classify_log_level():
    assert lf._classify_log_level("request_failed status=500") == "ERROR"
    assert lf._classify_log_level("ERROR timeout on upstream") == "ERROR"
    assert lf._classify_log_level("WARN pool near capacity") == "WARN"
    assert lf._classify_log_level("ok processed") == "INFO"


def test_parse_loki_streams_classifies_and_limits():
    body = {
        "data": {
            "result": [
                {
                    "values": [
                        ["1700000000000000000", "ERROR boom"],
                        ["bad-ts", "WARN slow"],
                        ["1700000001000000000", "info ok"],
                    ]
                }
            ]
        }
    }
    lines = lf._parse_loki_streams("checkout-svc", body, limit=2)
    assert len(lines) == 2
    assert {ln.level for ln in lines} <= {"ERROR", "WARN", "INFO"}
    assert lines[0].service == "checkout-svc"


def test_loki_base_url_from_env(monkeypatch):
    monkeypatch.setenv("LOKI_URL", "http://loki:3100")
    from backend.config import get_settings

    get_settings.cache_clear()
    assert lf._loki_base_url() == "http://loki:3100"
    get_settings.cache_clear()


def test_loki_base_url_from_grafana(monkeypatch):
    monkeypatch.delenv("LOKI_URL", raising=False)
    monkeypatch.setenv("GRAFANA_URL", "http://prom.example:3000")
    from backend.config import get_settings

    get_settings.cache_clear()
    assert lf._loki_base_url() == "http://prom.example:3100"
    get_settings.cache_clear()


def test_env_val_reads_first_container():
    env = MagicMock(name="ERROR_RATE", value="0.4")
    env.name = "ERROR_RATE"
    env.value = "0.4"
    c = MagicMock()
    c.env = [env]
    assert lf._env_val([c], "ERROR_RATE", "0") == "0.4"
    assert lf._env_val([], "ERROR_RATE", "0") == "0"
    assert lf._env_val([c], "MISSING", "x") == "x"


def test_work_urls_include_nodeport(monkeypatch):
    monkeypatch.setenv("K8S_NAMESPACE", "demo")
    from backend.config import get_settings

    get_settings.cache_clear()
    urls = lf._work_urls("checkout-svc")
    assert any(":30081/" in u for u in urls)
    assert any("checkout-svc.demo.svc" in u for u in urls)
    get_settings.cache_clear()


def test_generate_load_no_url(monkeypatch):
    monkeypatch.setattr(lf, "_pick_work_url", lambda _s: None)
    assert lf._generate_load("checkout-svc", bursts=2) == {"ok": 0, "err": 0, "total": 0}


def test_generate_load_counts_status(monkeypatch):
    class _Resp:
        def __init__(self, code):
            self.status_code = code

    class _Client:
        def __init__(self, *a, **k):
            self.n = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            self.n += 1
            return _Resp(500 if self.n == 1 else 200)

    monkeypatch.setattr(lf, "_pick_work_url", lambda _s: "http://svc/api/work")
    monkeypatch.setattr(lf.httpx, "Client", _Client)
    out = lf._generate_load("checkout-svc", bursts=2)
    assert out["err"] == 1
    assert out["ok"] == 1
    assert out["total"] == 2


def test_clear_live_inject_skips_unknown():
    lf.clear_live_inject("not-a-scrape-svc")  # no raise


def test_live_mode_enabled(monkeypatch):
    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090")
    from backend.config import get_settings

    get_settings.cache_clear()
    assert lf.live_mode_enabled() is True
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    get_settings.cache_clear()
    assert lf.live_mode_enabled() is False
    get_settings.cache_clear()


def test_fetch_live_log_lines_prefers_loki(monkeypatch):
    fake = [MagicMock()]
    monkeypatch.setattr(lf, "_logs_from_loki", lambda s, limit=40: fake)
    monkeypatch.setattr(
        lf, "_logs_from_k8s", lambda s, limit=40: (_ for _ in ()).throw(AssertionError())
    )
    assert lf.fetch_live_log_lines("checkout-svc") is fake


def test_ingest_live_logs(monkeypatch):
    storage = MagicMock()
    lines = [MagicMock(), MagicMock()]
    monkeypatch.setattr(lf, "fetch_live_log_lines", lambda s: lines)
    assert lf.ingest_live_logs(storage, "checkout-svc") == 2
    assert storage.add_log.call_count == 2
