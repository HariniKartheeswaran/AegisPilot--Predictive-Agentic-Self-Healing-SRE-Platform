"""Comms Agent — write the RCA, notify Slack (PII-scrubbed), file a ticket.

Closes the loop for BOTH outcomes (resolved or rejected). It:
  1. asks Gemini for a full RCA in markdown *prose* (not JSON) from every finding
     and the incident timeline, and stores it on the incident,
  2. scrubs PII from the Slack-bound text (the self-implemented "Model Armor")
     BEFORE anything leaves the system, and honestly records the console
     fallback when no webhook is configured,
  3. files a real ticket artifact to disk so "filed a ticket" is verifiable.
"""
from __future__ import annotations

from backend.agents.base import BaseAgent, RunContext
from backend.config import get_settings
from backend.guardrails import scrub_pii
from backend.services import slack
from backend.tools import comms as CT

SYSTEM = (
    "You are the Comms agent of an autonomous SRE system. Write a clear, honest "
    "post-incident RCA in GitHub-flavored Markdown. Be specific and concise; use "
    "sections (Summary, Impact, Root Cause, Detection & Diagnosis, Remediation, "
    "Timeline, Follow-ups). If the remediation was executed in simulation, say so "
    "plainly. Return the Markdown document only — no code fences, no JSON."
)


class CommsAgent(BaseAgent):
    name = "Comms"
    version = "1.0.0"
    allowed_tools = ["rca_writer", "slack_poster", "ticket_filer"]
    scope = "Write the RCA, notify Slack (PII-scrubbed), file a ticket"
    headline = "Writing RCA + notifying"

    async def execute(self, ctx: RunContext) -> None:
        inc = ctx.incident
        f = inc.findings
        triage = f.get("triage", {})
        diagnosis = f.get("diagnosis", {})
        correlation = f.get("correlation", {})
        memory = f.get("memory", {})
        remediation = f.get("remediation", {})
        execution = f.get("execution", {})
        res_minutes = CT.resolution_minutes(inc)

        # 1. Generate the RCA as markdown prose (NOT json) -------------------
        prompt = f"""Write the post-incident RCA for this incident.

Incident: {inc.id} on service '{inc.service}'. Final status: {inc.status.value}.
Severity: {inc.severity.value if inc.severity else 'n/a'}. Blast radius: {inc.blast_radius or 'n/a'}.
Alert: {inc.alert.alert if inc.alert else 'n/a'} (error rate {inc.alert.error_rate if inc.alert else 'n/a'}).

Triage: {triage}
Diagnosis: {diagnosis.get('summary', 'n/a')} (symptom: {diagnosis.get('primary_symptom', 'n/a')})
Correlation: probable cause = {correlation.get('probable_cause', 'n/a')} (confidence {correlation.get('confidence', 'n/a')}); suspect = {correlation.get('suspect')}
Memory: {memory.get('recommendation', 'n/a')}
Remediation plan: {remediation}
Execution: {execution or 'not executed (plan was not approved)'}
Approved by: {inc.approved_by or 'n/a'}. Time to resolution: {res_minutes} min.

Produce the full Markdown RCA now."""

        # RCA reasoning runs on the Pro tier (Phase 5); other agents stay on Flash.
        rca_text, _ = await ctx.think(
            self.name, "rca_writer", prompt, system=SYSTEM,
            model=get_settings().gemini_model_pro,
        )
        rca_text = rca_text or f"# RCA — {inc.id}\n\n_No narrative generated._"
        inc.rca_doc = rca_text
        ctx.deps.storage.save_incident(inc)
        await ctx.tool(
            self.name, "rca_writer", f"generate RCA for {inc.id}",
            {"chars": len(rca_text)},
        )

        # A one-line summary for the Slack notification fallback text.
        rca_summary = (
            f"[{inc.severity.value if inc.severity else 'SEV?'}] {inc.service}: "
            f"{inc.probable_cause or 'incident'} — {inc.status.value}"
            + (f" in {res_minutes}m" if res_minutes else "")
        )

        # 2. File the ticket first so the Slack card can link it -------------
        ticket = CT.file_ticket(inc, rca_summary)
        inc.findings["comms"] = {"ticket": {"id": ticket.id, "url": ticket.url, "path": ticket.path}}
        await ctx.tool(
            self.name, "ticket_filer", f"file_ticket({inc.id})",
            {"id": ticket.id, "path": ticket.path, "url": ticket.url},
        )

        # 3. Post a concise, PII-scrubbed Slack card (full RCA stays in ticket)
        scrubbed = scrub_pii(CT.render_slack_summary(inc))
        slack_result = await slack.post_incident(scrubbed.text, summary=rca_summary)
        # delivered=False is the honest console/degraded fallback, not a crash.
        await ctx.tool(
            self.name, "slack_poster",
            f"post_incident(redactions={scrubbed.redactions})",
            {"delivered": slack_result.delivered, "channel": slack_result.channel,
             "detail": slack_result.detail},
        )

        ctx.remember("comms", {
            "rca": rca_text,
            "slack": {
                "delivered": slack_result.delivered,
                "channel": slack_result.channel,
                "redactions": scrubbed.redactions,
            },
            "ticket": {"id": ticket.id, "url": ticket.url, "path": ticket.path},
            "resolution_minutes": res_minutes,
        })
        await ctx.emit(
            "comms_result", agent=self.name,
            ticket_id=ticket.id, slack_channel=slack_result.channel,
            resolution_minutes=res_minutes, rca_present=True,
        )
