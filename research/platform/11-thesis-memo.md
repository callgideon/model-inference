# Thesis memo — replacing frontier models with distilled specialists

**For:** whoever decides whether to fund this. **Date:** 2026-09-19. Every number links to the document that
sourced it; nothing is derived anew. Loop stages **S1–S9** and invariants **I1–I7** are
[doc 00 §1.2/§1.3](00-goal-and-problem-statement.md).

## 1. The problem

A team runs one product feature on a frontier API — GPT-5.6 Sol, Opus 5, Fable 5.1 — and pays frontier prices
for a task that is, in practice, narrow. They would switch to a smaller specialist tomorrow if someone could
tell them, credibly, that nothing would break.

Nobody can. The blocker is the **evidence**, not the model:
[doc 08 §4.6](08-economics-and-business-case.md) finds that **not one published distillation case study
reports a non-inferiority margin, a confidence level and a power** — so by
[doc 00 §5.2](00-goal-and-problem-statement.md)'s standard, every parity claim in the public record is not a
claim. Restated: **the customer is not buying a smaller model, they are buying permission to switch**
([00 §5](00-goal-and-problem-statement.md)). Everything hard, and everything defensible, is in manufacturing
that permission.

## 2. Why now

- **The method is settled and free.** On-policy distillation has a measured compute case — AIME'24
  **60 % (post-SFT) → 74.4 %** in ~150 steps, **1,800 vs 17,920 GPU-hours** against an RL baseline reaching
  67.6 %, **9–30×** cheaper than SFT ([00 §3.1](00-goal-and-problem-statement.md)) — and every rung of the
  [00 §3.2](00-goal-and-problem-statement.md) ladder ships as an Apache-2.0 TRL trainer:
  `DistillationTrainer`, `GKDTrainer`, `MiniLLMTrainer` and six more
  ([07 §9.1](07-competitor-analysis.md)). **Method risk is zero; infrastructure risk is the whole cost.**
- **Training is not a cost centre.** A real iteration is **$224–$2,708** across four vendors with published
  rate cards; OpenPipe's fully-costed ART•S run was **$15 GPU + $7 judge = $22**
  ([08 §1.4(b)](08-economics-and-business-case.md)).
- **The seam is closing in public.** LangChain trains LangSmith Engine on Baseten Loops; CoreWeave bought W&B
  *and* OpenPipe; Together bought Refuel; Datadog bought Adaptive ML
  ([07 §3.1, §3.3, §3.6, §4.4](07-competitor-analysis.md)). **The loop is being assembled by partnership — the
  barrier to a competitor is a contract, not a build.** The counterweight — seven first-party retirements in
  one day's survey ([07 §5.5](07-competitor-analysis.md)) — is §8, item 1.

## 3. Does distillation-to-specialist work?

**Where it works — the strongest sourced results:**

| Result | Number | Source |
|---|---|---|
| **UniversalNER** — student *beats* teacher on extraction | **+7–9 absolute F1** over ChatGPT across **43 datasets / 9 domains** | [08 §4.5](08-economics-and-business-case.md) |
| **Distilling Step-by-Step** — 700× fewer params, *less* data | **770M T5 > 540B few-shot PaLM** on **80 %** of the data | [08 §4.5](08-economics-and-business-case.md) |
| **NVIDIA data flywheel** — production tool-calling | **Llama 3.2 1B at ≈98 % of Llama 3.1 70B**, inference cost **−98.6 %** | [00 §3.1](00-goal-and-problem-statement.md), [07 §3.5](07-competitor-analysis.md) |
| **ART•S** — cleanest published end-to-end economics | Qwen 2.5 14B **43 % → 85 %**, past **Sonnet 4's 72 %**, for **$22** | [08 §1.4(b), §4.1](08-economics-and-business-case.md) |
| **Axis** — a named production customer | **>95 %** per-token saving vs GPT-4-Turbo, *"the next cheapest model that met their high quality bar"* | [08 §4.1](08-economics-and-business-case.md) |
| **Databricks TAO** — a scorer, no labelled data | beat fine-tuning on **4,800–8,137 labelled examples**, three benchmarks | [07 §3.4](07-competitor-analysis.md) |
| Orca · phi-1 · Zephyr · STaR · MiniLLM · RLAIF | the method literature, unanimous on *narrow* tasks | [00 §3.1](00-goal-and-problem-statement.md) |

