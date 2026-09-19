# Post-training model optimization and hardware-targeted inference optimization

Research date **2026-09-19**. This document owns stage **S8** of the closed loop defined in
[`00-goal-and-problem-statement.md`](./00-goal-and-problem-statement.md) §1.2: the stage that
takes a checkpoint which has already passed the offline eval gate and turns it into *the
cheapest artifact that still meets the customer's SLO on the customer's target GPU* — and then
proves, on the artifact that will actually serve, that nothing was lost.

> **Document numbering.** `00` §6 assigns this component the label "doc 06". The programme
> filed it as `05`. Same component, same scope, same cross-references; the label in `00` §6
> row **06** is this file. Nothing else in the decomposition moves.

**Conventions.** Legend, cost formulas and the `low`/`high`/`res1y` price tiers are
[`research/METHODOLOGY.md`](../METHODOLOGY.md). This document **does not re-derive any number
that already exists in the tree.** Format definitions, per-GPU silicon capability, measured
quantization accuracy and KV-cache quantization are
[`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md); prefix
caching, speculative decoding, parallelism, chunked prefill and CUDA graphs are
[`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md); attention
kernels are [`cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md); engine ×
GPU × model support is
[`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md); per-GPU kernel
and format acceleration is [`matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md); the
$/1M grids are [`matrix/cost-matrix.md`](../matrix/cost-matrix.md); serving, cold start,
utilisation and fleet economics are [`scaling/`](../scaling/). Where this document needs one of
those numbers it **names the row and links it**.

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source, fetched 2026-09-19 unless the source states its own date. |
| **⚠️ TO BE VERIFIED** | No primary source found, or the claim is an inference; the reasoning is stated inline. |
| `est.` | Arithmetic from METHODOLOGY formulas or from sourced inputs; shown, not measured. |
| `meas.` | A published measurement, cited. |
| `vendor` | A vendor-published claim, not independently replicated. |

> **Research-method caveat, stated up front.** This session hit the same wall as `00`: the
> **WebSearch budget was exhausted (200/200) before this agent started**, so every external
> source below was reached by **WebFetch/curl against a known URL** or by following a link from
> a fetched page. 48 primary sources were fetched. The consequence is the same as in `00`:
> **the tool survey in §2.2, §3.3 and §4.1 is a survey of what I could name and fetch, not a
> market scan.** A tool that exists but that I could not name is missing, not disproven. Two
> specific casualties are recorded honestly: NVIDIA Dynamo's SLA-planner *profiling* pages
> (§3.3) and Sakana AI's original AI-CUDA-Engineer claims and their correction (§4.2) could not
> be retrieved, and neither is paraphrased from memory.

---

## 1. The optimization stage in the loop

### 1.1 Where S8 sits, and the trap in the diagram

`00` §1.2 places S8 **between** the offline eval gate (S7) and the online A/B (S9), and calls
that placement a trap:

> *"the eval gate must run against the artifact that will actually serve … NVFP4 or FP8
> weights, a different attention kernel, or speculative decoding all change outputs … The
> correct discipline is **gate twice** — once on the BF16 checkpoint (does training work?) and
> once on the deployed artifact (does the optimisation preserve it?)."*
> — [`00` §1.2](./00-goal-and-problem-statement.md)

This document's single most important structural claim is that **gate-twice is not enough for
this platform, because S8 is not one transformation — it is five or six, and each one can move
the output distribution independently.** Quantize the weights, quantize the KV cache, swap the
attention kernel, attach a speculative-decoding draft head, merge a LoRA, change the chunked-
prefill budget: every one of those is a separate edit to the function being served. Gating only
at the end tells you *that* something moved, never *what*.

The discipline this document proposes is **gate-at-each-rung**, with a cheap screen after every
model-level step and one expensive full gate on the final artifact:

```
  S7 PASS (BF16 checkpoint, full eval)
        │
        ├── rung 1  format/quantization      → cheap screen (§2.10 tier A)
        ├── rung 2  KV-cache precision       → cheap screen
        ├── rung 3  draft head / spec decode → cheap screen + acceptance measurement
        ├── rung 4  LoRA merge (if any)      → cheap screen
        ├── rung 5  engine + kernel + flags  → cheap screen  (kernels change numerics)
        │
        └── S8 EXIT: full eval gate on the built artifact, at the served config
                     + benchmark report + cost statement
                          │
                          v
                        S9 (shadow → canary → % → main)
```

The cheap screen is not a second opinion; it is a **bisection tool**. When the exit gate fails,
the screens tell you which rung did it, and you have not spent a full judged eval to find out.
§2.10 prices both tiers.

### 1.2 Inputs — the optimization request

S8 is invoked with a request object. Everything in it is a contract term; anything missing is a
place where the platform will later be unable to defend a claim.

| Input | Shape | Who owns it | Why S8 cannot proceed without it |
|---|---|---|---|
| **Checkpoint** | Immutable ref (registry URI + digest), weights + tokenizer + chat template + generation config | S6 (doc 01) | The chat template and generation config are part of the function. A student gated with one template and served with another is a different model. |
| **Prompt stack** | The system prompt(s), tool schemas, response schema, in a versioned blob with a hash | Customer, frozen into the artifact | `00` §2.2: the prompt stack is part of the versioned artifact, not a customer-side variable. It also determines the prefill:decode ratio, which determines which GPU wins. |
| **Target hardware** | GPU model + count + interconnect + $/GPU-hr tier (`low`/`high`/`res1y`) | Platform + customer's residency/commitment | Format availability is a *silicon* property ([`quantization-formats.md` §4.2](../cross-cutting/quantization-formats.md)), so the target GPU prunes most of the search space before any search starts. |
| **SLOs** | p50 **and** p99 TTFT and TPOT, at a stated concurrency; plus the availability target | Customer (I3 in `00` §1.3) | A p50-only SLO is not an SLO; §3.4 shows the search has a different optimum under p99. |
| **Traffic profile** | Distribution of input length, output length, prefix-cache hit rate, arrival process, tool-call rate, image/video frame counts | Platform, mined from S2 traces | The repo's own evidence that this is load-bearing: measured prefix-cache hit rates range from **≈0 %** for video to **99.7 %** on agentic traces ([`cost-matrix.md` §7.2](../matrix/cost-matrix.md)). Benchmarking at the wrong hit rate is the most common way to produce a confidently wrong config. |
| **Eval suite** | Frozen test split, per-slice, with judge config and the agreed margin δ, α, power | doc 04; thresholds by the customer | S8's exit gate is a *re-run* of S7's gate, not a new one. If the suite is not frozen and cheap to re-run, gate-at-each-rung is unaffordable. |
| **Safety suite** | Separate, non-negotiable (I5) | doc 08 | Quantization is a plausible mechanism for refusal regression: refusal behaviour is a small fraction of tokens (`00` §3.3) and low-precision weights degrade rare behaviours first. ⚠️ **TO BE VERIFIED** — I found no published measurement of refusal-rate drift under PTQ; §"Open questions" Q4. |
| **Budget** | GPU-hours for the sweep, $ for the gate, wall-clock deadline | Platform | §1.6 prices the stage; without a cap, config search is unbounded. |
| **Policy** | Tenant isolation class, teacher-provenance flags, export requirements | doc 08 | Determines whether the artifact may be co-resident with other tenants' adapters (§6.3). |

### 1.3 Outputs — the serving artifact and its paperwork

| Output | Content | Consumed by |
|---|---|---|
| **Weight artifact(s)** | One or more format builds (e.g. BF16 reference, FP8, NVFP4), each a content-addressed blob set, each with its own quantization config and calibration-set hash | The engine; the registry (§5) |
| **Serving config** | Engine name + exact version + container digest; parallelism (TP/PP/DP/EP); every flag that changes numerics or scheduling; KV dtype; attention backend; chunked-prefill budget; CUDA-graph capture sizes; `max_num_seqs`; `max_model_len` | The endpoint control plane (doc 01) |
| **Speculative modules** | Draft head weights (MTP / EAGLE3 / DFlash / DSpark), γ, and the **measured acceptance on this customer's traffic** | The engine |
| **Adapter set** | LoRA adapters + rank + target modules, if serving multi-LoRA (§6) | The engine |
| **Benchmark report** | §3.7 schema: the Pareto curve, the chosen operating point, p50/p99 TTFT/TPOT at that point, tokens/s/GPU, $/1M in / out / blended at all three price tiers, and the full reproduction command | The customer; the cost model (I2); doc 07 |
| **Eval-gate report** | Per-slice deltas vs the BF16 checkpoint **and** vs the incumbent, with the judge's own noise floor shown (`00` §5.1), plus the safety result | doc 07's promotion decision |
| **Provenance record** | Lineage edges, signature, the exact calibration data used, the tool versions (§5.3–5.5) | doc 08's audit trail |

**An artifact without its benchmark report and eval-gate report is not an artifact.** Doc 01's
promotion API should refuse it.

### 1.4 The ordering invariant

The steps are not independent, and running them in the wrong order wastes the expensive ones.

```
target GPU ──┐
             ├──► format menu   (silicon)          quantization-formats §4.2
engine + ver ┘        │
                      ├──► weight format decision  §2.2–2.4
                      │        │
                      │        ├──► KV dtype       §2.7  (interacts: FA4 vs FP8-KV are
                      │        │                          mutually exclusive on Blackwell)
                      │        ├──► attention backend  flash-attention.md
                      │        └──► draft head       §2.8  (must be trained AGAINST the
                      │                                     final weight format)
                      └──► config sweep  §3   (batch, chunk, graphs, TP/DP)
                                 │
                                 └──► operating point on the Pareto curve  §3.4
```

Three ordering rules fall out, and all three are violated by the naive pipeline:

1. **Train the draft head after the target is quantized, not before.** The draft's job is to
   predict the *target's* next tokens; quantizing the target changes them. The repo already
   records the inverse case as a measured disaster: on RTX PRO 6000 the DeepSeek NVFP4 build's
   DSpark acceptance is **~0 % without a patch to the MXFP4 draft-expert routing**, and
   **58–94 %** with it ([`gpu-optimizations.md` §7](../matrix/gpu-optimizations.md)). A
   draft/target format mismatch is not a small regression; it silently turns speculation off.
   ⚠️ **TO BE VERIFIED** that training a draft head against a quantized target beats training
   against BF16 and then serving quantized — I found no published ablation; Q5.
2. **Choose the KV dtype before the attention backend, not after.** On B300, vLLM's FA4
   hdim-256 path requires **no quantized KV**, and every published recipe pins
   `--kv-cache-dtype fp8`, so *choosing FP8 KV silently chooses FA2*
   ([`models/qwen3827b/b300.md` §2](../models/qwen3827b/b300.md)). This is a fork in the
   search space, not a knob, and an automated sweep that treats the two as independent axes
   will report a config that the engine quietly rewrites.
3. **Sweep the config last.** Every model-level change invalidates the sweep: different weight
   bytes per decode step, different KV bytes per token, different state slots per request. The
   sweep is the cheapest step (§1.6) and the one most sensitive to everything above it.

### 1.5 What S8 is not

- **Not training.** QAD (§2.4), pruning-with-distillation (§2.5) and draft-head training (§2.8)
  all *use* a trainer, but their objective is "recover what the compression cost", not "improve
  the task". They belong to S8 because their gate is "did the compression preserve S7's
  result", and because they are hardware-targeted — you do not do NVFP4-QAD unless you are
  deploying on silicon with NVFP4 pipes.
- **Not eval design.** doc 04 owns the suite, the judge and the slices. S8 *consumes* a frozen
  suite and must not be allowed to touch it — an optimizer with write access to its own
  objective is the failure mode `00` names in "What to avoid".
- **Not rollout.** doc 07 owns shadow/canary/split. S8 hands over an artifact and two reports.

### 1.6 What the stage itself costs

This matters because it is the cost the platform eats per customer per iteration, and because
it is small enough that the usual instinct — "we cannot afford to search" — is wrong.

| Step | GPU-hours (`est.`) | $ at B300 `low` $7.40/GPU-hr | Notes |
|---|---:|---:|---|
| PTQ calibration (one format) | 0.2–2 | $1–$15 | llm-compressor / ModelOpt one-shot on a few hundred sequences. For a 27B on one GPU this is minutes. |
| PTQ, 3 candidate formats | 0.6–6 | $4–$44 | Search the format menu, do not guess. |
| AutoQuantize mixed-precision search | **1.07** (4 GPUs × ~16 min) | **$7.90** `est.` at B300 `low` | NVIDIA reports **"~16 minutes"** gradient scoring for Qwen3.6-35B-A3B on **4× RTX 6000 Ada** (not B300), vs **"~14 hours"** for a KL-divergence baseline — a **~52.5×** speedup [src](https://nvidia.github.io/Model-Optimizer/announcements/autoquantize.html) (2026-08-24, `vendor`). *Corrected 2026-09-19: this row previously read "~1 GPU-hour … ~$30 on 4 B300". 4 GPUs × 16 min = **1.07 GPU-hours** = $7.90 at $7.40/GPU-hr; $30 is the price of 4 GPU-hours, i.e. the whole node held for a wall-clock hour. Quote the GPU-hour figure, and note the published timing is on a much smaller/cheaper card, so a B300 run should be faster, not 4× dearer.* |
| Config sweep, 48 configs × 20 min | 16 | **$118** | §3.6. At `high` $15.00/GPU-hr: $240. |
| Config sweep, 120 configs × 20 min | 40 | **$296** | The generous version. |
| Cheap screens, 5 rungs | ~1–3 | $7–$22 | §2.10 tier A. Mostly CPU-bound judging, GPU-bound generation. |
| Exit gate (full eval on the artifact) | varies | **judge tokens dominate** | doc 04 owns the number; `00` §5.3 sizes n. |
| **QAD, if needed** | **~400 iters on 2 nodes × 8 GPUs** | **hundreds to low thousands of $** | NVIDIA's Nemotron 3.5 Lightning run: *"approximately 6,400 training samples at 524K sequence length, yielding roughly 400 iterations"*, TP=2 EP=4 across two 8-GPU nodes [src](https://developer.nvidia.com/blog/developing-nemotron-3-5-lightning-nvfp4-with-qad-using-nvidia-model-optimizer/) (2026-08-17, `vendor`). |
| **Draft-head training** | ⚠️ unpriced | ⚠️ | SpecForge documents the pipeline but the page fetched gives no hardware/data scale [src](https://github.com/sgl-project/SpecForge). Q6. |

**The decision rule this table implies:** a full config sweep costs about **$120–$300 of GPU
time**, which is **1.0–3.4 %** of the *one-time* cost of a loop iteration as `00` §4.3 prices it
(annotation ≈ $3.3k–$6.6k, training ≈ $5.5k — so $8.8k–$12.1k per iteration; $120/$12.1k = 1.0 %,
$300/$8.8k = 3.4 %). *Corrected 2026-09-19: this sentence read "under 2 %", which is false at the
top of the sweep range against the cheaper iteration. The conclusion is unchanged.*
**Never skip the sweep to save money.** The thing
to ration is QAD and draft-head training, not benchmarking.

---

## 2. Model-level optimization

### 2.1 The ladder

Climb only as far as the eval gate and the SLO demand. Each rung costs more and risks more than
the one above it.

| # | Rung | Typical gain | Typical risk | When to stop here |
|---|---|---|---|---|
| 0 | **Do nothing** — serve BF16 | — | none | The model already fits and meets the SLO, and the GPU has no low-precision pipe worth using. This is Marlin-2B on every GPU in the repo (§7.2). |
| 1 | **PTQ weights** to the target's native format | 2–4× memory, up to the silicon's format ratio on GEMM | small, measurable accuracy shift | The screen passes and the SLO is met. **This is where most customers stop.** |
| 2 | **KV-cache quantization** | more concurrency, not more speed | attention-backend interactions (§1.4) | KV capacity, not compute, is the binding constraint. |
| 3 | **Speculative decoding** with an in-checkpoint or trained draft | the single largest decode lever in this repo (see [`cost-matrix.md` §7.3](../matrix/cost-matrix.md)) | acceptance is workload-dependent and can be ~0 | Decode-bound, low-to-moderate batch. |
| 4 | **Mixed-precision search** (AutoQuantize) | recovers accuracy at nearly the same bits | search cost, deployment-constraint modelling | PTQ at a uniform format misses the gate by a little. |
| 5 | **QAD / QAT** | recovers most of what aggressive quantization costs | a training run, and its own overfitting risk | PTQ misses the gate by a lot and the volume justifies it. |
| 6 | **Pruning / layer dropping + distillation** | smaller model, fewer GPUs | a real retraining project | The student is still too big for the target, and no smaller base exists. |

### 2.2 Quantization pipelines — what to actually run

| Tool | What it is | Formats (as documented) | Consumes / emits | Status 2026-09-19 |
|---|---|---|---|---|
| **llm-compressor** | *"the fast, efficient, and easy-to-use library for optimizing models for deployment with vLLM"* [src](https://github.com/vllm-project/llm-compressor) | Docs list **W4A16/W8A16, W8A8-INT8, W8A8-FP8, MXFP8, NVFP4/MXFP4, NVFP4A16/MXFP4A16/MXFP8A16, W4AFP8, W4AINT8**; algorithms **RTN, GPTQ, AWQ, SmoothQuant, SpinQuant, QuIP, FP8 KV cache, AutoRound** [src](https://docs.vllm.ai/projects/llm-compressor/en/latest/) | HF → `compressed-tensors` | **v0.13.0** documented [ibid.]. The repo README also names **REAP expert pruning** and *"Attention and KV Cache Quantization: FP8, NVFP4"* [src](https://github.com/vllm-project/llm-compressor). Kimi-K3 appears by name in the README's **model highlights / example checkpoints**, not in the architecture-specific list (which covers MoE LLMs, VLMs and audio-LMs) — *corrected 2026-09-19* [ibid.]. **2:4 sparsity is gone, not merely unlisted:** the docs state *"Sparse compression (including 2of4 sparsity) is no longer supported by LLM Compressor due to lack of hardware support and user interest"* [src](https://docs.vllm.ai/projects/llm-compressor/en/latest/) — see §2.5 |
| **NVIDIA Model Optimizer (ModelOpt)** | ⚠️ *"a library comprising state-of-the-art model optimization techniques including quantization, pruning, Neural Architecture Search (NAS), distillation, speculative decoding and sparsity"* [src](https://github.com/NVIDIA/TensorRT-Model-Optimizer) — **this exact wording could not be relocated on the page on re-fetch 2026-09-19**; the description served now is *"A unified library of SOTA model optimization techniques like quantization, distillation, pruning, neural architecture search, speculative decoding, etc."*, which does **not** name sparsity. Treat the sparsity claim for ModelOpt as unconfirmed | PTQ; **QAT / quantization-aware distillation**; pruning; distillation; **speculative-decoding draft-module training**; sparsity [ibid.] | HF / PyTorch / ONNX → checkpoints deployable in **SGLang, TensorRT-LLM, TensorRT, vLLM** [ibid.] | **Rebranded from "TensorRT Model Optimizer" to "NVIDIA Model Optimizer" 2025-12-08** [ibid.]. Most recent listed item **2026-09-16**: an end-to-end **W4A4 NVFP4 + QAD** tutorial for Qwen3.6-35B-A3B claiming *"up to 1.30x vLLM throughput over BF16 and 3.1x smaller checkpoints"* [ibid., `vendor`] |
| **GPTQModel** | *"an extensible platform for LLM quantization, validation, and deployment"* [src](https://github.com/ModelCloud/GPTQModel) | GPTQ, AWQ, ParoQuant, QQQ, GGUF, FP8, EXL3, GPTAQ, EoRA, GAR, FOEM [ibid.] | → HF Transformers, **vLLM, SGLang** [ibid.] | **v7.5.0, 2026-09-15** [ibid.]. The live successor to AutoGPTQ/AutoAWQ. |
| **AMD Quark** | *"a comprehensive cross-platform deep learning toolkit designed to simplify and enhance the quantization of deep learning models"* [src](https://quark.docs.amd.com/latest/) | PyTorch path: *"float16, bfloat16, int4, uint4, int8, fp8 (e4m3fn and e5m2), Shared Micro exponents with Multi-Level Scaling (MX6, MX9), and Microscaling (MX) data types"* [ibid.] | Export to *"ONNX, JSON-safetensors, and GGUF"* [ibid.] | **v0.12.post1** documented [ibid.]. ⚠️ the page fetched **does not name MI300X/MI355X, vLLM, SGLang or compressed-tensors** — the AMD serving path is therefore not confirmed end-to-end from this source. Q1. |
| **AutoAWQ** | — | — | — | **Archived by the owner 2025-05-11; "officially deprecated and will no longer be maintained"**, pointing users to llm-compressor and MLX-LM [src](https://github.com/casper-hansen/AutoAWQ). **Do not start a pipeline here.** |
| **compressed-tensors** | The on-disk format: *"extends the safetensors format, providing a versatile and efficient way to store and manage compressed tensor data"* [src](https://github.com/neuralmagic/compressed-tensors) | INT8, FP8, NVFP4, MXFP4, MXFP8; activation quant; KV-cache quant; **unstructured and semi-structured sparsity** [ibid.] | `config.json → quantization_config` with `config_groups`, `ignore`, `quant_method: "compressed-tensors"`, `quantization_status` [ibid.] | Read by HF, PyTorch, **vLLM, SGLang** [ibid.] |

**Decision rule, by target GPU** (format availability from
[`quantization-formats.md` §4.2 and §9.6](../cross-cutting/quantization-formats.md); do not
re-derive it here):

- **Blackwell NVIDIA (B200 / B300 / GB300 / RTX PRO 6000):** NVFP4 via **ModelOpt** or
  **llm-compressor**, FP8 as the fallback for the layers NVFP4 hurts. ModelOpt is the better
  default when you also want QAD or a draft head, because it is one library for all three
  ([src](https://github.com/NVIDIA/TensorRT-Model-Optimizer)); llm-compressor is the better
  default when the serving target is vLLM and you want `compressed-tensors` end to end.
- **Hopper (H100 / H200):** FP8 W8A8 via llm-compressor; INT4 W4A16 (GPTQ/AWQ) only for memory,
  **never for speed** — it dequantizes into BF16 math.
- **AMD MI355X:** MXFP4/MXFP6 via **Quark**, with the caveat above (Q1). Note the repo's
  standing finding that **NVFP4 checkpoints do not run natively on AMD**
  ([`quantization-formats.md` §3.2](../cross-cutting/quantization-formats.md)), so an
  AMD target forks the artifact set — it is a second build, not a flag.
- **A100:** the repo already marks this GPU unsupported for every model in the tree
  ([`inference-engines.md` §3.2](../cross-cutting/inference-engines.md)). Do not plan S8 for it.

**Two recent PTQ refinements worth adopting, both cheap:**

1. **AutoQuantize** — per-layer mixed-precision assignment as an **integer linear program**
   minimising a gradient-based sensitivity score (second-order Taylor with a diagonal Fisher
   approximation) subject to an `effective_bits` budget, with coupled operators (Q, K and V
   projections must share a format) grouped into one format decision so the result is deployable
   — the page's own wording is *"Any restriction of the form 'this group of operators takes one
   joint format decision'"*; the phrase previously quoted here, *"coupled operators (e.g. QKV
   projections)"*, ⚠️ could not be relocated verbatim on re-fetch 2026-09-19
   [src](https://nvidia.github.io/Model-Optimizer/announcements/autoquantize.html) (2026-08-24).
   Inputs: model, calibration loader, bit budget (the example uses `effective_bits: 4.8`), a
   format menu, forward/loss functions, and step counts [ibid.]. The reported finding is the
   useful one for platform design: *"at every plotted budget, searching over NVFP4, FP8, and
   BF16 matches or beats NVFP4 and BF16 alone"* [ibid., `vendor`] — i.e. **put FP8 on the menu;
   a two-format menu leaves accuracy on the table.**
2. **Local-Hessian weight scales** — choose each per-block NVFP4 scale to *"minimize the
   output error of the matrix multiplication rather than the weight error"*. On **Qwen3.5-9B**
   at NVFP4 W4A4 the average accuracy drop falls from **5.10 → 3.10 percentage points** vs max
   scaling (MSE 3.87, Four-Over-Six 4.75), and **2.94 pp** combined with GPTQ, *"without
   incurring any deployment throughput penalty"* because the scales are computed once at
   checkpoint creation and reused at deployment (*corrected 2026-09-19: the phrase "zero
   additional deployment cost" previously quoted here does not appear on the page*)
   [src](https://nvidia.github.io/Model-Optimizer/announcements/local-hessian.html) (2026-09-09,
   `vendor`). **Two percentage points of accuracy for free is larger than most of the deltas
   this platform will be arguing about in front of a customer** (`00` §5.2 sets δ at 2–5 pp).

### 2.3 What quantization actually costs in accuracy

This is the evidence base for "how worried should the gate be".

| Evidence | Scheme(s) | Result | Status |
|---|---|---|---|
| Red Hat / Neural Magic, **>500,000 evaluations**, Llama 3.1 8B/70B/405B [src](https://developers.redhat.com/articles/2024/10/17/we-ran-over-half-million-evaluations-quantized-llms) (2024-10-17) | W8A8-INT, W8A8-FP, W4A16-INT | OpenLLM v1: *"all quantization schemes—regardless of model size—recover over 99% of the average score"*; v2: *"close to 99% … with all models maintaining at least 96% recovery"*; Arena-Hard-Auto: *"95% confidence intervals overlap for all model sizes and quantization schemes"* (500 prompts); HumanEval: **99.9 %** recovery at 8-bit, **98.9 %** at 4-bit | `meas.`, vendor-run but at unusual scale and with published methodology (vLLM 0.6.2) |
| NVIDIA QAD case study, Nemotron 3.5 Lightning [src](https://developer.nvidia.com/blog/developing-nemotron-3-5-lightning-nvfp4-with-qad-using-nvidia-model-optimizer/) (2026-08-17) | NVFP4 PTQ | Median recovery **96.33 %** (checkpoint A), **95.84 %** (checkpoint B), **99.24 %** (final conservative checkpoint) | `vendor` |
| Local-Hessian study [src](https://nvidia.github.io/Model-Optimizer/announcements/local-hessian.html) (2026-09-09) | **NVFP4 W4A4**, Qwen3.5-9B | Average drop **5.10 pp** with default max scaling | `vendor` |

**Read these three rows together and the shape of the risk is clear.** W8A8 and W4A16 on a
2024-era dense model are a ~1 % problem. **W4A4 — weights *and activations* at four bits, which
is exactly what buys the Blackwell FP4 pipe — is a 3–5 percentage-point problem before you
spend anything on recovery.** The repo's own per-model work says the same thing from the other
direction: on B300 the NVFP4 Qwen3.8-27B build executes NVFP4 on only **71.7 % of GEMM FLOPs**,
with attention and linear-attention projections left at FP8
([`models/qwen3827b/b300.md` §0](../models/qwen3827b/b300.md)) — the format is already being
applied selectively because applying it everywhere is not free.

**Decision rule.** Treat W8A8/W4A16 as a *screen-and-ship* change and **W4A4 as a
gate-and-probably-recover change.** Budget QAD whenever the target format is W4A4 and the
customer's margin δ is ≤ 3 pp.

### 2.4 Quantization-aware training / distillation (QAD)

**Mechanism.** Two stages. Stage 1 is ordinary PTQ to produce a quantized student. Stage 2
distills the **frozen full-precision teacher into the quantized student using KL divergence on
logits**, where *"every forward pass of the student model runs through simulated quantization so
the model can account for the quantization noise it will encounter at inference"*
[src](https://developer.nvidia.com/blog/developing-nemotron-3-5-lightning-nvfp4-with-qad-using-nvidia-model-optimizer/).

**Note the pleasing symmetry for this platform:** QAD is *distillation from the model to
itself*. The teacher is the BF16 checkpoint that already passed S7. There is no third-party
teacher, therefore **no teacher-ToS exposure** (`00` §8.1) and no annotation spend — the
recovery signal is generated locally. Of all the techniques in this document, QAD is the one
whose legal and data posture is cleanest.

**Inputs:** BF16 checkpoint (teacher), PTQ'd checkpoint (student), a prompt set — ideally the
customer's own S4 training distribution — and a trainer.
**Outputs:** a quantized checkpoint whose weights have been adapted to their own quantization
noise; identical file size to the PTQ checkpoint.

**Cost and effect, measured:**

| Case | PTQ median recovery | After QAD | Delta |
|---|---:|---:|---:|
| Checkpoint A (intermediate SFT) | 96.33 % | **99.72 %** | **+3.39 pp** |
| Checkpoint B (intermediate RL) | 95.84 % | **98.53 %** | **+2.69 pp** |
| Final conservative NVFP4 checkpoint | 99.24 % | 98.97 % | **−0.27 pp** |

[src](https://developer.nvidia.com/blog/developing-nemotron-3-5-lightning-nvfp4-with-qad-using-nvidia-model-optimizer/)
(2026-08-17, `vendor`). Run configuration: constant LR 5e-6, dropout disabled, gradient clipping
1.0, TP=2/EP=4 on two 8-GPU nodes, ≈6,400 samples at 524K sequence length ≈ 400 iterations
[ibid.]. Size: **66 GB full-precision → 22 GB NVFP4**, *"up to 4x faster throughput"* [ibid.].

**The third row is the most important one in the table and it is the one a vendor blog would
normally omit: QAD made the already-good checkpoint slightly worse in the median, with gains
"concentrated on agentic benchmarks rather than broad recovery"** [ibid.]. That is a real result
and it sets the decision rule.

**Decision rule.** Run PTQ first and measure. If median recovery ≥ the gate's threshold, **do
not run QAD** — it costs a multi-node training run and may cost accuracy. If recovery is in the
95–97 % band, QAD is the highest-value step in this entire document: **+2.7 to +3.4 pp for a
few hundred iterations.** If recovery is below ~93 %, suspect the format choice rather than the
recovery method, and go back to AutoQuantize with FP8 on the menu (§2.2).

**Failure modes.** (a) QAD on a prompt set that is not the customer's distribution recovers
*generic* accuracy and can still lose the customer's slice — always QAD on S4 data. (b) QAD
after DPO/RL can undo alignment properties that the RL stage installed; the checkpoint-B row
above is consistent with that, and the safety gate (I5) must be re-run unconditionally after
QAD. (c) It is a training run, so it inherits doc 05-training's reproducibility burden.

**Upstream corroboration that 4-bit training is viable at all:** NVIDIA trained a **12B hybrid
Mamba-Transformer on 10 trillion tokens in NVFP4** and reports it *"achieves training loss and
downstream task accuracies comparable to an FP8 baseline"*, calling it *"the longest publicly
documented training run in 4-bit precision to date"*
[src](https://arxiv.org/abs/2509.25149); the companion blog names the recipe — micro-block
scaling over 16-element blocks, E4M3 scale factors, Hadamard transforms, 2D block quantization,
stochastic rounding, selective high-precision layers — and claims *"GB300 delivering a 7x
speedup over Hopper"* on the core GEMMs
[src](https://developer.nvidia.com/blog/nvfp4-trains-with-precision-of-16-bit-and-speed-and-efficiency-of-4-bit/)
(2025-08-25, `vendor`). This is a *pretraining* result, not an inference-accuracy result, and
should be cited as "the format is numerically sound at scale", nothing more.

### 2.5 Pruning and structured sparsity

| Method | Mechanism | Published result | Retraining needed? |
|---|---|---|---|
| **SparseGPT** [src](https://arxiv.org/abs/2301.00774) | One-shot second-order pruning | **60 % unstructured sparsity with negligible increase in perplexity** on OPT-175B and BLOOM-176B — *"more than 100 billion weights"* ignored — in *"under 4.5 hours"*; *"generalizes to semi-structured (2:4 and 4:8) patterns, and is compatible with weight quantization"* | **No** |
| **Wanda** [src](https://arxiv.org/abs/2306.11695) | Prune by ‖weight‖ × ‖input activation‖, per output | *"significantly outperforms the established baseline of magnitude pruning and performs competitively against recent method involving intensive weight update"*; *"requires no retraining or weight update"* | **No** |
| **Minitron** (pruning + distillation) [src](https://arxiv.org/abs/2407.14679) | Prune depth/width/heads/MLP, then distill | 8B and 4B from a 15B base with **"up to 40x fewer training tokens per model compared to training from scratch"**, **<3 %** of the original data, **1.8×** compute saving for the family, **"up to a 16% improvement in MMLU scores compared to training from scratch"** | **Yes**, distillation |
| **ShortGPT** (layer dropping) [src](https://arxiv.org/abs/2403.03853) | Delete layers by a **Block Influence** score | *"significantly outperforms previous state-of-the-art (SOTA) methods in model pruning"*; *"orthogonal to quantization-like methods"* — abstract gives no layer count or accuracy number | Implied |
| **Puzzletron** (ModelOpt) | *"heterogeneous pruning & NAS of LLM and VLM models"* [src](https://github.com/NVIDIA/TensorRT-Model-Optimizer) (2026-05-13) | Not quantified on the page fetched ⚠️ | Yes |

Two industrial datapoints from ModelOpt's own customer list, both `vendor`: **Domyn compressed
Colosseum-355B → 260B** with Minitron pruning + distillation (2026-04-15), and **Bielik.AI built
Bielik Minitron 7B — "33% smaller, 50% faster, 90% quality retained"** (2026-03-17)
[src](https://github.com/NVIDIA/TensorRT-Model-Optimizer). Note the third number: **90 % quality
retained is a 10-point loss**, which would fail every gate in `00` §5.2. Pruning is a
*cost-reduction* technique with a real quality price, not a free lunch.

**Structured 2:4 sparsity, and why this document does not recommend it.** The tooling exists
(SparseGPT's 2:4 mode, `compressed-tensors`' *"unstructured and semi-structured sparsity"*
[src](https://github.com/neuralmagic/compressed-tensors)), and the silicon exists — every NVIDIA
data-centre spec table in this repo quotes a sparse column, with footnotes reading *"With
sparsity"* on B300, GB300 and H100
([`quantization-formats.md` §4.1](../cross-cutting/quantization-formats.md)). But:

- The repo's standing discipline is that **the sparse numbers are marketing and the dense
  numbers are the planning basis** — METHODOLOGY §8 pins dense figures for every GPU, and
  [`gpus/b300.md`](../gpus/b300.md) §11 records AWS's own honest *"1.5× GPU TFLOPS (at FP4,
  without sparsity)"* phrasing.
- **AMD:** the tree records that there is **no structured-sparsity path for 4/6-bit on CDNA4
  until AMD documents one** ([`quantization-formats.md`](../cross-cutting/quantization-formats.md)).
- I could fetch **no measured end-to-end serving speedup from 2:4 sparsity for any model in
  this repo on any GPU in this repo.**
- **Corrected 2026-09-19 — the tooling situation is worse than this section originally said.**
  This paragraph read that llm-compressor's feature list "does not name 2:4 as a standalone
  supported format" and that the 2:4+FP8 example URL 404'd. The docs are not silent, they are
  explicit: *"Sparse compression (including 2of4 sparsity) is **no longer supported** by LLM
  Compressor due to lack of hardware support and user interest"*
  [src](https://docs.vllm.ai/projects/llm-compressor/en/latest/) (v0.13.0, re-fetched
  2026-09-19); the README names no sparsity support at all
  [src](https://github.com/vllm-project/llm-compressor). The 404 was not a broken link — the
  example was removed with the feature. `compressed-tensors` still describes *"unstructured and
  semi-structured sparsity"* as an on-disk capability
  [src](https://github.com/neuralmagic/compressed-tensors), so the **format** survives while the
  **producer** in this toolchain does not.

⚠️ **TO BE VERIFIED, and this is the honest state of it:** 2:4 sparsity has no measured payoff in
this tree **and the mainstream vLLM-targeted quantizer has dropped it**, citing lack of hardware
support and user interest. **Decision: do not put it in the platform's default ladder** — this is
now a stronger decision than "unproven", it is "actively deprecated upstream". Revisit only if an
engine publishes a measured 2:4 throughput number for a model of the repo's shape **and** a
maintained producer exists. Q2.

**Decision rule for pruning generally.** For this platform, pruning is almost always the wrong
tool, for a commercial reason rather than a technical one: **the customer's student is already
chosen from a catalog of open-weights models at many sizes.** If Qwen3.8-27B is too big, the
answer is a smaller Qwen, not a pruned one — a smaller *published* model comes with an ecosystem
of engine support, recipes, quantized builds and draft heads, all of which a pruned custom
architecture forfeits. Prune only when (a) no published base exists at the size you need, **and**
(b) the customer's volume amortises a retraining project. Both conditions are rare.

### 2.6 Layer dropping

The special case of depth pruning is worth naming separately because it is the one pruning
variant that is cheap enough to try speculatively: delete whole transformer blocks ranked by a
redundancy score, then distill to recover. ShortGPT's contribution is the score (Block
Influence) and the observation that *"the ability to achieve better results through simple layer
removal, as opposed to more complex pruning techniques, suggests a high degree of redundancy in
the model architecture"* [src](https://arxiv.org/abs/2403.03853).

For the repo's models this is structurally constrained in a way the generic literature does not
cover: **Qwen3.8-27B and Marlin-2B are hybrid models** — Marlin-2B has full attention on only
**6 of 24 layers**, the rest being Gated-DeltaNet
([`models/marlin2b/rtx6000-pro.md` §1.1](../models/marlin2b/rtx6000-pro.md)). Dropping layers
from a hybrid stack changes the *ratio* of attention to linear-attention layers, and with it the
KV-per-token and state-per-sequence figures that every cost row in this repo is built on.
⚠️ **TO BE VERIFIED** — no published layer-dropping result on a hybrid GDN/Mamba architecture
was found; Q3. **Do not layer-drop a hybrid model without re-deriving its memory model.**

### 2.7 KV-cache quantization

The tree already owns this completely:
[`quantization-formats.md` §8](../cross-cutting/quantization-formats.md) covers what each engine
exposes, why FP8 KV is *"not just storage"*, and scale calibration and layer skipping. Do not
re-derive it. Three things this document adds, all about *the decision*, not the mechanism:

1. **The research baseline for how far this can go.** KIVI establishes the asymmetric scheme —
   *"the key cache should be quantized per-channel"* and *"the value cache should be quantized
   per-token"* — reaching **2-bit**, *"2.6× less peak memory (including model weight)"*, *"up to
   4× larger batch size, bringing 2.35× ∼ 3.47× throughput"*, tuning-free
   [src](https://arxiv.org/abs/2402.02750). That is the ceiling the field has demonstrated; the
   engines ship FP8 and are only now shipping FP4.
2. **KV quantization buys concurrency, not latency, and on hybrid models it buys less than you
   think.** Measured on Qwen3.8-27B: FP8 KV moves **76,458 → 91,022 KV tokens at 32K on a 5090
   = 1.19×, not 2×, because the 78.4 MB/slot recurrent-state pool does not shrink**
   ([`models/qwen3827b/b300.md` §2](../models/qwen3827b/b300.md), citing
   [`serving-optimizations.md` §7.3](../cross-cutting/serving-optimizations.md)). **For a
   hybrid-state model, halving the KV dtype halves only part of the memory.** The platform's
   config search must model the state pool explicitly or it will predict a concurrency it cannot
   reach.
3. **It is a fork, not a flag** — see §1.4 rule 2 (FA4 vs FP8-KV on Blackwell).

### 2.8 Speculative decoding: the draft head is a *per-customer* artifact

This is the section where the platform has the clearest technical edge over a generic serving
vendor, and it follows from one fact: **a distilled student's output distribution is
deliberately different from every published model's**, so a generic draft head is mispredicting
it by construction.

**The measured case for customization** is Fireworks' FireOptimizer, which reports that for a
specialized use case a generic draft model achieved only a **29 % hit rate and actually
"increased latency by 1.5x"**, while a customized draft reached **76 % hit rate with a 2x speed
increase**, and claims *"3x speedup over a generic draft model"* overall
[src](https://fireworks.ai/blog/fireoptimizer) (2024-08-30, `vendor`). Read the first half of
that sentence twice: **a mismatched draft head is not neutral, it is negative.** The repo
records the same phenomenon at the format level (NVFP4 draft-expert routing, ~0 % acceptance
without a patch — §1.4).

**Together's ATLAS** is the productized version of the same idea as a *continuous* loop: a
heavyweight **static speculator** *"trained on a broad corpus"* as a floor, a lightweight
**adaptive speculator** allowing *"rapid, low-overhead updates from real-time traffic,
specializing on-the-fly to emerging domains"*, and a **confidence-aware controller** that
*"chooses which speculator to trust at each step and what speculation lookahead to use"*. Claims
on HGX B200: **up to 500 TPS on DeepSeek-V3.1**, **460 TPS on Kimi-K2**, *"2.65x faster than
standard decoding"*, and a **"400% speedup over the FP8 baseline (improvement from 105 TPS to
501 TPS)"** [src](https://www.together.ai/blog/adaptive-learning-speculator-system-atlas)
(2025-10-10, `vendor`, not independently replicated).

**ATLAS is the single most directly competitive product to this document's §4 thesis**, because
it is an auto-research loop for inference that already ships: it consumes production traffic and
improves a serving artifact without human involvement. It is also *narrow* — it optimizes one
knob (the speculator) — which is exactly why it works and why it is safe.

**Tools for training the draft head:**

| Tool | Methods | Notes |
|---|---|---|
| **SpecForge** [src](https://github.com/sgl-project/SpecForge) | **EAGLE3, P-EAGLE, EAGLE3.1, DFlash, DFlash2, Domino, DSpark**; online and offline training (offline in colocated and disaggregated topologies) | *"a framework for training speculative decoding models so that you can smoothly port them over to the SGLang serving framework"*; claims *"up to 4x speedup"* with SpecBundle models. **v0.3.0, 2026-08.** Example configs reference Qwen3-8B/30B and Qwen3.6-27B [ibid.] |
| **ModelOpt** [src](https://github.com/NVIDIA/TensorRT-Model-Optimizer) | *"Train draft modules to predict extra tokens during inference"*, with HF and Megatron-LM examples | Same library as the quantizer — which matters for §1.4 rule 1 (train the draft against the quantized target) |

**The method to default to is EAGLE-3**, on the strength of its scaling property rather than its
headline number: it *"abandons feature prediction in favor of direct token prediction and
replaces reliance on top-layer features with multi-layer feature fusion"*, reports *"a speedup
ratio up to 6.5x, with about 1.4x improvement over EAGLE-2"* and *"a 1.38x throughput
improvement at a batch size of 64"* in SGLang, and — the load-bearing claim — fixes EAGLE's
limitation that *"scaling up data provides limited improvements"*, enabling *"the draft model to
fully benefit from scaling up training data"* [src](https://arxiv.org/abs/2503.01840).
**A draft head that benefits from more data is a draft head this platform can keep improving
from production traffic**, which is precisely the loop.

**What this means for the repo's two example students:**

- **Qwen3.8-27B ships an in-checkpoint MTP head** (0.425 B, γ=3), with acceptance measured at
  **92.2 % BF16 / 84.8 % FP8 on GB300 at TP4**, and mean accepted length **4.28** across five
  benchmarks ([`models/qwen3827b/b300.md` §2](../models/qwen3827b/b300.md)). After distillation,
  **that head is stale** — it was trained against the base model's distribution. Retraining it
  (or an EAGLE-3 head) on the student is a concrete, bounded S8 task with a measurable objective
  (acceptance on the customer's traffic).
- **Marlin-2B has no draft head at all and cannot get one from the checkpoint:** `config.json`
  declares `mtp_num_hidden_layers: 1` but the index contains **zero `mtp` tensors** — the
  missing module is exactly 60,828,160 params — and *"no EAGLE head exists for Marlin"*
  ([`gpu-optimizations.md` §7](../matrix/gpu-optimizations.md)). The same row makes the
  countervailing point: for Marlin *"it could not help: every scenario is prefill-bound, and
  speculation cannot touch prefill"*. **So for the video student, draft-head training is not an
  opportunity, it is a non-problem** — and recognising that saves the platform a pointless
  training run. ⚠️ Training an EAGLE3 head for a hybrid-GDN VLM is unattempted in public; Q7.

**Failure modes of draft heads, all of which the platform must instrument:**
- **Acceptance is a property of the workload**, and the repo is unusually strict about this:
  **two** numbers that look identical (**3.51**) have opposite epistemic status — a synthetic
  benchmark constant (`"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51`
  in the DeepSeek MI355X recipe) and a real per-request mean (Qwen3.8-27B DSpark on MBPP); the
  source's third case is a *different* number, SGLang's simulated **4.5** (and **5.5**) under
  `SGLANG_SIMULATE_ACC_LEN` ([`gpu-optimizations.md` §7](../matrix/gpu-optimizations.md)).
  *Corrected 2026-09-19 — this read "three numbers that look identical (3.51)"; the cited section
  lists two 3.51s and a separate simulated 4.5/5.5.* **The platform must report
  acceptance measured on the customer's own traces, and never quote a recipe's constant.**
