# F2P wire-in (item 7) — the product-v2 contracts composed into the fakes, ports, config, conformance, console and mutant tooling

| Field | Value |
|---|---|
| Task | F2P item 7, the coordinator-owned wire-in phase (research/plan/01a-contracts-v2-map.md §7, 13 files), plus F2R-B NB-1/2/3, the AuditAction console alignment (C0 half), the two 01a §7 wire-in notes, Q2 request 3, and the coordinator's three D2-handback additions |
| Status | **partial / implemented (fake-only)**: items 1–9 and 11–13 implemented; item 10 implemented for the v1 read projection (Python half against real D1R rows, console half over the harness rows) but **not** for the console DTO swap (`WalletBalance`/`LedgerEntry`/`UsageRow` stay v1 — needs C1/U1 consumer changes, IR-DTO). Nothing integrated: no composition root, route, worker or SQL touched; nothing pushed. |
| Base SHA | `0cc4936` (claude/backend-impl after the F2R-A follow-ups merge `5f7ca02`) |
| Implementation SHA | `@@IMPL@@` (evidence committed after it) |
| Branch / worktree | `codex/f2p-wirein` in `.claude/worktrees/codex-f2p-wirein` |
| Classification | local only; the one PostgreSQL run used the D harness's own labelled container (created and removed by the run) |

## Commits (one per item or item pair)

