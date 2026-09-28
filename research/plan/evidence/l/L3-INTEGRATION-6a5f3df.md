# L3-INTEGRATION (lane l3-integration, wave LW2) — code head 6a5f3dfb

- Branch `codex/w5-l3-integration`, worktree `.claude/worktrees/codex-w5-l3-integration`.
- Base `0bfaa6ed` (the lab-sql-lw2 head: 0031–0040, `PgControlStore` in `infrx/state/lab_control.py`).
- First step: `git merge --no-ff codex/w5-lab-access-lw2` (08a353e7, L3). The merge was clean with no conflicts.
- Commits:
  - `98c9d069` WR-L3-5 + WR-LSQ-8
  - `47afe9be` WR-R3-1
  - `6a5f3dfb` WR-LAB-API-2 + WR-R2-2 + WR-I2L-2
  - then this evidence commit
- Task-local key `l3` (PostgreSQL 57502, `infrx-l3-*`). Key `l4` was not used: the Lab App's L4 journey is not on this base (see below).

## Changed paths (since 0bfaa6ed, excluding the merged L3 files)
- `infrx/state/catalog.py`: WR-L3-5, one line. `_PRIVATE` now has `d.state = 'ready_private'`. This is byte-identical to batch #8 (`codex/w5-merge-8` cbd73efa). `git diff HEAD codex/w5-merge-8 -- catalog.py` is empty.
- `tests/d/code_mutants_d5.py` and `tests/d/test_operations_units.py`: re-anchored on the new text. Both are byte-identical to merge-8's versions, so the merge stays clean.
- `infrx/lab/access/__init__.py`: WR-R3-1 adds a public `LabAccess.require(user_id, provider_org_id, capability)` over `_member`. `LabControl` now calls `require` (4 call sites).
- `tests/l/access/{test_access.py,mutants.py}`: one `require` case (fake and pg) and the mutant `require_waves_through`, which the pg list also derives.
- `infrx/lab/control/operations.py` (new):
  - WR-LAB-API-2 `Operations`: the route's `ControlOperations` over `LabControl`. The route records are copied verbatim from `routes/lab_control.py` at f9892c30.
  - WR-R2-2 `Serving` plus `serving_ref`.
  - `ControlReads`, the read protocol requested as WR-LSQ-9.
- `infrx/lab/control/app.py` (new): WR-I2L-2 `create_app`, which serves `/readyz` over `PgControlStore.db_now` and has no docs or openapi.
- `infrx/lab/control/fakes.py`: the fake `ControlReads`, and the WR-L3-5 docstring updated.
- `tests/l/control/test_operations.py` (new, 7 cases).
- `tests/l/control/mutants.py`: WR-LSQ-8, 26 new mutants, and the runner now covers both suite files.

