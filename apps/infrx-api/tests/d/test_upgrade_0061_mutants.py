#!/usr/bin/env python3
"""AP-04 (lane api-artifacts, R271): `0061_model_projects_artifacts.sql` on real PostgreSQL -
the upgrade over a database holding consumer history, its checks and its SQL mutation list
(R32/R40; needs Docker, skips visibly without it; plain PostgreSQL and, with
`INFRX_D1_IMAGE=supabase`, the Supabase image). Every check is named by a mutant.

    INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/d/test_upgrade_0061_mutants.py
    INFRX_D_TASK=ap4 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d -k 0061
"""
from __future__ import annotations

import json

import psycopg
import pytest

from infrx.state import migrations

from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_upgrade_d10 as d10

FILE = "0061_model_projects_artifacts.sql"
DB, DB_MUT, DB_UP = (f"{pgharness.DATABASE}_0061", f"{pgharness.DATABASE}_0061mut",
                     f"{pgharness.DATABASE}_0061up")
_reason = pgharness.unavailable()
LOGIN = "infrx_lab_control"
A, B = "a0610000-0000-4000-8000-00000000000a", "b0610000-0000-4000-8000-00000000000b"
PROJECT = "c0610000-0000-4000-8000-00000000000c"
TABLES = ("model_projects", "artifacts", "artifact_uploads", "artifact_imports",
          "model_project_revisions")
NEW_TABLES = {f"infrx.{t}" for t in TABLES}
HASH = "sha256:" + "a" * 64
FILES = json.dumps([{"relative_path": "config.json", "bytes": 2, "sha256": HASH,
                     "media_type": "application/json"}])


def refused(conn, sql: str, params=(), role: str | None = LOGIN):
    """The SQLSTATE `sql` fails with (as `role`, rolled back), or None when it runs."""
    try:
        with conn.transaction(force_rollback=True):
            if role:
                conn.execute(f"set local role {role}")
            conn.execute(sql, params)
    except psycopg.Error as error:
        return error.sqlstate
    return None


PROJECT_SQL = ("insert into infrx.model_projects (project_id, provider_org_id, slug, name, "
               "description, request_hash, created_by) values (%s, %s, %s, 'P', '', %s, 'u')")


def as_login(conn, *statements):
    """Each (sql, params) as the login in one rolled-back transaction: the last result, or
    the SQLSTATE of the first failure."""
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(f"set local role {LOGIN}")
            result = None
            for sql, params in statements:
                cur = conn.execute(sql, params)
                result = cur.fetchall() if cur.description else None
            return result
    except psycopg.Error as error:
        return error.sqlstate


ARTIFACT_SQL = ("insert into infrx.artifacts (artifact_id, project_id, provider_org_id, source, "
                "source_repo, source_commit, files, manifest_sha256, card, provenance, "
                "compatibility, verified_at, created_by) values ('d0610000-0000-4000-8000-"
                "00000000000d', %s, %s, %s, %s, %s, %s, %s, '{}', '{}', '{}', infrx.now(), 'u')")
IMPORT_SQL = ("insert into infrx.artifact_imports (import_id, project_id, provider_org_id, "
              "source_host, source_repo, source_commit, secret_ref, files, manifest_sha256, "
              "card, state, operation_id, request_hash, created_by) values ("
              "gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s, '{}', 'queued', "
              "gen_random_uuid(), %s, 'u')")
UPLOAD_SQL = ("insert into infrx.artifact_uploads (upload_id, project_id, provider_org_id, files, "
              "manifest_sha256, card, state, expires_at, operation_id, artifact_id, "
              "request_hash, created_by) values (gen_random_uuid(), %s, %s, %s, %s, '{}', %s, "
              "infrx.now(), %s, null, %s, 'u')")
P = (PROJECT_SQL, (PROJECT, A, "proj", HASH))


