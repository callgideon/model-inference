"""Composition root: create_app() builds one independent gateway.

Everything mutable lives on the Runtime — clients, settings, the in-flight
counter, the key caches, the usage queue — so two apps in one process share
nothing and nothing is created at import time.
"""
import time

import httpx
from fastapi import FastAPI

from ..auth.keys import Auth
from ..config import from_env, validate_runtime
from ..media.video import Media
from ..usage import Usage
from .routes import chat, health, models

# The composition root's router list, fixed and documented (r1 R44). A track's router is
# a module exposing `register(app, rt)`; the coordinator adds it here on an integration
# request, which is why the list is a literal rather than a discovery walk - an
# import-time scan would let a half-finished track mount itself on the public gateway.
ROUTERS = (health, models, chat)


def upstream_client(settings):
    return httpx.AsyncClient(base_url=settings.upstream, timeout=httpx.Timeout(600, connect=10))


def supabase_client(settings):
    return httpx.AsyncClient(base_url=f"{settings.supabase_url}/rest/v1", timeout=httpx.Timeout(5, connect=2),
                             headers={"apikey": settings.supabase_key,
                                      "Authorization": f"Bearer {settings.supabase_key}",
                                      "Content-Type": "application/json"})


class Runtime:
    """One app's state and collaborators. `clock` is a callable returning epoch
    seconds: it drives cache expiry, last-used throttling, price TTL and request
    timing, so tests can move time without sleeping."""

    def __init__(self, settings, client=None, sb=None, clock=time.time):
        self.settings = settings
        self.clock = clock
        self.mode = "legacy"            # r1 R44; create_app replaces it with the validated mode
        self.client = upstream_client(settings) if client is None else client
        self.sb = supabase_client(settings) if sb is None else sb
        self.inflight = 0
        self.auth = Auth(self)
        self.usage = Usage(self)
        self.media = Media(self)
        self.app = None


def create_app(settings=None, client=None, sb=None, clock=time.time):
    """The FastAPI app. `settings` defaults to the process environment; the
    upstream and Supabase clients and the clock are injectable for tests.

    r1 R44: one coordinator-owned hook, `config.validate_runtime`, decides whether this
    configuration may serve at all. With `INFRX_MODE` unset it logs `legacy` and changes
    nothing, so the F1 entry point keeps its behaviour; `pilot` without authentication or
    metering, and any unrecognised mode, refuse to start.
    """
    rt = Runtime(from_env() if settings is None else settings, client, sb, clock)
    rt.mode = validate_runtime(rt.settings)
    app = FastAPI()
    app.state.runtime = rt
    rt.app = app
    for module in ROUTERS:
        module.register(app, rt)
    return app
