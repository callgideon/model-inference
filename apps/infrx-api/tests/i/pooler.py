"""I8's local stand-in for the hosted Supavisor pooler: PostgreSQL + two PgBouncers (docker).

    infrx-i8-postgres   127.0.0.1:55450  postgres:16 (digest), database infrx_i8 with the
                                         real schema: supabase_shim.sql + 0001-0018
    infrx-i8-pgbouncer  127.0.0.1:55496  PgBouncer 1.24.1 (digest), two entries onto it:
        infrx_i8          pool_mode=transaction, 2 server connections, no prepared statements
        infrx_i8_session  pool_mode=session, max_db_client_connections=15

(The task-local reservation of `infrx.contracts.tasklocal`, task `i8`: the PostgreSQL
container, port and database are its; the pooler's port and container are its `pgbouncer` entry.)

Why these settings model the hosted pooler (research/plan/evidence/i/I8-*.md):
* session: Supavisor refuses the 16th session client (`EMAXCONNSESSION ... pool_size 15`,
  meas. 2026-09-24); PgBouncer's `max_db_client_connections=15` refuses it the same way (an
  error at connect, not a queue).
* transaction: session state belongs to the SERVER connection, which the pooler hands to a
  different client at every transaction boundary - the property Supabase documents for 6543
  ("Session-level state is lost between transactions ... set and reset, session-level
  advisory locks, listen and notify, and temporary tables"; "Transaction mode does not
  support prepared statements"). Two server connections make the handoff observable, and
  PgBouncer's LIFO reuse (server_round_robin=0) makes it deterministic.
PgBouncer is a stand-in, not Supavisor: the coordinator's probe on the real 6543
(infra/runbooks/privilege_probe.py --pooler-semantics) confirms the hosted behaviour.

Nothing here touches a container it did not create: the names carry the `infrx-i8-` prefix,
every container is labelled as an I8 harness's, a foreign one is refused, and an flock on the
PostgreSQL port keeps two runs (a checkout and its mutation copies) apart. Everything is removed at teardown.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from infrx.contracts.tasklocal import local_services

PG_IMAGE = "postgres@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20"
BOUNCER_IMAGE = ("edoburu/pgbouncer@sha256:"
                 "53d98b3174b0842c475b9842fb0a733b2d9f7ec9da834ec42252aa553f48c628")  # 1.24.1
SERVICE, BOUNCER_SERVICE = local_services("i8")["postgres"], local_services("i8")["pgbouncer"]
PREFIX = "infrx-i8-"
NETWORK = PREFIX + "net"
PG, BOUNCER = SERVICE.container, BOUNCER_SERVICE.container
PORTS = {PG: SERVICE.host_port, BOUNCER: BOUNCER_SERVICE.host_port}
DATABASE = SERVICE.database                        # infrx_i8
# The three ways in: straight to PostgreSQL, and the pooler's two modes.
PG_DIRECT, TXN, SESSION = "direct", "transaction", "session"
PASSWORD = "infrx-i8-local"        # a throwaway local literal, never a secret
# Created by an I8 harness (this checkout or a mutation copy of it): the flock below, not
# the path, is what keeps two runs apart.
LABEL = "ai.infrx.i8.harness"
CHECKOUT = "infrx-i8"


def _docker(*args, check=True):
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=check)


def unavailable() -> str | None:
    if shutil.which("docker") is None:
        return "docker is not installed"
    if _docker("info", "--format", "{{.ServerVersion}}", check=False).returncode != 0:
        return "docker is not usable"
    return None


def _owner(name: str) -> str | None:
    probe = _docker("inspect", "-f", "{{json .Config.Labels}}", name, check=False)
    if probe.returncode != 0:
        return None
    return (json.loads(probe.stdout.strip() or "null") or {}).get(LABEL, "")


BOUNCER_INI = f"""[databases]
{DATABASE} = host={PG} port=5432 dbname={DATABASE} pool_mode=transaction pool_size=2
{DATABASE}_session = host={PG} port=5432 dbname={DATABASE} pool_mode=session pool_size=15 max_db_client_connections=15
[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt
server_round_robin = 0
max_client_conn = 400
max_prepared_statements = 0
query_wait_timeout = 10
ignore_startup_parameters = extra_float_digits
"""


class Stack:
    """Start with `up()`, stop with `down()`; the DSNs are `dsn(port, user)`."""

    def __init__(self) -> None:
        self._lock = None
        self._dir = None
        self._made: list[str] = []
        self.users = {"postgres": PASSWORD}

    def up(self, extra_users: dict[str, str] | None = None) -> "Stack":
        self.users.update(extra_users or {})
        path = Path("/tmp") / f"infrx-i8-pooler-{PORTS[PG]}.lock"   # not TMPDIR: copies too
        self._lock = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # a second run is refused
        for name in (BOUNCER, PG):
            owner = _owner(name)
            if owner is None:
                continue
            if owner != CHECKOUT:
                raise RuntimeError(f"refusing to touch {name}: not created by an I8 harness")
            _docker("rm", "-f", "-v", name, check=False)          # our crashed run
        if _docker("network", "inspect", NETWORK, check=False).returncode != 0:
            _docker("network", "create", "--label", f"{LABEL}={CHECKOUT}", NETWORK)
        self._run(PG, "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", f"POSTGRES_DB={DATABASE}",
                  PG_IMAGE)
        self._wait(self.dsn(PG_DIRECT))
        self._schema()
        self.write_bouncers()
        return self

    def write_bouncers(self) -> None:
        """(Re)start the pooler with the current user list: fresh server connections, so a
        case that leaked session state cannot hand it to the next."""
        if BOUNCER in self._made:
            _docker("rm", "-f", BOUNCER, check=False)
            self._made.remove(BOUNCER)
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
        self._dir = Path(tempfile.mkdtemp(prefix="infrx-i8-"))
        (self._dir / "pgbouncer.ini").write_text(BOUNCER_INI)
        (self._dir / "userlist.txt").write_text(
            "".join(f'"{u}" "{p}"\n' for u, p in self.users.items()))
        os.chmod(self._dir, 0o755)
        for f in self._dir.iterdir():
            os.chmod(f, 0o644)
        self._run(BOUNCER, "-v", f"{self._dir}:/etc/pgbouncer:ro", "--entrypoint",
                  "/usr/bin/pgbouncer", BOUNCER_IMAGE, "/etc/pgbouncer/pgbouncer.ini")
        self._wait(self.dsn(TXN))

    def _run(self, name: str, *args: str) -> None:
        inner = 6432 if name != PG else 5432
        _docker("run", "-d", "--name", name, "--label", f"{LABEL}={CHECKOUT}",
                "--network", NETWORK, "-p", f"127.0.0.1:{PORTS[name]}:{inner}", *args)
        self._made.append(name)

    @staticmethod
    def _wait(dsn: str, timeout_s: float = 60.0) -> None:
        import psycopg
        deadline, last = time.monotonic() + timeout_s, None
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(dsn, connect_timeout=3) as c:
                    c.execute("select 1")
                return
            except psycopg.Error as not_yet:
                last = not_yet
                time.sleep(0.5)
        raise RuntimeError(f"not ready: {last}")

    def _schema(self) -> None:
        import psycopg
        from infrx.state import migrations
        with psycopg.connect(self.dsn(PG_DIRECT), autocommit=True) as conn:
            for label, sql in migrations.sql_for(shim=True, clock=False):
                try:
                    conn.execute(sql)
                except psycopg.Error as failed:
                    raise AssertionError(f"{label} failed to apply: {failed}") from failed

    def dsn(self, way: str, user: str = "postgres") -> str:
        """`direct`, `transaction` or `session`; the password is the local literal."""
        port = PORTS[PG] if way == PG_DIRECT else PORTS[BOUNCER]
        database = DATABASE + ("_session" if way == SESSION else "")
        return (f"postgresql://{user}:{self.users[user]}@127.0.0.1:{port}/{database}"
                "?connect_timeout=10")

    def down(self) -> None:
        for name in reversed(self._made):
            if _owner(name) == CHECKOUT:
                _docker("rm", "-f", "-v", name, check=False)
        self._made.clear()
        net = _docker("network", "inspect", "-f", "{{json .Labels}}", NETWORK, check=False)
        if net.returncode == 0 and \
                (json.loads(net.stdout.strip() or "null") or {}).get(LABEL) == CHECKOUT:
            _docker("network", "rm", NETWORK, check=False)
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