| Item | Commit | What | Killing tests / mutants |
|---|---|---|---|
| 1 | `37d84a8` | `contracts/__init__.py` `_SUBMODULES` += `"v2"` | `test_contracts_v2_resolves_by_attribute_access_like_every_submodule` / `contracts_v2_not_a_submodule` |
| 2 + 6 | `e1b5670` | `conformance/__init__.py` exports `run_v2_conformance`, `V2_SUITES` (a sibling of `SUITES`); the v2 Python mutants folded into `tests/contracts/mutants.py` (the one list; `CONTRACTS` targets `tests/contracts/v2/test_conformance_v2.py`), `mutants_v2.py`/`test_mutants_v2.py` deleted, `api-mutants` drops the second target (IR-A10 had already delegated the classifier) | `test_the_conformance_package_exports_the_v2_suite` / `v2_suite_runs_nothing`; the 61 folded mutants are field-identical to the deleted list (checked by import before deletion) |
| 3 + 4 | `8835935` | `ports.CreditJobStore` (`admit_credit`, `get_owned_credit`, `load_work_credit`, `complete_credit`) beside v1 `JobStore`, names and shapes of D2's `PgJobStore` (`infrx/state/jobstore.py` on `codex-d2`, read-only); the fake store admits and settles in CREDIT; `credit_jobstore` suite in `V2_SUITES`; `credit_jobstore_factory` + `V2_FACTORIES` beside the v1 factories | 5 cases `credit_admit__*`/`credit_settle__*`; mutants `credit_hold_lands_on_the_usd_wallet`, `credit_hold_ignores_the_ceilings`, `credit_balance_checked_on_the_usd_wallet`, `get_owned_serves_a_credit_job`, `credit_wallet_by_organization`, `credit_replay_crosses_regimes`, `credit_replay_not_marked`, `credit_settles_at_the_published_card`, `credit_charge_in_the_usd_field`, `credit_settles_on_the_usd_wallet`, `load_work_serves_a_credit_job` (`dies_by=ValidationError`, declared and explained), `credit_free_outcome_settles`; 10 v1 mutants re-anchored to the shared admission path, all killed |
| coord. 1 | `ff8af86` | `dur_cap__total_org_and_key_limits…` gives each organization its own second key (`builders.KEY_A2`/`KEY_B2`) | its 4 existing mutants re-run: 4/4 killed |
| coord. 2 | `dc55fb2` | `Work.prompt_tokens: int \| None = None` (≥ 0, ≤ `max_input_tokens`), mirrored on `WorkV2`; `map.json` regenerated | `test_work_carries_preparations_prompt_count_within_the_admitted_ceiling`, `test_work_v2_carries_the_prompt_count_within_the_admitted_ceiling` / `work_prompt_tokens_unbounded`, `work_prompt_tokens_negative`, `work_v2_prompt_tokens_unbounded` |
| coord. 3 | `8835935` | the port siblings are D2's: `admit_credit(request: NormalizedRequest, idem: IdempotencyRef) -> AdmissionV2`, `get_owned_credit(org_id, job_handle) -> tuple[AdmissionV2, TerminalOutcome \| None]`; `prepared` answers a CREDIT job with its `AdmissionV2`, as D2's does | (as items 3 + 4) |
| 5 (+ Q2 req. 3, NB-3) | `c4286ba` | `DeploymentSettings.accounting_regime` (knob; empty refused; `legacy_usd`\|`credit`); `PilotSettings.active_rate_card_version` (contract data, may be empty; a CREDIT deployment refuses to start without it, R69) and `provider_dev_allocation_ceiling_credit` (a `Credit`, default zero = no allocation; negative/NaN/exponent/9-digit refused); `max_index_items`/`max_index_bytes` moved from `DeploymentSettings` to `PilotSettings` (Q2's Valkey adapter reads them by `getattr`); the cursor-secret bound pinned at 15 refused / 16 accepted | `CFG-V2-01…05`, `DEPLOY-06`/`07` (NB-3); `DEPLOY-03` re-anchored |
| 7 | `4eecedb` | the v1 fixture guard knows `fixtures/v2/`: the root holds exactly `v1/` and `v2/`, every v2 file claimed | `test_the_fixture_root_holds_exactly_the_two_revisions` / `v2_fixture_table_unclaimed` |
| 8 + 9 | `8bfa234` | `types.ts` re-exports the whole revision as the `v2` namespace (`v2/types.ts` now also carries `money-units.ts`): v1's `ACCOUNTING_REGIMES` (`legacy_usd`\|`pilot`) and v2's (`legacy_usd`\|`credit`) — the one name both declare — cannot shadow each other. `test_parity_console.py` owns that seam; `test_parity_v2.py` keeps the per-enum comparison | `the v1 contract module carries the v2 revision as a namespace, not a shadow` / `V2-NS-01`, `V2-NS-02` (console list); the parity test itself has no mutant (it compares two languages' sources, like every parity test) |
| 11 + 12 | `6feecd5` | `run-mutants.mjs` takes an `entry` per mutant (`--entry` selects): `conformance`, `v2` (and `fixtures`, NB-2); one kill rule; `mutants-v2.json` folded into `mutants.json`, `run-mutants-v2.mjs` deleted; `console-mutants` runs the runner's self-tests (two new v2-entry ones) then every entry | runner self-tests 14/14; the 23 folded v2 mutants all killed under the stricter assertion rule |
| NB-1 | `ad4c9ba` | `judge.json` gains a capped dry run (50 embedded samples, `sample_count` 73); the fake projects the run's own count; the judge case requires a capped run to report beyond the cap on a harness that declares its history | `JUDGESAMPLE-04`; `JUDGESAMPLE-03` re-anchored |
| NB-2 | `913ffa5` | R62 guard: every trace content template's model is `<public_model_id>@<revision>` and a served model | `R62-TRACES-01` (entry `fixtures`) |
| 01a §7 note | `148de1e` | v2 grant/membership checks compare instants through `instantKey` (Z only, fraction padded to microseconds); offsets are refused, not compared | `V2-INSTANT-01…04` |
| AuditAction | `2c38694` | console `AUDIT_ACTIONS` = D1's ten values in D1's order (0003's four, 0009's six); fake and conformance speak them | `AUDIT-ACTION-01`; `AUDIT-02`/`04` re-anchored |
| 10 (Python) | `2e6538e` | `tests/contracts/v2/test_v1_projection_pg.py`: the v1 → v2 read projection over D1R's real `upgrade05` rows. **Finding:** 0001–0005 wrote legacy usage with NULL tokens and NULL settlement state, which `UsageRecordV2` could not hold and `project_v1_usage` filled with an invented `settled`. Now `usage`/`outcome` are absent-able on a `legacy_usd` row (required on `credit`) and nothing is invented; fixtures regenerated | the three PG tests; `legacy_projection_invents_settled`, `legacy_projection_invents_zero_usage`, `credit_row_without_usage` |
| 10 (console) | `2a22176` | `v2.projectV1UsageRow`: a v1 console usage row as a `legacy_usd` USD `UsageRecordV2`, every absence kept; a conformance case runs it over the harness's own rows (C1's harness included) with one USD total | `PROJ-01…03` |
| 13 | `91b4a83` | `01-contracts.md` and `06-database-map.md` link 01a/06a; 01a records what the wire-in composed and what it did not (08 §10's R64–R78 fold was done at the additive merge) | docs |
| fix | `092960f` | four console mutants re-anchored after `instantKey`/`run.sample_count` (found by the full `console-mutants` run) | `V2-GRANT-01/02`, `V2-ROLE-03`, `XJUDGE-02`: 4/4 killed |

## Requirement coverage (the invariants the new tests claim)

| Test | Invariant |
|---|---|
| `credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit` | The wallet is the credential's own (R66); pins equal the fixture pins and the card is the catalog's; the hold is `card.maximum_hold(ceilings)` on that CREDIT wallet; the org's USD wallet has no hold; `get_owned_credit` answers the admission and v1 `get_owned` is `not_found` |
| `credit_admit__refusals_leave_no_job_and_no_hold` | Consumer key on a private dev model and an unknown model → `not_found`; unpriced → `invalid_request`; operator key → `forbidden`; unfunded provider dev wallet → `insufficient_credit` until an audited allocation; no refusal leaves a job or a hold; a provider admission never touches the consumer wallet |
| `credit_admit__a_replay_is_pinned_and_never_crosses_regimes` | A replay after a rate change is the admitted card and hold, marked replayed (revalidated, never `model_copy(update=)`, R78); the same key through v1 `admit` is `IdempotencyConflict` |
| `credit_settle__at_the_admitted_card_on_the_credit_wallet_only` | Settlement at the admitted card (R68) though a 10× card was published mid-flight; ledger moves by exactly the charge, hold released; v1 `TerminalOutcome.debit` is zero; USD wallet unmoved; `load_work_credit` carries the admitted card, pins and resolved wallet; v1 `load_work` is `not_found` |
| `credit_settle__a_free_outcome_moves_no_credit` | A free cause settles nothing: no `SettlementV2`, balance identical |
| `test_every_pre_cutover_usage_row_reads_as_a_legacy_usd_dto` (PG) | Every `usage_events` row of both upgrade05 orgs, through D1R's `infrx.usage_records`, is a `legacy_usd` USD DTO equal to `cost_usd`, with no rate card/serving/deployment, and `UsageHistory.totals()` is exactly `{USD: sum(cost_usd)}` |
| `test_project_v1_usage_reads_the_raw_rows_to_the_same_dtos` (PG) | `project_v1_usage` over the raw pre-cutover rows equals the seam's DTOs row for row |
| `test_the_legacy_statement_is_a_rollout_hold_never_a_credit_figure` (PG) | `console_legacy_usd_statement` is a valid `LegacyUsdStatement` equal to the ledger sum and count, `rollout_hold` iff nonzero; no CREDIT wallet or ledger row exists after the upgrade |
| `a pre-cutover usage row reads as a legacy USD v2 record and invents nothing` (console) | Over the harness's rows: regime `legacy_usd`, unit USD, charge to the digit, no rate card/serving, outcome only where the row has a settlement state, usage exactly where tokens were recorded, one USD total |
| `grant and membership checks compare instants, never mixed spellings` | `12:00:00Z` and `12:00:00.5Z` order correctly; `+00:00`, `z`, no zone, 7 fraction digits are refused |
| config tests (`test_the_accounting_regime_is_a_v2_regime`, `test_a_credit_deployment_needs_an_approved_rate_card`, `test_the_allocation_ceiling_is_a_credit_amount_defaulting_to_nothing`, `test_the_active_rate_card_version_is_exact_text`, `test_the_cursor_secret_bound_is_exactly_sixteen_characters`) | As stated in their names; the CREDIT regime refuses to start without an approved card in every mode (legacy, dev, pilot) and names the setting |

## Environment

Linux 7.0.0-1010-aws x86_64; Python 3.12.3 (`make api-env`, uv 0.11.8, pydantic 2.13.5, psycopg 3.3.6); Node v22.23.1, pnpm 9.15.9 (`pnpm install --frozen-lockfile`); Docker server 29.6.2. PostgreSQL: the D harness's plain image (`postgres@sha256:33f923b0…`, 16 + shim), container `infrx-d1-postgres` on 55432 created and removed by this run. Environment variable names used: `INFRX_MUTANTS`.

## Commands and results

All times UTC. Python commands in `apps/infrx-api`, console commands at the repository root. "At" names the commit the run saw; later commits are listed with what they re-ran.

| Command | At | Exit | Result (quoted) |
|---|---|---|---|
| `make api-env` | `0cc4936` | 0 | `uv sync --frozen --all-extras` completed |
| `make console-test` | `91b4a83` (23:25Z) | 2 | `# tests 288` / `# pass 286` / `# fail 2` / `# skipped 0`; the two are `not ok 13 - every conformance case C1 owns passes against the real services` and `not ok 14 - every failing case fails only because an operation is not implemented in C1` (IR-C1). Re-run at `2925d63` (00:38Z): identical |
| same, with IR-C1 applied on an uncommitted scratch edit (reverted) | `2a22176` | 0 | `# tests 288` / `# pass 288` / `# fail 0`; `node tests/c/run-mutants.mjs`: `104 mutants: 104 killed by a named declared case, 0 survived, 0 stale, 0 runner errors` |
| `make console-lint` | `91b4a83` | 0 | `✖ 2 problems (0 errors, 2 warnings)` (the two pre-existing warnings) |
| `make console-typecheck` | `91b4a83` | 0 | `next typegen` then `tsc --noEmit`, no diagnostics |
| `make console-mutants` (first run) | `91b4a83` | 2 | runner self-tests `14 self-tests, 14 passed, 0 failed`; then `195 mutants: 191 killed by a named declared case, 0 survived, 4 stale, 0 runner errors` — V2-GRANT-01/02, V2-ROLE-03, XJUDGE-02 stale after this lane's own edits; fixed in `092960f` |
| `make console-mutants` (every line, each also run on its own) | `092960f` | 0 | contracts runner: `14 self-tests, 14 passed, 0 failed`, baselines `conformance: 49`, `fixtures: 5`, `v2: 19` cases pass unmutated, `195 mutants: 195 killed by a named declared case, 0 survived, 0 stale, 0 runner errors`; `tests/v`: `40/40 mutants killed by a declared case.`; `tests/u`: `2 self-checks, 2 as expected; 64 mutants, 64 killed, 0 not killed`; `tests/c`: `4 self-tests, 0 failed`, `baseline: 85 cases pass unmutated, 32 fail (C2/C3 operations this task does not implement)`, `104 mutants: 104 killed by a named declared case, 0 survived, 0 stale, 0 runner errors` |
| `uv run --frozen pytest -q tests/contracts` (v1 + v2) | `91b4a83` (23:26Z) | 1 | `3 failed, 1034 passed` — the three are `test_v1_projection_pg.py`, each `HarnessBusy: another run holds /tmp/infrx-d1-postgres-55432.lock (… checkout …/codex-g1r)` |
| `… pytest -q tests/contracts --ignore=tests/contracts/v2/test_v1_projection_pg.py` | `2925d63` (00:38Z) | 0 | `1034 passed in 145.51s` |
| `… pytest -q tests/contracts/v2/test_v1_projection_pg.py`, retried while `HarnessBusy` (17 busy attempts from 23:54Z) | `2e6538e`…`2925d63` (same projection code) | 0 | `3 passed in 3.67s` (00:03Z); the container it created was removed at exit |
| legacy-first: `pytest -q -p no:cacheprovider tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/contracts tests/g tests/i tests/j tests/m tests/q tests/t tests/w` | `91b4a83` | 1 | `3 failed, 2365 passed, 2 warnings in 648.90s` — the same three `HarnessBusy` (holder `codex-g1r`) |
| track-first: `pytest -q -p no:cacheprovider tests/g tests/i tests/j tests/m tests/q tests/t tests/w tests/contracts tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py` | `91b4a83` | 1 | `3 failed, 2365 passed, 2 warnings in 883.96s` — the same three `HarnessBusy` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py` (the one Python list, v1 + folded v2 + this lane's) | `91b4a83` | 1 | `1 failed, 392 passed in 1380.57s` — `the_pin_hardcodes_a_rate_card_version is broken_runner … undeclared exception deaths ['ValidationError@v2_contracts.py']` (a mutant of the folded additive list; fixed in `2925d63`) |
| same | `2925d63` (00:18Z–00:37Z) | 0 | `393 passed in 1179.52s` |
| `make integration INTEGRATION_ARGS="--layer 1 --canary"` | `2925d63` (00:18Z–00:33Z) | 2 | `[skip] preflight: layer 1 only`; `[ok  ] engine: … "cases": 8, "transport": "http"`; `[FAIL] suites`: `pytest tests/integration` exit 0 `passed 90, skipped 54`, `make api-test` exit 2 with tail `129 failed, 2371 passed` (the failing names shown are `tests/d/test_schema_postgres.py::…` and one `tests/q/test_valkey_scheduler.py::…[dur_outbox__a_candidate_carries_its_dispatch_kind]`), `make console-test` exit 2 `node_pass 286, node_fail 2` (IR-C1), `make bench-test` exit 0 `67 passed`; `[ok  ] mutants: {"mutants": 67, "killed": 65, "controls_survived": 2, "not_killed": 0}`; `[ok  ] canary: both runners fail and name the canary`; `exit 1 (see the stages above)` |
| probes after the integration run | `2925d63` (00:34Z) | 0 / 1 | `pytest -q tests/q/test_valkey_scheduler.py`: `45 passed in 3.19s`; `pytest -q tests/d/test_schema_postgres.py -x`: `HarnessBusy: another run holds /tmp/infrx-d1-postgres-55432.lock (pid … checkout …/codex-d3)` |
@@TESTSD@@

## Failure drill

Not a durable-state task; the drills are the mutants (each a single edit whose named case must fail by assertion or by a declared death) and the PostgreSQL projection run. Its first execution, before the contract fix, **failed** on a real defect in the v2 record against D1R's real rows:

```
E       pydantic_core._pydantic_core.ValidationError: 1 validation error for UsageRecordV2
E       outcome
E         Input should be 'settled', 'released_free', 'held_unknown' or 'released_platform_absorbed' [type=enum, input_value=None, input_type=NoneType]
FAILED tests/contracts/v2/test_v1_projection_pg.py::test_every_pre_cutover_usage_row_reads_as_a_legacy_usd_dto
``` The D harness container this run created was removed at exit (`docker ps -a --filter label=ai.infrx.d1.checkout` showed only another checkout's container afterwards).

## Changes

Owned paths only: the 13 files of 01a §7 and the tests they need, plus `contracts/limits.py`, `contracts/records.py` (`Work.prompt_tokens`), `contracts/v2/records.py`, `conformance/{builders,jobs,v2_contracts}.py`, `apps/app/lib/contracts/{v2/types.ts,fixtures/*.json}` and `apps/app/tests/contracts/**` under this phase's delegation. Deleted: `tests/contracts/v2/{mutants_v2.py,test_mutants_v2.py}`, `apps/app/tests/contracts/v2/{mutants-v2.json,run-mutants-v2.mjs}`. Not touched: `gateway/app.py`, routes, worker, SQL, other tracks' files, lockfiles. No migration; rollback is `git revert` of the commits (no persisted state depends on them).

Contract changes a reviewer should see: v1 `Work` gains an optional field; v2 `UsageRecordV2.usage/outcome` become absent-able for `legacy_usd` only (a v2 contract correction found against real rows); `max_index_*` moved between settings objects (same env names and defaults; an empty value is now "unset" rather than refused, the `PilotSettings` rule); console `AUDIT_ACTIONS` values renamed to D1's.

## Limits

1. **Item 10's DTO swap is not done.** `WalletBalance`/`LedgerEntry`/`UsageRow` stay v1 in `services.ts`: the swap needs C1 (`lib/services/console.ts`, `query.ts`) to read D1R's `console_wallet_summary`/`console_legacy_usd_statement`/`console_credit_ledger` and `console_usage`'s appended columns, and U1 (`app/(console)/billing|usage/*`, `tests/u`) to render CREDIT beside the legacy statement. Those are other tracks' files and App work (P-17 orders App after the backend) — IR-DTO.
2. **`make console-test` is red on two C1 cases until IR-C1** (two lines in `tests/c/harness.ts` still seed the old audit spellings). Verified, on an uncommitted scratch edit that was reverted: `pnpm test` 288/288 and `node tests/c/run-mutants.mjs` 104/104 killed. `make console-mutants` is green without it (the `tests/c` runner's baseline records 32 unimplemented-or-failing cases and still kills all 104). `make integration --layer 1 --canary` is red for the same reason plus the shared-port `HarnessBusy` failures of `tests/d` inside `make api-test`.
3. **Fake-only.** The CREDIT JobStore suite passes against the fake store; D2/D5 must run `run_credit_jobstore_conformance` against PostgreSQL. The v1 read projection is the one thing here proven against a real database.
4. The fake's lifecycle row for a CREDIT job is a v1 `Admission` built with `model_construct` and `price_snapshot=None` (marked `ponytail:` in `fakes/state.py`): it never leaves the store (every v1 read of a CREDIT job is refused), but the clean fix is a v1 revision making `Admission.price_snapshot` optional, as D1R already made the column nullable — requested below.
5. `NormalizedRequestV2.wallet_id` has a single writer in the fake (`resolve_wallet` inside `_credit_terms`), proven by `credit_wallet_by_organization`; nothing in the tree outside the fake constructs a `NormalizedRequestV2` yet, so the rule binds G1R/D2 at composition.
6. `provider_dev_allocation_ceiling_credit` defaults to zero: the product number is ⚠️ TO BE VERIFIED (operator input with Lab onboarding, P-08). Nothing enforces it yet (no allocation path exists outside the fake's test hook).
7. The three PostgreSQL tests fail with `HarnessBusy` whenever another checkout holds port 55432 (they did during both full orderings, holder `codex-g1r`); they passed on their own run, quoted above. `tests/d` itself was not run (not touched; the brief allows `--ignore=tests/d`).

## Handback

Next unblocked: **G1R** (composition), **D2/D3/D5** (the CREDIT port on PostgreSQL), **C0/U1R** (the console DTO swap).

### integration_requests

- **IR-C1** (`apps/app/tests/c/harness.ts`, C1/C0), two lines: `action: "entitlements_set"` → `action: "admin_set_entitlements"`; `action: i % 2 === 0 ? "grant" : "suspension_set"` → `action: i % 2 === 0 ? "admin_grant" : "admin_set_suspension"`. Verified green on a scratch edit (above).
- **IR-DTO** (C0/C1 + U1R, App phase): `WalletBalance` → `v2.BalanceV2` (the individual's CREDIT wallet, `legacy_usd: LegacyUsdStatement | null` beside it; the session must carry the individual's user id for `console_wallet_summary(user)`); `LedgerEntry` → the `console_credit_ledger` row (`kind: v2.LedgerEntryKindV2`, `amount: Credit`, `unit: "CREDIT"`, keyset `(created_at desc, entry_id desc)` per wallet), USD history read only through the legacy statement; `UsageRow` gains `unit`, v2 `accounting_regime` (`legacy_usd`\|`credit`, D1R's column) beside the v1 settlement regime, `charged_credits: Credit | null`, `rate_card_version`, `serving_version_id`, `deployment_revision_id` (all appended to `console_usage` by 0008; column list in `infrx/state/credit_schema.py`), totals one per unit; `AdminGrantResult`/`AdminOrgSummary.balance` follow `WalletBalance`. `v2.projectV1UsageRow` is the adapter for pre-cutover rows.
- **G1R composition** (`gateway/app.py`, G's): dispatch admission on `settings.deployment.accounting_regime` — `legacy_usd` → `JobStore.admit`, `credit` → `CreditJobStore.admit_credit` (and `get_owned_credit`/`load_work_credit`/`complete_credit` for those jobs; a CREDIT job's charge is `SettlementV2.charged`, never `TerminalOutcome.debit`); pass `pilot.max_index_items`/`max_index_bytes` to `MemoryScheduler(max_items=…, max_bytes=…)`; startup already refuses `ACCOUNTING_REGIME=credit` without `ACTIVE_RATE_CARD_VERSION` (`validate_runtime`). G1R's own factories should run `V2_SUITES`.
- **D2 cutover**: `PgJobStore.admit_credit`/`get_owned_credit` already match `ports.CreditJobStore`; adopt `run_credit_jobstore_conformance` with a factory exposing `credit_balance(wallet_id)`, `credit_grant(wallet_id, amount)`, `register_credential(auth)` (a key row with its audience), `publish_rate_card(card)` beside v1's `balance`/`active_jobs`. **D3**: `load_work_credit(lease) -> WorkV2` and `Work.prompt_tokens`/`WorkV2.prompt_tokens` from `jobs.prepared_prompt_tokens`. **D5**: `complete_credit` returns `(TerminalOutcome with debit 0, SettlementV2 | None)`, settlement at the admitted card only.
- **Coordinator, v1 revision**: `Admission.price_snapshot: PriceSnapshot | None` for CREDIT jobs (D1R relaxed the column) — removes the fake's `model_construct`.
- **Coordinator, 08 §5 / §10**: record `ACCOUNTING_REGIME` (§5.1 deployment table), `ACTIVE_RATE_CARD_VERSION` and `PROVIDER_DEV_ALLOCATION_CEILING_CREDIT` (§5 pilot table), and `MAX_INDEX_ITEMS`/`MAX_INDEX_BYTES` moving from §5.1 to §5. Ruling candidates: (a) a CREDIT job's v1 `TerminalOutcome.debit` is always zero, the charge is `SettlementV2`; (b) `UsageRecordV2.usage`/`outcome` may be absent on a `legacy_usd` row, never on `credit`; (c) the console compares contract instants only through `instantKey`; (d) console `AUDIT_ACTIONS` are D1's spellings.
- **Q**: `infrx.scheduling.memory.MAX_INDEX_*` are still module constants, pinned equal to the `PilotSettings` defaults by `test_the_g1_and_q1_constants_match_the_deployment_defaults`.
- **W2**: `Work.prompt_tokens` exists (None until D3 fills it).

Nothing was pushed, deployed or applied to any hosted project; no paid provider was called.

## Verification log

- 2026-09-22: Written by the F2P wire-in session; every count above is quoted from the command logs of this run.
