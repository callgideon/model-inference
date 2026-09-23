#!/usr/bin/env python3
"""I2B.b `deploy/migrate.py`: D's additive migrations, applied fail closed after a
reviewed dry run (`DEPLOY-FAILCLOSED`, "partial migration failure").

The decisions - what is pending, whether the history can be explained, whether the plan
is the reviewed one, and that a failure rolls every file back - are driven here through a
recording connection. What PostgreSQL does with the same calls (one transaction, the
advisory lock, the Supabase CLI's history table) is the local rehearsal's
(`deploy/rehearse.sh`), against the pinned `supabase/postgres` image.
"""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys

import pytest
from psycopg.pq import TransactionStatus

from . import support

_spec = importlib.util.spec_from_file_location("infrx_migrate",
                                               support.API_DIR / "deploy" / "migrate.py")
migrate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = migrate
_spec.loader.exec_module(migrate)

FILES = {"0001_init.sql": "create table a (x int);",
         "0002_seed.sql": "insert into a values (1);",
         "0003_more.sql": "create table b (y int);"}


class Conn:
    """Records every statement; answers the two history reads; fails on demand."""

    def __init__(self, applied=(), columns=("version", "name", "statements"), fail_on=None,
                 ends_on=None):
        self.applied, self.columns, self.fail_on = list(applied), columns, fail_on
        self.ends_on = ends_on      # a body that ends the transaction itself (its COMMIT)
        self.info = type("Info", (), {"transaction_status": TransactionStatus.IDLE})()
        self.log: list[str] = []
        self.inserted: list[list] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)
        self.info.transaction_status = (TransactionStatus.IDLE if sql == self.ends_on
                                        else TransactionStatus.INTRANS)
        if "information_schema.columns" in sql:
            return [(c,) for c in self.columns]
        if sql.startswith("select version"):
            names = {f[:4]: f[5:-4] for f in FILES}
            return [(v, names.get(v, "renamed")) if ", name" in sql else (v,)
                    for v in self.applied]
        if sql.startswith(f"insert into {migrate.HISTORY}"):
            self.inserted.append(params)
        if self.fail_on and sql == self.fail_on:
            raise RuntimeError("syntax error at or near")
        return []

    def rollback(self):
        self.rollbacks += 1
        self.inserted.clear()

    def commit(self):
        self.commits += 1


def migrations(tmp_path, **changes) -> pathlib.Path:
    directory = tmp_path / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in {**FILES, **changes}.items():
        if body is None:
            (directory / name).unlink(missing_ok=True)
        else:
            (directory / name).write_text(body)
    return directory


def run(monkeypatch, conn, *argv):
    monkeypatch.setattr(migrate, "connect", lambda: conn)
    return migrate.main(list(argv))


def planned_digest(directory, applied) -> str:
    return migrate.digest(migrate.pending(migrate.local_migrations(directory), list(applied)))


def test_backend_deploy__migrate_plans_read_only_and_names_every_pending_file(
        tmp_path, monkeypatch, capsys):
    """The dry run sets the session read-only before it reads anything, lists what is
    applied (version and recorded name, so a renamed file shows) and each pending file
    with its sha256, prints the digest `apply` will demand, and writes nothing."""
    directory = migrations(tmp_path)
    conn = Conn(applied=["0001"])
    assert run(monkeypatch, conn, "plan", "--dir", str(directory)) == 0
    assert conn.log[0] == "set transaction_read_only = on"
    out = capsys.readouterr().out
    assert "applied: 0001 init\n" in out
    for name in ("0002_seed.sql", "0003_more.sql"):
        assert f"pending: {name} sha256={hashlib.sha256(FILES[name].encode()).hexdigest()}" in out
    assert f"plan digest: {planned_digest(directory, ['0001'])}" in out
    assert conn.inserted == [] and conn.commits == 0


def test_deploy_failclosed__migrate_applies_only_the_reviewed_plan(tmp_path, monkeypatch,
                                                                    capsys):
    """`apply` takes the lock, re-reads the history, and refuses (exit 2, nothing run)
    unless `--expect` is the digest of what is pending now: a file edited after the
    review is a different plan. The reviewed plan runs in order, each file with its
    history row, and commits once."""
    directory = migrations(tmp_path)
    reviewed = planned_digest(directory, ["0001"])
    migrations(tmp_path, **{"0003_more.sql": "drop table a;"})
    conn = Conn(applied=["0001"])
    assert run(monkeypatch, conn, "apply", "--dir", str(directory), "--expect", reviewed) == 2
    assert "not the one reviewed" in capsys.readouterr().err
    assert not [s for s in conn.log if s in FILES.values() or s == "drop table a;"]
    assert conn.commits == 0 and conn.inserted == []

    migrations(tmp_path)
    conn = Conn(applied=["0001"])
    assert run(monkeypatch, conn, "apply", "--dir", str(directory), "--expect", reviewed) == 0
    assert conn.log[0].startswith("select pg_advisory_xact_lock")
    ran = [s for s in conn.log if s in FILES.values()]
    assert ran == [FILES["0002_seed.sql"], FILES["0003_more.sql"]]
    assert [row[:2] for row in conn.inserted] == [["0002", "seed"], ["0003", "more"]]
    assert conn.commits == 1


