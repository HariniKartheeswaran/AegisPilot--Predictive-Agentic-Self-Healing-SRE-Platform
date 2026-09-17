"""PubSubBus — the cloud implementation of EventBus (real Google Pub/Sub).

The other half of the cloud-portable seam (upgrade spec Phase 4). Same EventBus
contract; the in-process bus is untouched and stays selectable via BACKEND=local.

Delivery model:
  * publish(alert)  → publishes JSON to the `incident-alerts` topic
  * subscribe(cb)   → a streaming PULL subscriber; each message is parsed into an
                      Alert and the async handler is scheduled on the app's event
                      loop (Pub/Sub callbacks run in worker threads).

For Cloud Run (Phase 6) a PUSH subscription hits POST /api/pubsub/push instead;
both paths funnel into the same orchestrator.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

from backend.models import Alert
from backend.services.eventbus import EventBus, Subscriber

log = logging.getLogger("aegisops.pubsub")

TOPIC_ID = "incident-alerts"
SUBSCRIPTION_ID = "aegisops-worker"


class PubSubBus(EventBus):
    def __init__(self, project: str, topic_id: str = TOPIC_ID,
                 subscription_id: str = SUBSCRIPTION_ID) -> None:
        self._project = project
        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()
        self._topic_path = self._publisher.topic_path(project, topic_id)
        self._sub_path = self._subscriber.subscription_path(project, subscription_id)
        self._handler: Optional[Subscriber] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pull_future = None

    # -- provisioning (idempotent) --
    def ensure(self, create_pull_subscription: bool = True) -> None:
        """Create the topic (+ optional pull subscription) if absent. Fails loud
        on auth errors (per the upgrade rules) rather than hiding them. In PUSH
        mode the pull subscription is skipped — a separate push subscription
        (created by deploy.sh) delivers to Cloud Run instead."""
        try:
            self._publisher.create_topic(name=self._topic_path)
            log.info("created Pub/Sub topic %s", self._topic_path)
        except AlreadyExists:
            pass
        if not create_pull_subscription:
            return
        try:
            self._subscriber.create_subscription(
                name=self._sub_path, topic=self._topic_path,
                ack_deadline_seconds=60,
            )
            log.info("created Pub/Sub subscription %s", self._sub_path)
        except AlreadyExists:
            pass

    # -- publish --
    async def publish(self, alert: Alert) -> None:
        data = json.dumps(alert.model_dump()).encode("utf-8")
        # publish() returns a concurrent future; resolve it off the event loop.
        future = self._publisher.publish(self._topic_path, data=data)
        await asyncio.to_thread(future.result, 15)

    # -- subscribe (streaming pull) --
    def subscribe(self, handler: Subscriber) -> None:
        self._handler = handler
        # Capture the app's running loop so thread callbacks can schedule the
        # async orchestrator safely.
        self._loop = asyncio.get_event_loop()
        self._pull_future = self._subscriber.subscribe(
            self._sub_path, callback=self._on_message
        )
        log.info("streaming pull started on %s", self._sub_path)

    def _on_message(self, message: pubsub_v1.subscriber.message.Message) -> None:
        try:
            alert = Alert(**json.loads(message.data.decode("utf-8")))
        except Exception as exc:  # noqa: BLE001 — bad message, ack + drop
            log.warning("dropping malformed Pub/Sub message: %s", exc)
            message.ack()
            return
        # Hand off to the async orchestrator on the main loop, then ack.
        if self._handler and self._loop:
            asyncio.run_coroutine_threadsafe(self._handler(alert), self._loop)
        message.ack()

    def close(self) -> None:
        if self._pull_future is not None:
            self._pull_future.cancel()
