"""Composition root: create_app() builds one independent gateway.

Everything mutable lives on the Runtime — clients, settings, the in-flight
counter, the key caches, the usage queue — so two apps in one process share
nothing and nothing is created at import time.
"""
import time

import httpx
from fastapi import FastAPI

from ..auth.keys import Auth
from ..config import from_env
from ..media.video import Media
from ..usage import Usage
from .routes import chat, health, models


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
        self.client = upstream_client(settings) if client is None else client
        self.sb = supabase_client(settings) if sb is None else sb
        self.inflight = 0
        self.auth = Auth(self)
        self.usage = Usage(self)
        self.media = Media(self)
        self.app = None


def create_app(settings=None, client=None, sb=None, clock=time.time):
    """The FastAPI app. `settings` defaults to the process environment; the
    upstream and Supabase clients and the clock are injectable for tests."""
    rt = Runtime(from_env() if settings is None else settings, client, sb, clock)
    app = FastAPI()
    app.state.runtime = rt
    rt.app = app
    for module in (health, models, chat):
        module.register(app, rt)
    return app
