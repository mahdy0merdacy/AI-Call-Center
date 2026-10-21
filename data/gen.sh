#!/usr/bin/env bash
# Regenerate Python code for the event schema. Run from anywhere.
#   data/gen.sh
set -euo pipefail
cd "$(dirname "$0")"
rm -rf gen && mkdir -p gen
../.venv/bin/python -m grpc_tools.protoc -I proto \
  --python_out=gen --pyi_out=gen \
  aicallcenter/events/v1/events.proto
# Descriptor set: what Schema Registry, Spark's from_protobuf and buf consume.
../.venv/bin/python -m grpc_tools.protoc -I proto \
  --include_imports --descriptor_set_out=gen/events.desc \
  aicallcenter/events/v1/events.proto
echo "generated into $(pwd)/gen"
