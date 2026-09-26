"""E3A: the Supabase edge the App talks to, over the composed E3C world.

The App reaches "Supabase" at one origin (`NEXT_PUBLIC_SUPABASE_URL`): `/auth/v1/*` for the
auth server and `/rest/v1/*` for PostgREST. The E3C world has PostgREST (the journey clone's,
`stack.journey_postgrest`) but no auth server, and no GoTrue image is pinned here, so this
module is that origin:

* `/auth/v1/*` - the slice of GoTrue the App's supabase-js calls (signup, verify by
  `token_hash`, password and refresh-token grants, user, logout, resend, recover). A signup
  writes `auth.users` on the clone (0001's trigger makes the profile and personal
  organization), a verification sets `email_confirmed_at` there - what A1's
  `claim_signup_grant` derives verification from. Access tokens are HS256 under the journey
  PostgREST's own local secret (`stack.JWT_SECRET`), so RLS sees the real `auth.uid()`.
  Passwords live in this process only (PBKDF2); "email" is a mailbox the control API reads.
* `/rest/v1/*` - a plain proxy to the journey PostgREST. **`freeze=True` is the break-seam
  `fixture-port`**: each read is answered with the first answer it ever got (a recorded
  fixture) instead of the live database - a mocked C0 port, which the journey must FAIL.

Loopback only; nothing here knows a hosted URL or key.
"""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

API_VERSION = "2024-01-01"
# Request headers PostgREST reads, and the response headers the App's client reads back.
FORWARD = ("authorization", "apikey", "content-type", "accept", "prefer", "range",
           "range-unit", "accept-profile", "content-profile")
BACK = ("content-type", "content-range", "preference-applied", "location")
# Reads the App makes through PostgREST that a frozen port replays (never a write, never
# the grant: the mocked port is a READ port).
FROZEN_RPCS = ("console_", "consumer_")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(claims: dict, secret: str) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(mac)}"


def verify(token: str, secret: str, now: float | None = None) -> dict | None:
    """The claims of a token this edge signed and that has not expired, else None."""
    try:
        head, body, mac = token.split(".")
        want = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(want), mac):
            return None
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    return claims if claims.get("exp", 0) > (time.time() if now is None else now) else None


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)


def _iso(at: float | None) -> str | None:
    return None if at is None else datetime.fromtimestamp(at, timezone.utc).isoformat()


