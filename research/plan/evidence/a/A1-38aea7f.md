# A1 — verified individual signup entitlement and idempotent backfill

| Field | Value |
|---|---|
| Task | A1 (shared entitlement logic, track D-owned), oracles CREDIT-GRANT, CREDIT-IDENTITY, CREDIT-UNITS; rulings R59, R64–R66, R71, R72 |
| Status | **implemented** (real PostgreSQL, both images). Not integrated, **not applied to any hosted project**, nothing pushed. Hosted auth config (email confirmation, abuse bounds) is **P-05 pending**. |
| Base SHA | `0cc4936` (claude/backend-impl, D1R merged) |
| Implementation SHA | `38aea7f` |
| Branch / worktree | `codex/a1-signup-grant` in `.claude/worktrees/codex-a1` |
| Classification | local only; the D harness's own labelled containers (`infrx-d1-postgres`, `infrx-d1-postgres-supabase`), created and removed by this checkout's runs |

## Commits

| Commit | Content |
|---|---|
| `5870378` | items 1–6 in one WIP commit (the migration is one file): `0015_signup_eligibility.sql`, `infrx/state/signup.py`, `tests/d/{checks_signup,signup_mutants,test_signup}.py`, one import line in `tests/d/migration_mutants.py` |
| `25d5887` | fix (item 4): claim race arbitrates on any identity-claim key; USD fixture sign |
| `4e2f3c4` | mutant list: the default-ACL mutant removed (it survived; see below) |
| `5c0bd7b` | shared-address race uses distinct `auth.users` strings (the Supabase image keeps emails unique) |
| `38aea7f` | fix (CREDIT-UNITS): the R72 USD test is its own USD-only boolean function |

Items did not land one commit each: the brief's six items are one migration file, written and then verified together. Each item's killing mutants are listed below.

## What was built

