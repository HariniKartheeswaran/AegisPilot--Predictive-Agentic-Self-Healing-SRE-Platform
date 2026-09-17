import { motion } from "framer-motion";
import {
  Brain,
  CheckCircle2,
  Eye,
  GitCommitHorizontal,
  ImageOff,
  Siren,
  Target,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { WarRoomState } from "../types";
import { ConfidenceBar, SeverityBadge, Spinner } from "./ui";

const dash = <span className="text-slate-600">—</span>;

function Insight({
  icon: Icon,
  label,
  accent,
  children,
}: {
  icon: LucideIcon;
  label: string;
  accent: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-ink-900/40 p-3">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
        <Icon size={12} style={{ color: accent }} />
        {label}
      </div>
      <div className="mt-1.5 text-[13px] text-slate-200">{children}</div>
    </div>
  );
}

// Body-only Diagnosis view: the exact Grafana image the vision agent read, its
// AI annotation overlaid, plus the key structured findings. Every field access
// is guarded so a missing/partial payload can never throw.
export default function DiagnosisPanel({ state }: { state: WarRoomState }) {
  const id = state.incidentId;
  const vision = state.vision;
  const mem = state.memory;
  const [imgStatus, setImgStatus] = useState<"loading" | "ok" | "error">("loading");

  // Reset image status whenever the incident changes so a new snapshot reloads.
  useEffect(() => {
    setImgStatus("loading");
  }, [id]);

  const confirmed = vision?.confirmed;
  const confirmChip =
    confirmed === true ? (
      <span className="chip border-signal-green/40 bg-signal-green/10 text-signal-green">
        <CheckCircle2 size={12} /> confirmed
      </span>
    ) : confirmed === false ? (
      <span className="chip border-signal-red/40 bg-signal-red/10 text-signal-red">
        <XCircle size={12} /> not confirmed
      </span>
    ) : null;

  return (
    <div className="space-y-4 p-4">
      {/* Grafana snapshot + vision annotation overlay */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            <Eye size={12} className="text-signal-violet" /> Grafana snapshot · vision
          </div>
          {confirmChip}
        </div>
        <div className="relative overflow-hidden rounded-lg border border-white/10 bg-black/40">
          {id && imgStatus === "loading" && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-ink-900/60">
              <Spinner />
            </div>
          )}
          {!id || imgStatus === "error" ? (
            <div className="flex h-44 flex-col items-center justify-center gap-2 text-xs text-slate-500">
              <ImageOff size={22} />
              {id ? "Snapshot unavailable" : "No snapshot loaded"}
            </div>
          ) : (
            <>
              <img
                key={id}
                src={api.grafanaUrl(id)}
                alt="Grafana dashboard analyzed by the vision agent"
                className="block w-full object-contain"
                onLoad={() => setImgStatus("ok")}
                onError={() => setImgStatus("error")}
              />
              {vision?.annotation && imgStatus === "ok" && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="absolute inset-x-2 bottom-2 rounded-md border border-signal-violet/40 bg-ink-950/85 px-3 py-2 backdrop-blur-sm"
                >
                  <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-signal-violet">
                    <Eye size={11} /> AI annotation
                  </div>
                  <div className="mt-0.5 font-mono text-[11.5px] text-slate-100">
                    {vision.annotation}
                  </div>
                </motion.div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Key structured insights */}
      <div className="grid grid-cols-2 gap-2.5">
        <Insight icon={Siren} label="Severity" accent="#ffb020">
          <SeverityBadge severity={state.severity} />
        </Insight>

        <Insight icon={Target} label="Blast radius" accent="#ffb020">
          {state.blastRadius ? (
            <span className="text-[12.5px] leading-snug">{state.blastRadius}</span>
          ) : (
            dash
          )}
        </Insight>

        <Insight icon={GitCommitHorizontal} label="Probable root cause" accent="#4d9fff">
          {state.probableCause ? (
            <>
              <span className="text-[12.5px] leading-snug">{state.probableCause}</span>
              {typeof state.confidence === "number" && (
                <ConfidenceBar value={state.confidence} />
              )}
            </>
          ) : (
            dash
          )}
        </Insight>

        <Insight icon={Brain} label="Memory" accent="#a78bfa">
          {mem && mem.times_seen != null ? (
            <span className="text-[12.5px] leading-snug">
              Seen <span className="font-semibold text-slate-100">{mem.times_seen}×</span>
              {mem.avg_resolution_minutes != null && (
                <>
                  {" "}· avg{" "}
                  <span className="font-mono text-slate-100">{mem.avg_resolution_minutes}m</span>
                </>
              )}
              {mem.similarity != null && (
                <span className="ml-1 text-slate-500">
                  ({Math.round(mem.similarity * 100)}% match)
                </span>
              )}
            </span>
          ) : mem ? (
            <span className="text-slate-500">no strong prior</span>
          ) : (
            dash
          )}
        </Insight>
      </div>

      {/* Full vision observation */}
      {vision?.observation ? (
        <div className="rounded-lg border border-white/[0.06] bg-ink-900/40 p-3">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            Vision observation
          </div>
          <p className="mt-1.5 font-mono text-[11.5px] leading-relaxed text-slate-300">
            {vision.observation}
          </p>
        </div>
      ) : (
        <p className="px-1 text-[11px] text-slate-500">
          Waiting for the Diagnosis agent to read this dashboard with Gemini vision…
        </p>
      )}
    </div>
  );
}
