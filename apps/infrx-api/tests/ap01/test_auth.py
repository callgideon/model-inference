#!/usr/bin/env python3
"""AP-01 01a: the auth facade (`infrx.auth_facade`, `/auth/v1/*`) over a local GoTrue stub.

    uv run --frozen pytest -q tests/ap01/test_auth.py

The stub (`infrx.auth_facade.stub`) speaks hosted GoTrue's REST shapes; nothing here reaches a
real identity provider. The failure codes are the App's (`apps/app/app/(auth)/flow.ts`
`AuthFailure`), so the forms can switch to the facade without a new copy table.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from infrx.auth_facade import FAILURES, MESSAGES, AuthFacade, failure
from infrx.auth_facade.stub import GoTrueStub, s256
from infrx.gateway.routes import auth as auth_routes

APP = "https://app.example"
PASSWORD = "correct horse 1"
SAFE = {"x-request-id": "req-1"}
DOORS = ("sign_in", "sign_up", "recovery", "signup_grant")


def run(coro):
    return asyncio.run(coro)


class Flag:
    """`rt.identity`'s one read the availability route makes."""

    def __init__(self, enabled=True, broken=False) -> None:
        self.enabled, self.broken = enabled, broken

    async def signup_grant_enabled(self) -> bool:
        if self.broken:
            raise RuntimeError("database down")
        return self.enabled


def api(stub: GoTrueStub, *, idp: str = "http://gotrue.test", captcha: bool = False,
        identity=None, transport=None, site_key: str = "") -> httpx.AsyncClient:
    gotrue = httpx.AsyncClient(base_url=idp, transport=transport or httpx.ASGITransport(stub.app))
    rt = SimpleNamespace(auth_facade=AuthFacade(gotrue, stub.apikey, origins=(APP,),
                                                captcha_required=captcha,
                                                captcha_provider="turnstile",
                                                captcha_site_key=site_key),
                         identity=identity if identity is not None else Flag())
    app = FastAPI()
    auth_routes.register(app, rt)
    return httpx.AsyncClient(base_url="http://api.test", transport=httpx.ASGITransport(app),
                             headers=SAFE)


def refused(answer: httpx.Response, reason: str) -> None:
    status, retryable = FAILURES[reason]
    assert answer.status_code == status, (answer.status_code, answer.text)
    error = answer.json()["error"]
    assert (error["code"], error["message"], error["retryable"]) == (
        reason, MESSAGES[reason], retryable), error
    assert error["request_id"] == "req-1"
    assert answer.headers["cache-control"] == "no-store"


def idp_calls(stub: GoTrueStub, path: str) -> list:
    return [call for call in stub.seen if call[1] == path]


# --- sign-in ----------------------------------------------------------------------------------
def test_auth__sign_in_answers_tokens_in_the_body_no_store():
    """Oracle: a good password is a session in the BODY (Next sets the cookies), never a URL,
    a redirect or a cookie of ours, and never cacheable."""
    stub = GoTrueStub()
    user = stub.user("a@example.com")

    async def go():
        async with api(stub) as c:
            return await c.post("/auth/v1/sign-in", json={"email": "a@example.com",
                                                          "password": PASSWORD})
    answer = run(go())
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["user_id"] == user.id and stub.access[body["access_token"]] == user.id
    assert body["refresh_token"] in stub.refresh and body["token_type"] == "bearer"
    assert answer.headers["cache-control"] == "no-store"
    assert "set-cookie" not in answer.headers and "location" not in answer.headers
    assert idp_calls(stub, "/auth/v1/token")[0][2] == {"grant_type": "password"}


def test_auth__a_wrong_password_and_an_unknown_email_read_the_same():
    """Oracle: account enumeration - a wrong password and a never-registered address are the
    same 401 `invalid_credentials`, byte for byte (flow.ts: user_not_found reads the same)."""
    stub = GoTrueStub()
    stub.user("a@example.com")

    async def go():
        async with api(stub) as c:
            wrong = await c.post("/auth/v1/sign-in", json={"email": "a@example.com",
                                                           "password": "nope nope 1"})
            unknown = await c.post("/auth/v1/sign-in", json={"email": "z@example.com",
                                                             "password": "nope nope 1"})
            return wrong, unknown
    wrong, unknown = run(go())
    refused(wrong, "invalid_credentials")
    assert wrong.content == unknown.content and wrong.status_code == unknown.status_code


