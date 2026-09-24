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
