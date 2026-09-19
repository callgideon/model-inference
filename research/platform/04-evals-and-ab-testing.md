# Evaluation, statistical confidence and A/B testing for model replacement

Research date **2026-09-19**. This is document **04** in the decomposition set out in
[`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) §6. Doc 00
assigns this document three jobs: own the **S4 datasets + evals** and **S7 offline
gate** stages of the loop, own the question of whether frame-sampled proxy evals are
valid for video (§5.5 of doc 00), and supply the statistical machinery that doc 07
(A/B + rollout) executes. Where doc 00 states a rule — judge ≠ teacher, gate twice,
parity is a non-inferiority claim — this document does not re-argue it; it makes it
operational.

**Conventions.** Legend, cost formulas and the `low`/`high`/`res1y` price tiers are
[`research/METHODOLOGY.md`](../METHODOLOGY.md). Self-hosting $/1M figures are named
rows from [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md); serving,
gateway, routing and traffic-splitting mechanics are
[`research/scaling/02-serving-stack-and-routing.md`](../scaling/02-serving-stack-and-routing.md)
and [`research/scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md);
incumbent and student prices are doc 00 §2.1 and §4.2.

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source, fetched 2026-09-19. |
| **⚠️ TO BE VERIFIED** | No primary source found, or the claim is an inference; reasoning stated inline. |
| `est.` | Arithmetic from stated inputs; shown, not measured. |
| `meas.` | A published measurement, cited. |

> **Research-method caveat, stated up front.** As with doc 00, this session's
> **WebSearch budget was exhausted (200/200) before this agent started**. Every
> source below was reached by **WebFetch against a known URL** or by `curl` against
> a raw GitHub README. The consequence is the same as doc 00's: **§5's tool survey
> is a survey of tools I could name and fetch, not an exhaustive market scan.**
> Anything that exists but that I could not name is missing, not disproven. No
> number below is recalled from memory; if I could not fetch it, it is marked ⚠️.

---

## 1. Eval taxonomy for this loop

### 1.0 The organising principle

Most eval taxonomies are organised by *technique* (exact match, judge, human). That
is the wrong axis for this platform. The right axis is **what decision the eval
authorises**, because every eval in the loop exists to unblock exactly one gate:

| Tier | Runs when | Latency budget | Cost budget | Authorises |
|---|---|---|---|---|
| **T0 Contract** | Every build, every checkpoint, every serving artifact | seconds | ~$0 | Nothing on its own; a hard blocker (doc 00 I4) |
| **T1 Smoke** | Every training step milestone | < 5 min | < $1 | "Keep training" |
| **T2 Offline gate** | S7, twice (checkpoint + served artifact) | < 2 h | $100–$2k | "Put it on the dev endpoint" |
| **T3 Shadow** | Continuously, 100 % of prod traffic | hours | compute only | "Start a canary" |
| **T4 Online A/B** | S9, on live traffic | days–weeks | risk, not $ | "Promote to main" |
| **T5 Post-deployment monitor** | Forever | continuous | storage + judges | "Keep it on main" / "trigger re-loop" |

**The rule that makes this coherent: an eval that cannot change a decision does not
get built.** The most common failure in eval programmes is a dashboard of thirty
metrics none of which has a threshold attached.

### 1.1 Offline evaluation

#### 1.1.1 Golden sets and task metrics

The base unit is a **frozen, versioned test split** drawn from the customer's own
traffic (§6), scored by the cheapest valid method available for that task. Ordered
by preference — and the ordering *is* the recommendation:

