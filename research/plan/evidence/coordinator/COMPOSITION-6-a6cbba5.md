# COMPOSITION-6 (LW6): composition batch 6 - WR-R4-2, WR-C5-N4-ROUTE, WR-C5-RECEIPT, WR-C5-PROVIDERS, WR-C5-PLAN, WR-C5-REPORT, the merge #42 lens minors

- Lane `composition-6`, branch `codex/w5-composition-6`, worktree `.claude/worktrees/codex-w5-composition-6`, base `8949ffb1`. Code head `a6cbba5d`; this file and the update JSON are the next commit.
- No plan entry (wave5-plan.md has no `composition-6` row): the brief is the coordinator's notes plus §2 rules 3-5 and §5 COMMON.
- The lens minors' text was found in the composition-5 verify output (`wdrv38s9a`, findings 0-F1..0-F4, 1-C5-2, 1-C5-3), not re-derived.
- One commit per item:
  - `868ff366` (1) WR-R4-2
  - `8c8eb68f` (2) WR-C5-N4-ROUTE
  - `4a16520f` (3) WR-C5-RECEIPT
  - `c03848b6` (4) WR-C5-PROVIDERS + lens 0-F2
  - `722390b6` (5) WR-C5-PLAN
  - `64beb75e` (6) WR-C5-REPORT
  - `9d0ba0b8` (7) lens minors 0-F1, 1-C5-2, 0-F3 + the preflight case
  - `a6cbba5d` fix: one gateway mutant anchor re-cut (found by the full run) + the 08 §5 row.
- **Not done: WR-COMP-4 (blocked) and WR-C5-LIVE (needs rulings).** Reasons and the exact requests are below.
- **Every switch stays OFF.** There is no new setting in `DeploymentSettings`, preflight, `infra/lab/app/lab.json` or the README.
  - The gateway changes are inside `LAB_RELEASES` (default False) and `LAB_DATASETS` (default False).
  - The rest is Lab worker roles and their operator subcommands, which run only as `python -m infrx.lab.workers rollout …` from a role's env file.
  - E4 below: **2828 passed, 0 failed**.
- **One new migration, `0053_lab_composition_reads.sql`: LOCAL-ONLY (R150/R151/R201), declared in its header.**
  - It is additive: three SECURITY DEFINER read functions, with EXECUTE for service_role through 0004's defaults. No table, column, constraint or grant changes, and no existing function is redefined.
  - 0001-0052 are untouched. Nothing is applied hosted. `infra/rollout/hosted-migrate.sh` `EXPECTED_PENDING` and `known-good.json` stay at 0051 (0053, like 0052, is not in the R151 window).
- Nothing else was touched:
  - no `infrx/rollouts/control` (R216 kept: the pass and `decide` call `Controller.step` / `emergency_rollback` only);
  - no `apps/lab`, no `tests/integration/*`, no Makefile, no tasks.json;
  - no hosted system, pilot box, AWS/SSM/S3, Vercel or secret.
