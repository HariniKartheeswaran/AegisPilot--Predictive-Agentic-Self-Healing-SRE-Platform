"""Seed the demo fixtures into real Firestore (upgrade spec Phase 3).

Ports the same fixtures used by the local build into Firestore collections:
deploys, logs, incident_memory, agent_registry. Idempotent — documents are keyed
by stable ids, so re-running just refreshes them.

Run:  python scripts/seed_firestore.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings  # noqa: E402

get_settings().apply_google_env()

from backend.seed.seed_data import seed_all  # noqa: E402
from backend.services.firestore_storage import FirestoreStorage  # noqa: E402


def main() -> int:
    s = get_settings()
    print(f"Seeding Firestore in project {s.google_cloud_project!r} ...")
    store = FirestoreStorage(s.google_cloud_project)
    try:
        store.init_schema()  # round-trip: fails loud if Firestore isn't Native-mode/reachable
    except Exception as exc:  # noqa: BLE001 — report the exact error, don't hide it
        print(f"[FAIL] Firestore not reachable: {type(exc).__name__}: {exc}")
        print("  Ensure a Native-mode Firestore database exists in this project.")
        return 1
    seed_all(store, s.gemini_model)
    counts = {
        "deploys": len(store.deploys_for_service("checkout-svc")),
        "memories": len(store.all_memories()),
        "agents": len(store.list_agents()),
    }
    print(f"[OK] Seeded Firestore. checkout-svc deploys={counts['deploys']}, "
          f"memories={counts['memories']}, agents={counts['agents']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
