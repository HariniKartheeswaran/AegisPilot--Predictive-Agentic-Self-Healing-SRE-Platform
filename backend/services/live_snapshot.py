"""Live Grafana/Prom snapshot for Diagnosis vision.

Pulls the same Prometheus series as the AegisPilot Grafana board and renders a
4-panel dark PNG (request rate · errors · avg latency · p95) for Gemini vision.
Also attaches an Open-in-Grafana deep-link to the live dashboard.

Optional: set GRAFANA_TOKEN to prefer a real Grafana /render screenshot when the
image-renderer plugin is installed; otherwise the Prom 4-panel PNG is used.

Snapshots live under a process-owned temp dir (or ``AEGIS_SNAPSHOT_DIR``)
plus base64 on alert.metadata so the UI survives pod recycle.
"""
from __future__ import annotations

import base64
import logging
import os
import tempfile
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
GREEN = "#3fb950"   # cart
YELLOW = "#d29922"  # checkout
BLUE = "#58a6ff"    # payments
ORANGE = "#f0883e"  # 5xx
RED = "#f85149"
AMBER = "#d29922"


def _snapshot_dir() -> Path:
    """App-owned snapshot dir (not a world-writable path literal)."""
    raw = (os.environ.get("AEGIS_SNAPSHOT_DIR") or "").strip()
    base = Path(raw) if raw else Path(tempfile.gettempdir()) / "aegis_snapshots"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


SNAP_DIR = _snapshot_dir()
_SCRAPE_SERVICES = {"checkout-svc", "cart-svc", "payments-svc"}
# PromQL for cluster-wide up series (duplicated literal → single constant for Sonar).
_UP_AEGIS_JOB = 'up{job=~"aegis-.*"}'
_SVC_COLOR = {
    "cart-svc": GREEN,
    "checkout-svc": YELLOW,
    "payments-svc": BLUE,
}


def _sel(service: str) -> str:
    if service in _SCRAPE_SERVICES:
        return f'service="{service}"'
    return 'job=~"aegis-.*"'


def _error_rate_query(service: str) -> str:
    sel = _sel(service)
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
    return _UP_AEGIS_JOB


def explore_url(service: str, *, grafana_base: str) -> str:
    """Deep-link into the live AegisPilot Grafana dashboard (never Explore panes).

    Explore ``panes=`` URLs break on current Grafana ("Could not parse Explore URL").
    """
    base = grafana_base.rstrip("/")
    s = get_settings()
    dash = (
        getattr(s, "grafana_dashboard_path", None)
        or os.environ.get("GRAFANA_DASHBOARD_PATH")
        or "/d/ad6nckx/aegispilot-dashboard"
    ).strip() or "/d/ad6nckx/aegispilot-dashboard"
    if not dash.startswith("/"):
        dash = "/" + dash
    svc = quote(service, safe="")
    return (
        f"{base}{dash}"
        f"?orgId=1&from=now-1h&to=now&timezone=browser&refresh=10s"
        f"&var-service={svc}"
    )


def _query_range(
    prom_url: str, query: str, minutes: int = 60, step: str = "15s"
) -> list[tuple[float, float]]:
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
    by_ts: dict[float, list[float]] = {}
    for series in result:
        for ts, val in series.get("values") or []:
            try:
                t, v = float(ts), float(val)
            except (TypeError, ValueError):
                continue
            by_ts.setdefault(t, []).append(v)
    return sorted((t, sum(vs) / len(vs)) for t, vs in by_ts.items())


def _style_ax(ax, title: str, ylabel: str) -> None:
    ax.set_facecolor(PANEL)
    ax.set_title(title, color=TEXT, fontsize=10, loc="left", fontweight="bold", pad=6)
    ax.grid(True, color=GRID, linewidth=0.5, alpha=0.55)
    ax.tick_params(colors=TEXT, labelsize=7)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.set_ylabel(ylabel, color=TEXT, fontsize=8)


