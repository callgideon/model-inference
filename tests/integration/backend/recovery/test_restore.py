"""I3B.b on the real PostgreSQL: backup and restore, the hosted-apply rehearsal, the
database's own durability boundary and the maintenance switch. Layer 3 (E2's stack).

The hosted Supabase project has **no backup** (I1B inventory), and the coordinator's rule is
that 0003-0009 are applied there only after a backup/restore rehearsal exists. These cases
ARE that rehearsal, on the pinned `supabase/postgres` 17.6 image (hosted runs 17.6), driving
`infra/runbooks/pgrestore.py` - the same tool, the same flags and the same client container
`infra/runbooks/restore.md` runs against the hosted pooler:

* bk01 - dump a migrated, populated database as the non-superuser `postgres` role, restore
  it into a fresh database from the image's template, and CHECK it: every row of every
  table, plus the privileges, policies, functions, triggers, constraints, indexes and
  default privileges the tenant boundary depends on - and the detectors report zero drift.
  bk01b/bk01c/bk01d prove the check catches a lost trigger, widened privileges and a
  damaged backup; bk01f damages one catalog family (or one row) of a good restore at a
  time and the check names exactly that family; bk01e refuses a non-empty target and the backup's own source before any
  write. bk01g/bk01h (R92): an ACL is compared by what it grants, so an owner-only table
  restored with a NULL ACL is equal, and its SECURITY DEFINER RPCs still work.
* bk02 - a database shaped like hosted today (0001-0002, four users, two keys, one usage
  row, no ledger): back it up, restore it, apply 0003-0009 to the RESTORED copy, and prove
  the rows the deployed pre-refactor gateway reads and writes are value-identical and its
  own statements still work; then restore the pre-apply backup as the rollback and check it.
* bk03 - SIGKILL PostgreSQL: a committed ledger row survives, an uncommitted one does not,
  and the restart-to-first-connection time is measured.
* bk04 - the maintenance switch: flags off refuse every admission write with 55000 and
  write nothing; flags back on, admission is accepted again.

Scratch databases are `infrx_i3b_*` inside E2's PostgreSQL container, created from its
template and dropped at the end of each case; `harness.PG_DATABASE` is only ever read
(`pg_dump`).

`INFRX_I3B_PG=d` runs the same cases on the D harness instead (`tests/d/pgharness.py`: one
task-local container on `INFRX_D_TASK`'s port, removed at exit; `INFRX_D1_IMAGE=supabase` only:
the plain image is refused by name, bk00), with
its task database migrated and seeded the way `run.py` builds E2's as the source. No
compose stack; bk03 (a SIGKILL of E2's compose service) still needs E2's.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import recoverykit as kit                               # noqa: E402

import harness                                          # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pg = _load("i3b_pgrestore", kit.ROOT / "infra" / "runbooks" / "pgrestore.py")


@pytest.fixture(autouse=True)
def _password(monkeypatch):
    """The tool reads the password from the environment only, as it does against hosted."""
    monkeypatch.setenv("PGPASSWORD", pg_password())


# ------------------------------------------------------------------ database plumbing

ON_D = os.environ.get("INFRX_I3B_PG") == "d"
_d_source = None


def d_harness():
    """D's `tests/d/pgharness.py`, loaded once per process - also when rc10 imports this
    module under another name - because its port lock and container are per process."""
    if "i3b_pgharness" not in sys.modules:
        sys.modules["i3b_pgharness"] = _load("i3b_pgharness",
                                             harness.API_ROOT / "tests" / "d" / "pgharness.py")
    return sys.modules["i3b_pgharness"]


def pg_port() -> int:
    return d_harness().PORT if ON_D else harness.PORTS["postgres"]


def pg_password() -> str:
    return d_harness().PASSWORD if ON_D else harness.PG_PASSWORD


# RST-2: the one D image this module supports, refused by name before any container starts.
PLAIN_IMAGE = ("UNSUPPORTED[plain D image] INFRX_I3B_PG=d needs INFRX_D1_IMAGE=supabase: the "
               "restore client (pgrestore.IMAGE) is PostgreSQL 17.6 and sends SET "
               "transaction_timeout, which D's plain 16.14 server rejects, and D's shim has no "
               "auth.users.confirmed_at (bk02's hosted seed)")


def needs_pg() -> None:
    """E2's stack, or the D harness's Supabase container (started here, removed at exit)."""
    if not ON_D:
        return kit.needs_stack()
    if not d_harness().ON_SUPABASE:
        pytest.skip(PLAIN_IMAGE)
    why = d_harness().unavailable()
    if why:
        pytest.skip(why)
    d_harness().ensure()


def test_i3b_bk00_the_plain_d_image_is_refused_by_name(monkeypatch):
    """RST-2: `INFRX_I3B_PG=d` on D's plain image is a typed skip naming the 17.6-client /
    16.14-server mismatch, before any container is started - not 13 red restores. Layer 0:
    the harness is a stand-in reporting the plain image."""
    import types
    monkeypatch.setitem(globals(), "ON_D", True)
    monkeypatch.setitem(sys.modules, "i3b_pgharness", types.SimpleNamespace(
        ON_SUPABASE=False, unavailable=lambda: None, ensure=lambda: None))
    with pytest.raises(pytest.skip.Exception, match=r"^UNSUPPORTED\[plain D image\] .*17\.6"):
        needs_pg()


def source_db() -> str:
    """The populated database the bk01 cases dump: `harness.PG_DATABASE`, which `run.py`
    migrated and seeded; on the D harness its task database, built the same way once per
    process."""
    global _d_source
    if not ON_D:
        return harness.PG_DATABASE
    if _d_source is None:
        import pgstate
        name = d_harness().DATABASE
        _create(name)
        with connect(name) as conn:
            pgstate.apply_migrations(conn)
            pgstate.install_test_clock(conn)
            pgstate.seed_fixtures(conn)
        _d_source = name
    return _d_source


def _admin(*statements: str) -> None:
    """As the image's superuser over the local socket, like E2's `provision_database`."""
    import subprocess
    if ON_D:
        for statement in statements:                # needs_pg refused the plain image
            d_harness()._sb("template1", statement)
        return
    argv = ["docker", "exec", "-i", harness.assert_ours(harness.container_of("postgres")),
            "psql", "-U", harness.PG_ADMIN_ROLE, "-d", "template1", "-v", "ON_ERROR_STOP=1"]
    for statement in statements:
        argv += ["-c", statement]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise harness.HarnessError(f"psql failed: {result.stderr[-400:]}")


def _create(name: str) -> None:
    if ON_D:
        d_harness().recreate(name)                  # the Supabase template's copy
        return
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
    needs_pg()
    assert all(name.startswith("infrx_i3b_") for name in names)
    try:
        for name in names:
            _create(name)
        yield names
    finally:
        for name in names:
            _admin(f"drop database if exists {name} with (force)")


def conninfo(database: str) -> str:
    """What the runbook passes for hosted, pointed at E2's published port instead."""
    return (f"host=127.0.0.1 port={pg_port()} user={harness.PG_USER} "
            f"dbname={database} sslmode=disable")


