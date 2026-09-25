#!/usr/bin/env bash
# Build the same War Room + scrape images K8s pulls from ECR, then push.
#
# Usage:
#   ./scripts/docker_ecr_push.sh              # tag = v0.1.0
#   TAG=v0.2.0 ./scripts/docker_ecr_push.sh
#
# Requires: docker, aws cli logged in for account 850887971586 (ap-south-1).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

AWS_ACCOUNT="${AWS_ACCOUNT:-850887971586}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
ECR_HOST="${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
TAG="${TAG:-v0.1.0}"

WARROOM_LOCAL="aegispilot/warroom:${TAG}"
SCRAPE_LOCAL="aegispilot/scrape:${TAG}"
WARROOM_ECR="${ECR_HOST}/aegispilot/warroom:${TAG}"
SCRAPE_ECR="${ECR_HOST}/aegispilot/scrape:${TAG}"

echo "==> Building warroom (${WARROOM_LOCAL})"
docker build -f docker/Dockerfile -t "${WARROOM_LOCAL}" -t "${WARROOM_ECR}" .

echo "==> Building scrape (${SCRAPE_LOCAL})"
docker build -f docker/scrape/Dockerfile -t "${SCRAPE_LOCAL}" -t "${SCRAPE_ECR}" docker/scrape

echo "==> ECR login"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_HOST}"

echo "==> Push ${WARROOM_ECR}"
docker push "${WARROOM_ECR}"

echo "==> Push ${SCRAPE_ECR}"
docker push "${SCRAPE_ECR}"

echo "Done. K8s manifests already reference:"
echo "  ${ECR_HOST}/aegispilot/warroom:${TAG}"
echo "  ${ECR_HOST}/aegispilot/scrape:${TAG}"
echo "Update image tags in k8s/ if TAG != v0.1.0, then roll the Deployments."
