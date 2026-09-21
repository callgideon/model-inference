# C1 — Typed repositories, pagination and tenant query boundary

## Task and status

| Field | Value |
|---|---|
| Task | C1 (track C, wave 2) |
| Owner / session | Claude Opus 5 (1M context), session `01XbryjFN2xhdKwhuUQvbyFn` |
| Status | **implemented** — not integrated, not live-verified |
| Why not integrated | The named queries are written against `06-database-map.md`'s relations, which D1 owns and which do not exist yet, and against T3's ClickHouse projection. No PostgreSQL or ClickHouse client is in the console's frozen dependency set (08 §6), so the only executor in this repository is the track's in-memory port. Every statement below is **integration-pending**. |

## Source

| Field | Value |
|---|---|
| Base SHA | `8744418` (coordinator-recorded committed base) |
| Implementation SHA | `f8eb53e` |
| Branch | `codex/c1-console-repositories` |
| Worktree | `/home/rey/workspace/rey/code/model-inference/.claude/worktrees/codex-c1` |
| Integrated SHA | none |

Commits, oldest first:

```
5cc89e5 C1: named queries with a reserved, last-bound tenant parameter, and authenticated cursors
d868ab3 C1: the ConsoleServices read half behind the frozen interface
09805e6 C1: the console credits card reads the reconciled wallet, not the whole ledger
b78c6d3 C1: the exported conformance suite, this track's cases, and a mutation run that kills them
f8eb53e C1: consent history keys on 06's version column and is read bounded
```

## What was built

`createConsoleServices({ pg, ch, cursorSecret })` implements the frozen `ConsoleServices`
interface for the read side: `usage`, `usageSummary`, `usageDaily`, `balances`, `ledger`,
`keys.list`, `settings.get`, `traces`, `traceDetail`, `feedback.list`, `adminOrgs`, `adminAudit`,
`judgeRuns`. The mutating operations are C3's and `traceContent` is C2's; their **guards** run here
anyway (field allowlist, role, suspension scope, ownership resolution), and the operation then
returns `internal_error` naming the owning task instead of pretending to work. That is why a
cross-tenant `feedback.submit` or `keys.revoke` is already `not_found` and a member's
`settings.update` is already `forbidden`.

- **Trusted tenant.** `lib/services/server.ts` resolves `SessionContext` from `getSession()`; no
  operation accepts an organization id, and the only exceptions the contract allows
  (`adminGrant.target_org_id` and the other operator writes) are C3's. `buildPlan` refuses to build
  a tenant-scoped query without the session organization.
- **Named queries only.** `lib/services/query.ts` holds one registry: relation, select list, sort
  key, and the accepted filter *names* per query. A caller filter can reach only the column the
  registry names for it; anything else throws `QueryPlanError` before a statement exists.
- **Reserved tenant parameter, bound last.** The tenant predicate is appended after every caller
  predicate and its parameter is bound last under `__org_id`, in the PostgreSQL renderer
  (positional `$n`) and in the ClickHouse renderer (named `{name:Type}`). The ClickHouse parameter
  map therefore cannot lose its tenant to a merge order — the defect the handoff's gotcha names
  (“chQuery parameter merging must not let callers override bound org”).
- **Bounded authenticated cursors.** `lib/services/cursor.ts`: HMAC-SHA256 over the row's sort key
  and the query scope (tenant, operation, filters — *not* the page size, which the contract lets
  change mid-walk), base64url, constant-time comparison, 512-character ceiling. The secret is a
  constructor value; only `server.ts` reads `CONSOLE_CURSOR_SECRET`, by name, and fails closed when
  it is missing or shorter than 16 characters.
- **Hard limit ≤ 100.** `limit > 100`, `0`, negative and non-integer are `invalid_request`, never a
  clamp. Pages read `limit + 1` rows so “is there more” needs no count, and the extra row never
  escapes.
- **Wallet from summary columns.** `balances` reads `wallets.ledger_total` and
  `wallets.reserved_total` and reports `available = ledger_total − reserved_total`. `lib/credits.ts`
  no longer fetches and sums the whole ledger: it reads the reconciled summary, bounds the displayed
  rows to one page, and keeps working against the currently deployed schema (see *Migration*).
- **Server-only boundaries.** `lib/services/{query,cursor,console}.ts` import nothing from `next/*`,
  hold no client and read no environment variable, so they are loadable under `node --test`
  (R48) and carry no secret into a bundle. `lib/services/server.ts` is the only module that reads
  the environment or the cookie, and it throws when loaded in a browser — the same guard
  `lib/supabase/admin.ts` uses, because F2 froze the dependency set and `server-only` is not in it.
- **Projection, not spreading.** Every DTO is built field by field from named columns. The stored
  `by_operator` marker never leaves the service: a customer session reads `platform` as the actor of
  an operator's ledger entry, the principal of operator-submitted feedback and the `changed_by` of an
  operator's consent change (R41/R50); an operator session reads the real principal.
- **Nothing throws.** One wrapper at the boundary turns any escaped exception — a rejecting port, a
  stored total outside the money domain, a malformed JSON column — into a `Result` error with a fixed
  safe message carrying no connection string, key or exception text.

## Requirement coverage

Oracles: **TRACE-TENANT**, **DUR-RLS**, **CONSOLE-FLOWS** (partial — the write half of CONSOLE-FLOWS
is C3, and DUR-RLS's SQL-role half needs D1's migrations).

| Test ID / case | Exact invariant |
|---|---|
| `every tenant-scoped named query binds the tenant last, under the reserved name` | For all 16 named queries: a tenant-scoped query renders exactly one `__org_id` binding and it is the final one; an untenanted one renders none. |
| `a tenant-scoped query cannot be built without the session organization` | `buildPlan` throws for a missing or empty organization on every tenant-scoped query, so an untenanted read is unconstructible rather than empty. |
| `the tenant predicate is the last condition, after every caller filter` | With three caller filters bound, the last `where` condition is `e.org_id = $n` and the organization is the last bound value, bound exactly once. |
| `only the filters a named query declares can be bound, and never a reserved name` | `org_id`, `o.org_id`, `e.org_id` and `__org_id` are not bindable on `usage_page` or `traces_page`; `allowedFilters` is the whole accepted set. |
| `a ClickHouse parameter map cannot lose its tenant to a caller parameter` | `params.__org_id` is the session organization, assigned last; no other organization appears in the map; the projection's own `trace_mode <> 'off'` predicate is present as a bound constant. |
| `the rendered statements are the shipped SQL: one relation, named columns, bounded` | Each statement selects named columns (never `*`), from the 06 relation, ordered by its sort key and bounded by `limit`; the wallet statement derives `available` from the summary columns and never touches `credit_ledger`. |
| `a keyset bound compares the whole sort key, in the list's own direction` | `(created_at, id) < (…)` descending, `(name, org_id) > (…)` ascending — a row-wise comparison, so a timestamp tie neither skips nor repeats. |
| `a cursor round-trips only under its own scope and secret` | A cursor verifies only under its own tenant, operation, filter set and signing key. |
| `the page size is not part of the scope, so it may change mid-walk` | Scope ignores `limit` and `cursor`, is independent of field order, and treats `undefined` as absent. |
| `a caller who knows the format still cannot mint or edit a cursor` | Twelve shape-aware forgeries — hand-built payload under a stolen tag, no tag, empty tag, no payload, one changed tag character, oversized, and six authenticating-but-malformed payloads — all decode to null. |
| `a cursor carries no secret and no readable tenant` | The payload is the row's sort key and nothing else; the signing key appears nowhere in the cursor. |
| `an operator's identity never reaches a customer view, in any actor field (R41)` | Walking the whole ledger as the owner, an operator-made entry reads `platform` and the operator's address appears in no page; the same entry reads the real principal for an operator session; every entry that shows a principal is one the organization itself made. |
| `the same masking covers feedback and consent history, which only a write can create` | A `by_operator` feedback entry and consent change (seeded, because C3 owns the write) read `platform` for the owner in `feedback.list`, `traceDetail.feedback` and `settings.get`, and the real principal for an operator — while the feedback entry stays `author_role: customer` (R19). |
| `the balance comes from the wallet summary columns, not from a sum over the ledger` | Moving `wallets.reserved_total` alone changes `reserved_total` and lowers `available`; with no wallet row the balance is three zeros. |
| `a page is bounded by the contract's default and its look-ahead row never escapes` | An absent limit returns exactly `DEFAULT_PAGE_LIMIT` rows; each requested limit returns exactly that many; no page reached through a cursor is empty; a full walk returns exactly the row count the summary reports. |
| `usageDaily groups by UTC day, newest first, and its costs add up` | One row per `YYYY-MM-DD`, strictly descending, and the daily costs and request counts sum to `usageSummary`. |
| `a suspended organization reads everything and is refused only new work (R33)` | Every read and `keys.revoke` remain reachable for a suspended organization; `judgeRuns` is `org_suspended`; the revocation refusal is the unimplemented write, never the suspension. |
| `operator authority is the flag and never a substitute for the organization role` | A platform operator whose organization role is `member` is `forbidden` on `settings.update`, `keys.create` and `keys.revoke`, and allowed on `adminOrgs`, `adminAudit` and `judgeRuns`. |
| `an off-mode request has no trace row but still has a detail to open (R13)` | No `traces` row has `trace_mode: off` and the off-mode request id is absent from the list, while `traceDetail` on it reports `trace_mode`/`content` = `off`. |
| `a query port that fails is a Result error, never a thrown promise` | Ten read operations against a port that rejects with a connection string return `internal_error` with no connection detail in the message; a rejected promise fails the case as an assertion. |
| `the services hold no secret and no client: a cursor secret is required and never echoed` | Construction with a weak secret throws, so an unsigned (caller-writable) cursor cannot be reached. |
| `every conformance case C1 owns passes against the real services` | The 16 exported cases listed below pass against this adapter. |
| `every failing case fails only because an operation is not implemented in C1` | Every failing exported case's diagnostic names an unimplemented C2/C3 operation (or shows `internal_error` where the write's own body validation will refuse), so a tenant, pagination or projection defect cannot be filed as someone else's. |

