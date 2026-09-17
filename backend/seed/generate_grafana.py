"""Render realistic dark-theme Grafana-style snapshots for the vision agent.

One image per demo scenario — REAL images the Diagnosis agent reads with Gemini
vision, each showing a distinct failure signature so multimodal analysis has
genuine, scenario-specific signal:

  * checkout : 5xx error-rate + latency spike after a deploy
  * cart     : memory climbing to the pod limit + OOM restarts
  * payments : p50/p95/p99 latency blow-out + downstream saturation
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless render
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SEED_DIR = Path(__file__).resolve().parent
OUT = SEED_DIR / "grafana_checkout_spike.png"      # back-compat (checkout)
CART_OUT = SEED_DIR / "grafana_cart_oom.png"
PAYMENTS_OUT = SEED_DIR / "grafana_payments_latency.png"

# Grafana-ish palette
BG = "#0b0e14"
PANEL = "#12161f"
GRID = "#2a3242"
TEXT = "#c7d0e0"
GREEN = "#3fb950"
AMBER = "#d29922"
RED = "#f85149"
BLUE = "#58a6ff"
VIOLET = "#8957e5"


def _style_ax(ax, title: str) -> None:
    ax.set_facecolor(PANEL)
    ax.set_title(title, color=TEXT, fontsize=11, loc="left", fontweight="bold", pad=8)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.6)
    ax.tick_params(colors=TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))


def _timeline(minutes: int = 60):
    now = datetime.now()
    times = [now - timedelta(minutes=minutes - i) for i in range(minutes + 1)]
    x = np.array([mdates.date2num(t) for t in times])
    return x, len(times)


def _finish(fig, axes, out: Path, spike_start: int, x, deploy_label: str, y_top: float):
    for ax in axes:
        ax.axvline(x[spike_start], color=VIOLET, linestyle=":", linewidth=1.4, alpha=0.9)
    axes[0].annotate(deploy_label, xy=(x[spike_start], y_top), color="#b392f0",
                     fontsize=8, xytext=(x[spike_start] + 0.004, y_top))
    fig.autofmt_xdate()
    fig.subplots_adjust(left=0.09, right=0.97, top=0.92, bottom=0.07, hspace=0.35)
    fig.savefig(out, dpi=130, facecolor=BG)
    plt.close(fig)
    return out


# --------------------------------------------------------------------------- #
# checkout-svc — 5xx error rate + latency spike
# --------------------------------------------------------------------------- #
def generate() -> Path:
    x, n = _timeline()
    spike_start = n - 12
    rng = np.random.default_rng(7)
    step = np.zeros(n); step[spike_start:] = 1.0
    ramp = np.clip((np.arange(n) - spike_start) / 4.0, 0, 1)

    req = 820 + rng.normal(0, 18, n)
    err = np.clip(0.4 + rng.normal(0, 0.15, n) + step * ramp * 41.5, 0, 100)
    p50 = 45 + rng.normal(0, 4, n) + step * ramp * 120
    p95 = 130 + rng.normal(0, 10, n) + step * ramp * 520
    p99 = 210 + rng.normal(0, 16, n) + step * ramp * 900

    fig, axes = plt.subplots(3, 1, figsize=(11, 8.5), sharex=True)
    fig.patch.set_facecolor(BG)
    fig.suptitle("checkout-svc  ·  Production  ·  us-central1", color=TEXT, fontsize=14,
                 fontweight="bold", x=0.09, ha="left", y=0.965)

    _style_ax(axes[0], "Request rate (req/s)")
    axes[0].plot(x, req, color=BLUE, linewidth=1.6); axes[0].fill_between(x, req, color=BLUE, alpha=0.10)
    axes[0].set_ylim(0, 1100)
    _style_ax(axes[1], "HTTP 5xx error rate (%)")
    axes[1].plot(x, err, color=RED, linewidth=1.8); axes[1].fill_between(x, err, color=RED, alpha=0.15)
    axes[1].axhline(5, color=AMBER, linestyle="--", linewidth=1, alpha=0.8); axes[1].set_ylim(0, 55)
    _style_ax(axes[2], "Request latency (ms)")
    axes[2].plot(x, p50, color=GREEN, linewidth=1.4, label="p50")
    axes[2].plot(x, p95, color=AMBER, linewidth=1.4, label="p95")
    axes[2].plot(x, p99, color=RED, linewidth=1.6, label="p99")
    axes[2].legend(loc="upper left", facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=8)
    axes[2].set_ylim(0, 1250)
    return _finish(fig, axes, OUT, spike_start, x, "deploy v2.4.1", 1010)


# --------------------------------------------------------------------------- #
# cart-svc — memory climbing to the limit + OOM restarts
# --------------------------------------------------------------------------- #
def generate_cart() -> Path:
    x, n = _timeline()
    spike_start = n - 11
    rng = np.random.default_rng(11)
    idx = np.arange(n)
    ramp = np.clip((idx - spike_start) / 11.0, 0, 1)

    # Memory sawtooths up to the 512Mi limit, dropping on each OOM restart.
    mem = 55 + rng.normal(0, 2, n) + ramp * 48
    restarts = np.zeros(n, dtype=int)
    for r in (spike_start + 6, spike_start + 9):
        if r < n:
            mem[r:] -= 42
            restarts[r:] += 1
    mem = np.clip(mem, 40, 100)
    err = np.clip(0.5 + rng.normal(0, 0.2, n) + ramp * 12, 0, 100)

    fig, axes = plt.subplots(3, 1, figsize=(11, 8.5), sharex=True)
    fig.patch.set_facecolor(BG)
    fig.suptitle("cart-svc  ·  Production  ·  us-central1", color=TEXT, fontsize=14,
                 fontweight="bold", x=0.09, ha="left", y=0.965)

    _style_ax(axes[0], "Container memory (% of 512Mi limit)")
    axes[0].plot(x, mem, color=VIOLET, linewidth=1.8); axes[0].fill_between(x, mem, color=VIOLET, alpha=0.12)
    axes[0].axhline(100, color=RED, linestyle="--", linewidth=1, alpha=0.8)
    axes[0].annotate("OOM limit", xy=(x[2], 100), color=RED, fontsize=8, va="bottom")
    axes[0].set_ylim(0, 115)
    _style_ax(axes[1], "Pod restarts (cumulative)")
    axes[1].step(x, restarts, color=AMBER, linewidth=1.8, where="post")
    axes[1].fill_between(x, restarts, color=AMBER, alpha=0.15, step="post")
    axes[1].set_ylim(0, max(3, restarts.max() + 1))
    _style_ax(axes[2], "HTTP 5xx error rate (%)")
    axes[2].plot(x, err, color=RED, linewidth=1.6); axes[2].fill_between(x, err, color=RED, alpha=0.15)
    axes[2].set_ylim(0, 20)
    return _finish(fig, axes, CART_OUT, spike_start, x, "deploy v1.8.1", 105)


# --------------------------------------------------------------------------- #
# payments-svc — latency blow-out + downstream saturation
# --------------------------------------------------------------------------- #
def generate_payments() -> Path:
    x, n = _timeline()
    spike_start = n - 13
    rng = np.random.default_rng(23)
    idx = np.arange(n)
    ramp = np.clip((idx - spike_start) / 5.0, 0, 1)
    step = np.zeros(n); step[spike_start:] = 1.0

    p50 = 40 + rng.normal(0, 3, n) + step * ramp * 90
    p95 = 120 + rng.normal(0, 8, n) + step * ramp * 700
    p99 = 180 + rng.normal(0, 12, n) + step * ramp * 1320
    queue = 8 + rng.normal(0, 2, n) + step * ramp * 232          # thread-pool queue depth
    ledger = 60 + rng.normal(0, 6, n) + step * ramp * 2900       # downstream ledger latency ms

    fig, axes = plt.subplots(3, 1, figsize=(11, 8.5), sharex=True)
    fig.patch.set_facecolor(BG)
    fig.suptitle("payments-svc  ·  Production  ·  us-central1", color=TEXT, fontsize=14,
                 fontweight="bold", x=0.09, ha="left", y=0.965)

    _style_ax(axes[0], "Request latency (ms)")
    axes[0].plot(x, p50, color=GREEN, linewidth=1.4, label="p50")
    axes[0].plot(x, p95, color=AMBER, linewidth=1.4, label="p95")
    axes[0].plot(x, p99, color=RED, linewidth=1.6, label="p99")
    axes[0].axhline(300, color=AMBER, linestyle="--", linewidth=1, alpha=0.7)
    axes[0].legend(loc="upper left", facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=8)
    axes[0].set_ylim(0, 1650)
    _style_ax(axes[1], "Thread-pool queue depth")
    axes[1].plot(x, queue, color=BLUE, linewidth=1.6); axes[1].fill_between(x, queue, color=BLUE, alpha=0.12)
    axes[1].set_ylim(0, 280)
    _style_ax(axes[2], "Downstream ledger-svc latency (ms)")
    axes[2].plot(x, ledger, color=RED, linewidth=1.6); axes[2].fill_between(x, ledger, color=RED, alpha=0.15)
    axes[2].set_ylim(0, 3300)
    return _finish(fig, axes, PAYMENTS_OUT, spike_start, x, "deploy v5.5.3", 1520)


def generate_all() -> dict[str, Path]:
    return {"checkout": generate(), "cart": generate_cart(), "payments": generate_payments()}


if __name__ == "__main__":
    for name, path in generate_all().items():
        print(f"{name:9s} -> {path}")
