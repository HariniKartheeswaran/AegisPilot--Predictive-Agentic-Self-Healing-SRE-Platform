"""Pytest configuration and shared fixtures for Aegisops tests."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure test run does not attempt to contact real cloud services
os.environ["BACKEND"] = "local"
os.environ["ORCHESTRATOR"] = "local"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
os.environ["GEMINI_API_KEY"] = "mock-key-for-tests"


@pytest.fixture
def temp_db(tmp_path: Path):
    """Provides an isolated temporary SQLite database path for each test."""
    db_file = tmp_path / "test_aegisops.db"
    return str(db_file)


@pytest.fixture
def isolated_storage(temp_db):
    """Provides an initialized SQLiteStorage instance backed by an isolated database."""
    from backend.services.storage import SQLiteStorage

    storage = SQLiteStorage(temp_db)
    storage.init_schema()
    return storage
