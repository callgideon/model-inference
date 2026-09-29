# E8L continuation (lane lab-rollout-7, LW6): WR-LR6-VERDICT, k10's UI journey bound (no sub-cell),
# WR-LR6-GATE-OUT, WR-LR6-E2E-SHADOW; `make lab-rollout`: only k08 NOT RUN

Task: E8L (tasks.json:3438), LAB-ROLLOUT gate, under R222/R234/R238/R240-R249/R253. Branch
`codex/w5-lab-rollout-7`, worktree `.claude/worktrees/codex-w5-lab-rollout-7`, base `33547abd`
(merge #56 + lab-rollout-6 + composition-7). Measured code head `e65bbecf`.

Keys:
- `e8l` (compose 57400-57499, PostgreSQL 57432) for the gate;
- `r2` (PostgreSQL 57534) for the pilot PG cases and E4;
- `l4` (PostgreSQL 57503) for the e2e suites, through `gate.py` / `node --test`.

d1/55432, e5l, t2f, lab-on and l3 were not touched. Everything ran locally: no hosted Supabase,
pilot box, AWS/SSM/S3, Vercel or secrets, no migration, and every Lab switch OFF. Continues
`E8L-4d394ef.md`.

## Commits (one per step, never amended)

| commit | step |
|---|---|
| `ac25185c` | (1) WR-LR6-VERDICT: `pilot.ReleaseRecords.verdict`; 4 fake cases + 1 r2 PG case; 13 mutants |
| `62132927` | (2) k10's UI journey bound: rollout backend drops both stand-ins; `SUB_CELLS = {}`, `OUT_OF_SCOPE = {P-08}` |
| `3a02f611` | (3) WR-LR6-GATE-OUT: `gate.py` resolves `--out` |
| `e65bbecf` | (4) WR-LR6-E2E-SHADOW: evaluate/improve/rollout served through the control unit's own composition |
| (this commit) | evidence, raw (`E8L-raw-lr7/`, `E8L-raw-e65bbecf/`), coordinator update JSON |

## (1) WR-LR6-VERDICT - DONE

`infrx/gateway/pilot.py` changes in three places:
- **`ReleaseRecords.verdict`** is new. It returns D9's latest decision when D9 holds one.
  Otherwise, for a *running* release, it returns R2's `evaluate` at read time, with:
  - the Live that the row's `progress` shows (one `PgReleaseStore.live` read per release, so
    progress and verdict come from one snapshot);
  - `release_report(PgLabReads, PgLabDataStore, …)`, WR-C5-REPORT's selection;
  - `now = live.observed_until`, the database clock of that read, so `evaluated_at` is the same.

  It is read-only and never recorded. It returns null in three cases: nothing is assigned
  (R244; the report is not even read), R2 refuses the plan's unit (R248: an `InvalidRequest`
  from `evaluate`), or the release is not running and has no decision.
- **`releases()`** hoists the single Live read into a local and passes it to `_progress` and
  `verdict`. This is the only change outside `verdict`, and it is a declared deviation.
- **`lab_releases()`** passes `PgLabReads(connect)` as the new `reads` argument
  (`ReleaseRecords(…, variants, reads=None)`). This is a one-line composition change for the
  verdict's second port.

**LR6-RV-3.**
- *Refusal path.*
  - R2's unit refusal is caught and reads null.
  - 0054's own refusal (jobs settled in legacy USD, or both regimes) is raised by the *single*
    Live read. It still fails the whole listing through `progress`. That behaviour is
    unchanged: it is composition-7's carried open issue, and whether a row degrades needs a
    ruling. The verdict never reads Live a second time.
- *The control unit's grants.* **Confirmed and pre-existing** (WR-LR7-GRANT below).
  - `tests/i/lab_control/test_control_routes_pg.py` on l4 fails `('lab','releases')`: 503,
    `InsufficientPrivilege`. The cause is `lab_release_live`, read by WR-LIVE-PAGE's progress,
    which 0056 does not grant.
  - The failure is identical with the base's `pilot.py` (`i-lab-control-pg-l4-at-base-pilot.log`).
  - The verdict adds `lab_experiments`. Its second failure (`optimizations` 503 → 200) is the
    stale expectation after #56 (WR-LR7-I-OPT).

