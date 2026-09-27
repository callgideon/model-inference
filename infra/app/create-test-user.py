#!/usr/bin/env python3
"""Create (or repair) an internal test user with no email-verification step (TEST-USER).

    python3 infra/app/create-test-user.py --email tester@example.com [--json] [--dry-run]
        [--password-env INFRX_TEST_USER_PASSWORD] [--reset-existing]

For internal v1 testing only (operations.md, "Test users without email verification").
Secrets come from the environment, never argv: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and
the password in the variable --password-env names.

  1. GoTrue admin API: create the user already confirmed (`email_confirm: true`) with the
     password. An address that exists is refused (exit 3) unless --reset-existing: then it
     is found, confirmed and given the password instead.
  2. The App's own grant: `claim_signup_grant(p_user_id, 'consumer-v1')` through PostgREST as
     the service role - the call app/(auth)/grant.ts makes after a verified sign-in (0015).
     The database decides: one 10,000 CREDIT grant per individual, a replay answers the
     first. Nothing here writes a table.
  3. The wallet read back through `console_wallet_summary` (0008), as /welcome reads it.

Prints the user id, email, confirmed, grant (granted | already-granted | flag-off |
unavailable) and the available CREDIT as the database's exact decimal string; never the key
or the password. Exit 0 ok (flag-off included), 2 bad input or environment, 3 a refused or
failed call (a held grant included).
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CAMPAIGN = "consumer-v1"        # app/(auth)/flow.ts SIGNUP_CAMPAIGN
TIMEOUT_S = 5
LOCAL = ("localhost", "127.0.0.1", "::1")


class Refused(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):    # never carry the key to another URL
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def call(base: str, key: str, method: str, path: str, body: dict | None = None):
    """(HTTP status, parsed JSON or None). Only the status and a reply's code are ever shown."""
    request = urllib.request.Request(
        base + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with OPENER.open(request, timeout=TIMEOUT_S) as reply:
            status, raw = reply.status, reply.read()
    except urllib.error.HTTPError as reply:
        status, raw = reply.code, reply.read()
    except (OSError, http.client.HTTPException, ValueError) as failed:  # URLError, timeouts,
        # refused connections, a non-HTTP answer, a header value http.client rejects (it quotes it)
        raise Refused(f"{method} {path.split('?')[0]}: {type(failed).__name__}") from None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def why(what: str, status: int, doc) -> Refused:
    code = doc.get("error_code") or doc.get("code") if isinstance(doc, dict) else None
    return Refused(f"{what}: HTTP {status}" + (f" ({code})" if code else ""))


def ensure_user(base: str, key: str, email: str, password: str,
                reset: bool) -> tuple[dict, bool]:
    status, doc = call(base, key, "POST", "/auth/v1/admin/users",
                       {"email": email, "password": password, "email_confirm": True})
    created = status in (200, 201)
    if not created:
        if status not in (409, 422):
            raise why("create user", status, doc)
        refusal = why("create user", status, doc)
        status, found = call(base, key, "GET", "/auth/v1/admin/users?"
                             + urllib.parse.urlencode({"filter": email}))
        if status != 200:
            raise why("find user", status, found)
        # ponytail: GoTrue's filter is a substring match; the exact address is picked here.
        match = [u for u in (found or {}).get("users") or []
                 if str(u.get("email", "")).lower() == email.lower()]
        if len(match) != 1:
            raise refusal                            # e.g. 422 weak_password, not an existing user
        if not reset:                                # a real account is never taken over by default
            raise Refused("address exists; pass --reset-existing to confirm and re-password it")
        status, doc = call(base, key, "PUT", f"/auth/v1/admin/users/{match[0]['id']}",
                           {"email_confirm": True, "password": password})
        if status != 200:
            raise why("confirm user", status, doc)
    if not isinstance(doc, dict) or not doc.get("id") or not doc.get("email_confirmed_at"):
        raise Refused("the auth service did not confirm the user (no email_confirmed_at)")
    return doc, created


def claim(base: str, key: str, uid: str) -> tuple[str, str | None]:
    status, doc = call(base, key, "POST", "/rest/v1/rpc/claim_signup_grant",
                       {"p_user_id": uid, "p_campaign_version": CAMPAIGN})
    if status == 200 and isinstance(doc, list) and len(doc) == 1:
        state = doc[0].get("status")
        if state == "granted":
            return "granted", None
        if state == "replayed":
            return "already-granted", None
        return "unavailable", str(state)            # unverified | identity_reused | rollout_hold | retired
    if isinstance(doc, dict) and doc.get("code") == "55000" \
            and "signup_grant is not enabled" in str(doc.get("message")):
        return "flag-off", None
    return "unavailable", str(why("claim", status, doc))


def wallet(base: str, key: str, uid: str) -> dict:
    status, doc = call(base, key, "POST", "/rest/v1/rpc/console_wallet_summary", {"p_user": uid})
    if not isinstance(doc, list) or len(doc) != 1 or not isinstance(doc[0].get("available"), str):
        raise why("wallet read", status, doc)
    return doc[0]


def show(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result))
    else:
        for name, value in result.items():
            print(f"{name}: {json.dumps(value) if not isinstance(value, str) else value}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--email", required=True)
    ap.add_argument("--password-env", default="INFRX_TEST_USER_PASSWORD", metavar="NAME",
                    help="the environment variable holding the password (never argv)")
    ap.add_argument("--reset-existing", action="store_true",
                    help="an existing address: confirm it and overwrite its password")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; call nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    password = os.environ.get(a.password_env, "")
    url = urllib.parse.urlsplit(base)
    problem = ("--email is not an address" if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a.email)
               else f"{a.password_env} is not set" if not password
               else "SUPABASE_SERVICE_ROLE_KEY is not set" if not key
               else "SUPABASE_SERVICE_ROLE_KEY has characters a header cannot carry"
               if not re.fullmatch(r"[\x21-\x7e]+", key)
               else "SUPABASE_URL is not set" if not base
               else "SUPABASE_URL must be https (plain http only for localhost)"
               if not (url.scheme == "https" or (url.scheme == "http" and url.hostname in LOCAL))
               else None)
    if problem:
        print(f"create-test-user: {problem}", file=sys.stderr)
        return 2
    if a.dry_run:
        show({"supabase_url": base, "email": a.email, "plan": [
            "POST /auth/v1/admin/users email_confirm=true (an existing address: "
            + ("GET ?filter=, PUT email_confirm=true + password)" if a.reset_existing
               else "refused, exit 3)"),
            f"POST /rest/v1/rpc/claim_signup_grant p_campaign_version={CAMPAIGN}",
            "POST /rest/v1/rpc/console_wallet_summary"]}, a.json)
        return 0
    try:
        user, created = ensure_user(base, key, a.email, password, a.reset_existing)
        grant, detail = claim(base, key, user["id"])
        row = wallet(base, key, user["id"])
    except Refused as refused:
        print(f"create-test-user: refused: {refused}", file=sys.stderr)
        return 3
    show({"user_id": user["id"], "email": user.get("email", a.email), "confirmed": True,
          "created": created, "grant": grant, "grant_detail": detail,
          "wallet_id": row.get("wallet_id"), "available": row["available"], "unit": "CREDIT"},
         a.json)
    return 0 if grant != "unavailable" else 3


if __name__ == "__main__":
    sys.exit(main())
