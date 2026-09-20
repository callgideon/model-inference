# Contracts v1, executable (F2)

Coordinator-owned. This directory is the single Python spelling of
[contracts v1](../../../../research/plan/01-contracts.md), the
[durable protocols](../../../../research/plan/02-durable-protocols.md) and the
[encoding refinement](../../../../research/plan/08-contracts-v1-encoding.md).
A change here is a contract revision: update the fixtures, the conformance suites
and every consumer in the same review.

Passing a conformance suite against the fakes means **implemented**. Only the same
suite against the real service means integrated.

| Module | What it owns |
|---|---|
| `ids.py` | UUIDv4 request identity, opaque `job_`/`upl_`/`fb_` handles, `chatcmpl-` ids |
| `money.py` | `Decimal`-only USD, one half-up debit, ceiling reservations, the JSON codec |
| `errors.py` | `DomainError` hierarchy, code → HTTP status/type table, fixed safe messages, `ErrorEnvelope` |
| `records.py` | The contracts-v1 records and the frozen vocabulary of 08 §3 |
| `wire.py` | Public HTTP/SSE bodies (chat, jobs, uploads, feedback, trace export) |
| `ports.py` | One `typing.Protocol` per ports-table row, same operation names |
| `limits.py` | The 08 §5 names and frozen defaults as pure data (`PilotSettings`) |
| `tasklocal.py` | Task-local container names, host ports, databases and object prefixes (08 §8) |
| `codec.py` | The one canonical JSON form: sorted keys, 2-space indent, no nulls |
| `fixtures/v1/` | 37 serialized fixtures, byte-stable through their models |
| `fakes/` | In-memory adapters with the real durable semantics, plus clock/ids/failure injection |
| `conformance/` | Importable suites `run_<port>_conformance(factory)` |

## Encoding decisions

1. **Canonical JSON.** `codec.canonical_bytes` is the on-disk and on-wire form:
   sorted keys, two-space indent, trailing newline, `None` omitted (an absent field
   reads back as its `None` default). `codec.compact_bytes` is the same content
   without pretty printing, for SSE `data:` fields and database payloads.
   "Round-trips byte-stably" means `file → model → file` is the identity, which
   `tests/contracts/test_fixtures.py` asserts for every fixture.
2. **Records are closed.** Frozen pydantic v2, `extra="forbid"`, `schema_version:
   int = 1`. Adding a field is a contract revision, not a producer's local choice.
3. **Money never touches `float`.** `money.parse` is a trust boundary: it accepts
   plain decimal strings (and `int`/`Decimal` in process) and rejects exponents,
   `NaN`, `+`, leading zeros, negative zero, anything finer than 1e-8 and anything
   past `numeric(20, 8)`. A value finer than the scale is **rejected**, never
   silently rounded. `format_money` always emits eight fractional digits.
4. **Datetimes are UTC-aware** and serialize as RFC 3339 with `Z`. A naive
   datetime is a validation error, because the database clock is the only clock.
5. **Unknown usage is `usage=None`.** A `TerminalOutcome` with a usage present must
   be `authoritative`; unknown usage is the absence of usage plus
   `settlement_state=held_unknown` and a `reconcile_after`. Output chunks are never
   a token estimator.
6. **Error detail never ships.** The envelope `message` comes from
   `errors.MESSAGES` only; `DomainError.detail` is for operators and logs.
   `errors.http_status` **raises** for internal codes (`stale_lease`,
   `already_terminal`, …) so an unhandled internal error cannot become an accidental
   500. Routes translate deliberately. `429`/`503` envelopes must carry
   `retry_after_s`; building one without it raises.
7. **Ownership failures are `NotFound`.** Every `*_owned` operation takes the
   caller's org and answers 404 for another tenant's row, with no distinction
   between "absent" and "not yours".

### Refinements (decided once, here)

