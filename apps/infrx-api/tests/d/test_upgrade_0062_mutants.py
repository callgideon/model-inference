#!/usr/bin/env python3
"""AP-05 (lane api-hosting, R271): `0062_deployments_hosting.sql` on real PostgreSQL - its
checks, the upgrade over a database holding consumer history (re-run, the file's own ROLLBACK
lines, forward again) and its SQL mutation list (R32/R40; needs Docker, skips visibly
without it; plain PostgreSQL and, with `INFRX_D1_IMAGE=supabase`, the Supabase image).
Every check is named by a mutant.

    INFRX_D_TASK=ap5 uv run --frozen pytest -q tests/d/test_upgrade_0062_mutants.py
    INFRX_D_TASK=ap5 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d -k 0062
"""
from __future__ import annotations

import json
import uuid

import psycopg
import pytest

from infrx.state import migrations

from . import checks_admission as ca
from . import checks_credit as cc
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_upgrade_d10 as d10

FILE = "0062_deployments_hosting.sql"
DB, DB_MUT, DB_UP = (f"{pgharness.DATABASE}_0062", f"{pgharness.DATABASE}_0062mut",
                     f"{pgharness.DATABASE}_0062up")
_reason = pgharness.unavailable()
LOGIN = "infrx_lab_control"
A, B = cc.NEMO, "b0620000-0000-4000-8000-00000000000b"
TABLES = ("hosting_deployments", "hosting_allocations", "hosting_receipts")
NEW_TABLES = {f"infrx.{t}" for t in TABLES}
NEW_FUNCTIONS = {"infrx.hosting_request(jsonb)", "infrx.hosting_fence(jsonb)",
                 "infrx.hosting_active(jsonb)", "infrx.hosting_allocations_guard()"}
HASH = "sha256:" + "a" * 64
ACTOR = {"audience": "session", "user_id": cc.PROVIDER_DEV_USER, "provider_org_id": A}
CTX: dict = {}


def failure(conn, sql: str, params=(), role: str | None = LOGIN):
    """(SQLSTATE, refusal code) `sql` fails with (as `role`, rolled back), or None."""
    try:
        with conn.transaction(force_rollback=True):
            if role:
                conn.execute(f"set local role {role}")
            conn.execute(sql, params)
    except psycopg.Error as error:
        return error.sqlstate, str(error).split(":")[0]
    return None


def call(conn, function: str, args: dict, role: str | None = LOGIN):
    """`infrx.<function>(args)` (committed) as `role`."""
    with conn.transaction():
        if role:
            conn.execute(f"set local role {role}")
        return conn.execute(f"select infrx.{function}(%s)", (json.dumps(args),)).fetchone()[0]


def request(conn, key: str, *, deployment: str | None = None, digest: str = HASH,
            role: str | None = LOGIN, **change) -> dict:
    d = {"deployment_revision_id": deployment or str(uuid.uuid4()),
         "endpoint_id": CTX["endpoint"], "provider_org_id": A,
         "serving_version_id": CTX["serving"], "max_input_tokens": 100,
         "max_output_tokens": 10}
    args = {"deployment": d, "profile_id": "marlin2b-vllm-l40s-bf16-v1",
            "request": {"max_input_tokens": 100}, "expire_after_s": 3600, "actor": ACTOR,
            "idempotency_key": key, "input_hash": digest, **change}
    return call(conn, "hosting_request", args, role)


def leased(conn, key: str) -> tuple[str, str, int]:
    """A requested deployment whose create operation a worker leased: (op, deployment, fence)."""
    started = request(conn, key)
    op = started["operation"]["operation_id"]
    fence = call(conn, "control_op_lease", {"operation_id": op, "owner": "w", "ttl_s": 60})
    return op, started["operation"]["resource_id"], fence["fence"]


def fenced(conn, op: str, deployment: str, fence: int, sql: str, params=()):
    """`sql` as the login after `hosting_fence` in one rolled-back transaction: None or the
    (SQLSTATE, refusal) it failed with."""
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(f"set local role {LOGIN}")
            conn.execute("select infrx.hosting_fence(%s)", (json.dumps({
                "operation_id": op, "fence": fence, "deployment_revision_id": deployment}),))
            conn.execute(sql, params)
    except psycopg.Error as error:
        return error.sqlstate, str(error).split(":")[0]
    return None