**Where it does not work, or is unproven:**

- **The scoping caveats are load-bearing.** NVIDIA scopes its own 98 % result to *"simpler tool calling use
  cases where an agent is using a tool call to route between a small set of tools"*
  ([00 §3.1](00-goal-and-problem-statement.md)), and the specialist trade-off is a published finding, not a
  slogan: *"by paying the price of decreased generic ability"*
  ([08 §4.5](08-economics-and-business-case.md)).
- **Agents and multi-turn.** Per-turn 0.98 over 10 turns is **0.82** at session level
  ([00 §5.4](00-goal-and-problem-statement.md)); τ-bench's best agents *"succeed on <50 % of the tasks"*,
  **pass^8 <25 % in retail** ([04 §1.3](04-evals-and-ab-testing.md)). Coding is worse — the strongest signal
  there is that a routing company sells **routing**, not distillation
  ([08 §5.2](08-economics-and-business-case.md)).
- **Video understanding: no published parity result exists**, and a full market scan did not move it
  ([00 §3.4](00-goal-and-problem-statement.md), [07 §13.2](07-competitor-analysis.md)). The video case is a
  cost-structure argument only ([08 §5](08-economics-and-business-case.md)).
- **Publication bias is total.** No case study reports a **failed** distillation, and none reports a **second
  loop iteration beating the first** ([08 §4.6](08-economics-and-business-case.md)) — the latter being the
  only thing separating a platform from a consultancy ([00 §9.1](00-goal-and-problem-statement.md)).

**Verdict.** "A small specialist can match a large generalist *on one task*" is as well supported as applied
ML gets; "it happens automatically, on an arbitrary customer's task, without an expert in the loop" is not
supported at all ([00 §3.1](00-goal-and-problem-statement.md)). **That gap is the product.**

## 4. The crux: confidence

**The risk dwarfs the saving.** A 0.5 % regression on a 2M-request/month support chat at $12 per escalated
ticket costs **$120,000/month** — **18.5×** the **$6,477/month** saved by distilling off a batched, cached
GPT-5.6 Sol; **37×** at a 1 % regression rate, and **46×** on the rare-high-severity row
([08 §0, §3.1](08-economics-and-business-case.md)). **The platform is insurance with a rebate, not a
discount.** Four traps stand between a candidate checkpoint and a defensible claim:

| Trap | The number | The build answer |
|---|---|---|
| **Judge validity** — strong judges reach *"over 80 % agreement, the same level of agreement between humans"*, with position, verbosity and self-enhancement bias ([00 §5.1](00-goal-and-problem-statement.md), [04 §3.1](04-evals-and-ab-testing.md)) | At 80 % agreement a true 3 pp gap **reads as 1.8 pp**; sample sizes inflate **2.78×** ([04 §3.1](04-evals-and-ab-testing.md)) | Judge ≠ teacher, asserted in the artifact schema; a **100-item judge canary** on every run; the **noise floor as a shaded band** on every chart ([04 §3.2, §3.4, §7.4](04-evals-and-ab-testing.md)) |
| **Clustering** — turns, trials, QA-pairs-per-clip are not independent | Clustered SEs *"can be over three times as large as naive standard errors"* ([04 §2.3](04-evals-and-ab-testing.md)) | Mandatory `cluster_id`; a scorer that **refuses to emit a CI without it** |
| **Peeking** — a live significance indicator *is* a 50-look test | 50 looks turns a 5 % test into a **32.4 %** test ([04 §2.5](04-evals-and-ab-testing.md)) | Fixed horizon offline; **asymptotic confidence sequence** online, N\* pre-registered ([04 §2.5](04-evals-and-ab-testing.md)) |
| **Rare tails** — aggregate A/B never finds 1-in-10,000 | 7 days of shadow ≈ 460k requests → **95 % upper bound 6.5e-6** (rule of three) ([04 §2.7, §8.1](04-evals-and-ab-testing.md)) | **Shadow-first by default**: rare-failure detection becomes a *coverage* problem, and coverage is purchasable with compute |

