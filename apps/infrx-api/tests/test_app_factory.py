#!/usr/bin/env python3
"""The application factory: no import-time side effects, no state shared between
apps, an injectable clock, the deployment import path, and the legacy globals
gateway.py still exposes.

    python3 -m pytest apps/infrx-api/tests/test_app_factory.py
    python3 apps/infrx-api/tests/test_app_factory.py     # same checks, no pytest

No network: vLLM and Supabase are httpx.MockTransport. `gateway` is imported
inside the one test that needs it, not at module level: test_gateway_auth.py sets
the environment before its own `import gateway`, and this file sorts first.
"""
import asyncio, os, subprocess, sys, time

USAGE_LOG = "/tmp/gw-test-usage.jsonl"
os.environ.setdefault("USAGE_LOG", USAGE_LOG)
API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, API_DIR)

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.config import Settings, from_env
from infrx.gateway.app import create_app

BODY = {"model": "marlin2b", "messages": [{"role": "user", "content": "hi"}]}
OK = {"choices": [{"message": {"role": "assistant", "content": "hello"}}],
      "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
ROW = {"id": "11111111-1111-1111-1111-111111111111", "org_id": "22222222-2222-2222-2222-222222222222",
       "revoked_at": None}

# Imports the package under a scrubbed environment, with os.environ replaced by a
# recorder: an import-time read or a client/app built at import shows up here.
PROBE = r"""
import sys
sys.path.insert(0, %r)
# every stdlib module the package imports, plus httpx/fastapi: loaded before the
# recorder, so only infrx's own environment reads can be recorded.
import asyncio, base64, collections, dataclasses, hashlib, hmac, ipaddress, json, os
import re, socket, subprocess, tempfile, time, uuid
import fastapi, httpx

class Recorder(dict):
    def __init__(self, env):
        dict.__init__(self, env)
        self.reads = []
    def __getitem__(self, k):
        self.reads.append(k)
        return dict.__getitem__(self, k)
    def get(self, k, d=None):
        self.reads.append(k)
        return dict.get(self, k, d)

os.environ = rec = Recorder(os.environ)
import infrx.config, infrx.usage, infrx.auth.keys, infrx.media.video, infrx.gateway.app
import infrx.gateway.routes.chat, infrx.gateway.routes.health, infrx.gateway.routes.models
built = sorted(m + "." + k for m in list(sys.modules) if m.startswith("infrx")
               for k, v in vars(sys.modules[m]).items()
               if isinstance(v, (fastapi.FastAPI, httpx.AsyncClient)))
print(repr((rec.reads, built)))
"""

SMOKE = ("import fastapi, uvicorn.importer;"
         "app = uvicorn.importer.import_from_string('gateway:app');"
         "assert isinstance(app, fastapi.FastAPI), type(app);"
         "print(sorted(r.path for r in app.routes if hasattr(r, 'path')))")


class Req:
    def __init__(self, token):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}


def run(code, cwd=None):
    """A subprocess with nothing but PATH in its environment."""
    return subprocess.run([sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True,
                          env={"PATH": os.environ.get("PATH", "")})


def mock_app(sb_handler=None, clock=time.time, **kw):
    """create_app() with a mocked upstream and Supabase: no network, no env."""
    return create_app(
        Settings(usage_log=USAGE_LOG, **kw),
        client=httpx.AsyncClient(base_url="http://vllm.local",
                                 transport=httpx.MockTransport(lambda request: httpx.Response(200, json=OK))),
        sb=httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(sb_handler or (lambda request: httpx.Response(200, json=[])))),
        clock=clock)


def test_importing_the_package_has_no_side_effects():
    """No environment read, no client and no app built at import time."""
    p = run(PROBE % API_DIR)
    assert p.returncode == 0, p.stderr
    reads, built = eval(p.stdout)
    assert reads == [], reads
    assert built == [], built