ALLOCATE = ("insert into infrx.hosting_allocations (allocation_id, deployment_revision_id, "
            "slot, port, resource_tag, operation_id, reserved_at) values (%s, %s, %s, 8100, %s, "
            "%s, infrx.now())")


def allocation(deployment: str, op: str, slot: str | None = None, aid=None) -> tuple:
    """An allocation row's values; a slot of its own unless one is named."""
    return (aid or str(uuid.uuid4()), deployment, slot or f"pilot/{uuid.uuid4().hex[:12]}",
            f"infrx-hosting-{deployment}", op)


RECEIPT = ("insert into infrx.hosting_receipts (receipt_id, deployment_revision_id, "
           "allocation_id, kind, passed, observed, reasons, operation_id, checked_at, "
           "expires_at) values (gen_random_uuid(), %s, %s, %s, %s, '{}', %s, %s, infrx.now(), "
           "infrx.now() + interval '1 hour')")


# ----------------------------------------------------------------------------- checks
def check_the_control_login_works_through_its_functions_and_policies(conn) -> str:
    """The login requests a deployment, holds its lease, reserves and launches an allocation
    and records a receipt; without the grants and policies the Lab control unit is a 503."""
    try:
        return _login_works(conn)
    except psycopg.Error as refused:
        raise AssertionError(f"the login cannot work a deployment: {refused.sqlstate} "
                             f"{str(refused).splitlines()[0]}") from None


def _login_works(conn) -> str:
    op, deployment, fence = leased(conn, "login-1")
    aid = str(uuid.uuid4())
    for sql, params in ((ALLOCATE, allocation(deployment, op, aid=aid)),
                        ("update infrx.hosting_allocations set state = 'launched', launched_at = "
                         "infrx.now() where allocation_id = %s", (aid,)),
                        (RECEIPT, (deployment, aid, "identity", True, "[]", op))):
        with conn.transaction():
            conn.execute(f"set local role {LOGIN}")
            conn.execute("select infrx.hosting_fence(%s)", (json.dumps({
                "operation_id": op, "fence": fence, "deployment_revision_id": deployment}),))
            conn.execute(sql, params)
    with conn.transaction(force_rollback=True):
        conn.execute(f"set local role {LOGIN}")
        seen = conn.execute("select h.profile_id, a.state, r.kind, d.state from "
                            "infrx.hosting_deployments h join infrx.hosting_allocations a "
                            "using (deployment_revision_id) join infrx.hosting_receipts r "
                            "using (deployment_revision_id) join infrx.deployment_revisions d "
                            "using (deployment_revision_id) where h.deployment_revision_id = %s",
                            (deployment,)).fetchall()
    assert seen == [("marlin2b-vllm-l40s-bf16-v1", "launched", "identity", "draft")], seen
    assert call(conn, "hosting_active", {"deployment_revision_id": deployment}) == [op]
    return "request, fence, allocate, launch, receipt, read as the login"


def check_browser_roles_reach_nothing(conn) -> str:
    for role in ("anon", "authenticated"):
        reached = [f"{p} {t}" for t in TABLES for p in ("select", "insert", "update", "delete")
                   if conn.execute("select has_table_privilege(%s, %s, %s)",
                                   (role, f"infrx.{t}", p)).fetchone()[0]]
        assert not reached, f"{role} holds {reached}"
        executes = [f for f in NEW_FUNCTIONS if conn.execute(
            "select has_function_privilege(%s, %s, 'execute')", (role, f)).fetchone()[0]]
        assert not executes, f"{role} executes {executes}"
    held = {t: sorted(p for p in ("select", "insert", "update", "delete") if conn.execute(
        "select has_table_privilege(%s, %s, %s)", (LOGIN, f"infrx.{t}", p)).fetchone()[0])
        for t in TABLES}
    assert held == {"hosting_deployments": ["select"],
                    "hosting_allocations": ["insert", "select"],
                    "hosting_receipts": ["insert", "select"]}, held
    columns = sorted(c for (c,) in conn.execute(
        "select column_name from information_schema.column_privileges where grantee = %s and "
        "table_name = 'hosting_allocations' and privilege_type = 'UPDATE'", (LOGIN,)))
    assert columns == ["launched_at", "released_at", "state"], columns
    return "browser roles nothing; login select (+insert, + update of 3 allocation columns)"


