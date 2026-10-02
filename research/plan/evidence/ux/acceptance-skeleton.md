# UX-11 acceptance report — skeleton

Template for the batch-3 final acceptance (ux-verify-final, UX-11). Prepared by lane ux-verify (wave 7,
batch 2) at the base `b05eb6f4`. This directory is append-only: the final report is a new file,
`acceptance-<merged-sha7>.md`, filled from this skeleton; this file is never edited into a report.
Requirement: [07-handoff](../../../design/v1/07-handoff.md) (fixtures, UX-T01–T13, evidence per lane),
[06-implementation UX-11](../../../design/v1/06-implementation.md), [api-lifecycle verification](../../api-lifecycle/verification.md).

## How a run is produced

```sh
cd apps/app && pnpm install --frozen-lockfile && cd ../lab && pnpm install --frozen-lockfile && cd ../..
node apps/app/tests/ux/matrix/run-matrix.mjs --out research/plan/evidence/ux/matrix-<sha7>.json   # exit 0 PASS / 1 FAIL / 3 BLOCKED / 4 INVALID
```

The runner (`apps/app/tests/ux/matrix/run-matrix.mjs`, journeys and fixture register in `matrix.json`)
runs each journey's synthetic-harness suites with `node --test` in its app, on a clean tree only (a dirty
tree is INVALID). A part whose suite is not on the tree is BLOCKED naming its owner; no case run is
BLOCKED, never PASS. It starts no Docker, calls no hosted service and takes no screenshots; it never runs
the real E3A suite (`apps/app/tests/e2e`), whose capture policy stays off.

## 1. Run identity

| Field | Value |
|---|---|
| Base SHA / merged SHA (clean tree) | _ / _ |
| Node / pnpm / Next / Playwright Chromium | _ |
| Environment | synthetic fixtures (matrix) · real gates in §6 |
| Matrix command, exit, verdict | _ |

## 2. UX-T01–T13 matrix

Paste the runner's table for the merged SHA. A journey is accepted only when every part is PASS; a
BLOCKED part keeps its cause and owner here and in §5. Directory parts attribute every case in the
directory to the journey: narrow `matrix.json` with a `match` per journey before accepting it.

| Journey | Part | Status | Passed/failed/skipped | Cause |
|---|---|---|---|---|
| _ | _ | _ | _ | _ |

## 3. Four states per lane (06-implementation: tracked separately, never collapsed)

| Lane | UI implemented | Service integrated | Real workflow verified | Enabled |
|---|---|---|---|---|
| UX-00 foundations | _ | n/a (presentation) | _ | _ |
| UX-01 retention truth | _ | _ (live `/v1/models` wire) | _ | _ |
| UX-02 App navigation | _ | n/a | _ | _ |
| UX-03 Lab operate | _ | _ (AP-04/05/06, AP-09 09d) | _ | _ |
| UX-04 first call/docs | _ | _ | _ (T03 real) | _ |
| UX-05 Lab requests | _ | _ (AP-07) | _ | _ |
| UX-06 datasets | _ | _ | _ | _ |
| UX-07 usage/credits | _ | _ (AP-09 09a/09b) | _ | _ |
| UX-08 evaluations | _ | _ (SR-AP10-1, AP-10) | _ | _ |
| UX-09 review/training | _ | _ (AP-08, AP-10) | _ | _ |
| UX-10 releases | _ | _ (AP-06) | _ | _ |

## 4. Screenshot index

Rules: captured from the synthetic harnesses only (`apps/app/tests/ux/harness`, `apps/lab/tests/ux/harness`);
never from the E3A suite; synthetic names/IDs/content only (no password, API secret, signed media URL,
customer prompt, video or private trace); a screenshot is never the sole oracle. Each row names the source
of every metric or status the shot shows. None is captured in batch 2.

| Id | Journey | Route / screen | Fixture state | Viewport (320/390/768/1440/200%) | Theme | File | SHA | Source of each figure/status |
|---|---|---|---|---|---|---|---|---|
| _ | _ | _ | _ | _ | _ | _ | _ | _ |

## 5. Open-gap register (mapped to backend/UX ids)

