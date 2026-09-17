# syntax=docker/dockerfile:1
# ============================================================================
# AegisPilot — single-container image for Cloud Run.
# Build context is the repo root:  docker build -f docker/Dockerfile .
#
#   Stage 1 (node:20)          builds the React war room  → frontend/dist
#   Stage 2 (python:3.11-slim) runs FastAPI + serves that dist from "/"
#
# One service hosts both the API and the UI. Secrets are NEVER baked in — they
# are injected as env vars at deploy time (see deploy.sh).
# ============================================================================

# --------------------------------------------------------------------------- #
# Stage 1 — build the frontend
# --------------------------------------------------------------------------- #
FROM node:20-slim AS frontend
WORKDIR /app/frontend

# Install deps first (cached until the lockfile changes).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build the production bundle → /app/frontend/dist
COPY frontend/ ./
RUN npm run build

# --------------------------------------------------------------------------- #
# Stage 2 — Python runtime
# --------------------------------------------------------------------------- #
FROM python:3.11-slim AS runtime

# - PYTHONUNBUFFERED: logs stream to Cloud Run in real time.
# - PYTHONDONTWRITEBYTECODE: no .pyc clutter in the image.
# - MPLBACKEND=Agg: matplotlib renders the seed Grafana snapshot headlessly
#   (no display in the container). generate_grafana.py also sets this itself.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    PORT=8080

WORKDIR /app

# Install backend deps first so the layer caches across code changes.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Application code: the FastAPI app, the agents, and the demo publisher.
COPY backend/ ./backend/
COPY scripts/ ./scripts/

# The compiled frontend from stage 1. main.py mounts this at "/" when present.
COPY --from=frontend /app/frontend/dist ./frontend/dist

EXPOSE 8080

# Shell form so ${PORT} (Cloud Run injects it) expands; defaults to 8080 locally.
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}
