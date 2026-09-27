#!/usr/bin/env python3
"""R32/R40/R83 for T3: one single-edit defect per decision `tests/t/retention` claims.

The shared runner (`tests/contracts/mutants.py`) runs `test_retention.py` against each
mutated copy: memory always, the ClickHouse + MinIO half with `INFRX_T3_STACK=1`. The
mutants only the real store can see (`NEEDS_STACK`) skip, naming their owner, without it.
`schema.sql` is not runner-mutable (the runner compiles mutated files as Python): its FINAL
reads and version are proved through the store queries mutated here.

    uv run --frozen pytest -q tests/t/retention/test_mutants.py                     # subset
    INFRX_T3_STACK=1 INFRX_MUTANTS=all uv run --frozen pytest -q tests/t/retention/test_mutants.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE = "tests/t/retention/test_retention.py"

if str(API_DIR) not in sys.path:        # `python tests/t/retention/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

R = "traces/retention/policy.py"
S = "traces/ship/shipper.py"
F = "traces/feedback/projector.py"

DELETE = "test_a_deletion_removes_logical_access_at_once_while_the_bytes_remain"
SWEEP = "test_the_sweep_reaches_the_projection_the_objects_and_the_feedback"
REPLAY = "test_a_replay_after_deletion_resurrects_nothing"
EXPIRY = "test_expired_content_is_unreadable_then_swept_while_its_metadata_stays"
HOLDS = "test_content_a_live_grant_or_export_references_is_kept"
MIDWAY = "test_a_sweep_that_fails_midway_finishes_on_the_next_one"
GAUGES = "test_loss_lag_and_retention_gauges_fire_their_alarms"


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # --- logical expiry ---------------------------------------------------------------
    _m("content_bound_inclusive", "at the content bound the content has expired", R,
       "        return now < started_at + timedelta(days=self.content_days)",
       "        return now <= started_at + timedelta(days=self.content_days)", EXPIRY),
    _m("metadata_never_expires", "metadata and feedback end at 13 calendar months", R,
       "        return now < add_months(at, self.metadata_months)", "        return True",
       EXPIRY),
    _m("months_ignore_the_year", "13 months crosses a year", R,
       "    year, month = at.year + index // 12, index % 12 + 1",
       "    year, month = at.year, index % 12 + 1", EXPIRY),
    _m("read_ignores_content_expiry", "expired content is unreadable before any cleanup", R,
       "        if not rows or not all(self.content_live(r.started_at, now) for r in rows):",
       "        if not rows:", EXPIRY),
    _m("expiry_not_recorded", "an expiry is recorded, once", R,
       "        if pairs:\n            await self.store.put(",
       "        if False:\n            await self.store.put(", EXPIRY),
    _m("expiry_cutoff_is_now", "only content past its bound expires", R,
       "self.store.expired_content(now - timedelta(days=self.content_days), limit)",
       "self.store.expired_content(now, limit)", EXPIRY),
    # --- logical deletion -------------------------------------------------------------
    _m("delete_not_recorded", "a deletion writes its receipt", R,
       "        stone = Tombstone(org_id, request_id, REQUEST, reason, self.clock())\n"
       "        await self.store.put([stone])",
       "        stone = Tombstone(org_id, request_id, REQUEST, reason, self.clock())", DELETE),
    _m("traces_read_after_deletion", "a deleted request's traces are unreadable at once", R,
       "    async def find_traces(self, org_id: str, request_id: str):\n        if REQUEST in",
       "    async def find_traces(self, org_id: str, request_id: str):\n        if None in",
       DELETE),
    _m("feedback_read_after_deletion", "a deleted request's feedback is unreadable at once", R,
       "    async def find_feedback(self, org_id: str, request_id: str):\n        if REQUEST in",
       "    async def find_feedback(self, org_id: str, request_id: str):\n        if None in",
       DELETE),
    # --- the sweep --------------------------------------------------------------------
    _m("sweep_ignores_holds", "content a live grant or export references is kept", R,
       "                if self.holds is not None and await self.holds(stone.org_id, "
       "stone.request_id):",
       "                if False:", HOLDS),
    _m("sweep_keeps_objects", "the sweep reaches the objects", R,
       "                        await self.objects.delete(row.content_key)",
       "                        pass", SWEEP),
    _m("sweep_follows_a_cross_org_key", "one tenant's deletion never reaches another's object",
       R, "                    if row.content_key and row.content_key.startswith(own):",
       "                    if row.content_key:", SWEEP),
    _m("sweep_keeps_rows", "a deletion reaches the trace and feedback rows", R,
       "                    await self.store.purge(stone.org_id, stone.request_id)",
       "                    pass", SWEEP),
    _m("sweep_purges_expired_metadata", "an expiry removes the content, not the metadata", R,
       "                if stone.scope == REQUEST:", "                if True:", EXPIRY),
    _m("sweep_never_marks_cleaned", "the receipt records the cleanup, once", R,
       "                await self.store.put([replace(stone, cleaned_at=self.clock())])",
       "                pass", SWEEP),
    _m("sweep_failure_escapes", "one failed tombstone stays pending; the sweep goes on", R,
       "            except Exception:                   # noqa: BLE001 - pending, retried "
       "next sweep",
       "            except ZeroDivisionError:           # noqa: BLE001 - pending, retried "
       "next sweep", MIDWAY, dies_by=("DependencyUnavailable",)),
    # --- no resurrection --------------------------------------------------------------
    _m("replay_ignores_deletion", "a replayed deleted record is dropped", R,
       "            if REQUEST in scopes or not self.metadata_live(e.started_at, now):",
       "            if not self.metadata_live(e.started_at, now):", REPLAY),
    _m("late_ship_keeps_expired_content", "a record past its content bound ships no content",
       R, "            elif CONTENT in scopes or not self.content_live(e.started_at, now):",
       "            elif CONTENT in scopes:", EXPIRY),
    _m("feedback_redelivery_ignores_deletion", "a redelivered deleted feedback stays gone", R,
       "        return [r for r in rows if REQUEST not in stones.get((r.org_id, r.request_id), {})\n"
       "                and self.metadata_live(r.created_at, now)]",
       "        return [r for r in rows if self.metadata_live(r.created_at, now)]", REPLAY),
    _m("shipper_skips_the_policy", "the shipper consults the tombstones", S,
       "        verdicts = await self.retention.verdicts(scan.records) if self.retention else {}",
       "        verdicts = {}", REPLAY),
    _m("replay_leaves_its_object", "an object whose row never landed goes with the replay", S,
       "                await self.objects.delete(content_key(envelope.org_id, trace_id))",
       "                pass", REPLAY),
    _m("expired_record_ships_content", "no content object after the bound", S,
       '                    continue\n                content = b""',
       "                    continue", EXPIRY),
    _m("projector_skips_the_policy", "the feedback projector consults the tombstones", F,
       "            rows = await self.retention.keep_feedback(rows)", "            pass", REPLAY),
    # --- gauges and alarms ------------------------------------------------------------
    _m("loss_ratio_over_the_wrong_denominator", "loss over the eligible rows", R,
       'out["infrx_trace_loss_ratio"] = sum(c[1] for c in counts.values()) / eligible if',
       'out["infrx_trace_loss_ratio"] = sum(c[1] for c in counts.values()) / (eligible + 1) if',
       GAUGES),
    _m("backlog_counts_held", "a held deletion is not a cleanup backlog", R,
       "    due = [s for s in pending if s not in held]", "    due = pending", GAUGES),
    _m("backlog_age_zero", "the backlog's age is the oldest due deletion's", R,
       "        ((now - s.deleted_at).total_seconds() for s in due), default=0.0)",
       "        (0.0 for s in due), default=0.0)", GAUGES),
    _m("lag_is_the_count", "feedback lag is the oldest event's age", R,
       'out["infrx_feedback_projection_lag_seconds"] = lag["oldest_s"]',
       'out["infrx_feedback_projection_lag_seconds"] = lag["pending"]', GAUGES),
    _m("every_rule_fires", "an alarm fires only past its threshold", R,
       '            if rule["metric"] in values and _OPS[rule["op"]](values[rule["metric"]],\n'
       '                                                             rule["threshold"])]',
       '            if rule["metric"] in values]', GAUGES),
    # --- the ClickHouse store (stack only) --------------------------------------------
    _m("pending_includes_cleaned", "a cleaned tombstone is not swept again", R,
       'f"SELECT {\', \'.join(COLUMNS)} FROM {TABLE} FINAL WHERE state = {PENDING} "',
       'f"SELECT {\', \'.join(COLUMNS)} FROM {TABLE} FINAL "', SWEEP),
    _m("pending_without_final", "a superseded pending tombstone is not swept again", R,
       'f"SELECT {\', \'.join(COLUMNS)} FROM {TABLE} FINAL WHERE state = {PENDING} "',
       'f"SELECT {\', \'.join(COLUMNS)} FROM {TABLE} WHERE state = {PENDING} "', SWEEP),
    _m("expiry_repeats", "an expired request already tombstoned is not expired again", R,
       'f"AND (org_id, request_id) NOT IN (SELECT org_id, request_id FROM {TABLE} FINAL) "',
       '"AND 1 = 1 "', EXPIRY),
    _m("expiry_ignores_the_cutoff", "only content past the cutoff expires", R,
       '"AND started_at <= toDateTime64({cutoff:String}, 6, \'UTC\') "',
       '"AND toDateTime64({cutoff:String}, 6, \'UTC\') IS NOT NULL "', EXPIRY),
    _m("purge_keeps_feedback", "a deletion reaches the feedback projection", R,
       "        for table in (TRACES, FEEDBACK):", "        for table in (TRACES,):", SWEEP),
    _m("loss_counts_every_row", "only rows with a loss reason are lost", R,
       "\"SELECT mode, count(), countIf(loss_reason != 'none'), sum(content_bytes) \"",
       "\"SELECT mode, count(), count(), sum(content_bytes) \"", GAUGES),
)

#: Only the real ClickHouse sees these: the in-memory store has no query text.
NEEDS_STACK = frozenset({"pending_includes_cleaned", "pending_without_final", "expiry_repeats",
                         "expiry_ignores_the_cutoff", "purge_keeps_feedback",
                         "loss_counts_every_row"})
RUNNER = Runner(name="t3", targets=(SUITE,), env=("INFRX_T3_STACK",))


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run T3's mutation list"))
