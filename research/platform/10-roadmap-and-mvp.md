# Roadmap and MVP — building the closed loop

Research date **2026-09-19**. This document is the *plan*. It carries no new
research: every decision below is argued and sourced in
[`00`](00-goal-and-problem-statement.md)–[`09`](09-video-and-multimodal-loop.md)
of this directory and in [`../scaling/`](../scaling/), and every number is a
named row from one of them or from
[`../METHODOLOGY.md`](../METHODOLOGY.md), [`../matrix/`](../matrix/) and
[`../models/`](../models/). **Where a figure appears here it is linked, not
re-derived.** Uncertainty inherited from a source document is carried with its
⚠️ marker; uncertainty introduced *by this document* (sequencing, effort,
staffing) is marked **⚠️ TO BE VERIFIED** and its reasoning is stated inline.

**Doc-number note.** [`00` §6](00-goal-and-problem-statement.md) sketched a
different 01–09 split than the one the tree actually grew. The mapping in force
is the filenames: 01 observability · 02 annotation · 03 training · 04 evals and
A/B · 05 optimisation · 06 architecture · 07 competitors · 08 economics · 09
video. Read `00` §6's "doc NN" references through that table.

**Effort convention.** All effort is in **engineer-weeks (ew)**, never dates.
The conversion to a calendar is the reader's, and §3.4 states the assumptions
that make it wrong. Integration-cost rows are taken from
[`06` §6.1](06-platform-architecture.md), which flags its own estimates as
⚠️ unsourced; where this document adds an estimate it says so.

---

## 1. The loop at MVP, v1 and v2

[`00` §1.2](00-goal-and-problem-statement.md) defines the nine stages S1–S9 and
the contract on each edge. This section says, per stage, **what runs without a
human, what a human does, and what is bought**, at three maturity points. The
automation column is the policy ladder of
[`06` §4.2](06-platform-architecture.md) — L0 Observe, L1 Auto-annotate, L2
Auto-train, L3 Auto-eval, L4 Auto-promote — which is a *per-task, customer-chosen
level*, not a platform mode.

### 1.1 MVP — one customer, one text task, L0 + a manual round

[`00` §9.1](00-goal-and-problem-statement.md): "one customer, one text task, one
student, one GPU class". The loop turns once, by hand, and the machinery
underneath it is what gets built.

