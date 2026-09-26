#!/usr/bin/env python3
"""R32/R83 for W5: every invariant `tests/w/test_w5.py` claims is killable by a named case,
through the shared runner (`tests/contracts/mutants.py`) on PREP-WORKER's copy of the tree.

    uv run --frozen pytest -q tests/w/test_w5_mutants.py                 # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_w5_mutants.py
    uv run --frozen python -m tests.w.w5_mutants --list

`w5_readiness_barrier_media_only` is RV-05's executable negative control: the worker as it was
before W5 (the manifest waited for only when the request carries media) fails the text-only
canary. Equivalent edit left out on purpose: `most_video_tokens` sampling one frame fewer
instead of two (the budget's frame count is even, so one fewer always dominates two fewer;
the parity case pins the whole function to W4's `decide.worst_tokens` at every duration).
"""
from __future__ import annotations

import pathlib
import re
import shutil

from ..contracts import mutants as shared
from ..contracts.mutants import Result, Runner, _m
from . import prep_worker_mutants

API_DIR = shared.API_DIR
SUITE_FILE = "tests/w/test_w5.py"
P = "worker/preparation.py"
S = "contracts/fakes/state.py"                 # F's fake store: the bound D10 must also keep

# 1. readiness
CANARY = "test_w5_ready__a_text_job_is_never_prepared_before_its_durable_manifest"
POSTCHECK = "test_w5_ready__a_late_postcheck_decides_before_anything_is_prepared"
D1 = "test_w5_ready__the_d1_marker_is_what_the_worker_reads"
UNREADY = "test_w5_ready__an_unready_job_ends_at_its_preparation_deadline_released_once"
NO_LEGACY = "test_w5_ready__no_marker_is_never_legacy_ready"
REPLAY = "test_w5_ready__the_acceptance_transcripts_replay_through_the_runners_doors"
VIDEO_CLAIM = "test_w5_ready__a_video_job_is_never_claimed_before_its_marker"
# 2. crash boundaries
NEVER_READY = "test_w5_crash__accepted_but_never_ready_is_bounded_and_released_once"
WAKEUP = "test_w5_crash__a_lost_queue_wakeup_loses_no_job_and_a_redelivery_runs_it_once"
PREP_CRASH = "test_w5_crash__a_preparation_that_dies_mid_way_is_prepared_once"
ENGINE_CRASH = "test_w5_crash__engine_output_before_the_journal_commit_is_never_relayed_or_billed"
CANCEL = "test_w5_crash__a_cancel_during_preparation_or_before_the_claim_runs_nothing_more"
A = "worker/attempt.py"
V = "worker/service.py"
# S3 F4: the reconciliation gauges
GAUGES = "test_w5_reconcile__each_reaper_tick_publishes_the_reconciliation_gauges"
PG_VIEWS = "test_w5_reconcile_pg__the_detector_views_count_drift_and_unknown_holds"
PG_READY = "test_w5_ready_pg__the_worker_prepares_only_what_admit_ready_marked"
# 3. refusals
PERMANENT = "test_w5_refuse__a_permanent_refusal_ends_the_job_once"
TEMPORARY = "test_w5_refuse__a_temporary_failure_is_retried_within_its_bound_never_ended_early"
ENCODER = "test_w5_refuse__a_clip_past_the_encoder_budget_is_refused_before_it_is_queued"
PARITY = "test_w5_refuse__the_video_bound_is_the_processors_worst_case_at_every_duration"
GEOMETRY = "test_w5_refuse__the_maximum_geometry_at_the_cap_is_prepared"
RACE = "test_w5_refuse__a_permanent_refusal_racing_a_cancel_settles_once"
NO_PORT = "test_w5_refuse__without_fail_preparation_a_permanent_refusal_lapses_within_its_bound"
PG_CLOSED = "test_w5_ready_pg__without_a_readiness_store_a_text_job_fails_closed"
NO_PORT_BRANCH = ("            return None\n        try:\n"
                  "            ended = await fail(lease, cause)")

