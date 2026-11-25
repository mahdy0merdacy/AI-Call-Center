#!/usr/bin/env bash
# Demo: the Schema Registry accepts a safe change and rejects a breaking one.
# Check-only (the /compatibility endpoint) — nothing is ever registered.
#
#   data/kafka/demo_compat.sh
set -euo pipefail

REGISTRY=${REGISTRY:-http://localhost:8085}
SUBJECT=calls.events.v1-value

echo "Compatibility mode: $(curl -s "$REGISTRY/config/$SUBJECT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["compatibilityLevel"])')"
echo

check() {   # $1 = label, $2 = python that rewrites the schema string `s`
  curl -s "$REGISTRY/subjects/$SUBJECT/versions/latest" | python3 -c "
import json, sys
d = json.load(sys.stdin)
s = d['schema']
$2
print(json.dumps({'schemaType': 'PROTOBUF', 'schema': s, 'references': d.get('references') or []}))
" | curl -s -X POST -H "Content-Type: application/vnd.schemaregistry.v1+json" --data @- \
      "$REGISTRY/compatibility/subjects/$SUBJECT/versions/latest?verbose=true" \
    | LABEL="$1" python3 -c "
import json, os, re, sys
r = json.load(sys.stdin)
ok = r.get('is_compatible')
print('  ' + os.environ['LABEL'])
print('    -> ' + ('ACCEPTED' if ok else 'REJECTED'))
for m in r.get('messages', []):
    t = re.search(r'errorType:\"(\w+)\"', m)
    if t: print('       ', t.group(1))
"
}

echo "1) Safe: add a new optional field with a NEW tag number"
check "CallEnded gets 'string agent_notes = 4'" \
  "s = s.replace('message CallEnded {', 'message CallEnded {\n  string agent_notes = 4;', 1)"
echo
echo "2) Breaking: change an existing field's type, same tag number"
check "Event.event_id: string -> bool" \
  "s = s.replace('string event_id = 1', 'bool event_id = 1', 1)"
echo
echo "3) Breaking: reuse a tag number for a different field"
check "CallEnded.duration_ms (tag 2) renamed and retyped" \
  "s = s.replace('uint32 duration_ms = 2;', 'string ended_by = 2;', 1)"
