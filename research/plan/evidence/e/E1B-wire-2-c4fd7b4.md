# E1B-WIRE-2 — E1B-WIRE's WREQ-1..3 and its lens minors, applied (no measurement)

- Lane: E1B-WIRE-2 (support lane for E1B). Branch `codex/e1b-wire-2`, base `aac38207`.
- Code commit `c4fd7b44`; this file and the update file are committed on top of it.
- No GPU, no box, no AWS/SSM, no hosted Supabase, no paid call. Task-local docker `INFRX_D_TASK=w5` only (no container left).

## 1. Changed paths

| Path | Item |
|---|---|
| `apps/infrx-api/infrx/worker/__main__.py` (the inference `WorkerLoop`) | WREQ-1 |
| `apps/infrx-api/infrx/worker/loop.py` (`observe_phases` guard) | E1B-WIRE lens: observe_phases assumed `result.timings` |
| `apps/infrx-api/tests/w/test_worker_main.py`, `tests/w/worker_main_mutants.py` | WREQ-1 case + 2 mutants |
| `apps/infrx-api/tests/w/test_loop.py`, `tests/w/loop_mutants.py` | guard case + 2 mutants; 2 anchors moved |
| `models/marlin2b/tests/mutants.py` | WREQ-2 (e1bm32-34); e1bm17 re-anchored |
| `infra/rollout/e1b-window.sh`, `apps/infrx-api/tests/i/test_rollout.py` | E1BW-R1 (guard), E1BW-R2 (interrupted log), E1BW-R3 (bounded wait) |
| `models/marlin2b/results/E4C-runbook.md` §3, §4a, §5, §5.0, §5.2, §7, log | WREQ-3, E1BW-R4 |
| `models/marlin2b/results/E1B-protocol.md` §7.2 WC-9 row, Order, log | WREQ-3 |

## 2. Per item: failed before, passes after, mutants

**WREQ-1.** `loop = WorkerLoop(..., limits=limits, metrics=rt.metrics)`; the preparation loop keeps none.
- Case: `test_worker_main__the_composition_is_the_pilots_stores_and_settings` now asserts
  `service.loop.metrics is service.metrics and service.preparation.metrics is None`.
- Before: FAILED, `assert None is <Registry>` (`service.loop.metrics` was `None`). After: passed.
- Mutants (`worker_main_mutants.py`): `main_loop_phases_unobserved` (drop `metrics=rt.metrics`) killed;
  `main_preparation_phases_observed` (the preparation loop also given `metrics=rt.metrics`) killed.

**Timings guard (lens: observe_phases assumed `result.timings`).** `loop.py` reads
`timings = getattr(result, "timings", None)` and observes only when `self.metrics is not None and timings`.
- Case `test_ops_recover__a_result_without_phase_timings_is_never_observed`: a metrics loop over a runner
  returning a `PreparationResult` returns it, `loop.failures == []`, no `infrx_phase_seconds_count`.
- Before: FAILED, `loop.failures == [AttributeError("'PreparationResult' object has no attribute 'timings'")]`.
  After: passed.
- Mutants (`loop_mutants.py`): `phase_timings_read_unguarded` (`result.timings`) killed;
  `phase_timings_observed_when_absent` (guard without `and timings`) killed. The two E1B-WIRE mutants
  (`phase_timings_never_observed`, `phase_timings_in_milliseconds`) are re-anchored on `timings` and still killed.

**WREQ-2.** `models/marlin2b/tests/mutants.py`, select `eos_ids`, case
`test_the_direct_leg_supplies_both_eos_ids_and_the_gateway_leg_neither`:
- `e1bm32` the `stop_token_ids` line dropped: killed; `e1bm33` `[248046]` only: killed;
  `e1bm34` the line dedented so the gateway leg carries the ids too: killed.