| Refinement | Why |
|---|---|
| `wire.py` added beside `records.py` | 08 §2 keeps `records.py` to the contracts-v1 table, but the required fixtures (chat success, SSE transcript, job/upload/feedback/trace bodies) need models. Public bodies live in `wire.py`. |
| Extra value types: `IdempotencyRef`, `OutboxEvent`, `PreparedRequest`, `EngineEvent`, plus the `MediaKind` and `ContentState` enums | The ports need argument and event types; `ContentState` is the console's closed set from 08 §9. |
| `Chunk.event_type` / `EngineEvent.type` | `type` shadows nothing inside a record but reads badly on a persisted row, so the journal row spells it `event_type`. |
| `complete()` recomputes settlement | The caller proposes `cause`, `usage` and `result_ref`; the store decides `settlement_state` and `debit`. Settlement authority never leaves D. |
| `released_free` vs `released_platform_absorbed` | `released_free` = the customer was never going to be charged (`invalid_media`, `preparation_failed`, `queue_wait_expired`, a zero-cost success). `released_platform_absorbed` = we did work and ate the cost (`engine_error`, `lost_after_publication`, `journal_write_failed`, `retries_exhausted`, `platform_error`, and an aged unknown-usage hold). |
| Unknown usage is free when nothing was published | A billable cause with no authoritative usage becomes `held_unknown` only if the publication marker was set. With no output ever committed there is nothing to reconcile, so the hold is released instead of freezing a customer's credit for 24h. |
| `UNKNOWN_USAGE_RECONCILE_S` (86400) | 02 requires "after 24h, once execution is fenced and the job is terminal"; the window needed a name. |
| `journal_write_failed` as an internal code | 02's "unforeseen write failure" needs a raisable error; `TerminalCause` already had the matching cause. |
| `MAX_READ_LIMIT = 1000` | `read_owned` needs a hard page bound; requests above it are clamped, `limit <= 0` is `invalid_request`. |
| A request UUID is admitted **once** | 06 keys jobs by the request UUID and allows "no second active hold per request". A second `admit` of the same request is a `state_conflict` (409), not a replay: without an idempotency key there is no payload to compare, and admitting it again would mint a hold nothing releases. The supported retry after an ambiguous acknowledgment is the idempotency key. |
| A negative `hold` is `invalid_request` | `money.parse` allows negatives because ledger deltas need them; a *reservation* never does, and a negative one would lower `reserved_total` and fabricate available credit. |
| `caps` names extra reservation *kinds* | Amounts always come from the store's limits. A caller cannot ask for a smaller journal reservation or a cheaper capacity slot. |
| The terminal journal event is written by the settling transaction | 02 §7 lists it inside the one transaction, so `complete`/`cancel`/`recover` write it where the money moves. `StreamStore.finalize_in_transaction` reads it back (and writes it only if some adapter settles without a journal), stays idempotent, and no longer fails a settled job for want of journal budget. |
| Per-job journal limit = `JOURNAL_JOB_RESERVE_BYTES` | 02 requires per-job *and* global limits. Appends past the job's 16 MiB reservation are `journal_capacity_exhausted`; the terminal event is charged to the job but never refused, because settlement has already committed. |
| Queue wait is cumulative | 01: the budget "starts at durable queued transition; no extension through retries", so a prepublication requeue keeps the queue time already spent instead of restarting the budget. |
| `JudgeCoordinator.reserve` is idempotent per `run_id` | 01: "duplicate calls produce one run/intent". A second reserve returns the stored run untouched; it never resets state, mints a second submit intent or reserves twice. Changing a reservation means a new run. |
| `begin_submit` rechecks consent | 02: consent "must be current at submission", not merely at reservation, so a snapshot revoked in between refuses with `consent_missing` before any egress. |
| Media objects are keyed by `(org_id, handle)` | Finalized content is immutable and tenant scoped: one org's handle can never replace, shadow or reach another org's object, and restaging the same handle with different content is a conflict rather than an overwrite. |
| `TerminalOutcome` validates `cause` against `state` (`records.CAUSE_STATES`) | The pair is one fact. `succeeded` + `engine_error` would be a free success and `failed` + `completed` would lose a settled debit, so any cause not listed may only carry `failed`. |
| Header names are constants in `wire.py` | 08 §3 fixes the header vocabulary; G, W and the console read `HEADER_*`/`PREFER_RESPOND_ASYNC` instead of hand-typing strings. |
| `PREPARATION_CONCURRENCY` is not an admission gate | See the amendment request below. |
| No Python `ConsoleServices` protocol | That ports-table row is TypeScript (08 §9), owned by the console half of F2. |

## Running a conformance suite against a real adapter

```python
from infrx.contracts.conformance import Harness, run_jobstore_conformance

def factory(limits=None, **kw):
    store = MyPostgresJobStore(dsn=..., clock=db_clock, ids=ids, limits=limits or DEFAULTS)
    return Harness(port=store, clock=db_clock, ids=ids, failures=my_failure_plan,
                   extra={"grant": ..., "balance": ..., "active_jobs": ...,
                          "outbox": ..., "outbox_kinds": ..., "publish": ...})

run_jobstore_conformance(factory)      # raises AssertionError on the first failure
```

Rules for a factory:

- it returns a **fresh, empty** adapter on every call, and honours the `limits`
  keyword (cases tighten capacity and TTLs to make bounds observable);
- `clock` must be the same clock the adapter reads (for PostgreSQL, a clock the
  test can move, not `now()`), with `now()` and `advance(seconds)`;
- `ids` supplies `uuid()`/`event_id()`;
- `failures` is optional. Without it the crash-after-commit cases return early
  instead of failing, so a suite can be adopted in steps;