def _plot_line(ax, pts: list[tuple[float, float]], color: str, label: str = "") -> None:
    if not pts:
        ax.text(0.5, 0.5, "no series", transform=ax.transAxes, ha="center",
                color=AMBER, fontsize=9)
        return
    xs = [mdates.date2num(datetime.fromtimestamp(t, tz=timezone.utc)) for t, _ in pts]
    ys = [v for _, v in pts]
    ax.plot(xs, ys, color=color, linewidth=1.6, label=label or None)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    if label:
        ax.legend(loc="upper right", fontsize=7, facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)


def _render_dashboard_png(
    service: str,
    rate_pts: list[tuple[float, float]],
    ok_pts: list[tuple[float, float]],
    err_pts: list[tuple[float, float]],
    avg_pts: list[tuple[float, float]],
    p95_pts: list[tuple[float, float]],
    out: Path,
) -> Path:
    """4-panel layout mirroring AegisPilot Dashboard (live Prom data)."""
    color = _SVC_COLOR.get(service, YELLOW)
    fig, axes = plt.subplots(2, 2, figsize=(11.2, 6.2), sharex=True)
    fig.patch.set_facecolor(BG)

    _style_ax(axes[0, 0], "HTTP Request Rate", "req/s")
    _plot_line(axes[0, 0], rate_pts, color, service)

    _style_ax(axes[0, 1], "HTTP Success / 5xx rate", "req/s")
    _plot_line(axes[0, 1], ok_pts, color, f'{service} 200')
    _plot_line(axes[0, 1], err_pts, ORANGE, f'{service} 5xx')
    if ok_pts or err_pts:
        axes[0, 1].legend(loc="upper right", fontsize=7, facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)

    _style_ax(axes[1, 0], "Average Request Duration", "ms")
    avg_ms = [(t, v * 1000.0) for t, v in avg_pts]
    _plot_line(axes[1, 0], avg_ms, color, service)

    _style_ax(axes[1, 1], "P95 Request Latency", "s")
    _plot_line(axes[1, 1], p95_pts, color, service)

    fig.suptitle(
        f"AegisPilot Dashboard · {service} · live Prometheus",
        color=TEXT, fontsize=12, fontweight="bold", y=0.98,
    )
    fig.autofmt_xdate()
    fig.subplots_adjust(left=0.07, right=0.98, top=0.90, bottom=0.08, hspace=0.32, wspace=0.22)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, facecolor=BG)
    plt.close(fig)
    return out


def _try_grafana_render(service: str, grafana_base: str, token: str) -> Optional[bytes]:
    """Best-effort real Grafana dashboard PNG (needs API token + image renderer)."""
    s = get_settings()
    dash = (
        getattr(s, "grafana_dashboard_path", None)
        or os.environ.get("GRAFANA_DASHBOARD_PATH")
        or "/d/ad6nckx/aegispilot-dashboard"
    ).strip()
    if not dash.startswith("/"):
        dash = "/" + dash
    # /d/UID/slug → /render/d/UID/slug
    render_path = "/render" + dash if dash.startswith("/d/") else f"/render{dash}"
    url = (
        f"{grafana_base.rstrip('/')}{render_path}"
        f"?orgId=1&from=now-1h&to=now&width=1200&height=700"
        f"&tz=UTC&var-service={quote(service, safe='')}"
    )
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=25.0, follow_redirects=False) as client:
        r = client.get(url, headers=headers)
        if r.status_code != 200 or not r.content.startswith(b"\x89PNG"):
            log.warning("grafana render unavailable status=%s bytes=%d", r.status_code, len(r.content))
            return None
        return r.content


