"""AP-01 01a: the auth facade - the web apps' identity-provider operations as typed calls over
the project's auth server (Supabase Auth, GoTrue REST), so neither app needs more of Supabase
than its cookie transport (R271). Routes: `infrx.gateway.routes.auth` (`/auth/v1/*`).

Kept from `apps/app/app/(auth)/flow.ts` (A2), the behaviour the forms have today:
- every failure is one of the App's fixed `AuthFailure` codes (`failure`: the IdP's
  `error_code` table, a bare 429 is rate limited, anything else `unavailable`) with the App's
  own copy; the IdP's text never leaves;
- enumeration-safe outcomes: signup of an existing address, recovery of an unknown one and a
  resent verification for an unknown or already confirmed one read `sent`, and a wrong
  password reads like an unknown address;
- the email-link callback: an IdP `error_code` is read only as a fixed code, recovery lands on
  `/update-password`, `next` only as a same-site path.

Added: an unreachable or failing IdP is 503 `unavailable`, never bad credentials; tokens only
in response bodies (Next sets the cookies); `redirect_to` only on a configured web origin; a
cross-origin POST refused before the IdP is called; the CAPTCHA token forwarded as
`gotrue_meta_security` (LR-02: the hosted challenge policy decides); the PKCE challenge
forwarded while its verifier stays with Next until the callback. Nothing is stored here and
nothing logged: no password, token or session outlives the call.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi.responses import JSONResponse

from ..contracts import api, errors

#: reason -> (HTTP status, retryable). The App's `AuthFailure` set plus the callback's
#: `link_invalid`, a dead session's `unauthenticated` and a failed challenge's
#: `captcha_failed` (LR-02: the App's forms gain it with the widget, AP-09).
FAILURES: dict[str, tuple[int, bool]] = {
    "invalid_credentials": (401, False), "email_not_confirmed": (403, False),
    "rate_limited": (429, True), "weak_password": (422, False), "same_password": (422, False),
    "invalid_email": (422, False), "signup_closed": (403, False),
    "email_unavailable": (422, False), "link_expired": (410, False),
    "link_invalid": (422, False), "unauthenticated": (401, False), "unavailable": (503, True),
    "captcha_failed": (422, False),
}
#: flow.ts FAILURE_COPY (and its link notice), word for word.
MESSAGES: dict[str, str] = {
    "invalid_credentials": "That email and password do not match an account.",
    "email_not_confirmed": "Verify your email address first. We can send you a new "
                           "verification link.",
    "rate_limited": "Too many attempts. Wait a minute, then try again.",
    "weak_password": "Choose a stronger password: at least 8 characters, not a common one.",
    "same_password": "Choose a password different from your current one.",
    "invalid_email": "Enter a valid email address.",
    "signup_closed": "New accounts are not being accepted right now. Try again later.",
    "email_unavailable": "We could not send an email to this address right now. Try again "
                         "later.",
    "link_expired": "This link has expired or was already used. Request a new one.",
    "link_invalid": "This link is not valid. Sign in, or request a new link.",
    "unauthenticated": "Your session has ended. Sign in again.",
    "unavailable": "Something went wrong on our side. Try again in a moment.",
    "captcha_failed": "Complete the verification challenge, then try again.",
}
#: flow.ts BY_CODE: the IdP `error_code` -> the App's failure.
BY_CODE: dict[str, str] = {
    "invalid_credentials": "invalid_credentials", "user_not_found": "invalid_credentials",
    "email_not_confirmed": "email_not_confirmed", "over_request_rate_limit": "rate_limited",
    "over_email_send_rate_limit": "rate_limited", "weak_password": "weak_password",
    "same_password": "same_password", "email_address_invalid": "invalid_email",
    "validation_failed": "invalid_email", "signup_disabled": "signup_closed",
    "email_provider_disabled": "signup_closed",
    "email_address_not_authorized": "email_unavailable", "otp_expired": "link_expired",
    "flow_state_expired": "link_expired", "flow_state_not_found": "link_expired",
    "session_not_found": "link_expired", "session_expired": "link_expired",
    "captcha_failed": "captcha_failed",
}
#: The hosted auth server's CAPTCHA providers (its bot-protection setting).
CAPTCHA_PROVIDERS = ("hcaptcha", "turnstile")
EXISTING = ("user_already_exists", "email_exists")
EMAIL_LINK_TYPES = ("signup", "email", "magiclink", "recovery", "invite", "email_change")
AFTER_VERIFY = "/welcome"
RETRY_AFTER_S = 60
TOKEN = "/auth/v1/token"
#: flow.ts safeNext: a same-site path, no backslash, whitespace or control character.
SAME_SITE = re.compile(r"/(?!/)[^\\\s\x00-\x1f\x7f]*")
SNEAKY = re.compile(r"%5c|%2f%2f", re.I)


def failure(code: str | None, status: int) -> str:
    """flow.ts `authFailure`: the IdP code's failure; a 429 is rate limited; else unavailable."""
    if code in BY_CODE:
        return BY_CODE[code]
    if status == 429:
        return "rate_limited"
    return "unavailable"


