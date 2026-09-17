import { motion } from "framer-motion";
import {
  Activity,
  BrainCircuit,
  GitCompareArrows,
  Megaphone,
  Network,
  ScanEye,
  ShieldCheck,
  Siren,
  type LucideIcon,
} from "lucide-react";
import { AGENT_ORDER, type AgentName, type AgentPhase, type WarRoomState } from "../types";
import { PanelTitle } from "./ui";

const META: Record<AgentName, { icon: LucideIcon; accent: string; blurb: string }> = {
  Orchestrator: { icon: Network, accent: "#a78bfa", blurb: "Lifecycle & routing" },
  Triage: { icon: Siren, accent: "#ffb020", blurb: "Severity & blast radius" },
  Diagnosis: { icon: ScanEye, accent: "#4d9fff", blurb: "Logs + Grafana vision" },
  Correlation: { icon: GitCompareArrows, accent: "#4d9fff", blurb: "Deploy correlation" },
  Memory: { icon: BrainCircuit, accent: "#a78bfa", blurb: "Past incidents" },
  Remediation: { icon: ShieldCheck, accent: "#3ddc97", blurb: "Fix (human gate)" },
  Comms: { icon: Megaphone, accent: "#3ddc97", blurb: "RCA + notify" },
};

const PHASE_RING: Record<AgentPhase, string> = {
  idle: "border-white/10",
  active: "border-signal-blue",
  done: "border-signal-green/70",
  error: "border-signal-red",
};

function Node({
  name,
  phase,
  headline,
  toolsCount,
  active,
}: {
  name: AgentName;
  phase: AgentPhase;
  headline: string;
  toolsCount: number;
  active: boolean;
}) {
  const { icon: Icon, accent, blurb } = META[name];
  const dim = phase === "idle";
  return (
    <div className="relative flex w-[128px] shrink-0 flex-col items-center gap-2 text-center">
      {/* pulsing halo for the active agent */}
      {active && (
        <motion.span
          className="pointer-events-none absolute -top-1 h-16 w-16 rounded-2xl"
          style={{ boxShadow: `0 0 0 1px ${accent}55` }}
          animate={{ boxShadow: [`0 0 0 0px ${accent}66`, `0 0 0 12px ${accent}00`] }}
          transition={{ duration: 1.5, repeat: Infinity, ease: "easeOut" }}
        />
      )}
      <motion.div
        initial={false}
        animate={{ scale: active ? 1.06 : 1, opacity: dim ? 0.55 : 1 }}
        transition={{ type: "spring", stiffness: 260, damping: 20 }}
        className={`relative flex h-14 w-14 items-center justify-center rounded-2xl border bg-ink-800 ${PHASE_RING[phase]}`}
        style={active ? { boxShadow: `0 0 22px ${accent}40` } : undefined}
      >
        <Icon
          size={22}
          strokeWidth={2}
          style={{ color: dim ? "#64748b" : accent }}
        />
        {phase === "done" && (
          <span className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-signal-green text-[9px] font-bold text-ink-950">
            ✓
          </span>
        )}
        {phase === "error" && (
          <span className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-signal-red text-[10px] font-bold text-ink-950">
            !
          </span>
        )}
      </motion.div>
      <div className="leading-tight">
        <div className={`text-[13px] font-semibold ${dim ? "text-slate-500" : "text-slate-100"}`}>
          {name}
        </div>
        <div className="mt-0.5 h-7 text-[10px] text-slate-500">
          {active && headline ? (
            <span className="text-slate-300">{headline}</span>
          ) : (
            <>
              {blurb}
              {toolsCount > 0 && (
                <span className="ml-1 text-slate-600">· {toolsCount} tools</span>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Connector({ flowing, complete }: { flowing: boolean; complete: boolean }) {
  return (
    <div className="relative mt-6 h-[3px] flex-1 min-w-[24px] overflow-hidden rounded-full bg-white/[0.06]">
      {complete && !flowing && (
        <div className="absolute inset-0 bg-signal-green/50" />
      )}
      {flowing && (
        <>
          <div className="absolute inset-0 bg-signal-blue/25" />
          <motion.div
            className="absolute inset-y-0 w-1/3 rounded-full bg-gradient-to-r from-transparent via-signal-blue to-transparent"
            animate={{ x: ["-120%", "320%"] }}
            transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
          />
        </>
      )}
    </div>
  );
}

export default function AgentGraph({ state }: { state: WarRoomState }) {
  const anyActive = !!state.activeAgent;
  return (
    <section className="panel overflow-hidden">
      <PanelTitle
        icon={<Activity size={15} className="text-signal-blue" />}
        right={
          <span className="chip border-white/10 bg-white/5 text-slate-400">
            {anyActive ? (
              <>
                <span className="h-1.5 w-1.5 animate-blink rounded-full bg-signal-blue" />
                {state.activeAgent} working
              </>
            ) : state.done ? (
              "pipeline complete"
            ) : (
              "idle"
            )}
          </span>
        }
      >
        Agent Pipeline
      </PanelTitle>

      <div className="grid-bg overflow-x-auto px-5 py-6">
        <div className="flex min-w-[820px] items-start">
          {AGENT_ORDER.map((name, i) => {
            const a = state.agents[name];
            const active = state.activeAgent === name;
            const node = (
              <Node
                key={name}
                name={name}
                phase={a.phase}
                headline={a.headline}
                toolsCount={a.tools.length}
                active={active}
              />
            );
            if (i === AGENT_ORDER.length - 1) return node;
            const next = state.agents[AGENT_ORDER[i + 1]];
            const complete = a.phase === "done" && next.phase !== "idle";
            const flowing =
              (a.phase === "done" || a.phase === "active") && next.phase === "active";
            return (
              <div key={name} className="flex flex-1 items-start">
                {node}
                <Connector flowing={flowing} complete={complete} />
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
