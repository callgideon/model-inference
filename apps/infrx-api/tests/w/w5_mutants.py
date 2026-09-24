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

MUTANTS = (
    # --- 1. the readiness barrier (RV-05, ADMISSION-READY) -------------------------------
    _m("w5_readiness_barrier_media_only", "RV-05: a text-only job waits for its committed "
       "manifest exactly as a video job does (the canary's negative control)",
       P, "        await self._ready(lease.job_id)\n",
       "        if work.media_refs:\n            await self._ready(lease.job_id)\n",
       CANARY, POSTCHECK),
    _m("w5_empty_manifest_read_as_missing", "an EMPTY manifest is completed work, not "
       "missing work", P, "        while (manifest := await self._manifest(job_id)) is None:",
       "        while not (manifest := await self._manifest(job_id)):", POSTCHECK, D1),
    _m("w5_d1_marker_ignored", "on a store with D1's port the committed marker is what the "
       "worker reads", P, '        readiness = getattr(self.jobs, "readiness", None)',
       "        readiness = None", D1),
    _m("w5_readiness_wait_unbounded", "the wait for a manifest is bounded (not_claimable)",
       P, "            if time.monotonic() >= end:", "            if False:", CANARY),
    _m("w5_unready_job_never_reaped", "a job that never became ready ends at its "
       "preparation deadline, its hold released once (the store's bound D10 keeps too)",
       S, "        if job.state is JobState.preparing and now >= job.admission.preparation_"
          "deadline_at:\n            # r1 R29/R20: a preparation worker that never comes back",
       "        if False:\n            # r1 R29/R20: a preparation worker that never comes back",
       UNREADY),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="w5", targets=(SUITE_FILE,), layout=prep_worker_mutants._layout)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run W5's mutation list"))
