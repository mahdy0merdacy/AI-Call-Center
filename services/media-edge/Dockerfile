# Media edge image. Build from the REPO ROOT, like the other services:
#   docker build -f services/media-edge/Dockerfile -t media-edge .
#
# WebRTC: audio arrives as UDP straight from the caller's browser, on ports the
# edge picks per call during ICE, addressed to the edge's own interface addresses.
# Publishing one TCP port does not cover that, so the browser must be able to
# route to those addresses:
#   * a browser on the same Linux machine: a bridge network is enough, because
#     the host routes to its containers' addresses; publish 8082 for the page
#     and signalling only;
#   * reached from other machines: run on the HOST network (docker --network
#     host; in k8s, hostNetwork: true with dnsPolicy: ClusterFirstWithHostNet) so
#     the candidates are the node's real addresses, Tailscale included.
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
# Upstreams come from the environment: STT_ADDR, ORCHESTRATOR_ADDR, TTS_URL (and
# DEFAULT_TENANT, PORT), set to service names on the compose network or in the
# cluster. Unset, they are the 127.0.0.1 ports of a single host-network machine.
ENTRYPOINT ["python", "server.py"]
CMD ["--host", "0.0.0.0"]
