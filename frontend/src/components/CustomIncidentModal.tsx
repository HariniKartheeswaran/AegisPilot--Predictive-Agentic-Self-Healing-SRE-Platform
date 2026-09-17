import { AnimatePresence, motion } from "framer-motion";
import { FlaskConical, ImagePlus, Loader2, X, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";

// "Bring your own incident" — judges type their OWN data and the real agents
// process exactly it (logs classified, deploy correlated, screenshot read by
// Gemini vision). Nothing is randomized; the pipeline runs on their input.
export default function CustomIncidentModal({
  open,
  onClose,
  onFired,
}: {
  open: boolean;
  onClose: () => void;
  onFired: (msg: string) => void;
}) {
  const [service, setService] = useState("auth-svc");
  const [alert, setAlert] = useState("HighErrorRate");
  const [errorRate, setErrorRate] = useState("23%");
  const [logs, setLogs] = useState(
    "auth-svc v3.2.0 rollout complete, 5/5 pods ready\n" +
      "ERROR JWT validation failed: token signature mismatch after key rotation\n" +
      "ERROR 500 on POST /api/login (trace_id=a91f..)\n" +
      "WARN auth error rate 23% over last 60s exceeds SLO 2%"
  );
  const [deployVersion, setDeployVersion] = useState("v3.2.0");
  const [rollbackTarget, setRollbackTarget] = useState("v3.1.9");
  const [image, setImage] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  const submit = async () => {
    if (!service.trim()) return setErr("Service name is required.");
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.set("service", service.trim());
      fd.set("alert", alert.trim() || "HighErrorRate");
      fd.set("error_rate", errorRate.trim() || "10%");
      fd.set("logs", logs);
      if (deployVersion.trim()) fd.set("deploy_version", deployVersion.trim());
      if (rollbackTarget.trim()) fd.set("rollback_target", rollbackTarget.trim());
      if (image) fd.set("image", image);
      const res = await api.fireCustom(fd);
      onFired(
        `Custom incident on ${res.service} — ${res.logs_ingested} log lines ingested` +
          (res.vision_image ? " + dashboard image for vision" : "") + "."
      );
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to submit");
    } finally {
      setBusy(false);
    }
  };

  const field = "w-full rounded-lg border border-white/10 bg-ink-900/70 px-3 py-2 text-[13px] text-slate-100 outline-none focus:border-signal-blue/60";
  const label = "mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-slate-500";

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
          onClick={() => !busy && onClose()}
        >
          <motion.div
            initial={{ scale: 0.96, y: 12, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.97, y: 8, opacity: 0 }}
            onClick={(e) => e.stopPropagation()}
            className="panel max-h-[92vh] w-full max-w-2xl overflow-y-auto"
          >
            <div className="flex items-center justify-between border-b border-white/[0.07] px-5 py-3.5">
              <div className="flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-signal-violet/15 text-signal-violet ring-1 ring-signal-violet/30">
                  <FlaskConical size={16} />
                </span>
                <div>
                  <h2 className="text-[14px] font-bold text-slate-50">Custom incident</h2>
                  <p className="text-[11px] text-slate-500">
                    Your data — the real agents diagnose exactly what you enter.
                  </p>
                </div>
              </div>
              <button onClick={onClose} disabled={busy} className="text-slate-500 hover:text-slate-300">
                <X size={17} />
              </button>
            </div>

            <div className="space-y-3.5 px-5 py-4">
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className={label}>Service</label>
                  <input className={field} value={service} onChange={(e) => setService(e.target.value)} />
                </div>
                <div>
                  <label className={label}>Alert</label>
                  <input className={field} value={alert} onChange={(e) => setAlert(e.target.value)} />
                </div>
                <div>
                  <label className={label}>Error rate / metric</label>
                  <input className={field} value={errorRate} onChange={(e) => setErrorRate(e.target.value)} />
                </div>
              </div>

              <div>
                <label className={label}>Log lines (one per line — the Diagnosis agent classifies these)</label>
                <textarea
                  className={`${field} h-36 resize-none font-mono text-[12px] leading-relaxed`}
                  value={logs}
                  onChange={(e) => setLogs(e.target.value)}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={label}>Recent deploy (optional → Correlation)</label>
                  <input className={field} value={deployVersion} onChange={(e) => setDeployVersion(e.target.value)} placeholder="v3.2.0" />
                </div>
                <div>
                  <label className={label}>Rollback target (optional)</label>
                  <input className={field} value={rollbackTarget} onChange={(e) => setRollbackTarget(e.target.value)} placeholder="v3.1.9" />
                </div>
              </div>

              <div>
                <label className={label}>Dashboard screenshot (optional → Gemini vision)</label>
                <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-white/15 bg-ink-900/50 px-3 py-2.5 text-[12.5px] text-slate-400 hover:border-signal-violet/50">
                  <ImagePlus size={15} className="text-signal-violet" />
                  {image ? image.name : "Attach any Grafana/monitoring screenshot — the agent will read it"}
                  <input type="file" accept="image/*" className="hidden" onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
                </label>
              </div>

              {err && (
                <div className="rounded-lg border border-signal-red/30 bg-signal-red/10 px-3 py-2 text-[12px] text-signal-red">
                  {err}
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-white/[0.07] px-5 py-3.5">
              <button onClick={onClose} disabled={busy} className="btn btn-ghost">Cancel</button>
              <button onClick={submit} disabled={busy} className="btn btn-primary px-5">
                {busy ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}
                Run incident
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
