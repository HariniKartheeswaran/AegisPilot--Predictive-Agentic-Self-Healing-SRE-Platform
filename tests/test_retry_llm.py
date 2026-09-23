
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.adk.retry_llm import RetryGemini


def make_settings(max_retries=3, base_delay=0):
    return SimpleNamespace(
        gemini_max_retries=max_retries,
        gemini_base_delay=base_delay,
    )


async def collect_async_generator(generator):
    results = []
    async for item in generator:
        results.append(item)
    return results


@pytest.mark.anyio
async def test_generate_content_async_yields_successful_response():
    service = RetryGemini()
    request = MagicMock()
    response = MagicMock()

    async def fake_generate(*args, **kwargs):
        yield response

    with patch(
        "backend.adk.retry_llm.get_settings",
        return_value=make_settings(),
    ), patch.object(
        RetryGemini.__bases__[0],
        "generate_content_async",
        new=fake_generate,
    ) as parent_method:
        results = await collect_async_generator(
            service.generate_content_async(request)
        )

    assert results == [response]

@pytest.mark.anyio
async def test_generate_content_async_yields_multiple_responses():
    service = RetryGemini()
    request = MagicMock()
    responses = [MagicMock(), MagicMock(), MagicMock()]

    async def fake_generate(*args, **kwargs):
        for response in responses:
            yield response

    with patch(
        "backend.adk.retry_llm.get_settings",
        return_value=make_settings(),
    ), patch.object(
        RetryGemini.__bases__[0],
        "generate_content_async",
        new=fake_generate,
    ):
        results = await collect_async_generator(
            service.generate_content_async(request, stream=True)
        )

    assert results == responses


@pytest.mark.anyio
async def test_generate_content_async_passes_stream_argument():
    service = RetryGemini()
    request = MagicMock()
    response = MagicMock()

    async def fake_generate(*args, **kwargs):
        yield response

    with patch(
        "backend.adk.retry_llm.get_settings",
        return_value=make_settings(),
    ), patch.object(
        RetryGemini.__bases__[0],
        "generate_content_async",
        new=fake_generate,
    ) as parent_method:
        await collect_async_generator(
            service.generate_content_async(request, stream=True)
        )

@pytest.mark.anyio
async def test_generate_content_async_retries_transient_error():
    service = RetryGemini()
    request = MagicMock()
    response = MagicMock()

    calls = 0

    async def fake_generate(*args, **kwargs):
        nonlocal calls
        calls += 1

        if calls == 1:
            raise Exception("503 Service Unavailable")

        yield response

    with patch(
        "backend.adk.retry_llm.get_settings",
        return_value=make_settings(max_retries=3, base_delay=0),
    ), patch.object(
        RetryGemini.__bases__[0],
        "generate_content_async",
        new=fake_generate,
    ), patch(
        "backend.adk.retry_llm.random.random",
        return_value=0,
    ), patch(
        "backend.adk.retry_llm.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep_mock:
        results = await collect_async_generator(
            service.generate_content_async(request)
        )

    assert results == [response]
    assert calls == 2
    sleep_mock.assert_awaited_once_with(0)


@pytest.mark.anyio
async def test_generate_content_async_uses_exponential_backoff():
    service = RetryGemini()
    request = MagicMock()
    response = MagicMock()

    calls = 0

    async def fake_generate(*args, **kwargs):
        nonlocal calls
        calls += 1

        if calls <= 2:
            raise Exception("429 Too Many Requests")

        yield response

    with patch(
        "backend.adk.retry_llm.get_settings",
        return_value=make_settings(max_retries=3, base_delay=2),
    ), patch.object(
        RetryGemini.__bases__[0],
        "generate_content_async",
        new=fake_generate,
    ), patch(
        "backend.adk.retry_llm.random.random",
        return_value=0,
    ), patch(
        "backend.adk.retry_llm.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep_mock:
        results = await collect_async_generator(
            service.generate_content_async(request)
        )

    assert results == [response]
    assert calls == 3
    assert sleep_mock.await_args_list[0].args == (2,)
    assert sleep_mock.await_args_list[1].args == (4,)


@pytest.mark.anyio
async def test_generate_content_async_does_not_retry_non_transient_error():
    service = RetryGemini()
    request = MagicMock()

    async def fake_generate(*args, **kwargs):
        raise Exception("403 Permission Denied")
        yield  # keeps this an async generator

    with patch(
        "backend.adk.retry_llm.get_settings",
        return_value=make_settings(max_retries=3),
    ), patch.object(
        RetryGemini.__bases__[0],
        "generate_content_async",
        new=fake_generate,
    ), patch(
        "backend.adk.retry_llm.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep_mock:
        with pytest.raises(Exception, match="403 Permission Denied"):
            await collect_async_generator(
                service.generate_content_async(request)
            )

    sleep_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_generate_content_async_stops_after_max_retries():
    service = RetryGemini()
    request = MagicMock()

    calls = 0

    async def fake_generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise Exception("503 Service Unavailable")
        yield

    with patch(
        "backend.adk.retry_llm.get_settings",
        return_value=make_settings(max_retries=3, base_delay=0),
    ), patch.object(
        RetryGemini.__bases__[0],
        "generate_content_async",
        new=fake_generate,
    ), patch(
        "backend.adk.retry_llm.random.random",
        return_value=0,
    ), patch(
        "backend.adk.retry_llm.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep_mock:
        with pytest.raises(Exception, match="503 Service Unavailable"):
            await collect_async_generator(
                service.generate_content_async(request)
            )

    assert calls == 3
    assert sleep_mock.await_count == 2


@pytest.mark.anyio
async def test_generate_content_async_logs_retry_warning():
    service = RetryGemini()
    request = MagicMock()
    response = MagicMock()

    calls = 0

    async def fake_generate(*args, **kwargs):
        nonlocal calls
        calls += 1

        if calls == 1:
            raise Exception("503 Service Unavailable")

        yield response

    with patch(
        "backend.adk.retry_llm.get_settings",
        return_value=make_settings(max_retries=2, base_delay=0),
    ), patch.object(
        RetryGemini.__bases__[0],
        "generate_content_async",
        new=fake_generate,
    ), patch(
        "backend.adk.retry_llm.random.random",
        return_value=0,
    ), patch(
        "backend.adk.retry_llm.asyncio.sleep",
        new_callable=AsyncMock,
    ), patch(
        "backend.adk.retry_llm.log.warning"
    ) as warning_mock:
        results = await collect_async_generator(
            service.generate_content_async(request)
        )

    assert results == [response]
    warning_mock.assert_called_once()

    message = warning_mock.call_args.args[0]
    assert "Vertex transient error" in message
