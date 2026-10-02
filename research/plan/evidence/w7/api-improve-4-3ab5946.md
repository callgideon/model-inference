# AP-10 (lane api-improve-4, wave 7 batch 4): 10e routes + composed AP-04 test, register row 105

- Base `29b9df84`. Branch `codex/w7-api-improve-4`. Code head `3ab5946e`; the evidence commit follows it.
- Slice commits:
  - `4d1871ab`: row 105, the from-traces outcome read;
  - `99f9f9f7`: 10e fix, one import per checkpoint is refused before AP-04 starts another;
  - `22e4529c`: 10e, the R270 training-run routes (`routes/lab_improve.py`);
  - `3ab5946e`: 10e, the composed AP-04 world on ap10, plus the regenerated OpenAPI artifact and client.
- Key `ap10`: PostgreSQL 57566. MinIO used 57572 only while WR-AP10E-2 was applied transiently (see below). `make lab-improve` ran on its own E7L block.
- No d1/55432 container was used. Nothing hosted, no box, no AWS/SSM/Vercel, no secret. No migration was written.
- Nothing is enabled:
  - The training-run routes mount only on `rt.lab_improve`, which nothing composes until WR-AP10E-1. That wiring rides LAB_ARTIFACTS, which is off.
  - The outcome read mounts with the datasets family beside the POST. It answers 503 without a composed ControlOps.
  - `LAB_TRACE_DATASETS` (the worker pass) stays off, so no operation succeeds in production.

## Changed paths (all owned)

- `apps/infrx-api/infrx/gateway/routes/lab_datasets.py`:
  - the outcome read `GET .../from-traces/{operation_id}` and its `TraceDatasetRead` model;
  - the POST's scoping moved into `scoped()` with the same lines, so the existing `rt_*` mutants still apply.
- `apps/infrx-api/infrx/gateway/routes/lab_improve.py` (new): the 10e routes.
- `apps/infrx-api/infrx/lab/improve/release.py`: `register_candidate` checks the key before AP-04 starts an import.
- `apps/infrx-api/tests/ap10/`:
  - `test_from_traces_route.py` (+1 case);
  - `test_release.py` (an assertion added to an existing case);
  - `test_improve_routes.py` (new, 4 cases);
  - `test_improve_composed_pg.py` (new, 1 case; outside the mutant runner, like the other `_pg` files);
  - `mutants.py` (+20 mutants, 97 in total).
- Regenerated:
  - `apps/infrx-api/openapi/lab-control.json`;
  - `packages/api-client/src/lab.ts`;
  - `research/plan/api-lifecycle/evidence/route-inventory.{json,md}` (one row, the outcome read).
- Gate output (written by the Make target): `research/plan/evidence/e/E7L-raw-3ab5946e/`.
- `research/plan/evidence/w7/api-improve-4-3ab5946-WR-AP10E-{1,2}.patch`.

## Row 105: `GET /lab/v1/providers/{provider}/datasets/from-traces/{operation_id}`

- **Response.** An R270 route returning 200 `{operation: OperationDoc, outcome}` with `no-store`.
  - `outcome` is the record `from_traces.work` writes once the operation succeeded: the selected and split refs, the split digest, the holdout, the omissions, the grant id and version.
  - Until then `outcome` is null.
- **Refusals.**
  - 404 for each of these:
    - an unknown id;
    - an operation of another kind;
    - a path naming another workspace than the actor's;
    - a session of no member (`LabAccess.require`, read capability);
    - another tenant's operation (0060's `get`).
  - 503 when ControlOps or the actors are not composed.
- **Red.** At the base the read was `404 {"detail":"Not Found"}` (no route).
- **Mutants.** `rt_read_any_kind`, `rt_read_no_member`, `rt_read_no_outcome`, `rt_read_unscoped`.

## 10e defect fixed: a second import per checkpoint reached AP-04

- **The defect.** In api-improve-3's `register_candidate`, a registration under another `Idempotency-Key` called `artifacts.imports.start` first. Only then did the D8 note refuse it with a 409. So AP-04 queued, fetched and verified an orphan import.
- **The fix.** The candidate note now carries `key_sha256`. A noted checkpoint under another key is refused before AP-04 is called.
- **Remaining ceiling.** Two first registrations racing under different keys can still both pass the read. This is marked in the code with a `ponytail:` comment.
- **Red, recording stand-in.** `assert ['imp-1', 'imp-2'] == ['imp-1']`.
- **Red, composed world** (the fix reverted with the WR-AP10E-2 port applied): `infrx.artifact_imports` held `2 == 1` rows.
- **Mutant.** `rel_other_key_reaches_ap04`.

## 10e: the R270 routes (`routes/lab_improve.py`, `register(app, rt)` over `rt.lab_improve`)

All three routes are under `/lab/v1/providers/{provider}/training-runs/{run_id}`.

