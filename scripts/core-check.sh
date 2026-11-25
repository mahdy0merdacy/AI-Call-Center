#!/usr/bin/env bash
# Prove the containers of the core stack talk to each other.
#
#   scripts/core-check.sh
#
# For every call the app makes, a probe runs INSIDE the calling container and
# dials the callee by its service name, with the client library and the
# configuration that service itself uses (deploy/check/probe.py). Then the same
# from the host through the published ports, as a browser would. Prints one row
# per call (from -> to, protocol, result, latency) and exits non-zero if any
# fails. Read-only: it writes nothing anywhere.

# The '${NAME}' arguments below are single-quoted on purpose: the probe expands
# them inside the container, from that service's own environment.
# shellcheck disable=SC2016
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPLOY_DIR=${DEPLOY_DIR:-$ROOT/deploy}
PROBE=$DEPLOY_DIR/check/probe.py
compose() { docker compose -f "$DEPLOY_DIR/compose.yaml" --env-file "$DEPLOY_DIR/.env" "$@"; }

case ${1:-} in
  -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
  "") ;;
  *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
esac
if [ ! -f "$DEPLOY_DIR/.env" ]; then
  echo "deploy/.env is missing: start the stack first with scripts/core-up.sh" >&2
  exit 1
fi

if [ -t 1 ]; then GREEN=$'\e[32m' RED=$'\e[31m' BOLD=$'\e[1m' RESET=$'\e[0m'; else GREEN='' RED='' BOLD='' RESET=''; fi

echo "${BOLD}== services${RESET}"
compose ps --all --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
mapfile -t running < <(compose ps --services --status running)
is_running() { [[ " ${running[*]} " == *" $1 "* ]]; }

total=0
failed=0
header() {
  echo
  echo "${BOLD}== $1${RESET}"
  printf '%-13s %-20s %-26s %-6s %8s  %s\n' FROM TO PROTOCOL RESULT LATENCY DETAIL
}
# FROM TO PROTOCOL RESULT LATENCY DETAIL
row() {
  local color=$GREEN
  total=$((total + 1))
  if [ "$4" != OK ]; then
    color=$RED
    failed=$((failed + 1))
  fi
  printf '%-13s %-20s %-26s %s%-6s%s %8s  %s\n' "$1" "$2" "$3" "$color" "$4" "$RESET" "$5" "$6"
}

# The web image is nginx on Alpine: no Python, so its probe is BusyBox sh + wget.
# Arguments: the env var holding the upstream, its default, the path. /proc/uptime
# is the only sub-second clock there (10 ms resolution).
WGET_PROBE='
base=$(printenv "$1" || true); [ -n "$base" ] || base=$2
t0=$(cut -d" " -f1 /proc/uptime)
if body=$(wget -q -O - -T 5 "$base$3" 2>&1); then result=OK; else
  rc=$?; result=FAIL; [ -n "$body" ] || body="wget failed (exit $rc)"; fi
t1=$(cut -d" " -f1 /proc/uptime)
ms=$(awk -v a="$t0" -v b="$t1" "BEGIN { printf \"%d\", (b - a) * 1000 }")
echo "$result|$ms|$base$3 -> $(printf "%s" "$body" | tr -d "|" | tr "\n" " " | cut -c1-90)"
'

# FROM TO PROTOCOL KIND ARGS... (see deploy/check/probe.py for the kinds)
probe() {
  local from=$1 to=$2 protocol=$3 out line result ms detail
  shift 3
  if [ "$from" != host ] && ! is_running "$from"; then
    row "$from" "$to" "$protocol" FAIL - "$from is not running"
    return
  fi
  case $from in
    host) out=$(python3 -I - "$@" <"$PROBE" 2>&1) || true ;;
    web) out=$(compose exec -T web sh -c "$WGET_PROBE" sh "$@" </dev/null 2>&1) || true ;;
    *) out=$(compose exec -T "$from" python -I - "$@" <"$PROBE" 2>&1) || true ;;
  esac
  line=$(printf '%s\n' "$out" | grep -E '^(OK|FAIL)\|' | tail -n 1 || true)
  if [ -z "$line" ]; then
    row "$from" "$to" "$protocol" FAIL - "$(printf '%s' "$out" | tail -n 1 | cut -c1-120)"
    return
  fi
  IFS='|' read -r result ms detail <<<"$line"
  row "$from" "$to" "$protocol" "$result" "$ms ms" "$detail"
}

