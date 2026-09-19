# Cost matrix — $ per 1M tokens, model × GPU

Research date **2026-09-19**. Every number below comes from
[`research/matrix/pairs.json`](pairs.json) — the machine-readable summary of the 40
per-(model, GPU) analyses — or from the document cited in the cell. All grids were
generated with `python3` from `pairs.json` and pasted verbatim; nothing was typed by hand.

Formulas are [`METHODOLOGY.md`](../METHODOLOGY.md) §6 verbatim:

```
cost_per_1M_output = n_gpus × $/GPU-h / (aggregate_output_tok/s × 3600) × 1e6
cost_per_1M_input  = n_gpus × $/GPU-h / (aggregate_prefill_tok/s × 3600) × 1e6
blended            = 0.75 × (0.5 × c_in + 0.5 × 0.10 × c_in) + 0.25 × c_out
                   = 0.4125 × c_in + 0.25 × c_out        (75 % input, half of it cached at 10 %)
```

**Prefix-cache convention.** Prefix-cache hits are costed at 10 % of an uncached prefill
(multiplier `1 − 0.9h`) in every cell, per [METHODOLOGY §6](../METHODOLOGY.md); `(1 − h)`
appears only in TTFT columns.

Scenarios: **S1** = 4K in / 512 out at TPOT ≤ 50 ms (interactive); **S4** = 4K in / 512 out,
no SLO (max throughput). `low` = cheapest reputable on-demand, `high` = cheapest hyperscaler
on-demand, `res1y` = cheapest published 1-year commitment — all three by name from
[`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md).

**Legend.** `⚠️` = the pair's confidence is `not-runnable` or `speculative`; the cost is what
the arithmetic says, not what you can buy today. `\*` = fits and is priced, but
`supported_now: false` in `pairs.json`. Every cell links to the pair document it came from.

### Three cells checked by hand against their pair documents

Not generated — read out of the source document and compared to the grid cell:

| Cell | Grid says (S1 out · input · blended, `low`/`high`) | Pair document says | Where |
|---|---|---|---|
| `deepseek41f/b200` | $0.462/$1.079 · $0.1806/$0.4200 · $0.190/$0.443 | **$0.462/$1.079 · $0.180/$0.420 · $0.190/$0.443** | [b200.md §4.1](../models/deepseek41f/b200.md), the three-row price table |
| `kimik3/b300` | $7.3926/$14.9850 · $1.2921/$2.6188 · $2.3811/$4.8265 | **decode-only**: $7.3926/$14.9850 · $1.2917/$2.6183 · $2.3811/$4.8265; **sustained** (what §4.2 prints): $10.336/$20.951 · — · $3.117/$6.318 | [b300.md §4.2](../models/kimik3/b300.md), the S1 tier table — see the throughput-basis note in §3 |
| `marlin2b/h100` | $0.0237/$0.0509 · $0.0087/$0.0184 · $0.0095/$0.0203 | **$0.0237/$0.0509 · $0.0086/$0.0184 · $0.0095/$0.0203** | [h100.md §4.2](../models/marlin2b/h100.md), `S1 @ KV max (834)` rows |

All three reproduce to rounding on the decode-only basis the grids use. Two further hand checks are recorded in place: the break-even
percentages in §6.2 against [qwen3827b/a100.md §4.4](../models/qwen3827b/a100.md), and the
`res1y` scaling in §7.4 against three documents.

**Verification log.**

- 2026-09-19, gap `X1-throughput-basis-mixed-decode-vs-sustained` — an internal-consistency
  sweep of all 40 rows in `python3` (`concurrency / TPOT / gpus` vs the stated
  `output_tokens_per_s_per_gpu`) found `pairs.json` mixing two definitions. **Resolved in
  favour of decode-only**, now stated in [METHODOLOGY §4](../METHODOLOGY.md); the sustained
  figure moves to a new `sustained_output_tokens_per_s_per_gpu` field. Five rows were re-cut
  (`marlin2b/gb300` S1+S4, `deepseek41f/gb300` S1, `kimik3/b300` S1+S4, `kimik3/mi355x` S1+S4,
  `kimik3/h200` S1) with `cost_per_1m_output_*` and `blended_*` recomputed from the decode-only
  rate. **The three grids were regenerated and only `kimik3/h200` moved** ($51.5504→$51.4488 /
  $102.2222→$102.0208 in §2, $16.1826→$16.1572 / $32.088→$32.0377 in §4, ≤ 0.2 %) — §2–§5
  already carried the decode-only rate; it was `pairs.json` that was on the sustained one. The
  other recut cells reproduce their printed value to ≤ 0.03 % and were left as the pair
  documents print them.
  Downstream: §6.2 (5 cells), §7.1–§7.3 worked examples and §8's Kimi-K3 ranking, where B300
  now sweeps all four columns, and Marlin-2B's S4 tok/s/GPU rank 3 (MI355X 41,304 → GB300
  84,003). Two rows were **not** rewritten, and carry a `throughput_basis_note` in `pairs.json`
  instead: `deepseek41f/a100` S4 (380.1 is a measured decode aggregate; the mismatch is that
  concurrency 256 is the *requested* level at which the engine admits ~128, against a P95 TPOT)
  and `marlin2b/mi355x` S1 (ratio runs the other way — the rate uses the 7.07 ms roofline step,
  `tpot_ms` the 8.07 ms figure that adds the ROCm overhead floor; no drafter exists for Marlin).

- 2026-09-19 — `pairs.json`'s `deepseek41f/b200` S4 block re-cut to the pair document's
  corrected 4,696 tok/s/GPU at TPOT 27.3 ms → **$0.355–$0.828** ([b200.md §4.1](../models/deepseek41f/b200.md));
  §3's S4 grid, its operating-point table and §8's ranking row regenerated from it. The grid and
  the document now agree, so the `‡` footnote and its legend entry are removed and §9.3's
  open question about the disagreement is closed. Blended ($0.190/$0.443) is unchanged — it is
  built on the S1 operating point, which the correction does not touch.

- 2026-09-19, gap `C8-prefix-cache-hit-costed-free` — the prefix-cache convention note above §1
  was added after three pair docs were found costing a hit as free (multiplier `1 − h`) in
  their prefix-caching tables while their own blended rows applied METHODOLOGY §6's 10 % rule
  ([qwen3827b/mi355x.md §4.4](../models/qwen3827b/mi355x.md),
  [deepseek41f/gb300.md §4.4](../models/deepseek41f/gb300.md),
  [deepseek41fnvfp4/h200.md §4.5](../models/deepseek41fnvfp4/h200.md), all now recomputed at
  `1 − 0.9h`). **No cell in this file changes**: every cost cell here was already built on the
  `blended = 0.4125 × c_in + 0.25 × c_out` identity above, which is the 10 % rule.

- 2026-09-19, gap `X5-b200-prefill-roofline-13x-high` — checked whether §5's input grid inherited
  [gpus/b200.md §9.3](../gpus/b200.md)'s withdrawn 123,750 prefill tok/s/GPU roofline. **It did
  not:** every §5 cell is inverted from `pairs.json`'s blended and S1 output fields, and the
  `deepseek41f/b200` cell's derived $0.1806 already reproduces the pair document's measured-rate
  $0.180 (the §5 cross-check table above). No number in this file changed; §5 gained a paragraph
  recording the 13.4× roofline-vs-measurement gap as the calibration to expect on any un-measured
  DSA/CSA-family input cell.

---

## 1. $/GPU-hour inputs used

Taken by name from [`cloud-pricing.md` §5.14 "Planning prices — the three rows every other doc
cites"](../cross-cutting/cloud-pricing.md). No cost figure in this file re-derives a price from
§3–§5 of that document, and none uses a neighbouring GPU's row.

| GPU | `low` — cheapest reputable on-demand | `high` — cheapest hyperscaler on-demand | `res1y` — cheapest 1-year commitment | high ÷ low |
|---|---:|---:|---:|---:|
| **H100** (`h100`) | $3.20 (Hyperstack) | $6.880 (AWS `p5`) | $2.72 (Hyperstack reserved) | 2.15× |
| **H200** (`h200`) | $3.99 (Hyperstack) | $7.912 (AWS `p5en`) | $2.79 (Hyperstack reserved) | 1.98× |
| **B200** (`b200`) | $6.00 (Hyperstack) | $14.00 (OCI `BM.GPU.B200.8`) | $5.10 (Hyperstack reserved) | 2.33× |
| **B300** (`b300`) | $7.40 (Hyperstack) | $15.00 (OCI `BM.GPU.B300.8`) | $7.94 (DigitalOcean 12-mo) | 2.03× |
| **GB300** (`gb300`) | $18.00 (OCI — only published rate) | $18.00 (OCI `BM.GPU.GB300.4`) | ⚠️ none published — use `high` | 1.00× |
| **A100** (`a100`) | $1.59 (RunPod Secure) | $3.431 (AWS `p4de`) | $1.36 (Hyperstack reserved SXM) | 2.16× |
| **RTX PRO 6000** (`rtx6000-pro`) | $1.80 (Nebius) | $4.143 (AWS `g7e.48xlarge`) | $1.30 (Hyperstack reserved) | 2.30× |
| **MI355X** (`mi355x`) | $8.60 (OCI — only published rate) | $8.60 (OCI `BM.GPU.MI355X.8`) | ⚠️ none published — use `high` | 1.00× |

Three things about this table decide half the conclusions below:

- **`gb300` and `mi355x` have `low = high`.** OCI is the only published seller of either, so
  every GB300 and MI355X cost in this file rests on a **single price point**
  ([`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)). They are not cheap GPUs in
  this matrix because they are slow — they are expensive because nobody discounts them yet.
