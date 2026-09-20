# Closed-loop verification and release gates

Every test ID below names a required **future** test; none is claimed implemented by this package. Module briefs map tasks to these IDs. Retain existing research test cases, but namespace them as `SERV-*`, `TRACE-*`, `JUDGE-*` or `CONSOLE-*` so repeated A1/Q1/I1 labels cannot collide. E2 publishes the explicit legacy-to-new mapping in its evidence.

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

## Test environments and commands

Current commands: `python -m pytest apps/infrx-api/tests -q`; in `apps/app`, `pnpm test` and `pnpm lint`. Use a clean pinned environment once F2 lands. Planned module suites live under `apps/infrx-api/tests/<track>/` or `apps/app/tests/<track>/`; E's integration suite under root `tests/integration/`. Those directories do not exist at documentation baseline. F2 must update console discovery before accepting nested tests, and record Python/Node/dependency versions. An undiscovered test is a failed test setup, not a pass.

Layer 1: fake engine/storage/clock contract and unit tests, no cloud needed. Layer 2: isolated real PostgreSQL, Valkey, ClickHouse and S3-compatible storage with pinned versions; test actual SQL/Lua/DDL and RLS, not mocks. Layer 3: allocated GPU/staging and real storage semantics for media, engine cancellation, journaling latency and deployment. E2 owns local integration compose and fake engine; I owns production deployment. Fakes must support prefill stall, midstream stall, malformed usage, cancellation race and abrupt exit.

## Performance evidence

E1 builds a consented/licensed corpus of at least 64 distinct clips; 32 may form a fast regression set. Distinct media, prompts, durations and aspect ratios must avoid a warm-cache-only result. Benchmark clients authenticate, use intended request forms and support open-loop arrival rates as well as fixed concurrency. Record rejected/accepted separately, client end-to-end latency, queue/prep/TTFT/decode/total, tokens, retries and cache state. Pin engine image/model/tokenizer/profile/GPU/driver and corpus hashes. Never commit secrets, signed URLs or private videos.

32 request samples cannot establish a reliable p99. For trace-overhead microbenchmarks, use repeated interleaved capture on/off runs with >=10, 000 observations where practical, report sample counts and uncertainty, and distinguish synthetic microbenchmarks from GPU request latency. The original H1 overhead thresholds remain targets requiring evidence, not automatic acceptance from a small sample. Cross-region us-east-1 GPU to us-east-2 PG journaling at 50ms batching is an explicit early measurement gate; redesign locality before promising throughput if it fails.

## Gates

**G0 contract-ready:** F1/F2 integrated, test discovery proven, all schema/ports/fixtures committed. **G1 locally integrated:** all applicable table cases pass against real local services, migration upgrade tests and failures included. **G2 pilot-ready:** I2/I3 and E3 integrated; tenant/credit/privacy/security cases pass; operator grant, end-to-end inference, async recovery, traces, feedback and judge dry-run work. **G3 pilot verified:** E4 captures allocated live evidence, pilot envelope/SLOs and rollback decision. Judge live evaluation requires separately assigned consented/budgeted run; dry-run alone is not live judge verification. **G4 fleet verified:** I4/FLEET-GATE after G3; no claim fleet is ready from a one-node test.

Payments/OpenRouter/second-owner commercial gates remain deferred. No throughput, availability or quality claim is promoted from a research estimate to a measurement without attached raw evidence.

## Verification log

- 2026-09-20: Defined planned pass/fail oracles and release gate separation. Existing local baseline results are recorded in the scope document; new implementation tests remain pending.
