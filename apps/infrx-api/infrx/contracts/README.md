# Contracts v1, executable (F2)

Coordinator-owned. This directory is the single Python spelling of
[contracts v1](../../../../research/plan/01-contracts.md), the
[durable protocols](../../../../research/plan/02-durable-protocols.md) and the
[encoding refinement](../../../../research/plan/08-contracts-v1-encoding.md).
A change here is a contract revision: update the fixtures, the conformance suites
and every consumer in the same review.

**Revision r1** (08 §10, rulings R1–R15) is implemented here: read that section
before changing anything below, because most of the refinement rows now quote it.

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
| `fixtures/v1/` | 38 serialized fixtures, byte-stable through their models; `money_cases.json` and `error_codes.json` are the cross-language parity tables |
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
| `journal_write_failed` as an internal code | 08 §3 lists it as a `TerminalCause`, and it is one, but `StreamStore.append` also has to *raise* something when an event exceeds `JOURNAL_EVENT_MAX_BYTES`: 02 forbids silently truncating a successful result, and a retry cannot help, so `429 journal_capacity_exhausted` would be a lie. `errors.JournalWriteFailed` is therefore an **internal** code with no HTTP status (`errors.http_status` raises for it): W stops relaying and the job settles with the matching `TerminalCause`. It appears in `error_codes.json` only under `internal_only`, never in the HTTP table. |
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
| No Python `ConsoleServices` protocol | That ports-table row is TypeScript (08 §9), owned by the console half of F2. |

### Revision r1 (08 §10) as implemented

