# ux-app-2 (UX-07) — consumer usage, credits and privacy — evidence at d21a14e

Lane ux-app-2 of wave 7 (LW7 batch 2), branch `codex/w7-ux-app-2`, base `b05eb6f4`, code head `d21a14ef` (this file and the update JSON are committed after it). No tasklocal key (fixtures only); no Docker, hosted Supabase, box, AWS, Vercel or secret touched. Presentation only: no new route, read, action or server-side call; every figure and state still comes from the existing view models over the existing reads (`billing/credit-reads.ts`, `usage/[requestId]/request-reads.ts`, `lib/services` — api-frontends-app's, untouched).

## Slices (in brief order; each: failing seam test first, then the change, then its mutants)

1. **WR-UXF-4 / WR-UXF-5** (applied first, as the coordinator asked). `request-view-model.ts` `resultNote`: available → "Readable until … After that the result is no longer available; this page keeps the request's details and charge."; expired → "The result stopped being available at …. Request status and usage remain available." `settings/view-model.ts` serving retention: "for limited periods, after which it can no longer be read". UXU-01/02.
2. **C-04 request detail + UX-T05.** New `usage/[requestId]/request-detail.tsx` (`RequestDetailView`): request ID + phase badge + status/model/submitted + failure + **Copy request ID** + "Status last checked HH:MM:SS UTC" (only while polling; the server's read time, re-rendered by each poll) → **Result** → **Usage and charge** → **Technical details** (request ID, serving revision, mode, retry guidance). `page.tsx` keeps the reads, header, back link and the poller mount (`{pollsFor(model) ? <StatusPoller /> : null}` unchanged). New `copy-button.tsx` + `copyText()` (a refused or missing clipboard says "Copying was blocked. Select the text and copy it by hand.", never "Copied"); result panel: "Copy result" (before Download), "Retry loading result" (the handler still only calls `readResult`). UXU-03 renders the six fixtures (ready, running, failed, expired, not found, unavailable) through the real component; UXU-04 is UX-T05 on the tested drivers (outage → Retry → ready → expiry timer → bfcache restore reads expired; four calls, all same-origin no-store GETs of the result route, no body, no method); UXU-05 clipboard.
3. **C-04 list.** Subtitle "Requests, results and credit charges."; requests first (new `requests-table.tsx`: Request · Started · Model/mode · Status (execution only) · Charge (settlement label + detail) · Charged (only when settled) · Held); compact `credit-summary.tsx` after the list (exact figures, the low/exhausted warning, CREDIT badge, "View credits"; a failed wallet read is "Credits unavailable" + its retry + the Credits link, never a figure); empty account "Your requests will appear here." + Set up a call (/models); any filter (window, model, key) "No requests match these filters." + Clear filters (/usage); failed list "We couldn’t load usage" + Try again; unknown state "Unknown status". Token counts moved off the list (detail keeps them). Loading skeleton in the same order. UXU-06/07/08 (UXU-08 renders the table: a 0.00000001 debit is "0.00000001 credits" under Charged; a hold is Held, never Charged; no status cell carries a money word; no row says "spent").
4. **C-05 Credits.** Figures "Available to use" (primary) / "Reserved for requests" / Spent / Balance (values and hints unchanged); grant "10,000 promotional credits, granted once after verification. Received <UTC>." or "… Not received yet."; ledger `signup_grant` → "Promotional credit grant"; every row carries its raw event `code` (shown under the label; unknown events stay "Other" with the code). UXU-09.
5. **C-03 list.** Empty list: "No keys yet. Create a key to call Marlin from your code." with Create key inside the card; the header action only once the list is not empty (or unavailable); a Status column (Active / Revoked with the revocation time as title). UXU-10 renders the real page through a stubbed `consumerSession` (ready empty, suspended empty, two keys). `create-key-dialog.tsx` untouched (UX-04's merged version).

Settings data-use controls: **not live** (see Open items) — the page keeps its read-only rows (trace capture Off, sharing/training Not offered, export/deletion by email), which is the honest state while `/console/v1/data-use` is off and absent from the generated client.

## Changed paths

Owned: `apps/app/app/(console)/usage/` (`page.tsx`, `loading.tsx`, `credit-view-model.ts`, new `credit-summary.tsx`, new `requests-table.tsx`, `[requestId]/page.tsx`, `[requestId]/request-view-model.ts`, `[requestId]/result-panel.tsx`, new `[requestId]/request-detail.tsx`, new `[requestId]/copy-button.tsx`), `billing/credit-view-model.ts`, `billing/page.tsx`, `settings/view-model.ts`, `api-keys/page.tsx`, `apps/app/tests/ux/usage/` (new: `usage.test.ts` 10 cases, `render.ts`, `run-mutants.mjs`, `mutants.json` 41).

Outside the owned list, test pins only (no behaviour): `tests/u/request-detail.test.ts` (WR-UXF-4 names it), `tests/u/usage-credits-view-model.test.ts` (3 empty-text regexes), `tests/u/credits-view-model.test.ts` (labels, grant regex, ledger label and key list), `tests/u/credit-pg.test.ts` (labels, grant regex; PG-gated, skipped here), `tests/e2e/journey.e2e.ts` (E3A, Docker-gated, not run: figure labels, the charge-state column index 2 → 4, the expired note). These files are api-frontends-app's (`apps/app/tests/**`); each edit is a one-line expectation change that merges independently of their rewrite.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `make console-test` (base b05eb6f4) | 0 | 692 tests, 633 pass, 0 fail, 59 skipped |
| `node --test tests/ux/usage/usage.test.ts` before slice 1 | 1 | red: 0 pass / 2 fail (assertion: "Kept until … the content is removed"; "then delete it") |
| same, before slice 2 | 1 | red: module load — `request-view-model.ts` does not provide `copyText` (UXU-03..05 absent) |
| same, before slice 3 | 1 | red: 5 pass / 3 fail (UXU-06/07/08) |
| same, before slice 4 | 1 | red: 8 pass / 1 fail (UXU-09) |
| same, before slice 5 | 1 | red: 9 pass / 1 fail (UXU-10: "No keys yet. Create one to start calling the API.") |
| `make console-test` (head) | 0 | 702 tests, 643 pass, 0 fail, 59 skipped (+10 UXU cases) |
| `make console-lint` | 0 | 0 errors, 2 warnings (both pre-existing: `lib/contracts/conformance.ts`, `lib/contracts/fake-services.ts`) |
| `make console-typecheck` | 0 | clean |
| `make console-built` | 0 | build OK; tests/i2a 22/22 pass |
| `node tests/ux/usage/run-mutants.mjs` | 0 | 2/2 self-checks; 41 mutants, 41 killed, 0 not killed; every UXU case declared (runner guard) |
| `make console-mutants` | 0 | contracts 212/212, u 217/217, c 195/195, a 47/47, catalog 46/46, feedback 21/21, ux 26/26, first-call 36/36 (0 survivors, 0 stale) |
| `node tests/u/run-mutants.mjs --only` (U4-M24/27/29/31/32/34/35, U1R-M13..18/20/32/33, U2-M23/29/34: the U mutants whose finds sit in files this lane edited) | 0 | 19/19 killed, before the full run |
| WR-UX07-1 composed: Makefile patched, `uv run --frozen pytest -q tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed (then reverted; the Makefile is the coordinator's) |
| `make api-lint` / `make api-typecheck` | n/a | no Python touched |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (915 links, 482 documents) |

## Wiring requests

- **WR-UX07-1 Makefile `console-mutants`** (coordinator): after `\tcd apps/app && node tests/ux/first-call/run-mutants.mjs` add `\tcd apps/app && node tests/ux/usage/run-mutants.mjs`. Composed test: `make console-mutants` exits 0 with the line (the runner: 2/2 self-checks, 41/41 killed, every UXU case declared); `tests/integration/test_makefile_mutant_lists.py` 7 passed with the patch.

## Schema requests

None.

## Open items

- **Data-use controls (C-07 / brief "live once the transport lands") — BLOCKED** on (a) `/console/v1/data-use` and `/console/v1/data-use/grants` being in the OpenAPI export and so in `packages/api-client/src/consumer.ts` (today the generated consumer client carries only `/v1/*` and health paths: the console routers are unmounted), and (b) api-frontends-app's App transport (`apps/app/lib/api/`) reaching Settings. Until both, Settings shows the existing read-only facts (no control that could look saved; `tests/u/keys-source.test.ts` T.settings still pins that). Building a hand-typed `DataUseDoc` here would be the second contract R271 forbids.
- Not done by design: duration in the list (no measured duration on `ConsumerJob`); a catalog-backed model picker (C-04 makes it optional).
- `tests/ux/usage/render.ts` and `run-mutants.mjs` copy UX-04's module hooks and runner (`ponytail:` notes): fold into one shared helper when the UX lanes merge.
- The UXU-10 page render stubs `@/lib/services/server`; if api-frontends-app moves `api-keys/page.tsx` to the client port, the stub specifier follows that import (one line).
- E3A (`tests/e2e/journey.e2e.ts`) expectations were updated textually but not run (Docker gate, coordinator's).

## Proposed ruling (unnumbered)

A consumer page's charge state and execution state render in separate cells/fields: a status cell never carries a settlement label and a Charged value appears only for a settled request (UXU-08 is the oracle).

## Estimate (remaining)

optimistic 0.5 h / likely 1.5 h / pessimistic 4 h, confidence medium — review round, WR-UX07-1 at merge, the test-pin overlap with api-frontends-app at merge; the data-use controls are a separate ~2–4 h once the generated client carries `/console/v1/data-use` (not in this estimate).
