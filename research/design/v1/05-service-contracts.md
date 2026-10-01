# UI service map and backend dependencies

**Superseding requirement:** [API lifecycle contracts](../../plan/api-lifecycle/contracts.md) require all product features behind FastAPI. Paths below identify code to reuse/migrate, not permission to retain direct product DB/RPC operations in Next.js. Auth/session transport may remain thin. First-model onboarding, dev keys, operator publication and judge HTTP APIs are tracked in AP-00–11. CX-01 now requires an authenticated FastAPI capability/availability response; a web-local config adapter is no longer the target boundary.

Read before implementing the mockups. “Exists” below means a source interface/implementation exists, **not that its hosted composition is accepted**. [STATUS](../../../STATUS.md) and the [carried register](../../plan/consumer-v1/10-carried-work-register.md) own runtime enablement.

## Existing sources of truth

| UI area | Source path from repository root | Safe use / limit |
|---|---|---|
| App theme/components | `apps/app/app/globals.css`, `components/ui`, `components/sidebar.tsx` | Reuse visual system; fix mobile behavior, don't copy auth into Lab |
| App auth/keys/account | `apps/app/app`, `lib/services/console.ts`, `lib/contracts/types.ts` | Migrate authoritative reads/actions to FastAPI; Next actions only forward sessions/requests. AP-03 replaces the current process-local key replay cache with durable identity; no browser service credential |
| Published model/docs | `apps/app/lib/contracts/v2/published-model.ts`, `app/(console)/models`, `app/(console)/docs`, `components/snippet.tsx` | Capabilities/rates/TTL come from actual published data; wire null requires deliberate handling |
| Usage/results/credits | `apps/app/app/(console)/usage` (including `[requestId]/result-panel.tsx` and `status-poller.tsx`), `lib/services/query.ts` | Consumer-owned records; no Lab content grant inferred; exact accounting and transient content remain |
| Lab membership | `apps/lab/lib/auth/access.ts`, `guard.ts`, provider layout | Session-scoped workspace, roles; `read_customer_content` is in no role by default |
| Lab registration/control | `apps/lab/lib/services/control/{port,http,actions,view}.ts` | Model/revision, deployment record, smoke result, proposals, aggregates; no general hosting API |
| Lab traces | `apps/lab/lib/services/traces/port.ts`, `components/traces` | Cursor-only listing, explicit metadata/content access; hosted composition needs repair/proof |
| Lab datasets | `apps/lab/lib/services/datasets/{port,flows,views}.ts` | JSONL preview/import/status/requeue, immutable versions, derive, export, read part |
| Lab evaluation | `apps/lab/lib/services/evaluation/port.ts` | Catalog, runs, experiments, report, launch/cancel, subscriptions; listings/worker accounting gates open |
| Lab review/training | `apps/lab/lib/services/pipelines/port.ts`, `review`, `judge` | Labels, approvals, teacher plans, external training and checkpoints; no native trainer provisioning |
| Lab rollout/optimization | `apps/lab/lib/services/rollouts/port.ts` | Frozen policy evidence, operator proposals, variant comparison; no instant traffic slider |

Paths within a row's app are relative to that app when abbreviated. Validate exact exports/types at the implementation checkout; this package does not replace them.

## Dependencies introduced or clarified by the design

