# Verification and fresh-session implementation prompt

**Read the later [API lifecycle handoff](../../plan/api-lifecycle/verification.md) first.** Its API ownership, first-model import/deployment prerequisites and real-service gates supersede earlier adapter assumptions here. Retain this UI acceptance matrix, but never implement direct frontend product DB/RPC adapters or offer first-model onboarding through the old revision-only Register form.

Use [implementation lanes](06-implementation.md) and [service contracts](05-service-contracts.md). Verification must test what users see and what the backend persisted. Fixtures are useful for visual states; they do not certify live inference, permissions, payment or quality.

## Required fixtures

| Dimension | Cases |
|---|---|
| App identity | Signed out, pending verification, verified/no grant yet, verified/granted, auth unavailable |
| App activity | No keys, one key, revoked key, no requests, filtered-empty, running/succeeded/failed/expired, unknown accounting, exhausted wallet, wallet unavailable |
| Lab role | No membership, viewer, developer, administrator, multiple workspaces, lost membership |
| Lab control | Empty, registered private revision, smoke none/pass/fail without engine provenance, pending/rejected/approved publication, retired deployment, partial service failure |
| Lab content | Metadata-only, shared complete, shared partial, expired, revoked, cross-workspace not-found |
| Improvement | Import invalid/partial/published/requeued; leakage refusal; evaluation accept/reject/inconclusive; teacher dry-run/approved/ambiguous; external training ambiguous; rollout stale fence/unit refusal |
| Presentation | 320px reflow, 390px mobile, 768px tablet, 1440px desktop, 200% zoom, long names/IDs, reduced motion, keyboard-only |

Use synthetic names/IDs/content for screenshots. No saved password, API secret, signed media URL, customer prompt, actual video or private trace in artifacts. Visual suite has its own fixture harness; do not enable screenshots on the production-like E3A suite, whose capture policy is intentionally off.

## Acceptance journeys

| Test | Steps | Observable pass condition |
|---|---|---|
| UX-T01 truthful retention | Load catalog wire fixture with null/absent/positive/zero/invalid bound → Models/Docs/Settings | Unknown cannot render 0 hours; positive only under accepted semantics; malformed response fails safely; other TTLs remain correct |
| UX-T02 mobile keyboard | Closed drawer → Tab; open → cycle focus → Escape; navigate; resize | No offscreen focus; background inert when modal; Escape returns focus; route change closes; desktop nav usable |
| UX-T03 first call | Verified account → model → key creation → local text request → Usage → result | Exactly one persisted key operation; valid supported request; owned result; correct charge/hold release; guide completion from real evidence |
| UX-T04 finite video | Follow upload or URL guide with rights-cleared bounded clip → async request → poll → result | Actual returned job, bounded polling honors retry hints, result saved before expiry, tokens/ledger reconcile; output quality evaluated separately |
| UX-T05 read retry/expiry | Running detail → transient read failure → Retry → success → expiry/bfcache | No new inference submitted; content clears at expiry/lost access; status/usage remain; no physical-purge claim |
| UX-T06 consumer isolation | User A request/key deep link as B; revoke A key; refresh | No cross-account result or secret; expected refusal and persisted revocation behavior; no fake successful read |
| UX-T07 Lab roles | No-member/viewer/developer/admin through same direct routes/actions | Server authorization matches capabilities; workspace derived from session; no customer content from role alone |
| UX-T08 registration | Invalid fields → valid registration → smoke → proposal | Errors preserve inputs; actual revision receipt; recorded smoke distinct from engine readiness; administrator proposes only |
| UX-T09 partial services | Control succeeds/aggregate fails; trace service unavailable; eval disabled | Successful sections preserved; clear heading and next action; unavailable never zero/empty/healthy |
| UX-T10 dataset | Mapping preview → edit → preview invalidates → import → rejects → derive → export | Current mapping used; precise split validation; lineage and omissions retained; leakage/rights enforcement holds |
| UX-T11 comparison | Frozen suite → declare protocol → launch/retry → inspect inconclusive report | Same experiment identity; missing cases/intervals/slices visible; no re-derived quality verdict; budgets/units intact |
| UX-T12 improvement safety | Teacher dry-run → approve/resume; external training uncertain; stale release fence | No send on dry-run; no duplicate paid submit; ambiguous state reconciled; stale release requires review |
| UX-T13 usability | Representative user completes setup/recovery tasks without coaching | Record completion, assistance, errors and time; fix observed blockers; do not claim success based only on expert inspection |

UX-T03/04/06 production variants require the existing bounded operating window, credit/request budget and test data. Do not start uncontrolled inference/load testing while E4C or another operator task is running. Backend production certification is outside a synthetic UX pass.

## Commands and environments

Use Node ≥22.18 and the pinned pnpm version. Inspect current readmes and supported environment first. Existing targets:

```sh
pnpm --dir apps/app install --frozen-lockfile
pnpm --dir apps/lab install --frozen-lockfile
make console-test console-lint console-typecheck
make lab-test lab-lint lab-typecheck
make console-built lab-build
```

Run focused tests during lane work, then the affected suites on the combined SHA. If API schemas/parsers change, run the matching Python/TS conformance cases and API checks; do not run unrelated full GPU suites for a CSS-only change. Existing type baseline and visible skipped checks must not be rewritten as full passes.

