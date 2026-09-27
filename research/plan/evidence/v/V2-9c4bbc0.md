# V2: trace detail, content and feedback (lane trace-ui, wave LW4), fakes plus real C3F

- Base `2252e5a0`, V2 code `9c4bbc0c`, branch `codex/w5-trace-ui`, worktree `.claude/worktrees/codex-w5-trace-ui`.
- Tasklocal key `lab-v1m` (PG 57513): **not used**. The one real dependency on the base is C3F's door, and its stack (`apps/app/tests/c/feedback/stack.py`) is hard-bound to key `app-c3f` (it asserts port 57509 and uses fixed `infrx-app-c3f-*` names). Running it would mean using another lane's key, so the real-door cases are filed as WR-V2-3 for the coordinator's `make console-c3f-real`. No container was created.
- Status: **review (fake-backed)**. Integration is not claimed. Per 13:9, fakes never count: the C2 and trace-read halves wait on WR-V2-1 and WR-V2-2.

## What was built (owned paths only)

| path | what |
|---|---|
| `apps/lab/app/(provider)/requests/[id]/page.tsx` | Guard first. The actor is the session workspace. The trace read and C3F `reviewRequestFeedback(id)` run in parallel, and the feedback panel renders outside the trace branch (FEEDBACK-ACK: the durable record shows while the projection lags). Content is read only for `?content=1`. |
| `…/[id]/loading.tsx` | loading state (`role="status"`) |
| `apps/lab/components/traces/detail/port.ts` | `TraceReadPort` is shaped by lab-api's `GET /lab/v1/traces/{id}` (codex/w5-lab-api f36a6ab4), including its `access: metadata/content` split and the granted-only fields, plus `grant_ref`. `ContentPort` is shaped by C2's refs (codex/w5-content ae7375e7). `tracePorts()` returns **unavailable** until wired, whatever the environment says. |
| `…/detail/fake.ts` | The stand-in, which enforces the rules: developer+ only, another provider's request or any unknown id is not_found, a forged or revoked grant is forbidden, an expired ref or T3 retention is expired. |
| `…/detail/view.ts` | Named metadata rows only; an unknown record field is never rendered. Content states: not captured, lost, metadata only, expired or tombstoned, revoked, unavailable, plus the "truncated at capture" flag. The not-found copy is the same for another provider's request and for one not projected yet (TRACE-TENANT). Feedback rows carry their stored provenance (`author_role · channel`), with a fixed note: customer feedback is never a calibration label. C3F refusals are shown as fixed copy. |
| `…/detail/panels.tsx` | Plain semantic elements. `<a href>` is the only control (keyboard). Content renders as text in a `<pre>` with `pre-wrap`/`anywhere` (mobile). Never `dangerouslySetInnerHTML`, `onClick`, `tabIndex` or tables. |
| `apps/lab/tests/v/detail/{view,journey,page}.test.ts` | 12 cases, V2-D01…D07, J01…J03, P01…P02 |
| `apps/lab/tests/v/detail/feedback-postgrest.test.ts` | V2-R01/R02, run against the REAL C3F door (skip without `INFRX_C3F_STACK`; WR-V2-3). Outside the mutant suite, like C3F's own real test. |
| `apps/lab/tests/v/detail/run-mutants.mjs` | 41 mutants on the shared harness (`tests/l/shell/harness.mjs`) |

No change was made to `(provider)/{overview,models,deployments,settings}`, `lib/services/{control,review}`, `lib/auth`, the layout or nav, the App, `packages/shared`, the Makefile, lockfiles or any SQL (`git diff 2252e5a0 -- apps/app packages Makefile apps/lab/lib apps/lab/pnpm-lock.yaml` is empty).

## Failing seam first

`node --test tests/v/detail/view.test.ts` was run before any implementation: **exit 1**, `ERR_MODULE_NOT_FOUND … components/traces/detail/port.ts`. Every case passed after the implementation. One failed-then-passed regression: mutant V2-X24 first survived because node's truncated diff printed a bare `...` line, which ends the harness's YAML block before `code: 'ERR_ASSERTION'`. The assertion was split so the comment text is checked on its own, and V2-X24 is now killed.

## Checks

