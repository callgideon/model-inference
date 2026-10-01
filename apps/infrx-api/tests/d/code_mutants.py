#!/usr/bin/env python3
"""R32/R40 for D2's Python half (`infrx/state/jobstore.py`, `outbox.py`, `pgtesting.py`) and
W6 A5's one-connection statement helper (`rpc.py`) with each adapter's wiring of it.

Delegates to the one runner (`tests/contracts/mutants.py`, R83): each mutant is one edit
to a throwaway copy of the package, the named cases in `tests/d/test_adapter_units.py`
run there (no Docker: the adapter's own decisions, not the SQL's), and only `killed`
counts. The SQL invariants have their own list (`migration_mutants.py`).

    uv run --frozen pytest -q tests/d/test_code_mutants.py
    uv run --frozen python -m tests.d.code_mutants --list
"""
from __future__ import annotations

import sys

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner

J, OUTBOX, T = "state/jobstore.py", "state/outbox.py", "state/pgtesting.py"
RPC, OPS, LC = "state/rpc.py", "state/operations.py", "state/lifecycle.py"   # W6 A5
RUNNER = Runner(name="d2", targets=("tests/d/test_adapter_units.py",))


def _m(name, invariant, file, old, new, *cases, dies_by=(), occurrences=1) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by), occurrences=occurrences)


