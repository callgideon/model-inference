# F2-ts — console contracts, round 5 (the suite made enforceable: B1–B11, R32–R36)

- **Status: implemented.** Fake only; C is integrated when the same exported suite passes against
  real PostgreSQL and ClickHouse.
- Worktree `.claude/worktrees/codex-f2ts`, branch `codex/f2-contracts-ts`, base `fab9fbe`.
- Head at this report: `ab442af`. Raw output, with every count and command printed rather than
  typed: `F2-ts-ab442af-verify.txt`.
- Earlier reports kept as written: `F2-ts-af5856c.md`, `F2-ts-7272695.md`, `F2-ts-9f266de.md`,
  `F2-ts-2959a3d.md`, `F2-ts-b48ffaa.md`.
- Nothing pushed, deployed or run against a cloud service, database or paid provider.

## 0. What this round was actually about

The review found the fake sound — no tenant, role, secret, provenance or money defect, Python parity
exact — and the **exported suite** weak: 50 of 183 mutants survived it, 31 on invariants the case
titles claimed. A case that names an invariant it does not enforce is worse than no case, because it
tells track C the invariant is checked. Every change below is about what C is held to.

## 1. Blocking findings

| # | Finding | Disposition |
|---|---|---|
| B1 | The suite decoded the cursor (`JSON.parse(atob(...))`), so C could not pass with an opaque or signed cursor | Fixed per R36. The exported cases now forge only black box: a cursor from another list, another tenant or another filter, and this one with a character appended, removed, prepended, the characters reversed or the case flipped. The shape-aware forgeries moved to `tests/contracts/services.test.ts`. **Proof**: `tests/contracts/opaque-cursor.test.ts` runs the whole exported suite (39 cases) against a wrapper that reverses every `next_cursor` and prefixes it `v1.`, and asserts the fake itself answers `invalid_cursor` for that form — so a suite that parsed a cursor could not pass there |
| B2 | The harness hid caller/target confusion: the only operator owned the target organization | Fixed. `sessions.operatorMember` is an operator whose org role is only `member`; the new case "an operator acts on the organization it names, not on its own" targets `ids.otherOrgId` / `ids.otherOrgRequestId` through it and asserts the result's `org_id`, the ledger row's `actor`, `entitlements.updated_by`, the label's `author_principal`, and that the operator's *own* organization was not touched. The role matrix runs every operator operation through the member-operator as well. Mutants TARGET-01/02/03, LABEL-01/02, ROLE-04, GRANT-02 |
| B3 | The invented-field case covered 7 of 15 inputs | Fixed. One `operationProbes` table drives it from `CONSOLE_OPERATIONS`; `assertProbesCoverEveryOperation` fails if an operation has no probe or if an operation that takes an object has no extra-field probe (`OPERATIONS_WITHOUT_INPUT_OBJECT` declares the exceptions). 16 object-taking operations covered, plus a string, an array and `null` for each. Mutants INPUT-01, XINPUT-01 |
| B4 | The org part of the idempotency scope was untested and conflicts changed two fields at once | Fixed. Every payload field of every mutating operation is now changed **alone** (14 tenant-scoped assertions plus 3 suspension, 5 entitlement and 4 label fields), and the same key used from a second organization must *apply*, not replay. Mutants IDEM-01/02, PAYLOAD-01…13 |
| B5 | Validate-before-write covered 5 of 8 operations, single-field only | Fixed. All eight, 15 refusals, each with valid earlier fields and one invalid **later** field, with a deep-equal snapshot of ledger, usage, keys, settings, feedback, balance, operator list, labels, audit and the other organization's state either side. The case asserts its own coverage against `MUTATING_OPERATIONS`. Mutants ORDER-01…06 |
| B6 | The filtered-walk case could not detect a truncated walk | Fixed. The filtered set is computed first; after the cursor row leaves the filter the next page must be non-empty, the walk must terminate, and the collected ids must deep-equal the precomputed set. Mutant CURSOR-02 and the lookup-resume mutant both die |
| B7 | Holds: double-counting survived, and the assertion was guarded by `if reserved_total > 0` | Fixed. The harness must hold at least one outstanding hold; `reserved_total` must equal the sum of every non-null `max_hold` over the walked rows, `available` must equal `ledger_total − reserved_total` exactly, `pending_reconciliation` must equal the unknown-usage held sum and be non-zero, and the operator's view of the wallet must deep-equal the customer's. Mutants HOLD-01…04, BAL-01 |
| B8 | Tenant isolation untested for `settings.get/update`, `usageSummary`, `usageDaily` | Fixed. Per-organization expectations computed from the walked rows (requests, cost, tokens, failures, day set), both organizations asserted, the two required to differ, and writes asserted in **both** directions — ours invisible to them and theirs invisible to us. Mutants TENANT-02…05 |
| B9 | `feedback.submit` accepting `calibration_label`, and `calibration.list` returning everything | Fixed and pinned in the R35 case. Mutants LABEL-04, LABEL-05 |
| B10 | The status table case pinned the count, not the values | Fixed: the expected 27-entry map is embedded and compared with `deepEqual`. Mutants CODE-01/02/03 |
| B11 | "each is audited" was not true | Fixed per R34 (below) |

