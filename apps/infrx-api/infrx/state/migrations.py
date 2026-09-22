"""Where the SQL migrations are, in what order, and how to apply them.

Every D task and C1 needs the same three things to build a task-local database:
the ordered migration list, the Supabase shim (plain PostgreSQL has no `auth`
schema and no `anon`/`authenticated`/`service_role` roles) and the test clock. They
live here rather than in one task's tests so the next task does not rediscover them.

`DIR` points at the checked-in `apps/app/supabase/migrations/`, which the Supabase
CLI applies in production; the two fixtures beside this module are never applied
there. Nothing in this module opens a connection or reads the environment: it hands
back SQL text, and the caller runs it.
"""
from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[3]                      # .../apps/infrx-api/infrx/state -> repo root

#: The production migration directory, applied by the Supabase CLI in order.
DIR = _REPO / "apps" / "app" / "supabase" / "migrations"

#: Test fixtures. Deliberately outside `DIR` so no deployment can apply them.
SHIM = _HERE / "supabase_shim.sql"
TEST_CLOCK = _HERE / "test_clock.sql"

#: Operator seed (D1R): the Marlin registry rows and a PROVISIONAL rate card (P-01).
#: Never a migration - an operator applies it deliberately; the tests apply it too.
SEED_MARLIN = _HERE / "seed_marlin_provisional.sql"


def migrations() -> tuple[Path, ...]:
    """`0001_init.sql`, `0002_…`, … in lexicographic order, which is their order."""
    return tuple(sorted(DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")))


def sql_for(*, shim: bool = True, clock: bool = True,
            directory: Path | None = None) -> tuple[tuple[str, str], ...]:
    """`((label, sql), …)` to execute in order against an empty database.

    `directory` overrides `DIR`, which is how the mutation runner applies an edited
    copy of a migration without touching the checked-in one.
    """
    files: list[Path] = []
    if shim:
        files.append(SHIM)
    base = directory or DIR
    files.extend(sorted(base.glob("[0-9][0-9][0-9][0-9]_*.sql")))
    if clock:
        files.append(TEST_CLOCK)
    return tuple((path.name, path.read_text()) for path in files)
