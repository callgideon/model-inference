# F2P — product-v2 contracts, additive design phase

| Field | Value |
|---|---|
| Task and status | **F2P, additive phase only — implemented.** Items 1–6 of `.claude/handoff/wave3/F2P.md` are encoded, tested and mutation-checked. **Item 7 (wire-in) is NOT done** and is not started: the PHASE RULE of `F2P-prep.md` allowed new files only. Nothing in the tree imports `contracts.v2` yet, so this is *implemented*, never *integrated*, and it does **not** mark A1 or D1R implemented. |
| Source | Base `ec6c5483f472ee84e10d48ca3c51ba474b4efed2`; implementation head `ee4b7ab` (this report is appended after it, so the committed report SHA differs by one); branch `codex/f2p-v2-contracts`; worktree `.claude/worktrees/codex-f2p`. Not rebased onto F2R (unmerged at the time of writing) and not onto E2R's `c23d804`. |
| Owner/session | Opus 5 (1M context), session `015Tix7PsULvyhV5Lw6chMwh`, coordinator-delegated. |

## Commits, one per brief item

| Commit | Item |
|---|---|
| `d5de896` | 2 — `Credit` / `Usd` / `ProviderUsd` as three distinct denominations, both languages |
| `74f77f2` | 3 — credential → wallet resolution (lands the v2 records/ports/fixtures base and the exported conformance suite, because the resolution is only meaningful against the rows it reads) |
| `832a29a` | 4 — admission pins |
| `e23d2ea` | 5 — the individual signup grant; legacy USD kept apart |
| `b6770ce` | coordinator inputs: r1 R62 and S2M's measured Marlin artifact identity |
| `62aade5` | 6 — provider roles and source-purpose grants |
| `282899e` | 1 — the v1→v2 field map, both languages, and the two doc appendices |
| `ee4b7ab` | mutation lists for every v2 invariant, both languages |

## Environment

| Component | Version | Classification |
|---|---|---|
| OS / kernel | Linux 7.0.0-1010-aws | local development worktree |
| Python | 3.12.3 (`apps/infrx-api/.venv`, `uv sync --frozen --all-extras`) | local |
| uv | 0.11.8 | local |
| pydantic | 2.13.5 / pydantic-core 2.46.5 (from `uv.lock`, unchanged) | local |
| pytest | 8.4.2 | local |
| Node | v22.23.1 (type stripping unflagged; `engines.node >= 22.18`) | local |
| pnpm | 9.15.9, `pnpm install --frozen-lockfile` (no manifest or lockfile edited) | local |
| TypeScript | 5.9.3 | local |
| Docker | 29.6.2 available; **no container created or touched by this task** | n/a |
| GPU / model / serving | none used. No engine, no vLLM, no pilot host, no paid provider, no cloud call. | n/a |

## Commands, exit status, results

