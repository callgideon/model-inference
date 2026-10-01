# W6 makefile-pins (DT-10, DT-11, DT-16 + api-lint/api-typecheck) — f17e8d4

Base `2add8e0a`, branch `codex/w6-makefile-pins`, worktree `.claude/worktrees/codex-w6-makefile-pins`.
Commits: `29c0643a` (DT-10/DT-11), `3fdd62e0` (api-lint/api-typecheck), `f17e8d40` (DT-16).
Key: none. No docker started; no shared key (d1/r2/t2f/l3/l4) used.

## Changed paths

| Path | Lines before → after |
|---|---|
| `Makefile` | 231 → 252 |
| `tests/integration/test_makefile_mutant_lists.py` | new, 99 |
| `tests/integration/mutants.py` | 1600 → 1625 (mpm01–mpm07 + `MP_PIN`) |
| `tests/integration/README.md` | 264 → 298 |

## DT-11 pin (red first) then DT-10

1. `pytest tests/integration/test_makefile_mutant_lists.py -k exactly_once` at 2add8e0a's Makefile: **exit 1**,
   `api-mutants names apps/infrx-api/tests/h/test_mutants.py 2 times, not once` and
   `api-mutants names tests/integration/lab_local/test_mutants.py 0 times, not once` (the two DT-10 findings, nothing else).
2. DT-10: `tests/h/test_mutants.py` dropped from api-mutants' first list (it still runs once, on the own-process
   Lab PG line); lab_local's list placed beside the other `tests/integration/lab_*` lines (its guard: 82 layer-1
   cases + 12 stack cases that skip visibly `NOT RUN` without a kept e3l stack — so it qualifies; it ran nowhere before);
   `~75s` comment replaced by a pointer to the measured table; the stale `console-mutants' tests/v line when V1M
   removes tests/v` sentence replaced (apps/app/tests has no v/).
3. Pin after: **5 passed** (exit 0).

Dedup counts (`make -n api-mutants`, named runner paths): before 92 mentions / 91 unique; after 92 / 92.
Effect (`INFRX_MUTANTS=all pytest --collect-only` of api-mutants line 1): 64 files → 5931 cases before, 63 files → 5892
after; the delta 39 = exactly `tests/h/test_mutants.py`'s 39 cases, which still run on line 2. So every mutant that ran
before runs once now; the one addition is lab_local's 94 cases (`INFRX_MUTANTS=all ... lab_local/test_mutants.py`:
**82 passed, 12 skipped in 223.68s**, exit 0).

The pin's discovery: `apps/infrx-api/tests/**/test_*mutants*.py` + `tests/integration/**/test_*mutants*.py` vs api-mutants,
`apps/lab/tests/**/run-*mutants.mjs` vs lab-mutants; each recipe path is resolved against its segment's `cd`
(`$(API)`, `$(CURDIR)`, `apps/lab`), recipe comment lines name nothing, and every named path must exist.

## Mutants (tests/integration/mutants.py, the E2 runner)

`apps/infrx-api/.venv/bin/python tests/integration/mutants.py --only <id>` — each **killed** (assertion failure,
pristine baseline green, exit 0): mpm01 duplicate undetected, mpm02 omission undetected, mpm03 existence check off,
mpm04 `cd` tracking off, mpm05 comment lines counted, mpm06 Lab runners unpinned, mpm07 tests/integration runners unpinned.
7/7 killed. List size 300 → 307 (`mutants.py --list`).

## api-lint / api-typecheck

Guarded targets in `check` (and `.PHONY`): `api-lint` = `uv run --frozen ruff check infrx deploy tests` when
`apps/infrx-api/ruff.toml` or `[tool.ruff` in pyproject; `api-typecheck` = `uv run --frozen pyright <pkg>` for every
`infrx/*/`, one process each (whole-tree OOM, audit A13), exit 1 if any fails, when `pyrightconfig.json` or `[tool.pyright`.
Today both print `not run - no ... config in apps/infrx-api yet (api-L5 owns it)` and exit 0. With the config present
(scratch copy, `uv` stubbed): ruff line runs; pyright runs per package; one failing package → make exit 2.
api-L5 has not sent its exact lines (no commits on `codex/w6-api-L5` at handback); the coordinator replaces the two
recipes with them if they differ. Note: ruff/pyright are not in `uv.lock` today — api-L5's dev-group + lock regen.

## Checks

