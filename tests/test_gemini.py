
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services.gemini import (
    GenResult,
    GeminiService,
    _estimate_tokens,
    _extract_json,
    _is_retriable,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_service():
    service = GeminiService()
    service._settings = SimpleNamespace(
        gemini_model="gemini-test-model",
        gemini_max_retries=3,
        gemini_base_delay=0,
        use_vertex=False,
        has_gemini_key=True,
        gemini_api_key="test-key",
        google_cloud_project="test-project",
        vertex_location="global",
    )
    return service


# ---------------------------------------------------------------------------
# GenResult
# ---------------------------------------------------------------------------

def test_genresult_json_parses_plain_json():
    result = GenResult(
        text='{"status": "ok"}',
        tokens=5,
        latency_ms=10,
        model="gemini-test",
    )

    assert result.json() == {"status": "ok"}


def test_genresult_json_parses_json_code_fence():
    result = GenResult(
        text='```json\n{"status": "ok"}\n```',
        tokens=5,
        latency_ms=10,
        model="gemini-test",
    )

    assert result.json() == {"status": "ok"}


def test_genresult_json_extracts_json_from_prose():
    result = GenResult(
        text='Here is the result:\n{"status": "ok"}\nDone.',
        tokens=5,
        latency_ms=10,
        model="gemini-test",
    )

    assert result.json() == {"status": "ok"}


def test_genresult_json_raises_for_invalid_json():
    result = GenResult(
        text="not valid json",
        tokens=5,
        latency_ms=10,
        model="gemini-test",
    )

    with pytest.raises(Exception):
        result.json()


# ---------------------------------------------------------------------------
# JSON helper
# ---------------------------------------------------------------------------

def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_code_fence():
    assert _extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_extract_json_embedded_object():
    assert _extract_json("Result: {\"a\": 1}") == {"a": 1}


# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    [
        "503 Service Unavailable",
        "429 Too Many Requests",
        "502 Bad Gateway",
        "resource exhausted",
        "rate limit exceeded",
        "deadline exceeded",
        "request timeout",
        "temporarily unavailable",
        "model overloaded",
    ],
)
def test_is_retriable_detects_transient_errors(message):
    assert _is_retriable(Exception(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "400 Bad Request",
        "403 Permission Denied",
        "invalid argument",
        "authentication failed",
    ],
)
def test_is_retriable_rejects_non_transient_errors(message):
    assert _is_retriable(Exception(message)) is False


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def test_estimate_tokens_uses_real_usage_metadata():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(total_token_count=123)
    )

    assert _estimate_tokens(response, "hello") == 123


def test_estimate_tokens_falls_back_to_text_length():
    response = SimpleNamespace(usage_metadata=None)

    assert _estimate_tokens(response, "a" * 20) == 5


def test_estimate_tokens_never_returns_zero():
    response = SimpleNamespace(usage_metadata=None)

    assert _estimate_tokens(response, "") == 1


# ---------------------------------------------------------------------------
# Client creation
# ---------------------------------------------------------------------------

def test_get_client_uses_gemini_api_key():
    service = make_service()

    fake_client = MagicMock()

    with patch(
        "backend.services.gemini.genai.Client",
        return_value=fake_client,
    ) as client_cls:
        client = service._get_client()

    assert client is fake_client
    client_cls.assert_called_once_with(api_key="test-key")


def test_get_client_uses_vertex_ai_configuration():
    service = make_service()
    service._settings.use_vertex = True
    service._settings.has_gemini_key = False

    fake_client = MagicMock()

    with patch(
        "backend.services.gemini.genai.Client",
        return_value=fake_client,
    ) as client_cls:
        client = service._get_client()

    assert client is fake_client
    client_cls.assert_called_once_with(
        vertexai=True,
        project="test-project",
        location="global",
    )


def test_get_client_raises_when_no_auth_configured():
    service = make_service()
    service._settings.use_vertex = False
    service._settings.has_gemini_key = False

    with pytest.raises(RuntimeError, match="No Gemini auth configured"):
        service._get_client()


def test_get_client_reuses_existing_client():
    service = make_service()
    existing_client = MagicMock()
    service._client = existing_client

    with patch("backend.services.gemini.genai.Client") as client_cls:
        assert service._get_client() is existing_client

    client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Model property
# ---------------------------------------------------------------------------

def test_model_returns_configured_model():
    service = make_service()

    assert service.model == "gemini-test-model"


# ---------------------------------------------------------------------------
# Synchronous generation
# ---------------------------------------------------------------------------

def test_generate_sync_success():
    service = make_service()

    response = SimpleNamespace(
        text='{"result": "success"}',
        usage_metadata=SimpleNamespace(total_token_count=20),
    )

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = response
    service._client = fake_client

    result = service._generate_sync(
        "hello",
        system="You are a test assistant",
        temperature=0.5,
        response_json=True,
    )

    assert result.text == '{"result": "success"}'
    assert result.tokens == 20
    assert result.model == "gemini-test-model"
    assert result.attempts == 1
    assert result.latency_ms >= 0

    fake_client.models.generate_content.assert_called_once()

    call = fake_client.models.generate_content.call_args

    assert call.kwargs["model"] == "gemini-test-model"
    assert call.kwargs["contents"] == "hello"