## 2. Rulings applied

- **R32 — conformance strength.** `pnpm test:mutants` runs `tests/contracts/run-mutants.mjs` over
  `tests/contracts/mutants.json`: **115 declared single-edit mutants**, each applied to a temporary
  copy (never the worktree) and required to fail at least one case of the *exported* functions — the
  runner writes its own entry point that calls only `runConsoleServicesConformance`, so a mutant
  killed merely by a fake-only test still counts as a survivor. A survivor or a stale mutant (one
  whose `find` text no longer matches, or matches more than once) exits non-zero. Measured:
  **115 mutants, 115 killed, 0 survived, 0 stale, 14.8 s** at four parallel jobs. Not part of
  `pnpm test`. The two mutants the coordinator named as fake-only kills are now conformance kills:
  IDEM-06 (`keys.revoke` ignores its key) and IDEM-05 (the record written before the effect).
- **R33 — suspension scope.** `tenant()` takes the operation and consults
  `SUSPENDED_ALLOWED_OPERATIONS`: every read plus `keys.revoke` stays available — a leaked key must
  be revocable whatever the organization's status — while `keys.create`, `settings.update`,
  `feedback.submit` and `judgeRuns` return `org_suspended`, and operator operations keep working so a
  suspension can be lifted. The contradictory `services.ts` comment and README line are corrected,
  and the role/suspension matrix case walks every operation. Mutants SUSP-01, SUSP-02.
- **R34 — audit trail.** `adminGrant`, `adminSetSuspension`, `adminSetEntitlements` and
  `calibration.label` append an immutable `{id, at, actor_principal, action, target_org_id, reason,
  before, after, idempotency_key}`; operator-only `adminAudit` pages them, filtered by target,
  newest first, strictly totally ordered. The conformance case asserts five writes append exactly
  five entries with the right actions, that each names actor, target, reason and key, that a
  **restore adds** a second `suspension_set` entry with its own reason rather than overwriting the
  first, and that the entitlement reason — which the entitlement record deliberately does not keep —
  survives in the audit. Mutants AUDIT-01…07, GRANT-01.
- **R35 — label visibility.** Calibration labels live in a per-organization `labels` list, not in
  `traces[].feedback`, so invisibility is structural rather than a filter someone must remember. The
  case labels a request as an operator and then asserts, from the owner *and* member sessions, that
  the label is absent from `feedback.list` and `traceDetail.feedback`, that `feedback_count` and the
  `has_feedback` partition do not move, and that neither the operator principal nor the operator's
  note appears anywhere in a customer response. Mutants LABEL-03, LABEL-04.
- **R36 — cursor opacity.** As B1.

## 3. Non-blocking items applied