# ----------------------------------------------------------------------------- checks
def check_the_control_login_writes_through_its_policies(conn) -> str:
    """The login inserts and reads every 0061 row, moves an intake row and binds a project
    (RLS policies + grants + EXECUTE): without them the Lab control unit answers 503."""
    up = (UPLOAD_SQL, (PROJECT, A, FILES, HASH, "open", None, HASH))
    got = as_login(conn, P, up, ("update infrx.artifact_uploads set state = 'expired'", ()),
                   ("select count(*) from infrx.artifact_uploads where state = 'expired'", ()),)
    assert got == [(1,)], f"the login cannot write an upload session: {got}"
    bound = as_login(conn, P, ("select infrx.model_project_bind(%s)",
                               (json.dumps({"project_id": PROJECT, "provider_org_id": A}),)))
    assert isinstance(bound, list) and bound[0][0]["public_model_id"] == "prov-a/proj", bound
    return "insert/select/update/bind as the login"


def check_browser_roles_reach_nothing(conn) -> str:
    """anon/authenticated read and write no 0061 row and execute no 0061 function; the login
    holds select + insert (update on the two intake tables only)."""
    for role in ("anon", "authenticated"):
        reached = [f"{p} {t}" for t in TABLES for p in ("select", "insert", "update", "delete")
                   if conn.execute("select has_table_privilege(%s, %s, %s)",
                                   (role, f"infrx.{t}", p)).fetchone()[0]]
        assert not reached, f"{role} holds {reached}"
        assert not conn.execute("select has_function_privilege(%s, "
                                "'infrx.model_project_bind(jsonb)', 'execute')",
                                (role,)).fetchone()[0], f"{role} executes model_project_bind"
    held = {t: sorted(p for p in ("select", "insert", "update", "delete") if conn.execute(
        "select has_table_privilege(%s, %s, %s)", (LOGIN, f"infrx.{t}", p)).fetchone()[0])
        for t in TABLES}
    assert held == {"model_projects": ["insert", "select"], "artifacts": ["insert", "select"],
                    "artifact_uploads": ["insert", "select", "update"],
                    "artifact_imports": ["insert", "select", "update"],
                    "model_project_revisions": ["insert", "select"]}, held
    return "browser roles 42501; login select/insert(+update intake)"


def check_an_artifact_and_a_revision_never_change(conn) -> str:
    art = (ARTIFACT_SQL, (PROJECT, A, "upload", None, None, FILES, HASH))
    link = ("insert into infrx.model_project_revisions values (gen_random_uuid(), %s, %s, "
            "'d0610000-0000-4000-8000-00000000000d', '{}', %s, 'u')", (PROJECT, A, HASH))
    for table in ("artifacts", "model_project_revisions"):
        for edit in (f"update infrx.{table} set created_by = 'x'", f"delete from infrx.{table}"):
            with conn.transaction(force_rollback=True):
                for statement in (P, art, link):
                    conn.execute(*statement)
                state = refused(conn, edit, role=None)
            assert state == "23514", f"{edit}: {state}"
    return "artifacts and revision links: 23514 on update and delete"


