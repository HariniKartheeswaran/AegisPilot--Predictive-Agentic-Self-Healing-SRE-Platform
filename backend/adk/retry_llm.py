"""RetryGemini — the 503-safe retry wrapper for the REAL google-adk model layer.

Hard rule (both specs): every model call is wrapped in exponential-backoff +
jitter retry — Vertex throttles too. ADK drives model calls internally through a
`BaseLlm`, so to keep our guarantee we subclass ADK's own `Gemini` and wrap its
`generate_content_async` in the same retry loop the custom GeminiService uses.
This is the single place ADK-path model reliability is enforced.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import AsyncGenerator

from google.adk.models import Gemini, LlmRequest, LlmResponse

from backend.config import get_settings
from backend.services.gemini import _is_retriable  # reuse the transient-error classifier

log = logging.getLogger("aegisops.adk.retry")


class RetryGemini(Gemini):
    """Drop-in ADK Gemini that retries transient (503/429/UNAVAILABLE) failures."""

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        s = get_settings()
        max_retries = s.gemini_max_retries
        base = s.gemini_base_delay

        attempt = 0
        while True:
            try:
                # Non-streaming yields a single response, so re-running on a
                # transient failure is safe (no half-emitted output).
                async for resp in super().generate_content_async(llm_request, stream=stream):
                    yield resp
                return
            except Exception as err:  # noqa: BLE001 — classify then decide
                if attempt >= max_retries - 1 or not _is_retriable(err):
                    raise
                delay = (base * (2 ** attempt)) + random.random()
                log.warning(
                    "Vertex transient error (attempt %d/%d): %s — backing off %.1fs",
                    attempt + 1, max_retries, str(err)[:120], delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