**Common rules:**
- Path ids are UUIDs. A non-UUID path id is 422 `path.run_id` before anything is read, because these ids name object keys.
- The actor comes from `rt.actors`, scoped like the datasets routes: a web session claims the path's workspace, and a credential of another workspace is 404.
- Every failure is the R270 envelope. An `Unsupported` lists its `field_errors`.

**`POST .../export`: P3's bundle of P1's reviewed-label export.**
- **Response.** 202 + Location (`/lab/v1/operations/{id}`, AP-04's read) + the `training.export` OperationDoc. Its resource is the run.
- **Who may call it.**
  - A developer+ member (`acting_provider`).
  - The payer must be the provider's own (`require_own_payer`), else 403.
- **The key.** It is bound to run + body: another body or another run under the same key is 409.
- **Execution.** The work runs inside the request under 0060's lease (60 s):
  - It finishes `succeeded`.
  - On a domain refusal it finishes `failed`, carrying that refusal's code and `field_errors`, and the response is the refusal. A replay answers the stored `failed` operation with 202.
  - A transient failure (ServerError/RateLimitError) finishes nothing. The response is a retryable 503. The same key answers the `running` operation while the lease lives, and re-runs it after the lease lapses. This is safe because P3's `prepare` is write-once.
  - A finished or held operation answers as it stands.
- **Unsupported trainer.** Any trainer other than `manual-bundle` is 422 `unsupported` on `trainer`, and nothing is bundled.

**`POST .../checkpoints/{checkpoint_id}/candidate`: `release.register_candidate`.**
- **Response.** 202 + Location: AP-04's `artifact.import` operation.
- **Who may call it.** A developer+ member.
- **The body.** The fields are exactly what `register_candidate` passes to AP-04's `ImportRequest` (no `card`), plus `artifact_key`.

**`GET .../checkpoints/{checkpoint_id}/release-evidence`: the write-once record.**
- The first read after AP-04's operation succeeded writes the record. A repeated read returns the same record.
- Before that it is 409 `state_conflict`.
- Any current member may read it.
- `qualification: pending`.

**Mutants.** 14 `imp_*` mutants:
- membership on export and on registration;
- the payer;
- key/run binding;
- unfinished, held, refusal-unrecorded and refusal-reasons-dropped exports;
- transient-as-failed;
- unscoped actor;
- unwired actors;
- the path pattern;
- candidate status 200;
- unguarded evidence read.

**Red.** With the module set aside, `test_improve_routes.py` failed at collection (1 error).

## 10e composed: `test_improve_composed_pg.py`

**What runs:**
- **AP-04**: composed as the Lab unit does with LAB_ARTIFACTS, i.e. `artifacts.compose.surface` on the Lab control login `infrx_lab_control`, over:
  - 0060's `PgControlOps`;
  - 0061's `PgArtifactStore`;
  - `S3Objects` on MinIO.
- **The worker**: AP-04's `artifacts` worker role (`artifacts.compose.role`, the factory `python -m infrx.lab.workers artifacts` runs) makes its pass in process.
- **The source**: a local MockTransport hub serving two adapter files at one commit.
- **The routes**: mounted beside AP-04's own routes, so the Location is read through AP-04's `GET /lab/v1/operations/{id}`.
- **P3's ports** (D7/D8/B3, the training records and objects) stay P3's fake world. Their SQL proofs are `tests/p`'s, on p3's key.

**Deviation from api-lifecycle-3's world.** The worker is not started as a separate process. A process role always targets huggingface.co, and no hub override exists. So the role's own factory ran in process with the stub as its `source`.

**Oracles:**
- export `succeeded`;
- the registration is `artifact.import` `queued`, with one `infrx.artifact_imports` row;
- evidence is 409 before the worker's pass;
- after the pass, the Location read is `succeeded` (200);
- the record names exactly the 0061 artifact row (`source` `import`, repo, commit, manifest digest) and the operation;
- `qualification: pending`, written once;
- another key is 409 and the row count stays at 1;
- the adapter bytes are on MinIO.

**BLOCKED[WR-AP10E-2] at this head.** ap10 has no s3 port, so the case skips visibly with that reason.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/ap10/test_from_traces_route.py` (before the read) | 1 | 1 failed (red: 404 Not Found), 2 passed |
| `uv run --frozen pytest -q tests/ap10/test_release.py` (before the fix) | 1 | 1 failed (red: `['imp-1', 'imp-2'] == ['imp-1']`) |
| `uv run --frozen pytest -q tests/ap10/test_improve_routes.py` (module set aside) | 2 | 1 collection error (red) |
| `INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10 tests/contracts/test_openapi_export.py -k 'not test_mutants'` | 0 | 75 passed, 1 skipped (BLOCKED[WR-AP10E-2]) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap10/test_mutants.py` | 0 | 99 passed: 97 mutants killed (20 new), 0 survivors; well-formed and every-case green |
| WR-AP10E-2 applied transiently: `INFRX_D_TASK=ap10 pytest tests/ap10/test_improve_composed_pg.py` | 0 | 1 passed (plain image) |
| same, `INFRX_D1_IMAGE=supabase` | 0 | 1 passed |
| same, with the `release.py` key check reverted | 1 | 1 failed: `infrx.artifact_imports` `2 == 1` |
| WR-AP10E-2 applied: `pytest tests/contracts/test_config_and_imports.py tests/contracts/lab` | 0 | 451 passed. Without the test/pin halves: 10 failed (band test, frozen digest, broken-runner lab mutants) |
| WR-AP10E-1 applied: `INFRX_D_TASK=ap10 pytest tests/l/control/test_operations.py -k 'artifact or judge'` | 0 | 4 passed. Without the `app.py` half: 2 failed |
| WR-AP10E-1 applied: `python -m infrx.contracts.openapi.export` + `pytest tests/contracts/test_openapi_export.py` | 0 | 12 passed (typed; legacy baseline unchanged; lab-control.json +967/-87). Reverted after |
| `uv run --frozen pytest -q tests/contracts/test_openapi_export.py` (this head) | 0 | 12 passed |
| `make api-client-test` | 0 | typecheck + 6 pass, 0 fail |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 457 errors (baseline 458; none in the touched files) |
| `make lab-improve` (E7L) | 2 (runner 3) | NOT RUN: PIPELINE-LINEAGE PASS (i01–i06, i08); i07 NOT RUN[P-11], i09 NOT RUN[staging-target]; no FAIL cell |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

## Wiring requests

**WR-AP10E-1: mount the training-run routes on the Lab unit, riding LAB_ARTIFACTS.**
- Files:
  - `apps/infrx-api/infrx/lab/control/app.py`: the `_compose` artifacts branch builds `LabImprove(access, PgLabDataStore(connect), lab_objects(...), RunLedger(PgRunLedger(connect)), PgControlOps(connect), artifacts)` and sets `rt.lab_improve`; `create_app` registers `lab_improve` with the other families;
  - `apps/infrx-api/tests/l/control/test_operations.py`.
- Patch: `research/plan/evidence/w7/api-improve-4-3ab5946-WR-AP10E-1.patch` (`git apply`).
- Composed test: `test_control_app__mounts_the_artifact_families_only_with_lab_artifacts` gains the export path. With the switch off it is 404; with it on it is 503 until AP-01's actors are composed. Results are in the table above.
- After applying: regenerate the export and the client (`uv run --frozen python -m infrx.contracts.openapi.export`; `cd packages/api-client && pnpm generate`). This adds the three typed operations `startTrainingExport`, `registerTrainingCandidate` and `getReleaseEvidence`, and does not grow the legacy baseline.

**WR-AP10E-2: give ap10 its MinIO port 57572 (the next free port in the Lab band).**
- Files:
  - `apps/infrx-api/infrx/contracts/tasklocal.py`;
  - `tests/contracts/test_config_and_imports.py` (`LAB_LANE_PORTS`);
  - `tests/contracts/lab/frozen_contracts.json` (the digest re-pin plus a revisions entry).
- Patch: `research/plan/evidence/w7/api-improve-4-3ab5946-WR-AP10E-2.patch`.
- Composed test: `INFRX_D_TASK=ap10 pytest tests/ap10/test_improve_composed_pg.py` on both images. Results are in the table above.

## Schema requests

None.

## Proposed ruling (unnumbered)

- **(10e routes)** A short write-once Lab step may run inside its request under a 0060 lease and still answer R270's 202 + OperationDoc:
  - a domain refusal finishes the operation `failed` with the same envelope the response carries;
  - a transient failure finishes nothing, so the same key resumes after the lease lapses.
- **(10e)** A checkpoint's candidate import is refused under another key BEFORE AP-04 is called, never after.

## Open / not done

- 10d on the real candidate: BLOCKED[GPU-window]. The quality verdict: BLOCKED[P-07]. Both are unchanged.
- The composed test is BLOCKED[WR-AP10E-2] until the port is allocated. It was proven green with the WR applied transiently.
- Not composed: the training-run routes on the unit (WR-AP10E-1) and the datasets worker's pass (`LAB_TRACE_DATASETS`, off).
- No Lab web client calls the new routes yet. The route inventory's consumer column is the prefix default.

## Estimate (remaining for AP-10 10e closure, excluding the GPU window and P-07)

- Hours: 1 / 2 / 4 (optimistic / likely / pessimistic).
- Confidence: medium.
- Basis:
  - applying both WRs plus the regeneration, about 0.5 h;
  - the Lab UI wiring of the three routes is AP-09d / UX work, outside this lane;
  - the real candidate run is the 10d CLI inside a coordinator GPU window.
