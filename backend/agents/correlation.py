"""Correlation Agent — tie the incident to a recent change (bad-deploy hunt).

Same pattern as Triage/Diagnosis:
  1. Deterministic tools gather hard signals (deploy history + proximity scores).
  2. Each tool call is streamed via ctx.tool(...).
  3. Gemini (via ctx.think, response_json) names the probable root cause and a
     confidence number — but that confidence is *grounded* in the top suspect's
     proximity_score, not invented, so the UI's confidence badge is defensible.
"""
from __future__ import annotations

from backend.agents.base import BaseAgent, RunContext
from backend.tools import correlation as C

SYSTEM = (
    "You are the Correlation agent of an autonomous SRE system. You decide "
    "whether a recent change (deploy) caused the incident. You are handed a "
    "ranked list of recent deploys, each with a temporal proximity score to the "
    "moment of detection (higher = shipped closer before the incident, more "
    "suspicious). Name the single most probable root cause and a calibrated "
    "confidence. Respond ONLY with JSON."
)


class CorrelationAgent(BaseAgent):
    name = "Correlation"
    version = "1.0.0"
    allowed_tools = ["deploy_history_query", "change_correlator"]
    scope = "Correlate recent deploys/changes against the incident window"
    headline = "Correlating recent deploys"

    async def execute(self, ctx: RunContext) -> None:
        alert = ctx.incident.alert
        assert alert is not None
        service = alert.service
        detected_at = ctx.incident.detected_at

        # 1. Pull the deploy history around the incident window ---------------
        deploys = C.deploys_in_window(ctx.deps.storage, service, detected_at)
        await ctx.tool(
            self.name, "deploy_history_query",
            f"deploys_in_window({service}, lookback=180m)",
            {"count": len(deploys),
             "deploys": [{"service": d.service, "version": d.version,
                          "deployed_by": d.deployed_by} for d in deploys]},
        )

        # 2. Score each change by temporal proximity to detection ------------
        suspects = C.correlate_changes(deploys, detected_at)
        ranked = [
            {"service": s.deploy.service, "version": s.deploy.version,
             "minutes_before": s.minutes_before, "proximity_score": s.proximity_score}
            for s in suspects
        ]
        await ctx.tool(
            self.name, "change_correlator",
            f"correlate_changes({len(deploys)} deploys)",
            {"ranked": ranked},
        )

        # The strongest suspect anchors both the model's answer and our own
        # deterministic confidence floor.
        top = suspects[0] if suspects else None
        suspect_payload = None
        if top is not None:
            suspect_payload = {
                "service": top.deploy.service,
                "version": top.deploy.version,
                "deployed_by": top.deploy.deployed_by,
                "commit_sha": top.deploy.commit_sha,
                "minutes_before": top.minutes_before,
                "rollback_target": top.deploy.rollback_target,
            }

        # 3. Gemini synthesis, grounded in the proximity score ---------------
        diagnosis = ctx.incident.findings.get("diagnosis", {})
        prompt = f"""Incident on service '{service}': {alert.alert} (error rate {alert.error_rate}).
Diagnosis so far: {diagnosis.get('summary', 'n/a')} (symptom: {diagnosis.get('primary_symptom', 'n/a')}).

Ranked recent changes (most suspicious first):
{chr(10).join('- ' + r['service'] + ' ' + r['version'] + ' shipped ' + str(r['minutes_before']) + ' min before detection (proximity ' + str(r['proximity_score']) + ')' for r in ranked) or '- no recent deploys found'}

Top suspect proximity score: {top.proximity_score if top else 0.0} (0..1).
A high proximity score for a deploy just before detection is strong evidence of a
bad-deploy regression. Let your confidence track that score.

Return JSON:
{{"probable_cause": "one crisp sentence naming the most likely root cause",
  "confidence": 0.0,
  "reasoning": "1-2 sentences tying the change to the incident window"}}"""

        decision, _ = await ctx.think(
            self.name, "correlate", prompt, system=SYSTEM, response_json=True
        )

        # 4. Reconcile: keep the model's confidence honest against the score.
        #    If no change lines up, cap confidence low regardless of the model.
        try:
            model_conf = float(decision.get("confidence", 0.0))
        except (TypeError, ValueError):
            model_conf = 0.0
        model_conf = max(0.0, min(1.0, model_conf))
        if top is None:
            confidence = min(model_conf, 0.2)
            probable_cause = decision.get(
                "probable_cause", "No recent change correlates with the incident window."
            )
        else:
            # Don't let the model claim more certainty than the deterministic
            # proximity evidence supports.
            confidence = round(min(model_conf, top.proximity_score + 0.15), 3)
            probable_cause = decision.get(
                "probable_cause",
                f"Bad deploy: {top.deploy.service} {top.deploy.version}",
            )

        ctx.incident.probable_cause = probable_cause
        ctx.incident.confidence = confidence
        ctx.deps.storage.save_incident(ctx.incident)

        ctx.remember("correlation", {
            "probable_cause": probable_cause,
            "confidence": confidence,
            "suspect": suspect_payload,
            "ranked": ranked,
            "reasoning": decision.get("reasoning", ""),
        })
        await ctx.emit(
            "correlation_result", agent=self.name,
            probable_cause=probable_cause, confidence=confidence,
        )