On the supported real-service/Docker host, use the documented gates in [integration README](../../../tests/integration/README.md) and [environment](../../../tests/integration/ENVIRONMENT.md), including `make lab-e2e` for changed Lab integration and the E3A App journey through its existing runner. Do not invoke its Playwright config with invented environment values; target validation intentionally restricts it. Add a separate synthetic UX browser suite for screenshots/focus tests, using the project's supported browser setup.

The initial desktop review could not rerun Docker/GPU gates on the Mac. Any unavailable prerequisite is BLOCKED/NOT RUN with reason, never PASS. Browser accessibility checks complement automated assertions; test tab order, focus return, contrast, screen-reader names and error recovery manually on representative screens.

## Evidence required per lane

- Base and tested merged SHA, runtime/browser versions, fixture or real environment, exact command and result, skipped checks with reason.
- Before/after screenshot(s) with viewport and state, captured from rendered UI using synthetic data; screenshot is not the sole oracle.
- At least one complete normal journey and its relevant failure/permission/expiry path.
- For mutations: stable operation ID, authoritative receipt/persisted outcome and confirmation that retry did not duplicate work. Redact secrets by not capturing them in the first place.
- Remaining dependencies, service enablement status and owner; no fabricated ETA. Update the existing tracker, not a second competing progress page.

## Ready-to-copy prompt

Copy the following into the implementation session. It is intentionally model/tool neutral.

```text
Implement the Consumer App and Provider Lab UX plan in research/design/v1.

First fetch the latest main without losing any current work. Read STATUS.md,
CLAUDE.md, research/platforms/README.md, the launch review linked from STATUS,
and research/design/v1/README.md. Read research/plan/api-lifecycle/README.md and
its contracts/implementation/verification documents: this later requirement
supersedes earlier adapter assumptions. Both apps must be thin FastAPI clients;
no frontend product database/RPC logic. First-model onboarding needs the new
import/deployment APIs; the old Register form only revises an imported model.
Coordinate with AP-00–11. Then read all seven numbered design documents
and open research/design/v1/design-reference.html in a browser. The reference
is a proposed visual/interaction specification with illustrative data, not
implemented product behavior. Reconcile any changes since the reviewed source
795f1e7b/upstream 6462ed06 before editing. Do not repeat completed backend waves.

Your goal is a coherent, intuitive Lab and an easier first-call Consumer App,
while preserving the existing authorization, credits, idempotency, retention,
service ports and production launch gates. Follow the detailed screen/state
specifications and existing App visual language. Keep apps/app and apps/lab
separate. Do not import consumer business code into Lab or invent a shared
permission model. Do not hard-code the reference's illustrative records.

Start with UX-00, then prioritize UX-01 (the live null-retention/zero-hour
deletion claim) and UX-02 (mobile drawer focus/Escape). Run UX-03 Lab operations
and UX-04 consumer first-call/docs in parallel when ownership allows, then
UX-07 consumer usage/credits after UX-01. Implement the remaining Lab lanes
in the plan, keeping individual capabilities disabled until their real service,
rights and accounting gates pass. Missing integrations must have honest,
usable unavailable states; styling them is not backend completion.

Use the maximum safe parallel agents supported by your environment, with
isolated worktrees and one owner per file. On a four-agent session keep one
coordinator and three independent implementation agents. Follow the ownership
and dependency map in 06-implementation.md. The foundation owner controls
package/lockfile changes; the coordinator owns composition conflicts, tracker,
docs and integration. Register UX lane IDs in the existing task/tracker system
with real states/evidence, not a duplicate tracker or guessed progress.

Preserve the current implementation's strengths: one-time API secrets held only
in memory, stable mutation IDs, exact CREDIT/PROVIDER_USD separation, no-store
result reads and expiry/bfcache clearing, server-derived provider workspaces,
separate content grants, immutable evaluation inputs and operator publication
approval. A model registration/active record is not a working endpoint, generic
smoke success is not real-engine qualification, and teacher labels are not
human ground truth. No fake telemetry, autoscaler controls, public signup,
payments, native video streaming or arbitrary-model hosting claims.

For any new field or control, check 05-service-contracts.md against the actual
port/backend. Implement its named dependency or use the specified fallback.
Optional backend enhancements must not block the consumer pilot. Do not add
database migrations without the repository's existing allocation/reproof rules.
Do not enable currently inert Lab workers or change production flags as part
of a visual implementation. Production work belongs in the existing authorized
operating window with its own bounds and acceptance evidence.

Validate each lane in a real browser at mobile/tablet/desktop sizes and with
keyboard navigation. Execute 07-handoff.md's role, failure, expiry and normal
journeys. Use synthetic data in a separate visual test harness. Do not enable
screenshots/traces in the real E3A suite or capture keys/customer content.
Run relevant tests/builds on the merged SHA; run supported-host real integration
gates for changed integrations. A skipped/blocked test is not a pass.

Persist until the specified work is implemented and verified, or record a
concrete external blocker with the affected lane and safe fallback. Fix review
findings before handoff. Keep concise progress updates. Finish with the merged
commit(s), implemented screens, test/browser evidence, remaining backend gates,
and separate statements of UI readiness, hosted integration and launch status.
Do not mark production accepted because the UI builds or the mockup looks good.
```