All run from the worktree. UTC 2026-09-22. No seed applies (no randomised suite).

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned environment created in `apps/infrx-api/.venv` |
| `cd apps/infrx-api && uv run --frozen pytest -q --ignore=tests/d` | 0 | `1786 passed, 2 warnings in 194.71s` (baseline on `ec6c548` was `1518 passed`; +268 new) |
| track-first ordering: `uv run --frozen pytest -q tests/g tests/j tests/m tests/q tests/t tests/w tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/contracts` | 0 | `1786 passed, 2 warnings in 171.00s` — identical count to legacy-first, so no order dependence |
| `uv run --frozen pytest -q tests/contracts/v2/` | 0 | `272 passed` (the v2 suites alone) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/v2/test_mutants_v2.py` | 0 | `64 passed in 49.43s` — 54 mutants killed, 4 list-shape checks, 6 runner self-tests |
| `uv run --frozen python tests/contracts/v2/mutants_v2.py --list` | 0 | `54 mutants over 31 named cases` |
| `make console-test` | 0 | `# tests 272 / # pass 272 / # fail 0 / # skipped 0` |
| `make console-lint` | 0 | `2 problems (0 errors, 2 warnings)` — both warnings pre-existing (`conformance.ts`, `fake-services.ts`), neither in a file this task owns |
| `make console-typecheck` | 0 | `next typegen` then `tsc --noEmit`, no output |
| `make console-mutants` | 0 | `88 mutants: 88 killed by a named declared case, 0 survived, 0 stale, 0 runner errors, 69.9s` (the pre-existing lists; the v2 runner is **not** in this target — see integration_requests) |
| `cd apps/app && node tests/contracts/v2/run-mutants-v2.mjs --self-test` | 0 | `5/5 self-tests as expected` |
| `cd apps/app && node tests/contracts/v2/run-mutants-v2.mjs` | 0 | `23/23 mutants killed` |
| `make bench-test` | 0 | `40 passed in 4.46s` |
| `make api-mutants` | **not run as the canonical target** | it includes `tests/d/test_migration_mutants.py`. Per the coordinator's interim rule the equivalent run excluding tests/d was used instead (see below). |
| all mutation lists except tests/d: `INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/m/test_mutants.py tests/q/test_mutants.py tests/j/test_mutants.py tests/w/test_mutants.py tests/t/test_trace_mutants.py tests/g/test_mutants.py tests/contracts/v2/test_mutants_v2.py` | 0 | `1019 passed in 1396.28s (0:23:16)` — every pre-existing track list plus the new v2 list, with no survivor and no runner error |
| `make check` | **not run as one target** | `api-test` and `api-mutants` both include `tests/d`. Every other component of `check` was run individually and is listed above. |
| `make integration INTEGRATION_ARGS="--layer 1 --canary"` | **not run** | Not a skip being counted as a pass: this task touched no file under `tests/integration/`, and `tests/integration/compose.yaml` pins project `infrx-e2` with fixed `container_name: infrx-e2-*` and fixed host ports. The common brief reserves that stack for the E2R lane's own checkout and forbids touching containers this task did not create. E2R's per-process labelling (`c23d804`) is not in this base. |
| `tests/d` (and its migration mutants) | **not run (harness collision, E2R)** | the coordinator's interim rule; the lift to that rule requires a rebase onto `c23d804` or later, which this branch has not done (base is `ec6c548`). |

## Requirement coverage

Case ids are the exported conformance case names in
`infrx/contracts/conformance/v2_contracts.py`, run by
`tests/contracts/v2/test_conformance_v2.py::test_the_fakes_pass_every_v2_case[<case>]`.
31 cases over six oracles: split_contract 5, credit_units 5, credit_identity 7,
credit_grant 2, credit_rate 7, lab_access 5. Every one is covered by at least one
mutant (`test_every_v2_case_is_covered_by_a_mutant`).

### SPLIT-CONTRACT

| Test id | Exact invariant | Killing mutant |
|---|---|---|
| `split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint` | A private dev deployment is not in the public catalog AND the pin refuses it when handed the row directly; a provider credential pointed at another endpoint or belonging to another provider is also refused. Each layer is asserted separately. `not_found` throughout, never a 403 that confirms the artifact exists. | `catalog_lists_private_dev_publicly`, `pin_ignores_the_credentials_endpoint_scope`, `pin_ignores_the_credentials_provider` |
| `split_contract__a_provider_credential_cannot_borrow_another_endpoint` | Endpoint scope is part of the credential; the catalog answers nothing for a foreign endpoint. | `catalog_ignores_the_endpoint_scope` |
| `split_contract__an_internal_v1_payload_is_refused_not_upgraded` | `schema_version != 2` is a refusal, not a silent upgrade; a v1 USD `PriceSnapshot` has no conversion to a CREDIT rate card. | `a_v1_payload_is_silently_accepted`, `a_v1_price_snapshot_converts_to_credit` |
| `split_contract__the_v1_model_revision_string_is_unchanged_r62` | r1 R62: `model_revision` keeps `<public_model_id>@<revision>`, identical across the v1 request, the pin and the serving revision; the artifact identity is the serving revision's (commit `fd111fca…`, two shard digests); `digest_source` is `served_bytes` and `image_is_pinned` is False. | `model_revision_loses_its_revision`, `digest_provenance_claims_upstream_confirmation`, `an_unpinned_runtime_image_reads_as_pinned` |
| `split_contract__the_surface_carries_one_reviewed_version` | ONE reviewed identifier (`contracts-v2.0`) for the whole changed surface, and the fixture base states it. | `two_surface_versions` |

### CREDIT-UNITS

