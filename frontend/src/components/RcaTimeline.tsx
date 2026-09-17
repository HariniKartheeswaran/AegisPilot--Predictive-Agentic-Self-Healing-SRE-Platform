import { motion } from "framer-motion";
import { Clock, FileText, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { AuditStep, WarRoomState } from "../types";
import { Empty, Spinner } from "./ui";
import Markdown from "./Markdown";

const AGENT_DOT: Record<string, string> = {
  Orchestrator: "bg-signal-violet",
  Triage: "bg-signal-amber",
  Diagnosis: "bg-signal-blue",
  Correlation: "bg-signal-blue",
  Memory: "bg-signal-violet",
  Remediation: "bg-signal-green",
  Comms: "bg-signal-green",
};

function fmtTime(ts: number) {
  return new Date(ts).toLocaleTimeString([], { hour12: false });
}

// Body-only RCA + timeline view. The rendered markdown postmortem and the
// audited agent timeline are two sub-tabs so only one dense thing shows at once.
export default function RcaTimeline({ state }: { state: WarRoomState }) {
  const id = state.incidentId;
  const [tab, setTab] = useState<"rca" | "timeline">("rca");
  const [rca, setRca] = useState<string>("");
  const [audit, setAudit] = useState<AuditStep[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const [r, a] = await Promise.all([api.rca(id), api.audit(id)]);
      setRca(r?.rca || "");
      setAudit(Array.isArray(a) ? [...a].sort((x, y) => x.ts - y.ts) : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [id]);

  // Refresh when the incident reaches a terminal state (RCA is written last).
  useEffect(() => {
    if (id) load();
  }, [id, state.done, load]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Sub-tab toggle + manual refresh */}
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-2.5">
        <div className="flex rounded-lg border border-white/10 p-0.5">
          {(["rca", "timeline"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-md px-3 py-1 text-[11px] font-medium capitalize transition-colors ${
                tab === t ? "bg-white/10 text-slate-100" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {t === "rca" ? "Postmortem" : "Timeline"}
            </button>
          ))}
        </div>
        <button
          onClick={load}
          disabled={!id || loading}
          className="btn-ghost rounded-lg p-1.5 disabled:opacity-30"
          aria-label="Refresh"
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {!id ? (
          <Empty>
            <FileText size={20} className="text-slate-600" />
            <div>No incident selected.</div>
          </Empty>
        ) : error ? (
          <Empty>
            <div className="text-signal-amber">Couldn't load the postmortem yet.</div>
            <div className="text-slate-600">It's written once the incident closes.</div>
            <button onClick={load} className="btn btn-ghost mt-2">
              <RefreshCw size={13} /> Retry
            </button>
          </Empty>
        ) : loading && !rca && audit.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <Spinner />
          </div>
        ) : tab === "rca" ? (
          rca ? (
            <div className="rounded-lg border border-white/[0.06] bg-ink-900/30 p-5">
              <Markdown source={rca} />
            </div>
          ) : (
            <Empty>
              <FileText size={20} className="text-slate-600" />
              <div>RCA not generated yet.</div>
              <div className="text-slate-600">
                The Comms agent writes it once the incident closes.
              </div>
            </Empty>
          )
        ) : audit.length === 0 ? (
          <Empty>
            <Clock size={20} className="text-slate-600" />
            <div>No audit steps recorded yet.</div>
          </Empty>
        ) : (
          <ol className="relative ml-2 border-l border-white/10">
            {audit.map((s, i) => (
              <motion.li
                key={s.id}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(i * 0.015, 0.3) }}
                className="mb-4 ml-4"
              >
                <span
                  className={`absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-ink-850 ${
                    AGENT_DOT[s.agent] ?? "bg-slate-500"
                  }`}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[12px] font-semibold text-slate-200">{s.agent}</span>
                  <span className="font-mono text-[10.5px] text-slate-500">{s.step}</span>
                  <span className="ml-auto font-mono text-[10px] text-slate-600">
                    {fmtTime(s.ts)}
                  </span>
                </div>
                {(s.reasoning || s.tool_call) && (
                  <p className="mt-1 line-clamp-3 font-mono text-[11px] leading-relaxed text-slate-400">
                    {s.tool_call ? `⚙ ${s.tool_call}` : s.reasoning}
                  </p>
                )}
                {(s.tokens > 0 || s.latency_ms > 0) && (
                  <div className="mt-1 flex gap-1.5">
                    {s.tokens > 0 && (
                      <span className="chip border-white/10 bg-white/5 text-slate-500">
                        {s.tokens} tok
                      </span>
                    )}
                    {s.latency_ms > 0 && (
                      <span className="chip border-signal-blue/20 bg-signal-blue/10 text-signal-blue">
                        {s.latency_ms} ms
                      </span>
                    )}
                  </div>
                )}
              </motion.li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
