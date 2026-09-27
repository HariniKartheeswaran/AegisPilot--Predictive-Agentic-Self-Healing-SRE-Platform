# Final demo — backup video + live day

## Commit / deploy (do once today)

1. Code is on **your fork** `feature/k8s` (Fire speed + AM dedup fixes).
2. Open PR → Harini `feature/k8s` → merge.
3. Jenkins **aegisops-ci** from that branch:
   - `DEPLOY_ENABLED` = **true**
   - `RUN_SONAR` = false (skip for speed)
   - Promote when prompted.
4. Smoke (30s):
   - http://13.207.225.219/api/health
   - http://13.207.225.219/api/pre-alerts → JSON (not 404)
   - http://13.207.225.219:30903/-/healthy
   - http://3.111.113.151:9090/alerts → 2 rules (often green/inactive)

## Tabs to open (video + demo day)

1. War Room — http://13.207.225.219/
2. Prometheus Alerts — http://3.111.113.151:9090/alerts
3. Alertmanager — http://13.207.225.219:30903/#/alerts
4. Grafana (optional) — http://3.111.113.151:3000
5. Slack `#…` where AegisOps posts (optional)

**Do not open Jenkins on camera.** It already deployed.

## Backup video script (~4–6 min)

Say: *“Live fault on checkout → Prometheus detects → Alertmanager routes → War Room agents remediate.”*

1. **War Room** — click **Fire Incident once**. Spinner should clear in ~seconds (not minutes). Agents start.
2. **Prometheus** — while errors are up, show **Pending/Firing** (red).  
   If still green, wait ~30–60s or keep talking over Triage.
3. **Alertmanager** — show the same alert group (`HighErrorRateWarning` / critical).
4. **War Room** — yellow **PRE-ALERT** if present; walk agents; when **Approve** appears → click **Approve**.
5. **Resolved** + RCA. Prom returns **Inactive/green** = healed.
6. **Slack** — show **RESOLVED** post (not REJECTED).

**Rules while recording**
- One Fire only. Do not spam.
- Approve within a few minutes (timeout → Slack REJECTED).
- Green Prom *before* Fire is healthy; green *after* Resolve is success.

## Live demo day (same flow)

Same tabs + same script. Worst case: play this backup video.

## Already wired (no redo unless broken)

- Alertmanager on K8s `:30903`
- EC2 Prom rules → AM
- War Room webhook + pre-alerts
