# Contracts v1 — executable encoding (F2 design refinement)

Coordinator-owned refinement of [contracts v1](01-contracts.md) that F2 encodes and every track consumes. It fixes the choices contracts v1 left to F2 (“F2 chooses concrete import/type syntax once”): file layout, vocabulary, serialization, configuration names, dependency set, test discovery and local-service namespaces. Where this file and `01`/`02` disagree, `01`/`02` win and the disagreement is a contract-revision request to the coordinator. Nothing here is a claim that code exists; F2 evidence records what was built.

## 1. Layout and ownership after F2

| Path | Content | Owner after F2 |
|---|---|---|
| `apps/infrx-api/pyproject.toml`, `uv.lock`, `.python-version` | Pinned Python environment and pytest configuration | Coordinator |
| `apps/infrx-api/infrx/config.py` | Settings object; the only module that reads the environment | Coordinator (tracks request names) |
| `apps/infrx-api/infrx/contracts/` | `ids`, `money`, `errors`, `records`, `ports`, `limits`, `fixtures/v1/*.json`, `fakes/`, `conformance/` | Coordinator; a change is a contract revision |
| `apps/infrx-api/tests/contracts/` | Runs every conformance suite against the fakes; fixture round-trips | F / coordinator |
| `apps/infrx-api/infrx/{auth,media,gateway/routes}/` | Extracted by F1 | G, M, G |
| `apps/infrx-api/infrx/{state,scheduling,worker,traces,judge}/`, `tests/{d,q,w,t,j,m,g}/` | Created by their tracks | D, Q, W, T, J, M, G |
| `apps/app/lib/contracts/` | TS DTO types, service interface, money helpers, fixtures, fixture-backed fake services | Coordinator; a change is a contract revision |
| `apps/app/tests/contracts/`, `apps/app/tests/{c,u,v}/` | Console contract tests; track suites | F / C, U, V |
| `Makefile` (repo root) | The canonical check commands | Coordinator |

Composition roots (`infrx/gateway/app.py`, console `layout.tsx`/navigation) stay coordinator-owned: tracks hand back an integration request naming the router/import/nav entry instead of editing them.

## 2. Python encoding

- Records are frozen pydantic v2 models (pydantic is already a FastAPI dependency) with `schema_version: int = 1`, `extra="forbid"`, UTC-aware datetimes serialized as RFC 3339 with `Z`, UUIDs as lowercase strings, money as decimal strings (§4). `records.py` holds exactly the contracts-v1 table: `AuthContext`, `NormalizedRequest`, `PriceSnapshot`, `Admission`, `Lease`, `Chunk`, `TerminalOutcome`, `TraceEnvelope`, `Feedback`, `JudgeRun`, plus the small value types they need (`MediaRef`, `Cursor`, `CapacityReservation`, `Usage`, `ConsentSnapshot`, `IndexEvent`).
- Ports are `typing.Protocol` classes with `async` methods in `ports.py`, one per row of the contracts-v1 ports table, same operation names. They take and return records, raise `DomainError` subclasses, and receive clock/ID/storage collaborators by constructor injection. No port exposes SQL, keys of another tenant or a raw object path.
- `fakes/` holds one in-memory adapter per port plus `FakeClock`, `SequentialIds` and `FailurePlan` (deterministic injection: fail/raise/crash-after-commit on the *n*-th call of a named operation). Fakes implement the real semantics of `02-durable-protocols.md` (idempotent admission, capacity and hold accounting, generation fencing, publication marker, single terminal settlement, bounded trace offer); they are the executable specification, not stubs that return canned values.
- `conformance/` holds importable suites `run_<port>_conformance(factory)`; `tests/contracts/` applies them to the fakes, and each owning track must apply the same suite to its real adapter (D to PostgreSQL, Q to memory and Valkey, W to the engine adapter, T to the spool sink, M to its store). Passing the suite on a fake never marks a task integrated.
- The fake engine supports the fault modes `04-verification.md` requires: prefill stall, mid-stream stall, malformed/missing usage, cancellation race, abrupt exit, reasoning delimiters split across chunks.

## 3. Vocabulary (string values are frozen)

