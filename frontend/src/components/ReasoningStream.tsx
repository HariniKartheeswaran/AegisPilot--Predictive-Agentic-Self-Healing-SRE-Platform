import { AnimatePresence, motion } from "framer-motion";
import { Cpu, Terminal, Wrench, Zap } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import type { StreamLine, WarRoomState } from "../types";
import { Empty, PanelTitle } from "./ui";

const AGENT_COLOR: Record<string, string> = {
  Orchestrator: "text-signal-violet",
  Triage: "text-signal-amber",
  Diagnosis: "text-signal-blue",
  Correlation: "text-signal-blue",
  Memory: "text-signal-violet",
  Remediation: "text-signal-green",
  Comms: "text-signal-green",
};

function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: string }) {
  const map: Record<string, string> = {
    slate: "border-white/10 bg-white/5 text-slate-400",
    blue: "border-signal-blue/30 bg-signal-blue/10 text-signal-blue",
    green: "border-signal-green/30 bg-signal-green/10 text-signal-green",
    amber: "border-signal-amber/30 bg-signal-amber/10 text-signal-amber",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-[1px] text-[10px] font-medium ${map[tone]}`}>
      {children}
    </span>
  );
}

function Line({ line }: { line: StreamLine }) {
  const color = AGENT_COLOR[line.agent] ?? "text-slate-300";

  if (line.kind === "reasoning_start") {
    return (
      <div className="flex items-center gap-2 py-1 pl-1 text-[11px] text-slate-500">
        <span className={`font-semibold ${color}`}>{line.agent}</span>
        <span className="italic">{line.step ?? "thinking"}</span>
        <span className="flex gap-0.5">
          <span className="h-1 w-1 animate-blink rounded-full bg-slate-500" />
          <span className="h-1 w-1 animate-blink rounded-full bg-slate-500" style={{ animationDelay: "0.2s" }} />
          <span className="h-1 w-1 animate-blink rounded-full bg-slate-500" style={{ animationDelay: "0.4s" }} />
        </span>
      </div>
    );
  }

  if (line.kind === "system") {
    return (
      <div className="flex items-start gap-2 border-l-2 border-white/10 py-1 pl-2 text-[11px] text-slate-400">
        <Cpu size={12} className="mt-0.5 shrink-0 text-slate-500" />
        <span>
          <span className={`font-semibold ${color}`}>{line.agent}</span> · {line.text}
        </span>
      </div>
    );
  }

  if (line.kind === "tool") {
    return (
      <div className="rounded-md border border-white/[0.06] bg-ink-900/60 px-2.5 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <Wrench size={12} className="text-signal-amber" />
          <span className={`text-[11px] font-semibold ${color}`}>{line.agent}</span>
          <Badge tone="amber">{line.tool}</Badge>
        </div>
        {line.detail && (
          <div className="mt-1 break-words font-mono text-[11px] text-slate-400">{line.detail}</div>
        )}
        {line.output && (
          <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words rounded bg-black/30 px-2 py-1 font-mono text-[10.5px] leading-relaxed text-slate-500">
            {line.output}
          </pre>
        )}
      </div>
    );
  }

  // reasoning
  return (
    <div className="rounded-md border border-white/[0.06] bg-ink-900/40 px-2.5 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Zap size={12} className="text-signal-blue" />
        <span className={`text-[11px] font-semibold ${color}`}>{line.agent}</span>
        {line.step && <span className="text-[10px] text-slate-500">{line.step}</span>}
        <span className="ml-auto flex items-center gap-1">
          {typeof line.tokens === "number" && <Badge tone="slate">{line.tokens} tok</Badge>}
          {typeof line.latency_ms === "number" && (
            <Badge tone="blue">{line.latency_ms} ms</Badge>
          )}
          {typeof line.attempts === "number" && line.attempts > 1 && (
            <Badge tone="amber">{line.attempts}× retry</Badge>
          )}
        </span>
      </div>
      <div className="mt-1.5 whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-slate-200">
        {line.text}
      </div>
      {line.model && (
        <div className="mt-1 text-right font-mono text-[9.5px] text-slate-600">{line.model}</div>
      )}
    </div>
  );
}

export default function ReasoningStream({ state }: { state: WarRoomState }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  // Auto-follow the tail unless the operator has scrolled up to read history.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [state.lines]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  };

  return (
    <section className="panel flex min-h-0 flex-col">
      <PanelTitle
        icon={<Terminal size={15} className="text-signal-green" />}
        right={<span className="chip border-white/10 bg-white/5 text-slate-400">{state.lines.length} events</span>}
      >
        Reasoning Stream
      </PanelTitle>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 space-y-1.5 overflow-y-auto px-3 py-3"
      >
        {state.lines.length === 0 ? (
          <Empty>
            <Terminal size={20} className="text-slate-600" />
            <div>No telemetry yet.</div>
            <div className="text-slate-600">Fire a demo alert to watch the agents reason live.</div>
          </Empty>
        ) : (
          <AnimatePresence initial={false}>
            {state.lines.map((line) => (
              <motion.div
                key={line.id}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.18 }}
              >
                <Line line={line} />
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </div>
    </section>
  );
}
