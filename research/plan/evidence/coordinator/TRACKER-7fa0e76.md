# TRACKER — consumer-v1 progress tracker handback (head `7fa0e76`)

Support lane for [brief 06](../../consumer-v1/06-progress-tracker.md) under [program 22](../../22-consumer-v1-implementation.md). Branch `codex/tracker`, worktree `.claude/worktrees/codex-tracker`, base **`dff31efc`**, implementation head **`7fa0e76f`**. No box, hosted database, AWS or network access was used. Other branches were read only through `git show`/`git log` (the S3 record and `tasklocal.py` on `claude/consumer-v1`).

## Changed paths (all owned)

- `research/plan/scripts/progress.py`: replaced. The entrypoint is kept, and `apply-updates` and `check` are added.
- `research/plan/scripts/test_progress.py`: new, 22 unittest cases.
- `research/plan/evidence/coordinator/progress-state.json`: overlay schema 2. The v46 content survives in `tracker-v46/` and in `history.v46`.
- `research/plan/evidence/coordinator/PROGRESS.md` and `progress.html`: generated.
- `research/plan/evidence/coordinator/tracker-v46/`: byte copies of the four v46 files as at `dff31efc`, plus a README with sha256 values.
- `research/plan/evidence/coordinator/updates/README.md`: the update-file schema, overlay schema 2 and the ETA method.
- This file and `updates/TRACKER-20260924T2204Z.json`.

## Commands (repo root unless noted)

| Command | Exit | Result |
|---|---|---|
| `cd research/plan/scripts && python3 -m unittest test_progress` | 0 | 22 tests OK |
| `python3 research/plan/scripts/progress.py check` | 0 | PASS: 133 tasks rendered, 15 lanes, 4 gates PENDING, 0 errors, 11 warnings (all overlapping-writer warnings; see open issues) |
| `python3 research/plan/scripts/progress.py apply-updates` | 0 | overlay revision 0 → 1; four "forecast moved" log entries |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS: 887 local links across 179 documents, including the new Markdown; ledger current |
| `cd research/plan/scripts && python3 -m unittest test_validate_plan` | 0 | 7 tests OK |
| Headless Chromium 1217 (Playwright build, `chrome --headless --dump-dom`) on `progress.html` with hash presets `''`, `#activity=running`, `#cat=deferred`, `#q=d10`, `#all=1` | 0 | JSON parsed and filters applied: 70, 9, 57, 1 and 133 of 133 tasks shown; stale banner hidden on a fresh page |
| The same page with `generated` rewritten to 10:00Z | 0 | stale banner shown ("722 min ago; threshold 15 min") |
| `chrome-headless-shell --window-size=390` and `1280` with every task expanded, measuring `scrollWidth` and any element past the viewport outside `.scroll` | 0 | 390 → scroll 375, 0 overflow; 1280 → 1265, 0 overflow. Screenshots of the overview, task list and expanded task at both widths were inspected |
| Read-only: the committed overlay against `claude/consumer-v1:research/plan/tasks.json` (S3 implemented) | 0 | 0 errors, all 133 tasks covered, Backend corrections 1/14, baseline 44 |

Headful Chromium enforces a minimum window width of 500 px, so the 390 px checks used `chrome-headless-shell`.

## Failed-then-passed regressions

- `test_progress.py` against the old renderer failed on import: v46's `load()` returns three values, and `Model` and `apply_updates` do not exist. All 22 pass on `7fa0e76`.
- The coverage oracle (every manifest task in both outputs), applied to v46's committed `PROGRESS.md`, found **103 of 133 tasks missing**, E4C among them. The new outputs cover all 133, and `check` re-renders in memory to confirm it.
- Each oracle test has a positive control, checked in the same test:
  - an implemented E4C with every cell PASS and no decision stays PENDING; the same state with `decision: accepted` is green;
  - an open P-01 gives no finish date; resolving it gives one;
  - a missing GPU window gives no date; allocating one gives a date;
  - an estimate 7 h old is flagged stale; one 5 h old is not.

