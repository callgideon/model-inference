# LW1 integration — L2 on real rows, L1 on the one membership seam, H1's real port (lane lw1-integration)

- Branch `codex/w5-lw1-integration`, worktree `.claude/worktrees/codex-w5-lw1-integration`, base `fb9989ee`
  (tip + lab-access 93b0fb2c + lab-app 80dae52c). Code head `dd79f8cd`.
- Commits: `94ed729b` L2 db_now · `b299b703` L1 seam test (red) · `2c627848` 0030 + Lab switch + WR-L1-1..4 ·
  `8e6f0b72` H1/L2 dataset-gate tests (red) · `dd79f8cd` WR-H1-1 + async bind + mutants.
- Tasklocal: `l2` (PG 57501) for L2 and H1; `l4` (PG 57503) for 0030's proof. Never `dlab`. The D harness
  created and removed `infrx-l2-postgres` / `infrx-l4-postgres` per run; nothing else touched. No hosted
  service, box, AWS, Vercel or secret. 0030 is local-only (R151); nothing applied hosted.

## Changed paths
`apps/infrx-api/infrx/state/lab_access.py` (WR-L2-3 `db_now`, as the brief directs) ·
`apps/infrx-api/infrx/lab/access/{__init__,fakes}.py` · `apps/infrx-api/infrx/harnesses/replay.py` ·
`apps/infrx-api/tests/l/access/{test_access,worlds,mutants}.py` · `apps/infrx-api/tests/h/{test_harness,mutants,test_mutants}.py` ·
`apps/app/supabase/migrations/0030_lab_access_self.sql` (new) · `apps/infrx-api/tests/d/test_l2sql_self{,_mutants}.py` (new) ·
`apps/lab/lib/auth/memberships.ts` · `apps/lab/tests/l/shell/{access,guard}.test.ts`, `run-mutants.mjs` ·
`apps/lab/app/(provider)/page.tsx`, `apps/lab/.gitignore`, `apps/lab/.env.example` (new) · `Makefile` (lab-mutants line only).

## 1. L2 on real PostgreSQL (WR-L2-3, WR-LABSQL-3)
- `PgAccessStore.db_now()` = `select infrx.now()` (R7). The worlds already used v2 model UUIDs and
  `membership_rows()` for the provider name (WR-LABSQL-3): no change needed there.
- New PG mutant `pg_store_clock_is_the_server_clock` (`infrx.now()` -> `now()` in `db_now`), killed by the store-clock case.

