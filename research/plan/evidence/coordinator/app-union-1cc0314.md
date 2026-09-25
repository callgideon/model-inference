# APP-UNION: the integrated App candidate (C0 + U1R + A2 + A3 with their coordinator wiring)

## Task and status

| Field | Value |
|---|---|
| Lane | APP-UNION (Opus integrator; consumer-v1 wave, program 22) |
| Status | **review**: four verified App lane branches merged in order, each lane's coordinator wiring applied as its own commit, every named check green. The coordinator can merge this one branch. |
| Branch / worktree | `codex/app-union` / `.claude/worktrees/codex-app-union` |
| Base | `ca226130` (= `claude/consumer-v1` tip at dispatch) |
| Code head | `1cc03146` (this file and the tracker update are committed on top of it) |
| Isolation | Task-local Docker only, `INFRX_D_TASK=app-c0` (PostgreSQL 127.0.0.1:55451, `infrx-app-c0-*` containers and network, removed at exit; `docker ps -a` / `docker network ls` show nothing left behind). No hosted Supabase, pilot box, AWS or SSM. No secret in any file or log. Nothing pushed, rebased, reset, amended or stashed. |

## Per-step SHAs (first-parent, oldest first)

| Step | SHA | What |
|---|---|---|
| 1 merge | `ae49d947` | `git merge --no-ff codex/app-c0` (verified head `ab0b5179`) |
| 1 wiring | `e47c2a2f` | C0 WR-1 (revised): `app/(console)/layout.tsx`, the whole file |
| 1 wiring | `2049d615` | C0 WR-3: the consumer context and read-port types move into `lib/contracts/v2/consumer.ts` |
| 1 wiring | `7ce5b20a` | C0 WR-4 (restated, exact): Makefile `console-c0-real` and `.PHONY` |
| 2 merge | `f5b18aad` | `git merge --no-ff codex/app-u1r` (verified head `7482c21a`) |
| 2 wiring | `ceb13f9c` | U1R WR-1 + WR-2, composed onto C0's layout, plus `tests/u/sidebar-credits.test.ts` |
| 3 merge | `f4cc45cc` | `git merge --no-ff codex/app-a2` (verified head `cd4a688e`) |
| 3 wiring | `1ae23f38` | A2 WR-A2-1: middleware public paths, plus `tests/a/public-routes.test.ts` |
| 3 wiring | `a90567ed` | A2 WR-A2-2: Makefile `console-mutants` runs `tests/a/run-mutants.mjs` |
| 3 wiring | `db8448a6` | C0 WR-1 follow-up: layout `ROUTES` set to A2's shipped `/verify-email` and `/welcome` (see below) |
| 4 merge | `fffab549` | `git merge --no-ff codex/app-a3` (verified head `7b0ea93f`) |
| 4 wiring | `f0fe2793` | A3 WR-1: `apps/infrx-api/tests/g/test_app_examples.py` |
| 4 wiring | `1cc03146` | A3 WR-2: Makefile `console-mutants` runs `tests/a/run-catalog-mutants.mjs` |

All four merges were textually clean. The lanes' code paths are disjoint, and `apps/app` is unchanged between the lanes' base `46776646` and `ca226130`. Every composition below is between a lane's pinned test or mutant and a coordinator wiring.

## Wirings applied (source evidence section → what landed)

- **C0 WR-1 (revised)**, from [C0-acfd9a5.md](../c/C0-acfd9a5.md) "Wiring requests (revised)".
  - `layout.tsx` is byte-identical to the evidence block: diffed against the extracted text, IDENTICAL.
  - Composed check: `tests/c/client-boundary.test.ts` "the console layout never redirects…" is now unconditional, and `tests/c/credits.test.ts`'s layout case also applies.
- **C0 WR-3**, from C0-acfd9a5.md WR-3 and its fix-round addition.
  - `AuthUser`, `ConsumerAccount`, `ConsumerContext`, `ConsumerReads`, `ConsumerRequest`, `CreditLedgerEntry`, `ConsumerSession` and `ConsoleShell` move verbatim, doc comments included, into the new type-only `lib/contracts/v2/consumer.ts`.
  - The v2 namespace reaches them through `export * from "./consumer.ts"` in `lib/contracts/v2/types.ts`, the same pattern as `published-model` and `lifecycle`.
  - There is no `lib/contracts/index.ts`; the v2 `types.ts` re-export list is the index, and it is consistent.
  - `lib/services/console.ts` imports the types and re-exports them, so its existing importers (`server.ts`, tests) are unchanged. Three imports that only the moved types used were dropped, because lint flagged them.
  - **Not promoted: `ConsumerClient`.** It is `PostgrestClient & {auth}`, and `PostgrestClient` is `query.ts`'s adapter type. Promoting it would move the adapter into the contract, and no App or U lane constructs it.
