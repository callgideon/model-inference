# LAB-SQL-INTEGRATION — the consuming lanes on lab-sql-lw2's real stores (LW3, post-launch Lab)

- Base `8ae65fa8` (merge batch #8: migrations 0031–0040, LOCAL-ONLY, R151) · code head `a6b4bbb4` · branch `codex/w5-lab-sql-integration`, worktree `.claude/worktrees/codex-w5-lab-sql-integration`.
- Commits, one per item: `359d05a4` WR-LSQ-4 · `3481864e` WR-LSQ-7 eval half + B-R1 + E6L-O1/O2 · `862b5b68` WR-LSQ-5 · `d654f6e8` WR-LSQ-6 · `a6b4bbb4` WR-OBS-3. R1's real half: NOT RUN (below).
- Task-local only: keys j2 (57511/57512), b3 (57522), b1 (57520/57521), r1 (57532), r2 (57534). No migration, no `infrx/state/lab_*.py`, no composition root, no `apps/lab`, nothing hosted. Everything touched is Lab-only and reachable from the launched App/API only through flags that stay OFF (`lab_submission` is a DB flag row, absent = OFF; J2's `JUDGE_MODE` stays `dry_run` by default).

## Changed paths (all owned)
- `infrx/judge/submit.py` (J2): `JudgeWiring.retention` (T3 `Retention`) replaces `projection`/`objects`; content is read with `retention.read_content(grantor, request)` (WR-OBS-3). `JudgeLedger` names `PgJudgeLedger` as its PostgreSQL adapter (WR-LSQ-4; no code change was needed: `JudgeRun` is `LedgerRun` field for field).
- `infrx/evaluation/runner` (B1): `freeze` registers the evaluator (`store.put_evaluator`, 0034 R167) before publishing the run; the 402 give-back calls `store.release` (0034 `lab_release_attempt`) unconditionally (the `getattr` ponytail removed, B-R1); `case_of` gives H1 a finite-video clip as `sample.media_ref` = N1's media key and `sample.duration_ms` = `span_ms[1]-span_ms[0]` (E6L-O1).
- `infrx/evaluation/reports` (B2): `case_records(frozen, rows, objects, *, slice_path=None)` builds B2's case records from D7's `run_results` (0034) + the frozen manifest: result under the run's own evaluator = score/latency, failed without one = error, else missing; every attempt's cost; `group_key` = cluster; slices at a declared path into the case (E6L-O2 / WR-B-8).
- `infrx/rollouts/control` (R2): docstring only - `PgReleaseStore` is its `ReleaseStore` (WR-LSQ-5; `Release` field for field).
- `infrx/rollouts/optimization` (R3): `store(data, variant, comparison, report, *, provider_org_id, actor)`: publish the variant, `put_eval_report`, then `put_variant_comparison` (0040); refuses a comparison of another variant/report before any write (WR-LSQ-6).
- Tests: `tests/j/submit/{test_submit_ledgers.py (new, 6 cases x fake|pg), fakes.py (Tombstones, retention), test_submit.py (+1), mutants.py}`; `tests/b/runner/{world.py (fake put_evaluator + D7's refusal, F4 end state), test_runner.py, test_runner_pg.py (xfail removed, +1 PG case), mutants.py}`; `tests/b/reports/{test_reports.py (+1), mutants.py (+8)}`; `tests/h/{test_harness.py (+1 E6L-O1 case), mutants.py (+3 B1 mutants)}`; `tests/r/control/test_control_pg.py` (new, 3); `tests/r/optimization/{test_optimization.py (+1), test_optimization_pg.py (new, 1), mutants.py (+4)}`.
- Checkpoints (B3): unchanged. `PgCheckpointLedger` is not on the base (lab-sql-lw3, 0041+), so B3 keeps its fake; nothing new filed.

## Commands (`apps/infrx-api` unless noted)
| # | command | exit | result |
|---|---|---|---|
| 1 | `INFRX_D_TASK=j2 pytest -q tests/j/submit` (base, before) | 0 | 35 passed |
| 1 | seam: `test_submit_ledgers.py` on j2 with the `lab_submission` row inserted as `false` (scratch edit, reverted) | 1 | **5 failed [pg]** (`DependencyUnavailable` from `PgJudgeLedger`), 7 passed - the pg param really runs on 0036 |
| 1 | `INFRX_D_TASK=j2 pytest -q tests/j/submit/test_submit_ledgers.py` | 0 | **12 passed** (6 fake + 6 on `PgJudgeLedger`: one intent/one batch + worst-case hold, concurrent runs under the payer's budget (real DB contention), revocation before egress releases, ambiguous held then reconciled (adopt + release), results of sent samples stored/settled once, no grant reserves nothing) |
| 1 | `INFRX_D_TASK=j2 pytest -q tests/j` | 0 | 310 passed |
| 2 | seam: `INFRX_D_TASK=b3 pytest -q tests/b/runner/test_runner_pg.py tests/b/checkpoints/test_checkpoints_pg.py` (before) | 1 | **10 failed** (`not_found: no such evaluator for this provider`; the 402 case no longer xfail-guarded) |
| 2 | same, after WR-LSQ-7 + B-R1 (+ the new E6L-O2 PG case) | 0 | **11 passed** (incl. `test_b1_pg_402_deliveries_past_max_attempts_never_fail_a_case` without its xfail) |
| 2 | `INFRX_D_TASK=b1 pytest -q tests/b/runner/test_runner_pg.py` (b1 model-fake 57521) | 0 | 8 passed (before the E6L-O2 case was added; it ran on b3 and in api-test on b1) |
| 2 | `INFRX_D_TASK=b3 pytest -q tests/b` | 0 | 75 passed |
| 2 | E6L-O1 seam: `INFRX_MUTANTS=all pytest -q tests/h/test_mutants.py -k b1_clip` (`b1_clip_inputs_never_added` = the old behaviour) | 0 | 3 killed: the H1 case fails on the old case shape (`missing_input:sample.media_ref`, E6L j11's defect) |
| 2 | `pytest -q tests/b/reports` | 0 | 20 passed |
| 2 | `INFRX_D_TASK=b3 INFRX_MUTANTS=all pytest -q tests/b/runner/test_mutants.py tests/b/checkpoints/test_mutants.py tests/b/reports/test_mutants.py tests/h/test_mutants.py tests/i/lab_eval/test_mutants.py` | 1 | **216 passed, 32 failed**: every B1/B2/B3/H1 mutant killed (new: `b1_evaluator_unregistered`, `b1_clip_*` x3, `b2r_*` x8; `b1_402_release_refusal_raises` re-anchored); the 32 = all of I5's `tests/i/lab_eval` list, `broken_runner` because its pristine baseline fails on the BASE: `test_i5_every_role_is_off_until_its_env_file_exists_and_nothing_installs_it` (`infra/rollout/steps/72-observe-install.sh` contains `infrx-lab`, from merge batch #9) - not this lane's; reproduced alone without any key |
| 3 | `INFRX_D_TASK=r1 pytest -q tests/r/control/test_control_pg.py` (r2 was held by a foreign `infrx-r2-postgres`, label `.../scratchpad/verify-lab-api-2`) | 0 | 3 passed: first run, no product change needed |
| 3+4 | `INFRX_D_TASK=r2 pytest -q tests/r` (after the foreign container was gone) | 0 | **53 passed** (incl. `test_control_pg.py` 3 + `test_optimization_pg.py` 1 on r2) |
| 4 | `pytest -q tests/r/optimization/test_optimization.py`; `INFRX_D_TASK=r1 pytest -q tests/r/optimization/test_optimization_pg.py` | 0 | 10 passed; 1 passed (store twice = one digest; read back beside the report; OTHER sees none; a comparison on an unstored report is `NotFound` from D7) |
| 3+4 | `INFRX_MUTANTS=all pytest -q tests/r/control/test_mutants.py tests/r/optimization/test_mutants.py` | 0 | **115 passed** (new `r3_store_*` x4) |
| 5 | seam: `pytest -q tests/j/submit/test_submit.py -k deleted_or_expired` (before) | 1 | **1 failed**: the owner-deleted request `...0001` was sent to the judge (E5L-F1) |
| 5 | after: `INFRX_D_TASK=j2 pytest -q tests/j/submit` | 0 | all passed (mutant-list shape cases too) |
| 1+5 | `INFRX_D_TASK=j2 INFRX_MUTANTS=all pytest -q tests/j/submit/test_mutants.py tests/j/test_mutants.py` (final code) | 0 | **161 passed** (J2 list + J1's; new `content_read_from_the_raw_projection`; `content_read_as_the_provider` re-anchored; the six ledger cases named on existing mutants). An earlier run was `broken_runner` only because I ran `tests/j` concurrently on the same judge-fake port 57512 - rerun alone: 160 passed |
| 5 | repo root: `apps/infrx-api/.venv/bin/python tests/integration/lab_observe/runner.py --out <scratch>/e5l-o06/run --only o06` (with WR-LSQ-INT-1 applied as a scratch edit, reverted) | 3 | **BLOCKED**: preflight refuses foreign leftover volumes `infrx-e5l_{clickhouse,postgres,s3}-data` (label `ai.infrx.e2.checkout=.../scratchpad/rv-lab-observe-0F2/tests/integration`); not touched |
| - | repo root: `pytest -q tests/integration/lab_observe/test_mutants.py`; `... lab_evaluate/test_mutants.py` | 0 | 8 passed 1 skipped; 6 passed 1 skipped (their anchors in my files still hold) |
| - | `ruff check` of the changed modules/tests | 1 | 1 finding, pre-existing on the base: `tests/j/submit/test_http.py:19` unused `fakes` import (untouched) |
| all | repo root: `INFRX_D_TASK=b1 make api-test` (detached, 4200 s) | 2 | **5819 passed, 90 skipped, 9 xfailed, 60 failed, 8 errors** - none in tests/j, tests/b, tests/r, tests/h; every failure is outside this lane (see "Final sweep") |

## Final sweep: the 60 failures + 8 errors of `make api-test` (none this lane's)
- 7 `tests/d/test_outbox_relay.py[valkey]`: the foreign `infrx-d2-valkey` (known, recorded in the brief).
- 43 `tests/i/test_mutants.py`: pristine baseline fails under a non-default key (known, recorded in the brief; here via `tests/i/test_ops_steps.py` + the pooler case below).
- 8 errors `tests/i/test_pooler.py` x5, `test_observe.py`, `test_privilege_probe.py`, `test_rollback_drill.py`: `BlockingIOError` on `/tmp/infrx-i8-postgres-*.lock` - another checkout held the i8 pooler lock during the run (environmental).
- 1 `tests/i/lab_eval/test_units.py::test_i5_every_role_is_off_until_its_env_file_exists_and_nothing_installs_it` + 5 `tests/i/lab_eval/test_mutants.py` (broken_runner from it): base defect, `infra/rollout/steps/72-observe-install.sh` names `infrx-lab` (merge batch #9); reproduced without any key.
- 1 `tests/i/lab/test_lab_packaging.py::test_i2l__secret_names_only_and_every_name_the_files_read_is_declared` + 2 `tests/i/lab/test_mutants.py` (broken_runner from it): base defect, `LAB_DATASETS_API_URL` read but not declared; reproduced alone.
- 1 `tests/d/test_code_mutants_d6j.py[c3l_double_click_twice]` survived: lab-sql's race-timing SQL mutant on 0037 (`check_a_concurrent_double_click_queues_one_run`) under the load of the full suite; this lane touches no SQL and no `infrx/state/lab_*.py` - for lab-sql to harden.

## Oracles (what each new case catches)
- J2 over both ledgers: a second egress for one intent, a hold under one sample, budget overrun under contention, a hold kept after a pre-egress revocation, an unknown outcome released, provider evidence ignored, a skipped sample scored, a settle twice (mutants `any_caller_egresses`, `reservation_is_one_sample`, `no_recheck_before_egress`, `revoked_run_keeps_its_hold`, `unknown_outcome_released`, `evidence_ignored`, `no_record_keeps_the_hold`, `skipped_sample_projected`, `requested_ids_recorded_as_sent`, `completed_run_collected_again`, `permission_not_checked_first`, `questions_alone_send_answers`).
- WR-OBS-3: deleted or expired content read from the raw projection (`content_read_from_the_raw_projection`).
- WR-LSQ-7: a run published under an unregistered evaluator (`b1_evaluator_unregistered`, the fake mirrors D7's `not_found`); an all-revoked run reported `succeeded` (the fake now ends it `failed`, 0034 F4).
- E6L-O1: the clip's digest passed instead of its media key, the span end as its duration, no video inputs at all (`b1_clip_*`).
- E6L-O2: another evaluator's result, the last attempt's cost only, a failed case dropped, a pending case as an error, cluster = case, a slice name split into letters, an unsliced case in a slice, latency dropped (`b2r_*`).
- WR-LSQ-6: a comparison stored with another variant/report, the report or the variant not written first (`r3_store_*`).
- The PG files (`test_submit_ledgers.py[pg]`, `test_runner_pg.py`, `test_checkpoints_pg.py`, `test_control_pg.py`, `test_optimization_pg.py`) are outside the mutant runners (N2/T2I's pattern); their oracles are the fake cases' mutants named above.

## NOT RUN
- **R1 real half**: R1 (rollout-routing, merge batch #6 `22ae31dc`) and SR-R1-1's adapter (`PgRoutingReleases`, `codex/w5-lab-sql-lw3` `1079c328`, 0043) are both absent from the base; `tests/r/routing` does not exist here. Rerun once both merge: `cd apps/infrx-api && INFRX_D_TASK=r1 uv run --frozen pytest -q tests/r/routing` with the routing ReleaseStore swapped to the lw3 adapter.
- **E5L o06** on the e5l block: BLOCKED by the foreign e5l volumes above. Rerun (after WR-LSQ-INT-1 and the owner removes `infrx-e5l_*`): `apps/infrx-api/.venv/bin/python tests/integration/lab_observe/runner.py --out <abs dir> --only o06`. Its semantics are proved in the fake world (`test_j2__deleted_or_expired_content_never_reaches_the_judge`: T3's real `Retention`).
- `tests/i/lab_eval/test_drills_pg.py` (I5's S3) - calls `runner.freeze`, covered by WR-LSQ-7; I5 reruns.

## Wiring requests
- **WR-LSQ-INT-1** (lab-observe / E5L owner; REQUIRED at merge, `JudgeWiring` no longer takes `projection`/`objects`): `tests/integration/lab_observe/observe_world.py` `judge()`:
  ```diff
  -                             projection=trip.traces.projection, objects=trip.traces.objects,
  -                             rates=rates, settings=settings)
  +                             retention=trip.traces.retention, rates=rates, settings=settings)
  ```
  and `tests/integration/lab_observe/mutants.py`: `KNOWN_FAIL = {}` (E5L-F1 fixed) plus a mutant naming `O06_JUDGE` on `SUBMIT`: old `        body = await wiring.retention.read_content(job.grantor_org_id, request_id)`, new `        body = await __import__("infrx.traces.ship.shipper", fromlist=["x"]).read_content(\n            wiring.retention.traces, wiring.retention.objects, job.grantor_org_id, request_id)`. Then the o06 rerun above.
- **WR-LSQ-INT-2** (lab-evaluate / E6L owner): `tests/integration/lab_evaluate/lab_world.py` `case_records` -> `run(reports.case_records(frozen, run(store.run_results(frozen.run.run_id, provider_org_id=NEMO)), self.objects, slice_path="sample.original.slice"))`; j11 now reaches `HttpDevEndpoint` (media refused until L3) - rebind it from FAIL to `NOT RUN[L3]` per the gate's rule. B4 / lab-api-2 (`/lab/v1/evaluations`) builds its comparison input with the same function.
- **WR-LSQ-INT-3** (composition, when the Lab workers compose these): J2 `JudgeWiring(ledger=PgJudgeLedger(connect), retention=<T3 Retention of the trace stack>, ...)`; R2 `Controller(PgReleaseStore(connect), <L3 serving>, actor_id=...)`, the launcher `PgReleaseStore.start(policy_ref, provider_org_id=, plan_digest=control.plan_digest(plan), decided_by=, reason=)`; R3 `optimization.store(PgLabDataStore(connect), variant, comparison, report, provider_org_id=, actor=)`. Every path stays behind its OFF flag.
- No Makefile line: every touched list is already on `api-mutants`.

## Open issues
- Foreign leftovers blocking task-local runs (not touched): `infrx-e5l_*` volumes (rv-lab-observe-0F2); `infrx-r2-postgres` was held by verify-lab-api-2 for part of the session (released later); `infrx-d2-valkey` (the known tests/d `test_outbox_relay[valkey]` blocker) - see api-test below.
- I5's `tests/i/lab_eval` mutant list is `broken_runner` on the base (72-observe-install.sh names `infrx-lab`) - the I5/lab-observe owner's.
- 0004's closed `infrx.reserve_judge`/`record_submission` stubs stay unused (D6J open issue).

## Proposed rulings (coordinator numbers)
- J2 reads judged content only through T3's `Retention` (tombstones and the content bound are the authorization boundary), never the raw projection; `JudgeWiring` carries the Retention.
- B1 registers its evaluator in D7 at `freeze` (0034 R167); a run whose every case failed ends `failed` (0034 F4), in the fake as in D7.
- E6L-O1 as proposed in the E6L evidence (B1 passes `sample.media_ref` = N1's media key and `sample.duration_ms` = `span_ms[1]-span_ms[0]`, keeping `span_ms`); E6L-O2: B2's input is `reports.case_records` from D7's `run_results` + the frozen manifest, slices from a caller-declared path into the case, cluster = the sample's group key.
- R3 stores a comparison only after its variant is published and its B2 report stored (0040's order).

## Estimate (remaining, this lane): 1/2.5/5 h, confidence medium
Basis: one review round (D10-0025 analogue 1/2/5) plus the E5L o06 rerun once the foreign e5l volumes are cleared and the R1 real half once batch #6 and lab-sql-lw3 merge (~1 h each, analogue: this lane's R2 swap took ~0.5 h).

## Fix round (handback 1159cee8, finding 0-LSQI-1)
0-LSQI-1 (major): `JudgeWiring` dropped `projection`/`objects` for `retention`, so the E5L world's `judge()` raises TypeError until WR-LSQ-INT-1 lands. Both files are outside this lane's owned paths, so the fix ships as a ready-to-apply, pre-verified patch for the coordinator to apply in the same merge batch: `research/plan/evidence/coordinator/WR-LSQ-INT-1.patch` (`git apply` from the repo root).
- Contents: `observe_world.py` `judge()` passes `retention=trip.traces.retention` (the property already exists at `observe_world.py:101`); `mutants.py` `KNOWN_FAIL = {}` and a new stack mutant `st_judge_reads_the_raw_projection` (SUBMIT: `wiring.retention.read_content(...)` -> `shipper.read_content(wiring.retention.traces, wiring.retention.objects, ...)`) naming `O06_JUDGE`.

| # | command (worktree root; patch applied as a scratch edit, then reverted) | exit | result |
|---|---|---|---|
| F1 | `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/lab_observe/test_mutants.py` | 0 | 8 passed 1 skipped: new mutant's anchor occurs once, `KNOWN_FAIL={}` coverage holds, every stack case bound |
| F2 | mutant `st_judge_reads_the_raw_projection` applied to `infrx/judge/submit.py`, `pytest -q apps/infrx-api/tests/j/submit/test_submit.py -k deleted_or_expired` | 1 | killed in the fake world (T3's real `Retention`): deleted/expired content reaches the judge; restored -> 1 passed |
| F3 | `git apply --check research/plan/evidence/coordinator/WR-LSQ-INT-1.patch` on 1159cee8 | 0 | applies cleanly |
| F4 | `apps/infrx-api/.venv/bin/python tests/integration/lab_observe/runner.py --out <scratch>/e5l-o06-fix/run --only o06` | 3 | **BLOCKED**, still: foreign `infrx-e5l_{clickhouse,postgres,s3}-data` volumes present (not touched) |

Status: fixed on this lane's side (patch + oracle proof). Still open, and still required before J2 flips to implemented: the coordinator applies the patch at merge, and the o06 rerun (F4 command) is recorded once the owner clears the e5l volumes.
