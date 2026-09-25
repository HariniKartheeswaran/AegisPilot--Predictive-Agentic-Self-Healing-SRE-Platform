FROM python:3.11-slim
# Shared scrape image for Compose + K8s/ECR (aegispilot/scrape).
# Build: docker build -t aegispilot/scrape:local docker/scrape
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    SERVICE_NAME=checkout-svc \
    SERVICE_VERSION=v1.0.0 \
    ERROR_RATE=0.0 \
    LATENCY_MS=20 \
    FAIL_READY=false
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 8080
USER nobody
CMD ["python", "app.py"]
