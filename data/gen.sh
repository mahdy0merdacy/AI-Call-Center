#!/usr/bin/env bash
# Regenerate code for the event schema. Run from anywhere.
#   data/gen.sh
#
# Writes two copies of the same contract:
#   data/gen/            canonical path + events.desc, for Spark and the data tools
#   libs/aicc_events/    flat events_pb2.py, imported by the services' publishers
# Both encode identical bytes; only the Python import path differs.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-../.venv/bin/python}

rm -rf gen && mkdir -p gen
"$PY" -m grpc_tools.protoc -I proto \
  --python_out=gen --pyi_out=gen \
  aicallcenter/events/v1/events.proto
# Descriptor set: what Schema Registry, Spark's from_protobuf and buf consume.
"$PY" -m grpc_tools.protoc -I proto \
  --include_imports --descriptor_set_out=gen/events.desc \
  aicallcenter/events/v1/events.proto

# The file imports nothing local, so it can be compiled from its own folder and
# land as a flat module the library imports with `from . import events_pb2`.
"$PY" -m grpc_tools.protoc -I proto/aicallcenter/events/v1 \
  --python_out=../libs/aicc_events --pyi_out=../libs/aicc_events \
  events.proto

echo "generated into $(pwd)/gen and $(cd ../libs/aicc_events && pwd)"
