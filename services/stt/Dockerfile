# STT image: Simul-Whisper on the GPU. Build from the REPO ROOT:
#   docker build -f services/stt/Dockerfile -t stt .
#
# Run with the GPU and the Whisper checkpoint mounted:
#   docker run --rm --gpus all -p 50051:50051 \
#     -v ~/.cache/whisper:/models/whisper:ro stt
#
# What is in the image and what is not:
#   * CUDA libraries: IN, from the PyTorch cu130 wheels (nvidia-* packages). No
#     nvidia/cuda base image, which would ship a second copy of the same libraries.
#   * GPU driver: NOT in, it is always the host's. CUDA 13 needs driver >= 580 on
#     every node that runs this (on WSL2 laptops, the Windows driver).
#   * Whisper checkpoint (small.pt, 462 MB): NOT in. Mounted at /models/whisper, so
#     a code change does not re-ship it. Download, sha256 is in the path:
#     https://openaipublic.azureedge.net/main/whisper/models/9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794/small.pt
#   * Simul-Whisper code (15 MB, Apache-2.0): IN, fetched by commit below.

ARG BASE=python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# ── pipcache: optional pip cache to seed the build from ──────────────────────
# Empty by default, so CI downloads as usual. On a machine whose pip cache
# already holds torch (anyone who set up .venv), skip the 4.4 GB download:
#   docker build --build-context pipcache=$HOME/.cache/pip -f services/stt/Dockerfile .
# pip still verifies each wheel against the hash its index publishes.
FROM scratch AS pipcache

# ── deps ─────────────────────────────────────────────────────────────────────
FROM ${BASE} AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10 \
    PIP_CACHE_DIR=/pipcache
RUN python -m venv /opt/venv
# Torch first, on its own: ~4.4 GB with its CUDA libraries, and it changes far less
# often than requirements.txt. Layer order is what keeps a rebuild (and a pull on
# a laptop over home internet) down to megabytes.
# rw: pip may write to the cache, but writes are discarded and never reach the image.
RUN --mount=type=bind,from=pipcache,target=/pipcache,rw \
    /opt/venv/bin/pip install --index-url https://download.pytorch.org/whl/cu130 \
        torch==2.14.0+cu130 torchaudio==2.11.0+cu130
COPY services/stt/requirements.txt /tmp/requirements.txt
RUN --mount=type=bind,from=pipcache,target=/pipcache,rw \
    /opt/venv/bin/pip install -r /tmp/requirements.txt

# ── runtime ──────────────────────────────────────────────────────────────────
FROM ${BASE}
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIMUL_WHISPER_DIR=/opt/simul_whisper \
    HOME=/tmp \
    NUMBA_CACHE_DIR=/tmp/numba

RUN useradd --system --uid 10001 --no-create-home stt
COPY --from=deps /opt/venv /opt/venv
# The commit the repo's simul_whisper gitlink pins. whisper_compat.py shims it at
# import time rather than editing it, so it stays upstream-pristine.
ADD https://github.com/backspacetg/simul_whisper.git#ffeb3ff333026f29053d01c95a1a0524f2a41865 /opt/simul_whisper
COPY services/stt/app /srv/aicc/services/stt/app

WORKDIR /srv/aicc/services/stt
USER 10001
EXPOSE 50051

# Exec form: python is PID 1 and gets SIGTERM; server.py stops reporting SERVING,
# drains open streams, then exits hard so the GPU memory is released at once.
# Readiness is the standard gRPC health service, SERVING only after warm-up.
ENTRYPOINT ["python", "-m", "app.server"]
CMD ["--host", "0.0.0.0", "--port", "50051", "--model-path", "/models/whisper/small.pt"]