| Item | Where | What |
|---|---|---|
| 1 eligibility + binding | `public.claim_signup_grant(user, campaign, op)` | THE eligibility operation. Derives the verification evidence itself (`infrx.verified_user`, i.e. GoTrue's `email_confirmed_at`; a GoTrue soft-deleted row is unverified). A caller cannot hand evidence in. Answers `status` ∈ granted / replayed / unverified / identity_reused / rollout_hold / retired, plus the grant columns for the first two. An unknown id answers `unverified` like a known unverified one, with no denial row. |
| 1 binding | trigger `org_members_personal_binding` → `infrx.personal_org_binding_guard()` | Once a personal org funds a consumer wallet its membership is frozen: nobody joins it, and its owner is not removed, demoted or moved. The wallet's own owner/org binding was already immutable (0006). |
| 2 one grant | reuses D1R's `infrx.grant_signup_credit` | One transaction: wallet, entitlement, +10000.00000000. Replays are keyed by the user (R71): campaign, operation id, another org and a later loss of verification all return the same grant. `infrx.signup_identity_claims` adds one grant per **verified address** (sha256 of `lower(btrim(email))`), claimed in the grant's own transaction. |
| 2 R72 | `infrx.legacy_usd_rollout_hold(org)` | A nonzero legacy USD balance is a `rollout_hold`. It is a boolean, never an amount: no function reads both units, which D1R's unit scan checks. |
| 3 reuse | `infrx/state/signup.py` | `backfill(conn, campaign, page)`: keyset over `public.profiles`, one transaction per individual, refusals counted as `error:<SQLSTATE>`, maintenance (flag off) raises. `PgSignup(pool)` is the A1 port for G6B. Browser privileges: see item 3 tests. |
| 3 denials | `infrx.signup_denials` + `infrx.record_signup_denial` | Why a known individual was not granted (reason, attempts, first/last). Operator-readable only. |
| 5 retention | `infrx.retire_individual(user, actor, reason, idempotency_key)`, `infrx.retired_individuals`, triggers `credit_wallet_holds_frozen` and `credit_ledger_signup_frozen` | Anonymises the profile, revokes keys, suspends the personal org through the audited `infrx.set_suspension`, and freezes the wallet: no new hold and no signup grant. D5 compensating entries and debits of holds admitted before retirement still land. Idempotent. The ledger, entitlement and identity claim are kept. |
| 6 ports | `PgSignup.verified_user`, `PgSignup.grant_initial` | The `IdentityDirectory` and `Ledger.grant_initial` shapes in `infrx/operations/ports.py`, over a psycopg `AsyncConnectionPool`. A grant bound to another org than the identity's is Forbidden and rolled back. `adjust`/`reconcile` belong to D5 and are not here. |

Everything is service_role only. `claim_signup_grant` sits in `public` because PostgREST exposes `public` only (README: "never expose `infrx`"). It is not executable by anon or authenticated. The console calls it server-side after it reads the session user.

## Requirement coverage

`tests/d/test_signup.py` runs the checks in `tests/d/checks_signup.py`.

| Test | Oracle | Invariant |
|---|---|---|
| `test_eligibility__verified_once_denials_recorded_usd_untouched` | CREDIT-GRANT / IDENTITY / UNITS, R71, R72 | A verified individual gets exactly one `10000.00000000` grant, and its evidence equals `verified_user`'s. A retry with another campaign, another op id, a second organization or after verification is withdrawn answers `replayed` with the same op id; one ledger row. Unverified and soft-deleted users get nothing, and neither does an unknown id (no denial row). The unverified denial counts attempts (2). Verified-later is granted. The same address on another account in another case/whitespace is `identity_reused`. A shared personal org is a `rollout_hold`, and its identity claim is rolled back. Nonzero USD is a `rollout_hold`; a zero net USD balance (+5/−5) is granted. The USD ledger is identical before and after. Flag off → 55000. Null user → 22023. |
| `test_binding__frozen_personal_org_and_no_cross_user_spend` | CREDIT-IDENTITY | 4 membership changes to a bound personal org are refused (23514). Joining another org, and gaining then revoking a provider developer role, leave the grant `replayed`: 1 ledger row and 1 entitlement each, provider dev wallet total unchanged, wallet binding unchanged. A CREDIT job on B's wallet through W's org is refused; through B's own org it is accepted (control). W's session sees only W's wallet. |
| `test_privileges__browser_financial_writes_denied` | R59 (item 3) | anon, an ungranted self and a granted consumer are each refused 8 things (42501): claim, retire, the grant seam, record-denial, inserting a claim, reading denials, inserting a retirement, inserting a ledger row. service_role may call the claim and read denials, but may not insert claims or retirements, edit denials or call record-denial. EXECUTE on claim/retire is exactly service_role's. |
| `test_race__retries_backfill_and_shared_addresses` | CREDIT-GRANT (item 4) | 10 rounds × 8 concurrent callers (7 callback retries + 1 whole-DB backfill) per fresh individual. No caller errors. At most one retry sees `granted`, else the backfill issued. All callers get one op id; 1 ledger row. Then 3 rounds × 8 different accounts sharing one verified address (distinct strings) → exactly 1 `granted` and 7 `identity_reused`. |
| `test_retirement__wallet_owner_retired_never_deleted` | item 5 | Before retirement a hold is accepted (control). After it: the second call returns the same timestamp; the profile reads `retired+<uuid>@invalid` with name null; 0 unrevoked keys; the org is suspended with 1 `admin_set_suspension` audit row; the hold is refused with `23514 … frozen`; another individual's hold is still accepted; the claim answers `retired` (recorded); total, ledger rows and entitlement are unchanged. A retired, never-granted individual stays ungranted, and the grant seam into their wallet is refused (`frozen`). Hard delete → 23503. After a GoTrue soft delete, a new account with the old address is `identity_reused`. Retiring an unknown id → P0002. |
| `test_backfill__hosted_accounts_upgraded_from_0002` | CREDIT-GRANT / UNITS (item 3) | The I1B shape is written on the 0001–0002 schema: 4 users (3 confirmed), 4 single-owner orgs, 2 keys, 1 usage event of 0.00019660, 0 USD rows. It is upgraded through 0015, and the upgrade grants nothing (0 wallets, 0 entitlements). The first backfill (page 3) is exactly `{granted: 3, unverified: 1}`: each confirmed account has 1 entitlement and 1 ledger row, total `10000.00000000`. The unconfirmed one gets nothing and a denial is recorded. USD ledger, usage and keys are identical. The legacy USD statement for each org is `0.00000000` with no hold. The second run grants nothing and the ledger count is unchanged. |
| `test_port__the_g6b_grant_over_postgres` | item 6 | `PgSignup.verified_user` resolves the personal org and derived evidence; unverified and unknown → None. G6B's `OperatorSession.grant_initial` over the real ports grants once. Under a second idempotency key it replays with the same `ledger_operation_id` = `stable_id("signup_grant", key1)`. Unverified → NotFound. An identity naming another org → Forbidden, for a granted user and for a new one, and the new one is rolled back (no wallet, no entitlement). |
| `test_answer__*`, `test_identity_from__*`, `test_grant_initial__*`, `test_backfill__pages…`, `test_backfill__maintenance…` | code (R32) | Pure cases, the targets of the code mutants. Status → NotFound/Forbidden mapping, replay flag, org binding, one transaction with rollback on refusal, op id passed, per-user transactions, keyset progress, refusals counted, maintenance raised. |
| D1R's suite, unchanged, with 0015 applied | regression | 0001–0005 inventory unchanged, upgrade from 0005 imports nothing, unit scan, re-run no-op, function EXECUTE surface, role matrices. |

## Environment

Linux 7.0.0-1010-aws x86_64; Python 3.12.3 (`make api-env`, psycopg 3.3.6, psycopg_pool 3.3.2); Docker server 29.6.2. Images:

- `postgres@sha256:33f923b0…` (16.14 + shim)
- `supabase/postgres@sha256:7768d0d1…` (17.6.1.173, no shim, `INFRX_D1_IMAGE=supabase`)

The bare Supabase image's `auth.users` lacks GoTrue's `email_confirmed_at`/`deleted_at`. `checks_signup.gotrue_columns` adds them as GoTrue has them, as `supabase_admin` in this checkout's own container. A missing column reads as unverified or not deleted through `to_jsonb`.

## Commands and results

Commands run in `apps/infrx-api`. Times are UTC, 2026-09-22.

| Command | Exit | Result |
|---|---|---|
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d` (plain), at `38aea7f` | 0 | `286 passed in 454.06s` (23:04–23:11Z) |
| `INFRX_D1_IMAGE=supabase INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d` | 0 | `286 passed in 441.61s` (23:11–23:19Z) |
| track-first, `tests/d` excluded: `pytest -q tests/g tests/i tests/j tests/m tests/q tests/t tests/w tests/contracts tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py` | 0 | `2353 passed, 2 warnings in 608.58s` (23:19–23:29Z) |
| legacy-first, `tests/d` excluded: `pytest -q tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/contracts tests/g tests/i tests/j tests/m tests/q tests/t tests/w` | 0 | `2353 passed, 2 warnings in 689.04s` (23:29–23:41Z) |
| `INFRX_MUTANTS=all … pytest -q -s tests/d/test_signup.py -k code_mutant` | 0 | `11 passed` (every code mutant `killed`) |
| `INFRX_MUTANTS=all … pytest -q -s tests/d/test_migration_mutants.py -k 'a1_ or well_formed'` (plain, before `4e2f3c4`) | 1 | 20 killed, 1 survived (`a1_claim_keeps_default_acl`, removed; see below) |
| same on `INFRX_D1_IMAGE=supabase` (after `4e2f3c4`) | 0 | `21 passed` (20 killed + list well-formed; the D list is 213 mutants over 45 checks) |
| `…/.venv/bin/python -m pytest -q tests/integration/test_harness.py -k migration_set` (repo root) | 1 | fails as expected: the list names 0001–0009 exactly (integration request 1) |

Mutant counts: `migration_mutants.MUTANTS` 213 (193 D + 20 A1), `signup_mutants.MUTANTS` 11. The two full `tests/d` runs above include all 20 A1 migration mutants and all 11 code mutants.

**Not run:** `make check` as a whole. No console/TypeScript is touched. `make api-mutants`'s other-track lists were not run; only the D list and the A1 lists ran. `make integration`: its layer uses E2's containers, which are not this task's. The one known break is integration request 1.

## Migration mutants (R32/R40), 20, each killed by its named check on both images

| Check | Mutants |
|---|---|
| `signup_eligibility` | `a1_unverified_mints`, `a1_soft_deleted_is_verified`, `a1_identity_digest_is_case_sensitive`, `a1_identity_reuse_allowed`, `a1_usd_balance_not_a_hold`, `a1_denial_not_recorded`, `a1_denial_enumerates_unknown`, `a1_replay_reverifies` |
| `signup_binding` | `a1_membership_not_frozen`, `a1_owner_removable` |
| `signup_privileges` | `a1_claim_callable_by_browsers`, `a1_service_writes_claims` |
| `signup_race` | `a1_claim_race_raises` |
| `signup_retirement` | `a1_retired_wallet_spends`, `a1_retired_keeps_keys`, `a1_retired_org_not_suspended`, `a1_retired_profile_kept`, `a1_retirement_not_idempotent`, `a1_retired_regranted` |
| `signup_backfill` | `a1_backfill_grants_unverified` |

A claim or retirement that raises where the operation promises an answer is converted into an assertion by `checks_signup.claim`/`retire`. The invariant is "answers with a status", so this is declared, not a crash scored as a kill.

`a1_claim_keeps_default_acl` (dropping 0015's `revoke all on function public.claim_signup_grant`) **survived**, measured. D1's default privileges already keep later public functions from browser roles, so that revoke is a restatement. It was removed from the list with a note. The browser-EXECUTE invariant is killed by `a1_claim_callable_by_browsers`.

## Code mutants (shared runner, R83), 11, all killed

| Mutant | Case |
|---|---|
| `unverified_is_forbidden`, `denial_is_not_found` | `test_answer__denials_are_not_found_or_forbidden` |
| `binding_unchecked` | `test_answer__a_grant_bound_elsewhere_is_forbidden` |
| `replay_flag_lost` | `test_answer__granted_and_replayed` |
| `unverified_identity` | `test_identity_from__unverified_and_orgless_are_none` |
| `grant_outside_a_transaction`, `operation_id_dropped` | `test_grant_initial__one_transaction_rolled_back_on_refusal` |
| `backfill_one_transaction`, `backfill_stops_on_refusal` (declared `dies_by=FakeRefusal`: the invariant is "does not raise"), `backfill_pages_overlap` | `test_backfill__pages_per_user_transactions_and_errors` |
| `backfill_swallows_maintenance` | `test_backfill__maintenance_stops_the_run` |

Default run: 3 of the 11 (`SUBSET`); `INFRX_MUTANTS=all`: all 11.

## Failure drills (found by the suite, fixed)

1. **Claim race** (the 10×8 race). The first run failed round 0: `23505 duplicate key value violates unique constraint "signup_identity_claims_user_id_key"`. The insert's single arbiter was the digest, and a racing retry for the same individual hit the per-user key first. The fix, `25d5887`, removes the conflict target, the same shape as D1R's wallet fix. Afterwards all 10 rounds pass with no caller errors, and per individual there is 1 wallet, 1 ledger row, 1 entitlement and 1 identity claim. Mutant `a1_claim_race_raises` restores the bug and is killed.
2. **Unit scan**. D1R's `check_no_unit_conversion` flagged `public.claim_signup_grant: reads USD and CREDIT amounts together`, because the R72 test summed `delta_usd` inside the grant function. `38aea7f` moves it into `infrx.legacy_usd_rollout_hold(org) → boolean`.
3. **Supabase `auth.users` unique email**: the shared-address race now uses distinct strings that normalise to one address (`5c0bd7b`). This is what GoTrue permits, so the case is also the realistic one.
4. **Flag off**: claim → 55000, state unchanged. `backfill` raises on maintenance instead of counting it.

## Artifacts

Sweep logs in the session scratchpad (`sweep.log`, `d_plain.out`, `d_supabase.out`, `rest_*.out`). Not durable storage; the counts above are quoted from them.

## Changes (owned paths)

- `apps/app/supabase/migrations/0015_signup_eligibility.sql` (new; 0010–0014 left to D2)
- `apps/infrx-api/infrx/state/signup.py` (new)
- `apps/infrx-api/tests/d/{checks_signup,signup_mutants,test_signup}.py` (new)
- `apps/infrx-api/tests/d/migration_mutants.py`: exactly one appended line: `from . import signup_mutants  # noqa: E402,F401  A1 (0015): appends its mutants and checks`
- this report

One behaviour change touches a 0001 table. `public.org_members` gains the trigger `org_members_personal_binding`, which refuses writes that touch an org funding a consumer wallet. Browser roles could not write memberships before, and the D1R inventory check (existing keys only) still passes.

### Migration / rollback (0015)

0015 is additive and re-runnable: D1R's `test_rerun__…` re-applies it and passes on both images. It creates no rows, and the upgrade from 0002 grants nothing.

- **Before any claim or retirement row exists:** drop the triggers `org_members_personal_binding`, `credit_wallet_holds_frozen` and `credit_ledger_signup_frozen`. Drop the functions `public.claim_signup_grant`, `infrx.retire_individual`, `infrx.record_signup_denial`, `infrx.legacy_usd_rollout_hold`, `infrx.personal_org_binding_guard` and `infrx.retired_wallet_guard`. Then drop the tables `infrx.signup_denials`, `infrx.signup_identity_claims` and `infrx.retired_individuals`.
- **After rows exist:** roll back by setting the flag `signup_grant` off (the claim answers 55000). Never drop `retired_individuals`, because that would unfreeze retired wallets. Never drop `signup_identity_claims`, because that would re-open eligibility for re-created accounts. Grants themselves are 0006's and follow D1R's rollback.

## Ruling proposal: identity retention and deletion (for 08 §10)

> **R-A1 (proposed). A wallet owner is retired, never deleted.** An individual who owns a CREDIT wallet or a signup entitlement is never hard-deleted: the foreign keys already refuse it (`organizations.created_by` since 0001; wallets and entitlements `on delete restrict` since 0006).
>
> The deletion request is one operator action: GoTrue's soft delete (`auth.admin.deleteUser(id, true)`: row kept, address obfuscated), then `infrx.retire_individual(user, actor, reason, idempotency_key)`. That call:
>
> - anonymises the profile to `retired+<uuid>@invalid`, with name and avatar null;
> - revokes every key whose individual is the user (`coalesce(user_id, created_by)`);
> - renames every organization the user created and alone owns (not only the personal one) to `retired` and suspends it (audited `admin_set_suspension`, code `operator_request`) *(restated in review round 3, RV2-2)*;
> - freezes the wallet: no new CREDIT hold and no signup grant. In-flight settlement and D5 compensating entries still land.
>
> An organization the user created but shares with another member is neither renamed nor suspended, and neither is one they solely own but did not create *(RV2-2, round 3)*. *(Added in review round 2, RM-1/RM-3.)* The signup-time R72 scope is every organization the individual created (`organizations.created_by`). An organization with a NULL `created_by` is outside that scope, and no product path creates one: 0001's `handle_new_user` is the only insert and always sets it.
>
> *(RV3-3, round 4.)* Race limit: the "shared ... left as is" rule reads the memberships as the retirement's loop statement sees them. A member joining the individual's solo created org concurrently with the retirement can end in a 2-member org that is renamed `retired` and suspended (join first: the retirement waits for it, then suspends; retire first: the join waits, then lands in the suspended org). Retirement is operator-initiated and there is no money effect (a side org has no wallet); the operator lifts the suspension with `infrx.set_suspension(org, false, …)`.
>
> *(RV3-2, round 4.)* The claim locks every organization the individual created (FOR NO KEY UPDATE) before binding. A concurrent transaction that writes memberships in two or more of those organizations in the opposite order can make the claim raise `40P01`; the caller retries the same idempotent call, and nothing is minted by the failed attempt.
>
> The ledger, entitlement and identity claim are retained as money history. Eligibility is one grant per individual UUID **and** per verified address: sha256 of the lower-cased, trimmed email, stored as a digest only. The digest is retained after retirement, so delete + re-create with the same address is `identity_reused`, never a second grant.
>
> The digest is an unsalted, unkeyed sha256 of an email address, which a dictionary of addresses can reverse. It is therefore **pseudonymous personal data**, kept for abuse control (one grant per human), and its retention must be bounded by the legally approved period (**P-05**). Erasing it re-opens eligibility for that address, so erasure requires deleting the `infrx.signup_identity_claims` row. That is an UPDATE/DELETE the immutability trigger refuses today, so it has to be a new, audited D operation plus a ruling.
>
> Open for the coordinator/legal (**P-05** abuse bounds; **P-09**-adjacent retention commitments): whether the digest's retention is bounded, and whether provider-specific address folding (dots, `+tags`) is required. Neither is implemented; the code comment marks the exact-match ceiling.

## A2 fixture: the console onboarding action

The console calls this server-side only (route handler or server action), after `supabase.auth.getUser()`, with the service-role client from `lib/supabase/admin.ts`. It never calls it from the browser, and it passes no wallet id and no evidence:

```ts
const { data, error } = await createAdminClient()
  .rpc("claim_signup_grant", { p_user_id: user.id, p_campaign_version: "launch_2026_09" });
// data: an array holding exactly one row - read data[0]
```

| `status` | Row | Console meaning |
|---|---|---|
| `granted` | `{status, user_id, wallet_id, ledger_operation_id, amount: "10000.00000000", granted_at}` | credited now (then read `console_wallet_summary`) |
| `replayed` | same columns, the original grant | already credited (callback retry / first login / backfill) |
| `unverified` | grant columns null | "verify your email" |
| `identity_reused` / `rollout_hold` / `retired` | grant columns null | one neutral "not eligible / under review" message; do not distinguish them to the user |
| error `55000` | — | signup grant not enabled yet (flag): "credits pending", retry later |
| error `22023` | — | programming error (no user) |
| error `40P01` | — | deadlock with a concurrent membership change in the individual's organizations: retry the same call (idempotent; nothing was minted) *(RV3-2, round 4)* |

Example `granted` row: `{"status":"granted","user_id":"<uuid>","wallet_id":"<uuid>","ledger_operation_id":"<uuid>","amount":"10000.00000000","granted_at":"2026-09-22T23:04:31.123456+00:00"}`. Amounts are text (R59-9). Persist-before-display holds, because the RPC returns after commit.

Migration compatibility: 0015 requires 0006 (grant seam, flags), 0009 (`verified_user`, `set_suspension`) and D2's 0010–0014 in number order only (no dependency). The deployed console's reads are unchanged, and the D1R legacy read path passes.

## Limits

1. Not applied to any hosted project. The hosted auth has `disable_signup` true and `mailer_autoconfirm` false (I1B). Public onboarding config is **P-05 pending**.
2. The code mutants target pure cases with fakes, because the shared runner's nested pytest cannot hold the D harness's port lock while the parent suite does. The PostgreSQL paths of `PgSignup`/`backfill` are proven by the DB cases, not by code mutants.
3. *(Corrected in review round 2, RM-2.)* A claim racing a retirement of the same individual waits for it: the claim takes a KEY SHARE lock on the profile row that `retire_individual` locks FOR UPDATE, so its retired check runs after the retirement commits, and it answers `retired` with nothing minted. At `47b0382` the same race made the claim raise `23514 … frozen` instead (the closure review's probe P4a); nothing was minted then either.
4. The identity digest is an exact normalised-address match: no dot/`+tag` folding (P-05).
5. `WalletDirectory` over PostgreSQL is not built here (D2/D5). The G6B test uses the contracts' `FakeWalletDirectory`.
6. The R72 rollout-hold function sums the org's USD ledger on each claim (indexed by `credit_ledger_org_created_idx` on org). Hosted has 0 rows.

## Handback

Next unblocked: **A2** (onboarding action above), **G6B** composition root, **D5** (`adjust`/`reconcile` next to `PgSignup.grant_initial`).

### integration_requests

1. **Coordinator / E2R** (`tests/integration/test_harness.py::test_the_migration_set_is_the_console_one_and_is_read_in_filename_order`): append `"0015_signup_eligibility.sql"` after D2's 0010–0014 in the expected list. It fails today by design.
2. **Coordinator** (`tests/d/test_migration_mutants.py` `ALWAYS`): add `a1_unverified_mints`, `a1_identity_reuse_allowed`, `a1_membership_not_frozen`, `a1_claim_callable_by_browsers`, `a1_claim_race_raises` and `a1_retired_wallet_spends` to the default subset. Today A1's migration mutants run only with `INFRX_MUTANTS=all`.
3. **Coordinator** (`Makefile` `api-mutants`): add `tests/d/test_signup.py`, which runs all 11 code mutants under `INFRX_MUTANTS=all` (the DB cases ride along in the same process as the D list).
4. **D** (`tests/d/checks.py` `INFRX_CALLABLE`): add `infrx.retire_individual(uuid,text,text,text)`. In `infrx/state/credit_schema.py` `SEAMS`, add `"public.claim_signup_grant(uuid,text,uuid)": (frozenset({"service_role"}), (("status","text"),("user_id","uuid"),("wallet_id","uuid"),("ledger_operation_id","uuid"),("amount","text"),("granted_at","timestamp with time zone")))`.
5. **G6B** (`infrx/operations/cli.py` `build_operations`): `pool = AsyncConnectionPool(<service-role DSN>)`, then `signup = PgSignup(pool)`; `identities=signup`; `ledger` = an object with `grant_initial = signup.grant_initial` and D5's `adjust`/`reconcile`. Add CLI commands `backfill-signup-grants` (sync connection → `infrx.state.signup.backfill(conn, campaign)`, printing the per-status counts) and `retire-user` (`select infrx.retire_individual(user, principal, reason, idempotency_key)`).
6. **A2**: use the fixture above. The flag `signup_grant` is enabled only by an operator (`infrx.feature_flags`); until then the RPC answers 55000.
7. **D5**: a frozen (retired) wallet still accepts `operator_adjustment` and debits of pre-retirement holds; settlement needs nothing new.
8. **D2**: a hold refused with `23514 … frozen` means the account was retired. *(Corrected in the review round, SEC-6.)* Refusing a suspended org before admission is **D2's** ordering and does not exist at this base. The legacy gateway (`infrx/auth/keys.py`) reads only `id, org_id, revoked_at`: it never reads suspension, and it honours a revocation only after `key_ttl` (60 s default), or later while Supabase is unreachable and a cached row is served. *(Added in review round 2, SEC-R3.)* Revocation-lag budget for consumer keys: unstated in the brief; input needed (D2/P-05); legacy gateway = key_ttl 60 s + outage length.
9. **Coordinator / 08 §10**: record R-A1 (above) as **R85** *(number assigned at the round-2 dispatch)* or rule otherwise. Also record that the migration numbers 0010–0014 belong to D2 and 0015 to A1.
10. **Docs** (`apps/app/supabase/README.md`, not owned): add a 0015 paragraph.

Nothing was applied to any hosted project.

## Review round (coordinator fix_required at `abbf066`), head `c4d7fa3`

One commit per item. The implementation head is `c4d7fa3`; the evidence commit follows it.

| Item | Commit | Change | Killing test / mutant |
|---|---|---|---|
| M-1 | `ab45f6f` | Adds an individual whose personal org holds a single negative legacy row (usage −0.000001); the claim answers `rollout_hold` and no wallet is created. | `test_eligibility__…`; mutant `a1_usd_hold_only_on_credit_balances` (`<> 0` → `> 0`) |
| M-2 | `787b4e4` | Seeds and commits a hold before retirement. After `retire(t)`, its settlement lands (hold → `settled` plus `inference_debit` −9.976), and so does an `operator_adjustment` of +5 (both rolled back by `attempt`). | `test_retirement__…`; mutants `a1_frozen_holds_refuse_settlement` (`before insert or update`) and `a1_frozen_ledger_refuses_every_kind` (drops `when (new.kind = 'signup_grant')`) |
| M-3/H4 | `8d3b4bc` | `PgSignup.grant_initial` answers `granted`/`replayed` inside the transaction, so a foreign binding still rolls back. A denial is answered after the commit, so its `signup_denials` row persists on the G6B path. | `test_grant_initial__a_denial_commits_its_recorded_reason` (pure; code mutant `denial_answered_inside_the_transaction`); DB: `test_port__…` asserts that the unverified y's denial count is 1 after a port NotFound |
| M-4 | `c09afaa` | **Decision: R72 scope = every organization the individual created** (`organizations.created_by`), not only the org the wallet would bind. Fails closed; recorded in the migration comment. | Case: USD +1 in a second org the individual created → `rollout_hold`; mutant `a1_usd_hold_personal_org_only` |
| M-5 | `41f611f` | The backfill keyset starts at `UUID(int=0)` and raises `RuntimeError` if a page does not advance. The fake parses the `>`/`>=` comparison out of `signup.PAGE` itself and caps page calls. | `test_backfill__pages…` (pages 1, 2, 500) and `test_backfill__a_keyset_that_does_not_advance_stops_loudly`; code mutants `backfill_page_inclusive` (declared `dies_by=RuntimeError`: the guard is the detection) and `backfill_unguarded_keyset` |
| M-6 | `587bd5b` | A `campaign_version` over 100 characters is answered with 22023 before any write; exactly 100 is granted. | Case in `test_eligibility__…`; mutant `a1_campaign_length_unchecked` |
| SEC-3 | `351a249` | `retire_individual` renames each personal org it suspends to `retired`. | `test_retirement__…`; mutant `a1_retired_org_keeps_the_name` |
| SEC-4 | this commit | Adds the R-A1 wording above: pseudonymous personal data, retention bounded by the legal period (P-05), and what erasure requires. | — |
| SEC-5 | `c4d7fa3` | The backfill re-raises 42501, because a role that cannot grant stops the run. | `test_backfill__a_role_without_execute_stops_the_run`; code mutant `backfill_counts_a_missing_privilege` |
| SEC-6 | this commit | Corrects integration request 8: suspension-first ordering is D2's, and the legacy gateway honours a revocation only after `key_ttl`. | — |
| H3 | this commit | The drill below. | — |
| H5 | this commit | The A2 fixture now reads "an array holding exactly one row (`data[0]`)". | — |

### Results at `c4d7fa3`

UTC, 2026-09-23. The host was under load (load average about 41), so these runs are about 2.5 times slower than the first sweep.

| Command (in `apps/infrx-api`) | Exit | Tail |
|---|---|---|
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d` (plain) | 0 | `299 passed in 1134.09s (0:18:54)` (00:53–01:12Z) |
| `INFRX_D1_IMAGE=supabase INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d` | 0 | `299 passed in 1181.55s (0:19:41)` (01:24–01:43Z) |
| `INFRX_MUTANTS=all … pytest -q -s tests/d/test_signup.py -k code_mutant` | 0 | `15 passed, 17 deselected in 83.68s`: all 15 code mutants `killed` |
| quick A1 slice (plain): `pytest tests/d/test_signup.py tests/d/test_migration_mutants.py -k 'not code_mutant and (a1_ or …)'` | 0 | `43 passed, 210 deselected in 90.37s` |

Mutant lists:

- **D list:** 219 = 193 D + 26 A1. The 6 A1 mutants added since `abbf066` are `a1_usd_hold_only_on_credit_balances`, `a1_usd_hold_personal_org_only`, `a1_campaign_length_unchecked`, `a1_frozen_holds_refuse_settlement`, `a1_frozen_ledger_refuses_every_kind` and `a1_retired_org_keeps_the_name`. All are killed on both images inside the two full runs above.
- **Code list:** 15, adding `denial_answered_inside_the_transaction`, `backfill_counts_a_missing_privilege`, `backfill_page_inclusive` and `backfill_unguarded_keyset`.

The earlier HarnessBusy refusals (the port was held by `codex-d3`) were retried. The harness containers this run created were removed.

### H3: the removed mutant `a1_claim_keeps_default_acl`, as a drill

Script: `a1r/h3_drill.py` in the session scratchpad. It builds the pristine and the mutated migration sets on each image, then runs the D runner's `kill()`:

```
plain pristine: ('{postgres=X/postgres,service_role=X/postgres}', False, False)
plain mutated:  ('{postgres=X/postgres,service_role=X/postgres}', False, False)
plain kill():   ('survived', '')
supabase pristine: ('{postgres=X/postgres,service_role=X/postgres}', False, False)
supabase mutated:  ('{postgres=X/postgres,service_role=X/postgres}', False, False)
supabase kill():   ('survived', '')
```

Columns: `proacl` of `public.claim_signup_grant(uuid,text,uuid)`, then whether anon and authenticated may execute it. The ACL is byte-identical with and without 0015's revoke on both images, so the mutant is an equivalent mutant: D1's default privileges already produce this ACL, and its survival is not a missing test. The browser-EXECUTE invariant stays killed by `a1_claim_callable_by_browsers`.

## Review round 2 (closure review fix_required at `47b0382`), head `8a26524`

The closure review (`research/plan/evidence/a/A1-confirm-47b0382.json` on `claude/backend-impl`) confirmed 1 blocking item and 8 nonblocking ones. There is one commit per item. The last code commit is `8a26524`, a message-only change to the two race checks so a kill shows the SQLSTATE; the evidence commit follows it.

| Item | Commit | Change | Killing test / mutant (kill detail from `kill()`, plain; the same on supabase) |
|---|---|---|---|
| **RM-1** (blocking) | `e77f917` | Before `retire(t)`, `check_retirement` creates the org `team` (`created_by` t, t owner, o member). Afterwards it asserts `(name, suspended) == ('team', false)`. | `test_retirement__…`. The mutant `a1_retirement_suspends_shared_orgs` drops the two `not exists` lines and is killed: `a shared organization the individual created was retired with them` |
| RM-2 | `8539a77` | `claim_signup_grant` takes `for key share` on the profile row before the retired check, so it waits for `retire_individual`'s FOR UPDATE. The new check `check_retirement_race` shows this: A retires x in a transaction it holds open, and B claims x. A commits only once a third connection sees B's `wait_event_type = 'Lock'` in `pg_stat_activity`. B then answers `retired`: no wallet, no ledger row, no entitlement. Limits item 3 is corrected in place. | `test_retirement_race__…`. The mutant `a1_claim_races_retirement` drops the lock line and is killed: `claim for a100000b-…-000000000001 raised instead of answering: 23514 CREDIT wallet …` |
| RM-3 | `a35cf4c` | Option (b), documented rather than widened. The migration comment says `created_by` is the boundary, that an org with a NULL `created_by` is outside the scope, and that no product path creates one (0001's `handle_new_user` is the only insert). R-A1 carries the same line. | — (the review's control `rv_hold_ignores_created_by` is an equivalent mutant) |
| RM-4 | `0c1ab9b` | The 100-character campaign case is now `'é' × 100` (200 bytes) and is still granted. | `test_eligibility__…`. The mutant `a1_campaign_counts_octets` (`length(` → `octet_length(`) is killed: `raised instead of answering: 22023 invalid_request: campaign_version is at most 100 characters` |
| RM-5 | `b454757` | New individual `pm`: +3.000000 USD on the personal org and −3.000000 on a second org they created. Result: `rollout_hold`, no wallet. | `test_eligibility__…`. The mutant `a1_usd_hold_summed_across_orgs` (the review's `rv_hold_summed_across_orgs`) is killed: `+3 and -3 USD in two organizations are two nonzero balances (R72): ('granted', …` |
| SEC-R1 | `e1e00f4` | The first case of `check_retirement_race`: A retires y and holds its transaction open, then B retires y. B waits, then returns A's `retired_at`. The brief asked for a Barrier; this held transaction replaces it. A Barrier only makes the interleaving likely, while this makes B always run into A's uncommitted retirement. The race case runs first, so each race mutant is killed by its own case: without the FOR UPDATE, the claim case fails too. | `test_retirement_race__…`. The mutant `a1_retirement_not_serialised` (`for update;` → `;`) is killed: `retire a100000b-…-000000000002 raised: 23505 duplicate key value violates unique constraint "ret…` |
| SEC-R2 | `7012e59` | 0015 line 1 now reads: `-- 0015 · A1 · 2026-09-22; amended 2026-09-23 in the A1 review rounds (M-4, M-6, SEC-3, RM-*); applied to no hosted or shared environment (R84).` | — |
| SEC-R3 | this commit | Integration request 8 gains the revocation-lag line (unstated budget, input needed from D2/P-05; the legacy gateway lags by `key_ttl` 60 s plus the outage length). | — |
| SEC-R4 | `ef58367` | The `backfill` docstring now states the partial-page outcome. When a run stops, the claims made before the stop stay committed, one transaction each. The refused individual wrote nothing and the later ones are untouched, so a rerun resumes and answers the earlier ones as replays. | — (the review's probe P4b measured it) |

Version of 0015 tested (R84): `git rev-parse 8a26524:apps/app/supabase/migrations/0015_signup_eligibility.sql` → `178698bef10421fd98ec06b8b302ac87e837207f`. The file is unchanged since `7012e59`.

### Results

UTC, 2026-09-23, in `apps/infrx-api`. Logs are in `/tmp/claude-1000/a1-round2/`. Harness: the shared port 55432, whose lock was free at start (the recorded holder pid was dead); no HarnessBusy retries were needed (`steps.log`).

| Command | At | Exit | Tail |
|---|---|---|---|
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d/test_signup.py` (plain) | `ef58367` | 0 | `33 passed in 46.24s` |
| same, `INFRX_D1_IMAGE=supabase` | `ef58367` | 0 | `33 passed in 55.59s` |
| `INFRX_MUTANTS=all … tests/d/test_migration_mutants.py -k 'a1_ or well_formed'` (plain) | `ef58367` | 0 | `32 passed, 194 deselected in 46.29s` |
| same, `INFRX_D1_IMAGE=supabase` | `ef58367` | 0 | `32 passed, 194 deselected in 49.50s` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d` (plain, full D suite with every mutant) | `ef58367` | 0 | `305 passed in 501.08s (0:08:21)` (02:55–03:04Z) |
| `INFRX_MUTANTS=all … tests/d/test_signup.py` (plain / supabase) | `8a26524` | 0 / 0 | `33 passed in 68.31s (0:01:08)` / `33 passed in 53.41s` |
| `INFRX_MUTANTS=all … tests/d/test_migration_mutants.py -k 'a1_ or well_formed'` (plain / supabase) | `8a26524` | 0 / 0 | `32 passed, 194 deselected in 58.47s` / `32 passed, 194 deselected in 65.26s (0:01:05)` |
| `kill()` on the 5 new migration mutants (`kill_new_{plain,supabase}.log`, at `ef58367`), and the 2 race mutants again at `8a26524` (`kill_race_*.log`) | | 0 | all `killed`, details in the table above |

The full `tests/d` sweep was not rerun on Supabase this round. The brief asked for plain only; the A1 slice ran on both images.

Mutant lists, by import at `ef58367`: `D total 224 A1 migration 31 A1 code 15 checks 46`. The D list is 224 = 193 D + 31 A1. The 5 A1 mutants new this round are `a1_retirement_suspends_shared_orgs`, `a1_claim_races_retirement`, `a1_campaign_counts_octets`, `a1_usd_hold_summed_across_orgs` and `a1_retirement_not_serialised`. The one new check is `signup_retirement_race`. `test_signup.py` has 18 cases (one new: `test_retirement_race__a_racing_claim_or_retirement_waits_and_answers`) plus 15 code mutants; the code list is unchanged.

Cleanup: the harness removed its containers at exit (`docker ps -a | grep -c infrx-d1-postgres` → `0`). No other lane's container, lock or run was touched.

### Limits (round 2)

- Item 3 is corrected in place (RM-2). The claim now serialises on the profile row. The fix depends on `retire_individual`'s FOR UPDATE: a claim's KEY SHARE does not conflict with the retirement's later non-key UPDATE of the profile alone. `a1_retirement_not_serialised` shows this, because without the FOR UPDATE the claim case fails as well.
- RM-3 was documented, not widened. An owner of an org with a NULL `created_by`, or of an org another individual created, is not held at signup for that org's USD. That org's own statement still answers `rollout_hold`. Widening the predicate to ownership is a one-line change plus one case, if the coordinator prefers it.
- The race checks observe B's wait through `pg_stat_activity` from a third connection. If B never waits and never finishes within 60 s, the check fails with `B neither waited nor finished`.
- The earlier limits 1, 2, 4, 5 and 6 are unchanged.

### integration_requests (current)

Requests 1–10 above still stand. Changes in this round:

- **1 (merge):** `"0015_signup_eligibility.sql"` must be appended to the expected list in `tests/integration/test_harness.py` after D2's 0010–0014 at merge.
- **2 (optional):** add `a1_retirement_suspends_shared_orgs` and `a1_claim_races_retirement` to the default `ALWAYS` subset.
- **3:** `tests/d/test_signup.py` runs 18 cases plus the 15 code mutants under `INFRX_MUTANTS=all`.
- **8:** now carries the SEC-R3 revocation-lag line (input needed from D2/P-05).
- **9:** R-A1 is to be recorded as **R85**.

## Review round 3 (re-confirmation fix_required at `d8a3e84`), head `d653e7c`

The re-confirmation (`research/plan/evidence/a/A1-confirm-d8a3e84.json` on `claude/backend-impl`) closed RM-1…RM-5 and SEC-R1…SEC-R4. It raised one new blocking item, RV2-1, and one nonblocking item, RV2-2. Its nonblocking list has no other entries. There is one commit per item; the last code commit is `d653e7c` (the header note).

| Item | Commit | Change | Killing test / mutant (plain and supabase give the same detail) |
|---|---|---|---|
| **RV2-1** (blocking) | `5205780` | The reviewer's verified two-line fix. `personal_org_binding_guard` takes `for share` on the organization row(s) before its wallet check. `claim_signup_grant` takes `for no key update` on the organizations the individual created, immediately before `grant_signup_credit`. `check_binding` gains a two-order race through `_behind`. **Claim first:** A holds the claim open and B inserts a member into the org → B waits, then gets `23514` frozen, and the org keeps 1 member. **Member first:** A holds the member insert open and B claims → B waits, then answers `rollout_hold` with no wallet. | `test_binding__…`. Mutant `a1_binding_guard_unlocked` (drops the guard's `for share`) is killed: `a member joined a personal org while the claim bound it: {…, 'got': None}`. Mutant `a1_claim_binding_unlocked` (drops the claim's `for no key update`) is killed with the same message. Each lock is needed in both orders, so whichever order runs first kills both mutants; the pristine check asserts both orders. |
| RV2-2 | `277116c` | The migration comment (at the loop and in the header bullet) and R-A1 now say "every organization the user created and alone owns (not only the personal one); one shared with another member, or solely owned but created by someone else, is left as is". `check_retirement` adds two orgs before `retire(t)`. `solo`, created by t and owned by t alone, ends as `('retired', True)`. `kept`, created by o and owned by t alone, ends as `('kept', False)`. | `test_retirement__…`. Mutant `a1_retirement_ignores_created_by` (`where o.created_by = p_user` → `where true`, the reviewer's `rv2_retire_loop_ignores_created_by`) is killed: `an organization the individual owns but did not create was retired` |
| header | `d653e7c` | 0015 line 1 now ends `(M-4, M-6, SEC-3, RM-*, RV2-1, RV2-2); applied to no hosted or shared environment (R84).` | — |

Version of 0015 tested (R84): `git rev-parse d653e7c:apps/app/supabase/migrations/0015_signup_eligibility.sql` → `195b62117651cccec3d5f768356dee02c64bc337`.

### Results

UTC, 2026-09-23, in `apps/infrx-api`, at `d653e7c`. Logs are in `/tmp/claude-1000/a1-round3/`. Before the first step could start, the shared port 55432 was held by another run (a full `tests` sweep from the coordinator's scratchpad). The runner retried on HarnessBusy three times, a minute apart, and touched nothing else (`steps.log`: `busy kill_new_plain; retry in 60s` ×3, then started at 03:30:44Z).

| Command | Exit | Tail |
|---|---|---|
| `kill()` on the 3 new mutants (plain / `INFRX_D1_IMAGE=supabase`) | 0 / 0 | all 3 `killed` on both images, with the details in the table above |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d/test_signup.py` (plain) | 0 | `33 passed in 44.98s` |
| same, `INFRX_D1_IMAGE=supabase` | 0 | `33 passed in 52.79s` |
| `INFRX_MUTANTS=all … tests/d/test_migration_mutants.py -k 'a1_ or well_formed'` (plain) | 0 | `35 passed, 194 deselected in 48.00s` |
| same, `INFRX_D1_IMAGE=supabase` | 0 | `35 passed, 194 deselected in 51.51s` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d` (plain, full D suite with every mutant) | 0 | `308 passed in 431.32s (0:07:11)` (03:34–03:41Z) |

Mutant lists, by import: `D total 227 A1 migration 34 A1 code 15 checks 46`. The D list is 227 = 193 D + 34 A1. New this round: `a1_binding_guard_unlocked`, `a1_claim_binding_unlocked` and `a1_retirement_ignores_created_by`. There is no new check, and the test count is unchanged, because the race rides in `check_binding` and the scope cases in `check_retirement`. The full `tests/d` sweep was not rerun on Supabase; the brief asked for plain only, and the A1 slice ran on both images. Cleanup: `docker ps -a | grep -c infrx-d1-postgres` → `0`.

### Limits (round 3)

- The claim now takes, in order, the profile KEY SHARE (RM-2) and then FOR NO KEY UPDATE on every organization the individual created (RV2-1). `retire_individual` takes the profile FOR UPDATE and then the org FOR UPDATE (through `set_suspension`). Both take the profile first, so a claim and a retirement cannot deadlock.
- *(Corrected in review round 4, RV3-2.)* Two claims for the same individual cannot deadlock: the second waits on the `signup_identity_claims` unique index before it reaches any org lock (re-confirmation P4: 20/20 `(granted, replayed)`, no 40P01, both images). The real 40P01 surface is a claim racing a transaction that writes memberships in two or more of the claimant's created orgs in the opposite order: the claim then raises `40P01` (retryable; nothing minted, no wallet or ledger row; P4b 5/5). `order by o.id` does not remove it, because the writer's order is its own. Only platform-role membership writers reach it; the backfill counts `error:40P01` and continues.
- The guard's FOR SHARE is taken on every `org_members` write, including 0001's `handle_new_user` owner insert. That lock waits only on a claim holding the same org.
- The earlier limits are unchanged.

### integration_requests

Unchanged from round 2 (requests 1–10 with the round-2 amendments). Adding `a1_binding_guard_unlocked` to the default `ALWAYS` subset is optional (request 2).

## Review round 4 (re-confirmation fix_required at `dc3cb9c`), head `7a90b92`

The re-confirmation (`research/plan/evidence/a/A1-confirm-dc3cb9c.json` on `claude/backend-impl`) closed RV2-1 and RV2-2. It raised one blocking item, RV3-1 (a test gap; the code was correct), and two nonblocking items, RV3-2 and RV3-3. There is one commit per item. No SQL changed this round; the last commit touching 0015 is `7a90b92` (header comment only).

| Item | Commit | Change | Killing test / mutant (plain and supabase give the same detail) |
|---|---|---|---|
| **RV3-1** (blocking) | `d6e1e13` | `check_binding` gains two `_behind` cases. A claims and holds; B deletes the owner's membership of the personal org, or moves it to another org → B waits, then gets `23514` frozen, and the org keeps 1 member. The docstring now says a join is raced in either order, and a removal or move into an uncommitted claim. | `test_binding__…`. Mutant `a1_binding_guard_locks_new_only` (the guard's `array[old.org_id, new.org_id]` → `array[new.org_id]`, the reviewer's `r3_guard_new_only`) is killed: `the owner left a personal org while the claim bound it (delete): {'pid': 78, 'got': None}` (supabase: `'pid': 267`) |
| RV3-2 | `1879b3c` | Limits (round 3) bullet 2 corrected in place: two same-user claims serialise on the identity-claim unique index; the 40P01 surface is a claim racing a multi-org membership writer in the opposite order (raised, retryable, nothing minted; `order by o.id` does not remove it). The A2 fixture gains `error 40P01` = retry the same call, and R-A1 states it. **Choice: the lock on every created org is kept**, not narrowed to the personal org. The personal org is selected before the lock, and 0006 re-selects it in a later statement, so a personal-only lock can miss the org 0006 finally binds when the owner's membership changes in between. The lock on every created org has no such gap. | — (documentation; no code change) |
| RV3-3 | `fee0804` | Recorded as an R-A1 race limit rather than a FOR UPDATE + re-check in the loop. That fix covers only the join-first order: a retire-first join still waits and lands in the suspended org. Retirement is operator-initiated, has no money effect (a side org has no wallet), and `set_suspension(org, false, …)` lifts the suspension. | — (documentation) |
| header | `7a90b92` | 0015 line 1 now ends `(M-4, M-6, SEC-3, RM-*, RV2-1, RV2-2, RV3-1..3: tests and notes only, no SQL change); applied to no hosted or shared environment (R84).` | — |

Version of 0015 tested (R84): `git rev-parse 7a90b92:apps/app/supabase/migrations/0015_signup_eligibility.sql` → `1c7e7cbba86c14ec0e8138bf34e72fc02dc2cbac`. It differs from `d653e7c`'s only in line 1.

### Results

UTC, 2026-09-23, in `apps/infrx-api`, at `7a90b92`. Logs are in `/tmp/claude-1000/a1-round4/`. There was no HarnessBusy wait this round (`steps.log`).

| Command | Exit | Tail |
|---|---|---|
| `kill()` on `a1_binding_guard_locks_new_only` (plain / `INFRX_D1_IMAGE=supabase`) | 0 / 0 | `killed` on both images, with the detail in the table above |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d/test_signup.py` (plain) | 0 | `33 passed in 43.70s` |
| same, `INFRX_D1_IMAGE=supabase` | 0 | `33 passed in 49.13s` |
| `INFRX_MUTANTS=all … tests/d/test_migration_mutants.py -k 'a1_ or well_formed'` (plain) | 0 | `36 passed, 194 deselected in 46.77s` |
| same, `INFRX_D1_IMAGE=supabase` | 0 | `36 passed, 194 deselected in 48.30s` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/d` (plain, full D suite with every mutant) | 0 | `309 passed in 415.25s (0:06:55)` (04:09–04:16Z) |

Mutant lists, by import: `D total 228 A1 migration 35 A1 code 15`. New this round: `a1_binding_guard_locks_new_only`. Cleanup: `docker ps -a | grep -c infrx-d1-postgres` → `0`.

### integration_requests

Unchanged from round 3.

## Verification log

- 2026-09-22: Written by the A1 implementation session at `38aea7f`. Counts are quoted from the sweep logs.
- 2026-09-23: Review round appended (M-1…M-6, SEC-3…SEC-6, H3, H5). In place, marked: the H5 fixture line, integration request 8 (SEC-6), and R-A1's rename and digest wording (SEC-3/SEC-4). Counts are quoted from the logs at `c4d7fa3`.
- 2026-09-23: Review round 2 appended (RM-1 blocking; RM-2…RM-5, SEC-R1…SEC-R4). In place, marked: Limits item 3 (RM-2), integration requests 8 (SEC-R3) and 9 (R85), and R-A1's shared-org and created_by-scope lines (RM-1/RM-3). Counts and tails are quoted from `/tmp/claude-1000/a1-round2/*.log` at `ef58367`/`8a26524`.
- 2026-09-23: Review round 3 appended (RV2-1 blocking, RV2-2). In place, marked: R-A1's retirement-scope bullet and the shared/not-created line (RV2-2). Counts and tails are quoted from `/tmp/claude-1000/a1-round3/*.log` at `d653e7c`.
- 2026-09-23: Review round 4 appended (RV3-1 blocking; RV3-2, RV3-3). In place, marked: Limits (round 3) bullet 2 (RV3-2), the A2 fixture 40P01 row (RV3-2), and two R-A1 lines (RV3-2, RV3-3). Counts and tails are quoted from `/tmp/claude-1000/a1-round4/*.log` at `7a90b92`.