| ID | Contract and acceptance | Launch treatment / owner |
|---|---|---|
| CX-01 | FastAPI-owned feature/service availability by selected workspace: capability, configured/disabled/unavailable/unknown state, safe reason, optional verified evidence. Auth checked separately; 404/timeout must not be guessed into “disabled.” Use authenticated capability APIs in AP-01, not web-local product configuration. | Needed for setup guidance; absent this, show unavailable and Retry. UX-03 coordinates AP-01. |
| CX-02 | Scoped deployment detail + verified readiness/endpoint evidence: exact model/deployment/serving identity, actual engine identity, check kind/environment/time/result, authorized endpoint when one exists. Reject stale/mismatched identity; no shared secret in endpoint metadata. | Enhancement, not prerequisite for styling current records. UX-03 ships honest fallback; hosting/control owner adds evidence before “Ready/Connect.” |
| CX-03 | Provider trace filters/search and enriched latency/usage fields, scoped server-side with stable cursor semantics and explicit grants. | Deferred. UX-05 initially renders existing cursor list and available metadata; no imaginary global filters. |
| CX-04 | Dataset display metadata, schema-driven mapping definitions and collision-safe version allocation. Existing import schema may supply mapping structure; inspect before inventing a schema. | Basic guided import can wrap existing schema. Names/version auto-allocation require owned backend work; fallback validated refs/version. UX-06. |
| CX-05 | Authorized sample media read with purpose/grant/expiry checks for synchronized video review, plus actual typed temporal labels. | Deferred for L2. Review can render current authorized text/JSON; no raw object-store URL bypass. UX-08 + dataset/review owner. |
| CX-06 | Exact wire semantics for `physical_deletion_bound_s`: absent/null is unknown; accepted bound requires evidence; parser parity across TS/Python and serialization. Determine whether zero is a valid approved value before displaying a promise. | P1 correction. UX-01 owns formatter + actual catalog parsing path and coordinates backend schema owner; regressions tested from live-shaped fixture. |
| CX-07 | Optional persisted “has successful request”/onboarding state if existing scoped reads cannot establish it reliably. No new event tracking or lifetime scans. | Optional. UX-04 may ship collapsible Quickstart with no completed-state claim instead. |

No new database migration is required merely to restyle these screens. A lane needing schema work must use the repository's migration allocation/reproof process, rather than independently choosing a migration number. Do not turn optional contracts into consumer launch blockers.

## Roles and content

| Action | Viewer | Developer | Administrator | Other requirement |
|---|---|---|---|---|
| Read permitted workspace records/aggregate health | Yes | Yes | Yes | Backend scope and service availability |
| Register / manage private dev deployment | No | Yes | Yes | Existing action preconditions |
| Run evaluation | No | Yes | Yes | Catalog, budgets, accounting and worker gates |
| Propose public publication | No | No | Yes | Operator makes final production decision; current provider rollback proposals are refused, so do not offer that action |
| Manage members / assign reviewer where required | No | No | Yes | Actual backend operation integrated; no fake UI |
| Read customer content | Not automatic | Not automatic | Not automatic | Specific current content/purpose grant |
| Train/export/teacher use of data | Not implied | Not implied | Not implied | Separate permitted purpose, rights, holdout and expiry checks |

Use capability functions, not scattered role string comparisons. The UI table is explanatory; the backend enforces every operation. Authorization failures may intentionally be not-found to avoid disclosure; preserve that distinction without leaking hidden resource existence.

## Financial and status boundaries

- CREDIT and PROVIDER_USD are separate units with exact decimal-string arithmetic. Never combine them in one total or convert implicitly.
- An inference job's execution state, usage report and financial settlement are separate. No success badge derived only from a ledger entry.
- A model/deployment record's state, engine readiness and publication approval are separate. No green status derived only from `active`.
- A dataset import being published is not a public model release.
- A teacher label is not human ground truth; evaluation acceptance is not universal quality certification.
- Result/trace access expiry is not a measured storage deletion time.

## Interaction adapter rules

Preserve session security and narrow ports while replacing product DB/RPC adapters with generated FastAPI clients. View models format returned states into copy/actions, but never invent live data, permissions or calculated scientific verdicts. Do not let a global `catch` convert every service error into `[]`.

Operations have identity and receipts. Preserve existing stable IDs for keys, experiments, exports, teacher batches and external training. Registration and dataset imports must be checked for their actual replay behavior before generic retry UI is added. An HTTP timeout is “Outcome not confirmed,” not “Nothing happened.”

For screenshot/interaction tests, fake adapters stay explicit and outside production. Existing `LAB_*_PREVIEW` protections remain; production failures must never fall back to sample records. The reference HTML's illustrative records cannot be copied into live default data.