def test_deploy_failclosed__a_failed_migration_rolls_back_the_whole_plan(tmp_path,
                                                                         monkeypatch):
    """A failure in the second pending file rolls back the first as well: exit 3, no
    history row, no commit - the database is at the old state, never between."""
    directory = migrations(tmp_path)
    conn = Conn(applied=["0001"], fail_on=FILES["0003_more.sql"])
    digest = planned_digest(directory, ["0001"])
    assert run(monkeypatch, conn, "apply", "--dir", str(directory), "--expect", digest) == 3
    assert conn.rollbacks == 1 and conn.commits == 0 and conn.inserted == []


def test_deploy_failclosed__a_migration_that_ends_the_transaction_stops_the_plan(
        tmp_path, monkeypatch, capsys):
    """A file with its own COMMIT ends the one transaction mid-plan, so "all or nothing"
    no longer holds: the plan stops right there - no history row for it, no later file,
    no commit of its own - and says what may already be committed (exit 4, not 3's
    "nothing changed")."""
    directory = migrations(tmp_path)
    conn = Conn(applied=["0001"], ends_on=FILES["0002_seed.sql"])
    digest = planned_digest(directory, ["0001"])
    assert run(monkeypatch, conn, "apply", "--dir", str(directory), "--expect", digest) == 4
    assert FILES["0003_more.sql"] not in conn.log
    assert conn.inserted == [] and conn.commits == 0
    assert "0002_seed.sql ended the transaction" in capsys.readouterr().err


def test_deploy_failclosed__a_connection_failure_never_echoes_the_dsn(tmp_path, monkeypatch,
                                                                    capsys):
    """The DSN carries the password, and libpq quotes a DSN it cannot parse in its error:
    through the real `connect`, a malformed value is a refusal (exit 2) naming the
    variable and the failure's type, never the value. Both values fail in the parser, so
    no connection is attempted."""
    directory = migrations(tmp_path)
    for dsn in (f"postgresql://infrx:{support.MARKER}@[::1:5432/infrx",
                f"postgresql://infrx:{support.MARKER}%zz@db.invalid:5432/infrx"):
        monkeypatch.setenv(migrate.DSN_ENV, dsn)
        try:
            code = migrate.main(["plan", "--dir", str(directory)])
        except Exception as leaked:         # noqa: BLE001 - what a traceback would print
            code = type(leaked).__name__
            print(leaked, file=sys.stderr)
        out = capsys.readouterr()
        assert code == 2, code
        assert support.MARKER not in out.out + out.err
        assert f"{migrate.DSN_ENV}: the connection failed" in out.err


def test_deploy_failclosed__migrate_refuses_a_history_it_cannot_explain(tmp_path,
                                                                         monkeypatch, capsys):
    """No history table, a version the repository lacks, a gap, a file outside the
    grammar, a statement that cannot run inside the plan's one transaction, no DSN, or no
    digest: each is exit 2 before any migration runs."""
    directory = migrations(tmp_path)
    digest = planned_digest(directory, [])
    cases = [Conn(columns=()), Conn(applied=["0001", "0004"]), Conn(applied=["0001", "0003"])]
    for conn in cases:
        assert run(monkeypatch, conn, "apply", "--dir", str(directory), "--expect", digest) == 2
        assert not [s for s in conn.log if s in FILES.values()] and conn.commits == 0
    local = migrate.local_migrations(directory)
    with pytest.raises(migrate.Refused, match="this repository does not"):
        migrate.pending(local, ["0001", "0004"])
    with pytest.raises(migrate.Refused, match="a gap"):
        migrate.pending(local, ["0001", "0003"])
    for i, extra in enumerate(({"0004-Bad Name.sql": "select 1;"},
                               {"0003_other.sql": "select 1;"},
                               {"0004_idx.sql": "create unique index concurrently i on a (x);"},
                               {"0004_vac.sql": "-- tidy up\nVACUUM a;"},
                               {"0004_sys.sql": "alter system set work_mem = '64MB';"})):
        odd = migrations(tmp_path / f"odd{i}", **extra)
        conn = Conn()
        assert run(monkeypatch, conn, "plan", "--dir", str(odd)) == 2 and conn.log == []
    # ... but only statements: the words in a comment or a literal are not refused
    words = migrations(tmp_path / "words", **{"0004_words.sql": (
        "/* retired:\nvacuum a;\ncreate index concurrently i on a (x);\n*/\n"
        "create table w (\n  x int, -- rebuilt concurrently by the worker\n"
        "  note text default 'refreshed concurrently'\n);\n")})
    assert run(monkeypatch, Conn(), "plan", "--dir", str(words)) == 0
    assert run(monkeypatch, Conn(), "apply", "--dir", str(directory)) == 2
    monkeypatch.undo()                      # the real `connect`, with no DSN set
    monkeypatch.delenv(migrate.DSN_ENV, raising=False)
    capsys.readouterr()
    try:
        code = migrate.main(["plan", "--dir", str(directory)])
    except Exception as attempted:          # noqa: BLE001 - a connection was attempted
        code = type(attempted).__name__
    assert code == 2, code
    # refused for the missing value, not for a default connection that happened to fail
    assert f"{migrate.DSN_ENV} is not set" in capsys.readouterr().err
