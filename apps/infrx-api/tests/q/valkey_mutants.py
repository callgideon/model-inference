#!/usr/bin/env python3
"""R32 for the Valkey adapter: one single-edit defect per invariant the Q2 suite claims.

Same rules and the same runner as Q1's list (`tests/q/mutants.py`, whose `run_mutant` this
imports): the edit is applied to a **copy** of the package in a temporary directory, the
cases the mutant names are run there against the real server, and a kill needs pytest to
exit 1 with every named case among the failures - so a syntax error, an import failure or
a stray failure is `broken_runner`, never a kill. Nothing is written inside the worktree.

Most of the anchors are Lua. That is the point: the fairness arithmetic, the selection
rule and the accounting now live in scripts, and a script nobody can break on purpose is
a script nobody has tested.

    uv run --frozen pytest -q tests/q/test_valkey_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/q/test_valkey_mutants.py   # all
    uv run --frozen python tests/q/valkey_mutants.py --list
"""
from __future__ import annotations

import argparse
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
VALKEY = "scheduling/valkey.py"

if str(API_DIR) not in sys.path:        # `python tests/q/valkey_mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts.mutants import Mutant, Outcome  # noqa: E402
from tests.q.mutants import run_mutant               # noqa: E402

#: The only suite a Q2 mutant may be killed by: the cases that drive the real adapter.
PATHS = "tests/q/test_valkey_scheduler.py"


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=VALKEY, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- level 2: the fair choice inside one kind (points 1, 2, 4) ------------
    _m("tie_break_prefers_the_newest_arrival",
       "two flows at the same virtual time are separated by arrival order",
       "             or (tag == best.tag and e.seq < best.seq) then",
       "             or (tag == best.tag and e.seq > best.seq) then",
       "test_q2_fair__tenants_alternate_and_ties_break_on_arrival"),
    _m("the_fair_choice_takes_the_largest_virtual_time",
       "dispatch serves the smallest virtual finish time, so a peer is never starved",
       "          if best == nil or tag < best.tag",
       "          if best == nil or tag > best.tag",
       "test_q2_fair__tenants_alternate_and_ties_break_on_arrival"),
    _m("the_flow_is_not_walked_in_arrival_order",
       "FIFO inside a flow is the ZSET's arrival-sequence order, not insertion order",
       "      local queued = redis.call('ZRANGE', pending_key(flow), 0, -1)",
       "      local queued = redis.call('ZREVRANGE', pending_key(flow), 0, -1)",
       "test_q2_fair__tenants_alternate_and_ties_break_on_arrival"),
    _m("a_candidate_is_offered_before_it_is_available",
       "`available_at` bounds dispatch, so a scheduled retry is not dispatched early",
       "        if e and e.avail <= now then",
       "        if e then",
       "test_q2_stats__a_candidate_is_not_offered_before_it_is_available"),
    _m("the_flow_scan_stops_at_its_head",
       "a not-yet-available older candidate does not block a newer available one",
       "          break\n        end\n      end\n    end\n  end\n  return best",
       "          break\n        end\n        break\n      end\n    end\n  end\n  return best",
       "test_q2_stats__a_candidate_is_not_offered_before_it_is_available"),
    _m("the_tag_does_not_advance_at_dispatch",
       "the virtual finish time advances by the service a dispatch hands out",
       "redis.call('ZADD', TAGS, score_text(start + cost / weight), pick.flow)",
       "redis.call('ZADD', TAGS, score_text(start), pick.flow)",
       "test_q2_fair__the_virtual_finish_time_advances_at_dispatch_only"),
    _m("the_tag_ignores_the_weight",
       "a weighted tenant gets a proportionally larger share",
       "redis.call('ZADD', TAGS, score_text(start + cost / weight), pick.flow)",
       "redis.call('ZADD', TAGS, score_text(start + cost), pick.flow)",
       "test_q2_fair__weight_buys_a_proportional_share"),
    _m("the_tag_advance_ignores_the_estimator",
       "one dispatch moves the tag by exactly cost/weight, from the injected estimator",
       "redis.call('ZADD', TAGS, score_text(start + cost / weight), pick.flow)",
       "redis.call('ZADD', TAGS, score_text(start + 1 / weight), pick.flow)",
       "test_q2_fair__one_dispatch_moves_the_tag_by_exactly_cost_over_weight"),
    _m("the_default_weight_is_not_one",
       "an unweighted tenant is weighted 1, not 0 (a division by zero) or anything else",
       "if weight == nil then weight = 1 end",
       "if weight == nil then weight = 2 end",
       "test_q2_fair__the_virtual_finish_time_advances_at_dispatch_only"),
    _m("the_weight_is_read_for_the_wrong_tenant",
       "the weight is the dispatched candidate's own organization's",
       "local org = string.sub(pick.flow, #pick.kind + 2)",
       "local org = pick.flow",
       "test_q2_fair__weight_buys_a_proportional_share"),
    _m("the_dispatch_start_is_not_clamped_to_the_virtual_time",
       "a candidate that waited catches up once and cannot then hoard the index",
       "local start = pick.tag\nif vt > start then start = vt end",
       "local start = pick.tag",
       "test_q2_fair__a_candidate_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_virtual_time_is_the_unclamped_tag",
       "the pool's virtual time is the clamped start of the dispatch, so it never moves "
       "backwards when a delayed flow catches up",
       "redis.call('ZADD', CLOCK, score_text(start), 'vt|' .. pick.kind)",
       "redis.call('ZADD', CLOCK, score_text(pick.tag), 'vt|' .. pick.kind)",
       "test_q2_fair__a_candidate_that_waited_catches_up_once_and_cannot_hoard"),
    _m("an_arriving_tenant_starts_at_zero",
       "an arriving tenant starts at its pool's virtual time, not at zero",
       "  local vt = redis.call('ZSCORE', CLOCK, 'vt|' .. kind)\n"
       "  if vt == false or vt == nil then vt = '0' end",
       "  local vt = '0'",
       "test_q2_fair__a_newcomer_arrives_at_its_own_kinds_virtual_time"),
    _m("a_new_flow_arrives_at_the_highest_virtual_time",
       "a flow arrives at its OWN kind's virtual time, never the highest of any kind",
       "  local vt = redis.call('ZSCORE', CLOCK, 'vt|' .. kind)\n"
       "  if vt == false or vt == nil then vt = '0' end",
       "  local vt = redis.call('ZSCORE', CLOCK, 'vt|prepare_dispatch')\n"
       "  if vt == false or vt == nil then vt = '0' end",
       "test_q2_fair__a_newcomer_arrives_at_its_own_kinds_virtual_time"),
    _m("the_virtual_time_of_a_kind_is_reset_when_it_empties",
       "V[kind] is never reset when a flow empties - only a rebuild resets it",
       "  if redis.call('HINCRBY', REFS, e.flow, -1) <= 0 then\n"
       "    redis.call('HDEL', REFS, e.flow)",
       "  if redis.call('HINCRBY', REFS, e.flow, -1) <= 0 then\n"
       "    redis.call('ZADD', CLOCK, '0', 'vt|' .. e.kind)\n"
       "    redis.call('HDEL', REFS, e.flow)",
       "test_q2_fair__the_virtual_time_of_a_kind_survives_an_empty_index"),
    _m("fairness_state_is_shared_across_dispatch_kinds",
       "R52: preparation and inference are separate pools, so separate fairness state",
       "local flow = kind .. '|' .. org\nif redis.call('HINCRBY', REFS, flow, 1) == 1 then",
       "local flow = 'any|' .. org\nif redis.call('HINCRBY', REFS, flow, 1) == 1 then",
       "test_q2_kind__preparation_and_inference_are_separately_fair"),
    # --- the estimator is priced before anything moves (point 8) -------------
    _m("the_probe_commits_before_the_cost_is_validated",
       "the selecting call moves no state: the candidate is priced first, so a bad "
       "estimator answer leaves the index byte-identical (the r2 B4 defect, in Lua)",
       "if expected == '' or expected ~= pick.id then return {1, pick.id, payload} end",
       "if false then return {1, pick.id, payload} end",
       "test_q2_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing"),
    _m("a_bad_service_cost_is_not_validated",
       "a bad estimator answer is a typed internal_error raised before anything moves",
       '        if not (cost > 0) or cost == float("inf"):',
       "        if False:",
       "test_q2_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing"),
    _m("the_estimator_is_never_consulted",
       "the injected estimator is called on every dispatch",
       "            cost = self._service_cost(event)         # validated before anything moves",
       "            cost = 1.0",
       "test_q2_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing"),
    _m("an_estimator_bug_is_reported_as_the_callers_fault",
       "an estimator that raises is `internal_error` - it is our collaborator, not input",
       '            raise errors.InternalError("the service-cost estimator raised") from exc',
       '            raise errors.InvalidRequest("the service-cost estimator raised") from exc',
       "test_q2_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing"),
    _m("a_domain_error_from_the_estimator_is_rewrapped",
       "a DomainError the estimator raises itself passes through unchanged",
       "        except errors.DomainError:\n            raise",
       "        except errors.DomainError as domain:\n"
       '            raise errors.InternalError("wrapped") from domain',
       "test_q2_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing"),
    _m("an_unset_valkey_url_is_not_refused",
       "VALKEY_URL has one reader and an unset one is a typed refusal, not a client "
       "pointed at a default nobody chose",
       "    if not limits.valkey_url:",
       "    if False:",
       "test_q2_config__the_url_comes_from_the_settings_and_an_unset_one_is_refused"),
    _m("a_non_positive_weight_is_accepted",
       "a weight that would divide by zero inside the script is refused where it is set",
       '            if not (weight > 0) or weight == float("inf"):',
       "            if False:",
       "test_q2_config__a_weight_that_would_break_dispatch_is_refused_where_it_is_set"),
    # --- visibility (point 9) ------------------------------------------------
    _m("visibility_expires_after_the_ttl",
       "visibility ends at exactly the TTL (`>=`, the boundary the fake pins)",
       "local due = redis.call('ZRANGEBYSCORE', INFLIGHT, '-inf', int_text(now))",
       "local due = redis.call('ZRANGEBYSCORE', INFLIGHT, '-inf', '(' .. int_text(now))",
       "test_q2_kind__visibility_expires_at_the_ttl_and_on_its_pools_lease"),
    _m("visibility_is_never_evaluated",
       "a lost worker's candidate comes back without touching PostgreSQL",
       "local due = redis.call('ZRANGEBYSCORE', INFLIGHT, '-inf', int_text(now))",
       "local due = {}",
       "test_q2_kind__visibility_expires_at_the_ttl_and_on_its_pools_lease",
       "dur_outbox__a_lost_worker_returns_its_candidate"),
    _m("visibility_is_measured_from_the_event_time",
       "visibility is a timeout from the claim, so a backlogged candidate is not handed "
       "to two workers at once",
       "redis.call('ZADD', INFLIGHT, int_text(now + e.ttl), pick.id)",
       "redis.call('ZADD', INFLIGHT, int_text(e.avail + e.ttl), pick.id)",
       "dur_outbox__a_lost_worker_returns_its_candidate"),
    _m("the_preparation_lease_times_out_every_kind",
       "R52: visibility follows the lease of the pool that was fed",
       "        visibility = (self.limits.preparation_lease_ttl_s if event.is_preparation",
       "        visibility = (self.limits.preparation_lease_ttl_s if True",
       "test_q2_kind__visibility_expires_at_the_ttl_and_on_its_pools_lease"),
    _m("returned_candidates_lose_their_arrival_order",
       "a returned candidate re-enters at its ORIGINAL sequence, so FIFO survives a "
       "lost worker",
       "    redis.call('ZADD', pending_key(e.flow), int_text(e.seq), due[i])",
       "    redis.call('ZADD', pending_key(e.flow), int_text(e.seq + 1000000), due[i])",
       "test_q2_kind__returned_candidates_keep_their_arrival_order"),
    _m("a_claimed_candidate_stays_claimable",
       "a claim takes the candidate out of its flow, so two workers never hold one",
       "redis.call('ZREM', pending_key(pick.flow), pick.id)",
       "redis.call('ZCARD', pending_key(pick.flow), pick.id)",
       "test_q2_replay__enqueue_is_replay_safe_across_pending_inflight_and_acknowledged",
       "dur_outbox__enqueue_is_replay_safe"),
    # --- R60 level 1 (points 5, 6) -------------------------------------------
    _m("the_kind_tag_does_not_advance",
       "R60 level 1: a dispatched kind is charged, so an unfiltered worker cannot serve "
       "one kind to exhaustion",
       "  redis.call('ZADD', CLOCK, score_text(top_start + cost), 'kt|' .. pick.kind)",
       "  redis.call('ZADD', CLOCK, score_text(top_start), 'kt|' .. pick.kind)",
       "test_q2_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog"),
    _m("the_kind_tag_advances_by_one_not_by_the_cost",
       "R60 level 1 charges the kind the service it received, so an unfiltered worker "
       "shares the machine by service time and not by request count",
       "  redis.call('ZADD', CLOCK, score_text(top_start + cost), 'kt|' .. pick.kind)",
       "  redis.call('ZADD', CLOCK, score_text(top_start + 1), 'kt|' .. pick.kind)",
       "test_q2_none__the_kind_tag_advances_by_the_service_the_kind_received"),
    _m("the_kind_tag_is_weighted",
       "R60 level 1 charges the RAW cost: the weight belongs to level 2 alone",
       "  redis.call('ZADD', CLOCK, score_text(top_start + cost), 'kt|' .. pick.kind)",
       "  redis.call('ZADD', CLOCK, score_text(top_start + cost / 4), 'kt|' .. pick.kind)",
       "test_q2_none__the_kind_tag_advances_by_the_service_the_kind_received"),
    _m("the_kind_choice_takes_the_largest_kind_tag",
       "R60 level 1 serves the smallest kind tag, so the other kind is never starved",
       "      if best == nil or ktag < best.ktag",
       "      if best == nil or ktag > best.ktag",
       "test_q2_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog"),
    _m("the_kinds_are_ranked_by_their_tenants_tags",
       "R60 withdrew `tag - V[kind]` across kinds: a kind's virtual time stands still "
       "while the other kind is dispatched, so a peer keeps its lag for ever",
       "      local ktag = score(CLOCK, 'kt|' .. kinds[i])",
       "      local ktag = candidate.tag",
       "test_q2_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog"),
    _m("the_kind_tie_breaks_on_list_order",
       "R60 level 1: equal kind tags are separated by the candidate's arrival sequence, "
       "not by the order the kinds sit in a list",
       "         or (ktag == best.ktag and candidate.seq < best.seq) then",
       "         or (ktag == best.ktag and false) then",
       "test_q2_none__the_kind_tie_breaks_on_arrival_order"),
    _m("the_kind_tie_breaks_on_the_kind_name",
       "R60 level 1: equal kind tags are separated by arrival, not by the kind's name",
       "         or (ktag == best.ktag and candidate.seq < best.seq) then",
       "         or (ktag == best.ktag and kinds[i] < best.kind) then",
       "test_q2_none__the_kind_tie_breaks_on_arrival_order"),
    _m("the_kind_is_ranked_by_its_oldest_candidate",
       "R60 level 1 ranks a kind by the candidate its own level-2 rule would hand out, "
       "not by the oldest candidate it holds (corrected 2026-09-21)",
       "      local ktag = score(CLOCK, 'kt|' .. kinds[i])\n"
       "      if best == nil or ktag < best.ktag\n"
       "         or (ktag == best.ktag and candidate.seq < best.seq) then",
       "      local ktag = score(CLOCK, 'kt|' .. kinds[i])\n"
       "      local oldest = candidate.seq\n"
       "      candidate.seq = oldest\n"
       "      local all = redis.call('ZRANGE', TAGS, 0, -1)\n"
       "      for n = 1, #all do\n"
       "        if string.sub(all[n], 1, #kinds[i] + 1) == kinds[i] .. '|' then\n"
       "          local head = redis.call('ZRANGE', pending_key(all[n]), 0, 0, 'WITHSCORES')\n"
       "          if head[2] and tonumber(head[2]) < oldest then oldest = tonumber(head[2]) end\n"
       "        end\n"
       "      end\n"
       "      candidate.seq = oldest\n"
       "      if best == nil or ktag < best.ktag\n"
       "         or (ktag == best.ktag and oldest < best.seq) then",
       "test_q2_none__the_kind_is_ranked_by_the_candidate_it_would_actually_hand_out"),
    _m("a_kind_with_no_eligible_pick_is_charged",
       "R60 level 1: a kind with no eligible candidate is skipped and left untouched",
       "    local candidate = pick_in_kind(kinds[i], now)\n    if candidate then",
       "    local candidate = pick_in_kind(kinds[i], now)\n"
       "    if not candidate then\n"
       "      redis.call('ZADD', CLOCK, score_text(score(CLOCK, 'kt|' .. kinds[i]) + 1),\n"
       "                 'kt|' .. kinds[i])\n"
       "    end\n    if candidate then",
       "test_q2_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_kind_start_is_not_clamped_to_the_top_virtual_time",
       "R60 level 1: a kind that waited catches up once and cannot then hoard",
       "  if top > top_start then top_start = top end",
       "  local _unused = top",
       "test_q2_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_top_virtual_time_is_the_unclamped_kind_tag",
       "R60 level 1: the top-level virtual time is the clamped start of the dispatch, so "
       "it never moves backwards when a quiet kind comes back",
       "  redis.call('ZADD', CLOCK, score_text(top_start), 'top')",
       "  redis.call('ZADD', CLOCK, score_text(pick.ktag), 'top')",
       "test_q2_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("the_top_virtual_time_never_advances",
       "R60 level 1: the top-level virtual time follows the dispatched kind's start",
       "  redis.call('ZADD', CLOCK, score_text(top_start), 'top')",
       "  redis.call('ZADD', CLOCK, score_text(0), 'top')",
       "test_q2_none__a_kind_that_waited_catches_up_once_and_cannot_hoard"),
    _m("a_filtered_claim_moves_the_kind_state",
       "R60: kind-filtered claims never read or write level-1 state",
       "if top_level then\n  -- level 1 writes",
       "if true then\n  -- level 1 writes",
       "test_q2_none__a_filtered_worker_never_moves_the_kind_state"),
    _m("an_unfiltered_claim_serves_one_kind_only",
       "R60: an unfiltered claim is served through the two-level rule across both kinds",
       "for k in string.gmatch(ARGV[5], '[^,]+') do kinds[#kinds + 1] = k end",
       "for k in string.gmatch(ARGV[5], '[^,]+') do kinds[1] = k end",
       "test_q2_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog"),
    # --- dispatch kinds (R52, R55; point 13) --------------------------------
    _m("the_kind_filter_is_inverted",
       "R52: a preparation pool is never handed an inference candidate",
       "    if string.sub(flow, 1, #kind + 1) == kind .. '|' then",
       "    if string.sub(flow, 1, #kind + 1) ~= kind .. '|' then",
       "dur_outbox__a_candidate_carries_its_dispatch_kind"),
    _m("an_unknown_kind_is_swallowed",
       "R55: an unknown kind is a typed refusal, not an empty index",
       "        if kind is not None and kind not in DISPATCH_KINDS:",
       "        if False:",
       "test_q2_kind__an_unknown_kind_is_a_typed_refusal"),
    # --- replay safety and bounded memory (point 10) ------------------------
    _m("enqueue_reindexes_a_candidate_already_in_the_index",
       "a replayed outbox event indexes exactly one candidate",
       "if redis.call('HEXISTS', META, id) == 1 then return 0 end",
       "if false then return 0 end",
       "test_q2_replay__enqueue_is_replay_safe_across_pending_inflight_and_acknowledged",
       "dur_outbox__enqueue_is_replay_safe"),
    _m("enqueue_forgets_the_acknowledged_ids",
       "an acknowledged candidate is never indexed again",
       "if redis.call('SISMEMBER', ACKED, id) == 1 then return 0 end",
       "if false then return 0 end",
       "test_q2_replay__enqueue_is_replay_safe_across_pending_inflight_and_acknowledged",
       "dur_outbox__acknowledged_candidates_do_not_come_back"),
    _m("acknowledge_does_not_remember_the_candidate",
       "the acknowledgment is remembered, so a replayed outbox event cannot re-index it",
       "redis.call('SADD', ACKED, ARGV[1])",
       "redis.call('SCARD', ACKED, ARGV[1])",
       "test_q2_replay__enqueue_is_replay_safe_across_pending_inflight_and_acknowledged",
       "dur_outbox__acknowledged_candidates_do_not_come_back"),
    _m("the_item_cap_is_not_enforced",
       "a full index refuses with a typed retryable error instead of growing",
       "if redis.call('HLEN', META) + 1 > max_items then return -1 end",
       "if false then return -1 end",
       "test_q2_caps__a_full_index_refuses_with_a_typed_retryable_error"),
    _m("the_item_cap_is_off_by_one",
       "the cap is the number of candidates the index may HOLD, so the refusal comes "
       "before the write that would exceed it",
       "if redis.call('HLEN', META) + 1 > max_items then return -1 end",
       "if redis.call('HLEN', META) > max_items then return -1 end",
       "test_q2_caps__a_full_index_refuses_with_a_typed_retryable_error"),
    _m("the_item_cap_is_read_from_the_wrong_argument",
       "the caps come from the adapter's configuration, not from somewhere convenient",
       "local max_items, max_bytes = tonumber(ARGV[8]), tonumber(ARGV[9])",
       "local max_items, max_bytes = 1000000, tonumber(ARGV[9])",
       "test_q2_caps__a_full_index_refuses_with_a_typed_retryable_error"),
    _m("the_byte_cap_is_not_enforced",
       "queued bytes are capped independently of the item count",
       "if bytes + size > max_bytes then return -2 end",
       "if false then return -2 end",
       "test_q2_caps__queued_bytes_are_counted_per_candidate_and_returned"),
    _m("the_byte_charge_is_not_the_candidates_compact_bytes",
       "the byte cap charges what the candidate actually costs",
       "local size = #payload\nif redis.call('HLEN', META) + 1 > max_items",
       "local size = 1\nif redis.call('HLEN', META) + 1 > max_items",
       "test_q2_caps__queued_bytes_are_counted_per_candidate_and_returned"),
    _m("bytes_are_never_returned",
       "byte accounting is symmetric: what enqueue charges, leaving gives back",
       "  redis.call('HINCRBY', COUNTERS, 'bytes', int_text(-e.size))",
       "  redis.call('HSTRLEN', COUNTERS, 'bytes', int_text(-e.size))",
       "test_q2_caps__queued_bytes_are_counted_per_candidate_and_returned"),
    _m("the_index_is_not_namespaced",
       "each index owns its keys, so two indices in one server cannot corrupt each "
       "other's capacity truth",
       '        self._keys = [f"{namespace}:{name}" for name in',
       '        self._keys = [f"shared:{name}" for name in',
       "test_q2_isolation__two_namespaces_do_not_see_each_other"),
    # --- cancellation (point 11) --------------------------------------------
    _m("cancellation_keeps_the_candidates",
       "cancellation removes every candidate of the job, pending or in flight",
       "  if f[6] == job then dropped = dropped + forget(all[i]) end",
       "  if false then dropped = dropped + forget(all[i]) end",
       "test_q2_cancel__removal_drops_pending_and_in_flight_candidates_and_their_flow"),
    _m("cancellation_matches_on_the_wrong_field",
       "cancellation is by job id: matching anything else would cancel a stranger's work",
       "  if f[6] == job then dropped = dropped + forget(all[i]) end",
       "  if f[5] == job then dropped = dropped + forget(all[i]) end",
       "test_q2_cancel__removal_drops_pending_and_in_flight_candidates_and_their_flow"),
    _m("cancellation_makes_the_index_authoritative",
       "the index never becomes the authority on cancellation: a replayed dispatch event "
       "may be re-indexed, and `JobStore.claim` refuses it",
       "  if f[6] == job then dropped = dropped + forget(all[i]) end",
       "  if f[6] == job then dropped = dropped + forget(all[i])\n"
       "    redis.call('SADD', ACKED, all[i]) end",
       "test_q2_cancel__a_cancelled_candidate_may_be_re_indexed"),
    _m("an_empty_tenant_keeps_its_fairness_state",
       "a flow is deleted when its pending AND in-flight count reaches zero",
       "  if redis.call('HINCRBY', REFS, e.flow, -1) <= 0 then",
       "  if redis.call('HINCRBY', REFS, e.flow, -1) < 0 then",
       "test_q2_cancel__removal_drops_pending_and_in_flight_candidates_and_their_flow"),
    _m("a_flow_is_deleted_while_it_still_holds_work",
       "the reference count covers pending AND in-flight candidates, so a claim does not "
       "erase the tenant's fairness state",
       "if redis.call('HINCRBY', REFS, flow, 1) == 1 then\n"
       "  -- An arriving flow's tag is ITS OWN kind's virtual time",
       "if true then\n"
       "  -- An arriving flow's tag is ITS OWN kind's virtual time",
       "test_q2_kind__preparation_and_inference_are_separately_fair"),
    # --- rebuild (point 12) -------------------------------------------------
    _m("rebuild_indexes_a_duplicate_twice",
       "a duplicate in the snapshot is indexed, handed out and charged exactly once",
       "  if redis.call('HEXISTS', META, id) == 0 then",
       "  if true then",
       "test_q2_rebuild__from_a_postgresql_snapshot_with_duplicates"),
    _m("rebuild_does_not_charge_bytes",
       "a rebuilt index charges exactly the compact bytes of its snapshot",
       "    redis.call('HINCRBY', COUNTERS, 'bytes', int_text(size))\n"
       "    local flow = kind .. '|' .. org",
       "    local flow = kind .. '|' .. org",
       "test_q2_rebuild__from_a_postgresql_snapshot_with_duplicates"),
    _m("rebuild_keeps_the_stale_pending_candidates",
       "the snapshot REPLACES the index, so a candidate PostgreSQL no longer reports is "
       "gone rather than dispatched for ever",
       "for i = 1, #flows do redis.call('DEL', pending_key(flows[i])) end",
       "for i = 1, #flows do redis.call('EXISTS', pending_key(flows[i])) end",
       "test_q2_rebuild__clears_in_flight_acknowledged_and_both_fairness_levels"),
    _m("rebuild_keeps_the_in_flight_entries",
       "a pre-rebuild claim was only a hint: in-flight entries are dropped",
       "redis.call('DEL', META, PAYLOAD, ACKED, INFLIGHT, TAGS, CLOCK, REFS, COUNTERS)",
       "redis.call('DEL', META, PAYLOAD, ACKED, TAGS, CLOCK, REFS, COUNTERS)",
       "test_q2_rebuild__clears_in_flight_acknowledged_and_both_fairness_levels"),
    _m("rebuild_keeps_the_acknowledged_ids",
       "rebuild clears the acknowledged ids, so a requeued job is indexable again",
       "redis.call('DEL', META, PAYLOAD, ACKED, INFLIGHT, TAGS, CLOCK, REFS, COUNTERS)",
       "redis.call('DEL', META, PAYLOAD, INFLIGHT, TAGS, CLOCK, REFS, COUNTERS)",
       "test_q2_rebuild__clears_in_flight_acknowledged_and_both_fairness_levels"),
    _m("rebuild_keeps_the_fairness_epoch",
       "R60: a rebuild restarts BOTH fairness levels, so no tenant and no kind inherits "
       "a penalty from an index that no longer exists",
       "redis.call('DEL', META, PAYLOAD, ACKED, INFLIGHT, TAGS, CLOCK, REFS, COUNTERS)",
       "redis.call('DEL', META, PAYLOAD, ACKED, INFLIGHT, TAGS, REFS, COUNTERS)",
       "test_q2_rebuild__clears_in_flight_acknowledged_and_both_fairness_levels"),
    _m("rebuild_does_not_restart_the_counters",
       "a rebuild starts a fresh accounting epoch: the arrival sequence and the byte "
       "total restart, so the recovered index's accounting is its snapshot's",
       "redis.call('DEL', META, PAYLOAD, ACKED, INFLIGHT, TAGS, CLOCK, REFS, COUNTERS)",
       "redis.call('DEL', META, PAYLOAD, ACKED, INFLIGHT, TAGS, CLOCK, REFS)",
       "test_q2_rebuild__clears_in_flight_acknowledged_and_both_fairness_levels"),
    _m("rebuild_does_not_count_what_it_indexed",
       "rebuild answers with the number of candidates indexed, which is what the "
       "reconciler logs and alerts on",
       "return redis.call('HLEN', META)",
       "return redis.call('SCARD', ACKED)",
       "test_q2_rebuild__from_a_postgresql_snapshot_with_duplicates"),
    # --- observability -------------------------------------------------------
    _m("depth_is_not_reported_per_kind",
       "index depth is published per dispatch kind",
       "  depth[f[4]] = (depth[f[4]] or 0) + 1",
       "  depth[f[4]] = (depth[f[4]] or 0) + 0",
       "test_q2_stats__depth_and_waiting_age_are_reported_without_claiming_capacity"),
    _m("stats_counts_an_in_flight_candidate_as_waiting",
       "the waiting age is measured over pending candidates: an in-flight one is not "
       "waiting, it is being worked on",
       "  if not held[all[i]] then",
       "  if true then",
       "test_q2_stats__depth_and_waiting_age_are_reported_without_claiming_capacity"),
    _m("the_waiting_age_ignores_availability",
       "a candidate that is not available yet has not started waiting",
       "    if avail <= now and (oldest == nil or avail < oldest) then oldest = avail end",
       "    if (oldest == nil or avail < oldest) then oldest = avail end",
       "test_q2_stats__depth_and_waiting_age_are_reported_without_claiming_capacity"),
    _m("the_waiting_age_is_reported_in_microseconds",
       "`oldest_wait_s` is seconds, as every other duration in the contracts is",
       "if oldest ~= nil then wait = (now - oldest) / 1000000 end",
       "if oldest ~= nil then wait = (now - oldest) end",
       "test_q2_stats__depth_and_waiting_age_are_reported_without_claiming_capacity"),
    _m("the_state_snapshot_expires_visibilities",
       "expiry is evaluated lazily by a CLAIM: `stats()` still counts an entry whose "
       "visibility has run out until the next claim happens",
       "local inflight = redis.call('ZRANGE', INFLIGHT, 0, -1)",
       "local inflight = redis.call('ZRANGEBYSCORE', INFLIGHT, '(' .. int_text(now), '+inf')",
       "test_q2_kind__visibility_expires_at_the_ttl_and_on_its_pools_lease"),
    # --- shape ---------------------------------------------------------------
    _m("the_scores_are_formatted_with_lua_default_precision",
       "a ZSET score is a double and is written with all 17 digits it needs: Lua 5.1's "
       "own number formatting is %.14g and would round every fairness tag",
       "local function score_text(x) return string.format('%.17g', x) end",
       "local function score_text(x) return string.format('%.14g', x) end",
       "test_q2_differential__the_two_adapters_agree_on_every_operation"),
    _m("instants_lose_their_microseconds",
       "an instant crosses into Lua as exact integer microseconds - `datetime`'s own "
       "resolution - so the availability and TTL boundaries compare as `datetime` does",
       "    return (moment - _EPOCH) // _MICROSECOND",
       "    return int(moment.timestamp()) * 1_000_000",
       "test_q2_stats__a_candidate_is_not_offered_before_it_is_available"),
    _m("a_port_method_is_not_the_one_the_port_declares",
       "the adapter satisfies `ports.Scheduler`: every method the port declares exists "
       "under its own name and is a coroutine function",
       "    async def acknowledge(self, event: IndexEvent) -> None:",
       "    async def acknowledge_candidate(self, event: IndexEvent) -> None:",
       "test_q2_contract__the_adapter_satisfies_the_scheduler_protocol"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="run the Q2 mutation list")
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
