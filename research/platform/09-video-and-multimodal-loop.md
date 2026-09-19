# Video and multimodal specifics of the closed loop

Research date **2026-09-19**. This is doc 09 of `research/platform/`. It takes the
loop defined in
[`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) — stages
S1 traffic → S2 traces → S3 annotation → S4 datasets+evals → S5 training → S6
checkpoint → S7 offline gate → S8 hardware optimisation → S9 online A/B — and
states **what changes when the payload is video instead of text.**

Doc 00 §3.4 names video as *"the single biggest evidence gap in this programme"*
and hands doc 04 the video-eval question. §1 below closes part of that gap with a
published result doc 00 did not have; the rest of this document is the mechanism,
the arithmetic and the failure modes.

**Conventions.** Legend, cost formulas and the `low`/`high`/`res1y` GPU price
tiers are [`../METHODOLOGY.md`](../METHODOLOGY.md); this document does not
re-derive them. Self-hosting figures are named rows from
[`../models/marlin2b/README.md`](../models/marlin2b/README.md) and
[`../models/qwen3827b/README.md`](../models/qwen3827b/README.md). Serving,
autoscaling, cold-start and utilisation economics are
[`../scaling/`](../scaling/). Terminology (S1–S9, I1–I7, the §3.2 ladder) is
doc 00's.

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source fetched 2026-09-19. |
| **⚠️ TO BE VERIFIED** | No primary source found, or the claim is an inference; the reasoning is stated inline. |
| `est.` | Arithmetic from METHODOLOGY formulas or from sourced inputs; shown, not measured. |
| `meas.` | A published measurement, cited. |

> **Research-method caveat, stated up front.** This session's WebSearch budget was
> exhausted (200/200) before this agent started. Every source below was reached by
> **WebFetch against a known URL** or by `curl` against a raw file, and by
> following links out of fetched pages. The consequence is the same as doc 00's:
> **the competitor and open-model surveys here are a floor on the landscape, not
> an exhaustive scan.** Nothing below is recalled from memory; if it has no
> `[src]`, it is marked `est.` or ⚠️.

---

## 0. The five findings that reorder the video plan

Put before everything because they change what doc 04 and doc 07 have to build.

1. **"GPT-5.6 video" and "Fable-5.1 video" do not exist.** The OpenAI API accepts
   only *"PNG (`.png`), JPEG (`.jpeg` or `.jpg`), WEBP (`.webp`), and non-animated
   GIF (`.gif`)"*
   [[src](https://developers.openai.com/api/docs/guides/images-vision)]. Claude
   accepts *"JPEG, PNG, GIF, and WebP"* and *"Animations are unsupported, and only
   the first frame is used"*
   [[src](https://platform.claude.com/docs/en/build-with-claude/vision)]. **Gemini
   is the only frontier first-party video-understanding API in the set the platform
   owner named.** Every OpenAI/Anthropic "video" customer is doing client-side frame
   extraction into an image API — and §1.4 shows that costs **11–123×** what Gemini
   charges for the same video-minute. Those customers are the best distillation
   targets in the market.
2. **The video evidence gap doc 00 §3.4 flagged is now partly closed, and it closed
   in the platform's favour.** TimeLens (Dec 2025) reports **TimeLens-8B at 55.2 /
   53.2 / 65.5 mIoU** on Charades / ActivityNet / QVHighlights against **GPT-5 at
   40.5 / 42.9 / 56.8** and **Gemini-2.5-Flash at 48.6 / 52.5 / 64.3**
   [[src](https://arxiv.org/html/2512.14698v1)]. An 8 B open model, trained with
   RLVR on 100 K re-annotated examples, **beats two frontier models on the exact
   video task the repo's student is built for.** This is the strongest published
   support for the video half of the commercial thesis that exists.
3. **Temporal grounding needs no judge.** Its metric — R@1 at IoU 0.3/0.5/0.7 and
   mIoU [[src](https://arxiv.org/html/2512.14698v1)] — is a *verifiable reward*, so
   it drives an RLVR loop directly and costs nothing per evaluation. Doc 00 §5.1's
   judge-validity problem and §5.5's "no cheap judge" objection **do not apply to
   this task class.** §6 gives the ladder for pushing other video tasks down onto
   verifiable or text-only metrics.
4. **The 240-frame cap is not only a serving fact, it is the eval design.**
   Marlin-2B's prefill is **~23,560 tokens for any clip length**, giving 2.0 fps at
   2 minutes and **0.067 fps at 60 minutes**
   ([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.3).
   Gemini's default is a flat 1 fps out to 3 hours
   [[src](https://ai.google.dev/gemini-api/docs/video-understanding)]. So the
   student out-samples the teacher below ~4 minutes and under-samples it above.
   **Segment at ≤2 minutes and the comparison is honest and the student wins; do
   not segment and the parity claim is a sampling artefact.** §6.4.
5. **The counter-move is already shipped and it is severe.** Gemini's agentic video
   mode is *"Up to 88% more token-efficient and ~7% higher quality on long-form
   content"* [[src](https://ai.google.dev/gemini-api/docs/video-understanding)]. At
   88 % fewer content tokens, a Flash-class incumbent on long video gets cheaper
   than the *GPU floor* of a small self-hosted student at the volumes in §8.
   Doc 00 §4.4's warning applies harder to video than to text.

---

## 1. Video-understanding use cases, incumbents and prices

### 1.1 The task taxonomy customers actually run

Six shapes, ordered by how well they distil. The ordering is `est.` — it follows
from the eval ladder in §6, not from a survey.

| # | Task | What the output is | Why it distils well or badly | Eval class (§6) |
|---|---|---|---|---|
| 1 | **Temporal grounding / "find"** | `(start, end)` span for a natural-language query | Best case. Output is tiny, the metric is verifiable (IoU), and a published 8 B model already beats GPT-5 and Gemini-2.5-Flash [[src](https://arxiv.org/html/2512.14698v1)] | **Verifiable** |
| 2 | **Structured extraction** | JSON: objects, counts, states, timestamps against a fixed schema | Good. Schema-valid output is programmatically checkable; the frontier model's value was mostly its prior, which SFT transfers | **Verifiable** (schema + field match) |
| 3 | **Moderation / classification** | Label + confidence, optionally with a span | Good. Exact-match metric, class-balanced eval is buildable, and the incumbents here are expensive per-minute APIs (§1.5) | **Verifiable** |
| 4 | **Dense captioning** | Scene paragraph + timestamped event list | Medium. No exact metric, but AutoDQ-style event-extraction + NLI gives a *text-only* F1 against a frozen human reference [[src](https://arxiv.org/html/2407.00634v2)] — the judge never re-watches the video | **Text-only judge** |
| 5 | **Video QA** | Free-form answer to an arbitrary question | Harder. Convertible to MCQ (the Video-MME construction, [[src](https://arxiv.org/html/2405.21075v2)]) at the cost of a human-authored option set | **MCQ if converted, else video judge** |
| 6 | **Long-form summarisation** | Multi-paragraph narrative over 10–60 min | Worst. Needs the full clip in context, the ground truth is genuinely ambiguous, and it is exactly where the 240-frame cap bites | **Video judge** |

**Decision rule.** The platform should qualify a video customer by which row they
are in. **Rows 1–3 are sellable today.** Row 4 is sellable with a one-off human
reference set (§4.5 prices it). **Rows 5–6 should be declined for the MVP** — not
because the model cannot do them, but because the *eval* for them costs more than
the inference saving at any realistic volume (§8 shows this arithmetically).

### 1.2 Gemini — the only native video API in the named set

Everything in this sub-section is from
[[src](https://ai.google.dev/gemini-api/docs/video-understanding)] and
[[src](https://ai.google.dev/gemini-api/docs/pricing)], fetched 2026-09-19.

| Property | Value |
|---|---|
| Default sampling | **1 fps**, static mode; configurable via `fps` in the `processing` object |
| Tokens per frame | **66** (low media resolution) / **258** (high) |
| Audio | **32 tokens/second** |
| Tokens per video-second, all-in | **~100** (low) / **~300** (high) |
| Max length, 1M-context models | **3 hours** (low res) / **1 hour** (high res) |
| Videos per request | **10** (Gemini 2.5+); 1 before that |
| Formats | MP4, MPEG, MOV, AVI, FLV, MPG, WebM, WMV, 3GPP |
| YouTube URLs | Public videos only; free tier 8 h/day, paid unlimited |
| Clipping | `start_offset` / `end_offset` — **static mode only** |
| Agentic mode | Gemini 3.8 / 3.7 / 3.6 Flash and 3.5 Flash-Lite; *"dynamically navigates the video timeline, loading only the content it needs"*; **"Up to 88% more token-efficient and ~7% higher quality on long-form content"**; bills `total_thought_tokens` + `total_tool_use_tokens`; may raise TTFT on clips < 5 min |
| Batch | **50 % of interactive price**, 24 h turnaround, *"supported modalities for Batch API are the same as what's supported on the interactive API"* [[src](https://ai.google.dev/gemini-api/docs/batch-api)] |

Sanity check on the two token rates: `66 + 32 = 98 ≈ 100`/s and
`258 + 32 = 290 ≈ 300`/s. The docs' per-second figures are the per-frame figure at
1 fps plus audio. `est.`, but it reconciles exactly, and it reconciles again
against the context limits: `3 h × 3,600 × 100 = 1.08 M` and
`1 h × 3,600 × 300 = 1.08 M` — both ≈ the 1 M window.

**Price per video-minute, input only, list price, static mode.** `est.` from the
two sourced rates (6,000 tok/min low, 17,400 tok/min high — the latter computed as
`258×60 + 32×60`, which is 3 % under the docs' rounded ~300/s):

| Gemini model | $/1M input | **low-res $/video-min** | **high-res $/video-min** |
|---|---:|---:|---:|
| 2.5 Flash-Lite | $0.10 | $0.0006 | $0.0017 |
| 3.1 Flash-Lite | $0.25 | $0.0015 | $0.0044 |
| 3.5 Flash-Lite / 2.5 Flash | $0.30 | $0.0018 | $0.0052 |
| **3.8 / 3.7 / 3.6 Flash** (promo to 2026-12-31) | **$0.75** | **$0.0045** | **$0.0131** |
| 3.8 Flash (from 2027-01-01) | $1.50 | $0.0090 | $0.0261 |
| 3.5 Flash | $1.50 | $0.0090 | $0.0261 |
| 2.5 Pro (≤200 K) | $1.25 | $0.0075 | $0.0218 |
| **3.1 Pro Preview (≤200 K)** | **$2.00** | **$0.0120** | **$0.0348** |
| 3.1 Pro Preview (>200 K) | $4.00 | $0.0240 | $0.0696 |

Two prices that are *not* video understanding but get confused with it, both from
the same page: **Veo 3.1** generation at $0.05–$0.60/second and **Gemini Embedding
2 video** at $12.00/1M or **$0.00079 per frame**. The embedding row is the one to
watch — at 1 fps it is $0.047/video-minute, i.e. **more than Gemini 3.1 Pro
Preview charges to *understand* the same minute at high resolution.**

> **⚠️ TO BE VERIFIED — the promotional price.** Three Flash generations are at
> $0.75/$3.75 *"through Dec 31, 2026"*, reverting to $1.50/$7.50
> [[src](https://ai.google.dev/gemini-api/docs/pricing)]. Every break-even in §7
> and §8 that uses Flash **doubles on 2027-01-01** unless the promotion is
> extended. A customer's cost model built this quarter is wrong next quarter, in
> the platform's favour. Say so in the sales conversation rather than being caught
> by it.

### 1.3 Vertex AI — the incumbent's counter-product for video

Google's own tuning path is the most important competitive fact in this document
after §0.1:

- **In the Gemini API and AI Studio, fine-tuning is gone.** Verbatim: *"With the
  deprecation of Gemini 1.5 Flash-001 in May 2025, we no longer have a model
  available which supports fine-tuning in the Gemini API or AI Studio"*
  [[src](https://ai.google.dev/gemini-api/docs/model-tuning)].
- **On Vertex / Gemini Enterprise Agent Platform it is not gone, and it includes
  video.** The supervised-fine-tuning modality list is *"Text tuning, Document
  tuning, Image tuning, Audio tuning, Video tuning, Function calling"*
  [[src](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/tune-models)].

⚠️ **TO BE VERIFIED**: the per-modality limits (videos per example, max duration,
MIME types) and the tuning price. The dedicated page
(`/gemini-enterprise-agent-platform/models/tuning/video`) returned only navigation
structure on fetch, twice. **This is a blocking gap for competitive positioning and
doc 09's owner should close it first.**

**What it means.** For text, doc 00 §7.1 found the incumbents *retreating* from
first-party distillation (OpenAI winding down fine-tuning, Anthropic prohibiting
it). For **video, Google is doing the opposite**: it is the only vendor with a
native video API *and* it sells video SFT of that model on its enterprise
platform. The platform's video wedge is therefore not "nobody offers this" — it is:

| Our video wedge vs Vertex video tuning | Why it holds |
|---|---|
| **The student is ours and runs anywhere** | Vertex tuning produces a tuned *Gemini*, served by Google at Gemini prices. A tuned Gemini is cheaper than an untuned one only if Google prices it so; a Marlin-2B checkpoint is **$0.000432 per 2-minute caption** on a B200 ([`../models/marlin2b/README.md`](../models/marlin2b/README.md)) and is a file we own |
| **We close the loop; they close the seam** | Vertex tuning is stage S5. It has no S1/S2 production trace capture from the customer's own endpoint, no S7 gate, no S9 A/B. Same gap doc 00 §7.1 found in Baseten and Tinker |
| **They cannot be the teacher and the student** | Distilling Gemini *into* Gemini does not reduce the customer's dependence on Google, which is frequently the actual buying motive |
| **Where it does not hold** | If the customer's only goal is cost and Vertex prices a tuned Flash-Lite below our GPU floor, we lose on arithmetic. §8 shows the GPU floor is the binding term at small volumes |

### 1.4 OpenAI and Anthropic — frame extraction, and what it costs

Neither API accepts a video file. A customer "using GPT-5.6 for video" is
extracting frames client-side and sending them as images. The token accounting is
documented for both, so the cost is computable.

**Anthropic** [[src](https://platform.claude.com/docs/en/build-with-claude/vision)]:
images cost `⌈width/28⌉ × ⌈height/28⌉` visual tokens. Claude 4.7-and-later models
are the high-resolution tier: max long edge 2576 px, max 4784 visual tokens; all
others 1568/1568. Limits: **100 images per request** for 200 K-context models, 600
otherwise, 8000×8000 px max, 10 MB per image, 32 MB per request.

**OpenAI** [[src](https://developers.openai.com/api/docs/guides/images-vision)]:
newer models cover the image in **32×32 px patches**, cost =
`⌈patch_count × model_multiplier⌉`, the multiplier being **1.2** for `gpt-6-astra`,
every `gpt-5.6-*`, `gpt-5.5`, `gpt-5.4*` and `gpt-5.2` (1.62 for `gpt-4.1-mini`).
A 448×448 frame is `⌈448/32⌉² = 196` patches → `⌈196 × 1.2⌉ =` **236** tokens
(earlier drafts of this table printed 235, truncating instead of taking the
documented ceiling — corrected 2026-09-19).

`est.` cost per video-minute at **1 fps** (60 frames), list price, input only:

| Stack | Frame size | Tokens/frame | Tokens/video-min | $/1M in | **$/video-min** | vs Gemini 3.8 Flash high-res ($0.0131) |
|---|---|---:|---:|---:|---:|---:|
| **Claude Fable 5.1**, native 1080p | 1920×1080 | 2,691 | 161,460 | $10.00 | **$1.615** | **123×** |
| **Claude Opus 5**, native 1080p | 1920×1080 | 2,691 | 161,460 | $5.00 | $0.807 | 62× |
| **Claude Fable 5.1**, downsized | 448×448 | 256 | 15,360 | $10.00 | $0.154 | 11.7× |
| **Claude Haiku 4.5**, downsized | 448×448 | 256 | 15,360 | $1.00 | $0.0154 | 1.2× |
| **GPT-6 Astra**, downsized | 448×448 | 236 | 14,160 | $10.00 | $0.1416 | 10.8× |
| **GPT-5.6 Sol**, downsized | 448×448 | 236 | 14,160 | $4.00 | $0.0566 | 4.3× |
| **GPT-5.6 Luna**, downsized | 448×448 | 236 | 14,160 | $0.20 | $0.0028 | 0.22× |

Price sources: [OpenAI](https://developers.openai.com/api/docs/pricing),
[Anthropic](https://claude.com/pricing) (both re-confirmed 2026-09-19: GPT-6 Astra
$10/$50, GPT-5.6 Sol $4/$20, GPT-5.6 Luna $0.20/$1.20; Fable 5.1 $10/$50, Opus 5
$5/$25, Haiku 4.5 $1/$5 — the table matches doc 00 §2.1). The 2,691-token figure
for a 1080p frame is not only the formula's output, it is printed verbatim in
Anthropic's own resolution table for the high-resolution tier
[[src](https://platform.claude.com/docs/en/build-with-claude/vision)].

⚠️ **One limit the native-1080p row brushes against.** Above **20 image blocks in
one request** Anthropic applies a *stricter per-image dimension limit* to every
image in that request, and advises resizing so neither dimension exceeds **2000 px**
[[ibid.]]. A 60-frame video-minute is a 60-image request; 1920×1080 still clears
2000 px on both axes, so the row stands — but a customer sending 4K frames is
rejected, not downscaled-and-billed.

**Three consequences the sales motion should use directly.**

1. **The customer running a Fable-class model on native-resolution frames is paying
   ~123× the cheapest frontier alternative and does not know it.** The first thing
   the platform should do for them is not distillation, it is *downscale the frames
   and tier down* — doc 00 §3.2 rung 1, and it buys credibility before any GPU is
   provisioned.
2. **The 100-image cap is a hard 100-second ceiling at 1 fps** on a 200 K-context
   Claude model. Any Claude "video" pipeline past ~1.7 minutes is already chunking,
   which means the customer has *already* built the segmentation the student needs
   (§6.4) and has the chunk boundaries in their trace.
3. **Frame extraction means the trace already contains the frames.** For an
   OpenAI/Anthropic customer, S2 capture is trivially solved (§3.2): the request
   body *is* the frame set. For a Gemini customer it is not — the request contains
   a file reference or a YouTube URL.

### 1.5 Specialist video APIs — the other incumbent class

Customers doing rows 3 and 4 of §1.1 are frequently not on an LLM API at all.

| Vendor | Product | Price | **$/video-min** | Source |
|---|---|---|---:|---|
| **AWS Rekognition** | Video label detection | $0.10/min | **$0.100** | [[src](https://aws.amazon.com/rekognition/pricing/)] |
| **AWS Rekognition** | Video content moderation | $0.10/min | **$0.100** | [[ibid.](https://aws.amazon.com/rekognition/pricing/)] |
| **AWS Rekognition** | Shot detection | $0.05/min | $0.050 | [[ibid.](https://aws.amazon.com/rekognition/pricing/)] |
| **TwelveLabs** | Analyze API, input video | $1.75/hour | **$0.0292** | [[src](https://www.twelvelabs.io/pricing)] (page dated 2026-09-17) |
| **TwelveLabs** | Analyze API, output text | $7.50/1M tok | — | [[ibid.](https://www.twelvelabs.io/pricing)] |
| **TwelveLabs** | Video indexing | $2.50/hour | $0.0417 | [[ibid.](https://www.twelvelabs.io/pricing)] |
| **TwelveLabs** | Search API | $4/1,000 queries | — | [[ibid.](https://www.twelvelabs.io/pricing)] |
| **TwelveLabs** | Embed API, video input | $0.260/1M tok | — | [[ibid.](https://www.twelvelabs.io/pricing)] |
| **Gemini Embedding 2** | Video | $12.00/1M, **$0.00079/frame** | $0.047 @1 fps | [[src](https://ai.google.dev/gemini-api/docs/pricing)] |

**Rekognition at $0.100/video-minute is the single best distillation target in this
table**, and it is a *classification* task — §1.1 row 3, the easiest class to
evaluate. A moderation customer at 500 K video-minutes/month is paying $50,000/month
for a task a fine-tuned 2 B model does on one rented GPU (§7).

---

## 2. Students: what can absorb which task

### 2.1 The repo's two students

| | **Marlin-2B** | **Qwen3.8-27B** |
|---|---|---|
| Params | 2.2 B (4.426 GB BF16) | 27.78 B (55.56 GB BF16 / 21.92 NVFP4) |
| Base | Qwen3.5-2B, vision tower intact [[src](https://huggingface.co/NemoStation/Marlin-2B)] | Qwen3.5 architecture, native VLM [[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md)] |
| Video token budget | **23,520 LM tokens, fixed** (240-frame cap) | **12,288 as shipped**, 229,376 at the card's recommended `longest_edge` |
| Effective fps | 2.0 @ ≤2 min, 0.40 @ 10 min, **0.067 @ 60 min** | `fps=2` default; the budget, not the clock, binds |
| Context | 262,144 (`rope_type: default`; **not 1 M**) | 262,144 native, extensible to 1,000,000 |
| Modes | `caption` / `find`, one canonical prompt each | General VLM + text + agentic |
| Min GPUs | **1, everywhere** | **1, everywhere** |
| Best $/unit | **$0.432 / 1,000 two-min captions** (B200, `low`) | **$0.153/1M out**, $0.046/1M in (B300 NVFP4, `low`) |
| Confidence | `estimate` everywhere (`speculative` on MI355X) — **no published throughput measurement on any hardware** | `estimate` on all eight pairs — **no throughput/TPOT/TTFT measurement on any datacentre GPU** |

Sources: [`../models/marlin2b/README.md`](../models/marlin2b/README.md),
[`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §5.3, §6.3,
[`../models/qwen3827b/README.md`](../models/qwen3827b/README.md),
[`../models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md) §6.5.

**The two token budgets are structurally different and this is the main modelling
trap.** Marlin's cap is **per frame × frames** (`VIDEO_MAX_PIXELS=200704`,
`FPS=2.0`, `FPS_MAX_FRAMES=240` → 98 LM tokens/frame × 240
[[src](../models/marlin2b/architecture.md)] §6.3). Qwen3.8's is **per whole video**
(`tokens_per_video = total_pixels / 2048`, `size.longest_edge` 25,165,824 as
shipped → 12,288 tokens for the entire clip, whatever its length
[[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/video_preprocessor_config.json)],
[`../models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md) §6.5).

