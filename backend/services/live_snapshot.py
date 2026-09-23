"""Live Grafana/Prom snapshot for Diagnosis vision.

When an alert has no ``grafana_snapshot``, pull real Prometheus series and render
a dark Grafana-style PNG the vision agent can read. Also builds a Grafana Explore
deep-link so the War Room UI can open the same query.
"""
from __future__ import annotations

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

from backend.config import SEED_DIR, get_settings

log = logging.getLogger("aegisops.live_snapshot")

BG = "#0b0e14"
PANEL = "#12161f"
GRID = "#2a3242"
TEXT = "#c7d0e0"
GREEN = "#3fb950"
RED = "#f85149"
BLUE = "#58a6ff"
AMBER = "#d29922"

LIVE_DIR = SEED_DIR / "live"

# service name → Prometheus label matcher used by our scrape jobs
_SERVICE_MATCH = {
    "checkout-svc": 'service="checkout-svc"',
    "cart-svc": 'service="cart-svc"',
    "payments-svc": 'service="payments-svc"',
}


def explore_url(service: str, *, grafana_base: str, prom_datasource_uid: str = "") -> str:
    """Grafana Explore URL for the live up query (same data as scrapes)."""
    base = grafana_base.rstrip("/")
    left = 'up{job=~"aegis-.*"}'
    org = "1"
    query = quote(left, safe="")
    return (
        f"{base}/explore?orgId={org}&left="
        f"%5B%22now-1h%22,%22now%22,%22prometheus%22,%7B%22expr%22:%22{query}%22%7D%5D"
    )


def _error_rate_query(service: str) -> str:
    matcher = _SERVICE_MATCH.get(service, f'service="{service}"')
    return (
        f'sum(rate(http_requests_total{{{matcher},code=~"5.."}}[1m])) '
        f'/ clamp_min(sum(rate(http_requests_total{{{matcher}}}[1m])), 1e-9) * 100'
    )


def _up_query(service: str) -> str:
    if service in _SERVICE_MATCH:
        return f'up{{{_SERVICE_MATCH[service]}}}'
    return 'up{job=~"aegis-.*"}'


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
    # Merge first series (or max across series for up)
    points: list[tuple[float, float]] = []
    for series in result:
        for ts, val in series.get("values") or []:
            try:
                points.append((float(ts), float(val)))
            except (TypeError, ValueError):
                continue
    points.sort(key=lambda p: p[0])
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
        # Fallback: all aegis ups if service-specific empty
        if not up_pts:
            up_pts = _query_range(prom, 'up{job=~"aegis-.*"}')
        stamp = int(time.time() * 1000)
        rel = Path("backend/seed/live") / f"{service}_{stamp}.png"
        abs_path = SEED_DIR.parent.parent / rel
        _render_png(service, up_pts, err_pts, abs_path)
        link = explore_url(service, grafana_base=grafana) if grafana else ""
        log.info("live snapshot for %s → %s (up=%d err=%d pts)",
                 service, rel, len(up_pts), len(err_pts))
        return {
            "grafana_snapshot": str(rel).replace("\\", "/"),
            "grafana_explore_url": link,
            "source": "prometheus",
        }
    except Exception:  # noqa: BLE001 — never block the incident pipeline
        log.exception("live snapshot failed for service=%s", service)
        return None


def ensure_alert_snapshot(alert) -> Any:
    """If alert has no snapshot, attach a live Prom PNG + explore URL in metadata."""
    if getattr(alert, "grafana_snapshot", None):
        # Still attach explore link when Grafana URL is configured.
        s = get_settings()
        if s.grafana_url and "grafana_explore_url" not in (alert.metadata or {}):
            alert.metadata = dict(alert.metadata or {})
            alert.metadata["grafana_explore_url"] = explore_url(
                alert.service, grafana_base=s.grafana_url
            )
        return alert
    captured = capture_live_snapshot(alert.service)
    if not captured:
        return alert
    alert.grafana_snapshot = captured["grafana_snapshot"]
    meta = dict(alert.metadata or {})
    if captured.get("grafana_explore_url"):
        meta["grafana_explore_url"] = captured["grafana_explore_url"]
    meta["snapshot_source"] = captured.get("source", "prometheus")
    alert.metadata = meta
    return alert