def test_auth__an_unreachable_or_failing_idp_is_503_never_bad_credentials():
    """Oracle: an outage is `unavailable` (503, retryable), never a 401 that tells the user
    their password is wrong (or a refresh or link that reads as dead) - both a refused
    connection and a 5xx answer."""
    stub = GoTrueStub()
    stub.user("a@example.com")
    creds = {"email": "a@example.com", "password": PASSWORD}

    async def go():
        async with api(stub, idp="http://127.0.0.1:9", transport=httpx.AsyncHTTPTransport()) as c:
            unreachable = await c.post("/auth/v1/sign-in", json=creds)
        stub.down = True
        async with api(stub) as c:
            failing = await c.post("/auth/v1/sign-in", json=creds)
            signup = await c.post("/auth/v1/sign-up", json={"email": "n@example.com",
                                                            "password": PASSWORD})
            refresh = await c.post("/auth/v1/refresh", json={"refresh_token": "r" * 20})
            link = await c.get("/auth/v1/callback", params={"token_hash": "f" * 32,
                                                            "type": "signup"})
        return unreachable, failing, signup, refresh, link
    for answer in run(go()):
        refused(answer, "unavailable")


def test_auth__unconfirmed_and_rate_limited_keep_their_fixed_codes():
    """Oracle: the IdP's own codes reach the App as its fixed failures (email_not_confirmed
    403, rate_limited 429 retryable), not as `unavailable` or the IdP's text."""
    stub = GoTrueStub()
    stub.user("u@example.com", confirmed=False)

    async def go():
        async with api(stub) as c:
            unconfirmed = await c.post("/auth/v1/sign-in", json={"email": "u@example.com",
                                                                 "password": PASSWORD})
            stub.limited = True
            limited = await c.post("/auth/v1/sign-in", json={"email": "u@example.com",
                                                             "password": PASSWORD})
            return unconfirmed, limited
    unconfirmed, limited = run(go())
    refused(unconfirmed, "email_not_confirmed")
    refused(limited, "rate_limited")
    assert limited.headers.get("retry-after") == "60"


# --- sign-up and recovery ---------------------------------------------------------------------
def test_auth__sign_up_of_an_existing_email_reads_sent():
    """Oracle: enumeration - an address that already has an account is `sent`, exactly like a
    new one (flow.ts signupSettled)."""
    stub = GoTrueStub()
    stub.user("a@example.com")

    async def go():
        async with api(stub) as c:
            old = await c.post("/auth/v1/sign-up", json={"email": "a@example.com",
                                                         "password": PASSWORD})
            new = await c.post("/auth/v1/sign-up", json={"email": "b@example.com",
                                                         "password": PASSWORD})
            return old, new
    old, new = run(go())
    assert old.status_code == new.status_code == 200
    assert old.content == new.content and old.json() == {"status": "sent"}
    assert "b@example.com" in stub.users


def test_auth__sign_up_forwards_captcha_challenge_and_allowlisted_redirect():
    """Oracle: LR-02 - the CAPTCHA token is forwarded as `gotrue_meta_security.captcha_token`
    (the hosted policy decides); the PKCE challenge is forwarded (the verifier stays with
    Next); an allowlisted `redirect_to` is the IdP's `redirect_to` query."""
    stub = GoTrueStub()
    stub.captcha = True
    link = f"{APP}/auth/callback?next=/welcome"

    async def go():
        async with api(stub, captcha=True) as c:
            missing = await c.post("/auth/v1/sign-up", json={"email": "a@example.com",
                                                             "password": PASSWORD})
            ok = await c.post("/auth/v1/sign-up", json={
                "email": "a@example.com", "password": PASSWORD, "captcha_token": "captcha-ok",
                "redirect_to": link, "code_challenge": s256("v" * 43)})
            return missing, ok
    missing, ok = run(go())
    refused(missing, "captcha_failed")       # LR-02: a failed challenge is its own code
    assert ok.status_code == 200, ok.text
    _, _, query, body = idp_calls(stub, "/auth/v1/signup")[-1]
    assert query == {"redirect_to": link}
    assert body["gotrue_meta_security"] == {"captcha_token": "captcha-ok"}
    assert (body["code_challenge"], body["code_challenge_method"]) == (s256("v" * 43), "s256")


