# COMPOSITION-5 (LW6): composition batch 5 - WR-B3-3 / WR-C4-B3-SUITES, WR-R2-3, WR-LSQ-C2A, WR-LSQ-C2B, WR-N4-3, the E6L j09 checkpoint half

- Lane `composition-5`, branch `codex/w5-composition-5`, worktree `.claude/worktrees/codex-w5-composition-5`, base `5ed53a41` (head of `codex/w5-merge-36`). Code head `bd5a2735`; this file, the 08 §5 row, the raw E6L run and the update JSONs are the next commit.
- No plan entry (wave5-plan.md has no `composition-5` row): the brief is the coordinator's notes plus §2 rule 5 and §5 COMMON.
- One commit per item: `4cbeb5fd` (1) WR-B3-3 + WR-C4-B3-SUITES + j09 bind · `60216fcd` (2) WR-R2-3 · `462cb261` (3) WR-LSQ-C2A · `7c8eca1f` (4) WR-LSQ-C2B · `da7e7702` (5) WR-N4-3 · `bd5a2735` fix (an N1 mutant anchor made unique; found by the full mutant run).
- **Every switch stays OFF.** No new setting in `DeploymentSettings`/`preflight`. The only gateway change is inside `LAB_PIPELINES` (default False): P3's `Evaluations` gets the production suites. Everything else is Lab worker roles, which run only as `python -m infrx.lab.workers <role>` from their own env file (R198). E4 below: 2822 passed, 0 failed.
- Nothing touched: no migration, no `infrx/state/lab_*.py`, `infrx/lab/control`, `infrx/rollouts/control` (R216 kept: the pass calls `Controller.step` only), `apps/lab`, `tests/integration/lab_rollout`, `tests/integration/lab_operate`, Makefile or tasks.json. Nothing hosted, no pilot box, AWS/SSM/S3, Vercel or secret.
- Keys used, one at a time per key: p3 (57530; 57531 = the E4 run's Valkey), b1 (57520; 57521 = the mutant run's Valkey), j2 (57511), r2 (57534), n3 (57518), e6l (compose 57200-57299). b3 was not used (the foreign `infrx-b3-postgres` is still there, Created). e8l was not used (`infrx-e8l-postgres` is up: lab-rollout-3's).
- Foreign leftovers were left alone: `infrx-d1-postgres`, `infrx-d2-*`, `infrx-t2f-*`, `infrx-b3-postgres`, `infrx-e8l-postgres`, `infrx-m5-s3`, `infrx-q3-valkey`, `infrx-lab-c2-s3`, the `infrx-e5l_*` volumes. No `infrx-{p3,b1,j2,r2,n3,e6l}-*` container is left at exit.

## Changed paths

- `infrx/evaluation/checkpoints/__init__.py` (B3; additions only):
  - `lab_registry(objects, provider)`: `lab://<provider>/<path>` reads `lab/<provider>/<path>`, and only for the event's own provider. Another provider's object or a missing one gives no bytes, so the checkpoint is rejected `digest_mismatch`.
  - `DevDeployer(reads)`: L3's dev deployer. It picks the provider's newest READY private dev revision whose serving revision pins the checkpoint digest (a weight shard or the adapter) and answers L3's `serving_ref`. If no revision serves the digest yet, it answers a typed 503.
  - `suites(receipts, ledger, deployer)`: D7 receipt → the oldest D8 subscription of that external run → the deployer. No receipt is `not_found`; no subscription is a 503.
  - `pg_receipts(connect)`: one select (a ponytail; WR-C5-RECEIPT).
  - `production_suites(connect)`.
- `infrx/gateway/pilot.py`: `_lab_2` passes `Evaluations(store, objects, access, suites=checkpoints.production_suites(connect))`.
- `infrx/lab/workers/__main__.py`:
  - `checkpoints` needs `LAB_S3_BUCKET` and composes the Lab registry per event provider plus `DevDeployer(PgControlStore)`. A checkpoint without B3's signed event (a P3 one) is acknowledged. Its relay claims `checkpoint_received` only.
  - `rollout` needs `LAB_S3_BUCKET` and `LAB_OPERATOR_ID`. It adds `rollout_pass` every 30 s, `NoLive` and `plan_key`.
  - `judge` adds `judge_collect` every 60 s, `judge_pass` and `provider_ids` (a ponytail; WR-C5-PROVIDERS).
  - `datasets` adds `import_jobs` every 5 s.
- `infrx/worker/__main__.py`: `Kinds(store, kinds)`; `lab_eval`'s relay claims `eval_run` only.
- `infrx/datasets/imports/__init__.py`: `rows_key`, `enqueue`, `work`, `IMPORT_LEASE_S`.
- `research/plan/08-contracts-v1-encoding.md` §5: the "Lab worker roles" row is refreshed.
- Tests:
  - `tests/b/checkpoints/test_checkpoints.py`: +3 cases.
  - `tests/b/checkpoints/mutants.py`: +15.
  - `tests/b/checkpoints/test_checkpoints_sources_pg.py`: new (p3).
  - `tests/b/checkpoints/test_checkpoints_composition_pg.py`: env gets the bucket.
  - `tests/w/test_lab_workers.py`: +5 cases; 2 replaced (the checkpoints and rollout refusals); 3 extended.
  - `tests/w/lab_workers_mutants.py`: +34; 2 removed; 2 re-cut (`lw_checkpoints_payload_provider`, and `lw_collect_uncounted`, whose anchor was made unique).
  - `tests/w/test_lab_workers_mutants.py`: the subset name.
  - `tests/w/test_worker_main.py`: 1 case extended.
  - `tests/w/worker_main_mutants.py`: +3.
  - `tests/w/test_worker_lab_eval_pg.py`: +1 case (b1).
  - `tests/w/test_lab_workers_judge_pg.py` (j2) and `tests/w/test_lab_workers_imports_pg.py` (n3): new.
  - `tests/r/control/test_control_pass_pg.py`: new (r2).
  - `tests/n/imports/test_import.py`: +1 case.
  - `tests/n/imports/mutants.py`: +8.
  - `tests/g/test_startup.py`: 1 case changed.
  - `tests/g/mutants.py`: +2, 2 re-cut.
- `tests/integration/lab_evaluate/` (the j09 bind only):
  - `scenarios_workers.py`: the j09 checkpoint case, bound on the real process.
  - `scenarios_pending.py`: j09 and its tripwire removed; j10 only.
  - `runner.py`: j09 lanes `[]`.
  - `test_e6l_runner.py`: the lanes assertion.
  - `mutants.py`: `checkpoint_tripwire_blind` removed; +2 stack mutants.

## Per item: fail-first · what is composed · proof · mutants

**(1) WR-B3-3 + WR-C4-B3-SUITES**
- **Fail-first (red at base):**
  - `tests/b`: 3 cases, `AttributeError` (no `DevDeployer` / `lab_registry` / `suites`).
  - `tests/w`: 3 cases. One was the WR-B3-3 refusal. Another was the P3 ack (`3 == 2` calls).
  - `tests/g`: `x.evals.suites is None`.
  - The PG proof at the base composition: the role refused `WR-B3-3`, and `evaluate` gave `DependencyUnavailable ... (WR-B3-3)`.
- **Composed:**
  - `python -m infrx.lab.workers checkpoints` runs with nothing injected. It composes D8's `PgCheckpointLedger`, the Lab registry over the role's objects (the event's own provider) and `DevDeployer` over `PgControlStore` (0044's reads) on the same pool.
  - A `checkpoint_received` of a P3 checkpoint (no signed event in D8) is acknowledged. Before this it was redelivered and errored forever.
  - `LAB_PIPELINES` (off): P3's `evaluate` freezes ONE B1 run on the production suite instead of answering 503.
- **Real-service proofs:**
  - `INFRX_D_TASK=p3 pytest tests/b/checkpoints/test_checkpoints_sources_pg.py tests/b/checkpoints/test_checkpoints_composition_pg.py` gave **3 passed**.
    - The role decides a `lab://` checkpoint whose digest an L3 dev revision pins: one queued run, whose serving ref is L3's.
    - `pilot._lab`'s evaluations freeze one run, and a repeat is the same run.
    - A digest no revision serves is a 503; another provider is `not_found`.
  - **E6L j09 checkpoint half, bound:** see "E6L" below.
- **Mutants (killed):**
  - `b3_deployer_any_digest`, `_weights_only`, `_foreign_serving`, `_foreign_revision`, `_any_environment`, `_public`, `_unvalidated`, `_oldest`, `_absent_not_found`.
  - `b3_registry_any_provider`, `_missing_none`.
  - `b3_suites_no_receipt`, `_newest`, `_unsubscribed`, `_digest_unpinned`.
  - `lw_checkpoints_bucket_optional`, `_no_deployer`, `_deployer_off_the_pool`, `_registry_other_provider`, `_registry_other_objects`, `_p3_decided_by_b3`, `_signed_other_provider`.
  - `lab_pipelines_suites_absent`, `_suites_off_the_pool`.
  - Stack: `st_checkpoints_serve_unvalidated`, `st_checkpoints_run_per_delivery`.

**(2) WR-R2-3**
- **Fail-first (red at base):** the rollout case, and the settings case `[rollout]` (the role refused `WR-LSQ-9`).
- **Composed:** `python -m infrx.lab.workers rollout` runs `rollout_pass` every 30 s:
  - It builds `Controller(PgReleaseStore, control_serving(connect, LAB_OPERATOR_ID), actor_id=LAB_OPERATOR_ID)`.
  - Its providers are those with `lab/<p>/releases/` objects.
  - For each provider it lists `PgReleaseStore.releases_in(("running","rolled_back"))`. For each release it reads the stored `plan.json` (R2 checks D9's digest) and D7's policy, then calls `Controller.step`.
  - A running release needs R1's `Live`. In production that source is `NoLive`: R1 records assignments only, with no error, latency, spend or health aggregate, so the release is **held**, never evaluated on invented numbers (WR-C5-LIVE).
  - A rolled-back release converges through `step` (R216's path), never the operator's stop.
  - A release without a plan is held. One failure is counted and the next release still runs.
  - No B2 report is passed: none is linked to a release anywhere (WR-C5-REPORT).
  - `infrx/rollouts/control` is untouched.
- **Real-service proof:** `INFRX_D_TASK=r2 pytest tests/r/control/test_control_pass_pg.py tests/r/control/test_control_pg.py` gave **4 passed**. The pass is the composed role's own, with the objects in memory and R1's `live` injected. It runs over D9 + D7 + L3's real alias reads of the seed (`control_serving`):
  - Without `live`: held.
  - A breach: one D9 decision by `LAB_OPERATOR_ID`, and the alias stays on the baseline.
  - The next pass converges with no second decision.
- **E8L k09 (not run here):** `infrx-e8l-postgres` is up (lab-rollout-3's stack). The rerun command is `apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py --out <dir> --only k09`. k09's pass-loop half still calls `lw.not_run("k09", "WR-R2-3", …)` inside `tests/integration/lab_rollout` (not owned), so it needs WR-C5-K09 below.
- **Mutants (killed):** `lw_rollout_operator_optional`, `_cadence`, `_other_actor`, `_serving_off_the_pool`, `_every_state`, `_any_prefix`, `_planless_stepped`, `_invented_live`, `_live_by_default`, `_held_is_failed`, `_one_failure_stops_all`, `_policy_foreign`.

**(3) WR-LSQ-C2A**
- **Fail-first (red at base):** the judge pass case, plus the judge case's task set.
- **Composed:** `judge_collect` every 60 s, for every provider org (read on the role's login):
  - D6J's `runs_in(("ambiguous",), 100)`: each run is looked up by its submit key. The provider's record moves it to `submitted`. With no record the run is left as it is, with its hold (R184/R192: never released or resent by the platform).
  - Then `runs_in(("submitted",), 100)`: each run gets J2's `collect`.
  - Teacher runs (consented by a dataset ref) are skipped as the annotation role's. One failure is counted.
  - J2's own `reconcile()` (which releases on a missing record) is not called by the pass.
- **Real-service proof:** `INFRX_D_TASK=j2 pytest tests/w/test_lab_workers_judge_pg.py tests/j/calibration/test_calibration_composition_pg.py` gave **2 passed**.
  - A lost-answer run is reconciled and collected in one pass: results stored once, settled once.
  - A never-taken run stays `ambiguous` with its hold.
  - A second pass changes nothing, and the provider's `submit` is called only 2 times.
- **Mutants (killed):** `lw_judge_pass_unscheduled`, `_cadence`, `_providers_off_the_login`, `_ambiguous_released`, `_teachers_collected`, `_submitted_only`, `_one_failure_stops_all`, `_unbounded`, `_other_wiring`.

**(4) WR-LSQ-C2B (R215)**
- **Fail-first (red at base):** 2 cases, `AttributeError: no attribute 'Kinds'`.
- **Composed:** `worker_main.Kinds(store, kinds)` passes 0050's `kinds` to `dispatch_pending`. Every other call is delegated. The eval relay claims `("eval_run",)` (the Lab eval role and the consumer worker's `LAB_EVAL_WORKER`, off). The checkpoints relay claims `("checkpoint_received",)`.
- **Proof:**
  - Fake outbox: the unit cases record the kinds on the claim.
  - PG twin: `INFRX_D_TASK=b1 pytest tests/w/test_worker_lab_eval_pg.py` gave **4 passed**. A `checkpoint_received` beside an `eval_run` is never claimed, errored or acknowledged by the eval relay.
  - **Red** with the filter dropped: `InvalidRequest: no Lab worker handles checkpoint_received`.
  - j09 also asserts that the eval_run event is never claimed by the checkpoints role.
- **Mutants (killed):** `main_lab_eval_claims_every_kind`, `main_lab_eval_other_kind`, `main_kinds_unfiltered`, `lw_checkpoints_claims_every_kind`, `lw_checkpoints_other_kind`.

**(5) WR-N4-3**
- **Fail-first (red at base):** `AttributeError: no attribute 'enqueue'`; the datasets role's task set.
- **Composed:**
  - `imports.enqueue` (the route's half): the rows are written write-once to `lab/<p>/imports/<id>/rows.jsonl`, then ONE `PgLabImportJobs` job per import id, whose spec carries `accept_rejects` and the actor.
  - `imports.work` (the pool's half): claim, then N1's `Importer` as the job's actor, heartbeating every lease/3, then finish once.
    - `succeeded` carries the report.
    - `failed` carries the refusal's text; rejected rows keep their report; a missing upload says so.
    - An infrastructure failure finishes nothing: the lease lapses and the job is reclaimed.
  - The datasets role runs `import_jobs` every 5 s over `PgLabImportJobs` + `PgLabDataStore` on its pool.
- **Real-service proof:** `INFRX_D_TASK=n3 pytest tests/w/test_lab_workers_imports_pg.py tests/w/test_lab_workers_lineage_pg.py` gave **2 passed**:
  - enqueue ×2 is one job;
  - a "dead" worker claims and heartbeats, and another worker's heartbeat is refused;
  - after DB clock +61 s, the composed role's pass reclaims it (attempts 2) and finishes it `succeeded`, publishing one dataset;
  - the dead worker's late finish is refused (`state_conflict`).
- **Mutants (killed):**
  - `n4_rows_not_stored`, `_accept_rejects_forced`, `_other_actor`, `_no_heartbeat`, `_rejected_succeeds`, `_missing_upload_retried`, `_infra_failure_finishes`, `_claims_every_job`.
  - `lw_import_jobs_unscheduled`, `_cadence`, `_off_the_pool`, `_other_objects`.

## E6L: `tests/integration/lab_evaluate/runner.py` on e6l (the j09 bind)

- **The j09 checkpoint case** (`scenarios_workers.py::test_j09_the_checkpoint_worker_drains_the_outbox_once`):
  - A real PgCheckpointLedger subscription and a signed event whose artifact is `lab://NEMO/checkpoints/j09/1.safetensors` in the stack's MinIO. The digest is pinned by a ready private dev revision registered in L3's rows.
  - The real `python -m infrx.lab.workers checkpoints` (env: the Lab DB, MinIO, prefix, health port) reaches `/readyz`, then decides and acknowledges.
  - The event is released and redelivered, and acknowledged again.
  - Counts (receipt, checkpoint events, decisions, D7 runs, eval_run events) are `(1,1,1,1,1)` both times.
  - The decision is `queued` with the derived run id; the receipt is `evaluated`; the run's serving ref is L3's revision's.
  - The eval_run event is never claimed by this role (R215). SIGTERM exits 0.
- **Try 1:** `--only j09 --keep` at the item-(1) tree gave **j09 PASS** (both cases). On the kept stack, `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e6l pytest tests/integration/lab_evaluate/test_mutants.py -k "st_checkpoints or well_formed or every_case"` gave **4 passed** (both new stack mutants killed), 116 s.
- **Final:** `--only j09 --reuse` at `da7e7702` (with the R215 assertion) gave **j09 PASS** (eval case PASS, checkpoint case PASS). The gate is NOT RUN only because nothing else was selected (exit 3). `pins.dirty: false`. Torn down: `removed [infrx-e6l-clickhouse, -postgres, -s3, -valkey]`. Raw output: `research/plan/evidence/e/E6L-raw-da7e770/run/`.
- Layer 1: `pytest tests/integration/lab_evaluate/test_e6l_runner.py scenarios_pending.py` gave 11 passed and 1 failed. The failure is `test_j10_…`: B4's tripwire, red on the base since merge #11 and not this lane's (as in COMPOSITION-2). `INFRX_MUTANTS=all … test_mutants.py -k "not stack"` gave 18 passed.
- **Not run:** the full E6L runner. The other scenarios' code is unchanged: B3 core has additions only, and `scenarios_checkpoint.py` is untouched.

## Commands (from `apps/infrx-api` unless noted)

| # | command | head | exit | result |
|---|---|---|---|---|
| 1 | fail-first per item (above; logs in the lane scratch) | per item | 1 | red as recorded |
| 2 | `pytest -q tests/b/checkpoints/test_checkpoints.py tests/w/test_lab_workers.py tests/g/test_startup.py tests/n/imports/test_import.py tests/w/test_worker_main.py` | da7e7702 | 0* | all pass (*`test_worker_main_pg__*` 2 cases need a key: the default d1 is foreign; they pass in E4 on p3) |
| 3 | `INFRX_D_TASK=p3 pytest tests/p/training/test_training_composition_pg.py tests/b/checkpoints/test_checkpoints_composition_pg.py tests/b/checkpoints/test_checkpoints_sources_pg.py` | da7e7702 | 0 | 5 passed |
| 4 | `INFRX_D_TASK=j2 pytest tests/j/calibration/test_calibration_composition_pg.py tests/w/test_lab_workers_judge_pg.py` | da7e7702 | 0 | 2 passed |
| 5 | `INFRX_D_TASK=n3 pytest tests/w/test_lab_workers_lineage_pg.py tests/w/test_lab_workers_imports_pg.py` | da7e7702 | 0 | 2 passed |
| 6 | `INFRX_D_TASK=r2 pytest tests/r/control/test_control_pass_pg.py tests/r/control/test_control_pg.py` | da7e7702 | 0 | 4 passed |
| 7 | `INFRX_D_TASK=b1 pytest tests/w/test_worker_lab_eval_pg.py` | da7e7702 | 0 | 4 passed (red without the kinds filter recorded) |
| 8 | (repo) E6L j09 `--keep`, the stack mutants, then `--reuse` (teardown) | 4cbeb5fd / da7e7702 | 3 / 0 / 3 | j09 PASS; 2 killed; j09 PASS, torn down |
| 9 | **E4, every switch OFF**: `INFRX_D_TASK=p3 INFRX_D2_VALKEY_PORT=57531 INFRX_D2_VALKEY_CONTAINER=infrx-p3-valkey .venv/bin/python -m pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | da7e7702 | 0 | **2822 passed, 27 skipped, 0 failed** (25m08s). The skips are key- and stack-scoped: MinIO unset, the PG cases of b1/b3/p1/p2/r2/j2, the lab_traces ClickHouse stack, the t2f proof, and 3 empty mutant parameter sets |
| 10 | **MUTANTS**: `INFRX_MUTANTS=all INFRX_D_TASK=b1 INFRX_D2_VALKEY_PORT=57521 INFRX_D2_VALKEY_CONTAINER=infrx-b1-valkey pytest -q -rs tests/g/test_mutants.py tests/w/test_worker_main_mutants.py tests/contracts/test_mutants.py tests/w/test_lab_workers_mutants.py tests/g/lab_datasets/test_mutants.py tests/g/lab_checkpoints/test_mutants.py tests/g/lab_pipelines/test_mutants.py tests/b/checkpoints/test_mutants.py tests/n/imports/test_mutants.py` | da7e7702 | 1 | **1439 passed, 7 skipped, 1 failed** (1h12m): the one failure is `n1_fetch_refusal_escapes` `misdeclared` (its anchor `except errors.DomainError as refused:` appeared twice after item 5), not a survivor. **0 survivors.** Fixed in `bd5a2735`. The 7 skips are worker-main mutants that need a MinIO |
| 10b | `INFRX_MUTANTS=all pytest tests/n/imports/test_mutants.py` (the whole N1 list); `pytest tests/n/imports/test_import.py`; `INFRX_D_TASK=n3 pytest tests/w/test_lab_workers_imports_pg.py` | bd5a2735 | 0 | **68 passed** (every mutant incl. the 8 `n4_*` killed); 17 passed; 1 passed |
| 10c | anchor/declared checks of every other list anchoring a changed file: `tests/i`, `tests/m/pilot`, `tests/m/s3`, `tests/w/prep_worker`, `tests/integration/{lab_improve,lab_rollout,lab_evaluate}`, `backend/recovery` i3b | bd5a2735 | 0 | 12 + 3 + 3 + 3 + 1 passed |
| 11 | per-item mutant subsets during the work (`-k <new names> or well_formed or every_case or anchor or declared`) | per item | 0 | every new mutant killed; the anchor checks pass |
| 12 | `uv run --frozen ruff check <changed .py>`; `--line-length 100 tests/integration/lab_evaluate` | da7e7702 | 0 | All checks passed |

**`make lab-compositions`:** its p3, j2 and n3 lines ran (rows 3-5). Its p2 and r1 lines were not run: p2 and r1 are not this lane's keys, and neither `pilot._teachers` nor R1's routing composition changed.

## Wiring requests (none applied)

- **WR-C5-PREFLIGHT (I6/I5 infra):**
  - `infra/lab/workers/training/preflight.py` `allowed_names("rollout")` += `LAB_OPERATOR_ID`. It is the controller's principal id: a UUID, since the decision record requires one.
  - The rollout unit's env must carry `LAB_S3_BUCKET` and `LAB_OPERATOR_ID`.
  - The checkpoints unit's env (I5) must carry `LAB_S3_BUCKET` (+ `LAB_S3_ENDPOINT`/`LAB_S3_PREFIX`).
  - `infra/lab/workers/eval/RUNBOOK.md` should say the checkpoints role reads `lab://` artifacts and needs an L3 dev revision of the digest.
- **WR-C5-K09 (lab-rollout-3, `tests/integration/lab_rollout/scenarios_recover.py`):** bind k09's pass-loop half.
  - Steps: write `lab/<p>/releases/<policy_id>/plan.json` (the plan whose digest D9 holds), record a rollback in D9 with the alias still on the candidate (a controller killed between the decision and the CAS), then start the bare `python -m infrx.lab.workers rollout` (`LAB_S3_BUCKET`, `LAB_OPERATOR_ID`).
  - Assert: the alias converges on the first pass, with one decision.
  - The breach half ("let it see a breach") needs WR-C5-LIVE.
  - Rerun: `apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py --out <dir> --only k09`.
- **WR-C5-LIVE (R1 + lab-sql):** R1 records assignments only. R2's `Live` needs, per policy revision and arm: requests, errors, p99, quality coverage, spend and candidate health, with an `observed_until`. Until a read exists, the rollout pass holds running releases (`NoLive`).
- **WR-C5-REPORT (R4/B2 + lab-sql):** a link from a release to its stored B2 report (`lab_eval_reports`) and its two runs. Until then the pass passes no report; a running release would hold `no_report`, and a rejecting report cannot roll back through the pass.
- **WR-C5-PLAN (the release launcher, R4):** whoever calls `PgReleaseStore.start(plan_digest=…)` writes the plan write-once to `lab/<p>/releases/<policy_id>/plan.json`. Without it the pass holds the release.
- **WR-C5-RECEIPT (lab-sql):** `PgLabDataStore.checkpoint_receipt(checkpoint_id, provider_org_id=)` → (external run, digest). It replaces `checkpoints.pg_receipts`' one select.
- **WR-C5-PROVIDERS (lab-sql):** a listing of providers with judge work (or `runs_in` across providers). It replaces `provider_ids`' select of `infrx.provider_orgs`.
- **WR-C5-N4-ROUTE (G/N4, `infrx/gateway/routes/lab_datasets.py`):** `POST imports` calls `imports.enqueue(PgLabImportJobs(connect), objects, body, provider_org_id=, actor=user)` instead of `create_task`. `GET imports/{id}` reads `PgLabImportJobs.job(...)`, mapping `succeeded` → `published` with `result`, `failed` with `error`/`result`. The datasets role then works them.
- **WR-C5-ANNOT (optional, composition):** the annotation role could list submitted teacher runs via `runs_in(("submitted",))` (teacher runs share D6J's table) instead of replanning every approved batch (COMPOSITION-4's open issue).
- **WR-C5-MK (Makefile, optional):** `lab-compositions` += `p3 tests/b/checkpoints/test_checkpoints_sources_pg.py`, `j2 tests/w/test_lab_workers_judge_pg.py`, `n3 tests/w/test_lab_workers_imports_pg.py`, `r2 tests/r/control/test_control_pass_pg.py`. `api-mutants`/`lab-mutants` need no new list: every list touched (incl. `tests/n/imports` and `tests/b/checkpoints`) is already in the Makefile.

## Ruling proposals (unnumbered)

- **A checkpoint is served only where L3 validated its digest.** B3/P3 evaluate a checkpoint only on the provider's own READY private dev revision whose serving revision pins that digest (a weight shard or the adapter), resolved through L3's reads. With none, nothing is frozen (503, the event waits). The platform never deploys an artifact L3 has not registered and validated.
- **The Lab registry reads only the event's provider.** `lab://<provider>/<path>` resolves under `lab/<provider>/` of the Lab objects only when `<provider>` is the signed event's provider. Anything else yields no bytes (`digest_mismatch`).
- **The rollout pass never evaluates on invented telemetry.** A running release is evaluated only on R1's recorded aggregates and only on the plan stored beside it (digest = D9's). Without either it is held. A rolled-back release is only converged, through `Controller.step` (R216).
- **The judge worker never releases an ambiguous run.** Its pass only looks the submit key up. A found batch moves to `submitted`; a missing one stays `ambiguous` with its hold (R184/R192). J2's `reconcile` release branch is an operator's tool, not the worker's.
- **An import job finishes once, by its lease holder.** An infrastructure failure never finishes a job; the lease lapses and the job is claimed again. A refusal fails it by name.

## Open issues

- `pg_receipts` and `provider_ids` are single selects in composition glue (ponytail) until lab-sql owns them (WR-C5-RECEIPT / -PROVIDERS).
- The rollout pass lists providers from `lab/<p>/releases/` objects and uses the wall clock as `now` (D9's CAS orders decisions), as `emergency_rollback` does.
- `imports.work` keeps a whole upload in memory, bounded by the route's 64 MiB body (ponytail).
- DevDeployer resolves an L3 revision; it never launches one. A runtime that pulls weights by digest is the fleet's (I4/W3).

## Estimate (remaining for this lane to merge)

Optimistic 0.5 h, likely 1.5 h, pessimistic 4 h; confidence medium. Basis:
- all five items are done with fail-first, named mutants and real-service proofs;
- E4 is 2822/0;
- E6L j09 PASS on the real processes;
- composition-3/-4 each needed about one fix round (about 2 h);
- what remains is one verify round, and the filed requests outside the lane (WR-C5-LIVE/-REPORT/-PLAN gate the rollout breach half and E8L k09).

## Fix round (handback 085a9846)

- **1-C5-1 (major) fixed.** `imports.work` finished a job terminal `failed` on a transient `DomainError` (`DependencyUnavailable` from a throttled or unreachable S3 `objects.get`, `RateLimited`, `InternalError`). A new clause `except (errors.ServerError, errors.RateLimitError)` now sits before the `DomainError` clause. It logs, counts `retry` and finishes nothing, so the lease lapses and the job is claimed again. This matches the docstring and the proposed ruling "An import job finishes once, by its lease holder". The docstring now names the transient case.
- Fail-first: `test_n4_an_import_job_is_enqueued_once_and_worked_by_the_pool_under_its_lease` gains a lapsed-lease pass where `objects.get` raises `DependencyUnavailable`, then `RateLimited`. Each pass asserts `{"succeeded": 0, "failed": 0, "retry": 1}` and that the job is still `running`. At 085a9846 it went red with `{'failed': 1} != {'failed': 0}`.
- Named mutant: `n4_transient_refusal_finishes` in tests/n/imports/mutants.py. It turns the new clause into `except ():` and is killed by the case above.
- Reruns: `pytest -q tests/n` gave 63 passed, 7 skipped (PG needs a key). `INFRX_D_TASK=n3 pytest tests/w/test_lab_workers_imports_pg.py tests/n/imports/test_import_pg.py` gave 4 passed. `pytest tests/w/test_lab_workers.py tests/w/test_lab_workers_imports_pg.py` gave 29 passed, 1 skipped (no key). `INFRX_MUTANTS=all pytest tests/n/imports/test_mutants.py tests/w/test_lab_workers_mutants.py` gave 177 passed with 0 survivors, including the new mutant and `n4_infra_failure_finishes`. Every switch is still OFF. No other path was touched.