**And confidence is cheap, which is the counter-intuitive result.** The offline protocol at δ=2 pp across
five slices costs **$473** of judge tokens, **$1,892** at δ=1 pp
([08 §1.4(c)](08-economics-and-business-case.md)); for the worked support-chat profile the *entire* evidence
package — gold set, calibration, gate-twice, canary, 30 days of shadow, online judging — is **≈$1,500 list /
≈$1,020 batch** ex-SME against a **$47,600/month** incumbent bill, i.e. **under one day of their inference
spend** ([04 §8.1](04-evals-and-ab-testing.md)). The costly parts are human review and engineering, not
tokens. Two artifacts sell harder than the statistics and should ship first: the **disagreement explorer**
("here are the 214 requests where the two models differed, and here is who was right") and a **rollback drill
demonstrated live, under 60 s** ([00 §5.6](00-goal-and-problem-statement.md),
[04 §7.4](04-evals-and-ab-testing.md)).

## 5. Competitive landscape

Scoring is [07 §2](07-competitor-analysis.md)'s: **●** first-class, **◐** partial, **○** absent, against S1–S9.

| Who | Covers | Misses | Read |
|---|---|---|---|
| **Distil Labs** — nearest full-loop competitor | S1 ● S5 ● S7 ● **S8 ●**; *"route 1 % of production traffic"*, *"one day of traffic"*, quantise in-pipeline | **S9 ◐** — approves *offline*, then scales to 100 %; no judge validation; no pricing | Their pilot ask is a better **sales mechanic** than ours; steal it ([07 §3.2, §16](07-competitor-analysis.md)) |
| **W&B + CoreWeave** (ex-OpenPipe) | S1–S6 ●; Serverless RL, multiplexing claimed *"40 % lower cost"* | **S8 ○, S9 ○** | The closest historical analogue was **acquired and its standalone surface switched off** ([07 §3.1](07-competitor-analysis.md)) |
| **LangSmith + Baseten** | everything except **S9 ○** | S9 | The loop assembling by **partnership**, not acquisition ([07 §3.3](07-competitor-analysis.md)) |
| **Databricks** (Agent Bricks + TAO) | S1, S4–S7 ●; auto prompt-optimisation, auto-inferred eval criteria | **S8 ○, S9 ○**; *recommends* ≥100 inputs and describes **no gate at all** | Sells **optimisation**, not **confidence** ([07 §3.4, §14.1](07-competitor-analysis.md)) |
| **NVIDIA NeMo Platform** | S1 ●, S4 ●, S5 ●, **S8 ●** | **S9 ○** | Same box diagram shipped **three times in 18 months** — the architecture is not the moat ([07 §3.5](07-competitor-analysis.md)) |
| **Adaptive ML → Datadog** | **the only S9 ●**; AT&T *"51 % win rate vs GPT-4o"* | S8 ○; names A/B with no design, power calculation or stopping rule | Forward-deployed engineers **sold as a line item** ([07 §3.6, §14.1](07-competitor-analysis.md)) |
| **AWS Bedrock Distillation** | **S3 ●** — trains on production invocation logs, `requestMetadata` filtering | S7/S8/S9 ○; **15k prompt-response-pair ceiling**; **Anthropic teachers withdrawn, *"no confirmed timeline"*** | The empirical answer on teacher ToS ([07 §3.7](07-competitor-analysis.md)) |
| **Azure AI Foundry** | S1, S2, S4, S5 ●; stored completions → Distill/Evaluate | **Retires 2026-10-15**; training files **non-exportable** | Lock-in to point at in a sales call ([07 §3.8](07-competitor-analysis.md)) |
| **Eval layer** — Langfuse, Braintrust, Arize, Galileo, Confident AI | S2 ●, S4 ● | S5/S6/**S9 ○** | Commodity: Langfuse **self-hosts for $0 licence**, trace pricing spreads **~100×** ([07 §15.4](07-competitor-analysis.md)). Steal Galileo's **distilled judge** (*"96 % lower cost"*, scores 100 % of traffic) ([07 §6.5](07-competitor-analysis.md)) |
| **Routing** — Not Diamond, LiteLLM, Portkey | S1 ●, S2 ● | everything else | *"20 % + cost savings"* for **zero engineering and zero risk** is the benchmark every distillation pitch is measured against ([07 §8.1](07-competitor-analysis.md)) |

**Two findings set the strategy** ([07 §12](07-competitor-analysis.md)): **no product scores ● on S2 *and*
S5 *and* S9** — one ● in the whole S9 column, and it describes no method; and **gate-twice, evaluating the
quantised artifact that actually serves, is not a product feature anywhere**
([07 §14.2](07-competitor-analysis.md)) — while this repo already has the per-GPU format analysis to do it.

## 6. Unit economics — three customer profiles

All from [08 §7](08-economics-and-business-case.md), `est.` throughout. "Honest floor" = the incumbent
**batched, cached, or tier-downed** — the price a competent buyer quotes back at us
([08 §1.2](08-economics-and-business-case.md)).

| | **A — support chat** 2M req/mo, on GPT-5.6 Sol | **B — extraction** 50M req/mo, mid-tier | **C — video captioning** 50K clips/mo |
|---|---|---|---|
| Incumbent, list (h=50 %) | **$33,600**/mo | **$172,500**/mo (Terra) | **$43,862**/mo (Twelve Labs, 30-min clips) |
| Incumbent, honest floor | **$11,040** (Sol, batch + 90 % cached) | **$59,250** (Terra) · **$19,406** (Gemini 3.8 Flash promo, batched) | **$4,492** (Gemini batch, 30-min) |
| Distilled deployment | 3 × RTX PRO 6000 (2 prod + 1 dev) | 2 × B200 + 1 dev | 2 × RTX PRO 6000 |
| Delivered cost | **$4,563** (Langfuse Cloud) · $4,092 self-hosted | **$22,641** (Cloud) · **$13,740** self-hosted | **$2,828** |
| Payback vs list | $29,037/mo → **1.4 mo** @ $40k one-off | $149,859/mo → **0.5 mo** @ $80k | $41,021/mo vs Twelve Labs → **2.0 mo** @ $80k |
| Payback vs honest floor | **$6,477**/mo → **6.2 mo** @ $40k, **12.4 mo** @ $80k | **$36,609**/mo → **2.2 mo** @ $80k | **$1,651**/mo vs Gemini batch → **48 mo** |
| Where it dies | Haiku 4.5 or Luna batched+cached → **never** | Gemini batched promo → **never** on Cloud, **14 mo** self-hosted; `gpt-5-nano` → **never** | 5-minute clips → **loses to Gemini at every price** |
| Utilisation reality | a B300 would run at **9.3 %** ($0.65/1M delivered); RTX PRO 6000 at **53 %** is the right class | B200 and RTX PRO 6000 cost the **same** ($13,140) — prefer 3 machines to 10 | **0.20–0.91 %** utilisation: unsellable single-tenant |

