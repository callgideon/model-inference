# api-judge-2 (AP-08 remainder) — evidence at e354988

Lane api-judge-2, wave 7 batch 2 (LW7), branch `codex/w7-api-judge-2`, base `b05eb6f4`, code head
`e354988c` (this file and the update JSON are the next commit). Key `ap8` only (PG 57563,
judge-fake 57564); never d1/55432 by intent, nothing hosted, no box/AWS/SSM/S3/Vercel/secrets.

## Changed paths

- Brief-owned: `infrx/judge/start.py`, `infrx/judge/rubric.py`, `infrx/judge/calibration/goldset.py`
  (new), `infrx/lab/workers/__main__.py` (the judge role only), `tests/ap08/` (new:
  `test_rubric_versions.py`, `test_rubric_store_pg.py`, `test_goldset.py`, `test_judge_role.py`,
  `test_judge_cli_pg.py`, `cli_site/sitecustomize.py`, `sr_ap08_1.sql`; extended: conftest, mutants,
  test_mutants, test_judge_units, test_judge_worker_pg, test_lab_judge_routes, test_rubric_projection).
- AP-08's own batch-1 modules the brief's POST route needs (deviation, no other lane owns them):
  `infrx/gateway/routes/lab_judge.py` (POST /rubrics), `infrx/lab/judge_api/{rubric,service,doors}.py`,
  `infrx/judge/submit.py` (`JudgeWiring.rubric_of`, collect grades with the run's rubric).
- Generated: `apps/infrx-api/openapi/lab-control.json`, `research/plan/api-lifecycle/evidence/
  route-inventory.{json,md}` (`python -m infrx.contracts.openapi.export`), `packages/api-client/src/lab.ts`
  (openapi-typescript 7.13.0; it was already stale at the base for the batch-1 judge family).

## Slices

| Slice | State | What |
|---|---|---|
| Rubric versions | done | `judge.rubric`: `definition()`/`from_definition()` (closed keys, evidence media/text, SOP steps), `digest()` = sha256 of the canonical definition. `POST /lab/v1/judge/rubrics` (201, `Idempotency-Key` validated, the version is the identity): a reviewed definition (`review_ref` required, not part of the digest) becomes ONE immutable stored version; the same digest replays, another is 409 `idempotency_conflict`; a code version (v1) is 409 (immutable); malformed 422. GET lists code + stored `active` (digest, output schema) and pending skeletons `definition_pending` (no digest/schema, `pending_reason`). Configurations pin code or stored versions; a pending one is 409 and never reaches the door. Results of a stored version project with that version (no-media abstention on its media criteria). |
| SOP v2 skeleton (P-07) | done | `SOP_VIDEO_V2` (`sop-video`, v2 reserved): instruction_following + output_validity (text), step_evidence + task_correctness (media); thresholds and steps pending. A v2 definition must fill exactly that skeleton and name its SOP steps. |
| SR-AP08-1 store | proven, not allocated | `tests/ap08/sr_ap08_1.sql` (exact DDL, applied after 0001..0064 by the ap8 seed): `infrx.lab_judge_rubrics` (append-only), `public.lab_judge_rubric_create` (platform operator only, `profiles.is_operator`), `public.lab_judge_rubric_list` (viewer+ of the provider), `infrx.lab_judge_rubric_of` (worker: the run's version + stored definition), `infrx.lab_judge_results_of` (calibration: COMPLETED runs, CURRENT grant, that configuration only). EXECUTE: the two doors to `infrx_lab_control`; browsers nothing. |
| Worker: rubric per run | done | `JudgeWiring.rubric_of` (None = code registry, as before); `start` refuses a run whose pinned rubric is absent or of another version; `collect` grades with the run's rubric, none gradable = StateConflict (never silently v1). |
| Gold-set calibration | done | `judge.calibration.goldset`: a reviewed reference set (provider, grantor, judge model, rubric version, reviewer, review_ref, one verdict per sample id - ids only) -> J3's operator `calibration_label` rows in memory -> J3 `report` over the configuration's settled results -> D8 `put_calibration`. `insufficient` below MIN_PAIRS (30) pairs, limited results never pair (R56). |
| Judge role (WR-2) | done in-branch | `_judge`: the provider gets `egress_hosts(JUDGE_MODE, JUDGE_PROVIDER_ALLOWLIST)` (https + listed host + live only; loopback otherwise); `rubric_of` composed; `JUDGE_GOLD_SET` loaded at start (malformed/missing = refuse to start) and graded after every collect pass; the START job (`start.start_pass` over 0064's queue) is composed only when `start.eligible_read(limits)` returns AP-07's read - None on this base (WR-AP08-2b). tests/w's mutant anchors kept verbatim (`jobs` dict). |
| End-to-end worker proof | done (isolated) | `test_judge_cli_pg.py`: real `python -m infrx.lab.workers judge` subprocess on ap8: queued via the doors -> process 1 `/readyz` 200, START freezes the sample, reserves, ONE batch to the judge fake, `submitted`, SIGTERM exit 0 -> process 2: no second send, COLLECT stores 2 accepted results and settles (`completed`), CALIBRATION publishes; the Lab door answers `insufficient`, labels 1 (one media pair; limited never pair); consumer CREDIT unchanged. Declared fakes: the trace store + AP-07's eligible read in memory, the fake judge at J2's TEST rates (`cli_site/sitecustomize.py`). NOT a live pass. |

## Commands (exit, counts)

Red seam runs (before each implementation; logs in the lane scratchpad):
- `pytest tests/ap08/test_rubric_versions.py` -> exit 1, 12 failed (`from_definition`/`PENDING`/`SOP_VIDEO_V2` absent, `state` missing).
- `INFRX_D_TASK=ap8 pytest tests/ap08/test_rubric_store_pg.py` -> exit 1, 1 failed (`start.pg_rubric_of` absent), 1 passed (the SR DDL itself).
- `pytest tests/ap08/test_goldset.py` -> collection error (module absent).
- `INFRX_D_TASK=ap8 pytest tests/ap08/test_judge_cli_pg.py` with the BASE `__main__.py` -> exit 1:
  "the start pass sending the run never happened (exit 0)" (no start job in the role).
- After the first full mutant run: 8 broken_runner/misdeclared (undeclared KeyError/ValidationError/RuntimeMisconfigured deaths; one stale anchor) -> deaths declared where the defect is the raise, one test asserts before indexing -> all killed.
- First Supabase-image run: the CLI proof raced its own calibration wait (process 1 publishes `uncalibrated`) -> the wait names a calibration with labels > 0 -> green.

Green:
- `INFRX_D_TASK=ap8 uv run --frozen pytest -q tests/ap08 --ignore=tests/ap08/test_mutants.py` -> exit 0, **62 passed** (plain postgres:16).
- `INFRX_D1_IMAGE=supabase INFRX_D_TASK=ap8 … same` -> exit 0, **62 passed** (SR-AP08-1 proven on both images).
- `INFRX_D_TASK=ap8 INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap08/test_mutants.py` -> exit 0, **88 passed**: 62/62 Python mutants killed (33 new), 13/13 0064 SQL, 11/11 SR-AP08-1 SQL (new in-process runner), well-formed + every-case green (36 runner-visible cases).
- `INFRX_MUTANTS=all pytest tests/w/test_lab_workers_mutants.py -k "lw_judge or lw_report"` -> exit 0, 26 passed; `-k every_anchor` green; `pytest tests/w/test_lab_workers.py -k judge` -> 5 passed.
- `pytest tests/w tests/j tests/contracts tests/g/test_startup.py tests/i -m 'not pg' --ignore-glob='*test_mutants*.py'` -> exit 1, 2436 passed, 11 failed: 1 = tests/w's judge anchors (fixed, then green above); 10 = `*_pg` cases in tests/w (prep_worker, w5, worker_main) and tests/contracts/v2/test_v1_projection_pg that default to d1 and REFUSED (`HarnessBusy`: another lane holds /tmp/infrx-d1-postgres-55432.lock; nothing touched). Unrelated to this diff; not re-run on a key.
- `pytest tests/integration/test_makefile_mutant_lists.py` -> 7 passed.
- `make api-lint` -> All checks passed. `make api-typecheck` -> **458** (baseline 458).
- `packages/api-client`: `pnpm typecheck` 0; `pnpm test` 5 pass, 1 fail = "generated clients are current" on **consumer** only (consumer.ts stale at the base b05eb6f4; lab.ts current after regeneration) -> WR-AP08-3.
- `python3 research/plan/scripts/validate_plan.py` -> PASS.

## Schema request SR-AP08-1

Exact DDL = `apps/infrx-api/tests/ap08/sr_ap08_1.sql` (130 lines; header says what/why/rollback). Move it
unchanged into the allocated LOCAL-ONLY migration (api-schema-2's 0066, or 0064 amended before the
window), then: drop the `conn.execute(sr_sql() if sr is None else sr)` line from `tests/ap08/conftest.py:seed`
(keep the `sr` parameter only if the SR mutants stay on the test file) and turn `mutants.SR_MUTANTS` into
`_s(...)` 0064-style entries on the migration file. The hosted window re-proof (R151/R269) carries it.
Without it, `GET/POST /lab/v1/judge/rubrics` and the judge role's `rubric_of`/gold-set reads fail closed
(undefined function -> 500/failed pass) — LAB_JUDGE_API is off and the judge unit is disabled, so it must
land with or before this merge.

## Wiring requests

- WR-AP08-2a (the judge role): applied in-branch in `infrx/lab/workers/__main__.py` `_judge` (the brief
  lists the judge role as owned); the exact patch is `git diff b05eb6f4..e354988c -- apps/infrx-api/infrx/lab/workers/__main__.py`.
  Composed tests: `tests/ap08/test_judge_cli_pg.py` (the process) + `tests/ap08/test_judge_role.py`.
- WR-AP08-2b (after api-traces-2 merges `infrx.traces.eligible`): `infrx/judge/start.py:eligible_read`
  returns that read bound to the trace projection, signature `(grantor_org_id, model_id, limit) ->
  [(request_id, has_video)]` (full traces with stored content, CURRENT external_judging grant).
  Patch shape: `from ..traces import eligible as traces_eligible; return traces_eligible.<factory>(limits)`.
  Composed test: `test_ap08_role__the_start_job_needs_the_eligible_read` without the monkeypatch asserts
  `judge_start` in the tasks; the CLI proof on the ap7/e5l stack without `cli_site`'s eligible fake.
- WR-AP08-3: regenerate `packages/api-client/src/consumer.ts` (`pnpm generate`), stale at the base.
- WR-AP08-4 (AP-09 api-frontends-lab, informational): RubricDoc gains `state`, `digest`, `review_ref`,
  `pending_reason`, `sop_steps`; `output_schema` is nullable; `POST /lab/v1/judge/rubrics` is operator-only.
- `infra/lab/rollout/steps/50-lab-role` still refuses JUDGE_MODE other than dry_run (coordinator, with P-10).

## Live external judging: BLOCKED (P-10) — exact operator configuration

1. Approved judge provider + model with video modality; its `ProviderRate` row (price_version, per-MTok
   input/output, source document, effective_at) added by review to `infrx/judge/cost.APPROVED_RATES`
   (empty: `GET /lab/v1/judge/models` answers `unavailable`).
2. A provider adapter for its batch API that also sends the run's rubric (criteria, evidence kinds, SOP
   steps) - `HttpJudgeProvider` speaks only the fake's `/batches` and sends no rubric, so only v1-shaped
   output is gradable today; the credential as an SSM parameter NAME read by the role.
3. Judge role env (`/etc/infrx-lab/judge.env`, 50-lab-role SPEC): `JUDGE_MODE=live`,
   `JUDGE_LIVE_BUDGET_USD=<positive USD cap>`, `JUDGE_PROVIDER_URL=https://<host>`,
   `JUDGE_PROVIDER_ALLOWLIST=<host>` (bare names; ignored unless live), optional
   `JUDGE_GOLD_SET=<path of the reviewed reference set>`, plus LAB_DATABASE_URL, LAB_WORKER_HEALTH_PORT=8017,
   CLICKHOUSE_URL, S3_TRACE_BUCKET; 50-lab-role must admit `live`.
4. Database: `lab_submission` flag on; the payer `lab:payer:<provider_org_id>:<id>@sha256:<64 hex>` with a
   PROVIDER_USD limit set by an administrator (`PUT /lab/v1/judge/budgets/{payer_ref}`); the grantor's
   CURRENT external_judging grant over request+response content naming the model; 0064 + SR-AP08-1 applied
   in an R151 window.
5. P-07: the SOP definition (steps + thresholds) POSTed as rubric v2 by a platform operator with its
   review_ref; a reviewed gold set with >= 30 binary media pairs before any `calibrated` claim.
6. AP-07's eligible read composed (WR-AP08-2b); until then the role starts nothing.

## Proposed ruling (unnumbered; next free R272)

Rubric versions are platform data: one integer namespace (1..1000) shared by every provider; a version is
immutable once it exists (code-registered versions cannot be shadowed by a stored one); a stored version
is created only by a platform operator from a reviewed definition whose review reference is recorded; a
reserved version (the SOP v2 skeleton) is listed `definition_pending`, cannot be configured, and is filled
only with its fixed criteria/evidence shape.

## Open items / deviations

- Edited AP-08's batch-1 modules beyond the brief's list (route, judge_api, submit.py) - required by
  POST /rubrics and per-run grading; edited the judge role in place (also given as WR-AP08-2a).
- SR-AP08-1 is a test-applied DDL file, not a migration (R271: only the schema owner writes migrations).
- The CLI proof's trace store, eligible read and rates are declared fakes; a stored rubric other than v1
  is not exercised through the fake judge (P-10 adapter).
- Estimates' `eligible` stays null (the Lab unit does not read the trace store).

## Estimate (remaining for AP-08 incl. merge/wiring)

optimistic 2 h, likely 4 h, pessimistic 8 h; confidence medium. Basis: this lane ~6 h (the 4/6/10 band);
remaining = SR-AP08-1 allocation + both-image re-proof, WR-AP08-2b after api-traces-2, generated-artifact
merge conflicts, review fixes. The live pass is excluded (P-10/P-07).
