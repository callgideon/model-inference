# WR-V1M-2 — `/lab/v1/traces`, the provider trace read (lane lab-api, wave LW2)

- Base `f4bceeba` · code head `f36a6ab4` · branch `codex/w5-lab-api` · worktree `.claude/worktrees/codex-w5-lab-api`
- Tasklocal key `l4` (PG 57503): `LabAccess` over `PgAccessStore`, grants written through lab-sql's RPCs, `PgServing` over the seeded registry. ClickHouse: `infrx-t2i-clickhouse` on the t2i block (57540/57541), image pinned at `tests/integration/compose.yaml:68`, for `ClickHouseTraceRows` only; removed after the run.
- Oracles: TRACE-TENANT, LAB-ACCESS, SPLIT-CONTRACT.

## Changed paths (all owned)

`apps/infrx-api/infrx/gateway/routes/lab_traces.py`, `apps/infrx-api/tests/g/lab_traces/{conftest,test_lab_traces,test_lab_traces_stack,mutants,test_mutants}.py`.

## Design (one line each)

- `GET /lab/v1/traces?provider_org_id=&limit=&cursor=` → `{data, next_cursor}`; `GET /lab/v1/traces/{request_id}?provider_org_id=` → item (+ `content` when granted).
- Order per call, nothing cached: session (`lab_auth`) → current **developer+** membership (a viewer holds aggregate health only: per-request rows are 403) → the provider's own serving versions (`ProviderServing`; `PgServing` = `select serving_version_id, model_id from infrx.serving_versions where provider_org_id = %s`, service_role, like T2I's `PgPins`) → rows only for those serving versions (another provider's requests are unreachable, not filtered) → T3: `request` tombstone or past 13 months = absent; past the content bound, `content` tombstone or never stored = `content_available: false` → per (grantor org, model) `LabAccess.authorize_content` for **both** `request_content` and `response_content` under `provider_sharing` (the stored object holds both) → else metadata only.
- Metadata item: request_id, times, mode, loss_reason, serving_version_id, model_id, model_revision, rate_card_version, policy_version, `access`. **Never** org_id, key_id, size or content without a grant. Granted adds grantor_org_id, content_complete, content_bytes, content_available.
- Pages: newest first by (started_at, trace_id), `limit` 1..200 (default 50), cursor = base64url of `[started_at, trace_id]` (no organization); malformed/naive cursor or bad limit = 422. ponytail: equal instant AND trace id across orgs would share a position (trace ids are unique per segment).
- `ClickHouseTraceRows`: `trace_envelopes FINAL WHERE serving_version_id IN {serving:Array(UUID)}`, parameters bound, reuses T2I's `COLUMNS`/`_row`. ponytail: scans (key leads with org_id); a serving_version_id skip index when it matters.
- Unmounted unless `rt.lab_traces`; `lab_auth.refusal` renders every failure.

## Red first

| cmd | exit | result |
|---|---|---|
| `uv run --frozen pytest -q tests/g/lab_traces` (tests written, no route) | 2 | `ImportError: cannot import name 'lab_traces' from 'infrx.gateway.routes'` |
| first green attempt (`INFRX_D_TASK=l4`) | 1 | 14 passed, 2 failed: the "expired" fixture was 40 d old, under the 90 d content bound → fixture at `content_days + 1 min`; a 400 d row added for the metadata bound |
| first `INFRX_MUTANTS=all tests/g/lab_traces/test_mutants.py` | 1 | `mounted_without_traces` broken_runner (`traces.retention` read at register time) → read lazily; 29/29 on rerun |

## Green (on `f36a6ab4`)

| cmd | exit | result |
|---|---|---|
| `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/g/lab_traces/test_lab_traces.py` | 0 | 16 passed (9 cases; 7 `world` cases × fake + **real PG l4**) |
| `INFRX_LAB_API_STACK=1 uv run --frozen pytest -q tests/g/lab_traces/test_lab_traces_stack.py` | 0 | 1 passed (real ClickHouse: FINAL dedup of a replay, serving scope, NULL serving excluded, cursor strictness, request scope) |
| `INFRX_LAB_API_STACK=1 INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_traces/test_mutants.py` | 0 | 32 passed: 29/29 killed (5 STACK_ONLY) + 3 meta |

## Checks (lane, both tasks)

See `## Lane checks` appended below.

## Interface requests / wiring

- WR-LAB-API-1 (the patch) composes `LabTraces` in `pilot._lab_traces` over `CLICKHOUSE_URL`, T3's `Retention` and the trace bucket at the shipper's `infrx/` prefix; `LAB_TRACES` without `CLICKHOUSE_URL`/`S3_TRACE_BUCKET` refuses startup.
- **WR-LAB-API-4 (→ lab-sql, optional)**: `PgServing` reads `infrx.serving_versions` directly (service_role select granted by 0007); a named RPC if the registry is closed to direct reads.
- **WR-LAB-API-5 (→ lab-app-lw2, V1M)**: `apps/lab/lib/services/traces/` adapter over this shape; the three tenancy suites rewrite against it (provider A vs B, consumer-only, revoked grant, expired content, lost capture = `loss_reason`, capture off = no rows).
- Deploy note (enable step, not this lane): the gateway image needs the `traces` extra (clickhouse-connect, boto3) before `LAB_TRACES` is turned on.

## Ruling proposal (unnumbered)

LAB-TRACES: a provider reads per-request rows of its own deployments only, as a current developer+ member; content only under the grantor organization's current `provider_sharing` grant naming the model and **both** content categories; otherwise metadata only, without organization, key, size or content; T3 tombstones and bounds apply before anything is shown; cursors are server-minted and carry no organization.

## Open issues

- The V1M acceptance (the Lab explorer on `lab-v1m` with the merged projection) is lab-app's.

## Estimate (remaining for WR-V1M-2)

optimistic 1 h / likely 2 h / pessimistic 5 h, confidence medium. Basis: G4F 1/2/4 per route plus the ClickHouse read; remaining = wiring apply, enable-step extra, one verify round.

## Audit log

- 2026-09-27: written for `f36a6ab4` (lane lab-api, LW2).

## Lane checks (both tasks, on `f36a6ab4` unless noted)

| # | cmd | exit | result |
|---|---|---|---|
| 1 | `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/l/access` (baseline, key sanity) | 0 | 33 passed, 1 skipped |
| 2 | `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/g/lab_auth` | 0 | 12 passed (real PG l4 half included) |
| 3 | `uv run --frozen pytest -q tests/g/lab_control` | 0 | 11 passed |
| 4 | `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/g/lab_traces/test_lab_traces.py` | 0 | 16 passed (real PG l4 half included) |
| 5 | `INFRX_LAB_API_STACK=1 ... tests/g/lab_traces/test_lab_traces_stack.py` (infrx-t2i-clickhouse 57540) | 0 | 1 passed; container removed after |
| 6 | `INFRX_MUTANTS=all ... tests/g/lab_auth/test_mutants.py` | 0 | 27 passed = 24/24 killed + 3 meta |
| 7 | `INFRX_MUTANTS=all ... tests/g/lab_control/test_mutants.py` | 0 | 30 passed = 27/27 killed + 3 meta |
| 8 | `INFRX_LAB_API_STACK=1 INFRX_MUTANTS=all ... tests/g/lab_traces/test_mutants.py` | 0 | 32 passed = 29/29 killed + 3 meta |
| 9 | `INFRX_MUTANTS=all ... tests/g/lab_traces/test_mutants.py` (no stack: STACK_ONLY deselected) | 0 | 27 passed = 24/24 + 3 meta |
| 10 | `INFRX_D_TASK=l4 make api-test` | 2 | 5136 passed, 43 failed, 78 skipped, 9 xfailed, 8 errors (1:10:53). **All 8 errors** = `tests/i/pooler.py:110 BlockingIOError` (the shared i8 pooler lock `/tmp/infrx-i8-postgres-*.lock`, held by another lane's run); **all 43 failures** = `tests/i/test_mutants.py` (fails its baseline under a non-default key: recorded, not blocking, per the brief) |
| 11 | rerun of #10's 8 errors alone (`tests/i/test_observe.py test_pooler.py test_privilege_probe.py test_rollback_drill.py`) | 1 | 28 passed, 1 xfailed, 8 errors: same `BlockingIOError` - the i8 lock was still held by the concurrent lane; to be rerun by the coordinator when free |
| 12 | wiring proof, scratch clone at `f36a6ab4` + patch r0: `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/contracts tests/i/test_packaging.py tests/g tests/w` | 0 | 2580 passed, 15 skipped (30:35) |
| 13 | final patch `git apply --check` + `git apply` on a fresh `--shared` clone at `f36a6ab4`, then `tests/g/test_startup.py tests/g/test_mutants.py tests/contracts/test_config_and_imports.py tests/i/test_packaging.py tests/g/lab_auth tests/g/lab_control tests/g/lab_traces` | 0 | 446 passed, 1 skipped |
| 14 | the wiring's G mutants in the scratch (`python -m tests.g.mutants <15 names>`: the 6 re-anchored ROUTERS mutants, feedback's 2, the 8 new lab ones) | 0 | 15/15 killed after the fix round (`lab_control_composed_when_off` survived and `lab_traces_without_projection` died by `OperationalError` on the first run: the case now stubs `_lab_traces` and fails any ClickHouse connect by assertion) |

Patch r0 → final: + the `_lab` composition case, its 3 G mutants, the Makefile `api-mutants` line (#13/#14 cover them).

## Fix round (2026-09-28, finding 1-LAB-API-RSI-1)

Finding: the patch put `**_lab(settings, connect),` between the two lines coordinator commit `37859776` (on `claude/consumer-v1`) re-anchored `given_stores_replaced` to, so on the merged tree that G mutant was misdeclared (anchor 0 times). Fix (patch only, same file name): the `_lab` line moves above the G4F feedback block in `adapters_from_env`, so the `...feedback_api else {}),\n **adapters}` anchor is intact; no mutant re-anchor needed. Route/auth code unchanged.

| # | cmd | exit | result |
|---|---|---|---|
| F1 | `--shared` clone, `claude/consumer-v1` + `merge 6487d36a` + `git apply --exclude=Makefile` fixed patch; `python -c ... run_mutant(given_stores_replaced)` | 0 | `killed` (was `misdeclared ... appears 0 times`) |
| F2 | same merged tree: `INFRX_D_TASK=l4 INFRX_MUTANTS=all pytest -q tests/g/test_mutants.py` | 0 | 416 passed (22:19): every G mutant killed, incl. the lab/feedback/ROUTERS ones |
| F3 | `--shared` clone at `6487d36a` + fixed patch (`git apply --check` then apply): `INFRX_D_TASK=l4 pytest -q tests/contracts tests/i/test_packaging.py tests/g tests/w` | 0 | 2581 passed, 15 skipped (18:40) |
| F4 | same clone: `run_mutant(given_stores_replaced)` | - | `misdeclared`: pre-existing at `f4bceeba` (the anchor `37859776` fixed is not on this base); killed once merged (F1) |
