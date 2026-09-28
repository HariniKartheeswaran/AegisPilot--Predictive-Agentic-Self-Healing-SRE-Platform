"""Alertmanager webhook ingest — real Prom → AM → War Room path.

Maps Alertmanager v4 webhook payloads into either:
  - pre-alerts (severity=warning / stage=pre): mandatory UI + Slack, no incident yet
  - full alerts (severity=critical / stage=full): publish onto the event bus

Pub/Sub remains the cloud hop (later); Compose uses this HTTP webhook.

Dedup: while an incident is already open for a service (or Fire just ran),
critical AM webhooks are skipped so Prom lag does not spawn duplicate runs.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from backend.models import Alert, IncidentStatus, StreamEvent, now_ms

log = logging.getLogger("aegisops.alertmanager")

_PRE_ALERTS: dict[str, "PreAlert"] = {}
# service → unix time until which AM "full" publishes are suppressed (Fire path)
_SUPPRESS_FULL_UNTIL: dict[str, float] = {}

_OPEN_STATUSES = frozenset(
    {
        IncidentStatus.DETECTED,
        IncidentStatus.TRIAGED,
        IncidentStatus.DIAGNOSED,
        IncidentStatus.CORRELATED,
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.REMEDIATING,
    }
)


def suppress_am_full_for(service: str, seconds: float = 300.0) -> None:
    """After War Room Fire, ignore Prom→AM critical for a short window."""
    if not service:
        return
    _SUPPRESS_FULL_UNTIL[service] = time.time() + max(0.0, seconds)
    log.info("AM full suppress %s for %.0fs", service, seconds)


def _full_suppressed(service: str) -> bool:
    until = _SUPPRESS_FULL_UNTIL.get(service) or 0.0
    return time.time() < until


def _has_open_incident(storage: Any, service: str) -> bool:
    if storage is None or not service:
        return False
    try:
        for inc in storage.list_incidents():
            if getattr(inc, "service", None) != service:
                continue
            status = getattr(inc, "status", None)
            if status in _OPEN_STATUSES:
                return True
    except Exception:  # noqa: BLE001
        log.exception("open-incident check failed")
    return False


@dataclass
class PreAlert:
    id: str
    fingerprint: str
    alertname: str
    service: str
    severity: str
    summary: str
    error_rate: str
    status: str  # firing | resolved
    acked: bool = False
    started_at: int = field(default_factory=now_ms)
    updated_at: int = field(default_factory=now_ms)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_pre_alerts(*, include_acked: bool = False) -> list[dict[str, Any]]:
    rows = []
    for p in _PRE_ALERTS.values():
        if p.status != "firing":
            continue
        if p.acked and not include_acked:
            continue
        rows.append(p.as_dict())
    rows.sort(key=lambda r: r["started_at"], reverse=True)
    return rows


def ack_pre_alert(pre_id: str) -> Optional[dict[str, Any]]:
    p = _PRE_ALERTS.get(pre_id)
    if not p:
        return None
    p.acked = True
    p.updated_at = now_ms()
    return p.as_dict()


def clear_pre_alerts_for_service(service: str) -> list[str]:
    cleared: list[str] = []
    for key, p in list(_PRE_ALERTS.items()):
        if p.service == service and p.status == "firing":
            p.status = "resolved"
            p.updated_at = now_ms()
            cleared.append(p.id)
            del _PRE_ALERTS[key]
    return cleared


def _pct_from_value(raw: Any) -> str:
    """AM annotation value is usually a ratio (0.12); accept % strings too."""
    if raw is None or raw == "":
        return "n/a"
    text = str(raw).strip()
    if text.endswith("%"):
        return text
    try:
        val = float(text)
        if val <= 1.0:
            val *= 100.0
        return f"{val:.1f}%"
    except ValueError:
        return text


def _is_pre_alert(labels: dict[str, Any]) -> bool:
    stage = str(labels.get("stage") or "").lower()
    severity = str(labels.get("severity") or "").lower()
    if stage == "pre" or severity in ("warning", "warn"):
        return True
    if stage == "full" or severity in ("critical", "error", "page"):
        return False
    # Default: treat unknown as full so we never drop a real page.
    return False


def alert_from_am(entry: dict[str, Any]) -> Alert:
    labels = entry.get("labels") or {}
    annotations = entry.get("annotations") or {}
    service = (
        labels.get("service")
        or labels.get("app")
        or labels.get("job")
        or "unknown"
    )
    alertname = labels.get("alertname") or "AlertmanagerAlert"
    error_rate = _pct_from_value(
        annotations.get("error_rate") or annotations.get("value")
    )
    return Alert(
        alert=str(alertname),
        service=str(service),
        error_rate=error_rate,
        metadata={
            "source": "alertmanager",
            "severity": labels.get("severity"),
            "stage": labels.get("stage") or ("pre" if _is_pre_alert(labels) else "full"),
            "fingerprint": entry.get("fingerprint"),
            "summary": annotations.get("summary") or annotations.get("description"),
            "generator_url": entry.get("generatorURL"),
            "am_status": entry.get("status"),
            "starts_at": entry.get("startsAt"),
        },
    )


def _pre_id(fingerprint: str, service: str) -> str:
    fp = (fingerprint or "").strip() or f"{service}-{int(time.time())}"
    return f"pre_{fp}"


async def _handle_resolved_pre(hub, fingerprint: str, service: str) -> None:
    pid = _pre_id(fingerprint, service)
    existing = _PRE_ALERTS.pop(pid, None)
    if not existing:
        return
    existing.status = "resolved"
    await hub.publish(
        StreamEvent(
            type="pre_alert_cleared",
            incident_id="",
            payload=existing.as_dict(),
            ts=now_ms(),
        )
    )


async def _handle_pre_alert(hub, storage, labels, entry, service: str, fingerprint: str) -> None:
    from backend.services.slack import post_incident

    annotations = entry.get("annotations") or {}
    pid = _pre_id(fingerprint, service)
    pre = PreAlert(
        id=pid,
        fingerprint=fingerprint or pid,
        alertname=str(labels.get("alertname") or "HighErrorRateWarning"),
        service=service,
        severity=str(labels.get("severity") or "warning"),
        summary=str(
            annotations.get("summary")
            or annotations.get("description")
            or f"Pre-alert on {service}"
        ),
        error_rate=_pct_from_value(
            annotations.get("error_rate") or annotations.get("value")
        ),
        status="firing",
        acked=_PRE_ALERTS[pid].acked if pid in _PRE_ALERTS else False,
        started_at=_PRE_ALERTS[pid].started_at if pid in _PRE_ALERTS else now_ms(),
        updated_at=now_ms(),
    )
    _PRE_ALERTS[pid] = pre
    await hub.publish(
        StreamEvent(
            type="pre_alert",
            incident_id="",
            payload=pre.as_dict(),
            ts=now_ms(),
        )
    )
    if _has_open_incident(storage, service) or _full_suppressed(service):
        return
    try:
        await post_incident(
            f"*PRE-ALERT* `{pre.service}` — {pre.summary}\n"
            f"Error rate: `{pre.error_rate}` · Ack required in War Room.",
            summary=f"[PRE-ALERT] {pre.service}: {pre.alertname} @ {pre.error_rate}",
        )
    except Exception:  # noqa: BLE001
        log.exception("pre-alert Slack notify failed")


async def _escalate_full_alert(bus, hub, entry, service: str) -> None:
    for cleared_id in clear_pre_alerts_for_service(service):
        await hub.publish(
            StreamEvent(
                type="pre_alert_cleared",
                incident_id="",
                payload={"id": cleared_id, "service": service, "reason": "escalated"},
                ts=now_ms(),
            )
        )
    alert = alert_from_am(entry)
    await bus.publish(alert)
    log.info(
        "Alertmanager → bus (%s): %s on %s (%s)",
        type(bus).__name__,
        alert.alert, alert.service, alert.error_rate,
    )


async def _process_one_alert(
    entry: Any,
    payload: dict[str, Any],
    *,
    bus,
    hub,
    storage: Any,
) -> str:
    """Handle a single AM alert entry. Returns counter key to increment."""
    if not isinstance(entry, dict):
        return "ignored"

    labels = entry.get("labels") or {}
    status = str(entry.get("status") or payload.get("status") or "firing").lower()
    service = str(
        labels.get("service") or labels.get("app") or labels.get("job") or "unknown"
    )
    fingerprint = str(entry.get("fingerprint") or "")
    is_pre = _is_pre_alert(labels)

    if status == "resolved":
        if is_pre:
            await _handle_resolved_pre(hub, fingerprint, service)
        return "resolved"

    if is_pre:
        await _handle_pre_alert(hub, storage, labels, entry, service, fingerprint)
        return "pre_alerts"

    if _full_suppressed(service) or _has_open_incident(storage, service):
        log.info(
            "AM full suppressed for %s (open incident or Fire cooldown)",
            service,
        )
        return "suppressed"

    await _escalate_full_alert(bus, hub, entry, service)
    return "incidents"


async def process_webhook(
    payload: dict[str, Any],
    *,
    bus,
    hub,
    storage: Any = None,
) -> dict[str, Any]:
    """Handle one Alertmanager webhook body.

    Returns counts: pre_alerts, incidents, resolved, ignored, suppressed.
    """
    counts = {
        "pre_alerts": 0,
        "incidents": 0,
        "resolved": 0,
        "ignored": 0,
        "suppressed": 0,
    }
    for entry in payload.get("alerts") or []:
        key = await _process_one_alert(
            entry, payload, bus=bus, hub=hub, storage=storage
        )
        counts[key] += 1

    return {"accepted": True, **counts}


def reset_pre_alerts_for_tests() -> None:
    _PRE_ALERTS.clear()
    _SUPPRESS_FULL_UNTIL.clear()
