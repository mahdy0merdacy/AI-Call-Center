#!/usr/bin/env bash
# Regenerate Python code for the orchestrator contract. Run from anywhere.
#   services/orchestrator/gen.sh
#
# Writes the orchestrator's own copy and the media edge's (the client side of the
# same stream). CI runs this and fails if the result differs from what is committed.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-../../.venv/bin/python}

for out in gen ../media-edge/gen; do
  "$PY" -m grpc_tools.protoc -I proto \
    --python_out="$out" --pyi_out="$out" --grpc_python_out="$out" \
    orchestrator.proto
  # protoc emits a top-level import; gen/ is a package, so make it relative.
  sed -i 's/^import orchestrator_pb2 as/from . import orchestrator_pb2 as/' \
    "$out/orchestrator_pb2_grpc.py"
done
echo "generated into $(pwd)/gen and $(cd ../media-edge && pwd)/gen"
