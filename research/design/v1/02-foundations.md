# Design foundations and navigation

This is a proposed implementation specification. [Visual reference](design-reference.html) demonstrates the chosen direction; [service mapping](05-service-contracts.md) constrains what it can claim.

## Product shape

**Consumer App:** choose a model → get a key → call it from code → inspect status/result/charge. **Provider Lab:** register a revision → understand deployment evidence → observe authorized requests → create an evaluation dataset → compare a change → request a controlled release.

Keep separate products, account/workspace context and credentials. A link between App and Lab may be shown with descriptive copy, but never implies shared membership. Lab uses provider workspace selection; App uses the individual account and its central CREDIT balance. Do not add a fake organization switcher to the consumer app.

### Navigation

| Surface | Main navigation | Secondary |
|---|---|---|
| App | Models, API keys, Usage, Credits | Docs, Settings, account menu; authorized Operator area stays separate |
| Lab / Operate | Overview, Models, Deployments, Requests | Workspace selector at top, Settings/account below |
| Lab / Improve | Datasets, Evaluations, Review, Training, Releases | Optimization comparisons nested under Releases; Judge setup nested under Evaluations |

Improve is a collapsible group, collapsed for a new workspace until an enabled improvement capability or an explicit visit makes it relevant. Capability-disabled links remain discoverable in the group with “Setup required” and lead to an explanatory read-only page. Do not leave inaccessible disabled anchors in keyboard order. Role-restricted operations are explained locally; permission is still enforced on the server.

Keep existing URLs. “Review” labels `/annotations`; Judge remains `/judge`; Optimizations remains `/optimizations`, reached from Releases. Deep links to disabled features still resolve with the shell, heading, reason and next action. New detail routes require workspace-scoped server reads; route existence is not permission.

## Visual system

Reuse the semantic variables from `apps/app/app/globals.css`. Dark is the v1 default. No brand redesign, glow, decorative charts or background imagery. Reference colors below approximate the existing neutral family; implementation should use the existing OKLCH values rather than create competing hard-coded values.

| Token | Design rule |
|---|---|
| Canvas / sidebar / surface | Near #0a0a0a / #171717 / #171717; inset code/form region near #101010 |
| Text / secondary | Near #fafafa / #a3a3a3; do not use reduced opacity on entire disabled panels |
| Border | Existing white/10% semantic border; one border layer per section |
| Primary | Near-white solid button, dark text; one dominant action per page/step |
| Success | Muted green label + words; only proven outcomes |
| Warning | Amber label + words: awaiting approval, incomplete evidence, expired soon |
| Error | Red label + clear cause/action; never communicate by color alone |
| Focus | Visible 2px ring plus offset, distinguishable against every surface |
| Radius | Existing 10px base; 8px controls, 12–16px content containers |
| Spacing | 4px base; 8/12px related controls, 16/24px groups, 32px page sections |
| Type | Geist Sans for UI; Geist Mono for IDs/code. Body 14–16px, secondary 13px, h1 26–28px/semibold, h2 18px |
| Numbers | Tabular figures; exact accounting strings formatted without float arithmetic |

Do not extract App auth or business components into Lab. `@infrx/shared` is currently Lab contract code; it is **not a UI library**. For v1, Lab gets its own scoped stylesheet and primitives using the same documented tokens, and the existing App stays on Tailwind/Base UI. Adopt Base UI and Lucide in Lab only through the foundation owner's reviewed package/lockfile change. A later neutral UI package can replace duplication after both surfaces stabilize; this is not a launch prerequisite.

### Shell and responsive rules

- Desktop ≥1024px: sidebar 232–240px; page padding 32px; content max 1280px except dense data tables. Top strip identifies product/workspace and current role; page header has breadcrumb, title, one-line purpose and primary action.
- Tablet 768–1023px: reduced 24px padding; details stack when a column becomes narrower than 320px; no fixed-width inspector cutting off the task.
- Mobile <768px: 52px header with labeled menu trigger; modal navigation drawer; 16px page padding; full-width primary actions. Lists become stacked records where practical. Wide comparison tables have a labeled horizontal scroll region, not whole-page overflow.
- Forms max 720px, auth max 400px. Desktop reference includes a 280–320px explanatory summary beside complex forms; mobile puts it before the final submit.
- IDs may truncate in tables with an accessible full value/copy action; never truncate a field's validation message. Code scrolls inside its own labeled region and never widens the page.
- Sticky headers/footers must not cover focused elements. At 200% zoom, primary tasks remain operable. Test 390px, 768px and 1440px, plus 320px reflow and long content.

