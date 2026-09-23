
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.slack import SlackResult, post_incident


def make_settings(
    *,
    has_slack=True,
    webhook_url="https://hooks.slack.test/services/test",
):
    return SimpleNamespace(
        has_slack=has_slack,
        slack_webhook_url=webhook_url,
    )


@pytest.mark.anyio
async def test_post_incident_uses_console_fallback_without_webhook():
    settings = make_settings(has_slack=False)

    with patch(
        "backend.services.slack.get_settings",
        return_value=settings,
    ):
        result = await post_incident(
            "Incident details",
            summary="Database incident",
        )

    assert isinstance(result, SlackResult)
    assert result.delivered is False
    assert result.channel == "console"
    assert "SLACK_WEBHOOK_URL not set" in result.detail

    assert result.payload == {
        "text": "Database incident",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Incident details",
                },
            }
        ],
    }


@pytest.mark.anyio
async def test_post_incident_successfully_delivers_to_slack():
    settings = make_settings()

    response = MagicMock()
    response.status_code = 200
    response.text = "ok"

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)

    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_context.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "backend.services.slack.get_settings",
        return_value=settings,
    ), patch(
        "backend.services.slack.httpx.AsyncClient",
        return_value=mock_client_context,
    ) as client_cls:
        result = await post_incident(
            "CPU usage is high",
            summary="High CPU incident",
        )

    assert result.delivered is True
    assert result.channel == "slack"
    assert result.detail == "Slack responded 200"

    mock_client.post.assert_awaited_once_with(
        settings.slack_webhook_url,
        json=result.payload,
    )

    client_cls.assert_called_once_with(timeout=10.0)


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [201, 204, 200, 299])
async def test_post_incident_accepts_any_2xx_response(status_code):
    settings = make_settings()

    response = MagicMock()
    response.status_code = status_code
    response.text = "success"

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)

    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_context.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "backend.services.slack.get_settings",
        return_value=settings,
    ), patch(
        "backend.services.slack.httpx.AsyncClient",
        return_value=mock_client_context,
    ):
        result = await post_incident(
            "Incident details",
            summary="Incident",
        )

    assert result.delivered is True
    assert result.channel == "slack"


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 500, 503])
async def test_post_incident_handles_non_2xx_response(status_code):
    settings = make_settings()

    response = MagicMock()
    response.status_code = status_code
    response.text = "Slack error response"

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)

    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_context.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "backend.services.slack.get_settings",
        return_value=settings,
    ), patch(
        "backend.services.slack.httpx.AsyncClient",
        return_value=mock_client_context,
    ):
        result = await post_incident(
            "Incident details",
            summary="Incident",
        )

    assert result.delivered is False
    assert result.channel == "error"
    assert str(status_code) in result.detail
    assert "Slack error response" in result.detail
    assert result.payload["text"] == "Incident"


@pytest.mark.anyio
async def test_post_incident_truncates_long_non_2xx_response():
    settings = make_settings()

    long_error = "X" * 300

    response = MagicMock()
    response.status_code = 400
    response.text = long_error

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)

    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_context.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "backend.services.slack.get_settings",
        return_value=settings,
    ), patch(
        "backend.services.slack.httpx.AsyncClient",
        return_value=mock_client_context,
    ):
        result = await post_incident(
            "Incident details",
            summary="Incident",
        )

    assert result.delivered is False
    assert len(result.detail) < 200
    assert "X" * 120 in result.detail


@pytest.mark.anyio
async def test_post_incident_handles_network_exception():
    settings = make_settings()

    mock_client = MagicMock()
    mock_client.post = AsyncMock(
        side_effect=TimeoutError("connection timed out")
    )

    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_context.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "backend.services.slack.get_settings",
        return_value=settings,
    ), patch(
        "backend.services.slack.httpx.AsyncClient",
        return_value=mock_client_context,
    ):
        result = await post_incident(
            "Incident details",
            summary="Incident",
        )

    assert result.delivered is False
    assert result.channel == "error"
    assert "TimeoutError" in result.detail
    assert "connection timed out" in result.detail


@pytest.mark.anyio
async def test_post_incident_preserves_blocks_text_and_summary():
    settings = make_settings()

    response = MagicMock()
    response.status_code = 200
    response.text = "ok"

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)

    mock_client_context = MagicMock()
    mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_context.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "backend.services.slack.get_settings",
        return_value=settings,
    ), patch(
        "backend.services.slack.httpx.AsyncClient",
        return_value=mock_client_context,
    ):
        result = await post_incident(
            "**SEV-2** database outage",
            summary="Database outage detected",
        )

    sent_payload = mock_client.post.await_args.kwargs["json"]

    assert sent_payload["text"] == "Database outage detected"
    assert (
        sent_payload["blocks"][0]["text"]["text"]
        == "**SEV-2** database outage"
    )