- **`high ÷ low` exceeds 2× on five of eight GPUs**, which is why every grid is a band and
  never a point.
- **`b300`'s `res1y` ($7.94) is *above* its `low` ($7.40)** and `gb300`/`mi355x` have no
  published commitment at all, so "reserved" is not a universal discount — see §7.4.

---

## 2. Interactive — $/1M **output** tokens at TPOT ≤ 50 ms (S1, 4K in / 512 out)

Each cell is `low–high`, at the operating point (`gpus`, `concurrency`) recorded in
`pairs.json` for that pair.

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | [$2.2–$4.73](../models/deepseek41f/h100.md) | [$1.289–$2.556](../models/deepseek41f/h200.md) | [$0.462–$1.079](../models/deepseek41f/b200.md) | [$1.4973–$3.0352](../models/deepseek41f/b300.md) | [$3.9219](../models/deepseek41f/gb300.md) | [$1.162–$2.508](../models/deepseek41f/a100.md) \* | [$2.701–$6.217](../models/deepseek41f/rtx6000-pro.md) | [$1.85](../models/deepseek41f/mi355x.md) |
| **DeepSeek-V4.1-Flash-NVFP4** | [$0.922–$1.983](../models/deepseek41fnvfp4/h100.md) \* | [$1.01–$2.01](../models/deepseek41fnvfp4/h200.md) \* | [$0.565–$1.319](../models/deepseek41fnvfp4/b200.md) | [$4.274–$8.663](../models/deepseek41fnvfp4/b300.md) | [$1.2395](../models/deepseek41fnvfp4/gb300.md) | [$1.162–$2.508](../models/deepseek41fnvfp4/a100.md) ⚠️ | [$1.754–$4.036](../models/deepseek41fnvfp4/rtx6000-pro.md) \* | [$1.94](../models/deepseek41fnvfp4/mi355x.md) ⚠️ |
| **Qwen3.8-27B** | [$0.221–$0.475](../models/qwen3827b/h100.md) | [$0.159–$0.452](../models/qwen3827b/h200.md) | [$0.185–$0.433](../models/qwen3827b/b200.md) | [$0.1649–$0.3343](../models/qwen3827b/b300.md) | [$0.347](../models/qwen3827b/gb300.md) | [$0.218–$0.469](../models/qwen3827b/a100.md) \* | [$0.244–$0.561](../models/qwen3827b/rtx6000-pro.md) | [$0.211](../models/qwen3827b/mi355x.md) |
| **Kimi-K3** | [**infeasible (SLO)**](../models/kimik3/h100.md) | [$51.4488–$102.0208](../models/kimik3/h200.md) | [$16.339–$38.783](../models/kimik3/b200.md) | [$7.3914–$14.9826](../models/kimik3/b300.md) | [$19.74](../models/kimik3/gb300.md) | [$35.21–$75.98](../models/kimik3/a100.md) ⚠️ | [$27.24–$62.69](../models/kimik3/rtx6000-pro.md) ⚠️ | [$21.3675](../models/kimik3/mi355x.md) |
| **Marlin-2B** | [$0.0237–$0.0509](../models/marlin2b/h100.md) | [$0.033–$0.0655](../models/marlin2b/h200.md) | [$0.0235–$0.0547](../models/marlin2b/b200.md) | [$0.022–$0.0447](../models/marlin2b/b300.md) | [$0.0711](../models/marlin2b/gb300.md) | [$0.0221–$0.0476](../models/marlin2b/a100.md) | [$0.0372–$0.0857](../models/marlin2b/rtx6000-pro.md) | [$0.066](../models/marlin2b/mi355x.md) ⚠️ |

Operating points behind the S1 grid (GPUs × concurrency, out tok/s/GPU, TPOT):

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | 8×64 · 404 · 19.8 ms | 8×256 · 860 · 37.2 ms | 8×640 · 3,604 · 22.2 ms | 4×128 · 1,373 · 23.31 ms | 4×128 · 1,275 · 25.1 ms | 8×128 · 380 · 42.1 ms | 4×8 · 185 · 10.8 ms | 4×128 · 1,291 · 24.79 ms |
| **DeepSeek-V4.1-Flash-NVFP4** | 8×256 · 964 · 33.2 ms | 8×256 · 1,094 · 29.2 ms | 4×256 · 2,947 · 21.7 ms | 4×96 · 481 · 49.9 ms | 4×256 · 4,034 · 15.87 ms | 8×128 · 380 · 42.1 ms | 8×114 · 285 · 50 ms | 4×128 · 1,265 · 25.29 ms |
| **Qwen3.8-27B** | 2×128 · 4,027 · 15.89 ms | 1×168 · 6,953 · 24.2 ms | 1×128 · 8,989 · 14.2 ms | 1×256 · 12,463 · 20.54 ms | 1×414 · 14,411 · 28.7 ms | 1×92 · 2,030 · 45.3 ms | 1×64 · 2,052 · 31.2 ms | 1×433 · 11,312 · 38.3 ms |
| **Kimi-K3** | 32×1 · 0.2763 · 113.1 ms | 16×8 · 22 · 23.21 ms | 16×64 · 102 · 39.2 ms | 8×111 · 278 · 49.9 ms | 8×96 · 253 · 46.7 ms | 32×20 · 13 · 49.8 ms | **32**×29 · 18 · 49.4 ms | 8×44 · 112 · 49.2 ms |
| **Marlin-2B** | 1×834 · 37,549 · 22.21 ms | 1×256 · 33,565 · 7.63 ms | 1×256 · 71,040 · 3.6 ms | 1×3,327 · 93,271 · 35.7 ms | 1×256 · 70,330 · 3.64 ms | 1×256 · 20,011 · 12.79 ms | 1×256 · 13,434 · 19.06 ms | 1×256 · 36,232 · 8.1 ms |

