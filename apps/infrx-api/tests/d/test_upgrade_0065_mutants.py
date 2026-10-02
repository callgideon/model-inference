#!/usr/bin/env python3
"""AP-00 00d remainder (lane api-schema-2, wave 7 batch 2, R271): `0065_identity_functions.sql`
(SR-AP01-1) over AP-01's identity world on real PostgreSQL - each check, the SQL mutation list that must break it (R32/R40; needs Docker,
skips visibly without it; plain PostgreSQL and, with `INFRX_D1_IMAGE=supabase`, the Supabase
image), and the guards. Every check is named by a mutant. The whole-set R151 rehearsal
(0060-0066 over hosted history) is `test_control_ops_upgrade.py`.

    INFRX_MUTANTS=all INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_upgrade_0065_mutants.py
"""
from __future__ import annotations

import psycopg
import pytest

from infrx.console import session as identity
from infrx.state import migrations

from ..ap01 import worlds as w
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness

F65 = "0065_identity_functions.sql"
DB, DB_MUT = f"{pgharness.DATABASE}_0065", f"{pgharness.DATABASE}_0065mut"
_reason = pgharness.unavailable()
LOGIN = "infrx_lab_control"
OTHERS = ("anon", "authenticated", "infrx_runtime", "infrx_monitor")
IDENTITY = ("infrx.identity_account(uuid)", "infrx.identity_user_by_email(text)",
            "infrx.identity_members(uuid)", "infrx.identity_grant_member(uuid,uuid,text,text)",
            "infrx.identity_revoke_member(uuid,uuid)",
            "infrx.identity_create_provider(text,text,text)")
TWIN = "a1a1a1a1-0000-4000-8000-000000000099"         # a second profile with CONSUMER's email


def as_login(conn, sql: str, params=(), *, keep: bool = False):
    """`sql` as the Lab control login in one savepoint, rolled back unless `keep` (then its
    writes stay in the caller's transaction): its rows, or the failure as (SQLSTATE,
    message). The harness login takes SET on the role inside the caller's transaction:
    Supabase's `postgres` is no superuser and holds 0043's role only WITH ADMIN (PostgreSQL
    16+), so neither `set role` nor `set session authorization` works bare."""
    try:
        with conn.transaction(force_rollback=not keep):
            conn.execute(f"grant {LOGIN} to current_user with inherit false, set true")
            conn.execute(f"set local role {LOGIN}")
            rows = conn.execute(sql, params).fetchall()
            conn.execute("reset role")
            return rows
    except psycopg.Error as error:
        return error.sqlstate, error.diag.message_primary


def _can(conn, role: str, fn: str) -> bool:
    return conn.execute("select has_function_privilege(%s, %s, 'execute')",
                        (role, fn)).fetchone()[0]


# ------------------------------------------------------------------------ 0065 checks
def check_the_identity_doors_answer_as_the_platform_sql(conn) -> str:
    """SR-AP01-1: on the control login, the account, email and member reads give exactly what
    PgIdentity's platform-role statements give for every person of AP-01's world - verified,
    suspended, granted, operator; an email two profiles share and an unknown one resolve to
    nobody; a provider lists only its current members."""
    for user in [*w.USERS, w._id(98)]:
        door = as_login(conn, "select * from infrx.identity_account(%s)", (user,))
        platform = conn.execute(identity.ACCOUNT, (user,)).fetchall()
        assert [tuple(str(v) if i == 2 and v else v for i, v in enumerate(r)) for r in door] \
            == platform, (user, door, platform)
    for email, want in ((w.EMAIL[w.DEV_A].upper(), w.DEV_A), (w.EMAIL[w.CONSUMER], None),
                        ("nobody@example.com", None)):
        got = as_login(conn, "select infrx.identity_user_by_email(%s)::text", (email,))
        assert got == [(want,)], (email, got)
    for provider in (w.A, w.B, w.NEMO):
        door = as_login(conn, "select * from infrx.identity_members(%s)", (provider,))
        platform = conn.execute(identity.MEMBERS, (provider,)).fetchall()
        assert [(str(r[0]), *r[1:]) for r in door] == platform, (provider, door)
    return f"{len(w.USERS) + 1} accounts, 3 emails, 3 member lists = the platform SQL"


