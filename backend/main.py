"""AegisPilot FastAPI app — event-bus consumer, REST API, and SSE streaming.

Startup wires the local implementations of the cloud-portable interfaces
(SQLiteStorage, InProcessBus) into the Orchestrator and subscribes it to the
bus. Swapping to Firestore/PubSub for real GCP means changing only the two
constructor lines marked below.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.adk.orchestrator import AdkOrchestrator
from backend.agents.base import Deps
from backend.config import SEED_DIR, get_settings
from backend.guardrails import ApprovalDecision, ApprovalGate
from backend.models import Alert, Deploy, LogLine, now_ms
from backend.orchestrator import Orchestrator
from backend.seed.generate_grafana import (
    CART_OUT,
    OUT as GRAFANA_IMG,
    PAYMENTS_OUT,
    generate_all as generate_grafana_all,
)
from backend.seed.scenarios import next_scenario
from backend.seed.seed_data import seed_all
from backend.services.eventbus import InProcessBus
from backend.services.gemini import gemini
from backend.services.storage import SQLiteStorage
from backend.services.stream import hub

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aegisops.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Export Vertex config so ADK's Gemini + google-genai pick the Vertex backend.
    settings.apply_google_env()

    # --- The one place impls are chosen. BACKEND=cloud swaps to Firestore. ---
    if settings.backend.lower() == "cloud":
        # Local import so `local` mode never needs the firestore package.
        from backend.services.firestore_storage import FirestoreStorage

        storage = FirestoreStorage(settings.google_cloud_project)
    else:
        storage = SQLiteStorage(settings.db_path)

    # BACKEND=cloud also swaps the in-process bus for real Pub/Sub.
    if settings.backend.lower() == "cloud":
        from backend.services.pubsub_bus import PubSubBus

        bus = PubSubBus(settings.google_cloud_project)
        # Push mode (Cloud Run) needs only the topic; pull mode also needs the
        # in-app subscription. Idempotent; fails loud on auth errors.
        bus.ensure(create_pull_subscription=settings.pubsub_mode.lower() != "push")
    else:
        bus = InProcessBus()
    # ------------------------------------------------------------------------
    storage.init_schema()
    # Seed only when empty. Firestore writes are per-doc round-trips (~20s), so
    # re-seeding every boot would make Cloud Run cold starts crawl; the fixtures
    # are stable, and the seed_* scripts force a refresh when needed.
    if not storage.list_agents():
        seed_all(storage, settings.gemini_model)
        log.info("seeded fixtures")
    else:
        log.info("fixtures already present; skipping seed")
    if not (GRAFANA_IMG.exists() and CART_OUT.exists() and PAYMENTS_OUT.exists()):
        generate_grafana_all()  # deterministic; renders all three scenario snapshots

    gate = ApprovalGate()
    deps = Deps(storage=storage, gemini=gemini, hub=hub, gate=gate)
    # Orchestrator selection: real google-adk (default) or the local fallback.
    # The local Orchestrator is never deleted — set ORCHESTRATOR=local to use it.
    if settings.orchestrator.lower() == "adk":
        orchestrator = AdkOrchestrator(deps)
    else:
        orchestrator = Orchestrator(deps)
    # In PUSH mode (Cloud Run) Pub/Sub delivers to POST /api/pubsub/push, so we
    # do NOT start an in-app pull subscriber (an instance at scale-zero can't
    # pull). Otherwise subscribe the orchestrator to the bus directly.
    if not (settings.backend.lower() == "cloud" and settings.pubsub_mode.lower() == "push"):
        bus.subscribe(orchestrator.handle_alert)
    else:
        log.info("Pub/Sub PUSH mode: incidents arrive via POST /api/pubsub/push")

    app.state.settings = settings
    app.state.storage = storage
    app.state.bus = bus
    app.state.gate = gate
    app.state.orchestrator = orchestrator

    log.info("AegisPilot ready. orchestrator=%s  backend=%s  vertex=%s  model=%s  slack=%s",
             type(orchestrator).__name__, settings.backend, settings.use_vertex,
             settings.gemini_model, settings.has_slack)
    yield
    # --- shutdown: stop the Pub/Sub streaming pull cleanly (no-op for local) ---
    if hasattr(bus, "close"):
        bus.close()


app = FastAPI(title="AegisPilot", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
@app.post("/api/alerts")
async def post_alert(alert: Alert, request: Request):
    """Drop an alert on the event bus (the demo publisher hits this)."""
    await request.app.state.bus.publish(alert)
    return {"accepted": True, "service": alert.service}


@app.post("/api/demo/fire")
async def demo_fire(request: Request):
    """Fire the NEXT rotating demo scenario (checkout → cart → payments)."""
    sc = next_scenario()
    alert = Alert(**sc.alert)
    await request.app.state.bus.publish(alert)
    return {"accepted": True, "scenario": sc.key, "service": alert.service, "alert": alert.alert}


@app.post("/api/incidents/custom")
async def custom_incident(
    request: Request,
    service: str = Form(...),
    alert: str = Form("HighErrorRate"),
    error_rate: str = Form("10%"),
    logs: str = Form(""),
    deploy_version: str = Form(""),
    rollback_target: str = Form(""),
    image: Optional[UploadFile] = File(None),
):
    """Bring-your-own-incident: judges submit their OWN data and the real agents
    process exactly it — no randomness. Their log lines are ingested and read by
    the Diagnosis agent, an optional recent deploy feeds Correlation, and an
    optional dashboard screenshot is read by Gemini vision.
    """
    storage = request.app.state.storage
    now = now_ms()

    # Ingest the judge's log lines (newest last), spaced over the last ~12 min.
    lines = [ln.strip() for ln in logs.splitlines() if ln.strip()]
    for i, msg in enumerate(reversed(lines)):
        lvl = "ERROR" if any(k in msg.lower() for k in ("error", "fail", "timeout", "exception", "5xx", "oom")) \
            else "WARN" if any(k in msg.lower() for k in ("warn", "degraded", "slow", "retry")) else "INFO"
        storage.add_log(LogLine(
            id=f"log_custom_{service}_{now}_{i}", service=service,
            ts=now - i * 45_000, level=lvl, message=msg,
        ))

    # Optional: a recent deploy for Correlation to blame (~10 min ago).
    if deploy_version.strip():
        storage.add_deploy(Deploy(
            id=f"dep_custom_{service}_{now}", service=service, version=deploy_version.strip(),
            deployed_at=now - 10 * 60_000, deployed_by="judge@demo",
            commit_sha="custom0", rollback_target=(rollback_target.strip() or None),
        ))

    # Optional: the judge's dashboard screenshot → real Gemini vision.
    snapshot: Optional[str] = None
    if image is not None:
        customs = SEED_DIR / "custom"
        customs.mkdir(exist_ok=True)
        ext = ".png" if (image.content_type or "").endswith("png") else ".jpg"
        dest = customs / f"{service}_{now}{ext}"
        dest.write_bytes(await image.read())
        snapshot = str(dest.relative_to(SEED_DIR.parent.parent))

    payload = Alert(alert=alert, service=service, error_rate=error_rate, grafana_snapshot=snapshot)
    await request.app.state.bus.publish(payload)
    return {"accepted": True, "service": service, "logs_ingested": len(lines),
            "deploy": bool(deploy_version.strip()), "vision_image": snapshot is not None}


@app.post("/api/pubsub/push")
async def pubsub_push(request: Request):
    """Pub/Sub PUSH endpoint for Cloud Run (Phase 6).

    Pub/Sub POSTs a base64 envelope here; we decode it to an Alert and run the
    orchestrator directly. Returns 204 to ack; a non-2xx makes Pub/Sub retry.
    """
    import base64
    import json as _json

    envelope = await request.json()
    msg = (envelope or {}).get("message", {})
    raw = msg.get("data")
    if not raw:
        raise HTTPException(400, "no message data")
    alert = Alert(**_json.loads(base64.b64decode(raw).decode("utf-8")))
    # Fire-and-forget so we ack Pub/Sub promptly while the incident runs.
    asyncio.create_task(request.app.state.orchestrator.handle_alert(alert))
    from fastapi import Response

    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Read API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health(request: Request):
    s = request.app.state.settings
    return {
        "status": "ok",
        "orchestrator": type(request.app.state.orchestrator).__name__,
        "model": s.gemini_model,
        "model_pro": s.gemini_model_pro,
        "auth": "vertex-adc" if s.use_vertex else ("ai-studio-key" if s.has_gemini_key else "none"),
        "vertex": s.use_vertex,
        "project": s.google_cloud_project or None,
        "vertex_location": s.vertex_location if s.use_vertex else None,
        "compute_location": s.google_cloud_location,
        "backend": s.backend,
        "slack_configured": s.has_slack,
    }


@app.get("/api/registry")
async def registry(request: Request):
    return [a.model_dump() for a in request.app.state.storage.list_agents()]


@app.get("/api/incidents")
async def incidents(request: Request):
    return [i.model_dump() for i in request.app.state.storage.list_incidents()]


@app.get("/api/incidents/{incident_id}")
async def incident(incident_id: str, request: Request):
    inc = request.app.state.storage.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "incident not found")
    return inc.model_dump()


@app.get("/api/incidents/{incident_id}/audit")
async def audit(incident_id: str, request: Request):
    return [s.model_dump() for s in request.app.state.storage.audit_for_incident(incident_id)]


@app.get("/api/incidents/{incident_id}/rca")
async def rca(incident_id: str, request: Request):
    inc = request.app.state.storage.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "incident not found")
    return {"rca": inc.rca_doc or "", "findings": inc.findings.get("comms", {})}


@app.get("/api/incidents/{incident_id}/grafana")
async def grafana(incident_id: str, request: Request):
    """Serve the exact Grafana image THIS incident's vision agent analyzed.

    Each scenario carries its own snapshot; a custom incident may carry none
    (then 404, and the UI shows a clean 'no snapshot' state) — we never serve a
    misleading fallback from a different service.
    """
    inc = request.app.state.storage.get_incident(incident_id)
    if not (inc and inc.alert and inc.alert.grafana_snapshot):
        raise HTTPException(404, "no snapshot for this incident")
    p = Path(inc.alert.grafana_snapshot)
    if not p.is_absolute():
        p = SEED_DIR.parent.parent / p
    if not p.exists():
        raise HTTPException(404, "snapshot not available")
    media = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(p, media_type=media)


# --------------------------------------------------------------------------- #
# Human-in-the-loop approval
# --------------------------------------------------------------------------- #
class Decision(BaseModel):
    approver: str = "on-call-engineer"
    note: str = ""


@app.post("/api/incidents/{incident_id}/approve")
async def approve(incident_id: str, decision: Decision, request: Request):
    ok = request.app.state.gate.resolve(
        incident_id, ApprovalDecision(approved=True, approver=decision.approver, note=decision.note)
    )
    if not ok:
        raise HTTPException(409, "no open approval gate for this incident")
    return {"resolved": True, "approved": True}


@app.post("/api/incidents/{incident_id}/reject")
async def reject(incident_id: str, decision: Decision, request: Request):
    ok = request.app.state.gate.resolve(
        incident_id, ApprovalDecision(approved=False, approver=decision.approver, note=decision.note)
    )
    if not ok:
        raise HTTPException(409, "no open approval gate for this incident")
    return {"resolved": True, "approved": False}


# --------------------------------------------------------------------------- #
# SSE — live reasoning-chain stream
# --------------------------------------------------------------------------- #
async def _sse(incident_id: str | None):
    async for event in hub.subscribe(incident_id):
        yield {"event": event.type, "data": event.model_dump_json()}
        # Cooperative yield so a burst of events flushes promptly.
        await asyncio.sleep(0)


@app.get("/api/stream")
async def stream_all(request: Request):
    return EventSourceResponse(_sse(None))


@app.get("/api/stream/{incident_id}")
async def stream_one(incident_id: str, request: Request):
    return EventSourceResponse(_sse(incident_id))


# --------------------------------------------------------------------------- #
# Static frontend (single-container Cloud Run). Mounted last so /api wins.
# --------------------------------------------------------------------------- #
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("backend.main:app", host=s.host, port=s.port, reload=False)