**⚠️ `kimik3/rtx6000-pro` moved 16 → 32 GPUs on 2026-09-19** (gap
`X7-kimik3-rtx6000-pro-16-vs-19-32-gpus`), and its cells in §2, §3 and §4 were
regenerated at **32 × $1.80 / $4.143 = $57.60 – $132.58 per node-hour**.
METHODOLOGY §3's floor for this pair is `ceil(1,560,860,324,864 / (86.40e9 −
4e9))` = **19 cards → topology step 32** ([gpus/rtx6000-pro.md §9g](../gpus/rtx6000-pro.md));
the previous 16-card figures ($36.08–$83.05 S1, $34.89–$80.31 S4,
$9.91–$22.81 blended) depended on an **unverified community MXFP8 weight-only
overlay** that METHODOLOGY §7 does not allow as a sizing basis. They survive as
a labelled alternative in [kimik3/rtx6000-pro.md §4](../models/kimik3/rtx6000-pro.md);
the 32-card arithmetic is in **§3.7** of that document. Two health warnings
travel with these three cells: **32 cards is two chassis with no GPU fabric**,
and the **S4 row is a roofline** — it drops the measured 12-sequence engine
admission cap (a ~2 GB/card headroom artifact that does not survive 33.62
GB/card) without a measurement to replace it, and reuses collective constants
estimated at 16 ranks. Read §3's $5.10–$11.73 as an upper bound on what the
silicon could do, **not** as a price anyone has paid.

**The one cell that does not meet its own SLO:** `kimik3/h100` is recorded at concurrency 1
with **TPOT 113.1 ms**, i.e. it never reaches TPOT ≤ 50 ms at any concurrency
([kimik3/h100.md](../models/kimik3/h100.md) §0, §3). It is therefore rendered as
**infeasible (SLO)** above rather than as a price. Treat that cell as *infeasible at S1*,
not as an expensive option.

**The arithmetic behind that cell**, for reference only and **not a price**:
**$3,217.07–$6,916.69 / 1M output** at 32 GPUs × concurrency 1, 0.2763 out tok/s/GPU
(= `1 / 0.1131 s / 32`) — the cost of a single-stream 32-GPU node. `pairs.json` carries this
row as `"slo_met": false` with an explicit `cost_basis`, so it can no longer be read as an
interactive price.

---

## 3. Max throughput — $/1M **output** tokens (S4, 4K in / 512 out, no SLO)

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | [$0.96–$2.07](../models/deepseek41f/h100.md) | [$0.458–$0.909](../models/deepseek41f/h200.md) | [$0.355–$0.828](../models/deepseek41f/b200.md) | [$0.774–$1.569](../models/deepseek41f/b300.md) | [$4.026](../models/deepseek41f/gb300.md) | [$1.162–$2.507](../models/deepseek41f/a100.md) \* | [$0.662–$1.525](../models/deepseek41f/rtx6000-pro.md) | [$1.07](../models/deepseek41f/mi355x.md) |
| **DeepSeek-V4.1-Flash-NVFP4** | [$0.208–$0.446](../models/deepseek41fnvfp4/h100.md) \* | [$0.25–$0.49](../models/deepseek41fnvfp4/h200.md) \* | [$0.048–$0.112](../models/deepseek41fnvfp4/b200.md) | [$0.216–$0.437](../models/deepseek41fnvfp4/b300.md) | [$0.3556](../models/deepseek41fnvfp4/gb300.md) | [$1.162–$2.507](../models/deepseek41fnvfp4/a100.md) ⚠️ | [$0.924–$2.126](../models/deepseek41fnvfp4/rtx6000-pro.md) \* | [$1.12](../models/deepseek41fnvfp4/mi355x.md) ⚠️ |
| **Qwen3.8-27B** | [$0.188–$0.403](../models/qwen3827b/h100.md) | [$0.159–$0.452](../models/qwen3827b/h200.md) | [$0.15–$0.351](../models/qwen3827b/b200.md) | [$0.1527–$0.3095](../models/qwen3827b/b300.md) | [$0.347](../models/qwen3827b/gb300.md) | [$0.218–$0.469](../models/qwen3827b/a100.md) \* | [$0.192–$0.441](../models/qwen3827b/rtx6000-pro.md) | [$0.195](../models/qwen3827b/mi355x.md) |
| **Kimi-K3** | [$32.46–$69.8](../models/kimik3/h100.md) | [$44.33–$87.91](../models/kimik3/h200.md) | [$3.814–$9.053](../models/kimik3/b200.md) | [$3.61–$7.3176](../models/kimik3/b300.md) | [$14.52](../models/kimik3/gb300.md) | [$14.72–$31.75](../models/kimik3/a100.md) ⚠️ | [$5.10–$11.73](../models/kimik3/rtx6000-pro.md) ⚠️ **roofline** | [$21.5409](../models/kimik3/mi355x.md) |
| **Marlin-2B** | [$0.0237–$0.0509](../models/marlin2b/h100.md) | [$0.0292–$0.058](../models/marlin2b/h200.md) | [$0.0195–$0.0455](../models/marlin2b/b200.md) | [$0.022–$0.0447](../models/marlin2b/b300.md) | [$0.0595](../models/marlin2b/gb300.md) | [$0.0188–$0.0406](../models/marlin2b/a100.md) | [$0.0315–$0.0724](../models/marlin2b/rtx6000-pro.md) | [$0.058](../models/marlin2b/mi355x.md) ⚠️ |

Every S4 cell reproduces its document. **Except in kind, `kimik3/rtx6000-pro`:**
its $5.10–$11.73 is a **roofline, not a price** — see the ⚠️ note in §2 and
[kimik3/rtx6000-pro.md §3.7](../models/kimik3/rtx6000-pro.md).

**Throughput basis (§2, §3 and §4 alike).** Six pairs published a **sustained
prefill+decode** rate rather than the decode-only rate
[METHODOLOGY §4](../METHODOLOGY.md) defines: `marlin2b/gb300`, `deepseek41f/gb300`,
`kimik3/b300`, `kimik3/mi355x` and `kimik3/h200` — plus `deepseek41f/a100`, whose row was
found on re-check to be decode-only already (its mismatch is a concurrency/TPOT field
error, flagged in `pairs.json`). **Those cells now use the decode-only rate for
comparability**, and each sustained rate is shown in the pair document
([marlin2b/gb300.md §3.5](../models/marlin2b/gb300.md),
[kimik3/b300.md §4.2](../models/kimik3/b300.md), and the corresponding §3/§4 elsewhere) and
carried as `sustained_output_tokens_per_s_per_gpu` in [`pairs.json`](pairs.json). The two
bases are never compared cell-for-cell. Regenerated 2026-09-19: only `kimik3/h200`'s §2/§4
cells moved (≤ 0.2 %); the other grids already carried the decode-only rate — it was
`pairs.json` that carried the sustained one.

Operating points behind the S4 grid (GPUs × concurrency, out tok/s/GPU, TPOT):

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | 8×256 · 924 · 34.6 ms | 8×1,024 · 2,418 · 52.9 ms | 8×1,024 · 4,696 · 27.3 ms | 2×128 · 2,656 · 24.1 ms | 4×256 · 1,242 · 50 ms | 8×256 · 380 · 45.6 ms | 4×256 · 755 · 84.8 ms | 4×256 · 2,241 · 28.56 ms |
| **DeepSeek-V4.1-Flash-NVFP4** | 8×2,048 · 4,283 · 59.78 ms | 8×2,048 · 4,461 · 57.4 ms | 4×3,557 · 34,705 · 25.6 ms | 4×2,048 · 9,531 · 53.7 ms | 4×1,024 · 14,060 · 18.21 ms | 8×256 · 380 · 84.2 ms | 8×256 · 541 · 59.1 ms | 4×256 · 2,246 · 28.49 ms |
| **Qwen3.8-27B** | 2×193 · 4,739 · 20.36 ms | 1×168 · 6,953 · 24.2 ms | 1×242 · 11,082 · 21.8 ms | 1×384 · 13,461 · 28.5 ms | 1×414 · 14,411 · 28.7 ms | 1×92 · 2,030 · 45.3 ms | 1×111 · 2,610 · 42.5 ms | 1×1,025 · 12,225 · 83.8 ms |
| **Kimi-K3** | 32×128 · 27 · 146.1 ms | 16×74 · 25 · 185 ms | 16×411 · 437 · 58.8 ms | 8×256 · 569 · 56.2 ms | 8×147 · 344 · 51.6 ms | 32×143 · 30 · 148.9 ms | **32**×465 · 98 · 148.1 ms | 8×70 · 111 · 78.9 ms |
| **Marlin-2B** | 1×834 · 37,549 · 22.21 ms | 1×1,551 · 37,903 · 40.92 ms | 1×1,024 · 85,544 · 11.97 ms | 1×3,327 · 93,271 · 35.7 ms | 1×1,024 · 84,003 · 12.19 ms | 1×887 · 23,450 · 37.83 ms | 1×1,019 · 15,886 · 64.14 ms | 1×3,292 · 41,304 · 79.7 ms |

S4 carries no SLO, so several of these points sit well past 50 ms TPOT (`qwen3827b/mi355x`
83.8 ms, `marlin2b/mi355x` 79.7 ms, `kimik3/h200` 185 ms) and several sit *inside* it
(`deepseek41f/b200` 27.3 ms) — where S4 TPOT ≤ 50 ms, S1 and S4 are the same operating point
and the two grids agree by construction.

---

## 4. Blended — $/1M tokens, 75 % input (half of it cached at 10 %) / 25 % output

`blended = 0.4125 × c_in + 0.25 × c_out`, at the **S1** operating point, per
[METHODOLOGY §6](../METHODOLOGY.md). This is the number to compare against a vendor's list
price, because a vendor's list price is also a blend.

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | [$0.595–$1.279](../models/deepseek41f/h100.md) | [$0.379–$0.752](../models/deepseek41f/h200.md) | [$0.19–$0.443](../models/deepseek41f/b200.md) | [$0.4809–$0.9747](../models/deepseek41f/b300.md) | [$1.2935](../models/deepseek41f/gb300.md) | [$0.361–$0.778](../models/deepseek41f/a100.md) \* | [$0.7181–$1.6528](../models/deepseek41f/rtx6000-pro.md) | [$0.748](../models/deepseek41f/mi355x.md) |
| **DeepSeek-V4.1-Flash-NVFP4** | [$0.254–$0.546](../models/deepseek41fnvfp4/h100.md) \* | [$0.2818–$0.5588](../models/deepseek41fnvfp4/h200.md) \* | [$0.1485–$0.3464](../models/deepseek41fnvfp4/b200.md) | [$1.16–$2.351](../models/deepseek41fnvfp4/b300.md) | [$0.33108](../models/deepseek41fnvfp4/gb300.md) | [$0.354–$0.764](../models/deepseek41fnvfp4/a100.md) ⚠️ | [$0.4812–$1.1076](../models/deepseek41fnvfp4/rtx6000-pro.md) \* | [$0.771](../models/deepseek41fnvfp4/mi355x.md) ⚠️ |
| **Qwen3.8-27B** | [$0.089–$0.192](../models/qwen3827b/h100.md) | [$0.082–$0.196](../models/qwen3827b/h200.md) | [$0.0633–$0.1477](../models/qwen3827b/b200.md) | [$0.0602–$0.1221](../models/qwen3827b/b300.md) | [$0.134](../models/qwen3827b/gb300.md) | [$0.126–$0.271](../models/qwen3827b/a100.md) \* | [$0.087–$0.199](../models/qwen3827b/rtx6000-pro.md) | [$0.08](../models/qwen3827b/mi355x.md) |
| **Kimi-K3** | [$8.38–$18.01](../models/kimik3/h100.md) | [$16.1572–$32.0377](../models/kimik3/h200.md) | [$4.402–$10.449](../models/kimik3/b200.md) | [$2.3808–$4.8259](../models/kimik3/b300.md) | [$5.61](../models/kimik3/gb300.md) | [$10.51–$22.67](../models/kimik3/a100.md) ⚠️ | [$7.70–$17.72](../models/kimik3/rtx6000-pro.md) ⚠️ | [$5.7619](../models/kimik3/mi355x.md) |
| **Marlin-2B** | [$0.0095–$0.0203](../models/marlin2b/h100.md) | [$0.0127–$0.0251](../models/marlin2b/h200.md) | [$0.0095–$0.0221](../models/marlin2b/b200.md) | [$0.0092–$0.0185](../models/marlin2b/b300.md) | [$0.0294](../models/marlin2b/gb300.md) | [$0.0112–$0.0242](../models/marlin2b/a100.md) | [$0.0135–$0.0311](../models/marlin2b/rtx6000-pro.md) | [$0.0236](../models/marlin2b/mi355x.md) ⚠️ |

One blended cell is built on a different operating point than the rest: **`kimik3/h100`'s
$8.38–$18.01 uses the S4 output cost ($32.46–$69.80), not the S1 one** — `0.4125 × 0.633 +
0.25 × 32.46 = 8.376`. That is consistent with the pair document (whose S1 point is
concurrency 1 and meaningless as a blend) but it is **not** comparable cell-for-cell with the
other 39. Flagged, not silently rewritten. **Resolved: yes — S4, now declared in
`pairs.json`** as `"blended_basis": "S4"` on that pair; all 39 other rows blend at S1.

---

## 5. $/1M **input** tokens (prefill, uncached)

`pairs.json` does not carry an input-cost field, so this grid is recovered exactly by
inverting METHODOLOGY §6's blended identity, in `python3`:

```
c_in = (blended − 0.25 × c_out_S1) / 0.4125
```

This is algebra on two published fields, not a new estimate. It was cross-checked against the
figure printed in the pair document itself for **19 of the 40 pairs** and reproduces it to
within rounding everywhere (see the list under the grid).

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | [$0.1091–$0.2339](../models/deepseek41f/h100.md) | [$0.1376–$0.2739](../models/deepseek41f/h200.md) | [$0.1806–$0.42](../models/deepseek41f/b200.md) | [$0.2584–$0.5234](../models/deepseek41f/b300.md) | [$0.7588](../models/deepseek41f/gb300.md) | [$0.1709–$0.3661](../models/deepseek41f/a100.md) \* | [$0.1039–$0.2389](../models/deepseek41f/rtx6000-pro.md) | [$0.6921](../models/deepseek41f/mi355x.md) |
| **DeepSeek-V4.1-Flash-NVFP4** | [$0.057–$0.1218](../models/deepseek41fnvfp4/h100.md) \* | [$0.071–$0.1365](../models/deepseek41fnvfp4/h200.md) \* | [$0.0176–$0.0404](../models/deepseek41fnvfp4/b200.md) | [$0.2218–$0.4491](../models/deepseek41fnvfp4/b300.md) | [$0.0514](../models/deepseek41fnvfp4/gb300.md) | [$0.1539–$0.3321](../models/deepseek41fnvfp4/a100.md) ⚠️ | [$0.1035–$0.239](../models/deepseek41fnvfp4/rtx6000-pro.md) \* | [$0.6921](../models/deepseek41fnvfp4/mi355x.md) ⚠️ |
| **Qwen3.8-27B** | [$0.0818–$0.1776](../models/qwen3827b/h100.md) | [$0.1024–$0.2012](../models/qwen3827b/h200.md) | [$0.0413–$0.0956](../models/qwen3827b/b200.md) | [$0.046–$0.0934](../models/qwen3827b/b300.md) | [$0.1145](../models/qwen3827b/gb300.md) | [$0.1733–$0.3727](../models/qwen3827b/a100.md) \* | [$0.063–$0.1424](../models/qwen3827b/rtx6000-pro.md) | [$0.0661](../models/qwen3827b/mi355x.md) |
| **Kimi-K3** | [$0.633–$1.361](../models/kimik3/h100.md)† | [$7.9879–$15.8364](../models/kimik3/h200.md) | [$0.7691–$1.8261](../models/kimik3/b200.md) | [$1.2921–$2.6188](../models/kimik3/b300.md) | [$1.6364](../models/kimik3/gb300.md) | [$4.1394–$8.9091](../models/kimik3/a100.md) ⚠️ | [$2.1576–$4.9636](../models/kimik3/rtx6000-pro.md) | [$1.0182](../models/kimik3/mi355x.md) |
| **Marlin-2B** | [$0.0087–$0.0184](../models/marlin2b/h100.md) | [$0.0108–$0.0211](../models/marlin2b/h200.md) | [$0.0088–$0.0204](../models/marlin2b/b200.md) | [$0.009–$0.0178](../models/marlin2b/b300.md) | [$0.0281](../models/marlin2b/gb300.md) | [$0.0138–$0.0298](../models/marlin2b/a100.md) | [$0.0102–$0.0234](../models/marlin2b/rtx6000-pro.md) | [$0.0172](../models/marlin2b/mi355x.md) ⚠️ |

† **`kimik3/h100` is the one pair where the identity fails** (it returns a negative number,
because that row's blended figure is S4-based — `blended_basis: "S4"`, see §4). The value shown is the document's own:
**$0.633 / $1.361 / $0.538** at `low` / `high` / `res1y`, from
[kimik3/h100.md](../models/kimik3/h100.md) §4.1, computed from a 44,935 node tok/s S1 prefill rate.

**Cross-checks of the derived input grid against the pair documents** (derived → doc-stated):

| Pair | derived `low` | document says | Pair | derived `low` | document says |
|---|---:|---:|---|---:|---:|
| [deepseek41f/b200](../models/deepseek41f/b200.md) | $0.1806 | **$0.180** | [deepseek41f/h200](../models/deepseek41f/h200.md) | $0.1376 | **$0.1386** |
| [deepseek41f/gb300](../models/deepseek41f/gb300.md) | $0.7588 | **$0.757** | [deepseek41f/rtx6000-pro](../models/deepseek41f/rtx6000-pro.md) | $0.1039 | **$0.104** |
| [ds…nvfp4/h200](../models/deepseek41fnvfp4/h200.md) | $0.071 | **$0.0692** | [ds…nvfp4/b300](../models/deepseek41fnvfp4/b300.md) | $0.2218 | **$0.22182** |
| [ds…nvfp4/gb300](../models/deepseek41fnvfp4/gb300.md) | $0.0514 | **$0.05142** | [ds…nvfp4/a100](../models/deepseek41fnvfp4/a100.md) | $0.1539 | **$0.154** |
| [qwen3827b/h200](../models/qwen3827b/h200.md) | $0.1024 | **$0.1012** | [qwen3827b/gb300](../models/qwen3827b/gb300.md) | $0.1145 | **$0.1149** |
| [qwen3827b/a100](../models/qwen3827b/a100.md) | $0.1733 | **$0.173** | [qwen3827b/mi355x](../models/qwen3827b/mi355x.md) | $0.0661 | **$0.067** |
| [kimik3/h200](../models/kimik3/h200.md) | $7.9879 | **$7.98** | [kimik3/b300](../models/kimik3/b300.md) | $1.2921 | **$1.2917** |
| [kimik3/gb300](../models/kimik3/gb300.md) | $1.6364 | **$1.633** | [kimik3/mi355x](../models/kimik3/mi355x.md) | $1.0182 | **$1.010** |
| [marlin2b/h100](../models/marlin2b/h100.md) | $0.0087 | **$0.0086** | [marlin2b/b200](../models/marlin2b/b200.md) | $0.0088 | **$0.0087** |
| [marlin2b/rtx6000-pro](../models/marlin2b/rtx6000-pro.md) | $0.0102 | **$0.0102** | | | |

Largest deviation: 2.6 % (`deepseek41fnvfp4/h200`), from `pairs.json` rounding its blended
field to four decimals. The remaining 20 cells are the same algebra on the same two fields and
are labelled `est.` by inheritance from their pair document.

**What the input grid does *not* say.** Input cost is a prefill-rate number, and prefill rates
in these documents rest on MFU assumptions far more than decode rates do. Three pairs say so
explicitly: [deepseek41f/a100.md](../models/deepseek41f/a100.md) (*"No prefill throughput
measurement — §4's `$/1M input` rests on a roofline"*), [qwen3827b/rtx6000-pro.md](../models/qwen3827b/rtx6000-pro.md)
(a measured TTFT says the estimate is **2.7× pessimistic** at long context, *"the single
largest calibration error in the document"*), and [qwen3827b/gb300.md](../models/qwen3827b/gb300.md)
(architecture.md's full-FP4-peak rate vs. this doc's mixed-checkpoint rate *"moves $/1M input
from $0.073 to $0.115"*). ⚠️ **TO BE VERIFIED** for all three.

**How wrong a roofline can be — measured.** On `deepseek41f/b200` the two methods are both on
record: [gpus/b200.md §9.3](../gpus/b200.md) held a GEMM-only `2 × active × T` roofline of
**123,750 prefill tok/s/GPU**, and [deepseek41f/b200.md §3.1](../models/deepseek41f/b200.md)
measured **9,267** from vLLM PR #56686's step times — **13.4×** apart, because a
sparse/indexer-attention model's prefill is dominated by the indexer scan, top-k, Engram gathers
and mHC mixing rather than the expert GEMMs (gpus/b200.md §9.3 was corrected to 9,267 on
2026-09-19). **This grid's B200 cell is safe** — $0.1806 was inverted from the pair document's
measured-rate blended figure and reproduces its stated $0.180. Read it as the scale of the error
to expect in any *un-measured* DSA/CSA-family input cell here, not as a defect in this row.

---

## 6. Vendor API prices and break-even utilisation

### 6.1 Published vendor prices

| Model | Vendor / endpoint | $/1M input (miss) | $/1M input (hit) | $/1M output | Vendor **blended** on the same 75/25 mix | Source |
|---|---|---:|---:|---:|---:|---|
| DeepSeek-V4.1-Flash (both checkpoints) | DeepSeek first-party `deepseek-flash`, off-peak | $0.15 | $0.003 | **$0.60** | **$0.2074** | [deepseek41f/b200.md §4.3](../models/deepseek41f/b200.md) |
| — same, peak | | $0.30 | $0.006 | $1.20 | $0.4147 | [deepseek41f/rtx6000-pro.md §4.3](../models/deepseek41f/rtx6000-pro.md) |
| Qwen3.8-27B | Qwen Cloud list | $0.50 | $0.05 | **$3.00** | **$0.956** | [qwen3827b/a100.md §4.4](../models/qwen3827b/a100.md) |
| — same, third-party FP8 market | OpenRouter | $0.20–0.30 | — | $2.20–2.55 | $0.659 | [qwen3827b/a100.md §4.4](../models/qwen3827b/a100.md) |
| Kimi-K3 | Moonshot `platform.kimi.ai` | $3.00 | $0.30 | **$15.00** | **$4.99** | [kimik3/h200.md §4.5](../models/kimik3/h200.md) |
| Marlin-2B | **none** — no vendor API exists | — | — | — | — | [marlin2b/gb300.md §4.4](../models/marlin2b/gb300.md) |

DeepSeek's own cached-input ratio is **2 %**, not METHODOLOGY's generic 10 % self-serving
assumption; Moonshot's and Qwen's are 10 %. The vendor blends above use each vendor's own
ratio, per [METHODOLOGY §6](../METHODOLOGY.md).

### 6.2 Break-even utilisation

`u* = self-hosted blended ÷ vendor blended` — the fraction of wall-clock the rented GPUs must
spend producing at the S1 rate for self-hosting to match the API's blended price. Above 100 %
self-hosting cannot match the API at any utilisation. Cells are `low-tier / high-tier`.

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | [287 % ✗ / 617 % ✗](../models/deepseek41f/h100.md) | [183 % ✗ / 363 % ✗](../models/deepseek41f/h200.md) | [**92 %** / 214 % ✗](../models/deepseek41f/b200.md) | [232 % ✗ / 470 % ✗](../models/deepseek41f/b300.md) | [624 % ✗ / 624 % ✗](../models/deepseek41f/gb300.md) | [174 % ✗ / 375 % ✗](../models/deepseek41f/a100.md) \* | [346 % ✗ / 797 % ✗](../models/deepseek41f/rtx6000-pro.md) | [361 % ✗ / 361 % ✗](../models/deepseek41f/mi355x.md) |
| **DeepSeek-V4.1-Flash-NVFP4** | [122 % ✗ / 263 % ✗](../models/deepseek41fnvfp4/h100.md) \* | [136 % ✗ / 269 % ✗](../models/deepseek41fnvfp4/h200.md) \* | [**72 %** / 167 % ✗](../models/deepseek41fnvfp4/b200.md) | [559 % ✗ / 1,134 % ✗](../models/deepseek41fnvfp4/b300.md) | [160 % ✗ / 160 % ✗](../models/deepseek41fnvfp4/gb300.md) | [171 % ✗ / 368 % ✗](../models/deepseek41fnvfp4/a100.md) ⚠️ | [232 % ✗ / 534 % ✗](../models/deepseek41fnvfp4/rtx6000-pro.md) \* | [365 % ✗ / 365 % ✗](../models/deepseek41fnvfp4/mi355x.md) ⚠️ |
| **Qwen3.8-27B** | [**9 %** / **20 %**](../models/qwen3827b/h100.md) | [**9 %** / **21 %**](../models/qwen3827b/h200.md) | [**7 %** / **15 %**](../models/qwen3827b/b200.md) | [**6 %** / **13 %**](../models/qwen3827b/b300.md) | [**14 %** / **14 %**](../models/qwen3827b/gb300.md) | [**13 %** / **28 %**](../models/qwen3827b/a100.md) \* | [**9 %** / **21 %**](../models/qwen3827b/rtx6000-pro.md) | [**8 %** / **8 %**](../models/qwen3827b/mi355x.md) |
| **Kimi-K3** | [168 % ✗ / 361 % ✗](../models/kimik3/h100.md) | [324 % ✗ / 642 % ✗](../models/kimik3/h200.md) | [**88 %** / 209 % ✗](../models/kimik3/b200.md) | [**48 %** / **97 %**](../models/kimik3/b300.md) | [112 % ✗ / 112 % ✗](../models/kimik3/gb300.md) | [211 % ✗ / 454 % ✗](../models/kimik3/a100.md) ⚠️ | [154 % ✗ / 355 % ✗](../models/kimik3/rtx6000-pro.md) | [116 % ✗ / 116 % ✗](../models/kimik3/mi355x.md) |
| **Marlin-2B** | — | — | — | — | — | — | — | — |

Denominators: DeepSeek $0.2074 (off-peak), Qwen Cloud $0.956, Moonshot $4.99. `✗` = above
100 %, i.e. impossible. Marlin-2B has no API, so it has no break-even; its economics are
absolute (`marlin2b` §8 below), not relative.

**Validation of this table against a source document:** [qwen3827b/a100.md §4.4](../models/qwen3827b/a100.md)
prints *"low $1.59 RunPod Secure SXM vs Qwen Cloud 13.2 %, high $3.431 AWS p4de vs Qwen Cloud
28.4 %"*. This generator computes 13.2 % / 28.3 % from `pairs.json` for the same cell.

What the table says in one line per model:

- **Qwen3.8-27B: self-hosting wins everywhere**, 6–28 % break-even. Even the single worst cell
  (A100 at AWS list) needs the GPU busy only ~28 % of the time.
- **Marlin-2B: not a contest.** There is no API; the absolute blended cost is $0.009–$0.031 on
  everything except GB300.
- **DeepSeek-V4.1-Flash: one cell out of sixteen clears the bar.** The base checkpoint beats
  DeepSeek's own $0.2074 blend nowhere; its best cell is B200 at **92 %**, i.e. a node that is
  busy 92 % of every hour merely draws level. The NVFP4 checkpoint on B200 is the only
  comfortable win at **72 %** — and it is also the cell whose operating point (concurrency
  3,557) is the least defensible in the matrix (§9.2). On a hyperscaler price, no DeepSeek cell
  is within 1.6× of break-even.
- **Kimi-K3: one node, and only on the cheap tier.** On the decode-only basis (§3's
  throughput-basis note) the cheapest cell is **B300 at 48 %/97 %** — the first Kimi cell that
  clears 100 % on *both* tiers, though the high tier only draws level. Everything else is
  above break-even, and the *absolute* self-hosted blend ($2.38–$32.04) is 0.5×–6.4×
  Moonshot's own $4.99. Outside B300 you are renting 8–32 GPUs to reproduce a price the
  vendor already sells. ⚠️ B300's 48 % rests on an S1 point at batch 111 with a **10.1 s
  batch-wave TTFT** ([kimik3/b300.md §4.2](../models/kimik3/b300.md)); decode-only cost says
  nothing about that queueing delay.

---

## 7. Sensitivity

### 7.1 ±20 % MBU

Decode in these documents is memory-bandwidth bound at the S1/S4 batches, so
[METHODOLOGY §4](../METHODOLOGY.md)'s `decode_step_time ≈ bytes_per_step / (HBM_BW × MBU)`
makes throughput **linear in MBU** and cost **inversely linear**:

| MBU change | throughput | $/1M output and $/1M blended |
|---|---:|---:|
| **+20 %** (e.g. 0.60 → 0.72) | ×1.20 | **×0.833** |
| baseline | ×1.00 | ×1.000 |
| **−20 %** (e.g. 0.60 → 0.48) | ×0.80 | **×1.250** |

Worked on the two extremes of the blended grid at `low`: `marlin2b/b300` $0.0092 → $0.0077 /
$0.0115; `kimik3/h200` $16.1572 → $13.46 / $20.20.

[qwen3827b/gb300.md](../models/qwen3827b/gb300.md) states the same mechanism independently for
its own numbers — *"the whole §3 table scales linearly with MBU; the 0.60–0.75 band is a ±13 %
swing on every cost figure"* — and flags that **no end-to-end MBU/MFU figure for GB300 has been
published**. [serving-optimizations.md §4.4](../cross-cutting/serving-optimizations.md) says the
same for every generation: ⚠️ **per-GPU-generation MBU/MFU tables are TO BE VERIFIED**; the
METHODOLOGY planning bands (0.6–0.8 Hopper, 0.5–0.7 first-gen Blackwell, 0.4–0.6 MI355X/ROCm)
stand until a measurement replaces them.

**Where the ×0.833 / ×1.25 rule does not apply.** Three pairs are *not* bandwidth-bound at
their operating point, so scaling MBU moves nothing:

- [kimik3/h200.md](../models/kimik3/h200.md): *"a decode step that is latency-, not
  bandwidth-bound (implied MBU 2–20 %)"* against a 0.65 roofline assumption.
- [kimik3/a100.md](../models/kimik3/a100.md): plans **MBU 0.20** against METHODOLOGY's 0.6–0.8
  Hopper/Ampere band and says so explicitly — every A100 kernel in that path is a fallback.
- [qwen3827b/a100.md](../models/qwen3827b/a100.md): compute-bound at S1, which is also why
  speculation stops paying there (§7.3).

### 7.2 Prefix-cache hit rate 0 / 50 / 90 %

The blended definition already bakes in **h = 50 %**. Re-evaluating
`blended(h) = 0.75 × c_in × ((1−h) + 0.10 h) + 0.25 × c_out` gives the input-leg multipliers
**1.000 / 0.550 / 0.190** — the third is `(1−0.9) + 0.9 × 0.1`, printed identically by
[deepseek41fnvfp4/b300.md](../models/deepseek41fnvfp4/b300.md) (`$0.2218 → $0.122 → $0.0421`
per 1M input, `×1.000 / ×0.550 / ×0.190`).

Blended $/1M at `low` as the hit rate moves, for each model's cheapest-blended GPU:

| Model | GPU | h = 0 % | h = 50 % (as published) | h = 90 % | output share of the blend at h = 50 % |
|---|---|---:|---:|---:|---:|
| DeepSeek-V4.1-Flash | [B200](../models/deepseek41f/b200.md) | $0.251 | $0.19 | $0.1412 | 61 % |
| DeepSeek-V4.1-Flash-NVFP4 | [B200](../models/deepseek41fnvfp4/b200.md) | $0.1544 | $0.1485 | $0.1437 | 95 % |
| Qwen3.8-27B | [B300](../models/qwen3827b/b300.md) | $0.0757 | $0.0602 | $0.0478 | 68 % |
| Kimi-K3 | [B300](../models/kimik3/b300.md) | $2.8172 | $2.3811 | $2.0323 | 78 % |
| Marlin-2B | [B300](../models/marlin2b/b300.md) | $0.0122 | $0.0092 | $0.0068 | 60 % |

The "output share" column is the whole story: **once output is ~80 % of the blend, prefix
caching stops being a cost lever and becomes a TTFT lever.** Kimi-K3 on B300 is the extreme —
[kimik3/h200.md](../models/kimik3/h200.md) makes the same point for its own pair
(*"the blend is already output-dominated (output is 83 % of the blended cost)"*), where a 90 %
hit moves blended only $16.1572 → $14.00.

Two caveats the pair documents raise, both cost-relevant:

- **The assumed hit rate can be far too low.** The agentic traces for DeepSeek-V4.1-Flash run
  at a **94.3–98.3 % measured hit rate** (median 97.6 %) —
  [deepseek41fnvfp4/a100.md](../models/deepseek41fnvfp4/a100.md) — and
  [deepseek41f/mi355x.md](../models/deepseek41f/mi355x.md) records **99.7 %** on InferenceX
  traces, at which point *"input cost effectively vanishes and the blended figure collapses
  onto the output term"*.
- **Or far too high.** [marlin2b/gb300.md](../models/marlin2b/gb300.md): hit rate ≈ **0 %** for
  video, so the 10 % cached term is *"charged but never earned"* and the realistic blend is
  **+32 %** ($0.0294 → $0.0388 on the decode-only basis; $0.0680 → $0.0776 as the document
  prints it on the sustained basis). On the AMD path, **SGLang on MI350X must run
  `--disable-radix-cache`** and forfeits prefix caching entirely — *"worth roughly 2× on
  blended cost for agentic traffic"* ([deepseek41f/mi355x.md](../models/deepseek41f/mi355x.md)).

### 7.3 Speculative decoding on / off

Every DeepSeek and Kimi cost in §2–§4 is **with** speculation on (DSpark / MTP); it is on in
the measured runs. Turning it off is the single largest swing in the matrix:

| Pair | with speculation | without | effect on $/1M output |
|---|---|---|---|
| [deepseek41f/gb300](../models/deepseek41f/gb300.md) | DSpark γ=5, acceptance 3.5 — 6,330 tok/s, $0.79 | 2,061 tok/s, $2.43 | **3.1× worse** |
| [deepseek41f/b300](../models/deepseek41f/b300.md) | $1.50 at $7.40 | $4.69 | **3.1× worse** (~68 % of throughput lost) |
| [deepseek41f/mi355x](../models/deepseek41f/mi355x.md) | S1 point as published | $12.44 at batch 38 | **6.7× worse**, and TPOT ≤ 50 ms becomes unreachable above batch 39 |
| [deepseek41f/h100](../models/deepseek41f/h100.md) | $2.20 at batch 64 | $7.96 "no-spec floor" at batch 32 | **3.6× worse** |
| [kimik3/b300](../models/kimik3/b300.md) | +DSpark 1.64× → $4.508, blended $1.660 | $7.3926, blended $2.3811 | **1.64× better with it** (the document prints the same ratio on its sustained basis: $6.302/$2.108 vs $10.336/$3.117) |
| [qwen3827b/a100](../models/qwen3827b/a100.md) | MTP γ=3, accept 4.28, vcr 0 → $0.051 | vcr 0.5 → $0.218 | **up to 4.3× better**, or nothing |

**Do not plan with the acceptance constant.** [deepseek41f/h100.md](../models/deepseek41f/h100.md)
establishes that the widely quoted **3.51** is a benchmark constant
(`"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51`), not a measurement;
the real sm_90 measurement is **2.82**, with per-position acceptance decaying to 0.096 at
position 5. [deepseek41f/h200.md](../models/deepseek41f/h200.md) lists DSpark acceptance on H200
as ⚠️ **TO BE VERIFIED** (only a lower bound `a ≥ 1.15` is derivable). And
[qwen3827b/a100.md](../models/qwen3827b/a100.md) is blunt that at a compute-bound operating
point `vcr → 1` and speculation stops paying — its own range is *"between 2× cheaper and no
change"*.