def check_a_request_is_one_transaction_and_replays(conn) -> str:
    """The operation, the draft and the hosting row commit together: a refused request leaves
    no operation; a replay writes nothing and names the first deployment; another body under
    the key is idempotency_conflict."""
    ops = conn.execute("select count(*) from infrx.control_operations").fetchone()[0]
    bad = failure(conn, "select infrx.hosting_request(%s)", (json.dumps({
        "deployment": {"deployment_revision_id": str(uuid.uuid4()), "endpoint_id": CTX["endpoint"],
                       "provider_org_id": A, "serving_version_id": CTX["serving"],
                       "max_input_tokens": 100, "max_output_tokens": 10},
        "profile_id": "NOT A PROFILE", "request": {}, "expire_after_s": 3600, "actor": ACTOR,
        "idempotency_key": "refused", "input_hash": HASH}),))
    assert bad == ("P0001", "invalid_request"), bad
    assert conn.execute("select count(*) from infrx.control_operations").fetchone()[0] == ops
    first = request(conn, "replay-1")
    again = request(conn, "replay-1")
    assert (first["replayed"], again["replayed"]) == (False, True)
    assert again["operation"]["resource_id"] == first["operation"]["resource_id"]
    drafts = conn.execute("select count(*) from infrx.hosting_deployments h join "
                          "infrx.control_operations o using (operation_id) where "
                          "o.operation_id = %s", (first["operation"]["operation_id"],)).fetchone()
    assert drafts == (1,), drafts
    deployment = first["operation"]["resource_id"]
    row = conn.execute("select state, visibility, environment from infrx.deployment_revisions "
                       "where deployment_revision_id = %s", (deployment,)).fetchone()
    assert row == ("draft", "private", "dev"), row
    clash = failure(conn, "select infrx.hosting_request(%s)", (json.dumps({
        "deployment": {"deployment_revision_id": str(uuid.uuid4()), "endpoint_id": CTX["endpoint"],
                       "provider_org_id": A, "serving_version_id": CTX["serving"],
                       "max_input_tokens": 100, "max_output_tokens": 10},
        "profile_id": "p", "request": {}, "expire_after_s": 3600, "actor": ACTOR,
        "idempotency_key": "replay-1", "input_hash": "sha256:" + "b" * 64}),))
    assert clash == ("P0001", "idempotency_conflict"), clash
    return "atomic; replay names the first deployment; idempotency_conflict"


def check_a_write_needs_the_live_lease_of_an_operation_on_its_deployment(conn) -> str:
    op, deployment, fence = leased(conn, "fence-1")
    write = (ALLOCATE, allocation(deployment, op))
    assert fenced(conn, op, deployment, fence, *write) is None
    assert fenced(conn, op, deployment, fence - 1, *write) == ("P0001", "state_conflict")
    other_op, other, other_fence = leased(conn, "fence-2")
    assert fenced(conn, other_op, deployment, other_fence, *write) == ("P0001", "not_found")
    with conn.transaction(force_rollback=True):
        conn.execute("select infrx_test.advance(61)")
        assert fenced(conn, op, deployment, fence, *write) == ("P0001", "state_conflict")
    call(conn, "control_op_finish", {"operation_id": other_op, "fence": other_fence,
                                     "state": "succeeded"})
    assert fenced(conn, other_op, other, other_fence, *(ALLOCATE, allocation(
        other, other_op))) == ("P0001", "state_conflict")
    return "stale fence, expired lease, finished operation: state_conflict; other: not_found"


