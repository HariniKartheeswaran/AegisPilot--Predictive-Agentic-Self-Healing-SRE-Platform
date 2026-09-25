# Docker (Compose + ECR) — AegisPilot

Same container images as Kubernetes. Compose is the local/dev path; ECR + K3s is
the cluster path. Nothing here is a separate “demo-only” stack.

## Images (shared with K8s)

| Image | Dockerfile | ECR (ap-south-1) |
|-------|------------|------------------|
| War Room (API + UI) | `docker/Dockerfile` | `850887971586.dkr.ecr.ap-south-1.amazonaws.com/aegispilot/warroom:<tag>` |
| Scrape tier | `docker/scrape/Dockerfile` | `850887971586.dkr.ecr.ap-south-1.amazonaws.com/aegispilot/scrape:<tag>` |

K8s Deployments (`k8s/warroom/*`, `k8s/scrape/*`) already pull those ECR tags.
Build once, run in Compose **or** push and roll K8s.

```bash
# Local compose tags
docker compose build

# ECR (same Dockerfiles)
./scripts/docker_ecr_push.sh
TAG=v0.2.0 ./scripts/docker_ecr_push.sh
```

## Compose stack

Services:

- **warroom** — FastAPI + React (`:8080`)
- **checkout-svc / cart-svc / payments-svc** — scrape apps (`:8081–8083` on host)
- **prometheus** — scrapes the three services (`:9090`)

```bash
cp .env.example .env   # optional Gemini / Slack / remote Grafana
docker compose up --build
# War Room → http://localhost:8080
# Prometheus → http://localhost:9091
```

Compose sets:

- `REMEDIATION_MODE=docker`
- `PROMETHEUS_URL=http://prometheus:9090`
- `SCRAPE_URL_*` to the Compose DNS names

That enables **live Fire** (real ERROR_RATE spike + load + Prom metrics), not
the HikariCP seed scenario path.

### Live path (Compose)

1. Open War Room → **Fire**
2. War Room `POST`s `/admin/fault` on `checkout-svc` (ERROR_RATE≈0.42, new version)
3. Generates `/api/work` traffic; Prometheus records 5xx
4. Agents diagnose from live metrics/logs (`/admin/logs` or Loki if configured)
5. **Approve** remediates via `/admin/fault` (clears ERROR_RATE, restores version)

Seed HikariCP only runs when live mode is off (`REMEDIATION_MODE=simulate` and
no Prom URL).

### Optional: remote Grafana / Loki (same EC2 as K8s)

In `.env`:

```env
GRAFANA_URL=http://3.111.113.151:3000
GRAFANA_TOKEN=...
LOKI_URL=http://3.111.113.151:3100
```

Compose passes these into warroom. Snapshots and Loki log fetch then match K8s.

## Scrape control API

Every scrape container exposes:

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/admin/fault` | Read/set `error_rate`, `service_version`, `latency_ms`, `fail_ready` |
| GET | `/admin/logs` | Recent request log lines (Docker diagnosis) |
| GET | `/metrics` | Prometheus |
| GET | `/api/work` | Workload (5xx under ERROR_RATE) |

K8s live Fire still patches Deployment env; Compose uses `/admin/fault`. Same
metrics series either way.

## Modes

| `REMEDIATION_MODE` | Where | Fire / remediate |
|--------------------|-------|------------------|
| `simulate` | unit tests / offline | seed scenarios + dry-run steps |
| `docker` | Compose | `/admin/fault` + local Prom |
| `kubernetes` | K3s | Deployment patch-env + cluster Prom |

## Env

See `.env.example`. Compose loads `.env` when present. Minimum for agents:

```env
GEMINI_API_KEY=...
# or Vertex:
# GOOGLE_GENAI_USE_VERTEXAI=true
# GOOGLE_CLOUD_PROJECT=...
```

Slack (optional): `SLACK_WEBHOOK_URL=...`

## Smoke checks

```bash
curl -sf http://localhost:8081/health
curl -sf http://localhost:9091/-/ready
curl -sf -X POST http://localhost:8080/api/demo/fire
# expect "scenario":"live","live":true
```

## Relationship to K8s

1. Develop/fix with `docker compose up`
2. Push with `./scripts/docker_ecr_push.sh`
3. Cluster pulls the same tags (`imagePullPolicy: Always`); ECR pull secret + CronJob refresh already in `k8s/warroom/`

Do not maintain a second scrape implementation for Compose — one `docker/scrape` image for both.
