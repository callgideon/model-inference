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
COMPLETE = "test_complete__fails_closed_after_the_fence_and_raises_the_fences_refusals"
RECOVER = "test_recover__outcomes_events_and_the_unsettleable_backlog"


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
       '            raise errors.InvalidRequest(f"job {lease.job_id} is a CREDIT job',
       '        if False:\n'
       '            raise errors.InvalidRequest(f"job {lease.job_id} is a CREDIT job',
       "test_load_work__a_credit_job_is_refused_not_invented"),
    _m("d3_prompt_count_dropped", "load_work carries preparation's exact prompt count",
       '                            prompt_tokens=admission["prepared_prompt_tokens"])',
       "                            prompt_tokens=None)", WORK),
    _m("d3_sources_as_prepared_refs", "prepared refs are preparation's, not the sources",
       '                                                for r in doc["prepared_refs"]),',
       '                                                for r in doc["request"]["media"]),',
       WORK),
    _m("d3_fence_refusal_swallowed", "complete never turns a fence refusal into the D5 stub",
       "        except FeatureNotSupported:\n            pass",
       "        except Exception:\n            pass", COMPLETE),
    _m("d3_complete_succeeds_silently", "complete fails closed after the fence",
       '        raise NotImplementedError("JobStore.complete: the fence held; the settlement '
       "is D5's\")",
       "        return None", COMPLETE),
    _m("d3_backlog_never_cleared", "each sweep reports its own backlog",
       "        self.unsettleable = {}\n        for item in",
       "        for item in", RECOVER),
    _m("d3_index_event_dropped", "a requeue is returned as its index event",
       '                produced.append(IndexEvent.model_validate(item["index_event"]))',
       '                IndexEvent.model_validate(item["index_event"])', RECOVER),
)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run the D3 adapter mutation list"))