def safe_next(path: str | None, fallback: str = AFTER_VERIFY) -> str:
    if not isinstance(path, str) or not SAME_SITE.fullmatch(path) or SNEAKY.search(path):
        return fallback
    return path


class AuthRefused(Exception):
    """One fixed failure, rendered as the R270 envelope with the App's copy as message."""

    def __init__(self, reason: str) -> None:
        if reason not in FAILURES:
            raise ValueError(f"unknown auth failure {reason!r}")
        super().__init__(reason)
        self.reason = reason

    def response(self, request_id: str) -> JSONResponse:
        status, retryable = FAILURES[self.reason]
        body = api.ErrorEnvelope(error=api.ErrorBody(
            code=self.reason, message=MESSAGES[self.reason], request_id=request_id,
            retryable=retryable))
        headers = {"Cache-Control": "no-store"}
        if self.reason == "rate_limited":
            headers["Retry-After"] = str(RETRY_AFTER_S)
        return JSONResponse(body.model_dump(mode="json"), status, headers=headers)


class Session(api.Wire):
    """A signed-in session, for Next to set as its cookies. Never in a URL or a log."""

    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    expires_at: int | None = None
    user_id: str


def _session(answer: httpx.Response) -> Session:
    try:
        body = answer.json()
        return Session(access_token=body["access_token"], refresh_token=body["refresh_token"],
                       token_type=body["token_type"], expires_in=body["expires_in"],
                       expires_at=body.get("expires_at"), user_id=body["user"]["id"])
    except (ValueError, KeyError, TypeError):
        raise AuthRefused("unavailable") from None


