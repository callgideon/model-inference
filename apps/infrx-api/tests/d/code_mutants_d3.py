#!/usr/bin/env python3
"""R32/R40 for D3's Python half (`infrx/state/jobstore.py`: the lease operations).

Delegates to the one runner (`tests/contracts/mutants.py`, R83): each mutant is one edit to
a throwaway copy of the package, the named cases in `tests/d/test_lease_units.py` run there
(no Docker), and only `killed` counts. The SQL has its own list (`migration_mutants.py`,
the `d3_` entries).

    uv run --frozen pytest -q tests/d/test_code_mutants_d3.py
    uv run --frozen python -m tests.d.code_mutants_d3 --list
"""
from __future__ import annotations

import sys

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner

J = "state/jobstore.py"
RUNNER = Runner(name="d3", targets=("tests/d/test_lease_units.py",))
LIMITS = "test_lease_calls__send_the_stores_own_lease_limits"
FENCED = "test_fenced_calls__a_committed_refusal_is_raised_as_its_type"
WORK = "test_load_work__the_admitted_work_with_the_prompt_count"
RECOVER = "test_recover__outcomes_events_and_the_unsettleable_backlog"
RESULT_WRITE = "test_put_result__the_workers_lease_is_sent_and_null_is_already_terminal"


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=J, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    _m("d3_default_lease_limits_are_sent", "the store's retuned limits, never the defaults",
       '        return {name: getattr(self.limits, name) for name in (\n'
       '            "lease_ttl_s",',
       '        return {name: getattr(DEFAULTS, name) for name in (\n'
       '            "lease_ttl_s",', LIMITS),
    _m("d3_reconcile_window_not_sent", "the 24 h window is the store's, sent every call",
       '"max_prepublication_retries",\n            "unknown_usage_reconcile_s")}',
       '"max_prepublication_retries")}', LIMITS),
    _m("d3_fenced_refusal_returned", "R39: a committed refusal still raises its type",
       "        return self._answer(await self._call(function, {",
       "        return dict(await self._call(function, {", FENCED),
    _m("d3_credit_work_invented", "a CREDIT job's work is not a v1 Work",
       '        if admission["accounting_regime"] != "legacy_usd":\n'
       '            raise errors.NotFound(f"job {lease.job_id} is a CREDIT job',
       '        if False:\n'
       '            raise errors.NotFound(f"job {lease.job_id} is a CREDIT job',
       "test_load_work__a_credit_job_is_refused_not_invented"),
    _m("d3_prompt_count_dropped", "load_work carries preparation's exact prompt count",
       '                            prompt_tokens=admission["prepared_prompt_tokens"])',
       "                            prompt_tokens=None)", WORK),
    _m("d3_sources_as_prepared_refs", "prepared refs are preparation's, not the sources",
       '                                                for r in doc["prepared_refs"]),',
       '                                                for r in doc["request"]["media"]),',
       WORK),
    # D5 replaced `complete`'s fail-closed stub with the settlement: its two D3 mutants
    # (`d3_fence_refusal_swallowed`, `d3_complete_succeeds_silently`) lost their anchors
    # and are retired; `complete` is code_mutants_d5.py's.
    # R147 (0026): the worker's result write is fenced by its lease.
    _m("d3_result_write_drops_the_lease", "the worker's lease and the store's limits are sent",
       "        if lease is not None:\n            args |= {",
       "        if False:\n            args |= {", RESULT_WRITE),
    _m("d3_result_null_is_a_reference", "R29's committed NULL is already_terminal",
       "        if ref is None:\n            raise errors.AlreadyTerminal(",
       "        if False:\n            raise errors.AlreadyTerminal(", RESULT_WRITE),
    _m("d3_backlog_never_cleared", "each sweep reports its own backlog",
       "        self.unsettleable = {}\n        for item in",
       "        for item in", RECOVER),
    _m("d3_index_event_dropped", "a requeue is returned as its index event",
       '                produced.append(IndexEvent.model_validate(item["index_event"]))',
       '                IndexEvent.model_validate(item["index_event"])', RECOVER),
)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run the D3 adapter mutation list"))
