"""AP-01 01a: `/auth/v1/*`, the auth facade's routes (`infrx.auth_facade`).

Mounted only when the composition root puts an `AuthFacade` on `rt.auth_facade` (a switch,
default off). The routes hold nothing: the IdP keeps every session, Next sets the cookies from
the tokens in the body, and a failure is one of the App's fixed codes (R270 envelope).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import Field, SecretStr

from ...auth_facade import AuthFacade, AuthRefused, Session
from ...console import EnvelopeRoute
from ...contracts import api
from .. import control, lab_auth

EMAIL = Field(min_length=3, max_length=320)
SECRET = Field(min_length=1, max_length=4096)
CAPTCHA = Field(default=None, max_length=4096)
REDIRECT = Field(default=None, max_length=2048)
CHALLENGE = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43,128}$")


class SignIn(api.Wire):
    email: str = EMAIL
    password: SecretStr = SECRET
    captcha_token: str | None = CAPTCHA       # the hosted policy checks sign-in too (LR-02)


class SignUp(SignIn):
    redirect_to: str | None = REDIRECT
    code_challenge: str | None = CHALLENGE


class Recovery(api.Wire):
    email: str = EMAIL
    captcha_token: str | None = CAPTCHA
    redirect_to: str | None = REDIRECT
    code_challenge: str | None = CHALLENGE


class Resend(Recovery):
    """WR-AP09-RESEND: a new sign-up verification link for this address."""


class Refresh(api.Wire):
    refresh_token: SecretStr = SECRET


class NewPassword(api.Wire):
    password: SecretStr = SECRET


class Sent(api.Wire):
    status: Literal["sent"] = "sent"


class Landing(api.Wire):
    session: Session
    redirect: str


class Captcha(api.Wire):
    """LR-02: whether the password doors need a challenge, and the widget a form renders."""

    required: bool
    provider: str | None = None              # `auth_facade.CAPTCHA_PROVIDERS`
    site_key: str | None = None
    state: api.Availability


class AuthAvailability(api.Wire):
    sign_in: api.Availability
    sign_up: api.Availability
    recovery: api.Availability
    signup_grant: api.Availability
    captcha: Captcha


def bearer(request: Request) -> str:
    match = lab_auth.BEARER.fullmatch(request.headers.get("authorization") or "")
    if match is None:
        raise AuthRefused("unauthenticated")
    return match.group(1)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def availability(idp: dict[str, Any] | None, grant: bool | None, required: bool,
                 widget: tuple[str, str] | None) -> AuthAvailability:
    """The IdP's settings, the grant flag and the CAPTCHA policy as R270 availability: a read
    that failed is `unknown`, never `disabled`; a required challenge with no widget to render
    makes every password door `unavailable` (no form could pass it)."""
    at = _now()

    def state(on: bool | None, reason: str) -> api.Availability:
        if on is None:
            return api.Availability(state="unknown", reason="unreachable", verified_at=at)
        if on and required and widget is None:
            return api.Availability(state="unavailable", reason="captcha_unconfigured",
                                    verified_at=at)
        return api.Availability(state="configured" if on else "disabled",
                                reason=None if on else reason, verified_at=at)
    email = None if idp is None else bool((idp.get("external") or {}).get("email"))
    signup = None if idp is None else email and idp.get("disable_signup") is False
    if widget is not None:
        challenge = api.Availability(state="configured", verified_at=at)
    elif required:
        challenge = api.Availability(state="unavailable", reason="site_key_missing",
                                     verified_at=at)
    else:
        challenge = api.Availability(state="disabled", reason="not_required", verified_at=at)
    provider, site_key = widget or (None, None)
    return AuthAvailability(
        sign_in=state(email, "email_provider_disabled"), sign_up=state(signup, "signup_closed"),
        recovery=state(email, "email_provider_disabled"),
        signup_grant=api.Availability(state="unknown", reason="unreachable", verified_at=at)
        if grant is None else api.Availability(state="configured" if grant else "disabled",
                                               reason=None if grant else "flag_off",
                                               verified_at=at),
        captcha=Captcha(required=required, provider=provider, site_key=site_key,
                        state=challenge))


def register(app, rt) -> None:
    facade: AuthFacade | None = getattr(rt, "auth_facade", None)
    if facade is None:
        return
    router = APIRouter(route_class=EnvelopeRoute)

    @router.post("/auth/v1/sign-in", response_model=Session, operation_id="auth_sign_in")
    async def sign_in(body: SignIn, request: Request):
        facade.check_origin(request.headers.get("origin"))
        return control.ok(await facade.sign_in(body.email, body.password.get_secret_value(),
                                               captcha_token=body.captcha_token))

    @router.post("/auth/v1/sign-up", response_model=Sent, operation_id="auth_sign_up")
    async def sign_up(body: SignUp, request: Request):
        facade.check_origin(request.headers.get("origin"))
        await facade.sign_up(body.email, body.password.get_secret_value(),
                             captcha_token=body.captcha_token, redirect_to=body.redirect_to,
                             code_challenge=body.code_challenge)
        return control.ok(Sent())

    @router.post("/auth/v1/recovery", response_model=Sent, operation_id="auth_recovery")
    async def recovery(body: Recovery, request: Request):
        facade.check_origin(request.headers.get("origin"))
        await facade.recover(body.email, captcha_token=body.captcha_token,
                             redirect_to=body.redirect_to, code_challenge=body.code_challenge)
        return control.ok(Sent())

    @router.post("/auth/v1/resend", response_model=Sent, operation_id="auth_resend")
    async def resend(body: Resend, request: Request):
        facade.check_origin(request.headers.get("origin"))
        await facade.resend(body.email, captcha_token=body.captcha_token,
                            redirect_to=body.redirect_to, code_challenge=body.code_challenge)
        return control.ok(Sent())

    @router.post("/auth/v1/refresh", response_model=Session, operation_id="auth_refresh")
    async def refresh(body: Refresh, request: Request):
        facade.check_origin(request.headers.get("origin"))
        return control.ok(await facade.refresh(body.refresh_token.get_secret_value()))

    @router.post("/auth/v1/sign-out", status_code=204, operation_id="auth_sign_out",
                 openapi_extra={"x-infrx-no-body": True})   # the bearer is a header, not a body
    async def sign_out(request: Request):
        facade.check_origin(request.headers.get("origin"))
        await facade.sign_out(bearer(request))
        return Response(status_code=204, headers=dict(control.NO_STORE))

    @router.post("/auth/v1/password", status_code=204, operation_id="auth_password")
    async def password(body: NewPassword, request: Request):
        facade.check_origin(request.headers.get("origin"))
        await facade.update_password(bearer(request), body.password.get_secret_value())
        return Response(status_code=204, headers=dict(control.NO_STORE))

    @router.get("/auth/v1/callback", response_model=Landing, operation_id="auth_callback")
    async def callback(code: str | None = None, token_hash: str | None = None,
                       type: str | None = None, next: str | None = None,       # noqa: A002
                       error: str | None = None, error_code: str | None = None,
                       x_auth_code_verifier: str | None = Header(default=None)):
        session, redirect = await facade.callback(
            code=code, token_hash=token_hash, kind=type, next_path=next, error=error,
            error_code=error_code, verifier=x_auth_code_verifier)
        return control.ok(Landing(session=session, redirect=redirect))

    @router.get("/auth/v1/availability", response_model=AuthAvailability,
                operation_id="auth_availability")
    async def available():
        identity = getattr(rt, "identity", None)
        try:
            grant = await identity.signup_grant_enabled() if identity is not None else None
        except Exception:                         # noqa: BLE001 - unknown, never disabled
            grant = None
        return control.ok(availability(await facade.settings(), grant, facade.captcha_required,
                                       facade.captcha_widget))

    app.router.routes.extend(router.routes)     # the app's own table, as every router (lab_datasets)
