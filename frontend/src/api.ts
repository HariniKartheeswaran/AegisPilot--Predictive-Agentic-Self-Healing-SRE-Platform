import type { AuditStep, Incident, RegistryEntry } from "./types";

// All calls hit relative /api — Vite proxies to :8080 in dev, same-origin in the
// single-container Cloud Run build. Small wrapper so callers get typed JSON or a
// thrown Error they can surface in the UI.
async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export interface Health {
  status: string;
  model: string;
  gemini_key_present: boolean;
  slack_configured: boolean;
}

export const api = {
  health: () => fetch("/api/health").then(j<Health>),

  registry: () => fetch("/api/registry").then(j<RegistryEntry[]>),

  incidents: () => fetch("/api/incidents").then(j<Incident[]>),

  incident: (id: string) => fetch(`/api/incidents/${id}`).then(j<Incident>),

  audit: (id: string) => fetch(`/api/incidents/${id}/audit`).then(j<AuditStep[]>),

  rca: (id: string) =>
    fetch(`/api/incidents/${id}/rca`).then(
      j<{ rca: string; findings: Record<string, any> }>
    ),

  grafanaUrl: (id: string) => `/api/incidents/${id}/grafana`,

  approve: (id: string, approver = "on-call-engineer", note = "") =>
    fetch(`/api/incidents/${id}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approver, note }),
    }).then(j<{ resolved: boolean; approved: boolean }>),

  reject: (id: string, approver = "on-call-engineer", note = "") =>
    fetch(`/api/incidents/${id}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approver, note }),
    }).then(j<{ resolved: boolean; approved: boolean }>),

  // Fire the NEXT rotating scenario (checkout → cart → payments). We send an
  // explicit empty body so Cloud Run's front-end always gets a Content-Length
  // (a bodyless POST can be rejected with 411).
  fireDemoAlert: () =>
    fetch("/api/demo/fire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).then(j<{ accepted: boolean; scenario: string; service: string; alert: string }>),

  // Bring-your-own-incident: judges submit their own data (multipart so they can
  // optionally attach a dashboard screenshot for the vision agent to read).
  fireCustom: (form: FormData) =>
    fetch("/api/incidents/custom", { method: "POST", body: form }).then(
      j<{ accepted: boolean; service: string; logs_ingested: number; vision_image: boolean }>
    ),
};
