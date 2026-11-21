# Ingestion worker image. Build context is this directory (services/ingestion).
#   docker build -t ingestion .

# Same base + pinned digest as the documents service.
ARG BASE=python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# ── deps: build the virtualenv once, copy it into a clean runtime ──────────────
# checkov:skip=CKV_DOCKER_7:BASE is pinned by digest in the ARG above (checkov cannot resolve ARGs)
FROM ${BASE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

# ── runtime ────────────────────────────────────────────────────────────────────
FROM ${BASE}
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ROOT=/data/media

# Same numeric non-root user as the documents service (uid 10001), so the shared
# media volume is readable. It is mounted read-only: this worker never writes files.
RUN useradd --system --uid 10001 --no-create-home app

COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY ingestion ./ingestion

USER 10001

# No HTTP port: it is a background worker. No secrets here: MONGODB_URI and
# DATABASE_URL are supplied at runtime.
CMD ["python", "-m", "ingestion.worker"]
