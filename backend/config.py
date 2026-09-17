"""Central configuration, loaded once from the environment / .env.

Everything that touches a secret or a tunable reads from here so that the rest
of the codebase never sees `os.environ` directly and never hardcodes a key.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root is one level up from backend/. Keeps the SQLite file and seed
# assets resolvable no matter which directory uvicorn is launched from.
BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
SEED_DIR = BACKEND_DIR / "seed"


class Settings(BaseSettings):
    # Loads the repo-root .env; unknown keys are ignored so the file can hold
    # extra notes without breaking startup.
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Gemini model access ---
    # Two auth paths: AI Studio API key (free tier) OR Vertex AI via ADC (billed
    # to the GCP project, no key). Vertex is selected when use_vertexai=true.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"       # all six agents (Flash-only)
    gemini_model_pro: str = "gemini-2.5-pro"     # RCA tier (Phase 5); 3.5-pro n/a yet
    gemini_max_retries: int = 5
    gemini_base_delay: float = 1.0

    # --- Vertex AI (GCP, credit-covered) ---
    google_genai_use_vertexai: bool = False
    google_cloud_project: str = ""
    # Compute region for Firestore / Pub/Sub / Cloud Run.
    google_cloud_location: str = "us-central1"
    # Vertex MODEL endpoint region. gemini-3.5-flash is published in 'global',
    # not us-central1 — keep these independent so we can use real 3.5 Flash.
    vertex_location: str = "global"

    # --- Backend selection (never delete local impls; swap via env) ---
    backend: str = "local"        # local | cloud  (SQLite/in-proc  vs  Firestore/PubSub)
    orchestrator: str = "local"   # local | adk    (custom  vs  real google-adk Runner)
    # Pub/Sub delivery: 'pull' runs an in-app streaming subscriber (good for a
    # long-lived local/dev process); 'push' relies on a Pub/Sub PUSH subscription
    # hitting /api/pubsub/push (correct for Cloud Run, which scales to zero).
    pubsub_mode: str = "pull"     # pull | push

    # --- Slack (optional; comms falls back to console when empty) ---
    slack_webhook_url: str = ""

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8080

    # --- Storage ---
    aegis_db_path: str = "aegisops.db"

    @property
    def db_path(self) -> Path:
        """Absolute DB path so state is stable regardless of launch cwd."""
        p = Path(self.aegis_db_path)
        return p if p.is_absolute() else (REPO_ROOT / p)

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key and "paste_your" not in self.gemini_api_key)

    @property
    def use_vertex(self) -> bool:
        return bool(self.google_genai_use_vertexai)

    @property
    def can_call_model(self) -> bool:
        """True when the model layer has a viable auth path (key OR Vertex)."""
        return self.has_gemini_key or self.use_vertex

    @property
    def has_slack(self) -> bool:
        return bool(self.slack_webhook_url.strip())

    def apply_google_env(self) -> None:
        """Export Vertex config to os.environ so libraries that read it directly
        (ADK's Gemini model, google-genai) pick the Vertex backend. We set the
        genai-facing GOOGLE_CLOUD_LOCATION to the *model* region (vertex_location)
        because that's where gemini-3.5-flash is published; other GCP clients get
        google_cloud_location passed explicitly."""
        if self.use_vertex:
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
            if self.google_cloud_project:
                os.environ["GOOGLE_CLOUD_PROJECT"] = self.google_cloud_project
            os.environ["GOOGLE_CLOUD_LOCATION"] = self.vertex_location


@lru_cache
def get_settings() -> Settings:
    return Settings()
