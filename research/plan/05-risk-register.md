# Review findings, decisions and evidence owners

Open means implementation/evidence is pending, not that the design decision is missing. All rows begin open at this documentation baseline. Coordinator closes a row only with a merged implementation SHA and the named test evidence.

Amended 2026-09-21: reconcile actual remote progress before interpreting status. Original mixed task IDs below map to successors in [the platform split](08-platform-split.md).

| Finding / risk | Required resolution | Tasks | Evidence |
|---|---|---|---|
| Volatile acceptance and Valkey failover could lose acknowledged jobs | PG job/hold/outbox transaction is authority; index rebuild is routine | D2/Q3 | DUR-ADMIT, DUR-OUTBOX |
| Payload exists only in gateway memory | Stage immutable request before acceptance; orphan GC | M1/M3/D2 | DUR-ADMIT |
| Credits race, ship-time price changes, unknown price 0 | Wallet locks, admit-time price version, maximum hold, terminal settlement, fail unknown price | D1/D2/D5 | DUR-CAP, DUR-SETTLE |
| Protected new columns writable through existing grants | Column grants/RPC and role matrix, preserve historical totals | D1/C3 | DUR-RLS |
| Preprocessing bypasses admission limits | Reserve preparation and inference capacity before work | D2/M2/G1 | DUR-CAP |
| Worker retries after visible output or stale lease | Generation checks on every mutation; committed publication marker prohibits retry | D3/D4/W2 | DUR-FENCE, DUR-OUTPUT |
| SSE success precedes durable usage/result | Terminal journal event in settlement transaction | D5/G2/W2 | DUR-OUTPUT |
| Unknown usage creates held balance forever or late debit | Fenced terminal release after 24h; platform absorbs; alert repeat failures | D5/U1 | DUR-SETTLE |
| Surprise202 breaks OpenAI clients | Explicit preference/jobs route only; no response-mode change after headers | G2/G3 | API-MODES |
| DNS check then re-resolve; oversized intake | Pin validated connection, every redirect, bound bytes before parse | M1/G1 | MEDIA-SEC |
| Wrong pixel/context assumptions | Area<=200704, no upsize; context per request; CRF/quantization parity gates | M2/W3/E1 | MEDIA-PARITY |
| Tenant cache namespace incomplete | Salt media cache and engine multimodal IDs plus prefix cache | M2/W1 | MEDIA-SEC |
| Current reasoning filter fails split delimiters and stream errors | Stateful parsing and complete header/disconnect/error matrix | G2 | API-STREAM |
| Trace loss policy conflicts with claimed guaranteed ingestion | Explicit fsync durability boundary, loss counters, off-mode denominator | T1/T2 | TRACE-BOUNDS, TRACE-RECOVER |
| Trace memory bounded by count only; disk blocks loop | Account active bytes; dedicated writer; memory/spool/free-space caps | T1 | TRACE-BOUNDS |
| WAL rotation can truncate unacknowledged data | Segmented checksummed spool, ack-based deletion | T1/T2 | TRACE-RECOVER |
| CH schema/dedup/TTL assumptions untested | Execute pinned DDL; stable IDs/nonnullable version; dedup every consumer; logical expiry | T2/T3/C2 | TRACE-RECOVER, TRACE-TENANT |
| Caller query parameters/object refs bypass tenant binding | Named operations, reserved params, tenant bound last, owned object lookup | C1/C2/G4 | TRACE-TENANT |
| Feedback 201 only volatile; trace lag yields false404 | PG acceptance/outbox and durable ownership lookup | D6/C3/G4 | FEEDBACK-ACK |
| Console feedback incorrectly treated as operator calibration | Separate channel/author role; explicit authorized labels | D6/J3/V2 | JUDGE-SCORES |
| Judge budgets underreserve or double-submit | Maximum cost reservation, consent check, unique intent, quarantine ambiguity | D6/J2 | JUDGE-BUDGET |
| Small/warm benchmark claims p99 and throughput | Distinct licensed corpus, open-loop arrivals, interleaved overhead, uncertainty | E1/E4 | PERF-PILOT |
| Cross-region journal latency invalidates stream targets | Measure east1-east2 commit RTT/batching early; choose locality before pilot gate if needed | I1/W3/E4 | PERF-PILOT |
| Node-local spool on ephemeral disk | Persistent volume, fsync drills, documented host-loss boundary | I2/I3/T1 | OPS-RECOVER |
| Unpinned engine dependencies alter media/cancellation | Pin image/model/tokenizer; capability and parity probes | F2/W3 | MEDIA-PARITY, OPS-RECOVER |
| ALB algorithm/slow-start incompatibility | LOR uses slow-start 0; validate chosen attributes in infrastructure | I4 | FLEET-GATE |
| Rollback drops queue or removes metering | Pause/drain/fence; compatible runtime or maintenance503 | I3/D5 | OPS-RECOVER |
| Shared-file conflicts defeat parallel work | Foundation freeze; sole migration owner; coordinator wiring/locks; per-track roots | F2/all | F-CONTRACT |
| Historical A0 fixes are repeated work | Preserve already-fixed cache/inflight/base64/notes behavior | F1 | F-BASE |
| Org creation or campaign changes repeat the individual grant | Unique user initial entitlement, verified issuance, immutable wallet mapping | A1/D1/A2 | CREDIT-GRANT, CREDIT-IDENTITY |
| USD history silently reinterpreted as credits | Separate unit/ledger, explicit legacy-account cutover policy, original-regime job drain | D1/D5/I2A | CREDIT-UNITS |
| Provider role leaks customer data or grants operator authority | Explicit provider membership and purpose-scoped source grants; test DB and privileged services | L2/C2/C3L | LAB-ACCESS |
| Provider changes model/rate under in-flight calls | Immutable deployment/rate snapshots and audited publication | A3/L3/D2 | CREDIT-RATE, LAB-PUBLISH |
| Lab scope prevents consumer release | Split mixed tasks and remove Lab from App gate closure | S1/E3A/E4 | SPLIT-CONTRACT, APP-JOURNEY |
| Documentation overwrites unseen remote implementation | Inventory current work; map old/new IDs; move code only through active owners | S1 | Reconciliation record with actual SHAs |