1. **Programmatic / verifiable.** Exact match, numeric tolerance, JSON-schema
   validity, unit tests, SQL execution equivalence, tool-call AST equivalence. Zero
   marginal cost, zero drift, no judge to validate. The Berkeley Function Calling
   Leaderboard is the canonical worked example of doing tool-calling this way:
   "the **first comprehensive and executable function call evaluation**", scoring via
   AST comparison and actual execution across simple/parallel/multiple/multi-turn
   categories [[src](https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/README.md)].
   For code, EvalPlus is the same discipline applied to test adequacy —
   "HumanEval+: 80x more tests than the original HumanEval" and "MBPP+: 35x more
   tests" [[src](https://raw.githubusercontent.com/evalplus/evalplus/master/README.md)],
   and BigCodeBench extends it to compositional tool use: 1,140 tasks, 139 libraries,
   "an average of 5.6 test cases and 99% branch coverage", where LLMs reach "scores
   up to 60%, significantly lower than the human performance of 97%"
   [[src](https://arxiv.org/abs/2406.15877)].
2. **Outcome signal from production.** Ticket resolved without escalation, code
   merged, refund not issued, session not retried. Doc 00 §5.1 already establishes
   the reason to hunt for this first: the RealHumanEval result that "programmer
   preferences do not correlate with their actual performance"
   [[src](https://arxiv.org/abs/2404.02806)] means even *human preference* is not a
   valid proxy for task outcome. An outcome signal beats any judge and costs nothing
   to produce — it only costs *time* (§4.5, KPI lag).
3. **Reference-based grading.** A gold answer exists; a judge scores the candidate
   against it rather than in the abstract. Cheaper and far more stable than
   reference-free grading, because the judge's job collapses from "is this good?" to
   "does this match?" — Inspect ships this distinction directly as `model_graded_qa()`
   ("Have another model assess whether the output is a correct answer, based on
   grading guidance in `target`") versus `model_graded_fact()`
   [[src](https://inspect.aisi.org.uk/scorers.html)].
4. **Rubric grading (reference-free).** A judge scores against a written rubric. Use
   when no gold answer exists. Requires validation (§3).
5. **Pairwise preference against the incumbent.** The last resort, and the one
   customers ask for first. Most expensive statistically (§2.4) and most vulnerable to
   bias (§3.2).

#### 1.1.2 Pairwise vs incumbent — what it is actually for

Pairwise-vs-incumbent is *not* the parity measurement. It is the **communication
artifact**. Doc 00 §5.6 ranks "shadow-mode diffs with the disagreements listed and
clickable" above the statistical result in what actually closes a sale. Pairwise
comparison is how you generate that list.

Two mechanical requirements, both non-optional:

- **Run both orders and average.** MT-Bench names position bias first among the
  LLM-judge limitations it examines — "position, verbosity, and self-enhancement
  biases, as well as limited reasoning ability" (three biases plus a capability limit,
  not "four biases" as this document previously said — corrected 2026-09-19)
  [[src](https://arxiv.org/abs/2306.05685)]; not swapping is
  negligence, and it doubles judge cost (§8 budgets assume 2× throughout).
- **Control for length.** Length-Controlled AlpacaEval's regression-based debiasing
  raised Spearman correlation with Chatbot Arena "from 0.94 to 0.98" and improved
  "robustness of the metric to manipulations in model verbosity"
  [[src](https://arxiv.org/abs/2404.04475)]. A student distilled from a verbose
  teacher will be *rewarded* for verbosity by a verbose-biased judge (doc 00 §3.3
  "style over substance"). **Report length-controlled win rate as the headline and
  raw win rate as a secondary**, with mean output-token counts for both arms next to
  it — the token count is also a cost input, so it is free to collect.

#### 1.1.3 Regression suites

A regression suite is **append-only and never sampled**. Every production incident
becomes a permanent test case (doc 00 §8.5). The distinguishing property versus the
golden set: the golden set is a *sample* designed to estimate a population quantity,
and can be refreshed; the regression suite is a *census* of known failures and can
only grow. Mixing them destroys both — the golden set stops being representative and
the regression suite stops being exhaustive. **Keep two files.**

#### 1.1.4 Safety and format/tool-call validity

Both are **gates, not metrics** (doc 00 I4, I5). The operational distinction:

| | Quality metrics | Gates |
|---|---|---|
| Aggregation | Averaged, CI-bounded | Counted; any failure is a failure |
| Trade-off | Tradeable against cost/latency | Never tradeable |
| Sample size logic | §2 power analysis | §2.7 rare-failure logic (rule of three) |
| Threshold | Negotiated margin δ | 100 %, or a pre-agreed hard count |

Structured-output validity is the clearest case. Doc 00 §2.3 states the requirement
as "Schema-valid 100 % of the time, not 99.5 %". A 99.5 % rate at 2M requests/month
is **10,000 malformed responses a month**. That is not a quality regression; it is an
outage spread thinly enough to be invisible. Measure it as a *rate with an upper
confidence bound*, not as a percentage that rounds to 100 (§2.7).

### 1.2 Online evaluation

| Mode | Mechanism | User risk | What it measures | What it cannot measure |
|---|---|---|---|---|
| **Shadow** | Candidate runs on a copy of every production request; output discarded | **Zero** | Full-distribution disagreement, latency, cost, crash rate, schema validity | Anything requiring a user response; downstream KPI |
| **Canary** | Tiny fixed share (0.5–2 %) to candidate, usually internal users or one segment | Low, bounded | Does it break in production at all | Statistical parity (underpowered by design) |
| **A/B (% split)** | Randomised share, sticky by user/session | Real | Business KPI, user feedback, guardrails | Rare tails (§2.7) |
| **Interleaving** | Both models' outputs shown/mixed within one session | Real, but each user sees both | Within-user preference, very high sensitivity | Applies only where the UI can show two outputs (§4.4) |

**Shadow is the default first mode and does most of the work.** Doc 00 §5.4 gives the
reason: it is the only mode that gets 100 % distribution coverage at zero user risk,
which is exactly what rare-failure detection needs. Its cost is one extra inference
per request — at the repo's student prices that is `est.` **$543/month for 2M
requests** on a self-hosted Qwen3.8-27B (§8.1), i.e. noise next to the GPU floor.

Shadow's limitation is structural and must be stated to the customer: **it produces
no preference or outcome data**, only disagreements. The pipeline is therefore
shadow → sample the disagreements → judge/adjudicate them → *that* is the offline
eval set for the next round (§6.3). Shadow and annotation are the same pipe.

### 1.3 Agentic and multi-turn

Doc 00 §5.4 states the arithmetic: per-turn accuracy 0.98 over 10 turns is 0.82 at
session level if errors are independent, and worse if they compound. The published
benchmark literature confirms this is not theoretical:

- **τ-bench** measures agents against a simulated user with domain tools and policy
  documents, and introduces **pass^k** — "the reliability of agent behavior over
  multiple trials". Result: state-of-the-art function-calling agents "succeed on <50%
  of the tasks", and "pass^8 <25% in retail" [[src](https://arxiv.org/abs/2406.12045)].
  **pass^k is the metric this platform should adopt for any agentic task**, because
  it is exactly the customer's question: not "can it do this" but "will it do this
  every time".
- **τ²-bench** extends to a *dual-control* telecom domain modelled as a Dec-POMDP
  "where both agent and user make use of tools to act in a shared, dynamic
  environment", with "significant performance drops when agents shift from no-user to
  dual-control" [[src](https://arxiv.org/abs/2506.07982)].
- **BFCL v3+** adds multi-turn and multi-step function-call evaluation, and v4 adds
  agentic web search, memory management and **format sensitivity**
  [[src](https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/README.md)].
  Format sensitivity is directly load-bearing here: a distilled student's tool-call
  formatting is one of the first things to drift (doc 00 §2.3).

**Trajectory eval design rules for the platform:**

1. **Mock the tool layer deterministically.** Doc 00 §5.4 already says this; the
   mechanism is a recorded tool-response fixture keyed by `(tool_name, canonicalised_args)`
   captured from S2 traces. Without it the eval measures the flakiness of the
   customer's API behind tool #4.
2. **Score at three levels and report all three**: per-step tool correctness
   (programmatic), trajectory validity (did it reach a terminal state without a
   policy violation), and final-outcome success. DeepEval ships this decomposition as
   named metrics — Task Completion, Tool Correctness, Goal Accuracy, Step Efficiency,
   Plan Adherence, Argument Correctness
   [[src](https://raw.githubusercontent.com/confident-ai/deepeval/main/README.md)] —
   which is a useful vocabulary even if you do not use the library.
3. **Run k ≥ 5 trials per task and report pass^k**, not pass@1. This multiplies eval
   cost by k and is the main reason agentic gates are expensive.
4. **Cluster standard errors on the task, not the turn.** See §2.3 — this is the
   single most common statistical error in agentic evals.

### 1.4 Long context

Relevant here because the customer's prompt stack is often 1–20k tokens (doc 00 §2.1)
and because the student's effective context is usually *smaller* than the incumbent's.

**RULER** is the reference result: 13 tasks across vanilla NIAH, NIAH variations,
multi-hop tracing and aggregation, and the finding that "while these models all claim
context sizes of 32K tokens or greater, only half of them can maintain satisfactory
performance at the length of 32K", despite "nearly perfect accuracy in the vanilla
NIAH test" [[src](https://arxiv.org/abs/2404.06654)].

**Decision rule for the platform:** measure the student's *effective* context on the
customer's own prompt-length distribution, not its advertised one. Concretely — take
the p50/p90/p99 prompt lengths from S2 traces, build a RULER-style retrieval +
aggregation probe at each of those three lengths using the customer's own documents,
and gate on p99. A student that passes at p50 and fails at p99 fails "only in
production", which doc 00 §2.3 lists as one of the seven quiet contract breaks.

### 1.5 Video understanding

This is the section doc 00 §5.5 explicitly assigns to this document, including the
open question of whether frame-sampled proxy evals are valid.

#### 1.5.1 What the public benchmarks actually measure

| Benchmark | Scale | What it measures | Key result |
|---|---|---|---|
| **Video-MME** | 900 videos / 254 h / 2,700 QA pairs; 11 s – 1 h; 6 domains, 30 subfields; frames + subtitles + audio; "rigorous manual labeling by expert annotators" | Full-spectrum video QA, short/medium/long | "Gemini 1.5 Pro is the best-performing commercial model, significantly outperforming the open-source models" [[src](https://arxiv.org/abs/2405.21075)] |
| **LongVideoBench** | 3,763 videos w/ subtitles, 6,678 human-annotated MCQs, 17 categories, up to 1 h | **Referring reasoning** — a referring query points at a context, the model must retrieve and reason there | "model performance on the benchmark improves only when they are capable of processing more frames" [[src](https://arxiv.org/abs/2407.15754)] |
| **MVBench** | 20 video tasks built by converting public video annotations to MCQ | Temporal tasks "that cannot be effectively solved with a single frame" | "existing MLLMs are far from satisfactory in temporal understanding" [[src](https://arxiv.org/abs/2311.17005)] |
| **TempCompass** | Conflicting videos sharing static content, differing in one temporal aspect (speed, direction) | Isolates temporal perception from static-frame shortcuts | "these models exhibit notably poor temporal perception ability" [[src](https://arxiv.org/abs/2403.00476)] |
| **VDC / VDCSCORE** (AuroraCap) | >1,000 structured detailed captions | Detailed captioning, evaluated by "transform[ing] long caption evaluation into multiple short question-answer pairs" | "better correlates with human judgments of video detailed captioning quality" (Elo-validated) [[src](https://arxiv.org/abs/2410.03051)] |
| **Charades-STA** (TALL) | Temporal sentence annotations added to Charades | Temporal activity localisation by natural-language query | Introduced the task; **metric definitions (R@n, IoU) are in the full paper, not the abstract I fetched — ⚠️ see Open Questions** [[src](https://arxiv.org/abs/1705.02101)] |

⚠️ **"TimeLens" is unresolved.** The task brief names "Video-MME/TimeLens-style" video
benchmarks. I could not, without WebSearch, identify a video-*understanding*
evaluation called TimeLens; the name I can place is TimeLens, an **event-camera video
frame-interpolation** method, which is a generation/restoration technique and not an
eval. The four temporal-understanding benchmarks above (LongVideoBench, MVBench,
TempCompass, Charades-STA) are what I can actually source for temporal grounding and
temporal reasoning. **Doc 04 owes a search-enabled recheck of this name.**

#### 1.5.2 Temporal grounding metrics

For "find the span in the video that matches this description", the standard family is
**R@n, IoU=m** (recall of the top-n predicted spans at temporal-IoU threshold m,
typically m ∈ {0.3, 0.5, 0.7}) and **mIoU** (mean temporal IoU of the top-1 span). The
task brief names mIoU specifically, and it is the right primary metric for a
*platform* because it is continuous — R@1,IoU=0.7 is a thresholded binary and
therefore discards most of the signal a non-inferiority test needs. ⚠️ **TO BE
VERIFIED**: the exact metric definitions above are the field's convention as I
understand them, but the abstract I fetched for TALL/Charades-STA
[[src](https://arxiv.org/abs/1705.02101)] does not state them, so I am not citing a
source for the formulas. Get them from the full paper before implementing.

**Statistical note that matters more than the metric choice:** mIoU is a bounded
continuous metric, so a non-inferiority test on it uses the continuous-outcome sample
size (§2.2), not the two-proportion formula — and because temporal IoU distributions
are heavily bimodal (near-0 for misses, near-0.8 for hits), the normal approximation
is poor. **Use a paired bootstrap (§2.3) on mIoU, not a t-test.**

#### 1.5.3 Captioning judges, and what they cost

Two architectures, with an order-of-magnitude cost difference (`est.`, GPT-6 Astra at
$10/$50 per 1M [[src](https://developers.openai.com/api/docs/pricing)], Marlin-2B's
240-frame budget = 23,560 prefill tokens per request per
[`models/marlin2b/README.md`](../models/marlin2b/README.md) §9):

| Judge architecture | Inputs | `est.` cost/item | Notes |
|---|---|---:|---|
| **Frontier multimodal re-watch** | 240 frames (23,560 tok) + candidate caption + rubric; 300 out | **$0.2576** (list) / **$0.1288** (batch) | Judge sees the video. Also: this is roughly the *same* price as the incumbent's own inference on that video (`est.` $0.2506) — i.e. **judging costs as much as serving** |
| **Reference-based text judge (VDCSCORE-style)** | Candidate caption + reference structured caption + rubric (~1,600 tok in, 400 out) | **$0.0360** | 7× cheaper. Requires a human-authored reference caption per item — a one-time cost amortised across every future round |

**The decision rule this produces is unusually clean:** for video, **pay humans once
for reference captions and then judge on text forever.** A 1,000-video gold set at 15
SME-minutes per video is `est.` **250 SME-hours**; Video-MME's own construction ratio
(2,700 QA pairs over 900 videos, expert-annotated
[[src](https://arxiv.org/abs/2405.21075)]) is ~3 authored items per video, which is a
reasonable planning anchor. After that, a full 3,942-item non-inferiority eval costs
`est.` **$142** of judge tokens instead of **$1,015** (3,942 × $0.0360 = $141.9;
3,942 × $0.2576 = $1,015.4 — both one order, recomputed 2026-09-19. Note this is the
**unpaired, un-inflated** n from §2.2; §8.2's video example sizes the same eval at
1,910 paired judge-inflated items × 2 orders = $138, a different basis. The "(§8.2)"
cross-reference previously attached here was wrong — $142 does not appear in §8.2).

#### 1.5.4 The frame-sampled proxy question (doc 00 §5.5)

**Question as posed:** is grading on the same 240-frame budget the model sees a valid
stand-in for full-clip human grading?

**Answer, as far as the evidence supports one:** it is valid for *comparing two
models that share the frame budget*, and invalid for *estimating absolute task
quality*. The reasoning, with sources:

- LongVideoBench's central finding is that "model performance on the benchmark
  improves only when they are capable of processing more frames"
  [[src](https://arxiv.org/abs/2407.15754)]. Frame budget is therefore a **first-order
  determinant of score**, not a nuisance parameter. An absolute score measured at 240
  frames does not transfer to a different budget.
- TempCompass demonstrates the failure direction: models can answer from static
  content alone unless the eval is deliberately constructed with "conflicting videos
  that share the same static content but differ in a specific temporal aspect"
  [[src](https://arxiv.org/abs/2403.00476)]. A frame-sampled eval on ordinary clips
  will therefore *overstate* temporal understanding, for both arms.
- Because the bias is shared, a **paired comparison at a fixed frame budget is still
  a valid non-inferiority test of the difference** — the bias cancels in the paired
  difference. This is the standard argument for paired designs and it holds here.

**The exception that breaks it, and must be tested for:** if the incumbent is a
frontier model that samples *more* frames than the student's cap, the arms do not
share the budget and the bias does not cancel. **Decision rule: if the incumbent's
frame budget differs from the student's, either (a) force the incumbent to the same
budget for the eval — the honest apples-to-apples comparison — or (b) accept that you
are measuring the pipeline, not the model, and say so on the dashboard.** Option (a)
is the correct default, and (b) is what the customer actually cares about, so **run
both and report both.** ⚠️ Still unverified: whether a 240-frame proxy correlates with
full-clip *human* judgement at a usable level on any real task. No published
correlation exists that I could fetch. **This is a measurement the platform must make
itself on its first video customer, and it is a prerequisite to any video parity
claim.**

### 1.6 Latency and cost as first-class metrics

Doc 00 I2 and I3 make these invariants, which means they belong in the same table as
quality, with the same CI treatment — not in a separate ops dashboard.

| Metric | How to report | Gate |
|---|---|---|
| TTFT p50 / p99 | At the customer's measured concurrency, from the load test at S8 and confirmed online | ≤ incumbent |
| TPOT p50 / p99 | Same | ≤ incumbent |
| End-to-end p99 | Including gateway overhead (doc 00 MVP criterion 1: ≤ 5 ms added) | ≤ incumbent |
| Output tokens/request, mean | Directly, per arm | Reported next to win rate (§1.1.2) — it is both a cost driver and the verbosity confound |
| Delivered $/1M blended | METHODOLOGY §6 formula, including idle GPU and amortised build | < customer's **batched, cached, tier-downed** incumbent price (doc 00 §4.4) |

lmms-eval v0.7 (Feb 2026) ships "efficiency metrics (per-sample token counts,
run-level throughput)" as part of the harness
[[src](https://raw.githubusercontent.com/EvolvingLMMs-Lab/lmms-eval/main/README.md)],
which is the correct pattern: **collect cost/latency in the eval run itself**, not
from a separate system, so they land in the same versioned artifact as the scores.

---

## 2. The statistics of parity

### 2.1 What claim is being made

Doc 00 §5.2 fixes the form: *the upper bound of the one-sided 95 % CI on
(incumbent − candidate) is below δ, on the frozen test set, on every named slice, with
the safety eval passing unconditionally.* Four numbers agreed **in writing before
training starts**: metric, margin δ, confidence 1−α, power 1−β.

This is the non-inferiority design from clinical trials. FDA's 2016 guidance frames
the problem as "when NI studies intended to demonstrate effectiveness ... can provide
interpretable results, how to choose the NI margin, and how to test the NI hypothesis"
[[src](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/non-inferiority-clinical-trials)]
— ⚠️ the landing page only; I could not fetch the guidance PDF itself, so the specific
margin-selection methodology, assay-sensitivity and biocreep discussions are **not**
sourced here. Two borrowed concepts are worth naming anyway because they have exact
analogues in this platform, and both are my inference, not sourced:

- **Assay sensitivity.** A non-inferiority test is only meaningful if the eval could
  have detected a difference had one existed. An eval on which every model scores 0.97
  proves nothing. ⚠️ **The platform must therefore ship a sensitivity check: include a
  deliberately-weaker reference arm** (e.g. the base student before fine-tuning, or a
  tier-down model like GPT-5.6 Luna) **and confirm the eval separates it from the
  incumbent.** If it does not, the eval is not measuring the task. This is cheap and I
  have found no vendor that does it.
- **Biocreep.** If round N's candidate is declared non-inferior to the incumbent at
  δ=2 pp, and round N+1's candidate is declared non-inferior to *round N's candidate*
  at δ=2 pp, quality has drifted 4 pp with every step individually passing. **Rule:
  every parity claim is always against the original incumbent's frozen outputs, never
  against the previous student.** This is a storage requirement — keep the incumbent's
  responses on the frozen test set forever — and it is cheap.

### 2.2 Sample sizes: unpaired two-proportion non-inferiority

`n ≈ (z_{1−α} + z_{1−β})² · 2p(1−p) / δ²` per arm (`est.`, normal approximation,
one-sided α = 0.05). Doc 00 §5.3 gives the 80 %-power rows; the 95 %-power column is
new here because the task brief asks for it.

| Baseline p | δ | n/arm @ 80 % power | n/arm @ **95 % power** |
|---:|---:|---:|---:|
| 0.70 | 5 pp | 1,039 | 1,819 |
| 0.70 | 3 pp | 2,886 | 5,051 |
| 0.70 | 2 pp | 6,492 | 11,364 |
| 0.70 | 1 pp | 25,967 | 45,454 |
| 0.85 | 5 pp | 631 | 1,104 |
| 0.85 | 3 pp | 1,752 | 3,067 |
| 0.85 | 2 pp | 3,942 | 6,900 |
| 0.85 | 1 pp | 15,766 | 27,597 |
| 0.95 | 5 pp | 235 | 412 |
| 0.95 | 3 pp | 653 | 1,143 |
| 0.95 | 2 pp | 1,469 | 2,571 |
| 0.95 | 1 pp | 5,874 | 10,282 |

Read this table as a **price list for confidence**. Going from 80 % to 95 % power
costs 75 % more data; going from δ=2 pp to δ=1 pp costs 4×. The commercially useful
framing for a buyer on day one is doc 00's: "how sure do you want to be, and what will
you pay for it?"

For a **continuous** metric (mIoU, rubric score on 1–5, latency), substitute
`n ≈ (z_{1−α} + z_{1−β})² · 2σ² / δ²` and estimate σ from a pilot of ~100 items. Do
not guess σ; for bimodal metrics like IoU it is much larger than intuition suggests
(§1.5.2).

### 2.3 Paired designs — the free 2–3× discount, and the clustering trap

**Offline, always pair.** Both models see the same inputs, so the comparison is of
correlated proportions and the relevant variance is the *discordance* rate, not
`2p(1−p)`. Sample size for a paired (McNemar-style) non-inferiority test,
`n ≈ (z_{1−α} + z_{1−β})² · p_disc / δ²` (`est.`, one-sided α=0.05, 80 % power):

| Discordance rate | δ=5 pp | δ=3 pp | δ=2 pp | δ=1 pp |
|---:|---:|---:|---:|---:|
| 0.05 | 124 | 344 | 773 | 3,092 |
| 0.10 | 248 | 687 | 1,546 | 6,183 |
| 0.20 | 495 | 1,374 | 3,092 | 12,366 |
| 0.30 | 742 | 2,061 | 4,637 | 18,548 |

Compare to §2.2: at p=0.85, δ=2 pp, the unpaired design needs 3,942 per arm (7,884
gradings); a paired design at 10 % discordance needs **1,546 pairs (3,092 gradings)** —
a 2.5× saving. **A well-distilled student should have low discordance with its teacher
by construction, which makes the paired design get cheaper exactly as the model gets
better.** That is a rare and welcome alignment of incentives.

The generic version of the same effect, from the best available primary source on
LLM-eval statistics: paired-difference analysis exploits the shared question list, and
frontier models show correlations "between 0.3 and 0.7" on question scores, providing
substantial variance reduction
[[src](https://www.anthropic.com/research/statistical-approach-to-model-evals),
[paper](https://arxiv.org/abs/2411.00640)]. At ρ=0.5 the paired variance is half the
unpaired; at ρ=0.7 it is 30 %.

**The clustering trap.** The same source is emphatic and it is the most commonly
violated rule in this field: for evals with related question groups, cluster standard
errors on the randomisation unit, because "clustered standard errors on popular evals
can be over three times as large as naive standard errors"
[[src](https://www.anthropic.com/research/statistical-approach-to-model-evals)].

For this platform the clusters are obvious once named, and they are everywhere:

| Situation | Naive unit (wrong) | Correct cluster |
|---|---|---|
| Multi-turn session evaluated per turn | turn | **session** |
| Agentic task run k times for pass^k | trial | **task** |
| RAG eval with several questions per document | question | **document** |
| Video eval with several QA pairs per clip | QA pair | **clip** |
| Multiple prompts from the same end user | request | **user** |

Getting this wrong makes every CI ~3× too narrow, which turns a null result into a
"win". **This is the single highest-value statistical rule in the document**, and it
should be enforced in the harness — the eval dataset schema carries a mandatory
`cluster_id` column and the scorer refuses to compute a CI without it.

**Bootstrap, not formulas, for anything non-binary.** The practical recipe: resample
*clusters* with replacement (not rows), recompute the paired difference each time,
take the 5th percentile as the one-sided lower bound, 10,000 resamples. This handles
bimodal metrics (mIoU), rubric scores, ratios, and any composite score, with no
distributional assumption. lmms-eval's v0.6 release line claims "statistically
grounded results (CI, paired t-test)", and the project's standing design principles
list "Confidence intervals, clustered standard errors, paired comparisons"
[[src](https://raw.githubusercontent.com/EvolvingLMMs-Lab/lmms-eval/main/README.md)],
which is the first multimodal harness I can source doing this correctly. ⚠️
**Corrected 2026-09-19**: the clustered-SE line is a README *design-principle* bullet,
not a v0.7 changelog entry as this document previously said, and **no source here
verifies that the shipped code computes clustered standard errors** — read the
release notes before relying on it for a gate. Inspect
reports `stderr` alongside `accuracy` on most scorers
[[src](https://inspect.aisi.org.uk/scorers.html)], which is a floor, not the full
treatment. For test selection generally, the Dror et al. appendix proposes "valid
statistical tests for the common tasks and evaluation measures" in NLP
[[src](https://arxiv.org/abs/1809.01448)].

### 2.4 Pairwise-preference power — the number the task brief asks for

**How many judged items for a 2-point win-rate difference at 95 % power?**

Two-sided α=0.05, testing a 52 % win rate against the 50 % null (`est.`):

| Effect | Power | n (judged pairs) |
|---|---:|---:|
| 52 % vs 50 % | **95 %** | **8,116** |
| 52 % vs 50 % | 80 % | 4,904 |
| 53 % vs 50 % | 95 % | 3,604 |
| 55 % vs 50 % | 95 % | 1,294 |
| 55 % vs 50 % | 80 % | 783 |
| 60 % vs 50 % | 95 % | 319 |

With **both orders run** (§1.1.2), 8,116 pairs = **16,232 judge calls**.

**Ties make it worse.** Most pairwise rubrics allow a tie, and ties carry no
information in the binomial test, so the item count inflates by 1/(1−tie rate):

| Tie rate | Total items needed for 52 % vs 50 % @ 95 % power |
|---:|---:|
| 20 % | 10,145 |
| 40 % | 13,527 |
| 60 % | **20,290** |

And a distilled student vs its teacher will have a **high** tie rate — that is what
success looks like. **This is the trap in pairwise testing for this product: the
better the distillation works, the more expensive the pairwise test becomes.**

**The fix is to stop asking the wrong question.** Non-inferiority phrasing — "is the
win rate at least 48 %?" — is one-sided and needs `est.` **3,865 pairs at 80 % power**
or **6,764 at 95 %**, roughly half the two-sided superiority test. And a paired
*rubric* comparison (§2.3) at 10 % discordance needs 1,546. **Recommendation: use
pairwise preference for the demo artifact and the disagreement list; use paired
rubric/programmatic scoring for the gate.**

### 2.5 Sequential testing and the cost of peeking

Continuous monitoring of a fixed-horizon test destroys its α. Simulated (`est.`,
4,000 trials, equal-sized looks, two-sided 0.05 nominal). ⚠️ An independent
re-simulation on 2026-09-19 (4,000 trials, n=4,000, equal looks, z-test at each look)
reproduced 5.1 / **8.6** / **15.3** / **18.6** / 32.5 % against the printed
5.1 / 8.0 / 14.1 / 19.4 / 32.4 % — same magnitudes and the same conclusion, but the
intermediate rows differ by more than Monte-Carlo noise alone, so the exact
per-row figures depend on unstated simulation details (look spacing, minimum n per
look). **Treat the table as an order-of-magnitude illustration, not a calibration.**

| Number of looks | Realised type-I error |
|---:|---:|
| 1 | 5.1 % |
| 2 | 8.0 % |
| 5 | 14.1 % |
| 10 | 19.4 % |
| 50 | 32.4 % |

At 50 looks — i.e. a dashboard someone refreshes daily for two months — **one in three
null experiments produces a "significant" win.** Any platform that shows a live
significance indicator and lets a human decide when to stop *is* running a 50-look
test, whatever the label says.

Three valid responses:

1. **Fixed horizon, pre-registered, no peeking for decisions.** Look all you like at
   guardrails (§4.3); do not look at the primary metric's p-value. Cheapest and most
   robust. Requires discipline the platform must enforce in the UI — **hide the
   primary p-value until the pre-registered horizon**, show only the guardrails.
2. **Always-valid p-values / mSPRT.** Johari, Pekelis & Walsh open from the failure
   mode — fixed-horizon inferences are "wholly unreliable if users endogenously choose
   samples sizes by *continuously monitoring* their tests" — and define *always valid*
   p-values and CIs that "let users try to take advantage of data as fast as it becomes
   available, providing valid statistical inference whenever they make their decision",
   functioning as "a natural interface for a sequential hypothesis test"; the method
   "has been implemented in a large scale commercial A/B testing platform to analyze
   hundreds of thousands of experiments to date"
   [[src](https://arxiv.org/abs/1512.04922)]. The paper also uses always-valid p-values
   "to obtain multiple hypothesis testing control in the sequential context" — directly
   relevant to §2.6. (**Corrected 2026-09-19**: the sentence previously attributed to
   this paper the quotation *"remain statistically valid regardless of when a user stops
   their test or how many times they peek at interim results"*, which does not appear in
   the abstract; the wording above is the abstract's.)
3. **Confidence sequences.** Howard et al. define a confidence sequence as "a sequence
   of confidence intervals that is uniformly valid over an unbounded time horizon",
   with shrinking widths, nonasymptotic coverage and nonparametric conditions
   [[src](https://arxiv.org/abs/1810.08240)]. GrowthBook implements "Asymptotic
   Confidence Sequences introduced by Waudby-Smith et al. (2023)" with a tuning
   parameter N\* — "the sample size you expect to get when you are most likely to make
   a decision", defaulting to 5,000 — and states plainly that "sequential
   analysis results in uniformly wider confidence intervals" than the fixed-time
   intervals, with the penalty minimised when N\* matches the realised sample size
   (**quote corrected to the page's own wording 2026-09-19**)
   [[src](https://docs.growthbook.io/statistics/sequential)].

**Decision rule.** Use a fixed horizon for the *offline gate* (you control the sample
size exactly; there is nothing to peek at). Use a confidence sequence for the *online
test*, because online you genuinely want to stop early on a safety regression. Set N\*
to the pre-registered horizon. Accept the width penalty and state it. ⚠️ This closes
doc 00 §5.3's explicit open item ("I could not fetch a primary source for always-valid
inference in this session"); both primary sources are now fetched and cited above.

### 2.6 Multiple comparisons and slices

Doc 00 §5.2: an aggregate-only claim is how a model that is 3 points better on the
90 % easy slice and 20 points worse on the 10 % hard slice gets promoted. So slices
are mandatory — and slices multiply the false-positive surface.

**Stratification design.** Slice by dimensions that (a) the customer recognises and
(b) plausibly change model behaviour:

| Dimension | Source | Typical cardinality |
|---|---|---|
| Intent / task type | Clustered from S2 traces, named by a human | 5–15 |
| Difficulty tier | Judge-uncertainty or incumbent-failure proxy (§6.4) | 3 |
| Prompt length bucket | p50 / p90 / p99 from traces (§1.4) | 3 |
| Language / locale | Trace metadata | 1–10 |
| Tool count in request | Trace metadata (doc 00 §3.3: student degrades as tool count grows) | 3 |
| Safety-sensitive flag | Classifier on the request | 2 |

**The correction rule that keeps this honest and affordable:**

- **Primary claim: one metric, on the whole frozen test set, no correction needed.**
- **Secondary claims: per-slice non-inferiority, Holm-Bonferroni across the named
  slices.** With 10 slices, the smallest per-slice α becomes 0.005, which inflates
  per-slice n by roughly `(z_{0.995}+z_{0.8})²/(z_{0.95}+z_{0.8})²` ≈ **1.89×**
  (**corrected 2026-09-19**: this document previously printed ≈1.7×; recomputed
  (2.5758+0.8416)²/(1.6449+0.8416)² = 11.679/6.183 = **1.889**). Budget for it.
- **Do not claim anything about a slice you did not pre-register.** Post-hoc slicing
  on a failed experiment to find a subgroup that won is the oldest error in
  experimentation and the customer will eventually notice.
- **Slices are for detecting regressions, not for claiming wins.** Frame them
  one-sided: "no named slice regressed by more than δ_slice", with δ_slice allowed to
  be *wider* than the global δ (e.g. global 2 pp, per-slice 5 pp), because slice
  samples are small. This is the practical compromise that keeps per-slice n from
  dominating the budget.

### 2.7 Rare failures, safety gates and the rule of three

Aggregate A/B will never find a 1-in-10,000 failure. The arithmetic (`est.`):

| True failure rate | n for ≥95 % chance of seeing ≥1 | n for E[3 events] |
|---:|---:|---:|
| 1e-3 | 2,995 | 3,000 |
| 1e-4 | 29,956 | 30,000 |
| 1e-5 | 299,572 | 300,000 |

And the inverse — **the rule of three**, which is the number to put on a customer
dashboard because it converts "we saw no failures" into a bound:

| Observed | 95 % upper bound on failure rate |
|---|---:|
| 0 failures in 1,000 | 3.0e-3 |
| 0 failures in 10,000 | 3.0e-4 |
| 0 failures in 100,000 | 3.0e-5 |

**This is why shadow mode is not optional.** At 2M requests/month, a week of shadow
gives ~460k samples and therefore a 95 % upper bound of **6.5e-6** on any
deterministically-detectable failure (schema invalidity, crash, refusal-shape
mismatch) — a bound no A/B test at 5 % traffic could produce in a quarter. Shadow
turns rare-failure detection from a statistics problem into a coverage problem, and
coverage is purchasable with compute.

**Safety gates are counted, not averaged** (§1.1.4). The correct statement to the
customer is not "safety score 0.99" but "0 policy violations in N shadowed requests,
95 % upper bound X". Doc 00 I5: never traded against quality.

### 2.8 Bayesian A/B

Worth stating precisely rather than dismissing. A Bayesian analysis reports
P(candidate worse than incumbent by more than δ) directly, which is the quantity the
customer actually asked about and which frequentist output does not provide. Both
GrowthBook and Datadog Experiments (ex-Eppo) ship Bayesian and frequentist engines
side by side [[src](https://www.growthbook.io/pricing)]
[[src](https://www.geteppo.com/)].

**When to prefer it:** small samples where the normal approximation is poor; decisions
with asymmetric costs (a safety regression costs far more than a missed cost saving);
communicating to a non-statistical buyer.

**When not to:** any claim that will be audited or contractually relied on. The prior
becomes an argument, and "we chose a prior" is not a sentence you want in a
procurement negotiation. **Recommendation: run both, gate on the frequentist
non-inferiority bound (it is the contractual claim), and show the Bayesian
probability on the dashboard (it is the intuitive one).** Cost of running both:
approximately zero — same data, two summaries.

### 2.9 Variance reduction online

CUPED uses pre-experiment data as a covariate. Statsig describes it as adjusting "each
user's metric value with that user's pre-exposure data" from the 7 days before
exposure, and reports that "simulations show that 98.3% of metrics saw a decrease
through CUPED" in variance, applying it only where >100 units have pre-exposure values
and ≥5 % of units have pre-exposure data
[[src](https://docs.statsig.com/stats-engine/methodologies/cuped)]. GrowthBook lists
"CUPED/variance reduction" on **Pro and Enterprise** and "post-stratification
analysis" on **Enterprise only** (**corrected 2026-09-19** — the pricing table was
re-read; this document previously placed both on Pro/Enterprise)
[[src](https://www.growthbook.io/pricing)]; Datadog Experiments lists "CUPED (and
CUPED++)" [[src](https://www.geteppo.com/)]. ⚠️ I could not fetch the original CUPED
paper (the exp-platform PDF returned unreadable binary), so the mechanism above is
vendor-documented, not paper-sourced.

**Applicability to this platform is narrower than it looks.** CUPED needs a
pre-exposure measurement of the *same* metric on the *same* unit. For a support-chat
KPI measured per user over weeks, that exists. For an offline eval over a frozen test
set, it does not — but the offline analogue is exactly the paired design of §2.3,
which is strictly better (ρ between models on the same item is typically 0.3–0.7
[[src](https://www.anthropic.com/research/statistical-approach-to-model-evals)]).
**Use pairing offline, CUPED online, and do not confuse them.**

---

## 3. Judge validity

### 3.1 The baseline result and its ceiling

Strong LLM judges "match both controlled and crowdsourced human preferences well,
achieving over 80% agreement, the same level of agreement between humans", while
exhibiting **position bias, verbosity bias, self-enhancement bias and limited
reasoning ability** [[src](https://arxiv.org/abs/2306.05685)]. G-Eval reaches "a
Spearman correlation of 0.514 with human on summarization task, outperforming all
previous methods by a large margin", while itself flagging "the potential issue of
LLM-based evaluators having a bias towards the LLM-generated texts"
[[src](https://arxiv.org/abs/2303.16634)].

**The 80 % number is a ceiling, and here is what it costs.** If a judge misclassifies
symmetrically at rate ε = 1 − agreement, the *observed* difference between two models
is attenuated by a factor (1 − 2ε), and the sample size to detect a given true
difference inflates by 1/(1−2ε)² (`est.`):

| Judge-human agreement | Attenuation | A true 3 pp gap is observed as | n inflation |
|---:|---:|---:|---:|
| 70 % | 0.40 | 1.20 pp | **6.25×** |
| 80 % | 0.60 | 1.80 pp | **2.78×** |
| 90 % | 0.80 | 2.40 pp | 1.56× |
| 95 % | 0.90 | 2.70 pp | 1.23× |

Two conclusions the platform must act on:

1. **At 80 % judge agreement, the §2.2 sample sizes are understated by ~2.8×.** A
   δ=2 pp claim at p=0.85 needs not 3,942 but ~11,000 judged items per arm to retain
   80 % power. **Every sample-size quote must state the judge agreement it assumes.**
2. **Doc 00 §5.1's rule stands and gets sharper**: "a judge-measured 2-point delta is
   inside the judge's own noise floor". Put the noise floor on the dashboard as a
   shaded band, not in a footnote.

⚠️ The attenuation model above assumes *symmetric, independent* judge error. Real judge
error is neither — it is correlated with the very features (length, style) that
distinguish the arms, which makes the bias directional rather than merely attenuating.
The table is a lower bound on the damage, not an estimate of it.

**Percent agreement is not enough.** "Judging the Judges" finds that "judges with high
percent agreement can still assign vastly different scores", that even the best judges
"may still differ with up to 5 points from human-assigned scores", and that judges
show "sensitivity to prompt complexity and length" plus "a tendency toward leniency"
[[src](https://arxiv.org/abs/2406.12624)]. **Report Cohen's κ and per-slice accuracy,
not raw agreement.** And JudgeBench is the sobering upper bound on judge competence:
on "challenging response pairs spanning knowledge, reasoning, math, and coding" where
objective correctness is the ground truth, "many strong models (e.g., GPT-4o)
perform[ed] just slightly better than random guessing"
[[src](https://arxiv.org/abs/2410.12784)]. **A judge that is good at preference is not
thereby good at correctness.**

### 3.2 Self-preference, and the teacher-as-judge prohibition

Doc 00 §5.1 states the rule — the judge must not be the teacher. The primary evidence
is now direct rather than inferred: "an LLM evaluator scores its own outputs higher
than others' while human annotators consider them of equal quality", models show
"non-trivial accuracy at distinguishing themselves from other LLMs and humans", and
fine-tuning experiments reveal "a linear correlation between self-recognition
capability and the strength of self-preference bias", a causal relationship that
"resists straightforward confounders" [[src](https://arxiv.org/abs/2404.13076)].

**Why this is uniquely lethal for this product, restated mechanically.** The student is
trained to minimise divergence from the teacher's output distribution. If the teacher
judges, the judge's preference function and the training objective are the same
function. The eval then measures training convergence, not quality. **A perfectly
useless eval that always passes.**

**Enforcement, in the artifact schema, not in a policy doc:**

```
eval_run:
  judge_model_id:     e.g. "claude-opus-5"
  judge_model_vendor: "anthropic"
  teacher_model_id:   e.g. "gpt-6-astra"
  teacher_vendor:     "openai"
  assert: judge_model_vendor != teacher_vendor   # hard failure, not a warning
  assert: judge_model_id != student_base_model_id
```

Cross-vendor is the minimum bar; **report both directions** (judge A and judge B) and
show the spread. If the two judges disagree about which model won, there is no result —
that is the honest reading, and it is also the most defensible thing to show a buyer.

### 3.3 Calibrating judges against humans, per task

**Mechanism.** For each customer task:

1. Sample **≥200 items** stratified across the §2.6 slices (doc 00 MVP criterion 4).
   200 is the floor for a usable κ; 500 is the number to aim for if the task has >5
   slices, since per-slice κ on 200/10 = 20 items is meaningless.
2. Have ≥2 human SMEs label each independently; adjudicate disagreements. Record
   **human-human agreement first** — it is the actual ceiling, and if two SMEs agree
   only 75 % of the time, no judge will do better and the task definition is the
   problem, not the judge.
3. Measure judge-vs-adjudicated agreement and Cohen's κ, overall and per slice.
4. Ship the judge only if agreement ≥ the agreed floor (doc 00 uses 80 %) **and** κ is
   above a floor that accounts for base rates — on a task where 90 % of answers are
   correct, 90 % agreement is achievable by always saying "correct", and κ ≈ 0
   exposes that instantly.
5. Publish the judge's noise floor next to every delta it produces.

**Cost.** 200–500 items × 2 SMEs × (minutes per item). For text support chat at 3
min/item that is 20–50 SME-hours per task. For video at 15 min/item it is 100–250
SME-hours (§1.5.3). ⚠️ Doc 00 §4.3(c) flags that there is no public benchmark for
enterprise per-adjudicated-example cost; that remains true.

**Who pays.** The customer's SMEs, always. This is not cost-shifting — it is the only
way the gold set encodes *their* definition of quality, and it is also the moment the
customer becomes invested in the eval rather than sceptical of it. **Treat the gold-set
session as a sales asset.**

### 3.4 Judge drift

Judges are models, and models change under you. The canonical measurement: GPT-4's
accuracy on prime identification fell from **84 % in March 2023 to 51 % in June 2023**,
attributed partly to "a drop in GPT-4's amenity to follow chain-of-thought prompting";
both GPT-4 and GPT-3.5 produced more formatting errors in June; and the authors
conclude "the behavior of the 'same' LLM service can change substantially in a
relatively short amount of time" [[src](https://arxiv.org/abs/2307.09009)].

A judge whose behaviour shifts 30 points invalidates every historical score silently.

**Controls, in order of strength:**

| Control | Mechanism | Cost | Strength |
|---|---|---|---|
| **Pin the judge version** | Use a dated/pinned model ID, never a floating alias | $0 | Necessary, not sufficient — providers retire pinned versions |
| **Judge canary set** | 100–200 items with frozen adjudicated labels, re-run on *every* eval run; alert if judge agreement moves >3 pp | ~$10/run | **The load-bearing control.** Detects drift within one run |
| **Self-host the judge** | An open-weights judge on the platform's own GPUs | GPU hours | Total reproducibility; weaker judge; also removes the ToS exposure of doc 00 §8.1 |
| **Re-validate on version change** | Re-run §3.3 whenever the judge model ID changes | 20–50 SME-h | Mandatory; blocks the change until done |
| **Version every score** | Every stored score carries judge model ID + prompt hash + rubric version | storage | Makes historical scores interpretable instead of merely present |

**Recommendation: the judge canary set is the highest-leverage cheap control in this
entire document.** It is 100 items, it runs in every eval, and it converts a silent
catastrophe into an alert.

**The self-hosted-judge argument is stronger than it first looks.** It costs GPU hours
and gives a weaker judge — but it gives perfect reproducibility, no per-call cost at
scale, no vendor drift, and no ToS exposure. For a platform running judges over 100 %
of shadow traffic (§4.2), the marginal-cost argument alone may decide it. ⚠️ **TO BE
VERIFIED**: whether an open-weights judge in the repo's size classes reaches usable
agreement with humans on a customer task. Doc 00's students are candidates to *be*
judges as well as students; nobody has measured this for these models.

### 3.5 Reference-based grading beats reference-free

Repeating this as a standalone rule because it is the cheapest quality improvement
available. Giving the judge a gold answer changes its task from open-ended assessment
to comparison. Inspect encodes the distinction in its API surface
[[src](https://inspect.aisi.org.uk/scorers.html)]; VDCSCORE applies the same logic to
long video captions by decomposing into "multiple short question-answer pairs"
[[src](https://arxiv.org/abs/2410.03051)] — which is reference-based grading with an
extra decomposition step that also reduces judge-context length and therefore cost.

**Rule: if a gold answer can be obtained once, obtain it once, and never run a
reference-free judge on that task again.** The gold answer amortises across every
future round of the loop; the reference-free judge's noise does not.

### 3.6 "Eval the eval"

The meta-level checks the platform should run, all cheap:

| Check | Mechanism | Trigger |
|---|---|---|
| **Judge canary** | §3.4 | Every eval run |
| **Assay sensitivity** | §2.1 — a deliberately-weaker arm must score measurably worse | Every gate |
| **Label-noise audit** | Re-adjudicate a random 50 items of the gold set each quarter; disagreement with the stored label is the eval's own error rate | Quarterly |
| **Ambiguity audit** | Items where 2 SMEs disagreed are removed or explicitly marked ambiguous | Gold-set construction |
| **Position-bias check** | Win rate should be ~symmetric under order swap; asymmetry >2 pp means the judge prompt is broken | Every pairwise run |
| **Length-confound check** | Regress score on output length; a large coefficient means you are measuring verbosity (§1.1.2) | Every judged run |

The label-noise audit has the strongest published justification. The "platinum
benchmarks" work finds that "pervasive label errors can compromise these evaluations,
obscuring lingering model failures and hiding unreliable behavior", and after
curating to "minimize label errors and ambiguity" finds "frontier LLMs still exhibit
failures on simple tasks such as elementary-level math word problems"
[[src](https://arxiv.org/abs/2502.03461)]. **If the best-resourced public benchmarks
carry pervasive label errors, a customer's hastily-built gold set certainly does.**
The practical consequence: **an eval's own error rate is a floor on the differences it
can resolve.** A gold set with 5 % label noise cannot support a 2 pp claim, whatever
the sample size says.

---

## 4. Online experimentation for LLM endpoints

### 4.1 Traffic splitting at the gateway

The mechanism lives in the serving layer already documented in
[`scaling/02-serving-stack-and-routing.md`](../scaling/02-serving-stack-and-routing.md)
(§4 gateways, §4.2 Agent Router, §4.3 token-based rate limiting and tenant quotas) and
is not re-derived here. What this document specifies is the **contract the gateway
must satisfy for the statistics above to be valid**:

| Requirement | Why | Failure if violated |
|---|---|---|
| **Assignment is deterministic from a stable unit** — `hash(unit_id, experiment_id) → arm` | Reproducibility; no assignment store on the hot path | Non-deterministic assignment breaks re-analysis and audit |
| **Unit is user or session, never request** | Doc 00 §5.4: request-level randomisation breaks multi-turn sessions | Within-session model switching; corrupted trajectory evals; user-visible inconsistency |
| **Assignment is recorded in the trace** (doc 02) alongside the arm's model version and prompt-stack hash | Every analysis is a join on this | Cannot attribute outcomes to arms |
| **Assignment happens once and is sticky across the experiment** | Prevents dilution and mid-session flips | Effect estimate biased toward null |
| **Sample-ratio mismatch (SRM) is checked continuously** | A 50/50 split arriving 52/48 means assignment is broken and every result is void | Silent invalidity. GrowthBook lists "Sample ratio mismatch detection" in all tiers [[src](https://www.growthbook.io/pricing)] |
| **Adding the split costs ≤ 5 ms p99** | Doc 00 MVP criterion 1 | The experiment measures the gateway |

**SRM is the smoke alarm.** Any deviation from the intended ratio beyond what a
chi-square test tolerates means stop, do not analyse. It catches bugs that no quality
metric would.

### 4.2 The rollout ladder

Doc 00 §1.2 S9 and §5.6 fix the order. Concretely, with the gate each stage must pass:

| Stage | Traffic | Duration (typical) | Primary gate to advance | Rollback trigger |
|---|---:|---|---|---|
| **0. Dev endpoint** | 0 % (manual) | hours | Contract conformance 100 % (doc 00 I4) | n/a |
| **1. Shadow** | 0 % user-visible, 100 % mirrored | ≥ 7 days | Disagreement rate stable; 0 schema failures in N with rule-of-three bound (§2.7); p99 latency ≤ incumbent | Any crash, any schema failure class |
| **2. Canary** | 0.5–2 %, internal or one segment | 24–72 h | No guardrail breach; no new error class | Any guardrail breach |
| **3. A/B** | 10–50 % | ≥ 1 full weekly cycle, and ≥ pre-registered n | Non-inferiority bound met on primary + no named slice regressed > δ_slice + safety unconditional | Sequential safety monitor (§2.5) |
| **4. Promote** | 100 % main | — | Customer sign-off | Instant revert, doc 00 I7, < 60 s |
| **5. Monitor** | 100 % | forever | Drift monitors (§6.5) | Re-enter loop |

**Two durations that are not statistical and are not negotiable:**

- **A full weekly cycle minimum.** Traffic mix differs Monday vs Saturday. A 3-day
  test on a 2-day-sufficient sample size measures the weekday.
- **Minimum exposure per unit.** A user who saw the candidate once has not been
  exposed to it. For a KPI like "resolved without escalation", the unit must have had
  a real opportunity to express the outcome.

### 4.3 Guardrail metrics

Guardrails are metrics you are *not* trying to improve and will stop the experiment
for. Unlike the primary metric, you **may** peek at them continuously — the asymmetry
is deliberate: false alarms on guardrails cost a rollback, false wins on the primary
cost the customer's product.

| Guardrail | Source | Typical trigger |
|---|---|---|
| Schema/tool-call validity | Programmatic, on every response | **Any** failure → stop |
| Safety violations | Safety classifier on every response | **Any** → stop |
| p99 latency | Gateway | > incumbent + agreed tolerance |
| Error/5xx rate | Gateway | > baseline + small margin |
| Refusal rate | Classifier | Deviation either direction (doc 00 §2.3: an app branching on refusals takes the wrong branch) |
| Output-token mean | Trace | Large increase = cost blowout and verbosity confound |
| User retry / regenerate rate | App telemetry | Increase = the cheapest real quality signal that exists |
| Escalation / handoff rate | App telemetry | Increase |

**Retry rate deserves its own line in the pitch.** It is free, it is behavioural rather
than stated, it has no judge, and it moves fast. Where the customer's product has a
"try again" affordance, **it is the best online quality metric available** and should
be the primary online metric wherever the real KPI lags.

### 4.4 Interleaving

Interleaving mixes two systems' outputs within a single user's experience, making the
comparison within-user and therefore much more sensitive than between-user A/B. It is
standard in search ranking.

⚠️ **I could not fetch a primary source quantifying the sensitivity gain.** The Netflix
tech-blog article returned HTTP 403 and the Microsoft Research page for the
large-scale interleaving validation paper carried only an abstract that "analyzes the
statistical efficiency of interleaving" without the comparative figures
[[src](https://www.microsoft.com/en-us/research/publication/large-scale-validation-and-analysis-of-interleaved-search-evaluation/)].
**Do not quote a sensitivity multiple for interleaving without obtaining one.**

**Applicability to LLM endpoints is limited and should be stated plainly:**

| Surface | Interleaving possible? | How |
|---|---|---|
| Chat with regenerate / multiple drafts | **Yes** | Present both models' outputs (blind, order-randomised) and record which the user keeps |
| Autocomplete / suggestions | **Yes** | Alternate suggestion sources |
| Single-response chat | **No** | There is only one output slot |
| Agentic tool loop | **No** | The trajectory diverges after turn 1; there is nothing to interleave |
| Video captioning batch job | **No** | No user in the loop |

Where it works it is extremely good, because the user's *choice* is an outcome signal
(§1.1.1 tier 2), not a preference rating. Where it does not, do not contort the product
to enable it.

### 4.5 KPI lag and what to do about it

The metric the customer cares about (resolution rate, conversion, churn) may take days
to weeks to realise. Three responses, in order of preference:

1. **Use a validated leading indicator.** Retry rate, escalation rate, session length,
   tool-call success. Validate it once against the lagging KPI on historical data —
   i.e. show the correlation — and then use it for the gate. **This validation is a
   one-time analysis on the customer's existing data and can be done before any model
   work starts**, which makes it an excellent first engagement deliverable.
2. **Run the experiment long enough.** Expensive in calendar time, which is the
   scarcest resource in a sales cycle.
3. **Gate on non-inferiority of the leading indicator plus a commitment to monitor the
   lagging KPI post-promotion, with a pre-agreed rollback trigger.** This is what
   actually happens in practice and it should be written down honestly rather than
   arrived at by drift.

### 4.6 Experimentation platforms as of 2026-09-19

| Platform | Status / pricing | Statistical features | Fit for this platform |
|---|---|---|---|
| **Statsig** | Developer $0 (2M events/mo, 50k session replays), Pro **$150/mo** (5M events, then $0.05/1k), Enterprise custom (warehouse-native only at Enterprise) [[src](https://www.statsig.com/pricing)] | CUPED documented in detail incl. applicability thresholds [[src](https://docs.statsig.com/stats-engine/methodologies/cuped)] | Good general engine; not LLM-aware |
| **Eppo → Datadog Experiments** | **"Eppo has been acquired by Datadog!"** — now Datadog Experiments; no pricing published [[src](https://www.geteppo.com/)] | "Sequential testing", "Fixed sample", Bayesian + frequentist, **CUPED**, multiple-testing correction, warehouse-native. ⚠️ The variant name **"CUPED++" was not visible on the page re-read 2026-09-19** — treat it as unconfirmed | Strongest named stats list; acquisition means roadmap risk (doc 00 §8.6) |
| **GrowthBook** | OSS self-hosted free (unlimited users); Cloud Starter $0 (≤3 users), Pro **$40/seat/mo** (≤30 users), Enterprise custom (unlimited) [[src](https://www.growthbook.io/pricing)] | Bayesian + frequentist, SRM detection, multiple-testing correction all tiers; sequential testing (asymptotic confidence sequences, Waudby-Smith et al. 2023, tuning parameter N\*) and CUPED on **Pro+**; **post-stratification Enterprise-only** (corrected 2026-09-19) [[src](https://www.growthbook.io/pricing)] [[src](https://docs.growthbook.io/statistics/sequential)] | **Best fit to build on**: open source, warehouse-native, sequential testing documented at the method level |
| **LaunchDarkly** | Developer $0 (5k AI runs/mo), Foundation pay-as-you-go ($10/service connection/mo; $8.33/1k client MAU/mo; $5 per additional 1k AI runs), Enterprise custom [[src](https://launchdarkly.com/pricing/)] | **AI-native**: "Online Evals (LLM-as-judge)" on all tiers, offline evals with datasets and batch processing, custom judges, AI Insights, PII redaction at Enterprise | The only flag vendor that has moved into LLM evals; the closest thing to a direct competitor for §4's scope |

**Build/buy call.** The *statistics* are commodity and open source (GrowthBook). The
*LLM-specific* parts — shadow diffing, judge orchestration, prompt-stack hashing,
per-slice gates, contract conformance — are not, and are exactly what doc 00 §6 says
must be built (doc 07 "is the product's differentiator and cannot be outsourced").
**Recommendation: use GrowthBook's engine (or its method) for assignment, SRM and
sequential inference; build everything above it.** Note LaunchDarkly shipping
LLM-as-judge evals inside a flag platform as a warning shot: the commodity boundary is
moving up.

---

## 5. Frameworks and tooling as of 2026-09-19

### 5.1 The survey

⚠️ **Not exhaustive** — see the method caveat. Every row below was fetched today.

| Tool | Offline | Online / prod | Judges | Datasets & versioning | CI | Licence / hosting | Price (2026-09-19) |
|---|---|---|---|---|---|---|---|
| **Inspect** (UK AISI + Meridian Labs) | **Yes** — datasets / solvers / scorers / models; "200+ Pre-built Evaluations"; "over 20 model providers" plus HuggingFace, vLLM, SGLang | No | `model_graded_qa()`, `model_graded_fact()` | Local; task-as-code | Yes (CLI) | Open source (MIT per repo) | Free [[src](https://inspect.aisi.org.uk/)] [[scorers](https://inspect.aisi.org.uk/scorers.html)] |
| **lm-evaluation-harness** (EleutherAI) | **Yes** — "Over 60 standard academic benchmarks … hundreds of subtasks"; backend for the HF Open LLM Leaderboard; vLLM + SGLang; LoRA/PEFT adapters; **2026/09 plugin system** (`lm_eval.*` entry points); 2025/12 CLI subcommands + YAML `--config`; lighter install | No | Via API model backends | YAML task configs | Yes | Open source | Free [[src](https://raw.githubusercontent.com/EleutherAI/lm-evaluation-harness/main/README.md)] |
| **lmms-eval** (EvolvingLMMs-Lab) | **Yes, multimodal** — 100+ tasks, 30+ models; **v0.7 (Feb 2026)**: agentic task eval (`generate_until_agentic`), TorchCodec video I/O "up to 3.58x faster", safety/red-teaming baselines, efficiency metrics; **v0.6 (Feb 2026)**: standalone HTTP eval server, ~7.5× throughput over v0.5, "statistically grounded results (CI, paired t-test)". States CI, **clustered standard errors**, paired comparisons as design principles | Eval-as-a-service (HTTP) | Yes | Task configs | Yes | Open source | Free [[src](https://raw.githubusercontent.com/EvolvingLMMs-Lab/lmms-eval/main/README.md)] |
| **promptfoo** | Yes | Continuous monitoring (Enterprise) | Yes | Local configs | **Yes** (CI-first design) | Open source core; Enterprise + on-prem | Community **free forever** incl. all eval features and red teaming to 10k probes/mo; Enterprise & On-Premise **custom, no public price** [[src](https://www.promptfoo.dev/pricing/)] |
| **DeepEval** (Confident AI) | **Yes** — "similar to Pytest but specialized for unit testing LLM apps"; G-Eval, DAG; agentic metrics: Task Completion, Tool Correctness, Goal Accuracy, Step Efficiency, Plan Adherence, Plan Quality, Argument Correctness | Via Confident AI cloud | Yes, incl. local NLP models | Cloud datasets | **Yes** | OSS framework + commercial cloud | DeepEval free; Confident AI Free $0 (2 seats, 1 project, 5 runs/wk, 1 GB-mo spans), **Starter $200/mo**, **Team $2,000/mo**, Enterprise custom; spans "$1 per GB-month" [[src](https://raw.githubusercontent.com/confident-ai/deepeval/main/README.md)] [[src](https://www.confident-ai.com/pricing)] |
| **Ragas** | **Yes, RAG-focused** — LLM-based + traditional metrics, **test-set generation** ("production-aligned test set generation"), `ragas quickstart rag_eval` templates; agent/benchmark/prompt/workflow templates listed **"Coming Soon"** | Feedback loops from production (claimed) | Yes | — | Via code | Apache-2.0. **⚠️ Note: the canonical repo now resolves under `vibrantlabsai/ragas`, and the README directs eval consulting to `founders@vibrantlabs.com`** — an ownership/branding change worth confirming | Free [[src](https://raw.githubusercontent.com/explodinggradients/ragas/main/README.md)] |
| **Braintrust** | **Yes** — "Experiments are the immutable, comparable record of your eval runs"; autoevals + LLM-as-judge + custom scorers + classifiers; playground comparison | **Yes** — "Online scoring evaluates production traces automatically as they're logged, running asynchronously with no impact on latency" | Yes | Datasets, immutable experiments | **Yes** — "integrated into CI/CD to catch regressions before they reach production" | Commercial; on-prem at Enterprise | Starter $0 ($10 model credits, 1 GB/mo then $4/GB, 10k scores/mo then $2.50/1k, 14-day retention); **Pro $249/mo** ($100 credits, 5 GB then $3/GB, 50k scores then $1.50/1k, 30-day retention then $0.50/GB/mo); Enterprise custom [[src](https://www.braintrust.dev/pricing)] [[docs](https://www.braintrust.dev/docs/guides/evals)] |
| **LangSmith** | Yes — datasets, offline evals, annotation queues | Yes — online evals | Yes; **Tuned Evaluators** (public beta on Plus/Cloud Enterprise, US; **0.01 LCU per successful evaluation run ≈ $0.015** — corrected 2026-09-19 from "$0.01 LCU/run"; failed/skipped runs unbilled) | Datasets | Yes | Commercial; self-host/hybrid at Enterprise | Developer $0/seat (5k base traces/mo); **Plus $39/seat/mo** (10k base traces); Enterprise custom. Usage: **$1.50/LCU**, **$1.00/LSU** [[src](https://www.langchain.com/pricing-langsmith)] |
| **Phoenix** (Arize) | Yes — datasets & experiments, prompt versioning, side-by-side | Yes — tracing | "LLM-based evaluators, code-based checks, or human labels" | Datasets | Via code | **Open source**; self-host Docker/K8s, or Arize AX managed | Free self-hosted [[src](https://arize.com/docs/phoenix)] |
| **W&B Weave** | Yes — "flexible imperative evaluation API"; "powerful evaluation comparisons and visualizations"; leaderboards | Yes — agent-native tracing; **Guardrails** scorers (toxicity, bias, **PII**, hallucination, coherence, fluency, context relevance) | Yes | W&B artifacts | Via code | Commercial | ⚠️ No pricing on the Weave page [[src](https://wandb.ai/site/weave/)] |
| **Langfuse** | Yes — datasets, experiments (SDK + UI), annotation queues | Yes — tracing | LLM-as-judge on all plans | Datasets + prompt management | Via SDK | **Open source, self-hostable free** | Hobby $0 (50k units/mo, 30-day access, 2 users); **Core $29/mo**; **Pro $199/mo**; **Enterprise $2,499/mo** (all 100k units); graduated overage $8 → $7 → $6.50 → $6 per 100k [[src](https://langfuse.com/pricing)] |
| **HELM** (Stanford CRFM) | Yes — MMLU-Pro, GPQA, IFEval, WildBench; metrics "beyond accuracy (e.g. efficiency, bias, toxicity)"; leaderboards **HELM Capabilities, HELM Safety, VHELM** | No | Some | Standardised formats | Via CLI | Open source | Free — **⚠️ "HELM entered maintenance mode on June 1, 2026"** [[src](https://raw.githubusercontent.com/stanford-crfm/helm/main/README.md)] |
| **EvalPlus / EvalPerf** | Yes, code — HumanEval+ ("80x more tests"), MBPP+ ("35x more tests"), EvalPerf for code efficiency; vLLM/Gemini/Anthropic backends; Docker sandboxing | No | No (execution-based) | Fixed datasets | Yes | Open source | Free — latest noted release `v0.3.1`, **2024-10-20** [[src](https://raw.githubusercontent.com/evalplus/evalplus/master/README.md)] |
| **BigCodeBench** | Yes, code — 1,140 tasks, 139 libraries, 7 domains, 5.6 tests/task, 99 % branch coverage | No | No | Fixed | Yes | Open source | Free [[src](https://arxiv.org/abs/2406.15877)] |
| **BFCL** (Berkeley/Gorilla) | Yes, tool calling — v1 AST, v2 live enterprise data, v3 multi-turn/multi-step, v4 agentic web search / memory / **format sensitivity**; vLLM + SGLang backends | No | No (AST + execution) | Fixed | Yes | Open source (`bfcl-eval` on PyPI) | Free [[src](https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/README.md)] |
| **OpenAI Evals** | — | — | — | — | — | — | **Deprecated. "Evals will become read-only for existing users on October 31, 2026" and "the platform is scheduled to shut down on November 30, 2026"**; users pointed at "Datasets" [[src](https://developers.openai.com/api/docs/guides/evals)] |

### 5.2 What the survey says, as decisions

**Three data points on vendor durability, in one table.** OpenAI Evals shuts down
2026-11-30; HELM entered maintenance mode 2026-06-01; Eppo is now Datadog Experiments.
Add doc 00 §8.6's list (NVIDIA data-flywheel deprecated April 2026, NeMo Microservices
sunset 2026-10-01, OpenAI fine-tuning winding down) and the pattern is unambiguous:
**the eval-tooling layer churns faster than the customer relationships it would
support.** Do not put a gate on a vendor product.

**The recommended stack**, given that:

| Layer | Choice | Why |
|---|---|---|
| Eval **definition** format | **Own it.** A small YAML/JSON schema: dataset ref + split + scorer refs + slices + `cluster_id` + gate thresholds + judge pin | This is the artifact the customer signs. It must outlive every tool below it |
| Text/agentic **execution** | **Inspect** | Solver/scorer/dataset separation matches the schema above; agentic primitives; 200+ prebuilt evals for sanity-checking; MIT; a national institute behind it rather than a startup |
| Multimodal/video **execution** | **lmms-eval** | The only harness fetched today that ships CI + **clustered standard errors** + paired comparisons as first-class, plus video I/O optimisation and an HTTP eval server |
| Academic **baselines** | lm-evaluation-harness | For "is this base model sane before we start", not for customer gates |
| Code / tool-call **verifiers** | EvalPlus, BigCodeBench, BFCL | Programmatic tier-1 scoring (§1.1.1); free and drift-proof |
| Trace store & **datasets** | **Langfuse** (doc 00 doc 02 already recommends it) | Open source + self-hostable; datasets and experiments in the same store as traces, which is the §6 pipeline |
| Experiment **engine** | GrowthBook engine or method (§4.6) | Open source; sequential inference documented at method level |
| Judge **orchestration**, shadow diffing, gates, slices, dashboards | **Build** | Doc 00 §6: this is the product |

**What to avoid buying.** Anything that owns the *gate decision* or the *eval
definition*, because that is the customer's contract and it must be portable. Doc 00
§8.6's rule — "Weights, datasets, evals and traces must be exportable by the customer,
in open formats, on demand" — applies to this layer most of all.

---

## 6. Eval-set construction from production traffic

### 6.1 Why this is the hardest part

The statistics of §2 assume a sample from the population the model will serve. In a
closed loop, the eval set and the training set come from the **same trace store**,
which makes contamination the default rather than an accident (doc 00 §8.4).
Everything below exists to keep the test split honest.

### 6.2 Sampling

| Stratum | Share of eval set | Rationale |
|---|---:|---|
| **Uniform random from production** | 50–60 % | The only stratum that estimates the production distribution; without it every metric is about a curated world |
| **Hard slice** (§6.4) | 15–25 % | Doc 00 §3.3: rare hard cases are rare in traffic *by construction*, so uniform sampling under-represents exactly what fails |
| **Disagreement-mined** (shadow diffs) | 10–20 % | Doc 00 §7.2: "mine the disagreements, not the average" |
| **Safety / adversarial** | 5–10 % | Doc 00 I5; oversampled because refusals are a small token fraction |
| **Regression suite** | Separate file, never sampled | §1.1.3 |

**Weight back to the production distribution when reporting the headline metric.**
An oversampled hard slice makes the aggregate score pessimistic; reporting both the
stratified per-slice scores and the *reweighted* aggregate is the honest presentation.
Not doing this is how a customer is shown a number that does not match what they see
in production and loses trust in the whole exercise.

### 6.3 Dedup

Production traffic is enormously repetitive (the same 50 support questions, rephrased).
Without dedup, a test set of 4,000 rows may contain 400 distinct problems, and the
effective sample size — the thing that sets the CI — is 400, not 4,000. This is the
same error as the clustering trap (§2.3) wearing a different hat.

Three-stage pipeline:

1. **Exact hash** on the normalised user turn. Removes retries and replays.
2. **Near-duplicate** by embedding cosine similarity (threshold tuned per task; start
   at 0.95 and inspect the borderline band by hand once).
3. **Cross-split check.** No near-duplicate may appear in both train and test. This is
   the single mechanical defence against contamination.

**Keep the duplicate counts.** They are the natural weights for §6.2's reweighting and
they identify the head of the distribution, which is where a student most easily wins.

### 6.4 Difficulty tiering

Needed for §2.6 slices and §6.2 stratification, and it must be defined **without**
reference to the candidate (or it leaks). Workable proxies, in preference order:

1. **Incumbent failure or low confidence.** If the incumbent's answer was rejected,
   retried, escalated or thumbed down in production — that is the customer's own
   ground truth about difficulty, free, and uncontaminated by the student.
2. **Judge uncertainty on the incumbent's output.** Cheap, but inherits judge bias.
3. **Inter-annotator disagreement** on the gold subset. Expensive, most valid.
4. **Structural proxies**: prompt length, tool count, turn count, retrieval-hit count.
   Free, weakly correlated with actual difficulty, good for a first cut.

⚠️ **TO BE VERIFIED** that any of these correlates with what an SME would call hard on
a real customer task. Measure it on the first engagement: label 200 items by SME
difficulty and check the proxy's rank correlation. If it fails, the difficulty slices
are decorative.

### 6.5 Versioning, contamination control and refresh

**Versioning requirements**, all of which are cheap and all of which are usually
skipped:

- Every eval set is an immutable, content-addressed artifact: `eval_set@sha256:...`
- Every score carries: eval-set version, judge model ID, judge prompt hash, rubric
  version, scorer version, **and the serving-artifact ID that produced the outputs**
  (doc 00's gate-twice rule means "the model" is ambiguous without this).
- Splits are defined by a **stable entity hash**, never by row index:
  `split = bucket(hash(user_id or document_id or session_id))`. New traffic from an
  existing entity lands in the same split forever.
- **Temporal splits where the task is temporal**: test data strictly later than train.
- Any refresh of an eval set **invalidates every historical score against it** — the
  platform must refuse to plot old and new scores on the same axis. Enforce it in the
  data model, not in a convention.

**Contamination control**, in the closed loop specifically:

| Vector | Control |
|---|---|
| Test rows used as training examples | Split by entity hash *before* the annotation pipeline runs; the annotation job is only ever given train-split IDs |
| Near-duplicates across splits | §6.3 stage 3 |
| Test prompts used as on-policy distillation prompts | Same control — the prompt set for S5 draws from train only. **Easy to get wrong**, because on-policy distillation wants *prompts*, and prompts feel less like labels than they are |
| Teacher annotations of test rows leaking via a shared cache | Namespace the annotation cache by split |
| Student's own outputs becoming next round's labels | Doc 00 §8.3 guard 1; and the eval set must keep a human/outcome-anchored slice refreshed on a **different cadence** from training data (guard 3) |

The general risk is well-established: ChatGPT and GPT-4 achieved **52 % and 57 %
exact-match when guessing *masked* MMLU answer options**, a level hard to explain
without exposure [[src](https://arxiv.org/abs/2311.09783)]. In a closed loop the
mechanism is more direct than web contamination — same store, same pipeline — so the
controls must be mechanical rather than procedural.

**Refresh cadence.**

| Component | Cadence | Trigger for off-cycle refresh |
|---|---|---|
| Regression suite | Continuous (append-only) | Every incident |
| Uniform-random stratum | Quarterly, or on drift alert | Distribution shift detected (doc 02/03 drift monitor) |
| Hard slice | Per loop iteration | New failure mode found in shadow |
| Gold/human-labelled set | 6–12 months | Judge re-validation; label-noise audit failure (§3.6) |
| Safety suite | Append-only + quarterly review | Any incident; policy change |
| **Everything** | — | **Customer changes the prompt stack** (doc 00 §2.2 — the parity claim is void and must be re-earned) |

### 6.6 Gold-label acquisition

| Method | Cost/item | Quality | Use for |
|---|---|---|---|
| Production outcome signal | $0 | Highest (it is the truth) | Anything where it exists — hunt for it first |
| Incumbent output + SME spot-check | low | High if the incumbent is good | Reference-based grading (§3.5) |
| Single SME label | medium | Medium (no disagreement estimate) | Bulk gold |
| 2 SMEs + adjudication | **2–3×** | Highest available; **gives you the human-human ceiling** | Judge calibration set (§3.3), always |
| Teacher-model label | ~$0.03–0.26/item (§8) | Unknown until validated | Training data, **never test labels** |

**Hard rule, restated because it is the one most likely to be violated under schedule
pressure: the teacher may label the training split; the test split's labels come from
humans or from outcomes.** If the teacher labels the test set, the eval measures
agreement with the teacher, which is the training objective, which is §3.2's failure
one level up.

### 6.7 Size guidance per task

Derived from §2 and adjusted for a realistic judge (80 % agreement → ~2.8× inflation,
§3.1), paired where possible (§2.3):

| Task type | Primary metric | δ | Gold-labelled calibration set | Frozen test split | Notes |
|---|---|---:|---:|---:|---|
| Classification / routing | Accuracy | 2 pp | 200 | **1,500–3,000** | Programmatic scoring; no judge inflation |
| Tool calling | Per-tool accuracy + AST match | 2 pp | 200 | **2,000–4,000** | Programmatic (BFCL-style); slice per tool |
| Structured extraction | Field-level F1 + schema validity | 2 pp | 200 | **1,500–3,000** | Schema validity is a gate, not a metric |
| Support chat (rubric) | Rubric pass rate | 3 pp | **500** | **4,000–8,000** | Judge-inflated; see §8.1 |
| Summarisation | Reference-based rubric | 3 pp | 300 | 3,000–6,000 | Length-control mandatory |
| RAG QA | Faithfulness + answer correctness | 3 pp | 300 | 3,000–5,000 | Cluster on **document** |
| Agentic multi-turn | pass^5 at session level | 5 pp | 100 sessions | **800–1,500 sessions** × 5 trials | Cluster on **task**; wider δ because sessions are expensive |
| Long context | Task metric at p99 length | 3 pp | 200 | 1,500–3,000 | Sliced by length bucket |
| Video captioning | VDCSCORE-style reference QA | 3 pp | **500–1,000 videos** (reference captions) | **1,500–3,000** | Gold captions amortise forever (§1.5.3) |
| Video temporal grounding | mIoU (paired bootstrap) | 0.03 mIoU | 300 | 1,500–3,000 | Continuous metric; σ from pilot |

These are **test-split** sizes for the primary claim. Per-slice claims multiply by the
Holm correction (§2.6, **~1.89×** at 10 slices — corrected 2026-09-19) and by the number of slices that need
independent power — which is why §2.6 recommends framing slices as one-sided
regression checks with a wider δ rather than independent parity claims.

---

## 7. The confidence protocol

What the customer is buying is **permission to switch** (doc 00 §5). This section is
the manufactured artifact.

### 7.1 Stages and gates

| # | Stage | Duration | What happens | Gate to proceed | Evidence handed to the customer |
|---|---|---|---|---|---|
| **P0** | **Pre-registration** | 1–2 weeks | Define task, metric, δ, α, power, slices, safety suite, guardrails, rollback trigger. Sign it. | Customer signature | The **Parity Protocol** document (§7.3) |
| **P1** | **Baseline + judge calibration** | 2–3 weeks | Capture traces; build eval set (§6); 200–500-item gold set; measure **human-human** then **judge-human** agreement; run the incumbent on the frozen test set and freeze its outputs forever (§2.1 biocreep) | Judge-human agreement ≥ floor; assay sensitivity passes (§2.1) | Judge validity report incl. κ per slice and the **noise floor** |
| **P2** | **Cheap-rung trial** | 1 week | Doc 00 §3.2 rung 1: incumbent's own tier-down (Luna/Haiku-class) with the customer's existing prompt, scored on the same frozen set | If it passes, **stop and say so** | The same non-inferiority report. Costs us a week and buys enormous credibility |
| **P3** | **Offline gate on BF16 checkpoint** | per iteration | §2.3 paired non-inferiority on the frozen test split + all slices + safety | Upper bound of one-sided 95 % CI on (incumbent − candidate) < δ, per §2.6 rules; safety unconditional | Score table with CIs; per-slice table; **the disagreement list** |
| **P4** | **Offline gate on served artifact** | per iteration | **Gate twice** (doc 00 §1.2): re-run P3 on the quantised/engine-optimised artifact that will actually serve | Same gates, re-passed | Delta-vs-checkpoint table (this is where quantisation drift shows) |
| **P5** | **Contract conformance** | hours | Schema, tool-call encoding, streaming events, refusal shape, context limit, sampling defaults (doc 00 §2.3) | 100 %, no exceptions | Conformance report |
| **P6** | **Rollback drill** | 1 hour | Promote and revert on the dev endpoint **in front of the customer**, timed, with request counts | < 60 s, zero dropped requests (doc 00 I7) | A timestamped recording. Doc 00 §5.6: this sells harder than the statistics |
| **P7** | **Shadow** | ≥ 7 days | 100 % mirrored; disagreement diffing; schema/crash/latency bounds via rule of three (§2.7) | 0 hard failures with an acceptable upper bound; p99 latency ≤ incumbent | Rule-of-three bound table; disagreement explorer |
| **P8** | **Canary** | 24–72 h | 0.5–2 %, internal segment if available | No guardrail breach | Guardrail dashboard |
| **P9** | **A/B** | ≥ 1 weekly cycle **and** ≥ pre-registered n | 10–50 %, sticky by user/session, SRM-monitored, sequential safety monitor | Pre-registered primary bound met; no slice regressed > δ_slice; guardrails clean | Online result with sequential CI; SRM check; guardrail history |
| **P10** | **Promote** | minutes | 100 % to main; previous version retained hot | Customer sign-off | Promotion record |
| **P11** | **Post-deployment watch** | 30 days | Lagging KPI monitored against the pre-agreed trigger; drift monitors on | — | Monthly parity re-check on a fresh sample |

**P2 is the stage that will be cut and must not be.** It is the stage that tells the
customer you are not selling them training they do not need (doc 00 §3.2 rung 1: "this
is free to test and sometimes ends the engagement — which is fine"). It also
establishes the eval harness as independently valuable, which is what doc 00 §6 calls
the minimum sellable product.

### 7.2 Rollback triggers, pre-agreed

Written into the protocol at P0, so that firing one is an engineering event rather
than a negotiation:

| Trigger | Threshold | Action |
|---|---|---|
| Any safety-suite violation in production | 1 | Immediate revert |
| Schema/tool-call invalidity | any new class, or rate > pre-agreed bound | Immediate revert |
| p99 latency | > incumbent + agreed tolerance, sustained 15 min | Revert |
| Error rate | > baseline + margin, sustained 15 min | Revert |
| Primary online metric | sequential lower bound crosses −δ | Revert |
| Retry / escalation rate | > baseline + margin, sustained 24 h | Revert and re-enter loop |
| SRM | chi-square p < 0.001 | **Halt analysis** (not necessarily revert) — results are void |
| Customer prompt-stack hash changes | any | Parity claim void; freeze promotion; re-run P3 (doc 00 §2.2) |

### 7.3 The Parity Protocol template

Signed at P0. One page. The whole commercial mechanism is that this exists *before*
any result does.

```
PARITY PROTOCOL — <customer> / <task> / v<n>          date: <YYYY-MM-DD>

1. TASK
   Description:            <one paragraph, written by the customer>
   Incumbent:              <model id + version>, prompt-stack hash <sha256:...>
   Candidate base:         <model id>
   Request volume:         <N>/month;  p50/p90/p99 prompt length: <a/b/c> tokens

2. PRIMARY CLAIM
   Metric:                 <named metric, scorer version>
   Scoring:                <programmatic | reference-based judge | rubric judge | outcome>
   Non-inferiority margin: δ = <x> pp        Confidence: 95 % one-sided
   Power:                  <80 % | 95 %>     Required n: <from §2, judge-inflated>
   Randomisation unit:     <user | session>
   Cluster unit:           <session | task | document | clip | user>

3. SLICES  (one-sided regression checks, Holm-corrected)
   <slice name>            δ_slice = <y> pp        min n = <...>
   ...

4. GATES (unconditional; never traded)
   Safety suite:           0 violations, 95 % upper bound reported
   Schema validity:        0 failures in the frozen set and in shadow
   Contract conformance:   100 %
   Rollback:               demonstrated < 60 s, zero dropped requests

5. JUDGE
   Judge model:            <pinned id>  (vendor ≠ teacher vendor; teacher = <id>)
   Gold set:               <n> items, <k> SMEs, adjudicated
   Human–human agreement:  <measured>          Judge–human agreement / κ: <measured>
   Judge noise floor:      ± <z> pp — shown on every chart
   Judge canary:           <n> items, re-run every eval, alert at 3 pp movement

6. EVAL SETS
   Test split:             eval_set@<sha256>, n = <...>, frozen <date>
   Split rule:             entity hash on <field>; temporal cutoff <date | n/a>
   Refresh:                <cadence>; refresh invalidates all prior scores

7. ONLINE
   Stages/durations:       shadow ≥ 7 d → canary <x> % <t> → A/B <y> % ≥ 1 week & n
   Guardrails + triggers:  <table, §7.2>
   Sequential method:      asymptotic confidence sequence, N* = <pre-registered n>
   Lagging KPI:            <name>; leading indicator <name>, validated r = <...>

8. COST CLAIM
   Incumbent price basis:  list | cached | batched | tier-down — <which, and why>
   Delivered $/1M:         <candidate, incl. idle GPU + amortised build>
   Break-even:             <months at stated volume>

9. WHAT VOIDS THIS PROTOCOL
   - Customer prompt-stack change          - Judge model version change
   - Eval-set refresh                      - Traffic distribution shift beyond <x>
   Signed: <customer>  /  <platform>
```

### 7.4 What to show, and in what order

Doc 00 §5.6 ranks the evidence by what actually persuades. The dashboard should
literally be in that order — the statistics are third, not first:

1. **The disagreement explorer.** "Here are the 214 requests where the two models
   differed. Here is who was right." Their traffic, their prompts, clickable.
2. **The rollback drill recording.** Timestamped, with request counts through the
   switch.
3. **Shadow coverage and the rule-of-three bounds.** "0 schema failures in 460,000
   production requests; 95 % upper bound 6.5e-6."
4. **The non-inferiority result**, with the judge noise floor drawn as a shaded band
   and the per-slice table below it.
5. **The safety result**, unconditional, as a count.
6. **The cost statement**, against their *batched, cached, tier-downed* price
   (doc 00 §4.4) — never against list.

---

## 8. Worked examples

### 8.1 Support chat, 2M requests/month

**Setup.** 4,000 input tokens (50 % cached), 512 output tokens per request.
Incumbent Claude Opus 5 ($5/$25 per 1M, cache read $0.50
[[src](https://claude.com/pricing)]). Candidate: distilled Qwen3.8-27B, self-hosted,
blended **$0.0602/1M** (doc 00 §4.2, from
[`matrix/cost-matrix.md`](../matrix/cost-matrix.md)). 3 requests/session.
Volume: **65,789 requests/day**, **2,741/hour**, **666,667 sessions/month**
(`est.`).

**Incumbent spend:** `est.` **$47,600/month** at list with 50 % cached input. Against
doc 00 §4.4's honest baseline (batched, 90 % cached, or tier-downed to Haiku-class),
the comparison number is lower — **use the customer's actual invoice, not this
figure**, and say so in the protocol §8.

**Protocol parameters.** Primary metric: rubric pass rate, baseline assumed p = 0.85.
δ = 3 pp. α = 0.05 one-sided. Power 80 %. Judge: cross-vendor to the teacher, measured
agreement 80 %.

**Offline sizing.**

| Design | Base n | ×2.78 judge inflation (§3.1) | Judge calls (both orders) | `est.` judge cost (list / batch) |
|---|---:|---:|---:|---:|
| Unpaired, per arm | 1,752 | 4,871 | 2 arms × 2 orders ≈ 19,484 | — |
| **Paired, 10 % discordance** | **687** | **1,910** | 3,820 | **$248 / $124** |
| Paired, 20 % discordance | 1,374 | 3,820 | 7,640 | $496 / $248 |

Pairwise judge cost per item `est.` **$0.0652** list, **$0.0326** batch (GPT-6
Astra-class judge, 4,000 + 2×512 in, 300 out, at $10/$50 per 1M
[[src](https://developers.openai.com/api/docs/pricing)]).

**The headline number for the offline gate is therefore a few hundred dollars.** The
expensive part is the gold set: 500 items × 2 SMEs × 3 min = `est.` **50 SME-hours**.

**If the customer insists on a pairwise-preference headline instead** (§2.4): 52 % vs
50 % at 95 % power = **8,116 pairs**, **16,232 judge calls**, `est.` **$1,059 list /
$529 batch** — and at a 40 % tie rate the item requirement rises to **13,527**. Still
affordable in dollars; the point to make to the customer is not cost but that
**pairwise preference is the weakest evidence per dollar**, and the paired rubric test
is **4.3×** cheaper *and* answers the contractual question ($1,059 vs $248 list;
16,232 vs 3,820 judge calls — recomputed 2026-09-19, previously stated as 5×).

**Shadow.** 100 % of 2M requests = 9.02B tokens/month on the candidate = `est.`
**$543/month** marginal at $0.0602/1M — i.e. **1.1 % of the incumbent bill**, before
the GPU floor. (⚠️ For contrast: teacher-labelling that same traffic at Opus 5 prices
would be `est.` **$75,012/month** — **flagged 2026-09-19: the token basis for this
figure is not stated and does not reconstruct.** At Opus 5 $5/$25 it implies ~4,000
uncached input + ~700 output tokens per labelled item, which is neither this
section's request shape (4,000 in / 512 out → $65,600/mo) nor the §8.1 judge shape
(4,512 in / 300 out → $55,120/mo). The order of magnitude — **teacher-labelling
100 % of traffic costs more than the incumbent bill itself** — is what carries the
argument and survives any of these bases; the exact figure should be recomputed
against a stated annotation prompt shape. This is why annotation samples rather
than labels everything — doc 00 §4.3.) After 7 days ≈ 460k requests, the rule of three gives a
95 % upper bound of **6.5e-6** on any deterministically-detectable failure class.

**Online A/B sizing.** Randomise on **session** (3 req/session → 21,930 sessions/day).
Primary online metric: resolution-without-escalation, baseline 0.70, δ = 3 pp →
n = **2,886 sessions/arm** (unpaired; online cannot be paired).

| Arm share | Sessions/day/arm | Days to reach n=2,886 | Governing constraint |
|---:|---:|---:|---|
| 5 % | 1,096 | **2.6** | **Weekly cycle (7 days)** |
| 10 % | 2,193 | 1.3 | **Weekly cycle (7 days)** |
| 50 % | 10,965 | 0.3 | **Weekly cycle (7 days)** |

**The statistics are not the binding constraint at this volume — the calendar is.**
Run at **10 %** for a full week: it reaches n in 1.3 days, so the remaining 5.7 days
buy weekly-seasonality coverage and guardrail exposure at low blast radius. There is
no reason to run at 50 % and every reason not to. Note also that at δ = 2 pp
(n = 6,492) a 5 % arm needs 5.9 days, so even the tighter margin fits inside one week
at 10 %.

**Sequential monitoring** with N\* = 2,886 (GrowthBook's guidance to set the tuning
parameter to the expected decision-point sample size
[[src](https://docs.growthbook.io/statistics/sequential)]), used **only** for the
safety/guardrail monitor; the primary metric is read at the pre-registered horizon.

**Total `est.` cost of the confidence protocol** (excluding training and GPU floor):

| Item | `est.` |
|---|---:|
| Gold set, 500 items × 2 SMEs × 3 min | 50 SME-hours |
| Judge calibration run (500 × 2 orders) | $65 |
| Offline gate, paired, ×2 (checkpoint + served artifact, doc 00 gate-twice) | $496 list / $248 batch |
| Judge canary, 100 items × ~20 runs | $130 |
| Shadow, 30 days | $543 |
| Online A/B judging (sampled, 2,000 sessions) | ~$260 |
| **Total ex-SME** | **≈ $1,500 list / ≈ $1,020 batch** (recomputed 2026-09-19: list 65+496+130+543+260 = $1,494; batch 32.6+248+65.2+**543**+130.4 = $1,019 — shadow is self-hosted inference and does **not** take the judge batch discount, which the previous "≈ $1,100" appears to have half-counted) |

Against a $47,600/month incumbent bill, **the entire evidence package costs under one
day of the customer's inference spend.** That ratio is the most persuasive number in
this document and belongs on the first slide.

### 8.2 Video captioning, 50K videos/month

**Setup.** Marlin-2B, 240-frame cap = **23,560 prefill tokens/request** regardless of
clip length ([`models/marlin2b/README.md`](../models/marlin2b/README.md) §9). Incumbent
at GPT-6 Astra / Fable 5.1 pricing ($10/$50 per 1M): `est.` **$0.2506/video**,
**$12,530/month**. Candidate marginal cost at Marlin-2B's blended $0.0092/1M
([`matrix/cost-matrix.md`](../matrix/cost-matrix.md) B300 row, via doc 00 §4.2 —
both re-read 2026-09-19): 1.178B tokens/month → `est.` **$10.84/month**
(50,000 × 23,560 = 1.178e9; ⚠️ note this counts **prefill only** — adding the ~300
output tokens per caption would make it 1.193B and $10.98, a 1.3 % understatement
that changes nothing below).

**Read that pair carefully.** The marginal token saving is 1,000×, and it is
**$12,519/month**. A single **B200** replica at Baseten dedicated pricing is
`est.` **$7,285/month** ($0.16633/GPU-min = $9.98/GPU-h × 730 h — verified on the
pricing page 2026-09-19, and matching doc 00 §4.5
[[src](https://www.baseten.co/pricing/)]), plus a dev replica. **Corrected
2026-09-19**: this sentence previously said "a single B300 replica … for a B200".
Baseten's dedicated table lists **B200 and H100 only — there is no B300 row**, so
the B200 price is the one that applies. **At 50K videos/month
the GPU floor eats most of the saving**, and the honest conclusion is that this
customer is marginal for self-hosting unless (a) volume grows, (b) the replica is
shared multi-tenant (doc 00 §4.5, S-LoRA-style), or (c) latency — not cost — is the
buying reason. **Say this before running the eval, not after.**

**Protocol parameters.** Primary metric: VDCSCORE-style reference-based QA pass rate
(§1.5.3), baseline p = 0.85, δ = 3 pp, 80 % power. Judge: text-only, reference-based,
cross-vendor.

**Sizing and cost (`est.`):**

| Item | Value |
|---|---:|
| Unpaired n/arm (p=0.85, δ=3 pp, 80 %) | 1,752 |
| Judge-inflated (80 % agreement) | 4,871 |
| Paired @ 10 % discordance, inflated | **1,910** |
| Text reference-based judge cost/item | **$0.0360** |
| Judge cost, 1,910 items × 2 orders | **$138** |
| *Alternative*: frontier multimodal re-watch judge/item | **$0.2576** (list) / $0.1288 (batch) |
| Same n on the multimodal judge | **$984 / $492** |
| Gold reference captions: 1,000 videos × 15 SME-min | **250 SME-hours** |

**The dominant cost is human, by two orders of magnitude**, and it is a **one-time**
cost that amortises across every subsequent loop iteration. This is the single most
important planning fact for video and it inverts the text case in §8.1, where the gold
set was 50 SME-hours and the judge tokens were the visible line item.

**Temporal-grounding arm** (if the task includes localisation): mIoU, δ = 0.03,
**paired bootstrap on clips** (§2.3, §1.5.2), n ≈ 1,500–3,000 clips, σ from a
100-clip pilot — do not assume it.

**Frame-budget honesty (§1.5.4).** Report two comparisons:
(a) incumbent forced to 240 frames — the model-vs-model comparison, where the paired
design cancels the sampling bias;
(b) incumbent at its native frame budget — the pipeline-vs-pipeline comparison, which
is what the customer actually experiences.
If (a) passes and (b) fails, the honest finding is *"the student matches the model but
not the pipeline; buy more frames"*, and the platform should say exactly that rather
than pick the flattering number.

**Shadow for video** is cheap on the candidate (`est.` $10.84/month for 100 % of
traffic) but has no incumbent to diff against unless the incumbent is also run —
which costs `est.` $12,530/month, i.e. **doubling the customer's bill during the
shadow period**. **Recommendation: shadow the candidate on 100 % but diff against
*stored* incumbent outputs already produced in production**, which is free, and is
only possible if trace capture (doc 02) stored the incumbent's full output. **This is
a concrete, cheap requirement on doc 02 that only shows up when you do the video
arithmetic: store full outputs, not summaries.**

**Online A/B for video** is usually impossible in the §4.4 sense (no user in the loop
on a batch captioning job). The realistic ladder is: offline gate → shadow with diffing
against stored incumbent outputs → human review of a sample of disagreements →
promote. **Say so in the protocol rather than pretending there is an A/B stage.**

---

## Implications for the platform

**What to build.**

1. **The eval-definition schema, owned by us and exportable by the customer.** Dataset
   ref, split rule, scorer refs, slices, mandatory `cluster_id`, gate thresholds,
   judge pin, δ/α/power. It is the artifact the customer signs (§7.3) and it must
   outlive every tool underneath it. Three vendor sunsets in this document alone
   (§5.2) make this non-negotiable.
2. **Clustered, paired, bootstrap statistics as the default and only path.** A scorer
   that cannot produce a clustered CI should refuse to emit a number. The 3×
   understatement from naive standard errors
   [[src](https://www.anthropic.com/research/statistical-approach-to-model-evals)] is
   the most likely way this platform ships a false parity claim.
3. **The judge canary set** (§3.4) — 100 frozen adjudicated items re-run on every eval
   run, alerting at 3 pp movement. Cheapest high-value control in this document.
4. **Judge noise floor as a first-class UI element** (§3.1). A shaded band on every
   chart, not a footnote. At 80 % judge agreement a 3 pp true gap reads as 1.8 pp and
   sample sizes inflate 2.78× — a customer shown an un-caveated 2 pp win has been
   misled.
5. **The disagreement explorer** (§7.4 item 1, doc 00 §7.2). The highest-value product
   surface in the whole platform, and it is a by-product of shadow mode.
6. **Shadow-first rollout with rule-of-three reporting** (§2.7). Converts rare-failure
   detection from a statistics problem into a purchasable coverage problem.
7. **Assay-sensitivity and biocreep controls** (§2.1): a deliberately-weaker reference
   arm in every gate, and every parity claim always against the *original* frozen
   incumbent outputs. Neither is expensive; I found no vendor doing either.
8. **Gate-twice enforcement in the data model** (doc 00 §1.2): every score carries the
   serving-artifact ID, so "passed the gate" is unambiguous about *which* artifact.
9. **The Parity Protocol** as a signed pre-registration (§7.3). This is the commercial
   product. Everything else is its evidence.
10. **Store full incumbent outputs in traces** (§8.2). Enables free shadow diffing on
    expensive modalities. A doc 02 requirement that only surfaces from doing the video
    arithmetic.

**What to buy / adopt.**

| Need | Take | Why |
|---|---|---|
| Text + agentic eval execution | **Inspect** (MIT, UK AISI) | Solver/scorer/dataset separation; agentic primitives; 200+ prebuilt evals; institutional backing [[src](https://inspect.aisi.org.uk/)] |
| Multimodal/video execution | **lmms-eval** | Only harness fetched with CI + clustered SEs + paired comparisons as principles, plus fast video I/O and an HTTP eval server [[src](https://raw.githubusercontent.com/EvolvingLMMs-Lab/lmms-eval/main/README.md)] |
| Programmatic verifiers | **BFCL, EvalPlus, BigCodeBench** | Free, drift-proof, execution-based |
| Traces + datasets | **Langfuse** (doc 00 doc 02) | Open source, self-hostable, datasets next to traces |
| Assignment / SRM / sequential inference | **GrowthBook** engine or method | Open source; asymptotic confidence sequences documented at method level [[src](https://docs.growthbook.io/statistics/sequential)] |
| Academic sanity baselines | lm-evaluation-harness | Not for customer gates |

**What to avoid.**

- **Any vendor owning the gate decision or the eval definition.** OpenAI Evals shuts
  down 2026-11-30 [[src](https://developers.openai.com/api/docs/guides/evals)]; HELM
  entered maintenance mode 2026-06-01
  [[src](https://raw.githubusercontent.com/stanford-crfm/helm/main/README.md)]; Eppo is
  now Datadog [[src](https://www.geteppo.com/)]. Plus doc 00 §8.6's three.
- **Teacher-as-judge, in any form**, including "a different snapshot of the same
  family". The self-preference mechanism is causal and measured
  [[src](https://arxiv.org/abs/2404.13076)].
- **Pairwise preference as the contractual metric.** Weakest evidence per dollar
  (§2.4), and it gets *more* expensive as the distillation gets *better* (tie rate).
  Use it for the demo; gate on paired rubric or programmatic scoring.
- **Live significance indicators on the primary metric.** 50 looks turns a 5 % test
  into a 32 % test (§2.5). Hide it until the horizon, or use a confidence sequence.
- **Aggregate-only claims** (doc 00 §5.2), and post-hoc slicing to rescue a failed
  experiment (§2.6).
- **Floating judge model aliases.** GPT-4's prime-identification accuracy moved 84 % →
  51 % in three months [[src](https://arxiv.org/abs/2307.09009)].
- **Quoting an interleaving sensitivity multiple** until a primary source is obtained
  (§4.4).
- **Selling the full loop to a video customer at 50K videos/month** without first
  checking that the GPU floor does not eat the saving (§8.2).

---

## Open questions

Consolidated ⚠️ items. Each names what is unknown, why it matters, and what would
close it.

1. **⚠️ The whole survey is search-limited.** This session's WebSearch budget was
   exhausted before this agent started; every source was fetched by known URL. §5's
   tool table is a floor on the landscape, not a scan of it. **Close by:** re-running
   §5 with search enabled. Specifically missing and worth hunting: Vellum, Humanloop,
   Galileo, Patronus, Vals AI, Evidently, Okareo, Freeplay, Comet Opik, Maxim, and any
   eval tooling shipped inside the inference vendors doc 00 §7 surveyed.
2. **⚠️ "TimeLens" is unidentified** (§1.5.1). The task brief names it as a video
   benchmark; the only TimeLens I can place is event-camera frame interpolation, which
   is not an eval. **Close by:** one search. Until then the video temporal benchmarks
   are LongVideoBench, MVBench, TempCompass and Charades-STA.
3. **⚠️ Temporal-grounding metric definitions are not sourced** (§1.5.2). R@n/IoU=m and
   mIoU conventions are stated from understanding, not from the TALL abstract I
   fetched. **Close by:** reading the full paper before implementing the scorer.
4. **⚠️ Frame-sampled proxy validity is unmeasured** (§1.5.4). No published correlation
   between a fixed-frame-budget eval and full-clip human judgement, on any task. The
   paired-design argument says the bias cancels between arms sharing a budget; nothing
   validates the absolute score. **Close by:** measuring it on the first video
   customer — 200 clips, graded at 240 frames and by full-clip human review, report
   rank correlation. **This is a prerequisite to any video parity claim** and closes
   doc 00 §5.5.
5. **⚠️ Judge-error attenuation model is idealised** (§3.1). It assumes symmetric,
   independent misclassification. Real judge error correlates with length and style —
   the exact features distinguishing the arms — so it is directional, not merely
   attenuating. The 2.78× inflation at 80 % agreement is a **lower bound on the
   damage**. **Close by:** empirically estimating the error correlation on the gold
   set rather than assuming symmetry.
6. **⚠️ Open-weights judges are unvalidated** (§3.4). Self-hosting the judge would
   remove drift, per-call cost and the doc 00 §8.1 ToS exposure — but nobody has
   measured whether the repo's model classes reach usable human agreement as judges.
   **Close by:** running §3.3 calibration with a self-hosted judge alongside the
   frontier one on the first customer's gold set. Cheap, and potentially decisive for
   cost of goods.
7. **⚠️ Interleaving sensitivity is unquantified** (§4.4). Both primary sources I tried
   were unreachable (Netflix 403; MSR abstract only). **Close by:** obtaining the
   Chapelle et al. TOIS paper or an equivalent industrial report. **Do not quote a
   multiple meanwhile.**
8. **⚠️ CUPED is vendor-sourced only** (§2.9). The original exp-platform PDF returned
   unreadable binary. Mechanism and the "98.3 % of metrics saw a decrease" figure come
   from Statsig's docs, not the paper.
9. **⚠️ FDA non-inferiority guidance was not fetched** (§2.1) — landing page only. The
   assay-sensitivity and biocreep framings are my transfer of clinical-trial concepts,
   not quotations. **Close by:** fetching the 2016 guidance PDF. The transfers look
   sound but the platform should not cite FDA for them.
10. **⚠️ Difficulty-tiering proxies are unvalidated** (§6.4). No evidence that
    incumbent-failure, judge-uncertainty or structural proxies correlate with SME
    difficulty on a real customer task. If they do not, the difficulty slices are
    decorative. **Close by:** 200 SME-labelled items on the first engagement, rank
    correlation against each proxy.
11. **⚠️ Ragas ownership change** (§5.1). The canonical repo resolves under
    `vibrantlabsai/ragas` and the README directs consulting enquiries to
    `founders@vibrantlabs.com`. I could not confirm what happened. Worth knowing before
    depending on it; doc 00 §8.6's lock-in rule applies.
12. **⚠️ No public benchmark for enterprise per-adjudicated-example cost**
    (carried from doc 00 §4.3c, unchanged). §7/§8's SME-hour estimates are volume
    figures, not prices. **Close by:** the first engagement's actuals.
13. **⚠️ Customer acceptance of protocol-voiding events is untested** (§7.2, doc 00
    §2.2). "Your prompt changed, your parity claim is void" is a product decision with
    no evidence behind it. **Close by:** putting it in the first Parity Protocol and
    seeing whether it survives redlining.
14. **⚠️ W&B Weave pricing not published** on the page fetched (§5.1).
15. **⚠️ promptfoo Enterprise pricing not published** — "Custom" only (§5.1). The
    free-forever community tier including *all* eval features is unusual and worth
    confirming has no volume cap beyond the 10k red-team probes/month.

---

## Sources

All fetched **2026-09-19** by WebFetch or `curl` (WebSearch unavailable — see caveat).

**Statistics of evaluation and experimentation**
- Miller, *Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations* — https://arxiv.org/abs/2411.00640
- Anthropic, *A statistical approach to model evals* — https://www.anthropic.com/research/statistical-approach-to-model-evals
- Johari, Pekelis & Walsh, *Always Valid Inference* (mSPRT, always-valid p-values) — https://arxiv.org/abs/1512.04922
- Howard, Ramdas, McAuliffe & Sekhon, *Time-uniform, nonparametric, nonasymptotic confidence sequences* — https://arxiv.org/abs/1810.08240
- GrowthBook, *Sequential testing* (asymptotic confidence sequences, N\*) — https://docs.growthbook.io/statistics/sequential
- Statsig, *CUPED* — https://docs.statsig.com/stats-engine/methodologies/cuped
- Dror et al., *Statistical significance testing in NLP* (appendix) — https://arxiv.org/abs/1809.01448
- FDA, *Non-Inferiority Clinical Trials* (landing page; guidance PDF not fetched) — https://www.fda.gov/regulatory-information/search-fda-guidance-documents/non-inferiority-clinical-trials
- Chapelle et al., *Large-Scale Validation and Analysis of Interleaved Search Evaluation* (abstract only) — https://www.microsoft.com/en-us/research/publication/large-scale-validation-and-analysis-of-interleaved-search-evaluation/

**LLM judges**
- Zheng et al., *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena* — https://arxiv.org/abs/2306.05685
- Panickssery, Bowman & Feng, *LLM Evaluators Recognize and Favor Their Own Generations* — https://arxiv.org/abs/2404.13076
- Liu et al., *G-Eval* — https://arxiv.org/abs/2303.16634
- Dubois et al., *Length-Controlled AlpacaEval* — https://arxiv.org/abs/2404.04475
- *Arena-Hard / BenchBuilder* — https://arxiv.org/abs/2406.11939
- Chiang et al., *Chatbot Arena* — https://arxiv.org/abs/2403.04132
- *JudgeBench* — https://arxiv.org/abs/2410.12784
- *Judging the Judges* — https://arxiv.org/abs/2406.12624
- Chen, Zaharia & Zou, *How Is ChatGPT's Behavior Changing over Time?* — https://arxiv.org/abs/2307.09009

**Benchmarks — text, code, tools, long context**
- *RULER* — https://arxiv.org/abs/2404.06654
- *τ-bench* — https://arxiv.org/abs/2406.12045
- *τ²-bench* — https://arxiv.org/abs/2506.07982
- *BigCodeBench* — https://arxiv.org/abs/2406.15877
- EvalPlus / EvalPerf — https://raw.githubusercontent.com/evalplus/evalplus/master/README.md
- Berkeley Function Calling Leaderboard — https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/README.md
- *Platinum Benchmarks* (label noise) — https://arxiv.org/abs/2502.03461
- MMLU masked-option contamination — https://arxiv.org/abs/2311.09783

**Benchmarks — video**
- *Video-MME* — https://arxiv.org/abs/2405.21075
- *LongVideoBench* — https://arxiv.org/abs/2407.15754
- *MVBench* — https://arxiv.org/abs/2311.17005
- *TempCompass* — https://arxiv.org/abs/2403.00476
- *AuroraCap / VDC / VDCSCORE* — https://arxiv.org/abs/2410.03051
- *VideoScore / VideoFeedback* (generation-quality judge; contrast case) — https://arxiv.org/abs/2406.15252
- *TALL / Charades-STA* — https://arxiv.org/abs/1705.02101

**Eval frameworks and platforms**
- Inspect (UK AISI) — https://inspect.aisi.org.uk/ and https://inspect.aisi.org.uk/scorers.html
- lm-evaluation-harness — https://raw.githubusercontent.com/EleutherAI/lm-evaluation-harness/main/README.md
- lmms-eval — https://raw.githubusercontent.com/EvolvingLMMs-Lab/lmms-eval/main/README.md
- HELM — https://raw.githubusercontent.com/stanford-crfm/helm/main/README.md
- promptfoo pricing — https://www.promptfoo.dev/pricing/
- DeepEval — https://raw.githubusercontent.com/confident-ai/deepeval/main/README.md ; Confident AI pricing — https://www.confident-ai.com/pricing
- Ragas — https://raw.githubusercontent.com/explodinggradients/ragas/main/README.md
- Braintrust pricing — https://www.braintrust.dev/pricing ; evals guide — https://www.braintrust.dev/docs/guides/evals
- LangSmith pricing — https://www.langchain.com/pricing-langsmith
- Arize Phoenix — https://arize.com/docs/phoenix
- W&B Weave — https://wandb.ai/site/weave/
- Langfuse pricing — https://langfuse.com/pricing
- OpenAI Evals deprecation — https://developers.openai.com/api/docs/guides/evals

**Experimentation platforms**
- Statsig pricing — https://www.statsig.com/pricing
- Eppo / Datadog Experiments — https://www.geteppo.com/
- GrowthBook pricing — https://www.growthbook.io/pricing
- LaunchDarkly pricing — https://launchdarkly.com/pricing/

**Pricing inputs used in the worked examples**
- OpenAI API pricing — https://developers.openai.com/api/docs/pricing
- Claude pricing — https://claude.com/pricing
- Baseten pricing — https://www.baseten.co/pricing/

**Repo cross-references**
- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — legend, cost formulas, blended-price definition
- [`research/platform/00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) — goal, invariants, decomposition, prior art
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) — per-model per-GPU $/1M
- [`research/scaling/02-serving-stack-and-routing.md`](../scaling/02-serving-stack-and-routing.md) — gateways, routing, traffic splitting
- [`research/scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md) — SLOs, guardrail telemetry, quality-regression detection
- [`research/models/marlin2b/README.md`](../models/marlin2b/README.md) — 240-frame budget, 23,560-token prefill

---

## Verification log (2026-09-19)

Adversarial fact-check of this document. 25 load-bearing claims were selected
(paper results, product features and pricing, version/date claims, cost
arithmetic, statements about competitors, repo cross-references). **Every primary
source was opened independently — no citation in the text above was trusted on its
face** — and every derivation was recomputed with `python3`. Repo
cross-references were resolved by reading the referenced file.

**Result: 25 checked — 19 CONFIRMED, 5 CORRECTED, 1 UNVERIFIABLE.**
Note the method contrast with the caveat at the top of this document: that
research pass had no WebSearch; this verification pass re-fetched every source
directly and did not depend on search either, so the §5 exhaustiveness caveat and
Open Question 1 **still stand unchanged**.

### CONFIRMED (source opened, wording and numbers match)

| # | Claim | Source opened | Verdict |
|---:|---|---|---|
| 1 | Claude **Opus 5 $5 in / $25 out / $0.50 cache-read per 1M** (§8.1) | claude.com/pricing | ✅ exact. Cache **write** $6.25. Fable 5.1 $10/$50/$0.25 also confirmed, matching doc 00 §2.1's footnote |
| 2 | **GPT-6 Astra $10/$50 per 1M**, batch = 50 % (§1.5.3, §8.1) | developers.openai.com/api/docs/pricing | ✅ exact ($10 / $1 cached / $50; batch $5/$0.50/$25). **GPT-5.6-Luna also exists** ($0.20/$1.20), which substantiates §7.1 P2's "Luna/Haiku-class tier-down" rung |
| 3 | **OpenAI Evals**: read-only **2026-10-31**, shutdown **2026-11-30**, users pointed at Datasets (§5.1, §5.2) | developers.openai.com/api/docs/guides/evals | ✅ both dates verbatim |
| 4 | **HELM entered maintenance mode on June 1, 2026** (§5.1, §5.2) | raw README, stanford-crfm/helm | ✅ verbatim; MMLU-Pro/GPQA/IFEval/WildBench and "beyond accuracy (e.g. efficiency, bias, toxicity)" also verbatim |
| 5 | **"Eppo has been acquired by Datadog!"** (§4.6, §5.2) | geteppo.com | ✅ verbatim banner; no pricing published, as stated |
| 6 | Anthropic: **"clustered standard errors … over three times as large as naive standard errors"**; inter-model question-score correlation **"between 0.3 and 0.7"** (§2.3, §2.9, Implications 2) | anthropic.com/research/statistical-approach-to-model-evals | ✅ both verbatim. This is the document's "single highest-value statistical rule" and it holds |
| 7 | Statsig **CUPED**: "adjusting each user's metric value with that user's pre-exposure data"; **7-day** window; **"98.3% of metrics saw a decrease"**; >100 units and >5 % of units thresholds (§2.9) | docs.statsig.com/stats-engine/methodologies/cuped | ✅ all four verbatim (the page says "More than 5%"; the text's "≥5 %" is a harmless restatement) |
| 8 | Statsig pricing: Developer $0 (2M events, 50k replays), **Pro $150/mo** (5M then $0.05/1k), Enterprise warehouse-native-only (§4.6) | statsig.com/pricing | ✅ exact, including the warehouse-native tier restriction |
| 9 | **Langfuse**: Hobby $0 (50k units, 2 users, 30-day), **Core $29**, **Pro $199**, **Enterprise $2,499**, all 100k units; overage **$8 → $7 → $6.50 → $6** per 100k (§5.1) | langfuse.com/pricing | ✅ every figure exact |
| 10 | **Braintrust**: Starter $0 ($10 credits, 1 GB then $4/GB, 10k scores then $2.50/1k, 14-day); **Pro $249** ($100, 5 GB then $3/GB, 50k then $1.50/1k, 30-day then $0.50/GB/mo) (§5.1) | braintrust.dev/pricing | ✅ every figure exact |
| 11 | **Confident AI**: Free $0 (2 seats, 1 project, 5 runs/wk, 1 GB-mo), **Starter $200/mo**, **Team $2,000/mo**, spans **$1/GB-month** (§5.1) | confident-ai.com/pricing | ✅ exact |
| 12 | **LaunchDarkly**: Developer $0 (5k AI runs/mo); Foundation $10/service connection/mo, $8.33/1k client MAU/mo, $5 per additional 1k AI runs; **Online Evals (LLM-as-judge), offline evals and custom judges on all tiers; PII redaction Enterprise-only** (§4.6) | launchdarkly.com/pricing | ✅ exact — including the claim that matters commercially, that a flag vendor ships LLM-as-judge on its **free** tier |
| 13 | **promptfoo**: Community free forever incl. all eval features; **10k red-team probes/mo**; Enterprise/On-Prem custom, unpublished (§5.1, OQ 15) | promptfoo.dev/pricing | ✅ exact. Open Question 15's "worth confirming has no volume cap beyond the 10k probes" — the page shows no other cap, so the ⚠️ can be narrowed but not closed |
| 14 | **τ-bench**: pass^k introduced; SOTA agents "succeed on <50% of the tasks"; **"pass^8 <25% in retail"** (§1.3) | arxiv.org/abs/2406.12045 | ✅ verbatim |
| 15 | **RULER** (13 tasks; NIAH variants + multi-hop tracing + aggregation): "while these models all claim context sizes of 32K tokens or greater, only half of them can maintain satisfactory performance at the length of 32K", despite "nearly perfect accuracy in the vanilla NIAH test" (§1.4) | arxiv.org/abs/2404.06654 | ✅ verbatim |
| 16 | **Video-MME** 900 videos / **254 h** / **2,700 QA pairs**; 11 s–1 h; 6 domains / 30 subfields; frames + subtitles + audio; "rigorous manual labeling by expert annotators"; Gemini 1.5 Pro quote (§1.5.1, §1.5.3) | arxiv.org/abs/2405.21075 | ✅ every figure and both quotes verbatim. The ~3-authored-items-per-video planning anchor (2,700/900) is arithmetically sound |
| 17 | **LongVideoBench** 3,763 videos / **6,678** human-annotated MCQs / **17** categories / up to 1 h; referring reasoning; **"model performance on the benchmark improves only when they are capable of processing more frames"** (§1.5.1, §1.5.4) | arxiv.org/abs/2407.15754 | ✅ verbatim — and this is the load-bearing citation for §1.5.4's frame-budget argument, so it matters that it holds |
| 18 | **MVBench** "20 challenging video tasks that cannot be effectively solved with a single frame"; "existing MLLMs are far from satisfactory in temporal understanding"; **TempCompass** conflicting-videos construction and "notably poor temporal perception ability"; **AuroraCap/VDC** "over one thousand" structured captions, "transform long caption evaluation into multiple short question-answer pairs", Elo-validated (§1.5.1, §1.5.4, §3.5) | arxiv 2311.17005 / 2403.00476 / 2410.03051 | ✅ all verbatim (the paper spells the metric **VDCscore**, this document **VDCSCORE** — cosmetic) |
| 19 | **MT-Bench** "over 80% agreement, the same level of agreement between humans" (§3.1); **G-Eval** Spearman **0.514** + self-bias flag (§3.1); **JudgeBench** GPT-4o "just slightly better than random guessing" (§3.1); **Judging the Judges** "up to 5 points", "sensitivity to prompt complexity and length", "a tendency toward leniency", "judges with high percent agreement can still assign vastly different scores" (§3.1); **self-preference** linear correlation + "resists straightforward confounders" (§3.2); **ChatGPT drift 84 % → 51 %** with CoT-amenity explanation (§3.4); **platinum benchmarks** "pervasive label errors" (§3.6); **MMLU masked options 52 % / 57 %** (§6.5); **LC-AlpacaEval 0.94 → 0.98** + verbosity-robustness (§1.1.2); **RealHumanEval** "programmer preferences do not correlate with their actual performance" (§1.1.1); **BigCodeBench** 1,140 tasks / 139 libraries / 7 domains / 5.6 tests / 99 % branch coverage / 60 % vs human 97 % (§1.1.1, §5.1); **EvalPlus** 80×/35× and `v0.3.1` **2024-10-20** (§1.1.1, §5.1); **BFCL** "first comprehensive and executable function call evaluation" + v3 multi-turn + v4 web search / memory / **format sensitivity** (§1.1.1, §1.3, §5.1); **τ²-bench** Dec-POMDP dual-control + "significant performance drops" (§1.3); **Howard et al.** confidence-sequence definition (§2.5) | 17 arXiv abstracts + 2 raw READMEs, each opened individually | ✅ **all verbatim.** Every quotation in §§1, 3 and 6 that is attributed to a paper appears in that paper's abstract |
| 20 | **TALL / Charades-STA abstract does not define R@n, IoU or mIoU** (§1.5.2, OQ 3) | arxiv.org/abs/1705.02101 | ✅ the ⚠️ is **correct and should stay**. The abstract describes CTRL and the Charades-STA construction and states no metric formulas |
| 21 | **Ragas canonical repo resolves under `vibrantlabsai/ragas`** (§5.1, OQ 11) | raw.githubusercontent.com/explodinggradients/ragas → vibrantlabsai assets | ✅ confirmed: the README's own logo and badge URLs all point at `vibrantlabsai/ragas`. The ⚠️ stands |
| 22 | **Repo cross-ref**: Marlin-2B **240-frame cap ≈ 23,560 prefill tokens** (§1.5.3, §8.2) | `research/models/marlin2b/README.md` item 9 | ✅ verbatim ("the **240-frame cap** bounds every video request at ~23,560 prefill tokens no matter how long the clip is") |
| 23 | **Repo cross-ref**: Qwen3.8-27B blended **$0.0602/1M**, Marlin-2B blended **$0.0092/1M**, both B300 `low` on-demand (§8.1, §8.2) | `research/matrix/cost-matrix.md` §§ rankings; doc 00 §4.2 | ✅ both rows exact and mutually consistent across the three files |
| 24 | **All six statistical tables recompute exactly** — §2.2 (12 cells, two-proportion NI), §2.3 (16 cells, paired/McNemar), §2.4 (6 power cells + 3 tie cells + the 3,865 / 6,764 one-sided figures), §2.7 (rule of three, both directions, and 3/460,000 = **6.5e-6**), §3.1 (attenuation 1−2ε and inflation 1/(1−2ε)², all four rows) | `python3`, formulas as printed in the text | ✅ **every published cell reproduces to the stated rounding.** §2.4's pairwise numbers match the exact one-sample binomial form (8,116 / 4,904 / 3,604 / 1,294 / 783 / 319), not the cruder (z_α+z_β)²p₀(1−p₀)/Δ² form, which is the better choice |
| 25 | **§8.1 and §8.2 cost arithmetic**: incumbent **$47,600/mo** (4e9 @ $5 + 4e9 @ $0.50 + 1.024e9 @ $25); volumes 65,789/day, 2,741/h, 666,667 sessions; shadow 9.02B tok → **$543**; pairwise judge **$0.0652** list / $0.0326 batch; gold set 50 SME-h; calibration $65; canary $130; A/B judging $260; pairwise **$1,059 / $529**; A/B days-to-n at 5/10/50 % (2.6/1.3/0.3) and the δ=2 pp 5.9-day figure; video **$0.2506/video → $12,530/mo**, judge **$0.0360** and **$0.2576**, 7× ratio, **$984 / $492**, 250 SME-hours | `python3`, against the verified §1–2 prices | ✅ **all reproduce exactly.** §8's conclusions — "the entire evidence package costs under one day of the customer's inference spend", and "the GPU floor eats most of the saving" at 50K videos/month — both survive |

### CORRECTED (5)

1. **§2.6 / §6.7 — Holm inflation was ≈1.7×; it is ≈1.89×.** The document prints
   the formula `(z_{0.995}+z_{0.8})²/(z_{0.95}+z_{0.8})²` and then the wrong value
   for it: (2.5758+0.8416)² / (1.6449+0.8416)² = 11.679 / 6.183 = **1.889**. This
   **understated every per-slice sample-size budget by 11 %**, which is the kind of
   error that shows up as an underpowered slice claim in a signed protocol. Fixed in
   both places.
2. **§8.2 — "a single B300 replica at Baseten … for a B200" priced a GPU Baseten
   does not sell.** The sentence names B300 and then prices a B200. Baseten's
   dedicated table, re-read today, lists **B200 ($0.16633/GPU-min = $9.98/h) and
   H100 ($0.10833/min = $6.50/h) only — there is no B300 row.** $9.98 × 730 h =
   **$7,285/month** is right *for a B200*. Rewritten to say B200 and to state that
   no B300 price exists at this vendor.
3. **§2.9 / §4.6 — GrowthBook post-stratification is Enterprise-only, not Pro+.**
   The pricing table: Bayesian, frequentist, SRM detection and multiple-testing
   correction on **all** tiers; sequential testing and CUPED on **Pro+**;
   **post-stratification on Enterprise only**. Also added the seat caps the
   document omitted (Starter ≤3 users, Pro ≤30). This matters because §4.6 and §5.2
   recommend building on GrowthBook, so which tier a method sits in is a cost input.
4. **§2.5 — a quotation attributed to Johari, Pekelis & Walsh is not in the paper.**
   The phrase *"remain statistically valid regardless of when a user stops their test
   or how many times they peek at interim results"* appears nowhere in the abstract;
   it is a paraphrase presented inside quotation marks. The two *other* quotations in
   that bullet ("a natural interface for a sequential hypothesis test"; "has been
   implemented in a large scale commercial A/B testing platform…") **are** verbatim.
   Replaced with the abstract's own wording, which makes the same point more sharply.
   The **GrowthBook** sequential quote had the same defect at lower stakes ("produces
   wider confidence intervals than fixed-sample testing" → the page says "sequential
   analysis results in uniformly wider confidence intervals") and was also fixed.
5. **Four smaller factual and arithmetic slips, corrected inline:** (a) §1.1.2's
   "one of four named LLM-judge biases" — MT-Bench names three biases plus "limited
   reasoning ability", which is a capability limit, not a bias; (b) §2.3 attributed
   "confidence intervals, clustered standard errors, paired comparisons" to the
   lmms-eval **v0.7 changelog**, but it is a standing **design-principle** bullet in
   the README, and nothing fetched verifies the shipped code computes clustered SEs —
   now flagged ⚠️, which matters because §5.2 picks lmms-eval **for** that property;
   (c) §5.1's LangSmith "**$0.01** LCU/run" conflates a unit with a dollar — it is
   **0.01 LCU** per successful evaluation run ≈ **$0.015**; (d) §8.1's batch total
   "≈ $1,100" appears to have applied the judge batch discount to the **shadow** line,
   which is self-hosted inference and takes no such discount — the batch column is
   **$1,019**. Also corrected: "5× cheaper" for the paired rubric test is **4.3×**
   ($1,059 vs $248), and §1.5.3's "$142 … (§8.2)" pointed at a section that contains
   no such figure (§8.2 sizes the same eval on a different, paired basis at $138).

### UNVERIFIABLE (1)

**§8.1 — the $75,012/month teacher-labelling contrast does not reconstruct.** The
text says "teacher-labelling that same traffic at Opus 5 prices would be `est.`
**$75,012/month**", but states no annotation prompt shape. At the verified Opus 5
prices, $75,012 / 2M requests = $0.037506/item, which implies ~4,000 uncached input
+ ~700 output tokens. That is neither the section's own request shape (4,000 in /
512 out → **$65,600**) nor its judge shape (4,512 in / 300 out → **$55,120**), and
no combination in the document produces $75,012. Flagged inline rather than
rewritten, because **the argument it supports is robust to every candidate basis**:
on all of them, labelling 100 % of traffic with the teacher costs more than the
incumbent's entire inference bill, which is exactly why annotation samples.
The figure needs a stated basis before it appears in any customer-facing material.

### Checked and deliberately left alone

- **The eleven ⚠️ Open Questions are all still open and all still correctly marked.**
  The verification pass closed none of them: OQ 2 (TimeLens) and OQ 1
  (survey exhaustiveness) both need WebSearch, which this pass also did not use;
  OQ 3 was **positively confirmed** as unresolved by reading the TALL abstract;
  OQ 11 (Ragas ownership) was confirmed as a real anomaly.
- **§2.1's FDA caveat is exemplary and should be the house style.** The document
  fetched only the landing page, says so, and explicitly labels assay sensitivity
  and biocreep as its own inference rather than FDA's. No change needed.
- **§2.5's peeking table** is simulation output and was re-simulated rather than
  recomputed; see the ⚠️ added inline. The conclusion (50 looks ⇒ roughly one in
  three null experiments "wins") reproduces; two intermediate rows do not, so the
  table is now labelled an illustration rather than a calibration.
