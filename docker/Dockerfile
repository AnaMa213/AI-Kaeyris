# Single-stage by choice: the project's deps are pure Python (wheels
# available for asyncpg, argon2-cffi…). A multi-stage build would shave
# ~30 MB on the cost of build-cache complexity (pip --prefix paths get
# brittle when relocated). Revisit if image size becomes a concern.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime OS deps:
#   - ffmpeg/ffprobe: JDR worker chunks audio into ~30s pieces before
#     sending each one to Whisper. ffprobe also reads audio duration at
#     upload (best-effort). Both are why we ship a `slim` not `alpine`.
#   - curl: used by the Docker HEALTHCHECK below to hit /healthz.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

# Install Python deps BEFORE copying source so a code-only change doesn't
# bust the pip layer cache.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN chown -R app:app /app

USER app

EXPOSE 8000

# Liveness probe — mirrors what /healthz returns (Jalon 6). The runtime
# orchestrator (Compose, Watchtower, future K8s) reads `Status: healthy`
# from this instead of guessing port-open == alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
