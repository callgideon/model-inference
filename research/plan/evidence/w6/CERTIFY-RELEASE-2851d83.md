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

## Fix round (0-CR-1)

Finding 0-CR-1 (major): RELEASE and MIGRATION_VERSION were re-derived on every resume of a LOGDIR. After a `git fetch` or a
new migration file, one window could plan two-tenant-fill on another release than profiles77. Fixed in
`infra/rollout/certify-window.sh`: `pinned()`, placed just above the window id, writes both values with the existing
`once()` (files `$LOGDIR/release`, `$LOGDIR/migration-version`). A resume with other values exits 2 before any plan line,
naming the LOGDIR's value and the new one. The header states the rule. The README wiring-request patch gains the same
sentence (`git apply --check` clean at this head).

- Test first: `test_certify_window__a_logdir_certifies_one_release_and_one_migration_version` (same LOGDIR: first run with
  41693d5d; a resume with the same values plans; `RELEASE=1111…` exits 2 naming 41693d5d with no plan line; a new
  0027 migration exits 2 naming 0026 with no plan line). It was red before the fix (1 failed) and green after.
  `test_certify_window__release_is_an_input…` now runs its MIGRATION_VERSION=0059 override in its own LOGDIR, because
  reusing the LOGDIR is exactly what the pin refuses.
- Mutants (block "CERTIFY-RELEASE fix round"): certify_window_release_unpinned, _migration_unpinned,
  _pin_never_refuses. All three are killed, as are the seven earlier ones (12 selected, 12 passed).
- Reruns: `pytest -q tests/i/test_rollout.py` 27 passed; `pytest -q tests/i` (no lab*, no test_mutants) 211 passed,
  9 skipped, 1 xfailed; `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py` 468 passed, 16 skipped, 0 survivors.
  The skips are the harness's `_not_run` (I-HARNESS-KEY / i8 PG not run). None is in the CERTIFY-RELEASE blocks.
  `bash -n` passes.
- Nothing ran against the box, AWS or hosted (DRY_RUN with stubs only).

## Merge (#78, codex/w5-merge-78)

- Merged at lane head `95f680a8` onto `c56cfce5` (no conflict; `tests/i/mutants.py` auto-merged: the lane's
  CERTIFY-RELEASE blocks — the seven `certify_window_*` mutants and the fix round's three — are a sanctioned append
  (LANE-RULES rule 13), in union with the tip's blocks).
- The README patch applied (lens CR-4): its first hunk at an offset, the log line by hand after merge #76's (the
  patch's context predated the docs-state lines): §5 names the RELEASE/MIGRATION_VERSION inputs and the
  pinned-per-LOGDIR rule; the 2026-10-01 line records go-live-remaining.sh's retirement and the next 50-install's
  INSTALL_ARGS.
- WR-IL-2 applied (host-lib.sh landed at merge #72): `certify-window.sh` sets `HOST_LOG=$LOGDIR/window.log` and
  sources `host-lib.sh` from its own directory; its local `aws()`/`say()` and venv check are gone (`need_venv`), its
  `secret_to_file` is the lib's `ssm_to_file`; it is in `HOST_SCRIPTS` (`tests/i/test_rollout_host.py`). The
  certify_window cases' scratch root copies `host-lib.sh` beside the script (their stub PATH still intercepts aws).
- Lens minors: CR-2 — RELEASE unset with a failing `git rev-parse` exits 2 naming `git fetch origin` with no plan line
  (mutant `certify_window_release_default_fails_set_e` drops `|| true`); outside the repo root (no numbered migration)
  the window exits 2 `run from the repo root (no numbered migration found)` instead of certifying `[0-9` (mutant
  `certify_window_no_migration_guard`); both in `test_certify_window__an_unreadable_origin_main_or_no_migration_refuses_before_any_plan`.
  CR-3 — the header states the coupling: the MIGRATION_VERSION default holds only while the checkout's newest
  migration is hosted's applied version; otherwise pass it explicitly.
- Operational note: RELEASE must be passed explicitly once main is fast-forwarded past `41693d5d`
  (`RELEASE=41693d5de57746b7dd1d68e40230db6dcd8c4e20`, or whatever the box serves); the `/tmp/e4c` wrapper sets
  `RELEASE=origin/main` at launch time, which is right only until main moves.
- No ruling is numbered at this merge (none proposed). Nothing ran against the box, AWS or hosted (DRY_RUN and stubs).

## Log

- 2026-10-01T04:50Z: written by the certify-release lane at code head 2851d83f.
- 2026-10-01T05:40Z: fix round (0-CR-1) appended by the certify-release lane.
- 2026-10-01: merge #78 (CR-3, first lens): the whole-list mutant runs' skips (6 at 2851d83f, 16 at the fix round)
  are I-HARNESS-KEY NOT RUN — the lens counts 8 i8-harness mutants (cases in tests/i/test_pooler, test_observe,
  test_privilege_probe, test_rollback_drill) whose copy found the exclusive i8 lock held by another lane; NOT RUN is
  not a kill, and none is in the CERTIFY-RELEASE blocks.
- 2026-10-01: merge #78 (CR-5): a mismatched RELEASE with a bundle makes 76 move the measurement checkout (w3-checkout)
  to that release before 77's served-build check refuses (exit 2); the stop is real but 76 has already acted, so pass
  RELEASE explicitly after main moves.
- 2026-10-01: merge #78 section appended by the coordinator merge lane (codex/w5-merge-78).
