# F2R lane A shared mutant-run logs (raw pytest output, preserved by the coordinator on 2026-09-23)

| log | tree | result | meaning |
|---|---|---|---|
| `f2r-a-mutants-detached.log` | WIP snapshot 48d61b9 (coordinator-detached run during the 2026-09-22 account restart) | `7 failed, 953 passed in 2211s` | all seven in tests/t/test_trace_mutants.py and tests/w: anchor drift — the lane's concurrent item-2/item-3 edits moved the lines those mutants targeted while the run was copying the live tree; each was re-anchored, retargeted or retired (see `../F2R-A-6ab781b.md`) |
| `f2r-a-mutants-final.log` | d7790c6 (all seven lists) | `967 passed in 2201s` | the accepted run |
| `f2r-a-mutants-final2.log` | b4521c1 (contracts, w, m lists rerun after the post-merge fixes) | `535 passed in 1052s` | touched lists green |

These were untracked in the lane worktree; committed here so a worktree removal cannot erase the only record of those runs.
