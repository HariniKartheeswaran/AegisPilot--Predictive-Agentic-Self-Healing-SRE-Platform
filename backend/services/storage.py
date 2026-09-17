"""Persistence layer.

`StorageService` is the abstract interface every part of the app codes against.
`SQLiteStorage` is the local, zero-billing implementation. The tables mirror the
future Firestore collections 1:1, so migrating to cloud is a matter of writing a
`FirestoreStorage(StorageService)` and changing ONE line in main.py — nothing
else in the codebase moves. That is the "cloud-portable" story in the README.
"""
from __future__ import annotations

import abc
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from backend.models import (
    AgentRegistryEntry,
    AuditStep,
    Deploy,
    Incident,
    IncidentMemory,
    IncidentStatus,
    LogLine,
    RemediationPlan,
    Severity,
)


class StorageService(abc.ABC):
    """The seam. A Firestore impl would satisfy exactly this contract."""

    # --- lifecycle ---
    @abc.abstractmethod
    def init_schema(self) -> None: ...

    # --- incidents ---
    @abc.abstractmethod
    def save_incident(self, incident: Incident) -> None: ...
    @abc.abstractmethod
    def get_incident(self, incident_id: str) -> Optional[Incident]: ...
    @abc.abstractmethod
    def list_incidents(self) -> list[Incident]: ...

    # --- deploys ---
    @abc.abstractmethod
    def add_deploy(self, deploy: Deploy) -> None: ...
    @abc.abstractmethod
    def deploys_for_service(self, service: str) -> list[Deploy]: ...

    # --- logs ---
    @abc.abstractmethod
    def add_log(self, log: LogLine) -> None: ...
    @abc.abstractmethod
    def logs_for_service(self, service: str, limit: int = 200) -> list[LogLine]: ...

    # --- incident memory ---
    @abc.abstractmethod
    def add_memory(self, mem: IncidentMemory) -> None: ...
    @abc.abstractmethod
    def all_memories(self) -> list[IncidentMemory]: ...

    # --- agent registry ---
    @abc.abstractmethod
    def upsert_agent(self, entry: AgentRegistryEntry) -> None: ...
    @abc.abstractmethod
    def list_agents(self) -> list[AgentRegistryEntry]: ...

    # --- audit ---
    @abc.abstractmethod
    def add_audit_step(self, step: AuditStep) -> None: ...
    @abc.abstractmethod
    def audit_for_incident(self, incident_id: str) -> list[AuditStep]: ...


