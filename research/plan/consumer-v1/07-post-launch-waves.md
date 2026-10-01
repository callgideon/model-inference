# Post-launch waves (LW0–LW6, conditional C1–C3)

Status: checked-in wave map for the 57 post-launch (deferred) manifest tasks, copied 2026-09-28 from the coordinator's wave-5 plan §4 (plan §3 item 9; [R155](../08-contracts-v1-encoding.md)). [progress.py](../scripts/progress.py) reads the table below and renders the "Post-launch waves" section of the [HTML tracker](../evidence/coordinator/progress.html) and [PROGRESS.md](../evidence/coordinator/PROGRESS.md). [tasks.json](../tasks.json) stays the only task graph and the only implementation status; [progress-state.json](../evidence/coordinator/progress-state.json) stays the only lane activity. Waves are named LW0 to LW6 so they cannot be confused with the task IDs W2 and W3; C1 to C3 are the conditional waves.

## How the tracker reads the table

- One row per wave. The wave ID is the first bold word of the first column.
- Lanes are `name [ID, ID (note), …]`, separated by ` · `. IDs inside a lane run serially in the order given; a parenthetical is a note (an "after X" merge constraint), not an ID.
- An ID that is not a manifest ID is a slice of the manifest ID before its first hyphen (`L2-SQL` is L2's SQL slice, `I2L-OBS` I2L's observability slice). Slices are shown but never counted.
- A lane written in italics (`*discovery*`) does not gate its wave: its tasks are counted in the wave's denominator but not in its exit.
- Gate task (lane) lists `ID (lane)` pairs, or `(lane)` alone for a wave without a manifest task. The lane is the plan's lane name; the tracker matches it against the overlay lane whose slice reads `(wave5 <wave> lane <name>` (or whose ID is the name).
- `progress.py check` fails when a deferred manifest task is in no wave or in two, when a launch-scope or baseline task is listed, when an ID is neither a manifest ID nor a slice of one, or when a gate task is not one of its wave's IDs.

## Waves and gates

