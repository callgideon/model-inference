# api-artifacts-2 (AP-04 composition, WR-AP04-2), code head 4a5a257: evidence

Lane api-artifacts-2, wave 7 batch 2, branch `codex/w7-api-artifacts-2`, base `b05eb6f4`, code head `4a5a2579`. Key `ap4` (PostgreSQL 57554, MinIO 57555). Written 2026-10-02.

## Changed paths (all owned)

- `apps/infrx-api/infrx/lab/artifacts/compose.py` (new):
  - `DurableOps` puts AP-04's operation port on top of 0060's `ControlOps` (`PgControlOps`, or `FakeControlOps` in tests). It has a per-phase lease heartbeat.
  - `WorkspaceActors` gives a web session the `?provider_org_id=` workspace claim. Every AP-04 door checks that claim.
  - `mount(app, rt)` mounts the two AP-04 families on the Lab unit.
  - `surface(connect, objects)` builds `LabArtifacts` on one login over 0061, 0060 and `S3Objects`.
  - `role(...)` is the `artifacts` worker role.
  - `secrets(refs)` resolves only allowlisted `env:` and `ssm:` references.
  - `files_from` and `import_request` turn the box manifest into the import body.
- `apps/infrx-api/tests/ap04/` changes:
  - Both worlds now run on 0060 (`DurableOps(FakeControlOps)` and `DurableOps(PgControlOps)` as `infrx_lab_control`).
  - Six new cases.
  - 12 new mutants: 11 fake and 1 PG.
  - Two MemoryControlOps fence mutants were retargeted to the composition.
  - The mutant layout now copies `infra/runbooks/artifacts.py`.
- `research/plan/evidence/w7/api-artifacts-2-WR-AP04-2.patch` (new): the wiring patch below. It was proven and then reverted.

## Slices

| Slice | State | Proof |
|---|---|---|
| 1. AP-04 on 0060 | done | Every API-ARTIFACT case passes on `PgControlOps` as the Lab control login, on plain PG and on the Supabase image. The 0060 protocol differs from the one AP-04 published (`start` signature, `get`/`cancel` take an actor, `lease` returns an Operation and raises Conflict). `DurableOps` maps between them: a held lease returns None, a lost fence raises `stale_lease`, and the system reads while the route scopes. A new heartbeat case shows that phases which together outlast the 600 s lease still finish in one pass (a Marlin shard is about 5 GB). |
| 2. Lab unit composition | done (own paths) + wiring filed | `the_lab_unit_surface_runs_on_its_own_login` (pg) runs `surface` + `mount` + `role` entirely as `infrx_lab_control` over 0060/0061/MinIO. A session names its workspace, uploads real bytes, the role's worker verifies them and they become a serving revision. A member of another provider is refused. `a_session_names_its_workspace...` (fake) covers four things: a claim is checked, a key keeps its own provider, no workspace gives 403, and missing actors give 503. |
| 3. Restart (kill test) | done | `an_operation_survives_a_killed_worker_process` (pg) composes a real `python -c` worker process like the role and SIGKILLs it while it is hashing. The operation stays `running`, and its live lease is not taken over. After the lease expires, a pass in another process finishes it, and exactly one artifact row exists. |
| 4. Secret refs | done | `an_import_resolves_only_the_secret_refs_its_role_names` (fake): an unlisted `ssm:` path fails with `secret_unavailable` before SSM is read; so does a listed but unset `env:`. A listed `ssm:` is read decrypted and the import succeeds. The token is never stored or echoed. |
| 5. Real import/adoption rehearsal | stub only (by brief) | `the_box_manifest_becomes_the_import_request` builds the body from the real `infra/runbooks/artifacts.py files()` over the stub repo written to disk, with a hidden `.gitattributes` excluded. The import verifies into exactly the manifest's digests. The real runs are the coordinator's (see below). |

## Commands (exit codes, counts)

