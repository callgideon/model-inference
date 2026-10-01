# Implementation work packages

**Status: proposed, undispatched.** The user requested plans plus an API lifecycle exercise; this audit has not implemented the missing services. Integrate these packages into the existing task manifest/carried register before assigning work. Do not reset the 133 historical task records or create a competing tracker. [Contracts](contracts.md) define the target; [verification](verification.md) defines completion.

## Milestones and priorities

| Milestone | Required outcome | Gate |
|---|---|---|
| M0: current endpoint pilot | Existing Marlin endpoint and consumer journey meet operating acceptance | Existing E4C, review 26, App pilot/public-signup decisions. No new hosting/trace claim |
| M1: thin web/API parity | Every shipped App/Lab feature has a typed FastAPI operation; Next does not own product database/business operations | AP-00–03, relevant AP-07/08 APIs and AP-09 boundary tests; disabled features explicitly represented |
| M2: first provider model to endpoint | A new internal model project imports real Marlin artifacts, deploys privately, proves serving identity and becomes callable after operator publication | AP-04–06 plus API-only acceptance through consumer settlement |
| M3: production request to review | A permitted real request reaches Lab; a bounded real media-aware judge returns a result; a human review is preserved | AP-07/08 plus real capture/egress/permission/billing proofs |
| M4: improvement loop | Authorized traces become a dataset; evaluation compares revisions; a qualified candidate is promoted/rolled back | AP-10 plus remaining evaluation/release gates. Hosted training is separate scope |

M0 can close against the existing provisioned model without waiting for M2–M4. M1 is mandatory for calling the new architecture API-first. A full lifecycle pass needs M2 and M3 as well. General multi-model autoscaling, SGLang support, heterogeneous chips, continuous video/ROS2 and commercial self-service compute are not hidden inside M2.

No calendar ETA is asserted. Subtasks below are **2–8 engineer-hour planning slices**, excluding review, GPU waits and deployment windows; split a slice again if investigation exceeds that size. Provisioning, source-download, permissions and external judge configuration make elapsed time uncertain. Record actual progress and next blocker instead of counting a mock or scaffold as completion.

## Worktree and ownership rules

Use one recorded integration SHA and isolated `codex/api-*` worktrees. One coordinator owns `gateway/pilot.py`, `lab/compose.py`, `lab/control/app.py`, route mounting, public schemas/client generation, lockfiles, task metadata and migration numbering; lanes propose patches at those seams. No concurrent edits of those shared files without allocation.

One schema owner allocates all new tables/functions/migrations after inspecting hosted pending ranges. Do not assume the next migration is 0060. Preserve immutable migrations and R151 reproof. Each lane uses task-local DBs, object prefixes, API ports and credentials. Production deploys, engine switches and load tests are serialized and recorded by the coordinator. No implementation agent may stop the active E4C window to free a port or GPU.

The original UX worktrees can continue presentation-only changes, but must consume the new client ports after their API prerequisites land. A temporary direct database adapter cannot count toward M1. Existing Supabase functions may remain backend implementation details while clients migrate.

## AP-00 — Contract, schema and integration owner

**Own:** API contract modules/OpenAPI export, generated-client package boundary, route registry/composition integration, migrations and manifest updates. **Depends:** current source/state review. **Crosswalk:** shared contracts/execution protocol; this is an addition, not a completed historical lane.

| Slice | Work | Verifiable exit |
|---|---|---|
| 00a | Inventory every mounted route and web action; finalize paths/DTOs/error mapping/auth audiences in contracts.md | One route/action coverage artifact flags undocumented or unsupported actions; target vs existing is explicit |
| 00b | Export schemas from enabled compositions with explicit request/response models and security declarations | Generated OpenAPI includes real body/response schemas, not only generic `Request`; schema diff checked in CI |
| 00c | Generate TS clients and introduce thin transport ports without switching all consumers at once | Both apps compile using client types; no server secrets bundled; no circular App/Lab imports |
| 00d | Allocate durable operations/idempotency/artifact/model-project/readiness additions with lane owners | Migration rehearsal and rollback/forward recovery evidence; identity/financial invariants unchanged |
| 00e | Integrate route mounts, feature availability, least-privilege DB roles and deployment config | Enabled/disabled/unconfigured matrices tested; no silent mounted-503 pretending to be enabled |

