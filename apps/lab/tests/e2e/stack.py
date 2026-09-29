"""LAB-E2E's shared backend half: one process per suite on the task-local key l4 (PostgreSQL
57503) serving, on ONE port, the Lab routes and the Supabase endpoints the Lab web calls.

* the Lab routes: R186's control factory (`infrx.lab.control.app:create_app`: `/readyz`,
  `/lab/v1/control` over L3) plus the suite's surface composed by the gateway's own
  `pilot._lab` with that one switch ON in this process only (LAB_EVALS / LAB_PIPELINES /
  LAB_RELEASES); the session verifier is the real `lab_auth.GoTrueSessions`, pointed here.
* the Supabase stand-in (the stack runs no GoTrue or PostgREST, R203/R223's one stand-in):
  `/auth/v1/token` (password sign-in), `/auth/v1/user` and `/auth/v1/logout` for this
  world's users, and `/rest/v1/rpc/<fn>`, which runs the REAL `public.<fn>` of the migrated
  database as `authenticated` with the caller's claims (PostgREST's own impersonation: 0030's
  memberships, 0038's review door, 0043's judge runs), so RLS and each door's own checks
  decide. Nothing here decides an oracle.

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/e2e/<x>/backend.py
    # prints one line: READY <port> <world json>
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

API = Path(os.environ.get("INFRX_API_DIR") or Path(__file__).resolve().parents[3] / "infrx-api")
sys.path.insert(0, str(API))

from fastapi import Request  # noqa: E402 - FastAPI resolves the handlers' annotations here
PASSWORD = "lab-e2e-password"            # task-local literal, the only one this world accepts
SWITCHES = ("lab_control", "lab_traces", "lab_evals", "lab_pipelines", "lab_releases",
            "lab_datasets")


def email(name: str) -> str:
    return f"{name}@lab.e2e"


def token(user: str, exp: int = 4_102_444_800) -> str:
    """A JWT-shaped access token per user (unsigned: only this door ever verifies it)."""
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    claims = {"sub": user, "aud": "authenticated", "role": "authenticated", "exp": exp}
    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.e2e"


def database(name: str):
    """A fresh migrated database on the l4 key: (conn, dsn)."""
    from infrx.state import migrations
    from tests.d import pgharness
    if os.environ.get("INFRX_D_TASK") != "l4":
        raise SystemExit("the l4 task-local key only (INFRX_D_TASK=l4)")
    db = f"{pgharness.DATABASE}_e2e_{name}"
    pgharness.ensure()
    pgharness.recreate(db)
    pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    return pgharness.connect(db), pgharness.dsn(db)


def listen() -> tuple[socket.socket, str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock, f"http://127.0.0.1:{sock.getsockname()[1]}"


def control_app(dsn: str, url: str):
    """R186's factory on its own INFRX_LAB_* settings, its session verifier pointed here."""
    from infrx.lab.control import app as control
    os.environ.update({control.DATABASE_URL: dsn, control.SUPABASE_URL: url,
                       control.SUPABASE_KEY: "anon"})
    for name in ("CLICKHOUSE_URL", "S3_TRACE_BUCKET"):
        os.environ.pop(name, None)
    return control.create_app()


def composed(switch: str, dsn: str, url: str, objects=None) -> dict:
    """The gateway's own Lab composition (`pilot._lab`) with `switch` ON and every other Lab
    switch off: what `LAB_<X>=1` mounts, over this database and this door."""
    from infrx.gateway import pilot
    from infrx.state.jobstore import connector
    deployment = SimpleNamespace(**{s: s == switch for s in SWITCHES}, lab_teachers=False,
                                 lab_checkpoints=False)
    settings = SimpleNamespace(deployment=deployment, supabase_url=url, supabase_key="anon")
    return pilot._lab(settings, connector(dsn), objects)


def door(app, dsn: str, users: dict[str, str]) -> None:
    """GoTrue's three calls and PostgREST's RPC call, for `users` (name -> user id)."""
    import psycopg
    from fastapi.responses import JSONResponse, Response
    from psycopg import sql
    from psycopg.types.json import Jsonb

    from tests.d import checks
    by_email = {email(n): u for n, u in users.items()}
    by_token = {token(u): u for u in users.values()}

    def user_of(request: Request) -> str | None:
        bearer = (request.headers.get("authorization") or "").removeprefix("Bearer ")
        return by_token.get(bearer)

    def profile(user: str) -> dict:
        mail = next(m for m, u in by_email.items() if u == user)
        return {"id": user, "aud": "authenticated", "role": "authenticated", "email": mail,
                "app_metadata": {}, "user_metadata": {}, "created_at": "2026-09-01T00:00:00Z"}

    @app.post("/auth/v1/token")
    async def sign_in(request: Request):
        body = await request.json()
        user = by_email.get(body.get("email"))
        if request.query_params.get("grant_type") != "password" or user is None \
                or body.get("password") != PASSWORD:
            return JSONResponse({"code": 400, "error_code": "invalid_credentials",
                                 "msg": "Invalid login credentials"}, 400)
        return {"access_token": token(user), "token_type": "bearer", "expires_in": 3600,
                "expires_at": 4_102_444_800, "refresh_token": f"refresh-{user}",
                "user": profile(user)}

    @app.get("/auth/v1/user")
    async def who(request: Request):
        user = user_of(request)
        if user is None:
            return JSONResponse({"code": 401, "msg": "invalid JWT"}, 401)
        return profile(user)

    @app.post("/auth/v1/logout")
    async def logout():
        return Response(status_code=204)

    @app.post("/rest/v1/rpc/{fn}")
    async def rpc(fn: str, request: Request):
        user, raw = user_of(request), await request.body()
        args = json.loads(raw) if raw else {}

        def call():
            with psycopg.connect(dsn) as conn:
                conn.execute(checks._jwt(user) if user else "set local role anon")
                found = conn.execute("select p.proretset from pg_proc p join pg_namespace n on "
                                     "n.oid = p.pronamespace where n.nspname = 'public' and "
                                     "p.proname = %s", (fn,)).fetchone()
                if found is None:
                    return 404, {"code": "PGRST202", "message": f"no function public.{fn}"}
                named = sql.SQL(", ").join(sql.SQL("{} => %s").format(sql.Identifier(k))
                                           for k in args)
                call_ = sql.SQL("public.{}({})").format(sql.Identifier(fn), named)
                query = sql.SQL("select coalesce(jsonb_agg(to_jsonb(r)), '[]') from {} r"
                                if found[0] else "select to_jsonb({})").format(call_)
                values = [Jsonb(v) if isinstance(v, (dict, list)) else v for v in args.values()]
                try:
                    return 200, conn.execute(query, values).fetchone()[0]
                except psycopg.Error as refused:
                    return (403 if refused.sqlstate == "42501" else 400), {
                        "code": refused.sqlstate, "message": str(refused.diag.message_primary),
                        "details": None, "hint": None}
        status, body = await asyncio.to_thread(call)
        return JSONResponse(body, status)


def serve(app, sock: socket.socket, world: dict, finally_=lambda: None) -> None:
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))

    async def run():
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            await asyncio.sleep(0.05)
        print(f"READY {sock.getsockname()[1]} {json.dumps(world)}", flush=True)
        await task
    try:
        asyncio.run(run())
    except KeyboardInterrupt:              # uvicorn re-raises the harness's SIGINT once it has stopped
        pass
    finally:
        finally_()