| Ruling | Where it lives |
|---|---|
| R1 preparation capacity | `MAX_PREPARING_JOBS` (8) in `limits.py`; `admit` counts jobs holding an active `preparation` reservation and answers `429 capacity_exhausted`. `PREPARATION_CONCURRENCY` (2) stays M/W's host pool size and gates nothing at admission. `prepared()` releases the unit. |
| R2 media fetch names | `MEDIA_FETCH_TIMEOUT_S` (20), `MEDIA_FETCH_CONNECT_TIMEOUT_S` (3), `MEDIA_FETCH_MAX_REDIRECTS` (3), `MAX_MEDIA_BYTES`. F1's `FETCH_TIMEOUT_S` (30), `MAX_VIDEO_MB` and `MAX_REDIRECTS` keep their names, defaults and reader in `config.Settings`; no pilot name overlaps an F1 one (`test_the_f1_fetch_names_are_not_pilot_names`). |
| R3 feedback body | `FeedbackName` (`thumb`/`rating`/`correction`/`comment`) plus `value` and optional `comment` on `records.Feedback` and `wire.FeedbackSubmission`. The name fixes the value's type: boolean, integer 1–5, or nonempty text. A JSON float is refused *before* pydantic's lax mode can round it into an integer. An empty body and a missing `idem.key` are both `400 invalid_request`. `rating: -1|0|1` is gone. |
| R4 budgets snapshot | `records.Budgets` (`preparation_s`, `queue_wait_s`, `generation_s`, `first_token_s`, `stall_s`), captured by `admit` with `Budgets.of(limits, execution_mode)` and carried on `Admission`, which the store reads instead of its current settings. `Admission.admitted_at` **is** R4's `accepted_at`; the record was not renamed because `06` and the console already use `admitted_at`. |
| R5 cumulative queue wait | `_queue_wait` accumulates across requeues and is compared with the admission's own `budgets.queue_wait_s`. |
| R6 re-admitting a request | Unchanged from the previous pass: `409 state_conflict`, no side effects. |
| R7 no caller time | `JobStore.recover()` takes no argument; `StreamStore.expire(now)` keeps 01's signature but clamps to `min(now, clock.now())`, so an argument is a bound at most. |
| R8 ambiguous resolution | `JudgeCoordinator.resolve_ambiguous(run_id, operator, resolution, reason, *, external_id=None)` with `records.JudgeResolution`. `adopt_provider_evidence` needs the provider id (an extra keyword, because 01's four positional arguments carry nowhere to put it) and continues to `collecting`; `release_reservation` is terminal `quarantined` with the reservation freed. Both append an audit record and neither touches `submit_intent`. |
| R9 current consent | The fake holds a per-org current consent record (`set_consent`/`revoke_consent` hooks) standing for `consent_history`. `begin_submit` reads *that*, not the snapshot the run was reserved with; a revocation releases the reservation, moves the run to `cancelled` and refuses egress. A frozen snapshot can never be the whole check, because the row a customer revokes is the live one. |
| R10 tenant coherence | `admit` requires `idem.org_id == request.org_id` and the request's own media; `complete` requires `outcome.job_id == lease.job_id`; `reserve` requires `consent.org_id == run.org_id`; `accept` already required the caller's idempotency scope. Admission rechecks revocation, suspension **and** entitlement through `FakeJobStore.is_entitled`, an injectable callable a real adapter replaces with its query (`unentitle`/`entitle` hooks). |
| R11 monetary inputs | `fakes/support.money_input` is the shared boundary: `money.parse` (no floats, exponents, `NaN`, negative zero, over-scale) and then a refusal of negatives, raised as `invalid_request` rather than a `ValueError`. Used by `admit`'s hold, `grant`, `reserve` and `settle`. The domain is exactly `numeric(20, 8)`: `money.MAX_VALUE = 10^12`. `fixtures/v1/money_cases.json["parse"]` is the accept/reject set both languages must match. |
| R12 mode versus content | `TraceEnvelope` refuses content in any mode but `full`, and the sink drops a smuggled one as `malformed` and charges every content byte it accepts. |
| R14 module list | No code change: `wire.py` and `tasklocal.py` are in the table above, and `UNKNOWN_USAGE_RECONCILE_S` is a §5 name rather than a refinement. |

### Further refinements from the r1 pass

| Refinement | Why |
|---|---|
| A refused admission reserves nothing | The journal reservation was taken before the price snapshot was resolved, so a client retrying an unpriced model drained the global journal budget. Everything that can refuse now runs first. |
| Settlement is validated before money moves | `_terminalize` checks the `(cause, state)` pair and the usage certainty up front. Building the record last would debit the wallet and *then* raise, leaving money moved on a job that never became terminal. |
| `completed` without authoritative usage is not a success | With nothing published there is no usage to reconcile, so the honest outcome is `engine_incomplete`/`failed` with no result reference, not a delivered success at zero cost. |
| The winning worker can replay a rewritten settlement | `complete` compares the caller's proposal with the stored proposal as well as the committed outcome, so a crash-and-retry after an over-envelope rewrite returns the committed outcome instead of `AlreadyTerminal`. |
| The queue-wait budget gates `claim` | A job the customer has already been told to give up on must not start running because a worker reached it before the reconciler. |
| Staging is all or nothing | Every media reference is validated before any is stored, so a refused request leaves nothing staged (02: "a staging failure creates no job or hold"). |
| `finalize_upload` cannot replace an object | Immutability held across tenants and for `stage`, but completing an upload over a handle the same tenant had already staged replaced its content. |
| Index visibility is measured from the claim | `FakeScheduler` timed a candidate out from `available_at`, so an event older than the lease TTL was handed to two workers at once: pure throughput loss, and a misleading example for Q. |
| Terminal judge runs are sticky, provider ids are required | `quarantine` refused to reopen `settled`/`cancelled`/`quarantined`; `record_submission` refuses an empty id, which is the ambiguity it exists to remove; `reserve` resets a caller-supplied `submit_intent` or `external_batch_id`. |
| Port boundaries raise domain errors, never `ValueError` | `create_upload` (non-integer or out-of-range `max_bytes`, empty mime allow-list), `accept` (a non-object body) and every monetary input answer `400`, so a route cannot turn a caller's input into a 500. |

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
- optional hooks behave the same way: absent means the case returns early, and the
  evidence report must say so rather than claim a pass. JobStore: `publish`,
  `revoke_key`, `unrevoke_key`, `suspend_org`, `unentitle`, `entitle`, `retune`,
  `journal_bytes`. JudgeCoordinator: `available`, `runs`, `set_consent`,
  `revoke_consent`, `audit`. `retune` reconfigures the live adapter, so a case can
  prove an accepted job keeps its own budgets;
- the `streamstore`, `scheduler` and `feedback` factories also publish
  `extra["jobs"]`, because those cases must admit a job first. The suites only call
  *port* operations on it and never read a fake's attributes, so a real adapter can
  pass its own JobStore there.

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

Requests 1, 2, 3, 5, 6 and 7 of the previous pass were answered by revision r1
(R1, R2, R3, R8, R7 and R3 respectively) and are implemented above. Still open:

1. **`GENERATION_TIMEOUT_S` and the absolute deadline** (was request 4, partly
   answered). R4 puts the budgets on the accepted job, so `budgets.generation_s`,
   `first_token_s` and `stall_s` now exist as data. What 01/02 still leave open is
   *who enforces* them: the records carry no per-phase deadline instants, so G and W
   must derive them from `admitted_at + budgets` themselves. If D should persist a
   `generation_deadline_at` instead, say so before D1.
2. **`deadline_exceeded` and `sync_deadline` are billable.** `BILLABLE_CAUSES`
   includes both, so a job that dies on the platform's own generation deadline with
   authoritative usage is charged. 02 says platform-caused failures are free but
   also that "known authoritative usage on customer cancellation may consume
   promotional credits", and a synchronous client timeout is the customer's
   deadline while the absolute generation deadline is ours. These two causes need a
   ruling; money is not a place to guess. Owner: coordinator + D.
3. **An expired upload window answers `result_expired`.** `finalize_upload` raises
   `410 result_expired` ("The result is no longer available.") for an upload whose
   window has closed, which is the wrong customer-facing message. The 08 §3 code
   table has no `upload_expired`, and adding a code is a coordinator revision that
   both halves and `error_codes.json` follow. Owner: coordinator.
4. **`resolve_ambiguous`'s provider id is a keyword argument.** R8 names four
   positional arguments and also requires the provider id for
   `adopt_provider_evidence`; the id arrives as `external_id=` because there is no
   fifth positional slot in the ruling. If the coordinator prefers `resolution` to
   be a small record carrying the evidence, that is a signature change for D and J.