### 7.4 Reserved (`res1y`) pricing

Cost is linear in $/GPU-hour, so the `res1y` blend is the `low` blend scaled by
`res1y ÷ low`. Multipliers, from §1: **H100 0.850 · H200 0.699 · B200 0.850 · B300 1.073 ·
GB300 1.000 · A100 0.855 · RTX PRO 6000 0.722 · MI355X 1.000**.

Blended $/1M at `res1y`:

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | [$0.50575](../models/deepseek41f/h100.md) | [$0.26502](../models/deepseek41f/h200.md) | [$0.1615](../models/deepseek41f/b200.md) | [$0.51599](../models/deepseek41f/b300.md) | [$1.2935](../models/deepseek41f/gb300.md) | [$0.30878](../models/deepseek41f/a100.md) \* | [$0.51863](../models/deepseek41f/rtx6000-pro.md) | [$0.748](../models/deepseek41f/mi355x.md) |
| **DeepSeek-V4.1-Flash-NVFP4** | [$0.2159](../models/deepseek41fnvfp4/h100.md) \* | [$0.19705](../models/deepseek41fnvfp4/h200.md) \* | [$0.12622](../models/deepseek41fnvfp4/b200.md) | [$1.24465](../models/deepseek41fnvfp4/b300.md) | [$0.33108](../models/deepseek41fnvfp4/gb300.md) | [$0.30279](../models/deepseek41fnvfp4/a100.md) ⚠️ | [$0.34753](../models/deepseek41fnvfp4/rtx6000-pro.md) \* | [$0.758](../models/deepseek41fnvfp4/mi355x.md) ⚠️ |
| **Qwen3.8-27B** | [$0.07565](../models/qwen3827b/h100.md) | [$0.05734](../models/qwen3827b/h200.md) | [$0.0538](../models/qwen3827b/b200.md) | [$0.06459](../models/qwen3827b/b300.md) | [$0.134](../models/qwen3827b/gb300.md) | [$0.10777](../models/qwen3827b/a100.md) \* | [$0.06283](../models/qwen3827b/rtx6000-pro.md) | [$0.08](../models/qwen3827b/mi355x.md) |
| **Kimi-K3** | [$7.123](../models/kimik3/h100.md) | [$11.31164](../models/kimik3/h200.md) | [$3.7417](../models/kimik3/b200.md) | [$2.55460](../models/kimik3/b300.md) | [$5.61](../models/kimik3/gb300.md) | [$8.98969](../models/kimik3/a100.md) ⚠️ | [$5.5611](../models/kimik3/rtx6000-pro.md) | [$5.7619](../models/kimik3/mi355x.md) |
| **Marlin-2B** | [$0.00808](../models/marlin2b/h100.md) | [$0.00888](../models/marlin2b/h200.md) | [$0.00807](../models/marlin2b/b200.md) | [$0.00987](../models/marlin2b/b300.md) | [$0.0294](../models/marlin2b/gb300.md) | [$0.00958](../models/marlin2b/a100.md) | [$0.00975](../models/marlin2b/rtx6000-pro.md) | [$0.0236](../models/marlin2b/mi355x.md) ⚠️ |

