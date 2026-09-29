# COMPOSITION-4 (LW6): composition batch 4 - WR-P4B-1, the P2 collect caller, WR-DS5-2, WR-E7L-1 / WR-B3-EVALS, WR-P2-4

- Lane `composition-4`, branch `codex/w5-composition-4`, worktree `.claude/worktrees/codex-w5-composition-4`, base `9698e51b`. Code head `b7db806b`; this file and the update JSONs are the next commit.
- One commit per item: `105d5454` (5) WR-P2-4 · `41e8f9cc` (1) WR-P4B-1 · `3cff0744` (2) WR-P4B-2 collect caller · `93fa844c` (3) WR-DS5-2 · `a4aaed42` (4) WR-E7L-1 / WR-B3-EVALS · `17176f58` (4) E7L i07/i08 bound · `b7db806b` fix (two mutant anchors made unique; found by the E4 run).
- **Every switch stays OFF.** `LAB_TEACHERS` is new, default False and never installer-settable (`preflight.NOT_SETTABLE`). `LAB_TEACHER_URL` is new and unset. `LAB_PIPELINES` and `JUDGE_MODE` are unchanged. Nothing reachable from the launched App/API changes with the switches off (E4 below).
- Nothing touched: no migration, `infrx/state/lab_*.py`, `infrx/lab/control`, `apps/lab`, `infrx/rollouts`, `tests/integration/lab_rollout`, Makefile or tasks.json. Nothing hosted, no pilot box, AWS/SSM/S3, Vercel or secret.
- Keys used (one at a time per key): p2 (57528/57529), p3 (57530; its 57531 carried the E4 run's Valkey, `infrx-p3-valkey`), b1 (57520; 57521 carried the mutant run's Valkey, `infrx-b1-valkey`), n3 (57518), e7l (compose block 57300-57399, PG 57332).
- `docker ps -a` shows no `infrx-{p2,p3,b1,n3,e7l}-*` container at exit: every pgharness container is removed at exit, and the E7L stack is torn down by the final runner run.
- Foreign leftovers were not touched: `infrx-d1-postgres`, `infrx-d2-*`, `infrx-t2f-*`, `infrx-b3-postgres` (Created), `infrx-e5l_*` volumes, `infrx-dlab/r2/e8l/i8/q3/m5-*`.