def check_a_membership_is_granted_and_revoked_once(conn) -> str:
    """0007's one current membership per pair: a grant makes it once (a retry answers it,
    `created` false), another role is a state_conflict, a revocation answers the revoked
    row (again on a retry), and a never-member is not_found."""
    grant = "select role, revoked_at is null, created from infrx.identity_grant_member(%s, %s, %s, 'ops')"
    revoke = "select role, revoked_at is not null from infrx.identity_revoke_member(%s, %s)"
    with conn.transaction(force_rollback=True):
        conn.execute(f"grant {LOGIN} to current_user with inherit false, set true")
        conn.execute(f"set local role {LOGIN}")
        made = conn.execute(grant, (w.B, w.VIEWER_A, "viewer")).fetchall()
        again = conn.execute(grant, (w.B, w.VIEWER_A, "viewer")).fetchall()
        assert (made, again) == ([("viewer", True, True)], [("viewer", True, False)]), \
            (made, again)
        try:
            with conn.transaction():
                conn.execute(grant, (w.B, w.VIEWER_A, "developer"))
            other = None
        except psycopg.Error as error:
            other = (error.sqlstate, error.diag.message_primary)
        assert other == ("P0001", "state_conflict: another current role"), other
        gone = conn.execute(revoke, (w.B, w.VIEWER_A)).fetchall()
        assert gone == conn.execute(revoke, (w.B, w.VIEWER_A)).fetchall() == \
            [("viewer", True)], gone
        assert conn.execute("select count(*) from infrx.identity_members(%s) where user_id = %s",
                            (w.B, w.VIEWER_A)).fetchone() == (0,)
    never = as_login(conn, revoke, (w.B, w.CONSUMER))
    assert never == ("P0001", "not_found: member"), never
    return "grant once, retry answers, other role 409, revoke answers twice, never-member 404"


def check_a_provider_slug_is_created_once_and_never_reassigned(conn) -> str:
    """0007's unique slug: a new slug is created once (a retry answers it, `created` false);
    an existing slug under another name is a state_conflict and renames nothing."""
    make = "select slug, display_name, created from infrx.identity_create_provider(%s, %s, 'ops')"
    with conn.transaction(force_rollback=True):
        conn.execute(f"grant {LOGIN} to current_user with inherit false, set true")
        conn.execute(f"set local role {LOGIN}")
        first = conn.execute(make, ("lab-c", "Lab C")).fetchall()
        again = conn.execute(make, ("lab-c", "Lab C")).fetchall()
        assert (first, again) == ([("lab-c", "Lab C", True)], [("lab-c", "Lab C", False)])
    taken = as_login(conn, make, (w.SLUG[w.NEMO], "Someone Else"))
    assert taken == ("P0001", "state_conflict: slug taken"), taken
    assert conn.execute("select display_name from infrx.provider_orgs where slug = %s",
                        (w.SLUG[w.NEMO],)).fetchone() == (w.NAME[w.NEMO],)
    return "created once, retry answers, a taken slug is refused and keeps its name"


def check_only_the_platform_and_the_control_login_execute_the_identity_doors(conn) -> str:
    """R271: no browser role, nor the gateway's or the monitor's pinned dedicated login,
    executes a 0065 door; service_role and the control login execute all six."""
    reached = [f"{r} {f}" for r in OTHERS for f in IDENTITY if _can(conn, r, f)]
    assert not reached, f"0065 reached by {reached}"
    missing = [f"{r} {f}" for r in ("service_role", LOGIN) for f in IDENTITY
               if not _can(conn, r, f)]
    assert not missing, f"0065 refused to {missing}"
    return f"{len(IDENTITY)} doors: service_role + {LOGIN} only"


CHECKS = {c.__name__: c for c in (
    check_the_identity_doors_answer_as_the_platform_sql,
    check_a_membership_is_granted_and_revoked_once,
    check_a_provider_slug_is_created_once_and_never_reassigned,
    check_only_the_platform_and_the_control_login_execute_the_identity_doors)}
READS, MEMBERS, PROVIDERS, DOORS = CHECKS