## (1) L3's PG world on the real PgControlStore (INFRX_D_TASK=l3)
| cmd | exit | result |
|---|---|---|
| `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l/control -m pg` (before WR-L3-5) | 1 | 12 passed, 1 failed: `a_newer_unvalidated_revision_is_never_keyed_priced_or_served[pg]`. `pin_admission` got the newer draft, so it answered "has no approved CREDIT rate card". |
| `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l/control` (before) | 1 | 31 passed, 1 failed, 1 skipped (the lab-sql probe's 25/26 as measured here) |
| same, after WR-L3-5 | 0 | 32 passed, 1 skipped (the skip is the mutant subset's PG list, which runs only in a full run) |
| `INFRX_D_TASK=l3 INFRX_MUTANTS=all pytest tests/l/control/test_mutants.py -k pg_dev_revision_of_any_environment` | 1 | survived (1 passed). This is WR-LSQ-8. 0032's `lab_control_transition`/`lab_control_propose` refuse a prod source themselves (`environment = 'dev'`, `(s.environment, …) <> ('dev', …)`), so on PG the edit is equivalent. It has been dropped from `PG_MUTANTS` (`PG_EQUIVALENT`). The fake list still kills it. |

## (2) WR-L3-5 regressions
- `INFRX_D_TASK=l3 pytest tests/d -k "catalog or operations_units"` exit 0: 49 passed, 1 xfailed.
- `pytest tests/contracts -k "catalog or private or dev"` exit 0: 43 passed.
- `INFRX_D_TASK=l3 INFRX_MUTANTS=all pytest tests/d/test_code_mutants_d5.py -k "private_resolves or well_formed or every_case"` exit 0: 2 passed. The re-anchored `private_resolves_a_retired_deployment` is killed.
- tests/g: run inside `make api-test` below.

## (3) WR-LAB-API-2: `Operations` (the ControlOperations port)
- Failing seam first: `pytest tests/l/control/test_operations.py` exited 2 with `ImportError: cannot import name 'app' from 'infrx.lab.control'` (the modules were absent).
- Mapping onto the route's closed records:
  - Records:
    - `Model` and `Deployment` are built from L3's rows. `model_id` is the public model id. `runtime` is the image repository and `artifact_digest` its digest.
    - The schema version is `infrx.request.<v>` without its prefix. `rate_card_version` is the listing's card.
    - `state` is liveness only (`retired` or `active`). `smoke` is `none` for draft, validating or retired, `passed` from ready_private on, and `failed` when a `lab_transition` event moved validating to retired.
  - Proposals:
    - A proposal comes from `lab_propose` events. `proposal_id` is the prod revision and `deployment_revision_id` the dev source.
    - Its state is `proposed` while the revision is `proposed_public`. It is `approved` with `decided_at` once a `lab_publish` event names it, and `rejected` if it was retired unpublished.
  - A registration, which carries only name, digest, schema and runtime, creates a NEW serving revision over the model's newest pinned weights. The runtime becomes `<runtime>@<artifact_digest>` and the schema pair comes from the schema version.
    - Its label is `lab-<n>`. The `ponytail:` note: two concurrent registrations share a label and the registry refuses the second.
    - Its dev revision goes on the `<name tail>` dev endpoint, with limits copied from the model's newest deployment.
    - A model with no operator-imported weights is `not_found`. A model without deployed limits is `invalid_request`.
  - `propose(kind="rollback")` is `invalid_request`: rollback is the operator's (or R2's) listing CAS.
  - Every call first asks `LabAccess.require` for the actor's current (user, provider). The claimed `role` is ignored.
- Fake world: 7/7 cases pass.
  - The two-provider denial covers lists and moves.
  - A forged role is `forbidden` and a foreign user `not_found`.
  - A failed smoke reads failed and is never proposed.
  - Only a pinned, supported runtime and schema register.
  - The full journey register → smoke → propose → operator approve reads back from the stored rows.
- The PG half of these cases is NOT claimed. It needs WR-LSQ-9.

## (4) WR-I2L-2: control-service factory
- `infrx.lab.control.app:create_app` (uvicorn `--factory`, 127.0.0.1:8003 per `infra/lab/app/lab.json` on the tip) serves:
  - `/readyz`: 200 while `PgControlStore(INFRX_LAB_DATABASE_URL).db_now()` answers, otherwise 503 `{"status":"unavailable"}` with the error not echoed.
  - No docs, openapi or consumer routes.
- On this base it mounts NO Lab router: `routes/lab_control.py` and `lab_traces.py` exist only on the tip (batch #4). That is WR-I2L-2b.
- `test_control_app__serves_readiness_and_no_consumer_route` passes on fake and pg: real `PgControlStore.db_now` on l3.

## (5) WR-R2-2: `Serving` (R2's ServingControl)
- `serving(endpoint_id)` returns `(serving_ref, fence)`. The fence is the alias's current listing version.
- `serving_ref = lab:serving:<provider>:<deployment_revision_id>@sha256:<JCS sha256 of the ServingRevision>`.
- `rollback(endpoint_id, *, fence, to_serving_ref, reason)` finds the newest listing version whose ref equals the target and calls `LabControl.rollback(operator, alias, to_version, expected_version=fence)`. That is the store's audited `lab_rollback` CAS: a stale fence gives `StateConflict`, and a version that is not earlier gives `NotFound`. It returns the new version.
- Admitted jobs' pins are values and are never touched. The case pins a job before the rollback and checks it still names the rolled-back-from revision.
- Fake world case passes. The PG half needs WR-LSQ-9 (`endpoint_alias`, `listing_versions`).

## (6) WR-R3-1: `LabAccess.require`
- Failing seam first: `AttributeError: 'LabAccess' object has no attribute 'require'` on both fake and pg.
- After the change, `INFRX_D_TASK=l3 INFRX_MUTANTS=all pytest tests/l/access/test_mutants.py -k "require or well_formed or every_case"` exit 0: 4 passed (`require_waves_through` and `pg_require_waves_through` killed).

## Real evidence on l3 (LAB-ACCESS + LAB-PUBLISH, PgControlStore + A3 PgRegistry/PgCatalogDirectory, 13/13 pg cases)
- Two-provider denial: `a_provider_never_mutates_another_providers_registry[pg]`, plus the `require[pg]` case.
- A dev revision is never public: `a_dev_revision_never_reaches_app_discovery[pg]`.
- Publish and rollback CAS with a stale fence: `publication_and_rollback_are_compare_and_set[pg]` and `a_rollback_targets_an_earlier_servable_listing_only[pg]`.
- A dev key never serves a draft: `a_newer_unvalidated_revision_is_never_keyed_priced_or_served[pg]`, which is red before WR-L3-5 and green after.

## Lab App L4 journey J01/J02 on l4: NOT RUN
`apps/lab` on this base is only L1's shell (`tests/l/shell`). L4's `tests/l/ui/journey.test.ts` is on the tip. Command for the merged tree: `cd apps/lab && pnpm install --frozen-lockfile && pnpm test tests/l/ui/journey.test.ts`, then `make lab-test`.

## Mutants (own list)
- `INFRX_D_TASK=l3 INFRX_MUTANTS=all uv run --frozen pytest -q tests/l/control/test_mutants.py`: exit 0, 86 passed in 464 s.
  - 59 fake mutants killed: 33 L3 plus 26 new (22 in operations.py, 2 in app.py, 2 in the fake reads).
  - 22 PG mutants killed: 20 service mutants and 2 app mutants.
  - Well-formed, every-case and the 3 runner self-tests pass.
  - 0 survivors.
- First full run: 4 survivors, all fixed before the final run:
  - `register_keeps_base_schema`: the output ref alone refused, so the mutant now drops the whole capability update.
  - `register_without_limits`: was a crash death, not a kill. The mutant now invents limits.
  - `proposal_reads_approved_early` and `proposal_names_the_prod_revision`: the list's state and source are now asserted.
- `ruff check` on the new files: clean. Pre-existing E741 remains in fakes.py:113/116 (L3's).

## make api-test (INFRX_D_TASK=l3)
- Full run: `INFRX_D_TASK=l3 make api-test` exited 2: 5522 passed, 9 failed, 78 skipped, 9 xfailed in 4153 s.
  - `tests/d/test_outbox_relay.py[valkey]` ×7: the shared-lock failures.
  - `tests/d/test_schema_postgres.py::test_dur_rls__the_execute_surface_is_enumerated` and `tests/d/test_credit_schema.py::test_credit_privileges__service_reads_money_and_writes_through_seams`: base failures. authenticated may EXECUTE the six 0037/0038 doors (`lab_judge_*`, `lab_review_feedback`, `submit_feedback`), which base `tests/d/checks.py` does not enumerate. Batch #8's WR-LSQ-3 (cbd73efa, +7 lines in checks.py) enumerates them. None of this lane's paths is involved.
- tests/i/test_mutants.py did not fail in this run.
- Solo rerun of the 9 (`INFRX_D_TASK=l3 pytest` on those ids): the same 2 base failures. The 7 valkey cases fail with `ForeignContainer: infrx-d2-valkey exists and is not this checkout's`, because another lane holds the shared d2 Valkey. It was not free during this lane, so they were not rerun when free.
- Everything else, tests/g and tests/l included, passed.
- Note: a second, accidental `make api-test` (a shell backtick expansion while writing this file) was killed within about 2 minutes. Its result is not used.

## Wiring / schema requests
- **WR-LSQ-9 (lab-sql, `infrx/state/lab_control.py` PgControlStore)**: add `ControlReads`. These are four service_role reads with no migration; the fake states them.
  - `provider_servings(p)`: A3's `_SERVING` select with `where s.provider_org_id = %s order by s.created_at`, rows through `_record(ServingRevision, _SERVING_FIELDS, row)`.
  - `provider_deployments(p)`: `_DEPLOYMENT`'s columns, `where provider_org_id = %s order by created_at`.
  - `endpoint_alias(e)`: `select l.public_model_id from infrx.catalog_listings l join infrx.deployment_revisions d using (deployment_revision_id) where d.endpoint_id = %s order by l.version desc limit 1`.
  - `listing_versions(a)`: `select public_model_id, version, deployment_revision_id::text, rate_card_version from infrx.catalog_listings where public_model_id = %s order by version` → `Listing`.
  - Proof: switch `test_operations.py`'s `fake_world` cases to `world` (the fake-internal asserts read `w.control_store`, so give PgWorld the same few accessors) and run on l3.
- **WR-LAB-API-2 (coordinator, `infrx/gateway/pilot.py` `_lab`, on the tip)**:
  - Change: `store = PgControlStore(connect)`, `l3 = infrx.lab.control.LabControl(access, store, PgRegistry(connect), PgCatalogDirectory(connect), <engine>)`, and `LabControl(sessions, access, operations=Operations(l3, store))`.
  - Needs WR-LSQ-9 first.
  - `<engine>` is the G/W runtime smoke adapter (WR-L3-2, not built). Until it exists, compose an engine that raises `DependencyUnavailable` so `smoke` answers 503.
  - LAB_CONTROL stays OFF.
  - Proof: tests/g/lab_control with `operations=` the real adapter over the fake world.
- **WR-LAB-API-2b (merged tree)**: replace the five record classes in `operations.py` with `from ...gateway.routes.lab_control import Actor, Deployment, Model, Proposal, Registration` (they are identical at f9892c30).
- **WR-I2L-2b (merged tree, `infrx/lab/control/app.py`)**: compose `rt` from `INFRX_LAB_*` (sessions, `LabAccess(PgAccessStore)`, the WR-LAB-API-2 operations), then `lab_control.register(app, rt, control)` and `lab_traces.register(app, rt, …)`. Later add lab_evaluations, pipelines and releases the same way.
- **WR-R2-2 composition (coordinator, wherever R2's controller is built)**: `Controller(PgReleaseStore(connect), Serving(l3, store, OperatorSession(ops=None, principal="rollout:controller")), actor_id=…)`. Needs WR-LSQ-9. Real proof: R2's concurrent-controller case on key r2.
- **WR-L3-MK (`Makefile:23` api-mutants own-process PG line)**: add `tests/l/control/test_mutants.py`, run with `INFRX_D_TASK=l3`.

## Proposed rulings (propose, never number)
- **I2L control service.** `infrx.lab.control.app:create_app` is a thin FastAPI factory. It mounts ONLY the Lab routers (lab_control, lab_traces, later lab_evaluations/pipelines/releases) over the same composition as `pilot._lab`, plus `/readyz` over its own database login. The consumer gateway keeps LAB_CONTROL/LAB_TRACES off while this unit serves them.
- **Lab registration.** A Lab registration never declares weights. It is a new serving revision (runtime image by digest, schema pair) over the model's newest operator-imported, digest-pinned weights, with the model's deployed limits. A model's first weights and limits are an operator import. A provider `rollback` proposal is refused: rollback is the operator's (or R2's) listing CAS.
- **R2 serving ref.** `lab:serving:<provider>:<deployment_revision_id>@sha256:<JCS digest of the ServingRevision>`. R2's fence is the alias's catalog listing version.
- **Route record coarseness.** The route's `Deployment.state` (active|retired) and `smoke` are a lossy view of L3's seven states. A later revision should expose `DeploymentState` directly.

## Open issues
- The adapters' PG half waits on WR-LSQ-9. Smoke on the real stack waits on the G/W engine adapter (WR-L3-2).
- The L4 journey is NOT RUN (not on this base).

## Estimate (remaining for L3 to be accepted on the tip)
- Optimistic 1 h / likely 2.5 h / pessimistic 5 h. Confidence medium.
- Basis: WR-LSQ-9 is four SELECTs (lab-sql about 1 h). Switching the operations cases to `world` and running them on l3 takes about 0.5 h. Then there are the WR-LAB-API-2b/I2L-2b merged-tree follow-ups and a verify round (47–234 min per session-03).
- This lane took about 3 h against its 3/6/12 L3 analogue.

## Fix round (0-L3I-R1) — code head d068a483

Finding: `Operations.register` could not accept anything the Lab App's form sends, and it stored the submitted artifact digest as the runtime image digest. The side chosen is the App's. The App's form is left unchanged.

- **`name`** is the model's bare name in the workspace. The App's pattern is `[a-z0-9][a-z0-9-]{0,62}` and allows no `/`. The name is resolved only among the actor's provider's own serving revisions, by the last segment of `public_model_id`, so it stays provider-scoped. The dev endpoint is that name. A slug-qualified name (`nemostation/marlin-2b`) is now `not_found`.
- **`runtime`** is the image by digest (`<repo>@sha256:<hex>`) and is stored verbatim as `runtime_image_ref`. `runtime_image_digest` is that ref's own digest. A bare repo, a tag or an unsupported repo is refused by `LabControl.register` (`unsupported`) as `invalid_request`, with no row written.
- **`artifact_digest`** is the model's weights. It must be one of the shard digests the model was imported with (`weight_shard_digests`), otherwise `invalid_request` ("a Lab registration declares none"). The new revision keeps those weights and never writes the digest into the image. `Model.artifact_digest` reads `weight_shard_digests[0]`, and `Model.runtime` and `Deployment.runtime` read the full runtime ref, so the App's list shows back what it submits.
- Proposed ruling amended: *a Lab registration names the model by its bare workspace name, the runtime by `<repo>@sha256:<hex>`, and the weights by one of the model's imported shard digests (checked, never declared)*.
- Note for the App lane: the App's own fake-test REG (`vllm@sha256:bb`, `chat.v2`) is not a shape L3 accepts, because the digest is short, `vllm` is unsupported and only `chat.v1` is served. It is kept as a refusal case here. J01/J02 on the merged tree need a supported runtime by full digest, `chat.v1`, and the model's weights digest.

Tests first: the new case `test_operations__the_lab_apps_registration_shape_registers` covers four things. The App's exact REG is refused, both with its own name and with ours. The slug name is `not_found`. The App's shape with valid values registers, pinned to that runtime and those weights. The existing cases moved to the App shape. Before the fix: 6 failed, 2 passed. After: 8 passed.

New mutants, all killed: `register_slug_qualified_name`, `register_any_weights`, `register_runtime_repinned_to_artifact`, `register_image_digest_dropped`, `model_shows_the_runtime_digest`. `register_any_model_name`, `register_keeps_base_runtime` and `register_dev_endpoint_misnamed` were re-anchored.

| command (apps/infrx-api) | result |
|---|---|
| `uv run --frozen pytest -q tests/l/control tests/l/access -m "not pg"` (without the mutant files) | 36 passed |
| `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l/control -m pg` (without the mutant file) | 14 passed |
| `INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l/control/test_mutants.py` | **91 passed**: 64 fake mutants and 22 PG mutants killed, 0 survivors |
| `uv run --frozen ruff check` on the three changed files | clean |

Earlier mutant runs are recorded rather than hidden. A first run without `INFRX_D_TASK` failed 22 PG mutants on the default key. A first run on `l3` killed 8 PG mutants, and every later one was then `broken_runner`: a mutant copy's `infrx-l3-postgres` had been left in state `Created` (owner label `/tmp/l3-pg-mutant-pg_smoke_skipped-*`, a temp tree that no longer existed) and blocked the harness (`ForeignContainer`). That container was on this lane's own key. I removed it (`docker rm infrx-l3-postgres`), and the full rerun above is the result of record. `make api-test` was not rerun: the change is confined to `infrx/lab/control/operations.py` and the `tests/l/control` suite and mutant list, and those are rerun above.
