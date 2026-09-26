# APP-UNION round 2: C3A + U4 + U2 + U3 onto the I2A-PREP tip, with coordinator wiring

## Task and status

| Field | Value |
|---|---|
| Lane | APP-UNION round 2 (Opus integrator; consumer-v1, program 22) |
| Status | **review**: four verified lanes merged in the given order, eight wiring commits applied (one of them reverted, see WR-U2-3), one plan-link fix, every named check green |
| Branch / worktree | `codex/app-union-2` / `.claude/worktrees/codex-app-union-2` |
| Base | `35bd43dd`, as dispatched: `claude/consumer-v1` with I2A-PREP. The tip has since moved to `1f9c99ef`, then `818667e9`, and `git merge-tree` of this head against `818667e9` is clean. |
| Code head | `2fad3510` (this file is committed on top of it) |
| Isolation | Task-local Docker only (`app-c0`, `app-u1r`, `app-u4`, `app-c3a`, `app-u3` blocks, D harness), each removed at exit. `docker ps -a` and `docker network ls` show nothing left behind. No hosted Supabase, pilot box, AWS or SSM. No secrets. Nothing pushed, rebased, reset, amended or stashed. |

## Merge order and conflict resolutions (first parent)

| SHA | Merge | Conflicts |
|---|---|---|
| `f7ebc726` | `codex/app-c3a` (`91af4caf`) | none |
| `f702f989` | `codex/app-u4` (`d76f912f`) | none |
| `caabf2e4` | `codex/app-u2` (`2bd04497`) | `apps/app/tests/u/run-mutants.mjs`, 2 hunks: the SUITE list and the mutant list |
| `6883dbe0` | `codex/app-u3` (`b80c4cd8`) | `apps/app/tests/u/run-mutants.mjs`, 3 hunks: the file constants, the SUITE list and the mutant list |

- Both conflicts were resolved as the **union** you directed: the parent's entries first, then the incoming lane's. No mutant was edited.
- After `caabf2e4`, the merged id set equals the union of U4, U2 and the parent: 182 ids.
- After `6883dbe0`, it equals the union of the parent and U3: 206 ids, which is 204 mutants plus 2 SELF checks.
- `node --check` passes, so there are no duplicate `const` names.
- Per lane, the U list holds U1 64, U1R 33, U4 43, U2 40 and U3 24.
- No other file conflicted. U2 and U3 had each merged C3A and U1R already, so those arrived as the same commits.

## Wiring commits (each with its proof)

| SHA | Wiring | Source | Proof |
|---|---|---|---|
| `d6ba0028` | **WR-U4-1**: Makefile `console-pg` and `.PHONY` | U4-5bf1bc2.md, recipe verbatim | `make console-pg`: credit_world 6/6; request_world 7 pass + 1 todo (U4-P08, the known WR-U4-2 gap) |
| `bad5ced3` | **WR-U3-2**: `app/actions.ts` passes `operator: operatorRpcPort(async () => (await createClient()) as unknown as OperatorRpcClient)`, with the import and doc line | U3-d8da867.md, diff verbatim | New `tests/u/wiring-u3.test.ts` U3-W02: 0/1 on the unwired file, 1/1 after. U3-DB09 on the real stack (`console-u3-real`) passes. |
| `8a54867a` | **WR-U2-1**: sidebar Settings entry (lucide `Settings` import and NAV row) | U2-9f4f0b0.md, hunks verbatim | `tests/u/wiring-u2.test.ts` U2-W01, verbatim: 0/1 before, 1/1 after |
| `0d5b55a6` | **WR-U2-2**: removes `lib/keys.ts` `STORE_PREFIX`/`rememberKey`/`recallKey` and `snippet.tsx`'s `recallKey`/`useStoredKey` path and its "full key available in this browser session" copy | U2-9f4f0b0.md. The tip's A3 `snippet.tsx` matched the evidence hunks. | Before the edit, `components/snippet.tsx` was the only reader (grep over `app`, `components`, `lib`); after it, nothing names them. U2-W02, verbatim: fails before, 2/2 after. |
| `47368223` → **reverted** `0a17a75e` | **WR-U2-3**: `api-keys/view-model.ts` re-exports `REVOCATION_COPY` from `../docs/content.ts` | U2-9f4f0b0.md | **Conflicts with I2A-BUNDLE-01**; see below |
| `d4bbc663` | **WR-U3-4**: the sidebar `/admin` entry is labelled "Operator" | U3-d8da867.md | U3-W04: 1 fail on "Admin", passes after (2/2 with W02) |
| `82fd8c55` | **WR-C3A-6 / WR-U3-3**: Makefile `console-c3a-real` and `console-u3-real`, plus `.PHONY` | C3A-3f4e5f1.md "restated, exact"; U3-d8da867.md | Both targets 9/9 (below) |
| `2fad3510` | Plan link fix, not a lane wiring | — | U3 deleted `admin/actions.ts` (WR-C3A-2), which broke a link in `research/plan/handoffs/C-console-services.md` and failed `validate_plan`. The link is now plain text with the reason, and a line is appended to that file's verification log. `validate_plan`: PASS. |