def check_a_project_binds_once_to_its_own_public_id(conn) -> str:
    """Only the first bind writes; a slug edit is refused; a public id another model holds is
    `state_conflict`; another provider's project is `not_found`."""
    args = json.dumps({"project_id": PROJECT, "provider_org_id": A})
    with conn.transaction(force_rollback=True):
        conn.execute(*P)
        first = conn.execute("select infrx.model_project_bind(%s)", (args,)).fetchone()[0]
        again = conn.execute("select infrx.model_project_bind(%s)", (args,)).fetchone()[0]
        assert first == again and first["public_model_id"] == "prov-a/proj", (first, again)
        state = refused(conn, "update infrx.model_projects set slug = 'other'", role=None)
        assert state == "23514", f"a bound project's slug moved ({state})"
        other = conn.execute("insert into public.models (id, name, provider, description, "
                             "status, base_url, served_model, input_usd_per_m, output_usd_per_m, "
                             "context_tokens, input_modalities, output_modalities, "
                             "provider_org_id) values ('prov-a/other', 'o', 'o', '', 'live', '', "
                             "'o', 0, 0, 0, '{}', '{}', %s) returning model_uuid",
                             (A,)).fetchone()[0]
        rebind = refused(conn, "update infrx.model_projects set model_uuid = %s, "
                         "public_model_id = 'prov-a/other'", (other,), role=None)
        assert rebind == "23514", f"a bound project was re-bound ({rebind})"
    with conn.transaction(force_rollback=True):
        conn.execute(*P)
        state = refused(conn, "update infrx.model_projects set name = 'renamed'", role=None)
        assert state == "23514", f"an unbound project's name moved ({state})"
    with conn.transaction(force_rollback=True):
        conn.execute(PROJECT_SQL, (PROJECT, A, "taken", HASH))
        conn.execute("insert into public.models (id, name, provider, description, status, "
                     "base_url, served_model, input_usd_per_m, output_usd_per_m, context_tokens, "
                     "input_modalities, output_modalities) values ('prov-a/taken', 't', 't', "
                     "'', 'live', '', 't', 0, 0, 0, '{}', '{}')")
        try:
            with conn.transaction():
                conn.execute("select infrx.model_project_bind(%s)", (args,))
            taken = None
        except psycopg.Error as error:
            taken = (error.sqlstate, str(error).split(":")[0])
        assert taken == ("P0001", "state_conflict"), taken
    foreign = json.dumps({"project_id": PROJECT, "provider_org_id": B})
    with conn.transaction(force_rollback=True):
        conn.execute(*P)
        try:
            with conn.transaction():
                conn.execute("select infrx.model_project_bind(%s)", (foreign,))
            other = None
        except psycopg.Error as error:
            other = (error.sqlstate, str(error).split(":")[0])
        assert other == ("P0001", "not_found"), other
    return "bind once; state_conflict; not_found; slug immutable"


def check_a_slug_is_unique_per_provider(conn) -> str:
    second = (PROJECT_SQL, ("c0610000-0000-4000-8000-0000000000c2", A, "proj", HASH))
    assert as_login(conn, P, second) == "23505", "two projects share a slug in one provider"
    other = (PROJECT_SQL, ("c0610000-0000-4000-8000-0000000000c3", B, "proj", HASH))
    assert as_login(conn, P, other) is None, "a slug is unique across providers"
    return "23505 within a provider, free across"


def check_an_import_is_pinned_and_holds_no_credential(conn) -> str:
    def imp(host="huggingface.co", commit="f" * 40, ref="env:HF_TOKEN"):
        return (IMPORT_SQL, (PROJECT, A, host, "Org/Repo", commit, ref, FILES, HASH, HASH))
    assert as_login(conn, P, imp()) is None
    for bad in (imp(commit="main"), imp(host="evil.example"), imp(ref="hf_" + "x" * 30)):
        assert as_login(conn, P, bad) == "23514", f"refused nothing: {bad[1][2:6]}"
    art = (ARTIFACT_SQL, (PROJECT, A, "import", None, None, FILES, HASH))
    assert as_login(conn, P, art) == "23514", "an imported artifact without its commit"
    return "commit 40-hex, allowlisted host, reference-shaped secret, pinned import artifact"


def check_an_intake_states_its_operation_and_artifact(conn) -> str:
    def up(state, op):
        return (UPLOAD_SQL, (PROJECT, A, FILES, HASH, state, op, HASH))
    assert as_login(conn, P, up("open", None)) is None
    assert as_login(conn, P, up("verifying", None)) == "23514", "verifying with no operation"
    assert as_login(conn, P, up("verified", "e0610000-0000-4000-8000-00000000000e")) == \
        "23514", "verified with no artifact"
    return "verifying needs an operation; verified needs an artifact"


CHECKS = {c.__name__: c for c in (
    check_the_control_login_writes_through_its_policies, check_browser_roles_reach_nothing,
    check_an_artifact_and_a_revision_never_change, check_a_project_binds_once_to_its_own_public_id,
    check_a_slug_is_unique_per_provider, check_an_import_is_pinned_and_holds_no_credential,
    check_an_intake_states_its_operation_and_artifact)}
