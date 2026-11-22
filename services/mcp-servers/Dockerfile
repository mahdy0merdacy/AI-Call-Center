# MCP servers image: one image, both tenants' tool servers. Build from the REPO ROOT,
# because the fixtures they serve live in tenants/fixtures:
#   docker build -f services/mcp-servers/Dockerfile -t mcp-servers .
#
# Pick the server with the command (the default is Acme's):
#   docker run --rm -p 9101:9101 mcp-servers acme_delivery.py --host 0.0.0.0 --port 9101
#   docker run --rm -p 9102:9102 mcp-servers swiftnet_isp.py  --host 0.0.0.0 --port 9102
#
# The image mirrors the repo layout so the servers' relative fixture paths hold.
# Today the fixtures are JSON baked into the image; once these servers front a
# real database, that becomes a connection string instead and the image stays put.

ARG BASE=python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

FROM ${BASE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN python -m venv /opt/venv
COPY services/mcp-servers/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

FROM ${BASE}
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --no-create-home mcp
COPY --from=deps /opt/venv /opt/venv
COPY tenants/fixtures/shipments.json tenants/fixtures/lines.json /srv/aicc/tenants/fixtures/
COPY services/mcp-servers/acme_delivery.py services/mcp-servers/swiftnet_isp.py /srv/aicc/services/mcp-servers/

WORKDIR /srv/aicc/services/mcp-servers
USER 10001
EXPOSE 9101 9102

# Bind 0.0.0.0: the scripts default to 127.0.0.1, which inside a container is
# reachable only from the container itself.
ENTRYPOINT ["python"]
CMD ["acme_delivery.py", "--host", "0.0.0.0", "--port", "9101"]
