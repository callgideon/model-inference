#!/usr/bin/env python3
"""R32/R40 for api-schema (AP-00 00d): single-edit defects of `0060_control_operations.sql`
(and of the `control_op_cancel` that `0066_wave7_grants_and_reads.sql` re-creates), each killed by the named check of `test_control_ops.py` on a database built from the mutated
set (needs Docker; skips visibly without it; D7's runner), and of `infrx/state/control_ops.py`,
each killed by the named case of `test_control_ops_units.py` through the shared runner (no
Docker).

    INFRX_MUTANTS=all INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_control_ops_mutants.py
"""
from __future__ import annotations

import pytest

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_control_ops as t
from . import test_control_ops_units as units

FILE = "0060_control_operations.sql"
DB = f"{pgharness.DATABASE}_control_ops_mut"

S1 = "check_start_replays_the_same_request_and_refuses_another"
S2 = "check_start_refuses_what_it_cannot_store"
EXP = "check_an_expired_key_is_refused_never_restarted"
LEASE = "check_a_lease_is_fenced_and_expires"
FIN = "check_finish_records_one_terminal_state"
CANCEL = "check_cancel_from_each_state"
READS = "check_reads_belong_to_the_owning_tenant_or_an_operator"
PEND = "check_pending_is_the_unleased_unfinished_work_of_its_kinds"
ROLES = "check_browser_roles_reach_nothing"
RACE = "check_two_instances_race_one_key_to_one_operation"
AUD = "check_the_sql_refuses_an_actor_python_never_sends"

ADV_FENCE = ("  if r.fence is distinct from (p_args->>'fence')::bigint or r.lease_until is null\n"
             "     or r.lease_until <= v_now then\n"
             "    perform infrx.refuse('state_conflict', 'stale fence: the lease was lost');")
FIN_FENCE = ADV_FENCE.replace("lost');", "lost before finishing');")
CANCEL_SEEN = ("  if not (coalesce((p_args->'actor'->>'operator')::boolean, false)\n"
               "          or infrx.control_owner(r.actor) = infrx.control_owner(p_args->'actor'))")
GET_SEEN = ("     or not (coalesce((p_args->'actor'->>'operator')::boolean, false)\n"
            "             or infrx.control_owner(r.actor) = infrx.control_owner(p_args->'actor'))")
GRANT = "  to service_role, infrx_lab_control;"
TS = """'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')"""


