"""I3B.b on the real PostgreSQL: backup and restore, the hosted-apply rehearsal, the
database's own durability boundary and the maintenance switch. Layer 3 (E2's stack).

The hosted Supabase project has **no backup** (I1B inventory), and the coordinator's rule is
that 0003-0009 are applied there only after a backup/restore rehearsal exists. These cases
ARE that rehearsal, on the pinned `supabase/postgres` 17.6 image (hosted runs 17.6), with the
same commands `infra/runbooks/restore.md` runs against the hosted pooler:

* bk01 - dump a migrated, populated database as the non-superuser `postgres` role, restore
  it into a fresh database from the image's template, and prove it equal: every row of
  every table, plus the privileges, policies, functions, triggers, constraints and indexes
  the tenant boundary depends on - and the ledger/hold detectors report zero drift.
* bk02 - a database shaped like hosted today (0001-0002, four users, two keys, one usage
  row, no ledger): back it up, restore it, apply 0003-0009 to the RESTORED copy, and prove
  the rows the deployed pre-refactor gateway reads and writes are value-identical and its
  own statements still work; then restore the pre-apply backup as the rollback and prove
  it equals the original.
* bk03 - SIGKILL PostgreSQL: a committed ledger row survives, an uncommitted one does not,
  and the restart-to-first-connection time is measured.
* bk04 - the maintenance switch: flags off refuse every admission write with 55000 and
  write nothing; flags back on, admission is accepted again.

Scratch databases are `infrx_i3b_*` inside E2's PostgreSQL container, created from its
template, and dropped at the end of each case; nothing touches `infrx_e2`'s data except a
read-only `pg_dump` of it.
"""
from __future__ import annotations

import contextlib
import importlib.util
import re
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import recoverykit as kit                               # noqa: E402

import harness                                          # noqa: E402

# The project's own schemas, and the auth rows the tenant tables point at (Supabase owns
# the auth schema itself; its definition comes with the image, never with the dump).
SCHEMAS = ("public", "infrx")
# Hosted has both (GoTrue creates `identities`); the pinned image has only `users`. The dump
# and the fingerprint take whichever of them exist.
AUTH_TABLES = ("auth.users", "auth.identities")


def auth_tables(database: str) -> list[str]:
    with connect(database) as conn:
        return [table for table in AUTH_TABLES
                if conn.execute("select to_regclass(%s)", (table,)).fetchone()[0]]


# ------------------------------------------------------------------ database plumbing

def _container() -> str:
    return harness.assert_ours(harness.container_of("postgres"))


