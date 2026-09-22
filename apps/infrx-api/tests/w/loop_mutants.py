#!/usr/bin/env python3
"""r1 R32 for W2: every invariant `tests/w/test_loop.py` claims must be killable.

One entry is one single edit to `infrx/worker/` that breaks one named invariant, with
the cases that must fail because of it. The machinery is `tests/w/mutants.py`'s - the
same runner, the same kill rule (a kill is an assertion or a `pytest.raises` that did
not raise; a `NameError` is a broken copy, not a proof) - with this file as the pytest
target.

    uv run --frozen pytest -q tests/w/test_loop_mutants.py          # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_loop_mutants.py
    uv run --frozen python tests/w/loop_mutants.py --list
"""
from __future__ import annotations

import argparse
import sys

from .mutants import Mutant, Outcome, run_mutant as _run_mutant

SUITE = ("tests/w/test_loop.py",)

A = "worker/attempt.py"
L = "worker/loop.py"
E = "worker/engine.py"


def _m(name, invariant, file, old, new, *cases, allowed_errors=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  allowed_errors=tuple(allowed_errors))


def run(mutant: Mutant):
    return _run_mutant(mutant, SUITE)


# Case names as constants: a typo is a `NameError` here rather than a mutant that cannot
# die.
HAPPY = "test_dur_output__the_answer_is_journalled_visible_only_then_relayed_and_settled"
WRITE_FAILED = ("test_dur_output__a_failed_journal_write_relays_nothing_and_settles_the_"
                "write_failure")
UNCONFIRMED = ("test_dur_output__an_unconfirmed_journal_write_is_never_relayed_though_the_"
               "journal_took_it")
RECONCILE = "test_dur_settle__published_output_with_an_unknown_count_waits_for_reconciliation"
BATCH = "test_dur_output__a_batch_is_bounded_by_the_clock_and_by_its_own_size"
NO_VISIBLE = "test_dur_output__a_delta_without_visible_text_is_refused_not_relayed"
TERMINAL = "test_dur_output__a_worker_never_journals_a_terminal_event"
SUPERSEDED = "test_dur_fence__a_superseded_generation_appends_nothing_and_settles_nothing"
EXPIRED = "test_dur_fence__a_stale_worker_cannot_append_after_its_lease_expired"
DUPLICATE = "test_dur_fence__two_workers_claiming_one_job_produce_one_attempt"
PREPARATION = "test_dur_fence__a_preparation_lease_cannot_execute_an_attempt"
LOST_FENCE = "test_dur_fence__a_lost_fence_never_cancels_another_generation"
HEARTBEAT = ("test_dur_fence__the_heartbeat_renews_inside_the_lease_and_is_what_a_silent_"
             "stream_polls")
USAGE = ("test_dur_settle__usage_reaches_the_store_only_when_the_stream_says_it_is_"
         "authoritative")
TWO_USAGE = "test_dur_settle__two_usage_events_make_the_count_unknown"
BILLABLE = "test_dur_settle__only_completed_and_cancellation_are_billable"
DROPPED = "test_dur_settle__a_finished_stream_with_a_dropped_line_is_not_a_billable_success"
ACK_LOST = "test_dur_settle__a_lost_terminal_acknowledgment_settles_exactly_once"
RESULT = "test_dur_settle__a_success_the_customer_cannot_fetch_is_not_a_success"
USAGE_WITH_STALL = "test_dur_settle__a_usage_event_can_arrive_with_a_stall_or_a_cancellation"
CANCEL_PHASES = "test_dur_fence__a_cancellation_is_honoured_in_every_phase"
DEADLINE_PHASES = "test_dur_fence__a_deadline_is_enforced_in_every_phase"
SILENT = "test_dur_fence__a_silent_engine_is_bounded_by_the_attempts_own_deadline"
CANCEL_REFUSED = "test_dur_settle__an_engine_cancel_that_is_refused_still_stops_the_task"
REFUSED = ("test_dur_settle__a_request_the_engine_cannot_accept_settles_free_and_runs_"
           "nothing")