LOGIN_OK, BROWSER, IMMUTABLE, BIND, SLUG, IMPORT, INTAKE = CHECKS


def seed(conn) -> None:
    if pgharness.ON_SUPABASE:           # `postgres` is no superuser there: `set role` needs it
        pgharness._sb(conn.info.dbname, f"grant {LOGIN} to postgres with set true")
    conn.execute("insert into infrx.provider_orgs (provider_org_id, slug, display_name, "
                 "created_by) values (%s, 'prov-a', 'A', 't'), (%s, 'prov-b', 'B', 't')", (A, B))


# ----------------------------------------------------------------------------- mutants
def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    _s("ap04_no_login_policy", "    execute format('create policy api_artifacts_control on "
       "infrx.%I to infrx_lab_control '\n                   'using (true) with check (true)', r);\n",
       "", LOGIN_OK, "the Lab control unit answers 503 on every artifact route (RLS refuses)"),
    _s("ap04_no_bind_execute",
       "grant execute on function infrx.model_project_bind(jsonb) to infrx_lab_control;",
       "", LOGIN_OK, "the first serving revision of every project fails on the unit"),
    _s("ap04_intake_not_updatable", "grant select, insert, update on infrx.artifact_uploads, "
       "infrx.artifact_imports\n  to infrx_lab_control;",
       "grant select, insert on infrx.artifact_uploads, infrx.artifact_imports\n"
       "  to infrx_lab_control;", LOGIN_OK, "no upload or import ever leaves `open`/`queued`"),
    _s("ap04_bind_to_authenticated",
       "grant execute on function infrx.model_project_bind(jsonb) to infrx_lab_control;",
       "grant execute on function infrx.model_project_bind(jsonb) to infrx_lab_control, "
       "authenticated;", BROWSER, "a browser session writes public.models rows"),
    _s("ap04_tables_to_authenticated", "grant select, insert on infrx.model_projects, "
       "infrx.artifacts, infrx.model_project_revisions\n  to infrx_lab_control;",
       "grant select, insert on infrx.model_projects, infrx.artifacts, "
       "infrx.model_project_revisions\n  to infrx_lab_control, authenticated;", BROWSER,
       "a signed-in browser reads every provider's projects and artifacts"),
    _s("ap04_projects_updatable", "grant select, insert on infrx.model_projects, "
       "infrx.artifacts, infrx.model_project_revisions\n  to infrx_lab_control;",
       "grant select, insert, update on infrx.model_projects, infrx.artifacts, "
       "infrx.model_project_revisions\n  to infrx_lab_control;", BROWSER,
       "the route login rewrites artifacts past the service"),
    _s("ap04_artifacts_mutable", "create or replace trigger artifacts_immutable before update or "
       "delete", "create or replace trigger artifacts_immutable before delete", IMMUTABLE,
       "a verified artifact's manifest is rewritten after a revision pinned it"),
    _s("ap04_revisions_deletable", "create or replace trigger model_project_revisions_immutable "
       "before update or delete", "create or replace trigger model_project_revisions_immutable "
       "before update", IMMUTABLE, "a serving version loses the artifact it was built from"),
    _s("ap04_project_rewritable", "  if old.model_uuid is not null\n     or (to_jsonb(new)",
       "  if old.model_uuid is not null\n     and (to_jsonb(new)", BIND,
       "a project's slug or owner changes under its revisions"),
    _s("ap04_rebind", "  if old.model_uuid is not null\n     or", "  if false\n     or", BIND,
       "a bound project is re-pointed at another model"),
    _s("ap04_bind_takes_any_public_id", "    on conflict (id) do nothing\n", "\n", BIND,
       "a taken public model id is a 500, not a state_conflict"),
    _s("ap04_bind_any_provider", "     and provider_org_id = (p_args->>'provider_org_id')::uuid "
       "for update;", "     for update;", BIND,
       "a provider binds (and names) another provider's project"),
    _s("ap04_slug_global_or_none", "  constraint model_projects_slug_key unique (provider_org_id, "
       "slug),\n", "", SLUG, "two projects of one provider answer to one slug"),
    _s("ap04_mutable_commit", "  source_commit text not null check (source_commit ~ "
       "'^[0-9a-f]{40}$'),\n  -- a reference", "  source_commit text not null,\n  -- a reference",
       IMPORT, "an import pinned to `main` changes under its artifact"),
    _s("ap04_any_host", "  source_host text not null check (source_host in ('huggingface.co')),",
       "  source_host text not null,", IMPORT, "the worker fetches from any host"),
    _s("ap04_token_stored", "  secret_ref text check (secret_ref ~ '^(env:[A-Z][A-Z0-9_]{0,63}|"
       "ssm:/[A-Za-z0-9_./-]{1,200})$'),", "  secret_ref text,", IMPORT,
       "a pasted token is stored in the import row"),
    _s("ap04_import_unpinned", "  constraint artifacts_source_pinned check (source <> 'import'\n"
       "    or (source_repo is not null and source_commit is not null))\n", "  constraint "
       "artifacts_source_pinned check (true)\n", IMPORT,
       "an imported artifact names no source commit"),
    _s("ap04_verifying_without_operation", "  constraint artifact_uploads_verifying_has_operation"
       "\n    check (state in ('open', 'expired') or operation_id is not null)",
       "  constraint artifact_uploads_verifying_has_operation\n    check (true)", INTAKE,
       "a session is verifying under no operation a worker can lease"),
    _s("ap04_verified_without_artifact", "  constraint artifact_uploads_verified_has_artifact\n"
       "    check ((state = 'verified') = (artifact_id is not null)),", "  constraint "
       "artifact_uploads_verified_has_artifact\n    check (true),", INTAKE,
       "a session reads verified with no artifact behind it"),
)


