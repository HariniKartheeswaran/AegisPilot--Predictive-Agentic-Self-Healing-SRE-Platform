
import asyncio

import pytest

from backend.models import StreamEvent
from backend.services.stream import StreamHub


def make_event(incident_id="inc-1", message="test event"):
    return StreamEvent(
        incident_id=incident_id,
        type="test",
        payload={"message": message},
    )


def test_replay_for_returns_all_events_when_no_incident_filter():
    hub = StreamHub()

    event1 = make_event("inc-1", "first")
    event2 = make_event("inc-2", "second")

    hub._replay.append(event1)
    hub._replay.append(event2)

    assert hub.replay_for(None) == [event1, event2]


def test_replay_for_filters_by_incident_id():
    hub = StreamHub()

    event1 = make_event("inc-1", "first")
    event2 = make_event("inc-2", "second")
    event3 = make_event("inc-1", "third")

    hub._replay.extend([event1, event2, event3])

    assert hub.replay_for("inc-1") == [event1, event3]
    assert hub.replay_for("inc-2") == [event2]


def test_replay_buffer_respects_configured_size():
    hub = StreamHub(replay_size=2)

    event1 = make_event("inc-1", "first")
    event2 = make_event("inc-1", "second")
    event3 = make_event("inc-1", "third")

    hub._replay.extend([event1, event2, event3])

    assert hub.replay_for(None) == [event2, event3]


@pytest.mark.anyio
async def test_publish_stores_event_in_replay_buffer():
    hub = StreamHub()
    event = make_event()

    await hub.publish(event)

    assert hub.replay_for(None) == [event]


@pytest.mark.anyio
async def test_publish_delivers_event_to_subscriber():
    hub = StreamHub()
    queue = asyncio.Queue()

    async with hub._lock:
        hub._subscribers.add(queue)

    event = make_event()

    await hub.publish(event)

    assert await queue.get() == event


@pytest.mark.anyio
async def test_publish_does_not_block_when_subscriber_queue_is_full():
    hub = StreamHub()
    queue = asyncio.Queue(maxsize=1)

    queue.put_nowait(make_event("inc-1", "already full"))

    async with hub._lock:
        hub._subscribers.add(queue)

    event = make_event("inc-1", "new event")

    await asyncio.wait_for(hub.publish(event), timeout=1)

    assert queue.qsize() == 1
    received = await queue.get()
    assert received.payload["message"] == "already full"


@pytest.mark.anyio
async def test_subscribe_replays_matching_incident_events_first():
    hub = StreamHub()

    first = make_event("inc-1", "replay-1")
    other = make_event("inc-2", "other")
    second = make_event("inc-1", "replay-2")

    await hub.publish(first)
    await hub.publish(other)
    await hub.publish(second)

    stream = hub.subscribe("inc-1")

    received = []

    async for event in stream:
        received.append(event)
        if len(received) == 2:
            break

    await stream.aclose()

    assert received == [first, second]


@pytest.mark.anyio
async def test_subscribe_without_filter_replays_all_events():
    hub = StreamHub()

    first = make_event("inc-1", "first")
    second = make_event("inc-2", "second")

    await hub.publish(first)
    await hub.publish(second)

    stream = hub.subscribe()

    received = []

    async for event in stream:
        received.append(event)
        if len(received) == 2:
            break

    await stream.aclose()

    assert received == [first, second]


@pytest.mark.anyio
async def test_subscribe_receives_new_matching_event():
    hub = StreamHub()

    stream = hub.subscribe("inc-1")
    iterator = stream.__aiter__()

    # Start the generator so it registers its queue.
    task = asyncio.create_task(iterator.__anext__())

    await asyncio.sleep(0)

    event = make_event("inc-1", "live event")
    await hub.publish(event)

    received = await asyncio.wait_for(task, timeout=1)

    assert received == event

    await stream.aclose()


@pytest.mark.anyio
async def test_filtered_subscriber_ignores_other_incident_events():
    hub = StreamHub()

    stream = hub.subscribe("inc-1")
    iterator = stream.__aiter__()

    task = asyncio.create_task(iterator.__anext__())

    await asyncio.sleep(0)

    other = make_event("inc-2", "wrong incident")
    await hub.publish(other)

    matching = make_event("inc-1", "correct incident")
    await hub.publish(matching)

    received = await asyncio.wait_for(task, timeout=1)

    assert received == matching

    await stream.aclose()


@pytest.mark.anyio
async def test_subscriber_is_removed_when_stream_is_closed():
    hub = StreamHub()

    stream = hub.subscribe("inc-1")
    iterator = stream.__aiter__()

    task = asyncio.create_task(iterator.__anext__())

    await asyncio.sleep(0)

    assert len(hub._subscribers) == 1

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await stream.aclose()

    assert len(hub._subscribers) == 0