CLAIM_ALL = "test_ops_recover__the_loop_claims_acknowledges_and_settles_every_candidate"
LOSER = "test_ops_recover__a_candidate_whose_claim_loses_is_still_acknowledged"
NOT_RESUMED = "test_ops_recover__a_lease_held_by_a_dead_worker_is_never_resumed"
DRAIN_RELEASE = "test_ops_recover__a_drain_stops_claiming_and_releases_what_it_cannot_finish"
DRAIN_WAIT = "test_ops_recover__a_drain_that_can_wait_lets_the_attempt_finish"
FAKE_ENGINE = "test_ops_recover__the_shared_fake_engine_drives_the_same_loop"
RELAY_FAIL = "test_dur_output__a_relay_that_fails_never_fails_a_committed_attempt"
MEDIA_FILE = ("test_api_stream__a_prepared_video_reaches_the_engine_as_a_local_file_of_its_"
              "own_tenant")
EOS = "test_api_stream__both_eos_ids_are_supplied_on_every_request"
TRUST = "test_dur_settle__an_adapters_completed_is_not_taken_on_trust"
CERTAINTY = "test_dur_settle__a_usage_record_that_is_not_authoritative_is_unknown"
CANCEL_AT_COMPLETE = ("test_gap__a_cancellation_that_lands_between_the_last_append_and_"
                      "complete_settles_nothing")
STALE_COMPLETE = "test_gap__a_stale_complete_settles_nothing"
CLAMP = "test_gap__the_task_deadline_is_the_clamped_instant_not_the_generation_budget"
RECORDING_RELAY = "test_gap__the_relay_never_receives_anything_the_journal_has_not_taken"

