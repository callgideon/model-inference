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