| Test id | Exact invariant | Killing mutant |
|---|---|---|
| `credit_units__mixed_unit_arithmetic_is_refused_by_construction` | `+`, `-` and every ordering across two denominations raise `TypeError`; `==` is False, never an error; no unit can be cast into another; `raw(unit)` refuses a foreign unit. | `units_are_interchangeable`, `raw_answers_any_unit` |
| `credit_units__a_mixed_history_totals_per_unit_and_never_once` | A mixed legacy/CREDIT history answers `{CREDIT: …, USD: …}`, the legacy value is byte-exact, and a legacy row relabelled CREDIT is refused. | `history_totals_collapse_into_one_unit`, `a_legacy_row_is_read_as_credit` |
| `credit_units__a_legacy_row_invents_none_of_the_new_fields` | An old row's rate card, serving and deployment ids read back absent; a row that never had a price version keeps `None`; a CREDIT row may not carry `price_version`. | `a_credit_row_may_carry_a_legacy_price_version`, `the_projection_invents_a_price_version` |
| `credit_units__a_wrong_unit_or_unpriced_rate_card_cannot_exist` | Unit and meter are literals; a USD card, an unknown meter, a negative rate, an unapproved status or a blank approver cannot be constructed. | `a_negative_credit_rate_is_accepted`, `an_unapproved_rate_card_is_accepted` |
| `credit_units__a_nonzero_legacy_balance_is_a_hold_not_a_conversion` | A nonzero legacy USD balance must be flagged `rollout_hold`; the balance DTO keeps CREDIT and USD in separate typed fields. | `a_nonzero_legacy_balance_needs_no_hold` |

### CREDIT-IDENTITY

| Test id | Exact invariant | Killing mutant |
|---|---|---|
| `credit_identity__a_consumer_credential_resolves_its_own_user_wallet` | The wallet owned by the credential's own `user_id`, bound to that user's personal org; `available` is total − reserved. | `available_ignores_reservations` |
| `credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet` | Zero-initialised provider wallet, no signup entitlement, no individual owner; a consumer wallet and a rival provider's wallet are both `Forbidden`. | `a_provider_wallet_claims_a_signup_entitlement`, `a_provider_credential_spends_any_wallet` |
| `credit_identity__no_request_field_can_select_a_wallet` | `AuthContextV2` has no wallet field and refuses one; the v1 public request has none either. | `a_request_may_name_a_wallet` (`extra="forbid"` → `"ignore"`) |
| `credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback` | A wallet with another owner or another personal-org binding is `Forbidden`; a missing wallet is `NotFound`, never a fallback. | `a_credential_spends_another_users_wallet`, `the_personal_org_binding_is_not_checked`, `a_missing_wallet_resolves_to_something` |
| `credit_identity__an_operator_credential_spends_no_wallet` | An operator credential resolves nothing spendable. | `an_operator_credential_spends_a_wallet` |
| `credit_identity__a_provider_wallet_has_no_grant_and_no_transfer` | A provider wallet cannot receive the grant; the ledger vocabulary is exactly four kinds and none is a transfer; a signup entry on a provider wallet cannot be recorded. | `a_provider_wallet_receives_the_signup_grant`, `a_transfer_kind_exists`, `a_provider_wallet_may_carry_a_signup_entry` |
| `credit_identity__campaign_and_membership_never_reset_the_grant_key` | Two issuances with different campaign versions, evidence and operation ids produce the SAME key. | `the_grant_key_includes_the_campaign` |

### CREDIT-GRANT

| Test id | Exact invariant | Killing mutant |
|---|---|---|
| `credit_grant__the_grant_is_exactly_ten_thousand_credit_once` | `+10000.00000000` CREDIT, the wallet, the verification evidence and one ledger operation, produced as one operation; a wrong amount or a blank evidence reference is refused. | `the_grant_amount_changes`, `an_unverified_grant_is_accepted` |
| `credit_grant__the_grant_lands_only_in_the_individuals_own_wallet` | A grant cannot be pointed at another user's wallet. | `the_grant_lands_in_another_users_wallet` |

### CREDIT-RATE

