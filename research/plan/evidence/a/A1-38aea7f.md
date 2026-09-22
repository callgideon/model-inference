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
> - suspends each personal org the user alone owns (audited `admin_set_suspension`, code `operator_request`);
> - freezes the wallet: no new CREDIT hold and no signup grant. In-flight settlement and D5 compensating entries still land.
>
> The ledger, entitlement and identity claim are retained as money history. Eligibility is one grant per individual UUID **and** per verified address: sha256 of the lower-cased, trimmed email, stored as a digest only. The digest is retained after retirement, so delete + re-create with the same address is `identity_reused`, never a second grant.
>
> Open for the coordinator/legal (**P-05** abuse bounds; **P-09**-adjacent retention commitments): whether the digest's retention is bounded, and whether provider-specific address folding (dots, `+tags`) is required. Neither is implemented; the code comment marks the exact-match ceiling.

## A2 fixture: the console onboarding action

The console calls this server-side only (route handler or server action), after `supabase.auth.getUser()`, with the service-role client from `lib/supabase/admin.ts`. It never calls it from the browser, and it passes no wallet id and no evidence:

```ts
const { data, error } = await createAdminClient()
  .rpc("claim_signup_grant", { p_user_id: user.id, p_campaign_version: "launch_2026_09" });
// data: exactly one row
```

| `status` | Row | Console meaning |
|---|---|---|
| `granted` | `{status, user_id, wallet_id, ledger_operation_id, amount: "10000.00000000", granted_at}` | credited now (then read `console_wallet_summary`) |
| `replayed` | same columns, the original grant | already credited (callback retry / first login / backfill) |
| `unverified` | grant columns null | "verify your email" |
| `identity_reused` / `rollout_hold` / `retired` | grant columns null | one neutral "not eligible / under review" message; do not distinguish them to the user |
| error `55000` | — | signup grant not enabled yet (flag): "credits pending", retry later |
| error `22023` | — | programming error (no user) |

Example `granted` row: `{"status":"granted","user_id":"<uuid>","wallet_id":"<uuid>","ledger_operation_id":"<uuid>","amount":"10000.00000000","granted_at":"2026-09-22T23:04:31.123456+00:00"}`. Amounts are text (R59-9). Persist-before-display holds, because the RPC returns after commit.

Migration compatibility: 0015 requires 0006 (grant seam, flags), 0009 (`verified_user`, `set_suspension`) and D2's 0010–0014 in number order only (no dependency). The deployed console's reads are unchanged, and the D1R legacy read path passes.

## Limits

1. Not applied to any hosted project. The hosted auth has `disable_signup` true and `mailer_autoconfirm` false (I1B). Public onboarding config is **P-05 pending**.
2. The code mutants target pure cases with fakes, because the shared runner's nested pytest cannot hold the D harness's port lock while the parent suite does. The PostgreSQL paths of `PgSignup`/`backfill` are proven by the DB cases, not by code mutants.
3. There is a small window between a claim's retired check and a concurrent retirement's commit, in which the claim can still grant. The wallet is frozen by the hold guard either way (documented, not closed).
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
8. **D2**: a hold refused with `23514 … frozen` means the account was retired. Its personal org is also suspended, so the gateway's existing suspension refusal answers first.
9. **Coordinator / 08 §10**: record R-A1 (above) or rule otherwise. Also record that the migration numbers 0010–0014 belong to D2 and 0015 to A1.
10. **Docs** (`apps/app/supabase/README.md`, not owned): add a 0015 paragraph.

Nothing was applied to any hosted project.

## Verification log

- 2026-09-22: Written by the A1 implementation session at `38aea7f`. Counts are quoted from the sweep logs.