def _code(answer: httpx.Response) -> str | None:
    try:
        body = answer.json()
    except ValueError:
        return None
    code = body.get("error_code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def _link(answer: httpx.Response) -> AuthRefused:
    """flow.ts completeCallback: a refused link is expired only when the IdP says expired."""
    expired = failure(_code(answer), answer.status_code) == "link_expired"
    return AuthRefused("link_expired" if expired else "link_invalid")


class AuthFacade:
    """The IdP's REST calls. `apikey` is the project's publishable (anon) key: the facade acts
    for an end user, so the service-role key - which the IdP treats as an administrator and
    lets past its CAPTCHA - is never sent here.

    `captcha_required` is the hosted bot-protection policy (on, the auth server checks a token
    at password sign-in, sign-up and recovery); `captcha_provider` + `captcha_site_key` are
    its public half, the widget a form renders. Neither is readable from the auth server's
    public settings, so the deployment states them (LR-02)."""

    def __init__(self, client: httpx.AsyncClient, apikey: str, *, origins: tuple[str, ...] = (),
                 captcha_required: bool = False, captcha_provider: str = "",
                 captcha_site_key: str = "") -> None:
        self.client, self.apikey = client, apikey
        self.origins = tuple(origin.rstrip("/") for origin in origins)
        self.captcha_required = captcha_required
        ready = captcha_provider in CAPTCHA_PROVIDERS and bool(captcha_site_key.strip())
        self.captcha_widget = (captcha_provider, captcha_site_key.strip()) if ready else None

    async def _send(self, method: str, path: str, *, token: str | None = None,
                    params: dict[str, str] | None = None,
                    body: dict[str, Any] | None = None) -> httpx.Response:
        headers = {"apikey": self.apikey, "authorization": f"Bearer {token or self.apikey}"}
        try:
            answer = await self.client.request(method, path, params=params, json=body,
                                               headers=headers)
        except httpx.HTTPError:
            raise AuthRefused("unavailable") from None
        if answer.status_code >= 500:
            raise AuthRefused("unavailable")
        return answer

    # --- the request's own checks, before the IdP is called --------------------------------
    def check_origin(self, origin: str | None) -> None:
        """A browser's cross-origin POST carries its Origin; Next's server-side call none."""
        if origin is not None and origin.rstrip("/") not in self.origins:
            raise errors.Forbidden("cross-origin submission")

    def redirect(self, url: str | None) -> dict[str, str]:
        """The IdP's `redirect_to` query: only an absolute URL on a configured web origin."""
        if url is None:
            return {}
        parts = urlsplit(url)
        if not url.isprintable() or " " in url or f"{parts.scheme}://{parts.netloc}" \
                not in self.origins:
            raise errors.InvalidRequest("redirect_to is not an allowed origin",
                                        param="redirect_to")
        return {"redirect_to": url}

    @staticmethod
    def _extras(captcha_token: str | None, code_challenge: str | None) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        if captcha_token:
            extras["gotrue_meta_security"] = {"captcha_token": captcha_token}
        if code_challenge:
            extras |= {"code_challenge": code_challenge, "code_challenge_method": "s256"}
        return extras

    # --- operations ---------------------------------------------------------------------------
    async def sign_in(self, email: str, password: str, *,
                      captcha_token: str | None = None) -> Session:
        answer = await self._send("POST", TOKEN, params={"grant_type": "password"}, body={
            "email": email, "password": password, **self._extras(captcha_token, None)})
        if answer.status_code != 200:
            raise AuthRefused(failure(_code(answer), answer.status_code))
        return _session(answer)

    async def sign_up(self, email: str, password: str, *, captcha_token: str | None = None,
                      redirect_to: str | None = None, code_challenge: str | None = None) -> None:
        """flow.ts signupSettled: an existing address is `sent` like a new one."""
        params = self.redirect(redirect_to)
        answer = await self._send("POST", "/auth/v1/signup", params=params, body={
            "email": email, "password": password, **self._extras(captcha_token, code_challenge)})
        if answer.status_code != 200 and _code(answer) not in EXISTING:
            raise AuthRefused(failure(_code(answer), answer.status_code))

    async def recover(self, email: str, *, captcha_token: str | None = None,
                      redirect_to: str | None = None, code_challenge: str | None = None) -> None:
        """flow.ts emailSettled: an unknown address is `sent` like a known one."""
        params = self.redirect(redirect_to)
        answer = await self._send("POST", "/auth/v1/recover", params=params, body={
            "email": email, **self._extras(captcha_token, code_challenge)})
        if answer.status_code != 200:
            reason = failure(_code(answer), answer.status_code)
            if reason != "invalid_credentials":
                raise AuthRefused(reason)

    async def resend(self, email: str, *, captcha_token: str | None = None,
                     redirect_to: str | None = None, code_challenge: str | None = None) -> None:
        """WR-AP09-RESEND: a new sign-up verification link. Like recovery, an unknown or an
        already confirmed address reads `sent` (no enumeration)."""
        params = self.redirect(redirect_to)
        answer = await self._send("POST", "/auth/v1/resend", params=params, body={
            "type": "signup", "email": email, **self._extras(captcha_token, code_challenge)})
        code = _code(answer)
        unknown = failure(code, answer.status_code) == "invalid_credentials"
        if answer.status_code != 200 and code not in EXISTING and not unknown:
            raise AuthRefused(failure(code, answer.status_code))

    async def refresh(self, refresh_token: str) -> Session:
        answer = await self._send("POST", TOKEN, params={"grant_type": "refresh_token"},
                                  body={"refresh_token": refresh_token})
        if answer.status_code == 429:
            raise AuthRefused("rate_limited")
        if answer.status_code != 200:
            raise AuthRefused("unauthenticated")
        return _session(answer)

    async def sign_out(self, token: str) -> None:
        """Ends this session at the IdP. A session already gone is the same success."""
        answer = await self._send("POST", "/auth/v1/logout", token=token,
                                  params={"scope": "local"})
        if answer.status_code >= 300 and answer.status_code not in (401, 403, 404):
            raise AuthRefused(failure(_code(answer), answer.status_code))

    async def update_password(self, token: str, password: str) -> None:
        answer = await self._send("PUT", "/auth/v1/user", token=token,
                                  body={"password": password})
        if answer.status_code in (401, 403):
            raise AuthRefused("unauthenticated")
        if answer.status_code != 200:
            raise AuthRefused(failure(_code(answer), answer.status_code))

    async def callback(self, *, code: str | None, token_hash: str | None, kind: str | None,
                       next_path: str | None, error: str | None, error_code: str | None,
                       verifier: str | None) -> tuple[Session, str]:
        """flow.ts completeCallback without the grant claim (the console's own operation):
        the session and the same-site path to land on."""
        if error or error_code:
            expired = failure(error_code, 400) == "link_expired"
            raise AuthRefused("link_expired" if expired else "link_invalid")
        if code:
            if not verifier:
                raise AuthRefused("link_invalid")
            answer = await self._send("POST", TOKEN, params={"grant_type": "pkce"},
                                      body={"auth_code": code, "code_verifier": verifier})
        elif token_hash and kind in EMAIL_LINK_TYPES:
            answer = await self._send("POST", "/auth/v1/verify",
                                      body={"type": kind, "token_hash": token_hash})
        else:
            raise AuthRefused("link_invalid")
        if answer.status_code != 200:
            raise _link(answer)
        return _session(answer), ("/update-password" if kind == "recovery"
                                  else safe_next(next_path))

    async def settings(self) -> dict[str, Any] | None:
        """The IdP's public settings, or None when it cannot say."""
        try:
            answer = await self._send("GET", "/auth/v1/settings")
            body = answer.json() if answer.status_code == 200 else None
        except (AuthRefused, ValueError):
            return None
        return body if isinstance(body, dict) else None
