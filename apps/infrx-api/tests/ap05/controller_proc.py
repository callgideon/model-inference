#!/usr/bin/env python3
"""A hosting controller in its own OS process, SIGKILLed by itself at one named boundary: the
real-process half of API-DEPLOY's kill test (tests/ap05, `pg` only). Nothing is cleaned up
on the way out - that is the point: its lease, its engine process and its half-done work stay
for the next holder to find.

    python tests/ap05/controller_proc.py <service dsn> <login dsn> <world tmp> <owner> <boundary>

Exit 0: the boundary was never reached within the passes (the case fails on that).
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import signal
import sys
import time

API = pathlib.Path(__file__).resolve().parents[2]
if str(API) not in sys.path:
    sys.path.insert(0, str(API))


def main(service_dsn: str, login_dsn: str, tmp: str, owner: str, boundary: str) -> int:
    import psycopg

    from infrx.lab.artifacts.store import PgArtifactStore
    from infrx.lab.hosting import LabHosting
    from infrx.lab.hosting.controller import Controller
    from infrx.lab.hosting.store import PgHostingStore
    from infrx.state.control_ops import PgControlOps
    from infrx.state.jobstore import connector
    from tests.ap05.conftest import local_launcher, target_in
    from tests.l.control import worlds as l3

    def die(name: str) -> None:
        if name == boundary:
            os.kill(os.getpid(), signal.SIGKILL)

    root = pathlib.Path(tmp)
    with psycopg.connect(service_dsn, autocommit=True) as conn:
        w = l3.PgWorld(conn, service_dsn)
        login = connector(login_dsn, set_role=False)
        target = target_in(root)
        hosting = LabHosting(w.access, w.control, PgArtifactStore(login), PgControlOps(login),
                             PgHostingStore(login), target)
        controller = Controller(hosting, local_launcher(root / "engines"), target, owner=owner,
                                boundary=die)
        for _ in range(300):
            asyncio.run(controller.run_once())
            time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:6]))
