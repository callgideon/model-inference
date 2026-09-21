#!/usr/bin/env python3
"""R32 for the Q adapter: one single-edit defect per invariant the Q suite claims.

Same rules as the coordinator's list for the fakes (`tests/contracts/mutants.py`,
whose `Mutant`/`Outcome`/`Result` this reuses): the edit is applied to a **copy** of
the package in a temporary directory, the named cases are run there, and a mutant that
survives means the case asserting that invariant proves nothing. Nothing is ever
written inside the worktree. The runner differs in one thing only - it runs `tests/q`
instead of `tests/contracts/test_conformance.py`, because the cases that must notice
are Q's own plus the exported scheduler suite applied to Q's adapter.

    uv run --frozen pytest -q tests/q/test_mutants.py     # the whole list
    uv run --frozen python tests/q/mutants.py --list
    uv run --frozen python tests/q/mutants.py the_kind_filter_is_inverted
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

API_DIR = pathlib.Path(__file__).resolve().parents[2]
Q = "scheduling/memory.py"

if str(API_DIR) not in sys.path:        # `python tests/q/mutants.py` starts in tests/q
    sys.path.insert(0, str(API_DIR))

# The coordinator's runner is hard-wired to `tests/contracts/test_conformance.py`, so Q
# brings its own `run_mutant`; the declaration, the outcome vocabulary and the "only a
# kill counts" result are shared rather than re-invented.
from tests.contracts.mutants import Mutant, Outcome, Result, _failing_ids  # noqa: E402


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=Q, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- fair selection ------------------------------------------------------
    _m("tie_break_prefers_the_newest_arrival",
       "two flows at the same virtual time are separated by arrival order",
       "            order = (flow.tag - self._virtual_time[flow_kind], self._entries[candidate].seq)",
       "            order = (flow.tag - self._virtual_time[flow_kind], -self._entries[candidate].seq)",
       "test_q1_fair__tenants_alternate_and_ties_break_on_arrival"),
    _m("the_fair_choice_takes_the_largest_virtual_time",
       "dispatch serves the smallest virtual finish time, so a peer is never starved",
       "            if best is None or order < best[0]:",
       "            if best is None or order > best[0]:",
       "test_q1_fair__a_noisy_tenant_cannot_delay_a_peer_past_one_round"),
    _m("the_tag_does_not_advance_at_dispatch",
       "the virtual finish time advances by the service a dispatch hands out",
       "        flow.tag = start + cost / self._weight(entry.event.org_id)",
       "        flow.tag = start",
       "test_q1_fair__the_virtual_finish_time_advances_at_dispatch_only"),
    _m("the_tag_ignores_the_weight",
       "a weighted tenant gets a proportionally larger share",
       "        flow.tag = start + cost / self._weight(entry.event.org_id)",
       "        flow.tag = start + cost",
       "test_q1_fair__weight_buys_a_proportional_share"),
    # r2 B1: restored. The earlier claim that this edit was behaviour-preserving was
    # wrong - a served flow sitting at exactly the virtual time with an older head
    # sequence beats a newcomer at tag = V and loses to it at tag = 0.
    _m("an_arriving_tenant_starts_at_zero",
       "an arriving tenant starts at its pool's virtual time, not at zero",
       "            flow = self._flows[key] = _Flow(tag=self._virtual_time[key[0]])",
       "            flow = self._flows[key] = _Flow(tag=0.0)",
       "test_q1_fair__a_newcomer_does_not_outrank_a_served_flow_with_an_older_head"),
    _m("the_dispatch_start_is_not_clamped_to_the_virtual_time",
       "a candidate that waited catches up once and cannot then hoard the index",
       "        start = max(flow.tag, self._virtual_time[kind_value])",
       "        start = flow.tag",
       "test_q1_fair__a_candidate_that_waited_catches_up_once_and_cannot_hoard"),
    _m("a_non_positive_weight_is_accepted",
       "a weight that would divide by zero inside dispatch is refused where it is set",
       '            if not (weight > 0) or weight == float("inf"):',
       "            if False:",
       "test_q1_config__a_weight_that_would_break_dispatch_is_refused_where_it_is_set"),
    # r2 B2/B3/B4 -----------------------------------------------------------
    _m("virtual_time_is_shared_across_dispatch_kinds",
       "each dispatch kind keeps its own virtual time, so one pool's traffic never "
       "erases the debt between another pool's tenants",
       "        kind_value = entry.event.kind.value",
       "        kind_value = DISPATCH_KINDS[0].value",
       "test_q1_kind__preparation_traffic_does_not_erase_the_weighted_share",
       "test_q1_kind__preparation_traffic_does_not_erase_a_service_time_debt"),
    _m("tie_break_ignores_arrival_order",
       "the tie-break is the candidate's arrival sequence, not flow creation order",
       "            order = (flow.tag - self._virtual_time[flow_kind], self._entries[candidate].seq)",
       "            order = (flow.tag - self._virtual_time[flow_kind], 0)",
       "test_q1_fair__ties_break_on_arrival_not_on_flow_creation_or_on_the_org_id"),
    _m("tie_break_uses_the_org_id",
       "the tie-break is the candidate's arrival sequence, not the organization id",
       "            order = (flow.tag - self._virtual_time[flow_kind], self._entries[candidate].seq)",
       "            order = (flow.tag - self._virtual_time[flow_kind], _org_id)",
       "test_q1_fair__ties_break_on_arrival_not_on_flow_creation_or_on_the_org_id"),
    _m("the_tag_advance_ignores_the_estimator",
       "one dispatch moves the tag by exactly cost/weight, from the injected estimator",
       "        flow.tag = start + cost / self._weight(entry.event.org_id)",
       "        flow.tag = start + 1.0 / self._weight(entry.event.org_id)",
       "test_q1_fair__one_dispatch_moves_the_tag_by_exactly_cost_over_weight"),
    _m("a_bad_service_cost_is_not_validated",
       "a bad estimator answer is a typed internal_error raised before anything moves",
       '        if not (cost > 0) or cost == float("inf"):',
       "        if False:",
       "test_q1_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing"),
    _m("visibility_expires_after_the_ttl",
       "visibility ends at exactly the TTL (`>=`, the boundary the fake pins)",
       "            if now >= entry.claimed_at + timedelta(seconds=self._visibility_s(entry.event)):",
       "            if now > entry.claimed_at + timedelta(seconds=self._visibility_s(entry.event)):",
       "test_q1_kind__visibility_expires_at_the_ttl_not_after_it"),
    _m("rebuild_indexes_a_duplicate_twice",
       "a duplicate in the snapshot is indexed, handed out and charged exactly once",
       "            if event.event_id in self._entries:\n                continue",
       "            if False:\n                continue",
       "test_q1_rebuild__a_duplicate_in_the_snapshot_is_indexed_and_charged_once"),
    _m("rebuild_does_not_charge_bytes",
       "a rebuilt index charges exactly the compact bytes of its snapshot",
       "            self._bytes += size\n            flow = self._flow(event.kind, event.org_id)",
       "            flow = self._flow(event.kind, event.org_id)",
       "test_q1_rebuild__a_duplicate_in_the_snapshot_is_indexed_and_charged_once"),
    _m("fairness_state_is_shared_across_dispatch_kinds",
       "R52: preparation and inference are separate pools, so separate fairness state",
       "        key = (kind.value, org_id)",
       '        key = ("any", org_id)',
       "test_q1_kind__preparation_and_inference_are_separately_fair"),
    # --- one event, one worker (visibility and replay) -----------------------
    _m("visibility_is_measured_from_the_event_time",
       "visibility is a timeout from the claim, so a backlogged candidate is not "
       "handed to two workers at once",
       "            if now >= entry.claimed_at + timedelta(seconds=self._visibility_s(entry.event)):",
       "            if now >= entry.event.available_at + timedelta(seconds=self._visibility_s(entry.event)):",
       "dur_outbox__a_lost_worker_returns_its_candidate"),
    _m("a_claim_does_not_record_when_it_happened",
       "a claimed candidate is in flight, and a lost worker's candidate comes back",
       "        entry.worker, entry.claimed_at = worker_id, now",
       "        entry.worker = worker_id",
       "dur_outbox__a_lost_worker_returns_its_candidate"),
    _m("the_preparation_lease_times_out_every_kind",
       "R52: visibility follows the lease of the pool that was fed",
       "        return (self.limits.preparation_lease_ttl_s if event.is_preparation",
       "        return (self.limits.preparation_lease_ttl_s if True",
       "test_q1_kind__a_lost_preparation_worker_returns_its_candidate_on_the_preparation_lease"),
    _m("returned_candidates_lose_their_arrival_order",
       "FIFO inside a tenant survives a lost worker",
       "            self._flows[key].events.sort(key=lambda event_id: self._entries[event_id].seq)",
       "            pass",
       "test_q1_kind__returned_candidates_keep_their_arrival_order"),
    _m("enqueue_forgets_the_acknowledged_ids",
       "an acknowledged candidate is never indexed again",
       "        if event.event_id in self._entries or event.event_id in self.acknowledged:",
       "        if event.event_id in self._entries:",
       "dur_outbox__acknowledged_candidates_do_not_come_back"),
    _m("enqueue_reindexes_a_candidate_already_in_the_index",
       "a replayed outbox event indexes exactly one candidate",
       "        if event.event_id in self._entries or event.event_id in self.acknowledged:",
       "        if event.event_id in self.acknowledged:",
       "dur_outbox__enqueue_is_replay_safe",
       "dur_outbox__a_claimed_candidate_is_not_re_indexed"),
    # --- dispatch kinds (R52, R55) ------------------------------------------
    _m("the_kind_filter_is_inverted",
       "R52: a preparation pool is never handed an inference candidate",
       "            if kind is not None and flow_kind != kind:",
       "            if kind is None and flow_kind != kind:",
       "dur_outbox__a_candidate_carries_its_dispatch_kind"),
    _m("an_unknown_kind_is_swallowed",
       "R55: an unknown kind is a typed refusal, not an empty index",
       "        if kind is not None and kind not in DISPATCH_KINDS:",
       "        if False:",
       "dur_outbox__a_candidate_carries_its_dispatch_kind"),
    _m("a_candidate_is_offered_before_it_is_available",
       "`available_at` bounds dispatch, so a scheduled retry is not dispatched early",
       "                              if self._entries[event_id].event.available_at <= now), None)",
       "                              if True), None)",
       "test_q1_stats__a_candidate_is_not_offered_before_it_is_available",
       "test_q1_property__the_index_is_work_conserving_and_loses_nothing"),
    # --- bounded memory ------------------------------------------------------
    _m("the_item_cap_is_not_enforced",
       "a full index refuses with a typed retryable error instead of growing",
       "        if len(self._entries) + 1 > self._max_items:",
       "        if False and len(self._entries) + 1 > self._max_items:",
       "test_q1_caps__a_full_index_refuses_with_a_typed_retryable_error"),
    _m("the_byte_cap_is_not_enforced",
       "queued bytes are capped independently of the item count",
       "        if self._bytes + size > self._max_bytes:",
       "        if False and self._bytes + size > self._max_bytes:",
       "test_q1_caps__queued_bytes_are_counted_per_candidate_and_returned"),
    _m("bytes_are_never_returned",
       "byte accounting is symmetric: what enqueue charges, leaving gives back",
       "        self._bytes -= entry.size",
       "        pass",
       "test_q1_caps__queued_bytes_are_counted_per_candidate_and_returned"),
    _m("depth_is_not_reported_per_kind",
       "index depth is published per dispatch kind",
       "            depth_by_kind[entry.event.kind.value] += 1",
       "            depth_by_kind[entry.event.kind.value] += 0",
       "test_q1_stats__depth_and_waiting_age_are_reported_without_claiming_capacity"),
    # --- cancellation --------------------------------------------------------
    _m("cancellation_keeps_the_candidates",
       "cancellation removes every candidate of the job, pending or in flight",
       "            if entry.event.job_id == job_id:\n                self._forget(event_id)",
       "            if entry.event.job_id == job_id:\n                pass",
       "test_q1_cancel__removal_drops_pending_and_in_flight_candidates_and_their_flow"),
    _m("cancellation_makes_the_index_authoritative",
       "the index never becomes the authority on cancellation: a replayed dispatch "
       "event may be re-indexed and is refused by JobStore.claim",
       "                self._forget(event_id)",
       "                self._forget(event_id)\n                self.acknowledged.add(event_id)",
       "test_q1_index__a_stale_candidate_never_executes"),
    _m("an_empty_tenant_keeps_its_fairness_state",
       "empty and cancelled tenants retain no fairness state",
       "        if flow.refs <= 0:",
       "        if flow.refs < 0:",
       "test_q1_cancel__removal_drops_pending_and_in_flight_candidates_and_their_flow"),
    # --- rebuild -------------------------------------------------------------
    _m("rebuild_keeps_the_in_flight_entries",
       "rebuild replaces the index with PostgreSQL truth exactly once",
       "        self._entries.clear()",
       "        pass",
       "dur_outbox__rebuild_restores_every_queued_job_exactly_once"),
    _m("rebuild_keeps_the_acknowledged_ids",
       "rebuild clears the acknowledged ids, so a requeued job is indexable again",
       "        self.acknowledged.clear()",
       "        pass",
       "test_q1_rebuild__clears_in_flight_and_acknowledged_and_keeps_no_stale_tenant"),
    _m("rebuild_does_not_restart_the_fairness_epoch",
       "rebuild o enqueue is idempotent: a running index and a rebuilt one are equal",
       "        self._virtual_time = {kind.value: 0.0 for kind in DISPATCH_KINDS}\n        for event in snapshot:",
       "        for event in snapshot:",
       "test_q1_property__rebuild_after_enqueue_is_the_same_index_as_rebuild_alone"),
    # --- shape ---------------------------------------------------------------
    _m("acknowledge_is_synchronous",
       "every port operation is async, as the ports table declares it",
       "    async def acknowledge(self, event: IndexEvent) -> None:",
       "    def acknowledge(self, event: IndexEvent) -> None:",
       "test_q1_contract__the_adapter_satisfies_the_scheduler_protocol"),
)


def run_mutant(mutant: Mutant, *, paths: str = "tests/q") -> Result:
    """Apply one mutant to a throwaway copy and run the cases it names under `tests/q`.

    A kill needs all three of the coordinator runner's conditions: pytest exited 1,
    at least one test failed, and every failing id names one of the mutant's own cases
    - so a syntax error or an import failure is `broken_runner`, never a kill. The
    anchor must appear **exactly once**, because two identical lines (there are two
    `self._virtual_time = 0.0`) would otherwise silently mutate the wrong one.
    """
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"q-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        junk = shutil.ignore_patterns("__pycache__")
        shutil.copytree(API_DIR / "infrx", root / "infrx", ignore=junk)
        shutil.copytree(API_DIR / "tests", root / "tests", ignore=junk)
        target = root / "infrx" / mutant.file
        source = target.read_text()
        found = source.count(mutant.old)
        if found != 1:
            return Result(Outcome.misdeclared,
                          f"anchor appears {found} times in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        selection = " or ".join(mutant.cases)
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
             "-rf", "--tb=no", "-o", "addopts=--import-mode=importlib",
             "-o", "testpaths=tests", paths, "-k", selection],
            cwd=root, capture_output=True, text=True,
            env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (0, 1):
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        if "no tests ran" in summary or not any(word in summary for word in
                                                ("passed", "failed", "skipped")):
            return Result(Outcome.misdeclared, f"no case matched {selection!r}: {summary}")
        failed, errored = _failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"errors outside the named cases: {errored[:3]}")
        if done.returncode == 0 or not failed:
            return Result(Outcome.survived, summary)
        stray = [test_id for test_id in failed
                 if not any(case in test_id for case in mutant.cases)]
        if stray:
            return Result(Outcome.broken_runner,
                          f"failures outside the named cases: {stray[:3]}")
        # r2: **every** named case must notice. The coordinator's runner is satisfied by
        # one failure among the names, and that let a mutant claim coverage from a case it
        # could not kill - this list had exactly that shape (a service-time fixture whose
        # preparation cost hid the defect it was named for).
        unproven = [case for case in mutant.cases
                    if not any(case in test_id for test_id in failed)]
        if unproven:
            return Result(Outcome.misdeclared,
                          f"named cases that did not notice: {unproven}")
        return Result(Outcome.killed, summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="run the Q1 mutation list")
    parser.add_argument("names", nargs="*", help="mutants to run (default: all)")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:52s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({case for m in MUTANTS for case in m.cases})} named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad: dict[str, list[str]] = {}
    for mutant in chosen:
        result = run_mutant(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}")
        if not result.killed:
            bad.setdefault(str(result.outcome), []).append(mutant.name)
    failures = sum(len(names) for names in bad.values())
    print(f"\n{len(chosen) - failures}/{len(chosen)} killed"
          + "".join(f"; {outcome}: {names}" for outcome, names in sorted(bad.items())))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
