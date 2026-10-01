# W6 certify-release (INFRA-02) — evidence

- Lane: certify-release, branch `codex/w6-certify-release`, base `2add8e0a`, code head `2851d83f`. Key `m6` (reserved; no
  docker or PostgreSQL was needed: the touched cases run against stubs). Never touched: the box, AWS/SSM/S3, hosted, Vercel.
- Changed: `infra/rollout/certify-window.sh` (+6/-3 effective), `infra/rollout/go-live-remaining.sh` (deleted, 116 lines),
  `apps/infrx-api/tests/i/test_rollout.py` (+41/-1), `apps/infrx-api/tests/i/mutants.py` (+22: seven mutants, appended as
  its own block before the CERTIFY-WINDOW fix round; the lane rule of 2026-09-26 requires a mutant per new case).
- `infra/rollout/e4c-certify.sh`: unchanged — it carries no pin (`: "${RELEASE:?the release commit}"` at :9).

## What changed

- `certify-window.sh`: `RELEASE=${RELEASE:-$(git rev-parse --verify -q 'origin/main^{commit}' || true)}` (was the
  `d3a99e01…` literal); refuses anything but `^[0-9a-f]{40}$` with exit 2 before any plan or call (DRY_RUN included);
  `MIGRATION_VERSION=${MIGRATION_VERSION:-<newest [0-9]{4}_*.sql in apps/app/supabase/migrations>}` (was `0026`; the
  audit's `ls | tail -1` would read `README.md`, which sorts last today — the glob excludes it). Header documents both.
  CERTIFY_ORG / CERTIFY_KEY_PARAM stay constants (the certify tenant is unchanged; an override is not needed — YAGNI).
- `go-live-remaining.sh` retired: steps h3 w8 w9 w10 w10b w11 user w12 main ran 2026-09-27 on d3a99e01
  (RELEASE-d3a99e01.md:71, session-03:454); its INSTALL_ARGS served the 2026-09-29 window on 41693d5d (session-03:525).
  No step remains; the INSTALL_ARGS line is kept in the README wiring request. 0 references in code/tests.

## Commands (from apps/infrx-api unless noted)

| command | exit | result |
|---|---|---|
| `pytest -q tests/i/test_rollout.py -k certify_window` (new case before the script change) | 1 | RED: `release_is_an_input…` failed (`named == set()`: every step named d3a99e01), 8 passed |
| `pytest -q tests/i/test_rollout.py` (after) | 0 | 26 passed (was 25 at base) |
| `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py -k 'certify_window or well_formed or every_case'` | 0 | 33 passed |
| `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py -k 'certify_window_release or certify_window_migration'` | 0 | 7 passed (the seven new mutants killed) |
| `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py` (whole tests/i list) | 0 | 475 passed, 6 skipped (I-HARNESS-KEY i8 NOT RUN, unrelated cases), 0 survivors, 46m48s |
| `pytest -q tests/i --ignore=tests/i/test_mutants.py --ignore=tests/i/lab*` | 0 | 210 passed, 9 skipped (PG only under INFRX_D_TASK: test_ops_steps, untouched), 1 xfailed |
| repo root: `bash -n infra/rollout/certify-window.sh infra/rollout/e4c-certify.sh` | 0 | |
| repo root: `DRY_RUN=1 bash infra/rollout/certify-window.sh` (RELEASE unset; origin/main = 41693d5d) | 0 | 8 lines name 41693d5de577…, 0 name d3a99e01; MIGRATION_VERSION=0059 on profiles77 and two-tenant-fill |
| repo root: `DRY_RUN=1 RELEASE=abc bash infra/rollout/certify-window.sh` | 2 | `RELEASE 'abc' is not a 40-hex commit …` |

The stubbed dry run (the new case; ssm.sh, aws, git, curl, operator-cli.sh recording stand-ins) shows RELEASE in exactly
prep76, profiles77, certify, e1b, wc7, rel-tree, two-tenant-fill and the window id `e4c-side-<release[:8]>-…`; the default
calls `git rev-parse --verify -q origin/main^{commit}` once and yields the same plan; "", "d3a99e01", 40×"g" and 41 hex
are refused with exit 2 and no plan line.

## Mutants (tests/i/mutants.py, block CERTIFY-RELEASE)

certify_window_release_literal, _release_env_ignored, _release_default_not_main, _release_unchecked,
_release_unanchored, _migration_any_file, _migration_override_ignored — all killed by the new case.

## Wiring requests

1. docs-state (owner of `infra/rollout/README.md`): apply `research/plan/evidence/w6/CERTIFY-RELEASE-WR-infra-rollout-README.patch`
   (`git apply --check` clean at 2add8e0a): §5 states the RELEASE/MIGRATION_VERSION inputs; a verification-log line records
   the retirement, that no step of go-live-remaining.sh remains, and the INSTALL_ARGS for the next 50-install.
2. infra-libs (host-lib.sh): once `infra/rollout/host-lib.sh` lands, `certify-window.sh` sources it and drops its local
   `aws()` (:53) and the venv check (:48); its `say()` tees to `$LOGDIR/window.log` (set `HOST_LOG=$LOGDIR/window.log`
   before sourcing). Not done here (parallel lane; ssm.sh/lib.sh untouched).

## Open items

- Default = origin/main is the box's release only while main is not moved past it: after the operator fast-forwards main
  to the wave-6 tip (decision 8), the pending E4C run must pass `RELEASE=41693d5de57746b7dd1d68e40230db6dcd8c4e20` (or
  whatever the box runs); a mismatch is caught by prep76's w3-checkout line and 77's served-build check (exit 2, nothing written).
- The E4C run itself is operator work (not run here).

## Estimate

Remaining for this lane: 0.25/0.5/1 h (review fixes only), confidence high; basis: one script edit, one case, seven mutants,
full tests/i list green.

## Log

- 2026-10-01T04:50Z: written by the certify-release lane at code head 2851d83f.
