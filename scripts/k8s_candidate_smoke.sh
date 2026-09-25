#!/usr/bin/env bash
# Verifies the inactive Blue/Green candidate directly before it receives traffic.
set -euo pipefail

NAMESPACE="${NAMESPACE:-aegispilot}"
DEPLOY_COLOR="${DEPLOY_COLOR:?DEPLOY_COLOR must be blue or green}"
APP_LABEL="${APP_LABEL:-aegis-warroom}"
HEALTH_PATH="${HEALTH_PATH:-/api/health}"
HEALTH_PORT="${HEALTH_PORT:-8080}"

case "$DEPLOY_COLOR" in blue|green) ;; *) echo "Invalid DEPLOY_COLOR: $DEPLOY_COLOR" >&2; exit 2 ;; esac

# Avoid `kubectl | awk ... exit` under pipefail — awk closing early sends SIGPIPE
# to kubectl and the script exits 141 even when a Ready pod exists.
pod=""
while IFS=$'\t' read -r name ready; do
  if [[ "$ready" == "True" ]]; then
    pod="$name"
    break
  fi
done < <(kubectl get pods -n "$NAMESPACE" \
  -l "app=${APP_LABEL},slot=${DEPLOY_COLOR}" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}')

if [[ -z "$pod" ]]; then
  echo "No Ready ${DEPLOY_COLOR} candidate pod found in ${NAMESPACE}." >&2
  exit 1
fi

body=$(kubectl exec -n "$NAMESPACE" "$pod" -c warroom -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${HEALTH_PORT}${HEALTH_PATH}', timeout=5).read().decode())")

if ! grep -qiE '"status"[[:space:]]*:[[:space:]]*"(ok|healthy|up)"' <<<"$body"; then
  echo "Candidate ${DEPLOY_COLOR} returned an unexpected health payload: ${body}" >&2
  exit 1
fi

echo "Candidate ${DEPLOY_COLOR} smoke test passed via pod ${pod}."
