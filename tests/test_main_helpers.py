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


def test_prefer_live_b64_seed_and_empty_snap():
    from backend.main import _prefer_live_b64

    assert _prefer_live_b64({}, None, seed_path=True) is True
    assert _prefer_live_b64({}, None, seed_path=False) is True
    assert _prefer_live_b64({"snapshot_source": "other"}, "live.png", False) is False
    assert _prefer_live_b64({"snapshot_source": "grafana-render"}, "x", False) is True


def test_disk_snapshot_response_relative_and_missing(tmp_path, monkeypatch):
    from backend import main as main_mod

    seed = tmp_path / "backend" / "seed"
    seed.mkdir(parents=True)
    monkeypatch.setattr(main_mod, "SEED_DIR", seed)
    # relative path resolves against repo root (= SEED_DIR.parent.parent)
    shot = tmp_path / "live.png"
    shot.write_bytes(b"png")
    assert main_mod._disk_snapshot_response("live.png") is not None
    assert main_mod._disk_snapshot_response("nope-missing.png") is None


def test_serve_grafana_snapshot_disk_then_b64_fallback(tmp_path):
    import base64

    from backend.main import _serve_grafana_snapshot

    shot = tmp_path / "dash.png"
    shot.write_bytes(b"disk-bytes")
    resp = _serve_grafana_snapshot({}, str(shot), None)
    assert resp is not None
    assert resp.path == shot

    raw = b"\x89PNG-fallback"
    b64 = base64.b64encode(raw).decode()
    # non-seed missing file → disk miss → b64 branch (line that was uncovered)
    resp2 = _serve_grafana_snapshot({}, str(tmp_path / "gone.png"), b64)
    assert resp2 is not None
    assert resp2.body == raw


def test_serve_grafana_snapshot_none():
    from backend.main import _serve_grafana_snapshot

    assert _serve_grafana_snapshot({}, None, None) is None