| Enum | Values |
|---|---|
| `JobState` | `preparing`, `queued`, `running`, `succeeded`, `failed`, `cancelled`, `expired` |
| `ExecutionMode` | `sync`, `stream`, `async` |
| `TerminalCause` | `completed`, `client_cancelled`, `client_disconnected`, `sync_deadline`, `queue_wait_expired`, `deadline_exceeded`, `invalid_media`, `preparation_failed`, `engine_error`, `engine_incomplete`, `lost_after_publication`, `journal_write_failed`, `retries_exhausted`, `platform_error` |
| `UsageCertainty` | `authoritative`, `unknown` |
| `SettlementState` | `settled`, `released_free`, `held_unknown`, `released_platform_absorbed` |
| `HoldState` | `held`, `settled`, `released`, `unknown` |
| `ReservationKind` | `preparation`, `inference`, `journal_bytes` |
| `ChunkEventType` | `progress`, `delta`, `usage`, `error`, `terminal` |
| `OutboxKind` | `prepare_dispatch`, `inference_dispatch`, `usage_projection`, `trace_projection`, `feedback_projection`, `judge_projection`, `callback_delivery` |
| `TraceMode` | `off`, `minimal`, `full` |
| `TraceLossReason` | `none`, `memory_budget`, `metadata_budget`, `queue_full`, `disk_budget`, `disk_error`, `shutdown`, `malformed` |
| `TraceOfferResult` | `accepted_in_memory`, `dropped` |
| `FeedbackChannel` / `AuthorRole` | `api`, `console` / `customer`, `operator`, `judge` |
| `JudgeRunState` | `dry_run`, `reserved`, `submitting`, `submitted`, `ambiguous`, `collecting`, `settled`, `quarantined`, `cancelled` |
| `UploadState` | `created`, `finalized`, `aborted`, `expired` |
| `Role` | `owner`, `member`, `operator`, `service` |

Public identifiers: `request_id` UUIDv4 string; `Inference-Id` header = `request_id`; chat id `chatcmpl-<request_id>`; `job_handle` = `job_` + 32 bytes from `secrets.token_urlsafe` (never derived from the UUID); upload handle `upl_…`, feedback id `fb_…` likewise opaque. Stream cursor / SSE `id` = `<generation>-<sequence>` (decimal integers).

### Error envelope

`{"error": {"message", "type", "code", "param", "request_id", "infrx": {…optional retry_after_s etc.}}}`. `message` is a fixed safe string per code; never upstream exception text, URLs with credentials, storage keys or secrets.

| HTTP | `type` | `code` values |
|---|---|---|
| 400 | `invalid_request_error` | `invalid_request`, `unsupported_parameter`, `unsupported_media`, `media_fetch_failed`, `context_length_exceeded`, `invalid_cursor` |
| 401 | `authentication_error` | `invalid_api_key` |
| 402 | `insufficient_quota` | `insufficient_credit` |
| 403 | `permission_error` | `forbidden`, `org_suspended`, `model_not_entitled` |
| 404 | `not_found_error` | `not_found` (also every ownership failure) |
| 409 | `conflict_error` | `idempotency_conflict`, `result_pending`, `state_conflict` |
| 410 | `gone_error` | `result_expired`, `journal_expired`, `replay_gap`, `idempotency_expired` |
| 413 | `invalid_request_error` | `request_too_large` |
| 429 | `rate_limit_error` | `capacity_exhausted`, `journal_capacity_exhausted`, `rate_limited` (always `Retry-After`) |
| 500 | `server_error` | `internal_error` |
| 503 | `server_error` | `dependency_unavailable` (always `Retry-After`) |
| 504 | `server_error` | `deadline_exceeded` |

In-stream terminal error events use the same object with codes `stream_interrupted` or `status_unknown`; `status_unknown` never asserts a committed terminal state. Internal (non-HTTP) domain errors additionally include `stale_lease`, `already_terminal`, `not_claimable`, `capacity_unavailable`, `budget_exceeded`, `consent_missing`, `ambiguous_submission`.

Headers: in `Authorization`, `Idempotency-Key` (≤255 chars), `Prefer: respond-async`, `Last-Event-ID`; out `Inference-Id`, `Preference-Applied: respond-async`, `Retry-After`, `Idempotency-Replayed: true`, `Server-Timing`. The old `X-Infrx-Accept-Async` upgrade header is superseded by `Prefer`.

## 4. Money