# --------------------------------------------------------------------------- #
# SQLite implementation
# --------------------------------------------------------------------------- #
class SQLiteStorage(StorageService):
    def __init__(self, db_path: Path | str) -> None:
        self._path = str(db_path)
        # check_same_thread=False + a lock: FastAPI hits this from worker
        # threads (we run blocking model calls via asyncio.to_thread).
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    # -- helpers --
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -- schema --
    def init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY, status TEXT, severity TEXT, service TEXT,
                blast_radius TEXT, detected_at INTEGER, probable_cause TEXT,
                confidence REAL, remediation_plan TEXT, approved_by TEXT,
                resolved_at INTEGER, rca_doc TEXT, fingerprint TEXT,
                findings TEXT, alert TEXT
            );
            CREATE TABLE IF NOT EXISTS deploys (
                id TEXT PRIMARY KEY, service TEXT, version TEXT,
                deployed_at INTEGER, deployed_by TEXT, commit_sha TEXT,
                rollback_target TEXT
            );
            CREATE TABLE IF NOT EXISTS logs (
                id TEXT PRIMARY KEY, service TEXT, ts INTEGER, level TEXT,
                message TEXT, log_class TEXT
            );
            CREATE TABLE IF NOT EXISTS incident_memory (
                fingerprint_id TEXT PRIMARY KEY, fingerprint TEXT, embedding TEXT,
                past_incident_ids TEXT, typical_cause TEXT, typical_fix TEXT,
                avg_resolution_minutes REAL
            );
            CREATE TABLE IF NOT EXISTS agent_registry (
                id TEXT PRIMARY KEY, name TEXT, version TEXT, model TEXT,
                allowed_tools TEXT, scope TEXT, status TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY, incident_id TEXT, agent TEXT, step TEXT,
                input TEXT, reasoning TEXT, tool_call TEXT, output TEXT,
                tokens INTEGER, latency_ms INTEGER, ts INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_audit_incident ON audit_log(incident_id);
            CREATE INDEX IF NOT EXISTS idx_logs_service ON logs(service);
            CREATE INDEX IF NOT EXISTS idx_deploys_service ON deploys(service);
            """
        )
        self._conn.commit()

    # -- incidents --
    def save_incident(self, incident: Incident) -> None:
        plan = incident.remediation_plan.model_dump_json() if incident.remediation_plan else None
        alert = incident.alert.model_dump_json() if incident.alert else None
        self._exec(
            """
            INSERT INTO incidents (id, status, severity, service, blast_radius,
                detected_at, probable_cause, confidence, remediation_plan,
                approved_by, resolved_at, rca_doc, fingerprint, findings, alert)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, severity=excluded.severity,
                service=excluded.service, blast_radius=excluded.blast_radius,
                probable_cause=excluded.probable_cause, confidence=excluded.confidence,
                remediation_plan=excluded.remediation_plan, approved_by=excluded.approved_by,
                resolved_at=excluded.resolved_at, rca_doc=excluded.rca_doc,
                fingerprint=excluded.fingerprint, findings=excluded.findings,
                alert=excluded.alert
            """,
            (
                incident.id,
                incident.status.value,
                incident.severity.value if incident.severity else None,
                incident.service,
                incident.blast_radius,
                incident.detected_at,
                incident.probable_cause,
                incident.confidence,
                plan,
                incident.approved_by,
                incident.resolved_at,
                incident.rca_doc,
                incident.fingerprint,
                json.dumps(incident.findings, default=str),
                alert,
            ),
        )

    def _row_to_incident(self, r: sqlite3.Row) -> Incident:
        from backend.models import Alert

        return Incident(
            id=r["id"],
            status=IncidentStatus(r["status"]),
            severity=Severity(r["severity"]) if r["severity"] else None,
            service=r["service"] or "",
            blast_radius=r["blast_radius"],
            detected_at=r["detected_at"],
            probable_cause=r["probable_cause"],
            confidence=r["confidence"],
            remediation_plan=RemediationPlan(**json.loads(r["remediation_plan"]))
            if r["remediation_plan"]
            else None,
            approved_by=r["approved_by"],
            resolved_at=r["resolved_at"],
            rca_doc=r["rca_doc"],
            fingerprint=r["fingerprint"],
            findings=json.loads(r["findings"]) if r["findings"] else {},
            alert=Alert(**json.loads(r["alert"])) if r["alert"] else None,
        )

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        rows = self._query("SELECT * FROM incidents WHERE id=?", (incident_id,))
        return self._row_to_incident(rows[0]) if rows else None

    def list_incidents(self) -> list[Incident]:
        rows = self._query("SELECT * FROM incidents ORDER BY detected_at DESC")
        return [self._row_to_incident(r) for r in rows]

    # -- deploys --
    def add_deploy(self, deploy: Deploy) -> None:
        self._exec(
            """INSERT OR REPLACE INTO deploys
               (id, service, version, deployed_at, deployed_by, commit_sha, rollback_target)
               VALUES (?,?,?,?,?,?,?)""",
            (
                deploy.id, deploy.service, deploy.version, deploy.deployed_at,
                deploy.deployed_by, deploy.commit_sha, deploy.rollback_target,
            ),
        )

    def deploys_for_service(self, service: str) -> list[Deploy]:
        rows = self._query(
            "SELECT * FROM deploys WHERE service=? ORDER BY deployed_at DESC", (service,)
        )
        return [Deploy(**dict(r)) for r in rows]

    # -- logs --
    def add_log(self, log: LogLine) -> None:
        self._exec(
            """INSERT OR REPLACE INTO logs (id, service, ts, level, message, log_class)
               VALUES (?,?,?,?,?,?)""",
            (log.id, log.service, log.ts, log.level, log.message, log.log_class),
        )

    def logs_for_service(self, service: str, limit: int = 200) -> list[LogLine]:
        rows = self._query(
            "SELECT * FROM logs WHERE service=? ORDER BY ts DESC LIMIT ?", (service, limit)
        )
        return [LogLine(**dict(r)) for r in rows]

    # -- memory --
    def add_memory(self, mem: IncidentMemory) -> None:
        self._exec(
            """INSERT OR REPLACE INTO incident_memory
               (fingerprint_id, fingerprint, embedding, past_incident_ids,
                typical_cause, typical_fix, avg_resolution_minutes)
               VALUES (?,?,?,?,?,?,?)""",
            (
                mem.fingerprint_id, mem.fingerprint, json.dumps(mem.embedding),
                json.dumps(mem.past_incident_ids), mem.typical_cause,
                mem.typical_fix, mem.avg_resolution_minutes,
            ),
        )

    def all_memories(self) -> list[IncidentMemory]:
        rows = self._query("SELECT * FROM incident_memory")
        return [
            IncidentMemory(
                fingerprint_id=r["fingerprint_id"],
                fingerprint=r["fingerprint"],
                embedding=json.loads(r["embedding"]),
                past_incident_ids=json.loads(r["past_incident_ids"]),
                typical_cause=r["typical_cause"],
                typical_fix=r["typical_fix"],
                avg_resolution_minutes=r["avg_resolution_minutes"],
            )
            for r in rows
        ]

    # -- registry --
    def upsert_agent(self, entry: AgentRegistryEntry) -> None:
        self._exec(
            """INSERT OR REPLACE INTO agent_registry
               (id, name, version, model, allowed_tools, scope, status)
               VALUES (?,?,?,?,?,?,?)""",
            (
                entry.id, entry.name, entry.version, entry.model,
                json.dumps(entry.allowed_tools), entry.scope, entry.status,
            ),
        )

    def list_agents(self) -> list[AgentRegistryEntry]:
        rows = self._query("SELECT * FROM agent_registry")
        return [
            AgentRegistryEntry(
                id=r["id"], name=r["name"], version=r["version"], model=r["model"],
                allowed_tools=json.loads(r["allowed_tools"]), scope=r["scope"],
                status=r["status"],
            )
            for r in rows
        ]

    # -- audit --
    def add_audit_step(self, step: AuditStep) -> None:
        self._exec(
            """INSERT OR REPLACE INTO audit_log
               (id, incident_id, agent, step, input, reasoning, tool_call,
                output, tokens, latency_ms, ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                step.id, step.incident_id, step.agent, step.step, step.input,
                step.reasoning, step.tool_call, step.output, step.tokens,
                step.latency_ms, step.ts,
            ),
        )

    def audit_for_incident(self, incident_id: str) -> list[AuditStep]:
        rows = self._query(
            "SELECT * FROM audit_log WHERE incident_id=? ORDER BY ts ASC", (incident_id,)
        )
        return [AuditStep(**dict(r)) for r in rows]
