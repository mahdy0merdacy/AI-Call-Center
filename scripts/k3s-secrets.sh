#!/usr/bin/env bash
# Create or update the core stack's Secrets in the aicc namespace, from the same
# files the compose stack uses. Prints names only, never values.
#
#   scripts/k3s-secrets.sh            # show what would be applied (no values)
#   scripts/k3s-secrets.sh --apply    # apply it
#
#   aicc-core      every key of deploy/.env (passwords, Django keys, JWT_PUBLIC_KEY)
#   aicc-jwt-keys  deploy/keys/jwt_private.pem + jwt_public.pem (mounted by the portal)
#   aicc-llm       GROQ_API_KEY (+ LLM_URL / LLM_MODEL / GROQ_MODEL if set) from the root .env
#
# Why a script and not a kustomize secretGenerator: the values must never land in
# git, and `kubectl create secret ... --dry-run=client -o yaml | kubectl apply`
# is the standard idempotent way to sync a Secret from local files.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
NS=aicc
apply=0
[ "${1:-}" = "--apply" ] && apply=1

for f in "$ROOT/deploy/.env" "$ROOT/deploy/keys/jwt_private.pem" "$ROOT/deploy/keys/jwt_public.pem"; do
  [ -s "$f" ] || { echo "missing $f: run scripts/core-setup.sh first" >&2; exit 1; }
done

llm_env=$(mktemp)
trap 'rm -f "$llm_env"' EXIT
grep -E '^(GROQ_API_KEY|GROQ_MODEL|LLM_URL|LLM_MODEL)=.' "$ROOT/.env" >"$llm_env" 2>/dev/null || true

render() {
  # deploy/.env has comment lines; --from-env-file skips them.
  kubectl -n "$NS" create secret generic aicc-core \
    --from-env-file="$ROOT/deploy/.env" --dry-run=client -o yaml
  echo "---"
  kubectl -n "$NS" create secret generic aicc-jwt-keys \
    --from-file=jwt_private.pem="$ROOT/deploy/keys/jwt_private.pem" \
    --from-file=jwt_public.pem="$ROOT/deploy/keys/jwt_public.pem" --dry-run=client -o yaml
  echo "---"
  kubectl -n "$NS" create secret generic aicc-llm \
    --from-env-file="$llm_env" --dry-run=client -o yaml
}

if [ "$apply" = 1 ]; then
  render | kubectl apply -f -
else
  echo "Would apply (keys only; values are never shown):"
  # kubectl prints each Secret's key names; the template never touches values.
  render | kubectl apply --dry-run=client -f - -o go-template='{{range .items}}  {{.metadata.name}}:{{range $k, $v := .data}} {{$k}}{{end}}{{"\n"}}{{end}}'
  echo "Run with --apply to create them in namespace $NS."
fi
