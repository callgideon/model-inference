#!/usr/bin/env python3
"""R32/R40 for 0030 (the Lab session door, `test_l2sql_self.py`): single-edit defects of
`0030_lab_access_self.sql`, each killed by its named check on a database built from the
mutated set (migration_mutants' runner; only an AssertionError kills). Every check is named.

    INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_l2sql_self_mutants.py
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
import pytest
from infrx.state import migrations

from . import migration_mutants as _d
from . import pgharness
from . import test_l2sql_self as t

FILE = "0030_lab_access_self.sql"
DB = f"{pgharness.DATABASE}_l2selfmut"
SAME = "check_the_session_reads_its_own_current_workspaces_as_the_port_does"
DOOR = "check_only_a_signed_in_session_calls_it"
INSTANT = "check_a_revocation_ends_the_workspace_at_its_instant"


def _s(name, old, new, check, why):
    return _d.Mutant(name, FILE, old, new, "lab", check, why)


MUTANTS = (
    _s("self_revoked_listed", "\n     and (r->>'revoked_at' is null or ", "\n     and (true or ",
       SAME, "a revoked provider member still selects the workspace"),
    _s("self_future_listed", "   where (r->>'granted_at')::timestamptz <= infrx.now()\n",
       "   where true\n", SAME, "a membership is usable before it is granted"),
    _s("self_process_clock", "infrx.now() < (r->>'revoked_at')", "now() < (r->>'revoked_at')",
       SAME, "currency judged on a second clock (R7), not the database's"),
    _s("self_leaks_columns", "'role', r->'role')", "'role', r->'role', 'user_id', r->'user_id')",
       SAME, "the browser receives identities (user, granting operator) it never reads"),
    _s("self_revoked_at_its_instant", "infrx.now() < (r->>'revoked_at')",
       "infrx.now() <= (r->>'revoked_at')", INSTANT,
       "a revocation takes effect after its instant, unlike the contract and L2"),
    _s("self_anon_calls", "from public, anon;", "from public;", DOOR,
       "a signed-out caller reaches the membership read"),
    _s("self_runs_as_caller", "language sql stable security definer set search_path = public",
       "language sql stable security invoker set search_path = public", DOOR,
       "the session cannot reach the closed infrx read, so every Lab user is unavailable"),
)


def kill(mutant) -> tuple[str, str]:
    pgharness.ensure()
    with TemporaryDirectory(prefix=f"infrx-l2self-{mutant.name}-") as tmp:
        directory = Path(tmp)
        refused = _d._mutate(directory, mutant)
        if refused is not None:
            return _d.MISDECLARED, refused
        try:
            pgharness.recreate(DB)
            pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM,
                                                   directory=directory))
        except (AssertionError, psycopg.Error) as broken:
            return _d.APPLY_ERROR, _d._first_line(broken)
        try:
            with pgharness.connect(DB) as conn:
                t.seed(conn)
                return _d._run(t.CHECKS[mutant.check], conn, pgharness.dsn(DB))
        except (AssertionError, psycopg.Error) as during_setup:
            return _d.SETUP_ERROR, _d._first_line(during_setup)


def test_the_list_is_well_formed_and_names_every_check() -> None:
    assert len({m.name for m in MUTANTS}) == len(MUTANTS), "duplicate mutant names"
    stale = [m.name for m in MUTANTS if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared anchors: {stale}"
    assert set(t.CHECKS) == {m.check for m in MUTANTS}, "a check no mutant can break"


@pytest.mark.skipif(t._reason is not None, reason=f"task-local PostgreSQL unavailable: {t._reason}")
@pytest.mark.parametrize("mutant", MUTANTS, ids=lambda m: m.name)
def test_mutant_is_killed(mutant) -> None:
    outcome, detail = kill(mutant)
    assert outcome == _d.KILLED, f"{mutant.name} was {outcome} by {mutant.check}: {detail}. " \
                                 f"In production: {mutant.why}"
    print(f"{mutant.name}: {outcome} -> {detail}")
