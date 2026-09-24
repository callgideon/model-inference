# Lane update files and overlay schema 2

Lanes never edit [the overlay](../progress-state.json) or [the manifest](../../../tasks.json). Each lane writes one immutable JSON file per checkpoint into this directory. The coordinator, as the only writer, merges the files with:

```bash
python3 research/plan/scripts/progress.py apply-updates   # merge, bump revision atomically, re-render
python3 research/plan/scripts/progress.py check           # validate; exit 1 on errors
python3 research/plan/scripts/progress.py                 # render only
```

The renderer is [progress.py](../../../scripts/progress.py). Its outputs are [PROGRESS.md](../PROGRESS.md) and [progress.html](../progress.html). Its brief is [06-progress-tracker.md](../../../consumer-v1/06-progress-tracker.md).

## Update file

The file name is `<TASK>-<YYYYMMDDTHHMMZ>.json`, for example `D10-20260924T2315Z.json`. Never rewrite a file after it is committed. A correction goes in a new file with a later timestamp.

| Field | Required | Meaning |
|---|---|---|
| `task` | yes | A manifest task ID, or the ID of a support lane with no task (such as `TRACKER`). Any other ID is rejected. |
| `activity` | yes | One of `queued ready running review changes-requested integration blocked complete deferred`. These describe the lane. They do not replace the manifest `status`. |
| `at` | no | UTC time of the checkpoint in ISO 8601 form. If absent, the time in the file name is used. |
| `lane` | when ambiguous | The lane ID. It is needed when a task has more than one lane (for example `F2C-L` and `F2C-C`). A `slice` that matches exactly one lane also works. |
| `slice`, `head`, `base`, `branch`, `worktree`, `agent`, `owned_paths`, `isolation`, `next_action` | no | Each replaces the lane's value. `isolation` is `{ports, prefix, db}`. |
| `commands` | no | `[{"cmd", "exit", "summary"}]`. Replaces the lane's list. |
| `estimate` | no | `{optimistic_h, likely_h, pessimistic_h, confidence, basis, at}`. It is the **remaining** effort after inspecting the actual slice. Hours are numbers with optimistic ≤ likely ≤ pessimistic, or all `null` for unknown. `confidence` is `unknown`, `low`, `medium` or `high`. |
| `blockers` | no | A list of strings, joined into the lane's `blocker`. An empty list clears it. |
| `evidence` | no | Repo-relative paths, appended without duplicates. Box-only paths render as text, not links. |
| `wiring_requests` | no | Replaces the lane's list. The coordinator applies wiring; the tracker only displays it. |

## Ingestion rules

- Files are applied in `at` order. For each lane the newest checkpoint wins.
- An update whose `at` is not newer than the lane's `updated` is **rejected as stale**. The rejection is listed in the HTML.
- An unknown task ID, an unknown activity or an unreadable file is rejected.
- Impossible transitions are rejected:
  - leaving `complete`;
  - leaving `deferred` for anything other than `queued` or `ready`;
  - jumping from `queued` or `ready` straight to `review`, `integration` or `complete`;
  - any active state for a task with `dispatch_after_gate` while that gate is not accepted;
  - `complete` before the task's start dependencies are finished.
- Applied and rejected files are both recorded in the overlay `ingested` map, so a re-run is idempotent. Each result also adds an `activity_log` entry.
- Entering `review` or `integration` adds the lane to that queue; leaving the state removes it.
- A changed `likely_h` is logged with its `basis`. Every milestone whose forecast moves gets a "forecast … (because: …)" entry.
- The writer takes the lock `progress-state.json.lock` (`O_EXCL`). It re-reads `revision` and refuses to write if another writer changed it. It then writes a temporary file and calls `os.replace`, so no reader ever sees a partial file.

## Overlay schema 2 (`progress-state.json`)

