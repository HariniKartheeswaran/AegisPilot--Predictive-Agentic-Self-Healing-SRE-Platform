"""FirestoreStorage — the cloud implementation of StorageService.

This is the payoff of the cloud-portable seam (upgrade spec Phase 3): the same
`StorageService` contract the whole app codes against, now backed by real
managed Firestore. SQLiteStorage is untouched and stays selectable via
BACKEND=local. Collections mirror the SQLite tables 1:1:

    incidents · deploys · logs · incident_memory · agent_registry · audit_log

Query design: we use equality-only `where` filters and sort/limit in Python, so
Firestore never demands a composite index — the demo works the instant the API
is enabled, with zero index provisioning.
"""
from __future__ import annotations

from typing import Optional

from google.cloud import firestore
from google.cloud.firestore import FieldFilter

from backend.models import (
    AgentRegistryEntry,
    Alert,
    AuditStep,
    Deploy,
    Incident,
    IncidentMemory,
    IncidentStatus,
    LogLine,
    RemediationPlan,
    Severity,
)
from backend.services.storage import StorageService


class FirestoreStorage(StorageService):
    def __init__(self, project: str, database: str = "(default)") -> None:
        # ADC supplies credentials; project pins the target GCP project.
        self._db = firestore.Client(project=project or None, database=database)

    # -- collections --
    @property
    def _incidents(self):
        return self._db.collection("incidents")

    @property
    def _deploys(self):
        return self._db.collection("deploys")

    @property
    def _logs(self):
        return self._db.collection("logs")

    @property
    def _memory(self):
        return self._db.collection("incident_memory")

    @property
    def _registry(self):
        return self._db.collection("agent_registry")

    @property
    def _audit(self):
        return self._db.collection("audit_log")

    # -- schema (no-op: Firestore is schemaless; collections autocreate) --
    def init_schema(self) -> None:
        # A trivial round-trip surfaces auth/mode errors early (fail loud, per
        # the upgrade rules) instead of on the first real write mid-demo.
        _ = self._db.collection("_healthcheck").document("ping")
        _.set({"ok": True})
        _.delete()

    # -- incidents --
    def _incident_doc(self, inc: Incident) -> dict:
        return {
            "id": inc.id,
            "status": inc.status.value,
            "severity": inc.severity.value if inc.severity else None,
            "service": inc.service,
            "blast_radius": inc.blast_radius,
            "detected_at": inc.detected_at,
            "probable_cause": inc.probable_cause,
            "confidence": inc.confidence,
            "remediation_plan": inc.remediation_plan.model_dump() if inc.remediation_plan else None,
            "approved_by": inc.approved_by,
            "resolved_at": inc.resolved_at,
            "rca_doc": inc.rca_doc,
            "fingerprint": inc.fingerprint,
            "findings": inc.findings or {},
            "alert": inc.alert.model_dump() if inc.alert else None,
        }

    def _doc_to_incident(self, d: dict) -> Incident:
        return Incident(
            id=d["id"],
            status=IncidentStatus(d["status"]),
            severity=Severity(d["severity"]) if d.get("severity") else None,
            service=d.get("service") or "",
            blast_radius=d.get("blast_radius"),
            detected_at=d.get("detected_at"),
            probable_cause=d.get("probable_cause"),
            confidence=d.get("confidence"),
            remediation_plan=RemediationPlan(**d["remediation_plan"]) if d.get("remediation_plan") else None,
            approved_by=d.get("approved_by"),
            resolved_at=d.get("resolved_at"),
            rca_doc=d.get("rca_doc"),
            fingerprint=d.get("fingerprint"),
            findings=d.get("findings") or {},
            alert=Alert(**d["alert"]) if d.get("alert") else None,
        )

    def save_incident(self, incident: Incident) -> None:
        self._incidents.document(incident.id).set(self._incident_doc(incident))

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        snap = self._incidents.document(incident_id).get()
        return self._doc_to_incident(snap.to_dict()) if snap.exists else None

    def list_incidents(self) -> list[Incident]:
        docs = [s.to_dict() for s in self._incidents.stream()]
        docs.sort(key=lambda d: d.get("detected_at", 0), reverse=True)
        return [self._doc_to_incident(d) for d in docs]

    # -- deploys --
    def add_deploy(self, deploy: Deploy) -> None:
        self._deploys.document(deploy.id).set(deploy.model_dump())

    def deploys_for_service(self, service: str) -> list[Deploy]:
        docs = [s.to_dict() for s in self._deploys.where(filter=FieldFilter("service", "==", service)).stream()]
        docs.sort(key=lambda d: d.get("deployed_at", 0), reverse=True)
        return [Deploy(**d) for d in docs]

    # -- logs --
    def add_log(self, log: LogLine) -> None:
        self._logs.document(log.id).set(log.model_dump())

    def logs_for_service(self, service: str, limit: int = 200) -> list[LogLine]:
        docs = [s.to_dict() for s in self._logs.where(filter=FieldFilter("service", "==", service)).stream()]
        docs.sort(key=lambda d: d.get("ts", 0), reverse=True)
        return [LogLine(**d) for d in docs[:limit]]

    # -- memory --
    def add_memory(self, mem: IncidentMemory) -> None:
        self._memory.document(mem.fingerprint_id).set(mem.model_dump())

    def all_memories(self) -> list[IncidentMemory]:
        return [IncidentMemory(**s.to_dict()) for s in self._memory.stream()]

    # -- registry --
    def upsert_agent(self, entry: AgentRegistryEntry) -> None:
        self._registry.document(entry.id).set(entry.model_dump())

    def list_agents(self) -> list[AgentRegistryEntry]:
        return [AgentRegistryEntry(**s.to_dict()) for s in self._registry.stream()]

    # -- audit --
    def add_audit_step(self, step: AuditStep) -> None:
        self._audit.document(step.id).set(step.model_dump())

    def audit_for_incident(self, incident_id: str) -> list[AuditStep]:
        docs = [s.to_dict() for s in self._audit.where(filter=FieldFilter("incident_id", "==", incident_id)).stream()]
        docs.sort(key=lambda d: d.get("ts", 0))
        return [AuditStep(**d) for d in docs]
