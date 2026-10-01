# Parallel implementation plan

**Backend dependency amendment:** apply [AP-00–11](../../plan/api-lifecycle/implementation.md) before closing these lanes. UX-03 first-model onboarding requires import/deployment APIs; its legacy four-field form only revises imported models. Consumer actions require durable key/grant APIs; trace/judge/review require AP-07/08. Presentation work can use committed typed clients, but direct product DB/RPC fallbacks and mocked service success cannot satisfy completion.

Status of every UX lane in this document: **proposed, not started by this design task**. Existing implementation is the baseline, not work to rewrite. UX IDs are a new design-work namespace; the coordinator should register them in the existing task/tracker system without changing historical backend task verdicts.

## Coordination and merge order

Start from the latest main containing this design package; compare source/runtime changes since `795f1e7b` and reconcile [STATUS](../../../STATUS.md). Commit a path-ownership map before dispatch. Use `codex/ux-<lane>` worktrees; one owner per file and per app lockfile. Each lane records base SHA, integration SHA, affected routes, contract changes, command evidence and screenshots of synthetic fixtures. Do not estimate completion from lines of code or mark screenshot completion as service acceptance.

UX-00 freezes tokens, primitives and fixture-state names. Then parallelize:

```text
UX-00 foundations
 ├─ UX-01 truth/copy ────────────────┐
 ├─ UX-02 App navigation            ├─ UX-07 usage/credits (after UX-01)
 ├─ UX-03 Lab operate               │
 ├─ UX-04 App first call/docs       │
 ├─ UX-05 Lab requests              │
 ├─ UX-06 Lab datasets              │
 ├─ UX-08 Lab evaluations           │
 ├─ UX-09 Lab review/training       │
 └─ UX-10 Lab releases              │
                                   └─ UX-11 integration and verification
```

UX-11 may prepare fixtures and the verification matrix while feature lanes run; final acceptance waits for merged code. Backend gate closures run under their existing owners in parallel, not hidden inside UI lanes. On a four-agent implementation session use one coordinator and up to three independent lane agents, rotating completed lanes. More agents only help where their files, dependencies and test resources are independent.

Merge UX-01/02 first, then UX-04/07 for the consumer pilot. Lab L1 (UX-03/05) can merge independently without delaying it. UX-06/08/09/10 stay capability-gated until their real backends are ready. The full design is not a new all-or-nothing launch gate.

## UX-00 — Foundations and shared decisions

Own: Lab `app/layout.tsx`, new scoped stylesheet, `components/ui/*`; App `app/globals.css` only for agreed additive tokens; app package/lockfile changes; test fixture scaffold/config. Do not change business ports, routes or permissions. Coordinator owns root docs/manifest updates.

Deliver: semantic tokens matching App; Lab font loading; Button, Field, Badge, PageHeader, ServiceState and accessible drawer/dialog contracts. Existing App primitives reused in place. Decide explicit fixture entry points isolated from production. Supply primitive examples for loading/error/denied/empty/long IDs. No cross-import from `apps/app` into Lab; `@infrx/shared` remains contract-only.

Exit: independent builds, no global CSS leaks, keyboard primitives verified at desktop/mobile, no production fake fallback; frozen component interface note consumed by all lanes. Foundation changes are small enough that downstream lanes can adopt one commit.

## UX-01 — Truthful retention, expiry and retry copy (P1)

Own: App `app/(console)/docs/content.ts`, affected published-model parser/adapter and contract tests, expiry copy in `app/(console)/usage/[requestId]/result-panel.tsx` **before UX-07 begins editing it**. Backend retention serialization edits require a named API owner and reproof, not uncoordinated schema change.

Tasks: reproduce wire null → zero-hour message; identify actual catalog ingestion path; represent unknown safely; distinguish result readability from physical deletion; audit pricing/idempotency/retry claims for their scope and time bounds. Update exact tests and shared fixtures together. UX-04 owns example-generation behavior/layout; give it required retry-copy corrections.

Exit: absent, null, zero, positive and malformed retention values have intentional behavior. A live-shaped payload produces accurate Docs and Settings copy without breaking catalog availability. Unknown bound cannot produce an immediate deletion guarantee. Result read expiry clears the result and says unavailable, not physically purged. Avoid a test limited to calling the formatter with a cast value: exercise the wire-to-view boundary too.

