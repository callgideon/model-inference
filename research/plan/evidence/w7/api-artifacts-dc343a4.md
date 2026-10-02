# api-artifacts (AP-04a-e), head dc343a4: evidence

Lane api-artifacts, wave 7 (LW7), branch `codex/w7-api-artifacts`, base `cd9f517c`, code head `dc343a40`. Key `ap4` (PostgreSQL 57554, MinIO 57555). Written 2026-10-02.

## Changed paths (all owned)

- `apps/app/supabase/migrations/0061_model_projects_artifacts.sql` (new, LOCAL-ONLY header). Adds `model_projects`, `artifacts` (immutable), `artifact_uploads`, `artifact_imports`, `model_project_revisions` (the serving version -> artifact link, immutable) and the SECURITY DEFINER `model_project_bind`. Has RLS, the control login only, and no browser grant.
- `apps/infrx-api/infrx/lab/artifacts/` (new): `__init__.py` (`LabArtifacts`, `ArtifactWorker`), `manifest.py`, `verify.py`, `store.py`, `uploads.py`, `imports.py`, `projects.py`.
- `apps/infrx-api/infrx/gateway/routes/lab_model_projects.py` and `lab_artifacts.py` (new; R270 `register(app, rt)`; nothing mounts while `rt.lab_artifacts` is None).
- `apps/infrx-api/tests/ap04/` (new: conftest, test_artifacts, mutants, test_mutants).
- `apps/infrx-api/tests/d/test_upgrade_0061_mutants.py` (new).

## Slices

