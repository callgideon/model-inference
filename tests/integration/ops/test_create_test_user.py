"""TEST-USER: infra/app/create-test-user.py - an internal test user with no email step.

Layer 1: a local fake GoTrue admin API + PostgREST (http.server in a thread) records every
request; the tool runs as the operator runs it (a subprocess, secrets in the environment).
Layer 2 (tu11): the real grant function (0015 `claim_signup_grant`) and wallet read (0008
`console_wallet_summary`) through the pinned PostgREST on the task-local app-c0 stack
(INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase, 55451); GoTrue is not in that stack, so the fake
answers /auth/v1 and forwards /rest/v1 to the real PostgREST. Skips visibly without it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "infra" / "app" / "create-test-user.py"
# Placeholders only: nothing here is a real secret.
KEY = "placeholder-service-role-key-0123456789"
PASSWORD = "placeholder-test-password-9876"
EMAIL = "tester@example.com"
GRANT = "10000.00000000"


class Fake:
    """GoTrue's admin users API and the two PostgREST RPCs, in memory."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.users: dict[str, dict] = {}       # id -> {id, email, email_confirmed_at, password}
        self.granted: set[str] = set()
        self.flag = True
        self.auth_status: int | None = None     # every /auth/v1 call answers this, when set
        self.confirms = True                    # False: GoTrue ignores email_confirm
        self.redirect = False                   # the create answers 302 to /leak
        self.stall_s = 0.0                      # the create answers only after this long
        self.wallet_status = 200                # console_wallet_summary's HTTP status
        self.claim_status: str | None = None    # a canned claim answer (e.g. identity_reused)
        self.upstream: str | None = None        # forward /rest/v1 here (tu11)

    def add(self, email: str, confirmed: bool = False) -> str:
        uid = str(uuid.uuid4())
        self.users[uid] = {"id": uid, "email": email, "password": "old",
                           "email_confirmed_at": "2026-09-27T00:00:00Z" if confirmed else None}
        return uid

    def paths(self) -> list[str]:
        return [f"{r['method']} {r['path']}" for r in self.requests]

    def answer(self, method: str, path: str, body):
        url = urllib.parse.urlsplit(path)
        if url.path.startswith("/auth/v1/"):
            if self.auth_status is not None:
                # A hostile reply that quotes the token back: the tool must never echo a body.
                return self.auth_status, {"code": self.auth_status, "error_code": "bad_jwt",
                                          "msg": f"invalid JWT {KEY} {PASSWORD}"}
            if url.path == "/auth/v1/admin/users" and method == "POST":
                if self.redirect:
                    return 302, None
                time.sleep(self.stall_s)
                if any(u["email"].lower() == body["email"].lower() for u in self.users.values()):
                    return 422, {"code": 422, "error_code": "email_exists"}
                uid = self.add(body["email"])
                self.users[uid].update(password=body["password"])
                if body.get("email_confirm") is True and self.confirms:
                    self.users[uid]["email_confirmed_at"] = "2026-09-27T00:00:01Z"
                return 200, self.public(uid)
            if url.path == "/auth/v1/admin/users" and method == "GET":
                needle = urllib.parse.parse_qs(url.query).get("filter", [""])[0].lower()
                return 200, {"users": [self.public(u) for u, row in self.users.items()
                                       if needle in row["email"].lower()]}
            uid = url.path.rsplit("/", 1)[-1]
            if method == "PUT" and uid in self.users:
                if body.get("email_confirm") is True and self.confirms:
                    self.users[uid]["email_confirmed_at"] = "2026-09-27T00:00:02Z"
                if "password" in body:
                    self.users[uid]["password"] = body["password"]
                return 200, self.public(uid)
            return 404, {"code": 404}
        if url.path == "/rest/v1/rpc/claim_signup_grant":
            if not self.flag:
                return 400, {"code": "55000", "message": "maintenance: signup_grant is not enabled"}
            uid = body["p_user_id"]
            if self.claim_status:
                return 200, [{"status": self.claim_status, "user_id": uid}]
            if not (self.users.get(uid) or {}).get("email_confirmed_at"):
                return 200, [{"status": "unverified", "user_id": uid}]
            replay = uid in self.granted
            self.granted.add(uid)
            return 200, [{"status": "replayed" if replay else "granted", "user_id": uid,
                          "wallet_id": f"w-{uid}", "amount": GRANT,
                          "granted_at": "2026-09-27T00:00:03Z"}]
        if url.path == "/rest/v1/rpc/console_wallet_summary" and self.wallet_status != 200:
            return self.wallet_status, {"code": "XX000", "message": f"boom {KEY}"}
        if url.path == "/rest/v1/rpc/console_wallet_summary":
            uid = body["p_user"]
            granted = uid in self.granted
            return 200, [{"user_id": uid, "wallet_id": f"w-{uid}" if granted else None,
                          "kind": "consumer", "unit": "CREDIT",
                          "available": GRANT if granted else "0.00000000"}]
        return 404, {"code": "PGRST202"}

    def public(self, uid: str) -> dict:
        return {k: v for k, v in self.users[uid].items() if k != "password"}


