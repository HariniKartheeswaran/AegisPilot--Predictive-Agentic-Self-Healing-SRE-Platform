#!/usr/bin/env bash
# Apply in-cluster Alertmanager (safe additive). Run where kubeconfig works.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
kubectl apply -f "${ROOT}/k8s/alerting/alertmanager.yaml"
kubectl -n aegispilot rollout status deploy/aegis-alertmanager --timeout=120s
kubectl -n aegispilot get pods,svc -l app=aegis-alertmanager -o wide
echo
echo "AM UI:  http://<NODE-IP>:30903"
echo "War Room webhook (in-cluster): http://aegis-warroom.aegispilot.svc.cluster.local/api/alertmanager/webhook"