### WR-U2-3 was reverted: a conflict for you to decide

With the re-export applied, `make console-test` failed one case: I2A-BUNDLE-01 (`tests/i2a/surface.test.ts`, from I2A-PREP on the tip). The case reported:

```
app/(console)/api-keys/create-key-dialog.tsx → app/(console)/models/catalog.ts: INFRX_API_BASE_URL
app/(console)/api-keys/revoke-button.tsx → app/(console)/models/catalog.ts: INFRX_API_BASE_URL
```

- The path is a client dialog → `view-model.ts` → `docs/content.ts`, whose `import type { PriceView } from "../models/catalog.ts"` reaches `catalog.ts`. `catalog.ts` names the server-only variable (`API_BASE_ENV = "INFRX_API_BASE_URL"`).
- The import is type-only, so it is erased from the real bundle.
- I2A's source walk deliberately does not distinguish type imports.
- U2's claim that "the dialog stays client-safe" was true for C0's client-boundary walker, which predates I2A. It does not hold against I2A's stricter walk.
- You asked me to report other conflicts before choosing a composition, so I reverted WR-U2-3 with `git revert`, as a new commit. The branch keeps U2's own literal:
  - U2-K06 pins it in `keys-view-model.test.ts`.
  - A3's `content.test.ts` pins the identical text.
- The revert commit carries git's default message, without the session attribution trailer. I did not amend it.

Options for you:

- **(a)** Keep two pinned copies, as now.
- **(b)** Make I2A's walker skip `import type`. That is I2A's oracle and needs its owner's agreement.
- **(c)** Move `REVOCATION_COPY` into a leaf module, for example `lib/copy/revocation.ts`, that both `docs/content.ts` (A3) and the keys page model re-export. This edits A3's module.

Before the revert, U2-M10 had been repointed to `docs/content.ts` and was killed; the revert restores its original anchor.

## Verified, not re-implemented

- **WR-C3A-1 (U2 applied it).**
  - `apps/app/app/(console)/api-keys/actions.ts` does not exist.
  - `create-key-dialog.tsx` imports and calls `createConsumerKey` from `@/app/actions`, and `revoke-button.tsx` calls `revokeConsumerKey`.
  - `tests/u/keys-source.test.ts` pins the deletion.