**Deviation (order).** Item 5 (WR-P2-4, N2's redaction helper) was committed first. Item 1's `TeacherWiring(redact=…)` needs it, and the brief's item 1 names it ("see item 5").

## Changed paths

- `infrx/datasets/versions/__init__.py`: `PERSONAL` and `redact_content(value)`, N2's public redaction.
  - No lane owns it: lab-sql-lw6 does not, and datasets-lw5 is merged.
  - It masks emails and `+`-prefixed phone numbers in every string at any depth. Keys, ids and plain numbers are kept.
  - Ponytail ceiling: two shapes.
- `infrx/config.py`: `lab_teachers: bool = False`, `lab_teacher_url: str = ""`.
- `deploy/preflight.py`: `NOT_SETTABLE` += `LAB_TEACHERS`, `LAB_TEACHER_URL`.
- `infrx/gateway/pilot.py`:
  - `_teachers(settings, connect, objects)`, called from `_lab`. It is None when the switch is off. It refuses when on without `LAB_PIPELINES`. It refuses naming `LAB_TEACHER_URL` when the URL is missing or not the local fake (P-10); the value is never echoed.
  - `_lab_2(..., teachers)` passes `teachers=` and `evals=Evaluations(store, objects, access)` into `LabPipelines`.
- `infrx/lab/workers/__main__.py`:
  - The `annotation` role needs `LAB_S3_BUCKET` and `LAB_TEACHER_URL`. It composes `teacher_wiring(..., settings=<its env's pilot settings>, redact=redact_content)`. Its pass `teacher_collect` = `collect_teachers(wiring)` runs every `TEACHER_PASS_S` = 60 s.
  - The datasets reconcile pass passes `restrictions=PgSampleRestrictions(connect)` explicitly.
  - `training` still refuses by name. So does a non-dry-run `LAB_ANNOTATION_TEACHER`.
- `infrx/evaluation/checkpoints/__init__.py`: `Evaluations(store, objects, access, suites=None)`, P3's port over B3/B1.
- `research/plan/08-contracts-v1-encoding.md` §5: the `LAB_TEACHERS` / `LAB_TEACHER_URL` row.
- Tests:
  - `tests/contracts/test_config_and_imports.py`: `DEPLOYMENT_EXPECTED` += 2 (instructed by the brief).
  - `tests/g/test_startup.py`: +1 case; 2 cases extended for `evals`.
  - `tests/g/mutants.py`: +10; 3 re-cut.
  - `tests/g/lab_pipelines/test_lab_teachers_composition_pg.py` (new; p2 proof). `test_lab_teachers_pg`'s fixture is untouched. Its world exposes the composed surface as `app.state.lab`.
  - `tests/w/test_lab_workers.py`: +2 cases; 1 replaced (`annotation_and_training_have_no_pass_and_refuse` became `training_has_no_pass_and_a_teacher_host_needs_its_approval`); 1 extended.
  - `tests/w/lab_workers_mutants.py`: +12; 1 re-cut.
  - `tests/w/test_lab_workers_teachers_pg.py` (new; p2 proof).
  - `tests/w/test_lab_workers_lineage_pg.py` (new; n3 twin).
  - `tests/p/teachers/test_teachers.py`: +1 case.
  - `tests/p/teachers/mutants.py`: +5.
  - `tests/b/checkpoints/test_checkpoints.py`: +1 case.
  - `tests/b/checkpoints/mutants.py`: +8.
- `tests/integration/lab_improve/` (the i07/i08 binds only):
  - new `scenarios_surface.py`: i07 annotation and i08, bound.
  - `scenarios_pending.py`: i07 training (NOT RUN[P-11]) and i09 only.
  - `lab_world.py`: `Lab.surface` / `call` / `suite` / `work` and `Listing`.
  - `runner.py`: i07 lanes `["P-11"]`; i08 lanes `[]`, new title, renamed case.
  - `test_e7l_runner.py`: the lanes assertions.
  - `mutants.py`: `SCENARIO_FILES` += surface; +2 stack mutants.

## Per item: fail-first · what is composed · proof · mutants

**(1) WR-P4B-1**
- **Fail-first (red at base):** `test_lab_teachers__p2_is_composed_under_lab_pipelines_only_when_lab_teachers_is_on` failed with `AttributeError: 'DeploymentSettings' object has no attribute 'lab_teachers'`.
- **Composed:** `LAB_TEACHERS` → `LabPipelines(teachers=teacher_wiring(connect, objects, provider_url=LAB_TEACHER_URL, settings=settings.pilot, redact=versions.redact_content))`. That is:
  - `PgAccessStore`, `PgTeacherLedger`, `PgLabDataStore` and `PgLabelLog` on the pool;
  - the Lab objects;
  - `p1.import_labels`;
  - `APPROVED_RATES`;
  - judge mode not live by default, so an approval is a 503.

  Off, `teachers` is None and `GET teacher-batches` is a typed 503.
- **Real-service proof:** `INFRX_D_TASK=p2 pytest tests/g/lab_pipelines/test_lab_teachers_composition_pg.py` gave 1 passed. It runs P4.b's flow unchanged over `pilot._lab`'s surface, with content carrying `a@b.example` that never reaches the teacher fake.
  - The case's own parts: the session verifier, `TEST_RATES` (the approved table is empty), and `JUDGE_MODE` live.
  - **Red** with `redact=lambda text: text`: `'a@b.example' not in …` failed.
- **Mutants (killed):** `lab_teachers_composed_when_off`, `_not_passed`, `_without_pipelines`, `_unredacted`, `_live_by_default`, `_url_ignored`, `_other_host_escapes`, `_off_the_pool`. Re-cut: `lab_pipelines_ledger_bare`, `_ledger_off_the_pool`, `_objects_not_passed`.

**(2) WR-P4B-2: a production caller for P2 `collect()`**
- **Fail-first (red at base):** 4 `tests/w` cases, red because the role refused ("annotation has no worker pass"):
  - `each_role_refuses…[annotation]`
  - `training_has_no_pass_and_a_teacher_host_needs_its_approval`
  - `the_annotation_role_collects_teacher_batches_with_n2s_redaction`
  - `the_teacher_pass_collects_every_submitted_run_of_every_approved_batch`
- **Composed:** `python -m infrx.lab.workers annotation`'s pass `collect_teachers(wiring)`.
  - It reads every APPROVED batch the Lab route stored (`lab/<p>/teacher-batches/<id>/batch.json` + `approval.json`, R202).
  - For each batch, P2's `plan` gives the chunk runs. Every run whose `PgTeacherLedger` state is `submitted` goes to `p2.collect` as the approver. `collect` imports once, records the per-item failures (WR-P4B-2's `record_failures` is inside `collect`) and settles once.
  - One failure is counted and the next run still runs.
  - R211 stays: without `LAB_TEACHER_URL` (or with another host) the role exits 2 by name.