def _exec(argv: list[str], *, stdin: bytes | None = None, timeout: float = 300.0) -> bytes:
    import subprocess
    result = subprocess.run(["docker", "exec", "-i", _container(), *argv], input=stdin,
                            capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise harness.HarnessError(f"{argv[0]} failed ({result.returncode}): "
                                   f"{result.stderr.decode(errors='replace')[-600:]}")
    return result.stdout


def _admin(*statements: str) -> None:
    """As the image's superuser over the local socket, like E2's `provision_database`."""
    argv = ["psql", "-U", harness.PG_ADMIN_ROLE, "-d", "template1", "-v", "ON_ERROR_STOP=1"]
    for statement in statements:
        argv += ["-c", statement]
    _exec(argv)


def _create(name: str) -> None:
    terminate = ("select pg_terminate_backend(pid) from pg_stat_activity "
                 f"where datname = '{harness.PG_TEMPLATE_SOURCE}' and pid <> pg_backend_pid()")
    last = None
    for _ in range(4):
        try:
            _admin(f"drop database if exists {name} with (force)", terminate,
                   f"create database {name} template {harness.PG_TEMPLATE_SOURCE} "
                   f"owner {harness.PG_USER}")
            return
        except harness.HarnessError as refused:        # pg_cron/pg_net reconnect: retry
            last = refused
            time.sleep(1.0)
    raise last


@contextlib.contextmanager
def scratch(*names: str):
    """Fresh `infrx_i3b_*` databases from the image's template, dropped afterwards."""
    kit.needs_stack()
    assert all(name.startswith("infrx_i3b_") for name in names)
    try:
        for name in names:
            _create(name)
        yield names
    finally:
        for name in names:
            _admin(f"drop database if exists {name} with (force)")


def connect(database: str):
    import psycopg
    return psycopg.connect(harness.pg_dsn(database), autocommit=True)


def apply(database: str, files) -> list[str]:
    with connect(database) as conn:
        for path in files:
            with conn.transaction():
                conn.execute(path.read_text())
    return [path.name for path in files]


def dump(database: str) -> tuple[bytes, bytes]:
    """The runbook's two dumps, as the non-superuser `postgres` role hosted gives us: the
    project's schemas with data, and the auth rows as data only."""
    schemas = [arg for schema in SCHEMAS for arg in ("--schema", schema)]
    tables = [arg for table in auth_tables(database) for arg in ("--table", table)]
    project = _exec(["pg_dump", "-U", harness.PG_USER, "-d", database, "--format=custom",
                     *schemas])
    auth = _exec(["pg_dump", "-U", harness.PG_USER, "-d", database, "--format=custom",
                  "--data-only", *tables])
    return project, auth


def auth_triggers(database: str) -> list[str]:
    """Triggers the project defined on Supabase's own tables (0001's signup trigger on
    auth.users). A schema-scoped dump cannot carry them - they belong to auth's table - so
    the restore re-creates them from the source's own definition."""
    with connect(database) as conn:
        return [row[0] for row in conn.execute(
            "select pg_get_triggerdef(t.oid) from pg_trigger t "
            "join pg_proc p on p.oid = t.tgfoid join pg_namespace fn on fn.oid = p.pronamespace "
            "join pg_class c on c.oid = t.tgrelid join pg_namespace tn on tn.oid = c.relnamespace "
            "where not t.tgisinternal and tn.nspname = 'auth' "
            "and fn.nspname = any(%s) order by 1", (list(SCHEMAS),)).fetchall()]


# TOC entries the target already has because it was created from the same image template:
# the `public` schema itself (and its comment), and the image's own roles' default
# privileges, which the non-superuser `postgres` role may not re-issue.
TEMPLATE_OWNED = ("SCHEMA - public ", "COMMENT - SCHEMA public ")


def restorable(listing: str) -> str:
    """`pg_restore -l` output minus what the template already provides (see above)."""
    keep = []
    for line in listing.splitlines():
        if any(marker in line for marker in TEMPLATE_OWNED):
            continue
        if " DEFAULT ACL " in line and not line.rstrip().endswith(f" {harness.PG_USER}"):
            continue
        keep.append(line)
    return "\n".join(keep) + "\n"


# MEASURED on the pinned image (the first run of bk01): the template gives the `postgres`
# role DEFAULT privileges in `public` that grant ALL on every new table, sequence and
# function to anon, authenticated and service_role. pg_dump writes an object's grants as a
# difference from the built-in default (owner only), so a plain pg_restore creates each
# table WITH those template grants and never revokes them: `anon` ended up with full
# access to `public.api_keys`. The migrations revoke per object (R59), which a restore does
# not replay. So the target's per-schema defaults are emptied before the project is
# restored, and the dump's own DEFAULT ACL entries put the source's back at the end.
NEUTRALIZE_DEFAULTS = tuple(
    f"alter default privileges for role {harness.PG_USER} in schema public "
    f"revoke all on {kind} from anon, authenticated, service_role"
    for kind in ("tables", "sequences", "functions"))
# 0004's GLOBAL default (EXECUTE revoked from PUBLIC for every function `postgres` creates)
# is not schema-scoped, so a schema-scoped dump cannot carry it; it is copied when the
# source has it.
GLOBAL_FUNCTION_DEFAULT = ("select defaclacl::text from pg_default_acl where defaclrole = "
                           "%s::regrole and defaclnamespace = 0 and defaclobjtype = 'f'")


def restore(database: str, project: bytes, auth: bytes, triggers: list[str], *,
            source: str | None = None, neutralize: bool = True) -> None:
    """Auth rows first (the tenant tables' foreign keys point at them), then the project's
    schemas through a filtered table of contents, then what a schema-scoped dump cannot
    carry: the project's triggers on auth tables and the global function default.
    `--exit-on-error`: a partial restore is a failed restore, not a warning."""
    _exec(["pg_restore", "-U", harness.PG_USER, "-d", database, "--data-only",
           "--exit-on-error"], stdin=auth)
    if neutralize:
        with connect(database) as conn:
            for statement in NEUTRALIZE_DEFAULTS:
                conn.execute(statement)
    stem = f"/tmp/infrx-i3b-{database}"
    _exec(["sh", "-c", f"cat > {stem}.dump"], stdin=project)
    try:
        listing = _exec(["pg_restore", "-l", f"{stem}.dump"]).decode()
        _exec(["sh", "-c", f"cat > {stem}.list"], stdin=restorable(listing).encode())
        _exec(["pg_restore", "-U", harness.PG_USER, "-d", database, "--exit-on-error",
               "-L", f"{stem}.list", f"{stem}.dump"])
    finally:
        _exec(["rm", "-f", f"{stem}.dump", f"{stem}.list"])
    with connect(database) as conn:
        for definition in triggers:
            conn.execute(definition)
        if source is not None:
            with connect(source) as original:
                wanted = original.execute(GLOBAL_FUNCTION_DEFAULT, (harness.PG_USER,)).fetchone()
            # An aclitem granted to PUBLIC has an empty grantee: `=X/postgres`.
            public_executes = wanted is None or any(
                item.startswith("=") for item in wanted[0].strip("{}").split(","))
            if not public_executes:
                conn.execute(f"alter default privileges for role {harness.PG_USER} "
                             f"revoke execute on functions from public")


CATALOG = {
    "relations": "select n.nspname || '.' || c.relname, c.relkind::text, c.relrowsecurity, "
                 "c.relforcerowsecurity, array(select unnest(c.relacl)::text order by 1)::text "
                 "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                 "where n.nspname = any(%(s)s) and c.relkind in ('r','p','v','m','S') order by 1",
    "columns": "select table_schema || '.' || table_name, column_name, data_type, is_nullable, "
               "coalesce(column_default, '') from information_schema.columns "
               "where table_schema = any(%(s)s) order by 1, 2",
    "policies": "select schemaname || '.' || tablename, policyname, cmd, roles::text, "
                "coalesce(qual, ''), coalesce(with_check, '') from pg_policies "
                "where schemaname = any(%(s)s) order by 1, 2",
    "functions": "select n.nspname || '.' || p.proname, pg_get_function_identity_arguments(p.oid), "
                 "p.prosecdef, array(select unnest(p.proacl)::text order by 1)::text, "
                 "md5(p.prosrc) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                 "where n.nspname = any(%(s)s) order by 1, 2",
    "triggers": "select c.relname, t.tgname, pg_get_triggerdef(t.oid) from pg_trigger t "
                "join pg_class c on c.oid = t.tgrelid join pg_proc p on p.oid = t.tgfoid "
                "join pg_namespace fn on fn.oid = p.pronamespace "
                "where not t.tgisinternal and fn.nspname = any(%(s)s) order by 1, 2",
    "constraints": "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                   "from pg_constraint where connamespace in "
                   "(select oid from pg_namespace where nspname = any(%(s)s)) order by 1, 2",
    "indexes": "select schemaname || '.' || indexname, indexdef from pg_indexes "
               "where schemaname = any(%(s)s) order by 1",
    "views": "select schemaname || '.' || viewname, md5(definition) from pg_views "
             "where schemaname = any(%(s)s) order by 1",
    "schemas": "select nspname, array(select unnest(nspacl)::text order by 1)::text "
               "from pg_namespace where nspname = any(%(s)s) order by 1",
    # What every FUTURE object gets: the defaults 0004 narrowed, per schema and global.
    "default_acls": "select defaclrole::regrole::text, "
                    "coalesce(nullif(defaclnamespace, 0)::regnamespace::text, '-'), "
                    "defaclobjtype::text, array(select unnest(defaclacl)::text order by 1)::text "
                    "from pg_default_acl order by 1, 2, 3",
}


def fingerprint(database: str) -> dict:
    """Every row (count + md5 of the sorted row texts) of every project table and the auth
    rows, and the catalog facts the tenant boundary rests on."""
    with connect(database) as conn:
        tables = [row[0] for row in conn.execute(
            "select quote_ident(n.nspname) || '.' || quote_ident(c.relname) from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = any(%s) and c.relkind in ('r', 'p') order by 1",
            (list(SCHEMAS),)).fetchall()] + auth_tables(database)
        rows = {table: conn.execute(
            f"select count(*), md5(coalesce(string_agg(t::text, E'\\n' order by t::text), '')) "
            f"from {table} t").fetchone() for table in tables}
        catalog = {name: conn.execute(sql, {"s": list(SCHEMAS)}).fetchall()
                   for name, sql in CATALOG.items()}
    # MEASURED: a CHECK re-parsed on restore deparses its AND chain flattened -
    # `((a AND b) AND c)` comes back as `(a AND b AND c)`. Same constraint, different text,
    # so constraint text is compared without parentheses.
    catalog["constraints"] = [(table, name, re.sub(r"[()]", "", definition))
                              for table, name, definition in catalog["constraints"]]
    return {"rows": rows, "catalog": catalog}


def drift(database: str) -> list:
    """The two detectors (R59-7): a wallet total that disagrees with its ledger or holds."""
    with connect(database) as conn:
        found = []
        for view in ("infrx.wallet_reconciliation", "infrx.credit_wallet_reconciliation"):
            if conn.execute("select to_regclass(%s)", (view,)).fetchone()[0] is None:
                continue
            found += conn.execute(f"select * from {view} where ledger_drift <> 0 "
                                  f"or reserved_drift <> 0").fetchall()
        return found


def migrations(first: int, last: int):
    return [path for path in sorted(harness.MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
            if first <= int(path.name[:4]) <= last]


def assert_same(source: dict, restored: dict) -> None:
    for table, value in source["rows"].items():
        assert restored["rows"].get(table) == value, f"rows of {table} differ after restore"
    assert restored["rows"].keys() == source["rows"].keys()
    for name, value in source["catalog"].items():
        missing = sorted(set(value) - set(restored["catalog"][name]))
        extra = sorted(set(restored["catalog"][name]) - set(value))
        assert (missing, extra) == ([], []), f"{name} differ after restore: " \
                                             f"missing {missing[:3]}, extra {extra[:3]}"


# ------------------------------------------------------------------ bk01 backup/restore

def test_i3b_bk01_a_backup_restores_every_row_privilege_and_policy(record_property):
    """OPS-RECOVER (backup restore): the populated, fully migrated database is dumped as the
    non-superuser role, restored into a fresh database and checked - not merely listed:
    rows, RLS/policies/ACLs/functions/triggers/constraints/indexes identical, detectors
    zero drift on both."""
    kit.needs_stack()
    source = harness.PG_DATABASE
    with scratch("infrx_i3b_restored") as (target,):
        started = time.monotonic()
        project, auth = dump(source)
        dumped = time.monotonic()
        restore(target, project, auth, auth_triggers(source), source=source)
        restored = time.monotonic()
        before, after = fingerprint(source), fingerprint(target)
        assert_same(before, after)
        assert sum(count for count, _ in before["rows"].values()) > 0, "nothing to restore"
        assert drift(source) == [] and drift(target) == []
        record_property("dump_s", round(dumped - started, 2))
        record_property("restore_s", round(restored - dumped, 2))
        record_property("dump_bytes", len(project) + len(auth))
        record_property("rows", sum(count for count, _ in after["rows"].values()))


def test_i3b_bk01b_detects_a_restore_that_lost_the_signup_trigger():
    """Intentional defect: restore without re-creating the project's trigger on auth.users
    (the step a schema-scoped dump cannot carry). The comparison must name it."""
    kit.needs_stack()
    with scratch("infrx_i3b_restored") as (target,):
        project, auth = dump(harness.PG_DATABASE)
        restore(target, project, auth, triggers=[], source=harness.PG_DATABASE)
        with pytest.raises(AssertionError, match="triggers differ"):
            assert_same(fingerprint(harness.PG_DATABASE), fingerprint(target))


def test_i3b_bk01c_detects_a_restore_that_widened_browser_privileges():
    """Intentional defect, and the pitfall bk01 found: a plain restore under the template's
    default privileges hands anon/authenticated ALL on the tenant tables. The comparison
    must name the widened relations."""
    kit.needs_stack()
    with scratch("infrx_i3b_restored") as (target,):
        project, auth = dump(harness.PG_DATABASE)
        restore(target, project, auth, auth_triggers(harness.PG_DATABASE),
                source=harness.PG_DATABASE, neutralize=False)
        with connect(target) as conn:
            acl = conn.execute("select relacl::text from pg_class "
                               "where oid = 'public.api_keys'::regclass").fetchone()[0]
        assert "anon=arwdDxtm" in acl, acl
        with pytest.raises(AssertionError, match="relations differ"):
            assert_same(fingerprint(harness.PG_DATABASE), fingerprint(target))


# ------------------------------------------------------------------ bk02 the hosted rehearsal

HOSTED_SEED = """
insert into auth.users (id, email, confirmed_at) values
  ('0a000000-0000-4000-8000-000000000001', 'one@example.com', now()),
  ('0a000000-0000-4000-8000-000000000002', 'two@example.com', now()),
  ('0a000000-0000-4000-8000-000000000003', 'three@example.com', now()),
  ('0a000000-0000-4000-8000-000000000004', 'four@example.com', null);
insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash)
  select ('0b000000-0000-4000-8000-00000000000' || n)::uuid, m.org_id, m.user_id, 'k' || n,
         'sk-infrx-0000000' || n, encode(sha256(('i3b-key-' || n)::bytea), 'hex')
  from (values (1), (2)) v(n)
  join public.org_members m on m.user_id = ('0a000000-0000-4000-8000-00000000000' || n)::uuid;
insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream,
                                 prompt_tokens, completion_tokens, video_seconds, ttft_ms,
                                 latency_ms, cached, cost_usd)
  select '0c000000-0000-4000-8000-000000000001', k.org_id, k.id, 'nemostation/marlin-2b', 200,
         true, 1966, 0, 4.0, 767, 1200, false, 0.00019660
  from public.api_keys k where k.name = 'k1';
"""
# The deployed pre-refactor gateway's four statements against hosted (through PostgREST,
# which runs them as `service_role`): price read, key lookup, last-used touch, usage write.
LEGACY_GATEWAY = (
    "select input_usd_per_m, output_usd_per_m from public.models "
    "where id = 'nemostation/marlin-2b'",
    "select id, org_id, revoked_at from public.api_keys "
    "where key_hash = encode(sha256('i3b-key-1'::bytea), 'hex')",
    "update public.api_keys set last_used_at = now() "
    "where key_hash = encode(sha256('i3b-key-1'::bytea), 'hex')",
    "insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream, "
    "prompt_tokens, completion_tokens, video_seconds, ttft_ms, latency_ms, cached, cost_usd) "
    "select gen_random_uuid(), org_id, id, 'nemostation/marlin-2b', 200, false, 1200, 34, "
    "2.0, 700, 900, false, 0.00025000 from public.api_keys where name = 'k1'",
)
LEGACY_TABLES = ("public.profiles", "public.organizations", "public.org_members",
                 "public.models", "public.api_keys", "public.usage_events",
                 "public.credit_ledger")


# 0003 deliberately EXTENDS `models.limits` with the pilot's keys, leaving every existing
# key untouched, and the row's `updated_at` trigger records that (D1, 02). Those two columns
# are compared by that rule instead of by equality.
EXTENDED = {"public.models": ("limits", "updated_at")}


def legacy_rows(database: str, columns: dict[str, list[str]]) -> dict:
    """The legacy tables' rows over their ORIGINAL columns, as Python values: an added column
    or a widened numeric type is not a change to the data the old runtime reads. For the
    extended columns, the value kept is the original `limits` keys only."""
    rows = {}
    with connect(database) as conn:
        for table in LEGACY_TABLES:
            plain = [c for c in columns[table] if c not in EXTENDED.get(table, ())]
            select = ", ".join(plain + (["limits"] if table in EXTENDED else []))
            fetched = conn.execute(f"select {select} from {table}").fetchall()
            rows[table] = sorted(fetched, key=repr)
    return rows


def extended_ok(before: dict, after: dict) -> None:
    """Every original `limits` key survives with its value; only keys were added."""
    for old, new in zip(before["public.models"], after["public.models"]):
        assert old[:-1] == new[:-1]
        assert {key: new[-1][key] for key in old[-1]} == old[-1], (old[-1], new[-1])


def original_columns(database: str) -> dict[str, list[str]]:
    with connect(database) as conn:
        return {table: [row[0] for row in conn.execute(
            "select column_name from information_schema.columns where table_schema = %s "
            "and table_name = %s order by ordinal_position",
            tuple(table.split("."))).fetchall()] for table in LEGACY_TABLES}


def legacy_gateway_works(database: str) -> None:
    import psycopg
    with connect(database) as conn:
        with conn.transaction():
            conn.execute("set local role service_role")
            for statement in LEGACY_GATEWAY:
                cursor = conn.execute(statement)
                touched = len(cursor.fetchall()) if cursor.description else cursor.rowcount
                assert touched == 1, (statement, touched)
            raise psycopg.Rollback()


def test_i3b_bk02_the_hosted_apply_rehearsal_backup_restore_migrate_and_roll_back(
        record_property):
    """The coordinator's gate before 0003-0009 go to hosted: back up a hosted-shaped
    database, restore it, apply the migrations to the RESTORED copy, and prove (1) every row
    the deployed pre-refactor gateway reads is value-identical, (2) its four statements
    still run as `service_role`, (3) no detector drift and no flag enabled by the apply;
    then restore the pre-apply backup as the rollback and prove it equals the original."""
    kit.needs_stack()
    with scratch("infrx_i3b_hosted", "infrx_i3b_hosted_copy",
                 "infrx_i3b_hosted_back") as (hosted, copy, back):
        apply(hosted, migrations(1, 2))
        with connect(hosted) as conn:
            conn.execute(HOSTED_SEED)
            counts = conn.execute(
                "select (select count(*) from auth.users), (select count(*) from public.organizations), "
                "(select count(*) from public.api_keys), (select count(*) from public.usage_events), "
                "(select count(*) from public.credit_ledger)").fetchone()
        assert counts == (4, 4, 2, 1, 0), counts          # I1B's hosted shape
        legacy_gateway_works(hosted)
        columns = original_columns(hosted)
        before_rows, before = legacy_rows(hosted, columns), fingerprint(hosted)

        started = time.monotonic()
        project, auth = dump(hosted)
        triggers = auth_triggers(hosted)
        restore(copy, project, auth, triggers, source=hosted)
        restored = time.monotonic()
        assert_same(before, fingerprint(copy))

        applied = apply(copy, migrations(3, 9))
        migrated = time.monotonic()
        assert applied[0].startswith("0003") and applied[-1].startswith("0009")
        after_rows = legacy_rows(copy, columns)
        extended_ok(before_rows, after_rows)
        assert {t: v for t, v in after_rows.items() if t not in EXTENDED} == \
            {t: v for t, v in before_rows.items() if t not in EXTENDED}
        legacy_gateway_works(copy)
        assert drift(copy) == []
        with connect(copy) as conn:
            flags = dict(conn.execute("select name, enabled from infrx.feature_flags").fetchall())
        assert flags == {"signup_grant": False, "credit_admission": False,
                         "legacy_usd_admission": True}, flags

        restore(back, project, auth, triggers, source=hosted)
        assert_same(before, fingerprint(back))
        assert legacy_rows(back, columns) == before_rows
        record_property("backup_restore_s", round(restored - started, 2))
        record_property("apply_0003_0009_s", round(migrated - restored, 2))
        record_property("backup_bytes", len(project) + len(auth))


# ------------------------------------------------------------------ bk03 durability boundary

def test_i3b_bk03_a_postgres_sigkill_keeps_what_committed_and_nothing_else(record_property):
    """OPS-RECOVER (DB interruption), the durability boundary every accepted job rests on:
    after SIGKILL of PostgreSQL mid-transaction a committed ledger grant is there, the
    uncommitted one is not, the wallet total still equals its ledger (zero drift), and the
    restart-to-first-connection window is measured."""
    import psycopg
    kit.needs_stack()
    with scratch("infrx_i3b_durable") as (database,):
        apply(database, migrations(1, 9))
        d = _d_checks()
        with connect(database) as conn:
            d.seed_fixtures(conn)
            conn.execute(f"insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                         f"values ('{d.ORG_A}', 5.00000000, 'grant', 'i3b committed')")
        before = fingerprint(database)
        doomed = psycopg.connect(harness.pg_dsn(database))       # not autocommit
        doomed.execute(f"insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                       f"values ('{d.ORG_A}', 7.00000000, 'grant', 'i3b uncommitted')")
        with harness.Faults() as faults:
            faults.kill_container("postgres")
            killed = time.monotonic()
        harness.wait_postgres(database=database)
        window = time.monotonic() - killed
        _postgrest_back()
        with contextlib.suppress(Exception):
            doomed.close()
        after = fingerprint(database)
        assert after == before, "the SIGKILL changed committed state"
        with connect(database) as conn:
            reasons = [row[0] for row in conn.execute(
                "select reason from public.credit_ledger where reason like 'i3b %'").fetchall()]
        assert reasons == ["i3b committed"], reasons
        assert drift(database) == []
        record_property("postgres_restart_to_connection_s", round(window, 2))


def _postgrest_back(timeout: float = 60.0) -> None:
    """E3B's PostgREST shares this PostgreSQL during the layer-3 stage and runs after this
    file; it reconnects on its own, and this waits until it has, so the kill cannot turn
    into a failure of E3B's cases."""
    import stack
    import httpx
    if stack.postgrest_owner() != "ours":
        return
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with contextlib.suppress(httpx.HTTPError):
            if httpx.get(stack.postgrest_url() + "/", timeout=2.0).status_code < 500:
                return
        time.sleep(0.5)
    raise AssertionError("PostgREST did not come back after the PostgreSQL restart")


def _d_checks():
    spec = importlib.util.spec_from_file_location(
        "i3b_d_checks", harness.API_ROOT / "tests" / "d" / "checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ bk04 maintenance switch

MAINTENANCE = ("update infrx.feature_flags set enabled = %s, updated_by = 'i3b-drill', "
               "reason = 'maintenance drill' where name in "
               "('legacy_usd_admission', 'credit_admission')")


def as_role(conn, role: str | None, sql: str, params=None):
    """One statement as `role` (the runbook's `service_role`), inside the caller's
    transaction; `None` is the connection's own role (D2's SECURITY DEFINER path)."""
    if role:
        conn.execute(f"set local role {role}")
    result = conn.execute(sql, params)
    # On failure nothing to undo here: the caller's savepoint rolls back, SET LOCAL with it.
    if role:
        conn.execute("reset role")
    return result


def test_i3b_bk04_maintenance_refuses_every_admission_write_and_writes_nothing():
    """The runbook's maintenance switch, flipped as `service_role`: with both admission flags
    off, an old-regime job insert and the CREDIT pin resolution refuse with 55000 and leave
    no job, hold or ledger row behind; switched back on, the same admission is accepted.
    Everything inside one rolled-back transaction on a scratch database."""
    import psycopg
    kit.needs_stack()
    d = _d_checks()
    count = ("select (select count(*) from infrx.jobs), (select count(*) from infrx.credit_holds), "
             "(select count(*) from public.credit_ledger)")
    job = d._job_values("5c000000-0000-4000-8000-0000000000f1", "job_maint") + ")"
    pins = "select * from infrx.resolve_admission_pins('nemostation/marlin-2b')"
    with scratch("infrx_i3b_maintenance") as (database,):
        apply(database, migrations(1, 9))
        with connect(database) as conn:
            d.seed_fixtures(conn)
            with conn.transaction():
                before = conn.execute(count).fetchone()
                as_role(conn, "service_role", MAINTENANCE, (False,))
                for statement, role in ((job, None), (pins, "service_role")):
                    with pytest.raises(psycopg.Error) as refused:
                        with conn.transaction():
                            as_role(conn, role, statement)
                    assert refused.value.sqlstate == "55000", (statement, refused.value)
                assert conn.execute(count).fetchone() == before
                as_role(conn, "service_role", MAINTENANCE, (True,))
                with conn.transaction():
                    conn.execute(job)                     # admission is accepted again
                raise psycopg.Rollback()
        assert drift(database) == []