| Test id | Exact invariant | Killing mutant |
|---|---|---|
| `credit_rate__admission_pins_model_serving_deployment_and_rate_card` | Acceptance freezes model → deployment revision → serving version + rate card version + policy version; a card that prices another version cannot be attached. | `an_admission_may_carry_a_foreign_card`, `an_admission_may_price_another_deployment` |
| `credit_rate__a_rate_published_after_acceptance_does_not_move_the_job` | A doubled published rate leaves the admitted job settling at `9.97600000`, and its idempotent replay identically; a *fresh* request does see the new rate. | `the_pin_hardcodes_a_rate_card_version`, `publishing_a_rate_does_nothing` |
| `credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job` | After the alias moves, the settlement keeps the admitted serving and deployment revision. | `the_settlement_records_the_wrong_serving_revision` |
| `credit_rate__an_unknown_private_or_unpriced_model_is_refused` | Unknown and private-to-others are `not_found`; unpriced is `invalid_request` with "no approved CREDIT rate card". | `an_unpriced_deployment_is_admitted` |
| `credit_rate__a_caller_supplied_price_or_identity_is_refused` | The public request shape refuses a price, rate card version, serving id or price snapshot; `pin_admission`'s parameter set is exactly the six trusted rows. | `pin_admission_takes_a_caller_rate_card` |
| `credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once` | At a sub-1e-8 cost (0.001 CREDIT/M, one token = 1e-9) the hold rounds UP to `0.00000001` and the charge rounds half-up DOWN to `0.00000000`; 5e-9 → `0.00000001`, 4e-9 → `0.00000000`; usage beyond the envelope is a platform incident. | `the_hold_rounds_like_a_charge`, `the_charge_rounds_up`, `a_settlement_may_exceed_the_hold` |
| `credit_rate__unknown_usage_is_never_settled` | Only authoritative usage settles a debit. | `settle_ignores_certainty` |

### LAB-ACCESS

| Test id | Exact invariant | Killing mutant |
|---|---|---|
| `lab_access__provider_ownership_alone_yields_no_customer_payload` | `read_customer_content` is in no role's capability set; with a current grant the same read is allowed; with no grant it is denied, not defaulted. | `a_role_grants_customer_content`, `content_access_needs_only_a_membership` |
| `lab_access__a_revoked_grant_blocks_access_immediately` | Revocation denies at once; the pre-revocation snapshot is audit evidence (version changed), and the port raises `Forbidden`. | `revocation_is_ignored` |
| `lab_access__an_expired_grant_and_a_rival_provider_are_refused` | Expiry, a rival recipient, an out-of-scope model and a rival provider with no membership are all refused. | `expiry_is_ignored`, `the_recipient_provider_is_not_checked` |
| `lab_access__each_purpose_is_a_separate_permission` | A grant for `provider_sharing` authorises none of capture / external_judging / training, and no other category. | `the_purpose_is_not_checked`, `the_category_is_not_checked` |
| `lab_access__roles_default_deny_and_a_viewer_reaches_nothing` | A viewer has aggregate health and nothing else; a revoked membership and a foreign provider permit nothing; a developer cannot propose publication. | `a_revoked_membership_still_permits`, `every_role_permits_everything` |

### Record-level and fixture-level suites

