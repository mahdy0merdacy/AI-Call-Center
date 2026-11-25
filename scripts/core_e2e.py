#!/usr/bin/env python3
"""End-to-end check of the core stack, from a new client's sign-up to a call that
answers from the document they uploaded.

    .venv/bin/python scripts/core_e2e.py            # against the running core stack

Every step goes through the same doors a person would use, so a pass means the
containers really talk to each other:

  1. sign up                  browser -> web (nginx) -> portal -> postgres, mailpit
  2. verify the email         link read from Mailpit's API, posted back through web
  3. log in                   portal signs an RS256 JWT
  4. upload a document        web -> documents (verifies the JWT) -> mongo, media volume
  5. wait for ingestion       ingestion -> mongo, media volume, knowledge (embeds), postgres
  6. client is listed         edge /tenants -> orchestrator -> postgres (voice_reader)
  7. call the new client      edge container -> orchestrator -> knowledge -> postgres, LLM
  8. isolation                a second client (no documents) asked the same question, and
                              a search under its id, must not see client A's document

Step 7 runs inside the edge container, on the compose network, speaking the same
gRPC stream the media edge uses; only the audio legs (STT/TTS) are skipped, which
scripts/webrtc_smoke.py covers.

Exit code 0 only if every step passed. Secrets are never printed: the throwaway
account's password is random and only used here.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "compose.yaml")]

# Facts that exist nowhere but in the uploaded document, so an answer containing
# them can only have come from retrieval.
FACTS = """Pistachio Logistics — customer handbook

Warranty
Every Pistachio cargo bike comes with a warranty of 27 months from the delivery date.
The warranty covers the frame, the motor and the battery.

Opening hours
Our workshop in Lyon is open Monday to Friday, from 08:15 to 17:45.
On Saturdays the workshop opens from 09:00 to 12:30. It is closed on Sundays.