def test_auth__a_required_challenge_guards_every_password_door_and_reveals_no_account():
    """Oracle: LR-02 - with the hosted CAPTCHA on, sign-in, sign-up and recovery forward the
    token (the IdP checks it at all three, so a sign-in without it would lock everyone out);
    a missing or failed challenge is 422 `captcha_failed`, identical for a registered and an
    unknown address (no enumeration through the challenge), and a solved one proceeds."""
    stub = GoTrueStub()
    stub.captcha = True
    stub.user("a@example.com")
    ok = {"captcha_token": "captcha-ok"}

    async def go():
        async with api(stub, captcha=True) as c:
            doors = {}
            for email in ("a@example.com", "z@example.com"):
                creds = {"email": email, "password": PASSWORD}
                doors[email] = [await c.post("/auth/v1/sign-in", json=creds),
                                await c.post("/auth/v1/sign-up", json=creds),
                                await c.post("/auth/v1/recovery", json={"email": email})]
            solved = [await c.post("/auth/v1/sign-in", json={"email": "a@example.com",
                                                             "password": PASSWORD, **ok}),
                      await c.post("/auth/v1/recovery", json={"email": "z@example.com", **ok})]
            return doors, solved
    doors, (signed_in, recovered) = run(go())
    for known, unknown in zip(doors["a@example.com"], doors["z@example.com"], strict=True):
        refused(known, "captcha_failed")
        assert known.content == unknown.content
    assert signed_in.status_code == 200, signed_in.text
    assert recovered.json() == {"status": "sent"}
    sign_in = idp_calls(stub, "/auth/v1/token")[-1][3]
    assert sign_in["gotrue_meta_security"] == {"captcha_token": "captcha-ok"}


def test_auth__sign_up_failures_map_like_the_app():
    """Oracle: closed signup 403 `signup_closed`, a weak password and a bad address 422 with
    their own codes - each the App's fixed copy, never the IdP's text."""
    stub = GoTrueStub()

    async def go():
        async with api(stub) as c:
            weak = await c.post("/auth/v1/sign-up", json={"email": "a@example.com",
                                                          "password": "short"})
            bad = await c.post("/auth/v1/sign-up", json={"email": "nobody",
                                                         "password": PASSWORD})
            stub.disable_signup = True
            closed = await c.post("/auth/v1/sign-up", json={"email": "a@example.com",
                                                            "password": PASSWORD})
            return weak, bad, closed
    weak, bad, closed = run(go())
    refused(weak, "weak_password")
    refused(bad, "invalid_email")
    refused(closed, "signup_closed")


def test_auth__recovery_of_an_unknown_email_reads_sent():
    """Oracle: enumeration - recovery for an unknown address is `sent` like a known one
    (flow.ts emailSettled), and a rate limit is still `rate_limited`."""
    stub = GoTrueStub()
    stub.user("a@example.com")

    async def go():
        async with api(stub) as c:
            known = await c.post("/auth/v1/recovery", json={"email": "a@example.com"})
            unknown = await c.post("/auth/v1/recovery", json={"email": "z@example.com"})
            stub.limited = True
            limited = await c.post("/auth/v1/recovery", json={"email": "z@example.com"})
            return known, unknown, limited
    known, unknown, limited = run(go())
    assert known.status_code == unknown.status_code == 200
    assert known.content == unknown.content and known.json() == {"status": "sent"}
    refused(limited, "rate_limited")


