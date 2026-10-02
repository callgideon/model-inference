# api-judge (AP-08) — evidence at 7c383a0

Lane api-judge, wave 7 (LW7), branch `codex/w7-api-judge`, base `cd9f517c`, code head `7c383a0e`
(this file and the update JSON are the next commit). Key `ap8` (PG 57563, judge-fake 57564); no
other port, never d1/55432, nothing hosted, no AWS/SSM/S3/Vercel/secrets.

## Changed paths (all owned)

- `apps/app/supabase/migrations/0064_judge_api.sql` (new, LOCAL-ONLY header; R271's allocation)
- `apps/infrx-api/infrx/lab/judge_api/{__init__,doors,service,rubric}.py` (new)
- `apps/infrx-api/infrx/gateway/routes/lab_judge.py`, `lab_reviews.py` (new; not mounted — WR-1)
- `apps/infrx-api/infrx/judge/start.py` (new), `judge/submit.py` (P-10 seam), `judge/rubric.py` (`RUBRICS`)
- `apps/infrx-api/tests/ap08/` (conftest, 6 test files, `mutants.py`, `test_mutants.py`)

## Why 0064 (R271: "only if a read has no function")

Reads with no door: provider's configurations (list/detail), runs with ledger state (0043's
`lab_judge_runs` is per-request only), a run's results, budgets. Writes with no door: keyed
configure / keyed budget (0037 mints a fresh config id / limit version per call, so
`Idempotency-Key` replay needs them), cancel, provider human trace reviews (no store), the worker's
START read (`infrx.lab_judge_queued`). Plus EXECUTE for `infrx_lab_control` on the session doors
(the Lab unit's login is a member of no role and held none of 0037/0038's). Tables:
`lab_judge_cancellations`, `lab_trace_reviews` (append-only, provenance `human` by CHECK).
Authenticated/anon gain nothing (R271: web apps call FastAPI).

## Slices

| Slice | State | What |
|---|---|---|
| 08a | done (unmounted) | `/lab/v1/judge/models|rubrics|configs[/{id}]|budgets[/{payer}]|estimates|runs[/{id}[/results|/cancel]]|calibration`; doors run as the session user (`SessionDoors`: transaction-local PostgREST claims, both GUC forms, no `set role`); run/config/review identity = uuid5(action, workspace, user, Idempotency-Key); 409 on same key/other body; R270 envelopes incl. request validation (`R270Route`); no-store; 202+Location (OperationDoc) for runs; PROVIDER_USD as `Money`; opaque keyset cursors |
| 08b | done | `judge_api/rubric.py`: rubric doc with per-criterion evidence (media/text) + judge output JSON schema; result projection: media criterion without video = `abstained/no_media`, never a pass; `Rejected` = `quarantined` with reason code only. SOP rubric v2 NOT added (no SOP definition input; the collector grades v1 only) |
| 08c | done against the fake | `judge/start.py`: queued (or reserved-never-sent) requests -> seeded frozen sample -> J2 `submit` as the requester. Live external pass BLOCKED (P-10) |
| 08d | done against the fake | restart = no second send; ambiguous never resent (reconciled by lookup); budget exhaustion, dry-run, revocation, cancel send nothing; consumer CREDIT unchanged |
| 08e | done | human review stored once (`lab_trace_reviews`, provenance `human`, reviewer = session user), read beside customer signals (0038) in `GET /lab/v1/traces/{id}/feedback`; calibration `calibrated` only with labels >= required and an agreement, else `insufficient`/`uncalibrated` |

## Commands (exit, counts)

- Red seam runs recorded before each implementation: `tests/ap08/test_judge_doors_pg.py`,
  `test_rubric_projection.py`, `test_lab_judge_routes.py`, `test_judge_worker_pg.py`,
  `test_egress_allowlist.py` -> collection error (module absent) each, exit 2/1.
- `INFRX_D_TASK=ap8 INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap08` -> exit 0, **81 passed**
  (37 cases incl. 14 PG on ap8; 29/29 Python mutants killed; 13/13 0064 SQL mutants killed;
  well-formed + every-case green).
- `INFRX_D1_IMAGE=supabase INFRX_D_TASK=ap8 pytest tests/ap08 --ignore=test_mutants.py` -> exit 0,
  37 passed (real supabase/postgres@sha256:7768d0d1…, no shim).
