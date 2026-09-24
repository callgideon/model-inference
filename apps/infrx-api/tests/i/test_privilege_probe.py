"""I8 slice 2: infra/runbooks/privilege_probe.py through the transaction pooler, on the real
schema (tests/i/pooler.py), with a STAND-IN for D10's runtime login (`infrx_i8_runtime`:
LOGIN, no inherited privilege, a role-level statement_timeout, USAGE on `infrx` and EXECUTE
on one function). D10 supplies the real login and its function list; the probe is
parameterized by both.

Failure oracles: the privileged configurations cannot pass - today's runtime login
(`postgres`), a login that is a member of service_role, one over-granted table privilege,
a BYPASSRLS login and a login with no statement_timeout of its own each fail the probe,
naming the check. The DSN never reaches the output.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from . import support
from .pooler import PG_DIRECT, TXN

PROBE = support.REPO / "infra" / "runbooks" / "privilege_probe.py"
ROLE, PASSWORD = "infrx_i8_runtime", "infrx-i8-runtime-local"


def _sql(stack, *statements):
    import psycopg
    with psycopg.connect(stack.dsn(PG_DIRECT), autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


def _run(dsn, *args):
    done = subprocess.run([sys.executable, str(PROBE), *args], capture_output=True, text=True,
                          env={**os.environ, "PROBE_DATABASE_URL": dsn})
    assert PASSWORD not in done.stdout + done.stderr and "infrx-i8-local" not in done.stdout
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    return done.returncode, lines


def test_ops_continuous__the_least_privilege_login_passes_and_privileged_ones_fail(
        i8_stack, tmp_path):
    _sql(i8_stack, f"drop role if exists {ROLE}",
         f"create role {ROLE} login noinherit password '{PASSWORD}'",
         f"alter role {ROLE} set statement_timeout = '15s'",
         f"grant usage on schema infrx to {ROLE}",
         f"grant execute on function infrx.admission_lock_key() to {ROLE}")
    i8_stack.users[ROLE] = PASSWORD
    i8_stack.write_bouncers()
    functions = tmp_path / "functions.txt"
    functions.write_text("# D10's list, stand-in\ninfrx.admission_lock_key()\n")
    runtime = i8_stack.dsn(TXN, ROLE)

    code, lines = _run(runtime, "--role", ROLE, "--allow-functions", str(functions),
                       "--pooler-semantics")
    assert code == 0, [line for line in lines if not line.get("pass", True)]
    assert lines[-1] == {"verdict": "PASS", "failed": []}
    denied = [line for line in lines if line.get("expect") == "denied"]
    assert len(denied) == 17 and all(line["got"] == "denied" for line in denied)
    observed = {line["observe"]: line["got"] for line in lines if "observe" in line}
    assert observed["8 executions with automatic prepared statements"].startswith(
        ("error", "no error"))                                       # reported, not judged

    # no D10 function list: cannot pass (PENDING is not a pass)
    code, lines = _run(runtime, "--role", ROLE)
    assert code == 1 and lines[-1]["failed"] == ["executes the runtime's functions"]

    # a table privilege behind row-level security: still refused, but reported as RLS-only
    _sql(i8_stack, f"grant insert on public.credit_ledger to {ROLE}")
    code, lines = _run(runtime, "--role", ROLE, "--allow-functions", str(functions))
    assert code == 0
    (ledger,) = [line for line in lines if "credit_ledger" in line.get("check", "")]
    assert ledger["got"] == "denied (row-level security only)"
    _sql(i8_stack, f"revoke insert on public.credit_ledger from {ROLE}")

    # one over-granted privilege RLS does not cover (TRUNCATE)
    _sql(i8_stack, f"grant truncate on infrx.outbox to {ROLE}")
    code, lines = _run(runtime, "--role", ROLE, "--allow-functions", str(functions))
    assert code == 1 and lines[-1]["failed"] == ["denied: truncate the outbox"]
    _sql(i8_stack, f"revoke truncate on infrx.outbox from {ROLE}")

    # membership in service_role (today's `set role service_role` design)
    _sql(i8_stack, f"grant service_role to {ROLE}")
    code, lines = _run(runtime, "--role", ROLE, "--allow-functions", str(functions))
    assert code == 1
    assert {"the login is no member of a privileged role",
            "denied: become service_role"} <= set(lines[-1]["failed"])
    _sql(i8_stack, f"revoke service_role from {ROLE}")

    # least privilege in every grant, but BYPASSRLS - the hosted `postgres` login's attribute
    # (D-31): exactly the attribute check fails
    _sql(i8_stack, f"alter role {ROLE} bypassrls")
    code, lines = _run(runtime, "--role", ROLE, "--allow-functions", str(functions))
    assert code == 1 and lines[-1]["failed"] == [
        "the login is not superuser, bypassrls, createrole, createdb or replication"]
    _sql(i8_stack, f"alter role {ROLE} nobypassrls")

    # no role-level statement_timeout: nothing bounds a statement on a pooled connection
    # (fresh server connections: the role default is read at a server's session start)
    _sql(i8_stack, f"alter role {ROLE} reset statement_timeout")
    i8_stack.write_bouncers()
    code, lines = _run(runtime, "--role", ROLE, "--allow-functions", str(functions))
    assert code == 1 and lines[-1]["failed"] == [
        "statements are bounded by the login's own default (survives transaction pooling)"]
    _sql(i8_stack, f"alter role {ROLE} set statement_timeout = '15s'")
    i8_stack.write_bouncers()

    # today's runtime login: the project's `postgres` (superuser here, bypassrls on hosted)
    code, lines = _run(i8_stack.dsn(TXN), "--role", ROLE, "--allow-functions", str(functions))
    assert code == 1 and len(lines[-1]["failed"]) >= 10


def test_ops_continuous__the_probe_refuses_to_run_without_its_dsn_in_the_environment():
    done = subprocess.run([sys.executable, str(PROBE), "--role", ROLE], capture_output=True,
                          text=True, env={k: v for k, v in os.environ.items()
                                          if k != "PROBE_DATABASE_URL"})
    assert done.returncode == 2 and "PROBE_DATABASE_URL" in done.stderr
