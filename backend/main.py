"""AegisPilot FastAPI app — event-bus consumer, REST API, and SSE streaming.

Startup wires the local implementations of the cloud-portable interfaces
(SQLiteStorage, InProcessBus) into the Orchestrator and subscribes it to the
bus. Swapping to Firestore/PubSub for real GCP means changing only the two
constructor lines marked below.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
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

_MEDIA_PNG = "image/png"
_MEDIA_JPEG = "image/jpeg"
_JPEG_SUFFIXES = (".jpg", ".jpeg")


def _media_for_snapshot(path_or_name: object | None) -> str:
    name = str(path_or_name or "").lower()
    if name.endswith(_JPEG_SUFFIXES):
        return _MEDIA_JPEG
    return _MEDIA_PNG


def _is_seed_snapshot(snap: object | None) -> bool:
    return bool(snap) and ("backend/seed" in str(snap).replace("\\", "/"))


def _grafana_b64_response(b64: str, snap: object | None):
    import base64
    from fastapi.responses import Response

    return Response(content=base64.b64decode(b64), media_type=_media_for_snapshot(snap))


def _grafana_file_response(snap: object, seed_path: bool, b64: object | None):
    p = Path(snap)
    if not p.is_absolute():
        p = SEED_DIR.parent.parent / p
    if p.exists() and (not seed_path or not b64):
        return FileResponse(p, media_type=_media_for_snapshot(p))
    return None


def _build_storage(settings):
    if settings.backend.lower() != "cloud":
        return SQLiteStorage(settings.db_path)
    from backend.services.firestore_storage import FirestoreStorage

    return FirestoreStorage(settings.google_cloud_project)


def _build_bus(settings):
    if settings.backend.lower() != "cloud":
        return InProcessBus()
    from backend.services.pubsub_bus import PubSubBus

    bus = PubSubBus(settings.google_cloud_project)
    bus.ensure(create_pull_subscription=settings.pubsub_mode.lower() != "push")
    return bus


def _build_orchestrator(settings, deps: Deps):
    if settings.orchestrator.lower() == "adk":
        return AdkOrchestrator(deps)
    return Orchestrator(deps)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.apply_google_env()

    storage = _build_storage(settings)
    bus = _build_bus(settings)
    storage.init_schema()
    if not storage.list_agents():
        seed_all(storage, settings.gemini_model)
        log.info("seeded fixtures")
    else:
        log.info("fixtures already present; skipping seed")
    if not (settings.prometheus_url or "").strip():
        if not (GRAFANA_IMG.exists() and CART_OUT.exists() and PAYMENTS_OUT.exists()):
            generate_grafana_all()

    gate = ApprovalGate()
    deps = Deps(storage=storage, gemini=gemini, hub=hub, gate=gate)
    orchestrator = _build_orchestrator(settings, deps)
    push_only = settings.backend.lower() == "cloud" and settings.pubsub_mode.lower() == "push"
    if push_only:
        log.info("Pub/Sub PUSH mode: incidents arrive via POST /api/pubsub/push")
    else:
        bus.subscribe(orchestrator.handle_alert)

    app.state.settings = settings
    app.state.storage = storage
    app.state.bus = bus
    app.state.gate = gate
    app.state.orchestrator = orchestrator

    log.info(
        "AegisPilot ready. orchestrator=%s  backend=%s  vertex=%s  model=%s  slack=%s",
        type(orchestrator).__name__, settings.backend, settings.use_vertex,
        settings.gemini_model, settings.has_slack,
    )
    yield
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
    """Fire an incident.

    Live mode (REMEDIATION_MODE=kubernetes|docker + PROMETHEUS_URL): spikes a
    real scrape ERROR_RATE (K8s patch-env or Compose /admin/fault), generates
    /api/work load, ingests live logs — no HikariCP seed fixtures.

    Otherwise: rotating demo scenario (local / offline).
    """
    from backend.services.live_fire import live_mode_enabled, prepare_live_fire

    storage = request.app.state.storage
    if live_mode_enabled():
        import asyncio

        prepared = await asyncio.to_thread(prepare_live_fire, storage, "checkout-svc", 0.42)
        alert = prepared["alert"]
        await request.app.state.bus.publish(alert)
        return {
            "accepted": True,
            "scenario": "live",
            "service": alert.service,
            "alert": alert.alert,
            "error_rate": alert.error_rate,
            "live": True,
            "load": prepared.get("load"),
        }

    sc = next_scenario()
    alert = Alert(**sc.alert)
    await request.app.state.bus.publish(alert)
    return {"accepted": True, "scenario": sc.key, "service": alert.service, "alert": alert.alert}


@app.post("/api/deploys")
async def record_deploy(
    request: Request,
    service: str = Form(...),
    version: str = Form(...),
    commit_sha: str = Form(""),
    deployed_by: str = Form("jenkins-ci"),
    rollback_target: str = Form(""),
):
    """Record a deploy for Correlation only — does NOT open an incident.

    Jenkins must call this (not /api/incidents/custom) after a promote.
    """
    storage = request.app.state.storage
    t = now_ms()
    dep = Deploy(
        id=f"dep_{service}_{t}",
        service=service,
        version=version,
        deployed_at=t,
        deployed_by=deployed_by,
        commit_sha=(commit_sha or "unknown")[:12],
        rollback_target=(rollback_target.strip() or None),
    )
    storage.add_deploy(dep)
    return {"accepted": True, "deploy_id": dep.id, "service": service, "version": version}


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
        lower = msg.lower()
        if any(k in lower for k in ("error", "fail", "timeout", "exception", "5xx", "oom")):
            lvl = "ERROR"
        elif any(k in lower for k in ("warn", "degraded", "slow", "retry")):
            lvl = "WARN"
        else:
            lvl = "INFO"
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
    if s.use_vertex:
        auth = "vertex-adc"
    elif s.has_gemini_key:
        auth = "ai-studio-key"
    else:
        auth = "none"
    return {
        "status": "ok",
        "orchestrator": type(request.app.state.orchestrator).__name__,
        "model": s.gemini_model,
        "model_pro": s.gemini_model_pro,
        "auth": auth,
        "vertex": s.use_vertex,
        "project": s.google_cloud_project or None,
        "vertex_location": s.vertex_location if s.use_vertex else None,
        "compute_location": s.google_cloud_location,
        "backend": s.backend,
        "slack_configured": s.has_slack,
        # getattr: unit tests may stub Settings with SimpleNamespace
        "prometheus_configured": bool(getattr(s, "has_prometheus", False)),
        "grafana_url": (getattr(s, "grafana_url", None) or None),
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

    Prefers the on-disk path; falls back to base64 stored on the alert metadata
    so a pod recycle does not blank the Diagnosis panel.
    """
    inc = request.app.state.storage.get_incident(incident_id)
    alert = getattr(inc, "alert", None) if inc else None
    meta = (getattr(alert, "metadata", None) or {}) if alert else {}
    snap = getattr(alert, "grafana_snapshot", None) if alert else None
    b64 = meta.get("grafana_snapshot_b64") if meta else None
    if not (alert and (snap or b64)):
        raise HTTPException(404, "no snapshot for this incident")

    seed_path = _is_seed_snapshot(snap)
    live_src = meta.get("snapshot_source") in ("prometheus", "grafana-render")
    if bool(b64) and (live_src or seed_path or not snap):
        return _grafana_b64_response(b64, snap)

    if snap:
        file_resp = _grafana_file_response(snap, seed_path, b64)
        if file_resp is not None:
            return file_resp

    if b64:
        return _grafana_b64_response(b64, snap)

    raise HTTPException(404, "snapshot not available")


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