## Source verification and outstanding experiments

Primary references supporting review decisions (reviewed during planning, not live environment evidence): [ElastiCache failover](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/AutoFailover.html), [ALB target-group attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html), [ClickHouse ReplacingMergeTree](https://clickhouse.com/docs/reference/engines/table-engines/mergetree-family/replacingmergetree), [Anthropic model documentation](https://platform.claude.com/docs/en/models/overview). Reverify deployed versions/provider features during implementation. Prices belong in the existing cloud-pricing source and provider-rate snapshot; this package does not introduce a second price sheet.

Historical ⚠️ research items remain experiments, not launch facts. E1/E4 own diverse video arrival distributions and performance; M2 owns preprocessing/CRF parity; W3 owns engine/KV/concurrency/media-UUID probes; I1/I4 own region capacity, latency and fleet cold start; T1/T3 own storage volume/rate and retention cleanup; J2/J3 own real provider behavior and quality calibration. Kernel, quantization, response-cache and commercial capacity optimizations remain deferred until their prerequisite evidence exists.

## Verification log

- 2026-09-20: Converted review findings into named implementation owners and objective test oracles. No risk is marked closed based on documentation alone.

- 2026-09-21: Amended for separate consumer App/provider Lab, individual signup credits and independent release gates; see the platform-split review. Implementation evidence on the other system remains unverified here.

## Wave-2 audit additions (2026-09-21)

See [the audit](10-wave2-platform-audit.md) for findings A01–A14 and disposition. Original USD contracts/schema, absent consumer database adapter, uncomposed runtime and absent provider authorization are release blockers until F2P/D1R/C0/G2/L2 evidence exists. Production fixture account reporting is disabled by the local audit correction. Existing hosted installer fail-open exposure remains a deployment risk; I0 is code/test preparation, not proof of a live repair. E2R must repair D harness ownership before parallel database suites. Real-service tests are pending on this host because Docker is unavailable; no unit count substitutes for them.

## Complete-plan additions and latest priority

| Risk | Effect | Mitigation / owner |
|---|---|---|
| App launch drowned in broader Lab roadmap | Immediate Marlin customers still lack a stable self-service endpoint | APP-FIRST execution scope, S2M profile and independent E3A/E4 gate; coordinator activates Lab only after accepted App candidate |
| VLA application context interpreted as action/streaming model support | Wrong API promises, untestable latency or unsafe integration expectations | S2M verifies actual artifact/protocol; recorded SOP analysis first; X1/X3 contracts gate transport/action trials |
| Large robotics corpus split by clip instead of episode/source | Leakage and overstated SOP accuracy | N2 grouped splits, N3 lineage, B2 clustered/paired reports; predeclared temporal rubric and missing-case denominator |
| Later Lab becomes a second wallet/job/permission system | Divergent accounting and data rights | F3 common fixtures, D7–D9 same D owner, reuse exact budgets and source-purpose grants |
| External train/teacher submission times out | Duplicate spending and inconsistent model lineage | Durable intent/unknown state, provider idempotency or reconciliation; manual training baseline; P2/P3 tests |
| Immutable datasets evade revocation | Unauthorized reuse after consent/retention change | Current authorization at read/export/submit, transitive lineage, logical restriction and physical purge evidence; no unlearning promise |
| Fixed metric hides failures or sequential testing overclaims | Bad model promoted | B2 full denominators/required slices; R2 predeclared analysis, coverage and inconclusive states |
| Synthetic quality/performance fixture presented as real measurement | False readiness and unsupported hardware claims | Separate software/local/staging/model/hardware evidence in every E gate; R3/X5/X6 pins |
| Main push implicitly deploys unintegrated work | Consumer regression | Dedicated integration/review branch; release-specific evidence and authorized deployment context |
