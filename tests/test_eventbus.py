import asyncio

import pytest

from backend.models import Alert
from backend.services.eventbus import InProcessBus


def make_alert() -> Alert:
    return Alert(
        alert="HighErrorRate",
        service="checkout",
        error_rate="25%",
        metadata={"source": "test"},
    )


def test_subscribe_registers_handler():
    bus = InProcessBus()

    async def handler(alert: Alert):
        pass

    bus.subscribe(handler)

    assert bus._subscribers == [handler]


@pytest.mark.anyio
async def test_publish_delivers_alert_to_subscriber():
    bus = InProcessBus()

    received = []

    async def handler(alert: Alert):
        received.append(alert)

    bus.subscribe(handler)

    alert = make_alert()

    await bus.publish(alert)

    # publish() uses create_task(), so allow the scheduled task to run.
    await asyncio.sleep(0.01)

    assert received == [alert]


@pytest.mark.anyio
async def test_publish_delivers_to_multiple_subscribers():
    bus = InProcessBus()

    received_by_first = []
    received_by_second = []

    async def first_handler(alert: Alert):
        received_by_first.append(alert)

    async def second_handler(alert: Alert):
        received_by_second.append(alert)

    bus.subscribe(first_handler)
    bus.subscribe(second_handler)

    alert = make_alert()

    await bus.publish(alert)

    await asyncio.sleep(0.01)

    assert received_by_first == [alert]
    assert received_by_second == [alert]


@pytest.mark.anyio
async def test_publish_with_no_subscribers_completes_successfully():
    bus = InProcessBus()

    await bus.publish(make_alert())

    # No subscribers should not cause an exception.
    assert bus._subscribers == []


@pytest.mark.anyio
async def test_failing_subscriber_does_not_stop_other_subscribers():
    bus = InProcessBus()

    received = []

    async def failing_handler(alert: Alert):
        raise RuntimeError("subscriber failure")

    async def working_handler(alert: Alert):
        received.append(alert)

    bus.subscribe(failing_handler)
    bus.subscribe(working_handler)

    alert = make_alert()

    await bus.publish(alert)

    await asyncio.sleep(0.01)

    # The failing subscriber must not prevent the other subscriber
    # from receiving the alert.
    assert received == [alert]


@pytest.mark.anyio
async def test_safe_deliver_logs_subscriber_exception(caplog):
    bus = InProcessBus()

    async def failing_handler(alert: Alert):
        raise RuntimeError("test subscriber failure")

    alert = make_alert()

    with caplog.at_level("ERROR", logger="aegisops.eventbus"):
        await bus._safe_deliver(failing_handler, alert)

    assert "subscriber failed: test subscriber failure" in caplog.text


@pytest.mark.anyio
async def test_safe_deliver_passes_alert_to_handler():
    bus = InProcessBus()

    received = []

    async def handler(alert: Alert):
        received.append(alert)

    alert = make_alert()

    await bus._safe_deliver(handler, alert)

    assert received == [alert]