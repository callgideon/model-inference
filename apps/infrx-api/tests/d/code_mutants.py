#!/usr/bin/env python3
"""R32/R40 for D2's Python half (`infrx/state/jobstore.py`, `outbox.py`, `pgtesting.py`).

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

J, O, T = "state/jobstore.py", "state/outbox.py", "state/pgtesting.py"
RUNNER = Runner(name="d2", targets=("tests/d/test_adapter_units.py",))


def _m(name, invariant, file, old, new, *cases, dies_by=(), occurrences=1) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by), occurrences=occurrences)


MAPPING = "test_domain_error__each_refusal_is_its_most_specific_type"
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
    _m("everything_read_is_acknowledged", "only what the index took is acknowledged", O,
       "acknowledged = await self.store.acknowledge_dispatch(taken) if taken else 0",
       "acknowledged = await self.store.acknowledge_dispatch("
       "[e.event_id for e in events]) if events else 0",
       "test_relay__acknowledges_exactly_what_the_index_took"),
    _m("a_full_index_stops_the_pump", "a full index defers a row, it does not fail the pump", O,
       "            except errors.CapacityExhausted:", "            except errors.RateLimited:",
       "test_relay__acknowledges_exactly_what_the_index_took"),
    _m("a_failing_index_is_acknowledged", "enqueue before acknowledge", O,
       "            except errors.CapacityExhausted:", "            except Exception:",
       "test_relay__a_failing_index_acknowledges_nothing"),
    _m("an_empty_pump_acknowledges", "no acknowledgment without an indexed row", O,
       "if taken else 0", "if True else 0",
       "test_relay__a_failing_index_acknowledges_nothing"),
    _m("a_rebuild_does_not_reopen", "OB-1: a rebuild reopens the acknowledgments it may "
       "have erased", O, "        await self.store.reopen_dispatch(since)\n", "",
       "test_relay__a_rebuild_fences_the_acknowledgments_it_may_have_erased"),
    _m("the_fence_is_read_after_the_snapshot", "OB-1: the fence's lower bound predates the "
       "snapshot", O,
       "        since = await self.store.db_now()                 # BEFORE the snapshot (the fence)\n"
       "        indexed = await self.scheduler.rebuild(await self.store.dispatch_snapshot())\n",
       "        snapshot = await self.store.dispatch_snapshot()\n"
       "        since = await self.store.db_now()\n"
       "        indexed = await self.scheduler.rebuild(snapshot)\n",
       "test_relay__a_rebuild_fences_the_acknowledgments_it_may_have_erased"),
    _m("the_crash_happens_before_the_commit", "crash_after_commit loses the answer, not "
       "the commit", T,
       "            result = await target(*args, **kw)\n            self.plan.after_commit(name)",
       "            self.plan.after_commit(name)\n            result = await target(*args, **kw)",
       "test_crash_after_commit_commits_then_loses_the_answer"),
)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run the D2 adapter mutation list"))
