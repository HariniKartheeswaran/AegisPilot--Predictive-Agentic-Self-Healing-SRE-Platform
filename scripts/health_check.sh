#!/usr/bin/env bash
# ==============================================================================
# health_check.sh — Smoke & readiness verification script for Jenkins CI/CD
#
# Validates that the application or deployment slot is alive, responsive,
# and serving healthy responses on the /api/health endpoint.
#
# Usage:
#   TARGET_URL=http://localhost:8080/api/health ./scripts/health_check.sh
#   or:
#   BASE_URL=http://localhost:8080 ./scripts/health_check.sh
# ==============================================================================
set -euo pipefail

BASE="${BASE_URL:-}"
TARGET="${TARGET_URL:-}"

if [[ -z "$TARGET" ]]; then
  if [[ -n "$BASE" ]]; then
    TARGET="${BASE%/}/api/health"
  else
    TARGET="http://localhost:8080/api/health"
  fi
fi

MAX_RETRIES="${MAX_RETRIES:-5}"
RETRY_DELAY="${RETRY_DELAY:-3}"

echo "============================================================"
echo "▶ Running health check against: ${TARGET}"
echo "▶ Max retries: ${MAX_RETRIES} | Retry delay: ${RETRY_DELAY}s"
echo "============================================================"

attempt=1
success=false

while [[ $attempt -le $MAX_RETRIES ]]; do
  echo "Attempt ${attempt}/${MAX_RETRIES}..."
  
  # Fetch health endpoint response; enforce timeout of 10s per request
  if response=$(curl --fail --silent --show-error --max-time 10 "$TARGET" 2>&1); then
    # Verify the payload contains status ok
    if echo "$response" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      echo "✓ Health check passed on attempt ${attempt}."
      echo "Response payload: ${response}"
      success=true
      break
    else
      echo "⚠ HTTP succeeded but unexpected payload received: ${response}"
    fi
  else
    echo "⚠ Request failed: ${response}"
  fi

  if [[ $attempt -lt $MAX_RETRIES ]]; then
    echo "Waiting ${RETRY_DELAY}s before next attempt..."
    sleep "$RETRY_DELAY"
  fi
  attempt=$((attempt + 1))
done

if [[ "$success" = true ]]; then
  echo "============================================================"
  echo "✓ HEALTH CHECK SUCCEEDED"
  echo "============================================================"
  exit 0
else
  echo "============================================================"
  echo "✖ HEALTH CHECK FAILED after ${MAX_RETRIES} attempts"
  echo "============================================================"
  exit 1
fi
