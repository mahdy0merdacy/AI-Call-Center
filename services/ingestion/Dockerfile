# Ingestion image. Build context is this directory (services/ingestion).
#   docker build -t ingestion .
# One image, two programs:
#   worker      default CMD               turns uploads into document_chunks
#   knowledge   python -m ingestion.api   the one model in RAM: embeds and searches,
#                                         port 8100 (PORT), probes /livez and /healthz

# Same base + pinned digest as the documents service.
ARG BASE=python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# ── deps: build the virtualenv once, copy it into a clean runtime ──────────────
# checkov:skip=CKV_DOCKER_7:BASE is pinned by digest in the ARG above (checkov cannot resolve ARGs)
FROM ${BASE} AS deps
# Slow or unstable internet: wait up to 120 s when a download stalls (default 15 s)
# and retry up to 10 times, instead of failing the whole build.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN python -m venv /opt/venv
# pip's download cache lives in a BuildKit cache mount: kept between builds (a retry
# does not download torch's ~210 MB again) but NOT copied into the image.
# CPU-only torch FIRST, from the PyTorch CPU index (~200 MB instead of ~3 GB for
# the default GPU build). requirements.txt pins the same version, so the next
# install sees it as already satisfied.
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install -r /tmp/requirements.txt

# ── runtime ────────────────────────────────────────────────────────────────────
FROM ${BASE}
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ROOT=/data/media \
    HF_HOME=/models/huggingface

# Same numeric non-root user as the documents service (uid 10001), so the shared
# media volume is readable. It is mounted read-only: this worker never writes files.
# /models holds the downloaded embedding model (hf_cache volume in compose). It is
# created here, owned by uid 10001, so a NEW volume mounted on it gets the same
# owner and the worker can write the model there on first start.
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /models/huggingface \
    && chown -R 10001 /models

COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY ingestion ./ingestion

USER 10001

# The knowledge API's port; the worker opens none. No secrets here: MONGODB_URI,
# DATABASE_URL and EMBEDDING_URL are supplied at runtime.
EXPOSE 8100

# Exec form: python is PID 1 and gets SIGTERM, so the worker finishes its current
# document (and the API its requests) before stopping.
CMD ["python", "-m", "ingestion.worker"]
