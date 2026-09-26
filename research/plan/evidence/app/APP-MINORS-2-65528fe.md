# APP-MINORS-2: C0 WR-6, U1R WR-6, A2 WR-A2-3, E3A-RUN S-5/RV-3, AM1-L2/L3 (evidence)

- Lane APP-MINORS-2, branch `codex/app-minors-2`, worktree `.claude/worktrees/codex-app-minors-2`.
- Base `b967a033`. Code head `65528fef`. Commits: `bfe1b31d` (C0 WR-6 + U1R WR-6), `6de9fb17` (WR-A2-3), `0dee38f6` (RV-3), `20760ec1` (AM1-L3), `65528fef` (F-BASE discovery follow-up). This file and `updates/APP-MINORS-2-20260926T1859Z.json` are committed on top.
- Nothing hosted was touched: no Supabase project, Vercel, pilot box, AWS or SSM. Docker ran only as `app-c0` (55451) and `app-u1r`/`app-u4` (55457/55456), and the harnesses removed their containers on exit. The contracts under `lib/contracts/` are untouched. Nothing was pushed.

## Items

| Item | Disposition |
|---|---|
| C0 WR-6: the first-membership pick in `lib/session.ts` | **Fixed** |
| U1R WR-6: one getUser per console render | **Fixed** for the layout and the /usage and /billing pages. /usage/<id> still makes a second call until **WR-AM2-1** lands (U4's file). |
| A2 WR-A2-3: the dead `lib/utils` safeNext | **Fixed** |
| E3A-RUN RV-3: `delegated_reference` parses anywhere in the document | **Fixed**, using the finding's "at minimum" rule |
| E3A-RUN S-5: evidence document count | **Erratum recorded** (below). It is an evidence-text finding with no code path. The E3A evidence file is append-only and not owned here. |
| AM1-L3: the walk-stops-early mutant | **Fixed**: P08 now kills it in the default world |
| AM1-L2: a real-browser segment-throw case in the built suite | **Not done**; the reason is below |

### C0 WR-6: the personal organization (R66, 04-app C0.1)

- **Code.** `lib/services/console.ts` gains `personalOrg(client, userId)`. It reads `org_members` with `organizations!inner(name, created_by)`, filtered by `user_id`, by `role = owner` and by `organizations.created_by = user`, with `limit(2)`.
  - Exactly one row gives the organization. Zero rows, or two to guess between, give `null`. A failed read throws.
  - This is the same rule `infrx.grant_signup_credit` uses to bind the wallet.
- **Session.** `getSession()` uses `personalOrg()` and throws when the result is null ("No personal organization…"). `role` is always `owner`.
- **Unit tests** (`tests/c/consumer.test.ts`, default world):
  - **C0-ORG-01.** The first membership row is SHARED's (member). The other rows are an org the user owns but did not create, an org the user created but no longer owns, and the user's own org. Only the user's own org is returned.
  - **C0-ORG-02.** None, two owned personal orgs and an empty membership all give `null`. A failed read rejects.
- **Real PostgREST: C0-ORG-PG** (`tests/c/consumer-postgrest.test.ts`, `console-c0-real`). `stack.py` seeds two new users:
  - JOINED: their membership rows are re-inserted so SHARED's comes first.
  - ORPHAN: their personal org was deleted, and they remain a member of SHARED's only.
  - Results: JOINED, C1, C2 (also a member of SHARED), SHARED (owns an org that has another member) and the operator (who sees every org through RLS) each get their own org. ORPHAN gets `null` (refused). C1's session naming C2 gets `null`. An anonymous session gets `null` or a refusal.
  - **Precondition asserted:** the old `.eq(user_id).limit(1)` pick returns SHARED's org for JOINED.
- **Fails-before (real PG).** With `personalOrg` reverted to the old pick, `console-c0-real` gave **15 pass / 1 fail**. C0-ORG-PG expected JOINED's org and got SHARED's.
- **Passes-after.** `console-c0-real` **16/16** (previously 15).
- **Mutants** (C runner, all killed):
  - **C0-ORG-01**: the `limit(1)` pick restored.
  - **C0-ORG-02**: the owner predicate dropped.
  - **C0-ORG-03**: the creator predicate dropped.
  - **C0-ORG-04**: the first of two owned orgs is taken.
  - **C0-ORG-05**: a failed read is treated as "none".

### U1R WR-6: one GoTrue call per request

- **`requestClient`.** `lib/session.ts` defines `requestClient = cache(async () => onceGetUser(await createClient()))`. It is the request's one cookie client.
  - `onceGetUser` (console.ts) memoises `auth.getUser()`, so every later call gets the first answer, an outage included.
  - `getSession()`, `consumerSession()` (`lib/services/server.ts`) and `consumerCreditReads()` (`billing/credit-context.ts`) all take it.
  - A console render (layout: session, operator flag and sidebar credits; page: its credit reads) now reaches GoTrue once. It was 3 calls for the layout and 4 on /billing and /usage.
  - The middleware's `getUser()` is a separate phase that runs before the render. It is unchanged.
  - This also closes app-union's "transient auth failure on the second read": every read now sees the same answer.
- **U1R-AUTH-01** (recording client). consumerSession, the operator-flag read and the sidebar read run concurrently, then the page's read runs. The result is exactly **1** getUser call, and every caller gets the same answer.
- **U1R-AUTH-02** (source; these modules import `next/*`, R48). It pins the following:
  - `requestClient` is cached and memoised, and `getSession` takes it.
  - `session.ts` builds exactly one client.
  - `server.ts` and `credit-context.ts` take `requestClient()` and never call `createClient(`.
  - The layout still makes its three reads.
- **Fails-before.** The base glue fails U1R-AUTH-02. The base `console.ts` has no `onceGetUser`, so the test file does not load. At the behaviour level, restoring each old call is one of the mutants below.
- **Mutants** (C runner, all killed):
  - **U1R-AUTH-01**: credit-context gets its own client back. This is "the second call restored".
  - **U1R-AUTH-02**: `consumerSession` gets its own client.
  - **U1R-AUTH-03**: `getSession` gets its own client.
  - **U1R-AUTH-04**: `requestClient` is not cached.
  - **U1R-AUTH-05**: `getUser` is not memoised.

### A2 WR-A2-3: the dead safeNext

- `lib/utils.ts` keeps only `cn`, and `lib/utils.test.ts` is deleted. The live, stricter `safeNext` stays in `app/(auth)/flow.ts`.
- **A2-NEXT-02** (`tests/a/onboarding-flow.test.ts`) walks `app/`, `lib/` and `components/` for a `function safeNext` or `const/let/var safeNext =` definition. It expects exactly `app/(auth)/flow.ts`.
  - Fails-before (base `lib/utils.ts`): 25 pass / 1 fail, with `lib/utils.ts` listed.
  - Mutant **NEXT-SECOND-DEF** (the looser definition restored in `lib/utils.ts`): killed.
- **Follow-up.** `tests/contracts/discovery.test.ts` (F-BASE) asserted that `lib/utils.test.ts` stays discoverable, and it failed once the file was deleted (console-test 611 pass / 1 fail). That assertion is dropped. `lib/keys.test.ts` still proves `lib/` discovery, and the glob's `**` zero-directory check is unchanged. Contract mutants stay 212/212.

### E3A-RUN RV-3: `delegated_reference` (`tests/integration/app/runner.py`)

- **Change.** Every `| sNN … | STATUS |` row anywhere in the document is collected per scenario. A scenario binds only when all of its rows are PASS. Rows that disagree read `sNN: FAIL/PASS`, which gives NOT RUN whichever row comes last. Before, the last row won.
- **Test.** `test_a_scenario_whose_rows_disagree_is_not_a_reference` covers two cases: an accepted FAIL followed by another run's PASS, and a PASS followed by a run-1 FAIL. Both give NOT RUN with `s05: FAIL/PASS`. Two agreeing PASS rows still bind.
  - Fails-before (base runner): **1 failed, 19 passed**, because the FAIL-then-PASS document gave `PASS[delegated …]`.
  - Passes-after: **20 passed**.
- **Real reference.** `delegated_reference` on `E3C-CELLS-9227e9e.md` returns `None` (bound) for all four cells, so the binding is unchanged.
- **Residual.** The run-head and verdict-line checks are still substring matches over the whole document. Scoping all parsing to the accepted run's section would couple the parser to the document's heading layout. A document that records only another run's all-PASS table under the accepted run's head would still bind.

### E3A-RUN S-5: document count (erratum)

- The lens found a count mismatch, reproduced here from `git archive` exports:
  - `validate_plan.py` at the E3A code head `555355d8` reports **250 documents**, which is what `E3A-run-555355d.md:185` records.
  - At the evidence commit `7d51c5e5` it reports **251**; the extra document is the evidence file itself.
  - The verdict is PASS both times.
- This lane records the correction here instead of editing that append-only file.
- To avoid the same mistake, this file's `validate_plan` line below was taken **after** this evidence commit.
- No runner exit path is part of S-5: the lens's S-5 is the document count only. "exit paths" in the dispatch summary is E4C-RUNBOOK's CS-5.

### AM1-L3: the walk-stops-early mutant in the default world

- **Change.** `tests/u/credit_world.py` now defaults `U1R_CAPPED_LEDGER` to **200** (it was 120). CONSUMER_2's ledger is 201 rows, which is 3 pages at limit 100. Jobs stay at 120 (121 rows).
- **`console-pg`.** 8/8 + 8/8, with "ledger: 201 rows in 3 pages" and "jobs: 121 rows in 2 pages".
- **Hand mutant at the default world.** The mutant is APP-MINORS-1's, on `credit-reads.ts` rpcPage: `next_cursor … && page.cursor === null ? …`, which means only the first page carries a cursor.
  - P08 is **killed**: `ledger: page 2 has 100 rows and cursor null`.
  - P02, P03 and P07 fail too, for 4 fail / 4 pass.
  - Before, at 120, P08 survived (APP-MINORS-1 evidence).
- It stays a hand mutant, because the U runner skips the database suites.

### AM1-L2: a real-browser segment throw (not done)

- **The built suite cannot host it.** `make console-built` is `pnpm build && node --test tests/i2a`, with no server, auth or browser.
- **A real case needs E3A's composed world.** A console segment renders only for a verified, signed-in individual. The middleware revalidates the token with GoTrue, and the layout reads PostgREST. A real-browser case therefore needs `next start` plus an auth stand-in, a PostgREST origin and Chromium. That is E3A's world (`tests/integration/app/runner.py`, block e4b 56801–56899), a namespace this lane may not use.
- **No production path throws inside a segment.** The reads return Results, and the only throws on the render path (`getSession`) happen in the layout, so the root boundary catches them. A segment throw would need a test-only hook in production code or a harness fault chosen by E3A.
- **Current coverage.** I3-BOUND-01 already renders every `error.tsx` for real and asserts exactly one POST.
- **Recommendation.** An E3A journey check `segment-error`, with an edge fault that E3A chooses.

## Commands (at `65528fef` unless noted)

| Command | Exit | Counts |
|---|---|---|
| `make api-env`; `cd apps/app && pnpm install --frozen-lockfile` | 0 | — |
| `make console-test` | 0 | 669 tests: 614 pass, 0 fail, 55 skip (stack/DSN skips). The first run at `20760ec1` gave 611 pass / **1 fail**: the F-BASE discovery pin, fixed in `65528fef`. |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings |
| `make console-typecheck` | 0 | typegen + tsc clean |
| `make console-built` (at `20760ec1`; the later commit is test-only) | 0 | build ok; i2a 22/22 |
| `make console-mutants` (at `20760ec1`; contracts rerun at `65528fef`) | 0 | contracts 212/212; V 40/40; U 218/218; C **195/195** (+10); A **47/47** (+1); A catalog 46/46 |
| `make console-pg` | 0 | u1r 8/8 (ledger 201 / 3 pages); u4 8/8 |
| `make console-c0-real` | 0 | 16/16 |
| same, with `personalOrg` = the old `limit(1)` pick | 2 | 15 pass / 1 fail (C0-ORG-PG) |
| `credit_world.py` (app-u1r, default world) + the AM1-L3 hand mutant | 1 | 4 fail, incl. P08 (killed) |
| `uv run pytest -q tests/integration/app` | 0 | 20 passed (base runner: 1 failed / 19 passed) |
| `python3 research/plan/scripts/validate_plan.py` (after the evidence commit) | 0 | see the update file |

`git diff --stat b967a033..65528fef`: 16 files, all in the owned set except `apps/app/tests/contracts/discovery.test.ts` (the deviation below). They are `lib/session.ts`, `lib/services/{console,server}.ts`, `billing/credit-context.ts`, `lib/utils.ts` and `lib/utils.test.ts` (deleted), `tests/{a,c}/…`, `tests/u/credit_world.py` and `tests/integration/app/{runner,test_e3a_runner}.py`.

## Wiring requests

**WR-AM2-1** (U4: `app/(console)/usage/[requestId]/request-context.ts`). /usage/<id> renders make a second getUser until this lands. The result route handler has no React cache scope, so it behaves exactly as before.
```diff
--- a/apps/app/app/(console)/usage/[requestId]/request-context.ts
+++ b/apps/app/app/(console)/usage/[requestId]/request-context.ts
@@ -1,4 +1,4 @@
-import { createClient } from "@/lib/supabase/server";
+import { requestClient } from "@/lib/session";
@@ -14 +14 @@
-    const supabase = await createClient();
+    const supabase = await requestClient();
```
Test: add `"app/(console)/usage/[requestId]/request-context.ts"` to the file list in U1R-AUTH-02 (`tests/c/consumer.test.ts`). It fails on the current tree and passes with the hunk.

**WR-AM2-2** (A2: `app/(auth)/flow.ts:30`, comment only). "Stricter than `lib/utils` `safeNext`: a backslash…" should become "A backslash…" now that the other definition is gone.

## Deviations

`apps/app/tests/contracts/discovery.test.ts` (F-BASE) is outside the listed paths. It pinned the deleted `lib/utils.test.ts`, so the deletion the brief asked for required dropping that one assertion.

## Remaining effort

Optimistic 0.25 h / likely 0.5 h / pessimistic 1.5 h, confidence high. What remains is the merge, WR-AM2-1 and WR-AM2-2, and E3A's decision on a `segment-error` journey check for AM1-L2. The pessimistic case is that the E3A check is wanted now.
