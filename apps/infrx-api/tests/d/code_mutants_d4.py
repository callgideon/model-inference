#!/usr/bin/env python3
"""R32/R40 for D4's Python half (`infrx/state/journal.py`, `PgStreamStore`).

Delegates to the one runner (`tests/contracts/mutants.py`, R83): each mutant is one edit to
a throwaway copy of the package, the named cases in `tests/d/test_journal_units.py` run there
(no Docker), and only `killed` counts. The SQL has its own list (`migration_mutants.py`,
the `d4_` entries).

    uv run --frozen pytest -q tests/d/test_code_mutants_d4.py
    uv run --frozen python -m tests.d.code_mutants_d4 --list
"""
from __future__ import annotations

import sys

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner

J = "state/journal.py"
RUNNER = Runner(name="d4", targets=("tests/d/test_journal_units.py",))
APPEND = "test_append__sends_the_stores_own_limits_and_raises_a_committed_refusal"
ROWS = "test_append__answers_the_committed_rows_not_its_input"
READ = "test_read_owned__sends_the_cursor_and_returns_the_next_one"
FINALIZE = "test_finalize__the_outcome_is_a_lookup_key_not_content"
EXPIRE = "test_expire__passes_the_callers_bound_and_counts"
JSONB = "test_append__refuses_what_jsonb_cannot_store_before_sending_it"


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=J, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    _m("the_refusal_is_returned_not_raised", "R39: a committed refusal still raises its type",
       '        rows = PgJobStore._answer(answer)["chunks"]',
       '        rows = answer.get("chunks", ())', APPEND),
    _m("the_default_event_limit_is_sent", "the store's retuned limits, never the defaults",
       '                "limits": {"journal_event_max_bytes": self.limits.journal_event_max_bytes,',
       '                "limits": {"journal_event_max_bytes": DEFAULTS.journal_event_max_bytes,',
       APPEND),
    _m("chunks_built_from_the_input_not_the_rows", "the relay gets the committed rows' bytes",
       "        return tuple(Chunk.model_validate(row) for row in rows)",
       "        return tuple(Chunk.model_validate({**row, \"bytes\": len(__import__(\"json\").dumps(\n"
       "            event.payload, separators=(\",\", \":\")).encode())}) for row, event in zip(rows, events))",
       ROWS),
    _m("chunks_numbered_from_the_batch_not_the_rows", "the relay gets the committed cursors",
       "        return tuple(Chunk.model_validate(row) for row in rows)",
       "        return tuple(Chunk.model_validate({**row, \"sequence\": n + 1})\n"
       "                     for n, row in enumerate(rows))", ROWS),
    _m("the_next_cursor_is_always_the_callers", "a page advances the cursor to its last chunk",
       "        return page, (page[-1].cursor if page else cursor)",
       "        return page, cursor", READ),
    _m("a_malformed_limit_reaches_the_database", "the limit is an integer before anything is sent",
       "        if isinstance(limit, bool) or not isinstance(limit, int):",
       "        if False:", READ),
    _m("finalize_accepts_another_outcome", "R30: the outcome is a lookup key, not content",
       "        if outcome != _outcome(stored):", "        if False:", FINALIZE),
    _m("finalize_answers_after_expiry", "an expired journal is 410, never a terminal chunk",
       "        if expired:", "        if False:", FINALIZE),
    # review M1: what jsonb cannot store is refused typed, before anything is sent
    _m("the_unjournalable_payload_is_sent", "a NUL or a non-finite number is refused typed",
       "        if not all(_journalable(event.payload) for event in events):",
       "        if False:", JSONB),
    _m("a_nul_in_a_key_is_sent", "a NUL character in a key is refused",
       "        return all(_journalable(key) and _journalable(item) for key, item in value.items())",
       "        return all(_journalable(item) for key, item in value.items())", JSONB),
    _m("a_non_finite_number_is_sent", "NaN and the infinities are refused",
       "        return math.isfinite(value)", "        return True", JSONB),
    _m("a_nul_inside_a_list_is_sent", "a NUL at any depth is refused",
       "        return all(_journalable(item) for item in value)", "        return True", JSONB),
    _m("expire_drops_the_callers_bound", "a caller's tighter bound is kept (R7)",
       '            "now": None if now is None else now.isoformat(), "limit":',
       '            "now": None, "limit":', EXPIRE),
    _m("the_pruning_bound_is_not_sent", "one pass prunes at most EXPIRE_JOBS_PER_PASS jobs",
       '"limit": EXPIRE_JOBS_PER_PASS}))', "}))", EXPIRE),
)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run the D4 adapter mutation list"))
