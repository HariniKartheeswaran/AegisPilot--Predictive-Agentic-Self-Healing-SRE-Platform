#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-aegispilot}"
ACTIVE_SERVICE="${ACTIVE_SERVICE:-aegis-warroom}"
SVC="${SVC:-$ACTIVE_SERVICE}"
HEALTH_PATH="${HEALTH_PATH:-/api/health}"
HEALTH_PORT="${HEALTH_PORT:-8080}"
CURL_IMAGE="${CURL_IMAGE:-curlimages/curl:8.5.0}"

log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

check_health_body() {
  local body=$1
  [[ -n "$body" ]] || return 1
  echo "$body" | grep -qiE '"status"[[:space:]]*:[[:space:]]*"(ok|healthy|up)"' && return 0
  echo "$body" | grep -qiE '(^|[{,[:space:]])"(ok|healthy|up)"([,}]|$)' && return 0
  echo "$body" | grep -qi 'ok' && return 0
  return 1
}

exec_health_in_pod() {
  local pod=$1
  kubectl exec -n "$NAMESPACE" "$pod" -c warroom -- \
    python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${HEALTH_PORT}${HEALTH_PATH}', timeout=5).read().decode())" \
    2>/dev/null
}

run_curl_pod() {
  local url=$1
  local name="aegis-smoke-$(date +%s)-$RANDOM"
  kubectl run "$name" -n "$NAMESPACE" --restart=Never \
    --image="$CURL_IMAGE" \
    --rm -i --quiet \
    --command -- curl -sf --max-time 10 "$url"
}

active_slot=$(kubectl get service "$SVC" -n "$NAMESPACE" -o jsonpath='{.spec.selector.slot}' 2>/dev/null || true)
[[ -z "$active_slot" ]] && fail "could not read active slot from service ${SVC}"

log "smoke: namespace=${NAMESPACE} service=${SVC} slot=${active_slot} path=${HEALTH_PATH}"

endpoints=$(kubectl get endpoints "$SVC" -n "$NAMESPACE" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)
if [[ -z "$endpoints" ]]; then
  log "WARN: no endpoints yet for service ${SVC}"
else
  log "endpoints: ${endpoints}"
fi

pod=$(kubectl get pods -n "$NAMESPACE" \
  -l "app=aegis-warroom,slot=${active_slot}" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
  2>/dev/null | awk '$2=="True"{print $1; exit}')

body=""
if [[ -n "$pod" ]]; then
  log "checking health via pod exec: ${pod}"
  if body=$(exec_health_in_pod "$pod"); then
    if check_health_body "$body"; then
      log "smoke succeeded via pod exec"
      log "response: ${body}"
      exit 0
    fi
    log "WARN: pod exec returned unexpected body: ${body}"
  else
    log "WARN: pod exec health check failed for ${pod}"
  fi
else
  log "WARN: no ready warroom pod found for slot=${active_slot}"
fi

service_url="http://${SVC}.${NAMESPACE}.svc.cluster.local${HEALTH_PATH}"
log "checking health via ephemeral curl pod: ${service_url}"
if body=$(run_curl_pod "$service_url"); then
  if check_health_body "$body"; then
    log "smoke succeeded via curl pod"
    log "response: ${body}"
    exit 0
  fi
  fail "curl pod returned unexpected body: ${body}"
fi

fail "smoke test failed for ${SVC}${HEALTH_PATH}"
