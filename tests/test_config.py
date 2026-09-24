"""Unit tests for backend configuration."""

from pathlib import Path

from backend.config import Settings


def test_default_settings():
    settings = Settings(
        _env_file=None,
        gemini_api_key="",
        google_genai_use_vertexai=False,
    )

    assert settings.backend == "local"
    assert settings.orchestrator == "local"
    assert settings.pubsub_mode == "pull"
    assert settings.remediation_mode == "simulate"
    assert settings.k8s_namespace == "aegispilot"
    assert settings.port == 8080


def test_db_path_returns_absolute_path():
    settings = Settings(
        _env_file=None,
        aegis_db_path="test.db",
    )

    assert settings.db_path == Path(settings.db_path).resolve()
    assert settings.db_path.name == "test.db"


def test_db_path_preserves_absolute_path(tmp_path):
    db_file = tmp_path / "custom.db"

    settings = Settings(
        _env_file=None,
        aegis_db_path=str(db_file),
    )

    assert settings.db_path == db_file


def test_authentication_properties():
    no_auth = Settings(
        _env_file=None,
        gemini_api_key="",
        google_genai_use_vertexai=False,
    )

    assert no_auth.has_gemini_key is False
    assert no_auth.use_vertex is False
    assert no_auth.can_call_model is False

    api_key_auth = Settings(
        _env_file=None,
        gemini_api_key="valid-test-key",
        google_genai_use_vertexai=False,
    )

    assert api_key_auth.has_gemini_key is True
    assert api_key_auth.can_call_model is True

    vertex_auth = Settings(
        _env_file=None,
        gemini_api_key="",
        google_genai_use_vertexai=True,
    )

    assert vertex_auth.use_vertex is True
    assert vertex_auth.can_call_model is True


def test_slack_and_prometheus_properties():
    settings = Settings(
        _env_file=None,
        slack_webhook_url="  https://example.com/slack  ",
        prometheus_url="  http://prometheus:9090  ",
    )

    assert settings.has_slack is True
    assert settings.has_prometheus is True


def test_empty_slack_and_prometheus_properties():
    settings = Settings(
        _env_file=None,
        slack_webhook_url="   ",
        prometheus_url="   ",
    )

    assert settings.has_slack is False
    assert settings.has_prometheus is False


def test_apply_google_env_exports_configuration(monkeypatch):
    settings = Settings(
        _env_file=None,
        google_genai_use_vertexai=True,
        google_cloud_project="test-project",
        vertex_location="global",
        remediation_mode="kubernetes",
        k8s_namespace="aegispilot",
        k8s_remediate_deployments="checkout-svc,cart-svc",
        k8s_active_slot="blue",
        prometheus_url="http://prometheus:9090",
        grafana_url="http://grafana:3000",
    )

    settings.apply_google_env()

    assert settings.google_genai_use_vertexai is True
    assert settings.remediation_mode == "kubernetes"

    import os

    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "test-project"
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global"
    assert os.environ["REMEDIATION_MODE"] == "kubernetes"
    assert os.environ["K8S_NAMESPACE"] == "aegispilot"
    assert os.environ["K8S_REMEDIATE_DEPLOYMENTS"] == "checkout-svc,cart-svc"
    assert os.environ["K8S_ACTIVE_SLOT"] == "blue"
    assert os.environ["PROMETHEUS_URL"] == "http://prometheus:9090"
    assert os.environ["GRAFANA_URL"] == "http://grafana:3000"
