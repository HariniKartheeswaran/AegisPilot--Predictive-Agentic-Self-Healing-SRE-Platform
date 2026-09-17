import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, ArrowRight, Check, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import type { RemediationPlan } from "../types";
import { RiskBadge, Spinner } from "./ui";

export default function ApprovalModal({
  incidentId,
  plan,
  open,
  onResolved,
}: {
  incidentId: string;
  plan: RemediationPlan;
  open: boolean;
  onResolved: (decision: "approved" | "rejected") => void;
}) {
  const [busy, setBusy] = useState<null | "approve" | "reject">(null);
  const [error, setError] = useState<string | null>(null);
  const approveRef = useRef<HTMLButtonElement>(null);

  const submit = async (kind: "approve" | "reject") => {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      if (kind === "approve") await api.approve(incidentId);
      else await api.reject(incidentId);
      onResolved(kind === "approve" ? "approved" : "rejected");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
      setBusy(null);
    }
  };

  // Keyboard: Enter approves, Esc rejects. Focus the primary action on open.
  useEffect(() => {
    if (!open) return;
    approveRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        submit("approve");
      } else if (e.key === "Escape") {
        e.preventDefault();
        submit("reject");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="approval-title"
        >
          <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />
          <motion.div
            initial={{ scale: 0.94, y: 12, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.96, opacity: 0 }}
            transition={{ type: "spring", stiffness: 300, damping: 26 }}
            className="relative w-full max-w-lg overflow-hidden rounded-2xl border border-signal-violet/30 bg-ink-850 shadow-glow"
          >
            <div className="flex items-center gap-3 border-b border-white/10 bg-signal-violet/10 px-5 py-4">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-signal-violet/20 text-signal-violet">
                <AlertTriangle size={18} />
              </span>
              <div>
                <h2 id="approval-title" className="text-sm font-semibold text-slate-100">
                  Human approval required
                </h2>
                <p className="text-[11px] text-slate-400">
                  The Remediation agent has halted for a destructive action.
                </p>
              </div>
            </div>

            <div className="space-y-4 px-5 py-5">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Action">
                  <span className="font-mono text-signal-amber">{plan.action}</span>
                </Field>
                <Field label="Risk">
                  <RiskBadge risk={plan.risk} />
                </Field>
                <Field label="Target" full>
                  <span className="flex items-center gap-1.5 font-mono text-slate-100">
                    {plan.target}
                    <ArrowRight size={12} className="text-slate-500" />
                  </span>
                </Field>
                {plan.rollback_target && (
                  <Field label="Rollback target" full>
                    <span className="flex items-center gap-1.5 font-mono text-signal-green">
                      <RotateCcw size={12} /> {plan.rollback_target}
                    </span>
                  </Field>
                )}
              </div>

              {plan.rationale && (
                <div className="rounded-lg border border-white/[0.06] bg-ink-900/50 p-3">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                    Rationale
                  </div>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-slate-300">
                    {plan.rationale}
                  </p>
                </div>
              )}

              <div className="flex items-center gap-2 text-[11px] text-slate-500">
                <span className={`chip ${plan.reversible ? "border-signal-green/30 bg-signal-green/10 text-signal-green" : "border-signal-red/30 bg-signal-red/10 text-signal-red"}`}>
                  {plan.reversible ? "reversible" : "irreversible"}
                </span>
                <span>Approving executes as <span className="font-mono text-slate-300">on-call-engineer</span>.</span>
              </div>

              {error && (
                <div className="rounded-lg border border-signal-red/30 bg-signal-red/10 px-3 py-2 text-[11px] text-signal-red">
                  {error}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-3 border-t border-white/10 px-5 py-4">
              <span className="text-[10px] text-slate-600">
                <kbd className="rounded border border-white/15 bg-white/5 px-1">Enter</kbd> approve ·{" "}
                <kbd className="rounded border border-white/15 bg-white/5 px-1">Esc</kbd> reject
              </span>
              <div className="flex gap-2">
                <button
                  className="btn btn-ghost"
                  onClick={() => submit("reject")}
                  disabled={!!busy}
                >
                  {busy === "reject" ? <Spinner /> : <X size={15} />} Reject
                </button>
                <button
                  ref={approveRef}
                  className="btn bg-signal-green text-ink-950 hover:bg-signal-green/90"
                  onClick={() => submit("approve")}
                  disabled={!!busy}
                >
                  {busy === "approve" ? <Spinner /> : <Check size={15} />} Approve & execute
                </button>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Field({
  label,
  children,
  full,
}: {
  label: string;
  children: ReactNode;
  full?: boolean;
}) {
  return (
    <div className={`rounded-lg border border-white/[0.06] bg-ink-900/40 px-3 py-2 ${full ? "col-span-2" : ""}`}>
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-[13px]">{children}</div>
    </div>
  );
}
