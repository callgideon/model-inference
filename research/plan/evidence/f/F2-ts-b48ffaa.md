# F2-ts — rulings R24, R25 and R26 (addendum to round 4)

- **Status: implemented.** Still fake-only; C is integrated when the same suite passes against real
  PostgreSQL and ClickHouse.
- Worktree `.claude/worktrees/codex-f2ts`, branch `codex/f2-contracts-ts`, base `fab9fbe`.
- Head at this report: `b48ffaa`. Raw output: `F2-ts-b48ffaa-verify.txt`.
- Earlier reports kept as written: `F2-ts-af5856c.md`, `F2-ts-7272695.md`, `F2-ts-9f266de.md`,
  `F2-ts-2959a3d.md` (round 4, whose verification log now points here).
- Nothing pushed, deployed or run against a cloud service, database or paid provider.

## R24 — entitlements accepted, and made fail-closed

`OrgEntitlements` (`model_ids` plus named integer limits from the closed `ENTITLEMENT_LIMIT_NAMES`
set) is the contract. The change this ruling asked for is that the default is *explicit*, because
"nobody has decided" and "decided: nothing" were both representable as an empty list:

| `model_ids` | Meaning | In the fixtures |
|---|---|---|
| `null` | The platform default set; no per-organization decision recorded, so `updated_at` and `updated_by` are null too | Fresh Pilot |
| `[]` | **Nothing entitled** — every admission fails `model_not_entitled`; a recorded, audited decision | Halted Pilot |
| non-empty | Exactly that set | Northwind Labs (two models) |

Measured from the fake (`verify.txt`): `Fresh Pilot -> null updated_at null`,
`Halted Pilot -> [] updated_at 2026-08-25T10:00:00.000Z`,
`Northwind Labs -> ["deepseek-v41-flash@2026-08-15","marlin-2b@2026-09-01"]`.

Validation on `adminSetEntitlements`: `null`, or a list of at most 100 ids, each one a model the
platform serves (`KNOWN_MODEL_IDS` here; C validates against D1's catalogue — only the shape of the
check is contract), with no duplicates. `null` and `[]` produce *different* idempotency payloads, so
a key replayed from one to the other is `idempotency_conflict` rather than a silent switch between
the default and a denial. `ids.modelId` gives the harness a served model id, which also replaced the
one model literal the cursor test still hardcoded.

Conformance: a new case asserts all three states are present in the harness, that a stored list never
repeats, that a `null` list carries no audit stamp, that setting `[]` then `null` reads back as
exactly those two values, that the same key cannot mean both, and that a duplicate or unserved model
id is `invalid_request`. The fixture test asserts one organization of each kind, that every entitled
model is served, and that every limit name is in the vocabulary. README gains the state table with
copy guidance for U3 ("Platform default models" / "No models — all requests will be refused" / the
named list), because rendering the first two identically is how an operator suspends a tenant by
accident.

## R25 — `journal_write_failed` is an internal-only code

Added to `ERROR_CODES` with a null HTTP status, and `IN_STREAM_ONLY_CODES` and
`INTERNAL_ONLY_CODES` are now exported so the G0 parity test can compare all three sets against the
Python `error_codes.json` set by set. Measured: **37 codes total, 27 with an HTTP status, 2 in-stream,
8 internal**. The internal set is
`already_terminal, ambiguous_submission, budget_exceeded, capacity_unavailable, consent_missing,
journal_write_failed, not_claimable, stale_lease` — identical to the Python half's `internal_only`,
which closes the last cross-half difference this half had forwarded (rounds 2, 3 and 4). The
conformance case asserts the partition whole: the three sets cover the union with no overlap, and no
non-HTTP code carries a status. `journal_write_failed` remains a `TerminalCause`, which the same case
asserts.

## R26 — platform-wide operator scope, recorded

No code change: `calibration.label` already takes the tenant from the row it names and audits the
operator principal. The README now states platform-wide scope as a deliberate contracts-v1 pilot
decision rather than an oversight, and names `calibration.label`/`calibration.list` as where a future
org-subset scoping rule would land.

## Mutation drills

| Mutation | Failures |
|---|---|
| `null` collapsed to `[]` on write (R24) | 1 — the three-states case |
| `null` and `[]` share an idempotency payload (R24) | 1 — same case |
| Unserved model ids accepted again (R24) | 1 — same case |
| `journal_write_failed` dropped from `INTERNAL_ONLY_CODES` (R25) | 1 — the partition case |

Scratch copies of `apps/app` (`rsync`, `node_modules` symlinked); the worktree was never modified.
The twelve drills from earlier rounds still hold, unchanged.

## Checks

| Command | Exit | Result |
|---|---|---|
| `pnpm --dir apps/app test` | 0 | `# tests 62  # suites 2  # pass 62  # fail 0` (4 pre-existing `lib/` + 58 contract: 11 money, 5 discovery, 4 fixtures, 38 in `services.test.ts` — 31 conformance across the two exported suites plus 7 fake-only) |
| `pnpm --dir apps/app lint` | 0 | eslint, no output |
| `pnpm --dir apps/app exec tsc --noEmit` | 0 | clean |
| `pnpm --dir apps/app build` | 0 | compiled; `git status` clean afterwards |
| `git diff --stat fab9fbe -- apps/app/pnpm-lock.yaml apps/app/tsconfig.json` | 0 | empty — untouched, ES2017 with `BigInt()` calls only |
| `git diff fab9fbe -- apps/app/package.json` | 0 | `scripts.test` and `engines` only |
| per-commit owned-path check, `fab9fbe..HEAD` | — | all 19 commits touch owned paths only |

## Open questions

None new. Q1–Q4 from round 4 are all answered by R24–R26; the Python-side internal-code difference
that had been forwarded three times is closed by R25.

## Verification log

- 2026-09-20: R24 (explicit fail-closed entitlement states, validation against the served model set,
  fixtures and copy guidance), R25 (`journal_write_failed` internal-only; the 27 / 2 / 8 partition
  exported and asserted) and R26 (platform-wide operator scope documented) applied. 62 tests pass,
  `lint`, `tsc --noEmit` and `build` exit 0; lockfile and `tsconfig.json` untouched. Four mutation
  drills confirm the new guards. Status **implemented**.
- 2026-09-20: Round 5 followed, recorded in `F2-ts-ab442af.md`: the exported conformance suite was
  rebuilt so every invariant it names is killable, with `pnpm test:mutants` (115 mutants, 115 killed)
  as the standing proof.