## Baseline reconciliation (state as rendered)

- **Gates:** all four PENDING, with no decision and every cell NOT RUN. BACKEND-LOCAL: "Not declared. E3B's harness is green at 95fbb90 (historical)". BACKEND-READY lists RV-01…RV-12 as open (per S3).
- **Progress:** Backend corrections 0/14 on this branch; 1/14 after the merge, when S3 is implemented. App completion 0/12. Deferred 0/57. Reused baseline 44 (39 implemented + 5 integrated). Superseded 6.
- **ETA:**
  - E3C: `unknown`, because no lane has an estimate yet.
  - E4C, E3A, E4: `blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25`, with no GPU window for E1B/E4C (plus I2A/E4 for E4).
- **Lanes:**
  - Running: 11 (TRACKER, E2C, F2C-L, F2C-C, E1C, I8, D10, M5, W5, G7, G8). The coordinator records 12 active, and the page shows both numbers.
  - Complete: S3.
  - Queued: M6, E3C, E4C.
  - Next ready work: E1B, which has no lane and needs the GPU box.
- **Clock slip corrected:** coordinator update 1 is labelled 22:30Z, but its record commit `f764e396` is at 21:58Z host time, and this host read 22:01Z. Stamping lanes 22:30Z would have made every genuine lane update "stale" for about 30 minutes. The overlay uses 21:58Z and keeps the label in the log, and `check` now warns on future-dated lanes (tested).

## Wiring requests

- Optional: add `cd research/plan/scripts && python3 -m unittest test_progress test_validate_plan` to `make check` (Makefile is coordinator-owned). No other wiring is needed.

## Open issues and decisions for the coordinator

1. **"The three files" versus four.** All four v46 files were preserved: the renderer, the state and both outputs.
2. **Classification choices (edit the overlay if you disagree):**
   - The backend corrections are the E4C closure minus the reused baseline: 13 BACKEND-CLOSURE tasks + E1B = 14. U4 (the 14th `consumer-v1-closure` task) counts under App.
   - Input `blocks` edges: P-05 → E1B/E4C/I2A/E4. P-06, P-19, P-22, P-24 and P-25 → E4C only. None blocks E3C.
3. **Parameters.** `eta_params.integration_h_per_task = 0.5` is an assumed merge cost, labelled on the page. Calibrate it from observed merges.
4. **Overlapping-writer warnings (11).** They are real at directory granularity:
   - E2C's `apps/infrx-api/tests/ (portability)` against every lane test directory;
   - F2C-L against F2C-C on the contracts directories;
   - G7 `tests/g/` against G8 `tests/g/ops/`;
   - E1C against E4C on `models/marlin2b/`.

   Narrow `owned_paths` in the overlay, or accept them.
5. **Future lane updates.** Lane update files should carry `lane` when a task has two lanes (F2C). Without it, and without a matching `slice`, the update is rejected as ambiguous.
6. **Publishing.** The page was not published as an Artifact. The v46 tracker URLs are kept in `history.v46.tracker_url` for the coordinator to republish.

## Remaining effort (TRACKER)

Optimistic 0.5 h, likely 1.5 h, pessimistic 3 h; confidence medium. Basis: one review/fix round, plus schema adjustments once the first real lane update files arrive. Ongoing checkpoints are coordinator runs of `apply-updates`, not lane work.

## Fix round (2026-09-24, host UTC 22:24Z)

Base `dff31efc`; reviewed head `f1a202ab`; fix commit **`32bbe9a8`** (code, tests, README); this section, the update file and the regenerated overlay/outputs follow in the next commit on `codex/tracker`. Owned paths only.

### Changes (file:line at `32bbe9a8`)

