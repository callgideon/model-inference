# F2 (Python half) — review-fix pass on the typed contracts and executable spec

## Task and status

| Field | Value |
|---|---|
| Task | F2-py — freeze typed contracts, pins and executable fixtures (Python half of F2), **resumed to fix eight blocking review findings** |
| Owner | Claude Opus 5 (1M context) implementation session, dispatched by the coordinator |
| Status | **implemented** (not integrated: the coordinator merges; no real service and no console check ran here) |
| Oracles claimed | F-CONTRACT, F-BASE |
| Supersedes | `F2-py-440d0c7.md` (first pass; that report's claims about the terminal journal event and about one intent per judge run were refuted by the review and are corrected here) |

## Source

| Field | Value |
|---|---|
| Base SHA | `fab9fbe` (coordinator-recorded committed base) |
| Implementation SHA | `cdf6b87` |
| Commits in this pass | `b8a39f3` fixes + regression cases and tests · `cdf6b87` contracts README decisions and amendment requests |
| Earlier commits (unchanged) | `09ad82f`, `82ad650`, `ec153e9`, `b0fe5b8`, `440d0c7`, `8401e59` (first-pass evidence) |
| Branch / worktree | `codex/f2-contracts-py` in `.claude/worktrees/codex-f2py` |
| Integration target | `claude/infrx-impl` (coordinator only) |
| Console half | `apps/app/**` belongs to the parallel agent; untouched here |

## Findings addressed

Each finding was reproduced against `8401e59` before the fix, fixed at its root cause, and is now covered by a conformance case (so every track's real adapter inherits it). All eight new/extended cases were re-run against a `git archive` copy of the pre-fix code and **all eight failed there**; they pass after the fix.

| # | Finding | Root-cause fix | Regression case |
|---|---|---|---|
| 1 | Re-admitting the same `request_id` overwrote the job and leaked the first hold | `FakeJobStore.admit` refuses a request already admitted (`state_conflict`): 06 keys jobs by the request UUID and allows no second active hold per request; the supported retry stays the idempotency key | `dur_admit__a_request_uuid_is_admitted_once` |
| 2 | `JudgeCoordinator.reserve` on an existing run reset it, so an ambiguous/submitted run got a second intent and a second provider batch | `reserve` is idempotent per `run_id`: it returns the stored run untouched | `judge_budget__reserving_a_run_twice_does_not_reset_it` |
| 3 | A negative maximum hold was accepted, inflating `available` and driving the ledger negative from a zero grant | `admit` rejects `hold < 0` with `invalid_request` before any lock is taken | `dur_cap__a_negative_maximum_hold_is_refused` |
| 4 | The terminal journal event sat outside the settling transaction and could fail after settlement committed | `_terminalize` (used by `complete`, `cancel` and `recover`) writes the terminal event through the store's journal in the same transaction; `finalize_in_transaction` reads it back idempotently; the write is charged but never refused | `dur_settle__the_terminal_event_belongs_to_the_settling_transaction` (plus the existing `dur_output__the_terminal_event_is_written_once_with_the_settlement`) |
| 5 | Only the global journal budget was enforced, so one job stored past its 16 MiB reservation | `_Journal.store` enforces the per-job reservation as well as the global budget (02: "per-job and global byte limits") | `dur_cap__a_job_cannot_store_past_its_journal_reservation` |
| 6 | The queue-wait budget restarted on every prepublication requeue | queue time accumulates across attempts (`queued_elapsed` charged at claim); a requeue keeps what was already spent (01: "no extension through retries") | `dur_output__queue_wait_does_not_restart_on_a_requeue` |
| 7 | `begin_submit` never rechecked consent, so a snapshot revoked after reservation still authorized egress | `begin_submit` requires `consent.allows_evaluation(now)` (02: consent must be current **at submission**) | extended `judge_budget__revoked_or_missing_consent_is_refused_before_egress` |
| 8 | `MediaStore.stage` overwrote an existing finalized upload by handle, across tenants | objects are keyed by `(org_id, handle)`; staging over an existing handle with different content is a conflict, identical content is idempotent | `media_sec__staging_never_replaces_an_existing_object` |

Non-blocking notes also applied (cheap and clearly right): `money.parse` and every `ids` pattern use `fullmatch`, so a trailing newline no longer passes; `PriceSnapshot` rates must be `>= 0`; `TerminalOutcome` validates `cause` against `state` (`records.CAUSE_STATES`), closing the `succeeded` + `engine_error` free-success hole; an empty `append` no longer sets the publication marker; `config._coerce` parses money through `money.parse` and rejects `nan`/`inf`/negatives, with `config.MUST_BE_POSITIVE` refusing a zero on bounds a zero would disable; 08 §3's header vocabulary is `HEADER_*`/`PREFER_RESPOND_ASYNC` constants in `wire.py`; `ports.JobStore.admit` types `caps` as `tuple[ReservationKind, ...]` and documents that amounts come from the store, never the caller; `factories._job_hooks` builds one journal per store instead of a throwaway per `publish`.

Notes deliberately **not** acted on (recorded instead, with owners): the ambiguous-run resolution gap, `recover(now)` taking a caller-supplied time, feedback body/idempotency-key validation, and `quarantine` of a settled run — all four are now amendment requests 5–7 plus a refinement note in `infrx/contracts/README.md`, because each changes the contract surface a track codes against. The first-pass note that "all 54 names of 08 §5" are encoded is corrected: 08 §5 defines 53 names and `UNKNOWN_USAGE_RECONCILE_S` is the documented 54th refinement.

## Requirement coverage

The first-pass coverage table (`F2-py-440d0c7.md`) still holds except where this pass changed it. Deltas:

| Test ID | Invariant demonstrated | Where |
|---|---|---|
| F-BASE | The 29 existing Python tests pass **unmodified**; `git diff fab9fbe` over the four baseline files and `gateway.py` is empty | commands 4–5 below |
| DUR-ADMIT | A request UUID is admitted once: a second admission (no key, or a different key) is 409 with the reserved total unchanged and one active job | `dur_admit__a_request_uuid_is_admitted_once` |
| DUR-CAP | A negative maximum hold is refused and leaves `reserved == 0`, `available == 0`, no job | `dur_cap__a_negative_maximum_hold_is_refused` |
| DUR-CAP | A job cannot store past its per-job journal reservation (429 + `Retry-After`), and its charge never exceeds `JOURNAL_JOB_RESERVE_BYTES` | `dur_cap__a_job_cannot_store_past_its_journal_reservation` |
| DUR-SETTLE | Reading the journal straight after settlement already shows exactly one terminal event carrying the committed settlement state; cancellation terminalizes through the same transaction | `dur_settle__the_terminal_event_belongs_to_the_settling_transaction` |
| DUR-OUTPUT | Queue time spent before a lost attempt still counts after the requeue: 9 s + 9 s against a 10 s budget expires the job with `queue_wait_expired` and no debit | `dur_output__queue_wait_does_not_restart_on_a_requeue` |
| MEDIA-SEC | A colliding handle from another tenant cannot replace a finalized upload; the owner cannot rewrite it either; restaging identical content is idempotent | `media_sec__staging_never_replaces_an_existing_object` |
| JUDGE-BUDGET | One reservation, one intent per run: a second `reserve` returns the stored run, does not double-reserve the budget and cannot revive an ambiguous run | `judge_budget__reserving_a_run_twice_does_not_reset_it` |
| JUDGE-BUDGET | Consent revoked between reservation and submission refuses with `consent_missing` before egress | `judge_budget__revoked_or_missing_consent_is_refused_before_egress` |
| F-CONTRACT | `cause`/`state` pairs must agree; price rates are never negative; ids and money reject trailing whitespace; nonsense numeric configuration (`nan`, `inf`, negatives, and a zero on bounds a zero disables) is refused at the boundary | `test_fixtures.py::test_cause_and_state_must_agree`, `::test_price_rates_are_never_negative`, `::test_public_identifier_shapes`, `test_money.py::test_trailing_whitespace_is_not_a_money_string`, `test_config_and_imports.py::test_nonsense_numbers_are_refused_at_the_boundary`, `::test_bounds_a_zero_would_disable_are_refused` |

Counts now: **78 conformance cases** across 8 suites (was 71), 37 fixtures unchanged, **353 contract tests** (was 322), 29 preserved baseline tests, **382 total** (was 351).

## Environment

| Field | Value |
|---|---|
| Classification | **local**, no cloud, no GPU, no paid provider, no network in any test |
| OS / kernel | Linux 7.0.0-1010-aws x86_64 |
| Python | CPython 3.12.3 (`.python-version` pins `3.12`, `requires-python == "3.12.*"`) |
| Package manager | uv 0.11.8, committed `uv.lock` unchanged in this pass (`uv lock --check` consistent), installed with `uv sync --frozen --all-extras` into `apps/infrx-api/.venv` |
| Core versions resolved | fastapi 0.141.1, pydantic 2.13.5, httpx 0.28.1, uvicorn 0.53.0, pytest 8.4.2 |
| Extras resolved | `state` psycopg 3.2.x + pool, `scheduling` valkey 6.1.1, `traces` clickhouse-connect + boto3, `judge` anthropic (installed, never imported by the contracts) |
| Environment variable names read by the new code | `INFRX_MODE`, `DATABASE_URL` and the remaining 08 §5 names (values never logged) |
| Services started | none. No container, no PostgreSQL, Valkey, ClickHouse, S3 or Supabase call |

## Commands

All from `apps/infrx-api` unless stated, UTC 2026-09-20.

| # | Command | Exit | Time (UTC) | Result |
|---|---|---|---|---|
| 1 | `uv sync --frozen --all-extras` | 0 | 20:21 | 41 packages checked, `.venv` from the committed lock |
| 2 | `uv run --frozen pytest -q` | 0 | 20:21 | **382 passed**, 2 warnings, 1.78 s (29 baseline + 353 contracts) |
| 3 | `make api-test` (repo root) | 0 | 20:22 | 382 passed |
| 4 | `git diff --stat fab9fbe -- apps/infrx-api/tests/test_gateway_auth.py apps/infrx-api/tests/test_inflight.py apps/infrx-api/tests/test_media.py apps/infrx-api/tests/test_app_factory.py apps/infrx-api/gateway.py` (repo root) | 0 | 20:22 | **empty** — baseline tests and `gateway.py` byte-identical |
| 5 | `uv run --frozen pytest -q tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/test_app_factory.py` | 0 | 20:19 | 29 passed |
| 6 | `uv run --frozen pytest -q tests/contracts` | 0 | 20:24 | 353 passed, 0.62 s |
| 7 | `uv run --frozen pytest -q tests/contracts/test_conformance.py` | 0 | 20:24 | 96 passed (78 cases + 8 runners + 8 protocol shape checks + 2 registry guards) |
| 8 | `uv lock --check` | 0 | 20:22 | lockfile consistent with `pyproject.toml`; no dependency changed in this pass |
| 9 | `uv run --frozen python -c "import infrx.contracts; from infrx.contracts import conformance, fakes, wire, tasklocal, fixtures; …"` | 0 | 20:22 | printed `no optional dependency loaded` — no psycopg/valkey/clickhouse-connect/boto3/anthropic in `sys.modules` with all extras installed |
| 10 | pre-fix regression proof: `git archive HEAD apps/infrx-api` into the session scratch directory, the two new conformance modules copied over the **old** fakes, then `uv run --frozen pytest -q tests/contracts/test_conformance.py -k "<the eight cases>"` | 1 | 20:15 | **8 failed, 88 deselected** — every new/extended case fails against `8401e59` and passes at `cdf6b87`. The scratch copy is outside the repository and is not committed |
| 11 | `make bench-test` (repo root) | 0 | 20:22 | printed `bench-test: not run - models/marlin2b/tests does not exist yet (E1 owns it)` — **not run**, not a pass |
| 12 | `make console-test` (repo root) | 1 | 20:22 | **not run**: `apps/app/node_modules` absent and `apps/app/**` belongs to the parallel console agent, so no `pnpm install` was performed. `make console-lint` was not run for the same reason |

Seeds: money property sweeps use `random.Random(20260920)` / `random.Random(20260921)`; every other case is deterministic through `FakeClock` and `SequentialIds`. No test sleeps, opens a socket or reads the wall clock. The `--import-mode=importlib` collision drill (first pass, commands 11–12 of `F2-py-440d0c7.md`) was **not repeated** in this pass; the setting is unchanged in `pyproject.toml`.

## Results

- 382 tests pass, 0 failed, 0 skipped. (`test_the_extras_are_installed_so_the_check_is_meaningful` would skip without `--all-extras`; with them it passes.)
- Baseline preserved exactly: 29 tests, four files plus `gateway.py` unmodified (command 4).
- Contract suite 353: fixture/vocabulary/envelope cases, money cases, configuration/import cases and 96 conformance tests.
- Deliberate non-results: console tests and lint (command 12) and the benchmark suite (command 11) did not run; they are **pending**, not passing.

## Failure drill

The first-pass drill table stands. This pass adds the injections the findings named, each now a conformance case:

| Injection point | Durable state before | Durable state after | Duplicate/retry behaviour | Cleanup |
|---|---|---|---|---|
| Same `NormalizedRequest` admitted twice (no idempotency key, then a different key) | one job, one hold, reserved = hold | unchanged | both retries raise a 409 `state_conflict`; reserved total identical, exactly one active job, no orphaned hold | none needed; retries must carry the idempotency key |
| `admit` with a negative maximum hold against a zero-credit wallet | wallet 0/0 | unchanged | `invalid_request`; `reserved` stays 0 and `available` stays 0, so no later admission can settle against fabricated credit | — |
| Job settles while the global journal budget is fully committed | `running`, 16 MiB reserved, chunks stored | terminal + settled **with** its terminal event in the journal | the settling transaction wrote the event; a later `finalize_in_transaction` returns the same chunk instead of attempting a write that could fail | reservation released after the write; stored bytes keep counting until pruned |
| One job appending past its 16 MiB reservation | 4 KiB reservation (tightened limits), ~3.6 KiB stored | unchanged | `journal_capacity_exhausted` 429 with `Retry-After`; nothing truncated, the job's charge never exceeds its reservation | — |
| Lease lost after 9 s of a 10 s queue budget, requeued, then 9 s more | `running`, unpublished | `queued`, then `expired` / `queue_wait_expired`, debit 0 | the retry does not hand the job a fresh queue budget | reservations and hold released once |
| `reserve` called again for an ambiguous run | `ambiguous`, reservation held, one intent | unchanged | the stored run is returned; `begin_submit` still raises `AmbiguousSubmission`; the budget is reserved once, not twice | operator reconciliation still required (amendment request 5) |
| Consent revoked between `reserve` and `begin_submit` | `reserved`, intent absent | unchanged | `consent_missing` before any egress; no submission intent is minted | — |
| Another tenant stages a handle that collides with a finalized upload | org A owns the finalized ref | org A's ref unchanged, org B gets its own tenant-scoped object | the owner's `resolve_owned` still returns the original; the owner restaging different content under that handle is a conflict | — |

## Artifacts

- Source of truth: this repository at `cdf6b87` on `codex/f2-contracts-py`.
- Fixtures unchanged: `apps/infrx-api/infrx/contracts/fixtures/v1/*.json` (37 files, canonical JSON, no credentials, no signed URLs, no customer content).
- The reproduction script and the pre-fix `git archive` copy used for command 10 live in the session scratch directory only and are not committed. Test output was read from the terminal and not retained as files; commands 1–9 reproduce from a clean checkout with no secrets.

## Changes

Owned paths modified in this pass:

- `apps/infrx-api/infrx/contracts/fakes/state.py` — request-uniqueness and negative-hold guards in `admit`; per-job journal limit with a never-refused `settling` write; terminal event written by `_terminalize`; cumulative queue wait; empty `append` no longer publishes; `FakeStreamStore.write_terminal` and the journal registration.
- `apps/infrx-api/infrx/contracts/fakes/judge.py` — idempotent `reserve`, consent recheck in `begin_submit`.
- `apps/infrx-api/infrx/contracts/fakes/media.py` — `(org_id, handle)` object keys, immutable staging.
- `apps/infrx-api/infrx/contracts/fakes/factories.py` — one journal per store in `_job_hooks`.
- `apps/infrx-api/infrx/contracts/{records,money,ids,ports,wire}.py` — `CAUSE_STATES` + `states_for_cause`, non-negative price rates, `fullmatch` patterns, `caps` typing and `admit` contract notes, header constants.
- `apps/infrx-api/infrx/config.py` — money coercion through `money.parse`, finite/nonnegative numbers, `MUST_BE_POSITIVE` in `validate_pilot`. F1's `Settings`/`from_env` untouched.
- `apps/infrx-api/infrx/contracts/conformance/{jobs,services}.py` — 7 new cases, 1 extended case.
- `apps/infrx-api/tests/contracts/{test_fixtures,test_money,test_config_and_imports}.py` — regression tests for the record, money, id and configuration fixes.
- `apps/infrx-api/infrx/contracts/README.md` — 12 new refinement rows and amendment requests 5–7.
- `research/plan/evidence/f/F2-py-cdf6b87.md` — this report (and a forward pointer appended to the first-pass report's verification log).

Not touched: `apps/app/**`, `pyproject.toml`, `uv.lock`, `.python-version`, the root `Makefile` and `.gitignore` (unchanged this pass), `research/plan/tasks.json`, the coordinator session record, `CLAUDE.md`, `HANDOFF.md`, `gateway.py`, `infrx/{auth,media,gateway,usage}`, any other track's files.

Contract change requests (full text in `infrx/contracts/README.md`): requests 1–4 from the first pass stand (`PREPARATION_CONCURRENCY` as a reservation not an admission gate, the `FETCH_TIMEOUT_S`/`MAX_MEDIA_BYTES` name clashes with F1, the feedback `rating` range, deadline decomposition). New in this pass:

5. **An ambiguous judge run has no resolution operation.** Its reservation is held for ever because `record_submission` and `settle` both refuse it (correctly — 02 forbids a second billable batch). Requesting one operator-only operation that adopts discovered provider evidence or releases the reservation with an audit record. Owner: coordinator + J/D.
6. **`recover(now)` takes a caller-supplied time.** 01's signature is kept, and the fake honours it so cases can drive the clock, but D must read the database clock inside the transaction and treat the argument as a bound at most; otherwise a `now` two days ahead would release an unknown-usage hold before its 24 h window. Owner: D.
7. **Feedback body validation.** `accept` takes `{}` (no rating, no correction) and allows `idem.key=None`, while 01 says `POST /v1/feedback` uses an idempotency key. Tightening either is a contract revision plus a suite change. Owner: coordinator + D/G.

Migration / deploy / rollback implications: unchanged — no migration, no schema, no deployment change; the only runtime addition is `config.pilot_from_env`/`validate_pilot`, which nothing calls yet. Rollback is `git revert` of `b8a39f3` and `cdf6b87` (which restores the eight defects, so a revert should be paired with a contract decision). The 06 keys were kept in mind throughout: no record field was renamed in this pass, and `CAUSE_STATES` constrains values D will persist in `jobs.outcome`/`usage_events.outcome`, not their column names.

## Limits

- **Fakes only.** Every conformance pass here is against in-memory adapters: **implemented**, never integrated or live verified. D (PostgreSQL), Q (Valkey and memory), W (engine), T (spool/ClickHouse) and M (object store) must each run the same suite against the real service.
- **The terminal-event fix is a spec statement, not a proof about PostgreSQL.** The fake writes the event under the same lock as the settlement; D must put it in the same SQL transaction, and DUR-OUTPUT against real PostgreSQL is what proves it. Owner: D.
- **Re-admitting a request is a 409 by decision, not by 01.** 01 and 02 specify only the idempotency-key retry path; the conflict is F2's encoding of 06's job key and of "no double reservation". If the coordinator wants an internal retry without a key to replay instead, that is a contract revision affecting D and G.
- **Cumulative queue wait is F2's reading of 01.** A plain non-reset would have expired a job instantly after a 120 s lease loss, so the budget tracks accumulated queued time. If the coordinator wants a per-attempt budget, say so before D1.
- **Console half pending.** `pnpm test` / `pnpm lint` did not run (no `node_modules`, `apps/app/**` is the parallel agent's path).
- **Benchmark target not run.** `models/marlin2b/tests` does not exist yet (E1); `make bench-test` reports "not run" by design.
- **Import-mode collision drill not repeated** in this pass (first pass proved it; the setting is unchanged).
- **`anyio`'s pytest plugin is auto-registered** transitively through httpx/starlette. Nothing uses it — async cases run through `asyncio.run` — but "no pytest plugins" is true of this repository's configuration, not of the installed environment.
- **Single-lock concurrency in the fake** (unchanged, `ponytail:` comment in the source): `FakeJobStore` serializes with one `asyncio.Lock` instead of the documented lock order, so the concurrency cases prove the invariant, not D's lock ordering.
- **Optional harness hooks skip cases.** A factory without `failures`, `publish`, `revoke_key`/`unrevoke_key`/`suspend_org`, `journal_bytes` or `available` makes the matching cases return early. The fakes provide all of them, so nothing was skipped here; an adapter's evidence must say which hooks it supplied.
- **Not covered by design:** SSRF/DNS rebinding, real ClickHouse DDL and dedup, RLS and column grants, callback delivery, console flows, any latency or throughput number. No performance claim is made.

## Handback

- **Next unblocked task:** the coordinator integrates this branch and the console half of F2 into `claude/infrx-impl`, reruns `make api-test` on the merged SHA, then gate **G0 contract-ready** still needs the console half's proof of recursive test discovery. After G0, D1 is the critical path; M, Q, W, G, T, J, C, U, V can start against `infrx.contracts.fakes`.
- **Pending coordinator wiring:** rule on contract change requests 1–7, in particular (a) `PREPARATION_CONCURRENCY`, (b) the `FETCH_TIMEOUT_S`/`MAX_MEDIA_BYTES` clashes, (c) the ambiguous-run resolution operation before J starts, (d) whether re-admitting a request UUID is a 409 or a replay, (e) whether queue wait is cumulative, and (f) whether `08-contracts-v1-encoding.md` §2's module list should record `wire.py` and `tasklocal.py`.
- **Unresolved findings:** none failing. All eight blocking findings are fixed with regression coverage; four non-blocking notes were converted into amendment requests rather than silently encoded.
- **For the next implementation session:** read `apps/infrx-api/infrx/contracts/README.md` first (the refinements table now carries every decision this pass made), use `tasklocal.local_services("<task>")` for any container or port, and run your port's `run_<port>_conformance(factory)` against the real adapter before claiming integrated. Do not edit `pyproject.toml`, `uv.lock`, the contracts directory or the Makefile; request the change from the coordinator.

## Verification log

- 2026-09-20: Review-fix pass on `codex/f2-contracts-py` from base `fab9fbe`, implementation `cdf6b87`. Eight blocking findings reproduced against `8401e59`, fixed at root cause, and covered by 7 new plus 1 extended conformance case; all eight were proved failing against the pre-fix code in a scratch copy (command 10) before being proved passing here. 382 tests pass, 29 baseline tests preserved byte-identical, `uv.lock` unchanged and consistent, no optional dependency imported by the contracts. Console tests/lint and the benchmark suite are pending, not passing; every conformance pass is against fakes, so the task is implemented and not integrated. No cloud, GPU, container, paid provider or production resource was touched.
- 2026-09-20: Superseded by `F2-py-11607aa.md` (contracts v1 revision r1). Two claims of this report are corrected there: the row "Consent revoked between reserve and begin_submit ... refuses" overstated what was proven (the case reserved with an already-revoked snapshot, which production never produces; R9 now requires the current consent record), and amendment requests 1, 2, 3, 5, 6 and 7 were answered by the coordinator's r1 rulings and are implemented. History kept; nothing above was rewritten.
