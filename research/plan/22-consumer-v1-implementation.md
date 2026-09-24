# 22 — Consumer v1 implementation program

Status: **planned, not implemented**. Prepared 2026-09-24 from main `6db71ee3`, including the `726d004d` acceptance update. Reconcile newer main and the in-flight E4B report before coding. This plan replaces obsolete next-task instructions; it preserves implementation evidence and the full App/Lab roadmap.

## Objective and scope

Deliver a reliable Marlin finite-video endpoint, then a working consumer App: verified individual → one 10,000 CREDIT grant → key → actual inference/result → exact usage/balance → revocation. The original backend-first order remains: accept the repaired backend before App feature dispatch; accept App before Lab. Existing implementations are repaired/reused, not rebuilt.

The user also wants a later inference-hosting platform with GPU scale-up/down, load management, custom weights, hardware-aware vLLM/SGLang deployment and Modal comparisons. [The follow-on program](23-inference-hosting-roadmap.md) preserves that direction, defines the interfaces v1 must retain and a comparison protocol. It is **not an instruction to provision a fleet or add a second runtime in this implementation wave**.

Current launch profile: one pinned Marlin model, finite clips up to the deployed 82-second ceiling, validated codecs/geometry and bounded bytes; text and video input, text/output-SSE and explicit async jobs. A duration below 82 seconds is not a promise that every source encoding/frame pattern fits the engine. Test the complete capability boundary. No native live-video, robot actions, SOP accuracy guarantee, payments or Lab requirement is added.

## Authoritative package

1. [Fresh-session handoff and copyable prompt](24-consumer-v1-session-handoff.md).
2. This program and [manifest v4](tasks.json); [generated ledger](17-task-ledger.md) gives all task IDs.
3. [Audit 21](21-v1-consumer-readiness-review-2026-09-24.md), [as-built handoff 20](20-platform-handoff-2026-09-24.md) and the newest committed operational tail.
4. Detailed briefs: [contracts/data](consumer-v1/01-contracts-and-data.md), [media/worker/gateway](consumer-v1/02-runtime.md), [operations/integration/release](consumer-v1/03-operations-and-verification.md), [consumer App](consumer-v1/04-app.md), [client/load/fault protocol](consumer-v1/05-client-and-load-testing.md).
5. [HTML progress/ETA tracker and parallel coordination](consumer-v1/06-progress-tracker.md): extend the existing tracker early; retain one task graph and evidence-backed gate decisions.
6. Existing [contracts](01-contracts.md), [durable protocols](02-durable-protocols.md), [App requirements](../platforms/03-app-spec.md), [credit policy](../platforms/02-credits.md) and [pending inputs](15-pending-inputs.md).

The manifest remains the only task/dependency graph. These briefs refine existing App tasks and add corrective tasks with their own status; they do not demote previously implemented tasks. E3C/E4C become the current BACKEND-LOCAL/BACKEND-READY gate roots. E3B/E4B stay as their implemented predecessors; a new final certificate may supersede an unfinished old release decision without requiring a pointless re-certification of an obsolete binary.

## Delivery bands and dispatch

| Band | Assignable work | Exit / next action |
|---|---|---|
| V0: current evidence | S3; E2C may begin environment inventory in parallel | S3 records actual release, open findings, latest run results and owned test resources. Do not repeat fixes proven closed by new evidence. |
| V1: contracts and verification | F2C and E2C after S3 | Commit revised contracts/fixtures, owned path map and reproducible Linux checks. Freeze migration design before concurrent consumers rely on it. |
| V2: runtime repairs | D10, M5, W5, G7, G8, E1C, I8 in separate owned paths | Development against F2C is parallel; integrate in dependency order. M6 follows M5 because both edit media lifecycle code. D10 alone writes migrations. |
| V3: real-service backend | E3C, consuming the repaired modules | Real PG/PostgREST/Valkey/S3-compatible adapters, process restarts, permission matrix, retention/expiry/cancel/accounting faults pass. No Next.js process required. |
| V4: measured backend | E1B remaining evidence + E1C protocol; E4C with I8 | One release SHA/image/profile in CREDIT mode; load/soak/overload/recovery and operator decision. Reuse valid prior evidence; rerun affected or missing cells. |
| V5: consumer App | C0 → C3A; A2, A3, U1R; then U2/U3/U4; I2A deployment preparation | Dispatch only after BACKEND-READY is accepted. UI can use approved fixtures during development; production must use real services. |
| V6: consumer release | E3A → I2A/I3 → E4 | Complete browser+API journey, two tenants, hosted email, abuse controls, operational handover, staged/public launch evidence. |