Exported cases run against the real services (`node --test --test-reporter=tap
tests/c/conformance/console-services.conformance.ts`, exit 1, `# tests 45 / # pass 16 / # fail 29`):

```
  ok 1 - usage pages walk every row exactly once, newest first
  ok 2 - ledger and trace pages walk every row exactly once
  ok 3 - rejects a limit above the hard bound and a limit that is not a positive integer
  ok 4 - rejects a cursor it did not issue for this query
  ok 5 - balance is the ledger total minus reservations, and holds reduce what is available
  ok 6 - a new organization starts at zero with no ledger history
  ok 7 - identifiers from another organization are not_found, in both directions
  ok 8 - a filter or cursor from another organization never widens the tenant
  ok 9 - a filter outside its vocabulary is invalid_request, not an empty page
  ok 10 - a member can read but cannot mutate settings or keys
  ok 11 - a non-operator can neither grant nor see other organizations
  not ok 12 - feedback follows the R3 body, appears immediately, and has provenance the client cannot set
  not ok 13 - retention is capped and evaluation consent is a separate control
  not ok 14 - an operator grant is idempotent per key and conflicts on a changed payload
  not ok 15 - every amount the money domain rejects is rejected by the grant boundary too
  not ok 16 - the provisional input bounds hold
  not ok 17 - content availability decides the payload and never leaks a storage reference
  ok 18 - the HTTP status table is the one 08 §3 freezes, code for code
  not ok 19 - suspension gates new work and configuration, and nothing else (R33)
  not ok 20 - the role and suspension matrix holds for every operation
  not ok 21 - an operator acts on the organization it names, not on its own (R26)
  not ok 22 - a suspension can be lifted, and lifting it restores exactly what it gated
  not ok 23 - suspending and restoring an organization with real accounting changes none of it
  not ok 24 - entitlement limits are replaced, not merged
  not ok 25 - the operator's own lists page at a small limit too
  not ok 26 - a fresh read of the ledger puts a new grant in its place
  not ok 27 - the three entitlement states are distinct, and the empty one is a denial (R24)
  not ok 28 - an operator suspension needs a reason within the bound
  not ok 29 - no customer view ever names an operator (R41)
  not ok 30 - an operator write is audited, and a restore adds an entry instead of erasing one (R34)
  not ok 31 - an operator label is the only path to operator authorship and calibration membership
  not ok 32 - a created key's secret is shown once and never again, to anybody
  ok 33 - judge runs separate estimates, limited evaluations and held budgets
  not ok 1 - an amount that would leave the money domain is refused, and nothing is written
  not ok 2 - every mutating operation validates before it writes, whichever field is bad
  not ok 3 - an idempotency key is scoped to its operation, its organization and its payload
  not ok 4 - generated identifiers never collide, within an organization or across two
  not ok 5 - a list walked while rows arrive at its head still returns every row exactly once
  not ok 6 - a filtered walk survives the cursor's own row leaving the filter, and stays complete
  not ok 7 - nothing creates a legacy purchase entry
  not ok 8 - a calibration label is operator data a customer never sees (R35)
  ok 9 - no operation accepts a field the caller invented
  not ok 10 - reads and writes never cross the tenant boundary, in either direction
  ok 11 - a filter filters, and an absent filter does not
  ok 12 - the operator list pages like every other list
```

**These 29 are not skips and not passes.** Each one reaches an operation this task does not
implement and is asserted to fail *for that reason* by
`tests/c/console-conformance.test.ts`. The cause per case: `feedback.submit` (12, 31, mutation 4, 6),
`adminGrant` (14, 15, 21, 26, 29, 30, mutation 1, 3, 5, 7), `settings.update` (13, mutation 10),
`keys.create` (16, 32), `traceContent` (17, 19, 20, mutation 8), `adminSetSuspension` (22, 23, 28),
`adminSetEntitlements` (24, 27), `calibration.label`/`calibration.list` (25, mutation 2). Cases 19,
20 and mutation 8 are blocked *only* by `traceContent` (C2) and should pass when C2 lands, before C3.

## Environment

| Field | Value |
|---|---|
| Host | Linux 7.0.0-1010-aws x86_64 (the `g6e.2xlarge` dev box), local only |
| Node | v22.23.1 (type stripping, `engines.node >=22.18`) |
| pnpm | 9.15.9 (`packageManager`), dependencies installed with `--frozen-lockfile` |
| Python | `apps/infrx-api/.venv` via `uv sync --frozen --all-extras` (baseline only; no Python changed) |
| Services | **none**. No container was created, no port opened, nothing contacted. No PostgreSQL, ClickHouse, Supabase, AWS or paid provider. The reserved task-local port C1 `55441` (R48) was not used, because no real-service test exists yet. |
| Classification | local Layer 1 (fakes and an in-memory port). Layer 2 is pending on D1/T3. |
| Seed | none: no randomness; the fixture clock is frozen and the harness derives everything from it. |

## Commands

UTC window `2026-09-21T06:28:18Z` – `2026-09-21T06:29:05Z`, all run from the worktree root
(`node tests/c/run-mutants.mjs` from `apps/app`). Environment variable names referenced by the code:
`CONSOLE_CURSOR_SECRET` (unset here; the tests pass a value in).

```
=== make console-test
exit=0
1..64
# tests 150
# suites 4
# pass 150
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 818.636577
=== make console-lint
exit=0

/home/rey/…/apps/app/lib/contracts/conformance.ts
  3924:35  warning  'ids' is assigned a value but never used  @typescript-eslint/no-unused-vars

/home/rey/…/apps/app/lib/contracts/fake-services.ts
  80:8  warning  'AuditQuery' is defined but never used  @typescript-eslint/no-unused-vars

✖ 2 problems (0 errors, 2 warnings)

=== make console-typecheck
exit=0
cd apps/app && pnpm exec next typegen && pnpm exec tsc --noEmit
Generating route types...
✓ Types generated successfully
=== make api-test
exit=0
670 passed, 2 warnings in 28.74s
=== node tests/c/run-mutants.mjs --jobs 4
exit=0
baseline: 37 cases pass unmutated, 31 fail (C2/C3 operations this task does not implement); 25 mutants, 4 at a time

25 mutants: 25 killed by a named declared case, 0 survived, 0 stale, 0 runner errors, 7.0s
=== node --test --test-reporter=tap tests/c/conformance/console-services.conformance.ts
exit=1
# tests 45
# pass 16
# fail 29
```

`pnpm build` (from `apps/app`) also completed, which is what exercises `lib/credits.ts` on the
server-rendered `/billing` and `/usage` routes; its route table is unchanged from the base.

The two lint warnings are in coordinator-owned files and predate this task (`git log` shows both
files untouched by these commits). `make console-test` is green **including** the child-process
conformance run, which is why the 29 unimplemented cases do not redden the canonical command.

## Results

- `make console-test`: 150 tests, 150 pass, 0 fail, 0 skipped. 24 of them are this task's
  (`tests/c/console-conformance.test.ts` 3, `cursor.test.ts` 4, `query-boundary.test.ts` 7,
  `read-services.test.ts` 10).
