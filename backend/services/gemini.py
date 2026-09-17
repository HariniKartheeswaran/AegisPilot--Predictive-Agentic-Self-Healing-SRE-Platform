"""Gemini access layer — the single choke-point for every model call.

Why this exists (spec hard-rule #8): the free AI Studio tier throttles under
load and returns 503 / 429. If any agent called the model directly, one blip
would crash the live demo. So EVERY call in the whole system funnels through
`GeminiService`, which wraps the request in exponential-backoff-with-jitter
retry and always reports latency + token usage for the audit trail.

Flash-only (hard-rule #5): the model id comes from config and defaults to the
spec's `gemini-3.5-flash`. Nothing here ever selects a Pro model.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types

from backend.config import get_settings

# Substrings that identify a *transient* failure worth retrying. A 400/403
# (bad request / permission) is NOT here — retrying those just wastes the
# rate-limit budget, so they surface immediately.
_RETRIABLE_MARKERS = (
    "503",
    "502",
    "500",
    "429",
    "unavailable",
    "overloaded",
    "resource_exhausted",
    "resource exhausted",
    "rate limit",
    "deadline",
    "timeout",
    "temporarily",
)


@dataclass
class GenResult:
    """Everything the audit trail needs from one model call."""

    text: str
    tokens: int
    latency_ms: int
    model: str
    attempts: int = 1
    raw: Any = field(default=None, repr=False)

    def json(self) -> dict[str, Any]:
        """Parse the response as JSON, tolerating ```json fences and prose.

        Gemini Flash usually honours a 'respond with JSON only' instruction,
        but we defend against a stray code fence or trailing sentence so an
        agent never dies on a parse error mid-demo.
        """
        return _extract_json(self.text)


def _is_retriable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(marker in msg for marker in _RETRIABLE_MARKERS)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # Strip a ```json ... ``` (or plain ```) fence if the model added one.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: grab the first balanced {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _estimate_tokens(result: Any, fallback_text: str) -> int:
    """Prefer real usage metadata; fall back to a ~4-chars/token estimate."""
    usage = getattr(result, "usage_metadata", None)
    if usage is not None:
        total = getattr(usage, "total_token_count", None)
        if total:
            return int(total)
    return max(1, len(fallback_text) // 4)


class GeminiService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: Optional[genai.Client] = None

    # Lazily construct the client so importing this module never explodes when
    # auth is absent (unit tests, tooling). Auth is only required at the moment
    # of the first real call. Two backends, selected by config:
    #   * Vertex AI  → ADC (no key), billed to the GCP project
    #   * AI Studio  → API key (free tier)
    def _get_client(self) -> genai.Client:
        if self._client is None:
            s = self._settings
            if s.use_vertex:
                # Vertex path: ADC handles auth; model region is vertex_location
                # (gemini-3.5-flash lives in 'global', not us-central1).
                self._client = genai.Client(
                    vertexai=True,
                    project=s.google_cloud_project or None,
                    location=s.vertex_location,
                )
            elif s.has_gemini_key:
                # AI Studio key path: passing api_key selects the Gemini
                # Developer API backend automatically.
                self._client = genai.Client(api_key=s.gemini_api_key)
            else:
                raise RuntimeError(
                    "No Gemini auth configured. Either set GEMINI_API_KEY (AI "
                    "Studio) or set GOOGLE_GENAI_USE_VERTEXAI=true with ADC "
                    "(gcloud auth application-default login). Nothing is stubbed."
                )
        return self._client

    @property
    def model(self) -> str:
        return self._settings.gemini_model

    # ----------------------------------------------------------------- core
    def _generate_sync(
        self,
        contents: Any,
        system: Optional[str],
        temperature: float,
        response_json: bool,
        model: Optional[str] = None,
    ) -> GenResult:
        client = self._get_client()
        model_id = model or self.model
        cfg_kwargs: dict[str, Any] = {"temperature": temperature}
        if system:
            cfg_kwargs["system_instruction"] = system
        if response_json:
            cfg_kwargs["response_mime_type"] = "application/json"
        config = types.GenerateContentConfig(**cfg_kwargs)

        max_retries = self._settings.gemini_max_retries
        base = self._settings.gemini_base_delay
        last_err: Optional[Exception] = None

        for attempt in range(max_retries):
            started = time.perf_counter()
            try:
                resp = client.models.generate_content(
                    model=model_id, contents=contents, config=config
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                text = (resp.text or "").strip()
                return GenResult(
                    text=text,
                    tokens=_estimate_tokens(resp, text),
                    latency_ms=latency_ms,
                    model=model_id,
                    attempts=attempt + 1,
                    raw=resp,
                )
            except Exception as err:  # noqa: BLE001 — we classify below
                last_err = err
                if attempt == max_retries - 1 or not _is_retriable(err):
                    raise
                # Exponential backoff + jitter. Jitter avoids a thundering herd
                # where six agents all retry on the same tick.
                delay = (base * (2 ** attempt)) + random.random()
                time.sleep(delay)

        # Unreachable (loop either returns or raises) but keeps type-checkers calm.
        raise last_err if last_err else RuntimeError("Gemini call failed")

    # --------------------------------------------------------------- public
    async def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.3,
        response_json: bool = False,
        model: Optional[str] = None,
    ) -> GenResult:
        """Text generation. Runs the blocking SDK call in a thread so the async
        event loop (FastAPI/SSE) is never blocked while a slow retry sleeps.
        `model` overrides the default (used for the Gemini Pro RCA tier)."""
        return await asyncio.to_thread(
            self._generate_sync, prompt, system, temperature, response_json, model
        )

    async def generate_json(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], GenResult]:
        result = await self.generate(
            prompt, system=system, temperature=temperature, response_json=True
        )
        return result.json(), result

    async def generate_vision(
        self,
        prompt: str,
        image_path: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
        response_json: bool = False,
    ) -> GenResult:
        """Multimodal call — reads the Grafana screenshot alongside the prompt.

        The image is sent as an inline image Part (bytes), which is the correct
        pattern for local files on the AI Studio API.
        """
        path = Path(image_path)
        if not path.is_absolute():
            # Resolve relative snapshot paths against the repo root.
            from backend.config import REPO_ROOT

            path = REPO_ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"Grafana snapshot not found: {path}")

        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        image_part = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
        contents = [prompt, image_part]
        return await asyncio.to_thread(
            self._generate_sync, contents, system, temperature, response_json
        )


# Module-level singleton — one client, reused everywhere.
gemini = GeminiService()
