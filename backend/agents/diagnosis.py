"""Diagnosis Agent — summarize logs + READ THE GRAFANA IMAGE with Gemini vision.

This is the multimodal centerpiece (spec §5.5). It:
  1. fetches + deterministically classifies the log stream,
  2. builds a stable incident fingerprint (for the Memory agent),
  3. sends the actual Grafana snapshot to Gemini vision to CONFIRM the anomaly
     and produce an annotation the UI overlays on the image,
  4. writes a diagnosis summary.
"""
from __future__ import annotations

from backend.agents.base import BaseAgent, RunContext
from backend.tools import diagnosis as D

SYSTEM_LOGS = (
    "You are the Diagnosis agent of an autonomous SRE system. Given a classified "
    "log stream, write a crisp technical summary of what is failing and why it "
    "looks that way. Respond ONLY with JSON."
)
SYSTEM_VISION = (
    "You are an SRE reading a Grafana dashboard screenshot. Identify the anomaly "
    "(which panel, what shape, when it started relative to the window) and state "
    "whether it CONFIRMS an incident. Be specific about numbers you can see "
    "(error %, latency ms). Respond ONLY with JSON."
)


class DiagnosisAgent(BaseAgent):
    name = "Diagnosis"
    version = "1.0.0"
    allowed_tools = ["log_fetcher", "grafana_vision", "log_classifier"]
    scope = "Summarize logs; read the Grafana screenshot (vision); classify log lines"
    headline = "Reading logs + Grafana snapshot (vision)"

    async def execute(self, ctx: RunContext) -> None:
        alert = ctx.incident.alert
        assert alert is not None
        service = alert.service

        # 1. Fetch + classify logs -------------------------------------------
        raw_logs = D.fetch_logs(ctx.deps.storage, service)
        await ctx.tool(self.name, "log_fetcher", f"fetch_logs({service})",
                       {"lines": len(raw_logs)})

        logs, analysis = D.analyze_logs(raw_logs)
        await ctx.tool(
            self.name, "log_classifier", "classify + aggregate log stream",
            {"dominant_class": analysis.dominant_class, "errors": analysis.error_count,
             "class_counts": analysis.class_counts},
        )

        fingerprint = D.build_fingerprint(service, analysis)
        ctx.incident.fingerprint = fingerprint

        top_lines = [
            {"level": lg.level, "message": lg.message, "log_class": lg.log_class}
            for lg in sorted(logs, key=lambda l: l.ts)
            if lg.level.upper() in ("ERROR", "FATAL", "WARN")
        ][:8]

        # 2. Gemini VISION on the actual Grafana snapshot --------------------
        vision = {"confirmed": None, "observation": "", "annotation": ""}
        snapshot = alert.grafana_snapshot
        if snapshot:
            vprompt = (
                f"This Grafana dashboard is for '{service}'. An alert '{alert.alert}' "
                f"fired with a reported error rate of {alert.error_rate}. "
                "Look at the error-rate and latency panels.\n\n"
                'Return JSON: {"confirmed": true|false, '
                '"observation": "what you see in the panels, with numbers", '
                '"annotation": "a short caption to overlay on the image", '
                '"reasoning": "why this confirms/denies the incident"}'
            )
            try:
                vresult, _ = await ctx.think(
                    self.name, "grafana_vision", vprompt,
                    system=SYSTEM_VISION, response_json=True, image_path=snapshot,
                )
                vision = {
                    "confirmed": vresult.get("confirmed"),
                    "observation": vresult.get("observation", ""),
                    "annotation": vresult.get("annotation", ""),
                }
            except FileNotFoundError as exc:
                await ctx.tool(self.name, "grafana_vision", "snapshot missing",
                               {"error": str(exc)})
            # Give the UI what it needs to render + overlay the image.
            await ctx.emit(
                "vision_result", agent=self.name, image_url=f"/api/incidents/{ctx.incident.id}/grafana",
                confirmed=vision["confirmed"], observation=vision["observation"],
                annotation=vision["annotation"],
            )

        # 3. Summarize the diagnosis -----------------------------------------
        sprompt = f"""Service: {service}. Dominant failure class: {analysis.dominant_class}.
Error lines: {analysis.error_count} of {analysis.total}. Class breakdown: {analysis.class_counts}.
Key log lines:
{chr(10).join('- [' + l['level'] + '] ' + l['message'] for l in top_lines)}

Vision read of Grafana: {vision['observation'] or 'n/a'}

Return JSON: {{"summary": "2-3 sentence technical diagnosis of the failure mode",
"primary_symptom": "short phrase", "reasoning": "1 sentence"}}"""

        diag, _ = await ctx.think(
            self.name, "summarize", sprompt, system=SYSTEM_LOGS, response_json=True
        )

        ctx.remember("diagnosis", {
            "summary": diag.get("summary", ""),
            "primary_symptom": diag.get("primary_symptom", analysis.dominant_class),
            "dominant_class": analysis.dominant_class,
            "class_counts": analysis.class_counts,
            "error_count": analysis.error_count,
            "fingerprint": fingerprint,
            "vision": vision,
            "top_log_lines": top_lines,
        })
        ctx.deps.storage.save_incident(ctx.incident)
