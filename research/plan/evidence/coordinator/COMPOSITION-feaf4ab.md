# COMPOSITION (LW2): the coordinator-delegated wiring batch WR-T-4 → WR-N-3 → WR-B-5 + one E4 regression

- Lane `composition`, branch `codex/w5-composition`, worktree `.claude/worktrees/codex-w5-composition`, base `2252e5a0`.
- Code head `feaf4ab4` (commits `c349ebd5` wirings + unit cases + mutants + t2f proof, `ba1ce992` b1 proof, `feaf4ab4` lint); this file, the 08 settings rows and the update JSON are the next commit.
- Keys: t2f (PostgreSQL 57549 through the D harness; ClickHouse `infrx-t2f-clickhouse` 57543/57544 and MinIO `infrx-t2f-s3` 57545, both started by this lane with the E2-pinned images and removed at the end), b1 (PostgreSQL 57520, model-fake 57521), n2 (57516, the G/contracts mutant run's PostgreSQL key), e4b (the App gate). No hosted Supabase, pilot box, AWS/SSM/S3, Vercel or secret touched; no migration.
- Pattern followed: G4F's `85a8dd66..f4bceeba` (switch off by default, composed only when on, DEPLOYMENT_EXPECTED, preflight NOT_SETTABLE, startup cases + mutants, one settings row).

## Changed paths (composition roots and their tests only)

- `apps/infrx-api/infrx/config.py` — `DeploymentSettings.trace_pumps` / `lab_eval_worker`, both `False`.
- `apps/infrx-api/infrx/worker/__main__.py` — `compose(..., evaluators=None, targets=None)`; `trace_pumps(settings, mode, connect)`; `lab_eval(mode, connect, objects, evaluators, targets, worker_id)`; `EvalRuns` (the `eval_run` handler); cadence constants.
- `apps/infrx-api/deploy/preflight.py` — `NOT_SETTABLE["TRACE_PUMPS"]`, `NOT_SETTABLE["LAB_EVAL_WORKER"]`.
- `apps/infrx-api/tests/contracts/test_config_and_imports.py` — `DEPLOYMENT_EXPECTED` += both switches, `False`.
- `apps/infrx-api/tests/w/test_worker_main.py` — 7 new cases (10 items); `tests/w/worker_main_mutants.py` — 20 new mutants.
- `apps/infrx-api/tests/w/test_worker_traces_pg.py` (t2f + stack), `tests/w/test_worker_lab_eval_pg.py` (b1) — real-service proofs, outside the mutant runner (the T2I/N2/B1 `_pg` pattern).
- `research/plan/08-contracts-v1-encoding.md` — the two settings rows under `FEEDBACK_API`.
- `apps/infrx-api/tests/g/mutants.py` — one stale G anchor re-cut (base defect from `f4bceeba`, command 13/13b).
- Not touched: `gateway/app.py`, `gateway/pilot.py`, the ROUTERS literal and its G anchors (no route was mounted: WR-N-3 below), `tests/g/test_startup.py`, any product module.

## WR-T-4 — trace pumps in the worker (`TRACE_PUMPS`, default off) — DONE

- Off (default): `compose` builds none of it; the worker's housekeeping stays the three M6 loops (`retention`, `cache_keeper`, `journal_expire`), no spool directory is created, no ClickHouse client exists.
- On: `TRACE_SPOOL_DIR`, `CLICKHOUSE_URL`, `S3_TRACE_BUCKET` required (refusal names the missing one; a spool/ClickHouse failure at construction is a `RuntimeMisconfigured` naming `TRACE_PUMPS`, never a value). Composes exactly T3's WR-T-4 calls: `SpoolTraceSink(Wall)` on `TRACE_SPOOL_DIR`; `ship.build_shipper(limits, spool, endpoint_url=S3_ENDPOINT_URL)`; tasks `trace_ship` (every 10 s: `spool.rotate()` then `shipper.ship()`), `trace_retention` (every 300 s: `retention.expire()` then `retention.sweep()`), `feedback_projection` (every 10 s: `FeedbackProjector(PgFeedbackOutbox(<the worker pool's connect>), shipper.retention.feedback, retention=shipper.retention).pump()`). D5 pins: `build_shipper`'s own `ship.PgPins` (the T2F-carried lookup; WR-4's `admission_pins` is superseded).
- Capture stays off: nothing builds a sink in the gateway. The worker's sink only adopts sealed segments left in the directory; `holds=` stays None (T3's note).

## WR-B-5 — the Lab `eval_run` worker (`LAB_EVAL_WORKER`, default off, Lab-only) — DONE (composition), sources BLOCKED on other lanes

- On: `OutboxRelay(PgLabDataStore(<pool connect>), EvalRuns(...), worker_id=<worker>-lab-relay)` pumped every 5 s, and `store.recover` (`lab_recover`) every 30 s. `EvalRuns.enqueue(event)`: other kinds are refused (stay pending, error recorded: B3's `checkpoint_received` is not this handler's); `run_status` → `resolve(run_ref)` for the event's own provider → `targets(record.serving_ref)` → `evaluation.resume(store, run_id, evaluator=evaluators(record.evaluator_ref), provider_org_id)` → `Runner(store, objects, endpoint, deployment, worker_id, LAB_EVAL_LIMITS(30 s, 3, 2, 2)).run(frozen)`. **Never `freeze`** (B1's recheck: after a revocation `freeze` is Forbidden and the run would never end). A `wallet_exhausted` stop raises `InsufficientCredit` (not acknowledged: redelivered after the relay's window); a budget stop, a cancel or completion acknowledges.
- The Lab objects are the worker's media store (`S3_MEDIA_BUCKET` + its prefix, `lab/<provider>/...` keys; M6 retention deletes only `media/`, `uploads/`, `payloads/`), which is also WR-N-3's store decision.
- On without sources: refuses to start (`LAB_EVAL_WORKER needs an evaluator source (WR-B-2(b)) and a dev target source (WR-B-3)`). Neither exists on the base (no evaluator table, no L3 target resolution), so `python -m infrx.worker` cannot run it yet; `compose(evaluators=, targets=)` is the seam the b1 proof and the future lanes fill.

## WR-N-3 — Lab object store + dataset bundle upload route (`LAB_DATASETS`) — BLOCKED, nothing mounted

- There is no bundle route to mount: `infrx/datasets/imports` is a library (no `register`, no FastAPI), and N1's WR-N-3 addresses the route to "G/L lanes". Writing one is a product module (outside this lane's paths).
- Its authority does not exist at the gateway: N1 WR-N-2 requires `provider_org_id` + actor from the L2 port's *current membership with an import-capable role* (R156/R160), i.e. an individual. The gateway authenticates API keys only; the one provider credential (`provider_dev`, R70) is scoped to one private endpoint and `auth_context` keeps no individual for it. Authorizing data uploads with it would widen R70 — a ruling, not wiring. N4 (LW4) owns the import wizard ("long imports run as backend jobs").
- Decided here only: the Lab object store = the media store under `lab/` (used by WR-B-5). No `LAB_DATASETS` switch was added (a switch that mounts nothing would be a lie).

## Commands (from `apps/infrx-api` unless stated)

| # | Command | Head | Exit | Result |
|---|---|---|---|---|
| 1 | FAIL-FIRST `uv run --frozen pytest -q tests/w/test_worker_main.py -k "switch or trace_pumps or lab_eval or eval_run or cannot_finish"` (cases written, no implementation) | base+tests | 1 | **10 failed**: `DeploymentSettings has no attribute 'trace_pumps'`, `compose() got an unexpected keyword argument 'evaluators'`, `module has no attribute 'EvalRuns'` (3), housekeeping set without the trace tasks, DID NOT RAISE (4) |
| 2 | same, implemented | c349ebd5 | 0 | 10 passed |
| 3 | `uv run --frozen pytest -q tests/w/test_worker_main.py tests/contracts/test_config_and_imports.py tests/i/test_packaging.py tests/i/test_worker_unit.py` | c349ebd5 | 0 | 370 passed, 4 skipped |
| 4 | `INFRX_MUTANTS=all … tests/w/test_worker_main_mutants.py -k "<the 20 new>"` first run | pre | 1 | 19 passed, 2 broken_runner (KeyError / AttributeError deaths) → cases now assert (`built.get`, `type(refused.value) is RuntimeMisconfigured`); rerun 2 passed |
| 5 | `INFRX_D_TASK=t2f INFRX_T2F_STACK=1 … tests/w/test_worker_traces_pg.py` | c349ebd5 | 0 | **1 passed**: an adopted sealed segment of a CREDIT request shipped by the composed step to real ClickHouse with the pins PostgreSQL admitted (serving/rate card/policy), content in MinIO at `infrx/trace/<org>/…`, segment unlinked; feedback accepted through `infrx.accept_feedback` projected 1/1 and acknowledged (outbox pending 1 → 0); retention pass `{cleaned 0, held 0, failed 0}`; second ship pass 0 rows |
| 6 | FAIL-CHECK of #5 with the projector on another database (`PgFeedbackOutbox(connector(<dead dsn>))`, reverted) | c349ebd5+edit | 1 | `DependencyUnavailable: postgres: OperationalError` in the feedback step |
| 7 | `INFRX_D_TASK=b1 … tests/w/test_worker_lab_eval_pg.py` | feaf4ab4 | 0 | **2 passed**: (a) the event `freeze` wrote is pumped, all 4 cases scored, 4 paid calls, acknowledged, second pump reads 0, recover 0; (b) worker killed mid-run (event stays pending), grant revoked, clock +31 s, `lab_recover` ≥ 1, the redelivery resumes: 4/4 cases `failed` (revoked), no result, 1 paid call in all, acknowledged |
| 8 | FAIL-CHECK of #7 with `EvalRuns` calling `freeze` on the run record (reverted) | ba1ce992+edit | 1 | 1 failed, 1 passed: the revocation case dies `Forbidden: schedule: a sample's grant is not in force for provider_sharing` - the recheck the brief names |
| 9 | `uv run --frozen pytest -q -rs tests/w/test_worker_lab_eval_pg.py` (no key) | feaf4ab4 | 0 | 2 skipped, visibly naming `INFRX_D_TASK=b1` |
| 10 | `uv run --frozen ruff check` on every changed Python file | feaf4ab4 | 0 | all checks passed (first run: 4 F811 on re-imported fixtures, fixed in feaf4ab4) |
| 11 | E4 regression, every switch off: `INFRX_D_TASK=t2f INFRX_T2F_STACK=1 INFRX_M_S3_ENDPOINT=http://127.0.0.1:57545 INFRX_M_S3_LOCAL_CREDS=1 uv run --frozen pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | feaf4ab4 | 1 | **2523 passed, 5 skipped, 10 errors** (23m12s); all 10 = `HarnessBusy: another run holds /tmp/infrx-d2-valkey-55463.lock` (holder: `codex-w5-rollout-control`'s pytest, `fuser`), in `test_w5_pg`×4, `test_prep_worker_pg`×3, `test_worker_main_pg`×3 |
| 12 | rerun of #11's contended cases alone once the lock was free (first retry: HarnessBusy again, holder `codex-w5-lab-api`): same env, `pytest -q -rs tests/w/test_w5.py tests/w/test_prep_worker.py tests/w/test_worker_main.py -k _pg__` | feaf4ab4 | 0 | **14 passed** (every `_pg__` case of the three files, the 10 errored ones included) → the regression is 2533 passed, 5 skipped, 0 failed |
| 13 | `INFRX_D_TASK=n2 INFRX_MUTANTS=all uv run --frozen pytest -q -rs tests/contracts/test_mutants.py tests/g/test_mutants.py` | feaf4ab4 | 1 | **948 passed, 1 failed** (56m39s): `given_stores_replaced` **misdeclared** - its anchor in `gateway/pilot.py` (`"jobs": PgJobStore(...), "pool": pool,\n **adapters}`) no longer exists since G4F's `f4bceeba` put the feedback entry between them; a base defect (pilot.py is unchanged by this lane), so `make api-mutants` fails on the base too |
| 14 | `INFRX_D_TASK=t2f INFRX_M_S3_ENDPOINT=… INFRX_M_S3_LOCAL_CREDS=1 INFRX_MUTANTS=all uv run --frozen pytest -q -rs tests/w/test_worker_main_mutants.py` | feaf4ab4 | 0 | **81 passed** (9m41s): the service-free list incl. the 20 new mutants and the PostgreSQL list (valkey lock free at the time), well-formed, every case covered, anchors present |
| 13b | the G anchor re-cut in `tests/g/mutants.py` (`given_stores_replaced` now anchors `…feedback_api else {}),\n **adapters}` and drops `**adapters`; same invariant, same case, same `dies_by`): `INFRX_MUTANTS=all … tests/g/test_mutants.py -k "given_stores_replaced or anchor or well_formed or every_case"` | (next) | 0 | 5 passed (killed; anchors present) |
| 15 | root: `PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright make app-e2e` (namespace e4b; apps/app + apps/lab `pnpm install --frozen-lockfile` first) | ba1ce992→feaf4ab4 (tree dirty: this lane's uncommitted 08 rows / the F811 fix; no runtime file) | 0 | **PASS**: preflight, console-test, console-lint, console-typecheck, browser-journey all PASS; journey checks 20/20 PASS; **APP-LOCAL cells 17/17 PASS** (4 delegated to E3C-CELLS 9227e9ed: s05/s08, s14, s15, s16) = the last accepted gate (17/17); teardown: no `infrx-e4b` container, ports free. Verdict `/tmp/infrx-e2c-app-e2e-jjasuuy4/verdict.json` |

## Tests and the mutants naming them (`tests/w/worker_main_mutants.py`)

| Case | Oracle | Mutants |
|---|---|---|
| `…every_trace_and_lab_switch_is_off_and_composes_nothing` | both off by default; off composes nothing (E4 unchanged) | `main_trace_pumps_on_by_default`, `main_lab_eval_on_by_default`, `main_trace_switch_ignored`, `main_lab_switch_ignored` |
| `…trace_pumps_refuse_to_start_without_their_settings` (3) | on needs its three settings, named | `main_trace_settings_not_required` |
| `…trace_pumps_ship_retain_and_project_on_the_workers_stores` | WR-T-4's calls, order, stores | `main_trace_switch_composes_nothing`, `main_trace_ship_without_rotate`, `main_trace_sweep_without_expire`, `main_trace_shipper_endpoint_ignored`, `main_feedback_projection_off_the_pool`, `main_feedback_projection_ignores_retention` |
| `…the_lab_eval_worker_refuses_to_start_without_its_sources` | no source, no worker | `main_lab_sources_not_required` |
| `…the_lab_eval_worker_pumps_d7s_outbox_and_recovers` | relay over D7 on the pool, recover timer | `main_lab_switch_composes_nothing`, `main_lab_store_off_the_pool`, `main_lab_recover_not_scheduled` |
| `…an_eval_run_delivery_resumes_the_created_run_never_freezes` | resume, never freeze; the event's provider; the record's evaluator | `main_redelivery_freezes_again`, `main_eval_provider_from_the_payload`, `main_evaluator_by_the_serving_ref` |
| `…a_delivery_the_handler_cannot_finish_stays_pending` (2) | wallet stop not acked; budget stop acked; other kinds not taken | `main_wallet_stop_acknowledged`, `main_every_stop_pending`, `main_other_kinds_taken` |

## Wiring requests (for other owners; none applied)

- **WR-COMP-1 (lab-sql, WR-B-2(b))**: an evaluator table / read RPC so the worker's `evaluators(ref) -> spec` is D7's; then `main()` composes it and `LAB_EVAL_WORKER` can start.
- **WR-COMP-2 (lab-access, WR-B-3 / L3)**: `targets(serving_ref) -> (HttpDevEndpoint, DeploymentRevision)` from the dev deployment registry (secret by name only).
- **WR-COMP-3 (T, hosted enable of TRACE_PUMPS)**: `ship.build_shipper` builds `PgPins(connector(DATABASE_URL))` with I8's default role rule; on the dedicated `infrx_runtime` login (R127) that `set role service_role` fails, so every pins lookup would hold its segment. Pass the worker's `connect` (or `set_role=not pilot.dedicated_login(dsn)`) through `build_shipper`; also check the runtime login's grants for `PgFeedbackOutbox`'s outbox DML (0004 gives it to service_role). Hosted enable is not this lane's.
- **WR-COMP-4 (capture wiring, T/G)**: when the gateway builds the spool sink, the ship step moves to that process (one writer per spool directory, `SpoolTraceSink`'s lock); the worker keeps retention + projection.
- **WR-N-3 (G/L + N4)**: carried unchanged, blocked on a Lab provider identity at the gateway (see above) and a route owner.
- **Makefile**: none (the new cases live in `tests/w/test_worker_main.py`, whose mutant list `api-mutants` already runs; the two `_pg` files run in `make api-test` and skip without their keys).

## Ruling proposal (coordinator numbers it)

- Worker-side Lab/trace pumps: every Lab or trace pump the worker runs sits behind its own deployment switch, off by default and never installer-settable; an `eval_run` delivery rebuilds the created run with `resume` (never `freeze`), acknowledges only a finished, cancelled or budget-stopped run, and leaves a wallet-stopped one pending; the Lab's content objects live in the media store under `lab/<provider>/` until a Lab bucket is decided.

## Open issues

- `LAB_EVAL_WORKER` cannot be enabled by any deployment until WR-COMP-1/2 land (fail closed by design).
- A run longer than the relay's 30 s `redelivery_s` is handed to a second worker process too; D7 leases make it a duplicate delivery (B1's drill), `ponytail:` note in `lab_eval`.
- Cadences are constants (`ponytail:`), not settings.

## Estimate (remaining for this lane to merge)

optimistic 0.5 h / likely 1.5 h / pessimistic 4 h, confidence medium. Basis: implementation and all checks took ~3.5 h of lane time; what remains is one verify round (G4F's took one fix round) and, if the reviewer wants WR-N-3 forced, a ruling first.

## Fix round (handback 3549edd0, finding 1-F1)

**1-F1 (major): `EvalRuns` acknowledged a delivery whose run was still running.** Fixed in `infrx/worker/__main__.py`: `enqueue` returns `True` only when the report's `state` is final (`RUN_FINAL = succeeded/failed/cancelled`) or `stopped == "budget_exhausted"`; a wallet stop still raises `InsufficientCredit`, and any other outcome (nothing pending but a dead attempt's cases still `leased`, since `lab_recover` has not reaped them yet and emits no event) raises `errors.ResultPending`. The relay records the error on the row and hands it out again after its window, by which time `lab_recover` has returned the leases to `pending`. The settings row (`LAB_EVAL_WORKER`, 08) now states the same rule; it matches the ruling proposal above ("acknowledges only a finished, cancelled or budget-stopped run").

Tests first (red before the fix: 2 failed, `[queued]`/`[running]`):
- `tests/w/test_worker_main.py::…a_run_left_unfinished_is_not_acknowledged[queued|running]`: Runner returning `{'state': <unfinished>, 'stopped': None}` raises; `succeeded/failed/cancelled` are acknowledged. The fake Runner now reports a `state`; the budget-stop case of `…cannot_finish_stays_pending` runs with `state='running'` so it proves the budget clause on its own.
- Mutants (`tests/w/worker_main_mutants.py`): `main_unfinished_run_acknowledged` (guard → `if True:`, killed by the new case) and `main_budget_stop_pending` (budget clause dropped, killed by `PENDING`).
- b1 proof `tests/w/test_worker_lab_eval_pg.py::…a_redelivery_before_recover_is_not_acknowledged`: the finding's ordering: crash the first pump (`Dying`), clock +31 s, pump again WITHOUT `lab recover` → `ResultPending`, cases still `leased` and none `pending`, the event still pending; then `lab recover` ≥ 1, clock +31 s, pump → acknowledged, event pending 0, every case scored.

| # | Command | Head | Exit | Result |
|---|---|---|---|---|
| F1 | `pytest tests/w/test_worker_main.py -k "eval_run or unfinished or cannot_finish"` (before the fix) | 3549edd0 + tests | 1 | 2 failed (the new cases), 3 passed |
| F2 | `pytest tests/w/test_worker_main.py` | fix | 0 | 53 passed, 4 skipped |
| F3 | `ruff check` on the four touched files | fix | 0 | clean |
| F4 | `INFRX_D_TASK=n2 INFRX_MUTANTS=all pytest -q -rs tests/w/test_worker_main_mutants.py` | fix | 0 | **76 passed, 7 skipped** (3m17s): 83 collected incl. the 2 new mutants, both killed; the 7 skipped are the PostgreSQL list (`no local S3 endpoint`), which this fix does not touch (81 passed at feaf4ab with the t2f S3) |
| F5 | `INFRX_D_TASK=t2f INFRX_T2F_STACK=1 INFRX_M_S3_ENDPOINT=http://127.0.0.1:57545 INFRX_M_S3_LOCAL_CREDS=1 pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | fix | 1 | **2489 passed, 6 skipped, 32 failed, 14 errors** (15m44s); all 46 = `ForeignContainer` (pgharness.py:209): `infrx-t2f-postgres` and `infrx-d2-valkey` are held by a reviewer checkout (`scratchpad/rvcomp-cmo-2774177`), `infrx-b1-postgres` by `scratchpad/rv-comp`; no product failure. The service-free set is green |
| F6 | `INFRX_D_TASK=b1 pytest -q tests/w/test_worker_lab_eval_pg.py` | fix | 1 | **not run**: 3 errors, `ForeignContainer: refusing to use infrx-b1-postgres: another checkout's run (…/scratchpad/rv-comp)`. The b1 proof (incl. the new ordering case) has to be rerun once that reviewer container is gone |

Harness note: one earlier attempt at F5 ran without `INFRX_D_TASK` and created `infrx-d1-postgres` labelled with this checkout; it was stopped at once. Removing that container was not permitted from this session, so it is still there (ours by label, d1 default ports) for the coordinator to remove.

Not rerun in this round: `make app-e2e` (the fix is inside the `LAB_EVAL_WORKER`-on path only, which the App gate never composes; last run PASS 17/17 at feaf4ab), and `tests/g`/`tests/contracts` mutants (no gateway or contract file changed).

Rulings: numbered R182 (worker-side Lab and trace pumps: own switch each, off, never installer-settable; eval_run resumes never freezes, acknowledges only a finished, cancelled or budget-stopped run, wallet stop pending; Lab objects under `lab/<provider>/` in the media store) in `research/plan/08-contracts-v1-encoding.md` §10 at the composition merge (2026-09-28); WR-N-3 stays unmounted and unruled.
