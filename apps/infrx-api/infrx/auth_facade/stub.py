"""A local stand-in for the project's auth server (Supabase Auth / GoTrue REST), for tests only.

It serves exactly the endpoints `infrx.auth_facade.AuthFacade` and `gateway.lab_auth.GoTrueSessions`
call, with hosted GoTrue's error shape (`{code, error_code, msg}`), in memory. Mount it under an
`httpx.ASGITransport` (or uvicorn) and point a client's `base_url` at it. Never composed in a
deployment: no composition root imports this module.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import secrets
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


def jwt_like() -> str:
    """Three base64url segments: the shape `lab_auth.BEARER` accepts, signed by nobody."""
    return ".".join("eyJ" + secrets.token_urlsafe(12) for _ in range(3))


def s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


@dataclasses.dataclass
class User:
    id: str
    email: str
    password: str
    confirmed: bool = True


class GoTrueStub:
    """In-memory users, sessions, PKCE codes and email-link token hashes. The switches model
    the hosted service's states: `down` (5xx), `limited` (429), `disable_signup`, `captcha`."""

    def __init__(self, apikey: str = "anon-key") -> None:
        self.apikey = apikey
        self.users: dict[str, User] = {}
        self.access: dict[str, str] = {}
        self.refresh: dict[str, str] = {}
        self.codes: dict[str, tuple[str, str]] = {}
        self.links: dict[str, tuple[str, str]] = {}
        self.down = self.limited = self.disable_signup = self.captcha = False
        #: (method, path, query, json body) of every call, for the forwarding assertions.
        self.seen: list[tuple[str, str, dict, dict]] = []
        self.app = self._app()

    # --- fixtures --------------------------------------------------------------------------
    def user(self, email: str, password: str = "correct horse 1", *, confirmed: bool = True,
             user_id: str | None = None) -> User:
        made = User(user_id or str(uuid.uuid4()), email, password, confirmed)
        self.users[email] = made
        return made

    def session(self, user_id: str) -> dict:
        access, refresh = jwt_like(), secrets.token_urlsafe(16)
        self.access[access], self.refresh[refresh] = user_id, user_id
        return {"access_token": access, "token_type": "bearer", "expires_in": 3600,
                "expires_at": 1_900_000_000, "refresh_token": refresh,
                "user": {"id": user_id, "aud": "authenticated", "role": "authenticated"}}

    def code(self, user_id: str, verifier: str) -> str:
        made = secrets.token_urlsafe(12)
        self.codes[made] = (user_id, s256(verifier))
        return made

    def link(self, user_id: str, kind: str) -> str:
        made = secrets.token_hex(16)
        self.links[made] = (user_id, kind)
        return made

    # --- the service -----------------------------------------------------------------------
    def _app(self) -> FastAPI:
        app = FastAPI()
        stub = self

        def refuse(status: int, code: str) -> JSONResponse:
            return JSONResponse({"code": status, "error_code": code, "msg": code}, status)

        def bearer(request: Request) -> str | None:
            value = request.headers.get("authorization", "")
            return stub.access.get(value.removeprefix("Bearer ")) if value.startswith("Bearer ") \
                else None

        @app.middleware("http")
        async def gate(request: Request, call_next):
            body = {}
            if request.method in ("POST", "PUT"):
                raw = await request.body()
                body = json.loads(raw) if raw else {}
            stub.seen.append((request.method, request.url.path, dict(request.query_params), body))
            if stub.down:
                return JSONResponse({"message": "upstream unavailable"}, 503)
            if request.headers.get("apikey") != stub.apikey:
                return JSONResponse({"message": "No API key found in request"}, 401)
            if stub.limited:
                return refuse(429, "over_request_rate_limit")
            return await call_next(request)

        @app.get("/auth/v1/settings")
        async def settings():
            return {"external": {"email": True}, "disable_signup": stub.disable_signup,
                    "mailer_autoconfirm": False}

        @app.post("/auth/v1/token")
        async def token(request: Request):
            grant, body = request.query_params.get("grant_type"), await request.json()
            if grant == "password":
                if stub.captcha and (body.get("gotrue_meta_security") or {}).get(
                        "captcha_token") != "captcha-ok":
                    return refuse(400, "captcha_failed")
                user = stub.users.get(body.get("email", ""))
                if user is None:            # an older auth server's answer: the App maps it
                    return refuse(400, "user_not_found")      # like a wrong password
                if user.password != body.get("password"):
                    return refuse(400, "invalid_credentials")
                if not user.confirmed:
                    return refuse(400, "email_not_confirmed")
                return stub.session(user.id)
            if grant == "refresh_token":
                user_id = stub.refresh.pop(body.get("refresh_token", ""), None)
                return refuse(400, "refresh_token_not_found") if user_id is None \
                    else stub.session(user_id)
            if grant == "pkce":
                found = stub.codes.pop(body.get("auth_code", ""), None)
                if found is None:
                    return refuse(404, "flow_state_not_found")
                if s256(body.get("code_verifier", "")) != found[1]:
                    return refuse(400, "bad_code_verifier")
                return stub.session(found[0])
            return refuse(400, "unsupported_grant_type")

        @app.post("/auth/v1/signup")
        async def signup(request: Request):
            body = await request.json()
            if stub.captcha and (body.get("gotrue_meta_security") or {}).get(
                    "captcha_token") != "captcha-ok":
                return refuse(400, "captcha_failed")
            if stub.disable_signup:
                return refuse(422, "signup_disabled")
            email = body.get("email", "")
            if "@" not in email:
                return refuse(400, "email_address_invalid")
            if len(body.get("password", "")) < 8:
                return refuse(422, "weak_password")
            if email in stub.users:
                return refuse(422, "user_already_exists")
            stub.user(email, body["password"], confirmed=False)
            return {"id": stub.users[email].id}

        @app.post("/auth/v1/recover")
        async def recover(request: Request):
            body = await request.json()
            if stub.captcha and (body.get("gotrue_meta_security") or {}).get(
                    "captcha_token") != "captcha-ok":
                return refuse(400, "captcha_failed")
            if body.get("email") not in stub.users:   # an older server's answer: the App
                return refuse(400, "user_not_found")     # reads it as sent
            return {}

        @app.post("/auth/v1/resend")
        async def resend(request: Request):
            body = await request.json()
            if stub.captcha and (body.get("gotrue_meta_security") or {}).get(
                    "captcha_token") != "captcha-ok":
                return refuse(400, "captcha_failed")
            if body.get("type") != "signup":
                return refuse(400, "validation_failed")
            user = stub.users.get(body.get("email", ""))
            if user is None:                          # an older server's answers: the App
                return refuse(400, "user_not_found")  # reads both as sent
            if user.confirmed:
                return refuse(422, "email_exists")
            return {}

        @app.post("/auth/v1/verify")
        async def verify(request: Request):
            body = await request.json()
            found = stub.links.pop(body.get("token_hash", ""), None)
            if found is None or found[1] != body.get("type"):
                return refuse(403, "otp_expired")
            return stub.session(found[0])

        @app.post("/auth/v1/logout")
        async def logout(request: Request):
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if stub.access.pop(token, None) is None:
                return refuse(403, "session_not_found")
            return Response(status_code=204)

        @app.get("/auth/v1/user")
        async def get_user(request: Request):
            user_id = bearer(request)
            if user_id is None:
                return refuse(401, "bad_jwt")
            email = next((u.email for u in stub.users.values() if u.id == user_id), None)
            return {"id": user_id, "aud": "authenticated", "role": "authenticated",
                    "email": email}

        @app.put("/auth/v1/user")
        async def put_user(request: Request):
            user_id = bearer(request)
            if user_id is None:
                return refuse(401, "bad_jwt")
            password = (await request.json()).get("password", "")
            user = next(u for u in stub.users.values() if u.id == user_id)
            if len(password) < 8:
                return refuse(422, "weak_password")
            if password == user.password:
                return refuse(422, "same_password")
            user.password = password
            return {"id": user_id}

        return app