def test_generate_sync_supports_model_override():
    service = make_service()

    response = SimpleNamespace(
        text="RCA result",
        usage_metadata=SimpleNamespace(total_token_count=10),
    )

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = response
    service._client = fake_client

    result = service._generate_sync(
        "diagnose this",
        system=None,
        temperature=0.2,
        response_json=False,
        model="gemini-pro-override",
    )

    assert result.model == "gemini-pro-override"

    fake_client.models.generate_content.assert_called_once()

    call = fake_client.models.generate_content.call_args
    assert call.kwargs["model"] == "gemini-pro-override"


def test_generate_sync_retries_transient_failure():
    service = make_service()

    response = SimpleNamespace(
        text="success",
        usage_metadata=SimpleNamespace(total_token_count=5),
    )

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [
        Exception("503 Service Unavailable"),
        Exception("429 Too Many Requests"),
        response,
    ]

    service._client = fake_client

    with patch("backend.services.gemini.random.random", return_value=0):
        result = service._generate_sync(
            "hello",
            system=None,
            temperature=0.2,
            response_json=False,
        )

    assert result.text == "success"
    assert result.attempts == 3
    assert fake_client.models.generate_content.call_count == 3


def test_generate_sync_does_not_retry_non_transient_failure():
    service = make_service()

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = Exception(
        "403 Permission Denied"
    )

    service._client = fake_client

    with pytest.raises(Exception, match="403 Permission Denied"):
        service._generate_sync(
            "hello",
            system=None,
            temperature=0.2,
            response_json=False,
        )

    fake_client.models.generate_content.assert_called_once()


def test_generate_sync_raises_after_retry_limit():
    service = make_service()
    service._settings.gemini_max_retries = 3

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = Exception(
        "503 Service Unavailable"
    )

    service._client = fake_client

    with patch("backend.services.gemini.random.random", return_value=0):
        with pytest.raises(Exception, match="503 Service Unavailable"):
            service._generate_sync(
                "hello",
                system=None,
                temperature=0.2,
                response_json=False,
            )

    assert fake_client.models.generate_content.call_count == 3


# ---------------------------------------------------------------------------
# Async public API
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_generate_runs_sync_generation():
    service = make_service()

    expected = GenResult(
        text="hello",
        tokens=3,
        latency_ms=10,
        model="gemini-test-model",
    )

    with patch.object(
        service,
        "_generate_sync",
        return_value=expected,
    ) as generate_sync:
        result = await service.generate(
            "hello",
            system="system",
            temperature=0.4,
            response_json=True,
        )

    assert result is expected
    generate_sync.assert_called_once_with(
        "hello",
        "system",
        0.4,
        True,
        None,
    )


@pytest.mark.anyio
async def test_generate_json_returns_parsed_json_and_result():
    service = make_service()

    expected = GenResult(
        text='{"answer": 42}',
        tokens=5,
        latency_ms=10,
        model="gemini-test-model",
    )

    with patch.object(
        service,
        "generate",
        return_value=expected,
    ) as generate:
        data, result = await service.generate_json(
            "give me json",
            system="return json",
            temperature=0.2,
        )

    assert data == {"answer": 42}
    assert result is expected

    generate.assert_called_once_with(
        "give me json",
        system="return json",
        temperature=0.2,
        response_json=True,
    )


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_generate_vision_raises_for_missing_image():
    service = make_service()

    with pytest.raises(FileNotFoundError, match="Grafana snapshot not found"):
        await service.generate_vision(
            "analyze this screenshot",
            "does-not-exist.png",
        )


@pytest.mark.anyio
async def test_generate_vision_builds_image_part(tmp_path):
    service = make_service()

    image_path = tmp_path / "grafana.png"
    image_path.write_bytes(b"fake-image-data")

    expected = GenResult(
        text="analysis",
        tokens=5,
        latency_ms=10,
        model="gemini-test-model",
    )

    fake_part = MagicMock()

    with patch(
        "backend.services.gemini.types.Part.from_bytes",
        return_value=fake_part,
    ) as from_bytes, patch.object(
        service,
        "_generate_sync",
        return_value=expected,
    ) as generate_sync:
        result = await service.generate_vision(
            "analyze screenshot",
            str(image_path),
            system="vision system",
            temperature=0.4,
            response_json=True,
        )

    assert result is expected

    from_bytes.assert_called_once_with(
        data=b"fake-image-data",
        mime_type="image/png",
    )

    args = generate_sync.call_args.args

    assert args[0][0] == "analyze screenshot"
    assert args[0][1] is fake_part
    assert args[1] == "vision system"
    assert args[2] == 0.4
    assert args[3] is True
