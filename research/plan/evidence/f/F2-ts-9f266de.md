# F2-ts — console contracts, round 3 (review fix + contract revision r1)

- **Status: implemented.** Not integrated: the conformance suite has run against the
  fixture-backed fake only. C is integrated when the same suite passes against real PostgreSQL
  and ClickHouse (F-CONTRACT, CONSOLE-FLOWS in `04-verification.md`).
- Worktree `.claude/worktrees/codex-f2ts`, branch `codex/f2-contracts-ts`, base `fab9fbe`.
- Head at this report: `9f266de`. Raw command output: `F2-ts-9f266de-verify.txt`.
- Earlier rounds: `F2-ts-af5856c.md` (round 1), `F2-ts-7272695.md` (round 2). Neither was
  rewritten.
- Nothing was pushed, deployed or run against a cloud service, a database or a paid provider.

## 1. The blocking finding

Review at head `92a6022`: *`adminGrant` throws on wallet overflow after mutating the ledger →
double grant on retry and permanently broken balances/adminOrgs.*

**Fixed at the root, not at the report.** The report named one input
(`amount: "999999999999.99999999"`); the defect was that the operation wrote first and computed
money afterwards, where `moneyFromUnits` throws. The fix removes the ordering, not the input:

- `money.ts` gains `tryMoneyFromUnits(units): Money | null` — the same conversion without the
  throw. `moneyFromUnits` now delegates to it and throws only when it returns null, so there is
  one conversion and one bound (`MAX_UNITS`, `|value| < 10^12`).
- `balanceOf(org, deltaUnits = 0n)` answers *what the wallet would be* after a delta, in BigInt
  units, and returns `null` when any of `ledger_total`, `reserved_total` or
  `available = ledger_total - reserved_total` leaves `numeric(20, 8)`. `available` matters: with a
  negative ledger total and a large hold it can overflow even when both components fit.
- `adminGrant` calls it with the grant's units *before* touching anything. `null` is
  `invalid_request`; the ledger is untouched, no idempotency record is filed, and the retry that
  follows is refused identically. The accepted path reuses the same projected balance as its
  response, so the response cannot disagree with the write.
- `balances` and `adminOrgs` return `internal_error` instead of throwing if an aggregate ever
  leaves the domain, so a console page renders an error rather than a stack trace.

