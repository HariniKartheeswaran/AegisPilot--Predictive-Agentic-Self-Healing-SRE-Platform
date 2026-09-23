import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import AlreadyExists

from backend.models import Alert
from backend.services.pubsub_bus import PubSubBus


def make_alert() -> Alert:
    return Alert(
        alert="HighErrorRate",
        service="checkout",
        error_rate="25%",
        metadata={"source": "test"},
    )


def make_pubsub_bus():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    publisher_client_patch = patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    )
    subscriber_client_patch = patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    )

    return publisher, subscriber, publisher_client_patch, subscriber_client_patch


def test_pubsub_bus_initializes_with_mock_clients():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "projects/test/topics/incident-alerts"
    subscriber.subscription_path.return_value = (
        "projects/test/subscriptions/aegisops-worker"
    )

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")

    assert bus._project == "test-project"
    assert bus._topic_path == "projects/test/topics/incident-alerts"
    assert bus._sub_path == "projects/test/subscriptions/aegisops-worker"

    publisher.topic_path.assert_called_once_with(
        "test-project", "incident-alerts"
    )
    subscriber.subscription_path.assert_called_once_with(
        "test-project", "aegisops-worker"
    )


def test_pubsub_ensure_creates_topic_and_subscription():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")
        bus.ensure()

    publisher.create_topic.assert_called_once_with(name="topic-path")
    subscriber.create_subscription.assert_called_once_with(
        name="subscription-path",
        topic="topic-path",
        ack_deadline_seconds=60,
    )


def test_pubsub_ensure_can_skip_pull_subscription():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")
        bus.ensure(create_pull_subscription=False)

    publisher.create_topic.assert_called_once_with(name="topic-path")
    subscriber.create_subscription.assert_not_called()


def test_pubsub_ensure_ignores_existing_topic_and_subscription():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    publisher.create_topic.side_effect = AlreadyExists("topic already exists")
    subscriber.create_subscription.side_effect = AlreadyExists(
        "subscription already exists"
    )

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")

        # ensure() should treat already-existing resources as a normal
        # condition rather than failing the application.
        bus.ensure()

    publisher.create_topic.assert_called_once_with(name="topic-path")
    subscriber.create_subscription.assert_called_once_with(
        name="subscription-path",
        topic="topic-path",
        ack_deadline_seconds=60,
    )


@pytest.mark.anyio
async def test_pubsub_publish_sends_alert_as_json():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    future = MagicMock()
    future.result.return_value = "message-id"
    publisher.publish.return_value = future

    alert = make_alert()

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")
        await bus.publish(alert)

    publisher.publish.assert_called_once()

    args, kwargs = publisher.publish.call_args

    assert args[0] == "topic-path"
    assert json.loads(kwargs["data"].decode("utf-8")) == alert.model_dump()

    future.result.assert_called_once_with(15)


@pytest.mark.anyio
async def test_pubsub_subscribe_registers_handler():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    pull_future = MagicMock()
    subscriber.subscribe.return_value = pull_future

    async def handler(alert: Alert):
        pass

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")
        bus.subscribe(handler)

    assert bus._handler is handler
    assert bus._pull_future is pull_future

    subscriber.subscribe.assert_called_once_with(
        "subscription-path",
        callback=bus._on_message,
    )


@pytest.mark.anyio
async def test_pubsub_valid_message_is_delivered_to_handler():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    async def handler(alert: Alert):
        received_alerts.append(alert)

    received_alerts = []

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")
        bus._handler = handler
        bus._loop = asyncio.get_running_loop()

        message = MagicMock()
        message.data = make_alert().model_dump_json().encode("utf-8")

        bus._on_message(message)

        # _on_message schedules the async handler on the event loop.
        await asyncio.sleep(0.1)

    message.ack.assert_called_once()

    assert len(received_alerts) == 1
    assert isinstance(received_alerts[0], Alert)
    assert received_alerts[0].alert == "HighErrorRate"
    assert received_alerts[0].service == "checkout"


@pytest.mark.anyio
async def test_pubsub_malformed_message_is_acknowledged():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    async def handler(alert: Alert):
        received_alerts.append(alert)

    received_alerts = []

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")
        bus._handler = handler
        bus._loop = asyncio.get_running_loop()

        message = MagicMock()
        message.data = b"this is not valid JSON"

        bus._on_message(message)

        await asyncio.sleep(0)

    message.ack.assert_called_once()

    # Invalid messages should not be delivered to the application handler.
    assert received_alerts == []


@pytest.mark.anyio
async def test_pubsub_close_cancels_pull_future():
    publisher = MagicMock()
    subscriber = MagicMock()

    publisher.topic_path.return_value = "topic-path"
    subscriber.subscription_path.return_value = "subscription-path"

    pull_future = MagicMock()
    subscriber.subscribe.return_value = pull_future

    async def handler(alert: Alert):
        pass

    with patch(
        "backend.services.pubsub_bus.pubsub_v1.PublisherClient",
        return_value=publisher,
    ), patch(
        "backend.services.pubsub_bus.pubsub_v1.SubscriberClient",
        return_value=subscriber,
    ):
        bus = PubSubBus(project="test-project")
        bus.subscribe(handler)
        bus.close()

    pull_future.cancel.assert_called_once()