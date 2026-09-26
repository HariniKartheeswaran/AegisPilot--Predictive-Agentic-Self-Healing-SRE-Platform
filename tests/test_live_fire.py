"""Tests for live Fire (docker + kubernetes modes) — raise coverage on live_fire."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.models import Alert, LogLine
from backend.services import live_fire
from backend.services.storage import SQLiteStorage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DB_PATH", str(tmp_path / "live_fire_test.db"))
    from backend.config import get_settings

    get_settings.cache_clear()
    s = SQLiteStorage(tmp_path / "live_fire_test.db")
    s.init_schema()
    return s


def test_live_mode_requires_prometheus_and_mode(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("PROMETHEUS_URL", "")
    get_settings.cache_clear()
    assert live_fire.live_mode_enabled() is False

    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")
    get_settings.cache_clear()
    assert live_fire.live_mode_enabled() is True
    assert live_fire.docker_mode() is True

    monkeypatch.setenv("REMEDIATION_MODE", "simulate")
    get_settings.cache_clear()
    assert live_fire.live_mode_enabled() is False


def test_live_mode_kubernetes(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090")
    get_settings.cache_clear()
    assert live_fire.live_mode_enabled() is True
    assert live_fire.docker_mode() is False


def test_scrape_base_defaults_and_overrides(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.delenv("SCRAPE_URL_CHECKOUT", raising=False)
    get_settings.cache_clear()
    assert live_fire._scrape_base("checkout-svc") == "http://checkout-svc:8080"

    monkeypatch.setenv("SCRAPE_URL_CHECKOUT", "http://localhost:8081/")
    get_settings.cache_clear()
    assert live_fire._scrape_base("checkout-svc") == "http://localhost:8081"


def test_prepare_docker_fire_ingests_deploy_and_alert(storage, monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")
    monkeypatch.setenv("SCRAPE_URL_CHECKOUT", "http://checkout:8080")
    get_settings.cache_clear()

    with (
        patch.object(
            live_fire,
            "_get_docker_fault",
            return_value={"service_version": "v1.0.0", "error_rate": 0.0},
        ),
        patch.object(live_fire, "_set_docker_fault", return_value={}) as set_fault,
        patch.object(
            live_fire,
            "_generate_load",
            side_effect=[
                {"ok": 40, "err": 20, "total": 60},
                {"ok": 20, "err": 10, "total": 30},
            ],
        ),
        patch.object(live_fire, "ingest_live_logs", return_value=3),
        patch.object(live_fire, "time") as mock_time,
    ):
        mock_time.time.return_value = 1_700_000_000
        mock_time.sleep = MagicMock()
        prepared = live_fire.prepare_live_fire(storage, "checkout-svc", 0.42)

    assert prepared["scenario"] == "live"
    assert prepared["service"] == "checkout-svc"
    alert = prepared["alert"]
    assert isinstance(alert, Alert)
    assert alert.alert == "HighErrorRate"
    assert alert.metadata["live_mode"] == "docker"
    assert alert.metadata["live_fire"] is True
    assert set_fault.called
    deploys = storage.deploys_for_service("checkout-svc")
    assert len(deploys) >= 1
    assert deploys[0].rollback_target == "v1.0.0"


def test_prepare_docker_fire_unknown_service_defaults_checkout(storage, monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")
    get_settings.cache_clear()

    with (
        patch.object(live_fire, "_get_docker_fault", return_value={"service_version": "v1.0.0"}),
        patch.object(live_fire, "_set_docker_fault"),
        patch.object(
            live_fire,
            "_generate_load",
            return_value={"ok": 1, "err": 0, "total": 1},
        ),
        patch.object(live_fire, "ingest_live_logs", return_value=0),
        patch.object(live_fire, "time") as mock_time,
    ):
        mock_time.sleep = MagicMock()
        mock_time.time.return_value = 100
        prepared = live_fire.prepare_live_fire(storage, "unknown-svc", 0.5)

    assert prepared["service"] == "checkout-svc"


def test_fetch_live_logs_prefers_scrape_admin_in_docker(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")
    get_settings.cache_clear()

    rows = [
        {"ts": 1, "level": "ERROR", "message": "request_failed path=/api/work"},
        {"ts": 2, "level": "INFO", "message": "request_ok"},
    ]
    with (
        patch.object(live_fire, "_logs_from_loki", return_value=[]),
        patch.object(live_fire, "_logs_from_scrape_admin", return_value=[
            LogLine(id="1", service="checkout-svc", ts=1, level="ERROR", message="fail"),
        ]) as admin,
        patch.object(live_fire, "_logs_from_k8s", return_value=[]) as k8s,
    ):
        lines = live_fire.fetch_live_log_lines("checkout-svc", limit=10)

    assert len(lines) == 1
    assert admin.called
    assert not k8s.called


def test_logs_from_scrape_admin_http(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    get_settings.cache_clear()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {"ts": 10, "level": "WARN", "message": "fault_updated"},
    ]
    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_resp

    with patch("backend.services.live_fire.httpx.Client", return_value=mock_client):
        lines = live_fire._logs_from_scrape_admin("checkout-svc", limit=5)

    assert len(lines) == 1
    assert lines[0].level == "WARN"


def test_generate_load_docker_counts_5xx(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("SCRAPE_URL_CHECKOUT", "http://checkout:8080")
    get_settings.cache_clear()

    ok = MagicMock(status_code=200)
    err = MagicMock(status_code=500)
    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = [ok, err, ok]

    with patch("backend.services.live_fire.httpx.Client", return_value=mock_client):
        stats = live_fire._generate_load("checkout-svc", bursts=3)

    assert stats["total"] == 3
    assert stats["ok"] == 2
    assert stats["err"] == 1


def test_build_live_alert_wrapper(storage, monkeypatch):
    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")
    from backend.config import get_settings

    get_settings.cache_clear()

    fake = {"alert": Alert(alert="HighErrorRate", service="checkout-svc", error_rate="40%")}
    with patch.object(live_fire, "prepare_live_fire", return_value=fake):
        alert = live_fire.build_live_alert(storage)
    assert alert.service == "checkout-svc"


def test_env_val_reads_container_env():
    env = SimpleNamespace(name="SERVICE_VERSION", value="v9")
    container = SimpleNamespace(env=[env])
    assert live_fire._env_val([container], "SERVICE_VERSION", "v0") == "v9"
    assert live_fire._env_val([], "SERVICE_VERSION", "v0") == "v0"
    assert live_fire._env_val([SimpleNamespace(env=[])], "X", "d") == "d"


def test_set_and_get_docker_fault_http(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("SCRAPE_URL_CHECKOUT", "http://checkout:8080")
    get_settings.cache_clear()

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"error_rate": 0.4, "service_version": "v-live-1"}
    client = MagicMock()
    client.__enter__.return_value.post.return_value = resp
    client.__enter__.return_value.get.return_value = resp

    with patch("backend.services.live_fire.httpx.Client", return_value=client):
        out = live_fire._set_docker_fault(
            "checkout-svc", error_rate=0.4, service_version="v-live-1"
        )
        got = live_fire._get_docker_fault("checkout-svc")
    assert out["error_rate"] == 0.4
    assert got["service_version"] == "v-live-1"


def test_logs_from_loki_parses_streams(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("LOKI_URL", "http://loki:3100")
    get_settings.cache_clear()

    body = {
        "data": {
            "result": [
                {
                    "values": [
                        ["1700000000000000000", "request_failed path=/api/work"],
                        ["1700000001000000000", "request_ok"],
                    ]
                }
            ]
        }
    }
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = body
    client = MagicMock()
    client.__enter__.return_value.get.return_value = resp

    with patch("backend.services.live_fire.httpx.Client", return_value=client):
        lines = live_fire._logs_from_loki("checkout-svc", limit=10)
    assert len(lines) == 2
    assert lines[0].level == "ERROR"


def test_logs_from_loki_empty_without_url(monkeypatch):
    monkeypatch.delenv("LOKI_URL", raising=False)
    with patch.object(
        live_fire,
        "get_settings",
        return_value=SimpleNamespace(loki_url="", grafana_url=""),
    ):
        assert live_fire._logs_from_loki("checkout-svc") == []


def test_logs_from_loki_derives_base_from_grafana(monkeypatch):
    monkeypatch.delenv("LOKI_URL", raising=False)
    body = {"data": {"result": []}}
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = body
    client = MagicMock()
    client.__enter__.return_value.get.return_value = resp

    with (
        patch.object(
            live_fire,
            "get_settings",
            return_value=SimpleNamespace(loki_url="", grafana_url="http://grafana:3000"),
        ),
        patch("backend.services.live_fire.httpx.Client", return_value=client),
    ):
        assert live_fire._logs_from_loki("checkout-svc") == []
    get_url = client.__enter__.return_value.get.call_args[0][0]
    assert get_url.startswith("http://grafana:3100/")


def test_logs_from_k8s_parses_pod_log(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("K8S_NAMESPACE", "aegispilot")
    get_settings.cache_clear()

    pod = SimpleNamespace(metadata=SimpleNamespace(name="checkout-abc"))
    core = MagicMock()
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])
    core.read_namespaced_pod_log.return_value = (
        "2026-09-24T10:00:00+00:00 request_failed path=/api/work\n"
        "2026-09-24T10:00:01+00:00 request_ok\n"
    )
    with patch.object(live_fire, "_load_apps", return_value=(MagicMock(), core)):
        lines = live_fire._logs_from_k8s("checkout-svc", limit=10)
    assert len(lines) == 2
    assert lines[0].level == "ERROR"


def test_logs_from_k8s_empty_on_error():
    with patch.object(live_fire, "_load_apps", side_effect=RuntimeError("no kube")):
        assert live_fire._logs_from_k8s("checkout-svc") == []


def test_ingest_live_logs(storage):
    fake = [
        LogLine(id="1", service="checkout-svc", ts=1, level="INFO", message="ok"),
        LogLine(id="2", service="checkout-svc", ts=2, level="ERROR", message="fail"),
    ]
    with patch.object(live_fire, "fetch_live_log_lines", return_value=fake):
        n = live_fire.ingest_live_logs(storage, "checkout-svc")
    assert n == 2


def test_fetch_prefers_loki(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    get_settings.cache_clear()
    loki_line = [
        LogLine(id="l", service="checkout-svc", ts=1, level="INFO", message="from-loki")
    ]
    with (
        patch.object(live_fire, "_logs_from_loki", return_value=loki_line),
        patch.object(live_fire, "_logs_from_k8s") as k8s,
    ):
        lines = live_fire.fetch_live_log_lines("checkout-svc")
    assert lines[0].message == "from-loki"
    assert not k8s.called


def test_fetch_falls_back_to_scrape_admin_in_docker(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    get_settings.cache_clear()
    admin = [
        LogLine(id="a", service="checkout-svc", ts=1, level="INFO", message="admin")
    ]
    with (
        patch.object(live_fire, "_logs_from_loki", return_value=[]),
        patch.object(live_fire, "_logs_from_scrape_admin", return_value=admin),
        patch.object(live_fire, "_logs_from_k8s") as k8s,
    ):
        lines = live_fire.fetch_live_log_lines("checkout-svc")
    assert lines[0].message == "admin"
    assert not k8s.called


def test_prepare_k8s_fire_mocked(storage, monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090")
    monkeypatch.setenv("K8S_NAMESPACE", "aegispilot")
    get_settings.cache_clear()

    env = SimpleNamespace(name="SERVICE_VERSION", value="v1.0.0")
    container = SimpleNamespace(env=[env])
    dep = SimpleNamespace(
        spec=SimpleNamespace(
            template=SimpleNamespace(spec=SimpleNamespace(containers=[container]))
        )
    )
    apps = MagicMock()
    apps.read_namespaced_deployment.return_value = dep

    with (
        patch.object(live_fire, "_load_apps", return_value=(apps, MagicMock())),
        patch.object(live_fire, "_patch_env") as patch_env,
        patch.object(live_fire, "_wait_ready"),
        patch.object(
            live_fire,
            "_generate_load",
            side_effect=[
                {"ok": 10, "err": 10, "total": 20},
                {"ok": 5, "err": 5, "total": 10},
            ],
        ),
        patch.object(live_fire, "ingest_live_logs", return_value=2),
        patch("backend.services.live_fire.time.sleep"),
        patch("backend.services.live_fire.time.time", return_value=1_700_000_042),
    ):
        prepared = live_fire.prepare_live_fire(storage, "checkout-svc", 0.5)

    assert prepared["alert"].metadata["live_mode"] == "kubernetes"
    assert patch_env.called
    assert prepared["load"]["total"] == 30


def test_prepare_docker_resets_v_live_rollback_target(storage, monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")
    get_settings.cache_clear()

    with (
        patch.object(
            live_fire,
            "_get_docker_fault",
            return_value={"service_version": "v-live-999", "error_rate": 0.5},
        ),
        patch.object(live_fire, "_set_docker_fault"),
        patch.object(
            live_fire,
            "_generate_load",
            return_value={"ok": 0, "err": 0, "total": 0},
        ),
        patch.object(live_fire, "ingest_live_logs", return_value=0),
        patch("backend.services.live_fire.time.sleep"),
        patch("backend.services.live_fire.time.time", return_value=42),
    ):
        prepared = live_fire.prepare_live_fire(storage, "checkout-svc", 0.3)

    assert prepared["alert"].metadata["rollback_target"] == "v1.0.0"


def test_scrape_admin_logs_on_http_error(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("SCRAPE_URL_CHECKOUT", "http://checkout:8080")
    get_settings.cache_clear()
    client = MagicMock()
    client.__enter__.return_value.get.side_effect = RuntimeError("down")
    with patch("backend.services.live_fire.httpx.Client", return_value=client):
        assert live_fire._logs_from_scrape_admin("checkout-svc") == []


def test_generate_load_kubernetes_tries_urls(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "kubernetes")
    monkeypatch.setenv("K8S_NAMESPACE", "aegispilot")
    get_settings.cache_clear()

    ok = MagicMock(status_code=200)
    mock_client = MagicMock()
    # First URL fails, second succeeds
    mock_client.__enter__.return_value.get.side_effect = [ConnectionError("no"), ok]

    with patch("backend.services.live_fire.httpx.Client", return_value=mock_client):
        stats = live_fire._generate_load("checkout-svc", bursts=1)
    assert stats["ok"] == 1
    assert stats["total"] == 1


def test_prepare_live_fire_unknown_service_defaults_checkout(storage, monkeypatch):
    from backend.config import get_settings

    monkeypatch.setenv("REMEDIATION_MODE", "docker")
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")
    get_settings.cache_clear()

    with (
        patch.object(
            live_fire,
            "_get_docker_fault",
            return_value={"service_version": "v1.0.0"},
        ),
        patch.object(live_fire, "_set_docker_fault"),
        patch.object(
            live_fire,
            "_generate_load",
            return_value={"ok": 1, "err": 1, "total": 2},
        ),
        patch.object(live_fire, "ingest_live_logs", return_value=0),
        patch("backend.services.live_fire.time.sleep"),
        patch("backend.services.live_fire.time.time", return_value=99),
    ):
        prepared = live_fire.prepare_live_fire(storage, "unknown-svc", 0.4)
    assert prepared["service"] == "checkout-svc"
    assert prepared["alert"].error_rate == "50%"