def _s66(name, old, new, check, why, **kw):
    return _d.Mutant(name, "0066_wave7_grants_and_reads.sql", old, new, "lab", check, why, **kw)


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    # --- what a start may store
    _s("cto_kind_one_segment", r"check (kind ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')",
       r"check (kind ~ '^[a-z][a-z0-9_.]*$')", S2,
       "an undotted kind is stored: no family can route its operations"),
    _s("cto_audience_unchecked",
       "    check (actor->>'audience' in ('consumer', 'operator', 'provider_dev', 'session')),",
       "    check (true),", AUD, "an operation whose actor no reader can classify is stored"),
    _s("cto_key_unbounded", "check (length(key) between 1 and 255)", "check (length(key) >= 1)",
       S2, "a client stores an unbounded Idempotency-Key"),
    _s("cto_hash_unchecked", "  input_hash text not null check (input_hash ~ "
       "'^sha256:[0-9a-f]{64}$'),", "  input_hash text not null,", S2,
       "a replay is decided on a value that is not a canonical hash"),
    _s("cto_retention_short", "check (expires_at >= created_at + interval '24 hours')",
       "check (true)", S2, "a receipt expires before contracts.md's 24 h"),
    _s("cto_starts_running", "  state text not null default 'queued'",
       "  state text not null default 'running'", S1, "a new operation claims work started"),
    _s("cto_unstorable_raw", "  exception when check_violation or not_null_violation then\n"
       "    perform infrx.refuse('invalid_request', 'not a startable",
       "  exception when check_violation then\n    perform infrx.refuse('invalid_request', "
       "'not a startable", S2, "a tenant-less actor's start is a 500, not a 422"),
    # --- the idempotency scope and replay
    _s("cto_scope_without_kind", "  v_scope text := v_owner || '/' || (p_args->>'kind');",
       "  v_scope text := v_owner;", S1,
       "one key replays another action's operation"),
    _s("cto_scope_without_tenant", "  v_scope text := v_owner || '/' || (p_args->>'kind');",
       "  v_scope text := (p_args->>'kind');", S1,
       "a key replays (and discloses) another tenant's operation"),
    _s("cto_owner_ignores_workspace",
       "  select case when p_actor->>'provider_org_id' is not null then 'provider:' || "
       "(p_actor->>'provider_org_id')\n              when", "  select case when", READS,
       "the same user reads a workspace's operations from another workspace"),
    _s("cto_owner_no_user", "              when p_actor->>'user_id' is not null then 'user:' || "
       "(p_actor->>'user_id') end;", "              end;", READS,
       "a user-only actor (a personal key) cannot start an operation"),
    _s("cto_expired_key_replays", "  if i.expires_at <= v_now then", "  if false then", EXP,
       "an expired key silently replays instead of the explicit 410"),
    _s("cto_conflict_replays", "  if i.input_hash <> p_args->>'input_hash' then",
       "  if false then", S1, "a different request under one key answers the first operation"),
    _s("cto_replay_new_outcome", "                            'outcome', i.outcome);",
       "                            'outcome', p_args->'outcome');", S1,
       "a replay answers what the retry sent, not what the first start stored"),
    _s("cto_race_unique_violation", "    on conflict (scope, key) do nothing;", "    ;", RACE,
       "two API instances racing one key: one gets a 500"),
    # --- the lease and its fence
    _s("cto_lease_terminal", "  if r.state in ('succeeded', 'failed', 'cancelled') then\n"
       "    perform infrx.refuse('state_conflict', 'the operation is finished');",
       "  if false then\n    perform infrx.refuse('state_conflict', 'the operation is finished');",
       LEASE, "a finished operation is worked again"),
    _s("cto_lease_ttl_unbounded", "     or v_ttl not between 1 and 3600 then",
       "     or v_ttl < 1 then", LEASE, "a worker holds an operation for days"),
    _s("cto_lease_owner_unbounded", "length(v_owner) not between 1 and 128",
       "length(v_owner) < 1", LEASE, "an unbounded lease owner is stored"),
    _s("cto_renewal_new_fence", "    update infrx.control_operations set lease_until = v_now + "
       "make_interval(secs => v_ttl),\n           updated_at = v_now",
       "    update infrx.control_operations set lease_until = v_now + "
       "make_interval(secs => v_ttl),\n           updated_at = v_now, fence = fence + 1", LEASE,
       "a heartbeat invalidates the holder's own fence"),
    _s("cto_lease_stolen", "  elsif r.lease_until > v_now then", "  elsif false then", LEASE,
       "a second worker takes a live lease: two controllers act on one operation"),
    _s("cto_fence_not_bumped", "set lease_owner = v_owner, fence = fence + 1,",
       "set lease_owner = v_owner,", LEASE,
       "a lost worker's fence still writes after another took over"),
    _s("cto_lease_not_running", "           state = case when state = 'queued' then 'running' "
       "else state end", "           state = state", LEASE,
       "a leased operation still reads queued"),
    _s("cto_lease_clears_cancel", "           state = case when state = 'queued' then 'running' "
       "else state end", "           state = 'running'", CANCEL,
       "a new holder no longer sees the cancellation it must reconcile"),
    _s("cto_advance_any_fence", ADV_FENCE, ADV_FENCE.replace(
        "r.fence is distinct from (p_args->>'fence')::bigint or ", ""), LEASE,
       "a stale worker's progress overwrites the holder's"),
    _s("cto_advance_expired_lease", ADV_FENCE,
       ADV_FENCE.replace("\n     or r.lease_until <= v_now", ""), LEASE,
       "a worker whose lease expired keeps writing"),
    _s("cto_advance_finished", ADV_FENCE, ADV_FENCE.replace(" or r.lease_until is null", ""),
       LEASE, "a finished operation's phase is rewritten"),
    _s("cto_advance_sets_running", "set phase = p_args->>'phase', updated_at = v_now,",
       "set phase = p_args->>'phase', state = 'running', updated_at = v_now,", CANCEL,
       "a worker's progress clears a cancellation"),
    _s("cto_retry_negative", "  retry_after_s int check (retry_after_s >= 0),",
       "  retry_after_s int,", LEASE, "a negative Retry-After reaches a client"),
    _s("cto_advance_raw_violation", "  exception when check_violation then\n"
       "    perform infrx.refuse('invalid_request', 'not a phase or retry interval');",
       "  exception when division_by_zero then\n"
       "    perform infrx.refuse('invalid_request', 'not a phase or retry interval');", LEASE,
       "a bad retry interval is a 500"),
    # --- finishing
    _s("cto_finish_any_state", "  if coalesce(p_args->>'state', '') not in ('succeeded', "
       "'failed', 'cancelled') then", "  if false then", FIN,
       "a worker 'finishes' an operation as running"),
    _s("cto_finish_any_fence", FIN_FENCE, FIN_FENCE.replace(
        "r.fence is distinct from (p_args->>'fence')::bigint or ", ""), LEASE,
       "a stale worker's result overwrites the holder's"),
    _s("cto_finish_expired_lease", FIN_FENCE,
       FIN_FENCE.replace("\n     or r.lease_until <= v_now", ""), LEASE,
       "a worker whose lease expired settles the operation"),
    _s("cto_finish_twice", FIN_FENCE, FIN_FENCE.replace(" or r.lease_until is null", ""),
       LEASE, "a finished operation is finished again with another result"),
    _s("cto_finish_keeps_lease", "           lease_owner = null, lease_until = null, "
       "updated_at = v_now", "           updated_at = v_now", LEASE,
       "a finished operation still shows a holder"),
    _s("cto_error_without_failure",
       "check ((state = 'failed') = (error is not null))",
       "check (error is null or state = 'failed')", FIN,
       "a failed operation carries no error to show"),
    _s("cto_json_null_error", "error = nullif(p_args->'error', 'null'),",
       "error = p_args->'error',", LEASE, "every success is refused (JSON null stored as error)"),
    _s("cto_finish_raw_violation", "  exception when check_violation then\n"
       "    perform infrx.refuse('invalid_request', 'a failed operation",
       "  exception when division_by_zero then\n"
       "    perform infrx.refuse('invalid_request', 'a failed operation", FIN,
       "a failure without an error is a 500"),
    # --- cancellation (0066 re-creates control_op_cancel: a finished operation answers as is)
    _s66("cto_cancel_any_tenant", CANCEL_SEEN,
         CANCEL_SEEN.replace("  if not (coalesce(", "  if false and (coalesce("), READS,
         "a stranger cancels another tenant's deployment"),
    _s66("cto_cancel_operator_refused", CANCEL_SEEN,
         CANCEL_SEEN.replace("(coalesce((p_args->'actor'->>'operator')::boolean, false)",
                             "(false"), READS, "an operator cannot stop a tenant's operation"),
    _s66("cto_cancel_finished", "  if r.state in ('queued', 'running') then",
         "  if r.state <> 'cancelled' then", CANCEL,
         "a cancel that lost the race to the finish reopens a succeeded operation"),
    _s66("cto_cancel_queued_waits", "       set state = case when state = 'queued' then "
         "'cancelled' else 'cancel_requested' end,", "       set state = 'cancel_requested',",
         CANCEL, "a never-started operation waits for a worker to cancel it"),
    _s66("cto_cancel_restamps", "  if r.state in ('queued', 'running') then",
         "  if r.state in ('queued', 'running', 'cancel_requested') then", CANCEL,
         "a repeated cancel moves the cancellation instant"),
    # --- reads
    _s("cto_get_any_tenant", GET_SEEN, "     or false", READS,
       "any authenticated caller reads any tenant's operation"),
    _s("cto_get_operator_refused", GET_SEEN,
       GET_SEEN.replace("(coalesce((p_args->'actor'->>'operator')::boolean, false)", "(false"),
       READS, "the operator console cannot read a tenant's operation"),
    _s("cto_get_bad_id_500", "  exception when invalid_text_representation then\n"
       "    r := null;\n  end;\n  if r.operation_id is null\n",
       "  end;\n  if r.operation_id is null\n", READS, "a non-uuid path id is a 500, not a 404"),
    _s("cto_row_bad_id_500", "  exception when invalid_text_representation then\n"
       "    r := null;\n  end;\n  if r.operation_id is null then\n",
       "  end;\n  if r.operation_id is null then\n", READS,
       "a non-uuid id on cancel/lease is a 500, not a 404"),
    _s("cto_doc_local_time", f"'created_at', to_char(r.created_at at time zone 'UTC', {TS},",
       "'created_at', r.created_at,", S1, "the wire instant depends on the session time zone"),
    _s("cto_doc_no_fence", "'error', r.error, 'fence', r.fence,", "'error', r.error,", LEASE,
       "a worker never learns its fence"),
    # --- discovery
    _s("cto_pending_any_kind",
       "             and kind in (select jsonb_array_elements_text(p_args->'kinds'))\n", "",
       PEND, "a controller claims another family's operations"),
    _s("cto_pending_leased", "             and (lease_until is null or lease_until <= "
       "infrx.now())\n", "", PEND, "a live lease's operation is handed to a second worker"),
    _s("cto_pending_finished", "           where state in ('queued', 'running', "
       "'cancel_requested')\n             and kind", "           where state <> 'succeeded'\n"
       "             and kind", PEND, "a cancelled operation is worked again"),
    _s("cto_pending_uncapped", "limit least(greatest((p_args->>'limit')::int, 1), 100)) o;",
       "limit greatest((p_args->>'limit')::int, 1)) o;", PEND,
       "one discovery call returns an unbounded list"),
    _s("cto_pending_zero", "limit least(greatest((p_args->>'limit')::int, 1), 100)) o;",
       "limit least((p_args->>'limit')::int, 100)) o;", PEND,
       "limit 0 starves the controller"),
    # --- privileges (R271)
    _s("cto_browser_executes", GRANT,
       "  to service_role, infrx_lab_control, authenticated;", ROLES,
       "a browser session starts and reads operations around FastAPI"),
    _s("cto_runtime_granted", GRANT, "  to service_role, infrx_lab_control, infrx_runtime;",
       ROLES, "the gateway's pinned dedicated login silently gains seven functions"),
    _s("cto_control_unit_cannot_run", GRANT, "  to service_role;", ROLES,
       "the Lab control unit answers 503 for every operation"),
    _s("cto_helpers_callable", "infrx.control_op_row(jsonb) from public, anon, authenticated, "
       "service_role;", "infrx.control_op_row(jsonb) from public, anon, authenticated;", ROLES,
       "the platform role locks rows outside the boundary functions"),
    _s("cto_rls_off", "execute format('alter table infrx.%I enable row level security', t);",
       "execute format('alter table infrx.%I disable row level security', t);", ROLES,
       "a future browser grant exposes every tenant's operations"),
    _s("cto_service_writes", "execute format('grant select on infrx.%I to service_role', t);",
       "execute format('grant select, insert on infrx.%I to service_role', t);", ROLES,
       "the platform role writes operations around the fence"),
)

