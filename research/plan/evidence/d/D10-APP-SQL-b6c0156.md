# D10-APP-SQL: 0024 console read port (C0 WR-5, U1R WR-3(a)/(b), U4 WR-U4-2, C3A WR-C3A-4, W5-F5 WR-W5F5-1)

| Field | Value |
|---|---|
| Lane / branch | D10-APP-SQL, `codex/d10-app-sql` (worktree `.claude/worktrees/codex-d10-app-sql`) |
| Base | `273990a0` (codex/door-revoke: 0001-0023 final) |
| Code head | `12f92a6f`. This evidence is committed on top. Commits:<br>• tests first `21107cbb`<br>• migration `b6c01566`<br>• coordinator addendum (U4/C3A): tests first `d90933c8`, SQL `a273cac3`, test fixes `9c89593c`<br>• WR-W5F5-1: tests first `6cff4f9d`, SQL `12f92a6f`<br>No amend, reset or rebase. The filename keeps the first code head, `b6c0156`. | (The filename keeps the first code head, `b6c0156`.) |
| Migration | `apps/app/supabase/migrations/0024_console_read_port.sql`, sha256 `985d01ea52a1736ed5bde6b42f4eb315a4b61d3c405f2f04a3e636c786dd7d50` (earlier: `9f9c14aa…` at b6c01566, `e0ff5026…` at a273cac3) |
| 0024 carries | credits-in index; consumer_credit_ledger page; consumer_jobs filters; consumer_job_result withhold (U4); consumer_may_create_key policy (C3A); monitor grant + policy (W5-F5) |
| Rulings | R59-2/R59-4 (no actor/wallet exposed; revoke-then-grant), R64 (money as exact text), R66 (identity via the consumer wallet), R122-R127 (least privilege; PostgreSQL decides), R117/R118, R129 |
| Isolation | task-local PostgreSQL `INFRX_D_TASK=revoke` (55459, `infrx-revoke-postgres[-supabase]`), PostgREST `infrx-d10-postgrest` on `infrx-d10-net` (D10's names; nothing published on the host). No hosted DB, pilot box, AWS/SSM. Containers removed by the harness at exit. |

## Changed paths (all owned)

| Path | What |
|---|---|
| `apps/app/supabase/migrations/0024_console_read_port.sql` | new: the index, `public.consumer_credit_ledger`, `public.consumer_jobs` with filters, `public.consumer_job_result` with the withheld refusal, `public.consumer_may_create_key()` and the recreated `api_keys_insert_owner` policy, grants |
| `apps/infrx-api/tests/d/checks_port.py` | new: eight SQL-level checks (ledger page, ledger plan, credits-in plan, jobs filters, privileges, withheld result, verified+funded key insert, monitor login reads unknown holds) |
| `apps/infrx-api/tests/d/test_port_d10.py` | new: the eight checks plus re-run and 0001-0023 byte-identity (10 cases) |
| `apps/infrx-api/tests/d/checks.py` | `EXPECTED_FUNCTION_CALLERS`: consumer_jobs' new signature, `consumer_credit_ledger(text,integer)`, `consumer_may_create_key()`. `check_role_matrix` makes the owner a verified individual with a consumer wallet for the duration of the check (rolled back), so the key-insert attacks are still refused by their own defenses (column grant, tenant, authorship) and "owner creates a key" stays a real positive control. Without it the 0001 mutant `api_keys_insert_is_table_wide` would survive, because the new policy alone would refuse every insert. |
| `apps/infrx-api/tests/d/checks_credit.py` | `ALLOWED_LEGACY_CHANGES` names the one 0001-0005 object 0024 changes (the `api_keys_insert_owner` policy; its exact expression is pinned in `checks_port`). The console-insert control in `check_operator_seams` confirms its individual's email first (rolled back). |
| `apps/infrx-api/tests/d/checks_reads.py` | `check_reads_privileges` table: the same two function rows (the exact `RUNTIME_FUNCTIONS` set is unchanged: 41). The monitor column matrix gains `credit_wallet_holds.state` (granted) and `credit_wallet_holds.amount` (not granted). |
| `apps/infrx-api/tests/d/test_upgrade_d10.py` | the D10 set includes `0024_` (upgrade from 0018 + re-run) |
| `apps/infrx-api/tests/d/test_postgrest_d10.py` | the new reads through real PostgREST v13.0.4 with signed JWTs, plus the direct `POST /api_keys` of an unverified individual (refused) and of a verified one (201) |
| `apps/infrx-api/tests/d/d10_mutants.py` | `PORT`. Four 0021 mutants are re-anchored on 0024's copies (0024 redefines `consumer_jobs` and `consumer_job_result`). Nine new mutants. |
| `apps/infrx-api/infrx/state/pgtesting.py` | not changed: it holds no migration pin |

## The SQL, per request

1. **U1R WR-3(b), credits-in index.** Exactly the requested text:
   `create index if not exists credit_ledger_wallet_credits_in_idx on infrx.credit_ledger (wallet_id) where kind <> 'inference_debit';`
   U1R's read (`console_credit_ledger` filtered by `wallet_id` and `kind <> 'inference_debit'`, `limit 101`) is unchanged. Its quals are leakproof, so they reach the base relation through the barrier view.
   Before: `Bitmap Heap Scan on credit_ledger l`, `Recheck Cond: (wallet_id = …)`, `Filter: (kind <> 'inference_debit')`. After: the plan names `credit_ledger_wallet_credits_in_idx`.
   This was measured as role `authenticated` with the JWT subject and `enable_seqscan = off` (U1R's P02 form), with 2,000 debits in the wallet.
2. **C0 WR-5, `public.consumer_credit_ledger(p_after text default null, p_limit int default 50)`.** This is the name and shape C0 specified.
   - Identity: SECURITY DEFINER, `search_path = public, infrx, pg_temp`. `auth.uid()` must be set (else 42501), then resolves to `infrx.credit_wallets` where `owner_user_id = auth.uid() and kind = 'consumer'`. That is the identity `consumer_org()` resolves (R66). A caller never names a tenant.
   - Returns exactly `entry_id, created_at, kind, amount (text), unit, request_id, reason, cursor`, the columns C0's `creditLedgerEntryOf` reads. No wallet, org or actor is returned (no `visible_principal` per row).
   - Pagination: keyset on `(created_at, entry_id) < (v_at, v_id)` in `order by created_at desc, entry_id desc`, which is 0006's `credit_ledger_wallet_created_idx` order. With no cursor the bound is (`infinity`, `ffffffff-…`), so the first page and later pages are the same single index range stopped by the LIMIT. The cursor is `created_at::text|entry_id`, the same form as `consumer_jobs`; a forged cursor is refused `invalid_cursor`.
   - Limit: `p_limit` outside 1..100 (0021's page cap) or null is refused `invalid_request` (P0001, typed by `domain_error`). It is not clamped, as the brief requires. `consumer_jobs` keeps 0021's clamp, since that is existing callers' behaviour.
   - Plan, measured through auto_explain (nested statements, analyze) as the signed-in individual with 3,000 entries in each of two wallets:
     ```
     Limit  (actual rows=25 loops=1)
       ->  Index Scan using credit_ledger_wallet_created_idx on credit_ledger l  (actual rows=25 loops=1)
             Index Cond: ((wallet_id = '…'::uuid) AND (ROW(created_at, entry_id) < ROW('infinity'::timestamp with time zone, 'ffffffff-…'::uuid)))
     ```
     The page from a cursor 2,000 entries deep has the same plan and reads 25 rows. There is no Sort, Seq Scan or Bitmap: O(limit), where C0 measured the view's page at O(rows before the cursor).
3. **U1R WR-3(a), `consumer_jobs` filters.** Four defaulted parameters are appended: `p_model text, p_key_id uuid, p_from timestamptz, p_to timestamptz`.
   - `p_model` matches the requested string or the canonical `model_revision`.
   - `p_key_id` is `j.key_id = p_key_id`.
   - The window is half-open: `created_at >= p_from and created_at < p_to`. Filters AND together and page with the same keyset.
   - Only the WHERE clause changed. Body, columns, clamp, identity and grants are 0021's.
   - PostgreSQL cannot add parameters in place, and a second overload would make every one- or two-argument call ambiguous (42725). So 0021's `(text, integer, uuid)` is dropped and recreated in the same migration transaction. Every existing call (positional SQL; PostgREST named `{p_after, p_limit, p_request_id}`) answers exactly as before. That is tested: the unfiltered call equals the all-nulls named call and equals the base table's own-org keyset.
   - **Minimal-form note.** U1R also asked that rows "return `key_id`, key name". That is NOT done: it changes the result shape the C0/U1R parsers pin, and the brief asks for the minimal defaulted-parameter form. The App already lists its keys (`api_keys`) for the filter control. If per-row key names are needed, D10 can append `key_id` to the result in a later migration.
4. **U4 WR-U4-2, `public.consumer_job_result`.** This is U4's replacement text verbatim, applied with `create or replace`, so 0021's ACL (authenticated, service_role) is kept.
   - Order: `auth.uid()` must be set (42501). The ownership check comes next (`consumer_org()` → the job with a `result_ref` in that org, else `not_found`).
   - Then a job that is `held_unknown` or has no usage is refused `result_pending`, the code the App maps to `pending` (the gateway withholds this result).
   - Then 0020's `read_result` runs (pending, expired and scrubbed are refused typed).
   - Tested: the owner's held_unknown success (state succeeded, result stored, expiry persisted) → `result_pending`. The settled sibling is served. Another individual gets `not_found` for both.
   - Not added: a refusal for a non-succeeded job that carries a `result_ref`. F2C.b calls that `no_result`, and U4 did not ask for it. Add it if the settlement ever writes one.
5. **C3A WR-C3A-4, `api_keys_insert_owner`.** 0001 is untouched. 0024 runs `drop policy if exists` + `create policy`, keeping 0001's `is_org_owner(org_id) and created_by = auth.uid()` and adding `and public.consumer_may_create_key()`.
   - The predicate is SECURITY DEFINER with a pinned search_path, and takes no argument. It is true only for `auth.uid()` when all of these hold:
     - `infrx.verified_user(auth.uid()).verification_evidence_ref is not null`
     - `auth.users` email is non-empty and `deleted_at` is null. That is `claim_signup_grant`'s predicate (0015:187-190).
     - The caller owns a `kind = 'consumer'` wallet.
   - It must be SECURITY DEFINER because the policy runs as the caller, who has no `infrx` usage. It is granted to authenticated and service_role and revoked from anon and public. It reveals one boolean about the caller only.
   - Tested (SQL, as the JWT principal), each case refused 42501 by RLS:
     - unverified with a wallet (the D fixture's grant seam takes evidence as an argument, so `email_confirmed_at` is null)
     - unverified with no wallet
     - verified with no wallet

     A verified individual with a wallet is accepted. Through PostgREST: the unverified `POST /api_keys` is refused, and after verification it returns 201.
   - **Behaviour change:** an org owner with no consumer wallet can no longer create keys through the browser. That includes a legacy USD pilot owner, and the D1 and E2 fixture owners. Keys for them go through the service/operator seams (0009).
   - Not added: a rule tying the key to the wallet's personal org. The brief did not ask, and 0001's owner check still scopes the org.
6. **W5-F5 WR-W5F5-1, the monitor login on CREDIT holds.** This is W5-F5's text verbatim:
   - `grant select (state) on infrx.credit_wallet_holds to infrx_monitor;`
   - `drop policy if exists monitor_reads …; create policy monitor_reads on infrx.credit_wallet_holds for select to infrx_monitor using (true);`

   The table has RLS (0006), so the grant alone reads zero rows, and both statements are needed. The monitor gets the state only, never an amount.
   Tested on the monitor LOGIN itself: `alter role … login password` in the lane's container, then back to NOLOGIN, with no `set role`. W5-F5's `RECONCILIATION_SQL` (copied verbatim) answers `(0, n)`, where n counts every unknown hold, including a CREDIT held_unknown job the check commits. A hold's `amount` is refused 42501.
   Fails before: `permission denied for table credit_wallet_holds`, the W5-F5 measurement.
7. **A2:** needs nothing from this migration.

## Tests first: fails-before (base + tests, no 0024)

`INFRX_D_TASK=revoke uv run --frozen --no-sync pytest -q tests/d/test_port_d10.py` gave exit 1: **6 failed, 1 passed** (the byte-identity case).

| Case | Failure before |
|---|---|
| ledger own rows / exact / bounded | `UndefinedFunction: public.consumer_credit_ledger(text,integer)` |
| ledger page plan | same (42883) |
| credits-in on the partial index | `credits-in filters every wallet entry by kind`: plan `Bitmap Heap Scan … Filter: (kind <> 'inference_debit')` |
| consumer_jobs filters | `the unfiltered call changed: … / ('42883', None)` (no filter parameters) |
| port privileges | `UndefinedFunction` |
| re-runnable | 1 of 3 objects present (`consumer_jobs(text,integer,uuid)` only) |

Addendum (tests first `d90933c8` on 0024 as of `b6c01566`), same command: exit 1, **4 failed, 5 passed**.

| Case | Failure before |
|---|---|
| withheld result | `consumer_job_result served an unknown-usage result: (None, [('result of 9c95280c-…',)])` |
| key insert | `unverified, funded: an api_keys insert was accepted (None)` |
| port privileges | `UndefinedFunction` (`public.consumer_may_create_key()`) |
| re-runnable | 5 of 6 objects (no `consumer_may_create_key`) |

The base behaves the same way on both cases, since 0021's `consumer_job_result` and 0001's policy are what `b6c01566` still ran.

After 0024 (`a273cac3`): 9/9 on the plain image. On Supabase the ledger-plan case is skipped (see below).

WR-W5F5-1 (tests first `6cff4f9d` on 0024 as of `9c89593c`): `pytest -q tests/d/test_port_d10.py tests/d/test_reads.py -k "monitor or re_runnable or reads_privileges"` gave exit 1, **3 failed**.

| Case | Failure before |
|---|---|
| monitor login | `InsufficientPrivilege: permission denied for table credit_wallet_holds` |
| reads privileges | `infrx_monitor select infrx.credit_wallet_holds.state: False` |
| re-run | 7 of 8 objects |

At `12f92a6f`: 3 passed, and `test_port_d10.py` 10/10.

## Commands (worktree `apps/infrx-api`, `INFRX_D_TASK=revoke`)

| Command | Head | Exit | Result |
|---|---|---|---|
| `make api-env` (repo root) | base | 0 | pinned env |
| `pytest -q tests/d/test_port_d10.py` without 0024 | 21107cbb | 1 | 6 failed / 1 passed (above) |
| same, with 0024 | b6c01566 | 0 | 7 passed |
| `pytest -q -rs tests/d/test_port_d10.py test_reads.py test_followup_d10.py test_upgrade_d10.py test_schema_postgres.py test_credit_schema.py test_postgrest_d10.py test_composition_pg.py tests/g/test_composition.py` (plain PG 16) | b6c01566 | 1 | **125 passed, 1 failed, 1 skipped, 3 xfailed**. The 1 failure is `test_composition_pg::test_f_base…dedicated_login` (`permission denied for function admit`), identical with 0024 moved out of the tree: DOOR-REVOKE's recorded W5 merge gate, by design on this base. The skip is PostgREST (plain image). |
| `INFRX_D1_IMAGE=supabase pytest -q -rs tests/d/test_port_d10.py test_postgrest_d10.py test_reads.py test_followup_d10.py test_upgrade_d10.py` | b6c01566 (before the Supabase skip marker) | 1 | 57 passed, 1 failed, 3 xfailed. The failure is the ledger-plan case: `access to library "auto_explain" is not allowed` (Supabase's `postgres` is not a superuser). It is now a declared skip on Supabase. PostgREST ran and passed: own ledger through `/rpc/consumer_credit_ledger`, over-cap 400 `invalid_request`, anon 401/403, `consumer_jobs` with `{p_limit}` only (no ambiguity), `p_key_id` and an unknown `p_model` → []. |
| `INFRX_MUTANTS=all pytest -q tests/d/test_migration_mutants.py` (whole D list) | b6c01566 | none | **Not completed.** The run died partway through the D4 mutants: a second agent began running `tests/d` on the same worktree and port 55459 (see "Concurrency" below). No result is claimed. |
| `INFRX_MUTANTS=all pytest -q tests/d/test_migration_mutants.py -k "d10_ or well_formed or superseded"` and `test_port_d10.py test_reads.py test_upgrade_d10.py` | 12f92a6f | 1 | **Not a result.** Every case failed in under 25 s with `pgharness.HarnessRefused`: the 55459 flock was held by the parallel agent's run. Nothing ran against a database. |
| `pytest -q tests/d/test_migration_mutants.py -k "well_formed or superseded or d10_ledger or d10_credits_in or d10_jobs_key or d10_consumer"` | b6c01566 | 0 | 14 passed (the supersession guard is green with the three mutants re-anchored) |
| `python3 research/plan/scripts/validate_plan.py` (repo root) | b6c01566 | 0 | PASS (927 links / 219 docs) |
| `git diff --name-only 273990a0 -- apps/app/supabase/migrations/00{01..23}_*.sql` (also the test `test_0001_to_0023_are_byte_identical_to_the_base`) | b6c01566 | 0 | empty: 0001-0023 byte-identical |

The 880-case layer-3 RLS suite (`tests/integration`, E2's pgstate role matrix) **was not run**. It needs the e2 stack namespace, which this lane does not own. Its rows for 0024 are wiring W-D10A-1 below.

## Mutants (new, file `0024_console_read_port.sql`, scenario admission; all KILLED)

| Name | Edit | Killed by |
|---|---|---|
| `d10_ledger_any_wallet` | drop `w.owner_user_id = auth.uid() and` (ownership check) | port_ledger (the second individual pages another wallet) |
| `d10_ledger_limit_uncapped` | drop `or p_limit > 100` (page cap) | port_ledger (101 answered rows instead of `invalid_request`) |
| `d10_ledger_page_sorts` | `entry_id desc` → `entry_id` in the page order | port_ledger_plan (Sort appears) |
| `d10_credits_in_unindexed` | drop the partial index | port_credits_in |
| `d10_jobs_key_filter_ignored` | drop the `p_key_id` predicate | port_jobs_filters |
| `d10_ledger_for_the_runtime` | grant the ledger read to `infrx_runtime` | port_privileges |
| `d10_monitor_holds_ungranted` | drop the `select (state)` grant | port_monitor (the reconciliation read is refused) |
| `d10_monitor_holds_without_policy` | drop the monitor policy | port_monitor (RLS answers 0 unknown holds while 1 waits) |
| `d10_result_served_while_unreconciled` | the held_unknown / no-usage refusal made `false` | port_result_withheld |
| `d10_key_insert_unverified` | drop `and public.consumer_may_create_key()` from the policy | port_key_insert |
| `d10_key_insert_verification_ignored` | the predicate ignores `verification_evidence_ref` | port_key_insert |

Re-anchored on 0024's copies (same name, edit and check): `d10_consumer_reads_any_tenant`, `d10_consumer_unit_mislabelled` and `d10_consumer_limit_unbounded` (`consumer_jobs`), and `d10_consumer_result_past_expiry` (`consumer_job_result`). Without this, the supersession guard fails.

## Re-run and rollback

- Re-run: `test_0024_is_re_runnable` re-applies 0024 on the migrated, seeded database. Function definitions (md5 of `pg_get_functiondef`), ACLs and the index are identical, and the ledger and privilege checks still pass. `test_upgrade_d10` also applies 0019-0024 over a seeded 0018 history and re-applies them with no snapshot change.
- ROLLBACK (0024 alone, also in the file header):
  ```sql
  drop policy if exists monitor_reads on infrx.credit_wallet_holds;
  revoke select (state) on infrx.credit_wallet_holds from infrx_monitor;
  drop policy if exists api_keys_insert_owner on public.api_keys;
  create policy api_keys_insert_owner on public.api_keys for insert to authenticated
    with check (public.is_org_owner(org_id) and created_by = auth.uid());   -- 0001's
  drop function if exists public.consumer_may_create_key();
  -- re-run 0021's create or replace function public.consumer_job_result(...)
  drop function if exists public.consumer_credit_ledger(text, integer);
  drop function if exists public.consumer_jobs(text, integer, uuid, text, uuid, timestamptz, timestamptz);
  -- re-run 0021's create or replace function public.consumer_jobs(...) and its revoke/grant lines
  drop index if exists infrx.credit_ledger_wallet_credits_in_idx;
  ```
  No data moves. Roll back any App build that calls the new reads first.

## Wiring (who consumes what; coordinator applies, this lane does not)

- **C0** (`apps/app/lib/services/console.ts` `creditLedger`): replace the `credit_ledger_page` query over `console_credit_ledger` with `rpc("consumer_credit_ledger", { p_after: cursor, p_limit })`.
  - The rows carry `entry_id, created_at, kind, amount (string), unit, request_id, reason, cursor`.
  - `p_limit` must be 1..100: ask for `limit + 1` ≤ 100, or the call is refused 400 `invalid_request`.
  - The cursor is opaque (`created_at|entry_id`).
- **U1R** (`billing/credit-reads.ts`):
  - "Spent" needs no code change: the credits-in read now uses the index. U1R's own P02 lines (their WR-3(b) diff) can land.
  - `usage/`: `consumer_jobs` accepts `p_model`, `p_key_id`, `p_from`, `p_to`. Remove "Filtering by API key or model is not available yet" when the controls are wired. The row does NOT carry `key_id`/key name (see note 3).
  - The ledger page can move to `consumer_credit_ledger` (U1R's WR-3(c) is subsumed by C0 WR-5).
- **U4** (`tests/u/request-pg.test.ts`, U4-owned): P08 drops its `todo: WR_U4_2` once 0024 is in the tree. The App mapping (`result_pending:` → `pending`) is unchanged.
- **C3A** (`tests/c/actions-postgrest.test.ts`, C3A-owned): flip the "known gap" case to `assert.ok(error !== null)`, as C3A proposed.
- **W-D10A-1: layer-3 and E3C tables** (outside this lane's paths). This is the exact patch (`git apply --check -p1` is clean on 273990a0):
  - pgstate's function rows
  - owner_alpha made a verified individual with a consumer wallet, so the E2-RLS-30 positive control survives the tighter INSERT policy
  - the E3C signed-in function list
  - the migration list
  - the `infrx_monitor` login's column surface (`L3-LOGIN-infrx_monitor` columns: `infrx.credit_wallet_holds.state:SELECT`)

  ```diff
diff -ru a/tests/integration/backend/e3c/scenarios_surface.py b/tests/integration/backend/e3c/scenarios_surface.py
--- a/tests/integration/backend/e3c/scenarios_surface.py
+++ b/tests/integration/backend/e3c/scenarios_surface.py
@@ -33,7 +33,9 @@
     "public.console_legacy_usd_statement"}
 # 0021 (D10, C0/U4): the signed-in consumer's own reads, auth.uid()-scoped, granted to
 # `authenticated` only - never `anon` (anon executing them is still reported).
-SIGNED_IN_FUNCTIONS = {"public.consumer_jobs", "public.consumer_job_result"}
+SIGNED_IN_FUNCTIONS = {"public.consumer_jobs", "public.consumer_job_result",
+                       "public.consumer_credit_ledger",              # 0024 (D10-APP-SQL)
+                       "public.consumer_may_create_key"}             # 0024 (C3A WR-C3A-4)
 BROWSER_WRITES = {("public.profiles", "UPDATE"), ("public.organizations", "UPDATE"),
                   ("public.api_keys", "UPDATE")}
 
diff -ru a/tests/integration/pgstate.py b/tests/integration/pgstate.py
--- a/tests/integration/pgstate.py
+++ b/tests/integration/pgstate.py
@@ -292,6 +292,14 @@
     # to administer.
     conn.execute("insert into public.org_members (org_id, user_id, role) values (%s, %s, 'member')",
                  (fixtures.orgs["alpha"], fixtures.user("member_alpha")))
+    # 0024 (C3A WR-C3A-4): a browser key INSERT needs a verified individual holding a
+    # consumer wallet, so owner_alpha is one: E2-RLS-30 stays a positive control and
+    # E2-RLS-20/31/32 stay refused by the owner/tenant/authorship checks, not by a wallet.
+    conn.execute("update auth.users set email_confirmed_at = now() where id = %s",
+                 (fixtures.user("owner_alpha"),))
+    conn.execute("insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id) "
+                 "values ('consumer', %s, %s)", (fixtures.user("owner_alpha"),
+                                                  fixtures.orgs["alpha"]))
 
     model_id = conn.execute("select id from public.models order by sort, id limit 1").fetchone()
     if model_id is None:
@@ -811,7 +819,12 @@
     "public.claim_signup_grant(uuid,text,uuid)": SERVICE,
     # 0021:420-428 (D10): the signed-in consumer reads; the org resolver is nobody's
     "public.consumer_job_result(uuid)": BROWSER,
-    "public.consumer_jobs(text,integer,uuid)": BROWSER,
+    # 0024 (D10-APP-SQL): consumer_jobs gains four defaulted filters (0021's signature is
+    # dropped and recreated), and C0 WR-5's own-ledger page
+    "public.consumer_credit_ledger(text,integer)": BROWSER,
+    "public.consumer_may_create_key()": BROWSER,   # 0024: the api_keys INSERT predicate
+    "public.consumer_jobs(text,integer,uuid,text,uuid,timestamp with time zone,"
+    "timestamp with time zone)": BROWSER,
     "public.consumer_org()": NOBODY,
     "public.handle_new_user()": SERVICE,
     "public.is_operator()": BROWSER,
@@ -1000,7 +1013,9 @@
                 "kind", "acknowledged_at", "claimed_at", "available_at")]
             + [f"infrx.credit_holds.{c}:SELECT" for c in (
                 "request_id", "state", "reconcile_after")]
-            + ["infrx.stream_chunks.expires_at:SELECT", "infrx.job_results.request_id:SELECT"])),
+            + ["infrx.stream_chunks.expires_at:SELECT", "infrx.job_results.request_id:SELECT",
+               # 0024 (W5-F5 WR-W5F5-1): the CREDIT holds' state, with a monitor policy
+               "infrx.credit_wallet_holds.state:SELECT"])),
     },
 }
 _LOGIN_ATTRIBUTES = ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb", "rolcanlogin",
diff -ru a/tests/integration/test_harness.py b/tests/integration/test_harness.py
--- a/tests/integration/test_harness.py
+++ b/tests/integration/test_harness.py
@@ -208,6 +208,8 @@
         "0018_terminal_settlement.sql",
         # D10 (upload readiness, content lifecycle, read authority + the dedicated logins)
         "0019_upload_readiness.sql", "0020_content_lifecycle.sql", "0021_read_authority.sql",
+        # (W-DR1 v2 adds 0022/0023 here) D10-APP-SQL: the console read port
+        "0024_console_read_port.sql",
     ]
     assert files[0].parent == harness.MIGRATIONS_DIR
     digests = pgstate.migration_digests()
  ```
  Proof: the e2 layer-3 run on the merged SHA. Without the patch:
  - the old `consumer_jobs(text,integer,uuid)` row fails (the function is absent)
  - the two new functions fail completeness
  - E2-RLS-30 (an owner mints a key) is refused by the new policy
  - the E3C browser-surface scenario reports `consumer_credit_ledger` and `consumer_may_create_key` as extra
  - `L3-LOGIN-infrx_monitor-columns` differs by the new column
- **W5-F5** (`tests/w/test_worker_main_pg…the_monitor_login_reads_what_the_runtime_login_may_not`, W5-owned): the in-test grant and policy (its third step) become a plain assertion once 0024 is in the tree.
- **ORDER:** merge after DOOR-REVOKE (base 273990a0). Deploy 0024 before the App build that calls the new reads. The running App is unaffected.

## Concurrency and WR-W5F5-1 (read this first when reviewing the final head)

- While this lane was verifying, a second implementer committed three 0024 addenda to this branch in this worktree, using the same port 55459:
  - `d90933c8`, `a273cac3`, `9c89593c`: U4 WR-U4-2 (the `consumer_job_result` withheld refusal) and C3A WR-C3A-4 (the verified+funded `api_keys` INSERT policy, `consumer_may_create_key()`).
  - `6cff4f9d` (tests first), `12f92a6f`: W5-F5 **WR-W5F5-1**.
  The two agents apparently share a session scratchpad, which also overwrote this lane's run log.
- WR-W5F5-1 is the addendum the coordinator later asked this lane for. It was already committed by the other agent when this lane came to apply it, so this lane did **not** duplicate it. Review of `12f92a6f`:
  - The SQL is exactly the three requested statements: `grant select (state) on infrx.credit_wallet_holds to infrx_monitor`; `drop policy if exists monitor_reads …`; `create policy monitor_reads … for select to infrx_monitor using (true)`.
  - Header and ROLLBACK lines were added.
  - `checks_reads` monitor matrix: `state` select only.
  - A monitor-login case was added with its fails-before.
  - Two mutants were added: `d10_monitor_holds_ungranted` and `d10_monitor_holds_without_policy`.
- This lane has not run the suites or mutants at `12f92a6f`. The port was held by the other agent's runs (the refused attempt above). The final-head verification belongs to that agent's evidence, or to a coordinator rerun of:
  - `INFRX_D_TASK=revoke uv run --frozen --no-sync pytest -q tests/d/test_port_d10.py tests/d/test_reads.py tests/d/test_upgrade_d10.py tests/d/test_followup_d10.py`
  - `INFRX_D_TASK=revoke INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/d/test_migration_mutants.py -k "d10_ or well_formed or superseded"`
  - the same suites with `INFRX_D1_IMAGE=supabase` (plus `test_postgrest_d10.py`).
- The 0024 sha256 above (`9f9c14aa…`) is the file at `b6c01566`, before the addenda. The final file's hash is `985d01ea52a1736ed5bde6b42f4eb315a4b61d3c405f2f04a3e636c786dd7d50 (at 12f92a6f)`.

## Open issues

- WR-C3A-4 narrows who may create a key in the browser. Org owners without a consumer wallet (legacy USD pilot owners, operators without a claim) now get RLS 42501 from the console's create-key action, and need the operator seam. The coordinator should confirm that no live console flow relies on it (C3A's shared action already refuses them).

- Ledger plan on the Supabase image (PG 17) is not measured by auto_explain: its `postgres` cannot LOAD the library. The case is a declared skip there. The SQL and the index are identical and the row-comparison index condition is standard since PG 8.2, but the proof is PG 16's.
- `consumer_jobs` model/key filters are applied inside the org's keyset scan (`ponytail:` note in the SQL). A rare value walks the org's history. Add `(org_id, key_id, created_at)` if per-key pages get slow at scale.
- `test_composition_pg::…dedicated_login` stays red on this base until W5 is merged (DOOR-REVOKE's recorded gate). It is not caused by 0024.

## Proposed ruling (coordinator numbers it)

> D10-APP-SQL: consumer ledger pages are read only through `public.consumer_credit_ledger`: SECURITY DEFINER, the caller's own consumer wallet from `auth.uid()`, keyset on `credit_ledger_wallet_created_idx`, 1..100 rows or `invalid_request`, amounts as exact text, no actor/wallet/org. Consumer read filters are appended as defaulted parameters; a signature change drops and recreates in one migration and restates the grants (authenticated + service_role, never the runtime or monitor logins). `consumer_job_result` serves exactly what the gateway serves: after the ownership check, an unreconciled result (held_unknown or no usage) is `result_pending`. A browser `api_keys` INSERT requires, besides 0001's owner/creator check, a verified individual (the claim path's predicate) holding a consumer wallet; anyone else's keys are issued through the service/operator seams.

## Remaining effort

Lane: 0 h pending review. Coordinator: W-D10A-1 (about 0.25 h) and a layer-3 rerun on the merged SHA. C0/U1R: switch to the new RPC and filters (about 1-2 h, their lanes).
- Estimate: optimistic 0.5 h, likely 1 h, pessimistic 3 h. Confidence medium.
- Basis: SQL, tests, plans and mutants are green here. What remains is shared-file wiring and the App lanes' adoption.

## Fix round (2026-09-26, code head `df21cfa6`)

Findings 0-D10SQL-CM-1, 1-D10R-1 and 1-D10R-2 (review of handback `5055aae5`). One instance only; nothing else used this worktree or port 55459 during the round. No push, rebase, reset, amend or stash. No hosted DB, pilot box, AWS or SSM. The lane's containers and network were gone at the end (checked with `docker ps -a`).

| Field | Value |
|---|---|
| Commits | `57ced756` tests first · `3b6dee82` 0024 + mutant · `df21cfa6` plain-image-only mutant declaration. This evidence is committed on top. |
| 0024 sha256 | `8e0bfd6288ad716342de932053b7f7aca8c9517b551458e1b86a1a84acc394e0` (was `985d01ea…7d50` at `12f92a6f`). Only `consumer_may_create_key()`'s body and the header/section comments changed. The policy expression, grants and every other object are unchanged. |
| 0001-0023 | `git diff --name-only 273990a0 -- apps/app/supabase/migrations/` lists only `0024_console_read_port.sql` |
| Changed paths (all owned) | `apps/app/supabase/migrations/0024_console_read_port.sql`, and under `apps/infrx-api/tests/d/`: `checks.py`, `checks_credit.py`, `checks_port.py`, `d10_mutants.py`, `test_migration_mutants.py`, `test_port_d10.py`, `test_postgrest_d10.py` |

| Finding | Status | What changed |
|---|---|---|
| 0-D10SQL-CM-1 and 1-D10R-1 (fixtures write `auth.users.email_confirmed_at`, which the bare Supabase image lacks) | **fixed** | Each of the four fixtures now calls `checks_signup.gotrue_columns(conn)` first: `checks.check_role_matrix`, `checks_credit.check_operator_seams`, `checks_port`'s key-insert check and `test_postgrest_d10`. The call runs before any rolled-back body, because on Supabase it is `supabase_admin` DDL in another session and must not wait on this session's row locks. `checks.py` and `checks_credit.py` import it locally, since `checks_signup` imports both. `check_operator_seams` had an `information_schema` probe; now that the column always exists, its verified path runs unconditionally and the probe is removed. |
| 1-D10R-2 (the INSERT policy shut out org owners with no consumer wallet) | **fixed, pending the coordinator's ruling text** | `consumer_may_create_key()` no longer joins `infrx.credit_wallets`. A browser key INSERT now needs 0001's owner/creator check AND a **verified individual**: the claim path's predicate, `verified_user` evidence plus a live, non-empty email. A verified pilot or invited owner with no wallet passes. See "The predicate chosen" below. |

**The predicate chosen.** C3A's first form was "owns a consumer wallet OR verified" (C3A-3f4e5f1.md:247). Its restated form was "the creator is verified; D10 must check pilot/invited owners still pass" (:310). The fix takes the verified branch.

- A consumer wallet is created only by a verified claim (`claim_signup_grant`, or G6B `grant_initial` through `verified_user`). So the wallet branch adds only one case: a wallet holder who is no longer verified (soft-deleted, or email unconfirmed). 04-app §2 refuses that case: "Unverified users cannot issue a funded consumer key".
- A verified owner with no wallet gets a key with no funds, and admission refuses it. That is the same outcome as before 0024.
- **Remaining narrowing versus 0001:** an **unverified** owner, for example a pilot owner whose email was never confirmed, now needs the operator seam. The coordinator can check hosted `auth.users` for owners with a null `email_confirmed_at` who created keys in the browser. This lane cannot query the hosted database.

### Tests first: fails-before

| Head | Command (worktree `apps/infrx-api`, `INFRX_D_TASK=revoke`) | Exit | Result |
|---|---|---|---|
| `5055aae5` (handback) | `INFRX_D1_IMAGE=supabase uv run --frozen --no-sync pytest -q -rs tests/d/test_port_d10.py tests/d/test_postgrest_d10.py tests/d/test_schema_postgres.py tests/d/test_credit_schema.py` | 1 | **5 failed**, 45 passed, 1 skipped, all `UndefinedColumn: column "email_confirmed_at"`. Failing cases: the key insert, the re-run, the PostgREST matrix, `test_dur_rls__browser_roles_cannot_reach_protected_state` and `test_operator_seams__…`. This reproduces the finding. |
| `57ced756` (tests only, old 0024) | same | 1 | **3 failed**, 47 passed, 1 skipped. The two D1/D1R suites pass again. The key insert and the re-run fail with `verified, no wallet: refused (42501)`. The PostgREST case fails on the verified pilot owner's `POST /api_keys`: 42501, "new row violates row-level security policy". |
| `57ced756` | same, plain image | 1 | **2 failed**, 48 passed, 1 skipped (`verified, no wallet: refused (42501)`) |

The new cases, and the broken behaviour each one catches:

- `check_key_insert_needs_verified_individual` (renamed from `…_verified_wallet`).
  - Refused with 42501: unverified with a wallet (`CONSUMER_1`), unverified with no wallet (`RACER`), and the unverified pilot owner (`pgtesting.USERS[ORG_A]` into `ORG_A`, a legacy USD org).
  - Accepted: verified with a wallet, verified with no wallet (`UNGRANTED`), and the **verified pilot owner**.
  - It first asserts which fixture users hold wallets, so a moved wallet cannot make a control vacuous.
  - Catches: a policy that requires a wallet (the 1-D10R-2 regression), and a policy that ignores verification.
- `test_postgrest_d10`: the verified pilot owner's `POST /api_keys` returns 201 through PostgREST v13.0.4 with a signed JWT. That is the deployed console's own insert path.

### Commands at the final code head

| Head | Command | Exit | Result |
|---|---|---|---|
| `3b6dee82` | `INFRX_D1_IMAGE=supabase … pytest -q -rs tests/d/test_port_d10.py tests/d/test_postgrest_d10.py tests/d/test_schema_postgres.py tests/d/test_credit_schema.py` | 0 | 50 passed, 1 skipped (the declared auto_explain plan case) |
| `3b6dee82` | `INFRX_D1_IMAGE=supabase … pytest -q -rs tests/d/test_port_d10.py tests/d/test_postgrest_d10.py tests/d/test_reads.py tests/d/test_followup_d10.py tests/d/test_upgrade_d10.py tests/d/test_schema_postgres.py tests/d/test_credit_schema.py` (the handback's Supabase commands plus the two regressed D suites) | 0 | **100 passed, 1 skipped, 3 xfailed** |
| `3b6dee82` | `… pytest -q -rs tests/d --deselect tests/d/test_migration_mutants.py` (plain PG 16, all of `tests/d`) | 1 | **600 passed, 2 failed, 1 skipped, 8 xfailed**. The 2 failures are the two the coordinator recorded as pre-existing at `12f92a6f`: `test_composition_pg::test_f_base…dedicated_login` (the DOOR-REVOKE W5 gate, `dependency_unavailable`) and `test_lifecycle_conformance::…replay_exactly` (`retention_durable__…`). The skip is PostgREST, which needs the Supabase image. `df21cfa6` changes only the mutant runner's Supabase skip. |
| `df21cfa6` | `INFRX_MUTANTS=all … pytest -q -rs tests/d/test_migration_mutants.py -k 'd10_ or well_formed or superseded or api_keys_insert'` (plain) | 0 | **98 passed**: 93 D10 mutants killed (the new `d10_key_insert_needs_a_wallet` among them), `api_keys_insert_is_table_wide` (0001, role matrix) killed, the two D2 `superseded` mutants killed, the list is well formed, and the supersession guard passes |
| `df21cfa6` | same, `INFRX_D1_IMAGE=supabase` | 0 | **97 passed, 1 skipped**: 92 D10 mutants killed plus the same five other cases. The skip is `d10_ledger_page_sorts` (see below). `d10_key_insert_unverified`, `d10_key_insert_verification_ignored` and `d10_key_insert_needs_a_wallet` are **KILLED** on Supabase, where before they ended as SETUP_ERROR. |
| `3b6dee82` | Supabase run of the same subset, before `df21cfa6` | 1 | 97 passed, 1 failed: `d10_ledger_page_sorts` SETUP_ERROR, `InsufficientPrivilege: access to library "auto_explain" is not allowed` |

Mutant kill details on Supabase (`-s`):

- `d10_key_insert_unverified` → `unverified, funded: an api_keys insert was accepted`
- `d10_key_insert_verification_ignored` → same message
- `d10_key_insert_needs_a_wallet` (**new**: the `credit_wallets` join put back, i.e. the AND form) → `verified, no wallet: refused (42501)`
- `api_keys_insert_is_table_wide` → `browser roles reached protected state` (plain and Supabase)

**Outside the findings: `d10_ledger_page_sorts`.** Its check reads the nested plan through auto_explain, and Supabase's non-superuser `postgres` cannot LOAD that library. The suite case is already a declared skip there. `d10_mutants.PLAIN_IMAGE_ONLY` names this one mutant, and `test_mutant_is_killed` skips it visibly on Supabase. It is still KILLED on the plain image.

### Wiring W-D10A-1 v2 (replaces W-D10A-1 above; the coordinator applies it)

v1 had two defects. It gave `owner_alpha` a wallet the predicate no longer needs. More importantly, it wrote `auth.users.email_confirmed_at`, which E2's pinned image does not have: this is the same defect as 0-D10SQL-CM-1. Measured on the D harness's Supabase container with v1 applied:

- `INFRX_I3B_PG=d INFRX_D_TASK=revoke INFRX_D1_IMAGE=supabase apps/infrx-api/.venv/bin/python -m pytest -q -rs tests/integration/backend/recovery/test_restore.py` gave exit 1, **5 failed, 12 errors**, `UndefinedColumn`.
- The same command without any wiring gives 28 passed, 1 skipped.

v2 changes:

- **E2's template.** v2 adds GoTrue's two columns to E2's template, which is what `backend/stack.py:155-161` already does for its own template. Because the copy and every I3B restore target then match the source, bk01's `auth.dump` restores column for column.
- **The `owner_alpha` hunk.** It only confirms the email.
- **Pinned statement test.** `test_run.py`'s pinned-statement test now expects the template call first.
- **Unchanged hunks.** The other hunks are v1's.
- **Base.** `git apply --check -p1` is clean on this branch, where `tests/integration` is identical to `273990a0`. Patch sha256: `6c55c1c9…88db`.

```diff
diff -ru a/tests/integration/backend/e3c/scenarios_surface.py b/tests/integration/backend/e3c/scenarios_surface.py
--- a/tests/integration/backend/e3c/scenarios_surface.py
+++ b/tests/integration/backend/e3c/scenarios_surface.py
@@ -33,7 +33,9 @@
     "public.console_legacy_usd_statement"}
 # 0021 (D10, C0/U4): the signed-in consumer's own reads, auth.uid()-scoped, granted to
 # `authenticated` only - never `anon` (anon executing them is still reported).
-SIGNED_IN_FUNCTIONS = {"public.consumer_jobs", "public.consumer_job_result"}
+SIGNED_IN_FUNCTIONS = {"public.consumer_jobs", "public.consumer_job_result",
+                       "public.consumer_credit_ledger",              # 0024 (D10-APP-SQL)
+                       "public.consumer_may_create_key"}             # 0024 (C3A WR-C3A-4)
 BROWSER_WRITES = {("public.profiles", "UPDATE"), ("public.organizations", "UPDATE"),
                   ("public.api_keys", "UPDATE")}
 
diff -ru a/tests/integration/backend/recovery/test_restore.py b/tests/integration/backend/recovery/test_restore.py
--- a/tests/integration/backend/recovery/test_restore.py
+++ b/tests/integration/backend/recovery/test_restore.py
@@ -162,6 +162,7 @@
 
 def _create(name: str) -> None:
     if ON_D:
+        d_harness()._sb(harness.PG_TEMPLATE_SOURCE, harness.GOTRUE_COLUMNS)   # as E2's template
         d_harness().recreate(name)                  # the Supabase template's copy
         return
     terminate = ("select pg_terminate_backend(pid) from pg_stat_activity "
diff -ru a/tests/integration/harness.py b/tests/integration/harness.py
--- a/tests/integration/harness.py
+++ b/tests/integration/harness.py
@@ -79,6 +79,11 @@
 PG_DATABASE = f"infrx_{NAMESPACE}"
 PG_ADMIN_ROLE = "supabase_admin"     # the image's superuser; `postgres` is not one
 PG_TEMPLATE_SOURCE = "postgres"
+# GoTrue's own `auth.users` columns: every hosted project has them (GoTrue's migrations), the
+# pinned image's bare auth schema does not, and A1/0024 derive verification from the first.
+# Added to the TEMPLATE, so every copy - and every I3B restore target - matches its source.
+GOTRUE_COLUMNS = ("alter table auth.users add column if not exists email_confirmed_at "
+                  "timestamptz, add column if not exists deleted_at timestamptz")
 CH_USER, CH_PASSWORD, CH_DATABASE = "infrx_e2", "infrx-e2-local", "infrx_e2"
 S3_ACCESS_KEY, S3_SECRET_KEY = "infrxe2minio", "infrx-e2-local-secret"
 S3_BUCKET = PROJECT
@@ -454,6 +459,8 @@
     `database` defaults to E2's; E3B phase 2 builds its JobStore template the same way.
     """
     container = assert_ours(container_of("postgres"))
+    run(["docker", "exec", "-i", container, "psql", "-U", PG_ADMIN_ROLE, "-d",
+         PG_TEMPLATE_SOURCE, "-v", "ON_ERROR_STOP=1", "-c", GOTRUE_COLUMNS], timeout=120.0)
     terminate = (f"select pg_terminate_backend(pid) from pg_stat_activity "
                  f"where datname = '{PG_TEMPLATE_SOURCE}' and pid <> pg_backend_pid()")
     attempts = []
diff -ru a/tests/integration/pgstate.py b/tests/integration/pgstate.py
--- a/tests/integration/pgstate.py
+++ b/tests/integration/pgstate.py
@@ -292,6 +292,12 @@
     # to administer.
     conn.execute("insert into public.org_members (org_id, user_id, role) values (%s, %s, 'member')",
                  (fixtures.orgs["alpha"], fixtures.user("member_alpha")))
+    # 0024 (C3A WR-C3A-4): a browser key INSERT needs a verified individual, so owner_alpha
+    # is one: E2-RLS-30 stays a positive control and E2-RLS-20/31/32 stay refused by the
+    # owner/tenant/authorship checks, not by verification. GoTrue's column is on the
+    # template (`harness.GOTRUE_COLUMNS`).
+    conn.execute("update auth.users set email_confirmed_at = now() where id = %s",
+                 (fixtures.user("owner_alpha"),))
 
     model_id = conn.execute("select id from public.models order by sort, id limit 1").fetchone()
     if model_id is None:
@@ -811,7 +817,12 @@
     "public.claim_signup_grant(uuid,text,uuid)": SERVICE,
     # 0021:420-428 (D10): the signed-in consumer reads; the org resolver is nobody's
     "public.consumer_job_result(uuid)": BROWSER,
-    "public.consumer_jobs(text,integer,uuid)": BROWSER,
+    # 0024 (D10-APP-SQL): consumer_jobs gains four defaulted filters (0021's signature is
+    # dropped and recreated), and C0 WR-5's own-ledger page
+    "public.consumer_credit_ledger(text,integer)": BROWSER,
+    "public.consumer_may_create_key()": BROWSER,   # 0024: the api_keys INSERT predicate
+    "public.consumer_jobs(text,integer,uuid,text,uuid,timestamp with time zone,"
+    "timestamp with time zone)": BROWSER,
     "public.consumer_org()": NOBODY,
     "public.handle_new_user()": SERVICE,
     "public.is_operator()": BROWSER,
@@ -1000,7 +1011,9 @@
                 "kind", "acknowledged_at", "claimed_at", "available_at")]
             + [f"infrx.credit_holds.{c}:SELECT" for c in (
                 "request_id", "state", "reconcile_after")]
-            + ["infrx.stream_chunks.expires_at:SELECT", "infrx.job_results.request_id:SELECT"])),
+            + ["infrx.stream_chunks.expires_at:SELECT", "infrx.job_results.request_id:SELECT",
+               # 0024 (W5-F5 WR-W5F5-1): the CREDIT holds' state, with a monitor policy
+               "infrx.credit_wallet_holds.state:SELECT"])),
     },
 }
 _LOGIN_ATTRIBUTES = ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb", "rolcanlogin",
diff -ru a/tests/integration/test_harness.py b/tests/integration/test_harness.py
--- a/tests/integration/test_harness.py
+++ b/tests/integration/test_harness.py
@@ -208,6 +208,8 @@
         "0018_terminal_settlement.sql",
         # D10 (upload readiness, content lifecycle, read authority + the dedicated logins)
         "0019_upload_readiness.sql", "0020_content_lifecycle.sql", "0021_read_authority.sql",
+        # (W-DR1 v2 adds 0022/0023 here) D10-APP-SQL: the console read port
+        "0024_console_read_port.sql",
     ]
     assert files[0].parent == harness.MIGRATIONS_DIR
     digests = pgstate.migration_digests()
diff -ru a/tests/integration/test_run.py b/tests/integration/test_run.py
--- a/tests/integration/test_run.py
+++ b/tests/integration/test_run.py
@@ -870,8 +870,11 @@
 
     with patched(harness, run=fake_run, assert_ours=lambda name: name):
         detail = harness.provision_database()
-    assert len(issued) == 1, issued
-    argv = issued[0]
+    # 0024 (W-D10A-1): GoTrue's columns go onto the template first, so the copy has them
+    assert len(issued) == 2, issued
+    assert issued[0][-2:] == ["-c", harness.GOTRUE_COLUMNS] and \
+        issued[0][issued[0].index("-d") + 1] == harness.PG_TEMPLATE_SOURCE, issued[0]
+    argv = issued[1]
     assert argv[:3] == ["docker", "exec", "-i"], argv[:3]
     assert argv[3] == f"{harness.PREFIX}postgres"
     assert argv[4:8] == ["psql", "-U", harness.PG_ADMIN_ROLE, "-d"], argv[4:8]
```

Proof, with v2 applied in this worktree and then removed (`git checkout -- tests/integration`, confirmed clean):

| Command | Exit | Result |
|---|---|---|
| `INFRX_I3B_PG=d INFRX_D_TASK=revoke INFRX_D1_IMAGE=supabase …/python -m pytest -q -rs tests/integration/backend/recovery/test_restore.py` | 0 | 28 passed, 1 skipped (bk03 needs E2's stack): the same as with no wiring |
| `…/python -m pytest -q tests/integration/test_run.py` (layer 1) | 0 | 53 passed, including the pinned `provision_database` statements |
| Scratch script: on the D Supabase harness, add the GoTrue columns to the template the way `provision_database` does, then run `pgstate.apply_migrations`, `install_test_clock`, `seed_fixtures`, `run_role_matrix` and `catalog_objects`. This is E2's layer-3 RLS stage without E2's compose stack. | 0 | **886 rows, 2 failed**. E2-RLS-30 passed (rowcount 1). E2-RLS-20, 31 and 32 passed (42501). Every 0024 row passed. The two failures belong to 0022/0023, which W-DR1 v2 covers, not 0024: `E3B-RLS-infrx.jobs_result_expiry_guard()-service_role` (0022 revokes it) and `L3-LOGIN-infrx_runtime-functions` (0023). Two functions have no row, also 0022's: `fail_preparation(jsonb)` and `set_feature_flag(text,boolean,text,text)`. |

E2's own layer-3 run (`tests/integration/run.py --layer 3`, namespace e2) was **not run**. This lane does not own that namespace. The coordinator should run it on the merged SHA together with W-DR1 v2.

### Proposed ruling (replaces the key-insert sentence above)

> A browser `api_keys` INSERT requires, besides 0001's owner/creator check, a verified individual (the claim path's predicate: `verified_user` evidence and a live, non-empty email). A consumer wallet is not required, so verified pilot or invited owners keep creating keys. An unverified owner's keys are issued through the service/operator seams.

### Open issues (fix round)

- **Unverified owners.** The coordinator should confirm on hosted that no live owner with a null `email_confirmed_at` relies on browser key creation. This lane may not query the hosted database.
- **W-D10A-1 v2** must land with W-DR1 v2. Neither is applied here.
- **Pre-existing plain-image failures.** Both are unchanged: the W5 gate and the lifecycle replay.

### Remaining effort (fix round)

- **Lane:** 0 h, pending review.
- **Coordinator:** apply W-D10A-1 v2 with W-DR1 v2 and run layer 3 (about 0.5 h), plus the hosted owner check (about 0.25 h).
- **Estimate:** optimistic 0.5 h, likely 1 h, pessimistic 2.5 h. Confidence medium.
- **Basis:** both images and both mutant runs are green at `df21cfa6`. The E2 layer-3 proof is partial: the role matrix passed on the D harness, but E2's compose stack was not run.

## Verification log

- 2026-09-25: evidence written at code head `b6c01566`, and every command above with head `b6c01566` was run at that head. The Supabase skip marker for the plan case was committed by the parallel agent inside `d90933c8` and was NOT rerun on Supabase by this lane.
- 2026-09-26: appended "Concurrency and WR-W5F5-1". The final branch head is `12f92a6f` plus this evidence commit.
- 2026-09-26: appended "Fix round" (0-D10SQL-CM-1, 1-D10R-1, 1-D10R-2); code head `df21cfa6`, 0024 sha256 `8e0bfd62…94e0`; every command in that section was run at the head it names.