- **C0 WR-4 (restated, exact)**: the Makefile target and `.PHONY` entry, text from the evidence. It is not added to `check`, because it needs Docker.
- **C0 WR-6** (`lib/session.ts`): the evidence gives no exact patch, so it was **not applied**. It stays open for A2/U3. After WR-1, `getSession()` serves only the operator flag in the layout.
- **U1R WR-1 + WR-2**, from [U1R-ae8c388.md](../u/U1R-ae8c388.md) "Wiring requests (revised)".
  - `components/sidebar.tsx`: the NAV label and the balance-row label become "Credits". These are the evidence hunks, verbatim.
  - `layout.tsx`: the evidence diff was written against the legacy layout, so its `sidebarCredits()` hunk is integrated into C0's layout:

    ```tsx
    const balance = shell.reads === null ? null : sidebarCredits(await (await consumerCreditReads()).reads.wallet());
    ```

    It keeps C0's `consoleShell` gate (an operator with no wallet gets the fixed copy), renders U1R's figure, and never shows legacy USD.
  - `tests/u/sidebar-credits.test.ts` is verbatim from the evidence. It fails 0/1 on the pre-patch tree and passes 1/1 after.
- **A2 WR-A2-1**, from [A2-16fe503.md](../a/A2-16fe503.md): the evidence diff was applied with `git apply`, and `tests/a/public-routes.test.ts` was added verbatim. It fails 0/2 on the pre-patch middleware and passes 2/2 after.
- **A2 WR-A2-2**: `cd apps/app && node tests/a/run-mutants.mjs` added to `console-mutants`. The evidence expects 45/45 after the fix round.
- **C0 WR-1 follow-up (ROUTES)**, applied under C0's own instruction: "Set ROUTES.verifyEmail in the same commit that ships A2's /verify-email, and ROUTES.onboarding in the one that ships C3A/A2's onboarding route".
  - A2 ships `/verify-email` (resend) and `/welcome` (grant-claim retry plus the actual CREDIT balance).
  - The layout now has `ROUTES = { verifyEmail: "/verify-email", onboarding: "/welcome" }`.
  - Both targets are in the `(auth)` group, outside the console layout, so there is no redirect loop.
  - Composed check: A2-ROUTE-03, appended to `tests/a/public-routes.test.ts`. It requires both targets set, each a shipped `(auth)` page, and `/verify-email` public. It fails 1 on the null-ROUTES layout and passes 3/3 after.
  - This is the only wiring the brief did not list by name. To revert it, revert `db8448a6`.
- **A3 WR-1**, from [A3-19f8467.md](../a/A3-19f8467.md) "WR-1 file content (exact)": the file is verbatim, and ruff is clean.
- **A3 WR-2**, from the "Wiring request update" section: `cd apps/app && node tests/a/run-catalog-mutants.mjs`, placed after A2's line. Both runner lines are present and both are green.

## Conflicts and how they were composed

The merges had no textual conflicts. These are the semantic ones, where a lane's pin met a wiring:

1. **C0 mutants CREDITS-07, CREDITS-11 and CREDITS-12 went stale with C0's own WR-1.** Their `find` was the legacy line `const balance = sidebarBalance(await getBalance(session.orgId));`, and `--only` reported 3 stale. The lane had never run its runner with WR-1 applied.
   - At `e47c2a2f` they were repointed to WR-1's balance line, each with the same defect: an own amount, a throw, or a `?? "$0.00"` default. `--only`: 3/3 killed.
   - At `ceb13f9c` they were repointed again, to the composed U1R line. With CREDITS-13, `--only` gave 4/4 killed.
2. **C0 mutant C0-SES-02 was anchored on `/** What the console shell`**, the doc comment that moved with `ConsoleShell` in WR-3.
   - Its anchor is now the next text after `consumerSessionFrom` (`/**\n * The shell's decision.`). `--only`: killed. The static scan of every mutant list shows 0 stale.
