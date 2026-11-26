# Sentiment (emotion) service image. Build with this folder as the context:
#   docker build -t sentiment services/sentiment
#
# The model is NOT baked in (it is ~1 GB and trained separately). Mount a trained
# model folder and point MODEL_DIR at it:
#   docker run -p 8080:8080 -v ./models/20261120-1703/model:/models/current:ro sentiment

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
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    MODEL_DIR=/models/current \
    PYTHONPATH=/srv/sentiment/gen

RUN useradd --system --uid 10001 --no-create-home sentiment
COPY --from=deps /opt/venv /opt/venv
COPY app /srv/sentiment/app
COPY gen /srv/sentiment/gen

WORKDIR /srv/sentiment
USER 10001
EXPOSE 8080

# Exec form: uvicorn is PID 1 and receives SIGTERM for a clean shutdown.
# The same image also runs the Kafka worker: override the entrypoint,
#   docker run --entrypoint python ... sentiment -m app.consumer
ENTRYPOINT ["uvicorn", "app.service:app"]
CMD ["--host", "0.0.0.0", "--port", "8080"]