Reproduction of the review's exact scenario is now a test
(`tests/contracts/services.test.ts`, "the exact overflow the review found is refused, and the
fake stays usable afterwards"): both attempts return `invalid_request`, the ledger id list is
byte-identical after each, the wallet deep-equals its pre-attempt value, `adminOrgs` still works,
and a grant that *does* fit still lands exactly once under its key.

## 2. Defect-class sweep

The reviewers found a new instance of "validates after it writes" every round, so this round
attacks the class. Every mutating operation (`feedback.submit`, `settings.update`, `keys.create`,
`keys.revoke`, `adminGrant`) now runs **all** validation, then the replay check, then the write,
then files the idempotency record. `MUTATING_OPERATIONS` in `services.ts` names them, and
`runMutationSafetyConformance` asserts the property rather than trusting the ordering.

| Class item | What changed | Where it is asserted |
|---|---|---|
| Mutation before validation | Every mutating operation validates, then writes, then records | conformance: "every mutating operation refuses an injected failure without half-changing anything" — snapshots ledger, keys, settings and feedback around five refusals, one per operation, and deep-equals |
| Failure after partial mutation | `failNext` is intercepted at the top of every operation, before any read or write | `services.test.ts`: every operation fails on demand and recovers on the next call; the snapshot test above covers the state |
| Idempotency scope | One shared record keyed `(caller org, operation, key)`, with the target org and the normalised payload *inside* the stored payload; required on `adminGrant`/`feedback.submit` (R3), optional on the three tenant-scoped mutations | conformance: "an idempotency key is scoped to its operation, its organization and its payload" — replay returns the original, a changed target/value/amount is `idempotency_conflict`, the same key on another operation is a separate record |
| Id collisions across orgs | Feedback ids are `fb_<namespace><ordinal>` with a per-organization counter that starts past the seeded rows (the round-2 finding: the 187th submission collided with a seeded id) | `services.test.ts`: 200 submissions in *each* organization, 400 distinct ids; conformance: keys, grants and feedback ids collected across both organizations |
| Caller-supplied `org_id`/`author_role`/`channel`/storage key | Refused, not ignored: unknown input fields are `invalid_request`, mirroring pydantic `extra="forbid"` (08 §2). Field lists exported from `types.ts` | conformance: "no operation accepts a field the caller invented" (usage, traces, ledger, settings.update, keys.create, judgeRuns) |
| `limit > 100`, forged/foreign cursors | Unchanged behaviour, verified again; `limit: "5"`, `NaN`, `0`, `-5`, `2.5`, `101` all `invalid_request`; a 5,000-char, non-string, mutated, forged, cross-filter or cross-tenant cursor is `invalid_cursor` | existing conformance cases plus an adversarial probe (§5) |
| Pagination under insertion at the head | Cursors now carry the id of the last row delivered instead of an offset, and resuming looks it up in the current list. A grant arriving at the head mid-walk no longer shifts offsets. `limit` left the cursor scope, so the page size may change mid-walk | conformance: "a list walked while rows arrive at its head still returns every row exactly once" — walks the ledger at limit 2, grants twice during the walk, and asserts the collected ids deep-equal the pre-walk snapshot |
| Money through `Number`/`parseFloat` | None existed; now enforced permanently | `money.test.ts` greps `money.ts` for `parseFloat`, `toFixed`, `Number(`, `parseInt`, `Math.` |

**Mutation testing.** Each new guard was reverted in a scratch copy of `apps/app`
(`rsync`, `node_modules` symlinked; the worktree was never modified) to confirm the tests fail
without it:

| Mutation | Result |
|---|---|
| Overflow pre-check replaced by a zero fallback | 2 failures (conformance mutation suite, fake-only overflow test) |
| Idempotency record filed before the write | 2 failures (same two) |
| Keyset cursor reverted to an offset cursor (both encode and decode sites) | 1 failure ("a list walked while rows arrive at its head…") |
| Unknown-field check disabled | 2 failures (main suite, mutation suite) |
| Feedback id minted without the organization namespace | 1 failure (400-submission id test) |

## 3. Rulings applied (08 §10, revision r1)

- **R3 — feedback body.** `FeedbackInput` is now `{request_id, name, value, comment?,
  idempotency_key}` with `name ∈ thumb | rating | correction | comment` and `value` a boolean /
  integer 1–5 / non-empty text according to the name; `comment` is optional and non-empty when
  present; the idempotency key is required and bounded at 255 characters (08 §3).
  `FeedbackEntry` carries `name` and `value` in place of `rating`/`correction`. The fixture seeds
  one entry per name, both channels and a judge-authored one. Eleven wrong-value cases are in the
  conformance suite.
- **R11 — money domain.** `numeric(20, 8)`, `|value| < 10^12`, identical accept/reject set in both
  languages. The set moved out of the TypeScript-only fixture into
  `apps/app/tests/contracts/money_cases.json`: 69 cases, data only, sorted by input, in the
  requested `[{input, valid, canonical}]` shape, for the coordinator to diff against the Python
  half. It covers the boundary (`999999999999.99999999` valid, `1000000000000` invalid), over-scale
  (9 fractional digits), exponents, non-finite, negative zero, leading `+`, whitespace (including
  `"1.00\n"` — the one differential mismatch round 2 found, where the Python `$`-anchored regex
  accepts and TypeScript refuses), empty, leading zeros, and bare `.5` / `5.`.
  `adminGrant` refuses negative, zero, non-decimal and over-scale amounts at the boundary.
- **R13 — console amendments.**
  - `LedgerEntryKind` keeps `purchase` as a legacy read-only value; `orgs.json` carries one
    historical purchase row so it renders, `CREATABLE_LEDGER_ENTRY_KINDS` is what a running system
    may write, and a conformance case asserts nothing creates one.
  - Off-mode requests have no trace row: `traces` excludes them, and `traceDetail` /
    `traceContent` reached from a usage row report availability `off` with `content: null`. This
    reverses what the round-2 README claimed; both the README and the suite now match the ruling.
  - `UsageRow.settlement_state` (and `TraceDetail.settlement_state`) are nullable, null exactly
    while the row is non-terminal — asserted both ways in `assertUsageRow`.
    `pending_reconciliation` now keys off unknown usage with an outstanding hold rather than off
    the settlement column.
  - `judgeRuns` is owner and operator only; a member gets `forbidden`.
  - `ERROR_CODE_HTTP_STATUS` kept as is; round 2's programmatic diff found it equal to the Python
    table and to 08 §3.
  - `tsconfig` stays ES2017: no BigInt literals, only `BigInt()` calls (`git diff` on
    `tsconfig.json` is empty).

## 4. Fixture change

The trace list lost its off-mode rows under R13, which took the established organization below one
page, so `usage_rows` went 137 → 160. The established organization now has **160 usage rows, 107
trace rows and 137 ledger entries** (53 off-mode requests have no trace row) — more than one page
of each at the maximum limit of 100. `orgs.json` also gains `legacy_purchase`.

## 5. Checks

| Command | Exit | Result |
|---|---|---|
| `pnpm --dir apps/app test` | 0 | `# tests 51  # suites 2  # pass 51  # fail 0` (4 pre-existing `lib/` tests + 47 contract tests) |
| `pnpm --dir apps/app lint` | 0 | eslint, no output |
| `pnpm --dir apps/app exec tsc --noEmit` | 0 | clean |
| `git diff --stat fab9fbe -- apps/app/pnpm-lock.yaml apps/app/tsconfig.json` | 0 | empty — both untouched, no dependency added |
| `git diff fab9fbe -- apps/app/package.json` | 0 | `scripts.test` and `engines` only |
| per-commit owned-path check over `fab9fbe..HEAD` | — | all 12 commits touch owned paths only |

Test discovery: the four nested `tests/contracts/*.test.ts` files run under
`"tests/**/*.test.ts"`, and the F-BASE guard (`discovery.test.ts`, 5 tests) walks the console tree
and fails on any test file no declared pattern would run. Round 2's gap is closed: `out`, `build`,
`coverage` and friends are skipped **only at the console root**, so `scripts/build/z.test.ts` is no
longer invisible, and the walker also recognises `.test.mts`, `.test.cts`, `.spec.ts` and
`.spec.tsx` so those spellings are *reported* rather than silently skipped. Drilled in a scratch
copy: the guard named `scripts/build/z.test.ts` and `tests/v/deep/nest/x.spec.ts`, and a failing
test four directories deep executed (`SCRATCH_NESTED_EXECUTED`, 52 tests, 2 failures). The
worktree was not touched.

Adversarial probe against `createFakeConsoleServices` (scratch script, read-only against the
worktree): `limit` as `"5"`/`NaN`/`101` → `invalid_request`; foreign, huge and non-string cursors →
`invalid_cursor`; a 256-character idempotency key → `invalid_request` and 255 → accepted; a
10,000-character key name → `invalid_request`; `amount` as a `Number`, with nine decimals, or
`kind: "purchase"` → `invalid_request`; an impossible date (`2026-13-45T…`) → `invalid_request`;
a row stamped `…:00.000Z` **is** included by `from: …:00Z` (the round-2 lexical-comparison bug is
fixed); `traceContent` on the off-mode request → `off`; a member's `judgeRuns` → `forbidden`;
a second revoke → `state_conflict`, but a second revoke under the same idempotency key → the
original result.

## 6. Non-blocking review items

| Item | Disposition |
|---|---|
| Feedback ids not namespaced (187th submission collided) | Fixed; 400-submission cross-organization test added. The round-2 sentence "every generated id mixes in the org's fixture namespace" was wrong when written; it is true now |
| Python accepts `"1.00\n"`, TypeScript refuses | TypeScript is correct. Now encoded in the shared `money_cases.json` as `valid: false`, so the Python half fails until it uses `fullmatch`/`\Z`. **Forwarded to the Python half / coordinator** |
| Python `INTERNAL_CODES` has an extra `journal_write_failed` (a `TerminalCause` in 08 §3, not an error code) | Not a TypeScript defect. **Coordinator to reconcile**; the TypeScript list matches 08 §3 exactly |
| Discovery SKIP applied at every depth; `.spec.ts` / `.test.mts` uncaught | Both fixed (§5) |
| Idempotency key and key name unbounded | Bounded: 255 (08 §3) and 200 (new, no source — change request below) |
| `from`/`to` compared lexically | Fixed: instants via `Date.parse`, and a timestamp the regex accepts but the calendar does not is `invalid_request` |
| No operator calibration affordance, no org suspension/entitlement controls, feedback had no idempotency key | The third is fixed by R3. The first two remain interface gaps for the coordinator; see the limits below |
| No fixture org is suspended, so `org_suspended` is never exercised | Still true; recorded as a limit, not fixed — it needs a third fixture organization and a session for it, which changes the harness shape C must supply |
| `rate_units_per_token` is a JSON number | Unchanged; 15 and 60 are integer 1e-8 units converted with `BigInt()`, never a float, and no money value in the fixtures is a number |
| Operator submitting feedback on their own org's trace gets `author_role: "operator"` | Still the behaviour; now exercised indirectly (the provenance assertions use a customer session, and the operator path is one branch on `session.isOperator`) |
| Uncommitted change in the `codex-f2py` worktree | Not mine; that worktree was not read or touched this round |

## 7. Limits

- Everything here runs against the fake. Nothing proves C, D or the database.
- `org_suspended` is unreachable through any harness session (above).
- The fake's idempotency records never expire, so `idempotency_expired` (08 §5) cannot be
  exercised. C must implement expiry and will need a case the suite does not yet have.
- `traceContent` ignores signed-URL and storage concerns entirely: the DTO has no such field, so
  the suite can only assert the absence of `s3://` and `X-Amz-` in the serialized view.
- The keyset cursor resolves by scanning the list (O(n)); C will use an index. A cursor whose row
  has disappeared is `invalid_cursor` here, and the suite does not test deletion, so C is free to
  resume after a missing key instead.
- `runMutationSafetyConformance` mutates the harness tenant (grants, keys, feedback, settings), so
  C's factory must hand out a fresh tenant per call. The same was already true of the main suite.

## 8. Contract change requests

1. **Optional `idempotency_key` on `keys.create`, `keys.revoke` and `settings.update`** (as a
   third argument on `revoke`). R3 requires one only for feedback and R13 only for grants, but the
   double-submit that mints two keys or appends two consent-history entries is the same defect. A
   replayed `keys.create` returns the original response *including the secret*; if the coordinator
   would rather not store a presented secret for replay, say so and `keys.create` will decline the
   key instead.
2. **`MAX_KEY_NAME_CHARS = 200` and `MAX_GRANT_REASON_CHARS = 500`** — new bounds with no source
   in 08, added because a key label and a grant reason were unbounded. Confirm or replace the
   numbers.
3. **Unknown input fields are `invalid_request`** rather than ignored, mirroring pydantic
   `extra="forbid"`. Stricter than §9 states, and the thing that makes a smuggled `org_id`,
   `author_role`, `channel` or storage key visible to the caller instead of silently dropped.
   The Python half should refuse the same way (it does, via `extra="forbid"`).
4. **A suspended fixture organization with a session** would make `org_suspended` reachable, at
   the cost of a wider `ConsoleHarness` (a fifth session). Needed if any track must render the
   suspended state.
5. Still open from earlier rounds, now formally: **no operation exists for the V3 operator
   calibration affordance** (`calibration_set` is read-only) or for **U3's org
   suspension/entitlement controls**.

## Verification log

- 2026-09-20: Round 3. Fixed the blocking overflow-after-write finding at its root, swept the
  class across every mutating operation, applied rulings R3/R11/R13, authored the shared
  `money_cases.json`, closed the discovery-guard gap, and recorded five contract change requests.
  `pnpm --dir apps/app test` (51 pass, 0 fail), `lint` and `tsc --noEmit` all exit 0; lockfile and
  `tsconfig.json` untouched; `package.json` differs only in `scripts.test` and `engines`. Five
  mutation drills confirm the new tests fail without each new guard. Status **implemented**; no
  integration, push, deploy or cloud call.
