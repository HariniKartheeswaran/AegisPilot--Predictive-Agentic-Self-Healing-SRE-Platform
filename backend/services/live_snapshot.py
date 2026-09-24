"""Live Grafana/Prom snapshot for Diagnosis vision.

When an alert has no ``grafana_snapshot``, pull real Prometheus series and render
a dark Grafana-style PNG the vision agent can read. Also builds a Grafana Explore
deep-link so the War Room UI can open the same query.

Snapshots are written under ``/tmp/aegis_snapshots`` (always writable) and a
base64 copy is stored on ``alert.metadata`` so the UI can still serve the image
after a pod recycle (Firestore keeps the incident; local disk does not).
"""
from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from backend.config import get_settings

log = logging.getLogger("aegisops.live_snapshot")

BG = "#0b0e14"
PANEL = "#12161f"
GRID = "#2a3242"
TEXT = "#c7d0e0"
GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"

# Writable in the container even when the image layer is read-only elsewhere.
SNAP_DIR = Path("/tmp/aegis_snapshots")

# Scrape services that expose http_requests_total
_SCRAPE_SERVICES = {"checkout-svc", "cart-svc", "payments-svc"}


def _error_rate_query(service: str) -> str:
    """5xx %% for a scrape service, or all aegis scrapes for warroom/other."""
    if service in _SCRAPE_SERVICES:
        sel = f'service="{service}"'
    else:
        sel = 'job=~"aegis-.*"'
    return (
        f"("
        f'sum(rate(http_requests_total{{{sel},code=~"5.."}}[1m])) '
        f'or vector(0)'
        f") "
        f"/ "
        f"clamp_min("
        f'sum(rate(http_requests_total{{{sel}}}[1m])) or vector(1e-9)'
        f", 1e-9) * 100"
    )


def _up_query(service: str) -> str:
    if service in _SCRAPE_SERVICES:
        return f'up{{service="{service}"}}'
    return 'up{job=~"aegis-.*"}'


def explore_url(service: str, *, grafana_base: str, prom_datasource_uid: str = "") -> str:
    """Grafana Explore deep-link (schemaVersion=1 panes) for live scrapes."""
    base = grafana_base.rstrip("/")
    # Match the Diagnosis PNG: up + 5xx rate so "Open in Grafana" is never empty.
    panes = {
        "aegis": {
            "datasource": "prometheus",
            "queries": [
                {"refId": "A", "expr": _up_query(service)},
                {"refId": "B", "expr": _error_rate_query(service)},
            ],
            "range": {"from": "now-1h", "to": "now"},
        }
    }
    return (
        f"{base}/explore?orgId=1&schemaVersion=1&panes="
        f"{quote(json.dumps(panes, separators=(',', ':')))}"
    )


def _query_range(prom_url: str, query: str, minutes: int = 60, step: str = "15s") -> list[tuple[float, float]]:
    end = time.time()
    start = end - minutes * 60
    url = f"{prom_url.rstrip('/')}/api/v1/query_range"
    with httpx.Client(timeout=12.0) as client:
        r = client.get(url, params={"query": query, "start": start, "end": end, "step": step})
        r.raise_for_status()
        body = r.json()
    if body.get("status") != "success":
        raise RuntimeError(f"prom query failed: {body}")
    result = body.get("data", {}).get("result") or []
    if not result:
        return []
    # Prefer a single series: take values from the first series; if multiple "up"
    # series, average at each timestamp via simple last-writer merge then fill.
    by_ts: dict[float, list[float]] = {}
    for series in result:
        for ts, val in series.get("values") or []:
            try:
                t, v = float(ts), float(val)
            except (TypeError, ValueError):
                continue
            by_ts.setdefault(t, []).append(v)
    points = sorted((t, sum(vs) / len(vs)) for t, vs in by_ts.items())
    return points


