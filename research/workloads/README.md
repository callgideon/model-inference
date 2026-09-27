# research/workloads — what a launched endpoint actually serves

One document per workload that the platform operates, pinning the **served**
configuration: the model artifact and its revision, the processor and preprocessing
profile, the request/response surface and caps, a client recipe for the real usage
pattern, and the separation between endpoint conformance and task-quality certification.

These documents sit between the per-model research in
[`research/models/<exp>/`](../models/) (architecture, sizing, GPU pairs — estimates) and
the implementation plan in [`research/plan/`](../plan/) (contracts, tasks, gates). A
workload document answers "what can we honestly publish and bill for today", cites a file
and line or a dated source for every value, and marks every unknown
**⚠️ TO BE VERIFIED** with the method that would close it, per
[`research/METHODOLOGY.md`](../METHODOLOGY.md).

| Document | Workload | Status |
|---|---|---|
| [`marlin-sop.md`](marlin-sop.md) | SOP verification over recorded robotics video with Marlin-2B: finite clips, dense captions with timestamps, temporal grounding | Profile pinned 2026-09-22 (S2M). Resolves [P-06](../plan/15-pending-inputs.md) for the artifact/processor/surface with four named ⚠️ digest items; registers P-07 certification inputs and **provisional** P-18 criteria; lists fourteen runtime-versus-contract discrepancies for M2/M3/G2/G1R/W3/F/E1B |
| [`video/live-video-contract.md`](video/live-video-contract.md) | X1 discovery draft: live/streaming video freshness contract (causal windows, hops, loss); trial BLOCKED on P-13 (task, W/H, deadlines, ground truth, trial target) | draft (planned) |
| [`robotics/robot-freshness-contract.md`](robotics/robot-freshness-contract.md) | X3 discovery draft: robot observation-age / stale-action contract over OpenPI remote inference and MCAP; trial BLOCKED on P-14 (design partner, robot/task, schemas, authorisation) | draft (planned) |
| [`hardware/non-nvidia-backend.md`](hardware/non-nvidia-backend.md) | X5 discovery draft: candidate MI355X × vLLM-ROCm backend, five gates, idle-capacity trap; no purchase; BLOCKED on P-15 (choice, access, thresholds) | draft (planned) |

## Rules for a document in this directory

1. **Pin, do not describe.** A value without a file/line, a dated API call or a committed
   measurement does not belong here.
2. **Served is not specified.** Say which surfaces are mounted and which are planned. A
   contract that no route serves is labelled as such.
3. **Never invent a target.** Performance and availability criteria are either an owner's
   input or **explicitly provisional**, and a provisional row is never quoted without its
   label.
4. **Conformance and quality are separate sections.** An endpoint can launch with honest
   capability limits; it cannot claim task accuracy without a rubric, ground truth, a
   split and thresholds.
5. **End with a verification log**, appended to and never rewritten.

## Verification log

- 2026-09-22 (S2M): Directory and index created with the Marlin SOP launch profile. No
  measurement, deployment or code change is claimed by either file.
- 2026-09-27: X1/X3/X5 discovery drafts indexed (LW1 discovery lane; owner approval pending on P-13/P-14/P-15). `video/check_discovery.py` checks the three contract documents.