Read that again: **as shipped, Qwen3.8-27B spends 12,288 tokens on a whole video —
half of what Marlin-2B spends — and Marlin is 12× smaller.** The preprocessor
trades frames against resolution inside that budget. At 240 frames the shipped
config leaves ~51.2 LM tokens/frame `est.` — i.e. `25,165,824 px ÷ 240 = 104,858
px/frame ≈` **324×324 px**, against Marlin's 98 tokens at 448×448 (`48,168,960 px ÷
240 = 200,704`). *(Corrected 2026-09-19: earlier drafts printed 229×229, applying the
image divisor 1,024 instead of the video divisor 2,048 that
[`../models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md) §6.5
derives and that Marlin's own 23,520 tokens reproduce exactly.)* **The 27 B model is, by default, looking at a blurrier video than the 2 B
one.** Raising `longest_edge` to the card's recommended 469,762,048 gives 229,376
tokens — and then, in the repo's own words, *"A 224 K-token video at the recommended
setting is a far larger encoder job than anything the LM does"*
([`../models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md) §6.5).

**Decision rule.** For rows 1–4 of §1.1 at ≤2-minute segments, **Marlin-2B is the
default student and Qwen3.8-27B is not a 12× better one — it is a differently
configured one.** Reach for Qwen3.8-27B only when the task needs (a) real text
reasoning alongside the video, (b) tool calls, or (c) >2-minute context that
segmentation genuinely cannot express. Otherwise you are paying 12× the parameters
for a *lower* frame fidelity at the shipped config.

### 2.2 The student ladder that actually exists

ms-swift lists the whole Qwen3.5 family as `vision, video` capable, one
`model_type`, one template, one dependency set
([`transformers>=5.2.0, qwen_vl_utils>=0.0.14, decord`])
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Supported-models-and-datasets.md)]:

| Model | Params | Video? | Note |
|---|---|---|---|
| Qwen3.5-0.8B (+ `-Base`) | 0.8 B | ✅ | The floor. Untested for video by anyone I could find ⚠️ |
| **Qwen3.5-2B (+ `-Base`)** | 2 B | ✅ | **Marlin-2B's base** |
| Qwen3.5-4B (+ `-Base`) | 4 B | ✅ | The obvious next rung if 2 B misses the gate |
| Qwen3.5-9B (+ `-Base`) | 9 B | ✅ | |
| Qwen3.5-27B / Qwen3.6-27B (+ `-FP8`) | 27 B | ✅ | Dense rungs above 9 B; same `qwen3_5` `model_type` and template |
| Qwen3.5-35B-A3B / -122B-A10B / -397B-A17B, Qwen3.6-35B-A3B (+ `-FP8`) | MoE | ✅ | `qwen3_5_moe`; sparse rungs, same template |
| **Qwen3.8-27B** (+ `-FP8`) | 27.8 B | ✅ | The repo's large student |
| Qwen3.8-2.4T-A95B (+ `-FP8`) | 2.4 T MoE | ✅ | Not a student; a self-hosted *teacher* candidate (§4.2) |
| Qwen3.8-Flash-Next (+ `-FP8`) | ⚠️ | ✅ | `transformers>=5.16.0` |

**This is the single most valuable operational fact in §2.** A capacity ladder
where every rung shares an architecture, a chat template, a preprocessor and a
training recipe means **the auto-research loop (doc 09's sibling component) can
search over student size as a hyperparameter with no per-rung engineering.** That
is not true of a ladder that mixes LLaVA, InternVL and Qwen.

### 2.3 Other open VLMs, and what is actually known about them

Sizes below are from LLaMA-Factory's supported-model table
[[src](https://raw.githubusercontent.com/hiyouga/LLaMA-Factory/main/README.md)],
which is a trainability statement, not a benchmark statement.

| Family | Sizes | Video-capable | What I could source about video quality |
|---|---|---|---|
| **Qwen3-VL** | 2B/4B/8B/30B/32B/235B | ✅ | *"native 256K context, expandable to 1M"*, *"hours-long video with full recall"*, and *"Text–Timestamp Alignment… timestamp-grounded event localization"* [[src](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)]. **No Video-MME / LVBench / MLVU / TempCompass numbers on the card** ⚠️. It is the base for **TimeLens-8B** (§2.4), which is where its video numbers actually come from |
| **Qwen2/2.5-VL, QVQ** | 2B/3B/7B/32B/72B | ✅ | **Qwen2.5-VL-7B: 39.3 / 31.4 / 31.6 mIoU** on Charades/ActivityNet/QVHighlights-TimeLens [[src](https://arxiv.org/html/2512.14698v1)]. Qwen2-VL-72B: **77.8 % Video-MME with subtitles** [[src](https://video-mme.github.io/home_page.html)] |
| **InternVL 2.5–3.5** | 1B/2B/4B/8B/14B/30B/38B/78B/241B | ✅ | Widest size ladder in open VLMs. ⚠️ no video number sourced in this session |
| **LLaVA-NeXT-Video** | 7B/34B | ✅ | LLaVA-Video-72B: **76.9 % Video-MME with subtitles** [[src](https://video-mme.github.io/home_page.html)] |
| **GLM-4.5(6)V** | 9B/106B/355B | ✅ | ⚠️ no video number sourced |
| **MiniCPM-V 4.5 / 4.6** | 3B/8B/9B | ✅ | ⚠️ no video number sourced |
| **Kimi-VL** | 16B | ✅ | ⚠️ |
| **LFM 2.5-VL** | 1.2B/1.6B | ✅ | Smallest video-capable family found. ⚠️ |
| **Llama 3.2 Vision** | 11B/90B | image only | Not a video model |
| **Tarsier / Tarsier2** | 7B/34B | ✅ | **DREAM-1K F1: Tarsier-7B 34.6, Tarsier-34B 36.3, Tarsier2-7B 40.1**, vs **GPT-4o 39.2** and **Gemini 1.5 Pro 36.2** [[src](https://arxiv.org/html/2407.00634v2)] |
| **TimeLens-7B / -8B** | 7B / 8B | ✅ | §2.4 — the headline |

ms-swift counts **245 model entries tagged `vision, video`**
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Supported-models-and-datasets.md)].
Trainability is not the constraint. **Knowing which one is good at the customer's
task is the constraint**, and §2.5 says why the public leaderboards do not answer it.

### 2.4 TimeLens — the result that closes doc 00's evidence gap

TimeLens (arXiv 2512.14698) re-annotated three temporal-grounding benchmarks under
*"strict quality criteria"* and trained two models with **RLVR — reinforcement
learning with verifiable rewards** [[src](https://arxiv.org/html/2512.14698v1)].

**TimeLens-Bench**, 4,279 videos / 9,404 annotations:

| Split | Source | Videos | Annotations |
|---|---|---:|---:|
| Charades-TimeLens | Charades-STA | 1,313 | 3,363 |
| ActivityNet-TimeLens | ActivityNet Captions | 1,455 | 4,500 |
| QVHighlights-TimeLens | QVHighlights | 1,511 | 1,541 |

Metrics: **R@1 at IoU 0.3 / 0.5 / 0.7, and mIoU.**

**mIoU results** [[src](https://arxiv.org/html/2512.14698v1)]:

| Model | Params | Charades | ActivityNet | QVHighlights |
|---|---:|---:|---:|---:|
| Qwen2.5-VL-7B (untuned base) | 7 B | 39.3 | 31.4 | 31.6 |
| **TimeLens-7B** (RLVR on Qwen2.5-VL-7B) | 7 B | **48.8** | **46.2** | **56.0** |
| GPT-5 | frontier | 40.5 | 42.9 | 56.8 |
| Gemini-2.5-Flash | frontier | 48.6 | 52.5 | 64.3 |
| **TimeLens-8B** (RLVR on Qwen3-VL-8B) | 8 B | **55.2** | **53.2** | **65.5** |

Training data: **TimeLens-100K**, built by an *"automated re-annotation
pipeline"* over existing training corpora.

**Why this is the most important citation in this document.**

1. It is a **task-specific small VLM beating two frontier models on video**, which
   doc 00 §3.4 could not find. The thesis is no longer unsupported for video.
2. The gain from the *same base* is enormous: Qwen2.5-VL-7B 39.3 → TimeLens-7B 48.8
   on Charades (+9.5 mIoU), 31.4 → 46.2 on ActivityNet (**+14.8**). **The base model
   is not the ceiling; the data and the objective are.**
3. The method is **RLVR on a verifiable reward (IoU)** — not a judge, not preference
   pairs, not teacher logprobs. It sidesteps every one of doc 00 §5.1's
   judge-validity problems and §3.2's teacher-logprob availability problem.
4. It needed **100 K examples, automatically re-annotated.** That is within one
   month of a mid-size customer's traffic at the volumes in §8.

⚠️ **The limits, stated honestly.** (a) Single paper, no independent replication
found. (b) 7–8 B, not 2 B — whether Marlin-2B's class reaches these numbers is
unknown. (c) The benchmarks were **re-annotated by the same authors who trained on
a re-annotation of their training sets**; doc 00 §8.4's contamination concern
applies with force, and the platform must not quote these numbers to a customer as
if they were third-party. (d) mIoU on a 30-second Charades clip is a far easier
problem than grounding in a 40-minute clip.

### 2.5 What Marlin-2B claims, and how to read it

From the model card [[src](https://huggingface.co/NemoStation/Marlin-2B)]:

| Claim | Number | How to read it |
|---|---|---|
| Dense captioning | *"Tops the CaReBench leaderboard"*; on DREAM-1K *"sits between Tarsier-34B and Gemini-1.5-Pro"* | Tarsier-34B is **36.3 F1** and Gemini-1.5-Pro **36.2** [[src](https://arxiv.org/html/2407.00634v2)] — the two are 0.1 apart, so "between" implies **≈36.2–36.3 F1** `est.` A 2 B model at Gemini-1.5-Pro's DREAM-1K score is a strong claim with **no number published** ⚠️ |
| Temporal grounding | *"On Tencent's TimeLens-Bench (Charades / ActivityNet / QVHighlights), Marlin beats Qwen2.5-VL-7B by +6.4 mIoU"* | ⚠️ **The card does not say which split, or whether +6.4 is a per-split or a cross-split average, and the two readings land far apart.** Read as Charades it implies `39.3 + 6.4 =` **≈45.7 mIoU** `est.`; read as the three-split mean it implies `(39.3+31.4+31.6)/3 + 6.4 =` **≈40.5 mIoU** `est.` The first reading puts Marlin above GPT-5's 40.5 Charades and below Gemini-2.5-Flash's 48.6; the second puts it at roughly GPT-5's level. **Do not quote either as the model's grounding score** |
| Grounding vs frontier | Key-features section: *"matches Gemini-2.0-Flash"*. Evaluation section: *"matches Gemini-2.5-Flash (non-thinking)"* | ⚠️ **The card contradicts itself.** Two different Gemini generations in two sections of the same card. Do not quote either without re-checking |
| Caption vs teacher | *"closes the gap to its Gemini-2.5-Flash teacher to within 0.21 / 0.43 of 10"* | A 0–10 rubric score, judge unspecified ⚠️. This is exactly doc 00 §5.1's "the judge is the bottleneck" |
| **Which model was the teacher** | Training-data section: *"dense re-annotations from **Gemini-3-Flash in thinking mode**"* and Stage 2 scored *"against a stronger **Gemini-3-Flash** judge"*. Evaluation section: *"closes the gap to its **Gemini-2.5-Flash** teacher"* | ⚠️ **A second self-contradiction, and this one matters more than the first.** The card names two different teacher generations for the same checkpoint. §4.4's teacher-cost arithmetic and §8.4's reproduction estimate both assume a Flash-class teacher, which holds either way — but the *quality* claim in §2.5 row 4 depends on which teacher the 0.21/0.43 gap was measured against. Re-check before reproducing the recipe |
| Everything | Three-panel figure, *"Recipe paper coming soon"* | **Every headline number is in a PNG.** There is no table, no eval harness reference, no seed. For an S7 gate this is unusable as-is |

**And the fact that matters most for this whole programme: Marlin-2B *is* an output
of the loop this platform is selling.** Verbatim from the card:

> *"dense re-annotations from **Gemini-3-Flash in thinking mode**, followed by
> targeted human review on the highest-impact splits… ~400K high-quality
> clip-level annotations… Two-stage post-training on a single H100. Stage 1 is
> supervised fine-tuning… Stage 2 is preference optimization via **SimPO**… candidate
> completions from the SFT checkpoint are scored against a stronger Gemini-3-Flash
> judge using a rich rubric (factual accuracy, completeness, temporal alignment)"*
> [[src](https://huggingface.co/NemoStation/Marlin-2B)]

Map it onto doc 00's stages: sparse public labels + **teacher annotation (S3)** →
**400 K dataset (S4)** → **SFT (S5)** → student samples re-scored by a **teacher
judge (S3 again)** → **SimPO preference optimisation (S5)** → checkpoint (S6). On
**one H100**. This is the reference implementation of the platform's video loop,
executed by a two-person team, and it is sitting in this repo.

**The three things it did that the platform must automate:** (a) teacher =
Gemini-3-Flash *in thinking mode*, tuned to emit *"temporally grounded atomic
events with explicit `<start-end>` boundaries per claim rather than free-form
prose"* — i.e. the teacher prompt was engineered for *checkability*, which is §4.3;
(b) *"targeted human review on the highest-impact splits"* — i.e. selective human
adjudication, which is §4.5; (c) SimPO rather than DPO, *"without a reference
model, making it cheaper and more stable than DPO at this scale"* — a method choice
doc 00's §3.2 ladder does not list and should.

### 2.6 What the public leaderboards do and do not tell you

The Video-MME leaderboard, fetched 2026-09-19, is topped by **video-SALMONN 2+ (72
B) at 81.6 %** and **Gemini 1.5 Pro at 81.3 %** with subtitles
[[src](https://video-mme.github.io/home_page.html)]. The benchmark's own paper puts
**Gemini 1.5 Pro at 75.0 % without / 81.3 % with subtitles** and **GPT-4o at 71.9 /
77.2**, best open-source **VILA-1.5 at 59.0 / 59.4**
[[src](https://arxiv.org/html/2405.21075v2)].

**Gemini 1.5 Pro is a 2024 model still sitting at #2 on a public leaderboard in
late 2026**, and the leaderboard's own entries stop at **2025-09-28**
[[src](https://video-mme.github.io/home_page.html)]. It is not being maintained
against current frontier models: no GPT-5, no GPT-6 Astra and no Gemini 3.x appears.
⚠️ *Corrected 2026-09-19: an earlier draft said "nor Claude appears" — **Claude 3.5
Sonnet is on the board at 62.9 % with subtitles**, which if anything sharpens the
point, since that is also a 2024 model and it sits 18 points below the top.*

**Decision rule, and it is doc 04's rule for video too: do not gate on a public
video leaderboard.** Use them for *student shortlisting* (they rank open models
against each other adequately) and never for the parity claim. The parity claim is
customer traffic, customer prompts, customer slices — doc 00 §5.6 item 1, which is
non-negotiable and is even more non-negotiable here because the leaderboards are
stale.

**Video-MME's split structure is worth stealing anyway** — it is the correct
stratification for a video eval (§6.4):

| Split | Videos | Mean duration |
|---|---:|---:|
| Short | 300 | **82.5 s** |
| Medium | 300 | **562.7 s** (9.4 min) |
| Long | 300 | **2,385.5 s** (39.8 min) |

*(Corrected 2026-09-19. Earlier drafts printed 80.7 / 515.9 / 2,466.7 s; the paper's
Table 1 `Avg. V.L.` column reads **82.5 / 562.7 / 2385.5**, with an all-splits mean of
**1017.9 s** [[src](https://arxiv.org/html/2405.21075v2)]. The 254-hour total is
consistent with both, so only the source settles it.)*

900 videos, 254 hours, 2,700 QA pairs, 6 visual domains / 30 subfields, built by
*"researchers proficient in English with extensive research experience in
vision-language learning"*, each watching the complete video and writing **3
questions per video** [[src](https://arxiv.org/html/2405.21075v2)].

---

## 3. Data capture for video

### 3.1 What a video trace is, and the four things you can store

S2's contract in doc 00 is "immutable trace rows: prompt stack, tools, tool
results, output, tokens, latency, cost, user feedback, outcome signal". For video
the *input* is not a string, and there are four materially different things you
could persist:

| Option | What it is | Size, 3-min 1080p clip | Reproducible? | Privacy surface |
|---|---|---:|---|---|
| **A — Reference only** | Customer's URI + content hash + byte range | ~200 B | ❌ — the object can be deleted, moved or re-encoded | Smallest |
| **B — Sampled frames** | The exact frames the model saw, as JPEG | **7.2 MB** `est.` (240 × 30 KB @448²) | ✅ **exactly** | Medium — faces, screens, documents |
| **C — Full video** | The source bytes | **112.5 MB** `est.` (H.264 @5 Mbps) | ✅ | Largest — audio, all frames, metadata |
| **D — Encoder output** | ViT token embeddings | **96.3 MB** `est.` (23,520 × 2,048 × 2 B bf16) | ✅ for this checkpoint only | Medium — inverts partially ⚠️ |

`est.` derivations: B assumes Marlin's 240-frame, 448×448 budget
([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.3) at
~30 KB/frame JPEG q85; C assumes 5 Mbps H.264; D uses Marlin's 23,520 video tokens
at hidden dim 2,048.

**The recommendation is B, and it is not close.**

- It is **15.6× smaller than C** and **13.4× smaller than D**.
- It is **the actual model input**, so a replay is bit-exact — which is what I4
  (contract fidelity) and the gate-twice rule (doc 00 §1.2) require. Replaying from
  C re-runs the decoder, and decoder version differences change frames.
- It **removes the decode cost from every downstream stage.** §5.4 shows video
  decode, not GPU compute, is the training bottleneck; storing B pays that cost once.
- It is **codec-independent**, so it survives the customer rotating their CDN.
- **It stores strictly less than C** — no audio track, no unsampled frames, no
  container metadata. That is a privacy improvement, not just a size one (§3.4).

Store **A alongside B always** (the reference is 200 bytes and is the only way to
re-sample at a different fps later). Store **C only for an explicitly flagged
subset** — see §3.3.

### 3.2 Trace schema deltas for video

On top of doc 02's text schema, a video trace needs these fields. This is a
proposal, not a fetched standard; ⚠️ the OpenTelemetry GenAI semantic conventions
doc 00 §6 standardises on has **no video-specific attributes I could confirm in
this session**.

```jsonc
{
  // ---- identity of the media, not the request ----
  "media": [{
    "media_id":        "sha256:...",     // content hash of the SOURCE bytes
    "source_uri":      "s3://cust/...",  // option A
    "duration_s":      182.4,
    "container":       "mp4", "codec": "h264",
    "width": 1920, "height": 1080, "source_fps": 29.97,
    "has_audio":       true,
    "audio_used":      false,            // did THIS model consume audio?

    // ---- what the model actually saw: option B ----
    "frames_uri":      "r2://traces/2026/09/sha256-.../frames.tar",
    "frames_hash":     "sha256:...",     // hash of the frame set, not the video
    "n_frames":        240,
    "frame_w": 448, "frame_h": 448,
    "sample_policy":   "uniform_capped", // see §3.3
    "requested_fps":   2.0,
    "effective_fps":   1.32,             // n_frames / duration_s  <-- THE field
    "frame_ts_s":      [0.0, 0.76, ...], // per-frame source timestamp, REQUIRED
    "decoder":         "torchcodec@0.9.1",   // or pynvvideocodec@2.0.4
    "sampler_version": "qwen-vl-utils@0.0.14",

    // ---- segmentation, if the clip was split ----
    "segment_index":   0, "segment_count": 2,
    "segment_span_s":  [0.0, 120.0],
    "parent_media_id": "sha256:..."
  }],

  // ---- token accounting, which is NOT the text accounting ----
  "usage": {
    "vision_tokens":   23520,   // LM tokens from the visual tower
    "text_tokens":     40,      // the scaffold
    "audio_tokens":    0,
    "output_tokens":   768,
    "vit_patches":     94080    // the ViT job, which $/1M-in does NOT price (§7.3)
  },

  // ---- the budget knobs, hashed into the artifact (doc 00 §2.2) ----
  "media_config_hash": "sha256:...",  // over {VIDEO_MAX_PIXELS, FPS, FPS_MAX_FRAMES,
                                      //       FPS_MIN_FRAMES, longest_edge, backend}
  "privacy": { "faces_detected": 3, "redaction_applied": false, "retention_class": "90d" }
}
```

**Five fields carry the weight.**

1. **`effective_fps`.** Both students silently drop below the requested rate:
   Marlin's 240-frame cap gives *"0.40 fps of a 10-minute clip and 0.067 fps of an
   hour"*
   ([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.3);
   Qwen3.8's whole-video token budget trades frames against resolution. **Without
   this field, a quality regression that is really a sampling regression is
   undebuggable.** It is also the field that makes §6.4's parity protocol possible.
2. **`frame_ts_s`.** Every temporal output — `<start-end>` spans, event timestamps
   — is only interpretable against the frames' source timestamps. vLLM lets a
   client pass `"fps"`, `"frames_indices"`, `"total_num_frames"` and `"duration"`
   in `--media-io-kwargs` precisely *"to preserve temporal information from
   client-side frame extraction"*
   [[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)]. Capture
   what you passed.
3. **`media_config_hash`.** This is doc 00 §2.2's prompt-hash argument extended to
   pixels. Marlin has **two conflicting preprocessing paths in its own repo** — the
   `qwen-vl-utils` env-var path (`VIDEO_MAX_PIXELS=200704, FPS=2.0,
   FPS_MAX_FRAMES=240` → 23,520 tokens) and the HF `Qwen3VLVideoProcessor` path
   (`max_frames: 768`, `longest_edge: 25165824` for the whole video → 12,288
   tokens), and taking the wrong one gives *"half the frames or half the
   resolution the training-time path did — a silent degradation"*
   ([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.3).
   **The media config belongs inside the versioned artifact** exactly as the prompt
   stack does. A customer changing `fps` is a distribution shift that voids the
   parity claim, and the hash is how you detect it.
4. **`vit_patches`.** §7.3 shows the repo's `$/1M input` figures are computed from
   *LM* prefill rates and do not price the vision tower. Capturing the patch count
   is the only way to reconcile a video bill.
5. **`frames_hash` separate from `media_id`.** Two requests against the same video
   at different fps are different inputs. Dedup on `frames_hash`; group on
   `media_id`.

**vLLM gives you a free win here.** It supports *"optional `uuid` fields in
requests; sending `None` with a UUID allows cache hit reuse without retransmitting
video data"*
[[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)]. Use
`frames_hash` as the UUID. Re-annotation, shadow-mode replay and A/B then **never
re-upload or re-decode the video** — they send a hash and a prompt. This is the
mechanism that makes §6's shadow mode affordable.

### 3.3 Frame-sampling policy — the one place to spend engineering

Three policies, and the choice is a product decision, not a default.

| Policy | Mechanism | When | Cost |
|---|---|---|---|
| **Uniform-capped** (both students' default) | `frames = clamp(fps × duration, min, max)`, uniform | Default. Deterministic, replayable, matches training | Free |
| **Segment-and-batch** | Split at ≤120 s, run N inferences, merge with offset | **The recommendation for any clip > 2 min** (§6.4). Restores 2 fps at all lengths | N× inference, and N is small because a 240-frame prefill is a constant |
| **Content-adaptive** (shot boundaries, motion) | Sample more where the scene changes | Only if the eval shows uniform sampling misses the failure slice | Adds a decoder pass; **breaks replayability unless the chosen indices are in the trace** |

**Segment-and-batch is the important one and it is cheap.** Marlin's per-request
cost is flat in clip length — a 10-minute clip and a 2-minute clip both prefill
~23,560 tokens. Segmenting a 10-minute clip into five 2-minute windows costs **5 ×
$0.000432 = $0.00216** on a B200 (`low` tier,
[`../models/marlin2b/README.md`](../models/marlin2b/README.md)) and restores the
full 2.0 fps. Against Gemini 3.8 Flash at 1 fps low-res on the same clip (60,000
input tokens = $0.045, plus $0.00288 output) that is still **22× cheaper at 2× the
frame rate.** Not segmenting saves $0.0017 and destroys the quality argument.

⚠️ **The cost of segmenting is cross-segment reasoning.** "What happened after the
man left?" spanning a boundary is unanswerable per-segment. Mitigations, in order of
laziness: (a) overlap windows by 10–15 s; (b) pass the previous segment's caption as
text context into the next; (c) for row 6 tasks (§1.1), don't segment — route to
the frontier. **⚠️ TO BE VERIFIED: the accuracy cost of (a)+(b) versus a single
long-context pass. No measurement found. This is doc 04's first video experiment.**

**Sampling *traces*, not frames.** Doc 00 §6 warns that sampling drops the tail that
matters. For video the storage cost forces sampling, so make it stratified rather
than random:

| Bucket | Store | Rate | Why |
|---|---|---|---|
| Student/incumbent disagreement | **A + B + C** | 100 % | Doc 00 §7.2: "mine the disagreements". These are the disengagements |
| Judge uncertain / low confidence | A + B + C | 100 % | The S3 human-review queue |
| User retried, edited or thumbs-downed | A + B + C | 100 % | Free ground truth |
| Schema-invalid or parse-failure output | A + B | 100 % | I4 violations |
| Long tail by `duration_s` bucket | A + B | 100 % of the >15-min bucket | Rarest and hardest; never let random sampling starve it |
| Everything else | **A only** + usage/latency/cost | 2–5 % get B | The average request teaches nothing |

### 3.4 Privacy — where video is categorically worse than text

Doc 00's I6 says PII is redacted before any egress to a teacher. For text that is
a regex-and-NER problem. For video it is not.

| Risk | Why video is worse | Mitigation | Residual ⚠️ |
|---|---|---|---|
| **Faces** | Biometric data under GDPR Art. 9 / BIPA / Illinois etc. Every frame is a face capture | Detect-and-blur before storage; store only blurred frames as option B | Blurring changes the model input, so the trace is no longer what the model saw — **you must blur before inference or accept the trace is a redacted copy.** This is a real fork in the design |
| **Anthropic refuses anyway** | *"Claude cannot be used to name people in images and refuses to do so"* [[src](https://platform.claude.com/docs/en/build-with-claude/vision)] | — | A teacher that refuses on a fraction of your traffic silently biases the annotation set (§4.6) |
| **Screens and documents in frame** | A 448×448 downscale does not reliably destroy legible text; a 1080p frame certainly does not | OCR-scan frames, route hits to a stricter retention class | OCR on 240 frames/video is its own cost |
| **Audio** | Carries names, numbers, everything. Gemini bills it at 32 tok/s, i.e. it is *on by default* if you send the file | **Set `audio_used: false` and strip the track before egress** unless the task needs it. Marlin's `caption`/`find` modes are frame-only | If the incumbent used audio and the student does not, that is a capability gap, not a privacy win — declare it |
| **Location and identity metadata** | EXIF/container metadata, GPS, device IDs | Option B drops it by construction | Option C does not — another reason to store C only for flagged traces |
| **Re-identification from embeddings** | Option D (ViT embeddings) is not obviously non-invertible | Prefer B over D | ⚠️ **TO BE VERIFIED**: inversion risk for Qwen3.5-class ViT embeddings. No source found. Treat D as PII until proven otherwise |

**The teacher-egress question is sharper for video than text.** Doc 00 §8.1
documents that all three text vendors prohibit distillation. Google's terms say:
*"You may not use the Services to develop models that compete with the Services
(e.g., Gemini API or Google AI Studio). You also may not attempt to reverse
engineer, extract or replicate any component of the Services, including the
underlying data or models (e.g., parameter weights)"*
[[src](https://ai.google.dev/gemini-api/terms)].

The paid-vs-free distinction matters and is worth quoting to customers: on **unpaid**
services Google uses content *"to provide, improve, and develop Google products and
services and machine learning technologies"* and *"human reviewers may read,
annotate, and process"* it; on **paid** services *"Google doesn't use your
prompts… or responses to improve our products"*, with retention *"for a limited
period of time, solely for detecting and preventing violations"* and legal compliance
[[ibid.](https://ai.google.dev/gemini-api/terms)]. ⚠️ *Corrected 2026-09-19: this
sentence previously read "30-day retention". The terms say "a limited period of time"
and name no number — do not quote 30 days to a customer.*

**Operational rules that follow, both non-negotiable.** (1) **Never send customer
video through a free-tier key** — human reviewers may watch it. (2) The
competing-models clause is at least as broad as OpenAI's and Anthropic's, and
Gemini is the *only* native video teacher. **Doc 08 owns getting this authorised in
writing; it is the risk that can end the video product**, and unlike text there is
no second vendor to fall back to except self-hosted open weights (§4.2).

### 3.5 Volumes and storage cost

Anchored on **Cloudflare R2 at $0.015/GB-month Standard, $0.010 Infrequent Access,
free egress, Class A $4.50/M and Class B $0.36/M operations**
[[src](https://developers.cloudflare.com/r2/pricing/)]. ⚠️ S3's own pricing page did
not yield extractable per-GB numbers on two fetches; R2 is the cited anchor and S3
Standard is in the same order. Free egress materially favours R2 for a workload that
replays frames into training and eval repeatedly.

**50,000 videos/month, 3-minute mean** (the §8 scenario):

| Policy | Per video | Per month | **Month-12 stored** | **Month-12 $/mo** | Year-1 cumulative $ |
|---|---:|---:|---:|---:|---:|
| **C — full video** | 112.5 MB | 5.63 TB | 67.5 TB | **$1,013** | $6,581 |
| **B — frames, all traces** | 7.2 MB | 360 GB | 4.32 TB | $65 | $421 |
| **B — frames, 5 % + all flagged (≈12 %)** | — | 43 GB | 518 GB | **$7.77** | $50 |
| **A — reference only** | 200 B | 10 MB | 120 MB | $0.002 | $0.01 |

Recommended mix (A always + B at ~12 % + C for flagged only, ~2 %):
`(0.12 + 0.02×15.625) × 360 GB =` **156 GB/month** `est.` → **~$28/month at month 12,
~$182 in year 1.**

> **⚠️ Corrected 2026-09-19.** Earlier drafts wrote this line as `0.12 + 0.02×15.6 =
> 0.43` **TB**/month and carried $78/month and ~$500/year downstream into §8.4. The
> `0.43` is a *ratio* against the all-traces-B row (360 GB/month), not a tonnage:
> `0.43 × 360 GB = 155 GB`, not 430 GB. The mix is **2.8× cheaper** than the
> superseded figure, and §8.4/§8.5 are recut on $28.

Set against §8.3's **$58.50/month** marginal inference cost, storage is roughly
**half** the inference cost at this volume — not a multiple of it. *(An earlier draft
compared it against $21.60/month, which is 50 K single Marlin calls at B200's
$0.432/1k; §8 actually serves two calls per video on an RTX PRO 6000, so $58.50 is
the comparable line.)* It is still the item that **grows**, and inference is not. Put
a retention class on every trace on day one; retrofitting one onto 67 TB is a project.

---

## 4. Annotation for video with teacher VLMs

### 4.1 What a video annotation actually is

| Label type | Shape | Teacher call | Verifiable without a human? |
|---|---|---|---|
| **Dense caption** | Scene paragraph + `<start–end> description` events | 1 video call | Partially — AutoDQ F1 against a reference (§6.2) |
| **Temporal span** | `(start_s, end_s)` for a query | 1 video call | ✅ **IoU against reference. Fully verifiable** |
| **Structured extraction** | JSON against a schema | 1 video call | ✅ schema validity + field match |
| **Classification / moderation** | Label + confidence | 1 video call | ✅ exact match |
| **QA pair** | Question + answer (+ distractors for MCQ) | 1 video call, or 1 to generate + 1 to answer | ✅ if MCQ |
| **Preference pair** | (chosen, rejected) over 2 student samples | 2 student samples (own GPU) + 1 teacher judge call | ❌ — this is where judge validity bites |
| **Per-token logprobs** | Teacher logprob on the student's own trajectory | **Not available from any frontier video API** ⚠️ | — |

**The last row is the structural constraint on video distillation.** Doc 00 §3.2
makes on-policy distillation the default method, and it needs per-token teacher
logprobs on the student's rollouts. I found **no evidence Gemini exposes logprobs
at all**: neither the text-generation guide
[[src](https://ai.google.dev/gemini-api/docs/text-generation)] nor the
`generate-content` reference
[[src](https://ai.google.dev/api/generate-content)] mentions `responseLogprobs`
or `logprobs` in the sections that fetched. ⚠️ **TO BE VERIFIED** — the reference
page truncated before `GenerationConfig`, so this is "not found", not "does not
exist". But even if Gemini returned logprobs on *its own* generations, on-policy
distillation needs them on *the student's* continuation, which requires an
echo/prompt-logprobs mode no frontier vendor documents.

**So for video the §3.2 ladder truncates.** Rungs available:

| Rung | Available for video? | Note |
|---|---|---|
| 1. Prompt + tier-down only | ✅ | Gemini Pro → Flash → Flash-Lite, or static → agentic mode. **Always try first.** §8 shows this alone can end the engagement |
| 2. Rejection-sampling SFT | ✅ | Teacher generates N, a verifier or rubric filters. Needs no logprobs |
| 3. Off-policy SFT on teacher traces | ✅ | **What Marlin-2B stage 1 did** — 400 K Gemini-3-Flash re-annotations |
| 4. DPO / preference distillation | ✅ | **What Marlin-2B stage 2 did**, via SimPO |
| 5. On-policy distillation (GKD) | **⚠️ only with a self-hosted teacher** | §4.2 |
| 6. RL against a verifiable reward | ✅ **and it is the best rung for video** | **What TimeLens did** — RLVR on IoU. No teacher needed at inference time at all |

**The reordering this forces.** For text, doc 00 puts on-policy distillation at the
top. **For video the top rung is 6, then 4, then 3** — because temporal grounding,
extraction and classification all have verifiable rewards, and because rung 5 needs
a teacher you host yourself.

### 4.2 The self-hosted teacher, which video makes attractive

ms-swift's GKD supports three teacher sources: `--teacher_model` (loaded in-process),
`--teacher_model_server` (an external vLLM service via `swift deploy`), or
self-distillation
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Distillation.md)].
An external teacher service *"requests logprobs by prompt without loading teacher
weights on training GPUs"*, and GKD against an API teacher *"Requires
`--gkd_logits_topk` (API returns only top-k logprobs)"* [[ibid.]].

**That is the whole mechanism, and it works with an open-weights video teacher.**
The repo's own ladder supplies candidates: **Qwen3.8-2.4T-A95B** is listed in
ms-swift as `vision, video` capable
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Supported-models-and-datasets.md)],
as is Qwen3.8-27B. A self-hosted teacher unlocks:

| Unlocked | Why it matters |
|---|---|
| **Full-vocabulary or top-K logprobs** | Rung 5 becomes available for video. The one method doc 00 calls the right default |
| **No ToS problem (§3.4)** | Apache/Qwen-licence weights, not a vendor's API. This alone may decide it |
| **No PII egress** | Frames never leave the tenancy. I6 becomes trivial instead of hard |
| **Flat cost** | Teacher tokens are GPU-hours you already pay for, not a per-call meter |

The trade is quality: a self-hosted 27 B or MoE teacher is not Gemini-3-Flash. ⚠️
**TO BE VERIFIED — the quality gap between the best self-hostable video teacher and
Gemini-3-Flash on dense captioning.** No head-to-head found. **Doc 03 owes this
experiment**, because the answer decides whether the video annotation pipeline runs
on an API meter with a legal problem or on our own GPUs without one.

ms-swift also notes, citing DeepSeek-V4's technical report, that *"using only
sampled-token log-ratio as advantage yields high gradient variance, so its
full-vocabulary OPD uses complete logit distillation"*
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Distillation.md)]
— another argument for a teacher you control, since full-vocabulary logits are only
available locally.

### 4.3 Teacher-prompt design — make the annotation checkable

Marlin-2B's card is explicit that the win came from *how* the teacher was asked:

> *"The teacher pipeline was tuned specifically to produce temporally grounded
> atomic events and actions, with explicit `<start-end>` boundaries per claim
> rather than free-form prose."* [[src](https://huggingface.co/NemoStation/Marlin-2B)]

**This is the highest-leverage decision in the whole video annotation pipeline and
it costs nothing.** An atomised, span-tagged annotation is:

- **checkable** — each claim has a timestamp that can be spot-verified by a human in
  seconds instead of by re-watching the clip;
- **decomposable** — AutoDQ-style event extraction becomes trivial because the
  events are already extracted (§6.2), so the eval judge is cheap;
- **partially rejectable** — a caption with 6 events of which 1 is wrong yields 5
  good training claims instead of 1 discarded example;
- **a preference signal for free** — two candidate captions differing on one event
  give a targeted pair.

**Rule: never accept free-form prose from a video teacher.** Always demand a
schema with per-claim temporal boundaries, and validate it programmatically before
it enters S4.

### 4.4 Teacher cost arithmetic

Gemini 3.8 Flash, **Batch API at 50 %** (24 h turnaround, same modalities as
interactive [[src](https://ai.google.dev/gemini-api/docs/batch-api)]) — which is
correct for annotation, since S3 is never latency-sensitive. Effective rates
**$0.375/1M in, $1.875/1M out**.

| Clip | Res | Input tok | Output tok | **$/clip (batch)** | $/100 K clips | $/400 K clips |
|---|---|---:|---:|---:|---:|---:|
| 2 min | low | 12,000 | 768 | **$0.00594** | $594 | $2,376 |
| 2 min | high | 34,800 | 768 | **$0.01449** | $1,449 | **$5,796** |
| 3 min | high | 52,200 | 768 | $0.02101 | $2,102 | $8,406 |
| 10 min | high | 174,000 | 768 | $0.06669 | $6,669 | $26,676 |
| 10 min, **agentic** ⚠️ | — | ~20,880 | 768 | $0.00927 | $927 | $3,708 |

The agentic row applies the documented *"up to 88% more token-efficient"* claim to
content tokens and **excludes `total_thought_tokens` and `total_tool_use_tokens`**,
which are billed
[[src](https://ai.google.dev/gemini-api/docs/video-understanding)] — so it is a
**lower bound on the bill, not an estimate of it.** ⚠️ **TO BE VERIFIED**: the
realised all-in cost of agentic mode. It is doc 03's second experiment and it cuts
both ways — it is the incumbent's counter-move (§0.5) *and* the platform's cheapest
teacher.

**The headline: Marlin-2B's entire 400 K-clip teacher corpus reproduces for
~$5,800 at 2-minute high-resolution clips, or ~$2,380 at low resolution.** Against
the ~$672 of GPU time to train on it (§5.5) and the ~$25 K of the eval set (§4.5),
**teacher tokens are the cheapest input to the video loop.** Do not optimise them.
Optimise the human review and the eval set.

### 4.5 Human review — the real cost, and how much you can afford

There is no published $/minute for expert video annotation that I could fetch.
Three anchors, all marked:

| Anchor | Figure | Source | Gives |
|---|---|---|---|
| **Roboflow managed labeling** | $0.05/classification, $0.10/bounding box, $0.20/polygon | [[src](https://roboflow.com/pricing)] | Per-*object* frame labelling, not clip-level judgement |
| **Video-MME construction** | 900 videos / 254 h / 2,700 items, each annotator watching the complete video, 3 questions each, by domain experts | [[src](https://arxiv.org/html/2405.21075v2)] | **Watch-time floor: 254 h for 2,700 items = 5.6 min of watching per item, before authoring** |
| **DREAM-1K construction** | 1,000 clips, 8.9 s mean, 6.3 events / 2.2 subjects / 1.9 shots, **59.3 words** of *"fine-grained manual annotation… covering all events, actions, and motions"* per clip | [[src](https://arxiv.org/html/2407.00634v2)] | **Dense-caption authoring on a 9-second clip is a ~60-word structured write-up** |

`est.` model, reasoning stated: a reviewer **adjudicating** a teacher-produced,
span-tagged caption (§4.3) does not author from scratch — they watch once and check
claims. Call it 1.5× real time plus 1 minute of verdict-writing. At a loaded rate of
$20/h for a generalist and $50/h for a domain expert:

| Clip | Reviewer min | **$ @ $20/h** | **$ @ $50/h** | × teacher cost (2-min high-res, $0.0145) |
|---|---:|---:|---:|---:|
| 30 s | 1.75 | $0.58 | $1.46 | 40× / 101× |
| 2 min | 4.0 | **$1.33** | **$3.33** | **92× / 230×** |
| 10 min | 16.0 | $5.33 | $13.33 | 368× / 919× |

Authoring a **new eval item** from scratch is the Video-MME shape, not the
adjudication shape: 5.6 min of watching plus authoring, `est.` 3× that end-to-end at
expert rates → **~$8.50/item** `est.` (`17 min × $30/h = $8.50`). For a 3,000-item
eval set: **~$25,500, one-off.** *(Corrected 2026-09-19: the draft printed $8.47 and
$25,410, which is `16.94 min`, not the 17 min the method states.)*

**The three rules this gives the platform, and they are the operating model of S3
for video:**

1. **Teacher-annotate 100 %; human-review ~1 %.** At 92–230× the cost ratio, 1 %
   human review roughly doubles the annotation budget. 10 % would sextuple it.
2. **Spend the 1 % where the loop points**, never at random: teacher/student
   disagreement, judge-uncertain, schema-invalid, and the long-duration bucket
   (§3.3). This is Marlin's *"targeted human review on the highest-impact splits"*,
   automated.
3. **The eval set is the expensive artifact, not the training set.** ~$25.5 K for
   3,000 items against ~$5,800 for 400 K training clips. **Build it once, freeze
   it, version it, and never let it leak into training** (doc 00 §8.4). In video
   the contamination risk is sharper than in text because the *same source video*
   may legitimately appear under two IDs after a re-encode — **dedup the eval split
   on perceptual hash of the frame set, not on `media_id` or URI.**

### 4.6 Judge validity for video

Doc 00 §5.1 makes judge validity the crux. For video, three regimes, and the whole
point is to stay out of the third.

| Regime | Method | Cost per item | Validity |
|---|---|---:|---|
| **Verifiable** | Temporal IoU vs reference span; schema validity; exact-match label | **$0** | Highest. R@1@IoU{0.3,0.5,0.7} + mIoU is a published, standard protocol [[src](https://arxiv.org/html/2512.14698v1)] |
| **Text-only judge** | AutoDQ: *"an event extraction model"* pulls events from both candidate and reference, then *"a natural language inference model computes precision and recall by determining how many events extracted from one description are entailed by the other"* [[src](https://arxiv.org/html/2407.00634v2)] | **~$0.0002** `est.` (2 × ~500 text tokens on a Flash-class model) | Good, **and the judge never watches the video** — so it costs text prices and is fully reproducible. Requires a human reference caption |
| **Video judge** | Frontier multimodal call scoring the candidate against the clip | **$0.0145** `est.` (2-min high-res batch) — **72× the text judge** | Weakest and dearest. Marlin's own SimPO used this shape (Gemini-3-Flash rubric judge) with an unspecified 0–10 scale ⚠️ |

**⚠️ AutoDQ's correlation with human judgement is not reported in the Tarsier
paper** [[src](https://arxiv.org/html/2407.00634v2)]. Doc 00 §5.1 requires judge
validation against human labels before a judge can gate a promotion. **So AutoDQ
must be validated on the customer's own data before it gates anything** — budget
~200 human-adjudicated items (≈$266 at $20/h, 2-min clips) to measure
judge-vs-human agreement. That is cheap and it is not optional.

**Four video-specific judge failure modes**, `est.` from the mechanisms above:

| Failure | Mechanism | Detection |
|---|---|---|
| **Temporal hallucination scored as correct** | A text judge comparing two captions cannot check a timestamp against pixels. A confidently wrong `<12.0–18.0>` entails the reference's event text and scores as a hit | Score events on **text entailment AND temporal IoU jointly**; report them separately. Never collapse into one number |
| **Verbosity wins** | Event-recall rewards listing more events; precision is the only counterweight and NLI entailment is lenient | Report P and R separately; cap event count; include a length-matched slice |
| **The judge sees fewer frames than the model under test** | A video judge at 1 fps grading a student that saw 2 fps will mark correct fine-grained events as hallucinations | Pin the judge's `effective_fps` ≥ the student's, and record it in the trace |
| **Teacher-as-judge self-preference** | Gemini-3-Flash judging a student distilled from Gemini-3-Flash rewards its own style. Doc 00 §3.3's "style over substance" | Use a **different family** as judge from the teacher, or prefer the verifiable/text-only regimes |

---

## 5. Training video VLMs

### 5.1 Framework selection — one clear answer

| Framework | Video SFT | Video GKD / on-policy | Qwen3.5/3.8-VL | Verdict |
|---|---|---|---|---|
| **ms-swift** | ✅ *"mixed modality data training with text, images, video and audio"*, *"multimodal packing technology to improve training speed by 100%+"*, *"independent control of vit/aligner/llm"* [[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/README.md)] | ✅ **multimodal GKD since 2025-06-15**, with a dedicated `examples/train/multimodal/rlhf/gkd` path [[ibid.]] | ✅ Qwen3.5-0.8B/2B/4B/9B and Qwen3.8-27B all tagged `vision, video`; Qwen3.8-Flash-Next Day-0 [[src](.../Supported-models-and-datasets.md)] | **Use this** |
| **LLaMA-Factory** | ✅ *"video recognition"*; Qwen3-VL 2B–235B, LLaVA-NeXT-Video, InternVL 2.5–3.5 [[src](https://raw.githubusercontent.com/hiyouga/LLaMA-Factory/main/README.md)] | ❌ no GKD listed | ⚠️ **VL entries stop at Qwen3-VL**; Qwen3.5/3.6 appear as text templates only | Fine for Qwen3-VL-class students. **Cannot train Marlin-2B or Qwen3.8-27B video as listed** |
| **TRL** | ❌ in practice | `trl.experimental.gkd`, `AutoModelForCausalLM` teacher, text-`messages` dataset, `max_length` default **1024** [[src](https://huggingface.co/docs/trl/en/gkd_trainer)] | ❌ | **Reference implementation of GKD, not a video trainer.** 1024 tokens is 4 % of one video request |
| **NVIDIA NeMo microservices** | ❌ | ❌ | ❌ | Customizer lists *"LoRA, SFT, DPO, Embedding"* only; Evaluator lists no multimodal or video benchmark; and *"NeMo Microservices will be sunset on October 1, 2026"* [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)]. **Do not build on it** |

**Decision: ms-swift for the video training path, full stop.** It is the only
framework that trains the repo's actual students, supports video GKD, and is what
Marlin-2B's own card names (*"vLLM- and swift-deploy-compatible"*
[[src](https://huggingface.co/NemoStation/Marlin-2B)]). Everything else is either a
different model family or a different modality.

### 5.2 The SFT recipe, knob by knob

ms-swift's defaults, all from
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Command-line-parameters.md)]:

| Knob | Default | What to set for video, and why |
|---|---|---|
| `--freeze_vit` | **`True`** | **Keep `True` for the first run.** The ViT is 302 M of Marlin's 2.2 B ([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §3) and Marlin was built *"with the video-capable visual tower kept intact"*. Unfreezing triples the step cost (§5.4) and is the fastest way to destroy general visual competence |
| `--freeze_aligner` | **`True`** | **Set `False`.** The aligner (merger: 25.2 M params) is where a new output *format* — a span schema, a JSON shape — is cheapest to learn. Small, high-leverage |
| `--freeze_llm` | `False` | Leave `False`. This is where the task lives |
| `--vit_lr` / `--aligner_lr` | = `learning_rate` | If you do unfreeze the ViT, set `vit_lr` **5–10× below** the LLM lr `est.` — standard VLM practice, ⚠️ no source fetched for the exact ratio |
| `--vit_gradient_checkpointing` | auto-on when `freeze_vit=False` | Leave auto |
| `--packing` | `False` | **`True`.** *"improve training speed by 100%+"* for multimodal, supports **CPT/SFT/DPO/KTO/GKD**, `binpack` best-fit-decreasing default. Requires `--attn_impl flash_attn` |
| `--max_length` | model max | **Set explicitly to ~25,000** for Marlin (23,520 video + scaffold + output). Leaving it at the 262,144 model max makes packing meaningless and memory unpredictable |
| `--truncation_strategy` | `delete` | Keep `delete`. A truncated video is a corrupt example, not a short one |
| `VIDEO_MAX_PIXELS` | **`768*28*28` = 602,112** | **Set to `200704`** for Marlin. The ms-swift default is **3× Marlin's training-time per-frame budget** — training at the default and serving at 200,704 is a silent train/serve mismatch |
| `FPS` | `2.0` | Matches Marlin. Leave |
| `FPS_MAX_FRAMES` | **`768`** (settable via `--model_kwargs '{"fps_max_frames": N}'` or the env var) | **`240`** for Marlin. ⚠️ **Corrected 2026-09-19: this row previously listed no default.** ms-swift's documented default is **768 frames**, i.e. **3.2× Marlin's 240-frame cap**, so `FPS_MAX_FRAMES` is a *second* silent train/serve mismatch stacked on `VIDEO_MAX_PIXELS` — at the two defaults together a training example is 768 frames × 602,112 px against Marlin's 240 × 200,704, ~9.6× the pixels. ms-swift's own docs *example* uses `12`; copy neither the example nor the default [[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Command-line-parameters.md)] |
| `--target_modules all-linear` | — | *"For multimodal LLMs, tuners are by default only attached to the LLM component"* — so LoRA + `all-linear` already implies a frozen tower unless you flip the freeze flags |

**The `VIDEO_MAX_PIXELS` mismatch is a real, shipped trap.** Marlin's `.caption()`
path sets `VIDEO_MAX_PIXELS=200704`, `FPS=2.0`, `FPS_MAX_FRAMES=240`
([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.3);
ms-swift defaults to `768 * 28 * 28` = **602,112** px/frame and **768** frames, with
`FPS` 2.0 and `FPS_MIN_FRAMES` 4 matching
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Command-line-parameters.md)]. Train at the default, serve at Marlin's, and you have
changed the model's input distribution between S5 and S9 for no reason. **Put the
media config in the artifact (§3.2) and assert it at train time.**

**Resolution / fps / sequence-length budgets to plan against**, from
[`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.3:

| Clip | 2 fps frames | Capped | Effective fps | LM video tokens | ViT patches | Prefill tokens | KV (BF16) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2 min | 240 | 240 | **2.00** | 23,520 | 94,080 | ~23,560 | 275.6 MiB |
| 10 min | 1,200 | 240 | **0.40** | 23,520 | 94,080 | ~23,560 | 275.6 MiB |
| 60 min | 7,200 | 240 | **0.067** | 23,520 | 94,080 | ~23,560 | 275.6 MiB |

**Every training example costs the same regardless of clip length.** That is
unusual and it is good: batch composition is trivially predictable, packing is
near-perfect, and there is no length-based load imbalance. It also means **your
training set should be 2-minute segments** — longer clips buy you nothing but
blur.

### 5.3 Which rung to train, for which task

| Task (§1.1) | Rung | Method | Signal | Why |
|---|---|---|---|---|
| Temporal grounding | **6** | GRPO/RLVR with **IoU reward** | Verifiable, $0/step | **TimeLens's recipe.** +9.5 to +14.8 mIoU from the same base [[src](https://arxiv.org/html/2512.14698v1)] |
| Structured extraction | **6**, then 3 | RLVR with schema+field reward; SFT cold-start | Verifiable | Same shape as grounding |
| Moderation / classification | **3** | SFT on teacher labels | Exact match | Cheapest thing that works; go to 4 only if the confusion matrix is skewed |
| Dense captioning | **3 → 4** | SFT on teacher captions, then **SimPO** on judge-ranked pairs | Rubric judge | **Marlin-2B's recipe**, which produced the strongest 2 B captioner claimed |
| Video QA | 3 → 4 | As above | MCQ if convertible | |
| Long summarisation | — | Don't | — | Route to frontier (§1.1) |

**SimPO belongs on doc 00's §3.2 ladder and is not there.** Marlin's card: *"align
Marlin without a reference model, making it cheaper and more stable than DPO at
this scale"* [[src](https://huggingface.co/NemoStation/Marlin-2B)]. ms-swift lists
SimPO alongside DPO/KTO/RM/CPO/ORPO
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/README.md)]. For a
2 B student, not holding a frozen reference model in memory is a material saving,
and it is the method that actually produced the repo's video student. **Add it as
rung 4b.**

