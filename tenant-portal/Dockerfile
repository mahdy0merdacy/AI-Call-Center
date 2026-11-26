# Tenant portal web image ("web"): the React build served by nginx, which also proxies
# the API paths to the auth (portal) and documents services, so the browser talks to
# one origin. The build context is THIS directory:
#   docker build -t aicc/web:dev tenant-portal
#
# Configured only by environment variables at start-up, so the same image runs on
# Docker and on k3s (defaults below; see README.md, "Container image"):
#   PORTAL_UPSTREAM, DOCUMENTS_UPSTREAM   backends, resolved per request
#   DNS_RESOLVER                          the resolver for them (Docker's by default)
#   VOICE_TEST_URL                        media edge test page, linked from the dashboard

# Tag + digest, like the other images. Node matches the frontend CI job.
ARG NODE_IMAGE=node:22.12.0-bookworm-slim@sha256:35531c52ce27b6575d69755c73e65d4468dba93a25644eed56dc12879cae9213
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03

# ── build: the static bundle ───────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS build
ENV npm_config_fund=false npm_config_audit=false npm_config_update_notifier=false
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Same origin: this image's nginx proxies /api/... to the backends. Settings that
# differ per deployment are read at runtime from /config.js, never baked in here.
ENV VITE_API_BASE_URL="" VITE_DOCUMENTS_API_URL="" VITE_ENABLE_MSW=false
# config.js is the development placeholder (nginx serves the generated one) and the
# MSW worker only runs in the mock build.
RUN npm run build && rm dist/config.js dist/mockServiceWorker.js

# ── runtime: nginx as uid 101, port 8080 ───────────────────────────────────────
FROM ${NGINX_IMAGE}
# The stock site would also listen on 8080, and its sample pages have no use here.
USER root
RUN rm /etc/nginx/conf.d/default.conf /usr/share/nginx/html/*
USER 101

ENV PORTAL_UPSTREAM=http://portal:8000 \
    DOCUMENTS_UPSTREAM=http://documents:8000 \
    INSIGHTS_UPSTREAM=http://insights.aicc-data.svc.cluster.local:8000 \
    DNS_RESOLVER=127.0.0.11 \
    VOICE_TEST_URL=http://localhost:8082
# envsubst replaces only these in the template, so nginx's own $variables survive.
ENV NGINX_ENVSUBST_FILTER=^(DNS_RESOLVER|PORTAL_UPSTREAM|DOCUMENTS_UPSTREAM|INSIGHTS_UPSTREAM)\$

# Owned by root and read-only to nginx; config.js and the rendered conf are written
# under /tmp and /etc/nginx/conf.d at start-up (mount emptyDirs there on k3s with
# readOnlyRootFilesystem).
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/default.conf.template /etc/nginx/templates/default.conf.template
COPY --chmod=755 docker/18-upstreams.envsh docker/40-runtime-config.sh /docker-entrypoint.d/

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1
# ENTRYPOINT and CMD come from the base image: /docker-entrypoint.sh runs the
# docker-entrypoint.d scripts, then execs nginx in the foreground (PID 1, SIGQUIT
# for a graceful stop).
