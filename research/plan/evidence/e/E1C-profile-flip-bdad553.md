# E1C-PROFILE-FLIP — `--profile` mandatory for paid targets, certify opt-out removed

- Branch `codex/e1c-profile-flip`, base `b9b7df3e` (CERTIFY-WIRING head), code head `bdad5532`.
- Changed: `models/marlin2b/bench.py`, `models/marlin2b/tests/test_profile.py`, `models/marlin2b/tests/mutants.py`.

## Change

- `UNPROFILED_OPT_OUT = {"smoke": (4, 512)}`. The `certify` entry is gone because certify.py passes `--profile/--key-inventory` to every remote cell since b9b7df3e. `--unprofiled` uses `choices=sorted(UNPROFILED_OPT_OUT)`, so `--unprofiled certify` is now an argparse error: exit 2, `invalid choice: 'certify'`. The help text was updated.
- `unprofiled_refusal`: every remaining cap is a real bound, so the `cap and` guard was dropped. Classification is unchanged. `runprofile.is_local(a)` is true for an injected `--dry-run-transport` or a loopback `--base-url` host; every other target is paid. A paid run with no `--profile` and no valid `--unprofiled smoke` exits 2 in `_run` before `raw_path`/`makedirs`/`open`, so no file is written and no request is sent. The message names `--profile`. `--validate-only` without a profile reports the same refusal and still exits 2.

## Commands (worktree root)

| cmd | exit | summary |
|---|---|---|
| `make api-env` | 0 | pinned env |
| `.venv/bin/python -m pytest -q models/marlin2b/tests/test_profile.py -k certify_opt_out` at b9b7df3e code + new test | 1 | FAIL: `(['--unprofiled', 'certify'], 0)`. The paid unprofiled certify run started and exited 0 |
| same, after the change (`-k "certify_opt_out or unprofiled_run"`) | 0 | 2 passed |
| `make bench-test` | 0 | 107 passed (was 106 + 1 new) |
| `.venv/bin/python -m pytest -q tests/integration/backend/test_certify.py` | 0 | 42 passed. Certify's local fake-engine cells still use the local exemption |
| `.venv/bin/python models/marlin2b/tests/mutants.py` | 0 | 108 mutants: 105 killed, 3 controls survived, 0 errors |
| `mutants.py --only e1cf12/e1cf13/e1cf27/e1cf28` | 0 | each killed |

## Tests

- New test `test_a_paid_run_needs_a_profile_and_the_certify_opt_out_is_gone`. It uses a paid `https://paid.invalid/v1` URL, with every httpx client routed to a fake gateway that counts requests. It checks four cases, `--unprofiled certify` (run and `--validate-only`) and no flag (run and `--validate-only`). Each must give exit 2, send no request or upload, and write neither the `--out` nor the `--raw` file. The certify cases must print `invalid choice: 'certify'`, and the no-flag cases must name `--profile`. The test then asserts that `UNPROFILED_OPT_OUT == {"smoke": (4, 512)}`. Failure oracle: before the change, the certify run completed with exit 0.
- `test_an_unprofiled_run_against_a_paid_target_refuses_to_start` now iterates only `smoke`, because certify can no longer parse.

## Mutants

- `e1cf27` restores the certify opt-out (`"certify": (10**9, 10**9)`). Killed by the new test.
- `e1cf28` makes `unprofiled_refusal` treat every target as local. Killed by the new test and by the old one.
- `e1cf13` has its `before` string updated to the new line without `cap and`. Still killed.

## Coordinator-owned references (reported, not edited)

- `models/marlin2b/results/E1B-protocol.md:71` (the E1C amendment) still says "or `--unprofiled certify` until the E2C wiring passes profiles". That clause is now false and should be struck.
- The same wording appears in `research/plan/08-contracts-v1-encoding.md` R110 and in `research/plan/evidence/coordinator/2026-09-24-session-03.md:85`.
- Historical evidence (`E1C-2531dc4.md`, `CERTIFY-wiring-5921d95.md`) is append-only and left unchanged.
- No smoke command anywhere passes `--unprofiled certify`; the smoke template uses `--unprofiled smoke`.

## Wiring requests

None. Suggested R110 amendment text: "the only exception is an explicit, logged `bench --unprofiled smoke` (at most 4 requests of at most 512 output tokens); `--unprofiled certify` was removed once certify passed profiles (E1C-PROFILE-FLIP)."

## Estimate

Remaining effort is 0 / 0.25 / 0.5 h (optimistic / likely / pessimistic), confidence high. That time covers the coordinator merge and the protocol/R110 wording edit.