def test_auth__resend_reads_sent_for_any_address_and_keeps_its_guards():
    """Oracle: WR-AP09-RESEND - a new sign-up link (IdP `type=signup`, the allowlisted
    `redirect_to` and the challenge forwarded) reads `sent` byte-identically for an unconfirmed,
    an already confirmed and an unknown address (no enumeration, like recovery); a rate limit
    and an outage keep their codes; a cross-origin POST or a foreign redirect never reaches the
    IdP."""
    stub = GoTrueStub()
    stub.user("new@example.com", confirmed=False)
    stub.user("done@example.com")
    link = f"{APP}/auth/callback"

    async def go():
        async with api(stub) as c:
            sent = [await c.post("/auth/v1/resend", json={
                "email": email, "redirect_to": link, "captcha_token": "captcha-ok"})
                for email in ("new@example.com", "done@example.com", "z@example.com")]
            seen = len(stub.seen)
            guarded = [await c.post("/auth/v1/resend", json={"email": "new@example.com"},
                                    headers={"origin": "https://evil.example"}),
                       await c.post("/auth/v1/resend", json={
                           "email": "new@example.com",
                           "redirect_to": "https://evil.example/auth/callback"})]
            after = len(stub.seen)
            stub.limited = True
            limited = await c.post("/auth/v1/resend", json={"email": "z@example.com"})
            stub.limited, stub.down = False, True
            down = await c.post("/auth/v1/resend", json={"email": "new@example.com"})
            return sent, seen, guarded, after, limited, down
    sent, seen, (evil, foreign), after, limited, down = run(go())
    for answer in sent:
        assert answer.status_code == 200, answer.text
        assert answer.content == sent[0].content and answer.json() == {"status": "sent"}
    _, _, query, body = idp_calls(stub, "/auth/v1/resend")[0]
    assert query == {"redirect_to": link}
    assert (body.get("type"), body.get("email")) == ("signup", "new@example.com")
    assert body.get("gotrue_meta_security") == {"captcha_token": "captcha-ok"}
    assert evil.status_code == 403 and foreign.status_code == 422 and after == seen
    refused(limited, "rate_limited")
    refused(down, "unavailable")


def test_auth__a_redirect_outside_the_allowlist_never_reaches_the_idp():
    """Oracle: open redirect - a `redirect_to` on any origin but the configured ones (or a
    look-alike) is a 422 and the IdP is never asked to email that link."""
    stub = GoTrueStub()
    stub.user("a@example.com")
    bad = ("https://evil.example/auth/callback", "https://app.example.evil.example/x",
           "http://app.example/auth/callback", "https://app.example@evil.example/",
           "javascript:alert(1)", "/relative")

    async def go():
        async with api(stub) as c:
            return [await c.post(path, json={"email": "a@example.com", "password": PASSWORD,
                                             "redirect_to": url} if path.endswith("up")
                                 else {"email": "a@example.com", "redirect_to": url})
                    for url in bad for path in ("/auth/v1/sign-up", "/auth/v1/recovery")]
    for answer in run(go()):
        assert answer.status_code == 422, answer.text
        assert [f["field"] for f in answer.json()["error"]["field_errors"]] == ["redirect_to"]
    assert stub.seen == []


def test_auth__a_cross_origin_submission_never_reaches_the_idp():
    """Oracle: CSRF - a browser POST from another origin is refused (403) before the IdP is
    called; the server-to-server call (no Origin) and the App's own origin pass."""
    stub = GoTrueStub()
    stub.user("a@example.com")
    creds = {"email": "a@example.com", "password": PASSWORD}

    async def go():
        async with api(stub) as c:
            evil = await c.post("/auth/v1/sign-in", json=creds,
                                headers={"origin": "https://evil.example"})
            seen = len(stub.seen)
            own = await c.post("/auth/v1/sign-in", json=creds, headers={"origin": APP})
            return evil, seen, own
    evil, seen, own = run(go())
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "forbidden"
    assert seen == 0 and own.status_code == 200


