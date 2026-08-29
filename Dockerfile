# Model v0.1 experiment runner -- the hosted endpoint cron POSTs to.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingest/        ./ingest/
COPY scripts/       ./scripts/
COPY db/migrations/ ./db/migrations/

# The commit this image was built from, baked at build time. The runner reads it
# for the activation provenance record. Baked rather than read from .git so the
# image does not have to carry the repository's history -- and so provenance
# cannot silently come from a stale checkout inside the container.
#
#   fly deploy --build-arg DEPLOYMENT_COMMIT=$(git rev-parse --short HEAD)
ARG DEPLOYMENT_COMMIT=""
ENV OLP_DEPLOYMENT_COMMIT=$DEPLOYMENT_COMMIT

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OLP_BIND_HOST=0.0.0.0 \
    PORT=8080

# Non-root. The process writes nothing to disk.
RUN useradd --create-home --uid 10001 runner && chown -R runner:runner /app
USER runner

EXPOSE 8080
CMD ["python", "scripts/v01_service.py"]
