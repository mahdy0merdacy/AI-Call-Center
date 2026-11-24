#!/usr/bin/env bash
# Everything after `docker compose up`, for whichever services are running.
# Idempotent: safe to run after every start.
#   data/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }

echo "== topics";  data/kafka/topics.sh | grep -E "^(created|updated)"
echo "== schemas"; .venv/bin/python data/kafka/register_schemas.py

if running seaweedfs; then
  echo "== lake bucket"
  if docker exec seaweedfs sh -c 'echo "s3.bucket.list" | weed shell -master=seaweedfs:9333' 2>/dev/null | grep -qw lake; then
    echo "  bucket lake exists"
  else
    docker exec seaweedfs sh -c 'echo "s3.bucket.create -name lake" | weed shell -master=seaweedfs:9333' >/dev/null
    echo "  created bucket lake"
  fi
fi

if running connect; then
  echo "== CDC connector"; data/connect/register.sh
fi
