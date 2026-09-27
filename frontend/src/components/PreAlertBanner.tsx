import { AlertTriangle } from "lucide-react";
import type { PreAlert } from "../types";

export default function PreAlertBanner({
  alerts,
  onAck,
}: {
  alerts: PreAlert[];
  onAck: (id: string) => void;
}) {
  if (!alerts.length) return null;

  return (
    <div className="border-b border-amber-500/30 bg-amber-500/10 px-5 py-2.5">
      <div className="mx-auto flex max-w-[1600px] flex-col gap-2">
        {alerts.map((a) => (
          <div
            key={a.id}
            className="flex flex-wrap items-center gap-3 text-sm text-amber-100"
          >
            <span className="flex items-center gap-1.5 font-semibold uppercase tracking-wide text-amber-300">
              <AlertTriangle size={14} /> Pre-alert
            </span>
            <span className="font-mono text-amber-50">{a.service}</span>
            <span className="text-amber-100/80">{a.summary}</span>
            <span className="rounded bg-amber-500/20 px-1.5 py-0.5 font-mono text-xs text-amber-200">
              {a.error_rate}
            </span>
            <button
              type="button"
              onClick={() => onAck(a.id)}
              className="ml-auto rounded-md border border-amber-400/40 bg-amber-500/20 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-amber-100 hover:bg-amber-500/30"
            >
              Acknowledge
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
