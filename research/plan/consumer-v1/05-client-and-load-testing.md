# Client, capacity and recovery protocol — E1C / E1B / E4C

Status: specification to implement and execute. **No benchmark result is claimed by this document.** E1C owns the client/measurement changes; E2C owns runner integration; E4C owns the final decision. Reuse `models/marlin2b/bench.py`, its tests and existing backend certify code. [Program 22](../22-consumer-v1-implementation.md) controls dispatch and authorization. Slice the client, profile validator, load profiles, fault integration and reporting into separate 2–8 hour reviews.

## 1. Executable preflight contract

Deliver a versioned JSON profile/schema and `--validate-only` path that performs no inference/provisioning. Existing runner flags may be extended; do not document a proposed CLI as already supported. A resource-consuming run must refuse to start without:

| Field group | Required information |
|---|---|
| Identity | Run ID; source/deployed SHA; image/runtime/weights/tokenizer/processor/template hashes; preparation and engine options; migration/config versions |
| Target ownership | Explicit host/endpoint/resource allowlist; environment owner; allowed fault targets; test tenant/key references; whether customer traffic exists; authorized maintenance window if relevant |
| Bounds | Maximum duration, requests, input bytes, output tokens, concurrency, spend and pending/drain time; approved currency/rates or auditable cost calculation; resource/latency stop thresholds |
| Workload | Immutable manifest hash, permitted data source, item IDs, duration/geometry/FPS/codec/bytes, prompt/output constraints, transport/mode, tenant mix, seed and expected invalid cases |
| Measurement | Client location/resources/clock, open- or closed-loop arrival model, warmup exclusion, cache regime, metrics/thresholds, minimum samples, repeats, fault schedule |
| Cleanup | Cancel/drain/reconcile policy, owned prefix/DB names, restore config/bundle, maximum cleanup window and evidence destination |

Do not put secrets, media or raw prompts into this profile. Reference protected config/data by IDs/hashes. A missing rate/budget blocks paid testing, not profile validation/local fixtures. Reuse prior explicit authorization where applicable; this plan does not approve new GPU/Modal purchases.

Budget checks must include outstanding requests/holds, not only settled usage. Bound token/video/input volume independently when final usage is uncertain. Stop admission early enough to cover already accepted work. Never treat the user-visible promotional CREDIT balance as infrastructure USD cost.

## 2. E1C client repairs and dataset usability

1. Implement actual mounted upload contract: accepted create fields (`max_bytes`, `bytes`, `accepted_mime`, `digest` as applicable), response `upload_handle`/`destination_ref`, authenticated same-host PUT and completion. Replace the incompatible `purpose`/`filename`/`sha256`/`content_type` assumptions. Consume F2C DTO fixtures and test through real router + M5 storage, including gateway restart. Never forward API credentials to an arbitrary returned origin.
2. Add a resumable large-dataset recipe: stable dataset/item ID → persisted request/idempotency mapping; bounded producer queue and upload/inference concurrency; durable local progress; result/usage export; interruption/restart without new logical billing. Stream/iterate the manifest rather than loading an unbounded corpus into memory. Respect result expiry; retain authorized output locally only according to declared policy. Failure export supports retrying an identified subset, not rerunning the entire corpus.
3. Separate transport retry, logical replay and new inference. Intentional resume may reuse output and must be labelled; capacity measurement must invalidate **any unexpected replay**. Zero replay is necessary but not sufficient: also verify exact candidate/model/config, missing attempts, driver lag and counter reconciliation. SDK retries are disabled or explicitly observed.
4. Record upload, admission, queue, preparation, engine-start, first-output, terminal, fetch/result and reconciliation timestamps where available. Use monotonic durations within one process; clock synchronization/error bounds are required for cross-host stage comparisons. Non-streaming first response is not interchangeable with streamed TTFT. Report output tokens and lengths to avoid rewarding shorter outputs as speed.
5. Unit-test parsers/counters/invalidity rules, route-conformance test, then actual hosted smoke with an in-cap video and the explicit over-cap control. Preserve old raw E1B data; regenerate derived summaries only with labelled parser version and no retroactive reclassification of measured inference.

