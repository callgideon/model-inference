#!/usr/bin/env python3
"""R32/R40 for Q3: one single-edit defect per invariant `tests/q/test_reconcile.py`
claims, each naming the case that must fail.

The shared runner (`tests/contracts/mutants.py`, through `tests.q.mutants.run_mutant`)
applies the edit to a throwaway copy of the package and runs the named cases there, on
both adapters - the Valkey ones against this lane's task-local server. A kill needs every
named case among the failures, each death assertion-shaped (R83); a crash, a skip or a
stray failure is not a kill.

    uv run --frozen pytest -q tests/q/test_reconcile_mutants.py                   # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/q/test_reconcile_mutants.py # all
    uv run --frozen python tests/q/reconcile_mutants.py [--list] [names...]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
RECONCILE = "scheduling/reconcile.py"
OUTBOX_FAKE = "../tests/q/outboxfake.py"      # a test double, relative to the package

if str(API_DIR) not in sys.path:        # `python tests/q/reconcile_mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts.mutants import Mutant, Outcome  # noqa: E402,F401
from tests.q import vkharness                        # noqa: E402
from tests.q.mutants import run_mutant               # noqa: E402

#: The only suite a Q3 mutant may be killed by.
PATHS = "tests/q/test_reconcile.py"


def _m(name, invariant, old, new, *cases, file=RECONCILE) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- (1) the drain -------------------------------------------------------
    _m("the_row_is_acknowledged_before_it_is_indexed",
       "index first, acknowledge second: a row is never acknowledged for a candidate "
       "the index does not hold",
       '                        report["indexed"] += await self.index.enqueue(event)',
       "                        await self.store.acknowledge_dispatch([event.event_id], "
       "worker_id=self.worker_id)\n"
       '                        report["indexed"] += await self.index.enqueue(event)',
       "test_q3_drain__an_index_outage_acknowledges_only_what_was_indexed"),
    _m("every_row_read_is_acknowledged",
       "only rows the index took are acknowledged (a deferred or failed one is redelivered)",
       "await self.store.acknowledge_dispatch(\n                        indexed, ",
       "await self.store.acknowledge_dispatch(\n                        "
       "[e.event_id for e in events], ",
       "test_q3_drain__an_index_outage_acknowledges_only_what_was_indexed",
       "test_q3_drain__a_full_index_hands_the_row_back_and_the_next_drain_retries_it"),
    _m("the_ack_names_another_worker",
       "D2 OB-1b: the drain acknowledges as the worker that claimed the rows",
       "                        indexed, worker_id=self.worker_id)",
       "                        indexed, worker_id=\"relay\")",
       "test_q3_drain__every_dispatch_row_is_indexed_once_and_acknowledged"),
    _m("a_deferred_row_is_acknowledged",
       "a row refused by a full index stays unacknowledged, so the retry indexes it",
       "                        rest = events[position:]",
       "                        indexed.append(event.event_id)\n"
       "                        rest = events[position + 1:]",
       "test_q3_drain__a_full_index_hands_the_row_back_and_the_next_drain_retries_it"),
    _m("a_failed_batch_acknowledges_nothing",
       "what the index took before an outage is still acknowledged",
       "            finally:\n                if indexed:",
       "            except BaseException:\n                raise\n"
       "            else:\n                if indexed:",
       "test_q3_drain__an_index_outage_acknowledges_only_what_was_indexed"),
    _m("the_scan_is_unbounded",
       "one drain reads at most batch x max_batches rows",
       "        for _ in range(self.max_batches):",
       "        while True:",
       "test_q3_drain__one_call_reads_a_bounded_number_of_rows_and_the_next_continues"),
    _m("the_scan_stops_after_one_batch",
       "a full batch is followed by the next, up to the bound",
       "            if rest or len(events) < self.batch:\n                break",
       "            break",
       "test_q3_drain__one_call_reads_a_bounded_number_of_rows_and_the_next_continues"),
    _m("a_refused_row_waits_the_redelivery_window",
       "review DUR-6: a row read but not indexed is handed back at once (release_dispatch)",
       "                    await self.store.release_dispatch([event.event_id for event in rest])",
       "                    pass",
       "test_q3_drain__a_full_index_hands_the_row_back_and_the_next_drain_retries_it",
       "test_q3_drain__an_index_outage_acknowledges_only_what_was_indexed"),
    _m("the_failing_row_is_handed_back_too",
       "review DUR-6: the row the index failed on keeps its claim, so a row it keeps "
       "rejecting cannot block the rows behind it",
       "                    rest = events[position + 1:]           # this one fails",
       "                    rest = events[position:]           # this one fails",
       "test_q3_drain__an_index_outage_acknowledges_only_what_was_indexed"),
    _m("a_full_index_keeps_the_scan_going",
       "review DUR-6: a full index stops the drain (the handed-back rows are not re-read "
       "by the same call)",
       "            if rest or len(events) < self.batch:",
       "            if len(events) < self.batch:",
       "test_q3_drain__a_full_index_hands_the_row_back_and_the_next_drain_retries_it"),
    _m("the_redelivery_delay_is_not_passed",
       "a handed-out row comes back only after redelivery_s (the store's checkpoint)",
       "                                                       redelivery_s=self.redelivery_s)",
       "                                                       redelivery_s=0.0)",
       "test_q3_drain__a_lost_acknowledgment_is_a_redelivery_that_indexes_nothing_twice"),
    _m("the_outbox_lag_is_the_newest_row",
       "the outbox lag is the age of the OLDEST waiting dispatch",
       '                self.metrics["outbox_lag_s"] = max(',
       '                self.metrics["outbox_lag_s"] = min(',
       "test_q3_metrics__the_outbox_lag_is_the_oldest_waiting_dispatch"),
    _m("the_outbox_lag_outlives_the_backlog",
       "review DUR-3: a drain that finds nothing waiting reports no lag",
       '        self.metrics["outbox_lag_s"] = 0.0',
       "        pass",
       "test_q3_metrics__the_outbox_lag_is_the_oldest_waiting_dispatch"),
    _m("the_outbox_lag_is_the_last_batchs",
       "review DUR-3b: the lag is the oldest across all the drain's batches",
       '                    self.metrics["outbox_lag_s"],\n',
       "                    0.0,\n",
       "test_q3_metrics__the_outbox_lag_is_the_oldest_waiting_dispatch"),
    # --- (2) the reconciler --------------------------------------------------
    _m("dead_candidates_are_kept",
       "a candidate whose job no longer wants dispatch is removed",
       "        for job_id in dead:\n            await self.index.remove(job_id)",
       "        for job_id in dead:\n            pass",
       "test_q3_reconcile__a_dead_candidate_is_removed"),
    _m("a_wanted_job_is_judged_dead",
       "only jobs PostgreSQL does not want are dead; a consistent index is left alone",
       "for job_id in members.values() if job_id not in wanted})",
       "for job_id in members.values() if job_id in wanted})",
       "test_q3_reconcile__a_consistent_index_is_left_alone"),
    _m("the_pass_does_not_enqueue_what_is_missing",
       "a wanted job missing from the index is enqueued again",
       "                if await index.enqueue(event):",
       "                if False:",
       "test_q3_reconcile__a_queued_job_missing_from_the_index_is_indexed_again"),
    _m("an_acknowledged_wanted_job_is_left_out",
       "a wanted event the index refuses as acknowledged forces a rebuild",
       '        if report.pop("blocked", 0):',
       '        if report.pop("blocked", 0) and False:',
       "test_q3_reconcile__an_acknowledged_candidate_postgresql_still_wants_forces_a_rebuild",
       "test_q3_drill__an_acknowledgment_after_a_rebuild_is_repaired_by_the_next_pass"),
    _m("a_concurrent_delivery_is_taken_for_an_acknowledged_one",
       "a refused event is re-read before it counts as blocked (no spurious rebuild)",
       "        if refused:\n            present = await index.members()",
       "        if refused:\n            pass",
       "test_q3_reconcile__a_candidate_delivered_concurrently_is_not_taken_for_an_"
       "acknowledged_one"),
    _m("the_removal_is_not_followed_by_a_fresh_snapshot",
       "a job re-dispatched between the snapshot and the removal keeps its new candidate",
       "            snapshot = await self.store.dispatch_snapshot()\n",
       "            pass\n",
       "test_q3_reconcile__a_job_redispatched_during_the_pass_keeps_its_new_candidate"),
    _m("the_missing_gauge_is_not_published",
       "missing_index reports the wanted jobs the pass found unindexed",
       "self.metrics.update(missing_index=report[\"missing\"],",
       "self.metrics.update(missing_index=0,",
       "test_q3_reconcile__a_queued_job_missing_from_the_index_is_indexed_again"),
    _m("the_missing_lag_is_the_newest",
       "missing_lag_s is the age of the OLDEST missing job",
       "        oldest = max(((self.now() - event.available_at).total_seconds()",
       "        oldest = min(((self.now() - event.available_at).total_seconds()",
       "test_q3_reconcile__a_queued_job_missing_from_the_index_is_indexed_again"),
    _m("the_dead_gauge_is_not_published",
       "dead_candidates reports the candidates the pass removed",
       "                            dead_candidates=len(dead))",
       "                            dead_candidates=0)",
       "test_q3_reconcile__a_dead_candidate_is_removed"),
    # --- rebuild -------------------------------------------------------------
    _m("the_rebuild_is_not_topped_up",
       "Q2's hole: a delivery acknowledged behind a stale snapshot survives the rebuild",
       "            report, _ = await self._top_up(await self.store.dispatch_snapshot(), index)\n"
       '            if not report["blocked"]:',
       "            report = Counter(repaired=0)\n"
       '            if not report["blocked"]:',
       "test_q3_reconcile__a_delivery_behind_a_stale_snapshot_survives_the_rebuild"),
    _m("the_rebuild_starts_empty_and_applies_the_caps",
       "a rebuild is recovery: PostgreSQL's whole snapshot, caps not applied",
       "            count = await index.rebuild(await self.store.dispatch_snapshot())",
       "            count = await index.rebuild(())",
       "test_q3_reconcile__a_rebuild_is_recovery_and_ignores_the_caps"),
    _m("rebuilds_are_not_counted",
       "the rebuild counter moves once per rebuild",
       '            self.metrics["rebuilds"] += 1',
       '            self.metrics["rebuilds"] += 0',
       "test_q3_reconcile__an_acknowledged_candidate_postgresql_still_wants_forces_a_rebuild",
       "test_q3_reconcile__the_rebuild_is_postgresql_truth"),
    _m("a_blocked_rebuild_is_not_repeated",
       "review DUR-4: an acknowledgment landing inside the rebuild is repaired by it",
       '            if not report["blocked"]:\n                break',
       "            break",
       "test_q3_reconcile__an_acknowledgment_inside_the_rebuild_is_repaired_by_the_rebuild"),
    _m("a_twice_blocked_rebuild_counts_what_it_lost",
       "review DUR-4b: the rebuild's count leaves out the candidates a blocked top-up lost",
       '        return count + report["repaired"] - report["blocked"]',
       '        return count + report["repaired"]',
       "test_q3_reconcile__a_rebuild_blocked_twice_leaves_the_job_to_the_next_pass"),
    _m("a_rebuild_is_counted_only_after_its_top_up",
       "review DUR-4b: a rebuild is counted once the index is replaced, whatever follows",
       '            self.metrics["rebuilds"] += 1              # the index was replaced (DUR-4b)\n'
       "            report, _ = await self._top_up(await self.store.dispatch_snapshot(), index)\n",
       "            report, _ = await self._top_up(await self.store.dispatch_snapshot(), index)\n"
       '            self.metrics["rebuilds"] += 1\n',
       "test_q3_reconcile__a_rebuild_whose_top_up_fails_is_still_counted"),
    # --- (3) the switch ------------------------------------------------------
    _m("the_switch_is_not_topped_up_after_the_swap",
       "a delivery into the old index during the switch reaches the new one",
       "        self.index = index\n"
       "        report, _ = await self._top_up(await self.store.dispatch_snapshot(), index)",
       "        self.index = index\n"
       "        report = Counter(repaired=0)",
       "test_q3_switch__a_delivery_into_the_old_index_during_the_switch_reaches_the_new_one"),
    _m("the_switch_tops_up_from_a_snapshot_read_before_the_swap",
       "review DUR-2: the switch's top-up snapshot is read after the drain points at "
       "the new index",
       "        self.index = index\n"
       "        report, _ = await self._top_up(await self.store.dispatch_snapshot(), index)",
       "        report, _ = await self._top_up(await self.store.dispatch_snapshot(), index)\n"
       "        self.index = index",
       "test_q3_switch__a_delivery_after_the_post_swap_snapshot_lands_in_the_new_index"),
    _m("the_switch_leaves_the_drain_on_the_old_index",
       "after a switch the drain feeds the new index",
       "        count = await self.rebuild(index)\n        self.index = index",
       "        count = await self.rebuild(index)\n        pass",
       "test_q3_switch__a_live_pipeline_moves_to_the_other_adapter_and_loses_no_job",
       "test_q3_switch__a_delivery_into_the_old_index_during_the_switch_reaches_the_new_one"),
    # --- the loop ------------------------------------------------------------
    _m("the_loop_dies_on_an_outage",
       "the relay outlives a failing pass or drain",
       "                except Exception:\n                    self._failed(step.__name__)",
       "                except errors.DomainError:\n                    self._failed(step.__name__)",
       "test_q3_run__the_relay_reconciles_first_and_retries_a_failed_pass",
       "test_q3_run__a_pass_that_keeps_failing_never_stops_the_drain"),
    _m("the_drain_runs_behind_the_pass_loop",
       "review DUR-1: the drain does not wait for the passes to end",
       "        await asyncio.gather(every(self.reconcile, reconcile_every_s, drain_every_s),\n"
       "                             every(self.drain, drain_every_s, drain_every_s))",
       "        await every(self.reconcile, reconcile_every_s, drain_every_s)\n"
       "        await every(self.drain, drain_every_s, drain_every_s)",
       "test_q3_run__a_pass_that_keeps_failing_never_stops_the_drain"),
    _m("the_pass_is_retried_in_the_drain_loop",
       "review DUR-1b: a slowly failing pass does not serialise the drain (1ba884b's loop)",
       "        await asyncio.gather(every(self.reconcile, reconcile_every_s, drain_every_s),\n"
       "                             every(self.drain, drain_every_s, drain_every_s))",
       "        loop = asyncio.get_running_loop()\n"
       "        due = loop.time()\n"
       "        while not stop.is_set():\n"
       "            try:\n"
       "                if loop.time() >= due:\n"
       "                    await self.reconcile()\n"
       "                    due = loop.time() + reconcile_every_s\n"
       "            except Exception:\n"
       "                self._failed(\"reconcile\")\n"
       "            try:\n"
       "                await self.drain()\n"
       "            except Exception:\n"
       "                self._failed(\"drain\")\n"
       "            try:\n"
       "                await asyncio.wait_for(stop.wait(), drain_every_s)\n"
       "            except TimeoutError:\n"
       "                pass",
       "test_q3_run__a_slowly_failing_pass_does_not_hold_the_drain_back"),
    _m("the_first_tick_does_not_reconcile",
       "a starting relay reconciles at once",
       "        async def every(step, period_s: float, retry_s: float) -> None:\n",
       "        async def every(step, period_s: float, retry_s: float) -> None:\n"
       "            try:\n"
       "                await asyncio.wait_for(stop.wait(), period_s)\n"
       "            except TimeoutError:\n"
       "                pass\n",
       "test_q3_run__the_relay_reconciles_first_and_retries_a_failed_pass"),
    _m("a_failed_pass_waits_a_whole_period",
       "a failed pass stays due and is retried at the next tick",
       "                    wait = retry_s",
       "                    wait = period_s",
       "test_q3_run__the_relay_reconciles_first_and_retries_a_failed_pass"),
    _m("errors_are_not_counted",
       "every failed pass is counted",
       '        self.metrics["errors"] += 1',
       '        self.metrics["errors"] += 0',
       "test_q3_run__the_relay_reconciles_first_and_retries_a_failed_pass"),
    # --- the adapters' members() and caps -----------------------------------
    _m("memory_members_forget_in_flight_candidates",
       "members() lists in-flight candidates too, so a held dead one is removed",
       "        return {event_id: entry.event.job_id for event_id, entry in "
       "self._entries.items()}",
       "        return {event_id: entry.event.job_id for event_id, entry in "
       "self._entries.items() if entry.claimed_at is None}",
       "test_q3_reconcile__a_dead_candidate_is_removed",
       file="scheduling/memory.py"),
    _m("valkey_members_read_the_wrong_field",
       "members() maps an event to its job (field 6 of the packed entry)",
       '        return {_text(event_id): _text(packed).split("\\t")[5]',
       '        return {_text(event_id): _text(packed).split("\\t")[4]',
       "test_q3_reconcile__a_consistent_index_is_left_alone",
       file="scheduling/valkey.py"),
    _m("memory_caps_ignore_the_settings",
       "the memory adapter takes max_index_items from the settings",
       '                              else getattr(limits, "max_index_items", MAX_INDEX_ITEMS))',
       "                              else MAX_INDEX_ITEMS)",
       "test_q3_caps__both_adapters_take_the_caps_from_the_settings",
       file="scheduling/memory.py"),
    _m("valkey_caps_ignore_the_settings",
       "the Valkey adapter takes max_index_items from the settings",
       '_ITEMS_FIELD, _BYTES_FIELD = "max_index_items", "max_index_bytes"',
       '_ITEMS_FIELD, _BYTES_FIELD = "max_items", "max_index_bytes"',
       "test_q3_caps__both_adapters_take_the_caps_from_the_settings",
       file="scheduling/valkey.py"),
    # --- test-double checks (review HON-5): NOT product mutants -----------------
    # Each edits a test double - the contract fake store or D2's outbox fake - to prove
    # the named case can see the store rule it leans on; Q3's code fences nothing here.
    # DUR-FENCE: the fence is the store's (`JobStore.claim`); the fence case must see a
    # second lease when a stale candidate is handed one.
    _m("the_store_hands_a_running_job_a_second_lease",
       "a stale candidate never acquires a second lease",
       "            if job.state is not JobState.queued:",
       "            if job.state not in (JobState.queued, JobState.running):",
       "test_q3_fence__a_stale_candidate_never_acquires_a_second_lease",
       file="contracts/fakes/state.py"),
    # D2 OB-1b: only the claim holder's acknowledgment lands, so D2's rebuild fence can
    # refuse a Q3 drain's late acknowledgment.
    _m("the_store_acknowledges_a_row_it_reopened",
       "a reopened row's late acknowledgment is refused (D2's fence holds against Q3's drain)",
       "                 if event_id in self.claimed_at\n"
       "                 and self.claimed_by.get(event_id) == worker_id]",
       "                 ]",
       "test_q3_drain__an_acknowledgment_behind_another_relays_rebuild_fence_is_refused",
       file=OUTBOX_FAKE),
    # FID-3: the two `dispatch_pending` rules only the SIGKILL drill used to reach.
    _m("the_store_leaves_a_superseded_row_pending",
       "a row whose job moved on is acknowledged by the store as superseded",
       "                self.acknowledged[row.event_id] = now\n"
       "                self.last_error[row.event_id] = \"superseded\"",
       "                pass",
       "test_q3_drain__a_superseded_row_is_acknowledged_by_the_store_and_uses_its_slot",
       file=OUTBOX_FAKE),
    _m("the_store_filters_before_the_limit",
       "as in SQL, the limit is applied before the row filter: a superseded row uses a slot",
       "        for row in rows[:max(1, min(limit, 1000))]:",
       "        for row in [r for r in rows if wanted(r.kind, self.jobs.jobs[r.aggregate_id]"
       ".state)][:max(1, min(limit, 1000))]:",
       "test_q3_drain__a_superseded_row_is_acknowledged_by_the_store_and_uses_its_slot",
       file=OUTBOX_FAKE),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="run the Q3 mutation list")
    parser.add_argument("names", nargs="*", help="mutants to run (default: all)")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:56s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({case for m in MUTANTS for case in m.cases})} named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    vkharness.ensure()          # one server for the whole run, owned by this process
    bad: dict[str, list[str]] = {}
    for mutant in chosen:
        result = run_mutant(mutant, paths=PATHS)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}", flush=True)
        if not result.killed:
            bad.setdefault(str(result.outcome), []).append(mutant.name)
    failures = sum(len(names) for names in bad.values())
    print(f"\n{len(chosen) - failures}/{len(chosen)} killed"
          + "".join(f"; {outcome}: {names}" for outcome, names in sorted(bad.items())))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
