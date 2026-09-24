# Implementation-session progress tracker and parallel coordination

Status: implementation-session instructions, added 2026-09-24. The user explicitly requests maximum parallel agents/worktrees and an HTML tracker covering task progress and ETA. This is coordinator support work alongside S3/E2C, not a consumer product feature or a second task graph. [Program 22](../22-consumer-v1-implementation.md) and [manifest](../tasks.json) remain authoritative.

## Extend the existing tracker first

Reuse these files instead of creating an unrelated dashboard:

- [Renderer](../scripts/progress.py)
- [Coordinator state overlay](../evidence/coordinator/progress-state.json)
- [HTML output](../evidence/coordinator/progress.html)
- [Markdown output](../evidence/coordinator/PROGRESS.md)

The existing renderer hardcodes the E4B closure, old task bands and cadence estimates. It treats implemented/integrated as done, which is unsuitable for interpreting the new release gates. Preserve the v46-era state/history with an immutable snapshot or commit reference, then migrate the renderer/state deliberately. Do not run the old renderer and present its result as the new program's progress.

A tracker support agent can inspect/build this during S3 reconciliation and E2C environment inventory. Its first output should show all current manifest tasks and honest baseline states; ETA can initially be unknown. S3's committed evidence reconciliation may unblock F2C while tracker polish continues. Do not wait for a polished dashboard before implementing ready tasks.

## Authority and records

`tasks.json` owns task IDs, scope, dependency edges, release-gate roots and evidence-backed implementation status. `progress-state.json` owns assignments, worktrees, current activity, estimates, blockers, timestamps and candidate-specific gate decisions. Generate both HTML and Markdown from those same inputs. The HTML must never be hand-edited or become an independent source of completion claims.

Each active task record should include task/slice ID, owner/agent, branch/worktree, base/head/integration SHA, owned files, isolated ports/DB/object namespace, activity state, started/updated timestamps, blocker/next action, dependencies, acceptance checklist, relevant commands/evidence links, actual elapsed effort and remaining effort range with confidence/basis. Record implementation, real-service integration, deployment and release acceptance separately. Activity states can be queued, ready, running, review, changes-requested, integration, blocked, complete or deferred; do not replace the manifest's existing status vocabulary.

Gate records include gate ID, candidate source/deployed/config identity, required cells, PASS/FAIL/BLOCKED/INVALID/NOT RUN per cell, evidence links and explicit decision/date. A completed E4C software task must not automatically turn BACKEND-READY green. Old E4B acceptance, if later available, remains historical evidence scoped to its candidate and is not silently relabelled E4C acceptance.

Use one coordinator writer, atomic file replacement and a lock/revision check. Agents submit separate structured handback/update files; they do not concurrently rewrite the shared state or manifest. Keep an append-only activity/checkpoint log. Conflicting or stale updates must be rejected or flagged rather than overwriting newer progress. Unknown task IDs and impossible dependency/gate transitions must be visible validation failures.

## Required HTML views

1. Overview: current integration/deployed candidate, update age, current delivery band, active/available agent slots, actionable blockers and next ready work.
2. Separate progress summaries for backend corrections, App completion and deferred Lab/hosting. Preserve older foundations as a reused-baseline category; they must not inflate the percentage for newly requested work. Label task-count progress and acceptance-checklist progress with explicit denominators; neither implies launch readiness.
3. Searchable/filterable task table by task, lane, owner, product, activity and blocked/ready state. Expand a task to see its acceptance checklist, commits, commands, evidence and next action. Include all manifest tasks, with deferred and superseded items available without cluttering the default active view.
4. Agent/worktree board: who is doing what, file boundaries, review queue, integration queue and resource locks. Flag overlapping writers before dispatch.
5. Dependency/critical-path and milestone view: E3C, E4C, E3A and E4; explain which dependency, resource, input or test window controls each forecast. Backend acceptance precedes App dispatch; Lab/fleet remain gated.
6. Verification view: test suites, last result, candidate SHA, failed/skipped/invalid cells, soak progress/remaining time and reconciliation status. Show elapsed runtime separately from expected finish.
7. Timeline/change log, unresolved P-inputs, ETA ranges/confidence and stale-estimate warnings. Show UTC timestamps and explicit timezone for dates; users should not need to inspect JSON to understand state.

Deliver a self-contained responsive HTML file with embedded data/assets that opens locally without a server or external CDN. Keep keyboard-accessible filters, readable contrast and text labels beyond color. A snapshot has a visible last-generated time; browser refresh is not evidence collection. Optional localhost refresh must preserve the offline snapshot. Escape untrusted notes/titles and embedded JSON safely; no secrets, raw customer content or credentials in the page. Relative evidence links must work from the output location.

## ETA method

- Ask each lane for optimistic/likely/pessimistic **remaining effort**, estimate timestamp and basis after inspecting its actual slice. Initial estimates may be low-confidence or unknown. Calibrate from observed cycles; do not extrapolate old wave cadence or infer speed from the number of agents alone.
- Compute milestone ranges from remaining dependency paths plus actual worker capacity, exclusive file ownership, the single SQL writer, integration/review queues and serialized GPU/soak/restore windows. Parallel durations do not simply add, and total effort divided by agent count is not a valid completion forecast.
- Include review/fix/retest and deployment/soak/drain time. Show active engineering effort separately from elapsed wall-clock forecast. Pause/interruptions and unavailable service/GPU windows affect wall-clock availability, not fabricated completed effort.
- If an external input/resource has no known availability, use `unknown` or `blocked pending <input>`. Optionally show a conditional duration after that input arrives, clearly distinguished from an absolute finish date. Never assign a guaranteed launch ETA while mandatory acceptance inputs remain unresolved.
- Regenerate forecasts when dependencies, estimates, staffing, test duration or scope change. Record why a forecast moved. Mark stale estimates visibly; no countdown implying progress while an agent is stalled.

## Parallel dispatch and update cadence

Use maximum **safe** concurrency supported by the implementation environment. After S3/F2C prerequisites, the seven disjoint runtime lanes are D10, M5, W5, G7, G8, E1C and I8; M6 follows M5. Read-only reviewers and tracker support can run alongside writers if agent/resource limits permit. E2C/E3C/E4C share runner paths and require serialized ownership; shared GPU deployments/fault tests have one lock. More agents must not create duplicate implementations or bypass dependency gates.

At each handoff/merge/failure/blocker/estimate change, update the overlay and regenerate outputs. During active work, checkpoint around every 5–10 minutes when possible, retaining a visible stale indicator when updates stop. This is a session coordination requirement, not a promise of an unattended background automation. On restart, reconcile commits/processes/artifacts before resuming previous assignments.

## Verification and handback

Add focused tracker tests for full task coverage, dynamic gate closure, implemented-versus-accepted distinction, unknown inputs, dependency/resource-constrained ETA, stale timestamps, malformed/unknown IDs, safe escaping and relative links. Verify that a task omitted from the manifest view, a pending E4C decision shown as passed, or an unallocated required GPU assigned a definite finish date makes a test fail. Open the HTML and exercise filters/details in a browser at desktop and narrow widths; verify updates survive reload.

Keep the existing `python3 research/plan/scripts/progress.py` entrypoint or document a compatible replacement. Hand back the exact render/check commands, output path and optional local-view URL, plus initial baseline reconciliation. Commit renderer/state/output changes together at meaningful checkpoints; do not invent task progress to populate the dashboard.
