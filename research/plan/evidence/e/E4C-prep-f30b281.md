# E4C-PREP: decision-bound preparation for the E4C certificate (P-17, P-18, P-24, PCC-V5)

- Base `56c284cf` (integration head `claude/consumer-v1`, with PROFILE-CREDIT-CAP `93fdab4e` and R132).
- Implementation head `f30b281b` on `codex/e4c-prep`; this evidence commit follows it.
- Commits: `1193a7bb` (PCC-V5), `e83572ed` (P-18 limits and the P-24 base profile), `f30b281b` (P-17 checklist).
- Decisions applied as written: `research/plan/15-pending-inputs.md` "Decisions 2026-09-25", rows P-01, P-17, P-18 and P-24, plus the draft sections §P-17, §P-18 and §P-24 in `research/plan/evidence/coordinator/2026-09-25-inputs-decisions-draft.md`.
- No service, box, hosted DB, AWS or docker was used. No secret was read, printed or committed.

## Changed paths
- `models/marlin2b/profiles/E4C-box.base.json` (new): the P-24 base profile, in CREDIT.
- `models/marlin2b/runprofile.py`: PCC-V5 fix. The finiteness check now applies to floats only. The finiteness message names the rates' path.
- `models/marlin2b/tests/test_profile.py`: 2 new tests. The PCC-V1 test now expects `bounds.spend.rates.input_per_mtok`, the corrected path.
- `models/marlin2b/tests/mutants.py`: new mutant `e1cp25`.
- `tests/integration/backend/certify.py`: `CRITERIA` changes `e2e_p95_s_per_clip_minute` from 45.0 to 90.0 and adds `latency_p95_s` 9.0. `rung_verdicts` judges a `latency_p95` row next to `ttft_p95_short`.
- `tests/integration/backend/test_certify.py`:
  - 1 new test.
  - The rung test's verdict set gains `latency_p95`.
  - Its "dragging" case moves from 8 s to 16 s latency. At the new limit, 8 s is 48 s per clip-minute and passes; 16 s is 96 s and still fails.
- `tests/integration/backend/e4b_mutants.py`: 3 new mutants, plus the `P18` case constant. Every case must be covered by a mutant (`test_every_e4b_case_is_covered_by_a_mutant`).
- `models/marlin2b/results/E4B-protocol.md`:
  - Intro note.
  - §4 envelope row: adds `latency_p95_s`.
  - §5: every `provisional (P-18)` marker becomes `decided (P-18, 2026-09-25; research/plan/15-pending-inputs.md)`. The run3 measured values are cited.
  - New §5 rows: the table of decided limits outside `CRITERIA` (declared supported rate, soak rate, the four recovery bounds, recovery correctness) and the public-edge rule for the box burst.
  - Amendment 6 appended to the verification log.
- `research/plan/consumer-v1/03-operations-and-verification.md`: the new subsection "Acceptance checklist (P-17, decided 2026-09-25)" under `## E4C`. It lists the ten checks with their sources, plus the rule that BACKEND-READY is accepted only when all ten hold, recorded in the E4C evidence and in `gates.BACKEND-READY`.
- Not changed: `tasks.json`, gates, the coordinator's updates overlay (no update file, per the brief), `bench.py`, the schema.