# --- the email-link callback ------------------------------------------------------------------
def test_auth__callback_exchanges_the_code_with_the_forwarded_verifier():
    """Oracle: the PKCE exchange uses the verifier Next forwards in a header (never the URL);
    a wrong verifier is `link_invalid`; `next` is honoured only as a same-site path."""
    stub = GoTrueStub()
    user = stub.user("a@example.com")

    async def go():
        async with api(stub) as c:
            code = stub.code(user.id, "v" * 43)
            good = await c.get("/auth/v1/callback", params={"code": code, "next": "/models"},
                               headers={"x-auth-code-verifier": "v" * 43})
            code = stub.code(user.id, "v" * 43)
            wrong = await c.get("/auth/v1/callback", params={"code": code},
                                headers={"x-auth-code-verifier": "w" * 43})
            code = stub.code(user.id, "v" * 43)
            none = await c.get("/auth/v1/callback", params={"code": code})
            code = stub.code(user.id, "v" * 43)
            unsafe = await c.get("/auth/v1/callback", params={"code": code, "next": "//evil"},
                                 headers={"x-auth-code-verifier": "v" * 43})
            recovery = await c.get("/auth/v1/callback", params={
                "token_hash": stub.link(user.id, "recovery"), "type": "recovery"})
            return good, wrong, none, unsafe, recovery
    good, wrong, none, unsafe, recovery = run(go())
    assert good.status_code == 200, good.text
    assert good.json()["redirect"] == "/models"
    assert stub.access[good.json()["session"]["access_token"]] == user.id
    refused(wrong, "link_invalid")
    refused(none, "link_invalid")
    assert unsafe.json()["redirect"] == "/welcome"
    assert recovery.status_code == 200 and recovery.json()["redirect"] == "/update-password"


def test_auth__callback_links_expire_or_are_invalid_like_the_app():
    """Oracle: flow.ts completeCallback - an IdP `error_code` reads as expired only when it
    means expired; the free-text `error_description` is never read; a used link is expired;
    a link with neither code nor token hash is invalid."""
    stub = GoTrueStub()
    user = stub.user("a@example.com")

    async def go():
        async with api(stub) as c:
            expired = await c.get("/auth/v1/callback", params={
                "error": "access_denied", "error_code": "otp_expired",
                "error_description": "<script>"})
            other = await c.get("/auth/v1/callback", params={"error": "x", "error_code": "boom"})
            used = await c.get("/auth/v1/callback", params={"token_hash": "f" * 32,
                                                            "type": "signup"})
            empty = await c.get("/auth/v1/callback")
            wrong_type = await c.get("/auth/v1/callback", params={
                "token_hash": stub.link(user.id, "signup"), "type": "bogus"})
            return expired, other, used, empty, wrong_type
    expired, other, used, empty, wrong_type = run(go())
    refused(expired, "link_expired")
    refused(other, "link_invalid")
    refused(used, "link_expired")
    refused(empty, "link_invalid")
    refused(wrong_type, "link_invalid")
    assert "<script>" not in expired.text


# --- the session's own operations ---------------------------------------------------------------
def test_auth__refresh_rotates_and_a_used_refresh_token_is_unauthenticated():
    """Oracle: refresh answers a new session; a used or unknown refresh token is a 401
    `unauthenticated` (sign in again), never `unavailable`."""
    stub = GoTrueStub()
    session = stub.session(stub.user("a@example.com").id)

    async def go():
        async with api(stub) as c:
            fresh = await c.post("/auth/v1/refresh",
                                 json={"refresh_token": session["refresh_token"]})
            again = await c.post("/auth/v1/refresh",
                                 json={"refresh_token": session["refresh_token"]})
            return fresh, again
    fresh, again = run(go())
    assert fresh.status_code == 200 and fresh.json()["access_token"] != session["access_token"]
    refused(again, "unauthenticated")


def test_auth__sign_out_is_idempotent():
    """Oracle: sign-out ends the IdP session (the token stops verifying); signing out a
    session that is already gone is the same 204; no bearer is a 401; an outage is 503."""
    stub = GoTrueStub()
    token = stub.session(stub.user("a@example.com").id)["access_token"]
    bearer = {"authorization": f"Bearer {token}"}

    async def go():
        async with api(stub) as c:
            first = await c.post("/auth/v1/sign-out", headers=bearer)
            second = await c.post("/auth/v1/sign-out", headers=bearer)
            anonymous = await c.post("/auth/v1/sign-out")
            stub.down = True
            down = await c.post("/auth/v1/sign-out", headers=bearer)
            return first, second, anonymous, down
    first, second, anonymous, down = run(go())
    assert first.status_code == second.status_code == 204
    assert token not in stub.access
    refused(anonymous, "unauthenticated")
    refused(down, "unavailable")