Acceptance: an operator can interrupt/resume an owned finite-video corpus, obtain one logical result/settlement per item and an exact failure export. A benchmark containing a replay, omitted request or overloaded client receives INVALID, never PASS with an inflated throughput number.

## 3. Workload and sample controls

- Freeze actual bytes/hashes across implementations/providers. A regenerated clip of nominally equal duration is not the same input. Use permitted representative robotics clips plus explicit security/format fixtures; synthetic clips are labelled and cannot establish SOP accuracy.
- Stratify primary **video-only** results by supported duration/geometry/FPS and output length. Keep text-only and mixed-load results separate. Include short, typical and near-cap clips, plus exact boundary/invalid cases. 82 seconds is a duration cap, not a guarantee against independent frame/context/resource limits.
- Distinguish cold host/container, cold artifact cache, loaded model with cold preparation/prefix cache, warm repeated input and warm distinct inputs. Warmup attempts/costs remain recorded but excluded from explicitly warm performance denominators.
- Provisional sample floor: at least 60 fresh accepted requests per reported p95 class and 300 for a gating p99, with raw sample count and uncertainty. These floors are reporting discipline, not statistical confidence guarantees. If budget/profile cannot support a tail estimate, mark it descriptive/not gated rather than inventing precision. Predeclare repeat count; prefer three independent warm runs where budget permits and report spread.
- Select numeric SLO/error/cost thresholds with the workload owner (P-18) **before** a qualifying run. Harness correctness assertions are fixed regardless of SLO. Do not invent a universal video latency promise from the historical mixed 114-accepted cells.

## 4. Required progression of profiles

| Profile | Traffic / fault | Pass evidence and scope |
|---|---|---|
| P0 smoke | Verified funded key; text plus in-cap video; sync/SSE/async; upload and permitted direct media path | Fresh inference, result, exact hold/settlement, revoke denial; not capacity proof |
| P1 boundary | Byte/duration/frame/context limits; wrong MIME/digest; foreign/expired handle; malformed/slow/large body; rejected output options | Bounded rejection without leaked credit/work/buffer; valid adjacent cases still serve |
| P2 local recovery | Every durable transition, zero-media readiness, two collectors, DB/object/queue outages and two tenants | E3C deterministic state/ledger/object assertions, bounded recovery, no cross-tenant output |
| P3 steady arrival | Video-only duration bins; then declared production mix; open-loop rate ladder | Fresh completions and SLO goodput, queue age/stage/resource profiles; driver capacity valid |
| P4 burst/overload | Bursts 8, 16, 32 where authorized; sustained above measured capacity; slow body clients | Defined 429/503/deadline behavior, bounded memory/queue/drain, recovery after offered load drops |
| P5 fairness/accounting | Noisy and quiet tenant; max-output and large-video mix; low/zero balance, concurrent cancel/reconcile | Quiet tenant's predeclared service bound, no tenant cap bypass, exact holds/settlement and no negative funds |
| P6 soak | At least four hours at selected sustainable workload/rate, unless a stronger existing approved protocol applies | No unbounded resource/queue growth; every accepted item accounted for; scheduled cleanup/alerts and post-drain checks |
| P7 hosted recovery | Worker/gateway/engine/Valkey restart, scoped DB/object interruption; artifact restore and real rollback | User-visible recovery time/errors plus final inference/result/accounting, not health URL only |
| P8 final combined | Accepted config after last fix; P0 plus impacted P1–P7 cells and reconciled run | SHA-bound certificate, explicit reused cells with rationale, no unresolved required failures/skips |

Suggested **exploratory**, not promised SLO rates: 0.25, 0.5, 1 and 2 requests/s, stopping below any resource/spend/correctness bound. Select actual ladder from the measured workload; large near-cap videos can saturate much earlier. Record all attempted arrivals. A fixed-concurrency loop is useful for saturation but cannot replace open-loop arrivals for tail latency/overload analysis. Use a capable external driver; record scheduled-versus-actual start lag and invalidate runs where the driver silently limits offered load. Avoid coordinated omission.

