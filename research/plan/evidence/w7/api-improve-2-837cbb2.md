# AP-10 (lane api-improve-2, wave 7 batch 2): slice 10c

- Base `b05eb6f4`. Branch `codex/w7-api-improve-2`. Code head `837cbb27`; the slice commits are `21310fce` (the operation), `c16c889e` (PG), `e38be3a8` (j10 probe), `ab71df95` and `837cbb27` (import resume). The evidence commit follows them.
- Key `ap10`: PostgreSQL 57566 only (`INFRX_D_TASK=ap10`, container `infrx-ap10-postgres`). This lane touched no other key, no d1/55432 container, no hosted service, no box and no secret. No migration was written.
- Nothing is enabled. The new datasets-role pass is composed only through WR-AP10C-1, and only with `LAB_TRACE_DATASETS=1` (default off). No route starts the operation yet (see "Open"). The eval worker is not touched.

## Changed paths (all owned)

- `apps/infrx-api/infrx/lab/datasets/from_traces.py` (new; namespace package `infrx.lab.datasets`).
- `apps/lab/tests/e2e/evaluate/backend.py`: the j10 probe (`carried`).
- `apps/infrx-api/tests/ap10/`:
  - `test_from_traces.py` (unit, 8 cases);
  - `test_from_traces_pg.py` (ap10 PG, 1);
  - `test_e2e_probe.py` (unit, 1);
  - `test_import_resume_pg.py` (ap10 PG, 1);
  - `mutants.py` (+19 mutants: 18 `ft_*` and 1 `j10_*` on a second runner, `PROBE`, whose layout copies the e2e backend beside the package).
- `research/plan/evidence/w7/api-improve-2-837cbb2-WR-AP10C-1.patch`: the wiring patch, verified transiently.

## 10c: the trace -> dataset operation (`infrx.lab.datasets.from_traces`)

The module reuses existing parts and replaces nothing:
- N3's `lineage.select`: permitted traces become an immutable D7 version, with lineage per sample and content bounds.
- N2's `versions.derive`: a new immutable version with a deterministic, leakage-trapped train/validation/holdout split.
- 0060's `ControlOps`.
- AP-07's grants, read through L2 (`LabAccess.authorize_content` over `infrx.lab_access_grants`).

### `start` (the selection, the route's half)

1. The actor must be a provider workspace session and a current developer+ member (`acting_provider`).
2. The grantor's CURRENT grant must permit `request_content` and `response_content` of the model for both `provider_sharing` and `training`. Annotation counts as `training`, as `console.data_use` documents. The body must keep a holdout (`train_bp + validation_bp < 10000`).
3. The selection is written once to `lab/<provider>/lineage/selections/<selection id>.json`. It records the body, the user, the grant id and VERSION, and the instant. The selection id is derived from (provider, Idempotency-Key). Then `ControlOps.start("dataset.from_traces", …)` runs, with `resource_kind=dataset_selection`.
4. Writing the selection first means a worker never leases an operation without its selection. A key reused with another body is a 409, even when the first attempt died between the two writes. The selection keeps its input hash for this check.

### `work` (the materialisation, the datasets pass)

1. Lease the operation, then run the same gate again. The grant must still be the selected VERSION.
   - A revocation, an expiry or any new version since selection refuses: `failed`, code `forbidden`, `field_errors=[grant/grant_not_current]`.
   - The refusal happens before any phase, so C2 is never asked and nothing is published.
2. `select` publishes `<dataset>@<version>`. It checks `provider_sharing` again per request and omits expired content.
3. `derive` publishes `<dataset>@<version+1>` over it. The manifest records the holdout.
4. The outcome (both refs, the split digest, the holdout ids, every omission, the grant version) is written once beside the selection.
5. Crashes and refusals:
   - A crash republishes the same bytes under the next fence (D7 and the objects are write-once on identical bytes).
   - A 5xx or 429, or an unexpected failure, finishes nothing; the lease lapses.
   - `cancel_requested` is honoured at the next phase.

### Production ports (`pg_ports`)

- L2 over `PgAccessStore`, plus D7 and D6F feedback.
- C2: `C2Content` over `content.ContentAccess(PgContentRefs)`. Every ref is issued to the operation's user, bound to the selected grant version's ref, for `training`. Content C2 does not serve is `Gone`, which means an omission.
- `model_of`: the row's serving version mapped to its model, read from 0007 `serving_versions` (WR-N3-4).

### Red before green

- At the base, `infrx.lab.datasets` does not exist, so `test_from_traces.py` errors at collection: `1 error in 0.80s`.
- The probe case fails with `AttributeError: module … has no attribute 'carried'`. At the base the probe was `getattr(gateway, name) is not None`. Since WR-AP10-1 composes a catalog whose listing is the honest 503, that probe would report every port carried and let j10 bind over the route suite's fake catalog.
- The WR-AP10C-1 composed test fails without the worker patch: `1 failed`.

