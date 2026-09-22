#!/usr/bin/env python3
"""R32 for the Q adapter: one single-edit defect per invariant the Q suite claims.

Same rules as the coordinator's list for the fakes, because it is the same runner
(`tests/contracts/mutants.py`, F2R item 9): the edit is applied to a **copy** of the
package in a temporary directory, the named cases are run there, and a mutant that
survives means the case asserting that invariant proves nothing. Nothing is ever
written inside the worktree. This file supplies the target - `tests/q` instead of
`tests/contracts/test_conformance.py`, because the cases that must notice are Q's own
plus the exported scheduler suite applied to Q's adapter - and the list.

    uv run --frozen pytest -q tests/q/test_mutants.py     # the whole list
    uv run --frozen python -m tests.q.mutants --list
    uv run --frozen python -m tests.q.mutants the_kind_filter_is_inverted
"""
from __future__ import annotations

import pathlib
import sys

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner   # noqa: F401

API_DIR = pathlib.Path(__file__).resolve().parents[2]
Q = "scheduling/memory.py"


def _m(name, invariant, old, new, *cases, dies_by=(), occurrences=1) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=Q, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by), occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- fair selection ------------------------------------------------------
    _m("tie_break_prefers_the_newest_arrival",
       "two flows at the same virtual time are separated by arrival order",
       "            order = (flow.tag, self._entries[candidate].seq)",
       "            order = (flow.tag, -self._entries[candidate].seq)",
       "test_q1_fair__tenants_alternate_and_ties_break_on_arrival"),
    _m("the_fair_choice_takes_the_largest_virtual_time",
       "dispatch serves the smallest virtual finish time, so a peer is never starved",
       "            order = (flow.tag, self._entries[candidate].seq)\n"
       "            if best is None or order < best[0]:",
       "            order = (flow.tag, self._entries[candidate].seq)\n"
       "            if best is None or order > best[0]:",
       "test_q1_fair__a_noisy_tenant_cannot_delay_a_peer_past_one_round"),
    _m("the_kind_choice_takes_the_largest_kind_tag",
       "R60 level 1 serves the smallest kind tag, so the other kind is never starved",
       "            order = (self._kind_tag[dispatch_kind.value], self._entries[event_id].seq)\n"
       "            if best is None or order < best[0]:",
       "            order = (self._kind_tag[dispatch_kind.value], self._entries[event_id].seq)\n"
       "            if best is None or order > best[0]:",
       "test_q1_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog"),
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
       "            order = (flow.tag, self._entries[candidate].seq)",
       "            order = (flow.tag, 0)",
       "test_q1_fair__ties_break_on_arrival_not_on_flow_creation_or_on_the_org_id"),
    _m("tie_break_uses_the_org_id",
       "the tie-break is the candidate's arrival sequence, not the organization id",
       "            order = (flow.tag, self._entries[candidate].seq)",
       "            order = (flow.tag, _org_id)",
       "test_q1_fair__ties_break_on_arrival_not_on_flow_creation_or_on_the_org_id"),
    _m("the_tag_advance_ignores_the_estimator",
       "one dispatch moves the tag by exactly cost/weight, from the injected estimator",
       "        flow.tag = start + cost / self._weight(entry.event.org_id)",
       "        flow.tag = start + 1.0 / self._weight(entry.event.org_id)",
       "test_q1_fair__one_dispatch_moves_the_tag_by_exactly_cost_over_weight"),
    _m("the_virtual_time_is_the_unclamped_tag",
       "the pool's virtual time is the clamped start of the dispatch, so it never "
       "moves backwards when a delayed flow catches up",
       "        self._virtual_time[kind_value] = start",
       "        self._virtual_time[kind_value] = flow.tag",
       "test_q1_fair__a_candidate_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_virtual_time_is_written_before_the_cost_is_validated",
       "nothing is written before the estimator's answer is validated (r3 N18)",
       "        cost = self._service_cost(entry.event)",
       "        self._virtual_time[entry.event.kind.value] = flow.tag\n"
       "        cost = self._service_cost(entry.event)",
       "test_q1_fair__a_bad_service_cost_after_a_dispatch_moves_no_virtual_time"),
    _m("the_claim_is_recorded_before_the_cost_is_validated",
       "the candidate is not marked in flight by a dispatch that raises",
       "        cost = self._service_cost(entry.event)",
       "        entry.claimed_at = now\n        cost = self._service_cost(entry.event)",
       "test_q1_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing"),
    _m("the_candidate_leaves_its_flow_before_the_cost_is_validated",
       "the literal r2 bug: taking the candidate out of its flow before validating the "
       "cost wedges it - neither pending nor in flight, so never re-offered",
       "        cost = self._service_cost(entry.event)",
       "        flow.events.remove(event_id)\n        cost = self._service_cost(entry.event)",
       "test_q1_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing"),
    _m("the_estimator_is_never_consulted",
       "the injected estimator is called on every dispatch, so its answer is what the "
       "tag advances by and a bad answer is still a typed refusal",
       "        cost = self._service_cost(entry.event)",
       "        cost = 1.0",
       "test_q1_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing",
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
       "test_q1_kind__preparation_and_inference_are_separately_fair", dies_by=("KeyError",)),
    # --- R60: unfiltered claims (r2 B6/B7) -----------------------------------
    _m("the_kind_tag_does_not_advance",
       "R60 level 1: a dispatched kind is charged, so an unfiltered worker cannot serve "
       "one kind to exhaustion",
       "            self._kind_tag[kind_value] = top_start + cost",
       "            self._kind_tag[kind_value] = top_start",
       "test_q1_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog",
       "test_q1_none__one_org_with_both_kinds_cannot_starve_another_orgs_single_kind_work",
       "test_q1_none__a_filtered_worker_never_moves_the_kind_state",
       "test_q1_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_kind_tag_advances_by_one_not_by_the_cost",
       "R60 level 1 charges the kind the service it received, so an unfiltered worker "
       "shares the machine by service time and not by request count (r3 T07)",
       "            self._kind_tag[kind_value] = top_start + cost",
       "            self._kind_tag[kind_value] = top_start + 1.0",
       "test_q1_none__the_kind_tag_advances_by_the_service_the_kind_received"),
    _m("the_kind_is_ranked_by_its_oldest_candidate",
       "R60 level 1 ranks a kind by the candidate its own rule would hand out, not by "
       "the oldest candidate it holds (r3 T22)",
       "            order = (self._kind_tag[dispatch_kind.value], self._entries[event_id].seq)",
       "            order = (self._kind_tag[dispatch_kind.value],\n"
       "                     min((self._entries[other].seq\n"
       "                          for (flow_kind, _org), other_flow in self._flows.items()\n"
       "                          if flow_kind == dispatch_kind.value\n"
       "                          for other in other_flow.events), default=0))",
       "test_q1_none__the_kind_is_ranked_by_the_candidate_it_would_actually_hand_out"),
    _m("the_top_virtual_time_is_the_unclamped_kind_tag",
       "R60 level 1: the top-level virtual time is the clamped start of the dispatch, so "
       "it never moves backwards when a quiet kind comes back (r3 T04)",
       "            self._top_virtual_time = top_start",
       "            self._top_virtual_time = self._kind_tag[kind_value]",
       "test_q1_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_kind_start_is_not_clamped_to_the_top_virtual_time",
       "R60 level 1: a kind that waited catches up once and cannot then hoard",
       "            top_start = max(self._kind_tag[kind_value], self._top_virtual_time)",
       "            top_start = self._kind_tag[kind_value]",
       "test_q1_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_top_virtual_time_never_advances",
       "R60 level 1: the top-level virtual time follows the dispatched kind's start",
       "            self._top_virtual_time = top_start",
       "            self._top_virtual_time = 0.0",
       "test_q1_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_kind_tie_breaks_on_dict_order",
       "R60 level 1: equal kind tags are separated by the candidate's arrival sequence, "
       "not by the order the kinds sit in a dict",
       "            order = (self._kind_tag[dispatch_kind.value], self._entries[event_id].seq)",
       "            order = (self._kind_tag[dispatch_kind.value], 0)",
       "test_q1_none__the_kind_tie_breaks_on_arrival_order"),
    _m("the_kind_tie_breaks_on_the_kind_name",
       "R60 level 1: equal kind tags are separated by arrival, not by the kind's name",
       "            order = (self._kind_tag[dispatch_kind.value], self._entries[event_id].seq)",
       "            order = (self._kind_tag[dispatch_kind.value], dispatch_kind.value)",
       "test_q1_none__the_kind_tie_breaks_on_arrival_order"),
    _m("a_filtered_claim_moves_the_kind_state",
       "R60: kind-filtered claims never read or write level-1 state",
       "        if kind is None:\n            # R60 level 1: for an unfiltered worker",
       "        if True:\n            # R60 level 1: for an unfiltered worker",
       "test_q1_none__a_filtered_worker_never_moves_the_kind_state"),
    _m("an_unfiltered_claim_ignores_the_kind_level",
       "R60: an unfiltered claim is served through the two-level rule, not by comparing "
       "tenant flows across kinds",
       "        chosen = (self._select_across_kinds(now) if kind is None\n"
       "                  else self._select(kind, now))",
       "        chosen = self._select(kind, now)",
       "test_q1_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog",
       "test_q1_none__one_org_with_both_kinds_cannot_starve_another_orgs_single_kind_work",
       "test_q1_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("a_new_flow_arrives_at_the_highest_virtual_time",
       "a flow arrives at its OWN kind's virtual time, never the highest of any kind",
       "            flow = self._flows[key] = _Flow(tag=self._virtual_time[key[0]])",
       "            flow = self._flows[key] = _Flow(tag=max(self._virtual_time.values()))",
       "test_q1_none__a_new_flow_arrives_at_its_own_kinds_virtual_time"),
    _m("rebuild_keeps_the_kind_level_state",
       "R60: a rebuild restarts both levels",
       "        self._kind_tag = {kind.value: 0.0 for kind in DISPATCH_KINDS}",
       "        pass",
       "test_q1_rebuild__clears_the_kind_level_state"),
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
       "        self._virtual_time = {kind.value: 0.0 for kind in DISPATCH_KINDS}\n"
       "        self._kind_tag = {kind.value: 0.0 for kind in DISPATCH_KINDS}",
       "        self._kind_tag = {kind.value: 0.0 for kind in DISPATCH_KINDS}",
       "test_q1_property__rebuild_after_enqueue_is_the_same_index_as_rebuild_alone"),
    # --- shape ---------------------------------------------------------------
    _m("acknowledge_is_synchronous",
       "every port operation is async, as the ports table declares it",
       "    async def acknowledge(self, event: IndexEvent) -> None:",
       "    def acknowledge(self, event: IndexEvent) -> None:",
       "test_q1_contract__the_adapter_satisfies_the_scheduler_protocol"),
)


#: F2R item 9: the shared runner, aimed at `tests/q`. `require_every_case` is Q's own
#: stricter rule, now a runner option: a mutant may not claim coverage from a case that
#: cannot see it (the r2 review found exactly that - a service-time fixture whose
#: preparation cost hid the defect its second case was named for).
RUNNER = Runner(name="q", targets=("tests/q",), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    """Apply one mutant to a throwaway copy and run the cases it names under `tests/q`."""
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run the Q1 mutation list"))