class Edge:
    """The auth server's state (users, one-use email tokens, sessions) and the proxy."""

    def __init__(self, dsn: str, rest_url: str, secret: str, app_origin: str, *,
                 access_ttl_s: int = 3600, freeze: bool = False) -> None:
        self.dsn, self.rest_url, self.secret = dsn, rest_url.rstrip("/"), secret
        self.app_origin, self.access_ttl_s = app_origin, access_ttl_s
        self.users: dict[str, dict] = {}              # email -> user
        self.tokens: dict[str, dict] = {}             # token_hash -> {email, used}
        self.refresh: dict[str, tuple[str, str]] = {}  # refresh token -> (email, session)
        self.sessions: set[str] = set()
        self.mail: dict[str, list[str]] = collections.defaultdict(list)
        self.stats: collections.Counter = collections.Counter()
        self.frozen: dict | None = {} if freeze else None
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ the auth server

    def user_json(self, user: dict) -> dict:
        confirmed = _iso(user["confirmed_at"])
        return {"id": user["id"], "aud": "authenticated", "role": "authenticated",
                "email": user["email"], "phone": "", "email_confirmed_at": confirmed,
                "confirmed_at": confirmed, "confirmation_sent_at": _iso(user["created_at"]),
                "last_sign_in_at": _iso(user.get("signed_in_at")),
                "app_metadata": {"provider": "email", "providers": ["email"]},
                "user_metadata": {}, "identities": [], "is_anonymous": False,
                "created_at": _iso(user["created_at"]), "updated_at": _iso(user["created_at"])}

    def session(self, user: dict) -> dict:
        now, sid = int(time.time()), str(uuid.uuid4())
        user["signed_in_at"] = now
        access = sign({"aud": "authenticated", "iat": now, "exp": now + self.access_ttl_s,
                       "iss": f"{self.app_origin}/auth/v1", "sub": user["id"],
                       "email": user["email"], "phone": "", "role": "authenticated",
                       "aal": "aal1", "amr": [{"method": "password", "timestamp": now}],
                       "session_id": sid, "is_anonymous": False}, self.secret)
        refresh = secrets.token_urlsafe(24)
        self.sessions.add(sid)
        self.refresh[refresh] = (user["email"], sid)
        return {"access_token": access, "token_type": "bearer", "expires_in": self.access_ttl_s,
                "expires_at": now + self.access_ttl_s, "refresh_token": refresh,
                "user": self.user_json(user)}

    def _db(self, sql: str, *args) -> None:
        import psycopg
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(sql, args)

    def signup(self, body: dict, redirect_to: str | None) -> tuple[int, dict]:
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", ""))
        if "@" not in email:
            return 400, error("email_address_invalid", "invalid email")
        if len(password) < 8:
            return 422, {**error("weak_password", "weak password"),
                         "weak_password": {"reasons": ["length"]}}
        with self.lock:
            user = self.users.get(email)
            if user is None:
                salt = secrets.token_bytes(16)
                user = {"id": str(uuid.uuid4()), "email": email, "salt": salt,
                        "hash": _hash(password, salt), "created_at": time.time(),
                        "confirmed_at": None}
                self._db("insert into auth.users (id, email) values (%s, %s)", user["id"], email)
                self.users[email] = user
                self.stats["signups"] += 1
            elif user["confirmed_at"] is not None:        # GoTrue: same answer, no email
                self.stats["signup_existing"] += 1
                return 200, self.user_json(user)
            self._send_link(email, redirect_to)
        return 200, self.user_json(user)

    def _send_link(self, email: str, redirect_to: str | None, kind: str = "signup") -> None:
        """Mail a one-use `token_hash` link to the App's callback (GoTrue's email template
        form `{{ .RedirectTo }}&token_hash={{ .TokenHash }}&type=...`), allowlisted to the App."""
        target = redirect_to if redirect_to and redirect_to.startswith(self.app_origin + "/") \
            else f"{self.app_origin}/auth/callback"
        token = secrets.token_hex(28)
        self.tokens[token] = {"email": email, "used": False, "kind": kind}
        joiner = "&" if "?" in target else "?"
        self.mail[email].append(f"{target}{joiner}{urlencode({'token_hash': token, 'type': kind})}")

    def resend(self, body: dict, redirect_to: str | None) -> tuple[int, dict]:
        email = str(body.get("email", "")).strip().lower()
        with self.lock:
            user = self.users.get(email)
            if user is not None and user["confirmed_at"] is None:
                self._send_link(email, redirect_to)
        return 200, {}

    def recover(self, body: dict, redirect_to: str | None) -> tuple[int, dict]:
        """A recovery link for a known address; the same empty answer for any other."""
        email = str(body.get("email", "")).strip().lower()
        with self.lock:
            if email in self.users:
                self._send_link(email, redirect_to, "recovery")
        return 200, {}

    def verify_token(self, body: dict) -> tuple[int, dict]:
        with self.lock:
            entry = self.tokens.get(str(body.get("token_hash", "")))
            kind = str(body.get("type", ""))
            if entry is None or entry["used"] or kind not in (
                    entry["kind"], *(("email",) if entry["kind"] == "signup" else ())):
                self.stats["verify_refused"] += 1
                return 403, error("otp_expired", "Email link is invalid or has expired")
            entry["used"] = True
            user = self.users[entry["email"]]
            if user["confirmed_at"] is None:
                user["confirmed_at"] = time.time()
                self._db("update auth.users set email_confirmed_at = infrx.now() where id = %s",
                         user["id"])
            self.stats["verified"] += 1
            return 200, self.session(user)

    def token(self, grant: str, body: dict) -> tuple[int, dict]:
        with self.lock:
            if grant == "password":
                user = self.users.get(str(body.get("email", "")).strip().lower())
                if user is None or not hmac.compare_digest(
                        user["hash"], _hash(str(body.get("password", "")), user["salt"])):
                    return 400, error("invalid_credentials", "Invalid login credentials")
                if user["confirmed_at"] is None:
                    return 400, error("email_not_confirmed", "Email not confirmed")
                self.stats["password_grants"] += 1
                return 200, self.session(user)
            if grant == "refresh_token":
                found = self.refresh.get(str(body.get("refresh_token", "")))
                if found is None or found[1] not in self.sessions:
                    return 400, error("refresh_token_not_found", "Invalid Refresh Token")
                self.stats["refresh_grants"] += 1
                return 200, self.session(self.users[found[0]])
        return 400, error("validation_failed", "unsupported grant_type")

    def bearer(self, authorization: str | None) -> tuple[dict | None, dict | None]:
        token = (authorization or "").removeprefix("Bearer ").strip()
        claims = verify(token, self.secret) if token else None
        if claims is None or claims.get("role") != "authenticated":
            return None, None
        user = next((u for u in self.users.values() if u["id"] == claims.get("sub")), None)
        return claims, user

    def get_user(self, authorization: str | None) -> tuple[int, dict]:
        claims, user = self.bearer(authorization)
        if user is None:
            return 403, error("bad_jwt", "invalid JWT")
        if claims.get("session_id") not in self.sessions:
            return 403, error("session_not_found", "Session from session_id claim in JWT "
                                                   "does not exist")
        return 200, self.user_json(user)

    def logout(self, authorization: str | None) -> None:
        claims, _ = self.bearer(authorization)
        if claims:
            with self.lock:
                self.sessions.discard(claims.get("session_id"))

    # ------------------------------------------------------------------ the read port

    def frozen_key(self, method: str, path: str, query: str, body: bytes,
                   authorization: str | None) -> tuple | None:
        """The fixture key of a READ (None: a write, or not frozen)."""
        if self.frozen is None:
            return None
        if method == "GET" or (method == "POST" and path.startswith("rpc/")
                               and path[4:].startswith(FROZEN_RPCS)):
            claims = verify((authorization or "").removeprefix("Bearer ").strip(), self.secret)
            return method, path, query, body, (claims or {}).get("sub")
        return None


