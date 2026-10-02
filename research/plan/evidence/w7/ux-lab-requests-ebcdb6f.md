# UX-05 — Lab request list and evidence detail (lane ux-lab-requests, wave 7 batch 2)

- Base `b05eb6f4` (claude/consumer-v1, wave-7 batch-2 base); branch `codex/w7-ux-lab-requests`; code head `ebcdb6f2`.
- Brief: wave7-plan §5 batch 2 `ux-lab-requests` (UX-05), research/design/v1 03-lab.md L-05, 06-implementation.md UX-05,
  07-handoff.md UX-T09 + "Lab content". Presentation on AP-07c's merged projection (`lab_traces.py`: `access_state`,
  `elapsed_ms`, pins `price_version`/`request_schema_version`, server-side `serving_version_id`/`model_id` filters bound
  into the cursor as `<position>.<digest>`), on the frozen UX-00 primitives. Key: none (fixtures only). Node v22.23.1.

## Slices (commits)

| Slice | Commit | What |
|---|---|---|
| 1 red | `7ee1f5d9` | `tests/ux/requests/{render.ts,requests.test.ts}`: the real pages rendered to static HTML (TypeScript transpile + `@/` resolution; the guard, trace port, feedback and judge reads stubbed from a fixture world that records every read). Red on the base: **15 of 16 fail** (E01, the error boundary, already passed). |
| 2 | `aad876a3` | list + detail on `PageHeader`/`ServiceState`/`Badge`/`CopyButton`/`buttonClass`, a lane-local CSS module; `access_state` labels (never re-derived), loss reason as its own column, server `elapsed_ms`, readable UTC with `<time dateTime>`, pins; the route's filters from the URL (model link on each row, "Requests on this serving version" on the detail), next/first links carry filter + cursor, the cursor parser admits `.<hexdigest>`; a row outside the requested filter makes the page **unavailable** (never a list that looks filtered); content region: body only for `content`/`partial` on a granted record carrying text, as a React text child in `<pre>`, with provenance (organization, grant, size); `partial` marked; `metadata`/`revoked`/`expired`/`not_captured`/unknown each a sentence and no body; T09 states keep the heading, `unavailable` offers Try again on the same URL, feedback renders independently of the trace read. 16/16 green. |
| 3 | `8ac4562e` | superseded presentation pins (outside the owned list, see Deviations) moved to the UX05 suite. |
| 4 | `ebcdb6f2` | `tests/ux/requests/run-mutants.mjs` (shared Lab harness): 67 mutants, 67 killed; 16 cases all named. V2-X30 names V2-J01. |

## Commands (worktree root unless stated)

| Command | Exit | Result |
|---|---|---|
| `cd apps/lab && node --test tests/ux/requests/requests.test.ts` (at `7ee1f5d9`, before slice 2) | 1 | 16 tests, 1 pass, 15 fail (assertions, e.g. "Cannot read properties of undefined (reading 'label')", missing `pv-1`) |
| same, at `ebcdb6f2` | 0 | 16 pass |
| `cd apps/lab && pnpm test` (= `make lab-test`) base `b05eb6f4` | 0 | 286 tests, 272 pass, 0 fail, 14 skipped |
| same at `ebcdb6f2` | 0 | 289 tests, 275 pass, 0 fail, 14 skipped (−13 superseded tests/v cases, +16 UX05) |
| `make lab-typecheck` | 0 | clean |
| `make lab-lint` | 0 | clean |
| `make lab-build` | 0 | `/requests`, `/requests/[id]` dynamic |
| `node tests/ux/requests/run-mutants.mjs` | 0 | 16 cases, all named: true; 67 mutants, 67 killed, 0 not killed |
| `node tests/v/list/run-mutants.mjs` | 0 | 9 cases, all named: true; 46 mutants, 46 killed |
| `node tests/v/detail/run-mutants.mjs` | 0 | 5 cases, all named: true; 13 mutants, 13 killed |
| `node tests/v/judge/run-mutants.mjs` | 0 | 8 cases; 36 mutants, 36 killed (page pins unchanged) |
| `node tests/l/shared/run-mutants.mjs` | 0 | 3 cases; 9 mutants, 9 killed (TraceLossReason union kept verbatim) |
| `node tests/e2e/run-mutants.mjs` | 0 | harness list killed; stack list NOT RUN (needs `LAB_E2E_REAL=1 INFRX_D_TASK=l4`, Docker; UX lanes hold no key) |
| `make lab-mutants` (whole existing list, detached, at `ebcdb6f2`) | 0 | 14 runners, every one "all named: true", 0 not killed (106, 88, 21, 13, 36, 46, 72, 129, 220, 159, 45, 17, 9, 16 mutants); e2e stack list not run |
| `uv run --frozen pytest -q ../../tests/integration/test_makefile_mutant_lists.py` (from apps/infrx-api) | 1 | 7 failed: the new runner is not in `lab-mutants` yet (WR-UX05-2) |
| same with WR-UX05-2 applied (scratch worktree at `ebcdb6f2`) | 0 | 7 passed |
| With WR-UX05-1 applied (scratch worktree at `ebcdb6f2`): `pnpm test` / `pnpm lint` / `make lab-typecheck` / `node tests/v/list/run-mutants.mjs` | 0/0/0/0 | 290 tests, 276 pass, 0 fail, 14 skipped; lint clean; types clean; 10 cases, 54 mutants, 54 killed (X74–X81 new; V1M-X23 first survived — the unnamed-grant detail also lacked `content`, fixed in the patch's test) |

No Python touched: `make api-lint` / `make api-typecheck` not applicable. No screenshots taken (synthetic fixtures only; UX-11 owns the visual suite).

## Oracle mapping (brief → case)

- T09 partial services: UX05-L07 (list: heading + alert + Try again on the same filtered URL; denied/not_found their own states, never rows/empty), UX05-D06 (detail: heading + retry, feedback still renders when the trace read fails), UX05-E01 (error boundary).
- Metadata-only emits no content read: UX05-D01 (recorded reads are exactly detail → feedback → judge; a carried body is not rendered; a metadata record claiming `access_state: content` shows none). The adapter half (a metadata detail never keeps `content`) is V1M-A03 in WR-UX05-1.
- Expired/revoked content disappears: UX05-D02 (expired, revoked on metadata and on a granted record, not_captured with its loss reason; metadata kept, no body).
- Cross-workspace URL rejected: UX05-D05 (the read names the session workspace; not_found shows the not-projected copy, no metadata, no judge panel, a way back) and UX05-L01 (`?provider_org_id=` reported as ignored, never sent).
- Partial capture marked: UX05-D03. Content as text, never HTML: UX05-D04. No client-side filter pretending to search all traffic: UX05-L05 (filters only travel to the route; a mismatching row = unavailable).

## Deviations (paths outside the owned list)

The owned files are pinned by other tracks' tests/mutants (source-regex pins of the old markup, which forbade `@/components/ui`
— the very primitives the brief requires). Precedent: ux-app (UX-04) updated `apps/app/tests/a/examples.test.ts` the same way.
Changed in slice 3/4, each a test of the owned files only (no production code outside the owned list):