| cmd | exit | result |
|---|---|---|
| `node --test tests/v/detail/view.test.ts` (before implementation) | 1 | module not found (the seam) |
| `node tests/v/detail/run-mutants.mjs` @ 9c4bbc0 | 0 | 12 cases, all named: true; 41 mutants, 41 killed, 0 not killed |
| `make lab-test` @ V2 tree (before feedback-postgrest) | 0 | tests 67, pass 65, fail 0, skipped 2 (C3F-P03/P04, no stack) |
| `make lab-lint` / `make lab-typecheck` / `make lab-build` @ V2 tree | 0 / 0 / 0 | clean / clean / `ƒ /requests/[id]` built |
| V2-R01/R02 (real C3F door) | — | **NOT RUN**: the stack is bound to key app-c3f (WR-V2-3) |

The final run on the V3 head (V2 + V3 together) is in `V3-<head7>.md`.

## Wiring / interface requests

- **WR-V2-1 (lab-api lane → coordinator)**: add the Lab TS adapter for `TraceReadPort` (owned path `apps/lab/lib/services/traces/`, as V1M proposed) over `GET /lab/v1/traces/{id}?provider_org_id=`, with the forwarded Supabase access token. Wire it into `components/traces/detail/port.ts` `tracePorts()` (this lane's file; send it back here or apply it at merge). Also add `grant_ref` to `lab_traces.py` `GRANTED` (the id of the current provider_sharing grant the row was authorized under), so a C2 ref can be bound to it. Map the status codes: 404 → not_found, 403 → denied, anything else → unavailable. Test: V2-D07 flips to the adapter; a stack case on `lab-v1m` (PG 57513) with the pinned ClickHouse, where provider A reads its own request, provider B gets not_found, and a metadata-only row carries no org, size or content.
- **WR-V2-2 (content lane → coordinator)**: `ContentPort.read(actor, grantRef, requestId)` = `issueContentRef(purpose provider_sharing)`, then a read of the content by handle through the platform's content service (a Lab-reachable read, e.g. `GET /lab/v1/content/{handle}`; C2 has only the issue side on the Lab today). Refusal mapping: not_found/forbidden → the page's `revoked`, `result_expired` → `expired`, anything else → `unavailable`. Test: a forged grant ref, a revoked grant and a lapsed ref each yield no text (V2-J03 on the real adapter).
- **WR-V2-3 (coordinator)**: in `apps/app/tests/c/feedback/stack.py` `adapters()`, add `(LAB, "tests/v/detail/feedback-postgrest.test.ts")` to the list, then run `make console-c3f-real`. V2-R01/R02 must pass: developer sees `customer · api` and `customer · console`, never the operator label; revoked job_2 → the not-shared copy; viewer → the role copy.
- **WR-V2-4 (coordinator)**: in the `Makefile` `lab-mutants` target, add the line `cd apps/lab && node tests/v/detail/run-mutants.mjs && node tests/v/judge/run-mutants.mjs`.
- **WR-V2-5 (V1M, when it lands)**: link each list row to `/requests/${request_id}`. No nav entry is needed for a detail page.

## Ruling proposal (unnumbered)

A Lab request page reads the trace projection, C3F feedback and J2 judge runs independently. Feedback, a durable PG record behind its own grant, shows even when the projection has no row yet. Judge scores show only beside a request the trace read returns, so a T3-deleted request hides its derived scores too.

## Open issues

- There is no dev preview for V2/V3: nothing in the Lab creates trace rows, so a preview would need per-provider seeding config. Until WR-V2-1/2 and WR-V3-1 are wired, the page shows the unavailable copy.
- Provider feedback **submission** is not in V2, because C3F exposes no provider submit (review only). Operator calibration labels belong to C3L/J3 and are not built here.
- Media refs (image/video) are not rendered, only text. Add them when C2 serves media refs.

## Estimate (remaining for V2)

Optimistic 1 h / likely 2 h / pessimistic 4 h after WR-V2-1/2/3 land, confidence medium. Basis: two thin adapters wired into one seam each, the real stack cases rerun, one verify round (brief 2/4/8; about 3 h spent).

## Audit log

- 2026-09-27: written at 9c4bbc0c (lane trace-ui, LW4).