Three checks that this scaling is what the pair documents do: `deepseek41f/h100` → **$0.506**,
document prints **$0.506**; `qwen3827b/a100` → **$0.108**, document prints **$0.108**;
`deepseek41fnvfp4/h200` → **$0.1970**, document prints **$0.1970**.

**Reserved is not a discount on three of eight GPUs.** `b300` costs **+7.3 %** reserved
($7.94 DigitalOcean 12-mo vs $7.40 Hyperstack on-demand); `gb300` and `mi355x` have **no
published commitment price at all** and fall back to `high`
([`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)). The real reserved wins are
**H200 (−30 %)** and **RTX PRO 6000 (−28 %)**, and they reorder the cheapest-blended column:
at `res1y`, Qwen3.8-27B's cheapest blend moves from B300 ($0.0602 on-demand) to **B200
($0.0538)**, and Marlin-2B's from B300 to **H100 / B200 ($0.0081)**.

---

## 8. Ranking per model

`low`-tier unless stated. Ties broken by the cheaper `high` tier.

### DeepSeek-V4.1-Flash (`deepseek41f`)

| Rank | Cheapest at the interactive SLO (S1, $/1M out) | Cheapest at max throughput (S4, $/1M out) | Best tokens/s/GPU (S4) | Cheapest blended |
|---|---|---|---|---|
| 1 | [B200](../models/deepseek41f/b200.md) **$0.462** | [B200](../models/deepseek41f/b200.md) **$0.355** | [B200](../models/deepseek41f/b200.md) **4,696** tok/s/GPU | [B200](../models/deepseek41f/b200.md) **$0.19** |
| 2 | [A100](../models/deepseek41f/a100.md) **$1.162** \* | [H200](../models/deepseek41f/h200.md) **$0.458** | [B300](../models/deepseek41f/b300.md) **2,656** tok/s/GPU | [A100](../models/deepseek41f/a100.md) **$0.361** \* |
| 3 | [H200](../models/deepseek41f/h200.md) **$1.289** | [RTX PRO 6000](../models/deepseek41f/rtx6000-pro.md) **$0.662** | [H200](../models/deepseek41f/h200.md) **2,418** tok/s/GPU | [H200](../models/deepseek41f/h200.md) **$0.379** |

### DeepSeek-V4.1-Flash-NVFP4 (`deepseek41fnvfp4`)

| Rank | Cheapest at the interactive SLO (S1, $/1M out) | Cheapest at max throughput (S4, $/1M out) | Best tokens/s/GPU (S4) | Cheapest blended |
|---|---|---|---|---|
| 1 | [B200](../models/deepseek41fnvfp4/b200.md) **$0.565** | [B200](../models/deepseek41fnvfp4/b200.md) **$0.048** | [B200](../models/deepseek41fnvfp4/b200.md) **34,705** tok/s/GPU | [B200](../models/deepseek41fnvfp4/b200.md) **$0.1485** |
| 2 | [H100](../models/deepseek41fnvfp4/h100.md) **$0.922** \* | [H100](../models/deepseek41fnvfp4/h100.md) **$0.208** \* | [GB300](../models/deepseek41fnvfp4/gb300.md) **14,060** tok/s/GPU | [H100](../models/deepseek41fnvfp4/h100.md) **$0.254** \* |
| 3 | [H200](../models/deepseek41fnvfp4/h200.md) **$1.01** \* | [B300](../models/deepseek41fnvfp4/b300.md) **$0.216** | [B300](../models/deepseek41fnvfp4/b300.md) **9,531** tok/s/GPU | [H200](../models/deepseek41fnvfp4/h200.md) **$0.2818** \* |

### Qwen3.8-27B (`qwen3827b`)

| Rank | Cheapest at the interactive SLO (S1, $/1M out) | Cheapest at max throughput (S4, $/1M out) | Best tokens/s/GPU (S4) | Cheapest blended |
|---|---|---|---|---|
| 1 | [H200](../models/qwen3827b/h200.md) **$0.159** | [B200](../models/qwen3827b/b200.md) **$0.15** | [GB300](../models/qwen3827b/gb300.md) **14,411** tok/s/GPU | [B300](../models/qwen3827b/b300.md) **$0.0602** |
| 2 | [B300](../models/qwen3827b/b300.md) **$0.1649** | [B300](../models/qwen3827b/b300.md) **$0.1527** | [B300](../models/qwen3827b/b300.md) **13,461** tok/s/GPU | [B200](../models/qwen3827b/b200.md) **$0.0633** |
| 3 | [B200](../models/qwen3827b/b200.md) **$0.185** | [H200](../models/qwen3827b/h200.md) **$0.159** | [MI355X](../models/qwen3827b/mi355x.md) **12,225** tok/s/GPU | [MI355X](../models/qwen3827b/mi355x.md) **$0.08** |

### Kimi-K3 (`kimik3`)

| Rank | Cheapest at the interactive SLO (S1, $/1M out) | Cheapest at max throughput (S4, $/1M out) | Best tokens/s/GPU (S4) | Cheapest blended |
|---|---|---|---|---|
| 1 | [B300](../models/kimik3/b300.md) **$7.3926** | [B300](../models/kimik3/b300.md) **$3.6101** | [B300](../models/kimik3/b300.md) **569** tok/s/GPU | [B300](../models/kimik3/b300.md) **$2.3811** |
| 2 | [B200](../models/kimik3/b200.md) **$16.339** | [B200](../models/kimik3/b200.md) **$3.814** | [B200](../models/kimik3/b200.md) **437** tok/s/GPU | [B200](../models/kimik3/b200.md) **$4.402** |
| 3 | [GB300](../models/kimik3/gb300.md) **$19.74** | [GB300](../models/kimik3/gb300.md) **$14.52** | [GB300](../models/kimik3/gb300.md) **344** tok/s/GPU | [GB300](../models/kimik3/gb300.md) **$5.61** |

Excluded from the S1 column (TPOT > 50 ms at its recorded operating point): H100 (113.1 ms).

**Recut 2026-09-19** on the decode-only basis (§3's throughput-basis note): B300 was carrying
`kimik3/b300.md` §4.2's sustained request-cycle rate (198.9 / 321.6 tok/s/GPU), which put B200
first at S4 and on tok/s/GPU. At the decode-only rate (278 / 569) **B300 sweeps all four
columns** — it was never actually slower than B200, it was measured against a different clock.

### Marlin-2B (`marlin2b`)

| Rank | Cheapest at the interactive SLO (S1, $/1M out) | Cheapest at max throughput (S4, $/1M out) | Best tokens/s/GPU (S4) | Cheapest blended |
|---|---|---|---|---|
| 1 | [B300](../models/marlin2b/b300.md) **$0.022** | [A100](../models/marlin2b/a100.md) **$0.0188** | [B300](../models/marlin2b/b300.md) **93,271** tok/s/GPU | [B300](../models/marlin2b/b300.md) **$0.0092** |
| 2 | [A100](../models/marlin2b/a100.md) **$0.0221** | [B200](../models/marlin2b/b200.md) **$0.0195** | [B200](../models/marlin2b/b200.md) **85,544** tok/s/GPU | [H100](../models/marlin2b/h100.md) **$0.0095** |
| 3 | [B200](../models/marlin2b/b200.md) **$0.0235** | [B300](../models/marlin2b/b300.md) **$0.022** | [GB300](../models/marlin2b/gb300.md) **84,003** tok/s/GPU | [B200](../models/marlin2b/b200.md) **$0.0095** |

**Reading the four columns together.** They do not agree, and the disagreement is the
point:

- **Cheapest at S1 ≠ cheapest at S4 for two of five models** (Qwen3.8-27B H200 → B200,
  Marlin-2B B300 → A100; Kimi-K3 stays on B300 in both since the 2026-09-19 decode-only recut). Only the two DeepSeek checkpoints put B200 at
  the top of both columns.
- **Best tokens/s/GPU is rarely the cheapest cell.** GB300 takes a top-3 throughput slot for
  three of five models but a top-3 *cost* slot only for Kimi-K3 — where every option is
  expensive anyway. At $18.00/GPU-h it is 2.4× H100's `low` with no second seller (§1).
- **No `supported_now: false` cell wins a column**, but four place second or third
  (`deepseek41f` on A100 — second in *two* columns, S1 and blended — plus `deepseek41fnvfp4`
  on H100 and H200, `marlin2b` on MI355X). Marked `\*` and `⚠️`; see §9.2.
- **The `deepseek41f` S4 column uses the `pairs.json` figure**, now re-cut to the pair
  document's corrected $0.355 (§3). B200 still ranks first — H200 is $0.458 — so the ranking
  is unchanged, only the margin.

---

## 9. Confidence and open questions

### 9.1 Confidence label per pair, from `pairs.json`

| Model \ GPU | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-V4.1-Flash** | [estimate](../models/deepseek41f/h100.md) · 12 ⚠️ | [estimate](../models/deepseek41f/h200.md) · 16 ⚠️ | [estimate](../models/deepseek41f/b200.md) · 16 ⚠️ | [measured](../models/deepseek41f/b300.md) · 23 ⚠️ | [measured](../models/deepseek41f/gb300.md) · 17 ⚠️ | [measured](../models/deepseek41f/a100.md) · 23 ⚠️ | [measured](../models/deepseek41f/rtx6000-pro.md) · 20 ⚠️ | [estimate](../models/deepseek41f/mi355x.md) · 30 ⚠️ |
| **DeepSeek-V4.1-Flash-NVFP4** | [estimate](../models/deepseek41fnvfp4/h100.md) · 11 ⚠️ | [estimate](../models/deepseek41fnvfp4/h200.md) · 20 ⚠️ | [estimate](../models/deepseek41fnvfp4/b200.md) · 14 ⚠️ | [estimate](../models/deepseek41fnvfp4/b300.md) · 22 ⚠️ | [estimate](../models/deepseek41fnvfp4/gb300.md) · 20 ⚠️ | [not-runnable](../models/deepseek41fnvfp4/a100.md) · 22 ⚠️ | [estimate](../models/deepseek41fnvfp4/rtx6000-pro.md) · 18 ⚠️ | [not-runnable](../models/deepseek41fnvfp4/mi355x.md) · 24 ⚠️ |
| **Qwen3.8-27B** | [estimate](../models/qwen3827b/h100.md) · 26 ⚠️ | [estimate](../models/qwen3827b/h200.md) · 13 ⚠️ | [estimate](../models/qwen3827b/b200.md) · 16 ⚠️ | [estimate](../models/qwen3827b/b300.md) · 17 ⚠️ | [estimate](../models/qwen3827b/gb300.md) · 18 ⚠️ | [estimate](../models/qwen3827b/a100.md) · 18 ⚠️ | [estimate](../models/qwen3827b/rtx6000-pro.md) · 14 ⚠️ | [estimate](../models/qwen3827b/mi355x.md) · 19 ⚠️ |
| **Kimi-K3** | [estimate](../models/kimik3/h100.md) · 14 ⚠️ | [estimate](../models/kimik3/h200.md) · 12 ⚠️ | [estimate](../models/kimik3/b200.md) · 18 ⚠️ | [estimate](../models/kimik3/b300.md) · 18 ⚠️ | [estimate](../models/kimik3/gb300.md) · 16 ⚠️ | [not-runnable](../models/kimik3/a100.md) · 16 ⚠️ | [estimate](../models/kimik3/rtx6000-pro.md) · 15 ⚠️ | [measured](../models/kimik3/mi355x.md) · 19 ⚠️ |
| **Marlin-2B** | [estimate](../models/marlin2b/h100.md) · 29 ⚠️ | [estimate](../models/marlin2b/h200.md) · 23 ⚠️ | [estimate](../models/marlin2b/b200.md) · 16 ⚠️ | [estimate](../models/marlin2b/b300.md) · 23 ⚠️ | [estimate](../models/marlin2b/gb300.md) · 22 ⚠️ | [estimate](../models/marlin2b/a100.md) · 17 ⚠️ | [estimate](../models/marlin2b/rtx6000-pro.md) · 26 ⚠️ | [speculative](../models/marlin2b/mi355x.md) · 16 ⚠️ |

The second figure in each cell is that document's own count of **⚠️ TO BE VERIFIED** items.
Totals across the 40 pairs: **5 `measured`, 31 `estimate`,
3 `not-runnable`, 1 `speculative`**;
**749 unverified items** and **32 flagged cross-document contradictions** in total.

**Only 5 of the 40 pairs are labelled `measured`.** Every other cell in
every grid above is a roofline with a stated MBU/MFU assumption. That is the single most important caveat on every
grid above.

### 9.2 Cells that are priced but cannot be bought today

| Pair | Why |
|---|---|
| [deepseek41f/a100](../models/deepseek41f/a100.md) | `supported_now: false`, confidence `measured` — measured, but on a **community `vllm-backport` fork + 24-file SM80 patch set, on 8× A800**; not upstream, not vendor-verified. [gpus/a100.md §9](../gpus/a100.md), [inference-engines.md §3.2](../cross-cutting/inference-engines.md) and [flash-attention.md §16.1](../cross-cutting/flash-attention.md) all scope A100 out |
| [deepseek41fnvfp4/h100](../models/deepseek41fnvfp4/h100.md) | `supported_now: false`, confidence `estimate` |
| [deepseek41fnvfp4/h200](../models/deepseek41fnvfp4/h200.md) | `supported_now: false`, confidence `estimate` |
| [deepseek41fnvfp4/a100](../models/deepseek41fnvfp4/a100.md) | `supported_now: false`, confidence `not-runnable` |
| [deepseek41fnvfp4/rtx6000-pro](../models/deepseek41fnvfp4/rtx6000-pro.md) | `supported_now: false`, confidence `estimate` |
| [deepseek41fnvfp4/mi355x](../models/deepseek41fnvfp4/mi355x.md) | `supported_now: false`, confidence `not-runnable` |
| [qwen3827b/a100](../models/qwen3827b/a100.md) | `supported_now: false`, confidence `estimate` |
| [kimik3/a100](../models/kimik3/a100.md) | `supported_now: false`, confidence `not-runnable` |
| [marlin2b/mi355x](../models/marlin2b/mi355x.md) | `supported_now: false`, confidence `speculative` |

Five of the eight `deepseek41fnvfp4` cells are in this list, plus one each for
DeepSeek-V4.1-Flash (A100), Qwen3.8-27B, Kimi-K3 and Marlin-2B. Their costs
are arithmetically correct and operationally unavailable as of 2026-09-19. Note especially
that [deepseek41fnvfp4/b200](../models/deepseek41fnvfp4/b200.md) — the **cheapest S4 cell in the
entire matrix at $0.048/1M** — *is* supported, but its operating point is concurrency 3,557 at
34,705 tok/s/GPU, which is 13.6× the next-largest concurrency in its own row. ⚠️ **TO BE
VERIFIED** before anyone plans against it.

### 9.3 Open questions this matrix cannot close

1. **`kimik3/h100`'s blended figure uses S4 output, not S1**, unlike the other 39 rows (§4),
   and its S1 cell fails its own SLO at 113.1 ms (§2). **Declared, not closed:** `pairs.json`
   now states both (`blended_basis: "S4"`, `slo_met: false`), so §2 publishes *infeasible
   (SLO)* instead of a price — but the blended cell is still not cell-for-cell comparable
   with the other 39.
2. **`deepseek41f/h100`'s measured and estimated costs differ by 4–9×.** `pairs.json` carries
   the `est.` operating point ($2.20–$4.73 at 404.4 tok/s/GPU); the same document's headline
   carries the **measured** agentic point — *"89.8 out tok/s/GPU MEASURED … measured cost
   $9.90–$21.28/1M output"* — and its §4.3 calls the spread *"the honest uncertainty of this
   section."* Both cited; the grid shows the `est.` figure.
3. **GB300 and MI355X economics rest on one price each.** OCI is the only published seller of
   either ([`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)). If a second seller
   appears at neocloud rates, every GB300 and MI355X cell moves by the same factor and several
   §8 rankings invert.
