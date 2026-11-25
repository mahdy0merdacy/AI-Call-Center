"""End-to-end check: tenant-portal auth + documents service, against the running stacks.

Needs both stacks up (see the two READMEs), with the documents service using this
portal's public key (JWT_PUBLIC_KEY):

    cd services/tenant-portal && docker compose up -d --build
    cd services/documents     && docker compose up -d --build
    python services/tenant-portal/tests/e2e/e2e_auth_documents.py

Registers two tenants (email verification read from Mailpit), logs in, calls
/api/auth/me/, then uses the token on the documents service: upload, list, get,
session links, 401 without/with a tampered token, 404 across tenants.
Standard library only. Not collected by pytest (outside src/).
"""

import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PORTAL = "http://127.0.0.1:8000"
DOCS = "http://127.0.0.1:8001"
MAILPIT = "http://127.0.0.1:8025"


def call(method, url, body=None, token=None, raw=None, ctype=None):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data, headers["Content-Type"] = raw, ctype
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text[:200]


def show(label, status, body, keep=("id", "email", "role", "tenant", "tenant_id", "detail",
                                     "original_filename", "ingestion_status", "is_active",
                                     "email_verified", "is_email_verified", "count")):
    if isinstance(body, dict):
        short = {k: v for k, v in body.items() if k in keep}
        if "results" in body:
            short["results"] = len(body["results"])
    elif isinstance(body, list):
        short = f"list of {len(body)}"
    else:
        short = body
    print(f"{label:<58} -> {status}  {json.dumps(short, default=str)[:150]}")


def register_and_login(tag):
    email = f"{tag}-{secrets.token_hex(3)}@clinic.test"
    password = "Vx9!" + secrets.token_urlsafe(12)
    s, b = call("POST", f"{PORTAL}/api/auth/register/", {
        "company_name": f"{tag.title()} Clinic", "email": email, "password": password,
        "first_name": tag.title(), "last_name": "Tester"})
    show(f"[{tag}] POST /api/auth/register/", s, b)

    link = None
    for _ in range(20):
        msgs = json.load(urllib.request.urlopen(f"{MAILPIT}/api/v1/search?query=to:{urllib.parse.quote(email)}"))
        if msgs.get("messages"):
            mid = msgs["messages"][0]["ID"]
            text = json.load(urllib.request.urlopen(f"{MAILPIT}/api/v1/message/{mid}"))["Text"]
            link = re.search(r"https?://\S+", text).group(0)
            break
        time.sleep(0.5)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    print(f"[{tag}] verification email received in Mailpit       -> uid/token found: {bool(q.get('uid') and q.get('token'))}")
    s, b = call("POST", f"{PORTAL}/api/auth/verify-email/", {"uid": q["uid"][0], "token": q["token"][0]})
    show(f"[{tag}] POST /api/auth/verify-email/", s, b)

    s, b = call("POST", f"{PORTAL}/api/auth/login/", {"email": email, "password": password})
    access = (b or {}).get("access") or (b or {}).get("access_token")
    print(f"[{tag}] POST /api/auth/login/{'':<34} -> {s}  access token: {'yes' if access else 'NO'} (keys: {sorted((b or {}).keys())})")
    s, b = call("GET", f"{PORTAL}/api/auth/me/", token=access)
    show(f"[{tag}] GET  /api/auth/me/", s, b)
    return access, b


def multipart(filename, content, ctype="text/plain"):
    boundary = "----e2e" + secrets.token_hex(8)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


print("=== tenant-portal (no migrate was run) ===")
s, b = call("GET", f"{PORTAL}/health/")
show("GET  /health/", s, b)
token_a, me_a = register_and_login("alpha")

print("\n=== documents service, called with the portal's token ===")
s, b = call("GET", f"{DOCS}/health/")
show("GET  /health/ (no token)", s, b)
s, b = call("GET", f"{DOCS}/api/documents/")
show("GET  /api/documents/ without token", s, b)
s, b = call("GET", f"{DOCS}/api/documents/", token=token_a[:-4] + "AAAA")
show("GET  /api/documents/ with tampered token", s, b)

raw, ctype = multipart("opening-hours.txt", b"Open Monday to Friday, 8:00-18:00.\n")
s, doc = call("POST", f"{DOCS}/api/documents/", token=token_a, raw=raw, ctype=ctype)
show("POST /api/documents/ (upload txt)", s, doc)
print(f"{'   document tenant_id == /me tenant id':<58} -> {str((doc or {}).get('tenant_id')) == str((me_a.get('tenant') or {}).get('id') if isinstance(me_a.get('tenant'), dict) else me_a.get('tenant_id') or me_a.get('tenant'))}")
s, b = call("GET", f"{DOCS}/api/documents/", token=token_a)
show("GET  /api/documents/", s, b)
s, b = call("GET", f"{DOCS}/api/documents/{doc['id']}/", token=token_a)
show("GET  /api/documents/<id>/", s, b)
s, link = call("POST", f"{DOCS}/api/session-links/", {}, token=token_a)
show("POST /api/session-links/", s, link)
s, b = call("GET", f"{DOCS}/api/session-links/", token=token_a)
show("GET  /api/session-links/", s, b)

print("\n=== tenant isolation: a second tenant ===")
token_b, _ = register_and_login("beta")
s, b = call("GET", f"{DOCS}/api/documents/{doc['id']}/", token=token_b)
show("[beta] GET alpha's document", s, b)
s, b = call("GET", f"{DOCS}/api/documents/", token=token_b)
show("[beta] GET /api/documents/", s, b)
sys.exit(0)
