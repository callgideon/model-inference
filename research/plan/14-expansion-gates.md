# Conditional modality and hardware workstreams

These are bounded discovery and trial plans, not unqualified commitments to streaming, robotics or every chip. All remain planned and conditional. Start only the discovery that available inputs support; do not invent workload targets. Speech, payments, generic managed RL and arbitrary provider code remain deferred without executable tasks.

Read [the complete build plan](12-complete-build-plan.md), [pending inputs](15-pending-inputs.md), [task manifest](tasks.json) and [verification oracles](04-verification.md).

## Shared implementation rules

Each numbered slice is a review checkpoint targeting 2–8 hours. Split larger slices before assignment without replacing the parent acceptance criteria. UI/backend subdirectories may be assigned separately after a reviewed contract commit; shared migrations, global contracts, navigation, composition and lockfiles retain a single owner. A task cannot be integrated solely against fakes. Long jobs use backend workers. New paths below are proposed ownership, not existing code.

## X1 — Specify a bounded live-video workload and trial contract

**Owner:** X. **Milestone:** LAB-M5. **Start:** F2P. **Real integration:** own actual-adapter acceptance. **Status:** planned.

**Activation:** Workload owner supplies the live-input task and constraints; absent inputs permit documented discovery only, not fabricated acceptance targets.

**Owned paths:** `research/workloads/video/ (proposed)`.

### Implementation slices

1. X1.a: Record provider-selected task, input cadence/cameras, window/hop, timestamps, retention, response shape and deployment location.
2. X1.b: Define causal benchmark, ground truth, freshness/quality/throughput thresholds, duplicate-event policy and buffer/backpressure failure behavior.
3. X1.c: Resolve serving/preprocessor capabilities, transport and allocated capacity; publish approved trial contract and split implementation slices before X2.

### Acceptance and failure proof

No native streaming claim follows from output SSE or clip inference. Discovery can draft synthetic fixtures immediately after F2P; approval needs a real workload owner and measured requirements.

Contract has missing deadline, clock alignment, overload policy or causal labeling rule: mark trial blocked with exact missing input, keep App/Lab work running.

**Oracles:** VIDEO-CONTRACT. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## X2 — Implement and verify the approved video session adapter

**Owner:** X. **Milestone:** LAB-M5. **Start:** X1. **Real integration:** W3, M3, E6L. **Status:** planned.

**Activation:** Approved X1 contract, recorded implementation slices, verified model capability and an allocated trial environment.

**Owned paths:** `apps/infrx-api/infrx/sessions/video/ (proposed)`; `apps/infrx-api/tests/x/video/ (proposed)`.

### Implementation slices

1. X2.a: Implement the approved session/transport adapter with timestamped bounded buffers, window/hop sampling, overlap dedup and preparation identity.
2. X2.b: Integrate scheduling/backpressure/expiry through existing durable runtime using coordinator-owned hooks; record fresh/stale/dropped windows and loss.
3. X2.c: Replay causal streams with arrival jitter/disconnect/reconnect, memory pressure and overload, then run the allocated model trial before enabling support.

### Acceptance and failure proof

Only the approved task and measured envelope are supported. No future frames enter a causal window; reused preprocessing preserves finite-clip parity. Billable units/rates are explicit before public availability.

Inject clock jumps, out-of-order frames, stuck producer/consumer and session restart; bounded memory, honest loss/freshness and no duplicate accepted events.

**Oracles:** VIDEO-CONTRACT, VIDEO-CAUSAL. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## X3 — Specify a robot/task and inference freshness contract

**Owner:** X. **Milestone:** LAB-M5. **Start:** F2P. **Real integration:** own actual-adapter acceptance. **Status:** planned.

**Activation:** An identified design partner, robot/task and available recordings; missing inputs are explicit pending decisions.

**Owned paths:** `research/workloads/robotics/ (proposed)`.

### Implementation slices

1. X3.a: Record actual policy artifact, robot/task, cameras/proprioception/state/action schemas, normalization, horizon, clocks and ROS2/recording versions.
2. X3.b: Measure end-to-end transport/queue/compute/freshness at intended edge/cloud placement; define local fallback/stop owner and proposed-versus-executed action evidence.
3. X3.c: Define offline/simulator-first acceptance and authorized hardware trial, select adapter/MCAP mapping, then split X4 against that approved contract.

