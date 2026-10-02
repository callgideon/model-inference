# UX-11 final acceptance — run at e28ee95

Lane ux-verify-final (wave 7, batch 3). Filled from the skeleton
[`research/plan/evidence/ux/acceptance-skeleton.md`](../../../plan/evidence/ux/acceptance-skeleton.md).
Requirement: [07-handoff](../07-handoff.md), [06-implementation UX-11](../06-implementation.md),
[api-lifecycle verification](../../../plan/api-lifecycle/verification.md). This is a local acceptance of
the merged tree: it claims nothing about hosted integration or launch (§8).

## 1. Run identity

| Field | Value |
|---|---|
| Base SHA / run SHA (clean tree) | `49114933` (merges #94–#97) / `e28ee95c` (base + this lane's runner, matrix, a11y suites; no product file changed) |
| Node / pnpm / Next / Playwright Chromium | 22.23.1 / 9.15.9 / 16.3.5 (App and Lab) / 1.63.0 headless |
| Environment | synthetic fixtures (matrix default) + real-route Lab stacks on task-local keys `l4` (PG 57503), `lab-v1m` (PG 57513) and `t2i` (ClickHouse 57540/57541, `infrx-t2i-clickhouse`, the pinned image of `tests/integration/compose.yaml`, started for the run and removed after) |
| Matrix command, exit, verdict | `node apps/app/tests/ux/matrix/run-matrix.mjs --real --out …/matrix-e28ee95.json` → exit 1, **FAIL** (one reported product defect, WR-UXVF-3); 36 parts: 26 PASS, 1 FAIL, 9 BLOCKED; journeys 5 PASS, 1 FAIL, 7 BLOCKED; fixtures 40 covered, 1 gap, 0 dangling ([`matrix-e28ee95.json`](matrix-e28ee95.json)) |

The t2i ClickHouse for the V1M part (task-local literals, as `tests/v/list/backend.py` expects):
`docker run -d --name infrx-t2i-clickhouse -e CLICKHOUSE_DB=infrx_t2i -e CLICKHOUSE_USER=infrx_t2i -e
CLICKHOUSE_PASSWORD=infrx-t2i-local -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 -p 127.0.0.1:57540:8123 -p
127.0.0.1:57541:9000 --ulimit nofile=262144:262144 clickhouse/clickhouse-server@sha256:87e0a5b7…`
(the digest of `tests/integration/compose.yaml`); `docker rm -f infrx-t2i-clickhouse` after.

Without `--real` (`make ux-matrix`) the seven real parts read BLOCKED "real gate not requested: rerun
with --real (needs …)"; the run above is the one with them.

## 2. UX-T01–T13 matrix

A journey is accepted only when every part is PASS. Directory parts are narrowed to their journey's cases
(`match` in `apps/app/tests/ux/matrix/matrix.json`). Real parts run the Lab's own real-route suites
(lab-e2e journeys, the V1M stack, N4-J01) on the keys above, one file at a time.

| Journey | Part | Status | Passed/failed/skipped | Cause |
|---|---|---|---|---|
| T01 truthful retention | Models/Docs wire-to-view (app) | PASS | 7/0/0 |  |
| T01 truthful retention | Settings privacy row (WR-UXF-5) (app) | PASS | 2/0/0 |  |
| T02 mobile keyboard | App drawer (app) | PASS | 6/0/0 |  |
| T02 mobile keyboard | Lab dialog/drawer primitives (lab) | PASS | 2/0/0 |  |
| T02 mobile keyboard | Lab provider shell (lab) | PASS | 2/0/0 |  |
| T02 mobile keyboard | App accessibility pass: 200% zoom, focus stops, names, AA contrast, reduced motion (shell) (app) | PASS | 4/0/0 |  |
| T02 mobile keyboard | App dialog and menu popups under reduced motion (app) | FAIL | 0/1/0 | 1 failed, exit 0; FAIL[WR-UXVF-3]: no prefers-reduced-motion rule in app/globals.css |
| T02 mobile keyboard | Lab accessibility pass: 200% zoom, focus stops, overlays, names, AA contrast, reduced motion (lab) | PASS | 4/0/0 |  |
| T03 first call | fixture guide, snippets, fold (app) | PASS | 25/0/0 |  |
| T03 first call | real persisted key, request, charge (-) | BLOCKED | -/-/- | the real variant (one persisted key operation, a real text request, the charge/hold release, guide completion from the real read) runs in the E3A app-e2e gate on the e4b block (not this lane's key) or the operator's bounded window; AP-09 09a/09b (the thin App) is on the tree with CONSOLE_READS/CONSOLE_ACTIONS_API off (unblocks: coordinator (make app-e2e on e4b; production window)) |
| T04 finite video | fixture async/video examples (app) | PASS | 6/0/0 |  |
| T04 finite video | fixture result states (app) | PASS | 2/0/0 |  |
| T04 finite video | real bounded clip (-) | BLOCKED | -/-/- | a rights-cleared bounded clip through the real async path (job, bounded poll, result before expiry, ledger reconcile) runs only in the operator's bounded window, never during E4C (unblocks: api-lifecycle-3 (AP-11 11e hosted plan) + coordinator window) |
| T05 read retry/expiry | fixture read retry, expiry, bfcache (app) | PASS | 2/0/0 |  |
| T06 consumer isolation | real two-user journey (-) | BLOCKED | -/-/- | user A's request/key deep link as user B, A's key revoked, refresh: the two-user real consumer journey (review 26 §4 Step C) needs the App cut over to the API in an environment with two verified accounts (unblocks: coordinator window (AP-09 on the tree, switches off)) |
| T07 Lab roles | fixture roles through direct routes (lab) | PASS | 11/0/0 |  |
| T07 Lab roles | server authorization on the real routes (lab-e2e) (lab) | PASS | 4/0/0 |  |
| T07 Lab roles | workspace isolation on the real trace route (V1M stack) (lab) | PASS | 3/0/0 |  |
| T08 registration | fixture wizard, errors, receipt, proposal (lab) | PASS | 23/0/0 |  |
| T08 registration | engine readiness receipts (-) | BLOCKED | -/-/- | readiness distinct from a recorded smoke needs a candidate engine: AP-05's deployments API is on the tree (LAB_HOSTING off) and its real smoke (05d) needs a GPU window on the box - BLOCKED[CANDIDATE-ENGINE] (unblocks: api-hosting (AP-05 05d) + coordinator GPU window) |
| T09 partial services | Lab service-state primitive (lab) | PASS | 1/0/0 |  |
| T09 partial services | control/aggregate split (lab) | PASS | 5/0/0 |  |
| T09 partial services | trace service unavailable (lab) | PASS | 3/0/0 |  |
| T09 partial services | evaluations disabled (lab) | PASS | 3/0/0 |  |
| T09 partial services | partial services on the real routes (lab-e2e) (lab) | PASS | 3/0/0 |  |
| T10 dataset | fixture mapping → import → derive → export (lab) | PASS | 13/0/0 |  |
| T10 dataset | labels import and train-only export on the real routes (lab-e2e) (lab) | PASS | 2/0/0 |  |
| T10 dataset | import interrupted and resumed, splits, export, revocation (N4-J01) (lab) | PASS | 1/0/0 |  |
| T11 comparison | fixture frozen suite → inconclusive report (lab) | PASS | 14/0/0 |  |
| T11 comparison | launch, cancel and compare on the real routes (lab-e2e E02-E05) (lab) | BLOCKED | 0/0/4 | no case ran (4 skipped: NOT RUN[SR-AP10-1]: the composed catalog answers 503 - 0066's lab_eval_catalog has no caller in infrx/lab/evaluation (WR-UXVF-1); owner api-improve-3 (AP-10: the Catalog port over 0066's lab_eval_catalog, WR-UXVF-1) + the lab-e2e journey composition (WR-UXVF-2)) |
| T11 comparison | judge runs on the request page (register row 98) (-) | BLOCKED | -/-/- | lib/services/judge/runs.ts answers unavailable until row 98 maps GET /lab/v1/traces/{request_id}/judge-runs through the generated client; re-point this part at its suite when it merges (unblocks: lab-judge-runs (row 98, UX-08/AP-09 09c follow-up)) |
| T12 improvement safety | fixture teacher dry-run, ambiguous training (lab) | PASS | 7/0/0 |  |
| T12 improvement safety | stale release fence, release/optimization evidence (fixture) (lab) | BLOCKED | -/-/- | ux-lab-releases (UX-10) has not merged tests/ux/releases |
| T12 improvement safety | review, bundle, fence and rollback on the real routes (lab-e2e) (lab) | PASS | 6/0/0 |  |
| T12 improvement safety | real teacher/training (-) | BLOCKED | -/-/- | no duplicate paid submit and ambiguous reconciliation against a live teacher/judge need P-10 (provider, credentials by SSM name, budget, payer) and AP-10 10e's external-training path (unblocks: api-improve-3 (AP-10 10e) + operator P-10) |
| T13 usability | moderated session (-) | BLOCKED | -/-/- | representative users complete setup/recovery tasks without coaching (completion, assistance, errors, time recorded); not automatable and never claimed from expert inspection (unblocks: operator-recruited participants (UX-11 protocol in the acceptance report)) |

Journey verdicts: **PASS** T01 truthful retention, T05 read retry/expiry, T07 Lab roles, T09 partial
services, T10 dataset · **FAIL** T02 (the App's dialog/menu popups ignore reduced motion: WR-UXVF-3) ·
**BLOCKED** T03, T04, T06 (real consumer variants: e4b gate / operator window), T08 (candidate engine),
T11 (catalog not carried: WR-UXVF-1/2; row 98), T12 (UX-10 in flight; P-10), T13 (participants).

Re-running after the in-flight lanes merge: `ux-lab-releases` (UX-10) unblocks its T12 part by itself when
`apps/lab/tests/ux/releases` lands; `lab-judge-runs` (row 98) needs its part re-pointed from `blocked` to
its suite path + `match` (one line in `matrix.json`). Then `node apps/app/tests/ux/matrix/run-matrix.mjs
--real` with `infrx-t2i-clickhouse` running (the V1M part says BLOCKED, naming the port, if it is not).

## 3. Four states per lane (06-implementation: tracked separately, never collapsed)

"Real workflow verified" here means a real-route local suite on a task-local key, not production.

| Lane | UI implemented | Service integrated | Real workflow verified | Enabled |
|---|---|---|---|---|
| UX-00 foundations | yes (merged) | n/a (presentation) | a11y pass PASS on the Lab primitives (UXV-L01..L04) | n/a |
| UX-01 retention truth | yes | live `/v1/models` wire parsed (T01 PASS) | fixtures only | as deployed App (`8b63eb36`); not re-verified here |
| UX-02 App navigation | yes | n/a | keyboard/200%/names PASS (UXN, UXV-A02..A04); popups' reduced motion FAIL (WR-UXVF-3) | as deployed |
| UX-03 Lab operate | yes | AP-09 09c/09d, AP-04/05/06 routes on the tree | lab-e2e roles PASS (T07); engine readiness BLOCKED[CANDIDATE-ENGINE] | no (Lab switches off) |
| UX-04 first call/docs | yes | AP-09 09a/09b on the tree, CONSOLE_* off | BLOCKED (T03 real: e4b gate / window) | no |
| UX-05 Lab requests | yes | AP-07 traces route | V1M stack PASS on lab-v1m + t2i (T07); T09 trace-unavailable PASS | no (LAB_TRACES off) |
| UX-06 datasets | yes | datasets route (0051) | lab-e2e I02/I04 + N4-J01 PASS (T10) | no |
| UX-07 usage/credits | yes | AP-09 09a/09b, CONSOLE_READS off | fixtures only (T04/T05 PASS); real BLOCKED | no |
| UX-08 evaluations | yes | experiments/ledger carried; catalog not (WR-UXVF-1) | E01 PASS; E02–E05 NOT RUN[SR-AP10-1]; judge runs BLOCKED[lab-judge-runs] | no |
| UX-09 review/training | yes | AP-08/AP-10 routes | lab-e2e I03/I05 PASS (T12); live teacher BLOCKED[P-10] | no |
| UX-10 releases | in flight (ux-lab-releases) | AP-06 on the tree | lab-e2e R02–R05 (rollout routes) PASS; the UX-10 screens BLOCKED[ux-lab-releases] | no |

## 4. Screenshot index

Captured from the synthetic harnesses only (`UXV_SCREENS=<dir> node --test tests/ux/matrix/a11y.check.ts`
in each app); never from E3A; synthetic names/ids only. A screenshot is never the oracle: each row's
statuses come from the named case. The round "N" badge bottom-left is `next dev`'s own overlay (a
development tool, absent from a production build; the Tab walks stop at it).

| Id | Journey | Route / screen | Fixture state | Viewport | Theme | File | SHA | Source of each status |
|---|---|---|---|---|---|---|---|---|
| S01 | T02 | App shell `/usage` | long email, operator | 640×400 (200%) | dark | [app-shell-200pct.png](screens-uxvf/app-shell-200pct.png) | e28ee95 | UXV-A02 (no overflow, Tab stops) |
| S02 | T02 | App navigation menu open | same | 640×400 (200%) | dark | [app-menu-200pct.png](screens-uxvf/app-menu-200pct.png) | e28ee95 | UXV-A02 |
| S03 | T02 | App shell | same | 1440×900 | dark | [app-shell-1440.png](screens-uxvf/app-shell-1440.png) | e28ee95 | UXV-A03 (names, AA) |
| S04 | T02 | App navigation menu open | same | 390×844 | dark | [app-menu-390.png](screens-uxvf/app-menu-390.png) | e28ee95 | UXV-A03; balance "10,000" is the fixture's literal |
| S05 | T02/T09 | Lab primitives (every service state, field error, long id) | UX-00 harness | 640×400 (200%) | dark | [lab-primitives-200pct.png](screens-uxvf/lab-primitives-200pct.png) | e28ee95 | UXV-L01 |
| S06 | T02/T09 | Lab primitives | UX-00 harness | 1440×900 | dark | [lab-primitives-1440.png](screens-uxvf/lab-primitives-1440.png) | e28ee95 | UXV-L03 |
| S07 | T02 | Lab dialog open | UX-00 harness | 1440×900 | dark | [lab-synthetic-dialog-1440.png](screens-uxvf/lab-synthetic-dialog-1440.png) | e28ee95 | UXV-L03 |
| S08 | T02 | Lab drawer open | UX-00 harness | 1440×900 | dark | [lab-synthetic-drawer-1440.png](screens-uxvf/lab-synthetic-drawer-1440.png) | e28ee95 | UXV-L03 |
| S09 | T10 | Lab import wizard | UX-06 harness | 640×400 (200%) | dark | [lab-wizard-200pct.png](screens-uxvf/lab-wizard-200pct.png) | e28ee95 | UXV-L01 |
| S10 | T10 | Lab import wizard | UX-06 harness | 1440×900 | dark | [lab-wizard-1440.png](screens-uxvf/lab-wizard-1440.png) | e28ee95 | UXV-L03 |

The audit's live screenshots stay in [`../evidence/`](../evidence/) (they predate the UX lanes).

## 5. Open-gap register (mapped to backend/UX ids)

| Gap | Journeys | Owner (unblocks) | Ids | State at e28ee95 |
|---|---|---|---|---|
| The App's dialog/select/dropdown popups zoom/slide under prefers-reduced-motion (no reduced-motion rule in `app/globals.css`) | T02 | coordinator → UX-00's file | WR-UXVF-3 | **FAIL** (UXV-A05, a TODO FAIL[WR-UXVF-3] case; passes with the patch) |
| Evaluation catalog not carried: 0066's `infrx.lab_eval_catalog` has no caller; `Catalog.catalog` still raises SR-AP10-1's 503 | T11 | api-improve-3 (`infrx/lab/evaluation/`) | WR-UXVF-1, SR-AP10-1 | BLOCKED; with the patch applied transiently `world.composed.catalog` became true |
| The evaluate e2e journey composes the route suite's fakes over ports the unit carries; E01 anchored to the 503 | T11 | lab-e2e harness follow-up (owner by the coordinator) | WR-UXVF-2 | guard in place: E02–E05 stay NOT RUN until it lands |
| Judge runs on the request page | T11 | lab-judge-runs | row 98 | BLOCKED (in flight) |
| Release/optimization screens; rollout unit refusal fixture | T12 | ux-lab-releases | UX-10, AP-06 | BLOCKED (in flight) |
| Real first call (one key operation, real request, charge/hold release, completion from the real read) | T03 | coordinator: `make app-e2e` (e4b) / window | AP-09 09a/09b, AP-03, AP-02 | BLOCKED |
| Real bounded clip through the async path | T04 | api-lifecycle-3 (11e plan) + window | AP-11 11e | BLOCKED |
| Two-user isolation after the App cut-over | T06 | coordinator window | AP-09, AP-01, AP-03 | BLOCKED |
| Engine readiness distinct from a recorded smoke | T08 | api-hosting 05d + GPU window | AP-05 | BLOCKED[CANDIDATE-ENGINE] |
| Live teacher/judge; external training | T12 | api-improve-3 + operator | AP-10 10e, AP-08, P-10 | BLOCKED |
| Moderated usability session | T13 | operator-recruited participants | UX-11 | BLOCKED (protocol §7) |
| Published record has no display name/summary | T03 | API contract owner | AP-02/AP-00 | carried from the skeleton |
| Snippet clipboard-failure path (a denied-clipboard browser fixture for the first-call snippets) | T03 | UX-04 owner | UX-04 | open, carried (UXU-05 covers Usage's copy buttons, not the snippets) |
| 200% zoom, reduced motion | presentation | ux-verify-final | UX-11 | closed for the harness screens (UXV-A02/L01, UXV-A04/L04); open for the App popups (WR-UXVF-3) |

## 6. Real suites on the run SHA (run or BLOCKED with cause; never a skip read as a pass)

| Gate | Command | Result |
|---|---|---|
| App checks | `make console-test console-lint console-typecheck`; App UX runners | console-test exit 0 (589 tests: 587 pass, 0 fail, 2 skipped); lint exit 0 (0 errors, 2 pre-existing warnings); typecheck exit 0; `node tests/ux/matrix/run-mutants.mjs` 27/27 killed, 10 cases named; `node tests/ux/run-mutants.mjs --only UXM-X01..X16` 16/16 killed. `console-built`/full `console-mutants`: NOT RUN here (no App product file changed) |
| Lab checks | `make lab-test lab-lint lab-typecheck lab-build` | lab-test exit 0 (365: 355 pass, 0 fail, 10 skipped); lint 0; typecheck 0; `make lab-build` exit 0; `node tests/ux/matrix/run-mutants.mjs` 7/7 killed |
| Lab real integration | `INFRX_D_TASK=l4 make lab-e2e` | exit 0: 4/4 suites, 24 tests, 20 pass, 0 fail, 0 skipped, 4 todo (E02–E05 NOT RUN[SR-AP10-1]); harness mutants 17/17; stack mutants `--only E2E-S13..S17`: 1 killed (S13 by E01), 4 not run (their cases NOT RUN) |
| Lab V1M stack | `LAB_V1M_REAL=1 INFRX_D_TASK=lab-v1m node --test tests/v/list/stack.test.ts` (t2i ClickHouse) | exit 0: 6/6 pass (merge #92 had left it BLOCKED on ClickHouse) |
| Lab N4 journey | `LAB_N_REAL=1 INFRX_D_TASK=l4 node --test tests/n/journey.test.ts` (matrix part) | PASS 1/1 |
| App E3A journey | `make app-e2e` | BLOCKED: the e4b block is not this lane's key (coordinator); capture policy untouched |
| Consumer real-DB gates | `make console-pg`, `console-*-real` | NOT RUN: not this lane's keys; no App product file changed |
| API lifecycle (isolated) | `make api-lifecycle` | BLOCKED[api-lifecycle-3]: the base's isolated verdict is FAIL on stages 02/14 (3e3d9929), owned and in flight in api-lifecycle-3 |

## 7. Accessibility pass (representative screens)

Automated in a real Chromium on the synthetic harnesses (`a11y.check.ts` in each app, run by the matrix
and the mutant runners; the probe's own failure oracle is UXV-A01, which plants one defect per check). It
does not replace a screen reader or a person: the manual rows below say what was not done.

| Screen | Check | Result | Notes |
|---|---|---|---|
| App shell, menu (390/640/1440) | 200% zoom reflow (640×400 CSS px), Tab order: every stop at least partly in view, not covered, with an outline or ring; menu opens/closes by keyboard, focus returns | PASS (UXV-A02) | WCAG 1.4.10, 2.4.7, 2.4.11 |
| App shell, menu | every control named; text ≥ 4.5:1 (3:1 large) against its composited background | PASS (UXV-A03) | 1.4.3, 4.1.2 |
| App menu | reduced motion: open/close moves nothing | PASS (UXV-A04) | the drawer has no animation |
| App dialog, dropdown/select popups (committed classes) | reduced motion | **FAIL** (UXV-A05): `enter` 100 ms with transform (zoom-in-95 / slide-in-from-top-2) | WR-UXVF-3 |
| Lab primitives, import wizard | 200% zoom reflow + Tab stops in view/uncovered/indicated | PASS (UXV-L01) | the import spec textarea is taller than a 200% viewport: judged on its visible part |
| Lab dialog, drawer | open by keyboard at 200%, fit, Escape returns focus to the trigger | PASS (UXV-L02) | |
| Lab primitives, wizard, dialog, drawer | names; AA contrast (disabled controls exempt) | PASS (UXV-L03) | |
| Lab primitives, dialog, drawer | reduced motion: spinner still, overlays move nothing | PASS (UXV-L04) | |
| Production pages (Usage, Credits, Keys, Lab Overview/Models/Requests…) | the same checks in a browser | NOT RUN | the harnesses render the shell and primitives; the pages are covered by the lanes' static-render suites (headings, labels, states), not by a browser a11y pass |
| Screen-reader names/announcements with real AT; error recovery by a person | manual | NOT RUN | needs a human tester with NVDA/VoiceOver; the automated name check is a proxy |

Usability protocol for T13 (operator-recruited, about 5 participants per product, no coaching): invited
consumer — verify email, create a key, make the first text request, find its usage and result, recover a
lost password; Lab developer — register a revision, read a request's access state, import a labels file
and fix a mapping error. Record completion, assistance, errors and time per task; fix observed blockers
before claiming T13.

## 8. Statements (separate, per 07-handoff)

UI readiness: the merged UX-00–UX-09 screens pass their fixture journeys and the real-route Lab journeys
on task-local keys (T01, T05, T07, T09, T10), with one open presentation defect (WR-UXVF-3) and UX-10 in
flight. · Hosted integration: not established here — the deployed App is `8b63eb36` (before AP-09's thin
App), the thin App on the tree needs CONSOLE_READS/CONSOLE_ACTIONS_API with their DSN and cursor secret
and the R151 window for 0060+, hosted schema is at 0059 and the Lab switches are off; T03/T04/T06 real
variants are the coordinator's. · Launch status: unchanged — production acceptance pending, public signup closed (STATUS.md);
nothing in this run is a production acceptance.

## Verification log

- 2026-10-02 (ux-verify-final, wave 7 batch 3): matrix at `e28ee95c` with `--real`, clean tree: FAIL
  (WR-UXVF-3), 26 PASS / 1 FAIL / 9 BLOCKED parts; lab-e2e exit 0 (20 pass, 4 NOT RUN); V1M stack 6/6.