**Failure coverage:** missing auth schema, unknown money unit, missing/null retention bound, incompatible schema change, duplicate operation ID, unauthorized DTO field injection. Preserve OpenAI inference contract compatibility.

## AP-01 — Identity, account and workspace APIs

**Own:** new `infrx/console` session/access service, auth/console/workspace route modules, associated backend tests. Request shared composition changes from AP-00. **Depends:** 00a. **Source assets:** `gateway/lab_auth.py`, `state/lab_access.py`, current account/membership RPCs; App `lib/session.ts`; Lab auth membership adapter.

| Slice | Work | Verifiable exit |
|---|---|---|
| 01a | Wrap supported IdP flows in typed FastAPI auth operations; preserve current callback/PKCE and cookie/CSRF behavior | Existing login/recovery/verification/signout work through facade; tokens/passwords absent from logs; unavailable IdP is not bad credentials |
| 01b | Implement console `me`/capabilities and current account resolution | Caller cannot choose another individual/org; suspended/verified/grant status is server-owned |
| 01c | Implement Lab workspace/capability/member reads and authorized member mutations | Current membership on every operation; revoked membership fails immediately; developer cannot self-promote |
| 01d | Add operator provider onboarding using existing ownership rules | New internal provider/workspace created once; no reassignment of existing NemoStation model |

**Failure coverage:** consumer API key at Lab session door; developer at operator door; foreign workspace; forged user ID; expired/revoked session; cross-origin submission; same email enumeration policy as current auth.

## AP-02 — Consumer reads and exact billing projection

**Own:** console query/read DTOs/services/routes; scoped repository adapters; tests. **Depends:** 00a, 01b. **Source assets:** App console/query/credits and billing/request reads; existing console SQL views/functions. **Do not own:** new accounting algorithms or ledger mutation.

| Slice | Work | Verifiable exit |
|---|---|---|
| 02a | Port wallet/ledger/legacy statement reads into backend repositories | Identical balances for seeded real-PG fixtures; CREDIT and USD remain separate; strings preserve precision |
| 02b | Implement request list/detail/result with filters and server cursors | Key isolation, stable pagination, expired result and unsettled/unknown-usage states survive real wire roundtrip |
| 02c | Port key summaries, account/member summaries and operator list/read projections | Response bounded and authorized; API fields cover every existing screen; no raw SQL errors exposed |

**Failure coverage:** cursor from another user/filter; record created between pages; deleted/expired result; suspended read access; storage/database outage; corrupt/null retention fields. Preserve unknown as unknown.

## AP-03 — Consumer mutations, grant and operator APIs

**Own:** consumer key/grant/feedback service and routes; operator console mutation routes; backend replay tests. **Depends:** 00d, 01b. **Source assets:** App `actions.ts`, `keys.ts`, auth `grant.ts`, feedback and operator adapters; existing audited Python/SQL operations.

| Slice | Work | Verifiable exit |
|---|---|---|
| 03a | Durable API-key create/list/revoke; remove reliance on web process replay cache | Concurrent calls through two API instances create exactly one key; key secret handling matches policy below |
| 03b | Claim signup grant as verified-individual campaign operation | Concurrent login/retry across accounts creates one 10,000 CREDIT grant; unverified account cannot claim |
| 03c | Session-authorized feedback API over existing domain and feature flag | Caller can annotate only own request; replay does not duplicate signal; disabled feature is explicit |
| 03d | Wrap credit adjustment, suspension and key revocation in operator APIs | Nonoperator refused at API and repository; reason/actor/receipt audited; duplicate request applies once |

**Key-response policy:** store secret hash/prefix and durable operation identity. First successful creation returns the secret once. A retry returns the same key metadata with `secret:null`/`secret_returned:false` when the secret is no longer available, never creates a second key. The UI tells the user to revoke that key and explicitly create a replacement if delivery was lost. Do not invent recoverable plaintext storage to make retries convenient. Document and test this wire distinction.

## AP-04 — Model projects and verified artifacts