3. **C0's `tests/c/credits.test.ts` pins the layout's balance line to one of two forms**, the legacy form and C0's `sidebarCredit(await shell.reads.balance())`.
   - U1R's composed test requires `sidebarCredits(await (await consumerCreditReads()).reads.wallet())`. The two cannot both hold for one `const balance`, and the brief says the composed result must render `sidebarCredits(wallet)`.
   - Composition: C0's regex gains the composed line as a third accepted form. Every other assertion is unchanged: no `??`, no `$`, no `throw`, no own formatting, and `balance={balance}`.
   - Before the edit it failed 1 (6/7). After, it passes 7/7.

None of these changes a lane's behaviour. Edits to lane-owned test and mutant files are the three compositions above and nothing else.

## Commands (worktree root unless noted; all exit codes observed)

| Step / head | Command | Exit | Result |
|---|---|---|---|
| setup | `cd apps/app && pnpm install --frozen-lockfile` | 0 | lockfile unchanged |
| setup | `make api-env` | 0 | pinned `uv sync --frozen` |
| base `ca226130` | `cd apps/app && pnpm test` | 0 | 351 tests, 351 pass |
| 1 `7ce5b20a` | `pnpm test` | 0 | 393 tests: 381 pass, 0 fail, 12 skipped (C0 real-stack file, visibly) |
| 1 | `pnpm lint` | 0 | 0 errors, 2 pre-existing warnings (`lib/contracts/conformance.ts`, `fake-services.ts`) |
| 1 | `pnpm exec next typegen && pnpm exec tsc --noEmit` | 0 | clean |
| 1 | `node tests/c/run-mutants.mjs --self-test && node tests/c/run-mutants.mjs` | 0 | 4 self-tests; 142/142 killed, 0 stale |
| 1 | `cd apps/infrx-api && uv run --frozen --no-sync pytest -q tests/contracts` (after WR-3) | 0 | 1266 passed |
| 1 | `make console-c0-real` | 0 | **12/12 pass**. The brief expected 11; the lane's fix round added the composed session/shell case. 201 ledger pages of 100; page 100 in 11.5 ms |
| 2 `ceb13f9c` | `pnpm test` | 0 | 431 tests: 413 pass, 0 fail, 18 skipped (+6 U1R `credit-pg` without DSN) |
| 2 | `pnpm lint` / typecheck | 0 / 0 | 0 errors, 2 pre-existing warnings / clean |
| 2 | `node tests/u/run-mutants.mjs` | 0 | 2 self-checks; 97/97 killed |
| 2 | `node --test tests/u/sidebar-credits.test.ts` on a scratch copy of the pre-patch layout and sidebar | 1 | 0 pass, 1 fail (the composed check discriminates) |
| 3 `db8448a6` | `pnpm test` | 0 | 476 tests: 453 pass, 0 fail, 23 skipped (+5 A2 real-PG without DSN) |
| 3 | `pnpm lint` / typecheck | 0 / 0 | 0 errors, 2 pre-existing warnings / clean |
| 3 | `node tests/a/run-mutants.mjs` | 0 | **45/45 killed**. The brief expected 23/23; the lane's fix round added 22 |
| 3 | `public-routes.test.ts` on a scratch copy of the pre-patch middleware / null-ROUTES layout | 1 / 1 | 0/2 / 2 pass 1 fail |
| 4 `1cc03146` | `cd apps/infrx-api && uv run --frozen --no-sync pytest -q tests/g/test_app_examples.py` | 0 | 1 passed; `ruff check` clean |
| 4 | same, negative control: `tools: []` injected into the recorded text body | 1 | 400 `unsupported_parameter`, param `tools` |
| 4 | same, negative control: `idempotency-key` dropped from the async example | 1 | `AssertionError: resume` (not `idempotency_replayed`). `example-calls.json` restored with `git checkout --` |
| 4 | `node tests/a/run-catalog-mutants.mjs` | 0 | 2 self-checks; 46/46 killed |
| 4 | `pnpm test` / lint / typecheck | 0 / 0 / 0 | 508 tests: 485 pass, 0 fail, 23 skipped / 0 errors, 2 warnings / clean |
| 4+5 `1cc03146` | `make console-test console-lint console-typecheck console-mutants` | 0 | See the breakdown below |
| 5 | `make console-c0-real` | 0 | **12/12 pass**; page 100 in 12.5 ms; no `app-c0` container or network left |
| 5 | `cd apps/infrx-api && uv run --frozen --no-sync pytest -q tests/g/test_app_examples.py tests/contracts` | 0 | 1267 passed |
| 5 | `python3 research/plan/scripts/validate_plan.py` | 0 | PASS: 133 tasks; backend closure 50; App closure 64; 934 links across 224 documents |
| 5 | `git merge-tree --write-tree --name-only codex/app-union codex/wave4b-union` (`9d61d1e1`) | 0 | **no conflicts**; tree `806b2360`. Both touch the Makefile, and it merges cleanly |
| 5 (WR-7 data point) | C0 stack over `git archive 806b2360` (app-union ⊕ wave4b-union, migrations 0001–**0022**), scratch export, same `app-c0` block | 0 | **12/12 pass**; page 100 in 12.3 ms |

