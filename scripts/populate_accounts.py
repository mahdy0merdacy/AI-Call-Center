#!/usr/bin/env python3
"""Portal accounts for the two demo tenants, linked to their call data.

    kubectl -n aicc port-forward svc/mailpit 8025:8025 &
    kubectl -n aicc-data port-forward svc/postgres 55432:5432 &
    .venv/bin/python scripts/populate_accounts.py --password '<a password>'

The call history (data/simulator/history.py) files acme's and swiftnet's calls
under their test-tenant ids, 'acme-delivery' and 'swiftnet-isp'. A portal account
sees its own tenant's UUID. This creates (or finds) a portal account for each,
through the same doors a person uses (scripts/core_e2e.py: sign up, verify the
email from Mailpit, log in), and writes the link into the data stack's Postgres:

    analytics.tenant_links(portal_tenant_id uuid, data_tenant_id text)

Idempotent: an account that already logs in with the password is reused, one
that exists unverified is verified, and each link replaces any older link of the
same portal tenant or data tenant. stockline needs no link: it is a portal
tenant already, and its calls carry its UUID.

The accounts are demo accounts: their credentials are printed at the end. Pass
the same --password (or POPULATE_PASSWORD) on every run; without one a random
password is made, and a second run cannot log in with it.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass

from core_e2e import http  # scripts/ is on sys.path when run as a script

ACCOUNTS = [
    # (company, default email, data tenant id: tenant_id in tenants/*.yaml)
    ("Acme Delivery", "demo@acme-delivery.test", "acme-delivery"),
    ("SwiftNet Broadband", "demo@swiftnet-broadband.test", "swiftnet-isp"),
]

LINKS_DDL = """
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE TABLE IF NOT EXISTS analytics.tenant_links (
    portal_tenant_id uuid PRIMARY KEY,
    data_tenant_id   text NOT NULL UNIQUE
);
"""


@dataclass
class Account:
    company: str
    email: str
    data_tenant_id: str
    tenant_id: str = ""
    slug: str = ""
    created: bool = False


def login(api: str, email: str, password: str) -> str | None:
    st, body = http("POST", f"{api}/auth/login/", {"email": email, "password": password})
    return body.get("access_token") if st == 200 and isinstance(body, dict) else None


def messages_to(mailpit: str, email: str) -> list[dict]:
    """Mailpit's messages to `email`, newest first."""
    _, msgs = http("GET", f"{mailpit}/api/v1/messages?limit=200")
    return [m for m in (msgs.get("messages") if isinstance(msgs, dict) else None) or []
            if any((t.get("Address") or "").lower() == email.lower() for t in m.get("To") or [])]