- Red, slice 1: `uv run --frozen pytest -q tests/ap04/test_artifacts.py -m 'not pg'` exited 4 with `ModuleNotFoundError: infrx.lab.artifacts.compose` (conftest switched to `DurableOps` first).
- Red, slice 2: the same command gave 3 failed and 31 passed. The three new fake cases failed on ImportError of `mount` / `secrets` / `import_request`.
- `INFRX_D_TASK=ap4 uv run --frozen pytest -q tests/ap04/test_artifacts.py`: exit 0, 64 passed (34 fake + 30 pg).
- `INFRX_D_TASK=ap4 INFRX_D1_IMAGE=supabase ... tests/ap04/test_artifacts.py -m pg`: exit 0, 30 passed.
- `INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap04/test_mutants.py`: exit 0, 72 passed and 1 skipped (the empty PG parametrization). All 66 fake mutants were killed. The every-case guard is green over 32 cases.
- `INFRX_MUTANTS=all INFRX_D_TASK=ap4 ... tests/ap04/test_mutants.py -k pg_mutant`: exit 0, 5 passed. All 5 PG mutants were killed, including `surface_ops_in_memory`.
- `make api-lint`: exit 0. `make api-typecheck`: exit 0, `pyright: 458 errors (baseline 458)`.
- `tests/integration/test_makefile_mutant_lists.py`: 7 passed (no new mutant file).
- With the WR-AP04-2 patch applied, then reverted:
  - `INFRX_D_TASK=ap4 ... tests/l/control/test_operations.py tests/contracts/test_config_and_imports.py tests/i/lab_control tests/i/lab`: 448 passed (after the two pin bumps in the patch).
  - `tests/w/test_lab_workers.py tests/g/test_startup.py`: 77 passed.
  - `INFRX_MUTANTS=all INFRX_D_TASK=ap4 tests/l/control/test_mutants.py -k "artifacts_mounted_without or artifacts_never_mounted"`: 4 killed (fake and pg).
  - The first `tests/i/lab` run failed on the unit-set, revert-count and role-spec pins. The patch now carries those edits.