- Exported conformance against the real services: 45 cases, 16 pass, 29 fail — every failure
  attributable to a C2/C3 operation, asserted case by case.
- Mutants: **25 declared, 25 killed by a named declared case, 0 survived, 0 stale, 0 runner
  errors.** The list (`tests/c/mutants.json`) covers the five areas the brief names and three more
  the same boundary owns: tenant binding (TENANT-01…05), role check (ROLE-01…03), limit bound
  (LIMIT-01), cursor MAC and scope (CURSOR-01…03), projection (PROJECT-01…04), wallet identity
  (WALLET-01), suspension scope in both directions (SUSPEND-01/02), the field and filter allowlists
  (FIELD-01, FILTER-01/02), pagination termination and the look-ahead row (PAGE-01/02), and the
  no-throw boundary (GUARD-01).
- `make api-test`: 670 passed — unchanged baseline; this task touched no Python.

Two mutants initially reported honestly against me and were fixed rather than re-declared:
`PAGE-01` (a cursor minted on the exhausted page) survived, because no case asserted that a page
reached through a cursor is non-empty — the assertion was added; and `GUARD-01` was a *runner error*,
because my assertion message contained the phrase the runner reserves for “an exception in
disguise”, so the kill could not be attributed — the message was reworded. Both now kill on an
assertion.

## Failure drill

