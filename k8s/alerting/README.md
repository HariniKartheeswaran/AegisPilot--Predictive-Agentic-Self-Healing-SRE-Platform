# K8s production alerting (Prometheus + Grafana + Alertmanager + Pub/Sub)

Additive only — does **not** change existing warroom Deployments, scrape apps, or ConfigMap values.

## Final path (what runs on demo / K8s day)

```
scrape pods (metrics NodePorts)
    → EC2 Prometheus  (:9090)  ← rules: HighErrorRateWarning + HighErrorRate
    → Alertmanager in K8s      (:30903 NodePort)
    → War Room POST /api/alertmanager/webhook
         ├─ warning / stage=pre  → mandatory Pre-alert (UI + Slack)
         └─ critical / stage=full → bus.publish
              → Google Pub/Sub topic `incident-alerts`   (BACKEND=cloud)
              → War Room pull subscriber → agents (Vertex Gemini)
```

Grafana (`:3000`) stays the **dashboard / vision** source (unchanged ConfigMap `GRAFANA_URL`).  
It does **not** replace Alertmanager — Prom rules fire AM; Grafana shows the same metrics.

Pub/Sub is already enabled on K8s (`BACKEND: cloud`, `PUBSUB_MODE: pull` in `aegis-warroom-config`).  
Critical AM alerts use the same `bus.publish` path as `/api/alerts`, so they land on Pub/Sub for real.

## 1) Apply Alertmanager (cluster) — safe add

```bash
kubectl apply -f k8s/alerting/alertmanager.yaml
kubectl -n aegispilot rollout status deploy/aegis-alertmanager
kubectl -n aegispilot get svc aegis-alertmanager
```

AM UI: `http://<K3s-NODE-IP>:30903`  
Webhook target (in-cluster): `http://aegis-warroom.aegispilot.svc.cluster.local/api/alertmanager/webhook`

## 2) EC2 Prometheus — rules + point at AM

On the Prom host (`PROMETHEUS_URL`, e.g. `3.111.113.151`):

1. Copy `k8s/alerting/rules.yml` → `/etc/prometheus/aegis_rules.yml`
2. Merge `k8s/alerting/ec2-prometheus-snippet.yml` into `prometheus.yml`  
   - set `NODE_IP` to a K3s node that already reaches NodePorts `30081–30083`
3. Reload: `curl -X POST http://127.0.0.1:9090/-/reload`
4. Check: `http://<prom-host>:9090/alerts` → both rules listed

## 3) Grafana (existing board — no break)

- Keep using dashboard `GRAFANA_DASHBOARD_PATH` (`/d/ad6nckx/aegispilot-dashboard`)
- Optional: Grafana → **Alerting → Alert rules** can *view* the same Prom datasource; routing stays on **Alertmanager** (single webhook to War Room)
- War Room vision still pulls live Prom / Grafana render via existing env

## 4) War Room image

Deploy an image that includes:
- `POST /api/alertmanager/webhook`
- `GET/POST /api/pre-alerts…`
- Pre-alert banner UI

(Jenkins build + `DEPLOY_ENABLED` / your usual ECR roll.)  
ConfigMap / secrets / Vertex / Pub/Sub settings stay as they are.

## 5) Verify (one by one)

1. `kubectl -n aegispilot get pods -l app=aegis-alertmanager` → Running  
2. Spike checkout (Fire or ERROR_RATE)  
3. Prom `/alerts` → Warning then Critical firing  
4. AM `:30903` → alert groups  
5. War Room → amber **Pre-alert** (ack) and/or full incident  
6. GCP Console → Pub/Sub `incident-alerts` shows publishes on critical  

## Files in this folder

| File | Role |
|------|------|
| `alertmanager.yaml` | In-cluster AM Deployment + ConfigMap + NodePort **30903** |
| `alertmanager.yml` | Same AM config (readable copy) |
| `rules.yml` | Prom rules (pre + full) for EC2 Prom |
| `ec2-prometheus-snippet.yml` | What to merge into EC2 `prometheus.yml` |
| `README.md` | This guide |