Five facts that discipline the business:

1. **The honest multiple is single- to low-double-digit, not 138×.** Against the cheapest tier, batched and
   90 % cached, self-hosted Qwen3.8-27B at $0.0602/1M is **0.9×–11.6×** — and **`gpt-5-nano` at $0.0536/1M is
   already below our best self-hosted cell** ([08 §0, §1.2](08-economics-and-business-case.md)).
2. **Cost of goods is idle GPU, not tokens** — delivered $/1M is blended ÷ utilisation
   ([08 §1.1, §1.3](08-economics-and-business-case.md)). Multi-tenant adapter serving is therefore **the
   business model, not an optimisation**: the only mechanism turning N customers' 9 % utilisations into one
   70 % ([00 §4.5](00-goal-and-problem-statement.md)).
3. **Engineering is 85–95 % of the one-off** (~$45k / $67k / $151k low-typical-high); annotation, training and
   judge tokens are noise ([08 §1.4](08-economics-and-business-case.md)). The platform's job is to amortise it
   — **customer 2 in a vertical must cost a fraction of customer 1.**
4. **The observability vendor can eat the whole saving.** Same traces at 50M req/mo: **$9,501** Langfuse
   Cloud, **$8,649** Braintrust, **$33,698** W&B Weave ([08 §1.5](08-economics-and-business-case.md)) — in
   profile B, self-hosting is the difference between "never" and a 14-month payback.