| Wave | Lanes, with in-lane order | Gate task (lane) | Gate to start | Exit |
|---|---|---|---|---|
| **LW0 pre-wave** | coordinator (one commit; no manifest task) | (LW0) | APP-PILOT accepted plus the P-17 record (overridden by the user's post-launch directive of 2026-09-27; see the LW0 evidence) | merged: rulings R150–R155, the Lab tracks and 57500–57599 band, the D10 split, the Lab make targets, the apps/lab package, the `activated` tracker category |
| **LW1 roots** | lab-sql [L2-SQL, D6F, D7 (after F3's reviewed commit, 13:25)] · lab-access [L2 (after L2-SQL)] · lab-app [L1 (merges after L2)] · lab-contracts [F3, H1 (after L2)] · trace-ship [T2I] · *discovery* [X1, X3, X5] (docs; does not gate) | — | LW0 merged, APP-PILOT, P-17 | merged: L2-SQL, D6F, D7, L2, L1, F3, H1, T2I; `make check` green |
| **LW2 M0 services, M1/M2 foundations** | lab-sql [D6J, L3-SQL, D9] · lab-access [L3 (after L3-SQL)] · lab-app [L4 (after L3), V1M] · datasets [N1, N2] · trace-ship [T2F, T3] · feedback [G4F, C3F] | — | LW1 exit; `packages/shared` QueryPort copy landed | merged: D6J, L3, D9, L4, V1M, N1, N2, T2F, T3, G4F, C3F |
| **LW3 M0 gate, runner, judge, content** | lab-sql [C2-RPC, D8] · lab-operate [I2L, E3L] · eval-runner [B1, B2] · judge [J2, C3L] · content [C2 (after C2-RPC), G4T] · rollout-routing [R1] | E3L (lab-operate) | LW2 exit | E3L green on one SHA plus I2L packaging (the local half of LAB-OPERATE; staging waits on P-08); B1, B2, J2, C3L, C2, G4T, D8, R1 merged |
| **LW4 M1 UI, M2 ops, M3 labels, M4 control** | eval-ops [B3, I5] · trace-ui [V2, V3] · judge [J3 (after V2), P2 (after P1)] · datasets [N3, N4] · pipelines [P1, P3 (after B3)] · rollout-control [R2, R3] | — | LW3 exit | all 12 merged |
| **LW5 M1/M2 gates, UIs, workers** | lab-observe [I2L-OBS, E5L] · eval-ui [B4] · lab-evaluate [E6L (final run after B4)] · pipeline-ui [P4] · release-ui [R4] · lab-workers [I6, I7] | E5L (lab-observe), E6L (lab-evaluate) | LW4 exit | LAB-OBSERVE local (E5L + I2L with the worker extension; staging waits on P-08); LAB-EVALUATE-LOCAL (E6L) accepted; P4, R4, I6, I7 merged |
| **LW6 M3/M4 gates** | lab-improve [E7L] · lab-rollout [E8L] | E7L (lab-improve), E8L (lab-rollout) | E6L and E5L accepted, LW5 merged | LAB-IMPROVE-LOCAL, LAB-ROLLOUT-LOCAL, then COMPLETE-LOCAL |
| **LW7 API-first lifecycle and UX** (2026-10-01, base 2eeff879) | api-contracts [AP-00] · api-identity [AP-01, AP-02 (after AP-01), AP-03 (after AP-01)] · api-artifacts [AP-04, AP-05 (after AP-04), AP-06 (after AP-05)] · api-traces [AP-07, AP-08 (after AP-07)] · api-frontends [AP-09] · api-improve [AP-10] · api-lifecycle [AP-11] · ux-foundations [UX-00, UX-01 (after UX-00), UX-02 (after UX-00)] · ux-app [UX-04, UX-07 (after UX-01)] · ux-lab [UX-03, UX-05, UX-06, UX-08, UX-09, UX-10] · ux-verify [UX-11] | AP-11 (api-lifecycle) | lifecycle-plan merge 2eeff879 (planning 3012dfb0); the E4C rerun on the box is serialized by the coordinator and does not gate local lanes | API-BOUNDARY, API-LIFECYCLE and UX-JOURNEY on one merged SHA; the four verdicts (API boundary, lifecycle, quality, operations/performance) recorded in STATUS.md |
| **C1 callbacks** (conditional, any time after APP-PILOT) | lab-sql [G5-SQL] · callbacks [G5] | G5 (callbacks) | explicit user selection (tasks.json:853) | API-CALLBACK; APP-LOCAL conditional (tasks.json:4668) rerun |
| **C2 fleet** (conditional) | fleet [I4] | I4 (fleet) | E4C and APP-PILOT accepted, P-16, purchase approval (tasks.json:1421) | FLEET-GATE |
| **C3 expansion** (conditional) | video [X2] · robotics [X4] · alt-backend [X6] | — | approved X1/X3/X5 plus P-13/14/15; X2/X4 also need E6L accepted, and X6 needs R3 merged | per-task oracles |

The plan checked this assignment mechanically against tasks.json: 52 + 5 = 57 IDs, each exactly once, no dependency on a later wave, the only same-wave start dependency D7 ← F3 handled by the lane order, and no two lanes of a wave owning overlapping paths. The tracker re-checks the first property on every `check`.

**COMPLETE-RELEASE** additionally needs hosted Lab (P-08), a hosted apply of the Lab migrations (R151), and staging runs for I2L, I5, I6 and I7. Those are operator-run steps, not lanes, and are not in this table.

## What the tracker shows per wave

- **Implemented k / n:** manifest `implemented`/`integrated` over the wave's manifest IDs (slices excluded, non-gating lanes included).
- **Lanes:** every overlay lane whose slice names the wave (`(wave5 <wave>`), whose ID is the wave ID, or whose task is one of the wave's IDs, with its activity (running, review, complete, blocked, …).
- **Gate state:** EXIT MET (the wave gate accepted) only when every task of the wave's gating lanes is implemented and every gate lane is present in the overlay and complete; otherwise PENDING with the reasons. The exit text above is shown beside it. This is a tracker reading of manifest status and lane activity, never a release-gate decision, so it deliberately does not use the word ACCEPTED, which the tracker keeps for recorded gate decisions: the release gates (LAB-OPERATE, …) are decided in progress-state `gates` as before.
- **ETA:** the eta_params arithmetic of the milestone forecasts, over the wave's live lanes: each lane's remaining hours × (1 + review_rework_fraction) + integration_h_per_task; wall-clock is the largest of the longest lane, the serial merge queue and effort over the free implementation slots. An open input that blocks one of the wave's open tasks gives "blocked pending", and a live lane without an estimate, or a gating lane of the map with open tasks that no live overlay lane serves (its slice names that lane, or it is keyed to one of that lane's open tasks), gives "unknown" naming the uncovered IDs: never a date. Only lanes serving open gating work enter the arithmetic, so a lane for a finished task (a blocked V1M after V1M is implemented) forecasts nothing.

## Audit log

- 2026-09-28: Created (lane tracker-waves, LW0 WR-LW0-3) from the wave-5 plan §4 table, adding the LW0 row and the Gate task (lane) column the tracker needs; no wave, lane, order, gate or exit changed. Read by `progress.py` (`load_waves`).
- 2026-09-28: Fix round (findings 0-TW-1, 0-TW-2): the ETA rule now requires a live lane per gating map lane with open work and ignores lanes for finished work; no wave, lane, order, gate or exit changed.
- 2026-10-01T23:21Z: LW7 row added for the API-first lifecycle (AP-00–AP-11) and UX (UX-00–UX-11) tasks registered at the lifecycle-plan merge 2eeff879; the conditional rows unchanged