| Stage | Automated | Manual | Bought / adopted |
|---|---|---|---|
| **S1 Traffic** | Gateway routes `main`/`dev`, resolves versions, fans out shadow, emits traces | Customer changes one base URL ([`06` §7.1](06-platform-architecture.md)) | vLLM/SGLang ([`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)); routing per [`../scaling/02`](../scaling/02-serving-stack-and-routing.md) |
| **S2 Traces** | 100 % of rows captured, content sampled per tier, PII redacted, egress gate evaluated per row ([`01` §6.2](01-observability-and-tracing.md)) | Redaction profile agreed per route | **Langfuse self-hosted** + OTel Collector + Presidio ([`01` §6.3](01-observability-and-tracing.md)) |
| **S3 Annotation** | Teacher/judge calls, verify-before-judge, dedup, queue routing 60/25/15 ([`02` §4.2](02-annotation-and-teacher-labeling.md)) | **H1 judge gold set, H2 frozen references, H3 safety, H4 disagreements** ([`02` §4.1](02-annotation-and-teacher-labeling.md)) | Teacher tokens from a **self-hosted open-weights** model ([`02` §5.5](02-annotation-and-teacher-labeling.md)); annotation widgets, never the router |
| **S4 Datasets + evals** | `from-traces` split by stable entity, test split frozen and un-annotatable ([`02` §7.1](02-annotation-and-teacher-labeling.md)) | Customer signs the **Parity Protocol** ([`04` §7.3](04-evals-and-ab-testing.md)) | **Inspect** for scorers ([`04` "What to buy"](04-evals-and-ab-testing.md)); Iceberg + Langfuse datasets |
| **S5 Training** | One rung of the ladder, chosen by rule ([`03` §1.0](03-training-sft-rlhf-distillation.md)) | Rung choice reviewed; round size set by the previous gate ([`03` §8.1](03-training-sft-rlhf-distillation.md)) | **Tinker or Baseten Training Jobs** for round 1 ([`06` §6.2](06-platform-architecture.md)) |
| **S6 Checkpoint** | Four-axis `ModelVersion` with `content_hash` ([`06` §2.4](06-platform-architecture.md)) | — | **MLflow Model Registry**, extended ([`06` §6.1](06-platform-architecture.md)) |
| **S7 Gate 1** | Paired, clustered, per-slice non-inferiority on the BF16 checkpoint ([`04` §7.1](04-evals-and-ab-testing.md) P3) | Judge validation signed off (H2) | lm-evaluation-harness through the vLLM backend ([`05` "What to buy"](05-model-and-inference-optimization.md)) |
| **S8 Optimisation** | **Stage-0 lookup** against the repo's support matrices, then the sweep ([`05` §7.1](05-model-and-inference-optimization.md)) | — | llm-compressor / ModelOpt; GuideLLM for the sweep profile ([`05` "What to buy"](05-model-and-inference-optimization.md)) |
| **S7′ Gate 2** | Same gates re-run on the **served** artifact ([`04` §7.1](04-evals-and-ab-testing.md) P4) | — | — |
| **S9 A/B** | Shadow at 100 %, disagreement diffing, rule-of-three bounds ([`04` §2.7](04-evals-and-ab-testing.md)) | **Rollback drill in front of the customer** (P6); H4 promotion | OpenFeature for assignment plumbing; **statistics are built** ([`06` §6.1](06-platform-architecture.md)) |

**Automation level: L0 for everything, with one manually-triggered pass through
L1–L3.** [`06` §4.2](06-platform-architecture.md) makes L1 conditional on "≥1
month at L0" and L3 conditional on a validated judge; the MVP satisfies neither
on day one, so the loop is driven by `plat` CLI invocations
([`06` §7.2](06-platform-architecture.md)) with a human at each edge.

**Explicit MVP non-goals**, carried unchanged from
[`00` §9.2](00-goal-and-problem-statement.md): no auto-research loop, no
multi-tenant adapter packing, no video, no agentic/multi-turn parity claim, no
frontier-teacher dependency, no trace-store or eval-UI rewrite, no promised
speedup multiple.

**One narrow exception to "no auto-research", and it is
[`05` §4.6](05-model-and-inference-optimization.md)'s**: tier-1 (config search)
and tier-3 (draft-head refresh) automation are safe *before* the eval is trusted,
because their objectives — latency, cost, acceptance rate — are mechanically
measurable and do not route through a judge. Automate those from day one;
everything that optimises against a judged score waits.

### 1.2 v1 — several customers, one modality, L3 steady state

| Stage | Changes from MVP |
|---|---|
| **S1** | Adapters multiplexed within a **serving pool** — an enforced set of `(base, format, engine version)` triples ([`05` §6.3](05-model-and-inference-optimization.md)). This is the decision that "must be made before the first customer, because retrofitting it means migrating everyone" |
| **S2** | Late-binding `signal` join live; coverage dashboard with **slice power** ([`01` "What to build"](01-observability-and-tracing.md) item 7) |
| **S3** | **L1**: sampling → redaction → teacher/judge on a schedule, per-tenant budget capped, teacher policy fail-closed. Annotation runs as the **lowest-priority tenant of the serving GPU pool** with preemption ([`02` §6.4](02-annotation-and-teacher-labeling.md)) |
| **S4** | Eval-set refresh cadence and contamination controls automated ([`04` §6.5](04-evals-and-ab-testing.md)) |
| **S5** | **L2**: a round fires on a trigger ([`06` §4.1](06-platform-architecture.md)). Behaviour-preservation stage standing in every cycle ([`03` §8.1](03-training-sft-rlhf-distillation.md) Stage 5). The Tinker API becomes the *internal interface* with two backends — hosted, and `skyrl-tx`/VeRL-Tinker on our own B300s ([`03` §3.4](03-training-sft-rlhf-distillation.md)) |
| **S7/S8/S7′** | **L3**: gate 1 → optimise → gate 2 run automatically; candidate lands on `dev` and shadow, **never `main`** |
| **S9** | Sequential guardrail monitor; SRM check; the disagreement explorer is the primary product surface ([`04` §7.4](04-evals-and-ab-testing.md)) |
| **Ops** | The `serving-headroom` ClusterQueue lends the diurnal trough to training and annotation ([`06` "What to build"](06-platform-architecture.md) item 5) |

**L4 stays off**, with one narrow exception that
[`06` §4.2](06-platform-architecture.md) already draws: auto-promotion of a
candidate that is *strictly a re-optimisation of an already-promoted version* —
same adapter, new quantisation or engine config, gate 2 passing at equal quality
and better cost/latency. "Offering L4 for a **new adapter** means a machine
decided the customer's product got better." ⚠️ That doc's own inference is that
the first several customers will not sign it.

### 1.3 v2 — video, multi-tenant packing, bounded auto-research

| Stage | Changes from v1 |
|---|---|
| **S1** | Video request policy: **segment at ≤2 minutes by default**, boundary recorded in the trace, boundary slice in the eval ([`09` "What to build"](09-video-and-multimodal-loop.md) item 4) |
| **S2** | Video trace schema with `effective_fps`, `frame_ts_s`, `media_config_hash` as required fields; **option-B frame storage as the single shared artifact** for traces, training cache, eval corpus and gate corpus ([`09` "What to build"](09-video-and-multimodal-loop.md) items 1–2) |
| **S3** | Frame-budget matching on every teacher record: `fps_teacher = min(native, 240/dur)` ([`02` §8.2](02-annotation-and-teacher-labeling.md)) |
| **S4** | The **eval ladder** of [`09` §6.1](09-video-and-multimodal-loop.md): push every video task down to regime 1 (verifiable, $0/item) or 2 (text-only judge). A video task is MVP-sellable **iff** it evaluates in regime 1 or 2 |
| **S5** | ms-swift for video ([`09` "What to buy"](09-video-and-multimodal-loop.md) item 2); multi-target sharing of one visual encoding, which is a **7× training-cost difference** from a data-loader choice ([`03` §8.2](03-training-sft-rlhf-distillation.md)) |
| **S8** | The **bounded auto-research loop** of [`05` §4.5](05-model-and-inference-optimization.md): drift → re-sweep → screens → gate → **promotion request**. It never promotes |
| **S9** | The **three-arm parity protocol** for video: incumbent-default, incumbent-frame-matched, student ([`09` §6.4](09-video-and-multimodal-loop.md)) |
| **Economics** | Cross-tenant adapter packing offered **as a priced tier**, never as a silent default ([`06` "What to avoid"](06-platform-architecture.md)) |

**What is still not automated at v2**: the four human gates H1–H4 of
[`06` §4.3](06-platform-architecture.md), each a Temporal Signal that **fails
closed** on timeout, with the agent principal structurally unable to write
`EvalSuite`, gold sets, `Approval` or `Deployment` rows. RBAC, not a prompt.

---

## 2. MVP scope

**The claim the MVP has to manufacture:** one customer moves one text task from
GPT-5.6 in production to a distilled **Qwen3.8-27B** on our endpoint, with a
parity report they can hand to their own management. The video variant
(**Marlin-2B**) is the same machine with the deltas of §1.3, sequenced second for
the reasons in [`00` §9.2](00-goal-and-problem-statement.md) and priced in §4.2.

### 2.1 The minimum component set, with the build/buy call

Component IDs are [`06` §1.1](06-platform-architecture.md)'s C1–C10; the call
and the integration cost are [`06` §6.1](06-platform-architecture.md)'s, which
flags its own effort column as ⚠️ unsourced.

| # | Component | Call | Integration | In MVP because |
|---|---|---|---|---|
| C1 | Gateway / inference | **Build** | 6–10 ew | Contract fidelity (I4), shadow fan-out and version resolution are ours; "it is the product's surface" |
| C2 | Trace store | **Adopt — Langfuse self-hosted** | 2–3 ew | [`01` §7.3](01-observability-and-tracing.md): self-hosting is **4–38× cheaper** than the SaaS rows at 50M req/mo, for the same software in Langfuse's case |
| C3 | Annotation | **Build the pipeline, buy the components** | 8–12 ew | The queue router is "the highest-leverage component" in [`02`](02-annotation-and-teacher-labeling.md); PII detection is not ours to write |
| C4 | Dataset / eval store | **Build on OSS formats** | 6–8 ew | OpenAI Evals goes read-only 2026-10-31 and shuts 2026-11-30 ([`00` §6](00-goal-and-problem-statement.md)) |
| C5 | Training orchestration | **Buy first, keep the exit** | 3–4 ew | Both Tinker and Baseten export weights, so the exit is real |
| C6 | Optimisation | **Build** | 8–12 ew | [`../matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md) and [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) **already are** the IP |
| C7 | Model registry | **Adopt MLflow and extend** | 2–4 ew | Its alias/version split is [`06` §2.4](06-platform-architecture.md) rule 1 already implemented; it does not model the four-axis artifact |
| C8 | Experiment / A-B | **Build the statistics, adopt OpenFeature** | 6–10 ew | [`07` §14.1](07-competitor-analysis.md): one ● in the entire S9 column of the survey, and that vendor describes no method |
| C9 | Control plane / API / CLI | **Build** | 10–16 ew | It is the product |
| C10 | Tenancy | **Build on K8s primitives** | 4–6 ew | Minimal at one customer; the *pool* decision is not deferrable (§1.2) |
| — | Orchestration | **Adopt Temporal + Ray-on-Kueue** | 4–6 ew | The 1,000× duration spread between a month-long loop and an hour-long job ([`06` "What to build"](06-platform-architecture.md) item 4) |

Plus **the six gaps** of [`01` §6.1](01-observability-and-tracing.md) that no OSS
stack covers and that together are "perhaps a quarter of one engineer-year":
prompt-stack hash and drift on it; artifact-level lineage to the served quantised
build; late-arriving outcome-signal joins; shadow capture + diff + disagreement
classification; the annotation sampler; and the per-tenant teacher-egress policy
gate with audit. Gap 6 is existential, not convenient.

**The MVP reference stack itself is already written down** —
[`06` §6.2](06-platform-architecture.md), layer by layer, scoped to exactly
[`00` §9.1](00-goal-and-problem-statement.md)'s MVP — and this document adopts it
without amendment.

### 2.2 Where it is hosted

On [`../scaling/10-blueprint.md` §1.2](../scaling/10-blueprint.md)'s **four-node
floor (32 × B300)**, unchanged: five model pools, one gateway, nothing
PD-disaggregated, weights staged on every node's NVMe by a DaemonSet with
`fastsafetensors`, autoscaling on engine queue / token backlog / KV occupancy and
never on GPU utilisation, KEDA installed in observe-only mode. Qwen3.8-27B is the
TP1 × 6-replica pool on nodes 03/04; Marlin-2B is TP1 × 2 on node 04.

Two blueprint facts that decide MVP economics rather than MVP engineering:

- **Qwen3.8-27B is the fleet's profit centre** — it clears its vendor API at
  **6.3 % utilisation** and delivers 157 billion output tokens per node-month at
  60 % ([`../scaling/10` §6.3, §6.5](../scaling/10-blueprint.md)).
- **Utilisation outranks hardware on four of five rows.** Moving
  DeepSeek-V4.1-Flash from 30 % to 85 % `U` is worth **2.83×**; the hardware move
  is worth 2.53×, "and the free one is the utilisation" (ibid.).

The buy/rent/API gate for a new task is
[`../scaling/11-playbook.md` §1.5](../scaling/11-playbook.md), first matching row
wins, and it exists to prevent exactly one mistake: comparing a self-hosted
`$/1M` (a `U`=1 floor) against a vendor list price (`U`-inclusive). **Divide by
your `U` first.**

### 2.3 The confidence protocol, as shipped in the MVP

[`04` §7.1](04-evals-and-ab-testing.md)'s P0–P11 runs in full from the first
customer. Three of its stages are the MVP's reason to exist:

- **P2 cheap-rung trial.** Run the incumbent's own tier-down (Luna/Haiku-class)
  against the customer's existing prompt on the same frozen set. "If it passes,
  **stop and say so**." [`04`](04-evals-and-ab-testing.md) calls P2 "the stage
  that will be cut and must not be"; [`09` §8.6](09-video-and-multimodal-loop.md)
  makes the same move week 1 for video and calls it "the credibility purchase".
- **P6 rollback drill.** Promote and revert on `dev` in front of the customer,
  timed, with request counts — < 60 s, zero dropped requests (I7). `plat rollback
  drill` is a first-class verb ([`06` §7.2](06-platform-architecture.md)).
- **P7 shadow.** 100 % mirrored for ≥ 7 days, disagreements diffed and clickable.
  [`00` §5.6](00-goal-and-problem-statement.md) ranks shadow diffs and the
  rollback drill **above** the statistics in what closes a sale. Build them first.

The pre-registered artifact is the **Parity Protocol** template of
[`04` §7.3](04-evals-and-ab-testing.md), signed at P0. The commercial mechanism
is that it exists *before* any result does.

### 2.4 Training, annotation and optimisation, as scoped for one round

- **Annotation** ([`02` §6.2](02-annotation-and-teacher-labeling.md) Scenario A,
  the recommended default): open-weights teacher, **$17,000–$31,500** per 100k-
  example iteration, of which **96–98 % is human**. The lever is the human
  sampling policy, not the teacher choice. The MVP's H1 line is 1,000 SME
  adjudications at ⚠️ **$12,500–$25,000** — the least-sourced number with the
  largest effect in the whole cost model.
- **Training** ([`03` §8.1](03-training-sft-rlhf-distillation.md)): round 1 is
  **2,000 examples for ~$24** of teacher + GPU. "Round 1 costs less than lunch.
  **This is the point**: the first iteration should be run before any contract
  negotiation about annotation budgets." Round 2 at 60,000 examples is **~$716**.
  A full cycle is **$1k–$4k** variable. Round-based budgeting — 2k → gate → 10k →
  gate → 60k — is the MVP's default.
- **Optimisation** ([`05` §1.6, §7.1](05-model-and-inference-optimization.md)):
  the full config sweep is **$118–$296** of GPU time, 1.0–3.4 % of a loop
  iteration's one-time cost. **Never skip the sweep to save money**; ration QAD
  and draft-head training instead. And most of S8 is **lookup, not search** —
  steps 1, 4 and 5 for Qwen3.8-27B on B300 are answered by the repo's support
  matrices before any GPU is booked.

### 2.5 The ten MVP exit criteria

[`00` §9.1](00-goal-and-problem-statement.md), unchanged and not restated in
full: trace capture ≥ 99.9 % at ≤ 5 ms added p99; 100 % shadow with
disagreements surfaced within 1 h; offline non-inferiority per slice at ≥ 80 %
power; judge–human agreement ≥ 80 % on a ≥ 200-example gold set with judge ≠
teacher; unconditional safety; 100 % contract conformance; p50 **and** p99
latency ≤ incumbent; total delivered $/1M below the customer's **batched, cached,
tier-downed** price; rollback demonstrated < 60 s; and —

> **Criterion 10 is the only one that proves the loop**: a second iteration
> trained from post-deployment traffic beats the first on the frozen test set.
> "If the MVP cannot hit 10, the platform is a consultancy."

---

## 3. Sequenced plan

Six phases, **M0–M6**. Each names its deliverable, its exit criterion (a measurement, not an
opinion) and the benchmark or eval that gates it. Serving-side benchmarks are
`B1`–`B10` from [`../matrix/recommendations.md` §3](../matrix/recommendations.md)
and `R1`–`R6` from
[`../scaling/02` §8.8](../scaling/02-serving-stack-and-routing.md); the
loop-side gates are [`04` §7.1](04-evals-and-ab-testing.md)'s P0–P11.

**The ordering is not this document's invention.**
[`00` §6](00-goal-and-problem-statement.md) says traces → evals → A/B is the
minimum sellable product, because it delivers
[`00` §5.6](00-goal-and-problem-statement.md) items 1–3 and 6 with no training at
all. [`07` §18](07-competitor-analysis.md) sharpens it: since traces and evals
are cheap-to-buy commodities and S9 is empty whitespace, **the differentiated
minimum sellable product is "ingest someone else's traces → validated eval +
validated judge → online A/B with a defensible claim", with no training in v1.
Training is what we sell second.** [`09` §8.5](09-video-and-multimodal-loop.md)
independently reaches the same ordering for video and states it harder: the eval
set is **84 % of the one-off cost**, so "the product is the eval".

### 3.1 The phases

| Phase | Deliverable | Exit criterion (measured) | Gating benchmark / eval |
|---|---|---|---|
| **M0 — Serving floor** | One node, one model, no platform. `vllm serve` for Qwen3.8-27B TP1 and DeepSeek-V4.1-Flash TP4; node conformance as a pre-join check | `cuobjdump --list-elf \| grep sm_103` passes on every wheel; resolved attention backend and KV bytes/token match [`../METHODOLOGY.md` §8](../METHODOLOGY.md); `fio` ≥ 5 GB/s on the NVMe RAID0 | **B3** (Qwen — parent of every other Qwen item, no measurement exists on any datacentre GPU), **B2** (KV replicated vs sharded, a **4–8×** difference), **B4** for Marlin ([`../scaling/10` Phase 0](../scaling/10-blueprint.md)) |
| **M1 — Capture** | Gateway in `observe` mode in front of the customer's *incumbent*; OTel Collector with normalisation, content externalisation, redaction, egress gate; Langfuse self-hosted | ≥ 99.9 % of requests captured at ≤ 5 ms added p99 ([`00` §9.1](00-goal-and-problem-statement.md) criterion 1); every row leaving the tenant boundary carries `redaction_status` + policy version | Shadow-capture correctness: the trace store can reconstruct a byte-exact training example ([`01` "What to avoid"](01-observability-and-tracing.md): never truncate) |
| **M2 — Eval + judge** | Frozen test split by stable entity; 200–500-item gold set; judge validated; **P0 Parity Protocol signed**; **P2 cheap-rung trial run** | Human–human then judge–human agreement measured per slice; κ ≥ the agreed floor; judge noise floor published; judge ≠ teacher | [`04` P1](04-evals-and-ab-testing.md) assay sensitivity; **P2 may end the engagement at a win for the customer, which is a pass** |
| **M3 — Proof without training** | Shadow at 100 % against the incumbent; disagreement explorer; **P6 rollback drill**; rule-of-three coverage report | Disagreements surfaced < 1 h; rollback < 60 s with zero dropped requests; 0 hard failures in shadow with a stated upper bound | [`04` P6, P7](04-evals-and-ab-testing.md). **This is the first sellable artifact.** [`01` §7.4](01-observability-and-tracing.md): running it costs ~0.7 % of the incumbent's token spend |
| **M4 — First model** | Round 1 (2k examples) → gate → round 2 sized by the gate; S8 lookup + sweep; **gate twice** | Gate 1 on the BF16 checkpoint and **gate 2 on the served NVFP4/FP8 artifact**, both per-slice, safety unconditional; `artifact_hash_evaluated` recorded on every score | [`04` P3, P4](04-evals-and-ab-testing.md) + [`05` §2.10](05-model-and-inference-optimization.md) tier-A screens (contract, fixed probe, per-token KL vs the retained BF16 reference) |
| **M5 — Online** | Canary → A/B at 10 %, sticky by session, SRM-monitored; promotion; 30-day watch | Pre-registered primary bound met at the pre-registered horizon; no slice regressed beyond δ_slice; guardrails clean; SRM χ² p ≥ 0.001 | [`04` P8–P11](04-evals-and-ab-testing.md). At [`04` §8.1](04-evals-and-ab-testing.md)'s volume the binding constraint is **the calendar, not the statistics** — n is reached in 1.3 days at 10 %, and the remaining 5.7 buy weekly-seasonality coverage |
| **M6 — Second turn** | A round trained from **post-deployment** traffic | It beats round 1 on the frozen test set | [`00` §9.1](00-goal-and-problem-statement.md) criterion 10 — "the whole thesis, in one number" |

Serving-side phases run in parallel on the blueprint's own schedule
([`../scaling/10` §8](../scaling/10-blueprint.md) Phases 1–4: cold start and
weight distribution; the static 4-node cluster gated on **B1** DSpark acceptance
on *our own* traffic and **B5** batch size; routing and KV tiering gated on
**R2**/**R4**/**R6**; autoscaling gated on two weeks of the forecaster in
advisory mode with σ < 33 % at p90). Phases 5–6 (16 nodes, the PD decision,
SLA-native scaling) are out of scope until the loop has customers.

### 3.2 Effort

⚠️ **TO BE VERIFIED — this whole table.** The per-component rows are
[`06` §6.1](06-platform-architecture.md)'s ⚠️ unsourced estimates; the phase
allocation and the "six gaps ≈ 13 ew" line are **mine**, derived by splitting
those rows across phases. Treat the ratios as more defensible than the totals.

| Phase | Components touched | ew |
|---|---|---:|
| M0 | — (serving, already the repo's baseline) | 2 |
| M1 | C2 (2–3) + C1 partial (4) + gaps 1, 3, 6 (~6) | 12–13 |
| M2 | C4 (6–8) + C8 statistics partial (4) + gap 5 (~2) | 12–14 |
| M3 | C1 shadow fan-out (3) + gap 4 (~4) + C8 assignment (3) | 10 |
| M4 | C3 (8–12) + C5 buy (3–4) + C6 (8–12) + C7 (2–4) + gap 2 (~1) | 22–33 |
| M5 | C8 remainder (4) + C9 (10–16) + C10 (4–6) | 18–26 |
| M6 | Orchestration (4–6) | 4–6 |
| | **Total to a closed second turn** | **80–104 ew** |

**The assumptions that make this wrong, stated so they can be checked:**

1. **One customer, one text task, one student, one GPU class.** Every number
   above is [`00` §9.1](00-goal-and-problem-statement.md)'s MVP scope. Video
   (§1.3), a second base family, or a second GPU class each re-open C6 and C10.
2. **The serving stack is not being built here.** M0 assumes
   [`../scaling/10` §1.2](../scaling/10-blueprint.md)'s four-node floor exists or
   is being stood up by the infrastructure track in parallel. If the loop team
   also owns the cluster, add [`../scaling/10` §8](../scaling/10-blueprint.md)'s
   Phases 0–4 in full.
3. **Adopt means adopt.** Langfuse, MLflow, Temporal, Kueue, Presidio,
   OpenFeature and Inspect are configured, not forked. Every fork moves a 2–4 ew
   row into the 8–12 ew column.
4. **Human adjudication capacity is contracted, not hired.**
   [`02` "What to avoid"](02-annotation-and-teacher-labeling.md): do not build a
   labelling org.
5. **No effort is budgeted for the legal work** that
   [`00` §8.1](00-goal-and-problem-statement.md) and
   [`06` §4.3](06-platform-architecture.md) H3 require. It is a prerequisite, not
   a phase.

### 3.3 Team and skills

⚠️ **TO BE VERIFIED — my inference from the component table**, not a sourced
staffing model.

| Skill | Owns | Why it cannot be merged into another row |
|---|---|---|
| **Serving / GPU systems** | C1, C6, M0, the blueprint interface | [`05`](05-model-and-inference-optimization.md) is a per-GPU, per-format, per-kernel discipline; its failure mode (a format-mismatched draft head at ~0 % acceptance) is invisible to everyone else |
| **Data / pipelines** | C2, C3, C4, the six gaps | Append-only, idempotent, versioned transforms over a label store ([`02` §7.1, §7.3](02-annotation-and-teacher-labeling.md)) |
| **Statistics / evals** | C8's statistics, the Parity Protocol, judge validation | [`04` "What to build"](04-evals-and-ab-testing.md) item 2: a scorer that cannot produce a **clustered** CI should refuse to emit a number; naive standard errors understate by **3×** and are "the most likely way this platform ships a false parity claim" |
| **Training / distillation** | C5, the ladder-as-rules-engine | [`03` §1.0](03-training-sft-rlhf-distillation.md)'s rungs, and the chat-template/loss-mask CI of [`03` §4.4](03-training-sft-rlhf-distillation.md) |
| **Platform / control plane** | C9, C10, Temporal + Kueue | [`06` §3](06-platform-architecture.md) |
| **Forward-deployed engineer** | The customer-facing half of P0–P11 | [`07` §17](07-competitor-analysis.md): "Do not plan the pure self-serve SaaS configuration… Price the forward-deployed engineer, as Adaptive ML does" |
| **Counsel (fractional)** | H3, teacher policy, [`01` OQ5](01-observability-and-tracing.md) Phoenix licence, [`02` OQ7–OQ9](02-annotation-and-teacher-labeling.md) | Three of the tree's open questions are legal, not technical |

The statistics and the forward-deployed roles are the two that competitors do not
staff and that [`07` §14.1](07-competitor-analysis.md) says the wedge depends on.

---

## 4. The two lighthouse runs

Both are end-to-end walk-throughs assembled from the pair documents. **No figure
below is new.** Where two source documents price the same thing on different
assumptions, both are shown and neither is silently picked.

### 4.1 Lighthouse A — text support agent

**Profile** ([`08` §7.1](08-economics-and-business-case.md)): 2M requests/month,
4,000 input tokens, 400 output, incumbent **GPT-5.6 Sol**. (Note
[`04` §8.1](04-evals-and-ab-testing.md) runs the same volume at 512 output
against Claude Opus 5 for its *statistical* sizing; the shapes differ and the two
sets of numbers are not interchangeable.)

**The incumbent bill, at four price points** —
[`08` §7.1](08-economics-and-business-case.md), `est.`:

| GPT-5.6 Sol | h=50 %, standard | h=90 %, standard | h=50 %, batch | **h=90 %, batch** |
|---|---:|---:|---:|---:|
| $/month | **$33,600** | $22,080 | $16,800 | **$11,040** |

Tier-downs on the same shape: Haiku 4.5 batched+cached **$2,760**, Luna batched+
cached **$632**.

**The run:**

| Stage | What happens | Cost / outcome |
|---|---|---|
| **P2 tier-down test** | Luna and Haiku 4.5 against the customer's own prompt on a 500-example paired sample ([`03` §8.1](03-training-sft-rlhf-distillation.md) Stage 0) | **$0**. If it passes, the engagement ends at a win for the customer |
| **P1 judge + gold set** | 500 items × 2 SMEs × 3 min | **50 SME-hours**; judge calibration run **$65** ([`04` §8.1](04-evals-and-ab-testing.md)) |
| **P3/P4 offline gate** | Paired, 10 % discordance, judge-inflated ×2.78 → **n = 1,910**; run twice (checkpoint + served artifact) | **$496 list / $248 batch** (ibid.) |
| **P7 shadow, 30 days** | 100 % of 2M requests on the candidate at Qwen3.8-27B's blended **$0.0602/1M** ([`../matrix/cost-matrix.md`](../matrix/cost-matrix.md) via [`00` §4.2](00-goal-and-problem-statement.md)) | **$543/month marginal — 1.1 % of the incumbent bill** |
| **P9 A/B** | Randomise on **session**; resolution-without-escalation, baseline 0.70, δ = 3 pp → **n = 2,886 sessions/arm**; run at **10 % for a full week** | Online judging ~**$260**; n reached in 1.3 days, the week buys seasonality coverage |
| **S5 training** | Round 1 (2k) → gate → round 2 (60k), LoRA rank 128 | **~$24** then **~$716** ([`03` §8.1](03-training-sft-rlhf-distillation.md)) |
| **S8 optimisation** | NVFP4-mixed on B300 — but see the GPU note below | **$118–$296** sweep ([`05` §1.6](05-model-and-inference-optimization.md)) |

**Total cost of manufacturing the confidence: ≈ $1,500 list / ≈ $1,020 batch**,
ex-SME ([`04` §8.1](04-evals-and-ab-testing.md)). Against a $47,600/month
incumbent bill on that document's shape, "the entire evidence package costs under
one day of the customer's inference spend."

**The GPU choice, which is the whole economics.**
[`08` §7.1](08-economics-and-business-case.md): at 2M requests/month a B300
replica runs at **9.3 % utilisation** and its *delivered* cost is **$0.65/1M**,
not $0.0602/1M — worse than Gemini 3.8 Flash batched. **The RTX PRO 6000 at 53 %
is the right class and is also the cheapest cell.** Chosen configuration: 2
production + 1 dev replica = **$3,942/month**, steady-state total **≈ $4,092**
with self-hosted Langfuse (**≈ $4,563** on Langfuse Cloud).

**Expected outcome:**

| Dimension | Expected | Source |
|---|---|---|
| **Cost** | Payback **1.4 mo** vs Sol at list ($40k one-off); **6.2 mo** vs Sol batched+cached — the honest floor; **never** vs Haiku or Luna batched+cached | [`08` §7.1](08-economics-and-business-case.md) |
| **Latency** | p50 **and** p99 TTFT/TPOT ≤ incumbent at the customer's concurrency, measured at S8 and confirmed online (I3) | [`00` §9.1](00-goal-and-problem-statement.md) criterion 7; [`05` §7.3](05-model-and-inference-optimization.md)'s report shape |
| **Quality** | Non-inferiority at δ = 3 pp, 95 % one-sided, 80 % power, per slice, with the judge noise floor drawn next to it; **⚠️ no published result predicts the delta for this task** — [`03` §8.1](03-training-sft-rlhf-distillation.md)'s trajectory table is explicitly "analogies, not predictions" | [`04` §8.1](04-evals-and-ab-testing.md), [`03` §8.1](03-training-sft-rlhf-distillation.md) |

**The sentence that must be in the proposal.**
[`08` §7.1](08-economics-and-business-case.md): "This engagement is sellable **if
and only if** the customer's real price is list-ish. If they already batch and
cache, the payback is 6–12 months and the case rests on latency and quality, not
cost. If they can tier down to Haiku or Luna and pass their eval, the correct
advice is to do that and buy only the eval harness."

### 4.2 Lighthouse B — video captioning

**Profile** ([`09` §8.1](09-video-and-multimodal-loop.md)): 50,000 videos/month,
3-minute mean, dense captioning, incumbent Gemini 3.1 Pro (A) or Gemini 3.8 Flash
(B), student **Marlin-2B** segmented into 2 × ≤2-minute windows at 2 fps / 240
frames, served on RTX PRO 6000 SE, eval regime 2 (AutoDQ text-only judge against
a frozen human reference).

**The incumbent bill** ([`09` §8.2](09-video-and-multimodal-loop.md), `est.`):
A **$5,681/mo** ($68,175/yr); B promo **$2,102/mo** ($25,218/yr), post-promo
$4,203/mo; B agentic mode ⚠️ lower bound **$379/mo**; C low-res $819/mo.

**The student bill** ([`09` §8.3](09-video-and-multimodal-loop.md)): marginal
**$58.50/month** — but 4.5 % utilisation, so 2 cards 24/7 for `main` + `dev` =
**$2,628/month**. "**$2,628/month of GPU serves $58.50/month of actual inference —
45× overhead.**"

**The loop bill** ([`09` §8.4](09-video-and-multimodal-loop.md)): one-off
**$30,226**, ongoing **$230/month**. Of the one-off, the **eval set is $25,500 —
84 %**. Teacher annotation is $1,449, training $672.

**Year totals** ([`09` §8.5](09-video-and-multimodal-loop.md)): our year 1
**$64,522**, year 2 onward **$34,296**.

| Against | Year-1 Δ | Year-2 Δ |
|---|---:|---:|
| **A — Gemini 3.1 Pro** | **−$3,653 (−5 %)** | **−$33,879 (−50 %)** |
| **B — Gemini 3.8 Flash** (promo) | **+$39,304 (+156 %)** | −$16,140 (−32 %) |
| **B — agentic mode** ⚠️ | +$59,972 | +$25,196 |
| **C — Flash low-res** | +$54,694 | +$14,640 |

[`08` §7.3](08-economics-and-business-case.md) reaches the same verdict from a
different shape (50,000 clips, cost bucketed by clip length): the deployment wins
outright only at **30-minute** average clips, and at 5 minutes on Gemini Flash
"**the honest answer is that the customer should stay on Gemini**".

**Expected outcome:**

| Dimension | Expected | Source |
|---|---|---|
| **Cost** | Wins against a Pro-class or per-media-minute incumbent; **loses in year 1 against Flash-class**. Qualification is volume **≥ ~150 K videos/month** *or* an incumbent priced like Rekognition rather than like Flash. At 500 K/month it saves ~**$217,000/year (86 %)** | [`09` §8.5](09-video-and-multimodal-loop.md) |
| **Latency** | Prefill-bound and flat in clip length — **~23,560 tokens for any clip** ([`../models/marlin2b/README.md`](../models/marlin2b/README.md) §9). Speculative decoding is "impossible and pointless" here; the serving win is `pynvvideocodec` + CUDA MPS | [`05` §7.2](05-model-and-inference-optimization.md), [`09` "What to build"](09-video-and-multimodal-loop.md) item 7 |
| **Quality** | The strongest published support in the tree: **TimeLens-8B at 55.2 / 53.2 / 65.5 mIoU** on Charades / ActivityNet / QVHighlights against **GPT-5 at 40.5 / 42.9 / 56.8** and **Gemini-2.5-Flash at 48.6 / 52.5 / 64.3** — an 8 B open model beating two frontier models on the task class the repo's student is built for. **⚠️ But Marlin-2B is 2.21 B, ~3× smaller than the 7 B analogue, and no published result shows a ~2 B VLM at parity with a frontier model on a customer video task** | [`09` §0](09-video-and-multimodal-loop.md) finding 2, [`03` §8.2](03-training-sft-rlhf-distillation.md) |

**Three rules this run exists to prove**, all from
[`09`](09-video-and-multimodal-loop.md): segment at ≤2 minutes or the parity
claim is a sampling artefact (§0 finding 4); run the **three-arm** protocol —
incumbent-default, incumbent-frame-matched, student (§6.4); and **do not
annotate before the eval exists** (§8.6). The week-by-week order is
[`09` §8.6](09-video-and-multimodal-loop.md)'s table, adopted unchanged.

**The counter-move to state up front** ([`09` §0](09-video-and-multimodal-loop.md)
finding 5): Gemini's agentic video mode is "up to 88 % more token-efficient and
~7 % higher quality on long-form content". At 88 % fewer content tokens a
Flash-class incumbent on long video gets cheaper than the **GPU floor** of a
small self-hosted student at these volumes.

---

## 5. Platform economics at 10 and 100 customer tasks

All rows from [`06` §8.3–8.5](06-platform-architecture.md), which assumes 1M
requests/month per task, one loop round per task per month, and — for the packed
configurations — a shared base model. Planning GPU price `b300 · low` **$7.40/GPU-
hour** [[repo](../cross-cutting/cloud-pricing.md)], 730 h/month.

### 5.1 Ten tasks

| Configuration | Serving GPUs | Serving $/mo | Loop $/mo | Infra | **Total** |
|---|---:|---:|---:|---:|---:|
| **A — Unpacked** (1 main + 1 dev per task) | 20 | $108,040 | $66,290 | ~$2,000 | **~$176k** |
| **B — Packed** (adapters on 2 shared bases, HA, + dev + eval) | 8 | $43,216 | $66,290 | ~$2,000 | **~$112k** |
| **C — Packed + headroom scavenging** | 8 | $43,216 | ~$55,550 | ~$2,000 | **~$101k** |

The capacity check is the finding: 10M requests/month is **under one GPU of work
at 100 % utilisation** (decode 1,946 tok/s against 12,463 tok/s/GPU; prefill
15,200 against ~44,671). "Eight GPUs is entirely a redundancy-plus-peak-headroom
decision, not a throughput one — which is
[`00` §4.5](00-goal-and-problem-statement.md)'s GPU floor in its purest form."

### 5.2 One hundred tasks

| Configuration | Serving GPUs | Serving $/mo | Loop $/mo | Infra | **Total** |
|---|---:|---:|---:|---:|---:|
| **Unpacked** | 200 | $1,080,400 | $662,900 | ~$8,000 | **~$1.75M** |
| **Packed** (4 base families × ~25 adapters) | ~40 | $216,080 | $662,900 | ~$8,000 | **~$887k** |
| **Packed + scavenging + open-weights teacher** | ~40 | $216,080 | ~$430,000 | ~$8,000 | **~$654k** |

**The packing lever is worth ~$864k/month at 100 tasks (5× on serving)** — and it
is also the lever that collides with tenant isolation. "That collision is the
platform's central architectural trade-off and it should be **priced, not
engineered around**": a shared tier at the packed cost, a dedicated tier at the
unpacked cost, and the difference stated plainly.

**What the customers were paying, and the correction that must never be
omitted.** 100M requests at
[`00` §4.1](00-goal-and-problem-statement.md)'s per-request figures: Claude Opus
5 **$2.38M/month**, GPT-6 Astra **$4.76M/month** — a **3.6×** and **7.3×** saving
against the packed $654k. But against **Haiku 4.5 batched** on the same shape,
`est.` **$238k/month**, "**we are 2.7× more expensive**".

> The product conclusion, [`06` §8.4](06-platform-architecture.md) verbatim: "at
> 100 tasks the platform's economics work against *frontier-tier* incumbents and
> are marginal against *cheap-tier* incumbents… A customer on Astra or Opus 5 is a
> customer. A customer already on Luna or Haiku, batched, is a customer for the
> eval harness and the observability — and that is still a product, just a
> different one."

### 5.3 What moves these numbers

[`06` §8.5](06-platform-architecture.md)'s sensitivity table, ordered by effect:
peak-to-average ratio ⚠️ (3× → 6× doubles serving); adapter packing (+$864k/mo if
it fails); frontier vs open-weights teacher (+$220k/mo); human adjudication cost
⚠️ (+$100k/mo at 2× ). Storage does not appear. **Two of the three biggest swings
are unsourced assumptions and the third is an engineering decision we control.**

### 5.4 How the platform charges

[`08` §6.2](08-economics-and-business-case.md), because margin cannot come from
tokens (the incumbent's cheapest tier is at or below our marginal cost), from
training (commoditised, 12× vendor spread), or from traces (per-unit trace
pricing is what makes Weave unusable at volume), while cost of goods is *fixed*
idle GPU:

> **A platform subscription that prices the confidence protocol, plus inference
> at cost-plus, plus a one-time engagement fee for iteration 1.**

Engagement fee **$40k–$150k**; subscription **$3k–$15k per task per month** by
volume band; inference cost-plus at a **published** 1.3–1.6× multiple; shadow and
A/B metered separately at cost; teacher tokens passed through, itemised by
teacher (which the H3 audit trail requires anyway). **Not outcome-based** — the
baseline is the customer's honest price, which they can lower unilaterally, and a
regression costs them **18–46×** the saving, so an incentive tied only to savings
is an incentive to under-invest in the gates. A **warranty** — a discount if the
promoted model fails its eval within 90 days — is the defensible version.

**And the qualification filter is the actual product decision.**
[`08` §6.3](08-economics-and-business-case.md): "the sellable engagements are the
ones with a five-figure monthly saving… The pricing model does not need to be
clever; the qualification does."

---

## 6. Positioning and whitespace

### 6.1 What we say we are

**The closed loop with a defensible statistical claim at the end of it.**
[`07` §14.1](07-competitor-analysis.md): one ● in the entire S9 column of a
41-product survey, from a vendor whose page says "Guarantee performance with A/B
testing" without describing a design, a power calculation or a stopping rule. It
stays empty because "it is the least glamorous and most statistically demanding
part of the loop, it requires being in the production request path (which eval
vendors are not), and getting it wrong is a customer incident rather than a bad
chart."

Five more gaps, each with evidence of absence in
[`07` §14](07-competitor-analysis.md):

| # | Whitespace | Why it is ours |
|---|---|---|
| 14.2 | **Gate-twice** — evaluating the artifact that actually serves | Every training vendor hands back a checkpoint; every eval vendor scores an endpoint without knowing its serving config. This repo already has the per-GPU format analysis |
| 14.3 | **The prompt stack as a versioned artifact** | No vendor surveyed treats the customer's system prompt as part of the model artifact. A prompt hash in every trace and a hard refusal to claim parity across a prompt change "costs almost nothing to build" |
| 14.4 | **Judge validation as a priced, auditable stage** | "**Nobody publishes judge-vs-human agreement as a product artifact.**" ≥200 human-adjudicated examples per task and a κ floor is a sellable deliverable |
| 14.5 | **Regulated / on-prem full loop** | No single vendor offers the whole loop inside the customer's boundary — and it is the segment likeliest to accept an open-weights teacher |
| 14.6 | **Video understanding** | "Unclaimed, unproven, and possibly unbuildable. Treat as a research bet, not a product line" |

### 6.2 What we do not do

[`07` §14.7](07-competitor-analysis.md), stated so nobody spends a quarter on it:
"**trace capture, gateways, eval harnesses, LLM-judge libraries, LoRA training
APIs, multi-adapter serving and prompt optimisation are all solved, cheap, and
often free.** Langfuse self-hosts for $0, LiteLLM adds 0.66 ms, TRL ships nine
distillation trainers, LoRAX is Apache-2.0, DSPy is MIT. Building any of these is
a negative-value activity."

Four more explicit non-positions, from
[`07` "Avoid"](07-competitor-analysis.md): **no per-seat pricing** (the market
moved to volume, and seats discourage exactly the trace volume the loop needs);
**no roadmap built on an Anthropic teacher**; **no dependency on any vendor's
eval product**; **no pure self-serve SaaS configuration** — every comparable
standalone tooling company in the survey was acquired, sunset or pivoted.

### 6.3 The sentence for the first slide

We are not selling cheaper tokens. [`08` §0](08-economics-and-business-case.md)
result 3: a 0.5 % regression rate on a 2M-request/month support chat at $12 per
escalated ticket costs **$120,000/month** `est.` — **18×** the $6,477/month saved
by distilling off a batched, cached GPT-5.6 Sol. **Customers are buying a
defensible reason to believe nothing broke**, and
[`08` §7.1](08-economics-and-business-case.md) prices manufacturing that belief
at **under $6,000** for a one-month shadow: a 5 % premium on one month of risk.

---

## 7. Top risks

Ordered by how much they can end the product. Every mitigation is a component or
a gate that already exists somewhere in §2–§3.

| # | Risk | Evidence | Mitigation | Owner |
|---|---|---|---|---|
| 1 | **Teacher ToS.** The mechanism we sell is restricted by the vendors whose models the customer already uses | Anthropic's AUP forbids "model scraping or model distillation" without prior authorisation; Google's terms likewise; OpenAI's §3.3(e) is now quoted verbatim in [`02` §5.1](02-annotation-and-teacher-labeling.md) (2026-09-12 Wayback snapshot; the live pages still 403, re-tested 2026-09-19) and is **narrower than feared but not permissive** — the Permitted Exception excludes a distributed generative student, and "model extraction" is a separate clause with no exception. **⚠️ The executed document is still not in counsel's hands**; the four off-web routes to it, each with an owner, are in [`02` §5.1](02-annotation-and-teacher-labeling.md). ([`06` §4.3](06-platform-architecture.md) H3 flagged the old header/body mismatch in [`00` §8.1](00-goal-and-problem-statement.md); §8.1 now carries the quote) | **Open-weights-teacher-first by construction** — the MVP has no legal dependency ([`00` §9.2](00-goal-and-problem-statement.md)). Per-tenant teacher policy, **fail-closed**; `teacher_policy_id` on every job and row; `derived_teacher_policy_ids[]` on every ModelVersion so "which checkpoints are exposed if this authorisation is revoked?" is one query. And the cost table agrees with the lawyers: self-hosted open-weights teacher **$1,074 vs $3,280** batched frontier ([`06` §8.1](06-platform-architecture.md)) | 02, 06, counsel |
| 2 | **Judge validity.** The gate rests on a judge, and **no published measurement exists for any 2026-generation judge** | [`02` OQ1](02-annotation-and-teacher-labeling.md): every agreement number in the tree is 2023–2025, and the gap is *confirmed unresolved*, not confirmed absent | Judge ≠ teacher, enforced structurally (self-preference is *caused* by self-recognition). κ **and per-class recall** on the dashboard — the κ paradox means an agreement-only view shows 95 % while the judge catches 1 failure in 5. The **judge canary**: 100 frozen adjudicated items re-run every eval, alert at 3 pp. The **noise floor** as a shaded band on every chart, never a footnote. Measure agreement **per customer** or do not claim it | 04, 02 |
| 3 | **Model collapse / self-training.** Synthetic data trained on synthetic data degrades | [`00` §8.3](00-goal-and-problem-statement.md); [`03` §4.5](03-training-sft-rlhf-distillation.md) | **Append-never-replace datasets** — union of all cycles, deduplicated, never evicted: "theoretically grounded and nearly free" ([`03` "What to build"](03-training-sft-rlhf-distillation.md) item 6). Never train on the student's own outputs as targets. Teacher-written references never enter the frozen test split | 03 |
| 4 | **Regressions after promotion**, including the rare tail an aggregate A/B will never find (1-in-10,000 needs ~30,000 samples to see three) | [`00` §5.4](00-goal-and-problem-statement.md) | **Shadow-first by default** — full coverage at zero user risk, ~1.1 % of the incumbent bill ([`04` §8.1](04-evals-and-ab-testing.md)). Rule-of-three bounds reported. Pre-agreed rollback triggers written into P0 ([`04` §7.2](04-evals-and-ab-testing.md)) so firing one is an engineering event, not a negotiation. I7: < 60 s, drilled in front of the customer. Every incident becomes a permanent regression test | 04, 01 |
| 5 | **Ops burden.** Two systems, five pools, a loop that runs for months | [`../scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md); [`06` §0](06-platform-architecture.md) sentence 1 | **The two-system split with one-way coupling**: every loop component is crash-only from serving's perspective — the gateway drops traces with a counter rather than blocking, assignment is cached last-known-good, the registry is read at replica start and never per request. Only C1, C7 and C8 are in the production blast radius. `max_failures ≥ 1` on Ray Train (the default of 0 loses a multi-day run) | 06 |
| 6 | **Incumbent price cuts and counter-moves** | Cached input as low as **2.5 %** on Fable 5.1; Batch **−50 %** on both vendors; tier-down one config change away; Gemini 3.8 Flash **doubles on 2027-01-01**, which flips one worked example from "never" to 4.9 months ([`08` §7.2](08-economics-and-business-case.md)); Gemini agentic video at **−88 % tokens** ([`09` §0](09-video-and-multimodal-loop.md)) | **Quote only against the batched, cached, tier-downed price** — the correction every credible buyer makes in the first meeting, "and making it first is worth more than the number it costs". The **cost model as a live artifact** holding each incumbent's published rates *with effective dates*. The qualification calculator **must be willing to output "do not do this"** | 08 |
| 7 | **GPU floor / utilisation.** The per-token story breaks on a dedicated replica billed by the hour | A B300 at 2M req/mo runs at **9.3 %** util and delivers $0.65/1M, not $0.0602 ([`08` §7.1](08-economics-and-business-case.md)); video at **0.9 %** util is unsellable single-tenant ([`08` §5.4](08-economics-and-business-case.md)) | Right-sizing before packing (RTX PRO 6000 at 53 % is 4.1× cheaper fixed cost than a B300 at 9 %). Then packing (§5.2). Then the `serving-headroom` queue. `res1y` can invert the hardware ranking ([`05` §7.1](05-model-and-inference-optimization.md)) | 08, 05, `../scaling/07` |
| 8 | **Eval contamination and eval overfitting** | [`00` §8.4](00-goal-and-problem-statement.md); [`03` §7.2](03-training-sft-rlhf-distillation.md) | `split_assignment` **refuses** to emit test-split rows — a hard failure at the source node, not a warning ([`02` §7.1](02-annotation-and-teacher-labeling.md)). The eval suite is owned by 04, frozen, and **read-only to S8**: "an optimizer with write access to its own objective" is the named failure mode. Never seed synthetic-prompt generation from the whole trace store | 04, 02, 05 |
| 9 | **Vendor sunsets.** Seven counted across the tree | OpenAI fine-tuning closed to new users, Evals shuts 2026-11-30; Azure stored completions retire 2026-10-15; NeMo microservices sunset 2026-10-01; HELM in maintenance; torchtune unmaintained; Eppo → Datadog; Predibase gone | **The eval-definition schema is ours and exportable** and "must outlive every tool underneath it". The Tinker API with **two** backends. Copy Tinker's export verbs as our own exit guarantee. Langfuse under MIT, self-hosted | 04, 03, 06 |
| 10 | **Tenant isolation.** The one failure that ends the business | [`06` §1.1](06-platform-architecture.md) C10 | Cross-tenant packing is **a priced tier, never a silent default**. Do not sell shared-node vCluster as isolation — the vendor's own docs say it "isn't a security boundary for untrusted tenants". Never expose vLLM's runtime LoRA-loading endpoints on a tenant-reachable surface | 06 |

---

## 8. What to research or prototype next

Ranked by how much each changes a decision already made above. Each names the ⚠️
items it resolves.

| # | Do this | Resolves | Why it ranks here |
|---|---|---|---|
| 1 | **Measure judge–human agreement on one real customer task with a 2026-generation judge**, ≥200 adjudicated items, per slice | [`02` OQ1](02-annotation-and-teacher-labeling.md), [`00` OQ](00-goal-and-problem-statement.md) judge-validity thread, [`04` §3.1](04-evals-and-ab-testing.md)'s ×2.78 inflation constant | Every gate, every sample size and every promotion decision multiplies through this number, and the tree's best evidence is a 2023 chat-preference measurement |
| 2 | **Get an actual RFQ for SME adjudication**, per example, at enterprise quality | [`00` OQ10](00-goal-and-problem-statement.md), [`02` OQ2](02-annotation-and-teacher-labeling.md), [`06` §8.1](06-platform-architecture.md)'s ⚠️ $400–$1,000 row | It is **77–98 % of the annotation budget** and **the single biggest unsourced number in the platform's economics**. No vendor publishes a price |
| 3 | **Run `B3` and `B2`** on one B300 node (Qwen3.8-27B TP1; KV replicated vs sharded) | [`../scaling/10` Phase 0](../scaling/10-blueprint.md); the 4–8× capacity uncertainty | B3 is "the parent of every other Qwen item; no measurement exists on any datacentre GPU". Every $/1M in §4 and §5 is downstream of it |
| 4 | **Round 1 on a real customer task — 2,000 examples, ~$24** | [`03` §8.1](03-training-sft-rlhf-distillation.md); [`00` §9.1](00-goal-and-problem-statement.md) criterion 10's feasibility | It produces a real checkpoint and a real gate result for the price of lunch, and it calibrates the eval harness before any training risk is taken |
| 5 | **Test whether LIMA-scale curation transfers** (~1,000 curated examples at 2–30 B) | [`02` OQ12](02-annotation-and-teacher-labeling.md) | "Cheap to test in the first iteration and would materially change the cost model" — it would re-allocate the entire 100k-example budget toward hard-slice acquisition |
| 6 | **Measure `cold_start_p95` per model on the real node** | [`../scaling/10` Phase 1](../scaling/10-blueprint.md); the 2–4× band disagreement between `05` and `06` of the scaling tree | No end-to-end cold-start measurement exists for any of the five models, and I7's < 60 s rollback promise sits on top of it |
| 7 | **Prototype the shadow-diff + disagreement explorer** against stored incumbent outputs | [`01` §6.1](01-observability-and-tracing.md) gap 4; [`04` §8.2](04-evals-and-ab-testing.md)'s free-video-shadow requirement | [`00` §5.6](00-goal-and-problem-statement.md) ranks it above the statistics in what closes a sale, and nobody ships it. It also forces the "store **full** incumbent outputs" requirement into `01` before traces accumulate in the wrong shape |
| 8 | **Resolve Gemini logprob availability for caller-supplied continuations** | [`00` OQ6](00-goal-and-problem-statement.md), [`02` OQ3](02-annotation-and-teacher-labeling.md) | If it exists, Gemini is the only frontier teacher that can drive on-policy distillation (rung 5) — the largest published jump per unit compute. If not, the ladder tops out at rung 4 for frontier teachers, permanently |
| 9 | **Measure Qwen3.8-27B's video token count for a 240-frame clip** through vLLM | [`02` OQ4](02-annotation-and-teacher-labeling.md) | Blocks any video annotation budget using the clean open-weight teacher; its ViT runs full bidirectional attention with no windowing, so it must be measured, not estimated |
| 10 | **Answer the frame-sampled-eval validity question** — is grading on the model's own 240-frame budget a valid proxy for full-clip human grading? | [`00` OQ8](00-goal-and-problem-statement.md), [`02` OQ6](02-annotation-and-teacher-labeling.md), [`03` §8.2](03-training-sft-rlhf-distillation.md)'s training half | The three-arm protocol ([`09` §6.4](09-video-and-multimodal-loop.md)) is the proposed answer and costs one extra Gemini `fps` parameter. Until it is run, every video parity claim carries a methodological asterisk |
| 11 | **Source an always-valid / sequential inference method properly** before any stopping rule ships | [`00` OQ9](00-goal-and-problem-statement.md) | A fixed-horizon test monitored continuously and stopped on a win is not a 95 % test; 50 looks turns a 5 % test into a 32 % test |
| 12 | **Measure the real compression ratio for LLM trace content** on a day of real traffic | [`01` OQ6](01-observability-and-tracing.md) | Moves the storage line 3× — and storage is the one cost people instinctively worry about and that [`06` §8.5](06-platform-architecture.md) says to ignore. Worth one day to stop the conversation |
| 13 | **Re-run the market scan with web search available** | [`00` OQ1](00-goal-and-problem-statement.md), [`01` OQ1](01-observability-and-tracing.md), [`02` OQ15](02-annotation-and-teacher-labeling.md), [`06` OQ1](06-platform-architecture.md), [`07` OQ1](07-competitor-analysis.md) | Five documents carry the same gap. [`07`](07-competitor-analysis.md) closed it partially at 41 products; anything launched since May 2026 is missing, not disproven |
| 14 | **Prototype the bounded auto-research loop on config search only** (tier 1 + tier 3) | [`05` §4.5–4.6](05-model-and-inference-optimization.md) | The one automation that is safe before the eval is trusted, because latency, cost and acceptance rate do not route through a judge. **$1.5k–$4k/customer/year** of GPU time |

Items 1–4 are the ones that would change §3's phase order if they came back
badly. Items 5–14 change numbers, not sequence.

---

## 9. Open questions

⚠️ New to this document. The tree's existing open questions are not restated —
each source document owns its own, and §8 names the ones that gate a decision
here.

1. **⚠️ Is M3 (proof without training) sellable on its own?**
   [`07` §18](07-competitor-analysis.md) argues the differentiated MSP is "traces
   → validated eval → online A/B, with no training at all in v1", and
   [`08` §6.3](08-economics-and-business-case.md) prices at least one worked
   profile where the correct advice is "buy only the eval harness". Nobody has
   priced *that* product. If it sells, §3's phases M4–M6 are a second product
   line rather than the completion of the first. **Owner: product, with `08`.**
2. **⚠️ The effort table in §3.2 is mine, built on `06` §6.1's own ⚠️ estimates.**
   80–104 ew to a closed second turn is a number nobody has checked against a
   delivered system. The most likely error is C9 (control plane, 10–16 ew), which
   is the row with no OSS floor under it. **Owner: engineering lead.**
3. **⚠️ Does the MVP's "one customer, dedicated replica" posture survive contact
   with the first customer's *second* task?** [`05` §6.3](05-model-and-inference-optimization.md)
   says the serving-pool decision "must be made before the first customer, because
   retrofitting it means migrating everyone", while
   [`00` §9.2](00-goal-and-problem-statement.md) explicitly defers packing. Those
   are compatible only if the *pool abstraction* ships in the MVP with a
   population of one. This document assumes it does; that assumption is untested.
   **Owner: `06`/`05` jointly.**
4. **⚠️ What is the right trigger to move a task from L3 to the narrow L4?**
   [`06` §4.2](06-platform-architecture.md) defines the defensible narrow case
   (re-optimisation of an already-promoted version) but not the evidence a
   customer would want before signing it — number of clean gate-2 passes? months
   without a rollback? **Owner: product, with `04`.**
5. **⚠️ Is the video lighthouse worth running at all before a ≥150 K-videos/month
   customer exists?** [`09` §8.5](09-video-and-multimodal-loop.md)'s qualification
   test excludes the literal profile the platform owner named, and
   [`08` §7.3](08-economics-and-business-case.md) agrees. The case for running it
   anyway is that the eval set is the durable asset and the methodology (three-arm,
   segmentation, frame-budget matching) has to be built once regardless. The case
   against is that it doubles the unknowns, exactly as
   [`00` §9.2](00-goal-and-problem-statement.md) warns. **Owner: product.**
6. **⚠️ Who owns the qualification calculator's "no"?** [`08` "What to build"](08-economics-and-business-case.md)
   item 2 requires it to be "willing to output *do not do this*", and
   [`06` "What to build"](06-platform-architecture.md) item 6 makes it a circuit
   breaker in the product. A tool that tells a paying customer to stop paying
   needs an owner who is not compensated on the opposite. **Owner: commercial.**
7. **⚠️ What does the platform commit to contractually on deletion?**
   [`00` OQ12](00-goal-and-problem-statement.md) is unresolved and there is no
   cheap un-training mechanism. It is a prerequisite for the regulated/on-prem
   segment that [`07` §14.5](07-competitor-analysis.md) identifies as whitespace.
   **Owner: `06`, with counsel.**
8. **⚠️ Does the first customer's traffic actually contain enough of the
   high-margin task shapes?** [`03` §2.1](03-training-sft-rlhf-distillation.md)
   says the qualification question is "how much of your traffic is routing,
   extraction or classification?", because that fraction "is where the loop pays
   back in weeks rather than quarters" — while
   [`08` §5](08-economics-and-business-case.md) ranks extraction first and agents
   hard, coding hardest. A support agent (lighthouse A) is a *chat* task, which
   is the weakest verifier class on the ladder. Whether lighthouse A is the right
   first task, or whether an extraction task should displace it, is unresolved.
   **Owner: product, with `08` §5.**

---

## Sources

This document cites no external source. Every figure, quotation and decision is
carried from, and linked to, a document in this repository:

- [`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) —
  §1.2 the nine stages, §1.3 invariants I1–I7, §4 economics, §5.2–5.6 parity and
  evidence ordering, §9.1–9.2 MVP criteria and non-goals
- [`01-observability-and-tracing.md`](01-observability-and-tracing.md) —
  §6.1 the six gaps, §6.2 reference pipeline, §6.3 buy/build, §7 the 50M-request
  worked example
- [`02-annotation-and-teacher-labeling.md`](02-annotation-and-teacher-labeling.md) —
  §4.1 H1–H4, §4.2 the 60/25/15 queue split, §6.2 the three budget scenarios,
  §6.4 annotation on idle GPUs, §7.1 the DAG, §8.2 frame-budget matching
- [`03-training-sft-rlhf-distillation.md`](03-training-sft-rlhf-distillation.md) —
  §1.0 the ladder, §2 the recipe decision table, §3.4 the two-backend Tinker
  conclusion, §8.1–8.2 the worked examples
- [`04-evals-and-ab-testing.md`](04-evals-and-ab-testing.md) —
  §2 the statistics of parity, §3 judge validity, §7.1–7.4 the confidence
  protocol and the Parity Protocol, §8.1–8.2 the worked examples
- [`05-model-and-inference-optimization.md`](05-model-and-inference-optimization.md) —
  §1.4 the ordering invariant, §1.6 what S8 costs, §4.5–4.6 the bounded
  auto-research loop, §6.3 serving pools, §7 the worked examples
- [`06-platform-architecture.md`](06-platform-architecture.md) —
  §1.1 C1–C10, §4.1–4.3 triggers, the policy ladder and the four human gates,
  §6.1–6.3 build/buy and the MVP reference stack, §7 the seven verbs and the CLI,
  §8.1–8.5 platform capacity and cost
- [`07-competitor-analysis.md`](07-competitor-analysis.md) —
  §12 the coverage matrix, §14 the whitespace, §15 pricing benchmarks, §17
  threats, "Implications"
- [`08-economics-and-business-case.md`](08-economics-and-business-case.md) —
  §0 the five results, §1 the unit-economics model, §3 quality-risk pricing,
  §6 pricing models, §7.1–7.3 the three profiles
- [`09-video-and-multimodal-loop.md`](09-video-and-multimodal-loop.md) —
  §0 the five findings, §3 video capture, §6.1 the eval ladder, §6.4 the
  three-arm protocol, §8 the worked example and the week-by-week plan
- [`../scaling/10-blueprint.md`](../scaling/10-blueprint.md) —
  §1.2 the four-node floor, §6 the cost projection, §8 the six rollout phases and
  their exit criteria
- [`../scaling/11-playbook.md`](../scaling/11-playbook.md) —
  §1.4 scale-to-zero, §1.5 buy vs rent vs API, §1.7 workload × model class
- [`../METHODOLOGY.md`](../METHODOLOGY.md), [`../matrix/`](../matrix/),
  [`../models/`](../models/), [`../cross-cutting/`](../cross-cutting/) — every
  per-model and per-GPU figure

---

*Update 2026-09-19 — §7 row 1: the OpenAI teacher-ToS clause is no longer unread.
[`02` §5.1](02-annotation-and-teacher-labeling.md) now quotes the Services
Agreement §3.3(e) verbatim from a 2026-09-12 Wayback snapshot and lists four
off-web routes to the executed document, with owners. All five `openai.com` policy
URLs were re-tested with a browser user-agent on 2026-09-19 and still return HTTP
403 — do not spend another attempt on them.*