## 1. P-24 base profile
- **Path.** `models/marlin2b/profiles/E4C-box.base.json`, **sha256 `c0d4aa1b9ef3f467ebb17b5d8bf6367a45507bf02685f2ebe8ddd9d279a0d2ff`**.
- **Draft JSON as decided.**
  - Target: path `direct-gateway`, allowlist `127.0.0.1:8001`, `tenant_key_env` `["INFRX_API_KEY"]`, `test_key_ids` `["142c7d81"]` (the draft's recorded test key id prefix, not key material).
  - Request bounds:
    - `max_requests` 3600
    - `max_duration_s` 14700
    - `max_input_bytes` 14000000000
    - `max_input_tokens_per_request` 30720
    - output caps 1024 per request and 3686400 in total
    - `max_drain_s` 600
- **Spend, in CREDIT.**
  - `bounds.spend = {"currency": "CREDIT", "max_spend": 50000, "outstanding_holds": 0}`.
  - Rates `input_per_mtok` 400 and `output_per_mtok` 1200 (the P-01 card `rc_marlin2b_20260925_launch`, 400.00000000 / 1200.00000000 CREDIT per 1M tokens), `as_of` 2026-09-25.
  - JSON integers are numbers to the schema and exact Decimals to the projection.
- **Items.**
  - `workload.item_ids` holds the 64 ids of the committed corpus `models/marlin2b/corpus/manifest.json` (`e1-2026-09-20`). Its sha256 is `386a2d89…095e18`, the one the draft cites, so the count matches the draft.
  - `expected_invalid` holds the four 112 s clips: c012, c025, c038 and c051.
- **Left as the draft's `FILL` placeholders, frozen per run:**
  - identity: `source_sha`, `deployed_sha`, `image_digest`, `weights_sha256`, `processor_sha256`, `migration_version`, `config_version`
  - target: `allowed_fault_targets[1..2]` (the worker and Valkey units), `maintenance_window`
- **Key inventory.**
  - The profile has no inventory block. The schema has no such field, and `runprofile.key_inventory_errors` reads a separate file passed with `bench --key-inventory` or `certify --key-inventory`, shaped `{"active_key_id_prefixes": [...], "taken_at", "source"}` and holding id prefixes only.
  - The profile carries only the schema-required `target.test_key_ids`.
  - The inventory is built at run time from the P-02 dry-run's `keys` block and is not committed here.
- **Validation of the base as committed (soak shape).**
  - Command: `bench.py --corpus models/marlin2b/corpus/manifest.json --subset full --base-url http://127.0.0.1:8001/v1 --target gateway --model nemostation/marlin-2b --rate 0.25 --requests 3600 --seed 20260922 --dataset-version e4c-1 --forms video_b64 --max-tokens 128,512,1024 --retries 0 --profile models/marlin2b/profiles/E4C-box.base.json --validate-only`, run with `MARLIN_API_KEY` and `INFRX_API_KEY` unset.
  - Result: **exit 2**, and the only errors are the 5 pattern-bound FILL identities: `$.identity.source_sha`, `deployed_sha`, `image_digest`, `weights_sha256` and `processor_sha256`, each "does not match". There are no blocks.
  - Nothing was sent, and neither `--out` nor `--raw` was created.
- **Validation once filled.** The FILL values are replaced with well-formed values and the profile is stamped as `certify.cell_profile` stamps it (`rate_per_s` is the cell's rate). The run passes `--key-inventory {"active_key_id_prefixes": ["142c7d81"]}`. Result: **exit 0**, `runnable`, no errors, blocks or warnings. Derived values:
  - `spend_currency` CREDIT
  - `scheduled_requests` 3600
  - `media_bytes` 11,619,893,735, under the 14 GB bound
  - `output_token_ceiling` 1,996,800
  - **`projected_spend` "48660.4800"**
- **Exact projected spend, recomputed from the file.**
  - Per request: (30,720 × 400 + 1,024 × 1,200) / 10^6 = **13.5168 CREDIT**.
  - Soak: 3,600 × 13.5168 + 0 holds = **48,660.48 CREDIT**, under the 50,000 cap.
  - One 0.5 req/s rung (135 requests): 1,824.768 CREDIT.
  - At 3,601 requests the soak is refused: `bounds.max_requests 3600 < scheduled requests 3601`.
  - These figures match the E1C evidence (13.5168; 48,660.48), which was recomputed and not copied.

## 2. P-18 limits (committed before any qualifying run)
- **`CRITERIA` changes.** `e2e_p95_s_per_clip_minute` is 90.0 and `latency_p95_s` is 9.0. `max_failure_rate` 0.01, `ttft_p95_short_s` 6.0, host 512 MiB, GPU 256 MiB and drift 1.5 are unchanged.
- **The `latency_p95` row.**
  - It is computed from accepted rows' `latency_s`, over the same rows as the other tails.
  - `unknown` below `p95_min_accepted` (60), like the existing rows.
  - It is inside `rung_verdicts`, so the envelope's chosen rung carries it: a failing latency fails the envelope (`envelope_summary`).
- **Soak.** The `box` row still reads "half the highest passing envelope rate × 14400". P-18's value, 0.25 req/s = half of the declared 0.5, is now explicit in §5.
  - The runner's derivation is **not** changed.
  - A soak derived from a higher passing rung would be 7,200 or 14,400 requests. The P-24 bound of 3,600 refuses it (bench exit 2, cell FAIL), so the code can only PASS a soak at 0.25 req/s.
  - The new test pins that `max_requests == 0.5 × rate_fraction × 14400` and that every higher rung's derived soak exceeds it.
  - Changing `MATRIX` to a fixed rate would have removed the "no supported envelope rate to soak at" guard, which the 4226315 run1 hit, and would run a 4 h soak after a failed envelope.
- **Recovery bounds** (engine ≤ 300 s, worker ≤ 30 s, Valkey ≤ 30 s, DB/object-store stall → retryable 503/504 within 45 s) are in §5 as decided protocol text, with sources:
  - `research/plan/evidence/coordinator/2026-09-22-session-02.md`:940-942
  - `research/plan/evidence/e/E3C-8406c79.md`:249
  - S3 reconciliation :170
  - They are **not** in `CRITERIA`: the recovery cell (`e4b.b.recovery`) is a suite/drill verdict that reads no number from `CRITERIA`.
- **Box burst.** §5 now says the box overload burst must enter through the public edge (E1B-protocol rule 11; S3 F5; CW-V3). This is protocol text only.

## 3. P-17 checklist
- The ten checks from the P-17 row and draft §P-17 are listed with their sources: gate schema, frozen identities (P-06, P-24 sha), CREDIT regime with the P-01 card, P-18 limits committed before the first qualifying run, the two-tenant journey, exact reconciliation within the P-24 cap, alert/expiry/restore/known-good rollback, no open launch-path P1, published limitations, and cutover kept separate.
- Line references were checked on this base: `tasks.json`:4501-4508 (test_ids), :4521 (acceptance) and :4718-4757 (RV-04/08/09/10); updates/README.md:62 (gates).

## 4. PCC-V5
- `schema_errors`: the check is now `isinstance(value, float) and not math.isfinite(value)`.
- `max_spend = 10**400` validates through `schema_errors` and through `bench --validate-only` (exit 0, runnable). Float Infinity and NaN are still refused with `$.max_spend: must be finite`.
- Cosmetic path fix: `spend_projection` now reports `bounds.spend.rates.input_per_mtok` / `output_per_mtok`. The PCC-V1 test pinned the old string, so it was updated.

## Fails-before (new tests against base `56c284cf` code, run from a `git archive` copy with the new test files)
| test | before | after |
|---|---|---|
| `test_a_huge_integer_spend_validates_and_only_float_inf_or_nan_is_refused` | FAIL: `OverflowError: int too large to convert to float` (runprofile.py:61) | pass |
| `test_a_non_finite_spend_amount_is_a_refusal_not_an_open_cap` (path updated) | FAIL: `bounds.spend.input_per_mtok` ≠ `bounds.spend.rates.input_per_mtok` | pass |
| `test_the_e4c_base_profile_refuses_only_on_its_fill_identities_then_bounds_the_soak` | FAIL: `FileNotFoundError` (no base profile) | pass |
| `test_e4c_the_p18_limits_are_the_runners_and_request_latency_p95_is_judged` | FAIL: `assert 45.0 == 90.0` | pass |
| `test_e4b_an_envelope_rung_judges_the_duration_cap_apart_from_its_failures` (updated) | FAIL: no `latency_p95` row in the verdict set | pass |

## Commands
| cmd | exit | result |
|---|---|---|
| `make api-env` | 0 | env synced |
| `make bench-test` (base, before any change) | 0 | 111 passed |
| `make bench-test` (head) | 0 | **113 passed** (111 + 2 new) |
| `apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py` | 0 | 114 mutants: 111 killed, 3 controls survived, 0 problems (113 at base + e1cp25) |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_certify.py tests/integration/test_run.py` | 0 | **97 passed** (96 + 1 new; the tree test ran on a real git tree) |
| `INFRX_MUTANTS=all apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_e4b_mutants.py` | 0 | 245 passed in 767 s (every e4b mutant killed, the list well formed, every case covered; 3 new) |
| `e4b_mutants.py e2e_limit_reverted_to_provisional request_latency_row_dropped e4c_base_bound_off_the_soak criterion_drifts_from_protocol tail_limit_ignored` | 0 | 5/5 killed. `request_latency_row_dropped` first died by `ValueError` (the runner calls that broken); the test now asserts the row, so it dies by assertion |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (4 lines; 932 local links) |
| `bench.py … --profile models/marlin2b/profiles/E4C-box.base.json --validate-only` (base as committed) | 2 | only the 5 FILL identity errors |
| the same, filled and stamped at 0.25 req/s × 3600 with a matching key inventory | 0 | runnable; projected 48660.4800 CREDIT |

## Mutants (new, all killed)
- `e1cp25` (models/marlin2b): restores `if not math.isfinite(value):`, and the huge-integer test dies.
- `e2e_limit_reverted_to_provisional` (e4b): sets 90.0 back to 45.0.
- `request_latency_row_dropped` (e4b): removes the `latency_p95` tuple from `rung_verdicts`.
- `e4c_base_bound_off_the_soak` (e4b, on the JSON): `max_requests` 3600 → 7200.

## Left for the E4C lane and the operator (not done here, by design)
- **Key inventory**: the file for `--key-inventory`, from the P-02 read-only `credit-transition --dry-run` `keys` block. Coordinator.
  - The two pre-cutover consumer keys must first be revoked or proven revoked (S3 finding 8). Otherwise the run refuses: any active prefix other than `142c7d81` is an error.
- **Funding**: `adjust --user <certify tenant> --amount 40000 --idempotency-key adj-e4c-20260925 --reason "E4C test allocation"`. Operator-held (P-24).
- **Card publication**: `publish-card … rc_marlin2b_20260925_launch --input-rate 400 --output-rate 1200 …` (P-01). Coordinator.
- **FILL values**, frozen at the run:
  - candidate `source_sha` / `deployed_sha`
  - `image_digest`, `weights_sha256`
  - `processor_sha256` (P-06 served bytes)
  - `migration_version` (0025 or newer), `config_version`
  - the worker and Valkey unit names
  - the maintenance-window id
- **Separate profiles** derived from this base:
  - the two-tenant cell: `tenants` 2 and `tenant_key_env` + `INFRX_API_KEY_B` (P-05)
  - the P4 `public-edge` overload profile (CW-V3)

## Open issues (for the coordinator)
1. **FILL strings the schema does not catch.** `migration_version`, `config_version`, `maintenance_window` and the two FILL `allowed_fault_targets` entries only need `minLength` 1, so a profile validates with those FILL strings left in; only the 5 pattern-bound identities refuse.
   - The new test fills all 10 and asserts that no "FILL" remains, but the validator does not enforce it.
   - The P0 template's test has the same gap (its oracle says "refuses until filled").
   - Proposed follow-up (not applied: outside the decision text): make `runprofile.validate` refuse any string value starting with `FILL`, reported by path, plus a mutant.
2. **Envelope rate vs P-18's declared rate.** If rung 1.0 passes its core rows (failure_rate, answered, rejections, client_exit) in E4C, `envelope_summary` judges the P-18 latency rows at 1.0 req/s, not at the declared 0.5. The derived soak (0.5 req/s × 14,400) is then refused by the P-24 bound.
   - Both outcomes fail closed; neither passes silently.
   - An E4C-lane decision is needed before the first qualifying run: either run the box ladder at 0.5 only, or judge the declared rate.
3. **Loopback targets skip blocks.** `runprofile.is_local` treats the loopback base URL `127.0.0.1:8001` as a free target, so bench does not enforce blocks (a missing inventory or missing rates) on box direct-gateway cells.
   - certify's `profile_blocked` still requires `--key-inventory` for a remote target, and inventory mismatches are errors, not blocks.
   - Low severity; stated for the E4C runbook.
4. **Key variable choice.** bench chooses `MARLIN_API_KEY` before `INFRX_API_KEY` when both are set. The box shell must export only `INFRX_API_KEY` (the profile's `tenant_key_env`), or the cell is refused.

## Wiring requests
- None.

## Proposed ruling text (the coordinator numbers it)
- "E4C's predeclared criteria are `models/marlin2b/results/E4B-protocol.md` §5 as amended on 2026-09-25 (amendment 6, P-18) and `certify.CRITERIA`, including `latency_p95_s` 9.0 and `e2e_p95_s_per_clip_minute` 90.0. Loosening either after a qualifying run's start disqualifies that run."

## Estimate
- Remaining for this lane: 0 h optimistic, 0.5 h likely (a review fix round), 1.5 h pessimistic. Confidence high. Basis: all four deliverables are done and checked; only review findings remain.

## Fix round (recheck ACCEPT_WITH_FIXES on 1acf85b4; implementation `fea6a58a`)
- Base of this round: `1acf85b4`. Implementation: `fea6a58a`; this evidence commit follows it.
- Scope: the coordinator's decisions E4P-V1 to V6 and open issues 1, 3 and 4. Nothing wider.

### Changes
- **E4P-V1 (declared rate)**
  - `certify.CRITERIA["declared_rate_per_s"] = 0.5`.
  - `envelope_summary(rungs, declared)`: the climb stops at the declared rate. The supported rate is the declared rung when it and every rung below pass, and only that rung's P-18 rows (TTFT, latency, e2e) are judged. If the declared rung fails, the result is FAIL with no supported rate.
  - `load_cells`: the envelope detail adds `measured_passing_rate_per_s`, the old highest passing rung (measured only).
  - `MATRIX["box"]["soak"]` is `{"rate": 0.25, "seconds": 14400, "sample_s": 30}`: fixed, not derived.
  - The box soak runs only once the declared rung is supported; otherwise it FAILs with "no supported envelope rate to soak at" and does not run.
  - The `tiny` scale is unchanged: no declared rate, and its fixed 2.0 soak runs as before. `rate_fraction` is gone.
- **E4P-V2 (overload burst)**
  - `cell_profile` stamps the overload cell `profile_class` P4, so bench refuses it on any profile but `public-edge`.
  - New `certify --overload-profile <path>`. On a non-local target the burst runs under that profile at `https://<its first allowlist host>/v1`.
  - Without the profile, a box run reports `e4b.b.overload` PENDING on `PROFILE` ("BLOCKED: … pass --overload-profile …") and the burst never runs.
  - An unreadable profile, or one with a wrong schema, is BLOCKED by `profile_blocked`.
- **E4P-V3 (FILL placeholders)**
  - `runprofile.fill_paths` plus `validate`: every string starting with `FILL` is an error, reported by path only, alongside the schema errors.
  - The committed base now fails validation with 15 errors: the 5 pattern errors plus the 10 FILL paths.
  - Filling only the 5 pattern-bound identities still leaves 5 refused. Fully filled, the profile is runnable.
  - The P0 template test and certify's `_base_profile` fill every FILL they carry, so they are unchanged.
- **Open issue 3 (loopback)**
  - `runprofile.is_local(a, profile=None)`: loopback is local only when no profile is given or the profile's `target.path` is `direct-engine`. An injected transport is still local.
  - A profiled `direct-gateway` (or `public-edge`) run on loopback is metered: missing rates, cap, holds or key inventory refuse it (exit 2), as for a remote target.
  - `bench.check_profile` and `dataset.check_profile` pass the loaded profile. The no-profile path is unchanged (e1cf28).
- **E4P-V4 (brief)**, consumer-v1/03 checklist:
  - item 4 now reads "…before the first qualifying run's start timestamp"
  - item 5 now reads "…journey passed"
  - item 7 adds `infra/rollout/steps/85-known-good-box.sh` (P-25)
- **Open issue 4**: one runbook line in consumer-v1/03 §E4C. The box shell exports only `INFRX_API_KEY`; bench prefers `MARLIN_API_KEY` and refuses the cell when both are set.
- **E4P-V5/V6 and V1/V2 protocol text**, in E4B-protocol:
  - §4: the envelope row names the declared rung; the overload row says it is a P4 cell.
  - §5: new `declared_rate_per_s` row; box soak cell `0.25 × 14400 (… fixed; runs only once the declared rung is supported)`.
  - §5 decided-limits table: "Declared supported rate" and "Soak rate" (now in CRITERIA/MATRIX) are replaced by "Refusals within the cap at the declared 0.5 req/s | 0".
  - §5 notes: the runner's failure rate is `< 1 %` over every unanswered attempt, platform and transport alike, which is stricter than P-18's platform ≤ 1 %. The E4C drill record prints each measured recovery time next to its §5 bound with PASS/FAIL (no runner code). The burst rule is restated.
  - Log: "amendment 6, fix round" appended.
- **Tests and mutants**
  - `test_certify`: CELLS test now expects soak `(0.25, 3600)`; the protocol test binds the box soak cell to `MATRIX` (`0.25 × 14400`).
  - The P-18 test now pins `max_requests == soak.rate × seconds == 3600` instead of the removed `rate_fraction`.
  - New mutants: `models/marlin2b/tests/mutants.py` e1cp26 and e1cp27. `e4b_mutants.py`: `soak_without_a_supported_rate`, `declared_rate_not_applied`, `climb_past_the_declared_rate`, `declared_rung_failure_accepted`, `p4_stamp_dropped`, `box_burst_unblocked`, `burst_off_the_edge`, with `soak_at_the_full_rate` retargeted to the new soak line.

### Fails-before (fix-round test files on the `1acf85b4` code, from a `git archive` copy)
| test | before | after |
|---|---|---|
| `test_the_e4c_base_profile_refuses_until_every_fill_is_frozen_then_bounds_the_soak` | FAIL: no FILL errors (`set() == {…10 paths}`) | pass |
| `test_a_profiled_loopback_gateway_is_metered_and_its_blocks_refuse_the_run` | FAIL: the unpriced loopback direct-gateway profile validated (`0 == 2`) | pass |
| `test_e4c_the_box_supports_only_the_declared_rate_and_soaks_at_p18s_fixed_rate` | FAIL: `KeyError: 'declared_rate_per_s'` | pass |
| `test_e4c_the_overload_burst_is_p4_and_enters_through_the_public_edge_or_is_blocked` | FAIL: overload PASS through the direct gateway (silent P1), not BLOCKED | pass |
| `test_e4b_the_load_cells_run_the_declared_shapes…` (updated) | FAIL: `KeyError: 'rate'` | pass |
| `test_e4b_the_protocol_file_states_the_numbers…` (updated) | FAIL: `KeyError: 'rate'` | pass |
| `test_e4c_the_p18_limits_are_the_runners…` (updated) | FAIL: `KeyError: 'rate'` | pass |

A replay on `1acf85b4` in which every box rung passes, using the E4C base with the FILL values frozen:
- Before this round: `supported_rate_per_s` 2.0; the soak was derived at 1.0 req/s × 14,400 requests, and bench refused it (exit 2, `bounds.max_requests 3600 < scheduled requests 14400`); the overload cell reported PASS through the direct gateway as P1.
- After: supported 0.5, `measured_passing_rate_per_s` 2.0, and a soak of 0.25 × 3,600 requests that bench admits (exit 0, projected 48,660.48 CREDIT). The overload cell is BLOCKED without `--overload-profile`, and runs P4 through `https://marlin2b.callbill.ai/v1` with it (bench exit 0).

### Commands (at `fea6a58a`)
| cmd | exit | result |
|---|---|---|
| `make bench-test` | 0 | 114 passed |
| `apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py` | 0 | 116 mutants: 113 killed, 3 controls survived, 0 problems (e1cp26, e1cp27 new) |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_certify.py tests/integration/test_run.py` | 0 | 99 passed |
| `INFRX_MUTANTS=all … pytest -q tests/integration/backend/test_e4b_mutants.py` (detached) | 0 | 252 passed in 689 s (every e4b mutant killed; 7 new and 1 retargeted in this round) |
| `e4b_mutants.py` on the 8 new or retargeted mutants | 0 | 8/8 killed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (932 local links) |
| `bench.py … --profile models/marlin2b/profiles/E4C-box.base.json --key-inventory … --validate-only` (committed base, soak shape) | 2 | 15 errors: the 5 identity pattern errors and the 10 FILL paths |
| the same, fully filled, stamped at 0.25 req/s | 0 | runnable, no errors, blocks or warnings; `projected_spend` 48660.4800 CREDIT |

- The base profile is unchanged: sha256 `c0d4aa1b9ef3f467ebb17b5d8bf6367a45507bf02685f2ebe8ddd9d279a0d2ff`.

### Still open
- None of this round's items. The run-time inputs listed above (key inventory, the +40,000 CREDIT adjust, card publication, FILL values, the two-tenant profile) stay with the E4C lane and the operator.
- A public-edge P4 overload profile must now be supplied with `--overload-profile`.
