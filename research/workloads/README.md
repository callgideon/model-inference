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