| cmd (cwd apps/infrx-api) | exit | result |
|---|---|---|
| `INFRX_D_TASK=l2 uv run --frozen pytest -q tests/l/access/test_access.py` at base (red) | 1 | 10 failed (every pg case, `AttributeError: 'PgAccessStore' object has no attribute 'db_now'`), 13 passed |
| same after `94ed729b` | 0 | 23 passed (13 fake + 10 pg) |
| `INFRX_MUTANTS=all INFRX_D_TASK=l2 uv run --frozen pytest -q tests/l/access/test_mutants.py` (94ed729b) | 0 | 35 passed: 16/16 fake + 13/13 PG mutants killed, 3 checks, 3 self-tests |
| `INFRX_D_TASK=l2 uv run --frozen pytest -q tests/d/test_l2sql_units.py tests/d/test_l2sql_access.py tests/d/test_code_mutants_l2sql.py` | 0 | 43 passed (lab-sql's cases + 30 mutants green with db_now) |

Role matrix (unchanged from L2's fix round, now on the tip): 2 consumer orgs (CONSUMER_1 owned by BOTH,
CONSUMER_2 owned by CONSUMER_ONLY), 2 providers (NemoStation, Other Lab), BOTH in both products, direct DB
roles (anon + three `authenticated` sessions -> 42501; service_role via RPC).

## 2. L1: one membership seam (R156; WR-L1-5/7; WR-L1-1..4)
Decision: **migration 0030**, not the service-role path. Reasons: PostgREST exposes `public` only and
`infrx` must never be exposed (`apps/app/supabase/README.md:102`), so the service-role option also needs a
public wrapper; the App's pattern for a user's own read is a `public` SECURITY DEFINER RPC on `auth.uid()`
granted to `authenticated` (0021/0024 `consumer_*`), and its service-role key serves only `/admin`
(`apps/app/lib/supabase/admin.ts`); the Lab env example holds no service-role key. 0030 keeps it that way.

- `public.lab_provider_memberships()`: no argument; `infrx.lab_provider_memberships({user_id: auth.uid()})`
  (0027, THE read) filtered to current on `infrx.now()` with `ProviderMembership.is_current`'s predicate;
  returns only `{provider_org_id, provider_name, role}`; EXECUTE revoked from public/anon, granted to
  `authenticated` (service_role holds it by 0004's default and reads `[]`).
- `apps/lab/lib/auth/memberships.ts` calls `lab_provider_memberships` (was `lab_my_provider_memberships`);
  shape parsing unchanged. L1-X18 now pins the new name.
- WR-L1-1 `app/(provider)/page.tsx` (calls `requireProviderWorkspace`); WR-L1-2 `make lab-mutants` runs
  `tests/l/shell/run-mutants.mjs`; WR-L1-3 `.gitignore` `next-env.d.ts`; WR-L1-4 `.env.example` (no service-role key).

Proof on `l4` (`tests/d/test_l2sql_self.py`, 3 checks): lab-sql's L2-SQL seed + EARLIER (revoked a day
ago), LATER (granted tomorrow), LEAVING (revoked in an hour). For DEV, VIEWER, BOTH (dual), CONSUMER_1
(consumer only), the operator, EARLIER, LATER, LEAVING: session answer == `LabAccess.workspaces()` over
`PgAccessStore` == expected, three columns only; anon 42501, `authenticated` allowed, grantees exact;
LEAVING disappears from both reads exactly at `revoked_at` after `infrx_test.advance(3600)`.

| cmd | exit | result |
|---|---|---|
| `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_l2sql_self.py tests/d/test_l2sql_self_mutants.py -k "not killed"` (red, b299b703) | 1 | 3 checks failed `42883` (function absent); list check failed (no 0030 file) |
| `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_l2sql_self.py tests/d/test_l2sql_self_mutants.py` (2c627848) | 0 | 11 passed: 3 checks, list check, 7/7 SQL mutants killed |
| `cd apps/lab && pnpm test` after flipping the name in the tests (red) | 1 | 27 tests, 25 pass, 2 fail (L1-M01, L1-G03) |
| `make lab-test` | 0 | tests 27, pass 27, fail 0 |
| `make lab-lint` / `make lab-typecheck` | 0 / 0 | 0 problems / typegen + tsc clean |
| `make lab-build` | 0 | routes `ƒ /` (the provider home) and `○ /_not-found` |
| `make lab-mutants` (WR-L1-2 applied) | 0 | 27 cases all named; 50 mutants, 50 killed |
| `cd apps/app && pnpm install --frozen-lockfile && pnpm build` | 0 / 0 | App builds unchanged |

SQL mutants (0030): `self_revoked_listed`, `self_future_listed`, `self_process_clock` (`now()`),
`self_leaks_columns` (+user_id), `self_revoked_at_its_instant` (`<` -> `<=`), `self_anon_calls`
(`to anon, authenticated`), `self_runs_as_caller` (security invoker). First run: the anon mutant anchored on
the explicit `revoke ... from public, anon` survived (0004's default privileges already withhold anon); it was
re-anchored on the grant, which is the real defect.

## 3. H1: WR-H1-1 and the real-port case
- `LabAccess.authorize(gate, *, user_id, provider_org_id, dataset_ref)`: `Forbidden` unless the user is a
  current developer+ member AND every source of the dataset is under its grantor's current grant for the
  gate's purpose (F3 `lab.authorize`), on the store clock. The dataset's sources come from a new
  `DatasetSources` port (`uses(provider_org_id, dataset_ref) -> [DatasetUse(grantor_org_id, model_id,
  category)]`) that D7 must implement; until then `FakeDatasets` in both worlds. Default deny: no port, or no
  sources (unknown/foreign ref). The two-purpose gate (external_submission) is refused (no purpose parameter).
- `RightsPort.authorize` and `bind` are now `async` (the real port is async; a sync adapter would need
  `asyncio.run` inside a server loop). `LabAccess` satisfies `RightsPort` directly; no adapter class.
- tests/l/access: `test_lab_access__a_dataset_passes_a_gate_only_under_every_sources_current_grant`
  (fake + pg). tests/h: `test_h1_binding_asks_the_real_l2_port` (pg, the L2 world via
  `tests.l.access.conftest`'s fixtures): BOTH binds; A's viewer and B's developer are Forbidden; after the
  grantor revokes, BOTH is Forbidden.

| cmd (cwd apps/infrx-api) | exit | result |
|---|---|---|
| `INFRX_D_TASK=l2 uv run --frozen pytest -q tests/l/access/test_access.py -k dataset` (red, 8e6f0b72) | 1 | 2 failed (fake, pg) |
| `INFRX_D_TASK=l2 uv run --frozen pytest -q tests/h/test_harness.py` (red, 8e6f0b72) | 1 | 4 failed (3 bind cases: `bind` not awaitable; real-port case), 18 passed |
| `INFRX_D_TASK=l2 uv run --frozen pytest -q tests/h/test_harness.py tests/l/access/test_access.py` (dd79f8cd) | 0 | 47 passed |
| `INFRX_MUTANTS=all INFRX_D_TASK=l2 uv run --frozen pytest -q tests/h/test_mutants.py` | 0 | 35 passed: 27/27 fake + 2/2 PG mutants killed, 3 checks, 3 self-tests |
| `INFRX_MUTANTS=all INFRX_D_TASK=l2 uv run --frozen pytest -q tests/l/access/test_mutants.py` | 0 | 43 passed: 20/20 fake + 17/17 PG mutants killed, 3 checks, 3 self-tests |
| `uv run --frozen ruff check infrx/lab infrx/harnesses infrx/state/lab_access.py tests/l tests/h tests/d/test_l2sql_self*.py` | 0 | all checks passed |

New mutants: L2 `dataset_first_source_only`, `dataset_grant_of_the_first_grantor`,
`dataset_without_sources_passes`, `dataset_gate_ignored` (+ their `pg_` copies); `clock_is_the_process_clock`
now 4 occurrences. H1 anchors moved for `await` (`h1_bind_skips_the_rights_port` = `None and await …`,
`h1_bind_forwards_no_principal` on `lab.Gate.schedule, user_id=user_id,`); PG list
`pg_h1_bind_skips_the_rights_port`, `pg_h1_bind_forwards_no_principal` (runner `-m pg`, env `INFRX_D_TASK`);
the fake runner now runs `-m "not pg"`.

## make api-test (INFRX_D_TASK=l2)
| cmd | exit | result |
|---|---|---|
| `INFRX_D_TASK=l2 make api-test` (dd79f8cd, 42m31s) | 2 | 4871 passed, 56 skipped, 9 xfailed, **9 failed** |

- 2 = WR-LW1I-2 (the enumerated EXECUTE surface does not list 0030's function yet):
  `tests/d/test_credit_schema.py::test_credit_privileges__service_reads_money_and_writes_through_seams`,
  `tests/d/test_schema_postgres.py::test_dur_rls__the_execute_surface_is_enumerated`, both
  "authenticated may execute public.lab_provider_memberships()". With the WR-LW1I-2 patch applied locally:
  `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_credit_schema.py tests/d/test_schema_postgres.py`
  -> exit 0, 40 passed; patch reverted (checks.py is not this lane's).
- 7 = `tests/d/test_outbox_relay.py[valkey]`: `HarnessBusy: another run holds /tmp/infrx-d2-valkey-55463.lock`
  (another lane's shared d2 Valkey lock; the Valkey harness has no per-key port); nothing in this diff touches it.
- The migration pin (`tests/integration/test_harness.py`, outside api-test) needs WR-LW1I-1.

## Wiring requests
- **WR-LW1I-1** `tests/integration/test_harness.py:222` migration pin: add `"0030_lab_access_self.sql",` after
  0028 (and D7's 0029 before it when D7 merges; `# Lab (local-only, R151): LW1 integration (R156 session door)`).
- **WR-LW1I-2** `apps/infrx-api/tests/d/checks.py` `EXPECTED_FUNCTION_CALLERS`, after `consumer_may_create_key()`:
  ```
      # LW1 integration (0030, Lab, local-only): the Lab session's own current memberships
      # (auth.uid(); R156's one read); the platform role reads [] (no auth.uid()).
      "public.lab_provider_memberships()": {"authenticated", "service_role"},
  ```
  Proof: with it `INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_credit_schema.py` 21 passed; without it
  `test_credit_privileges__service_reads_money_and_writes_through_seams` fails
  ("authenticated may execute public.lab_provider_memberships()"). Applied locally, reverted.
- **WR-LW1I-3** `Makefile:21` api-mutants: append `tests/l/access/test_mutants.py` (WR-L2-2, still absent; before
  `tests/d/test_migration_mutants.py`) and `tests/d/test_l2sql_self_mutants.py`; run with `INFRX_D_TASK=<key>`.
- **WR-LW1I-4 (to lab-sql, D7)** implement `infrx.lab.access.DatasetSources.uses(provider_org_id, dataset_ref)`
  over D7.a's source/grant refs: the provider's own dataset's `(grantor_org_id, model_id, category)` per source,
  none for a foreign or unknown ref. F3's `Sample` carries `grant_ref` only (no model or category), so D7.a must
  store them per source.
- **WR-LW1I-5 (optional)** register the `pg` marker once in `pyproject.toml` (`tests/h` alone warns
  `PytestUnknownMarkWarning`; tests/l/access/conftest registers it only when collected).

## Proposed rulings (coordinator numbers)
- R156 amendment: "The Lab App reads its session's memberships through `public.lab_provider_memberships()` (0030),
  a no-argument `authenticated` door over lab-sql's `infrx.lab_provider_memberships` for `auth.uid()`, current on
  `infrx.now()`, three columns. It answers exactly `LabAccess.workspaces()` for that user
  (`tests/d/test_l2sql_self.py`). The Lab holds no service-role key."
- WR-H1-1: "`LabAccess.authorize` is H1's `RightsPort`: current developer+ membership AND, for every source of the
  dataset (D7's `DatasetSources`), the grantor's current grant for the gate's purpose, on the store clock; default
  deny. `bind` is async."

## Open issues
- Dataset sources are fake in both worlds until D7 lands (WR-LW1I-4); the membership and grant halves are real.
- external_submission through `authorize` is always refused (no purpose parameter); add one when J/X need it.
- WR-L1-6 (Lab sign-in / callback / middleware) is still unassigned: no Lab session can be established yet.

## Estimate (remaining, to merge)
optimistic 0.5 h / likely 1.5 h / pessimistic 4 h, confidence medium. Basis: code, tests and mutants are
green; remaining = the coordinator wirings WR-LW1I-1..3 and one verify round (47-234 min, session-03 analogue).
Spent ~2.5 h.

## Audit log
- 2026-09-27: evidence written for `dd79f8cd` (lane lw1-integration, LW1).