### Acceptance and failure proof

π0.5 is treated as a VLA policy; future world-model annotations are a different integration. No general WAN suitability or support for arbitrary modalities is asserted.

Missing robot schema, latency/freshness target, stale-action rejection or local control owner prevents hardware enablement; architecture discovery remains useful.

**Oracles:** ROBOT-CONTRACT. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## X4 — Implement the bounded policy/ROS2 observation adapter

**Owner:** X. **Milestone:** LAB-M5. **Start:** X3. **Real integration:** W3, E6L. **Status:** planned.

**Activation:** Approved X3 contract, recorded implementation slices, simulator fixtures and explicit hardware-trial owner/allocation.

**Owned paths:** `apps/infrx-api/infrx/sessions/robotics/ (proposed)`; `apps/infrx-api/tests/x/robotics/ (proposed)`.

### Implementation slices

1. X4.a: Implement the pinned observation/action codec, authentication/session sequencing and policy runtime adapter for the approved robot.
2. X4.b: Integrate freshness rejection, deadline/backpressure behavior and synchronized proposed/executed action recording through approved ROS2/MCAP adapters.
3. X4.c: Exercise offline/simulator failure cases before an explicitly allocated robot trial; preserve local actuator authority and emergency controls.

### Acceptance and failure proof

Serving emits schema-valid proposals and explicit stale/failed results; offline evaluation cannot actuate hardware. Compatibility is limited to the verified policy/robot/task contract.

Delayed/reordered observations, unit mismatch, disconnect and late action chunks cause declared rejection/fallback, not stale actuation. Compare recorded execution/outcomes separately from proposals.

**Oracles:** ROBOT-CONTRACT, ROBOT-REPLAY. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## X5 — Qualify a non-NVIDIA backend investment

**Owner:** X. **Milestone:** INFRA-EXPAND. **Start:** F2P. **Real integration:** own actual-adapter acceptance. **Status:** planned.

**Activation:** Chosen hardware/model workload and access to a trial target; research may proceed without purchases.

**Owned paths:** `research/workloads/hardware/ (proposed)`.

### Implementation slices

1. X5.a: Select a real chip/runtime/model/workload and collect supported operators/dtypes/licensing/driver/toolchain constraints from primary sources.
2. X5.b: Define artifact conversion/engine port, measured load profile, quality parity, capacity, cost and failure recovery gates.
3. X5.c: Publish prototype scope, resource allocation and go/no-go criteria; reuse R3 experiment records and avoid promising generic heterogeneous placement.

### Acceptance and failure proof

Discovery produces a measured investment decision and explicit adapter contract; no new kernel or cloud capacity is assumed available.

Model conversion loses required operators, claimed throughput uses incomparable inputs or economics omit idle capacity: trial cannot be certified.

**Oracles:** BACKEND-CONTRACT. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.

## X6 — Implement and validate one qualified inference backend

**Owner:** X. **Milestone:** INFRA-EXPAND. **Start:** X5. **Real integration:** R3, W3. **Status:** planned.

**Activation:** Approved X5 contract, allocated hardware, artifact access and adapter-specific implementation slices reviewed before coding.

**Owned paths:** `apps/infrx-api/infrx/worker/backends/ (coordinator-assigned adapter)`; `apps/infrx-api/tests/x/hardware/ (proposed)`.

### Implementation slices

1. X6.a: Implement the approved engine adapter with capability handshake, artifact identity, raw/visible output and exact usage contract.
2. X6.b: Integrate cancellation, fencing/drain/health and placement constraints; supported capability registry rejects incompatible requests.
3. X6.c: Run held-out quality and sustained/burst/fault benchmarks on actual hardware and register a separately gated serving variant with rollback evidence.

### Acceptance and failure proof

One validated combination may be advertised; other chips/models remain unsupported. Scheduler/runtime never sacrifices durable acceptance or accounting for throughput.

Backend crash, unsupported dtype/tokenizer, usage loss and late output cannot corrupt settlement or claim successful completion. Quality regressions block variant promotion.

**Oracles:** OPT-PARITY, BACKEND-CONTRACT. Hand back code/fixtures, exact commands and environment, current-HEAD review, failure-injection evidence, migration/rollback notes and any remaining external inputs.