MUTANTS = (
    # --- 1. the readiness barrier (RV-05, ADMISSION-READY) -------------------------------
    _m("w5_readiness_barrier_media_only", "RV-05: a text-only job waits for its committed "
       "manifest exactly as a video job does (the canary's negative control)",
       P, "        await self._ready(lease.job_id)\n",
       "        if work.media_refs:\n            await self._ready(lease.job_id)\n",
       CANARY, POSTCHECK, NEVER_READY),
    _m("w5_empty_manifest_read_as_missing", "an EMPTY manifest is completed work, not "
       "missing work", P, "        while (manifest := await self._manifest(job_id)) is None:",
       "        while not (manifest := await self._manifest(job_id)):", POSTCHECK, D1),
    _m("w5_d1_marker_ignored", "with the ReadinessStore wired the committed marker is what "
       "the worker reads", P, "        if self.readiness is None:\n            return await "
       "self.media.attached(job_id)", "        if True:\n            return await "
       "self.media.attached(job_id)", D1, NO_LEGACY),
    _m("w5_no_marker_read_as_legacy_ready", "no marker is NOT READY - never read as ready from "
       "the previous gateway's attach record (the cutover rule, no backfill)",
       P, "        return None if ready is None else tuple(",
       "        return (await self.media.attached(job_id)) if ready is None else tuple(",
       NO_LEGACY),
    _m("w5_claim_bypasses_the_marker_gate", "the preparation claim goes through the "
       "ReadinessStore's marker-gated door", P,
       "        return self.readiness or self.jobs\n", "        return self.jobs\n",
       D1, REPLAY, VIDEO_CLAIM),
    _m("w5_readiness_wait_unbounded", "the wait for a manifest is bounded (not_claimable)",
       P, "            if time.monotonic() >= end:", "            if False:", CANARY),
    _m("w5_unready_job_never_reaped", "a job that never became ready ends at its "
       "preparation deadline, its hold released once (the store's bound D10 keeps too)",
       S, "        if job.state is JobState.preparing and now >= job.admission.preparation_"
          "deadline_at:\n            # r1 R29/R20: a preparation worker that never comes back",
       "        if False:\n            # r1 R29/R20: a preparation worker that never comes back",
       UNREADY),
    # --- 2. crash boundaries (DUR-FENCE, OPS-RECOVER) ------------------------------------
    _m("w5_queued_without_a_durable_dispatch", "a queued job's wakeup is a durable outbox row "
       "(the index is only a hint that can be lost and redelivered)",
       S, "            self._emit(job.id, OutboxKind.inference_dispatch, now,\n"
          "                       {\"job_handle\": job.admission.job_handle, "
          "\"request_id\": job.id})\n", "", WAKEUP),
    _m("w5_lapsed_preparation_not_redispatched", "a preparation whose worker died is "
       "redispatched from the outbox (R93), so the next worker prepares it",
       S, "            self._emit(job.id, OutboxKind.prepare_dispatch, now,\n"
          "                       {\"request_id\": job.id, \"attempt\": job.preparation_attempts})",
       "            pass", PREP_CRASH),
    _m("w5_released_attempt_flushes_on_the_way_out", "an attempt released mid-generation "
       "journals nothing on its way out: unjournalled engine output is never published, "
       "relayed or billed", A,
       "            finally:\n                # Deterministic: stop reading the engine now, "
       "whatever ended the loop.\n",
       "            finally:\n                await self._flush(state, result)\n"
       "                # Deterministic: stop reading the engine now, whatever ended the loop.\n",
       ENGINE_CRASH),
    _m("w5_published_output_regenerated", "published output is never regenerated after a "
       "lost worker (lost_after_publication)",
       S, "            if job.published:\n", "            if False:\n", ENGINE_CRASH),
    _m("w5_preparation_refusal_escapes_on_cancel", "a job cancelled during preparation is a "
       "typed answer (already_terminal), never a dead runner",
       P, "        except errors.DomainError as refused:\n            log.warning(",
       "        except errors.StaleLease as refused:\n            log.warning(", CANCEL),
    _m("w5_cancelled_queued_job_claimed", "a job cancelled after it was queued is never "
       "claimed as already_terminal's own answer",
       S, "            if job.terminal:\n                raise errors.AlreadyTerminal(f\"job "
          "{job_id} is {job.state}\")\n            now = self.clock.now()\n"
          "            if job.state is not JobState.queued:",
       "            now = self.clock.now()\n            if job.state is not JobState.queued:",
       CANCEL),
    # --- S3 F4: the reconciliation pass publishes its gauges ------------------------------
    _m("w5_reconciliation_not_published", "each reaper tick publishes the reconciliation "
       "gauges (the negative control: no runtime producer, as before S3 F4)",
       V, "        await self._reconciled()\n", "", GAUGES),
    _m("w5_reconciliation_unsettleable_dropped", "infrx_unsettleable_jobs is the reaper's own "
       "unsettleable set", V, "unsettleable=len(self.jobs.unsettleable)", "unsettleable=0",
       GAUGES),
    _m("w5_reconciliation_failure_kills_the_reaper", "a failed reconciliation read is counted "
       "and the reaper lives on", V,
       "        except Exception as failure:              # the database is down: the last "
       "pass stands", "        except ZeroDivisionError as failure:", GAUGES),
    # --- 3. permanent versus temporary refusals ------------------------------------------
    _m("w5_permanent_refusal_left_to_lapse", "a permanent refusal ends the job at once - "
       "unsupported media never cycles preparation leases (the negative control: the "
       "pre-W5 worker)", P, "                                     ended=await self._end(lease, "
       "refused))", "                                     ended=None)", PERMANENT, ENCODER),
    _m("w5_temporary_refusal_ended_early", "a temporary failure is retried within its bound, "
       "never ended by the worker", P,
       "PERMANENT = {errors.UnsupportedMedia: TerminalCause.invalid_media,",
       "PERMANENT = {errors.DependencyUnavailable: TerminalCause.preparation_failed,\n"
       "             errors.UnsupportedMedia: TerminalCause.invalid_media,", TEMPORARY),
    _m("w5_over_context_left_to_lapse", "a prompt past max_input_tokens is permanent", P,
       "             errors.RequestTooLarge: TerminalCause.invalid_media,\n"
       "             errors.ContextLengthExceeded: TerminalCause.preparation_failed}",
       "             errors.RequestTooLarge: TerminalCause.invalid_media}", PERMANENT),
    _m("w5_invalid_media_ends_as_a_platform_cause", "over-cap media ends with the actionable "
       "invalid_media cause", P,
       "PERMANENT = {errors.UnsupportedMedia: TerminalCause.invalid_media,",
       "PERMANENT = {errors.UnsupportedMedia: TerminalCause.preparation_failed,",
       PERMANENT, ENCODER),
    _m("w5_encoder_budget_unchecked", "a video item past the served encoder budget is refused "
       "before it is queued (P-23)", P, "        if video > ENCODER_CACHE_TOKENS:",
       "        if False:", ENCODER),
    _m("w5_encoder_budget_raised", "the encoder budget is the served engine's 16,384, whose "
       "ceiling is the 82 s cap", P, "ENCODER_CACHE_TOKENS = 16_384",
       "ENCODER_CACHE_TOKENS = 32_768", PARITY, ENCODER),
    _m("w5_video_bound_fully_sampled_only", "the video bound is the processor's worst case, "
       "not the fully sampled count (max geometry at the cap)", P,
       '        most = most_video_tokens(longest, budget["min_frames"])',
       "        most = longest // PIXELS_PER_TOKEN", GEOMETRY),
    _m("w5_worst_case_without_group_padding", "the worst case pads the sampled frames to "
       "two-frame groups as the processor does", P,
       "(min(round(sampled / 2) * 2, sampled) * 1024)", "(sampled * 1024)", PARITY),
    _m("w5_permanent_end_refusal_escapes", "a permanent refusal racing a cancel is answered "
       "(already_terminal), never a dead runner or a second settlement", P,
       "        except errors.DomainError as lost:\n            return lost.code",
       "        except errors.StaleLease as lost:\n            return lost.code", RACE),
    # --- fix round: TODAY's path, a store without fail_preparation (0-W5-R1, 2-W5-ACC-2) ---
    _m("w5_fail_preparation_required", "a store without fail_preparation (every real store "
       "until wiring 3) is not an untyped crash of the runner", P,
       '        fail = getattr(self.jobs, "fail_preparation", None)',
       "        fail = self.jobs.fail_preparation", NO_PORT),
    _m("w5_no_port_crashes_the_runner", "no fail_preparation port: the refusal is logged and "
       "the lease lapses, the runner lives on", P, NO_PORT_BRANCH,
       NO_PORT_BRANCH.replace("return None", 'raise RuntimeError("no port")'), NO_PORT),
    _m("w5_no_port_reports_an_end", "no fail_preparation port: nothing ended, so `ended` is "
       "None", P, NO_PORT_BRANCH,
       NO_PORT_BRANCH.replace("return None", 'return "invalid_media"'), NO_PORT),
)