These are dependency bands, not full-band barriers. For example G7 can code from F2C while D10 is being implemented, but cannot claim integration until the real D10/M5 adapters work. No task may mark itself integrated by substituting another fake for its dependency.

### Parallel lanes and file boundaries

| Lane | Tasks | Owns | Must not edit concurrently |
|---|---|---|---|
| Contract | F2C | Python/TS contracts, wire DTOs, fixture/conformance revisions | Coordinator-owned app composition, database migrations |
| Durable data | D10 | All new SQL migrations, `infrx/state/`, PG race/RLS tests | No second schema writer; D7–D9 are later program tasks, not reserved migration numbers |
| Media | M5 → M6 | `infrx/media/`, media unit/adapter tests | Same lane serialized; SQL requests go to D10 |
| Worker | W5 | `infrx/worker/`, worker tests, bounded warmup behavior | Worker composition root wired by coordinator; no independent engine flag changes |
| API | G7 | `infrx/gateway/routes/`, public model document projection, route tests | No financial implementation in handlers |
| Headless account | G8 | `infrx/operations/`, headless examples/ops tests | Shared CLI entry edits coordinated; grants/ledger remain D-owned |
| Client/performance | E1C | `models/marlin2b/bench.py`, its tests, dataset client/protocol/evidence | Does not alter runtime to make a benchmark pass |
| Operations | I8 | `infra/`, deployment/observe/config changes, operational tests | Shared config/composition merges coordinated; no parallel live deployment |
| Integration | E2C → E3C → E4C | Integration harness, fault/load orchestration, release decision | One owner per runner directory; share E1C via imports/contracts, not copied clients |
| App data | C0 → C3A | Server query/action ports, trusted consumer context | A/U do not invent alternate server actions |
| App auth | A2 | Signup/verification/recovery/callback flow and auth tests | Coordinate session/callback services with C3A; no browser-issued credits |
| App catalog/docs | A3 | Models/Docs pages, consumer examples | G7 owns API discovery projection; consume it |
| App accounting | U1R | Usage/credits presentation and common CREDIT display | Shared layout/navigation wired once by coordinator |
| App controls | U2, U3 | Keys/settings UI; operator UI in separate subpaths | C3A owns the actions; U1R helpers committed first |
| App results | U4 | Consumer request detail/result states | No provider trace explorer, analytics or content-sharing controls |

The maximum safe concurrency is determined by disjoint paths and test capacity, not a requested worker count. Seven runtime development lanes are possible after F2C; shared integration and hosted GPU tests stay serialized. For each task use `codex/<task-id>-<slug>` and a worktree from a **committed** integration SHA. Record base/head/owner, path ownership, ports, database/schema, object prefix and container project. Allocate available/reserved ports through the harness; do not reuse the earlier conflicting ephemeral-port convention. Never share destructive-test credentials or targets with production.

Coordinator owns `tasks.json`, common contracts after F2C handback, Makefile/lockfiles/CI, gateway/worker composition roots and App navigation/layout. Workers submit explicit wiring requests with imports/routes/config and a test proving the composed result. When two lanes need a shared file, serialize that change rather than letting both rebase divergent designs.

## Repair coverage and launch consequences

| Audit item | Corrective tasks | Required closure |
|---|---|---|
| RV-01 public claims | F2C/G7/A3 | Release-derived capability/rate/retention; no false ZDR or unsupported tool/video/readiness claims |
| RV-02 upload restart | D10/M5/E1C/E3C | Create/PUT/finalize/resolve survives process replacement; owned, immutable and expiring |
| RV-03 cleanup | D10/M6/I8/E3C | Durable liveness, safe deletion races, content cleanup and bounded cache/process maps |
| RV-04 final certificate | S3/E4C | Final SHA-bound release decision, missing cells resolved; no inferred pass |
| RV-05 readiness barrier | F2C/D10/W5/G7/E3C | No preparer executes before durable acceptance checks/attachment completion, including text-only |
| RV-06 App integration | C0/C3A/A2/A3/U1R/U2/U3/U4/E3A | Real individual context and signup-to-spend journey |
| RV-07 upload client | E1C/M5/G7 | Actual mounted route conformance, then hosted smoke |
| RV-08 replayed performance | E1C/E4C | Unexpected replay invalidates capacity measurement; intended recovery replay remains allowed |
| RV-09 operations | D10/I8/E4C | Bounded pool usage, dedicated runtime role, continuous alerts, durable artifacts and restore |
| RV-10 rollback | I8/E4C | Known-good bundle; public inference/result/settlement after backward and forward rollout |
| RV-11 result TTL | F2C/D10/G7/U4/E3C | Persisted expiry governs every read and cleanup path |
| RV-12 verification | E2C/E3C | Supported Linux runner; explicit prereq failures; Git-default/metrics/utility assumptions fixed |