| Item | What changed |
|---|---|
| Replays return original results | Documented: a replayed suspension, entitlement write or label returns the first call's body, so U3 must re-read the state it renders. Only `adminGrant` refreshes the balance |
| Stale fake-only cursor lines | Replaced with what the cursor actually is (`{a, b, k}`, O(n) resolve), why the shape is not contract, and the note that it is tamper-*evident* for scope but not tamper-proof — swapping the key component resumes elsewhere rather than failing, which is why that case is absent |
| `compareKeys` string-orders `created_at` | `assertComparableTimestamps` requires every timestamp in one list to have the same width and match RFC 3339, which is the real precondition for a lexical keyset resume; the README makes it a harness requirement |
| Two organizations' first key shared a prefix and secret | The key suffix now mixes in the organization namespace |
| Hand-typed counts | Every count in this report is printed by a command in `-verify.txt`: per-file test counts, `it(` count, mutant count, and `git rev-list --count` for the commit total (24) |

## 4. Checks

| Command | Exit | Result |
|---|---|---|
| `pnpm --dir apps/app test` | 0 | `# tests 109  # suites 4  # pass 109  # fail 0` |
| `pnpm --dir apps/app test:mutants` | 0 | `115 mutants, 115 killed by the exported conformance suite, 0 survived, 0 stale, 14.8s` |
| `pnpm --dir apps/app lint` | 0 | eslint, no output |
| `pnpm --dir apps/app exec tsc --noEmit` | 0 | clean |
| `pnpm --dir apps/app build` | 0 | compiled; `git status` clean afterwards |
| `git diff --stat fab9fbe -- apps/app/pnpm-lock.yaml apps/app/tsconfig.json` | 0 | empty — untouched, no dependency added, ES2017 with `BigInt()` calls only |
| `git diff fab9fbe -- apps/app/package.json` | 0 | `scripts.test`, the new `scripts.test:mutants` (the coordinator's one relaxation) and `engines` |
| per-commit owned-path check, `fab9fbe..HEAD` | — | all 24 commits touch owned paths only |

Measured test counts, per file: `money.test.ts` 11, `discovery.test.ts` 5, `fixtures.test.ts` 4,
`services.test.ts` 46, `opaque-cursor.test.ts` 39, plus the 4 pre-existing `lib/` tests. The exported
suite is 40 `it(` cases across `runConsoleServicesConformance` and `runMutationSafetyConformance`;
`services.test.ts` runs them once (46 = 40 conformance + 6 fake-only) and `opaque-cursor.test.ts`
runs 39 of them again through the wrapper plus its own wrapper check.

## 5. Limits

- Still fake-only. A green mutation run says the suite can *detect* these defects, not that C is
  correct.
- The mutation list is one mutant per invariant, not exhaustive: 115 single edits over three files.
  A defect no declared mutant expresses can still hide. The ids are this repository's; the `was`
  field records the reviewer's id where their description identified the mutant, and I did not
  attempt to reconstruct their 183-mutant catalogue.
- `run-mutants.mjs` copies the console per job and runs `node --test` without typechecking, so a
  mutant may be type-invalid as long as it is runtime-valid. That is deliberate — several
  write-before-validate mutants would not compile — but it means `pnpm test:mutants` is not a
  substitute for `tsc`.
- The suite cannot prove a secret is *stored* nowhere, only that no response returns it; the deep
  scan needs `unsafeDebugState()`, which only the fake has.
- `idempotency_expired` remains unexercised (the fake's records never expire).
- N8 (read paths reporting `internal_error` on an overflowing aggregate) is still unreachable from
  outside, so no mutant covers it.

## 6. Open questions

None new. R24–R26 and R32–R36 close everything this half had raised; the only judgement calls left
are the provisional numbers R17 already labelled as such.

## Verification log

- 2026-09-20: Round 5. Rebuilt the exported suite so every invariant it names is enforceable, added
  the mutation runner that proves it (115/115 killed), gave suspension its scope (R33), added the
  operator audit trail and `adminAudit` (R34), made calibration labels structurally invisible to
  customers (R35), and made cursor handling opaque with a wrapper proof (R36). `pnpm test` 109 pass
  0 fail; `test:mutants`, `lint`, `tsc --noEmit` and `build` all exit 0; lockfile and `tsconfig.json`
  untouched. Status **implemented**; no integration, push or deploy.