def test_auth__password_update_needs_the_session_and_maps_policy():
    """Oracle: the update runs as the session's own user (bearer forwarded); no or a dead
    session is 401; weak/same password keep their codes; success is 204."""
    stub = GoTrueStub()
    user = stub.user("a@example.com")
    bearer = {"authorization": f"Bearer {stub.session(user.id)['access_token']}"}

    async def go():
        async with api(stub) as c:
            anonymous = await c.post("/auth/v1/password", json={"password": "brand new 22"})
            dead = await c.post("/auth/v1/password", json={"password": "brand new 22"},
                                headers={"authorization": "Bearer a.b.c"})
            weak = await c.post("/auth/v1/password", json={"password": "short"}, headers=bearer)
            same = await c.post("/auth/v1/password", json={"password": PASSWORD},
                                headers=bearer)
            ok = await c.post("/auth/v1/password", json={"password": "brand new 22"},
                              headers=bearer)
            return anonymous, dead, weak, same, ok
    anonymous, dead, weak, same, ok = run(go())
    refused(anonymous, "unauthenticated")
    refused(dead, "unauthenticated")
    refused(weak, "weak_password")
    refused(same, "same_password")
    assert ok.status_code == 204 and user.password == "brand new 22"


# --- availability -------------------------------------------------------------------------------
def test_auth__availability_comes_from_the_idp_the_flag_and_the_captcha_setting():
    """Oracle: signup availability is the IdP's closed-signup setting AND the grant flag is
    reported beside it; CAPTCHA required is the configured yes/no; an IdP or database outage
    is `unknown` with a reason, never `disabled`."""
    stub = GoTrueStub()

    async def go():
        async with api(stub, captcha=True, site_key="0x4AAA-site") as c:
            open_ = (await c.get("/auth/v1/availability")).json()
        async with api(stub, captcha=True) as c:
            unset = (await c.get("/auth/v1/availability")).json()
        stub.disable_signup = True
        async with api(stub, identity=Flag(enabled=False)) as c:
            closed = (await c.get("/auth/v1/availability")).json()
        stub.down = True
        async with api(stub, identity=Flag(broken=True)) as c:
            down = await c.get("/auth/v1/availability")
        return open_, unset, closed, down
    open_, unset, closed, down = run(go())

    def states(doc):
        return {k: (doc[k]["state"], doc[k]["reason"]) for k in DOORS}
    assert states(open_) == {k: ("configured", None) for k in DOORS}
    assert open_["sign_in"]["verified_at"]
    assert {k: v for k, v in open_["captcha"].items() if k != "state"} == {
        "required": True, "provider": "turnstile", "site_key": "0x4AAA-site"}
    assert open_["captcha"]["state"]["state"] == "configured"
    # required by the hosted policy but no site key to render: every password door is
    # honestly unavailable (a form could never pass the challenge), the grant flag is not
    assert states(unset) == {**{k: ("unavailable", "captcha_unconfigured")
                                for k in ("sign_in", "sign_up", "recovery")},
                             "signup_grant": ("configured", None)}
    assert (unset["captcha"]["required"], unset["captcha"]["site_key"]) == (True, None)
    assert (unset["captcha"]["state"]["state"], unset["captcha"]["state"]["reason"]) == (
        "unavailable", "site_key_missing")
    assert closed["sign_up"] == {**closed["sign_up"], "state": "disabled",
                                 "reason": "signup_closed"}
    assert closed["signup_grant"]["state"] == "disabled"
    assert (closed["captcha"]["required"], closed["captcha"]["state"]["state"],
            closed["captcha"]["state"]["reason"]) == (False, "disabled", "not_required")
    assert closed["sign_in"]["state"] == "configured"
    assert down.status_code == 200 and down.headers["cache-control"] == "no-store"
    assert {down.json()[k]["state"] for k in DOORS} == {"unknown"}