def seed(conn) -> None:
    """AP-01's world, a second profile sharing CONSUMER's email and a revoked member of A."""
    w.seed_pg(conn)
    conn.execute("insert into auth.users (id, email) values (%s, %s)",
                 (TWIN, w.EMAIL[w.CONSUMER].upper()))
    conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                 "granted_by, granted_at, revoked_at) values (%s, %s, 'viewer', 'ops', "
                 "infrx.now() - interval '2 days', infrx.now() - interval '1 day')",
                 (w.A, w.DEV_B))


# ----------------------------------------------------------------------------- mutants
def _m65(name, old, new, check, why, **kw):
    return _d.Mutant(name, F65, old, new, "lab", check, why, **kw)


GRANT65 = "  to service_role, infrx_lab_control;"
SQL_MUTANTS = (
    _m65("ap0065_verified_any", "  select p.is_operator, v.verification_evidence_ref is not null,",
         "  select p.is_operator, true,", READS,
         "an unverified user reads as verified on the Lab unit"),
    _m65("ap0065_suspension_ignored", "         coalesce(o.suspended, false), e.amount::text",
         "         false, e.amount::text", READS,
         "a suspended organization's members act as if it were not"),
    _m65("ap0065_email_ambiguous", "  select case when count(*) = 1 then min(id::text)::uuid end",
         "  select case when count(*) >= 1 then min(id::text)::uuid end", READS,
         "an email two profiles share adds whichever sorts first"),
    _m65("ap0065_email_case", "    from public.profiles where lower(email) = lower(p_email) $$;",
         "    from public.profiles where email = p_email $$;", READS,
         "an administrator adding `Dev@…` finds nobody"),
    _m65("ap0065_revoked_members_listed",
         "   where m.provider_org_id = p_provider and m.revoked_at is null\n   order by",
         "   where m.provider_org_id = p_provider\n   order by", READS,
         "a revoked member is still listed"),
    _m65("ap0065_members_any_provider",
         "   where m.provider_org_id = p_provider and m.revoked_at is null\n   order by",
         "   where m.revoked_at is null\n   order by", READS,
         "a workspace lists another workspace's members"),
    _m65("ap0065_role_switch_silent",
         "    raise exception 'state_conflict: another current role' using errcode = 'P0001';",
         "    null;", MEMBERS, "a role change answers the old role as if it had been made"),
    _m65("ap0065_grant_always_created", "  on conflict (provider_org_id, user_id) where "
         "revoked_at is null do nothing;\n  v_created := found;", "  on conflict "
         "(provider_org_id, user_id) where revoked_at is null do nothing;\n  v_created := true;",
         MEMBERS, "a retried grant reports a new membership (201 twice)"),
    _m65("ap0065_revoke_unknown_ok",
         "  if not found then raise exception 'not_found: member' using errcode = 'P0001'; end if;",
         "", MEMBERS, "revoking a never-member answers 200 with nothing"),
    _m65("ap0065_revoke_noop", "  update infrx.provider_memberships set revoked_at = "
         "greatest(infrx.now(), granted_at)\n", "  update infrx.provider_memberships set "
         "revoked_at = null\n", MEMBERS, "a revoked member keeps access"),
    _m65("ap0065_slug_reassignable",
         "    raise exception 'state_conflict: slug taken' using errcode = 'P0001';", "    null;",
         PROVIDERS, "creating NemoStation's slug under another name answers as if it worked"),
    _m65("ap0065_provider_always_created", "  on conflict (slug) do nothing;\n  v_created := found;",
         "  on conflict (slug) do nothing;\n  v_created := true;", PROVIDERS,
         "a retried provider creation reports a new provider"),
    _m65("ap0065_browser_executes", GRANT65, "  to service_role, infrx_lab_control, authenticated;",
         DOORS, "a browser session lists members and creates providers around FastAPI"),
    _m65("ap0065_runtime_granted", GRANT65, "  to service_role, infrx_lab_control, infrx_runtime;",
         DOORS, "the gateway's pinned dedicated login silently gains six functions"),
    _m65("ap0065_control_unit_refused", GRANT65, "  to service_role;", DOORS,
         "lab_workspaces answers 503 on the Lab unit"),
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
def test_0065_checks(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


def test_the_lists_are_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in CHECKS for m in SQL_MUTANTS)


def test_no_mutant_anchors_in_a_superseded_function_body() -> None:
    found = _d.superseded(SQL_MUTANTS)
    assert not found, found


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