- Also: the whole list's first run reported `e1bm17` **stale** (`` `--seed 20260922` `` occurs twice since
  E1B-PREP's §7.1 rule 3, fc3d66a8; pre-existing at `aac38207`). Re-anchored on `--seed 20260922` with
  `occurrences=4` (§1 row, §7.1 rule 3, §7.2's BOX and WC-9): killed. Second full run: 119 mutants,
  116 killed, 3 controls survived, 0 problems, exit 0.

**E1BW-R1 (certify-live guard).** `grep -v '^infrx-e1b-' | grep -q ' infrx-certify:'` → one
`awk '$2 ~ /^infrx-certify:/ {f=1} END {exit !f}' <<< "$names"`. The `infrx-e1b-*` exclusion was dead: the
left-cell check on the line above already refuses any such listing (an ad hoc mutant dropping it survived, so it
was removed rather than kept untested).
- Demonstrated: a 200,001-line listing with the certify container first → the pipeline's status `141 0`
  (SIGPIPE under pipefail): guard missed; awk caught it.
- Pin: the refusal test gains a 50,001-line listing case. Before (aac38207 launcher): FAILED,
  `('a certify run is live', '')`, `assert (0 == 2)`. After: passed.

**E1BW-R2 (interrupted WC-8 half kept) and E1BW-R3 (bounded wait).** The first half runs attached in the
background (`container … > "$out/$label-interrupted.log" 2>&1 &`; `--rm`, so the client returns once the
container is removed), polled with `kill -0`; after the SIGINT the launcher waits at most 120 s, then
`docker kill`s the container and appends `WC-8 interrupt did not exit in 120 s: killed` to `cells.tsv`, then
the resume runs as before. Also: the progress poll opens `sop.sqlite` with `?mode=ro`, so it never creates a
root-owned empty state file before `dataset.py` does (found while editing; the box's certify image user is not
pinned, `chmod 777` on `$out` suggests non-root).
- Pin `test_e1b_window__wc8_keeps_the_interrupted_half_and_bounds_its_exit` (stub docker holding the first half:
  3 `done` rows, then SIGINT honoured or ignored; the test shortens the bound to 2 s): `sop-interrupted.log`
  holds the half's output; `exits` → one `kill --signal INT`, `cells.tsv` = `WC-8 sop exit=0`; `stuck` → the
  last kill is a plain `kill`, `cells.tsv` = the did-not-exit row then `WC-8 sop exit=0`; 3 runs (half, resume,
  export); with no state yet, `sop.sqlite` is not created by the poll.
- Before (aac38207 launcher): FAILED, `FileNotFoundError … /sop-interrupted.log`. After: passed.
- Ad hoc single-edit mutants on the launcher, each run against `tests/i/test_rollout.py`, 7/7 killed:
  `r1_guard_back_to_the_pipeline`, `r1_guard_reads_the_name_column`, `r2_interrupted_half_discarded`,
  `r3_wait_unbounded`, `r3_stuck_half_not_killed`, `r3_stuck_half_not_recorded`, `poll_creates_the_state`.
  (No mutation runner covers `test_rollout.py`; E1B-WIRE's launcher mutants were ad hoc too.)

**WREQ-3 and E1BW-R4 (docs).**
- E4C-runbook §3: copies `E1B-{direct,box,box-forms,sop}.json` from the four box bases into
  `/opt/dlami/nvme/e4b/e4c/`; FILL rows: the §2 identity and `maintenance_window`, as box, no fault targets,
  `E1B-direct.json` keeps `model_revision` `marlin2b`; the two-tenant copy is §5.0's.
- §4a (new): `TIMEOUT_S=7200 infra/rollout/ssm.sh infra/rollout/e1b-window.sh RELEASE=$RELEASE` after §4,
  then WC-6a, then `… CELLS=WC-7` right after `restored=yes`, then WC-6b; WC-0 from §4 to the last cell; no
  certify run, soak, journey leg or drill started while the launcher runs (E1B §7.1 rule 1); a window cell has
  no effect on certify or P-17. §5 starts only after §4a's last cell.
- §5.0 step 2 also fills `~/e4c/E1B-two-tenant.json` from `E1B-edge.two-tenant.base.json` with the tenant-2
  prefix; §5.2 (new) places WC-9 after §5.1 and before §6, by reference to E1B-protocol §7.2 (no bench line is
  pasted: `test_profile.py`'s runbook test validates every bench line the runbook prints). §7's
  `profiles.sha256` line names the E1B bases. No step renumbered.
- E1B-protocol §7.2: WC-9's parenthesis names runbook §5.0 step 2; the Order paragraph names §4a and §5.2.
  The "runbook §3 fills" sentence had no "pending" word; it is now true. No §7.2 command changed.
- A verification-log line appended to each document.

## 3. Commands (committed tree `c4fd7b44`)

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env synced |
| `INFRX_D_TASK=w5 uv run --frozen --no-sync pytest -q tests/w` | 0 | 333 passed, 10 skipped in 612 s (331 + 2 new; skips: local S3/MinIO or root-only) |
| `INFRX_D_TASK=w5 INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/w/test_worker_main_mutants.py tests/w/test_loop_mutants.py` | 0 | 128 passed, 7 skipped in 278 s: 48/48 worker_main service-free mutants and 72/72 loop mutants killed, 8 list checks; the 7 skips are the PG+S3 worker_main mutants (no local S3 endpoint) |
| `.venv/bin/python models/marlin2b/tests/mutants.py --report …` | 0 | 119 mutants: 116 killed, 3 controls survived, 0 problems |
| `uv run --frozen --no-sync pytest -q ../../models/marlin2b/tests` | 0 | 118 passed (includes the §7.2 pin and the runbook bench-line pin) |
| `uv run --frozen --no-sync pytest -q ../../tests/integration/backend/recovery/test_runbooks.py` | 0 | 13 passed |
| `uv run --frozen --no-sync pytest -q tests/i/test_rollout.py` | 0 | 15 passed (14 + 1 new) |
| launcher ad hoc mutants (7) | 0 | 7/7 killed |
| `bash -n infra/rollout/e1b-window.sh` | 0 | — |
| `DRY_RUN=1` on a scratch box tree (committed bases copied, FILLs as is) | 0 | 12 plan lines (WC-1 ×4, WC-2 ×2, WC-3, WC-4, WC-5, WC-8 run/resume/export); `CELLS=WC-7`: `plan WC-7 cold`; `sop-incap.jsonl` sha256 `5e2b71bacdd4…` = the SOP base's `manifest_sha256` |
| `python3 research/plan/scripts/validate_plan.py` | 0 | 4 PASS lines, 0 failures (931 links, 278 documents) |
| `git diff --stat aac38207..HEAD` | 0 | the 11 paths above + this file + the update file |

## 4. Wiring requests

None. Every change is in the lane's listed paths.

## 5. Open issues

- WC-8: if the first half does not exit within 120 s of the SIGINT it is SIGKILLed; the resume then follows a
  hard kill, not an interrupt. The launcher records it; whether MARLIN-SOP's interrupt half counts is E1B's
  post-window call.
- A `docker run` of the first half that fails at once (image missing) is no longer fatal to the launcher (it was
  `-d` under `set -e`); the resume then runs and its exit is recorded in `cells.tsv`, the first half's error is in
  `sop-interrupted.log`.
- The WC-8 poll still needs host `python3` with `sqlite3` (E1B-WIRE open issue, unchanged).

## 6. Remaining effort (E1B through the window)

Optimistic 2.5 h, likely 4.5 h, pessimistic 8.5 h. Confidence: medium. Basis: the wiring is done; the window
cells are 1.0–1.6 h of the E4C window (E1B-protocol §7.2, `est.`); post-window analysis 2–4 h; pessimistic adds
one pair re-run and a launcher fix found on the box.