- **WR-C3A-5 (withdrawn by C3A's fix round).** A2's `app/(auth)/grant.ts` `claimSignupGrant`, campaign `consumer-v1`, is the one adapter.
  - It is composed by the auth callback (`app/auth/callback/route.ts`, `claim: claimSignupGrant`).
  - It is composed by the onboarding page's action (`app/(auth)/welcome/actions.ts` `claimOnboarding` → `claimSignupGrant`), which `retry.tsx` and the sign-in form (`afterSignIn(claimOnboarding, …)`) use.
  - The console layout's `ROUTES.onboarding` is `/welcome`.
  - C3A's `lib/services/actions.ts` carries no grant.
  - Nothing was applied.
- **Known gaps left as the branches have them** (both flip at the 0024 merge):
  - C3A case 9 prints `ACCEPTED (gap)`: WR-C3A-4, an unverified direct key insert.
  - U4-P08 is `not ok 8 # TODO`: WR-U4-2, `consumer_job_result` returns an unknown-usage success's body.

## Commands (union worktree; the code checks ran at `0a17a75e`)

`2fad3510` changes only a plan document, so every code result below holds at the code head.

| Command | Exit | Result |
|---|---|---|
| `cd apps/app && pnpm install --frozen-lockfile`; `make api-env` | 0 / 0 | pinned |
| After the 4 merges: `pnpm test` / typecheck / lint | 0 / 0 / 0 | 632 tests: 581 pass, 0 fail, 51 skipped / clean / 0 errors, 2 old warnings |
| `make console-test console-lint console-typecheck console-mutants` with WR-U2-3 applied (`47368223`…`82fd8c55`) | 2 | 637 tests: 585 pass, **1 fail** (I2A-BUNDLE-01), 51 skipped. The chain stopped at console-test. |
| **`make console-test console-lint console-typecheck console-mutants`** | **0** | See the breakdown below |
| `make console-built` (`pnpm build` + `tests/i2a/*.test.ts`) | 0 | compiled; 22/22 |
| `make console-c0-real` | 0 | 12/12 |
| `make console-pg` | 0 | credit_world 6/6; request_world 7 pass + 1 todo (U4-P08, known gap) |
| `make console-c3a-real` | 0 | 9/9; case 9 `ACCEPTED (gap)` (known) |
| `make console-u3-real` | 0 | 9/9, including U3-DB09. The stack applies `operator_rpc_proposed.sql` in-test until D10 ships WR-U3-1. |
| `python3 research/plan/scripts/validate_plan.py` | 1, then **0** | Before `2fad3510`: broken link to the deleted `admin/actions.ts`. After: PASS (133 tasks; 939 links across 231 documents). |
| `git merge-tree --write-tree --name-only HEAD claude/consumer-v1` (`818667e9`) | 0 | no conflicts |
| Wiring negative controls: U3-W02, U2-W01, U2-W02, U2-W03, U3-W04 on scratch copies of the pre-wiring files | 1 each | each case fails before its wiring |

Breakdown of the final `make console-test console-lint console-typecheck console-mutants`:

- console-test: 636 tests, 585 pass, 0 fail, 51 skipped. The skips are the real-database files, which run through the Docker targets.
- console-lint: 0 errors, 2 old warnings.
- console-typecheck: clean.
- console-mutants:

  | Runner | Result |
  |---|---|
  | contracts | 14/14 self-tests; 212/212 killed |
  | V | 40/40 |
  | **U (unioned)** | 2 self-checks; **204/204** (U1 64, U1R 33, U4 43, U2 40, U3 24) |
  | C | 4 self-tests; **176/176**, 0 stale (C0 38, C3A 34, earlier C1) |
  | A2 | 45/45 |
  | A3 catalog | 2 self-checks; 46/46 |

Logs are in the session scratchpad, `…/7aae6bdd-…/scratchpad/logs2/`:

| Log | Run |
|---|---|
| `f-make2.log` | the final console chain |
| `f-make.log` | the failing run with WR-U2-3 applied |
| `f-built.log` | console-built |
| `f-console-c0-real.log`, `f-console-pg.log`, `f-console-c3a-real.log`, `f-console-u3-real.log` | the four Docker targets |
| `f-validate.log` | validate_plan |
| `m-test.log` | the post-merge console tests |
| `w-*.log` | the first Docker runs, one per wiring |

## Open items (recorded, not applied)

- **WR-U2-3**: the decision above.
- `SUPPORT_EMAIL` in `settings/view-model.ts` could import A3's. It was optional and was not applied.
- **D10**:
  - WR-C3A-4 (the unverified key-insert RLS gap).
  - WR-U4-2 (`consumer_job_result` refuses unknown usage).
  - WR-U3-1 (ship `operator_rpc_proposed.sql` as a migration, then delete the in-test apply line).
  - WR-C3A-3(b) (durable cross-instance key-create replay).
- **Still open from round 1**: U1R WR-3(a/b/c); WR-U4-3 (optional `job_handle`/`settled_at`); WR-U4-4 and U1R WR-6, both about one consumer read port; C0 WR-5/6/7; A2 WR-A2-3.
- **Hosted behaviour until WR-U3-1 lands:** operator changes in the hosted App answer "not confirmed", which is honest. The unknown-usage and drift sections show "Unavailable".

## Remaining effort

| | Hours |
|---|---|
| Optimistic | 0.25 |
| Likely | 0.75 |
| Pessimistic | 2 |

Confidence is medium. Everything named is green. What remains is your WR-U2-3 decision, the review, the merge, and reruns of `console-c3a-real` and `console-pg` at the 0024 merge, where the two known gaps flip.

## Verification log

- 2026-09-26: written at code head `2fad3510` (base `35bd43dd`); every command above run in this worktree.
