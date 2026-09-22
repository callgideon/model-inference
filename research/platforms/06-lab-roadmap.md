# Platform 2 — roadmap and release gates

The Lab roadmap is separate from [App's roadmap](04-app-roadmap.md). Shared foundation is reused, while Lab features have their own release evidence. Dates and staffing remain unestimated. Lab is follow-on to the accepted Marlin App launch candidate. LAB-M2–M4 now have [detailed task/slice handoffs](../plan/13-lab-improvement-handoffs.md); specialized M5/hardware trials use [explicit discovery/activation gates](../plan/14-expansion-gates.md).

| Milestone | Deliverable / value | Dependency and scope | Exit gate |
|---|---|---|---|
| LAB-M0: operate a provider model | Provider access, model/serving versions, private dev deployment, controlled prod publication, rates and health | S1/F2P/D1R plus L1–L4, I2L/E3L; assisted Marlin onboarding | LAB-OPERATE: provider A cannot affect B; dev is private; publication reaches App; revoke/promote/rollback audited and request versions pinned |
| LAB-M1: understand deployment | Authorized trace explorer, feedback/review, permission controls; calibrated judge dry-run and separately budgeted live pilot | T, D6F/D6J, G4F/G4T, C2/C3F/C3L, V, J; consumer core need not await these | LAB-OBSERVE: content isolation/revocation/expiry, bounded capture, no duplicate feedback or external judge submission; operator approves enabled data uses |
| LAB-M2: compare and select | Dataset import/versioning, experiment runner, prompt/harness variants, baseline/candidate reports and checkpoint-triggered benchmarks | F3/D7/N1/N2/N4/H1/B1–B4/I5/E6L; owned imports independent of E5L/live traces | LAB-EVALUATE: two serving versions reproduce a frozen benchmark, report failures/slices/cost/latency, and satisfy predefined quality thresholds |
| LAB-M3: improve with data | Review exports, teacher-labeling jobs and external SFT/adapter/preference-training integrations | D8/N3/P1–P4/I6/E7L; provenance, purpose grants, budgets; manual training export/import baseline | LAB-IMPROVE: a new checkpoint links to authorized data/config; held-out improvement verified; second iteration can use permitted post-deployment data |
| LAB-M4: controlled rollout and optimization | Shadow/canary/A-B, release evidence, optimization/heterogeneous backend comparisons | D9/R1–R4/I7/E8L; independent of training after evaluation; measured runtime parity | LAB-ROLLOUT: routing consistency, non-inferiority/cost decision protocol, rollback and hardware parity demonstrated; no uncontrolled production tool effects |
| LAB-M5: specialized sessions | Marlin live-video windows; bounded π0.5/ROS2 trial; modality-specific telemetry | X1/X2 video, X3/X4 robotics; X5/X6 hardware separately; real workload contract and allocated target | Each workload meets measured freshness/throughput/quality requirements under failures before general availability |

## Implementation packages and validation focus

| Package | Deliverables | Independent work and acceptance |
|---|---|---|
| Dataset lifecycle | Manifest/schema, import validator, dedup/splits, source access links, export | Can develop against synthetic owned data; reject schema and split leakage; expiry/revocation propagates |
| Evaluation execution | Frozen run config, deterministic case IDs, worker lifecycle, bounded retries, result artifacts | Can use fake models then actual endpoints; retries cannot produce duplicate scores/costs; cancellation is durable |
| Comparison reports | Baseline/candidate pairs, task slices, missing-case accounting, confidence and cost/latency | Use versioned fixtures; no aggregate hides a severe required-slice regression |
| External checkpoint hook | Authenticated event, checksum registration, suite subscription and budget | Duplicate/out-of-order events deduplicate; malformed artifacts cannot deploy |
| Annotation integration | Existing pipeline import, review provenance/disagreement, teacher job adapter | Ground-truth and synthetic labels remain distinguishable; authorized exports and spending only |
| Training integration | Adapter config, external run state, checkpoints, cost, lineage | Resume/poll without duplicate paid jobs; held-out eval before candidate promotion |
| Release experiments | Assignment and analysis unit, cohort state, drift guardrails, canary rollback | Repeat requests preserve intended cohort; explicit pins remain honored; uncertain results block unsupported claims |
| Specialized runtime | Stream/episode schema, transport, scheduler class, recorder adapter and failure behavior | Benchmark observed freshness and actual task outcome; compare proposed/executed actions for robotics |

Each [detailed handoff](../plan/13-lab-improvement-handoffs.md) specifies reviewed slices, dependencies, owned paths and failure oracles. Split any slice exceeding a 2–8 hour review unit before assignment; do not collapse a whole milestone into one worktree. Dates require actual team availability and code state. See [all task states](../plan/17-task-ledger.md).

## Investment order and decision criteria

Prioritize Marlin endpoint stability and provider visibility because an actual model-team relationship exists. Validate robotics demand and latency on one application while preserving specialist runtime boundaries. LLM migration tooling can reuse datasets/evaluations; commit to broader integration work when customer benchmarks and volume justify it. Speech remains parked.

Do not assume annotation/training orchestration is the business's strongest differentiator. Buy/integrate established data, visualization and training tools when they preserve lineage and permissions. Invest internally in inference preparation, kernels, memory, batching, scheduling and heterogeneous compute where measured performance changes operating economics. Expose these results as versioned serving options rather than requiring users to manage low-level hardware details.

## Independent release discipline

LAB-M0 can launch without datasets or judge. LAB-M1 may start with authorized internal/provider-owned data while customer sharing remains disabled. LAB-M2 can operate on imported benchmarks before production trace collection. Managed RLHF/RL training is not required for checkpoint benchmarking. Every enabled feature must pass its own local integration and staging gates; a roadmap row is not permission to provision infrastructure or incur external training charges.
