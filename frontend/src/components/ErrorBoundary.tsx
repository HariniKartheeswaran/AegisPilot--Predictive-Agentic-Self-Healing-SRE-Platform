import { Component, type ErrorInfo, type ReactNode } from "react";
import { RefreshCw, ShieldAlert } from "lucide-react";

// Top-level safety net: if any render throws (e.g. an unexpected payload shape),
// the operator sees a calm recovery card instead of a blank white screen.
export default class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface for debugging without crashing the app.
    console.error("War Room render error:", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-5 p-6 text-center">
        <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-signal-amber/15 text-signal-amber ring-1 ring-signal-amber/30">
          <ShieldAlert size={26} />
        </span>
        <div className="space-y-1.5">
          <h1 className="text-lg font-semibold text-slate-100">The console hit a snag</h1>
          <p className="max-w-md text-[13px] leading-relaxed text-slate-400">
            Something in the view failed to render. Your incident data is safe on the
            backend — reloading reconnects to the live stream and rebuilds the board.
          </p>
        </div>
        <button onClick={() => window.location.reload()} className="btn btn-primary">
          <RefreshCw size={15} /> Reload console
        </button>
        {this.state.error.message && (
          <pre className="max-w-md overflow-x-auto rounded-lg border border-white/[0.06] bg-ink-900/60 px-3 py-2 text-left font-mono text-[10.5px] text-slate-500">
            {this.state.error.message}
          </pre>
        )}
      </div>
    );
  }
}