## UX-02 — Consumer navigation and mobile access (P1)

Own: App `components/sidebar.tsx`, shell/accessibility tests; composition layout only after coordinator allocates it. UX-00 owns global tokens.

Tasks: nav order from foundations; active-page semantics; skip link; correct internal Docs icon; mobile drawer via existing accessible primitive; hidden nav removed from focus order, background inert while open, Escape and focus restoration, route-change close. Preserve account menu, balance failure and authorized operator entry.

Exit: reproductions in audit C9 fail before patch and pass afterward. At 390px, keyboard never reaches offscreen links while closed, cannot escape an open modal into page controls and Escape returns focus to trigger. At desktop nav remains persistent. Long email and unavailable balance fit without overflow.

## UX-03 — Lab shell, access and model/deployment operations

Own: Lab provider layout; `lib/auth/sign-in-form.tsx` presentation; pages Overview/Models/Deployments/Settings and their lane-local components; control view models/action responses. Do not rewrite auth policy or generic HTTP infrastructure. Coordinate CX-01/02 with the backend owner.

Tasks: L-01–06 except Requests; grouped sidebar, workspace selector, role context; per-section loading/errors; setup checklist from API records; Add model import/deployment flow once AP-04/05 exist, with the four-field legacy registration restricted to imported models; deployment expansion and proposal review; honest smoke/readiness terminology. Deep detail route only with scoped API read; existing list expansion is sufficient initially.

Exit: role fixture matrix through server actions; registration validation and uncertain response path; success remains Registered; passed generic smoke cannot enable a real-engine Ready badge; publication submission cannot claim public acceptance; missing service never becomes a zero metric. Existing shell no-App-import conformance still passes.

## UX-04 — Consumer model setup and documentation

Own: App Models page, `components/snippet.tsx`, Docs page/layout and example generator/tests, lane-local quickstart components. UX-01 owns `docs/content.ts` until merged; avoid concurrent edits. Key dialog behavior changes belong here only if coordinated with its existing owner; no independent key API rewrite.

Tasks: compact model summary + three-step quickstart; environment-variable snippets; text connectivity call vs finite-video guide; visible input substitutions; conditional model availability; contents/anchor navigation; bounded async examples using actual retry hints and terminal outcomes. Reuse secure key dialog with safe return state. Preserve code languages and contract-derived limits.

Exit: a brand-new fixture account finds Create key above fold at 1280×720; an existing-key user is not forced to create another. Copied examples never insert public prefixes as secrets. Video placeholder is clearly incomplete; a provided safe input produces correct escaped code. Docs anchors work from Settings. No new UI inference endpoint or browser secret persistence introduced.

## UX-05 — Lab request list and evidence detail

Own: Lab `/requests` pages, `components/traces/list`, `components/traces/detail` presentation; trace-specific view model and tests. Other lanes must not edit shared judge/review actions through this lane. Generic service fixes stay with backend owners.

Tasks: readable metadata list, cursor navigation, independent states, request detail hierarchy, explicit access/completeness labels, read-only content boundary and existing action affordances. Keep actual available fields; defer global filters until CX-03. Distinguish denied, not-found and unavailable without leaking hidden records.

Exit: metadata-only fixture emits no content read; expired/revoked content disappears; direct cross-workspace URL rejected; partial capture marked; unavailable service preserves shell/heading and retry. Hosted trace-composition gate remains separate and must pass before calling the feature operational.

## UX-06 — Dataset workflow

Own: Lab `/datasets` routes/forms and dataset-specific UI/view helpers/tests. Existing dataset port behavior changes require explicit ownership; no changes to unrelated pipelines.

Tasks: L-07 library/details; source/mapping/preview/import steps around real schema; reject review; status/receipt and requeue; exact split percentages with stable IDs; lineage and omission-aware export. Preserve advanced mapping for schema features not yet rendered. CX-04 additions only where needed and independently testable.

Exit: valid import, malformed JSONL, partial acceptance, preview invalidation, lost response, failed/requeued import, split overlap and expired export all exercised. Holdout/training restrictions survive guided UI. Test large allowed input within existing limits and bounded progress, without loading all production data into a client table.

## UX-07 — Consumer usage, credits and privacy

