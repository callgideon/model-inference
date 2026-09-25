# E1C follow-up PROFILE-CREDIT-CAP (P-24 gap): a run-profile spend cap in CREDIT

- Base `b0858371`, implementation head `54b3733` on `codex/profile-credit-cap` (evidence commit follows).
- Gap closed: `research/plan/evidence/coordinator/2026-09-25-inputs-decisions-draft.md` §P-24 "Required amendment for a CREDIT run".

## Changed paths
- `models/marlin2b/profiles/run-profile.v1.schema.json`: `bounds.spend.currency` enum `["USD","CREDIT"]`; unit-neutral `max_spend`, `outstanding_holds`, `rates.input_per_mtok`, `rates.output_per_mtok`; the legacy `max_usd`, `outstanding_holds_usd`, `rates.input_usd_per_mtok`, `rates.output_usd_per_mtok` stay as deprecated properties.
- `models/marlin2b/runprofile.py`: new `spend_projection(bounds, n)` and `exact()`. `validate()` uses it and derives `spend_currency` plus `projected_spend` (an exact decimal string). The old `projected_spend_usd` float is gone, because its name hard-coded USD.
- `models/marlin2b/profiles/P0-hosted-smoke.template.json`: moved to the new keys (still USD).
- `models/marlin2b/tests/test_profile.py`: 3 new tests; 2 existing assertions moved to the new derived and template keys.
- `models/marlin2b/tests/mutants.py`: e1cp05 retargeted to the new line; new e1cp20 to e1cp23.
- `tests/integration/backend/test_certify.py:1559`: the fixture's spend override now uses `max_spend`/`outstanding_holds`. Without this change it would mix namings on the migrated template and be refused.
- `tests/integration/backend/certify.py`: **unchanged**. `cell_profile` does not stamp any spend key.
- `models/marlin2b/bench.py`: **unchanged**. It does not read the spend fields.

## Design
- **Schema id stays `infrx.run-profile/1`.** Legacy `*_usd` keys are accepted and read as USD, with a deprecation warning, so committed and in-flight profiles keep validating. The change is additive and a legacy profile keeps its old meaning, so a version bump would only force churn.
- **Units are never converted.**
  - Legacy keys are accepted only when `currency` is `USD`. In a CREDIT profile they are refused with "units are never converted".
  - Legacy keys mixed with the new ones in one profile are refused.
  - An unknown currency is refused by the schema enum.
  - A missing cap key is refused with `bounds.spend.max_spend: required`.
- **Projection unchanged.** Per request: `(max_input_tokens_per_request × input_rate + max_output_tokens_per_request × output_rate) / 1e6`. The projection is n × that value plus the holds, compared to `max_spend`.
- **Exact arithmetic.** Every amount goes through `Decimal(str(json_number))` (the literal written) before the arithmetic and the cap comparison.
  - Limit, marked in the code: a literal with more than 15 significant digits is rounded by `json` before this step. The fix would be `parse_float=Decimal` if one is ever needed.
- **Target accounting regime.** The client cannot learn the target's accounting unit before the run: validation opens no connection, and bench has no pre-run unit endpoint. The currency is therefore **recorded** in `derived.spend_currency` and in the stamped profile, not checked. certify's `reconcile_problems` already refuses usage recorded outside CREDIT after the run (certify.py:570).
- Blocks keep their earlier meaning. A null rate or cap blocks a paid run, and so do undeclared holds; local or fake runs are not blocked.

## Verified values (tests, exact)
- One request at the E4C bounds (30,720 in / 1,024 out, 400 / 1,200 CREDIT per Mtok) projects **13.5168 CREDIT**.
- 3,600 requests (the soak) project **48,660.48 CREDIT**. That fits the 50,000 cap and is refused at 48,660.47.
- Float edge: 5 × 13.5168 + 0.01 CREDIT of holds is exactly 67.594, which binary floats compute as 67.59400000000001. The run is admitted at a 67.594 cap and refused at 67.5939.
- USD: 3,600 × 0.0033792 = 12.16512 USD under both the new and the legacy naming.

## Commands
| cmd | exit | result |
|---|---|---|
| `make api-env` | 0 | env synced |
| `pytest -q tests/test_profile.py` on a scratch copy with the base runprofile.py, schema and template (fails-before) | 1 | 5 failed, 14 passed: 3 new tests plus the 2 moved assertions |
| `pytest -q tests/test_profile.py` (after) | 0 | 19 passed |
| `make bench-test` | 0 | 110 passed |
| `apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py` | 0 | 112 mutants: 109 killed, 3 controls survived, 0 problems |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_certify.py tests/integration/test_run.py` | 0 | 96 passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (3 lines) |

## Mutants (new, all killed)
- e1cp20: currency ignored (`cur = "USD"`).
- e1cp21: the legacy-USD-in-CREDIT refusal removed (USD read as CREDIT, i.e. a 1:1 conversion).
- e1cp22: `exact()` returns `float` (the cap is compared as a float).
- e1cp23: the mixed-naming refusal removed.
- e1cp05 was retargeted to the new projection line and is still killed.

## Open issues / wiring
- No wiring requests.
- The coordinator still has to commit the E4C base profile (P-24 draft) with `"currency": "CREDIT", "max_spend": 50000, "outstanding_holds": 0, rates 400/1200 (card rc_marlin2b_20260925_launch)` once P-01 is ratified.
- The 40,000 CREDIT adjust in the draft is still coordinator-held.
- Proposed ruling text: "A run profile's spend amounts are in `bounds.spend.currency` (USD | CREDIT) and are compared exactly. The legacy `*_usd` keys are read as USD only, and USD is never read as CREDIT."

## Estimate
- Remaining for this lane: 0 h (review only).
- optimistic 0.25 h / likely 0.5 h / pessimistic 1 h to address review findings.
- Confidence: high. Basis: all listed suites are green.