def _render_png(
    service: str,
    up_pts: list[tuple[float, float]],
    err_pts: list[tuple[float, float]],
    out: Path,
) -> Path:
    fig, axes = plt.subplots(2, 1, figsize=(10.5, 5.6), sharex=True)
    fig.patch.set_facecolor(BG)

    def _plot(ax, pts: list[tuple[float, float]], title: str, color: str, ylabel: str) -> None:
        ax.set_facecolor(PANEL)
        ax.set_title(title, color=TEXT, fontsize=11, loc="left", fontweight="bold", pad=8)
        ax.grid(True, color=GRID, linewidth=0.6, alpha=0.6)
        ax.tick_params(colors=TEXT, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.set_ylabel(ylabel, color=TEXT, fontsize=9)
        if not pts:
            ax.text(0.5, 0.5, "no series", transform=ax.transAxes, ha="center",
                    color=AMBER, fontsize=10)
            return
        xs = [mdates.date2num(datetime.fromtimestamp(t, tz=timezone.utc)) for t, _ in pts]
        ys = [v for _, v in pts]
        ax.plot(xs, ys, color=color, linewidth=1.8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    _plot(axes[0], up_pts, f"{service} · up (live Prometheus)", GREEN, "up")
    _plot(axes[1], err_pts, f"{service} · 5xx error rate % (live)", RED, "error %")
    fig.suptitle("AegisPilot live metrics · Prometheus", color=TEXT, fontsize=12, fontweight="bold", y=0.98)
    fig.autofmt_xdate()
    fig.subplots_adjust(left=0.09, right=0.97, top=0.90, bottom=0.08, hspace=0.28)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, facecolor=BG)
    plt.close(fig)
    return out


def capture_live_snapshot(service: str) -> Optional[dict[str, Any]]:
    """Fetch Prom + render PNG. Returns paths/urls or None if Prom unset/unreachable."""
    s = get_settings()
    prom = (s.prometheus_url or "").strip()
    if not prom:
        return None
    grafana = (s.grafana_url or "").strip()
    try:
        up_pts = _query_range(prom, _up_query(service))
        err_pts = _query_range(prom, _error_rate_query(service))
        if not up_pts:
            up_pts = _query_range(prom, 'up{job=~"aegis-.*"}')
        stamp = int(time.time() * 1000)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in service)
        abs_path = SNAP_DIR / f"{safe}_{stamp}.png"
        _render_png(service, up_pts, err_pts, abs_path)
        raw = abs_path.read_bytes()
        link = explore_url(service, grafana_base=grafana) if grafana else ""
        log.info("live snapshot for %s → %s (up=%d err=%d pts, %d bytes)",
                 service, abs_path, len(up_pts), len(err_pts), len(raw))
        return {
            # Absolute path so FileResponse does not depend on cwd / image layers.
            "grafana_snapshot": str(abs_path),
            "grafana_explore_url": link,
            "grafana_snapshot_b64": base64.b64encode(raw).decode("ascii"),
            "source": "prometheus",
        }
    except Exception:  # noqa: BLE001 — never block the incident pipeline
        log.exception("live snapshot failed for service=%s", service)
        return None


def ensure_alert_snapshot(alert) -> Any:
    """Attach a live Prom PNG when Prometheus is configured.

    Demo / Fire scenarios ship with seed PNGs (``backend/seed/...``). Those are
    only kept when live capture is unavailable — otherwise vision always sees
    real cluster metrics, not the canned dashboard.
    """
    s = get_settings()
    seed = getattr(alert, "grafana_snapshot", None)

    def _attach_explore_only() -> Any:
        if s.grafana_url and "grafana_explore_url" not in (alert.metadata or {}):
            alert.metadata = dict(alert.metadata or {})
            alert.metadata["grafana_explore_url"] = explore_url(
                alert.service, grafana_base=s.grafana_url
            )
        return alert

    if (s.prometheus_url or "").strip():
        captured = capture_live_snapshot(alert.service)
        if captured:
            alert.grafana_snapshot = captured["grafana_snapshot"]
            meta = dict(alert.metadata or {})
            if captured.get("grafana_explore_url"):
                meta["grafana_explore_url"] = captured["grafana_explore_url"]
            if captured.get("grafana_snapshot_b64"):
                meta["grafana_snapshot_b64"] = captured["grafana_snapshot_b64"]
            meta["snapshot_source"] = captured.get("source", "prometheus")
            if seed and str(seed).startswith("backend/seed/"):
                meta["seed_snapshot_replaced"] = str(seed)
            alert.metadata = meta
            return alert
        # Live Prom failed — fall back to seed PNG if the demo attached one.
        return _attach_explore_only() if seed else alert

    return _attach_explore_only() if seed else alert
