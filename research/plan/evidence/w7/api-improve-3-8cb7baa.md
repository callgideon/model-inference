# AP-10 (lane api-improve-3, wave 7 batch 3): slices 10d and 10e, and register row 92

- Base `49114933`. Branch `codex/w7-api-improve-3`. Code head `8cb7baa3`.
- Slice commits:
  - `c84f1011`: row 92, the route;
  - `055528fc`: 10d;
  - `6af47b02`: 10e;
  - `8cb7baa3`: lint and type hygiene.
- The evidence commit follows them.
- Key `ap10`: PostgreSQL 57566 only (`INFRX_D_TASK=ap10`).
  - The real fake-engine process ran on a free loopback port.
  - `make lab-evaluate` and `make lab-improve` ran on their own gate blocks (E6L, E7L), as the brief's oracles name them.
- No d1/55432 container was used. Nothing hosted, no box, no AWS/SSM/Vercel, no secret. No migration was written.
- Nothing is enabled:
  - The new route mounts only where `LAB_DATASETS` composes a `LabDatasets` (off by default). Without a composed ControlOps (WR-AP10D-1) it answers 503.
  - 10d and 10e are library and CLI code with no switch.

## Changed paths (all owned)

- `apps/infrx-api/infrx/gateway/routes/lab_datasets.py`: the from-traces route only, plus `LabDatasets.ops` (default None).
- `apps/infrx-api/infrx/lab/improve/sop.py` (new): 10d.
- `apps/infrx-api/infrx/lab/improve/release.py` (new): 10e.
- `apps/infrx-api/tests/ap10/`:
  - `test_from_traces_route.py` (2 cases);
  - `test_sop_benchmark.py` (6 cases, one of them parametrized ×3);
  - `test_sop_fake_engine.py` (1 case: a real process, outside the mutant runner, like the `_pg` files);
  - `test_release.py` (3 cases);
  - `mutants.py` (+41 mutants: `rt_` 5, `sop_` 21, `rel_` 15; 77 in total).
- `models/marlin2b/sop/README.md` (new): the harness command and protocol.
- Regenerated:
  - `apps/infrx-api/openapi/lab-control.json`;
  - `packages/api-client/src/lab.ts`;
  - `research/plan/api-lifecycle/evidence/route-inventory.{json,md}` (one row each).
- Gate output: `research/plan/evidence/e/E6L-raw-8cb7baa3/` and `E7L-raw-8cb7baa3/`. These are written by the Make targets and committed like the earlier `E7L-raw-*` directories.
- `research/plan/evidence/w7/api-improve-3-8cb7baa-WR-AP10D-1.patch`.

## Row 92 (WR-AP10C-4): `POST /lab/v1/providers/{provider}/datasets/from-traces`

An R270 route over api-improve-2's `from_traces.start`:

- **Router and body.** It uses `control.R270Route` with the `lab_artifacts` `ERRORS` responses. The body is the typed `TraceDataset`. `Idempotency-Key` is required.
- **Response.**
  - 202 with the 0060 `OperationDoc`, `Location: /lab/v1/operations/{id}` (AP-04's read) and `no-store`.
  - The same key and body is the same operation.
  - The same key with another body is 409 `idempotency_conflict`.
- **Actor.** The actor comes from `rt.actors`:
  - A web session (no workspace) acts in the path's workspace. That is a claim only: `start` checks a current developer+ membership.
  - An actor naming another workspace is 404.
  - A key audience or a viewer is 403.
  - With no `rt.actors`, or no composed `ops`, the route is 503 and never a 500.
- **Mounting.** The route mounts with the datasets family, so the lab-control OpenAPI artifact documents it now (typed request and response). The legacy baseline is unchanged: it does not grow. `packages/api-client` was regenerated.
- **Red.** At the base, `LabDatasets(..., ops=...)` raised `TypeError: unexpected keyword argument 'ops'`. Both cases failed.

## 10d: the SOP benchmark (`python -m infrx.lab.improve.sop`)

The harness is a fixed-case run at concurrency 1 against one candidate's OpenAI base URL. Every request uses `temperature` 0 and the run's `seed`.

**What the report pins:**
- the dataset: version, manifest sha256, and a digest of the case ids;
- the identity: the model requested, the models `/v1/models` lists, and the serving revision;
- the harness (`infrx.sop_benchmark.1`), the parser (`sop-events.1`: caption `<a - b>` and find `From a to b.`) and the seed.

**What it lists, by item id:**
- **Failures:** `http_<status>`, `malformed_answer`, `identity_mismatch` (an answer from another model), and transport errors by class.
- **Abstentions:** `no_media` (never sent) and `insufficient_evidence` (no timed event).

**Quality.**
- Output validity is always counted, apart from task agreement.
- Agreement is `BLOCKED[P-07]` until a reviewed SOP definition (with `tolerance_s`) AND a `human_reviewed` gold set of exactly this manifest are supplied.
- Teacher or model labels, another manifest, and unknown item ids all stay BLOCKED.
- With both inputs, agreement is one-to-one and in order, with the start AND the end within the tolerance. It reports matched, missed and hallucinated counts, precision and recall, and lists unanswered gold items. No number is invented.

**Performance.**
- The label is `fake` when the endpoint answers the fake engine's `GET /_control` (fake_vllm, which is also AP-05's candidate engine) or when the target is declared `fake`. It is `meas.` only for a declared candidate that does not answer that probe.
- p50 needs at least 6 samples and p95 at least 60 (`bench.py`'s rule). Below that, a percentile is refused, never guessed.

**Local fake run** (the standalone `tests/integration/fake_vllm.py`; 6 media items and 1 without media; declared `--target candidate`):
- quality `BLOCKED` ("P-07: the SOP definition and a human-reviewed gold set are not supplied"), output validity 0/6 with timed events;
- performance `fake`: p50 latency 0.0015 s (fake), p95 refused (n=6);
- 0 failures;
- 7 abstentions (6 `insufficient_evidence`: the fake's text has no timed event; 1 `no_media`);
- manifest sha256 `796b1284…`; dataset version `sop-fixture-local-1`; seed 7.

The command:

```
apps/infrx-api/.venv/bin/python -m infrx.lab.improve.sop --manifest <scratch>/items.jsonl \
  --dataset-version sop-fixture-local-1 --base-url http://127.0.0.1:<free>/v1 \
  --model infrx-e2/fake-vllm --serving-revision fake-vllm-local --seed 7 --target candidate --out <scratch>/report.json
```

**Not run here:** the real candidate (AP-05 on the box) needs a coordinator GPU window, and the quality verdict needs P-07. Both are BLOCKED, never faked.

**Red.** Without the module, `test_sop_benchmark.py` failed at collection: `1 error`.

## 10e: reviewed labels → external training → AP-04 import → release evidence (`infrx.lab.improve.release`)

- **`export_for_training`.**
  - The one supported trainer is P3's manual bundle (`training.ADVERTISED`). Any other trainer is AP-04's `Unsupported` (422, field `trainer`, code `unsupported`) and nothing is bundled.
  - The bundle carries P1's export of reviewed labels (`infrx.label_export.1`: accepted labels only, train-only).
  - The config's objective must be the export's adapter (`sft.1`/`preference.1`). Otherwise it is `config.objective`/`adapter_mismatch`.
  - The export id must be a UUID before any object is read.
  - P3's `prepare` does the rest.
- **`register_candidate`.** It starts AP-04's import only when all of these hold; anything else starts nothing:
  - the checkpoint is approved (P3 `eligible:` note) and belongs to this run;
  - the actor is in the run's own workspace;
  - the descriptor is read under the run's prefix, and its bytes match D7 0053's receipt digest;
  - the import declares exactly the descriptor's files;
  - it is the only import for the checkpoint (D8's write-once note: a replay is the same operation, another import is 409).
- **`release_evidence`.**
  - It is written once, only after AP-04's operation `succeeded` and its verified artifact is exactly the registration (source `import`, repo, commit, files).
  - It pins: the dataset, the export, label methods counted from the export lineage, the frozen holdout, the config, the checkpoint digest, the holdout evaluation, the approver and the artifact (id, manifest sha256, operation).
  - Its `qualification` is `pending`: AP-05 readiness and the 10d report of that deployment qualify a revision, never this module.
  - P1's fixture labels are model-made and accepted on review, so the record shows `{"synthetic": 3}`, not human ground truth.
- **AP-04 in these tests** is a recording stand-in for `LabArtifacts` (`imports.start`, `ops.get`, `projects.store.get`). The body is AP-04's real `ImportRequest`. A composed proof needs a route or worker composition; see "Open".
- **Red.** Without the module, `test_release.py` failed at collection: `1 error`.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/ap10/test_from_traces_route.py` (base `LabDatasets`) | 1 | 2 failed (red: TypeError `ops`) |
| `uv run --frozen pytest -q tests/ap10/test_sop_benchmark.py` (module set aside) | 2 | 1 collection error (red) |
| `uv run --frozen pytest -q tests/ap10/test_release.py` (before the module) | 2 | 1 collection error (red) |
| `INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10 tests/g/lab_datasets tests/p/training tests/n tests/contracts/test_openapi_export.py --ignore=tests/ap10/test_mutants.py` | 0 | 190 passed, 5 skipped (the p3-key-only `_pg` cases) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap10/test_mutants.py` | 0 | 79 passed: 77 mutants killed (41 new), 0 survivors; well-formed and every-case green |
| same, `-k 'sop_ or rel_ or rt_ or well_formed or every'`, rerun after the hygiene edits | 0 | 44 passed |
| `uv run --frozen pytest -q tests/ap10/test_sop_fake_engine.py` | 0 | 1 passed (real fake_vllm process) |
| `uv run --frozen pytest -q tests/contracts/test_openapi_export.py` | 0 | 12 passed (artifacts current; baseline only shrinks) |
| `make api-client-test` | 0 | typecheck plus 6 pass, 0 fail |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed (`tests/ap10/test_mutants.py` already listed) |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 457 errors (baseline 458; none new, one fewer) |
| `make lab-evaluate` (E6L) | 2 (runner 3) | NOT RUN: 6 cells PASS; EVAL-COMPARE NOT RUN (j10 NOT RUN[SR-AP10-1]); r222 accepted |
| `make lab-improve` (E7L) | 2 (runner 3) | NOT RUN: PIPELINE-LINEAGE PASS (i08 included); i07 NOT RUN[P-11], i09 NOT RUN[staging-target] |
| WR-AP10D-1 applied transiently: `pytest tests/g/test_startup.py -k lab` | 0 | 13 passed; without the compose half, 1 failed |

The first gate attempt failed with `EnvironmentBlocked: ENOENT apps/lab/node_modules`, because this worktree had no Lab install. After `pnpm install --frozen-lockfile` in `apps/lab`, the rerun is the result above. There is no FAIL cell.

## Wiring requests

- **WR-AP10D-1**: the from-traces route gets its operations.
  - Files: `apps/infrx-api/infrx/lab/compose.py` (`lab_surfaces`) and `apps/infrx-api/tests/g/test_startup.py`.
  - The exact patch is `research/plan/evidence/w7/api-improve-3-8cb7baa-WR-AP10D-1.patch` (`git apply`). It passes `PgControlOps(connect)` as `LabDatasets`' sixth field.
  - Composed test: the existing lab-datasets startup case gains `type(x.ops) is PgControlOps and x.ops._connect is x.store._connect`. It is green with the patch and red without it.
  - On the Lab unit (`_families`), the same composition uses the unit's login. That login already runs `PgControlOps` for AP-05/AP-06.
  - The route's actors are the unit's `rt.actors` (IDENTITY_API). Its Location read is AP-04's `GET /lab/v1/operations/{id}`, which is mounted only with LAB_ARTIFACTS.
  - The datasets worker's pass is api-improve-2's WR-AP10C-1, still requested. SR-AP10C-1's grants belong to api-schema-3.

## Schema requests

None.

## Proposed rulings (unnumbered)

- **(10e)** A candidate from external training becomes a serving revision only through AP-04's import, and only for:
  - an eligible checkpoint of its run (approved on the frozen holdout), in its own workspace;
  - whose received descriptor (D7's receipt digest) names exactly the imported files;
  - with one import per checkpoint.

  Its release evidence record is write-once and says `qualification: pending` until AP-05 readiness and the 10d report of that deployment exist. The only supported trainer is P3's manual bundle. Any other trainer is `unsupported`, by name.
- **(10d)** The SOP benchmark's performance is `meas.` only for a declared candidate that does not answer the fake engine's control surface. Its task-quality agreement is `BLOCKED[P-07]` until a reviewed SOP definition and human-reviewed gold labels of the same manifest exist. Teacher or model labels never grade it.

## Open / not done

- 10d on the real candidate: BLOCKED. It needs a GPU window with AP-05's candidate engine on the box (coordinator) and P-07 for quality.
- 10e has no HTTP surface or worker composition yet.
  - The functions are complete over P3, D7 and D8 and AP-04's `LabArtifacts` port. They are tested with a recording AP-04 stand-in, not a composed PG/MinIO AP-04 world (the P3 world and AP-04's fake world use different provider fixtures).
  - Routes under `/lab/v1/pipelines/training-runs/{id}/...` (`candidate`, `release-evidence`) and a composed test are the next owner's work (AP-09d / the pipelines-route owner).
- The from-traces outcome read (`GET .../from-traces/{selection_id}` over `from_traces.outcome`) was not added; the brief names only the POST. Until it exists, the produced dataset refs are read from the Lab objects.
- The route inventory's consumer column shows `datasets/port.ts` for the new route, by prefix default. No web client calls it yet. That fixes itself when the Lab adds the call.

## Estimate (remaining for 10d/10e closure)

- Hours: 3 / 6 / 12 (optimistic / likely / pessimistic).
- Confidence: medium.
- Basis:
  - WR-AP10D-1 is verified, about 0.5 h.
  - The 10e routes plus a composed AP-04 test are about 2–4 h.
  - The real candidate run is the CLI unchanged inside a coordinator GPU window, about 1 h plus the window.
  - P-07 (operator input) gates the quality verdict and is not lane work.