4. **No per-GPU-generation MBU/MFU measurement exists**
   ([serving-optimizations.md §4.4](../cross-cutting/serving-optimizations.md)), so §7.1's
   ±20 % band is the honest width of every cost cell that is not `measured`.
5. **Prefill rates are the weakest input to the §5 grid** — one pair is 2.7× pessimistic
   against its own measured TTFT, one has no prefill measurement at all, and one disagrees with
   its own architecture.md by 1.6× (§5).
6. **DSpark / MTP acceptance is a benchmark constant on most of these pairs, not a
   measurement** (§7.3), and it is worth 3–6.7× on DeepSeek output cost.
7. **The `res1y` row does not exist for GB300 or MI355X and is *above* on-demand for B300**
   (§7.4), so "reserved pricing" is not a uniform lever across this matrix.

---

*Generated from [`pairs.json`](pairs.json) with `python3`; grids pasted verbatim. Source of
every price: [`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md). Formulas:
[`METHODOLOGY.md`](../METHODOLOGY.md) §4 and §6.*

## Amendment log

- **2026-09-19 — gap `X7-kimik3-rtx6000-pro-16-vs-19-32-gpus` RESOLVED → 32 GPUs; §2, §3 and §4 regenerated for `kimik3/rtx6000-pro`.** The pair was priced at 16 cards on the strength of a receipted community deployment whose fit depends on an **unverified online MXFP8 weight-only overlay from an unaffiliated fork**; [gpus/rtx6000-pro.md §9g](../gpus/rtx6000-pro.md)'s standard-convention floor is `ceil(1,560,860,324,864 / (86.40e9 − 4e9))` = **19 → topology step 32** (re-verified exact with `python3`). Resolved to **32** per METHODOLOGY §7 (do not size on an unsupported engine path). Recomputed with `python3` at **32 × $1.80 / $4.143 = $57.60 / $132.58 per node-hour** from [kimik3/rtx6000-pro.md §3.7](../models/kimik3/rtx6000-pro.md): **§2 S1 $36.08–$83.05 → $27.24–$62.69** (op point `16×11 · 14 · 49.6 ms` → `32×29 · 18 · 49.4 ms`); **§3 S4 $34.89–$80.31 → $5.10–$11.73** (`16×12 · 14 · 52.3 ms` → `32×465 · 98 · 148.1 ms`), flagged a **roofline** because it drops the measured 12-sequence engine cap without a replacement measurement; **§4 blended $9.91–$22.81 → $7.70–$17.72** (the $/1M **input** row in §5 is unchanged at $2.1576–$4.9636 — node cost and prefill rate both scale ×2). `pairs.json` updated (`min_gpus`/`recommended_gpus` 32, new `support_caveat`, both operating points and all four output-cost fields; validated with `json.load`), as were [fit-matrix.md](fit-matrix.md) §1/§2/§4.1/§6.7, [gpus/rtx6000-pro.md §9g](../gpus/rtx6000-pro.md) and [models/kimik3/README.md](../models/kimik3/README.md). **Not regenerated, and now stale:** §7's break-even-utilisation cell for this pair (199 % / 457 %) and §8's $7.15722 row still sit on the 16-card output costs — out of the scope this amendment was given, flagged here rather than silently left.
- **2026-09-19 — `deepseek41f/a100` demoted to `supported_now: false`** in [`pairs.json`](pairs.json) (confidence stays `measured`, new `support_caveat` field): the measurement is real but runs on a community `vllm-backport` fork + 24-file SM80 patch set on 8× A800, which METHODOLOGY §7 says must not be presented as supported, and [gpus/a100.md §9](../gpus/a100.md), [inference-engines.md §3.2](../cross-cutting/inference-engines.md) and [flash-attention.md §16.1](../cross-cutting/flash-attention.md) independently scope A100 out. No cost number changed; the `\*` marker now applies to its cells in §2, §3, §4, §5, §6.2, §7.4 and §8, and the pair is listed in §9.2.
- **2026-09-19, final consistency pass.** Closed two stale cells the `X7` amendment above flagged and left: `kimik3/rtx6000-pro`'s §7.2 break-even utilisation recut from the 16-card **199 % / 457 %** to the 32-card **154 % / 355 %** (`$7.70 / $17.72` blended ÷ Moonshot's `$4.99`), and §7.4's `res1y` cell recut from **$7.15722** to **$5.5611** (`$7.70 × $1.30/$1.80`). Also: `deepseek41f/b200`'s S4/max-throughput cell (§3) and `deepseek41fnvfp4/mi355x`'s S1/S4/blended cells (§2–§4) had not picked up those pair documents' own 2026-09-19 audit fixes (4,696 tok/s/GPU; $1.94/$1.12) — `matrix/pairs.json` was recut to match and this file's §3 `deepseek41f/b200` cell was already correct (generated after the doc fix); §2–§4's three `deepseek41fnvfp4/mi355x` cells recut **$1.89→$1.94**, **$1.06→$1.12**, **$0.758→$0.771**.
