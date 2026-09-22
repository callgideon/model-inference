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
    with pytest.raises(SystemExit):
        cli.main(["grant", "--user", USER_A, "--idempotency-key", w.operator_secret, *R],
                 ops=w.ops, environ={})
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