Hold the model/version and engine flags constant while sweeping load. Diagnose the bottleneck before changing concurrency: gateway/body pools, DB pool/commit, storage transfer, preparation CPU/disk, queue, GPU prefill/decode and result delivery all matter. One-at-a-time controlled optimization precedes a final combined trial; reject changes that break quality/parity or recovery.

## 5. Soak and fault discipline

The runner timeout must cover setup + full soak + drain + reconciliation, not terminate a four-hour soak after one hour. Sample a warmed steady baseline and resource slopes, not first cold sample versus last warm sample alone. Track RSS, object/cache/disk usage, FD/tasks/connections, DB waits, GPU memory/utilization, queue depth/age and unresolved holds. Include idle/drain windows to distinguish bounded cache growth from leaks.

Define per-fault expected retry/error/deadline behavior and recovery deadline before execution. Terminate only allowlisted test processes/resources. Disk-pressure tests use a bounded isolated volume; network/DB faults target an isolated environment or authorized maintenance window. Replacement-host and rollback tests require a recoverable bundle. Do not kill a shared production DB, erase caches of live customer jobs or globally shorten customer TTLs for a test.

Use accelerated clocks for local retention races, plus hosted expiry/cleanup smoke for the actual configured policy where feasible. Explicitly report a long-duration check still pending; a simulated clock cannot establish actual scheduler operation by itself. During each drill, compare serving availability with **settled durable state** after recovery. Unknown engine usage is reconciled explicitly, never silently charged zero.

Stop immediately on negative funds, duplicate settlement, cross-tenant access, conflicting terminal output, loss of accepted data or unsafe resource headroom. Stop new traffic at declared spend/time/request/volume limits, drain/cancel within policy, preserve evidence and restore. Budget exhaustion or missing infrastructure is BLOCKED/NOT RUN, not a performance failure or pass.

## 6. Results and validity

Required summary fields: profile/schema/run ID; candidate/deployed identity; corpus/config/card hashes; offered attempts, valid offers, fresh accepted, deliberate invalid, rejected, replayed, failed, timed out, canceled and completed; actual rate/concurrency; warmup count; driver lag; output lengths; TTFT/e2e/stage latency by class; successful clip-seconds/hour; SLO goodput; actual charges/estimated infrastructure cost labelled separately; telemetry/evidence references; invariant reconciliation; stop/cleanup state; verdict/reasons.

Define denominators explicitly. Error rate includes failed valid offered traffic, not only admitted requests; overload rejection is visible even when contract-correct. Report accepted-service success separately. Unexpected benchmark replay invalidates the run. Do not count rejected over-cap clips as accepted video capacity, completed text as video throughput or the same idempotent output twice.

Cost per successful video-hour = attributable infrastructure USD / (unique successfully processed in-contract clip-seconds / 3,600). Also report SLO-qualified cost/goodput and total spend including idle, warmup, rejected work/retries and control plane. If price, billing granularity or allocated-cost evidence is missing, cost is unavailable/estimated with source date, not zero. Model quality/parity is reported separately from transport success.

## 7. Required automatic fix loop

Runner emits a failing cell with seed/item/request IDs, sanitized configuration and replay command. Agent classifies correctness defect / performance bottleneck / unsupported workload / environment fault / invalid measurement. Reproduce at the smallest failing seam; add regression; fix owning module; run focused and affected integration checks; redeploy pinned candidate; repeat the failed cell and affected invariants. After the last fix, run final combined verification. Keep failed reports and configuration changes in an append-only experiment log.

A run cannot lower its threshold mid-flight, suppress errors, drop difficult cases or exceed the authorized budget in pursuit of green results. When an external input blocks live testing, complete code and local verification, name the exact blocked cell and next command, and hand it back without claiming acceptance.
