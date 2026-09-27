"""Tests for Alertmanager webhook ingest + mandatory pre-alerts."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import alertmanager_ingest as ami


@pytest.fixture(autouse=True)
def _clear_pre_alerts():
    ami.reset_pre_alerts_for_tests()
    yield
    ami.reset_pre_alerts_for_tests()


def test_pct_from_ratio_and_percent():
    assert ami._pct_from_value("0.12") == "12.0%"
    assert ami._pct_from_value("42%") == "42%"
    assert ami._pct_from_value(None) == "n/a"


def test_is_pre_alert_by_stage_and_severity():
    assert ami._is_pre_alert({"stage": "pre", "severity": "warning"}) is True
    assert ami._is_pre_alert({"severity": "warning"}) is True
    assert ami._is_pre_alert({"stage": "full", "severity": "critical"}) is False
    assert ami._is_pre_alert({"severity": "critical"}) is False


def test_alert_from_am_maps_labels():
    alert = ami.alert_from_am(
        {
            "labels": {
                "alertname": "HighErrorRate",
                "service": "checkout-svc",
                "severity": "critical",
                "stage": "full",
            },
            "annotations": {"value": "0.42", "summary": "High 5xx"},
            "fingerprint": "abc",
            "status": "firing",
            "generatorURL": "http://prom/graph",
        }
    )
    assert alert.service == "checkout-svc"
    assert alert.alert == "HighErrorRate"
    assert alert.error_rate == "42.0%"
    assert alert.metadata["source"] == "alertmanager"
    assert alert.metadata["stage"] == "full"


@pytest.mark.asyncio
async def test_process_webhook_pre_alert_only():
    bus = AsyncMock()
    hub = AsyncMock()
    with patch("backend.services.slack.post_incident", new_callable=AsyncMock) as slack:
        slack.return_value = None
        out = await ami.process_webhook(
            {
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "HighErrorRateWarning",
                            "service": "checkout-svc",
                            "severity": "warning",
                            "stage": "pre",
                        },
                        "annotations": {
                            "summary": "Elevated 5xx",
                            "value": "0.12",
                        },
                        "fingerprint": "fp-warn-1",
                    }
                ],
            },
            bus=bus,
            hub=hub,
        )
    assert out["pre_alerts"] == 1
    assert out["incidents"] == 0
    bus.publish.assert_not_called()
    hub.publish.assert_called()
    rows = ami.list_pre_alerts()
    assert len(rows) == 1
    assert rows[0]["service"] == "checkout-svc"
    assert rows[0]["error_rate"] == "12.0%"


@pytest.mark.asyncio
async def test_process_webhook_critical_publishes_and_clears_pre():
    bus = AsyncMock()
    hub = AsyncMock()
    with patch("backend.services.slack.post_incident", new_callable=AsyncMock):
        await ami.process_webhook(
            {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "HighErrorRateWarning",
                            "service": "checkout-svc",
                            "severity": "warning",
                            "stage": "pre",
                        },
                        "annotations": {"summary": "warn", "value": "0.1"},
                        "fingerprint": "fp-w",
                    }
                ],
            },
            bus=bus,
            hub=hub,
        )
        assert ami.list_pre_alerts()

        out = await ami.process_webhook(
            {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "HighErrorRate",
                            "service": "checkout-svc",
                            "severity": "critical",
                            "stage": "full",
                        },
                        "annotations": {"summary": "crit", "value": "0.35"},
                        "fingerprint": "fp-c",
                    }
                ],
            },
            bus=bus,
            hub=hub,
        )
    assert out["incidents"] == 1
    assert ami.list_pre_alerts() == []
    bus.publish.assert_called_once()
    published = bus.publish.await_args.args[0]
    assert published.service == "checkout-svc"
    assert published.alert == "HighErrorRate"


@pytest.mark.asyncio
async def test_ack_pre_alert():
    bus = AsyncMock()
    hub = AsyncMock()
    with patch("backend.services.slack.post_incident", new_callable=AsyncMock):
        await ami.process_webhook(
            {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "HighErrorRateWarning",
                            "service": "cart-svc",
                            "severity": "warning",
                        },
                        "annotations": {"summary": "warn", "value": "0.09"},
                        "fingerprint": "fp-ack",
                    }
                ],
            },
            bus=bus,
            hub=hub,
        )
    row = ami.list_pre_alerts()[0]
    acked = ami.ack_pre_alert(row["id"])
    assert acked and acked["acked"] is True
    assert ami.list_pre_alerts() == []
