# Mercer Live ML-1 capture host (docs/MERCER_LIVE_OPERATIONAL_PATH.md).
#
# Build from the REPOSITORY ROOT so the frozen capture code is copied, not
# re-implemented:
#   fly deploy --config mercer-live-host/fly.toml --dockerfile mercer-live-host/Dockerfile \
#              --build-arg DEPLOYMENT_COMMIT=$(git rev-parse --short HEAD)
#
# The image carries CODE only. No raw observation, no digest, no secret, no
# .env.local. Raw evidence lives on the Fly volume mounted at /data and in the
# private R2 bucket; credentials arrive as Fly secrets at run time.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends rclone ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir numpy requests tzdata

# Only what capture.py imports: its own package, the read-only football
# identity helpers, the one credit writer, and the team-name table.
COPY scripts/mercer_live/          ./scripts/mercer_live/
COPY scripts/football/             ./scripts/football/
COPY scripts/odds_credits.py       ./scripts/odds_credits.py
COPY data/football/team_names.json ./data/football/team_names.json

ARG DEPLOYMENT_COMMIT=""
ENV ML1_DEPLOYMENT_COMMIT=$DEPLOYMENT_COMMIT \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    ML1_DATA_DIR=/data/mercer_live \
    ML1_PUBLISHED_DIR=/data/published \
    ML1_ODDS_AUTH=/data/odds-authorization.json

# The Fly volume mounts root-owned at /data. The entrypoint gives the
# non-root user its directories and then drops privileges for good; the
# supervisor and every capture tick run as ml1, never as root.
RUN useradd --create-home --uid 10001 ml1 && chown -R ml1:ml1 /app
CMD ["sh", "-c", "mkdir -p /data/mercer_live /data/published && chown ml1:ml1 /data/mercer_live /data/published && exec setpriv --reuid=10001 --regid=10001 --init-groups python scripts/mercer_live/host.py serve"]
