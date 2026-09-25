# CERTIFY-WIRING — E1C wiring requests into certify.py (toward E4C)

- **Base:** `e56dbf19` (`claude/consumer-v1`). **Implementation head:** `5921d956`, branch `codex/certify-wiring`, worktree `.claude/worktrees/codex-certify-wiring`. Not pushed.
- **Source:** the wiring requests of [E1C-2531dc4.md](E1C-2531dc4.md).
- No network, hosted DB, pilot box, AWS or Docker was used. Every test is in-process; the one bench call is `--validate-only`, which sends no request.

## Per-request status

| E1C request | Status |
|---|---|
| (1) certify passes `--unprofiled certify` to remote cells | **Superseded by (2), not applied.** The merged certify.py did not pass it, so a non-loopback remote cell exited 2. A loopback box target (`127.0.0.1:8001`) was allowed to start, because bench treats loopback as local, but its summary was INVALID/unprofiled. Remote cells now always run profiled, so bench's `certify` opt-out has no caller left. E1C's one-line follow-up flip can now land. |
| (2) per-cell `--profile` / `--key-inventory`, and `rung_verdicts` FAILs when validity is not VALID | **Done** (`5921d956`). |
| (3) E1B-protocol §5 items 9–11, and the §3 note that hosted cells need a profile and inventory | **Already done** on base, in coordinator commit `bba27b02`. The file lives at `models/marlin2b/results/E1B-protocol.md`, not `research/plan/evidence/e/`. It was not touched. |

## What changed (`tests/integration/backend/certify.py`)

**New flags.** certify.py takes `--run-profile <infrx.run-profile/1 base>` and `--key-inventory <sanitized id prefixes>`. `remote_target` records both paths. Only paths are recorded; no key value is.

**`profile_blocked(target)`.** A remote target needs both files, readable, and the base profile's `schema` must be `infrx.run-profile/1`.
- If they are missing, the four bench cells (`e4b.a.dataset-resume`, `e4b.b.envelope`, `e4b.b.soak`, `e4b.b.overload`) are recorded **PENDING, owner `PROFILE`**, with detail `BLOCKED: …`. `PROFILE` is a new owner in the closed `OWNERS` vocabulary.
- No client runs in that case, and the report cannot exit 0.
- `bench_argv` raises `Blocked` for such a target, as a second guard.

**`cell_profile(...)`.** It stamps the coordinator's base profile per cell and writes `<workdir>/<cell>-profile.json`.
- **Stamped by certify:** `identity.run_id` (base plus `-<cell>`), `workload.dataset_version` (already per cell), `manifest_sha256` (the manifest certify hands to bench; the dataset cell uses its within-cap copy), `seed`, `forms`, `max_tokens_mix`, and `measurement.arrival=open-loop` with `rate_per_s`.
- **Kept from the coordinator, never loosened:** identity pins, target and allowlist, bounds, spend, item ids, `expected_invalid`, `profile_class` and cleanup.
- bench validates the stamped profile. If it does not cover the cell, bench exits 2, and the `client_exit` row records that as a FAIL.

**Validity gate.**
- `bench_summary(path)` reads the last summary line of a cell's `--out`.
- `bench_validity(summary, local)` returns:
  - PASS only when `validity.verdict == "VALID"`;
  - FAIL for any other verdict, or when a remote cell has no summary;
  - UNKNOWN only for a local fake-engine cell whose sole reason is `bench.UNPROFILED`.
- Where the gate applies:
  - `rung_verdicts(..., summary=, local=)` includes it;
  - `envelope_summary` carries it from **every** rung, as it already does for the cap;
  - the soak appends it;
  - overload adds a problem when it is FAIL.
- The dataset drill is **not** gated. Its first run is interrupted by design, so bench reports it INVALID ("interrupted"). The drill's own resume and ledger invariants judge it.

## Commands

| Command (repo root) | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned venv |
| new e1c cases before the implementation (`pytest -k e1c`) | 1 | 3 failed: `ValueError '--key-inventory' is not in list`, `TypeError 'NoneType' …` (no BLOCKED entries), `TypeError rung_verdicts() got an unexpected keyword argument 'summary'` |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_certify.py tests/integration/test_run.py` | 0 | **95 passed** (test_certify 42 = 38 + 4 new) |
| `INFRX_MUTANTS=all apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/backend/test_e4b_mutants.py` (detached) | 0 | **239 passed in 584 s**: 237 mutants killed (224 + 13 new) + 2 list checks |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (924 links across 205 docs) |

## New cases and mutants

**New cases:**
- `test_e1c_a_remote_cell_runs_under_its_own_profile_and_the_key_inventory` runs the real `bench.py --validate-only` on the stamped profile, which must exit 0 with `runnable: true`.
- `…_without_its_profile_or_inventory_is_blocked_never_pass` covers five configurations: none, profile only, inventory only, an absent file, and the wrong schema.
- `test_e1c_a_rung_whose_bench_summary_is_not_valid_fails`.
- `test_e1c_the_soak_and_overload_cells_fail_when_bench_calls_them_invalid`.

**Existing cases adjusted:** `rung_verdicts` callers pass `summary=VALID_CELL, local=False`. The OWNERS set now includes PROFILE. The remote `bench_argv` and `main` cases supply a profile and an inventory.

**New mutants, all killed:** `profile_dropped_from_argv`, `profile_not_stamped_per_cell`, `profile_bounds_loosened`, `unprofiled_remote_cells_run`, `bench_argv_starts_unprofiled`, `profile_schema_unchecked`, `validity_gate_removed`, `validity_invalid_is_pass`, `remote_unprofiled_excused`, `missing_summary_excused`, `other_rungs_validity_ignored`, `soak_validity_unwired`, `overload_validity_unwired`.

**Anchors moved:** `other_rungs_cap_ignored`, `remote_run_measured_by_default`, `retries_hide_refusals` and `soak_gateway_unwired`.

## Open issues / for the coordinator

1. **Box runs need two files.** A box run now needs `--run-profile` and `--key-inventory`. The base profile's allowlist must name the target host, `127.0.0.1:8001` on the box, and its `target.path` must match it (`direct-gateway`). Its bounds must cover the largest cell: the 4 h soak (`max_duration_s` of at least 14400, plus `max_requests`) and the 32-burst.
2. **Overload profile class.** The overload cell uses the base `profile_class`. If the coordinator declares P4, bench refuses a non-edge target (S3 F5), so an edge overload needs its own run with a public-edge base. This is not stamped by certify, on purpose.
3. **E1C follow-up flip.** Making `--profile` mandatory for a non-local target, and dropping `UNPROFILED_OPT_OUT["certify"]`, is E1C's change in `models/marlin2b/bench.py`. It is now unblocked.
4. **No tracker update JSON.** The E4C lane is not this lane's to move out of queued, and `queued → review` is a rejected transition. There is also no support-lane id for this wiring.

## Remaining effort

0 h on this lane. Optimistic 0 / likely 0.5 / pessimistic 2 h for coordinator review and merge. Confidence medium, based on the size of the diff and the green suites.
