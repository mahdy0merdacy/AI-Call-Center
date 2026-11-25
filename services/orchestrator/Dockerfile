# Orchestrator image. Build from the REPO ROOT, because tenant configs live there:
#   docker build -f services/orchestrator/Dockerfile -t orchestrator .
#
# The image mirrors the repo layout (services/orchestrator, tenants) so the code's
# relative paths hold. In Kubernetes, mount tenant configs over /srv/aicc/tenants
# or point TENANTS_DIR elsewhere; the baked-in copy is the default.

# Follow the rolling minor tag (rebuilt upstream with Debian security fixes) but
# pin its digest so builds are reproducible. Bump the digest deliberately; a
# patch tag like 3.12.13-slim stops receiving rebuilds once 3.12.14 ships.
ARG BASE=python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# ── deps: build the virtualenv once, copy it into a clean runtime ───────────────
FROM ${BASE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN python -m venv /opt/venv
COPY services/orchestrator/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

# ── runtime ──────────────────────────────────────────────────────────────────
FROM ${BASE}
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TENANTS_DIR=/srv/aicc/tenants

RUN useradd --system --uid 10001 --no-create-home orchestrator
COPY --from=deps /opt/venv /opt/venv
COPY tenants /srv/aicc/tenants
COPY services/orchestrator/gen /srv/aicc/services/orchestrator/gen
COPY services/orchestrator/app /srv/aicc/services/orchestrator/app
# Shared call-event publisher; server.py adds /srv/aicc/libs to the import path.
COPY libs/aicc_events /srv/aicc/libs/aicc_events

WORKDIR /srv/aicc/services/orchestrator
USER 10001
EXPOSE 50052

# For Docker/Compose only; Kubernetes ignores it and uses a native gRPC probe.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

# Exec form: python is PID 1 and receives SIGTERM, which server.py drains on: live
# calls get SHUTDOWN_GRACE_S (25 s) to finish, so give the container 35 s to stop
# (Compose stop_grace_period, k8s terminationGracePeriodSeconds). Docker's 10 s
# default would kill calls mid-sentence.
# Configuration is environment only (HOST, PORT, LLM_*, MCP_SERVERS, KNOWLEDGE_URL,
# PLATFORM_DATABASE_URL...; see app/server.py), so no CMD: flags still work as
# arguments. Call events: set KAFKA_BOOTSTRAP and SCHEMA_REGISTRY_URL.
ENTRYPOINT ["python", "-m", "app.server"]
