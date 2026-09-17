import { FileText, ScanEye } from "lucide-react";
import { useEffect, useState } from "react";
import type { WarRoomState } from "../types";
import DiagnosisPanel from "./DiagnosisPanel";
import RcaTimeline from "./RcaTimeline";

type Tab = "diagnosis" | "rca";

const TABS: { id: Tab; label: string; icon: typeof ScanEye }[] = [
  { id: "diagnosis", label: "Diagnosis", icon: ScanEye },
  { id: "rca", label: "RCA", icon: FileText },
];

// Tabbed detail panel: only one dense view shows at a time. Auto-advances to the
// RCA once the incident closes, so the postmortem is front-and-center at the end.
export default function RightPanel({ state }: { state: WarRoomState }) {
  const [tab, setTab] = useState<Tab>("diagnosis");

  useEffect(() => {
    if (state.done) setTab("rca");
  }, [state.done]);

  return (
    <section className="panel flex min-h-0 flex-col">
      <div className="flex items-center gap-1 border-b border-white/[0.06] px-2 py-2">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-[12.5px] font-semibold transition-colors ${
              tab === id
                ? "bg-white/[0.07] text-slate-100"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            <Icon size={14} className={tab === id ? "text-signal-blue" : ""} />
            {label}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        {tab === "diagnosis" ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <DiagnosisPanel state={state} />
          </div>
        ) : (
          <RcaTimeline state={state} />
        )}
      </div>
    </section>
  );
}
