# C0 — Real consumer context and read port

## Task and status

| Field | Value |
|---|---|
| Task | C0 (track C, product app; program 22, [brief](../../consumer-v1/04-app.md#c0--real-consumer-context-and-read-port)) |
| Owner / session | Claude Opus 5.5 (1M context), session `01TK5CtkWA94qD9HopmUbcsD` |
| Status | **review**: the port, context and reads are implemented and proven against real PostgREST; they are **not yet wired into pages**. The layout and pages are outside this lane's paths, so that step is a wiring request (below). |
| Test IDs | CONSOLE-TENANT, CREDIT-UNITS (read half), APP-JOURNEY (read half) |

## Source

| Field | Value |
|---|---|
| Base SHA | `46776646` (= `claude/consumer-v1` tip at dispatch) |
| Code head reviewed | `acfd9a51` |
| Branch / worktree | `codex/app-c0` / `.claude/worktrees/codex-app-c0` |
| Integrated SHA | none |

Commits, oldest first:

```
7d080923 tests(c0): consumer context and read port seams, written before the implementation
82e870ce services(c0): real consumer context and read port over PostgREST
3cc6d78d tests(c0): type the ledger walk's page (tsc)
9a1fe367 tests(c0): CONSOLE-TENANT through real PostgREST; ledger read drops the per-row actor
acfd9a51 mutants(c0): 29 single-edit mutants over the consumer seams; C runner runs consumer.test.ts
```

Changed paths (all owned): `apps/app/lib/services/{query,console,credits,server}.ts`,
`apps/app/tests/c/{consumer.test.ts,consumer-postgrest.test.ts,realdb/stack.py,mutants.json,run-mutants.mjs,query-boundary.test.ts}`.
`cursor.ts` is reused unchanged. `lib/session.ts` is not touched (see WR-6).

## What was built (brief item → code)

1. **Verified individual → personal consumer account, server-side**. `resolveConsumerContext(port, user)` in `lib/services/console.ts` returns one of five typed states:
   - `signed_out`
   - `unverified` (GoTrue `email_confirmed_at` is absent)
   - `onboarding` (verified, but no consumer wallet yet)
   - `ready {userId, email, walletId, orgId, suspended}`
   - `unavailable`

   The account is the caller's **own** consumer wallet, found by owner. It uses the new named query `consumer_wallet`: `console_credit_wallets` where `owner_user_id` is the caller and `kind = consumer`, with the owner bound last and checked per row by `scopedPort`. Its personal organization comes from the wallet (`org_status`). The context never uses membership order, and it never infers the account from an operator or provider organization.

   Any failure resolves to `unavailable`. It never resolves to `onboarding`, which would offer a second grant flow, and it never resolves to `signed_out`. `authUserOutcome` maps `auth.getUser()` in the same way: an outage is `unavailable`, not a sign-out.

   `lib/services/server.ts` adds `consumerSession()` (React `cache`, once per request). It uses the Supabase server client with the individual's cookie JWT, calls `getUser()`, then `postgrestPort` → `resolveConsumerContext` → `createConsumerReads`. Any throw (missing env, missing cursor secret, client failure) is `unavailable`.
2. **Real Supabase/PostgREST query path**.
   - `postgrestPort(client)` in `lib/services/query.ts` is the first real executor of the C1 `QueryPort`. It renders the same `QueryPlan` that `renderSql` renders: one relation, named columns (with PostgREST renames), the registry's predicates, the keyset on the whole sort key (quoted, with quotes and backslashes refused), the tenant **last**, the order and the cap. Errors keep the code only. Aggregates and ClickHouse queries are refused.
   - `createConsumerReads({pg, rpc, cursorSecret}, account)` provides these reads:
     - `balance()`: `console_wallet_summary(p_user)` → `creditBalanceOf` in `credits.ts`. The result is exact CREDIT, in its own unit, and must be this wallet. `available` is recomputed and must equal the stored value. A null wallet, another wallet, USD, a JSON number or drift is a refusal, never a zero. `sidebarCredit` returns `null` for fixed "unavailable" copy.
     - `legacyUsd()`: `console_legacy_usd_statement(p_org)` as a **separate** USD statement. It returns `null` when there is no history, and it is never merged into the balance.
     - `ledger()`: `credit_ledger_page`, keyset `(created_at, entry_id)` desc on `credit_ledger_wallet_created_idx`. The wallet is the tenant, the cursor is HMAC-bound to the wallet, and only CREDIT rows of the closed v2 kinds are accepted.
     - `requests()` and `request(id)`: D10's `consumer_jobs`. Money is in the row's own unit, and the unit must match the regime. `charged` stays `null` until settled, never zero. `result` is F2C.b's `ReadOutcome` from the database's `result_available` and persisted expiry, not the page's clock. At the RPC's 100-row cap a full page still carries a cursor. Malformed ids are `not_found` without a query.
     - `result(id)`: D10's `consumer_job_result`, the trusted port. No customer API key reaches the browser. `not_found`, `result_pending` and `result_expired` come back typed with **fixed** text (the database's message names identifiers).
     - `keys()`: `keys_list` scoped to the personal organization. Metadata only, no hash.

     Every read returns a `Result`. A port failure is `dependency_unavailable` (retry), and a malformed row is `internal_error`.
   - Model availability is not a C0 read: no browser-readable catalog projection exists in the database (see UI-2).
3. **Real RLS/RPC grants** (`tests/c/consumer-postgrest.test.ts`, 11 cases, through supabase-js against the pinned PostgREST v13.0.4 and the pinned Supabase PostgreSQL with migrations 0001–0021).

   Sessions tested:
   - individuals A and B
   - B's personal org suspended (still ready, marked `suspended`)
   - B as a member of two organizations
   - ungranted (`onboarding`)
   - provider developer (`onboarding`)
   - operator (`onboarding`; the view shows them every wallet)
   - anonymous (`unavailable`, and every forged-account read refused)
   - B's JWT reading A's account (no balance, legacy statement, ledger, keys, detail or result)

   Data cases:
   - an individual with an empty history
   - 20,001 ledger entries over 500 tied instants: every entry exactly once, and the sum equals the durable `ledger_total` exactly
   - request walks at limits 1, 2, 5 and 100 across four admissions at one frozen instant
   - the expired result versus the available one (the persisted expiry decides)
   - held requests: charge `null` with an active hold
   - mixed USD (`12.34567891`, `rollout_hold`) and CREDIT kept apart

   Unverified is covered by the unit suite, because the stack has no GoTrue (see UI-1).

## Fails-before proofs

| Seam | Old behaviour | Proof |
|---|---|---|
| All 24 unit cases | Nothing at base `46776646` provides the port | `node --test tests/c/consumer.test.ts` at `7d080923` (tests only): exit 1, `SyntaxError: … does not provide an export named 'QueryPortError'` (1 file, 0 pass) |
| Sidebar balance (a plausible wrong balance) | `lib/credits.ts` shows the legacy USD `org_wallet_summary` of the session org | Real stack, case "fails-before: the legacy sidebar…": `legacy sidebar: $12.34567891; C0 sidebar: 9,939.2416 credits`. The old path shows USD history to an individual holding 9,939.24 CREDIT. |
| Account selection | `lib/session.ts` takes `org_members … .limit(1)` | Real stack: CONSUMER_2 has **two** readable memberships (their own org and SHARED's), so the old choice follows no ownership rule. C0 resolves their wallet's personal org. |
| Adapter defect 1, found only by the real run | — | `stack1.log`: 8 pass, 3 fail. supabase-js wraps `or()` in parentheses itself, so every keyset request was malformed and every second page was `dependency_unavailable`. Fixed in `9a1fe367`; the unit expectation was corrected. |
| Adapter defect 2, found only by the real run | — | `stack2.log`: the large-history case took 343 s. `EXPLAIN ANALYZE` showed the view evaluating `visible_principal()` for every wallet row below its sort (1,435 ms for a 10,000-row page). The ledger read no longer selects `actor`; the same page now takes 12.3 ms (`stack4.log`), with a regression bound in the test (< 250 ms, plan uses `credit_ledger_wallet_created_idx`, no seq scan on `credit_ledger`). |
| Mutants C0-CTX-02 and C0-CTX-04 | Survived the first run | The predicate-blind case returned two rows, which the count refused rather than the owner check. The executor case built its plan by hand, so the registry constant was never sent. Both cases were tightened in `acfd9a51`, and both mutants are now killed. |

## Commands (worktree `codex-app-c0`)

| Command | Exit | Result |
|---|---|---|
| `cd apps/app && pnpm install --frozen-lockfile` | 0 | lockfile unchanged |
| `make api-env` | 0 | pinned `uv sync --frozen` |
| `pnpm test` at base `46776646` | 0 | 351 tests, 351 pass |
| `pnpm test` (head) | 0 | 386 tests: 375 pass, 0 fail, 11 skipped (the real-stack file, which skips visibly without `INFRX_C0_STACK`) |
| `pnpm lint` | 0 | 0 errors, 2 warnings (pre-existing: `conformance.ts`, `fake-services.ts`) |
| `pnpm exec next typegen && pnpm exec tsc --noEmit` | 0 | clean |
| `node tests/c/run-mutants.mjs --self-test` | 0 | 4 self-tests |
| `node tests/c/run-mutants.mjs` | 0 | 133 mutants, 133 killed (29 new C0; PAGE-01, PAGE-02, SECRET-01 and SAMPLE-C2 repointed after `keysetPage`) |
| `node tests/contracts/run-mutants.mjs --self-test && pnpm test:mutants` | 0 | 212 of 212 killed |
| `node tests/v/run-mutants.mjs` | 0 | 40 of 40 killed |
| `node tests/u/run-mutants.mjs` | 0 | 64 of 64 killed |
| `cd apps/infrx-api && INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/realdb/stack.py` | 0 | 11 of 11 pass in 5.5 s; seeding about 17 s; 201 ledger pages of 100; page 100 executes in 12.3 ms |

Isolation:
- PostgreSQL ran in `infrx-app-c0-postgres-supabase` on 127.0.0.1:55451 (D harness, label-owned, removed at exit).
- PostgREST ran in `infrx-app-c0-postgrest` on the `infrx-app-c0-net` network, reached over the bridge only, and was removed at exit. `docker ps -a` and `docker network ls` show nothing left behind.
- No hosted Supabase, pilot box, AWS or SSM was used.
- The JWT secret and database password are local constants, following the `test_postgrest_d10.py` pattern. No DSN or key appears in logs.

## Wiring requests (coordinator; not applied)

- **WR-1: `apps/app/app/(console)/layout.tsx`**. Use the consumer context for the shell. The composed path is proven by `consumer-postgrest.test.ts`, whose `accountOf`/`readsAs` are exactly `consumerSession()`'s composition. With the patch, add the static case below to `tests/c/client-boundary.test.ts`.
  ```tsx
  import { redirect } from "next/navigation";
  import { ConsoleDataUnavailable } from "@/components/console-data-state";
  import { Sidebar } from "@/components/sidebar";
  import { sidebarCredit } from "@/lib/services/credits";
  import { consumerSession } from "@/lib/services/server";
  import { getSession } from "@/lib/session"; // operator flag only, until U3 moves it

  export const dynamic = "force-dynamic";

  export default async function ConsoleLayout({ children }: LayoutProps<"/">) {
    const { context, reads } = await consumerSession();
    if (context.state === "signed_out") redirect("/login");
    if (context.state === "unverified") redirect("/verify-email");   // A2's route
    if (context.state === "onboarding") redirect("/onboarding");     // C3A/A2 grant retry
    if (context.state !== "ready" || reads === null) {
      return <main className="px-4 py-6"><ConsoleDataUnavailable title="Your account" /></main>;
    }
    const balance = sidebarCredit(await reads.balance());
    const { isOperator } = await getSession();
    return ( /* unchanged shell */ <Sidebar email={context.account.email} balance={balance} isOperator={isOperator} /> );
  }
  ```
  Static proof:
  ```ts
  test("the console shell reads the consumer context, not the legacy USD sidebar", () => {
    const layout = code("app/(console)/layout.tsx");
    assert.match(layout, /consumerSession\(\)/);
    assert.match(layout, /sidebarCredit\(/);
    assert.doesNotMatch(layout, /getBalance\(/);
  });
  ```
- **WR-2: `app/(console)/usage/page.tsx` and `billing/page.tsx`** (U1R/U4 page owners). Replace `consoleContext()` fixtures with `const { reads } = await consumerSession()`. Render `reads.balance()` and `reads.legacyUsd()` separately, the CREDIT history from `reads.ledger()`, requests from `reads.requests()`, and detail and result from `reads.request/result()`. A non-ok `Result` is the existing `ErrorPanel` or unavailable copy. Once U1R lands, `fake-console-context.ts` stays development-only.
- **WR-3: `apps/app/lib/contracts/` (coordinator contract)**. Promote the port types exported from `lib/services/console.ts` so that A/U lanes do not import from services: `ConsumerContext`, `ConsumerAccount`, `ConsumerReads`, `ConsumerRequest`, `CreditLedgerEntry` and `AuthUser`. The parity tests are the existing contract ones.
- **WR-4: `Makefile`**. Add
  `console-c0-real: cd apps/infrx-api && INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/realdb/stack.py`.
  Proof: the command table above (exit 0, 11 of 11).
- **WR-5: D10 SQL lane (not App)**. `console_credit_ledger` is a `security_barrier` view that joins wallets, so a page cannot stop early on the index. It is bitmap-bounded to the wallet's rows before the cursor, then sorted with top-N: 12 ms at 10,000 rows, but O(rows before cursor), not O(limit). For truly O(limit) pages, add a `public.consumer_credit_ledger(p_after, p_limit)` SECURITY DEFINER function shaped like `consumer_jobs`: `auth.uid()` → the caller's wallet, a row-comparison keyset, and the same grants. Also, `visible_principal()` per row makes any caller selecting `actor` from that view pay about 140 µs per wallet row, and legacy `console_ledger` is likely the same.
- **WR-6: `apps/app/lib/session.ts` (coordinate A2)**. `getSession()` still takes the first membership. After WR-1 it should serve only the operator flag and admin pages. A2/U3 should replace its membership pick with the consumer context or an explicit operator lookup.
- **WR-7: rerun on the merged SHA**. The stack applies every migration in the directory, so after D10-FOLLOWUP's `0022` lands, rerun WR-4's target. The C0 port does not depend on 0022 objects.

## Unresolved inputs and open issues

- **UI-1 (P-05)**: "verified" is GoTrue's `email_confirmed_at` from `auth.getUser()`, which is server-revalidated. The stack has no GoTrue, so the real test supplies the user object and exercises the database half. The target-environment verification, callback and abuse configuration belongs to A2/I2A under P-05.
- **UI-2**: model availability. No browser-readable catalog projection exists (`catalog_listings` lives in `infrx`, and `resolve_admission_pins` is `service_role` only). A3 consumes G7's published-model projection. A live App read of availability would need a D-lane public view, so it is not faked here.
- **UI-3**: `consumer_jobs` returns `charged` only for `settled`. A settled CREDIT job with no `inference_debit` row would show `null` (unknown), not zero. That is fail-closed, and D10 owns the SQL.
- The ledger no longer shows `actor`. For a consumer the entry `kind` already says who acted (grant or adjustment by the platform, debit by settlement).

## Proposed ruling (next free: R134)

> **R134 (C0):**
> - The consumer App resolves the account server-side from the revalidated session user to their consumer wallet by owner (`console_credit_wallets`, `owner_user_id = auth.uid()`, `kind = consumer`, checked per row) and that wallet's personal organization, never by membership order or an operator/provider organization. Every failure to resolve is `unavailable`, distinct from `signed_out`, `unverified` and `onboarding`.
> - Consumer reads run with the individual's JWT only: no service key, no customer API key.
> - The balance is exact CREDIT from `console_wallet_summary`, recomputed and matched to the wallet, and refused rather than zero. Legacy USD is a separate statement.
> - Request charges are in the row's own unit, `null` until settled. Result access is only through `consumer_job_result` with the persisted expiry.

## Remaining effort (C0 to merged and wired)

| | Hours |
|---|---|
| Optimistic | 1 |
| Likely | 3 |
| Pessimistic | 6 |

Confidence is medium. The code and tests are done. What remains is the coordinator's WR-1 through WR-4 patches, a rerun of the real stack and the App suites on the merged SHA with 0022, and any review fixes. The page rewrites themselves are U1R's and U4's.

## Verification log

- 2026-09-25T22:04Z: evidence written at code head `acfd9a51`; all commands above rerun at that head.

## Fix round (review findings 0-C0-V1, 0-C0-V2, 0-C0-V3, 1-C0-V1)

| Field | Value |
|---|---|
| Handback head reviewed | `5a152cdd` (code `acfd9a51`) |
| Fix-round code head | `ffd5ba7c` (this section is committed on top of it) |
| Commits | `f2746f01` tests first · `40758d58` split + shell · `8264e104` real-stack case · `ffd5ba7c` layout guard |
| Changed paths (all owned) | `apps/app/lib/services/{console,server}.ts`, `apps/app/tests/c/{consumer.test.ts,consumer-postgrest.test.ts,client-boundary.test.ts,credits.test.ts,mutants.json}` |

### Per finding

| Id | Fixed here | What changed | Fails before → passes after |
|---|---|---|---|
| 0-C0-V1 | **yes** | `consumerSession()`'s logic is now `consumerSessionFrom(client, cursorSecret)` in `console.ts` (no `next/*`, so testable under R48). `server.ts` is a one-line `cache()` wrapper passing `createClient` and `consoleCursorSecret` as thunks. Both are called **inside** the guard, the secret first, so a missing secret is `unavailable` for every state, not only once an account is ready. Three new cases: auth outage (retryable fetch, GoTrue 500) → `unavailable`; missing secret / `createClient` throw / `getUser` reject → `unavailable`; signed out, unverified and onboarding → `reads: null`, ready → reads on the same client. | Reproduced both reviewer mutants on `server.ts` at `acfd9a51`: `pnpm test` exit 0, 375 pass / 0 fail (both **survive**). The tests at `f2746f01` fail to load (`does not provide an export named 'consoleShell'`). The mutants are re-expressed in the tested function: `C0-SES-01` (outage → signed_out) and `C0-SES-02` (catch → onboarding) are **killed**, as are `C0-SES-03` (secret read lazily) and `C0-SES-04` (non-ready context gets reads). |
| 1-C0-V1 | **yes** (decision + guard); applying the layout is the coordinator's (WR-1 revised below) | New `consoleShell(session, isOperator, routes)` in `console.ts` is the layout's whole decision. An operator whose consumer state is `onboarding` gets `render` with `reads: null`, so `/admin` (which checks `isOperator` itself and 404s otherwise) stays reachable. `unverified`/`onboarding` redirect only when the route is non-null, otherwise a fixed `panel`, so there is never a redirect to a route that has not shipped. `unavailable` is a panel for everyone, operators included. The unit composition is `consumerSessionFrom` (operator: view returns no own wallet) → `consoleShell(…, true, …)` → `render`. The real stack now runs the same composition over supabase-js and PostgREST: the stack's operator renders, the ungranted individual is redirected to `/onboarding`, and c1 renders with a CREDIT balance equal to the durable wallet's. | `C0-SHELL-01` removes the operator bypass (WR-1 as first written) and is **killed**. `C0-SHELL-02..05` (ungated onboarding or verify redirect, operator outage rendered, ready without reads rendered) are **killed**. At the composition root, the new `client-boundary` case "the console layout never redirects to onboarding or verification itself…" **fails** against WR-1 as first written (exit 1, `those redirects are consoleShell's, gated on the route`) and **passes** on today's layout and on WR-1 as revised. |
| 0-C0-V2 | **no**: coordinator (Makefile, gates) | WR-4 restated with an exact patch (below). The real stack was rerun at `8264e104`, and it is now 12 cases with the composed session/shell. | n/a |
| 0-C0-V3 | **no**: coordinator (layout) and U1R/U4 (pages) | WR-1 is revised (below) so that it is safe to apply. WR-2 is unchanged. C0/APP-M1 closure stays held until WR-1 and WR-2 land. | n/a |

Found while fixing: the first real run of the new composed case failed at `40758d58`. `Object.assign(client, {auth})` replaced the object supabase-js reads its access token through, so every read failed. The session resolved to **`unavailable`**, not onboarding or a fake balance, which is the fail-closed path working. The test now delegates `from`/`rpc` instead (`8264e104`).

### Commands (worktree `codex-app-c0`, head `ffd5ba7c`)

| Command | Exit | Result |
|---|---|---|
| `pnpm test` with reviewer mutant V-SV-AUTH-OUTAGE on `server.ts` at `5a152cdd` | 0 | 386 / 375 pass / 0 fail / 11 skip: **survives** (reproduced) |
| `pnpm test` with reviewer mutant V-SV-CATCH on `server.ts` at `5a152cdd` | 0 | 386 / 375 pass / 0 fail / 11 skip: **survives** (reproduced) |
| `node --test tests/c/consumer.test.ts` at `f2746f01` (tests only) | 1 | `SyntaxError … does not provide an export named 'consoleShell'` |
| `pnpm test` | 0 | 393 tests: 381 pass, 0 fail, 12 skipped (the real-stack file, now 12 cases) |
| `pnpm lint` | 0 | 0 errors, 2 pre-existing warnings |
| `pnpm exec next typegen && pnpm exec tsc --noEmit` | 0 | clean |
| `node tests/contracts/run-mutants.mjs --self-test && pnpm test:mutants` | 0 | 212 of 212 killed |
| `node tests/v/run-mutants.mjs` | 0 | 40 of 40 killed |
| `node tests/u/run-mutants.mjs` | 0 | 64 of 64 killed |
| `node tests/c/run-mutants.mjs --self-test && node tests/c/run-mutants.mjs` | 0 | 4 self-tests; **142 of 142 killed** (9 new: C0-SES-01..04, C0-SHELL-01..05) |
| `cd apps/infrx-api && INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/realdb/stack.py` at `8264e104` | 0 | **12 of 12** pass. Code under `lib/` and this file are unchanged since. `docker ps -a` / `docker network ls` show nothing `app-c0` left behind. |
| WR-1 revised, applied temporarily: `next typegen && tsc --noEmit`, `eslint layout.tsx`, `pnpm test` | 0 / 0 / 0 | clean / clean / 393: 381 pass, 0 fail, 12 skip; reverted with `git checkout -- 'app/(console)/layout.tsx'` |
| WR-1 as first written, applied temporarily: `node --test --test-name-pattern="never redirects to onboarding" tests/c/client-boundary.test.ts` | 1 | 0 pass, 1 fail: the guard catches the defect; reverted |

Isolation is the same as before: `infrx-app-c0-*` containers and network on 127.0.0.1:55451. No hosted Supabase, pilot box, AWS or SSM was used.

### Wiring requests (revised; supersede WR-1 and WR-4 above)

- **WR-1 (revised): `apps/app/app/(console)/layout.tsx`, the whole file.** Proof: `tests/c/client-boundary.test.ts` "the console layout never redirects…", which is already committed and becomes unconditional once the layout reads `consumerSession()`. `tests/c/credits.test.ts` already accepts the `sidebarCredit` form. Also the unit and real-stack `consoleShell` cases. `tsc`, `eslint` and `pnpm test` are green with it applied.
  ```tsx
  import { redirect } from "next/navigation";
  import { ConsoleDataUnavailable } from "@/components/console-data-state";
  import { Sidebar } from "@/components/sidebar";
  import { consoleShell } from "@/lib/services/console";
  import { sidebarCredit } from "@/lib/services/credits";
  import { consumerSession } from "@/lib/services/server";
  import { getSession } from "@/lib/session"; // the operator flag only (WR-6)

  // Every console page reads Supabase with the user's cookie: never prerender.
  export const dynamic = "force-dynamic";

  // Set each to its path in the commit that ships the route (A2: /verify-email; C3A/A2: /onboarding).
  // Until then the state gets a fixed panel: a redirect to a missing route is a 404.
  const ROUTES = { verifyEmail: null, onboarding: null };

  export default async function ConsoleLayout({ children }: LayoutProps<"/">) {
    const session = await consumerSession();
    const { state } = session.context;
    // Only a signed-in, verified user has an operator flag worth reading. An operator with no consumer
    // wallet still gets the shell, so /admin (which checks the role itself) stays reachable.
    const isOperator = state === "ready" || state === "onboarding" ? (await getSession()).isOperator : false;
    const shell = consoleShell(session, isOperator, ROUTES);
    if (shell.kind === "redirect") redirect(shell.to);
    if (shell.kind === "panel") {
      return (
        <main className="px-4 py-6 md:px-8 md:py-8">
          <div className="mx-auto max-w-6xl">
            <ConsoleDataUnavailable title="Your account" />
          </div>
        </main>
      );
    }
    // `null` when the wallet could not be read, or an operator has none: fixed copy, never a zero.
    const balance = shell.reads === null ? null : sidebarCredit(await shell.reads.balance());

    return (
      <div className="flex min-h-svh flex-col md:flex-row">
        <Sidebar email={shell.email} balance={balance} isOperator={isOperator} />
        <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    );
  }
  ```
  Set `ROUTES.verifyEmail` in the same commit that ships A2's `/verify-email`, and `ROUTES.onboarding` in the one that ships C3A/A2's onboarding route. Until then those states show the fixed panel. `getSession()` is read only for `ready`/`onboarding` users, and only for the operator flag (WR-6). The 0001 signup trigger gives every user a membership, so its first-membership pick cannot throw here. The operator-flag read moves to U3/A2's explicit lookup once WR-6 lands.
- **WR-4 (restated, exact): `Makefile`.** Add `console-c0-real` to `.PHONY`, and add:
  ```make
  # C0 CONSOLE-TENANT through real Supabase PostgreSQL + PostgREST (Docker; fails visibly without it).
  # Gate for C0 / APP-M1 and E3A; rerun on the merged SHA once 0022 lands (WR-7).
  console-c0-real:
  	cd $(API) && INFRX_D_TASK=app-c0 INFRX_D1_IMAGE=supabase uv run --frozen python ../app/tests/c/realdb/stack.py
  ```
  Proof: the table above, exit 0, 12 of 12. It is not added to `check`, because `check` must not need Docker beyond lists that skip visibly. The coordinator names it in the C0/APP-M1 and E3A gate commands instead (0-C0-V2).
- WR-2, WR-3, WR-5, WR-6 and WR-7 are unchanged. WR-3 adds `ConsumerSession`, `ConsumerClient` and `ConsoleShell` to the types to promote.

### Open after this round

- 0-C0-V2 and 0-C0-V3 close only when the coordinator lands WR-4 in the gate and WR-1/WR-2 in the pages. C0/APP-M1 closure stays held until then.
- The layout panel for `unverified` and `onboarding` reuses `ConsoleDataUnavailable` ("not available yet") until A2 and C3A ship their routes. That is honest but generic copy. It is A2/C3A's to replace by setting `ROUTES`.

### Remaining effort (C0 to merged and wired)

| | Hours |
|---|---|
| Optimistic | 1 |
| Likely | 2 |
| Pessimistic | 4 |

Confidence is medium. The code, decisions and guards are done. What remains is the coordinator applying WR-1 (the file above) and WR-4, U1R/U4 applying WR-2, and a rerun of `console-c0-real` plus the App suites on the merged SHA with 0022.

### Verification log (fix round)

- 2026-09-25T22:40Z: fix round appended at code head `ffd5ba7c`; every command in the fix-round table rerun at that head (the real stack at `8264e104`, with the same `lib/` and stack test file).
