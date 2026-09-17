"""In-process broadcast hub for real-time reasoning-chain streaming (SSE).

Every agent step is published here and fanned out to all connected war-room
clients. It's decoupled from the audit log on purpose: audit_log is the durable
record; this hub is the live wire. Both get every event.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import AsyncIterator

from backend.models import StreamEvent


class StreamHub:
    def __init__(self, replay_size: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[StreamEvent]] = set()
        # Small ring buffer so a client that connects mid-incident still sees
        # the steps that already happened (the UI reconstructs the timeline).
        self._replay: deque[StreamEvent] = deque(maxlen=replay_size)
        self._lock = asyncio.Lock()

    async def publish(self, event: StreamEvent) -> None:
        self._replay.append(event)
        # Copy under lock; deliver outside to avoid holding it during put.
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            # Never block the producer: drop on a full queue rather than stall
            # the whole pipeline for one slow client.
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def replay_for(self, incident_id: str | None) -> list[StreamEvent]:
        if incident_id is None:
            return list(self._replay)
        return [e for e in self._replay if e.incident_id == incident_id]

    async def subscribe(
        self, incident_id: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        q: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers.add(q)
        try:
            # Replay history first so late joiners catch up.
            for event in self.replay_for(incident_id):
                yield event
            while True:
                event = await q.get()
                if incident_id is None or event.incident_id == incident_id:
                    yield event
        finally:
            async with self._lock:
                self._subscribers.discard(q)


# Module-level singleton hub.
hub = StreamHub()
