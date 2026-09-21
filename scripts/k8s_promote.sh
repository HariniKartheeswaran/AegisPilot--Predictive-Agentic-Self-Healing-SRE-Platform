#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-aegispilot}"
ACTIVE_SERVICE="${ACTIVE_SERVICE:-aegis-warroom}"
BLUE_DEPLOYMENT="${BLUE_DEPLOYMENT:-aegis-warroom-blue}"
GREEN_DEPLOYMENT="${GREEN_DEPLOYMENT:-aegis-warroom-green}"

log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

deployment_for_slot() {
  case "$1" in
    blue) echo "$BLUE_DEPLOYMENT" ;;
    green) echo "$GREEN_DEPLOYMENT" ;;
    *) fail "unknown slot '$1'" ;;
  esac
}

other_slot() {
  case "$1" in
    blue) echo "green" ;;
    green) echo "blue" ;;
    *) fail "unknown slot '$1'" ;;
  esac
}

deployment_ready() {
  local deployment=$1
  local desired ready
  desired=$(kubectl get deployment "$deployment" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)
  ready=$(kubectl get deployment "$deployment" -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
  desired=${desired:-0}
  ready=${ready:-0}
  [[ "$ready" -ge 1 && "$ready" -eq "$desired" ]]
}

patch_service_slot() {
  local slot=$1
  kubectl patch service "$ACTIVE_SERVICE" -n "$NAMESPACE" --type merge \
    -p "{\"metadata\":{\"labels\":{\"slot\":\"${slot}\"}},\"spec\":{\"selector\":{\"slot\":\"${slot}\"}}}" \
    >/dev/null
}

current_slot=$(kubectl get service "$ACTIVE_SERVICE" -n "$NAMESPACE" -o jsonpath='{.spec.selector.slot}' 2>/dev/null || true)
[[ -z "$current_slot" ]] && fail "could not read active slot from service ${ACTIVE_SERVICE}"

target_slot=$(other_slot "$current_slot")
target_deployment=$(deployment_for_slot "$target_slot")

log "promote: namespace=${NAMESPACE} service=${ACTIVE_SERVICE} current=${current_slot} target=${target_slot}"

if [[ "$current_slot" == "$target_slot" ]]; then
  log "already on slot ${target_slot}; nothing to promote"
  exit 0
fi

if ! deployment_ready "$target_deployment"; then
  fail "target deployment ${target_deployment} is not ready"
fi

log "patching service selector to slot=${target_slot}"
if ! patch_service_slot "$target_slot"; then
  fail "failed to patch service ${ACTIVE_SERVICE}"
fi

verified_slot=$(kubectl get service "$ACTIVE_SERVICE" -n "$NAMESPACE" -o jsonpath='{.spec.selector.slot}')
if [[ "$verified_slot" != "$target_slot" ]]; then
  fail "service selector verification failed (expected ${target_slot}, got ${verified_slot})"
fi

# Only the active slot should pull Pub/Sub / hold in-memory approval gates.
# Scale the previous slot to zero so alerts and /approve stay on one process.
prev_deployment=$(deployment_for_slot "$current_slot")
log "scaling previous slot ${current_slot} (${prev_deployment}) to 0 replicas"
kubectl scale deployment "$prev_deployment" -n "$NAMESPACE" --replicas=0 >/dev/null

log "promote succeeded: ${current_slot} -> ${target_slot}"
exit 0