def serve(fake: Fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_one(self, method: str) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.loads(raw) if raw else None
            fake.requests.append({"method": method, "path": self.path, "body": body,
                                  "apikey": self.headers.get("apikey"),
                                  "authorization": self.headers.get("Authorization")})
            if fake.upstream and self.path.startswith("/rest/v1/"):
                forward = urllib.request.Request(
                    fake.upstream + self.path[len("/rest/v1"):], data=raw or None, method=method,
                    headers={"Authorization": self.headers.get("Authorization") or "",
                             "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(forward, timeout=10) as reply:
                        status, out = reply.status, reply.read()
                except urllib.error.HTTPError as reply:
                    status, out = reply.code, reply.read()
            else:
                status, doc = fake.answer(method, self.path, body)
                out = b"" if doc is None else json.dumps(doc).encode()
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/leak")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            self.handle_one("GET")

        def do_POST(self):
            self.handle_one("POST")

        def do_PUT(self):
            self.handle_one("PUT")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def fake():
    state = Fake()
    server = serve(state)
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()
    server.server_close()


def run(fake: Fake | None, *args: str, env: dict | None = None, key: str = KEY):
    base = {"PATH": os.environ.get("PATH", ""), "SUPABASE_SERVICE_ROLE_KEY": key,
            "INFRX_TEST_USER_PASSWORD": PASSWORD,
            "SUPABASE_URL": fake.url if fake else "http://127.0.0.1:9"}
    done = subprocess.run([sys.executable, str(TOOL), *args],
                          env={**base, **(env or {})}, capture_output=True, text=True, timeout=60)
    return done.returncode, done.stdout, done.stderr


def assert_no_secret(*texts: str, key: str = KEY) -> None:
    for text in texts:
        assert key not in text and PASSWORD not in text


def test_tu01_a_new_user_is_created_confirmed_and_granted_once(fake):
    code, out, err = run(fake, "--email", EMAIL, "--json")
    assert code == 0, err
    doc = json.loads(out)
    uid = doc["user_id"]
    assert fake.paths() == ["POST /auth/v1/admin/users", "POST /rest/v1/rpc/claim_signup_grant",
                            "POST /rest/v1/rpc/console_wallet_summary"]
    create, claim, summary = fake.requests
    assert create["body"] == {"email": EMAIL, "password": PASSWORD, "email_confirm": True}
    # The App's call: grant.ts claimArgs(userId) with flow.ts SIGNUP_CAMPAIGN.
    assert claim["body"] == {"p_user_id": uid, "p_campaign_version": "consumer-v1"}
    assert summary["body"] == {"p_user": uid}
    assert all(r["apikey"] == KEY and r["authorization"] == f"Bearer {KEY}" for r in fake.requests)
    assert doc == {"user_id": uid, "email": EMAIL, "confirmed": True, "created": True,
                   "grant": "granted", "grant_detail": None, "wallet_id": f"w-{uid}",
                   "available": GRANT, "unit": "CREDIT"}
    assert fake.users[uid]["email_confirmed_at"] and fake.granted == {uid}


def test_tu02_an_existing_user_is_confirmed_and_repassworded_and_the_grant_is_idempotent(fake):
    fake.add("x" + EMAIL)                       # a filter match that is another address
    uid = fake.add(EMAIL.upper())               # GoTrue may hold the address in another case
    code, out, err = run(fake, "--email", EMAIL, "--json")
    assert code == 0, err
    assert fake.paths()[:3] == ["POST /auth/v1/admin/users",
                                f"GET /auth/v1/admin/users?filter={urllib.parse.quote(EMAIL)}",
                                f"PUT /auth/v1/admin/users/{uid}"]
    assert fake.requests[2]["body"] == {"email_confirm": True, "password": PASSWORD}
    assert fake.users[uid]["password"] == PASSWORD and fake.users[uid]["email_confirmed_at"]
    first = json.loads(out)
    assert (first["user_id"], first["created"], first["grant"]) == (uid, False, "granted")
    code, out, err = run(fake, "--email", EMAIL, "--json")
    again = json.loads(out)
    assert code == 0, err
    assert (again["grant"], again["available"]) == ("already-granted", GRANT)
    claims = [r for r in fake.requests if r["path"].endswith("/claim_signup_grant")]
    assert len(claims) == 2 and fake.granted == {uid}


def test_tu03_the_grant_flag_off_is_reported_and_is_not_a_failure(fake):
    fake.flag = False
    code, out, err = run(fake, "--email", EMAIL, "--json")
    doc = json.loads(out)
    assert code == 0, err
    assert (doc["grant"], doc["wallet_id"], doc["available"]) == ("flag-off", None, "0.00000000")


def test_tu04_an_unauthorized_key_stops_at_the_first_call(fake):
    fake.auth_status = 401
    code, out, err = run(fake, "--email", EMAIL, "--json")
    assert code == 3 and out == ""
    assert fake.paths() == ["POST /auth/v1/admin/users"]
    assert "401" in err and "bad_jwt" in err
    assert_no_secret(out, err)


def test_tu05_dry_run_calls_nothing(fake):
    code, out, err = run(fake, "--email", EMAIL, "--dry-run")
    assert code == 0, err
    assert fake.requests == []
    assert "claim_signup_grant" in out and fake.url in out
    assert_no_secret(out, err)


def test_tu06_secrets_never_reach_the_output(fake):
    fake.add(EMAIL)
    for args in (("--json",), (), ("--dry-run", "--json")):
        code, out, err = run(fake, "--email", EMAIL, *args)
        assert code == 0, err
        assert_no_secret(out, err)
    fake.claim_status = "identity_reused"
    assert_no_secret(*run(fake, "--email", EMAIL)[1:])


@pytest.mark.parametrize("args, env, why", [
    (("--email", "not-an-address"), {}, "email"),
    (("--email", EMAIL), {"INFRX_TEST_USER_PASSWORD": ""}, "INFRX_TEST_USER_PASSWORD"),
    (("--email", EMAIL, "--password-env", "OTHER_PW"), {}, "OTHER_PW"),
    (("--email", EMAIL), {"SUPABASE_SERVICE_ROLE_KEY": ""}, "SUPABASE_SERVICE_ROLE_KEY"),
    (("--email", EMAIL), {"SUPABASE_URL": ""}, "SUPABASE_URL"),
    (("--email", EMAIL), {"SUPABASE_URL": "http://project.invalid"}, "https"),
], ids=["email", "password", "password-env", "key", "url", "plain-http-remote"])
def test_tu07_bad_input_exits_2_and_calls_nothing(fake, args, env, why):
    code, out, err = run(fake, *args, env=env)
    assert code == 2 and why in err
    assert fake.requests == []
    assert_no_secret(out, err)


def test_tu08_a_redirect_is_never_followed_with_the_key(fake):
    fake.redirect = True
    code, _, err = run(fake, "--email", EMAIL)
    assert code == 3 and "302" in err
    assert fake.paths() == ["POST /auth/v1/admin/users"]


def test_tu09_an_unconfirmed_user_is_refused_before_the_grant(fake):
    fake.confirms = False
    code, out, err = run(fake, "--email", EMAIL)
    assert code == 3 and "confirm" in err and out == ""
    assert not any("rpc" in p for p in fake.paths())


def test_tu10_a_held_grant_is_unavailable_and_exits_3(fake):
    fake.claim_status = "identity_reused"
    code, out, err = run(fake, "--email", EMAIL, "--json")
    doc = json.loads(out)
    assert code == 3, err
    assert (doc["grant"], doc["grant_detail"]) == ("unavailable", "identity_reused")


def test_tu12_every_call_gives_up_after_5_s(fake):
    fake.stall_s = 7.0
    started = time.monotonic()
    code, out, err = run(fake, "--email", EMAIL)
    assert code == 3 and out == "" and "timeout" in err.lower()
    assert time.monotonic() - started < 6.5


def test_tu13_a_failed_wallet_read_is_a_failure_not_a_zero(fake):
    fake.wallet_status = 500
    code, out, err = run(fake, "--email", EMAIL)
    assert code == 3 and out == "" and "wallet read: HTTP 500 (XX000)" in err
    assert_no_secret(out, err)


# ---------------------------------------------------------------- layer 2: app-c0 ---
def _service_jwt(secret: str) -> str:
    def enc(doc) -> str:
        return base64.urlsafe_b64encode(json.dumps(doc).encode()).rstrip(b"=").decode()
    head, body = enc({"alg": "HS256", "typ": "JWT"}), enc({"role": "service_role",
                                                          "exp": int(time.time()) + 600})
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def test_tu11_the_real_grant_through_postgrest_on_app_c0(fake, monkeypatch):
    """0015's claim and 0008's wallet read, for a synthetic confirmed auth.users row: one
    10,000 CREDIT grant, a second run replays it (the balance stays 10000, never 20000)."""
    if os.environ.get("INFRX_D1_IMAGE") != "supabase":
        pytest.skip("not run: needs INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase (C0 real stack)")
    monkeypatch.setenv("INFRX_D_TASK", "app-c0")
    # The stack's own modules from the real checkout (a mutant copy carries tests/d only).
    root = Path(os.environ.get("INFRX_E2_REPO_ROOT", REPO))
    for path in (root / "apps/infrx-api", root / "apps/app/tests/c/realdb"):
        monkeypatch.syspath_prepend(str(path))
    from tests.d import pgharness
    if reason := pgharness.unavailable():
        pytest.skip(f"not run: {reason}")
    import stack
    from infrx.state import migrations
    from tests.d import checks_credit, checks_signup
    pgharness.ensure()
    pgharness.recreate(stack.DB)
    pgharness.apply(stack.DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    uid = str(uuid.uuid4())
    with pgharness.connect(stack.DB) as conn:
        checks_signup.gotrue_columns(conn)
        checks_signup.individual(conn, uid, EMAIL)          # what GoTrue's admin create writes
        checks_credit.set_flag(conn, "signup_grant", True)
    fake.upstream = stack.up()
    fake.users[uid] = {"id": uid, "email": EMAIL, "password": "old",
                       "email_confirmed_at": "2026-09-27T00:00:00Z"}
    try:
        key = _service_jwt(stack.JWT_SECRET)
        outcomes = []
        for _ in range(2):
            code, out, err = run(fake, "--email", EMAIL, "--json", key=key)
            assert code == 0, err
            assert_no_secret(out, err, key=key)
            doc = json.loads(out)
            outcomes.append((doc["user_id"], doc["grant"], doc["available"], doc["unit"]))
        assert outcomes == [(uid, "granted", GRANT, "CREDIT"), (uid, "already-granted", GRANT, "CREDIT")]
        with pgharness.connect(stack.DB) as conn:
            rows = conn.execute("select count(*), sum(amount)::text from infrx.credit_ledger l "
                                "join infrx.credit_wallets w using (wallet_id) "
                                "where w.owner_user_id = %s", (uid,)).fetchone()
        assert rows == (1, GRANT)
    finally:
        stack.down()