## The LAB-E2E evaluate probe (E6L j10)

`backend.carried(composition, provider)` counts a port as carried only when it is present. For the catalog it also requires `catalog.catalog(provider)` to answer; `DependencyUnavailable` means not carried. With today's composition `world.composed` reads `{store, experiments, ledger: true, catalog: false}`, so j10 stays NOT RUN, its message names `['catalog']`, and it never reaches a PASS no composition earns. When SR-AP10-1 lands, the catalog answers, the port counts as carried and j10 binds.

The reason label in the runner still names the old lanes. WR-AP10C-2 renames it to SR-AP10-1.

## Import resume under the corrected oracle

`test_import_resume_pg.py` uses 0051's queue and the composed datasets role on ap10. It asserts the oracle the durable queue actually has:

- **An interrupted import is not failed.** A worker dies after 2 staged writes and the pass counts `retry 1`, with the job still `running`. After the lease lapses, the same job id resumes and is `succeeded` once (`attempts 2`, `accepted 5`). The dataset record exists exactly once, and the same id again answers that job.
- **A refused import is `failed` for good (R243).** The same id again answers the failed job; no new job is created and the pass does nothing. The resume is `requeue` under a new id, which is then `succeeded` while the old id stays `failed`.

The Lab journey N4-J01 (`apps/lab/tests/n/journey.test.ts` over `apps/lab/tests/n/backend.py`'s in-memory job dict) still resumes a failed import by re-POSTing the same id. That is the pre-0051 oracle, and the production route does not behave that way. WR-AP10C-3 corrects it.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/ap10/test_from_traces.py` (module set aside, base state) | 2 | 1 error at collection (red) |
| `uv run --frozen pytest -q tests/ap10/test_e2e_probe.py` (before `carried`) | 1 | 1 failed (red) |
| `INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10/test_from_traces_pg.py` | 0 | 1 passed |
| `INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10/test_import_resume_pg.py` | 0 | 1 passed |
| `INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10 tests/n tests/w/test_lab_workers.py tests/g/lab_datasets tests/g/lab_evaluations` | 0 | 206 passed, 1 skipped |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap10/test_mutants.py` | 0 | 38 passed: 36 mutants killed (17 + 19 new), 0 survivors; well-formed and every-case green |
| `uv run --frozen pytest -q ../../tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed (the ap10 list is already in api-mutants) |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 458 errors (baseline 458; none new) |
| WR-AP10C-1 applied transiently: `pytest -q tests/w/test_lab_workers.py` | 0 | 35 passed (the new case included); without the worker half the new case fails |
| WR-AP10C-1 applied transiently: `INFRX_MUTANTS=all pytest -q tests/w/test_lab_workers_mutants.py -k 'lw_trace_datasets or lw_import_jobs or well_formed or every'` | 0 | 15 passed: 3 new `lw_trace_datasets_*` killed; the existing import anchors are intact |
| WR-AP10C-1 applied transiently: `uvx pyright … infrx/lab/workers/__main__.py tests/w/test_lab_workers.py` | 0 | 39 errors, the same as without the patch (no new error) |

`lab-*` targets were not run. The one Lab file touched is a Python harness (`apps/lab/tests/e2e/evaluate/backend.py`), which no `lab-test`/`lab-lint`/`lab-typecheck`/`lab-build` input includes. Its check is `tests/ap10/test_e2e_probe.py` plus its mutant. `make lab-e2e` (key l4) is the coordinator's rerun.

## Wiring requests

- **WR-AP10C-1**: the datasets role works trace-dataset operations, OFF by default.
  - Files: `apps/infrx-api/infrx/lab/workers/__main__.py`, `tests/w/test_lab_workers.py`, `tests/w/lab_workers_mutants.py`. The exact patch is `research/plan/evidence/w7/api-improve-2-837cbb2-WR-AP10C-1.patch` (`git apply`).
  - `TRACE_DATASET_PASS_S = 5.0`. In `_datasets`, when `env["LAB_TRACE_DATASETS"] == "1"`, a `trace_datasets` task runs `every(TRACE_DATASET_PASS_S, from_traces.work(PgControlOps(connect), from_traces.pg_ports(connect, objects, retention), worker_id=worker_id))`.
  - The existing return dict is kept line for line (`**traced`), so the `lw_import_jobs_*` anchors stand.
  - Composed test: `test_lab_workers__the_datasets_role_works_trace_dataset_operations_only_when_on`. It checks that the pass is off without the switch, and that with the switch it has its cadence, `PgControlOps` on the role's pool, the role's objects and the worker id. It comes with 3 mutants.
  - The box env file gains `LAB_TRACE_DATASETS=1` only by a coordinator decision, after SR-AP10C-1.
- **WR-AP10C-2**: E6L j10's reason names what it waits on.
  - `tests/integration/lab_evaluate/runner.py:117`: j10 `lanes` becomes `["SR-AP10-1"]`.
  - `OUT_OF_SCOPE` gains `"SR-AP10-1": "product WR: SR-AP10-1"`. Keep the three old keys for the recorded 24a7a065 verdict.
  - `scenarios_eval.py:373`: `lw.not_run("j10", "SR-AP10-1", why=…)`.
  - `test_e6l_runner.py:62,77-81`: same values.
  - Without it, j10 is still NOT RUN (honest), but under the old lane names.
- **WR-AP10C-3**: the Lab journey N4-J01 uses the corrected import-resume oracle (`apps/lab/tests/n/journey.test.ts:70-82`, `apps/lab/tests/n/backend.py`).
  - Compose the production `lab_datasets` router with 0051's `PgLabImportJobs` and the datasets pass, in place of the in-memory job dict.
  - A worker crash mid-staging resumes the same id after the lease lapses.
  - A `failed` import resumes through `requeueImport` and is read under the new id.
  - Assert that re-POSTing the failed id answers `failed`. The Python half of this oracle is `tests/ap10/test_import_resume_pg.py`.
- **WR-AP10C-4** (route, for the owner of the Lab datasets routes or AP-09d): a new R270 route `POST /lab/v1/providers/{provider}/datasets/from-traces`, mounted when LAB_DATASETS is on.
  - Body `from_traces.TraceDataset`, `Idempotency-Key` required.
  - The actor comes from `rt.actors` (provider workspace session) and is passed to `from_traces.start(ops, access, objects, actor, key, body)`.
  - Response: 202 with `Started.operation.doc()`; errors through `control.error_response`.
  - A no-store GET of the outcome: `from_traces.outcome(objects, provider_org_id, selection_id=op.resource_id)`.
  - It needs a composed `PgControlOps` in `lab_surfaces` (none is composed in the gateway today).
  - Composed test: start returns 202 queued; a replay returns the same `operation_id`; another body under the same key is 409; a viewer gets 403; a key audience gets 403.

## Schema requests (to api-schema-2; no SQL written here)

- **SR-AP10C-1**: the datasets worker's login (the `LAB_DATABASE_URL` login of the datasets role) needs these grants:
  - EXECUTE on `infrx.control_op_pending/lease/advance/finish(jsonb)`. This is the same "worker-login EXECUTE on the 0060 doors" api-artifacts asked for; name the datasets login too.
  - EXECUTE on `public.lab_content_ref_issue`/`lab_content_ref_redeem` (0041).
  - A read of `infrx.serving_versions(serving_version_id, model_id)`, either as a grant or as a SECURITY DEFINER `infrx.lab_serving_model(uuid)`. `pg_ports.model_of` then uses the door with no other change.
  - Until these grants exist, the pass refuses with 503 per pass, which is why WR-AP10C-1 keeps it off.

## Proposed ruling (unnumbered)

A trace-derived dataset is bound to the grant VERSION it was selected under. Materialisation re-checks membership, `provider_sharing` and `training` on the current grant, and refuses (`grant_not_current`, nothing published) if that grant is not the selected version. This covers revocation, expiry and any re-scoping. Annotation is `training`. Every trace dataset carries a non-empty holdout allotment, and the holdout ids are recorded in the outcome.

## Open / not done

- No route starts the operation yet (WR-AP10C-4). The operation is complete and tested at the module, PG and worker-composition levels.
- Real C2 (`PgContentRefs`) is composed in `pg_ports` but is a stand-in in the PG test (`FakeContent`, as N3's PG half), because C2's SQL needs a captured trace body on a task-local ClickHouse-less key.
- j10 binds only after SR-AP10-1 (batch-1 request) lands; then rerun `make lab-e2e` and `--only j10`.
- The 10d/10e slices belong to batch 3.

## Estimate (remaining for 10c closure)

- Hours: 2 / 4 / 7 (optimistic / likely / pessimistic).
- Confidence: medium.
- Basis: WR-AP10C-1 is verified transiently. WR-AP10C-2 is a label edit. WR-AP10C-3 is one journey (Lab TS + backend over the l-key PG), about 2 h. WR-AP10C-4 is a thin R270 route over `from_traces.start` plus a composed `PgControlOps`, about 1–2 h. SR-AP10C-1 is grants only.