def check_a_slot_and_a_deployment_hold_one_allocation(conn) -> str:
    op, deployment, _ = leased(conn, "slot-1")
    op2, second, _ = leased(conn, "slot-2")
    aid = str(uuid.uuid4())
    with conn.transaction(force_rollback=True):
        conn.execute(ALLOCATE, allocation(deployment, op, slot="pilot/one", aid=aid))
        taken = failure(conn, ALLOCATE, allocation(second, op2, slot="pilot/one"), role=None)
        assert taken and taken[0] == "23505", f"a second deployment took the slot: {taken}"
        twice = failure(conn, ALLOCATE, allocation(deployment, op), role=None)
        assert twice and twice[0] == "23505", f"one deployment holds two slots: {twice}"
        conn.execute("update infrx.hosting_allocations set state = 'released', released_at = "
                     "infrx.now() where allocation_id = %s", (aid,))
        assert failure(conn, ALLOCATE, allocation(second, op2, slot="pilot/one"),
                       role=None) is None
    return "23505 for a held slot or a second live allocation; free after release"


def check_an_allocation_moves_forward_only(conn) -> str:
    op, deployment, _ = leased(conn, "moves-1")
    aid = str(uuid.uuid4())
    with conn.transaction(force_rollback=True):
        conn.execute(ALLOCATE, allocation(deployment, op, aid=aid))
        conn.execute("update infrx.hosting_allocations set state = 'released', released_at = "
                     "infrx.now() where allocation_id = %s", (aid,))
        for edit in ("update infrx.hosting_allocations set state = 'launched', launched_at = "
                     "infrx.now(), released_at = null",
                     "update infrx.hosting_allocations set slot = 'pilot/elsewhere'",
                     "delete from infrx.hosting_allocations"):
            state = failure(conn, edit, role=None)
            assert state and state[0] == "23514", f"{edit}: {state}"
    with conn.transaction(force_rollback=True):
        conn.execute(ALLOCATE, allocation(deployment, op, aid=aid))
        state = failure(conn, "update infrx.hosting_allocations set state = 'launched'", role=None)
        assert state and state[0] == "23514", f"launched with no instant: {state}"
    return "released is final; identity fixed; never deleted; launched has its instant"


def check_a_receipt_is_immutable_and_passes_iff_no_reason(conn) -> str:
    op, deployment, _ = leased(conn, "receipt-1")
    other_op, other, _ = leased(conn, "receipt-2")
    aid = str(uuid.uuid4())
    reason = json.dumps([{"field": "x", "code": "identity_mismatch", "message": "m"}])
    with conn.transaction(force_rollback=True):
        conn.execute(ALLOCATE, allocation(deployment, op, aid=aid))
        conn.execute(RECEIPT, (deployment, aid, "identity", True, "[]", op))
        for edit in ("update infrx.hosting_receipts set observed = '{\"forged\": true}'",
                     "delete from infrx.hosting_receipts"):
            state = failure(conn, edit, role=None)
            assert state and state[0] == "23514", f"{edit}: {state}"
        for bad, params in (("passed with a reason", (deployment, aid, "smoke", True, reason, op)),
                            ("failed without one", (deployment, aid, "smoke", False, "[]", op)),
                            ("a smoke outside an operation",
                             (deployment, aid, "smoke", True, "[]", None))):
            state = failure(conn, RECEIPT, params, role=None)
            assert state and state[0] == "23514", f"{bad}: {state}"
        assert failure(conn, RECEIPT, (deployment, aid, "health", True, "[]", None),
                       role=None) is None
        foreign = failure(conn, RECEIPT, (other, aid, "identity", True, "[]", other_op),
                          role=None)
        assert foreign and foreign[0] == "23503", f"another deployment's allocation: {foreign}"
    return "immutable; pass iff no reason; only health outside an operation; own allocation"


def check_active_lists_only_unfinished_operations(conn) -> str:
    op, deployment, fence = leased(conn, "active-1")
    assert call(conn, "hosting_active", {"deployment_revision_id": deployment}) == [op]
    call(conn, "control_op_finish", {"operation_id": op, "fence": fence, "state": "succeeded"})
    assert call(conn, "hosting_active", {"deployment_revision_id": deployment}) == []
    return "unfinished operations of the deployment only"


CHECKS = {c.__name__: c for c in (
    check_the_control_login_works_through_its_functions_and_policies,
    check_browser_roles_reach_nothing, check_a_request_is_one_transaction_and_replays,
    check_a_write_needs_the_live_lease_of_an_operation_on_its_deployment,
    check_a_slot_and_a_deployment_hold_one_allocation, check_an_allocation_moves_forward_only,
    check_a_receipt_is_immutable_and_passes_iff_no_reason,
    check_active_lists_only_unfinished_operations)}
