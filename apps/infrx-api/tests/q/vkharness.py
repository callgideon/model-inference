"""A disposable task-local Valkey for Q2's Layer-2 tests (08 §8).

Container `infrx-q2-valkey` on host port 55461, image pinned by the digest E2's
`tests/integration/compose.yaml` uses, bound to the loopback interface. Nothing here
touches a container this module did not create: the removal hook is registered **only**
when this process started the container, so a mutation subprocess that finds it already
running leaves it alone.

A missing Docker, a missing `valkey` package or an unreachable server is a *skip naming
the reason*, never a pass.

    docker run -d --name infrx-q2-valkey -p 127.0.0.1:55461:6379 \\
      valkey/valkey@sha256:d2e18f… valkey-server --save '' --appendonly no
"""
from __future__ import annotations

import atexit
import dataclasses
import itertools
import os
import shutil
import socket
import subprocess
import time
import uuid

from infrx.contracts.fakes.factories import scheduler_factory
from infrx.contracts.limits import DEFAULTS
from infrx.scheduling.valkey import ValkeyScheduler

# `08` §8 reserves 56379 for track Q; the wave-3 Q2 brief assigns this task the
# task-local port 55461 (Q's task range), which `infrx/contracts/tasklocal.py` does not
# know yet - the coordinator request is in this task's evidence.
CONTAINER = "infrx-q2-valkey"
PORT = int(os.environ.get("INFRX_Q2_VALKEY_PORT", "55461"))
# valkey/valkey:8.1-alpine, the digest in tests/integration/compose.yaml (E2). Pinned so
# a rerun cannot silently move to another server version.
IMAGE = ("valkey/valkey@sha256:"
         "d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1")
URL = f"redis://127.0.0.1:{PORT}/0"

_started = False
_names = itertools.count()


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=check)


def unavailable() -> str | None:
    """Why this suite cannot run, or None."""
    try:
        import valkey                               # noqa: F401
    except ImportError:                             # pragma: no cover - env without extras
        return "the valkey client is not installed (uv sync --all-extras)"
    if _listening():                                # already up: Docker is irrelevant
        return None
    if shutil.which("docker") is None:
        return "docker is not installed"
    probe = _docker("info", "--format", "{{.ServerVersion}}", check=False)
    if probe.returncode != 0:
        return f"docker is not usable: {probe.stderr.strip().splitlines()[-1:]}"
    return None


def _listening() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def ensure() -> None:
    """Start the container if it is not already up, and remove it at exit **if this
    process is the one that started it**."""
    global _started
    if _started:
        return
    if not _listening():
        state = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER, check=False)
        if state.returncode != 0:
            run = _docker("run", "-d", "--name", CONTAINER,
                          "-p", f"127.0.0.1:{PORT}:6379", IMAGE,
                          # disposable by construction: a restart is an empty index,
                          # which is what DUR-OUTBOX's "lose Valkey data" wants to be cheap
                          "valkey-server", "--save", "", "--appendonly", "no", check=False)
            if run.returncode != 0:
                raise RuntimeError(f"could not start {CONTAINER}: {run.stderr.strip()}")
            atexit.register(remove)
        elif state.stdout.strip() != "true":
            _docker("start", CONTAINER)
        _wait_ready()
    _started = True


def _wait_ready(timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _listening():
            return
        time.sleep(0.1)
    raise RuntimeError(f"{CONTAINER} did not accept connections within {timeout_s}s")


def remove() -> None:
    _docker("rm", "-f", "-v", CONTAINER, check=False)


def client(**kw):
    """An async client for this task's server."""
    from valkey.asyncio import Valkey
    ensure()
    return Valkey.from_url(URL, **kw)


def namespace() -> str:
    """A fresh index namespace, so two tests in one process cannot see each other.

    The `{…}` hash tag holds every derived key in one slot, which is what makes the
    per-flow keys safe if this ever runs against a cluster.
    """
    return f"infrx:q2:{{{uuid.uuid4().hex}-{next(_names)}}}"


def harness(limits=None, *, weights=None, cost=None, max_items=None, max_bytes=None,
            namespace_=None, connection=None, **kw):
    """The shared scheduler harness with the *Valkey* adapter in `port`.

    Same shape as `tests/q/support.harness`: clock, ids and the fake JobStore come from
    the coordinator's factory, so the exported conformance cases run against this adapter
    with the same collaborators. Only `port` differs.
    """
    inner = scheduler_factory(limits=limits, **kw)
    settings = limits or DEFAULTS
    extra = {name: value for name, value in {"weights": weights, "cost": cost,
                                             "max_items": max_items,
                                             "max_bytes": max_bytes}.items()
             if value is not None}
    port = ValkeyScheduler(connection or client(), inner.clock.now, limits=settings,
                           namespace=namespace_ or namespace(), **extra)
    return dataclasses.replace(inner, port=port)
