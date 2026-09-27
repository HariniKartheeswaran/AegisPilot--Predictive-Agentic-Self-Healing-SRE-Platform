# Final demo wire-up (do today, in order)

Code is on `origin/feature/k8s` (`2ba966d`). Docker Compose changes were **not** committed.

## A — Jenkins: build + deploy new War Room image

Build job from branch **`feature/k8s`** with:

| Param | Value |
|-------|--------|
| `DEPLOY_ENABLED` | **true** |
| `RUN_SONAR` | true (optional) |
| `K8S_COMMIT` | `2593d5588042e0af43f24987145a68ff9b83540e` |

Promote candidate when asked. After deploy, check:

```text
http://13.207.225.219/api/pre-alerts
```

Must return `[]` (not 404).

## B — Apply Alertmanager (machine with kubeconfig)

```bash
kubectl apply -f k8s/alerting/alertmanager.yaml
# or: bash k8s/alerting/apply-alertmanager.sh
kubectl -n aegispilot get pods,svc -l app=aegis-alertmanager
```

Open: http://13.207.225.219:30903

## C — EC2 Prometheus rules (on 3.111.113.151)

Copy `k8s/alerting/rules.yml` + `wire-ec2-prometheus.sh` to the Prom host, then:

```bash
sudo NODE_IP=13.207.225.219 bash wire-ec2-prometheus.sh ./rules.yml
```

Open: http://3.111.113.151:9090/alerts  
Expect: `HighErrorRateWarning` + `HighErrorRate`

## D — Demo script

1. http://13.207.225.219/ → **Fire** (manual agents — already proven)
2. Or spike errors and wait ~30s → Prom firing → AM groups → War Room **Pre-alert** then full incident
3. Grafana http://3.111.113.151:3000 — charts

## Verify checklist

- [ ] `/api/pre-alerts` → 200
- [ ] AM UI `:30903` loads
- [ ] Prom `/alerts` shows Aegis rules
- [ ] Pre-alert banner after mild spike / webhook test
- [ ] Critical → agents (Pub/Sub path with `BACKEND=cloud`)
