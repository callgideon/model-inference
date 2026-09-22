# Platform 2 — Provider Lab requirements

**Latest sequence (2026-09-22):** finish the [Marlin endpoint backend](../plan/18-marlin-backend-first.md), including recovery and measured optimization; then launch App, then build Lab. Product requirements below are retained. Headless provisioning and backend gates remove the App UI from endpoint readiness.

**Delivery priority:** Backend endpoint first, then App launch for Marlin SOP verification over large robotics datasets. Lab is the follow-on product; [detailed implementation packages](../plan/13-lab-improvement-handoffs.md) now cover M2–M4. Recorded robotics SOP analysis does not depend on a robot action-policy endpoint or live-input transport.

Location: `apps/lab`. Lab helps model teams operate and improve their models on the shared inference infrastructure. The first provider is the Marlin team; the first useful deliverable is a reliable hosted model with visibility and controlled version promotion.

## Users and shape

Provider engineers register versions and configure dev deployments. Provider administrators manage their workspace and publication proposals. Reviewers inspect permitted examples and annotations. Platform operators control infrastructure, approve public publication and enforce shared resource budgets. These permissions are independent from consumer signup.

Initial navigation: **Overview, Models, Deployments, Requests, Access & Settings**. Add **Datasets, Evaluations, Experiments** when those workflows are usable; add training integrations later. Do not fill the first release with nonfunctional pages for the entire roadmap.

The main object is a **versioned serving configuration**: base weights + adapter + tokenizer/processor + prompt/harness + engine/build settings. Evaluation also pins dataset, evaluator and execution environment. A model checkpoint alone is insufficient to reproduce observed behavior.

## Requirements and phased scope

| ID | Capability | Acceptance oracle | Milestone |
|---|---|---|---|
| LAB-01 | Provider workspaces, protected membership and roles | Consumer owner cannot self-elevate; provider A cannot discover/read provider B's private resources | LAB-M0 |
| LAB-02 | Assisted model/version registration with artifact hashes, license/provenance, supported runtime and capability schema | Immutable version; invalid artifact/schema rejected; registration does not execute arbitrary uploaded code | LAB-M0 |
| LAB-03 | Private dev and production deployment revisions, health, limits and rollback | Dev not public; promote pins revision; old admitted requests stay pinned; rollback changes future requests | LAB-M0 |
| LAB-04 | Proposed/published model rates and catalog metadata | Only authorized publication becomes consumer-visible; price history immutable; rate snapshot test passes | LAB-M0 |
| LAB-05 | Provider-scoped health, usage, latency and failure analysis | Own deployment aggregates work without exposing customer identities/content; trace loss reported | LAB-M0/M1 |
| LAB-06 | Trace search/detail and temporal media evidence | Explicit customer grant plus capture/retention policy; cross-provider and cross-customer access denied | LAB-M1 |
| LAB-07 | Feedback and human review queues, corrections and provenance | Original output retained; labeler, method, version and disagreement captured; customer feedback cannot forge calibration authority | LAB-M1 |
| LAB-08 | Imported and trace-derived versioned datasets | Immutable manifests, schemas, licenses/use permissions, dedup and stable train/dev/test splits; revocation lineage enforced | LAB-M2 |
| LAB-09 | Offline baseline/candidate evaluation and experiment comparison | Same frozen cases/harness; failures/missing outputs included; sliced scores, uncertainty, quality/cost/latency and raw evidence | LAB-M2 |
| LAB-10 | Continuous checkpoint benchmarks from external training | Idempotent checkpoint event creates one authorized evaluation against pinned suites; visible failed/skipped states | LAB-M2 |
| LAB-11 | Versioned prompts, preprocessing and harness references | Evaluate one controlled change or explicitly record a multifactor change; tool side effects sandboxed during replay | LAB-M2 |
| LAB-12 | Teacher annotation and evaluator jobs with budgets/consent | Separate data-use permission, reserved budget, no ambiguous duplicate submission, judge calibration | LAB-M1 pilot / M3 expansion |
| LAB-13 | External training/distillation integrations and checkpoint import | Dataset/config/run/artifact lineage, costs, failure recovery and held-out validation; no automatic promotion from training loss | LAB-M3 |
| LAB-14 | Shadow/canary/A-B comparison and controlled release | Explicit allocation unit, bounded traffic/budget, consent, cohort balance, quality threshold and rollback; pinned consumers respected | LAB-M4 |
| LAB-15 | Optimization experiment registration | Quantization/pruning/speculative/engine/hardware changes become new serving versions; quality/performance parity evidence | LAB-M4 |
| LAB-16 | Streaming video and robotics extensions | Workload-specific schemas/deadlines, causal evaluation and integration gates; no generic chat compatibility claim | LAB-M5 discovery and staged trials |

