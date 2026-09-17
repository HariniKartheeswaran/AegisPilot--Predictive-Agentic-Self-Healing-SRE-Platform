import { useEffect, useReducer, useRef, useState } from "react";
import type { StreamEvent } from "./types";
import { initialState, reduce } from "./warroom";

// The backend (sse_starlette) tags every SSE message with a named event equal
// to StreamEvent.type, so the default `onmessage` never fires — we must attach a
// listener per type. This is the exhaustive set from CONTRACT.md.
const EVENT_TYPES = [
  "incident_created",
  "agent_start",
  "reasoning_start",
  "reasoning",
  "tool_call",
  "state_change",
  "agent_end",
  "triage_result",
  "vision_result",
  "correlation_result",
  "memory_result",
  "approval_required",
  "approved",
  "rejected",
  "exec_step",
  "resolved",
  "comms_result",
  "agent_error",
  "done",
] as const;

export type ConnState = "connecting" | "open" | "error";

export function useIncidentStream() {
  const [state, dispatch] = useReducer(reduce, undefined, initialState);
  const [conn, setConn] = useState<ConnState>("connecting");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource("/api/stream");
    esRef.current = es;

    es.onopen = () => setConn("open");
    es.onerror = () => setConn("error"); // EventSource auto-reconnects

    const handler = (e: MessageEvent) => {
      try {
        const parsed = JSON.parse(e.data) as StreamEvent;
        dispatch(parsed);
      } catch {
        /* ignore malformed frame */
      }
    };

    for (const t of EVENT_TYPES) es.addEventListener(t, handler as EventListener);
    es.addEventListener("message", handler as EventListener);

    return () => {
      for (const t of EVENT_TYPES)
        es.removeEventListener(t, handler as EventListener);
      es.close();
    };
  }, []);

  return { state, conn };
}