- **Future-dated updates (0-TRK-1, 1-TRK-R1, 2-TRK-3).** `judge()` rejects an update whose `at` is more than `CLOCK_SKEW_H` = 15 min past host UTC (`progress.py:42`, `:428-429`), before it touches a lane. A future-dated lane in the overlay is now a `check` error, not a warning (`:242-243`). Timestamps without a UTC offset are unreadable (`parse`, `:45-51`).
- **Malformed updates (0-TRK-4).** There is one estimate rule, `estimate_problem` (`:76-92`): hours are all null, or finite numbers with 0 ≤ o ≤ l ≤ p; confidence is in the enum; `at` parses. `judge()` uses it, together with list/object shape checks (`SHAPES`/`malformed`, `:408-419`), and rejects `malformed: …` updates (`:430-432`). `validate()` uses the same rule (`:240-241`). If a malformed overlay estimate is hand-edited in, the forecast becomes `unknown` and no date is produced (`:302`).
- **Gates (0-TRK-3, 2-TRK-4).** `gate()` (`:130-149`) is green only when all of the following hold:
  - the decision is `accepted`;
  - the root is implemented;
  - every required `test_id` cell is present;
  - every cell is PASS, with evidence;
  - `candidate.source` and `candidate.deployed` are recorded;
  - no source matches a `historical_runs` candidate of a non-root task (E4B-run3 `bda1586` for BACKEND-READY), unless `candidate.note` records the reuse.

  An `accepted` decision that fails any of these conditions is an `impossible gate transition` error, and the error names the reasons (`:233-234`).
- **Tests decoupled from live state (2-TRK-1).** `Base.pin()` (`test_progress.py:36-54`) freezes the parts that live progress changes:
  - non-baseline task status → `planned`;
  - lanes as dispatched at `f1a202ab` (the `LANES` list);
  - gates with no decision and cells NOT RUN;
  - inputs and findings open;
  - no GPU windows;
  - slots and ETA parameters.

  Structure still comes from `tasks.json`. `test_committed_overlay_passes_check` still checks the live overlay against the live manifest at host UTC. The oracles that used a fixed prefix now assert structure instead: the path ends at E3C and contains D10.
- **New oracles.**
  - exclusive lock over the independent pair W5 and G7 (`:275`);
  - GPU window start at NOW+30 h gives finish ≥ NOW+36 h (`:289`);
  - integration-queue bound (`:300`);
  - wall-clock < serial sum of task durations, and two F2C slices take the max, not the sum (`:258`);
  - a remaining task with no lane → `unknown`, no date (`:242`);
  - overlay: unknown IDs, schema and estimate order (`:113`);
  - required cells FAIL / NOT RUN / missing (`:167`);
  - candidate identity, evidence and historical reuse (`:179`);
  - invalid verdict and invalid decision (`:193`);
  - every transition rule, with the rejection logged (`:354`);
  - future-dated update, followed by a genuine update that applies (`:376`);
  - malformed files through the real `cmd_apply` path (`:385`);
  - same-batch ordering: the older update is applied, not rejected as stale (`:341`).
- `updates/README.md` documents the future-dated rule, the malformed-update rule and the stricter gate rule.

**Note on 0-TRK-2.** The suggested E1B-vs-E4C case cannot separate the GPU lock bound. E1B precedes E4C through `E4B.integration_dependencies` (`tasks.json`: `["I3B", "E1B", "M4", "W4"]`), which `rdeps` looks through, so the dependency path already equals dur(E1B) + dur(E4C) and ties the lock bound. The lock oracle therefore adds a lock over two independent tasks in the test. It also has a positive control: without the lock, the forecast is shorter than their sum.

### Commands (worktree root unless noted)

