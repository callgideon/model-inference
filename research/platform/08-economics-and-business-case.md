# Economics: when a distilled specialist beats the incumbent frontier model

Research date **2026-09-19**. This is document 08 in the component decomposition
set by
[`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) §6.
It answers one question and refuses to answer it with a single number:
**under what conditions is replacing a frontier API call with a distilled
specialist worth the money, and how long does it take to pay back?**

**Conventions.** The `low`/`high`/`res1y` price tiers, the blended-cost formula
and the scenario definitions (S1 = 4K in / 512 out at TPOT ≤ 50 ms, S4 = no SLO)
are [`research/METHODOLOGY.md`](../METHODOLOGY.md) — not re-derived here.
Self-hosted $/1M figures are named cells from
[`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md). Serving,
utilisation, autoscaling and serverless-provider economics are
[`research/scaling/`](../scaling/), in particular
[`07-cost-engineering.md`](../scaling/07-cost-engineering.md),
and [`12-inference-providers.md`](../scaling/12-inference-providers.md)
(**re-pointed 2026-09-19**: the `12a-serverless-gpu-platforms.md` /
`12b-model-api-providers.md` pair this document was written against has been
merged into that single file; §5.2/§5.3/§5.5 keep their numbers, 12a's §3.4
normalisation table is now §2.3 and §5.6).
Terminology (S1–S9 loop stages, invariants I1–I7, the §3.2 method ladder) is
doc 00's.

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source fetched 2026-09-19 (vendor pricing page, official docs, arXiv abstract, product page). |
| **⚠️ TO BE VERIFIED** | No primary source found, or the claim is an inference; the reasoning is stated inline. |
| `est.` | Arithmetic from METHODOLOGY formulas or from sourced inputs. Every `est.` table in this document was generated with `python3`, not typed. |
| `meas.` | A published measurement, cited. |
| `vendor` | A vendor's own claim about its own product. Treated as marketing until replicated. |

> **Research-method caveat, stated up front.** As with doc 00, this document was
> produced **without WebSearch** — the session's search budget (200/200) was
> exhausted before this agent started. Every source below was reached by known
> URL or by following links from a fetched page, and **every one was actually
> fetched**; none is recalled from memory. The consequence is the same as doc
> 00's: §4's case-study survey is a survey of vendors I could name and fetch,
> **not** an exhaustive scan of published distillation case studies. Absence
> from §4 is not evidence of absence. See the first Open Question.

---

## 0. The five results that matter

If you read nothing else:

1. **The honest cost multiple is single-digit to low-double-digit, not 138×.**
   ⚠️ **Corrected 2026-09-19:** this line previously read "3–30×", a range that
   appears nowhere in §1.2 — the honest (batch + h=90 %) column there runs
   **0.9× (`gpt-5-nano`) to 115.7× (GPT-6 Astra)**, and the cheapest-tier
   comparison the rest of this bullet actually makes is **0.9–11.6×**. Quote one
   of those, with the incumbent named. Against the customer's
   *current* model at list price, self-hosted Qwen3.8-27B is 55–276× cheaper.
   Against the same vendor's **cheapest tier, batched, with a 90 % prompt-cache
   hit** — which is one config change away and free — it is **0.9–12×**. At the
   very bottom (`gpt-5-nano`, batched, 90 % cached, blended **$0.0536**/1M
   `est.` [[src](https://developers.openai.com/api/docs/pricing)]) the frontier
   vendor is *already cheaper per token than our best self-hosted cell*
   ($0.0602/1M, [cost-matrix §4](../matrix/cost-matrix.md)). §1.2, §1.8.
2. **Above ~10M requests/month the token price stops mattering and the GPU floor
   decides.** At 2M requests/month a Qwen3.8-27B replica on B300 runs at **9.3 %
   utilisation** `est.`; delivered cost is then $0.65/1M, not $0.0602/1M. The
   single highest-leverage economic decision in the platform is **right-sizing
   the GPU class and packing tenants onto it**, not squeezing the kernel. §1.3,
   §1.7.
3. **The value of the confidence protocol is an order of magnitude larger than
   the token saving, and it is the product.** A 0.5 % regression rate on a 2M
   req/month support chat at $12 per escalated ticket costs **$120,000/month**
   `est.` — 18× the $6,477/month saved by distilling off a batched, cached
   GPT-5.6 Sol. Customers are not buying cheaper tokens; they are buying a
   defensible reason to believe nothing broke. §3.
4. **Video inverts the usual shape.** Frontier video understanding is priced
   **per second of video** (Gemini: ~100 tokens/s low-res
   [[src](https://ai.google.dev/gemini-api/docs/video-understanding)]; Twelve
   Labs: $1.75/hour of video [[src](https://www.twelvelabs.io/pricing)]), while
   Marlin-2B's 240-frame cap prices **per clip, flat**
   ([`models/marlin2b/architecture.md` §6.3](../models/marlin2b/architecture.md)).
   The cost ratio is therefore a function of **clip length**, running from ~34×
   at 1-minute clips to **~1,600×** at 60-minute clips `est.`. But the absolute
   spend is so small that at 50K videos/month of 5-minute clips the incumbent
   (Gemini batch, $780/mo) is **cheaper than the GPU floor** ($2,628/mo for two
   RTX PRO 6000). §5.4, §7.3.
5. **The observability layer's pricing model can eat the entire saving.** At 50M
   requests/month, the same traces cost **$9,501/mo** on Langfuse Cloud,
   **$8,649/mo** on Braintrust, and **$33,698/mo** on W&B Weave `est.` — against
   a gross saving of $36,421/mo vs Gemini 3.8 Flash. Self-hosting Langfuse (free
   licence [[src](https://langfuse.com/pricing)]) is not a preference, it is a
   requirement of the business model. §1.5.

---

## 1. Unit economics model

### 1.1 The formula

Everything in this document is one identity, applied twice — once to the
incumbent, once to the candidate — plus amortisation.

```
Monthly cost of an option
  = FIXED                                  (GPU floor, platform fees, seats)
  + VOLUME × MARGINAL_PER_REQUEST          (tokens or GPU-seconds)
  + LOOP                                   (traces, judging, re-annotation, shadow)
  + ONE_OFF / AMORTISATION_MONTHS          (annotation, training, evals, engineering)
```

For an API-priced option, `FIXED = 0` and

```
MARGINAL_PER_REQUEST = [ (1−h)·T_in·c_in + h·T_in·c_cached + T_out·c_out ] / 1e6 × B
```

where `h` is the prompt-cache hit rate on input tokens, `B` is the batch
multiplier (0.5 where the vendor offers a 50 % batch discount and the workload
tolerates asynchrony), and `c_*` are the published $/1M rates.

For a self-hosted option,

```
FIXED    = n_gpus × $/GPU-hour × 730
MARGINAL = (T_in·c_in^self + T_out·c_out^self) / 1e6    ← from cost-matrix.md
```

and the **delivered** cost per 1M tokens is `blended / u`, where `u` is
utilisation. That division is the whole story of §1.3 and it is the term every
vendor comparison omits.

To keep this comparable to the repo's grids, the **blended $/1M** convention of
[METHODOLOGY §6](../METHODOLOGY.md) is used throughout:

```
blended(h, s_out) = (1−s_out)·[(1−h)·c_in + h·c_cached] + s_out·c_out
```

with the repo default `s_out = 0.25`, `h = 0.5`. Per METHODOLOGY §6, vendor
comparisons use each vendor's **published** cached-input rate, never a generic
10 % — which matters for Claude Fable 5.1 (cache read is 2.5 % of input, not
10 %) and for `gpt-5-nano` / `gpt-4.1-nano`, whose cached rates are 10 % and
25 % of input respectively
[[src](https://developers.openai.com/api/docs/pricing)].

### 1.2 The incumbent side — list price, and the three levers that shrink it

**Published first-party prices, 2026-09-19.** Fetched, not recalled.

| Model | Input $/1M | Cached in $/1M | Cache write $/1M | Output $/1M | Source |
|---|---:|---:|---:|---:|---|
| **GPT-6 Astra** | 10.00 | 1.00 | — | 50.00 | [OpenAI](https://developers.openai.com/api/docs/pricing) |
| **GPT-5.6 Sol** | 4.00 | 0.40 | — | 20.00 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| **GPT-5.6 Terra** | 2.00 | 0.20 | — | 12.00 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| **GPT-5.6 Luna** | 0.20 | 0.02 | — | 1.20 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| gpt-5-mini | 0.25 | 0.025 | — | 2.00 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| **gpt-5-nano** | 0.05 | 0.005 | — | 0.40 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| gpt-4.1-mini | 0.40 | 0.10 | — | 1.60 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| gpt-4.1-nano | 0.10 | 0.025 | — | 0.40 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| **Claude Fable 5.1** | 10.00 | 0.25 | 12.50 | 50.00 | [Anthropic](https://claude.com/pricing) |
| **Claude Opus 5** | 5.00 | 0.50 | 6.25 | 25.00 | [ibid.](https://claude.com/pricing) |
| Claude Sonnet 5 | 2.00 | 0.20 | 2.50 | 10.00 | [ibid.](https://claude.com/pricing) |
| Claude Haiku 4.5 | 1.00 | 0.10 | 1.25 | 5.00 | [ibid.](https://claude.com/pricing) |
| **Gemini 3.8 Flash** | 0.75 | 0.075 | — | 3.75 | [Google](https://ai.google.dev/gemini-api/docs/pricing) ¹ |
| Gemini 3.5 Flash-Lite | 0.30 | 0.03 | — | 2.50 | [ibid.](https://ai.google.dev/gemini-api/docs/pricing) |
| Gemini 3.1 Pro **Preview** (≤200k ctx) ⚠️ | 2.00 | 0.20 | — | 12.00 | [ibid.](https://ai.google.dev/gemini-api/docs/pricing) |
| Gemini 3.1 Pro (>200k ctx) | 4.00 | 0.40 | — | 18.00 | [ibid.](https://ai.google.dev/gemini-api/docs/pricing) |

¹ The page labels the 3.1 Pro rows **"Gemini 3.1 Pro Preview"** (re-fetched
2026-09-19) — a preview SKU's price is not a contractual rate and can move
without the notice a GA price carries; every §7.2 row built on it inherits that
⚠️. Gemini 3.8 Flash's $0.75/$3.75 is a **promotional rate through 2026-12-31**;
the page states it reverts to **$1.50/$7.50** after that
[[src](https://ai.google.dev/gemini-api/docs/pricing)]. Any business case built
on Gemini Flash must be re-run at the post-2026 price — that alone doubles the
incumbent side of §7.2's extraction example and turns a "never pays back" into a
9-month payback. Google also charges **$0.50/1M tokens/hour** for context-cache
storage on 3.8 Flash and **$4.50** on 3.1 Pro [[ibid.](https://ai.google.dev/gemini-api/docs/pricing)],
a line item OpenAI and Anthropic do not have.

**All three vendors publish a 50 % batch discount.** Anthropic's page says
[**"Save 50% with batch processing"**](https://claude.com/pricing) verbatim
(re-fetched 2026-09-19). ⚠️ **Corrected 2026-09-19 for OpenAI:** the sentence
*"Batch pricing reduces costs to 50 % of Standard rates across all models"* was
attributed to OpenAI's pricing page as a quotation and **is not on it** — the
page ships a separate Batch table whose cells are half the Standard cells but
carries **no verbal statement of the discount**; the 50 % is read off the
numbers, not quoted [[src](https://developers.openai.com/api/docs/pricing)].
⚠️ **Corrected for Google too:** its Batch columns are *not* "exactly half"
everywhere — Gemini 3.1 Pro's batch **cached-input** rate is $0.20, identical to
Standard, and 3.5 Flash-Lite's is $0.02 against a Standard $0.03. The
non-cached cells are half [[src](https://ai.google.dev/gemini-api/docs/pricing)].

**Do batch and prompt caching compose? — partially RESOLVED 2026-09-19, in the
document's favour.** Every "batch + 90 % cached" figure here assumes a batched
request still gets the cached-input rate on its cached prefix.

- **Google: confirmed compose.** The pricing page publishes an explicit
  **Batch cached-input** rate per model — Gemini 3.8 Flash **$0.0375**, exactly
  half its Standard cached input of $0.075 — so the two discounts stack on a
  published rate card [[src](https://ai.google.dev/gemini-api/docs/pricing)].
  (Gemini 3.1 Pro is the exception noted above: its batch cached rate equals its
  standard cached rate, so caching composes but is *not* additionally halved.)
- **OpenAI and Anthropic: ⚠️ still TO BE VERIFIED.** Neither page publishes a
  batch × cached cell; both publish a batch table and a cached column
  independently, and neither states whether a cached prefix inside a batch job
  bills at the cached rate, the batched rate, or both
  [[src](https://developers.openai.com/api/docs/pricing)]
  [[src](https://claude.com/pricing)].

If they do *not* compose on OpenAI/Anthropic, the honest incumbent floor for
those two is the better of the two discounts, not the product — which
**raises** the incumbent's price and **improves** every payback number here by
roughly 20–40 %. §7.1 (GPT-5.6 Sol) and §7.2's Terra/Sonnet rows are the ones
exposed; §7.2's decisive **Gemini** rows are now settled.

**Blended $/1M at the repo's 25 %-output convention**, across cache hit rates,
`est.`:

| Model | h=0 % | h=50 % | h=90 % | h=90 % **+ batch** |
|---|---:|---:|---:|---:|
| GPT-6 Astra | 20.0000 | 16.6250 | 13.9250 | **6.9625** |
| Claude Fable 5.1 | 20.0000 | 16.3438 | 13.4187 | **6.7094** |
| Claude Opus 5 | 10.0000 | 8.3125 | 6.9625 | **3.4813** |
| GPT-5.6 Sol | 8.0000 | 6.6500 | 5.5700 | **2.7850** |
| GPT-5.6 Terra | 4.5000 | 3.8250 | 3.2850 | **1.6425** |
| Gemini 3.1 Pro (≤200k) | 4.5000 | 3.8250 | 3.2850 | **1.6425** |
| Claude Sonnet 5 | 4.0000 | 3.3250 | 2.7850 | **1.3925** |
| Claude Haiku 4.5 | 2.0000 | 1.6625 | 1.3925 | **0.6963** |
| Gemini 3.8 Flash | 1.5000 | 1.2469 | 1.0444 | **0.5222** |
| Gemini 3.5 Flash-Lite | 0.8500 | 0.7488 | 0.6677 | **0.3339** |
| gpt-5-mini | 0.6875 | 0.6031 | 0.5356 | **0.2678** |
| GPT-5.6 Luna | 0.4500 | 0.3825 | 0.3285 | **0.1643** |
| **gpt-5-nano** | 0.1375 | 0.1206 | 0.1071 | **0.0536** |

Three structural facts fall out of this table, and each one is a lever the
incumbent vendor has already shipped:

1. **Caching has a hard floor at the output share.** Moving Opus 5 from h=0 to
   h=100 % takes the blend only from $10.00 to $6.625 — a 34 % cut — because
   output is 25 % of the blend and no amount of caching touches it. At the repo
   convention the floor is `0.25 × c_out`: **$6.25 for Opus 5, $12.50 for Fable
   5.1, $5.00 for GPT-5.6 Sol** `est.`. **Corollary for the sales motion:** a
   customer with a huge system prompt and short answers has *already* captured
   most of the caching win and the remaining gap to a distilled model is nearly
   all output-side. A customer with short prompts and long answers has captured
   nothing from caching and will not — their gap is also output-side. Caching is
   a TTFT lever masquerading as a cost lever; the same point is made
   independently for self-hosting in
   [`cost-matrix.md` §7.2](../matrix/cost-matrix.md).
2. **Batch is a flat 50 % and costs the customer only latency.** Quoting list
   price to a customer who batches is a pitch that will be caught in the first
   technical review.
3. **Tier-down is free.** `gpt-5-nano` blended-batched-cached at **$0.0536/1M**
   is *below* the best self-hosted Qwen3.8-27B cell ($0.0602/1M at B300 `low`,
   [cost-matrix §4](../matrix/cost-matrix.md)). Luna at $0.1643 is 2.7× it.
   **This is the number that disciplines the whole business.**

**Honest cost ratios**, incumbent ÷ self-hosted Qwen3.8-27B at $0.0602/1M `est.`:

| Incumbent | ÷ Qwen, list @ h=50 % | ÷ Qwen, **batch + h=90 %** |
|---|---:|---:|
| GPT-6 Astra | 276.2× | **115.7×** |
| Claude Fable 5.1 | 271.5× | **111.5×** |
| Claude Opus 5 | 138.1× | **57.8×** |
| GPT-5.6 Sol | 110.5× | **46.3×** |
| GPT-5.6 Terra / Gemini 3.1 Pro | 63.5× | **27.3×** |
| Claude Sonnet 5 | 55.2× | **23.1×** |
| Claude Haiku 4.5 | 27.6× | **11.6×** |
| Gemini 3.8 Flash | 20.7× | **8.7×** |
| gpt-5-mini | 10.0× | **4.4×** |
| GPT-5.6 Luna | 6.4× | **2.7×** |
| **gpt-5-nano** | 2.0× | **0.9× — the incumbent wins** |

### 1.3 The distilled side — marginal cost, and the fixed cost that dominates it

**Marginal $/1M, from this repo's own grids** ([cost-matrix §2, §4, §5](../matrix/cost-matrix.md)),
`low` tier, on-demand, per-model best cell:

| Student | Blended $/1M (`low`–`high`) | Input $/1M | S1 output $/1M | GPUs | Operating point |
|---|---:|---:|---:|---:|---|
| **Marlin-2B** / B300 | **0.0092**–0.0185 | 0.0090 | 0.0220 | 1 | 1×3,327 · 93,271 tok/s · 35.7 ms |
| Marlin-2B / RTX PRO 6000 | 0.0135–0.0311 | 0.0102 | 0.0372 | 1 | 1×256 · 13,434 tok/s · 19.06 ms |
| **Qwen3.8-27B** / B300 | **0.0602**–0.1221 | 0.0460 | 0.1649 | 1 | 1×256 · 12,463 tok/s · 20.54 ms |
| Qwen3.8-27B / B200 | 0.0633–0.1477 | 0.0413 | 0.1850 | 1 | 1×128 · 8,989 tok/s · 14.2 ms |
| Qwen3.8-27B / H200 | 0.0820–0.1960 | 0.1024 | 0.1590 | 1 | 1×168 · 6,953 tok/s · 24.2 ms |
| Qwen3.8-27B / RTX PRO 6000 | 0.0870–0.1990 | 0.0630 | 0.2440 | 1 | 1×64 · 2,052 tok/s · 31.2 ms |
| DeepSeek-V4.1-Flash-NVFP4 / B200 | 0.1485–0.3464 | 0.0176 | 0.5650 | 4 | 4×256 · 2,947 tok/s · 21.7 ms |
| Kimi-K3 / B300 | 2.3808–4.8259 | 1.2921 | 7.3914 | 8 | 8×111 · 278 tok/s · 49.9 ms |

The **serverless middle option** — no GPU floor, no ops — from
[`scaling/12`](../scaling/12-inference-providers.md) §5.2 and
[Baseten](https://www.baseten.co/pricing/):

| Endpoint | in | cached | out | blended `est.` |
|---|---:|---:|---:|---:|
| Darkbloom (Qwen3.8-27B, fp4) | 0.100 | — | 1.800 | **0.5250** |
| DeepInfra (Qwen3.8-27B, **bf16**) | 0.150 | 0.0375 | 1.875 | 0.5391 |
| Qwen Cloud list | 0.500 | 0.050 | 3.000 | 0.9563 |
| Baseten GLM-5.3-Flash | 0.150 | 0.030 | 0.500 | **0.1925** ⚠️ corrected 2026-09-19 — `0.75 × (0.5 × 0.150 + 0.5 × 0.030) + 0.25 × 0.500`; the printed 0.1934 does not reproduce. Rates re-fetched and confirmed [[src](https://www.baseten.co/pricing/)] |
| Baseten DeepSeek V4.1 Flash | 0.300 | 0.030 | 1.200 | 0.4238 |
| Together Qwen3.5-9B class | — | — | — | see [Together](https://www.together.ai/pricing) |

Note the finding from [`12` §5.2](../scaling/12-inference-providers.md): the
cheapest *market* endpoint for a Qwen3.8-27B-class model ($0.5250 blended) is
**8.7× more expensive** than self-hosting it at full utilisation — and is
**still 2.4× cheaper than Gemini 3.8 Flash at h=50 %** (corrected
2026-09-19: $1.2469 ÷ $0.5250 = 2.375×; the printed 3.1× does not reproduce
from any pair of figures in §1.2). There are therefore
three rungs of cost, not two, and the middle rung needs no GPU commitment at
all.

**Now the term that destroys the per-token story.** A dedicated replica bills by
the hour whether or not traffic arrives. Real 2026 anchors:

| Source | GPU | $/GPU-hour | $/month @730 h |
|---|---|---:|---:|
| [cloud-pricing §5.14](../cross-cutting/cloud-pricing.md) `low` | RTX PRO 6000 (Nebius) | 1.80 | **$1,314** |
| ibid. `low` | A100 (RunPod Secure) | 1.59 | $1,161 |
| ibid. `low` | H100 (Hyperstack) | 3.20 | $2,336 |
| ibid. `low` | H200 (Hyperstack) | 3.99 | $2,913 |
| ibid. `low` | B200 (Hyperstack) | 6.00 | $4,380 |
| ibid. `low` | B300 (Hyperstack) | 7.40 | **$5,402** |
| [Baseten](https://www.baseten.co/pricing/) dedicated | H100 | 6.50 | $4,745 |
| [ibid.](https://www.baseten.co/pricing/) | B200 | 9.980 | $7,285 |
| [ibid.](https://www.baseten.co/pricing/) | A100 | 4.000 | $2,920 |
| [Fireworks](https://fireworks.ai/pricing) on-demand | H100 / H200 | 8.00 | $5,840 |
| [ibid.](https://fireworks.ai/pricing) | B200 | 13.00 | $9,490 |
| [Together](https://www.together.ai/pricing) on-demand | H100 | 5.49 (promo 3.99 to 09/30/26) | $4,008 |
| [ibid.](https://www.together.ai/pricing) | B200 | 8.19 | $5,979 |

**Delivered $/1M = blended ÷ utilisation**, `est.` — the table to put in front of
a buyer:

| Cell | u=100 % | 75 % | 50 % | 25 % | 10 % | 5 % | 1 % |
|---|---:|---:|---:|---:|---:|---:|---:|
| Qwen3.8-27B B300 `low` | 0.0602 | 0.0803 | 0.1204 | 0.2408 | 0.6020 | 1.2040 | 6.0200 |
| Qwen3.8-27B B200 `low` | 0.0633 | 0.0844 | 0.1266 | 0.2532 | 0.6330 | 1.2660 | 6.3300 |
| Qwen3.8-27B H200 `low` | 0.0820 | 0.1093 | 0.1640 | 0.3280 | 0.8200 | 1.6400 | 8.2000 |
| Qwen3.8-27B RTX PRO `low` | 0.0870 | 0.1160 | 0.1740 | 0.3480 | 0.8700 | 1.7400 | 8.7000 |
| Qwen3.8-27B B300 `high` | 0.1221 | 0.1628 | 0.2442 | 0.4884 | 1.2210 | 2.4420 | 12.2100 |
| Marlin-2B B300 `low` | 0.0092 | 0.0123 | 0.0184 | 0.0368 | 0.0920 | 0.1840 | 0.9200 |
| Marlin-2B RTX PRO `low` | 0.0135 | 0.0180 | 0.0270 | 0.0540 | 0.1350 | 0.2700 | 1.3500 |

Read it against §1.2's incumbent floors:

- **At u = 10 %**, Qwen3.8-27B on B300 delivers **$0.6020/1M** — *more expensive
  than Gemini 3.8 Flash batched-cached* ($0.5222) and **11× more expensive than
  `gpt-5-nano` batched-cached** ($0.0536).
- **At u = 50 %** it delivers $0.1204/1M, which finally beats Luna
  ($0.1643) but still loses to nano.
- **The B300 only makes sense above ~50 % sustained utilisation.** Below that,
  the RTX PRO 6000 at $1,314/month — 4.1× less fixed cost for 6.1× less decode
  throughput — is the correct class, and below *that*, serverless is.

This is the same conclusion doc 00 §4.5 reaches and
[`scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md) develops:
**the platform's cost of goods is idle GPU, not tokens.** It is also why
multi-tenant adapter serving (S-LoRA-style unified paging, "up to 4×" throughput
over HF PEFT and vLLM [[src](https://arxiv.org/abs/2311.03285)]) is not an
optimisation but the business model: it is the only mechanism that converts N
customers' 9 % utilisations into one 70 % utilisation.

### 1.4 One-off costs — and the surprise about which one is biggest

Four components. The intuition that annotation dominates is **wrong**; the
intuition that training dominates is **also wrong**.

#### (a) Annotation — cheap, and cheaper still on an open-weights teacher

50,000 examples at 4,000 in / 400 out, `est.`:

| Teacher | Standard | Batch (50 %) | Source |
|---|---:|---:|---|
| GPT-6 Astra | $3,000 | **$1,500** | [OpenAI](https://developers.openai.com/api/docs/pricing) |
| Claude Opus 5 | $1,500 | **$750** | [Anthropic](https://claude.com/pricing) |
| Claude Fable 5.1 | $3,000 | $1,500 | [ibid.](https://claude.com/pricing) |
| Gemini 3.1 Pro | $640 | $320 | [Google](https://ai.google.dev/gemini-api/docs/pricing) |
| **Kimi-K3 self-hosted on B300** (`low`) | **$406** | n/a | [cost-matrix §4/§5](../matrix/cost-matrix.md) |

Two things follow. First, **annotation at this scale is a rounding error** —
$750–$3,000 against a six-figure engagement. Second, **the open-weights teacher
is competitive on price and has no ToS problem** (doc 00 §8.1: Anthropic,
Google and — presumptively — OpenAI all restrict distillation of their outputs).
Kimi-K3 self-hosted on 8×B300 is the *most expensive* cell in this repo's grid
and it still undercuts every frontier teacher for bulk labelling. **The legal
argument and the cost argument point the same way**, which is rare and should be
exploited.

The caveat: annotation cost scales with *training steps* under on-policy
distillation (doc 00 §3.2), not with dataset size, because the teacher is called
on every student rollout. A 150-step on-policy run in the Thinking Machines
recipe would multiply this line item; ⚠️ **TO BE VERIFIED** — no vendor publishes
a teacher-token count for an on-policy run, and the Thinking Machines post
reports GPU-hours (1,800 vs 17,920) rather than teacher tokens
[[src](https://thinkingmachines.ai/blog/on-policy-distillation/)].

#### (b) Training — a commodity, priced per token, with a 12× spread

660M training tokens (50k examples × 4.4k tokens × 3 epochs), `est.`:

| Vendor | Rate | Cost | Source |
|---|---:|---:|---|
| Tinker, Qwen3.8-27B train | $4.103/1M | $2,708 | [Tinker](https://tinker-docs.thinkingmachines.ai/tinker/models/) |
| Tinker, Nemotron-3.5-Lightning-30B-A3B | $0.44/1M (**list $0.88**, "Limited-time 50% discount") | $290 | [ibid.](https://tinker-docs.thinkingmachines.ai/tinker/models/) |
| Tinker, GLM-5.3 (256K) | $14.58/1M | $9,623 | [ibid.](https://tinker-docs.thinkingmachines.ai/tinker/models/) |
| Fireworks, LoRA SFT, 16.1–80B | $3.00/1M | $1,980 | [Fireworks](https://fireworks.ai/pricing) |
| Fireworks, LoRA SFT, ≤16B | $0.50/1M | **$330** | [ibid.](https://fireworks.ai/pricing) |
| Fireworks, full-param SFT, ≤16B | $1.00/1M | $660 | [ibid.](https://fireworks.ai/pricing) |
| Together, Llama-3.3-70B SFT | $2.03/1M | $1,340 | [Together](https://www.together.ai/pricing) |
| Together, Qwen3.5-9B SFT | $0.34/1M | **$224** | [ibid.](https://www.together.ai/pricing) |
| Together, GLM-5.2 SFT | $40.00/1M | $26,400 | [ibid.](https://www.together.ai/pricing) |
| Databricks, Llama-3.1-8B, 500M words | 4,400 DBU @ $0.65 | $2,860 | [Databricks](https://www.databricks.com/product/pricing/mosaic-foundation-model-training) |
| Databricks, Llama-3.1-8B, 10M words | 100 DBU @ $0.65 | $65 | [ibid.](https://www.databricks.com/product/pricing/mosaic-foundation-model-training) |
| **Rent the GPU yourself** | ART precedent: 5 H100-hours | **$15 GPU + $7 judge = $22** | [OpenPipe archive](https://openpipe.ai/) |

That last row deserves emphasis because it is a *fully costed, published,
end-to-end* training run, not a rate card: OpenPipe's ART•S summarisation model
was a GRPO run on **Qwen 2.5 14B**, 5 hours on **one RunPod H100**, "GPU costs
were around $15, and after adding in the additional $7 spent on judge tokens,
the total cost of the final training run was only **$22**"
[[src](https://openpipe.ai/)]. It moved the model from **43 % → 85 %** on its
task, past **Sonnet 4 at 72 %** (§4).

**Conclusion: training is not a cost centre.** At $224–$2,708 for a real
iteration it is below the noise floor of an enterprise engagement. Anyone
pricing the platform around training margin is pricing around the wrong thing.
This is also the "buy, don't build" signal doc 00 §6 reaches for doc 05:
training-as-a-service is commoditised and has a 12× price spread between vendors
for the same job, so the platform should be a *price-taker* here and route jobs
to whichever vendor is cheapest for the student class.

#### (c) Evaluation — the judge is cheap, the humans are not

Using doc 00 §5.3's sample sizes for a one-sided non-inferiority test at
α = 0.05, power = 0.80, and judging with Claude Sonnet 5 ($2/$10) at ~5,000 in /
200 out per judgement, **both orders** run to control position bias, `est.`:

| Margin δ | n per arm | Judge cost, 1 slice | Judge cost, 5 slices |
|---:|---:|---:|---:|
| 5 pp | 631 | $15 | $76 |
| 3 pp | 1,752 | $42 | $210 |
| 2 pp | 3,942 | $95 | $473 |
| **1 pp** | **15,766** | $378 | **$1,892** |

**The LLM-judge cost of the entire confidence protocol is under $2,000 even at a
1-point margin across five slices.** That is a genuinely important and
counter-intuitive result: doc 00 §5.3 frames the sample-size table as "the price
of confidence", and in *judge tokens* the price is negligible. The price is
elsewhere:

| Line item | Basis | Cost `est.` |
|---|---|---:|
| Human gold set, 200 examples @ 5 min @ $80/h loaded | doc 00 §5.1's ≥200-example judge-validation requirement | **$1,333** |
| Human gold set, 500 examples | safer κ estimate | $3,333 |
| SME adjudication of 2,000 disagreements @ 5 min | shadow-mode diff review | $13,333 |
| Eval-harness build (slices, contract conformance, safety suite) | engineering, see (d) | — |

⚠️ **TO BE VERIFIED — the $80/h loaded SME rate is my assumption, not a sourced
benchmark.** Doc 00 §4.3(c) records the same gap: no public benchmark exists for
what an enterprise pays per adjudicated example, and Snorkel's positioning
("calibrated expert review trained against gold standards", "full audit trails
for label provenance" [[src](https://snorkel.ai/)]) is evidence the work is sold
as a premium service, not evidence of a rate. Treat every human-review figure in
this document as elastic by 3×.

**Judge-model choice is a cost decision as well as a validity decision.** Doc 00
§5.1's rule — the judge must not be the teacher — means that if the teacher is
Kimi-K3 (self-hosted, cheap), the judge is a frontier model (expensive per call,
but few calls). If the teacher is GPT-6 Astra, the judge might be Claude Sonnet
5 at $2/$10, or a *self-hosted* DeepSeek-V4.1-Flash at $0.190 blended
[[src](../matrix/cost-matrix.md)] — 17× cheaper than Sonnet 5 — with the
validity cost that a smaller judge's agreement with humans must be measured, not
assumed.

#### (d) Engineering — the biggest one-off, and the least sourceable

⚠️ **TO BE VERIFIED — there is no public benchmark for this and I will not
invent one.** The reasoning for a range: the MVP scope in doc 00 §9.1 is ten
acceptance criteria covering gateway capture, shadow diffing, an eval harness, a
conformance suite, a safety gate and a rollback drill. At 1.5 FTE for 6–10 weeks
and a fully-loaded engineering cost of ~$4,800/week/FTE, that is **$43,000–
$72,000** for iteration 1, falling steeply for iterations 2..N once the harness
exists. Every payback table below is therefore run at **three** one-off levels —
$40k, $80k, $150k for a small engagement and $80k, $150k, $300k for a large one
— rather than at a single fabricated figure.

**One-off summary for a representative first iteration**, `est.`:

| Component | Low | Typical | High |
|---|---:|---:|---:|
| Annotation (50k ex, batched teacher) | $406 | $1,500 | $3,000 |
| Training (one student, 3 epochs) | $224 | $2,000 | $9,623 |
| Judge-based evals, 5 slices, δ=2 pp | $473 | $473 | $1,892 |
| Human gold set + adjudication | $1,333 | $5,000 | $16,666 |
| **Engineering** ⚠️ | $43,000 | $57,600 | $120,000 |
| **Total** | **~$45,400** | **~$66,600** | **~$151,200** |

**Engineering is 85–95 % of the one-off cost.** Everything the industry talks
about — teacher tokens, GPU-hours, eval sample sizes — is in the noise. That has
a direct product consequence: **the platform's job is to amortise the
engineering across customers**, which is exactly what a platform is for, and it
means the second customer in a vertical should cost a fraction of the first.

### 1.5 Ongoing loop costs — the line item that quietly eats the margin

The loop does not stop when the model ships. Per month:

| Cost | Mechanism | Scales with |
|---|---|---|
| **Trace capture and storage** | S2 | every request |
| **Shadow inference** | S9's recommended default (doc 00 §5.4) | every request, ×1 candidate |
| **Disagreement judging** | S3 disagreement mining | disagreement rate × requests |
| **Re-annotation** | S3, for drift | sampled fraction |
| **Dev replica** | doc 01's `main`/`dev` split | fixed, per tenant |
| **Periodic re-training** | S5 | per iteration |

**Trace capture is the one that surprises people.** Three vendors, three
completely different pricing *models*, evaluated at two volumes assuming 3
billable units per request (trace + generation + score) and ~4 bytes per stored
token, `est.`:

| Volume | Langfuse Cloud | Braintrust | W&B Weave | Langfuse self-hosted |
|---|---:|---:|---:|---:|
| 2M req/mo (4.4k tok each) — 6M units, 35 GB, 0.2M scores | **$621** | **$565** | **$3,511** | **$0 licence** |
| 50M req/mo (1.65k tok each) — 150M units, 330 GB, 5M scores | **$9,501** | **$8,649** | **$33,698** | **$0 licence** |

Rate cards: Langfuse Core $29 / Pro $199 / Enterprise $2,499, 100k units
included, graduated overage **$8 → $7 → $6.50 → $6 per 100k** across the
100k–1M / 1M–10M / 10M–50M / 50M+ bands; "Langfuse is open source and you can
self-host it for free" [[src](https://langfuse.com/pricing)]. Braintrust Pro
$249/mo with 5 GB processed and 50,000 scores included, then **$3/GB** and
**$1.50 per 1,000 scores** [[src](https://www.braintrust.dev/pricing)]. W&B Pro
"starts at $60/month" with 1.5 GB Weave ingestion included and **$0.10/MB**
(= $102.40/GB) beyond [[src](https://wandb.ai/site/pricing/)].

Three readings:

1. **W&B Weave's per-MB model is 3.5–10× the others at these volumes** and would
   consume the *entire* saving in §7.2. It is priced for selective tracing, not
   for capturing 100 % of production traffic — which is precisely what this
   platform requires (doc 00 §9.1 criterion 1: ≥ 99.9 % capture).
2. **Braintrust's per-score line is the one to watch.** At 10 % scoring coverage
   and 50M requests, scores alone are $7,425/month. A platform that judges
   *everything* on Braintrust is paying more for scoring than for GPUs.
3. **Self-hosting Langfuse is a business-model requirement, not a preference.**
   Doc 00 §6 already recommends adopting Langfuse rather than building; this
   document adds that it must be the **self-hosted** deployment, because the
   cloud unit price is a per-request tax on exactly the volumes where the
   platform's own margin lives.

**Shadow inference doubles the serving load.** Doc 00 §5.4 recommends shadow-first
rollout because it is the only way to find a 1-in-10,000 failure. The cost is
exact and predictable: the candidate serves 100 % of production requests with
its output discarded, so the GPU-hours *double* for the duration of the shadow
period. In §7.1's profile that is +388 GPU-h/month = **+$698/month** on RTX PRO
6000, or one more GPU for headroom at **+$1,314/month**. That is cheap insurance
and should be priced into every engagement as a line item rather than absorbed.

**Disagreement judging** is the recurring analogue of §1.4(c). At a 10 %
material-disagreement rate on 2M requests, judging 200,000 diffs at $0.012 each
is **$2,400/month** `est.` — more than the monthly judge cost of the entire
offline confidence protocol, and the correct place to spend it, because those
are the examples doc 00 §7.2 identifies as the platform's "disengagements".

### 1.6 Break-even volume and payback

Break-even is **not** a token count; it is the volume at which

```
n_gpus(V) × $/GPU-h × 730 + V × marginal_self + LOOP
    <   V × marginal_incumbent
```

and because `n_gpus` steps, the curve is a sawtooth, not a line. Two usable
formulations:

**(a) Utilisation break-even** — the cleanest, and already computed for this
repo's five models in [`cost-matrix.md` §6.2](../matrix/cost-matrix.md): the
fraction of a replica you must keep busy for self-hosting to beat a given
endpoint. For Qwen3.8-27B that is **6–28 %** against Qwen Cloud list, and
[`scaling/12` §5.2](../scaling/12-inference-providers.md) tightens it to
**11.5 % / 23.3 %** against the cheapest fp4 endpoint in the market. Against a
*frontier* incumbent it is far lower — but against `gpt-5-nano` batched it is
**above 100 %, i.e. impossible**.

**(b) Payback months** — `one_off ÷ monthly_saving`. Computed for both worked
profiles in §7. The pattern:

| Situation | Payback |
|---|---|
| Replacing an Astra/Opus/Fable-class model at list price, ≥2M req/mo | **1–3 months** |
| Replacing a Sol/Terra-class model that already batches and caches | **4–12 months** |
| Replacing Gemini 3.8 Flash batched at 50M req/mo | **never** (at the 2026 promo price) |
| Replacing Luna / nano / Flash-Lite class | **never** |

**Decision rule (qualification filter).** Before any engineering:
`monthly_incumbent_spend_at_their_honest_price` must exceed **≈ $15,000/month**.
⚠️ **Corrected 2026-09-19 — the justification previously printed here was
arithmetically false.** It read "or the engagement cannot clear a $66,600 one-off
inside 12 months even at a 100 % cost reduction"; at a 100 % cost reduction the
saving *is* the spend, and $66,600 ÷ $15,000 = **4.4 months**. The arithmetic
floor for a 12-month payback is a *net* saving of $66,600 ÷ 12 = **$5,550/month**,
and since the distilled option carries its own delivered cost (§7.1's profile:
≈ $4,563/month of GPU + observability), the arithmetic threshold on incumbent
spend is ≈ **$10,100/month**, not $15,000. Keep $15,000 as a **judgement call**
carrying ~50 % headroom — no swap achieves a 100 % cost reduction, and §1.4(d)'s
engineering line is itself ⚠️ unsourced — but do not present it as a derivation. Below that, sell rungs 1–2 of doc 00 §3.2's ladder (a
prompt/model swap plus the eval harness) and nothing else. This is the same
filter doc 00 §4.3 derives from a different direction and the two agree.

### 1.7 Sensitivity

**To utilisation** — see §1.3's delivered-$/1M table. This is the dominant
sensitivity and it is monotone and brutal: halving utilisation doubles delivered
cost, with no diminishing return, until a whole replica can be removed.

**To cache hit rate** — bounded, and bounded tighter than people expect. `est.`,
at the 25 %-output convention:

| Incumbent | h=0 | h=25 % | h=50 % | h=75 % | h=90 % | h=100 % | floor (output-only) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Claude Opus 5 | 10.0000 | 9.1562 | 8.3125 | 7.4688 | 6.9625 | 6.6250 | **6.2500** |
| Claude Fable 5.1 | 20.0000 | 18.1719 | 16.3438 | 14.5156 | 13.4187 | 12.6875 | **12.5000** |
| GPT-5.6 Sol | 8.0000 | 7.3250 | 6.6500 | 5.9750 | 5.5700 | 5.3000 | **5.0000** |

Fable 5.1 is the extreme: because its cache read is only **2.5 %** of input
[[src](https://claude.com/pricing)], going from h=0 to h=100 % moves its blend
only from $20.00 to $12.69 — a 37 % cut — and it can never go below $12.50. A
customer on Fable 5.1 who "has already optimised caching" has captured almost
everything there is; the remaining 111× gap to a distilled specialist is real.
**Conversely, Anthropic's cache *write* is priced above base input** ($6.25/MTok
on Opus 5, $12.50 on Fable 5.1 [[src](https://claude.com/pricing)]), so a
customer whose prompt prefix changes every request is paying **more** than list —
a diagnosis the platform should run in week 1 and one that can pay for the whole
engagement by itself.

**To output length** — the ratio *improves* with output share, because
self-hosted decode is cheaper relative to prefill than the vendors' output
premium. `est.`, Qwen3.8-27B B300 vs Opus 5 @ h=50 %:

| Output share of tokens | Qwen B300 $/1M | Opus 5 $/1M | Ratio | Terra $/1M | Ratio |
|---:|---:|---:|---:|---:|---:|
| 5 % | 0.0323 | 3.8625 | 119.7× | 1.6450 | 51.0× |
| 10 % | 0.0393 | 4.9750 | 126.7× | 2.1900 | 55.8× |
| **25 %** (repo convention) | 0.0602 | 8.3125 | 138.1× | 3.8250 | 63.5× |
| 50 % | 0.0951 | 13.8750 | 145.9× | 6.5500 | 68.9× |
| 75 % | 0.1300 | 19.4375 | 149.5× | 9.2750 | 71.3× |
| 90 % | 0.1509 | 22.7750 | 150.9× | 10.9100 | 72.3× |

**Reasoning-token output is therefore the best possible workload to distil off**,
and the worst possible workload to leave on a frontier model: vendors charge 5×
input for output while self-hosted decode is only ~3.6× self-hosted prefill on
this cell. Any customer paying for long chains of thought is over-paying on the
most favourable axis. ⚠️ Caveat: a distilled student that must *also* emit
reasoning traces to match quality will emit more of them per answer than the
teacher did (doc 00 §3.3 "style over substance"), and the eval must be
length-controlled or this advantage is illusory.

**To the incumbent's price changes.** Gemini 3.8 Flash reverts from $0.75/$3.75
to $1.50/$7.50 on 2027-01-01 [[src](https://ai.google.dev/gemini-api/docs/pricing)].
That single scheduled change flips §7.2's extraction case from "never pays back"
to a ~9-month payback. **Every business case must carry the date of the price it
was computed against, and must be re-run when the vendor moves.** This is an
argument for the platform to *own* the cost model as a live artifact — a
continuously re-evaluated dashboard — rather than a slide.

### 1.8 Decision rules

| If… | Then… | Because |
|---|---|---|
| Honest incumbent spend < $15k/month | Sell the eval harness + a tier-down test. Do not train. | §1.6; the one-off cannot amortise |
| The incumbent is `gpt-5-nano`/Luna/Flash-Lite class | **Do not pitch cost.** Pitch quality-at-equal-price, or walk. | §1.2; nano batched-cached is already ≤ our best cell |
| Sustained utilisation would be < 25 % of one replica | Use serverless ([`12`](../scaling/12-inference-providers.md)), not a dedicated GPU | §1.3 delivered-cost table |
| Sustained utilisation 25–60 % | Right-size **down** the GPU class (RTX PRO 6000 / H200), don't buy a B300 | §1.3; 4.1× less fixed cost |
| Sustained utilisation > 60 % on one replica | Dedicated, and consider `res1y` — but check [cost-matrix §7.4](../matrix/cost-matrix.md): reserved is **not** a discount on B300/GB300/MI355X | ibid. |
| Workload tolerates asynchrony | Assume the customer *will* find the 50 % batch discount. Quote against it. | §1.2 |
| Output is > 50 % of tokens | The case is strong; lead with it | §1.7 |
| Input is > 90 % cached and output is short | The case is weak on cost; lead with latency (§2) or quality | §1.7 |
| Video, clips < 2 minutes | Very likely a loss against Gemini; check the GPU floor first | §5.4, §7.3 |
| Video, clips > 10 minutes, or per-second-priced incumbent | Very likely a large win | §5.4, §7.3 |

---

## 2. Latency and product value

### 2.1 What the model swap actually changes

Two numbers, and they move in opposite directions for different reasons.

**TTFT** is dominated by prefill. From [cost-matrix §5](../matrix/cost-matrix.md),
Qwen3.8-27B on B300 prefills at **44,686 tok/s** `est.` (inverted from the $/1M
input cell at $7.40/GPU-h), so a 4,000-token prompt prefills in ~90 ms on a free
GPU — but under the S1 operating point (concurrency 256) it queues behind other
requests, which is why
[`scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md)
exists. Prefix caching is a *TTFT* lever, not a cost lever, exactly as
[`cost-matrix.md` §7.2](../matrix/cost-matrix.md) concludes.

**TPOT** is the operating point. The repo's S1 cells for Qwen3.8-27B run
**14.2–45.3 ms/token** across GPUs ([cost-matrix §2](../matrix/cost-matrix.md)),
i.e. 22–70 tok/s per stream. ⚠️ **TO BE VERIFIED — I have no measured TTFT/TPOT
for GPT-5.6 Sol, Opus 5, Fable 5.1 or Gemini 3.8 Flash**; none of the three
vendor pricing pages publishes latency, and I had no search budget to find a
third-party benchmark. **Doc 01 or doc 07 must measure the incumbent's TTFT/TPOT
on the customer's own prompts before any latency claim is made.** Invariant I3
(doc 00 §1.3) requires p50 *and* p99, and a claim without a measurement is not a
claim.

What *is* sourced about the direction of the effect:

- NVIDIA's data-flywheel blueprint reports a replacement that cut cost **and**
  time-to-first-token **by >50 %** (`Qwen-2.5-32b-coder` in place of
  `Llama-3.1-70b-instruct`, un-fine-tuned)
  [[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)] — vendor, one
  workload, and now deprecated.
- Custom-silicon endpoints show what the ceiling looks like: Cerebras serves a
  Qwen3.8-27B-class model at **~1,850 tok/s** and Groq at **~450 tok/s**
  ([`12` §5.2](../scaling/12-inference-providers.md)). **Corrected 2026-09-19:**
  the document previously read "at 1.1–1.6× the blended price of a GPU endpoint";
  $1.1150 (Cerebras) and $1.6000 (Groq) are that table's **blended $/1M prices**,
  not multiples. Against the cheapest GPU endpoint in the same table (Darkbloom
  $0.5250) they are **2.1× and 3.0×** `est.`, and note §5.2's blend caveat — both
  custom-silicon rows publish no cached-input rate and are blended with all input
  uncached, so they are not arithmetically comparable to the cached rows. **If the customer's problem is purely latency, buying
  custom silicon is cheaper and faster than distilling** — a competitive fact the
  platform should state before a customer discovers it.
- OpenPipe's founder framing, from the archive: fine-tuned models "are much
  smaller, they also can have much lower latency than large generalist models",
  with inference cost "often a 10-100x improvement"
  [[src](https://openpipe.ai/)] — vendor, and the cost claim is the one §1.2
  disciplines.

### 2.2 The business impact of latency — what is actually measured, and what is not

There is **no published study I could reach measuring the revenue impact of LLM
TTFT specifically.** What exists is a large, well-replicated web-performance
literature, which is a *proxy* and must be labelled as one.

| Study | What was measured | Result | Source |
|---|---|---|---|
| **Deloitte / Google, "Milliseconds Make Millions", 2020-03-24** | A **0.1 s** improvement in mobile site speed, across retail/travel/luxury/lead-gen brands in Europe and the US over four weeks | Retail **conversions +8.4 %**, average order value **+9.2 %**; travel conversions **+10.1 %**, AOV **+1.9 %**; luxury **page views per session +8.6 %**; lead-gen **bounce rate improved 8.3 %** | [Deloitte](https://www.deloitte.com/ie/en/services/consulting/research/milliseconds-make-millions.html) |
| **Google web.dev case studies** | Core Web Vitals improvements vs business KPIs | Vodafone Italy: LCP **−31 %** → **+8 % sales**; Lazada: LCP 3× → **+16.9 % mobile conversion**; Tokopedia: LCP **−55 %** → **+23 % session duration**; GYAO: LCP 3.1× → **+108 % CTR**; Agrofy: **−76 % load abandonment**; NDTV: **−50 % bounce rate** | [web.dev](https://web.dev/case-studies/vitals-business-impact) |
| **Nielsen, response-time limits, 1993-01-01** | Human perception thresholds | **0.1 s** = "the limit for having the user feel that the system is reacting instantaneously"; **1.0 s** = "the limit for the user's flow of thought to stay uninterrupted"; **10 seconds** = "the limit for keeping the user's attention focused on the dialogue" | [NN/g](https://www.nngroup.com/articles/response-times-3-important-limits/) |

**How to use these honestly.** The 0.1 s → 8.4 % retail conversion result is
about *page load*, where the user waits with nothing on screen. A streaming LLM
response is a different perceptual object: Nielsen's 1.0 s "flow of thought"
limit maps onto **TTFT**, and once tokens are streaming the user's attention is
held, so TPOT trades against the 10-second limit rather than the 1-second one.
The defensible claims are therefore:

1. **TTFT below ~1 s preserves flow; above ~10 s the interaction needs a
   progress affordance** [[src](https://www.nngroup.com/articles/response-times-3-important-limits/)].
   This is a design constraint, not a revenue model.
2. **In conversion-funnel products, tenths of a second have measured revenue
   value in the high single digits of percent**
   [[src](https://www.deloitte.com/ie/en/services/consulting/research/milliseconds-make-millions.html)],
   and it is *plausible but unproven* that TTFT behaves similarly. ⚠️ **TO BE
   VERIFIED.**
3. **For batch and back-office workloads, latency has no revenue value at all** —
   and those workloads should be taking the vendors' 50 % batch discount (§1.2),
   which means the distilled model's latency advantage is worth **zero** there.
   State this plainly rather than letting a deck imply otherwise.

**Decision rule.** Latency is a *product* argument only where a human is waiting
inside a funnel. Where a human is waiting outside a funnel (internal tools) it is
a satisfaction argument with no measurable dollar value. Where no human is
waiting it is not an argument. Sizing the latency benefit is therefore the
customer's job, not ours, and the platform's contribution is the **measurement**
(p50/p99 TTFT and TPOT, before and after, on their traffic) rather than the
valuation.

### 2.3 Video turnaround

Video is the case where latency and cost are the *same* variable, because both
are set by the frame budget.

- Gemini processes video at **1 FPS**, **~100 tokens per second of video** at
  low/default resolution and **~300** at high, plus **32 tokens/s of audio**;
  models with 1M context support "videos up to 3 hours long by default (at low
  media resolution), or up to 1 hour long at high media resolution"
  [[src](https://ai.google.dev/gemini-api/docs/video-understanding)]. Turnaround
  therefore grows **linearly with clip length**.
- Gemini's "agentic" video mode "typically uses up to 88 % fewer total tokens
  than static processing for long-form content", on 3.8 Flash, 3.7 Flash, 3.6
  Flash and 3.5 Flash-Lite [[ibid.](https://ai.google.dev/gemini-api/docs/video-understanding)].
  **This is an incumbent counter-move of exactly the kind §1.2 catalogues for
  text, and it is large.** Any video business case computed against *static*
  token counts overstates the incumbent's cost by up to 8×. §5.4 and §7.3 compute
  both.
- Marlin-2B caps at **240 frames ⇒ ~23,560 prefill tokens for any clip ≥ 2
  minutes**, which means a 10-minute clip is sampled at **0.40 fps** and an hour
  at **0.067 fps** for identical cost and identical latency
  ([`models/marlin2b/architecture.md` §6.3](../models/marlin2b/architecture.md)).
  Turnaround is therefore **flat in clip length** — the single most commercially
  interesting property in the repo.

⚠️ **The video latency numbers are unmeasured on both sides.** Marlin-2B "has no
published throughput or latency measurement on any hardware, and does not load in
any engine as shipped" (doc 00 §3.4, from
[`models/marlin2b/README.md`](../models/marlin2b/README.md) §2, §10), and the
repo's own analysis warns that "**the real bottleneck may not be the GPU**" —
torchcodec/NVDEC decode throughput for 240 frames at 448×448 is unmeasured
everywhere consulted, and vLLM's own data says CPU decode saturates before 4
GPUs on a *16-frame* workload, 15× lighter than Marlin's canonical path
([`architecture.md` §12 #6](../models/marlin2b/architecture.md)). **Every video
cost figure in §5.4 and §7.3 is a GPU-inference cost that may be dominated by a
video-decode cost nobody has measured.** This is the largest single risk to the
video business case and it is a two-day measurement, not a research programme.

---

## 3. Quality risk pricing

### 3.1 The asymmetry that decides the product

§1 shows a saving measured in thousands of dollars a month. This section shows a
risk measured in hundreds of thousands. **The confidence protocol is not overhead
on the cost saving; the cost saving is a rounding error on the risk.**

Expected monthly cost of a regression, `est.`, as
`P(bad output) × requests × cost per bad outcome`:

| Scenario | P(bad) | Requests/mo | $ per bad outcome | Expected cost |
|---|---:|---:|---:|---:|
| Support chat, mild regression, escalation costs an agent 12 min | 0.5 % | 2,000,000 | $12.00 | **$120,000/mo** |
| Support chat, worse regression | 1.0 % | 2,000,000 | $12.00 | **$240,000/mo** |
| Bulk extraction feeding a downstream system | 0.2 % | 50,000,000 | $0.50 | **$50,000/mo** |
| Rare high-severity failure (wrong legal/medical/financial answer) | 0.1 % | 2,000,000 | $150.00 | **$300,000/mo** |

⚠️ **Every "$ per bad outcome" in that table is an assumption, not a sourced
benchmark.** I could not reach a published cost-per-escalated-ticket figure in
this session. What makes the table useful is not its absolute values but its
**ratio** to §7.1's saving: distilling off a batched, cached GPT-5.6 Sol at 2M
req/month saves **$6,477/month** (§7.1), so a regression that adds **0.027
percentage points** of bad outcomes at $12 each wipes out the entire saving.
**The quality margin δ that the customer agrees in doc 00 §5.2 is, numerically,
a bet on a quantity far larger than the thing being optimised.** ⚠️ **Corrected
2026-09-19:** the document previously printed "~40×" here, in §6.2 and in Open
Question 2, but the base-case ratio is $120,000 ÷ $6,477 = **18.5×** — the same
number §0 item 3 prints. It is **18.5× on the mild-regression row, 37× at a
1.0 % regression rate and 46× on the rare-high-severity row**; quote the row,
not a single multiple.

This is the argument that should open every customer conversation, and it
reframes the sale: the platform is **insurance with a rebate**, not a discount.

### 3.2 What the confidence protocol costs, and what it buys

From §1.4(c): the full offline protocol at δ=2 pp across 5 slices costs **~$473
in judge tokens plus ~$1,333–$13,333 in human review** `est.`. Against a
$120,000/month risk, that is a premium of **1.5 %–11.5 %** of one month's
exposure for a permanent artifact (⚠️ corrected 2026-09-19 from "roughly 1 %":
$1,806 ÷ $120,000 = 1.5 % at the cheap end of the human-review range and
$13,806 ÷ $120,000 = 11.5 % at the expensive end — and §1.4(c) says to treat
every human-review figure as elastic by 3× on top of that). The *marginal* cost of tightening δ from 3 pp to 1 pp
is another **$1,682** of judge tokens and a 9× larger human-review bill —
which is still, in this frame, obviously worth buying.

**Therefore: sell the tightest margin the customer will pay for, and show them
this arithmetic.** Doc 00 §5.3's sample-size table is the right artifact to put
in front of a buyer on day one — "how sure do you want to be, and what will you
pay for it?" — and §1.4(c) is the price list that goes next to it.

The second thing the protocol buys is **reversibility**, invariant I7. A
demonstrated <60 s rollback (doc 00 §9.1 criterion 9) converts an unbounded tail
risk into a bounded one: the expected cost of a regression becomes
`P(bad) × requests_during_detection_window × cost`, and the detection window is
an engineering parameter. **Halving mean-time-to-detect halves the risk**, which
is why doc 02's alerting and doc 07's shadow-diff pipeline are worth more than
any inference optimisation in doc 06.

### 3.3 When to keep a frontier fallback: routing and cascades

The economically correct answer to "the student is right 97 % of the time" is
usually **not** "improve the student" — it is "route the other 3 %".

**Published results:**

| System | Claim, verbatim where possible | Source |
|---|---|---|
| **FrugalGPT** (May 2023) | Can "match the performance of the best individual LLM (e.g. GPT-4) with up to **98 % cost reduction**" or "improve the accuracy over GPT-4 by **4 %** with the same cost". Three strategies: **prompt adaptation, LLM approximation, LLM cascade**. Observed API fees "differ by two orders of magnitude" | [arXiv 2305.05176](https://arxiv.org/abs/2305.05176) |
| **RouteLLM** (Jun 2024) | Routers "dynamically select between a stronger and a weaker LLM during inference", trained on "human preference data and data augmentation"; "significantly reduces costs — **by over 2 times in certain cases** — without compromising the quality of responses"; routers show "significant transfer learning capabilities, maintaining their performance even when the strong and weak models are changed at test time" | [arXiv 2406.18665](https://arxiv.org/abs/2406.18665) |
| **Not Diamond** (product, 2026) | Model routing for coding agents. Site claims **"5 %+"** accuracy gain, **"20 %+"** cost reduction, **"2x"** faster development cycles; one named case study (Rootly) with a **39 % average accuracy increase**. A calculator models $4.8M → $3.6M annually (25 %) for 1,000 engineers at $300/mo. No published benchmark methodology; the savings figures appear modelled rather than independently verified | [notdiamond.ai](https://www.notdiamond.ai/) |

**Critical read.** FrugalGPT's 98 % is the headline everyone quotes and it is a
*cascade* result on selected benchmark datasets with a 2023 price structure where
"fees differ by two orders of magnitude". Today's price structure is different —
§1.2's table shows the intra-vendor spread from Astra to nano is **373×** on
blended list price, *wider* than 2023's — so the mechanism is, if anything,
stronger now. RouteLLM's "over 2×" is the more sober number and it comes from a
binary strong/weak router, which is exactly the architecture this platform
should implement. Not Diamond's numbers are marketing and should not appear in a
customer deck without replication.

**The mechanism to build** (this is a doc 01 + doc 07 feature, sized here):

- **Inputs:** the student's own uncertainty signal (token-level entropy, or a
  trained verifier over the student's output), the request's slice label, and
  the customer's cost/quality budget.
- **Outputs:** a route decision (`student` | `frontier`) plus a logged reason.
- **Cost:** blended cost becomes `(1−f)·c_student + f·c_incumbent + c_router`
  where `f` is the fallback fraction. With Qwen3.8-27B at $0.0602 and GPT-5.6 Sol
  at $2.785 (batched, cached), **f = 5 % still delivers $0.1964/1M — a
  14.2× saving** `est.`; f = 10 % gives $0.3327/1M (8.4×), f = 20 % gives
  $0.6052/1M (4.6×), and even f = 30 % gives $0.8776/1M (3.2×). The economics
  survive a *very* generous fallback rate.
- **Failure modes:** (a) the router is itself a model and can be wrong, and its
  errors correlate with the hard cases — precisely where the cost is; (b) a
  latency penalty on every fallback (two sequential calls, not one) unless the
  route is decided *before* generation; (c) the fallback path keeps the customer
  on the incumbent's contract, so the ToS and data-residency questions of doc 00
  §8 never fully go away; (d) **f drifts upward silently** as traffic shifts, so
  `f` must be a monitored SLI with an alert, not a config value.

**Decision rule.** Ship the fallback route **in the MVP**, default `f` high
(20–30 %), and let the eval pull it down. A customer will accept a 4.6× saving
with a visible safety valve long before they accept a 46× saving with none —
which is the same finding as doc 00 §5.6, that items 3 and 6 (shadow diffs and
demonstrated rollback) do more selling than the statistics.

### 3.4 Insurance-style reasoning, stated plainly

The customer is buying a swap whose downside is bounded by rollback speed and
whose upside is bounded by §1's arithmetic. Price the engagement the way an
insurer would:

```
Expected value of the swap
  = monthly_saving × months
  − P(regression reaches production) × MTTD_hours × requests/hour × $_per_bad_outcome
  − one_off
```

and note that the platform controls **two** of those terms directly
(`P(regression reaches production)` via the gates, `MTTD` via shadow + alerting)
and the customer controls the third (`$_per_bad_outcome`). **The platform should
therefore be paid for the two terms it controls** — which is the argument for the
pricing models in §6.2 and §6.3 over pure per-token pricing.

---

## 4. Published case studies, with a critical read

Ordered by how much the published numbers actually support the thesis. **All
numbers below were fetched in this session**; none is recalled.

### 4.1 OpenPipe — the closest precedent, and now an archive

OpenPipe's historic pitch was this platform's thesis verbatim: capture production
traffic, fine-tune a smaller model, deploy, compare. Its status is now settled,
and the settlement is itself a finding.

**Status, 2026-09-19.** `openpipe.ai` serves a static archive whose meta
description reads: *"The OpenPipe platform has migrated to Weights & Biases and
CoreWeave. Browse migration details and the OpenPipe technical archive."* The
site states that following the *"acquisition by CoreWeave in 2025"* they
*"successfully migrated OpenPipe's core functionality, including model
distillation, to the CoreWeave platform"*, and continue to support the
open-source **ART** trainer. The migration post (dated **2026-05-18**) announces
*"deprecating the legacy OpenPipe platform"* with a hard deadline of **July 30,
2026** [[src](https://openpipe.ai/)].

**The part that matters for this document** is *which* features survived and
which did not. The migration post lists the deprecated features explicitly:

> **Reward Models · Managed Evaluations · Criteria · Pruning Rules · "Data
> Pipelines" (Relabeling Workflows) · DPO** [[src](https://openpipe.ai/)]

(**DPO added 2026-09-19** — it is the sixth bullet in the migration post's
sunset list and was missing from the quotation as first printed. Re-fetched from
the page's JS bundle, which is where the archive's content lives.)

Training and inference migrated; **the closed-loop layer — evals, relabeling,
criteria, reward models — was sunset.** That is the fourth vendor retreat from
the loop in twelve months, after NVIDIA's data-flywheel blueprint (deprecated
April 2026, **re-fetched and confirmed 2026-09-19**), NeMo Microservices (sunset
2026-10-01 — *"NeMo Microservices will be sunset on October 1, 2026. All new
development has moved to NeMo Platform"*, re-cited from
[doc 00 §8.6](00-goal-and-problem-statement.md), **not re-fetched here**) and
OpenAI's fine-tuning and Evals platforms (doc 00 §8.6; the fine-tuning notice
**was** re-fetched and is verbatim below). OpenAI's own page still reads: *"OpenAI is winding
down the fine-tuning platform. The platform is no longer accessible to new
users, but existing users of the fine-tuning platform will be able to create
training jobs for the coming months"*
[[src](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)].

**Read this correctly.** It is *not* evidence that the loop does not work — §4.1's
own numbers below show it working. It is evidence that **the loop is hard to
maintain as a product and easy to maintain as training-plus-inference**, which is
precisely the strategic gap doc 00 §6 identifies. The economic implication: the
platform's moat is the part that four vendors have now abandoned, and its risk is
whatever made them abandon it.

**Published results, with a critical read:**

| Result | Numbers, verbatim | What it proves | What to discount |
|---|---|---|---|
| **Axis** (regulatory-index product) | *"By using a custom Mistral 7B variant trained and deployed through OpenPipe, Axis was able to save over **95 %** on a per-token basis compared to GPT-4-Turbo, **the next cheapest model that met their high quality bar**."* Models *"in some cases outperform GPT-4"* | A named production customer, a **95 %** saving, **and** — crucially — the comparison is against the cheapest model that passed their bar, not against list price of the most expensive | Vendor-published; no eval methodology, no sample size, no margin. "In some cases outperform" is not a parity claim |
| **ART•S summarisation** (2025) | Base **Qwen 2.5 14B: 43 %** of questions answered correctly → **ART•S: 85 %**, against **Sonnet 4: 72 %**, Gemini 2.5 Flash Preview 69 %, GPT-4o 66 %, GPT-4.1 51 %. Judge = `gemini-2.5-flash-preview`. ⚠️ **Corrected 2026-09-19 —** the document previously said the judge "could itself only answer ~85 % given the *full* document"; the post says the opposite, that Sonnet 4's 72 % is *"only 28% below the perfect score of **100%** obtained by giving full documents to the judge"*. The full-document ceiling is 100 %, so ART•S's 85 % is **85 % of an attainable 100 %**, not parity with the judge's own ceiling — a weaker result than the corrected sentence implied. Dataset: ServiceNow Repliqa (synthetic docs, to prevent judge memorisation). **Cost: "around $15" GPU + "$7 spent on judge tokens" = "$22"**, 5 hours on one RunPod H100 | **The cleanest published end-to-end economics in this document.** A 14B student beat a frontier model on a real task for $22 of compute, with a verifiable (question-answering) reward rather than a preference judge | Single task; the reward is a proxy (questions answerable from the summary) and the model could in principle learn to game it; the 350-word cap is doing real work; no held-out human eval |
| **Mixture of Agents** (Jun 2024) | MoA "outperforms GPT-4 on 4/4 tasks" with GPT-4 as judge and "3/4" with Claude 3 Opus as judge; **19.25 %** improvement over GPT-4-Turbo on a 200-sample/task private benchmark; human-adjusted win rates still showed MoA stronger "by **9 %**" — ⚠️ **but corrected 2026-09-19: the 9 % is measured against a different baseline.** The post says the adjusted win rates showed MoA stronger *"than **the base models they came from** by 9%"*, whereas the 19.25 % is MoA **vs GPT-4-Turbo**. The two numbers are not the same comparison; MoA itself costs **3–4×** the base model and is **3×** slower, and is positioned as a *synthetic-data generator* whose outputs train a student that is **"1/25th the cost"** and **"1/3rd"** the latency | **The most methodologically honest vendor result here**: it reports an LLM-judge number, then re-scores a 32-sample subset with four human raters and reports the adjusted figure, and it swaps judges (GPT-4-Turbo and Claude) to test self-enhancement bias | The 25× is a Pareto-frontier claim about the *fine-tuned* model, not a measured deployment. ⚠️ **And the "19.25 % → 9 % collapse" this document built on does not exist as stated** (corrected 2026-09-19): 19.25 % is MoA vs GPT-4-Turbo under an LLM judge, 9 % is MoA vs *its own base models* after human adjustment — different baselines, so the pair cannot be read as a judge-validity haircut. The human-adjustment direction *is* published ("the adjustments decreased the degree to which MoA outperforms GPT-4-Turbo") and remains real evidence for doc 00 §5.1; the **magnitude** is not published and must not be quoted as 19.25 → 9 |
| **"50X less to run"** (early post) | *"not only does our fine-tuned model outperform GPT-3.5, it costs **50X less** to run"* | Directionally consistent with §1.2 | 2023-era pricing; against GPT-3.5, not a 2026 incumbent; no task named |
| **Founder's framing** | Fine-tuned models yield "often a **10-100x** improvement" in inference cost; but also *"as open source has become more accessible OpenAI and other closed providers have released newer models at cost points that are notably closer to what you experience using open source models"* (explicitly flagged by the author as "100 % speculation") | The vendor's own honest statement of §1.2's tier-down counter-move | — |

The last row is worth its weight: the company whose entire business was replacing
GPT-4 with fine-tuned models published, in 2024, the observation that the
incumbents' cheap tiers were closing the gap. §1.2's `gpt-5-nano` row is what
that trend looks like two years later.

### 4.2 NVIDIA — the most radical published result, and a deprecated blueprint

From doc 00 §3.1 and §7.1, re-stated here for the economics:

- Internal HR chatbot: a fine-tuned **Llama 3.2 1B reached ≈98 % of the
  production Llama 3.1 70B's accuracy** on the *tool-calling* task, with
  inference cost *"reduce[d] … by up to **98.6 %**"*
  [[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)].
- Separately, `Qwen-2.5-32b-coder` *"did as well as `Llama-3.1-70b-instruct`
  without any fine-tuning"*, cutting cost **and** time-to-first-token by **>50 %**
  [[ibid.](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)].
- **NVIDIA's own scoping caveat**: *"simpler tool calling use cases where an
  agent is using a tool call to route between a small set of tools"*
  [[ibid.](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)].
- **Status: deprecated April 2026**, "no longer actively maintained, and new
  production use is not recommended" [[ibid.](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)].

**Critical read.** A 70× parameter reduction at 98 % accuracy is the strongest
number in this document, and it is also the most narrowly scoped: routing among a
small tool set is close to a classification task, which §5 ranks as the easiest
segment. The second result — that an *un-fine-tuned* open model matched a larger
one — is the more commercially important one, because it is doc 00 §3.2's ladder
rung 1 and it costs nothing to test.

### 4.3 Baseten — the train→deploy seam, with one named customer

Baseten's training page carries a named case: **OpenEvidence** reporting
**"23x faster"** model performance and **"$1.9M projected savings"**, with
non-ML engineers producing results "in under 30 minutes"
[[src](https://www.baseten.co/products/training/)]. It also states the market's
baseline lock-in position verbatim: *"Full ownership of your trained weights, no
lock-in"* [[ibid.](https://www.baseten.co/products/training/)], and that models
trained with Loops "promote directly to Baseten Dedicated Inference with one
command".

**Critical read.** "23x faster" and "$1.9M projected savings" are unqualified —
no baseline model, no task, no measurement method, and "projected" is doing
visible work. Treat as a directional signal that inference vendors are closing
the train→serve seam (which is doc 00 §7.1's conclusion), not as evidence of a
saving magnitude.

### 4.4 AWS Bedrock Model Distillation — the incumbent cloud's version

Amazon Bedrock ships distillation as a managed workflow and, importantly for this
platform, **it can train from production traffic**: *"If you enable CloudWatch
Logs invocation logging, you can use existing teacher responses from invocation
logs stored in Amazon S3 as training data"*, with `requestMetadata` filters so
that "Amazon Bedrock only uses the filtered responses to fine-tune your student
model" [[src](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html)].
It also states the tenancy guarantee this platform must match: *"Only you can
access the final distilled model. Amazon Bedrock doesn't use your data to train
any other teacher or student model for public use."* [[ibid.](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html)]

**No accuracy or cost-saving percentages appear on the documentation page.**
That absence is itself informative: the cloud incumbent ships the mechanism and
declines to publish a parity claim.

**The economics are the interesting part, and they are unfavourable.** Bedrock
prices customization as training tokens + monthly storage + **provisioned
throughput** for serving: e.g. Meta Llama 2 at **$1.49/1M tokens** trained (**that is the Llama 2
Pretrained/Chat 13B rate; the 70B rate on the same page is $7.99/1M** — added
2026-09-19, the single-figure citation previously printed understated the range
by 5.4×),
**$1.95/month** custom-model storage, and **$21.18 per hour per model unit** on a
1-month commitment or **$13.08/hour** at 6 months, with fine-tuned models
"available only in provisioned throughput after customization" for some providers
[[src](https://aws.amazon.com/bedrock/pricing/)]. At $21.18/hour that is
**$15,461/month per model unit** `est.` — **2.9× the $5,402/month of a B300 at
Hyperstack `low`** (§1.3) and **11.8× an RTX PRO 6000**. Also note the data-synthesis
warning: *"your AWS account will incur additional charges for inference calls to
the teacher model … Data synthesis techniques may increase the size of the
fine-tuning dataset to a maximum of 15k prompt-response pairs"*
[[src](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html)].

**Read:** Bedrock's distillation is a *convenience* product for customers already
committed to AWS, not a cost-optimal one, and its 15k-pair ceiling is far below
the 50k used in §1.4's worked one-off. **A platform that self-hosts on rented
GPUs has a 3–12× structural cost advantage over the hyperscaler's own
distillation product**, which is the clearest "why does this company exist"
answer in the document.

⚠️ **TO BE VERIFIED** — whether the Llama 2 rates quoted on the Bedrock pricing
page are representative of 2026 model families, and whether distilled models on
newer bases support on-demand inference. The page as fetched mixes generations.

### 4.5 Academic results for classification and extraction

These are the segment-level evidence that §5's ranking rests on.

| Result | Numbers, verbatim | Why it matters here |
|---|---|---|
| **UniversalNER** (Aug 2023) | "ChatGPT can be distilled into much smaller UniversalNER models for open NER"; evaluated on **43 datasets across 9 domains**; UniversalNER **"outperforms its NER accuracy by 7-9 absolute F1 points in average"** and surpasses "Alpaca and Vicuna by over **30** absolute F1 points" | **The student beats the teacher**, by 7–9 F1, on extraction. This is the single strongest published case for the "extraction" segment and it breaks doc 00 §3.2's "cannot exceed the teacher" rule — because NER has a verifiable structure the teacher is sloppy about | [arXiv 2308.03279](https://arxiv.org/abs/2308.03279) |
| **Distilling Step-by-Step** (May 2023) | "our finetuned **770M T5** model outperforms the few-shot prompted **540B PaLM** model … using only **80 %** of available data on a benchmark, whereas standard finetuning the same T5 model struggles to match even by using 100 % of the dataset"; 4 NLP benchmarks | A **700× parameter reduction** with *less* data, by distilling rationales rather than answers. Directly supports the on-policy/rationale approach in doc 00 §3.2 | [arXiv 2305.02301](https://arxiv.org/abs/2305.02301) |
| **Specializing Smaller LMs** (Jan 2023) | Distilled "from GPT-3.5 (≥ 175B) to T5 variants (≤ 11B)"; **"by paying the price of decreased generic ability, we can clearly lift up the scaling curve of models smaller than 10B towards a specialized multi-step math reasoning ability"** | The specialist trade-off, stated as a finding rather than a slogan: **generic ability is the currency you spend.** This is the mechanism behind every "it regressed on something we didn't test" incident in doc 00 §8.5 | [arXiv 2301.12726](https://arxiv.org/abs/2301.12726) |

Plus, from doc 00 §3.1 (not re-fetched here): Orca (13B, explanation traces),
phi-1 (1.3B, 50.6 % HumanEval from ~7B tokens in 4 days × 8 A100), Zephyr-7B
(dDPO surpassing Llama2-Chat-70B on MT-Bench), STaR ("comparably to fine-tuning a
30× larger model"), MiniLLM (reverse KL), GKD/on-policy distillation, and
Thinking Machines' measured compute case (Qwen3-8B-Base student: AIME'24 60 % →
**74.4 %** in ~150 steps, **1,800 vs 17,920 GPU-hours**, **9–30×** cheaper than
SFT) [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)].

### 4.6 What the case-study literature does *not* contain

Stated explicitly, because absence is the finding:

1. **No published video-understanding distillation at parity.** Doc 00 §3.4 says
   the same. §5.4's video ranking is therefore reasoning from cost structure, not
   from evidence.
2. **No case study with a stated non-inferiority margin, confidence level and
   power.** Not one of the results above reports δ, α and β. Every parity claim
   in the public record is, by doc 00 §5.2's standard, **not a claim**.
3. **No case study reporting a failed distillation.** Publication bias is total.
   The base rate of failure is unknown and should be assumed non-trivial.
4. **No case study reporting the loop's second iteration beating the first.**
   Doc 00 §9.1's criterion 10 — the only criterion that proves a *loop* rather
   than a project — has no published precedent I could find. **This is the
   thesis's largest unproven claim and the platform's most defensible
   differentiator if it can be demonstrated.**

---

## 5. Segments and use cases, ranked by expected win

The ranking axis is **expected saving × probability of reaching parity ÷ cost of
proving it**. Confidence is my assessment of the evidence, not a measurement.

| # | Segment | Typical shape | Expected cost delta vs honest incumbent | Latency delta | Evidence | Confidence |
|---|---|---|---|---|---|---|
| **1** | **Extraction / structured output** (NER, field extraction, doc parsing) | 1–4k in, 50–300 out, verifiable | **10–60×** | TTFT-dominated; likely better | UniversalNER **beats teacher by 7–9 F1** across 43 datasets [[src](https://arxiv.org/abs/2308.03279)]; Axis **>95 %** saving [[src](https://openpipe.ai/)] | **High** |
| **2** | **Classification / routing / triage** | short in, tens of tokens out | **10–60×**, but check §1.2 — the incumbent tier-down is also cheap here | Better | NVIDIA **1B at ≈98 % of 70B** on tool-routing [[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)]; verifiable labels | **High** |
| **3** | **Summarisation** | 1–10k in, 200–500 out | **10–50×** | Better (output-dominated, §1.7) | ART•S **43 % → 85 %**, past **Sonnet 4's 72 %**, for **$22** [[src](https://openpipe.ai/)] | **High** |
| **4** | **Support chat / templated conversation** | 2–8k in (heavily cached), 200–600 out | **2–10×** (caching already captured, §1.7) | Better | OpenPipe/Axis-class; NVIDIA HR chatbot | **Medium-High** |
| **5** | **Video understanding, narrow + long clips** | 240-frame flat vs per-second billing | **30–1,600×** on marginal cost, but see §5.4 — **fixed cost usually dominates** | Flat vs linear in clip length | **None published.** Cost-structure argument only | **Medium** (economics) / **Low** (quality) |
| **6** | **RAG answer synthesis** | huge cached in, medium out | **2–8×** | Better | Confounded by retrieval quality (doc 00 §2.1) | **Medium** |
| **7** | **Agents with tools** | multi-turn, compounding errors | **5–30× if it works** | Mixed (more turns) | NVIDIA's result is scoped to *"a small set of tools"*; doc 00 §3.3: per-tool accuracy degrades with tool count; 0.98¹⁰ = 0.82 session-level | **Low-Medium** |
| **8** | **Coding / SWE agents** | long context, long output, high stakes | 5–30× nominal | Worse per-task (more retries) | phi-1 proves narrow code generation; nothing proves agentic SWE parity. Not Diamond's own answer in this segment is **routing**, not distillation [[src](https://www.notdiamond.ai/)] | **Low** |
| **9** | **Open-ended chat / creative** | unbounded | — | — | No task definition ⇒ no eval ⇒ no parity claim possible (doc 00 §5.2) | **Do not sell** |

### 5.1 Why extraction is first

Three properties compound: (a) **outputs are verifiable** without a judge, which
collapses §1.4(c)'s human-review cost and defuses doc 00 §5.1's judge-validity
problem entirely; (b) **volumes are large and latency-insensitive**, so the batch
discount applies on both sides and utilisation is easy to keep high; and (c) the
teacher is *sloppy* at it, which is why UniversalNER exceeds ChatGPT by 7–9 F1
[[src](https://arxiv.org/abs/2308.03279)] — a specialist trained on a consistent
schema beats a generalist improvising one.

**The catch, and it is the commercial catch:** extraction is also the segment
where the incumbent's cheapest tier is most likely to be good enough. §7.2 shows
`gpt-5-nano` batched-cached at **$1,856/month** for 50M extraction requests,
against a distilled deployment at **$13,140 + $9,501 observability**. **Rung 1 of
doc 00 §3.2's ladder must be run first here, and it will often end the
engagement.** That is fine: the platform still sold the eval harness, and an
honest "you don't need us" is worth more than a failed deployment.

### 5.2 Why agents are hard and coding is hardest

Doc 00 §5.4's arithmetic is the whole argument: per-turn accuracy of 0.98 over a
10-turn session is **0.82 session-level** if errors are independent, and worse if
they compound. To claim session-level non-inferiority at δ=3 pp you need doc 00
§5.3's n at the *session* level, and each session costs 10× a single-turn
example to generate and grade. **The confidence protocol for an agent costs
roughly an order of magnitude more than for a single-turn task, while the
probability of reaching parity is lower.** Both terms move the wrong way.

Coding adds two more: the output is long (favourable on cost, §1.7) but the
failure mode is *silent* (unfavourable on risk, §3.1), and the strongest evidence
in this document about coding agents is that a routing company sells routing
there rather than distillation [[src](https://www.notdiamond.ai/)].

### 5.3 Where the video number comes from

Marginal cost per video, `est.`, with the four options priced as published:

| Clip length | Gemini 3.8 Flash, low-res | …**batch** | Gemini high-res | Twelve Labs Analyze | **Marlin-2B B300** | **Marlin-2B RTX PRO** |
|---|---:|---:|---:|---:|---:|---:|
| 30 s | $0.00447 | $0.00224 | $0.00897 | $0.01683 | **$0.000219** | $0.000251 |
| 1 min | $0.00744 | $0.00372 | $0.01644 | $0.03142 | **$0.000219** | $0.000251 |
| 2 min | $0.01338 | $0.00669 | $0.03138 | $0.06058 | **$0.000219** | $0.000251 |
| 5 min | $0.03120 | $0.01560 | $0.07620 | $0.14808 | **$0.000219** | $0.000251 |
| 10 min | $0.06090 | $0.03045 | $0.15090 | $0.29392 | **$0.000219** | $0.000251 |
| 30 min | $0.17970 | $0.08985 | $0.44970 | $0.87725 | **$0.000219** | $0.000251 |
| 60 min | $0.35790 | $0.17895 | $0.89790 | $1.75225 | **$0.000219** | $0.000251 |

Assumptions, all sourced or stated: Gemini video at **100 tok/s** (low) or **300
tok/s** (high) plus **32 tok/s** audio
[[src](https://ai.google.dev/gemini-api/docs/video-understanding)], priced at
Gemini 3.8 Flash's $0.75/$3.75 [[src](https://ai.google.dev/gemini-api/docs/pricing)],
with a 500-token prompt and 300-token answer; Twelve Labs Analyze at **$1.75/hour
of video input** plus **$7.50/1M** output text [[src](https://www.twelvelabs.io/pricing)];
Marlin-2B at a flat **23,560 prefill tokens** per clip ≥2 min
([`architecture.md` §6.3](../models/marlin2b/architecture.md)) costed at the
`low`-tier input and output cells from [cost-matrix §5 and §2](../matrix/cost-matrix.md).

The ratio Marlin ÷ Gemini-low-res runs **20× at 30 s → 34× at 1 min → 142× at 5
min → 1,634× at 60 min** `est.` **Clip length is the variable.**

### 5.4 The video caveat that flips the conclusion

Marginal cost is not the decision. **A single RTX PRO 6000 running Marlin-2B can
process ~5.47M clips/month** (prefill 49,020 tok/s ÷ 23,560 tok/clip × 730 h)
`est.` — so any realistic video workload uses a fraction of one GPU, the fixed
cost dominates completely, and the comparison becomes `GPU floor vs incumbent
spend`:

**Break-even volume** (videos/month at which two RTX PRO 6000s at $2,628/month
break even), `est.`:

| Avg clip | vs Gemini standard | vs Gemini **batch** | vs Twelve Labs |
|---|---:|---:|---:|
| 1 min | 365,582 | 757,670 | 84,325 |
| 2 min | 200,175 | 408,168 | 43,559 |
| 5 min | 84,915 | 171,222 | **17,777** |
| 10 min | 43,332 | 87,024 | **8,949** |
| 30 min | 14,645 | 29,331 | **2,997** |
| 60 min | 7,348 | 14,706 | **1,500** |

**Reading.** Against **Gemini Flash on short clips, a self-hosted video
specialist essentially never wins** — you need ~750k one-minute clips a month
before two cheap GPUs pay for themselves, and Google's "agentic" mode claiming
"up to 88 % fewer total tokens"
[[src](https://ai.google.dev/gemini-api/docs/video-understanding)] would push that
to ~6M. Against **per-second-priced video APIs** (Twelve Labs) **or long clips**,
it wins easily and early: 3,000 half-hour videos a month is a small workload.

**Therefore, the video pitch is:** long-form content (broadcast, surveillance,
lectures, sports, meetings), or customers on a per-minute video API, or customers
whose privacy posture forbids sending video to Google. **Not** short-clip,
high-volume moderation at Gemini Flash prices. And **multi-tenancy is the only
thing that makes a video product viable at typical volumes**, because 0.2–0.9 %
utilisation (§7.3) is otherwise an unsellable cost structure — which makes doc
01's adapter packing a video *prerequisite*, not an optimisation.

---

## 6. Pricing models for the platform itself

### 6.1 What the market charges — the four models actually in use

| Model | Who uses it | Rate card | What it optimises for |
|---|---|---|---|
| **Per-GPU-time** | Baseten ($0.10833/min H100, $0.16633/min B200 [[src](https://www.baseten.co/pricing/)]), Fireworks ($8/h H100, $13/h B200 [[src](https://fireworks.ai/pricing)]), Together ($5.49/h H100, $8.19/h B200 [[src](https://www.together.ai/pricing)]), Modal/RunPod ([`12` §2.3](../scaling/12-inference-providers.md)) | $0.63–$13/GPU-h, ~5× spread on identical silicon | Vendor margin is predictable; **customer carries the utilisation risk** |
| **Per-token, inference** | Every model API; Baseten Model APIs; Tinker serverless (Inkling-Small **$0.30** in / **$0.06** cached / **$1.20** out, 256K, beta, Inkling-family only [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]) | $0.05–$50/1M | **Vendor carries utilisation risk**; commoditised, 9.5× spread within a single model slug ([`12` §5.3](../scaling/12-inference-providers.md)) |
| **Per-token, training** | Tinker ($0.44–$14.58/1M train [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]), Fireworks ($0.50–**$12**/1M ⚠️, [[src](https://fireworks.ai/pricing)]), Together ($0.34–**$40**/1M ⚠️, [[src](https://www.together.ai/pricing)]), Bedrock ($1.49/1M Llama 2 **13B**, $7.99/1M Llama 2 **70B** [[src](https://aws.amazon.com/bedrock/pricing/)]) | 12–**118×** spread by model class ⚠️ | ⚠️ **Corrected 2026-09-19** — the "$24" (Fireworks) and "$100" (Together) upper bounds printed here are **not on either page as re-fetched**. Fireworks tops out at **$12.00/1M** (full-parameter SFT and full-parameter DPO, 80B–300B band); Together's most expensive listed SFT is **GLM-5.2 at $40.00/1M**. The spread across the four vendors is therefore $0.34 → $40.00 = **118×**, not 290×. If a higher band exists behind a sales gate, cite it; do not carry the old figures. Aligns with work done; **but §1.4(b) shows training is ~3 % of the one-off, so this is not where margin lives** |
| **Per-unit / per-GB / per-score** (observability & evals) | Langfuse ($8→$6 per 100k units [[src](https://langfuse.com/pricing)]), Braintrust ($3/GB + $1.50/1k scores [[src](https://www.braintrust.dev/pricing)]), W&B ($0.10/MB [[src](https://wandb.ai/site/pricing/)]) | $0.006–$0.10 per unit | Scales with *traffic*, not with value; §1.5 shows this can exceed the GPU bill |
| **Platform fee + seats** | Databricks (DBU-based, $0.65/DBU [[src](https://www.databricks.com/product/pricing/mosaic-foundation-model-training)]), W&B ($60/mo Pro + seats [[src](https://wandb.ai/site/pricing/)]), Braintrust ($249/mo Pro), Langfuse ($199/$2,499) | $29–$2,499/mo + usage | Predictable floor; **the only model that funds the engineering in §1.4(d)** |
| **Per-hour of media** | Twelve Labs ($2.50/h index, $1.75/h analyse [[src](https://www.twelvelabs.io/pricing)]) | — | Natural unit for video; **directly comparable to the customer's mental model** |

**Nobody in this survey prices on outcomes.** Not Diamond comes closest by
framing value as a percentage of the customer's model spend (its calculator
models a 25 % reduction on $4.8M/yr [[src](https://www.notdiamond.ai/)]), but
the product is not billed that way on any page fetched.

### 6.2 What this platform should charge, and why

The pricing model has to satisfy four constraints that fall straight out of §1
and §3:

1. **Margin cannot come from tokens.** §1.2: the incumbent's cheapest tier is at
   or below our marginal cost. A per-token markup would price us out of exactly
   the workloads we are best at.
2. **Margin cannot come from training.** §1.4(b): $224–$2,708 per iteration,
   commoditised, 12× vendor spread.
3. **Margin cannot come from traces.** §1.5: per-unit trace pricing is what makes
   Weave unusable at volume; replicating it would make *us* unusable at volume.
4. **Cost of goods is idle GPU** (§1.3), which is a *fixed* cost — so a purely
   usage-based price transfers the utilisation risk to us at precisely the
   volumes where it is worst.

That leaves one coherent structure:

> **A platform subscription that prices the confidence protocol, plus inference
> at cost-plus, plus a one-time engagement fee for iteration 1.**

| Component | Basis | Rationale |
|---|---|---|
| **Engagement fee, iteration 1** | Fixed, $40k–$150k, scoped to the §1.4 table | Covers the engineering that is 85–95 % of the one-off. Falls for customers 2..N in a vertical — that is the platform's compounding advantage |
| **Platform subscription** | Per *task* per month (a task = one endpoint pair `main`/`dev` with its eval suite), e.g. $3k–$15k/task/mo by volume band | Prices the two terms the platform controls in §3.4 (`P(regression)` and `MTTD`). Predictable to both sides. Funds the loop costs in §1.5 |
| **Inference** | Cost-plus on GPU-time, with a published multiple (e.g. 1.3–1.6× the `low` rate from [cloud-pricing §5.14](../cross-cutting/cloud-pricing.md)) | Transparent; lets the customer verify §1's arithmetic; the multiple covers multi-tenant packing risk. **Pass the utilisation benefit of adapter packing to the customer** — it is a better story than keeping it |
| **Shadow / A-B period** | Metered separately, at cost | §1.5: shadow doubles serving load for a bounded period. Hiding it in the subscription makes the subscription look expensive |
| **Teacher tokens** | Pass-through at cost, itemised by teacher | Doc 00 §8.1 requires a per-tenant audit trail of which teacher produced which label anyway; billing is the natural place for it |

**Why not outcome-based.** It is the most attractive story ("we take 20 % of what
we save you") and the worst mechanism, for three reasons: the baseline is the
customer's *honest* price (§1.2), which they can lower unilaterally at any time
by tiering down; the saving is measured in their billing system, not ours; and a
regression costs them **18–46× the saving** depending on the §3.1 row (18.5× in
the base case; the flat "40×" previously printed here was not the base case —
corrected 2026-09-19), so an incentive tied only to savings
is an incentive to under-invest in the gates. ⚠️ A *bounded* outcome component —
a discount if the promoted model fails its eval within 90 days — is defensible
and is really a warranty, not outcome pricing. Recommend that instead.

**Why not per-token.** Covered above, and there is a second reason: per-token
billing forces the platform to have an opinion about the customer's token count,
which invites the exact arguments (caching, batching, output length) that §1.7
shows are where the incumbents' pricing is already contested.

### 6.3 Price points implied by the worked examples

From §7, `est.`, at the "typical" one-off of $66,600:

| Profile | Gross saving vs honest incumbent | Platform take at 30 % of saving | Implied subscription |
|---|---:|---:|---:|
| §7.1 support chat, 2M req/mo, vs Sol batched+cached | $6,477/mo | $1,943/mo | too thin — **price at cost + $3k/mo or decline** |
| §7.1 same, vs Sol at list | $29,037/mo | $8,711/mo | **$8k–$10k/mo** is defensible |
| §7.2 extraction, 50M req/mo, vs Terra batched+cached | $36,609/mo | $10,983/mo | **$10k–$12k/mo** |
| §7.3 video, 50K clips/mo, 30-min avg, vs Twelve Labs | $41,021/mo | $12,306/mo | **$12k/mo** |

The pattern: **the sellable engagements are the ones with a five-figure monthly
saving, and they are exactly the ones §1.6's qualification filter admits.** The
pricing model does not need to be clever; the qualification does.

---

## 7. Worked examples

All three use the same method: compute the incumbent at **four** price points
(list at h=50 %, cached at h=90 %, batched, and batched+cached — the honest
floor), compute the distilled option **including the GPU floor and the
observability bill**, then amortise. Arithmetic is `est.` throughout and was
generated with `python3`.

### 7.1 Profile A — support chat, 2M requests/month, currently on GPT-5.6 Sol

**Shape:** 4,000 input tokens (a large cached system prompt plus conversation),
400 output tokens. 8.00B input and 0.800B output tokens/month.

**Incumbent, `est.`:**

| Option | h=50 %, standard | h=90 %, standard | h=50 %, batch | **h=90 %, batch** |
|---|---:|---:|---:|---:|
| **GPT-5.6 Sol** (current) | **$33,600** | $22,080 | $16,800 | **$11,040** |
| Claude Opus 5 (alternative) | $42,000 | $27,600 | $21,000 | $13,800 |
| GPT-5.6 Terra (tier-down) | $18,400 | $12,640 | $9,200 | $6,320 |
| Claude Haiku 4.5 (tier-down) | $8,400 | $5,520 | $4,200 | $2,760 |
| GPT-5.6 Luna (tier-down) | $1,840 | $1,264 | $920 | **$632** |

**Distilled: Qwen3.8-27B.** Capacity, `est.`:

| GPU | Prefill h/mo | Decode h/mo | Total GPU-h/mo | Util of 1 GPU | Marginal token cost |
|---|---:|---:|---:|---:|---:|
| B300 ($7.40/h) | 50 | 18 | **68** | **9.3 %** | $500 |
| H200 ($3.99/h) | 205 | 32 | **237** | **32.5 %** | $946 |
| **RTX PRO 6000 ($1.80/h)** | 280 | 108 | **388** | **53.2 %** | $699 |

**The B300 is the wrong machine for this workload.** At 9.3 % utilisation its
delivered cost is $0.65/1M (§1.3) — worse than Gemini 3.8 Flash batched. The RTX
PRO 6000 at 53 % is the right class, and is *also* the cheapest cell available.
Chosen configuration: **2 production replicas + 1 dev replica** (doc 01's
`main`/`dev` split, doc 00 invariant I7) = **$3,942/month**.

**Total monthly cost of the distilled option, `est.`:**

| Line | Cost |
|---|---:|
| 3 × RTX PRO 6000 | $3,942 |
| Observability (Langfuse Cloud, 6M units) | $621 |
| Observability (Langfuse **self-hosted**, ~1 vCPU-heavy node) | ~$150 ⚠️ |
| Disagreement judging, 10 % of traffic | $2,400 (during shadow/A-B only) |
| **Steady-state total (self-hosted Langfuse)** | **≈ $4,092** |
| **Steady-state total (Langfuse Cloud)** | **≈ $4,563** |

**Payback**, against the Langfuse Cloud figure, `est.`:

| vs. | Incumbent $/mo | Monthly saving | $40k one-off | $80k | $150k |
|---|---:|---:|---:|---:|---:|
| Opus 5 at list (h=50 %) | $42,000 | $37,437 | **1.1 mo** | 2.1 mo | 4.0 mo |
| GPT-5.6 Sol at list (h=50 %) | $33,600 | $29,037 | **1.4 mo** | 2.8 mo | 5.2 mo |
| Sol, cached (h=90 %) | $22,080 | $17,517 | 2.3 mo | 4.6 mo | 8.6 mo |
| Opus 5, batched + cached | $13,800 | $9,237 | 4.3 mo | 8.7 mo | 16.2 mo |
| **Sol, batched + cached (the honest floor)** | **$11,040** | **$6,477** | **6.2 mo** | **12.4 mo** | 23.2 mo |
| Haiku 4.5, batched + cached | $2,760 | −$1,803 | **never** | never | never |
| Luna, batched + cached | $632 | −$3,931 | **never** | never | never |

**Confidence-protocol cost for this engagement** (§1.4(c)), at δ=3 pp, 5 slices,
p≈0.85: n = **1,752 per arm**, judge cost **$210**, human gold set of 200
examples **$1,333**, plus a shadow period costing **+$698–$1,314/month** of extra
GPU and **$2,400/month** of disagreement judging. **Total cost of manufacturing
the confidence: under $6,000 for a one-month shadow.** Against §3.1's
$120,000/month regression exposure, that is a 5 % premium on one month of risk.

**Verdict.** This engagement is sellable **if and only if** the customer's real
price is list-ish. If they already batch and cache, the payback is 6–12 months
and the case rests on latency and quality, not cost. If they can tier down to
Haiku or Luna and pass their eval, **the correct advice is to do that and buy
only the eval harness** — doc 00 §3.2's rung 1.

### 7.2 Profile B — extraction, 50M requests/month, currently on a mid-tier model

**Shape:** 1,500 input, 150 output. 75.0B input and 7.50B output tokens/month.

**Incumbent, `est.`:**

| Option | h=50 %, standard | h=90 %, standard | **h=90 %, batch** |
|---|---:|---:|---:|
| GPT-5.6 Terra | **$172,500** | $118,500 | **$59,250** |
| Claude Sonnet 5 | $157,500 | $103,500 | $51,750 |
| **Gemini 3.8 Flash** (promo price) | $59,062 | $38,812 | **$19,406** |
| Gemini 3.8 Flash (post-2026-12-31 list) | $118,125 | $77,625 | $38,812 |
| GPT-5.6 Luna | $17,250 | $11,850 | $5,925 |
| **gpt-5-nano** | $5,062 | $3,712 | **$1,856** |

**Distilled: Qwen3.8-27B.** Capacity, `est.`:

| GPU | Total GPU-h/mo | GPU-equivalents | Sized at 60 % util | Fixed $/mo |
|---|---:|---:|---|---:|
| B300 ($7.40/h) | 633 | 0.87 | 2 prod + 1 dev | $16,206 |
| **B200 ($6.00/h)** | 748 | 1.02 | **2 prod + 1 dev** | **$13,140** |
| RTX PRO 6000 ($1.80/h) | 3,640 | 4.99 | 9 prod + 1 dev | $13,140 |

Note the coincidence and what it means: **B200 and RTX PRO 6000 cost the same at
this workload** ($13,140/month), but the B200 configuration is 3 machines and the
RTX configuration is 10. Prefer the B200 for operational reasons
([`scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md)),
and note that at `res1y` the ranking changes again — Qwen3.8-27B's cheapest blend
moves from B300 to **B200 at $0.0538** ([cost-matrix §7.4](../matrix/cost-matrix.md)).

**Total monthly cost, `est.`:** $13,140 GPU + $9,501 Langfuse Cloud (150M units)
= **$22,641**, or **$13,140 + ~$600 self-hosted Langfuse ≈ $13,740** ⚠️
(self-hosting infra cost is an estimate).

**Payback**, using the Langfuse Cloud figure, `est.`:

| vs. | Incumbent $/mo | Monthly saving | $80k one-off | $150k | $300k |
|---|---:|---:|---:|---:|---:|
| GPT-5.6 Terra at list | $172,500 | $149,859 | **0.5 mo** | 1.0 mo | 2.0 mo |
| Terra, batched + cached | $59,250 | $36,609 | 2.2 mo | 4.1 mo | 8.2 mo |
| Gemini 3.8 Flash, list h=50 % | $59,062 | $36,421 | 2.2 mo | 4.1 mo | 8.2 mo |
| Sonnet 5, batched + cached | $51,750 | $29,109 | 2.7 mo | 5.2 mo | 10.3 mo |
| **Gemini 3.8 Flash, batched + cached (promo)** | **$19,406** | **−$3,235** | **never** | never | never |
| Gemini 3.8 Flash, batched + cached (post-promo) | $38,812 | $16,171 | 4.9 mo | 9.3 mo | 18.6 mo |
| Luna, batched + cached | $5,925 | −$16,716 | never | never | never |
| gpt-5-nano, batched + cached | $1,856 | −$20,785 | never | never | never |

**Two findings that should change how this is sold.**

1. **Self-hosting Langfuse converts one "never" into a win.** At $13,740/month
   total the Gemini-batched comparison becomes +$5,666/month, which pays back a
   $80k one-off in 14 months. **The observability vendor choice is, at this
   volume, the difference between a viable and a non-viable engagement.**
2. **A scheduled incumbent price change decides the deal.** Gemini 3.8 Flash
   reverts to $1.50/$7.50 on 2027-01-01
   [[src](https://ai.google.dev/gemini-api/docs/pricing)], moving the same
   comparison from "never" to **4.9 months**. A business case for this customer
   is only valid with a date on it.

**Confidence-protocol cost.** Extraction is verifiable, so the judge is largely
replaced by programmatic checks against a schema and a gold set — doc 00 §5.4's
"verifiable environments" point, and Snorkel's "programmatic pass/fail criteria"
[[src](https://snorkel.ai/)]. Cost collapses to the gold set (**$1,333–$3,333**)
plus harness engineering. At δ=2 pp with 5 slices the judged cost would be $473;
with programmatic grading it is **~$0**. **This is why extraction ranks first in
§5: the confidence is nearly free.**

### 7.3 Profile C — video captioning, 50,000 videos/month, currently on Gemini

**Shape:** 50,000 clips/month, 500-token instruction, 300-token caption out. Cost
computed at four average clip lengths because §5.4 shows that is the variable.

**Incumbent, `est.`:**

| Avg clip | Gemini 3.8 Flash std | Gemini 3.8 Flash **batch** | Gemini high-res std | **Twelve Labs Analyze** |
|---|---:|---:|---:|---:|
| 1 min | $372 | $186 | $822 | $1,571 |
| **5 min** | **$1,560** | **$780** | $3,810 | **$7,404** |
| 10 min | $3,045 | $1,522 | $7,545 | $14,696 |
| 30 min | $8,985 | $4,492 | $22,485 | $43,862 |

**Distilled: Marlin-2B**, flat at 23,560 prefill tokens/clip, `est.`:

| Config | Marginal $/mo | GPU-h/mo | Util of 1 GPU | Fixed $/mo (2 GPUs) |
|---|---:|---:|---:|---:|
| B300 | $10.93 | 1.4 | **0.20 %** | $10,804 |
| **RTX PRO 6000** | $12.57 | 6.7 | **0.91 %** | **$2,628** |

**The marginal cost is $11–$13 per month. The fixed cost is $2,628.** There is no
meaningful token economics here at all; this is a capacity-planning problem
disguised as a pricing problem.

**Verdict by clip length, against a 2×RTX PRO 6000 deployment at $2,628 + ~$200
observability = $2,828/month:**

| Avg clip | vs Gemini batch | vs Gemini std | vs Twelve Labs | Verdict |
|---|---:|---:|---:|---|
| 1 min | −$2,655/mo | −$2,469/mo | −$1,270/mo | **Loses to everything.** Stay on the API |
| 5 min | −$2,061/mo | −$1,281/mo | **+$4,563/mo** | Wins only against per-minute video APIs |
| 10 min | −$1,319/mo | +$204/mo | **+$11,855/mo** | Marginal vs Gemini; strong vs Twelve Labs |
| 30 min | **+$1,651/mo** | **+$6,144/mo** | **+$41,021/mo** | **Wins outright** |

**Payback at 30-minute clips**, one-off assumed higher for video because doc 00
§5.5 requires a separately-resourced eval (Video-MME's construction implies
roughly 3 human-authored items per video
[[src](https://arxiv.org/abs/2405.21075)]):

| vs. | Monthly saving | $80k one-off | $150k |
|---|---:|---:|---:|
| Gemini 3.8 Flash batched | $1,651 | 48 mo | 91 mo |
| Gemini 3.8 Flash std | $6,144 | 13 mo | 24 mo |
| Twelve Labs | $41,021 | **2.0 mo** | 3.7 mo |

**Confidence-protocol cost for video is the binding constraint, not the GPU.**
There is no cheap judge — a video judge is a frontier multimodal call per
example, so at δ=3 pp and n=1,752 per arm, judging with Gemini 3.1 Pro on
5-minute clips costs `est.` **1,752 × 2 orders × ~$0.12 = $420**, which is still
small; but the *human* gold set is the problem. At Video-MME's implied density
and a 5-minute clip taking ~10 minutes to adjudicate, a 200-clip gold set is ~33
SME-hours = **$2,667** `est.`, and a defensible 500-clip set is **$6,667**. Add
doc 00 §5.5's open question — whether frame-sampled proxy evals on the same
240-frame budget the model sees are a valid stand-in for full-clip human grading
— and the video engagement carries **one-off eval costs 2–5× a text engagement's
and an unresolved methodological risk on top**.

**Recommendation.** Sequence video second (doc 00 §9.2 says the same), and
qualify on **two** filters rather than one: average clip length ≥ 10 minutes
**and** an incumbent priced per unit of media time. At 50K clips/month of
5-minute video on Gemini Flash — the literal profile requested — **the honest
answer is that the customer should stay on Gemini**, and the platform's value to
them is the eval harness and the privacy posture, not the cost.

---

## Implications for the platform

### What to build

1. **The cost model as a live artifact, not a slide.** It must hold each
   incumbent's *published* rates with their effective dates (Gemini's promo
   expiry is the proof), the customer's measured cache-hit rate and output share,
   the measured utilisation of their replica, and the amortisation clock. Doc 00
   §9.1 criterion 8 requires cost to be "re-run at promotion"; this is the thing
   that re-runs it. **It is also the best demo in the product**, because §1.7's
   diagnosis of a badly-structured prompt stack (paying cache-*write* premiums on
   Anthropic) can pay for the engagement before any model is trained.
2. **A qualification calculator, used before any engineering.** Inputs: monthly
   requests, token shape, incumbent model, whether they batch, measured cache hit
   rate. Output: honest incumbent price, the right GPU class, delivered $/1M at
   the implied utilisation, break-even and payback. **It must be willing to
   output "do not do this"**, and §7.3 shows the flagship video profile is one of
   those cases.
3. **Right-sizing and multi-tenant packing as first-class features.** §1.3: the
   difference between a B300 at 9 % and an RTX PRO 6000 at 53 % is 4.1× of fixed
   cost for the same workload. §5.4: video at 0.9 % utilisation is unsellable
   single-tenant. S-LoRA-style unified paging [[src](https://arxiv.org/abs/2311.03285)]
   is the mechanism, and it converts §1.3's delivered-cost table from the
   platform's worst problem into its moat.
4. **The frontier fallback route, in the MVP, with `f` as a monitored SLI.** §3.3:
   a 20 % fallback still delivers a 4.6× saving and buys the customer's
   permission. FrugalGPT and RouteLLM are the evidence
   [[src](https://arxiv.org/abs/2305.05176)] [[src](https://arxiv.org/abs/2406.18665)].
5. **Shadow and disagreement judging as *metered, itemised* line items.** §1.5:
   shadow doubles serving load and disagreement judging is ~$2,400/month at 2M
   requests. Hiding these in a subscription makes the subscription look
   expensive; itemising them makes the protocol look rigorous.
6. **A warranty clause, not outcome pricing.** §6.2: a discount if the promoted
   model fails its eval within 90 days aligns incentives on the two terms the
   platform actually controls (§3.4).

### What to buy

1. **Training. Always.** §1.4(b): $224–$2,708 per iteration, 12× vendor spread,
   four credible vendors with published rate cards. Route each job to the
   cheapest vendor for that student class; build only the orchestration and the
   run record. This is doc 00 §6's doc-05 recommendation, now priced.
2. **Burst and low-volume serving.** [`12` §5.5](../scaling/12-inference-providers.md)
   is unambiguous: self-hosting wins decisively for a dense model that fits on
   one GPU and loses for every MoE that does not. Buy tokens for
   DeepSeek-V4.1-Flash-class and Kimi-K3-class; self-host Qwen3.8-27B and
   Marlin-2B.
3. **Teacher tokens from an open-weights teacher, self-hosted.** §1.4(a):
   Kimi-K3 on B300 labels 50k examples for **$406**, undercutting every frontier
   teacher *and* removing doc 00 §8.1's ToS exposure. The cheap answer and the
   safe answer coincide.

### What to avoid

1. **Never quote list price.** §1.2. Quote against batched, cached, tier-downed.
   Every credible buyer will make this correction in the first meeting; making it
   first is worth more than the number it costs.
2. **Never sell against a nano/Luna/Flash-Lite-class incumbent on cost.** §1.2:
   `gpt-5-nano` batched-cached is $0.0536/1M, below our best self-hosted cell.
   §5.1: this is most likely to happen in the segment we are best at.
3. **Never put the loop on per-unit-priced observability.** §1.5: W&B Weave at
   50M requests is $33,698/month and would consume the entire saving. Self-host
   Langfuse.
4. **Never buy the hyperscaler's distillation product for cost reasons.** §4.4:
   Bedrock's provisioned-throughput requirement ($21.18/hour/model unit
   [[src](https://aws.amazon.com/bedrock/pricing/)]) is 2.9–11.8× a rented GPU,
   and its data-synthesis path caps the fine-tuning set at 15k pairs.
5. **Never lead with cost on video at short clip lengths.** §5.4, §7.3.
6. **Never present a parity claim without δ, α and β.** §4.6: not one published
   case study in this survey does, which means the bar to clear is embarrassingly
   low and clearing it is a differentiator.

---

## Open questions

Consolidated ⚠️ items, ordered by how much they move the numbers.

1. **⚠️ Do batch discounts and prompt caching compose on OpenAI and Anthropic?**
   **Narrowed 2026-09-19.** **Google is settled** — its pricing page publishes a
   *Batch cached input* rate per model (Gemini 3.8 Flash $0.0375, half the
   standard cached $0.075), so the discounts stack
   [[src](https://ai.google.dev/gemini-api/docs/pricing)], which settles §7.2's
   decisive Gemini rows. OpenAI and Anthropic publish a batch table and a cached
   column but **no batch × cached cell and no statement either way**, so §1.2's
   "honest floor" for `gpt-5-nano`, Sol, Terra, Opus 5 and Fable 5.1, and every
   payback row in §7.1, still rests on the assumption. If it fails there, those
   floors rise 20–40 % and those paybacks improve correspondingly. **Still the
   highest-leverage open fact in this document, now scoped to two vendors.**
2. **⚠️ What is the real cost of a bad outcome, per segment?** §3.1's entire
   argument — that the risk is **18–46×** the saving (18.5× in the base case;
   corrected 2026-09-19 from a flat "~40×") — rests on assumed dollar values
   for an escalated ticket, a bad extraction and a wrong high-stakes answer. No
   public benchmark reached. Until these are sourced *per customer*, the
   insurance framing is a structure, not a valuation.
3. **⚠️ Is there a measured relationship between LLM TTFT/TPOT and business
   outcomes?** §2.2 has only web-page-load studies (Deloitte, web.dev) and
   Nielsen's 1993 perception thresholds. The extrapolation to streaming LLM
   responses is unproven and load-bearing for any latency-led pitch.
4. **⚠️ What does Marlin-2B actually cost to run end-to-end?** Every video figure
   in §5.3, §5.4 and §7.3 is a GPU-inference cost, while
   [`architecture.md` §12 #6](../models/marlin2b/architecture.md) warns that
   torchcodec/NVDEC decode for 240 frames at 448×448 is unmeasured everywhere and
   may be the actual bottleneck. **A two-day measurement that could invalidate a
   whole segment.**
5. **⚠️ What is the fully-loaded engineering cost of iteration 1, and of
   iteration N?** §1.4(d) is the largest line item and the only one with no
   source. The platform's entire compounding story is that iteration N is much
   cheaper than iteration 1; nobody has measured either.
6. **⚠️ What does an enterprise actually pay per adjudicated example?** §1.4(c)'s
   $80/h loaded SME rate is assumed. Doc 00 §4.3(c) flags the same gap. Snorkel
   sells this as a premium service [[src](https://snorkel.ai/)] but publishes no
   rate.
7. **⚠️ Is `f` (the frontier-fallback fraction) stable?** §3.3's economics survive
   f=20 %, but if traffic drift pushes f toward 50 % the case collapses. No
   published data on fallback-rate drift in production routers.
8. **⚠️ How much does Gemini's "agentic" video mode actually reduce token spend in
   practice?** "Up to 88 % fewer total tokens … for long-form content"
   [[src](https://ai.google.dev/gemini-api/docs/video-understanding)] is an
   upper bound on the vendor's own page. If typical is 50 %, §5.4's break-even
   volumes roughly double; if typical is 88 %, they multiply by ~8 and the video
   case against Gemini disappears at every clip length.
9. **⚠️ Why are four vendors retreating from the loop?** §4.1: OpenPipe's
   Evaluations/Criteria/Pruning/Relabeling features sunset 2026-07-30, NVIDIA's
   blueprint deprecated, NeMo Microservices sunset 2026-10-01, OpenAI's
   fine-tuning and Evals winding down. **If the reason is that customers will not
   pay for the loop, the pricing model in §6.2 is wrong.** This is a
   customer-research question, not a web-research question.
10. **⚠️ Predibase's status could not be confirmed.** `predibase.com`,
    `docs.predibase.com` and `predibase.com/pricing` all 301 to
    `rubrik.com/products/rubrik-agent-cloud`, which returns **HTTP 403** to both
    WebFetch and curl. The redirect is strong evidence of a Rubrik acquisition;
    the date and terms are unverified. Doc 00 §7.1 records the same failure.
    **Narrowed 2026-09-19:**
    [doc 07 §4.5](07-competitor-analysis.md) takes this further and should be
    read instead of this item — it confirms Rubrik's DNS/HTTP control by `curl`
    and resolves the **fate of LoRAX**, which is live, Apache-2.0 and
    independently citable [[src](https://github.com/predibase/lorax)]. Only the
    acquisition date, terms, and the fate of Turbo LoRA / reinforcement
    fine-tuning remain open.
11. **⚠️ Is a second loop iteration cheaper and better than the first?** §4.6:
    no published precedent. Doc 00 §9.1 criterion 10 makes it the MVP's decisive
    test, and §6.2's pricing assumes it. If it is false, this is a consultancy.
12. **⚠️ Self-hosted Langfuse infrastructure cost.** §7.1 and §7.2 assume
    ~$150–$600/month; not measured. It is the hinge on which §7.2's
    Gemini-batched comparison turns from "never" to a 14-month payback.
13. **⚠️ Do the Bedrock rates quoted for Llama 2 generalise to 2026 model
    families, and do distilled models there now support on-demand inference?**
    §4.4's 2.9–11.8× advantage depends on it.

---

## Sources

All fetched **2026-09-19**. `†` = fetched via `curl` because the page is a
client-rendered SPA and its content lives in a JavaScript bundle.

**Vendor pricing (incumbents)**
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) — GPT-6 Astra, GPT-5.6 Sol/Terra/Luna, gpt-5-mini/nano, gpt-4.1-mini/nano, Sora-2, batch 50 %.
- [Anthropic / Claude pricing](https://claude.com/pricing) — Fable 5.1, Opus 5, Sonnet 5, Haiku 4.5; cache read/write; batch 50 %.
- [Google Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) — Gemini 3.8/3.7/3.6 Flash, 3.5 Flash/Flash-Lite, 3.1 Pro, context-cache storage, Veo 3.1, batch 50 %, promo expiry 2026-12-31.
- [Google Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding) — 1 FPS, 100/300 tokens per second of video, 32 tokens/s audio, 3-hour limit, agentic mode "up to 88 % fewer total tokens".

**Vendor pricing (platforms, training, serving, observability)**
- [Baseten pricing](https://www.baseten.co/pricing/) — dedicated GPU $/min and $/h; Model API per-token rates.
- [Baseten Training / Loops](https://www.baseten.co/products/training/) — GA/early-access status, "Full ownership of your trained weights, no lock-in", OpenEvidence "23x faster" / "$1.9M projected savings".
- [Tinker model pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/) — prefill/sample/train $/1M for Qwen3.8-27B, Inkling, Inkling-Small, Nemotron, GLM-5.3; 80 % cached-prefill discount; $0.10/GB-month checkpoints; serverless beta rates.
- [Fireworks pricing](https://fireworks.ai/pricing) — LoRA/full SFT and DPO $/1M by model size band; on-demand H100/H200 $8/h, B200 $13/h.
- [Together pricing](https://www.together.ai/pricing) — serverless per-token, SFT/DPO $/1M, dedicated H100 $5.49/h (promo $3.99 to 09/30/26), B200 $8.19/h.
- [Databricks foundation-model training pricing](https://www.databricks.com/product/pricing/mosaic-foundation-model-training) — DBU tables for Llama 3.1/3.2/3.3 at 10M and 500M words, $0.65/DBU.
- [AWS Bedrock Model Distillation docs](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html) — invocation-log training path, `requestMetadata` filtering, tenancy guarantee, 15k-pair synthesis ceiling.
- [AWS Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) — customization $/1M trained, $1.95/mo storage, provisioned throughput $21.18/h (1-mo) and $13.08/h (6-mo) per model unit.
- [Langfuse pricing](https://langfuse.com/pricing) — plan tiers, billable-unit definition, graduated overage $8/$7/$6.50/$6 per 100k, free self-hosting.
- [Braintrust pricing](https://www.braintrust.dev/pricing) — Starter/Pro/Enterprise, $3–$4/GB, $1.50–$2.50 per 1,000 scores.
- [Weights & Biases pricing](https://wandb.ai/site/pricing/) — Pro from $60/mo, Weave ingestion 1.5 GB included then $0.10/MB, storage $0.03/GB.
- [Twelve Labs pricing](https://www.twelvelabs.io/pricing) — $2.50/h indexing, Analyze $1.75/h video in + $7.50/1M out, Embed rates.
- [OpenAI supervised fine-tuning guide](https://developers.openai.com/api/docs/guides/supervised-fine-tuning) — "winding down the fine-tuning platform"; supported bases; the documented distillation workflow.

**Case studies and competitor claims**
- [OpenPipe archive](https://openpipe.ai/) † — CoreWeave acquisition (2025), migration to W&B, legacy platform deprecation **2026-07-30**, sunset feature list (Reward Models, Managed Evaluations, Criteria, Pruning Rules, Relabeling Workflows, **DPO**); **Axis case study** (>95 % per-token saving vs GPT-4-Turbo on a custom Mistral 7B); **ART•S** (Qwen 2.5 14B 43 % → 85 % vs Sonnet 4 72 %, **$15 GPU + $7 judge = $22**, 5 h on one RunPod H100); **Mixture of Agents** (19.25 % LLM-judge improvement **vs GPT-4-Turbo**; 9 % human-adjusted **vs its own base models** — different baselines, see §4.1's ⚠️; 3–4× cost / 3× latency, "1/25th the cost" fine-tuned student); "50X less to run"; the "10-100x" and tier-down-convergence framings.
- [NVIDIA AI Blueprint: data flywheel](https://github.com/NVIDIA-AI-Blueprints/data-flywheel) — Llama 3.2 1B at ≈98 % of Llama 3.1 70B on tool calling, "up to 98.6 %" cost reduction, >50 % cost and TTFT reduction for `Qwen-2.5-32b-coder`, the "small set of tools" scoping caveat, deprecation April 2026. *(Re-cited from doc 00 §3.1/§7.1; not re-fetched in this session.)*
- [Not Diamond](https://www.notdiamond.ai/) — "5 %+" accuracy, "20 %+" cost, "2x" cycles, Rootly 39 % accuracy increase, $4.8M→$3.6M calculator; no published methodology.
- [Snorkel AI](https://snorkel.ai/) — expert data development, programmatic pass/fail agent benchmarks. *(Re-cited from doc 00 §6/§7.1.)*
- [Thinking Machines, on-policy distillation](https://thinkingmachines.ai/blog/on-policy-distillation/) — AIME'24 60 % → 74.4 %, 1,800 vs 17,920 GPU-hours, 9–30× cheaper than SFT. *(Re-cited from doc 00 §3.1.)*

**Papers**
- [FrugalGPT, arXiv 2305.05176](https://arxiv.org/abs/2305.05176) — "up to 98 % cost reduction" matching GPT-4, or "+4 % accuracy at the same cost"; prompt adaptation / LLM approximation / LLM cascade.
- [RouteLLM, arXiv 2406.18665](https://arxiv.org/abs/2406.18665) — strong/weak routing from preference data, "over 2 times" cost reduction, transfer across model pairs.
- [UniversalNER, arXiv 2308.03279](https://arxiv.org/abs/2308.03279) — ChatGPT distilled to a small NER model that **exceeds the teacher by 7–9 absolute F1** across 43 datasets / 9 domains.
- [Distilling Step-by-Step, arXiv 2305.02301](https://arxiv.org/abs/2305.02301) — 770M T5 > 540B few-shot PaLM using 80 % of the data.
- [Specializing Smaller LMs, arXiv 2301.12726](https://arxiv.org/abs/2301.12726) — GPT-3.5 → ≤11B T5; "by paying the price of decreased generic ability".
- [S-LoRA, arXiv 2311.03285](https://arxiv.org/abs/2311.03285) — unified paging, "up to 4×" throughput over HF PEFT and vLLM. *(Re-cited from doc 00 §4.5.)*
- [Video-MME, arXiv 2405.21075](https://arxiv.org/abs/2405.21075) — 900 videos / 254 hours / 2,700 expert-labelled QA pairs. *(Re-cited from doc 00 §3.4/§5.5.)*
- [LLM-as-a-judge / MT-Bench, arXiv 2306.05685](https://arxiv.org/abs/2306.05685) — >80 % judge–human agreement; position, verbosity and self-enhancement bias. *(Re-cited from doc 00 §5.1.)*

**Latency and business impact**
- [Deloitte Ireland / Google, "Milliseconds Make Millions", 2020-03-24](https://www.deloitte.com/ie/en/services/consulting/research/milliseconds-make-millions.html) — 0.1 s improvement → retail conversions +8.4 %, AOV +9.2 %; travel +10.1 % / +1.9 %; luxury page views/session +8.6 %; lead-gen bounce −8.3 %.
- [web.dev, Core Web Vitals business impact](https://web.dev/case-studies/vitals-business-impact) — Vodafone +8 % sales, Lazada +16.9 % mobile conversion, Tokopedia +23 % session duration, GYAO +108 % CTR, Agrofy −76 % load abandonment, and others.
- [Nielsen Norman Group, response-time limits, 1993-01-01](https://www.nngroup.com/articles/response-times-3-important-limits/) — 0.1 s / 1.0 s / 10 s.

**This repository**
- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — blended-cost identity, `low`/`high`/`res1y` tiers, S1–S4 scenarios, the "use the vendor's published cached rate" rule.
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) — §2 S1 output $/1M, §4 blended $/1M, §5 input $/1M, §6.2 break-even utilisation, §7.2 prefix-cache sensitivity, §7.4 `res1y` reordering.
- [`research/matrix/recommendations.md`](../matrix/recommendations.md), [`research/matrix/fit-matrix.md`](../matrix/fit-matrix.md), [`research/matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md).
- [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §5.14 — the pinned $/GPU-hour rows used for every fixed-cost figure here.
- [`research/scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md), [`05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md), [`06-cold-start.md`](../scaling/06-cold-start.md), [`08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md).
- [`research/scaling/12-inference-providers.md`](../scaling/12-inference-providers.md) §2.3 / §5.6 — serverless $/GPU-hour normalisation, ~5× spread on identical silicon ($1.89 fal → $10.00 HF-on-GCP), and the scale-to-zero premium, which that document **corrected on 2026-09-19 from a flat "≈ +20 %" to a 20–37 % range** (RunPod's serverless docs table vs its pricing page). Quote the range, not the point.
- [`research/scaling/12-inference-providers.md`](../scaling/12-inference-providers.md) §5.2, §5.3, §5.5 — the Qwen3.8-27B-class endpoints, the 9.5× within-slug input spread, and the self-host-vs-buy verdict per model. ⚠️ §5.2's own sourcing note says the "19 endpoints" heading counts three non-OpenRouter rows (Cerebras, Groq, Qwen Cloud list) and omits two OpenRouter ones; the OpenRouter listing returns 18.
- [`research/models/marlin2b/README.md`](../models/marlin2b/README.md), [`architecture.md`](../models/marlin2b/architecture.md) §6.3, §12 — the 240-frame cap, 23,520/23,560-token budget, 0.40 fps at 10 minutes, and the unmeasured video-decode bottleneck.
- [`research/platform/00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) — the loop S1–S9, invariants I1–I7, the method ladder §3.2, the failure taxonomy §3.3, judge validity §5.1, parity definition §5.2, sample sizes §5.3, the vendor survey §7, the ToS risk §8.1 and the vendor-churn list §8.6.

---

## Verification log (2026-09-19)

Adversarial re-check of this document. **32 load-bearing claims** were selected
(every price table row group, every published research result, every
cross-reference into `research/`, and every derived table) and each was taken to
its **primary source, opened, and read** — no citation was trusted on the
strength of its URL. Every `est.` table was **re-derived in `python3`** from the
inputs as fetched, not from the printed outputs. Result: **23 CONFIRMED,
9 CORRECTED, 0 UNVERIFIABLE.**

Note on the document's own research-method caveat at the top: it says doc 08 was
produced *without* WebSearch. **This verification pass had full web access**, so
the "absence from §4 is not evidence of absence" caveat still stands for
*discovery* (no new case-study scan was run), but every source the document does
cite has now been independently re-opened.

### CONFIRMED — primary source opened, figure reproduces exactly

| # | Claim | Source opened | Verdict |
|---|---|---|---|
| 1 | §1.2 OpenAI table: Astra 10/1/50, Sol 4/0.40/20, Terra 2/0.20/12, Luna 0.20/0.02/1.20, gpt-5-mini 0.25/0.025/2.00, gpt-5-nano 0.05/0.005/0.40, gpt-4.1-mini 0.40/0.10/1.60, gpt-4.1-nano 0.10/0.025/0.40 | [developers.openai.com](https://developers.openai.com/api/docs/pricing) | **CONFIRMED**, all 8 rows, all 3 columns |
| 2 | §1.2 Anthropic table: Fable 5.1 10/0.25/12.50/50, Opus 5 5/0.50/6.25/25, Sonnet 5 2/0.20/2.50/10, Haiku 4.5 1/0.10/1.25/5; *"Save 50% with batch processing"* | [claude.com/pricing](https://claude.com/pricing) | **CONFIRMED**, incl. the cache-write-above-input fact §1.7 leans on |
| 3 | §1.2 Gemini rows; the 3.8 Flash promo reverting to $1.50/$7.50 on 2027-01-01; context-cache storage $0.50/1M/h (3.8 Flash) and $4.50 (3.1 Pro) | [ai.google.dev](https://ai.google.dev/gemini-api/docs/pricing) | **CONFIRMED** verbatim (*"$0.75 through December 31, 2026. $1.50 starting January 1, 2027"*) |
| 4 | §2.3/§5.3 Gemini video: 1 FPS, ~100 tok/s low, ~300 tok/s high, 32 tok/s audio, 3 h at low / 1 h at high, agentic *"up to 88% fewer tokens"* on 3.8/3.7/3.6 Flash + 3.5 Flash-Lite | [video-understanding](https://ai.google.dev/gemini-api/docs/video-understanding) | **CONFIRMED** verbatim |
| 5 | §5.3 Twelve Labs: $2.50/h index, Analyze $1.75/h video in, $7.50/1M out | [twelvelabs.io/pricing](https://www.twelvelabs.io/pricing) | **CONFIRMED** verbatim |
| 6 | §1.5 Langfuse: Core $29 / Pro $199 / Enterprise $2,499, 100k units included, overage $8/$7/$6.50/$6 per 100k across 100k–1M / 1M–10M / 10M–50M / 50M+, *"open source and you can self-host it for free"* | [langfuse.com/pricing](https://langfuse.com/pricing) | **CONFIRMED**; §1.5's $621 and $9,501 re-derived in `python3` and reproduce exactly |
| 7 | §1.5 Braintrust: Pro $249, 5 GB + 50k scores included, $3/GB, $1.50/1k scores | [braintrust.dev/pricing](https://www.braintrust.dev/pricing) | **CONFIRMED**; $565 / $8,649 reproduce (at 35.2 GB / 330 GB) |
| 8 | §1.5 W&B: Pro from $60/mo, 1.5 GB Weave ingestion included, $0.10/MB, storage $0.03/GB | [wandb.ai/site/pricing](https://wandb.ai/site/pricing/) | **CONFIRMED**; $3,511 / $33,698 reproduce |
| 9 | §1.3/§6.1 Baseten dedicated: $0.10833/min H100 (= $6.50/h), $0.16633/min B200 (= $9.98/h), $0.06667/min A100 (= $4.00/h) | [baseten.co/pricing](https://www.baseten.co/pricing/) | **CONFIRMED** (per-minute rates are the published unit; the $/h column is ×60) |
| 10 | §1.3/§6.1 Fireworks: $8/h H100 **and H200**, $13/h B200; LoRA SFT $0.50 (≤16B) / $3.00 (16.1–80B); full SFT $1.00 (≤16B) | [fireworks.ai/pricing](https://fireworks.ai/pricing) | **CONFIRMED** (see CORRECTED #8 for the range's upper bound) |
| 11 | §1.3/§1.4(b) Together: H100 $5.49 with promo $3.99 to 09/30/26, B200 $8.99; Llama-3.3-70B SFT $2.03/1M, Qwen3.5-9B $0.34/1M, GLM-5.2 $40.00/1M | [together.ai/pricing](https://www.together.ai/pricing) | **B200 CORRECTED to $8.19** (platform consistency pass, 2026-09-19) — [`07` §15.1](07-competitor-analysis.md) had already re-fetched and corrected this same figure same-day; §1.3 and §1.5 updated to match. Rest CONFIRMED |
| 12 | §1.4(b) Tinker train rates: Qwen3.8-27B $4.103/1M, GLM-5.3 (256K) $14.58/1M, Nemotron-3.5-Lightning $0.44 promo; checkpoints $0.10/GB-month | [tinker-docs](https://tinker-docs.thinkingmachines.ai/tinker/models/) | **CONFIRMED**; list rate $0.88 added inline |
| 13 | §1.4(b) Databricks: $0.65/DBU; Llama-3.1-8B = 100 DBU at 10M words, 4,400 DBU at 500M words ⇒ $65 / $2,860 | [databricks.com](https://www.databricks.com/product/pricing/mosaic-foundation-model-training) | **CONFIRMED**, both rows reproduce |
| 14 | §4.4 Bedrock: provisioned throughput **$21.18/h** (1-mo) and **$13.08/h** (6-mo) per model unit, custom-model storage **$1.95/mo**; §4.4's $15,461/mo, 2.9× a B300 and 11.8× an RTX PRO 6000 all reproduce | [aws.amazon.com/bedrock/pricing](https://aws.amazon.com/bedrock/pricing/) | **CONFIRMED** (see CORRECTED #7 on the $1.49 attribution) |
| 15 | §4.4 Bedrock distillation: invocation-log training path, `requestMetadata` filtering, *"Only you can access the final distilled model…"*, data-synthesis extra charges, **15k prompt-response-pair ceiling** | [docs.aws.amazon.com](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html) | **CONFIRMED**, all four quotations verbatim |
| 16 | §4.1 OpenPipe status: meta description, CoreWeave acquisition, migration post dated **May 18, 2026**, legacy platform stops new training/inference **July 30, 2026**; Axis *"save over 95% on a per-token basis compared to GPT-4-Turbo, the next cheapest model that met their high quality bar"*; *"50X less to run"*; *"often a 10-100x improvement"* | [openpipe.ai](https://openpipe.ai/) (JS bundle, via `curl`) | **CONFIRMED** verbatim (see CORRECTED #4/#5 for two other rows of the same table) |
| 17 | §1.4(b)/§4.1 ART•S: Qwen 2.5 14B **43 %** → **85 %**, Sonnet 4 **72 %**, GPT-4o 66 %, GPT-4.1 51 %; *"GPU costs were around $15, and after adding in the additional $7 spent on judge tokens, the total cost of the final training run was only $22"*; ~5 h, one RunPod H100; ServiceNow Repliqa; judge `gemini-2.5-flash-preview`; 350-word cap | ibid. | **CONFIRMED** verbatim |
| 18 | §4.2 NVIDIA data flywheel: *"Deprecation notice (Apr 2026)"*, *"reduce inference costs by up to 98.6%"*, *"~98% accuracy relative to the 70b model"*, Qwen-2.5-32b-coder cost **and** TTFT *">50%"*, *"a small set of tools"* scoping caveat | [NVIDIA-AI-Blueprints/data-flywheel](https://github.com/NVIDIA-AI-Blueprints/data-flywheel) | **CONFIRMED** |
| 19 | §3.3 FrugalGPT *"up to 98% cost reduction"* / *"improve the accuracy over GPT-4 by 4%"* / prompt adaptation, LLM approximation, LLM cascade / *"two orders of magnitude"*; RouteLLM *"by over 2 times in certain cases"* + transfer learning; S-LoRA *"improve the throughput by up to 4 times"* vs HF PEFT and vLLM | [2305.05176](https://arxiv.org/abs/2305.05176), [2406.18665](https://arxiv.org/abs/2406.18665), [2311.03285](https://arxiv.org/abs/2311.03285) | **CONFIRMED**, all three abstracts |
| 20 | §4.5 UniversalNER *"7-9 absolute F1 points"*, *"43 datasets across 9 diverse domains"*, *"over 30"* vs Alpaca/Vicuna; Distilling Step-by-Step *"770M T5"* > *"540B PaLM"* at *"80%"* of data across *"4 NLP benchmarks"*; Specializing Smaller LMs *"GPT-3.5 (≥175B)"* → *"T5 variants (≤11B)"*, *"by paying the price of decreased generic ability"* | [2308.03279](https://arxiv.org/abs/2308.03279), [2305.02301](https://arxiv.org/abs/2305.02301), [2301.12726](https://arxiv.org/abs/2301.12726) | **CONFIRMED**, all three abstracts verbatim |
| 21 | §2.2 Deloitte 0.1 s → retail conversions **+8.4 %**, AOV **+9.2 %**, travel **+10.1 %** / **+1.9 %**, luxury page views/session **+8.6 %**, lead-gen bounce **8.3 %**, published **2020-03-24**, 4-week study; Nielsen **0.1 s / 1.0 s / 10 s**, 1993-01-01; web.dev Vodafone −31 % LCP → +8 % sales, Lazada 3× → +16.9 %, Tokopedia −55 % → +23 %, GYAO 3.1× → +108 % CTR, Agrofy −76 % abandonment, NDTV −50 % bounce; §4.5 Thinking Machines AIME'24 **60 % → 74.4 %**, ~150 steps, **1,800 vs 17,920** GPU-h, **9–30×**; §3.3 Not Diamond "5 %+"/"20 %+"/"2x", Rootly **39 %**, $4.8M → $3.6M at 1,000 engineers × $300/mo; §4.6/§7.3 Video-MME **900 videos / 254 h / 2,700 QA**; §4.1 OpenAI *"winding down the fine-tuning platform… no longer accessible to new users"* | 6 pages, all opened | **CONFIRMED**, every figure verbatim |
| 22 | **Every `est.` table re-derived in `python3`** and reproducing to the printed precision: §1.2's blended grid (13 models × 4 cache scenarios) and its ratio table; §1.4(a) annotation ($3,000/$1,500/$750/$640/$406 — the Kimi-K3 cell is exactly `50k × (4,000 × $1.2921 + 400 × $7.3914)`); §1.4(b) all 11 training rows; §1.4(c) judge costs ($15/$76 … $378/$1,892) and the $1,682 marginal; §1.4(d) $43,200–$72,000; §1.5 all 8 observability cells and the $7,425 Braintrust-scores figure; §1.7 both sensitivity tables (all 6 output-share ratios); §3.1 all 4 risk rows and the 0.027 pp break-even; §3.3 all 4 fallback points; §5.3 all 42 video cells; §5.4 break-even volumes and the 5.47M-clips capacity; §7.1, §7.2 and §7.3 **in full**, including every payback month | `python3` | **CONFIRMED** — this document's arithmetic is exceptionally clean; the only arithmetic defect found is CORRECTED #3 |
| 23 | §6.1 Tinker **serverless** (beta): `thinkingmachines/Inkling-Small:peft:262144:sampling-nvfp4` 256K at **$0.30** prefill / **$0.06** cached / **$1.20** sample, and `thinkingmachines/Inkling:peft:262144:sampling-nvfp4` 256K at **$1.00 / $0.17 / $4.05**; *"available for Inkling and Inkling-Small only"* | [tinker-docs](https://tinker-docs.thinkingmachines.ai/tinker/models/) (re-fetched 2026-09-19 with a browser UA) | **CONFIRMED** verbatim. The page carries **two distinct price tables**: the **Training** table (whose Inkling-Small cells are train $3.46 / promo $1.73, prefill $1.16 / promo $0.58, cached prefill $0.116) and, well below it, a separate section headed **"Serverless Inference (Beta)"** holding the $0.30/$0.06/$1.20 row. The earlier pass read the training table only and wrongly concluded the serverless figures were unsourced — see the UNVERIFIABLE note below. [`07` §15.3](07-competitor-analysis.md) and [`00` §7](00-goal-and-problem-statement.md) were right |
| 24 | Every cross-reference into `research/`, opened as a file: all 8 named cells in §1.3's marginal-cost table and their operating points against [`cost-matrix.md`](../matrix/cost-matrix.md) §2/§4/§5; the six `$/GPU-hour` rows against [`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md); §1.6's "6–28 %" and "11.5 % / 23.3 %"; §1.7's res1y B200 $0.0538; §2.1's 14.2–45.3 ms TPOT band and the inverted 44,686 / 49,020 tok/s prefill rates; §5.3's 23,560-token / 240-frame / 0.40 fps / 0.067 fps budget against [`marlin2b/architecture.md` §6.3](../models/marlin2b/architecture.md); §5.5's self-host-vs-buy verdict | `research/` files | **CONFIRMED** — every named cell matches its source document exactly |

### CORRECTED — edited in place above

1. **§2.1 — Cerebras/Groq priced in `×` instead of `$`.** *"at 1.1–1.6× the blended price of a GPU endpoint"* read [`12` §5.2](../scaling/12-inference-providers.md)'s **blended $/1M column** ($1.1150 Cerebras, $1.6000 Groq) as multiples. The actual ratio against the cheapest GPU endpoint in the same table (Darkbloom $0.5250) is **2.1× and 3.0×** — roughly double what the document claimed, which **strengthens** its "buy custom silicon if the problem is purely latency" caveat rather than weakening it. §5.2's blend caveat (neither custom-silicon row publishes a cached rate) is now carried with it.
2. **§1.3 — "3.1× cheaper than Gemini 3.8 Flash at h=50 %" → 2.4×.** $1.2469 ÷ $0.5250 = 2.375×. No pair of figures in §1.2 or §1.3 yields 3.1.
3. **§1.6 — the qualification filter's justification was arithmetically false.** *"cannot clear a $66,600 one-off inside 12 months even at a 100 % cost reduction"* is wrong by construction: at 100 % reduction the saving *is* the spend, and $66,600 ÷ $15,000 = **4.4 months**. The arithmetic threshold is a net saving of **$5,550/month**, i.e. ≈ **$10,100/month** of incumbent spend once §7.1's ≈$4,563/month delivered cost is subtracted. $15,000 is retained as a judgement call with the headroom now stated. **This is the most consequential defect found** — the filter is the document's gate on every engagement.
4. **§4.1 — the "19.25 % → 9 % collapse" does not exist.** The MoA post's 19.25 % is MoA **vs GPT-4-Turbo** under an LLM judge; the 9 % is MoA *"stronger than **the base models they came from** by 9%"* after human adjustment. Different baselines, so the pair cannot be read as a judge-validity haircut — and §4.1 called that reading *"the best available evidence for doc 00 §5.1's warning about judge validity"*. The **direction** is published (*"the adjustments decreased the degree to which MoA outperforms GPT-4-Turbo"*) and survives; the **magnitude** does not and must not be quoted.
5. **§4.1 — ART•S's judge ceiling inverted.** The document said the judge *"could itself only answer ~85 % given the full document"*, making ART•S's 85 % look like parity with the judge's own ceiling. The post says the full-document score is *"the perfect score of **100%**"*. ART•S's 85 % is 85 % of an attainable 100 % — a materially weaker result than printed, on the row §5 ranks summarisation with.
6. **§1.2 — two mis-attributed quotations about batch discounts, and the composition question is now half-resolved.** The sentence *"Batch pricing reduces costs to 50 % of Standard rates across all models"* is attributed to OpenAI's pricing page and **is not on it** (the page ships a Batch table and no verbal statement). Google's columns are **not** "exactly half" everywhere — Gemini 3.1 Pro's batch *cached* input is $0.20, identical to standard. Against that: Google **does** publish a *Batch cached input* cell (3.8 Flash $0.0375 = half of $0.075), which **settles Open Question 1 for Google** and therefore settles §7.2's decisive Gemini rows. OpenAI and Anthropic remain open; Open Question 1 is rescoped to those two.
7. **§4.4/§6.1 — the Bedrock $1.49/1M is the Llama 2 **13B** rate.** The same page prints **$7.99/1M** for Llama 2 70B. §6.1's "12–290× spread" row cited the single figure as if it were the family's rate.
8. **§6.1 — two rate-card upper bounds are not on the pages.** "Fireworks $0.50–**$24**/1M" and "Together $0.34–**$100**/1M" do not appear as re-fetched; Fireworks tops out at **$12.00/1M** (full-param SFT/DPO, 80B–300B) and Together's dearest listed SFT is **GLM-5.2 at $40.00/1M**. The cross-vendor training spread is therefore **118×**, not 290×. Downgraded to ⚠️ with the fetched maxima printed.
9. **Three smaller ones, each edited inline:** §1.5's Baseten GLM-5.3-Flash blend **0.1934 → 0.1925** (the only cell in the document that fails to reproduce; the Baseten rates themselves are confirmed); §0 item 1's headline **"3–30×"**, a range that appears nowhere in §1.2 (whose honest column runs 0.9×–115.7×, and whose cheapest-tier comparison is 0.9–11.6×); §3.2's **"roughly 1 %"** premium → **1.5–11.5 %**, since $473 + $1,333–$13,333 against $120,000 spans an order of magnitude. Plus §4.1's sunset list, which omitted **DPO** — the sixth bullet in the migration post.

### UNVERIFIABLE

**None.** The one entry that stood here — *"§6.1 — Tinker serverless
'Inkling-Small $0.30/$1.20' … no $0.30/$1.20 pair appears anywhere on it"* — was
**itself wrong and has been withdrawn**. See CONFIRMED #23: the page carries two
separate tables, the earlier pass read the training one, and the
"Serverless Inference (Beta)" table confirms the figure exactly. This is the
failure mode a verification log exists to prevent — a correctly sourced
competitor price recorded as unsupported — so it is recorded here rather than
silently deleted.

### Re-pointed, not corrected

[`12a-serverless-gpu-platforms.md`](../scaling/12-inference-providers.md) and
[`12b-model-api-providers.md`](../scaling/12-inference-providers.md) — the two
files this document cites **9 times** — have been **merged into
[`12-inference-providers.md`](../scaling/12-inference-providers.md)** and no
longer exist in the working tree. Every link is re-pointed. §5.2/§5.3/§5.5 keep
their numbers; 12a's §3.4 normalisation is now §2.3/§5.6, where the
scale-to-zero premium has itself been **corrected from "≈ +20 %" to a 20–37 %
range**, and §5.2 now carries a sourcing note that its "19 endpoints" heading
counts three non-OpenRouter rows. Both are noted at the citation sites.

**2026-09-19** — §6.1 Tinker serverless re-adjudicated: `curl` with a browser UA on <https://tinker-docs.thinkingmachines.ai/tinker/models/> shows the page carries a Training price table *and*, further down, a separate **"Serverless Inference (Beta)"** table (grep `sampling-nvfp4`). The earlier pass read only the first and filed the figure as UNVERIFIABLE; the serverless table confirms **Inkling-Small $0.30 / $0.06 cached / $1.20** and **Inkling $1.00 / $0.17 / $4.05**, both 256K. The ⚠️ and its parenthetical are removed from §6.1, the UNVERIFIABLE entry is withdrawn, and the claim is now CONFIRMED #23. `07` §15.3 and `00` §7 were correct throughout and are unchanged.
