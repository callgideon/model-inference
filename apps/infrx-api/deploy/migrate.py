#!/usr/bin/env python3
"""Apply D's additive SQL migrations, fail closed, after a reviewed dry run (I2B.b).

    export MIGRATE_DATABASE_URL          # never an argument: it carries a credential
    python deploy/migrate.py plan  [--dir apps/app/supabase/migrations]
    python deploy/migrate.py apply --expect <plan digest printed by `plan`> [--dir …]

The migrations are D's files (`apps/app/supabase/migrations/NNNN_name.sql`); this script
contains no SQL of its own beyond reading and writing the Supabase CLI's history table
`supabase_migrations.schema_migrations`, so the CLI and this script agree on what is
applied. Rules, each a refusal with nothing changed:

* `plan` is read-only (`transaction_read_only` on its own transaction - the `default_`
  form would only reach later ones); it prints what is applied, what is pending with
  each file's sha256, and a digest over exactly that pending list.
* `apply` refuses unless `--expect` equals the digest it computes **after** taking an
  exclusive lock - so it applies exactly the files, in exactly the database state, that
  the operator reviewed. A changed file, a concurrent migrator or a moved database is a
  different digest.
* No history table, a version the repository does not have, or a gap (a later version
  applied, an earlier one not) is a refusal: the database is not in a state this
  repository's order describes, and guessing is how migrations get applied twice.
* Every pending file and its history row are applied in **one** transaction. A failure
  anywhere rolls all of them back: the database is either at the old state or the new
  one, never between (infra/README.md §7: additive, forward-compatible, all or nothing).
  A file that ends that transaction itself (its own COMMIT or ROLLBACK) breaks the
  promise, so the plan stops at it and says so.

Exit codes: 0 done (or nothing pending), 2 refused (nothing changed), 3 a migration
failed and was rolled back (nothing changed), 4 a migration ended the transaction itself:
what ran before that point may be committed without its history rows - resolve with D.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import sys

REFUSED, FAILED, PARTIAL = 2, 3, 4
DSN_ENV = "MIGRATE_DATABASE_URL"
HISTORY = "supabase_migrations.schema_migrations"
FILENAME = re.compile(r"(\d{4})_([a-z0-9_]+)\.sql")
# Any constant works; it only has to be the same for every migrator of this database.
LOCK_KEY = 0x12BD3B
# Statements PostgreSQL refuses inside a transaction block. `apply` runs the whole plan in
# one, so such a file is refused at `plan`, before the window, not failed mid-`apply`.
OUTSIDE_TRANSACTION = re.compile(r"^\s*(vacuum|alter\s+system|(create|drop|reindex)\b[^;]*"
                                 r"\bconcurrently)\b", re.I | re.M)


class Refused(Exception):
    """A state this script will not act on. The message names files and versions only."""


def local_migrations(directory: pathlib.Path) -> list[tuple[str, str, bytes]]:
    """`(version, name, bytes)` in order. A file that does not fit the grammar is a
    refusal, not a skip: an unapplied migration nobody noticed is worse than a stop."""
    found = []
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME.fullmatch(path.name)
        if not match:
            raise Refused(f"{path.name}: not a NNNN_name.sql migration")
        body = path.read_bytes()
        if OUTSIDE_TRANSACTION.search(body.decode(errors="replace")):
            raise Refused(f"{path.name}: VACUUM, ALTER SYSTEM or CONCURRENTLY cannot run in "
                          f"the one transaction `apply` uses; D ships it as its own step")
        found.append((match.group(1), match.group(2), body))
    versions = [version for version, _, _ in found]
    if len(set(versions)) != len(versions):
        raise Refused("two migration files share a version")
    return found


def pending(local, applied: list[str]):
    """The files after the applied prefix, or a refusal if `applied` is not a prefix."""
    versions = [version for version, _, _ in local]
    unknown = sorted(set(applied) - set(versions))
    if unknown:
        raise Refused(f"the database has versions this repository does not: {unknown}")
    if versions[:len(applied)] != sorted(applied):
        raise Refused(f"the applied versions {sorted(applied)} are not a prefix of "
                      f"{versions}: a gap, which this script will not fill")
    return local[len(applied):]


def digest(plan) -> str:
    """What the operator reviews and passes back as `--expect`."""
    lines = "".join(f"{version} {name} {hashlib.sha256(body).hexdigest()}\n"
                    for version, name, body in plan)
    return hashlib.sha256(lines.encode()).hexdigest()


def describe(plan, applied: dict[str, str]) -> str:
    # `version name`: history is matched on versions only (as the Supabase CLI does), so
    # the names are printed for the reviewer to see a renamed file.
    listed = ", ".join(f'{v} {applied[v]}'.strip() for v in sorted(applied))
    rows = [f"applied: {listed or '(none)'}"]
    rows += [f"pending: {version}_{name}.sql sha256={hashlib.sha256(body).hexdigest()}"
             for version, name, body in plan]
    rows.append(f"plan digest: {digest(plan)}" if plan else "nothing pending")
    return "\n".join(rows)


def history(conn) -> tuple[dict[str, str], set[str]]:
    """The applied versions (-> their recorded names) and the history table's columns; no
    table is a refusal."""
    columns = {row[0] for row in conn.execute(
        "select column_name from information_schema.columns "
        "where table_schema = 'supabase_migrations' and table_name = 'schema_migrations'")}
    if "version" not in columns:
        raise Refused(f"{HISTORY} does not exist: not a database migrated by the Supabase "
                      f"CLI, and no applied set can be assumed")
    name = ", name" if "name" in columns else ""
    rows = conn.execute(f"select version{name} from {HISTORY}")
    return {row[0]: (row[1] or "" if name else "") for row in rows}, columns


def connect():
    import psycopg

    dsn = os.environ.get(DSN_ENV, "")
    if not dsn.strip():
        raise Refused(f"{DSN_ENV} is not set")
    try:
        return psycopg.connect(dsn, autocommit=False)
    except Exception as failure:  # noqa: BLE001 - libpq quotes a malformed DSN, password and all
        raise Refused(f"{DSN_ENV}: the connection failed ({type(failure).__name__}); "
                      f"the value is not echoed") from None


def plan_command(directory: pathlib.Path) -> int:
    local = local_migrations(directory)
    with connect() as conn:
        conn.execute("set transaction_read_only = on")
        applied, _ = history(conn)
        print(describe(pending(local, applied), applied))
        conn.rollback()
    return 0


def apply_command(directory: pathlib.Path, expect: str) -> int:
    from psycopg.pq import TransactionStatus

    local = local_migrations(directory)
    with connect() as conn:
        # One transaction for the lock, the re-read, every file and every history row.
        conn.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        applied, columns = history(conn)
        conn.execute(f"lock table {HISTORY} in exclusive mode")
        plan = pending(local, applied)
        if not plan:
            print("nothing pending")
            return 0
        if digest(plan) != expect:
            raise Refused(f"the plan is not the one reviewed (--expect does not match "
                          f"{digest(plan)}); run `plan` again")
        insert = ["version"] + [c for c in ("name", "statements") if c in columns]
        for version, name, body in plan:
            try:
                conn.execute(body.decode())
            except Exception as failure:              # noqa: BLE001 - any SQL error
                conn.rollback()
                print(f"{version}_{name}.sql failed ({type(failure).__name__}); every "
                      f"pending migration was rolled back", file=sys.stderr)
                return FAILED
            if conn.info.transaction_status != TransactionStatus.INTRANS:
                conn.rollback()
                print(f"{version}_{name}.sql ended the transaction (a COMMIT or ROLLBACK "
                      f"inside a migration): what ran before it may be committed without "
                      f"its history rows; nothing after it ran", file=sys.stderr)
                return PARTIAL
            values = {"version": version, "name": name, "statements": [body.decode()]}
            conn.execute(f"insert into {HISTORY} ({', '.join(insert)}) values "
                         f"({', '.join(['%s'] * len(insert))})",
                         [values[c] for c in insert])
        conn.commit()
        print(f"applied: {', '.join(v for v, _, _ in plan)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("plan", "apply"))
    parser.add_argument("--dir", default=None,
                        help="migration directory (default: apps/app/supabase/migrations)")
    parser.add_argument("--expect", default="", help="apply: the digest `plan` printed")
    args = parser.parse_args(argv)
    if args.dir:
        directory = pathlib.Path(args.dir)
    else:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from infrx.state.migrations import DIR as directory
    try:
        if args.command == "plan":
            return plan_command(directory)
        return apply_command(directory, args.expect)
    except Refused as refusal:
        print(f"refused, nothing changed: {refusal}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
