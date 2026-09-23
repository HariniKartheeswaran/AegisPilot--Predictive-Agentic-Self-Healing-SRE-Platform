from unittest.mock import MagicMock, patch

from backend.models import (
    AgentRegistryEntry,
    Alert,
    AuditStep,
    Deploy,
    Incident,
    IncidentMemory,
    IncidentStatus,
    LogLine,
    Severity,
)
from backend.services.firestore_storage import FirestoreStorage


def make_storage():
    db = MagicMock()

    with patch(
        "backend.services.firestore_storage.firestore.Client",
        return_value=db,
    ):
        storage = FirestoreStorage(project="test-project")

    return storage, db


def make_snapshot(data, exists=True):
    snapshot = MagicMock()
    snapshot.exists = exists
    snapshot.to_dict.return_value = data
    return snapshot


def make_incident(
    incident_id="inc-001",
    detected_at=100,
    status=IncidentStatus.DETECTED,
):
    return Incident(
        id=incident_id,
        status=status,
        severity=Severity.SEV2,
        service="checkout",
        blast_radius="checkout-api",
        detected_at=detected_at,
        probable_cause="High error rate",
        confidence=0.95,
        fingerprint="fingerprint-001",
        findings={"error_rate": "25%"},
        alert=Alert(
            alert="HighErrorRate",
            service="checkout",
            error_rate="25%",
            metadata={"source": "test"},
        ),
    )


def make_deploy(
    deploy_id="deploy-001",
    deployed_at=100,
):
    return Deploy(
        id=deploy_id,
        service="checkout",
        version="v1.2.3",
        deployed_at=deployed_at,
        deployed_by="jenkins",
        commit_sha="abc123",
    )


def make_log(
    log_id="log-001",
    ts=100,
):
    return LogLine(
        id=log_id,
        service="checkout",
        ts=ts,
        level="ERROR",
        message="Payment service failed",
    )


def make_memory(fingerprint_id="fp-001"):
    return IncidentMemory(
        fingerprint_id=fingerprint_id,
        fingerprint="high-error-checkout",
        embedding=[0.1, 0.2, 0.3],
        past_incident_ids=["inc-001"],
        typical_cause="Bad deployment",
        typical_fix="Rollback",
        avg_resolution_minutes=12.5,
    )


def make_agent(agent_id="agent-001"):
    return AgentRegistryEntry(
        id=agent_id,
        name="triage-agent",
        version="1.0.0",
        model="gemini",
        allowed_tools=["triage"],
        scope="incident-analysis",
    )


def make_audit_step(
    step_id="audit-001",
    incident_id="inc-001",
    ts=100,
):
    return AuditStep(
        id=step_id,
        incident_id=incident_id,
        agent="triage-agent",
        step="analyze",
        input="High error rate detected",
        output="Likely deployment issue",
        ts=ts,
    )


def test_firestore_storage_initializes_client():
    db = MagicMock()

    with patch(
        "backend.services.firestore_storage.firestore.Client",
        return_value=db,
    ) as client:
        storage = FirestoreStorage(
            project="test-project",
            database="test-db",
        )

    assert storage._db is db

    client.assert_called_once_with(
        project="test-project",
        database="test-db",
    )


def test_firestore_storage_allows_empty_project():
    db = MagicMock()

    with patch(
        "backend.services.firestore_storage.firestore.Client",
        return_value=db,
    ) as client:
        FirestoreStorage(project="")

    client.assert_called_once_with(
        project=None,
        database="(default)",
    )


def test_init_schema_creates_and_deletes_healthcheck():
    storage, db = make_storage()

    collection = db.collection.return_value
    document = collection.document.return_value

    storage.init_schema()

    db.collection.assert_called_once_with("_healthcheck")
    collection.document.assert_called_once_with("ping")
    document.set.assert_called_once_with({"ok": True})
    document.delete.assert_called_once()


def test_save_incident_writes_incident_document():
    storage, db = make_storage()

    incident = make_incident()

    incidents_collection = db.collection.return_value
    incident_document = incidents_collection.document.return_value

    storage.save_incident(incident)

    db.collection.assert_called_with("incidents")
    incidents_collection.document.assert_called_once_with(incident.id)

    saved_data = incident_document.set.call_args.args[0]

    assert saved_data["id"] == incident.id
    assert saved_data["status"] == incident.status.value
    assert saved_data["severity"] == incident.severity.value
    assert saved_data["service"] == incident.service
    assert saved_data["probable_cause"] == incident.probable_cause
    assert saved_data["confidence"] == incident.confidence
    assert saved_data["findings"] == incident.findings
    assert saved_data["alert"] == incident.alert.model_dump()


def test_get_incident_returns_incident_when_document_exists():
    storage, db = make_storage()

    incident = make_incident()
    incident_data = storage._incident_doc(incident)

    incidents_collection = db.collection.return_value
    incident_document = incidents_collection.document.return_value
    incident_document.get.return_value = make_snapshot(incident_data)

    result = storage.get_incident(incident.id)

    assert result is not None
    assert result.id == incident.id
    assert result.status == incident.status
    assert result.severity == incident.severity
    assert result.service == incident.service
    assert result.alert is not None
    assert result.alert.service == "checkout"

    incidents_collection.document.assert_called_once_with(incident.id)


def test_get_incident_returns_none_when_document_does_not_exist():
    storage, db = make_storage()

    incidents_collection = db.collection.return_value
    incident_document = incidents_collection.document.return_value
    incident_document.get.return_value = make_snapshot({}, exists=False)

    result = storage.get_incident("missing-incident")

    assert result is None

    incidents_collection.document.assert_called_once_with(
        "missing-incident"
    )


