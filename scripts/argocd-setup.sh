#!/usr/bin/env bash
# Install or upgrade Argo CD on the cluster and give it read access to the repo.
# Idempotent: run it again after changing deploy/argocd/values.yaml.
#
#   scripts/argocd-setup.sh
#
# Needs, in the root .env (gitignored), a GitLab deploy token with only
# read_repository:
#   ARGOCD_GIT_USERNAME=gitlab+deploy-token-...
#   ARGOCD_GIT_TOKEN=gldt-...
# Prints names only, never the token.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CHART_VERSION=10.18.0          # Argo CD v3.6.1
REPO_URL=https://gitlab.com/student-group213543/ai-call-center.git

user=$(grep -E '^ARGOCD_GIT_USERNAME=' "$ROOT/.env" | cut -d= -f2- || true)
token=$(grep -E '^ARGOCD_GIT_TOKEN=' "$ROOT/.env" | cut -d= -f2- || true)
[ -n "$user" ] && [ -n "$token" ] || { echo "ARGOCD_GIT_USERNAME / ARGOCD_GIT_TOKEN missing from $ROOT/.env" >&2; exit 1; }

helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update argo >/dev/null
helm upgrade --install argocd argo/argo-cd --version "$CHART_VERSION" \
  --namespace argocd --create-namespace \
  --values "$ROOT/deploy/argocd/values.yaml" --wait --timeout 10m

# A Secret labelled as a repository is how Argo CD learns credentials for a repo.
kubectl -n argocd create secret generic repo-aicc \
  --from-literal=type=git --from-literal=url="$REPO_URL" \
  --from-literal=username="$user" --from-literal=password="$token" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - argocd.argoproj.io/secret-type=repository -o yaml \
  | kubectl apply -f -

echo "Argo CD ready: http://<any node IP>:30880 (user admin)"
echo "Next: kubectl apply -f deploy/argocd/applications.yaml"
