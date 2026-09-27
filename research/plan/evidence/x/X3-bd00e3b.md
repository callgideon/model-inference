# X3 — robot/task and inference-freshness contract discovery (slices a–c) — evidence

- Lane `discovery`, wave LW1, branch `codex/w5-discovery`, base `9a6c3685`, head `bd00e3b` (X1 at `cdb8681`/`c501f0c`).
- Brief: wave5-plan §5 LW1 "discovery"; 14-expansion-gates.md §X3; oracle ROBOT-CONTRACT; pending input P-14.
- Tasklocal key: none. No docker, ports, DB, hosted service, AWS, purchase or secret touched. Web reads only (public GitHub/mcap.dev pages).

## Changed paths

- `research/workloads/robotics/robot-freshness-contract.md` — X3.a (OpenPI remote-inference + README facts, MCAP timestamp semantics, all fetched 2026-09-27), X3.b (observation age `a(i)`, stale rule, proposed-vs-executed records, measurement plan, fallback authority), X3.c (replay → simulator → authorised hardware order, MCAP mapping, draft X4 split), 18-row contract table, audit log.
- `research/workloads/video/check_discovery.py` — `SPECS` gains the robotics document (P-14; 18 required fields from X3.a–c and the ROBOT-CONTRACT failure oracle).

## Commands (cwd `research/workloads/video` unless stated)

| Command | Exit | Result |
|---|---|---|
| `python3 -m unittest test_check_discovery` (spec registered, document absent) | 1 | seam failure recorded: `test_real_documents_pass` — `robotics/robot-freshness-contract.md` missing |
| `python3 -m unittest test_check_discovery test_mutants` | 0 | Ran 14 tests, OK (10/10 mutants killed; every case covered) |
| `python3 check_discovery.py` | 0 | 2 documents, 0 errors |
| `cd research/plan/scripts && python3 validate_plan.py` | 0 | PASS (934 links / 291 documents) |

API/console/Lab checks not applicable: no code under `apps/` or `packages/` changed.

## Findings

- Marlin emits text only; π0.5 is a separate VLA integration; OpenPI's websocket interface is an adapter reference, not a latency proof.
- 16 of 18 contract fields BLOCKED: P-14; stale-action rejection and proposed-vs-executed evidence are plan-sourced rules (05-lab-spec.md:54, 04-verification.md:116).
- ⚠️ π0.5 action horizon / control frequency / normalisation stats and per-checkpoint weight licence not confirmed from the fetched pages; method recorded in §1.1.
- ROS 2 releases page returned access-denied; not cited.

## Wiring requests

1. `research/workloads/README.md` index: add a row for `robotics/robot-freshness-contract.md` (X3 discovery draft, BLOCKED on P-14).

## Estimate (remaining for the lane after X3)

optimistic 2 h / likely 4 h / pessimistic 8 h, confidence low-medium; basis: X5 (3 slices; most inputs already in `research/gpus/mi355x.md` and `research/models/marlin2b/mi355x.md`) plus one review round.