**Own:** `infrx/lab/artifacts` (new), artifact/project routes, scoped object adapter, manifest/import worker; registration revision logic after path ownership is allocated. **Depends:** 00a/00d, 01c. **Crosswalk:** closes first-model gap beyond carried row 30; future hosting roadmap remains broader.

| Slice | Work | Verifiable exit |
|---|---|---|
| 04a | Project identity and immutable artifact manifest schema; card/task metadata | Empty provider can create a model project without a preseeded serving revision; repeated action returns same ID |
| 04b | Resumable bounded upload session, part transport and final verification | Reordered/resumed parts succeed; wrong hash/missing shard/path traversal is refused; interrupted upload expires cleanly |
| 04c | Pinned repository import using approved source and secret references | Actual Marlin source/commit fetched and every expected file verified; mutable ref and prohibited source refused |
| 04d | Compatibility report and immutable serving revision creation | Supported Marlin profile accepted; unsupported architecture/runtime/input format returns specific reasons; no count-based label race |
| 04e | Operator adoption of verified existing artifacts/resources | Existing production identity preserved; adoption receipt includes independently measured hashes; foreign resource adoption refused |

**Failure coverage:** retry after object upload before DB commit; worker killed while hashing; duplicate completion; stale source credentials; too-large manifest; wrong owner; extra object paths; unsupported code-bearing artifact. Actual bytes must be tested, not only manifest DTO validation.

## AP-05 — Durable private deployment and real smoke

**Own:** deployment operation service/controller/engine-smoke adapter and dedicated deployment routes/tests; infrastructure adapter under allocated `infra/lab/hosting` paths. **Depends:** 00d, 04d/04e, approved target capacity. **Crosswalk:** carried rows 30 (real smoke), 31 (port allocation), 33 (dev target), plus new provisioning work.

| Slice | Work | Verifiable exit |
|---|---|---|
| 05a | Operation/outbox/lease/fence transitions, progress and cancel API | Controller killed at each boundary resumes or fails terminally; validation cannot remain orphaned forever |
| 05b | One approved host/profile allocator and engine launcher | Private candidate uses task-owned capacity/ports; production instance remains untouched; uncertain allocation reconciled once |
| 05c | Artifact install + pinned engine/readiness identity checks | Served hashes/image/options/processor/harness match requested version; mismatch prevents readiness |
| 05d | Real finite-video smoke through private admission path and readiness receipt | Actual GPU response plus bounded timing/usage; empty/stale/fake receipt cannot promote revision |
| 05e | Retirement/drain/cleanup, readiness expiry and restart reconciliation | Lost/failed engine becomes unavailable; active jobs honor drain rules; only owned resources are torn down |

The whole allocator is not one 8-hour task; break the hardware/provider adapter further after investigation. Start with one supported profile. Autoscaling and scale-to-zero need later queue/cold-start/SLO design; do not advertise them based on a fixed replica field.

## AP-06 — Private credentials, publication and catalog routing

**Own:** scoped control credential/funding API, operator publication APIs, catalog generalization and endpoint-binding adapters; ask AP-00 to mount. **Depends:** 03a replay primitive, 05d, existing catalog/ledger. **Source assets:** `lab/control/__init__.py`, `operations`, `gateway/routes/{catalog,models}.py`, existing registry/listing CAS.

| Slice | Work | Verifiable exit |
|---|---|---|
| 06a | Expose dev keys, dev wallet reads and operator private card/funding | Dev key invokes only its private ready endpoint; consumer/operator keys cannot do so; exact approved CREDIT settlement |
| 06b | Proposal detail, operator approval/rejection/rollback through API | Role matrix, reason, expected version and immutable candidate pins; two racing approvals cannot both win |
| 06c | Connect registry publication, approved per-model profile and endpoint routing | Newly approved test model appears in `/v1/models` and calls the intended engine; private/unpriced/stale revisions stay hidden |
| 06d | Rollback and route-propagation failure behavior | New admissions follow confirmed listing; old jobs keep original pins; route/cache outage produces explicit unavailable |

Preserve the existing Marlin alias and card. Test first under a distinct internal listing/environment. Do not publish an unqualified candidate over the consumer production alias merely to exercise a button.

