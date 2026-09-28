"""Unit tests for backend.main helper functions."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.main import (
    _auth_mode,
    _build_bus,
    _build_orchestrator,
    _build_storage,
    _log_level_from_message,
    _media_type_for_path,
    _MIME_JPEG,
    _MIME_PNG,
    _seed_if_needed,
)


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("ERROR connection failed", "ERROR"),
        ("pool timeout", "ERROR"),
        ("WARN degraded", "WARN"),
        ("slow retry", "WARN"),
        ("all good", "INFO"),
    ],
)
def test_log_level_from_message(msg, expected):
    assert _log_level_from_message(msg) == expected


def test_auth_mode_variants():
    assert _auth_mode(SimpleNamespace(use_vertex=True, has_gemini_key=False)) == "vertex-adc"
    assert _auth_mode(SimpleNamespace(use_vertex=False, has_gemini_key=True)) == "ai-studio-key"
    assert _auth_mode(SimpleNamespace(use_vertex=False, has_gemini_key=False)) == "none"


def test_media_type_for_path():
    assert _media_type_for_path("shot.png") == _MIME_PNG
    assert _media_type_for_path(Path("shot.JPEG")) == _MIME_JPEG
    assert _media_type_for_path("a.jpg") == _MIME_JPEG


def test_build_storage_local(tmp_path):
    from backend.services.storage import SQLiteStorage

    s = SimpleNamespace(backend="local", db_path=str(tmp_path / "t.db"))
    assert isinstance(_build_storage(s), SQLiteStorage)


def test_build_storage_cloud():
    fake = object()
    with patch("backend.services.firestore_storage.FirestoreStorage", return_value=fake):
        s = SimpleNamespace(backend="cloud", google_cloud_project="p")
        assert _build_storage(s) is fake


def test_build_bus_local():
    from backend.services.eventbus import InProcessBus

    s = SimpleNamespace(backend="local")
    assert isinstance(_build_bus(s), InProcessBus)


def test_build_orchestrator_variants():
    deps = MagicMock()
    s = SimpleNamespace(orchestrator="local")
    from backend.orchestrator import Orchestrator

    assert isinstance(_build_orchestrator(deps, s), Orchestrator)

    with patch("backend.main.AdkOrchestrator") as adk:
        adk.return_value = "adk"
        s2 = SimpleNamespace(orchestrator="adk")
        assert _build_orchestrator(deps, s2) == "adk"


def test_seed_if_needed_skips_when_agents_present():
    storage = MagicMock()
    storage.list_agents.return_value = [1]
    settings = SimpleNamespace(gemini_model="m", prometheus_url="http://p")
    with patch("backend.main.seed_all") as seed:
        _seed_if_needed(storage, settings)
        seed.assert_not_called()
