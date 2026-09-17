"""Event ingestion bus.

`EventBus` is the interface; `InProcessBus` is the local async implementation.
An alert published here triggers the Orchestrator. Swapping to real Google
Pub/Sub means writing `PubSubBus(EventBus)` and changing one line in main.py —
the publisher script and the orchestrator subscriber don't change. Same
cloud-portable story as StorageService.
"""
from __future__ import annotations

import abc
import asyncio
from typing import Awaitable, Callable

from backend.models import Alert

# A subscriber is any async callable that takes an Alert.
Subscriber = Callable[[Alert], Awaitable[None]]


class EventBus(abc.ABC):
    @abc.abstractmethod
    def subscribe(self, handler: Subscriber) -> None: ...

    @abc.abstractmethod
    async def publish(self, alert: Alert) -> None: ...


class InProcessBus(EventBus):
    """In-memory async fan-out. Delivery is fire-and-forget via a task per
    subscriber so a slow orchestrator never blocks the publisher (mirrors
    Pub/Sub's decoupled push model)."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, handler: Subscriber) -> None:
        self._subscribers.append(handler)

    async def publish(self, alert: Alert) -> None:
        for handler in self._subscribers:
            # Schedule each handler independently; exceptions are isolated so
            # one failing subscriber can't sink the others.
            asyncio.create_task(self._safe_deliver(handler, alert))

    @staticmethod
    async def _safe_deliver(handler: Subscriber, alert: Alert) -> None:
        try:
            await handler(alert)
        except Exception as exc:  # noqa: BLE001 — isolation boundary
            import logging

            logging.getLogger("aegisops.eventbus").exception(
                "subscriber failed: %s", exc
            )
