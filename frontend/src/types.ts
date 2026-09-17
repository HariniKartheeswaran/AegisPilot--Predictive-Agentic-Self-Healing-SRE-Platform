// Types mirror backend/models.py and CONTRACT.md exactly. Every SSE event is a
// StreamEvent whose real fields live under `payload`; we key handlers off `type`.

export type IncidentStatus =
  | "DETECTED"
  | "TRIAGED"
  | "DIAGNOSED"
  | "CORRELATED"
  | "AWAITING_APPROVAL"
  | "REMEDIATING"
  | "RESOLVED"
  | "REJECTED"
  | "FAILED";

export type Severity = "SEV1" | "SEV2" | "SEV3" | "SEV4";

export interface Alert {
  alert: string;
  service: string;
  error_rate?: string | null;
  grafana_snapshot?: string | null;
  metadata?: Record<string, unknown>;
}

export interface RemediationPlan {
  action: string;
  target: string;
  risk: string;
  reversible: boolean;
  rollback_target?: string | null;
  rationale?: string;
  requires_approval?: boolean;
}

export interface StreamEvent {
  type: string;
  incident_id: string;
  agent?: string | null;
  payload: Record<string, any>;
  ts: number;
}

export interface AuditStep {
  id: string;
  incident_id: string;
  agent: string;
  step: string;
  input: string;
  reasoning: string;
  tool_call: string;
  output: string;
  tokens: number;
  latency_ms: number;
  ts: number;
}

export interface RegistryEntry {
  id: string;
  name: string;
  version: string;
  model: string;
  allowed_tools: string[];
  scope: string;
  status: string;
}

export interface Incident {
  id: string;
  status: IncidentStatus;
  severity?: Severity | null;
  service: string;
  blast_radius?: string | null;
  detected_at: number;
  probable_cause?: string | null;
  confidence?: number | null;
  remediation_plan?: RemediationPlan | null;
  approved_by?: string | null;
  resolved_at?: number | null;
  rca_doc?: string | null;
  fingerprint?: string | null;
  findings: Record<string, any>;
  alert?: Alert | null;
}

// ---- Derived war-room state (reduced from the event stream) ----------------

export type AgentPhase = "idle" | "active" | "done" | "error";

export const AGENT_ORDER = [
  "Orchestrator",
  "Triage",
  "Diagnosis",
  "Correlation",
  "Memory",
  "Remediation",
  "Comms",
] as const;

export type AgentName = (typeof AGENT_ORDER)[number];

export interface AgentNodeState {
  name: AgentName;
  phase: AgentPhase;
  headline: string;
  tools: string[];
  error?: string;
}

// A single line in the live reasoning/tool stream.
export interface StreamLine {
  id: string;
  kind: "reasoning" | "tool" | "reasoning_start" | "system";
  agent: string;
  step?: string;
  text: string;
  tool?: string;
  detail?: string;
  output?: string;
  tokens?: number;
  latency_ms?: number;
  attempts?: number;
  model?: string;
  ts: number;
}

export interface VisionResult {
  image_url: string;
  confirmed: boolean | null;
  observation: string;
  annotation: string;
}

export interface WarRoomState {
  incidentId: string | null;
  service: string;
  alert: Alert | null;
  status: IncidentStatus;
  severity: Severity | null;
  blastRadius: string | null;
  oncall: string | null;
  detectedAt: number | null;
  resolvedAt: number | null;
  resolutionMinutes: number | null;
  probableCause: string | null;
  confidence: number | null;
  memory: {
    similarity: number | null;
    times_seen: number | null;
    avg_resolution_minutes: number | null;
  } | null;
  vision: VisionResult | null;
  plan: RemediationPlan | null;
  approver: string | null;
  decision: "approved" | "rejected" | null;
  comms: {
    ticket_id?: string;
    slack_channel?: string;
    resolution_minutes?: number;
    rca_present?: boolean;
  } | null;
  agents: Record<AgentName, AgentNodeState>;
  activeAgent: AgentName | null;
  lastHandoff: { from: AgentName; to: AgentName; at: number } | null;
  lines: StreamLine[];
  done: boolean;
  error: string | null;
}