def test_list_incidents_sorts_by_detected_at_descending():
    storage, db = make_storage()

    incident_older = make_incident(
        incident_id="inc-old",
        detected_at=100,
    )
    incident_newer = make_incident(
        incident_id="inc-new",
        detected_at=300,
    )
    incident_middle = make_incident(
        incident_id="inc-middle",
        detected_at=200,
    )

    incidents_collection = db.collection.return_value

    snapshots = [
        make_snapshot(storage._incident_doc(incident_older)),
        make_snapshot(storage._incident_doc(incident_newer)),
        make_snapshot(storage._incident_doc(incident_middle)),
    ]

    incidents_collection.stream.return_value = snapshots

    result = storage.list_incidents()

    assert [incident.id for incident in result] == [
        "inc-new",
        "inc-middle",
        "inc-old",
    ]


def test_add_deploy_writes_deploy_document():
    storage, db = make_storage()

    deploy = make_deploy()

    deploys_collection = db.collection.return_value
    deploy_document = deploys_collection.document.return_value

    storage.add_deploy(deploy)

    db.collection.assert_called_with("deploys")
    deploys_collection.document.assert_called_once_with(deploy.id)
    deploy_document.set.assert_called_once_with(deploy.model_dump())


def test_deploys_for_service_filters_and_sorts_deployments():
    storage, db = make_storage()

    deploy_old = make_deploy(
        deploy_id="deploy-old",
        deployed_at=100,
    )
    deploy_new = make_deploy(
        deploy_id="deploy-new",
        deployed_at=300,
    )

    deploys_collection = db.collection.return_value
    filtered_query = deploys_collection.where.return_value

    filtered_query.stream.return_value = [
        make_snapshot(deploy_old.model_dump()),
        make_snapshot(deploy_new.model_dump()),
    ]

    result = storage.deploys_for_service("checkout")

    assert [deploy.id for deploy in result] == [
        "deploy-new",
        "deploy-old",
    ]

    deploys_collection.where.assert_called_once()


def test_add_log_writes_log_document():
    storage, db = make_storage()

    log = make_log()

    logs_collection = db.collection.return_value
    log_document = logs_collection.document.return_value

    storage.add_log(log)

    db.collection.assert_called_with("logs")
    logs_collection.document.assert_called_once_with(log.id)
    log_document.set.assert_called_once_with(log.model_dump())


def test_logs_for_service_filters_sorts_and_limits_logs():
    storage, db = make_storage()

    logs = [
        make_log(log_id="log-1", ts=100),
        make_log(log_id="log-2", ts=300),
        make_log(log_id="log-3", ts=200),
    ]

    logs_collection = db.collection.return_value
    filtered_query = logs_collection.where.return_value

    filtered_query.stream.return_value = [
        make_snapshot(log.model_dump()) for log in logs
    ]

    result = storage.logs_for_service("checkout", limit=2)

    assert [log.id for log in result] == [
        "log-2",
        "log-3",
    ]

    logs_collection.where.assert_called_once()


def test_add_memory_writes_memory_document():
    storage, db = make_storage()

    memory = make_memory()

    memory_collection = db.collection.return_value
    memory_document = memory_collection.document.return_value

    storage.add_memory(memory)

    db.collection.assert_called_with("incident_memory")
    memory_collection.document.assert_called_once_with(
        memory.fingerprint_id
    )
    memory_document.set.assert_called_once_with(memory.model_dump())


def test_all_memories_returns_all_memories():
    storage, db = make_storage()

    memories = [
        make_memory("fp-001"),
        make_memory("fp-002"),
    ]

    memory_collection = db.collection.return_value

    memory_collection.stream.return_value = [
        make_snapshot(memory.model_dump()) for memory in memories
    ]

    result = storage.all_memories()

    assert [memory.fingerprint_id for memory in result] == [
        "fp-001",
        "fp-002",
    ]


def test_upsert_agent_writes_agent_document():
    storage, db = make_storage()

    entry = make_agent()

    registry_collection = db.collection.return_value
    registry_document = registry_collection.document.return_value

    storage.upsert_agent(entry)

    db.collection.assert_called_with("agent_registry")
    registry_collection.document.assert_called_once_with(entry.id)
    registry_document.set.assert_called_once_with(entry.model_dump())


def test_list_agents_returns_registered_agents():
    storage, db = make_storage()

    agents = [
        make_agent("agent-001"),
        make_agent("agent-002"),
    ]

    registry_collection = db.collection.return_value

    registry_collection.stream.return_value = [
        make_snapshot(agent.model_dump()) for agent in agents
    ]

    result = storage.list_agents()

    assert [agent.id for agent in result] == [
        "agent-001",
        "agent-002",
    ]


def test_add_audit_step_writes_audit_document():
    storage, db = make_storage()

    step = make_audit_step()

    audit_collection = db.collection.return_value
    audit_document = audit_collection.document.return_value

    storage.add_audit_step(step)

    db.collection.assert_called_with("audit_log")
    audit_collection.document.assert_called_once_with(step.id)
    audit_document.set.assert_called_once_with(step.model_dump())


def test_audit_for_incident_filters_and_sorts_by_timestamp():
    storage, db = make_storage()

    step_newer = make_audit_step(
        step_id="audit-2",
        ts=200,
    )
    step_older = make_audit_step(
        step_id="audit-1",
        ts=100,
    )

    audit_collection = db.collection.return_value
    filtered_query = audit_collection.where.return_value

    filtered_query.stream.return_value = [
        make_snapshot(step_newer.model_dump()),
        make_snapshot(step_older.model_dump()),
    ]

    result = storage.audit_for_incident("inc-001")

    assert [step.id for step in result] == [
        "audit-1",
        "audit-2",
    ]

    audit_collection.where.assert_called_once()