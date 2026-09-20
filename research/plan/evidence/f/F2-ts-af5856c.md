# F2 (console half) — console contract types, fixtures, fake services and recursive test discovery

## Task and status

| | |
|---|---|
| Task | F2-ts (track F, foundation) — the TypeScript/console half of F2: contract types, money helpers, fixtures, fixture-backed fake `ConsoleServices`, shared conformance suite, recursive console test discovery |
| Owner / session | Claude Opus 5 implementation session, worktree `codex-f2ts` |
| Status | **implemented, not integrated** (no merge into `claude/infrx-impl`, no push, no deploy, no cloud, database or paid-provider call) |
| Oracles | F-CONTRACT (console adapters run shared fixtures), F-BASE (nested discovery proven; the 4 pre-existing console tests still pass) |
| Not this task | the Python half of F2 (`apps/infrx-api/**`, `pyproject.toml`, `uv.lock`, root `Makefile`) is owned by a parallel agent |

## Source

| | |
|---|---|
| Base SHA | `fab9fbe` (`plan/evidence: record F1 integration (be7e984) and F2 dispatch`) |
| Implementation SHAs | `cb00862` (types, money, fixtures, fake), `108bed8` (discovery, conformance suite, tests, README), `af5856c` (README row count) |
| Evidence SHA | this report's own commit (separate, cites `af5856c`) |
| Branch / worktree | `codex/f2-contracts-ts` in `.claude/worktrees/codex-f2ts` |
| Integration target | `claude/infrx-impl` (coordinator only) |
| Integrated SHA | not available — not integrated |

## Requirement coverage

Oracle **F-CONTRACT** — "all port adapters run shared fixtures: matching envelopes, decimals, states, errors, cursors and schema versions". The console boundary's suite is `lib/contracts/conformance.ts`, exported as `runConsoleServicesConformance(factory)` so C reruns the identical bodies against PostgreSQL/ClickHouse.

