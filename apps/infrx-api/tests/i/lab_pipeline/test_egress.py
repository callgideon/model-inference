#!/usr/bin/env python3
"""I6 (LAB-WORKERS) drill, the security lens: blocked egress fails closed. The container
environment the training unit builds (its env file, then its `-e` flags, which docker applies
last) is given to an `httpx.AsyncClient` built the way P3's `HttpConnector` and B1's dev
endpoint build theirs (env proxies trusted). Two local servers stand in for an allowlisted
and an unlisted host: 127.0.0.1 and 127.0.0.2 (loopback, ephemeral ports; no docker).

    uv run --frozen pytest -q tests/i/lab_pipeline/test_egress.py
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from ..lab_eval.test_units import directive
from .test_units import unit


@contextmanager
def server(host: str):
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer((host, 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://{host}:{httpd.server_address[1]}", hits
    finally:
        httpd.shutdown()
        httpd.server_close()


def container_env(env_file: dict[str, str], role: str = "training") -> dict[str, str]:
    """docker: the env file first, then every `-e` flag (systemd expanded `${NAME}` from the
    same env file)."""
    (start,) = directive(unit(role), "ExecStart")
    env = dict(env_file)
    for name, value in re.findall(r"-e (\w+)=(\S*)", start):
        env[name] = re.sub(r"\$\{(\w+)\}", lambda m: env_file.get(m.group(1), ""), value)
    return env


def apply(env: dict[str, str], monkeypatch) -> None:
    """This process's environment becomes the container's (as far as proxies go)."""
    for name in [n for n in os.environ if n.lower().endswith("_proxy")]:
        monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def reach(env: dict[str, str], monkeypatch, url: str) -> int | str:
    apply(env, monkeypatch)

    async def get():
        async with httpx.AsyncClient(timeout=2) as client:
            return (await client.get(url)).status_code
    try:
        return asyncio.run(get())
    except (httpx.ConnectError, httpx.ProxyError) as blocked:
        return type(blocked).__name__


# An env file that tries every letter case to switch the deny proxy off.
OVERRIDES = {"https_proxy": "", "http_proxy": "", "HTTPS_PROXY": "", "No_Proxy": "*",
             "NO_PROXY": "*", "no_proxy": "*"}


@pytest.mark.parametrize("overrides", [{}, OVERRIDES], ids=["plain", "overrides"])
def test_i6_only_the_allowlisted_host_is_reachable_and_the_env_file_cannot_widen_it(
        overrides, monkeypatch) -> None:
    """Failure oracle: an unlisted host reached (the deny proxy missing, live, or switched off
    by an env file's lowercase or mixed-case proxy setting), or the allowlisted host (the
    object store, an approved endpoint) blocked so the worker cannot run at all."""
    with server("127.0.0.1") as (allowed, allowed_hits), \
            server("127.0.0.2") as (unlisted, unlisted_hits):
        env = container_env({"LAB_EGRESS_ALLOW": "127.0.0.1", **overrides})
        assert reach(env, monkeypatch, allowed + "/ok") == 200
        assert reach(env, monkeypatch, unlisted + "/exfiltrate") in ("ConnectError", "ProxyError")
        assert allowed_hits == ["/ok"] and unlisted_hits == []


def test_i6_an_empty_allowlist_denies_every_http_host(monkeypatch) -> None:
    """The shipped default (no approval, no object-store endpoint named). Failure oracle: an
    empty NO_PROXY read as "no proxy" (everything reachable)."""
    with server("127.0.0.1") as (url, hits):
        assert reach(container_env({}), monkeypatch, url) in ("ConnectError", "ProxyError")
        assert hits == []
