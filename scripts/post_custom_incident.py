#!/usr/bin/env python3
"""POST a Custom incident to War Room after Jenkins promote.

Uses the same /api/incidents/custom path as the War Room Custom form —
not /api/demo/fire. Payload comes from a JSON fixture (Grafana/Prom-style
logs you maintain), not a hardcoded checkout inject.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx required", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="War Room base URL (AEGIS_API_URL)")
    p.add_argument(
        "--payload",
        default="fixtures/post-promote-custom.json",
        help="JSON fixture with service/alert/logs (Custom form fields)",
    )
    args = p.parse_args()

    path = Path(args.payload)
    if not path.is_file():
        print(f"payload not found: {path}", file=sys.stderr)
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    service = str(data.get("service") or "").strip()
    if not service:
        print("payload.service is required", file=sys.stderr)
        return 1

    logs = data.get("logs") or []
    if isinstance(logs, list):
        logs_text = "\n".join(str(x) for x in logs)
    else:
        logs_text = str(logs)

    form = {
        "service": service,
        "alert": str(data.get("alert") or "HighErrorRate"),
        "error_rate": str(data.get("error_rate") or "10%"),
        "logs": logs_text,
    }
    if str(data.get("deploy_version") or "").strip():
        form["deploy_version"] = str(data["deploy_version"]).strip()
    if str(data.get("rollback_target") or "").strip():
        form["rollback_target"] = str(data["rollback_target"]).strip()

    base = args.url.rstrip("/")
    endpoint = f"{base}/api/incidents/custom"
    print(f"[CUSTOM] POST {endpoint} service={service}")
    with httpx.Client(timeout=60.0) as client:
        res = client.post(endpoint, data=form)
    print(f"[CUSTOM] HTTP {res.status_code}: {res.text[:500]}")
    if res.status_code >= 400:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
