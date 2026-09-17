"""Memory Agent — recall past incidents with the same fingerprint.

Institutional memory (spec §5.4): a real cosine-similarity search over embedded
fingerprints of resolved incidents. If a strong prior exists, the agent phrases
an actionable recommendation ("seen 2x, resolved in ~4m via rollback") that the
Remediation agent prefers. Below a similarity floor we honestly report "no
strong prior" rather than hallucinate a match.
"""
from __future__ import annotations

from backend.agents.base import BaseAgent, RunContext
from backend.tools import memory as M

# A match below this cosine similarity is too weak to steer remediation.
SIMILARITY_FLOOR = 0.4

SYSTEM = (
    "You are the Memory agent of an autonomous SRE system. You are handed the "
    "best-matching past incident for the current fingerprint, with a cosine "
    "similarity score and how it was historically resolved. Phrase a short, "
    "actionable recommendation the responders can trust. Respond ONLY with JSON."
)


class MemoryAgent(BaseAgent):
    name = "Memory"
    version = "1.0.0"
    allowed_tools = ["incident_memory_search"]
    scope = "Recall past incidents with the same fingerprint; recommend a known fix"
    headline = "Recalling past incidents"

    async def execute(self, ctx: RunContext) -> None:
        fingerprint = ctx.incident.fingerprint or ""

        # 1. Vector-similarity search over past-incident fingerprints --------
        matches = M.search_memory(ctx.deps.storage, fingerprint)
        await ctx.tool(
            self.name, "incident_memory_search",
            f"search_memory(fingerprint={fingerprint[:60]!r})",
            {"candidates": [
                {"typical_cause": m.memory.typical_cause,
                 "typical_fix": m.memory.typical_fix,
                 "similarity": m.similarity} for m in matches
            ]},
        )

        best = matches[0] if matches else None

        # 2. Below the floor (or no history at all) → honest "no strong prior".
        if best is None or best.similarity < SIMILARITY_FLOOR:
            ctx.remember("memory", {
                "match": None,
                "recommendation": "No strong prior — treat as a novel incident.",
                "reasoning": (
                    f"Best similarity {best.similarity if best else 0.0} is below "
                    f"the {SIMILARITY_FLOOR} confidence floor."
                ),
            })
            await ctx.emit(
                "memory_result", agent=self.name,
                similarity=best.similarity if best else 0.0,
                times_seen=0, avg_resolution_minutes=0.0,
            )
            return

        # 3. Strong prior → let Gemini phrase the recommendation -------------
        mem = best.memory
        times_seen = len(mem.past_incident_ids)
        prompt = f"""Current incident fingerprint: {fingerprint}
Closest historical match (cosine similarity {best.similarity}):
- Typical cause: {mem.typical_cause}
- Typical fix: {mem.typical_fix}
- Seen {times_seen} time(s) before; average resolution ~{mem.avg_resolution_minutes} min.

Return JSON:
{{"recommendation": "one sentence like 'Seen {times_seen}x, resolved in ~{mem.avg_resolution_minutes}m via <fix>' with a clear recommended action",
  "reasoning": "1 sentence on why this prior applies"}}"""

        decision, _ = await ctx.think(
            self.name, "recall", prompt, system=SYSTEM, response_json=True
        )

        ctx.remember("memory", {
            "match": {
                "similarity": best.similarity,
                "typical_cause": mem.typical_cause,
                "typical_fix": mem.typical_fix,
                "avg_resolution_minutes": mem.avg_resolution_minutes,
                "past_incident_ids": mem.past_incident_ids,
                "times_seen": times_seen,
            },
            "recommendation": decision.get(
                "recommendation",
                f"Seen {times_seen}x, resolved in ~{mem.avg_resolution_minutes}m "
                f"via {mem.typical_fix}.",
            ),
            "reasoning": decision.get("reasoning", ""),
        })
        await ctx.emit(
            "memory_result", agent=self.name,
            similarity=best.similarity, times_seen=times_seen,
            avg_resolution_minutes=mem.avg_resolution_minutes,
        )
