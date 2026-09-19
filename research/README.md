# research/ — index and reading guide

## 1. What this is

Fact-checked GPU-inference research for the five model experiments in this
repo, across eight GPUs. Research date **2026-09-19** (pinned in every
document; see [`METHODOLOGY.md`](METHODOLOGY.md)). It answers, per (model,
GPU) pair: does it fit, what parallelism, what format actually executes on
that silicon, what throughput/latency, and what it costs per 1M tokens —
against the model vendor's own API price as a sanity check.

40 pairs (5 models × 8 GPUs) were each written as an independent per-pair
analysis, numerically audited, then summarized into a machine-readable
[`matrix/pairs.json`](matrix/pairs.json) and five cross-model matrix docs.
**Every number in this tree traces to a primary source or is a formula from
`METHODOLOGY.md` applied to a primary-sourced input** — see §7 of that file,
"What must never happen."

## 2. How to read it

**Legend** (full version: [`METHODOLOGY.md` §Legend](METHODOLOGY.md#legend)):

| Marker | Meaning |
|---|---|
| `[src]` + URL | Sourced from a primary document (datasheet, model card, engine docs, vendor blog, MLPerf). |
| **⚠️ TO BE VERIFIED** | Best estimate; no primary source, or sources conflict. Method stated inline. |
| `est.` | Derived from METHODOLOGY's formulas, not measured. |
| `meas.` | Published or repo-measured value; source cited. |

**Confidence labels** (the `confidence` field of each `pairs.json` row, also
printed at the top of every `models/<exp>/<gpu>.md`): `measured` (repo or
vendor benchmark exists), `estimate` (roofline from METHODOLOGY §4/§6),
`not-runnable` (no engine admits the model on that GPU today), `speculative`
(neither the engine registration nor a working run exists). Across the 40
pairs: **5 measured, 31 estimate, 3 not-runnable, 1 speculative** — see
[`matrix/cost-matrix.md` §9.1](matrix/cost-matrix.md#91-confidence-label-per-pair-from-pairsjson).

**Where estimates come from.** Throughput and cost that are not `meas.` are
a roofline: `decode_step_time ≈ max(bytes_per_step / (HBM_BW × MBU),
2 × active_params × batch / (peak_FLOPS × MFU))`, using the planning MBU/MFU
bands in [`METHODOLOGY.md` §4](METHODOLOGY.md#4-throughput-and-latency-roofline)
unless a pair doc cites a published measurement instead. Prices are always
named rows from [`cross-cutting/cloud-pricing.md` §5.14](cross-cutting/cloud-pricing.md),
never re-derived. Anything not backed by a citation is marked
**⚠️ TO BE VERIFIED** with the estimation method stated inline, and where two
documents disagree, both are cited rather than one being silently picked.

## 3. Headline: best GPU per model, this repo's node

Built with `python3` from [`matrix/pairs.json`](matrix/pairs.json); the
underlying grids are [`matrix/cost-matrix.md`](matrix/cost-matrix.md) §2–§4,
§8 and [`matrix/fit-matrix.md`](matrix/fit-matrix.md) §4. "Best" = lowest
`low`-tier cell. Full ranges and every operating point are in the linked
matrix docs — nothing below is a new number.

| Model | Best interactive (S1) | Best $/token (blended) | Min GPUs, 8×B300 node | Interactive $/1M out, low–high (all 8 GPUs) | Max-throughput $/1M out, low–high (all 8 GPUs) | Vendor API $/1M out |
|---|---|---|---:|---:|---:|---:|
| [DeepSeek-V4.1-Flash](models/deepseek41f/README.md) | B200 — [$0.462](models/deepseek41f/b200.md) | B200 — [$0.19](models/deepseek41f/b200.md) | 2 ([b300.md](models/deepseek41f/b300.md)) | $0.462–$6.217 | $0.355–$4.026 | $0.60 |
| [DeepSeek-V4.1-Flash-NVFP4](models/deepseek41fnvfp4/README.md) | B200 — [$0.565](models/deepseek41fnvfp4/b200.md) | B200 — [$0.1485](models/deepseek41fnvfp4/b200.md) | 4 ([b300.md](models/deepseek41fnvfp4/b300.md)) | $0.565–$8.663 | $0.048–$2.507 | $0.60 |
| [Qwen3.8-27B](models/qwen3827b/README.md) | H200 — [$0.159](models/qwen3827b/h200.md) | B300 — [$0.0602](models/qwen3827b/b300.md) | 1 ([b300.md](models/qwen3827b/b300.md)) | $0.159–$0.561 | $0.15–$0.469 | $3.00 |
| [Kimi-K3](models/kimik3/README.md) | B300 — [$10.336](models/kimik3/b300.md) | B300 — [$3.117](models/kimik3/b300.md) | 8 ([b300.md](models/kimik3/b300.md)) | $10.336–$6,916.69¹ | $3.814–$87.91 | $15.00 |
| [Marlin-2B](models/marlin2b/README.md) | B300 — [$0.022](models/marlin2b/b300.md) | B300 — [$0.0092](models/marlin2b/b300.md) | 1 ([b300.md](models/marlin2b/b300.md)) | $0.022–$0.2256 | $0.0188–$0.2141 | none |

¹ The $6,916.69 high is `kimik3/h100` at concurrency 1 — it never reaches the
50 ms TPOT SLO at any concurrency, so it is **infeasible at S1**, not an
interactive price (`pairs.json` marks it `"slo_met": false`); see [`matrix/cost-matrix.md` §2](matrix/cost-matrix.md#2-interactive--1m-output-tokens-at-tpot--50-ms-s1-4k-in--512-out).
Vendor prices are DeepSeek off-peak, Qwen Cloud list, Moonshot list — full
table with peak/off-peak and third-party rows at
[`matrix/cost-matrix.md` §6.1](matrix/cost-matrix.md#61-published-vendor-prices).
Min-GPU counts and parallelism for every GPU, not just B300, are in
[`matrix/fit-matrix.md` §1](matrix/fit-matrix.md#1-fit-grid--status--minimum-gpus--parallelism--executed-format).

## 4. Full file index

- [`METHODOLOGY.md`](METHODOLOGY.md) — every formula (weights, KV, fit,
  throughput, cost), the legend, and §8's pinned GPU/model reference values.
  The single source of truth for arithmetic; nothing below re-derives it.

**`matrix/`** — cross-model, cross-GPU summaries, all generated from `pairs.json`:

- [`pairs.json`](matrix/pairs.json) — the 40 pair analyses as data: fit,
  min/recommended GPUs, parallelism, executed format, attention kernel, max
  concurrency, interactive/max-throughput tok/s and $/1M, blended cost,
  vendor price, confidence.
- [`fit-matrix.md`](matrix/fit-matrix.md) — which model runs on which GPU,
  minimum/recommended deployment shapes, per-GPU memory arithmetic, open
  questions on capacity and KV accounting.
- [`cost-matrix.md`](matrix/cost-matrix.md) — $/1M tokens (interactive, max
  throughput, blended, input), vendor break-even, sensitivity (MBU,
  cache-hit rate, speculation, reserved pricing), rankings per model.
- [`gpu-optimizations.md`](matrix/gpu-optimizations.md) — attention kernel
  and quantization-format support per GPU, executed format per (model, GPU),
  the NVFP4/MXFP4 DeepSeek and Kimi-K3 stories, open questions.
- [`recommendations.md`](matrix/recommendations.md) — verdict per model,
  recommended engine/launch shape on this repo's hardware, prioritized
  benchmark plan, risks, a decision table.

**`gpus/`** — one reference doc per GPU (HBM, bandwidth, TFLOPS by dtype,
attention kernel support, quantization support, pricing pointers):

- [`h100.md`](gpus/h100.md) — H100 SXM5 80GB; no FP4/MX hardware.
- [`h200.md`](gpus/h200.md) — H200 SXM 141GB; same die as H100, more/faster HBM.
- [`b200.md`](gpus/b200.md) — B200 HGX 180GB; first Blackwell-gen datacenter GPU.
- [`b300.md`](gpus/b300.md) — B300 HGX/DGX/AWS p6-b300 268GB; Blackwell Ultra, FP4-only 1.5× uplift over B200.
- [`gb300.md`](gpus/gb300.md) — GB300 NVL72 per-GPU; higher clocked, 72-GPU NVLink domain.
- [`a100.md`](gpus/a100.md) — A100 SXM4 80GB; no FP8/FP4 tensor cores.
- [`rtx6000-pro.md`](gpus/rtx6000-pro.md) — RTX PRO 6000 Blackwell Server 96GB GDDR7; PCIe, no FA3/FA4/tcgen05.
- [`mi355x.md`](gpus/mi355x.md) — AMD MI355X 288GB; gfx950/CDNA4, ROCm.

**`cross-cutting/`** — topic references shared across every model and GPU:

- [`flash-attention.md`](cross-cutting/flash-attention.md) — attention kernel
  (FA2/FA3/FA4/FlashMLA/FlashInfer/AITER) support and measured TFLOPS per GPU.
- [`quantization-formats.md`](cross-cutting/quantization-formats.md) — bytes/param
  by format and which GPU accelerates which format natively vs. via dequant.
- [`inference-engines.md`](cross-cutting/inference-engines.md) — vLLM/SGLang/TRT-LLM
  support matrix per GPU and per model.
- [`cloud-pricing.md`](cross-cutting/cloud-pricing.md) — $/GPU-hour across
  clouds; §5.14 is the `low`/`high`/`res1y` price every cost figure in this tree cites.
- [`serving-optimizations.md`](cross-cutting/serving-optimizations.md) —
  prefix caching, speculative decoding, parallelism strategies, disaggregation.
- [`inferencex-api.md`](cross-cutting/inferencex-api.md) — how to re-fetch the
  SemiAnalysis InferenceX/InferenceMAX benchmark rows cited elsewhere in this tree.

**`models/<exp>/`** — one directory per experiment (`deepseek41f`,
`deepseek41fnvfp4`, `qwen3827b`, `kimik3`, `marlin2b`), each holding:

- `README.md` — the per-model GPU guide: a cross-GPU comparison table,
  recommendation, and cost-vs-vendor-API section, consolidating the 8 pair
  docs below it. Start here for a model.
- `architecture.md` — params, weight memory, KV/state formula, attention
  type, what runs it today — the basis every `<gpu>.md` in the directory cites.
- `<gpu>.md` (one of `h100`/`h200`/`b200`/`b300`/`gb300`/`a100`/`rtx6000-pro`/`mi355x`)
  — the audited pair analysis: fit, parallelism, executed format, attention
  kernel, throughput/latency tables, cost, verification log.
- `config.json` — the model's HF config, reconciled against
  `model.safetensors.index.json` `total_size` per
  [`METHODOLOGY.md` §1](METHODOLOGY.md#1-weight-memory).

`marlin2b/` additionally carries `MODEL_CARD.md`, `FILES.md`, the base
HF config/processor/generation configs and `marlin_gb300.py` — the actual
gated-model artifacts the architecture doc cites, not research prose.

## 5. How to extend

**METHODOLOGY.md is the single formula source.** Never fork a formula, an
MBU/MFU band, or a pinned GPU/model value into another doc — cite
METHODOLOGY's section instead. If a formula needs to change, change it there
and every doc that cites it stays correct by reference.

**Adding a GPU:**
1. Write `gpus/<gpu>.md` — HBM as-deployed, bandwidth, dense BF16/FP8/FP4
   TFLOPS, attention kernel and quantization support (follow an existing GPU
   doc's structure).
2. Add its pinned row to [`METHODOLOGY.md` §8](METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs).
3. Write `models/<exp>/<gpu>.md` for each of the 5 experiments (fit,
   parallelism, format, throughput, cost — same structure as a sibling
   `<gpu>.md` in that model's directory).
4. Add the 5 new rows to `matrix/pairs.json`, then regenerate every
   `matrix/*.md` grid with `python3` (never hand-edit a generated table).
5. Add a column to each `models/<exp>/README.md`'s cross-GPU table.

**Adding a model:**
1. Write `models/<exp>/architecture.md` and `models/<exp>/config.json`
   (reconciled against the checkpoint's `total_size`, per METHODOLOGY §1).
2. Write `models/<exp>/<gpu>.md` for each of the 8 GPUs.
3. Write `models/<exp>/README.md` consolidating them.
4. Add 8 rows to `matrix/pairs.json`; regenerate `matrix/*.md` with `python3`;
   add the model's row to this file's §3 headline table.

## 6. Top 14 open questions across the tree

Deduplicated from the "open questions" sections of the three matrix docs;
each links to its fuller writeup. (The former item 1 — `deepseek41f/b200`'s
max-throughput cell contradicting its own pair document — is **closed**:
`pairs.json` was re-cut on 2026-09-19 to the document's corrected
$0.355–$0.828.)

1. **`kimik3/h100`'s blended cost figure is built from max-throughput
   output, not interactive**, unlike the other 39 pairs, and its interactive
   cell never reaches the 50 ms TPOT SLO at any concurrency.
   [cost-matrix §9.3.1](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
2. **`deepseek41f/h100`'s measured and estimated (roofline) costs differ
   4–9×** — the grid carries the roofline figure, the document's own
   measured agentic run is far worse.
   [cost-matrix §9.3.2](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
3. **GB300 and MI355X economics rest on a single published price each**
   (OCI is the only seller of either) — a second seller would move every
   GB300/MI355X cell and invert several rankings.
   [cost-matrix §9.3.3](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
4. **No per-GPU-generation MBU/MFU measurement exists anywhere in this
   tree** — every non-`measured` cost cell carries a ±20% band from the
   METHODOLOGY planning default alone.
   [cost-matrix §9.3.4](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close),
   [gpu-optimizations §8.16](matrix/gpu-optimizations.md#8-open-questions)
5. **Prefill / $-per-1M-input rates are the weakest input in the cost
   grid** — one pair is 2.7× pessimistic against its own measured TTFT, one
   has no prefill measurement at all.
   [cost-matrix §9.3.5](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
6. **DSpark/MTP speculative-decoding acceptance rates are benchmark
   constants, not measurements, on most pairs** — worth 3–6.7× on DeepSeek's
   output cost.
   [cost-matrix §9.3.6](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
7. **Reserved (`res1y`) pricing doesn't exist for GB300 or MI355X, and is
   *above* on-demand for B300** — "reserved" is not a uniform discount
   across this matrix.
   [cost-matrix §9.3.7](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
8. **DeepSeek-V4.1-Flash's KV-cache reading (replicated per rank vs.
   aggregated under DP-attention) is picked differently by sibling pair
   documents**, and `pairs.json` inherits whichever each one picked.
   [fit-matrix §6.1](matrix/fit-matrix.md#61-the-kv-reading-is-not-uniform-across-pair-documents--to-be-verified)
9. ~~**Whether the 890 B/token FP4 KV cache works outside Blackwell is
    disputed.**~~ **RESOLVED 2026-09-19** — the 890 B/token layout (720
    main + 170 indexer) is **architectural and byte-exact**, but it is
    *executed* only where vLLM's `nvfp4_ds_mla` resolves, i.e.
    `FLASHMLA_MEGA_ATTN_DSV41` gated on `capability.major == 10` →
    sm_100/sm_103 (B200, B300, GB300). Budget **FP8 1,650 B/token**
    elsewhere, and **BF16 3,200 B/token on H100** under vLLM's shipped SM90
    profile (measured KV pool). Full resolution with both citations:
    [fit-matrix §6.3](matrix/fit-matrix.md#63-deepseek-v41-flashs-890-b-fp4-kv-cache-outside-blackwell--resolved-2026-09-19).
    **Surviving question, narrowed:** FlashInfer PR #4955 merged an
    SM120/SM121 NVFP4 sparse-MLA kernel on 2026-09-07 that vLLM's SM12x
    dtype gate still refuses — if that gate opens, RTX PRO 6000 (and only
    it) moves to 890 B/token. ⚠️ TO BE VERIFIED per engine version.
10. **Whether DeepSeek's DSA sparse-decode kernel exists on SM90 at all is
    unresolved** (FlashMLA's README says SM100-only; vLLM's own gate and
    working H100 runs disagree) — leading suspect for H100's measured 2.11×
    roofline gap.
    [fit-matrix §6.4](matrix/fit-matrix.md#64-does-the-dsa-sparse-decode-kernel-exist-on-sm90-at-all),
    [gpu-optimizations §8.1](matrix/gpu-optimizations.md#8-open-questions)
11. **Whether MXFP4 executes natively on `sm_120` (RTX PRO 6000) or falls
    back to Marlin dequant is disputed**, with measured community builds on
    both sides and no published A/B.
    [fit-matrix §6.5](matrix/fit-matrix.md#65-does-mxfp4-execute-natively-on-sm_120-or-fall-back-to-marlin),
    [gpu-optimizations §8.10](matrix/gpu-optimizations.md#8-open-questions)
12. **Engram hash-table sharding policy on ROCm (MI355X) is unverified** —
    if the tables replicate instead of row-shard, the whole MI355X
    recommendation flips from TP4 to TP8 or host-offload.
    [fit-matrix §6.9](matrix/fit-matrix.md#69-engram-sharding-on-rocm--to-be-verified)
13. **No attention-kernel TFLOPS measurement exists for RTX PRO 6000 at
    all** — every attention row for it in this tree is a peak-ratio
    estimate, not a benchmark.
    [gpu-optimizations §8.2](matrix/gpu-optimizations.md#8-open-questions)
14. ~~**DeepSeek-V4.1-Flash's executed KV bytes/token disagree across
    sources.**~~ **RESOLVED for the Hopper split, 2026-09-19** — H100 runs
    **BF16 3,200 B/token** (measured KV pool,
    [deepseek41f/h100 §1.4](models/deepseek41f/h100.md)); H200, RTX PRO 6000
    and MI355X budget **FP8 1,650 B**; **890 B is executed only on
    B200/B300/GB300**.
    [fit-matrix §6.3](matrix/fit-matrix.md#63-deepseek-v41-flashs-890-b-fp4-kv-cache-outside-blackwell--resolved-2026-09-19).
    **Still open:** the **~2,340 B/token observed on B200** — 2.6× the
    architectural 890 on a GPU that *does* have the kernel — is still
    unexplained.
    [gpu-optimizations §8.17–18](matrix/gpu-optimizations.md#8-open-questions)
