#!/usr/bin/env bash
# Start the core stack (deploy/compose.yaml) and wait until it is healthy.
#
#   scripts/core-up.sh                  # setup (idempotent), build, start, wait
#   scripts/core-up.sh --no-build       # start the images you already have
#   scripts/core-up.sh orchestrator     # rebuild and restart only these services
#                                       # (and whatever they depend on)
#   scripts/core-up.sh --skip-checks    # start even if a prerequisite check fails
#   scripts/core-up.sh --timeout 1200   # seconds to wait for health (default 900)
#
# The first build is long (the STT image is ~10 GB) and cached afterwards. The
# first start is too: knowledge loads bge-m3, STT and TTS warm up on the GPU.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPLOY_DIR=${DEPLOY_DIR:-$ROOT/deploy}
compose() { docker compose -f "$DEPLOY_DIR/compose.yaml" --env-file "$DEPLOY_DIR/.env" "$@"; }

build=(--build)
setup_args=()
services=()
timeout=900
while [ $# -gt 0 ]; do
  case $1 in
    --no-build) build=() ;;
    --skip-checks) setup_args+=(--skip-checks) ;;
    --timeout) timeout=${2:?--timeout needs a number of seconds}; shift ;;
    -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    -*) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    *) services+=("$1") ;;
  esac
  shift
done

if ! "$ROOT/scripts/core-setup.sh" "${setup_args[@]}"; then
  echo "Fix the above, or start anyway with: scripts/core-up.sh --skip-checks" >&2
  exit 1
fi

echo
echo "== docker compose up (waits for every health check, up to ${timeout}s)"
# --wait also treats the one-shot jobs as done once they exit 0.
if ! compose up --detach "${build[@]}" --remove-orphans --wait --wait-timeout "$timeout" "${services[@]}"; then
  echo
  compose ps --all --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
  # The last lines of whatever is unhealthy, still starting, or exited with an error.
  failing=$(compose ps --all --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}' |
    awk -F'|' '$3 == "unhealthy" || $3 == "starting" || $2 == "restarting" ||
               ($2 == "exited" && $4 != 0) || $2 == "dead" { print $1 }')
  for service in $failing; do
    echo
    echo "--- $service (last 30 lines)"
    compose logs --no-log-prefix --tail 30 "$service" || true
  done
  echo
  echo "Something did not come up. More: docker compose -f deploy/compose.yaml logs <service>" >&2
  exit 1
fi

echo
compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
cat <<'EOF'

== ready
  Portal: sign up, upload documents   http://localhost:8080
  Voice test page (pick a client)     http://localhost:8082
  Mailpit: emails the portal sends    http://localhost:8025
  Postgres, for psql                  127.0.0.1:5434, db tenant_portal, user portal
                                      (password: POSTGRES_PASSWORD in deploy/.env)

  Prove the containers talk:          scripts/core-check.sh
  Follow a service:                   docker compose -f deploy/compose.yaml logs -f orchestrator
  Stop (keeps the data):              scripts/core-down.sh
EOF