There is no durable state to drill yet (the executor is in-memory and D1's relations do not exist),
so the drills that were possible are the ones the boundary itself can fail at:

| Injection | Before | After | Retry behaviour | Cleanup |
|---|---|---|---|---|
| Query port rejects with a connection string containing a password | in-memory dataset unchanged | unchanged (reads only) | every one of ten read operations returns `internal_error`; nothing throws; the message carries no connection detail | nothing to clean: no transaction, no file, no container |
| Forged/foreign/edited cursor (17 variants across two suites) | dataset unchanged | unchanged | `invalid_cursor`; the caller restarts the walk from the first page and gets the same ordered rows | none |
| `wallets.reserved_total` moved under the service | ledger rows unchanged | balance follows the summary column | repeat reads are stable | none |
| Cross-tenant request id / key id / cursor / filter | dataset unchanged | unchanged | `not_found` or `invalid_cursor`, in both directions between the two organizations | none |
| Suspended organization | dataset unchanged | unchanged | reads succeed; `judgeRuns` is `org_suspended`; `keys.revoke` is reached | none |

The drills the task cannot perform yet, and who owns them: RLS role attacks against protected
columns (DUR-RLS's SQL half — D1's migrations and grants), a killed connection mid-page against real
PostgreSQL, ClickHouse projection lag and TTL expiry (T3), and crash-after-commit on a mutation
(C3, and fake-only in the contract by the README's own note).

## Artifacts

All in the worktree at `f8eb53e`; no external artifact store, no raw log kept outside it.

| Path | What |
|---|---|
| `apps/app/lib/services/query.ts` | Named-query registry, plan construction, PostgreSQL and ClickHouse renderers |
| `apps/app/lib/services/cursor.ts` | Authenticated opaque cursors |
| `apps/app/lib/services/console.ts` | The read half of `ConsoleServices`, guards, projections |
| `apps/app/lib/services/server.ts` | Server-only session resolver and environment read |
| `apps/app/lib/credits.ts` | Reconciled wallet for the console's credits card |
| `apps/app/tests/c/harness.ts` | In-memory port and the dataset seeded from the shared fake |
| `apps/app/tests/c/conformance/console-services.conformance.ts` | The exported suites against the real services (deliberately not `*.test.ts`) |
| `apps/app/tests/c/console-conformance.test.ts` | Runs the above in a child process and pins pass/fail attribution |
| `apps/app/tests/c/{cursor,query-boundary,read-services}.test.ts` | This track's cases |
| `apps/app/tests/c/{mutants.json,run-mutants.mjs}` | R32 mutant list and runner |

No credential, customer prompt, signed URL or private video appears in any of them. `pnpm-lock.yaml`,
`package.json`, `lib/contracts/**`, `tests/contracts/**`, `research/plan/tasks.json` and every
composition root are untouched.

## The shipped SQL (integration-pending)

Rendered from the registry, with placeholder filter values and a cursor, to show the binding order.
`__org_id` is last in every tenant-scoped statement. Regenerate with the registry; nothing in this
repository executes it yet.

```sql
-- org_status  (pg; params in binding order: __org_id)
select o.id as org_id, o.name, o.suspended, o.suspension_reason
  from public.organizations o
 where o.id = $1
 limit 1;

-- wallet_summary  (pg; params: __org_id)
select w.ledger_total, w.reserved_total, (w.ledger_total - w.reserved_total) as available
  from public.wallets w
 where w.org_id = $1
 limit 1;

-- ledger_page  (pg; params: k_at, k_id, __org_id)
select l.id, l.created_at, l.delta_usd as delta, l.kind, l.reason, l.ref, l.actor, l.by_operator
  from public.credit_ledger l
 where (l.created_at, l.id) < ($1, $2)
   and l.org_id = $3
 order by l.created_at desc, l.id desc
 limit 26;

-- usage_page  (pg; params: f0=from, f1=to, f2=key_id, f3=model, k_at, k_id, __org_id)
select e.id as request_id, e.created_at, e.model_id as model, e.api_key_id as key_id,
       k.name as key_name, e.execution_mode, e.job_state, e.terminal_cause, e.status as http_status,
       e.prompt_tokens, e.completion_tokens, e.usage_certainty, e.settlement_state,
       e.cost_usd as cost, h.max_amount_usd as max_hold, e.trace_mode
  from public.usage_events e
       join public.api_keys k on k.id = e.api_key_id
       left join public.credit_holds h on h.request_id = e.id and h.state in ('held', 'unknown')
 where e.created_at >= $1 and e.created_at <= $2 and e.api_key_id = $3 and e.model_id = $4
   and (e.created_at, e.id) < ($5, $6)
   and e.org_id = $7
 order by e.created_at desc, e.id desc
 limit 26;

-- usage_summary  (pg; params: f0..f2 aggregate constants, f3..f6 filters, __org_id)
select count(*) as requests,
       count(*) filter (where e.status >= $1) as failed_requests,
       coalesce(sum(e.prompt_tokens), 0) as prompt_tokens,
       coalesce(sum(e.completion_tokens), 0) as completion_tokens,
       coalesce(sum(e.cost_usd), 0) as cost,
       coalesce(sum(h.max_amount_usd) filter (where e.usage_certainty = $2
                                                and h.max_amount_usd is not null), 0) as pending_reconciliation,
       count(*) filter (where e.settlement_state = $3) as platform_absorbed_requests
  from public.usage_events e
       join public.api_keys k on k.id = e.api_key_id
       left join public.credit_holds h on h.request_id = e.id and h.state in ('held', 'unknown')
 where e.created_at >= $4 and e.created_at <= $5 and e.api_key_id = $6 and e.model_id = $7
   and e.org_id = $8
 limit 1;

-- usage_daily  (pg; params: f0..f3 filters, __org_id)
select (e.created_at at time zone 'utc')::date as day, count(*) as requests,
       coalesce(sum(e.prompt_tokens), 0) as prompt_tokens,
       coalesce(sum(e.completion_tokens), 0) as completion_tokens,
       coalesce(sum(e.cost_usd), 0) as cost
  from public.usage_events e
       join public.api_keys k on k.id = e.api_key_id
       left join public.credit_holds h on h.request_id = e.id and h.state in ('held', 'unknown')
 where e.created_at >= $1 and e.created_at <= $2 and e.api_key_id = $3 and e.model_id = $4
   and e.org_id = $5
 group by 1
 order by 1 desc;

-- keys_list  (pg; params: k_at, k_id, __org_id)
select k.id, k.name, k.prefix, k.created_at, k.last_used_at, k.revoked_at, k.trace_mode
  from public.api_keys k
 where (k.created_at, k.id) < ($1, $2) and k.org_id = $3
 order by k.created_at desc, k.id desc
 limit 26;

-- key_by_id  (pg; params: f0=key id, __org_id)
select k.id, k.name, k.prefix, k.created_at, k.last_used_at, k.revoked_at, k.trace_mode
  from public.api_keys k
 where k.id = $1 and k.org_id = $2
 limit 1;

-- settings_get  (pg; params: __org_id)
select s.trace_mode, s.content_retention_days, s.evaluation_consent
  from public.org_settings s
 where s.org_id = $1
 limit 1;

-- consent_history  (pg; params: k_at, k_id, __org_id)
select c.version, c.changed_at, c.evaluation_consent, c.changed_by, c.by_operator
  from public.consent_history c
 where (c.changed_at, c.version) > ($1, $2) and c.org_id = $3
 order by c.changed_at asc, c.version asc
 limit 100;

-- feedback_by_request  (pg; params: f0=request id, k_at, k_id, __org_id)
select f.id, f.request_id, f.created_at, f.channel, f.author_role, f.author_principal,
       f.name, f.value, f.comment, f.calibration_set, f.rubric_version, f.by_operator
  from public.feedback f
 where f.request_id = $1 and (f.created_at, f.id) > ($2, $3) and f.org_id = $4
 order by f.created_at asc, f.id asc;

-- judge_runs_page  (pg; params: k_at, k_id, __org_id)
select r.id, r.created_at, r.state, r.mode, r.rubric_version, r.judge_model,
       r.judge_model_version, r.sample_count, r.limited_evaluation_count, r.budget_reserved,
       r.budget_settled, r.consent_snapshot_at, r.external_batch_id, r.quarantine_reason, r.samples
  from public.console_judge_runs r
 where (r.created_at, r.id) < ($1, $2) and r.org_id = $3
 order by r.created_at desc, r.id desc
 limit 26;

-- admin_orgs_page  (pg; operator-only, deliberately untenanted per R26; params: k_at, k_id)
select o.org_id, o.name, o.owner_email, o.created_at, o.suspended, o.suspension_reason,
       o.ledger_total, o.reserved_total, o.requests_30d, o.model_ids, o.limits,
       o.entitlements_updated_at, o.entitlements_updated_by
  from public.console_admin_orgs o
 where (o.name, o.org_id) > ($1, $2)
 order by o.name asc, o.org_id asc
 limit 26;

-- admin_audit_page  (pg; operator-only; params: f0=target_org_id, k_at, k_id)
select a.id, a.at, a.actor_principal, a.action, a.target_org_id, a.reason, a.before,
       a.after, a.idempotency_key
  from public.operator_audit a
 where a.target_org_id = $1 and (a.at, a.id) < ($2, $3)
 order by a.at desc, a.id desc
 limit 26;

-- traces_page  (clickhouse; params: f0='off' constant, f1..f7 filters, k_at, k_id, __org_id)
select request_id, created_at, model, key_id, job_state, http_status, trace_mode, content,
       loss_reason, prompt_tokens, completion_tokens, ttft_ms, wall_ms, cost, feedback_count,
       score_count, execution_mode, terminal_cause, error_code, usage_certainty, settlement_state,
       auth_ms, media_ms, admit_ms, queue_ms, gateway_version, model_revision,
       price_snapshot_version, trace_schema_version, content_expires_at, metadata_expires_at
  from infrx.trace_metadata
 where trace_mode <> {f0:String}
   and created_at >= {f1:String} and created_at <= {f2:String}
   and key_id = {f3:String} and model = {f4:String} and job_state = {f5:String}
   and trace_mode = {f6:String} and content = {f7:String}
   and (created_at, request_id) < ({k_at:String}, {k_id:String})
   and org_id = {__org_id:String}
 order by created_at desc, request_id desc
 limit 26;

-- trace_by_request  (clickhouse; params: f0=request id, __org_id)
select … same columns …
  from infrx.trace_metadata
 where request_id = {f0:String} and org_id = {__org_id:String}
 limit 1;
```

Relations these statements assume, beyond what `0001_init.sql` already has: `wallets`,
`credit_holds`, `org_settings`, `consent_history`, `feedback`, `operator_audit`, new
`usage_events` columns (`execution_mode`, `job_state`, `terminal_cause`, `usage_certainty`,
`settlement_state`, `trace_mode`, `cost_usd` at `numeric(20,8)`), plus two read views
(`console_admin_orgs`, `console_judge_runs`) and ClickHouse `infrx.trace_metadata`. Column names are
this task's proposal; D1 and T3 own the truth and any rename is a one-line registry change.

## Changes

Owned paths only:

```
$ git diff --numstat 8744418..HEAD
55	12	apps/app/lib/credits.ts
906	0	apps/app/lib/services/console.ts
64	0	apps/app/lib/services/cursor.ts
600	0	apps/app/lib/services/query.ts
63	0	apps/app/lib/services/server.ts
18	0	apps/app/tests/c/conformance/console-services.conformance.ts
102	0	apps/app/tests/c/console-conformance.test.ts
72	0	apps/app/tests/c/cursor.test.ts
259	0	apps/app/tests/c/harness.ts
222	0	apps/app/tests/c/mutants.json
132	0	apps/app/tests/c/query-boundary.test.ts
281	0	apps/app/tests/c/read-services.test.ts
239	0	apps/app/tests/c/run-mutants.mjs
```

No contract change and no contract revision request. No new dependency. No file outside
`apps/app/lib/services/`, `apps/app/lib/credits.ts`, `apps/app/tests/c/` and this report.

### Migration and rollback

- **No migration is authored here** (D alone writes them). `lib/credits.ts` is the only runtime path
  that changes, and it is written expand/contract: with `org_wallet_summary` absent — the schema
  deployed today — it falls back to the existing `org_balance` function and reports `reserved: 0`,
  which is correct in a schema that has no holds. So the previous deployed runtime keeps working and
  the new one is correct after D1, with no flag day.
- **Rollback** is reverting these commits: nothing writes, nothing migrates, no durable state is
  touched, and the cursors are stateless (rotating `CONSOLE_CURSOR_SECRET` invalidates outstanding
  cursors, whose callers restart their walk — which is the intended effect of a rotation).

## Limits

Unverified or out of scope, with the owner:

1. **Every statement is integration-pending (owner: D1, T3, coordinator).** No SQL has been executed.
   The in-memory port interprets the same declarative plan the renderers emit, so plan construction,
   binding order, filtering, keyset paging and limits are tested — but the *SQL text* is verified only
   by inspection. Column and relation names are this task's proposal against 06 and will need
   reconciling with D1's migrations and T3's DDL.
2. **No real executor exists (owner: coordinator).** The console's dependency set is frozen and has
   no PostgreSQL or ClickHouse client. See the integration requests below: either a pinned driver or
   D1-owned SQL functions.
3. **Aggregate drift (owner: C1 at integration).** `usage_summary` and `usage_daily` are declared once
   and interpreted twice — by the SQL renderer and by the in-memory port. A disagreement between the
   two can only be caught against real PostgreSQL, which is part of the Layer-2 run.
4. **`credits.ts` display figures (owner: C1/U).** `loaded` and `spent` come from the wallet summary
   after D1; on the pre-D1 fallback path they are computed from the bounded first page, so an
   organization with more than 100 ledger rows would see understated totals *on that path only*. The
   `available` figure is never affected. `Credits` still crosses as `number` because
   `components/credits-card.tsx` is not this task's file; the typed boundary (`balances`, `ledger`)
   uses `Money` strings throughout.
5. **`traceDetail` on an off-mode request (owner: T3/C2).** It resolves through the projection row,
   which means T3 must keep a metadata row for off-mode requests, or C2 must add the usage-row
   fallback. R13 says an off-mode request has no trace row in the *list*; where the detail's
   availability `off` comes from is the open question. Integration request below.
6. **`adminAudit` returns an empty page here**, because nothing in a read-only adapter appends an
   audit entry. Its pagination is exercised by the registry and cursor tests, not by rows. C3's
   writes make the exported audit cases meaningful.
7. **`traceContent`, every mutation, and the R43/R54 body bounds are not implemented** (C2, C3). The
   29 failing exported cases above are the exact list.
8. **DUR-RLS is half-covered.** Server-side authorization is tested; SQL-role and RLS behaviour
   against protected columns needs D1's grants and a real database (D1/E3).
9. **`server.ts` has no caller yet.** U and V wire the pages; C3 wires the actions. The browser guard
   is the `typeof window` check rather than the `server-only` package, which F2's frozen dependency
   set excludes.
10. **Cursor secret rotation has no grace period.** Rotating invalidates outstanding cursors
    immediately (callers restart the walk). A two-key scheme is not worth it for a page cursor.

## Handback

**Next unblocked task:** C2 (content access, expiry and safe signed references) — its start
dependency is C1, and it unblocks conformance cases 19, 20 and mutation-safety 8 on its own. C3
(mutations) is equally unblocked for coding and is the larger remaining share of the exported suite;
it should lift the read helpers rather than re-check ownership (`ownedTrace`, `ownedKey`, `tenant`,
`requireRole`, `badInput` in `lib/services/console.ts`).

**Integration requests (nothing outside my owned paths was touched):**

1. **A query executor.** Either (a) a pinned PostgreSQL driver for the console
   (`pg` or `postgres`) plus a ClickHouse HTTP client — a dependency decision only the coordinator
   makes, since `package.json` and the lockfile are coordinator-owned; or (b) D1-owned SQL functions
   per named query, reached through the existing `@supabase/supabase-js` client, which needs no new
   dependency and keeps RLS in play. (b) is the cheaper route and matches the existing
   `org_usage_summary`/`org_balance` style, but it makes D1 the owner of all sixteen statements.
   The port interface either one implements is `QueryPort = { run(plan: QueryPlan): Promise<Row[]> }`
   from `apps/app/lib/services/query.ts`.
2. **To D1** — the relations and columns listed under *The shipped SQL*, plus:
   - a reconciled `public.org_wallet_summary(p_org uuid) returns table (ledger_total numeric,
     reserved_total numeric, loaded numeric, spent numeric)`, `security invoker`, guarded like the
     existing `org_balance` (`is_org_member(p_org) or is_operator()`), with
     `grant execute … to authenticated`. `lib/credits.ts` calls it today and falls back to
     `org_balance` until it exists.
   - the `by_operator` column on `credit_ledger`, `feedback` and `consent_history` (R50), and an
     `actor` column on `credit_ledger`; the read projection depends on both.
   - `console_admin_orgs` and `console_judge_runs` read views (or a renamed equivalent) so the
     operator list and the judge list stay single named queries.
3. **To T3** — whether `infrx.trace_metadata` retains a row for `trace_mode = 'off'` requests. If it
   does not, C2 needs a usage-row fallback for `traceDetail`; if it does, the list's constant
   predicate (already in place) is what keeps them out of trace coverage.
4. **Configuration name** for `infrx/config.py`'s console counterpart: `CONSOLE_CURSOR_SECRET`
   (required wherever the console serves lists; the service refuses to construct without it). It is
   read only in `apps/app/lib/services/server.ts`.
5. **Optional, coordinator's call:** a `console-c1-mutants` Makefile target for
   `node tests/c/run-mutants.mjs`, alongside `console-mutants`. It is not wired into `make check`
   from here, because the root `Makefile` is coordinator-owned.

**Unresolved findings:** none outstanding against this task. Two invariants were initially
unenforceable and were fixed during the mutation run (recorded under *Results*); one claim was
deliberately *not* declared as a mutant — the reserved-parameter-name check in `buildPlan` is
redundant defence behind the filter allowlist, so removing it alone changes no observable behaviour,
and the killable invariant is the allowlist (`TENANT-03`).

## Verification log

- 2026-09-21: Implemented C1 on `codex/c1-console-repositories` from base `8744418`. No service,
  container, cloud resource or paid provider was contacted; nothing was pushed or deployed. The
  exported console conformance suite was run against the real services and reported case by case;
  16 of 45 pass and the other 29 are attributed to C2/C3 operations by an executable check, not by
  assertion in prose.

---

# Appendix — review round 1 (fix_required at `42e5669`)

Appended, not rewritten. Implementation SHA of the fixes: **`b978c81`**. Same worktree, same branch
(`codex/c1-console-repositories`), same owned paths; nothing pushed, no service contacted, no
container created.

| Commit | What |
|---|---|
| `e69cd5c` | B1, B2, B6 in `lib/services/**` and `lib/credits.ts` — **and** the D1 rename pass, the hard caps and the credits extraction, which its message does not name (recorded here because an amend is not allowed) |
| `b978c81` | the cases the review found missing, the harness seeding, and the runner's classification fixes |

## Per-item disposition

| # | Finding | Disposition |
|---|---|---|
| **B1** | `feedback_by_request` returned calibration labels to owner *and* operator, with the operator's principal | **fixed.** The named query carries `calibration_set = false` as a *constant of the query* (like the trace list's off-mode exclusion), so no caller and no forgotten filter can drop it; labels are read only through the calibration listing (C3). Masking is now fail-closed — `masked()` returns true unless `by_operator === false` — so a D1 function that omits the column hides principals rather than publishing them. The harness seeds `org.labels` into the feedback relation (dropping them is exactly why the leak was invisible), and the new case drives owner, member and operator. Mutants `LABEL-01`, `MASK-01`, `PROJECT-01..03`, `ROLE-04`. |
| **B2** | `spec.filters?.[name]` accepted `constructor`/`toString`/`valueOf`/`hasOwnProperty`/`isPrototypeOf`/`__proto__` and rendered `where undefined undefined $1` | **fixed.** `Object.hasOwn` everywhere an untrusted string indexes an object (swept `query.ts`, `console.ts` — `cell()` — and `cursor.ts`, which indexes nothing). `buildPlan` now range-checks `limit` (it is interpolated, not bound), requires a non-empty **string** tenant, refuses an organization on an operator-wide query, refuses a non-scalar filter value and a keyset that is not two strings. Both dead `RESERVED_PARAM_NAMES` checks removed; the invariant they guessed at ("no named query declares a filter under a reserved name") is a test instead. `session.isOperator === true` in the role check and in the masking decision. The evidence claim that provoked this is corrected below. Mutants `PROTO-01`, `ARG-01`, `ARG-02`, `ROLE-01`, `ROLE-04`. |
| **B3** | the tenant proof was vacuous for three queries | **fixed.** The tenanted set is a **literal list** in the test — 14 tenant-scoped, 2 operator-wide — so `tenantColumn: null` anywhere fails on a `deepEqual` rather than being agreed with. New own case "the single-row reads are tenant-bound too, in both directions" covers `settings.get`, consent history, `feedback.list`, `traceDetail` and key ownership with `sessions.otherOwner`. Mutants `TENANT-03`, `TENANT-04`. |
| **B4** | cursor scope proved for `usage` only; the "malformed payload" forgeries never authenticated; the over-long case died on the MAC | **fixed.** New case pairs **all six paged lists** in both directions (30 ordered pairs) and swaps filters per filtered list; the malformed payloads are signed with a **real HMAC** computed in the test (nine of them, including `"a"`, `null` and `["a",""]`), so the shape validation is what refuses them; the length bound is pinned with a genuine over-long cursor of the service's own making plus one just inside. The 15/16-character secret boundary is pinned on both sides. Mutants `CURSOR-01..05`, `SCOPE-01`, `SCOPE-02`, `SECRET-01`. |
| **B5** | "both renderers, bounded" proved for PostgreSQL only | **fixed.** One case loops **every page query** through **both** renderers and asserts the bound, the whole-sort-key order, the row-wise keyset comparison *in its own direction*, and the tenant as the final condition and last binding. `consent_history`'s cap is asserted with 150 seeded versions. Mutants `CH-01..04`, `CAP-01`, `TENANT-02`. |
| **B6** | nested JSON passed through unprojected; `credits.ts` had no test | **fixed.** `judgeSampleOf`/`judgeScoreOf` project field by field against the DTO (a seeded `org_id`, `provider_batch_secret` and `labelled_by` on a sample no longer reach an owner, asserted); entitlement limits come from the closed `ENTITLEMENT_LIMIT_NAMES` set with integer bounds; `json()` fails closed so a PostgreSQL array literal cannot read as `null` and invert R24; `text`/`integer`/`timestamp`/`flag`/`money` refuse instead of coercing (`String(undefined)` → blank cell, `Number("abc")` → NaN, a driver `Date` → a corrupted cursor key). The credits computation moved to `lib/services/credits.ts` (relatively importable, so `node --test` can load it) with both branches tested, `subMoney` on scaled integers, and a case a float cannot answer. **An RPC error no longer falls back**: only a missing function does (`PGRST202`, `42883`, "could not find the function"), because `org_balance` ignores holds and would report an inflated available balance. Mutants `JUDGE-01`, `ENT-01`, `STRICT-01..04`, `KEYNAME-01`, `CREDITS-01`, `CREDITS-02`. |
| **Ruling (1)** | executor = D1's views/functions through supabase-js | **accepted and renamed** — see *D1 rename pass* below. `QueryPort` stands. |
| **Ruling (2)** | no `server-only` dependency; static client-import test + module-scope guards | **done.** `tests/c/client-boundary.test.ts` walks every `"use client"` module's transitive imports (relative and `@/`, including `export … from` and dynamic `import()`) and fails if one reaches `lib/services/**`; it also asserts the walker finds client modules and can resolve an import, so it cannot pass by finding nothing. Module-scope `typeof window` guards in `query.ts`, `console.ts`, `cursor.ts` (and `server.ts` keeps its function-scope one). Mutant `CLIENT-01`. |
| **Ruling (3)** | hard caps where the contract has no pagination | **partly done, one item declined with a reason.** `keys_list` 100, `feedback_by_request` 100, `consent_history` 100, `usage_daily` 400 (13 months of metadata retention), declared on the named query as `hardLimit` and asserted. **`usageSummary`/`usageDaily` still accept a missing `from`/`to`**: the exported conformance calls `usageSummary(owner, {})` and `usageDaily(suspended, {})` and requires both to succeed (cases 5, 19, and the mutation-safety filter case), so refusing would fail the frozen suite. Both are single-row/bounded-row aggregates over an indexed `(org_id, created_at)` range rather than row transfers, and `usage_daily`'s output is now capped. Making the range mandatory is a contract revision in `services.ts` + `conformance.ts`; it is in the integration requests. |
| **Ruling (4)** | `CONSOLE_CURSOR_SECRET` | accepted, unchanged. |
| **Cheap ones** | audit target filter, `has_feedback` both ways, identifier length, strict RFC 3339, `pending_reconciliation` certainty | **all pinned.** Audit entries are seeded (12 per organization) so `adminAudit` pages and filters for real; `has_feedback` true/false partition the list exactly; `key_id`/`model` length and emptiness; `2026-09-01`, `2026-09-01T12:00:00`, `+02:00` and `now` are all `invalid_request` (`Date.parse` accepts the first); an *authoritative* hold does not move `pending_reconciliation` and flipping the same row to `unknown` does. Mutants `AUDIT-01`, `FEEDBACK-01`, `FILTER-03`, `FILTER-04`, `SUMMARY-01`. |
| **Runner** | a syntax error read as SURVIVED; a guard-converted throw counted as a kill | **fixed.** A failing *file* is a runner error (`did not run as a suite`); a suite that passes unchanged is a survival; a kill whose declared case failed only with the guard's fixed message requires `kills_by: "guarded"` (and declaring it wrongly is a runner error too). One more defect found while fixing it: the diagnostic block was truncated at the first `...` line, which node also emits when it **elides identical diff lines**, so a long `deepEqual` failure lost its `code: 'ERR_ASSERTION'` and was classified as a crash — `TENANT-04` was a false runner error because of it. The block now ends at the terminator indented exactly two past its own `not ok`. Four self-tests (`--self-test`) pin all of it. |

### The evidence claim that was false, corrected

The first report said, of `buildPlan`: *"A caller filter can reach only the column the registry names
for it; anything else throws `QueryPlanError` before a statement exists."* That was **false** for the
nine inherited keys of `Object.prototype`, on all eight filtered queries. It is true now, and the
claim is backed by a case that enumerates those keys against every filtered query rather than by one
example. The cursor row is corrected too: the first report credited "six authenticating-but-malformed
payloads", which did not authenticate; nine now do.

## D1 rename pass (done at `e69cd5c`, against `0005_console_read_surface.sql`)

| Was | Now | Note |
|---|---|---|
| `public.usage_events e join public.api_keys k left join public.credit_holds h` | `public.console_usage u` | one view; the hold lives in `infrx`. **Verified the view LEFT JOINs `api_keys`** (`0005` line 108) and `infrx.credit_holds` (line 109), so a deleted key keeps its row — which is why the projection now renders `(deleted key)` for a null `key_name` rather than refusing the row |
| `public.credit_ledger l` (+ `l.delta_usd as delta`) | `public.console_ledger l` (`delta` already aliased) | the view masks `actor` per viewer; C1 masks again, fail-closed |
| `(w.ledger_total - w.reserved_total) as available` | `w.ledger_total, w.reserved_total` | the view exposes a stored `available`; this service derives it in one place so the identity the suite asserts has a single home. **D1 may drop nothing** — the column is simply unread |
| `c.effective_at`/`c.actor` | `c.version, c.changed_at, c.changed_by` | D1's view already presents 06's names; C1's earlier guess at store column names is gone |
| — | `f.calibration_set = false` constant | defence in depth behind the view's own exclusion |
| `public.organizations`, `public.api_keys`, `public.org_settings`, `public.feedback`, `public.console_judge_runs`, `public.console_admin_orgs`, `public.operator_audit`, `public.wallets`, `public.org_wallet_summary(uuid)` | unchanged | already what C1 asked for |

### Where D1's shape still differs from what C1 needs (for D1's fix round)

1. **`console_usage.key_name` is `k.name`, nullable.** C1 renders `(deleted key)`. If D1 would rather
   own that string, `coalesce(k.name, '(deleted key)')` in the view makes the two agree; either way the
   row must not be dropped.
2. **Timestamps must reach the client as full-precision RFC 3339 strings.** C1 now *refuses* a
   non-string timestamp (a driver `Date` would corrupt the cursor key it becomes) and requires
   `YYYY-MM-DDTHH:MM:SS(.sss)Z`. PostgREST renders `timestamptz` as `+00:00` by default in some
   configurations; if that is what the view yields, either the view casts
   (`to_char(… , 'YYYY-MM-DD"T"HH24:MI:SS.MSZ')`) or C1 widens the accepted form — D1's call, but it
   has to be one of the two.
3. **`console_admin_orgs.model_ids` must be a real array or JSON**, never a PostgreSQL array literal
   string: C1 refuses `{a,b}` rather than reading it as `null`, because `null` *means* "platform
   default" and the silent reading would invert R24. `limits` must be a JSON object whose keys are
   exactly `ENTITLEMENT_LIMIT_NAMES` with integer values (`jsonb_strip_nulls` over the typed columns
   satisfies this).
4. **Money as strings.** `numeric(20,8)` must not arrive as a float; C1 accepts a number only via
   `toFixed(8)`, which is exact but lossy above 2^53 units, so a string is required for large wallets.
5. **`console_judge_runs.samples`** must carry exactly the sample/score fields of the DTO (any extra
   field is now dropped, and a missing or out-of-vocabulary one is a typed refusal);
   `consent_snapshot_at` must be a timestamp string.
6. **`console_usage` exposes `settlement_regime` and `price_version`**, which C1 does not read — no
   change needed, recorded so the difference is not mistaken for a gap.
7. **`org_settings` exposes `version`/`effective_at`**, which C1 does not read either; `settings.get`
   reads the three settings columns and the history separately.
8. **`infrx.trace_metadata` is T's**, unchanged: `traces_page`/`trace_by_request` remain
   integration-pending on T3, including whether an off-mode request keeps a metadata row (question 3
   below).

## Results (quoted from command output)

```
=== make console-test
exit=0
# pass 179
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 1037.053072
=== make console-lint
exit=0
  80:8  warning  'AuditQuery' is defined but never used  @typescript-eslint/no-unused-vars
✖ 2 problems (0 errors, 2 warnings)
=== make console-typecheck
exit=0
cd apps/app && pnpm exec next typegen && pnpm exec tsc --noEmit
Generating route types...
✓ Types generated successfully
=== make console-mutants
exit=0
144 mutants: 144 killed by a named declared case, 0 survived, 0 stale, 0 runner errors, 31.2s
=== make api-test
exit=0
670 passed, 2 warnings in 34.39s
=== node tests/c/run-mutants.mjs --jobs 6
exit=0
baseline: 66 cases pass unmutated, 31 fail (C2/C3 operations this task does not implement); 55 mutants, 6 at a time
55 mutants: 55 killed by a named declared case, 0 survived, 0 stale, 0 runner errors, 18.0s
=== node tests/c/run-mutants.mjs --self-test
exit=0
ok   a syntax error is a runner error, not a survival
ok   a no-op edit survives
ok   a kill that only the boundary guard produced is a runner error unless declared
ok   a stale find is stale
4 self-tests, 0 failed
=== exported conformance vs the adapter
exit=1
# tests 45
# pass 16
# fail 29
```

UTC window `2026-09-21T07:12:04Z` – `2026-09-21T07:13:42Z`. Environment unchanged from the first
report (Node v22.23.1, pnpm 9.15.9, no service of any kind).

Counts: console suite **179 pass / 0 fail / 0 skipped** (was 150); this track's own cases **45** in
six files; the exported conformance split is **unchanged at 16 pass / 29 fail** — every failure still
attributable to a C2/C3 operation by `tests/c/console-conformance.test.ts`. The R35 label case the
reviewer expected to move is still blocked *before* the label assertions, quoted from the run:

```
    not ok 8 - a calibration label is operator data a customer never sees (R35)
      error: 'content failed: internal_error trace content resolution is not implemented in this service yet (C2 owns content access and signed references)'
```

so the leak it would have caught is now covered by this track's own case instead, and C2 will make the
exported one meaningful. Mutants: **55 declared, 55 killed** (was 25/25); the new ones are `ARG-01/02`,
`AUDIT-01`, `CAP-01`, `CH-01..04`, `CLIENT-01`, `CREDITS-01/02`, `ENT-01`, `FEEDBACK-01`,
`FILTER-03/04`, `JUDGE-01`, `KEYNAME-01`, `LABEL-01`, `MASK-01`, `PROTO-01`, `ROLE-04`, `SCOPE-01/02`,
`SECRET-01`, `STRICT-01..04`, `SUMMARY-01`, `TENANT-04`.

Three of them survived or misreported on the first attempt and were fixed by strengthening the case,
not the declaration: `STRICT-01` (no case passed a non-string through a string column — `model: 42`
added), `ROLE-04` (no case used a truthy non-boolean `isOperator` — three added), `CREDITS-01` (the
chosen amounts were ones float arithmetic gets right — a 12-digit total added).

## Changes

```
$ git diff --numstat 42e5669..HEAD
30	33	apps/app/lib/credits.ts
197	52	apps/app/lib/services/console.ts
116	0	apps/app/lib/services/credits.ts
3	0	apps/app/lib/services/cursor.ts
108	42	apps/app/lib/services/query.ts
134	0	apps/app/tests/c/client-boundary.test.ts
94	0	apps/app/tests/c/credits.test.ts
42	8	apps/app/tests/c/cursor.test.ts
35	0	apps/app/tests/c/harness.ts
412	64	apps/app/tests/c/mutants.json
285	0	apps/app/tests/c/projection.test.ts
177	7	apps/app/tests/c/query-boundary.test.ts
167	1	apps/app/tests/c/read-services.test.ts
131	4	apps/app/tests/c/run-mutants.mjs
```

New files, both under owned paths: `apps/app/lib/services/credits.ts` (the pure credits computation)
and `apps/app/tests/c/{client-boundary,credits,projection}.test.ts`. No contract change, no new
dependency, nothing outside `apps/app/lib/services/`, `apps/app/lib/credits.ts`,
`apps/app/tests/c/` and this report.

## Limits, updated

Superseding the first report's list where they overlap:

1. **Still integration-pending**, now against D1's shipped names: nothing has executed the SQL. The
   eight differences above are what a real run would find first.
2. **`usageSummary` accepts an unbounded range** (ruling 3, declined with a reason above). The
   aggregate is indexed and returns one row; the *scan* is still the whole retained history for an
   organization that asks for no range. Needs a contract revision to fix properly.
3. **Aggregate drift** (unchanged): the SQL renderer and the in-memory port interpret one declarative
   aggregate spec; disagreement is only visible against real PostgreSQL.
4. **`credits.ts` fallback figures** (unchanged, now documented in code with the ceiling named): on the
   pre-D1 path `loaded`/`spent` see one page of the ledger. `available` never does.
5. **`Credits` still crosses as `number`** for `components/credits-card.tsx`, which is not this task's
   file. The typed boundary uses `Money` throughout.
6. **`traceContent`, every mutation, and the write-side body bounds** remain C2/C3.
7. **DUR-RLS's SQL half** still needs a real database: D1's views enforce `is_org_member` in SQL, and
   C1's server-side checks are tested, but the two together are an integration test.
8. **The client-boundary test is static.** It reads import specifiers with regular expressions, so a
   computed `import(variable)` would slip past it; the module-scope guard is the backstop.
9. **`adminAudit` rows are seeded by the harness**, not written by C1 — C3 owns the writes.

## Handback

**Next unblocked task:** C2 — it alone turns exported cases 17, 19, 20 and mutation-safety 8 green,
and the trace-content states are the only thing standing between C1's reads and a complete read half.
C3 remains the larger remaining share, and should lift `ownedTrace`, `ownedKey`, `tenant`,
`requireRole`, `badInput`, `cell`/`text`/`timestamp`/`money` and `masked` from
`lib/services/console.ts` rather than re-deriving them.

**Integration requests (unchanged unless noted):**

1. **Executor: settled** — D1's views and functions through the existing supabase-js client. C1 needs
   no new dependency. The remaining work is the eight shape differences listed above plus a Layer-2
   run of this suite against a task-local PostgreSQL once D1 merges (C1's reserved port is `55441`,
   R48; no container has been created).
2. **To D1:** items 1–5 of *Where D1's shape still differs*. Nothing there changes C1's code except
   possibly the timestamp form (item 2), which is one regular expression either way.
3. **To T3:** does `infrx.trace_metadata` keep a metadata row for an `off`-mode request? C1's
   `traceDetail` resolves through the projection and reports `off` from the row; if T3 drops those
   rows, C2 needs the usage-row fallback.
4. **Contract revision (coordinator):** make `from`/`to` required on `usageSummary`/`usageDaily`, in
   `lib/contracts/services.ts` *and* in the exported conformance cases that currently call them with
   `{}`. C1 will implement the refusal in the same pass.
5. **Optional:** a `console-c1-mutants` target for `node tests/c/run-mutants.mjs` (and
   `--self-test`), alongside `console-mutants`.

## Verification log

- 2026-09-21: Review round 1 addressed at `b978c81`. B1–B6, all four rulings and the six cheap items
  disposed of above; one ruling partly declined with its reason and a contract revision requested in
  its place. The D1 rename pass was done against `0005_console_read_surface.sql` (read-only) and the
  remaining shape differences are listed for D1's fix round. 179 console tests pass, 55 of 55 C1
  mutants and 144 of 144 F2 mutants are killed, and the exported conformance split is unchanged with
  every failure still attributed to C2/C3 by an executable check. No service was contacted; nothing
  was pushed or deployed.

---

# Appendix — review round 2 (fix_required, narrow, at `e91b288`)

Appended. Implementation SHA of these fixes: **`b71ea50`** (see the table; the head at hand-back is the
evidence commit on top of it). Same worktree and branch, same owned paths, nothing pushed, no service
contacted. One commit per item, because sessions have been cut by rate limits.

## Correction to the round-1 appendix

It said "this track's own cases **45** in six files". That was wrong then and is wrong now. Counted
from the runner, per file:

```
$ for f in tests/c/*.test.ts; do node --test --test-reporter=tap "$f" | grep -cE "^ok [0-9]+ - "; done
tests/c/client-boundary.test.ts: 3
tests/c/console-conformance.test.ts: 3
tests/c/credits.test.ts: 5
tests/c/cursor.test.ts: 5
tests/c/projection.test.ts: 10
tests/c/query-boundary.test.ts: 14
tests/c/read-services.test.ts: 20
```

**60 cases in seven files** at this round's head (53 in seven at the round-1 head, which is the figure
the reviewer corrected to; the difference is this round's new cases).

## Item → commit → killing test

| Item | Commit | Killing test (and mutants) |
|---|---|---|
| **B1** `isOperator === true` unpinned on the authorization path; adminAudit filter swap untested; `flag()` refusal unpinned | `30bdc29` | `a truthy isOperator is not authority on the authorization path either` (six spoofed values × adminOrgs, adminAudit, adminGrant, and judgeRuns as a member, with the genuine flag still passing) → **ROLE-05**, **ROLE-06**; `a cursor does not survive a change of filter, on every filtered list` extended with `target_org_id` → **SCOPE-03**; `a boolean column is a boolean: a suspension flag is never guessed at` → **STRICT-05** |
| **B2** NULL `key_id` denied the whole usage page | `90b8c75` | `a usage row survives a deleted key, and says so` — both columns null → sentinel `""` + `(deleted key)`; the empty key filter is `invalid_request`; a real key filter never matches the sentinel; one column null alone is a malformed row → **KEYNAME-01** (deny the page again, `kills_by: "guarded"`), **KEYNAME-02** (paper over a half-null pair), **KEYNAME-03** (a sentinel a caller could filter for) |
| **B3** JSON-number money fabricated digits from 2^26 | `d64a58e` | `money arrives as text: a number is accepted only where a double still holds eight digits` (123456789012.12345678, ±2^26, 1e20, NaN, Infinity refused; 2^26−1 exact; the same value as text keeps every digit) and `a wallet amount that arrives as a number is bounded by what a double can hold` → **MONEY-01**, **MONEY-02**, **MONEY-03** |
| **B4** missing-function detection forced by an error string | `a6afa15` | `a missing function falls back; a broken one does not` — the two real repros (`42883` from *inside* the function, `P0001` naming it) now raise, plus a pair differing only in the code → **CREDITS-03** (drop the name requirement), **CREDITS-04** (drop the code requirement), alongside **CREDITS-02** |
| **Rulings**: both UTC timestamp forms; the tenant check every port passes through; the real `usageDaily` cap | `973ce79` | `both UTC timestamp forms are accepted and normalised, microseconds and all` → **TS-01**, **TS-02**, **STRICT-03**; `every port passes through the tenant check, whatever the port does` → **PORT-01**, **PORT-02**, **PORT-03**; `usageDaily is bounded by its documented cap, not by the fixture` (401 days, and a port that ignores `limit`) → **CAP-02** |
| **Nonblocking**: `integer()` coercion, `value_num` coercion, credits guard, runner `viaGuard` | `a083e92` | the strict-row case extended with `""`, `true`, `["500"]`, `0x10`, `1e2` and a fractional count → **STRICT-02**; the judge case extended with five coercible score values → **STRICT-06**; the guard-presence list now includes `credits.ts` → **CLIENT-02** |
| stale mutant refresh | `b71ea50` | **STRICT-03** repointed at the normaliser's refusal |

### Ruling details as implemented

- **NULL key_id (B2 ruling).** Both columns NULL is the truth D1's LEFT JOIN produces; C1 projects
  `DELETED_KEY_ID = ""` with `(deleted key)` until the contract revision makes `UsageRow.key_id`
  nullable (F2.2). The empty string is chosen *because* a `key_id` filter must be identifier-shaped
  (non-empty, ≤ 200), so the sentinel is unreachable from a filter — asserted both ways. One column
  NULL without the other is refused as a malformed row.
- **Money (B3 ruling).** Text is the contract. A number is accepted only when finite and
  `< 2**26`, which is the legacy `org_balance` fallback's range; the false "the conversion is exact"
  comment is gone.
- **Timestamps (R59-9).** `Z` or `+00:00`, fractional up to microseconds, normalised to `Z` **without
  losing the fraction**; the cursor key is normalised the same way so the DTO and the cursor agree.
  Refused: driver `Date`, `::text` form, date-only, non-UTC offsets, more than microsecond precision.
- **Blast radius.** A malformed row remains a page-level `internal_error` (loud in the pilot), recorded
  as a limit below. The refusal names the relation and the column and never the value — e.g.
  `http_status must be an integer`, `model_ids must be a JSON array or null` — and the boundary guard
  replaces it with one fixed safe message before it leaves the service, so nothing reaches a client.
  **Server-side logging of which relation/column failed is not implemented**: the console has no
  logger of its own and adding one is a composition-root change. It is an integration request.
- **The tenant check every port passes through.** `scopedPort` wraps *both* injected ports inside
  `createConsoleServices`, so no implementation can skip it: it refuses a tenant-scoped plan with no
  tenant before calling the executor, and refuses any returned row whose tenant field is not the bound
  one. This matters because D1's views return every organization's rows to an operator or service-role
  session, so for those sessions C1's predicate is the only scoping there is.
- **`usageDaily` cap.** Real on both sides: the memory port honours `plan.limit` for grouped aggregates
  (the rendered statement always carried `limit 400`) and the service slices to the named query's cap,
  so an executor that ignores a LIMIT cannot produce an unbounded response. `usageSummary` stays
  unbounded pending the from/to contract revision, and stays listed as a limit.
- **The real executor.** Stated plainly: **the supabase-js `QueryPort` is unwritten.** Under "D1 views
  via supabase-js" the rendered SQL in this report is not what will run — it is the specification of
  what each named query must fetch, and the port that turns a `QueryPlan` into a PostgREST call is
  C2's first deliverable, together with a Layer-2 tenant test on task-local port `55441` once D1
  merges. Nothing in this repository executes SQL today.

## Results (quoted)

```
=== make console-test
exit=0
# tests 186
# pass 186
# fail 0
# skipped 0
=== make console-lint
exit=0
✖ 2 problems (0 errors, 2 warnings)
=== make console-typecheck
exit=0
✓ Types generated successfully
=== make console-mutants
exit=0
144 mutants: 144 killed by a named declared case, 0 survived, 0 stale, 0 runner errors, 28.1s
=== make api-test
exit=0
670 passed, 2 warnings in 29.70s
=== node tests/c/run-mutants.mjs --jobs 6
exit=0
baseline: 73 cases pass unmutated, 31 fail (C2/C3 operations this task does not implement); 74 mutants, 6 at a time
74 mutants: 74 killed by a named declared case, 0 survived, 0 stale, 0 runner errors, 24.1s
=== node tests/c/run-mutants.mjs --self-test
exit=0
4 self-tests, 0 failed
=== exported conformance
exit=1
# tests 45
# pass 16
# fail 29
```

UTC window `2026-09-21T16:57:34Z` – `2026-09-21T16:59:10Z` (the mutant totals re-run after the stale
refresh at `b71ea50`). The two lint warnings remain the pre-existing ones in coordinator-owned
`lib/contracts/*`. The exported conformance split is unchanged at **16 / 29**, every failure still
attributed to a C2/C3 operation by `tests/c/console-conformance.test.ts`.

One invariant was deliberately **not** claimed: `TS-03` (the cursor key keeping a row's raw timestamp
form) is unobservable through a port that compares the same values it returns, and in PostgreSQL both
forms are one instant — so a case for it could not fail. The normalisation stays for consistency
between the DTO and the cursor; the mutant is not in the list.

## Changes

```
$ git diff --numstat e91b288..HEAD
102	17	apps/app/lib/services/console.ts
46	14	apps/app/lib/services/credits.ts
37	0	apps/app/lib/services/query.ts
1	1	apps/app/tests/c/client-boundary.test.ts
52	3	apps/app/tests/c/credits.test.ts
2	1	apps/app/tests/c/harness.ts
216	25	apps/app/tests/c/mutants.json
168	4	apps/app/tests/c/projection.test.ts
125	2	apps/app/tests/c/read-services.test.ts
3	2	apps/app/tests/c/run-mutants.mjs
```

## Limits, updated again

Replacing the round-1 list where they overlap:

1. **The supabase-js `QueryPort` is unwritten** (C2's first deliverable). No SQL has executed; the
   rendered statements are the specification of each named query, not the text that will run.
2. **A malformed row fails the whole page** (`internal_error`), by ruling — a D/C shape break is loud in
   the pilot. Server-side logging of the failing relation and column is **not implemented** (no logger
   in the console); integration request below.
3. **`usageSummary` accepts an unbounded range** until the from/to contract revision (F2.2).
4. **`UsageRow.key_id` is not nullable in the frozen contract**, so a deleted key reads as the
   documented sentinel `""`. The revision is F2.2's.
5. **Money as a JSON number is accepted below 2^26** for the legacy `org_balance` fallback path only;
   everything else must be text.
6. **`Credits` still crosses as `number`** for `components/credits-card.tsx`, which is not this task's
   file.
7. **Aggregate drift** between the SQL renderer and the in-memory port is still only visible against
   real PostgreSQL.
8. **`traceContent`, every mutation and the write-side body bounds** remain C2/C3; `adminAudit` rows are
   seeded by the harness because C3 owns the writes.
9. **The client-boundary test is static** (regex import scan), so a computed `import(variable)` would
   slip past it; the module-scope guards are the backstop.
10. **DUR-RLS's SQL half** needs a real database and D1's grants.

## Integration requests (delta)

Unchanged from round 1, plus:

1. **A server-side log sink for the console** (coordinator): a one-line hook the services can call with
   the relation and column that failed shape validation, never the value. Without it a shape break is
   visible only as an `internal_error` in the page.
2. **F2.2 contract revision** now covers three items: `UsageRow.key_id: string | null`, `from`/`to`
   required on `usageSummary`/`usageDaily`, and (from round 1) nothing else in `ConsoleServices`.
3. **To D1:** money and timestamps as text, `model_ids` as a real array — as listed in round 1 — plus a
   note that `console_usage` leaves `key_name` and `key_id` both NULL for a deleted key, which C1 now
   handles; if D1 would rather `coalesce` the name in the view, C1's sentinel pair still applies to the
   id.

## Verification log

- 2026-09-21: Review round 2 addressed. B1–B4, the four rulings and the six nonblocking items each got
  a commit and a killing test; 186 console tests pass, 74 of 74 C1 mutants and 144 of 144 F2 mutants
  are killed, the runner's four self-tests pass, and the exported conformance split is unchanged with
  every failure still attributed to C2 or C3 by an executable check. The round-1 case count was
  corrected from the runner's own output rather than restated. No service was contacted; nothing was
  pushed or deployed.
