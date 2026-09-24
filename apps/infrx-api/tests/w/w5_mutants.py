#!/usr/bin/env python3
"""R32/R83 for W5: every invariant `tests/w/test_w5.py` claims is killable by a named case,
through the shared runner (`tests/contracts/mutants.py`) on PREP-WORKER's copy of the tree.

    uv run --frozen pytest -q tests/w/test_w5_mutants.py                 # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_w5_mutants.py
    uv run --frozen python -m tests.w.w5_mutants --list

`w5_readiness_barrier_media_only` is RV-05's executable negative control: the worker as it was
before W5 (the manifest waited for only when the request carries media) fails the text-only
canary.
"""
from __future__ import annotations

import re

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
# 2. crash boundaries
NEVER_READY = "test_w5_crash__accepted_but_never_ready_is_bounded_and_released_once"
WAKEUP = "test_w5_crash__a_lost_queue_wakeup_loses_no_job_and_a_redelivery_runs_it_once"
PREP_CRASH = "test_w5_crash__a_preparation_that_dies_mid_way_is_prepared_once"
ENGINE_CRASH = "test_w5_crash__engine_output_before_the_journal_commit_is_never_relayed_or_billed"
CANCEL = "test_w5_crash__a_cancel_during_preparation_or_before_the_claim_runs_nothing_more"
A = "worker/attempt.py"

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
       "lease = await (self.readiness or self.jobs).claim_preparation(",
       "lease = await self.jobs.claim_preparation(", D1),
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
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="w5", targets=(SUITE_FILE,), layout=prep_worker_mutants._layout)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run W5's mutation list"))
