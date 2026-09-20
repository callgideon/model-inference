# F2-ts — console contracts, round 6 (a runner that cannot lie, B1–B3, R40/R41)

- **Status: implemented.** Fake only; C is integrated when the same exported suite passes against
  real PostgreSQL and ClickHouse.
- Worktree `.claude/worktrees/codex-f2ts`, branch `codex/f2-contracts-ts`, base `fab9fbe`.
- Head at this report: `d9bc366`. Every number below is printed by a command in
  `F2-ts-d9bc366-verify.txt`; none is typed from memory.
- Earlier reports kept as written: `F2-ts-af5856c.md`, `F2-ts-7272695.md`, `F2-ts-9f266de.md`,
  `F2-ts-2959a3d.md`, `F2-ts-b48ffaa.md`, `F2-ts-ab442af.md`.
- Nothing pushed, deployed or run against a cloud service, database or paid provider.

## 1. Blocking findings

### B1 — the runner counted any non-zero exit as a kill

It did, and that made every "killed" claim in the round-5 evidence weaker than it read: a syntax
error, a module that threw on load and an `enum` the type stripper rejects would each have been
reported killed. Rewritten so that a kill requires **a failing case, named, and declared by the
mutant**:

- each mutant declares `cases`; a kill is a `not ok` line whose case name is in that list;
- no TAP summary, or a non-zero exit with no named case failing → **RUNNER-ERROR** (with the reason:
  stripping rejection, unresolvable module, parse failure, or "failed without naming a case");
- a per-child timeout, so a hanging mutant is a runner error rather than a hang;
- a mutant naming a case the suite does not have → the run fails, listed separately;
- a stale or ambiguous `find` → the run fails;
- temp copies are removed on every exit path, including the baseline-failure one (`finally`).

The load check is **structural** — no named case failed — not a search for `SyntaxError`, because a
legitimate mutant makes a case throw one (`MONEY-03` feeds an accepted exponent to `BigInt`). Getting
that wrong was worth one iteration: my first attempt sniffed the output and misclassified MONEY-03 as
a runner error.

`node tests/contracts/run-mutants.mjs --self-test` pins the classifier: **9 self-tests, 9 passed** —
syntax error, load throw, `enum`, real hang (kept alive with `setInterval`, or Node exits on its own
and the timeout path is never exercised), no-op → survivor, stale `find` → stale, a *real* defect
attributed to the wrong case → survivor, a genuine kill → killed, and a mutant with no declared cases
→ runner error.

### B2 — a false kill in the committed catalogue

`IDEM-05` ("was V03") died by `TypeError`, not because a record was filed before its effect. Deleted.
The invariant it claimed — **the idempotency record is written in the same transaction as the
effect** — cannot be observed through `ConsoleServices` at all, because the interface has no failure
injection. It is now recorded as **fake-only** in the README and here, with the note C needs: a store
that commits the effect and the record separately will double-apply a retry, and no conformance run
will ever tell it so. The fake's `after_write` injection covers it.