- optional hooks (`publish`, `revoke_key`, `unrevoke_key`, `suspend_org`,
  `journal_bytes`) behave the same way: absent means the case is skipped, and the
  evidence report must say so rather than claim a pass.

`infrx/contracts/fakes/factories.py` is the reference implementation of all eight
factories; `tests/contracts/test_conformance.py` runs every case against them.
Case names carry their oracle, so `pytest -k dur_settle` (or `-k trace_bounds`,
`-k api_stream`, …) selects one oracle's cases.

| Suite | Oracles covered | Owner of the real adapter |
|---|---|---|
| `jobstore` | DUR-ADMIT, DUR-CAP, DUR-FENCE, DUR-OUTPUT, DUR-SETTLE, DUR-OUTBOX | D |
| `streamstore` | DUR-OUTPUT, DUR-CAP, DUR-FENCE, API-STREAM | D |
| `mediastore` | MEDIA-SEC, MEDIA-PARITY | M |
| `scheduler` | DUR-OUTBOX | Q |
| `engine` | API-STREAM | W |
| `tracesink` | TRACE-BOUNDS (feeds TRACE-RECOVER) | T |
| `feedback` | FEEDBACK-ACK | D, adapted by C/G |
| `judge` | JUDGE-BUDGET | D/J |

## Task-local services (08 §8)

`tasklocal.local_services("d1")` returns the only names and ports a task may use.
Never point a test at Supabase production, AWS, or a container another session
created.

| Track | Service | Host port | Container | Database / prefix |
|---|---|---|---|---|
| D | PostgreSQL | 55432 | `infrx-<task>-postgres` | `infrx_<task>` |
| Q | Valkey | 56379 | `infrx-<task>-valkey` | `infrx_<task>` |
| T | ClickHouse | 58123 (HTTP), 59000 (native) | `infrx-<task>-clickhouse` | `infrx_<task>` |
| T | S3-compatible | 59110 | `infrx-<task>-s3` | `test/<task>/` |
| M | S3-compatible | 59100 | `infrx-<task>-s3` | `test/<task>/` |
| E | compose | 55500–55599 | `infrx-<task>-compose` | `infrx_<task>` |
| F, G, W, J, C, U, V, I | — | — | fakes until integration | — |

Temporary files belong in the session scratch area. D alone adds SQL migrations,
numbered after `0002`.

## Amendment requests to the coordinator

1. **`PREPARATION_CONCURRENCY` (2) versus 64 accepted nonterminal jobs.** 01 says
   admission reserves preparation capacity, and the limits table says two active
   preparation processes per host. Enforcing the concurrency figure at admission
   would make the third simultaneous request a 429 while 62 job slots sit idle, so
   the fake reserves a preparation *reservation row* at admission and treats the
   concurrency figure as M/W's execution-side limit. If the coordinator wants a
   hard admission gate instead, D and this suite both change.
2. **`FETCH_TIMEOUT_S` means two things.** F1's gateway reads it with a default of
   30s for inline media fetches; 08 §5 gives it a default of 20s for the pilot
   path. Both currently read the same variable with different defaults.
   Recommendation: rename the pilot one (`MEDIA_FETCH_TIMEOUT_S`) or retire F1's
   when G takes over the fetch path. Likewise `MAX_MEDIA_BYTES` (08) and
   `MAX_VIDEO_MB` (F1) express the same limit in different units.
3. **Feedback `rating` range.** Nothing specifies it; encoded as `-1 | 0 | 1`
   (thumbs). Widening it later is a contract revision plus a D migration.
4. **`GENERATION_TIMEOUT_S` and the absolute deadline.** The records carry
   `deadline_at` only; the split between preparation, queue and generation budgets
   is left to G and W. If the deadline must be decomposed on the record, say so
   before D1 persists it.
5. **An ambiguous judge run has no resolution operation.** The ports table ends at
   `quarantine`, so an `ambiguous` run keeps its budget reservation for ever:
   `record_submission` and `settle` both refuse it, which is correct (02 forbids a
   second billable batch) but leaves no way to record provider evidence. Requesting
   one operator-only operation, e.g. `resolve(run, evidence)`, that either adopts a
   discovered provider batch or releases the reservation with an audit record.
6. **`recover(now)` takes a caller-supplied time.** 01's signature is
   `recover(now)`, and the fake honours it so cases can drive the clock. In
   PostgreSQL, D must read the database clock inside the transaction and treat the
   argument as a bound at most: a caller-supplied `now` two days ahead would
   otherwise release an unknown-usage hold before its 24h window.
7. **Feedback body validation.** `accept` currently takes any body without a rating
   or a correction (`{}` is accepted) and allows `idem.key=None`, while 01 says
   `POST /v1/feedback` uses an idempotency key. If an empty submission must be
   rejected and the key made mandatory, that is a contract revision for the wire
   model plus this suite.
