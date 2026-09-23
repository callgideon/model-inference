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
