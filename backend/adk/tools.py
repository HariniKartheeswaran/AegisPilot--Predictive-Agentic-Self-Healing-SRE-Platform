"""Real tools exposed to the ADK LlmAgents.

Each is a thin, JSON-returning wrapper over the existing deterministic tool
functions in backend/tools/*, bound per-incident via closure so the LLM calls
them with no bookkeeping args. Their return values are captured by the
after_tool_callback and become the authoritative basis for findings.

Governance note: the destructive executor is deliberately NOT exposed as a
tool — remediation execution runs in the orchestrator only after the human
approval gate resolves, so the model can never trigger a rollback on its own.
"""
from __future__ import annotations

from typing import Any

from google.adk.tools import FunctionTool

from backend.agents.base import RunContext
from backend.tools import correlation as C
from backend.tools import diagnosis as D
from backend.tools import memory as M
from backend.tools import remediation as R
from backend.tools import triage as T


def triage_tools(rc: RunContext) -> list[FunctionTool]:
    alert = rc.incident.alert

    def resolve_service_and_severity() -> dict:
        """Resolve the affected service's topology (tier, downstreams, traffic
        share) and compute the rubric severity + blast radius from the error
        rate. Call this first to ground your severity decision."""
        info = T.resolve_service(alert.service)
        err = T.parse_error_rate(alert.error_rate)
        return {
            "service": alert.service, "tier": info.tier,
            "downstreams": info.downstreams, "traffic_share": info.traffic_share,
            "error_rate_pct": err,
            "rubric_severity": T.classify_severity(err, info.tier),
            "blast_radius": T.estimate_blast_radius(info, err),
            "oncall": T.oncall_for(alert.service),
        }

    return [FunctionTool(resolve_service_and_severity)]


def diagnosis_tools(rc: RunContext) -> list[FunctionTool]:
    service = rc.incident.alert.service

    def fetch_and_classify_logs() -> dict:
        """Fetch the recent log stream for the affected service and classify each
        line into an operational failure class. Returns the class breakdown, the
        dominant failure class, the key error lines, and a stable fingerprint.
        Call this to see what the logs show."""
        raw = D.fetch_logs(rc.deps.storage, service)
        logs, analysis = D.analyze_logs(raw)
        fingerprint = D.build_fingerprint(service, analysis)
        # Side effect: the Memory agent's search needs the fingerprint.
        rc.incident.fingerprint = fingerprint
        rc.deps.storage.save_incident(rc.incident)
        top = [
            {"level": lg.level, "message": lg.message, "log_class": lg.log_class}
            for lg in sorted(logs, key=lambda l: l.ts)
            if lg.level.upper() in ("ERROR", "FATAL", "WARN")
        ][:8]
        return {
            "dominant_class": analysis.dominant_class,
            "class_counts": analysis.class_counts,
            "error_count": analysis.error_count,
            "total_lines": analysis.total,
            "fingerprint": fingerprint,
            "top_log_lines": top,
        }

    return [FunctionTool(fetch_and_classify_logs)]


def correlation_tools(rc: RunContext) -> list[FunctionTool]:
    service = rc.incident.alert.service
    detected_at = rc.incident.detected_at

    def query_recent_deploys() -> dict:
        """Query deploy history in the incident window and score each change by
        temporal proximity to detection (higher = shipped closer before, more
        suspicious). Returns the ranked suspects and the single strongest one.
        Call this to find the probable bad deploy."""
        deploys = C.deploys_in_window(rc.deps.storage, service, detected_at)
        suspects = C.correlate_changes(deploys, detected_at)
        ranked = [
            {"service": s.deploy.service, "version": s.deploy.version,
             "deployed_by": s.deploy.deployed_by, "commit_sha": s.deploy.commit_sha,
             "minutes_before": s.minutes_before, "proximity_score": s.proximity_score,
             "rollback_target": s.deploy.rollback_target}
            for s in suspects
        ]
        return {"ranked": ranked, "top_suspect": ranked[0] if ranked else None}

    return [FunctionTool(query_recent_deploys)]


def memory_tools(rc: RunContext) -> list[FunctionTool]:
    def search_incident_memory() -> dict:
        """Vector-similarity search over past-incident fingerprints. Returns the
        closest historical matches with cosine similarity and how each was
        resolved. Call this to check whether we've seen this before."""
        fingerprint = rc.incident.fingerprint or ""
        matches = M.search_memory(rc.deps.storage, fingerprint)
        return {
            "fingerprint": fingerprint,
            "matches": [
                {"similarity": m.similarity, "typical_cause": m.memory.typical_cause,
                 "typical_fix": m.memory.typical_fix,
                 "avg_resolution_minutes": m.memory.avg_resolution_minutes,
                 "times_seen": len(m.memory.past_incident_ids),
                 "past_incident_ids": m.memory.past_incident_ids}
                for m in matches
            ],
        }

    return [FunctionTool(search_incident_memory)]


def remediation_tools(rc: RunContext) -> list[FunctionTool]:
    service = rc.incident.alert.service

    def propose_remediation(action: str, rollback_target: str = "", rationale: str = "") -> dict:
        """Build a concrete, reversible remediation plan. action must be one of:
        rollback, scale_out, restart, flag_off. For a bad-deploy regression use
        'rollback' with the suspect's rollback_target. Returns the structured plan
        (including whether it needs human approval). Call this once you've decided."""
        act = (action or "rollback").strip().lower()
        if act not in R.ACTION_CATALOG:
            act = "rollback"
        plan = R.build_plan(act, service, rollback_target or None, rationale or f"{act} on {service}")
        return plan.model_dump()

    return [FunctionTool(propose_remediation)]