PG_MUTANTS = (
    _m("w5_reconciliation_ignores_the_usd_view", "drift counts both regimes' detector views",
       V, '    "select (select count(*) from infrx.wallet_reconciliation"\n'
          '    " where ledger_drift <> 0 or reserved_drift <> 0)"\n    " + (',
       '    "select (', PG_VIEWS),
    _m("w5_reconciliation_ignores_the_credit_view", "drift counts the CREDIT detector view "
       "(0006) as well", V,
       '    " + (select count(*) from infrx.credit_wallet_reconciliation"\n'
       '    " where ledger_drift <> 0 or reserved_drift <> 0),"', '    " + 0,"', PG_VIEWS),
    _m("w5_reconciliation_ignores_usd_unknown_holds", "unknown holds count the USD regime's "
       "credit_holds", V,
       "    \" (select count(*) from infrx.credit_holds where state = 'unknown')\"",
       "    \" 0\"", PG_VIEWS),
    _m("w5_reconciliation_ignores_credit_unknown_holds", "unknown holds count the CREDIT "
       "regime's credit_wallet_holds", V,
       "    \" + (select count(*) from infrx.credit_wallet_holds where state = 'unknown')\")",
       "    \" + 0\")", PG_VIEWS),
    _m("w5_reconciliation_counts_known_holds", "only holds in the unknown state are counted",
       V, "infrx.credit_holds where state = 'unknown'",
       "infrx.credit_holds where state <> 'unknown'", PG_VIEWS),
    _m("w5_text_job_prepared_without_a_marker_on_postgresql", "without a ReadinessStore a text "
       "job on the pre-D10 PostgreSQL store fails closed (the merge-order gate)", P,
       "        await self._ready(lease.job_id)\n",
       "        if work.media_refs:\n            await self._ready(lease.job_id)\n", PG_CLOSED),
    # needs D10's `infrx.state.lifecycle` on the tree (skipped visibly without it)
    _m("w5_claim_bypasses_the_marker_gate_on_postgresql", "on PostgreSQL the worker claims "
       "through D10's marker-gated door: a job the previous runtime admitted is never prepared",
       P, "        return self.readiness or self.jobs\n", "        return self.jobs\n",
       PG_READY),
)
#: The PG mutants that also need D10's adapter on the tree.
NEEDS_D10 = frozenset({"w5_claim_bypasses_the_marker_gate_on_postgresql"})


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


DECIDE = pathlib.Path("models/marlin2b/measure/decide.py")      # W4's processor arithmetic


def _layout(root: pathlib.Path) -> pathlib.Path:
    """PREP-WORKER's copy plus W4's `decide.py`, the parity case's oracle."""
    api = prep_worker_mutants._layout(root)
    (root / DECIDE).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(API_DIR.parents[1] / DECIDE, root / DECIDE)
    return api


RUNNER = Runner(name="w5", targets=(SUITE_FILE,), layout=_layout)
PG_RUNNER = Runner(name="w5-pg", targets=(SUITE_FILE,), layout=prep_worker_mutants._pg_layout,
                   env=("INFRX_D_TASK",))


def run_mutant(mutant) -> Result:
    """`PG_MUTANTS` is not a module's `MUTANTS`: its cases run unmutated first (R83 (b))."""
    if mutant in PG_MUTANTS:
        cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
        return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run W5's mutation list"))
