# D5 — terminal transaction, grants and reconciliation

| Field | Value |
|---|---|
| Task | D5 (track D, durable state), "Terminal transaction, grants and reconciliation". Oracles DUR-SETTLE, DUR-CAP, DUR-OUTPUT, CREDIT-SPEND, CREDIT-RATE; also touched: DUR-FENCE, API-OPS, DUR-RLS; R91 (the replay lookup, coordinator follow-up) |
| Status | **implemented**: real PostgreSQL on both images (pinned `postgres:16.14` + shim, and `supabase/postgres` 17.6.1.173 without it). Not integrated: no coordinator merge, nothing applied to any hosted Supabase project, never the pilot box. Owner: the Opus D5 implementer, re-dispatched after the first D5 agent died (its one commit, `1d5c66e`, is kept) |
| Base SHA | `e2a52b2` (the integration head at dispatch, addendum 5: contains D4, the F2P wire-in `6a49af5` and the cancel-cause merge). Merged since, as the coordinator instructed: `origin/claude/backend-impl` `d486142` (G2 `2391d4d`: R91 port/fakes/case, relay; W4; G4U) via `f2ee977` (`--no-ff`) |
| Implementation SHA | `4bcac3b` (commits `1d5c66e` … `4bcac3b`, per item below). This evidence commit follows it; the head SHA is in the handback |
| Branch / worktree | `codex/d5-terminal-transaction` in `.claude/worktrees/codex-d5` |
| Classification | local only. Task-local Docker created and removed by this checkout's harness: `infrx-d5-postgres[-supabase]` on 55436, `infrx-d5-valkey` on 55467, the `test_pgharness` decoy `infrx-d5-dharness-postgres` on 55476; Q's harness on 55498 (no pre-started container). No cloud, hosted project, pilot host or paid provider; nothing pushed |

Merge `f2ee977` conflicted in ONE file, `tests/d/test_jobstore_conformance.py` (the partition).
Both semantics were kept: D5's lifted partition (every `_D5`/`RACY`/cancel-cause case strict),
plus G2's new R91 case, which stayed pending on D5 with the exception it dies of until
`fcaa979` implemented it and lifted the line.

## What was built

Every migration mutant below is a single edit of 0018 (or of the file named), scenario
`admission`, killed on **both images** by an `AssertionError` raised in the named check
(R40; the quoted `-s` runs are under "Commands and results"). Code mutants go through the one
shared runner (`tests/contracts/mutants.py` `Runner`, R83) over `tests/d/test_settle_units.py`
and `tests/d/test_operations_units.py` (no Docker).

