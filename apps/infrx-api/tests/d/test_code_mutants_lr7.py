#!/usr/bin/env python3
"""WR-LR7-GRANT (merge #62, R259): `0059_lab_control_grants_2.sql` on real PostgreSQL - the Lab
control login's EXECUTE on 0054's `lab_release_live` and 0043's `lab_experiments` (the release
page's progress and read-time verdict) - its SQL mutation list (R32/R40; needs Docker, skips
visibly without it). The checks are 0056's (`test_code_mutants_lw8`: the login's exact grant
set, R251, and no browser role or the runtime beside it); a survivor fails the suite.

    INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_code_mutants_lr7.py
"""
from __future__ import annotations

import pytest

from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_code_mutants_lw8 as lw8

FILE = "0059_lab_control_grants_2.sql"
DB_MUT = f"{pgharness.DATABASE}_lr7mut"
_reason = pgharness.unavailable()
GRANT = "grant execute on function infrx.lab_release_live(jsonb), infrx.lab_experiments(jsonb)"


def _s(name, old, new, check, why):
    return _d.Mutant(name, FILE, old, new, "lab", check, why)


SQL_MUTANTS = (
    _s("lr7_drop_live", GRANT, "grant execute on function infrx.lab_experiments(jsonb)",
       lw8.HOLDS, "the release page answers 503 on the unit once a release is listed"),
    _s("lr7_drop_experiments", GRANT, "grant execute on function infrx.lab_release_live(jsonb)",
       lw8.HOLDS, "a running release's read-time verdict answers 503 on the unit"),
    _s("lr7_grant_to_runtime", "  to infrx_lab_control;", "  to infrx_lab_control, infrx_runtime;",
       lw8.BROWSER, "the App runtime's login reads the Lab's release Live and experiments"),
)


def test_the_list_is_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in lw8.CHECKS for m in SQL_MUTANTS)
    assert {"lab_release_live", "lab_experiments"} <= lw8.GRANTED       # R251


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL_MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = d7.kill(mutant, DB_MUT, lw8)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
