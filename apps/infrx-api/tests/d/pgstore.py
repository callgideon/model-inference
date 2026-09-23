"""The D harness's PostgreSQL JobStore rig: a migrated, seeded TEMPLATE database built once
per process, cloned per conformance factory call (a fresh store in ~0.2 s instead of a
full migration run). Uses `pgharness`'s own labelled container and port only."""
from __future__ import annotations

import itertools

from infrx.state import migrations, pgtesting

from . import pgharness

TEMPLATE = f"{pgharness.DATABASE}_d2tmpl"
_names = itertools.count(1)
_made: list[str] = []
_ready = False


def _template() -> None:
    global _ready
    if _ready:
        return
    pgharness.ensure()
    pgharness.recreate(TEMPLATE)
    pgharness.apply(TEMPLATE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(TEMPLATE) as conn:
        pgtesting.seed(conn)
    _ready = True


def fresh_database() -> str:
    """A clone of the template, named `infrx_d1_d2_<n>` (the clock gate needs `infrx_`)."""
    _template()
    name = f"{pgharness.DATABASE}_d2_{next(_names)}"
    with pgharness.connect("postgres") as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')
        admin.execute(f'create database "{name}" template "{TEMPLATE}"')
    _made.append(name)
    # Keep the disk bounded: a case holds at most a few harnesses at once.
    while len(_made) > 12:
        old = _made.pop(0)
        with pgharness.connect("postgres") as admin:
            admin.execute(f'drop database if exists "{old}" with (force)')
    return name


factory = pgtesting.make_jobstore_factory(fresh_database, pgharness.dsn)
