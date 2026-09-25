"""G6B.a: the operator CLI keeps secrets out of argv, output and logs."""
from __future__ import annotations

import json
import os
import stat

import pytest

from infrx.operations import cli

from . import fakes
from .fakes import USER_A

R = ["--reason", "provision sweep tenant"]


def test_api_ops__the_cli_writes_the_secret_once_and_never_prints_it(tmp_path, capsys):
    w = fakes.world()
    env = {cli.OPERATOR_KEY_ENV: w.operator_secret}
    assert cli.main(["grant", "--user", USER_A, "--idempotency-key", "g", *R],
                    ops=w.ops, environ=env) == 0
    path = str(tmp_path / "sweep.key")
    argv = ["issue-key", "--user", USER_A, "--name", "sweep", "--secret-file", path,
            "--idempotency-key", "k", *R]
    assert cli.main(argv, ops=w.ops, environ=env) == 0
    out = capsys.readouterr()
    secret = open(path).read().strip()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    body = secret[len("sk-infrx-"):]
    assert body not in out.out + out.err and w.operator_secret[9:] not in out.out + out.err
    result = json.loads(out.out.splitlines()[-1])
    assert result["secret_file"] == path and not result["replayed"]
    # A replay reveals nothing and does not touch the file.
    os.unlink(path)
    assert cli.main(argv, ops=w.ops, environ=env) == 0
    assert not os.path.exists(path)
    assert json.loads(capsys.readouterr().out)["secret_file"] is None


def test_api_ops__the_cli_refuses_a_key_on_argv_and_an_existing_secret_file(tmp_path, capsys):
    w = fakes.world()
    prompted = []
    with pytest.raises(SystemExit):
        cli.main(["grant", "--user", USER_A, "--idempotency-key", w.operator_secret, *R],
                 ops=w.ops, environ={}, prompt=lambda text: prompted.append(text) or "x")
    assert prompted == []                              # refused before any prompt
    taken = tmp_path / "taken.key"
    taken.write_text("keep me\n")
    env = {cli.OPERATOR_KEY_ENV: w.operator_secret}
    cli.main(["grant", "--user", USER_A, "--idempotency-key", "g", *R], ops=w.ops, environ=env)
    with pytest.raises(SystemExit):
        cli.main(["issue-key", "--user", USER_A, "--name", "n", "--secret-file", str(taken),
                  "--idempotency-key", "k", *R], ops=w.ops, environ=env)
    assert taken.read_text() == "keep me\n"
    assert len(w.tenants.keys) == 1                    # refused before any key existed



def test_api_auth__the_cli_reports_a_refusal_without_the_secret(capsys):
    w = fakes.world()
    code = cli.main(["grant", "--user", USER_A, "--idempotency-key", "g", *R], ops=w.ops,
                    environ={}, prompt=lambda _: "wrong-operator-secret")
    err = capsys.readouterr().err
    assert code == 1 and json.loads(err)["error"] == "invalid_api_key"
    assert "wrong-operator-secret" not in err


def test_api_ops__the_secret_file_is_created_exclusively(tmp_path):
    """O_EXCL on its own, independent of the os.path.exists pre-check."""
    path = tmp_path / "raced.key"
    path.write_text("keep me\n")
    with pytest.raises(FileExistsError):
        cli._write_secret_once(str(path), "sk-infrx-" + "x" * 40)
    assert path.read_text() == "keep me\n"