**GKD settings if you reach rung 5** (self-hosted teacher, §4.2): `--rlhf_type gkd`,
`--lmbda` (student-data fraction; TRL's authors *"find that on-policy data (high
`lmbda`) performs better"* [[src](https://huggingface.co/docs/trl/en/gkd_trainer)]),
`--beta` (0 = forward KL / mode-covering, 1 = reverse KL / mode-seeking; ms-swift
defaults to 0.5 = JSD), `--sft_alpha` to mix in SFT loss, `--gkd_logits_topk`
**required** against an API teacher, and `--teacher_model_server` pointing at
`swift deploy`
[[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Distillation.md)].
Doc 00 §3.2 argues for reverse KL (mode-seeking) on MiniLLM's grounds — so
**`--beta 1.0`, not the 0.5 default**, for a narrow single-task student.

### 5.4 GPU-hours per 1,000 videos

No published measurement exists. `est.` from the repo's own FLOP accounting
([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.4,
§6.5): per 240-frame, 23,520-token example, **ViT forward = 65.32 TFLOP** and **LM
prefill forward = 102.12 TFLOP**.

Backward ≈ 2× forward. H100 BF16 dense peak 989.5 TFLOPS
([`../gpus/h100.md`](../gpus/h100.md)); at **40 % MFU** for a mixed ViT+hybrid-LM
job `est.` → 396 TFLOP/s effective.

| Configuration | FLOP/example | s/example | **GPU-h / 1K videos** | **$/1K videos @ $3.20/h H100** |
|---|---:|---:|---:|---:|
| **ViT frozen** (`65.32 + 3×102.12`) | 371.7 T | 0.94 | **0.26** | **$0.83** |
| **ViT unfrozen** (`3×167.44`) | 502.3 T | 1.27 | **0.35** | **$1.12** |

Per-epoch cost of Marlin's own 400 K corpus: **104 GPU-h frozen / 141 unfrozen**,
i.e. **$333–$451 per epoch.** Three epochs plus a SimPO stage ≈ **$1,300–$1,800**
of GPU time on a single H100 — consistent with the card's *"Two-stage post-training
on a single H100"* and with ~2–3 weeks wall-clock.

**⚠️ Every caveat on this estimate.** (a) 40 % MFU is assumed, not measured — the
repo has **no throughput measurement of Marlin-2B on any hardware**
([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §10.2).
(b) It ignores optimizer state, activation memory and gradient-checkpointing
recompute (checkpointing adds ~33 % to forward FLOPs). (c) **It ignores video
decode entirely, and that is probably the binding constraint** — see next.

**The decode bottleneck is the real story.** 400 K videos × 240 frames = **96 M
frames per epoch.** vLLM measured that CPU video decoding *"can quickly become a
bottleneck, maxing out CPU cores even with just 2 or 4 GPUs"* at **16 frames** per
request [[src](https://vllm.ai/blog/2026-09-18-pynvvideocodec)]. Marlin's training
path uses 240.

**Mitigation, and it is mandatory, not optional: decode once, cache option-B frame
tarballs (§3.1), and train from those.** 400 K × 7.2 MB = **2.88 TB** — $43/month on
R2 [[src](https://developers.cloudflare.com/r2/pricing/)]. This converts a
per-epoch decode of 96 M frames into a one-time decode plus a JPEG read, and it is
the same artifact S2 already stores. **The trace store and the training cache are
the same object.** That is the main architectural consequence of §3 for §5.

### 5.5 Serving-side parity of the training config

Doc 00's gate-twice rule (§1.2) has a video-specific edge. Things that change the
served output without changing the checkpoint:

| Change | Effect | Source |
|---|---|---|
| **FP8 KV cache** | On B200 it forces the head_dim-256 kernel back to FA2; **neither side is measured** | [`../models/marlin2b/README.md`](../models/marlin2b/README.md) |
| **Decoder backend** | `opencv` (default) vs `torchcodec` vs `pynvvideocodec` vs `deepstream` pick different frames on non-keyframe boundaries ⚠️ | [[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)] |
| **`--block-size`** | On B200/GB300, FA4's hd256 kernel **silently falls back to FA2** without `--block-size 128` | [`../models/marlin2b/README.md`](../models/marlin2b/README.md) |
| **The `--hf-overrides` remap** | Marlin loads in vLLM only via `--hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}'`, and *"the remap that makes it run on vLLM has never been executed by anyone in this repo"* | [`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §8.1 |
| **Preprocessing path A vs B** | Half the frames or half the resolution (§3.2) | [`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.3 |

**Gate the served artifact on a fixed frame-set corpus, not on video files.**
Because option B stores the exact frames, the S7 gate can feed *identical pixel
tensors* to the checkpoint and to the deployed engine and diff the outputs. Any
difference is a serving bug, not a quality question. **This is strictly stronger
than the text version of the gate-twice rule and it is only possible because you
stored frames.**

---

## 6. Evals for video

### 6.1 The eval ladder — push every task down it

This is the central contribution of this document to doc 04.

```
            cheapest, most valid
                    │
  1. VERIFIABLE     │  temporal IoU · schema validity · exact-match label · MCQ accuracy
     $0/item        │  ← grounding, extraction, moderation, converted QA
                    │
  2. TEXT-ONLY      │  AutoDQ: extract events → NLI entailment → P / R / F1
     JUDGE          │  against a FROZEN HUMAN REFERENCE. Judge never sees pixels.
     ~$0.0002/item  │  ← dense captioning
                    │
  3. VIDEO JUDGE    │  frontier multimodal call per item
     ~$0.0145/item  │  ← open QA, long summarisation. 72× regime 2. AVOID.
                    │
  4. HUMAN          │  ~$1.33–$3.33 per 2-min adjudication
     ~92–230× (2)   │  ← judge validation, disagreements, final sign-off only
                    ▼
            dearest, most valid
```

**Decision rule: a video task is MVP-sellable if and only if it can be evaluated in
regime 1 or 2.** Regime 3 makes doc 00 §5.3's sample-size arithmetic
unaffordable — at $0.0145/item, a 5,000-item non-inferiority test costs $72.50 per
arm per run, and you will run it dozens of times across the search in doc 09's
auto-research loop. Regime 1 costs nothing and can therefore be run on every
candidate checkpoint, which is what makes an auto-research loop possible at all.

### 6.2 Concrete protocols

**Temporal grounding.** `R@1 at IoU ∈ {0.3, 0.5, 0.7}` and `mIoU`, the TimeLens
protocol [[src](https://arxiv.org/html/2512.14698v1)]. Report all four; IoU 0.7 is
the one that separates real grounding from lucky overlap. Reference spans come from
human annotation or from the customer's own logs (a user who scrubbed to a
timestamp gave you a label for free — doc 00 §7.2's "disengagement" analogue).

**Dense captioning.** AutoDQ [[src](https://arxiv.org/html/2407.00634v2)]: extract
events from candidate and reference, compute entailment both ways, report
**precision, recall and F1 separately.** Anchor scores against the published
DREAM-1K frontier numbers — **GPT-4o 39.2, Gemini 1.5 Pro 36.2, Tarsier-34B 36.3,
Tarsier2-7B 40.1** — so a customer can see that "40 F1" is a frontier-class number,
not a failing grade. **Augment with temporal IoU per event** (§4.6): an event is a
hit only if it is both entailed *and* within IoU τ of the reference span. Doing this
is what stops a text judge rewarding temporal hallucination.

**Classification / moderation.** Per-class precision/recall and the full confusion
matrix, never aggregate accuracy — doc 00 §3.3's "per-tool accuracy, not aggregate"
applied to labels. Moderation additionally gets I5's non-negotiable safety gate:
**false-negative rate on the harmful class is never traded against anything.**

**Structured extraction.** Schema validity must be **100 %, not 99.5 %** (doc 00
§2.3), then per-field exact/fuzzy match, then a field-level confusion matrix.

**Video QA.** Convert to MCQ — Video-MME's own design, 3 items per video
[[src](https://arxiv.org/html/2405.21075v2)] — and accept the one-off human
authoring cost (§4.5). An MCQ eval is regime 1; the same questions as free-form are
regime 3.

### 6.3 Slices that must exist

Doc 00 §3.3 requires stratified slices rather than aggregate scores. For video:

| Slice axis | Buckets | Why |
|---|---|---|
| **Duration** | <2 min / 2–15 min / >15 min | Video-MME's **82.5 s / 562.7 s / 2,385.5 s** structure [[src](https://arxiv.org/html/2405.21075v2)] (corrected 2026-09-19, §2.6). **This is where the 240-frame cap shows up** and it will show up nowhere else |
| **Effective fps** | ≥2.0 / 1.0–2.0 / <1.0 | Directly separates a model regression from a sampling regression |
| **Motion / shot density** | shots per minute, terciles | DREAM-1K averages 1.9 shots in 8.9 s [[src](https://arxiv.org/html/2407.00634v2)]; fast-cut content is a different problem |
| **Source domain** | the customer's own categories | Video-MME uses 6 domains / 30 subfields |
| **Resolution / bitrate** | native vs downscaled vs heavily compressed | User-generated content is not stock footage |
| **Audio dependence** | tasks answerable without audio vs not | Gemini uses audio by default at 32 tok/s; Marlin's modes are frame-only. **A model that cannot hear will fail an audio-dependent slice for reasons unrelated to vision** |
| **Segment boundary** | items whose answer spans a segment cut | Measures the cost of §3.3's segmentation directly |

### 6.4 The parity protocol, adapted

Doc 00 §5.2–5.4's non-inferiority protocol, with five video amendments:

1. **Randomise on the video, not the request.** The same video re-queried gives
   correlated outcomes; treating those as independent samples inflates power and
   invents significance.
2. **Add a third arm: the incumbent at the student's frame budget.** Run Gemini at
   `fps` matched to the student's `effective_fps`. Three arms — incumbent-default,
   incumbent-frame-matched, student — decompose the gap into *model quality* versus
   *sampling budget*. **This is the direct answer to doc 00 §5.5's open question
   about whether frame-sampled proxy evals are a valid stand-in**, and it is cheap:
   Gemini's `fps` is a documented request parameter
   [[src](https://ai.google.dev/gemini-api/docs/video-understanding)]. **⚠️ It does
   not fully answer it** — it validates the comparison, not the eval's fidelity to
   full-clip human grading, which still needs a human-graded subset.
3. **State the latency SLO per video, bucketed by duration — not per video-minute.**
   Marlin's cost and latency are flat in clip length; Gemini's are linear. A single
   "$/video-minute" or "ms/video-minute" SLO is therefore meaningless across arms.
   The repo has one relevant figure: **0.86 s end-to-end for a 2-minute clip at
   batch 1 on B200** ([`../models/marlin2b/README.md`](../models/marlin2b/README.md))
   — an `estimate`, not a measurement.
4. **Freeze the media config and hash it into the run.** A change to `fps`,
   `VIDEO_MAX_PIXELS` or the decoder backend voids the parity claim exactly as a
   prompt change does (doc 00 §2.2). §3.2's `media_config_hash` is the enforcement.
5. **Shadow mode replays frame hashes, not videos.** vLLM's UUID-keyed multimodal
   cache lets a client *"provide your own stable IDs for each item so caching can
   reuse work across requests without rehashing the raw content"*
   [[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)], which
   makes a shadow arm nearly free on the student side. ⚠️ The same page warns the
   **request fails if the UUID does not match cached content** — so a replica restart
   or an eviction turns a shadow run into a wall of errors unless the client falls
   back to sending the frames. Build that fallback. It is **not** free on the
   incumbent side — every shadow request to Gemini is a full-price video call. Budget
   shadow mode as `incumbent_price × shadow_fraction`, and note that doc 00 §5.6
   ranks shadow-mode diffs as the **single most persuasive artifact** to a buyer.
   Pay for it.

### 6.5 Business-KPI A/B

Doc 00 §5.6 item 1 is non-negotiable: the customer's own traffic and their own
outcome signal. Video outcome signals that arrive for free and should be wired into
the trace schema as first-class fields:

| Signal | Task | Interpretation |
|---|---|---|
| User scrubbed to a different timestamp than the model returned | Grounding | **A labelled correction.** The highest-value row in the store |
| User edited the generated caption | Captioning | Diff = a preference pair, free |
| Moderation decision overturned on appeal | Moderation | A false positive with an adjudicated label |
| Downstream search CTR on the generated description | Captioning / indexing | The only signal that measures whether the caption was *useful* rather than *accurate* |
| Re-upload or re-run of the same `media_id` | Any | Dissatisfaction, unlabelled |

---

## 7. Serving economics

### 7.1 The unit that bills

**For Marlin-2B the billing unit is $/1,000 videos, not $/1M tokens**, because the
240-frame cap makes prefill a constant. From
[`../models/marlin2b/README.md`](../models/marlin2b/README.md) (all `low` tier, 768
output tokens, sustained prefill+decode, `estimate` confidence):

| Rank | GPU | **$/1k videos** | videos/h/GPU | $/GPU-h `est.` | NVDEC engines |
|---:|---|---:|---:|---:|---:|
| 1 | **B200** | **$0.432** | 13,881 | $6.00 | 7 |
| 2 | **H100** | $0.461 | 6,946 | $3.20 | 7 |
| 3 | B300 | $0.487 | 15,193 | $7.40 | ⚠️ none published |
| 4 | H200 | $0.541 | 7,372 | $3.99 | 7 |
| 5 | **RTX PRO 6000 SE** | $0.585 | 3,074 | $1.80 | ⚠️ none published |
| 6 | A100 | $0.696 | 2,285 | $1.59 | 5 |
| 7 | MI355X | $0.863 | 9,968 | $8.60 | rocDecode, no fps figure ⚠️ |
| 8 | GB300 | $1.085 | 16,596 | $18.00 | ⚠️ 7 for GB200; no GB300 row |

Reserved tiers reorder it: **H200 `res1y` $2.79 → $0.378/1k** and **RTX PRO 6000
`res1y` $1.30 → $0.423/1k**, both beating every on-demand row
([`../models/marlin2b/README.md`](../models/marlin2b/README.md)). NVDEC counts from
[[src](https://docs.nvidia.com/dynamo/multimodal/video-decode-gpu-requirements)],
whose table lists exactly **A100 5, H100 7, H200 7, B200 7, GB200 7, L4 4, L40/L40S 3,
RTX 6000 Ada 3** — and **nothing else**.

> **⚠️ Corrected 2026-09-19.** Earlier drafts printed **4 NVDEC for the RTX PRO 6000
> Blackwell** and **7 for GB300**, both cited to that page. Neither GPU appears on it:
> the only "4" in the table is **L4**, and the only Blackwell rows are B200/GB200. The
> RTX PRO 6000's decoder count is therefore **unsourced here** — treat §7.2's
> "4 NVDEC to H100's 7" comparison as an open question, not a measured constraint, and
> confirm against `nvidia-smi`/the product brief before it drives GPU selection.

### 7.2 NVDEC is a first-class capacity dimension for video

vLLM's measurement: **"At 8xH100, GPU-based video decoding provides more than
double the throughput compared to the CPU-based video decoder"**, on 8 single-GPU
vLLM replicas running `Qwen/Qwen3-VL-8B-Instruct`, bf16, `--max-model-len 32768`,
`min_frames:16, max_frames:16`, 100–200 output tokens, with CPU decode *"maxing out
CPU cores even with just 2 or 4 GPUs"*
[[src](https://vllm.ai/blog/2026-09-18-pynvvideocodec)]. The flags:

```
--dtype bfloat16 --max-model-len 32768 --max-num-seqs 1024
--max-num-batched-tokens 32768 --api-server-count 4
--renderer-num-workers 4 --async-scheduling --mm-ipc-gpu-memory-gb 2
--media-io-kwargs '{"video":{"backend":"pynvvideocodec","min_frames":16,"max_frames":16,"hw_decoders":2}}'
```
plus `nvidia-cuda-mps-control -d` before launch.

NVIDIA's own note is that *"decoding adds negligible load to the GPU beyond a small
YUV-to-RGB conversion"* because NVDEC is a fixed-function engine
[[src](https://docs.nvidia.com/dynamo/multimodal/video-decode-gpu-requirements)] —
so this is **free capacity, on a dedicated unit, that the $/1k-videos table does not
price.**

**Three consequences.**

1. **That result is at 16 frames. Marlin runs 240.** The CPU-decode bottleneck is
   15× worse per request. **NVDEC is not an optimisation for this workload, it is a
   prerequisite**, and `--media-io-kwargs` with `backend: pynvvideocodec` should be
   in the default serving config, not a tuning flag.
2. **NVDEC count should enter GPU selection.** RTX PRO 6000 wins on $/GPU-h and on
   break-even volume (§7.4), and the NVDEC question is the one thing that could undo
   that. ⚠️ **But the number is not sourced.** NVIDIA's Dynamo table gives A100 5,
   H100/H200 7, B200/GB200 7, L4 4, L40/L40S 3, RTX 6000 Ada 3
   [[src](https://docs.nvidia.com/dynamo/multimodal/video-decode-gpu-requirements)]
   and **lists no RTX PRO 6000 Blackwell, no B300 and no GB300 row at all** — the "4"
   this document previously attributed to the RTX PRO 6000 was the **L4's** figure
   (corrected 2026-09-19). So: whether *any* count is sufficient for 240-frame requests
   at 3,074 videos/h is unmeasured, **and on three of the repo's own cards the count
   itself is unknown** — a live gap for the primary node.
3. **Under MIG, NVDEC engines are divided and some profiles expose none**
   [[ibid.]]. Any multi-tenant packing plan (doc 01's adapter packing) that assumes
   MIG must check decoder availability, or the tenant gets CPU decode and 2× worse
   throughput with no error.

### 7.3 The $/1M-input figure does not include the vision tower

**A correction the platform must carry into every video cost model.** The repo's
`$/1M input` cells are derived from **LM prefill rates**. For Qwen3.8-27B on B300
that rate is 44,671 tok/s/GPU
([`../models/qwen3827b/b300.md`](../models/qwen3827b/b300.md) §3.1), computed from
LM FLOPs. The vision encoder is a **separate job** and for Marlin it is **39 % of
total prefill compute** (65.32 of 167.44 TFLOP)
([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.4).
For Qwen3.8-27B at the card's recommended video budget it is worse: *"A 224 K-token
video at the recommended setting is a far larger encoder job than anything the LM
does"*
([`../models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md) §6.5).

Marlin's `$/1k videos` **does** price the whole job (it is a sustained
prefill+decode figure). **Qwen3.8-27B's `$/1M in` does not.** So:

| Model | Per-video LM-only cost, `est.` | Honest cost |
|---|---:|---|
| Marlin-2B, B200 `low`, 2-min | — | **$0.000432/video**, all-in ✅ |
| Qwen3.8-27B, B300 `low`, as-shipped (12,288 tok) | $0.000565 | **+ an unpriced ViT job** ⚠️ |
| Qwen3.8-27B, B300 `low`, recommended (229,376 tok) | $0.01055 | **+ a ViT job larger than the LM's** ⚠️ |

**Do not quote a Qwen3.8-27B video price to a customer from the `$/1M in` column.**
⚠️ **TO BE VERIFIED**: the vision-tower cost for Qwen3.8-27B at video budgets. This
needs a `$/1k videos` row for Qwen3.8-27B computed on the same sustained basis as
Marlin's, and the repo does not have one.

### 7.4 Break-even against the frontier

The right question is not "$/token" but **"what monthly volume justifies a dedicated
GPU?"** — because doc 00 §4.5's GPU floor is the binding term for video.

Basis: 2-minute clips, 768 output tokens. Student = Marlin-2B, all-in `$/1k videos`
from §7.1. A dedicated GPU costs `$/h × 730` per month regardless of use.

| Card | $/month (24/7) | Capacity (videos/mo) | vs **Gemini 3.8 Flash** low-res ($0.01188) | vs **Gemini 3.1 Pro** high-res ($0.0788) | vs **TwelveLabs Analyze** ($0.0641) | vs **Rekognition** ($0.200) |
|---|---:|---:|---:|---:|---:|---:|
| **B200** $6.00/h | $4,380 | 10.1 M | **369 K** (3.6 % util.) | 55.6 K (0.55 %) | 68.3 K (0.67 %) | 21.9 K (0.22 %) |
| **H100** $3.20/h | $2,336 | 5.07 M | 197 K (3.9 %) | 29.6 K (0.58 %) | 36.4 K (0.72 %) | 11.7 K (0.23 %) |
| **RTX PRO 6000** $1.80/h | $1,314 | 2.24 M | **111 K** (4.9 %) | **16.7 K** (0.74 %) | 20.5 K (0.91 %) | **6.6 K** (0.29 %) |
| RTX PRO 6000 `res1y` $1.30/h | $949 | 2.24 M | 79.9 K (3.6 %) | 12.0 K (0.54 %) | 14.8 K (0.66 %) | 4.7 K (0.21 %) |

Incumbent per-video prices: Gemini 3.8 Flash low-res `2×6,000×$0.75/1M +
768×$3.75/1M`; Gemini 3.1 Pro high-res `2×17,400×$2.00/1M + 768×$12/1M`;
TwelveLabs `2/60×$1.75 + 768×$7.50/1M`
[[src](https://www.twelvelabs.io/pricing)]; Rekognition `2×$0.10`
[[src](https://aws.amazon.com/rekognition/pricing/)].

**Read the table three ways.**

1. **RTX PRO 6000 is the right card for the closed loop**, not B200. It has the
   lowest break-even volume against every incumbent (3.3× lower than B200) because
   the GPU floor, not throughput, is what a customer under ~1 M videos/month is
   paying for. B200's 13,881 videos/h is capacity almost nobody needs. This
   **reverses** the ranking in §7.1, which is a `$/1k videos` ranking at full
   utilisation, and it is the more decision-relevant ordering for this platform.
2. **Against Rekognition-class specialist APIs the case is overwhelming** —
   break-even at 6,600 videos/month on an RTX PRO 6000. **Moderation and label
   detection are the first video segment to sell into.**
3. **Against a Flash-class LLM API the case is weak below ~100 K videos/month**,
   and it gets weaker: agentic mode at 88 % fewer content tokens would push that
   break-even to ~900 K videos/month ⚠️ (bound, not estimate — thought and tool-use
   tokens are excluded).

**Below the break-even, do not buy a GPU.** Use hourly rental for batch annotation
(50 K videos = 16.3 RTX PRO 6000-hours = $29 at `low`), or a scale-to-zero
serverless platform — the repo's survey of those, with cold-start mechanisms and
per-second billing, is
[`../scaling/12-inference-providers.md`](../scaling/12-inference-providers.md).
Cold start matters more here than for text: a Marlin replica is only 4.426 GB of
weights, so the weight load is fast, but the **first video decode** is not.

### 7.5 Marlin's break-even against a hypothetical API

For completeness, from
[`../models/marlin2b/README.md`](../models/marlin2b/README.md): there is **no vendor
API for Marlin-2B** — HF `inferenceProviderMapping` is `{}`. The parametric
break-even utilisations against per-token APIs (max-throughput, `low` tier) are
H100 **23.7 % / 7.9 % / 2.4 %** against $0.10 / $0.30 / $1.00 per 1M output. i.e.
*"a single rented H100 at $3.20/hr beats a $0.30/1M-output API as soon as you keep
the card busy 7.9 % of the time."* For video that per-token framing is the wrong
unit (§7.1); §7.4's per-video framing is the one to quote.

---

## 8. Worked example: 50,000 videos/month, Gemini-class captioning → Marlin-2B

### 8.1 The setup

| Parameter | Value |
|---|---|
| Volume | **50,000 videos/month** (600 K/year) |
| Mean duration | **3 minutes** |
| Task | Dense captioning: scene paragraph + timestamped events (§1.1 row 4) |
| Output | ~768 tokens/video |
| Incumbent A | **Gemini 3.1 Pro Preview**, high media resolution, 1 fps |
| Incumbent B | **Gemini 3.8 Flash**, high media resolution, 1 fps |
| Student | **Marlin-2B**, segmented into 2 × ≤2-min windows (§3.3), 2 fps, 240 frames/window |
| Serving | RTX PRO 6000 SE, `low` $1.80/GPU-h |
| Eval regime | **2 — AutoDQ text-only judge** against a frozen human reference (§6.1) |

### 8.2 The incumbent bill

Input tokens per video at high resolution: `3 × 17,400 = 52,200`.

| Incumbent | Input $ | Output $ | **$/video** | **$/month** | **$/year** |
|---|---:|---:|---:|---:|---:|
| **A — Gemini 3.1 Pro** ($2.00/$12.00) | $0.10440 | $0.00922 | **$0.1136** | **$5,681** | **$68,175** |
| **B — Gemini 3.8 Flash** ($0.75/$3.75, promo) | $0.03915 | $0.00288 | **$0.0420** | **$2,102** | **$25,218** |
| B after 2027-01-01 ($1.50/$7.50) | $0.07830 | $0.00576 | $0.0841 | $4,203 | $50,436 |
| **B — agentic mode** ⚠️ lower bound | $0.00470 | $0.00288 | $0.0076 | **$379** | **$4,550** |
| C — Gemini 3.8 Flash, **low-res** | $0.01350 | $0.00288 | $0.0164 | $819 | $9,828 |

[[src](https://ai.google.dev/gemini-api/docs/pricing)],
[[src](https://ai.google.dev/gemini-api/docs/video-understanding)].

### 8.3 The student bill

Per video = 2 Marlin calls (two ≤2-min windows). At `$0.585/1k videos` on
RTX PRO 6000 `low` ([`../models/marlin2b/README.md`](../models/marlin2b/README.md)):
**$0.00117/video marginal → $58.50/month.**

But 100 K calls/month against a capacity of 2.24 M/month is **4.5 % utilisation**,
so the marginal cost is not the cost:

| Deployment | Cost/month | Why |
|---|---:|---|
| **Marginal only** (a fiction) | $58.50 | 32.5 GPU-h at $1.80 |
| **1 card, 24/7** | **$1,314** | An always-on `main` endpoint |
| **2 cards, 24/7** (`main` + `dev`, doc 00 I7/doc 01) | **$2,628** | What the closed loop actually requires |
| 2 cards at `res1y` $1.30 | $1,898 | If the commitment is acceptable |
| Hourly-rented, nightly batch | ~$59 | **No interactive SLO.** Viable only if the customer's captioning is asynchronous |

**Take 2 cards at `low` = $2,628/month = $31,536/year** as the honest serving line
for an interactive closed loop.

### 8.4 The loop bill

| Item | Basis | **One-off** | **$/month** |
|---|---|---:|---:|
| Teacher annotation, initial corpus | 100 K 2-min clips, Gemini 3.8 Flash high-res, **batch 50 %**, $0.01449/clip (§4.4) | **$1,449** | — |
| Preference pairs for SimPO | 50 K pairs × 1 judge call $0.0145 (student samples are free on own GPU) | **$725** | — |
| Human review, 1 % | 1,000 clips × $1.33 (2-min, $20/h) (§4.5) | **$1,330** | — |
| **Eval set** | 3,000 human-authored items × $8.50 `est.` (§4.5) | **$25,500** | — |
| Judge validation | 200 human-adjudicated items × $1.33 (§4.6) | **$266** | — |
| Training | 100 K clips × 0.26 GPU-h/1k × 3 epochs + SimPO ≈ 210 H100-h × $3.20 (§5.4) | **$672** | — |
| Shadow A/B, 4 weeks at 5 % | 2,500 videos × $0.1136 (incumbent A) | **$284** | — |
| Frame + trace storage | §3.5 recommended mix, growing | — | **~$28** (month 12) |
| Ongoing re-annotation, 5 % | 2,500 clips/mo × $0.0145 | — | **$36** |
| Ongoing human review, 0.5 % of re-annotated | 125 clips/mo × $1.33 | — | **$166** |
| **Totals** | | **$30,226** | **$230** |

### 8.5 The verdict, which is not uniformly favourable

**Year 1 total cost of the loop:** `$30,226 + 12 × ($2,628 + $230)` = **$64,522.**
**Year 2 onward:** `12 × $2,858` = **$34,296.**

*(Recut 2026-09-19 for the §3.5 storage correction, $78 → $28/month, and the §4.5 eval
item, $8.47 → $8.50. Year-1 falls $511; no verdict in this table changes sign.)*

| Against | Their year-1 | **Our year-1** | Year-1 Δ | Their year-2 | **Our year-2** | Year-2 Δ |
|---|---:|---:|---:|---:|---:|---:|
| **A — Gemini 3.1 Pro** | $68,175 | $64,522 | **−$3,653** (−5 %) | $68,175 | $34,296 | **−$33,879 (−50 %)** |
| **B — Gemini 3.8 Flash** (promo) | $25,218 | $64,522 | **+$39,304 (+156 %)** | $50,436 (post-promo) | $34,296 | **−$16,140 (−32 %)** |
| **B — agentic mode** ⚠️ | $4,550 | $64,522 | **+$59,972** | $9,100 | $34,296 | **+$25,196** |
| **C — Flash low-res** | $9,828 | $64,522 | +$54,694 | $19,656 | $34,296 | +$14,640 |

**Four conclusions, and three of them are uncomfortable.**

1. **At 50 K videos/month the loop is not obviously worth it, and the honest answer
   to the customer is often "tier down first."** Against a Flash-class incumbent it
   loses in year 1 by 156 %. Against agentic mode it loses in both years. This is
   doc 00 §3.2 rung 1 and §4.4's counter-moves, in video, with numbers. **Telling
   the customer this is the credibility purchase**; it is also how you find out
   whether Flash actually passes their eval, which is the only question that
   matters.
2. **The eval set is 40 % of year-1 cost and 84 % of the one-off cost.** Not the
   GPUs, not the teacher tokens, not the training. **The product is the eval.**
   Doc 00 §6's ordering — 02 → 04 → 07 before 03 and 05 — is exactly right for
   video, more so than for text. It also means the eval set is the asset with the
   most defensible margin: it is human-authored, customer-specific, and it is what
   the customer cannot get from Vertex.
3. **The GPU floor, not tokens, is the serving cost.** $2,628/month of GPU serves
   $58.50/month of actual inference — **45× overhead.** The fixes, in order: (a) run
   `dev` on a shared card or serverless; (b) multi-tenant the `main` card across
   customers, which is doc 01's adapter-packing problem and is only safe under I6;
   (c) take `res1y` pricing; (d) if the workload is genuinely asynchronous, use
   hourly rental and drop to ~$59/month, which changes every row above.
4. **Scale fixes everything.** At **500 K videos/month**, incumbent B costs
   `$0.0420 × 500,000 × 12` = **$252,000/year**, while the student needs 1 M
   calls/month = 45 % of one RTX PRO 6000 — call it 2 cards for headroom and `dev`,
   **$31,536/year** plus ~$4,000 of loop. **Saves ~$217,000/year, an 86 % cut.** The
   qualification question is therefore not "is video distillation viable" but
   **"does this customer have ≥ ~150 K videos/month, or an incumbent priced like
   Rekognition rather than like Flash?"**

### 8.6 The plan, in order

| Week | Stage | Action | Gate to proceed |
|---|---|---|---|
| 0 | — | **Qualify**: volume ≥150 K/mo **or** incumbent ≥$0.05/video; task in §1.1 rows 1–4; eval reachable in regime 1–2 | Fails → decline or sell the eval harness alone |
| 1 | S1/S2 | Deploy the gateway; capture option A+B traces (§3.1–3.2); `media_config_hash` from day one | 10 K traces with `effective_fps` and `frame_ts_s` populated |
| 1 | — | **Tier-down test**: run Flash-Lite, Flash, and agentic mode against the incumbent's own prompt on 500 captured traces | If Flash passes the customer's bar, **stop and bank the saving.** This is a win |
| 2–4 | S4 | **Build the eval set first**: 3,000 human-authored items, stratified by §6.3, frozen, perceptual-hash-deduped | Judge validation: AutoDQ vs human agreement measured on 200 items |
| 3–5 | S3 | Teacher-annotate 100 K segmented clips with Gemini 3.8 Flash **batch**, span-tagged schema (§4.3); 1 % targeted human review | Schema validity 100 %; human/teacher agreement measured |
| 5–6 | S5 | ms-swift SFT: `freeze_vit=True`, `freeze_aligner=False`, `packing=True`, `VIDEO_MAX_PIXELS=200704`, `FPS_MAX_FRAMES=240`, `max_length≈25000` | AutoDQ F1 on the held-out test split within margin of incumbent |
| 6–7 | S5 | SimPO on 50 K judge-ranked pairs | F1 improves; **safety/moderation slice does not regress** (I5) |
| 7 | S8 | Serve on RTX PRO 6000 with `pynvvideocodec` + CUDA MPS; **re-run the gate on the served artifact** against the stored frame sets (§5.5) | Output diff vs checkpoint = 0 on the frame corpus |
| 8–11 | S9 | Shadow 5 % of production; publish the disagreement list to the customer (doc 00 §5.6 item 3); demonstrate rollback (I7) before any traffic moves | Non-inferiority at the agreed margin, three-arm (§6.4) |
| 12+ | Loop | Promote `dev`→`main` at 5 % → 25 % → 100 %; re-annotate 5 %/month; monitor `effective_fps` and `media_config_hash` drift | — |

**The two weeks that decide the engagement are 1 and 2–4**, not 5–7. The tier-down
test can end it profitably; the eval set is the expensive irreversible commitment.
Do not annotate before the eval exists.

---

## Implications for the platform

### What to build

1. **The video trace schema of §3.2, with `effective_fps`, `frame_ts_s` and
   `media_config_hash` as required fields.** These three are what make every video
   regression debuggable. No off-the-shelf tracing product has them — ⚠️ the
   OpenTelemetry GenAI conventions doc 00 §6 standardises on have no video
   attributes I could confirm. Extend, don't wait.
2. **Option-B frame storage as the single shared artifact** for traces, training
   cache, eval corpus and serving-gate corpus. 15.6× smaller than the video, bit-exact
   replay, kills the decode bottleneck in §5.4, and strictly reduces the PII surface.
   **This is the highest-leverage architectural decision in this document.**
3. **The eval ladder of §6.1, with the verifiable and text-only regimes built
   first.** Temporal IoU and AutoDQ+IoU cost ~$0 per item against ~$0.0145 for a
   video judge. An auto-research loop is only affordable on a free eval.
4. **Segment-and-batch at ≤2 minutes as the default request policy** (§3.3), with
   the segment boundary recorded in the trace and a boundary slice in the eval.
5. **The three-arm parity protocol** (§6.4 item 2) — incumbent-default,
   incumbent-frame-matched, student. It is the direct answer to doc 00 §5.5's open
   question and it costs one extra Gemini `fps` parameter.
6. **A per-video SLO and cost model bucketed by duration**, never per-video-minute.
   The student's cost is flat in length; the incumbent's is linear. A single unit
   makes both arms wrong.
7. **`--media-io-kwargs` with `backend: pynvvideocodec` and CUDA MPS in the default
   serving config**, not as tuning. Doubling throughput at 16 frames means more at 240.
8. **Qualification gating on §8.6 week 0**, and a tier-down test before any GPU is
   provisioned.

### What to buy

1. **Gemini as the teacher, on the paid tier, via the Batch API.** 50 % off, 24 h
   turnaround, same modalities [[src](https://ai.google.dev/gemini-api/docs/batch-api)],
   and the paid tier is the one where *"Google doesn't use your prompts… or responses
   to improve our products"* [[src](https://ai.google.dev/gemini-api/terms)]. At
   $5,800 for a 400 K-clip corpus, teacher tokens are the cheapest input; do not
   engineer to save them.
2. **ms-swift as the training layer.** The only framework that trains the repo's
   students, supports video GKD, and handles multimodal packing. Build only the
   orchestration around it — the same conclusion doc 00 §6 reached for text.
3. **Human annotation capacity, at ~1 % of traffic and 100 % of the eval set.** The
   expensive, irreplaceable input. Snorkel-class vendors (doc 00 §7.1) are the
   comparable; Roboflow's $0.05–$0.20 per object [[src](https://roboflow.com/pricing)]
   prices the frame-level variety but not clip-level adjudication.
4. **Object storage with free egress.** R2 at $0.015/GB-month
   [[src](https://developers.cloudflare.com/r2/pricing/)]; frames get read many
   times across training, eval and gating, so egress pricing dominates the per-GB
   rate.
5. **Hourly / serverless GPU below the §7.4 break-even.** Do not sell a dedicated
   card to a 50 K-video customer.

### What to avoid

1. **Do not gate on a public video leaderboard.** Video-MME's is topped by a 2024
   model [[src](https://video-mme.github.io/home_page.html)].
2. **Do not build on NeMo microservices.** No multimodal customization, no video
   benchmarks, sunset 2026-10-01
   [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)].
3. **Do not use TRL for video.** `max_length` 1024 and a text-only `messages`
   dataset [[src](https://huggingface.co/docs/trl/en/gkd_trainer)].
4. **Do not assume on-policy distillation is available for video.** No frontier
   video API exposes per-token logprobs on a caller-supplied continuation (§4.1).
   Plan for rungs 3, 4/4b and 6; reach rung 5 only with a self-hosted teacher.
5. **Do not quote a Qwen3.8-27B video price from the `$/1M in` column** — it prices
   the LM and not the vision tower (§7.3).
6. **Do not send customer video through a free-tier Gemini key** — human reviewers
   may read it [[src](https://ai.google.dev/gemini-api/terms)]. And do not send it
   at all until doc 08 has written authorisation against the competing-models clause.
7. **Do not sell rows 5–6 of §1.1 in the MVP.** Long summarisation and open video QA
   need a regime-3 judge whose per-item cost makes doc 00 §5.3's sample sizes
   unaffordable.
8. **Do not train at ms-swift's `VIDEO_MAX_PIXELS` default while serving at
   Marlin's.** 3× mismatch, silent (§5.2).
9. **Do not compare a 10-minute Marlin caption to a 10-minute Gemini caption without
   segmenting.** 0.40 fps against 1.0 fps is not a model comparison.
10. **Do not copy Marlin-2B's published benchmark claims into a customer deck.**
    Every number is in a PNG, the card contradicts itself on which Gemini it matches,
    and the recipe paper is "coming soon" (§2.5).

---

## Open questions

Consolidated ⚠️, ordered by how much they change the plan.

1. **⚠️ Vertex AI video tuning: limits and price.** Google is the only native video
   teacher *and* sells video SFT of it [[src](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/tune-models)].
   The dedicated page returned only navigation on two fetches. Without the limits and
   the price, the competitive position for video is unquantified. **Close first.**
2. **⚠️ Agentic mode's realised bill.** *"Up to 88% more token-efficient"* excludes
   `total_thought_tokens` and `total_tool_use_tokens`, which are billed
   [[src](https://ai.google.dev/gemini-api/docs/video-understanding)]. §8.2's agentic
   row is a lower bound. If the real figure is near 88 %, the distillation case at
   ≤100 K videos/month collapses; if it is 40 %, it survives.
3. **⚠️ Teacher ToS authorisation for Gemini.** The competing-models clause
   [[src](https://ai.google.dev/gemini-api/terms)] covers the core mechanism, and
   unlike text there is no second native-video vendor. Doc 08 owns it. **This can end
   the video product.**
4. **⚠️ The cost of segmentation.** No measurement of accuracy loss from ≤2-min
   windowing versus a single long-context pass, with or without overlap and
   caption-carry-forward (§3.3). Doc 04's first video experiment.
5. **⚠️ Self-hosted video teacher quality.** No head-to-head between the best
   self-hostable video teacher (Qwen3.8-27B, Qwen3.8-2.4T-A95B) and Gemini-3-Flash on
   dense captioning (§4.2). Decides whether annotation runs on a legal risk or on our
   own GPUs.
6. **⚠️ Whether frame-sampled proxy evals stand in for full-clip human grading.**
   Doc 00 §5.5's question. §6.4's three-arm protocol validates the *comparison* but
   not the eval's fidelity; a human-graded subset is still needed. Doc 04 owns it.
7. **⚠️ AutoDQ's correlation with human judgement.** Not reported in the Tarsier
   paper [[src](https://arxiv.org/html/2407.00634v2)]. Must be measured on customer
   data (~200 items, ~$266) before AutoDQ gates a promotion (§4.6).
8. **⚠️ Qwen3.8-27B's true per-video cost.** Needs a `$/1k videos` row on the same
   sustained prefill+decode basis as Marlin's, including the vision tower (§7.3).
9. **⚠️ NVDEC counts and sufficiency on the repo's own cards.** Upgraded
   2026-09-19 from "is 4 enough?" to "**what is the number?**". NVIDIA's Dynamo table
   [[src](https://docs.nvidia.com/dynamo/multimodal/video-decode-gpu-requirements)]
   carries **no row for the RTX PRO 6000 Blackwell, B300 or GB300** — the "4" this
   document previously printed for the RTX PRO 6000 was the **L4's** figure. So the
   decoder count is unknown on the cheapest card (§7.4's recommendation), unknown on
   the primary node, and the only public 240-frame-relevant measurement is vLLM's, at
   **16** frames [[src](https://vllm.ai/blog/2026-09-18-pynvvideocodec)].
10. **⚠️ Marlin-2B has no measured throughput on any hardware**
    ([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §10.2)
    and the `--hf-overrides` vLLM remap *"has never been executed by anyone in this
    repo"*. Every §7 and §8 number inherits `estimate` confidence.
11. **⚠️ torchcodec decode throughput at 240 frames × 448×448** — unmeasured
    everywhere ([ibid.] §10.3), and §5.4 argues it is the binding constraint on
    training.
12. **⚠️ Gemini logprobs.** Not found in the text-generation guide or the
    `generate-content` reference, but the reference truncated before
    `GenerationConfig`. "Not found", not "does not exist" (§4.1).
13. **⚠️ ViT embedding invertibility** for Qwen3.5-class towers — decides whether
    option-D storage is PII (§3.4).
14. **⚠️ Marlin-2B's actual benchmark numbers**, the DREAM-1K/CaReBench/TimeLens
    figures behind the PNG, and which Gemini generation it matches (§2.5).
15. **⚠️ Whether a 2 B student reaches TimeLens-class grounding numbers.** TimeLens
    is 7–8 B [[src](https://arxiv.org/html/2512.14698v1)]; Marlin is 2 B. The §2.2
    ladder makes this a cheap experiment.
16. **⚠️ The Flash promotional price expires 2026-12-31**
    [[src](https://ai.google.dev/gemini-api/docs/pricing)]. Every Flash break-even
    doubles on 2027-01-01 unless extended.
17. **⚠️ Video attributes in the OpenTelemetry GenAI semantic conventions** — none
    confirmed in this session. If they exist, §3.2 should conform rather than invent.
18. **⚠️ The competitor survey is a floor, not a scan.** WebSearch was exhausted;
    every entry was reached by known URL. Video-native closed-loop platforms I could
    not name are missing, not disproven. Encord's pricing page truncated on fetch;
    Voxel51/FiftyOne, Scale, and vertical video-AI vendors were not reached at all.

---

## Sources

All fetched **2026-09-19** unless the page carried its own date.

**Frontier APIs — pricing and capability**
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) — all model rates; Flash promo to 2026-12-31; Veo 3.1; Gemini Embedding 2 video $0.00079/frame
- [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding) — 66/258 tok/frame, 32 tok/s audio, 1 fps default, 3 h/1 h limits, 10 videos/request, agentic mode "up to 88% more token-efficient and ~7% higher quality"
- [Gemini Batch API](https://ai.google.dev/gemini-api/docs/batch-api) — "priced at 50% of the standard interactive API cost", 24 h, same modalities
- [Gemini API terms](https://ai.google.dev/gemini-api/terms) — competing-models clause; paid vs unpaid data use
- [Gemini model tuning](https://ai.google.dev/gemini-api/docs/model-tuning) — "we no longer have a model available which supports fine-tuning in the Gemini API or AI Studio"
- [Vertex AI tune-models](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/tune-models) — SFT modality list includes "Video tuning"
- [Gemini text generation](https://ai.google.dev/gemini-api/docs/text-generation) and [generate-content reference](https://ai.google.dev/api/generate-content) — no logprobs found (both partial fetches)
- [OpenAI pricing](https://developers.openai.com/api/docs/pricing) — GPT-6 Astra / GPT-5.6 family; Sora-2 per-second; **no video-input pricing**
- [OpenAI images & vision](https://developers.openai.com/api/docs/guides/images-vision) — PNG/JPEG/WEBP/non-animated GIF only; 32×32 patches × ~1.2
- [Claude pricing](https://claude.com/pricing) — Fable 5.1, Opus 5, Sonnet 5, Haiku 4.5
- [Claude vision](https://platform.claude.com/docs/en/build-with-claude/vision) — "Animations are unsupported, and only the first frame is used"; 28×28 patches; 100/600 image caps; high-res tier 2576 px / 4784 tokens

**Specialist video APIs**
- [TwelveLabs pricing](https://www.twelvelabs.io/pricing) (page dated 2026-09-17) — Analyze $1.75/h input, $7.50/1M out; indexing $2.50/h; Search $4/1k; Embed video $0.260/1M
- [AWS Rekognition pricing](https://aws.amazon.com/rekognition/pricing/) — video label detection and content moderation $0.10/min; shot detection $0.05/min

**Benchmarks and papers**
- [Video-MME (arXiv 2405.21075)](https://arxiv.org/html/2405.21075v2) — 900 videos / 254 h / 2,700 QA; 300/300/300 at **82.5 s / 562.7 s / 2,385.5 s** (all-splits mean 1,017.9 s; corrected 2026-09-19); Gemini 1.5 Pro 75.0/81.3, GPT-4o 71.9/77.2, VILA-1.5 59.0/59.4
- [Video-MME leaderboard](https://video-mme.github.io/home_page.html) — video-SALMONN 2+ 81.6, Gemini 1.5 Pro 81.3, AdaReTaKe 79.6, JT-VL-Chat 79.1, Qwen2-VL-72B 77.8, GPT-4o 77.2, LLaVA-Video-72B 76.9, **Claude 3.5 Sonnet 62.9**; entries stop at 2025-09-28
- [Tarsier / DREAM-1K (arXiv 2407.00634)](https://arxiv.org/html/2407.00634v2) — 1,000 clips, 5×200 sources, 8.9 s / 6.3 events / 1.9 shots / 59.3 words; AutoDQ = event extraction + NLI; F1 GPT-4o 39.2, Gemini 1.5 Pro 36.2, Tarsier-7B 34.6, Tarsier-34B 36.3, Tarsier2-7B 40.1
- [TimeLens (arXiv 2512.14698)](https://arxiv.org/html/2512.14698v1) — TimeLens-Bench 4,279 videos / 9,404 annotations across three splits; R@1@IoU{0.3,0.5,0.7} + mIoU; TimeLens-8B 55.2/53.2/65.5 vs GPT-5 40.5/42.9/56.8 and Gemini-2.5-Flash 48.6/52.5/64.3; RLVR; TimeLens-100K
- [CaReBench (arXiv 2501.00513)](https://arxiv.org/abs/2501.00513) — 1,000 video/caption pairs, manually separated spatial and temporal annotations, ReBias and CapST metrics

**Models**
- [NemoStation/Marlin-2B model card](https://huggingface.co/NemoStation/Marlin-2B) — Gemini-3-Flash teacher, ~400K annotations, SFT + SimPO on a single H100, targeted human review
- [Qwen/Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md) — native video, 262,144 ctx → 1M, no published video benchmark
- [Qwen3.8-27B video_preprocessor_config.json](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/video_preprocessor_config.json) — `longest_edge` 25,165,824 as shipped
- [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) — Text–Timestamp Alignment; no video benchmark numbers on the card

**Training and serving frameworks**
- [ms-swift README](https://raw.githubusercontent.com/modelscope/ms-swift/main/README.md) — 400+ multimodal models, multimodal packing +100 %, vit/aligner/llm control, multimodal GKD from 2025-06-15, SimPO
- [ms-swift command-line parameters](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Command-line-parameters.md) — `freeze_vit` default True, `freeze_aligner` True, `freeze_llm` False, `vit_lr`, `aligner_lr`, `packing`, `VIDEO_MAX_PIXELS` 768×28×28, `FPS` 2.0, GKD `lmbda`/`sft_alpha`/`gkd_logits_topk`
- [ms-swift Distillation.md](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Distillation.md) — GKD / OPD-RL / OPSD; forward vs reverse KL vs JSD; full-vocab vs top-K vs sampled-token; `--teacher_model_server`
- [ms-swift supported models](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Supported-models-and-datasets.md) — Qwen3.5-0.8B/2B/4B/9B and Qwen3.8-27B tagged `vision, video`; 245 video-tagged entries
- [LLaMA-Factory README](https://raw.githubusercontent.com/hiyouga/LLaMA-Factory/main/README.md) — VLM size table; VL support stops at Qwen3-VL
- [TRL GKDTrainer](https://huggingface.co/docs/trl/en/gkd_trainer) — `trl.experimental.gkd`, `lmbda`/`beta`/`seq_kd`, `max_length` 1024, text `messages` dataset
- [NVIDIA NeMo microservices](https://docs.nvidia.com/nemo/microservices/latest/index.html) — LoRA/SFT/DPO/embedding only; no multimodal or video; sunset 2026-10-01
- [vLLM multimodal inputs](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html) — video backends opencv/torchcodec/pynvvideocodec/deepstream; `--limit-mm-per-prompt`; `--media-io-kwargs` fps/frames_indices/total_num_frames/duration; UUID-keyed multimodal cache
- [vLLM PyNvVideoCodec blog](https://vllm.ai/blog/2026-09-18-pynvvideocodec) — "At 8xH100, GPU-based video decoding provides more than double the throughput"; CPU decode maxes cores at 2–4 GPUs; full flag string
- [NVIDIA Dynamo video-decode GPU requirements](https://docs.nvidia.com/dynamo/multimodal/video-decode-gpu-requirements) — NVDEC counts A100 5, H100/H200 7, B200/GB200 7, L4 4, L40/L40S 3, RTX 6000 Ada 3; **no RTX PRO 6000 Blackwell, B300 or GB300 row**; MIG caveat; *"decoding adds negligible load to the GPU beyond a small YUV-to-RGB conversion"*

**Infrastructure and tooling pricing**
- [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/) — $0.015/GB-mo Standard, $0.010 IA, free egress, Class A $4.50/M, Class B $0.36/M
- [Baseten pricing](https://www.baseten.co/pricing/) — H100 $0.10833/GPU-min, B200 $0.16633; Training Jobs at the same rates; **no video or multimodal model APIs listed**
- [Roboflow pricing](https://roboflow.com/pricing) — managed labeling *"starting at"* $0.10/bounding box, $0.20/polygon, $0.05/classification-or-keypoint; Enterprise add-on only (so these are floors, not quotes)

**Repo documents referenced**
- [`../METHODOLOGY.md`](../METHODOLOGY.md) · [`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md)
- [`../models/marlin2b/README.md`](../models/marlin2b/README.md) · [`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) · [`../models/marlin2b/MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md) · [`../models/marlin2b/b200.md`](../models/marlin2b/b200.md) · [`../models/marlin2b/h100.md`](../models/marlin2b/h100.md) · [`../models/marlin2b/rtx6000-pro.md`](../models/marlin2b/rtx6000-pro.md)
- [`../models/qwen3827b/README.md`](../models/qwen3827b/README.md) · [`../models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md) · [`../models/qwen3827b/b300.md`](../models/qwen3827b/b300.md)
- [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md) · [`../matrix/recommendations.md`](../matrix/recommendations.md) · [`../matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md)
- [`../scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md) · [`../scaling/12-inference-providers.md`](../scaling/12-inference-providers.md)
- [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) · [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)

---

## Verification log (2026-09-19)

Adversarial re-check of this document. 25 load-bearing claims were selected by
consequence — published results, prices, version/limit facts, every derived cost
table, and every cross-reference into `research/`. **No claim was accepted on the
strength of the citation already printed here**; each primary source was re-opened
(`WebFetch`/`curl`), and each repo cross-reference was re-read in the file. All
arithmetic was recomputed in `python3`.

**Result: 25 checked — 18 CONFIRMED, 6 CORRECTED, 1 UNVERIFIABLE.**

### CORRECTED

| # | § | Claim as printed | What the source says | Blast radius |
|---|---|---|---|---|
| 1 | §3.5, §8.4, §8.5 | Recommended storage mix `0.12 + 0.02×15.6 = 0.43` **TB/month** → **$78/mo at month 12, ~$500 year 1** | `0.43` is a *ratio* against the all-traces-B row (360 GB/mo), not a tonnage. The mix is `(0.12 + 0.02×15.625) × 360 GB =` **156 GB/mo** → **$28/mo at month 12, $182 year 1** at R2's confirmed $0.015/GB-mo [[src](https://developers.cloudflare.com/r2/pricing/)] | §8.4 monthly $280 → **$230**; §8.5 year-1 $65,033 → **$64,522**, year-2 $34,896 → **$34,296**; all four Δ columns recut. **No verdict changes sign.** Also killed the "storage is 3.6× the inference cost" line, which additionally compared against §8's *wrong* inference figure ($21.60 = B200 single-call, where §8 serves two RTX PRO 6000 calls = $58.50) |
| 2 | §7.1, §7.2, OQ 9, Sources | **RTX PRO 6000 = 4 NVDEC**, GB300 = 7, cited to NVIDIA Dynamo | That page lists **A100 5, H100 7, H200 7, B200 7, GB200 7, L4 4, L40/L40S 3, RTX 6000 Ada 3 — and nothing else** [[src](https://docs.nvidia.com/dynamo/multimodal/video-decode-gpu-requirements)]. No RTX PRO 6000 Blackwell row, no B300, no GB300. The "4" is **L4's** number | §7.2's "NVDEC count should enter GPU selection" survives as a *question*, not a constraint; open question 9 upgraded from "is 4 enough" to "what is the number". The document's own Sources block already listed the correct table — the body contradicted it |
| 3 | §2.6, §6.3, Sources | Video-MME splits **80.7 / 515.9 / 2,466.7 s** | Table 1 `Avg. V.L.` reads **82.5 / 562.7 / 2,385.5 s**, all-splits mean **1,017.9 s** [[src](https://arxiv.org/html/2405.21075v2)] — re-fetched twice with a targeted prompt to be sure | Medium split 8.6 → **9.4 min**, long 41 → **39.8 min**. The 254-hour total reconciles with *both*, so only the source settles it. Stratification advice unchanged |
| 4 | §2.6 | *"Neither GPT-5, GPT-6 Astra, Gemini 3.x nor Claude appears"* on the Video-MME board | **Claude 3.5 Sonnet is on it at 62.9 % with subtitles**; the board's entries stop at 2025-09-28 [[src](https://video-mme.github.io/home_page.html)] | Strengthens the section's own point (another 2024 model, 18 pts off the top) but the sentence as written was false |
| 5 | §2.1 | Qwen3.8-27B at 240 frames ≈ **229×229 px/frame** | `25,165,824 px ÷ 240 = 104,858 px/frame ≈` **324×324**. 229 comes from the *image* divisor 1,024; video uses **2,048** (`patch² × merge² × temporal`), which [`../models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md) §6.5 derives and Marlin's `48,168,960 ÷ 2,048 = 23,520` reproduces exactly | The "the 27 B model looks at a blurrier video than the 2 B one" conclusion **survives** (324 < 448) but is materially weaker than 229 implied |
| 6 | §1.4 | GPT-family 448×448 frame = **235** tokens | The formula is `⌈patch_count × multiplier⌉`: `⌈196 × 1.2⌉ =` **236** [[src](https://developers.openai.com/api/docs/guides/images-vision)] | $0.141 → $0.1416, $0.0564 → $0.0566/video-min. Ratio column (10.8× / 4.3× / 0.22×) unchanged. Small, but it is a mis-applied formula, not a rounding choice |

Two further corrections were applied that are rounding-of-rounded rather than source
disagreements, and are listed here for completeness rather than counted above:
§4.4's teacher table (`$590 → $594`, `$2,360 → $2,376`, agentic `$0.0092 → $0.00927`),
§4.5's eval item (`$8.47 → $8.50`, so `$25,410 → $25,500` — the printed method says
17 min × $30/h, which is $8.50), §5.4's H100 peak (989.4 → **989.5**, the
METHODOLOGY §8 pin), and §3.4's paid-tier retention (**"30-day"** → the terms' actual
*"a limited period of time"*, which names no number).

### Material additions, no figure changed

- **§5.2 `FPS_MAX_FRAMES`** had no default printed. ms-swift's is **768** — *3.2×*
  Marlin's 240-frame cap, stacking on the already-flagged 3× `VIDEO_MAX_PIXELS`
  mismatch for ~9.6× the pixels per training example at the two defaults together
  [[src](https://raw.githubusercontent.com/modelscope/ms-swift/main/docs/source_en/Instruction/Command-line-parameters.md)].
  This makes §5.2's "real, shipped trap" worse than the section claimed.
- **§2.5** now flags a **second** self-contradiction in the Marlin-2B card that the
  section missed: the training-data section names **Gemini-3-Flash** as teacher and
  judge, the evaluation section says *"its Gemini-2.5-Flash teacher"*. The 0.21/0.43
  quality gap is measured against whichever one it was.
- **§2.5's +6.4 mIoU reading** is downgraded from a single `est.` to two. The card
  says "On TimeLens-Bench (Charades / ActivityNet / QVHighlights) … +6.4 mIoU" without
  naming a split: read as Charades it implies ≈45.7, read as the three-split mean
  ≈40.5. The document previously printed only the flattering reading.
- **§1.4** now records Anthropic's **>20-image stricter dimension rule** (resize to
  ≤2000 px). A 60-frame video-minute is a >20-image request; 1080p clears it, 4K does
  not [[src](https://platform.claude.com/docs/en/build-with-claude/vision)].
- **§6.4 item 5** now records that a UUID cache **miss fails the request**, so shadow
  mode needs a frame-resend fallback
  [[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)].
- **§2.2** now lists the Qwen3.5-27B / 3.6-27B dense rungs and the 35B-A3B / 122B-A10B
  / 397B-A17B / 3.6-35B-A3B MoE rungs, all tagged `vision, video` under the same
  `model_type` and template — the ladder is longer than the section showed.

### CONFIRMED (source re-opened, figure unchanged)

1. **TimeLens** — 48.8/46.2/56.0 (7B), **55.2/53.2/65.5** (8B), GPT-5 40.5/42.9/56.8,
   Gemini-2.5-Flash 48.6/52.5/64.3, Qwen2.5-VL-7B 39.3/31.4/31.6; bench 4,279 videos /
   9,404 annotations, splits 1,313/1,455/1,511 and 3,363/4,500/1,541; R@1@IoU{0.3,0.5,0.7}
   + mIoU; bases Qwen2.5-VL-7B and Qwen3-VL-8B; "thinking-free RLVR"; TimeLens-100K.
   Every number in §0.2, §2.4 and §5.3 is exact [[src](https://arxiv.org/html/2512.14698v1)].
2. **Tarsier / DREAM-1K** — F1 GPT-4o 39.2, Gemini 1.5 Pro 36.2, Tarsier-7B 34.6,
   Tarsier-34B 36.3, Tarsier2-7B 40.1; 1,000 clips, 8.9 s / 6.3 events / 2.2 subjects /
   1.9 shots / 59.3 words; AutoDQ = extraction model + entailment model. **And §4.6's
   ⚠️ is itself confirmed: the paper does not report AutoDQ-vs-human correlation** — its
   human evaluation is a separate side-by-side preference study
   [[src](https://arxiv.org/html/2407.00634v2)].
3. **Gemini video understanding** — 66/258 tok/frame, 32 tok/s audio, 1 fps default and
   `fps`-configurable, 3 h low / 1 h high on 1M models, 10 videos/request (1 pre-2.5),
   the 9-MIME format list, YouTube 8 h/day free, agentic mode on 3.8/3.7/3.6 Flash and
   3.5 Flash-Lite billing thought + tool-use tokens, **88 % / ~7 %**
   [[src](https://ai.google.dev/gemini-api/docs/video-understanding)]. ⚠️ One nuance: the
   page's wording is *"uses up to 88% fewer tokens … approximately 7% higher quality"*;
   this document renders it as *"up to 88% more token-efficient and ~7% higher quality"*.
   Same substance, not the same string — do not put the quoted form in a customer deck.
4. **Gemini pricing** — every rate in §1.2 verified, including the **$0.75/$3.75 promo
   through Dec 31 2026 → $1.50/$7.50 from Jan 1 2027** on 3.6/3.7/3.8 Flash, 3.1 Pro
   Preview $2.00/$4.00 in and $12/$18 out, Veo 3.1 $0.05–$0.60/s, Gemini Embedding 2
   video $12.00/1M at **$0.00079/frame** [[src](https://ai.google.dev/gemini-api/docs/pricing)].
   Both §1.2 token rates reconcile (`66+32≈100`, `258+32≈290`) and all 18 $/video-minute
   cells recompute exactly.
5. **Gemini Batch** — *"priced at 50% of the standard interactive API cost"*, 24 h,
   *"the supported modalities … are the same as … the interactive"*
   [[src](https://ai.google.dev/gemini-api/docs/batch-api)].
6. **Gemini tuning gone from the API/AI Studio** — the Flash-001 May 2025 sentence is
   verbatim [[src](https://ai.google.dev/gemini-api/docs/model-tuning)]; **Vertex SFT
   does list "Video tuning"** among Text/Document/Image/Audio/Video/function-calling
   [[src](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/tune-models)].
   §1.3's entire competitive argument stands.
7. **Gemini terms** — competing-models and reverse-engineering clause verbatim; unpaid
   tier *"human reviewers may read, annotate, and process"*, paid tier *"Google doesn't
   use your prompts … or responses to improve our products"* with limited-period
   violation-detection logs [[src](https://ai.google.dev/gemini-api/terms)]. §3.4's two
   non-negotiable rules stand.
8. **OpenAI / Anthropic accept no video** — OpenAI's format sentence verbatim; Claude
   *"Animations are unsupported, and only the first frame is used"*, `⌈w/28⌉×⌈h/28⌉`,
   high-res tier 2576 px / 4784 tokens on **Claude 4.7 and later**, 100 images per
   request at 200 K context and 600 otherwise, 8000×8000, 10 MB, 32 MB. **1920×1080 =
   2,691 tokens is printed in Anthropic's own table**, not just derived. *"Claude cannot
   be used to name people in images and refuses to do so"* verbatim.
9. **Specialist APIs** — Rekognition label detection and content moderation **$0.10/min**,
   shot detection $0.05/min [[src](https://aws.amazon.com/rekognition/pricing/)];
   TwelveLabs Analyze $1.75/h in + $7.50/1M out, indexing $2.50/h, Search $4/1k, Embed
   video $0.260/1M, page dated 2026-09-17 [[src](https://www.twelvelabs.io/pricing)].
10. **Cloudflare R2** — $0.015 Standard, $0.010 IA, free egress, Class A $4.50/M, Class B
    $0.36/M [[src](https://developers.cloudflare.com/r2/pricing/)]. (IA also carries a
    $0.01/GB retrieval fee the document does not mention; it does not use the IA tier.)
11. **vLLM PyNvVideoCodec** — *"At 8xH100, GPU-based video decoding provides more than
    double the throughput compared to the CPU-based video decoder"*, Qwen3-VL-8B-Instruct,
    `min_frames:16, max_frames:16`, `--max-model-len 32768`, the CPU-cores quote, and the
    full flag string [[src](https://vllm.ai/blog/2026-09-18-pynvvideocodec)]. §7.2's "15×
    worse per request at 240 frames" is arithmetically right.
12. **TRL GKD** — `max_length` default **1024**, `trl.experimental.gkd`,
    `AutoModelForCausalLM` teacher, text `messages` dataset, *"the authors find that
    on-policy data (high `lmbda`) performs better"*, `beta` 0 = forward KL / 1 = reverse
    [[src](https://huggingface.co/docs/trl/en/gkd_trainer)]. "1024 tokens is 4 % of one
    video request" holds.
13. **NeMo microservices** — *"will be sunset on October 1, 2026"*; Customizer LoRA/SFT/
    DPO/embedding only; no multimodal or video anywhere
    [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)].
14. **ms-swift** — packing *"+100%"*, mixed text/image/video/audio, vit/aligner/llm
    control, multimodal **GKD from 2025-06-15**, SimPO in the preference list;
    `freeze_vit` True, `freeze_aligner` True, `freeze_llm` False, `vit_lr`/`aligner_lr`
    default to `learning_rate`, `packing` False, `max_length` None, `truncation_strategy`
    `delete`, `target_modules` `['all-linear']` with tuners *"by default only attached to
    the LLM component"*, `VIDEO_MAX_PIXELS` **768×28×28 = 602,112**, `FPS` 2.0,
    `FPS_MIN_FRAMES` 4, GKD `lmbda` 0.5 / `beta` 0.5 / `sft_alpha` 0 / `gkd_logits_topk`
    None; `--teacher_model_server` and *"GKD: Requires `--gkd_logits_topk`"*; the
    DeepSeek-V4 sampled-token-log-ratio note. **245 `vision, video` entries** counted in
    the live file. Every Qwen3.5/3.8 rung in §2.2 is present and tagged, and
    Qwen3.8-Flash-Next does require `transformers>=5.16.0`.
15. **Qwen3.8-27B preprocessor** — `size.longest_edge` **25,165,824**, `patch_size` 16,
    `temporal_patch_size` 2, `merge_size` 2, `Qwen3VLVideoProcessor`
    [[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/video_preprocessor_config.json)].
    12,288 as shipped and 229,376 at the card's 469,762,048 both recompute.
16. **Marlin-2B card** (HF repo is gated; read from this repo's verbatim copy
    [`../models/marlin2b/MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md)) — *"Tops the
    CaReBench leaderboard"*, *"sits between Tarsier-34B and Gemini-1.5-Pro on DREAM-1K"*,
    *"+6.4 mIoU and matches Gemini-2.0-Flash"* in Key Features vs *"matches
    Gemini-2.5-Flash (non-thinking)"* in Evaluation (**the §2.5 contradiction is real**),
    *"0.21 / 0.43 of 10"*, Gemini-3-Flash thinking-mode teacher, ~400 K annotations,
    two-stage SFT + **SimPO** on a single H100, *"Recipe paper coming soon"*, and the
    span-tagged teacher-prompt quote §4.3 is built on.
17. **Every repo cross-reference in §2.1, §5.4, §7.1 and §7.3** — Marlin 23,520 LM tokens
    / ~23,560 prefill / 94,080 ViT patches / 275.6 MiB KV / 2.00, 0.40, 0.067 effective
    fps / 200,704 px / ViT 65.32 and LM 102.12 TFLOP / **39 %** / 262,144 ctx with
    `rope_type: default` / **4.426 GB BF16 resident**; the full §7.1 ranking $0.432,
    $0.461, $0.487, $0.541, $0.585, $0.696, $0.863, $1.085 with videos/h and both `res1y`
    rows ($0.378, $0.423) and the 0.86 s batch-1 figure; Qwen3.8-27B $0.153/1M out,
    $0.0460/1M in and 44,671 tok/s on B300. *One labelling note:* METHODOLOGY §8 pins
    Marlin at "5.444 GB BF16", which is the **on-disk** checkpoint with `lm_head`
    duplicated; §2.1/§7.4's 4.426 GB is the **resident** figure `architecture.md` §4 calls
    *"the number to plan with"*. Both are correct; neither document says which is which.
18. **Every remaining derivation recomputed in `python3` and unchanged**: §1.2's 18
    $/video-minute cells and the 1.08 M-token context reconciliation; §1.4's 2,691 → 123×
    chain; §3.1's 7.2 MB / 112.5 MB / 96.3 MB and the 15.6× / 13.4× ratios; §3.3's
    `5 × $0.000432 = $0.00216` vs $0.0479 → **22×**; §4.6's 72× and $266; §5.4's 371.7 /
    502.3 TFLOP, 0.26 / 0.35 GPU-h per 1 K, $0.83 / $1.12, 104 / 141 GPU-h per epoch, 96 M
    frames, 2.88 TB → $43/mo; §6.1's $72.50 per arm; **all 16 §7.4 break-even cells and
    their utilisation percentages** (the table correctly treats the dedicated card's
    $/1k-videos as already amortising the GPU, so it does *not* double-count a marginal
    term — verified, since subtracting one moves B200 from 369 K to 383 K); §8.2's five
    incumbent rows including the 12 %-of-content agentic bound; §8.3's $0.00117, $58.50,
    32.5 GPU-h, $1,314 / $2,628 / $1,898; §8.5's 45× overhead and the 500 K-video
    $216 K / 86 % conclusion.

### UNVERIFIABLE

- **Vertex AI video-tuning limits and price** (open question 1). Re-confirmed as still
  open: `tune-models` establishes that video SFT exists but carries **no per-modality
  limits and no pricing**, and the dedicated video page again returned navigation only.
  The document's ⚠️ and its "close this first" ranking are correct and stay.

### Not re-checked this pass

Gemini logprobs (open question 12), OpenTelemetry GenAI video attributes (17), ViT
embedding invertibility (13), the LLaMA-Factory and Qwen3-VL-8B card rows in §2.3, the
CaReBench paper, and Baseten's pricing row in Sources. Their ⚠️ markers are unchanged
and none of them carries a number used in §7 or §8.
