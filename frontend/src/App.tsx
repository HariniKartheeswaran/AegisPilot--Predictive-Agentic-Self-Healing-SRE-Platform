import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type Health } from "./api";
import AgentGraph from "./components/AgentGraph";
import ApprovalModal from "./components/ApprovalModal";
import CustomIncidentModal from "./components/CustomIncidentModal";
import Header from "./components/Header";
import IdleHero from "./components/IdleHero";
import ReasoningStream from "./components/ReasoningStream";
import RightPanel from "./components/RightPanel";
import RegistryDrawer from "./components/RegistryDrawer";
import { useIncidentStream } from "./useIncidentStream";

export default function App() {
  const { state, conn } = useIncidentStream();
  const [health, setHealth] = useState<Health | null>(null);
  const [registryOpen, setRegistryOpen] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [firing, setFiring] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  // The modal is dismissed locally the instant the operator acts, so a late
  // `approved`/`rejected` replay can't pop it back open.
  const [modalDismissed, setModalDismissed] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  const flash = (msg: string) => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4000);
  };

  const fireAlert = async () => {
    setFiring(true);
    try {
      await api.fireDemoAlert();
      flash("Alert published to the event bus — orchestrator engaging.");
    } catch (e) {
      flash(e instanceof Error ? e.message : "Failed to publish alert");
    } finally {
      setFiring(false);
    }
  };

  const modalOpen =
    !!state.incidentId &&
    !!state.plan &&
    state.status === "AWAITING_APPROVAL" &&
    !state.decision &&
    modalDismissed !== state.incidentId;

  const hasIncident = !!state.incidentId;

  return (
    <div className="flex min-h-screen flex-col">
      <Header
        state={state}
        conn={conn}
        health={health}
        firing={firing}
        onFire={fireAlert}
        onOpenCustom={() => setCustomOpen(true)}
        onOpenRegistry={() => setRegistryOpen(true)}
      />

      {!hasIncident ? (
        // IDLE — a single clear call-to-action instead of a wall of empty panels.
        <main className="flex flex-1 items-center justify-center px-4 py-10">
          <IdleHero firing={firing} onFire={fireAlert} />
        </main>
      ) : (
        // ACTIVE — the animated agent pipeline is the hero; details sit in a clean
        // two-column layout (live reasoning | tabbed Diagnosis/RCA panel).
        <main className="mx-auto w-full max-w-[1600px] flex-1 space-y-4 px-4 py-5 lg:px-6">
          <AgentGraph state={state} />

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="h-[520px] lg:h-[600px]">
              <ReasoningStream state={state} />
            </div>
            <div className="h-[520px] lg:h-[600px]">
              <RightPanel state={state} />
            </div>
          </div>

          <footer className="pb-6 pt-1 text-center text-[10.5px] text-slate-600">
            AegisPilot · autonomous SRE on-call · six ADK sub-agents on{" "}
            <span className="font-mono">{health?.model ?? "gemini-3.5-flash"}</span> ·
            human-gated remediation
          </footer>
        </main>
      )}

      {state.incidentId && state.plan && (
        <ApprovalModal
          incidentId={state.incidentId}
          plan={state.plan}
          open={modalOpen}
          onResolved={(d) => {
            setModalDismissed(state.incidentId);
            flash(d === "approved" ? "Approved — executor running." : "Remediation rejected.");
          }}
        />
      )}

      <CustomIncidentModal
        open={customOpen}
        onClose={() => setCustomOpen(false)}
        onFired={(msg) => flash(msg)}
      />

      <RegistryDrawer open={registryOpen} onClose={() => setRegistryOpen(false)} />

      {/* Toast */}
      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.96 }}
            className="fixed bottom-5 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-2 rounded-lg border border-white/10 bg-ink-800 px-4 py-2.5 text-[12.5px] text-slate-200 shadow-panel"
          >
            <AlertTriangle size={14} className="text-signal-amber" />
            {toast}
            <button onClick={() => setToast(null)} className="ml-1 text-slate-500 hover:text-slate-300">
              <X size={13} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