- Keys, one at a time per key:
  - r2 (57534): D checks, SQL mutants and the release/R2 PG proofs;
  - n3 (57518): the dataset proofs;
  - p3 (57530; 57531 = the E4 run's Valkey): the checkpoint proofs and E4;
  - j2 (57511; 57512 = the gateway mutant run's Valkey): the judge proofs and the gateway mutants.
- No `infrx-{p3,r2,n3,j2}-*` container is left at exit. Foreign containers were not touched (d1 was never used; see the note on row 12).

## Changed paths

- `apps/app/supabase/migrations/0053_lab_composition_reads.sql` (new, LOCAL-ONLY):
  - `lab_release_decisions {provider_org_id}` (WR-R4-2);
  - `lab_checkpoint_receipt {provider_org_id, checkpoint_id}` (WR-C5-RECEIPT);
  - `lab_providers_with {work: judge|release, states}` (WR-C5-PROVIDERS).
- `infrx/state/lab_rollout.py`: `PgReleaseStore.decisions`, `.providers_in` (additions).
- `infrx/state/lab_data.py`: `PgLabDataStore.checkpoint_receipt` (addition).
- `infrx/state/lab_consent.py`: `PgJudgeLedger.providers_in` (addition).
- `infrx/gateway/pilot.py`:
  - `lab_releases(connect, sessions, access, objects)` builds `ReleaseRecords` (D9's listing + 0053's decisions, D7's policy, the stored plan), `ReleaseProposals` (0043) and `PgReleaseStore`. This applies only under `LAB_RELEASES`.
  - `LAB_DATASETS` gets 0051's `PgLabImportJobs` on the pool.
- `infrx/gateway/routes/lab_releases.py`: a proposal carries `proposed_by` (the session's user; 0043 records it).
- `infrx/gateway/routes/lab_datasets.py`:
  - `POST imports` enqueues one 0051 job; `GET imports/{id}` reads it (`shown`).
  - The in-process import tasks, their job table and `objects_for` are removed.
  - Without a queue the import routes answer 503.
- `infrx/evaluation/checkpoints/__init__.py`: `production_suites` reads D7's receipt through `PgLabDataStore.checkpoint_receipt`; `pg_receipts` is removed.
- `infrx/datasets/imports/__init__.py` `work`: the import runs as a task beside its heartbeat. A lost lease (a refused heartbeat, or a refused finish) stops or leaves that job, logs it and counts `retry`; the pass continues.
- `infrx/lab/workers/__main__.py`:
  - `rollout decide` and `rollout launch` subcommands;
  - `release_report` (WR-C5-REPORT);
  - the judge pass uses `ledger.providers_in(JUDGE_WORK)` and the rollout pass uses `releases.providers_in(ROLLOUT_STATES)`; `provider_ids` is removed.
- `research/plan/08-contracts-v1-encoding.md` §5: the "Lab worker roles" row is refreshed.
- Tests:
  - new: `tests/d/test_c6_reads.py`, `tests/d/test_c6_judge.py`, `tests/d/code_mutants_c6.py`, `tests/d/test_code_mutants_c6.py`, `tests/g/lab_releases/test_lab_releases_composition_pg.py`, `tests/g/lab_datasets/test_lab_datasets_imports_pg.py`, `tests/r/control/test_control_report_pg.py`;
  - changed: `tests/g/test_startup.py` (+1 case, 2 cases extended), `tests/g/mutants.py` (+17, 1 re-cut), `tests/g/lab_releases/{test_lab_releases,mutants}.py` (+1 assertion, +1 mutant), `tests/g/lab_datasets/{test_lab_datasets,mutants}.py` (+1 case, the queue in the world, +5 with 1 replacing `import_job_any_provider`, 3 re-cut), `tests/w/test_lab_workers.py` (+4 cases, 2 extended), `tests/w/lab_workers_mutants.py` (+29, 2 removed as replaced, 2 re-cut), `tests/w/test_lab_workers_judge_pg.py`, `tests/n/imports/{test_import,mutants}.py` (+2 cases, `FakeJobs.job`, +5), `tests/i/lab_pipeline/{test_preflight,mutants}.py` (+1 case, +2), `tests/b/checkpoints/test_checkpoints_sources_pg.py` (0-F3).

## Per item: fail-first, what is composed, proof, mutants

**(1) WR-R4-2: the release surface's records and proposal ports, and the operator's decision through D9's CAS**
- The proposal storage WR-R4-2 asked for already exists in 0043: `lab_release_proposals`, the unique partial index `(policy_ref) where state='proposed'`, and `lab_decide_release_proposal` (D9's transition at the proposal's fence and the proposal state in one transaction). So the only schema needed was 0053's decision listing.
- **Fail-first (red at base):**
  - `tests/d/test_c6_reads.py` without 0053 gave 2 failed (`lab_release_decisions` absent).
  - `tests/g/test_startup.py -k "lab_releases__the_surface or composed_from_settings"` gave 2 failed (no `ReleaseRecords`; `LabReleases` built with no ports).
  - `tests/w/test_lab_workers.py -k decides` gave 1 failed (no `decide`).
- **Composed (`LAB_RELEASES`, off):** `LabReleases(sessions, access, records=ReleaseRecords(D9, D7, objects), proposals=ReleaseProposals(PgReleaseProposals), store=PgReleaseStore)`, all on the gateway's pool.
  - `releases`: D9's `releases_in(running, approved, rolled_back)` (the three states port.ts shows). Each row gets D7's policy revision (endpoint, version, baseline, mode, cohort, candidates) and the plan the launcher stored (`plan_key`). A release whose plan is not stored is a typed 503 naming WR-C5-PLAN, never a guessed plan.
    - `progress` is null: R1's aggregates are not readable (WR-C5-LIVE).
    - `verdict` is D9's latest decision.
    - Times are shown as UTC `…Z`.
  - `decisions`: 0053's listing.
  - `variants`: a typed 503. R3's variant listing is not written (WR-C6-VARIANTS below).
  - Proposals: 0043's `lab_propose_release`, where the proposer is the session's user, then shaped to the Lab's `Proposal`.
- **Operator:** `python -m infrx.lab.workers rollout decide --policy-ref R --proposal-id P --approve|--reject --reason T` as `LAB_OPERATOR_ID`.
  - The proposal is looked up among the ref's provider's proposals for that release.
  - A rejection moves nothing.
  - An approved rollback is 0043's decision, carrying R2's `lab.rollout_decision.1` with reasons `operator:<reason>` and `proposal:<id>`. R2's `emergency_rollback` then converges the alias; its CAS finds the release already rolled back and makes no second decision.
  - An approved expansion is refused by name (it needs R2's `expand` verdict on R1's aggregates, WR-C5-LIVE).
  - A stale fence is exit 1 and the proposal stays pending.
- **Real-service proof:** `INFRX_D_TASK=r2 pytest tests/g/lab_releases/test_lab_releases_composition_pg.py` gave **1 passed**. The route is mounted over `pilot.lab_releases` on real D9/D7/0043/L2, and both releases are launched by `rollout launch`:
  - the page lists the release with its stored plan, no progress and no verdict;
  - a developer's proposal is 403;
  - an expansion without an expand verdict is 409;
  - a rollback proposal is stored once, and a second is 409 (0043's one-pending index);
  - `rollout decide --approve` makes ONE D9 decision at fence 1→2 by the operator. The alias is still on the baseline. The page shows `rolled_back` with that decision and the proposal `approved`;
  - a second decide is exit 1;
  - a rejected proposal leaves its release running at fence 1.
- **SQL:** `tests/d/test_c6_reads.py`:
  - `check_browser_roles_reach_nothing`: every browser session is refused 42501 on 0053's three functions;
  - `check_decisions_are_the_providers_own_oldest_first`: own provider only, decisions only, oldest first (the frozen seed clock is advanced), `[]` for none, and the store reads it.
- **Mutants (killed):**
  - gateway: `lab_releases_records_absent`, `_proposals_absent`, `_store_absent`, `_off_the_pool`, `_other_objects`, `_every_state`, `_plan_guessed`, `_policy_foreign`, `_verdict_dropped`, `_budget_unnamed`, `_time_not_utc`, `_variants_invented`, `lab_proposals_proposer_dropped`, `_fence_zero`, `_other_provider`;
  - route: `proposer_not_the_session`;
  - worker: `lw_decide_without_operator`, `_other_release`, `_other_provider`, `_reject_approves`, `_expand_without_live`, `_other_actor`, `_reasons_unnamed`, `_no_converge`, `_refusal_escapes`, `_unparsed`;
  - SQL: `c6_browser_reads_decisions`, `c6_decisions_any_provider`, `_with_starts`, `_newest_first`, `_reasons_lost`.

**(2) WR-C5-N4-ROUTE**
- **Fail-first:** `tests/g/lab_datasets/test_lab_datasets.py` with the queue in the world gave 6 failed and 1 passed (the route ignored `jobs`, ran imports in-process and had no `shown`).
- **Composed (`LAB_DATASETS`, off):**
  - `POST imports` calls `imports.enqueue(PgLabImportJobs(connect), objects, body, provider_org_id=, actor=user)`: the rows are written write-once and ONE job is created per import id (a re-POST is the same job). Nothing is published by the gateway.
  - `GET imports/{id}` reads `PgLabImportJobs.job` for the path's provider:
    - queued/running shows `running`;
    - `succeeded` shows `published` with its report, and the version is then listed;
    - `failed` with error `rejected` (`imports.work`'s refused rows) shows `rejected` with the report;
    - any other failure shows `failed` with its reason.
  - Without a queue both routes answer 503.
- **Real-service proof:** `INFRX_D_TASK=n3 pytest tests/g/lab_datasets/test_lab_datasets_imports_pg.py` gave **1 passed**. The surface is built by `pilot._lab(LAB_DATASETS)` on n3:
  - two POSTs make one 0051 row, `created_by` = the session user;
  - GET shows `running`;
  - the composed datasets role's pass gives `{succeeded 1}`;
  - GET shows `published` with 5 accepted rows, and the version is listed;
  - another provider's member is refused.
- **Mutants (killed):** `import_running_as_published`, `import_rejected_as_failed`, `import_error_beyond_failure`, `import_queue_invented`, `import_job_other_provider` (replaces `import_job_any_provider`, whose in-memory table is gone), `lab_datasets_jobs_absent`, `lab_datasets_jobs_off_the_pool`. Re-cut: `actor_not_the_session`, `published_not_listed`, `body_before_identity`, `lab_datasets_objects_elsewhere`.

**(3) WR-C5-RECEIPT**
- **Fail-first:** `check_a_checkpoint_receipt_is_read_for_its_own_provider` gave 1 failed (no RPC, no store method).
- **Composed:** `checkpoints.production_suites` reads `PgLabDataStore(connect).checkpoint_receipt` (0053). It returns `(external run, digest)`, or None for another provider's or an unknown id, and the suites answer `not_found`. `pg_receipts`' own select is gone.
- **Proof:** `INFRX_D_TASK=p3 pytest tests/b/checkpoints/test_checkpoints_sources_pg.py tests/b/checkpoints/test_checkpoints_composition_pg.py tests/p/training/test_training_composition_pg.py` gave **5 passed**.
- **Mutants (killed):** `c6_receipt_any_provider`, `c6_receipt_browser_reads`.

**(4) WR-C5-PROVIDERS + lens 0-F2**
- **Fail-first:**
  - both new D checks failed (2 failed);
  - `tests/w -k "judge_pass_reconciles or rollout_pass"` gave 2 failed.
- **Composed:**
  - The judge pass lists `PgJudgeLedger.providers_in(("ambiguous", "submitted"))`: the providers with judge work, on its own ledger.
  - The rollout pass lists `PgReleaseStore.providers_in(("running", "rolled_back"))` from D9, no longer from `lab/<p>/releases/` objects. A release with no stored plan is counted `held`, including one whose provider has no Lab object at all (0-F2: the report shows the gap). `provider_ids` is removed.
- **Proofs:**
  - `INFRX_D_TASK=j2 pytest tests/w/test_lab_workers_judge_pg.py tests/j/calibration/test_calibration_composition_pg.py` gave **2 passed**;
  - `INFRX_D_TASK=r2 pytest tests/r/control` gave **41 passed** (incl. `test_control_pass_pg`, now on D9's provider listing).
- **Mutants (killed):**
  - `lw_judge_pass_providers_off_the_ledger`, `_providers_other_states` (replace `_providers_off_the_login`);
  - `lw_rollout_providers_other_states` (replaces `lw_rollout_any_prefix`);
  - re-cut `lw_judge_pass_submitted_only`, `lw_rollout_every_state`;
  - SQL: `c6_release_providers_any_state`, `c6_providers_unbounded`, `c6_providers_other_work`, `c6_judge_providers_any_state`, `c6_judge_providers_are_releases`.

**(5) WR-C5-PLAN**
- **Fail-first:** `tests/w -k launched` gave 1 failed (no `launch`).
- **Composed:** `python -m infrx.lab.workers rollout launch --policy-ref R --plan <plan.json> --reason T` runs as `LAB_OPERATOR_ID` over the Lab bucket:
  - R2's `Plan` is read from the file; anything else is refused (exit 2) before a write;
  - the plan is stored write-once at `plan_key(<provider>, <D7 policy id>)`, THEN `PgReleaseStore.start(plan_digest=plan_digest(plan))`;
  - a replay stores the same bytes;
  - another plan for a stored one is a conflict, and D9 is not asked.
- **Proof:** the WR-R4-2 PG proof above launches both releases with it, and the REPORT proof below launches one.
- **Mutants (killed):** `lw_launch_without_bucket`, `_plan_unvalidated`, `_plan_not_stored`, `_plan_elsewhere`, `_digest_other`, `_other_actor`, `_refusal_escapes`, `_unparsed`.

**(6) WR-C5-REPORT**
- **Fail-first:** `tests/w -k b2_report` gave 1 failed (the pass passed no report).
- **Composed:** `release_report`, called for a running release only (a rolled-back one reads none: converge only).
  - It takes the provider's NEWEST B4 experiment (0043 `lab_experiments`) with a stored B2 report, under the plan's own protocol (sha256 of the canonical protocol, B2's digest), whose runs are D7's records. The baseline run must serve the policy's baseline and the candidate one of its candidates.
  - The runs are dumped `exclude_unset`, so their `lab.ref_of` is the stored ref. A plain `model_dump` adds `payer_ref: null` and would make every report `report_runs_unbound`; this was caught while testing.
  - None found: no report, and R2 holds `no_report`. R2 re-checks the binding itself.
- **Real-service proof:** `INFRX_D_TASK=r2 pytest tests/r/control/test_control_report_pg.py` gave **1 passed**.
  - Setup: real D7 dataset, harness, evaluator and two created runs; two B4 experiments of the same runs, an accepting report under another protocol and a rejecting one under the plan's; the release is launched by `rollout launch`; R1's aggregates are injected, healthy on every bound.
  - Result: ONE D9 rollback by the operator, evidence `[base_run_ref, cand_run_ref]`, reasons `["quality_reject", "slice:van"]`. The other-protocol report was never used.
- **Mutants (killed):** `lw_report_never_read`, `_rolled_back_reads`, `_any_protocol`, `_unreported`, `_other_servings`, `_oldest`, `_run_refs_changed`, `_off_the_pool`.

**(7) The merge #42 lens minors**
- **0-F1 (heartbeat cadence):**
  - New case `test_n4_the_default_heartbeat_keeps_a_slow_import_leased`: no `beat_s`, lease 0.3 s, a 0.25 s import, and a heartbeat arrives before the lease lapses.
  - It passes at base (a guard). Its red is the reviewer's hand mutant, now named `n4_default_cadence_past_the_lease` (`lease_s * 2`), which is killed.
- **1-C5-2 (a lost lease is never noticed):**
  - `work` runs the import as a task beside the heartbeat.
  - A refused heartbeat cancels that import and logs "the lease was lost". A refused finish (`StateConflict`) is logged and counted.
  - Both count `retry` (the report keeps its shape), and the pass continues.
  - Case `test_n4_a_lost_lease_stops_the_import_and_the_pass_goes_on` (4 jobs: a refused heartbeat, a refused finish, 2 good) gave `{succeeded 2, retry 2}`. It was red at base (the StateConflict escaped `work`).
  - Mutants (killed): `n4_lost_lease_imports_on`, `n4_lost_import_not_stopped`, `n4_lost_lease_silent`, `n4_lost_finish_escapes`.
- **0-F2:** done in item (4).
- **0-F3:** `test_checkpoints_sources_pg.py` no longer catches `InvalidRequest` around the pump. It asserts every `eval_run` outbox event is unclaimed after it (R215). On p3 it gave 2 passed.
- **0-F4 (record only):** the flaky `test_worker_lab_eval_pg` revocation case (a killed attempt charged twice, 1-2 of 17-20 runs) is B1/worker-main's. It is filed as WR-C6-B1-FLAKE; this lane made no change.
- **1-C5-3:** not this lane's (`tests/integration/lab_rollout` belongs to lab-rollout-4, which runs WR-C5-K09). The same staleness now applies to k10's tripwire text (WR-C6-K10 below).
- **allowed_names test:** `test_i6_only_the_rollout_role_names_its_operator_principal`:
  - `LAB_OPERATOR_ID` ∈ `allowed_names("rollout")` and a rollout env with it passes;
  - `training` and `annotation` refuse it by name.
  - It is green at base (a guard for merge #42's wiring). Mutants `i6_pf_operator_refused` and `i6_pf_operator_any_role` are killed.

## Not done (by name, with why)

- **WR-COMP-4: BLOCKED. Not a composition wiring on this base.**
  - The gateway has no capture seam. Nothing on the request path opens a `TraceSink` capture.
  - Every request's trace policy is `off_mode_policy`: `IngressDeps.consent_for` is None in `pilot`. Nothing reads `infrx.consent_history` for an org's trace mode on the runtime path (validate.py: "T2/C1 supply the real source").
  - So a gateway-built `SpoolTraceSink` would store nothing for any request, and E5L o01's "capture on through the switch" still could not PASS.
  - Moving the ship step alone (gateway lifespan: rotate+ship every 10 s; the worker keeping retention + projection) would change the launched gateway's process model for no observable gain.
  - What it needs is a T/G lane (WR-C6-CAPTURE below): (a) a consent source; (b) the capture hook of `research/traces/05-gateway-capture-spec.md` §4.5-4.6 on every terminal path; (c) a ruling for async jobs, whose output is produced by the worker, which cannot write the gateway's spool (one writer per directory); (d) one gateway process per spool directory.
  - o01 stays NOT RUN[COMPOSITION]; its rerun command is unchanged (E5L-1d62678.md).
- **WR-C5-LIVE: NOT DONE. It needs rulings, not wiring.**
  - The data exists: `lab_rollout_assignments` (R1, 0033/0043) ⋈ `infrx.jobs` (state, admitted/terminal times) ⋈ settlement. Three of R2's `Live` inputs have no ruled source:
    - (a) `spent`: consumer jobs settle in CREDIT, while a plan's budget may be PROVIDER_USD, and R2 refuses to mix units. Provider-funded canary is P-12.
    - (b) `quality_covered`: feedback rows, judge results, or both?
    - (c) `candidate_healthy`: L3's deployment state, or error bursts?
  - `rollout_routing` is OFF (P-12), so no assignment exists outside tests anyway.
  - Until then the pass keeps holding running releases (R228), the page shows `progress: null`, and `rollout decide --approve` of an expansion is refused by name.
  - A ruling proposal and the RPC shape are below (WR-C6-LIVE).

## E4 (every switch OFF) and the mutant runs

| # | command (from `apps/infrx-api`) | head | exit | result |
|---|---|---|---|---|
| 1 | fail-first per item (logs in the lane scratch `c6/*-red*.log`) | per item | 1 | red as recorded above |
| 2 | `INFRX_D_TASK=p3 INFRX_D2_VALKEY_PORT=57531 INFRX_D2_VALKEY_CONTAINER=infrx-p3-valkey .venv/bin/python -m pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | 9d0ba0b8 | 0 | **2828 passed, 28 skipped, 0 failed** (21m43s). The skips are key/stack-scoped as in COMPOSITION-5 (MinIO unset, other keys' PG cases, the t2f proof, empty mutant parameter sets) |
| 3 | `INFRX_MUTANTS=all INFRX_D_TASK=j2 INFRX_D2_VALKEY_PORT=57512 INFRX_D2_VALKEY_CONTAINER=infrx-j2-valkey pytest tests/g/test_mutants.py` | 9d0ba0b8 | 1 → 0 | 490 passed, 1 failed: `lab_datasets_objects_elsewhere` **misdeclared** (its anchor, the old `LabDatasets(...)` call, changed in item 2), not a survivor. Re-cut in `a6cbba5d`: `-k lab_datasets_` gave 6 passed. **0 survivors, 491 killed** |
| 4 | `INFRX_MUTANTS=all INFRX_D_TASK=r2 pytest tests/w/test_lab_workers_mutants.py tests/g/lab_datasets/test_mutants.py tests/g/lab_releases/test_mutants.py tests/n/imports/test_mutants.py tests/i/lab_pipeline/test_mutants.py tests/b/checkpoints/test_mutants.py tests/d/test_code_mutants_c6.py` | 9d0ba0b8 | 0 | **412 passed, 0 failed** (16m13s): every mutant killed, incl. the 12 SQL ones of 0053 |
| 5 | anchor-count script over every Python mutant list anchoring a changed file (b/checkpoints, g, g/lab_*, m/pilot, n/imports, w/lab_workers, w/worker_main, d6j/d7/d8/d9 CODE) | a6cbba5d | 0 | 996 anchors, `BAD []` |
| 6 | `pytest tests/integration/lab_{evaluate,improve,observe,rollout}/test_mutants.py -k "well_formed or every_case or anchor or declared or known"` (repo root) | 9d0ba0b8 | 0 | 3 + 3 + 4 + 3 passed |
| 7 | `INFRX_D_TASK=r2 pytest tests/d/test_c6_reads.py tests/d/test_c6_judge.py tests/d/test_d9_release.py tests/d/test_d9_rollout.py tests/d/test_d7_lab_data.py tests/d/test_d7_followup.py tests/d/test_d6j_judge.py tests/d/test_upgrade_lab.py tests/r tests/g/lab_releases` | 9d0ba0b8 | 0 | **177 passed**, 4 skipped (r1-only routing PG) |
| 8 | `INFRX_D_TASK=n3 pytest tests/w/test_lab_workers_imports_pg.py tests/n/imports/test_import_pg.py tests/g/lab_datasets/test_lab_datasets_imports_pg.py` | 9d0ba0b8 | 0 | 5 passed |
| 9 | `INFRX_D_TASK=p3 pytest tests/b/checkpoints/test_checkpoints_sources_pg.py tests/b/checkpoints/test_checkpoints_composition_pg.py tests/p/training/test_training_composition_pg.py` | 4a16520f / 9d0ba0b8 | 0 | 5 passed / (sources) 2 passed |
| 10 | `INFRX_D_TASK=j2 pytest tests/w/test_lab_workers_judge_pg.py tests/j/calibration/test_calibration_composition_pg.py`; `… tests/j/submit -k pg` | c03848b6 | 0 | 2 passed; 7 passed (the 7 `[pg]` params that error without a key) |
| 11 | `pytest tests/n tests/i/lab_pipeline tests/b/checkpoints tests/g/lab_datasets tests/w/test_lab_workers.py`; `pytest tests/i/test_known_good_proof.py tests/i/test_migrate.py tests/i/test_release_bundle.py tests/i/lab_pipeline tests/i/lab_rollout tests/i/lab` | 9d0ba0b8 | 0 | 184 passed, 13 skipped (other keys' PG); 95 passed (0053 does not move the R151 proofs, which stop at 0051) |
| 12 | `uv run --frozen ruff check <every changed .py>` | a6cbba5d | 0 | All checks passed |

- `make -n lab-compositions`: **11 pytest lines** (unchanged). The new PG files are WR-C6-MK.
- `make api-test` (the whole suite, ~1.7 h on l4 in E5L) was not run. The affected tracks ran in rows 2-11.
- Note on row 12's neighbour: an anchor check run without `INFRX_D_TASK` picked one `d6jj` SQL mutant through `-k declared`. pgharness refused the foreign d1 container (it never adopts a container it did not create), so nothing on d1 was touched. Every later D run named its key.

## Wiring requests (none applied)

- **WR-C6-MK (Makefile):**
  - `lab-compositions` += `r2 tests/g/lab_releases/test_lab_releases_composition_pg.py tests/r/control/test_control_report_pg.py`, `n3 tests/g/lab_datasets/test_lab_datasets_imports_pg.py` (the existing shape: `cd apps/infrx-api && INFRX_D_TASK=<key> .venv/bin/python -m pytest -q <files>`);
  - `api-mutants` += `INFRX_D_TASK=r2 … tests/d/test_code_mutants_c6.py` (0053's SQL list: 12 mutants, needs Docker), beside the other `test_code_mutants_*` lines.
  - Every other list touched is already in the Makefile.
- **WR-C6-CAPTURE (T/G lane; replaces WR-COMP-4 as filed):**
  - (a) A consent source for the gateway: `IngressDeps.consent_for` over the org's current `infrx.consent_history` row, cached per org (the capture spec's NF9: the auth row carries the flags). This is a read the runtime login may make; a runtime grant or RPC is a migration.
  - (b) The capture hook on the request path: open, add and finish on every terminal path (05-gateway-capture-spec §4.5-4.6).
  - (c) A ruling on where an async job's output is captured, since the worker cannot write the gateway's spool.
  - (d) When the gateway owns the spool: its lifespan runs `spool.rotate(); shipper.ship()` every 10 s, `trace_pumps` drops `trace_ship` and keeps `trace_retention` + `feedback_projection`, and there is one gateway process per `TRACE_SPOOL_DIR` (`SpoolTraceSink`'s lock).
  - Then E5L `runner.py --only o01`.
- **WR-C6-LIVE (R1 + lab-sql, after rulings):** an RPC `lab_release_live {policy_ref}`, per arm (baseline vs candidate by `serving_ref`) over `lab_rollout_assignments ⋈ infrx.jobs` (terminal states):
  - `requests`; `errors` (failed/expired; cancelled excluded);
  - `p99_ms` (`percentile_disc(0.99)` of terminal − admitted);
  - `spent` in the plan's unit;
  - `quality_covered`; `candidate_healthy`;
  - `observed_until` (the DB clock at read).
  - The pass's `live` becomes that read. Needs the ruling proposal below.
- **WR-C6-VARIANTS (lab-sql, R3):** a listing of the provider's `lab.optimization_variant.1` records with both identities and their latest `infrx.variant_comparison.1` (0040). `/lab/v1/optimizations` answers 503 until then.
- **WR-C6-K10 (lab-rollout, `tests/integration/lab_rollout/scenarios_pending.py`):** k10's tripwire text ("composes LabReleases with no records or proposal port") is now stale. Bind k10's port half to `pilot.lab_releases` (records, proposals and D9 composed) plus `rollout decide`, the way `test_lab_releases_composition_pg.py` does, and leave the UI half on lab-e2e's harness.
- **WR-C6-REQUEUE (N/lab-sql, product):** with the durable queue a failed or rejected import id is terminal. A re-POST returns the same job (0051's replay). The Lab's "import again under the same import id to resume" copy (`apps/lab/lib/services/datasets/views.ts`) therefore needs either a new import id or a requeue RPC for failed jobs.
- **WR-C6-B1-FLAKE (B1/worker-main, lens 0-F4):** `tests/w/test_worker_lab_eval_pg.py` revocation case `assert len(c.wallet.calls) == 1` fails 1-2 of 17-20 runs (a killed attempt charged twice).

## Ruling proposals (unnumbered)

- **A Lab proposal is decided only through D9's CAS, by an operator.** `rollout decide` applies 0043's decision at the proposal's fence (a stale fence refuses and leaves it pending). An approved rollback carries R2's decision record by the operator, then R2's stop converges the alias. An expansion is approved only on R2's `expand` verdict over R1's recorded aggregates, and never while they are unreadable. A decided proposal is never decided again.
- **A release is launched with its plan stored first.** The launcher stores R2's plan write-once beside the release before D9 starts it with that plan's digest. A stored plan is never replaced. A release without a stored plan is never evaluated, shown with a guessed plan, or silently skipped: the pass counts it held and the page answers 503 naming it.
- **A release's B2 report is the provider's newest experiment under the plan's protocol that compares the policy's baseline with one of its candidates**, the runs being D7's records as stored. No other report is ever handed to R2, and R2 re-checks the binding.
- **The durable import queue is the only import path at the gateway.** The gateway enqueues and reads; the I5 datasets pool imports. A lost lease stops that worker's import and is never finished by it.
- **(for WR-C6-LIVE, proposed for the coordinator/user)** R2's `Live` for a release is read from R1's recorded assignments and the admitted jobs they name, per arm:
  - requests and errors (failed/expired) of terminal jobs;
  - p99 of terminal − admitted;
  - spend in the unit the jobs settled in: a plan whose budget is another unit is refused, never converted;
  - quality coverage = candidate requests with an operator or customer feedback row (T2F);
  - health = the candidate's L3 deployment revision is `ready`;
  - `observed_until` = the database clock at read.

## Open issues

- WR-COMP-4 and WR-C5-LIVE as above. Both gate E5L o01 and the E8L k09 breach half.
- The release page answers 503 if any listed release lacks its stored plan (one un-launched release darkens the page). This is by design (fail closed, like the Lab's own record check). With `rollout launch` as the only launcher, only releases started before it hit this.
- The page's `verdict` is D9's latest decision, not R2's latest `evaluate` (hold/expand are not persisted). An expansion proposal therefore needs WR-C6-LIVE plus a stored latest verdict (R4-swap's proposed ruling).
- `lab_providers_with` scans `lab_judge_runs` / `lab_rollouts` by state (distinct providers). This is a ponytail: add an index on `(state, provider_org_id)` if a table grows past a pass's budget.
- The `/versions` listing still comes from the route's in-memory `published` (WR-N4-2's ponytail). An import published by the pool is noted when this gateway process reads its job; `PgLabReads.datasets` exists for the real listing.

## Estimate (remaining for this lane to merge)

Optimistic 0.5 h, likely 1.5 h, pessimistic 4 h; confidence medium. Basis:
- six of the eight WR items are done with fail-first, named mutants (903 run, 0 survivors) and real-service proofs on four keys;
- E4 is 2828/0;
- composition-3/-4/-5 each needed one fix round (about 1-2 h);
- what remains is one verify round.
- WR-COMP-4 (a T/G lane, about 1-2 days once the consent and async-capture rulings exist) and WR-C5-LIVE (0.5-1 day after its ruling) are outside this estimate.
