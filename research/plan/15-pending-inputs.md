# Pending work, decisions and environment inputs

Updated 2026-09-21. Current priority is the Marlin SOP-use-case **App launch**. This register complements [all task statuses](17-task-ledger.md); it is not permission to skip unresolved acceptance criteria. Owners record resolved values/evidence and date here without secrets. Missing inputs block only the stated boundary; continue independent work inside the active scope.

## Release and product inputs

| ID | Missing input / decision | Owner and resolution artifact | Blocks | Work that can continue |
|---|---|---|---|---|
| P-01 | Operator-approved Marlin credit rate, unit, rounding/failed-execution disclosure | Product/operator + D/A3; versioned rate card and public docs | Public metered model publication/E4 | Exact CREDIT contracts, fake rate tests, UI and runtime |
| P-02 | Actual affected existing USD balances/accounts and transition policy | Operator + D/I3; read-only inventory then explicit transition/runbook; never choose an exchange rate | Cutover of affected accounts | Additive schema, separate CREDIT wallet, legacy USD display and compatibility tests |
| P-03 | Local Docker/service availability and test resource allocation | E2R/I; pinned PG/PostgREST/Valkey/ClickHouse/object services, owned names/ports; actual role-matrix evidence | Real-service integration claims | Unit/contract work and installer/harness repair. Docker skips do not pass the gate. |
| P-04 | Allocated staging/GPU target, artifact access, protected config and deploy owner | I2A/I3/E4; environment manifest with names only, backup/rollback/load report | Hosted pilot and actual Marlin parity/load | Local App completion and release scripts; no need to buy capacity automatically |
| P-05 | Verified signup email/callback/recovery configuration and abuse bounds in target environment | A2/I2A/operator; staging fresh-user journey and configured limits | Public onboarding | Local auth/RLS/grant/race tests and accessible onboarding |
| P-06 | Exact Marlin artifact/capabilities and finite-video launch limits | S2M with M/W/E; `research/workloads/marlin-sop.md` to create | Honest published capability/examples and actual-model smoke | Inspect current repo/artifacts, fake schema validation and App infrastructure |
| P-07 | SOP rubric, output/event schema, large robotics dataset rights/access, episode IDs, ground truth and acceptance thresholds | Provider/product + S2M/N/B; dataset/evaluator/workload manifest | SOP accuracy claims, realistic large-dataset evaluation/promotion decisions | Finite-video inference launch with disclosed capability; owned synthetic fixtures and software evaluation loop |
| P-08 | Provider Lab origin/deployment project, operator identity and membership onboarding | L/I2L/operator; origin/callback/role config and denial tests | Hosted Lab | Later activated Lab local features and service integration |
| P-09 | Actual customer source-purpose permissions and retention/deletion commitments | Data owner + C/L/N; grant policy and lineage enforcement evidence | Customer content sharing/annotation/export/training | Provider-owned licensed imports; aggregates without content; App capture off |
| P-10 | Selected live teacher/evaluator adapter/model, approved USD rates and budget/egress authorization | J/P2/operator; calibrated model/rate record and external submission evidence | Live teacher/evaluation calls | Dry-run, protocol server, manual annotation and all App work |
| P-11 | Selected automatic training connector/provider and paid execution terms | P3/operator; supported adapter contract and integration report | Advertising automated training submission | Required manual export/external-run/checkpoint-import workflow; protocol adapter tests |
| P-12 | Real rollout population, allocation unit, budget, quality/latency thresholds and analysis protocol | Provider/operator + R; immutable release policy | Public shadow/canary/A-B | Later local fixtures, control/routing/rollback integration |
| P-13 | Live-video task, cadence/freshness/window contract and trial target | X1 owner; approved workload document | X2 and live-video claims | Current finite-video Marlin App |
| P-14 | Robot/task/policy, clocks/action schema/local control owner and placement latency | X3 owner; approved trial contract | X4 and physical robot trial | SOP analysis of recorded robotics videos; offline model evaluation |
| P-15 | Non-NVIDIA chip/model/runtime choice and hardware access | X5 owner; measured go/no-go and trial contract | X6 and chip support claims | Variant registration and importing optimization evidence |
| P-16 | Measured fleet need and allocated topology/capacity | I4/E4/operator; fleet gate design and load/fault report | Fleet rollout | Single-GPU pilot and recovery; capacity purchases remain separate |
| P-17 | Accept App launch candidate, then activate next Lab milestone | Coordinator/user; dated scope/assignment record | Dispatch of Lab feature work during App-first scope | All App work, shared compatibility contracts and release readiness |

