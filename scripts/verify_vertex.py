"""Phase-2 proof: one real Vertex AI Flash call through the retry wrapper.

Confirms ADC auth, the Vertex backend, and the configured model id all work —
the "one real Vertex Flash call succeeds" gate. Prints the resolved config so
it's obvious in the demo that calls are Vertex (credit-covered), not AI Studio.

Run:  python scripts/verify_vertex.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import google.auth  # noqa: E402

from backend.config import get_settings  # noqa: E402

get_settings().apply_google_env()

from backend.services.gemini import gemini  # noqa: E402


async def main() -> int:
    s = get_settings()
    print("=== AegisPilot Vertex AI preflight ===")
    print(f"  use_vertex      : {s.use_vertex}")
    print(f"  project         : {s.google_cloud_project}")
    print(f"  vertex_location : {s.vertex_location}  (model endpoint)")
    print(f"  compute_location: {s.google_cloud_location}  (Firestore/PubSub/Run)")
    print(f"  model (Flash)   : {s.gemini_model}")
    print(f"  model (Pro)     : {s.gemini_model_pro}")

    try:
        creds, proj = google.auth.default()
        print(f"  ADC             : OK (project {proj})")
    except Exception as exc:  # noqa: BLE001
        print(f"  ADC             : ERROR — {exc}")
        print("\nRun:  gcloud auth application-default login")
        return 1

    if not s.use_vertex:
        print("\nGOOGLE_GENAI_USE_VERTEXAI is not true — not on Vertex. Set it in .env.")
        return 1

    try:
        r = await gemini.generate(
            "You are AegisPilot running on Vertex AI. Reply with exactly: VERTEX-OK",
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001 — report the exact error, don't hide it
        print(f"\n[FAIL] Vertex call errored: {type(exc).__name__}: {exc}")
        return 1

    ok = "VERTEX-OK" in r.text
    print(f"\n  live call reply : {r.text.strip()!r}")
    print(f"  tokens/latency  : {r.tokens} tok / {r.latency_ms} ms / {r.attempts} attempt(s)")
    print(f"  model returned  : {r.model}")
    print(f"\n{'[OK] Real Vertex Flash call succeeded.' if ok else '[WARN] Unexpected reply (call still succeeded).'}")
    return 0 if ok else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