Office mascot
The office cat is called Mirabelle and she sleeps on the returns shelf.
"""
QUESTION = "How long is the warranty on your cargo bikes?"
EXPECT = re.compile(r"\b(27|twenty[- ]seven)\b", re.I)


class Step:
    def __init__(self):
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""),
              flush=True)
        return ok


def http(method: str, url: str, body=None, headers=None, timeout=30):
    """(status, parsed JSON or text). Never raises on HTTP errors."""
    data = None
    hdrs = dict(headers or {})
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif isinstance(body, bytes):
        data = body
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw.decode("utf-8", "replace")


def multipart(field: str, filename: str, content: bytes, ctype: str) -> tuple[bytes, str]:
    boundary = "----aicc" + secrets.token_hex(12)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def compose_python(service: str, code: str, *args: str, timeout=120) -> subprocess.CompletedProcess:
    """Run a Python snippet INSIDE a compose service: its network, its DNS names."""
    return subprocess.run([*COMPOSE, "exec", "-T", service, "python", "-", *args],
                          input=code, capture_output=True, text=True, timeout=timeout)


# Runs in the edge container: a call on the orchestrator's gRPC stream, text only.
CALL_SNIPPET = r'''
import asyncio, json, sys, time
import grpc
from gen import orchestrator_pb2 as ob, orchestrator_pb2_grpc as og

slug, question, target = sys.argv[1], sys.argv[2], sys.argv[3]

async def main():
    q = asyncio.Queue()
    out = {"ready": None, "greeting": [], "answer": [], "turns": [], "error": None}
    state = {"phase": "greeting", "last": time.monotonic()}

    async def events():
        yield ob.CallEvent(start=ob.StartCall(
            tenant_slug=slug, call_id=f"e2e-{int(time.time()*1000)}", synthetic=True))
        while True:
            e = await q.get()
            if e is None:
                yield ob.CallEvent(end=ob.EndCall(reason="e2e done"))
                return
            yield e

    async with grpc.aio.insecure_channel(target) as ch:
        call = og.OrchestratorStub(ch).Session(events())

        async def reader():
            try:
                async for d in call:
                    state["last"] = time.monotonic()
                    k = d.WhichOneof("directive")
                    if k == "ready":
                        r = d.ready
                        out["ready"] = {"tenant_id": r.tenant_id, "name": r.display_name,
                                        "locale": r.locale, "voice": r.voice}
                    elif k == "say":
                        out[state["phase"]].append(d.say.text)
                    elif k == "turn":
                        out["turns"].append({"kind": d.turn.kind, "intent": d.turn.intent,
                                             "tool": d.turn.tool, "outcome": d.turn.outcome})
                    elif k == "hangup":
                        return
            except grpc.aio.AioRpcError as e:
                out["error"] = f"{e.code().name}: {e.details()}"

        task = asyncio.create_task(reader())
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20 and not out["error"]:
            if out["ready"] and out["greeting"] and time.monotonic() - state["last"] > 2.0:
                break
            await asyncio.sleep(0.1)
        if not out["error"]:
            state["phase"] = "answer"
            await q.put(ob.CallEvent(transcript=ob.Transcript(text=question, is_final=True)))
            t1 = time.monotonic()
            state["last"] = t1
            while time.monotonic() - t1 < 40 and not out["error"]:
                if out["answer"] and time.monotonic() - state["last"] > 3.0:
                    break
                await asyncio.sleep(0.1)
        await q.put(None)
        try:
            await asyncio.wait_for(task, 5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
    print(json.dumps(out))

asyncio.run(main())
'''

# Runs in the orchestrator container: a search under a tenant id that owns nothing.
SEARCH_SNIPPET = r'''
import json, sys, urllib.request
body = json.dumps({"tenant_id": sys.argv[1], "query": sys.argv[2], "k": 4}).encode()
req = urllib.request.Request("http://knowledge:8100/v1/search", data=body,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as r:
    print(r.read().decode())
'''


def call(slug: str, question: str) -> dict:
    p = compose_python("edge", CALL_SNIPPET, slug, question, "orchestrator:50052", timeout=120)
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout).strip()[-400:]}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": p.stdout[-400:]}


def onboard(s: Step, args, company: str, email: str, password: str, tag: str = ""):
    """Sign up, verify through Mailpit, log in. (token, slug, tenant_id) or None."""
    api = f"{args.web}/api"
    st, body = http("POST", f"{api}/auth/register/", {
        "company_name": company, "email": email, "password": password,
        "first_name": "Ada", "last_name": "Pistachio"})
    if not s.record(f"{tag}sign up", st == 201, f"HTTP {st}"):
        print(body)
        return None

    link = None
    for _ in range(30):
        st, msgs = http("GET", f"{args.mailpit}/api/v1/messages?limit=50")
        for m in (msgs.get("messages") if isinstance(msgs, dict) else None) or []:
            if any(t.get("Address") == email for t in m.get("To") or []):
                _, full = http("GET", f"{args.mailpit}/api/v1/message/{m['ID']}")
                text = (full.get("Text") or "") + (full.get("HTML") or "")
                link = re.search(r"verify-email\?uid=([^&\s\"'<]+)&(?:amp;)?token=([^\s\"'<&]+)",
                                 text)
                break
        if link:
            break
        time.sleep(1)
    if not s.record(f"{tag}verification email received", bool(link)):
        return None
    st, body = http("POST", f"{api}/auth/verify-email/",
                    {"uid": link.group(1), "token": link.group(2)})
    if not s.record(f"{tag}email verified", st in (200, 204), f"HTTP {st}"):
        print(body)
        return None

    st, body = http("POST", f"{api}/auth/login/", {"email": email, "password": password})
    token = body.get("access_token") if isinstance(body, dict) else None
    if not s.record(f"{tag}log in (JWT issued)", st == 200 and bool(token), f"HTTP {st}"):
        print(body)
        return None
    st, tenant = http("GET", f"{api}/tenant/me/", headers={"Authorization": f"Bearer {token}"})
    slug = tenant.get("slug") if isinstance(tenant, dict) else None
    tenant_id = tenant.get("id") if isinstance(tenant, dict) else None
    if not s.record(f"{tag}tenant created", st == 200 and bool(slug), f"slug {slug}"):
        return None
    return token, slug, tenant_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--web", default="http://localhost:8080")
    ap.add_argument("--edge", default="http://localhost:8082")
    ap.add_argument("--mailpit", default="http://localhost:8025")
    ap.add_argument("--ingest-timeout", type=float, default=600,
                    help="seconds to wait for the document to be ingested")
    args = ap.parse_args()

    s = Step()
    run = uuid.uuid4().hex[:8]
    company = f"Pistachio Logistics {run}"
    email = f"owner-{run}@pistachio.test"
    password = secrets.token_urlsafe(18) + "aA1!"
    api = f"{args.web}/api"

    print(f"core e2e — company {company!r}, {email}")

    # 1-3. sign up, verify the email, log in
    got = onboard(s, args, company, email, password)
    if got is None:
        return 1
    token, slug, tenant_id = got
    auth = {"Authorization": f"Bearer {token}"}

    # 4. upload
    payload, ctype = multipart("file", "pistachio-handbook.txt", FACTS.encode(), "text/plain")
    st, doc = http("POST", f"{api}/documents/", payload,
                   headers={**auth, "Content-Type": ctype}, timeout=60)
    doc_id = doc.get("id") if isinstance(doc, dict) else None
    if not s.record("document uploaded", st == 201 and bool(doc_id), f"HTTP {st}"):
        print(doc)
        return 1

    # 5. ingestion
    status, t0 = None, time.monotonic()
    while time.monotonic() - t0 < args.ingest_timeout:
        _, d = http("GET", f"{api}/documents/{doc_id}/", headers=auth)
        status = d.get("ingestion_status") if isinstance(d, dict) else None
        if status in ("completed", "failed"):
            break
        time.sleep(3)
    if not s.record("document ingested", status == "completed",
                    f"{status} after {time.monotonic() - t0:.0f}s"):
        return 1

    # 6. listed for the test page
    st, listing = http("GET", f"{args.edge}/tenants")
    entry = next((t for t in (listing.get("tenants") if isinstance(listing, dict) else None)
                  or [] if t.get("slug") == slug), None)
    s.record("client listed on the test page", bool(entry) and entry.get("source") == "platform"
             and entry.get("documents", 0) >= 1, json.dumps(entry))

    # 7. the call answers from the document
    res = call(slug, QUESTION)
    answer = " ".join(res.get("answer") or [])
    s.record("call: greeting from the new client",
             bool(res.get("greeting")) and not res.get("error"),
             (res.get("error") or " ".join(res.get("greeting") or []))[:160])
    s.record("call: answer comes from the uploaded document", bool(EXPECT.search(answer)),
             answer[:200] or res.get("error", ""))

    # 8. isolation: a second client, with no documents, asked the same question
    other_slug, other_id = None, None
    got = onboard(s, args, f"Quiet Company {run}", f"owner-{run}@quiet.test", password, "client B: ")
    if got:
        _, other_slug, other_id = got
        other = call(other_slug, QUESTION)
        other_answer = " ".join(other.get("answer") or [])
        s.record("isolation: client B's call does not see client A's document",
                 bool(other_answer) and not other.get("error")
                 and not EXPECT.search(other_answer) and "Mirabelle" not in other_answer,
                 other_answer[:160] or other.get("error", ""))
    p = compose_python("orchestrator", SEARCH_SNIPPET, other_id or str(uuid.uuid4()), QUESTION,
                       timeout=30)
    try:
        chunks = json.loads(p.stdout)["chunks"]
        s.record("isolation: search under client B's id returns nothing", chunks == [],
                 f"{len(chunks)} chunk(s)")
    except (json.JSONDecodeError, KeyError):
        s.record("isolation: search under client B's id returns nothing", False,
                 (p.stderr or p.stdout)[-200:])
    if tenant_id:
        p = compose_python("orchestrator", SEARCH_SNIPPET, tenant_id, QUESTION, timeout=30)
        try:
            chunks = json.loads(p.stdout)["chunks"]
            s.record("retrieval under the client's own id finds the warranty", any(
                EXPECT.search(c.get("content", "")) for c in chunks), f"{len(chunks)} chunk(s)")
        except (json.JSONDecodeError, KeyError):
            s.record("retrieval under the client's own id finds the warranty", False,
                     (p.stderr or p.stdout)[-200:])

    failed = [n for n, ok, _ in s.results if not ok]
    print(f"\n{len(s.results) - len(failed)}/{len(s.results)} passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    print(f"try it yourself: {args.edge}/w/{slug}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