# A one-shot job proves its own call by exiting 0.
job() {
  local name=$1 to=$2 protocol=$3 state code
  state=$(compose ps --all --format '{{.State}}|{{.ExitCode}}' "$name" 2>/dev/null | head -n 1)
  code=${state#*|}
  if [ -z "$state" ]; then
    row "$name" "$to" "$protocol" FAIL - "no container: it never ran"
  elif [ "${state%|*}" = exited ] && [ "$code" = 0 ]; then
    row "$name" "$to" "$protocol" OK - "job exited 0"
  else
    row "$name" "$to" "$protocol" FAIL - "job ${state%|*}, exit code $code"
  fi
}

# No tenant has this id: the search must answer, and answer with nothing.
NO_TENANT=00000000-0000-4000-8000-000000000000

header "voice plane: calls between containers, by service name"
probe edge orchestrator "gRPC health" grpc '${ORCHESTRATOR_ADDR:-orchestrator:50052}'
probe edge stt "gRPC health" grpc '${STT_ADDR:-stt:50051}'
probe edge tts "HTTP GET /ready" http '${TTS_URL:-http://tts:8083}/ready'
probe orchestrator mcp-acme "MCP initialize+list" mcp http://mcp-acme:9101/mcp
probe orchestrator mcp-swiftnet "MCP initialize+list" mcp http://mcp-swiftnet:9102/mcp
probe orchestrator knowledge "HTTP POST /v1/search" post \
  '${KNOWLEDGE_URL:-http://knowledge:8100}/v1/search' \
  "{\"tenant_id\": \"$NO_TENANT\", \"query\": \"opening hours\", \"k\": 1}"
probe orchestrator postgres "SQL as voice_reader" pg '${PLATFORM_DATABASE_URL}' \
  "SELECT format('%s active of %s tenants', count(*) FILTER (WHERE is_active), count(*)) FROM accounts_tenant"
probe orchestrator postgres "SQL, must be refused" pg-denied '${PLATFORM_DATABASE_URL}' \
  "SELECT email FROM accounts_user LIMIT 1"

header "platform and RAG: calls between containers, by service name"
probe knowledge postgres "SQL as voice_reader" pg '${DATABASE_URL}' \
  "SELECT format('%s chunks of %s tenants', count(*), count(DISTINCT tenant_id)) FROM document_chunks"
probe ingestion knowledge "HTTP POST /v1/embed" post \
  '${EMBEDDING_URL:-http://knowledge:8100}/v1/embed' '{"texts": ["connectivity check"]}'
probe ingestion postgres "SQL as owner" pg '${DATABASE_URL}' \
  "SELECT format('%s chunks', count(*)) FROM document_chunks"
probe ingestion mongo "MongoDB" mongo '${MONGODB_URI}' '${MONGODB_DB:-documents}' documents_document
probe documents mongo "MongoDB" mongo '${MONGODB_URI}' '${MONGODB_DB:-documents}' documents_document
probe portal postgres "SQL as owner" pg '${DATABASE_URL}' \
  "SELECT format('%s tenants, %s users', (SELECT count(*) FROM accounts_tenant), (SELECT count(*) FROM accounts_user))"
probe portal mailpit "SMTP EHLO" smtp '${EMAIL_HOST:-mailpit}:${EMAIL_PORT:-1025}'
probe web portal "HTTP GET /health/live/" PORTAL_UPSTREAM http://portal:8000 /health/live/
probe web documents "HTTP GET /health/" DOCUMENTS_UPSTREAM http://documents:8000 /health/
job db-grants postgres "psql (grants)"
job mongo-setup mongo "mongosh (app user)"

header "from the host, through the published ports (what a browser sees)"
if command -v python3 >/dev/null; then
  probe host web "HTTP GET /healthz" http http://127.0.0.1:8080/healthz
  probe host "web -> portal" "HTTP GET /health/" http http://127.0.0.1:8080/health/
  # 401 is the right answer: the gateway reached documents, which wants a token.
  probe host "web -> documents" "HTTP GET /api/documents/" http http://127.0.0.1:8080/api/documents/ 401
  probe host edge "HTTP GET /healthz" http http://127.0.0.1:8082/healthz
  # ListTenants end to end: edge -> orchestrator -> Postgres (platform tenants).
  probe host "edge -> orchestrator" "HTTP GET /tenants" http http://127.0.0.1:8082/tenants
else
  echo "(skipped: python3 is not installed on this machine)"
fi

echo
if [ "$failed" -gt 0 ]; then
  echo "${RED}$failed of $total checks failed.${RESET} Logs: docker compose -f deploy/compose.yaml logs --tail 50 <service>" >&2
  exit 1
fi
echo "${GREEN}All $total checks passed.${RESET}"