| Slice | State | Proof |
|---|---|---|
| 04a project + manifest | done | An empty provider creates a project with no serving claim (no `public.models` row and no serving version). A replay returns the same id. The same key with another body returns 409 `idempotency_conflict`. Slugs are unique within a provider. Pages use opaque cursors. Refusals use the R270 envelope (a FastAPI 422 included) and never echo input. |
| 04b uploads | done | Parts verify when PUT in reverse order and when resumed. A wrong hash, a missing shard or an extra path fails the operation with the path named, and the bytes are deleted. Unsafe paths and code-bearing files are refused before any byte is stored. An expired session answers 410 and the sweep deletes its bytes. A duplicate completion returns the same single operation. A worker killed mid-hash is fenced out (its stale advance or finish raises `stale_lease`). A retry after the artifact write still yields one artifact. Real presigned PUTs to MinIO run in the pg world. |
| 04c pinned import | done (stub) | A local stub repository serves the Marlin-shaped files at commit `fd111fca...`. The import fetches and verifies every file at that commit. A mutable ref, a disallowed host or a raw token is refused (422; the token-shaped value is not echoed). A stale credential, an off-host redirect or a file missing at the source fails the operation with its reason and keeps no bytes. The token never appears in any response or stored row. **The real Marlin import against huggingface.co is not run** (no network or secrets in lanes; this is the coordinator's run). |
| 04d compatibility + revision | done | A verified artifact on the supported Marlin profile becomes an immutable serving revision through the existing `LabControl.register` and A3's `Registry.put`. It does not go through `create_dev`; see deviations. The label is the revision's own id, so two revisions of one artifact never race for a label. An unsupported architecture, runtime, precision, hardware or input format returns 422 naming each reason. The profile constants are pinned to `models/marlin2b/serving-version.json`. |
| 04e adoption | done (fixture) | The operator adopts the seeded Marlin serving version, using measured digests read from serving-version.json. The production `serving_version_id` and public id are preserved and no new serving row is written. A replay returns the same artifact. A non-operator gets 403, a foreign provider 404, and other bytes 422 `digest_mismatch`. **The real adoption run is the coordinator's.** |

## Commands (exit codes and counts)

- Red: `uv run --frozen pytest -q tests/ap04 -m 'not pg'` with `infrx/lab/artifacts` and both route modules moved aside exited non-zero: ImportError at conftest (`cannot import name 'lab_artifacts'`). The implementation was drafted before the suite was written; the red run was recorded before the first commit.
- `uv run --frozen pytest -q tests/ap04/test_artifacts.py -m "not pg"` (fake world): exit 0, 29 passed.
- `INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/ap04/test_artifacts.py`: exit 0, 56 passed (fake + PostgreSQL as `infrx_lab_control` + MinIO).
- `INFRX_D_TASK=ap4 INFRX_D1_IMAGE=supabase ... tests/ap04/test_artifacts.py -m pg`: exit 0, 27 passed.
- `INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/d -k 0061`: exit 0, 29 passed (7 checks, upgrade over consumer history plus re-run, 19 SQL mutants killed). With `INFRX_D1_IMAGE=supabase`: exit 0, 29 passed.
- `INFRX_MUTANTS=all INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/ap04/test_mutants.py`: exit 0, 66 passed (56 fake-world mutants and 4 PostgreSQL/MinIO mutants killed, 0 survivors; `test_every_case_is_covered_by_a_mutant` green over 26 cases; runner self-tests green).
- `make api-lint`: exit 0. `make api-typecheck`: `pyright: 458 errors (baseline 458)`, no new errors.
- `INFRX_D_TASK=ap4 ... pytest -q tests/i/test_migrate.py tests/d/test_upgrade_lab.py tests/d/test_schema_postgres.py`: 31 passed, 1 failed. `test_upgrade_lab` fails because its `NEW_TABLES` lacks 0061's five tables. WR-AP04-3 fixes this; the patched copy passes.
- `INFRX_D_TASK=ap4 ... pytest -q tests/l tests/l3sql` (mutant lists excluded): 99 passed.
- `INFRX_D_TASK=ap4 ... pytest -q tests/contracts tests/g --deselect tests/contracts/test_mutants.py`: exit 0, 2393 passed, 15 skipped.
  - An earlier run of the same suites **without** `INFRX_D_TASK` reached the D harness default key d1 for the `[pg]` params of tests/g. Another checkout's `infrx-d1-postgres` is running there, so the harness refused it as foreign: 12 errors and 23 failures, nothing started, stopped or touched. The keyed rerun above is the result of record.
- `tests/integration/test_makefile_mutant_lists.py`: fails until WR-AP04-4. With the patch applied (then reverted): 7 passed.
- `tests/integration/test_harness.py`: 1 failure (the migration list pin) until WR-AP04-5. The patched copy passes.

## Wiring requests (coordinator-owned files; each patch proven, then reverted)

- **WR-AP04-1 `infrx/gateway/app.py` (ROUTERS).** Import `lab_artifacts, lab_model_projects` from `.routes` and append them to `ROUTERS`. Both mount nothing while `rt.lab_artifacts` is None (default off). Composed test: `tests/ap04 ... test_ap04__nothing_mounts_while_the_surface_is_unwired`, plus `tests/g/test_startup.py` unchanged.
- **WR-AP04-2 (batch 2, after 0060 merges).** Add a switch `LAB_ARTIFACTS` (`DeploymentSettings.lab_artifacts: bool = False`). In `lab/control/app.py:_compose`, set `rt.lab_artifacts = LabArtifacts.compose(access, lab_operations(connect, access).control, PgArtifactStore(connect), <api-schema's PgControlOps>(connect), S3Objects(S3ObjectStore.connect(LAB_S3_BUCKET, "<prefix>/", endpoint)), HubSource())` when the switch is on. `rt.actors` comes from AP-01. Add a worker role `artifacts` to `python -m infrx.lab.workers` that loops `ArtifactWorker(artifacts).run_once()` with the same store, ops and objects, and an `ssm:` secret resolver on the box. Composed test: the tests/ap04 pg world with the PG ops store in place of `MemoryControlOps`.
- **WR-AP04-3 `tests/d/test_upgrade_lab.py` NEW_TABLES.** Replace `"infrx.lab_variant_identities"}  # 0058` with `"infrx.lab_variant_identities",  # 0058` followed by `*(f"infrx.{t}" for t in ("model_projects", "artifacts", "artifact_uploads", "artifact_imports", "model_project_revisions"))}  # 0061 (R271)`. Proven: `test_lab_upgrade_preserves_history_money_identity_and_grants` passes on ap4.
- **WR-AP04-4 `Makefile` api-mutants.** After the lw9 line, add:
  - `# 0061's SQL list (api-artifacts, AP-04, R271): needs Docker, skips visibly without it; task-local key ap4`
  - `cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/d/test_upgrade_0061_mutants.py`
  - `# AP-04's list: fake world, then its PostgreSQL + MinIO half in its own process (the copy starts the D harness); task-local key ap4`
  - `cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/ap04/test_mutants.py`

  Proven: `tests/integration/test_makefile_mutant_lists.py` 7 passed.
- **WR-AP04-5 `tests/integration/test_harness.py`.** After `"0059_lab_control_grants_2.sql",` append `# Wave 7 (local-only, R271): api-artifacts (AP-04)` and `"0061_model_projects_artifacts.sql",` (0060 goes before it when api-schema merges). Proven: patched copy passes.

## Schema request (to api-schema, 0060 / `infrx/state/control_ops.py`)

`infrx/lab/artifacts/store.py:ControlOps` is the protocol AP-04 consumes. The PostgreSQL store must keep these method names and semantics:

- `start(kind, resource_kind, resource_id, actor, scope, key, input_hash) -> (op, replayed)`. Another input hash under the same key raises `idempotency_conflict`.
- `get(op_id)` exposes the starting actor (audience, user_id, provider_org_id) and the resource, so `GET /lab/v1/operations/{id}` can scope by provider.
- `lease(op_id, owner, ttl_s) -> fence | None`. Returns None when the op is terminal or a live lease holds it. The first lease moves `queued` to `running`.
- `advance(op_id, fence, phase) -> op`. Returns the op so the worker sees `cancel_requested`. A stale fence raises `stale_lease`.
- `finish(op_id, fence, state, error)`, where `error` is an R270 ErrorBody with `field_errors`.
- `cancel(op_id)`: queued becomes `cancelled`, running becomes `cancel_requested`, terminal is a no-op.
- EXECUTE for `infrx_lab_control` and for the artifacts worker's login.

## Open items

- The real Marlin import (huggingface.co, the gated repo, `HF_TOKEN` by an `ssm:` reference) and the real adoption of the hosted bytes: coordinator runs, needing network and secrets.
- `MemoryControlOps` does not survive a restart and is not shared between processes. The worker composition therefore waits for 0060 (WR-AP04-2).
- `ArtifactWorker` has no attempt cap (ponytail note; add it with 0060's reconciler). An operation cancelled while still queued leaves its intake row pending. Cancel is AP-05's route, so this is not reachable through an AP-04 route yet.
- One PUT per file (5 GiB ceiling). Marlin's largest shard is 4.999 GB and fits. Uploads have no multipart.
- `EnvelopeRoute` and `guarded` live in `routes/lab_artifacts.py`. Proposed ruling: move them into `infrx.gateway.control` as the shared R270 FastAPI-422 adapter (unnumbered; next free R272).

## Estimate (remaining for AP-04 to close: review fixes + batch-2 switch to 0060 + worker role + the coordinator's real import/adoption)

Optimistic 3 h, likely 5 h, pessimistic 9 h. Confidence: medium. Basis: the code paths are proven on PG and MinIO. What remains is swapping in the 0060 store (protocol-identical), one composition patch and one worker role, plus the real-network import, which can surface HF redirect or Xet behaviour the stub does not model.