| File | Count | What it adds beyond the conformance cases |
|---|---|---|
| `tests/contracts/v2/test_money_units.py` | 31 | distinct-type/abstract-base checks, the full out-of-domain input table, immutability, the identical-API check across the three units, and the "no conversion-shaped name exists" scan |
| `tests/contracts/v2/test_identity_v2.py` | 19 | the wallet ownership exclusive-or and every broken variant, audience/identity coherence, `resolve_wallet`'s signature, and that v1's `AuthContext` is untouched |
| `tests/contracts/v2/test_admission_v2.py` | 19 | the pin/card mismatch matrix, the public-shape refusal table, `settle`'s source read for directory access, the dev/public deployment CHECK, and the policy-version agreement |
| `tests/contracts/v2/test_credit_v2.py` | 19 | the grant-key invariance table, the ledger kind/sign/wallet matrix, the legacy projection, the regime/unit matrix and the derived balance |
| `tests/contracts/v2/test_access_v2.py` | 23 | all 11 role/capability pairs, every grant dimension one at a time, empty-scope fail-closed, retention bounds, and that `current_grant` has no "as of" parameter |
| `tests/contracts/v2/test_fixtures_v2.py` | 81 | every fixture is claimed, byte-identical to `fixtures.build()` and round-trips; the map's three lists are checked against both revisions' live `model_fields`; the provisional rate's documented arithmetic |
| `tests/contracts/v2/test_parity_v2.py` | 21 | 11 v2 enums plus units/regimes compared against the TS `as const` lists, the surface/schema version, the grant amount, the meter, the whole role/capability table, the absence of conversion in both languages, and that no fixture copy exists in the console |
| `tests/contracts/v2/test_conformance_v2.py` | 34 | the 31 exported cases, oracle-name and docstring checks, the exported runner, fresh-harness isolation, and Protocol conformance of the fakes |
| `apps/app/tests/contracts/v2/money-units.test.ts` | 8 | the generated parity table classified identically, runtime unit refusal, exact arithmetic and overflow, the `@ts-expect-error` compile-time proof, and credits displayed without `$` |
| `apps/app/tests/contracts/v2/types.test.ts` | 9 | the committed fixtures read as the console DTOs, available recomputed, legacy statement beside the total, per-unit totals with mismatch refusal, the provisional label, and the role/grant/both-halves matrices |

## Failure drill

No durable service, database or queue is involved, so the drill is the mutation
run itself: each of the 54 Python and 23 console guards was removed one at a time,
in a throwaway copy, and the named case was required to fail.

- **Injection point:** a single textual edit to `infrx/contracts/v2/{records,ports,money_units,fixtures,__init__}.py`, `conformance/v2_fakes.py`, `apps/app/lib/contracts/v2/{records-equivalent}`; declared in `mutants_v2.py` / `mutants-v2.json`.
- **State before/after:** none. Both runners copy to `mkdtemp`, apply the edit there, and never write inside the worktree (`test_every_mutant_targets_the_v2_surface` also refuses a mutant outside `contracts/v2/`).
- **Duplicate/retry behaviour:** the CREDIT-RATE case settles the same admission twice (`replayed=True`) and asserts an identical charge.
- **Runner honesty:** six Python and five console self-tests produce each outcome deliberately — a syntax error and an import error are `broken_runner`, a no-op edit `survived`, a lethal edit under the wrong case name is not a kill, a missing anchor and a case-less mutant are `misdeclared`/runner-error. `test_a_known_lethal_mutant_is_killed_for_the_right_reason` is the positive control and asserts the summary says `1 failed`.
- **Cleanup:** temporary directories removed (`--keep` is opt-in); no Docker container created, so none to remove.

### Three guards deleted because no single-edit mutant could kill them

Writing the lists exposed three checks that looked like defence and proved
nothing. Each was removed rather than left unkillable, and the comment at each site
records why the invariant still holds:

1. `pin_admission`'s `audience is provider_dev` check — `AuthContextV2` already refuses an endpoint scope on a consumer or operator context, so their `endpoint_id` is `None` and the endpoint equality *is* the audience check.
2. and 3. `resolve_wallet`'s two wallet-kind checks — `WalletRef` validates ownership as an exclusive-or by kind, so a consumer wallet never has a provider owner and a provider wallet never has an individual one; the ownership equalities already refuse the wrong kind, and each remaining check is individually lethal.

This is a deliberate reduction of apparent defence in depth in exchange for
checks that are provably load-bearing. **A reviewer who disagrees should say so:**
restoring the guards means either accepting three unkillable mutants or finding a
credential shape that violates only the removed check, which the record validators
currently make unconstructible.

## Artifacts

| Path | Notes |
|---|---|
| `apps/infrx-api/infrx/contracts/fixtures/v2/*.json` (27 files) | generated by `uv run --frozen python -m infrx.contracts.v2.fixtures --write`; regenerating is a no-op (`wrote nothing`) and `test_fixtures_v2.py` compares bytes |
| `research/plan/01a-contracts-v2-map.md` | prose map, §7 the wire-in file list, §8 the 14 proposed rulings, §9 the pending inputs |
| `research/plan/06a-database-map-v2.md` | record → relation binding and the constraint carrying each invariant; no SQL, no filename reserved |

No credentials, customer prompts, signed URLs or private content appear in any
committed file. The only external identifiers are the public HF repository name,
its commit and the served-bytes digests S2M already published.