def test_usage_failed_log_derives_only_when_unset():
    """USAGE_FAILED_LOG="" stayed "" in the old gateway (spill then writes nothing
    and logs the empty path); only an *unset* variable derives from USAGE_LOG."""
    assert from_env({"USAGE_LOG": "/x/y/u.jsonl"}).usage_failed_log == "/x/y/usage_failed.jsonl"
    assert from_env({"USAGE_LOG": "/x/y/u.jsonl", "USAGE_FAILED_LOG": ""}).usage_failed_log == ""
    assert from_env({"USAGE_FAILED_LOG": "/z/f.jsonl"}).usage_failed_log == "/z/f.jsonl"


def test_two_apps_share_no_state():
    a, b = mock_app(), mock_app()
    ra, rb = a.state.runtime, b.state.runtime
    assert ra.settings is not rb.settings
    assert ra.auth.keys is not rb.auth.keys and ra.auth.misses is not rb.auth.misses
    assert ra.usage.q is not rb.usage.q
    ra.inflight = ra.settings.max_inflight               # app a is full; app b is not
    with TestClient(a) as tc:
        assert tc.post("/v1/chat/completions", json=BODY).status_code == 429
    with TestClient(b) as tc:
        assert tc.post("/v1/chat/completions", json=BODY).status_code == 200
    assert (ra.inflight, rb.inflight) == (ra.settings.max_inflight, 0)


def test_injected_clock_drives_key_cache_expiry():
    """No sleeping: the cache expires because the clock says so."""
    now, gets = [1_000.0], []

    def sb(request):
        if request.method == "GET":
            gets.append(str(request.url))
        return httpx.Response(200, json=[ROW])

    rt = mock_app(sb_handler=sb, clock=lambda: now[0],
                  supabase_url="https://fake.supabase.co").state.runtime

    async def go():
        assert (await rt.auth.authenticate(Req("sk-clock")))[1] is None
        assert len(gets) == 1, gets
        now[0] += rt.settings.key_ttl - 1                # still inside the TTL: cached
        assert (await rt.auth.authenticate(Req("sk-clock")))[1] is None
        assert len(gets) == 1, gets
        now[0] += 2                                      # past it: one more lookup
        assert (await rt.auth.authenticate(Req("sk-clock")))[1] is None
        assert len(gets) == 2, gets
        me = asyncio.current_task()
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not me])   # the touch() tasks
    asyncio.run(go())


def test_deployment_import_smoke():
    """`uvicorn gateway:app` from apps/infrx-api, with an empty environment."""
    p = run(SMOKE, cwd=API_DIR)
    assert p.returncode == 0, p.stderr
    assert "/v1/chat/completions" in p.stdout and "/health" in p.stdout, p.stdout


def test_legacy_globals_reach_the_default_app():
    """gateway.<NAME> = x must change what the default app does, not just a copy."""
    import gateway

    names = ("client", "MAX_INFLIGHT", "LEGACY_KEY", "SUPABASE_URL", "USAGE_LOG", "inflight",
             "resolve_public")
    saved = {n: getattr(gateway, n) for n in names}
    try:
        assert isinstance(gateway.app, FastAPI)
        gateway.client = httpx.AsyncClient(base_url="http://vllm.local",
                                           transport=httpx.MockTransport(lambda r: httpx.Response(200, json=OK)))
        gateway.LEGACY_KEY = gateway.SUPABASE_URL = ""       # unauthenticated
        gateway.USAGE_LOG = USAGE_LOG
        gateway.inflight = 0
        with TestClient(gateway.app) as tc:                  # the injected client served it
            assert tc.post("/v1/chat/completions", json=BODY).status_code == 200
        assert gateway.inflight == 0, gateway.inflight       # and the live counter is readable

        gateway.MAX_INFLIGHT = 0                             # the limiter reads the new value
        with TestClient(gateway.app) as tc:
            assert tc.post("/v1/chat/completions", json=BODY).status_code == 429

        seen = []                                            # and so does the media fetcher

        async def resolve(host):
            seen.append(host)
            return None, "dns"

        gateway.resolve_public = resolve
        with open("/dev/null", "wb") as f:
            assert asyncio.run(gateway.fetch_video("https://cdn.test/a.mp4", f))[1] == "dns"
        assert seen == ["cdn.test"], seen
    finally:
        for name, value in saved.items():
            setattr(gateway, name, value)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
