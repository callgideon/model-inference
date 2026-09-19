# How optimized inference providers build their platforms

Research date: **2026-09-19**. Conventions and the `[src]` / ⚠️ legend are in
[`../METHODOLOGY.md`](../METHODOLOGY.md#legend).

**Who this is for.** You run LLMs on your own bare-metal GPU cluster. This
document reverse-engineers what **28 commercial inference operators** actually
built — schedulers, container runtimes, snapshot systems, weight-delivery
networks, serving engines, speculators, KV hierarchies, autoscalers, admission
control, billing models — and says, per field, what transfers to a
single-tenant on-prem cluster and what is an artefact of being a multi-tenant
public business.

It merges two earlier drafts: the serverless-GPU / container platforms study
(Modal, fal, Wafer, Baseten, RunPod, Replicate, Beam, Inferless, Northflank,
Cerebrium, Lambda, Nebius, Hugging Face, Anyscale, Cloudflare) and the
per-token model-API / custom-silicon study (Together, Fireworks, DeepInfra,
Novita, Groq, Cerebras, SambaNova, Perplexity, OpenRouter, DeepSeek, Moonshot,
Mistral, and the frontier labs' commercial scheduler interfaces). Profiles are
kept intact; only duplication was removed.

**Related documents — read these first, they are not repeated here.**

| Topic | Document |
|---|---|
| Node/rack hardware, fabric, OS, orchestration, weight distribution on bare metal | [`01-bare-metal-cluster.md`](01-bare-metal-cluster.md) |
| Serving stack layering, routing algorithms, gateways, multi-model serving | [`02-serving-stack-and-routing.md`](02-serving-stack-and-routing.md) |
| Capacity limits, admission control, backpressure, SLO-aware scheduling | [`03-concurrency-and-admission-control.md`](03-concurrency-and-admission-control.md) |
| Utilisation metrics, cluster-level batching, memory tiering for KV | [`04-throughput-and-utilization.md`](04-throughput-and-utilization.md) |
| Autoscaling signals, Kubernetes mechanisms, predictive scaling, scale-down | [`05-autoscaling-and-predictive-scaling.md`](05-autoscaling-and-predictive-scaling.md) |
| Cold-start anatomy, weight-load fast paths, compile caches, snapshot/restore | [`06-cold-start.md`](06-cold-start.md) |
| Fleet cost model, utilisation levers in cost terms, FinOps loop | [`07-cost-engineering.md`](07-cost-engineering.md) |
| SLOs, failure modes, recovery, observability, change management, runbooks | [`08-reliability-and-operations.md`](08-reliability-and-operations.md) |
| Published production architectures, open-source stacks, blueprints | [`09-reference-architectures.md`](09-reference-architectures.md) |
| Engine support matrix (vLLM / SGLang / TRT-LLM per GPU and model) | [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) |
| Prefix/KV caching, speculative decoding, parallelism, PD disaggregation | [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) |
| NVFP4 / MXFP4 / FP8 hardware acceleration per GPU | [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) |
| Attention kernels per architecture | [`../cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md) |
| GPU-hour prices used for every cost comparison below | [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) |

### The five business models in this study

| Model | Who | What you buy | What the provider owns |
|---|---|---|---|
| **GPU-seconds (serverless / container platform)** | Modal, RunPod, Beam, Cerebrium, Inferless, Northflank, Replicate, Baseten (dedicated), Hugging Face Endpoints, Anyscale (BYOC), fal (private serverless) | $/GPU-second or -minute of container lifetime | A scheduler, a runtime, a weight path — usually *not* the metal |
| **Per-token API on GPUs** | Together, Fireworks, DeepInfra, Novita, Baseten Model APIs, Nebius Token Factory, Cloudflare Workers AI, fal model APIs, and the ~20 endpoints visible through OpenRouter | $/1M tokens, plus optional dedicated endpoints at $/GPU-hour | An inference engine + a GPU fleet, owned or rented |
| **Per-token API on custom silicon** | Groq, Cerebras, SambaNova | $/1M tokens only (Cerebras and Groq also sell reserved capacity) | The chip, the compiler, the runtime and the data centre |
| **First-party model operator** | DeepSeek, Moonshot, Mistral, Anthropic, OpenAI, Google | $/1M tokens for *their own* model | Everything, including the model |
| **Neither capacity nor model** | **Wafer** (continual optimisation of someone else's capacity), **OpenRouter** (routing over everyone else's endpoints), **Perplexity** (operator for its own product, publishing kernels) | Optimisation, routing, or open kernels | Software, not silicon |

OpenRouter's row matters to a self-hoster for a non-obvious reason: its
`/api/v1/models/{slug}/endpoints` API is the only place where **twenty
providers' prices, quantization formats, context limits and 30-minute uptime
for the same model** are published side by side and machine-readable. It is the
price source throughout §5, and its per-endpoint `quantization` field is the
closest thing the market has to a truth-in-labelling regime.

### Method, and its explicit limits

**Source policy.** Every factual claim carries `[src](url)` to a primary
document — the provider's own engineering blog, docs, pricing page, paper or
repo — or is marked **⚠️ TO BE VERIFIED**. Where a claim is a reading of docs,
pricing or product structure rather than something the provider stated, it is
labelled **(inferred)**. No internal detail is invented: where a provider does
not publish something (Modal's scheduler language, fal's engine internals,
Inferless's cold-start mechanism, DeepInfra's engine), the field says so.
`meas.` marks a number the provider published as a measurement. Every profile
uses the same eleven fields **(a)–(k)**.

**⚠️ Tooling limits, stated because they affect coverage.**

- The serverless-platform half was researched with **zero web searches
  available** — the session's budget was exhausted (200/200) by earlier phases.
  Source discovery was done by fetching `sitemap.xml` / `sitemap-posts.xml` and
  `llms.txt` doc indexes and then the raw `.md` of each page (which is *more*
  faithful than a search snippet, and is where the exact parameter names and
  default values in §2.2 come from), plus bulk HTTP probing of candidate URLs.
  **85 distinct primary URLs** were fetched for that half. The coverage cost is
  concentrated in company identification for **Wafer** (§3A.3), resolved
  instead by probing eight candidate domains. Third-party benchmarks, press
  coverage, conference talks not linked from a sitemap, and job postings (all
  careers pages probed were client-rendered) are under-represented.
- **Perplexity's blog** (`perplexity.ai/hub/blog/*`) returned HTTP 403 behind a
  Cloudflare interstitial to every user-agent tried. §3B.8 is built from public
  GitHub repositories only. **⚠️ TO BE VERIFIED.**
- **Google's Vertex Provisioned Throughput GSU tables** fetched as navigation
  shells with the numeric tables absent. §3B.13 records the mechanism without
  the burndown rates. **⚠️ TO BE VERIFIED.**
- **Cold-start numbers are published by almost nobody in the per-token class** —
  Together is the sole exception — and **no provider in this document publishes
  fleet utilisation.** Every "what the pricing implies about utilisation"
  statement is explicitly labelled *(inferred)* and is an argument from
  published price against this repo's own cost model, not a disclosure.
- **Every performance number here is vendor-published and vendor-selected.** No
  independent cross-provider benchmark was located.

---

## 1. Executive summary — the methods and architectures that recur

Twenty-eight operators, one page. Each finding names the providers that
evidence it.

1. **Nobody's moat is the GPU.** Every GPU-based provider here rents or resells
   the same NVIDIA silicon this repo's [`../gpus/`](../gpus/) docs cover. The
   differentiation is entirely in (a) how fast a replica becomes ready, (b) how
   tightly supply tracks demand, and (c) how many tokens per second the serving
   engine extracts. All three transfer to bare metal; only (b) is fundamentally
   cheaper for them than for you, and only because they pool uncorrelated
   demand. The **~5× spread on an identical H100-hour** ($1.89 fal discounted →
   $10.00 Hugging Face on GCP, §2.3) is the stack wrapped around the GPU, not
   the GPU.

2. **Prefill/decode disaggregation is the one architectural conclusion every
   serious operator reached independently** — on GPUs, on wafers and on
   dataflow units alike. DeepSeek runs **EP32 for prefill and EP144 for
   decode** on the same model
   [src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md);
   Moonshot's Mooncake runs separate prefill and decoding clusters
   [src](https://arxiv.org/abs/2407.00079); Together adds a **third
   "pre-prefill" tier** for low-reuse prompts
   [src](https://www.together.ai/blog/cache-aware-disaggregated-inference);
   Baseten reports **up to 6× TPS/GPU** from it
   [src](https://www.baseten.co/blog/nvidia-dynamo-day-baseten-inference-stack/);
   Fireworks *(inferred)* makes it an axis of its deployment-shape search —
   ⚠️ the 3D FireOptimizer post names its three axes as **speed, throughput
   (cost) and quality**, not prefill/decode placement, so this attribution is a
   reading of the product, not a statement Fireworks published
   [src](https://fireworks.ai/blog/3d-fireoptimizer); Ray Serve LLM ships it
   [src](https://docs.ray.io/en/latest/serve/llm/index.html); and **Cerebras
   bought AWS Trainium for prefill** while keeping decode on CS-3
   [src](https://www.cerebras.ai/blog/disaggregated-inference). If a bare-metal
   cluster adopts exactly one idea from this document, it is this one.

3. **Cold start is the product, and snapshotting is its endgame.** Modal,
   Baseten, RunPod, Beam and Cerebrium each independently built the same
   three-layer answer: a lazy or tiered content-addressed weight/image path,
   CPU — and now GPU — memory checkpoint/restore, and a warm buffer you pay
   for. Modal publishes the whole chain: **~2,000 s naïve → ~50 s, a claimed
   40×** [src](https://modal.com/blog/truly-serverless-gpus). Three providers
   ship GPU checkpoint/restore in three different trigger designs — Modal
   (lifecycle-annotated), Beam (`checkpoint_enabled`, fixed point at the end of
   `on_start`), Cerebrium (explicit POST to a sidecar) — all resting on
   NVIDIA's CUDA checkpoint API in driver branches **570/575**
   [src](https://modal.com/blog/gpu-mem-snapshots). Once weights stream at
   multiple GB/s the remaining cost is CPU work bandwidth cannot touch —
   `import torch` alone is **26,000 syscalls**
   [src](https://modal.com/blog/mem-snapshots) — plus CUDA context creation,
   kernel JIT, `torch.compile` and graph capture. Snapshotting is the only way
   to skip it.

4. **Weight delivery converged on a content-addressed, tiered, peer-fanned-out
   store.** Modal: a **~5 MB image index** over content-addressed files served
   from memory → SSD → AZ cache → regional CDN → blob at **~2.5 GiB/s**
   [src](https://modal.com/blog/jono-containers-talk). Baseten's BDN: node NVMe
   → in-cluster peer cache on a **consistent hash ring** → mirrored origin,
   **>2 GB/s** onto H100 nodes, **2–3× faster cold starts**, and the property
   that matters — **50 replicas of a 140 GB model consume 1× the model size of
   origin bandwidth, not 50×**
   [src](https://www.baseten.co/blog/how-the-baseten-delivery-network-bdn-makes-cold-starts-fast/).
   RunPod's host-affinity "cached models", Cerebrium's region-cached persistent
   storage, Together's fleet-wide weight cache (**"4× faster warm starts"**)
   and SambaNova's DDR→HBM→SRAM promotion (**15–31× faster model switching**,
   [src](https://arxiv.org/abs/2405.07518)) are the same idea at different
   fidelities.

5. **Concurrency, not utilisation, is the scaling signal — and one provider
   ran the controlled experiment.** Together replayed identical load under
   three policies on Qwen3.5-9B: `inflight_requests` scaled 1→2→3 and improved
   p95; a **TTFT p95 target never fired** while client p95 hit 3–5 s; a **75 %
   GPU-utilisation target never fired at all** while the engine was saturated —
   *"a GPU can read 60 % utilized while the engine's request queue is already
   backing up"*
   [src](https://www.together.ai/blog/autoscaling-endpoints-for-llm-inference).
   Every other platform that documents a signal agrees: RunPod (queue delay or
   `ceil((queued+running)/scaler)`, request-count **recommended for LLMs**),
   Beam (`tasks_per_container`), Cerebrium (concurrency utilisation), Modal
   (`target_inputs` as a set-point distinct from `max_inputs`), Fireworks (five
   load signals, max wins), Baseten (traffic as the *leading* indicator,
   utilisation as the *lagging* one). Hugging Face is the outlier still
   defaulting to **80 % accelerator utilisation** — and documents why it
   misbehaves: *"If your replicas have a long initialization time, autoscaling
   may not be as effective. This is because the average GPU utilization might
   fall below the threshold during that time, triggering the automatic scaling
   down of your endpoint."*
   [src](https://huggingface.co/docs/inference-endpoints/en/autoscaling)
   (verified 2026-09-19; the earlier paraphrase was printed as a quotation).

6. **Asymmetric autoscaling windows are universal: fast up, slow down.**
   Together 60 s up / 300 s down; Fireworks **30 s up / 10 min down / 1 h to
   zero**; Modal `scaledown_window` 60 s default (20 min max); Hugging Face
   1 min up / 2 min down + **300 s stabilisation**, scale-to-zero at 15 min.
   That is the correct default for anything with a 90–150 s cold start, which
   is every LLM replica.

7. **Scale-to-zero is sold, and quietly disowned for production.** Baseten says
   plainly that latency-sensitive light traffic *"signals insufficient scale"*
   and such users *"should use pay-per-token APIs until greater scale is
   reached"*
   [src](https://www.baseten.co/inference-engineering/book/07-production/autoscaling/);
   Together's `min_replicas: 0` **does not auto-wake**; Fireworks returns
   **HTTP 503 `DEPLOYMENT_SCALING_UP`, explicitly not queued**; Hugging Face
   documents a **502** during replica init. Beam publishes the arithmetic that
   makes the cost visible: 1 s boot + 100 ms of work + `keep_warm_seconds=300`
   bills **301.1 s** — at low, spread-out request rates the warm window *is*
   the bill.

8. **Optimisation has moved from the kernel to the request graph.** The
   measured wins published in 2025–2026 are about *which* tokens get computed
   and *where*: PD disaggregation **up to 6×** (Baseten); KV-cache-aware
   routing **34–62 %** (Baseten); cache-aware disaggregation **35–40 % higher
   sustainable throughput** (Together); hybrid suffix-automaton+MTP speculation
   **+40 % throughput or −40 % latency vs MTP alone** (Baseten); adaptive
   speculators **up to 4×** at batch 1 (Together ATLAS); DSpark **acceptance
   length 4.6, 16× throughput** (fal) and **2.17→2.55×** in vLLM (Novita);
   training-free prompt-lookup **+40 % (8B) / +70 % (70B)** (Cloudflare);
   per-head KV compression **8× smaller at >95 % task quality** (Cloudflare);
   distillation + CFG folding **6.3× end-to-end** (fal). Kernel fusion delivers
   tens of percent (fal's CUTLASS EVT **1.28×**) — *except* when something is
   badly wrong, in which case it delivers 10×+ (Wafer's **11.65×** on a kernel
   running 64 blocks on a 145-SM GPU, **6.25 % occupancy**). **Profile first;
   the distribution of wins is bimodal.**

9. **Almost nobody writes their own engine, and the three who did explain
   why.** Of the GPU-based platforms, Baseten, RunPod, Hugging Face, Anyscale,
   Novita, Modal, Beam, Cerebrium, Northflank, Replicate and Inferless all
   compose **vLLM / SGLang / TensorRT-LLM**. The exceptions are instructive:
   **Cloudflare's Infire** (Rust) beats vLLM 0.10.0 on an H100 NVL by only
   **~6.6 % on requests/s** but uses **25 % CPU vs vLLM's 140 %** — they
   optimised co-tenancy and host CPU, the constraint they actually had
   [src](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine);
   **fal** is output-priced on diffusion/video, where the win comes from kernel
   fusion, FP4 and *fewer forward passes*; **Together** (TIE 2.0) and
   **Fireworks** (FireAttention) sell per-token and keep every millisecond they
   save. Baseten — arguably the most performance-focused general LLM provider —
   composed instead, and built only the piece nobody else had: a speculation
   engine.

10. **The KV cache became cluster infrastructure, not per-GPU state.**
    Moonshot's Mooncake uses *"the underutilized CPU, DRAM, and SSD resources
    of the GPU cluster"* as a disaggregated KVCache store, with a KVCache-centric
    scheduler and **prediction-based early rejection**, measured at **up to
    525 %** throughput under SLO in simulation and **75 % more requests** in
    production [src](https://arxiv.org/abs/2407.00079). Together runs a
    three-level hierarchy (GPU HBM → host DRAM → cluster-wide RDMA cache);
    Baseten ships a KV Block Manager with offload to CPU/SSD/remote; Cloudflare
    compresses per attention head. DeepSeek's production **56 % input-cache hit
    rate** (342B of 608B input tokens in 24 h) is the reference number for what
    prefix reuse is worth on an agentic/chat mix.

11. **Admission control is where the honest operators differ from the rest.**
    Cerebras rate-limits on **uncached tokens separately from total tokens** —
    an explicit admission that the scarce resource is *prefill compute*, not
    served tokens
    [src](https://inference-docs.cerebras.ai/support/rate-limits). Moonshot
    rejects predictively rather than queueing work it cannot finish in SLO.
    Together isolates batch, real-time and untrusted traffic in **separate
    queues** rather than one FIFO. Baseten queues during scale-up with optional
    priority classes. Hugging Face, by contrast, answers a cold start with a
    **502**, and Fireworks with an unqueued **503**.

12. **Every provider bills per second or per minute; the real design choice is
    what counts as billable.** Cerebrium does **not** bill cold start but
    **does** bill initialization — so the customer pays for init, and Cerebrium
    shipped checkpointing to remove it. RunPod does **not** bill weight
    downloads — so RunPod pays for a cache miss, and invests in host-affinity
    placement. Beam bills neither the machine wait nor the image pull. Hugging
    Face bills initialization. Each boundary creates exactly the incentive you
    would predict.

13. **The 50 % batch discount is unanimous, and priority is its inverse.**
    Anthropic, OpenAI, Groq, Fireworks and Together all land on exactly **50 %**
    (Novita's "50 % off both directions" is arithmetically identical). Five
    independent operators converging says what deferrable work is worth:
    roughly half. Groq's **24 h–7 day** window — three to seven times longer
    than anyone else's — suggests the discount could go further if the deferral
    does. On the other side, Anthropic sells *reserved* ITPM/OTPM commitments
    with a duration and a **99.5 % uptime target**, priced per token-type by
    **burndown rate** (cache read **0.1**, cache write **1.25**/5-min or
    **2.00**/1-hour TTL), while Fireworks sells a flat **1.25×** premium across
    every model — the *scheduler-weight* version of the same product. Only the
    first survives a genuine capacity crunch.

14. **Custom silicon changes which phase is expensive, and the price tables say
    so.** Cerebras is the only operator in the market whose **output price is
    within ~1.5× of its input price** ($0.99 in / $1.49 out on a 27B dense —
    1.51×);
    everyone on GPUs charges 4–12× more for output. That ratio is the
    architecture showing through the P&L: with 44 GB of SRAM at **21 PB/s**,
    decode stops being the expensive phase — and prefill, correspondingly,
    costs **6.6–10×** the GPU market's input price. Groq's LPU premise
    (**~80 TB/s** on-chip SRAM, statically compiled schedules) is the same
    shape. Neither publishes an aggregate per-chip throughput figure; both
    publish **per-user** token rates (Cerebras ~3,000 tok/s on gpt-oss-120b,
    Groq 500–1,000 tok/s). **⚠️ Those two number families are not comparable to
    this repo's aggregate tok/s/GPU columns and must never share a table.**

15. **Wafer is the shape of the thing a bare-metal operator must do in-house.**
    It sells neither capacity nor a model but *continual optimisation* of
    someone else's capacity — *Find Bottlenecks → Try Many Paths → Ship
    Measured Winner* across "batching, decoding, quantization, engines, kernels,
    and hardware" [src](https://wafer.ai) — heavily on AMD. Its published
    MI355X numbers (Kimi-K3 at **952 tok/s/node**, **48 tok/s/$** vs **33** for
    B300 [src](https://wafer.ai/blog/kimi-k3-mi355x)) are among the few public
    AMD-vs-Blackwell inference comparisons at this model scale. **Read the
    tok/s/$ figure with its two missing halves:** on the same benchmark the
    B300 is *faster* in absolute terms — **1,568 tok/s/node, 172 tok/s
    single-stream** vs the MI355X's 952 / 118 — and the dollar denominator is
    an assumed **$2.50/GPU-hour for MI355X against $6.00 for B300**. The
    comparison is a price assumption plus a throughput deficit, not a
    throughput win (verified against the source 2026-09-19).

**What does not transfer, in one line each:** multi-cloud capacity arbitrage
(Modal's LP/GLOP solver, Baseten's MCM across 20+ clouds); gVisor-grade
sandboxing of untrusted tenants (measured cost: vLLM host CPU **140 % →
250 %**); spot/preemptible capital structure; fleet-wide weight caching that
needs a fleet; 99.9 % multi-DC uptime that needs two facilities each sized for
full load; and everything downstream of 44 GB of SRAM at 21 PB/s.

### 1.1 Methods, and which providers evidence each

| Method / architecture | Who does it (published) | Best published evidence | Where implementing it is covered |
|---|---|---|---|
| **Prefill/decode disaggregation** | DeepSeek, Moonshot, Together (3-tier CPD), Baseten, Fireworks, Anyscale/Ray, Cerebras (with Trainium), Perplexity (stated priority) | DeepSeek EP32 prefill vs EP144 decode; Baseten **up to 6× TPS/GPU**; Together CPD **+35–40 % sustainable throughput** | [`02`](02-serving-stack-and-routing.md) §1, §3; [`09`](09-reference-architectures.md) §4; [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) §3 |
| **KV/prefix-cache-aware routing** | Baseten, Together (reuse-classified routing), Anyscale/Ray (prefix-aware), Cerebras (auto 128-token blocks), Cloudflare, Fireworks | Baseten **34–62 %** in production; DeepSeek **56 %** production hit rate | [`02`](02-serving-stack-and-routing.md) §3 |
| **Tiered KV beyond HBM (DRAM/SSD/RDMA)** | Moonshot (Mooncake), Together (HBM→DRAM→RDMA), Baseten (KV Block Manager), SambaNova (SRAM/HBM/DDR) | Mooncake **up to 525 %** under SLO (sim), **75 % more requests** (prod) | [`04`](04-throughput-and-utilization.md) §4 |
| **Content-addressed, tiered, peer-fanned weight store** | Baseten (BDN), Modal (lazy FUSE index), RunPod (cached models), Cerebrium, Together (fleet cache), Beam (Volumes) | BDN **>2 GB/s**, **2–3×**, **50 replicas = 1× origin BW**; Modal **~2.5 GiB/s**, 5 MB index | [`06`](06-cold-start.md) §2; [`01`](01-bare-metal-cluster.md) §5 |
| **CPU+GPU memory snapshot / restore** | Modal (CPU+GPU), Beam (`checkpoint_enabled`), Cerebrium (beta, explicit trigger), RunPod (FlashBoot ⚠️ unpublished) | Modal **vLLM Qwen2.5 45 s → 5 s**, Parakeet **20 s → 2 s**, ViT **8.5 s → 2.25 s** | [`06`](06-cold-start.md) §4 |
| **Compile / CUDA-graph cache persistence** | Replicate (`torch.compile` cache), Cloudflare (graph per batch size, on demand) | Replicate 2025-09-08 feature; Infire's per-batch-size graphs | [`06`](06-cold-start.md) §3 |
| **Concurrency/queue-depth autoscaling** | Together, RunPod, Beam, Cerebrium, Modal, Fireworks, Baseten; HF as the counterexample | Together's three-policy replay: only `inflight_requests` scaled | [`05`](05-autoscaling-and-predictive-scaling.md) §2, §3 |
| **Warm floor *plus* warm buffer** | Modal (`buffer_containers`), fal (`concurrency_buffer`, `_perc`), Together (proactive pre-warming) | Modal docs; Beam's 301.1 s billing example for the cost of the floor | [`05`](05-autoscaling-and-predictive-scaling.md) §5; [`06`](06-cold-start.md) §5 |
| **Host-affinity placement to resident weights** | RunPod (cached models), Cerebrium (region-cached storage) | RunPod: cold starts "to just a few seconds, even for large models" | [`05`](05-autoscaling-and-predictive-scaling.md) §7 |
| **Speculative decoding as a product** | Baseten (Speculation Engine, suffix automaton + MTP), Together (ATLAS, custom speculators), Fireworks (FireOptimizer), fal (DSpark), Novita (DSpark in vLLM), Cloudflare (prompt-lookup), Cerebras | Together **custom speculators from 20M tokens of your own traffic**, **1.23–1.45×** over base; Fireworks **76 % vs 29 % acceptance** | [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) §2 |
| **Multi-LoRA on a shared base** | Fireworks (thousands per cluster), Anyscale/Ray, Cloudflare, Cerebras (⚠️ mechanism unpublished) | Fireworks **~90 % of base-model speed** serving hundreds of adapters | [`02`](02-serving-stack-and-routing.md) §5 |
| **Quantization as a disciplined ladder** | Fireworks (L1–L4 + KLD bar), Cerebras (storage-only), Wafer (MXFP4/NVFP4), fal (NVFP4/MXFP8), Together (FP16→FP8→FP4 worth 20–40 %) | Fireworks: KLD 0.00286 (L1) → 0.00796 (L4), bar **KLD < 0.007** | [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md); [`07`](07-cost-engineering.md) §5 |
| **Admission control priced on the scarce phase** | Cerebras (uncached vs total token buckets), Moonshot (predictive early rejection), Together (queue-class isolation), Baseten (priority queues) | Cerebras dual-bucket rate limits; Mooncake early rejection | [`03`](03-concurrency-and-admission-control.md) §2–§4 |
| **Two request paths: durable queue vs direct routing** | RunPod (queue-based vs load-balancing endpoints), Together (batch/real-time/untrusted queues), all Batch APIs | RunPod `/run` vs load-balancing endpoints; 50 % batch discount across five vendors | [`09`](09-reference-architectures.md) §6; [`04`](04-throughput-and-utilization.md) §3 |
| **Standardised model container with a `setup()`/`run()` boundary** | Replicate (Cog), Baseten (Truss), Beam (`on_start`), Cerebrium (`cerebrium.toml`), fal (`fal.App.setup()`) | Cog: OCI image + OpenAPI schema from type hints + Rust/Axum server | [`09`](09-reference-architectures.md) §2 |
| **Active GPU health checking, drain-and-reimage** | Modal (DCGM/GPUBurn/NCCL, Xid+ECC), Together (health checks *between* workloads, "detect fast, drain, replace") | Modal: *"we almost never have GPU problems slip through"*; Baseten designs against **~1 GPU failure / 50,000 GPU-hours** | [`01`](01-bare-metal-cluster.md) §6; [`08`](08-reliability-and-operations.md) §2–§3 |
| **Own engine** | Cloudflare (Infire), fal (fal Inference Engine™), Together (TIE 2.0), Fireworks (FireAttention), Groq/Cerebras/SambaNova (compilers) | Infire: **+6.6 % req/s, −5.6× CPU** vs vLLM | [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) §9 |
| **Open MoE all-to-all kernels** | Perplexity (`pplx-kernels`, `pplx-garden`/`fabric-lib`) | EP8–EP128 dispatch/combine over NVLink/IBGDA/IBRC/EFA; **<2 s** weight transfer | [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) §3; [`01`](01-bare-metal-cluster.md) §4 |

---

## 2. Master comparison

All figures as published on **2026-09-19**. "n/p" = not published; "⚠️" = the
gap is stated in the profile; "—" = not applicable to this business model.

### 2.1 All 28 operators, one row each

| Provider | Offering | Hardware | Isolation / orchestration | Engine | Cold-start tech + published time | Autoscaling model | KV / prefix caching | PD disagg | Pricing model | Published throughput / latency |
|---|---|---|---|---|---|---|---|---|---|---|
| **Modal** (§3A.1) | serverless functions/containers, sandboxes, notebooks | none owned; AWS/GCP/Azure/OCI, **20k+ concurrent GPUs**, T4→B300 | **gVisor** + custom scheduler (not k8s); LP/GLOP capacity solver | engine-agnostic (bring vLLM/SGLang/TRT-LLM) | lazy content-addressed FUSE + **CPU CRIU + GPU CUDA checkpoint (drv 570/575)**; **~2,000 s → ~50 s**; vLLM 45 s → 5 s | queue + concurrency set-point; `target_inputs` vs `max_inputs`; `scaledown_window` 60 s | via your engine | your problem | per **second**, GPU+CPU+RAM separate; H100 **$3.95/h** | 2.5 GiB/s FS; container boot ~1 s; snapshots 2.25–10× |
| **fal** (§3A.2) | media model API (output-priced) + private serverless + enterprise | none owned; **AWS partnership**; A100/L40/H100/RTXPRO6000/H200/B200 | runner lifecycle state machine; substrate n/p | **fal Inference Engine™** (own); **SGLang** for the LLM path | image cache + `keep_alive`; **⚠️ no numbers, no snapshot** | `min/max_concurrency`, `concurrency_buffer(_perc)`, `scaling_delay`; signal n/p | n/p | n/p | per second **and** per output image / video-second | MXFP8 **6+ TB/s**; EVT fusion **1.28×**; Ideogram v4 **2.75 s → 0.44 s**; DSpark **830–1000 tok/s**, acceptance 4.6 |
| **Wafer** (§3A.3) | continual optimisation of *your* capacity; Dedicated endpoints | none owned; optimises on partners' AMD MI300X/MI350X/MI355X + NVIDIA | n/p | optimises **SGLang** etc.; agentic profile-guided kernel loop | n/a (not their domain) | n/p (tunes batching/caching/routing/decode) | "caching, routing, decode decisions" | ⚠️ | n/p (enterprise contract) | Kimi-K3 **952 tok/s/node**, **48 vs 33 tok/s/$** vs B300; Kimi 2.5 **11.33×**; KDA kernel **11.65×** |
| **Baseten** (§3A.4) | dedicated deployments + per-token Model APIs + training | none owned; **20+ clouds**; T4→B200 180GB | **Kubernetes + MCM** hub-and-spoke global control plane | **TRT-LLM + SGLang + vLLM under NVIDIA Dynamo + NIXL** | **BDN**: NVMe → peer cache (hash ring) → origin; **>2 GB/s, 2–3×**; no snapshot | traffic (leading) + utilisation (lagging); per-stage for pipelines | **KV Block Manager**, offload CPU/SSD/remote; **KV-aware routing 34–62 %** | **yes, up to 6× TPS/GPU** | per **minute**; H100 **$6.50/h**; Model APIs per token | **−50 % TTFT / −34 % TPOT / +61 % throughput**; Speculation Engine **+40 %** |
| **RunPod** (§3A.5) | Pods + Serverless endpoints + Clusters + Flash SDK | Secure Cloud + Community Cloud; A4000→B200 | n/p; containers from Docker Hub/GitHub/Hub | ships a **vLLM worker**; otherwise BYO | **FlashBoot** (⚠️ unpublished) + cached models (host affinity) + `VolumeCache`; "a few seconds" | **queue delay (4 s)** *or* **request count** `ceil((q+r)/scaler)` | via engine | no | per **second**; H100 **$4.18/h (docs rate table) vs $4.79/h (pricing page) ⚠️ conflict**, $3.49 pod | rate tables only; ⚠️ no benchmarks |
| **Replicate** (§3A.6) | public model marketplace + private Deployments + **Cog** | A100/H100/T4/L40S; cloud n/p | n/p | per-model optimisation (FLUX, Taylor Seer); Cog = OCI + Rust/Axum server | `torch.compile` cache; **fine-tuned models <1 s (2023)** ⚠️ mechanism n/p | traffic ⚠️; min/max instances | n/p | no | per second of prediction time | sub-1 s fine-tune boots; otherwise sparse |
| **Beam** (§3A.7) | OSS serverless cloud (endpoints, queues, sandboxes) + reserved machines | serverless T4/A10G/4090/5090 **+ H100 PCIe ($3.50/h)** ⚠️ (the docs say datacentre parts are reserved-only; the pricing page lists H100 PCIe under Serverless); H100/H200/A100/L40S on-demand reservations | n/p; containers "under a second" | BYO | Volume `cache_dir` + `on_start` + **`checkpoint_enabled`** (snapshot at end of `on_start`) ⚠️ no numbers | `QueueDepthAutoscaler(min,max,tasks_per_container)`; `workers=N` vertical | via engine | your problem | per second; **image pull and machine wait not billed**; keep-warm 180/10/600 s | containers <1 s; on-demand **A100-80 $1.36/h, H100 PCIe $1.83/h** (pricing page, 2026-09-19; the docs sample's $1.49 / $3.63 is stale) |
| **Inferless** (§3A.8) | serverless endpoints from Git/HF/Docker | ⚠️ no published GPU list | n/p | BYO; platform-level **dynamic batching** (`BATCH_SIZE`/`BATCH_WINDOW`) | ⚠️ none published; NFS volumes; builds ~5–10 min | min/max replicas, Scale Down, Container Concurrency 1–100; signal ⚠️ | n/p | no | ⚠️ n/p | ⚠️ none |
| **Northflank** (§3A.9) | general PaaS + GPU, **BYOC on any Kubernetes** | 18+ GPU types incl. H100/H200/B200 + TPUs; AWS/GCP/Azure/Oracle/CoreWeave/any k8s | **Kubernetes, openly** | none; claims "automatic batching" ⚠️ | "model caching" ⚠️ no mechanism, no numbers | traffic ⚠️, "intelligent request queuing" | n/p | no | per second, no hourly minimums ⚠️ rate table n/p | ⚠️ unqualified ("sub-100 ms", "2–4× via time-slicing") |
| **Cerebrium** (§3A.10) | real-time serverless GPU/CPU apps from `cerebrium.toml` | H100/A100/L40S+; `interruptible` vs `protected` (**2×**) | sidecar at `169.254.169.253:8234`; substrate n/p | BYO (vLLM, LitServe) | `/persistent-storage` (region-cached) + **CPU+GPU checkpointing (beta, explicit POST trigger)**; HF load **"40+ s at ~2 GB/s"** | configurable: concurrency util / RPS / CPU / memory; `cooldown` | via engine | your problem | per second; **cold start not billed, init billed** | ⚠️ no before/after checkpoint numbers |
| **Lambda** (§3A.11) | ⚠️ **Inference API winding down**; now GPU instances/clusters | **owns metal** (neocloud) | — | — | — | — | — | — | GPU-hour (see [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md)) | — |
| **Nebius Token Factory** (§3A.12) | shared per-token + dedicated endpoints + fine-tuning | **owns datacenters** — Finland, France, US | n/p | n/p; 60+ open models | n/p (absorbed by the provider) | "custom autoscaling" on dedicated ⚠️ | n/p | n/p | per token; **99.9 % SLA** on dedicated | "sub-second" ⚠️ unqualified |
| **HF Inference Endpoints** (§3A.13) | managed single-model endpoints | rented AWS/GCP/Azure; T4→H200, inf2, TPU v5e | per-customer instance; substrate n/p | **vLLM, TGI, SGLang, TEI, llama.cpp** | ⚠️ none; **returns 502 during init**, no built-in queueing | **GPU/CPU util ≥80 % over 1 min** (default); pending-requests **>1.5/replica over 20 s** (beta) | via engine | no | per hour, metered per minute; **initialization billed**; H100 **$10/h** | thresholds table only |
| **Anyscale / Ray Serve LLM** (§3A.14) | managed Ray in **your** cloud; Ray Serve LLM is open source | BYOC — whatever is in your account | **Ray** actors/deployments/placement groups (often on KubeRay) | **vLLM, SGLang** under Ray | ⚠️ not a distinct feature | `autoscaling_config` min/max + node-pool autoscaling | **prefix-aware routing** | **yes** | management fee over your cloud spend ⚠️ | ⚠️ none published |
| **Cloudflare Workers AI** (§3A.15) | edge per-token/per-unit API bound to Workers | **owns metal** — GPUs in **180+ cities**, 12th-gen servers, H100 NVL | **Omni**: namespaces + cgroups, `uv` venvs, FUSE `/proc/meminfo`, CUDA stub forcing **unified memory, ~400 % over-commit**, **13 models/GPU** | **Infire** (own, Rust) + vLLM + Python | swap-in over PCIe, not boot: **~156 ms for a 5 GB model** | platform-internal per PoP | **paged KV + per-head compression, 8× at >95 % quality** | no | **Neurons**, $0.011/1k; per-token equivalents per model | Infire **40.91 req/s, 17,224 tok/s, 25 % CPU** vs vLLM 38.38 / 140 %; **80+ TPS at 8B**, TTFT ~300 ms |
| **Together AI** (§3B.1) | per-token + dedicated endpoints + containers + clusters + batch | **owned and operated**; A100→H100→H200→HGX B200→GB200 NVL72 (36k) | multiple queues by traffic class (batch/real-time/untrusted); canary/blue-green/rollback; orchestrator n/p | **own (Together Inference Engine 2.0)** + TKC kernels + ATLAS + CPD | **86 s (9B) / 145 s (18B FT) / scale-up 2.5 min / deploy 2–14 min**; **"4× faster warm starts"** via fleet-wide weight cache | **8 signals**, default `inflight_requests`=8; `desired=ceil(obs/target×cur)`; up 60 s / down 300 s; **no auto-wake from zero** | three-level: **HBM → host DRAM → cluster RDMA**; reuse-classified routing | **yes — CPD, 3 tiers** (pre-prefill/prefill/decode), **+35–40 %** | per token; dedicated per GPU-hour (H100 $3.99, B200 $8.99); batch −50 % | DeepSeek-R1-0528 on B200 **334 TPS**; ATLAS **500 tok/s** (bs=1); **>400T tokens/month** |
| **Fireworks AI** (§3B.2) | serverless (Std/Priority/Fast) + on-demand per GPU-second + enterprise | A100/H100/H200/B200/B300 + **AMD MI325X/MI350X**; ownership n/p | replica-based, region-pinned; orchestrator n/p | **own (FireAttention V4, NVFP4)** + FireOptimizer | ⚠️ no numbers; scaled-to-zero returns **503 `DEPLOYMENT_SCALING_UP`, not queued** | up **30 s** / down **10 min** / zero **1 h**; 5 load signals, max wins; auto-delete at 7 d | cached-input pricing tier; caching per deployment | **yes — an axis of 3D FireOptimizer** | per token (**Priority = flat 1.25×**, US-only 1.5×); on-demand H100 $8.00/h; batch −50 % | **>250 tok/s DeepSeek V3** (8×B200); **3.5×** vs SGLang FP8 on H200; MiniMax M3 kernels **1.9–2.4×** |
| **DeepInfra** (§3B.3) | per-token + dedicated instances + GPU rental | A100/H100/H200/B200/B300; location n/p | "private deployments" ⚠️ | **n/p** — publishes no engine work; runs **multiple precisions per model** | **n/p** | exists, minute-granular billing; params n/p | n/p | n/p | per token (**market floor**); dedicated H100 **$2.20/h**, H200 $2.69/h | **none**; uptime via OpenRouter 82–100 % |
| **Novita AI** (§3B.4) | 200+ models per token + on-demand GPUs + agent runtimes | **H200 + Blackwell (B200/B300)**, SM90 and SM100 paths | n/p | **vLLM + SGLang with own upstream kernels** (Chord W4A16 MoE) | n/p | n/p | via engine | n/p | per token; **batch −50 % both directions** | Chord **B300 decode 1.81–2.15×**; DSpark **2.17→2.55×**; GLM4-MoE **−65 % TTFT** |
| **Groq** (§3B.5) | per-token GroqCloud + batch + sovereign deployments | **LPU** (SRAM, ~80 TB/s, static schedule) **→ NVIDIA Groq 3 LPX + Vera Rubin NVL72 (2026-08)** | static compile-time mapping; no runtime multiplexing to speak of | **Groq Compiler + RealScale**; TruePoint Numerics ⚠️ no technical page | n/a — weights SRAM-resident; catalogue is small and curated | n/p; rate limits are the capacity surface | n/p | n/p | per token only; batch **−50 %, 24 h–7 days** | **500 tok/s** gpt-oss-120b, **1,000** gpt-oss-20b (per stream); 3,400 tok/s Gemma 4 31B on the new NVIDIA platform |
| **Cerebras** (§3B.6) | per-token public endpoints + dedicated endpoints | **owns CS-3/WSE-3** (900k cores, **44 GB SRAM, 21 PB/s**), CS-4 (53.5 PB/s ⚠️ — the chip page describes CS-4 as *"powered by three WSE-3T processors"* and does not restate the bandwidth) | shared public vs reserved private instance; scheduler n/p | own; **layer-boundary pipeline across wafers**, TP/EP inside a wafer; BTA for MoE | n/a — SRAM-resident; two public models only | n/p; **dual-bucket rate limit (uncached vs total tokens; total TPM defaults to 3× uncached)** | **automatic, 128-token blocks, exact prefix, TTL ≥5 min** | **yes — Trainium for prefill, CS-3 for decode**, 5× throughput | per token; **out ÷ in = 1.5–2.1×** (the market is 4–12×) | **~3,000 tok/s** gpt-oss-120b, **~1,850** qwen-3.8-27b (per user); disagg **1,200 tok/s**, **4.5× p95** |
| **SambaNova** (§3B.7) | SambaCloud per token + on-prem SN40L systems | **SN40L RDU**, three-tier **SRAM / HBM / DDR** (MICRO 2024) | n/p | own dataflow compiler; n/p beyond the paper | **15–31× faster model switching** via tier promotion; up to **19×** footprint reduction | n/p | cached input on **exactly one model** (MiniMax-M2.7) | n/p | per token; DeepSeek V3.1/V3.2 at **10–20× market** | **2–13×** vs baselines, **3.7×** vs DGX H100 (paper); **no tok/s published** |
| **Perplexity** (§3B.8) | own answer engine + Sonar API | H100-class (`9.0a+PTX`), **AWS EFA** as a first-class transport | n/p | **own MoE all-to-all kernels, open** (`pplx-kernels`, `pplx-garden`) | `fabric-lib` **<2 s weight transfer** (RL post-training, not serving) | n/p | n/p | stated operational priority | ⚠️ not retrievable | EP8–EP128 dispatch/combine benchmarks in-repo |
| **OpenRouter** (§3B.9) | one API over ~447 models / several hundred endpoints ⚠️ (count not re-verified 2026-09-19; the `/models` endpoint paginates) | **none** | n/a | n/a — the routing policy *is* the product | routing to an already-warm provider **is** cold-start avoidance *(inferred)* | n/a | exposes `input_cache_read` pricing per endpoint | n/a | passthrough + 5.5 % on credit purchases; BYOK 5 % above $25k/mo | per-endpoint uptime (30 s / 30 m / 1 d); ⚠️ latency and throughput fields null |
| **DeepSeek** (§3B.10) | first-party per-token API, peak/off-peak | **H800 ×8/node**, 226.75 avg → 278 peak nodes in 24 h | n/p; node-granular elasticity | own; **EP32 prefill / EP144 decode**, dual-batch overlap, 5-stage decode pipeline | n/p | node-granular; nighttime discounting as demand shaping *(inferred)* | **56 % input-cache hit rate in production**; cached input at **2 %** of uncached | **yes — the reference implementation** | per token, off-peak $0.15/$0.60 on V4.1-Flash | **73.7k tok/s/node prefill, 14.8k decode**; 20–22 tok/s/user; **545 % theoretical margin** |
| **Moonshot (Kimi)** (§3B.11) | first-party Kimi API (+ 20 resale endpoints) | ⚠️ "GPU cluster" only | n/p | **Mooncake**: KVCache-centric scheduler, disaggregated clusters | n/p | n/p | **KVCache as cluster-wide tiered store over idle CPU/DRAM/SSD** | **yes** | per token; K3 $3.00/$0.30/$15.00, declares **mxfp4** | **up to 525 %** under SLO (sim); **75 % more requests** (prod) |
| **Mistral** (§3B.12) | La Plateforme per token + open weights + partner clouds | n/p | n/p | **none of its own** — documents vLLM/TensorRT/TGI/SkyPilot paths | n/p | n/p | via engine | via engine | per token; Priority Tier page ⚠️ 404 | n/p |
| **Anthropic / OpenAI / Google** (§3B.13) | first-party frontier APIs; the published surface is the **commercial interface to the scheduler** | n/p | n/p | n/p | n/p | n/p | Anthropic prices cache **writes** at 1.25–2.0× and reads at **0.1×** | n/p | **batch −50 %** (all three); Anthropic Priority = reserved ITPM/OTPM + **99.5 %**; OpenAI Flex = batch-priced, preemptible (**429, no charge**); Google GSUs ⚠️ tables not retrievable | n/p |

### 2.2 Autoscaling parameters — exact names, signals and defaults

| Provider | Primary signal | Scale-to-zero | Key parameters (exact names) | Notable defaults |
|---|---|---|---|---|
| **Together** | `inflight_requests` (default, target **8**); also `ttft`, `e2e_latency`, `gpu_utilization`, `token_utilization` — 8 metrics in 3 families | `min_replicas: 0` requires `max_replicas: 0` — **no auto-wake** | `min_replicas`, `max_replicas`, `scale_up_window`, `scale_down_window`, metric + target | up **60 s**, down **300 s**; proportional control `ceil(observed/target × current)` |
| **Fireworks** | general load target (0–1), tokens/s/replica, prompt-tokens/s/replica, RPS/replica, concurrent requests/replica — **max replica count across all targets wins** | yes, after **1 h** idle (min 5 min); deployment auto-deleted after **7 d** | `min_replicas`, `max_replicas`, autoscaling policy, `--accelerator-count` | up **30 s**, down **10 min**; 503 (not queued) at zero |
| **Modal** | input queue + concurrency set-point | default yes | `min_containers`, `max_containers`, `buffer_containers`, `scaledown_window`; `@modal.concurrent(max_inputs, target_inputs)` | `scaledown_window` **60 s** (max 20 min); 4,000 containers/Function cap |
| **fal** | ⚠️ not documented | yes | `min_concurrency`, `max_concurrency`, `concurrency_buffer`, `concurrency_buffer_perc`, `scaling_delay`, `keep_alive`, `request_timeout`, `regions`; `max_multiplexing` | **runtime-tunable vs code-versioned knob split** |
| **Baseten** | traffic (**leading**) + utilisation (**lagging**) | supported, discouraged for latency-sensitive | min/max replicas, autoscaling window, scale-down delay, concurrency target | per-stage scaling for pipelines; same-cluster (**10 ms** vs 50 ms inter-cluster) |
| **RunPod** | **queue delay** *or* **request count** `ceil((queued+running)/scaler)` | flex workers | active workers, max workers, idle timeout, execution timeout, job TTL, GPU priority list | active **0**, max **3**, idle **5 s**, exec **600 s**, TTL **24 h**, queue delay **4 s**; request-count **recommended for LLMs** |
| **Beam** | **queue depth** | yes | `QueueDepthAutoscaler(min_containers, max_containers, tasks_per_container)`, `workers`, `keep_warm_seconds` | keep-warm **180 s** endpoints / **10 s** task queues / **600 s** pods |
| **Cerebrium** | **configurable**: concurrency utilisation, RPS, CPU, memory | `min_replicas=0` | `min_replicas`, `max_replicas`, `cooldown`, `replica_concurrency` | **`replica_concurrency` = 1 on GPU**, 100 on CPU ← must raise for LLMs |
| **HF Endpoints** | **GPU/CPU utilisation ≥ 80 %** over 1 min; pending-requests beta | after **15 min** idle | min/max replicas, threshold | scale-up eval 1 min, scale-down 2 min + **300 s** stabilisation; beta **>1.5 pending/replica over 20 s**; **502 during init** |
| **Inferless** | ⚠️ | yes | min/max replicas, Scale Down, Inference Timeout, Container Concurrency (1–100), `BATCH_SIZE`/`BATCH_WINDOW` | ⚠️ |
| **Replicate** | traffic ⚠️ | yes | min instances, max instances | ⚠️ |
| **Northflank** | traffic ⚠️ | yes, "with intelligent request queuing" | ⚠️ | ⚠️ |
| **Anyscale / Ray** | Serve replica autoscaling + node pools | yes | `autoscaling_config` (min/max replicas), `tensor_parallel_size` | ⚠️ |
| **DeepInfra** | exists; params n/p | n/p | n/p | minute-granular billing, "no idle GPU charges" |
| **Cloudflare / Groq / Cerebras / SambaNova / Nebius** | platform-internal | n/a | n/a — the user-visible control is the **rate limit** | Cerebras: dual-bucket (uncached vs total tokens), continuous token-bucket replenishment |

### 2.3 Pricing, normalised to $/GPU-hour where published

| Provider | Billing unit | H100 80GB | H200 | B200 | A100 80GB | Notes |
|---|---|---:|---:|---:|---:|---|
| **fal** (discounted) | per second | **1.89** | 2.10 | 3.49 | — | list 4.50 / 4.50 / 6.25; B300 8.50→4.49 |
| **DeepInfra** (dedicated) | per hour, minute-metered | **2.20** | 2.69 | **3.69** | 0.89 | the cheapest published dedicated rate in this study; B300 **4.89** (both added 2026-09-19 from the live pricing page) |
| **RunPod** Pods (Secure) | per second | 3.49 SXM / 2.89 PCIe | 4.59 | 6.79 | 1.59 | B300 7.89 |
| **Beam** (on-demand) | per second | **1.83** (PCIe) | 2.09 | — | **1.36** | ⚠️ **corrected 2026-09-19** from 3.63 / 1.49 (stale docs sample) against [beam.cloud/pricing](https://www.beam.cloud/pricing); serverless H100 PCIe is **3.50**; L40S 0.76, 5090 0.72, 4090 0.44 |
| **Modal** | per second | **3.95** | 4.54 | 6.25 | 2.50 | B300 7.10; CPU and memory billed separately |
| **Together** (dedicated) | per hour | **3.99** (promo, from 5.49) | 5.99 (cluster) | 8.99 | 2.40–2.59 ⚠️ | clusters H100 3.99 / H200 5.99 / B200 8.19; reserved 3.19–7.99 |
| **RunPod** Serverless | per second | 4.18 / **4.79** ⚠️ | 5.58 / **5.93** ⚠️ | 8.64 | 2.74 / **2.72** ⚠️ | flex; **two RunPod sources disagree** — the serverless docs rate table (`$/s`) vs [runpod.io/pricing](https://www.runpod.io/pricing) (checked 2026-09-19). B300 serverless **9.98**. Premium over Pods is **20 % on the docs rates, 37 % on the pricing page** |
| **Baseten** | per **minute** | **6.50** | — | 9.98 | 4.00 | H100 MIG 40GB 3.75 |
| **Fireworks** on-demand | per GPU-second | **8.00** | 8.00 | 13.00 | — | B300 15.00, GB300 20.00 |
| **HF Endpoints** | per hour, metered/min | **10.00** (GCP) | 5.00 (AWS) | — | 2.50 | initialization **is** billed |
| **Cerebrium** | per second | ⚠️ | — | — | ⚠️ | A10 ≈1.10; `protected` tier **2×**; cold start **not** billed, init **is** |
| **Replicate / Northflank / Inferless / Nebius / Novita / Wafer** | per second / per token / ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | rate cards not on the fetched pages |
| **Cloudflare** | Neurons, $0.011/1k | n/a | n/a | n/a | n/a | per-token equivalents published per model |
| **Groq / Cerebras / SambaNova** | per token only | n/a | n/a | n/a | n/a | no chip-hour product publicly priced |

Cross-check every row against
[`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) before
using it in a cost model; that document, not this one, is the pinned price
source for [`../METHODOLOGY.md` §6](../METHODOLOGY.md).

Three things fall straight out of this table:

1. **The spread on identical silicon is ~5×** ($1.89 to $10.00 for an H100
   hour) — not a hardware difference, but how much stack the provider wraps
   around it and how it sources capacity.
2. **Serverless costs ~20–37 % over rental** (RunPod's own Serverless-vs-Pods
   spread, the only clean same-vendor comparison available). ⚠️ **Corrected
   2026-09-19:** the figure depends on which RunPod page you read — the
   serverless docs rate table gives H100 $4.18 vs $3.49 Pod (**+20 %**), the
   public pricing page gives $4.79 vs $3.49 (**+37 %**). Take **~20–37 %** as
   the market price of scale-to-zero plus a queue; the single "~20 %" figure
   previously printed here rested on the lower of two conflicting vendor
   sources.
3. **DeepInfra retails H100 at $2.20/h and H200 at $2.69/h — 0.67–0.69× the
   cheapest reputable on-demand price in
   [`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)** (H100 `low`
   $3.20, H200 `low` $3.99). *(Inferred)* that is only possible on long-term
   committed or owned capacity resold over amortised cost. A self-hoster
   comparing "rent vs DeepInfra dedicated" is comparing against a *better*
   hardware price than this repo's cost grid assumes.

### 2.4 Per-request optimisation matrix

| Provider | Engine(s) | Speculative decoding | Prefix/KV cache | PD disaggregation | Quantization | Custom kernels |
|---|---|---|---|---|---|---|
| **Together** | own (TIE 2.0) | **ATLAS** adaptive + **custom speculators trained on your traffic** (1.23–1.45× over base); MagicDec/Sequoia/Medusa lineage | 3-level HBM→DRAM→RDMA; reuse-classified routing | **yes — CPD, 3 tiers, +35–40 %** | FP16→FP8→FP4 worth **20–40 %**; QuIP-style incoherence processing | **yes — TKC, ThunderKittens, CUTLASS, Triton** |
| **Fireworks** | own (FireAttention V4) | **FireOptimizer** adaptive drafters, **up to 3×**; 76 % vs 29 % acceptance example | cached-input tier | **yes — 3D FireOptimizer axis** | **NVFP4** (chosen over MXFP4); **L1–L4 ladder with KLD < 0.007 bar**; QAT | **yes — sparse attention on SM100, KV-stationary, 1.9–2.4×** |
| **Baseten** | TRT-LLM, SGLang, vLLM under **Dynamo** + NIXL | **own Speculation Engine** (suffix automaton + MTP), EAGLE-3, DFlash | KV Block Manager; offload CPU/SSD/remote; **KV-aware routing 34–62 %** | **yes, up to 6× TPS/GPU** | MXFP4, NVFP4 discussed | via engines |
| **fal** | **fal Inference Engine™**; **SGLang** for LLM | **DSpark**, acceptance 4.6, **16×** throughput | ⚠️ | ⚠️ | **NVFP4, MXFP8**; quantization-aware distillation | **yes — CuTeDSL, CUTLASS EVT** |
| **Novita** | **vLLM + SGLang** (upstreamed) | **DSpark in vLLM**, window sweep 2.17→2.55× | via engine | n/p | **W4A16 INT4 MoE (Chord)** | **yes, shipped as vLLM/SGLang paths** |
| **Cloudflare** | **Infire** (Rust), vLLM, Python | **prompt-lookup**, +40 % (8B) / +70 % (70B) | **paged KV + per-head compression, 8× at >95 % quality** | no | **none in Infire yet** | CUDA graph per batch size, on demand |
| **Wafer** | SGLang (named); optimises whatever you run | yes (used in the K3 benchmarks) | "caching, routing, decode decisions" | ⚠️ | **MXFP4, NVFP4** | **yes — agentic, profile-guided** |
| **DeepSeek** | own | n/p | **56 % production hit rate**; cached input at 2 % | **yes — EP32 vs EP144** | n/p | dual-batch overlap, 5-stage decode pipeline |
| **Moonshot** | **Mooncake** | n/p | **cluster-wide tiered KVCache + KVCache-centric scheduler** | **yes** | mxfp4 (declared) | n/p |
| **Cerebras** | own | yes (cited in the 3× release) | **automatic 128-token-block prefix cache, TTL ≥5 min** | **yes — Trainium prefill + CS-3 decode** | **storage-only**; activations/attention/KV stay full precision | rewritten MatMul/reduce/broadcast kernels; **BTA** for MoE |
| **Groq** | Groq Compiler + RealScale | n/p | n/p | n/p | **TruePoint Numerics** ⚠️ undocumented | compile-time static scheduling |
| **SambaNova** | own dataflow compiler | n/p | one model only | n/p | n/p | n/p |
| **Perplexity** | own MoE a2a kernels (open) | n/p | n/p | stated priority | n/p | **yes — `pplx-kernels`, EP8–EP128, 4 transports** |
| **Anyscale / Ray** | vLLM, SGLang | via engine | **prefix-aware routing** | **yes** | via engine | no |
| **HF Endpoints** | vLLM, TGI, SGLang, TEI, llama.cpp | via engine | via engine | no | via engine | no |
| **RunPod** | ships a vLLM worker | via engine | via engine | no | via engine | no |
| **DeepInfra** | n/p | n/p | n/p | n/p | **multi-precision endpoints as a product axis** (bf16/fp8/turbo) | n/p |
| **Modal, Beam, Cerebrium, Northflank, Replicate, Inferless, Mistral** | bring your own | via engine | via engine | your problem | via engine | no |

### 2.5 Batch APIs, service tiers and rate limits

| Provider | Batch discount | Window | Limits | Priority / service tiers |
|---|---|---|---|---|
| **Together** | 50 % | 24 h | ≤50,000 requests/batch, 100 MB file, 10 MB/line, **30B tokens enqueued per model**; dedicated endpoints eligible but **not discounted** | queue-class isolation (batch / real-time / untrusted) |
| **Fireworks** | 50 % | ≤24 h, "a few hours" typical | **no upper limit on requests per batch**, datasets <500 MB | **Standard / Priority (flat 1.25×) / Fast**; US-only models **1.5×** |
| **Groq** | 50 % (⚠️ "raised from 25 %" not re-verified; the current batch doc states only *"50% cost discount compared to synchronous APIs"*) | **24 h – 7 days** | ≤50,000 lines, 200 MB per JSONL; charged only for completed requests; 30-day retention | Free / Developer / Enterprise; two Llama models **Enterprise-only, "ContactSales"** |
| **Novita** | 50 % on **both** input and output | n/p | n/p | n/p |
| **Anthropic** | 50 % | "most batches < 1 hour" | asynchronous, poll for status | **Priority / Standard / Batch**; Priority = ITPM+OTPM+duration+model, **99.5 % target**, burndown 0.1× read / 1.25–2.0× write; ⚠️ no longer purchasable as of 2026-09-19 |
| **OpenAI** | 50 % | `24h` | 50,000 requests/batch, 200 MB file, 2,000 batches/hour, **separate rate-limit pool** | **Flex**: batch-priced but synchronous, can return **429 Resource Unavailable, no charge** |
| **Google** | n/p here | n/p | — | **Vertex Provisioned Throughput**, unit = **GSU**, spillover to pay-as-you-go ⚠️ tables not retrievable |
| **Cerebras** | n/p | — | **dual-bucket**: uncached-token limit *and* total-token limit; pre-screened against `max_completion_tokens` | Free Trial / Developer tiers per model |
| **Cloudflare** | Batch API exists | n/p | Neuron quotas, 10,000/day free | — |
| **OpenRouter** | passthrough | — | `:nitro` (throughput + priority tiers) / `:floor` (price + flex tiers) route through to the upstream's own tiers | — |

---

## 3. Provider profiles

Every profile carries the same eleven fields **(a)–(k)**: (a) what they sell,
(b) hardware and where it runs, (c) orchestration and isolation, (d) cold-start
engineering, (e) autoscaling, (f) serving engine and per-request optimisations,
(g) routing / concurrency / limits / SLAs, (h) observability and developer
surface, (i) pricing and what it implies, (j) published numbers, (k) what
transfers to bare metal. A field reading "**Not published.**" or "⚠️ Not
published" means exactly that — the search was made and came back empty.

### 3A — Serverless-GPU and GPU-cloud platforms (they sell GPU-seconds)

---

### 3A.1 Modal

The most thoroughly documented serverless-GPU architecture in public. Modal
publishes engineering detail at a level no competitor matches, which makes it
the reference implementation for the recurring patterns in §1.

**(a) What they sell.** Serverless Python functions and containers with GPUs
attached, plus Sandboxes, Notebooks, and batch/job primitives. No per-token
model API — you bring the code and the model. Billing is per second of
container lifetime [src](https://modal.com/pricing).

**(b) Hardware and where it runs.** Modal owns no datacenter; it aggregates
on-demand capacity from **AWS, GCP, Azure and OCI**, running **over 20,000
concurrent GPUs** and having "launched over four million cloud instances in the
last couple years" [src](https://modal.com/blog/gpu-health) (Jonathon Belotti
and Amy Chang, 2025-12-28). GPU menu: T4, L4, A10, L40S, A100-40GB, A100-80GB,
RTX-PRO-6000, H100, H200, B200, B300; up to 8 GPUs per container for
B300/B200/H200/H100/A100/L4/T4/L40S — "up to 2,304 GB GPU RAM" — and up to 4
for A10 [src](https://modal.com/docs/guide/gpu). B300 requires CUDA 13.1+.
GPU-type fallback lists are honoured in preference order. Their stated
capacity philosophy is explicitly anti-reservation: "People will still make
long-term GPU reservations to get the lowest possible price. But this will not
be the default way to get capacity"
[src](https://modal.com/blog/the-future-of-ai-needs-more-flexible-gpu-capacity)
(Erik Bernhardsson, 2024-10-25).

**(c) Orchestration and isolation.** Not Kubernetes — a custom scheduler.
Containers run under **gVisor**, not runc: "gVisor, an application sandbox that
provides greatly increased host security over the popular runC container
runtime," whose "core security feature" is "application syscall isolation"
[src](https://modal.com/blog/catching-cryptominers) (Belotti, 2024-06-06).
Syscalls are intercepted and serviced by gVisor's **Sentry** userspace kernel;
Modal taps that same stream with an in-house component called **`seccheck`** —
"On container creation Modal's runtime creates a dedicated socket to receive
all runtime syscall trace data from gVisor" — for abuse detection (they were
hunting cryptominers). The same post's code samples use Rust's `tokio`, which
is the only public evidence for the widely repeated "Modal's runtime is written
in Rust" claim: **the runtime being Rust is inferred from those samples, not
stated** ⚠️.

The **capacity scheduler** is separate from the container scheduler and is
published: a linear program solved with Google OR-Tools' **GLOP** simplex
solver, deciding "which kinds of instances we should spin up and spin down"
across clouds, GPU types, CPU and RAM. Solve times are held "to fractions of a
second" via preprocessing heuristics, with instance pruning worth "as much as
an order of magnitude"; the system is "primarily limited by the speed at which
we can acquire and start new instances from cloud providers"
[src](https://modal.com/blog/resource-solver) (Colin Weld, Irfan Sharif,
2025-05-07). ⚠️ **Discrepancy:** the 2024 capacity post describes the same
machinery as "a mixed-integer programming problem every minute"
[src](https://modal.com/blog/the-future-of-ai-needs-more-flexible-gpu-capacity),
where the 2025 post says pure LP/GLOP. Either the formulation was relaxed from
MIP to LP between the posts, or one description is loose. Both are cited; not
resolved.

**(d) Cold-start engineering.** Four stacked mechanisms, and Modal is the only
provider that publishes the contribution of each:

| Layer | Mechanism | Published effect |
|---|---|---|
| Capacity | Cloud buffers — idle GPUs held ready | removes ~10–20 min |
| Filesystem | Lazy-loading content-addressed FUSE image | saves ~1 min |
| CPU state | CRIU-style memory snapshot under gVisor | ~10× on host startup |
| GPU state | CUDA checkpoint/restore | 4–10× on device init |

End to end: "**from approximately 2,000 seconds naïvely to around 50
seconds**", a 40× speedup; a 1 GiB model in vLLM goes **~95.7 s → ~13.8 s**
with snapshots [src](https://modal.com/blog/truly-serverless-gpus) (Charles
Frye, Jonathon Belotti, Erik Bernhardsson, Akshat Bubna, 2026-05-12).

*Filesystem.* "A container image serves as an index; essentially, it's a data
structure that holds all the files and metadata about those files. **This index
is around five megabytes**" and loads in 1–100 ms; file data is content-
addressed and served from a tier chain **memory → SSD → AZ cache → regional CDN
→ blob storage**, at roughly **2.5 GiB/s**, so "a 512 MiB model file [loads] in
roughly 200 ms from disk cache or 300 ms from network"
[src](https://modal.com/blog/jono-containers-talk) (Belotti, 2024-09-08, from
an Aug 2024 NYC Systems talk). Containers themselves "boot in about one second"
[src](https://modal.com/docs/guide/cold-start).

*CPU snapshots.* Built on **CRIU**, but adapted because Modal runs gVisor's
`runsc` rather than `runc` — "gVisor implements a userspace kernel" where CRIU
cooperates with the host kernel. Motivation is Python's filesystem-based import
system: importing torch alone triggers **26,000 syscalls**. Measured: overall
**2.5×**; Stable Diffusion **~13 s → 3.5 s**; a bare `import torch` **~5 s →
1.05 s (p50)** [src](https://modal.com/blog/mem-snapshots) (announced January
2025). At that point GPU state was explicitly *not* captured.

*GPU snapshots.* Released 2025-07-30 (Luis Capelo, Colin Weld), using
**NVIDIA's CUDA checkpoint/restore API, available in driver branches 570 and
575**, integrated with the gVisor runtime. The checkpoint locks CUDA processes
and captures **device memory, compiled kernels, CUDA contexts, streams, graphs,
and memory mappings with preserved addresses**, alongside CPU memory, into a
distributed filesystem. Published: **Parakeet 20 s → 2 s (10×)**, **ViT with
`torch.compile` 8.5 s → 2.25 s**, **vLLM (Qwen2.5) 45 s → 5 s**
[src](https://modal.com/blog/gpu-mem-snapshots).

*API and caveats.* `enable_memory_snapshot=True`; GPU adds
`experimental_options={"enable_gpu_snapshot": True}`. `@modal.enter(snap=True)`
includes a hook in the snapshot, plain `@modal.enter()` excludes it;
`Image.imports()` scopes remote-only imports. Documented limits: "**GPU Memory
Snapshots do not speed up model loading from storage**" — they skip CPU work,
JIT and kernel compilation, not I/O; generally incompatible with multi-GPU;
incompatible with non-CUDA GPU ops; can interact badly with `torch.compile`;
"requires code rewrites for complex inference engines"; and anything depending
on randomness must tolerate identical restored state
[src](https://modal.com/docs/guide/memory-snapshot). Typical speedup quoted as
3–10×, with "the relative speedup is unbounded: the more work you do to create
fewer bytes, the greater it becomes."

**(e) Autoscaling.** Four parameters: `min_containers` (warm floor),
`max_containers`, `buffer_containers` (spare capacity *while active*, to absorb
bursts), `scaledown_window` (idle time before shutdown; **default 60 s, max 20
min**) [src](https://modal.com/docs/guide/scale),
[src](https://modal.com/docs/guide/cold-start). Default behaviour is
scale-to-zero. Decisions are made "quickly and frequently." Hard ceiling of
**4,000 concurrent containers per Function**, plus plan-tier workspace limits.
The documented trade-off is stated in cost terms: bigger warm pools and buffers
cost money to reduce latency risk.

**(f) Serving engine and per-request optimisation.** **None of their own.**
Modal is engine-agnostic — you run vLLM, SGLang, TRT-LLM or raw PyTorch inside
your container. Concurrency is Modal's lever: `@modal.concurrent` with
`max_inputs` (hard per-container ceiling) and `target_inputs` (the autoscaler's
set-point, above which containers burst toward `max_inputs`). Docs explicitly
name "GPU inference using continuous batching frameworks (like vLLM)" as a
prime use case, and warn that sync functions run inputs on threads (must be
thread-safe) while async functions run as asyncio tasks; a single input
cancellation in a sync function "terminate[s] the entire container"
[src](https://modal.com/docs/guide/concurrent-inputs).

**(g) Routing, concurrency, limits, SLAs.** Inputs queue per-Function and are
dispatched to containers with free concurrency slots. Region selection is
supported [src](https://modal.com/docs/guide/region-selection). Published SLA
terms ⚠️ not found; SOC 2 Type 2 and HIPAA posts exist on the blog index.

**(h) Observability / developer surface.** Python-first SDK with decorators;
JS and Go SDKs [src](https://modal.com/blog/sdk-javascript-go); Sandboxes;
Notebooks; `modal.current_input_id()` for per-input log correlation. Their
open-source **GPU Glossary** [src](https://modal.com/gpu-glossary) is a genuinely
useful reference for cluster engineers.

**(i) Pricing and what it implies.** Per **second**, GPU + CPU + memory billed
separately [src](https://modal.com/pricing):

| GPU | $/s | implied $/hr |
|---|---:|---:|
| B300 | 0.001972 | 7.10 |
| B200 | 0.001736 | 6.25 |
| H200 SXM | 0.001261 | 4.54 |
| H100 SXM5 | 0.001097 | 3.95 |
| RTX PRO 6000 | 0.000842 | 3.03 |
| A100 80 GB | 0.000694 | 2.50 |
| A100 40 GB | 0.000583 | 2.10 |
| L40S | 0.000542 | 1.95 |
| A10 | 0.000306 | 1.10 |
| L4 | 0.000222 | 0.80 |
| T4 | 0.000164 | 0.59 |

Plus CPU $0.0000131/core/s (min 0.125 cores), memory $0.00000222/GiB/s, volumes
$0.09/GiB-month with 1 TiB free. Tiers: Starter $0 (100 containers, 10 GPU
concurrency, $30/mo free compute), Team $250/mo (5,000 containers, 50 GPU
concurrency, $100/mo free), Enterprise custom.

*Implication.* Compare these to [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md):
Modal's H100 at ~$3.95/hr sits between neocloud and hyperscaler on-demand,
while Modal is itself *buying* hyperscaler on-demand. That margin is only
payable because containers are billed per second against multi-tenant pooled
demand — i.e. **Modal's business model is arbitrage on the gap between
per-hour instance rental and per-second function billing**, which is exactly
the gap that vanishes on a cluster you already own and have already paid for.
(inferred)

**(j) Published numbers.** Collected above: 40× end-to-end cold start; 2.5 GiB/s
filesystem; 5 MB image index; 20,000+ concurrent GPUs; 4M+ instances launched;
container boot ~1 s; snapshot speedups 2.25×–10×. Fleet reliability: "today, we
almost never have GPU problems slip through and hit user containers," with one
named residual — "Cloud C's L4s flake at CUDA initialization in 0.1 % of cases"
[src](https://modal.com/blog/gpu-health).

**(k) What transfers to bare metal.**

| Transfers well | Does not transfer |
|---|---|
| **GPU + CPU checkpoint/restore.** Driver 570/575 CUDA checkpoint API is available to you too. Highest-leverage item in this document for model-swap and rolling-update latency — see [`06-cold-start.md` §4](06-cold-start.md). | **Multi-cloud LP capacity solver.** You have a fixed node count; the LP degenerates to a bin-packing of models onto known GPUs. |
| **Content-addressed, lazily-mounted image/weight store with a tier chain.** On-prem the chain is NVMe → node cache → rack cache → object store; 2.5 GiB/s is achievable and then some on local NVMe. | **gVisor.** A ~2× syscall-heavy overhead tax (Cloudflare measured vLLM's CPU load going 140 % → 250 % under gVisor) bought entirely to isolate mutually hostile tenants. You have one tenant. |
| **Warm buffer *above* the warm floor** (`buffer_containers` ≠ `min_containers`). Most in-house autoscalers only implement the floor. | **Per-second billing incentives.** Your marginal cost of an idle owned GPU is power, not $3.95/hr, which changes every scale-down threshold. |
| **Active GPU health checking** (DCGM diags, GPUBurn, NCCL all-reduce weekly; Xid and ECC monitoring; drain-and-reimage rather than recover). Directly applicable — see [`01-bare-metal-cluster.md` §6](01-bare-metal-cluster.md). | |

---

### 3A.2 fal (fal.ai)

The generative-media specialist. Unlike everyone else in §3A, fal claims a
**named in-house inference engine** and publishes the kernel-level work behind
it.

**(a) What they sell.** Three things: (1) a per-model, output-priced API over
1,000+ image/video/audio/3D models; (2) **private serverless** — your own code
deployed as a fal App on their GPUs, per-second billed; (3) enterprise
dedicated capacity with bespoke kernel work. Homepage claims: "fal Inference
Engine™ is up to 10x faster", "Trusted by over 1,500,000 developers", "100M+
daily inference calls", "1000s of H100, H200 and B200 VMs", "99.99% uptime",
"1,000+ production ready image, video, audio and 3D models" [src](https://fal.ai/).

**(b) Hardware and where it runs.** Machine types exposed to users
[src](https://fal.ai/docs/documentation/deployment/machine-types):

| Machine type | VRAM | RAM | CPU | BW (as fal states) |
|---|---|---|---|---|
| GPU-A100 | 40 GB | 60 GB | 12 | 2.0 TB/s |
| GPU-L40 | 48 GB | 100 GB | 6 | 0.9 TB/s |
| GPU-H100 | 80 GB | 112 GB | 12 | 3.4 TB/s |
| GPU-RTXPRO6000 | 96 GB | 100 GB | 6 | 1.8 TB/s |
| GPU-H200 | 141 GB | 112 GB | 12 | 4.8 TB/s |
| GPU-B200 | **192 GB** | 210 GB | 19 | 8.0 TB/s |

⚠️ **Two of these conflict with this repo's pinned values**
([`../METHODOLOGY.md` §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)).
fal lists B200 at **192 GB**, where this repo pins **180 GB** as deployed
(see [`../gpus/b200.md` §2](../gpus/b200.md)); and fal lists RTX PRO 6000 at
**1.8 TB/s**, which is the **Workstation** edition's 1,792 GB/s, not the Server
Edition's 1,597 GB/s that [`../gpus/rtx6000-pro.md`](../gpus/rtx6000-pro.md)
pins. Treat fal's table as marketing-tier spec, not a deployment fact; do not
recut any repo number from it.

Capacity sourcing: a **strategic partnership with AWS announced 2026-05-19**,
framed as wanting "capacity [that] exists across regions and compute types
precisely when you need it most"; CTO Gorkem Yurtseven: "The inference problem
for generative media is unlike anything else in AI infrastructure, the
parallelism, the model variety, the latency requirements"
[src](https://blog.fal.ai/fal-and-aws-building-for-the-next-phase-of-generative-media/).
⚠️ No GPU counts, instance families or commitment size disclosed. Funding:
**$140M Series D on 2025-12-09** (Sequoia, Kleiner Perkins, NVIDIA), 70
employees [src](https://blog.fal.ai/our-series-d-scaling-fal/).

**(c) Orchestration and isolation.** ⚠️ Not published. What *is* published is
the user-visible unit: a **runner**, an instance of your App bound to a machine
type, with a full lifecycle state machine —
`PENDING → DOCKER_PULL → SETUP → IDLE → RUNNING → DRAINING → TERMINATING →
TERMINATED`, plus `CRASH_BACKOFF`
[src](https://fal.ai/docs/documentation/deployment/runners). Backoff on failed
startup increments 30 s per retry to a 10-minute cap; hardware-scheduling
failures retry on a fixed 20 s; a successful runner "wakes all waiting
instances" and resets backoff. That a scheduling failure is a distinct,
separately-backed-off state implies a capacity-constrained pool behind a
queue (inferred).

**(d) Cold-start engineering.** fal defines cold start precisely as
`PENDING → IDLE`, i.e. hardware scheduling + `DOCKER_PULL` (skipped if cached)
+ `SETUP` (your `setup()`, model load) + health check
[src](https://fal.ai/docs/documentation/deployment/runners). Controls:
`keep_alive` (idle runner retention), `startup_timeout`, and a documented image
cache. ⚠️ **fal publishes no cold-start latency figures and no snapshot /
checkpoint-restore mechanism** — a real gap versus Modal, Beam and Cerebrium.
Their published latency work is all about making the *forward pass* fast, not
the boot (see (f)).

**(e) Autoscaling.** Two classes of parameter, and the split is unusually
well-designed [src](https://fal.ai/docs/documentation/deployment/scaling-configuration):

*Runtime-tunable* (take effect immediately, "persist across deploys" unless
`fal deploy --reset-scale`): `keep_alive`, `min_concurrency`, `max_concurrency`,
`concurrency_buffer`, `concurrency_buffer_perc`, `scaling_delay`,
`request_timeout`, `regions`.

*Code-specific* (affect correctness; **"they reset to code values on the next
deploy"**): `max_multiplexing` (concurrent requests per runner — requires your
code to handle it), `startup_timeout`, `machine_type`.

Note the design principle worth stealing: **knobs that can only cost money are
hot-tunable; knobs that can break correctness are versioned with the code.**
⚠️ The autoscaler's actual decision function (what `scaling_delay` delays,
what signal drives scale-up) is not documented.

**(f) Serving engine and per-request optimisations.** This is fal's strength,
and it is all diffusion/video-shaped rather than LLM-shaped:

| Work | Detail | Measured | Date, authors |
|---|---|---|---|
| **MXFP8 quantizer in CuTeDSL** | Writes scale factors **directly into the tcgen05 packed layout** Blackwell's block-scaled tensor cores expect, eliminating a separate packing pass for downstream GEMMs; 32-element blocks along K, E4M3 values, UE8M0 scales | **6+ TB/s** effective bandwidth on B200 (counting bf16/fp16 reads + FP8 writes + scale writes); path 1.3 → 3.3 TB/s via K-split for CTA count, then 6+ via TMA bulk loads and 32-bit-aligned scale stores | 2026-01-27, Yigithan Yigit [src](https://blog.fal.ai/chasing-6-tb-s-an-mxfp8-quantizer-on-blackwell/) |
| **Epilogue fusion** | CUTLASS **Epilogue Visitor Trees**; custom visitor for gated-SiLU/SwiGLU, fusing bias, activation and conversion inside the GEMM epilogue. Key trick: reorder weight columns at pack time so gate/activation pairs land adjacent in the GEMM output, avoiding cross-tile sync | **~1.28×**, **166 µs saved per invocation**; Hopper + Blackwell; recommends **NVFP4 block-scaled GEMM** over BF16 on Blackwell in production; applies to Flux, Flux2 and LLaMA-family MLPs | 2026-02-03, Abdussamet Turker [src](https://blog.fal.ai/crafting-efficient-kernels-with-epilogue-fusion/) |
| **Sub-second Ideogram v4** | Stacks four things: **NVFP4** transformer on Blackwell + epilogue fusion (with a "revisit" schedule for `head_dim=256` attention so the epilogue normalises tiles after both halves are computed); **quantization-aware distillation** against a bf16 teacher via straight-through estimators (naïve FP4 caused visible desaturation — "Reds washed out, chroma dropped"); **CFG folding** (student predicts the teacher's already-guided velocity → one branch, not two); **timestep distillation** via DMD variants, GAN-free first, adversarial loss added last for sharpness | **2.75 s → 0.44 s at 1K, ~6.3×**, no visible quality loss | 2026-07-09, Kaan Akan, Yigithan Yigit, Abdussamet Turker [src](https://blog.fal.ai/serving-sub-second-ideogram-v4-without-quality-loss/) |
| **DSpark speculative decoding** for the prompt expander | **Engine is SGLang, chosen over vLLM for low-batch performance.** DSpark = DeepSeek's diffusion block predictor (DFlash) + Markovian heads raising later-position acceptance. Qwen3.5-397B MoE distilled to Qwen3.6-35B MoE on 250K prompt-expansion samples; drafter trained with TorchSpec | **830 tok/s at TP=1** (the production config, cost-optimal), **~1000 tok/s at TP=4**, **acceptance length 4.6**, **16× throughput** vs baseline at 300 tok/s/user, **<1 s** single-user; single **B200** | 2026-07-08, Dogac Eldenk [src](https://blog.fal.ai/how-we-achieved-1000-tok-s-and-16x-throughput-with-dspark-for-ideogram-v4-prompt-expander/) |
| **Ulysses sequence parallelism** for video diffusion | Full sequence, sharded heads, all-to-all around attention. Three overlap strategies benchmarked: **async Ulysses** (overlap one Q/K/V branch's comm with the next's compute), **symmetric memory transport** (route via Copy Engine to cut SM contention), **fused QKV projections** (gather + project in one op) | Async: **−23…25 % chunk latency at 2/4/8 GPUs**. Fused QKV: **−37.3 % / −33.4 % at 2 / 4 GPUs**. Sym-mem best at 2–4 GPUs. **8× B200, bf16, cuDNN attention backend.** Conclusion: "no single strategy optimizes all regimes" — overlap wins at scale, fusion at low/mid scale | [src](https://blog.fal.ai/ulysses-unbound-experiments-in-communication-computation-overlap/) |

fal also ships its own models (e.g. **PATINA**, image→PBR material maps on a
modified FLUX.2 [klein] backbone with a DINOv2 adapter, ~7.5M training steps,
2026-04-10 [src](https://blog.fal.ai/introducing-patina/)) — relevant only as
evidence that the "inference engine" and the model org are the same team.

**(g) Routing, concurrency, limits, SLAs.** `max_multiplexing` sets per-runner
concurrency; `request_timeout` bounds a request; `regions` pins placement;
there is a documented `optimize-routing-behavior` page in the deployment docs
tree. Enterprise page references "SLA guarantees" and SOC 2 certification
(trust.fal.ai) without publishing terms; the 99.99 % uptime figure is a
homepage claim, not a contractual SLA [src](https://fal.ai/enterprise),
[src](https://fal.ai/).

**(h) Observability / developer surface.** Python SDK (`fal.App` with
`setup()`), a substantial CLI (`fal apps scale`, `apps runners`, `apps rollout`,
`apps set-rev`, `queue`, `run`, `doctor`), client libraries in JS, Python, Dart,
Kotlin and Swift, realtime/streaming/queue APIs, health-check endpoints,
`debug-runners`, rollbacks, and an MCP server.

**(i) Pricing.** Two models side by side [src](https://fal.ai/pricing):

*Serverless GPU, $/hr, list vs discounted:* B300 8.50 / 4.49 · B200 6.25 / 3.49
· H200 4.50 / 2.10 · H100 4.50 / **1.89** · RTX PRO 6000 2.99 / 1.10.

*Model APIs, output-priced:* video per output second (Wan 2.5 $0.05/s, Kling
2.5 Turbo Pro $0.07/s, Veo 3 $0.4/s, Ovi $0.2/video); images per image or per
megapixel (Seedream V4 $0.03, Flux Kontext Pro $0.04, Nanobanana $0.0398, Qwen
$0.02/MP).

*Implication.* The discounted H100 rate of **$1.89/hr is at or below the
neocloud on-demand rates in [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md)** —
which is only sustainable against committed capacity plus very high
utilisation, and is consistent with the AWS partnership being a volume
commitment rather than on-demand draw (inferred). The output-priced model APIs
are the real margin: fal keeps every second of latency it removes with the
kernel work in (f), because the customer pays per generated image or video
second regardless of how fast the GPU produced it. **That is the structural
reason fal invests in kernels and Modal does not.** (inferred)

**(j) Published numbers.** In (b), (f) and (i) above.

**(k) What transfers to bare metal.**

| Transfers well | Does not transfer |
|---|---|
| **The kernel work itself, verbatim.** CUTLASS EVT epilogue fusion, MXFP8/NVFP4 quantizer layouts matching tcgen05, Ulysses overlap strategies — all open techniques on hardware this repo already targets. See [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) and [`../cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md). | **Output-based pricing.** It is what funds the kernel work; on your own cluster the same work is a pure cost reduction and must clear a different bar. |
| **The hot-tunable / code-versioned parameter split** (§3A.2(e)) — a genuinely good config-design pattern for any in-house control plane. | **fal's model catalogue and licensing.** |
| **"SGLang over vLLM for low-batch"** as a concrete, dated data point for engine selection — corroborates [`../cross-cutting/inference-engines.md` §9](../cross-cutting/inference-engines.md). | |
| **Distillation as a serving optimisation** (CFG folding, timestep distillation, QAD): changing the model to cut forward passes beats optimising the pass. The LLM analogue is speculative decoding — see [`../cross-cutting/serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md). | |

---

### 3A.3 Wafer — company identification first

**⚠️ Name collision, resolved.** Probing eight candidate domains found **three
live sites and two distinct companies**:

| Domain | HTTP | Page title | Verdict |
|---|---|---|---|
| `wafer.ai` | 200 | "Wafer \| Continual Inference" | ✅ **The inference company.** |
| `usewafer.com` | 200 | "Wafer \| Continual Inference" | ✅ Same site/company (byte-identical markup, same GTM-KRTWTM8P container). |
| `wafer.systems` | 200 | "Wafer" — "An OS that understands you", "© 2026 Wafer, Inc", beta waitlist, personal-assistant demo | ❌ **Unrelated company**, same name. Consumer OS/assistant. Not an inference provider. |
| `wafer.dev`, `wafer.cloud`, `getwafer.com`, `wafer.inc` | 000 | — | No site. |

Everything below is **`wafer.ai`**. Confidence: high — the site self-describes
as inference optimisation, publishes GPU-kernel engineering, and its funding
post names AMD Ventures.

**(a) What they sell.** Not capacity — **continual optimisation of inference**.
"Inference that keeps getting better"; Wafer "learns how your workload behaves
and continuously optimizes" across "batching, caching, routing, and decode
decisions", with "**Model, engine, kernels, and hardware ... optimized
together**" and a stated method of *Find Bottlenecks → Try Many Paths → Ship
Measured Winner*, searching over "batching, decoding, quantization, engines,
kernels, and hardware" [src](https://wafer.ai). Headline counters: "**2T+ tokens
of continual inference**", "**400 TOK/S**" with a progression "230 tok/s → 315
tok/s → 400 tok/s". Product surfaces: **Dedicated** endpoints, **Cases**, a
startup credit program ($500 credits, 1:1 matching to $10,000). The manifesto's
framing is "maximize intelligence per watt by using AI to optimize AI
infrastructure" [src](https://wafer.ai/manifesto).

A second, developer-tool surface exists alongside: **KernelArena** (kernel
benchmarking on NVFP4 and HIP), **Trace Compare** (compare vLLM traces across
platforms), a built-in **Perfetto** trace viewer, **ROCprofiler Compute** and a
**cloud PTX/SASS compiler analyser** in-IDE, **GPU Docs**, **Workspaces** (GPU
compute for coding agents), and a **VS Code / Cursor extension**
[src](https://wafer.ai/blog).

**(b) Hardware and where it runs.** Wafer does not appear to own fleet; it
optimises on partners' hardware. Published work is overwhelmingly **AMD**:
MI300X, MI350X/MI355X, with DigitalOcean as a named partner/venue and an NVIDIA
line (NVFP4 on Blackwell) as the comparison arm. AMD Ventures is an investor
[src](https://wafer.ai/blog/series-a). (inferred: capacity is the customer's or
the partner cloud's, not Wafer's.)

**(c) Orchestration and isolation.** ⚠️ Not published. "Dedicated" implies
per-customer endpoints; nothing states the substrate.

**(d) Cold-start engineering.** ⚠️ Nothing published. Not their problem domain
— they optimise steady-state token economics.

**(e) Autoscaling.** ⚠️ Not published as parameters. The home page names
"batching, caching, routing, and decode decisions" as things the optimiser
touches, which implies it tunes serving concurrency rather than replica count
(inferred).

**(f) Serving engine and per-request optimisations.** This is the whole
company. Engine: **SGLang** is named for the Kimi-K3 MI355X work
[src](https://wafer.ai/blog/kimi-k3-mi355x); the AMD frontier post refers only
to "stock frameworks" and "standard kernel libraries" generically ⚠️. Formats:
**MXFP4** ("jointly developed by AMD, NVIDIA, Microsoft and others", ~3.8×
compression vs BF16, aimed at MoE expert weights) and **NVFP4** for Blackwell
(a dedicated post on quantizing Kimi K2.6 to NVFP4). Method: an agentic,
profile-guided kernel-optimisation loop.

Published results:

| Workload | Hardware | Result | Source |
|---|---|---|---|
| Kimi 2.5 | MI355X | **22.5 → 255.2 tok/s (11.33×)** | [src](https://wafer.ai/blog/inference-alpha-amd) |
| DeepSeek V3.2 | MI355X | **38.5 → 200.8 tok/s** single-request; **548 → 2,165 tok/s at 64 concurrency** | [src](https://wafer.ai/blog/inference-alpha-amd) |
| GLM-5 (774B) | 1× 8-GPU MI355X node | **151.1 tok/s** mean, **17.8 ms** ITL; fits a single node | [src](https://wafer.ai/blog/inference-alpha-amd) |
| **Kimi-K3 (2.8T)** | **8× MI355X, TP8, SGLang** | **952 tok/s/node** aggregate, **118 tok/s** single-stream, **48 tok/s/$** vs **33 tok/s/$** for B300; **~3.8× the aggregate throughput per node** of a 2-node B200 TP16 setup. ⚠️ **Context restored 2026-09-19:** on the *same* benchmark the B300 (TP8+DCP8) is faster in absolute terms — **1,568 tok/s/node, 172 tok/s single-stream** — so the MI355X win is entirely a **price** win, resting on an assumed **$2.50/GPU-hour (MI355X) vs $6.00/GPU-hour (B300)** and the post's claim that AMD is *"2.4× cheaper per GPU on average versus a B300"*. Draft model named: RadixArk's Kimi-K3-DSpark. MI355X VRAM 288 GB/GPU. Workload 1,024 in / 400 out, speculative decoding on. Notes the model needs "over 1.5TB of VRAM *before* allocating a KV cache for 1M tokens of context" | 2026-07-31, Ian Ye [src](https://wafer.ai/blog/kimi-k3-mi355x) |
| GLM5.2 | MI355X node | **2,626 tok/s/node** | [src](https://wafer.ai/blog) |
| Qwen3.6-35B-A3B | MI355X node | **~15k tok/s/node** with "ATOM" | [src](https://wafer.ai/blog) |
| **Kimi Delta Attention kernel** | 145-SM GPU | **11.65×** over `torch.compile` baseline (from an initial 9×). Root cause found by profiling: kernel launched **only 64 blocks on a 145-SM GPU → 6.25 % occupancy**; fix was register vectorisation + manual unrolling. Driven by an agent given an `ncu` subcommand so it could "read logs" and "use tools to understand what their code actually does" | 2026-01-30 [src](https://wafer.ai/blog/profile-guided-optimization) |
| topk_sigmoid kernel | — | **9×**, agent-optimised | [src](https://wafer.ai/blog) |
| Nordlys Labs routing | — | **8×** via Wafer-guided kernel optimisation | [src](https://wafer.ai/blog) |

Customer cases: **Y Combinator** — "Up to 44 % lower average LLM latency" and
2.5 minutes longer conversations on average; **Neon Health** — voice-agent TTFT
**800 ms → ~550 ms**; **DigitalOcean/AMD** — the 11.33× Kimi 2.5 result and
GLM-5 on one node [src](https://wafer.ai/cases). ⚠️ The cases page does not
publish baseline configurations, so the speedups are not independently
reproducible; the AMD post carries the caveat "based on internal testing … 
Results in customer environments may vary."

Notably, Wafer publishes on **benchmark integrity** — "A Field Guide to Reward
Hacking in AI Kernel Generation", "Case Study: A 104x (?) Speedup on
KernelBench", "Which models are the most HIP?" (HIP kernel correctness on
MI300X) [src](https://wafer.ai/blog). For a team about to trust
LLM-generated kernels, those are the most useful posts on the site.

**(g)–(h) Routing, limits, SLAs, observability.** ⚠️ Not published for the
Dedicated product. The *developer* observability surface is rich (Perfetto,
traces at `app.wafer.ai/traces`, ROCprofiler, PTX/SASS analysis, Trace Compare)
and an integration exists with the **TrueFoundry AI Gateway** offering
OpenAI-compatible serverless inference (2026-07-17) [src](https://wafer.ai/blog).

**(i) Pricing.** ⚠️ Not published. Startup program only ($500 + 1:1 matching to
$10,000) [src](https://wafer.ai). The absence of a public price list, combined
with "Dedicated", implies enterprise contract pricing (inferred).

**(j) Published numbers.** In (a) and (f). Funding: **$40M Series A,
2026-09-01**, co-led by **Marathon** and **Chemistry**, with **Wing**, **AMD
Ventures**, **Outset Capital**, **Fifty Years** and **Y Combinator**; angels
include Jeff Dean, Guillermo Rauch, Andy Fang, Kyle Vogt, Akshay Kothari,
Matthew Prince and Scott Stephenson. Seed round 2026-04-14. Positioning quote:
"AI learns from a workload's traffic patterns and performance constraints, then
finds the optimal deployment across the model, engine, kernels, and hardware"
[src](https://wafer.ai/blog/series-a).

**(k) What transfers to bare metal.** More than any other provider here,
because Wafer's product *is* the thing a bare-metal operator must do in-house.

| Transfers well | Does not transfer |
|---|---|
| **The loop itself**: profile → find the bottleneck → search configurations → ship the *measured* winner. This is the FinOps/perf loop of [`07-cost-engineering.md` §8](07-cost-engineering.md), applied at kernel granularity. | **The agent fleet.** Their search is LLM-agent-driven at a scale a single team will not reproduce. |
| **Occupancy as first check.** The **6.25 % → 11.65×** story is "64 blocks on 145 SMs = 6.25 % occupancy" (source also reports **0.04 waves per SM**; typo corrected 2026-09-19). Cheap to check, frequently the whole answer. | |
| **The AMD data points.** If MI355X is on your shortlist, the Kimi-K3 **48 vs 33 tok/s/$** comparison against B300 is the most concrete public number available — cross-check against [`../gpus/mi355x.md`](../gpus/mi355x.md) and [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md). | |
| **Benchmark-integrity discipline** for any LLM-generated kernel you accept. | |

---

### 3A.4 Baseten

The most complete published inference stack in §3A, and the closest analogue
to what a serious bare-metal operator would build. Everything they describe is
reproducible from open-source parts.

**(a) What they sell.** Dedicated deployments (your model, your GPUs, per-minute
billed), per-token **Model APIs** over open models, training/"Loops", Chains
for multi-step compound inference, and an open-source packaging format
(**Truss**). Plus an unusually good free artefact: the *Inference Engineering*
book [src](https://www.baseten.co/inference-engineering/book/), cited below
wherever it states Baseten's own practice.

**(b) Hardware and where it runs.** Nothing owned — **20+ cloud providers
across dozens of regions**, made "completely fungible regardless of provider or
location" [src](https://www.baseten.co/blog/how-we-built-multi-cloud-capacity-management/).
They name three supply categories and recommend a blend: **hyperscalers (AWS,
GCP)**, **neoclouds (CoreWeave, Nebius)**, **resellers (SF Compute Company)**,
with "a baseline of low-cost reserved instances and a mix of on-demand and spot
for handling peaks"
[src](https://www.baseten.co/inference-engineering/book/07-production/multi-cloud-capacity-management/).
GPU menu from pricing: T4, L4, A10G, A100 80GB, H100 MIG 40GB, H100 80GB, B200
180GB [src](https://www.baseten.co/pricing/). Note B200 at **180 GB**, matching
this repo's pin and contradicting fal's 192.

**(c) Orchestration and isolation.** Kubernetes, extended. **Multi-Cloud
Capacity Management (MCM)** is a **hub-and-spoke** design: one *global control
plane* consuming real-time event streams, many *workload planes* reporting
capacity, utilisation and traffic demand. The stated reason for building it:
"traditional Kubernetes … assumes tight latency (<10 ms) between nodes, which
breaks down across long distances." The global scheduler places on
geographic distance, customer priority, and compliance constraints
(region-locking, provider-locking). On a cloud outage it reschedules replicas
elsewhere and reroutes "within minutes — at least **6× faster than manual
failover**." **Over six months** of dedicated engineering, completed early 2024;
claims **99.99 % uptime** capability, customers named Writer, Abridge, Bland
[src](https://www.baseten.co/blog/how-we-built-multi-cloud-capacity-management/).
The book adds the failover taxonomy — active-active vs active-passive hot
standby — and the reliability datum they design against: Llama-3's
**~1 GPU failure per 50,000 GPU-hours**
[src](https://www.baseten.co/inference-engineering/book/07-production/multi-cloud-capacity-management/).

**(d) Cold-start engineering — the Baseten Delivery Network (BDN).** The single
best-documented weight-distribution design in public, and the direct analogue
of what [`01-bare-metal-cluster.md` §5](01-bare-metal-cluster.md) calls weight
distribution.

Mechanism: at *deploy* time weights are mirrored into Baseten-managed blob
storage, producing "a manifest: a list of files with their content hashes or
etags, which serves as the authoritative record for what a deployment needs to
start." At *run* time, three tiers:

| Tier | What | Property |
|---|---|---|
| 1 | Node-local NVMe SSD cache | "Reads happen at NVMe speeds (multiple GB/s) with zero network traffic" |
| 2 | In-cluster peer cache on a **consistent hash ring** | A miss is split into fixed-size chunks; each chunk is assigned to a node on the ring |
| 3 | Mirrored origin (Baseten blob storage) | Parallelised byte-range fetches |

Measured: **>2 GB/s** to pull weights onto H100 nodes; **2–3× faster cold
starts**; mirroring itself at **1–5 GB/s**. Checkpoints tested from ~10 GB
(quantized 7B) to hundreds of GB (full-precision 70B+, or multi-expert
architectures like DeepSeek-R1). The headline property: **scaling to 50
replicas of a 140 GB model consumes 1× the model size of origin bandwidth, not
50×**, because tier 2 fans out inside the cluster. Opt-in per deployment.
Last updated 2026-04-09; Gregory Kofman, Ujjwal Sarin, Stephen Day
[src](https://www.baseten.co/blog/how-the-baseten-delivery-network-bdn-makes-cold-starts-fast/).

The book names the four sequential cold-start bottlenecks — **GPU procurement →
container image load → weight load (hundreds of GB) → engine startup and
compilation** — and gives the general rule BDN implements: load weights "over
network within a node from a source cached physically near the GPU instance
within the same datacenter" to get the GB/s needed for large models
[src](https://www.baseten.co/inference-engineering/book/07-production/autoscaling/).
On containerisation their guidance is conservative and correct: dependency
chains for inference are "long and fragile" across CUDA toolkit, torch,
transformers/diffusers, engine version and system packages, so pin exact
versions for reproducible builds; NVIDIA NIMs are named as a prebuilt
alternative
[src](https://www.baseten.co/inference-engineering/book/07-production/containerization/).

⚠️ Baseten publishes **no memory/GPU checkpoint-restore** equivalent to Modal's.
Their cold-start answer is bandwidth, not snapshots.

**(e) Autoscaling.** Two signals, explicitly contrasted: **utilisation-based**
(GPU memory and compute — called out as "a lagging indicator") and
**traffic-based** (request volume, enabling proactive scaling). They note the
two diverge: "a few LLM requests with massive token counts can consume more GPU
resources than many smaller requests with high cache hits." Five knobs:
min/max replicas, autoscaling window, scale-down delay, concurrency target.
Queue-based scaling holds requests while capacity spins up (FIFO, with priority
queues as an option for paid tiers). Multi-component pipelines (VAD →
transcription → LLM) must **decompose autoscaling per stage** and keep all
stages "in the same cluster" — they quote **50 ms inter-cluster vs 10 ms
intra-cluster** as the reason.

Their scale-to-zero position is worth quoting as policy guidance: it suits
development and periodic workloads, but for latency-sensitive applications with
light traffic it "signals insufficient scale" and such users "should use
pay-per-token APIs until greater scale is reached"
[src](https://www.baseten.co/inference-engineering/book/07-production/autoscaling/).
That is a cleaner articulation of the rule in
[`05-autoscaling-and-predictive-scaling.md` §5](05-autoscaling-and-predictive-scaling.md)
than most vendors manage.

**(f) Serving engine and per-request optimisations — the Baseten Inference
Stack.** Composed, not written from scratch, with two in-house pieces
[src](https://www.baseten.co/blog/nvidia-dynamo-day-baseten-inference-stack/)
(Rachel Rapp, 2026-03-16):

| Layer | Component | Built or adopted |
|---|---|---|
| Engines | **TensorRT-LLM**, **SGLang**, **vLLM** — "as best suited to a particular model and use case" | adopted |
| Orchestration | **NVIDIA Dynamo** | adopted |
| Transport | **NIXL** (NVIDIA Inference eXchange Layer) | adopted |
| KV | **KV Block Manager**; KV-cache offload across CPU, SSD, remote storage | adopted |
| Speculation | **Baseten Speculation Engine** | **built** |
| Speculation | **Suffix Automaton MTP Accelerator** | **built, open-sourced** |

Published deltas from that stack:

- **Disaggregated prefill/decode: up to 6× higher TPS per GPU.**
- **KV-cache-aware routing: 34–62 % performance gains in production.**
- Combined: **−50 % TTFT, −34 % TPOT, +61 % throughput** on long input/output
  workloads.

The **Speculation Engine** (Mahmoud Hassan and the Model Performance Team,
2026-05-05) is a hybrid: a **suffix automaton** catching arbitrarily long
repeats from recent context, plus **MTP** heads, choosing between them on a
match-length threshold and batching verification. Measured on
`nvidia/DeepSeek-V3.1-NVFP4` against `glaiveai/code-edit-samples`: **up to 40 %
higher throughput at equal latency, or 40 % lower latency at equal
throughput, vs MTP alone**; **34 % higher acceptance lengths** (30–33 % across
batch sizes). Implementation detail worth stealing: **device-side suffix
automaton updates with zero synchronisation overhead**, POD structs and
header-only C++ shared between CPU and GPU, integrated into TensorRT-LLM, with
a Python API of `add_request` / `prepare` / `extend`
[src](https://www.baseten.co/blog/boosting-mtp-acceptance-rates-in-baseten-speculation-engine/).

Their framing doc, *The Efficient Frontier of LLM Inference* (Philip Kiely,
2026-09-01), separates **moving along** the latency/throughput frontier (batch
size; TP for latency vs EP + attention-DP for throughput; quantization
trade-offs in MXFP4/NVFP4) from **expanding** it (kernel optimisation;
speculative decoding — EAGLE-3 and DFlash, "particularly effective for code
generation"; disaggregation). It assumes a baseline of GLM-5.3 or Kimi-K3 with
KV-cache reuse and KV-aware routing already on
[src](https://www.baseten.co/blog/the-efficient-frontier-of-llm-inference/).
This is the same decomposition as
[`../cross-cutting/serving-optimizations.md` §8](../cross-cutting/serving-optimizations.md)
and [`04-throughput-and-utilization.md` §5](04-throughput-and-utilization.md) —
useful as independent corroboration.

**(g) Routing, concurrency, limits, SLAs.** KV-cache-aware, LoRA-aware and
sequence-length-aware routing are all named as production concerns
[src](https://www.baseten.co/inference-engineering/book/07-production/autoscaling/).
99.99 % uptime is claimed as an MCM capability
[src](https://www.baseten.co/blog/how-we-built-multi-cloud-capacity-management/);
⚠️ contractual SLA terms not published.

**(h) Observability / developer surface.** **Truss** — open-source model
packaging, `basetenlabs/truss` [src](https://github.com/basetenlabs/truss);
**Chains** for multi-step inference
[src](https://www.baseten.co/blog/introducing-baseten-chains/); metric export
to external observability tools; an MCP server and coding-agent skill; a public
changelog. The *Inference Engineering* book doubles as the documentation of
their opinions.

**(i) Pricing.** Per **minute** for dedicated deployments; "No idle time
charges" [src](https://www.baseten.co/pricing/):

| Instance | $/min | implied $/hr |
|---|---:|---:|
| T4 16GB | 0.01052 | 0.63 |
| L4 24GB | 0.01414 | 0.85 |
| A10G 24GB | 0.02012 | 1.21 |
| H100 MIG 40GB | 0.0625 | 3.75 |
| A100 80GB | 0.06667 | 4.00 |
| H100 80GB | 0.10833 | 6.50 |
| B200 180GB | 0.16633 | 9.98 |

CPU instances $0.00058–$0.01382/min. Model APIs per 1M tokens, e.g. GLM-5.3
$1.40 in / $4.40 out, **DeepSeek V4.1 Flash $0.30 in / $1.20 out**, Kimi-K3
$3.00 in / $15.00 out. Tiers Basic / Pro (volume discounts) / Enterprise.
Training bills at the same per-minute GPU rates.

*Implication.* Baseten's H100 at $6.50/hr is ~1.6× Modal's and ~3.4× fal's
discounted rate — they are selling a managed *stack*, not capacity, and price
accordingly. Note also that their published DeepSeek-V4.1-Flash output price
($1.20/1M) is **2× the model vendor's own $0.60/1M** that
[`../README.md` §3](../README.md) uses as the sanity check — useful calibration
for what a managed margin on top of self-hosting looks like. (inferred)

**(j) Published numbers.** BDN: >2 GB/s, 2–3×, 1–5 GB/s mirroring, 1× origin
bandwidth at 50 replicas. Stack: 6× TPS/GPU (PD), 34–62 % (KV routing), −50 %
TTFT / −34 % TPOT / +61 % throughput. Speculation: +40 % throughput or −40 %
latency, +34 % acceptance length. MCM: 20+ clouds, ≥6× faster failover,
99.99 %.

**(k) What transfers to bare metal.** Most of it — this is the closest thing to
a blueprint.

| Transfers well | Does not transfer |
|---|---|
| **BDN's three-tier weight path.** On one cluster: node NVMe → in-cluster peer cache on a consistent hash ring → object store, with a content-hash manifest per deployment. The "50 replicas costs 1× origin bandwidth" property is exactly what you need when a 510 GB or 1.56 TB checkpoint ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)) lands on every node at once. See [`06-cold-start.md` §2](06-cold-start.md). | **MCM across 20+ clouds.** On one cluster the global control plane collapses to a single scheduler. Its *ideas* (fungible capacity, compliance-constrained placement) survive only if you run multiple sites. |
| **The whole Inference Stack composition** — TRT-LLM/SGLang/vLLM under Dynamo with NIXL and a KV block manager — is open source and is the shape [`09-reference-architectures.md` §2](09-reference-architectures.md) describes. | **Spot/reserved blending.** You own the hardware; there is no spot tier. |
| **Two-signal autoscaling** (lagging utilisation + leading traffic) and their warning that token-weighted load ≠ request count. | |
| **Per-stage autoscaling in compound pipelines, colocated in one cluster** (50 ms vs 10 ms). | |
| **The suffix-automaton + MTP hybrid** and its device-side, sync-free update — open-sourced, and directly applicable to the DSpark/MTP configurations this repo already plans ([`../METHODOLOGY.md` §8](../METHODOLOGY.md) pins DSpark γ=5). | |
| **Pinning every layer of the CUDA/torch/engine chain.** Unglamorous, and the most common cause of "worked in staging." | |

---

### 3A.5 RunPod

The commodity end: cheapest per-second GPU-seconds, thinnest managed layer,
most explicit knobs. Also the best-documented *settings surface* of any
provider here, which makes it a good source for sane defaults.

**(a) What they sell.** **Pods** (rented GPU VMs/containers, on-demand or
spot), **Serverless** endpoints (queue-based or load-balancing), **Clusters**
(multi-GPU), and **Flash** — a Python SDK that turns `@Endpoint`-decorated
local functions into Serverless endpoints, Modal-style
[src](https://docs.runpod.io/flash/overview).

**(b) Hardware and where it runs.** Two tiers: **Secure Cloud** (vetted
datacenters) and **Community Cloud** (third-party hosts). Serverless GPU tiers
and their per-second rates
[src](https://docs.runpod.io/serverless/endpoints/endpoint-configurations):

| Tier | Memory | $/s | implied $/hr |
|---|---|---:|---:|
| A4000, A4500, RTX 4000 | 16 GB | 0.00016 | 0.58 |
| L4, A5000, 3090 | 24 GB | 0.00019 | 0.68 |
| 4090 PRO | 24 GB | 0.00031 | 1.12 |
| A6000, A40 | 48 GB | 0.00034 | 1.22 |
| L40, L40S, 6000 Ada PRO | 48 GB | 0.00053 | 1.91 |
| A100 | 80 GB | 0.00076 | 2.74 |
| H100 PRO | 80 GB | 0.00116 | 4.18 |
| 6000s PRO | 96 GB | 0.00111 | 4.00 |
| H200 PRO | 141 GB | 0.00155 | 5.58 |
| B200 | **180 GB** | 0.00240 | 8.64 |

Pods on-demand (Secure Cloud): B300 $7.89/hr, B200 $6.79, H200 $4.59, H100 SXM
$3.49, H100 PCIe $2.89, RTX PRO 6000 $2.09, A100 SXM $1.59, L40S $1.09, RTX
6000 Ada $0.84, RTX 4090 $0.74, L4 $0.49, A5000 $0.27. Clusters: H200 SXM
$4.31/hr, A100 SXM $1.79/hr. Storage: container disk $0.10/GB-mo; network
volume standard $0.05–0.07, high-performance $0.14
[src](https://www.runpod.io/pricing). Serverless B300 reaches $9.98/hr.

**(c) Orchestration and isolation.** ⚠️ Not published. Workers are containers
from Docker Hub / GitHub / the RunPod Hub; endpoints can be pinned to specific
data centers, with the explicit warning that "restricting decreases the
available GPU pool." Multi-tenancy model not disclosed; Community Cloud being
third-party-hosted implies a weaker trust boundary than Secure Cloud
(inferred).

**(d) Cold-start engineering.** Three named mechanisms, layered:

1. **FlashBoot** — "**Reduces cold starts by retaining worker state after
   spin-down, allowing faster 'revival' than fresh boots.** Most effective on
   endpoints with consistent traffic where workers frequently cycle between
   active and idle." **Enabled by default** on new GPU and CPU endpoints
   [src](https://docs.runpod.io/serverless/endpoints/endpoint-configurations).
   ⚠️ No mechanism detail and **no published latency figures** — this is a
   named product with an unpublished implementation.
2. **Cached models** — host-affinity scheduling against Hugging Face models
   pre-staged on machines. "Runpod automatically tries to start your workers on
   hosts that already contain the selected model"; if none is available it
   *delays* worker start until the download completes **and does not bill you
   for the download**. Claims cold starts "to just a few seconds, even for large
   models", smaller images (weights decoupled from the image), and sharing
   across workers on the same host. Works for public, gated and private HF
   models; not for non-HF private models — bake those into the image
   [src](https://docs.runpod.io/serverless/endpoints/model-caching).
3. **`VolumeCache`** — an SDK helper that mirrors local cache directories onto
   an attached network volume at `/runpod-volume`, restoring on cold start and
   copying newly written files back after the job. "Best-effort and
   self-contained … If any part of the cache fails, the worker falls back to a
   normal cold start." Namespaced by `RUNPOD_ENDPOINT_ID`
   [src](https://docs.runpod.io/serverless/development/volume-cache).

The "you aren't billed while the model downloads" property is the interesting
one: it shifts the cost of a cache miss from the customer to RunPod, which only
works because RunPod controls placement. (inferred)

**(e) Autoscaling.** Two selectable algorithms — and both are *concurrency*
based, not utilisation
[src](https://docs.runpod.io/serverless/endpoints/endpoint-configurations):

- **Queue delay** — add workers when requests wait longer than a threshold
  (**default 4 s**). "Best when slight delays are acceptable for higher
  utilization."
- **Request count** — `Math.ceil((requestsInQueue + requestsInProgress) / scalerValue)`.
  Scaler value 1 = maximum responsiveness. **"Recommended for LLM workloads or
  frequent short requests."**

Defaults and limits worth copying: active workers **0**, max workers **3**
(advice: set ~20 % above expected max concurrency), GPUs per worker **1**
("generally prioritize fewer high-end GPUs over multiple lower-tier GPUs"),
idle timeout **5 s**, execution timeout **600 s** (range 5 s–7 days), job TTL
**24 h** (range 10 s–7 days, timer starts at *submission* not execution). Up to
**three GPU types in priority order**; for endpoints with ≥5 workers RunPod
spreads workers across the priorities to reduce throttling, below 5 all workers
take the top available type. Idle *endpoints* are auto-shrunk: max workers → 2
after 3 days with no requests, → 0 after 7 days, and it stays there until you
raise it manually.

**(f) Serving engine and per-request optimisations.** **None of their own** —
but they ship a supported **vLLM worker** image
[src](https://docs.runpod.io/serverless/vllm/overview). Per-worker concurrency
is the user's job: an async `handler` plus a `concurrency_modifier` lets a
single worker adjust how many jobs it accepts at once
[src](https://docs.runpod.io/serverless/workers/concurrent-handler).

**(g) Routing, concurrency, limits, SLAs.** Two endpoint types: **queue-based**
(`/run` async, `/runsync` sync; guaranteed execution, automatic retries; job
states IN_QUEUE → RUNNING → COMPLETED) and **load-balancing** (traffic routed
straight to workers, no queue, for "low-latency applications like real-time
inference or custom REST APIs", supporting FastAPI/Flask)
[src](https://docs.runpod.io/serverless/overview). Result retention: async 30
min, sync 1 min. ⚠️ No published SLA.

**(h) Observability / developer surface.** Python SDK with handler functions;
Flash SDK for local-code-to-endpoint; docs MCP server; agent skills for Claude
Code/Codex/Cursor.

**(i) Pricing and what it implies.** Pure per-second, "billed from when a
worker starts until it fully stops, rounded up to the nearest second", **flex**
(scale to zero) vs **active** (24/7, discounts via sales)
[src](https://docs.runpod.io/serverless/pricing). Marketing claim: "Save 25 %
over other Serverless cloud providers on flex workers alone"
[src](https://www.runpod.io/pricing). The Serverless-vs-Pods spread is the
premium for scale-to-zero and the queue — a useful market price for "someone
else absorbs your idle." (inferred) ⚠️ **Two RunPod sources disagree on the
serverless side** (both re-checked 2026-09-19): the serverless docs rate table
above gives H100 $4.18/hr, H200 $5.58, A100 $2.74; `runpod.io/pricing` gives
H100 **$4.79**, H200 **$5.93**, A100 **$2.72**, B300 **$9.98**. Against the
same $3.49 Pod rate the premium is therefore **20 % or 37 %** depending on the
page. The Pods column reproduced exactly on both.

**(j) Published numbers.** The rate tables above; cached-model cold starts "a
few seconds"; queue-delay default 4 s. ⚠️ No FlashBoot latency numbers, no
throughput benchmarks.

**(k) What transfers to bare metal.**

| Transfers well | Does not transfer |
|---|---|
| **The settings surface as a default set.** Request-count scaling with scaler 1 for LLMs, queue-delay 4 s for batch, idle timeout on the order of seconds, execution timeout and job TTL as separate limits with the TTL clock starting at submission — these are good in-house defaults and cheap to copy. | **Community Cloud economics** (renting other people's idle consumer GPUs). |
| **Host-affinity scheduling against pre-staged weights.** The bare-metal version: label nodes by which checkpoints are resident on local NVMe and schedule replicas to them. Directly complements BDN-style tiering. | **"Not billed during download."** A billing construct, not an engineering one. |
| **Two endpoint types — queued vs direct-routed.** Batch/offline work wants the durable queue; interactive serving wants direct routing. Most in-house stacks build only one. See [`09-reference-architectures.md` §6](09-reference-architectures.md). | **Idle-endpoint auto-shrink at 3/7 days** — an anti-abandonment measure for a public platform. |
| **GPU-type priority lists with spread above a threshold** — useful in a heterogeneous cluster (e.g. mixed H200/B300 nodes). | |

---

### 3A.6 Replicate

The model-sharing platform. Its durable contribution is **Cog**, which is the
de-facto open packaging format for a GPU inference container.

**(a) What they sell.** A public catalogue of community models billed per
second of prediction time, **Deployments** (private, autoscaled instances of a
model), fine-tuning, and **Cog** as open-source tooling you can use anywhere.

**(b) Hardware and where it runs.** NVIDIA A100, H100, T4 "and additional
options"; hardware type is switchable "without changing your code"
[src](https://replicate.com/docs/topics/deployments). H100s arrived 2025-05-16,
L40S 2024-11-15 [src](https://replicate.com/blog). ⚠️ Underlying cloud(s) not
published.

**(c) Orchestration and isolation.** ⚠️ Not published.

**(d) Cold-start engineering.** Two published data points, a decade apart in
maturity:

- **Fine-tuned models boot in under one second** (2023-09-06) — down from
  "several minutes," for Llama-2 7B/13B/70B (chat and base) and SDXL. ⚠️ The
  post announces the result without publishing the mechanism; the obvious
  implementation is keeping the base model resident and swapping only adapter
  weights, but **that is inference, not something Replicate states**
  [src](https://replicate.com/blog/fine-tune-cold-boots).
- **`torch.compile` caching** (2025-09-08) — "Cache your compiled models for
  faster boot and inference times" [src](https://replicate.com/blog). This is
  the third of the four cold-start bottlenecks Baseten enumerates, and is
  covered generally in [`06-cold-start.md` §3](06-cold-start.md).

Otherwise the documented lever is `min_instances` to keep models warm.

**(e) Autoscaling.** Deployments "scale from zero to hundreds of instances
based on traffic," with min instances ("Keep models warm to eliminate cold start
delays"), max instances as a spend cap, and scale-to-zero when idle
[src](https://replicate.com/docs/topics/deployments). ⚠️ Signal, window and
thresholds not published.

**(f) Serving engine and per-request optimisations.** No general LLM engine of
their own; per-model optimisation instead, published and open-sourced: **FLUX**
optimisations released to the community (2024-10-10), and **FLUX.1 Kontext
[dev]** via the **Taylor Seer** technique (2025-07-15)
[src](https://replicate.com/blog).

**Cog** is the substantive artefact
[src](https://github.com/replicate/cog): a tool that builds an **OCI image**
from a `cog.yaml` plus a Python `Runner` class with `setup()` (load weights
once) and `run()` (typed inputs/outputs). It resolves CUDA/cuDNN/PyTorch
compatibility for you, caches dependencies efficiently, generates an **OpenAPI
schema from Python type hints**, and serves `/predictions` from "a
high-performance RESTful HTTP API using a **Rust/Axum** server", with a
standardised queue-worker interface for background work. Runs anywhere Docker
runs, or on Replicate. Current README shows `python_version: "3.13"` and the
`run: "run.py:Runner"` entrypoint form. ⚠️ Language split: the CLI is Go, the
HTTP server is Rust/Axum per the README; an exact per-component breakdown was
not obtainable (the GitHub languages API rate-limited during research).

**(g) Routing, concurrency, limits, SLAs.** ⚠️ Concurrency settings and SLA not
published in the fetched docs.

**(h) Observability.** Per-deployment metrics: latency, throughput, error
rates, and "GPU memory usage: Track resource utilization across all instances"
[src](https://replicate.com/docs/topics/deployments).

**(i) Pricing.** Per second of prediction time by hardware tier; "only pay for
what you need." ⚠️ The fetched deployments page does not carry the rate table.

**(j) Published numbers.** Sub-1 s fine-tune cold boots (2023); otherwise
sparse.

**(k) What transfers to bare metal.**

| Transfers well | Does not transfer |
|---|---|
| **Cog itself.** It is the single cheapest way to standardise "a model is a container with a typed predict endpoint and an OpenAPI schema" across a team, and it runs on your own cluster with no Replicate dependency. Compare with Baseten's Truss; pick one, don't write a third. | **The public model marketplace** and its per-second retail pricing. |
| **`setup()` / `run()` as an enforced boundary** — it makes "what is cold-start work" a type-level fact rather than a convention, which is what makes snapshotting and warm pools tractable later. | |
| **Compile-cache persistence** (`torch.compile` artefacts on a shared volume keyed by shape/arch/version) — see [`06-cold-start.md` §3](06-cold-start.md). | |

---

### 3A.7 Beam (beam.cloud)

Small, open-source-forward, and one of only three providers here shipping
checkpoint/restore.

**(a) What they sell.** "The open-source serverless cloud for AI and ML
workloads" — REST endpoints, ASGI apps, task queues, functions, scheduled jobs,
sandboxes for untrusted code, container hosting, and reservable on-demand
machines [src](https://docs.beam.cloud/v2/getting-started/introduction).

**(b) Hardware.** Serverless GPUs are the small end: `T4` (16Gi), `A10G` (24Gi),
`RTX4090` (24Gi), `RTX5090` (32Gi). Datacenter parts — `H100`, `H200`,
`A100-80`, `L40S`, `A6000` — are **on-demand reservations only**
(`beam machine reserve --gpu <type>`), not serverless. `beam machine list`
prints live availability and prices; the doc's sample shows **A100-80 $1.49/hr,
H100 $3.63/hr, RTX4090 $0.66/hr** [src](https://docs.beam.cloud/v2/environment/gpu).
⚠️ **Corrected 2026-09-19 against [beam.cloud/pricing](https://www.beam.cloud/pricing):**
the live rate card is **on-demand H100 PCIe from $1.83/hr, H200 SXM5 from
$2.09, A100-80 SXM4 from $1.36, L40S from $0.76, RTX 5090 from $0.72, RTX 4090
from $0.44** — the docs sample is stale and roughly 2× high. The same page
lists **H100 PCIe under *Serverless* at $0.000972/s ($3.50/hr)**, which
contradicts the "datacentre parts are reservation-only" reading above; treat
that restriction as ⚠️ unconfirmed.
Regions: US, Europe, Asia. Multi-GPU (`gpu_count`) is **by request only**.
*This matters for this repo:* Beam cannot serve any of the five models in
[`../METHODOLOGY.md` §8](../METHODOLOGY.md) serverlessly — DeepSeek-V4.1-Flash
at 510 GB and Kimi-K3 at 1.56 TB need the reserved tier and multi-node besides.

**(c) Orchestration and isolation.** ⚠️ Not published in detail; containers
"launch in under a second". Sandboxes are advertised for untrusted code, so
some isolation layer exists ⚠️.

**(d) Cold-start engineering.** A documented ladder
[src](https://docs.beam.cloud/v2/topics/cold-start):

1. **Cache weights in a Volume** rather than downloading per request
   (`cache_dir` into a mounted `Volume`).
2. **`on_start`** — a function that "will run exactly *once* when the container
   first starts"; its return value is handed to the handler via
   `context.on_start_value`.
3. **`checkpoint_enabled=True`** — "captures a memory snapshot of the running
   container **after `on_start` completes**. This means that you can load a
   model onto a GPU, run some setup logic, and when the app cold starts, it
   will start *right from that point*." Available on **RTX4090, H100, A10G**
   only. Known failure mode documented: a missing volume / wrong cache path.

Note the design difference from Modal: Beam snapshots at a **single fixed
point** (end of `on_start`), which removes the `snap=True`/`snap=False`
lifecycle complexity at the cost of flexibility. Cerebrium takes the third
option — an explicit trigger (§3A.10).

⚠️ No published cold-start latency numbers.

**(e) Autoscaling.** `QueueDepthAutoscaler(min_containers, max_containers,
tasks_per_container)` — pure queue-depth: at `tasks_per_container=30`, 30
queued tasks adds a container, 60 adds another
[src](https://docs.beam.cloud/v2/scaling/concurrency). Vertical concurrency is
separate: `workers=N` launches N workers inside one container sharing its CPU,
memory and GPU — "Workers allow you to increase your *per container*
throughput, vertically. Autoscaling allows to scale the *number of containers*
… horizontally." The docs give the sizing rule explicitly: a 3 GiB model on a
16 GiB T4 fits 4 workers
[src](https://docs.beam.cloud/v2/scaling/concurrent-inputs).

Keep-warm defaults are published per deployment type and are worth recording:
**Endpoints/ASGI/Realtime 180 s, Task Queues 10 s, Pods 600 s**
[src](https://docs.beam.cloud/v2/resources/pricing-and-billing).

**(f) Serving engine.** None of their own; bring your own (vLLM etc.).

**(g)–(h)** ⚠️ SLA not published. Developer surface is decorator-based Python
(`@endpoint`, `@task_queue`) plus a CLI; the platform is open source (the
`beta9` runtime).

**(i) Pricing and what it implies.** The billing boundary is unusually
customer-favourable and worth noting as a design choice: **you are charged for
`on_start`, your code, and `keep_warm_seconds`; you are *not* charged for
"waiting for a machine to start" or "pulling your container image"**. Storage
free to 1 TB then $0.021/GB-month, snapshots included
[src](https://docs.beam.cloud/v2/resources/pricing-and-billing). Their worked
example: 1 s boot + 100 ms task + `keep_warm_seconds=300` bills **301.1 s** —
i.e. for bursty low-traffic work, **the keep-warm window dominates the bill by
~300×**. That is the clearest published illustration of why scale-to-zero
platforms are expensive at low, spread-out request rates.

**(j) Published numbers.** Containers "under a second"; the GPU price sample
above; the 301.1 s billing example.

**(k) What transfers.** The **`on_start`-boundary snapshot** is the simplest
possible checkpoint design and the right first implementation on bare metal:
one snapshot point, taken when the engine is loaded and warm, restored on every
subsequent replica start. The **vertical-workers-vs-horizontal-containers**
distinction maps onto engine-internal batching vs replica count — see
[`04-throughput-and-utilization.md` §2](04-throughput-and-utilization.md). The
**billing boundary** does not transfer, but its lesson does: measure your
warm-window cost separately from your serving cost, because at low traffic it
*is* the cost.

---

### 3A.8 Inferless

⚠️ **Thin public documentation.** Most fields below are unfillable from primary
sources; this profile is deliberately short rather than padded with inference.

**(a) What they sell.** Serverless GPU model endpoints from a Git repo, a
Hugging Face model, or a Docker image — with the docs explicitly warning that
bring-your-own-Docker "might have higher coldstarts"
[src](https://docs.inferless.com/llms.txt).

**(b) Hardware.** ⚠️ No published GPU list; T4 appears in examples
[src](https://docs.inferless.com/concepts/overview).

**(c) Orchestration and isolation.** ⚠️ Not published.

**(d) Cold-start engineering.** ⚠️ **No mechanism and no numbers published.**
The changelog records a "Faster Cold-starts" release (2024-01-05) without
detail [src](https://docs.inferless.com/llms.txt). NFS-backed "My Volumes" exist
for weight persistence. Model builds take "~5-10 minutes"
[src](https://docs.inferless.com/concepts/overview). This is the weakest
cold-start story in §3A relative to its marketing category.

**(e) Autoscaling.** Min/max replicas, plus a dashboard **Scale Down** setting
("the duration of inactivity after which an instance is terminated"), an
**Inference Timeout**, and **Container Concurrency**
[src](https://docs.inferless.com/api-reference/model-endpoint/configuring-the-model-settings).
⚠️ Scaling signal not published.

**(f) Serving engine and batching.** Bring your own. Two documented concurrency
modes: *sequential processing with queue* (set Container Concurrency, **range
1–100**) and *batch processing with queue*, where you declare `BATCH_SIZE` and
`BATCH_WINDOW` (milliseconds) in `input_schema.py` — i.e. classic **dynamic
batching with a timeout**, the middle rung of Baseten's static/dynamic/
continuous ladder [src](https://docs.inferless.com/concepts/processing-concurrent-requests).
For LLMs that is strictly worse than the continuous batching a modern engine
does internally; the right configuration is high container concurrency and let
vLLM/SGLang batch (inferred).

**(g)–(j)** ⚠️ Routing, SLA, pricing and benchmarks not published in the
fetched docs.

**(k) What transfers.** Little. The one transferable observation is negative and
useful: **platform-level dynamic batching in front of an engine that already
does continuous batching is an anti-pattern** — it adds a `BATCH_WINDOW` of
latency for nothing. See [`04-throughput-and-utilization.md` §2](04-throughput-and-utilization.md).

---

### 3A.9 Northflank

Not an inference specialist — a general PaaS that added GPUs — but the **BYOC**
model makes it the closest commercial product to "run this on my own cluster."

**(a) What they sell.** A developer platform (build, deploy, CI/CD,
preview envs) that runs on **your** Kubernetes or theirs, with GPU workloads and
inference endpoints as one supported shape
[src](https://northflank.com/product/gpu-paas).

**(b) Hardware and where it runs.** "18+ GPU types" including **H100, H200,
B200, A100, L4, L40S** and TPUs. Runs on **AWS, GCP, Azure, Oracle, CoreWeave,
or any Kubernetes cluster** — "the same, consistent experience across AWS, GCP,
Azure, Oracle, CoreWeave, and any neocloud" — via EKS/GKE/AKS/OKE and custom
clusters.

**(c) Orchestration and isolation.** **Kubernetes, openly.** This is the one
provider in §3A that does not hide its substrate — which is precisely why it
is relevant to you. ⚠️ Multi-tenancy/isolation details not published beyond
supporting "untrusted code at scale".

**(d) Cold-start engineering.** "Model caching" is claimed; ⚠️ no mechanism, no
numbers.

**(e) Autoscaling.** Inference endpoints "scale from zero to hundreds of GPUs
based on traffic," with "intelligent request queuing" during scale-up. ⚠️
Parameters not published on this page.

**(f) Serving engine.** None of their own. Claims "automatic batching",
"sub-100 ms latency", and real-time APIs for text generation, embeddings and
chat completions ⚠️ (unqualified marketing claims — sub-100 ms is meaningless
without naming the model, GPU and token count).

**(g) Multi-tenancy feature worth flagging: GPU time-slicing.** "GPU
time-slicing to run multiple workloads on single GPUs, improving utilization
2-4×." ⚠️ Unqualified. Time-slicing a GPU between two LLM replicas that each
want the full HBM is not possible; this is credible for small models and
CPU-bound pre/post-processing, not for the models in this repo (inferred).
Compare Cloudflare's Omni (§3A.15), which solves the same problem with unified
memory and over-commitment and publishes the mechanism.

**(h)–(i)** Per-second billing, "no hourly minimums or long-term contracts".
⚠️ Rate table not on the fetched page.

**(j)** ⚠️ No published benchmarks.

**(k) What transfers.** The **BYOC-on-Kubernetes shape is the shape you are
building**, so Northflank is best read as a feature checklist for your own
control plane: per-second accounting, scale-to-zero with request queuing,
model caching, GPU sharing for small models, one experience across
heterogeneous node pools. Their unqualified performance claims transfer
nothing; see [`01-bare-metal-cluster.md` §3](01-bare-metal-cluster.md) for the
orchestration decision itself.

---

### 3A.10 Cerebrium

Real-time/voice-focused serverless GPU, and the third provider shipping
checkpoint/restore. Their docs are unusually precise about what is and is not
billed.

**(a) What they sell.** Serverless GPU and CPU apps from a `cerebrium.toml`,
aimed at "real-time and high-performance AI apps with low cold starts, burst
scaling, and global low-latency inference", plus partner services (self-hosted
Deepgram STT, Rime TTS) [src](https://cerebrium.ai/docs/llms.txt).

**(b) Hardware.** H100, A100, L40S among others; GPU type and count set in
`cerebrium.toml` [src](https://cerebrium.ai/docs/hardware/using-gpus.md). Two
**compute tiers**: `interruptible` (default) and `protected`, the latter billed
at **2× across GPU, CPU and memory**
[src](https://cerebrium.ai/docs/calculating-cost). Multi-region deployment is
supported, with per-region persistent-storage caches and a
`/global-persistent-storage` volume that avoids per-region copies.

**(c) Orchestration and isolation.** ⚠️ Not published, but one implementation
detail leaks usefully: a **runtime sidecar inside the container** reachable at
the link-local address **`169.254.169.253:8234`**, "reachable only from inside
the container, not from external networks"
[src](https://cerebrium.ai/docs/performance/checkpointing). Custom runtimes
(FastAPI/ASGI/WSGI, custom Dockerfiles, compiled Rust binaries) are supported
with `entrypoint`, `port`, `healthcheck_endpoint`, `readycheck_endpoint`.

**(d) Cold-start engineering.** The clearest published decomposition after
Modal's. They split cold start into **queueing** (no warm container) and
**initialization** (imports, weight load into GPU memory, CUDA kernel
compilation), and tell you to use dashboard metrics to find which dominates
[src](https://cerebrium.ai/docs/performance/faster-cold-starts). Ladder:

1. **Weights on persistent storage at `/persistent-storage`, not baked into the
   image** — "Cerebrium caches reads from persistent storage within each
   region", and a fat image "means Cerebrium has to pull and restore a larger
   image before your application can start initialization." Only bake in weights
   small enough to keep the image light. Useful datum they publish: **a standard
   Hugging Face load "can take 40+ seconds even when reading from storage at
   ~2 GB/s"** for large models. Also: **more CPU cores parallelise reads** and
   improve pull-through.
2. **Initialise at module scope**, outside the request path.
3. **Memory and GPU checkpointing (beta)** — snapshots CPU *and* GPU memory to
   "skip imports, model loading, and CUDA kernel compilation". The trigger
   design is the most flexible of the three checkpointing providers: **you POST
   to the sidecar yourself**, at whatever point in your startup you choose —
   "The state of your application at this exact moment is what will be restored
   for future container launches." Enabled via
   `[cerebrium.experimental] checkpointing = true`. Documented cost: **"Checkpoint
   duration scales with the amount of memory captured — from a few seconds for
   small CPU workloads to several minutes for large GPU snapshots"**, hence a
   ≥300 s client timeout. Early beta: "Some workloads may fail to checkpoint or
   restore" [src](https://cerebrium.ai/docs/performance/checkpointing).

**(e) Autoscaling.** `cerebrium.toml` `[cerebrium.scaling]`: `min_replicas`
(default 0), `max_replicas`, `cooldown` (seconds at reduced concurrency before
scale-down), `replica_concurrency` (a **hard** per-replica cap). The scaling
signal is **configurable** — "concurrency utilization, requests per second, CPU
usage, or memory usage" against a target threshold, with new instances starting
"within seconds". Scale-out is derived from in-flight work: at
`replica_concurrency=1` with 3 in-flight requests and no replicas, it starts the
replicas needed to serve them. In multi-region deployments min/max apply to the
app as a whole, with the platform distributing across eligible regions
[src](https://cerebrium.ai/docs/scaling/scaling-apps). Graceful termination via
SIGTERM handling in custom runtimes, explicitly to "avoid 502 errors".

**(f) Serving engine and batching.** Bring your own; vLLM and LitServe are the
documented paths. The key documented default: **`replica_concurrency` defaults
to 1 for GPU apps and 100 for CPU-only apps** — so an LLM on vLLM is
*single-request-at-a-time until you raise it*, which would silently destroy
throughput. They say so: raise `replica_concurrency` and let vLLM "combine them
into optimal batch sizes"
[src](https://cerebrium.ai/docs/scaling/batching-concurrency).

**(g)–(h)** Multi-region for latency and data residency; audit log; dashboard
metrics and request logs; docs MCP server and agent skill.

**(i) Pricing and what it implies.** Per **second** for GPU, CPU and memory
separately; persistent storage per GB-month; `protected` tier at 2×. Their
billing boundary is stated precisely and differs from Beam's
[src](https://cerebrium.ai/docs/calculating-cost):

| Phase | Billed? |
|---|---|
| Build (only when the environment changes; steps cached) | yes |
| **Cold start** — spinning up servers, loading the environment, connecting storage | **no** |
| **Model initialization** — module-scope code, loading weights to GPU, imports | **yes** |
| Function runtime — code inside the request handler | yes |

That split creates a sharp incentive: **initialization time is the customer's
cost, so checkpointing pays the customer directly** — which is presumably why
Cerebrium built it. Sample rates from their worked example: A10 24 GB
$0.000306/s (≈$1.10/hr), CPU $0.00000655/core/s, memory $0.00000222/GB/s.

**(j) Published numbers.** The 40 s HF-load-at-2 GB/s datum; checkpoint
capture "seconds to several minutes"; the rate sample above. ⚠️ No
before/after checkpoint latency numbers published.

**(k) What transfers.**

| Transfers well | Does not transfer |
|---|---|
| **Explicit-trigger checkpointing.** POST-to-sidecar-when-ready is the most flexible of the three designs and the easiest to bolt onto an existing engine startup (trigger after the engine reports ready and CUDA graphs are captured). | The `interruptible`/`protected` 2× tier — a spot-capacity construct. |
| **Cold start decomposed into queueing vs initialization, measured separately.** Most in-house dashboards report one number and hide which half is broken. | |
| **Weights on shared storage, not in the image**, with more CPU cores to parallelise the read. The "40+ s even at ~2 GB/s" figure is a good sanity check against [`06-cold-start.md` §2](06-cold-start.md). | |
| **`replica_concurrency` defaulting to 1 on GPU** as a cautionary tale: any platform layer in front of a continuous-batching engine must default to *high* concurrency or it silently serialises the batch. | |
| **SIGTERM draining** to avoid 502s during scale-down — see [`05-autoscaling-and-predictive-scaling.md` §5](05-autoscaling-and-predictive-scaling.md). | |

---

### 3A.11 Lambda — Inference API

⚠️ **Status change, flagged because it affects any plan built on it.** As of
the fetched page, Lambda's inference product is being retired: "**As the
Inference API winds down, you can continue deploying and scaling models
seamlessly on NVIDIA GPU instances**" [src](https://lambda.ai/inference). The
`docs.lambda.ai/public-cloud/lambda-inference-api/` path now serves only a
redirect notice.

**(a) What they sell (now).** GPU instances and clusters — i.e. Lambda is
reverting to being a **neocloud**, not an inference platform. Their GPU-hour
prices are already tracked in
[`../cross-cutting/cloud-pricing.md` §4](../cross-cutting/cloud-pricing.md);
use that, not this document.

**(b)–(j)** ⚠️ Not applicable / not published for a winding-down product. A
historical Lambda datum that remains useful is Baseten's GH200 inference
testing on Lambda Cloud
[src](https://www.baseten.co/blog/testing-llama-inference-performance-nvidia-gh200-lambda-cloud/).

**(k) What transfers.** One strategic observation: **a GPU cloud tried to move
up-stack into per-token inference and retreated.** The per-token business
requires the engine, routing, speculation and cache work that Baseten, fal and
Cloudflare invest in continuously; renting GPUs does not. If you are choosing
between "buy tokens" and "run the cluster", note that the vendor closest to the
metal concluded the middle position was not defensible. (inferred)

---

### 3A.12 Nebius Token Factory

**(a) What they sell.** "Inference at enterprise scale, from open models to
governed production", in two tiers: **shared** self-service per-token access,
and **dedicated endpoints** with "**99.9 % SLA**, custom autoscaling and
optional regional deployment" [src](https://nebius.com/services/token-factory).
Plus fine-tuning, custom-model deployment, and RAG tooling (embeddings +
PGVector).

**(b) Hardware and where it runs.** Nebius is a **neocloud that owns its
datacenters** — stated locations **Finland, France and the US**. This is the
one provider in §3A whose silicon is genuinely its own, and it is the same
company Baseten names as a supply source (§3A.4(b)). Its GPU-hour rates are in
[`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md).

**(c) Orchestration and isolation.** ⚠️ Not published.

**(d) Cold-start engineering.** ⚠️ Not published — for a per-token API the
provider absorbs cold start entirely and it never surfaces to the customer.

**(e) Autoscaling.** "Custom autoscaling" on dedicated endpoints ⚠️; shared
tier autoscales opaquely, claiming "hundreds of millions of tokens per minute".

**(f) Serving engine.** ⚠️ Not published. 60+ open models including
**DeepSeek-V4-Pro, Qwen3-235B, Kimi-K3, GLM-5.1**, Llama variants, GPT-OSS;
embeddings (bge-en-icl, e5-mistral-7b-instruct, Qwen3-Embedding-8B).

**(g) SLAs.** **99.9 %** on dedicated endpoints — one of only two numeric SLA
figures found in §3A (the other being Baseten's 99.99 % *capability* claim
and fal's 99.99 % *uptime* claim, neither stated as contractual).

**(h)–(i)** OpenAI-compatible API; "transparent, predictable $/token pricing"
with "clear input/output separation and volume discounts" ⚠️ — the rate card
lives on `tokenfactory.nebius.com` and was not fetched.

**(j)** "Sub-second" latency targets ⚠️ (unqualified).

**(k) What transfers.** Nebius is the **make-vs-buy comparator**: it runs its
own metal, publishes per-token prices, and offers a 99.9 % SLA. That is the
price your cluster must beat on the models you actually serve — the same role
the vendor API column plays in [`../README.md` §3](../README.md) and
[`../matrix/cost-matrix.md`](../matrix/cost-matrix.md). Architecturally it
publishes nothing to copy.

---

### 3A.13 Hugging Face Inference Endpoints

The most transparent autoscaler in §3A, and the only one still defaulting to
a utilisation signal.

**(a) What they sell.** Managed single-model endpoints on rented hyperscaler
instances, billed per hour (metered per minute).

**(b) Hardware and where it runs.** Explicitly on **AWS, GCP and Azure**, with
the instance type and cloud chosen by the user
[src](https://huggingface.co/docs/inference-endpoints/en/pricing):

| Cloud | GPU | $/hr | GPU mem |
|---|---|---:|---|
| AWS | nvidia-t4 ×1 | 0.50 | 14 GB |
| AWS | nvidia-l4 ×1 | 0.80 | 24 GB |
| AWS | nvidia-a10g ×1 | 1.00 | 24 GB |
| AWS | nvidia-l40s ×1 | 1.80 | 48 GB |
| AWS | nvidia-a100 ×1 | 2.50 | 80 GB |
| AWS | nvidia-h200 ×1 | 5.00 | 141 GB |
| GCP | nvidia-h100 ×1…×8 | 10.00…80.00 | 80…640 GB |

Plus accelerators: AWS `inf2` $0.75/hr (1× Inferentia2), GCP TPU v5e $1.20/hr
(1×) to $9.50/hr (8×). ⚠️ Note the **H100 at $10/hr is ~2.9× Modal's and ~5.3×
fal's discounted rate** — this is a managed wrapper on hyperscaler on-demand
with no capacity arbitrage, and it prices like one. (inferred)

**(c) Orchestration and isolation.** ⚠️ Not published; per-customer dedicated
instances, so the tenancy boundary is the instance.

**(d) Cold-start engineering.** ⚠️ **No mechanism published, and the docs are
candid that it hurts:** scaling to zero means "cold start latency when new
requests arrive", the endpoint "Returns `502 Bad Gateway` during replica
initialization", and there is "**No built-in request queueing** (implement
client-side queue with error handling)"
[src](https://huggingface.co/docs/inference-endpoints/en/autoscaling). Compare
RunPod, Beam and Baseten, all of which queue. A 502 as the documented
cold-start behaviour is the weakest design in §3A.

**(e) Autoscaling — published in full, which is rare.**

| Aspect | Value |
|---|---|
| GPU signal (default) | average GPU utilisation ≥ **80 %** over a **1-minute** window |
| CPU signal | average CPU utilisation ≥ **80 %** |
| Scale-up evaluation | every **1 minute** |
| Scale-down evaluation | every **2 minutes**, with a **300 s** stabilisation period after a scale-down |
| Alternative signal (beta) | **> 1.5 pending requests per replica** over the past **20 s**; pending = in-flight + processing; threshold adjustable |
| Scale to zero | after **> 15 minutes** idle |
| Enterprise | full customisation of thresholds |

They also document precisely why the utilisation signal misbehaves: "Long
initialization periods reduce autoscaling effectiveness; GPU utilization may
drop below threshold during model loading" — i.e. a replica loading a 500 GB
checkpoint reads as *idle* and the autoscaler can oscillate. **This is the
canonical argument for the pending-request signal** and belongs alongside
[`05-autoscaling-and-predictive-scaling.md` §2](05-autoscaling-and-predictive-scaling.md).

**(f) Serving engine.** Adopted, and named: **vLLM, Text Generation Inference
(TGI), SGLang, Text Embeddings Inference (TEI), llama.cpp**, or a custom
container [src](https://huggingface.co/docs/inference-endpoints/en/autoscaling).

**(g)–(h)** ⚠️ No published SLA. Console + API; quotas per account.

**(i) Pricing.** Formula given explicitly:
`instance hourly rate × ((hours × #min replica) + (scale-up hrs × #additional replicas))`.
Billed **by the minute**, charged only while *running* or *initializing* —
note that **initialization is billed**, matching Cerebrium and unlike Beam.
Scale-to-zero costs nothing but still counts against quota; paused endpoints do
not. No per-token pricing for Endpoints.

**(j) Published numbers.** The thresholds table and rate table above.

**(k) What transfers.**

| Transfers well | Does not transfer |
|---|---|
| **The autoscaler constants** — 80 %/1 min/2 min/300 s stabilisation, and the 1.5-pending-requests-per-replica alternative — are concrete starting values for a Kubernetes HPA/KEDA policy. | The instance pricing, which is a resale markup. |
| **The documented failure mode of utilisation-based scaling during model load.** Cite it when someone proposes GPU-util HPA for a 500 GB model. | |
| **"Billed while initializing"** as the incentive that makes weight-load speed a first-class metric. | |
| **Negative lesson: never answer a cold start with a 502.** Queue, with a bounded wait and a documented timeout. | |

---

### 3A.14 Anyscale / Ray Serve LLM

The BYOC platform play: not a capacity vendor at all, but the orchestration
layer, running in *your* cloud account.

**(a) What they sell.** Anyscale sells managed Ray — "create optimized Ray
clusters in your cloud account" with "pay-as-you-go, autoscaling node pools"
[src](https://docs.anyscale.com/llm/serving/). The serving layer, **Ray Serve
LLM**, is open source and usable without Anyscale
[src](https://docs.ray.io/en/latest/serve/llm/index.html).

**(b) Hardware and where it runs.** Whatever is in your cloud account —
**BYOC**. This makes Anyscale structurally the same choice as Northflank (§3A.9),
one layer up.

**(c) Orchestration and isolation.** **Ray** — actors, deployments,
placement groups — rather than Kubernetes primitives directly (Ray itself
commonly runs on Kubernetes via KubeRay). Multi-node serving is native.

**(d) Cold-start engineering.** ⚠️ Not published as a distinct feature.

**(e) Autoscaling.** `autoscaling_config` on an `LLMConfig` manages replicas
between min and max; Anyscale adds node-pool autoscaling beneath it
[src](https://docs.ray.io/en/latest/serve/llm/index.html).

**(f) Serving engine and per-request optimisations.** Backends are **vLLM and
SGLang**; Ray Serve LLM supplies the orchestration around them, and the feature
list maps almost one-for-one onto what a large-MoE deployment needs:

- **tensor, pipeline, expert, and data-parallel-attention** parallelism
  (`tensor_parallel_size` etc.);
- **prefill–decode disaggregation**, to scale the two phases independently;
- **multi-LoRA** on a shared base model;
- **prefix-aware routing** for cache hit rate;
- OpenAI-compatible API via `build_openai_app()`;
- built-in metrics and a Grafana dashboard.

Install is `pip install "ray[llm]"`. ⚠️ Version and release date of the feature
set were not obtainable from the index page; treat the list as "present in the
current docs as of 2026-09-19".

This is the same capability set as Baseten's Dynamo-based stack (§3A.4(f)) and
covers the parallelism strategies this repo's models need — see
[`../cross-cutting/serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md)
for wide-EP and DP-attention, and
[`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)
for engine support per GPU.

**(g)–(i)** Multi-model deployment and traffic routing from one config; metrics
and Grafana; Anyscale pricing is a management fee over your own cloud spend ⚠️
(not fetched).

**(j)** ⚠️ No published benchmark numbers on the fetched pages.

**(k) What transfers.** **More than any other entry: it is not a provider you
buy from, it is software you can run.** Ray Serve LLM is a legitimate candidate
for the orchestration layer of a bare-metal cluster — it gives multi-node
serving, PD disaggregation, prefix-aware routing, multi-LoRA and an
OpenAI-compatible front end without writing a control plane. The competing
choice is Dynamo (Baseten's pick) or plain Kubernetes + an engine's own router;
see [`09-reference-architectures.md` §2](09-reference-architectures.md). What
does *not* transfer is Anyscale's commercial layer, which exists to manage
cloud accounts you do not have.

---

### 3A.15 Cloudflare Workers AI

The edge case, literally — and the most architecturally distinctive stack in
§3A, because its constraints are the opposite of yours: hundreds of tiny
sites, no room for an 8-GPU node, and a mandate to run many models per GPU.

**(a) What they sell.** A per-token / per-unit serverless model API bound to
the Workers runtime. Billing is in **Neurons** at **$0.011 per 1,000 Neurons**,
with **10,000 Neurons/day free** on both Free and Paid plans, and per-model
prices published in token terms
[src](https://developers.cloudflare.com/workers-ai/platform/pricing/). Range:
LLMs **$0.017–$1.400 per 1M input tokens** (Llama 3.2-1b $0.027/M in;
DeepSeek V4-Pro $1.320/M in); embeddings $0.012–$0.204/M; image generation
$0.000059–$0.015 per 512×512 tile; audio per minute or per 1k characters;
ResNet-50 $2.51 per 1M images. Cached input is discounted on some frontier
models. ⚠️ The launch post priced two tiers — "Regular Twitch Neurons" at
$0.01/1k and "Fast Twitch Neurons" at $0.125/1k
[src](https://blog.cloudflare.com/workers-ai/) — which the current single
$0.011/1k rate has replaced; the tiering is gone.

**(b) Hardware and where it runs.** GPUs in Cloudflare's own edge PoPs.
Trajectory: 7 sites at launch (2023-09-27) → "roughly 100" data centers by end
of 2023 → "nearly everywhere" by end of 2024
[src](https://blog.cloudflare.com/workers-ai/); **"over 180 cities"** by
2024-09-26, having doubled capacity in a year, on **12th-generation compute
servers** with newer GPUs
[src](https://blog.cloudflare.com/workers-ai-bigger-better-faster/). Infire
benchmarks were run on an **H100 NVL**
[src](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine).

**(c) Orchestration and isolation — Omni.** Their internal platform for running
many models per GPU, published 2025-08-27 by Sven Sauleau
[src](https://blog.cloudflare.com/how-cloudflare-runs-more-ai-models-on-fewer-gpus):

- A **control plane** routes each request "to the closest Omni instance that
  has available capacity".
- Per-model processes isolated with **Linux namespaces and cgroups**; Python
  dependency isolation via **`uv`** virtualenvs.
- A **custom FUSE-based `/proc/meminfo`** so each model sees only its own
  allocated memory.
- **Memory over-commitment**: Omni injects a **CUDA stub library** that
  intercepts allocation calls and forces **unified memory** mode, allowing
  roughly **400 % of GPU memory** to be allocated on one GPU by swapping
  inactive models out to host RAM. **A 5 GB model cold-starts in ~156 ms over
  PCIe 4.0.**
- In production: **13 models co-resident on single GPUs**, rolling out weekly
  across the catalogue. Supports vLLM, plain Python, and Infire as engines.

That ~156 ms figure is the single most striking cold-start number in this
document, and it is only possible because the "cold start" is a **PCIe page
migration of already-initialised state**, not a filesystem read plus CUDA init.

**(d) Cold-start engineering.** See Omni above — swap-in over PCIe, not boot.

**(e) Autoscaling.** ⚠️ Not published; capacity is managed per-PoP by the Omni
control plane rather than exposed to users.

**(f) Serving engine — Infire.** Their own, in **Rust**, published 2025-08-27
[src](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine):

*Why not vLLM.* Four stated reasons: vLLM is "best optimized for large data
centers" and "much less optimized for dynamic workloads, distributed networks";
it cannot co-host multiple models on one GPU without MIG; their security model
required sandboxing it under **gVisor**, which added overhead; and they wanted
low-level control.

*Architecture.* Three parts: an OpenAI-compatible HTTP server built on
**hyper**, a **batcher**, and the engine.

*Techniques.* **Continuous batching with chunked prefill** (fill spare batch
slots with prefill tokens from incoming prompts); **paged KV cache**, giving
"essentially unlimited parallelism under typical load" by assigning extra pages
when a prompt exceeds the cache; **a dedicated CUDA graph for every possible
batch size, created on demand**. **Quantization is not implemented** — listed
as future work.

*Measured*, H100 NVL, ShareGPT v3, 4,000 prompts, concurrency 200:

| Engine | Requests/s | Tokens/s | CPU load |
|---|---:|---:|---:|
| **Infire** | **40.91** | **17,224.21** | **25 %** |
| vLLM 0.10.0 | 38.38 | 16,164.41 | 140 % |
| vLLM 0.10.0 under gVisor | 37.13 | 15,637.32 | 250 % |

Read this carefully: the throughput win over bare vLLM is **~6.6 %**, which is
small. **The real result is the CPU column** — 25 % vs 140 %, a 5.6× reduction
in host CPU per unit of inference, which on an edge box with few cores and many
co-resident models is the binding constraint. It also quantifies the **gVisor
tax**: 140 % → 250 % CPU and a further ~3 % throughput loss.

**(g) Other per-request optimisations** (2024-09-26,
[src](https://blog.cloudflare.com/workers-ai-bigger-better-faster/)):

- **KV cache compression**, open-sourced, built on PagedAttention with
  **per-attention-head compression rates**. On LongBench with Llama-3.1-8B they
  "retain over 95 % task performance while reducing cache size by up to 8×",
  with throughput up **3.44× at 8× compression and 5.18× at 64×**.
- **Speculative decoding** via **prompt-lookup decoding**: generation speed up
  **~40 % on llama-3.1-8b-instruct and ~70 % on the 70B**, with acknowledged
  output-quality trade-offs.
- Hardware: "two to three times the throughput" from the 12th-gen servers.
- Operating points published per model: **80+ TPS for 8B models**, **TTFT
  ~300 ms depending on user location**, 128K context for Llama 3.1/3.2.

**(h) Observability / developer surface.** Workers bindings, REST API, AI
Gateway (unified routing, caching and logging across providers), LoRA support,
a Batch API, Python support, and a model catalogue publishing TTFT/TPS/context
window/pricing per model.

**(i) Pricing and what it implies.** Neurons abstract away which GPU ran your
request, which is only possible because Cloudflare controls model, engine and
placement. Per-token rates at the small end ($0.017–0.027/M input) are far
below what a dedicated replica of the same model costs unless it is extremely
well utilised — i.e. **the Neuron price is a pooled-utilisation price**, and
the comparison for a self-hoster is not "can I beat $0.027/M" but "do I have
enough sustained traffic on this model to keep a replica busy." (inferred)

**(j) Published numbers.** All of the above: 40.91 req/s vs 38.38; 25 % vs
140 % CPU; 8× KV compression at >95 % task performance; 3.44×/5.18× throughput;
+40 %/+70 % from prompt-lookup; 400 % memory over-commit; 156 ms 5 GB swap-in;
13 models/GPU; 180+ cities; 80+ TPS at 8B; ~300 ms TTFT.

**(k) What transfers to bare metal.**

| Transfers well | Does not transfer |
|---|---|
| **Per-head KV cache compression** — open-sourced, and the 8×-at->95 % result directly extends the KV budget arithmetic in [`../METHODOLOGY.md` §2](../METHODOLOGY.md) and the concurrency tables in [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md). Verify per model before trusting it. | **Edge distribution across 180+ cities.** |
| **Prompt-lookup decoding** as a zero-training speculative method for repetitive/agentic workloads — cheaper to adopt than MTP/EAGLE, complements Baseten's suffix-automaton finding (§3A.4(f)). See [`../cross-cutting/serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md). | **Neuron-style abstract billing.** |
| **The gVisor cost, quantified** (140 % → 250 % CPU). If anyone proposes sandboxing your engine, this is the number. | **Omni's 400 % over-commit via unified memory** — a *small*-model technique. Unified-memory paging under a 510 GB or 1.56 TB checkpoint would thrash; the 156 ms figure is for a 5 GB model. |
| **CUDA graph per batch size, built on demand** — a concrete alternative to a fixed capture list. | **Writing your own engine.** Infire's own numbers say a from-scratch engine buys ~6.6 % throughput over vLLM. Unless host CPU or co-tenancy is your binding constraint, that is not a rational build. |
| **The honest reading of "we wrote our own engine":** they did it for *co-tenancy and CPU efficiency*, not raw tokens/s. | |

---

### 3B — Per-token model-API providers, custom silicon and first-party operators (they sell tokens)

---

### 3B.1 Together AI

The most documented inference operator in this study, by a wide margin. Roughly
a dozen engineering posts across 2024–2026 describe the engine, the speculator
system, the disaggregation design, the autoscaler and the uptime architecture,
with numbers and dates.

**(a) What they sell.** Serverless per-token endpoints; **Dedicated Endpoints**
billed hourly per GPU; **Dedicated Container Inference** (bring-your-own Docker
image) for generative-media models; GPU Clusters (on-demand and reserved); a
Batch API; fine-tuning. Serverless is explicitly positioned as the on-ramp —
*"Most teams start with serverless inference and move to dedicated endpoints at
scale"* [src](https://www.together.ai/pricing).

**(b) Hardware and where it runs.** Owned and operated, not rented from a
hyperscaler — the uptime post is unusually direct: *"one ticket covers the
hardware, network, storage, and software"* rather than renting hyperscaler
capacity [src](https://www.together.ai/blog/99-9-uptime-for-inference)
(2026-07-16). Generations named across posts: A100 SXM4 and H100 SXM5 with 3200
Gbps InfiniBand in the 2023 cluster post
[src](https://www.together.ai/blog/20-exaflops-gpu-clusters) (2023-11-13);
HGX B200 as the serving target from 2025 onward
[src](https://www.together.ai/blog/fastest-inference-for-deepseek-r1-0528-with-nvidia-hgx-b200)
(2025-07-17); GB200 NVL72 clusters at 36k GPUs
[src](https://www.together.ai/blog/nvidia-gb200-together-gpu-cluster-36k).
Dedicated-endpoint GPU menu as published: L40 48GB $1.49/h, L40S 48GB $2.10/h,
A100 PCIe/SXM 80GB $2.40–$2.59/h, H100 80GB $3.36/h, H200 141GB $4.99/h
[src](https://www.together.ai/blog/on-demand-dedicated-endpoints) (2025-03-13);
the current pricing page lists dedicated HGX H100 at $3.99/h (promotional, from
$5.49) and HGX B200 at $8.99/h, with clusters at H100 $3.99 / H200 $5.99 /
B200 $8.19 per GPU-hour and reserved 7–180+ day terms at $3.19–$7.99
[src](https://www.together.ai/pricing).

**(c) Orchestration and isolation.** Multiple deployments behind one stable
endpoint, with canary, blue-green and rolling updates plus automatic rollback
on performance thresholds; A/B testing and shadow traffic mirroring
[src](https://www.together.ai/blog/the-production-platform-for-open-weight-ai-inference)
(2026-07-23). Container inference isolates *"between batch, real-time, and
untrusted traffic"* using multiple independent queues and policy-driven traffic
control rather than a single FIFO
[src](https://www.together.ai/blog/dedicated-container-inference) (2026-02-12).
The underlying container runtime and sandbox technology are **not published**;
the CLI surface (`tg beta endpoints update …`) and replica vocabulary are
Kubernetes-shaped *(inferred)*, but Together never names an orchestrator.

**(d) Cold-start engineering.** The only provider here with published numbers.
Measured on 1×H100 replicas: base 9B model **~86 s to READY**; a custom
fine-tune (18 GB) **~145 s**; scale-up 1→2 replicas **~2.5 min**; restart from
STOPPED **1–2 min**
[src](https://www.together.ai/blog/autoscaling-endpoints-for-llm-inference)
(2026-07-31). At the platform level: deployment times **2–14 minutes** by model
size (small ~2–5 min, frontier MoE ~7–14 min) and **"4× faster warm starts"**
via fleet-wide model-weight caching and proactive pre-warming
[src](https://www.together.ai/blog/the-production-platform-for-open-weight-ai-inference)
(2026-07-23). The four cold-start stages named — GPU placement, weight download,
engine load, warmup — are the same four a self-hoster faces.

**(e) Autoscaling.** Eight inference-native metrics in three families:
concurrency-driven (`inflight_requests`, the default, target 8), SLO-driven
(`ttft`, `e2e_latency`), efficiency-driven (`gpu_utilization`,
`token_utilization`). Proportional control:
`desired = ceil(observed / target × current)`. Asymmetric windows — short
`scale_up_window` (60 s in their example), long `scale_down_window` (default
5 min). **Scale-to-zero does not auto-wake**: `min_replicas: 0` requires
`max_replicas: 0`, i.e. an explicit stop, which they say suits dev/staging and
not production SLOs.

The replay experiment is the most transferable single result in this document.
Same load, three policies, Qwen3.5-9B, 1–3 replicas:

| Policy | Scaled | Replica-min | Requests | Outcome |
|---|---|---:|---:|---|
| `inflight_requests` (8) | 1→2→3 | 26 | 40.6k | Scaled correctly; p95 improved |
| `ttft` p95 (300 ms) | never | 18 | 46.4k | Engine healthy; **client** p95 hit 3–5 s |
| `gpu_utilization` (75 %) | never | 18 | 46.5k | GPU never crossed threshold while saturated |

*"A GPU can read 60% utilized while the engine's request queue is already
backing up"* — only the concurrency signal detected real pressure
[src](https://www.together.ai/blog/autoscaling-endpoints-for-llm-inference).

**(f) Serving engine and per-request optimizations.** Own engine (Together
Inference Engine), not a vLLM fork. Lineage and components:

| Component | What is claimed | Date / source |
|---|---|---|
| Together Inference Engine 2.0 | 4× decode throughput vs open-source vLLM; 1.3–2.5× vs Bedrock/Azure/Fireworks/OctoAI; FlashAttention-3 at *"up to 75% of an H100's maximum"*; proprietary MHA + GEMM kernels for quantized inference; quality-preserving quantization via QuIP-style incoherence processing | 2024-07-18 [src](https://www.together.ai/blog/together-inference-engine-2) |
| Speculative decoding (research) | MagicDec (self-speculation + StreamingLLM), Adaptive Sequoia trees; **2.0×** on LLaMA-2-7B-32K, **1.84×** on LLaMA-3.1-8B, on 8×A100 — and the key claim that speculation *still pays at large batch* once the KV cache makes decode memory-bound again | 2024-09-05 [src](https://www.together.ai/blog/speculative-decoding-for-high-throughput-long-context-inference) |
| Medusa / Sequoia / SpecExec | Named as integrated algorithms, with custom drafters trained on RedPajama (30T tokens) | 2024-07-18 [src](https://www.together.ai/blog/together-inference-engine-2) |
| Together Kernel Collection (TKC) | FP8 GEMM **>2×** over H100 FP8 GEMMs; attention **1.8×** faster than FlashAttention-3; built on CUTLASS, Triton and ThunderKittens — the FP8 kernel in *"under two weeks, fewer than 200 lines"* | 2025-02-13 [src](https://www.together.ai/blog/nvidia-hgx-b200-with-together-kernel-collection) |
| Custom speculators (product) | Trained on a customer's own traffic: **1.23–1.45×** over the base speculator, **1.85–2.97×** over plain next-token; **23–26 %** GPU-hour reduction vs base speculator, **49–61 %** vs unoptimized. 20M tokens (~10k pairs) already buys >1.10× | 2025-05-12 [src](https://www.together.ai/blog/customized-speculative-decoding) |
| ATLAS (adaptive speculator) | Heavyweight static speculator + lightweight adaptive speculator learning from live traffic + confidence-aware controller adjusting lookahead. DeepSeek-V3.1 **500 tok/s** (2.65×); Kimi-K2 **460 tok/s**; up to **4×** over FP8 baseline (105→501 TPS) — 4×B200, **batch size 1** | 2025-10-10 [src](https://www.together.ai/blog/adaptive-learning-speculator-system-atlas) |
| CPD — cache-aware PD disaggregation | Adds a **pre-prefill tier** above ordinary prefill/decode: the router estimates prompt reusability and sends low-reuse ("cold") requests to pre-prefill nodes so they cannot block the warm path. Three-level cache — GPU HBM, host DRAM, cluster-wide RDMA cache. **35–40 % higher sustainable throughput** vs conventional disaggregation, tighter tails, sub-second-to-low-second median TTFT at saturation. B200, TP4 prefill nodes | 2026-03-04 [src](https://www.together.ai/blog/cache-aware-disaggregated-inference) |
| DeepSeek-V4-class serving | Three coexisting KV layouts (CSA stride-4 / HCA stride-128 / SWA ~128-token exact local), each needing its own eviction and prefix-reuse policy; single HGX B200 node moved **~1.2M → 3.7M tokens** of KV capacity by changing the SWA cache policy; SWA recompute-on-hit bounded to ~8K tokens; per-token KV initially 3.8 KB vs V3's 3.4 KB | 2026-05-11 [src](https://www.together.ai/blog/serving-deepseek-v4-why-million-token-context-is-an-inference-systems-problem) |
| Kernels practice | ThunderKittens cuts *"1,000+ lines of CUDA to 100–200"*; FP4/FP8 GEMMs up to **2× over cuBLAS on H100** within one week of Blackwell access; team ~15 people as of 2025-03 | 2026-04-01 [src](https://www.together.ai/blog/inside-the-together-ai-kernels-team) |
| Consolidated levers | FP16→FP8→FP4 worth **20–40 %** throughput; tuned MTP/speculation **20–50 %** faster decode; regional inference proxies shave **50–100 ms** off TTFT | 2026-01-22 [src](https://www.together.ai/blog/optimizing-inference-speed-and-costs) |

**(g) Routing, concurrency, rate limits, SLAs.** Contractual **99 % single-DC /
99.9 % multi-DC**, with 99.9 % *delivered* claimed. Engineering: weights
deployed across two facilities each sized to absorb full load, live traffic to
both (not cold standby), seconds-level failover, passive hardware telemetry plus
active health checks run *between workloads* by the scheduler — "detect fast,
drain, replace". Measured at inference completion, not at the load balancer.
They cite 2B+ tokens/minute during evaluation windows
[src](https://www.together.ai/blog/99-9-uptime-for-inference) (2026-07-16), and
**>400 trillion tokens/month** platform-wide
[src](https://www.together.ai/blog/the-production-platform-for-open-weight-ai-inference).

**(h) Observability / developer surface.** Organisation-level **Prometheus
endpoints** for external scraping, in-product analytics, `tg` CLI, REST and web
UI. Batch API: ≤50,000 requests/batch, 100 MB input file, 10 MB/line, **30B
tokens enqueued per model**, up to 50 % discount, charged only for successful
responses, dedicated endpoints eligible but **not discounted**
[src](https://docs.together.ai/docs/batch-inference).

**(i) Pricing and what it implies.** DeepSeek V4.1 Flash $0.30/$1.20 per 1M;
DeepSeek V4 Pro 0813 $1.32/$3.96; Qwen3.7-Plus $0.32/$1.28; Llama 3.3 70B
$1.04/$1.04 [src](https://www.together.ai/pricing). *(Inferred)* Together sits
in the middle of the DeepSeek-V4.1-Flash market (§5.1) at exactly the DeepSeek
first-party output price ×2 — consistent with a business that prices off
*capability and SLA* rather than to the floor, and that expects the
price-sensitive tail to go to dedicated endpoints where utilisation risk moves
to the customer.

**(j) Published numbers.** Collected above. The headline serving figures:
DeepSeek-R1-0528 on B200 **334 TPS** serverless peak (vs 302 unoptimized), up to
**386 TPS** at batch 1 on dedicated, **2.3–2.8×** vs H200
[src](https://www.together.ai/blog/fastest-inference-for-deepseek-r1-0528-with-nvidia-hgx-b200);
"#1 output speed among GPU-based providers" on Artificial Analysis with
GPT-OSS-20B ~2×, Qwen3-235B >2.75×, Kimi-K2 >65 %, DeepSeek-R1-0528 >13 % over
the next fastest
[src](https://www.together.ai/blog/fastest-inference-for-the-top-open-source-models).

**(k) What transfers to bare metal.**

*Transfers directly.* The autoscaling result — **scale on queue depth, not GPU
utilisation, and never on TTFT alone** — is a free win and costs one Prometheus
rule. The asymmetric window discipline (fast up, slow down) is the correct
default for anything with a 90–150 s cold start, which is every LLM replica.
CPD's core insight is implementable on any cluster that already does PD
disaggregation: **classify requests by expected prefix reuse and give cold
prefills their own pool**, so a 200K-token cold prompt cannot head-of-line-block
a warm 2K one. The three-level KV hierarchy (HBM → host DRAM → RDMA pool) is
exactly what LMCache/Mooncake-style tiering gives you on open engines. And the
custom-speculator economics are the single highest-leverage number here: **20M
tokens of your own traffic is enough to beat a generic drafter**, which any
self-hoster with a month of logs already has.

*Does not transfer.* "4× faster warm starts through fleet-wide weight caching"
needs a fleet — the win comes from another replica somewhere already holding the
weights. The 99.9 % multi-DC architecture requires two facilities each sized for
full load, i.e. ~2× the hardware; a single-site cluster buys at most the
detect-fast-drain-replace half of it. TKC and the ThunderKittens kernels are not
distributed as a usable artifact for outside workloads — the *library* is open,
the tuned FP8/FP4 kernels are the product.

---

### 3B.2 Fireworks AI

The closest competitor to Together in both product shape and publication habit,
with a different emphasis: Fireworks publishes more about **quantization
methodology and deployment-shape search**, less about fleet and uptime.

**(a) What they sell.** Three tiers, explicitly: **Serverless** (per-token,
"high rate limits and postpaid billing"), **On-Demand Deployments** (per
GPU-second, "faster speeds, higher rate limits, and lower costs at scale"), and
**Enterprise** (reserved) [src](https://fireworks.ai/pricing). Plus fine-tuning,
a Batch API, multi-LoRA, and an eval/RL product line.

**(b) Hardware and where it runs.** GPU menu from the deployment docs: **A100
80GB, H100 80GB, H200 141GB, B200 180GB, B300 288GB, AMD MI325X 256GB, MI350X
288GB**, with the caveat that *"availability varies by region"* and *"not every
model runs on every GPU"*
[src](https://docs.fireworks.ai/guides/ondemand-deployments). On-demand hourly:
H100 80GB **$8.00**, H200 141GB **$8.00**, B200 180GB **$13.00**, B300 288GB
**$15.00**, GB300 288GB **$20.00** [src](https://fireworks.ai/pricing). The AMD
partnership (MI325X/MI355X) was announced 2025-10-20 with *no* benchmarks —
*"Stay tuned for benchmarks"*
[src](https://fireworks.ai/blog/fireworks-amd-ai-infrastructure-partnership).

Where it physically runs is **not published**. The nearest statement is from
2024: *"The same infrastructure for on-demand powers companies like Uber,
Doordash and Cursor"* [src](https://fireworks.ai/blog/why-gpus-on-demand)
(2024-06-03), which says nothing about ownership. *(Inferred)* the per-region
GPU availability caveat and the presence of GB300 alongside MI350X within months
of each launch is the signature of **rented neocloud capacity across several
vendors**, not an owned fleet — but Fireworks has never said so, and this is an
inference from a docs caveat. Their own 2026 post on providers-vs-routers
insists *"the company controlling the API endpoint is the same company
controlling the hardware"*
[src](https://fireworks.ai/blog/inference-providers-vs-api-routers)
(2026-03-06), which asserts operational control, not ownership.

**(c) Orchestration and isolation.** Replica-based, with `--accelerator-count`
overriding GPUs per replica, per-deployment autoscaling policy, and region
pinning. The orchestrator and sandbox are **not published**.

**(d) Cold-start engineering.** No measured cold-start times are published.
What *is* documented is the behaviour, which matters more than the number: a
scaled-to-zero deployment receiving a request **immediately returns HTTP 503
with `DEPLOYMENT_SCALING_UP`** and *"Requests to a scaled-to-zero deployment are
**not queued**"* — the client must retry
[src](https://docs.fireworks.ai/deployments/autoscaling). That is a design
choice worth copying or rejecting deliberately, not a bug.

**(e) Autoscaling.** Scale-up window **30 s** default; scale-down **10 min**
default; scale-to-zero after **1 hour** idle (minimum 5 min); deployments with
`min_replicas: 0` are **auto-deleted after 7 days** without traffic. Signals:
general load target (0–1), tokens generated/s/replica, prompt tokens/s/replica,
requests/s/replica, concurrent requests/replica — *"when multiple targets are
specified, the maximum replica count across all is used"*
[src](https://docs.fireworks.ai/deployments/autoscaling). Note the contrast with
Together: Fireworks' default down-window is **2× longer** and its up-window
**2× shorter**, the same asymmetry pushed further.

**(f) Serving engine and per-request optimizations.** Own stack (FireAttention),
not a vLLM fork.

| Component | What is claimed | Date / source |
|---|---|---|
| FireAttention V4 / NVFP4 | NVFP4 chosen over MXFP4 for Blackwell: *"2× FLOPs throughput"* vs competing micro-scaling modes, 1.5–2× less memory bandwidth, better quality from block size 16 vs 32 and FP8-compatible scales. Complete backend rewrite for TensorCore Gen 5 (`sm_100`), because Hopper `9.0a` ops are not forward-compatible. GEMM, grouped GEMM and attention all re-authored. **>250 tok/s on DeepSeek V3, 8-GPU NVLink**; **3.5×** vs SGLang FP8 on H200; beats TRT-LLM's FP4 *"by a significant margin"*. QAT recovers FP4/FP8 quality loss | 2025-05-28 [src](https://fireworks.ai/blog/fireattention-v4-fp4-b200) |
| Quantization methodology | SmoothQuant, GPTQ, Hadamard/SpinQuant outlier transforms; scales from per-tensor to per-small-group. **Four named aggression levels**: L1 MLP (excl. first/last), L2 +omitted layers +QKV, L3 +KV cache, L4 +attention prefill. Quality judged by **KL divergence and token rejection rate, prefill and generation measured separately**, on forced quantized generation — not task metrics. Llama-3.1-8B generation KLD 0.00286 (L1) → 0.00796 (L4); their bar is **KLD < 0.007** | 2024-08-01 [src](https://fireworks.ai/blog/fireworks-quantization) |
| Multi-LoRA | Cross-model continuous batching + dynamic adapter loading with caching, so *"the number of supported LoRAs is not constrained by GPU memory and new models can be added or taken down within seconds"*. **Thousands of LoRAs on one Mixtral/Mistral cluster** (Cresta). ~**90 % of base-model** speed when serving hundreds of adapters. Priced at base-model rates | 2024-09-18 [src](https://fireworks.ai/blog/multi-lora) |
| FireOptimizer | Adaptive speculative execution: drafters trained on the customer's data. **Up to 3× latency improvement** vs generic drafters; Cursor **2×**. Worked example: **76 % hit rate → 2× faster** vs a generic drafter's **29 % hit rate → 1.5× *slower*** | 2024-08-30 [src](https://fireworks.ai/blog/fireoptimizer) |
| 3D FireOptimizer | Search over **deployment shape (which GPU), sharding (data/tensor/sequence parallel, disaggregation), and scheduling (TTFT vs throughput)**, producing Pareto curves per workload. Workloads characterised as (120k in / 400k out / 116k cached), (2.5k / 1.5k), (5k / 500 / 1.5k cache) | 2025-06-14 [src](https://fireworks.ai/blog/3d-fireoptimizer) |
| Sparse-attention kernels (MiniMax M3) | Four-stage pipeline — index build, schedule, main sparse attention, combine. Uses **tcgen05**, TMA, `cp.async` gather, 128×128 MMA on SM100. **KV-stationary** execution (each selected KV block loaded once in the outer loop, queries gathered). **1.9–2.4×** over query-stationary FlashInfer, **~1.6×** over MiniMax's own open MSA kernel; full module **1.18–1.43×**. Peak ~**980 TFLOP/s at ~4.1 TB/s** HBM | 2026-07-10 [src](https://fireworks.ai/blog/kernel-optimization-for-minimax-m3-on-nvidia-blackwell) |

**(g) Routing, concurrency, rate limits, SLAs.** Serverless is sold in
**Standard / Priority / Fast** tiers, with Priority at a flat **1.25×**
Standard across every model (see §5.1) and "Fast" as a separate,
higher-priced model variant. US-only serverless models are priced at **1.5×**
base from 2026-09-01 [src](https://docs.fireworks.ai/serverless/pricing). Their
routing argument, aimed at OpenRouter: *"Proxy hops are always additive"* —
median TTFT through a router is necessarily worse, though p95 can improve via
redundancy; and routers *"lack visibility into GPU-level optimization decisions
made upstream"*. They also raise "shadow traffic" (live requests duplicated for
evaluation, invisible in the response) and note a router's DPA *"can only bind
itself"*, not its upstreams
[src](https://fireworks.ai/blog/inference-providers-vs-api-routers) (2026-03-06).
No numeric SLA is published.

**(h) Observability / developer surface.** OpenAI-compatible API, a Responses
API, CLI (`firectl`), eval-protocol tooling. Batch API: **50 % below
serverless**, results *"in a few hours, with a max turnaround time of 24
hours"*, **no upper limit on requests per batch**, datasets <500 MB
[src](https://fireworks.ai/blog/batch-api) (2025-07-31).

**(i) Pricing and what it implies.** Serverless, per 1M, input / cached / output
[src](https://docs.fireworks.ai/serverless/pricing):

| Model | Standard | Priority |
|---|---|---|
| **DeepSeek V4.1 Flash** | **$0.30 / $0.006 / $1.20** | $0.375 / $0.0075 / $1.50 |
| DeepSeek V4.1 Flash (US) | $0.45 / $0.009 / $1.80 | $0.5625 / $0.01125 / $2.25 |
| DeepSeek V4 Pro (0813) | $1.32 / $0.044 / $3.96 | $1.65 / $0.055 / $4.95 |
| DeepSeek V4 Flash (0731) | $0.22 / $0.007 / $0.66 | $0.275 / $0.00875 / $0.825 |
| **Kimi K3** | **$3.00 / $0.30 / $15.00** | $3.75 / $0.375 / $18.75 |
| Kimi K3 (US) | $4.50 / $0.45 / $22.50 | $5.625 / $0.5625 / $28.125 |
| Qwen 3.8 Max | $2.00 / $0.25 / $6.00 | $3.00 / $0.375 / $9.00 |
| GLM 5.3 Flash | $0.15 / $0.03 / $0.50 | $0.1875 / $0.0375 / $0.625 |

**A discrepancy worth recording.** OpenRouter's endpoint API lists a Fireworks
endpoint for `deepseek/deepseek-v4.1-flash` at **$0.22 in / $0.66 out / $0.007
cached** [src](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints)
— which is Fireworks' *V4 Flash 0731* price, not its V4.1 Flash price. Either
OpenRouter is routing to a differently-named deployment, or one of the two
tables is stale. Both are cited in §5.1 rather than silently picking one.
**⚠️ TO BE VERIFIED.**

*(Inferred)* The flat 1.25× Priority multiplier is the most informative pricing
signal Fireworks emits: a uniform 25 % premium for queue priority across every
model implies priority is sold as **scheduler weight against a shared pool**,
not as reserved hardware — if it were reserved capacity the premium would scale
with how hard the model is to host. Compare Anthropic's Priority Tier (§3B.13),
which *is* reserved and is sold as ITPM/OTPM commitments with a duration.

**(j) Published numbers.** In the table above. Note that the headline *"3.5×
vs SGLang"* is FP4-on-B200 against FP8-on-H200 — a hardware-generation change
and a format change together, not an engine A/B.

**(k) What transfers.** The **quantization-aggression ladder (L1–L4) plus
KLD-based acceptance** is the most directly copyable artifact in this entire
document. It gives you a defensible, task-independent way to decide how far to
push a checkpoint — measure KL divergence and token rejection on forced
quantized generation, prefill and decode separately, and hold a bar (theirs:
KLD < 0.007). Do that once and quantization stops being a vibe. The
**76 % vs 29 % acceptance-rate example** is the clearest published statement of
why generic drafters can make things *worse*, and it bounds what
[`serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md#2-speculative-decoding)
calls the acceptance-rate risk. The **KV-stationary sparse-attention pattern**
(load each selected KV block once in the outer loop, gather queries) is a real
kernel-design lesson for anyone writing DSA/CSA-family kernels, and it is the
same shape the repo's own sparse-attention models need. The autoscaling
defaults (30 s up / 10 min down / 1 h to zero) are a sane starting point.

*Does not transfer.* FireAttention is not available outside Fireworks. The
multi-LoRA *"thousands of adapters"* economics depend on aggregating demand
across customers — a single tenant with three adapters gets almost none of it.

---

### 3B.3 DeepInfra

The price floor of the GPU-based market, and the most transparent about
hardware-per-price of anyone here.

**(a) What they sell.** Per-token serverless across a wide model catalogue;
**dedicated instances** for custom/fine-tuned models billed hourly; raw GPU
cluster rental with SSH.

**(b) Hardware.** Named without hedging: *"run your own fine-tuned LLM on
**A100 / H100 / H200 / B200 / B300** with autoscaling"*
[src](https://docs.deepinfra.com/). Dedicated hourly: **A100 80GB $0.89/h,
H100 80GB $2.20/h, H200 141GB $2.69/h** [src](https://deepinfra.com/pricing).
Where the fleet runs is **not published**.

Those hourly rates are the single most useful datum DeepInfra publishes. Set
against [`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)'s planning
rows — H100 `low` $3.20 (Hyperstack), H200 `low` $3.99 — DeepInfra is **retailing
H100 at 0.69× and H200 at 0.67× the cheapest reputable on-demand price this repo
could find**. *(Inferred)* That is only possible on long-term committed or owned
capacity resold at a margin over amortised cost; it is not resale of on-demand.
It also means a self-hoster comparing "rent vs DeepInfra dedicated" is comparing
against a *better* hardware price than the one in this repo's cost grid.

**(c) Orchestration and isolation.** Private deployments *"for users needing
data isolation or custom models"*; the mechanism is **not published**.

**(d) Cold-start engineering.** **Not published.**

**(e) Autoscaling.** Stated to exist ("with autoscaling"), billed at
**minute granularity** for custom deployments, with *"no idle GPU charges or
minimums"* on serverless [src](https://deepinfra.com/pricing). Signals,
windows and scale-to-zero behaviour are **not published**.

**(f) Serving engine.** **Not published.** DeepInfra publishes no engine or
kernel work. What it *does* publish, via OpenRouter's per-endpoint metadata, is
its **quantization per model**, and this is where DeepInfra becomes interesting:
it operates several endpoints for the same model at different precisions and
different prices — `deepinfra/bf16`, `deepinfra/fp8`, `deepinfra/turbo` all
appear for `openai/gpt-oss-120b` at $0.037/$0.17, $0.20/$0.95 and $0.15/$0.60
respectively [src](https://openrouter.ai/api/v1/models/openai/gpt-oss-120b/endpoints).
For `qwen/qwen3.8-27b` its endpoint is labelled **bf16** at $0.15/$1.875 —
i.e. **DeepInfra undercuts most FP8 competitors while serving BF16**, which is
either a very good deal or a labelling question. **⚠️ TO BE VERIFIED** —
OpenRouter's `quantization` field is provider-declared.

**(g) Routing, rate limits, SLAs.** **Not published** beyond OpenAI-compatible
rate limits.

**(h) Observability.** OpenAI SDK compatibility, *"no code changes required"*.

**(i) Pricing.** Per 1M [src](https://deepinfra.com/pricing): DeepSeek-V4-Flash-0731
$0.06/$0.18; DeepSeek-V4-Pro $1.30/$2.60; DeepSeek-V3 $0.32/$0.89; Qwen3.5-9B
$0.10/$0.15; Qwen3-Max $1.20/$6.00; Qwen3.6-27B $0.32/$3.20; Kimi-K2.6
$0.75/$3.50; Kimi-K3 $2.85/$14.25; Llama-3.1-8B $0.02/$0.04; Llama-3.3-70B
$0.10/$0.32. Via OpenRouter, DeepSeek V4.1 Flash at **$0.14/$0.42** (fp8) —
**the cheapest endpoint for that model in the entire market on 2026-09-19**
(§5.1).

*(Inferred)* $0.14/$0.42 is **below DeepSeek's own first-party $0.15/$0.60
off-peak price**. A reseller undercutting the model's author on output by 30 %
is either running at materially higher utilisation than DeepSeek's own fleet,
running a more aggressive quantization, or buying share. The repo's own numbers
say the first is plausible: DeepSeek's published margin analysis (§3B.10) implies
its API is priced far *above* its marginal cost, leaving a wide corridor.

**(j) Published numbers.** Throughput/latency: **none published**. Uptime via
OpenRouter's rolling 30-minute window: 100.0 % for `gpt-oss-120b`, 94.25 % for
`qwen3.8-27b`, 82.11 % for `kimi-k3` on 2026-09-19
[src](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints) — the
lowest of any Kimi-K3 endpoint listed that day, which is a real datum about what
the cheap tier costs in availability.

**(k) What transfers.** Almost nothing technical — DeepInfra publishes no
engineering. What transfers is **the price signal**: DeepInfra's dedicated
hourly rates are a usable floor estimate for what committed GPU capacity
actually costs, and its per-token floor is the number your self-hosted $/1M has
to beat if "just buy tokens" is on the table. Its multi-precision endpoint
strategy is also a reminder that **precision is a product axis**, not only an
engineering one.

---

### 3B.4 Novita AI

A mid-market per-token provider that, unusually for its price tier, does publish
kernel work — and publishes it against the exact model families this repo cares
about.

**(a) What they sell.** *"200+ models, on-demand GPUs, and secure agent
runtimes — unified under one API"*, plus batch inference at a **50 % discount on
input and output** [src](https://novita.ai/pricing).

**(b) Hardware.** Named in the engineering posts rather than the pricing page:
**H200** and **Blackwell (B200/B300)** for the MoE kernel work, with explicit
SM90 and SM100-family paths
[src](https://novita.ai/blog/novita-chord-w4a16-moe/) (2026-09-15). GPU hourly
pricing is **not published** on the pricing page.

**(c) Orchestration and isolation.** **Not published.**

**(d) Cold-start engineering.** **Not published.**

**(e) Autoscaling.** **Not published.**

**(f) Serving engine and per-request optimizations.** **vLLM- and
SGLang-based**, with upstream-contributed kernels — the opposite of the Together
/ Fireworks proprietary-engine model, and much more directly reusable:

| Work | What it is | Numbers | Date |
|---|---|---|---|
| **Chord** W4A16 INT4 MoE kernels | A CUDA operator package for INT4 (W4A16) MoE inference on Kimi-K2.x, in two families: *indexed* (Humming-derived, prefill+decode, H200/B200/B300) and *grouped* (SM90-specialised, different routing and weight layout). Enabled in vLLM via `--quantization humming` / `moe_backend="humming"` | Per-layer vs public Humming: **H200 prefill 1.11–1.20×, H200 decode 1.16–1.24×, B300 decode 1.81–2.15×**; grouped 1.00–1.35× | 2026-09-15 [src](https://novita.ai/blog/novita-chord-w4a16-moe/) |
| DSpark speculative decoding in vLLM | Lightweight drafters with parallel block-drafting, no target-model change. Swept draft window n=3→7 | Kimi-K2.6 **2.17× → 2.55×** (+17.8 %); Kimi-K2.7-Code **2.12× → 2.36×** (+11.7 %). TP=8, CUDA graphs, `max_model_len=20000`, **batch size 1**. Explicit finding: *"a larger draft window does not automatically improve throughput"* — DSpark held acceptance across larger windows better than Eagle3-MLA | 2026-07-10 [src](https://novita.ai/blog/kimi-k2-dspark-speculative-decoding-throughput/) |
| GLM4-MoE on SGLang | Production tuning | **65 % faster TTFT** | 2026-01-21 [src](https://novita.ai/blog/optimizing-glm4-moe-for-production/) |

**(g) Routing, rate limits, SLAs.** **Not published.**

**(h) Observability.** OpenAI-compatible API.

**(i) Pricing.** Per 1M [src](https://novita.ai/pricing): DeepSeek V4.1 Flash
$0.30/$1.20; DeepSeek V4 Pro $1.6/$3.2; DeepSeek V3.1 $0.27/$1.00; Llama 3.1 8B
$0.02/$0.05; Llama 3.3 70B $0.135/$0.40; Qwen 3.8 Flash $0.15/$0.47; Qwen 3.5
27B $0.30/$2.40. Via OpenRouter, DeepSeek V4.1 Flash at **$0.285/$1.14** (fp8)
and Qwen3.8-27B at $0.42/$3.00. Batch at **50 % off both directions** — a
steeper batch discount than the 50 %-off-total that Anthropic, OpenAI, Groq,
Fireworks and Together all converge on, though arithmetically identical.

**(j) Published numbers.** Kernel speedups above. Uptime via OpenRouter on
2026-09-19: `gpt-oss-120b` 85.0 %, `qwen3.8-27b` 100 %.

**(k) What transfers.** **More than any other provider in this document**,
because Novita's optimizations ship as vLLM/SGLang paths rather than as a closed
engine. The DSpark window sweep is directly actionable for this repo's DeepSeek
and Kimi experiments — it is a published acceptance-vs-window curve on the exact
model family, and it says the draft window is a tunable with a real optimum, not
a "bigger is better" knob. That is the empirical backing
[`serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md#2-speculative-decoding)
asks for and
[`README.md` open question 6](../README.md#6-top-14-open-questions-across-the-tree)
flags as missing. The Chord B300-decode **1.81–2.15×** figure is a concrete
statement that *MoE kernel choice on Blackwell is worth roughly a hardware
generation*, which bears directly on the Kimi-K3 B300 cells in
[`cost-matrix.md` §3](../matrix/cost-matrix.md#3-max-throughput--1m-output-tokens-s4-4k-in--512-out).

Caveat for both: **batch size 1**. Neither the DSpark nor the ATLAS numbers say
anything about speculation at the batch sizes this repo's cost grid operates at
(64–3,327). Treat them as latency results, not throughput results.

---

### 3B.5 Groq

The oldest custom-silicon inference story, and — as of 2026 — the one whose
premise has changed most.

**(a) What they sell.** GroqCloud: per-token APIs on Free / Developer /
Enterprise tiers, a Batch API, and enterprise/sovereign deployments. No GPU-hour
or chip-hour product is publicly priced.

**(b) Hardware and where it runs.** *This is the field that moved.* Three
published facts, in order:

1. **The LPU as designed**: on-chip SRAM with *"memory bandwidth upwards of 80
   terabytes/second"* against GPU off-chip memory at ~8 TB/s — a claimed 10×;
   deterministic execution where *"every execution step is completely
   predictable to the smallest execution period"*, statically scheduled at
   compile time; a compiler designed *before* the chip; chip-to-chip
   `RealScale` interconnect forming *"one shared resource fabric"* with no
   external routers or controllers; **14 nm** current generation with 4 nm
   planned [src](https://groq.com/blog/the-groq-lpu-explained). ⚠️ The LPU-explained
   post carries no visible date and the 14 nm/4 nm statement is clearly several
   years old; treat the *architecture* as sourced and the *process node* as
   stale.
2. **2025-12-24 — NVIDIA licensing.** Groq and NVIDIA entered a *"non-exclusive
   inference technology licensing agreement"*; founder Jonathan Ross, president
   Sunny Madra *"and team members will transition to NVIDIA"*; Simon Edwards
   becomes CEO; GroqCloud *"will continue to operate without interruption"*
   [src](https://groq.com/newsroom/groq-and-nvidia-enter-non-exclusive-inference-technology-licensing-agreement-to-accelerate-ai-inference-at-global-scale).
   The release does not address the LPU roadmap.
3. **2026-08-24 — Groq deploys NVIDIA silicon.** Groq will deploy **NVIDIA
   Groq 3 LPX accelerators paired with Vera Rubin NVL72**, with Dell, claiming
   **3,400 output tokens/second on Gemma 4 31B at 100K context** and *"4× higher
   interactivity for latency-sensitive agentic tasks"*
   [src](https://groq.com/blog/groq-among-the-first-to-bring-nvidia-groq-3-lpx-and-vera-rubin-nvl72-to-market).
   The same post claims *"six million developers"* and *"trillions of tokens
   weekly"*.

*(Inferred, and flagged as the most consequential inference in this document):*
the entity now called Groq is **in transition from a custom-silicon operator to
a specialised NVIDIA-based inference cloud running licensed Groq scheduling
technology**. Nothing published says the LPU fleet is being retired, and the
existing GroqCloud models still serve at LPU-class speeds; but a company whose
thesis was "GPUs are the wrong architecture"
[src](https://groq.com/blog/why-ai-requires-a-new-chip-architecture) announcing
it is among the first to market with an NVIDIA rack-scale system is a
structural change, not a product line extension. **⚠️ TO BE VERIFIED** — the
LPU's share of GroqCloud serving in 2026-09 is not published.

Physical footprint (published, all newsroom): Helsinki (Europe), Sydney
(APAC), a UK facility via Equinix, Saudi Arabia with Aramco Digital, plus a US
DOE partnership. Developer count *"now exceeding 3.5 million"* on the capacity
post [src](https://groq.com/blog/groqcloud-expanding-to-meet-demand), 6M on the
August 2026 post — LPU counts, tokens/day and utilisation are **not published**.

**(c) Orchestration and isolation.** **Not published.** By construction the LPU
model admits far less of it than a GPU cloud: the compiler *"maps and schedules
a program to run across one or multiple LPUs"* ahead of time, so there is no
runtime scheduler to multiplex against in the way a GPU has.

**(d) Cold-start engineering.** **Not published**, and *(inferred)* largely not
applicable in the GPU sense: weights live in SRAM across a statically-allocated
set of chips, so a model is either resident or it is not. The corollary is that
GroqCloud's model catalogue is **small and curated** — five production models
and a handful of previews [src](https://console.groq.com/docs/models) — which is
what you would expect when residency is binary and expensive.

**(e) Autoscaling.** **Not published**; no scale-to-zero, no per-container
concurrency knob is exposed. The user-visible capacity control is rate limits.

**(f) Serving engine and per-request optimizations.** Groq Compiler + RealScale.
For MoE and large models the only published statement is that chips
*"interconnect and create one shared resource fabric for models to run on"* and
that Llama 4 Maverick (400B MoE) was deployed day-of-release
[src](https://groq.com/blog/from-speed-to-scale-how-groq-is-optimized-for-moe-other-large-models)
— sharding strategy, SRAM budgeting and scheduling detail are **not published**.
Quantization is branded **TruePoint Numerics** and referenced on model pages
(*"Groq applies their TruePoint Numerics quantization approach to maintain
accuracy while accelerating performance"*
[src](https://console.groq.com/docs/model/qwen/qwen3.8-27b)) but has **no public
technical page** — the URL returns 404. **⚠️ TO BE VERIFIED.**

**(g) Routing, concurrency, rate limits, SLAs.** Rate limits are the product
surface and are published per model per plan. Developer plan, e.g.:
`gpt-oss-120b` **250K TPM / 1K RPM**; `qwen/qwen3.8-27b` **250K TPM / 1K RPM**;
Compound **200K TPM / 200 RPM**; Whisper metered in **ASH** (audio sample hours)
[src](https://console.groq.com/docs/models). The two Llama production models are
**Enterprise-only, "ContactSales" for both price and limits** — a notable
retreat from the open self-serve catalogue Groq launched with. No numeric uptime
SLA is published.

**(h) Observability / developer surface.** OpenAI-compatible API,
`api.groq.com/openai/v1/models`, a console, agentic tooling (Compound systems
with built-in web search and code execution). Batch API: **50 % discount**
(raised from a standard 25 % during a 2025 promotion), **24 hours to 7 days**
completion windows — a *longer* maximum window than anyone else here — ≤50,000
lines and 200 MB per JSONL, charged only for completed requests, inputs and
results retained 30 days
[src](https://console.groq.com/docs/batch),
[src](https://groq.com/blog/batch-processing-with-groqcloud-for-ai-inference-workloads).

**(i) Pricing and what it implies.** Published per 1M
[src](https://console.groq.com/docs/models):

| Model | Speed (tok/s) | Input | Output | Context |
|---|---:|---:|---:|---:|
| `openai/gpt-oss-120b` | 500 | $0.15 | $0.60 | 131,072 |
| `openai/gpt-oss-20b` | 1000 | $0.075 | $0.30 | 131,072 |
| `qwen/qwen3.8-27b` (preview) | 450 | **$0.80** | **$4.00** | 131,042 |
| `groq/compound` | 450 | — | 200K TPM / 200 RPM | 131,072 |
| `llama-3.1-8b-instant` | 560 | ContactSales | ContactSales | 131,072 |
| `llama-3.3-70b-versatile` | 280 | ContactSales | ContactSales | 131,072 |

*(Inferred)* The shape of this table is the tell. Groq prices `gpt-oss-120b` at
exactly the market rate ($0.15/$0.60 — identical to Together, Nebius, Bedrock
and SiliconFlow, §5.3) while delivering ~500 tok/s, and prices the **dense 27B
at $0.80/$4.00, which is 5.3× the market's cheapest Qwen3.8-27B endpoint and
1.67× the highest GPU-based one**. A provider whose architecture holds weights
in SRAM is *structurally* expensive per unit of resident parameter and cheap per
unit of latency; the price table reads exactly that way. The **7-day batch
window** — three to seven times longer than anyone else's — is the other tell:
*(inferred)* batch on Groq is filling scheduling gaps in a statically-compiled
fleet with far less elasticity than a GPU cluster, so it needs a wider window to
find room.

**(j) Published numbers.** Per-model tok/s in the table (these are Groq's own
published figures, not measured here); 3,400 output tok/s on Gemma 4 31B @100K
on the new NVIDIA platform; ~80 TB/s SRAM bandwidth. Cold start, uptime and
utilisation: **none published**.

**(k) What transfers.** *Architecturally, almost nothing* — the entire thesis is
"put the weights in SRAM", which a GPU owner cannot do. Two things do transfer.
First, **the determinism argument**: compile-time static scheduling means no
runtime contention and therefore tight, predictable tail latency. The
GPU-side version of that is real and cheap — CUDA graphs, fixed batch shapes,
pinned memory pools — and is exactly what Fireworks writes about when it
discusses CUDA graphs eliminating host-side launch overhead. Second, **the
7-day batch window as a capacity-smoothing instrument**: if your cluster's
interactive load is peaky, the correct lever is a long-window batch queue, not
more GPUs.

The thing that emphatically does *not* transfer is the headline tok/s. 450–1000
tok/s on Groq is **per-user single-stream speed** on a fleet whose economics are
set by chip count, not a throughput figure comparable to the aggregate
tok/s/GPU columns in [`cost-matrix.md` §3](../matrix/cost-matrix.md).

---

### 3B.6 Cerebras

The clearest architectural contrast to GPUs in this document, and the only
provider here serving **one of this repo's exact models** (Qwen3.8-27B) at a
published price and speed.

**(a) What they sell.** Cerebras Inference: free trial, pay-as-you-go
per-token public endpoints, and **Dedicated Endpoints** (reserved private
capacity, bring-your-own weights, bespoke draft models and quantization
strategies) [src](https://inference-docs.cerebras.ai/dedicated/overview). Also
resold through AWS Marketplace, OpenRouter, Hugging Face and Vercel
[src](https://www.cerebras.ai/pricing).

**(b) Hardware.** Owned CS-series systems in own/partner data centres.
Generations, with dates:

| Gen | Published specs | Source |
|---|---|---|
| **WSE-3 / CS-3** | 4 trillion transistors, **900,000 cores**, **44 GB on-chip SRAM**, **21 PB/s** aggregate memory bandwidth — *"7,000× that of an H100"*, and ~*"1,000–2,000× higher effective memory bandwidth than an NVIDIA B200"* | [src](https://www.cerebras.ai/blog/introducing-cerebras-inference-ai-at-instant-speed) (2024-08-27), [src](https://www.cerebras.ai/blog/disaggregated-inference) (2026-03-13) |
| **WSE-3T / CS-4** | **53.5 PB/s** aggregate on-wafer fabric bandwidth; AC/DC converters placed *"about 0.5 millimetres from the wafer"* vs ~50 mm conventionally, ~2× power-delivery efficiency | Hot Chips 2026, [src](https://www.cerebras.ai/blog/ultrafast-frontier-inference-cerebras-deep-dive-at-hot-chips-2026) (2026-08-25) |
| **CS-5 (targeted)** | *"up to 10,000 output tokens per second per user"* on leading open-source models; *"up to 5,000"* for frontier models | same |
| **CS-6 (future)** | wafer-scale SRAM + compute with **3D-stacked DRAM** to add capacity without losing locality | same |

**(c) Orchestration and isolation.** Public endpoints are shared; **Dedicated
Endpoints** are *"a private, provisioned instance … reserved exclusively for
your organization"* so that *"latency and throughput are not affected by other
users"* [src](https://inference-docs.cerebras.ai/dedicated/overview). The
internal scheduler is **not published**.

**(d) Cold-start engineering.** **Not published**; *(inferred)* structurally
similar to Groq — weights are resident in wafer SRAM, so the unit of
provisioning is a system, not a container, and the public catalogue is
correspondingly tiny: **two models** on public endpoints (`gpt-oss-120b`,
`qwen-3.8-27b`) with a much larger family list available only on dedicated
endpoints [src](https://inference-docs.cerebras.ai/models/overview).

**(e) Autoscaling.** **Not published.** The user-visible capacity mechanism is a
**dual-bucket rate limit**: every organisation has an *uncached token limit* and
a *total token limit*, so improving cache hit rate *"lets the same uncached
limit serve significantly more total tokens"*. Token-bucket replenishment
(continuous, not interval-reset); requests are pre-screened against
`max_completion_tokens` and rejected before processing if the estimate would
exceed quota [src](https://inference-docs.cerebras.ai/support/rate-limits).

*That dual-bucket design is the most quietly informative thing Cerebras
publishes.* Rate-limiting on **uncached** tokens separately from total tokens is
an explicit admission that the scarce resource is **prefill compute**, not
served tokens — which is precisely the finding
[`README.md` open question 5](../README.md#6-top-14-open-questions-across-the-tree)
records for this repo's own sparse-attention models.

**(f) Serving engine and per-request optimizations.**

- **Model partitioning:** models are split *"at layer boundaries across multiple
  CS-3 systems,"* with weights staying in local SRAM and only activations moving
  between stages [src](https://www.cerebras.ai/blog/how-cerebras-serves-gpt-5-6-sol-at-up-to-750-tokens-per-second)
  (2026-08-27). Earlier: *"20B models fit on a single CS-3 while 70B models fit
  on as few as four systems"* [src](https://www.cerebras.ai/blog/introducing-cerebras-inference-ai-at-instant-speed).
  On multi-wafer: *"execution is pipelined to keep high-volume tensor and expert
  communication within each wafer. Only lower-volume data, primarily
  activations, moves between wafers"*
  [src](https://www.cerebras.ai/blog/ultrafast-frontier-inference-cerebras-deep-dive-at-hot-chips-2026).
  **This is pipeline parallelism with tensor/expert parallelism confined inside
  a wafer** — the inverse of the GPU playbook, where TP crosses NVLink and PP is
  avoided.
- **MoE:** avoids expert parallelism entirely where it can — *"store much
  bigger models on the chip directly (roughly up to 1B in total parameter
  count)"*, weight streaming above that. The named innovation is **Batch Tiling
  on Attention (BTA)**: split attention's input batch into G tiles of size B,
  process independently, concatenate. Without BTA, throughput degraded **53 %**
  with more experts and **86 %** at lower sparsity on a Qwen3 3B-active/128-expert
  configuration; with BTA it stayed *"close to the dense model, across all
  expert counts"*. The WSE has *"about 900 times more on-chip memory (SRAM) than
  a latest single GPU"* [src](https://www.cerebras.ai/blog/moe-guide-scale) (2025-09).
- **Disaggregation:** announced **2026-03-13** with AWS — **Trainium for prefill,
  CS-3 for decode**, over EFA. Claims: **5× token throughput**, token generation
  **1,200 tok/s** (vs a 50 tok/s baseline), *"up to 4.5× improvement in P95
  latency"* on agentic workloads [src](https://www.cerebras.ai/blog/disaggregated-inference).
  Cerebras buying someone else's compute-optimised silicon for prefill is the
  strongest possible statement that **wafer-scale SRAM is a decode advantage,
  not a prefill one**.
- **Speculative decoding:** used — the 2024-10-24 "3× faster" release attributes
  Llama 3.1-70B at **2,100 tok/s** to rewritten MatMul/reduce/broadcast/elementwise
  kernels, asynchronous wafer I/O, and speculative decoding, calling it
  *"more than a hardware generation's worth of performance in a single software
  release"* [src](https://www.cerebras.ai/blog/cerebras-inference-3x-faster).
- **Quantization:** explicitly *storage-only*. *"Cerebras uses selective
  weight-only quantization only during storage… weights are stored in partial
  16-bit / 8-bit / 4-bit… For quality, sensitive layers are stored at full
  precision with dequantization on the fly… **The activations, attention, and kv
  cache remain in full precision and unquantized.**"* And: *"All of our public
  models are unpruned"*, with REAP-pruned models shared to Hugging Face for
  research but **not served**
  [src](https://inference-docs.cerebras.ai/models/overview).
- **Prompt caching:** automatic, no breakpoints or headers, **128-token blocks**,
  exact-prefix matching, **TTL guaranteed 5 minutes** and up to 1 hour under
  favourable load [src](https://inference-docs.cerebras.ai/capabilities/prompt-caching).
- **Multi-LoRA:** announced [src](https://www.cerebras.ai/blog/introducing-multi-lora-on-cerebras-inference);
  the hardware mechanism, adapter-switch cost and SRAM budget are **not published**.
  **⚠️ TO BE VERIFIED.**

**(g) Routing, concurrency, rate limits, SLAs.** Rate limits per model per tier,
e.g. Qwen 3.8 27B: Free Trial **5 RPM / 30K input TPM / 90K total TPM**;
Developer **300 RPM / 150K input TPM / 450K total TPM**
[src](https://inference-docs.cerebras.ai/models/qwen-3.8-27b). Production SLAs
are named as a dedicated-endpoint feature but **no numeric SLA is published**.

**(h) Observability / developer surface.** OpenAI-compatible SDK, reasoning
controls, structured outputs, tool calling with `strict: true`, `llms.txt` docs
index.

**(i) Pricing and what it implies.** Published per 1M
[src](https://inference-docs.cerebras.ai/models/qwen-3.8-27b),
[src](https://inference-docs.cerebras.ai/models/openai-oss):

| Model | Speed | Input | Output | Context (free/paid) |
|---|---:|---:|---:|---|
| `gpt-oss-120b` | **~3,000 tok/s** | $0.35 | $0.75 | 65k / 131k |
| `qwen-3.8-27b` | **~1,850 tok/s** | **$0.99** | **$1.49** | 64k / 128k |

*(Inferred, and this is the most interesting price in the document)*: Cerebras
is the **only provider in the market whose output price is within 1.5× of its
input price**. Everyone on GPUs charges 4–12× more for output than input,
because on a GPU output is memory-bandwidth-bound and input is compute-bound and
batchable. Cerebras charges **1.5×**. That ratio *is* the architecture showing
through the P&L: with 21 PB/s of SRAM bandwidth, decode stops being the
expensive phase, and the pricing follows. Correspondingly its *input* price
($0.99/1M on a 27B dense) is **4–10× the GPU market's**, because prefill FLOPs
on a wafer are not cheap. See §4.3.

**(j) Published numbers.** Llama3.1-8B 1,800 tok/s and 70B 450 tok/s at launch
(2024-08-27); Llama 3.1-70B 2,100 tok/s (2024-10-24); gpt-oss-120b *"a
blistering 3,000 tokens/sec"*; GPT-5.6 Sol **up to 750 tok/s** across multiple
CS-3s with *"the same model weights and precision as the standard endpoint — no
distillation or quantization"* (2026-08-27); disaggregated 1,200 tok/s and 4.5×
P95 (2026-03-13). Cold start, uptime, utilisation: **not published**.

**(k) What transfers.** The **dual-bucket rate limit** (uncached tokens vs
total tokens) is a design any self-hoster should steal outright: it is the only
admission-control scheme in this document that prices the thing that actually
saturates — prefill — instead of the thing that is easy to count. **Automatic
128-token-block prefix caching with a guaranteed TTL and no client
breakpoints** is a better developer contract than the explicit-breakpoint model,
and vLLM/SGLang already implement the mechanism. The **BTA** idea (tile the
attention batch to keep utilisation up as sparsity rises) has a GPU analogue
worth testing on this repo's MoE models. And the **storage-only quantization**
stance is a useful, explicitly-stated counterposition to
[`quantization-formats.md`](../cross-cutting/quantization-formats.md)'s
compute-precision story: dequantize-on-the-fly, keep activations/attention/KV in
full precision, and you buy capacity without buying accuracy risk — at the cost
of the FLOPs speedup, which on a wafer you do not need.

*Does not transfer.* Everything downstream of 44 GB of SRAM at 21 PB/s. The
layer-boundary pipeline split across systems, in particular, is the *opposite*
of the right answer on GPUs, where pipeline bubbles at inference batch sizes are
punishing and NVLink makes TP cheap.

---

### 3B.7 SambaNova

The least-documented of the three custom-silicon operators, but the one with a
peer-reviewed architecture paper.

**(a) What they sell.** SambaCloud per-token APIs with cached-input pricing on
some models; enterprise on-prem SN40L systems (not publicly priced).

**(b) Hardware.** **SN40L Reconfigurable Dataflow Unit (RDU)** — a streaming
dataflow architecture with a **three-tier memory system**: on-chip distributed
SRAM, on-package HBM, off-package DDR DRAM. Published in *Proceedings of MICRO
2024* (submitted 2024-05-13, revised 2024-11-05)
[src](https://arxiv.org/abs/2405.07518). The paper's framing is explicitly the
memory wall: modern accelerators have *"disproportionately high
compute-to-memory ratios"*.

**(c) Orchestration and isolation.** **Not published.**

**(d) Cold-start engineering.** This is SambaNova's distinctive claim and it is
a cold-start claim in disguise: an 8-socket RDU node achieves **15×–31× speedups
in model-switching time** versus conventional approaches
[src](https://arxiv.org/abs/2405.07518). The three-tier memory is what buys it —
DDR holds many models' weights, HBM stages them, SRAM runs them, so switching
between experts/models is a memory promotion rather than a reload from storage.
For a Composition-of-Experts deployment the paper claims **up to 19× reduction
in machine footprint**.

**(e) Autoscaling.** **Not published.**

**(f) Serving engine.** Proprietary dataflow compiler and runtime; **not
publicly documented** beyond the paper. Per-request optimizations (speculative
decoding, prefix caching, disaggregation) are **not published**, except that
prompt caching exists for exactly one model (below).

**(g) Routing, rate limits, SLAs.** **Not published.**

**(h) Observability.** OpenAI-compatible API.

**(i) Pricing and what it implies.** Per 1M
[src](https://cloud.sambanova.ai/plans/pricing): MiniMax-M2.7 **$0.06 cached /
$0.60 in / $2.40 out**; DeepSeek-V3.1 and V3.2 **$3.00 / $4.50, no cached
tier**; Gemma-4-31B-it $0.38/$1.15; gpt-oss-120b $0.22/$0.59;
Meta-Llama-3.3-70B $0.60/$1.20; MiniMax-M3 $0.60/$2.40. Via OpenRouter,
SambaNova's `gpt-oss-120b` endpoint is $0.14/$0.95.

*(Inferred)* **Only MiniMax-M2.7 supports cached input**; every other model
reads "N/A". Prompt caching is normally a property of the *engine*, not the
model — so a provider that offers it on exactly one model is most likely running
that model on a newer/different serving path. And the DeepSeek-V3.1/V3.2 price
($3.00/$4.50) is **10–20× the GPU market's price for the same model**, which
says SambaNova is not competing on DeepSeek-class cost at all.

**(j) Published numbers.** From the paper: **2–13×** speedups across benchmarks
on eight RDU sockets, **3.7×** over DGX H100, **6.6×** over DGX A100, 15–31×
model switching, up to 19× footprint reduction. SambaCloud publishes **no
tokens/second figures at all** — the docs give context windows only
[src](https://docs.sambanova.ai/cloud/docs/get-started/supported-models). For a
company whose peers lead with speed, that absence is itself a signal.
**⚠️ TO BE VERIFIED.**

**(k) What transfers.** The **three-tier memory hierarchy for fast model
switching** is the transferable idea, and it maps cleanly onto GPU hardware:
DDR/host-DRAM as a weight staging tier, HBM as the resident tier, with promotion
rather than reload. That is exactly the architecture behind Together's
*"fleet-wide model weight caching"* and Fireworks' *"LoRA adapters with caching…
new models can be added or taken down within seconds"* — same idea, different
substrate. If you host many models or many adapters on a fixed cluster, **keep a
host-DRAM weight tier and promote, never re-download**. Nothing else transfers;
the RDU's reconfigurable dataflow has no GPU analogue.

---

### 3B.8 Perplexity

**Scope warning.** Perplexity's engineering blog (`perplexity.ai/hub/blog/*` and
`research.perplexity.ai`, which redirects there) returned **HTTP 403 behind a
Cloudflare interstitial to every request made on 2026-09-19**. The posts
referenced across the industry — on TRT-LLM adoption, H100 serving economics,
and multi-node DeepSeek deployment — could not be read. **This profile is built
from Perplexity's public GitHub only**, and every field below is narrower than
the truth.

**(a) What they sell.** A consumer/enterprise answer engine, plus the
Perplexity API (Sonar family). *(Inferred from the repos)* Perplexity is
primarily an *operator for its own product*, with the API as a secondary
surface — which is why its published work is systems infrastructure rather than
product docs.

**(b) Hardware.** From the repos: CUDA arch targets `9.0a+PTX` (H100-class and
newer) in `pplx-kernels`; **AWS EFA** named as a transport in `fabric-lib`
[src](https://github.com/perplexityai/pplx-garden). *(Inferred)* a substantial
H100/H200-class fleet on AWS and/or similar, given EFA support is a first-class
transport rather than an afterthought.

**(c) Orchestration and isolation.** **Not published.**

**(d) Cold-start engineering.** Not published for serving. Adjacent: `fabric-lib`
claims **"Weight Transfer for RL Post-Training in under 2 seconds"**
[src](https://github.com/perplexityai/pplx-garden) — an RL-rollout number, not
an inference cold start, but it bounds what RDMA weight movement can do.

**(e) Autoscaling.** **Not published.**

**(f) Serving engine and per-request optimizations.** The open repos are the
evidence:

- **`pplx-kernels`** — MoE all-to-all dispatch/combine kernels over **NVSHMEM**,
  with *"Flexible transportation layers: NVLink, IBGDA, IBRC, EFA"*, CUDA Graph
  support, and *"overlapping communication and computation"*. Benchmarks span
  **EP8–EP128**. Now carries a deprecation notice pointing at `pplx-garden`
  [src](https://github.com/perplexityai/pplx-kernels).
- **`pplx-garden`** — successor umbrella. `fabric-lib`: point-to-point RDMA
  communication for LLM systems, targeting *"trillion-parameter models on AWS
  EFA"*, with an **MLSys'26** paper reference. Also `lily` (Rust + Metal
  inference server for Qwen3.6-35B-A3B on Apple Silicon) and `pplx-unigram`
  (unigram tokenizer CPU performance). The stated operational priorities
  include **disaggregated prefill/decode**
  [src](https://github.com/perplexityai/pplx-garden).

*(Inferred)* An organisation that writes its own EP8–EP128 all-to-all kernels
over four transports, and then a general RDMA fabric library, is running **large
MoE models across many nodes with expert parallelism as the primary
parallelism** — the same shape as DeepSeek's published EP144 decode (§3B.10).
That is an inference from the repos' contents, not a statement Perplexity has
made here.

**(g)–(i) Routing, observability, pricing.** **Not retrievable** on 2026-09-19.

**(j) Published numbers.** EP8–EP128 dispatch/combine benchmarks in-repo;
<2 s weight transfer. Serving throughput/latency/uptime: **not retrievable**.

**(k) What transfers.** `pplx-kernels` and `fabric-lib` are **permissively
published and directly usable** — they are the only production MoE all-to-all
kernels in this document that a self-hoster can actually run. For a cluster
serving this repo's DeepSeek-V4.1-Flash or Kimi-K3 at EP8 or above, the
transport abstraction alone (NVLink / IBGDA / IBRC / EFA behind one interface)
is worth reading before writing anything bespoke. See
[`serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md#3-parallelism-for-large-moe).

---

### 3B.9 OpenRouter

Owns no compute. Included because it is the market's price and quality
transparency layer, and because its routing semantics are a specification a
self-hoster can implement internally across their own pools.

**(a) What they sell.** A single OpenAI-compatible API over ~447 models and
several hundred provider endpoints, with routing policy exposed as request
parameters.

**(b) Hardware.** None.

**(c) Orchestration.** Not applicable.

**(d) Cold start.** Not applicable, and *(inferred)* this is part of the
product: routing to an already-warm provider is a cold-start avoidance
mechanism for the caller.

**(e) Autoscaling.** Not applicable.

**(f) "Serving engine" — the routing policy.** The default is the interesting
part: providers that *"have not seen significant outages in the last 30
seconds"* are load-balanced **weighted by inverse price squared** — a provider
at $1/1M gets **9× the traffic** of one at $3/1M, at equal uptime
[src](https://openrouter.ai/docs/features/provider-routing). Overrides:

| Parameter | Effect |
|---|---|
| `sort` = `price` / `throughput` / `latency` | Hard sort, no load balancing |
| `sort.by` + `sort.partition` (`model` \| `none`) | With model fallbacks, sort within each model or globally across all of them |
| `:nitro` / `:floor` suffixes | Shorthand: `:nitro` = throughput sort + priority service tiers; `:floor` = price sort + flex tiers |
| `preferred_min_throughput`, `preferred_max_latency` | Thresholds over a **rolling 5-minute window**, as a number or a `{p50,p75,p90,p99}` object. Preferred, not exclusive — they remain fallbacks |
| `order`, `only`, `ignore`, `allow_fallbacks` | Explicit provider control; base slug matches all regions, full slug pins one |
| `quantizations` | Filter to `int4, int8, fp4, fp6, fp8, fp16, bf16, fp32, unknown` |
| `max_price` | `{prompt, completion, request, image}` ceilings — over-price requests are **rejected, not upgraded** |
| `data_collection: "deny"`, `zdr: true`, `require_parameters`, `enforce_distillable_text` | Policy and capability filters |

**(g) Routing, rate limits, SLAs.** Uptime is tracked per endpoint and exposed
through the Endpoints API and dashboards (hourly over 3 days, 24-hour trend, and
"with routing" vs "without routing" availability)
[src](https://openrouter.ai/docs/features/uptime-optimization). No numeric SLA.
Fireworks' rebuttal — that proxy hops are additive to median TTFT while
improving p95 — is the fair statement of the trade
[src](https://fireworks.ai/blog/inference-providers-vs-api-routers).

**(h) Observability / developer surface.** The `/api/v1/models/{author}/{slug}/endpoints`
API returns, per endpoint: `provider_name`, `tag`, `pricing` (prompt,
completion, `input_cache_read`), `context_length`, **`quantization`**,
`max_completion_tokens`, `supported_parameters`, `status`, and
`uptime_last_5m / _30m / _1d`. That is the source for every third-party price
in §5.

**(i) Pricing and what it implies.** *"We pass through the pricing of the
underlying providers without any markup on inference pricing."* Revenue comes
from **5.5 % ($0.80 min) on Stripe credit purchases, 5 % on crypto**, and BYOK
at **5 % of usage above $25,000/month list price** (pay-as-you-go) or **above
$200,000/month** (enterprise). Prompts and completions are **not logged by
default**, *"even if an error occurs, unless you opt-in"*
[src](https://openrouter.ai/docs/faq).

**(j) Published numbers.** Per-endpoint uptime, live. On 2026-09-19 the
DeepSeek-V4.1-Flash endpoints ranged 99.5–100 % over 30 minutes with two
endpoints in a negative-status state; the Kimi-K3 endpoints ranged **82.11 %
(DeepInfra) to 100 %**. Latency and throughput fields were **null across every
endpoint queried** on that date — the API carries `latency_last_30m` and
`throughput_last_30m` fields but they were unpopulated. **⚠️ TO BE VERIFIED.**

**(k) What transfers.** Two things, both concrete. First, **inverse-price-squared
weighted load balancing gated on a 30-second outage window** is a good default
for any multi-pool router, substituting your own pools' marginal cost for price.
Second, and more useful: **the `quantizations` filter is a contract you should
impose internally**. A fleet that serves the same model at FP8 on some nodes and
NVFP4 on others, without the caller being able to say which, is silently varying
quality — exactly the failure OpenRouter's field exists to surface.

---

### 3B.10 DeepSeek — first-party operator

The most detailed public disclosure of a production LLM serving system by any
operator, anywhere.

**(a) What they sell.** First-party per-token API with peak/off-peak pricing.

**(b) Hardware.** **H800 nodes, 8 GPUs each.** In the 24-hour window 2025-02-27
to 2025-02-28: **peak 278 nodes, average 226.75 nodes**
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).

**(c) Orchestration.** Not published as such; the serving topology is:

**(f) Serving engine and per-request optimizations** — taken first, because it
is the substance:

| Element | Published detail |
|---|---|
| **Prefill** | **EP32 across 4 nodes**; each GPU hosts **9 routed experts + 1 shared expert**; **DP32** for MLA/shared-expert components |
| **Decode** | **EP144 across 18 nodes**; each GPU hosts **2 routed experts + 1 shared expert**; **DP144** |
| **Overlap** | Prefill uses a **dual-batch overlap**: two microbatches alternate so communication hides behind computation. Decode uses a **5-stage pipeline** subdividing attention layers for seamless comm/compute overlap |
| **Load balancing** | **Three distinct balancers.** (1) *Prefill*: balances core-attention compute and dispatch-send load across varying request counts. (2) *Decode*: equalises **KVCache usage** and request count per GPU. (3) *Expert-parallel*: minimises maximum dispatch-receive load |
| **Throughput** | **~73.7k tokens/s/node prefill**, **~14.8k tokens/s/node decode**; average output speed **20–22 tok/s** per user |
| **Volume** | 608B input tokens (**342B cached — 56 %**), 168B output tokens, in 24 h |

**(d) Cold start / (e) autoscaling.** Not published directly, but the node count
moving between 226.75 average and 278 peak in one day is **elastic
provisioning at node granularity**, and DeepSeek separately runs nighttime
discounting — *(inferred)* an explicit demand-shaping strategy to flatten that
curve rather than scale to it.

**(g)–(h) Routing, rate limits, observability.** Not published in this
document.

**(i) Pricing and what it implies.** This is the part every self-hoster should
read. Daily infrastructure cost **$87,072** at an assumed **$2/GPU-hour**;
theoretical daily revenue at R1 pricing **$562,027**; **theoretical profit
margin 545 %** — with the honest caveat that actual revenue is *"substantially
lower"* because V3 is cheaper, some access is free, and nights are discounted
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).

Current first-party prices, as recorded in
[`cost-matrix.md` §6.1](../matrix/cost-matrix.md#61-published-vendor-prices):
DeepSeek-V4.1-Flash off-peak **$0.15 / $0.003 cached / $0.60**, peak
$0.30/$0.006/$1.20. Cached input is **2 % of uncached**, not the generic 10 %.

**(j) Published numbers.** All of the above.

**(k) What transfers.** More than anything else in this document, and it is the
reference point for [`serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md#3-parallelism-for-large-moe):

- **Prefill and decode want completely different EP degrees** — EP32 vs EP144, a
  4.5× difference, on the same model. Any single-topology deployment is wrong
  for one of the two phases. This is the strongest published argument for PD
  disaggregation, and it is quantified.
- **Three load balancers, not one.** Decode balances *KV-cache occupancy*;
  prefill balances *attention compute and dispatch-send*; EP balances
  *dispatch-receive*. A single "least connections" balancer gets all three
  wrong.
- **56 % cache hit rate in production** (342B of 608B input tokens) is a real
  operational number for an agentic/chat mix, and it validates
  [`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost)'s blended assumption of half
  the input cached.
- **The margin corridor.** A 545 % theoretical margin at $2/GPU-hour means the
  *published API price is not near marginal cost*. When
  [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)
  says self-hosting DeepSeek-V4.1-Flash needs **92 % utilisation on B200** to
  match the API price, that is being measured against a price with an enormous
  margin baked in — the *physics* gap is far smaller than the *price* gap. What
  the API is really selling you is **someone else's utilisation risk**.

---

### 3B.11 Moonshot AI (Kimi) — first-party operator

**(a) What they sell.** First-party Kimi API; Kimi-K3 is also resold widely
(twenty OpenRouter endpoints on 2026-09-19).

**(b) Hardware.** Not published in the paper beyond "GPU cluster". **⚠️ TO BE
VERIFIED.**

**(c)–(f) Architecture — Mooncake.** Moonshot's serving platform is published as
a paper (arXiv 2407.00079, submitted 2024-06-24, v4 2025-09-03) and is
production infrastructure, not research
[src](https://arxiv.org/abs/2407.00079):

- **Disaggregated prefill and decoding clusters.**
- **KVCache-centric design**: the distinguishing move is using *"the
  underutilized CPU, DRAM, and SSD resources of the GPU cluster to implement a
  disaggregated cache of KVCache"* — the KV cache becomes a first-class,
  cluster-wide, tiered store rather than per-GPU state.
- **KVCache-centric scheduler** balancing throughput against latency SLOs.
- **Prediction-based early rejection** for overload — the scheduler predicts
  and rejects early rather than accepting work it cannot finish within SLO.
- Measured: **up to 525 % throughput increase** in simulation under SLO, and in
  production *"Kimi handles 75 % more requests"*. Strongest on long context.

**(g)–(h) Routing, rate limits, observability.** Not published here.

**(i) Pricing.** Moonshot first-party Kimi-K3 **$3.00 / $0.30 cached / $15.00**
per 1M ([`cost-matrix.md` §6.1](../matrix/cost-matrix.md#61-published-vendor-prices),
and confirmed on OpenRouter's Moonshot AI endpoint, which also declares
**mxfp4** quantization
[src](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints)). A
`:batch` variant is listed at the same $3.00/$15.00.

*(Inferred)* Moonshot's own endpoint declaring **MXFP4** is a useful data point
for [`gpu-optimizations.md`](../matrix/gpu-optimizations.md)'s Kimi-K3 format
question: the model's author serves it at MXFP4, while third parties declare
fp4 (Relace, InferenceNet, Sail Research, Parasail), mxfp4 (Chutes, Modal),
fp8 (BaseTen) and **bf16** (DeepInfra) for the same model ID. That is a ~4×
spread in weight bytes behind one API name.

**(k) What transfers.** **Mooncake is the blueprint for tiered KV caching on a
cluster you already own**, and unlike everything in §3B.1–§3B.2 it is published in
full with an open implementation lineage. Two specifics: (1) *your idle CPU
DRAM and NVMe are a KV cache tier* — on a node with 2 TB of host RAM serving a
model with an 890 B–3.2 KB/token KV footprint (see
[`fit-matrix.md` §6.3](../matrix/fit-matrix.md)), that is an enormous amount of
recoverable prefill; (2) **predictive early rejection beats queueing** under
overload, because a request admitted and missed costs the same compute as one
served. Both are directly implementable.

---

### 3B.12 Mistral — first-party operator

**(a) What they sell.** La Plateforme per-token APIs; self-deployment of
open-weight models; cloud-partner distribution.

**(b)–(f).** Mistral publishes **no inference-systems engineering** of the kind
Together, Fireworks, DeepSeek or Moonshot do. What it publishes instead is
**deployment guidance pointing at other people's engines**: the self-deployment
docs cover **vLLM** (with `tokenizer_mode="mistral"`, `load_format="mistral"`,
`config_format="mistral"`, vLLM ≥0.6.1.post1), **TensorRT**, SkyPilot,
Cerebrium, Cloudflare Workers AI and TGI
[src](https://docs.mistral.ai/deployment/self-deployment/vllm/). The docs
navigation also lists a **Priority Tier** and **Regional inference** under
Deployment → Cloud; the priority-tier page URL tried returned 404 and its terms
could not be read. **⚠️ TO BE VERIFIED.**

*(Inferred)* Mistral's strategic position is as a **model vendor whose serving
is deliberately commoditised** — it documents how to run its models on vLLM
rather than claiming a proprietary engine advantage. For a self-hoster that is
the friendliest possible posture, and it is the reason Mistral models have no
"which quantization is this endpoint actually running" problem: the reference
path is the open one.

**(g)–(j).** Not published / not retrievable.

**(k) What transfers.** The `tokenizer_mode`/`load_format`/`config_format`
trio is a real operational detail — Mistral checkpoints served through the
generic HF path tokenize differently, which is a silent quality bug. Beyond
that, Mistral is a reminder that **not every model vendor's serving advantage is
real**; some models are best run on the open engines this repo already
evaluates in [`inference-engines.md`](../cross-cutting/inference-engines.md).

---

### 3B.13 Anthropic, OpenAI, Google — what the frontier labs publish about serving

None of these three publishes its serving architecture. What they publish is
**the commercial interface to their scheduler**, which is informative in a
different way: batch discounts, priority tiers and provisioned throughput are
each a price put on a scheduling decision, and reading them tells you what they
believe their scarce resource is.

#### Anthropic

**Message Batches API** — *"most batches finishing in less than 1 hour while
reducing costs by 50 % and increasing throughput"*; asynchronous, poll for
status, results retrieved when all requests end
[src](https://platform.claude.com/docs/en/docs/build-with-claude/batch-processing).

**Service tiers** — three: Priority, Standard, Batch
[src](https://platform.claude.com/docs/en/api/service-tiers). The mechanics are
unusually explicit:

- A Priority Tier commitment is **a number of input tokens per minute, a number
  of output tokens per minute, a duration (1, 3, 6 or 12 months), and a specific
  model version**. It *"targets 99.5 % uptime with prioritized computational
  resources"*. Over-commitment traffic **falls back to standard automatically**.
- **Burndown rates**, which are the interesting part — capacity is consumed at:
  cache reads **0.1 tokens/token**, cache writes **1.25** (5-minute TTL) or
  **2.00** (1-hour TTL), US-only inference **1.1** on Claude 4.6+, everything
  else 1.0. *"These burndown rates reflect the relative pricing of each token
  type."*
- `service_tier` is a request parameter (`auto` | `standard_only`); the response
  `usage` object reports which tier served it; headers expose
  `anthropic-priority-{input,output}-tokens-{limit,remaining,reset}`.
- **Priority Tier capacity commitments are no longer available for purchase**
  as of this doc's state on 2026-09-19; existing commitments run to contract
  end. Priority is supported on all models *except* Claude Fable 5.1, Mythos
  5.1, Mythos 5, Mythos Preview, Opus 5 and Sonnet 5.

*(Inferred)* A cache **write** costing 1.25–2.0× a normal input token, while a
cache **read** costs 0.1×, is a published statement that **writing a KV cache
entry is more expensive than computing the tokens normally** — the write is the
prefill *plus* the persistence. Any self-hoster modelling prefix caching should
carry a write cost, not just a read discount; this repo's
[`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost) 10 %-of-uncached read rule has no
write-side term. The 1-hour TTL costing 1.6× the 5-minute TTL prices **cache
residency**, which is a real bandwidth/capacity cost on any tiered KV store.

#### OpenAI

**Batch API** — **50 % discount**, single completion window `24h`, *"and often
more quickly"*; **50,000 requests/batch**, **200 MB file**, **2,000 batches per
hour**, model-specific queued-token maxima, and — the operationally important
bit — **a separate rate-limit pool** that does not draw down synchronous quotas
[src](https://developers.openai.com/api/docs/guides/batch).

**Flex processing** — *"priced at Batch API rates, with additional discounts
from prompt caching"*, in exchange for *"slower response times and occasional
resource unavailability"*; default 10-minute request timeout should be raised;
can return **429 Resource Unavailable** with no charge. In beta with limited
model availability; exact multipliers and the model list are **not in the docs**
[src](https://developers.openai.com/api/docs/guides/flex-processing).
**⚠️ TO BE VERIFIED.**

*(Inferred)* Flex is batch-priced but synchronous, which means it is sold as
**preemptible interactive capacity** — the 429-with-no-charge is the preemption.
That is the API-level expression of a spot market in scheduler slots.

#### Google

**Vertex AI Provisioned Throughput** exists and is documented as a product
family (overview, supported models, *Calculate Provisioned Throughput
requirements*, purchase, use). The unit of sale is the **GSU (Generative AI
Scale Unit)**, and throughput is quantified per model in tokens/second per GSU
with a burndown that differs by modality, with spillover to pay-as-you-go. **The
numeric tables — GSU throughput per model, minimum GSU per model, commitment
durations, burndown rates — could not be retrieved on 2026-09-19**; both the
overview and purchase pages fetched as navigation shells.
**⚠️ TO BE VERIFIED**
[src](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/overview).

#### What transfers from all three

**The 50 % batch discount is unanimous.** Anthropic, OpenAI, Groq, Fireworks and
Together all land on exactly 50 %, with Novita's "50 % off both directions"
arithmetically identical. Five independent operators converging on the same
number is a strong signal about **what deferrable work is worth**: roughly half.
For a self-hoster the corollary is direct — if you can shift half your token
volume into a long-window queue, you need roughly half the peak capacity, and
that is worth about the same 50 %. Groq's willingness to go to a **7-day** window
suggests the discount could go further if the deferral does.

**Priority tiers are the inverse trade**, and Anthropic's is the more honest
construction: a *reserved* ITPM/OTPM commitment with a duration and a 99.5 %
target, priced per token-type by burndown. Fireworks' flat 1.25× (§3B.2) is the
*scheduler-weight* version of the same product. Knowing which one you are buying
matters: only the first survives a genuine capacity crunch.

---

## 4. Custom silicon — what it changes about the scaling problem

This repo's entire cost model rests on one inequality, from
[`METHODOLOGY.md` §4](../METHODOLOGY.md#4-throughput-and-latency-roofline):

```
decode_step_time ≈ max( bytes_per_step / (HBM_BW × MBU),
                        2 × active_params × batch / (peak_FLOPS × MFU) )
```

On every GPU in [`gpus/`](../gpus/), the left term wins at the batch sizes that
matter, and `bytes_per_step` is dominated by weights plus KV. Groq and Cerebras
attack the denominator of the left term by four orders of magnitude. That
changes three things, and leaves one thing unchanged.

### 4.1 The KV-cache HBM ceiling stops being the binding constraint — and becomes a different one

[`fit-matrix.md` §2](../matrix/fit-matrix.md) computes max concurrency for every
pair as `kv_budget / kv_bytes_per_token / context`, and the answers are brutal:
Kimi-K3 on H200 at 128K context falls to **12 concurrent requests** after the
2026-09-19 replicated-KV resolution; DeepSeek-V4.1-Flash on H100 to **9**. The
whole interactive column of [`cost-matrix.md` §2](../matrix/cost-matrix.md#2-interactive--1m-output-tokens-at-tpot--50-ms-s1-4k-in--512-out)
is shaped by it.

On a wafer, **44 GB of SRAM at 21 PB/s** replaces **141–288 GB of HBM at
4.8–8 TB/s**. The KV cache is no longer competing with weights for a
bandwidth-starved pool; it is competing for a *capacity*-starved one that is
three orders of magnitude faster. So:

- The **per-token KV footprint stops setting the concurrency ceiling** in the
  way it does on GPUs, because the ceiling is now total resident capacity across
  a fleet of wafers, allocated at provisioning time by the compiler — not a
  runtime pool you can trade against batch size.
- But the ceiling **becomes static**. A GPU operator can re-tune
  `--gpu-memory-utilization`, switch KV dtype from BF16 to FP8 to NVFP4 (890 B
  vs 1,650 B vs 3,200 B per token for DeepSeek-V4.1-Flash — see
  [`fit-matrix.md` §6.3](../matrix/fit-matrix.md)) and buy 3.6× the concurrency
  in an afternoon. A wafer operator cannot: Cerebras states outright that
  *"activations, attention, and kv cache remain in full precision and
  unquantized"*. **The KV-quantization lever this repo spends four sections on
  does not exist on the custom-silicon side.**
- The multi-wafer split is **pipeline-parallel at layer boundaries**, with TP and
  expert communication kept *inside* a wafer. That is the reverse of GPU
  practice. It works because inter-wafer traffic is activations only — small —
  whereas GPU pipeline parallelism at inference batch sizes produces bubbles.

### 4.2 Batch-size behaviour inverts

The single most important difference, and the one that decides when these
platforms are a good deal.

On a GPU, decode is memory-bandwidth-bound, so **larger batches are nearly free
in time and directly proportional in throughput** — this is why
[`cost-matrix.md` §3](../matrix/cost-matrix.md#3-max-throughput--1m-output-tokens-s4-4k-in--512-out)'s
S4 operating points sit at concurrency 256–3,327 and cost 2–10× less per token
than the S1 points at concurrency 8–128. Batching is the entire GPU cost story.

On Groq and Cerebras the weights are already resident at enormous bandwidth, so
**there is far less bandwidth-bound slack for batching to absorb.** Three
consequences follow, and all three are visible in published behaviour rather
than asserted here:

1. **Their headline numbers are per-user, not aggregate.** Cerebras' CS-5 target
   is *"up to 10,000 output tokens per second **per user**"*; Groq's table is
   "SPEED (T/SEC)" per stream. Nobody in §3B.5–§3B.7 publishes an aggregate
   tokens/s/chip figure, and that omission is consistent across all three
   vendors. Compare [`cost-matrix.md`](../matrix/cost-matrix.md)'s operating
   points, which are always `GPUs × concurrency · aggregate tok/s/GPU · TPOT`.
   **⚠️ The two number families are not comparable and must never be put in one
   column.**
2. **Cerebras bought Trainium for prefill.** The 2026-03-13 disaggregation
   announcement pairs *"compute-optimized"* AWS Trainium for prefill with CS-3
   for decode. A company with 21 PB/s of memory bandwidth outsourcing the
   *compute-bound* phase is the clearest available evidence that wafer-scale is a
   decode-side architecture whose prefill economics are poor.
3. **Cerebras needed BTA to keep MoE utilisation up.** Without batch tiling,
   throughput degraded **53 % with more experts and 86 % at lower sparsity**
   [src](https://www.cerebras.ai/blog/moe-guide-scale). Sparse models
   *underutilise* a wafer in a way they do not underutilise a bandwidth-bound
   GPU — the opposite of the GPU intuition, where MoE sparsity is what makes
   large models affordable.

### 4.3 The price ratio tells you the architecture

Set the output/input price ratio side by side. This is the cleanest published
fingerprint of where each architecture's cost lives:

| Provider | Model | Input $/1M | Output $/1M | **out ÷ in** |
|---|---|---:|---:|---:|
| **Cerebras** | qwen-3.8-27b | $0.99 | $1.49 | **1.5×** |
| **Cerebras** | gpt-oss-120b | $0.35 | $0.75 | **2.1×** |
| SambaNova | gpt-oss-120b | $0.22 | $0.59 | 2.7× |
| Groq | gpt-oss-120b | $0.15 | $0.60 | 4.0× |
| Groq | qwen/qwen3.8-27b | $0.80 | $4.00 | **5.0×** |
| DeepInfra | qwen3.8-27b | $0.15 | $1.875 | 12.5× |
| Together / Fireworks | DeepSeek-V4.1-Flash | $0.30 | $1.20 | 4.0× |
| DeepSeek (first-party) | DeepSeek-V4.1-Flash | $0.15 | $0.60 | 4.0× |
| Qwen Cloud list | Qwen3.8-27B | $0.50 | $3.00 | 6.0× |

Cerebras at **1.5×** is the outlier and it is not an accident: on a wafer,
decode is cheap and prefill is not, so the ratio compresses. Groq at **5.0×** on
the same 27B model sits in the GPU range — *(inferred)* consistent with Groq
pricing to the market on a model where its architecture does not give it a cost
edge, and/or with the fleet transition described in §3B.5.

And note the **absolute** input prices: Cerebras charges **$0.99/1M input on a
27B dense** where DeepInfra charges **$0.15** and Darkbloom **$0.10** — a 6.6–10×
premium on prefill. That is the cost of the architecture, paid in the phase it
is worst at.

### 4.4 Cost per token vs this repo's GPU numbers

Against [`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output)'s
blended self-hosting figure for **Qwen3.8-27B — $0.0602–$0.1221 on B300, best
cell in the matrix** — the custom-silicon prices are:

| Provider | Blended (75/25, half input cached) | Self-hosting must be busy… |
|---|---:|---:|
| Cerebras `qwen-3.8-27b` | **$1.1150** | **5.4 % – 11.0 %** |
| Groq `qwen/qwen3.8-27b` | **$1.6000** | **3.8 % – 7.6 %** |
| Cheapest GPU endpoint (Darkbloom, fp4) | $0.5250 | 11.5 % – 23.3 % |
| Qwen Cloud list ([§6.1](../matrix/cost-matrix.md#61-published-vendor-prices)) | $0.9563 | 6.3 % – 12.8 % |

(Break-even `u* = self-hosted blended ÷ provider blended`, the same identity as
[`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation);
Groq and Cerebras publish no cached-input rate, so their blends use the full
input price — which makes them *look better* than a cached-rate blend would.)

**A rented B300 that is busy 8 % of the time beats Groq on this model.** The
custom-silicon platforms are not competing on cost per token for a 27B dense
model, and their own pricing says so. What they sell is **1,850–3,000 tokens per
second to a single user**, which no configuration in
[`cost-matrix.md`](../matrix/cost-matrix.md) reaches at any price — the fastest
Qwen3.8-27B S1 operating point in the whole matrix is GB300 at **28.7 ms TPOT**,
i.e. ~35 tok/s per stream. That is a **50× per-stream gap**, and it is the
entire product.

### 4.5 The thing that does not change

**Prefill.** Every architecture in this document, silicon included, ends up
disaggregating prefill from decode and giving prefill different, compute-heavier
hardware or a different topology: DeepSeek EP32-vs-EP144, Moonshot's separate
prefill cluster, Together's three-tier CPD, Fireworks' disaggregation as a
FireOptimizer axis, Cerebras buying Trainium. **Prefill/decode disaggregation is
the one architectural conclusion that every serious operator in this study
reached independently**, on GPUs, on wafers and on dataflow units alike. If a
bare-metal cluster adopts exactly one idea from this document, it should be that
one — see [`serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md#3-parallelism-for-large-moe).

---

## 5. Pricing signals vs self-hosting — the market on 2026-09-19

Self-hosting figures are quoted **by name** from
[`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output);
nothing here re-derives one. Provider blends use
[`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost)'s mix — **75 % input, half of it
cached at the provider's own published cached rate, 25 % output** — and
break-even is `u* = self-hosted blended ÷ provider blended`, exactly as in
[`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation).

**Validation.** Reproducing the repo's own §6.2 cells with this generator gives
DeepSeek-V4.1-Flash on B200 = **91.6 %** (matrix prints 92 %) and Qwen3.8-27B on
B300 = **6.3 % / 12.8 %** (matrix prints 6 % / 13 %). The arithmetic below is
the same arithmetic.

Third-party prices are from
[`openrouter.ai/api/v1/models/{slug}/endpoints`](https://openrouter.ai/docs/features/provider-routing),
fetched 2026-09-19; first-party prices are from each vendor's own page.

### 5.1 DeepSeek-V4.1-Flash-class MoE — 23 endpoints

Self-hosted blended, best cell: **base checkpoint B200 $0.190–$0.443**;
**NVFP4 checkpoint B200 $0.1485–$0.3464**
([`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output)).

| Provider | in $/1M | out $/1M | cached in | **blended** | u* base (low–high) | u* NVFP4 (low–high) | declared quant |
|---|---:|---:|---:|---:|---:|---:|---|
| **DeepInfra** | 0.140 | 0.420 | 0.0042 | **0.1591** | 119 % ✗ / 278 % ✗ | **93 %** / 218 % ✗ | fp8 |
| Relace | 0.130 | 0.520 | 0.0026 | 0.1797 | 106 % ✗ / 247 % ✗ | **83 %** / 193 % ✗ | fp4 |
| Morph | 0.135 | 0.540 | 0.0041 | 0.1871 | 102 % ✗ / 237 % ✗ | **79 %** / 185 % ✗ | unknown |
| **DeepSeek (first-party)** | 0.150 | 0.600 | 0.0030 | **0.2074** | **92 %** / 214 % ✗ | **72 %** / 167 % ✗ | unknown |
| Alibaba | 0.150 | 0.600 | 0.0150 | 0.2119 | **90 %** / 209 % ✗ | **70 %** / 164 % ✗ | unknown |
| **Wafer** | 0.200 | 0.600 | 0.0060 | 0.2273 | **84 %** / 195 % ✗ | **65 %** / 152 % ✗ | unknown |
| Sail Research | 0.200 | 0.600 | 0.0400 | 0.2400 | **79 %** / 185 % ✗ | **62 %** / 144 % ✗ | fp4 |
| Fireworks *(OR row)* | 0.220 | 0.660 | 0.0070 | 0.2501 | **76 %** / 177 % ✗ | **59 %** / 139 % ✗ | unknown |
| StreamLake | 0.282 | 1.128 | 0.0056 | 0.3899 | **49 %** / 114 % ✗ | **38 %** / **89 %** | fp8 |
| GMICloud | 0.285 | 1.140 | 0.0057 | 0.3940 | **48 %** / 112 % ✗ | **38 %** / **88 %** | fp8 |
| **Novita** | 0.285 | 1.140 | 0.0057 | 0.3940 | **48 %** / 112 % ✗ | **38 %** / **88 %** | fp8 |
| **Fireworks** *(own docs, Standard)* | 0.300 | 1.200 | 0.0060 | 0.4147 | **46 %** / 107 % ✗ | **36 %** / **84 %** | — |
| **Together** | 0.300 | 1.200 | 0.0060 | 0.4147 | **46 %** / 107 % ✗ | **36 %** / **84 %** | unknown |
| Makora / DigitalOcean / SiliconFlow / Parasail | 0.300 | 1.200 | 0.0060 | 0.4147 | **46 %** / 107 % ✗ | **36 %** / **84 %** | unknown / fp8 |
| **Modal** | 0.300 | 1.200 | 0.0300 | 0.4237 | **45 %** / 105 % ✗ | **35 %** / **82 %** | unknown |
| AtlasCloud / BaseTen | 0.300 | 1.200 | 0.0300 | 0.4237 | **45 %** / 105 % ✗ | **35 %** / **82 %** | fp8 |
| Phala | 0.345 | 1.380 | 0.0069 | 0.4770 | **40 %** / **93 %** | **31 %** / **73 %** | unknown |
| Fireworks *(Priority)* / Venice | 0.375 | 1.500 | 0.0075 | 0.5184 | **37 %** / **85 %** | **29 %** / **67 %** | — / fp8 |

`✗` = above 100 %, i.e. self-hosting cannot match that price at any utilisation.
"low" = cheapest reputable on-demand B200 ($6.00 Hyperstack); "high" = cheapest
hyperscaler B200 ($14.00 OCI) — [`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md).

**What it says.**

1. **The floor moved below the model author.** DeepInfra at $0.14/$0.42 undercuts
   DeepSeek's own off-peak $0.15/$0.60 by **23 % blended**. Against that floor,
   the base checkpoint on B200 at the cheap tier needs **119 % utilisation — it
   cannot win at all**, and even the NVFP4 checkpoint needs **93 %**.
2. **Self-hosting the base checkpoint beats essentially nobody in the cheap half
   of the market.** Of 23 endpoints, the base checkpoint clears 100 % against 20
   of them only on the *low* price tier, and on the *high* (hyperscaler) tier it
   clears **three**. This agrees with
   [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)'s
   verdict — one cell of sixteen — and extends it: the market is now *cheaper*
   than the single vendor that §6.2 measured against.
3. **The 3.3× spread across identical model IDs is the real finding.**
   $0.1591 to $0.5184 blended, same `deepseek/deepseek-v4.1-flash` slug —
   **3.26×** (⚠️ corrected 2026-09-19 from "3.7×", which no pair of rows in
   this table produces). The
   declared quantizations behind that spread run **fp4 → fp8 → unknown**, and
   twelve of 23 endpoints declare `unknown`. A self-hoster's quality baseline is
   not comparable to "the API" — it is comparable to *a specific endpoint at a
   specific precision*, and half the market will not tell you which.
4. **⚠️ Fireworks appears twice at incompatible prices** ($0.22/$0.66 via
   OpenRouter vs $0.30/$1.20 in its own docs). Both rows are printed. This is
   unresolved.

### 5.2 Qwen3.8-27B dense — 19 endpoints, including both custom-silicon vendors

Self-hosted blended, best cell: **B300 $0.0602–$0.1221**; best interactive cell
H200 $0.159/1M output
([`cost-matrix.md` §2](../matrix/cost-matrix.md#2-interactive--1m-output-tokens-at-tpot--50-ms-s1-4k-in--512-out), §4).

| Provider | in $/1M | out $/1M | cached in | **blended** | u* (low–high) | quant | notes |
|---|---:|---:|---:|---:|---:|---|---|
| Darkbloom | 0.100 | 1.800 | — | **0.5250** | **11.5 % / 23.3 %** | fp4 | cheapest blended |
| **DeepInfra** | 0.150 | 1.875 | 0.0375 | 0.5391 | 11.2 % / 22.7 % | **bf16** | undercuts FP8 rivals at BF16 |
| Phala | 0.199 | 2.075 | 0.0415 | 0.6090 | 9.9 % / 20.0 % | unknown | |
| Chutes | 0.240 | 2.200 | 0.0240 | 0.6490 | 9.3 % / 18.8 % | fp8 | |
| Parasail | 0.240 | 2.200 | 0.0500 | 0.6588 | 9.1 % / 18.5 % | fp8 | |
| AkashML | 0.250 | 2.200 | 0.0500 | 0.6625 | 9.1 % / 18.4 % | fp8 | |
| DekaLLM | 0.200 | 2.500 | 0.0500 | 0.7188 | 8.4 % / 17.0 % | unknown | |
| Reka | 0.214 | 2.550 | 0.1500 | 0.7740 | 7.8 % / 15.8 % | fp8 | |
| Mancer 2 | 0.200 | 2.500 | — | 0.7750 | 7.8 % / 15.8 % | fp8 | 100 % uptime |
| Ionstream | 0.280 | 2.550 | 0.1000 | 0.7800 | 7.7 % / 15.7 % | fp8 | |
| Alibaba *(via OR)* | 0.425 | 2.550 | 0.0850 | 0.8287 | 7.3 % / 14.7 % | unknown | 1M ctx |
| Io Net | 0.300 | 2.800 | 0.1800 | 0.8800 | 6.8 % / 13.9 % | fp8 | 64K ctx only |
| Novita | 0.420 | 3.000 | 0.0850 | 0.9394 | 6.4 % / 13.0 % | unknown | 1M ctx |
| CoreWeave | 0.400 | 3.000 | 0.1500 | 0.9563 | 6.3 % / 12.8 % | fp8 | |
| **Qwen Cloud list** ([§6.1](../matrix/cost-matrix.md#61-published-vendor-prices)) | 0.500 | 3.000 | 0.0500 | **0.9563** | **6.3 % / 12.8 %** | — | the repo's existing denominator |
| Cloudflare | 0.450 | 3.200 | 0.0500 | 0.9875 | 6.1 % / 12.4 % | unknown | 91.07 % 30-min uptime |
| **Cerebras** | 0.990 | 1.490 | n/p | **1.1150** | **5.4 % / 11.0 %** | fp16 declared | **~1,850 tok/s** |
| Venice | 0.450 | 3.200 | — | 1.1375 | 5.3 % / 10.7 % | fp8 | |
| **Groq** | 0.800 | 4.000 | n/p | **1.6000** | **3.8 % / 7.6 %** | n/p | **~450 tok/s**, preview |

"low" = B300 at $7.40 (Hyperstack), "high" = $15.00 (OCI).

⚠️ **Sourcing correction, 2026-09-19.** Two rows in this table are **not**
OpenRouter endpoints: **Cerebras** and **Groq** do not serve this slug on
OpenRouter, so their prices are first-party. A third, **Qwen Cloud list**, is
the repo's denominator, not an endpoint. The OpenRouter listing for this slug
returns **18** endpoints, of which 16 appear above — so "19 endpoints" in the
heading counts three non-OpenRouter rows and omits two OpenRouter ones. The
per-row arithmetic is unaffected.

⚠️ **Blend convention, made explicit.** Rows with no published cached-input
rate (Darkbloom, Mancer 2, Cerebras, Venice, Groq) are blended as
`0.75 × in + 0.25 × out` — all input charged uncached — not at
METHODOLOGY's `0.4125 × in + 0.25 × out`. That is the correct treatment (no
cache product means no cache discount), but it makes those rows *not*
arithmetically comparable to the cached rows, and it is why they look
expensive. Re-derived in `python3` on 2026-09-19: every other cell in §5.1,
§5.2 and §5.4 reproduces to the printed precision.

**What it says.**

1. **Self-hosting wins this model against every endpoint in the market, by a
   wide margin.** Worst case is **23.3 %** utilisation (vs the cheapest fp4
   endpoint, at hyperscaler B300 prices); best case **3.8 %**. That confirms and
   strengthens [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)'s
   "self-hosting wins everywhere, 6–28 % break-even" — the 2026-09-19 market has
   a *cheaper* floor than Qwen Cloud list (Darkbloom $0.525 vs $0.956) and
   self-hosting still wins by 4×.
2. **A dense 27B is the shape self-hosting is built for.** One GPU, no expert
   parallelism, no MoE all-to-all, no exotic KV layout. Everything in this
   document that is hard — EP144, three load balancers, KVCache-centric
   scheduling, wafer partitioning — is machinery for models that do not fit on
   one card. For a model that does, the machinery is pure overhead, and the
   provider has to charge for it.
3. **The precision spread is 4× and the price does not track it.** DeepInfra
   serves **bf16** at $0.5391 blended while Reka and Ionstream serve **fp8** at
   $0.774–$0.78 — i.e. the *higher-precision* endpoint is **31 % cheaper**. Any
   "the API is cheaper than self-hosting at quality X" argument has to name the
   endpoint.

### 5.3 Cross-check: gpt-oss-120b, where all three custom-silicon vendors compete

Not a repo model, but the only slug served by Groq, Cerebras, SambaNova *and*
twenty GPU providers — so it isolates the silicon variable at fixed model
[src](https://openrouter.ai/api/v1/models/openai/gpt-oss-120b/endpoints):

| Provider | in | out | out÷in | Notes |
|---|---:|---:|---:|---|
| AkashML / CoreWeave / DekaLLM | $0.030 | $0.17–0.18 | 5.7–6.0× | bf16 / fp4 |
| DeepInfra `bf16` | $0.037 | $0.17 | 4.6× | ⚠️ **not** cheapest — AkashML/CoreWeave/DekaLLM undercut it at $0.030 input (corrected 2026-09-19) |
| Crusoe / Novita | $0.050 | $0.25 | 5.0× | |
| **SambaNova** | $0.140 | $0.95 | 6.8× | |
| **Groq** | $0.150 | $0.60 | 4.0× | **500 tok/s** |
| Together / Nebius / Bedrock / SiliconFlow / Phala | $0.150 | $0.60 | 4.0× | market consensus price |
| **Cerebras** `fp16` | $0.350 | $0.75 | **2.1×** | **~3,000 tok/s** |

Groq prices **identically to Together, Nebius and Bedrock** while delivering
~500 tok/s per stream; Cerebras charges **2.3× more on input, 1.25× on output**
and delivers ~6× the per-stream speed. **Cerebras' 2.1× out/in ratio persists
across both its models**, which is the strongest evidence that the compressed
ratio in §4.3 is architectural and not a one-model promotion. Meanwhile the
cheapest GPU endpoint is **$0.030/$0.17 (AkashML, CoreWeave, DekaLLM) — an
**11.7× input spread within one slug** (⚠️ corrected 2026-09-19 from "9.5×",
which was measured against DeepInfra's $0.037 rather than the actual floor).

### 5.4 Kimi-K3 — 20 endpoints, for the format question

Self-hosted blended, best cell: **B300 $2.3808–$4.8259**
([`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output)).
Cheapest endpoint **Relace $1.70/$8.50 (fp4)**; Moonshot first-party
**$3.00/$0.30/$15.00 (mxfp4)**; most expensive **Morph $6.00/$22.50**. Declared
quantizations across the twenty: **fp4** (Relace, InferenceNet, Sail Research,
Parasail), **mxfp4** (Chutes, Modal, Moonshot AI), **fp8** (BaseTen), **bf16**
(DeepInfra), **unknown** (eleven)
[src](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints).

Against Moonshot's own $4.99 blended, B300 at $2.38 needs **48 %** and at $4.83
needs **97 %** — reproducing
[`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)
exactly. Against the *cheapest* endpoint (Relace, blended **$2.8262**), B300
needs **84 % / 171 % ✗** — i.e. **the cheap tier of the market has already
closed most of the gap that §6.2 says B300 self-hosting opens**, and on
hyperscaler pricing it has closed it entirely.

### 5.5 Summary — what utilisation self-hosting must hit

| This repo's model | Best self-hosted blended | vs cheapest endpoint | vs first-party/list | Verdict |
|---|---|---|---|---|
| DeepSeek-V4.1-Flash (base) | B200 $0.190–$0.443 | **119 % / 278 % ✗** (DeepInfra $0.1591) | 92 % / 214 % ✗ (DeepSeek $0.2074) | **Buy tokens.** Cannot win against the market floor at any utilisation. |
| DeepSeek-V4.1-Flash-NVFP4 | B200 $0.1485–$0.3464 | **93 % / 218 % ✗** | 72 % / 167 % ✗ | **Marginal.** Wins only on cheap-tier B200 at near-full utilisation, at the matrix's least defensible operating point (concurrency 3,557, [§9.2](../matrix/cost-matrix.md#92-cells-that-are-priced-but-cannot-be-bought-today)). |
| **Qwen3.8-27B** | **B300 $0.0602–$0.1221** | **11.5 % / 23.3 %** | 6.3 % / 12.8 % | **Self-host.** Wins against all 19 endpoints including both custom-silicon vendors. |
| Kimi-K3 | B300 $2.3808–$4.8259 | **84 % / 171 % ✗** (Relace $2.8262) | 48 % / 97 % (Moonshot $4.99) | **Buy tokens** unless you need the weights on your own metal. |
| Marlin-2B | B300 $0.0092–$0.0185 | no endpoint exists | — | **Self-host** — no market. |

The pattern is consistent and worth stating plainly: **self-hosting wins
decisively on the model that fits on one GPU and loses on every model that does
not.** For the large MoE models, what you are buying from a provider is not
cheaper silicon — §3B.10's 545 % theoretical margin proves the price is not near
cost — it is **aggregated utilisation** across thousands of tenants, which is
the one input a single-tenant cluster cannot manufacture.

### 5.6 The economic reading, in three numbers

Three numbers frame the make-vs-buy decision, and all three come from this
document's own pricing tables:

1. **~5× spread on an identical H100-hour** ($1.89 fal discounted → $10.00
   Hugging Face on GCP, §2.3). The stack wrapped around the GPU, not the GPU,
   sets the price.
2. **~20–37 % is the market price of scale-to-zero plus a queue** — RunPod
   Serverless vs Pods, the same vendor on the same silicon. ⚠️ **Corrected
   2026-09-19:** RunPod's serverless docs rate table says $4.18 vs $3.49
   (+20 %); RunPod's own pricing page says $4.79 vs $3.49 (+37 %). Use the
   range, not the point.
3. **~2× is the market price of a managed serving stack on a per-token basis** —
   Baseten's DeepSeek-V4.1-Flash at $1.20/1M output against the model vendor's
   own $0.60/1M ([`../README.md` §3](../README.md)).

For a bare-metal operator, (1) says the opportunity is real, (2) says
elasticity is cheap to buy and expensive to build, and (3) says the managed
margin is roughly what you must beat in *engineering* cost, not just hardware
cost. Feed all three into
[`07-cost-engineering.md` §1](07-cost-engineering.md).

And hold §3B.10's disclosure alongside them: DeepSeek's **545 % theoretical
margin** at $2/GPU-hour means the published API price is nowhere near marginal
cost. When [`../matrix/cost-matrix.md` §6.2](../matrix/cost-matrix.md) says
self-hosting DeepSeek-V4.1-Flash needs **92 % utilisation on B200** to match
the API, that is measured against a price with an enormous margin baked in —
**the physics gap is far smaller than the price gap.** What the API really
sells you is someone else's utilisation risk.

---

## 6. What transfers to a bare-metal cluster — ranked

Ranked by expected leverage for a single-tenant cluster serving this repo's
five models ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)) — large MoE
checkpoints (DeepSeek-V4.1-Flash 510 GB, Kimi-K3 1.56 TB) plus one dense 27B.
The ranking is *leverage × evidence quality*, not novelty: #1–#5 are worth more
than everything below them combined, and every one of them is corroborated by
at least two independent operators.

### 6.1 The ranked list

| # | Technique | Provider evidence (published) | Where implementing it is covered |
|---|---|---|---|
| **1** | **Prefill/decode disaggregation**, with independently sized pools and independently chosen parallelism per phase | DeepSeek **EP32 prefill vs EP144 decode on one model** — a 4.5× difference, so any single-topology deployment is wrong for one phase; Baseten **up to 6× TPS/GPU**; Together's CPD adds a **pre-prefill tier** for low-reuse prompts and measures **+35–40 % sustainable throughput**; Moonshot, Fireworks, Ray Serve LLM ship it; **Cerebras bought Trainium for prefill** | [`02-serving-stack-and-routing.md` §1, §3](02-serving-stack-and-routing.md); [`09-reference-architectures.md` §4](09-reference-architectures.md); [`../cross-cutting/serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md) |
| **2** | **KV-aware routing plus a tiered KV store** — route to the replica holding the prefix, and spill the cache down HBM → host DRAM → NVMe/RDMA rather than dropping it | Baseten **KV-cache-aware routing 34–62 % in production**; Mooncake turns idle **CPU, DRAM and SSD** into a cluster-wide KVCache and measures **up to 525 %** under SLO (sim) / **75 % more requests** (prod); Together runs HBM → host DRAM → cluster RDMA; DeepSeek's production **56 % input-cache hit rate** is the reference for what reuse is worth; Cerebras proves the contract can be **automatic, 128-token blocks, TTL ≥5 min, no client breakpoints** | [`02-serving-stack-and-routing.md` §3](02-serving-stack-and-routing.md); [`04-throughput-and-utilization.md` §4](04-throughput-and-utilization.md) |
| **3** | **Concurrency-based autoscaling**, with a set-point distinct from the hard cap and **asymmetric windows** (fast up, slow down) | Together's controlled replay: `inflight_requests` scaled correctly and improved p95, while a **TTFT p95 target and a 75 % GPU-utilisation target both never fired** during saturation; RunPod recommends request-count **for LLM workloads**; Modal separates `target_inputs` from `max_inputs`; Fireworks **30 s up / 10 min down**; Together **60 s / 300 s**; Hugging Face documents why 80 % GPU-util misfires during model load. Refinement worth stealing: Baseten's warning that **token-weighted load ≠ request count** | [`05-autoscaling-and-predictive-scaling.md` §2, §3](05-autoscaling-and-predictive-scaling.md) |
| **4** | **Tiered, content-addressed weight store with in-cluster peer fan-out** and a per-deployment content-hash manifest | Baseten BDN: node NVMe → peer cache on a **consistent hash ring** → mirrored origin; **>2 GB/s** onto H100 nodes, **2–3× faster cold starts**, and **50 replicas of a 140 GB model = 1× origin bandwidth**. Modal: **~5 MB image index**, content-addressed files, memory → SSD → AZ → CDN → blob at **~2.5 GiB/s**, mount in 1–100 ms. Pulling 1.56 TB to eight nodes from one object store is 12.5 TB of origin traffic; the ring makes it 1.56 TB | [`06-cold-start.md` §2](06-cold-start.md); [`01-bare-metal-cluster.md` §5](01-bare-metal-cluster.md) |
| **5** | **CPU + GPU memory snapshot/restore**, fired by an **explicit trigger** once the engine reports ready and CUDA graphs are captured | Three shipped designs: Modal (lifecycle-annotated, **vLLM Qwen2.5 45 s → 5 s**, Parakeet **20 → 2 s**, ViT **8.5 → 2.25 s**, `import torch` **5 s → 1.05 s**), Beam (fixed point at the end of `on_start`), Cerebrium (**POST to a sidecar**, the most flexible and the easiest to bolt onto an existing engine startup). Enabling tech is **NVIDIA's CUDA checkpoint API, driver branches 570/575** — available to you. Published caveats are real: no multi-GPU, awkward with `torch.compile`, **does not speed up storage reads**, restored randomness is identical | [`06-cold-start.md` §4](06-cold-start.md) |
| **6** | **Speculative decoding, drafted on your own traffic** — and treated as a tunable, not a switch | Together: **20M tokens (~10k pairs) of a customer's own traffic already buys >1.10×**, and custom speculators give **1.23–1.45×** over a base speculator, **23–26 % fewer GPU-hours**; ATLAS reaches **up to 4×** at batch 1. Fireworks' worked example is the warning: **76 % acceptance → 2× faster vs a generic drafter's 29 % → 1.5× *slower***. Baseten's hybrid **suffix automaton + MTP** gives **+40 % throughput or −40 % latency vs MTP alone** and **+34 % acceptance length**, with device-side, sync-free automaton updates. Novita's published **draft-window sweep (n=3→7, 2.17 → 2.55×)** says the window has a real optimum. Cloudflare's **prompt-lookup** is the zero-training option: **+40 % (8B) / +70 % (70B)**. ⚠️ Every one of these numbers is at **batch size 1** | [`../cross-cutting/serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md) |
| **7** | **Admission control priced on the scarce phase**, not on the thing that is easy to count | Cerebras rate-limits **uncached tokens separately from total tokens**, so a better hit rate buys more total throughput — the only scheme here that meters *prefill*. Moonshot uses **prediction-based early rejection** under overload, because a request admitted and missed costs the same compute as one served. Together isolates **batch / real-time / untrusted** in separate queues with policy-driven control rather than one FIFO. Baseten queues during scale-up with optional priority classes | [`03-concurrency-and-admission-control.md` §2–§4](03-concurrency-and-admission-control.md) |
| **8** | **Warm floor *and* warm buffer, measured and costed separately** | Every provider exposes a floor; only Modal (`buffer_containers`) and fal (`concurrency_buffer`, `concurrency_buffer_perc`) expose capacity held **while the service is busy** to absorb the next burst. Keep-warm defaults cluster tightly where published: RunPod idle **5 s**; Beam **180 s / 10 s / 600 s**; Modal **60 s**; HF **15 min**. Beam's arithmetic makes the cost visible: 1 s boot + 100 ms work + 300 s keep-warm bills **301.1 s** | [`05-autoscaling-and-predictive-scaling.md` §5](05-autoscaling-and-predictive-scaling.md); [`06-cold-start.md` §5](06-cold-start.md) |
| **9** | **A quantization ladder with a published acceptance bar** instead of a vibe | Fireworks' **L1–L4 aggression levels** (L1 MLP excl. first/last → L2 +QKV → L3 +KV cache → L4 +attention prefill) judged by **KL divergence and token rejection rate on forced quantized generation, prefill and decode measured separately**, with the bar at **KLD < 0.007** (Llama-3.1-8B: 0.00286 at L1 → 0.00796 at L4). Counterpoint worth holding in view: Cerebras' **storage-only** quantization, where activations, attention and KV stay full precision. Together prices the whole axis: **FP16→FP8→FP4 is worth 20–40 %** | [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md); [`07-cost-engineering.md` §5](07-cost-engineering.md) |
| **10** | **Host-affinity scheduling to nodes that already hold the checkpoint** | RunPod's cached models: *"Runpod automatically tries to start your workers on hosts that already contain the selected model"*, claiming cold starts "to just a few seconds, even for large models", and it **does not bill the download** — i.e. it pays for its own cache misses. Bare-metal version: label nodes by resident checkpoints on local NVMe and schedule replicas to them; complements #4 exactly | [`05-autoscaling-and-predictive-scaling.md` §7](05-autoscaling-and-predictive-scaling.md) |
| **11** | **Two request paths: a durable long-window batch queue and direct interactive routing** | RunPod ships both shapes (queue-based `/run` with guaranteed execution and retries vs load-balancing endpoints with no queue); **five independent operators price deferral at exactly 50 %** (Anthropic, OpenAI, Groq, Fireworks, Together); Groq goes to a **7-day** window, and OpenAI gives batch a **separate rate-limit pool**. Corollary for a fixed fleet: if you can shift half your token volume into a long-window queue, you need roughly half the peak capacity | [`09-reference-architectures.md` §6](09-reference-architectures.md); [`04-throughput-and-utilization.md` §3](04-throughput-and-utilization.md) |
| **12** | **Multi-LoRA on a shared base with adapter caching** | Fireworks: cross-model continuous batching + dynamic adapter loading, **thousands of LoRAs on one Mixtral/Mistral cluster**, **~90 % of base-model speed** serving hundreds of adapters, priced at base-model rates. Ray Serve LLM and Cloudflare ship multi-LoRA; Cerebras announced it ⚠️ without a mechanism. Caveat: the *economics* come from aggregating demand across tenants — a single tenant with three adapters gets almost none of it | [`02-serving-stack-and-routing.md` §5](02-serving-stack-and-routing.md) |
| **13** | **Phase-specific load balancing — more than one balancer** | DeepSeek runs **three**: prefill balances core-attention compute and dispatch-send load; decode equalises **KV-cache occupancy** and request count; the expert-parallel balancer minimises maximum dispatch-receive load. A single "least connections" balancer gets all three wrong | [`02-serving-stack-and-routing.md` §3](02-serving-stack-and-routing.md) |
| **14** | **Compile, JIT and CUDA-graph caches persisted on shared storage**, keyed by shape × arch × engine version | Replicate ships `torch.compile` caching as a product feature (2025-09-08); Cloudflare's Infire builds **a dedicated CUDA graph for every batch size, on demand** — a concrete alternative to a fixed capture list. This is the third of Baseten's four named cold-start bottlenecks (procurement → image → weights → **engine startup and compilation**) | [`06-cold-start.md` §3](06-cold-start.md) |
| **15** | **Per-head KV-cache compression** | Cloudflare open-sourced theirs on PagedAttention with **per-attention-head compression rates**: on LongBench with Llama-3.1-8B, *"over 95 % task performance while reducing cache size by up to 8×"*, throughput **+3.44× at 8×** and **+5.18× at 64×**. ⚠️ Verify per model — the result is GQA, and four of this repo's five models use MLA, KDA or GDN, where "per-head" may not even be well-defined | [`04-throughput-and-utilization.md` §4](04-throughput-and-utilization.md); [`../cross-cutting/serving-optimizations.md` §1](../cross-cutting/serving-optimizations.md) |
| **16** | **A standardised model container with an enforced `setup()`/`run()` boundary** — adopt one, do not write a third | Replicate's **Cog** (OCI image from `cog.yaml` + a Python `Runner`, **OpenAPI schema generated from type hints**, Rust/Axum HTTP server, runs anywhere Docker runs) and Baseten's **Truss** are both open. Beam's `on_start`, Cerebrium's module-scope convention and fal's `fal.App.setup()` are the same boundary. It is what makes snapshotting (#5) and warm pools (#8) tractable later, because "what is cold-start work" becomes a type-level fact | [`09-reference-architectures.md` §2](09-reference-architectures.md) |
| **17** | **Active GPU health checking, with drain-and-reimage rather than recover** | Modal runs DCGM diags, GPUBurn and weekly NCCL all-reduce with Xid/ECC monitoring and reports *"we almost never have GPU problems slip through"* — with one honest residual, *"Cloud C's L4s flake at CUDA initialization in 0.1 % of cases"*. Together runs passive telemetry **plus active checks between workloads** — "detect fast, drain, replace" — and measures uptime at **inference completion, not at the load balancer**. Baseten designs against Llama-3's **~1 GPU failure per 50,000 GPU-hours** | [`01-bare-metal-cluster.md` §6](01-bare-metal-cluster.md); [`08-reliability-and-operations.md` §2–§3](08-reliability-and-operations.md) |
| **18** | **SIGTERM draining before replica termination**, and never a 502 as cold-start behaviour | Cerebrium documents SIGTERM handling in custom runtimes explicitly to *"avoid 502 errors"*. The negative examples are equally instructive: Hugging Face documents **502 Bad Gateway during replica initialization** with *"no built-in request queueing"*, and Fireworks returns an unqueued **503** at zero. Queue, with a bounded wait and a documented timeout | [`05-autoscaling-and-predictive-scaling.md` §5](05-autoscaling-and-predictive-scaling.md); [`08-reliability-and-operations.md` §5](08-reliability-and-operations.md) |
| **19** | **A five-number cold-start metric**: scheduling / image / weights / init / warm-up — not one number | Cerebrium splits cold start into **queueing** vs **initialization** and tells you to find which dominates before optimising; Baseten names the four sequential bottlenecks; Together publishes per-stage deployment times (**2–14 min by model size**). The internal metric to track is **GPU-seconds-to-first-token-served after a replica is requested**, decomposed | [`06-cold-start.md` §1](06-cold-start.md) |
| **20** | **Open MoE all-to-all kernels and an RDMA transport abstraction**, rather than writing your own | Perplexity's `pplx-kernels` (NVSHMEM dispatch/combine, **EP8–EP128**, transports **NVLink / IBGDA / IBRC / EFA** behind one interface, CUDA-graph support, comm/compute overlap) and its successor `pplx-garden`/`fabric-lib` (MLSys'26, trillion-parameter targets, **<2 s weight transfer**) are the only production MoE all-to-all kernels in this study a self-hoster can actually run | [`../cross-cutting/serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md); [`01-bare-metal-cluster.md` §4](01-bare-metal-cluster.md) |
| **21** | **Profile-first kernel work, starting with occupancy** | Wafer's loop — *Find Bottlenecks → Try Many Paths → Ship Measured Winner* — and its headline case: a Kimi Delta Attention kernel **11.65×** faster once profiling showed it launched **64 blocks on a 145-SM GPU (6.25 % occupancy)**; the fix was register vectorisation and manual unrolling. Wafer also publishes on **benchmark integrity** ("reward hacking in AI kernel generation", "a 104× (?) speedup on KernelBench") — required reading before trusting any LLM-generated kernel. Contrast with fal's CUTLASS EVT epilogue fusion at **1.28×**: graph-level wins are multiples, kernel wins are tens of percent *unless something is broken* | [`04-throughput-and-utilization.md` §5](04-throughput-and-utilization.md); [`08-reliability-and-operations.md` §6](08-reliability-and-operations.md) |
| **22** | **Internal service tiers, and knowing which kind you are selling yourself** | Anthropic's Priority Tier is **reserved** (ITPM + OTPM + duration + model version, 99.5 % target, burndown rates per token type); Fireworks' flat **1.25×** is **scheduler weight against a shared pool**. Only the first survives a capacity crunch. Anthropic's burndown numbers also carry a modelling lesson: a cache **write** costs **1.25× (5-min TTL) or 2.00× (1-hour TTL)** a normal input token while a read costs **0.1×** — so prefix caching needs a **write-side cost term**, which this repo's [`../METHODOLOGY.md` §6](../METHODOLOGY.md) 10 %-of-uncached read rule does not have | [`03-concurrency-and-admission-control.md` §4](03-concurrency-and-admission-control.md); [`07-cost-engineering.md` §4](07-cost-engineering.md) |

Two design patterns from the platforms are worth copying even though they are
not techniques:

- **fal's knob split** — parameters that can only cost money (`keep_alive`,
  `min/max_concurrency`, buffers) are **hot-tunable and persist across
  deploys**; parameters that can break correctness (`max_multiplexing`,
  `startup_timeout`, `machine_type`) are **versioned with the code and reset on
  deploy**. That is a good rule for any in-house control plane.
- **Cerebrium's `replica_concurrency` defaulting to 1 on GPU** is the
  cautionary tale: any platform layer in front of a continuous-batching engine
  must default to *high* concurrency, or it silently serialises the batch.

### 6.2 Do not build these

| Do not build | Why | Evidence |
|---|---|---|
| **Multi-cloud capacity arbitrage** (an LP/MIP solver over instance markets) | Fixed owned fleet; the problem degenerates to bin-packing models onto known GPUs | Modal's OR-Tools/GLOP resource solver exists to exploit price volatility you are not exposed to; Baseten's MCM spans 20+ clouds and took **six months of dedicated engineering** |
| **gVisor or equivalent sandboxing of your own engine** | Measured cost: vLLM host CPU **140 % → 250 %**, plus ~3 % throughput. Bought to isolate mutually hostile tenants; you have one tenant | [src](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine) |
| **Your own inference engine** | The one provider who did it and benchmarked honestly gained **~6.6 %** throughput over vLLM. Rational only if co-tenancy or host CPU is your binding constraint — or if you are output-priced, like fal | Cloudflare Infire; fal's kernel-vs-scheduler split |
| **Unified-memory GPU over-commitment** for large models | Cloudflare's 400 % over-commit and **156 ms** swap-in are for **5 GB** models. A 510 GB or 1.56 TB checkpoint would thrash PCIe | [src](https://blog.cloudflare.com/how-cloudflare-runs-more-ai-models-on-fewer-gpus) |
| **Platform-level dynamic batching in front of a continuous-batching engine** | Adds a `BATCH_WINDOW` of latency for no throughput gain | Inferless's `BATCH_SIZE`/`BATCH_WINDOW`; Baseten's static/dynamic/continuous ladder |
| **A 502 (or an unqueued 503) as your cold-start behaviour** | Queue with a bounded wait instead | HF Endpoints documents the 502 as expected; Fireworks documents *"requests to a scaled-to-zero deployment are not queued"* |
| **Spot/preemptible strategy** | No spot tier on owned hardware; Cerebrium's `protected` 2× tier is the same construct priced | Baseten's reserved/on-demand/spot blend |
| **Pipeline parallelism at layer boundaries as your default split** | Correct on wafers (inter-wafer traffic is activations only); wrong on GPUs, where inference-batch pipeline bubbles are punishing and NVLink makes TP cheap | Cerebras' multi-system split is explicitly the inverse of GPU practice |
| **A second packaging format** | Cog and Truss both exist, both open, both do the job | Replicate, Baseten |
| **A generic drafter, untested** | A 29 %-acceptance drafter made generation **1.5× slower** | Fireworks FireOptimizer |

### 6.3 What structurally does not transfer

Fleet-wide weight caching that needs a fleet (Together's **"4× faster warm
starts"** comes from another replica somewhere already holding the weights);
99.9 % multi-DC uptime, which requires two facilities **each sized to absorb
full load** (a single site buys at most the detect-fast-drain-replace half);
multi-LoRA economics that come from aggregating thousands of tenants' adapters;
per-second billing incentives (your marginal cost for an idle owned GPU is
power, not $3.95/hr, which changes every scale-down threshold); output-based
pricing, which is what funds fal's kernel work; proprietary engines and tuned
kernels that are not distributed (TKC, FireAttention, the fal Inference
Engine); and everything downstream of 44 GB of SRAM at 21 PB/s.

---

## 7. Open questions

Merged from both halves of the study, grouped. Numbering is this document's;
the profile that raises each one is named.

### 7.1 Mechanisms that are shipped but unpublished

1. **What is RunPod FlashBoot actually doing?** Default-enabled on new GPU and
   CPU endpoints, described only as *"retaining worker state after
   spin-down."* Memory snapshot, paused container, or host affinity plus page
   cache? No mechanism, no numbers
   [src](https://docs.runpod.io/serverless/endpoints/endpoint-configurations).
   (§3A.5)
2. **How did Replicate get fine-tuned models to sub-1-second boots in 2023?**
   Announced without mechanism
   [src](https://replicate.com/blog/fine-tune-cold-boots). Adapter-swap on a
   resident base is the obvious guess and is **not** stated. (§3A.6)
3. **What is inside the "fal Inference Engine™"?** The kernel posts are
   excellent and specific, but no architecture document exists: no scheduler,
   batching, cache or multi-tenancy detail, and **no cold-start numbers at
   all** — a conspicuous gap for a serverless vendor. (§3A.2)
4. **What engine does Nebius Token Factory run, and on what?** They own the
   metal and publish a 99.9 % SLA but nothing about the stack. (§3A.12)
5. **Northflank's "GPU time-slicing, 2–4× utilization."** Unqualified, and
   implausible for models that need full HBM. What is actually being shared?
   Compare Cloudflare's Omni, which solves the same problem with unified memory
   and publishes the mechanism. (§3A.9)
6. **Groq's TruePoint Numerics has no public technical page.** Referenced on
   model pages as the quantization approach; `console.groq.com/docs/truepoint`
   and `groq.com/blog/truepoint*` both 404. Precision, tensor coverage and
   quality methodology unknown. (§3B.5)
7. **Cerebras' multi-LoRA mechanism is unpublished.** On a platform whose whole
   premise is SRAM residency, how adapters are held and switched — and what
   they cost in SRAM — is the interesting question, and the announcement does
   not address it. (§3B.6)
8. **DeepInfra publishes no engineering at all** while retailing below the
   model authors. Engine, orchestration, cold start and autoscaling parameters
   are all absent; the only technical signal it emits is the per-endpoint
   `quantization` field it declares to OpenRouter. (§3B.3)
9. **Is Modal's runtime written in Rust?** Widely assumed; the only public
   evidence found is `tokio` in the `seccheck` code samples
   [src](https://modal.com/blog/catching-cryptominers). Not stated anywhere
   fetched. ⚠️ (§3A.1)

### 7.2 Numbers that do not exist in public

10. **Does anyone besides Modal publish GPU-snapshot latency for a real LLM
    engine?** Beam and Cerebrium ship checkpoint/restore with **zero**
    before/after numbers. Modal's vLLM figure (45 s → 5 s, Qwen2.5) is the only
    one found, and it is for a small model. (§3A.7, §3A.10)
11. **Cold-start times are published by exactly one per-token provider.**
    Together's 86 s / 145 s / 2.5 min figures are the only measured numbers in
    that class, and they are for 1×H100 replicas of 9B-class models. Whether
    they generalise — to other engines, model sizes or storage paths — is
    untested, and the serverless platforms report cold starts in a completely
    different regime (seconds, via snapshotting) that may or may not be
    reachable for multi-GPU LLM replicas. (§3B.1)
12. **Neither Groq nor Cerebras publishes an aggregate throughput figure per
    chip or per system** — only per-user token rates. Without one, §5's cost
    comparison can only be made price-to-price, never $/token-at-equal-
    utilisation, and whether wafer-scale is cheaper per token *at full load* is
    unanswerable from public data. SambaCloud publishes **no tokens/second at
    all**. (§3B.5–§3B.7)
13. **Every speculative-decoding number in this document is at batch size 1.**
    Together's ATLAS (4×B200), Novita's DSpark (TP8), Fireworks' acceptance-rate
    examples, fal's DSpark. This repo's cost grid operates at concurrency
    64–3,327. The one contrary data point is Together's 2024 research post,
    which argues speculation *keeps* paying at large batch once KV makes decode
    memory-bound again — on 8×A100 at 32K context only. Flagged as unmeasured
    in [`../README.md`](../README.md) open question 6.
14. **No provider publishes fleet utilisation.** Every "what this price
    implies" statement rests on comparing published price to this repo's cost
    model, which is a bound, not a measurement. DeepSeek's **545 % theoretical
    margin** is the closest thing to a disclosure and is explicitly labelled
    theoretical by its authors. (§3B.10)
15. **Third-party benchmarks are absent.** Every performance number here is
    vendor-published and vendor-selected; no independent cross-provider
    benchmark was located. ⚠️ Web search was unavailable for the §3A half.
16. **Wafer's baselines.** The 11.33×, 8×, 9× and 11.65× speedups are all
    relative to unstated baselines ("stock frameworks", `torch.compile`), and
    the cases page publishes no baseline configurations. Not reproducible as
    published. (§3A.3)
17. **Wafer's Kimi-K3 MI355X result vs this repo's roofline.** 952 tok/s/node
    aggregate and 118 tok/s single-stream on 8× MI355X TP8 with speculative
    decoding at 1,024 in / 400 out — how does that compare to
    [`../models/kimik3/mi355x.md`](../models/kimik3/mi355x.md)? Not reconciled
    here; worth a dedicated pass, since it is measured and most of this repo's
    MI355X rows are `estimate`.
18. **OpenRouter's `latency_last_30m` and `throughput_last_30m` were null for
    every endpoint queried on 2026-09-19.** The API carries them and the docs
    describe threshold routing over a rolling 5-minute window built on them, so
    the data exists somewhere. That is the one missing piece that would let
    §5's price tables carry a measured speed column. (§3B.9)

### 7.3 Conflicts, identity and status changes

19. **Is Groq still serving on LPUs?** The 2025-12-24 NVIDIA licensing
    agreement moved the founder and *"team members"* to NVIDIA; by 2026-08-24
    Groq was announcing deployment of **NVIDIA Groq 3 LPX + Vera Rubin NVL72**.
    Nothing published says what fraction of GroqCloud traffic is served on LPUs
    today, or whether the LPU roadmap continues. Every "custom silicon"
    statement about Groq in §4 is therefore about the architecture **as
    designed**, not necessarily as deployed. ⚠️ (§3B.5)
20. **Fireworks' DeepSeek-V4.1-Flash price is published twice at incompatible
    values** — $0.22/$0.66 via OpenRouter, $0.30/$1.20 in its own serverless
    docs. Either OpenRouter routes to a differently-named deployment or one
    table is stale. Both rows are printed in §5.1; neither is preferred. ⚠️
21. **Modal's scheduler: LP or MIP?** The 2025 post says a GLOP linear program
    [src](https://modal.com/blog/resource-solver); the 2024 post says *"a
    mixed-integer programming problem every minute"*
    [src](https://modal.com/blog/the-future-of-ai-needs-more-flexible-gpu-capacity).
    Either the formulation was relaxed, or one description is loose. (§3A.1)
22. **fal's B200 = 192 GB vs this repo's pinned 180 GB**, and fal's RTX PRO
    6000 at 1.8 TB/s (the *Workstation* part's 1,792 GB/s, not the Server
    Edition's 1,597 GB/s). Baseten and RunPod both list B200 at 180 GB. Is fal
    deploying a different SKU, or quoting marketing specs? Until resolved, do
    not recut any number in [`../METHODOLOGY.md` §8](../METHODOLOGY.md) from
    fal's table. (§3A.2)
23. **`quantization: "unknown"` covers roughly half the market.** Twelve of 23
    DeepSeek-V4.1-Flash endpoints and eleven of 20 Kimi-K3 endpoints declare
    unknown precision, and the field is provider-declared and unverified — so
    even the declared values are claims. Every price comparison in §5 is a
    comparison at *unknown quality* for those rows. (§3B.9)
24. **DeepInfra serves `qwen3.8-27b` declared BF16 below most FP8 competitors**,
    and `kimi-k3` declared BF16 at $2.85/$14.25. Either the declarations are
    wrong, or DeepInfra's hardware economics are materially better than the
    rest of the market's. Its published dedicated rates (H100 $2.20/h vs this
    repo's $3.20 `low`) point at the latter but do not prove it. (§3B.3)
25. **Lambda's retreat from the Inference API** — economics, engineering or
    focus? Only the wind-down notice was found
    [src](https://lambda.ai/inference). The strategic reading offered in §3A.11
    is *(inferred)*: a GPU cloud tried to move up-stack into per-token
    inference and retreated, because the per-token business requires the
    engine, routing, speculation and cache work that Baseten, fal, Together,
    Fireworks and Cloudflare invest in continuously.
26. **Does Cloudflare's per-head KV compression hold on MLA and linear/hybrid
    attention?** Their 8×-at->95 % result is on Llama-3.1-8B (GQA). Four of
    this repo's five models use MLA, KDA or GDN
    ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)), where "per-head compression"
    may not even be well-defined. (§3A.15)

### 7.4 Sourcing failures, recorded rather than filled in

27. **Perplexity's entire serving story is unread** — `perplexity.ai/hub/blog/*`
    and `research.perplexity.ai/*` returned **HTTP 403** behind a Cloudflare
    interstitial to every user-agent tried on 2026-09-19. §3B.8 is built from
    GitHub only; its hardware and parallelism claims are labelled *(inferred)*
    for that reason. A single successful fetch would likely close several
    fields.
28. **Google's Vertex Provisioned Throughput GSU tables could not be
    retrieved** — tokens/second per GSU, minimum GSU per model, burndown rates
    and commitment durations all fetched as empty navigation shells. §3B.13's
    Google entry is a mechanism description with no numbers.
29. **Job postings as a stack signal.** All careers pages probed (Modal, fal,
    Wafer) were client-rendered and yielded nothing. This is a real gap: hiring
    pages are usually the best source for undocumented internals — languages,
    orchestrator, storage layer.
30. **Cog's implementation language split** is only partially resolved: the
    README states a Rust/Axum HTTP server and the CLI is Go, but an exact
    per-component breakdown was not obtainable (the GitHub languages API
    rate-limited during research). (§3A.6)

---

## 8. Sources

All fetched **2026-09-19** unless a retrieval failure is recorded. Deduplicated
across both halves of the study.

### 8.1 Serverless-GPU and GPU-cloud platforms

**Modal** — [blog: truly serverless GPUs](https://modal.com/blog/truly-serverless-gpus) ·
[blog: GPU memory snapshots](https://modal.com/blog/gpu-mem-snapshots) ·
[blog: memory snapshots](https://modal.com/blog/mem-snapshots) ·
[blog: resource solver](https://modal.com/blog/resource-solver) ·
[blog: containers talk (Belotti)](https://modal.com/blog/jono-containers-talk) ·
[blog: GPU health](https://modal.com/blog/gpu-health) ·
[blog: catching cryptominers](https://modal.com/blog/catching-cryptominers) ·
[blog: flexible GPU capacity](https://modal.com/blog/the-future-of-ai-needs-more-flexible-gpu-capacity) ·
[blog: JS and Go SDKs](https://modal.com/blog/sdk-javascript-go) ·
[blog index](https://modal.com/blog) ·
[docs: cold start](https://modal.com/docs/guide/cold-start) ·
[docs: memory snapshot](https://modal.com/docs/guide/memory-snapshot) ·
[docs: scale](https://modal.com/docs/guide/scale) ·
[docs: concurrent inputs](https://modal.com/docs/guide/concurrent-inputs) ·
[docs: GPU](https://modal.com/docs/guide/gpu) ·
[docs: region selection](https://modal.com/docs/guide/region-selection) ·
[pricing](https://modal.com/pricing) ·
[GPU glossary](https://modal.com/gpu-glossary)

**fal** — [fal.ai](https://fal.ai/) ·
[pricing](https://fal.ai/pricing) ·
[enterprise](https://fal.ai/enterprise) ·
[docs: scaling configuration](https://fal.ai/docs/documentation/deployment/scaling-configuration) ·
[docs: runners](https://fal.ai/docs/documentation/deployment/runners) ·
[docs: machine types](https://fal.ai/docs/documentation/deployment/machine-types) ·
[blog: MXFP8 quantizer on Blackwell](https://blog.fal.ai/chasing-6-tb-s-an-mxfp8-quantizer-on-blackwell/) ·
[blog: epilogue fusion](https://blog.fal.ai/crafting-efficient-kernels-with-epilogue-fusion/) ·
[blog: sub-second Ideogram v4](https://blog.fal.ai/serving-sub-second-ideogram-v4-without-quality-loss/) ·
[blog: DSpark, 1000 tok/s](https://blog.fal.ai/how-we-achieved-1000-tok-s-and-16x-throughput-with-dspark-for-ideogram-v4-prompt-expander/) ·
[blog: Ulysses unbound](https://blog.fal.ai/ulysses-unbound-experiments-in-communication-computation-overlap/) ·
[blog: fal and AWS](https://blog.fal.ai/fal-and-aws-building-for-the-next-phase-of-generative-media/) ·
[blog: Series D](https://blog.fal.ai/our-series-d-scaling-fal/) ·
[blog: Patina](https://blog.fal.ai/introducing-patina/) ·
[blog index](https://blog.fal.ai) ·
[docs sitemap](https://docs.fal.ai/sitemap.xml)

**Wafer** — [wafer.ai](https://wafer.ai) ·
[usewafer.com](https://usewafer.com) ·
[wafer.systems — *different company*](https://wafer.systems) ·
[manifesto](https://wafer.ai/manifesto) ·
[cases](https://wafer.ai/cases) ·
[blog index](https://wafer.ai/blog) ·
[blog: Series A](https://wafer.ai/blog/series-a) ·
[blog: inference alpha on AMD](https://wafer.ai/blog/inference-alpha-amd) ·
[blog: Kimi-K3 on MI355X](https://wafer.ai/blog/kimi-k3-mi355x) ·
[blog: profile-guided optimization](https://wafer.ai/blog/profile-guided-optimization) ·
[OpenRouter provider page](https://openrouter.ai/provider/wafer)

**Baseten** — [blog: Baseten Delivery Network](https://www.baseten.co/blog/how-the-baseten-delivery-network-bdn-makes-cold-starts-fast/) ·
[blog: inference stack (Dynamo Day)](https://www.baseten.co/blog/nvidia-dynamo-day-baseten-inference-stack/) ·
[blog: MTP acceptance in the Speculation Engine](https://www.baseten.co/blog/boosting-mtp-acceptance-rates-in-baseten-speculation-engine/) ·
[blog: how we built MCM](https://www.baseten.co/blog/how-we-built-multi-cloud-capacity-management/) ·
[blog: the efficient frontier of LLM inference](https://www.baseten.co/blog/the-efficient-frontier-of-llm-inference/) ·
[blog: Chains](https://www.baseten.co/blog/introducing-baseten-chains/) ·
[blog: Llama on GH200 / Lambda Cloud](https://www.baseten.co/blog/testing-llama-inference-performance-nvidia-gh200-lambda-cloud/) ·
[blog index](https://www.baseten.co/blog/) ·
[book: multi-cloud capacity management](https://www.baseten.co/inference-engineering/book/07-production/multi-cloud-capacity-management/) ·
[book: autoscaling](https://www.baseten.co/inference-engineering/book/07-production/autoscaling/) ·
[book: containerization](https://www.baseten.co/inference-engineering/book/07-production/containerization/) ·
[book index](https://www.baseten.co/inference-engineering/book/) ·
[pricing](https://www.baseten.co/pricing/) ·
[github: Truss](https://github.com/basetenlabs/truss)

**RunPod** — [docs: serverless overview](https://docs.runpod.io/serverless/overview) ·
[docs: endpoint configurations](https://docs.runpod.io/serverless/endpoints/endpoint-configurations) ·
[docs: cached models](https://docs.runpod.io/serverless/endpoints/model-caching) ·
[docs: volume cache](https://docs.runpod.io/serverless/development/volume-cache) ·
[docs: serverless pricing](https://docs.runpod.io/serverless/pricing) ·
[docs: concurrent handler](https://docs.runpod.io/serverless/workers/concurrent-handler) ·
[docs: vLLM worker](https://docs.runpod.io/serverless/vllm/overview) ·
[docs: Flash overview](https://docs.runpod.io/flash/overview) ·
[docs: Flash pricing](https://docs.runpod.io/flash/pricing) ·
[pricing](https://www.runpod.io/pricing) ·
[llms.txt](https://docs.runpod.io/llms.txt)

**Replicate** — [docs: deployments](https://replicate.com/docs/topics/deployments) ·
[blog index](https://replicate.com/blog) ·
[blog: fine-tune cold boots](https://replicate.com/blog/fine-tune-cold-boots) ·
[github: Cog](https://github.com/replicate/cog) ·
[Cog README (raw)](https://raw.githubusercontent.com/replicate/cog/main/README.md)

**Beam** — [docs: introduction](https://docs.beam.cloud/v2/getting-started/introduction) ·
[docs: cold-start performance](https://docs.beam.cloud/v2/topics/cold-start) ·
[docs: scaling out](https://docs.beam.cloud/v2/scaling/concurrency) ·
[docs: concurrent inputs](https://docs.beam.cloud/v2/scaling/concurrent-inputs) ·
[docs: pricing and billing](https://docs.beam.cloud/v2/resources/pricing-and-billing) ·
[docs: GPU acceleration](https://docs.beam.cloud/v2/environment/gpu) ·
[llms.txt](https://docs.beam.cloud/llms.txt)

**Cerebrium** — [docs: faster cold starts](https://cerebrium.ai/docs/performance/faster-cold-starts) ·
[docs: memory and GPU checkpointing](https://cerebrium.ai/docs/performance/checkpointing) ·
[docs: autoscaling apps](https://cerebrium.ai/docs/scaling/scaling-apps) ·
[docs: batching and concurrency](https://cerebrium.ai/docs/scaling/batching-concurrency) ·
[docs: calculating compute cost](https://cerebrium.ai/docs/calculating-cost) ·
[docs: using GPUs](https://cerebrium.ai/docs/hardware/using-gpus.md) ·
[llms.txt](https://cerebrium.ai/docs/llms.txt)

**Inferless** — [docs: overview](https://docs.inferless.com/concepts/overview) ·
[docs: model settings](https://docs.inferless.com/api-reference/model-endpoint/configuring-the-model-settings) ·
[docs: concurrent requests](https://docs.inferless.com/concepts/processing-concurrent-requests) ·
[llms.txt](https://docs.inferless.com/llms.txt)

**Northflank** — [product: GPU PaaS](https://northflank.com/product/gpu-paas) ·
[sitemap](https://northflank.com/sitemap.xml)

**Lambda** — [inference (wind-down notice)](https://lambda.ai/inference) ·
[docs redirect](https://docs.lambda.ai/public-cloud/lambda-inference-api/)

**Nebius** — [Token Factory](https://nebius.com/services/token-factory)

**Hugging Face** — [docs: autoscaling](https://huggingface.co/docs/inference-endpoints/en/autoscaling) ·
[docs: pricing](https://huggingface.co/docs/inference-endpoints/en/pricing)

**Anyscale / Ray** — [Anyscale docs: LLM serving](https://docs.anyscale.com/llm/serving/) ·
[Ray docs: Serve LLM](https://docs.ray.io/en/latest/serve/llm/index.html)

**Cloudflare** — [blog: Infire, the most efficient AI inference engine](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine) ·
[blog: more AI models on fewer GPUs (Omni)](https://blog.cloudflare.com/how-cloudflare-runs-more-ai-models-on-fewer-gpus) ·
[blog: bigger, better, faster](https://blog.cloudflare.com/workers-ai-bigger-better-faster/) ·
[blog: making Workers AI faster](https://blog.cloudflare.com/making-workers-ai-faster) ·
[blog: Workers AI launch](https://blog.cloudflare.com/workers-ai/) ·
[docs: Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) ·
[docs: Workers AI](https://developers.cloudflare.com/workers-ai/) ·
[blog sitemap](https://blog.cloudflare.com/sitemap-posts.xml)

### 8.2 Per-token, custom-silicon and first-party operators

**Together AI** — [Together Inference Engine 2.0](https://www.together.ai/blog/together-inference-engine-2) (2024-07-18) ·
[Speculative decoding for high-throughput long-context inference](https://www.together.ai/blog/speculative-decoding-for-high-throughput-long-context-inference) (2024-09-05) ·
[NVIDIA HGX B200 with the Together Kernel Collection](https://www.together.ai/blog/nvidia-hgx-b200-with-together-kernel-collection) (2025-02-13) ·
[On-demand dedicated endpoints](https://www.together.ai/blog/on-demand-dedicated-endpoints) (2025-03-13) ·
[Customized speculative decoding](https://www.together.ai/blog/customized-speculative-decoding) (2025-05-12) ·
[Fastest inference for DeepSeek-R1-0528 with NVIDIA HGX B200](https://www.together.ai/blog/fastest-inference-for-deepseek-r1-0528-with-nvidia-hgx-b200) (2025-07-17) ·
[ATLAS: adaptive-learning speculator system](https://www.together.ai/blog/adaptive-learning-speculator-system-atlas) (2025-10-10) ·
[Optimizing inference speed and costs](https://www.together.ai/blog/optimizing-inference-speed-and-costs) (2026-01-22) ·
[Dedicated container inference](https://www.together.ai/blog/dedicated-container-inference) (2026-02-12) ·
[Cache-aware disaggregated inference (CPD)](https://www.together.ai/blog/cache-aware-disaggregated-inference) (2026-03-04) ·
[Inside the Together AI kernels team](https://www.together.ai/blog/inside-the-together-ai-kernels-team) (2026-04-01) ·
[Distribution-aware speculative decoding](https://www.together.ai/blog/distribution-aware-speculative-decoding) (2026-04-24) ·
[Serving DeepSeek-V4: why million-token context is an inference-systems problem](https://www.together.ai/blog/serving-deepseek-v4-why-million-token-context-is-an-inference-systems-problem) (2026-05-11) ·
[99.9 % uptime for inference](https://www.together.ai/blog/99-9-uptime-for-inference) (2026-07-16) ·
[The production platform for open-weight AI inference](https://www.together.ai/blog/the-production-platform-for-open-weight-ai-inference) (2026-07-23) ·
[Autoscaling endpoints for LLM inference](https://www.together.ai/blog/autoscaling-endpoints-for-llm-inference) (2026-07-31) ·
[Accelerate inference for large-scale workloads](https://www.together.ai/blog/accelerate-inference-large-scale-workloads) (mod. 2026-09-10) ·
[Fastest inference for the top open-source models](https://www.together.ai/blog/fastest-inference-for-the-top-open-source-models) ·
[20 exaflops GPU clusters](https://www.together.ai/blog/20-exaflops-gpu-clusters) (2023-11-13) ·
[GB200 NVL72 cluster, 36k GPUs](https://www.together.ai/blog/nvidia-gb200-together-gpu-cluster-36k) ·
[pricing](https://www.together.ai/pricing) ·
[docs: batch inference](https://docs.together.ai/docs/batch-inference)

**Fireworks AI** — [Fireworks quantization](https://fireworks.ai/blog/fireworks-quantization) (2024-08-01) ·
[FireOptimizer](https://fireworks.ai/blog/fireoptimizer) (2024-08-30) ·
[Multi-LoRA](https://fireworks.ai/blog/multi-lora) (2024-09-18) ·
[FireAttention V4: FP4 on B200](https://fireworks.ai/blog/fireattention-v4-fp4-b200) (2025-05-28) ·
[3D FireOptimizer](https://fireworks.ai/blog/3d-fireoptimizer) (2025-06-14) ·
[Batch API](https://fireworks.ai/blog/batch-api) (2025-07-31) ·
[Fireworks–AMD infrastructure partnership](https://fireworks.ai/blog/fireworks-amd-ai-infrastructure-partnership) (2025-10-20) ·
[Blazing fast inference on top OSS models](https://fireworks.ai/blog/blazing-fast-inference-on-top-oss-models) (2026-01-27) ·
[Inference providers vs API routers](https://fireworks.ai/blog/inference-providers-vs-api-routers) (2026-03-06) ·
[Kernel optimization for MiniMax M3 on Blackwell](https://fireworks.ai/blog/kernel-optimization-for-minimax-m3-on-nvidia-blackwell) (2026-07-10) ·
[Why GPUs on demand](https://fireworks.ai/blog/why-gpus-on-demand) (2024-06-03) ·
[pricing](https://fireworks.ai/pricing) ·
[docs: serverless pricing](https://docs.fireworks.ai/serverless/pricing) ·
[docs: on-demand deployments](https://docs.fireworks.ai/guides/ondemand-deployments) ·
[docs: autoscaling](https://docs.fireworks.ai/deployments/autoscaling)

**DeepInfra / Novita** — [DeepInfra pricing](https://deepinfra.com/pricing) ·
[DeepInfra docs](https://docs.deepinfra.com/) ·
[Novita pricing](https://novita.ai/pricing) ·
[Novita blog](https://novita.ai/blog) ·
[Chord W4A16 INT4 MoE kernels](https://novita.ai/blog/novita-chord-w4a16-moe/) (2026-09-15) ·
[Kimi DSpark speculative decoding in vLLM](https://novita.ai/blog/kimi-k2-dspark-speculative-decoding-throughput/) (2026-07-10) ·
[Optimizing GLM4-MoE for production with SGLang](https://novita.ai/blog/optimizing-glm4-moe-for-production/) (2026-01-21)

**Groq** — [The Groq LPU explained](https://groq.com/blog/the-groq-lpu-explained) (⚠️ undated) ·
[Why AI requires a new chip architecture](https://groq.com/blog/why-ai-requires-a-new-chip-architecture) (2019-10-22) ·
[From speed to scale: optimized for MoE and other large models](https://groq.com/blog/from-speed-to-scale-how-groq-is-optimized-for-moe-other-large-models) ·
[Batch processing with GroqCloud](https://groq.com/blog/batch-processing-with-groqcloud-for-ai-inference-workloads) (2025-03-13) ·
[GroqCloud expanding to meet demand](https://groq.com/blog/groqcloud-expanding-to-meet-demand) ·
[Groq and NVIDIA licensing agreement](https://groq.com/newsroom/groq-and-nvidia-enter-non-exclusive-inference-technology-licensing-agreement-to-accelerate-ai-inference-at-global-scale) (2025-12-24) ·
[Groq brings NVIDIA Groq 3 LPX and Vera Rubin NVL72 to market](https://groq.com/blog/groq-among-the-first-to-bring-nvidia-groq-3-lpx-and-vera-rubin-nvl72-to-market) (2026-08-24) ·
[console: supported models](https://console.groq.com/docs/models) ·
[console: batch](https://console.groq.com/docs/batch) ·
[console: Qwen3.8-27B](https://console.groq.com/docs/model/qwen/qwen3.8-27b) ·
❌ `groq.com/pricing` renders client-side; ❌ `console.groq.com/docs/truepoint` 404; ❌ ISCA-2020 TSP PDF 404

**Cerebras** — [Introducing Cerebras Inference](https://www.cerebras.ai/blog/introducing-cerebras-inference-ai-at-instant-speed) (2024-08-27) ·
[Cerebras inference 3× faster](https://www.cerebras.ai/blog/cerebras-inference-3x-faster) (2024-10-24) ·
[MoE guide: scale](https://www.cerebras.ai/blog/moe-guide-scale) (2025-09) ·
[Disaggregated inference with AWS Trainium](https://www.cerebras.ai/blog/disaggregated-inference) (2026-03-13) ·
[Hot Chips 2026 deep dive](https://www.cerebras.ai/blog/ultrafast-frontier-inference-cerebras-deep-dive-at-hot-chips-2026) (2026-08-25) ·
[Serving GPT-5.6 Sol at up to 750 tok/s](https://www.cerebras.ai/blog/how-cerebras-serves-gpt-5-6-sol-at-up-to-750-tokens-per-second) (2026-08-27) ·
[Multi-LoRA on Cerebras Inference](https://www.cerebras.ai/blog/introducing-multi-lora-on-cerebras-inference) ·
[model catalog](https://inference-docs.cerebras.ai/models/overview) ·
[Qwen 3.8 27B](https://inference-docs.cerebras.ai/models/qwen-3.8-27b) ·
[GPT-OSS](https://inference-docs.cerebras.ai/models/openai-oss) ·
[rate limits](https://inference-docs.cerebras.ai/support/rate-limits) ·
[prompt caching](https://inference-docs.cerebras.ai/capabilities/prompt-caching) ·
[dedicated endpoints](https://inference-docs.cerebras.ai/dedicated/overview) ·
[pricing](https://www.cerebras.ai/pricing)

**SambaNova** — [SN40L: scaling the AI memory wall with dataflow and composition of experts](https://arxiv.org/abs/2405.07518) (MICRO 2024) ·
[SambaCloud pricing](https://cloud.sambanova.ai/plans/pricing) ·
[supported models](https://docs.sambanova.ai/cloud/docs/get-started/supported-models)

**Perplexity** — [pplx-kernels](https://github.com/perplexityai/pplx-kernels) ·
[pplx-garden](https://github.com/perplexityai/pplx-garden) ·
❌ `perplexity.ai/hub/blog/*` and `research.perplexity.ai/*` — HTTP 403 (Cloudflare) to every user-agent tried

**OpenRouter** — [provider routing](https://openrouter.ai/docs/features/provider-routing) ·
[uptime optimization](https://openrouter.ai/docs/features/uptime-optimization) ·
[FAQ (fees, logging)](https://openrouter.ai/docs/faq) ·
[models list](https://openrouter.ai/api/v1/models) ·
endpoint price/quant/uptime data:
[deepseek-v4.1-flash](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints) ·
[qwen3.8-27b](https://openrouter.ai/api/v1/models/qwen/qwen3.8-27b/endpoints) ·
[kimi-k3](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints) ·
[gpt-oss-120b](https://openrouter.ai/api/v1/models/openai/gpt-oss-120b/endpoints)

**First-party model operators** — [DeepSeek V3/R1 inference system overview](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) (2025-03) ·
[Mooncake: a KVCache-centric disaggregated architecture for LLM serving](https://arxiv.org/abs/2407.00079) (v1 2024-06-24, v4 2025-09-03) ·
[Mistral self-deployment: vLLM](https://docs.mistral.ai/deployment/self-deployment/vllm/) ·
[Anthropic: batch processing](https://platform.claude.com/docs/en/docs/build-with-claude/batch-processing) ·
[Anthropic: service tiers](https://platform.claude.com/docs/en/api/service-tiers) ·
[OpenAI: Batch API](https://developers.openai.com/api/docs/guides/batch) ·
[OpenAI: flex processing](https://developers.openai.com/api/docs/guides/flex-processing) ·
[Google Vertex AI: Provisioned Throughput overview](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/overview) — ⚠️ numeric tables not retrievable

### 8.3 Repo cross-references (not external sources)

[`../METHODOLOGY.md`](../METHODOLOGY.md) §2 KV budget, §3 fit, §4 roofline, §6
cost, §8 pinned inputs ·
[`../README.md`](../README.md) §3, §6 open questions ·
[`../matrix/cost-matrix.md`](../matrix/cost-matrix.md) §1–§4, §6.1, §6.2, §9.2 ·
[`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) §2, §6.1, §6.3 ·
[`../matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md) ·
[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) §1–§5 ·
[`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) ·
[`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) ·
[`../cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md) ·
[`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §5.14 ·
[`../gpus/`](../gpus/), [`../models/`](../models/) ·
scaling siblings [`01`](01-bare-metal-cluster.md)–[`09`](09-reference-architectures.md)

---

## Verification log (2026-09-19)

| Item | Status |
|---|---|
| **Wafer company identity** | **Resolved.** `wafer.ai` = `usewafer.com` = "Wafer \| Continual Inference", the inference company. `wafer.systems` is an unrelated company of the same name (consumer OS/assistant, "© 2026 Wafer, Inc"). Four other candidate domains do not resolve. Method: HTTP-probed 8 domains, compared page content and analytics IDs. |
| **fal B200 = 192 GB** | **Conflict, unresolved, not propagated.** fal's machine-types doc says 192 GB; [`../METHODOLOGY.md` §8](../METHODOLOGY.md) pins 180 GB per [`../gpus/b200.md` §2](../gpus/b200.md). Baseten and RunPod both list 180 GB. No repo number recut. Open question 22. |
| **fal RTX PRO 6000 = 1.8 TB/s** | **Conflict, explained.** 1.8 TB/s is the *Workstation* edition (1,792 GB/s); [`../gpus/rtx6000-pro.md`](../gpus/rtx6000-pro.md) pins the Server Edition at 1,597 GB/s. Not propagated. |
| **Modal scheduler formulation** | **Conflict, unresolved.** LP/GLOP (2025) vs MIP-every-minute (2024). Both cited inline. Open question 21. |
| **"Modal's runtime is in Rust"** | **Downgraded to inferred.** Only evidence is `tokio` in `seccheck` code samples. Open question 9. |
| **Cog implementation language** | **Partially resolved.** README states a Rust/Axum HTTP server; the CLI is Go. GitHub languages API rate-limited. Open question 30. |
| **Cloudflare Neuron pricing** | **Change recorded.** Launch post (2023) had two tiers ($0.01/1k RTN, $0.125/1k FTN); current docs have a single $0.011/1k. Both cited. |
| **Lambda Inference API** | **Status change recorded.** Wind-down stated on the product page; docs path now redirects. |
| **Groq silicon** | **Status change recorded, consequence flagged.** NVIDIA licensing (2025-12-24) then NVIDIA Groq 3 LPX + Vera Rubin NVL72 deployment (2026-08-24). LPU share of current serving not published. Open question 19. |
| **Fireworks DeepSeek-V4.1-Flash price** | **Conflict, unresolved.** $0.22/$0.66 (OpenRouter) vs $0.30/$1.20 (own docs). Both rows printed in §5.1. Open question 20. |
| **§5 break-even arithmetic** | **Validated against the repo.** Regenerating [`../matrix/cost-matrix.md` §6.2](../matrix/cost-matrix.md) cells with this document's generator gives DeepSeek-V4.1-Flash on B200 = 91.6 % (matrix prints 92 %) and Qwen3.8-27B on B300 = 6.3 % / 12.8 % (matrix prints 6 % / 13 %). |
| **Every performance number in §3–§5** | **Vendor-published and vendor-selected.** No independent benchmark located. Open question 15. |
| **WebSearch coverage (§3A half)** | **Budget exhausted (200/200) before that research began.** Substituted sitemap/`llms.txt` crawling, raw-`.md` fetching and bulk URL probing; 85 distinct primary URLs fetched for that half. Open questions 15, 29. |
| **Perplexity blog, Vertex GSU tables** | **Retrieval failed, recorded not filled.** Open questions 27, 28. |
| **Sibling scaling docs** | `01`–`09` all present on disk at merge time and linked by section; the earlier drafts' note that `02`, `03` and `08` were absent no longer applies. |

---

## Verification log (2026-09-19) — adversarial fact-check pass

A second, adversarial pass over this document. The most consequential claims
were selected — published cold-start times, throughput and latency numbers,
every GPU-hour price, hardware and engine attributions, inferences presented as
fact, and the Wafer identification — and each was taken back to its primary
source. **38 claims were checked.** The §5 pricing-vs-self-hosting arithmetic
was regenerated from scratch in `python3` against
[`../matrix/cost-matrix.md`](../matrix/cost-matrix.md).

**Result: 21 confirmed, 9 corrected, 8 unverifiable.** Nothing was removed.
Three further presentation defects found inside otherwise-confirmed claims (a
paraphrase printed as a quotation, an occupancy typo, a rounded ratio) were
also fixed and are listed unnumbered in the corrections table.

**⚠️ Tooling limit, again.** The WebSearch budget was exhausted (200/200)
before this pass began, so verification was done by direct `WebFetch` of
primary URLs only. Claims whose only source is a search result or a page that
404s or blocks fetching are recorded as **UNVERIFIABLE**, not as false.

### Confirmed against the primary source

| # | Claim | Source | Note |
|---|---|---|---|
| 1 | **Wafer identity** — `wafer.ai` is the inference-optimisation company; "2T+ tokens", "230 → 315 → 400 tok/s", *Find Bottlenecks → Try Many Paths → Ship Measured Winner*, Dedicated + $500/1:1-to-$10,000 startup credits | [wafer.ai](https://wafer.ai) | Every headline counter reproduced verbatim. The §3A.3 identification stands. |
| 2 | **Wafer Series A** — $40M, 2026-09-01, co-led Marathon + Chemistry; Wing, AMD Ventures, Outset, Fifty Years, YC; the named angels | [wafer.ai/blog/series-a](https://wafer.ai/blog/series-a) | Exact. |
| 3 | **Wafer KDA kernel 11.65×** over `torch.compile`, 64 blocks on a 145-SM GPU, **6.25 %** occupancy, register vectorisation + manual unrolling, 2026-01-30 | [wafer.ai/blog/profile-guided-optimization](https://wafer.ai/blog/profile-guided-optimization) | Source adds **0.04 waves/SM**. A "11.65 %" typo in §3A.3(k) was fixed to 6.25 %. |
| 4 | **Modal ~2,000 s → ~50 s, "40× faster"** | [modal.com/blog/truly-serverless-gpus](https://modal.com/blog/truly-serverless-gpus) | Confirmed. Post also reports ~35M CPU and ~15M CPU+GPU snapshot restores Feb–Apr 2026, and Reducto ~70 s → ~12 s. |
| 5 | **Modal snapshots** — vLLM/Qwen2.5-0.5B **45 s → 5 s**, Parakeet **20 s → 2 s**, ViT **8.5 s → 2.25 s**; CUDA checkpoint API on driver branches **570/575** | [modal.com/blog/gpu-mem-snapshots](https://modal.com/blog/gpu-mem-snapshots) | Verbatim, including the P0 qualifier. |
| 6 | **Modal pricing** H100 $3.95 / H200 $4.54 / B200 $6.25 / B300 $7.10 / A100 $2.50 per hour; CPU and RAM billed separately | [modal.com/pricing](https://modal.com/pricing) | Every cell exact to the per-second rate. |
| 7 | **Baseten BDN** — **>2 GB/s** onto H100 nodes, **2–3×** faster cold starts, **50 replicas of a 140 GB model = 1× model size of origin bandwidth**, NVMe → hash-ring peer cache → mirrored origin | [baseten.co/blog/…bdn…](https://www.baseten.co/blog/how-the-baseten-delivery-network-bdn-makes-cold-starts-fast/) | Exact. Mirroring reaches 1–5 GB/s. |
| 8 | **Baseten** PD disagg **up to 6× TPS/GPU**; KV-aware routing **34–62 %**; **−50 % TTFT / −34 % TPOT / +61 % throughput**; TRT-LLM + SGLang + vLLM under Dynamo | [baseten.co/blog/nvidia-dynamo-day…](https://www.baseten.co/blog/nvidia-dynamo-day-baseten-inference-stack/) | Every number reproduced. |
| 9 | **Baseten pricing** H100 $0.10833/min ($6.50/h), MIG-40 $3.75/h, B200 $9.98/h, A100 $4.00/h, billed per minute | [baseten.co/pricing](https://www.baseten.co/pricing/) | Exact. |
| 10 | **Together's autoscaling experiment** — Qwen3.5-9B on 1×H100, bounds 1–3; `inflight_requests` 8 reacted and lowered p95; `ttft` p95 300 ms never fired; `gpu_utilization` 75 % never crossed; `ceil(N × observed/target)`; up 60 s / down 300 s; *"There is no scale-to-zero-with-automatic-wake"* | [together.ai/blog/autoscaling-endpoints…](https://www.together.ai/blog/autoscaling-endpoints-for-llm-inference) | The single best-evidenced finding in §1. Confirmed in full. |
| 11 | **Together CPD** — pre-prefill / prefill / decode, **+35–40 % sustainable QPS**; 1.1–1.15 vs 0.75–0.8 QPS/GPU | [together.ai/blog/cache-aware-disaggregated-inference](https://www.together.ai/blog/cache-aware-disaggregated-inference) | Exact. |
| 12 | **Together pricing** — dedicated H100 **$3.99** promo (from $5.49), B200 $8.99; clusters H100 3.99 / H200 5.99 / B200 8.19; reserved H100 $3.69 → $3.19 by term | [together.ai/pricing](https://www.together.ai/pricing) | Confirmed. A100 $2.40–2.59 is **not** on the current page — flagged ⚠️ in §2.3. |
| 13 | **Cloudflare Infire vs vLLM 0.10.0** on H100 NVL — **40.91 vs 38.38 req/s** (+6.6 %), **17,224 vs 16,164 tok/s**, **25 % vs 140 % CPU** (−5.6×); gVisor 250 % | [blog.cloudflare.com/…inference-engine](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine) | Exact, including the gVisor row quoted in §1's "what does not transfer". |
| 14 | **Cloudflare Neurons $0.011/1k**, 10,000 free/day, per-token equivalents published per model | [developers.cloudflare.com/workers-ai/platform/pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) | Exact. |
| 15 | **Cerebras disaggregated inference** — AWS Trainium for prefill, CS-3 for decode; **5×** throughput, **up to 4.5× p95** on agentic workloads, **1,200 tok/s** | [cerebras.ai/blog/disaggregated-inference](https://www.cerebras.ai/blog/disaggregated-inference) | Confirmed. The 1,200 tok/s figure is for **GPT-Codex-5.3-Spark**, against a 50 tok/s aggregated baseline — a named model this document did not state. |
| 16 | **Cerebras dual-bucket rate limits** — a separate **uncached TPM** and **total TPM**, pre-screened against `max_completion_tokens` | [inference-docs.cerebras.ai/support/rate-limits](https://inference-docs.cerebras.ai/support/rate-limits) | Confirmed, and **total TPM defaults to 3× the uncached limit** — added to §2.1. |
| 17 | **DeepSeek V3/R1 inference system** — EP32 prefill (DP32, 4 nodes) / EP144 decode (DP144, 18 nodes); 226.75 avg / 278 peak nodes; 608B input, 342B cache hits (**56.3 %**), 168B output; **73.7k** prefill / **14.8k** decode tok/s/node; 20–22 tok/s/user; $87,072/day at $2/H800-hour vs $562,027 theoretical → **545 %** | [open-infra-index day 6](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) | Every number exact. The most heavily reused source in this document survives intact. |
| 18 | **Mooncake** — *"up to a 525 % increase in throughput in certain simulated scenarios while adhering to SLOs"*; *"75 % more requests"* in production; disaggregated KVCache over *"underutilized CPU, DRAM, and SSD resources"*; prediction-based early rejection | [arXiv 2407.00079](https://arxiv.org/abs/2407.00079) | Verbatim. |
| 19 | **SambaNova SN40L** — **15–31×** model switching, **up to 19×** footprint reduction, **3.7×** vs DGX H100 (6.6× vs DGX A100), **2–13×** overall; SRAM / HBM / DDR three-tier | [arXiv 2405.07518](https://arxiv.org/abs/2405.07518) | Verbatim. |
| 20 | **HF Inference Endpoints** — default **80 % accelerator utilisation over 1 min**, scale-up eval 1 min / scale-down 2 min / **300 s** stabilisation, scale-to-zero after **15 min** idle, **502** during replica init; H100 $10/h (GCP), H200 $5/h (AWS), A100 $2.50/h (AWS); **initialization is billed** | [pricing](https://huggingface.co/docs/inference-endpoints/en/pricing), [autoscaling](https://huggingface.co/docs/inference-endpoints/en/autoscaling) | All confirmed. The quotation in §1 item 5 was a paraphrase printed as a quote — replaced with the real sentence. |
| 21 | **Anthropic Priority Tier** — commitment = ITPM + OTPM + duration (1/3/6/12 months) + specific model version; *"targets 99.5 % uptime"*; burndown cache read **0.1**, cache write **1.25** (5-min) / **2.00** (1-hour), US-only **1.1** on Claude 4.6+; **no longer available for purchase** | [platform.claude.com/docs/en/api/service-tiers](https://platform.claude.com/docs/en/api/service-tiers) | §3B.13 is exact, including the withdrawal notice. |
| — | **Groq batch** — 50 %, **24 h – 7 days**, ≤50,000 lines, 200 MB JSONL, 30-day retention | [console.groq.com/docs/batch](https://console.groq.com/docs/batch) | Confirmed except the "(raised from 25 %)" parenthetical — see UNVERIFIABLE. |
| — | **Beam does not bill machine wait or image pull** — *"We only charge for the time to load your application code. We don't charge for the time to spin up a server or load your container image."* | [beam.cloud/pricing](https://www.beam.cloud/pricing) | Confirmed verbatim. The §1 item 12 incentive argument stands. |
| — | **fal pricing** — H100 $4.50 list / **$1.89** discounted, H200 $4.50/$2.10, B200 $6.25/$3.49, B300 $8.50/$4.49 | [fal.ai/pricing](https://fal.ai/pricing) | Exact. The page now lists **B200 as 180 GB**, which quietly resolves the 192 GB conflict in the first verification log in this repo's favour. RTX PRO 6000 $2.99/$1.10 (96 GB) is new and not yet in §2.3. |
| — | **Fireworks on-demand** H100 $8.00, H200 $8.00, B200 $13.00, B300 $15.00, GB300 $20.00; region-restricted deployments at **1.5×** | [fireworks.ai/pricing](https://fireworks.ai/pricing) | Prices exact. |
| — | **DeepInfra dedicated** H100 **$2.20**, H200 $2.69, A100 $0.89, minute granularity | [deepinfra.com/pricing](https://deepinfra.com/pricing) | Confirmed, and **B200 $3.69 / B300 $4.89** recovered and added to §2.3. The 0.67–0.69× ratio in §2.3 note 3 recomputes correctly. |
| — | **Lambda Inference API is winding down** | [lambda.ai/inference](https://lambda.ai/inference) | *"As the Inference API winds down…"* — confirmed verbatim. No end date published. |
| — | **Every price in §5.1** (20 OpenRouter endpoints) and the four §5.2 rows checkable — Darkbloom, DeepInfra, Venice, Mancer 2 — reproduce exactly, including declared quantizations | [OpenRouter endpoints API](https://openrouter.ai/docs/features/provider-routing) | See the arithmetic section below. |

### Corrected in place

| # | What was wrong | What it is | Where fixed |
|---|---|---|---|
| **C1** | **"Serverless costs ~20 % over rental"**, printed three times as a headline market signal | **20 % *or* 37 %, depending on which RunPod page you read.** The serverless docs rate table gives H100 $4.18/h; [runpod.io/pricing](https://www.runpod.io/pricing) gives **$4.79/h** — against the same $3.49 Pod rate that is +20 % or **+37 %**. H200 $5.58 vs **$5.93**, A100 $2.74 vs **$2.72**. The Pods column reproduced exactly on both sources. This is a genuine vendor self-contradiction, not a transcription error, and the document had silently taken the lower branch. | §2.1, §2.3 row + note 2, §3A.5(b)(i), §5.6 point 2 |
| **C2** | **Beam H100 $3.63/h, A100-80 $1.49/h** | Stale by ~2×. Live rate card: **on-demand H100 PCIe from $1.83/h, H200 SXM5 $2.09, A100-80 $1.36, L40S $0.76, RTX 5090 $0.72, RTX 4090 $0.44**; **serverless H100 PCIe $0.000972/s = $3.50/h**. The old figures came from a sample `beam machine list` output pasted into the docs. | §2.1, §2.3, §3A.7(b) |
| **C3** | **"Datacenter parts … are on-demand reservations only, not serverless"** (Beam) | Contradicted by Beam's own pricing page, which lists **H100 PCIe under Serverless**. Downgraded to ⚠️ unconfirmed. This weakens — but does not void — the §3A.7 note that Beam cannot serve this repo's large MoE models serverlessly; that still holds on VRAM grounds. | §2.1, §3A.7(b) |
| **C4** | **"The 3.7× spread across identical model IDs"** (§5.1 note 3) | **3.26×.** $0.5184 ÷ $0.1591 = 3.258. No pair of rows in that table produces 3.7×. | §5.1 note 3 |
| **C5** | **DeepInfra `bf16` at $0.037/$0.17 marked "cheapest overall"** on gpt-oss-120b | It is not. **AkashML, CoreWeave and DekaLLM are all at $0.030 input** — listed one row *above* it in the same table. | §5.3 table |
| **C6** | **"a 9.5× input spread within one slug"** (gpt-oss-120b) | **11.7×.** $0.350 (Cerebras) ÷ $0.030 (the actual floor) = 11.67. The 9.5× was measured against DeepInfra's $0.037 rather than the cheapest endpoint. | §5.3 |
| **C7** | **DeepInfra high-tier break-even printed as 279 %** | **278 %.** $0.443 ÷ $0.1591 = 278.4 %. Off-by-one rounding, propagated into the §5.5 summary. | §5.1, §5.5 |
| **C8** | **Wafer's Kimi-K3 MI355X result presented as a throughput finding** — "952 tok/s/node, 48 vs 33 tok/s/\$" | The comparison is **entirely a price win**. On the same benchmark the **B300 (TP8+DCP8) is faster: 1,568 tok/s/node and 172 tok/s single-stream** vs the MI355X's 952 / 118. The tok/s/\$ ratio rests on an **assumed $2.50/GPU-hour for MI355X against $6.00 for B300** and the post's *"2.4× cheaper per GPU on average versus a B300"*. Omitting the B300 throughput inverts the reading for anyone choosing hardware. Both halves restored. | §1 item 15, §3A.3(f) table |
| **C9** | **"Fireworks makes [PD disaggregation] an axis of its deployment-shape search"**, cited to the 3D FireOptimizer post and presented as fact | The post names its three axes as **speed, throughput (cost) and quality** — not prefill/decode placement. Downgraded to *(inferred)* with the discrepancy stated. The §2.4 "yes — 3D FireOptimizer axis" cell carries the same caveat. | §1 item 2, §2.4 |
| — | **HF autoscaling quotation** | *"GPU utilization may drop below threshold during model loading"* was a paraphrase printed inside quotation marks. Replaced with the docs' actual sentence. The substance was right. | §1 item 5 |
| — | **"The 11.65 % → 11.65× story"** (Wafer occupancy) | Typo for **6.25 %**. | §3A.3(k) |
| — | **"Cerebras … output price within 1.5× of its input price"** | 1.49 ÷ 0.99 = **1.51×**. Softened to "~1.5×" with the figure shown. | §1 item 14 |

### Unverifiable — recorded, not filled in

| Claim | Why |
|---|---|
| **Groq's move to NVIDIA silicon** — "NVIDIA Groq 3 LPX + Vera Rubin NVL72 (2026-08-24)", and the **3,400 tok/s Gemma 4 31B** figure attributed to it | The only route to the licensing announcement and the deployment post is a web search, and the search budget was exhausted before this pass. `groq.com` does reference **"LPX"** alongside "LPU", which is weak corroboration of the product name and nothing more. `groq.com/pricing` served no price or speed table to `WebFetch`. **This is the single most consequential unverified claim in the document**: §2.1, §3B.5 and §4 all reason from Groq's silicon, and if the transition is real the LPU-derived conclusions have an expiry date. Open question 19 stands and is upgraded in importance. |
| **Groq per-stream speeds** — 500 tok/s gpt-oss-120b, 1,000 tok/s gpt-oss-20b, ~450 tok/s on the 27B | Not retrievable from `groq.com`. Groq's **prices** on gpt-oss-120b ($0.15/$0.60) were confirmed via OpenRouter; the speeds were not. |
| **Groq batch "(raised from 25 %)"** | Current batch doc states only the 50 % discount. The historical 25 % is not on any fetched page. |
| **Cloudflare Omni internals** — ~156 ms swap-in for a 5 GB model, **13 models/GPU**, **~400 % memory over-commit**, the CUDA-stub / unified-memory / FUSE `/proc/meminfo` mechanism | Both candidate blog URLs return **404**. These numbers underpin §2.1's Cloudflare row and §1's co-tenancy argument. The *Infire* numbers in the same row are separately confirmed. |
| **fal DSpark** — acceptance length 4.6, 830–1,000 tok/s, 16× throughput; and the Ideogram v4 2.75 s → 0.44 s figure | `blog.fal.ai/dspark` returns **404**. Used in §1 item 8 and §2.4. |
| **Fireworks quantization ladder** — L1–L4 and the **KLD < 0.007** acceptance bar (L1 0.00286 → L4 0.00796) | `docs.fireworks.ai/guides/quantization` returns **404**. This is the most specific quality-gate number in the document and it is currently unsupported by a reachable source. |
| **Fireworks AMD hardware** — MI325X / MI350X in the fleet | Not on the on-demand pricing page, which lists NVIDIA parts only. |
| **OpenRouter "~447 models"** | The `/models` endpoint paginates; a single fetch returns a partial page. Not re-counted. |
| **Cerebras CS-4 at 53.5 PB/s** | The Hot Chips 2026 citation in §3B.6 was not re-fetched; the current chip page describes CS-4 as *"powered by three WSE-3T processors"* and restates only 900k cores / 4T transistors / 250 PFLOPS. The 44 GB / 21 PB/s WSE-3 figures were not contradicted anywhere. Flagged in §2.1. |
| **Perplexity blog; Vertex Provisioned Throughput GSU tables** | Unchanged from the first verification log — still 403 and still navigation-shell-only. Open questions 27, 28. |

### §5 arithmetic, regenerated in `python3`

Every blended price and every break-even percentage in §5.1, §5.2 and §5.4 was
recomputed from the raw per-token prices against
[`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output)'s
self-hosted cells. **All rows reproduce to the printed precision**, with the
three exceptions already listed (C4, C6, C7).

Two conventions were recovered by inspection rather than from the text, and
both are now stated in §5.2:

- **The blend is not one formula, it is two.** Rows with a published cached
  rate use METHODOLOGY's `0.375 × in + 0.375 × cached + 0.25 × out`; rows
  without one use `0.75 × in + 0.25 × out` — all input charged uncached. That
  is the right treatment (no cache product, no cache discount) but it makes
  Cerebras, Groq, Venice, Darkbloom and Mancer 2 **not** arithmetically
  comparable to the cached rows, and it is most of why the custom-silicon
  vendors look expensive in §5.2. Reading those rows as like-for-like
  overstates the case against custom silicon.
- **§5.2's "19 endpoints" counts three rows that are not OpenRouter
  endpoints** — Cerebras and Groq do not serve that slug on OpenRouter (their
  prices are first-party), and "Qwen Cloud list" is the repo's denominator.
  OpenRouter returns 18 endpoints for the slug, 16 of which appear. Per-row
  arithmetic is unaffected.

Cross-checks against the matrix that **held**:

| Check | Recomputed | Matrix prints |
|---|---|---|
| DeepSeek-V4.1-Flash, B200 low, vs DeepSeek $0.2074 | 91.6 % | 92 % |
| DeepSeek-V4.1-Flash-NVFP4, B200 low | 71.6 % | 72 % |
| Qwen3.8-27B, B300, vs Qwen Cloud $0.9563 | 6.3 % / 12.8 % | 6 % / 13 % |
| Kimi-K3, B300, vs Moonshot $4.9875 | 47.7 % / 96.8 % | 48 % / 97 % |
| Kimi-K3, B300, vs Relace $2.8262 | 84.2 % / 170.8 % | 84 % / 171 % |
| §5.1 endpoint counts — 20 of 23 clear 100 % on the low tier, 3 on the high tier | 20 / 3 | 20 / 3 |
| §2.3 H100 spread $1.89 → $10.00 | 5.29× | "~5×" |
| §2.3 DeepInfra vs cheapest on-demand | 0.688× / 0.674× | "0.67–0.69×" |
| §5.6 point 3 — Baseten $1.20 vs DeepSeek $0.60 output | 2.00× | "~2×" |
| §4.3 Cerebras out÷in | 1.51× and 2.14× | "1.5–2.1×" |
| §1 item 14 — Cerebras input vs GPU market | 6.6× (vs DeepInfra) / 9.9× (vs Darkbloom) | "6.6–10×" |

### What this pass did not change

The document's **structural conclusions all survived**: prefill/decode
disaggregation as the one universal architectural answer (§1 item 2 — five
independent sources re-confirmed), concurrency over utilisation as the
autoscaling signal (§1 item 5 — Together's controlled experiment confirmed in
full), snapshotting as the endgame of cold start (§1 item 3 — Modal's chain
confirmed number by number), and §5's verdict that self-hosting wins on the
dense 27B and loses on the large MoE checkpoints. The nine corrections are
concentrated in **published prices that moved or that vendors publish
inconsistently**, and in **two places where an inference was printed as a
fact** (C8, C9). That distribution is itself a finding: the vendor-published
*engineering* numbers in this document held up almost perfectly, and the
vendor-published *prices* did not.