Breakdown of `make console-test console-lint console-typecheck console-mutants`:

- console-test: 508 tests, 485 pass, 0 fail, 23 skipped.
- console-lint: 0 errors, 2 warnings.
- console-typecheck: clean.
- console-mutants:

  | Runner | Result |
  |---|---|
  | contracts | 14/14 self-tests; 212/212 killed |
  | V | 40/40 |
  | U | 2 self-checks; 97/97 |
  | C | 4 self-tests; 142/142, 0 stale |
  | A2 | 45/45 |
  | A3 catalog | 2 self-checks; 46/46 |

The 23 skipped are the real-database files, which skip visibly without their DSN or stack: C0 12, U1R `credit-pg` 6, A2 `grant-pg` 5. C0's 12 ran through `make console-c0-real`. U1R's `credit_world.py` and A2's `pg_up.py` real-PG runs were **not** rerun here; each lane recorded them on its own branch, and nothing in their paths changed in the union.

## Items recorded for later lanes (not applied)

- **C0**
  - **WR-2** (U1R/U4 pages onto `consumerSession()`) is not applied. U1R's pages read through U1R's own `consumerCreditReads()`.
  - **WR-5** (D10 SQL): a `consumer_credit_ledger` SECURITY DEFINER page function for O(limit) ledger pages.
  - **WR-6** (`lib/session.ts` membership pick; A2/U3): no exact patch was given.
  - **WR-7**: rerun `console-c0-real` on the merged SHA once 0022 lands. The scratch run above (0001–0022) is a data point, not the gate.
- **U1R**
  - **WR-3(a)** (D10: `consumer_jobs` filters and key columns).
  - **WR-3(b)** (D10: the partial index `credit_ledger_wallet_credits_in_idx`, plus the P02 lines).
  - **WR-3(c)** (D10: a ledger page function; tens of thousands of entries).
  - **WR-4** (U4 route `usage/[requestId]/page.tsx`).
  - **WR-5** (A3/U3 use `credits()` / `amount()` for money).
  - **WR-6** (a single consumer credit port for C0 and U1R: coordinator decision).
- **A2 WR-A2-3** (low): `lib/utils.ts` `safeNext` has no production caller left. Delete it, or re-export flow's.
- **A3**
  - **WR-3** (operator/I2A): Vercel server env `INFRX_API_BASE_URL`, plus the README line. Until it is set, `/models` and `/docs` render "unavailable" by design.
  - **WR-4** (G7): derive `PROVISIONAL` from the card's approval instead of the constant.
  - Open findings: F-1 (legacy-regime bare id refused in G's fake world), F-2, F-3, and the support-email input.

## Open issues found while composing

- **Two `getUser()` calls per console render.** The layout resolves `consumerSession()` (C0) and then `consumerCreditReads()` (U1R) for the sidebar figure. U1R flagged the same double read. U1R WR-6 (one port) removes it.
- **A transient auth failure on the second read.** `consumerCreditReads()` redirects to `/login` when `getUser()` then returns no user (U1R's page behaviour). The window is one request, after C0 has already resolved `ready`. It closes with WR-6.
- **C0's `sidebarCredit` is unused by the layout now.** It remains tested in `lib/services/credits.ts`; it is C0's to keep or retire.

## Remaining effort (APP-UNION to merged)

| | Hours |
|---|---|
| Optimistic | 0.25 |
| Likely | 0.75 |
| Pessimistic | 2 |

Confidence is medium. The basis:

- Every named check is green at `1cc03146`, and the merge with the backend union is conflict-free.
- What remains is a coordinator review of the three compositions and the ROUTES follow-up, the merge, a rerun of `console-c0-real` and the App suites on the merged SHA with 0022 (WR-7), and the recorded later-lane items.

## Verification log

- 2026-09-25T23:25Z: written at code head `1cc03146` (base `ca226130`). Every command above was run in this worktree at the step head named, except the WR-7 data point, which ran from a scratch `git archive` export of tree `806b2360`.