`Decimal` only, context precision ≥ 40, scale `0.00000001` USD. Debit = `(prompt_tokens × input_rate + completion_tokens × output_rate) / 1_000_000`, quantized once with `ROUND_HALF_UP`. Maximum hold uses the same formula on the ceilings with `ROUND_CEILING`. JSON form is a fixed-point string with exactly eight fractional digits (`"0.10000000"`); parsers accept any plain decimal string and reject exponents, NaN, floats and negative zero. TypeScript never uses `number` for money: `Money` is a branded string with BigInt-scaled (1e8) `parse/format/add/sub/compare` helpers. Fixtures include the half-up boundary, ceiling-vs-half-up divergence, zero-token requests, negative ledger deltas and a 20-digit total.

## 5. Configuration names and frozen defaults

Names reuse the older spec where the meaning is unchanged. All are read only in `infrx/config.py`; values are the provisional defaults of contracts v1.

| Name | Default | Name | Default |
|---|---|---|---|
| `INFRX_MODE` | `dev` (`dev`/`test`/`pilot`; `pilot` refuses to start unauthenticated or unmetered) | `DATABASE_URL` | unset (required in `pilot`) |
| `MAX_REQUEST_BYTES` | 100663296 | `INTAKE_TIMEOUT_S` | 30 |
| `MAX_MEDIA_BYTES` | 67108864 | `MAX_VIDEO_SECONDS` | 120 |
| `FETCH_CONNECT_TIMEOUT_S` | 3 | `FETCH_TIMEOUT_S` | 20 |
| `FETCH_MAX_REDIRECTS` | 3 | `PROBE_TIMEOUT_S` | 10 |
| `PREPARATION_TIMEOUT_S` | 120 | `TRANSCODE_MIN_TIMEOUT_S` / `TRANSCODE_DURATION_FACTOR` | 15 / 0.5 |
| `QUEUE_WAIT_INTERACTIVE_S` | 10 | `QUEUE_WAIT_ASYNC_S` | 600 |
| `GENERATION_TIMEOUT_S` | 300 | `TTFT_TIMEOUT_S` / `TPOT_STALL_S` | 60 / 20 |
| `LEASE_TTL_S` | 120 | `LEASE_HEARTBEAT_S` | 40 |
| `MAX_PREPUBLICATION_RETRIES` | 2 | `SSE_KEEPALIVE_S` | 10 |
| `STREAM_BATCH_MS` | 50 | `JOURNAL_EVENT_MAX_BYTES` | 1048576 |
| `JOURNAL_JOB_RESERVE_BYTES` | 16777216 | `JOURNAL_TOTAL_BYTES` | 1073741824 |
| `JOURNAL_CHUNK_TTL_S` | 3600 | `RESULT_TTL_S` | 86400 |
| `PROCESSING_CACHE_TTL_S` | 604800 | `IDEMPOTENCY_TTL_S` | 86400 (after terminal) |
| `TRACE_CONTENT_MAX_DAYS` | 90 | `TRACE_METADATA_MONTHS` | 13 |
| `PREPARATION_CONCURRENCY` | 2 | `MAX_ACTIVE_JOBS` / `_PER_ORG` / `_PER_KEY` | 64 / 16 / 8 |
| `ENGINE_MAX_NUM_SEQS` | 8 | `WORKER_CONCURRENCY` | 10 |
| `MAX_OUTPUT_TOKENS` | 2048 | `MAX_CONTEXT_TOKENS` | 32768 |
| `TRACE_CAPTURE_BYTES` | 268435456 | `TRACE_METADATA_RESERVE_BYTES` | 8388608 |
| `TRACE_QUEUE_MAX` | 10000 | `TRACE_SPOOL_DIR` | unset (capture disabled) |
| `TRACE_SPOOL_MAX_BYTES` | 10737418240 | `TRACE_SPOOL_MIN_FREE_BYTES` | 2147483648 |
| `TRACE_FSYNC_INTERVAL_S` | 2 | `JUDGE_MODE` / `JUDGE_LIVE_BUDGET_USD` | `dry_run` / `0` |
| `VALKEY_URL` | unset ⇒ memory scheduler | `CLICKHOUSE_URL`, `S3_MEDIA_BUCKET`, `S3_TRACE_BUCKET` | unset |

