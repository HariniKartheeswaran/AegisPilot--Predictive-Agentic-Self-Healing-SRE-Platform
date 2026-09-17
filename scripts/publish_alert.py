"""Fire the demo incident: drop a real alert onto the running AegisPilot bus.

Usage:
    python scripts/publish_alert.py                 # default checkout-svc SEV1
    python scripts/publish_alert.py --url http://localhost:8080

This posts to /api/alerts, which publishes onto the in-process event bus exactly
as a real Pub/Sub push would. The Orchestrator picks it up and the war room
lights up.
"""
from __future__ import annotations

import argparse
import sys

import httpx

DEFAULT_SNAPSHOT = "backend/seed/grafana_checkout_spike.png"


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish a demo alert to AegisPilot")
    ap.add_argument("--url", default="http://localhost:8080", help="AegisPilot base URL")
    ap.add_argument("--service", default="checkout-svc")
    ap.add_argument("--alert", default="HighErrorRate")
    ap.add_argument("--error-rate", default="42%")
    ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    ap.add_argument("--scenario", choices=["checkout", "cart", "payments", "rotate"],
                    help="Use a predefined scenario's alert (overrides --service etc.)")
    ap.add_argument("--pubsub", action="store_true",
                    help="Publish to the real Pub/Sub topic instead of the HTTP endpoint")
    args = ap.parse_args()

    if args.scenario:
        import sys as _sys
        from pathlib import Path as _Path

        _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
        from backend.seed.scenarios import BY_KEY, next_scenario

        sc = next_scenario() if args.scenario == "rotate" else BY_KEY[args.scenario]
        payload = dict(sc.alert)
    else:
        payload = {
            "alert": args.alert,
            "service": args.service,
            "error_rate": args.error_rate,
            "grafana_snapshot": args.snapshot,
        }

    # --- Real Pub/Sub path: publish straight to the topic (proves the cloud
    #     ingestion path triggers an incident, not the in-process shortcut). ---
    if args.pubsub:
        import asyncio
        import sys as _sys
        from pathlib import Path as _Path

        _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
        from backend.config import get_settings
        from backend.models import Alert
        from backend.services.pubsub_bus import PubSubBus

        s = get_settings()
        s.apply_google_env()
        bus = PubSubBus(s.google_cloud_project)
        bus.ensure()
        asyncio.run(bus.publish(Alert(**payload)))
        print("[OK] Alert published to Pub/Sub topic 'incident-alerts':")
        print(f"    {payload['alert']} on {payload['service']} @ {payload['error_rate']}")
        print("  The subscriber will trigger the incident. Watch the war room.")
        return 0

    try:
        resp = httpx.post(f"{args.url}/api/alerts", json=payload, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[FAIL] Failed to publish alert: {exc}", file=sys.stderr)
        print("  Is the backend running?  uvicorn backend.main:app --port 8080", file=sys.stderr)
        return 1

    print("[OK] Alert published to AegisPilot event bus:")
    print(f"    {payload['alert']} on {payload['service']} @ {payload['error_rate']}")
    print(f"  Response: {resp.json()}")
    print("  Watch the war room -- the agents are now working the incident.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