RUNNER = Runner(name="control_ops", targets=("tests/d/test_control_ops_units.py",))
F = "state/control_ops.py"
U = "test_fake__start_replays_the_same_request_and_refuses_another"
STORE = "test_fake__start_refuses_what_it_cannot_store"
EXPIRY = "test_fake__an_expired_key_is_refused_never_restarted"
FENCE = "test_fake__a_lease_is_fenced_and_expires"
FINISH = "test_fake__finish_records_one_terminal_state"
CANCELS = "test_fake__cancel_from_each_state"
READ = "test_fake__reads_belong_to_the_owning_tenant_or_an_operator"
PENDING = "test_fake__pending_is_the_unleased_unfinished_work_of_its_kinds"
HASHED = "test_input_hash__is_the_sha256_of_the_canonical_body"
SENT = "test_pg__each_method_sends_its_function_the_callers_arguments"
TYPED = "test_pg__a_refusal_is_its_typed_error_and_an_outage_is_503"


def _p(name, invariant, old, new, *cases, **kw) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases, **kw)


CODE_MUTANTS = (
    _p("cto_py_hash_not_canonical", "the input hash is the canonical body's",
       "hashlib.sha256(codec.compact_bytes(body))", "hashlib.sha256(str(body).encode())", HASHED),
    _p("cto_py_pg_wrong_function", "each method calls its 0060 function",
       'f"control_op_{name}"', 'f"control_{name}"', SENT),
    _p("cto_py_pg_untyped", "a SQL refusal is its typed error", "error=domain_error,",
       "error=None,", TYPED),
    _p("cto_py_pg_outage_raw", "an outage is a retryable 503",
       'unreachable="the control operation store is unreachable")', "unreachable=None)", TYPED),
    _p("cto_py_pg_retention_dropped", "start sends its retention",
       '"retention_s": retention_s}))', "}))", SENT),
    _p("cto_py_pg_reads_unscoped", "get and cancel send the reader",
       '"operation_id": operation_id, "actor": actor.model_dump(mode="json")}))',
       '"operation_id": operation_id}))', SENT, occurrences=2),
    _p("cto_py_pg_error_dropped", "finish sends its error",
       '"error": error.model_dump(mode="json") if error else None}))', '"error": None}))', SENT),
    _p("cto_py_pg_limit_dropped", "pending sends its limit",
       '{"kinds": list(kinds), "limit": limit}', '{"kinds": list(kinds)}', SENT),
    _p("cto_py_pg_ttl_dropped", "lease sends its ttl",
       '"owner": owner, "ttl_s": ttl_s}))', '"owner": owner}))', SENT),
    _p("cto_py_pg_retry_dropped", "advance sends its retry interval",
       '"retry_after_s": retry_after_s}))', "}))", SENT),
    _p("cto_py_owner_ignores_workspace", "the workspace is the tenant first",
       "    if actor.provider_org_id is not None:\n", "    if False:\n", READ),
    _p("cto_py_owner_no_user", "a user-only actor is its own tenant",
       '    if actor.user_id is not None:\n        return f"user:{actor.user_id}"\n', "", READ),
    _p("cto_py_tenantless_start", "an actor without a tenant cannot start",
       "        if owner is None:\n", "        if False:\n", STORE),
    _p("cto_py_kind_unchecked", "a kind is dotted lower case", "not KIND.fullmatch(kind)",
       "not kind", STORE),
    _p("cto_py_hash_unchecked", "the stored hash is a sha256", "not HASH.fullmatch(input_hash)",
       "not input_hash", STORE),
    _p("cto_py_key_unbounded", "a key is 1-255 characters",
       "not 1 <= len(idempotency_key) <= 255", "not idempotency_key", STORE),
    _p("cto_py_retention_short", "a receipt answers at least 24 h",
       "retention_s < RETENTION_S", "retention_s < 0", STORE),
    _p("cto_py_scope_without_kind", "the key's scope is tenant + kind",
       'scope = (f"{owner}/{kind}", idempotency_key)', "scope = (owner, idempotency_key)", U),
    _p("cto_py_expired_replays", "an expired key is 410",
       "            if key.expires_at <= now:\n", "            if False:\n", EXPIRY),
    _p("cto_py_conflict_replays", "another body under one key is 409",
       "            if key.input_hash != input_hash:\n", "            if False:\n", U),
    _p("cto_py_replay_new_outcome", "a replay answers the stored outcome",
       "                           outcome=key.outcome)", "                           outcome=outcome)",
       U),
    _p("cto_py_lease_owner_unbounded", "a lease owner is 1-128 characters",
       "not 1 <= len(owner) <= 128", "not owner", FENCE),
    _p("cto_py_lease_ttl_unbounded", "a ttl is 1-3600 s", "not 1 <= ttl_s <= 3600",
       "ttl_s < 1", FENCE),
    _p("cto_py_lease_terminal", "a finished operation is not leased",
       '        if row.op.state in TERMINAL:\n            raise errors.Conflict("the operation is '
       'finished")', '        if False:\n            raise errors.Conflict("the operation is '
       'finished")', FENCE),
    _p("cto_py_renewal_new_fence", "a renewal keeps the fence",
       "            return self._put(row, lease_until=until)\n",
       "            return self._put(row, lease_until=until, fence=row.op.fence + 1)\n", FENCE),
    _p("cto_py_lease_stolen", "a live lease is its holder's",
       "        if live:\n            raise", "        if False:\n            raise", FENCE),
    _p("cto_py_fence_not_bumped", "a grant bumps the fence",
       "lease_until=until, fence=row.op.fence + 1,", "lease_until=until,", FENCE),
    _p("cto_py_lease_clears_cancel", "a lease keeps a cancellation",
       'state="running" if row.op.state == "queued" else row.op.state', 'state="running"',
       CANCELS),
    _p("cto_py_any_fence", "only the live fence writes",
       "        if row.op.fence != fence or row.lease_until", "        if row.lease_until", FENCE),
    _p("cto_py_expired_fence", "an expired lease writes nothing",
       " or row.lease_until <= self.now():", ":", FENCE),
    _p("cto_py_retry_negative", "a retry interval is not negative",
       "        if retry_after_s is not None and retry_after_s < 0:\n",
       "        if False:\n", FENCE),
    _p("cto_py_finish_any_state", "an operation finishes in a terminal state",
       "        if state not in FINISHED:\n", "        if False:\n", FINISH),
    _p("cto_py_error_without_failure", "error iff failed",
       '        if (state == "failed") != (error is not None):\n',
       '        if state == "failed" and error is None:\n', FINISH),
    _p("cto_py_finish_keeps_lease", "finishing clears the lease",
       "state=state, error=error, lease_owner=None, lease_until=None)",
       "state=state, error=error)", FENCE),
    _p("cto_py_cancel_queued_waits", "a queued operation cancels at once",
       'state="cancelled" if row.op.state == "queued" else "cancel_requested"',
       'state="cancel_requested"', CANCELS),
    _p("cto_py_cancel_reopens", "a settled cancellation answers as it is",
       '        if row.op.state in ("queued", "running"):\n', "        if True:\n", CANCELS),
    _p("cto_py_operator_refused", "an operator reads every tenant",
       "if not (actor.operator or owner_of(row.op.actor) == owner_of(actor)):",
       "if not owner_of(row.op.actor) == owner_of(actor):", READ),
    _p("cto_py_any_tenant", "a stranger reads nothing",
       "if not (actor.operator or owner_of(row.op.actor) == owner_of(actor)):",
       "if not (actor.operator or True):", READ),
    _p("cto_py_pending_leased", "a live lease is not pending",
       "\n                      and (row.lease_until is None or row.lease_until <= now))", ")",
       PENDING),
    _p("cto_py_pending_finished", "finished work is not pending",
       "and row.op.state not in TERMINAL", "and True", PENDING),
    _p("cto_py_pending_any_kind", "pending is per kind", "if row.op.kind in kinds and",
       "if", PENDING),
    _p("cto_py_pending_unbounded", "pending is clamped to 1-100",
       "free[:min(max(limit, 1), 100)]", "free[:limit]", PENDING),
)
_reason = pgharness.unavailable()