Existing F1 settings (`UPSTREAM`, `MODEL_ID`, `GATEWAY_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `MAX_INFLIGHT`, `USAGE_LOG`, …) keep their names until G/W retire them. Schema versions: records 1, error envelope 1, trace envelope 1, spool segment 1, rubric versioned by J.

## 6. Dependencies (pinned once by F2)

`pyproject.toml` + `uv.lock`, Python 3.12, installed with `uv sync --frozen`. Core: `fastapi`, `uvicorn`, `httpx`, `pydantic`. Extras so the request path imports only what it uses: `state` = `psycopg[binary,pool]` 3.x; `scheduling` = `valkey`; `traces` = `clickhouse-connect`, `boto3`; `judge` = `anthropic`; dev group = `pytest`. No pytest plugins: async tests drive `asyncio.run` as the baseline tests do. A track needing another package requests it from the coordinator; nobody else edits the manifest or lock. Console: `packageManager` stays `pnpm@9.15.9`, `engines.node` `>=22.18` (built-in TypeScript stripping), no new console dependency in F2. React component behaviour is verified through pure `.ts` view-model tests in the track suites and E's browser suite; F2 adds no DOM test stack.

## 7. Test discovery and canonical commands

- Python: `[tool.pytest.ini_options]` in `apps/infrx-api/pyproject.toml` with `testpaths=["tests"]`, `pythonpath=["."]`, `--import-mode=importlib` so same-named files in track directories cannot collide. Track suites live in `apps/infrx-api/tests/<track>/`.
- Console: `pnpm test` = `node --test "lib/**/*.test.ts" "tests/**/*.test.ts" "app/**/*.test.ts" "components/**/*.test.ts"` (patterns quoted so Node expands them). A guard test walks the tree and fails when any `*.test.ts` file is outside those patterns: an undiscovered test is a failed setup.
- Root `Makefile`: `make api-test`, `make console-test`, `make console-lint`, `make bench-test` (E's `models/marlin2b/tests`), `make check` (all of them). Evidence reports cite these.

## 8. Task-local services

Real-service tests use disposable containers named `infrx-<task>-<service>`, database `infrx_<task>`, object prefix `test/<task>/`, temp directories under the session scratch area, and these host ports so parallel worktrees never collide: PostgreSQL D `55432`; Valkey Q `56379`; ClickHouse T `58123` (HTTP) / `59000` (native); S3-compatible store M `59100`, T `59110`; E2 compose `55500–55599`; G/W/J/C use fakes until integration. Never point a test at Supabase production, AWS or a container another session created. D alone adds SQL migrations, numbered with the next four-digit integer after `0002`.

## 9. Console DTO surface (TypeScript)

`apps/app/lib/contracts/services.ts` declares the named operations of the contracts-v1 console row — `usage`, `balances`, `ledger`, `traces`, `traceDetail`, `traceContent`, `feedback.list/submit`, `settings.get/update`, `adminGrant`, `adminOrgs`, `judgeRuns` — each taking a trusted `SessionContext` (user, org, role, operator flag) resolved server-side plus validated filters, returning `Result<T, ServiceError>` where `ServiceError = {code, message, request_id?}` with the codes of §3. Lists use `Page<T> = {items, next_cursor}` with opaque cursors and a hard `limit ≤ 100`. `WalletBalance = {ledger_total, reserved_total, available}` as `Money`. Trace content availability is the closed set `available`, `metadata_only`, `pending`, `lost`, `expired`, `off`. Feedback DTOs carry `channel` and `author_role` as server-set fields the client cannot supply. The fixture-backed fake implements the same interface with owner/member/operator sessions, two organizations (for cross-tenant denial), pagination over >100 rows, every content state and injected failures, so U and V build against it while C implements the real services behind the identical interface.

## 10. Contract revision r1 — coordinator rulings (2026-09-20)

Rulings on the change requests raised while encoding F2. Both halves implement these; conformance suites and fixtures follow them.

| # | Question | Ruling |
|---|---|---|
| R1 | `PREPARATION_CONCURRENCY` vs admission | `PREPARATION_CONCURRENCY=2` is the host worker-pool size only. Admission reserves a `preparation` capacity unit against a separate cap `MAX_PREPARING_JOBS` (default 8, provisional); exhaustion is `429 capacity_exhausted`. Both reservations are taken in the admission transaction as `02` requires. |
| R2 | New limits vs F1 legacy settings | F1's `FETCH_TIMEOUT_S` (30), `MAX_VIDEO_MB`, `MAX_REDIRECTS` keep their names, defaults and behaviour until M1 replaces the fetch path. The contracts-v1 limits for the new path are named `MEDIA_FETCH_TIMEOUT_S` (20), `MEDIA_FETCH_CONNECT_TIMEOUT_S` (3), `MEDIA_FETCH_MAX_REDIRECTS` (3), `MAX_MEDIA_BYTES`; this supersedes the unprefixed fetch names in §5. M1 retires the legacy names at cutover. |
| R3 | Feedback body | Field semantics follow `research/traces/06` §2: `name` ∈ `thumb`, `rating`, `correction`, `comment`; `value` is boolean for `thumb`, integer 1–5 for `rating`, non-empty text for `correction`/`comment`; optional `comment`. An empty body is `400 invalid_request`. `Idempotency-Key` is required on `POST /v1/feedback` and console submit. `channel`, `author_role`, calibration membership remain server-set. |
| R4 | Deadlines | `NormalizedRequest`/job carries `accepted_at`, absolute `deadline_at`, and a `budgets` snapshot `{preparation_s, queue_wait_s, generation_s, first_token_s, stall_s}` captured at admission so later configuration changes never alter an accepted job. |
| R5 | Queue wait across requeues | Cumulative from the first durable `queued` transition; prepublication requeues do not reset it. |
| R6 | Re-admitting an existing `request_id` without the idempotency path | `409 state_conflict`, no side effects. Replay exists only through org + operation + idempotency key. |
| R7 | `recover` clock | `JobStore.recover()` takes no caller time. Adapters read the database clock inside the transaction; fakes read the injected clock that stands for it. Same rule for every expiry/lease/24 h decision. |
| R8 | Ambiguous judge run | New operator-only operation `JudgeCoordinator.resolve_ambiguous(run, operator, resolution, reason)` with `resolution` ∈ `adopt_provider_evidence` (requires the provider id; continues to collection) or `release_reservation` (terminal `quarantined`, reservation released); append-only audit record; never creates a second submission. |
| R9 | Judge consent | Consent is rechecked against the **current** consent record at `begin_submit`, not only against the snapshot taken at `reserve`; revocation after reservation blocks egress and releases the reservation. |
| R10 | Tenant coherence | Every port operation receiving two tenant-bearing arguments verifies they name the same organization and fails `not_found`/`forbidden` otherwise. Admission rechecks key revocation, org suspension and entitlement inside its transaction (`01` identity section); the fake models this with an injectable entitlement source. |
| R11 | Monetary inputs | Holds, grants via `adminGrant`, settlements and judge costs reject negative, non-finite or over-scale amounts at the port boundary; ledger deltas may be negative only for debits and compensating entries created by the store itself. Money domain is `numeric(20,8)` in both languages: |value| < 10^12, exactly the same accept/reject set in Python and TypeScript. |
| R12 | Trace mode vs content | A `minimal`-mode envelope never carries content; a sink rejects (`malformed`) rather than stores it. All accepted bytes are charged to the budget. |
| R13 | Console amendments | Accepted: `keys.list/create/revoke` in `ConsoleServices`; `adminGrant.target_org_id` (operator-only; the idempotency scope includes target org and payload); separate `usageSummary`/`usageDaily`; `TraceMode` `off`/`minimal`/`full` supersedes `07-console-spec`'s `metadata`; `UsageRow.settlement_state` is nullable for non-terminal rows; `judgeRuns` is owner and operator only. Amended: `LedgerEntryKind` keeps `purchase` as a legacy read-only value (historical rows must render; nothing creates it). Off-mode requests have no trace row, so they never appear in `traces`; `traceDetail` reached from a usage row reports availability `off`. `ERROR_CODE_HTTP_STATUS` is accepted and must equal the Python table (checked by the G0 parity test). `tsconfig` target stays ES2017 (use `BigInt()` calls). |
| R14 | Module list | §1/§2 also include `wire.py` (HTTP envelopes) and `tasklocal.py` (§8 helper). `UNKNOWN_USAGE_RECONCILE_S` = 86400 joins §5. |
| R15 | E/I ownership | E owns `models/marlin2b/corpus/**` and `models/marlin2b/tests/**` (run by `make bench-test`). I owns `infra/**` except `infra/clickhouse/`. Pinned tool downloads must use a versioned URL plus sha256. Raw evidence stays in git-ignored scratch with digests in reports until I2 provisions a durable evidence store. Journal-probe thresholds in `infra/README.md` are `est.` and become a gate only after E4 measures them. |

## Verification log

- 2026-09-20: Authored by the coordinator from contracts v1, durable protocols, module handoffs and the current code layout before F2 dispatch. Design only; F2 evidence will record what is implemented and any amendment requested during encoding.
- 2026-09-20: Revision r1 rulings added after two adversarial review rounds of both F2 halves; they are binding for the remaining F2 fixes and for D1/G1/J1/C1 consumers.