| Command | Exit | Result |
|---|---|---|
| `cd research/plan/scripts && python3 -m unittest test_progress test_validate_plan` | 0 | 38 OK (31 test_progress + 7 test_validate_plan) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS; ledger current |
| `python3 research/plan/scripts/progress.py check` | 0 | PASS: 133 tasks, 15 lanes, 4 gates PENDING, 0 errors, 11 warnings (the overlap warnings) |
| `python3 research/plan/scripts/progress.py apply-updates` | 0 | applied `TRACKER-20260924T2224Z.json`; overlay revision 2 → 3; re-rendered |
| Scratch copy (`git ls-files research \| tar`): new `test_progress.py` against the `f1a202ab` `progress.py` | 1 | FAILED (failures=5, errors=1): future_lane_is_an_error, overlay_unknown_ids…, candidate_identity…, every_required_cell…, future_dated_update…; malformed_update (ERROR: TypeError in the ETA) |
| Scratch mutation run: 43 single-line mutants of `progress.py` × `test_progress` | — | **43/43 killed**. These include every mutant the review reported as surviving: eta-no-lock-serial, eta-no-window-start, eta-no-integ-queue, eta-naive-sum, eta-sum-parallel-slices, eta-no-lanes-zero, gate-no-cells-required, gate-empty-cells-ok, label-ignore-green, missing-cells-err-removed, verdict-validation-removed, decision-enum-removed, unknown-task-ref-removed, schema-check-removed, estimate-order-removed, impossible-complete-terminal, impossible-deferred, judge-unknown-activity, judge-ambiguous-removed, judge-lane-mismatch, apply-no-log-reject, apply-unreadable-crash. New mutants were added for the new checks: judge-no-future, judge-no-malformed, judge-no-shape, gate-no-evidence, gate-no-candidate, gate-no-historical, future-lane-warn-only, parse-naive-ok, eta-bad-estimate-counted, apply-unsorted, escape-removed, atomic-direct-write |
| `git merge-tree --write-tree claude/consumer-v1 codex/tracker` → `c926a65a`; `git archive` to scratch; both suites | 0 | 38 OK (S3 implemented there). The `f1a202ab` test file on the same tree fails, as the review reported: `dependency path E2C → D10 → M5 → G7 → E1C → E3C` |
| The same merged tree: `validate_plan.py`; `progress.py check` | 0 / 0 | PASS (891 links / 182 docs); PASS with 0 errors and 12 warnings |
| The merged tree with simulated later progress: F2C, E2C, D10, M5, E1B, G7 and E1C implemented, three lanes complete, all estimates set, P-01 resolved, RV-01 fixed, the M6 lane removed; then `test_progress` | 0 | 31 OK |
| CLI repro in scratch at host 22:23Z: `D10-20991231T0000Z.json`, a genuine `D10-<now>.json`, an `M5` update with a string `likely_h`, and a non-JSON file; then `apply-updates` twice; then `check` | 0 | future → `REJECTED … future-dated`; genuine → applied; string estimate → `REJECTED … malformed`; non-JSON → `REJECTED … unreadable`; second run "nothing new"; check PASS |

### Findings closed

0-TRK-1, 1-TRK-R1, 2-TRK-3 (future-dated), 0-TRK-2 and 2-TRK-2 (resource/naive-sum ETA oracles), 2-TRK-1 (tests pinned), 0-TRK-3 (required cells), 0-TRK-4 (malformed updates), 0-TRK-5 (no-lane unknown), 0-TRK-6 (overlay and transition oracles), 2-TRK-4 (candidate identity).

### Wiring requests

Unchanged. Optionally, add `cd research/plan/scripts && python3 -m unittest test_progress test_validate_plan` to `make check`.

### Open issues

- A string estimate that the coordinator **hand-edits** into the overlay makes the forecast `unknown` and is reported by `check`. The lane card's `est_text` would still fail to format it. Update files cannot introduce such an estimate any more, because they are rejected at `judge()`.
- The 15-minute skew allowance is shared by the `judge()` rejection and the `check` error (`CLOCK_SKEW_H`). Tune it there.

### Remaining effort (TRACKER)

Optimistic 0 h, likely 0.5 h, pessimistic 2 h; confidence medium. Basis: the fix round is done; what remains is re-verification follow-up.