## AP-07 — Consent, trace capture and analysis APIs

**Own:** consumer data-use/grant/capture routes, trace query/projection extensions, trace deployment/configuration proof. **Depends:** 01/02 auth/context, existing trace stores and 00d if schema additions needed. **Crosswalk:** carried rows 26, 35, 42 (content reads), 43 (feedback UI) where still open; inspect latest evidence before reopening.

| Slice | Work | Verifiable exit |
|---|---|---|
| 07a | Typed capture/data-grant CRUD and expiry semantics | Grantor-controlled, purpose-specific permission; provider cannot grant itself content or judge rights |
| 07b | Compose trace storage/pumps/retention and scoped Lab routes | Sync, SSE and async requests with capture produce inspectable traces; off mode produces no content trace |
| 07c | Trace list/detail/filters/media and feedback read projections | Actual timestamps/usage/pins, metadata-only and missing/expired content faithfully represented; no foreign content |
| 07d | Lost-ack, duplicate delivery, outage and retention reconciliation proofs | Exactly one logical projection; inference remains available during trace outage; revocation before read/egress blocks access |

Use test-owned clips. Do not enable content capture or external judging for all existing customers as part of deployment. A legitimate customer request can succeed without trace content; the UI must say why evidence is missing.

## AP-08 — Judge and review API/worker loop

**Own:** judge HTTP/DTO layer and its scoped domain adapters, criterion/rubric/read projections, judge-worker configuration proof, request review APIs. **Depends:** 01c, 07a/07c; selected approved judge adapter/payer for live pass. **Source assets:** existing judge RPCs, judge worker, review and calibration domain.

| Slice | Work | Verifiable exit |
|---|---|---|
| 08a | Wrap configuration/budget/run/calibration operations in FastAPI; add missing detail/list/estimate contracts | Existing role/payer/purpose rules enforced through HTTP; no Supabase calls required from Lab |
| 08b | Versioned SOP rubric + media/evidence-aware result schema | Missing required video causes abstention; malformed judge result quarantined; human label and teacher label distinct |
| 08c | Live worker composition with frozen sample, reservation and egress checks | One bounded real call uses approved model/media and records exact pins/cost; dry-run never presented as scored |
| 08d | Restart/ambiguous send/budget-exhaustion/cancel integration | No duplicate charge or blind resubmission; revocation before send sends nothing; consumer CREDIT unchanged |
| 08e | Human review and calibration projection | Review stored once with provenance; insufficient reference labels never display “calibrated” |

If no approved judge credentials, spend limit or model modality support is configured, live judging stays BLOCKED. Building a UI cannot resolve those dependencies. An implementation session may configure the authorized test environment using the existing operator process; it must record the concrete configuration, not guess production permission.

## AP-09 — Thin Consumer App and Lab adapters

**Own:** frontend migration; split **09-App** and **09-Lab** into distinct worktrees. Shared generated clients belong to AP-00. **Depends:** corresponding routes above, not the whole lifecycle at once. **Crosswalk:** existing UX-00–08 lanes; preserve their visual/accessibility work.

| Slice | Work | Verifiable exit |
|---|---|---|
| 09a App reads | Replace console/query/billing/usage/direct page DB reads with generated clients | Existing page fixtures and hosted scoped data match; static dependency audit finds no product DB reader |
| 09b App actions | Replace auth grant/key/feedback/operator mutation adapters | Browser flow calls API; durable uncertain-outcome UX; no key minting/business replay cache in Next |
| 09c Lab legacy adapters | Replace memberships/judge/review RPCs; retain existing HTTP ports via generated transport | No product Supabase calls in Lab; role/content states remain distinct |
| 09d Lab onboarding | Add model/import/deploy operation wizard and API-backed readiness/details | Empty workspace can follow actual prerequisites; no “deployed” badge from record creation |
| 09e Both apps | Capabilities, service unavailable, mobile/keyboard and session expiration | Every visible action maps to a documented API; disabled/unavailable states tested without fake production data |

Remove superseded business adapters once the parity gates pass. Do not retain dormant direct-DB fallbacks that activate on an API failure. Frontend tests may mock generated clients; final hosted acceptance may not.