## Reusable component contracts

| Component | Inputs and required behavior |
|---|---|
| ProductShell | product, authorized navigation, workspace/account context; skip link, landmarks, `aria-current="page"`, responsive modal drawer |
| PageHeader | breadcrumb, h1, purpose, action slot; retained in loading, empty and failure states |
| ServiceState | explicit loading / empty / unavailable / denied / not-found / stale; title, safe explanation, recovery action and optional diagnostic ID |
| StatusBadge | domain-specific label + icon/word; `active` mapping differs by domain; never global “active = healthy” |
| Metric | value or unavailable, unit, observation window, sample count where applicable; no invented zero or percentage |
| RecordList | actual supported filters, cursor navigation, row links, accessible headings; pagination resets when filters change |
| ReferenceField | readable label + immutable ID; catalog selector when supported; manual reference fallback with validation when no catalog exists |
| FormStep | labels, descriptions, field errors, error summary, pending state, preserved non-secret inputs; Back never submits |
| OperationReceipt | submitted operation ID, current state, retry/resume semantics and detail link; successful request acceptance is not successful execution |
| CopyButton | copy payload, accessible label, success only after clipboard resolves, error with selectable text fallback |
| ContentBoundary | server-provided access state; never requests or renders content until permitted; clears on expiry/revocation/workspace change |

A transport failure is not an empty collection. Preserve independently successful sections when one service fails. Do not show stale success with a fresh timestamp. If stale reads are allowed, label last successful observation and prohibit mutations based on stale readiness.

### Feedback and forms

Field validation appears after interaction or submit, not immediately on untouched fields. On failure focus the error summary; link each item to the invalid field. Do not reset valid entries. Async operations show their actual state; do not fabricate percentage complete or ETA. Cancel means a server cancellation request and must not be represented as an immediate terminal outcome.

Keep operation identity stable across lost response/retry. New form session or “Start a new run” deliberately creates a new identity. No automatic mutation retry after a permission failure. For an uncertain submission, look up the original operation before allowing another submit.

Dialogs follow the [WAI modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/): enter with meaningful focus, contain keyboard focus, support Escape where safe, make background inert and restore focus to the trigger. For an unsaved one-time key, dismissal must clearly explain secret loss without trapping users permanently. Hidden mobile navigation is unmounted or inert; moving it offscreen is insufficient.

Nonblocking operation feedback is announced without stealing focus, following [WCAG status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html). Use `role=status` for progress/success and an appropriate alert for errors; don't announce every polling tick. Product target: 44px touch hit areas; WCAG 2.2 AA's [minimum target-size criterion](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) is 24px with specified exceptions, not 44px.

## Honest states and copy

| Condition | Preferred copy / interaction |
|---|---|
| No models | “Register your first model revision” + explanation that registration does not start a serving engine |
| No measured traffic | “No measured requests in this window” + window; no 0% error badge |
| Service down | “We couldn’t load requests” / “Try again”; configuration-needed only if a trusted server capability state says so |
| No provider workspace | “This account has no Lab workspace” / link to consumer App and contact administrator guidance |
| Metadata only | “Request content isn’t shared with this workspace” while still showing permitted metadata |
| Expired result | “This result is no longer available. Request status and usage remain available.” |
| Missing physical-deletion bound | “The result access window does not specify a physical-deletion deadline.” No zero-hour promise |
| Registered deployment record | “Registered” + “Serving readiness has not been verified” if readiness proof absent |
| Evaluation without sufficient cases | “Inconclusive” + missing evidence, never “failed quality” or “0% accuracy” |

## Performance and continuity

Keep server rendering for initial data/auth, small client islands for forms/tabs/polling, and skeletons matched to the final layout. No global loading gate across independent services. URL state holds non-sensitive filters and cursors; never API keys, signed content URLs or prompts. Preserve filter context when returning from a record. Respect reduced motion; 120–180ms transitions only for navigation/feedback, no continuous animation beyond real pending indicators.

The design reference is an illustrated specification, not a production component library or test double for backend acceptance.
