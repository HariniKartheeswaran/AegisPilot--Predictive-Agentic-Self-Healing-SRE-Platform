"""Unit tests for the publish_deploy_metadata script."""
import sys
import pytest
from scripts.publish_deploy_metadata import main, publish_local_sqlite
from backend.services.storage import SQLiteStorage


def test_publish_local_sqlite_records_deploy(temp_db):
    """Verify publish_local_sqlite persists record with correct rollback target."""
    ok = publish_local_sqlite(
        service="aegisops",
        version="1.0.0-rc1",
        commit_sha="a1b2c3d4e5",
        color="green",
        deployed_by="jenkins-runner",
        db_path=temp_db,
    )
    assert ok is True

    storage = SQLiteStorage(temp_db)
    deploys = storage.deploys_for_service("aegisops")
    assert len(deploys) == 1
    assert deploys[0].version == "1.0.0-rc1"
    assert deploys[0].commit_sha == "a1b2c3d4e5"
    assert deploys[0].rollback_target == "blue"


def test_main_cli_argument_validation(monkeypatch):
    """Verify CLI flags validation and rejection of missing parameters."""
    test_args = ["publish_deploy_metadata.py", "--service", "", "--version", "v1", "--commit", "c1"]
    monkeypatch.setattr(sys, "argv", test_args)
    assert main() == 1
