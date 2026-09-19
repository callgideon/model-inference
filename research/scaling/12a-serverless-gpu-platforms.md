# Serverless GPU and GPU-cloud platforms — how commercial inference providers build their stacks

Research date: **2026-09-19**. Conventions and the `[src]` / ⚠️ legend are in
[`../METHODOLOGY.md`](../METHODOLOGY.md#legend).

**Who this is for.** You run LLMs on your own bare-metal GPU cluster. This
document reverse-engineers what fifteen commercial inference providers actually
built — schedulers, container runtimes, snapshot systems, weight-delivery
networks, serving engines, autoscalers, billing models — and says, per field,
what transfers to a single-tenant on-prem cluster and what is an artefact of
being a multi-tenant public cloud.

**Related documents — read these first, they are not repeated here.**

| Topic | Document |
|---|---|
| Node/rack hardware, fabric, OS, orchestration, weight distribution on bare metal | [`01-bare-metal-cluster.md`](01-bare-metal-cluster.md) |
| Utilisation metrics, cluster-level batching, memory tiering for KV | [`04-throughput-and-utilization.md`](04-throughput-and-utilization.md) |
| Autoscaling signals, Kubernetes mechanisms, predictive scaling, scale-down | [`05-autoscaling-and-predictive-scaling.md`](05-autoscaling-and-predictive-scaling.md) |
| Cold-start anatomy, weight-load fast paths, compile caches, snapshot/restore | [`06-cold-start.md`](06-cold-start.md) |
| Fleet cost model, utilisation levers in cost terms, FinOps loop | [`07-cost-engineering.md`](07-cost-engineering.md) |
| Published production architectures, open-source stacks, blueprints | [`09-reference-architectures.md`](09-reference-architectures.md) |
| Engine support matrix (vLLM / SGLang / TRT-LLM per GPU and model) | [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) |
| Prefix/KV caching, speculative decoding, parallelism, PD disaggregation | [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) |
| NVFP4 / MXFP4 / FP8 hardware acceleration per GPU | [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) |
| Attention kernels per architecture | [`../cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md) |
| GPU-hour prices used for every cost comparison below | [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) |

⚠️ `scaling/02`, `scaling/03` and `scaling/08` did not exist on disk when this
document was written (2026-09-19); cross-references to them are deliberately
absent rather than guessed.

---

## 0. Executive summary

Fifteen providers, one page.

1. **Nobody's moat is the GPU.** Every provider in Part A rents or resells the
   same NVIDIA silicon this repo's [`../gpus/`](../gpus/) docs cover. The
   differentiation is entirely in (a) how fast a replica becomes ready, (b) how
   tightly supply tracks demand, and (c) how many tokens per second the serving
   engine extracts. All three transfer to bare metal; only (b) is
   fundamentally cheaper for them than for you, and only because they pool
   uncorrelated demand.
2. **Cold start is the product.** Modal, Baseten, RunPod, Beam and Cerebrium
   each independently built the same three-layer answer: *(i)* a lazy or
   tiered content-addressed weight/image path, *(ii)* CPU — and now GPU —
   memory checkpoint/restore, *(iii)* a warm buffer you pay for. Modal
   publishes the full chain: ~2,000 s naïve → ~50 s, a claimed 40×
   [src](https://modal.com/blog/truly-serverless-gpus).
3. **GPU checkpoint/restore went from research to shipped product in ~18
   months.** Modal (alpha, Jul 2025), Beam (`checkpoint_enabled`) and Cerebrium
   (beta) all ship it; it rests on NVIDIA's CUDA checkpoint API in driver
   branches 570/575 [src](https://modal.com/blog/gpu-mem-snapshots). This is
   the single most transferable technique in this document — see
   [`06-cold-start.md` §4](06-cold-start.md).
4. **Two providers wrote their own serving engine and published numbers.**
   Cloudflare's **Infire** (Rust) beats vLLM 0.10.0 on an H100 NVL by ~6.5 % on
   requests/s while using **25 % CPU vs vLLM's 140 %**
   [src](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine).
   fal's **fal Inference Engine™** claims "up to 10× faster"
   [src](https://fal.ai/) and backs it with published CuTeDSL/CUTLASS kernel
   work. Everyone else composes vLLM / SGLang / TensorRT-LLM.
5. **Baseten is the closest published analogue to a self-hosted stack.** Their
   Inference Stack is TensorRT-LLM + SGLang + vLLM under NVIDIA Dynamo with
   NIXL and a KV Block Manager, plus an in-house Speculation Engine
   [src](https://www.baseten.co/blog/nvidia-dynamo-day-baseten-inference-stack/).
   Every component is open source or reproducible. Their published deltas — PD
   disaggregation up to **6× TPS/GPU**, KV-cache-aware routing **34–62 %** —
   are the best available third-party corroboration for
   [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md).
6. **Wafer is a different shape of company** and the most directly relevant to
   a bare-metal operator: it does not sell capacity, it sells *continual
   optimisation* of someone else's capacity, heavily on AMD. Its published
   MI355X numbers (Kimi-K3 at **952 tok/s/node**, **48 tok/s/$** vs **33** for
   B300 [src](https://wafer.ai/blog/kimi-k3-mi355x)) are one of the few public
   AMD-vs-Blackwell inference comparisons at this model scale.
7. **Per-second billing is universal; scale-to-zero is not free.** Every
   provider bills per second or per minute. The ones honest about it (Baseten,
   RunPod, Cerebrium) say plainly that scale-to-zero is for development and
   bursty work, and that latency-sensitive production wants a warm floor —
   which is exactly the `min_replicas > 0` conclusion in
   [`05-autoscaling-and-predictive-scaling.md` §5](05-autoscaling-and-predictive-scaling.md).
8. **Concurrency, not utilisation, is the scaling signal.** Every serverless
   platform that serves LLMs scales on queue depth or in-flight request count,
   not GPU utilisation. Hugging Face is the outlier still defaulting to 80 %
   accelerator utilisation and documents why it misbehaves during model load
   [src](https://huggingface.co/docs/inference-endpoints/en/autoscaling).

**What does not transfer:** multi-cloud arbitrage (Modal's LP solver, Baseten's
MCM across 20+ clouds), gVisor-grade sandboxing of untrusted tenants, and
spot/preemptible capital structure. On a single-tenant cluster you own, those
are cost centres with no matching revenue.

---

## 1. Method, and an explicit limitation

**Source policy.** Every factual claim carries `[src](url)` to a primary
document — the provider's own engineering blog, docs, pricing page, or repo —
or is marked **⚠️ TO BE VERIFIED**. Where a claim is my reading of docs,
pricing or product structure rather than something the provider stated, it is
labelled **(inferred)**. No internal detail is invented: where a provider does
not publish something (Modal's scheduler language, fal's engine internals,
Inferless's cold-start mechanism), the field says so.

**⚠️ Tooling limitation, stated because it affects coverage.** The task
specified ≥ 20 distinct `WebSearch` queries. **The session's web-search budget
was already exhausted (200/200) by earlier phases of this research program on
the first attempt**, so zero searches were available to this document. Source
discovery was done instead by:

- fetching each provider's `sitemap.xml` / `sitemap-posts.xml` and grepping it
  (this is how the Modal blog index, the Baseten blog index, the Cloudflare
  Infire and Omni posts, and the full fal docs tree were found);
- fetching `llms.txt` doc indexes (RunPod, Beam, Inferless, Cerebrium) and then
  the raw `.md` of each page — which is *more* faithful than a search snippet
  or a WebFetch summary, and is where the exact parameter names and default
  values in §2 come from;
- HTTP-probing candidate URLs in bulk to find live pages.

Net effect: **85 distinct primary URLs were fetched**. The coverage cost is
concentrated in one place — company identification for **Wafer** (§2.3), where
search would have resolved ambiguity faster; it was instead resolved by probing
eight candidate domains and comparing page content. Second-order effects:
third-party benchmarks, press coverage, conference talks not linked from a
sitemap, and job postings (all careers pages probed were client-rendered and
yielded no stack signal) are under-represented. Fields that would normally be
filled from those are marked ⚠️.

**Field schema.** Every profile in §2 uses the same eleven fields (a)–(k).

---

## 2. Provider profiles

### 2.1 Modal

The most thoroughly documented serverless-GPU architecture in public. Modal
publishes engineering detail at a level no competitor matches, which makes it
the reference implementation for §4's patterns.

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

### 2.2 fal (fal.ai)

The generative-media specialist. Unlike everyone else in Part A, fal claims a
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
| **The hot-tunable / code-versioned parameter split** (§2.2(e)) — a genuinely good config-design pattern for any in-house control plane. | **fal's model catalogue and licensing.** |
| **"SGLang over vLLM for low-batch"** as a concrete, dated data point for engine selection — corroborates [`../cross-cutting/inference-engines.md` §9](../cross-cutting/inference-engines.md). | |
| **Distillation as a serving optimisation** (CFG folding, timestep distillation, QAD): changing the model to cut forward passes beats optimising the pass. The LLM analogue is speculative decoding — see [`../cross-cutting/serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md). | |

---

### 2.3 Wafer — company identification first

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
| **Kimi-K3 (2.8T)** | **8× MI355X, TP8, SGLang** | **952 tok/s/node** aggregate, **118 tok/s** single-stream, **48 tok/s/$** vs **33 tok/s/$** for B300; **~3.8× the aggregate throughput per node** of a 2-node B200 TP16 setup. Workload 1,024 in / 400 out, speculative decoding on. Notes the model needs "over 1.5TB of VRAM *before* allocating a KV cache for 1M tokens of context" | 2026-07-31, Ian Ye [src](https://wafer.ai/blog/kimi-k3-mi355x) |
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
| **Occupancy as first check.** The 11.65 % → 11.65× story is "64 blocks on 145 SMs = 6.25 % occupancy." Cheap to check, frequently the whole answer. | |
| **The AMD data points.** If MI355X is on your shortlist, the Kimi-K3 **48 vs 33 tok/s/$** comparison against B300 is the most concrete public number available — cross-check against [`../gpus/mi355x.md`](../gpus/mi355x.md) and [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md). | |
| **Benchmark-integrity discipline** for any LLM-generated kernel you accept. | |

---

### 2.4 Baseten

The most complete published inference stack in Part A, and the closest analogue
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

### 2.5 RunPod

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
[src](https://www.runpod.io/pricing). The Serverless-vs-Pods spread (H100
$4.18/hr serverless vs $3.49/hr pod) is the premium for scale-to-zero and the
queue — about **20 %**, which is a useful market price for "someone else
absorbs your idle." (inferred)

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

### 2.6 Replicate

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

### 2.7 Beam (beam.cloud)

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
option — an explicit trigger (§2.10).

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

### 2.8 Inferless

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
cold-start story in Part A relative to its marketing category.

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

### 2.9 Northflank

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
provider in Part A that does not hide its substrate — which is precisely why it
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
Compare Cloudflare's Omni (§2.15), which solves the same problem with unified
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

### 2.10 Cerebrium

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

### 2.11 Lambda — Inference API

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

### 2.12 Nebius Token Factory

**(a) What they sell.** "Inference at enterprise scale, from open models to
governed production", in two tiers: **shared** self-service per-token access,
and **dedicated endpoints** with "**99.9 % SLA**, custom autoscaling and
optional regional deployment" [src](https://nebius.com/services/token-factory).
Plus fine-tuning, custom-model deployment, and RAG tooling (embeddings +
PGVector).

**(b) Hardware and where it runs.** Nebius is a **neocloud that owns its
datacenters** — stated locations **Finland, France and the US**. This is the
one provider in Part A whose silicon is genuinely its own, and it is the same
company Baseten names as a supply source (§2.4(b)). Its GPU-hour rates are in
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
figures found in Part A (the other being Baseten's 99.99 % *capability* claim
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

### 2.13 Hugging Face Inference Endpoints

The most transparent autoscaler in Part A, and the only one still defaulting to
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
cold-start behaviour is the weakest design in Part A.

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

### 2.14 Anyscale / Ray Serve LLM

The BYOC platform play: not a capacity vendor at all, but the orchestration
layer, running in *your* cloud account.

**(a) What they sell.** Anyscale sells managed Ray — "create optimized Ray
clusters in your cloud account" with "pay-as-you-go, autoscaling node pools"
[src](https://docs.anyscale.com/llm/serving/). The serving layer, **Ray Serve
LLM**, is open source and usable without Anyscale
[src](https://docs.ray.io/en/latest/serve/llm/index.html).

**(b) Hardware and where it runs.** Whatever is in your cloud account —
**BYOC**. This makes Anyscale structurally the same choice as Northflank (§2.9),
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

This is the same capability set as Baseten's Dynamo-based stack (§2.4(f)) and
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

### 2.15 Cloudflare Workers AI

The edge case, literally — and the most architecturally distinctive stack in
Part A, because its constraints are the opposite of yours: hundreds of tiny
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
| **Prompt-lookup decoding** as a zero-training speculative method for repetitive/agentic workloads — cheaper to adopt than MTP/EAGLE, complements Baseten's suffix-automaton finding (§2.4(f)). See [`../cross-cutting/serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md). | **Neuron-style abstract billing.** |
| **The gVisor cost, quantified** (140 % → 250 % CPU). If anyone proposes sandboxing your engine, this is the number. | **Omni's 400 % over-commit via unified memory** — a *small*-model technique. Unified-memory paging under a 510 GB or 1.56 TB checkpoint would thrash; the 156 ms figure is for a 5 GB model. |
| **CUDA graph per batch size, built on demand** — a concrete alternative to a fixed capture list. | **Writing your own engine.** Infire's own numbers say a from-scratch engine buys ~6.6 % throughput over vLLM. Unless host CPU or co-tenancy is your binding constraint, that is not a rational build. |
| **The honest reading of "we wrote our own engine":** they did it for *co-tenancy and CPU efficiency*, not raw tokens/s. | |

---

## 3. Comparison tables

### 3.1 What each provider is, at one glance

| Provider | Category | Owns metal? | Own serving engine? | Ships snapshot/restore? | Public engineering depth |
|---|---|---|---|---|---|
| **Modal** | serverless functions/containers | no — AWS/GCP/Azure/OCI | no (engine-agnostic) | **yes — CPU + GPU** | ★★★★★ |
| **fal** | media model API + private serverless | no — AWS partnership | **yes — fal Inference Engine™** | no ⚠️ | ★★★★☆ (kernels) |
| **Wafer** | continual inference optimisation | no | no (optimises SGLang etc.) | n/a | ★★★★☆ (kernels/AMD) |
| **Baseten** | dedicated + per-token + training | no — 20+ clouds | composed (TRT-LLM/SGLang/vLLM + Dynamo) + own Speculation Engine | no — bandwidth instead | ★★★★★ |
| **RunPod** | GPU rental + serverless | partly (Secure/Community) | no (ships vLLM worker) | **FlashBoot** ⚠️ unpublished | ★★☆☆☆ |
| **Replicate** | model marketplace + deployments | no ⚠️ | no (per-model optimisation) | no | ★★☆☆☆ |
| **Beam** | OSS serverless | no | no | **yes — `checkpoint_enabled`** | ★★★☆☆ |
| **Inferless** | serverless endpoints | ⚠️ | no | ⚠️ none published | ★☆☆☆☆ |
| **Northflank** | general PaaS + GPU, BYOC | no — BYOC/any k8s | no | no | ★★☆☆☆ |
| **Cerebrium** | real-time serverless GPU | no | no | **yes — beta, explicit trigger** | ★★★☆☆ |
| **Lambda** | neocloud (API winding down) | **yes** | n/a | n/a | ★☆☆☆☆ |
| **Nebius Token Factory** | per-token + dedicated | **yes** (FI/FR/US) | ⚠️ | ⚠️ | ★☆☆☆☆ |
| **HF Inference Endpoints** | managed single-model endpoints | no — AWS/GCP/Azure | no (TGI/vLLM/SGLang/TEI/llama.cpp) | no | ★★★☆☆ (autoscaler) |
| **Anyscale / Ray Serve LLM** | BYOC orchestration | no — your cloud | no (vLLM/SGLang under Ray) | no | ★★★☆☆ |
| **Cloudflare Workers AI** | edge per-token API | **yes** (own PoPs) | **yes — Infire (Rust)** | no — Omni swap-in instead | ★★★★★ |

### 3.2 Cold-start mechanisms

| Provider | Image/weight path | Snapshot | Warm pool knob | Published cold-start numbers |
|---|---|---|---|---|
| **Modal** | lazy FUSE, content-addressed, 5 MB index, mem→SSD→AZ→CDN→blob, ~2.5 GiB/s | CPU (CRIU under gVisor) + GPU (CUDA checkpoint API, drv 570/575) | `min_containers`, `buffer_containers` | **~2,000 s → ~50 s (40×)**; vLLM 1 GiB 95.7 → 13.8 s; Parakeet 20 → 2 s; ViT 8.5 → 2.25 s; vLLM Qwen2.5 45 → 5 s; torch import 5 → 1.05 s; container boot ~1 s |
| **Baseten** | **BDN**: node NVMe → peer cache on consistent hash ring → mirrored origin; manifest of content hashes | none | min replicas, scale-down delay | **>2 GB/s onto H100 nodes; 2–3× faster; mirroring 1–5 GB/s; 50 replicas × 140 GB = 1× origin BW** |
| **RunPod** | cached models (host affinity, HF); `VolumeCache` mirror on network volume | **FlashBoot** (state retention) ⚠️ mechanism unpublished | active workers, idle timeout 5 s | "a few seconds, even for large models" (cached models) ⚠️ no FlashBoot figures |
| **Beam** | Volume `cache_dir` + `on_start` | **`checkpoint_enabled`**, snapshot at end of `on_start`; RTX4090/H100/A10G only | `min_containers`, `keep_warm_seconds` (180/10/600 s defaults) | containers "under a second" ⚠️ no model figures |
| **Cerebrium** | `/persistent-storage` (region-cached) or `/global-persistent-storage`; more CPU cores to parallelise reads | **CPU+GPU checkpointing (beta)**, explicit POST trigger to sidecar `169.254.169.253:8234` | `min_replicas`, `cooldown` | HF load "40+ s even at ~2 GB/s"; capture "seconds to several minutes" ⚠️ no before/after |
| **Replicate** | ⚠️ | no; `torch.compile` cache | `min_instances` | fine-tuned models **<1 s** (2023) ⚠️ mechanism unpublished |
| **Cloudflare** | models resident; **unified-memory swap-in over PCIe** | n/a (Omni over-commit instead) | n/a (platform-managed) | **~156 ms for a 5 GB model over PCIe 4.0** |
| **HF Endpoints** | ⚠️ | no | min replicas | ⚠️ none; documents a **502** during init |
| **fal**, **Inferless**, **Northflank**, **Anyscale**, **Nebius** | ⚠️ image cache / not published | no | `keep_alive` / min replicas | ⚠️ none published |

### 3.3 Autoscaling signals and parameters

| Provider | Primary signal | Scale-to-zero | Key parameters (exact names) | Notable defaults |
|---|---|---|---|---|
| **Modal** | input queue + concurrency set-point | default yes | `min_containers`, `max_containers`, `buffer_containers`, `scaledown_window`; `@modal.concurrent(max_inputs, target_inputs)` | `scaledown_window` 60 s (max 20 min); 4,000 containers/Function cap |
| **fal** | ⚠️ not documented | yes | `min_concurrency`, `max_concurrency`, `concurrency_buffer`, `concurrency_buffer_perc`, `scaling_delay`, `keep_alive`, `request_timeout`, `regions`; `max_multiplexing` | runtime vs code-versioned knob split |
| **Baseten** | **traffic (leading) + utilisation (lagging)** | supported, discouraged for latency-sensitive | min/max replicas, autoscaling window, scale-down delay, concurrency target | per-stage scaling for pipelines; same-cluster (10 ms vs 50 ms) |
| **RunPod** | **queue delay** *or* **request count** `ceil((queued+running)/scaler)` | flex workers | active workers, max workers, idle timeout, execution timeout, job TTL, GPU priority list | active 0, max 3, idle 5 s, exec 600 s, TTL 24 h, queue delay 4 s; request-count **recommended for LLMs** |
| **Beam** | **queue depth** | yes | `QueueDepthAutoscaler(min_containers, max_containers, tasks_per_container)`, `workers`, `keep_warm_seconds` | keep-warm 180 s endpoints / 10 s queues / 600 s pods |
| **Cerebrium** | **configurable**: concurrency utilisation, RPS, CPU, memory | `min_replicas=0` | `min_replicas`, `max_replicas`, `cooldown`, `replica_concurrency` | **`replica_concurrency` 1 on GPU, 100 on CPU** ← must raise for LLMs |
| **Inferless** | ⚠️ | yes | min/max replicas, Scale Down, Inference Timeout, Container Concurrency (1–100), `BATCH_SIZE`/`BATCH_WINDOW` | ⚠️ |
| **HF Endpoints** | **GPU/CPU utilisation ≥80 %** (1-min window); pending-requests beta | after **15 min** idle | min/max replicas, threshold | scale-up 1 min, scale-down 2 min + **300 s** stabilisation; beta **>1.5 pending/replica over 20 s**; **502 during init** |
| **Replicate** | traffic ⚠️ | yes | min instances, max instances | ⚠️ |
| **Northflank** | traffic ⚠️ | yes, "with intelligent request queuing" | ⚠️ | ⚠️ |
| **Anyscale/Ray** | Serve replica autoscaling + node pools | yes | `autoscaling_config` (min/max replicas), `tensor_parallel_size` | ⚠️ |
| **Cloudflare** | platform-internal | n/a | n/a | n/a |

### 3.4 Pricing, normalised to $/GPU-hour where published

| Provider | Billing unit | H100 80GB | H200 | B200 | A100 80GB | Notes |
|---|---|---:|---:|---:|---:|---|
| **fal** (discounted) | per second | **1.89** | 2.10 | 3.49 | — | list 4.50 / 4.50 / 6.25; B300 8.50→4.49 |
| **RunPod** Pods (Secure) | per second | 3.49 SXM / 2.89 PCIe | 4.59 | 6.79 | 1.59 | B300 7.89 |
| **Modal** | per second | **3.95** | 4.54 | 6.25 | 2.50 | B300 7.10; + CPU & memory billed separately |
| **RunPod** Serverless | per second | 4.18 | 5.58 | 8.64 | 2.74 | flex; ~20 % over Pods |
| **Beam** (on-demand) | per second | 3.63 | — | — | 1.49 | serverless tier is T4/A10G/4090/5090 only |
| **Baseten** | per **minute** | **6.50** | — | 9.98 | 4.00 | H100 MIG 40GB 3.75 |
| **HF Endpoints** | per hour, metered/min | **10.00** (GCP) | 5.00 (AWS) | — | 2.50 | initialization **is** billed |
| **Cerebrium** | per second | ⚠️ | — | — | ⚠️ | A10 ≈1.10; `protected` tier **2×**; cold start **not** billed, init **is** |
| **Replicate**, **Northflank**, **Inferless**, **Nebius**, **Wafer** | per second / per token / ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | rate cards not on fetched pages |
| **Cloudflare** | Neurons, $0.011/1k | n/a | n/a | n/a | n/a | per-token equivalents published per model |

Cross-check every row against [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md)
before using it in a cost model; that document, not this one, is the pinned
price source for [`../METHODOLOGY.md` §6](../METHODOLOGY.md).

Two things fall straight out of this table:

1. **The spread on identical silicon is ~5×** ($1.89 to $10.00 for an H100
   hour). That is not a hardware difference — it is how much stack the provider
   wraps around it and how they source capacity.
2. **Serverless costs ~20 % over rental** (RunPod's own Serverless-vs-Pods
   spread, the only clean same-vendor comparison available). That is the market
   price of scale-to-zero plus a queue.

### 3.5 Serving-engine and optimisation choices

| Provider | Engine(s) | Speculative decoding | Prefix/KV cache | PD disaggregation | Quantization | Custom kernels |
|---|---|---|---|---|---|---|
| **Baseten** | TRT-LLM, SGLang, vLLM under **Dynamo** + NIXL | **own Speculation Engine** (suffix automaton + MTP); EAGLE-3, DFlash | KV Block Manager; offload to CPU/SSD/remote; **KV-aware routing 34–62 %** | **yes, up to 6× TPS/GPU** | MXFP4, NVFP4 discussed | via engines |
| **fal** | **fal Inference Engine™**; SGLang for the LLM path | **DSpark**, acceptance length 4.6, 16× throughput | ⚠️ | ⚠️ | **NVFP4, MXFP8** | **yes — CuTeDSL, CUTLASS EVT** |
| **Cloudflare** | **Infire** (Rust), also vLLM, Python | **prompt-lookup**, +40 % (8B) / +70 % (70B) | **paged KV + per-head compression, 8× at >95 % quality** | no | **none in Infire yet** | CUDA graph per batch size |
| **Wafer** | SGLang (named); optimises whatever you run | yes (used in K3 benchmarks) | "caching, routing, decode decisions" | ⚠️ | **MXFP4, NVFP4** | **yes — agentic, profile-guided** |
| **Anyscale/Ray** | vLLM, SGLang | via engine | **prefix-aware routing** | **yes** | via engine | no |
| **HF Endpoints** | vLLM, TGI, SGLang, TEI, llama.cpp | via engine | via engine | no | via engine | no |
| **RunPod** | ships a vLLM worker | via engine | via engine | no | via engine | no |
| **Modal**, **Beam**, **Cerebrium**, **Northflank**, **Replicate**, **Inferless** | bring your own | via engine | via engine | your problem | via engine | no |

---

## 4. Patterns across serverless GPU platforms

Seven patterns recur independently across providers. Each is stated as the
pattern, the evidence, and the bare-metal translation.

### 4.1 Snapshotting is the endgame of cold start

Three providers ship checkpoint/restore and a fourth (RunPod's FlashBoot)
markets state retention without publishing a mechanism. The convergence is not
coincidence: once weights stream at multiple GB/s, the remaining cold-start
cost is **CPU work that cannot be parallelised** — Python imports (26,000
syscalls for `import torch` alone [src](https://modal.com/blog/mem-snapshots)),
CUDA context creation, kernel JIT, `torch.compile`, CUDA graph capture. None of
that gets faster with bandwidth. Snapshotting is the only way to skip it.

Three design points, all shipped:

| Design | Provider | Trade-off |
|---|---|---|
| Lifecycle-annotated (`@modal.enter(snap=True/False)`) | Modal | most flexible, most invasive to user code |
| Fixed point (end of `on_start`) | Beam | zero API surface, no control over *when* |
| Explicit trigger (POST to sidecar) | Cerebrium | full control, requires cooperating startup code |

**Bare metal:** the explicit-trigger design is the right first build — fire the
checkpoint once the engine reports ready and CUDA graphs are captured. The
enabling technology (CUDA checkpoint/restore, driver branches 570/575) is
available to anyone. Caveats are published and real: no multi-GPU, awkward with
`torch.compile`, does not speed up storage reads, and restored randomness is
identical. See [`06-cold-start.md` §4](06-cold-start.md).

### 4.2 Image and weight streaming from a content-addressed tiered store

Modal and Baseten converged on nearly the same architecture from opposite
directions:

| | Modal | Baseten BDN |
|---|---|---|
| Unit | ~5 MB image *index* + content-addressed files | manifest of content hashes/etags |
| Tiers | memory → SSD → AZ cache → regional CDN → blob | node NVMe → in-cluster peer cache (consistent hash ring) → mirrored origin |
| Throughput | ~2.5 GiB/s | >2 GB/s onto H100 nodes |
| Key property | mount in 1–100 ms, page in on demand | 50 replicas of a 140 GB model = **1× origin bandwidth** |

RunPod's cached models and Cerebrium's region-cached persistent storage are
lower-fidelity versions of the same idea; Beam's Volumes are the manual version.

**Bare metal:** this is the highest-value non-snapshot pattern, and it matters
*more* for you than for them, because this repo's checkpoints are 510 GB
(DeepSeek-V4.1-Flash) to 1.56 TB (Kimi-K3)
([`../METHODOLOGY.md` §8](../METHODOLOGY.md)). Pulling 1.56 TB from one object
store to eight nodes simultaneously is 12.5 TB of origin traffic; the peer-cache
ring makes it 1.56 TB. See [`01-bare-metal-cluster.md` §5](01-bare-metal-cluster.md)
and [`06-cold-start.md` §2](06-cold-start.md).

### 4.3 Warm pools are sold as a product, and the floor is not the buffer

Every provider exposes a warm floor. Only Modal and fal expose a distinct
**buffer above the active level** (`buffer_containers`, `concurrency_buffer` /
`concurrency_buffer_perc`) — capacity held *while the function is busy* to
absorb the next burst, which is a different thing from a minimum.

Keep-warm defaults, where published, cluster tightly and differ by workload
shape: RunPod idle timeout **5 s**; Beam **180 s** endpoints / **10 s** task
queues / **600 s** pods; Modal `scaledown_window` **60 s** default, 20 min max;
HF scale-to-zero after **15 min**.

Beam publishes the arithmetic that makes the cost visible: 1 s boot + 100 ms of
work + `keep_warm_seconds=300` bills **301.1 s**
[src](https://docs.beam.cloud/v2/resources/pricing-and-billing). At low,
spread-out request rates the warm window *is* the bill.

**Bare metal:** you already pay for the GPU, so the equivalent question is
opportunity cost — what else could that GPU be doing. Implement both knobs
(floor and buffer) and measure them separately. See
[`05-autoscaling-and-predictive-scaling.md` §5](05-autoscaling-and-predictive-scaling.md).

### 4.4 Per-second billing, and the initialization boundary as the real design choice

Every provider bills per second (Baseten: per minute). The interesting
divergence is **what counts**:

| Phase | Beam | Cerebrium | HF Endpoints | RunPod |
|---|---|---|---|---|
| Waiting for a machine | not billed | not billed ("cold start") | — | — |
| Image pull | **not billed** | billed (part of init) | billed ("initializing") | — |
| Model weight download | — | billed | billed | **not billed** (cached models) |
| Init / `on_start` / module scope | **billed** | **billed** | billed | billed |
| Request execution | billed | billed | billed | billed |
| Keep-warm / idle | billed | billed (via replicas) | billed | billed (idle timeout) |

Each boundary creates an incentive. Cerebrium bills initialization and ships
checkpointing — the customer pays for init, so the customer wants it gone.
RunPod does not bill weight downloads and instead invests in host-affinity
placement — RunPod pays for the miss, so RunPod wants cache hits.

**Bare metal:** you pay for all of it, always. The right internal metric is
**GPU-seconds-to-first-token-served after a replica is requested**, decomposed
into scheduling / image / weights / init / warm-up, and tracked as five numbers
rather than one — exactly Cerebrium's queueing-vs-initialization split.

### 4.5 Concurrency, not utilisation, is the scaling signal

Every LLM-serving platform here that documents a signal scales on **queue depth
or in-flight request count**:

- RunPod: queue delay (4 s default) or `ceil((queued + running) / scaler)` —
  and explicitly recommends request-count "for LLM workloads."
- Beam: `tasks_per_container` — pure queue depth.
- Cerebrium: concurrency utilisation against `replica_concurrency`.
- Baseten: traffic-based as the *leading* indicator, utilisation as the
  *lagging* one.
- Modal: `target_inputs` as an autoscaler set-point distinct from `max_inputs`.
- HF Endpoints: still defaults to 80 % accelerator utilisation, and **documents
  why it fails** — "GPU utilization may drop below threshold during model
  loading."

The reason is structural: a continuous-batching engine at batch 1 and at batch
64 can both show high GPU utilisation, and a replica loading a 500 GB
checkpoint shows none. Utilisation does not measure queueing, and queueing is
what users experience.

Two refinements worth stealing: Baseten's warning that **token-weighted load ≠
request count** ("a few LLM requests with massive token counts can consume more
GPU resources than many smaller requests with high cache hits"), and Modal's
separation of **set-point** (`target_inputs`) from **hard cap** (`max_inputs`),
which lets a replica burst without triggering a scale-up. See
[`05-autoscaling-and-predictive-scaling.md` §2](05-autoscaling-and-predictive-scaling.md).

### 4.6 Nobody writes their own engine unless co-tenancy or CPU is the constraint

Thirteen of fifteen providers compose vLLM, SGLang or TensorRT-LLM. The two
exceptions prove the rule:

- **Cloudflare/Infire** wrote Rust because they must co-host many models per
  GPU (vLLM cannot without MIG) and because host CPU is scarce at the edge. Their
  own benchmark shows the throughput win over bare vLLM is **~6.6 %**; the CPU
  win is **5.6×**. They optimised the constraint they actually had.
- **fal** wrote theirs because they are output-priced on diffusion/video, where
  the win comes from kernel fusion, FP4 and *fewer forward passes* (distillation),
  not from a better scheduler.

Baseten — arguably the most performance-focused general LLM provider — composed
instead, and built only the piece nobody else had: a speculation engine.

**Bare metal:** compose. Build only the component that is specific to your
workload and missing upstream. See
[`../cross-cutting/inference-engines.md` §9](../cross-cutting/inference-engines.md).

### 4.7 Optimisation has moved from the engine to the request graph

The measured wins providers publish in 2026 are no longer kernel-level alone;
they are about *which* tokens get computed and *where*:

| Technique | Best published number | Source |
|---|---|---|
| Prefill/decode disaggregation | **up to 6× TPS/GPU** | Baseten |
| KV-cache-aware routing | **34–62 %** | Baseten |
| Speculative decoding (hybrid SA+MTP) | **+40 % throughput or −40 % latency vs MTP alone**; +34 % acceptance length | Baseten |
| Speculative decoding (DSpark, diffusion drafter) | **acceptance length 4.6, 16× throughput** at fixed per-user rate | fal |
| Speculative decoding (prompt-lookup, training-free) | **+40 % (8B) / +70 % (70B)** | Cloudflare |
| KV cache compression, per-head | **8× smaller at >95 % task quality; 3.44× throughput** | Cloudflare |
| Distillation (CFG fold + timestep) | **6.3× end-to-end** | fal |
| Kernel fusion (CUTLASS EVT) | **1.28×** | fal |
| Occupancy fix (agent + profiler) | **11.65×** on one kernel | Wafer |

Note the ordering: graph-level and model-level changes (disaggregation,
routing, speculation, distillation) deliver multiples; kernel fusion delivers
tens of percent — except when something is badly wrong (6.25 % occupancy), in
which case kernels deliver 10×+. **Profile first; the distribution of wins is
bimodal.** Cross-reference
[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md)
and [`04-throughput-and-utilization.md` §5](04-throughput-and-utilization.md).

---

## 5. What transfers to a self-hosted bare-metal cluster — consolidated

### 5.1 Build these

| # | Pattern | Source of the design | Where it is detailed |
|---|---|---|---|
| 1 | **CPU+GPU checkpoint/restore**, explicit trigger after engine-ready | Cerebrium's trigger + Modal's mechanism (CUDA checkpoint API, drv 570/575) | [`06-cold-start.md` §4](06-cold-start.md) |
| 2 | **Tiered content-addressed weight store**: node NVMe → in-cluster peer cache on a consistent hash ring → object store, with a per-deployment content-hash manifest | Baseten BDN; Modal's lazy FUSE index | [`01-bare-metal-cluster.md` §5](01-bare-metal-cluster.md), [`06-cold-start.md` §2](06-cold-start.md) |
| 3 | **Concurrency-based autoscaling** with a set-point distinct from the hard cap, and token-weighted rather than request-count load | Modal (`target_inputs`/`max_inputs`), RunPod (request count), Baseten (traffic + utilisation) | [`05-autoscaling-and-predictive-scaling.md` §2](05-autoscaling-and-predictive-scaling.md) |
| 4 | **Warm floor *and* warm buffer**, measured and costed separately | Modal `buffer_containers`, fal `concurrency_buffer` | [`05-autoscaling-and-predictive-scaling.md` §5](05-autoscaling-and-predictive-scaling.md) |
| 5 | **Host-affinity scheduling to nodes with resident checkpoints** | RunPod cached models | [`05-autoscaling-and-predictive-scaling.md` §7](05-autoscaling-and-predictive-scaling.md) |
| 6 | **Compile/graph cache persisted on shared storage**, keyed by shape × arch × engine version | Replicate `torch.compile` caching | [`06-cold-start.md` §3](06-cold-start.md) |
| 7 | **Two request paths**: durable queue for batch, direct routing for interactive | RunPod queue-based vs load-balancing endpoints | [`09-reference-architectures.md` §6](09-reference-architectures.md) |
| 8 | **Active GPU health checks** — DCGM diags, GPUBurn, weekly NCCL all-reduce, Xid/ECC monitoring; drain and reimage rather than recover | Modal | [`01-bare-metal-cluster.md` §6](01-bare-metal-cluster.md) |
| 9 | **SIGTERM draining** before replica termination | Cerebrium | [`05-autoscaling-and-predictive-scaling.md` §5](05-autoscaling-and-predictive-scaling.md) |
| 10 | **Standardised model container** with an enforced `setup()`/`run()` boundary and a generated OpenAPI schema — adopt Cog or Truss, do not write a third | Replicate Cog, Baseten Truss | [`09-reference-architectures.md` §2](09-reference-architectures.md) |
| 11 | **Pinned CUDA/torch/engine chain** for reproducible builds | Baseten | [`01-bare-metal-cluster.md` §2](01-bare-metal-cluster.md) |
| 12 | **Five-number cold-start metric**: scheduling / image / weights / init / warm-up | Cerebrium's queueing-vs-init split | [`06-cold-start.md` §1](06-cold-start.md) |

### 5.2 Adopt these serving techniques (all corroborated by published numbers)

Prefill/decode disaggregation; KV-cache-aware routing; KV offload down the
memory hierarchy; hybrid speculative decoding (suffix automaton for repetitive
context + MTP/DSpark heads); per-head KV cache compression; NVFP4/MXFP4 with
quantization-aware distillation where quality regresses; CUTLASS EVT epilogue
fusion; CUDA graphs per batch size. Each is cross-referenced in
[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md)
and [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md).

### 5.3 Do not build these

| Do not build | Why | Evidence |
|---|---|---|
| **Multi-cloud capacity arbitrage** (LP/MIP solver over instance markets) | Fixed owned fleet; the problem degenerates to bin-packing models onto known GPUs | Modal's resource solver exists to exploit price volatility you are not exposed to |
| **gVisor or equivalent sandboxing of your own engine** | Measured cost: vLLM host CPU **140 % → 250 %**, ~3 % throughput. Bought to isolate hostile tenants; you have one tenant | [src](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine) |
| **Your own inference engine** | The one provider who did it and benchmarked honestly gained **~6.6 %** throughput over vLLM. Rational only if co-tenancy or host CPU is your binding constraint | Cloudflare Infire |
| **Unified-memory GPU over-commitment** for large models | Cloudflare's 400 % over-commit and 156 ms swap-in are for **5 GB** models. A 510 GB or 1.56 TB checkpoint would thrash PCIe | [src](https://blog.cloudflare.com/how-cloudflare-runs-more-ai-models-on-fewer-gpus) |
| **Platform-level dynamic batching in front of a continuous-batching engine** | Adds a batch-window of latency for no throughput gain | Inferless `BATCH_WINDOW`; Baseten's static/dynamic/continuous ladder |
| **A 502 as your cold-start behaviour** | Queue with a bounded wait instead | HF Endpoints documents the 502 as expected behaviour |
| **Spot/preemptible strategy** | No spot tier on owned hardware | Baseten's reserved/on-demand/spot blend |

### 5.4 The economic reading

Three numbers frame the make-vs-buy decision, and all three come from this
document's pricing tables:

1. **~5× spread on an identical H100-hour** ($1.89 fal discounted → $10.00 HF
   on GCP). The stack wrapped around the GPU, not the GPU, sets the price.
2. **~20 % is the market price of scale-to-zero plus a queue** (RunPod
   Serverless vs Pods, same vendor, same silicon).
3. **~2× is the market price of a managed serving stack on a per-token basis**
   (Baseten's DeepSeek-V4.1-Flash at $1.20/1M output vs the model vendor's
   $0.60/1M, per [`../README.md` §3](../README.md)).

For a bare-metal operator, (1) says the opportunity is real, (2) says
elasticity is cheap to buy and expensive to build, and (3) says the managed
margin is roughly what you must beat in *engineering* cost, not just hardware
cost. Feed all three into
[`07-cost-engineering.md` §1](07-cost-engineering.md).

---

## Open questions

1. **Modal's scheduler: LP or MIP?** The 2025 post says a GLOP linear program
   [src](https://modal.com/blog/resource-solver); the 2024 post says "a
   mixed-integer programming problem every minute"
   [src](https://modal.com/blog/the-future-of-ai-needs-more-flexible-gpu-capacity).
   Either the formulation was relaxed, or one is loose. Unresolved.
2. **Is Modal's runtime written in Rust?** Widely assumed; the only public
   evidence found is `tokio` in the `seccheck` code samples
   [src](https://modal.com/blog/catching-cryptominers). Not stated anywhere
   fetched. ⚠️
3. **What is RunPod FlashBoot actually doing?** Default-enabled, described only
   as "retaining worker state after spin-down." Is it a memory snapshot, a
   paused container, or host-affinity plus page cache? No mechanism, no numbers
   [src](https://docs.runpod.io/serverless/endpoints/endpoint-configurations).
4. **How did Replicate get fine-tuned models to sub-1-second boots in 2023?**
   Announced without mechanism [src](https://replicate.com/blog/fine-tune-cold-boots).
   Adapter-swap on a resident base is the obvious guess and is **not** stated.
5. **What is inside the "fal Inference Engine™"?** The kernel posts are
   excellent and specific, but no architecture document exists: no scheduler,
   batching, cache or multi-tenancy detail, and **no cold-start numbers at all**
   — a conspicuous gap for a serverless vendor.
6. **fal's B200 = 192 GB vs this repo's pinned 180 GB.** fal also lists RTX PRO
   6000 at 1.8 TB/s (the Workstation part, not the Server Edition). Is fal
   deploying a different SKU, or quoting marketing specs? Until resolved, do not
   recut any number in [`../METHODOLOGY.md` §8](../METHODOLOGY.md) from fal's
   table [src](https://fal.ai/docs/documentation/deployment/machine-types).
7. **Does anyone besides Modal publish GPU-snapshot latency for a real LLM
   engine?** Beam and Cerebrium ship the feature with zero before/after numbers.
   Modal's vLLM figure (45 s → 5 s, Qwen2.5) is the only one found, and it is
   for a small model.
8. **Wafer's baselines.** The 11.33×, 8×, 9× and 11.65× speedups are all
   relative to unstated baselines ("stock frameworks", `torch.compile`). Without
   the baseline configuration these are not reproducible.
9. **Wafer's Kimi-K3 MI355X result vs this repo's roofline.** 952 tok/s/node
   aggregate and 118 tok/s single-stream on 8× MI355X TP8 with speculative
   decoding at 1,024 in / 400 out — how does that compare to
   [`../models/kimik3/mi355x.md`](../models/kimik3/mi355x.md)? Not reconciled
   here; worth a dedicated pass, since it is measured and most of this repo's
   MI355X rows are `estimate`.
10. **What engine does Nebius Token Factory run, and on what?** They own the
    metal and publish an SLA but nothing about the stack.
11. **Third-party benchmarks.** Every performance number in this document is
    vendor-published and vendor-selected. No independent cross-provider
    benchmark was located (⚠️ web search was unavailable — see §1).
12. **Job postings as a stack signal.** All three careers pages probed were
    client-rendered and yielded nothing. This is a real gap: hiring pages are
    usually the best source for undocumented internals (languages, orchestrator,
    storage layer).
13. **Lambda's retreat from the Inference API** — was it economics, engineering,
    or focus? Only the wind-down notice was found
    [src](https://lambda.ai/inference).
14. **Does Cloudflare's per-head KV compression hold on MLA and
    linear/hybrid attention?** Their 8×-at->95 % result is on Llama-3.1-8B
    (GQA). Four of this repo's five models use MLA, KDA or GDN
    ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)), where "per-head compression"
    may not even be well-defined.
15. **Northflank's "GPU time-slicing, 2–4× utilization."** Unqualified, and
    implausible for models that need full HBM. What is actually being shared?

---

## Sources

All fetched 2026-09-19. **85 distinct URLs.**

**Modal** (14) — [blog: truly serverless GPUs](https://modal.com/blog/truly-serverless-gpus) ·
[blog: GPU memory snapshots](https://modal.com/blog/gpu-mem-snapshots) ·
[blog: memory snapshots](https://modal.com/blog/mem-snapshots) ·
[blog: resource solver](https://modal.com/blog/resource-solver) ·
[blog: containers talk (Belotti)](https://modal.com/blog/jono-containers-talk) ·
[blog: GPU health](https://modal.com/blog/gpu-health) ·
[blog: catching cryptominers](https://modal.com/blog/catching-cryptominers) ·
[blog: flexible GPU capacity](https://modal.com/blog/the-future-of-ai-needs-more-flexible-gpu-capacity) ·
[blog index](https://modal.com/blog) ·
[docs: cold start](https://modal.com/docs/guide/cold-start) ·
[docs: memory snapshot](https://modal.com/docs/guide/memory-snapshot) ·
[docs: scale](https://modal.com/docs/guide/scale) ·
[docs: concurrent inputs](https://modal.com/docs/guide/concurrent-inputs) ·
[docs: GPU](https://modal.com/docs/guide/gpu) ·
[docs: region selection](https://modal.com/docs/guide/region-selection) ·
[pricing](https://modal.com/pricing) ·
[GPU glossary](https://modal.com/gpu-glossary)

**fal** (13) — [fal.ai](https://fal.ai/) ·
[pricing](https://fal.ai/pricing) ·
[enterprise](https://fal.ai/enterprise) ·
[docs: scaling configuration](https://fal.ai/docs/documentation/deployment/scaling-configuration) ·
[docs: runners](https://fal.ai/docs/documentation/deployment/runners) ·
[docs: machine types](https://fal.ai/docs/documentation/deployment/machine-types) ·
[blog: MXFP8 quantizer on Blackwell](https://blog.fal.ai/chasing-6-tb-s-an-mxfp8-quantizer-on-blackwell/) ·
[blog: epilogue fusion](https://blog.fal.ai/crafting-efficient-kernels-with-epilogue-fusion/) ·
[blog: sub-second Ideogram v4](https://blog.fal.ai/serving-sub-second-ideogram-v4-without-quality-loss/) ·
[blog: DSpark 1000 tok/s](https://blog.fal.ai/how-we-achieved-1000-tok-s-and-16x-throughput-with-dspark-for-ideogram-v4-prompt-expander/) ·
[blog: Ulysses unbound](https://blog.fal.ai/ulysses-unbound-experiments-in-communication-computation-overlap/) ·
[blog: fal and AWS](https://blog.fal.ai/fal-and-aws-building-for-the-next-phase-of-generative-media/) ·
[blog: Series D](https://blog.fal.ai/our-series-d-scaling-fal/) ·
[blog: Patina](https://blog.fal.ai/introducing-patina/) ·
[blog index](https://blog.fal.ai) ·
[docs sitemap](https://docs.fal.ai/sitemap.xml)

**Wafer** (9) — [wafer.ai](https://wafer.ai) ·
[usewafer.com](https://usewafer.com) ·
[wafer.systems — *different company*](https://wafer.systems) ·
[manifesto](https://wafer.ai/manifesto) ·
[cases](https://wafer.ai/cases) ·
[blog index](https://wafer.ai/blog) ·
[blog: Series A](https://wafer.ai/blog/series-a) ·
[blog: inference alpha on AMD](https://wafer.ai/blog/inference-alpha-amd) ·
[blog: Kimi-K3 on MI355X](https://wafer.ai/blog/kimi-k3-mi355x) ·
[blog: profile-guided optimization](https://wafer.ai/blog/profile-guided-optimization)

**Baseten** (10) — [blog: Baseten Delivery Network](https://www.baseten.co/blog/how-the-baseten-delivery-network-bdn-makes-cold-starts-fast/) ·
[blog: inference stack (Dynamo Day)](https://www.baseten.co/blog/nvidia-dynamo-day-baseten-inference-stack/) ·
[blog: MTP acceptance in Speculation Engine](https://www.baseten.co/blog/boosting-mtp-acceptance-rates-in-baseten-speculation-engine/) ·
[blog: how we built MCM](https://www.baseten.co/blog/how-we-built-multi-cloud-capacity-management/) ·
[blog: efficient frontier of LLM inference](https://www.baseten.co/blog/the-efficient-frontier-of-llm-inference/) ·
[blog: Chains](https://www.baseten.co/blog/introducing-baseten-chains/) ·
[blog index](https://www.baseten.co/blog/) ·
[book: multi-cloud capacity management](https://www.baseten.co/inference-engineering/book/07-production/multi-cloud-capacity-management/) ·
[book: autoscaling](https://www.baseten.co/inference-engineering/book/07-production/autoscaling/) ·
[book: containerization](https://www.baseten.co/inference-engineering/book/07-production/containerization/) ·
[pricing](https://www.baseten.co/pricing/) ·
[github: Truss](https://github.com/basetenlabs/truss)

**RunPod** (9) — [docs: serverless overview](https://docs.runpod.io/serverless/overview) ·
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

**Replicate** (5) — [docs: deployments](https://replicate.com/docs/topics/deployments) ·
[blog index](https://replicate.com/blog) ·
[blog: fine-tune cold boots](https://replicate.com/blog/fine-tune-cold-boots) ·
[github: Cog](https://github.com/replicate/cog) ·
[Cog README (raw)](https://raw.githubusercontent.com/replicate/cog/main/README.md)

**Beam** (6) — [docs: introduction](https://docs.beam.cloud/v2/getting-started/introduction) ·
[docs: cold start performance](https://docs.beam.cloud/v2/topics/cold-start) ·
[docs: scaling out](https://docs.beam.cloud/v2/scaling/concurrency) ·
[docs: concurrent inputs](https://docs.beam.cloud/v2/scaling/concurrent-inputs) ·
[docs: pricing and billing](https://docs.beam.cloud/v2/resources/pricing-and-billing) ·
[docs: GPU acceleration](https://docs.beam.cloud/v2/environment/gpu) ·
[llms.txt](https://docs.beam.cloud/llms.txt)

**Cerebrium** (6) — [docs: faster cold starts](https://cerebrium.ai/docs/performance/faster-cold-starts) ·
[docs: memory and GPU checkpointing](https://cerebrium.ai/docs/performance/checkpointing) ·
[docs: autoscaling apps](https://cerebrium.ai/docs/scaling/scaling-apps) ·
[docs: batching and concurrency](https://cerebrium.ai/docs/scaling/batching-concurrency) ·
[docs: calculating compute cost](https://cerebrium.ai/docs/calculating-cost) ·
[docs: using GPUs](https://cerebrium.ai/docs/hardware/using-gpus.md) ·
[llms.txt](https://cerebrium.ai/docs/llms.txt)

**Inferless** (4) — [docs: overview](https://docs.inferless.com/concepts/overview) ·
[docs: model settings](https://docs.inferless.com/api-reference/model-endpoint/configuring-the-model-settings) ·
[docs: concurrent requests](https://docs.inferless.com/concepts/processing-concurrent-requests) ·
[llms.txt](https://docs.inferless.com/llms.txt)

**Northflank** (2) — [product: GPU PaaS](https://northflank.com/product/gpu-paas) ·
[sitemap](https://northflank.com/sitemap.xml)

**Lambda** (2) — [inference (wind-down notice)](https://lambda.ai/inference) ·
[docs redirect](https://docs.lambda.ai/public-cloud/lambda-inference-api/)

**Nebius** (1) — [Token Factory](https://nebius.com/services/token-factory)

**Hugging Face** (2) — [docs: autoscaling](https://huggingface.co/docs/inference-endpoints/en/autoscaling) ·
[docs: pricing](https://huggingface.co/docs/inference-endpoints/en/pricing)

**Anyscale / Ray** (2) — [Anyscale docs: LLM serving](https://docs.anyscale.com/llm/serving/) ·
[Ray docs: Serve LLM](https://docs.ray.io/en/latest/serve/llm/index.html)

**Cloudflare** (6) — [blog: Infire, most efficient AI inference engine](https://blog.cloudflare.com/cloudflares-most-efficient-ai-inference-engine) ·
[blog: more AI models on fewer GPUs (Omni)](https://blog.cloudflare.com/how-cloudflare-runs-more-ai-models-on-fewer-gpus) ·
[blog: bigger, better, faster](https://blog.cloudflare.com/workers-ai-bigger-better-faster/) ·
[blog: making Workers AI faster](https://blog.cloudflare.com/making-workers-ai-faster) ·
[blog: Workers AI launch](https://blog.cloudflare.com/workers-ai/) ·
[docs: Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) ·
[docs: Workers AI](https://developers.cloudflare.com/workers-ai/) ·
[blog sitemap](https://blog.cloudflare.com/sitemap-posts.xml)

---

## Verification log (2026-09-19)

| Item | Status |
|---|---|
| **Wafer company identity** | **Resolved.** `wafer.ai` = `usewafer.com` = "Wafer \| Continual Inference", the inference company. `wafer.systems` is an unrelated company of the same name (consumer OS/assistant, "© 2026 Wafer, Inc"). Four other candidate domains do not resolve. Method: HTTP-probed 8 domains, compared page content and analytics IDs. |
| **fal B200 = 192 GB** | **Conflict, unresolved, not propagated.** fal's machine-types doc says 192 GB; [`../METHODOLOGY.md` §8](../METHODOLOGY.md) pins 180 GB per [`../gpus/b200.md` §2](../gpus/b200.md). Baseten and RunPod both list 180 GB. No repo number recut. Open question 6. |
| **fal RTX PRO 6000 = 1.8 TB/s** | **Conflict, explained.** 1.8 TB/s is the *Workstation* edition (1,792 GB/s); [`../gpus/rtx6000-pro.md`](../gpus/rtx6000-pro.md) pins the Server Edition at 1,597 GB/s. Not propagated. |
| **Modal scheduler formulation** | **Conflict, unresolved.** LP/GLOP (2025) vs MIP-every-minute (2024). Both cited inline. Open question 1. |
| **"Modal's runtime is in Rust"** | **Downgraded to inferred.** Only evidence is `tokio` in `seccheck` code samples. Labelled inline and in open question 2. |
| **Cog implementation language** | **Partially resolved.** README states a "Rust/Axum" HTTP server; the CLI is Go. Exact breakdown not obtained — GitHub languages API rate-limited during research. Marked ⚠️ inline. |
| **Cloudflare Neuron pricing** | **Change recorded.** Launch post (2023) had two tiers ($0.01/1k RTN, $0.125/1k FTN); current docs have a single $0.011/1k. Both cited. |
| **Lambda Inference API** | **Status change recorded.** Wind-down stated on the product page; docs path now redirects. Profile marked accordingly. |
| **Every performance number in §2–§4** | **Vendor-published and vendor-selected.** No independent benchmark located. ⚠️ flagged in open question 11 and §1. |
| **WebSearch requirement (≥20 queries)** | **Not met — budget exhausted (200/200) before this document began.** Substituted sitemap/`llms.txt` crawling, raw-`.md` fetching and bulk URL probing; 85 distinct primary URLs fetched. Coverage impact stated in §1 and open questions 11–12. |
| **Careers/job-post sourcing** | **Attempted, failed.** Modal, fal and Wafer careers pages are client-rendered; no stack signal extractable. Open question 12. |
| **`scaling/02`, `03`, `08`** | **Absent from disk at write time.** No cross-references invented. Siblings `01`, `04`, `05`, `06`, `07`, `09` were read and linked by section. |