def verification_link(mailpit: str, email: str, timeout_s: float,
                      seen: set[str]) -> tuple[str, str] | None:
    """(uid, token) from the newest verification email to `email` that arrived
    after `seen` was taken. An older link is spent or stale."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        # Skip the "you already have an account" notices: they carry no link.
        for m in messages_to(mailpit, email):
            if m["ID"] in seen:
                continue
            _, full = http("GET", f"{mailpit}/api/v1/message/{m['ID']}")
            text = (full.get("Text") or "") + (full.get("HTML") or "") if isinstance(full, dict) else ""
            link = re.search(r"verify-email\?uid=([^&\s\"'<]+)&(?:amp;)?token=([^\s\"'<&]+)", text)
            if link:
                return link.group(1), link.group(2)
        time.sleep(1)
    return None


def ensure_account(args, acc: Account, password: str) -> None:
    api = f"{args.web}/api"
    token = login(api, acc.email, password)
    if token is None:
        seen = {m["ID"] for m in messages_to(args.mailpit, acc.email)}
        st, body = http("POST", f"{api}/auth/register/", {
            "company_name": acc.company, "email": acc.email, "password": password,
            "first_name": "Demo", "last_name": acc.company.split()[0]})
        if st != 201:
            raise SystemExit(f"{acc.company}: sign-up refused (HTTP {st}): {body}")
        # Exists but never verified: register sent an "account exists" notice, so
        # ask for a fresh verification link too. Harmless for a new account.
        http("POST", f"{api}/auth/resend-verification/", {"email": acc.email})
        link = verification_link(args.mailpit, acc.email, args.mail_timeout, seen)
        if link is None:
            raise SystemExit(
                f"{acc.company}: no verification email for {acc.email} in Mailpit "
                f"({args.mailpit}). If the account already exists and is verified, its "
                "password is not the one given: pass the right --password.")
        st, body = http("POST", f"{api}/auth/verify-email/", {"uid": link[0], "token": link[1]})
        if st not in (200, 204):
            raise SystemExit(f"{acc.company}: email verification failed (HTTP {st}): {body}")
        token = login(api, acc.email, password)
        if token is None:
            raise SystemExit(f"{acc.company}: cannot log in as {acc.email} after verifying: "
                             "the account existed with another password; pass that --password")
        acc.created = True
    st, tenant = http("GET", f"{api}/tenant/me/", headers={"Authorization": f"Bearer {token}"})
    if st != 200 or not isinstance(tenant, dict) or not tenant.get("id"):
        raise SystemExit(f"{acc.company}: no tenant for {acc.email} (HTTP {st}): {tenant}")
    acc.tenant_id, acc.slug = str(tenant["id"]), tenant.get("slug") or ""


def write_links(dsn: str, accounts: list[Account]) -> None:
    import psycopg

    with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(LINKS_DDL)
        for acc in accounts:
            # One portal tenant per data tenant: drop any older link of either side.
            cur.execute("DELETE FROM analytics.tenant_links "
                        "WHERE portal_tenant_id = %s OR data_tenant_id = %s",
                        (acc.tenant_id, acc.data_tenant_id))
            cur.execute("INSERT INTO analytics.tenant_links (portal_tenant_id, data_tenant_id) "
                        "VALUES (%s, %s)", (acc.tenant_id, acc.data_tenant_id))
        cur.execute("SELECT portal_tenant_id, data_tenant_id FROM analytics.tenant_links "
                    "ORDER BY data_tenant_id")
        rows = cur.fetchall()
    print("\nanalytics.tenant_links:")
    for portal, data in rows:
        print(f"  {portal}  ->  {data}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web", default="http://localhost:30808",
                    help="the portal's web entry (k3s NodePort; :8080 under compose)")
    ap.add_argument("--mailpit", default="http://localhost:8025")
    ap.add_argument("--analytics-dsn", default=os.environ.get(
        "ANALYTICS_DSN", "postgresql://aicc:aicc@localhost:55432/aicc"),
        help="the data stack's Postgres (port-forward svc/postgres in aicc-data)")
    ap.add_argument("--password", default=os.environ.get("POPULATE_PASSWORD", ""),
                    help="for both demo accounts (default: POPULATE_PASSWORD, else random)")
    ap.add_argument("--acme-email", default=ACCOUNTS[0][1])
    ap.add_argument("--swiftnet-email", default=ACCOUNTS[1][1])
    ap.add_argument("--mail-timeout", type=float, default=30.0)
    ap.add_argument("--no-links", action="store_true", help="accounts only, no tenant_links")
    args = ap.parse_args()

    password = args.password or secrets.token_urlsafe(14) + "aA1!"
    emails = [args.acme_email, args.swiftnet_email]
    accounts = [Account(company, email, data) for (company, _, data), email
                in zip(ACCOUNTS, emails, strict=True)]
    for acc in accounts:
        ensure_account(args, acc, password)
        print(f"  {acc.company:<20} {'created' if acc.created else 'exists '}  "
              f"tenant {acc.tenant_id}  slug {acc.slug}")
    if not args.no_links:
        write_links(args.analytics_dsn, accounts)

    print("\ndemo accounts (log in at the portal):")
    for acc in accounts:
        print(f"  {acc.company:<20} {acc.email:<32} password {password}")
    if not args.password:
        print("  (a random password: pass it as --password on the next run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
