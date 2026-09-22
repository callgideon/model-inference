"""Shared fixtures for the G suite. No network, no sleeps, no wall clock.

Two app shapes, both built without importing the legacy `gateway` shim (r1 R48):

* `cutover_app()` - the app the cutover produces: the ingress router mounted on a
  bare FastAPI with a real `Runtime`, i.e. `create_app` minus the legacy routers.
  Registering the ingress on top of the legacy chat route would leave that route in
  charge of `/v1/chat/completions` (Starlette matches in registration order), which
  would test the wrong handler.
* `legacy_app()` - `create_app()` itself, for the cases about what the composition
  root does today.
"""
from __future__ import annotations

import atexit
import dataclasses
import pathlib
import shutil
import tempfile

import httpx
from fastapi import FastAPI

from infrx.config import Settings, validate_runtime
from infrx.contracts.limits import DEFAULTS
from infrx.gateway.app import Runtime, create_app
from infrx.gateway.routes import ingress

CHAT_PATH = ingress.CHAT_PATH
HEALTH_PATH = ingress.HEALTH_PATH
READY_PATH = ingress.READY_PATH

ORG = "1a1a1a1a-0000-4000-8000-000000000001"
KEY = "3c3c3c3c-0000-4000-8000-000000000003"
USER = "2b2b2b2b-0000-4000-8000-000000000002"
# A consumer key as 0009 stores it: the audience and the individual it belongs to.
ROW = {"id": KEY, "org_id": ORG, "revoked_at": None, "audience": "consumer", "user_id": USER}
REQUEST_ID = "4d4d4d4d-0000-4000-8000-000000000004"
TOKEN = "sk-infrx-g1-test"
AUTH = {"authorization": f"Bearer {TOKEN}"}
# A raw `content=` post carries no content type of its own and the ingress requires
# one; `json=` sets it.
RAW = {**AUTH, "content-type": "application/json"}
# The public model id a caller names, and the revision the store prices. Deliberately
# different strings: copying one into the other is the defect the served-model map
# exists to prevent, and the revision is the one the contracts fake prices.
PUBLIC_MODEL = "nemostation/marlin-2b"
MODEL_REVISION = "nemostation/marlin-2b@2026-09-01"
SERVED_MODELS = {PUBLIC_MODEL: MODEL_REVISION}
BODY = {"model": PUBLIC_MODEL, "messages": [{"role": "user", "content": "hi"}]}
# Per run, and removed when the process exits: a bare `mkdtemp` left one directory
# behind per run *and per mutant subprocess* - 345 of them before this line. The legacy
# chat route does write this file, so the directory has to exist.
_USAGE_DIR = tempfile.mkdtemp(prefix="infrx-g1-")
atexit.register(shutil.rmtree, _USAGE_DIR, ignore_errors=True)
USAGE_LOG = str(pathlib.Path(_USAGE_DIR) / "usage.jsonl")


def supabase(rows=(ROW,), down=False, seen=None):
    """A Supabase stand-in; `seen` collects the GET urls so cache hits are countable."""

    def handler(request):
        if down:
            raise httpx.ConnectError("supabase unreachable")
        if request.method == "GET" and seen is not None:
            seen.append(str(request.url))
        return httpx.Response(200, json=list(rows))

    return httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(handler))


def upstream():
    return httpx.AsyncClient(base_url="http://vllm.local", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [], "usage": {}})))


PILOT_FIELDS = {f.name for f in dataclasses.fields(DEFAULTS)}


def settings(mode="pilot", *, legacy_key="", supabase_url="https://fake.supabase.co",
             supabase_key="service-role", **overrides):
    """A `Settings` whose pilot half names `mode`. Overrides go to whichever half
    owns the name, so a case can set `max_request_bytes` and `key_cache_max` alike."""
    pilot = {"infrx_mode": mode, "database_url": "postgresql:///infrx_g1",
             **{k: v for k, v in overrides.items() if k in PILOT_FIELDS}}
    return Settings(usage_log=USAGE_LOG, legacy_key=legacy_key, supabase_url=supabase_url,
                    supabase_key=supabase_key, pilot=DEFAULTS.replace(**pilot),
                    **{k: v for k, v in overrides.items() if k not in PILOT_FIELDS})


def runtime(config=None, *, sb=None, clock=None, seen=None):
    rt = Runtime(config if config is not None else settings(), client=upstream(),
                 sb=sb if sb is not None else supabase(seen=seen),
                 clock=clock if clock is not None else (lambda: 1_790_000_000.0))
    rt.mode = validate_runtime(rt.settings)
    return rt


def deps(**kw):
    """`IngressDeps` with both startup probes answering and the served model mapped,
    unless overridden."""
    kw.setdefault("checks", {"price_source": lambda: True, "journal": lambda: True})
    kw.setdefault("served_models", SERVED_MODELS)
    return ingress.IngressDeps(**kw)


def cutover_app(config=None, *, sb=None, clock=None, seen=None, ingress_deps=None):
    """(app, ingress) as the cutover mounts them."""
    rt = runtime(config, sb=sb, clock=clock, seen=seen)
    app = FastAPI()
    app.state.runtime = rt
    rt.app = app
    mounted = ingress.register(app, rt, ingress_deps if ingress_deps is not None else deps())
    return app, mounted


def legacy_app(config=None):
    return create_app(config if config is not None else Settings(usage_log=USAGE_LOG),
                      client=upstream(), sb=supabase(), clock=lambda: 1_790_000_000.0)


def recorder():
    """An `accept` that records what the ingress handed it and answers 202."""
    from fastapi.responses import JSONResponse

    calls = []

    async def accept(auth, request, idem):
        calls.append((auth, request, idem))
        return JSONResponse({"accepted": True}, status_code=202)

    return calls, accept


def error_of(response):
    return response.json()["error"]
