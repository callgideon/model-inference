# api-schema-3 (wave 7 batch 3, AP-00): 0068 LOCAL-ONLY - handback at fc63417

- Lane / branch / worktree: api-schema-3, `codex/w7-api-schema-3`, `.claude/worktrees/codex-w7-api-schema-3`
- Base: 49114933 (merges #94-#97). Code head: fc634170. Key: `ap0` (PostgreSQL 57550; container `infrx-ap0-postgres`, created and removed per run by `tests/d/pgharness.py`).
- Images: plain `postgres:16` (harness digest) and `INFRX_D1_IMAGE=supabase` (PostgreSQL 17.6).
- Nothing hosted, no box, no AWS/SSM/S3/Vercel, no secrets. Hosted schema stays at 0059; `infra/rollout/hosted-migrate.sh` untouched (R269: the window edit comes with the window). No switch added; nothing enabled.

## What 0068 carries (`apps/app/supabase/migrations/0068_wave7_followups.sql`)

| Item | Source | Content |
|---|---|---|
| SR-AP06-1 | register row 91; `w7/api-publication-5be6dbb.md` | `infrx.lab_control_dev_keys` (the provider's own DEV endpoint's keys, oldest first, never the hash; foreign provider or prod endpoint `not_found`), `infrx.lab_control_revoke_dev_key` (one-way through 0009's `revoke_key` + a `lab_dev_key_revoke` control event, both once; a replay answers the first `revoked_at`; the audit idempotency key is scoped `lab_dev_key_revoke:<key id>:<key>` because 0009's unique index is global), `infrx.lab_control_dev_wallet` (`{opened, balance}`: available CREDIT = ledger - reserved, exact text). EXECUTE: service_role (0004 default) + `infrx_lab_control`. 0032's `lab_control_events` action check admits `lab_dev_key_revoke`. |
| SR-AP10C-1 | row 91; `w7/api-improve-2-837cbb2.md` | `infrx_lab_datasets`: the datasets worker's own NOLOGIN, NOINHERIT role (0021's shape; the operator gives it a login from the secret store), member of nothing. EXECUTE on exactly its three passes' functions (22: 0060's pending/lease/advance/finish, L2 memberships/grants, D7 source/publish/resolve/accessible samples, 0051's claim/heartbeat/finish/job, 0041's tombstone/bound/blocked/permitted + content-ref issue/redeem, D6F `request_feedback`, `infrx.now()`), SELECT on `serving_versions(serving_version_id, model_id)` under its own policy (pg_ports.model_of unchanged). |
| artifacts-role grants | row 91; `w7/api-artifacts-2-4a5a257.md` | none: the artifacts role runs on the control login (proven there by api-artifacts-2); it has no login of its own, so the request's condition does not hold. |
| row 94 (E4C run 2) | `evidence/e/E4C-e6a8b40/README.md` | `jobs_content_unscrubbed_idx on infrx.jobs (request_id) where request_record is not null and content_scrubbed_at is null` (0020's scrub sweep job branch); `journal_bytes_charged()` re-created as two aggregates (stored bytes on 0011's `jobs_journal_stored_idx` + each live journal reservation's excess over its job's stored bytes on 0003's active-reservation index); identity `greatest(r, s) = s + greatest(r - s, 0)`. |

Decision recorded (proposed ruling below): the datasets grants go to a role of their own, not the control login. Measured with `track_functions = all` over the from-traces, import and lineage passes on ap0: on the control login the pass additionally needs `lab_bound_samples` (R251 keeps it off the control login: `test_code_mutants_lw8.py` comment), `lab_register_source` and `request_feedback` (any org's feedback rows). Granting those to the web-facing login would undo R251's split; the per-role role is WR-LDP-7's shape (register row 19).

## Red before green (seam tests first)

| Run | Exit | Outcome |
|---|---|---|
| `INFRX_D_TASK=ap0 pytest -q tests/d/test_upgrade_0068_mutants.py -k 'not sql_mutant'` before 0068 (slice 1) | 1 | 6 failed: no 0068 file (FileNotFoundError), doors absent |
| same, slice 2 checks before the role | 1 | 3 failed: `role "infrx_lab_datasets" does not exist` (the pass's login refused: FATAL) |
| same, slice 3 checks before the index/body | 1 | scrub sweep plan `Seq Scan on jobs j` with seq scans priced out; journal check ValueError (no 0068 body) |
| `pytest tests/d/test_control_ops_upgrade.py tests/d/test_upgrade_lab.py tests/i/... tests/integration/test_harness.py -k migration` with 0068 present, pins unmoved | 1 | rehearsal (range 0060-0067), lab upgrade (`now()` ACL moved), known-good / lab-rollout pins, harness pin red |

## Checks at fc63417

| Command (apps/infrx-api unless noted) | Exit | Outcome |
|---|---|---|
| `INFRX_MUTANTS=all INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_upgrade_0068_mutants.py tests/d/test_code_mutants_lw8.py` (plain) | 0 | 54 passed: 0068 8 checks + 28 SQL mutants killed + 3 guards; lw8 checks + mutants with the three doors added |
| `INFRX_MUTANTS=all INFRX_D1_IMAGE=supabase INFRX_D_TASK=ap0 … tests/d/test_upgrade_0068_mutants.py` | 0 | 39 passed (8 checks, 28 killed, 3 guards) |
| `INFRX_D_TASK=ap0 … pytest -q tests/d tests/l3sql tests/i/test_known_good_proof.py tests/i/lab/test_lab_rollout_steps.py tests/i/test_migrate.py -k 'not mutant_is_killed'` (plain, before the allowlist fixes) | 1 | 930 passed, 1 skipped, 8 xfailed, 8 failed: 5 were 0068's pin moves (lw8 exact set, d1r inventory, d6f/l2 grantee allowlists, d4 mutant on the superseded 0011 body) - fixed in slice 4; 3 pre-existing (below) |
| same on `INFRX_D1_IMAGE=supabase` | 1 | 920 passed, 18 failed: the same 8 + 10 role-switch failures (`permission denied to set role "infrx_runtime"` class) |
| the 10 Supabase-only failures at the BASE 49114933 (detached worktree, same venv, supabase image) | 1 | the same 10 fail - pre-existing on that image (api-schema-2 recorded 7 of them; lc2's 3 are the same class) |
| the 8 files touched in slice 4, `-k 'not mutant_is_killed'`, plain | 0 | 74 passed (rehearsal 0060-0068, lab upgrade, lw8, d1r, d6f, l2sql, migration-mutant guards, 0068 checks) |
| same on the Supabase image | 0 | 74 passed |
| `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py -k "hosted_migrate_pending_short or anchor_stale or post_check_stale or well_formed or every_case"` | 0 | 5 passed (pin mutants killed) |
| `INFRX_MUTANTS=all pytest -q tests/i/lab/test_mutants.py -k "r151_post_check_file_name or well_formed or every_case"` | 1 | 2 passed; `r151_post_check_file_name` broken_runner because the pristine tree fails `test_ldp__each_role…` - identical at the base (pre-existing) |
| `pytest -q tests/i/test_known_good_proof.py tests/i/lab/test_lab_rollout_steps.py` | 1 | 34 passed; 1 pre-existing (`test_ldp__each_role_the_step_enables…`, fails at the base) |
| repo root: `pytest -q tests/integration/test_harness.py -k 'migration or filename_order'` | 0 | 1 passed (pinned list 0060…0068, the 0067 "optional" escape removed) |
| repo root: `pytest -q tests/integration/test_harness.py` | 1 | 47 passed; `test_nothing_in_this_directory_points_at_production` fails identically at the base |
| repo root: `tests/integration/test_makefile_mutant_lists.py` | 1 → 0 | 7 failed without WR-AS3-1 (new runner unlisted); 7 passed with it applied transiently (reverted) |
| `make api-lint` | 0 | All checks passed (ruff 0.15.12); `uvx ruff@0.15.12 check tests/integration/test_harness.py` clean |
| `make api-typecheck` | 0 | pyright 457 errors (baseline 458); 0 in touched files |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (916 links, 502 documents) |

Pre-existing at the base, not this lane: `test_operations_pg::test_api_ops__the_operations_service_runs_on_the_postgres_adapters` (`replayed True != False`), `test_upgrade_0065_mutants::check_the_identity_doors_answer_as_the_platform_sql` (`infrx.console.session` no longer has `ACCOUNT` since b9c70599, api-identity-2 wirings), `test_lab_rollout_steps::test_ldp__each_role_the_step_enables…` (the `artifacts` role's line in `50-lab-role.sh`), the harness production check, the 10 Supabase role-switch checks.

## Rehearsal moves (`tests/d/test_control_ops_upgrade.py`)

0068 joins MINE (rolled back first). Admitted moves: `REGRANTED_0068` (18 existing functions gain exactly `infrx_lab_datasets=X/postgres`; the control login gains nothing there), `COLUMNS_0068` (two serving_versions columns `infrx_lab_datasets=r`), `REDEFINED` + `journal_bytes_charged()` (rollback restores 0011's body = the 0059 body), DROPPED + the three SR-AP06-1 doors, range 0060-0068. Rollback restores existing column ACLs exactly; re-forward is the same snapshot; an operation starts.

Slice-4 edits outside the four named pin files, all under `tests/d/` and forced by 0068's grants (the R251 pin moved here rather than by wiring request, unlike 0066): `test_code_mutants_lw8.py` GRANTED + the three doors; `checks_credit.D10_ROLES` + `infrx_lab_datasets=` (its surface is asserted exactly by 0068's ROLE check); `test_l2sql_access.py` / `test_d6f_feedback.py` grantee allowlists name the role for the two L2 reads and `request_feedback` only; `migration_mutants.py` / `test_migration_mutants.py`: `d4_stored_counted_beside_the_reservation` moved (0011's body is superseded) - its replacement is `ap0068_charged_reserved_and_stored`.

## Mutant notes

- Not mutated (asserted only): the datasets role's `nologin noinherit`. A role is the cluster's; a mutated `create role` is skipped by the role an earlier database in the same container created (the 0021 `if not exists` shape is kept so a re-run never takes back the operator's login).
- `journal_bytes_charged()` equality: 16 combinations (stored 0/40/100/150 × none / active / released / another kind's reservation) plus an orphan reservation, the installed body vs 0011's text over temp tables; the plan (seq scans priced out) must use `jobs_journal_stored_idx` and no Seq Scan - 0011's body uses a full `jobs_settlement_target_key` scan there.
- `ap0068_charged_unindexed` (dropping the `> 0` predicate) is equal in value and killed by the plan assertion.

## Wiring requests

- **WR-AS3-1** `Makefile` api-mutants, the ap0 0065/0066 line gains `tests/d/test_upgrade_0068_mutants.py` (patch: `api-schema-3-fc63417-WR-AS3-1.patch`). Composed test: `tests/integration/test_makefile_mutant_lists.py` (7 passed with it).
- **WR-AS3-2** (follow-up for api-publication): the `PgDevCredentials` adapter over the three doors (`DevCredentials` in `operator_publication.py`); `balance` is text - build `Money{amount, unit:"CREDIT"}` from it.
- **WR-AS3-3** (box, with WR-LDP-7's E4-ON o03): the datasets role on a login of its own. `infrx.lab.workers` builds `connector(LAB_DATABASE_URL)` with the default `set_role=None`, which on a direct (non-pooler) DSN runs `set role service_role` - a dedicated login is not a member and fails; on the :6543 pooler it does nothing. Either keep the pooler DSN (as `08-lab-internal-testing-rollout.md` §3 states) or pass `set_role=False` for role logins.

## Schema requests

None outstanding from this lane.

## Proposed ruling (unnumbered)

A Lab worker role's database grants go to a role of its own (NOLOGIN in the migration, login by the operator from the secret store, member of nothing), holding exactly the functions its passes call (measured), never to the web-facing control login; the control login keeps route halves only (R251).

## Open

- Hosted apply of 0060-0068 is the coordinator's R151 window (EXPECTED_PENDING edit + KNOWN-GOOD reproof + operator window); the index is a plain `create index` (SHARE lock on `infrx.jobs` for its build - milliseconds at ~13k rows).
- The import and lineage passes on `infrx_lab_datasets` are granted from the measurement and the adapters' calls; only the from-traces pass is proven end to end on the role (the other two on the owner login, by their own suites). E4-ON o03 on the role logins is WR-LDP-7's.
- `register_existing_database_content`'s `job_results` branch (`scrubbed_at is null`) was not named by E4C and is untouched.

## Estimate (remaining for this lane)

optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium. Basis: 0068, its proofs on both images and the rehearsal are done (~5 h of 3/5/9); left: WR-AS3-1 at merge, one review round, a rehearsal rerun if another wave-7 migration merges first (~3 min per image).
