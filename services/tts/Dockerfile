# TTS image: NVIDIA NeMo-Speech.cpp serving Magpie TTS on the GPU.
#   docker build -f services/tts/Dockerfile -t tts .
#
# There is no source of ours in this service: it is NVIDIA's native (C++/ggml)
# server, pinned to the release we measured latency on, and verified by checksum.
# The archive bundles the CUDA runtime and cuBLAS it needs (CUDA 12); the driver
# comes from the host, as for STT. The binary links libcuda.so.1 directly, so it
# will not even start (not even with --device cpu) unless the NVIDIA container
# toolkit has injected the driver: docker --gpus all, or runtimeClassName: nvidia.
#
# Models are NOT in the image (Magpie 448 MB + codec 76 MB, NVIDIA Open Model
# License): they live in a model directory mounted at /models/nemo-speech, the
# same layout as ~/.cache/nemo-speech/models. Fill an empty one with this image:
#   docker run --rm -v tts-models:/models/nemo-speech tts \
#     pull nvidia/magpie_tts_multilingual_357m
#   docker run --rm -v tts-models:/models/nemo-speech tts \
#     pull nvidia/nemo-nano-codec-22khz-1.89kbps-21.5fps
# Downloads are pinned by revision and SHA-256-verified by nemo-speech itself.
#
# Run:
#   docker run --rm --gpus all -p 8083:8083 -v ~/.cache/nemo-speech/models:/models/nemo-speech tts

ARG BASE=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
ARG NEMO_SPEECH_VERSION=0.1.0

# ── fetch: download and unpack the release ───────────────────────────────────
FROM ${BASE} AS fetch
ARG NEMO_SPEECH_VERSION
# The checksum is the release's published .sha256; a different file fails the build.
ADD --checksum=sha256:e68628f396489c98fb353e070efaea5bc4977409ae7734fce56c251a79e29147 \
    https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v${NEMO_SPEECH_VERSION}/nemo-speech-${NEMO_SPEECH_VERSION}-linux-x86_64-cuda.tar.gz \
    /tmp/nemo-speech.tar.gz
RUN mkdir /opt/nemo-speech \
 && tar -xzf /tmp/nemo-speech.tar.gz -C /opt/nemo-speech --strip-components=1 \
 && rm -rf /opt/nemo-speech/include /opt/nemo-speech/lib/cmake

# ── runtime ──────────────────────────────────────────────────────────────────
FROM ${BASE}
# curl: nemo-speech shells out to it for `pull`. ca-certificates: for HTTPS.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home tts
COPY --from=fetch /opt/nemo-speech /opt/nemo-speech

ENV PATH=/opt/nemo-speech/bin:$PATH \
    HOME=/tmp \
    NEMO_SPEECH_MODEL_DIR=/models/nemo-speech

USER 10001
EXPOSE 8083

# /ready answers 200 only once the engine is loaded; use it for readiness.
ENTRYPOINT ["nemo-speech"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8083", "--no-ui", \
     "--tts-model", "nvidia/magpie_tts_multilingual_357m", \
     "--codec-model", "nvidia/nemo-nano-codec-22khz-1.89kbps-21.5fps", \
     "--device", "cuda"]