- deleted `apps/lab/tests/v/list/view.test.ts`, `tests/v/list/page.test.ts`, `tests/v/detail/page.test.ts`; `tests/v/detail/view.test.ts` drops V2-D01..D03 (their intents: UX05-L01..L07, UX05-D01..D07);
- `tests/v/list/run-mutants.mjs` (view/page mutants X45–X71 removed; query mutants X35–X44 re-pointed at the filter-aware `query.ts`), `tests/v/list/query.test.ts` (parsed params gain `filter`);
- `tests/v/detail/run-mutants.mjs` (X01–X15, X35–X42 removed; V2-X30 added for V2-J01), `tests/v/detail/journey.test.ts` (fixtures carry AP-07c fields);
- `tests/v/list/stack.test.ts` (LAB_V1M_REAL) and `tests/e2e/observe/stack.test.ts` + `tests/e2e/run-mutants.mjs` E2E-S01..S04/S06 (LAB_E2E_REAL, l4): expectations follow the `access_state` ladder (after the grantor revokes: "Access revoked", not "Metadata only"; a lost capture under a grant: "Not captured" + "Lost: <reason>"; granted content inline with provenance). **NOT RUN** — Docker keys `lab-v1m`/`l4` are not this lane's; the coordinator runs `make lab-e2e` and `LAB_V1M_REAL=1` after WR-UX05-1.

## Wiring requests

1. **WR-UX05-1 (merge-blocking)** — `research/plan/evidence/w7/ux-lab-requests-ebcdb6f-WR-UX05-1.patch` (applies cleanly on `ebcdb6f2`): `apps/lab/lib/services/traces/port.ts` keeps AP-07c's `price_version` (string), `request_schema_version` (number), `access_state` (string), `elapsed_ms` (number|null) on every item, a granted **detail's** inline `content` (string|null; a list row and a metadata item never keep it), and `list(actor, cursor, filter?)` sends each set filter by name; `tests/v/list/adapter.test.ts` fixtures + V1M-A05; `tests/v/list/run-mutants.mjs` V1M-X74..X81. Without it the live adapter drops `access_state` (every row "Unknown", no body) and ignores the filter (the UX05-L05 guard turns a filtered list unavailable rather than lying). Composed test: `cd apps/lab && pnpm test` (290/276/0/14) and `node tests/v/list/run-mutants.mjs` (54/54) on the patched tree; then `make lab-e2e` on l4.
2. **WR-UX05-2** — `Makefile` `lab-mutants`: append `	cd apps/lab && node tests/ux/requests/run-mutants.mjs` (`ux-lab-requests-ebcdb6f-WR-UX05-2.patch`). Composed test: `tests/integration/test_makefile_mutant_lists.py` 7 passed (red without it); `make lab-mutants` prints "16 cases, all named: true; 67 mutants, 67 killed".

## Open items

- The `(provider)/layout.tsx` (UX-03) owns `lab-page`; these pages use `lab-stack` only, so they sit inside whatever shell UX-03 lands.
- Viewport/keyboard checks at 320/390/768/1440 were not run in a browser (render-to-markup only); the table scrolls inside its own frame (`overflow-x: auto`), identifiers use `lab-id`. UX-11's matrix should add the request pages.
- Hosted trace composition (AP-07 composed proof) remains the gate before calling the feature operational (06-implementation UX-05 exit).
- The content body is rendered whole inside a 32rem scroll box (ceiling: no paging of very large captures).
- Proposed ruling (unnumbered): "The Lab presents trace content access only from the route's `access_state`; a list filtered by the route refuses (unavailable) a page that contains a row outside the filter."

## Estimate (remaining: review + merge)

optimistic 1 h, likely 2 h, pessimistic 4 h; confidence medium; basis: all slices green with fixtures; remaining = apply WR-UX05-1/2, the l4/lab-v1m stack runs the lane could not do, review findings.