- `INFRX_D_TASK=ap8 pytest tests/ap08/test_egress_allowlist.py tests/j/submit` -> 54 passed;
  `INFRX_MUTANTS=all pytest tests/j/submit/test_mutants.py -k "any_host or well_formed or every_case"` -> 3 passed
  (J2's `any_host_egresses` anchor kept intact).
- `INFRX_D_TASK=ap8 pytest tests/d/test_schema_postgres.py tests/d/test_credit_schema.py
  tests/d/test_code_mutants_lw8.py tests/d/test_d6j_doors.py tests/d/test_d6j_judge.py
  tests/d/test_d6f_doors.py -k "not sql_mutant and not mutant_is_killed"` -> exit 0, 66 passed
  (function-privilege map, Lab-login grant set unchanged in `infrx`).
- `INFRX_D_TASK=ap8 pytest tests/contracts/v2/test_v1_projection_pg.py` -> 3 passed.
- `pytest tests/i tests/contracts tests/j tests/g/test_startup.py -m "not pg"` -> 2094 passed,
  48 failed: 2 are the migration pins (WR-4), 43 are `tests/i/test_mutants.py` broken_runner from
  the same pin failing their pristine baseline (proved: with 0064 moved aside both pins pass and an
  i-mutant's baseline is clean; with it present the baseline names only
  `test_known_good_proof::…hosted_migrate_expects_exactly…`), 3 were `test_v1_projection_pg`
  run without the key (refused d1's foreign container, touched nothing; green on ap8 above).
- `make api-lint` -> All checks passed. `make api-typecheck` -> **458** (baseline 458; no new errors).

## Wiring requests (coordinator)

WR-1 Lab unit mount, switch default OFF. `infrx/config.py` DeploymentSettings: add
`lab_judge_api: bool = False` (env `LAB_JUDGE_API`). `infrx/lab/control/app.py`: in `_compose`'s
namespace add `lab_judge=(JudgeApi(SessionDoors(connect)) if settings.deployment.lab_judge_api else None)`
(imports `from ..judge_api.doors import SessionDoors`, `from ..judge_api.service import JudgeApi`) and
`actors=<AP-01 SessionActors>`; in `create_app` add `lab_judge, lab_reviews` to the imported routes and
call `lab_judge.register(app, rt); lab_reviews.register(app, rt)`. Composed test: create_app with the
three INFRX_LAB_* env + `LAB_JUDGE_API=1` lists `/lab/v1/judge/runs` and
`/lab/v1/traces/{request_id}/reviews` in `app.routes`; without it neither path exists (404).

WR-2 Judge worker (`infrx/lab/workers/__main__.py:_judge`): `provider=HttpJudgeProvider(url,
allowed_hosts=egress_hosts(limits.judge_mode, env.get("JUDGE_PROVIDER_ALLOWLIST", "")))` and a job
`"judge_start": lambda: every(JUDGE_PASS_S, lambda: start.start_pass(start.pg_queued(connect), wiring,
eligible), "judge start")` where `eligible(grantor, model, limit)` is the trace store's read of that
model's `full` traces with stored content -> `(request_id, has_video)` (AP-07's ClickHouse projection;
until composed, the START job is not added). Composed test = `tests/ap08/test_judge_worker_pg.py`'s
world through `python -m infrx.lab.workers judge` with JUDGE_PROVIDER_URL at the ap8 fake.

WR-3 `Makefile` api-mutants: add `tests/ap08/test_mutants.py` (`tests/integration/test_makefile_mutant_lists.py`).

WR-4 Migration pins (every wave-7 migration lane hits them; R269 keeps `hosted-migrate.sh` for the
window): `tests/i/test_known_good_proof.py::test_ops_recover__hosted_migrate_expects_exactly_the_migrations_after_its_anchor`
and `tests/i/lab/test_lab_rollout_steps.py::test_ldp__todays_hosted_migrate_carries_the_reviewed_patch`
fail with 0064 present (and fail `tests/i/test_mutants.py` baselines). Either the window's reviewed
`EXPECTED_PENDING` edit lands with the merge, or the coordinator records them as expected-until-window.

WR-5 Move `R270Route` + `invalid` (routes/lab_judge.py) into `infrx/gateway/control.py`: every new
family needs request-validation errors as the R270 envelope.

## Live external judging: BLOCKED (P-10)

Exact operator configuration needed (none present; nothing guessed):
1. An approved judge provider + model with video modality, and its rate row (`ProviderRate`:
   price_version, per-MTok input/output, source document, effective_at) added to
   `infrx/judge/cost.APPROVED_RATES` by review (today empty: every estimate is unpriced, `GET /models`
   answers `unavailable: no approved judge rate (P-10)`).
2. A provider adapter for that provider's batch API (HttpJudgeProvider speaks only the local fake's
   `/batches` protocol), and the credential as an SSM parameter name read by the judge role (never env literal).
3. Judge role env: `JUDGE_MODE=live`, `JUDGE_PROVIDER_URL=https://<host>`, `JUDGE_PROVIDER_ALLOWLIST=<host>`
   (the seam: https only, live only, bare host names), `JUDGE_LIVE_BUDGET_USD>0`; and
   `infra/lab/rollout/steps/50-lab-role` must stop refusing JUDGE_MODE other than dry_run.
4. DB: `lab_submission` flag on; the provider payer budget (PUT budgets, administrator); the grantor's
   current `external_judging` grant over request+response content for the model; 0064 applied in an
   R151 window.
5. A test-owned clip set with an SOP definition and human reference labels (>= 30, J3's MIN_PAIRS) before
   any calibrated claim.

## Open items / deviations

- POST `/rubrics` not offered: rubrics are code-reviewed versioned data (`judge.rubric.RUBRICS`); a
  provider-authored rubric store is new scope. SOP rubric v2 waits on an SOP definition.
- Estimates: `eligible` is null (the Lab unit does not read the trace store); `samples_max` = config bound.
- Cursors are opaque base64 keysets, unsigned (`ponytail:`); AP-02's HMAC cursor replaces them.
- Budget PUT idempotency is the key digest in the limit version's reason (no second idempotency store);
  AP-00's `control_idempotency` (0060) can replace it when composed.
- The judge collector still grades with MARLIN_VIDEO_V1 regardless of config (only v1 is registered).

## Estimate (remaining for AP-08 incl. merge/wiring)

optimistic 3 h, likely 6 h, pessimistic 12 h; confidence medium. Basis: code + proofs done here (~9 h of
the 6/10/16 band spent); remaining = WR-1/WR-2 composition with AP-01's SessionActors and AP-07's trace
read, the Lab adapter migration (AP-09), and review fixes. The live pass is excluded (P-10).
