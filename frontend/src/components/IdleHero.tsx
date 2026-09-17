import { motion } from "framer-motion";
import {
  BrainCircuit,
  GitCompareArrows,
  Megaphone,
  Network,
  ScanEye,
  ShieldCheck,
  Siren,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { AGENT_ORDER, type AgentName } from "../types";
import { Spinner } from "./ui";

const ICON: Record<AgentName, { icon: LucideIcon; accent: string }> = {
  Orchestrator: { icon: Network, accent: "#a78bfa" },
  Triage: { icon: Siren, accent: "#ffb020" },
  Diagnosis: { icon: ScanEye, accent: "#4d9fff" },
  Correlation: { icon: GitCompareArrows, accent: "#4d9fff" },
  Memory: { icon: BrainCircuit, accent: "#a78bfa" },
  Remediation: { icon: ShieldCheck, accent: "#3ddc97" },
  Comms: { icon: Megaphone, accent: "#3ddc97" },
};

// Friendly empty-state shown before any incident exists. Replaces the wall of
// empty panels with a single clear call-to-action + a calm preview of the crew.
export default function IdleHero({
  firing,
  onFire,
}: {
  firing: boolean;
  onFire: () => void;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="panel grid-bg mx-auto flex w-full max-w-3xl flex-col items-center gap-7 px-8 py-14 text-center"
    >
      <motion.span
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 200, damping: 18, delay: 0.05 }}
        className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-signal-blue/25 to-signal-violet/20 text-signal-blue ring-1 ring-white/10"
      >
        <ShieldCheck size={30} />
      </motion.span>

      <div className="space-y-2.5">
        <h1 className="text-2xl font-bold tracking-tight text-slate-50">
          No active incident
        </h1>
        <p className="mx-auto max-w-lg text-[14px] leading-relaxed text-slate-400">
          Fire an incident to watch seven AI agents triage, diagnose, correlate with recent
          deploys, recall past incidents, and propose a fix — gated by your approval before
          anything destructive runs.
        </p>
      </div>

      {/* Calm preview of the pipeline the operator is about to see come alive */}
      <div className="flex flex-wrap items-center justify-center gap-2.5 opacity-70">
        {AGENT_ORDER.map((name, i) => {
          const { icon: Icon, accent } = ICON[name];
          return (
            <div key={name} className="flex items-center gap-2.5">
              <div className="flex flex-col items-center gap-1.5">
                <span
                  className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-ink-800"
                  style={{ color: accent }}
                >
                  <Icon size={17} strokeWidth={2} />
                </span>
                <span className="text-[9.5px] font-medium text-slate-500">{name}</span>
              </div>
              {i < AGENT_ORDER.length - 1 && (
                <span className="mb-4 h-px w-4 bg-white/10" />
              )}
            </div>
          );
        })}
      </div>

      <button onClick={onFire} disabled={firing} className="btn btn-primary px-6 py-3 text-[15px]">
        {firing ? <Spinner /> : <Zap size={17} />} Fire Incident
      </button>
      <p className="-mt-3 text-[11px] text-slate-600">
        Simulates a <span className="font-mono text-slate-500">HighErrorRate</span> alert on{" "}
        <span className="font-mono text-slate-500">checkout-svc</span>.
      </p>
    </motion.section>
  );
}
