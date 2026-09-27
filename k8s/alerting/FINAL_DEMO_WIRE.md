# Final demo — real inject path (not fake webhook)

## Why Prom stayed green

Prometheus had `evaluation_interval: 1m`. Rules only checked once a minute, so
Fire healed before alerts could go Pending→Firing. That looked “always green.”

## Fix (real system)

1. Rules evaluate every **15s**; warning `for: 15s`, critical `for: 30s`
2. War Room Fire keeps real `/api/work` load ~**120s** after inject
3. Critical AM→bus suppressed during Fire (one agent run); **warning pre-alert still real**

## Deploy once

### A — Code (Jenkins)
Merge `feature/k8s` → Jenkins `DEPLOY_ENABLED=true` → promote.

### B — Prometheus host `3.111.113.151` (required for red alerts)
Copy updated `k8s/alerting/rules.yml` + `wire-ec2-prometheus.sh`, then:

```bash
sudo NODE_IP=13.207.225.219 bash wire-ec2-prometheus.sh ./rules.yml
```

Confirm http://3.111.113.151:9090/alerts rules show; Status → Config has `evaluation_interval: 15s`.

## Demo (one Fire — real value)

Tabs: War Room | Prom Alerts | AM | Slack

1. **Fire Incident** once  
2. ~20–45s later → Prom **Pending/Firing** (red) → AM group → War Room **yellow PRE-ALERT** → Ack  
3. Agents → **Approve** → **Resolved**  
4. Prom returns **green** = healed  

No Jenkins on camera. No curl webhook for judges.