The currently moderate dependency alert must be triaged by E2C and the owning runtime/App lane, with a documented disposition before public launch. A vulnerability finding is not closed merely by the test suite passing.

## Hard invariants

- PostgreSQL owns acceptance, identity, balance/holds, fences, journal, terminal result metadata and lifecycle eligibility. Valkey remains rebuildable. Never regenerate already-published output or settle twice.
- CREDIT and USD stay exact, separately labelled units. One individual signup entitlement, not one per org/browser callback. Provider dev allocation is separate and starts at zero. No implicit refill/conversion.
- Zero customer trace capture is not zero serving-data retention. Serving payloads/results/media have an explicit lifecycle; metadata needed for billing/idempotency may outlive content without retaining that content.
- A gateway restart, worker crash or cleanup pass may not strand a valid accepted job or delete media referenced by a live job. Expired content cannot be resurrected by replay, alias changes or a second gateway.
- Model/image/processor/chat-template/rate/retention versions are pinned at the proper boundary. Uploaded customer metadata is not an authority for duration, MIME, ownership or resource needs.
- Admission, object retrieval, preparation, queues, body buffering, generation and retries all have bounds. Load tests must measure the DB/CPU/storage/edge as well as GPU.
- Public frontend code never receives privileged database or hosting credentials. Customer results do not become provider-visible without the later explicit data-grant workflow.

## Test-and-fix loop required of the implementation session

1. Run environment preflight; record unavailable prerequisites as BLOCKED/NOT RUN. Run the smallest real reproduction for the assigned finding.
2. Add an executable regression at the failing seam, then implement a bounded fix. Prove the failure oracle detects its intended broken behavior; do not add large mutation lists that test only syntax.
3. Run focused module and shared-contract checks. Integrate reviewed commits in order; run the affected real-service matrix on that combined SHA.
4. Exercise the complete request/accounting/result journey, then the allocated load/fault protocol. Preserve raw attempts, telemetry, state snapshots and a machine-readable verdict.
5. For a failure, classify product defect / workload outside contract / environment unavailable / invalid benchmark. Fix only the relevant owner paths, repeat that cell plus impacted invariants, and perform a final combined run after the last runtime change. Do not silently edit thresholds, omit failures or relabel replay as new inference.
6. Stop a live run on its declared duration/spend/resource bound or correctness failure; cancel/drain/reconcile its accepted jobs, restore the recorded configuration and preserve evidence. Continue code/test work that does not require the blocked resource. Ask only for the concrete missing input at its gate.

[The executable test specification](consumer-v1/05-client-and-load-testing.md) defines profiles, sample rules, fault cases, stop conditions and artifacts. Commands described there as **to implement** are deliverables, not claims that they exist today.

## Inputs and completion levels

P-01 approved CREDIT card, P-05 verified email/abuse configuration and P-18 representative workload/acceptance limits remain release inputs. Reuse existing authorized test resources; inspect historical authorizations rather than asking again automatically. New compute or Modal billing is not implied by writing this plan. Before any resource-consuming run, the environment manifest must state endpoint/resource allowlist, owner, runtime/spend bound, stop/cleanup procedure and whether customer traffic may be present.

Implemented = reviewed code and focused tests. Integrated = real services and fault evidence. Backend-ready = E4C accepted on a pinned target. App-ready = E4 accepted including browser/auth/deployment. A runner's implementation or a smoke pass is not a certificate. No public launch with unresolved P1 findings affecting the enabled path.

Record each handback under its owning evidence directory with source/implementation/integration/deployed SHA, requirements/test IDs, environment, exact commands and outcomes, skips, failure drill, migration/rollback implications, unresolved input and next task. Coordinator updates manifest status only from that evidence. Do not mark this documentation program implemented by committing its plans.

## Later work and estimates

The module briefs break packages into roughly 2–8 hour review slices where feasible; these are planning estimates, not promises about model/agent speed. Live soak windows and restore drills add elapsed time that cannot be parallelized on one GPU. Split any larger slice while preserving its parent acceptance and ownership. Reserve review/retest capacity; do not estimate public launch from file count or test count.

After App acceptance, discuss [inference-hosting milestones](23-inference-hosting-roadmap.md) and the separate Lab roadmap. The existing fleet note is a hypothesis, not approval for four more GPUs or two permanently warm replicas. V1 contributes trustworthy telemetry, deploy identity, artifact restoration, capacity bounds and a provider-neutral benchmark record; it does not pretend to implement autoscaling.

## Verification log

- 2026-09-24: Documentation/manifest revision from audit 21. No runtime feature, cloud deployment, inference or new paid resource was created by this planning work. Validate this package and read the fresh-session handoff for the actual planning checks.