def test_api_ops__the_operator_tool_builds_the_postgres_adapters_from_the_environment(
        monkeypatch):
    """D5 request 4 / E4B request 4: `build_operations()` composes D5/A1's PostgreSQL
    adapters over the deployment's `DATABASE_URL`, read through `config.from_env` like the
    gateway's (no connection is opened to build them); without one it refuses rather than
    run on an in-memory store."""
    from infrx.config import Settings
    from infrx.contracts.limits import DEFAULTS
    from infrx.state import jobstore, operations as pg
    from infrx.state.catalog import PgCatalogDirectory

    dialled = []
    real = jobstore.connector
    monkeypatch.setattr(jobstore, "connector", lambda dsn: dialled.append(dsn) or real(dsn))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for settings in (None, Settings(pilot=DEFAULTS.replace(database_url="  "))):
        with pytest.raises(SystemExit, match="DATABASE_URL is not set"):
            cli.build_operations(settings)
    monkeypatch.setenv("DATABASE_URL", "postgresql://ops@127.0.0.1:5432/infrx")
    ops = cli.build_operations()
    assert dialled == ["postgresql://ops@127.0.0.1:5432/infrx"]
    assert (type(ops.tenants), type(ops.ledger), type(ops.audit), type(ops.registry),
            type(ops.wallets), type(ops.accounts), type(ops.catalog), type(ops.jobs)) == (
        pg.PgTenantStore, pg.PgLedger, pg.PgAuditLog, pg.PgRegistry, pg.PgWalletDirectory,
        pg.PgAccountView, PgCatalogDirectory, jobstore.PgJobStore)


RUNTIME_DSN = "postgresql://infrx_runtime.projref:runtime-pw-0123@pooler.example:6543/postgres"
OWNER_DSN = "postgresql://postgres.projref:owner-pw-0123@pooler.example:5432/postgres"


def _dialled(monkeypatch):
    from infrx.state import jobstore
    dialled = []
    monkeypatch.setattr(jobstore, "connector", lambda dsn: dialled.append(dsn) or object())
    return dialled


def test_api_ops__the_operator_tool_refuses_a_dedicated_runtime_login(monkeypatch, capsys):
    """OPS-CLI-DSN (RL-V5): the box env file's DATABASE_URL moves to the dedicated
    `infrx_runtime` login (R127), which must never rewrite money and cannot `set role`.
    The tool refuses it before dialling anything, names the operator DSN variable and
    never echoes the DSN. Oracle: a tool that dials the runtime login, or whose refusal
    carries the password."""
    dialled = _dialled(monkeypatch)
    for env in ({"DATABASE_URL": RUNTIME_DSN},
                {"DATABASE_URL": "host=db port=5432 user=infrx_monitor password=monitor-pw-0123"},
                {"DATABASE_URL": OWNER_DSN, cli.OPERATIONS_DSN_ENV: RUNTIME_DSN}):
        monkeypatch.delenv(cli.OPERATIONS_DSN_ENV, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(SystemExit) as refused:
            cli.build_operations(environ=env)
        message = str(refused.value)
        assert cli.OPERATIONS_DSN_ENV in message and "dedicated" in message, message
        assert "pw-0123" not in message and "pooler.example" not in message, message
    assert dialled == []
    # through `main`, too: the refusal comes before any operation and prints no DSN
    monkeypatch.setenv("DATABASE_URL", RUNTIME_DSN)
    monkeypatch.delenv(cli.OPERATIONS_DSN_ENV, raising=False)
    with pytest.raises(SystemExit, match=cli.OPERATIONS_DSN_ENV):
        cli.main(["account", "--user", USER_A], environ={cli.OPERATOR_KEY_ENV: "x"})
    out = capsys.readouterr()
    assert "pw-0123" not in out.out + out.err and dialled == []


def test_api_ops__the_operator_dsn_takes_precedence_over_database_url(monkeypatch):
    """OPS-CLI-DSN: `OPERATIONS_DATABASE_URL` (the owner or broad login) is what the tool
    dials when set, even beside a runtime DATABASE_URL; unset, an ordinary DATABASE_URL
    still works unchanged. Oracle: the precedence inverted (the runtime login refused, or
    the operator DSN ignored)."""
    dialled = _dialled(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://ops@127.0.0.1:5432/infrx")
    cli.build_operations(environ={cli.OPERATIONS_DSN_ENV: OWNER_DSN})
    assert dialled == [OWNER_DSN]
    monkeypatch.setenv("DATABASE_URL", RUNTIME_DSN)
    cli.build_operations(environ={cli.OPERATIONS_DSN_ENV: f"  {OWNER_DSN} "})
    assert dialled == [OWNER_DSN, OWNER_DSN]
    monkeypatch.setenv("DATABASE_URL", "postgresql://ops@127.0.0.1:5432/infrx")
    cli.build_operations(environ={})
    assert dialled[-1] == "postgresql://ops@127.0.0.1:5432/infrx"