def test_the_lists_are_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS] + [m.name for m in CODE_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    for m in CODE_MUTANTS:
        source = (shared.API_DIR / "infrx" / m.file).read_text()
        assert source.count(m.old) == m.occurrences, f"{m.name}: {source.count(m.old)}"
    assert all(m.check in t.CHECKS for m in SQL_MUTANTS)
    print(f"control ops mutants: {len(SQL_MUTANTS)} SQL, {len(CODE_MUTANTS)} Python")


def test_no_mutant_anchors_in_a_superseded_function_body() -> None:
    found = _d.superseded(SQL_MUTANTS)
    assert not found, found


def test_every_case_is_covered_by_a_mutant() -> None:
    uncovered = sorted(set(t.CHECKS) - {m.check for m in SQL_MUTANTS})
    cases = {name for name in dir(units) if name.startswith("test_")}
    uncovered += sorted(cases - {c for m in CODE_MUTANTS for c in m.cases})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL_MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = d7.kill(mutant, DB, t)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")


@pytest.mark.parametrize("mutant", CODE_MUTANTS, ids=lambda m: m.name)
def test_code_mutant_is_killed(mutant) -> None:
    result = shared.run_mutant(mutant, RUNNER)
    assert result.killed, f"{mutant.name}: {result.outcome} - {result.detail}"
    print(f"{mutant.name}: killed -> {result.detail}")