5. **Qualify hard and be willing to say no.** Below roughly **$10,100/month** of honest incumbent spend the
   arithmetic cannot clear a typical one-off in 12 months ($15,000 is the judgement call with headroom,
   [08 §1.6](08-economics-and-business-case.md)); and for profile C as literally specified — 5-minute clips on
   Gemini Flash — **the honest answer is that the customer should stay on Gemini**
   ([08 §7.3](08-economics-and-business-case.md)).

Pricing follows: margin cannot come from tokens, training or traces, so the structure is **an engagement fee
for iteration 1 + a per-task subscription pricing the confidence protocol + inference at cost-plus**, with
shadow and teacher tokens itemised and a **warranty** in place of outcome pricing ([08 §6.2](08-economics-and-business-case.md)).

## 7. What the MVP is

**One customer, one text task, one student, one GPU class** ([00 §9.1](00-goal-and-problem-statement.md)) —
and, per the market scan, **with no training at all in v1**: ingest someone else's traces → validated eval and
validated judge → **online A/B with a defensible claim**. Training is what we sell second
([07 §18](07-competitor-analysis.md)). Build, in order:

1. **S9 — A/B and rollout.** The one empty column in the market ([07 §14.1](07-competitor-analysis.md)), and
   the stage that manufactures the permission the customer is actually buying.
2. **Shadow-first, with the disagreement explorer** and rule-of-three reporting
   ([04 §2.7, §7.4](04-evals-and-ab-testing.md)).
3. **Judge validation as a priced, auditable deliverable** — ≥200 adjudicated examples, published κ, judge ≠
   teacher, canary set — then **distil the judge** so it scores 100 % of traffic
   ([04 §3.3](04-evals-and-ab-testing.md), [07 §6.5, §14.4](07-competitor-analysis.md)).
4. **Gate-twice** on the quantised serving artifact, enforced in the data model
   ([00 §1.2](00-goal-and-problem-statement.md), [07 §14.2](07-competitor-analysis.md)).
5. **Prompt-stack inside the versioned artifact**, prompt-hash on every trace, and a contractual rule that a
   prompt change voids the parity claim ([00 §2.2](00-goal-and-problem-statement.md),
   [07 §14.3](07-competitor-analysis.md)).
6. **The frontier fallback route, in the MVP, with `f` a monitored SLI** — f = 20 % still delivers a **4.6×**
   saving and buys the customer's permission ([08 §3.3](08-economics-and-business-case.md)).

Buy, don't build: traces (**self-hosted Langfuse**, $0 licence), capture (a **LiteLLM/Portkey callback** at
0.66 ms p99 — not our own gateway), methods (**TRL**), prompt optimisation (**DSPy**), expert data
(Snorkel/Scale) ([07 §14.7](07-competitor-analysis.md)). Non-goals: the auto-research loop, multi-tenant
packing, video, agentic parity claims, frontier-teacher distillation by default, and any promise of a specific
cost multiple ([00 §9.2](00-goal-and-problem-statement.md)). **And one criterion decides platform vs
consultancy: a second iteration, trained from post-deployment traffic, beating the first on the frozen test
set** ([00 §9.1 criterion 10](00-goal-and-problem-statement.md)).

## 8. The biggest unknowns

Ordered by how much they move the decision; all carry ⚠️ in their sources.

