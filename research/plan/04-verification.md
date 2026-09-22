# Closed-loop verification and release gates

Every test ID below names a required **future** test; none is claimed implemented by this package. Module briefs map tasks to these IDs. Retain existing research test cases, but namespace them as `SERV-*`, `TRACE-*`, `JUDGE-*` or `CONSOLE-*` so repeated A1/Q1/I1 labels cannot collide. E2 publishes the explicit legacy-to-new mapping in its evidence.

**2026-09-21 amendment:** use [platform split](08-platform-split.md) and [manifest v3](tasks.json). Consumer, Lab operations and Lab observation are independently released. Implement new tests against actual services; this documentation has not executed them.

## Required suites

| ID | Injection / action | Observable passing condition | Owners |
|---|---|---|---|
| F-BASE | Extract monolith, replay existing tests | Same intended public behavior; 23 Python baseline cases preserved; console 4 tests/lint still pass | F |
| F-CONTRACT | All port adapters run shared fixtures | Matching envelopes, decimals, states, errors, cursors and schema versions | F + all |
| DUR-ADMIT | Kill after stage, before/after commit, before ack; retry idem | No acceptance without job/hold/outbox; same accepted identity; no double reservation | D/G |
| DUR-CAP | Concurrent admissions/grants across keys/orgs | No negative available balance or capacity oversubscription; stable lock ordering | D |
| DUR-FENCE | Expire lease, race stale/new workers at every mutation | Stale generation cannot append, renew, settle or consume second capacity | D/W/Q |
| DUR-OUTPUT | Kill before/after first committed chunk; lose terminal ack | Only prepublication retry; same durable replay; one terminal usage/settlement; no early success | D/W/G |
| DUR-SETTLE | Duplicate completion, cancel/complete race, unknown usage aged24h | One winner, exact decimals, no retrodebit, release only after fencing/terminal | D |
| DUR-OUTBOX | Lose Valkey data/ack, replay delivery, restart dispatcher | Rebuilt jobs, no missing accepted jobs, no duplicate executable attempt | D/Q |
| DUR-RLS | Member/browser/operator/service roles attack protected columns/RPC | Tenant/role enforcement in DB as well as route; migrations preserve existing balances | D/C |
| MEDIA-SEC | DNS rebinding, redirect-to-private, IPv6/mapped-IP, oversized/chunked body | Pinned validated destination on every hop; bounded bytes/time; no internal access | M/G |
| MEDIA-PARITY | Distinct clips, orientation/aspect extremes, corrupt media | Correct frame/token budget, pixel area <=200704, no upsize, measured answer parity | M/E |
| API-MODES | Sync/explicit async, queue expiry, disconnect and cursor replay | No surprise202; cancellation semantics correct; no orphan sync execution; explicit replay gaps | G |
| API-CALLBACK | Replay terminal outbox, fail receiver, rebind destination, rotate key | One inference/settlement; stable authenticated delivery ID; bounded retries/dead letter; no internal egress | G/D/M |
| API-STREAM | Split reasoning delimiters, upstream pre/post-header errors | Correct output across arbitrary chunk boundaries; honest terminal error; no inflight leak | G/W |
| TRACE-BOUNDS | Large concurrent traces; slow/full disk; queue count/bytes exhausted | Peak capture budgets enforced during accumulation; inference continues; accurate loss counters | T |
| TRACE-RECOVER | Crash before/after fsync; replay segments twice; CH unavailable | Only promised durable records recovered; logical rows/scores not duplicated; no copytruncate | T |
| TRACE-TENANT | Override query params, cross-org object ref, expired content | Tenant bound by trusted server operation; logical expiry; no arbitrary query/ref access | C/T |
| FEEDBACK-ACK | Submit before trace projection; crash after PG commit; spoof author | Feedback survives/replays once; ownership correct; no forged calibration labels | D/C/G |
| JUDGE-BUDGET | Concurrent runs, consent revocation, unknown submit timeout | Hard reservation cap incl outstanding runs; no revoked egress; no ambiguous resubmit | D/J |
| JUDGE-SCORES | Malformed result, duplicate delivery, missing media, calibration set | Schema validation/dedup; explicit limited scores; only operator labels calibrate | J/T/V |
| CONSOLE-FLOWS | Owner/member/operator, pagination, errors, expired content | Role-safe actions, understandable loading/empty/failure states, keyboard access | C/U/V |
| OPS-RECOVER | Process/node loss, backup restore, disk alarms, deploy rollback | Documented recovery window; no accepted-job/accounting loss within durable guarantees | I/E |
| PERF-PILOT | Diverse traffic/cold+warmed cache, auth, sustained+burst/open-loop | Published latency/error/cost distributions and resource limits at accepted pilot envelope | E/M/W/I |
| FLEET-GATE | Multiworker rebalance, index loss, AZ/capacity/drain tests | Fencing/recovery invariant retained; capacity and rollout thresholds measured | I/E |
| SPLIT-CONTRACT | Load both app/SDK contract fixtures; reject mixed unit/audience/schema | Immutable serving/rate IDs, explicit CREDIT vs USD, consumer/provider scopes; App runs with Lab unavailable | S/F/E |
| CREDIT-GRANT | Race/replay verified onboarding, callback and existing-user backfill | One initial +10,000 CREDIT ledger entry per individual; unverified user cannot mint | D/A/E |
| CREDIT-IDENTITY | Change membership/org/campaign metadata; spoof wallet/user | No repeat grant, ownership change or cross-user spending | D/C/L/E |
| CREDIT-UNITS | Upgrade nonzero/negative USD histories and new CREDIT wallets | Historical USD unchanged; no mixed totals or implicit conversion; old jobs keep denomination | D/E |
| CREDIT-RATE | Publish changed rates/deployment while jobs wait/run | Original admitted revision/rates retained; unknown/private/unpriced model rejected | D/G/A/L/E |
| CREDIT-SPEND | Concurrent two-model calls, refunds/adjustments and completion retries | Exact shared wallet totals/holds and one settlement; no negative available balance | D/C/E |
| APP-JOURNEY | Fresh signup → verify → grant → key → inference → usage → exhaustion; Lab down | Persisted 10,000 once, working examples, correct charge/402, own history and key revocation | A/U/G/E |
| LAB-ACCESS | Two providers/two consumers, mixed memberships, forged IDs, grant revocation mid-queue | No role escalation/cross-provider mutation; no unauthorized content/identity/egress; separate purposes enforced | L/C/D/J/E |
| LAB-PUBLISH | Register → dev validation → publication → App call → rollback during queued request | Dev stays private, public approval/rate audited, old requests pinned, new routing correct | L/G/A/E |

