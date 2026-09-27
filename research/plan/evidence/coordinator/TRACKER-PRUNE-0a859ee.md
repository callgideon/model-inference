# TRACKER-PRUNE — v1 launch scope in the tracker (0a859ee)

- Lane: TRACKER-PRUNE, branch `codex/tracker-prune`, base `17cc78bd`, code head `0a859eee`.
- Direction (user, 2026-09-27): remove not-required-now / superseded tasks from the tracker view; show the v1 launch path.
- Changed paths: `research/plan/scripts/progress.py`, `research/plan/scripts/test_progress.py`,
  `research/plan/22-consumer-v1-implementation.md` (dated amendment in its verification log), this file, the update JSON.
- Not changed: `tasks.json`, `progress-state.json`, the rendered `progress.html` / `PROGRESS.md` (rendered to check, then `git checkout`).

## Before / after headline (live overlay rev 151)

Before (Progress summaries, the headline):

    Backend corrections 12 / 14 | App completion 8 / 12 | Deferred Lab / hosting / later 0 / 57 | Reused baseline 44 / 44 | Superseded 0 / 6

After (new first section `v1 launch scope`, same in PROGRESS.md):

    v1 launch scope 20 / 30  (backend corrections + App completion + 4 go-live steps)
    - Backend corrections 12 / 14: E1B, E4C — window-bound: runs on the live system as the post-go-live test window; now: <readiness>
    - App completion 8 / 12: I2A, E3A, I3, E4 — go-live-bound: follows the live release and the BACKEND-READY decision; now: <readiness>
    Remaining, in order: RELEASE-FREEZE, MAIN-MERGE, E1B, E4C, BACKEND-READY, I2A, E3A, I3, E4, APP-PILOT
    Progress summaries: Backend corrections 12 / 14 | App completion 8 / 12 | Reused baseline 44 / 44
    End of page: <details> "Later — not in the v1 launch scope (57 tasks)" (collapsed; rows intact; uncounted)
    Footer: "6 superseded tasks are not shown (replaced by their split tasks, never scheduled): D6 → D6F, D6J; G4 → G4U, G4F, G4T; T2 → T2I, T2F; C3 → C3A, C3F, C3L; I2 → I2A, I2L; E3 → E3A, E3L, E5L."

Counts: 20 done = 12 backend + 8 App + 0 of 4 go-live steps (RELEASE-FREEZE lane running, MAIN-MERGE lane queued, BACKEND-READY and
APP-PILOT pending); total 30 = 14 + 12 + 4. E4C certify + E1B cells are named in the BACKEND-READY step (and E1B/E4C are counted once, as
backend tasks). Go-live steps are the constant `GO_LIVE` in progress.py (the overlay has no go-live list; add a lane → it counts when complete).

## Design notes

- `uncovered()` keeps the check's strictness: a superseded task must be named in the footer (`data-superseded="<id>"` in HTML, `` `<id>` → `` in
  Markdown) instead of a table row; every other task still needs its row in both outputs. Test: removing one footer mention makes uncovered() report it.
- HTML filters now apply to the main task list only (backend/App/baseline); the "include superseded and deferred" checkbox is gone; the JSON
  payload's `tasks` omits superseded.
- Remaining items are ordered by dependency (fewer transitive predecessors first), not manifest order.

## Commands (worktree root)

| Command | Exit | Result |
|---|---|---|
| `python3 -m pytest -q research/plan/scripts/test_progress.py` at base with the new cases (before the implementation) | 1 | 4 failed (the 3 new cases + the updated denominator oracle), 31 passed — failed-then-passed regressions |
| same, at base, unmodified test file | 1 | 2 failed / 30 passed: `test_gated_app_lane_running_is_an_error`, `test_impossible_transitions_are_rejected` (C0 no longer has `dispatch_after_gate`); fixtures moved to I3 |
| `python3 -m pytest -q research/plan/scripts/test_progress.py` at 0a859eee | 0 | 35 passed |
| `python3 -m pytest -q research/plan/scripts/` at 0a859eee | 1 | 41 passed, 1 failed: `test_validate_plan.py::test_app_cannot_dispatch_early` KeyError 'dispatch_after_gate' (C0) — pre-existing at 17cc78bd, file not owned, untouched |
| `python3 research/plan/scripts/progress.py check` base / 0a859eee | 0 / 0 | PASS, 0 errors, 7 warnings / PASS, 0 errors, 7 warnings (with this lane's update file present and unapplied: 8, the "not applied yet" warning) |
| `python3 research/plan/scripts/progress.py` (render) | 0 | `<h2>v1 launch scope</h2>`, `<summary><h2>Later — not in the v1 launch scope (57 tasks)</h2>`, footer line present; 0 superseded `data-id`s; renders reverted |
| determinism: `render_html`/`render_md` twice at a fixed instant | — | identical |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (934 links, 287 documents) |

## Wiring requests

- WR-TP-1: `progress-state.json` has no lane `TRACKER-PRUNE`; `apply-updates` rejects `updates/TRACKER-PRUNE-20260927T0446Z.json` as an unknown task
  unless the coordinator adds a support lane `{"id": "TRACKER-PRUNE", "task": null, ...}` first (as for RELEASE-FREEZE / MAIN-MERGE).
- WR-TP-2 (optional): `test_validate_plan.py::test_app_cannot_dispatch_early` indexes `C0["dispatch_after_gate"]`; C0 lost it (App-ahead override).
  Patch: use `I3` (still gated by BACKEND-READY), mirroring the test_progress.py fixture change.

## Open issues / estimate

None for this lane. Remaining effort: 0 h (optimistic/likely/pessimistic 0/0.2/0.5 h for review fixes), confidence high; basis: diff complete, checks green.