1. **⚠️ Seven vendor retirements in one survey** — OpenAI FT/RFT/distillation/evals, Azure stored completions,
   Mistral FT, NVIDIA's blueprint and NeMo microservices, plus OpenPipe, Humanloop, Predibase and Refuel
   absorbed ([07 §5.5, §17.5](07-competitor-analysis.md)). The evidence says distillation works, so the
   pattern is that **tooling-only businesses here do not survive standalone**; survivors are infrastructure
   that also does this, or services with engineers attached. **If customers will not pay for the loop, the
   pricing model is wrong** ([08 OQ9](08-economics-and-business-case.md)). Customer research, not web research.
2. **⚠️ OpenPipe's revenue at acquisition** — *"the single most decision-relevant unknown"* in the scan
   ([07 §3.1, OQ4](07-competitor-analysis.md)). $6.7M seed and ~$7M of claimed *customer* savings suggests
   small. It decides whether the loop is a business or a feature of infrastructure.
3. **⚠️ Has any second loop iteration ever beaten the first?** No published precedent
   ([08 §4.6, OQ11](08-economics-and-business-case.md)). The compounding story and §7's decisive MVP criterion
   both rest on it.
4. **⚠️ Teacher terms of service.** Anthropic prohibits distillation *"without prior authorization"*, Google
   flatly; **OpenAI's clause could not be read (HTTP 403)** ([00 §8.1](00-goal-and-problem-statement.md)). AWS
   shipped Claude distillation and **withdrew it with no restoration timeline**
   ([07 §3.7](07-competitor-analysis.md)). Design open-weights-teacher-first — the cheap answer and the safe
   answer coincide, since Kimi-K3 self-hosted labels 50k examples for **$406**
   ([08 §1.4(a)](08-economics-and-business-case.md)).
5. **⚠️ Do batch discounts and prompt caching compose on OpenAI and Anthropic?** Google is settled (they
   stack); the other two publish no batch × cached cell. If they do **not**, every incumbent floor in §6 rises
   20–40 % and every payback improves correspondingly ([08 OQ1](08-economics-and-business-case.md)).
6. **⚠️ Two unsourced cost inputs.** What a bad outcome is worth per segment — §4's whole insurance framing
   rests on assumed dollar values, no public benchmark reached
   ([08 §3.1, OQ2](08-economics-and-business-case.md)) — and the fully-loaded engineering cost of iteration 1
   vs iteration N, the largest line item and the only one with no source
   ([08 §1.4(d), OQ5](08-economics-and-business-case.md)).
7. **⚠️ Video, three times over.** No published distillation-at-parity on any video task
   ([00 §3.4](00-goal-and-problem-statement.md), [07 §13.2](07-competitor-analysis.md)); whether a
   frame-sampled proxy eval stands in for full-clip human grading is **unmeasured everywhere**
   ([04 §1.5.4, OQ4](04-evals-and-ab-testing.md)); and every video cost figure is a GPU-inference cost that
   may be dominated by an **unmeasured video-decode cost** — *"a two-day measurement that could invalidate a
   whole segment"* ([08 OQ4](08-economics-and-business-case.md)). **Sequence video second, and run that
   measurement before anything else.**
8. **⚠️ The market scan is search-limited.** Docs 00, 04, 07 and 08 were all produced without WebSearch; §5 is
   a floor on the landscape, not a scan of it ([07 §1.2, OQ1](07-competitor-analysis.md)). One search-enabled
   pass closes it in an hour and should precede any fundraise slide.

## In one paragraph

The technical bet is well supported on narrow tasks and unproven on agents and video. The economic bet is real
but far smaller than the headline — **single- to low-double-digit against an honest incumbent price, with idle
GPU, not tokens, as the cost of goods.** The defensible bet is neither: it is that **nobody sells a defensible
online non-inferiority test**, that a regression costs **18–46× the saving**, and that manufacturing the
confidence costs **under one day of the customer's inference spend**. Build S9 and the evidence package first,
buy everything below it, qualify ruthlessly above ~$10k/month of honest incumbent spend, and treat the seven
vendor retirements as this thesis's live counterargument rather than background noise.