- **Real-service proof:** `INFRX_D_TASK=p2 pytest tests/w/test_lab_workers_teachers_pg.py` gave 1 passed.
  - The composed gateway surface approves a 2-chunk batch. The composed role collects both: 1 + 2 labels, the malformed answer in D8's failure log, both runs `completed`, `sum(actual)` = 0.02000000.
  - A second pass collects 0 and imports nothing new.
  - **Red** with `is not None` in place of `== "submitted"`: the second pass returned `{'collected': 2}`.
- **Mutants (killed):** `lw_annotation_teacher_url_optional`, `_unredacted`, `_live_by_default`, `_other_host_escapes`, `_pass_idle`, `lw_collect_unapproved_batches`, `_every_state`, `_as_the_requester`, `_one_failure_stops_all`, `_uncounted`.

**(3) WR-DS5-2**
- **Fail-first (red at base):** `test_lab_workers__datasets_reconcile_every_providers_lineage_page_by_page` failed: no `restrictions` was passed.
- **Composed:** the datasets role's reconcile passes `restrictions=PgSampleRestrictions(connect)` on the directory's login.
  - T3's deletion hook already passes it explicitly (`lineage_push(objects, connect)`, 1-C3-1).
  - **No export-evidence route exists** (`lineage.export_evidence` has no caller in `infrx`), so there is nothing to wire; filed below.
- **Real-service proof:** `INFRX_D_TASK=n3 pytest tests/w/test_lab_workers_lineage_pg.py` gave 1 passed. It is `test_lineage_pg`'s selection → revocation → reconcile → re-grant flow, with its one reconcile run by the composed datasets role's pass. 0041 holds the tombstone, and a re-grant resurrects nothing.
  - **Red** with the kwarg dropped: `AssertionError: [None]`.
- **Mutants (killed):** `lw_reconcile_restrictions_implicit`, `lw_reconcile_restrictions_off_the_login`.