**Tip red fixed.** `test_lab_releases__a_releases_progress_is_d9s_live_null_only_before_one_is_observed`
was red on `33547abd`: `TypeError`, `ReleaseRecords` got 3 arguments after #56 added
`variants`. It now passes `None` plus a `Reads` fake with no experiment.

**Tests first.**
- Fake cases in `tests/g/lab_releases/test_lab_releases.py`: red 4 failed / 14 passed
  (`red/step1-verdict-unit-red.log`). The cases:
  - `…a_running_releases_verdict_is_r2s_evaluate_now_when_d9_holds_none`: hold `["no_report"]`
    with one Live read; expand with the report's two runs as evidence; judged at the Live's
    clock (a 60 s-old Live is `before_horizon`);
  - `…nothing_assigned_or_a_refused_unit_reads_no_verdict`: null, no experiment read; the plan
    in PROVIDER_USD against CREDIT jobs is null;
  - `…a_decided_release_reads_d9s_decision_never_a_fresh_evaluation`: approved and rolled back
    read D9's decision; a non-running release is never evaluated;
  - `…the_composed_records_read_b4s_experiments_on_the_pool`.
- r2 PG case `test_lab_releases_composition_pg__a_running_releases_verdict_is_r2s_evaluate_at_read_time`,
  over real 0054, 0043, D7 and the stored plans. The world's DB clock is advanced 5 s past a
  1 s horizon, then restored. It covers:
  - nothing assigned → null;
  - 3 healthy candidate jobs (DEV_DEPLOYMENT, ready_private) plus 1 baseline job and no
    report → hold `["no_report"]`, `evaluated_at == progress.observed_until`;
  - B4's experiment + B2's accepting report → expand with the runs as evidence;
  - D9 holds only its `start` event (read-only);
  - D9's rollback → the verdict is that decision;
  - a PROVIDER_USD plan over CREDIT jobs → verdict null while progress shows.

  Red: `TypeError: 'NoneType' object is not subscriptable` at the hold assertion
  (`red/step1-verdict-pg-red.log`). The module's two cases now share one in-memory objects
  store, because the page lists every NEMO release.
- Green:
  - `pytest tests/g/lab_releases`: 18 fake cases pass (`test_lab_releases.py`);
  - `INFRX_D_TASK=r2 … test_lab_releases_composition_pg.py test_lab_releases_pg.py`: 3 passed.

**Mutants** (`tests/g/lab_releases/mutants.py`, file `gateway/pilot.py`):
- 13 new: `verdict_never_evaluated`, `verdict_of_nothing_assigned` (`dies_by AttributeError`:
  R2 reads the arms of None), `verdict_of_a_settled_release`, `verdict_ignores_d9s_decision`,
  `verdict_unit_refusal_fails_the_listing`, `verdict_at_the_wall_clock`,
  `verdict_without_its_report`, `verdict_reasons_dropped`, `verdict_evidence_dropped`,
  `verdict_time_not_the_lives`, `verdict_reads_another_live`, `verdict_reads_absent`,
  `verdict_reads_off_the_pool`.
- `page_progress_withheld` is re-anchored on `"progress": _progress(live),`.
- **`INFRX_MUTANTS=all … tests/g/lab_releases/test_mutants.py`: 51 passed = 48 mutants, 0
  survivors, + 3 list checks** (`mutants-lab-releases.log`).
- The mutants name the fake cases; the r2 PG case is the real-service proof of the same four
  behaviours.

**Re-anchored outside the list, on the moved lines (anchors only):**
- `tests/g/mutants.py`:
  - `lab_releases_records_absent`: the composition line now ends `PgLabVariants(connect),
    PgLabReads(connect)),`;
  - `lab_releases_verdict_dropped`: `if d is not None:` → `if False:` in `verdict`;
  - `lab_releases_variants_off_the_pool`.
  - With `-k` on those three plus the list checks: 5 passed.