| Test ID | Case (test name) | Invariant demonstrated |
|---|---|---|
| F-CONTRACT/money-1 | `money.test.ts` "parsing normalises to exactly eight fractional digits" | Every accepted plain decimal becomes the canonical 8-digit string (08 §4); the canonical form is a fixed point. |
| F-CONTRACT/money-2 | "everything that is not a plain decimal string is rejected" | 18 fixture rejects (`1e3`, `1E-8`, `NaN`, `±Infinity`, `-0`, `-0.00000000`, `0.000000001`, leading/trailing space, `+1`, `.5`, `1.`, `1,000.00000000`, `0x10`, `$1.00`, empty, `--1`) plus 11 non-string values (`0`, `1`, `0.1`, `1e-8`, `NaN`, `Infinity`, `-0`, `null`, `undefined`, `{}`, `[]`, `BigInt(10)`) all fail `tryParseMoneyUnits`, `isMoney` and throw from `parseMoney`. Floats-as-number never become money. |
| F-CONTRACT/money-3 | "a value that is canonical but not eight digits is not money" | `isMoney("1.5") === false`: a DTO cannot drift to a short decimal. |
| F-CONTRACT/money-4 | "addition, subtraction, comparison and sums are exact at the eighth digit" | BigInt-scaled `add/sub/compare/sum` match the fixture, including a 20-digit total; `0.1 + 0.2 === "0.30000000"` (float would give `0.30000000000000004`). |
| F-CONTRACT/money-5 | "negative and zero are distinguishable, and negative zero does not exist" | `isNegativeMoney`/`isZeroMoney`; `-0.00000000` never parses; `moneyFromUnits(0)` is unsigned zero. |
| F-CONTRACT/money-6 | "scaled units are 1e-8 USD and survive twenty significant digits" | 1 USD = 100000000 units; a forged brand (`"1.5e0" as Money`) still throws at the boundary. |
| F-CONTRACT/money-7 | "the charge and hold fixtures agree across languages and stay canonical" | The half-up boundary, the ceiling-vs-half-up divergence (exactly one case, `diverges: true`), the zero-token request and an ordinary video request are canonical money, and a `ROUND_CEILING` hold is never below the `ROUND_HALF_UP` charge it must cover. TypeScript does not multiply: these are the constants the Python `Decimal` path must reproduce. |
| F-CONTRACT/money-8 | "display never routes the value through Number" | `displayMoney` keeps the eighth digit (`$0.00000015`), widens for sub-cent values, groups thousands, and is string+BigInt only. |
| F-CONTRACT/fixtures-1..4 | `fixtures.test.ts` (4 tests) | Hand-written runtime guards (no new dependency) over the raw JSON: exactly the four known fixture files; two distinct orgs; UUIDv4 and RFC 3339 shapes; every enum value inside the frozen §3 sets; retention within the 90-day cap; grant amounts canonical and reasoned; one org with zero grants and one with a key per trace mode plus a revoked key; exactly one operator and one member session; content templates are `v: 1` with array messages/choices; no `s3://`, `X-Amz-` or `https://` anywhere in a fixture; judge runs keep `ambiguous` unsettled with no provider id, dry runs never reach a provider, and a limited evaluation carries no groundedness score. |
| F-CONTRACT/services-pagination | `conformance.ts` "usage pages walk every row exactly once, newest first" and "ledger and trace pages walk every row exactly once" | Walking with limit 10/7/9 returns every row exactly once (set size equals length) in stable descending `created_at` order, no empty non-final page, and every row passes the DTO guards (unknown usage reports no tokens and no charge; only a settled row carries a charge; an off-mode row has no content; minimal capture is metadata only; lost content states a reason). |
| F-CONTRACT/services-limit | "rejects a limit above the hard bound and a limit that is not a positive integer" | `limit` 101, 1000, 0, −5 and 2.5 return `invalid_request`; 100 is accepted. The bound is a rejection, not a clamp. |
| F-CONTRACT/services-cursor | "rejects a cursor it did not issue for this query" | Garbage, a hand-built base64 cursor, a mutated cursor, and a valid cursor reused with a different filter all return `invalid_cursor`; the reissued cursor still works. |
| F-CONTRACT/services-balance | "balance is the ledger total minus reservations, and holds reduce what is available" | `available === ledger_total − reserved_total`, `ledger_total` equals the sum of the paginated ledger deltas, and with a hold outstanding `available < ledger_total`. |
| F-CONTRACT/services-neworg | "a new organization starts at zero with no ledger history" | DEC-01/DEC-08: the second org has zero total, zero available and an empty ledger. |
| F-CONTRACT/services-tenant | "identifiers from another organization are not_found, in both directions" | Cross-org request ids on `traceDetail`, `traceContent`, `feedback.list`, `feedback.submit` and cross-org key ids on `keys.revoke` return `not_found` (never `forbidden`, which would confirm existence), in both directions, as does an unknown id. |
| F-CONTRACT/services-role | "a member can read but cannot mutate settings or keys" | Member reads usage/settings/keys; `settings.update`, `keys.create`, `keys.revoke` return `forbidden`. |
| F-CONTRACT/services-operator | "a non-operator can neither grant nor see other organizations" | Owner and member get `forbidden` from `adminOrgs`; an owner's `adminGrant` is `forbidden`; the operator sees both orgs with canonical money balances. |
| FEEDBACK-ACK (fake) | "feedback appears immediately, with provenance the client cannot set" | A submission carrying `channel: "api"`, `author_role: "judge"`, another principal and `calibration_set: true` is accepted with `channel: "console"`, `author_role: "customer"`, the session's principal and `calibration_set: false`; it is listed immediately (projection lag is irrelevant); an invalid rating is `invalid_request`. Durable-path acceptance remains D6/C3's to prove. |
| F-CONTRACT/services-settings | "retention is capped and evaluation consent is a separate control" | 91 days and 0 days are `invalid_request`, 90 is accepted; switching trace mode leaves consent untouched and vice versa (DEC-10); a consent change appends one audit entry with the session principal. |
| DUR-CAP (fake) | "an operator grant is idempotent per key and conflicts on a changed payload" | Same key and payload returns the same `grant_id` with `replayed: true` and one wallet effect; a changed amount or reason under the same key is `idempotency_conflict`; zero, negative and exponent amounts and a blank reason are `invalid_request`; an unknown target org is `not_found`. Concurrency remains D's to prove. |
| TRACE-TENANT (fake) | "content availability decides the payload and never leaks a storage reference" | For all 137 traces the list and the content view agree on availability; only `available` carries a `v: 1` body; `metadata_only`, `pending`, `lost`, `expired` and `off` return `content: null`; no serialized view contains `s3://` or `X-Amz-`. |
| JUDGE-SCORES (fake) | "judge runs separate estimates, limited evaluations and held budgets" | `sample_count`/`limited_evaluation_count` match the samples; an `ambiguous` run is unsettled, still holds a positive reservation and records why; dry-run scores are estimates and live scores are not; a limited evaluation has a reason and no groundedness score. |
| F-BASE/discovery-1 | `discovery.test.ts` "the test script declares the four directory patterns of 08 §7" | `pnpm test` is `node --test` over exactly `lib/**/*.test.ts`, `tests/**/*.test.ts`, `app/**/*.test.ts`, `components/**/*.test.ts`, read back out of `package.json`. |
| F-BASE/discovery-2 | "every test file in the tree is matched by a declared pattern" | The walk (skipping `node_modules`, `.next`, `.git`, `out`, `build`, `coverage`) finds no `*.test.ts(x)` outside those patterns. An undiscovered test fails the suite instead of silently passing. |
| F-BASE/discovery-3 | "nested suites and the pre-existing library tests are both discovered" | `lib/utils.test.ts` and `lib/keys.test.ts` stay discoverable, the two-level-deep `tests/contracts/*.test.ts` files are discovered (the old `lib/*.test.ts` glob missed them), and the glob translation is asserted for zero-directory and one-directory `**`. |
| F-BASE/discovery-4 | "the console pins a Node version that strips types" | `engines.node` is `>=22.18` and the running interpreter satisfies it. |
| F-BASE/console-baseline | `pnpm --dir apps/app test` | The 4 pre-existing console tests (`keys.test.ts` ×3, `utils.test.ts` ×1) run first and pass, unmodified (`git diff fab9fbe -- apps/app/lib/keys.test.ts apps/app/lib/utils.test.ts` is empty). |
| F-CONTRACT/fake-determinism | `services.test.ts` "the fixtures are deterministic and each instance has its own state" | Two `createFakeConsoleServices()` instances produce deep-equal rows; mutating one does not affect the other. |
| F-CONTRACT/fake-coverage | "the fixture set covers what U and V need to build against" | >1 page at limit 100 for usage, ledger and traces; all six content availability states present; a positive `reserved_total` with `available < ledger_total`; an ambiguous, a dry-run and a limited-evaluation judge run present. |
| F-CONTRACT/fake-injection | "every operation can be made to fail on demand" | All 18 declared operations are covered by name (the test's call table is asserted equal to `CONSOLE_OPERATIONS`); each returns the injected error exactly once and recovers on the next call. |

## Environment

Local development host. No staging, no production, no GPU, no cloud resource, no network egress, no database, no paid provider. `apps/app/node_modules` was installed in this worktree before the session (`pnpm install --frozen-lockfile`).

| | |
|---|---|
| OS / kernel | Linux 7.0.0-1010-aws x86_64 |
| Node | v22.23.1 (built-in TypeScript type stripping, unflagged from 22.18) |
| pnpm | 9.15.9 (`packageManager` unchanged) |
| Next / React / TypeScript | next 16.3.5, react 19.2.8, typescript 5.x as installed by the existing lock |
| Dependencies added | none. `apps/app/pnpm-lock.yaml` and `dependencies`/`devDependencies` are untouched |
| Environment variables read | none by any file in this change; `next build` ran with the ambient environment and needed no secret |
| Seed | none needed: the fixtures are generated by a fixed-seed PRNG (`mulberry32(namespace × 7919 + 13)`) and deterministic identifiers, not by randomness at test time |

## Commands

Run from the worktree root `/home/rey/workspace/rey/code/model-inference/.claude/worktrees/codex-f2ts` at `af5856c`.

| UTC | Command | Exit |
|---|---|---|
| 2026-09-20T19:36:58Z | `pnpm --dir apps/app test` | 0 |
| 2026-09-20T19:36:58Z | `pnpm --dir apps/app lint` | 0 |
| 2026-09-20T19:37:02Z | `pnpm --dir apps/app exec tsc --noEmit` | 0 |
| 2026-09-20T19:37:04Z | `pnpm --dir apps/app build` | 0 |
| 2026-09-20T19:37:09Z | `git diff --stat fab9fbe -- apps/app/pnpm-lock.yaml` | 0, empty output |

## Results

| Command | Observed | Expected |
|---|---|---|
| `pnpm --dir apps/app test` | `# tests 37`, `# suites 1`, `# pass 37`, `# fail 0`, `# skipped 0`, `# todo 0`. Discovered files: `lib/keys.test.ts` (3), `lib/utils.test.ts` (1), `tests/contracts/discovery.test.ts` (4), `tests/contracts/fixtures.test.ts` (4), `tests/contracts/money.test.ts` (8), `tests/contracts/services.test.ts` (1 suite of 14 conformance cases + 3 fake-only tests) | the 4 pre-existing tests plus the nested contract suites, all passing |
| `pnpm --dir apps/app lint` | no output | no error, no warning |
| `pnpm --dir apps/app exec tsc --noEmit` | no output | clean. Before `next build` had ever run in this worktree, the same command reported 4 pre-existing `TS2304 Cannot find name 'PageProps'/'LayoutProps'` errors in `app/(auth)/login/page.tsx`, `app/(console)/layout.tsx`, `app/(console)/usage/page.tsx`, `app/layout.tsx` — files this task does not own; those names come from Next's generated `.next/types`, so `tsc` needs a prior `next build` (or `next dev`) on a clean checkout. No error ever came from `lib/contracts/**` or `tests/contracts/**`. |
| `pnpm --dir apps/app build` | `✓ Compiled successfully in 449ms`, 14 routes, static generation 7/7 | build feasible without secrets; reported rather than pending |
| fixture counts (measured with a scratch script, not committed) | established org: 137 usage rows, 117 ledger entries, 137 traces; content states `available` 15, `metadata_only` 54, `off` 45, `pending` 8, `lost` 8, `expired` 7; settlement states `settled` 114, `released_free` 10, `held_unknown` 7, `released_platform_absorbed` 6; balance `ledger_total 29.80374900`, `reserved_total 0.02102850`, `available 29.78272050`; new org all zero | >100 rows per list, every availability state, every settlement state, holds reducing available, a zero-balance org |

## Failure drill

No durable state exists in this task (the console half is in-memory), so the drills are the injected and adversarial paths rather than process kills:

| Injection | State before | Behaviour | State after |
|---|---|---|---|
| `failNext(op, "dependency_unavailable")` for each of the 18 operations | fixture state | the next call returns `{ok: false, error: {code: "dependency_unavailable"}}`; the following call succeeds | unchanged: injection never mutates rows |
| Forged cursor (`btoa('{"o":0,"k":"deadbeef"}')`), mutated cursor, cursor replayed with a different `model` filter | one page already issued | `invalid_cursor` each time; the legitimately reissued cursor still returns its page | unchanged |
| Duplicate `adminGrant` with the same idempotency key and payload | wallet at `10.00000000` after the first grant | one `grant_id`, `replayed: true`, wallet still `10.00000000` | one ledger entry, not two |
| `adminGrant` replayed with a changed amount, then a changed reason | one stored grant | `idempotency_conflict` both times | no second grant, original preserved |
| Feedback submitted with client-supplied `channel`, `author_role`, `author_principal`, `calibration_set` | 1 seeded entry on the trace | accepted with server-set provenance; the spoofed fields are discarded | 2 entries, neither forged, `calibration_set` false |
| Cross-tenant read with the other organization's request and key ids (both directions) | two populated orgs | `not_found`, never `forbidden` and never data | unchanged |
| `settings.update` with 91-day retention, 0 days, an invalid trace mode, and as a member | retention 30 | `invalid_request` / `invalid_request` / `invalid_request` / `forbidden` | retention unchanged on every rejection |

## Artifacts

| Path | Note |
|---|---|
| `apps/app/lib/contracts/{money,types,services,fake-services,conformance}.ts`, `apps/app/lib/contracts/README.md` | the contract surface; committed |
| `apps/app/lib/contracts/fixtures/{orgs,money,traces,judge}.json` | fixtures; no secret, no signed URL, no customer content — the only key-like strings are prefixes (`sk-infrx-nw01pro`) and the fake's create-once secret is literally `sk-infrx-FAKE…` |
| `apps/app/tests/contracts/{discovery,fixtures,money,services}.test.ts` | committed |
| `/tmp/f2ts-verify.txt` | raw command log of the run above; local scratch, not committed, no credential in it |

## Changes

Owned paths changed, and nothing else (`git diff --stat fab9fbe` touches only these):

- `apps/app/package.json` — the `test` script and a new `engines` block only; no dependency change; `pnpm-lock.yaml` byte-identical.
- `apps/app/lib/contracts/**` — new (6 TypeScript modules, 4 fixture files, README).
- `apps/app/tests/contracts/**` — new (4 test files).
- `research/plan/evidence/f/F2-ts-af5856c.md` — this report.

Contract amendments requested (also recorded in `lib/contracts/README.md`), none of which changes 08's frozen vocabularies:

1. **`keys.list/create/revoke` added to `ConsoleServices`.** 08 §9 does not list key operations, but U2 owns create-once secret presentation and revocation and must go through the same session/role boundary. Without them the "a member cannot mutate keys" requirement has nothing to test.
2. **`adminGrant` takes `target_org_id`.** The brief says no operation accepts an org id from the caller; an operator grant must name its target, so this is the single exception, authorized against `session.isOperator` and never used as a tenant binding. Every tenant-scoped operation resolves the org from the session only.
3. **`usageSummary` and `usageDaily` are separate operations** beside `usage`, matching the existing `org_usage_summary` / `org_usage_daily` SQL functions rather than overloading one call.
4. **`LedgerEntryKind` is `grant | usage | adjustment`** — the existing `credit_ledger.kind` minus `purchase`, which belongs to deferred payments (DEC-01).
5. **Trace list rows are usage-derived, so a request captured with mode `off` appears with `content: "off"`** rather than being absent. V1 must distinguish "not captured" from "capture failed"; `off` rows are excluded from trace-coverage denominators by their `trace_mode`.
6. **`TraceMode` is `off | minimal | full` (08 §3), not the `off | metadata | full` of `research/traces/07-console-spec.md` §5.** 08 wins; V should read the mode names from `lib/contracts/types.ts`.

No migration, no deployment and no rollback implication: nothing here runs in production, and the change is additive to the console (the old `test` script only ever matched `lib/*.test.ts`).

## Limits

- **Fake only.** Every result above is the in-memory fixture implementation. C must rerun `runConsoleServicesConformance` against real PostgreSQL/ClickHouse before anything here counts as integrated; a green fake is `implemented`.
- **Not exercised:** concurrency (two simultaneous grants, a hold taken while a balance is read), RLS and column grants (DUR-RLS, D/C), real projection lag, signed-URL issuance and expiry (C2/M3), durable feedback acceptance (D6) and real judge submission (J). The suite asserts the boundary's *shape and rules*, not the database's enforcement.
- **No DOM or component test stack** (08 §6 forbids adding one in F2); React behaviour remains for the track suites and E's browser suite.
- **`tsc --noEmit` needs a prior `next build`** on a clean checkout, because four pre-existing app files reference Next's generated `PageProps`/`LayoutProps` types. Worth a coordinator decision (it affects everyone's local check), but it is not F2-ts's file to fix.
- **`tsconfig.json` targets ES2017**, where BigInt literals (`0n`) are a compile error; the money module uses `BigInt(0)` instead. A target bump to ES2020 would be tidier and is an integration request, not an owned change.
- **The Python half of F2 is not in this worktree.** Cross-language agreement rests on the shared constants in `fixtures/money.json` (`charge_half_up`, `hold_ceiling`); the Python side must assert the same values. Until it does, cross-language parity is **pending**, not passed.
- `pnpm build` succeeded here without secrets, but it is a local build, not a deployment claim.