| Key | Content |
|---|---|
| `schema`, `revision`, `updated` | `2`; a counter bumped on every write; the UTC time of the last write. |
| `program`, `program_doc`, `integration_branch`, `integration_head`, `base`, `main` | The program identity. |
| `deployed` | `{release, install, image, config{}, regime, target}` for the running candidate. |
| `agent_slots` | `{total, active, reserved, note}`. The implementation capacity used by the ETA is `total − reserved`. |
| `eta_params` | `{review_rework_fraction (default 0.3), integration_h_per_task (default 0.5), note}`. |
| `bands` | `[{id, name, tasks[]}]`, the program-22 delivery bands. They are dependency bands, not barriers. |
| `reused_baseline` | `{at, note, tasks[]}`. The tasks implemented or integrated before program 22 (39 + 5). The list is fixed so that the new-work denominators never shrink. |
| `lanes[]` | `{id, task (null for support), slice, agent, branch, worktree, base, head, owned_paths[] (default: the manifest's), isolation{ports, prefix, db}, activity, started, updated, blocker, next_action, deviation, estimate{…}, evidence[], commands[], wiring_requests[]}`. `deviation` records why a lane runs before its start dependencies. |
| `review_queue[]`, `integration_queue[]` | `{lane, task, head, since}`. Maintained by `apply-updates`. |
| `resource_locks[]` | `{id, kind (gpu, sql, runner, integration, ports), label, holder, since, until, tasks[], windows[{task, start, end}], note}`. Tasks under one lock run serially in the ETA. A GPU task with no window gets no date. |
| `gates{}` | BACKEND-LOCAL, BACKEND-READY, APP-LOCAL, APP-PILOT, each `{candidate{source, deployed, config}, cells[{id, verdict, evidence[]}], decision (null, accepted or rejected), decided_at, note}`. A verdict is `PASS`, `FAIL`, `BLOCKED`, `INVALID`, `NOT RUN` or `PENDING`. The cells must cover the root task's `test_ids`. A gate is green only when its decision is `accepted`, its root is implemented in the manifest and every cell is `PASS`. |
| `findings[]` | `{id (RV-01…RV-12), status (open, fixed or superseded), at, source, evidence[]}`. A closed finding needs evidence. The corrective tasks come from the manifest. |
| `historical_runs[]` | `{id, task, candidate, status, started, clock, output, scope, commit, evidence[], cells[{id, verdict, note, started, ends_earliest, ends}]}`. These are evidence scoped to their own candidate; they are never relabelled as E4C acceptance. |
| `verification.suites[]` | `{name, cmd, result, at, candidate}`, the last recorded result. |
| `inputs[]` | `{id, status (open or resolved), what, owner, blocks[]}`. An open input that blocks a task on a milestone's remaining path turns that forecast into "blocked pending …". |
| `activity_log[]` | Append-only `{at, by, what, source}`. |
| `history.v46` | A pointer to the [v46 snapshot](../tracker-v46/README.md) and its final numbers. |
| `ingested{}` | `file → {status (applied or rejected), at, reason}`. |
| `eta` | `{computed_at, params, milestones{E3C, E4C, E3A, E4: {gate, status, text, constraint, effort_h[o,l,p], wall_h[o,l,p], finish[], conditional, confidence, critical_path[], stale[]}}}`. Written by `apply-updates`; the renderer recomputes it at render time. |

### How the ETA is computed

1. **Remaining path.** Take every task reachable from the milestone through start and integration edges. A task with `dispatch_after_gate` also depends on the roots of that gate until the gate is accepted. Drop tasks that are implemented or integrated in the manifest, except a gate root whose gate is not yet accepted.
2. **Stop conditions.** If any task on the path is blocked by an open input, the status is `blocked` and the text is "blocked pending P-…". If a GPU-gated task has no window, or any task on the path has no lane estimate, the status is `unknown`. Neither case ever shows a date. When every estimate is known, a conditional duration is still shown and labelled "not a date".
3. **Task duration.** Each task takes the maximum of its open lanes' hours (parallel slices), multiplied by (1 + review/rework fraction), plus one serial merge. Its effort is the sum of those lanes' hours.
4. **Wall-clock.** It is the largest of four bounds, computed separately for optimistic, likely and pessimistic hours:
   - the longest dependency path, which respects allocated GPU windows;
   - the sum of task durations under each exclusive lock (GPU box, SQL writer, runner directory);
   - the integration queue (number of merges × hours per merge);
   - the total effort divided by `total − reserved` slots.

   Whichever bound is largest is reported as the **controlling constraint**.
5. **Output.** Effort (engineering hours) and wall-clock (elapsed hours) are shown separately. An estimate older than 6 h is stale. The page shows a stale banner when it is viewed more than 15 min after generation.

## Verification log

- 2026-09-24: Created with overlay schema 2 and the consumer-v1 tracker (TRACKER lane, base `dff31efc`). Rules are exercised by `research/plan/scripts/test_progress.py`.