- `tests/integration/lab_rollout/mutants.py` `st_records_verdict_dropped`: same new anchor,
  killed by K10_PORT in the full list below.

## (2) k10's UI journey bound, no sub-cell - DONE

**Red first on the tip.** The rollout e2e suite at base `33547abd` gives 1 pass / 5 fail
(`e2e/rollout-at-base-33547abd.log`). This is not the stand-ins: #56's WR-C6-VARIANTS made
`/optimizations` read 0055's listing, which is empty, not a refusal. R01's
`/optimizations`-fails-closed assertion failed, the planless release was never stopped, and
every later page 503'd on WR-C5-PLAN. On the merged tip, k10 would FAIL.

**`apps/lab/tests/e2e/rollout/backend.py` changes:**
- Both stand-ins are gone: the verdict overlay (`ReleaseRecords.releases` wrapper,
  `/_test/composition`) and `Controller.approve` for an expansion (`Decided`).
- Every decision goes through `decide_proposal`, including an expansion on R2's expand
  verdict (WR-LIVE-DECIDE).
- The page's verdict is the composed records' own. The new `/_test/traffic` door writes:
  - two candidate jobs (each with an operator's feedback) and one baseline job, assigned to
    the release as rows (tests/d `job` fixture; no R1 traffic on l4);
  - B4's experiment of the release's own two runs under the plan's protocol (`lab_create_run`
    first);
  - B2's report (`accept` / `inconclusive`) stored in D7.

  It waits out the plan's horizon on the DB clock and answers the composed verdict.
- Each release has its own baseline ref and runs, so B4's newest-first selection binds per
  release. The candidate is DEV_DEPLOYMENT's serving ref (ready_private: R247 healthy).
- The e2e plan is `r2w.plan(horizon_s=1, min_requests=2, max_skew_bp=10_000)`.
- The world's clock is unfrozen after the seed: `select infrx_test.unfreeze()`. Admission's
  seed freezes it at 2026-09-20, and `rollout decide` evaluates at the wall clock, so
  `max_lag_s` against a frozen Live would always hold `metrics_stale`. This is composition-7's
  open issue, met here without a stand-in.
- `stand_ins` now lists: the session door; the in-memory objects; R1's assignments and jobs
  written as rows; R1's aggregates as the *rollback* step's input (`/_test/step`); and L3's
  serving alias (`FakeServing`).

**`stack.test.ts` changes:**
- R03, R04 and R05 read `traffic(…)`: expand, hold/inconclusive, expand.
- R01 asserts `/releases` fails closed and `/optimizations` reads "No optimized variants
  registered yet." (WR-C6-VARIANTS).
