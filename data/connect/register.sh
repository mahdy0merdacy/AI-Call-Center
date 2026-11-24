#!/usr/bin/env bash
# Create or update the Postgres CDC connector. Idempotent (PUT = upsert).
#   data/connect/register.sh
set -euo pipefail
cd "$(dirname "$0")"
CONNECT=${CONNECT:-http://localhost:8084}
NAME=$(python3 -c "import json; print(json.load(open('postgres-cdc.json'))['name'])")
python3 -c "import json; print(json.dumps(json.load(open('postgres-cdc.json'))['config']))" \
  | curl -sf -X PUT -H "Content-Type: application/json" --data @- \
      "$CONNECT/connectors/$NAME/config" > /dev/null
for _ in $(seq 1 30); do
  state=$(curl -sf "$CONNECT/connectors/$NAME/status" \
    | python3 -c "import json,sys; s=json.load(sys.stdin); print(s['connector']['state'], *[t['state'] for t in s['tasks']])" 2>/dev/null || true)
  [[ "$state" == "RUNNING RUNNING"* ]] && break
  sleep 2
done
echo "connector $NAME: ${state:-unknown}"
