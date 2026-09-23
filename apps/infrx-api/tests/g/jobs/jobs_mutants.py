#!/usr/bin/env python3
"""R32/R40/R83 for G3: one single-edit defect per invariant `tests/g/jobs` claims.

The list is G3's; the runner and its rules are the shared one (`tests/contracts/mutants.py`):
a mutant that does not compile is `broken_runner`, every failing test must be a named case,
every death must be an assertion or a typed `DomainError` unless the mutant declares it in
`dies_by`, the list's cases must pass unmutated first, and (G6B's stricter rule, kept) every
named case must notice.

One mutated file sits outside `infrx/` (`client_example.py`, which imports
`models/marlin2b/bench.py`), so a mutant's `file` is relative to `apps/infrx-api`
(`package=""`) and the copy keeps the repository shape (the G list's `_layout`).

    uv run --frozen pytest -q tests/g/jobs/test_jobs_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/jobs/test_jobs_mutants.py   # all
    uv run --frozen python -m tests.g.jobs.jobs_mutants --list
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE = "tests/g/jobs"

if str(API_DIR) not in sys.path:        # `python tests/g/jobs/jobs_mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401
from tests.g.mutants import _layout  # noqa: E402

J = "infrx/gateway/routes/jobs.py"
R = "infrx/gateway/routes/relay.py"      # G2's; G3's additive seams (admit, on_async, pump)
N = "infrx/gateway/routes/ingress.py"    # the route table
ST = "infrx/contracts/fakes/state.py"    # the contract store: idempotency is its rule
X = "client_example.py"

ACCEPT = "test_api_modes__respond_async_on_chat_answers_202_only_after_the_commit"
POST_JOBS = "test_api_modes__post_jobs_is_always_async_and_applies_no_preference"
LOST_202 = "test_dur_admit__a_lost_202_retried_with_its_key_answers_the_same_job"
CHANGED = "test_dur_admit__a_changed_payload_under_the_key_is_409_and_admits_nothing"
EXPIRED_KEY = "test_dur_admit__an_expired_mapping_is_410_and_never_a_new_billable_job"
TWICE = "test_dur_admit__two_concurrent_submissions_with_one_key_admit_once"
DETACHED = "test_api_modes__a_detached_202_never_cancels_its_job"
CREDIT_202 = "test_api_modes__a_credit_async_job_is_admitted_on_its_wallet"
NO_SURPRISE = "test_api_modes__plain_chat_is_never_a_surprise_202"
REPLAY_OUTAGE = "test_dur_admit__a_store_outage_answering_a_replay_leaves_the_job_for_the_retry"
TERMINAL_REPLAY = "test_dur_admit__a_terminal_async_replay_is_answered_by_lookup_without_fetching"
CRASH_REPLAY = "test_dur_admit__a_crash_after_the_admission_commit_is_completed_by_the_async_retry"
INFLIGHT_403 = "test_dur_admit__an_in_flight_async_replay_prepares_nothing_when_the_host_fails"
INFLIGHT_CREDIT = "test_dur_admit__an_in_flight_credit_replay_is_never_rechecked_or_cancelled"
MODES_409 = "test_dur_admit__a_key_reused_across_modes_is_409_and_the_job_runs_on"
STATUS = "test_api_modes__status_reports_the_committed_row_and_result_availability"
OUTLIVES = "test_api_modes__status_outlives_the_result_and_the_journal"
NO_USAGE = "test_api_modes__a_success_without_usage_reports_none_and_no_result"
ONE_404 = "test_dur_rls__a_malformed_unknown_or_foreign_handle_is_one_404"
OPERATOR = "test_dur_rls__an_operator_key_owns_no_job"
READ_OUTAGE = "test_api_modes__a_store_outage_on_a_handle_read_is_a_retryable_503"
PROVIDER_DEV = "test_dur_rls__a_provider_dev_key_owns_its_own_jobs"
RESULT = "test_api_modes__the_result_is_served_only_after_the_terminal_commit"
FAILURES = "test_api_modes__a_failed_cancelled_or_expired_job_is_a_result_not_an_error"
STORE_CLOCK = "test_api_modes__result_expiry_is_judged_on_the_store_clock"
EVENTS = "test_api_modes__events_replay_the_committed_journal_from_the_cursor"
CURSOR = "test_api_modes__a_malformed_or_forged_cursor_is_400_before_any_read"
GAP = "test_api_modes__a_replay_gap_or_an_expired_journal_is_an_explicit_410"
OBSERVER = "test_api_modes__an_observer_that_leaves_never_cancels_the_job"
UNSTARTED = "test_api_modes__an_unstarted_job_streams_its_identity_then_waits"
OBSERVER_FAILS = "test_api_modes__an_observer_whose_stream_fails_never_cancels_the_job"
OBSERVER_STOPPED = "test_api_modes__an_observer_stopped_from_outside_never_cancels_the_job"
CREDIT_EVENTS = "test_api_modes__a_credit_job_replays_its_events_in_its_committed_phase"
DELETE = "test_dur_fence__delete_cancels_durably_and_answers_the_committed_outcome"
RACE = "test_dur_fence__a_delete_racing_a_completion_settles_once"
MIDWAY = "test_dur_fence__a_delete_cancelled_midway_still_cancels_the_job"
BODY = "test_api_modes__a_delete_with_a_body_is_refused_and_cancels_nothing"
DELETE_OUTAGE = "test_dur_fence__a_delete_whose_cancel_fails_is_retryable_never_a_200"
MATRIX = "test_api_modes__the_async_matrix_end_to_end"
EXPIRED_Q = "test_api_modes__a_job_that_expires_in_the_queue_is_an_expired_result"
CLIENT = "test_api_modes__the_client_examples_async_flow_is_served"
COMPOSE = "test_f_base__the_jobs_router_mounts_only_over_a_relay"
TABLE = "test_f_base__each_jobs_route_has_one_handler_and_it_is_the_jobs_routers"
PILOT = "test_f_base__the_pilot_composition_carries_the_relay_the_jobs_router_needs"


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    # === item 1: acceptance and the 202 (API-MODES, DUR-ADMIT) ===========================
    _m("preference_not_echoed", "Preference-Applied: respond-async when the preference was used",
       J, "            **headers, wire.HEADER_PREFERENCE_APPLIED: wire.PREFER_RESPOND_ASYNC,",
       "            **headers,", ACCEPT),
    _m("location_dropped", "the 202 names its status resource (Location)",
       J, '            HEADER_LOCATION: f"{JOBS_PATH}/{job.handle}",\n', "", ACCEPT, POST_JOBS),
    _m("retry_after_dropped", "the 202 says when to poll (Retry-After)",
       J, "            wire.HEADER_RETRY_AFTER: str(POLL_AFTER_S)})", "            })", ACCEPT),
    _m("hook_not_installed", "mounting the jobs router installs the 202 hook",
       J, "    relay.on_async = jobs.accepted\n", "", ACCEPT, MATRIX, PILOT),
    _m("staged_refs_never_attached", "the 202 follows the attach of the staged refs",
       R, "                await _dependency(self.media.attach(job.request_id, refs))",
       "                pass", ACCEPT),
    _m("async_hook_ignores_regime", "the async path admits by regime (CREDIT on its wallet)",
       R, "        admit = self.jobs.admit_credit(prepared, idem) if self.regime == CREDIT \\",
       "        admit = self.jobs.admit_credit(prepared, idem) if False \\", CREDIT_202),
    _m("accepted_state_invented", "the 202's state is the committed one, never a constant",
       J, "                                state=self.state_of(admission, outcome),",
       "                                state=JobState.preparing,", LOST_202),
    _m("replay_state_stale", "a replay answers the original acceptance as it stands now",
       J, "        if replayed:\n            # The original acceptance",
       "        if False:\n            # The original acceptance", CREDIT_202),
    _m("replay_flag_dropped", "a replay says so in the body (idempotency_replayed)",
       J, "                                idempotency_replayed=replayed)",
       "                                idempotency_replayed=False)", LOST_202, TWICE),
    _m("deadline_from_the_gateway", "the 202's deadline is the stored one (legacy)",
       J, "            return admission.deadline_at", "            return job.bound", ACCEPT),
    _m("credit_deadline_ignores_the_caller", "a CREDIT deadline is the store's rule, both terms",
       J, "        return min(job.bound - timedelta(seconds=self.relay.grace_s),",
       "        return max(job.bound - timedelta(seconds=self.relay.grace_s),", CREDIT_202),
    _m("credit_replay_deadline_from_the_retry", "a CREDIT replay's deadline is not the retry's",
       J, "        if replayed:\n            return ceiling", "        if False:\n            return ceiling",
       CREDIT_202),
    _m("jobs_route_follows_prefer", "POST /v1/jobs is async whatever Prefer says",
       J, "await jobs.ingress.validated(_as_async(request), request_id)",
       "await jobs.ingress.validated(request, request_id)", POST_JOBS),
    _m("preference_applied_always", "POST /v1/jobs reports no preference applied",
       J, "        del answer.headers[wire.HEADER_PREFERENCE_APPLIED]\n", "", POST_JOBS),
    _m("detach_cancels", "the 202 path never cancels the job it accepted (the sync path does)",
       J, "        replayed, outcome = admission.replayed, None",
       "        await self.relay.cancel(job.org_id, job.handle, quiet=True)\n"
       "        replayed, outcome = admission.replayed, None", DETACHED),
    _m("surprise_202", "chat without respond-async is never answered 202",
       R, "        if request.execution_mode is ExecutionMode.async_:",
       "        if request.execution_mode is not ExecutionMode.stream:", NO_SURPRISE),
    _m("async_acceptance_uncounted", "an async acceptance is counted by mode (I3B request 3)",
       R, '            self._count("infrx_jobs_accepted_total", mode=request.execution_mode,\n'
          "                        tenant=auth.org_id)", "            pass", ACCEPT),
    _m("replay_outage_is_a_500", "a store outage answering a replay is a retryable 503",
       J, "            admission, outcome = await _dependency(self.relay._owned(job.org_id, "
          "job.handle))",
       "            admission, outcome = await self.relay._owned(job.org_id, job.handle)",
       REPLAY_OUTAGE),
    _m("lookup_skipped", "R91: a keyed request is answered from the lookup before preparation",
       R, "        found = await self._lookup(auth.org_id, idem)", "        found = None",
       LOST_202, TERMINAL_REPLAY),
    _m("inflight_replay_not_completed", "the retry of an acceptance cut short completes it",
       R, "        elif found[1] is None:", "        elif False:", CRASH_REPLAY),
    # Review ADM-R2-B1: the same single edits as G2's `inflight_replay_prepares` and
    # `inflight_replay_rechecked_after_attach` (the G list), here naming the 202 path's cases.
    _m("inflight_async_replay_prepares", "an in-flight async replay fetches and stages nothing",
       R, "        if found is None:\n            # Media is fetched and staged only",
       "        if found is None or found[1] is None:\n            # Media is fetched and staged only",
       INFLIGHT_403, INFLIGHT_CREDIT),
    _m("inflight_async_replay_rechecked", "an async replay never rechecks or cancels a running job",
       R, "        if job.request_id in self.media.by_job:", "        if False:", INFLIGHT_CREDIT),
    _m("mode_left_out_of_the_digest", "R94: a key reused across sync and async is 409",
       N, "    if request.execution_mode is not ExecutionMode.async_:\n        return request.payload_digest",
       "    if True:\n        return request.payload_digest", MODES_409),
    _m("replay_readmits", "a key the lookup missed is still one job: admission replays it",
       ST, "            replay = self._replay(idem, now, credit=credit)",
       "            replay = None", TWICE),
    _m("changed_payload_replayed", "a changed payload under the key is 409, never a replay",
       ST, "        if record.payload_hash != idem.payload_hash:", "        if False:", CHANGED),
    _m("expired_mapping_readmits", "an expired mapping is 410, never a new billable job",
       ST, '            raise errors.IdempotencyExpired(f"idempotency key expired at '
           '{record.expires_at}")',
       "            return None", EXPIRED_KEY),
    # === item 2: status (API-MODES, DUR-RLS) ============================================
    _m("handle_grammar_unchecked", "a malformed handle is refused before any store read",
       J, "        if auth.audience not in OWNERS or not ids.JOB_HANDLE_RE.fullmatch(handle):",
       "        if auth.audience not in OWNERS:", ONE_404),
    _m("operator_owns_jobs", "an operator credential owns no job (R33/R66)",
       J, "        if auth.audience not in OWNERS or not ids.JOB_HANDLE_RE.fullmatch(handle):",
       "        if not ids.JOB_HANDLE_RE.fullmatch(handle):", OPERATOR),
    _m("provider_dev_owns_none", "a provider-dev key owns the jobs it submits",
       J, "OWNERS = frozenset({CredentialAudience.consumer, CredentialAudience.provider_dev})",
       "OWNERS = frozenset({CredentialAudience.consumer})", PROVIDER_DEV),
    _m("status_leaks_foreign", "a foreign handle is the unknown handle's 404 (the store's check)",
       ST, "        if job is None or job.request.org_id != org_id:", "        if job is None:",
       ONE_404),
    _m("owned_read_outage_is_a_500", "an owned read the store cannot answer is a retryable 503",
       J, "        return await _dependency(self.relay._owned(org, handle))",
       "        return await self.relay._owned(org, handle)", READ_OUTAGE),
    _m("probe_outage_is_a_500", "the events' journal probe failing is a retryable 503",
       J, "        await _dependency(relay.stream.read_owned(org, handle, cursor, 1))\n",
       "        await relay.stream.read_owned(org, handle, cursor, 1)\n", READ_OUTAGE),
    _m("status_state_invented", "a job not yet terminal reports its committed state",
       J, "        return admission.state", "        return JobState.preparing", STATUS),
    _m("updated_at_is_created", "updated_at is the settlement instant once terminal",
       J, "            updated_at=outcome.settled_at if outcome is not None else admission.admitted_at,",
       "            updated_at=admission.admitted_at,", STATUS),
    _m("status_ignores_the_ttl", "result_available turns false once the TTL passed (store clock)",
       J, "        available = expires is not None and now < expires",
       "        available = expires is not None", OUTLIVES),
    _m("usage_invented", "usage is authoritative only when the store has it",
       J, "        certainty = (UsageCertainty.authoritative if usage is not None",
       "        certainty = (UsageCertainty.authoritative if outcome is not None", NO_USAGE),
    _m("unknown_usage_served_as_result", "no chat result is rendered without authoritative usage",
       J, "                or outcome.usage is None or not outcome.result_ref):",
       "                or not outcome.result_ref):", NO_USAGE),
    # === item 3: result (API-MODES) =====================================================
    _m("pending_code_wrong", "a job not yet terminal has no result: 409 result_pending",
       J, '            raise errors.ResultPending("the job is not terminal")',
       '            raise errors.StateConflict("the job is not terminal")', RESULT),
    _m("result_not_read", "the result body is the committed result object",
       J, "            text = await _dependency(relay.results.read_result(org, outcome.result_ref))",
       '            text = ""', RESULT, MATRIX),
    _m("completed_at_is_created", "completed_at is the store's settlement instant",
       J, "            completed_at=outcome.settled_at), admission)",
       "            completed_at=admission.admitted_at), admission)", RESULT),
    _m("failure_rendered_as_error", "a failed/cancelled/expired job is a 200 result, not an error",
       J, "        if outcome is None:\n            raise errors.ResultPending",
       "        if outcome is None or outcome.state is not JobState.succeeded:\n"
       "            raise errors.ResultPending", FAILURES),
    _m("expired_result_served", "a result past its TTL is 410 result_expired",
       J, "            if await jobs.now() >= expires:", "            if False:",
       STORE_CLOCK),
    _m("result_ttl_on_gateway_clock", "the result TTL is judged on the store clock (R29/R79)",
       J, "            if await jobs.now() >= expires:",
       "            if relay._now() >= expires:", STORE_CLOCK),
    _m("status_ttl_on_gateway_clock", "status availability is judged on the store clock",
       J, "        return _answer(jobs.status_of(admission, outcome, await jobs.now()), "
          "admission)\n\n    @app.get(RESULT_PATH)",
       "        return _answer(jobs.status_of(admission, outcome, relay._now()), "
       "admission)\n\n    @app.get(RESULT_PATH)", STORE_CLOCK),
    # === item 4: events replay (API-MODES, DUR-OUTPUT read side) =======================
    _m("cursor_ignored", "replay resumes after Last-Event-ID",
       J, "            await self.relay.pump(self.job, emit, gone, cursor=self.cursor,",
       "            await self.relay.pump(self.job, emit, gone, cursor=None,", EVENTS, GAP,
       MATRIX),
    _m("cursor_guessed", "a malformed cursor is 400 invalid_cursor, never a guess (R36)",
       J, "        cursor = Cursor.parse(last) if last is not None else None",
       "        cursor = None", CURSOR),
    _m("journal_checked_after_headers", "a forged cursor, a gap or an expired journal is a "
       "status, asked before any SSE header",
       J, "        await _dependency(relay.stream.read_owned(org, handle, cursor, 1))\n", "",
       CURSOR, GAP),
    _m("gap_silent", "a pruned cursor is 410 replay_gap, never an empty page (the store's)",
       ST, '            raise errors.ReplayGap(f"events up to {pruned_to} are no longer retained")',
       "            pass", GAP),
    _m("expired_journal_silent", "an expired journal is 410 journal_expired (the store's)",
       ST, '            raise errors.JournalExpired(f"journal for {job_handle} has expired")',
       "            pass", GAP),
    _m("identity_frame_dropped", "the first frame names the job (identity, no id)",
       J, "            await emit(wire.SseFrame(event=PROGRESS_EVENT,\n"
          "                                     data=_identity(self.job, self.state.value)).render())\n",
       "", EVENTS, UNSTARTED),
    _m("identity_phase_invented", "the identity frame's phase is the committed state",
       J, "                                     data=_identity(self.job, self.state.value)).render())",
       '                                     data=_identity(self.job, "running")).render())',
       EVENTS, UNSTARTED),
    _m("terminal_frame_dropped", "the replay ends with [DONE] at the terminal event",
       R, "        await emit(wire.SseFrame(id=token, data=wire.DONE).render())", "        pass",
       EVENTS, UNSTARTED),
    _m("raw_journal_relayed", "only visible text reaches the wire (R58/R80)",
       R, '        visible = chunk.payload.get("visible")',
       '        visible = chunk.payload.get("raw")', EVENTS),
    _m("observer_disconnect_cancels", "an observer that leaves detaches, never cancels",
       J, "                                  cancel_on_gone=False)",
       "                                  cancel_on_gone=True)", OBSERVER),
    _m("events_error_cancels", "an observer whose stream fails never cancels the job",
       J, "        except Exception as failure:\n",
       "        except Exception as failure:\n"
       "            await self.relay.cancel(self.job.org_id, self.job.handle, quiet=True)\n",
       OBSERVER_FAILS),
    _m("events_outside_cancel_cancels", "an observer stopped from outside never cancels the job",
       J, "        finally:\n            gone.cancel()\n",
       "        except BaseException:\n"
       "            await self.relay.cancel(self.job.org_id, self.job.handle, quiet=True)\n"
       "            raise\n        finally:\n            gone.cancel()\n", OBSERVER_STOPPED),
    _m("detach_check_removed", "the pump returns without a cancel for an observer",
       R, "            if gone.done() and not cancel_on_gone:\n                return\n", "",
       OBSERVER),
    # === item 5: DELETE (API-MODES, DUR-FENCE) ==========================================
    _m("delete_cause_disconnected", "an explicit DELETE is client_cancelled (R21)",
       J, "            relay.cancel(org, handle, cause=TerminalCause.client_cancelled))",
       "            relay.cancel(org, handle, cause=TerminalCause.client_disconnected))",
       DELETE, MIDWAY),
    _m("delete_cancels_nothing", "DELETE requests durable cancellation",
       J, "        outcome = await _dependency(\n            relay.cancel(org, handle, cause=TerminalCause.client_cancelled))",
       "        outcome = (await relay._owned(org, handle))[1]", DELETE),
    _m("delete_unshielded", "the DELETE's cancel survives the handler's own cancellation",
       J, "            relay.cancel(org, handle, cause=TerminalCause.client_cancelled))",
       "            relay.jobs.cancel(org, handle, cause=TerminalCause.client_cancelled))", MIDWAY),
    _m("delete_quiet", "a DELETE whose cancel failed is never a 200 with the pre-cancel row",
       J, "            relay.cancel(org, handle, cause=TerminalCause.client_cancelled))",
       "            relay.cancel(org, handle, cause=TerminalCause.client_cancelled, quiet=True))",
       DELETE_OUTAGE),
    _m("cancel_resettles_a_completed_job", "a DELETE after completion answers it, one settlement",
       ST, "            if job.terminal:\n                # Completion won the race",
       "            if False:\n                # Completion won the race", RACE),
    _m("delete_body_accepted", "a DELETE that declares a body is 400 and cancels nothing",
       J, '        if request.headers.get("content-length", "0") != "0" \\',
       "        if False \\", BODY),
    # === item 6: the async matrix (API-MODES) ===========================================
    _m("expired_job_served_as_running", "a terminal job reports its committed state (CREDIT)",
       J, "        if outcome is not None:\n            return outcome.state",
       "        if False:\n            return outcome.state", EXPIRED_Q, CREDIT_EVENTS),
    # === item 7: the client example's async flow ========================================
    _m("poll_ignores_retry_after", "the client polls at the 202's Retry-After",
       X, '        await SLEEP(min(wait, MAX_RETRY_AFTER_S) if wait is not None else cfg["poll_s"])',
       '        await SLEEP(cfg["poll_s"])', CLIENT),
    # === item 8: composition and the route table (F-BASE, M-FAILCLOSED) ==================
    # Review N1: with `rt.ingress` set (the cutover's shape) and no relay, the mutated router
    # mounts its five routes over nothing and then fails installing its hook on `None`
    # (`AttributeError` from `relay.on_async = ...`): that death IS the router mounting
    # without a relay, so it is declared.
    _m("mounted_without_relay", "no relay, no jobs routes (no fake fallback)",
       J, "    if relay is None:\n        return None", "    if False:\n        return None",
       COMPOSE, dies_by=("AttributeError",)),
    _m("second_jobs_route_tolerated", "each jobs route has exactly one handler",
       N, "any(found != [JOBS_MODULE] for found in declared)",
       "any(JOBS_MODULE not in found for found in declared)", TABLE),
    _m("partial_jobs_mount_tolerated", "the jobs routes are mounted all or none",
       N, "    if any(declared) and (", "    if all(declared) and (", TABLE),
    _m("jobs_route_shadow_tolerated", "each jobs route is the one Starlette picks (review N2)",
       N, "                          or any(module != JOBS_MODULE for module in picked)):",
       "                          or False):", TABLE),
    _m("jobs_routes_unnamed", "the route table names the jobs router as their handler",
       J, "        endpoint.__module__ = __name__", "        pass", TABLE),
)


#: The shared runner (R83), in the repository's shape, every named case required to notice.
RUNNER = Runner(name="g3", package="", targets=(SUITE,), layout=_layout,
                extra_args=(f"--ignore={SUITE}/test_jobs_mutants.py",), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


def case_names() -> set[str]:
    """Every `test_*` function in this suite except the list's own claims."""
    names = set()
    for path in sorted((API_DIR / SUITE).glob("test_*.py")):
        if path.name == "test_jobs_mutants.py":
            continue
        tree = ast.parse(path.read_text())
        names |= {n.name for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    return names


def main() -> int:
    return shared.main(MUTANTS, RUNNER, "run G3's mutation list")


if __name__ == "__main__":
    raise SystemExit(main())
