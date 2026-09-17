"""Memory tools — vector-similarity search over past-incident fingerprints.

Real cosine similarity (see services/embedding.py) over locally-embedded
fingerprints. Returns ranked matches so the Memory agent can say
"seen 2x before, resolved in ~4m via rollback" with an actual similarity score.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.models import Incident, IncidentMemory, IncidentStatus
from backend.services.embedding import cosine_similarity, embed_text
from backend.services.storage import StorageService

# Two incidents whose fingerprints are at least this similar are treated as the
# same recurring problem — we update that memory rather than create a duplicate.
_SAME_INCIDENT = 0.85


@dataclass
class MemoryMatch:
    memory: IncidentMemory
    similarity: float


def search_memory(
    storage: StorageService, fingerprint: str, top_k: int = 3
) -> list[MemoryMatch]:
    query = embed_text(fingerprint)
    matches = [
        MemoryMatch(memory=m, similarity=round(cosine_similarity(query, m.embedding), 4))
        for m in storage.all_memories()
    ]
    matches.sort(key=lambda x: x.similarity, reverse=True)
    return matches[:top_k]


def learn_incident(storage: StorageService, incident: Incident) -> IncidentMemory | None:
    """Closed-loop learning: write a resolved incident back into memory.

    This is what makes 'the AI learns' real — after an incident RESOLVES, its
    fingerprint + confirmed cause + effective fix + resolution time are persisted.
    The next time a similar incident fires (even one a judge invents live), the
    Memory agent recalls it: "seen before, resolved in Xm via Y". Recurring
    fingerprints update the existing entry (running average) instead of
    duplicating, so the store gets smarter, not just bigger.
    """
    if incident.status != IncidentStatus.RESOLVED or not incident.fingerprint:
        return None

    fp = incident.fingerprint
    emb = embed_text(fp)
    cause = incident.probable_cause or "Root cause confirmed during this incident."
    fix = (incident.remediation_plan.action if incident.remediation_plan else "rollback")
    res_min = incident.findings.get("comms", {}).get("resolution_minutes")
    if not res_min and incident.resolved_at and incident.detected_at:
        res_min = round((incident.resolved_at - incident.detected_at) / 60000.0, 1)
    res_min = float(res_min or 5.0)

    # Is this a recurrence of something we already know?
    best: IncidentMemory | None = None
    best_sim = 0.0
    for m in storage.all_memories():
        s = cosine_similarity(emb, m.embedding)
        if s > best_sim:
            best_sim, best = s, m

    if best is not None and best_sim >= _SAME_INCIDENT:
        n = len(best.past_incident_ids)
        updated = IncidentMemory(
            fingerprint_id=best.fingerprint_id, fingerprint=best.fingerprint,
            embedding=best.embedding,
            past_incident_ids=best.past_incident_ids + [incident.id],
            typical_cause=best.typical_cause, typical_fix=best.typical_fix,
            # Running average of resolution time across occurrences.
            avg_resolution_minutes=round((best.avg_resolution_minutes * n + res_min) / (n + 1), 1),
        )
        storage.add_memory(updated)
        return updated

    learned = IncidentMemory(
        fingerprint_id=f"fp_learned_{incident.id}", fingerprint=fp, embedding=emb,
        past_incident_ids=[incident.id], typical_cause=cause,
        typical_fix=f"Resolved via {fix}.", avg_resolution_minutes=res_min,
    )
    storage.add_memory(learned)
    return learned
