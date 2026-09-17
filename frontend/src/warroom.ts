import {
  AGENT_ORDER,
  type AgentName,
  type AgentNodeState,
  type StreamEvent,
  type StreamLine,
  type WarRoomState,
} from "./types";

// ---------------------------------------------------------------------------
// Pure reducer: rebuilds the entire war-room view from the SSE event stream.
// The backend replays its ring buffer on connect, so feeding events in order
// (live or replayed) always reconstructs a consistent state.
// ---------------------------------------------------------------------------

function freshAgents(): Record<AgentName, AgentNodeState> {
  return AGENT_ORDER.reduce((acc, name) => {
    acc[name] = { name, phase: "idle", headline: "", tools: [] };
    return acc;
  }, {} as Record<AgentName, AgentNodeState>);
}

export function initialState(): WarRoomState {
  return {
    incidentId: null,
    service: "",
    alert: null,
    status: "DETECTED",
    severity: null,
    blastRadius: null,
    oncall: null,
    detectedAt: null,
    resolvedAt: null,
    resolutionMinutes: null,
    probableCause: null,
    confidence: null,
    memory: null,
    vision: null,
    plan: null,
    approver: null,
    decision: null,
    comms: null,
    agents: freshAgents(),
    activeAgent: null,
    lastHandoff: null,
    lines: [],
    done: false,
    error: null,
  };
}

const isAgent = (n: unknown): n is AgentName =>
  typeof n === "string" && (AGENT_ORDER as readonly string[]).includes(n);

let _seq = 0;
const nextId = () => `l${Date.now()}_${_seq++}`;

function pushLine(state: WarRoomState, line: StreamLine): StreamLine[] {
  // Keep the stream bounded so a long incident doesn't grow unboundedly.
  const lines = [...state.lines, line];
  return lines.length > 400 ? lines.slice(lines.length - 400) : lines;
}

function setAgent(
  state: WarRoomState,
  name: AgentName,
  patch: Partial<AgentNodeState>
): Record<AgentName, AgentNodeState> {
  return { ...state.agents, [name]: { ...state.agents[name], ...patch } };
}