- R03's approved row must show `expand by <operator> at …: operator:proposal,
  proposal:<id> · evidence lab:run:…, lab:run:…`. That is 0043's decision through `rollout
  decide`, stronger than before.
- l4: **6/6** (`e2e/rollout-step2-6.log`); intermediate runs are kept as `rollout-step2-{1..5}`.

**Runner (layer 1).**
- `SUB_CELLS: dict[str, dict] = {}`, `OUT_OF_SCOPE = {"P-08": …}`, `BASE = "33547abd"`.
- Tests:
  - `test_e8l_k10s_composed_ui_journey_is_bound_and_no_longer_a_sub_cell` replaces the
    sub-cell case;
  - the R222 case checks `OUT_OF_SCOPE == {P-08}` and judges a monkeypatched fixture
    sub-cell: an in-scope lane is open, a P-08 lane is accepted.
- Red 2 failed / 14 passed (`red/step2-runner-red.log`); green 18 passed, with
  `test_lab_package_isolation`.
- Mutants:
  - new `k10_ui_still_a_sub_cell`;
  - re-anchored `k09_breach_still_a_sub_cell`, `r222_gpu_in_scope`, and
    `r222_landed_wr_still_excused` (now: WR-LR6-VERDICT put back into `OUT_OF_SCOPE`);
  - retired `k10_ui_sub_cell_without_its_lane` and `k10_ui_sub_cell_dropped`.

## (3) WR-LR6-GATE-OUT - DONE

- The fix in `gate.py`: `out = Path(out).resolve() / f"e2e-{suite}"`.
- Test: `test_e8l_the_ui_suites_record_is_read_back_from_a_relative_out` (layer 1). It runs a
  fake node that writes under `LAB_E2E_OUT` from its own cwd, calls `gate.run("rollout",
  Path("out"))`, and requires the record to be read back and `missing() == []`.
- Red: `assert None == {'composed': …}` (`red/step3-gate-out-red.log`).
- Mutant: `gate_out_relative`. The layer-1 copy now carries `gate.py`. Killed.

## (4) WR-LR6-E2E-SHADOW - DONE

**Red first on l4 at base:**
- evaluate: 1 pass / 5 fail (`e2e/evaluate-at-base-33547abd.log`);
- improve: 0 pass / 7 fail (`e2e/improve-at-base-33547abd.log`).

The cause is the one lab-rollout-6 found for rollout. Since WR-LDP-2 the unit mounts
`lab_evaluations` and `lab_pipelines` first, so the backends' own `register` and their
journey fakes never answered.

**Fix, in owned `apps/lab/tests/e2e/`:**
- `stack.unit_app(dsn, url, family, objects=None)` is new.
  - It wraps the unit's own composition of the family (`pilot._lab_2`'s key, patched only
    around `create_app`) in a `Switch`.
  - It hands in-memory objects through `LAB_S3_BUCKET`'s seam (`lab_objects`).
  - It returns `(app, switch)`. `switch.own` is what the unit composed.
- evaluate, improve and rollout mount their family only through it.
- `world.composed` now reads that composition, not a second call of the factory. This closes
  LR6-RV-4.
- `stack.composed` is retired.

**Green on l4:**
- evaluate 6/6, improve 7/7, rollout 6/6 (`e2e/*-step4.log`);
- **`make lab-e2e`: 24/24 pass, 0 fail, 0 skipped** (observe + evaluate + improve + rollout;
  `make-lab-e2e.log`).

**The e2e mutant lists** (`LAB_E2E_REAL=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs`,
exit 0, `e2e-mutants-all.log`):
- harness: 17 mutants, 17 killed;
- stack: 24 mutants, 24 killed (observe S01-S06, rollout S07-S12, evaluate S13-S17, improve
  S18-S24), **0 not killed**.

## (5) `make lab-rollout` at `e65bbecf` (fresh stack, torn down by the run)

**First attempt: BLOCKED.** The runner refused to touch four `infrx-e8l-*` containers that
another checkout had created (`E8L-raw-lr7/make-lab-rollout-1-blocked-orphan-e8l/`).
- Their label: `ai.infrx.e2.checkout = …/scratchpad/rv-lr6/tests/integration`. They were
  created at 18:58Z by lab-rollout-6's review copy, which the weekly subagent limit killed.
- No process referenced them.
- They were torn down through *that* checkout's own `harness.down()` + `clear_state()`, which
  respects the runner's ownership guard: `removed […4], left []`.

**Second attempt:**
- `E8L-raw-e65bbecf/`: verdict.json, scenarios.xml, scenarios.log, cases/.
- pytest: `24 passed, 1 skipped in 141.42s`.
- `make` exit 2: runner exit 3 = NOT RUN, k08 only.

| id | status | reason class | rerun |
|---|---|---|---|
| k01-k07 | PASS | | |
| k08 | NOT RUN[P-08] | GPU (P-08 staging target), out of local scope (R222) | `apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py --out <dir> --only k08` |
| k09 | PASS | | |
| k10 | **PASS** | listing + composed port half + UI suite 6/6 with no product-WR stand-in | |

- `sub_cells: []`.
- Cells: **ROLLOUT-PIN PASS, ROLLOUT-RECOVER PASS**, OPT-PARITY NOT RUN (k08).
- **`r222`: `{"accepted": true, "open": {}}`**.
- Pins: `base 33547abd`, `head e65bbecf`. `dirty: true` means only the untracked raw logs.
  The images are unchanged from E8L-4d394ef.

**R222 tally:** k01-k07, k09, k10 PASS; k08 NOT RUN[P-08 GPU]; no sub-cell; no FAIL; r222
accepted.

## (6) Mutants: the full E8L list on the kept stack

- Kept by `runner.py --keep --out <abs>/only-k10-kept --only k10`: k10 PASS.
- Then `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e8l pytest -q -rA tests/integration/lab_rollout/test_mutants.py`
  (`mutants-full.log`): **exit 0, 90 passed in 1169 s = 86 mutants (31 layer 1 + 55 stack),
  0 survivors, 0 broken_runner, + 2 self-tests + 2 list checks**. `st_records_verdict_dropped`
  (re-anchored) is killed by K10_PORT, and every k10 UI stack mutant is killed on the bound
  journey.
- Layer 1 alone (step 2): 34 passed (30 mutants + 4 checks). With `gate_out_relative` that
  is 31 layer-1 mutants.

## (7) E4, every switch OFF

- Command: `(apps/infrx-api) INFRX_D_TASK=r2 .venv/bin/python -m pytest -q -rs -p no:cacheprovider tests/g tests/w tests/contracts tests/i/test_packaging.py`
  at `e65bbecf`.
- Result: **2840 passed, 26 skipped, 0 failed in 1442 s** (`e4-r2.log`). The floor is 2831.
- The r2 PG cases run here; the skips are other keys' and stacks'.

## Commands

| command | exit | result |
|---|---|---|
| `make api-env`; `pnpm install --frozen-lockfile` (apps/lab, apps/app) | 0; 0; 0 | pinned envs |
| `pytest tests/g/lab_releases/test_lab_releases.py` (red, step 1) | 1 | 4 failed / 14 passed |
| `INFRX_D_TASK=r2 pytest …composition_pg.py` (red, step 1) | 1 | 1 failed / 1 passed (verdict None) |
| `pytest tests/g/lab_releases/` | 0 | green (fake cases; PG skipped without r2) |
| `INFRX_D_TASK=r2 pytest …composition_pg.py …lab_releases_pg.py` | 0 | 3 passed |
| `INFRX_MUTANTS=all pytest tests/g/lab_releases/test_mutants.py` | 0 | 51 passed (48 mutants, 0 survivors) |
| `INFRX_MUTANTS=all pytest tests/g/test_mutants.py -k "<3 re-anchored> or well_formed or every_case"` | 0 | 5 passed |
| `pytest tests/g/test_startup.py` | 0 | 37 passed |
| `node --test tests/e2e/rollout/stack.test.ts` at base 33547abd (l4) | 1 | 1 pass / 5 fail (red) |
| same at 62132927 | 0 | 6 / 0 |
| `pytest test_e8l_runner.py` (red, step 2 / step 3) | 1 / 1 | 2 failed / 14; 1 failed |
| `pytest test_e8l_runner.py tests/integration/test_lab_package_isolation.py` | 0 | 19 passed |
| `INFRX_MUTANTS=all pytest …lab_rollout/test_mutants.py -k "not stack_mutant"` | 0 | 34 passed (step 2) + `gate_out_relative` killed (step 3) |
| `pytest tests/integration/lab_*/test_mutants.py -k "well_formed or every_case or …"` | 0 | 19 passed; anchor script: 31 + 55 in source, 0 moved |
| `node --test tests/e2e/{evaluate,improve}/stack.test.ts` at base (l4) | 1 / 1 | 1/5; 0/7 (red) |
| same at e65bbecf | 0 / 0 | 6/0; 7/0 |
| **`make lab-e2e`** | **0** | **24/24** |
| `LAB_E2E_REAL=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs` | 0 | 17/17 + 24/24 killed |
| `make lab-lint` / `make lab-typecheck` | 0 / 0 | clean |
| `ruff check --line-length 100` on every changed .py | 0 | clean |
| `INFRX_D_TASK=l4 pytest tests/i/lab_control/test_control_routes_pg.py` (tip and base pilot.py) | 1 / 1 | 2 failed: `('lab','releases')` 503 InsufficientPrivilege + stale `optimizations` (pre-existing; WR-LR7-GRANT, WR-LR7-I-OPT) |
| `make lab-rollout` (1st) | 2 | BLOCKED: orphan e8l stack of rv-lr6 (torn down by its own checkout) |
| **`make lab-rollout`** (e65bbecf) | **2** (runner 3) | k01-k07, k09, k10 PASS; k08 NOT RUN[P-08]; no sub-cell; r222 accepted |
| `runner.py --keep --only k10` | 3 | k10 PASS, stack kept |
| `INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e8l pytest …lab_rollout/test_mutants.py` | 0 | 90 passed: 86 mutants, 0 survivors |
| `runner.py --reuse --only k08` (teardown) | 3 | the 4 e8l containers removed |
| **E4** `INFRX_D_TASK=r2 pytest tests/g tests/w tests/contracts tests/i/test_packaging.py` | **0** | **2840 passed, 26 skipped** |
| `make api-test` | not run | E4 covers tests/g, w, contracts; the changed Python outside is `tests/integration/lab_rollout` + e2e backends |

## Changed paths (owned only)

- `apps/infrx-api/infrx/gateway/pilot.py`: `ReleaseRecords.verdict`, plus the declared
  one-line Live hoist in `releases` and the `PgLabReads` port in `lab_releases`.
- `apps/infrx-api/tests/g/lab_releases/{test_lab_releases.py, test_lab_releases_composition_pg.py, mutants.py}`
- `apps/infrx-api/tests/g/mutants.py`: 3 anchors.
- `tests/integration/lab_rollout/{runner.py, mutants.py, test_e8l_runner.py}`
- `apps/lab/tests/e2e/{stack.py, gate.py, rollout/backend.py, rollout/stack.test.ts, evaluate/backend.py, improve/backend.py}`
- `research/plan/evidence/e/E8L-e65bbec.md`, `E8L-raw-lr7/`, `E8L-raw-e65bbecf/`
- `research/plan/evidence/coordinator/updates/E8L-20260929T2355Z.json`

## Wiring requests / WR texts (outside this lane's ownership; none applied)

**WR-LR7-GRANT** (lab-sql, a Lab-only additive migration, LOCAL-ONLY, number at merge; this
is LR6-RV-3's second half). The Lab control unit's login `infrx_lab_control` cannot execute
0054's `lab_release_live`. WR-LIVE-PAGE's progress reads it for every shown release, so on
the unit `/lab/v1/releases` answers 503 (`InsufficientPrivilege`) as soon as one release is
listed. That is pre-existing since composition-7, and reproduced with the base `pilot.py`.
The verdict also reads B4's `lab_experiments`. Patch:

```sql
-- WR-LR7-GRANT: the release page on the Lab control unit reads D9's Live (WR-LIVE-PAGE's
-- progress, R244) and B4's experiments (WR-LR6-VERDICT's B2 report). Grants only.
-- ROLLBACK: revoke execute on both functions from infrx_lab_control.
grant execute on function infrx.lab_release_live(jsonb), infrx.lab_experiments(jsonb)
  to infrx_lab_control;