- Pre-existing at base, not this lane: `tests/l/control/test_mutants.py::test_every_case_is_covered_by_a_mutant` fails. No mutant names `test_control_app__mounts_the_judge_family_only_with_lab_judge_api` (AP-08's WR-1 merge). This red is in `make check`.
- Docker: only `infrx-ap4-*` was used, and nothing is left running.

## Wiring requests

**WR-AP04-2** (one patch: `research/plan/evidence/w7/api-artifacts-2-WR-AP04-2.patch`; `git apply` checked on b05eb6f4):
- `infrx/config.py`: add `lab_artifacts: bool = False` (LAB_ARTIFACTS).
- `tests/contracts/test_config_and_imports.py`: add `"LAB_ARTIFACTS": False`.
- `infrx/lab/control/app.py`:
  - In `_compose`, set `rt.lab_artifacts = surface(connect, lab_objects(MODE, os.environ))` only when the switch is on. A missing LAB_S3_BUCKET refuses startup by name.
  - In `create_app`, call `mount(app, rt)`. The families run behind `WorkspaceActors(rt.actors)`, so they answer 503 until api-identity-2 composes `rt.actors` on the unit.
- `infrx/lab/workers/__main__.py`: add `ROLES += "artifacts"`, `NEEDS["artifacts"] = (BUCKET,)` and `BUILD["artifacts"] = _artifacts` (which calls `compose.role`), plus a docstring line.
- `infra/lab/rollout/steps/50-lab-role.sh`: `artifacts) names="$common LAB_S3_PREFIX LAB_ARTIFACT_SECRET_REFS"; needs="LAB_S3_BUCKET"`.
- `infra/lab/rollout/lib.sh`: add `artifacts` to `ROLES`.
- `apps/infrx-api/deploy/lab/artifacts/infrx-lab-artifacts.service` (new):
  - Health port 8018 and uid 10005.
  - An NVMe spool at `/opt/dlami/nvme/infrx-lab-artifacts` as TMPDIR, because a 5 GB shard never fits the tmpfs.
  - `AWS_DEFAULT_REGION=us-east-1` for the `ssm:` read.
- Pins: `tests/i/lab/test_lab_packaging.py` unit set += artifacts; `tests/i/lab/test_lab_rollout_steps.py` revert disables 8 to 9.
- Composed test: `tests/l/control/test_operations.py::test_control_app__mounts_the_artifact_families_only_with_lab_artifacts` (off gives 404; on gives 503 `dependency_unavailable` while no actors are composed; on without a bucket refuses naming LAB_S3_BUCKET). Its two mutants go in `tests/l/control/mutants.py`.
- For the journey on the real login, the composed test is `tests/ap04 ::the_lab_unit_surface_runs_on_its_own_login`.

`api_lifecycle` stages 02–03 stay BLOCKED until both WR-AP04-2 and api-identity-2's Lab-unit `rt.actors` are merged. Stage clients must send `?provider_org_id=<workspace>` on every AP-04 call (the Lab transport already does).

## Schema requests

None for this composition. The unit and the role are proven on `infrx_lab_control`, which has 0060 EXECUTE and the 0061 grants. If the coordinator gives the artifacts role its own login instead of the control DSN, that login needs:
- EXECUTE on `infrx.control_op_{start,lease,advance,finish,cancel,get,pending}(jsonb)` and `infrx.model_project_bind(jsonb)`;
- SELECT/INSERT/UPDATE on `infrx.artifact_uploads` and `infrx.artifact_imports`;
- SELECT/INSERT on `infrx.model_projects`, `infrx.artifacts` and `infrx.model_project_revisions`;
- the L3 registry/catalog reads `lab_control` already uses (that is api-schema-2's 0066 worker-login item).

## The coordinator's real import and adoption (not run here: network, the gated repo, secrets, hosted 0060/0061)

Prerequisites:
- An R151 window has applied 0060–0061 hosted. They are LOCAL-ONLY until then, so this is BLOCKED on that window.
- WR-AP04-2 and the api-identity-2 Lab-unit actors are merged and deployed.
- `/model-inference/hf_token` holds a token approved for the gated `NemoStation/Marlin-2B`.

1. Measure the served bytes on the box (read-only). Exit 2 means the served bytes are not the pins: stop.
   `python3 infra/runbooks/artifacts.py manifest --weights /opt/dlami/nvme/marlin2b --serving-version models/marlin2b/serving-version.json --release <sha> --out /tmp/marlin-manifest`
2. Switch on (a recorded reversible change):
   - LAB_ARTIFACTS=1 in the Lab unit env.
   - `infra/rollout/ssm.sh infra/lab/rollout/steps/50-lab-role.sh STATE=on ROLE=artifacts RELEASE=<sha> SPEC="LAB_DATABASE_URL=/model-inference/lab/control_dsn LAB_S3_BUCKET:=<lab bucket> LAB_ARTIFACT_SECRET_REFS:=ssm:/model-inference/hf_token"`
3. Create the project as a NemoStation developer session: `POST https://lab-control.callbill.ai/lab/v1/control/model-projects?provider_org_id=<NemoStation>` with `{"name":"Marlin-2B","slug":"marlin-2b"}` and `Idempotency-Key: marlin-project-1`. Expect 201.
4. Build the import body: `cd apps/infrx-api && uv run --frozen python -c 'import json,sys; from infrx.lab.artifacts.compose import import_request; print(json.dumps(import_request(json.load(open(sys.argv[1])), sys.argv[2], "ssm:/model-inference/hf_token")))' /tmp/marlin-manifest/manifest.json <project_id> > /tmp/marlin-import.json`
5. Start the import: `POST /lab/v1/artifacts/imports?provider_org_id=<NemoStation>` with that body and `Idempotency-Key: marlin-import-fd111fca`. Expect 202 with `Location: /lab/v1/operations/<id>`.
6. Poll `GET /lab/v1/operations/<id>?provider_org_id=...`. The phases are `fetching:<path>`, `hashing:<path>` and `settling`, ending in `succeeded`.
7. Check the artifact with `GET /lab/v1/artifacts/<resource_id>`. The expected manifest is `serving-version.json` `model`:
   - `source` = `import`, `source_repo` = `NemoStation/Marlin-2B`, `source_commit` = `fd111fca4fc7897876fb0d7e9df22ca5ac8ab965`.
   - The shard digests are `sha256:5d78fa4d…83b7` and `sha256:01d40ec9…d0db`.
   - `tokenizer.json` is `sha256:06b95093…e523`, `chat_template.jinja` is `sha256:273d8e0e…2d80`, `config.json` is `sha256:1325d779…bd20` and `generation_config.json` is `sha256:0d54a28c…25b4`.
   - `processor_config.json` is `sha256:d89ef49c…43b1` and `preprocessor_config.json` is `sha256:27225450…e516`.
   - `compatibility.supported` is true.
   - Byte sizes and the other files (the shard index, etc.) are ⚠️ TO BE VERIFIED. They are measured in step 1, because no in-repo record holds the shard sizes.
   - A failure names its reason: `source_unauthorized` (token or gating), `redirect_refused` (a storage host outside `*.huggingface.co` / `*.hf.co`), `missing_at_source` (a box-local file absent at the commit) or `digest_mismatch`.
8. Adoption (an operator session): `POST /operator/v1/artifacts/adopt` with `{"provider_org_id": <NemoStation>, "serving_version_id": <the production serving version>, "files": files_from(manifest), "evidence_ref": "<step 1's printed manifest sha256 + SSM command id>"}` and an Idempotency-Key. Build `files` with `from infrx.lab.artifacts.compose import files_from`. Expect 201 with `source` = `adopted` and the production `serving_version_id` and public id preserved. A replay returns the same artifact.

## Open items (unowned files; proposals)

- `uploads.Uploads.complete`, race path: two concurrent completions under the same key can produce a replayed operation. The loser cancels it (`await self.ops.cancel(op...)`), and the replayed operation is the winner's, so the session is stuck at `verifying`. One-line fix: unpack `op, replayed` and cancel only when `not replayed`. This predates the lane (MemoryControlOps did the same).
- `store.MemoryControlOps` and its OPS case are now dead outside their own test. Delete both at the next AP-04 touch; the mutants `ops_replay_ignores_body` / `ops_cancel_never_terminal` go with them.
- `imports.env_secret` still resolves any `env:` name when composed without `secrets()`. The composition always passes `secrets(refs)`, so this is a test-only path.
- A browser upload to S3 needs CORS on the Lab bucket for the Lab origin. That is box/bucket config at the real upload run; imports are server-side and need none.
- The heartbeat renews per phase, which is one file. A single file slower than 600 s (for example, a 5 GB fetch under 8.3 MB/s) loses its fence and is re-run on the next pass. ponytail: raise LEASE_S or renew inside the fetch if HF is slower.
- Proposed ruling (unnumbered; next free is R272): on the Lab unit a web session's workspace is `?provider_org_id=`, a claim every door verifies through `LabAccess.require`, and it never overrides a credential's own provider.

## Estimate (remaining for AP-04 to close)

Optimistic 2 h, likely 4 h, pessimistic 8 h. Confidence: medium. Basis: the composition, the restart and the role are proven locally on ap4. What remains:
- the coordinator applies WR-AP04-2 and api-identity-2's actors;
- the R151 window for 0060/0061;
- the real HF import and adoption run. Real HF/Xet redirects and 5 GB transfer times are the unknowns the stub cannot model.
