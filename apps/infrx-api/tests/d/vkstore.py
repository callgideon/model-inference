"""D2's own disposable Valkey, for the outbox relay against Q2's adapter (DUR-OUTBOX).

Container `infrx-d2-valkey` on 127.0.0.1:55463 (`INFRX_D2_VALKEY_PORT` overrides; not yet
in `tasklocal.TASK_PORTS` - integration request), the image digest Q2 and E2 pin, no
persistence (a restart IS the "lose Valkey data" drill). The same three safeguards as
`pgharness`: a host lock on the port, a checkout label, and only a container THIS run
created is ever removed. A foreign container on the name or port is refused, not used.
"""
from __future__ import annotations

import atexit
import fcntl
import os
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from . import pgharness

CONTAINER = "infrx-d2-valkey"
PORT = int(os.environ.get("INFRX_D2_VALKEY_PORT", "55463"))
IMAGE = ("valkey/valkey@sha256:"
         "d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1")
LABEL = pgharness.CHECKOUT_LABEL
_created = False
_lock_fd: int | None = None


class HarnessBusy(RuntimeError):
    pass


def unavailable() -> str | None:
    try:
        import valkey                               # noqa: F401
    except ImportError:                             # pragma: no cover
        return "the valkey client is not installed (uv sync --all-extras)"
    return pgharness.unavailable()


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=False)


def _lock() -> None:
    global _lock_fd
    if _lock_fd is not None:
        return
    path = Path(tempfile.gettempdir()) / f"{CONTAINER}-{PORT}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise HarnessBusy(f"another run holds {path}; nothing was altered") from None
    _lock_fd = fd


def ensure() -> None:
    global _created
    if _created:
        return
    _lock()
    probe = _docker("inspect", "-f", "{{json .Config.Labels}}", CONTAINER)
    if probe.returncode == 0:
        if f'"{LABEL}":"{pgharness.checkout()}"' not in probe.stdout.replace(" ", ""):
            raise pgharness.ForeignContainer(f"{CONTAINER} exists and is not this checkout's")
        _docker("rm", "-f", "-v", CONTAINER)        # ours, from a crashed run: replace it
    with socket.socket() as busy:
        busy.settimeout(0.25)
        if busy.connect_ex(("127.0.0.1", PORT)) == 0:
            raise HarnessBusy(f"something else listens on {PORT}; refusing to use it")
    run = _docker("run", "-d", "--name", CONTAINER, "--label", f"{LABEL}={pgharness.checkout()}",
                  "-p", f"127.0.0.1:{PORT}:6379", IMAGE,
                  "valkey-server", "--save", "", "--appendonly", "no")
    if run.returncode != 0:
        raise RuntimeError(f"could not start {CONTAINER}: {run.stderr.strip()}")
    _created = True
    atexit.register(remove)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5) as ping:
                ping.sendall(b"PING\r\n")
                if ping.recv(16).startswith(b"+PONG"):
                    return
        except OSError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{CONTAINER} did not answer within 30 s")


def remove() -> None:
    global _created
    if _created:
        _docker("rm", "-f", "-v", CONTAINER)
        _created = False


def client():
    from valkey.asyncio import Valkey
    ensure()
    return Valkey.from_url(f"redis://127.0.0.1:{PORT}/0")


def namespace() -> str:
    return f"infrx:d2:{{{uuid.uuid4().hex}}}"
