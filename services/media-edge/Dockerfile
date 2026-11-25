# Media edge image. Build from the REPO ROOT, like the other services:
#   docker build -f services/media-edge/Dockerfile -t media-edge .
#
# WebRTC: audio arrives as UDP straight from the caller's browser, on ports the
# edge picks per call during ICE. Publishing one TCP port does not cover that, so
# run the container on the HOST network (docker --network host; in k8s,
# hostNetwork: true with dnsPolicy: ClusterFirstWithHostNet) and the browser
# gets candidates on the node's real addresses, Tailscale included.
#
# Browsers only allow the microphone on a secure origin: http://localhost works,
# http://<another machine's IP> does not. Reaching the edge from another machine
# needs HTTPS in front of it (e.g. `tailscale serve`).

ARG BASE=python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

FROM ${BASE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN python -m venv /opt/venv
COPY services/media-edge/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

FROM ${BASE}
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --no-create-home edge
COPY --from=deps /opt/venv /opt/venv
COPY services/media-edge/gen /srv/aicc/services/media-edge/gen
COPY services/media-edge/static /srv/aicc/services/media-edge/static
COPY services/media-edge/server.py /srv/aicc/services/media-edge/server.py

WORKDIR /srv/aicc/services/media-edge
USER 10001
EXPOSE 8082

# Exec form so python is PID 1 and gets SIGTERM; aiohttp then closes open calls.
# Upstream addresses are flags: k8s Service names in the cluster, localhost on a
# single machine with host networking.
ENTRYPOINT ["python", "server.py"]
CMD ["--host", "0.0.0.0", "--port", "8082", \
     "--stt", "127.0.0.1:50051", "--orchestrator", "127.0.0.1:50052", \
     "--tts", "http://127.0.0.1:8083"]