## AP-10 — Dataset, evaluation and improvement closure

**Own:** fixes to existing dataset/evaluation/pipeline read adapters and worker composition, according to allocated original owners. **Depends:** 07/08 for production-derived data, 05/06 for callable candidate endpoint. **Crosswalk:** carried row 27 (double-debit), current evaluation/pipeline listings, row 29 external training input, existing dataset/teacher/rollout gates.

| Slice | Work | Verifiable exit |
|---|---|---|
| 10a | Fix evaluation listings and pipeline read composition | Authorized empty = 200 empty, service failure = 503; actual nonempty records readable |
| 10b | Root-cause killed evaluation attempt debit issue | Reproduce failure, fix authoritative accounting boundary, rerun real-process kill/restart oracle without weakening it |
| 10c | Objects/import workers and rights-preserving trace-to-dataset operation | Immutable version/manifest, content-purpose/expiry/holdout checks; import resume uses corrected oracle |
| 10d | Run SOP benchmark on private candidate with repeatable report | Exact dataset/model/harness pins and seed, same cases, failures/abstentions, quality and performance separately measured |
| 10e | Carry reviewed labels through external-training/checkpoint and release evidence | Existing supported external integration only; unsupported trainer remains explicit; new revision requires qualification |

Do not enable the eval worker before 10b passes. Do not let missing hosted-training credentials block the narrower trace/judge milestone; report that additional step separately.

## AP-11 — API-only acceptance and deployment evidence

**Own:** lifecycle runner, evidence manifest, service deployment integration/runbook, final boundary/conformance gate. **Depends:** 00 contracts; each stage runs only after its feature dependencies pass. **Crosswalk:** extends existing `lab-*`/`app-e2e`/backend gates rather than replacing their fault/scale coverage.

| Slice | Work | Verifiable exit |
|---|---|---|
| 11a | Persistent runner state with operation IDs, input hashes and explicit cleanup ownership | Restart resumes existing resources; no direct product DB/RPC/CLI bootstrap hidden in a pass |
| 11b | Stage-local API assertions and secret-safe evidence capture | Every step has receipt/assertion, exact release/identity and elapsed time; prerequisites yield BLOCKED, not green skip |
| 11c | Execute isolated real artifact → private engine → publication → consumer request | All positive-path outputs are real, including served identity and settlement; bounded resources and owned cleanup |
| 11d | Execute consent → trace → live judge → human review, then optional improvement | Proof of access/egress/budget and media handling; separate lifecycle/quality/performance verdicts |
| 11e | Deploy verified API + generated clients, run hosted smoke and UI thin-boundary check | Release identities recorded, existing consumer regression checks pass, rollback plan usable |

The current [probe.py](probe.py) only implements the read/refusal audit. It does not satisfy 11a–11e; its successful exit means evidence was collected, never that the complete lifecycle passed.

## Dependency and integration order

```text
AP-00 contracts/schema/clients
  ├─ AP-01 identity ─ AP-02 reads ─┐
  │                  AP-03 actions ─ AP-09-App ─ M1 consumer API parity
  ├─ AP-04 artifact ─ AP-05 deployment ─ AP-06 release ─ M2
  └─ AP-07 consent/traces ─ AP-08 judge ─ AP-09-Lab ─ M3
                             └─ AP-10 datasets/evals ─ M4
AP-11 runner develops alongside each lane; integration/production runs are serial.
M0 consumer pilot closure remains an independent existing operating track.
```

Maximum useful parallel work after contracts: consumer reads/actions, artifacts, trace APIs and judge HTTP wrappers (against committed DTOs) can proceed independently. Deployment hardware work depends on artifact/revision identity; real judging depends on trace grants/storage. UI lanes can build client-backed states before live services, but must not claim runtime completion until integration.

Each handoff records base SHA, branch/worktree, owned files/ports/schema requests, current task/register IDs, prerequisites, exact test commands, observed results and next blocker. A package closes with code + API contract + real seam evidence + documentation updates. Shared integration tests failing after merge reopen the responsible package; no task is closed solely because its isolated mocks pass.