export function reduce(state: WarRoomState, ev: StreamEvent): WarRoomState {
  const p = ev.payload || {};
  const agent = ev.agent || (p.agent as string | undefined);

  switch (ev.type) {
    case "incident_created": {
      // A genuinely new incident (newest wins) resets the board; replaying the
      // same one just rebuilds identical id-scoped state.
      const base = initialState();
      return {
        ...base,
        incidentId: ev.incident_id,
        service: p.service ?? "",
        alert: p.alert ?? null,
        status: p.status ?? "DETECTED",
        detectedAt: ev.ts,
        agents: {
          ...base.agents,
          Orchestrator: {
            name: "Orchestrator",
            phase: "active",
            headline: "Owning incident lifecycle",
            tools: ["sub-agent handoff", "state writer"],
          },
        },
        activeAgent: "Orchestrator",
        lines: [
          {
            id: nextId(),
            kind: "system",
            agent: "Orchestrator",
            text: `Incident opened for ${p.service ?? "service"} — ${p.alert?.alert ?? "alert"}`,
            ts: ev.ts,
          },
        ],
      };
    }
  }

  // Everything else only applies to the active incident.
  if (ev.incident_id !== state.incidentId) return state;

  switch (ev.type) {
    case "agent_start": {
      if (!isAgent(agent)) return state;
      const prev = state.activeAgent;
      const handoff =
        prev && prev !== agent
          ? { from: prev, to: agent, at: ev.ts }
          : state.lastHandoff;
      let agents = setAgent(state, agent, {
        phase: "active",
        headline: p.headline ?? "",
        tools: Array.isArray(p.tools) ? p.tools : [],
      });
      // The previous worker is done once the next lights up (Orchestrator stays
      // active as the coordinator until the run finishes).
      if (prev && prev !== agent && prev !== "Orchestrator" && agents[prev].phase === "active") {
        agents = { ...agents, [prev]: { ...agents[prev], phase: "done" } };
      }
      return {
        ...state,
        agents,
        activeAgent: agent,
        lastHandoff: handoff,
        lines: pushLine(state, {
          id: nextId(),
          kind: "system",
          agent,
          text: p.headline ?? `${agent} engaged`,
          ts: ev.ts,
        }),
      };
    }

    case "reasoning_start": {
      return {
        ...state,
        lines: pushLine(state, {
          id: nextId(),
          kind: "reasoning_start",
          agent: agent ?? "?",
          step: p.step,
          text: "",
          ts: ev.ts,
        }),
      };
    }

    case "reasoning": {
      return {
        ...state,
        lines: pushLine(state, {
          id: nextId(),
          kind: "reasoning",
          agent: agent ?? "?",
          step: p.step,
          text: p.text ?? "",
          tokens: p.tokens,
          latency_ms: p.latency_ms,
          attempts: p.attempts,
          model: p.model,
          ts: ev.ts,
        }),
      };
    }

    case "tool_call": {
      return {
        ...state,
        lines: pushLine(state, {
          id: nextId(),
          kind: "tool",
          agent: agent ?? "?",
          tool: p.tool,
          detail: p.detail,
          output: p.output,
          text: p.detail ?? p.tool ?? "",
          ts: ev.ts,
        }),
      };
    }

    case "exec_step": {
      return {
        ...state,
        lines: pushLine(state, {
          id: nextId(),
          kind: "tool",
          agent: agent ?? "Remediation",
          tool: p.tool ?? "executor",
          detail: p.step ?? p.detail,
          output:
            typeof p.output === "string" ? p.output : JSON.stringify(p.output ?? p),
          text: p.step ?? p.detail ?? "execution step",
          ts: ev.ts,
        }),
      };
    }

    case "state_change": {
      const to = p.to ?? state.status;
      return { ...state, status: to };
    }

    case "agent_end": {
      if (!isAgent(agent)) return state;
      const cur = state.agents[agent];
      const agents = setAgent(state, agent, {
        phase: cur.phase === "error" ? "error" : "done",
      });
      return {
        ...state,
        agents,
        activeAgent: state.activeAgent === agent ? "Orchestrator" : state.activeAgent,
      };
    }

    case "triage_result": {
      return {
        ...state,
        severity: p.severity ?? state.severity,
        blastRadius: p.blast_radius ?? state.blastRadius,
        oncall: p.oncall ?? state.oncall,
      };
    }

    case "vision_result": {
      return {
        ...state,
        vision: {
          image_url: p.image_url ?? "",
          confirmed: p.confirmed ?? null,
          observation: p.observation ?? "",
          annotation: p.annotation ?? "",
        },
      };
    }

    case "correlation_result": {
      return {
        ...state,
        probableCause: p.probable_cause ?? state.probableCause,
        confidence: typeof p.confidence === "number" ? p.confidence : state.confidence,
      };
    }

    case "memory_result": {
      return {
        ...state,
        memory: {
          similarity: p.similarity ?? null,
          times_seen: p.times_seen ?? null,
          avg_resolution_minutes: p.avg_resolution_minutes ?? null,
        },
      };
    }

    case "approval_required": {
      return {
        ...state,
        plan: p.plan ?? null,
        status: "AWAITING_APPROVAL",
        decision: null,
      };
    }

    case "approved": {
      return { ...state, decision: "approved", approver: p.approver ?? null };
    }

    case "rejected": {
      return {
        ...state,
        decision: "rejected",
        approver: p.approver ?? null,
        status: "REJECTED",
      };
    }

    case "resolved": {
      return {
        ...state,
        status: "RESOLVED",
        resolvedAt: ev.ts,
        resolutionMinutes:
          typeof p.resolution_minutes === "number"
            ? p.resolution_minutes
            : state.resolutionMinutes,
      };
    }

    case "comms_result": {
      return {
        ...state,
        comms: {
          ticket_id: p.ticket_id,
          slack_channel: p.slack_channel,
          resolution_minutes: p.resolution_minutes,
          rca_present: p.rca_present,
        },
        resolutionMinutes:
          typeof p.resolution_minutes === "number"
            ? p.resolution_minutes
            : state.resolutionMinutes,
      };
    }

    case "agent_error": {
      const agents = isAgent(agent)
        ? setAgent(state, agent, { phase: "error", error: p.error })
        : state.agents;
      return {
        ...state,
        agents,
        error: p.error ?? "agent error",
        lines: pushLine(state, {
          id: nextId(),
          kind: "system",
          agent: agent ?? "orchestrator",
          text: `ERROR — ${p.error ?? "unknown"}`,
          ts: ev.ts,
        }),
      };
    }

    case "done": {
      return {
        ...state,
        done: true,
        status: p.status ?? state.status,
        activeAgent: null,
        agents: {
          ...state.agents,
          Orchestrator: { ...state.agents.Orchestrator, phase: "done" },
        },
      };
    }

    default:
      return state;
  }
}