## Handback

- **Next unblocked work:** the coordinator can integrate `codex/f2-contracts-ts` into `claude/infrx-impl` (console-only paths; no conflict with the Python F2 worktree except `research/plan/evidence/f/`). C1, U1 and V1 can then start against `lib/contracts/fake-services.ts`.
- **Pending coordinator wiring:** (a) accept or amend the six contract amendments above, especially `keys.*` and `adminGrant`'s target; (b) root `Makefile` `console-test`/`console-lint` targets belong to the Python-half/coordinator change and must call `pnpm --dir apps/app test` / `lint`; (c) decide on the ES2020 target bump and on whether `tsc --noEmit` should be preceded by `next build` in the canonical check.
- **Unresolved findings:** none open in this task. The `tsc`/`next build` ordering and the ES2017 target are noted above as coordinator decisions, not defects introduced here.
- **Reminder for U/V:** import DTOs as types and call the fake from server components or server actions; it reads JSON fixtures and is not intended for a client bundle.

## Verification log

- 2026-09-20: Implemented the console half of F2 on `codex/f2-contracts-ts` from base `fab9fbe`. 37 console tests, lint, `tsc --noEmit` and `next build` pass locally against the fake; `pnpm-lock.yaml` untouched. No merge, push, deployment, cloud, database or paid-provider action. Cross-language money parity with the Python half is pending, not passed.
