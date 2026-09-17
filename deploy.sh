#!/usr/bin/env bash
# ============================================================================
# AegisPilot → Cloud Run deploy (full GCP / Vertex path, upgrade spec Phase 6).
#
# Builds the single-container image from docker/Dockerfile with Cloud Build,
# pushes it to Artifact Registry, deploys to Cloud Run (cost-safe: min 0 /
# max 2), grants the runtime service account the roles it needs, and wires a
# Pub/Sub PUSH subscription to the live URL.
#
# Auth is Application Default Credentials — NO API key. On Cloud Run the runtime
# service account provides ADC automatically (Vertex/Firestore/Pub-Sub). Only
# the optional Slack webhook is read from .env and injected as an env var.
#
# Prereqs:  gcloud auth login ; gcloud config set project <ID> ; ./deploy.sh
# ============================================================================
set -euo pipefail

SERVICE="${SERVICE:-aegisops}"
REGION="${REGION:-us-central1}"          # compute region (Run/AR/Firestore/PubSub)
REPO="${REPO:-aegisops}"
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"      # model region (gemini-3.5-flash)
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
GEMINI_MODEL_PRO="${GEMINI_MODEL_PRO:-gemini-2.5-pro}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Optional: pull SLACK_WEBHOOK_URL (+ model overrides) from .env ----------
if [[ -f .env ]]; then
  set -a; # shellcheck disable=SC1091
  source .env; set +a
fi

PROJECT="$(gcloud config get-value project 2>/dev/null)"
[[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]] && { echo "Set a project: gcloud config set project <ID>" >&2; exit 1; }
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:latest"

echo "▶ Project=${PROJECT}  Region=${REGION}  VertexLoc=${VERTEX_LOCATION}  Image=${IMAGE}"

# --- Enable APIs (idempotent) ------------------------------------------------
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com firestore.googleapis.com pubsub.googleapis.com

# --- Grant the runtime/build SA the roles it needs (idempotent) --------------
# On new GCP projects the compute SA is also the Cloud Build identity, so it
# needs the build + log roles too (else `builds submit` 403s on the source bucket).
for ROLE in roles/aiplatform.user roles/datastore.user roles/pubsub.editor \
            roles/cloudbuild.builds.builder roles/logging.logWriter \
            roles/artifactregistry.writer roles/storage.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${RUNTIME_SA}" --role="$ROLE" \
    --condition=None >/dev/null
done
echo "▶ Waiting 30s for IAM to propagate before build…"
sleep 30

# --- Artifact Registry repo (idempotent) -------------------------------------
if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker --location="$REGION" --description="AegisPilot images"
fi

# --- Build the image from docker/Dockerfile (repo root is the context) -------
CLOUDBUILD_CFG="$(mktemp)"; trap 'rm -f "$CLOUDBUILD_CFG"' EXIT
cat > "$CLOUDBUILD_CFG" <<YAML
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "docker/Dockerfile", "-t", "${IMAGE}", "."]
images: ["${IMAGE}"]
YAML
echo "▶ Building image with Cloud Build…"
gcloud builds submit --config "$CLOUDBUILD_CFG" .

# --- Runtime env (NO secret keys — ADC handles auth). '^@^' delimiter keeps
#     any commas in the webhook intact. ----------------------------------------
ENV_VARS="GOOGLE_GENAI_USE_VERTEXAI=true@GOOGLE_CLOUD_PROJECT=${PROJECT}"
ENV_VARS="${ENV_VARS}@GOOGLE_CLOUD_LOCATION=${REGION}@VERTEX_LOCATION=${VERTEX_LOCATION}"
ENV_VARS="${ENV_VARS}@GEMINI_MODEL=${GEMINI_MODEL}@GEMINI_MODEL_PRO=${GEMINI_MODEL_PRO}"
ENV_VARS="${ENV_VARS}@BACKEND=cloud@ORCHESTRATOR=adk@PUBSUB_MODE=push"
if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  ENV_VARS="${ENV_VARS}@SLACK_WEBHOOK_URL=${SLACK_WEBHOOK_URL}"
fi

# --- Deploy to Cloud Run (cost-safe: min 0 / max 2) --------------------------
echo "▶ Deploying '${SERVICE}' to Cloud Run…"
# --no-cpu-throttling + --min-instances 1: the incident pipeline runs partly as
# a background task after the Pub/Sub push returns 204 and blocks on the human
# approval gate — both need CPU allocated outside request handling. Max 2 caps cost.
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --allow-unauthenticated --port 8080 \
  --min-instances 1 --max-instances 2 \
  --no-cpu-throttling \
  --cpu 1 --memory 1Gi --timeout 300 \
  --set-env-vars "^@^${ENV_VARS}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"

# --- Wire a Pub/Sub PUSH subscription to the live URL ------------------------
gcloud pubsub topics create incident-alerts >/dev/null 2>&1 || true
PUSH_EP="${URL}/api/pubsub/push"
if gcloud pubsub subscriptions describe aegisops-push >/dev/null 2>&1; then
  gcloud pubsub subscriptions modify-push-config aegisops-push --push-endpoint="$PUSH_EP"
else
  gcloud pubsub subscriptions create aegisops-push \
    --topic incident-alerts --push-endpoint="$PUSH_EP" --ack-deadline=60
fi

echo "✓ Deployed."
echo "  War room:      ${URL}"
echo "  Health:        ${URL}/api/health"
echo "  Pub/Sub push:  ${PUSH_EP}"
echo
echo "Fire the demo against the live service:"
echo "  python scripts/publish_alert.py --url ${URL}          # via HTTP"
echo "  python scripts/publish_alert.py --pubsub              # via real Pub/Sub → push"
