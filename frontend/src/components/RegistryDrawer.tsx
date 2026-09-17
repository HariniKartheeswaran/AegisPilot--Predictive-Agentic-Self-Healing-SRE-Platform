import { AnimatePresence, motion } from "framer-motion";
import { BadgeCheck, Boxes, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { RegistryEntry } from "../types";
import { Empty, Spinner } from "./ui";

export default function RegistryDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [entries, setEntries] = useState<RegistryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    api
      .registry()
      .then(setEntries)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l border-white/10 bg-ink-850 shadow-panel"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}
            role="dialog"
            aria-label="Agent registry"
          >
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
              <div className="flex items-center gap-2">
                <Boxes size={17} className="text-signal-blue" />
                <h2 className="text-sm font-semibold text-slate-100">Agent Registry</h2>
              </div>
              <button onClick={onClose} className="btn-ghost rounded-lg p-1.5" aria-label="Close">
                <X size={16} />
              </button>
            </div>

            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              {error ? (
                <Empty>
                  <span className="text-signal-red">{error}</span>
                </Empty>
              ) : !entries ? (
                <div className="flex h-40 items-center justify-center">
                  <Spinner />
                </div>
              ) : entries.length === 0 ? (
                <Empty>No agents registered.</Empty>
              ) : (
                entries.map((a) => (
                  <div
                    key={a.id}
                    className="rounded-xl border border-white/[0.06] bg-ink-900/40 p-4"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-[13px] font-semibold text-slate-100">{a.name}</span>
                          <span className="chip border-white/10 bg-white/5 text-slate-400">
                            v{a.version}
                          </span>
                        </div>
                        <div className="mt-0.5 font-mono text-[11px] text-signal-blue">{a.model}</div>
                      </div>
                      <span
                        className={`chip ${
                          a.status === "healthy"
                            ? "border-signal-green/30 bg-signal-green/10 text-signal-green"
                            : "border-signal-amber/30 bg-signal-amber/10 text-signal-amber"
                        }`}
                      >
                        <BadgeCheck size={12} /> {a.status}
                      </span>
                    </div>

                    <p className="mt-2 text-[11.5px] leading-relaxed text-slate-400">{a.scope}</p>

                    <div className="mt-3">
                      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                        Allowed tools
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {a.allowed_tools.map((t) => (
                          <span
                            key={t}
                            className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-0.5 font-mono text-[10.5px] text-slate-300"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