LOGIN_OK, BROWSER, REQUEST, FENCE, CAPACITY, MOVES, RECEIPTS, ACTIVE = CHECKS


def seed(conn) -> None:
    """seed_admission's registry (provider A = NemoStation with its Marlin serving version,
    the clock frozen), a dev endpoint of A, and the login usable on either image."""
    if pgharness.ON_SUPABASE:           # `postgres` is no superuser there: `set role` needs it
        pgharness._sb(conn.info.dbname, f"grant {LOGIN} to postgres with set true")
    ca.seed_admission(conn)
    CTX["serving"] = str(conn.execute("select serving_version_id from infrx.serving_versions "
                                      "where provider_org_id = %s order by created_at limit 1",
                                      (A,)).fetchone()[0])
    CTX["endpoint"] = call(conn, "lab_control_endpoint", {
        "provider_org_id": A, "name": "candidate", "environment": "dev", "actor": "t"},
        role=None)["endpoint_id"]


# ----------------------------------------------------------------------------- mutants
def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    _s("ap05_no_login_policy", "    execute format('create policy api_hosting_control on "
       "infrx.%I to infrx_lab_control '\n                   'using (true) with check (true)', t);\n",
       "", LOGIN_OK, "every deployment route and the controller answer 503 (RLS refuses)"),
    _s("ap05_no_function_execute", "grant execute on function infrx.hosting_request(jsonb), "
       "infrx.hosting_fence(jsonb),\n  infrx.hosting_active(jsonb) to service_role, "
       "infrx_lab_control;", "", LOGIN_OK, "no deployment can be requested or worked"),
    _s("ap05_allocations_not_updatable", "grant update (state, launched_at, released_at) on "
       "infrx.hosting_allocations to infrx_lab_control;", "", LOGIN_OK,
       "no allocation is ever launched or released: the slot is held for ever"),
    _s("ap05_tables_to_authenticated", "grant select on infrx.hosting_deployments to "
       "infrx_lab_control;", "grant select on infrx.hosting_deployments to infrx_lab_control, "
       "authenticated;", BROWSER, "a browser session reads every provider's deployments"),
    _s("ap05_request_to_authenticated", "  infrx.hosting_active(jsonb) to service_role, "
       "infrx_lab_control;", "  infrx.hosting_active(jsonb) to service_role, infrx_lab_control, "
       "authenticated;", BROWSER, "a browser session creates deployments past the API"),
    _s("ap05_allocations_fully_updatable", "grant update (state, launched_at, released_at) on "
       "infrx.hosting_allocations to infrx_lab_control;", "grant update on "
       "infrx.hosting_allocations to infrx_lab_control;", BROWSER,
       "the route login re-points an allocation at another slot or port"),
    _s("ap05_replay_writes_again", "  if (v_started->>'replayed')::boolean then\n",
       "  if false then\n", REQUEST,
       "a retried request leaves a second draft and hosting row behind its first operation"),
    _s("ap05_refusal_swallowed", "  exception when check_violation or not_null_violation or "
       "foreign_key_violation then\n    perform infrx.refuse(",
       "  exception when check_violation or not_null_violation or foreign_key_violation then\n"
       "    return v_started;\n    perform infrx.refuse(", REQUEST,
       "a refused request leaves an operation with no deployment behind it"),
    _s("ap05_fence_ignored", "  if r.fence is distinct from (p_args->>'fence')::bigint or "
       "r.lease_until is null\n", "  if r.lease_until is null\n", FENCE,
       "a controller that lost its lease keeps writing beside its successor"),
    _s("ap05_expired_lease_writes", "     or r.lease_until <= infrx.now() then",
       "     then", FENCE, "a stalled controller writes after its lease expired"),
    _s("ap05_unleased_writes", "  if r.fence is distinct from (p_args->>'fence')::bigint or "
       "r.lease_until is null\n", "  if r.fence is distinct from (p_args->>'fence')::bigint\n",
       FENCE, "a finished (unleased) operation's last holder writes again"),
    _s("ap05_any_deployment", "  if r.resource_kind is distinct from 'deployment'\n"
       "     or r.resource_id is distinct from p_args->>'deployment_revision_id' then",
       "  if r.resource_kind is distinct from 'deployment' then", FENCE,
       "one deployment's lease writes another deployment's allocation"),
    _s("ap05_slot_shared", "create unique index if not exists hosting_allocations_slot_held\n"
       "  on infrx.hosting_allocations (slot) where state <> 'released';\n", "", CAPACITY,
       "two candidates share the one GPU slot (an eviction or an OOM)"),
    _s("ap05_two_live_allocations", "create unique index if not exists "
       "hosting_allocations_one_live\n  on infrx.hosting_allocations (deployment_revision_id) "
       "where state <> 'released';\n", "", CAPACITY,
       "a resumed controller reserves a second slot for the same deployment"),
    _s("ap05_released_relaunched", "        or (old.state = 'launched' and new.state = "
       "'released'))) then", "        or (old.state in ('launched', 'released'))\n"
       "        or (old.state = 'released'))) then", MOVES,
       "a released slot is launched again under its old tag"),
    _s("ap05_allocation_rewritable", "  if (to_jsonb(new) - 'state' - 'launched_at' - "
       "'released_at')\n       is distinct from (to_jsonb(old) - 'state' - 'launched_at' - "
       "'released_at')\n     or", "  if", MOVES, "an allocation's slot or tag moves under it"),
    _s("ap05_launched_without_instant", "  constraint hosting_allocations_launched_at check "
       "(state <> 'launched' or launched_at is not null),\n", "", MOVES,
       "the launch timeout counts from nothing"),
    _s("ap05_receipts_mutable", "create or replace trigger hosting_receipts_immutable before "
       "update or delete", "create or replace trigger hosting_receipts_immutable before delete",
       RECEIPTS, "a failed check is rewritten into a pass"),
    _s("ap05_pass_with_reasons", "  constraint hosting_receipts_pass_iff_no_reason check "
       "(passed = (jsonb_array_length(reasons) = 0)),\n", "", RECEIPTS,
       "a receipt reads passed while naming a mismatch"),
    _s("ap05_smoke_outside_operation", "  constraint hosting_receipts_decided_in_an_operation "
       "check (kind = 'health' or operation_id is not null),\n", "", RECEIPTS,
       "an identity or smoke receipt no fenced operation recorded promotes a revision"),
    _s("ap05_receipt_any_allocation", "  constraint hosting_receipts_allocation_fk foreign key "
       "(allocation_id, deployment_revision_id)\n    references infrx.hosting_allocations "
       "(allocation_id, deployment_revision_id) on delete restrict,\n", "", RECEIPTS,
       "a deployment is ready on another deployment's engine checks"),
    _s("ap05_active_includes_finished", "     and state in ('queued', 'running', "
       "'cancel_requested');\n$$;", "     ;\n$$;", ACTIVE,
       "a finished operation blocks every later retire and smoke"),
)