# --- secrets ------------------------------------------------------------------------------------
def test_auth__no_password_or_token_reaches_a_log_or_an_error(caplog):
    """Oracle: secrets never leave - no password, access or refresh token in any log record
    at any level, nor in an error body, across every operation and its failures."""
    stub = GoTrueStub()
    user = stub.user("a@example.com")
    session = stub.session(user.id)
    secrets = [PASSWORD, "brand new 22", session["access_token"], session["refresh_token"]]

    async def go():
        async with api(stub) as c:
            bodies = []
            for path, body, headers in (
                    ("/auth/v1/sign-in", {"email": "a@example.com", "password": PASSWORD}, {}),
                    ("/auth/v1/sign-in", {"email": "a@example.com", "password": "x" * 9}, {}),
                    ("/auth/v1/sign-in", {"email": "a@example.com", "password": PASSWORD,
                                          "extra": PASSWORD}, {}),
                    ("/auth/v1/password", {"password": "brand new 22"},
                     {"authorization": f"Bearer {session['access_token']}"}),
                    ("/auth/v1/refresh", {"refresh_token": session["refresh_token"]}, {}),
                    ("/auth/v1/refresh", {"refresh_token": session["refresh_token"]}, {})):
                answer = await c.post(path, json=body, headers=headers)
                bodies.append(answer.text if answer.status_code >= 400 else "")
            return bodies
    with caplog.at_level(logging.DEBUG):
        bodies = run(go())
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for secret in secrets:
        assert secret not in logged
        assert all(secret not in body for body in bodies)


def test_auth__an_invalid_body_is_the_r270_envelope_without_its_values():
    """Oracle: a malformed body is the R270 422 envelope with field errors naming the field,
    never FastAPI's default `{detail}` (which echoes the input, a password included)."""
    stub = GoTrueStub()

    async def go():
        async with api(stub) as c:
            missing = await c.post("/auth/v1/sign-in", json={"email": "a@example.com"})
            extra = await c.post("/auth/v1/sign-in", json={
                "email": "a@example.com", "password": "hunter2-secret", "role": "admin"})
            text = await c.post("/auth/v1/sign-in", content=b"email=a&password=hunter2-secret",
                                headers={"content-type": "application/x-www-form-urlencoded"})
            return missing, extra, text
    missing, extra, text = run(go())
    for answer in (missing, extra, text):
        assert answer.status_code == 422, answer.text
        assert "detail" not in answer.json(), answer.text
        assert answer.json()["error"]["code"] == "invalid_request"
        assert "hunter2" not in answer.text
    assert [f["field"] for f in missing.json()["error"]["field_errors"]] == ["password"]
    assert stub.seen == []


def test_auth__failure_codes_mirror_the_app_flow():
    """Oracle: flow.ts `authFailure` - the IdP code table, a 429 without a code is rate
    limited, any other unknown code is `unavailable`."""
    expected = {"invalid_credentials": "invalid_credentials",
                "user_not_found": "invalid_credentials",
                "email_not_confirmed": "email_not_confirmed",
                "over_request_rate_limit": "rate_limited",
                "over_email_send_rate_limit": "rate_limited", "weak_password": "weak_password",
                "same_password": "same_password", "email_address_invalid": "invalid_email",
                "validation_failed": "invalid_email", "signup_disabled": "signup_closed",
                "email_provider_disabled": "signup_closed",
                "email_address_not_authorized": "email_unavailable",
                "otp_expired": "link_expired", "flow_state_expired": "link_expired",
                "flow_state_not_found": "link_expired", "session_not_found": "link_expired",
                "session_expired": "link_expired"}
    for code, reason in expected.items():
        assert failure(code, 400) == reason, code
    assert failure(None, 429) == "rate_limited"
    assert failure(None, 400) == "unavailable"
    assert failure("captcha_failed", 400) == "captcha_failed"      # LR-02, the facade's own
    assert failure("toString", 400) == "unavailable"