def error(code: str, message: str) -> dict:
    return {"code": code, "error_code": code, "msg": message, "message": message}


def app(edge: Edge):
    """The ASGI app for the edge origin (CORS for the App's browser client only)."""
    import httpx
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    head = {"x-supabase-api-version": API_VERSION}
    client = httpx.AsyncClient(timeout=60.0)

    def answer(pair):
        status, body = pair
        return JSONResponse(body, status_code=status, headers=head)

    async def signup(request):
        return answer(edge.signup(await request.json(), request.query_params.get("redirect_to")))

    async def resend(request):
        return answer(edge.resend(await request.json(), request.query_params.get("redirect_to")))

    async def recover(request):
        return answer(edge.recover(await request.json(), request.query_params.get("redirect_to")))

    async def verify_route(request):
        return answer(edge.verify_token(await request.json()))

    async def token(request):
        return answer(edge.token(request.query_params.get("grant_type", ""),
                                 await request.json()))

    async def user(request):
        return answer(edge.get_user(request.headers.get("authorization")))

    async def logout(request):
        edge.logout(request.headers.get("authorization"))
        return Response(status_code=204, headers=head)

    async def settings(request):
        return JSONResponse({"external": {"email": True}, "disable_signup": False,
                             "mailer_autoconfirm": False}, headers=head)

    async def rest(request):
        path, query, body = request.path_params["path"], request.url.query, await request.body()
        authorization = request.headers.get("authorization")
        key = edge.frozen_key(request.method, path, query, body, authorization)
        if key is not None and key in edge.frozen:
            edge.stats["frozen_replays"] += 1
            content, status, headers = edge.frozen[key]
            return Response(content, status_code=status, headers=headers)
        upstream = await client.request(
            request.method, f"{edge.rest_url}/{path}" + (f"?{query}" if query else ""),
            content=body, headers={k: v for k, v in request.headers.items()
                                   if k.lower() in FORWARD})
        headers = {k: v for k, v in upstream.headers.items() if k.lower() in BACK}
        if key is not None and upstream.status_code < 300:
            edge.frozen[key] = (upstream.content, upstream.status_code, headers)
        edge.stats["rest"] += 1
        if path.startswith("rpc/"):
            edge.stats[f"{path}:{upstream.status_code}"] += 1
        return Response(upstream.content, status_code=upstream.status_code, headers=headers)

    routes = [Route("/auth/v1/signup", signup, methods=["POST"]),
              Route("/auth/v1/resend", resend, methods=["POST"]),
              Route("/auth/v1/recover", recover, methods=["POST"]),
              Route("/auth/v1/verify", verify_route, methods=["POST"]),
              Route("/auth/v1/token", token, methods=["POST"]),
              Route("/auth/v1/user", user, methods=["GET"]),
              Route("/auth/v1/logout", logout, methods=["POST"]),
              Route("/auth/v1/settings", settings, methods=["GET"]),
              Route("/rest/v1/{path:path}", rest,
                    methods=["GET", "POST", "PATCH", "DELETE", "HEAD"])]
    return Starlette(routes=routes, middleware=[Middleware(
        CORSMiddleware, allow_origins=[edge.app_origin], allow_methods=["*"],
        allow_headers=["*"], expose_headers=["content-range", "x-supabase-api-version"])])


class Served:
    """An ASGI app on 127.0.0.1:<port> in a daemon thread of this process."""

    def __init__(self, asgi, port: int, name: str) -> None:
        import uvicorn
        self.port, self.name = port, name
        self.server = uvicorn.Server(uvicorn.Config(asgi, host="127.0.0.1", port=port,
                                                    log_level="warning", access_log=False))
        self.thread = threading.Thread(target=self.server.run, name=name, daemon=True)

    def __enter__(self) -> "Served":
        self.thread.start()
        end = time.monotonic() + 20
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > end:
                raise RuntimeError(f"{self.name} did not start on 127.0.0.1:{self.port}")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