def kill(mutant) -> tuple[str, str]:
    import sys
    return d7.kill(mutant, DB_MUT, sys.modules[__name__])


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    if _reason is not None:
        pytest.skip(f"task-local PostgreSQL unavailable: {_reason}")
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_0061_checks(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
def test_0061_upgrade_preserves_history_and_reruns() -> None:
    """0061 over a database holding consumer history (every earlier file applied): only its
    five tables appear, empty; no row count, money sum, job, column, relation grant or
    existing function grant moves; a second application changes nothing."""
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    mine = tuple(f for f in everything if f[0] == FILE)
    assert mine, f"{FILE} is not in the migration directory"
    pgharness.ensure()
    pgharness.recreate(DB_UP)
    pgharness.apply(DB_UP, tuple(f for f in everything if f not in mine))
    with pgharness.connect(DB_UP) as conn:
        d10.seed_history(conn)
        before = d10.snapshot(conn)
        pgharness.apply(DB_UP, mine)
        after = d10.snapshot(conn)
        assert set(after["counts"]) - set(before["counts"]) == NEW_TABLES
        assert all(after["counts"][t] == 0 for t in NEW_TABLES)
        assert {t: n for t, n in after["counts"].items() if t not in NEW_TABLES} == \
            before["counts"]
        assert after["sums"] == before["sums"] and after["jobs"] == before["jobs"]
        assert {k: v for k, v in after["acl"].items() if k in before["acl"]} == before["acl"]
        assert {k: v for k, v in after["cols"].items() if k in before["cols"]} == before["cols"]
        assert {k: v for k, v in after["fns"].items() if k in before["fns"]} == before["fns"]
        pgharness.apply(DB_UP, mine)
        assert d10.snapshot(conn) == after, "0061 is not re-runnable"


def test_the_lists_are_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in CHECKS for m in SQL_MUTANTS)


def test_every_case_is_covered_by_a_mutant() -> None:
    uncovered = sorted(set(CHECKS) - {m.check for m in SQL_MUTANTS})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL_MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = kill(mutant)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
