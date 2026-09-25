
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.models import Incident, IncidentMemory, IncidentStatus,  RemediationPlan
from backend.tools.memory import learn_incident, search_memory

def make_incident(**overrides):
    defaults = {
        "id": "inc-1",
        "status": IncidentStatus.RESOLVED,
        "fingerprint": "payment-api high error rate",
        "probable_cause": "database connection pool exhausted",
        "remediation_plan": RemediationPlan(
            action="restart service",
            target="payment-api",
            risk="low",
            reversible=True,
        ),
        "findings": {},
    }
    defaults.update(overrides)
    return Incident(**defaults)

def test_search_memory_returns_ranked_matches():
    storage = MagicMock()

    memories = [
        IncidentMemory(
            fingerprint_id="fp-1",
            fingerprint="payment api errors",
            embedding=[1.0, 0.0],
            past_incident_ids=["inc-old"],
            typical_cause="database",
            typical_fix="rollback",
            avg_resolution_minutes=4.0,
        ),
        IncidentMemory(
            fingerprint_id="fp-2",
            fingerprint="payment api timeout",
            embedding=[0.0, 1.0],
            past_incident_ids=["inc-old-2"],
            typical_cause="network",
            typical_fix="restart",
            avg_resolution_minutes=6.0,
        ),
    ]
    storage.all_memories.return_value = memories

    with __import__("unittest").mock.patch(
        "backend.tools.memory.embed_text",
        return_value=[1.0, 0.0],
    ):
        results = search_memory(storage, "payment api error", top_k=2)

    assert len(results) == 2
    assert results[0].memory.fingerprint_id == "fp-1"
    assert results[0].similarity == 1.0
    assert results[1].memory.fingerprint_id == "fp-2"
    assert results[1].similarity == 0.0


def test_search_memory_respects_top_k():
    storage = MagicMock()

    memories = [
        IncidentMemory(
            fingerprint_id=f"fp-{i}",
            fingerprint=f"incident {i}",
            embedding=[1.0, 0.0],
            past_incident_ids=[f"inc-{i}"],
            typical_cause="cause",
            typical_fix="fix",
            avg_resolution_minutes=5.0,
        )
        for i in range(5)
    ]
    storage.all_memories.return_value = memories

    with __import__("unittest").mock.patch(
        "backend.tools.memory.embed_text",
        return_value=[1.0, 0.0],
    ):
        results = search_memory(storage, "incident", top_k=2)

    assert len(results) == 2


def test_learn_incident_ignores_unresolved_incident():
    storage = MagicMock()

    incident = make_incident(status=IncidentStatus.DETECTED)

    result = learn_incident(storage, incident)

    assert result is None
    storage.add_memory.assert_not_called()


def test_learn_incident_ignores_incident_without_fingerprint():
    storage = MagicMock()

    incident = make_incident(fingerprint=None)

    result = learn_incident(storage, incident)

    assert result is None
    storage.add_memory.assert_not_called()


def test_learn_incident_creates_new_memory():
    storage = MagicMock()
    storage.all_memories.return_value = []

    incident = make_incident()

    with __import__("unittest").mock.patch(
        "backend.tools.memory.embed_text",
        return_value=[1.0, 0.0],
    ):
        result = learn_incident(storage, incident)

    assert result is not None
    assert result.fingerprint_id == "fp_learned_inc-1"
    assert result.fingerprint == incident.fingerprint
    assert result.embedding == [1.0, 0.0]
    assert result.past_incident_ids == ["inc-1"]
    assert result.typical_cause == "database connection pool exhausted"
    assert result.typical_fix == "Resolved via restart service."
    assert result.avg_resolution_minutes == 5.0
    storage.add_memory.assert_called_once_with(result)


def test_learn_incident_uses_resolution_time_from_comms():
    storage = MagicMock()
    storage.all_memories.return_value = []

    incident = make_incident(
        findings={"comms": {"resolution_minutes": 12}},
    )

    with __import__("unittest").mock.patch(
        "backend.tools.memory.embed_text",
        return_value=[1.0, 0.0],
    ):
        result = learn_incident(storage, incident)

    assert result.avg_resolution_minutes == 12.0


def test_learn_incident_calculates_resolution_time_from_timestamps():
    storage = MagicMock()
    storage.all_memories.return_value = []

    incident = make_incident(
        detected_at=100000,
        resolved_at=106000,
    )

    with __import__("unittest").mock.patch(
        "backend.tools.memory.embed_text",
        return_value=[1.0, 0.0],
    ):
        result = learn_incident(storage, incident)

    assert result.avg_resolution_minutes == 0.1


def test_learn_incident_uses_default_resolution_time():
    storage = MagicMock()
    storage.all_memories.return_value = []

    incident = make_incident()

    with __import__("unittest").mock.patch(
        "backend.tools.memory.embed_text",
        return_value=[1.0, 0.0],
    ):
        result = learn_incident(storage, incident)

    assert result.avg_resolution_minutes == 5.0


def test_learn_incident_updates_recurring_memory():
    storage = MagicMock()

    existing = IncidentMemory(
        fingerprint_id="fp-existing",
        fingerprint="payment api errors",
        embedding=[1.0, 0.0],
        past_incident_ids=["inc-old", "inc-old-2"],
        typical_cause="database",
        typical_fix="Resolved via rollback.",
        avg_resolution_minutes=6.0,
    )
    storage.all_memories.return_value = [existing]

    incident = make_incident(
        id="inc-new",
        findings={"comms": {"resolution_minutes": 12}},
    )

    with __import__("unittest").mock.patch(
        "backend.tools.memory.embed_text",
        return_value=[1.0, 0.0],
    ):
        result = learn_incident(storage, incident)

    assert result.fingerprint_id == "fp-existing"
    assert result.past_incident_ids == ["inc-old", "inc-old-2", "inc-new"]
    assert result.typical_cause == "database"
    assert result.typical_fix == "Resolved via rollback."
    assert result.avg_resolution_minutes == 8.0
    storage.add_memory.assert_called_once_with(result)