```

Proof: `INFRX_D_TASK=l4 pytest tests/i/lab_control/test_control_routes_pg.py` shows
`('lab','releases')` 200, equal to the owner's (its release has no assignment, so this proves
the Live grant). Add to the lab-sql PG role matrix: on the lab login, a release with one
assigned terminal job lists progress and a `hold`/`no_report` verdict.
`anon`/`authenticated`/`infrx_runtime` stay refused.

**WR-LR7-I-OPT** (the lab-control-routes owner, `tests/i/lab_control/test_control_routes_pg.py:69`).
The expectation is stale since #56 (WR-C6-VARIANTS: 0055's listing, granted to the lab login
in 0055):

```diff
-    "optimizations": (503, '{"refusal":"unavailable"}'),
+    "optimizations": (200, '{"data":[]}'),
```

Also drop "R3's variants: WR-C6-VARIANTS" from the comment above `EXPECTED`. With
WR-LR7-GRANT the file is 2/2 green on l4.

**WR-LR7-DOC** (coordinator, 08 §5 Lab releases row, research only): replace "the verdict is
D9's latest decision" with "the verdict is D9's latest decision, else R2's `evaluate` at read
time over D9's Live and the B2 report (WR-LR6-VERDICT)".

## Ruling proposal (unnumbered; rulings run through R253)

- **The Lab page's verdict of a release.** It is D9's latest decision. For a running release
  D9 holds none for, it is R2's `evaluate` at read time over D9's Live (0054, the same read
  its progress shows, at that read's database clock) and the release's B2 report
  (WR-C5-REPORT's selection). It is read-only and never recorded. It is null while nothing is
  assigned (R244) or while R2 refuses the plan's unit (R248), and null for a release that is
  neither running nor decided. The route's expansion-proposal gate (`verdict.action ==
  "expand"`) reads it. An operator's approval re-evaluates at decide time (WR-LIVE-DECIDE).
- **E2E suites read the Lab control unit's composition** (lab-rollout-6's proposal, now
  applied to all three suites). A Lab e2e suite serves its family through the unit's own
  composition (`stack.unit_app`), never a route beside it. Fakes for ports the unit does not
  compose are laid over that composition and named in the suite's record.

## Open issues

- **0054's refusal of a release's jobs' unit** (legacy USD or mixed) still fails the whole
  listing through progress. This is composition-7's carried issue; a per-row degrade needs a
  ruling.
- **C7-RV-1 and C7-RV-2** (composition-7's decide-path test gaps) are not in this lane's
  paths.
- **Production path.** With `rollout_routing` OFF (P-12), no assignment exists, so progress
  and verdict stay null and no expansion can be proposed. That is correct and fails closed.
- **k08** is P-08 GPU operator work.

## Deviations

- `pilot.py` beyond `ReleaseRecords.verdict`: the single Live read is hoisted in `releases`
  (one snapshot for progress and verdict), and `lab_releases` passes `PgLabReads`.
- The e2e world's DB clock is unfrozen, not advanced, because `rollout decide` evaluates at
  the wall clock.
- The orphan e8l stack of lab-rollout-6's review copy was torn down through its own checkout.

## Estimate (remaining E8L)

- optimistic 0 h, likely 0.5 h, pessimistic 2 h; confidence high.
- Basis: the local gate is at its floor (only k08 NOT RUN, r222 accepted, no sub-cell). What
  remains is outside this lane:
  - WR-LR7-GRANT + WR-LR7-I-OPT: a lab-sql slice, about 1-2 h, not in this figure;
  - k08: P-08 GPU operator work;
  - one verify lens: 47-234 min, session-03.

## Verification log

- 2026-09-29 (lab-rollout-7): WR-LR6-VERDICT is in `pilot.ReleaseRecords.verdict` (48
  lab_releases mutants, 0 survivors; r2 PG proof). k10's UI journey is bound with no
  product-WR stand-in (`SUB_CELLS = {}`, `OUT_OF_SCOPE = {P-08}`). WR-LR6-GATE-OUT and
  WR-LR6-E2E-SHADOW are fixed (`make lab-e2e` 24/24; e2e mutants 41/41). `make lab-rollout`
  at e65bbecf: k01-k07, k09, k10 PASS, k08 NOT RUN[P-08], r222 accepted. E4 2840/0 on r2.
  Found: the unit login's missing `lab_release_live` grant (pre-existing, WR-LR7-GRANT).
  Local only.
