# Documents service image. The build context is THIS directory (the service root):
#   docker build -t documents .
# In the monorepo that is services/documents, so nothing outside it is needed.

# Same rolling minor tag + pinned digest as the other services (reproducible builds,
# Debian security rebuilds picked up by bumping the digest deliberately).
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

# Numeric non-root user. /data/media holds uploaded files.
# NOTE: this is local container disk, which conflicts with the spec's stateless
# services requirement (NFR-01). Known, accepted gap for now: mount a volume here
# (see docker-compose.yml) until files move to shared object storage.
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data/media \
    && chown -R app /data

COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY manage.py ./
COPY config ./config
COPY documents ./documents

USER 10001
EXPOSE 8000

# No secrets here: DJANGO_SECRET_KEY, MONGODB_URI etc. are supplied at runtime.
# Plain health endpoint, no database query (spec NFR-03).
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=2).status == 200 else 1)"

# Exec form: gunicorn is PID 1 and receives SIGTERM for a graceful drain.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
