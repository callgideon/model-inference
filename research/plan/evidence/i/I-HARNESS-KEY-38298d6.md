# I-HARNESS-KEY — tests/i mutant list green under any caller key (lane i-harness-key, wave LW3)

Base `49393c56`; commits `46d2daa5` (seam test + fix), `38298d65` (anchor fix), then this evidence.
Changed: `apps/infrx-api/tests/i/conftest.py` (+6), `tests/i/test_mutants.py` (+~95), `tests/i/mutants.py` (1 anchor).
`tests/d/pgharness.py` NOT touched (the cause is not there). No product code, no composition root, no Makefile.

## Root cause (not the key)
- The shared runner (`tests/contracts/mutants.py` `_pytest`) runs every copy with a scrubbed env
  (`PYTHONPATH/PATH/HOME/TMPDIR/PYTHONPYCACHEPREFIX` + `runner.env`, which is `()` for track I): the caller's
  `INFRX_D_TASK` never reaches the pristine baseline. Proof: with the i8 lock free,
  `INFRX_D_TASK=i3 … -k failure_not_rolled_back` exit 0 (1 passed, 156 s) and `INFRX_D_TASK=b1 …` the same (the key
  B1 reported failing), both running the full-list baseline.
- The real cause: the 8 I8 pooler cases (`test_pooler.py` x5, `test_observe`, `test_privilege_probe`,
  `test_rollback_drill`) take ONE host-wide exclusive flock, `/tmp/infrx-i8-postgres-55450.lock`
  (`pooler.Stack.up`, LOCK_NB). The session-scoped `i8_stack` fixture holds it until the end of the holder's
  pytest session (a whole `make api-test`, 1-2 h). Every concurrent lane's run therefore got `BlockingIOError`:
  8 errors in the outer run, and the same 8 cases failed the list's pristine baseline (the union of all 435
  mutants' cases) in the copy, so all 43 default-subset mutants were `broken_runner`. The correlation with
  "non-default key" is that lanes use keys and run concurrently; the default-key runs were the coordinator's,
  alone. (The WR-LW1I-6 own-process note is about lists that start the D harness; not this.)
- Reproduced before the fix: `flock -n /tmp/infrx-i8-postgres-55450.lock -c "INFRX_D_TASK=i3 pytest tests/i/test_mutants.py -k 'denied_read_tolerated or validation_skipped'"`
  exit 1, 2 failed, `broken_runner … pristine baseline … ['tests/i/test_observe.py::…', 'tests/i/test_pooler.py::…']` — the reported signature.

## Fix (smallest; the lock stays exclusive, unchanged)
- `conftest.py` `i8_stack`: `except BlockingIOError` → `pytest.skip("NOT RUN (i8 harness busy): another run holds the
  exclusive i8 lock /tmp/infrx-i8-postgres-55450.lock (…)")`. The cases are visibly NOT RUN, never failed; the
  baseline stays green; nothing of the holder's is waited on or touched.
- `test_mutants.py` `_not_run`: a mutant whose cases were all skipped ("its cases were skipped") while the i8 lock is
  held is `pytest.skip("NOT RUN (i8 harness busy) …: <mutant>")`, never a kill (the `blocked_off_linux` pattern). With
  the lock free the result is asserted exactly as before. None of the 19 i8-only mutants is in the default subset.
- `mutants.py` `merge_accepts_duplicate_rules`: anchor extended with the ops/App loop's header — WR-OBS-2 (`2de48ddd`)
  repeated `if rule["name"] in rules:` for the Lab loop, so the mutant was `misdeclared` (anchor x2) under
  `INFRX_MUTANTS=all`, a base defect found by proof 1.

## Tests first
| cmd | exit | result |
|---|---|---|
| seam before fix: `INFRX_D_TASK=i3 pytest -q tests/i/test_mutants.py -k held_i8` | 1 | 2 failed: `[pristine]` = `broken_runner: pristine baseline … ['tests/i/test_pooler.py::test_ops_continuous__the_computed_budget_is_what_the_session_pooler_admits']`; `[skip_removed]` anchor absent |
| seam after fix, same cmd | 0 | 2 passed (pristine green with the lock held; the `skip_removed` mutant of the conftest decision → `broken_runner`) |
| hand mutants of the guard/probe (drop the skipped-detail check; drop `busy()`; probe returns False on a held lock) | 1 each | 2 / 1 / 1 failed of the 5 guard cases — all killed |
| held lock: `pytest -rs tests/i/test_{pooler,observe,privilege_probe,rollback_drill}.py` | 0 | 28 passed, **8 skipped `NOT RUN (i8 harness busy)`**, 1 xfailed (before: 8 errors) |
| held lock: `INFRX_D_TASK=i3 INFRX_MUTANTS=all pytest -rs tests/i/test_mutants.py -k 'budget_session_limit_raised or pool_prepares_again or denied_read_tolerated or drift_usd_only'` | 0 | 2 passed (mixed `pool_prepares_again` still killed by its non-i8 case), 2 skipped NOT RUN |
| `pytest -q tests/i/test_mutants.py -k 'not_run or busy_probe or held_i8 or well_formed or every_case'` | 0 | 9 passed |
| `ruff check tests/i/conftest.py tests/i/test_mutants.py` | 0 | clean |