def kill(mutant) -> tuple[str, str]:
    import sys
    return d7.kill(mutant, DB_MUT, sys.modules[__name__])


def rollback_sql() -> str:
    """The `-- rollback: ` lines of the file's header, the one source of its rollback."""
    lines = [line.removeprefix("-- rollback: ") for line in
             (migrations.DIR / FILE).read_text().splitlines() if line.startswith("-- rollback: ")]
    assert len(lines) == 2, lines
    return "\n".join(lines)


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
def test_0062_checks(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
def test_0062_upgrade_preserves_history_reruns_and_rolls_back() -> None:
    """0062 over a database holding consumer history (every earlier file applied): only its
    three tables (empty) and four functions appear; no row count, money sum, job, column,
    relation grant or existing function grant moves; a second application changes nothing;
    its own ROLLBACK lines restore exactly the earlier state, and it rolls forward again."""
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
        assert set(after["fns"]) - set(before["fns"]) == NEW_FUNCTIONS
        pgharness.apply(DB_UP, mine)
        assert d10.snapshot(conn) == after, "0062 is not re-runnable"
        pgharness.apply(DB_UP, (("rollback", rollback_sql()),))
        assert d10.snapshot(conn) == before, "the ROLLBACK lines do not restore the earlier state"
        pgharness.apply(DB_UP, mine)
        assert d10.snapshot(conn) == after, "rolling forward again is not the same 0062"


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