Rows open at preparation (the matrix run at `d0c2ec0f`, lane evidence `../w7/ux-verify-*.md`); the final
report carries each forward with its state.

| Gap | Journeys | Owner (unblocks) | Ids |
|---|---|---|---|
| Real first call: one persisted key operation, real text request, charge/hold release, completion from the real read | T03 | api-frontends-app + coordinator window | AP-09 09a/09b, AP-03, AP-02 |
| Real bounded video clip through the async path | T04 | api-lifecycle-3 + coordinator window | AP-11 11e |
| Usage/result fixture journeys (read retry, expiry, bfcache); Settings privacy row; filtered-empty/lifecycle/unknown-accounting fixtures | T01, T04, T05 | ux-app-2 | UX-07, WR-UXF-4, WR-UXF-5 |
| Two-user isolation journey after the App cut-over | T06 | api-frontends-app + coordinator window | AP-09, AP-01, AP-03 |
| Lab shell, roles, registration wizard, control/aggregate split; lost membership, empty control, 768/1440 fixtures | T02, T07, T08, T09 | ux-lab-operate | UX-03, AP-09 09d |
| Server authorization on the merged SHA (`make lab-e2e`, key l4) | T07 | ux-verify-final | UX-11 |
| Engine readiness receipts distinct from recorded smoke; publication | T08 | api-hosting, api-publication | AP-05, AP-06 |
| Trace service unavailable; metadata-only/partial/expired/revoked content fixtures | T09 | ux-lab-requests | UX-05, AP-07 |
| Evaluations disabled/comparison fixtures; catalog 503 until 0066 | T09, T11 | ux-lab-evaluations, api-schema-2, api-improve-2 | UX-08, SR-AP10-1, AP-10 10c |
| Dataset journey; import partial/requeued fixtures | T10 | ux-lab-improve | UX-06 |
| Teacher/training safety (fixtures, then real) | T12 | ux-lab-improve, api-improve-2/3, operator | UX-09, AP-10 10c/10e, AP-08, P-10 |
| Stale release fence; rollout unit refusal fixture | T12 | ux-lab-releases | UX-10, AP-06 |
| Moderated usability session | T13 | ux-verify-final + operator | UX-11 |
| 200% zoom and reduced-motion checks | presentation | ux-verify-final | UX-11 |
| Published record has no display name/summary (the card heads with the canonical id) | T03 | API contract owner | AP-02/AP-00 (request from ux-app) |
| Snippet clipboard-failure path untested (needs a denied-clipboard browser fixture) | T03 | ux-verify-final | UX-04 |

## 6. Real suites on the merged SHA (run or BLOCKED with cause; never a skip read as a pass)

| Gate | Command | Result |
|---|---|---|
| App checks | `make console-test console-lint console-typecheck console-built console-mutants` | _ |
| Lab checks | `make lab-test lab-lint lab-typecheck lab-build lab-mutants` | _ |
| Lab real integration | `make lab-e2e` (LAB_E2E_REAL=1 LAB_E2E_BUILT=1, INFRX_D_TASK=l4) | _ |
| App E3A journey | `make app-e2e` (existing runner; capture policy unchanged) | _ |
| Consumer real-DB gates | `make console-pg` and the console-*-real gates touched | _ |
| API lifecycle (isolated) | `tests/integration/api_lifecycle` verdict | _ |

## 7. Manual accessibility pass (representative screens)

Tab order, focus return, contrast, screen-reader names, error recovery; screen, browser, result, tester.

| Screen | Check | Result | Notes |
|---|---|---|---|
| _ | _ | _ | _ |

## 8. Statements (separate, per 07-handoff)

UI readiness: _ · Hosted integration: _ · Launch status: _ (production acceptance is never claimed from a
build or a fixture run).

## Verification log

- 2026-10-02 (ux-verify, wave 7 batch 2): skeleton written; the matrix prepared and run at `d0c2ec0f`
  (BLOCKED: 6 parts PASS, 22 BLOCKED with owners, 0 FAIL; fixtures 29 covered, 12 gaps, 0 dangling).