## The three proofs (lock free at start; sequential, detached)
| # | cmd | head | exit | result |
|---|---|---|---|---|
| 1 | `INFRX_D_TASK=i3 INFRX_MUTANTS=all uv run --frozen pytest -q -rs tests/i/test_mutants.py` | 46d2daa | 1 | 448 passed, 1 failed (23 min): `merge_accepts_duplicate_rules` misdeclared (anchor x2, base defect) — 0 skipped, 0 survivors, every i8 mutant killed |
| 1b | `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py -k 'merge_accepts_duplicate_rules or well_formed or every_case'` | 38298d6 | 0 | 3 passed → full list = 435 mutants killed + 5 runner self-tests + 2 shape + 7 new = 449/449 |
| 2 | default key: `env -u INFRX_D_TASK uv run --frozen pytest -q -rs tests/i/test_mutants.py` | 46d2daa | 0 | 57 passed (the 50 as before + 7 new), unchanged |
| 3 | `INFRX_D_TASK=i4 make api-test` (2:13:41, concurrent with other lanes) | 38298d6 | 2 | 6464 passed, 137 skipped, 9 xfailed, **7 failed, 0 in tests/i** (was 43 failed + 8 errors); the 7 = `tests/d/test_outbox_relay.py[valkey]` `HarnessBusy` on the foreign `infrx-d2-valkey` (not mine, recorded) |

## Deviations / open issues
- Proof 3 ran under **i4** (conditional lane, not started: no worktree, lock or container), not i3: i3 has no
  PostgreSQL block in `infrx.contracts.tasklocal`, so every PG suite KeyErrors at `tests/d/pgharness` import under
  it — incl. `tests/i/test_ops_steps.py::test_ops_login__on_postgresql_…` (fails under i3, exit 1, 1 failed 18 passed;
  a key-table fact, not this defect). Proofs 1-2 and the seam ran under i3 as briefed.
- The guard decisions in `test_mutants.py` are killed by hand mutants (above) and pure cases, not by a MUTANTS entry:
  the track-I runner excludes `test_mutants.py` from its case set by design.
- Open (pooler.py owner, not owned here): `Stack.down()` runs in the fixture's `finally` after a refused lock and
  may `docker network rm infrx-i8-net` of the holder (docker refuses while its containers are attached);
  pre-existing, unchanged frequency. Upgrade: `package` scope for `i8_stack` would cut a run's hold of the shared
  lock from the whole session to tests/i (fewer NOT RUN skips for concurrent lanes) — not done (no mutant can see it).
- `_i8_busy` probes by taking the lock for an instant (ponytail comment): a run starting in that instant skips its
  i8 cases visibly.
- Housekeeping: one orphan fake `vmstat -t 30` from my proof-1 copy of `certify_wc0_second_start` (pid 1051235)
  stopped; five older ones from 2026-09-27 (other runs) left alone. The i8 lock/containers at the end belong to
  `codex-w5-pipelines-lineage`'s api-test; untouched. No container/volume of mine remains.

## Wiring requests
None (no Makefile/composition change; `tests/i/test_mutants.py` is already in `api-mutants`).

## Rulings (proposed, unnumbered)
- A host-wide exclusive harness lock held by another run makes that harness's cases a visible `NOT RUN` skip (and
  their mutants NOT RUN), never a failure or a kill; with the lock free they run and are asserted unchanged.

## Estimate
Remaining 0/0.5/1 h (review round only), confidence high; basis: one harness defect at 1/2/4, spent ~1.5 h active
plus 2.6 h of detached proofs.

## Fix round (review 0-F1, 0-F2; head cc594dd3, only `apps/infrx-api/tests/i/test_mutants.py`)
- **0-F1 (TOCTOU):** `_settled(mutant)` replaces the bare `run_mutant` call in `test_mutant_is_killed`. When a
  mutant naming an i8 case comes back "its cases were skipped" and the probe finds the lock free, it is rerun once, so
  the verdict comes from a real run and not from a probe taken after the copy finished. ponytail: if the lock changes
  hands again during the rerun, the race can still happen. Loop if that is ever seen.
- **0-F2 (over-broad NOT RUN):** `_not_run(mutant, result, busy)` reports NOT RUN only when
  `files_for(mutant.cases) & I8_FILES` (test_pooler/observe/privilege_probe/rollback_drill, the only `i8_stack`
  users). A skipped mutant that names no i8 case stays `misdeclared` whatever the lock state.
- Tests first: the new cases `NOT_RUN_CASES[non-i8 skipped while held]` and
  `test_a_lock_that_changes_hands_mid_run_is_rerun_not_misdeclared` (injected run sequence skipped→killed, lock free;
  and held → NOT RUN with no rerun), plus the four existing rows now carrying a mutant.

| cmd | head | exit | result |
|---|---|---|---|
| `INFRX_D_TASK=i3 pytest -q tests/i/test_mutants.py -k 'not_run or changes_hands'` (before fix) | 28861ac6+tests | 1 | 6 failed (scratchpad `ihk-fix-red.log`) |
| reviewer repro (LOGIN_PG-only mutant, `busy=lambda: True`) through `_settled`/`_not_run` | cc594dd3 | 0 | `misdeclared`, `_not_run` → None (before: NOT RUN) |
| `INFRX_D_TASK=i3 pytest -q tests/i/test_mutants.py` (subset) | cc594dd3 | 0 | 59 passed |
| default key `pytest -q tests/i/test_mutants.py` | cc594dd3 | 0 | 59 passed |
| `INFRX_D_TASK=i3 INFRX_MUTANTS=all pytest -q -rs tests/i/test_mutants.py` | cc594dd3 | 0 | **451 passed** (21 min): 435 mutants killed, 0 survivors, 0 skipped + 16 runner/shape/guard cases |
| `ruff check tests/i/test_mutants.py` | cc594dd3 | 0 | clean |

`make api-test` was not rerun. The only change is in `test_mutants.py`, and the full list above covers it. The
earlier i4 api-test result stays as recorded. Remaining estimate: 0/0.25/0.5 h (re-review), confidence high.