**(4) WR-E7L-1 / WR-B3-EVALS**
- **Fail-first (red at base):** `test_b3_p3_evaluations_freeze_one_b1_run_on_the_bundles_dataset_and_answer_d7s_state` (`no attribute 'Evaluations'`), plus the two `lab_api_2` startup cases (`{'ledger','log','store'} != {…,'evals'}`).
- **Composed:** `Evaluations(store, objects, access, suites)`.
  - `evaluate` freezes ONE B1 run on the dataset P3 asks (the bundle's), as the suite's owner, at the suite's harness, evaluator, seed, cases and CREDIT run limit, on the checkpoint's private dev serving.
  - The run id is derived from (subscription, checkpoint, dataset). A lost record resumes the run from D7 and never freezes another.
  - The write-once record `lab/<p>/checkpoints/<id>/evaluation.json` pins the holdout digest of what B1 froze, never the ask's.
  - `evaluation()` adds D7's run state. Only `split="holdout"` is accepted.
  - It is composed into `LabPipelines` under `LAB_PIPELINES` (off) with `suites=None`. There is no source of B3's suite for a P3 checkpoint and no dev deployer (WR-B3-3), so it freezes nothing: `evaluate` is a typed 503, and `evaluation()` reads records.
- **Real-service proof:** E7L gate (below). i08 is bound and PASSes. i07's annotation half is bound and PASSes.
- **Mutants (killed):** `p3_evals_freeze_without_a_suite`, `_digest_copied`, `_suites_dataset`, `_frozen_again`, `_run_per_call`, `_state_stale`, `_any_split`, `_not_the_owner`, `lab_pipelines_evals_absent`, `lab_pipelines_evals_other_objects`. Stack mutants: `st_worker_collects_nothing` (i07) and `st_evaluation_state_stale` (i08).

**(5) WR-P2-4**
- **Fail-first (red at base):** `test_p2__n2s_public_redaction_masks_personal_data_before_a_teacher_sees_it` raised `ImportError: cannot import name 'redact_content'`.
- **Composed:** `versions.redact_content` is the `redact` of both compositions (the gateway's `LAB_TEACHERS` and the annotation role). P2 applies it to each sample's content before J2's egress (unchanged).
- **Proof:** item 1's p2 proof (the red run is without it).
- **Mutants (killed):** `redaction_misses_phones`, `_masks_plain_numbers`, `_skips_lists`, `_skips_objects`, `_rewrites_keys`.

## E7L gate: `tests/integration/lab_improve/runner.py` on e7l

- **Try 1** (`--keep`, scratch out, at the uncommitted bind): **20 PASS / 0 FAIL / 2 NOT RUN** case-level. The kept stack then ran the two new stack mutants: `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e7l pytest tests/integration/lab_improve/test_mutants.py -k 'st_worker_collects_nothing or st_evaluation_state_stale'` gave **2 passed (both killed)**, 142 s.
- **Final** (`--reuse`, torn down after) at `17176f58`, out `research/plan/evidence/e/E7L-raw-17176f5/run/`. Exit 3 (gate NOT RUN), 39.3 s, `pins.dirty: true` (only the raw directory was untracked).
  - Case-level: **20 PASS / 0 FAIL / 2 NOT RUN**. The baseline was 18 / 0 / 4, and no cell regressed.
  - i01–i06 PASS. **i08 PASS**. i07 NOT RUN: the annotation case PASSes and the training case is NOT RUN[P-11]. i09 NOT RUN[staging-target].
  - Cells: **PIPELINE-LINEAGE PASS** (was NOT RUN). DATA-LINEAGE, PIPELINE-BUDGET and TRAIN-RECOVER are NOT RUN, on i09 / i07-training only.
  - `docker ps` shows 0 e7l containers after the run.
- **i07 (annotation, real process):**
  - A teacher batch is planned and approved through the composed surface: 3 chunks, its own payer, 3 teacher POSTs.
  - `python -m infrx.lab.workers annotation` is started with the unit's environment (MinIO objects, the Lab DB, the local teacher fake). It is SIGKILLed once a chunk settled; the kill landed at `[completed, submitted, submitted]` (`cases/…/i07.json`). It is restarted.
  - Result: still 3 POSTs; settled `0.03000000` (once each); no hold left; every label event once; the synthetic labels are exactly the fixture's teacher answers; one failure `(T08, malformed_label)`.
  - Exit codes: -9, then 0 on SIGTERM.
- **i08 (composed `/lab/v1/pipelines`):**
  - Label import (5), then assign + review (all accepted), label export, prepare, submit and finish.
  - Checkpoint upload + import: `validated`, evaluation `queued` on `holdout` with the bundle's pin, not eligible. An early approve is a 409.
  - The eval worker's B1 run succeeds. Approve then gives `eligible: true` with evaluation `succeeded`.
- **Stand-ins, named in `lab_world.Lab.surface`:** the session verifier; the checkpoint listing (WR-LAB2-4); B3's suite source + dev deployer (WR-B3-3); `TEST_RATES`; `JUDGE_MODE` live.
- i01–i06 still use `lab_world.Evaluations` (unchanged: it works its run in-process at evaluate time).
- **The provider UI half of i08** (`apps/lab/tests/e2e/improve/`) is the Lab app's and not bound here (apps/lab not owned).

## Commands (from `apps/infrx-api` unless noted)

| # | command | head | exit | result |
|---|---|---|---|---|
| 1 | fail-first per item (above) | per item | 1 | red as recorded |
| 2 | `pytest -q tests/g/test_startup.py tests/contracts/test_config_and_imports.py tests/i/test_packaging.py tests/g/lab_pipelines` | 41e8f9cc | 0 | 426 passed, 2 skipped |
| 3 | `pytest -q tests/w/test_lab_workers.py tests/i/lab tests/i/lab_pipeline` | 3cff0744 | 0 | 95 passed |
| 4 | `pytest -q tests/b/checkpoints/test_checkpoints.py`; `tests/g/test_startup.py tests/g/lab_pipelines` | a4aaed42 | 0 | 10 passed; 72 passed, 3 skipped |
| 5 | `INFRX_D_TASK=p2 pytest tests/p/teachers/test_teachers_composition_pg.py tests/g/lab_pipelines/test_lab_teachers_composition_pg.py tests/g/lab_pipelines/test_lab_teachers_pg.py tests/w/test_lab_workers_teachers_pg.py tests/p/teachers/test_teachers_pg.py` | 17176f58 | 0 | 6 passed |
| 6 | `INFRX_D_TASK=n3 pytest tests/w/test_lab_workers_lineage_pg.py` | 93fa844c | 0 | 1 passed (red recorded) |
| 7 | `INFRX_D_TASK=p3 pytest tests/p/training/test_training_composition_pg.py tests/b/checkpoints/test_checkpoints_composition_pg.py` | b7db806b | 0 | 3 passed |
| 8 | `INFRX_D_TASK=p2 pytest tests/p/teachers/test_teachers_composition_pg.py tests/g/lab_pipelines/test_lab_teachers_composition_pg.py tests/w/test_lab_workers_teachers_pg.py` | b7db806b | 0 | 3 passed |
| 9 | (repo) `.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/lab_improve`; `INFRX_MUTANTS=all … test_mutants.py` (layer 1) | 17176f58 | 0 | 18 passed, 1 skipped; 22 passed, 27 skipped (stack list without a kept stack) |
| 10 | E7L runner try 1 + the 2 stack mutants on the kept stack; final runner `--reuse` | 17176f58 | 3 / 0 / 3 | 20/0/2 NOT RUN; 2 killed; 20/0/2 NOT RUN (above) |
| 11 | **E4, every switch OFF**: `INFRX_D_TASK=p3 INFRX_D2_VALKEY_PORT=57531 INFRX_D2_VALKEY_CONTAINER=infrx-p3-valkey .venv/bin/python -m pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | 17176f58 | 1 | 2817 passed, 25 skipped, **2 failed**: `tests/w/test_lab_workers_mutants.py::test_every_anchor_is_in_the_source_as_often_as_declared` (`lw_judge_live_by_default` and `lw_report_one_failure_stops_all` anchors appeared twice after item 2) and `test_mutant_is_killed[lw_judge_live_by_default]` (subset run: `misdeclared`, anchor twice). Fixed in `b7db806b` (a local rename, re-cut anchors) |
| 12 | **E4 rerun**, same command | b7db806b | 0 | **2819 passed, 25 skipped, 0 failed** (21m08s). The skips are pre-existing and key/stack-scoped: MinIO (`INFRX_M_S3_ENDPOINT` unset), PG cases of b3/p1/p2/r2/b1, the lab_traces ClickHouse stack, the t2f proof, and 3 empty mutant parameter sets |
| 13 | **MUTANTS**: `INFRX_MUTANTS=all INFRX_D_TASK=b1 INFRX_D2_VALKEY_PORT=57521 INFRX_D2_VALKEY_CONTAINER=infrx-b1-valkey pytest -q -rs tests/g/test_mutants.py tests/w/test_worker_main_mutants.py tests/contracts/test_mutants.py tests/w/test_lab_workers_mutants.py tests/g/lab_datasets/test_mutants.py tests/g/lab_checkpoints/test_mutants.py tests/g/lab_pipelines/test_mutants.py tests/p/teachers/test_mutants.py tests/b/checkpoints/test_mutants.py` | collected at 17176f58 | 1 | 1365 passed, 7 skipped, 4 failed. All 4 are in the lab_workers list, which was collected before `b7db806b` changed its anchors mid-run: the anchor check + `lw_report_one_failure_stops_all`, `lw_annotation_unredacted`, `lw_annotation_live_by_default` `misdeclared` (stale anchors). **0 survivors**. The 7 skips are worker-main mutants that need a MinIO |
| 14 | lab_workers list rerun at the fixed head: `INFRX_MUTANTS=all INFRX_D_TASK=b1 … tests/w/test_lab_workers_mutants.py` | b7db806b | 0 | **76 passed, 0 survivors** (3m14s; every mutant of the list incl. the 12 new, the 4 above now killed) |
| 15 | `uv run --frozen ruff check <every changed .py>` (+ `--line-length 100 tests/integration/lab_improve`) | b7db806b | 0 | All checks passed |
| 16 | `pytest -q tests/i/test_mutants.py tests/m/test_pilot_mutants.py tests/m/test_s3_mutants.py tests/n/lineage/test_mutants.py tests/n/versions/test_mutants.py tests/w/test_worker_main_mutants.py -k 'well_formed or every_case or anchor or declared'` (the other lists anchoring files I touched) | b7db806b | 0 | 16 passed |

**Not run:**
- `make lab-compositions`' j2 and r1 lines: the proofs are key-bound (`INFRX_D_TASK=j2` / `r1`), j2 is not this lane's key, and r1 is held by lab-sql-lw6. Neither `pilot._rollouts` nor the judge role changed. Its p2 and p3 lines ran (rows 7-8).
- `make api-test` in full: E4 plus every touched track's suite and mutant list cover the changed code.
- The App gate: no App-reachable behaviour changed with the switches off.

## Wiring requests (none applied)

- **WR-C4-PREFLIGHT (I6 / infra, needed before the annotation unit can start):**
  - `infra/lab/workers/training/preflight.py` `allowed_names("annotation")` must accept `LAB_TEACHER_URL` (the local teacher fake only until P-10: `http://127.0.0.1:<port>`; its host joins `LAB_EGRESS_ALLOW`).
  - `infra/lab/app/lab.json` and `infra/lab/app/README.md` must list it.
  - The `deploy/lab/pipelines/infrx-lab-annotation.service` comment should say the role collects approved teacher batches.

  Until then the deployed preflight refuses the name, which is correct while off.
- **WR-C4-B3-SUITES (B3 + lab-sql + L3, WR-B3-3):** a production `suites(provider_org_id=, checkpoint_id=)` for P3's checkpoints. It needs:
  - a D7 read of a checkpoint receipt's external run (lab-sql), then B3's subscription of that run (`PgCheckpointLedger.subscriptions`);
  - L3's dev deployer for the serving.

  Then `pilot._lab_2` passes `Evaluations(store, objects, access, suites=…)`. Until then P3's checkpoint import on the gateway is a 503 at `evaluate`.
- **WR-C4-EVIDENCE-ROUTE (N4 / G, WR-DS5-2 remainder):** no export-evidence route exists. When one is built, it calls `lineage.export_evidence(objects, …, restrictions=PgSampleRestrictions(connect))`.
- **WR-LAB2-4 (carried, lab-sql):** `run_rows` / `checkpoint_rows`. i08 uses `lab_world.Listing` as the checkpoint listing stand-in.
- **WR-C4-UI (lab app):** bind the provider-UI half of i08 (`apps/lab/tests/e2e/improve/`) over the same composed surface.
- **WR-C4-MK (Makefile, optional):** add three lines to `lab-compositions`:
  - `p2 tests/g/lab_pipelines/test_lab_teachers_composition_pg.py tests/w/test_lab_workers_teachers_pg.py`
  - `n3 tests/w/test_lab_workers_lineage_pg.py`

  No new mutant list: every list touched is already on `Makefile:21`/`lab-mutants`. The E7L stack list is run by `test_mutants.py` with a kept stack.

## Ruling proposals (unnumbered)

- **Teacher egress is redacted public content.** Whatever leaves the Lab for a teacher model is N2's `redact_content` of the sample's content: email addresses and `+`-prefixed phone numbers are masked at any depth. A teacher composition without it does not exist (`TeacherWiring.redact` is required, and both compositions pass N2's).
- **The annotation worker collects only what an administrator approved.** Only a batch with its approval record (R202) is collected, as its approver. Only `submitted` chunk runs are collected. A completed run is never collected again, and a restart resumes without a second teacher job, import or settlement.
- **P3's evaluation evidence is what B1 froze.** A P3 checkpoint's evaluation is one B1 run per (suite, checkpoint, dataset) on the bundle's dataset. Its holdout digest is computed from the frozen cases, never copied from the request. Eligibility follows D7's run state.

## Open issues

- The annotation role replans each approved batch and lists the Lab objects every pass (a ponytail ceiling). D8's listing of submitted runs (WR-LSQ-C2A) replaces that.
- `LAB_TEACHERS` requires `LAB_PIPELINES`. On its own it refuses startup.
- The i07 kill point is best effort: the kill comes after the first settled chunk. This run landed between chunks, and the artifact records the state.

## Estimate (remaining for this lane to merge)

Optimistic 0.5 h, likely 1.5 h, pessimistic 4 h; confidence medium. Basis:
- all five items are done with fail-first, named mutants and real-service proofs;
- E4 is 2819/0;
- E7L is 20/0/2, with PIPELINE-LINEAGE now PASS;
- composition-3 needed one fix round (about 2 h);
- the remaining work is one verify round plus WR-C4-PREFLIGHT (outside the lane).