No pending commercial decision changes the confirmed **one-time 10,000 credits per individual** policy. No promotional expiry or refill is introduced. Lab provider_dev wallets start at zero and require audited funding; they cannot reuse consumer signup grants. External USD reservations are separate.

## All 16 original F2.2 carryovers

The [wave-2 audit](10-wave2-platform-audit.md) retains original detail. This table records the remaining owner so a fresh session neither repeats bounded fixes nor loses unfinished work.

| Original item | Disposition at audit `07dfb64` | Remaining owner / proof |
|---|---|---|
| 1 deadline clamp | Contract correction/regression implemented | D2 actual DB clock/elapsed/replay acceptance, G1R/G2 composition |
| 2 engine visible/raw output | W1 local alias remains | F2R shared fixtures; W removes alias only after conformance passes |
| 3 production trace base/clock/metadata | Production still derives from fake sink | F2R extract common base and required clock; T2I real journal/spool integration |
| 4 media staging/ordered parts/duration | Hook shape and duration work incomplete | F2R port/fixture repair; M2 materialization/duration/parity |
| 5 candidate port and UUID sampling | Shared ownership/uniqueness incomplete | F2R types/fixtures; Lab/J consumers use same approved port |
| 6 nullable/legacy console fields, judge audit, codepoint bounds | DTO boundaries incomplete | F2R/F2P contract fixtures; C0 real projections and C3L/J2 audit behavior |
| 7 config limits/pools/cursor secret | Structural validation incomplete | F2R config; I0/G2 fail-closed composition |
| 8 Decimal abs/context isolation | Fixed with regression/sensitivity | Preserve tests; D/J exact arithmetic and CREDIT conformance still required |
| 9 eight mutation-runner copies | Consolidation pending | F2R shared runner, private temp/cache paths and declared exception behavior |
| 10 root integration target/docs | Fixed | E2R maintains discovered suites and real-service execution |
| 11 UI/real context/shape logging | Preview and balances fixed; not real complete reporting | C0 database context/redacted diagnostics; U1R CREDIT; V1M moves provider traces later |
| 12 Supabase docs/role grants | Docs corrected | D1R actual additive migration/role evidence; no hosted apply claimed |
| 13 approved live judge rate | Empty by design until approved | J2/P-10 live-only input; never block App or dry-run |
| 14 unset-mode refusal inversion | Still only an early fixture | I0/G2 actual mounted composition and eighth deliberate defect detection |
| 15 queue fairness wording | Corrected upstream; preserve two-level fairness | Q2/Q3 real scheduling/rebuild tests; no new fairness algorithm required |
| 16 service ownership/RLS denial/shared clock | Pending real-service repairs | E2R five denial inversions with SQLSTATE 42501, owned resources and shared clock |

Additional audit work is assigned to F2P/D1R (product-v2 units/identity), C0 (real reporting including large indexed queries), G1R (endpoint audience), U1R (CREDIT views), V1M (App/Lab route boundary) and I0 (atomic installer). E4 retains cross-region DB/journal latency and actual GPU evidence. S1 still needs current-HEAD independent review; an audit author is not their own independent reviewer.

## Scope intentionally not scheduled for launch

- Live video input, action-policy hosting/robot actuation, additional model families and non-NVIDIA backends require their recorded contracts. The SOP use case over recorded robotics data does not imply these runtime features.
- Consumer paid plans/purchases/invoices/refunds, team pooling, provider payouts and marketplace terms need a separate product decision.
- Speech is explicitly deferred by the user. Do not create a speech implementation as a completeness exercise.
- Fully managed training/RL infrastructure and arbitrary provider-uploaded runtime code are outside the core implementation. Existing external annotation/training integration is core follow-on Lab work.
- G5 callbacks and I4 fleet are conditional; response caching/OpenRouter are not silently reintroduced.

## Handling a block during implementation

Record task/slice, command/evidence, exact missing input, affected gate and next independent task in the coordinator log. Keep `planned` or `implemented` as appropriate with a blocked reason; never mark integrated because the environment is missing. Do not infer budget, permission or a workload threshold from silence. Ask only for the missing decision when the relevant boundary is ready, not again for already authorized local work.
