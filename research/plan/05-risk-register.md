# Review findings, decisions and evidence owners

Open means implementation/evidence is pending, not that the design decision is missing. All rows begin open at this documentation baseline. Coordinator closes a row only with a merged implementation SHA and the named test evidence.

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

## Source verification and outstanding experiments

Primary references supporting review decisions (reviewed during planning, not live environment evidence): [ElastiCache failover](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/AutoFailover.html), [ALB target-group attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html), [ClickHouse ReplacingMergeTree](https://clickhouse.com/docs/reference/engines/table-engines/mergetree-family/replacingmergetree), [Anthropic model documentation](https://platform.claude.com/docs/en/models/overview). Reverify deployed versions/provider features during implementation. Prices belong in the existing cloud-pricing source and provider-rate snapshot; this package does not introduce a second price sheet.

Historical ⚠️ research items remain experiments, not launch facts. E1/E4 own diverse video arrival distributions and performance; M2 owns preprocessing/CRF parity; W3 owns engine/KV/concurrency/media-UUID probes; I1/I4 own region capacity, latency and fleet cold start; T1/T3 own storage volume/rate and retention cleanup; J2/J3 own real provider behavior and quality calibration. Kernel, quantization, response-cache and commercial capacity optimizations remain deferred until their prerequisite evidence exists.

## Verification log

- 2026-09-20: Converted review findings into named implementation owners and objective test oracles. No risk is marked closed based on documentation alone.
