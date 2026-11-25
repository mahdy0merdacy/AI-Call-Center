#!/usr/bin/env bash
# Stop the core stack and remove its containers. The data volumes (Postgres,
# MongoDB, uploaded files) are kept unless you ask for them to go.
#
#   scripts/core-down.sh                   # stop; everything you created is kept
#   scripts/core-down.sh --volumes         # ALSO delete all the data (asks first)
#   scripts/core-down.sh --volumes --yes   # ... without asking
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPLOY_DIR=${DEPLOY_DIR:-$ROOT/deploy}

volumes=0
yes=0
for arg in "$@"; do
  case $arg in
    --volumes | -v) volumes=1 ;;
    --yes | -y) yes=1 ;;
    -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

args=(down --remove-orphans)
if [ "$volumes" = 1 ]; then
  cat >&2 <<'EOF'
WARNING: --volumes deletes ALL data of the core stack, for good:
  aicc-core_pgdata      Postgres: accounts, tenants, document chunks (embeddings)
  aicc-core_mongo-data  MongoDB: documents and session links
  aicc-core_media       the uploaded files themselves
The model caches in ~/.cache, deploy/.env and deploy/keys/ are not touched.
EOF
  if [ "$yes" != 1 ]; then
    if [ ! -t 0 ]; then
      echo "Not a terminal, so nothing to confirm with: add --yes." >&2
      exit 1
    fi
    read -r -p "Type 'delete' to delete them: " answer
    if [ "$answer" != delete ]; then
      echo "Nothing deleted."
      exit 1
    fi
  fi
  args+=(--volumes)
fi

if [ -f "$DEPLOY_DIR/.env" ]; then
  docker compose -f "$DEPLOY_DIR/compose.yaml" --env-file "$DEPLOY_DIR/.env" "${args[@]}"
else
  # Without deploy/.env the compose file cannot be read (its secrets are
  # required); compose still finds the project's containers by name.
  docker compose --project-name aicc-core "${args[@]}"
fi
