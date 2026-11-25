# Auth service (tenant-portal) image. The build context is THIS directory (the service
# root):
#   docker build -t tenant-portal .
# In the monorepo that is services/tenant-portal, so nothing outside it is needed.
#
# STANDARD DJANGO SERVICE IMAGE. services/documents/Dockerfile follows the same
# structure; keep the two in step. They differ only where marked "service-specific":
# the code that is copied, the health path and the runtime settings.

# Same rolling minor tag + pinned digest as the other services (reproducible builds,
# Debian security rebuilds picked up by bumping the digest deliberately).
ARG BASE=python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# ── deps: build the virtualenv once, copy it into a clean runtime ──────────────
# checkov:skip=CKV_DOCKER_7:BASE is pinned by digest in the ARG above (checkov cannot resolve ARGs)
FROM ${BASE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN python -m venv /opt/venv
# Development tools (tests, linters) are installed only when INSTALL_DEV=true, which
# only a local docker compose build sets. Production images never contain them.
ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt /tmp/
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then /opt/venv/bin/pip install -r /tmp/requirements-dev.txt; fi

# ── runtime ────────────────────────────────────────────────────────────────────
FROM ${BASE}
# Numeric non-root user. The default is the same for every service; the local compose
# build overrides it with 1000, because scripts/generate_keys.sh makes the private key
# readable only by the host user, and the bind-mounted key must stay readable.
ARG APP_UID=10001
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HEALTH_PATH=/health/live/ \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN useradd --system --uid ${APP_UID} --no-create-home --shell /usr/sbin/nologin app

COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
# service-specific: the entrypoint (runs migrations only when RUN_MIGRATIONS=true, for
# local compose; in Kubernetes use a Job or init container) and the code this service runs.
COPY --chmod=755 scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY src/ /app/

USER ${APP_UID}
EXPOSE 8000

# Liveness only, no database query, so a database blip does not restart the container.
# The readiness probe (/health/) does check the database. HEALTH_PATH is set above.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8000' + os.environ['HEALTH_PATH'], timeout=2).status == 200 else 1)"

# No secrets here: they are supplied at runtime (see .env.example).
# Exec form: the entrypoint ends with `exec "$@"`, so gunicorn is PID 1 and receives
# SIGTERM for a graceful drain.
ENTRYPOINT ["entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30", "--access-logfile", "-"]