## Limits

1. **Item 7 is not done.** Nothing imports `contracts.v2`; the v1 fakes, ports, config, `types.ts`, `services.ts` and both mutant Makefile targets are untouched. The exact file list is `01a-contracts-v2-map.md` §7 and is repeated in the handback.
2. **The v1 read projection/upgrade test is only half done.** `project_v1_usage` proves the *shape* (old rows read back with the new fields absent). It has never seen a row produced by migrations 0001–0005; that test belongs to the wire-in phase with D1R's fixtures.
3. **P-01 pending.** The Marlin rate card is a fixture, labelled `provisional - P-01 pending` in `approved_by`. Its derivation is documented in `fixtures.py` and `01a` §9. It is not a price, and it fixes no CREDIT↔USD rate.
4. **W3/I2B pending.** `digest_source = served_bytes`: the registry `.lfs.oid` equality is unverified because the repository is gated. `runtime_image_digest` is absent because `serve.sh` pins a moving tag.
5. **tests/d not run** (harness collision, E2R) and **`make integration` not run** (no integration file touched; the `infrx-e2` stack has fixed names and ports reserved for the E2R lane). `make api-mutants` and `make check` were therefore not run as single targets; every other component was run individually.
6. **The v2 mutant runners are not in the Makefile.** `make console-mutants` and `make api-mutants` do not include them, so a future `make check` would not run them until the coordinator wires them in. They were run directly and both are green.
7. **Two duplicated runners** are marked with `ponytail:` comments and an upgrade path: `mutants_v2.py` repeats ~20 lines of `mutants.run_mutant`'s subprocess call (the classification is imported), and `run-mutants-v2.mjs` exists only because `run-mutants.mjs` hard-codes its entry point. Both should be deleted when F2R item 8's consolidation lands.
8. **No runtime behaviour changed.** The gateway, worker, scheduler, media and state modules are untouched; the 1,518-test baseline still passes unchanged inside the 1,786 total.

## Handback

**Next unblocked task:** F2P's own wire-in phase (item 7), on the coordinator's
go-ahead, rebased on F2R's accepted commit. D1R can start reading
`06a-database-map-v2.md` and the fixture base immediately — it needs no owner-local
edits — but D1R's migrations should land against the wired-in contracts, not the
additive base, so that the v1 projection test has real pre-cutover rows.

**integration_requests** (coordinator-owned files this phase deliberately did not
touch):

1. `apps/infrx-api/infrx/contracts/__init__.py` — add `"v2"` to `_SUBMODULES`.
2. `apps/infrx-api/infrx/contracts/conformance/__init__.py` — export `run_v2_conformance`; add the v2 suite to `SUITES` or a `V2_SUITES` sibling.
3. `Makefile` — `api-mutants` gains `tests/contracts/v2/test_mutants_v2.py`; `console-mutants` gains `node tests/contracts/v2/run-mutants-v2.mjs --self-test && node tests/contracts/v2/run-mutants-v2.mjs`.
4. `research/plan/01-contracts.md` and `06-database-map.md` — link `01a-contracts-v2-map.md` and `06a-database-map-v2.md`.
5. `research/plan/08-contracts-v1-encoding.md` §10 — fold in the 14 proposed rulings (`01a` §8) with coordinator-assigned numbers; R60 was the last one this task read, and R61/R62 arrived mid-task.
6. `research/plan/15-pending-inputs.md` — P-01's "affects" column can now name the exact fixture (`fixtures/v2/rate_card_marlin.json`) and the label that identifies a run that used it.

**Unresolved findings for the coordinator:** the three deleted guards (Failure
drill) are a judgement call that a reviewer may want to reverse; and `make check`
cannot currently be run as one green target from this base because of the tests/d
rule, which the rebase onto `c23d804` resolves.

## Verification log

- 2026-09-22: Written from the commands above at head `ee4b7ab`. Counts are quoted
  from command output, not typed from memory. tests/d and `make integration` are
  recorded as not run with their reasons; neither is counted as a pass.
- 2026-09-22: The cross-track mutation run completed after the report was first
  written and its quoted result was substituted for the placeholder:
  `1019 passed in 1396.28s`, exit 0, over the seven pre-existing lists plus the v2
  list. No other field changed.