## Revision-task oracles

| ID | Test boundary | Required observation | Owner |
|---|---|---|---|
| API-AUTH | Consumer/provider dev keys, private/public endpoints, revoked credentials | Trusted audience/wallet binding; denied request leaves no job/hold/journal reservation | G/D/E |
| CONSOLE-TENANT | Real PostgREST owner/foreign/provider/operator sessions, cursors, mixed history | Correct scoped rows and exact amounts; missing/failed data is never fixture or false zero success | C/U/V/E |
| DEPLOY-FAILCLOSED | Failed secret reads, partial config writes, runtime prerequisites and rollback | Prior env untouched and no restart on failure; new pilot refuses incomplete dependencies | I/G/E |
| HARNESS-ISOLATION | Concurrent and crashed runs, foreign labeled/unlabeled resources | Own resources only; second-run refusal is nondestructive; missing services remain pending | E/D |

## Test environments and commands

Canonical commands now exist: `make api-env`, `make check`, and `make integration` from the root. `make check` can succeed with Docker-dependent skips; it is not a real-service gate. `make integration INTEGRATION_ARGS="--layer 1 --canary"` checks the container-free subset and mutation/canary sensitivity. Full service absence is PENDING/exit 3. Actual local audit results are recorded in [audit evidence](evidence/wave2-platform-audit.md).

Module suites exist under API/App track directories; Lab remains a README until L1. E owns root `tests/integration/`. F2P/L1 configure discovery/build checks for both apps and shared packages and record runtime/dependency versions. An undiscovered test is a failed setup, not a pass.

Layer 1: fake engine/storage/clock contract and unit tests, no cloud needed. Layer 2: isolated real PostgreSQL, Valkey, ClickHouse and S3-compatible storage with pinned versions; test actual SQL/Lua/DDL and RLS, not mocks. Layer 3: allocated GPU/staging and real storage semantics for media, engine cancellation, journaling latency and deployment. E2 owns local integration compose and fake engine; I owns production deployment. Fakes must support prefill stall, midstream stall, malformed usage, cancellation race and abrupt exit.

## Performance evidence

E1 builds a consented/licensed corpus of at least 64 distinct clips; 32 may form a fast regression set. Distinct media, prompts, durations and aspect ratios must avoid a warm-cache-only result. Benchmark clients authenticate, use intended request forms and support open-loop arrival rates as well as fixed concurrency. Record rejected/accepted separately, client end-to-end latency, queue/prep/TTFT/decode/total, tokens, retries and cache state. Pin engine image/model/tokenizer/profile/GPU/driver and corpus hashes. Never commit secrets, signed URLs or private videos.

32 request samples cannot establish a reliable p99. For trace-overhead microbenchmarks, use repeated interleaved capture on/off runs with >=10, 000 observations where practical, report sample counts and uncertainty, and distinguish synthetic microbenchmarks from GPU request latency. The original H1 overhead thresholds remain targets requiring evidence, not automatic acceptance from a small sample. Cross-region us-east-1 GPU to us-east-2 PG journaling at 50ms batching is an explicit early measurement gate; redesign locality before promising throughput if it fails.

## Gates

The earlier combined G0–G4 sequence is superseded by these independent gates:

- **SPLIT-CONTRACT:** S1 reconciliation and F2R/F2P fixtures/discovery integrated, exact credit and ownership boundaries committed.
- **APP-LOCAL:** E3A passes applicable consumer tests against real local services, including fresh/upgrade migrations and faults. Lab, judge and optional analytics unavailable must not prevent the consumer journey.
- **APP-PILOT:** I2A/I3 and E4 provide allocated staging/GPU evidence, measured envelope, signup settings, published rates, legacy-account disposition and rollback. If capture/callbacks are enabled, their full applicable tests are required.
- **LAB-OPERATE:** E3L + I2L validate provider roles, dev/prod revisions, publication/rollback and separate deployment on staging.
- **LAB-OBSERVE:** E5L + extended I2L deployment evidence validate capture, source grants, content access/retention, review and judge dry-run. Live external evaluation needs a separately assigned consented/budgeted run; dry-run is not live verification.
- **FLEET:** I4/FLEET-GATE after APP-PILOT; no multi-node availability claim from one-node tests.

Payments/OpenRouter/second-owner commercial gates remain deferred. No throughput, availability or quality claim is promoted from a research estimate to a measurement without attached raw evidence.

## Verification log

- 2026-09-20: Defined planned pass/fail oracles and release gate separation. Existing local baseline results are recorded in the scope document; new implementation tests remain pending.

- 2026-09-21: Amended for separate consumer App/provider Lab, individual signup credits and independent release gates; see the platform-split review. Implementation evidence on the other system remains unverified here.
