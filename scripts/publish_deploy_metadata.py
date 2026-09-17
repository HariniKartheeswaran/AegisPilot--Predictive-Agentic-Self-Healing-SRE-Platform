#!/usr/bin/env python3
"""Publish deployment metadata from Jenkins CI/CD to AegisPilot.

This script records a completed deployment in AegisPilot's storage so the
Correlation Agent can correlate future incidents, latency spikes, or errors
with specific releases and commit SHAs.

Usage:
  python scripts/publish_deploy_metadata.py \
    --service aegisops \
    --version 1.0.0-build42 \
    --commit 1a2b3c4 \
    --color green
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure repository root is on sys.path so backend imports work cleanly
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import httpx
except ImportError:
    httpx = None


def publish_local_sqlite(
    service: str,
    version: str,
    commit_sha: str,
    color: str,
    deployed_by: str,
    rollback_target: Optional[str] = None,
) -> bool:
    """Store deployment metadata directly in local SQLite storage."""
    try:
        from backend.config import get_settings
        from backend.models import Deploy, now_ms
        from backend.services.storage import SQLiteStorage

        settings = get_settings()
        storage = SQLiteStorage(settings.db_path)
        storage.init_schema()

        if not rollback_target:
            rollback_target = "blue" if color.lower() == "green" else "green"

        deploy_id = f"dep_{service}_{commit_sha[:7]}_{now_ms()}"
        deploy = Deploy(
            id=deploy_id,
            service=service,
            version=version,
            deployed_at=now_ms(),
            deployed_by=deployed_by,
            commit_sha=commit_sha,
            rollback_target=rollback_target,
        )
        storage.add_deploy(deploy)
        print(f"[METADATA] Successfully recorded deployment in SQLite: {deploy_id} (service={service}, version={version})")
        return True
    except Exception as exc:
        print(f"[METADATA-ERROR] Failed to record in SQLite: {exc}", file=sys.stderr)
        return False


def publish_remote_http(
    url: str,
    service: str,
    version: str,
    commit_sha: str,
    color: str,
    deployed_by: str,
    rollback_target: Optional[str] = None,
) -> bool:
    """Send deployment metadata over HTTPS to remote AegisPilot (e.g. Cloud Run)."""
    if httpx is None:
        print("[METADATA-ERROR] httpx is required for remote publication", file=sys.stderr)
        return False

    if not rollback_target:
        rollback_target = "blue" if color.lower() == "green" else "green"

    endpoint = url.rstrip("/") + "/api/incidents/custom"
    payload = {
        "service": service,
        "deploy_version": version,
        "rollback_target": rollback_target,
        "logs": f"Deployment finished for {service} version {version} commit {commit_sha}",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(endpoint, data=payload)
            if resp.status_code in (200, 201, 204):
                print(f"[METADATA] Successfully published to remote {endpoint}")
                return True
            print(f"[METADATA-WARN] Remote returned status {resp.status_code}: {resp.text}", file=sys.stderr)
            return False
    except Exception as exc:
        print(f"[METADATA-ERROR] HTTP request failed: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish deployment metadata for AegisPilot correlation")
    parser.add_argument("--service", required=True, help="Target service name (e.g., aegisops)")
    parser.add_argument("--version", required=True, help="Deployment version / image tag")
    parser.add_argument("--commit", required=True, help="Git commit SHA (short or long)")
    parser.add_argument("--color", default="green", choices=["blue", "green"], help="Blue/Green slot color")
    parser.add_argument("--deployed-by", default="jenkins-ci", help="Deployment trigger identity")
    parser.add_argument("--rollback-target", default=None, help="Target rollback version or color")
    parser.add_argument("--url", default=None, help="Optional AegisPilot HTTP URL (e.g., https://aegisops...run.app)")

    args = parser.parse_args()

    # Validate parameters
    if not args.service.strip():
        print("[METADATA-ERROR] --service cannot be empty", file=sys.stderr)
        return 1
    if not args.version.strip():
        print("[METADATA-ERROR] --version cannot be empty", file=sys.stderr)
        return 1
    if not args.commit.strip():
        print("[METADATA-ERROR] --commit cannot be empty", file=sys.stderr)
        return 1

    success = False
    if args.url:
        success = publish_remote_http(
            url=args.url,
            service=args.service.strip(),
            version=args.version.strip(),
            commit_sha=args.commit.strip(),
            color=args.color,
            deployed_by=args.deployed_by,
            rollback_target=args.rollback_target,
        )
    else:
        success = publish_local_sqlite(
            service=args.service.strip(),
            version=args.version.strip(),
            commit_sha=args.commit.strip(),
            color=args.color,
            deployed_by=args.deployed_by,
            rollback_target=args.rollback_target,
        )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
