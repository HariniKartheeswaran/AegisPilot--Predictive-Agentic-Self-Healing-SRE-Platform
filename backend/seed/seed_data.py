"""Seed the demo fixtures for all rotating scenarios.

Scenario data (deploys/logs/memory per service) lives in `scenarios.py`; this
module wires it into a StorageService plus the agent registry. The autonomy is
what's judged, not live prod infra (spec §4), so we seed realistic fixtures for
three distinct incidents that the pipeline reads on every run.
"""
from __future__ import annotations

from backend.models import AgentRegistryEntry
from backend.seed import scenarios
from backend.services.storage import StorageService


def _registry(model: str) -> list[AgentRegistryEntry]:
    v = "1.0.0"
    return [
        AgentRegistryEntry(id="ag_orch", name="Orchestrator", version=v, model=model,
            allowed_tools=["subagent_handoff", "state_writer"],
            scope="Owns incident lifecycle + state machine"),
        AgentRegistryEntry(id="ag_triage", name="Triage", version=v, model=model,
            allowed_tools=["severity_classifier", "service_resolver"],
            scope="Classify severity, service, blast radius, routing"),
        AgentRegistryEntry(id="ag_diag", name="Diagnosis", version=v, model=model,
            allowed_tools=["log_fetcher", "grafana_vision", "log_classifier"],
            scope="Summarize logs + read Grafana image (vision)"),
        AgentRegistryEntry(id="ag_corr", name="Correlation", version=v, model=model,
            allowed_tools=["deploy_history_query", "change_correlator"],
            scope="Correlate deploys/changes to incident window"),
        AgentRegistryEntry(id="ag_mem", name="Memory", version=v, model=model,
            allowed_tools=["incident_memory_search"],
            scope="Vector-similarity search over past incident fingerprints"),
        AgentRegistryEntry(id="ag_rem", name="Remediation", version=v, model=model,
            allowed_tools=["remediation_planner", "approval_gate", "executor"],
            scope="Propose reversible fix; HALT for human approval"),
        AgentRegistryEntry(id="ag_comms", name="Comms", version=v, model=model,
            allowed_tools=["rca_writer", "slack_poster", "ticket_filer"],
            scope="Generate RCA + timeline; post Slack; file ticket"),
    ]


def refresh_demo_timeline(storage: StorageService) -> None:
    """Keep every scenario's bad deploy ~12 min before its alert (see
    scenarios.refresh_all_timelines). Called at the start of each incident."""
    scenarios.refresh_all_timelines(storage)


def seed_all(storage: StorageService, model: str) -> None:
    """Idempotent seed: all scenarios' deploys/logs (fresh), memories, registry."""
    scenarios.refresh_all_timelines(storage)
    for m in scenarios.all_memories():
        storage.add_memory(m)
    for a in _registry(model):
        storage.upsert_agent(a)


if __name__ == "__main__":
    from backend.config import get_settings
    from backend.seed.generate_grafana import generate_all
    from backend.services.storage import SQLiteStorage

    settings = get_settings()
    store = SQLiteStorage(settings.db_path)
    store.init_schema()
    seed_all(store, settings.gemini_model)
    imgs = generate_all()
    print(f"Seeded DB at {settings.db_path}")
    for name, path in imgs.items():
        print(f"  grafana[{name}] -> {path}")