Two more mutants went the same way rather than being quietly kept: `CURSOR-08` (audit timestamps are
unique by construction, so the id tiebreak is unobservable there) and `LABEL-07` (the customer id
sequence is only observable because the fake's ids are deterministic — moved to a fake-only test).
Every remaining mutant declares its cases and dies on an assertion, except three that kill by
throwing where the throw *is* the defect, now marked `kills_by: "throw"`: **GRANT-04, MONEY-03,
MONEY-04**.

### B3 — invariants the suite named but could not enforce

| Survivor | Now enforced by |
|---|---|
| `adminAudit` operator-only only for `{}` | Four sessions × four query shapes, including an owner asking for **its own** organization's entries (`AUDIT-11`) |
| R35 `has_feedback` check vacuous | The label is applied to a trace taken from the *unannotated* list; the case snapshots eleven customer views and asserts `deepEqual` across the label (`LABEL-06`, `LABEL-03`) |
| R35 checked only the label it created | Every entry of `calibration.list`, against every session that can read its request — seeded labels included — plus the operator-principal sweep |
| "a replay adds no audit entry" | Replayed grant, suspension, entitlement and label, then a conflicting grant and suspension: the audit must be byte-identical (`AUDIT-08`) |
| audit before/after only for suspension | A grant's `after.ledger_total` is the total it produced, an entitlement's `before` is the previous value (`AUDIT-09`, `AUDIT-10`) |
| `operatorMember` used only on operator-only ops | The matrix now runs it over **every** operation: owner-only must still refuse it (`ROLE-05`), reads must allow it, and every operator operation must work while the operator's *own* organization is suspended (`SUSP-03`) |
| "a suspension can be lifted" | A dedicated case: restore clears it, what it gated works again, keys are untouched (`SUSP-04`), a restore is not a no-op (`SUSP-05`), and a grant to a suspended organization is **allowed** (`SUSP-06` — accounting is independent of status) |
| WalletBalance identity on `AdminGrantResult.balance` | Asserted on a grant to the organization that *has* holds, and against what the organization reads (`GRANT-06`) |
| the cheap unclaimed ones | Entitlement limits replaced not merged (`ENT-05`); `calibration.list`, `judgeRuns` and `adminAudit` walked at limit 1 (`CURSOR-07`); a fresh ledger read ordered after a grant (`GRANT-07`); a label bumps no score count, no consent history and no customer comment |

## 2. Rulings applied

- **R40** — the merge criterion. The runner cannot report a false kill (B1, with self-tests), the
  committed list is clean of false kills (B2), and every count in this report comes from command
  output. **130 mutants, 130 killed by a named declared case, 0 survived, 0 stale, 0 runner errors,
  21.4 s.** Where the reviewer's corpus overlaps mine, the `was` field records their id; I have not
  seen their catalogue, so I cannot claim to have reproduced it — that check is theirs to run.
- **R41** — no customer-session view exposes an operator principal. Ledger rows keep an internal
  `by_operator` flag and the read projects `actor` to the literal `platform` (`PLATFORM_ACTOR`) for a
  customer session, while an **operator** session reading the same ledger still sees who acted, so
  the masking is a view and not missing data. Consent history records `platform` when an operator
  changes settings — including an operator who happens to own the organization. `adminAudit` always
  carries the real principal. The conformance case sweeps eleven customer views for both an owner and
  a member against both operator principals, and checks the audit still names them (`R41-01`,
  `R41-02`, `GRANT-01`). Operator labels and audit entries were also moved to their own clock and id
  prefix (`cal_…`), so an operator action leaves no observable mark on a customer's sequences.

## 3. Non-blocking items

| Item | Disposition |
|---|---|
| Evidence said "40 `it(` cases" | Wrong. Measured: **43** exported cases (`grep -c '^    it('`), `services.test.ts` **52** tests, `opaque-cursor.test.ts` **44**. Everything in §4 is quoted from the transcript |
| README U/V list missing `operatorMember`/`modelId` | Fixed: six sessions and ten ids, each with why it exists |
| Labels sharing the customer's id and clock sequence | Fixed: `cal_` prefix, its own counter and its own clock, shared with audit entries. Pinned by a fake-only test that runs two instances and requires the customer's second id and timestamp to be identical with and without an intervening label |

## 4. Checks (all quoted from `F2-ts-d9bc366-verify.txt`)

| Command | Exit | Result |
|---|---|---|
| `pnpm --dir apps/app test` | 0 | `# tests 120  # suites 4  # pass 120  # fail 0` |
| `pnpm --dir apps/app test:mutants` | 0 | `130 mutants: 130 killed by a named declared case, 0 survived, 0 stale, 0 runner errors, 21.4s` |
| `node tests/contracts/run-mutants.mjs --self-test` | 0 | `9 self-tests, 9 passed, 0 failed` |
| `pnpm --dir apps/app lint` | 0 | eslint, no output |
| `pnpm --dir apps/app exec tsc --noEmit` | 0 | clean |
| `pnpm --dir apps/app build` | 0 | compiled; `git status` clean afterwards |
| `git diff --stat fab9fbe -- apps/app/pnpm-lock.yaml apps/app/tsconfig.json` | 0 | empty |
| `git diff fab9fbe -- apps/app/package.json` | 0 | `scripts.test`, `scripts.test:mutants`, `engines` |
| `git rev-list --count fab9fbe..HEAD` | — | 27 commits, all touching owned paths only |

Per-file test counts, as printed: `money.test.ts` 11, `discovery.test.ts` 5, `fixtures.test.ts` 4,
`services.test.ts` 52, `opaque-cursor.test.ts` 44, `lib/keys.test.ts` 3, `lib/utils.test.ts` 1.
Exported conformance cases: 43.

## 5. Limits

- Still fake-only, and the mutation corpus is mine: 130 single edits over three files, one per named
  invariant. A defect no declared mutant expresses can still hide, which is why R40 makes the
  reviewer's corpus the criterion rather than mine.
- **Two invariants no exported case can enforce**, both now stated as fake-only rather than implied
  by a case: the idempotency record committed with its effect (C gets this from its transaction), and
  the label id/clock sequences (only observable because the fake is deterministic).
- The runner strips types without typechecking, so a mutant may be type-invalid while runtime-valid.
  Deliberate — several write-before-validate mutants would not compile — but it means
  `pnpm test:mutants` is not a substitute for `tsc`.
- `idempotency_expired` and the overflow branch on read paths remain unreachable from outside.
- R41 masks the principal; it does not encrypt it. An operator session reading the ledger sees the
  identity, which is the intent, so any future non-operator view added to that path must re-apply the
  projection rather than assume the row is safe.

## 6. Open questions

None undecided. R40 leaves the merge decision with the reviewer's corpus, which is the right place
for it: I cannot certify my own list is complete, only that every claim in it is now attributable.

## Verification log

- 2026-09-20: Round 6. Rewrote the mutation runner so a kill requires a named declared case and every
  non-suite outcome is a runner error, with nine self-tests; deleted the one false kill and recorded
  the invariant it claimed as fake-only; closed the nine claimed-but-unenforced invariants with new
  and strengthened cases; applied R41 across ledger actors, consent history and every customer view.
  `pnpm test` 120 pass 0 fail; `test:mutants` 130/130 killed, 0 survivors, 0 runner errors;
  self-tests 9/9; `lint`, `tsc --noEmit` and `build` all exit 0; lockfile and `tsconfig.json`
  untouched. Status **implemented**; no integration, push or deploy.
