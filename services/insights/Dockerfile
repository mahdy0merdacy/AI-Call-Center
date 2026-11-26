# Insights API image. Build with this folder as the context:
#   docker build -t insights services/insights
#   docker run -p 8000:8000 -e ANALYTICS_DATABASE_URL=... -e JWT_PUBLIC_KEY=... insights

# Same base as the other services (digest-pinned, see services/orchestrator/Dockerfile).
ARG BASE=python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# ── deps: build the virtualenv once, copy it into a clean runtime ───────────────
FROM ${BASE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

# ── runtime ──────────────────────────────────────────────────────────────────
FROM ${BASE}
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --no-create-home insights
COPY --from=deps /opt/venv /opt/venv
COPY app /srv/insights/app

WORKDIR /srv/insights
USER 10001
EXPOSE 8000

# Exec form: uvicorn is PID 1 and receives SIGTERM for a clean shutdown.
ENTRYPOINT ["uvicorn", "--factory", "app.main:app_from_env"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
