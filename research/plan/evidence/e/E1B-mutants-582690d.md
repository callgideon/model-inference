# E1B-MUTANTS — mutants for the three e1b-window cases (release-gate fix)

- Base: `b640c35c` (RELEASE freeze). Branch `codex/e1b-mutants`, worktree `.claude/worktrees/codex-e1b-mutants`.
- Code commit: `582690d2` (`apps/infrx-api/tests/i/mutants.py` only: +31/-1). No docker, AWS, SSM, Supabase or pilot box used.

## Defect

G2 (`make check`, api-test stage) at `b640c35c`: `tests/i/test_mutants.py::test_every_case_is_covered_by_a_mutant`
failed with "cases no mutant can break" = the three `test_e1b_window__*` cases
(`apps/infrx-api/tests/i/test_rollout.py`), added by E1B-WIRE/E1B-WIRE-2 for `infra/rollout/e1b-window.sh`
without mutants.

Second, latent defect found while fixing it: the mutation copy (`_layout`) did not carry what those cases read
(`models/marlin2b/profiles/E1B-*.base.json`, `bench.py`/`dataset.py` for their parsers, WC-8's
`corpus-synth/manifest.json`). Merely naming the cases would have made all three fail in the copy, so the
list's **pristine baseline** (R83 (b): the union of every case the list names) would refuse **every** tests/i
mutant as `broken_runner`. Fails-before (unextended layout, driver below):
`baseline 3 e1b cases: broken_runner — pristine baseline: the unmutated tree fails the list's own cases
(pytest exit 1): [all three test_e1b_window__* ids]`. The fix adds those four paths to `_layout`
(the profiles directory to the I8 copytree loop; the three files to the E4C-RUNBOOK-2 file loop).

## Mutants (file `../../infra/rollout/e1b-window.sh`; each anchor occurs exactly once)

| mutant | invariant | anchor → edit | case it names (killed) |
|---|---|---|---|
| `e1b_window_order_swapped` | the window runs §7.2's cells in order (WC-1..WC-5, WC-8; WC-7 alone) | `ORDER="WC-1 WC-2 WC-3 WC-4 WC-5 WC-8 WC-7"` → WC-3/WC-4 swapped | `cells_run_in_order_one_container_each_with_only_parser_flags` (test_rollout.py:635, the container-name order) |
| `e1b_window_non_parser_flag` | a cell hands bench.py only flags its parser defines | `--target gateway --model nemostation/marlin-2b --seed 20260922` → `--random-seed` (not a bench.py flag nor an abbreviation of one) | same case (test_rollout.py:650, `{'--random-seed'}`) |
| `e1b_window_certify_live_ignored` | no cell starts while a certify container exists (§7.1 rule 1) | the `awk '$2 ~ /^infrx-certify:/ …' … refuse "$1" "a certify run is live"` line → deleted | `refuses_a_cell_that_would_overlap_or_run_off_the_pinned_engine` (test_rollout.py:688, `('a certify run is live', '')`) |
| `e1b_window_engine_seqs_unchecked` | no cell starts on an engine off the profile's max_num_seqs | `\|\| refuse "$1" "the engine is not at the pinned max_num_seqs $seqs"` → `\|\| true` | same case (test_rollout.py:688, `('not at the pinned max_num_seqs 8', '')`) |
| `e1b_window_wc8_output_discarded` | WC-8's interrupted half keeps its output (E1BW-R2) | `> "$out/$label-interrupted.log" 2>&1 &` → `2> "$out/$label-interrupted.log" > /dev/null &` | `wc8_keeps_the_interrupted_half_and_bounds_its_exit` (test_rollout.py:709, mode `exits`) |
| `e1b_window_wc8_stuck_half_not_killed` | a WC-8 first half that ignores SIGINT is killed after the 120 s bound (E1BW-R3) | `    docker kill "infrx-e1b-$label" > /dev/null 2>&1 \|\| true` line → deleted | same case (test_rollout.py:718, last kill is the SIGINT, not the kill) |

All six die on an assertion (no `dies_by`). The output mutant keeps the log file but sends stdout to
/dev/null, so the case observes an empty log by assertion rather than a FileNotFoundError crash.

## Commands (run in `apps/infrx-api`; exit codes and counts)

1. `make api-env` (venv missing) — exit 0.
2. Fails-before: `uv run --frozen --no-sync pytest -q -p no:cacheprovider tests/i/test_mutants.py -k "well_formed or every_case"` at `b640c35c` — exit 1, `1 failed, 1 passed, 48 deselected` (every_case: the three e1b cases).
3. After: same command — exit 0, `2 passed, 48 deselected`.
4. Each mutant through the shared runner, docker-free (scratch driver: `_SHARED._baseline` over the three e1b cases in the copy, then `run_mutant(dataclasses.replace(m), RUNNER)` for each `e1b_window` mutant) — exit 0:
   `baseline 3 e1b cases: PASS (13s)`; `e1b_window_order_swapped: killed (1 failed, 14 deselected)`; `e1b_window_non_parser_flag: killed`; `e1b_window_certify_live_ignored: killed`; `e1b_window_engine_seqs_unchecked: killed`; `e1b_window_wc8_output_discarded: killed`; `e1b_window_wc8_stuck_half_not_killed: killed (23.7 s)`. **6/6 killed, 0 survived / broken / misdeclared.**
5. The list's pristine baseline in the extended copy (`_SHARED._baseline` over the union of every case `tests/i/mutants.py` names, minus the 6 `i8_stack` cases) — exit 0, `168 cases named; 162 run; PASS (73 s)`: the layout change breaks no other case's baseline.
6. By hand in the worktree (apply one mutant to `infra/rollout/e1b-window.sh`, `pytest -q --tb=line tests/i/test_rollout.py -k <case>`, restore from a saved copy): all six exit 1, `1 failed, 14 deselected`, failing at the lines in the table; script restored (`git diff -- infra/rollout/e1b-window.sh` empty).
7. `uv run --frozen --no-sync pytest -q -p no:cacheprovider tests/i/test_rollout.py` — exit 0, `15 passed` (unchanged file).
8. `uv run --frozen --no-sync ruff check tests/i/mutants.py` — exit 0, `All checks passed!`.
9. `git diff --stat b640c35c..HEAD` — `apps/infrx-api/tests/i/mutants.py` + this file only.

## Not run (lane bound), and what stands in for it

`INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q -p no:cacheprovider tests/i/test_mutants.py -k "e1b_window or well_formed"`
was **not** run: the first selected mutant makes the shared runner compute the list's pristine baseline over
the union of every named case, which includes 6 `i8_stack` cases; with docker usable on this host that fixture
starts the I8 PostgreSQL/PgBouncer stack on 127.0.0.1:55450 — docker and that port are out of this lane's
bounds. Items 4-5 are the same runner code on the same copy layout minus those 6 cases. The coordinator's G2
rerun (`make check`, which runs `INFRX_MUTANTS=all tests/i/test_mutants.py`) is the full proof; expected
there: tests/i list = previous count + 6 killed.

## Open issues

None in scope. Minor, for SWEEP-1: the LANE-RULES amendment of 2026-09-26 should also say that a case reading
a repository file outside `apps/infrx-api` needs that file in its track's mutation-copy layout, or the list's
pristine baseline refuses every mutant (the second defect above).

## Estimate

Remaining for this lane: 0 h (done). Coordinator verification: G2 rerun on the merged tip, ~3.5 h wall
(README §0), confidence high for the tests/i stage.

## Verification log

- 2026-09-26: created (E1B-MUTANTS, Opus implementer).