An initial operator-managed provider onboarding process is acceptable. Self-service arbitrary code/container execution requires its own isolation, provenance, resource and egress design; it is outside the first Lab release. Endpoint configuration uses validated fields/adapters, not arbitrary Python in a dashboard.

## Data-to-deployment loop

Import existing benchmark data and the provider's pipeline outputs before building a general annotation studio. Preserve media timelines, source identifiers, labels, method/version, review state and rights. Deduplicate by content and group related examples by source episode/customer/session when forming splits. A public benchmark is one evidence source, not proof of production adequacy.

Evaluate the unchanged candidate first. Compare prompt/harness changes next where practical. Import externally trained candidates and compare them against the same frozen holdout. Later support training adapters: SFT is a training objective, LoRA an adaptation technique, and preference/RL methods have different data/execution requirements. They should not be presented as interchangeable buttons.

Every comparison records the entire serving and harness configuration. Replay tool-using applications with mocked/recorded side effects or a sandbox; do not call production actuators or external mutation tools during offline evaluation. Report absent tool/environment coverage explicitly. A larger-model judge is one versioned evaluator; human calibration and task-specific metrics remain necessary. Do not equate a fluent teacher answer with correct labels.

Production data is selected only under the access policy. Annotations create a new dataset version; they do not overwrite original evidence. Promotions require a predeclared quality gate and operational validation. Post-deployment failures become candidates for the next authorized dataset iteration.

## Modality priorities

**Marlin SOP workload:** complete robust optimized endpoint hosting first, followed by the consumer App, for SOP verification over large robotics datasets. Freeze episode grouping, temporal label/rubric semantics, source rights and task-specific metrics when the actual benchmark is supplied; software fixtures are not accuracy evidence. Integrate the team's existing SAM/Gemini-assisted annotation outputs and external fine-tuning checkpoints as user-reported workflows. Provide clip/frame time references, preprocessing identity, temporal/SOP task rubrics and slice analysis. Separate output SSE from live-video input processing. Live windows require timestamps, overlap/dedup, backpressure, bounded buffering, frame sampling and causal tests; they do not create native temporal memory in a stateless model.

**Robotics:** π0.5 is a vision-language-action policy. Start with a specific robot/task and measured deployment location. Version camera/state/action schemas, normalization, action horizon and timestamps. Integrate ROS2/MCAP recording and existing visualization tools where appropriate. Compare proposed versus executed actions and episode outcomes; observations stale at inference completion are a first-class failure. Large world models may help annotation/simulation, but are not automatically interchangeable with action policies. The OpenPI remote-inference interface is a useful adapter reference, not proof that arbitrary WAN latency is acceptable. [OpenPI remote inference](https://github.com/Physical-Intelligence/openpi/blob/main/docs/remote_inference.md).

**LLM migration:** import incumbent traces/benchmarks; run candidate baseline and prompt/harness variants; compare quality by task slice and measured cost/latency; support external teacher/training pipelines. Win a defined workload before generalizing to every agent framework. Record exact model identifiers and revisions rather than relying on unverified future names.

**Speech:** deferred pending a separate requirements discussion.

## Lab architecture and success

Lab server actions authorize provider operations against shared records. Separate Lab workers handle evaluations and data transformations; they do not run long jobs inside Next.js request handlers. The shared inference gateway remains the serving entry point for dev/prod deployments with environment-scoped keys and explicit billing/budgets.

Measure time to a working dev endpoint, time to reproduce a failure, authorized trace completeness, evaluation reproducibility, model promotion/rollback time and the proportion of deployed versions with valid evidence. The later platform outcome is a second improvement iteration informed by actual deployment data. Do not make that long-term milestone a prerequisite for the consumer free launch.