def capture_live_snapshot(service: str) -> Optional[dict[str, Any]]:
    """Fetch live metrics + render PNG. Prefers Grafana render when token set."""
    s = get_settings()
    prom = (s.prometheus_url or "").strip()
    if not prom:
        return None
    grafana = (s.grafana_url or "").strip()
    token = (
        getattr(s, "grafana_token", None)
        or os.environ.get("GRAFANA_TOKEN")
        or ""
    ).strip()
    try:
        stamp = int(time.time() * 1000)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in service)
        abs_path = SNAP_DIR / f"{safe}_{stamp}.png"
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        source = "prometheus"

        rendered: Optional[bytes] = None
        if grafana and token:
            rendered = _try_grafana_render(service, grafana, token)
            if rendered:
                abs_path.write_bytes(rendered)
                source = "grafana-render"

        if rendered is None:
            sel = _sel(service)
            rate_pts = _query_range(prom, f'sum(rate(http_requests_total{{{sel}}}[1m])) or vector(0)')
            ok_pts = _query_range(
                prom, f'sum(rate(http_requests_total{{{sel},code=~"2.."}}[1m])) or vector(0)'
            )
            err5_pts = _query_range(
                prom, f'sum(rate(http_requests_total{{{sel},code=~"5.."}}[1m])) or vector(0)'
            )
            avg_pts = _query_range(
                prom,
                f"("
                f'sum(rate(http_request_duration_seconds_sum{{{sel}}}[1m])) '
                f"/ "
                f'clamp_min(sum(rate(http_request_duration_seconds_count{{{sel}}}[1m])), 1e-9)'
                f") or vector(0)",
            )
            p95_pts = _query_range(
                prom,
                f"histogram_quantile(0.95, "
                f"sum by (le) (rate(http_request_duration_seconds_bucket{{{sel}}}[1m]))) "
                f"or vector(0)",
            )
            # Fallback: if request series empty, still show up + 5xx %
            if not rate_pts and not err5_pts:
                up_pts = _query_range(prom, _up_query(service)) or _query_range(
                    prom, _UP_AEGIS_JOB
                )
                pct_pts = _query_range(prom, _error_rate_query(service))
                _render_dashboard_png(service, up_pts, [], pct_pts, [], [], abs_path)
            else:
                _render_dashboard_png(
                    service, rate_pts, ok_pts, err5_pts, avg_pts, p95_pts, abs_path
                )

        raw = abs_path.read_bytes()
        link = explore_url(service, grafana_base=grafana) if grafana else ""
        log.info(
            "live snapshot for %s → %s (%s, %d bytes)",
            service, abs_path, source, len(raw),
        )
        return {
            "grafana_snapshot": str(abs_path),
            "grafana_explore_url": link,
            "grafana_snapshot_b64": base64.b64encode(raw).decode("ascii"),
            "source": source,
        }
    except Exception:  # noqa: BLE001
        log.exception("live snapshot failed for service=%s", service)
        return None


def ensure_alert_snapshot(alert) -> Any:
    """Attach a live Prom/Grafana PNG when Prometheus is configured."""
    s = get_settings()
    seed = getattr(alert, "grafana_snapshot", None)

    def _attach_explore_only() -> Any:
        if s.grafana_url and "grafana_explore_url" not in (alert.metadata or {}):
            alert.metadata = dict(alert.metadata or {})
            alert.metadata["grafana_explore_url"] = explore_url(
                alert.service, grafana_base=s.grafana_url
            )
        return alert

    def _is_seed_path(path: Any) -> bool:
        return bool(path) and "backend/seed" in str(path).replace("\\", "/")

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
            if _is_seed_path(seed):
                meta["seed_snapshot_replaced"] = str(seed)
            alert.metadata = meta
            return alert
        # Do not keep demo seed PNGs when live Prom is configured but capture failed.
        if _is_seed_path(seed):
            alert.grafana_snapshot = None
        return _attach_explore_only()

    if _is_seed_path(seed) and (s.grafana_url or "").strip():
        # Still attach a dashboard link even if we refuse to show seed art.
        return _attach_explore_only()
    return _attach_explore_only() if seed else alert