| Item | Where | Commit(s) | Killing test (named) | Mutants (all killed) |
|---|---|---|---|---|
| (1) legacy USD settlement | 0018: `infrx.terminalize` (argument checks → replay → fence → validation → ONE settling UPDATE → reservations, attempt, tombstone, projections → money), `settle_legacy_usd`, `debit_legacy_usd`, `usage_doc`, `cause_carries_state`, jobs columns `proposal`/`usage_prompt_tokens`/`usage_completion_tokens` + `jobs_settled_usage_is_one_fact` + `jobs_settlement_record_guard`; `job_admission` outcome gains `usage`; `PgJobStore.complete` | `e96449e`, `a4ea315` (WIP), `9d0b24b` | `test_settle.py::test_dur_settle__one_winner_exact_decimals_then_replay`, `__a_different_proposal_after_terminal_is_already_terminal`, `__success_needs_this_jobs_stored_result`, `__only_three_causes_charge_and_only_with_usage`, `__over_envelope_is_a_free_platform_error`, `__published_without_usage_is_held_unknown`, `__everything_released_in_the_settling_transaction`, `__timestamps_are_the_db_clock`, `__the_terminal_event_is_the_triggers_one` | 26 migration: `d5_replay_after_the_fence`, `d5_settle_before_the_fence`, `d5_result_ref_unchecked`, `d5_foreign_result_ref_accepted`, `d5_disconnected_not_billable`, `d5_sync_deadline_billed`, `d5_engine_incomplete_is_a_success`, `d5_debit_rounds_down`, `d5_debit_rounded_twice`, `d5_debit_above_hold`, `d5_over_envelope_charged`, `d5_wallet_total_written_directly`, `d5_usage_debit_without_ledger_row`, `d5_hold_not_moved_on_settle`, `d5_reservation_kept`, `d5_attempt_kept`, `d5_tombstone_not_started`, `d5_projection_missing`, `d5_cost_is_not_the_debit`, `d5_result_retention_not_set`, `d5_usage_created_at_now`, `d5_ledger_created_at_now`, `d5_unknown_usage_released`, `d5_window_from_the_callers_clock`, `d5_second_terminal_event`, `d5_settled_usage_rewritable`. Code: `complete_raises_the_refusal_inside_the_transaction`, `the_proposal_is_not_sent`, `the_default_result_ttl_is_sent`, `the_default_tombstone_ttl_is_sent`, `complete_settles_in_the_credit_regime` |
| (2) CREDIT settlement + WorkV2 (D half) | 0018: `settle_credit` (hold `held → settled` first, then the `inference_debit`), `debit_credit` (the ADMITTED card by its pinned version), `claim` = 0016's body minus MY-3, `load_work_credit` (fenced; the admitted pins, card, `DataAccessPolicyRef`, prompt count); `PgJobStore.complete_credit`, `.load_work_credit`; `checks_leases.credit_running` uses the real `claim` | `8834fbe` | `test_settle.py::test_credit_spend__settles_on_the_credit_wallet_at_the_admitted_card`, `__sql_settle_equals_v2_settle_on_the_grid`, `__usd_wallet_untouched`, `__regimes_never_cross`, `test_credit_rate__a_card_published_after_admission_is_ignored`, `__retired_wallet_still_settles` | 11 migration: `d5_credit_debit_before_hold`, `d5_credit_settles_at_the_active_card`, `d5_credit_debits_the_usd_wallet`, `d5_credit_debit_rounds_down`, `d5_frozen_wallet_refuses_its_own_debit` (0015), `d5_charged_credits_not_recorded`, `d5_credit_v1_debit_nonzero`, `d5_regimes_cross`, `d5_load_work_credit_reads_current_pins`, `d5_load_work_credit_current_policy`, `d5_claim_refuses_credit_again`. Code: `complete_credit_settles_in_the_legacy_regime`, `a_settlement_for_every_outcome`, `the_charge_is_the_v1_debit`, `load_work_credit_serves_a_legacy_job`, `load_work_credit_policy_rewritten` |
| (3) cancel cause | 0018 `infrx.cancel` (0016's body + `v_cause := coalesce(p_args->>'cause', 'client_cancelled')`, checked BEFORE the lookup); `PgJobStore.cancel(..., cause=)` sends it (the pre-0018 Python refusal is gone) | `9d0b24b` | `test_settle.py::test_dur_settle__cancel_records_its_cause_and_bills_by_r21`, `__an_unknown_cause_is_refused_and_changes_nothing`; conformance `dur_settle__cancel_records_its_cause_and_settles_by_r21` and `credit_settle__cancel_records_its_cause_and_settles_by_r21` strict on PostgreSQL | 4 migration: `d5_cancel_cause_ignored`, `d5_cancel_accepts_any_cause`, `d5_cancel_cause_checked_after_the_lookup`, `d5_sync_deadline_released_free` (0016 `terminalize_no_usage`). Code: `the_cause_is_not_sent` |
| (4) operator money | 0018 `infrx.grant_credit` (0004 stub filled: `operator_adjustment` consumer ≠ 0 / `operator_allocation` provider_dev > 0, amount as exact CREDIT text, wallet `FOR UPDATE` only, replay by `operation_id`, never below reserved, one `admin_adjust` audit row keyed `grant_credit:<op>`), `infrx.reconcile` (tenant-bound, DB clock, D3's `release_aged_unknown`, `admin_reconcile`, replay by `reconcile:<op>`); `PgLedger.adjust`/`.reconcile` | `b1aa41d` | `test_operations_pg.py::test_credit_spend__adjust_is_audited_idempotent_and_never_below_reserved`, `__allocation_only_to_provider_dev_wallets`, `test_dur_settle__reconcile_waits_for_the_db_clock_and_never_debits`, `__reconcile_is_tenant_bound` | 9 migration: `d5_adjust_replay_appends`, `d5_adjust_without_audit`, `d5_adjust_below_reserved`, `d5_allocation_to_consumer_wallet`, `d5_amount_not_bounded`, `d5_reconcile_on_callers_clock`, `d5_reconcile_debits`, `d5_reconcile_any_tenant`, `d5_reconcile_replay_audits_again`. Code: `adjust_allocates` |
| (5a) `assert_no_drift` | `checks_settle.assert_no_drift(conn)`: both detectors (`infrx.wallet_reconciliation`, `infrx.credit_wallet_reconciliation`) return no rows; called by every settle, cancel, adjust, reconcile, race and lookup check | `9d0b24b` | every check above | `d5_hold_not_moved_on_settle`, `d5_usage_debit_without_ledger_row` (killed by the drift assertion first, quoted) |
| (5b) the released record | 0018 `release_aged_unknown` answers `[{"released": outcome}]`; `PgJobStore.recover` keeps returning the outcome and names the job in `store.released` for that sweep | `9d0b24b` | `test_settle.py::test_dur_settle__a_24h_release_is_reported_as_released_not_as_a_new_terminal` | `d5_release_reported_as_outcome`. Code: `a_release_read_as_a_terminalization`, `released_never_cleared` |
| (6) races | `test_settle_races.py` (lock-step both orders + barrier stress; 9 test items) and `checks_settle.check_settle_races` | `315710d` | `test_race__a_duplicate_completion_debits_once_and_replays`, `__cancel_and_complete_have_one_winner[client_cancelled/client_disconnected/sync_deadline]`, `__the_reaper_skips_a_settling_job_and_never_reterminalizes_it`, `__reconcile_and_the_reaper_release_once_never_a_debit`, `__two_settlements_and_an_adjustment_on_one_credit_wallet`, `__a_stale_generation_after_a_requeue_cannot_settle`, `test_races__the_mutants_concurrency_check` | `d5_settle_without_the_row_lock`, `d5_takes_the_scope_lock`. **Not written: `d5_wallet_locked_before_the_hold`** - equivalent (see Limits 6) |
| (7) G6B adapters | `infrx/state/operations.py`: `PgTenantStore`, `PgAuditLog`, `PgRegistry`, `PgAccountView`, `PgLedger` (A1's `PgSignup.grant_initial`, unchanged, + item 4), and `PgWalletDirectory` (the unassigned v2 port, built because `Operations` needs one) | `3585f67` (WIP), `74c9975` | `test_operations_pg.py::test_api_ops__tenant_keys_are_tenant_scoped_one_row_and_revoked_once`, `__suspension_is_one_audited_code`, `__the_audit_log_appends_once_and_answers_only_its_key`, `__registry_rows_are_immutable_and_the_alias_moves`, `__a_tenant_reads_its_own_usage_per_unit_and_its_credit_holds`, `__the_ledger_port_adjusts_reconciles_and_grants_through_d5_and_a1`, `__the_operations_service_runs_on_the_postgres_adapters` (G6B's `service.Operations` built from the Pg adapters only), `__a_partial_publication_is_unreachable_and_rerunnable`; `test_operations_units.py` (6, no Docker) | Code: `key_lookup_ignores_org`, `insert_key_duplicates`, `insert_key_reports_every_call_written`, `revoke_rewrites_revoked_at`, `suspension_lift_suspends`, `audit_lookup_by_any_key`, `usage_totals_merge_units`, `holds_read_usd_as_credit` |
| (8) `PgCatalogDirectory` | `infrx/state/catalog.py` (one statement per lookup over a `Connect`: a fresh connection per call, so `pilot.Probe`'s own thread and loop work) | `d4444a7` | `test_catalog_pg.py::test_credit_rate__private_is_none_to_everyone_else`, `__unpriced_answers_none`, `__alias_move_changes_resolve_not_an_admitted_job`, `__a_lookup_from_a_fresh_thread_and_loop_is_answered`, `__a_database_error_is_raised_not_answered_none`; the 32 v2 cases on `V2Harness(catalog=PgCatalogDirectory)` (1 partitioned) | Code: `private_visible_to_consumer`, `card_not_effective_checked`, `errors_become_none` |
| (9a/b) v1 conformance partitions | `test_jobstore_conformance.py` (PENDING = the F2 case only; no RACY), `test_streamstore_conformance.py` (PENDING `{}`) | `a4ea315`, `9d0b24b`, `f2ee977` (merge), `fcaa979` | the suites (partitions below) | - |
| (9c) CREDIT JobStore conformance | `pgtesting.make_credit_jobstore_factory`, `seed_credit_world`, `register_credential`, `credit_hooks`; `test_credit_jobstore_conformance.py` | `65aab20` | the 8 `credit_jobstore_cases()` (partitions below) | - |
| (9d) E3B drills | `test_e3b_drills.py`: dr07 verbatim from `tests/integration/backend/test_drills.py` (`codex/e3b-backend-gate` `5e2417b`) | `65aab20` | `test_e3b_dr06…`, `test_e3b_dr07…`, `test_e3b_dr09…` - all pass | their invariants' mutants are items 1/3's |
| (9e) Q3 rig on PostgreSQL | `pgtesting.JobsView`, `pgtesting.PgDispatchOutbox` (D2's outbox functions + `unacknowledged`/`last_error`/`deliveries`/`snapshots`/`ack_faults`), the factory's owner `conn`; the `q3rig.world` diff (Q's file) applied, run, reverted | `1107079` | `tests/q/test_reconcile.py` with the diff (results under integration request 6) | - |
| (9f) seams | `credit_schema.SEAMS` += `grant_credit`, `reconcile`, `load_work_credit`, `idempotency_lookup`; `checks.FILLED_RPCS` += `grant_credit`; `checks_credit.FILLED_BOUNDARIES` += `grant_credit` (D1R's named-boundary assertion lets its filled body move) | `65aab20`, `7d0c7a7` | `test_credit_schema.py::test_d1r_leaves_the_0001_0005_schema_unchanged`, `check_seams` | - |
| (10a) privileges | 0018 grants: the service operations `service_role` only; helpers revoked from everyone | `b1aa41d`, `fcaa979` | `test_operations_pg.py::test_privileges__d5_operations_service_only_helpers_nobody` | `d5_reconcile_granted_to_anon`, `d5_grant_credit_granted_to_authenticated`, `d5_settle_helper_callable_by_the_service` |
| (10b) supersession guard | `migration_mutants.superseded()`; 20 D3 mutants moved to 0018 (3 cancel, 14 claim, 3 unknown release); `d3_claim_leases_a_credit_job` retired with MY-3 | `9d0b24b`, `8834fbe` | `test_migration_mutants.py::test_no_mutant_anchors_in_a_superseded_function_body` (+ its control that one cancel mutant left on 0016 fails it) | the 20 re-anchored D3 names (quoted run) |
| (10c) header | 0018 header: lock order (continuing 0011/0016/0017) and rollback | `9d0b24b`, `b1aa41d` | documentation | - |
| (10d) ALWAYS | `test_migration_mutants.ALWAYS` += 27 D5 money-path names | `906d963` | the default mutant subset | - |
| R91 lookup (coordinator follow-up) | 0018 `infrx.idempotency_lookup` (STABLE, read-only over D2's mapping); `PgJobStore.lookup` | `fcaa979` | `test_settle.py::test_dur_admit__lookup_reads_the_mapped_job_and_writes_nothing`; conformance `dur_admit__lookup_reads_the_mapped_job_and_writes_nothing` and `credit_admit__lookup_answers_the_pinned_admission` strict | 4 migration: `d5_lookup_any_payload`, `d5_lookup_expired_answers`, `d5_lookup_active_mapping_expires`, `d5_lookup_any_org_scope`. Code: `lookup_crosses_regimes`, `lookup_sends_the_default_ttl`, `lookup_drops_the_outcome` |
| D4 verifier N1/N2 (addendum 5) | `journal.py` `append` docstring and `JournalWriteFailed` name the lone-surrogate refusal; the positive unit append fails with the test's own reason | `1d5c66e` | `test_journal_units.py` | - |

Totals: **60** D5 migration mutants (`D5_MUTANTS`), **28** D5 code mutants
(`code_mutants_d5.py`), **20** D3 mutants re-anchored on 0018; retired: `d3_claim_leases_a_credit_job`
(its invariant, MY-3, is deliberately lifted; control `d5_claim_refuses_credit_again`) and two D3
code mutants that exercised the old `NotImplementedError` of `complete`.

Commit-message corrections (history is not amended): `9d0b24b`'s subject says "37 D5 migration
mutants"; the list at that commit has **31** (`git show 9d0b24b:…/migration_mutants.py`, 31
distinct `d5_` names). `b1aa41d` says "12 mutants": 12 (9 item-4 + 3 privilege).

### Design decisions worth reviewing

1. **One settling UPDATE, money last.**
   - The argument checks, the replay, the fence and the validation run first; nothing is mutated until they pass.
   - `terminalize` then writes the job's outcome, usage, proposal and `settled_at` in ONE `UPDATE`. 0017's `AFTER UPDATE OF settled_at` trigger writes the one terminal event from that row. When the held-back bytes cannot fit the widest payload, it refuses the whole transaction before any money moves (R39).
   - Next come the reservations, the attempt, the idempotency tombstone and the two projections.
   - LAST comes the money, in the job's own unit (`settle_legacy_usd` / `settle_credit`). No function body names both money tables (R64), so `check_no_unit_conversion` stays green.
2. **The replay reads the job row FOR UPDATE before the fence.**
   - An identical duplicate completion therefore waits for the winner and replays the committed outcome. Without this, the fence would refuse it `already_terminal`; `d5_settle_without_the_row_lock` proves the difference under real transactions.
   - The lock is the job row the fence takes anyway, so the lock order is unchanged.
3. **The settled usage and the winner's proposal are stored.**
   - New columns `jobs.usage_prompt_tokens`, `usage_completion_tokens` and `proposal`. A CHECK and a BEFORE UPDATE guard freeze them once terminal.
   - `job_admission`'s outcome now carries `usage`, so `complete`, `get_owned`, `finalize_in_transaction` and the conformance comparisons all see one outcome.
   - The proposal is compared without the record's `schema_version`.
4. **R30 on PostgreSQL is stricter than the fake.**
   - A result reference must be THIS job's `infrx-result:<id>` with its 0014 row.
   - The builders' opaque `results/test/result.json` is staged by the rig exactly as W's worker does (`pgtesting.WorkerResults`: `put_result`, then `complete` with the returned reference).
   - The fake accepts any reference. This delta is recorded as an F delta.
5. **`completed` with no usage and nothing published** is rewritten `engine_incomplete` FIRST and then settled as that cause: `released_platform_absorbed`. That is the fake's order; the conformance case accepts either free settlement. A client's own cause with no usage is `released_free`.
6. **Usage rows.**
   - The legacy regime writes one `settlement_regime = 'pilot'` row for every `terminalize` outcome, with `cost_usd` = the debit.
   - A CREDIT usage row exists only when usage was metered. 06a `UsageRecordV2` requires a CREDIT row to carry its usage and outcome, so a free or unknown CREDIT outcome has its job row and projections but no usage row.
7. **`grant_credit` and `reconcile` write their own audit rows** (`admin_adjust` keyed `grant_credit:<operation_id>`, `admin_reconcile` keyed `reconcile:<operation_id>`), atomically with the money. G6B's service still appends its operator-request row under the operator's key afterwards: two rows, different keys, no collision (integration request 4).
8. **`reconcile` is D3's `release_aged_unknown` behind an audited, tenant-bound gate on the DB clock.**
   - A job the reaper already released answers `released_platform_absorbed`.
   - A job with no unknown usage is `state_conflict`.
   - A replayed operation id answers the state and writes nothing.
9. **Private catalog names.**
   - 0007 lists public deployments only.
   - A provider_dev credential reaches a private deployment on ITS endpoint as `<provider slug>/<endpoint name>-<env>`, with `@label` selecting the serving revision. That is exactly the v2 fixtures' `nemostation/marlin-2b-dev@2026-09-01`.
   - This is a ruling candidate: the name convention was implicit.
10. **`PgCatalogDirectory.data_access_policy` answers the policy version in force, with consent version 1 and trace mode `off`.**
    - 0007's policy table stores only a version and an instant.
    - An admitted job's own consent and trace mode are pinned from its request (`load_work_credit`).
    - Ruling candidate / 0007 follow-up.
11. **The supersession guard is structural** (`superseded()` + two tests): a mutant anchored in a function body that a later migration redefines fails the list statically. Quoted at this tree: `superseded(MUTANTS): []`, and `0016_fenced_leases.sql mutants anchored in the infrx.terminalize body: []`. That confirms item 10b: `terminalize`'s 0016 body had no mutants.
12. **`grant_credit` takes the amount as exact CREDIT text** (`^-?[0-9]{1,12}(\.[0-9]{1,8})?$`, non-zero): a JSON number could lose digits before the boundary sees it (R11). The adapter sends `str(Credit)`.
13. **R91 `idempotency_lookup` is STABLE and writes nothing.**
    - It reads the scope's mapping (org + operation + key).
    - A scope naming another org than the caller is `forbidden` (R10); a changed payload digest is `idempotency_conflict`, as `admit` raises.
    - An ACTIVE job's mapping never expires. A terminal job's expires at `coalesce(expires_at, settled_at + idempotency_ttl_s)`; the second term covers terminal paths that predate 0018's tombstone write.
    - It answers the job's own regime record (`job_admission` + `replayed`), so `lookup` returns `Admission` or `AdmissionV2` by the job's `accounting_regime`.

## Requirement coverage

SQL checks live in `tests/d/checks_settle.py` and `tests/d/checks_operations.py`. Each runs on a
committed admission-scenario database (a gateway clock an hour behind the database clock, the
database clock moved explicitly), and each ends with `assert_no_drift(conn)`: both wallet
reconciliation detectors (0003 USD, 0006 CREDIT) return no rows. That makes the D5 acceptance
line "wallet summary equals immutable ledger and active holds" a named assertion after every
money path.

| Test | Oracle | Invariant |
|---|---|---|
| `test_settle.py::test_dur_settle__one_winner_exact_decimals_then_replay` | DUR-SETTLE | the debit is the ADMITTED price snapshot over the usage, rounded half up once (equal to Python `money.debit`), and the ledger moves by exactly that (one `usage` row); the hold becomes `settled` and its reservation is released in the settling transaction. An identical proposal answers the committed outcome and writes nothing. Rounding boundary 0.005/M × 1 token → 0.00000001; an exact product settles as computed |
| `…__a_different_proposal_after_terminal_is_already_terminal` | DUR-SETTLE / DUR-OUTPUT | late data is internal only. After terminal, a different proposal gets `already_terminal` and writes no ledger, usage, outbox or journal row. A proposal naming another job is refused before any read. A late completion after cancel gets `already_terminal`. A foreign worker or stale generation gets `stale_lease`. Settled usage and the proposal cannot be rewritten (`jobs_settlement_record_guard`) |
| `…__success_needs_this_jobs_stored_result` | R30 | `succeeded` without a reference is `invalid_request` before any read. A reference must be THIS job's `infrx-result:<id>` with its 0014 row; one with no row, another job's, or opaque text is refused and moves nothing. `result_expires_at` = settled_at + `result_ttl_s` |
| `…__only_three_causes_charge_and_only_with_usage` | R21 | with the same usage, only `completed`, `client_cancelled` and `client_disconnected` debit; every other cause releases (`released_free` for never-charged causes, platform-absorbed otherwise). A client's cause with no usage and nothing published is free. `completed` with no usage is `engine_incomplete` (platform-absorbed, reference dropped) |
| `…__over_envelope_is_a_free_platform_error` | DUR-SETTLE | usage past a token ceiling is a `platform_error`, with nothing debited and the hold released, even when the debit would fit; usage exactly at the ceilings settles. A debit past `maximum_hold` is the same free platform error |
| `…__published_without_usage_is_held_unknown` | 02 | published with no usage gives `held_unknown` for every cause. The hold is `unknown` and still reserved, `reconcile_after` = DB clock + window, certainty `unknown`, and no ledger row is written |
| `…__everything_released_in_the_settling_transaction` | 02 §7 / DUR-CAP | every capacity reservation and the attempt are released. The tombstone expires `idempotency_ttl_s` after terminal. Exactly one usage projection, one trace projection and ONE pilot usage row are written (admitted price version, `cost_usd` = the debit) |
| `…__timestamps_are_the_db_clock` | R7 | `settled_at`, ledger `created_at`, usage `created_at` and the tombstone all use `infrx.now()`, never the gateway clock or `now()` |
| `…__the_terminal_event_is_the_triggers_one` | D4 request 6 / R30 | exactly one terminal journal event, last, whose payload is the STORED outcome (a rewritten one included); no second event |
| `…__cancel_records_its_cause_and_bills_by_r21` | R21 | `cancel` records the given cause in state `cancelled`, defaulting to `client_cancelled`. Unpublished: the client's causes are `released_free` and `sync_deadline` is platform-absorbed. Published: each is `held_unknown`. No ledger move. A repeat with another cause answers the committed outcome |
| `…__an_unknown_cause_is_refused_and_changes_nothing` | R21 | every other `TerminalCause`, and a non-cause, is `invalid_request` before any read. That holds for a foreign handle too (never `not_found`) |
| `…__a_24h_release_is_reported_as_released_not_as_a_new_terminal` | I3B req. 5 | the sweep reports the release as `{"released": …}`, once, at the window on the DB clock; a terminalization in the same sweep stays an `outcome` |
| `test_credit_spend__settles_on_the_credit_wallet_at_the_admitted_card` | CREDIT-SPEND | a CREDIT job is claimable and settles on ITS wallet at the ADMITTED card. The hold is `settled` BEFORE the `inference_debit` (a wallet with zero available still settles). One debit of `-charged` with the request id, dated `settled_at`; v1 debit 0. One pilot usage row: `accounting_regime = credit`, `charged_credits` = −ledger amount, `cost_usd` 0, the card/serving/deployment pins |
| `…__sql_settle_equals_v2_settle_on_the_grid` | CREDIT-SPEND | on a grid of token counts (0, 1, each ceiling, ceiling ± 1) and a card with half-unit ties (0.005/0.015 per M), the SQL charge equals `v2.records.settle`. End to end, the adapter's `SettlementV2` equals `settle(admission, usage, settled_at)` field for field |
| `…__usd_wallet_untouched` | R64/R65 | no CREDIT path (settle, free, quarantine, 24 h release, cancel) moves the USD wallet or ledger, and no legacy path moves a CREDIT wallet |
| `…__regimes_never_cross` | R64 | `terminalize` in the wrong regime is `not_found`, before and after terminal, and moves nothing; `load_work_credit` of a legacy job is refused by the adapter |
| `test_credit_rate__a_card_published_after_admission_is_ignored` | CREDIT-RATE / R68 / R78 | a card and a policy published after admission never reach the admitted job, in its WorkV2 or its charge. A job admitted afterwards pins and pays the new card |
| `…__retired_wallet_still_settles` | A1 req. 7 / R85 | a frozen wallet refuses new admission. A pre-retirement job still settles its debit on it, and a published job's unknown hold is still released at the window |
| `test_dur_admit__lookup_reads_the_mapped_job_and_writes_nothing` | R91 / 01 | `lookup` answers the job's own-regime admission (CREDIT with pins), `replayed`, and its outcome, and writes nothing. No key, an unmapped key or another org's unmapped scope → None. A changed payload → `idempotency_conflict`. A scope naming another org → `forbidden`. An active job's mapping never expires; a terminal one's expires `idempotency_ttl_s` after terminal, then → None |
| `test_settle_races.py` (9 items) | DUR-SETTLE / CREDIT-SPEND / DUR-FENCE | real transactions, lock-step in both orders plus barrier stress. A duplicate completion waits and replays (one debit). Cancel × 3 causes vs complete: one winner. The reaper skips a settling row and never re-terminalizes. Reconcile vs the reaper: one release, no debit. Two settlements plus an adjust on one CREDIT wallet: exact totals, never negative available. A stale generation after a requeue (same worker id) gets `stale_lease` |
| `test_operations_pg.py::test_credit_spend__adjust_is_audited_idempotent_and_never_below_reserved` | CREDIT-SPEND / API-OPS | one `operator_adjustment` row plus one `admin_adjust` audit row in one transaction. A replay answers `replayed` and appends nothing; the operation id reused for another movement → `idempotency_conflict`. A result below reserved is refused typed, nothing written. So is non-exact CREDIT text (> 8 places, 10^12, zero, a number, an exponent). A frozen wallet still accepts an adjustment |
| `…__allocation_only_to_provider_dev_wallets` | 0006 kinds | an allocation funds a provider_dev wallet only, positively; an adjustment targets a consumer wallet only; an unknown wallet → `not_found`; refusals write nothing |
| `test_dur_settle__reconcile_waits_for_the_db_clock_and_never_debits` | 02 / R7 | a release happens only once the DATABASE clock passes `reconcile_after` (a caller's `at` years later is audit data), through D3's release: no ledger move, audited `admin_reconcile`. A replay writes nothing. After the reaper already released → `released_platform_absorbed`. No unknown usage → `state_conflict` |
| `…__reconcile_is_tenant_bound` | R10 | another org's request and an unknown one get the same `not_found`, and nothing moves |
| `test_privileges__d5_operations_service_only_helpers_nobody` | R59 | every function 0018 defines or redefines is `service_role`-only; helpers are executable by nobody |
| `test_api_ops__*` (8) | API-OPS / DUR-RLS | tenant-scoped key rows, inserted once, revoked once (first `revoked_at` kept); suspension with one audited code; audit append-once and lookup by its own key only; immutable registry rows plus the alias move; per-unit usage totals and CREDIT-only holds; the ledger port; G6B's `service.Operations` end to end on the Pg adapters only (every write audited once); a partial publication is unreachable and rerunnable |
| `test_catalog_pg.py` (5 + 32 v2 cases) | CREDIT-RATE / R69 / R70 | private deployments → None for everyone except provider_dev on its own endpoint; unpriced → None; an alias move changes `resolve` but not an admitted job; a lookup from a fresh thread and loop is answered; a database error is raised, never answered None |
| `test_settle_units.py` (7), `test_operations_units.py` (6) | adapters (no Docker) | what the adapters send (the proposal, the regime, the store's own TTLs, the cause) and how they read answers (R39 refusals raised by type, `SettlementV2` exactly when settled, WorkV2, `released`, lookup regime) |

### Conformance partitions (exported suites, real stores)

**JobStore** (`test_jobstore_conformance.py`, 73 `jobstore_cases()` on `pgstore.factory`):

| | D4 (70 cases) | D5 (73 cases: + the cancel-cause lane's two, + G2's R91 lookup case) |
|---|---|---|
| must pass (and do) | 45 | **72**. The 23 former `_D5` cases, the former non-strict `RACY` case (now strict whichever transaction wins), `dur_settle__cancel_records_its_cause_and_settles_by_r21` (its pending line lifted, addendum 1), `dur_settle__cancel_refuses_any_other_cause_and_changes_nothing` and `dur_admit__lookup_reads_the_mapped_job_and_writes_nothing` (R91) |
| strict xfail | 23 `_D5` + 1 F2 | **1**: `dur_cap__total_org_and_key_limits_reject_with_retry_guidance`, "F2 conformance: the case reuses one key across two organizations" (`raises=InvalidApiKey`). No real store can pass it: the composite FK `jobs_key_belongs_to_org` (R59-6) ties a key to one org |
| non-strict RACY | 1 | **0** |
| skipped | 0 | **0** |

**StreamStore** (`test_streamstore_conformance.py`, 17 cases): **17 must pass (and do)**, 0 strict xfail (`PENDING = {}`: D4's 5 `_D5` cases lifted), 0 skipped.

**CreditJobStore** (`test_credit_jobstore_conformance.py`, NEW, 8 `credit_jobstore_cases()` on `pgtesting.make_credit_jobstore_factory`):

| | Cases |
|---|---|
| must pass (and do, both images at `4bcac3b`) | **5**: `credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit`, `credit_admit__lookup_answers_the_pinned_admission` (G2's R91 CREDIT case), `credit_settle__at_the_admitted_card_on_the_credit_wallet_only`, `credit_settle__a_free_outcome_moves_no_credit`, `credit_settle__cancel_records_its_cause_and_settles_by_r21` |
| strict xfail, owner G1R 2(c) (unassigned) | **2**: `credit_admit__refusals_leave_no_job_and_no_hold`, `credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet`. Each admits a provider_dev credential on its dev endpoint, which 0011 answers `not_found` (`raises=NotFound`) |
| strict xfail, owner F / coordinator (contract delta) | **1**: `credit_admit__a_replay_is_pinned_and_never_crosses_regimes`. 0011 (D2 review M7) answers a key of the other regime `state_conflict`, while the fake answers `idempotency_conflict`; both are 409 (`raises=StateConflict`) |
| skipped | **0** (the rig provides `credit_balance`, `credit_grant`, `register_credential`, `publish_rate_card`) |

**v2 cases on the PostgreSQL catalog** (`test_catalog_pg.py::test_v2_case_on_the_postgres_catalog`, 32 `v2_contracts.cases()`, `V2Harness(catalog=PgCatalogDirectory)`; wallet and provider directories are the fakes, labelled): **31 pass**, 1 strict xfail: `credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job`. "0007 lists PUBLIC deployments only: the case moves the public alias onto the private dev deployment, which catalog_listings' visibility FK refuses" (`raises=InvalidRequest`). The same invariant on a public deployment is `test_credit_rate__alias_move_changes_resolve_not_an_admitted_job`, which passes.

The focused run at `4bcac3b` gives `184 passed, 5 xfailed` = test_settle 19 + test_settle_races 9 + test_operations_pg 13 + test_catalog_pg 37 (36 + 1 xfail) + test_credit_jobstore_conformance 9 (1 list + 5 + 3 xfail) + test_jobstore_conformance 74 (1 list + 72 + 1 xfail) + test_streamstore_conformance 18 + test_e3b_drills 10 = 189 items.

## Environment

| | |
|---|---|
| OS / host | Linux 7.0.0-1010-aws (the coordinator's dev host), local Docker only |
| Python / uv | Python 3.12.3 (`make api-env`: pinned `uv sync --frozen --all-extras`, "Checked 41 packages"), uv 0.11.8, psycopg 3.3.6 |
| Docker | client 29.6.2 build dfc4efb, server 29.6.2 (commit 3d80467) |
| Images | plain `postgres@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20` (16.14-bookworm) + `infrx/state/supabase_shim.sql`; `supabase/postgres@sha256:7768d0d1d377250b718a9ad07f4661d008ebe6c96ecbbc4c08f3c5e53553e8fd` (17.6.1.173, no shim); `valkey/valkey@sha256:d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1` |
| Console | `pnpm install --frozen-lockfile --offline` in the worktree (pnpm 9.15.9, from the host's store; `node_modules` is ignored by git) so `make check`'s console targets run |
| Environment variable names | `INFRX_D_TASK`, `INFRX_D1_IMAGE`, `INFRX_MUTANTS`, `INFRX_D2_VALKEY_PORT`, `INFRX_D2_VALKEY_CONTAINER`, `INFRX_Q_VALKEY_PORT`, `INFRX_Q3_STORE` (the q3rig diff only), `DATABASE_URL` (only in the scratch-copy check of the cli diff, a dead DSN, nothing connected). Values are in the commands; no secret was used |
| Seeds | none (deterministic fixtures; the race tests' barrier rounds are fixed counts) |

## Commands and results (from `apps/infrx-api` unless stated)

The task-local environment in every PostgreSQL run is `INFRX_D_TASK=d5 INFRX_D2_VALKEY_PORT=55467 INFRX_D2_VALKEY_CONTAINER=infrx-d5-valkey`. `INFRX_D1_IMAGE=supabase` selects the Supabase image; unset means plain. Runs 20–25 were driven by `scratchpad/d5/driver.sh`, which also exports `INFRX_Q_VALKEY_PORT=55498`. Logs are in the session scratchpad (`logs/`, named in the last column).

| # | Command | Tree | UTC | Exit | Tail (quoted) | Log |
|---|---|---|---|---|---|---|
| 1 | `make api-env` (repo root) | `4bcac3b` | 17:04:45Z | 0 | `Checked 41 packages in 0.30ms` | `d5-api-env.log` |
| 2 | full D sweep, plain: `INFRX_MUTANTS=all uv run --frozen pytest -q -rs -p no:cacheprovider tests/d` | `906d963` | 16:02:42Z → 16:23:46Z | 0 | `1024 passed, 5 xfailed in 1261.53s (0:21:01)` | `sweep-plain.log` |
| 3 | full D sweep, Supabase (same command, `INFRX_D1_IMAGE=supabase`, `-rsx`) | `906d963` | 16:24:02Z → 17:00:10Z | 1 | `11 failed, 1016 passed, 2 xfailed in 2166.33s (0:36:06)`. The 11 are the rig and service failures of Limits 11 (8 CREDIT conformance cases, 3 `test_api_ops__*`), all fixed at `4bcac3b` (rows 4–6). **Every mutant in the list was killed**; no failure is a mutant | `sweep-supabase.log` |
| 4 | the two fixed files, Supabase: `pytest -q -rsx tests/d/test_credit_jobstore_conformance.py tests/d/test_operations_pg.py` | `4bcac3b` (uncommitted, then committed as is) | 17:01:45Z | 0 | `19 passed, 3 xfailed in 12.27s` | `d5-fix-supabase.log` |
| 5 | the same, plain | same | 17:02:05Z | 0 | `19 passed, 3 xfailed in 9.86s` | `d5-fix-plain.log` |
| 6 | focused (brief §7.2), plain: `uv run --frozen pytest -q -rsx tests/d/test_settle.py tests/d/test_settle_races.py tests/d/test_operations_pg.py tests/d/test_catalog_pg.py tests/d/test_credit_jobstore_conformance.py tests/d/test_jobstore_conformance.py tests/d/test_streamstore_conformance.py tests/d/test_e3b_drills.py` | `4bcac3b` | 17:04:28Z → 17:06:35Z | 0 | `184 passed, 5 xfailed in 126.00s (0:02:05)` (the 5 XFAIL lines are the partitions above) | `d5-focused-plain.log` |
| 7 | focused, Supabase | `4bcac3b` | 17:06:35Z → 17:12:47Z | 0 | `184 passed, 5 xfailed in 370.98s (0:06:10)` | `d5-focused-supabase.log` |
| 8 | partition counts: `pytest -q -s -k pending_list tests/d/test_jobstore_conformance.py tests/d/test_streamstore_conformance.py tests/d/test_credit_jobstore_conformance.py` | `4bcac3b` | 17:12:48Z | 0 | `73 cases: 72 must pass, 1 pending (strict xfail) - skips reported separately` / `17 cases: 17 must pass, 0 pending (strict xfail) - skips reported separately` / `8 cases: 5 must pass, 3 pending (strict xfail)` / `3 passed, 98 deselected` | `d5-partitions-plain.log` |
| 9 | code mutants + units: `pytest -q tests/d/test_code_mutants_d5.py tests/d/test_settle_units.py tests/d/test_operations_units.py` | `4bcac3b` | 17:04:45Z → 17:05:50Z | 0 | `42 passed in 64.91s` (28 mutants + the list check + 7 + 6 units) | `d5-code-mutants.log` |
| 10 | `pytest -q tests/contracts` | `4bcac3b` | 17:09:00Z | 1 | `23 failed, 1030 passed`. The failures are `tests/contracts/test_cancel_cause.py` (1, the obsolete pre-0018 case) and `tests/contracts/test_mutants.py` (22: the pristine baseline fails, so every runner case and subset mutant is `broken_runner`). Limits 1, integration request 2 | `d5-final-contracts.log`, `d5-final-contracts-rf.log` |
| 11 | `INFRX_MUTANTS=all pytest -q tests/contracts/test_mutants.py -k pg_cancel_records` | `906d963` | ~16:21Z | 1 | `AssertionError: pg_cancel_records_an_unsupported_cause is broken_runner (…): pristine baseline: the unmutated tree fails the list's own cases (pytest exit 1): ['tests/contracts/test_cancel_cause.py::test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record']` / `1 failed, 428 deselected in 7.23s`; and `-k "debit_rounds_up or settles_any_cause or the_charge_rounds_up"`: `3 failed, 36 deselected in 7.31s` | quoted in the session |
| 12 | the same suites on a scratch COPY of the tree with `contracts-retire-0016-refusal.diff` applied (`patch -p1`; the worktree untouched): `pytest -q tests/contracts/test_cancel_cause.py tests/contracts/test_mutants.py` | copy of `906d963` | 16:22:38Z | 0 | `40 passed in 87.65s` | `d5-contracts-retire-verify.log` |
| 13 | …and `INFRX_MUTANTS=all pytest -q tests/contracts/test_mutants.py` on that copy | copy of `906d963` | 16:22:48Z → 16:40:23Z | 0 | `428 passed in 1055.05s (0:17:35)` | `d5-contracts-mutants-all-patched.log` |
| 14 | …and `pytest -q tests/contracts` on a copy of `4bcac3b` (with `apps/app`) + the diff | copy of `4bcac3b` | 17:13:20Z → 17:14:48Z | 0 | `1052 passed in 87.72s` | `d5-contracts-patched-copy.log` |
| 15 | `pytest -q tests/g/ops` (fakes, unchanged) | `4bcac3b` | 17:09:43Z | 0 | `52 passed in 23.56s` | `d5-final-g-ops.log` |
| 16 | `pytest -q tests/g` | `4bcac3b` | 17:10:07Z → 17:11:53Z | 0 | `493 passed, 2 warnings in 104.40s` | `d5-final-g.log` |
| 17 | Q3 fake baseline: `INFRX_Q_VALKEY_PORT=55498 pytest -q tests/q/test_reconcile.py` | `906d963` | 16:25:07Z | 0 | `73 passed in 6.75s` | `d5-q3-fake-baseline.log` |
| 18 | Q3 on PostgreSQL: the same with `q3rig.diff` applied and `INFRX_Q3_STORE=postgres`, then reverted (`git status` clean, committed `1107079`) | `1107079` content | ~16:01Z | 1 | `4 failed, 69 passed in 44.47s` (integration request 6 names the 4) | `q3-pg-final.log` |
| 19 | 0018 privilege/definition measurement (`scratchpad/d5/measure_0018.py`, a fresh migrated clone, read-only) on both images | `4bcac3b` | 17:03:10Z / 17:03:13Z | 0 | identical on both images (quoted under "pgstate rows") | `d5-measure-0018.log` |
| 20 | mutants (brief §7.3), plain: `INFRX_MUTANTS=all uv run --frozen pytest -q -s tests/d/test_migration_mutants.py -k "d5_ or d3_cancel or d3_claim or d3_unknown_release"` | `4bcac3b` | 17:12:48Z → 17:14:38Z | 0 | `82 passed, 460 deselected in 109.06s (0:01:49)`; 82 kill lines, all `AssertionError` (below) | `d5-mutants-plain.log` |
| 21 | the same, Supabase | `4bcac3b` | 17:14:38Z → 17:17:34Z | 0 | `82 passed, 460 deselected in 175.40s (0:02:55)`; 82 kill lines, all `AssertionError` | `d5-mutants-supabase.log` |
| 22 | `make -k check` (repo root; `-k` so every target reports despite the known api-test red of Limits 1) | `c67e4f5` (= `4bcac3b` + evidence) | 17:17:34Z → 19:25:56Z | 2 | per target below: `api-test` and `api-mutants` red ONLY on `tests/contracts` (Limits 1); every console target and `bench-test` green | `d5-make-check.log` |
| 23 | `pytest -q --ignore=tests/d` legacy-first (`tests/test_*.py`, then `tests/contracts tests/g tests/i tests/j tests/m tests/q tests/t tests/w`) | `c67e4f5` (= `4bcac3b` + evidence) | 19:25:56Z → 19:50:27Z | 1 | `23 failed, 2759 passed, 2 warnings in 1455.85s (0:24:15)`: the 23 are `tests/contracts/test_cancel_cause.py` (1) + `tests/contracts/test_mutants.py` (22), Limits 1 | `d5-order-legacy-first.log` |
| 24 | the same, track-first | `c67e4f5` (= `4bcac3b` + evidence) | 19:50:27Z → 20:09:28Z | 1 | `23 failed, 2759 passed, 2 warnings in 1132.05s (0:18:52)`: the same 23, so the order changes nothing | `d5-order-track-first.log` |
| 25 | full D sweep, Supabase, at the implementation SHA | `4bcac3b` | 20:09:28Z → 20:43:30Z | 1 | `2 failed, 1022 passed, 5 xfailed in 2039.94s (0:33:59)`. The 5 XFAIL are the partitions, and every D5, D1–D4 and A1 mutant was killed. The 2 failures are `test_pgharness.py::test_a_second_concurrent_run_is_refused_and_alters_nothing` and `…::test_a_run_killed_mid_provision_is_cleaned_up_by_the_next_one`: `could not start infrx-d5-dharness-postgres: … failed to bind host port 127.0.0.1:55476/tcp: address already in use`. Another process held the decoy port during the run (the review lanes were running then); it was free again at 20:44Z. Re-run in the fix round | `d5-sweep-supabase-final.log` |

`make integration` was not run: no file under `tests/integration` was touched.

### Mutant kills (rows 20-21)

Both images: **82 killed, all by `AssertionError` in the named check**. That is 60 `d5_` names plus 22 `d3_` names matched by the `-k` filter: 14 claim, 3 cancel, 5 unknown-release (3 of those are anchored in 0018; `d3_unknown_released_early` is in 0016's `recover_job`, `d3_unknown_release_refused` in 0003). Tallies:

```
plain: 82 AssertionError
supabase: 82 AssertionError
```

Excerpt, plain (the full per-mutant lines are in `d5-kills-plain.txt` / `d5-kills-supabase.txt`):

```
d3_cancel_any_tenant: killed by cancel -> AssertionError: another tenant cancelled the job
d5_debit_rounds_down: killed by settle_exact -> AssertionError: ('pv_d5_tie', None, '0.00000000')
d5_usage_debit_without_ledger_row: killed by settle_exact -> AssertionError: a settled job: the wallet summary drifted from its ledger and holds: USD [(UUID('1a1a1a1a-0000-4000-8000-00000
d5_hold_not_moved_on_settle: killed by settle_exact -> AssertionError: a settled job: the wallet summary drifted from its ledger and holds: USD [(UUID('1a1a1a1a-0000-4000-8000-00000
d5_second_terminal_event: killed by settle_terminal_event -> AssertionError: untyped 23505
d5_credit_debit_before_hold: killed by credit_settle -> AssertionError: a settlement on a wallet with zero available failed: insufficient_credit
d5_claim_refuses_credit_again: killed by credit_settle -> AssertionError: a CREDIT job was not claimable: not_claimable
d5_settle_without_the_row_lock: killed by settle_races -> AssertionError: a duplicate completion was refused: already_terminal
d5_takes_the_scope_lock: killed by settle_races -> AssertionError: a settlement waited on the admission scope lock: untyped 57014
d5_reconcile_on_callers_clock: killed by reconcile_clock -> AssertionError: released before the window on the DB clock: None
d5_lookup_any_org_scope: killed by lookup -> AssertionError: another org read the scope
d5_release_reported_as_outcome: killed by settle_released -> AssertionError: [{'outcome': {'cause': 'queue_wait_expired', 'debit': '0.00000000', 'state': 'expired', 'usage': None, 'job_id
```

### `make -k check` by target (row 22; tree `c67e4f5` = `4bcac3b` + the WIP evidence commit)

| Target | Result (quoted) |
|---|---|
| `api-test` | `23 failed, 3394 passed, 5 xfailed, 2 warnings in 1664.50s (0:27:44)` → `make: *** [Makefile:13: api-test] Error 1`. The 23 are `tests/contracts/test_cancel_cause.py` (1) and `tests/contracts/test_mutants.py` (22), Limits 1. The 5 xfails are the D partitions. No skip |
| `api-mutants` | `412 failed, 2331 passed in 5889.43s (1:38:09)` → `Error 1`. **All 412 are `tests/contracts/test_mutants.py`** (`broken_runner`, Limits 1); every other list passed: m, q, j, w (incl. W4), t, d (migration list, D1–D4 code lists, signup), g, g/ops, g/uploads, i. `tests/d/test_code_mutants_d5.py` is not in the Makefile yet (integration request 1; run separately, row 9) |
| `console-test` | `# tests 289` / `# pass 289` / `# fail 0` / `# skipped 0` |
| `console-lint` | `✖ 2 problems (0 errors, 2 warnings)` (pre-existing, `apps/app`, not touched by D5) |
| `console-typecheck` | `✓ Types generated successfully`, then `tsc --noEmit` silent (exit 0) |
| `console-mutants` | contracts: `14 self-tests, 14 passed, 0 failed`, `199 mutants: 199 killed by a named declared case, 0 survived, 0 stale, 0 runner errors`; v: `40/40 mutants killed by a declared case.`; u: `2 self-checks, 2 as expected; 64 mutants, 64 killed, 0 not killed`; c: `4 self-tests, 0 failed`, `104 mutants: 104 killed by a named declared case, 0 survived, 0 stale, 0 runner errors` |
| `bench-test` | `67 passed in 5.93s` |
| overall | `make: Target 'check' not remade because of errors.` exit 2 at 19:25:56Z. Both errors are Limits 1 |

## Failure drill

| Injection | Durable state before → after | Duplicate / retry behavior | Cleanup |
|---|---|---|---|
| **Lost terminal ack.** The worker's completion commits, the answer is dropped, and the worker retries. Races: an identical second completion blocked on the job row; conformance: `FailurePlan` crash-after-commit on `complete` | running job, hold `held`, reservation active → `succeeded/completed`, hold `settled`, one `usage` ledger row, reservations released | the retry REPLAYS the committed outcome (same `settled_at`, debit), and exactly one ledger row exists (`rig.debits(request) == 1`, 5 barrier rounds × 8 callers). A different proposal is `already_terminal` and writes nothing (`check_settle_late_data`) | each case on a fresh clone; the harness drops its databases and removes its container at exit (`docker ps -a` shows no `infrx-d5-*` between runs) |
| **cancel × complete**, lock-step in both orders, for each of the three causes | running (published or not) → one terminal row | the cancel behind a settlement waits and answers the settled outcome, first cause and all. A completion behind a cancel is `already_terminal`. Never two debits; `assert_no_drift` after each | as above |
| **The reaper while a settlement holds the row** | running with a lapsed lease → settled by the completion | `recover` returns at once (SKIP LOCKED) and never re-terminalizes after the commit | as above |
| **reconcile × reaper** at the 24 h window | `held_unknown` → `released_platform_absorbed` once | the loser answers the released state; the ledger never moves; one audit row | as above |
| **A stale generation** after a requeue (same worker id) | generation 2 running | the generation-1 completion waiting behind the requeue is `stale_lease` | as above |
| **Partial publication** (`test_api_ops__a_partial_publication_is_unreachable_and_rerunnable`): a `Conflict` injected on a later registry row, with the alias move last | nothing is reachable (the alias has not moved) | the re-run completes the publication; `put` of identical rows answers False | as above |
| **Committed R29 refusal**: complete past the generation instant | the fence terminalizes (`deadline_exceeded`) and commits | `complete` raises `AlreadyTerminal` AFTER that commit (R39, `complete_raises_the_refusal_inside_the_transaction` killed) | as above |

## Changes (paths)

Every path is owned by this brief (D owns `tests/d/**` and `infrx/state/journal.py` since the D4 merge, per addendum 5). Nothing under `tests/contracts`, `tests/q`, `tests/g`, `tests/integration`, `infrx/contracts`, `infrx/operations`, the Makefile or `tasklocal.py` was committed.

- **New:** `apps/app/supabase/migrations/0018_terminal_settlement.sql` (850 lines, sha256 `1d1dc296846a38c9416b25febb76e10e505c568901731727a0ee378063257069`), `infrx/state/catalog.py`, `infrx/state/operations.py`, `tests/d/checks_settle.py`, `checks_operations.py`, `test_settle.py`, `test_settle_races.py`, `test_settle_units.py`, `test_operations_pg.py`, `test_operations_units.py`, `test_catalog_pg.py`, `test_credit_jobstore_conformance.py`, `code_mutants_d5.py`, `test_code_mutants_d5.py`.
- **Edited:**
  - `infrx/state/jobstore.py`: `complete`, `complete_credit`, `load_work_credit`, the `cancel` cause, `released`, `lookup`, the tolerant `_outcome`.
  - `infrx/state/pgtesting.py`: `WorkerResults`, the CREDIT factory and hooks, the Q3 hooks.
  - `infrx/state/credit_schema.py`: `SEAMS` and the lock-order note.
  - `infrx/state/journal.py`: D4 verifier N1/N2 wording.
  - `tests/d`: `checks.py` (`FILLED_RPCS`), `checks_credit.py` (`FILLED_BOUNDARIES`), `checks_leases.py` (`credit_running` on the real `claim`; D3's checks send well-formed proposals with TTL limits; the MY-3 case retired), `code_mutants_d3.py` (2 retired, 2 anchors `InvalidRequest`→`NotFound`), `migration_mutants.py` (`D5_MUTANTS`, `_CHECKS`, 20 re-anchored, `superseded()`), `test_migration_mutants.py` (guard tests, `ALWAYS`), `test_jobstore_conformance.py` / `test_streamstore_conformance.py` (partitions), `test_e3b_drills.py` (dr07), `test_lease_races.py`, `test_lease_units.py`, `test_journal_units.py`.
- **Contract change requests:** none of D5's own. The cancel-cause port was already merged. The R91 port is G2's. Proposals for G3 and the platform cause are under integration requests 3 and "Further deltas".

## Migration and rollback

- **0018 is additive and re-runnable.** 0001–0017 are untouched (`git diff e2a52b2 HEAD -- apps/app/supabase/migrations` lists only `0018_terminal_settlement.sql`).
- **It redefines**, with bodies copied verbatim except the named change:
  - `infrx.terminalize` (D3's stub filled);
  - `infrx.cancel` (+ cause);
  - `infrx.claim` (− MY-3);
  - `infrx.release_aged_unknown` (`{"released": …}`);
  - `infrx.grant_credit` (0004's stub filled);
  - `infrx.job_admission` (outcome + `usage`, top-level `charged_credits`).
- **It adds:**
  - functions `usage_doc`, `cause_carries_state`, `debit_legacy_usd`, `debit_credit`, `settle_legacy_usd`, `settle_credit`, `load_work_credit`, `reconcile`, `idempotency_lookup`, `jobs_settlement_record_guard`;
  - on `infrx.jobs`: columns `proposal jsonb`, `usage_prompt_tokens int`, `usage_completion_tokens int`, CHECK `jobs_settled_usage_is_one_fact`, trigger `jobs_settlement_record_guard` (BEFORE UPDATE).
- **Rollback** (header, verbatim intent):
  - Re-run the earlier `create or replace` statements (grants are kept): 0004's `grant_credit` stub, 0011's `job_admission`, and 0016's `terminalize` / `cancel` / `claim` / `release_aged_unknown`.
  - **Stop CREDIT admission before restoring 0016's `claim`**, or a claimed CREDIT job has no worker path.
  - Drop the added functions and the trigger, then the CHECK and the three columns.
  - Settled jobs, ledger, usage and audit rows stay: they are append-only money history and are never un-settled.
- **Deploy:**
  - Apply 0018 after 0017. It takes `ACCESS EXCLUSIVE` on `infrx.jobs` briefly: the three `add column` statements have no default, so there is no rewrite, and the new CHECK makes one validating scan (every existing row is NULL there).
  - No backfill: pre-0018 terminal jobs have NULL `proposal`/usage and read as before.
  - Until W wires `load_work_credit`/`complete_credit`, no deployment may set `ACCOUNTING_REGIME=credit` (integration request 5).
- **Pilot DSN login role:** unchanged from D4; every 0018 boundary is `service_role`-only and the adapters `set role service_role`.

### pgstate rows for 0018 (integration request 8; for `codex/e3b-phase2-gate`'s `tests/integration/pgstate.py`)

Measured on a fresh migrated clone, identical on both images (`scratchpad/logs/d5-measure-0018.log`):

```
FN infrx.cancel(jsonb) definer=True execute=('service_role',)
FN infrx.cause_carries_state(text,text) definer=False execute=()
FN infrx.claim(jsonb) definer=True execute=('service_role',)
FN infrx.debit_credit(text,integer,integer) definer=True execute=()
FN infrx.debit_legacy_usd(jsonb,integer,integer) definer=False execute=()
FN infrx.grant_credit(jsonb) definer=True execute=('service_role',)
FN infrx.idempotency_lookup(jsonb) definer=True execute=('service_role',)
FN infrx.job_admission(uuid) definer=True execute=('service_role',)
FN infrx.jobs_settlement_record_guard() definer=True execute=()
FN infrx.load_work_credit(jsonb) definer=True execute=('service_role',)
FN infrx.reconcile(jsonb) definer=True execute=('service_role',)
FN infrx.release_aged_unknown(uuid,timestamp with time zone) definer=True execute=()
FN infrx.settle_credit(uuid,numeric) definer=True execute=()
FN infrx.settle_legacy_usd(uuid,numeric) definer=True execute=()
FN infrx.terminalize(jsonb) definer=True execute=('service_role',)
FN infrx.usage_doc(integer,integer) definer=False execute=()
DEF CHECK ((((usage_prompt_tokens IS NULL) = (usage_completion_tokens IS NULL)) AND ((usage_prompt_tokens IS NULL) OR ((usage_prompt_tokens >= 0) AND (usage_completion_tokens >= 0) AND (NOT (usage_certainty IS DISTINCT FROM 'authoritative'::text))))))
DEF CREATE TRIGGER jobs_settlement_record_guard BEFORE UPDATE ON infrx.jobs FOR EACH ROW EXECUTE FUNCTION infrx.jobs_settlement_record_guard() enabled=O
COL proposal ('jsonb', False) ['service_role'] ['service_role']            # select / update holders
COL usage_prompt_tokens ('integer', False) ['service_role'] ['service_role']
COL usage_completion_tokens ('integer', False) ['service_role'] ['service_role']
SCHEMA usage ['service_role']
```

The rows, in that file's own format. The redefined `cancel`, `claim`, `grant_credit`, `job_admission`, `terminalize` (SERVICE) and `release_aged_unknown` (NOBODY) keep their existing rows unchanged:

```python
# FUNCTIONS (SECURITY DEFINER) - 0018 (D5)
    "infrx.debit_credit(text,integer,integer)": NOBODY,           # 0018 (D5)
    "infrx.idempotency_lookup(jsonb)": SERVICE,                   # 0018 (D5)
    "infrx.jobs_settlement_record_guard()": NOBODY,               # 0018 (D5)
    "infrx.load_work_credit(jsonb)": SERVICE,                     # 0018 (D5)
    "infrx.reconcile(jsonb)": SERVICE,                            # 0018 (D5)
    "infrx.settle_credit(uuid,numeric)": NOBODY,                  # 0018 (D5)
    "infrx.settle_legacy_usd(uuid,numeric)": NOBODY,              # 0018 (D5)
# INVOKER_FUNCTIONS - 0018 (D5), revoked from everyone
    "infrx.cause_carries_state(text,text)": NOBODY,
    "infrx.debit_legacy_usd(jsonb,integer,integer)": NOBODY,
    "infrx.usage_doc(integer,integer)": NOBODY,

SETTLED_USAGE_CHECK = ("CHECK ((((usage_prompt_tokens IS NULL) = (usage_completion_tokens IS "
                       "NULL)) AND ((usage_prompt_tokens IS NULL) OR ((usage_prompt_tokens >= 0) "
                       "AND (usage_completion_tokens >= 0) AND (NOT (usage_certainty IS DISTINCT "
                       "FROM 'authoritative'::text))))))")
SETTLEMENT_GUARD = ("CREATE TRIGGER jobs_settlement_record_guard BEFORE UPDATE ON infrx.jobs FOR "
                    "EACH ROW EXECUTE FUNCTION infrx.jobs_settlement_record_guard()")


def settlement_rows() -> list[Check]:
    """D5's 0018 objects on `infrx.jobs`: the settled usage/proposal columns per API role,
    their CHECK and the guard that freezes them once settled."""
    rows = [
        Check("E3B-RLS-0018-settled-usage-check", "postgres", None,
              "select pg_get_constraintdef(oid) from pg_constraint where conrelid = "
              "'infrx.jobs'::regclass and conname = 'jobs_settled_usage_is_one_fact'",
              ("value", SETTLED_USAGE_CHECK), "settled usage is one authoritative fact (0018)"),
        Check("E3B-RLS-0018-settlement-guard", "postgres", None,
              "select pg_get_triggerdef(oid) from pg_trigger where tgrelid = "
              "'infrx.jobs'::regclass and tgname = 'jobs_settlement_record_guard' "
              "and tgenabled = 'O'",
              ("value", SETTLEMENT_GUARD), "the settled usage and proposal are frozen (0018)")]
    for role in API_ROLES:
        sql = ("select proposal, usage_prompt_tokens, usage_completion_tokens "
               "from infrx.jobs limit 0")
        rows.append(Check(
            f"E3B-RLS-0018-settlement-columns-{role}", role, None, sql,
            ("rows", 0) if role == "service_role" else ("error", PERMISSION_DENIED),
            f"{role} {'reads' if role == 'service_role' else 'never reaches'} the settled usage",
            message_contains=None if role == "service_role"
            else "permission denied for schema infrx"))
    return rows
```

`service_role` holds UPDATE on the three columns through the earlier table-level grant on `infrx.jobs`, like every other `jobs` column. Once the job is settled, `jobs_settlement_record_guard` refuses the write (killed: `d5_settled_usage_rewritable`).

## Integration requests

1. **Coordinator (merge).**
   - `tests/integration/test_harness.py` expects the migration list to end at `0018_terminal_settlement.sql`; add that line with the merge.
   - `make api-mutants` += `tests/d/test_code_mutants_d5.py`.
   - `TASK_PORTS["d5"]` is already present (postgres 55436, valkey 55467); nothing to add.
   - `make integration` is not needed for this task: no file under `tests/integration` was edited.
2. **Coordinator / F (contracts): apply WITH the D5 merge. Otherwise `make api-test` and every contracts mutant are red.**
   - The cancel-cause lane's `tests/contracts/test_cancel_cause.py::test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record` pins the pre-0018 Python refusal that item 3 deletes. It fails on this branch (quoted).
   - Because the contracts runner's pristine baseline runs the whole list's targets, **every** contracts mutant becomes `broken_runner` (quoted: `debit_rounds_up`, `settles_any_cause`, `the_charge_rounds_up`).
   - The exact diff is `scratchpad/contracts-retire-0016-refusal.diff`, reproduced below. It deletes that test, its now-unused imports, and the mutant `pg_cancel_records_an_unsupported_cause` ("D5 retires this with the refusal", as its comment says).
   - Verified on a scratch copy of `apps/infrx-api` with the diff applied: `test_cancel_cause.py` + the default `test_mutants.py` subset gave `40 passed`, and the whole contracts list (`INFRX_MUTANTS=all`) gave `428 passed`, both exit 0.
   - What replaces the deleted test: `tests/d/test_settle_units.py::test_cancel__sends_the_cause_and_defaults_to_the_clients_own` (killing `the_cause_is_not_sent`), plus the strict conformance cases.
   - The port and fake cancel-cause change itself was merged earlier (0024da2); nothing else is needed from F.
3. **G2.**
   - 0018 records the cancel cause, so the relay's fallback on `UnsupportedParameter(param="cause")` no longer fires. G3/E4B remove it and its case (per the addendum).
   - Compose `PgCatalogDirectory(connector(dsn))`, or the pool's `Connect`. Every lookup is one statement on a fresh connection, and `test_credit_rate__a_lookup_from_a_fresh_thread_and_loop_is_answered` proves the probe path.
   - The sync wait's terminal commit is now real on PostgreSQL.
   - **Platform cancel cause (addendum 2), a ruling candidate.** Add `TerminalCause.platform_cancelled` to `CANCEL_CAUSES`, for the relay's own non-client stops and post-admission refusals. Its R21 settlement would be the `sync_deadline` row: platform-absorbed when unpublished, `held_unknown` when published, never billed. In 0018 it is one more literal in `infrx.cancel`'s cause check and in `cause_carries_state`, so it needs a new migration once ruled.
4. **Coordinator / G6B.**
   - `cli.build_operations` should be wired to the PostgreSQL adapters. The exact diff is `scratchpad/d5-cli-build-operations.diff`, reproduced below; it composes exactly what `test_api_ops__the_operations_service_runs_on_the_postgres_adapters` runs. It was checked on the scratch copy: without `DATABASE_URL` it refuses (`SystemExit`), and with one it builds `Operations` (`PgLedger`, `PgCatalogDirectory`, `PgJobStore`).
   - G6B's `ACTION` map files adjustments and reconciliations under `admin_grant`, but 0009 already has `admin_adjust`, `admin_reconcile`, `admin_job_cancel`, `admin_publish`, `admin_key_issue` and `admin_key_revoke`. Extend `ports.AUDIT_ACTIONS` to 0009's list and map each operation to its own action.
   - `grant_credit` and `reconcile` write their OWN audit row (`admin_adjust` / `admin_reconcile`, keyed `grant_credit:<op>` / `reconcile:<op>`), atomically with the money. The service then appends its operator-request row under the operator's key: two rows, different keys, no collision (asserted in the service test). If one row is preferred, the service can skip its append for `adjustment`/`reconcile`. That is G6B's call; no DB change is needed.
   - E3B phase 2 item 6 can drop its "fakes, labelled" note for these adapters.
5. **W (worker owner; the coordinator assigns).**
   - CREDIT jobs are now claimable (0018's `claim` lifts MY-3). The worker must use `load_work_credit` / `complete_credit` for them and drop the "drop the candidate" branch.
   - Until that lands, **no deployment may run `ACCOUNTING_REGIME=credit`**: a v1 worker would claim a CREDIT job and `load_work` answers it `not_found`.
6. **Q (Q3 rig on PostgreSQL; D3 request 5).**
   - The `q3rig.world` diff is `scratchpad/q3rig.diff`, reproduced below. `INFRX_Q3_STORE=postgres` builds the world on the D harness's `pgstore.factory` with `pgtesting.JobsView` and `pgtesting.PgDispatchOutbox`.
   - Result on PostgreSQL (plain image, `INFRX_Q_VALKEY_PORT=55498`): `4 failed, 69 passed`. Fake baseline at the same tree: `73 passed`. The diff was reverted and `git status` is clean.
   - The four failures are rig assumptions, not lost jobs:
     - `test_q3_run__a_slowly_failing_pass_does_not_hold_the_drain_back[memory]` and `[valkey]` assert `drains >= 10 × passes` and observed `23 >= 40` and `24 >= 40`. That ratio assumes the fake's microsecond drain; a real outbox read is milliseconds.
     - `test_q3_drill__valkey_sigkilled_under_queued_and_running_traffic_loses_no_job`: every admitted job succeeded and `leases == admitted`, but 3 outbox rows were still unacknowledged when the case read them after `stop`. The fake acknowledges synchronously; the real relay's last drain had not run.
     - `test_q3_differential__the_reconciler_agrees_on_both_adapters`: "seed 1 step 2 admit: unacknowledged diverged". The two adapters' worlds are two databases whose ids differ; the fake's are deterministic.
   - Q owns the case changes: compare shapes, not ids, and wait for the final drain.
7. **I3B.**
   - Feed `store.released` (the job ids released by that `recover()` sweep) to `metrics.record_recovery(released=…)`.
   - `reconcile.md#drift`: drift correction IS `Ledger.adjust` with a reason (an audited `operator_adjustment`). Retire the `settled_at` heuristic.
   - **The second half of request 5 is missing in D3 and is carried here, not implemented.** 0016 `infrx.recover_job`'s preparation branch inserts the `prepare_dispatch` outbox row and `return '[]'` (0016 l.409–412). The inference requeue returns `[{"index_event": …}]` (l.456–460).
   - Exact change, in a later migration that redefines `recover_job` and moves its 21 D3 mutants with it (the supersession guard enforces the move): `values (…, p_now) returning * into o; return jsonb_build_array(jsonb_build_object('index_event', infrx.index_event(o, j)));`. Check first that `index_event` accepts a `prepare_dispatch` row.
8. **E3B phase 2 / coordinator (pgstate rows for 0018; the 0017 rows are already on `codex/e3b-phase2-gate`).** Measured on the migrated database; see "pgstate rows" below.

Further deltas and proposals (the coordinator rules; no D edit is pending on them):

- **F fake deltas.**
  - (a) The fake accepts any `result_ref`, while the real store requires THIS job's stored `infrx-result:<id>` (R30). The conformance rig stages real results like W's worker does (`pgtesting.WorkerResults`).
  - (b) A key of the other regime: 0011 answers `state_conflict` (D2 review M7), while the fake answers `idempotency_conflict`. Both are 409. This is the CREDIT partition `credit_admit__a_replay_is_pinned_and_never_crosses_regimes`.
  - (c) A provider_dev admission on its dev endpoint (G1R 2c) is still unassigned (2 CREDIT partitions).
- **G3 contract proposals (addendum 4).**
  - `get_owned_credit` should return `state`, `deadline_at` and `max_output_tokens`. All three are columns of `infrx.jobs`; `job_admission` can add them at no cost.
  - `db_now()` on the `JobStore` port: `select infrx.now()`.
  - `result_expires_at` is now STORED at settlement (`settled_at + result_ttl_s`, D2 limit 9), so G3 can drop its route-side TTL.
  - Mapping retention is confirmed: an active job's idempotency mapping never expires, and a terminal one's expires `idempotency_ttl_s` (24 h by default) after its terminal state (`check_lookup`).
- **Ruling candidates.**
  - (i) A negative `operator_adjustment` is recorded as a store-made compensating entry under R11: audited `admin_adjust` with a reason, never below reserved (brief open input iv).
  - (ii) Private catalog naming is `<provider slug>/<endpoint name>-<env>` (design decision 9).
  - (iii) The data-access policy defaults are consent version 1 and trace `off` (design decision 10).
  - (iv) USD admin grants stay on the legacy `public.credit_ledger` writer and never go through `grant_credit` (R65/R72; brief open input ii).
- **Unassigned port built:** `PgWalletDirectory` (the v2 `WalletDirectory`, `revision >= 1`). It is used by the service composition; an owner should adopt it.

## Limits

1. **Red until integration request 2 is applied.**
   - `tests/contracts/test_cancel_cause.py::test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record` fails on this branch, by design of item 3. Through the contracts runner's pristine-baseline rule, it makes every contracts mutant `broken_runner`, so `make api-test`/`make api-mutants`/`make check` are red here for that one reason.
   - Owner: coordinator/F. The diff and its green verification on a scratch copy are quoted.
2. **Q3 on PostgreSQL: 4 of 73 fail**, for rig reasons (integration request 6). No lost or duplicated job was observed: every job succeeded and `leases == admitted`. Owner: Q.
3. **The second half of I3B request 5 is not implemented** (the preparation re-dispatch's index event); it is carried with the exact change. Owner: a D follow-up, which must re-anchor 21 D3 mutants.
4. **CREDIT partitions: 3 strict-xfail.**
   - Provider_dev admission on its dev endpoint (G1R 2(c)) is unassigned.
   - The regime-crossing replay code is a contract delta (F/coordinator).
5. **Catalog partition: 1 strict-xfail.** 0007 lists public deployments only, so a public alias cannot move onto a private deployment.
6. **`d5_wallet_locked_before_the_hold` (brief item 6) is not written**, because it is an equivalent mutant, not a missing check.
   - The settlement never locks the wallet itself. The hold's `held → settled` UPDATE and the ledger insert move the wallet through their triggers.
   - Every other writer of a hold (cancel, the reaper, `reconcile`) reaches it only under that hold's JOB row, which the settlement holds from its first statement.
   - Admission creates NEW holds and never locks an existing one.
   - So no transaction can hold this hold while waiting on its wallet, and the relative order of hold and wallet is unobservable. There is no single edit that "locks the wallet first" without adding a statement.
   - The lock-order property that IS observable, "never the admission scope lock", is `d5_takes_the_scope_lock`, killed.
7. **The catalog's data-access policy** answers consent version 1 and trace `off`: 0007 stores no per-deployment policy (design decision 10, a ruling candidate).
8. **The `connector` adapters open one connection per call** (`ponytail`, as D2's). A pooled `Connect` (G2's pilot pool) is the upgrade; the probe path is tested on a fresh thread and loop.
9. **Not measured:** production-size throughput or lock contention (the race tests prove order and exactness, not latency); any hosted Supabase project; the pilot box.
10. **`service_role` can UPDATE the new `jobs` columns before settlement** through the earlier table-level grant, as it can every `jobs` column. The gateway reaches them only through the SECURITY DEFINER functions, and the guard freezes them once settled.
11. **The 906d963 Supabase sweep had 11 failures**, in the conformance rig and a service test, not in 0018: 8 CREDIT cases (`InsufficientPrivilege: must be owner of table users`, D2's trigger bypass on `auth.users`) and 3 operations-service tests (`UndefinedColumn: email_confirmed_at`, since the bare image has no GoTrue columns). Both are fixed at `4bcac3b` (see its row).
    - Commit-message correction: `4bcac3b` says "(8 CREDIT cases, 2 service/partial-publication, 1 setup)". The three non-CREDIT failures are `test_api_ops__the_ledger_port_adjusts_reconciles_and_grants_through_d5_and_a1`, `…__the_operations_service_runs_on_the_postgres_adapters` and `…__a_partial_publication_is_unreachable_and_rerunnable`.

## Handback

- **Next unblocked:**
  - G2 integration: compose `PgCatalogDirectory`; the cause fallback is dead.
  - E3B phase 2 re-run: dr06/dr07/dr09 bodies pass on the real store, the pgstate 0018 rows are above, and the G6B adapters are real.
  - D6F/D6J (the remaining D stubs).
  - C3A/U1 (the console reads settled usage).
  - W's CREDIT worker path (request 5).
- **Pending coordinator wiring:**
  - requests 1 (harness list, Makefile), 2 (contracts retirement, together with the merge), 4 (cli diff);
  - the G6B action map;
  - the ruling candidates.
- **Unresolved findings:** Limits 1–4, 6, 7.

## Artifacts

Raw logs are in the session scratchpad (`…/scratchpad/logs/`, names in the run table). They are local only; no credentials, customer prompts or signed URLs. The exact diffs handed over:

- `contracts-retire-0016-refusal.diff`: sha256 `a8e75c3fbabc4c1ca9524b16067626e4cea487819e8bc5289dfa55f5639474ed`
- `d5-cli-build-operations.diff`: sha256 `d2a26348d34f61d9f54c13523c5b72c8e7437fc3be4ee1215d495af823f471ea`
- `q3rig.diff`: sha256 `4b69278e91efdf4003d31daf64371645cc089c09dd099f5ca1a7200f9f281497`

### `contracts-retire-0016-refusal.diff` (integration request 2)

```diff
--- a/apps/infrx-api/tests/contracts/test_cancel_cause.py
+++ b/apps/infrx-api/tests/contracts/test_cancel_cause.py
@@ -9,13 +9,9 @@
 """
 from __future__ import annotations
 
-import asyncio
 import inspect
-from types import SimpleNamespace
 
-import pytest
-from infrx.contracts import errors, ports
-from infrx.contracts.conformance import builders as b
+from infrx.contracts import ports
 from infrx.contracts.fakes.state import FakeJobStore
 from infrx.contracts.records import CANCEL_CAUSES, TerminalCause
 from infrx.state.jobstore import PgJobStore
@@ -37,39 +33,3 @@
         assert cause.kind is inspect.Parameter.KEYWORD_ONLY, operation.__qualname__
         assert cause.default is TerminalCause.client_cancelled, operation.__qualname__
     assert set(CANCEL_CAUSES) == THE_THREE
-
-
-def test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record():
-    """0016's `infrx.cancel` records `client_cancelled` whatever it is sent, so until D5's
-    0018 the adapter refuses every other cause - typed, `UnsupportedParameter` (an
-    `InvalidRequest`) naming `cause`, before any SQL - rather than silently recording the
-    wrong one. The default still reaches the database."""
-    sent = []
-    committed = {"job_id": b.ORG_A, "state": "cancelled", "cause": "client_cancelled",
-                 "result_ref": None, "settlement_state": "released_free",
-                 "debit": "0.00000000", "settled_at": "2026-09-20T12:00:00Z",
-                 "reconcile_after": None}
-
-    class Conn:
-        async def execute(self, sql, params=()):
-            sent.append(sql)
-
-            async def one():
-                return (committed,)
-            return SimpleNamespace(fetchone=one)
-
-        async def close(self):
-            pass
-
-    async def connect():
-        return Conn()
-
-    store = PgJobStore(connect)
-    for cause in (TerminalCause.client_disconnected, TerminalCause.sync_deadline,
-                  TerminalCause.completed, "bogus"):
-        with pytest.raises(errors.UnsupportedParameter) as refused:
-            asyncio.run(store.cancel(b.ORG_A, "job_x", cause=cause))
-        assert refused.value.param == "cause" and isinstance(refused.value, errors.InvalidRequest)
-    assert sent == [], "a refused cause reached the database"
-    outcome = asyncio.run(store.cancel(b.ORG_A, "job_x"))
-    assert outcome.cause is TerminalCause.client_cancelled and len(sent) == 1
--- a/apps/infrx-api/tests/contracts/mutants.py
+++ b/apps/infrx-api/tests/contracts/mutants.py
@@ -2081,10 +2081,6 @@
        S, "            if job.terminal:\n                # Completion won the race; a completed job stays completed.\n                return job.outcome",
        "            if job.terminal:\n                # Completion won the race; a completed job stays completed.\n                if job.state is JobState.cancelled:\n                    job.outcome = job.outcome.model_copy(update={\"cause\": cause})\n                return job.outcome",
        "credit_settle__cancel_records_its_cause_and_settles_by_r21"),
-    # Item 3: the PostgreSQL adapter until D5's 0018 (D5 retires this with the refusal).
-    _m("pg_cancel_records_an_unsupported_cause", "before 0018 no cause but client_cancelled reaches 0016",
-       "state/jobstore.py", "        if cause != TerminalCause.client_cancelled:", "        if False:",
-       "test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record"),
 )
 
 
```

### `d5-cli-build-operations.diff` (integration request 4)

```diff
--- a/apps/infrx-api/infrx/operations/cli.py
+++ b/apps/infrx-api/infrx/operations/cli.py
@@ -10,9 +10,10 @@
 issued secret is written once into `--secret-file` (created 0600, never overwritten).
 stdout carries the JSON result without the secret.
 
-The composition root is `build_operations()`. It refuses until D1R/D5/A1 provide the
-PostgreSQL adapters (the fakes live in tests only), so this tool cannot run against a
-store that would silently accept writes nobody persists.
+The composition root is `build_operations()`: the D5/A1 PostgreSQL adapters over
+`$DATABASE_URL`, one fresh `service_role` connection per operation. Without it the tool
+refuses (the fakes live in tests only), so it cannot run against a store that would
+silently accept writes nobody persists.
 """
 from __future__ import annotations
 
@@ -22,7 +23,7 @@
 import json
 import os
 import sys
-from datetime import datetime
+from datetime import datetime, timezone
 
 from ..contracts import errors
 from . import service
@@ -31,8 +32,20 @@
 
 
 def build_operations() -> service.Operations:
-    raise SystemExit("no state adapter is wired: the D1R/D5/A1 PostgreSQL adapters are "
-                     "pending, and this tool does not run against an in-memory store")
+    dsn = os.environ.get("DATABASE_URL")
+    if not dsn:
+        raise SystemExit("DATABASE_URL is not set: this tool runs only against the "
+                         "PostgreSQL store, never an in-memory one")
+    from ..state import operations as pg
+    from ..state.catalog import PgCatalogDirectory
+    from ..state.jobstore import PgJobStore, connector
+    connect = connector(dsn)
+    return service.Operations(
+        identities=pg.PgSignup(pg._Db(connect)), tenants=pg.PgTenantStore(connect),
+        ledger=pg.PgLedger(connect), audit=pg.PgAuditLog(connect),
+        registry=pg.PgRegistry(connect), wallets=pg.PgWalletDirectory(connect),
+        catalog=PgCatalogDirectory(connect), jobs=PgJobStore(connect),
+        accounts=pg.PgAccountView(connect), clock=lambda: datetime.now(timezone.utc))
 
 
 def refuse_secret_argv(argv: list[str]) -> None:
```

### `q3rig.diff` (integration request 6; applied, run, reverted)

```diff
diff --git a/apps/infrx-api/tests/q/q3rig.py b/apps/infrx-api/tests/q/q3rig.py
index e654998..970fe8f 100644
--- a/apps/infrx-api/tests/q/q3rig.py
+++ b/apps/infrx-api/tests/q/q3rig.py
@@ -8,6 +8,7 @@ for "a stale candidate never acquires a second lease".
 """
 from __future__ import annotations
 
+import os
 from collections import Counter
 from dataclasses import dataclass, field
 from typing import Any
@@ -21,7 +22,7 @@ from infrx.scheduling import MemoryScheduler, ValkeyScheduler
 from infrx.scheduling.reconcile import Reconciler
 
 from . import vkharness
-from .outboxfake import FakeDispatchOutbox
+from .outboxfake import FakeDispatchOutbox, OutboxLost
 
 ORG_A, KEY_A, ORG_B, KEY_B = b.ORG_A, b.KEY_A, b.ORG_B, b.KEY_B
 ADAPTERS = ("memory", "valkey")
@@ -50,13 +51,32 @@ def make_index(adapter: str, now, **kw):
     return ValkeyScheduler(vkharness.client(), now, namespace=vkharness.namespace(), **kw)
 
 
+#: D3 request 5 / D5 item 9e: `INFRX_Q3_STORE=postgres` runs every case on the REAL store -
+#: the PostgreSQL `PgJobStore` and D2's dispatch outbox functions on the D harness's own
+#: task-local database (`tests/d/pgstore`), with the store-side reads the cases make of the
+#: fake (`pgtesting.PgDispatchOutbox`, `pgtesting.JobsView`).
+STORE = os.environ.get("INFRX_Q3_STORE", "fake")
+
+
 def world(adapter: str, **index_kw) -> World:
     # Async jobs: their 600 s queue budget outlives the redeliveries a case waits for
     # (interactive jobs expire after 10 s queued); room for more than 8 in preparation.
-    h = jobstore_factory(limits=DEFAULTS.replace(max_preparing_jobs=256))
+    limits = DEFAULTS.replace(max_preparing_jobs=256)
+    if STORE == "postgres":
+        from infrx.state import pgtesting
+        from ..d import pgstore
+        # The real store runs each step slower than the fake, so the concurrent drills keep
+        # more jobs active at once: room for them (a rig setting, not store semantics).
+        h = pgstore.factory(limits=limits.replace(max_active_jobs=256, max_active_jobs_per_org=256,
+                                                  max_active_jobs_per_key=256))
+        store, rows = h.extra["store"], h.extra["conn"]
+        store.jobs = pgtesting.JobsView(rows)         # `w.jobs.jobs[id]`, read from the rows
+        outbox = pgtesting.PgDispatchOutbox(store, rows, lost=OutboxLost)
+    else:
+        h = jobstore_factory(limits=limits)
+        outbox = FakeDispatchOutbox(h.port)
     for org in (ORG_A, ORG_B):
         h.extra["grant"](org, "1000")
-    outbox = FakeDispatchOutbox(h.port)
     index = make_index(adapter, h.clock.now, **index_kw)
     return World(h, outbox, index, Reconciler(outbox, index, h.clock.now))
 
```

## Verification log

- 2026-09-23 (D5, Opus implementer): report written at implementation `4bcac3b` on `codex/d5-terminal-transaction`. Every count, SHA and tail is quoted from the logs named in the run table. Nothing was pushed, merged by D5 other than the coordinator-instructed `f2ee977`, or applied outside the task-local containers.