| Command | Exit | Result |
|---|---|---|
| `pytest tests/integration/test_makefile_mutant_lists.py` | 0 | 5 passed |
| `pytest tests/integration/{test_makefile_mutant_lists,test_run,test_harness,test_preflight,test_fake_vllm,test_lab_package_isolation}.py` | 1 | 182 passed, 2 failed — both pre-existing on 2add8e0a, untouched files: `test_run::test_every_mutant_anchor_occurs_as_declared_on_the_checkout` (`e3bm62` anchors `ROUTERS = (health, models, ingress, uploads, jobs, metrics)`, stale since f4bceeba G4F; app.py is api-L2's) and `test_preflight::test_a_green_consumer_local_composition_passes_on_its_own_namespace` (gates.API_SUITES is a fixed 9-tuple, the test discovers every tests/<dir>) |
| `make -n check` | 0 | 15 prerequisites, 0 duplicates |
| `make api-lint api-typecheck` | 0 | both "not run" (no config yet) |
| per-target timing run (below) | 0 each | all 13 targets exit 0 |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (963 links, 435 documents, ledger current) |

Not run: `make api-test`, the whole `make api-mutants` (they need d1 and r2/t2f/l3/l4, other lanes' keys; host load 25–33),
the whole `make check`. Their timings come from the logs (README table, labelled).

## DT-16 timings (tests/integration/README.md "Measured wall-clocks")

Raw: `research/plan/evidence/w6/MAKEFILE-PINS-f17e8d4-timings.txt` (sequential, 03:45–04:16Z, load 25–33/16 cores).
bench-test 26.0 s (121 passed); lab-test 2.8 s (268: 254 pass, 14 skip); lab-lint 9.6; lab-typecheck 9.8; lab-build 19.3;
lab-mutants 838.3 s; console-test 5.8 s (650: 591 pass, 59 skip); console-lint 15.6; console-typecheck 13.6;
console-mutants 871.1 s; console-built 23.9 s. From logs: api-test 1 h 28 min (LAB-DEPLOY-PREP-24512389, 2026-09-29);
api-mutants ≥ 2 h 42 min (G-GATES-9b21339, 2026-09-27, pre-Lab lists) + lab_local 224.6 s measured.

## Open items

- `e3bm62` stale anchor (pre-existing): re-point after api-L2 settles app.py's ROUTERS line (api-L2 or the coordinator).
- Console runners are not pinned (apps/app/tests/c/run-mutants.mjs is named twice by design: `--self-test` then the run);
  extending the pin to console-mutants needs a self-test-aware count — not in this brief.
- CLAUDE.md commands / ENVIRONMENT.md timing line (audit DT-16 wording) belong to docs-state: wiring request in the handback.

## Fix round (review of df1266cf; fix at 8a8e9c17)

**0-MP-RV-1 (major) — fixed.** The pin found Python runners only by the `test_*mutants*.py` glob, so
`apps/infrx-api/tests/d/test_signup.py` (the `INFRX_MUTANTS=all` runner for `tests/d/signup_mutants.py`) could be
dropped from `api-mutants` silently. A scan of every `test_*.py` reading `INFRX_MUTANTS` finds it as the only runner
outside the glob (the pin itself and `tests/integration/test_preflight.py` read it without running a list).
- Test first: `test_a_runner_outside_the_glob_is_found_by_reading_infrx_mutants` (drop ` tests/d/test_signup.py`
  → reported 0 times) — red on df1266cf's pin: `1 failed, 5 passed`.
- Fix: `runners()` adds every `test_*.py` under the Python bases that reads `INFRX_MUTANTS`, minus `NOT_RUNNERS`
  (pin, test_preflight.py). `pytest tests/integration/test_makefile_mutant_lists.py`: `6 passed`.
- Reviewer's repro at 8a8e9c17 (`sed -i 's# tests/d/test_signup.py##' Makefile`): the pin now fails
  (`5 failed, 1 passed`); Makefile restored with `git checkout Makefile`.
- Mutants: `mpm08` (content scan off: `and False}`) **killed**; mpm01–mpm07 re-run, all killed. 8/8.
  List size 307 → 308 (`mutants.py --list`).
- Side check: `pytest tests/integration/test_preflight.py` 58 passed, 1 failed
  (`test_a_green_consumer_local_composition_passes_on_its_own_namespace`: `ModuleNotFoundError: infrx` at its
  in-test import; file untouched by this lane, not caused by the fix).
