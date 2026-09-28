"""Settings / config property coverage."""
from __future__ import annotations

from backend.config import Settings, get_settings


def test_settings_auth_and_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "real-key")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
    monkeypatch.setenv("PROMETHEUS_URL", "http://p:9090")
    monkeypatch.setenv("AEGIS_DB_PATH", str(tmp_path / "x.db"))
    get_settings.cache_clear()
    s = get_settings()
    assert s.has_gemini_key is True
    assert s.use_vertex is False
    assert s.can_call_model is True
    assert s.has_slack is True
    assert s.has_prometheus is True
    assert s.db_path.is_absolute()
    get_settings.cache_clear()


def test_apply_google_env_vertex(monkeypatch):
    monkeypatch.delenv("GRAFANA_DASHBOARD_PATH", raising=False)
    s = Settings(
        google_genai_use_vertexai=True,
        google_cloud_project="demo-proj",
        vertex_location="global",
        remediation_mode="kubernetes",
        k8s_namespace="ns",
        k8s_active_slot="green",
        prometheus_url="http://prom",
        grafana_url="http://g",
        grafana_dashboard_path="/d/ad6nckx/aegispilot-dashboard",
        grafana_token="tok",
        loki_url="http://loki",
    )
    s.apply_google_env()
    import os

    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "demo-proj"
    assert os.environ["REMEDIATION_MODE"] == "kubernetes"
    assert os.environ["K8S_ACTIVE_SLOT"] == "green"
    assert os.environ["LOKI_URL"] == "http://loki"
    get_settings.cache_clear()


def test_paste_placeholder_key_rejected(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "paste_your_key_here")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    get_settings.cache_clear()
    s = get_settings()
    assert s.has_gemini_key is False
    assert s.can_call_model is False
    get_settings.cache_clear()