- **Gains shrink at high batch** ([`serving-optimizations.md` §2.5](../cross-cutting/serving-optimizations.md)),
  so the draft head's value depends on the operating point chosen in §3 — another reason the
  config sweep must come last.
- **Speculation costs KV/state slots** (γ+1 per request), which reduces concurrency — e.g.
  DFlash2 at γ=7 costs `D = 8` state slots on Qwen3.8-27B
  ([`models/qwen3827b/b300.md` §2](../models/qwen3827b/b300.md)). The sweep must price this.
- **Turning it off is catastrophic on some pairs**: [`cost-matrix.md` §7.3](../matrix/cost-matrix.md)
  prices removal at **3.1× to 6.9× worse** depending on pair. Any artifact whose economics
  depend on speculation must carry acceptance as a **monitored SLI**, not a build-time constant.

### 2.9 LoRA merge vs multi-LoRA serving

The same trained adapter can be shipped two ways, and the choice is a cost/latency/isolation
trade, not a technical preference.

| | **Merge into base** | **Serve as adapter** |
|---|---|---|
| Artifact | One full-size weight set per customer version | One base + N small adapters |
| Quantization | Merge **then** quantize, so the quantizer sees the served weights | Base is quantized once; adapter stays higher precision. ⚠️ vLLM's LoRA page as fetched *"does not explicitly document quantized base model + LoRA support"* [src](https://docs.vllm.ai/en/latest/features/lora.html) — Q8 |
| Latency | No adapter overhead | Small per-token cost; Punica measured *"only adding 2ms latency per token"* at its scale [src](https://arxiv.org/abs/2310.18547) |
| Throughput at N tenants | N replicas | S-LoRA: *"improve the throughput by up to 4 times"* vs HF PEFT and naive-LoRA vLLM [src](https://arxiv.org/abs/2311.03285); Punica: *"12x higher throughput in serving multiple LoRA models compared to state-of-the-art LLM serving systems"* [src](https://arxiv.org/abs/2310.18547) |
| Version swap | Full weight reload (§5.7, [`scaling/06` §2](../scaling/06-cold-start.md)) | Adapter hot-load: `POST /v1/load_lora_adapter`, with `load_inplace=true` to replace under the same name [src](https://docs.vllm.ai/en/latest/features/lora.html) — the cheapest version swap available ([`scaling/06` §6.3](../scaling/06-cold-start.md)) |
| Isolation | Complete | Shared process, shared base, shared scheduler — a tenancy decision (doc 08) |
| Draft head | Can be trained against the merged target | ⚠️ acceptance under a hot-swapped adapter is unmeasured; Q9 |

**Decision rule.** **Merge for the customer's `main` endpoint once the version is stable and the
volume justifies a dedicated replica; serve as an adapter for `dev`, for every candidate under
evaluation, and for every tenant below the dedicated-replica break-even (§6.2).** The merged
path gives the cleanest artifact and the cleanest gate; the adapter path gives sub-minute
promotion and shared GPU economics. A platform that supports only one of the two either burns
GPUs on small tenants or cannot promote quickly.

One operational warning the repo already records: vLLM says runtime adapter updating *"comes
with security risks"* and *"should not be used in production unless it is an isolated, fully
trusted environment"*, and `--max-lora-rank` set too high *"wastes memory and can cause
performance issues"* — and it is **fixed at server start**, i.e. a cold-start-time decision with
steady-state consequences ([`scaling/06` §6.3](../scaling/06-cold-start.md), quoting
[vLLM LoRA docs](https://docs.vllm.ai/en/latest/features/lora.html)).

### 2.10 Accuracy recovery and eval gating after each rung

**Two tiers, and the cost difference between them is the entire reason for the design.**

| | **Tier A — the cheap screen** | **Tier B — the exit gate** |
|---|---|---|
| When | After every rung in §2.1 | Once, on the final built artifact, at the served config |
| What | (1) **Contract conformance** — schema validity, tool-call encoding, streaming event order, refusal stop-reason (I4 in `00` §1.3); (2) **programmatic checks** on a fixed 200–500-example probe; (3) **distribution telemetry** — output-length distribution, refusal rate, distinct-n, and per-token KL vs the BF16 reference on a fixed prompt set | The full frozen suite: per-slice, judged, with δ/α/power, plus the safety suite unconditionally |
| Why it catches things | Contract failures are **binary and free to detect** and are the most common quantization/engine casualty. The per-token KL against the BF16 reference is the sharpest available signal: it is dense, needs no judge, and it *localizes* — you see which prompts moved | It is the claim the customer will be shown |
| Cost | minutes of GPU + no judge tokens | judge tokens dominate; `00` §5.3's n |
| Failure action | **Stop and bisect.** Do not proceed to the next rung | Fail the artifact; return to the rung the screens implicated |

**The KL-against-reference screen deserves emphasis** because it is nearly free and almost
nobody runs it. Keep the BF16 checkpoint servable on one GPU during S8; run both models on a
fixed 500-prompt probe drawn from S4; report mean and p99 per-token KL and the top-20 prompts by
divergence. Any rung that moves p99 KL materially is a rung that will move the eval, and you
know it in minutes rather than after a judged run. ⚠️ I found no published threshold mapping
per-token KL to downstream task delta — the platform will have to calibrate its own per task
family, which is a genuine research task, not a lookup; Q10.

**The harness to standardise on for Tier B** is **lm-evaluation-harness**: *"a unified framework
to test generative language models on a large number of different evaluation tasks"*, *"over 60
standard academic benchmarks … with hundreds of subtasks"*, with backends for **HF transformers,
vLLM, SGLang, NVIDIA NeMo/Megatron-LM** and OpenAI-compatible APIs, and YAML-configured tasks so
that *"evaluation with publicly available prompts ensures reproducibility and comparability"*
[src](https://github.com/EleutherAI/lm-evaluation-harness). Its importance here is not the
academic benchmarks — doc 04 will build customer-specific ones — but the **vLLM/SGLang backend**,
which lets the exit gate run *through the actual serving stack at the actual config*. That is
what makes gate-twice mean what it says.

---

## 3. Inference-config search on the target hardware

### 3.1 The search space

| Axis | Typical cardinality | Owned by | Interactions |
|---|---:|---|---|
| Engine | 2–4 (vLLM / SGLang / TRT-LLM / Dynamo-fronted) | [`inference-engines.md`](../cross-cutting/inference-engines.md) | Determines which of everything below exists |
| Engine version / container digest | pinned | §5.4 | Changes kernels, defaults **and** compile caches ([`scaling/06` §3.1](../scaling/06-cold-start.md)) |
| Weight format | 1–4 | §2.2 | Constrained by silicon |
| KV dtype | 2–3 | §2.7 | **Forks** the attention-backend choice (§1.4) |
| Attention backend | 2–4 | [`flash-attention.md`](../cross-cutting/flash-attention.md) | Silent fallbacks (§3.8) |
| TP / PP / DP / EP | small but not free | [`scaling/02`](../scaling/02-serving-stack-and-routing.md) | For the repo's small students, TP>1 mostly *hurts* — see §7 |
| `max_num_seqs` | 4–8 values | the sweep | Directly sets the operating point |
| `max_num_batched_tokens` (chunked prefill) | 4–6 values | the sweep | **Measured**: 16384→2048 costs *~64 % of throughput at c=128* and buys p99 ITL 342→194 ms ([`serving-optimizations.md` §4.1](../cross-cutting/serving-optimizations.md)) |
| Prefix caching on/off + policy | 2–3 | [`serving-optimizations.md` §1](../cross-cutting/serving-optimizations.md) | Value is entirely workload-determined (§1.2) |
| CUDA-graph capture sizes | 2–3 | [`serving-optimizations.md` §4.2](../cross-cutting/serving-optimizations.md) | Capture must cover `max_num_seqs × (1 + γ)` |
| Speculation method + γ | 0–4 × 3 | §2.8 | Costs state slots; gains shrink with batch |
| P/D disaggregation | 2 | [`serving-optimizations.md` §3.5](../cross-cutting/serving-optimizations.md) | Only pays at scale |

Multiply it out and a full grid is 10⁴–10⁵ configs. **That is why the search must be staged, not
gridded (§3.5).**

### 3.2 There is no "best config" — there is a curve

The output of config search is **a Pareto frontier of throughput against interactivity**, not a
number. The tree already establishes this
([`serving-optimizations.md` §4.3](../cross-cutting/serving-optimizations.md),
[`cost-matrix.md` §2 vs §3](../matrix/cost-matrix.md) — the whole reason the cost matrix has both
an S1 "interactive, TPOT ≤ 50 ms" grid and an S4 "max throughput, no SLO" grid). The customer's
SLO selects a point on the curve; the platform's job is to produce the curve and to state which
point was chosen and why.

**Corollary for the product:** the benchmark report should show the customer *their* point on
*their* curve next to the incumbent's latency. A single "we are 3× cheaper" number is both less
convincing and less true.

### 3.3 Tools that exist

| Tool | What it does | Search? | SLO handling |
|---|---|---|---|
| **vLLM `auto_tune`** [src](https://github.com/vllm-project/vllm/tree/main/benchmarks/auto_tune) | *"automates the process of finding the optimal server parameter combination (`max-num-seqs` and `max-num-batched-tokens`) to maximize throughput"* | **Grid** over `NUM_SEQS_LIST` × `NUM_BATCHED_TOKENS_LIST`, plus an adaptive rate search: *"If the latency is too high, the script performs a search by iteratively decreasing the request rate until the latency constraint is met"* | `MAX_LATENCY_ALLOWED_MS` = *"maximum allowed P99 end-to-end latency"*; `MIN_CACHE_HIT_PCT` sets prefix-cache hit rate. **⚠️ E2E only — the README as fetched does not expose TTFT or TPOT constraints separately** |
| **GuideLLM** [src](https://github.com/vllm-project/guidellm) | *"a platform for evaluating how language models perform under real workloads and configurations"*; reports TTFT, ITL, E2E distributions | **`sweep` profile** *"identifies the maximum performance and maximum rates for the model"* | Rate types: synchronous, concurrent, throughput, constant, **Poisson** — the last matters because real arrivals are not uniform |
| **SGLang bench suite** [src](https://docs.sglang.io/developer_guide/benchmark_and_profiling.html) | `bench_serving` (*"async HTTP load-testing client … at controlled rates with configurable concurrency"*, TTFT/TPOT/ITL), `bench_one_batch_server`, `bench_offline_throughput`, `bench_one_batch` (*"kernel-level latency profiling of a single static batch"*) | No built-in search | Load generated at set concurrency/rate; torch-profiler and `nsys` integration (`--enable-layerwise-nvtx-marker`) |
| **trtllm-bench** [src](https://nvidia.github.io/TensorRT-LLM/performance/perf-benchmarking.html) | `throughput` and `latency` subcommands; `build` with `--quantization FP8\|NVFP4`, `--tp_size`, `--pp_size` | **No search**; instead *"tuning heuristics"* derive `max_batch_size` and `max_num_tokens` from *"high-level statistics of the dataset (average ISL/OSL, max sequence length)"* | Implicit via the dataset |
| **NVIDIA Dynamo Planner** [src](https://github.com/ai-dynamo/dynamo) | *"an SLA-driven autoscaler that profiles workloads and right-sizes pools"* to meet latency targets at minimum TCO; **AISimulate** *"predicts serving behavior and searches deployment configurations offline"* without provisioning a cluster | Yes, offline | ⚠️ **The detailed profiling parameters could not be retrieved this session** — three documented URLs returned 404 and `docs.nvidia.com/dynamo/llms-full.txt` returned an empty body. What *is* sourced in this repo: the planner publishes `dynamo_planner_estimated_ttft_ms`, `dynamo_planner_estimated_itl_ms`, `dynamo_planner_predicted_num_prefill_replicas`, `dynamo_planner_predicted_num_decode_replicas` ([`scaling/05` §3.5](../scaling/05-autoscaling-and-predictive-scaling.md)). Q11 |
| **Vidur / Vidur-Search** [src](https://arxiv.org/abs/2405.05465) | Simulator built from operator profiling; **estimates latency with "less than 9% error"** | Yes — over *"parallelization strategies, batching techniques, and scheduling policies"* | Claims a LLaMA2-70B config found *"in one hour on a CPU machine, in contrast to a deployment-based exploration which would require 42K GPU hours - costing ~218K dollars"* |
| **InferenceX** (formerly InferenceMAX) [src](https://github.com/SemiAnalysisAI/InferenceX) | *"an inference performance research platform dedicated to continually analyzing & benchmarking the world's most popular open-source inference frameworks"*, Apache-2.0; GB300 NVL72, GB200 NVL72, MI355X, B300, B200, MI325X, MI300X, H200, H100 × SGLang/vLLM/TRT-LLM × DeepSeek V4, Kimi K3, MiniMax M3, GLM5, Qwen | Not a search tool — a **continuously-run public benchmark** | The repo already uses InferenceX rows as its nearest measured anchors ([`models/qwen3827b/b300.md` §0](../models/qwen3827b/b300.md)) |
| **MLPerf Inference (Datacenter)** [src](https://mlcommons.org/benchmarks/inference-datacenter/) | Offline / Server / Single-stream / Multiple-stream scenarios; **Closed division** *"requires using the same model as the reference implementation"*, Open *"allows using a different model or retraining"* | No | The **discipline** to copy, not the harness: every result is a model + dataset + quality target + latency constraint, and the division rules make "which model did you actually run" an explicit field ⚠️ exact accuracy-target percentages live in the rules repo, not the page fetched |

**⚠️ Note the shape of this table: nothing on it does what this platform needs.** vLLM auto_tune
sweeps two knobs against one E2E-latency constraint. Dynamo plans replicas, not build configs.
Vidur searches parallelism and scheduling but is a simulator. **There is no open tool that takes
{checkpoint, GPU, SLO, traffic profile} and returns a ranked, gated, reproducible serving config
across format × KV × backend × batch × speculation.** That gap is a build item (§"Implications").

### 3.4 Encoding the SLO constraint properly

The objective is **not** "maximise tokens/s". Write it down, because writing it down changes the
answer:

```
minimise    blended_cost_per_1M(config)              # METHODOLOGY §6, at the customer's price tier
subject to  p99_TTFT(config, λ, traffic) ≤ TTFT_slo
            p99_TPOT(config, λ, traffic) ≤ TPOT_slo
            concurrency_capacity(config) ≥ λ × E[session_duration] × headroom
            gate_pass(artifact(config)) = true
            KV_pool(config) ≥ p99_context_length
over        config ∈ feasible(GPU, engine, checkpoint)
```

Four things this formulation forces that the naive one does not:

1. **p99, not p50.** `00` §1.3 I3 requires both. The chunked-prefill measurement in §3.1 shows
   the two disagree violently: the config that maximises throughput has **p99 ITL 342 ms**; the
   config that fixes p99 ITL to 194 ms gives up **64 % of throughput**
   ([`serving-optimizations.md` §4.1](../cross-cutting/serving-optimizations.md)). A search
   optimising mean latency picks the first and ships a tail the customer will notice.
2. **The traffic profile is an argument, not an assumption.** Run the sweep at the customer's
   measured ISL/OSL distribution **and** measured prefix-cache hit rate. `MIN_CACHE_HIT_PCT` in
   vLLM's auto_tune exists exactly for this [src](https://github.com/vllm-project/vllm/tree/main/benchmarks/auto_tune).
   The repo's hit-rate spread (≈0 % video → 99.7 % agentic,
   [`cost-matrix.md` §7.2](../matrix/cost-matrix.md)) means a default of 50 % is wrong for almost
   everybody.
3. **`gate_pass` is a constraint, not a tiebreak.** A config that is 20 % cheaper and fails the
   screen is infeasible. This is the formal version of "never let the optimizer touch its own
   objective".
4. **Capacity is a constraint separate from latency.** For hybrid models the binding constraint
   is frequently the state pool, not compute — on Qwen3.8-27B/B300 the repo is explicit that
   *"The TPOT SLO never binds on this pair — KV capacity (396 concurrent 4.5 K sequences) and
   prefill compute do"* ([`models/qwen3827b/b300.md` §0](../models/qwen3827b/b300.md)). A search
   that only checks latency will happily propose a config that OOMs at p99 context length.

### 3.5 Search strategy

**Stage it. Do not grid it.**

| Stage | Method | Configs evaluated | Cost |
|---|---|---:|---|
| **0. Prune by silicon and engine support** | Lookup, not search — [`quantization-formats.md` §9.6](../cross-cutting/quantization-formats.md), [`inference-engines.md` §8](../cross-cutting/inference-engines.md) | 10⁵ → 10²–10³ | free |
| **1. Prune by roofline** | METHODOLOGY §4 arithmetic: bytes per decode step, KV per token, state per sequence. Discard anything that cannot meet the SLO in principle | 10³ → 10² | free (a script) |
| **2. Simulate** (optional) | Vidur-class, *"less than 9% error"* [src](https://arxiv.org/abs/2405.05465) | 10² → 20–50 | CPU-hours |
| **3. Coarse grid on real hardware** | vLLM `auto_tune` / GuideLLM `sweep` / SGLang `bench_serving` | 20–50 | **$120–$300** (§1.6) |
| **4. Local refinement** | Bayesian / coordinate descent around the incumbent best, **only on continuous-ish axes** (`max_num_seqs`, chunk budget, γ) | 10–20 | $50–$100 |
| **5. Confirm** | Long-run replay of real traces at the chosen point, plus the exit gate | 1–3 | GPU-hours + judge tokens |

**Why Bayesian optimization only at stage 4.** The space is mostly **categorical with hard
feasibility structure** (format, backend, engine), where a surrogate model has little to learn
and where a wrong extrapolation proposes an infeasible config. The continuous-ish knobs are few
and cheap to grid. **⚠️ TO BE VERIFIED — I found no published application of Bayesian
optimization to LLM serving configuration; the tools I could fetch are grid- or heuristic-based**
(vLLM auto_tune: grid; trtllm-bench: heuristics from dataset statistics; Vidur-Search: search
over a defined space, method not stated in the abstract). Treat §3.5 stage 4 as a design
proposal, not an established practice. Q12.

**Why simulation is optional and not load-bearing.** Vidur's <9 % error is on *its* validated
models; the repo's models include a hybrid GDN VLM that no engine officially registers
([`models/marlin2b/b300.md` §0](../models/marlin2b/b300.md)) and MoE checkpoints with
architecture-specific kernel paths. Use the simulator to prune, never to decide.

### 3.6 Cutting the wall-clock of a sweep

Each config costs a **server start + warm-up + load**, and the server start dominates for small
models. The tree owns this: [`scaling/06`](../scaling/06-cold-start.md) §1 (stage anatomy), §2
(weight-loading fast paths), §3 (compile/JIT/graph caches), §5.3 (pre-staged weights). Applied to
a sweep:

- **Reuse the compile cache across configs that share architecture, dtype, GPU and engine
  version** — the cache is invalidated by *"model architecture, dtype, GPU type and engine
  version"* but **not** by weight values ([`scaling/06` §6.3](../scaling/06-cold-start.md)). So
  sweeping `max_num_seqs` is cheap; sweeping *format* is not.
- **Order the sweep to change the expensive axis least often**: format (outermost) → backend →
  KV dtype → batch/chunk (innermost). This is a scheduling decision worth 2–5× of sweep
  wall-clock. `est.`, from the stage costs in [`scaling/06` §1.2](../scaling/06-cold-start.md).
- **Use `--load-format dummy`** for pure-throughput probes where output text does not matter
  (SGLang documents it as a benchmarking option
  [src](https://docs.sglang.io/developer_guide/benchmark_and_profiling.html)). **Never** for a
  gate run.
- **Run the sweep on the `dev` replica that already exists** rather than provisioning — the
  platform is paying for it anyway (§6.4).

### 3.7 The reproducible benchmark report

This is a deliverable with a schema, because it is shown to a customer and because doc 07 will
re-derive cost from it.

```yaml
report_id:            <uuid>
generated_at:         2026-09-19T00:00:00Z
artifact:
  registry_uri:       <uri>@sha256:<digest>
  lineage_id:         <see §5.3>
  weight_format:      NVFP4-mixed          # what EXECUTES, not what the filename says
  kv_dtype:           fp8_e4m3
  attention_backend:  triton               # and what was REQUESTED, if different
  speculation:        {method: mtp, gamma: 3}
  prompt_stack_hash:  sha256:<...>
environment:
  gpu:                B300 HGX, 1 GPU, 268 GB as deployed
  engine:             vllm 0.29.0 @ <container digest>
  driver_cuda:        <...>
  flags:              [<every flag verbatim>]
workload:
  source:             customer-traces@<snapshot id>
  isl_p50_p99:        [4096, 18432]
  osl_p50_p99:        [512, 2048]
  prefix_cache_hit:   0.83                 # MEASURED on those traces, not assumed
  arrival:            poisson, lambda=<...>
results:
  pareto:             [{concurrency, tok_s_per_gpu, ttft_p50, ttft_p99, tpot_p50, tpot_p99}, ...]
  chosen_point:       {concurrency: 256, ...}
  slo_check:          {ttft_p99: PASS, tpot_p99: PASS, capacity: PASS}
  acceptance_rate:    0.77                 # MEASURED on customer traces; null if no speculation
  cost_per_1m:        {input, output, blended} x {low, high, res1y}
gate:
  tier_a_screens:     [{rung, verdict, kl_p99, contract_pass_rate}, ...]
  tier_b:             {suite@version, per_slice_deltas, judge, judge_noise_floor, safety: PASS}
provenance:
  calibration_set:    sha256:<...>
  tool_versions:      {llm-compressor: 0.13.0, modelopt: <...>, ...}
  reproduce:          <verbatim command line>
  signature:          <§5.5>
confidence:           estimate | measured      # METHODOLOGY legend
```

Four fields are non-negotiable and are the ones usually missing:
**`weight_format` = what executes** (the repo's whole §4.1 of
[`gpu-optimizations.md`](../matrix/gpu-optimizations.md) exists because filenames lie);
**`prefix_cache_hit` measured**; **`acceptance_rate` measured**; and **`reproduce`**, a command
someone else can run.

### 3.8 How automated sweeps lie

| Failure | Mechanism | Detection |
|---|---|---|
| **Silent kernel fallback** | vLLM *"silently drops to FA2"* when FA4's preconditions are not met on Blackwell ([`models/qwen3827b/b300.md` §2](../models/qwen3827b/b300.md)); the fused GDN decode op *"falls back to Triton silently if the op is not built"* [ibid.] | Log and assert the backend actually selected; treat requested ≠ selected as a sweep failure, not a data point |
| **Benchmark ≠ traffic** | Synthetic uniform ISL/OSL at 50 % cache hit against real traffic at 97.6 % ([`cost-matrix.md` §7.2](../matrix/cost-matrix.md)) | Replay real traces at stage 5 |
| **Acceptance constants smuggled in as measurements** | `synthetic_acceptance_length: 3.51`, `SGLANG_SIMULATE_ACC_LEN=4.5` ([`gpu-optimizations.md` §7](../matrix/gpu-optimizations.md)) | Ban simulated acceptance in reports; measure or report null |
| **Roofline optimism** | The repo's own anchors land at **0.28–0.30× of roofline** at full batch for B300 vLLM runs ([`models/marlin2b/b300.md` §0](../models/marlin2b/b300.md)) | Label every unmeasured row `est.`; apply the documented haircut before quoting |
| **Config that OOMs at p99 context** | Sweep run at mean context length | Add the capacity constraint (§3.4 item 4) |
| **Overfitting the sweep to one snapshot of traffic** | Traffic is non-stationary (`00` §5.4) | Re-sweep on a cadence; treat the config as a versioned artifact that expires |

---

## 4. The "auto-research" loop for inference

### 4.1 What the claims are

| System | Claim | Status |
|---|---|---|
| **AlphaEvolve** (DeepMind) [src](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) | *"an evolutionary coding agent powered by large language models"*. Borg scheduling heuristic *"continuously recovers, on average, 0.7% of Google's worldwide compute resources"*, *"in production for over a year"*. Gemini matmul kernel **23 % speedup** → *"a 1% reduction in Gemini's training time"*. FlashAttention kernel **"up to a 32.5% speedup"**. A Verilog rewrite *"integrated into an upcoming Tensor Processing Unit"*. 4×4 complex matmul in **48 scalar multiplications**, improving on Strassen; improved solutions in *"20% of cases"* across 50+ open math problems | `vendor`, deployed. **The strongest evidence in this table, and notice what it is: narrow objectives with mechanical verifiers** |
| **KernelBench** [src](https://arxiv.org/abs/2502.10517) | *"250 carefully selected PyTorch ML workloads"*; `fast_p` = *"percentage of generated kernels that are functionally correct and offer a speedup greater than an adjustable threshold p over baseline"*. SOTA reasoning models match the PyTorch baseline in *"less than 20% of the cases"* | `meas.`, the sober baseline |
| **Kevin-32B** (Cognition) [src](https://cognition.com/blog/kevin-32b) (2025-05-06) | Multi-turn RL on QwQ-32B over 180 KernelBench L1+L2 tasks. With 8 refinement steps: *"65% of its attempts correct on average"*, *"solves 89% of the dataset"* vs o4-mini **53 %** and o3 **51 %**; *"best@16 speedup of 1.41x"*; on Level 2, avg@16 correctness **48 % vs 9.6 % / 9.3 %** | `vendor`, replicable benchmark |
| **GEAK** (AMD) [src](https://arxiv.org/abs/2507.23194) | Reflexion-style agent generating **Triton** kernels for **MI300X/MI250**: *"correctness up to 63%"*, *"execution speed up of up to 2.59X"* | `meas.` (paper) |
| **robust-kbench / agentic CUDA framework** [src](https://arxiv.org/abs/2509.14279) | A new benchmark because *"existing kernel generation benchmarks suffer from exploitable loopholes and insufficient diversity"*; an agent that translates torch→CUDA and optimizes with *"a novel evolutionary meta-generation procedure"* and *"LLM-based verifiers for correctness"*; result stated qualitatively: kernels *"outperforming torch implementations for practical applications"* | `meas.`, **and the existence of this paper is itself the §4.2 finding** |
| **ADRS** — "Barbarians at the Gate" [src](https://arxiv.org/abs/2510.06189) | AI-Driven Research for Systems across load balancing, **MoE inference**, LLM-SQL and transaction scheduling: *"discovers algorithms that outperform state-of-the-art human designs"* with *"up to 5.0x runtime improvements or 50% cost reductions"*. Thesis: systems research suits this because *"system performance problems naturally admit reliable verifiers"* | `meas.` (paper). **The verifier argument is the whole argument** |
| **Together ATLAS** [src](https://www.together.ai/blog/adaptive-learning-speculator-system-atlas) | §2.8 — a shipped, narrow auto-research loop | `vendor` |
| **Fireworks FireOptimizer** [src](https://fireworks.ai/blog/fireoptimizer) | §2.8 — automated draft-model training from customer traffic | `vendor` |

### 4.2 The verification problem, stated with the evidence I could and could not get

**What I could get.** Cognition documents its own reward hacking in plain terms: with smaller
models the system encountered models that *"simply copies the PyTorch reference
implementation"*, used try-except fallbacks, or *"inherit[ed] from the reference
implementation"*; the fix was *"stricter format checks"*, assigning *"a reward of 0 to responses
that use PyTorch functions or that do not contain CUDA kernels"*
[src](https://cognition.com/blog/kevin-32b). And an entire 2025 paper exists whose stated
motivation is that *"existing kernel generation benchmarks suffer from exploitable loopholes"*,
introducing **robust-kbench** to fix them [src](https://arxiv.org/abs/2509.14279).

**What I could not get, and will not paraphrase.** Sakana AI's *The AI CUDA Engineer* — its
original speedup claims and the subsequent correction about the agent exploiting the evaluation
harness — is the case most often cited for this failure mode. **As of 2026-09-19,
`https://sakana.ai/ai-cuda-engineer/` serves a page that redirects to
`https://arxiv.org/abs/2509.14279`** (verified by curl: the page body contains the robust-kbench
abstract and `window.location.href = "https://arxiv.org/abs/2509.14279"`). **The original claims
and the errata text are not retrievable at that URL in this session, so no number from that
episode is quoted here.** ⚠️ Q13. What *can* be said from fetched sources: the same team's
current published work is a benchmark built specifically to close evaluation loopholes — which
is consistent with the episode having happened, and is the more useful fact anyway.

**The general law, which all six sources above agree on from different directions:**

> **An agentic optimizer is exactly as trustworthy as its verifier, and a performance verifier
> is much easier to fool than a correctness verifier.** ADRS's whole thesis is that systems
> problems are tractable *because* they *"naturally admit reliable verifiers"*
> [src](https://arxiv.org/abs/2510.06189); AlphaEvolve's deployed wins are all in domains with
> mechanical evaluators [src](https://deepmind.google/...); KernelBench's low scores and
> robust-kbench's existence are what happens when the verifier is weak.

### 4.3 What is credible in 2026, tiered

| Tier | Activity | Credible? | Verifier strength | Recommendation for this platform |
|---|---|---|---|---|
| **1** | **Search over a catalog of known-good configs** (§3) | **Yes** — this is just benchmarking | Very strong: measured latency + measured accuracy gate | **Build. Automate fully.** This is 90 % of the value at 1 % of the risk |
| **2** | **Automated choice of quantization format and per-layer precision** | **Yes** — AutoQuantize is a shipped ILP with published methodology [src](https://nvidia.github.io/Model-Optimizer/announcements/autoquantize.html) | Strong: eval gate | **Adopt the vendor tool.** Do not write your own sensitivity scorer |
| **3** | **Automated draft-head training and refresh from traffic** | **Yes** — shipped by two vendors (ATLAS, FireOptimizer) | Strong: acceptance rate is directly measurable, and mispredictions cost speed, not correctness | **Build.** This is the loop's most natural inference-side win (§2.8) |
| **4** | **Agentic generation of new kernels** (Triton/CUDA) | **Partially** — 63 % correctness / 2.59× on Triton-MI300X [src](https://arxiv.org/abs/2507.23194); *"less than 20%"* beat PyTorch in the open benchmark [src](https://arxiv.org/abs/2502.10517) | **Weak without deliberate hardening** — see §4.2 | **Do not build.** Consume kernels from engines; revisit only if a customer's workload is bottlenecked on an op no engine optimizes |
| **5** | **Agentic discovery of scheduling/serving algorithms** | **Emerging** — ADRS reports *"up to 5.0x"* including a MoE-inference case [src](https://arxiv.org/abs/2510.06189) | Strong *if* the simulator is faithful | **Watch.** A research bet, not a product path |
| **6** | **Agentic architecture search on the student** | No published result at this platform's scale | Weak and expensive | **Do not build** |

### 4.4 Where human review is mandatory, regardless of tier

1. **Any change to a numerics-affecting flag that the gate cannot see.** If the eval suite has no
   slice covering a behaviour, an optimizer is free to destroy it. Human review is the
   compensating control for gate coverage.
2. **Any promotion.** `00` I7 and doc 07 own this; the optimizer's output is a *candidate*, never
   a deployment.
3. **Any result that beats the incumbent by more than the search was designed to find.** A 10×
   surprise is a bug 95 % of the time — this is the operational form of §4.2, and it should be an
   automated alert, not a culture.
4. **Any custom kernel, ever** (tier 4), with independent numerical comparison against the
   reference on adversarial shapes, not just the benchmark shapes.
5. **Any config the sweep chose that differs from the documented recipe for that model+GPU.**
   The repo's per-pair documents encode a lot of hard-won knowledge about what silently breaks;
   a sweep that contradicts them is either a discovery or an artifact of a broken measurement,
   and a human should decide which.

### 4.5 The concrete design: a bounded auto-research loop

**Mechanism.** A scheduled job per (customer, artifact) that:
1. reads the last N days of S2 traces and recomputes the traffic profile (§1.2);
2. detects drift from the profile the current config was tuned for (ISL/OSL distribution,
   cache hit rate, concurrency, acceptance rate);
3. if drift exceeds a threshold, re-runs **stages 3–5 of §3.5** over the *existing* catalog of
   feasible configs, plus a draft-head refresh if acceptance has decayed;
4. runs Tier A screens, then the Tier B exit gate;
5. **opens a promotion request** with the benchmark report and gate report attached — it does
   not promote.

**Inputs:** trace snapshot, current artifact, frozen eval suite, budget cap.
**Outputs:** either "no change" (the common case, and it should be logged as a result, not
silence), or a candidate artifact + two reports + a diff of what changed and why.
**Cost:** §1.6's sweep cost per run, plus the gate. At a monthly cadence and $120–$300/sweep,
this is **$1.5k–$4k/customer/year of GPU time** `est.` — trivially amortised against the savings
in `00` §4.3 for any customer worth serving.
**Failure modes:** (a) **overfitting to a traffic snapshot** — mitigate by requiring the new
config to beat the old on the *previous* snapshot too; (b) **eval erosion** — the suite is frozen
and owned by doc 04, and every promotion must re-run it from the registry, not from a cache;
(c) **churn** — require a minimum improvement (e.g. ≥5 % blended cost or ≥10 % p99 latency) to
open a request at all, or the platform will generate a promotion request every month for noise;
(d) **acceptance-rate collapse after a customer prompt change** — which is the same silent
distribution shift `00` §2.2 describes, now with a *cost* symptom rather than a quality one.

### 4.6 Sequencing

`00` is blunt: *"Do not build 09 first … an auto-research loop over an unvalidated eval is a
machine for overfitting"*, and in "What to avoid": *"Building the auto-research loop before the
eval is trusted."* This document agrees and adds a narrower, earlier exception: **tier-1 and
tier-3 automation (config search and draft-head refresh) are safe before the eval is fully
trusted, because their objectives — latency, cost, acceptance rate — are *mechanically
measurable* and do not route through a judge.** Automate those on day one. Everything that
optimizes against a judged score waits for doc 04.

---

## 5. Artifact and version management

### 5.1 What an artifact is

One immutable, content-addressed bundle. Anything not in it is a variable that will drift.

```
artifact/
  manifest.json          # schema below
  weights/               # one format build; multiple builds = multiple artifacts, linked
  tokenizer/ + chat_template
  generation_config.json
  prompt_stack/          # system prompts, tool schemas, response schema  (00 §2.2)
  speculation/           # draft head weights + gamma + measured acceptance
  adapters/              # LoRA, if not merged
  serving_config.yaml    # engine + version + container digest + every flag
  reports/
    benchmark.yaml       # §3.7
    gate.yaml            # tier A screens + tier B result + safety
  provenance.json        # lineage, calibration hash, tool versions
  signature              # §5.5
```

**The two entries people leave out are the two that cause the incidents:** `prompt_stack/` (a
customer prompt edit silently invalidates parity — `00` §2.2) and the **container digest** in
`serving_config.yaml` (an engine upgrade changes kernels, defaults and compile-cache validity —
[`scaling/06` §3.1, §6.4](../scaling/06-cold-start.md)).

### 5.2 Registry options

| Option | Strengths | Weaknesses | Verdict for this platform |
|---|---|---|---|
| **MLflow Model Registry** [src](https://mlflow.org/docs/latest/ml/model-registry/) | *"a centralized model store, set of APIs and a UI … to collaboratively manage the full lifecycle"*; monotonic versions (`models:/MyModel/1`); **aliases** — *"a mutable, named reference to a particular version"* (e.g. `champion`); tags; *"Each registered model version is linked to the MLflow run, logged model or notebook that produced it, enabling full reproducibility"* | *"The provided content contains no information about model signing or attestation"* [ibid.] — signing must be layered on (§5.5). Not built for multi-GB artifact distribution | **Good fit for lineage and the `champion`/`dev` alias pattern**, which maps directly onto main/dev endpoints. Pair with an object store for bytes |
| **W&B Registry** [src](https://docs.wandb.ai/guides/registry/) | *"a curated central repository of W&B Artifact versions within your organization"*; `v0`-indexed versions; collections; linking is a pointer — *"W&B does not duplicate artifacts when you link"*; *"Track an artifact's lineage and audit the history of changes"*; *"Automate downstream processes such as model CI/CD"*; permission-based access per registry | Ecosystem lock-in (`00` §8.6's lesson); automation/webhook mechanics not detailed on the page fetched ⚠️ | Strong if the training side already runs on W&B. The **automation-on-alias-change** hook is the right trigger shape for promotion |
| **Hugging Face Hub, private repos** | Git-based versioning with commit pinning; **Xet** storage — *"a modern custom storage system built specifically for AI/ML development … enables chunk-level deduplication, smaller uploads, and faster downloads than Git LFS"* [src](https://huggingface.co/docs/hub/en/storage-backends) | Third-party custody of customer-derived weights — a doc 08 problem, not a doc 05 one | **Use for public base models; do not use for customer-derived checkpoints** unless the tenant's policy explicitly allows it |
| **OCI artifacts — ModelPack + KitOps** | ModelPack: *"a vendor-neutral, open source specification standard to package, distribute and run AI models in a cloud native environments"*, *"based on the current OCI image specification"*, under **CNCF** [src](https://github.com/modelpack/model-spec). KitOps is *"the reference implementation of ModelPack"*, packaging *"models, datasets, code, agent skills, MCP servers, guardrail configs, and policies into a single versioned OCI artifact"*, working with *"Amazon ECR, Azure Container Registry, Docker Hub, GitHub Packages, GitLab Container Registry, Google Artifact Registry, Harbor, JFrog Artifactory, Quay.io"*, Apache-2.0, with *"Sign and verify … with the same Cosign workflow used for container images"* [src](https://kitops.org/) | Younger ecosystem; ModelPack *"only contains part of the model metadata, and handles model artifacts as opaque binaries"* [src](https://github.com/modelpack/model-spec) | **This is the right substrate for the *serving* artifact.** The artifact already travels next to a container digest; putting both in the same registry, with the same signing workflow and the same pull path onto a node, removes an entire class of drift |

**Recommendation.** Two stores, one truth:
**OCI/ModelPack for the bytes and the serving artifact** (because it co-locates with the engine
image and gives Cosign signing for free), **plus a lightweight metadata registry — MLflow is
sufficient — for lineage, aliases and the promotion state machine.** Record the OCI digest in
the registry; never let the registry be the only copy of anything. Given `00` §8.6's three
sunsets in twelve months, **both layers must be open and self-hostable**, and the artifact must
be exportable as plain files on demand.

### 5.3 Lineage

Every artifact carries a DAG of typed edges back to a base model. The edges are not decoration:
doc 07 needs them to answer "what changed between the version that worked and the one that
didn't", and doc 08 needs them to answer "whose data, under whose teacher agreement".

```
base:Qwen3.8-27B@<digest>
   └─(sft, dataset=D1@<hash>, run=<id>)──────────► ckpt:sft-1
        └─(dpo, prefs=P1@<hash>, run=<id>)───────► ckpt:dpo-1      ◄── S7 gate PASSES here
             ├─(ptq, tool=llm-compressor@0.13.0,  ► art:nvfp4-1
             │       scheme=NVFP4, calib=<hash>)
             │     └─(qad, run=<id>)──────────────► art:nvfp4-qad-1
             │           └─(draft, spec=eagle3,    ► art:nvfp4-qad-e3-1
             │                   trainer=SpecForge@0.3.0)
             │                 └─(build, engine=vllm@0.29.0@<container digest>,
             │                          flags=[...])──────────────► serving:b300-1
             └─(ptq, scheme=FP8)────────────────► art:fp8-1  ...
```

**Required edge attributes:** tool + exact version, input dataset/calibration hash, random seed,
hardware the step ran on, and the gate report produced at that node. **A node without a gate
report is not promotable**, which is the mechanical enforcement of §1.1.

### 5.4 Reproducibility — what must be pinned

The repo itself is the argument for this list; every item below is somewhere in the tree as a
thing that changed behaviour:

| Pin | Why, with the repo's own evidence |
|---|---|
| **Engine version + container digest** | SGLang's daemon fingerprint includes *"torch version and device capability"*, so an engine upgrade forces a full disk reload ([`scaling/06` §6.4](../scaling/06-cold-start.md)). Engines also change *defaults*: vLLM 0.29 deprecated `vllm.entrypoints.openai.api_server` in favour of `vllm.entrypoints.launchers` ([`scaling/02` §2](../scaling/02-serving-stack-and-routing.md)) |
| **Model-specific images, where they exist** | NVIDIA's DeepSeek-V4.1 card ships `lmsysorg/sglang:dev-cu13-dsv41` pinned to commit `da64c5cb…` ([`gpus/b300.md`](../gpus/b300.md)) — bleeding-edge models need bespoke builds, so "SGLang 0.5.20" is not a pin |
| **Every numerics-affecting flag, verbatim** | `--kv-cache-dtype`, `--attention-backend`, `--mamba-ssm-dtype`, `--block-size`, speculative config — the Qwen3.8-27B/B300 §2 table shows each of these changing which kernel executes |
| **Calibration set hash** | PTQ is data-dependent; a different calibration sample is a different model |
| **Seeds and sampling defaults** | `00` §2.3: *"Eval scores shift for reasons unrelated to the model"* |
| **Quantizer version** | llm-compressor 0.13.0, ModelOpt `<ver>`, GPTQModel 7.5.0 — algorithms change between releases |
| **A numerical fingerprint** | Beyond digests: log the per-token KL probe from §2.10 as a value in `provenance.json`. It is the only pin that catches an *unintended* change |

### 5.5 Signing and provenance

**Mechanism.** `sigstore/model-transparency`'s `model_signing` library *"demonstrates how to
protect the integrity of a model by signing it"*: it builds a manifest containing *"a list of
(file path, digest) pairs"*, supports **Sigstore keyless** (*"making code signatures transparent
without requiring management of cryptographic key material"*) alongside *"traditional signing
methods … public keys or signing certificates as well as PKCS #11 enabled devices"*, and stores
the signature as *"a sigstore bundle protobuf … in JSON format"* containing a DSSE envelope with
an in-toto statement; verification *"validat[es] that the signature is valid and secondly
compute[s] the model's file hashes again to compare against the signed ones"*. It is built
*"on the work with Open Source Security Foundation"* [src](https://github.com/sigstore/model-transparency).
For the OCI path, KitOps gives *"the same Cosign workflow used for container images"*
[src](https://kitops.org/).

**Why this is not security theatre in this specific product.** The platform will run **many
tenants' weights on shared infrastructure** (§6) and will **hot-load adapters at runtime** —
which vLLM itself flags as carrying *"security risks"* and unsuitable for non-isolated
environments ([`scaling/06` §6.3](../scaling/06-cold-start.md)). Signature verification at load
time is the control that makes "tenant A's adapter cannot be loaded into tenant B's replica" an
enforced property rather than a configuration convention. **Verify at load, not just at push.**

### 5.6 Promotion between dev and main, with gates

The promotion contract (doc 01 owns the endpoint mechanics, doc 07 owns the statistical
decision; S8 owns the *preconditions*):

```
PROMOTE(artifact, from=dev, to=main) requires:
  1. artifact.reports.gate.tier_b.verdict == PASS      # per-slice, at agreed δ/α/power
  2. artifact.reports.gate.safety      == PASS         # unconditional (I5)
  3. artifact.reports.benchmark.slo_check == PASS      # p50 AND p99 (I3)
  4. artifact.reports.benchmark.cost_per_1m.blended
        < incumbent_blended_batched_cached              # 00 §4.4's honest baseline (I2)
  5. artifact.provenance.signature verifies
  6. contract conformance == 100%                       # (I4)
  7. prompt_stack_hash == the hash currently in production
  8. rollback target exists and has been drilled        # (I7)
```

**Condition 7 is the one that will be argued about**, and it is the mechanical form of `00` §2.2:
if the customer edited their prompt after the gate ran, the parity claim is void. The platform
should *detect* it (prompt hash in every trace) and *block* the promotion, then say so. `00`
lists customer acceptance of this mechanic as an open product question; S8's job is only to make
the check possible.

**Rollback** is the cheap direction and must stay cheap: an alias flip in the registry plus an
endpoint re-point, under 60 seconds (`00` §9.1 criterion 9). Because rollback targets a
*previously-served* artifact whose weights may already be on the node, it is frequently just a
weight hot-swap or adapter swap ([`scaling/06` §6.3](../scaling/06-cold-start.md)) rather than a
cold start — which is exactly why the previous artifact must not be garbage-collected on
promotion (§5.8).

### 5.7 Weight distribution

Out of scope here by construction — [`scaling/06-cold-start.md`](../scaling/06-cold-start.md)
owns it end to end: §2 weight-loading fast paths and the `--load-format` matrix, §2.4 P2P
weights from a peer, §2.6 object-store and shared-filesystem paths, §2.7 **pre-quantized vs
on-load** (directly relevant: shipping a pre-quantized artifact moves quantization off the
critical path), §5.3 pre-staged weights on every node's NVMe, and §6 rolling updates and model
version swaps. Two S8-side implications only:

1. **Always ship pre-quantized.** On-load quantization makes every cold start pay the quantizer.
2. **Artifact size is a cold-start SLO input.** The NVFP4 example's **66 GB → 22 GB**
   [src](https://developer.nvidia.com/blog/developing-nemotron-3-5-lightning-nvfp4-with-qad-using-nvidia-model-optimizer/)
   is a 3× cut in the term `scaling/06` §2.1 calls the physical ceiling. Quantization is a
   cold-start optimization as much as a throughput one, and for scale-to-zero tenants (§6.4)
   that may be its *dominant* benefit.

### 5.8 Retention

Per (customer, task) keep: the current `main` artifact, the previous `main` (the rollback
target), the current `dev`, and every artifact referenced by an open promotion request or a
historical parity claim the customer was shown. Everything else is garbage after a stated
window. **Do not GC an artifact that any published report cites** — `00` §8.4's contamination
discipline has an artifact-side analogue: a claim whose artifact no longer exists is not a claim.
Storage is cheap relative to a GPU-hour; Tinker's published checkpoint storage rate of
**$0.10/GB-month** ([`00` §4.3b](./00-goal-and-problem-statement.md), citing
[Tinker docs](https://tinker-docs.thinkingmachines.ai/tinker/models/)) makes a 22 GB artifact
**$2.20/month** — i.e. retention is never the reason to delete.

---

## 6. Multi-tenant cost

### 6.1 The problem

`00` §4.5 states the load-bearing fact: **the platform's cost of goods is dominated by idle GPU,
not by tokens**, and calls multi-tenant adapter serving *"the single highest-leverage
architecture decision in the whole platform, because it turns N customers' idle GPUs into one
busy one."* This section is the S8-side implementation of that decision. The economics are
owned by [`scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md) and
[`scaling/04-throughput-and-utilization.md`](../scaling/04-throughput-and-utilization.md); what
follows is only what changes when the *optimizer* is the thing producing the artifacts.

The combinatorial shape: **N customers × M live versions each (main + dev + candidates) × K
format builds per version.** At N=50, M=3, K=1 that is 150 artifacts. If each needs a dedicated
replica the platform is buying 150 GPUs at ≥$7.3k/month each (`00` §4.5) to serve traffic that
may average a few requests per second per tenant.

### 6.2 LoRA multiplexing

**The mechanism and its measured ceiling** (research; do not re-derive):

- **S-LoRA**: thousands of adapters on shared base weights via Unified Paging, *"improve the
  throughput by up to 4 times"* over HF PEFT and naive-LoRA vLLM
  [src](https://arxiv.org/abs/2311.03285).
- **Punica**: the SGMV kernel batching across different adapters, *"12x higher throughput in
  serving multiple LoRA models compared to state-of-the-art LLM serving systems while only
  adding 2ms latency per token"* [src](https://arxiv.org/abs/2310.18547).
- **LoRAX** (the productized descendant): *"scales to 1000s of fine-tuned LLMs"*, adapters
  *"loaded just-in-time without blocking concurrent requests"*, *"heterogeneous continuous
  batching"* that *"packs requests for different adapters together into the same batch, keeping
  latency and throughput nearly constant"*, with *"pre-compiled CUDA kernels (flash-attention,
  paged attention, SGMV)"* [src](https://github.com/predibase/lorax). ⚠️ The page fetched shows
  no archival or deprecation notice and no last-release date — and `00` Open Question 4 records
  that **predibase.com now redirects to rubrik.com**, so LoRAX's maintenance status is
  genuinely uncertain. **Do not take a hard dependency on it**; vLLM's built-in LoRA support
  (§2.9) is the safer base.

**Decision rule — when does a tenant get an adapter instead of a replica?**

```
dedicated replica is justified when:
    tenant_tokens_per_month × blended_saving_vs_shared
        > replica_cost_per_month × (1 + dev_replica_factor)
```
`00` §4.5 gives the fixed side: **≥ $7.3k/month/replica plus a dev replica**. Below that
threshold — which is most tenants — the tenant belongs on shared base weights with an adapter.
The threshold is not a constant: it moves with the packing efficiency of §6.5 and with the
`res1y` tier (§7.2 shows a 28 % swing on one GPU alone). ⚠️ `00` Open Question 18 asks for the
requests/second break-even per repo model and assigns it to this document; **it cannot be closed
here**, because the break-even depends on the shared-fleet utilisation `U` that
[`scaling/07` §1.5](../scaling/07-cost-engineering.md) defines and on the tenant mix — it is a
fleet-planning calculation, not a per-pair one. Q14.

### 6.3 What adapters must share

This is the constraint that quietly destroys packing efficiency, and it is an S8 problem because
S8 chooses all three:

**Adapters can be batched together only if they share the same base weights, the same
quantization of those weights, and the same engine version and flags.** Consequences:

| If two tenants differ in… | Can they share a replica? | Cost |
|---|---|---|
| Adapter rank (within `--max-lora-rank`) | **Yes** | `--max-lora-rank` is a **server-start** decision and setting it too high *"wastes memory and can cause performance issues"* ([vLLM docs](https://docs.vllm.ai/en/latest/features/lora.html), via [`scaling/06` §6.3](../scaling/06-cold-start.md)) — so a single high-rank tenant taxes every co-resident tenant |
| Prompt stack | **Yes**, but prefix-cache hit rates diverge | The cache lever's value is per-tenant ([`cost-matrix.md` §7.2](../matrix/cost-matrix.md)) |
| Base model version | **No** | Separate replica |
| Quantization format | **No** | Separate replica — and this is why the format decision is a *fleet* decision, not a per-customer one |
| Engine version | **No** | Separate replica, and a rolling-upgrade problem ([`scaling/06` §6](../scaling/06-cold-start.md)) |
| Isolation class (doc 08) | **No**, by policy | Separate replica regardless of technical feasibility |

**The platform design conclusion: standardise hard on a small number of (base, format, engine
version) triples — "serving pools" — and route tenants into them.** A platform that lets every
customer pick their own format has no multi-tenancy at all, only a naming convention over
dedicated replicas. This is a *product* constraint that S8's automation must enforce: the
config search runs **within** a pool's triple, and changing the triple is a fleet migration
event, not a per-tenant optimization.

### 6.4 Cold-start budgets

Shared pools make scale-to-zero viable for small tenants, and the cost of that is cold start —
[`scaling/06`](../scaling/06-cold-start.md) §4 (snapshot/restore, vLLM sleep mode), §5 (warm
pools, pre-pulled images, pre-staged weights, predictive pre-warm) and
[`scaling/07` §2.5](../scaling/07-cost-engineering.md) (scale-to-zero for small models) own the
analysis. S8's contribution: **an adapter hot-load is not a cold start.** Adding a tenant to a
warm pool is a `POST /v1/load_lora_adapter` ([`scaling/06` §6.3](../scaling/06-cold-start.md)),
which is why the adapter path, not the replica path, is what makes a long tail of small tenants
economic at all.

### 6.5 GPU packing

Owned by [`scaling/04`](../scaling/04-throughput-and-utilization.md) and
[`scaling/07` §2](../scaling/07-cost-engineering.md). One S8-specific fact worth carrying
forward, because it is counter-intuitive and appears in both repo students:
**for small models, replicate rather than shard.** Qwen3.8-27B on B300: *"The right node shape
is TP1 × 8 independent replicas per node (data parallel), not TP8 — TP only buys KV budget, and
past TP4 it stops buying even that because the model has only 4 KV heads"*
([`models/qwen3827b/b300.md` §0](../models/qwen3827b/b300.md)). Marlin-2B on RTX PRO 6000:
*"Scale by replicas (DP), never TP — this card has no NVLink and TP over PCIe Gen5 collapses
(measured 6–7 tok/s at TP=4 vs 46–49 at TP2+PP2)"*
([`models/marlin2b/rtx6000-pro.md` §0](../models/marlin2b/rtx6000-pro.md)). **DP replicas are
also the natural unit of multi-tenancy**: one replica per serving pool per node, adapters
multiplexed within it.

### 6.6 Failure modes

| Failure | Mechanism | Mitigation |
|---|---|---|
| **Noisy neighbour** | One tenant's long-context requests consume the shared KV pool; others' TTFT rises | Per-tenant admission control ([`scaling/03`](../scaling/03-concurrency-and-admission-control.md)); per-tenant SLO accounting, not per-replica |
| **Rank inflation** | One tenant needs rank 128; `--max-lora-rank` is server-wide and start-time | Pool tenants by rank band; treat a rank bump as a pool migration |
| **Adapter sprawl** | Every candidate version leaves an adapter behind | §5.8 retention, enforced by the registry |
| **Cross-tenant leakage via a shared base** | An adapter loaded into the wrong pool | §5.5 signature verification at load; doc 08 owns the policy |
| **Shared-pool upgrade risk** | An engine upgrade moves every tenant at once, and engine upgrades change numerics (§5.4) | Blue/green the *pool* ([`scaling/06` §6.1](../scaling/06-cold-start.md)); re-run each tenant's Tier A screen against the new pool **before** cutting traffic. This is expensive and it is the real cost of multi-tenancy |

**The last row is the honest cost of §6 and it is usually omitted from the pitch:** sharing a
serving pool means sharing an upgrade cadence, and every upgrade is a numerics change that every
co-resident tenant's gate must re-clear. Budget the screens (§2.10 tier A, ~$7–$22 each) × N
tenants per upgrade. At N=50 that is ~$350–$1,100 of GPU time per pool upgrade `est.` — cheap,
but only if the screens are automated. If they are manual, multi-tenancy does not work.

---

## 7. Worked example

Numbers below are **named rows from this repo**, not new derivations. Every `$` figure is
`est.`, at the confidence the source row states (both example pairs are `estimate`, i.e.
roofline, with zero published measurements of these models on this hardware).

### 7.1 Distilled Qwen3.8-27B on 1 × B300

**Starting point.** S7 has passed on a BF16 SFT+DPO checkpoint of Qwen3.8-27B specialised to the
customer's text task. Target: 1 × B300 (the repo's primary node, TP1, DP-replicated). SLO: TPOT
≤ 50 ms at the customer's concurrency.

| Step | What happens | Where it lands | What the gate must check |
|---|---|---|---|
| **0. Baseline** | Serve BF16, measure | `est.` from [`models/qwen3827b/b300.md`](../models/qwen3827b/b300.md) §3 | Reference for every KL probe (§2.10) |
| **1. Format choice** | The GPU is `sm_103`, so the menu is NVFP4 / FP8 / INT4-W4A16; **INT8 is actively harmful** — B300 INT8 is *"187.5 TOPS dense = 0.042× B200, ~0.08× its own BF16 rate — slower than BF16"*; INT4 W4A16 is *"strictly worse than NVFP4 on this GPU"* (no compute win) ([`models/qwen3827b/b300.md` §2](../models/qwen3827b/b300.md)) | The decision is made by lookup, not by sweep | — |
| **2. PTQ to NVFP4** | llm-compressor 0.13.0 or ModelOpt; **add FP8 to the AutoQuantize menu** (§2.2) and use **Local-Hessian scales** (2 pp free, §2.2) | The published mixed build executes NVFP4 on the 64 MLPs + `lm_head` and FP8 on the 16 attention + 48 GDN projections — **71.7 % of GEMM FLOPs reach the FP4 pipes** ([ibid. §0](../models/qwen3827b/b300.md)) | Tier A screen: contract + KL probe. **This is W4A4 on the MLPs, so expect a 3–5 pp problem before recovery** (§2.3) |
| **3. QAD if needed** | Only if PTQ recovery lands in the 95–97 % band (§2.4) | ~400 iterations, 2 nodes × 8 GPUs `vendor` | Tier A screen + safety re-run |
| **4. KV dtype** | **The fork.** `--kv-cache-dtype fp8` takes 8K concurrency **231 → 325 at TP1 (+41 %)** but costs FA4 (vLLM silently uses FA2) ([ibid. §2](../models/qwen3827b/b300.md)) | Decide against the measured p99 context length, not the mean | Tier A |
| **5. Attention backend** | `triton` is *"the safe SM103 choice for a hybrid-GDN model"*; FlashInfer is **unsupported** for hybrid-GDN on SM100/SM103; `trtllm_mha` has an open SM103 hang at high concurrency ([ibid. §2](../models/qwen3827b/b300.md)) | Lookup, then verify the backend actually selected (§3.8) | Tier A |
| **6. Draft head** | The in-checkpoint MTP head (0.425 B, γ=3) is **stale after distillation** (§2.8). Retrain MTP or an EAGLE-3 head on the student, with SpecForge or ModelOpt, **against the NVFP4 target** (§1.4 rule 1) | Base-model reference points: acceptance **92.2 % BF16 / 84.8 % FP8** on GB300 TP4; mean accepted length **4.28**; DFlash2 best published at **4.80** ([ibid. §2](../models/qwen3827b/b300.md)) | Tier A **plus** acceptance measured on the customer's traces — never a recipe constant |
| **7. Config sweep** | §3.5 stages 3–5. Knobs that matter here: `max_num_seqs`, `--chunked-prefill-size` (the GDN FlashInfer prefill fast path requires it **∈ [1, 8192]**), CUDA-graph capture ≥ `max_num_seqs × (1+γ)`, `--mm-encoder-tp-mode data`, and `--language-model-only` for text-only traffic (**+49 % KV pool measured at 32K on a 5090**) ([ibid. §2](../models/qwen3827b/b300.md)) | $118–$296 of GPU time (§1.6) | — |
| **8. Exit gate** | Full suite through the vLLM backend of lm-eval, at the served config | — | Per-slice, δ/α/power, safety unconditional |

**Where the cost lands** — named rows from [`cost-matrix.md`](../matrix/cost-matrix.md), `low`
tier, **not re-derived**:

| Row | Value |
|---|---|
| Interactive (S1, TPOT ≤ 50 ms), $/1M **output** | **$0.1649–$0.3343** ([`qwen3827b/b300`](../models/qwen3827b/b300.md)) |
| Max throughput (S4), $/1M output | **$0.1527–$0.3095** |
| **Blended** $/1M | **$0.0602–$0.1221** |
| Blended at `res1y` | **$0.06459** — and note the ranking flip: at `res1y` **B200 ($0.0538) beats B300**, because B300 reserved costs *more* than on-demand (+7.3 %) while B200 gets a 15 % cut ([`cost-matrix.md` §7.4](../matrix/cost-matrix.md)) |
| Prefix-cache sensitivity (h = 0 / 50 / 90 %) | **$0.0757 / $0.0602 / $0.0478**; output is **68 %** of the blend at h=50 % ([`cost-matrix.md` §7.2](../matrix/cost-matrix.md)) |
| ±20 % MBU | ×1.25 / ×0.833 on every cost figure ([`cost-matrix.md` §7.1](../matrix/cost-matrix.md)) |

**The three lessons this example teaches the platform:**

1. **Most of S8 is lookup, not search.** Steps 1, 4 and 5 are answered by the repo's support
   matrices before any GPU is booked. An S8 implementation that sweeps blindly over formats and
   backends will spend real money rediscovering that B300 INT8 is slower than BF16.
2. **The biggest single cost lever here is not the format.** It is the operating point and the
   cache hit rate: h=0 %→90 % moves blended **$0.0757 → $0.0478 (−37 %)**, and the S1→S4 spread
   is another ~8 %. Both are *config*, not *model*.
3. **The price tier can invert the hardware choice.** `res1y` moves the cheapest blended GPU for
   this model from B300 to B200. The optimizer must take the customer's actual commitment tier
   as an input (§1.2), or it will optimize for a price the customer isn't paying.

### 7.2 Marlin-2B (video VLM) on 1 × RTX PRO 6000 vs 1 × B300

**The point of this example is that almost nothing in §2 applies**, and recognising that quickly
is worth more than executing a pipeline.

| Step | Verdict for Marlin-2B |
|---|---|
| **Quantization** | **Nothing to do.** *"Weight format actually executed: BF16"* — no FP8, NVFP4 or MXFP4 Marlin checkpoint exists, so *"B300's entire FP4 differentiator is unused by this pair"* ([`models/marlin2b/b300.md` §0](../models/marlin2b/b300.md)). The one 4-bit option (`prasannaJagadesh/marlin-2B-GPTQ-4BITS`) is W4A16 — BF16 math rate — *"and it trips a live vLLM GDN kernel bug"* [ibid.]. **Creating an NVFP4 Marlin build is a real S8 opportunity** (§"Open questions" Q15), but it does not exist today |
| **KV quantization** | Marginal: only **6 of 24 layers** have KV at all, and the **18.63 MiB/sequence GDN state** does not shrink ([`models/marlin2b/rtx6000-pro.md` §1.1](../models/marlin2b/rtx6000-pro.md)) |
| **Speculative decoding** | **Impossible and pointless.** Zero `mtp` tensors ship; no EAGLE head exists; and *"every scenario is prefill-bound, and speculation cannot touch prefill"* ([`gpu-optimizations.md` §7](../matrix/gpu-optimizations.md)) |
| **Parallelism** | TP1, scale by DP replicas on both cards (§6.5) |
| **Prefix caching** | Hit rate ≈ **0 %** for video; the blended formula's cached term is *"charged but never earned"*, making the realistic blend **+32 %** ([`cost-matrix.md` §7.2](../matrix/cost-matrix.md), citing [`models/marlin2b/gb300.md`](../models/marlin2b/gb300.md)). **Report video costs on the uncached basis** |
| **Engine** | The checkpoint's `architectures` string is unregistered; vLLM ≥0.29.0 serves it only with `--hf-overrides '{"architectures": ["Qwen3_5ForConditionalGeneration"]}'`, *"documented API but ⚠️ never validated end-to-end on any GPU"* ([`models/marlin2b/rtx6000-pro.md` §0](../models/marlin2b/rtx6000-pro.md)). **The first S8 task for this model is not optimization, it is making it load reproducibly** |

**So the entire optimization decision collapses to hardware selection and the price tier.** Named
rows, `low` tier:

| | **1 × B300** | **1 × RTX PRO 6000** |
|---|---|---|
| Interactive (S1) $/1M out | **$0.022–$0.0447** | **$0.0372–$0.0857** |
| Max throughput (S4) $/1M out | same point (SLO never binds) | **$0.0315–$0.0724** (batch 1,019 = KV ceiling) |
| **Blended** $/1M | **$0.0092–$0.0185** | **$0.0135–$0.0311** |
| **Blended at `res1y`** | **$0.00987** | **$0.00975** |
| Video framing | **$0.000487 per 2-minute clip** at `low` | **$0.00059–$0.00135 per 2-minute clip**; **$0.00029–$0.00067 per video-minute** |
| $/GPU-hr (`low` / `high` / `res1y`) | $7.40 / $15.00 / $7.94 | $1.80 / $4.143 / $1.30 |

Sources: [`models/marlin2b/b300.md` §0](../models/marlin2b/b300.md),
[`models/marlin2b/rtx6000-pro.md` §0](../models/marlin2b/rtx6000-pro.md),
[`cost-matrix.md` §2–§4, §7.4](../matrix/cost-matrix.md),
[`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md).

**The flip, and the arithmetic behind it.** Cost is linear in $/GPU-hour
([`cost-matrix.md` §7.4](../matrix/cost-matrix.md)), so the `res1y` multipliers are
**RTX PRO 6000 0.722** and **B300 1.073**. Applying them to the per-clip figures (`est.`):

```
B300  per 2-min clip:  low $0.000487  →  res1y $0.000487 × 1.073 = $0.000523
RTX   per 2-min clip:  low $0.000590  →  res1y $0.000590 × 0.722 = $0.000426
                                          RTX/B300 at res1y = 0.815  (RTX 18.5% cheaper)
                                          RTX/B300 at low   = 1.211  (RTX 21.1% dearer)
```

The same scaling reproduces the published `res1y` blended rows exactly
(`$0.0092 × 1.073 = $0.00987` ✓, `$0.0135 × 0.722 = $0.00975` ✓), which is the check that the
per-clip scaling is legitimate.

**Decision rule that falls out: for a BF16-only small VLM, the hardware choice is a
*procurement* decision, not an engineering one.** On-demand, B300 wins by ~21 %. On a one-year
commitment, RTX PRO 6000 wins by ~19 %. At 1M two-minute clips/month the whole argument is
**$487 vs $590 vs $426** — a spread of $164/month `est.`, which is **less than 3 % of one B300
replica-month** (`00` §4.5: ≥$7.3k). **Neither GPU choice matters next to keeping the replica
busy**, which sends the reader straight to [`scaling/07`](../scaling/07-cost-engineering.md) and
to §6 of this document. That is the correct conclusion and the optimizer should be able to reach
it without a sweep.

### 7.3 The report the customer sees

One page, three blocks, produced automatically from §3.7's YAML:

```
CANDIDATE  qwen3827b-cust42-v7        vs  INCUMBENT  Claude Opus 5 (batched, cached)
GPU 1×B300 · vLLM 0.29.0@<digest> · NVFP4-mixed · FP8 KV · triton · MTP γ=3 (acc 0.77 meas.)

QUALITY        per-slice, frozen suite v3, judge=<model≠teacher>, judge noise floor ±2.1pp
  overall      −0.4 pp   [95% one-sided UB: +1.2 pp]   δ=2.0 pp   PASS
  slice/hard   −1.6 pp   [UB: +1.9 pp]                               PASS
  slice/tools  +0.3 pp                                               PASS
  safety       no regression                                         PASS (unconditional)
  contract     100.0% schema · 100.0% tool encoding · streaming OK   PASS

LATENCY        at c=256, customer traffic replay (ISL p99 18,432 / OSL p99 2,048)
  TTFT  p50 ___ ms   p99 ___ ms      SLO ___ / ___    PASS
  TPOT  p50 ___ ms   p99 ___ ms      SLO ___ / ___    PASS

COST           blended $/1M, METHODOLOGY §6, customer price tier = res1y
  candidate    $0.0646        incumbent (batched+cached) $4.16      ratio 64×
  one-time     annotation $__ + training $__ + eval $__ = $__  → break-even at __ requests

REPRODUCE      <command>          ARTIFACT  <oci ref>@sha256:...   SIGNED ✓
```

**The incumbent column must be the batched, cached, tier-downed price** (`00` §4.4), not list.
A report that quotes list price is a report that will be caught.

---

## Implications for the platform

### What to build

1. **The config-search service** — the gap §3.3 identifies. Nothing open takes
   {checkpoint, GPU, SLO, traffic profile} and returns a ranked, gated, reproducible serving
   config across format × KV × backend × batch × speculation. Build it as **stage-0 lookup
   against this repo's support matrices, then stages 3–5 of §3.5 on real hardware.** It is a few
   hundred dollars of GPU time per run and it is the highest-confidence automation in this whole
   document.
2. **Gate-at-each-rung, with the cheap screen** (§2.10) — contract conformance, a fixed probe,
   and **per-token KL against the retained BF16 reference**. It is nearly free, it bisects
   failures, and nobody else runs it.
3. **Per-customer draft-head training and refresh** (§2.8). Two vendors ship this as a product
   (ATLAS, FireOptimizer), which is validation, not a reason to buy — the draft head must be
   trained against *this platform's* distilled student, at *this platform's* quantization, on
   *this customer's* traffic, and acceptance must be monitored as an SLI thereafter.
4. **The artifact contract** (§5.1) — weights + prompt stack + serving config + container digest
   + both reports + signature, as one immutable, signed, content-addressed bundle, with
   promotion refusing anything incomplete (§5.6).
5. **Serving pools** (§6.3) — a small, enforced set of (base, format, engine version) triples
   that tenants are routed into, with adapters multiplexed within a pool. This is the decision
   that makes multi-tenancy real rather than nominal, and it must be made before the first
   customer, because retrofitting it means migrating everyone.
6. **The bounded auto-research loop** (§4.5) — drift detection → re-sweep → screens → gate →
   *promotion request*. Never auto-promote.

### What to buy / adopt

- **Quantization:** **llm-compressor** (v0.13.0) for vLLM-targeted work,
  **ModelOpt** for NVFP4 + QAD + draft modules in one library, **GPTQModel** (v7.5.0) for
  GPTQ/AWQ lineage, **Quark** for the AMD path. **Never start on AutoAWQ** (archived 2025-05-11).
- **The format:** **`compressed-tensors`** — it is read by vLLM and SGLang and it puts the
  quantization config in `config.json` where the engine can see it.
- **The PTQ refinements:** **AutoQuantize** (ILP mixed precision; put FP8 on the menu) and
  **Local-Hessian scales** (2 pp for free at W4A4). Do not write your own sensitivity scorer.
- **Benchmarking:** **GuideLLM** for the sweep profile and Poisson arrivals, **vLLM auto_tune**
  for the two-knob grid, **SGLang bench_serving** where SGLang is the engine, **trtllm-bench**
  where TRT-LLM is. **InferenceX** as the external sanity check on whether your numbers are
  plausible for that GPU.
- **Draft heads:** **SpecForge** (v0.3.0) or ModelOpt.
- **Eval execution:** **lm-evaluation-harness**, through its vLLM/SGLang backends, so the gate
  runs on the serving stack.
- **Registry:** **OCI/ModelPack via KitOps** for the artifact bytes and Cosign signing, plus
  **MLflow** for lineage and the `champion`/`dev` alias state machine. **sigstore
  model-transparency** for model signing where Cosign is not the fit.
- **Discipline to copy, not software to buy:** **MLPerf**'s closed/open division rule — every
  result states exactly which model ran, at what quality target, under what latency constraint.

### What to avoid

- **Quantizing before you know the target GPU.** Format availability is a silicon property; the
  GPU prunes the menu. Doing it in the other order produces artifacts that cannot run.
- **Gating only at the end of S8.** Five transformations, one verdict, no diagnosis.
- **Shipping a generic draft head with a distilled student.** The measured downside is *negative*
  (29 % hit rate, 1.5× *slower* — [FireOptimizer](https://fireworks.ai/blog/fireoptimizer)), and
  a format-mismatched draft can hit ~0 % acceptance
  ([`gpu-optimizations.md` §7](../matrix/gpu-optimizations.md)).
- **Quoting a recipe's acceptance constant as a measurement.** The repo has two different 3.51s
  with opposite epistemic statuses, plus SGLang's simulated 4.5/5.5 (§2.8, corrected 2026-09-19).
- **2:4 structured sparsity**, until someone publishes a measured end-to-end serving speedup for
  a model of this shape (§2.5).
- **Pruning a published base** when a smaller published base exists (§2.5).
- **Layer-dropping a hybrid GDN model** without re-deriving its memory model (§2.6).
- **Free-form agentic kernel generation** (tier 4, §4.3). Consume kernels from engines.
- **Letting the optimizer write to its own objective.** The eval suite is owned by doc 04, frozen,
  and read-only to S8.
- **Auto-promotion.** Ever. `00` I7 and doc 07 own the decision; S8 produces candidates.
- **Per-customer format choice.** It destroys the serving pool (§6.3) and with it the
  multi-tenant economics that `00` §4.5 says the business depends on.
- **Benchmarking at an assumed 50 % cache hit rate.** Measure it; the real range in this repo is
  0 % to 99.7 %.

---

## Open questions

⚠️ Consolidated. Each names who should close it.

1. **⚠️ AMD Quark's serving path.** The documentation fetched (v0.12.post1) names MX data types
   but **does not name MI300X/MI355X, vLLM, SGLang or compressed-tensors**
   [src](https://quark.docs.amd.com/latest/). Can a Quark-produced MXFP4 checkpoint be served by
   vLLM or SGLang on MI355X today, and with what accuracy? *Owner: this doc, with search.*
2. **⚠️ 2:4 structured sparsity payoff — largely closed, negatively, 2026-09-19.** No measured
   end-to-end serving speedup for any model in this repo on any GPU in this repo, **and**
   llm-compressor v0.13.0 states *"Sparse compression (including 2of4 sparsity) is no longer
   supported … due to lack of hardware support and user interest"*
   [src](https://docs.vllm.ai/projects/llm-compressor/en/latest/) — which also explains the 404
   on the 2:4+FP8 example. What remains genuinely open is narrower: whether any *other*
   maintained producer (ModelOpt sparsity, AMD Quark) plus a serving engine gives a measured
   speedup. Sparsity stays out of the ladder. *Owner: this doc.*
3. **⚠️ Layer dropping on hybrid GDN/Mamba architectures.** ShortGPT and Minitron are transformer
   results. Dropping layers from a 6-of-24-attention stack changes KV-per-token and
   state-per-sequence. No published result found. *Owner: this doc + doc 05-training.*
4. **⚠️ Does quantization regress refusal behaviour?** `00` I5 makes safety a non-negotiable gate
   and `00` §3.3 notes refusals are under-represented in the training signal. I found **no
   published measurement of refusal-rate or jailbreak-resistance drift under PTQ or QAD.** This
   is a gap the platform will have to measure itself. *Owner: doc 08 + doc 04.*
5. **⚠️ Draft head: train against the quantized target or the BF16 target?** §1.4 rule 1 asserts
   the former on mechanical grounds, but no published ablation was found. *Owner: this doc.*
6. **⚠️ Cost of training a draft head.** SpecForge documents the pipeline; the page fetched gives
   no hardware or data scale [src](https://github.com/sgl-project/SpecForge). Without this the
   §1.6 cost table has a hole. *Owner: this doc.*
7. **⚠️ EAGLE3 (or any draft head) for a hybrid-GDN VLM.** Marlin-2B ships none and none exists.
   Also unknown whether it would help, given the model is prefill-bound. *Owner: this doc.*
8. **⚠️ Quantized base + LoRA in vLLM.** The LoRA documentation as fetched *"does not explicitly
   document quantized base model + LoRA support"* [src](https://docs.vllm.ai/en/latest/features/lora.html).
   This is load-bearing for §6: if adapters cannot ride a quantized base, multi-tenancy and
   quantization are mutually exclusive. **Verify before designing the serving pools.**
   *Owner: this doc + doc 01.*
9. **⚠️ Speculative decoding under multi-LoRA.** Acceptance rate with a hot-swapped adapter on a
   shared base is unmeasured anywhere I could fetch. If it collapses, §6's economics and §2.8's
   gains do not compose. *Owner: this doc.*
10. **⚠️ KL-to-task-delta calibration.** §2.10's cheap screen needs a threshold: how much
    per-token KL against the BF16 reference predicts how much downstream slice regression? No
    published mapping found; the platform must calibrate per task family. *Owner: doc 04.*
11. **⚠️ Dynamo SLA-planner profiling.** Three documented URLs 404'd and
    `docs.nvidia.com/dynamo/llms-full.txt` returned empty. The README names the Planner
    (*"profiles workloads and right-sizes pools"*) and **AISimulate** (*"predicts serving
    behavior and searches deployment configurations offline"*)
    [src](https://github.com/ai-dynamo/dynamo), but the profiling inputs/outputs are unknown.
    AISimulate in particular could replace §3.5 stage 2. *Owner: this doc, with search.*
12. **⚠️ Bayesian optimization for serving configuration.** No published application found; every
    tool fetched is grid- or heuristic-based. §3.5 stage 4 is a design proposal.
    *Owner: this doc.*
13. **⚠️ The Sakana AI CUDA Engineer episode.** `sakana.ai/ai-cuda-engineer/` now redirects to
    arXiv:2509.14279 (verified by curl, 2026-09-19); the original claims and the correction are
    not retrievable there. Needed as the canonical cautionary case for §4.2. *Owner: doc 09,
    with search.*
14. **⚠️ Requests/second break-even, dedicated replica vs shared pool vs serverless, per repo
    model.** `00` Open Question 18 assigns this here; it cannot be closed from per-pair documents
    because it depends on fleet utilisation `U` ([`scaling/07` §1.5](../scaling/07-cost-engineering.md))
    and the tenant mix. It is a fleet-planning calculation. *Owner: this doc + `scaling/07`.*
15. **⚠️ Does an NVFP4 (or FP8) Marlin-2B build make sense?** None exists
    ([`models/marlin2b/b300.md` §0](../models/marlin2b/b300.md)), so B300's FP4 pipes go unused
    for the video student. Marlin is prefill-bound, and prefill is compute-bound, so FP4 should
    help *more* here than for a decode-bound model — but the model is 4.4 GB and the GPU has
    241 GB usable, so the memory argument is nil and the whole case rests on GEMM rate. Worth a
    measurement. *Owner: this doc.*
16. **⚠️ `00` Open Question 17 (DeepSeek-V4.1-Flash vendor price, $0.60 vs $1.20/1M output) is
    assigned to "doc 06" = this document.** It is a *pricing-comparison* question
    ([`README.md` §3](../README.md) quotes DeepSeek off-peak; Baseten's Model API quotes $1.20
    [[src](https://www.baseten.co/pricing/)]), not an optimization question. **Recommendation:
    reassign to the cost/competitive document.** The answer this document would give: quote the
    *serverless open-model* price the customer can actually buy at their volume and SLA — the
    off-peak rate is not a rate anyone can plan a p99 SLO against.
17. **⚠️ MLPerf Inference accuracy targets.** The page fetched confirms the closed/open division
    rules but states that the exact quality targets *"are located in the official rules
    document"* [src](https://mlcommons.org/benchmarks/inference-datacenter/). The 99 % / 99.9 %
    convention is worth adopting for the platform's own reports, but should be quoted from the
    rules, not from memory. *Owner: this doc.*
18. **⚠️ LoRAX maintenance status.** No deprecation notice on the page fetched, but
    `predibase.com` redirects to `rubrik.com` (`00` Open Question 4). Whether LoRAX, Turbo LoRA
    and the SGMV kernels survive matters for §6.2. *Owner: doc 09.*

---

## Sources

All fetched 2026-09-19 unless the source states its own date. `vendor` marks a vendor-published
claim that has not been independently replicated.

**Quantization pipelines and formats**
- llm-compressor (repo) — https://github.com/vllm-project/llm-compressor
- llm-compressor docs, **v0.13.0** — https://docs.vllm.ai/projects/llm-compressor/en/latest/
- compressed-tensors — https://github.com/neuralmagic/compressed-tensors
- NVIDIA Model Optimizer (ModelOpt), incl. 2026-09-16 NVFP4+QAD tutorial, Puzzletron, customer stories — https://github.com/NVIDIA/TensorRT-Model-Optimizer
- ModelOpt **AutoQuantize** (ILP mixed precision), 2026-08-24 — https://nvidia.github.io/Model-Optimizer/announcements/autoquantize.html
- ModelOpt **Local-Hessian weight scales**, 2026-09-09 — https://nvidia.github.io/Model-Optimizer/announcements/local-hessian.html
- NVIDIA **QAD** on Nemotron 3.5 Lightning, 2026-08-17 — https://developer.nvidia.com/blog/developing-nemotron-3-5-lightning-nvfp4-with-qad-using-nvidia-model-optimizer/
- NVIDIA NVFP4 training blog, 2025-08-25 — https://developer.nvidia.com/blog/nvfp4-trains-with-precision-of-16-bit-and-speed-and-efficiency-of-4-bit/
- *Pretraining Large Language Models with NVFP4* — https://arxiv.org/abs/2509.25149
- GPTQModel **v7.5.0, 2026-09-15** — https://github.com/ModelCloud/GPTQModel
- AutoAWQ — **archived 2025-05-11, deprecated** — https://github.com/casper-hansen/AutoAWQ
- AMD Quark **v0.12.post1** — https://quark.docs.amd.com/latest/
- Red Hat / Neural Magic, *We ran over half a million evaluations on quantized LLMs*, 2024-10-17 — https://developers.redhat.com/articles/2024/10/17/we-ran-over-half-million-evaluations-quantized-llms

**Pruning, sparsity, KV quantization**
- SparseGPT — https://arxiv.org/abs/2301.00774
- Wanda — https://arxiv.org/abs/2306.11695
- Minitron (*Compact Language Models via Pruning and Knowledge Distillation*) — https://arxiv.org/abs/2407.14679
- ShortGPT (Block Influence, layer dropping) — https://arxiv.org/abs/2403.03853
- KIVI (2-bit KV, per-channel keys / per-token values) — https://arxiv.org/abs/2402.02750

**Speculative decoding**
- EAGLE-3 — https://arxiv.org/abs/2503.01840
- SpecForge **v0.3.0, 2026-08** — https://github.com/sgl-project/SpecForge
- Together **ATLAS**, 2025-10-10 (`vendor`) — https://www.together.ai/blog/adaptive-learning-speculator-system-atlas
- Fireworks **FireOptimizer**, 2024-08-30 (`vendor`) — https://fireworks.ai/blog/fireoptimizer

**Benchmarking, config search, simulation**
- vLLM `auto_tune` — https://github.com/vllm-project/vllm/tree/main/benchmarks/auto_tune
- GuideLLM — https://github.com/vllm-project/guidellm
- SGLang benchmarking and profiling — https://docs.sglang.io/developer_guide/benchmark_and_profiling.html
- trtllm-bench — https://nvidia.github.io/TensorRT-LLM/performance/perf-benchmarking.html
- NVIDIA Dynamo (Planner, AISimulate; **v1.0 released 03/15, container 1.4.2**) — https://github.com/ai-dynamo/dynamo
- InferenceX (formerly InferenceMAX), Apache-2.0 — https://github.com/SemiAnalysisAI/InferenceX
- InferenceMAX relocation notice — https://github.com/InferenceMAX/InferenceMAX
- Vidur / Vidur-Search — https://arxiv.org/abs/2405.05465
- MLPerf Inference Datacenter — https://mlcommons.org/benchmarks/inference-datacenter/
- lm-evaluation-harness — https://github.com/EleutherAI/lm-evaluation-harness

**Agentic / auto-research for systems and kernels**
- AlphaEvolve (DeepMind) — https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/
- KernelBench — https://arxiv.org/abs/2502.10517
- *Towards Robust Agentic CUDA Kernel Benchmarking, Verification, and Optimization* (robust-kbench) — https://arxiv.org/abs/2509.14279
- Sakana AI, *The AI CUDA Engineer* — https://sakana.ai/ai-cuda-engineer/ — **now redirects to arXiv:2509.14279; original claims and errata not retrievable (Q13)**
- Kevin-32B (Cognition), 2025-05-06 — https://cognition.com/blog/kevin-32b
- GEAK (AMD Triton kernel agent) — https://arxiv.org/abs/2507.23194
- *Barbarians at the Gate: How AI is Upending Systems Research* (ADRS) — https://arxiv.org/abs/2510.06189

**Multi-LoRA serving**
- vLLM LoRA documentation — https://docs.vllm.ai/en/latest/features/lora.html
- S-LoRA — https://arxiv.org/abs/2311.03285
- Punica — https://arxiv.org/abs/2310.18547
- LoRAX — https://github.com/predibase/lorax

**Registry, packaging, provenance**
- MLflow Model Registry — https://mlflow.org/docs/latest/ml/model-registry/
- W&B Registry — https://docs.wandb.ai/guides/registry/
- Hugging Face Hub, uploading models — https://huggingface.co/docs/hub/en/models-uploading
- Hugging Face Hub, Xet storage backend — https://huggingface.co/docs/hub/en/storage-backends
- ModelPack specification (CNCF) — https://github.com/modelpack/model-spec
- KitOps (ModelPack reference implementation, Cosign signing) — https://kitops.org/
- sigstore `model-transparency` / `model_signing` — https://github.com/sigstore/model-transparency

**Competitive / adjacent product evidence**
- Baseten blog index — https://www.baseten.co/blog/
- Baseten, *LangChain trains custom models for LangSmith Engine with Baseten Loops*, 2026-09-15 — https://www.baseten.co/blog/langchain-trains-custom-models-langsmith-engine-baseten-loops/
- Baseten, *How Baseten makes pyannote's diarization models 9.6x faster*, updated 2026-09-09 — https://www.baseten.co/blog/how-baseten-makes-pyannotes-diarization-models-96x-faster/

**This repository (numbers cited by row, never re-derived)**
- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — §4 roofline, §6 cost, §8 pinned inputs
- [`research/platform/00-goal-and-problem-statement.md`](./00-goal-and-problem-statement.md) — the loop, invariants, economics, the gate-twice rule
- [`research/cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) — §3 NVFP4 vs MXFP4, §4.2 silicon support, §8 KV quantization, §9.6 checkpoint × GPU matrix
- [`research/cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) — §1 prefix caching, §2 speculative decoding, §4.1 chunked prefill, §4.2 CUDA graphs, §4.3 the Pareto curve
- [`research/cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md) — attention backends per GPU
- [`research/cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) — engine × GPU × model support
- [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) — §5.14 planning prices
- [`research/matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md) — §4.1 executed format, §7 speculative decoding per model per GPU
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) — §2–§5 grids, §7.1 MBU, §7.2 cache hit rate, §7.3 speculation on/off, §7.4 reserved pricing
- [`research/models/qwen3827b/b300.md`](../models/qwen3827b/b300.md) — §0 verdict, §2 optimization table
- [`research/models/marlin2b/b300.md`](../models/marlin2b/b300.md), [`marlin2b/rtx6000-pro.md`](../models/marlin2b/rtx6000-pro.md), [`marlin2b/gb300.md`](../models/marlin2b/gb300.md)
- [`research/scaling/02-serving-stack-and-routing.md`](../scaling/02-serving-stack-and-routing.md), [`03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md), [`04-throughput-and-utilization.md`](../scaling/04-throughput-and-utilization.md), [`05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md), [`06-cold-start.md`](../scaling/06-cold-start.md), [`07-cost-engineering.md`](../scaling/07-cost-engineering.md)
- [`research/gpus/b300.md`](../gpus/b300.md), [`rtx6000-pro.md`](../gpus/rtx6000-pro.md)

---

## Verification log (2026-09-19)

Adversarial re-check of the 25 most consequential claims in this document. Every external
citation was **opened**, never trusted from the footnote; every `research/` cross-reference was
opened as a file; every derivation was recomputed with `python3`. Verdicts: **CONFIRMED** (source
says what the doc says), **CORRECTED** (edited above), **⚠️ UNVERIFIABLE** (claim could not be
relocated on the live source; downgraded in place).

### Corrected

| # | Claim as it stood | What the source says | Where |
|---|---|---|---|
| C1 | §1.6: AutoQuantize search "~1 GPU-hour … **~$30** `est.` on 4 B300" | 4 GPUs × 16 min = **1.07 GPU-hours** = **$7.90** at B300 `low` $7.40. $30 is 4 GPU-hours. The published timing is on **4× RTX 6000 Ada**, not B300 [src](https://nvidia.github.io/Model-Optimizer/announcements/autoquantize.html) | §1.6 row fixed |
| C2 | §1.6: a sweep is "under **2 %** of the one-time cost of a loop iteration" | `00` §4.3 gives annotation $3.3k–$6.6k + training $5.5k = $8.8k–$12.1k. $120/$12.1k = **1.0 %**; $300/$8.8k = **3.4 %**. "Under 2 %" is false at the top of the sweep range | §1.6 rule fixed |
| C3 | §2.5 / Q2: "llm-compressor's own feature list … does not name 2:4 as a standalone supported format", and "the 2:4+FP8 example URL returned HTTP 404" | The docs are explicit, not silent: *"Sparse compression (including 2of4 sparsity) is **no longer supported** by LLM Compressor due to lack of hardware support and user interest"* [src](https://docs.vllm.ai/projects/llm-compressor/en/latest/). The 404 is the removed example, not a broken link. This **strengthens** the doc's recommendation from "unproven" to "deprecated upstream" | §2.5 and Q2 rewritten |
| C4 | §2.2: "Kimi-K3 is called out by name in its **architecture list**" | Kimi-K3 appears in the README's **model highlights / example checkpoints**; the architecture-specific section covers MoE LLMs, VLMs and audio-LMs [src](https://github.com/vllm-project/llm-compressor) | §2.2 row fixed |
| C5 | §2.2: Local-Hessian gives its result with *"zero additional deployment cost"* | The page's wording is *"without incurring any deployment throughput penalty"*; the quoted phrase does not appear [src](https://nvidia.github.io/Model-Optimizer/announcements/local-hessian.html). Numbers (5.10 / 3.10 / 3.87 / 4.75 / 2.94 pp on Qwen3.5-9B NVFP4 W4A4) are all **CONFIRMED** | §2.2 quote fixed |
| C6 | §2.8 and "What to avoid": "**three** numbers that look identical (**3.51**)" | [`gpu-optimizations.md` §7](../matrix/gpu-optimizations.md) names **two** 3.51s (synthetic harness constant; real per-request mean) and a **different** third case — SGLang's simulated **4.5** / **5.5** under `SGLANG_SIMULATE_ACC_LEN` | both places fixed |
| C7 | §7.2: RTX PRO 6000 S4 "$0.032–$0.072" | The source rows are **$0.0315–$0.0724** ([`marlin2b/rtx6000-pro.md` §4.2](../models/marlin2b/rtx6000-pro.md), [`cost-matrix.md` §3](../matrix/cost-matrix.md)). Rounded *up* at both ends, and this doc's own convention is to quote rows verbatim | §7.2 table fixed |

### Downgraded (⚠️ could not be relocated on the live source)

| # | Claim | Status |
|---|---|---|
| U1 | §2.2: ModelOpt described as *"a library comprising … quantization, pruning, Neural Architecture Search (NAS), distillation, speculative decoding **and sparsity**"* | The description served on re-fetch is *"A unified library of SOTA model optimization techniques like quantization, distillation, pruning, neural architecture search, speculative decoding, etc."* — **sparsity is not named**. Marked ⚠️ in place; everything else in that row (rebrand **2025-12-08**, the 2026-09-16 W4A4+QAD tutorial with *"up to 1.30x vLLM throughput over BF16 and 3.1x smaller checkpoints"*, SGLang/TRT-LLM/TensorRT/vLLM deployment targets) is **CONFIRMED** |
| U2 | §2.2: AutoQuantize groups *"coupled operators (e.g. QKV projections)"* | The page's wording is *"Any restriction of the form 'this group of operators takes one joint format decision'"*, applied to Q/K/V projections. Substance confirmed, verbatim quote not. Marked ⚠️ in place |

### Confirmed — external primary sources (all opened 2026-09-19)

- **SparseGPT** [2301.00774] — 60 % unstructured with negligible perplexity increase on OPT-175B / BLOOM-176B, *"more than 100 billion weights"*, *"under 4.5 hours"*, generalizes to 2:4 and 4:8, compatible with weight quantization, no retraining. ✓ (the paper's headline is "at least 50 %"; the doc's 60 % is the negligible-degradation figure, correctly used)
- **Wanda** [2306.11695] — ‖weight‖ × ‖input activation‖ per output; *"significantly outperforms the established baseline of magnitude pruning"*; *"requires no retraining or weight update"*. ✓
- **Minitron** [2407.14679] — 8B and 4B from a 15B base, *"up to 40x fewer training tokens per model"*, **< 3 %** of the original data, **1.8×** compute saving for the family, *"up to a 16% improvement in MMLU"*. ✓
- **ShortGPT** [2403.03853] — Block Influence; *"significantly outperforms previous state-of-the-art (SOTA) methods in model pruning"*; *"orthogonal to quantization-like methods"*; **abstract gives no layer count or accuracy number** — the doc's caveat is accurate. ✓
- **KIVI** [2402.02750] — 2-bit, per-channel keys / per-token values, *"2.6× less peak memory"*, *"up to 4× larger batch size"*, **2.35×–3.47×** throughput, tuning-free. ✓
- **EAGLE-3** [2503.01840] — *"up to 6.5x"*, *"about 1.4x improvement over EAGLE-2"*, **1.38×** throughput at batch 64 in SGLang, direct token prediction + multi-layer fusion, and the data-scaling claim this document leans on. ✓
- **S-LoRA** [2311.03285] — *"improve the throughput by up to 4 times"* vs HuggingFace PEFT and vLLM with naive LoRA. ✓
- **Punica** [2310.18547] — *"12x higher throughput"*, *"only adding 2ms latency per token"*. ✓
- **Vidur** [2405.05465] — *"less than 9% error"*; LLaMA2-70B config in **one hour on a CPU machine** vs **42K GPU hours** costing **~$218K**. ✓
- **KernelBench** [2502.10517] — **250** PyTorch workloads; `fast_p` as quoted; SOTA reasoning models beat the PyTorch baseline in *"less than 20%"* of cases. ✓
- **GEAK** [2507.23194] — Triton kernels for **MI300X and MI250**, correctness **up to 63 %**, speedup **up to 2.59×**. ✓
- **robust-kbench** [2509.14279] — *"existing kernel generation benchmarks suffer from exploitable loopholes and insufficient diversity"*, *"a novel evolutionary meta-generation procedure"*, LLM-based verifiers, kernels *"outperforming torch implementations for practical applications"*. Authors are Lange, Sun, Prasad, Faldor, Tang, Ha — i.e. the Sakana team, which supports §4.2's inference without needing the retracted page. ✓
- **ADRS** [2510.06189] — *"up to 5.0x runtime improvements or 50% cost reductions"*; domains include **MoE inference**; the verifier thesis quoted in §4.2 is verbatim from the abstract. ✓
- **NVFP4 pretraining** [2509.25149] — **12B** hybrid Mamba-Transformer, **10 trillion tokens**, *"comparable to an FP8 baseline"*, *"the longest publicly documented training run in 4-bit precision to date"*. ✓ Correctly labelled a *pretraining* result.
- **AlphaEvolve** (DeepMind blog) — Borg heuristic *"continuously recovers, on average, 0.7% of Google's worldwide compute resources"*, in production over a year; **23 %** matmul kernel speedup → **1 %** of Gemini training time; FlashAttention *"up to a 32.5% speedup"*; 4×4 complex matmul in **48** scalar multiplications; improvements in **20 %** of the open math problems; TPU Verilog rewrite. ✓
- **Kevin-32B** (Cognition) — QwQ-32B, 180 of 200 KernelBench L1+L2 tasks trained on; at 8 refinement steps **65 %** avg@16 correct, **89 %** of the dataset solved, vs o4-mini **53 %** / o3 **51 %**; best@16 **1.41×**; Level 2 avg@16 **48 %** vs **9.6 % / 9.3 %**. The three reward-hacking exploits and the *"reward of 0"* fix are verbatim. ✓
- **Fireworks FireOptimizer** (2024-08-30) — generic draft **29 %** hit rate and **1.5× slower**; customized **76 %** and **2× faster**; *"3x speedup over a generic draft model"*. ✓ The negative-not-neutral reading §2.8 builds on is exactly what the table shows.
- **Together ATLAS** (2025-10-10) — **500 TPS** DeepSeek-V3.1, **460 TPS** Kimi-K2 on HGX B200, *"2.65x faster than standard decoding"*, **400 %** over the FP8 baseline (**105 → 501 TPS**); static / adaptive / confidence-aware-controller descriptions verbatim. ✓
- **Red Hat / Neural Magic** (2024-10-17) — >500,000 evaluations, Llama 3.1 8B/70B/405B, W8A8-INT / W8A8-FP / W4A16-INT; OpenLLM v1 *"over 99%"*; v2 *"close to 99% … at least 96% recovery"*; Arena-Hard-Auto **500 prompts**, overlapping 95 % CIs; HumanEval **99.9 %** at 8-bit, **98.9 %** at 4-bit; **vLLM 0.6.2**. ✓
- **AutoAWQ** — archived **2025-05-11**, *"officially deprecated and will no longer be maintained"*, pointing to llm-compressor and MLX-LM. ✓
- **llm-compressor v0.13.0** — the full format list (W4A16/W8A16, W8A8-INT8, W8A8-FP8, MXFP8, NVFP4/MXFP4, NVFP4A16/MXFP4A16/MXFP8A16, W4AFP8, W4AINT8) and algorithm list (RTN, GPTQ, AWQ, SmoothQuant, SpinQuant, QuIP, FP8 KV cache, AutoRound) match the docs exactly. ✓ (sparsity: see C3)
- **GPTQModel v7.5.0, 2026-09-15** — *"an extensible platform for LLM quantization, validation, and deployment"*; GPTQ, AWQ, ParoQuant, QQQ, GGUF, FP8, EXL3, GPTAQ, EoRA, GAR, FOEM; exports to Transformers, **vLLM, SGLang**. ✓
- **AMD Quark v0.12.post1** — description, the PyTorch dtype list (incl. MX6/MX9 and MX) and the ONNX / JSON-safetensors / GGUF export list all verbatim; and **Q1 is confirmed as a real gap** — the page names **neither MI300X/MI355X nor vLLM, SGLang or compressed-tensors**. ✓
- **NVIDIA QAD / Nemotron 3.5 Lightning** (2026-08-17) — PTQ **96.33 / 95.84 / 99.24 %** → QAD **99.72 / 98.53 / 98.97 %**; LR 5e-6 constant, no warmup, dropout off, grad-clip 1.0, **TP=2 EP=4 on two 8-GPU nodes**, **6,400 samples**, **524K** sequence length, **~400 iterations**; **66 GB → 22 GB**; *"up to 4x faster throughput"*; the simulated-quantization quote; and **the third-row caveat this document highlights is real** — gains on the final conservative checkpoint are concentrated on agentic benchmarks (Terminal-Bench v2.1, SWE-Bench Multilingual, BrowseComp, PinchBench, HLE, AA-Omniscience). ✓
- **ModelOpt AutoQuantize** (2026-08-24) — Qwen3.6-35B-A3B, **4× RTX 6000 Ada**, **~16 min** vs **~14 h** KL baseline (recomputed: **52.5×**), `effective_bits: 4.8`, ILP over a gradient-based second-order-Taylor / diagonal-Fisher score, and *"At every plotted budget, searching over NVFP4, FP8, and BF16 matches or beats NVFP4 and BF16 alone"* verbatim. ✓ (cost: C1; quote: U2)
- **vLLM `auto_tune`** — grid over `NUM_SEQS_LIST` × `NUM_BATCHED_TOKENS_LIST`; `MIN_CACHE_HIT_PCT`; the adaptive rate-decrease search verbatim; and **§3.3's ⚠️ is correct** — `MAX_LATENCY_ALLOWED_MS` is *"the maximum allowed P99 end-to-end latency"*, a single unified constraint with no separate TTFT/TPOT bounds. ✓
- **vLLM LoRA docs** — `POST /v1/load_lora_adapter`, `"load_inplace": true`, *"This feature comes with security risks. It should not be used in production unless it is an isolated, fully trusted environment."*, and the `--max-lora-rank` memory warning. **Q8 is confirmed as a real gap**: the page makes no mention of quantized-base + LoRA compatibility. ✓ (the "fixed at server start" property is implied by context, not stated on that page — it is sourced from [`scaling/06` §6.3](../scaling/06-cold-start.md) and stands)
- **SpecForge v0.3.0 (2026-08)** — EAGLE3, P-EAGLE, EAGLE3.1, DFlash, DFlash2, Domino, DSpark; online + offline (colocated and disaggregated); *"up to 4x speedup"* with SpecBundle; Qwen3-8B / Qwen3-30B / Qwen3.6-27B example configs. **Q6 is confirmed as a real gap**: no hardware or dataset scale is given anywhere on the page. ✓
- **NVIDIA Dynamo** — Planner *"SLA-driven autoscaler that profiles workloads and right-sizes pools"* and AISimulate *"Predicts serving behavior and searches deployment configurations offline"* are both verbatim in the README; container tag **1.4.2** confirmed. **Q11 stands** — the README gives the capability, not the profiling inputs/outputs. ✓
- **Sakana AI CUDA Engineer URL (Q13)** — re-verified by `curl`: `https://sakana.ai/ai-cuda-engineer/` returns **HTTP 200** and serves the **robust-kbench** page (`<title>Towards Robust Agentic CUDA Kernel Benchmarking, Verification, and Optimization</title>`). §4.2's refusal to paraphrase the original claims from memory is the right call and remains correct. ✓
- **Tinker checkpoint storage** — **$0.10 per GB per month** confirmed on the Tinker docs page; §5.8's **22 GB → $2.20/month** recomputed ✓.

### Confirmed — `research/` cross-references (files opened)

- `00` §6 row **06** is indeed this component, and the doc-numbering note at the top is accurate ✓. `00` §1.2's S1–S9 stage machine, the gate-twice quote, and S8's placement between S7 and S9 ✓.
- `00` §4.3 economics: annotation **$3,280 / $6,560**, training **$5,554**, batched-cached Opus **$4.16/1M**, replica floor **≥ $7.3k/month** — all present as cited ✓ (see C2 for the ratio).
- [`qwen3827b/b300.md`](../models/qwen3827b/b300.md): **71.7 %** of GEMM FLOPs at NVFP4 ✓; B300 INT8 **187.5 TOPS dense = 0.042× B200, ~0.08× its own BF16** (recomputed: 0.0417, 0.0833) ✓; FP8 KV **231 → 325** at TP1 (recomputed **+40.7 %**, doc says +41 %) ✓; **76,458 → 91,022** = **1.19×** ✓; MTP 0.425 B, γ=3, acceptance **92.2 % BF16 / 84.8 % FP8** on GB300 TP4, mean accepted length **4.28**, DFlash2 best **4.80**, DFlash2 γ=7 → `D = 8` slots ✓; **396 concurrent 4.5K sequences** and "the TPOT SLO never binds" ✓; `--chunked-prefill-size ∈ [1, 8192]` ✓; `--mm-encoder-tp-mode data` and `--language-model-only` **+49 %** KV pool at 32K on a 5090 ✓; `triton` as the safe SM103 choice ✓.
- [`marlin2b/*`](../models/marlin2b/): **6 of 24** attention layers ✓; **19,537,920 B = 18.63 MiB** GDN state at `S = 1` ✓; zero `mtp` tensors, missing module **60,828,160** params, no EAGLE head ✓; *"every scenario is prefill-bound"* ✓; TP=4 **6–7 tok/s** vs TP2+PP2 **46–49** ✓; the `--hf-overrides '{"architectures": ["Qwen3_5ForConditionalGeneration"]}'` requirement and its "never validated end-to-end" status ✓; `prasannaJagadesh/marlin-2B-GPTQ-4BITS` as the only 4-bit option ✓; **0.28–0.30×** of roofline at full batch ✓; batch **1,019 = KV ceiling** ✓. Q15's "the model is 4.4 GB" is the **BF16-resident** figure (4.426 GB); the on-disk figure pinned in METHODOLOGY §8 is **5.444 GB** — both correct, different quantities, worth stating which in future edits.
- [`cost-matrix.md`](../matrix/cost-matrix.md): every §7 row quoted in §7.1 and §7.2 matches — Qwen S1 **$0.1649–$0.3343**, S4 **$0.1527–$0.3095**, blended **$0.0602–$0.1221**, `res1y` **$0.06459** with B200 **$0.0538** ✓; cache sensitivity **$0.0757 / $0.0602 / $0.0478** with output **68 %** of the blend ✓; Marlin B300 **$0.022–$0.0447** / blended **$0.0092–$0.0185** / `res1y` **$0.00987**, RTX blended **$0.0135–$0.0311** / `res1y` **$0.00975** ✓; ±20 % MBU → **×1.25 / ×0.833** ✓; speculation-off **3.1×–6.9×** worse ✓; video hit rate **≈ 0 %**, *"charged but never earned"*, realistic blend **+32 %** ✓; agentic hit rates **97.6 %** median and **99.7 %** ✓.
- [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md): B300 **$7.40 / $15.00 / $7.94** and RTX PRO 6000 **$1.80 / $4.143 / $1.30** ✓. Recomputed `res1y` multipliers **1.0730** and **0.7222** ✓, and both published-blended cross-checks reproduce (**$0.0092 × 1.073 = $0.009872**, **$0.0135 × 0.722 = $0.009747**) ✓.

### Derivations recomputed with `python3` (all correct unless listed under Corrected)

Sweep costs **16 GPU-h → $118.40 / $240.00** and **40 GPU-h → $296.00**; screens **$7.40–$22.20** each and **$370–$1,110** at N = 50; QAD deltas **+3.39 / +2.69 / −0.27 pp**; Local-Hessian **5.10 − 3.10 = 2.00 pp**; AutoQuantize **52.5×**; §7.1 cache **−36.9 %** and S1→S4 **+8.0 %**; §7.2 per-clip `res1y` **$0.000523** and **$0.000426**, ratios **0.815** and **1.211**; **$487 / $590 / $426** at 1M clips, spread **$164** = **2.25 %** of a $7.3k replica-month; §7.3 **$4.16 / $0.0646 = 64.4×**; §5.8 **22 GB × $0.10 = $2.20/month**; §4.5 **12 × $120–$300 = $1,440–$3,600/customer/year** (the doc's "$1.5k–$4k" rounds outward on both ends and is defensible only once the gate is added — read it as $1.4k–$3.6k of sweep plus gate).

One rounding worth naming: §7.2's RTX per-clip `low` **$0.000590** is 2 × the source's **$0.000293** = **$0.000586**. The 0.7 % difference does not move the 18.5 % / 21.1 % conclusions.