Depends: UX-01 merged. Own: App Usage routes and result/status presentation, Billing, Settings, key-list empty state/reason label mapping. Coordinate any key dialog edit with UX-04. Preserve business service implementation unless a specific test identifies a gap.

Tasks: requests-first usage hierarchy, meaningful empty states, financial/execution distinction, result focus/expiry/error states, readable exact credit ledger, privacy copy aligned with UX-01. Preserve bounded polling and content lifecycle.

Exit: ready/running/failed/expired/not-found/unavailable result fixtures; expiry while tab open and back navigation; no sensitive browser persistence; wallet failure never zero; tiny nonzero debit not falsely “free”; no double charge/new inference from result Retry. Existing accounting/query conformance remains green.

## UX-08 — Evaluation comparison interface

Own: Lab `/evaluations`, `/judge` presentation, evaluation-specific components/view models/tests. Freeze shared reference picker in UX-00 or use lane-local wrapper; no race with pipeline forms.

Tasks: L-08 comparison wizard and evidence-first report; runs/subscriptions separated; judge settings discovered from Evaluations; readable reference selectors; explicit expert protocol and budgets; actual backend state/cancel semantics.

Exit: accept/reject/inconclusive, insufficient/missing/error cases, mixed units, changed immutable refs, duplicate launch and cancel race; no inferred scientific decision in frontend; no seeded quality thresholds as silent defaults. Listings and evaluation double-debit gate must be closed before worker enablement; unavailable UI can ship first.

## UX-09 — Review, teacher batches and external training

Own: Lab `/annotations`, `/training`, pipeline/review UI helpers/tests. Coordinate shared judge content rendering with UX-05/08 by fixed exported interfaces. Do not edit release port or dataset import forms.

Tasks: L-09/10 queue-detail-review, lineage and methods, teacher dry-run/approval, manual external-training bundle/checkpoint flow, ambiguous outcomes. Use current raw reference fallbacks when selectors lack catalogs; no fake teammate or model list.

Exit: synthetic label never silently human; no content access without grants; no holdout leakage; teacher dry-run sends nothing; approval/retry does not duplicate paid work; training ambiguous cannot resubmit; unknown PROVIDER_USD cost remains unknown. Real pipeline listings/permissions/worker gates required for activation.

## UX-10 — Release and optimization evidence

Own: Lab `/releases`, `/optimizations`, rollout-specific view helpers/tests.

Tasks: L-11 summary/detail states, scoped comparison evidence, proposal summary with server-bound fence, missing identity and non-comparable data labels. No new autoscaler or traffic mutation UI.

Exit: stale-fence re-review, viewer/developer/admin distinction, unit refusal, null progress, inconclusive comparison and mismatched hardware; approved proposal never substitutes for current serving proof. Existing rollout/operator gates preserved.

## UX-11 — Integration, accessibility and evidence

Own: dedicated synthetic-browser test suite/config, cross-app integration checks, screenshot index, acceptance report, root docs and tracker reconciliation via coordinator. Do not edit feature files concurrently with their owners; report defects and assign fixes.

Deliver: role/service/content-state fixtures; browser tasks in [handoff](07-handoff.md); screenshots at relevant breakpoints; source of each metric/status; test logs; open-gap register mapped to existing backend IDs. Synthetic visual evidence must be isolated from the real E3A suite, which intentionally disables screenshots/traces to avoid capturing secrets/content.

Exit: merged SHA passes relevant local checks; supported-host real suites run or explicitly blocked with cause; invited consumer journey has operational evidence in its authorized window. Public signup and Lab worker enablement retain separate decisions. No task marked done only because its branch tests pass before integration.

## Change management and risks

- Stable contracts first: foundational components, label/state mapping and package changes merge once, then parallel features consume them.
- Don't overwrite implementation in progress. Fetch main before dispatch and before integration; reconcile changed contracts, preserved migrations and current operator windows.
- Upgrade neither Next nor the engine merely to implement this design. Add dependencies only through UX-00, with both builds checked.
- UI tables need bounded query semantics. Adding client search does not authorize unbounded reads.
- Styles can ship while service capability is off; a styled error is not feature completion. Track **UI implemented / service integrated / real workflow verified / enabled** separately.
- Avoid duplicate tracking: import UX lanes into the existing task system when execution starts; this document remains the immutable design baseline plus explicit amendments.