def connect(database: str):
    return pg.connect(conninfo(database))


def fingerprint(database: str) -> dict:
    return pg.fingerprint(conninfo(database))


def drift(database: str) -> list:
    return pg.drift(conninfo(database))


def apply(database: str, files) -> list[str]:
    with connect(database) as conn:
        for path in files:
            with conn.transaction():
                conn.execute(path.read_text())
    return [path.name for path in files]


def migrations(first: int, last: int):
    return [path for path in sorted(harness.MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
            if first <= int(path.name[:4]) <= last]


def backup_and_restore(source: str, target: str, backup: Path, **restore_kw) -> dict:
    started = time.monotonic()
    pg.dump(conninfo(source), backup)
    dumped = time.monotonic()
    pg.restore(conninfo(target), backup, **restore_kw)
    return {"dump_s": round(dumped - started, 2),
            "restore_s": round(time.monotonic() - dumped, 2),
            "backup_bytes": sum(path.stat().st_size for path in backup.iterdir())}


# ------------------------------------------------------------------ bk01 backup/restore

def _watched_connect(seen: list):
    """`pg.connect`, observed from outside: after each use, what the session itself says
    about `transaction_read_only` (RS-7's witness - not the tool's own self-check)."""
    real = pg.connect

    @contextlib.contextmanager
    def watched(conninfo: str):
        with real(conninfo) as conn:
            yield conn
            seen.append(conn.execute("show transaction_read_only").fetchone()[0])
    return watched


def test_i3b_bk01_a_backup_restores_every_row_privilege_and_policy(tmp_path, record_property,
                                                                   monkeypatch):
    """OPS-RECOVER (backup restore): the populated, fully migrated database is dumped as the
    non-superuser role, restored into a fresh database and CHECKED - not merely listed:
    rows, RLS/policies/ACLs/functions/triggers/constraints/indexes/default privileges
    identical, detectors zero drift on both. The pinned client image is the runbook's. The
    check's sessions are read-only, observed from outside (A6 fingerprints LIVE hosted)."""
    needs_pg()
    assert pg.IMAGE == harness.compose_images()["postgres"]
    with scratch("infrx_i3b_restored") as (target,):
        timings = backup_and_restore(source_db(), target, tmp_path / "backup")
        seen: list = []
        with monkeypatch.context() as patched:
            patched.setattr(pg, "connect", _watched_connect(seen))
            before, after = fingerprint(source_db()), fingerprint(target)
        assert seen == ["on", "on"], seen
        assert pg.compare(before, after) == []
        assert pg.main(["check", "--source", conninfo(source_db()),
                        "--target", conninfo(target)]) == 0      # the runbook's own check
        assert sum(count for count, _ in before["rows"].values()) > 0, "nothing to restore"
        assert drift(source_db()) == [] and drift(target) == []
        for name, value in {**timings, "rows": sum(c for c, _ in after["rows"].values())}.items():
            record_property(name, value)


def test_i3b_bk01b_detects_a_restore_that_lost_the_signup_trigger(tmp_path):
    """Intentional defect: the backup's record of the project's trigger on auth.users (the
    step a schema-scoped dump cannot carry) is lost. The check must name it."""
    import json
    needs_pg()
    with scratch("infrx_i3b_restored") as (target,):
        backup = tmp_path / "backup"
        pg.dump(conninfo(source_db()), backup)
        meta = json.loads((backup / "meta.json").read_text())
        assert meta["auth_triggers"], "the source has no trigger on auth.users to lose"
        (backup / "meta.json").write_text(json.dumps({**meta, "auth_triggers": []}))
        _resum(backup)
        pg.restore(conninfo(target), backup)
        problems = pg.compare(fingerprint(source_db()), fingerprint(target))
        assert any(problem.startswith("triggers differ") for problem in problems), problems


def test_i3b_bk01c_detects_a_restore_that_widened_browser_privileges(tmp_path):
    """Intentional defect, and the pitfall bk01 found: a plain restore under the template's
    default privileges hands anon/authenticated ALL on the tenant tables. The check must
    name the widened relations."""
    needs_pg()
    with scratch("infrx_i3b_restored") as (target,):
        backup_and_restore(source_db(), target, tmp_path / "backup", neutralize=False)
        with connect(target) as conn:
            acl = conn.execute("select relacl::text from pg_class "
                               "where oid = 'public.api_keys'::regclass").fetchone()[0]
        assert "anon=arwdDxtm" in acl, acl
        problems = pg.compare(fingerprint(source_db()), fingerprint(target))
        assert any(problem.startswith("relations differ") for problem in problems), problems


def test_i3b_bk01d_detects_a_damaged_backup_and_restores_nothing(tmp_path):
    """A backup whose bytes no longer match its checksums is refused before anything is
    written to the target."""
    needs_pg()
    with scratch("infrx_i3b_restored") as (target,):
        backup = tmp_path / "backup"
        pg.dump(conninfo(source_db()), backup)
        damaged = bytearray((backup / "project.dump").read_bytes())
        damaged[len(damaged) // 2] ^= 0xFF
        (backup / "project.dump").write_bytes(bytes(damaged))
        with pytest.raises(RuntimeError, match="does not match SHA256SUMS"):
            pg.restore(conninfo(target), backup)
        with connect(target) as conn:
            assert conn.execute("select count(*) from pg_class c join pg_namespace n "
                                "on n.oid = c.relnamespace where n.nspname = 'public' "
                                "and c.relkind = 'r'").fetchone()[0] == 0


DEFAULT_ACLS = ("select defaclrole::regrole::text, defaclnamespace::regnamespace::text, "
                "defaclobjtype::text, defaclacl::text from pg_default_acl order by 1, 2, 3")


def _write_state(database: str) -> tuple:
    """What a refused restore must leave exactly as it was: the default privileges (the
    first thing the unguarded restore changed) and the auth rows (the second)."""
    with connect(database) as conn:
        return (conn.execute(DEFAULT_ACLS).fetchall(),
                conn.execute("select count(*) from auth.users").fetchone()[0])


def test_i3b_bk01e_a_a_non_empty_target_is_refused_before_any_write(tmp_path):
    """RS-1: restoring over a LIVE-shaped database (0001-0009 applied, no auth row - the
    shape where the unguarded tool got past the auth rows and emptied `public`'s default
    privileges before failing on CREATE SCHEMA infrx) raises before writing anything:
    pg_default_acl and auth.users are identical before and after."""
    needs_pg()
    with scratch("infrx_i3b_live") as (live,):
        apply(live, migrations(1, 9))
        backup = tmp_path / "backup"
        pg.dump(conninfo(source_db()), backup)
        before = _write_state(live)
        assert before[0], "the live-shaped target has no default privileges to lose"
        with pytest.raises(RuntimeError, match="the target is not empty"):
            pg.restore(conninfo(live), backup)
        assert _write_state(live) == before


def test_i3b_bk01e_b_a_backup_is_never_restored_onto_its_own_source(tmp_path):
    """RS-1: the backup records the database it came from (host, port, user, dbname) and
    `restore` refuses that target - even when it is empty, which is the one case the
    emptiness guard cannot see."""
    needs_pg()
    with scratch("infrx_i3b_empty_source") as (source,):
        backup = tmp_path / "backup"
        meta = pg.dump(conninfo(source), backup)
        assert meta["source"] == {"host": "127.0.0.1", "port": str(pg_port()),
                                  "user": harness.PG_USER, "dbname": source}
        before = _write_state(source)
        with pytest.raises(RuntimeError, match="the backup's own source"):
            pg.restore(conninfo(source), backup)
        assert _write_state(source) == before


SOURCE = "host=127.0.0.1 port=5432 user=postgres dbname=infrx_i3b_src sslmode=disable"
SPELLINGS = {        # the same server and database, as an operator might type it
    "localhost": ("host=localhost port=5432 user=postgres dbname=infrx_i3b_src", {}),
    "uri": ("postgresql://postgres@127.0.0.1:5432/infrx_i3b_src?sslmode=require", {}),
    "omitted_port": ("host=127.0.0.1 user=postgres dbname=infrx_i3b_src", {}),
    "hostaddr": ("hostaddr=127.0.0.1 port=5432 user=postgres dbname=infrx_i3b_src", {}),
    "environment": ("", {"PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "postgres",
                         "PGDATABASE": "infrx_i3b_src"}),
}


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_i3b_bk01e_c_every_spelling_of_the_source_is_the_source(monkeypatch, spelling):
    """RS-1 residual: the identity check resolves a conninfo the way libpq does (hostaddr
    for an empty host, the PG* environment, port 5432, every loopback name), so the source
    spelled another way is still refused - before any connection is made. A different
    database or login is not the source. Layer 1: no server is contacted."""
    for name in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGUSER", "PGDATABASE"):
        monkeypatch.delenv(name, raising=False)
    meta = {"source": pg.identity(SOURCE), "auth_tables": []}
    conninfo, env = SPELLINGS[spelling]
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    def contacted(conninfo):                   # past the identity check: it did not match
        raise AssertionError(f"the source spelled {spelling!r} was not recognised")
    monkeypatch.setattr(pg, "connect", contacted)
    with pytest.raises(RuntimeError, match="the backup's own source"):
        pg.refuse_live_target(conninfo, meta)
    for other in (SOURCE.replace("dbname=infrx_i3b_src", "dbname=infrx_i3b_copy"),
                  SOURCE.replace("user=postgres", "user=postgres.other")):
        assert pg.identity(other) != meta["source"], other


# RS-2: one damage per catalog family (and row content), applied to a copy of a GOOD
# restore; the check must name that family and no other. Each family is a single-edit
# mutant in mutants_i3b.py (i3bm60-64) that only its own parameter kills.
DAMAGE = {
    "policies": "drop policy {policy}",
    "columns": "alter table public.api_keys alter column name set default 'i3b'",
    "functions": "revoke execute on function infrx.now() from service_role",
    "indexes": "drop index public.org_members_user_id_idx",
    # MEASURED: `public`'s defaults already grant anon ALL (the template's, restored as
    # the source had them), so the same grant there is a no-op; 0004 narrowed `infrx`'s.
    "default_acls": "alter default privileges for role postgres in schema infrx "
                    "grant all on tables to anon",
    # RS-3: the two facts the money RPCs and the tenant guard rest on beyond their text.
    "triggers": "alter table public.api_keys disable trigger user",
    "functions_config": "alter function public.handle_new_user() set search_path = public",
    "rows": "update public.models set limits = limits || '{{\"i3b\": 1}}' "
            "where id = (select min(id) from public.models)",
    # RST-1: a schema grant is the tenant boundary's first gate (USAGE on infrx).
    "schemas": "grant usage on schema infrx to anon",
    # RST-3: a column grant (the one 0001 makes) moved to another grantee - same cardinality,
    # so only the grant's CONTENT tells the two apart (RST-R2-2).
    "column_acls": "revoke update (full_name) on public.profiles from authenticated; "
                   "grant update (full_name) on public.profiles to anon",
    # RST-R3-1: the same grantee, another privilege - only the privilege tells them apart.
    "column_acls_privilege": "revoke update (full_name) on public.profiles from authenticated; "
                             "grant select (full_name) on public.profiles to authenticated",
    # RST-R4-1: the same grant, now WITH GRANT OPTION (w -> w*): what it grants, fully.
    "column_acls_grantopt": "grant update (full_name) on public.profiles to authenticated "
                            "with grant option",
}
# A DAMAGE id that is not its family's name (a second witness of the same family).
FAMILY = {"functions_config": "functions", "column_acls_privilege": "column_acls",
          "column_acls_grantopt": "column_acls"}


def _family(problem: str) -> str:
    """`functions differ: ...` -> `functions`; a DAMAGE id names its family first."""
    return "rows" if problem.startswith("rows of ") else problem.split(" differ", 1)[0]


@pytest.fixture(scope="module")
def good_restore(tmp_path_factory):
    """One good restore of E2's database, checked equal, reused as the template of every
    damaged copy (a template copy takes a second; a restore takes a minute under load)."""
    with pytest.MonkeyPatch.context() as env, scratch("infrx_i3b_good") as (good,):
        env.setenv("PGPASSWORD", pg_password())          # module scope: before `_password`
        backup_and_restore(source_db(), good, tmp_path_factory.mktemp("f") / "backup")
        source = fingerprint(source_db())
        assert pg.compare(source, fingerprint(good)) == []
        yield good, source


@pytest.mark.parametrize("family", DAMAGE)
def test_i3b_bk01f_the_check_names_each_damaged_family(good_restore, family):
    """RS-2 - "checked, not merely listed" per family: after a good restore, one damage to
    one family (a dropped policy, a column default, a revoked function grant, a dropped
    index, a widened default privilege, one changed row) and `compare` names exactly that
    family."""
    good, source = good_restore
    damaged = "infrx_i3b_damaged"
    _admin(f"drop database if exists {damaged} with (force)",
           f"create database {damaged} template {good} owner {harness.PG_USER}")
    try:
        with connect(damaged) as conn:
            policy = conn.execute(
                "select format('%I on %I.%I', policyname, schemaname, tablename) "
                "from pg_policies where schemaname = 'public' order by 1 limit 1").fetchone()[0]
            conn.execute(DAMAGE[family].format(policy=policy))
        problems = pg.compare(source, fingerprint(damaged))
        assert {_family(problem) for problem in problems} == {FAMILY.get(family, family)}, \
            problems
    finally:
        _admin(f"drop database if exists {damaged} with (force)")


# R92: the check compares what an ACL grants, not how the catalog spells it.
ACLS = ("select (select nspacl from pg_namespace where nspname = 'infrx') is null, "
        "(select relacl from pg_class where oid = 'infrx.t'::regclass) is null, "
        "(select relacl from pg_class where oid = 'infrx.s'::regclass) is null, "
        "(select proacl from pg_proc where oid = 'infrx.f()'::regprocedure) is null")


def test_i3b_bk01g_an_acl_is_compared_by_the_privileges_it_grants():
    """R92: a NULL ACL is its owner's default (`acldefault`). The same privileges written
    out - which is what pg_dump turns back into NULL on a restore - compare equal, for a
    schema, a table, a sequence and a function; an ACL emptied by a revoke from the owner
    (`{}`, not NULL) is a real loss and is still named, as is a sequence grant widened."""
    with scratch("infrx_i3b_acl") as (database,):
        with connect(database) as conn:
            conn.execute("create schema infrx")
            conn.execute("create table infrx.t (id int)")
            conn.execute("create sequence infrx.s")
            conn.execute("create function infrx.f() returns int language sql as 'select 1'")
            assert conn.execute(ACLS).fetchone() == (True,) * 4, "the premise: all NULL"
        implicit = fingerprint(database)
        with connect(database) as conn:           # the owner's defaults, spelled out
            conn.execute("grant all on schema infrx to postgres")
            conn.execute("grant all on table infrx.t to postgres")
            conn.execute("grant all on sequence infrx.s to postgres")
            conn.execute("grant execute on function infrx.f() to public, postgres")
            assert conn.execute(ACLS).fetchone() == (False,) * 4, "the premise: explicit"
        assert pg.compare(implicit, fingerprint(database)) == []
        # RST-4: a sequence's ACL is compared too (the migrated schema has no sequence, so
        # this scratch one is the only witness): a widening is named, then put back.
        with connect(database) as conn:
            conn.execute("grant usage on sequence infrx.s to anon")
        problems = pg.compare(implicit, fingerprint(database))
        assert [_family(problem) for problem in problems] == ["relations"] \
            and "'infrx.s'" in problems[0], problems
        with connect(database) as conn:
            conn.execute("revoke all on sequence infrx.s from anon")
            conn.execute("revoke all on table infrx.t from postgres")
        problems = pg.compare(implicit, fingerprint(database))
        assert {_family(problem) for problem in problems} == {"relations"}, problems


def _service_role(conn, sql: str, params=None):
    with conn.transaction():
        conn.execute("set local role service_role")
        return conn.execute(sql, params).fetchone()[0]


def test_i3b_bk01h_a_restored_job_results_keeps_its_rpcs_and_stays_owner_only(tmp_path):
    """R92 on the object the E3B2 gate tripped over. 0014 leaves `infrx.job_results`
    owner-only by an explicit revoke; pg_dump writes no ACL for it (it equals the owner's
    default), so the restore leaves it NULL. That is the same privilege set: the check is
    equal, the SECURITY DEFINER `put_result`/`read_result` (owner `postgres`) still write and
    read it as service_role, and service_role still cannot touch the table itself.

    L3-REBASE: since D10's 0020 (`read_result`, 0020:439-462; R125) a result is served only
    for a SETTLED job while `infrx.now() < ` its persisted `result_expires_at`. The fixture's
    terminal job persists 2026-09-22T00:05Z, so the read runs on the restored copy with the
    test clock frozen just before that instant, and the restored expiry is proven to still be
    the authority at it; the new write goes to an unsettled job, whose read is
    `result_pending` - the row is read back as the owner instead."""
    import psycopg
    d = _d_checks()
    with scratch("infrx_i3b_results", "infrx_i3b_results_back") as (source, target):
        apply(source, migrations(1, 9999))
        with connect(source) as conn:
            d.seed_fixtures(conn)
            kept = _service_role(conn, "select infrx.put_result(%s::jsonb)",
                                 (json.dumps({"job_id": d.JOB_TERMINAL, "text": "kept"}),))
        backup_and_restore(source, target, tmp_path / "backup")
        acl = "select relacl::text from pg_class where oid = 'infrx.job_results'::regclass"
        with connect(source) as conn:
            assert conn.execute(acl).fetchone()[0] == "{postgres=arwdDxtm/postgres}"
        with connect(target) as conn:
            assert conn.execute(acl).fetchone()[0] is None, "the premise: restored as NULL"
            # the effective access, both ways: the definer and service_role keep theirs, and
            # nobody gains any (a PUBLIC execute or a service_role table grant is a widening)
            assert conn.execute(
                "select has_table_privilege('postgres', 'infrx.job_results', 'select, insert'),"
                " has_schema_privilege('service_role', 'infrx', 'usage'),"
                " has_function_privilege('service_role', 'infrx.put_result(jsonb)', 'execute'),"
                " has_function_privilege('service_role', 'infrx.read_result(uuid, text)', "
                "'execute'), has_function_privilege('anon', 'infrx.put_result(jsonb)', "
                "'execute'), has_table_privilege('service_role', 'infrx.job_results', "
                "'select')").fetchone() == (True, True, True, True, False, False)
        assert pg.compare(fingerprint(source), fingerprint(target)) == []
        with connect(target) as conn:             # the RPCs, used: a read and a new write
            import pgstate
            pgstate.install_test_clock(conn)      # after the compare: not part of the backup
            expires = conn.execute("select result_expires_at from infrx.jobs where "
                                   "request_id = %s", (d.JOB_TERMINAL,)).fetchone()[0]
            conn.execute("select infrx_test.freeze(%s - interval '1 minute')", (expires,))
            assert _service_role(conn, "select infrx.read_result(%s, %s)",
                                 (d.ORG_A, kept)) == "kept"
            conn.execute("select infrx_test.freeze(%s)", (expires,))
            with pytest.raises(psycopg.errors.RaiseException, match="result_expired"):
                _service_role(conn, "select infrx.read_result(%s, %s)", (d.ORG_A, kept))
            fresh = _service_role(conn, "select infrx.put_result(%s::jsonb)",
                                  (json.dumps({"job_id": d.JOB_QUEUED, "text": "new"}),))
            assert fresh == f"infrx-result:{d.JOB_QUEUED}"
            assert conn.execute("select body from infrx.job_results where request_id = %s",
                                (d.JOB_QUEUED,)).fetchone()[0] == "new"
            with pytest.raises(psycopg.errors.RaiseException, match="result_pending"):
                _service_role(conn, "select infrx.read_result(%s, %s)", (d.ORG_A, fresh))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _service_role(conn, "select count(*) from infrx.job_results")


def _resum(backup: Path) -> None:
    """Re-seal a backup a case edited on purpose (so only the edit is under test)."""
    import hashlib
    (backup / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256((backup / name).read_bytes()).hexdigest()}  {name}\n"
        for name in pg.FILES))


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
        tmp_path, record_property):
    """The coordinator's gate before 0003-0009 go to hosted: back up a hosted-shaped
    database, restore it, apply the migrations to the RESTORED copy, and prove (1) every row
    the deployed pre-refactor gateway reads is value-identical, (2) its four statements
    still run as `service_role`, (3) no detector drift and no flag enabled by the apply;
    then restore the pre-apply backup as the rollback and prove it equals the original."""
    needs_pg()
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

        # The runbook's commands, as it runs them against hosted (restore.md A3-A6).
        backup = tmp_path / "hosted-backup"
        started = time.monotonic()
        assert pg.main(["dump", "--conninfo", conninfo(hosted), "--out", str(backup)]) == 0
        assert pg.main(["restore", "--conninfo", conninfo(copy), "--from", str(backup)]) == 0
        assert pg.main(["check", "--source", conninfo(hosted), "--target", conninfo(copy)]) == 0
        restored = time.monotonic()
        timings = {"backup_restore_check_s": round(restored - started, 2),
                   "backup_bytes": sum(path.stat().st_size for path in backup.iterdir())}

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

        pg.restore(conninfo(back), backup)                # the rollback: the pre-apply backup
        assert pg.compare(before, fingerprint(back)) == []
        assert legacy_rows(back, columns) == before_rows
        for name, value in {**timings, "apply_0003_0009_s": round(migrated - restored, 2)}.items():
            record_property(name, value)


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
        window = kill_postgres(database)
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


def kill_postgres(database: str) -> float:
    """SIGKILL the PostgreSQL that serves `database`, bring it back, and return the
    kill-to-first-connection window: E2's compose service (bk03, the layer-3 gate) or, on the
    D harness, this run's own container (rc04a) - never anything else."""
    if ON_D:
        d = d_harness()
        container = d.assert_ours("kill")
        d._docker("kill", container)
        killed = time.monotonic()
        d._docker("start", container)
        d._wait_ready()
        return time.monotonic() - killed
    with harness.Faults() as faults:
        faults.kill_container("postgres")
        killed = time.monotonic()
    harness.wait_postgres(database=database)
    window = time.monotonic() - killed
    _postgrest_back()
    return window


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
    needs_pg()
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
                check = "select infrx.require_feature('credit_admission')"   # rollback.md's check
                for statement, role in ((job, None), (pins, "service_role"),
                                        (check, "service_role")):
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
