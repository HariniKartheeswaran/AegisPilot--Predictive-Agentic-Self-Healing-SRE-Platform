import { Boxes, Clock, FlaskConical, Radio, ShieldAlert, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import type { Health } from "../api";
import type { ConnState } from "../useIncidentStream";
import type { WarRoomState } from "../types";
import { SeverityBadge, Spinner, StatusPill } from "./ui";

function useElapsed(from: number | null, frozen: number | null): string {
  const [, tick] = useState(0);
  useEffect(() => {
    if (from == null || frozen != null) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [from, frozen]);

  if (frozen != null) {
    const m = Math.floor(frozen);
    const s = Math.round((frozen - m) * 60);
    return `${m}m ${s.toString().padStart(2, "0")}s`;
  }
  if (from == null) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - from) / 1000));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

const CONN: Record<ConnState, { label: string; cls: string }> = {
  connecting: { label: "connecting", cls: "text-signal-amber" },
  open: { label: "live", cls: "text-signal-green" },
  error: { label: "reconnecting", cls: "text-signal-red" },
};

export default function Header({
  state,
  conn,
  health,
  firing,
  onFire,
  onOpenCustom,
  onOpenRegistry,
}: {
  state: WarRoomState;
  conn: ConnState;
  health: Health | null;
  firing: boolean;
  onFire: () => void;
  onOpenCustom: () => void;
  onOpenRegistry: () => void;
}) {
  const terminal =
    state.status === "RESOLVED" || state.status === "REJECTED" || state.status === "FAILED";
  const elapsed = useElapsed(
    state.detectedAt,
    terminal ? state.resolutionMinutes ?? (state.resolvedAt && state.detectedAt ? (state.resolvedAt - state.detectedAt) / 60000 : null) : null
  );
  const c = CONN[conn];

  return (
    <header className="sticky top-0 z-30 border-b border-white/[0.07] bg-ink-900/80 backdrop-blur-md">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3">
        {/* Brand */}
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-signal-blue/30 to-signal-violet/20 text-signal-blue ring-1 ring-white/10">
            <ShieldAlert size={18} />
          </span>
          <div className="leading-tight">
            <div className="flex items-center gap-2">
              <span className="text-[15px] font-bold tracking-tight text-slate-50">AegisPilot</span>
              <span className="rounded bg-white/5 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-widest text-slate-400">
                War Room
              </span>
            </div>
            <div className={`flex items-center gap-1 text-[10px] font-medium ${c.cls}`}>
              <Radio size={10} className={conn === "open" ? "animate-blink" : ""} /> {c.label}
              {health && (
                <span className="ml-1 text-slate-600">· {health.model}</span>
              )}
            </div>
          </div>
        </div>

        {/* Incident summary */}
        <div className="flex flex-wrap items-center gap-2.5">
          <StatusPill status={state.status} />
          <SeverityBadge severity={state.severity} />
          {state.service && (
            <span className="chip border-white/10 bg-white/5 font-mono text-slate-300">
              {state.service}
            </span>
          )}
          <span className="chip border-white/10 bg-white/5 text-slate-400">
            <Clock size={12} className={terminal ? "" : "text-signal-blue"} />
            <span className="font-mono">{elapsed}</span>
          </span>
        </div>

        {/* Actions */}
        <div className="ml-auto flex items-center gap-2">
          <button onClick={onOpenRegistry} className="btn btn-ghost">
            <Boxes size={15} /> Registry
          </button>
          <button onClick={onOpenCustom} className="btn btn-ghost">
            <FlaskConical size={15} /> Custom
          </button>
          <button onClick={onFire} disabled={firing} className="btn btn-primary">
            {firing ? <Spinner /> : <Zap size={15} />} Fire Incident
          </button>
        </div>
      </div>
    </header>
  );
}
