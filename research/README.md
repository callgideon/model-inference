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
| [Kimi-K3](models/kimik3/README.md) | B300 — [$7.3926](models/kimik3/b300.md) | B300 — [$2.3811](models/kimik3/b300.md) | 8 ([b300.md](models/kimik3/b300.md)) | $7.3926–$6,916.69¹ | $3.6101–$87.91 | $15.00 |
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
   has no prefill measurement at all. **2026-09-19: GEMM-only prefill
   rooflines (`2 × active_params × T` at an assumed MFU) are now known to be
   ~13.4× high on the sparse/indexer-attention model class** — measured
   9,267 vs. roofline 123,750 prefill tok/s/GPU for DeepSeek-V4.1-Flash on
   B200, where the indexer scan, top-k, Engram gathers and mHC mixing
   dominate, not the expert GEMMs. Treat any un-measured `$/1M input` for a
   DSA/CSA-family model (DeepSeek-V4.1-Flash, Kimi-K3) as an order-of-magnitude
   optimistic floor until a step-time measurement replaces it.
   [gpus/b200.md §9.3](gpus/b200.md), [deepseek41f/b200.md §3.1](models/deepseek41f/b200.md),
   [cost-matrix §9.3.5](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
6. **DSpark/MTP speculative-decoding acceptance rates are benchmark
   constants, not measurements, on most pairs** — worth 3–6.7× on DeepSeek's
   output cost.
   [cost-matrix §9.3.6](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
7. **Reserved (`res1y`) pricing doesn't exist for GB300 or MI355X, and is
   *above* on-demand for B300** — "reserved" is not a uniform discount
   across this matrix.
   [cost-matrix §9.3.7](matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
8. ~~**The KV-cache reading (replicated per rank vs. aggregated under
   DP-attention) is picked differently by sibling pair documents**, and
   `pairs.json` inherits whichever each one picked.~~ **RULE RESOLVED
   2026-09-19** (gap `X6-kimik3-h200-concurrency-5x-disagreement`) —
   [METHODOLOGY §3](METHODOLOGY.md#3-fit) now multiplies `kv_budget` by
   `n_gpus` **only when KV is sharded across ranks**; with replicated KV
   (MLA at TP without DCP, or `num_key_value_heads = 1`) the single-GPU
   budget is used and only the per-request state that *is* sharded gets
   divided, and every table must state its reading. **Kimi-K3 is resolved
   outright** — no published H200/H100 recipe configures DCP or
   DP-attention, so [gpus/h200.md §2](gpus/h200.md) is recut
   **169 / 148 / 98 → 98 / 42 / 12** at 8K/32K/128K (aggregate retained
   beside it, labelled as requiring DCP), and `gpus/h100.md` §2 and
   `gpus/rtx6000-pro.md` §9g the same way. **DeepSeek-V4.1-Flash stays
   open** — it has a published EP8 + DP-attention shape, so both readings
   stay printed per pair. ⚠️ Remaining: **confirm against a booted engine's
   reported KV pool (benchmark B2)**.
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

## 7. Resolution log

- **2026-09-19 — gap `X5-b200-prefill-roofline-13x-high` RESOLVED in favour of the measurement.** [gpus/b200.md §9.3](gpus/b200.md)'s DeepSeek-V4.1-Flash prefill row was a GEMM-only roofline of **123,750 tok/s/GPU** (`2 × active × T` at MFU 0.22) while [deepseek41f/b200.md §3.1](models/deepseek41f/b200.md) measured **9,267 tok/s/GPU** (74,135 aggregate on 8× B200 TP8) from vLLM PR #56686's 220–222 ms chunked-prefill steps — **13.4×**. §9.3 now prints 9,267 `meas.` with the cause annotated (indexer scan, top-k, Engram gathers, mHC mixing) and a warning that a future DSA-family row must be ⚠️ **TO BE VERIFIED**, not formula-filled; §9.3 has no Kimi-K3 row and nothing else was rescaled. No `$/1M input` figure moved: gpus/b200.md §10.4 is an output grid, and [cost-matrix §5](matrix/cost-matrix.md)'s `deepseek41f/b200` cell **$0.1806–$0.42** was already built on the measured rate (it reproduces the pair document's own $0.180). Open question 5 above updated to carry the class-level warning.

- **2026-09-19 — gap `X6-kimik3-h200-concurrency-5x-disagreement` RESOLVED to the per-GPU replicated reading** (open question 8 above). [METHODOLOGY §3](METHODOLOGY.md#3-fit) now multiplies `kv_budget` by `n_gpus` **only when KV is sharded across ranks**; with replicated KV (MLA at TP without DCP, or `num_key_value_heads = 1`) the single-GPU budget is used and only the per-request state that *is* sharded gets divided, and every table must state its reading. Recut: [gpus/h200.md §2](gpus/h200.md) Kimi-K3 **169 / 148 / 98 → 98 / 42 / 12** and both DeepSeek rows to both readings; [gpus/h100.md §2](gpus/h100.md) DeepSeek **1 157 / 79 → 144 / 9** and Kimi-K3 **53 / 31 → 21 / 2** (measured resident); [gpus/rtx6000-pro.md §9g](gpus/rtx6000-pro.md) DeepSeek **9,068 / 2,613 / 679 → 1,133 / 326 / 84**. No `pairs.json` concurrency value and no [fit-matrix §2](matrix/fit-matrix.md) grid cell moved — both already carried per-pair replicated readings; `kimik3/h200`'s 74 / 6 were verified against the chosen reading. Kimi-K3 is closed; DeepSeek-V4.1-Flash's per-pair default stays open because it has a published EP8 + DP-attention shape. Full write-up: [fit-matrix §6.1](matrix/fit-matrix.md#61-the-kv-reading-is-not-uniform-across-pair-documents--to-be-verified), [kimik3/h200.md §1.3](models/kimik3/h200.md#13-max-concurrency).

- **2026-09-19 — gap `C1-a100-capacity-basis` RESOLVED → 80 GB decimal.** `usable_hbm` = 0.90 × 80 GB = **72.0 GB = 67.06 GiB/GPU** is now the single A100 planning basis tree-wide; `nvidia-smi`'s 81,920 MiB = 80 GiB = **85.90 GB** reading is demoted to a labelled ⚠️ TO BE VERIFIED **+7.4 %** sensitivity, never a planning basis ([METHODOLOGY §8](METHODOLOGY.md#gpus), [gpus/a100.md §2](gpus/a100.md)). Two pair docs had planned on the GiB reading and were recut: `deepseek41f/a100` (**15,473 → 12,886** @8K, 1,159 → 965 @128K) and `marlin2b/a100` (562 → 517 @8K, 41 → 38 @128K); `qwen3827b/a100`, `kimik3/a100` and both `deepseek41fnvfp4` A100 rows already used the decimal basis. No fit verdict, GPU count, throughput or $/1M figure moved. Full write-up: [fit-matrix §6.11](matrix/fit-matrix.md#6-open-questions-and-known-uncertainties).

- **2026-09-19 — gap `C2-deepseek-swa-fixed-state` RESOLVED → 2,906,112 B = 2.77 MiB** (43 SWA rings = 40 backbone + 3 MTP, MTP/DSpark enabled — the deployed configuration everywhere in this tree). **2,703,360 B = 2.58 MiB (40 backbone only) is the `--num-speculative-tokens 0` floor**, now labelled as such wherever it appears, not a competing planning value. Recut: [METHODOLOGY §8](METHODOLOGY.md#gpus), both DeepSeek architecture docs' §5.3–§5.6, [fit-matrix §2/§5.2](matrix/fit-matrix.md), and [`deepseek41fnvfp4/h100.md` §1.1–1.3](models/deepseek41fnvfp4/h100.md), which had been the only pair doc still on the 40-ring denominator. Effect is ≤ 2 % of `max_concurrency` at 8K, ~0 % at 1M — no fit verdict, GPU count or $/1M figure moved. `pairs.json`'s `deepseek41fnvfp4/h100` row (898 → **891** @8K) was the one place this had not yet been closed; recut in this pass.

- **2026-09-19 — gap `C6-kimi-k3-minimum-gpu-counts` RESOLVED → 32 GPUs.** 24 H100s is not a legal TP size for Kimi-K3 (hidden size 7168 is not divisible by 24; `gcd(96 heads, 7168) = 32`, valid TP set `{1, 2, 4, 8, 16, 32}`) and is outside [METHODOLOGY §3](METHODOLOGY.md#3-fit)'s topology set; the naive `checkpoint_bytes / n_gpus` share that made 24 look like it fit ignores the measured Marlin resident-weight multiplier, which puts N=24 at 79.5 GB/GPU against an 80 GB card. [gpus/h100.md §2](gpus/h100.md) and `gpus/a100.md` §2/§9 now state **≥ 32 H100/A100** (4 HGX nodes, TP32/EP32); `kimik3/a100`'s `min_gpus` moved 24 → 32 in `pairs.json` in the same pass. `kimik3/h100`'s `min_gpus` was already 32.

- **2026-09-19 — Qwen3.8-27B GDN state-slot clash RESOLVED.** The "H100 `S` and state dtype" disagreement (`gpus/h100.md` 87/8 vs `architecture.md` §10.2's 56/7) is closed: [METHODOLOGY §8](METHODOLOGY.md#gpus) pins **`S` = 5 slots × 78,446,592 B bf16 = 392.2 MB per request** — SGLang's shipped default, the same convention already pinned for Kimi-K3's KDA state — and every `gpus/<gpu>.md` Qwen3.8-27B row is recut to it. What is left open: vLLM's own `S` is unpublished (a 1.5–2× swing), tracked as [qwen3827b/README.md](models/qwen3827b/README.md) open question 2.

- **2026-09-19 — gap `C8-prefix-cache-hit-costed-free` RESOLVED.** Three pair docs ([qwen3827b/mi355x.md §4.4](models/qwen3827b/mi355x.md), [deepseek41f/gb300.md §4.4](models/deepseek41f/gb300.md), [deepseek41fnvfp4/h200.md §4.5](models/deepseek41fnvfp4/h200.md)) costed a prefix-cache hit as **free** (`1 − h`) in their own prefix-caching sensitivity tables while their blended $/1M rows correctly applied [METHODOLOGY §6](METHODOLOGY.md#6-cost)'s 10 %-of-uncached rule; all three are recut to `1 − 0.9h`. No blended cell moved anywhere in the tree — every one was already built on the `0.4125 × c_in + 0.25 × c_out` identity, which *is* the 10 % rule; only the three docs' own sensitivity tables changed. Full write-up: [cost-matrix.md](matrix/cost-matrix.md#amendment-log).

- **2026-09-19 — gap `X1-throughput-basis-mixed-decode-vs-sustained` RESOLVED.** An internal-consistency sweep of `pairs.json` (all 40 rows) found six pairs (`deepseek41f/gb300`, `deepseek41f/a100`, `kimik3/h200`, `kimik3/b300`, `kimik3/mi355x`, `marlin2b/gb300`) mixing the **sustained end-to-end** `out tok/s/GPU` (prefill amortised into the rate) with the **decode-only** rate [METHODOLOGY §4](METHODOLOGY.md#4-throughput) defines. Decode-only is now the uniform `pairs.json` contract for cross-pair comparability; the sustained figure survives on each affected row as `sustained_output_tokens_per_s_per_gpu`, and no pair document's own tables changed — only which of their two already-published bases `pairs.json` carries. [cost-matrix §8](matrix/cost-matrix.md#8-ranking-per-model) notes the practical effect: on the decode-only basis `kimik3/b300` sweeps every ranking column it had previously lost to `kimik3/b200`.

- **2026-09-19 — gap `X7-kimik3-rtx6000-pro-16-vs-19-32-gpus` RESOLVED → 32 GPUs.** [`kimik3/rtx6000-pro.md`](models/kimik3/rtx6000-pro.md) headlined a qualified 16-card deployment (a receipted community deployment resting on an **unverified online MXFP8 weight-only overlay**) against [gpus/rtx6000-pro.md §9g](gpus/rtx6000-pro.md)'s standard-convention floor of `ceil(1,560,860,324,864 / (86.40e9 − 4e9))` = 19 cards → topology step **32**. Resolved to 32 per [METHODOLOGY §7](METHODOLOGY.md#7-methodology)'s rule against sizing on an unsupported engine path; the 16-card case is retained in full as a labelled alternative with its receipts intact. `pairs.json`, [cost-matrix.md §2–§4](matrix/cost-matrix.md), [fit-matrix.md §1/§2/§4.1](matrix/fit-matrix.md) and [kimik3/README.md](models/kimik3/README.md) were all recut; this pass closed two cells the amendment left stale (`cost-matrix.md` §7.2's break-even cell and §7.4's `res1y` cell, both still on the 16-card blended figure).

- **2026-09-19, final consistency pass — small stale-figure sweep.** Three arithmetic leftovers found while cross-checking every pair doc's §0 against `pairs.json`: (1) `deepseek41f/b200`'s S4/max-throughput cell in `pairs.json` had not picked up [b200.md](models/deepseek41f/b200.md)'s own audit fix (5,676 → 4,696 tok/s/GPU), recut to $0.355–$0.828; (2) `deepseek41fnvfp4/mi355x`'s interactive/max-throughput/blended cells likewise lagged [mi355x.md](models/deepseek41fnvfp4/mi355x.md)'s own audit fix ($1.89 → $1.94, $1.06 → $1.12, blended $0.758 → $0.771); (3) several pair docs' own §0 prose (not `pairs.json`, which was already correct) had quoted the `res1y` 1-year-reserved price tier as the band's `low` end instead of `low` = cheapest reputable on-demand — fixed in `deepseek41f/h100.md`, `deepseek41f/b200.md`, `deepseek41fnvfp4/h100.md`, `deepseek41fnvfp4/a100.md`, `deepseek41fnvfp4/rtx6000-pro.md` and `marlin2b/h200.md`. `matrix/fit-matrix.md` and `matrix/cost-matrix.md`'s generated grids were already on the corrected figures in every case but one (the `mi355x` NVFP4 cells above); this file's §3 headline table's Kimi-K3 row was still on the pre-`X1` sustained basis and is recut above to the decode-only figures ($7.3926 / $2.3811).