MUTANTS: tuple[Mutant, ...] = (
    # --- r1 R58: the journal carries `visible`, and only what committed is relayed ----
    _m("journal_carries_the_raw_text", "the journal and the relay carry `visible` only (R58)",
       A, '        return EngineEvent(type=ChunkEventType.delta, payload={"visible": visible})',
       "        return event", HAPPY, NO_VISIBLE),
    _m("missing_visible_falls_back_to_content",
       "a delta with no `visible` is a breach, never `content` (R58, F2R item 2)",
       A, '        visible = event.payload.get("visible")',
       '        visible = event.payload.get("visible", event.payload.get("content"))',
       NO_VISIBLE),
    _m("empty_visible_journalled", "a delta with nothing for the customer is not journalled",
       A, "        if not visible:", "        if False:", HAPPY),
    _m("relay_without_a_commit", "only committed chunks are relayed (02 §6)",
       A, "            chunks = await self._fenced(self.stream.append(state.lease, events), "
          "state, result)",
       "            chunks = events", HAPPY, WRITE_FAILED),
    _m("relay_before_persist", "persist before relay: nothing reaches the relay uncommitted",
       A, "            chunks = await self._fenced(self.stream.append(state.lease, events), "
          "state, result)\n",
       "            await self._relay(result, events)\n"
       "            chunks = await self._fenced(self.stream.append(state.lease, events), "
       "state, result)\n", HAPPY, RECORDING_RELAY),
    _m("stale_fence_wins_on_complete", "a refused complete (stale or terminal) settles nothing",
       A, "            result.cancelled = result.cancelled or isinstance(refused, "
          "errors.AlreadyTerminal)\n            return result",
       "            result.cancelled = result.cancelled or isinstance(refused, "
       "errors.AlreadyTerminal)\n            result.outcome, result.cause = outcome, cause\n"
       "            return result", CANCEL_AT_COMPLETE, STALE_COMPLETE),
    _m("deadline_clamp_ignored", "R29: the task bound is the clamped instant, not the budget",
       A, "        return max(0.0, (lease.generation_deadline_at - self.clock.now())"
          ".total_seconds())",
       "        return float(self.limits.generation_timeout_s)", CLAMP,
       # with the 300 s budget the case's own 2 s bound reports it
       allowed_errors=("TimeoutError",)),
    _m("write_failure_is_a_success", "a journal write that failed is not a completed answer",
       A, "            cause = TerminalCause.journal_write_failed",
       "            cause = None", WRITE_FAILED, UNCONFIRMED),
    _m("unconfirmed_write_relayed", "a write we cannot prove is never relayed",
       A, '            raise _JournalFailed(f"{type(failed).__name__}: {failed}") from None',
       "            chunks = events\n            state.batch.clear()\n"
       "            await self._relay(result, chunks)\n            return", UNCONFIRMED),
    _m("terminal_event_journalled", "a worker never appends a terminal event (r1 R30)",
       A, "        if event.type is ChunkEventType.terminal:", "        if False:",
       TERMINAL),
    _m("batch_size_unbounded", "a burst inside one clock tick is still committed in pieces",
       A, "        if len(state.batch) >= BATCH_MAX_EVENTS:", "        if False:", BATCH),
    _m("batch_time_ignored", "a batch is committed within `stream_batch_ms` (02 §6)",
       A, "        return elapsed_ms >= self.limits.stream_batch_ms", "        return False",
       BATCH),
    _m("relay_failure_fails_the_attempt",
       "a delivery failure never fails an attempt whose output is committed",
       A, '            result.detail = f"relay failed: {type(failure).__name__}: {failure}"\n'
          "            return",
       "            raise", RELAY_FAIL),

    # --- fencing (r1 R29/R46) ---------------------------------------------------------
    _m("append_is_not_fenced", "every store mutation carries the lease",
       A, "            chunks = await self._fenced(self.stream.append(state.lease, events), "
          "state, result)",
       "            chunks = await self._fenced(\n"
       "                self.stream.append(state.lease.model_copy(update={'generation': 1}), "
       "events), state, result)",
       SUPERSEDED),
    _m("stale_lease_not_reported", "a lost fence is reported as a lost fence",
       A, '            result.refusal = lost.code\n            result.detail = str(lost)\n'
          "            return result",
       '            result.refusal = "unknown"\n            result.detail = str(lost)\n'
       "            return result",
       EXPIRED),
    _m("lease_loss_cancels_the_engine",
       "r1 R58: a lost fence never calls `Engine.cancel` - the generation it names is gone",
       A, "        except errors.AlreadyTerminal as settled_elsewhere:\n"
          "            await self._cancel_engine(state, result)",
       "        except (errors.AlreadyTerminal, errors.StaleLease) as settled_elsewhere:\n"
       "            await self._cancel_engine(state, result)",
       EXPIRED),
    _m("cancel_with_a_foreign_generation",
       "r1 R58: cancellation intents are keyed by (job_id, generation)",
       A, "            result.engine_cancel = await self.engine.cancel(state.lease)",
       "            result.engine_cancel = await self.engine.cancel(\n"
       "                state.lease.model_copy(update={'generation': "
       "state.lease.generation + 1}))",
       CANCEL_PHASES),
    _m("cancel_answer_assumed", "`cancel` answering False is recorded, not assumed away",
       A, "            result.engine_cancel = await self.engine.cancel(state.lease)",
       "            result.engine_cancel = True\n            await self.engine.cancel(state.lease)",
       CANCEL_REFUSED),
    _m("heartbeat_never_renews", "the lease is renewed inside its TTL (r1 R29)",
       A, "        if (self.clock.now() - state.last_renew).total_seconds() < "
          "self.limits.lease_heartbeat_s:",
       "        if True:", HEARTBEAT),
    _m("preparation_lease_executes", "r1 R46: a preparation lease fences M's work, not ours",
       A, "        if lease.kind is not LeaseKind.inference:", "        if False:",
       PREPARATION),
    _m("claim_refusal_becomes_an_attempt", "a claim that lost runs nothing",
       A, "        except errors.DomainError as refused:\n"
          "            return AttemptResult(job_id=job_id, refusal=refused.code, "
          "detail=str(refused))",
       "        except errors.DomainError as refused:\n"
          "            return AttemptResult(job_id=job_id, detail=str(refused))",
       DUPLICATE, DEADLINE_PHASES),
    _m("task_deadline_removed",
       "the whole attempt is bounded by the lease's persisted generation instant",
       A, "                async with asyncio.timeout(self._remaining(state.lease)):",
       "                async with asyncio.timeout(None):", SILENT,
       # A loop with no bound never returns, so the case's own `wait_for` is what fails:
       # a declared kill mode, and the only one in this list.
       allowed_errors=("TimeoutError",)),
    _m("deadline_is_not_the_leases", "the bound is the store's instant, not a constant",
       A, "        return max(0.0, (lease.generation_deadline_at - "
          "self.clock.now()).total_seconds())",
       "        return 0.0", SILENT),

    # --- exact accounting (r1 R21/R30/R58) -------------------------------------------
    _m("unknown_usage_settled_as_known",
       "r1 R58: only a usage event that says it is authoritative reaches `complete`",
       A, "        if event.usage is None or event.usage.certainty is not "
          "UsageCertainty.authoritative:",
       "        if event.usage is None:", CERTAINTY),
    _m("unknown_usage_reason_dropped",
       "an unknown count carries the reason the engine gave, for reconciliation (02)",
       A, '        result.usage_unknown_reason = str(reason) if reason else "unknown"',
       "        result.usage_unknown_reason = None", USAGE, CERTAINTY),
    _m("untyped_failure_completes",
       "nothing untyped becomes a settlement: an engine that crashed did not finish",
       A, "        except Exception as failure:                # nothing untyped becomes a "
          "settlement\n            cause = TerminalCause.platform_error",
       "        except Exception as failure:                # nothing untyped becomes a "
       "settlement\n            cause = TerminalCause.completed",
       FAKE_ENGINE),
    _m("two_usage_events_believed", "R58 allows one usage event; two is a contradiction",
       A, "        if state.usage_events > 1:", "        if False:", TWO_USAGE),
    _m("completed_taken_on_trust",
       "`terminal_cause` is advisory: the worker re-checks what `completed` needs",
       A, "        if cause is TerminalCause.completed and not self._whole(state, stream):",
       "        if False:", TRUST),
    _m("completed_without_a_usage_count", "`completed` needs authoritative usage (r1 R21)",
       A, "        if state.usage is None:\n            return False",
       "        if state.usage is None:\n            return True", TRUST),
    _m("a_dropped_line_still_completes", "a line we could not read is content we may have lost",
       A, '        if getattr(stream, "malformed_lines", 0):\n            return False',
       '        if getattr(stream, "malformed_lines", 0):\n            return True', TRUST),
    _m("any_finish_reason_completes", "only `stop` and `length` are completions",
       A, "        return finish is None or finish in FINISHED_REASONS", "        return True",
       TRUST),
    _m("adapter_report_ignored",
       "an adapter that reports how its stream ended is believed over our own derivation",
       A, "        cause = hint if isinstance(hint, TerminalCause) else "
          "self._derived_cause(state)",
       "        cause = self._derived_cause(state)", BILLABLE),
    _m("stale_lease_reported_as_a_cancellation",
       "a lost fence is not a cancellation: the job is another generation's, not the "
       "customer's",
       A, "            result.cancelled = isinstance(refused, errors.AlreadyTerminal)",
       "            result.cancelled = True", LOST_FENCE),
    _m("derived_cause_needs_no_usage",
       "with no adapter hint, an authoritative count arriving last is the completion signal",
       A, "        if state.usage is not None and state.last_event is ChunkEventType.usage:",
       "        if state.last_event is ChunkEventType.usage or True:", TWO_USAGE),
    _m("succeeded_state_for_every_cause", "the (cause, state) pair is part of the contract",
       A, "    if cause is TerminalCause.completed:\n        return JobState.succeeded",
       "    if cause is TerminalCause.completed:\n        return JobState.failed", HAPPY,
       # the record itself refuses the pair (`records.CAUSE_STATES`), which is the kill
       allowed_errors=("ValidationError",)),
    _m("cancelled_state_is_failed", "a cancellation is `cancelled`, not `failed`",
       A, "    if cause is TerminalCause.client_cancelled:\n        return JobState.cancelled",
       "    if cause is TerminalCause.client_cancelled:\n        return JobState.failed",
       BILLABLE, allowed_errors=("ValidationError",)),
    _m("result_never_stored", "r1 R30: a success the customer cannot fetch is not a success",
       A, "        if cause is TerminalCause.completed:\n            try:\n"
          "                result_ref = await _maybe_await(self.put_result(state.lease.job_id,",
       "        if False:\n            try:\n"
       "                result_ref = await _maybe_await(self.put_result(state.lease.job_id,",
       HAPPY),
    _m("result_store_failure_still_completes",
       "a result object that could not be stored is not a completed answer",
       A, "                cause, result_ref = TerminalCause.platform_error, None",
       "                result_ref = None", RESULT),
    _m("terminal_ack_retried_with_a_new_outcome",
       "the identical completion replays; a different one is a second settlement",
       A, "                settled = await self.jobs.complete(state.lease, outcome)\n"
          "            except errors.DomainError as refused:",
       "                settled = await self.jobs.complete(\n"
       "                    state.lease, outcome.model_copy(update={'cause': "
       "TerminalCause.platform_error, 'state': JobState.failed, 'usage': None, "
       "'result_ref': None}))\n"
       "            except errors.DomainError as refused:",
       ACK_LOST),
    _m("prompt_tokens_guessed_from_the_ceiling",
       "preparation's exact count, never an estimate (02 forbids one)",
       A, "            prepared = prepared_request(work, await self._prompt_tokens(work),",
       "            prepared = prepared_request(work, work.request.max_input_tokens,",
       REFUSED),
    _m("media_refusal_charged_as_ours", "invalid media is free (02: invalid input is free)",
       A, "    if isinstance(refused, errors.UnsupportedMedia):\n"
          "        return TerminalCause.invalid_media",
       "    if False:\n        return TerminalCause.invalid_media", REFUSED),

    # --- the loop ---------------------------------------------------------------------
    _m("candidate_never_acknowledged", "every candidate this worker consumed is acknowledged",
       L, "            await self._acknowledge(candidate)", "            pass", CLAIM_ALL),
    _m("only_winners_acknowledged", "a candidate whose claim lost is consumed too",
       L, "            await self._acknowledge(candidate)",
       "            if result.settled:\n                await self._acknowledge(candidate)",
       LOSER),
    _m("the_loop_claims_any_kind",
       "r1 R46/R52: preparation is M's attempt sequence, on its own lease kind",
       L, "        candidate = await self.scheduler.claim_candidate(self.worker_id, "
          "kind=self.kind)",
       "        candidate = await self.scheduler.claim_candidate(self.worker_id)", CLAIM_ALL),
    _m("drain_keeps_claiming", "draining stops claiming",
       L, "        while not self.draining:", "        while True:", DRAIN_WAIT,
       # a loop that never stops claiming never ends, so the case's own bound reports it
       allowed_errors=("TimeoutError",)),
    _m("drain_bound_ignored", "an attempt that can finish inside the bound is not released",
       L, "            _, pending = await asyncio.wait(tasks, timeout=max(0.0, within_s))",
       "            _, pending = await asyncio.wait(tasks, timeout=0.0)", DRAIN_WAIT),
    _m("drain_does_not_release", "what is still running at the bound is released",
       L, "        for task in pending:\n            # Released, not settled: the store "
          "fences the lease and `recover` decides.\n            task.cancel()",
       "        for task in pending:\n            pass", DRAIN_RELEASE,
       # with nothing cancelled the blocked runner never ends, so the case's own bound
       # reports it
       allowed_errors=("TimeoutError",)),

    # --- the two S2M profile items in the adapter ------------------------------------
    _m("media_sent_as_a_bare_key", "S2M D3: the engine is handed a local file, not a key",
       E, '"video_url": {"url": local_media_url(ref, self.local_media_root, org_id,\n'
          "                                                           self.local_uri)}},",
       '"video_url": {"url": ref.storage_ref}},', MEDIA_FILE),
    _m("media_root_is_not_the_engines", "the root is the one the engine was started with",
       E, "local_media_url(ref, self.local_media_root, org_id,",
       "local_media_url(ref, LOCAL_MEDIA_ROOT, org_id,", MEDIA_FILE,
       # M2's path under the pinned root fails the default root's check: that is the kill
       allowed_errors=("NotFound",)),
    _m("media_path_unchecked", "R61: M2's path is checked, not trusted",
       E, "    if not _inside_tenant_root(path, root, org_id, ref):", "    if False:",
       MEDIA_FILE),
    _m("media_org_from_the_ref", "R61: the organization is the request's, not a carried field",
       E, "    if not _inside_tenant_root(path, root, org_id, ref):",
       "    if not _inside_tenant_root(path, root, ref.org_id, ref):", MEDIA_FILE),
    _m("media_path_any_tenant", "the path's organization segment must be the request's",
       E, "segments[:3] == [org_id, ref.profile_version, digest16]",
       "segments[:3] == [segments[0], ref.profile_version, digest16]", MEDIA_FILE),
    _m("media_path_any_profile", "R61 amended: two profile versions never share a file",
       E, "segments[:3] == [org_id, ref.profile_version, digest16]",
       "segments[:3] == [org_id, segments[1], digest16]", MEDIA_FILE),
    _m("media_path_any_object", "the path names this ref's own object",
       E, "segments[:3] == [org_id, ref.profile_version, digest16]",
       "segments[:3] == [org_id, ref.profile_version, segments[2]]", MEDIA_FILE),
    _m("media_file_name_unchecked", "the last segment is M2's file name",
       E, "            and LOCAL_MEDIA_FILE.fullmatch(segments[3]) is not None)", "            )",
       MEDIA_FILE),
    _m("media_depth_unchecked", "exactly four segments under the root",
       E, "return (len(segments) == 4 and segments", "return (segments", MEDIA_FILE),
    _m("media_scheme_unchecked", "only a file:// answer becomes a path",
       E, "    path = uri[len(LOCAL_MEDIA_SCHEME):] if isinstance(uri, str) \\\n"
          "        and uri.startswith(LOCAL_MEDIA_SCHEME) else \"\"",
       "    path = uri[len(LOCAL_MEDIA_SCHEME):] if isinstance(uri, str) else \"\"",
       MEDIA_FILE),
    _m("media_relative_root_accepted", "a relative root cannot be compared, so it is refused",
       E, '    if not root.startswith("/") or not path.startswith("/") or not org_id:',
       "    if not org_id:", MEDIA_FILE),
    _m("media_no_request_org_accepted", "a request with no organization owns no file",
       E, '    if not root.startswith("/") or not path.startswith("/") or not org_id:',
       '    if not root.startswith("/") or not path.startswith("/"):', MEDIA_FILE),
    _m("media_path_guessed_without_resolver", "no resolver is a typed refusal, not a guess",
       E, '        raise errors.DependencyUnavailable("no local media resolver is configured")',
       '        return f"{LOCAL_MEDIA_SCHEME}{root}/{ref.storage_ref}"', MEDIA_FILE),
    _m("only_one_eos_id", "S2M §1.2: both EOS ids, or answers run to the ceiling",
       E, "MODEL_EOS_TOKEN_IDS = (248044, 248046)", "MODEL_EOS_TOKEN_IDS = (248046,)", EOS),
    _m("eos_ids_not_sent", "the ids are re-supplied on every request, not left to the flag",
       E, '            "stop_token_ids": list(MODEL_EOS_TOKEN_IDS),',
       '            "n": 1,', EOS),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="run W2's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:42s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({case for m in MUTANTS for case in m.cases})} named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad: dict[str, list[str]] = {}
    for mutant in chosen:
        result = run(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}")
        if not result.killed:
            bad.setdefault(result.outcome.value, []).append(mutant.name)
    failures = sum(len(names) for names in bad.values())
    print(f"\n{len(chosen) - failures}/{len(chosen)} killed"
          + "".join(f"; {outcome}: {names}" for outcome, names in sorted(bad.items())))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
