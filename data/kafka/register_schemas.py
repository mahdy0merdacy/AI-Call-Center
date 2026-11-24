"""Register events.proto with the Schema Registry, one subject per topic.

    .venv/bin/python data/kafka/register_schemas.py           # register
    .venv/bin/python data/kafka/register_schemas.py --check   # dry run: would it be accepted?

Subjects follow the TopicNameStrategy: `<topic>-value`. Three topics share the
`Event` schema, so the same text is registered under three subjects; the
registry dedupes identical schemas and hands back ONE id. That id is what each
record carries in its 5-byte header (see smoke.py).

`--check` is the evolution gate (DDIA.md §3): edit the .proto, run --check, and
the registry says whether every previous version can still read new records and
vice versa (FULL_TRANSITIVE). Run it before deploying a producer.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "proto/aicallcenter/events/v1/events.proto"
SUBJECTS = ["calls.events.v1-value", "calls.partials.v1-value", "dlq.calls.events.v1-value"]
COMPAT = "FULL_TRANSITIVE"


def call(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        # 404 on a compatibility check = subject has no versions yet.
        return {"_status": e.code, **json.loads(e.read() or b"{}")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="http://localhost:8085")
    ap.add_argument("--check", action="store_true",
                    help="only test compatibility, register nothing")
    args = ap.parse_args()

    schema = {"schemaType": "PROTOBUF", "schema": PROTO.read_text()}
    ok = True

    for subject in SUBJECTS:
        if args.check:
            r = call("POST", f"{args.registry}/compatibility/subjects/{subject}"
                             "/versions?verbose=true", schema)
            if r.get("_status") == 404:
                print(f"  new      {subject} (no versions yet: anything is compatible)")
            elif r.get("is_compatible"):
                print(f"  ok       {subject}")
            else:
                ok = False
                print(f"  BREAKING {subject}")
                for msg in r.get("messages", [r]):
                    print(f"           {msg}")
            continue

        # Pin the level per subject too, so a change to the global default
        # cannot quietly loosen it.
        call("PUT", f"{args.registry}/config/{subject}", {"compatibility": COMPAT})
        r = call("POST", f"{args.registry}/subjects/{subject}/versions", schema)
        if "id" in r:
            v = call("GET", f"{args.registry}/subjects/{subject}/versions/latest")
            print(f"  registered {subject}: schema id {r['id']}, version {v.get('version')}")
        else:
            ok = False
            print(f"  REJECTED   {subject}: {r}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