MAPPING = "test_domain_error__each_refusal_is_its_most_specific_type"
RPC_CALL = "test_rpc__a_call_is_one_named_function_on_its_own_closed_connection"
RPC_REFUSAL = "test_rpc__a_refusal_is_typed_and_the_connection_still_closed"
RPC_OUTAGE = "test_rpc__unreachable_is_a_retryable_503_connecting_or_executing"
RPC_ROWS = "test_rpc__rows_are_every_row_or_the_count_of_a_statement_without_rows"
RPC_ADAPTERS = "test_rpc__each_adapter_keeps_its_own_refusal"
MUTANTS: tuple[Mutant, ...] = (
    _m("retry_after_is_dropped", "a 429 carries the store's Retry-After", J,
       '                return cls(detail, retry_after_s=int(hint.split("=", 1)[1]))',
       "                return cls(detail)", MAPPING),
    _m("a_code_maps_to_its_base_class", "a refusal is its most specific type", J,
       '        if "code" in cls.__dict__ and depth >= found.get(cls.code, (None, -1))[1]:',
       '        if "code" in cls.__dict__ and cls.code not in found:', MAPPING),
    _m("insufficient_credit_is_unmapped", "the named reservation constraint is a 402", J,
       '            "credit_wallets_reserved_within_total":',
       '            "credit_wallets_reserved_within":', MAPPING),
    _m("maintenance_is_unmapped", "a disabled flag is a typed maintenance refusal", J,
       '    if state == "55000":', '    if state == "55001":', MAPPING),
    _m("an_unknown_error_is_typed", "an unknown database error stays the bug it is", J,
       "    return exc\n", "    return errors.InternalError(message)\n",
       "test_domain_error__anything_else_is_the_bug_it_is"),
    _m("a_committed_refusal_is_returned", "R39: a refusal after a committed terminalization "
       "still raises", J, "        if refusal:", "        if False:",
       "test_a_committed_refusal_is_raised_as_its_type"),
    _m("the_callers_limits_are_sent", "admission is checked against the store's limits", J,
       '"limits": _limits(self.limits),', '"limits": _limits(DEFAULTS),',
       "test_admit__sends_the_stores_own_limits_and_budgets_never_the_callers"),
    _m("the_budgets_are_the_defaults", "R4: the store's current budgets are snapshotted", J,
       '"budgets": Budgets.of(self.limits, request.execution_mode)',
       '"budgets": Budgets.of(DEFAULTS, request.execution_mode)',
       "test_admit__sends_the_stores_own_limits_and_budgets_never_the_callers"),
    _m("an_unknown_reservation_kind_is_accepted", "extra caps are reservation kinds", J,
       "            if kind not in tuple(ReservationKind):", "            if False:",
       "test_admit__sends_the_stores_own_limits_and_budgets_never_the_callers"),
    _m("credit_is_admitted_as_usd", "admit_credit names the CREDIT regime", J,
       'self._admit_args("credit", request, idem)',
       'self._admit_args("legacy_usd", request, idem)',
       "test_admit_credit__names_the_credit_regime"),
    _m("get_owned_reads_a_credit_job_as_usd", "a CREDIT job is never a v1 USD admission", J,
       '        if doc["accounting_regime"] != "legacy_usd":\n            raise errors.NotFound(',
       '        if False:\n            raise errors.NotFound(',
       "test_get_owned__a_credit_job_is_not_a_v1_admission"),
    _m("a_malformed_id_is_queried", "a malformed job id is not live and is not queried", J,
       "        if not ids.is_request_id(job_id):", "        if False:",
       "test_is_live__unknown_and_malformed_are_not_live"),
    _m("a_terminal_job_is_live", "only a non-terminal job is live", J,
       "        return bool(rows and rows[0][0])", "        return bool(rows)",
       "test_is_live__unknown_and_malformed_are_not_live"),
    _m("a_boolean_prompt_count_is_accepted", "the prompt count is an integer", J,
       "        if prompt_tokens is not None and (isinstance(prompt_tokens, bool)",
       "        if prompt_tokens is not None and (False",
       "test_prepared__a_prompt_count_must_be_an_integer"),
    _m("everything_read_is_acknowledged", "only what the index took is acknowledged", OUTBOX,
       "acknowledged = (await self.store.acknowledge_dispatch(taken, worker_id=self.worker_id)",
       "acknowledged = (await self.store.acknowledge_dispatch("
       "[e.event_id for e in events], worker_id=self.worker_id)",
       "test_relay__a_full_index_stops_and_hands_the_rest_back"),
    _m("a_full_index_is_a_failure", "a full index defers, it is not a row failure", OUTBOX,
       "            except errors.CapacityExhausted:", "            except errors.RateLimited:",
       "test_relay__a_full_index_stops_and_hands_the_rest_back"),
    _m("a_full_index_does_not_stop_the_pump", "OB-4: the pump stops at a full index", OUTBOX,
       "                deferred = [e.event_id for e in events[position:]]\n                break",
       "                deferred = [event.event_id]\n                continue",
       "test_relay__a_full_index_stops_and_hands_the_rest_back"),
    _m("deferred_rows_are_not_released", "OB-4: deferred rows are handed back now", OUTBOX,
       "        if deferred:\n            await self.store.release_dispatch(deferred)\n", "",
       "test_relay__a_full_index_stops_and_hands_the_rest_back"),
    _m("a_failing_row_is_acknowledged", "enqueue before acknowledge", OUTBOX,
       "                continue\n            taken.append(event.event_id)",
       "            taken.append(event.event_id)",
       "test_relay__a_failing_row_is_recorded_and_the_batch_goes_on"),
    _m("a_failing_row_is_not_recorded", "OB-7: the refused row is recorded", OUTBOX,
       "                await self.store.record_dispatch_error(event.event_id, "
       "repr(failed)[:500])\n", "",
       "test_relay__a_failing_row_is_recorded_and_the_batch_goes_on"),
    _m("a_failing_row_aborts_the_batch", "OB-7: one bad row does not strand the batch", OUTBOX,
       "                failures.append(failed)\n", "                raise\n",
       "test_relay__a_failing_row_is_recorded_and_the_batch_goes_on"),
    _m("a_failure_is_swallowed", "OB-7: the failure is raised after the batch", OUTBOX,
       "        if failures:\n            raise failures[0]\n", "",
       "test_relay__a_failing_row_is_recorded_and_the_batch_goes_on"),
    _m("an_empty_pump_acknowledges", "no acknowledgment without an indexed row", OUTBOX,
       "                        if taken else 0)", "                        if True else 0)",
       "test_relay__a_failing_row_is_recorded_and_the_batch_goes_on"),
    _m("the_ack_carries_another_workers_claim", "OB-1b: the relay acknowledges as the claim "
       "holder", OUTBOX, "acknowledge_dispatch(taken, worker_id=self.worker_id)",
       'acknowledge_dispatch(taken, worker_id="any")',
       "test_relay__a_full_index_stops_and_hands_the_rest_back"),
    _m("relays_share_a_worker_id", "OB-1b residual: each relay acknowledges under its own "
       "id", OUTBOX,
       '    worker_id: str = field(default_factory=lambda: f"relay-{uuid.uuid4().hex[:8]}")',
       '    worker_id: str = field(default_factory=lambda: "relay")',
       "test_relay__two_default_relays_carry_different_worker_ids"),
    _m("a_rebuild_does_not_reopen", "OB-1: a rebuild reopens the acknowledgments it may "
       "have erased", OUTBOX, "        await self.store.reopen_dispatch(since)\n", "",
       "test_relay__a_rebuild_fences_the_acknowledgments_it_may_have_erased"),
    _m("the_fence_is_read_after_the_snapshot", "OB-1: the fence's lower bound predates the "
       "snapshot", OUTBOX,
       "        since = await self.store.db_now()                 # BEFORE the snapshot (the fence)\n"
       "        indexed = await self.scheduler.rebuild(await self.store.dispatch_snapshot())\n",
       "        snapshot = await self.store.dispatch_snapshot()\n"
       "        since = await self.store.db_now()\n"
       "        indexed = await self.scheduler.rebuild(snapshot)\n",
       "test_relay__a_rebuild_fences_the_acknowledgments_it_may_have_erased"),
    # W6 A5: the one-connection statement helper and each adapter's wiring of it.
    _m("rpc_connection_reused", "R09/CF-4: a fresh connection per statement", RPC,
       "    conn = await connect()\n",
       '    conn = connect.__dict__.get("_kept") or connect.__dict__.setdefault('
       '"_kept", await connect())\n', RPC_CALL),
    _m("rpc_connection_left_open", "every statement's connection is closed", RPC,
       "    finally:\n        await conn.close()", "    finally:\n        pass", RPC_CALL,
       RPC_REFUSAL),
    _m("rpc_refusal_untyped", "a database error is the adapter's typed refusal", RPC,
       "                raise error(failed) from None", "                raise", RPC_REFUSAL),
    _m("rpc_no_refusal_calls_none", "without a mapping the error is raised as it is", RPC,
       "                if error is None or isinstance(failed, gone):",
       "                if isinstance(failed, gone):", RPC_REFUSAL),
    _m("rpc_outage_read_as_refusal", "RV-03: an outage is an outage before any refusal", RPC,
       "                if error is None or isinstance(failed, gone):",
       "                if error is None:", RPC_OUTAGE),
    _m("rpc_outage_raw", "RV-03: an unreachable store is a typed 503", RPC,
       "    except gone:\n", "    except ():\n", RPC_OUTAGE),
    _m("rpc_every_outage_typed", "without `unreachable` an outage is the bug it is", RPC,
       "    gone = OperationalError if unreachable is not None else ()",
       "    gone = OperationalError", RPC_OUTAGE),
    _m("rpc_retry_after_dropped", "the caller's Retry-After is carried", RPC,
       "DependencyUnavailable(unreachable, retry_after_s=retry_after_s)",
       "DependencyUnavailable(unreachable)", RPC_OUTAGE),
    _m("rpc_function_unnamed", "a call names its infrx function", RPC,
       'f"select infrx.{function}(%s)"', 'f"select infrx.call(%s)"', RPC_CALL),
    _m("rpc_count_read_as_rows", "a statement without rows answers its count", RPC,
       "if cursor.description else cursor.rowcount", "if True else cursor.rowcount", RPC_ROWS),
    _m("jobstore_refusal_untyped", "the job store's refusals are typed", J,
       "function, args, error=domain_error)", "function, args, error=None)", RPC_ADAPTERS),
    _m("operations_refusal_untyped", "a duplicate registry key is a Conflict", OPS,
       "params, error=_typed)", "params, error=domain_error)", RPC_ADAPTERS),
    _m("lifecycle_outage_raw", "RV-03: the lifecycle store's outage is a typed 503", LC,
       'unreachable="the lifecycle store is unreachable", ', "", RPC_ADAPTERS),
    _m("lifecycle_retry_after_default", "RV-03: the collector retries after 30 s", LC,
       "retry_after_s=30))", "retry_after_s=5))", RPC_ADAPTERS),
    _m("the_harness_alters_production", "SEC-1: guards are stepped around only in a test "
       "database", T, "    if not ok:\n        raise RuntimeError(", "    if False:\n"
       "        raise RuntimeError(",
       "test_the_harness_never_steps_around_a_guard_outside_a_test_database"),
    _m("the_crash_happens_before_the_commit", "crash_after_commit loses the answer, not "
       "the commit", T,
       "            result = await target(*args, **kw)\n            self.plan.after_commit(name)",
       "            self.plan.after_commit(name)\n            result = await target(*args, **kw)",
       "test_crash_after_commit_commits_then_loses_the_answer"),
)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run the D2 adapter mutation list"))
