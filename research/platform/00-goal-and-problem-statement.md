# Goal and problem statement — replacing large production models with distilled specialists in a closed loop

Research date **2026-09-19**. This document is the frame every other document in
`research/platform/` builds on: it states what the platform is, what the customer
is actually buying, what the evidence says about whether the core bet works, what
it costs, where it is hard, and how the work splits into components 01–09.

**Conventions.** Legend, cost formulas and the `low`/`high`/`res1y` price tiers are
[`research/METHODOLOGY.md`](../METHODOLOGY.md) — this document does not re-derive
them. Self-hosting $/1M figures are named rows from
[`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) and
[`research/matrix/recommendations.md`](../matrix/recommendations.md); serving,
autoscaling, cold-start and utilisation economics are
[`research/scaling/`](../scaling/); per-model architecture is
[`research/models/`](../models/). The repo's five models — DeepSeek-V4.1-Flash,
its NVFP4 build, Qwen3.8-27B, Kimi-K3 and the Marlin-2B video VLM — are the
candidate *students* and *self-hosted incumbents* throughout.

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source (vendor pricing/docs, paper, repo, official blog), fetched 2026-09-19. |
| **⚠️ TO BE VERIFIED** | No primary source found, or the claim is an inference; the reasoning is stated inline. |
| `est.` | Arithmetic from METHODOLOGY formulas or from sourced inputs; shown, not measured. |
| `meas.` | A published measurement, cited. |

> **Research-method caveat, stated up front.** This document was produced with
> WebFetch against primary sources only — the session's web-*search* budget was
> exhausted before this agent started, so sources were reached by known URL and by
> following links from fetched pages. The consequence: **the competitor survey in
> §7 is a survey of platforms I could name and fetch, not an exhaustive market
> scan.** Anything that exists but that I could not name is missing, not
> disproven. Every §7 entry below was fetched; none was recalled from memory.
> Treat §7 as a floor on the landscape, and see the first Open Question.

---

## 1. The goal restated as a system

### 1.1 Actors

| Actor | Who they are | What they want | What they can be held to |
|---|---|---|---|
| **Platform team** (us) | Operators of the closed loop | Many customer loops running unattended; GPU fleet utilised; no incident caused by an automatic promotion | Uptime, per-tenant isolation, cost of goods |
| **Customer team** (buyer) | ML/platform engineers + the product owner of one feature | A drop-in replacement for their frontier API call that is cheaper and faster at equal quality | A quality bar they must define, and traffic they must let us capture |
| **Customer's end users** | The people hitting the feature | Nothing changes for them | Nothing — they never opt into being an experiment subject, which is a governance constraint (§8) |
| **Teacher model vendor** | OpenAI / Anthropic / Google / an open-weights host | Paid per token | Their terms of service, which explicitly restrict what we are building (§8.1) |

The commercially load-bearing fact: **the customer team is the only actor who can
define "parity", and they are also the actor with the least incentive to define it
precisely.** Everything in §5 follows from that.

### 1.2 The loop as a state machine

The platform is one cycle with nine stages. Each edge is a contract — an artifact
with a schema, an owner and a failure mode — not a vibe.

```
                     ┌──────────────────────────────────────────────────┐
                     │                                                  │
                     v                                                  │
   [S1 TRAFFIC] → [S2 TRACES] → [S3 ANNOTATION] → [S4 DATASETS+EVALS]   │
   endpoints        capture       teacher labels    train/dev/test       │
   main + dev       + PII scrub   + human review    + eval suite         │
                                                         │              │
                                                         v              │
                                    [S5 TRAINING] ← ─────┘              │
                                    SFT / DPO / RL / distillation       │
                                            │                           │
                                            v                           │
                                    [S6 CANDIDATE CHECKPOINT]           │
                                            │                           │
                                            v                           │
                                    [S7 OFFLINE EVAL GATE] ─ fail ─→ back to S3/S5
                                            │ pass                      │
                                            v                           │
                                    [S8 HW OPTIMIZATION]                │
                                    quantise / engine / kernel / SD     │
                                            │                           │
                                            v                           │
                                    [S9 ONLINE A/B + ROLLOUT] ──────────┘
                                    dev endpoint → shadow → % → main
```

Per-stage contract:

| Stage | Input | Output | Owner | Primary failure mode |
|---|---|---|---|---|
| **S1 Traffic** | End-user request | Request/response + metadata | Customer's app, via our SDK/gateway | Gateway adds latency or becomes a SPOF for the customer's production path |
| **S2 Traces** | S1 | Immutable trace rows: prompt stack, tools, tool results, output, tokens, latency, cost, user feedback, outcome signal | Platform | PII lands in the store; sampling drops the tail that matters; schema drift between SDK versions |
| **S3 Annotation** | S2 sample | Labelled examples: teacher completion, preference pair, rubric score, rationale, human adjudication | Platform + customer SMEs | Teacher errors silently become ground truth; ToS violation (§8.1); annotation cost exceeds inference savings |
| **S4 Datasets + evals** | S3 | Versioned, split, deduplicated datasets; a frozen eval suite with a held-out test split | Platform, customer signs off | Test set leaks into train (contamination, §8.4); eval does not reflect production distribution |
| **S5 Training** | S4 + base model | Candidate checkpoint + training run record | Platform | Overfits the teacher's style; catastrophic forgetting of tool use / format compliance |
| **S6 Checkpoint** | S5 | Immutable versioned artifact (weights + tokenizer + chat template + serving config) | Platform | Checkpoint is not reproducible; serving config drifts from training config |
| **S7 Offline gate** | S6 + S4 test split | Pass/fail per criterion + score deltas | Platform, thresholds set by customer | Judge is the bottleneck, not the model (§5.1) |
| **S8 HW optimization** | S6 | Optimised serving artifact for target GPU (format, engine, parallelism, speculative decoding) + measured throughput/latency/cost | Platform ([`research/matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md)) | Quantisation silently shifts quality after the gate passed — the gate must re-run on the *served* artifact, not the checkpoint |
| **S9 Online A/B** | S8 artifact | Production traffic share; decision to promote or roll back | Customer approves, platform executes | Underpowered test declared a win; non-stationary traffic; rare-failure tail never sampled (§5) |

**Invariant: the eval gate must run against the artifact that will actually
serve.** S8 sits *between* S7 and S9 in the diagram, and that is a trap: NVFP4 or
FP8 weights, a different attention kernel, or speculative decoding all change
outputs. The repo already documents per-GPU format substitutions that change what
executes ([`matrix/fit-matrix.md`](../matrix/fit-matrix.md)). The correct
discipline is **gate twice** — once on the BF16 checkpoint (does training work?)
and once on the deployed artifact (does the optimisation preserve it?).

### 1.3 Invariants

These hold at every stage or the product is not sellable.

| Invariant | Statement | How it is enforced | Where covered |
|---|---|---|---|
| **I1 Quality parity** | The candidate is non-inferior to the incumbent on the customer's frozen eval, at a stated margin and confidence | S7 gate + S9 online test | §5, doc 04, doc 07 |
| **I2 Cost** | Blended $/1M of the served candidate < incumbent, *including* amortised annotation + training + eval + idle GPU | Cost model, re-checked at every promotion | §4, doc 06, [`scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md) |
| **I3 Latency** | p50 **and** p99 TTFT/TPOT no worse than incumbent at the customer's concurrency | S8 measurement + S9 online | doc 01, [`scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md) |
| **I4 Contract fidelity** | The API contract (schema, tool-call shape, refusal behaviour, streaming semantics) is byte-compatible enough that the customer changes one base URL | Contract conformance suite, run as part of S7 | §2.3, doc 01 |
| **I5 Safety** | The distilled model does not regress on refusals, jailbreak resistance or PII leakage relative to incumbent | Safety eval is a *separate, non-negotiable* gate, never traded against quality | §3.4, doc 08 |
| **I6 Privacy** | Customer traffic never trains another tenant's model; PII is redacted before annotation; residency honoured | Tenant-scoped storage + redaction before any egress to a teacher | §8.2, doc 08 |
| **I7 Reversibility** | Any promotion is revertible to the previous main version in under one minute, with no data loss | Versioned endpoints + traffic split control plane | doc 01, doc 07 |

**I7 is the one that makes the rest sellable.** A customer will accept an
imperfect eval if rollback is instant and cheap; they will not accept a perfect
eval with a one-hour rollback.

---

## 2. The customer's starting point

### 2.1 What "GPT-5.6 / Opus-5 / Fable-5.1 in production" actually is

The incumbents named by the platform owner are real, current, first-party API
models as of 2026-09-19:

| Named incumbent | Real product | Input $/1M | Cached input $/1M | Output $/1M | Source |
|---|---|---:|---:|---:|---|
| "GPT-Astra" | **GPT-6 Astra** | $10.00 | $1.00 | $50.00 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| "GPT-5.6" | **GPT-5.6 Sol** | $4.00 | $0.40 | $20.00 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| — | **GPT-5.6 Terra** | $2.00 | $0.20 | $12.00 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| — | **GPT-5.6 Luna** | $0.20 | $0.02 | $1.20 | [ibid.](https://developers.openai.com/api/docs/pricing) |
| "Opus-5" | **Claude Opus 5** | $5.00 | $0.50 (read) | $25.00 | [Claude pricing](https://claude.com/pricing) |
| "Fable-5.1" | **Claude Fable 5.1** | $10.00 | $0.25 (read) | $50.00 | [ibid.](https://claude.com/pricing) |
| — | **Claude Sonnet 5** | $2.00 | $0.20 (read) | $10.00 | [ibid.](https://claude.com/pricing) |
| — | **Claude Haiku 4.5** | $1.00 | $0.10 (read) | $5.00 | [ibid.](https://claude.com/pricing) |

Both vendors offer **50 % off for batch processing**
([OpenAI](https://developers.openai.com/api/docs/pricing),
[Anthropic](https://claude.com/pricing)). Anthropic's cache *write* is priced
above base input ($6.25/MTok on Opus 5, $12.50 on Fable 5.1
[[src](https://claude.com/pricing)]) — a detail that matters because a customer
with a badly-structured prompt stack may be paying *more* than list, not less.

The customer's "setup" is not one API call. In practice it is:

1. **A prompt stack** — a system prompt of 1–20k tokens (policies, persona, format
   rules, few-shot examples), often assembled per request from templates, and
   usually never re-tuned since it was written for a *previous* model generation.
2. **Tool / function definitions** — 5–50 JSON-Schema tools, with a tool-choice
   policy, parallel-call behaviour, and error-result conventions the app depends on.
3. **Structured outputs** — a response schema the downstream code parses. Any
   deviation is a hard failure, not a quality regression.
4. **RAG** — retrieval inserted between system and user turns; retrieval quality
   is a confound that will be blamed on the model.
5. **Agent loops** — multi-turn tool use where errors compound; one bad tool call
   at turn 3 destroys turn 9.
6. **Multimodal / video** — images or video frames in the request. For video this
   is where the repo's Marlin-2B work lands: a 240-frame cap bounding every request
   at ~23,560 prefill tokens regardless of clip length
   ([`models/marlin2b/README.md`](../models/marlin2b/README.md) §9).
7. **SLAs** — a p99 latency number and an availability number they already promise
   someone else.
8. **Caching and batching already in use** — see §4.4.

### 2.2 The prompt-stack problem nobody budgets for

A prompt written for GPT-5.6 Sol is not a specification of the task; it is a
specification of *how to get that model to do the task*. Distillation transfers
the input→output mapping, so a distilled student inherits the prompt stack's
workarounds as if they were requirements. Two consequences:

- The student must be trained with the *same* prompt stack it will be served with,
  or the distribution shifts between S5 and S9. This makes the prompt stack part of
  the versioned artifact (doc 01), not a customer-side variable.
- Any customer prompt change after deployment is a **silent distribution shift**
  that invalidates the eval. The platform must detect it (prompt-hash in every
  trace) and refuse to claim parity across it. ⚠️ **TO BE VERIFIED** that customers
  will accept a "your prompt changed, your parity claim is void" mechanic; it is a
  product decision, not a technical one.

### 2.3 What must be preserved when swapping the model (invariant I4)

| Surface | What must match | What breaks quietly if it doesn't |
|---|---|---|
| Request schema | OpenAI-compatible or Anthropic-compatible body; same role semantics | Customer SDK throws; caught immediately (the good case) |
| Tool-call shape | Same JSON encoding of arguments; same parallel-call packaging | Arguments parse but with different escaping → downstream corruption. OpenAI-compatible engines and Anthropic differ here; **always `json.loads` rather than string-match** |
| Structured output | Schema-valid 100 % of the time, not 99.5 % | Tail of malformed responses shows up as a product bug weeks later |
| Streaming | Same event types and ordering; same TTFT profile | UI feels broken even when quality is equal |
| Refusal behaviour | Same *shape* of refusal (the frontier models return a distinct stop reason) | An app that branches on refusals silently takes the wrong branch |
| Context limit | ≥ incumbent's effective limit at the customer's p99 prompt length | Long-tail requests fail only in production |
| Determinism/temperature | Same sampling defaults | Eval scores shift for reasons unrelated to the model |

Point 1 is where the repo's existing work connects: the
[`cross-cutting/inferencex-api.md`](../cross-cutting/inferencex-api.md) and
[`scaling/02-serving-stack-and-routing.md`](../scaling/02-serving-stack-and-routing.md)
documents already cover the serving surface; doc 01 will extend them with
versioning and contract conformance.

---

## 3. Why distillation to a specialist can work

### 3.1 The evidence base, ordered by what it actually proves

| Result | Claim proven | Numbers | Limits |
|---|---|---|---|
| **Orca** (13B student, GPT-4 + ChatGPT teachers, Jun 2023) [[src](https://arxiv.org/abs/2306.02707)] | Imitating *explanation traces*, not just answers, closes much of the gap | Beats Vicuna-13B by >100 % on BBH, +42 % on AGIEval; "parity with ChatGPT" on BBH; ~4-point gap to optimised ChatGPT on professional exams | Generalist imitation; still trails GPT-4 |
| **phi-1** (1.3B, textbook-quality synthetic data, Jun 2023) [[src](https://arxiv.org/abs/2306.11644)] | Data *quality* substitutes for scale in a narrow domain | 50.6 % HumanEval, 55.5 % MBPP pass@1, from ~7B tokens, 4 days × 8 A100 | One domain (Python); says nothing about breadth |
| **Zephyr-7B** (dSFT + AI-feedback preferences + dDPO, Oct 2023) [[src](https://arxiv.org/abs/2310.16944)] | Preference distillation without human labels works at 7B | "surpasses Llama2-Chat-70B, the best open-access RLHF-based model" on MT-Bench | Chat quality, judged by an LLM; not task accuracy |
| **STaR** (bootstrapped rationales, Mar 2022) [[src](https://arxiv.org/abs/2203.14465)] | A model can generate its own training data if answers are verifiable | "performs comparably to fine-tuning a 30× larger" model on CommonsenseQA | Needs a verifier; degenerates without one |
| **MiniLLM** (reverse-KL distillation, 120M–13B, Jun 2023) [[src](https://arxiv.org/abs/2306.08543)] | Reverse KL beats forward KL for generative students — stops the student "overestimating the low-probability regions of the teacher distribution" | Better precision, lower exposure bias, better calibration | Objective-level result; no single headline benchmark |
| **GKD / on-policy distillation** (Agarwal et al., ICLR 2024) [[src](https://arxiv.org/abs/2306.13649)] | Training on the student's *own* rollouts, graded by the teacher, fixes the train/serve distribution mismatch | Method paper; abstract reports no single headline number | — |
| **RLAIF** (Sep 2023) [[src](https://arxiv.org/abs/2309.00267)] | AI preference labels reach RLHF-comparable results; `d-RLAIF` skips the reward model entirely | "RLAIF achieves comparable performance to RLHF" on summarisation, helpful and harmless dialogue | Teacher's biases become the reward function |
| **Thinking Machines, on-policy distillation** (27 Oct 2025) [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)] | The compute case, measured | Qwen3-8B-Base student / Qwen3-32B teacher: AIME'24 **60 % (post-SFT, 400k prompts) → 74.4 %** in ~150 steps; **1,800 GPU-hours vs 17,920** for an RL baseline reaching **67.6 %**; **9–30×** cheaper than SFT (9× when the SFT dataset already exists, 30× when the teacher cost for a new task is included); personalisation case recovered IF-eval **79 % → 83 %** after mid-training had dropped it from an **85 %** baseline, while internal-QA **rose 36 % → 41 %** | Vendor blog, single model family; the **9–30×** figure is a compute-reduction claim, not an independent replication |
| **Survey of LLM KD** (Feb 2024, rev. Oct 2024) [[src](https://arxiv.org/abs/2402.13116)] | The taxonomy: algorithm (SFT, divergence/similarity, RL, rank optimisation) × skill distillation × verticalisation | 43-page survey | Survey, not evidence |
| **NVIDIA data-flywheel case** (blueprint repo) [[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)] | End-to-end, on production traffic, the replacement can be radical | Internal HR chatbot: a fine-tuned **Llama 3.2 1B reached ≈98 % of the production Llama 3.1 70B's accuracy** on the *tool-calling* task, with inference cost "reduce[d] … by up to 98.6 %"; separately `Qwen-2.5-32b-coder` "did as well as `Llama-3.1-70b-instruct` without any fine-tuning", cutting cost **and time-to-first-token** by >50 % | Vendor-claimed, one workload, and NVIDIA's own caveat scopes it to *"simpler tool calling use cases where an agent is using a tool call to route between a small set of tools"* — **not** a general parity result. Blueprint **deprecated as of April 2026** (§7) |

**The synthesis.** The claim "a small specialist can match a large generalist *on
one task*" is about as well supported as anything in applied ML gets: it holds
across explanation-trace imitation (Orca), data curation (phi-1), preference
distillation (Zephyr), self-bootstrapping (STaR), objective choice (MiniLLM),
on-policy correction (GKD), AI feedback (RLAIF), and at least one industrial
end-to-end case (NVIDIA). The thing that is *not* proven is any of it happening
**automatically, on an arbitrary customer's task, without an expert in the loop.**
That gap is the product.

### 3.2 The method the platform should default to, and why

On-policy distillation is the right default, and the reason is mechanical, not
fashionable:

- **Mechanism.** Sample trajectories from the *student* on the customer's real
  prompts; ask the *teacher* for per-token logprobs on those exact trajectories;
  train the student with per-token reverse KL against the teacher
  [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)]. Reverse KL is
  mode-seeking, which is what you want when you are trying to make a small model
  *reliably do one thing* rather than cover a big distribution
  [[src](https://arxiv.org/abs/2306.08543)].
- **Inputs.** Base student checkpoint; a prompt set drawn from S2 traces; a teacher
  that will return logprobs.
- **Outputs.** A student checkpoint, plus a dense per-token disagreement signal
  that is *itself* a diagnostic — the tokens where student and teacher diverge most
  are the best candidates for human review in S3.
- **Cost.** Dense supervision on every token of the student's own output, so it
  needs far fewer episodes than RL: 1,800 vs 17,920 GPU-hours in the published
  comparison [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)].
- **Failure modes.** (a) **It requires teacher logprobs.** Frontier APIs do not
  generally expose per-token logprobs for arbitrary continuations — ⚠️ **TO BE
  VERIFIED** per vendor; if unavailable, the method degrades to rejection-sampling
  fine-tuning or to a preference/rubric signal, both weaker per unit of teacher
  spend. (b) It optimises toward the teacher, so it **inherits the teacher's
  errors and cannot exceed the teacher** on anything the teacher gets wrong —
  unless a verifier is available, in which case STaR-style filtering can.
  (c) Every training step costs teacher tokens, so annotation spend scales with
  *training* steps, not with dataset size.

The pragmatic ladder, cheapest first, and the platform should climb it only as far
as the eval demands:

1. **Prompt + model swap only.** Try GPT-5.6 Luna ($0.20/$1.20) or Haiku 4.5
   ($1/$5) with the customer's existing prompt before training anything. This is
   free to test and sometimes ends the engagement — which is fine, because the
   customer still wins and we still sold the eval harness.
2. **Rejection-sampling SFT.** Teacher generates N completions on real prompts;
   keep the ones a verifier or rubric passes; SFT the student. No logprobs needed.
3. **Off-policy SFT on teacher traces** (the Orca pattern), including rationales.
4. **DPO / preference distillation** on teacher-ranked pairs (the Zephyr pattern).
5. **On-policy distillation** (above).
6. **RL against a verifiable reward**, where one exists.

### 3.3 Where it fails

| Failure | Why | Detection | Mitigation |
|---|---|---|---|
| **Long-tail reasoning** | Rare hard cases are rare in traffic, so they are rare in the distilled dataset by construction | Stratified eval slices, not aggregate score | Oversample hard slices in S3; keep a frontier fallback route for low-confidence requests |
| **Tool-use breadth** | A 2–30B student holds fewer tool schemas in working memory; degrades as the tool count grows | Per-tool accuracy, not aggregate | Cap the student's tool surface; route tool-heavy requests to the incumbent |
| **Multi-turn compounding** | Single-turn parity does not imply session parity; errors compound | Session-level eval (§5.4) | Train on full sessions, not turns |
| **Distribution shift** | Customer's traffic mix drifts; the student was trained on last quarter's | Drift monitor on the trace store (doc 02/03) | Continuous re-annotation; the loop's whole reason to exist |
| **Style over substance** | Distillation transfers surface form fastest; a judge that rewards style will pass a student that is wrong in the right voice | Judge validation against human labels (§5.1) | Rubric graders + programmatic checks, not preference-only |
| **Safety regression** | Refusal behaviour is a small fraction of tokens, so it is under-represented in the distillation signal | A *separate* safety eval, always run | Safety data explicitly oversampled; see I5 |
| **Quantisation drift** | The served artifact ≠ the gated checkpoint | Gate twice (§1.2) | Re-run the gate on the serving artifact |

### 3.4 Video understanding specifically

Nothing in the evidence base above is a video result. Video-understanding
distillation is the part of the thesis with the thinnest public support:

- The benchmark exists and is hard: **Video-MME**, 900 videos / 254 hours / 2,700
  QA pairs, clips from **11 seconds to 1 hour**, frames + subtitles + audio
  [[src](https://arxiv.org/abs/2405.21075)].
- Frontier video generation is priced per second, not per token — Sora-2 at
  $0.10/s, and Sora-2-pro at $0.30/s (720p), $0.50/s (1024p) and $0.70/s (1080p)
  [[src](https://developers.openai.com/api/docs/pricing)] — which tells us the
  *generation* side is metered differently, but says nothing about understanding.
- The repo's video student, Marlin-2B, has **no published throughput or latency
  measurement on any hardware**, and does not load in any engine as shipped
  ([`models/marlin2b/README.md`](../models/marlin2b/README.md) §2, §10).

⚠️ **TO BE VERIFIED — the single biggest evidence gap in this programme.** I found
no published result showing task-specific distillation of a frontier
video-understanding model into a small VLM at parity. The reasoning for why it may
still work: video understanding as customers use it is usually *narrow*
(is-this-defect-present, what-happened-in-this-clip, classify-this-ad), the frame
budget dominates cost, and the repo's own analysis shows the 240-frame cap makes a
10-minute clip cost the same as a 2-minute one — so the economics are unusually
favourable *if* quality holds. The reasoning for why it may not: temporal
reasoning over long clips is exactly the "long-tail reasoning" failure above, and
evaluating it is harder than evaluating text (§5.5). **Doc 04 must treat video
evals as a first-class, separately-resourced problem, not a variant of text.**

---

## 4. The economics

### 4.1 Incumbent blended price

Using METHODOLOGY §6's blended definition (75 % input, half of it cached at 10 %,
25 % output → `0.4125 × c_in + 0.25 × c_out`), so that these numbers are directly
comparable to the repo's self-hosting grids. `est.`, from the sourced list prices
in §2.1:

| Incumbent | Blended $/1M | Per request (4K in, 50 % cached, 512 out) | Per 1,000 requests |
|---|---:|---:|---:|
| GPT-6 Astra | $16.625 | $0.0476 | **$47.60** |
| Claude Fable 5.1 | $16.344¹ | $0.0461 | **$46.10** |
| Claude Opus 5 | $8.3125 | $0.0238 | **$23.80** |
| GPT-5.6 Sol | $6.650 | — | — |
| GPT-5.6 Terra | $3.825 | $0.0105 | **$10.54** |
| Claude Sonnet 5 | $3.325 | — | — |
| Claude Haiku 4.5 | $1.6625 | — | — |
| GPT-5.6 Luna | $0.3825 | — | — |

¹ Fable 5.1's cache read is $0.25/MTok = 2.5 % of input, not the 10 % the blended
formula assumes, so its true blended figure is **$16.344**, not $16.625; the
per-request column uses the real $0.25 rate. ⚠️ Note the methodology conflict:
[`METHODOLOGY.md`](../METHODOLOGY.md) §6 says vendor-API comparisons must use the
vendor's *published* cached-input ratio and "never a generic 10 %". The generic
10 % is harmless for GPT-6/GPT-5.6, Opus 5, Sonnet 5 and Haiku 4.5 (all exactly
10 % of input) and wrong only for Fable 5.1, corrected here.

### 4.2 The distilled alternative

From this repo's own grids ([`matrix/cost-matrix.md`](../matrix/cost-matrix.md),
[`README.md` §3](../README.md)), `low`-tier on-demand, self-hosted:

| Student | Best blended $/1M | Best interactive $/1M out | Min GPUs | Where |
|---|---:|---:|---:|---|
| **Marlin-2B** (video) | **$0.0092** (B300) | $0.022 (B300) | 1 | [`models/marlin2b/`](../models/marlin2b/README.md) |
| **Qwen3.8-27B** | **$0.0602** (B300) | $0.159 (H200) | 1 | [`models/qwen3827b/`](../models/qwen3827b/README.md) |
| **DeepSeek-V4.1-Flash-NVFP4** | $0.1485 (B200) | $0.565 (B200) | 4 | [`models/deepseek41fnvfp4/`](../models/deepseek41fnvfp4/README.md) |
| **DeepSeek-V4.1-Flash** | $0.190 (B200) | $0.462 (B200) | 2 | [`models/deepseek41f/`](../models/deepseek41f/README.md) |
| **Kimi-K3** | $2.3811 (B300) | $7.3926 (B300) | 8 | [`models/kimik3/`](../models/kimik3/README.md) |

Serverless third-party alternatives to self-hosting, for the same student class
(so that the customer has a middle option with no GPU floor):

| Model API | Input | Cached | Output | Blended `est.` | Source |
|---|---:|---:|---:|---:|---|
| DeepSeek V4.1 Flash | $0.30 | $0.03 | $1.20 | $0.424 | [Baseten](https://www.baseten.co/pricing/) |
| GLM-5.3-Flash | $0.15 | $0.03 | $0.50 | $0.193 | [ibid.](https://www.baseten.co/pricing/) |
| GLM-5.3 | $1.40 | $0.14 | $4.40 | $1.678 | [ibid.](https://www.baseten.co/pricing/) |
| DeepSeek V4 Pro | $1.74 | $0.145 | $3.48 | $1.577 | [ibid.](https://www.baseten.co/pricing/) |
| Kimi K3 | $3.00 | $0.30 | $15.00 | $4.988 | [ibid.](https://www.baseten.co/pricing/) |

Blended here uses each row's **listed** cached rate, per METHODOLOGY §6 (the
GLM-5.3-Flash and DeepSeek V4 Pro rows previously used a generic 10 % and read
$0.187 / $1.588).

**The headline spread.** Replacing Claude Opus 5 ($8.3125 blended) with a
self-hosted Qwen3.8-27B ($0.0602 blended) is a **138× reduction** in marginal
token cost; replacing GPT-6 Astra is **276×**; replacing GPT-5.6 Terra is **64×**.
These are the numbers that make the business exist. They are also the numbers that
are most misleading, for the reasons in §4.4 and §4.5.

### 4.3 Break-even arithmetic

The one-time cost of one loop iteration has three parts.

**(a) Annotation.** Teacher tokens spent labelling. For 100,000 examples at
4K in / 512 out with GPT-6 Astra: `100,000 × (4,000×$10 + 512×$50)/1e6` = **$6,560**,
or **$3,280 with the Batch API's 50 % discount**
[[src](https://developers.openai.com/api/docs/pricing)] (`est.`). Annotation is
cheap; this is the least of the three, and the intuition that it dominates is wrong.

**(b) Training.** Priced from a real 2026 training API rather than guessed. Tinker
charges **$4.103/1M tokens to train Qwen3.8-27B**, $1.86 prefill (the $0.372 figure
is the *cached*-prefill rate, an 80 % discount on list) and $5.595 sample
[[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]. 100k examples ×
4,512 tokens × 3 epochs = 1.354B tokens → **≈ $5,554** (`est.`). Checkpoint storage
is $0.10/GB-month [[ibid.](https://tinker-docs.thinkingmachines.ai/tinker/models/)].

**(c) Evaluation and human review.** The part that is not a line item on any
vendor's price list and is usually the largest. §5.3 sizes the *statistical*
requirement; the *cost* is SME hours, which is customer-specific. ⚠️ **TO BE
VERIFIED** — no public benchmark for what an enterprise pays per adjudicated
example; Snorkel's positioning ("calibrated expert review trained against gold
standards", "full audit trails for label provenance"
[[src](https://snorkel.ai/)]) is evidence that this is sold as a premium service,
not that it is cheap.

**Amortisation**, against the marginal saving per 1M blended tokens (`est.`):

| Incumbent replaced | Saving/1M vs Qwen3.8-27B @ $0.0602 | $10k one-time | $50k one-time | $250k one-time |
|---|---:|---:|---:|---:|
| GPT-6 Astra | $16.56 | 0.60B tok (134k req) | 3.0B tok (669k req) | 15.1B tok (3.3M req) |
| Claude Opus 5 | $8.25 | 1.21B tok (269k req) | 6.1B tok (1.34M req) | 30.3B tok (6.7M req) |
| GPT-5.6 Terra | $3.76 | 2.66B tok (589k req) | 13.3B tok (2.9M req) | 66.4B tok (14.7M req) |

Requests at 4,512 tokens each. **Read this table as a qualification filter**: a
customer replacing a Terra-class model at under ~600k requests should not be sold
the full loop, only the eval harness and a prompt/model swap (ladder rung 1 in §3.2).

### 4.4 The incumbents' counter-moves — why the 138× is not what the customer sees

Three levers the incumbent vendors already ship, all of which shrink the gap
*before* we do anything:

1. **Cached input.** 10 % of input price at OpenAI, and as low as 2.5 % on Claude
   Fable 5.1 [[src](https://claude.com/pricing)]. A customer with a 15k-token
   system prompt and a 500-token user turn is paying near-cache rates on ~97 % of
   their input. The blended formula above already assumes half the input is cached;
   a well-tuned customer is closer to 90 %, which cuts an Opus 5 blend from
   $8.3125 to `est.` **$6.96/1M** — still 116× our number, but the framing changes.
   (The floor is $6.625 at 100 % cached input: output is 75 % of the blend and no
   amount of caching touches it.)
2. **Batch API, 50 % off**, on both vendors. If the customer's workload tolerates
   asynchrony, their real incumbent price halves. Opus 5 batch blended is `est.`
   **$4.16/1M**. Any pitch that quotes list price against a customer who batches is
   a pitch that will be caught.
3. **Tier-down within the incumbent's own family.** GPT-5.6 Luna at $0.3825 blended
   and Haiku 4.5 at $1.6625 blended are one config change away. **The honest
   competitive comparison for a distilled specialist is not Astra or Opus 5 — it is
   Luna/Haiku-class at $0.38–$1.66 blended, batched, with cached prompts.** Against
   *that*, the self-hosted Qwen3.8-27B at $0.0602 is 6–28× cheaper, not 138×.
   That is still a real business. It is a different pitch.

### 4.5 The GPU floor — the thing that breaks the per-token story

Self-hosted $/1M assumes a busy GPU. A dedicated replica is billed by the hour
whether or not traffic arrives. Real 2026 anchors: **Baseten dedicated B200 at
$0.16633/GPU-minute = $9.98/GPU-hour ≈ $7,285/month**; H100 at $0.10833/min =
$6.50/h [[src](https://www.baseten.co/pricing/)] (`est.` monthly at 730 h).

So the actual decision is three-way, not two-way:

| Option | Fixed cost | Marginal cost | Wins when |
|---|---|---|---|
| Frontier API | $0 | $3.8–$16.6/1M blended | Low or spiky volume; no eval budget |
| Serverless open model | $0 | $0.19–$4.99/1M blended [[src](https://www.baseten.co/pricing/)] | Medium volume; no ops appetite; still wants a big saving |
| Self-hosted distilled specialist | ≥ $7.3k/mo/replica + a dev replica + an eval budget | $0.009–$0.19/1M blended | High, steady volume *and* a quality bar the serverless open model misses |

**Corollary: the platform's own cost of goods is dominated by idle GPU, not by
tokens.** Everything in [`scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md),
[`scaling/06-cold-start.md`](../scaling/06-cold-start.md) and
[`scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md) is therefore
commercially load-bearing, and multi-tenant adapter serving (S-LoRA-style: thousands
of adapters on shared base weights, "up to 4×" throughput over HF PEFT and vLLM via
Unified Paging [[src](https://arxiv.org/abs/2311.03285)]) is the single highest-
leverage architecture decision in the whole platform, because it turns N customers'
idle GPUs into one busy one. Doc 01 and doc 06 must treat it as a primary design
axis.

---

## 5. The crux — confidence

The customer is not buying a smaller model. They are buying **permission to switch**.
Everything hard is in manufacturing that permission.

### 5.1 Judge validity

The platform will lean on LLM-as-judge because human labels do not scale. The
foundational result is encouraging and the caveats are the whole problem: strong
LLM judges "match both controlled and crowdsourced human preferences well,
achieving over 80 % agreement, the same level of agreement between humans", while
exhibiting **position bias, verbosity bias, self-enhancement bias and limited
reasoning ability** [[src](https://arxiv.org/abs/2306.05685)].

Three of those four are lethal for *this specific* product:

- **Self-enhancement bias** — if the teacher is also the judge, the judge prefers
  outputs that look like the teacher's, which is precisely what the student was
  trained to produce. The judge and the training objective are then the same
  function, and the eval measures nothing. **Rule: the judge must not be the
  teacher.** If the teacher is GPT-6 Astra, judge with a Claude-family model, and
  vice versa; report both.
- **Verbosity bias** — a distilled student trained on a verbose teacher will be
  rewarded for verbosity by a verbose-biased judge while being *worse*. Control for
  length explicitly (length-matched comparisons or length as a covariate).
- **Position bias** — trivially fixed by running both orders and averaging; not
  doing so is negligence.

**Judge validation is a required, budgeted stage, not an afterthought.** The
mechanism: a gold set of ≥200 human-adjudicated examples per customer task;
measure judge-vs-human agreement (Cohen's κ, and per-slice accuracy); ship the
judge only if agreement exceeds an agreed floor; re-validate whenever the judge
model version changes. If judge-human agreement is 80 %, a judge-measured 2-point
delta is inside the judge's own noise floor — a fact that must be stated on the
customer's dashboard, not buried.

Note the sharper version of the problem: the RealHumanEval study found that
"programmer preferences do not correlate with their actual performance"
[[src](https://arxiv.org/abs/2404.02806)] — i.e. even *human* preference is not a
valid proxy for task outcome. Wherever an outcome signal exists (ticket resolved,
code merged, transaction completed), it beats any judge, and doc 03/04 should hunt
for one before building a rubric.

### 5.2 What "parity" should mean

Parity is a **non-inferiority claim**, not an equality claim, and it needs four
numbers agreed in writing before any training starts:

1. **Metric** — per task, and per slice, not one aggregate.
2. **Margin δ** — how much worse is acceptable (e.g. 2 percentage points).
3. **Confidence 1−α** — typically 95 %, one-sided.
4. **Power 1−β** — typically 80 %; without this the customer cannot know what a
   "pass" means.

The claim to make is: *the upper bound of the 95 % CI on (incumbent − candidate)
is below δ, on the frozen test set, on every named slice, with the safety eval
passing unconditionally.* Anything vaguer is not a claim, and an aggregate-only
claim is how a model that is 3 points better on the 90 % easy slice and 20 points
worse on the 10 % hard slice gets promoted.

### 5.3 Sample size — the arithmetic that sets the price of confidence

Two-proportion one-sided non-inferiority, `n ≈ (z_{1−α} + z_{1−β})² · 2p(1−p) / δ²`
(`est.`, standard normal approximation; α = 0.05, β = 0.20):

| Baseline accuracy p | Margin δ | n **per arm** |
|---:|---:|---:|
| 0.85 | 5 pp | 631 |
| 0.85 | 3 pp | 1,752 |
| 0.85 | 2 pp | 3,942 |
| 0.85 | 1 pp | **15,766** |
| 0.95 | 2 pp | 1,469 |
| 0.70 | 3 pp | 2,886 |

Read the last row of the 0.85 block: **a 1-point margin costs ~16k graded examples
per arm.** At a judged-eval cost of a few cents per example this is affordable; at
an SME-adjudicated cost of a few dollars it is not. This table is the reason the
platform must sell the customer a *margin*, and it is the single most useful thing
to put in front of a buyer on day one: "how sure do you want to be, and what will
you pay for it?"

Three multipliers on top:
- **Per-slice claims multiply n by the number of slices** (and demand a
  multiple-comparison correction, or the slice claims are noise).
- **Paired designs reduce n substantially** — run both models on the *same* inputs
  and test the paired difference. Always do this offline; it is free.
- **Sequential/peeking inflates α.** A fixed-horizon test that is monitored
  continuously and stopped on a win is not a 95 % test. Either commit to a horizon
  or use an always-valid/sequential procedure. ⚠️ **TO BE VERIFIED** — I could not
  fetch a primary source for always-valid inference in this session (the arXiv ID I
  tried was a different paper); doc 07 must source this properly before the
  platform implements a stopping rule.

### 5.4 Non-stationarity, rare tails, and multi-turn

- **Non-stationarity.** Traffic mix changes weekly; a test run across a mix shift
  measures the shift. Mitigations: run arms concurrently (never sequentially),
  randomise at a stable unit (user or session, not request — request-level
  randomisation breaks multi-turn sessions), and stratify.
- **Rare-failure tails.** A failure at 1-in-10,000 needs ~30,000 samples to see
  three of them. Aggregate A/B will never find it. The only workable approaches are
  (a) **shadow traffic** — run the candidate on 100 % of production requests with
  its output discarded, and diff against the incumbent offline; this gets full
  coverage at zero user risk and costs only compute; and (b) **targeted
  adversarial/regression suites** that permanently absorb every incident.
  **Shadow-first is the recommended default rollout mode** for exactly this reason.
- **Multi-turn / agentic.** Per-turn parity does not compose. A per-turn accuracy
  of 0.98 over a 10-turn session is 0.82 session-level if errors are independent,
  and worse if they compound. Evaluate at the session level, on real trajectories
  from S2, with the tool layer mocked deterministically so the eval measures the
  model and not the flaky API behind tool #4.
- **Agentic evaluation is where the published methodology is weakest.**
  Snorkel maintains agent benchmarks with "programmatic pass/fail criteria" —
  Terminal-Bench 4.0, Senior SWE-bench, OSWorld 2.0 [[src](https://snorkel.ai/)] —
  which is evidence that the industry's answer is *verifiable environments*, not
  judges, wherever one can be built.

### 5.5 Video

Everything above gets harder: no cheap judge (a video judge is a frontier
multimodal call per example, so the §5.3 sample sizes become expensive), long clips
mean long-context grading, and the ground truth is often genuinely ambiguous.
Video-MME's own construction — 2,700 QA pairs over 900 videos, built by "rigorous
manual labeling by expert annotators" [[src](https://arxiv.org/abs/2405.21075)] —
is the honest signal of what a *good* video eval costs: roughly 3 human-authored
items per video. ⚠️ **TO BE VERIFIED**: whether frame-sampled proxy evals (grading
on the same 240-frame budget the model sees, per
[`models/marlin2b/README.md`](../models/marlin2b/README.md) §9) are a valid stand-in
for full-clip human grading. Doc 04 owns this question.

### 5.6 The minimum evidence a customer will accept

Synthesised from what the vendors put in front of buyers (§7) and from the failure
modes above — ⚠️ this ordering is my inference, not a surveyed result:

1. **Their own traffic, their own prompts.** Not a benchmark. Non-negotiable.
2. **A held-out test set they can inspect**, with the hard slices visible.
3. **Shadow-mode diffs** on real production requests, with the disagreements
   listed and clickable — "here are the 214 requests where the two models differed,
   and here is who was right."
4. **A non-inferiority result with a stated margin and power** (§5.2–5.3).
5. **An unconditional safety result** (I5).
6. **Instant, demonstrated rollback** (I7) — demonstrated, in front of them, before
   any traffic moves.
7. **A cost statement that survives §4.4** — i.e. compared against their *batched,
   cached, tier-downed* incumbent price, not list.

Items 3 and 6 do more selling than items 4 and 5. Build them first.

---

## 6. Problem decomposition — the map for docs 01–09

| Doc | Component | Purpose | Hard problems | Build / buy candidates |
|---|---|---|---|---|
| **01** | **Inference, endpoints, versioning** | Serve `main` and `dev` endpoints per customer task; immutable versioned artifacts; instant promote/rollback; multi-adapter serving | Contract fidelity (I4); cold start; multi-tenant adapter packing; keeping the prompt stack inside the artifact | **Build** the control plane on **vLLM/SGLang** (already analysed in [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)); **buy** burst capacity from Baseten dedicated ($0.10833–$0.16633/GPU-min [[src](https://www.baseten.co/pricing/)]); adopt **S-LoRA**-style unified paging [[src](https://arxiv.org/abs/2311.03285)] |
| **02** | **Observability + traces** | Capture every request/response with full context; searchable; cheap at volume | Schema stability; cost of storing every token; PII before it lands; sampling that preserves the tail | **Buy/adopt**: Langfuse (open source, self-hostable, $29–$2,499/mo cloud, billed per "billable unit" [[src](https://langfuse.com/pricing)]), Braintrust ($0–$249/mo + $/GB + $/1k scores [[src](https://www.braintrust.dev/pricing)]), W&B Weave (agent-native tracing, PII/toxicity/hallucination scorers [[src](https://wandb.ai/site/weave/)]). Standardise on **OpenTelemetry GenAI semantic conventions** [[src](https://github.com/open-telemetry/semantic-conventions-genai)] |
| **03** | **Data capture, analysis, annotation** | Turn traces into labelled training data: teacher completions, preference pairs, rubric scores, human adjudication | Teacher ToS (§8.1); PII redaction before egress; annotation cost; label quality; dedup | **Build** the pipeline; **buy** the teacher tokens (batch API, 50 % off); consider Snorkel for expert data where correctness is hard to define [[src](https://snorkel.ai/)]; NVIDIA **Data Designer** / **Safe Synthesizer** for synthetic and privacy-preserving data [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)] |
| **04** | **Datasets + evals** | Versioned splits; a frozen, contamination-free eval suite; judge validation; video evals | Judge validity (§5.1); contamination (§8.4); slice design; session-level and video eval | **Build** the eval harness around the customer's outcome signal; **buy** nothing wholesale — note that **OpenAI's Evals platform is being sunset** (read-only 2026-10-31, shutdown 2026-11-30 [[src](https://developers.openai.com/api/docs/guides/evals)]), which is a warning about depending on a vendor eval product |
| **05** | **Training (SFT / DPO / RL / distillation)** | Produce candidate checkpoints from S4 data | Choosing the rung on the §3.2 ladder; teacher logprob availability; forgetting; reproducibility | **Buy first**: Tinker (LoRA 1B–1T+, SFT/RL/GRPO/PPO/DPO/distillation, export to HuggingFace, train price $0.44–$14.58/1M by model — Nemotron-3.5-Lightning to GLM-5.3 [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]), Baseten Training Jobs (GA) / Loops (early access, async RL, 256K+ sequences, one-command promotion to Dedicated Inference [[src](https://www.baseten.co/products/training/)]), Fireworks (SFT + RFT to 1T+ [[src](https://docs.fireworks.ai/)]). **Build** only the orchestration |
| **06** | **Model + inference optimization on target hardware** | Turn a checkpoint into the cheapest artifact that meets the SLO on the customer's GPU | Quantisation changing behaviour; engine/format availability per GPU; speculative decoding; the gate-twice rule | **Build**, on top of this repo's existing work: [`matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md), [`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md), [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md), [`cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md) |
| **07** | **A/B testing + rollout** | Shadow → canary → % split → promote, with a defensible statistical claim | Sample size (§5.3); peeking; randomisation unit; non-stationarity; rare tails | **Build.** This is the product's differentiator and cannot be outsourced — it is where §5.6 items 3 and 6 live |
| **08** | **Governance, privacy, safety, multi-tenancy** | Make the loop legally and contractually shippable | Teacher ToS (§8.1); PII; residency; tenant isolation; audit trail; safety gate | **Build** policy + enforcement; **buy** redaction/PII scorers (W&B Weave guardrails [[src](https://wandb.ai/site/weave/)], NVIDIA Guardrails [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)]) |
| **09** | **The auto-research loop** | Automatically search the space of {data mix, method, hyperparameters, student base, quantisation, engine config} and propose the next experiment | Search cost; overfitting the eval; knowing when to stop; explaining a choice to a customer | **Build**, informed by the one public precedent: NVIDIA's blueprint "explores a vast number of possible options down to a manageable set" without manual experiment design [[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)] — and is now deprecated, so the precedent is a design, not a dependency |

**The ordering that matters.** Docs 02 → 04 → 07 (traces, evals, A/B) are the
*minimum sellable product*, because they deliver §5.6 items 1–3 and 6 without any
training at all. Docs 03 and 05 (annotation, training) add the actual model. Docs
01, 06, 08, 09 are what makes it a platform rather than a consulting engagement.
**Do not build 09 first**, however tempting; an auto-research loop over an
unvalidated eval is a machine for overfitting.

---

## 7. Prior art — what has been proven, and what is still open

### 7.1 The platforms

| Platform | What it is (2026-09-19) | What it proves | What it leaves unsolved |
|---|---|---|---|
| **Thinking Machines Tinker** [[src](https://thinkingmachines.ai/tinker/)] [[docs](https://tinker-docs.thinkingmachines.ai/)] | A training API exposing four primitives — `forward_backward`, `optim_step`, `sample`, `save_state` — with LoRA over dense and MoE, text and vision; SFT, RL (GRPO/PPO), DPO, model *and prompt* distillation; ~30 open-weights models listed, spanning Qwen3.5-4B to Nemotron-3-Ultra-550B-A55B (⚠️ the "1B to 1T+" range is not stated on the pages fetched); export ("download any checkpoint you've saved") to HuggingFace; serverless inference in **beta** for its own Inkling models | **Training-as-a-service is solved and commoditised.** Also that a research lab thinks the *primitives*, not the pipeline, are the product | No traces, no production endpoints for arbitrary models, no A/B, no evals loop. It is stage S5 only |
| **"Inkling"** — resolved | **Thinking Machines' own model family**, `thinkingmachines/Inkling` and `Inkling-Small`, trainable on Tinker at $1.87/$4.68/$5.61 per 1M (prefill/sample/train, at a 50 % promotional discount), with 64K and 256K variants; serverless inference beta at $0.30 in / $1.20 out for Inkling-Small [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)] | That the training-API vendor is also shipping its own student models — i.e. the "bring your own base model" promise has a house brand next to it | Whether Inkling is competitive as a student for customer tasks — ⚠️ no published benchmarks found |
| **Baseten** [[training](https://www.baseten.co/products/training/)] [[pricing](https://www.baseten.co/pricing/)] | Training Jobs (GA, framework-agnostic, multi-node, SSH debugging) + **Loops** (early access: async RL, 256K+ sequences, 2T+ params, policy versioning, non-blocking weight sync) + Dedicated Inference + Model APIs. "Models trained with Loops promote directly to Baseten Dedicated Inference with one command." Users get "Full ownership of your trained weights, no lock-in", extending to training code and evaluation artifacts | **The train→deploy seam is being closed by an inference vendor**, and this is the nearest competitor to the platform's §6 docs 01+05. Also proves the pricing model: per-GPU-minute, no idle charge | No trace capture from customer production, no annotation pipeline, no A/B/eval loop, no teacher-in-the-loop. It is S5→S6→S8, missing S1–S4 and S7/S9 |
| **NVIDIA Data Flywheel Blueprint** [[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)] | Reference implementation of exactly this loop: Elasticsearch trace logs → dedup/split by `workload_id` → auto fine-tune across several candidate models → LLM-as-judge comparison → flag candidates for human review. Apache-2.0. **Status: DEPRECATED (April 2026), "no longer actively maintained, and new production use is not recommended"** | The end-to-end loop works and produces radical results: Llama 3.2 **1B at ≈98 % of Llama 3.1 70B's accuracy** on an internal HR chatbot's *tool-calling* task with "up to 98.6 %" cost reduction; `Qwen-2.5-32b-coder` ≈ `Llama-3.1-70b-instruct` un-fine-tuned, at >50 % lower cost and TTFT — with NVIDIA's own scoping caveat: *"simpler tool calling use cases where an agent is using a tool call to route between a small set of tools"* | It was a blueprint, not a product, and it is dead. **The most direct proof of the thesis is also the most direct proof that shipping it as a maintained product is hard** |
| **NVIDIA NeMo microservices** [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)] | Customizer (LoRA, SFT, DPO, embedding customisation), Evaluator, Guardrails, Data Designer (synthetic data), Safe Synthesizer (privacy-preserving synthetic), Auditor (agent vulnerability testing). SDK 2.0.1, image 26.03.1. **"NeMo Microservices will be sunset on October 1, 2026. All new development has moved to NeMo Platform"** | That the component decomposition in §6 is the industry-consensus decomposition — NVIDIA arrived at nearly the same box diagram | Distillation is **not** listed among Customizer's supported methods. And the second sunset in two rows: this space churns |
| **OpenAI** [[FT](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)] [[Evals](https://developers.openai.com/api/docs/guides/evals)] | SFT with a documented distillation workflow — verbatim, it is prompt-tuning the teacher, not fine-tuning it: *"Tune a prompt for a larger model (like `gpt-4.1`) until you get great performance"*, capture its results, build a dataset, then *"Tune a smaller model (like `gpt-4.1-mini`)"*. **"OpenAI is winding down the fine-tuning platform. The platform is no longer accessible to new users."** Supported bases are still gpt-4.1 / -mini / -nano. **Evals: read-only 2026-10-31, shutdown 2026-11-30**, users pointed at "Datasets" | That the incumbent *shipped* teacher→student distillation as a first-party product and is now **retreating from it**. Read this carefully: it is simultaneously the strongest validation of the demand and the strongest signal that the incumbents would rather you tier down within their family (Luna, §4.4) than distil out of it | Why they are winding it down. ⚠️ **TO BE VERIFIED** — strategy, economics, or replacement? |
| **Anthropic** | No first-party distillation product; the usage policy explicitly prohibits it without authorisation (§8.1) | The teacher side is a *licensing* problem, not a technical one | — |
| **Databricks Agent Bricks** [[src](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/)] | Knowledge Assistant and Supervisor Agent components; Agent Evaluation to "measure quality, cost, and latency" and "use LLM judges to identify and resolve quality issues". Agent Services in Beta, page dated 2026-09-15 | Evals + judges as a platform feature next to the data warehouse — the "your data is already here" wedge | ⚠️ The automatic-optimisation and synthetic-data claims I expected were **not** on the page I fetched; do not repeat them without a source |
| **Snorkel AI** [[src](https://snorkel.ai/)] | Expert data development, data-as-a-service with "curriculum-structured datasets… rubrics, reviewer guidance, difficulty tiers, and evaluation slices", benchmarks (Terminal-Bench 4.0, Senior SWE-bench, OSWorld 2.0), and "specialized agents… with pass/fail criteria" | That the expensive, human part of S3/S4 is a business in its own right, and that the sophisticated answer to agent eval is **verifiable environments** | It is a services company. It does not close the loop |
| **Langfuse** [[src](https://langfuse.com/pricing)] | Open-source tracing + datasets/experiments/scores + prompt management + dashboards. Hobby free (50k units/mo), Core $29, Pro $199, Enterprise $2,499 (100k units included on Core/Pro/Enterprise), graduated overage $8/100k (100k–1M) → $7 → $6.50 → $6/100k (50M+). Self-hostable free ⚠️ — the "feature parity with Cloud" phrasing is **not** on the pricing page as fetched; it links to self-hosting docs without a parity statement | Trace capture and offline experiments are commodity and cheap. **Do not build doc 02 from scratch** | No training, no endpoints, no rollout |
| **Braintrust** [[src](https://www.braintrust.dev/pricing)] | Observe + Evaluate (LLM-judge, autoevals, custom scorers) + Discover + Playground + Human Review + a "Loop Agent" for autonomous eval and test-case generation. $0 / $249 / Enterprise, billed on GB processed + scores; on-prem available | The eval-tooling layer is commodity too, **including** an agent that writes test cases | Same gap |
| **W&B Weave** [[src](https://wandb.ai/site/weave/)] | Agent-native tracing (sessions/turns/steps/tools/sub-agents as first-class), imperative eval API with regression comparison, Guardrails scorers (toxicity, bias, **PII**, hallucination, coherence, relevance), MCP server so coding agents can "read live production data, run evaluations, and execute automatic iteration loops" | That the "agent reads your traces and iterates" pattern — doc 09's core idea — is already shipping as a feature | Bound to the W&B ecosystem; no serving |
| **Fireworks AI** [[src](https://docs.fireworks.ai/)] | Serverless + dedicated GPU inference, **supervised and reinforcement fine-tuning of models up to 1T+**, 100+ models across text/vision/audio/image/embeddings, OpenAI-compatible | Another inference vendor closing the train→serve seam | ⚠️ Pricing, LoRA-serving specifics and any traffic-driven customisation workflow were **not** on the docs index I fetched |
| **Predibase** | ⚠️ **TO BE VERIFIED.** `predibase.com` now 301-redirects to `rubrik.com/products/rubrik-agent-cloud`, and that page returned 403. The redirect is strong evidence of an acquisition by Rubrik, but I could not fetch a confirming source in this session | — | Doc 09 must confirm the acquisition date and what survived of LoRAX / Turbo LoRA / reinforcement fine-tuning |
| **OpenPipe** | ⚠️ **TO BE VERIFIED.** The page fetched with no substantive content. OpenPipe's historic pitch — capture production traffic, fine-tune a smaller model, deploy, compare — is the closest thing to this platform's thesis, so its current status matters | — | Doc 09 owes a proper look |

### 7.2 The Tesla data-engine analogy, and where it breaks

The closed-loop framing borrows from autonomous driving's "data engine": deploy →
collect → mine for failures → label → retrain → redeploy. Two properties made it
work there that **do not hold here**, and pretending otherwise is the fastest way
to build the wrong platform:

1. **Ground truth was recoverable after the fact.** A disengagement, a collision, a
   human takeover — the label arrives for free from the future. In an LLM feature,
   the label usually does *not* arrive; someone has to decide whether the answer was
   good. That decision is §5.1 and it costs money.
2. **The failure distribution was physically bounded.** Roads are a finite world.
   A prompt space is not; the tail is adversarial and unbounded.

What does transfer: **mine the disagreements, not the average.** The highest-value
examples in S2 are the ones where student and incumbent disagree, or where the
judge is uncertain, or where the user retried. That is the platform's version of a
disengagement, and doc 03 should be built around it.

---

## 8. Risks

### 8.1 Teacher-model terms of service — the risk that can end the product

This is not a footnote. **All three major teacher vendors prohibit, in terms, the
core mechanism of the platform.**

> **Anthropic, Usage Policy, effective 2025-09-15** — "Do Not Abuse our Platform":
> *"Utilization of inputs and outputs to train an AI model (e.g., 'model scraping'
> or 'model distillation') without prior authorization from Anthropic"*
> [[src](https://www.anthropic.com/legal/aup)]

> **Anthropic, Commercial Terms of Service, effective 2025-06-17, §D.4 (Use
> Restrictions)** — *"Customer may not and must not attempt to (a) access the
> Services to build a competing product or service, including to train competing AI
> models or resell the Services except as expressly approved by Anthropic"* and
> *"(b) reverse engineer or duplicate the Services"*
> [[src](https://www.anthropic.com/legal/commercial-terms)]

> **Google, Gemini API Additional Terms, effective 2026-03-23** — *"You may not use
> the Services to develop models that compete with the Services (e.g., Gemini API
> or Google AI Studio). You also may not attempt to reverse engineer, extract or
> replicate any component of the Services, including the underlying data or models
> (e.g., parameter weights)."* [[src](https://ai.google.dev/gemini-api/terms)]

> **OpenAI** — ⚠️ **TO BE VERIFIED.** `openai.com/policies/business-terms/` and the
> terms-of-use pages returned **HTTP 403** to both WebFetch and curl in this
> session, so I have **no verbatim quote**. I will not paraphrase a legal clause I
> could not read. What *is* sourced: OpenAI documents a distillation workflow for
> distilling *their own* larger models into *their own* smaller ones
> [[src](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)],
> which is a meaningfully different act from distilling into an open-weights model
> served elsewhere. **Doc 08 must obtain and quote the actual OpenAI clause before
> any customer-facing claim is made.**

Note the asymmetry worth designing around: Anthropic's phrasing is *"without prior
authorization from Anthropic"* — i.e. it contemplates authorisation existing.
Google's is a flat prohibition on competing-model development. These are different
risks and should not be treated as one.

**Implications for the architecture, not just the legal review:**

- **The platform must not require a frontier teacher.** If every loop depends on a
  clause the vendor can enforce, the platform has a single point of legal failure.
  Design for **open-weights teachers** as the default path: a large open model
  (Kimi-K3 at 8 GPUs, DeepSeek-V4.1-Flash at 2 — see
  [`matrix/fit-matrix.md`](../matrix/fit-matrix.md)) teaching a small one is
  unencumbered, and the cost tables in §4.2 show it is affordable.
- **The customer's own authorisation is not ours.** If a customer has negotiated
  distillation rights with a vendor, that is a per-tenant configuration flag with a
  document attached, not a platform default.
- **"Teaching from the customer's own production traffic"** — where the customer
  already paid for the outputs, in their own account, under their own agreement —
  is a *different* legal posture from us calling a teacher API. It may still be
  prohibited; it is at minimum the customer's decision and their liability. Doc 08
  must make the platform capable of expressing that distinction, with per-tenant
  policy and an audit trail of which teacher produced which label.

### 8.2 Data privacy and PII in traces

Traces are the rawest data in any company: full prompts, retrieved documents,
tool arguments, user text. Concrete requirements:

- **Redact before egress, not before storage-and-then-egress.** Any path from the
  trace store to a third-party teacher or judge is an egress boundary, and PII must
  be removed *on that edge*. Off-the-shelf PII scorers exist (W&B Weave guardrails
  [[src](https://wandb.ai/site/weave/)], NVIDIA Guardrails and Safe Synthesizer for
  privacy-preserving synthetic data [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)]).
- **Tenant isolation is absolute** (I6). One customer's traffic never contributes
  to another's model, ever, including via a "shared base improvement". The moment
  this is fuzzy the enterprise sale dies.
- **Residency and retention** must be per-tenant configuration. Note that vendors
  now sell inference-geography controls and zero-data-retention modes as first-class
  features; enterprise buyers will ask.
- **Right to deletion.** A trace deleted from the store is still in the checkpoint
  it trained. ⚠️ **TO BE VERIFIED** — there is no cheap mechanism for un-training
  one example; the practical answer is retention windows plus documented retrain
  cadence, and doc 08 must say so plainly rather than imply deletion propagates.

### 8.3 Model collapse from self-training

"The Curse of Recursion" establishes that training on model-generated content
causes degradation in which "tails of the original content distribution disappear",
across VAEs, GMMs and LLMs [[src](https://arxiv.org/abs/2305.17493)]. The paper's
own mitigation framing is that "data collected about genuine human interactions
with systems will be increasingly valuable."

For this platform the risk is concrete and specific: **once the distilled student
is serving, its outputs become the next round's traces.** Left unguarded, round N+1
distils the student into itself, the tails vanish, and the eval — built from the
same traffic — does not notice.

Guards, in order of importance:
1. **Never train on the student's own outputs as targets.** Prompts from production
   traffic: yes (that is the point of on-policy distillation). Student completions
   as *labels*: no, unless a verifier passed them.
2. **Keep a human/outcome-anchored slice** in every training round.
3. **Freeze eval sets against a human-labelled reference**, refreshed on a
   different cadence from training data.
4. **Monitor output diversity** (entropy, distinct-n, refusal rate) round over
   round; collapse shows up as narrowing before it shows up as a score drop.

### 8.4 Eval contamination

Benchmarks leak into training data and inflate scores: ChatGPT and GPT-4 achieved
**52 % and 57 % exact-match** when guessing *masked* MMLU answer options — a level
that is hard to explain without exposure [[src](https://arxiv.org/abs/2311.09783)].

In a closed loop the mechanism is even more direct than web contamination: the test
set is drawn from the same trace store as the training set. Requirements: split by
a stable entity (user, document, session) and never by row; hold the test split out
of the annotation pipeline entirely; hash-dedup near-duplicates across splits;
timestamp splits so that test data is strictly *later* than train where the task is
temporal; and treat any eval-set refresh as invalidating every historical score.

### 8.5 Regressions after updates

Every promotion is a chance to break something that used to work. The mitigations
are unglamorous and non-optional: a permanent regression suite that only ever
grows; every production incident becomes a test case; the safety gate never trades
against the quality gate; canary before percentage split; and I7 rollback proven,
not assumed.

### 8.6 Vendor lock-in — including ours

Three sunsets inside twelve months, all found in this session's sources: NVIDIA's
data-flywheel blueprint **deprecated April 2026**
[[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)]; NeMo Microservices
**sunset 2026-10-01** [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)];
OpenAI Evals **read-only 2026-10-31, shutdown 2026-11-30**
[[src](https://developers.openai.com/api/docs/guides/evals)], alongside the
fine-tuning platform "winding down"
[[src](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)].

Two conclusions. **For us as a buyer:** do not put a load-bearing component of the
loop on a vendor product without an exit; prefer open source (Langfuse, vLLM/SGLang,
OpenTelemetry) or formats we control. **For us as a seller:** the customer will ask
the same question, and Baseten's answer — users keep "complete control of weights,
evaluation data, and training scripts" [[src](https://www.baseten.co/products/training/)],
and Tinker's export-to-HuggingFace path
[[src](https://tinker-docs.thinkingmachines.ai/)] — is now the market's baseline.
(Baseten's page words it *"Full ownership of your trained weights, no lock-in"*.)
**Weights, datasets, evals and traces must be exportable by the customer, in open
formats, on demand.** Anything less is not sellable to an enterprise in 2026.

---

## 9. Success criteria and non-goals for an MVP

### 9.1 MVP definition

**One customer, one text task, one student, one GPU class.** The MVP is the
smallest thing that produces §5.6 items 1–3 and 6.

| # | Criterion | Target | Measured how |
|---|---|---|---|
| 1 | Trace capture in production | ≥ 99.9 % of requests captured; added p99 latency ≤ 5 ms | Gateway metrics vs. the customer's own |
| 2 | Shadow evaluation | 100 % of production requests shadowed to the candidate; disagreements surfaced within 1 h | Diff pipeline |
| 3 | Offline non-inferiority | Upper bound of the 95 % one-sided CI on (incumbent − candidate) < δ, per slice, at ≥ 80 % power | §5.3 arithmetic, on a frozen test split |
| 4 | Judge validity | Judge-vs-human agreement ≥ 80 % on a ≥200-example gold set; judge ≠ teacher | Cohen's κ + per-slice accuracy |
| 5 | Safety | No regression on the safety suite, unconditional | Separate gate |
| 6 | Contract fidelity | 100 % pass on the conformance suite (schema, tools, streaming, structured output) | Automated, in CI |
| 7 | Latency | p50 and p99 TTFT/TPOT ≤ incumbent at the customer's concurrency | Load test at S8, confirmed online |
| 8 | Cost | Total delivered $/1M (including idle GPU and amortised build) below the customer's **batched, cached, tier-downed** incumbent price (§4.4) | Cost model, re-run at promotion |
| 9 | Rollback | Demonstrated < 60 s, with zero dropped requests | Live drill before any traffic moves |
| 10 | Loop closure | A second iteration trained from post-deployment traffic beats the first on the frozen test set | The whole thesis, in one number |

Criterion 10 is the only one that proves the *loop*, as opposed to a one-off
distillation project. If the MVP cannot hit 10, the platform is a consultancy.

### 9.2 Explicit non-goals for the MVP

- **Not** the auto-research loop (doc 09). One good manual iteration first.
- **Not** multi-tenant adapter packing. One customer, dedicated replica; take the
  bad unit economics and learn.
- **Not** video. §3.4 and §5.5 say it needs its own evidence base; doing it
  concurrently doubles the unknowns. Sequence it second, and budget the eval.
- **Not** agentic/multi-turn parity claims. Single-turn first; §5.4 explains why
  composing is a separate problem.
- **Not** frontier-teacher distillation by default. Start on open-weights teachers
  (§8.1) so the MVP has no legal dependency, and treat frontier teachers as a
  per-tenant, documented exception.
- **Not** a trace-store or eval-UI rewrite. Adopt Langfuse or equivalent (§6 doc 02).
- **Not** a promise of a specific speedup or cost multiple. Quote against §4.4's
  honest baseline or do not quote.

---

## Implications for the platform

**What to build** — the parts that are the product, that nobody sells, or that
cannot be outsourced without losing the business:

1. **The A/B and rollout control plane (doc 07) — build first and build well.**
   Shadow-by-default; randomise at the session; paired offline comparison;
   pre-registered margin, α and power; sequential-testing discipline; instant
   rollback demonstrated to the buyer. This is §5.6 items 3 and 6, which is what
   actually closes the sale.
2. **Versioned artifact + endpoint control plane (doc 01)**, where the artifact
   includes the prompt stack, tokenizer, chat template and serving config, and where
   `main`/`dev` promotion is a single reversible operation.
3. **The gate-twice discipline (doc 06)** — evaluate the *served* artifact, after
   quantisation and engine selection, not the BF16 checkpoint. This repo's per-GPU
   format substitutions make this a first-order concern, not a nicety.
4. **Judge validation as a product surface (doc 04)** — gold sets, agreement
   numbers, the judge's noise floor shown next to every delta, and a hard rule that
   the judge is never the teacher.
5. **Disagreement mining (doc 03)** — the platform's version of a disengagement.
   Highest-value data, cheapest to identify, and directly demoable.
6. **Per-tenant teacher policy + audit trail (doc 08)** — which teacher produced
   which label, under whose agreement.
7. **Multi-tenant adapter serving (doc 01/06)** — not in the MVP, but the thing
   that makes the unit economics work at N customers (§4.5), so design for it now.

**What to buy / adopt** — where the market has commoditised faster than we could build:

- **Tracing and offline experiments**: Langfuse (open source, self-hostable) or
  Braintrust or W&B Weave. Standardise the schema on OpenTelemetry GenAI semconv.
- **Training compute**: Tinker or Baseten Training Jobs / Loops or Fireworks. Buy
  the primitives; build only the orchestration and the artifact contract.
- **Burst and dev inference capacity**: per-GPU-minute dedicated deployments with
  no idle charge, until steady-state volume justifies owning the fleet.
- **Expert annotation** for tasks where correctness is hard to define: Snorkel-class
  services, not an in-house labelling team.
- **PII/safety scorers**: off-the-shelf; do not write a PII detector.

**What to avoid:**

- **Quoting list-price savings.** §4.4 — the real competitor is the incumbent's own
  cheap tier, batched and cached, at $0.38–$1.66 blended. Build the pitch against
  that and it survives contact with a competent buyer.
- **Building the auto-research loop before the eval is trusted.** An optimiser over
  an invalid objective is a machine for overfitting, and it will produce a confident
  regression.
- **Taking a hard dependency on any vendor's eval, fine-tuning or blueprint
  product.** Three sunsets in twelve months (§8.6).
- **Making the frontier teacher structurally necessary.** §8.1 — all three major
  vendors' terms prohibit the mechanism; design open-weights-teacher-first.
- **Aggregate-only quality claims.** Per-slice or nothing (§5.2).
- **Request-level randomisation** in anything multi-turn.
- **Training on the student's own outputs as labels** (§8.3).
- **Treating video as a text variant.** It needs its own evidence base, its own eval
  budget, and its own sequencing (§3.4, §5.5).

---

## Open questions

⚠️ Consolidated. Each names the doc that owns it.

1. **⚠️ Market coverage.** This session had **no web search** — §7 covers only
   platforms I could name and fetch by URL. Who else is selling parts of this loop?
   *Owner: doc 09, with search available.*
2. **⚠️ OpenAI's distillation terms.** `openai.com/policies/business-terms/` and the
   terms-of-use pages returned HTTP 403 to both WebFetch and curl. No verbatim
   clause obtained. **No customer-facing claim about OpenAI-teacher distillation
   may be made until this is read and quoted.** *Owner: doc 08.*
3. **⚠️ Why is OpenAI winding down fine-tuning?** The platform is "no longer
   accessible to new users" [[src](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)]
   and Evals shuts down 2026-11-30. Strategy, economics, or a replacement? This
   materially changes the competitive picture. *Owner: doc 09.*
4. **⚠️ Predibase.** `predibase.com` 301-redirects to `rubrik.com`; that page 403'd.
   Acquisition date, acquirer, and the fate of LoRAX / Turbo LoRA / RFT. *Owner: doc 09.*
5. **⚠️ OpenPipe.** Current status and product. Its historic pitch is the closest to
   ours. *Owner: doc 09.*
6. **⚠️ Teacher logprobs.** Which teachers expose per-token logprobs for
   *arbitrary supplied continuations*? On-policy distillation (§3.2) requires it;
   without it the method degrades to rejection sampling. *Owner: doc 05.*
7. **⚠️ Video-understanding distillation evidence.** No published result found
   showing a small VLM at parity with a frontier model on a customer video task.
   This is the largest evidence gap in the programme. *Owner: doc 04.*
8. **⚠️ Frame-sampled video eval validity.** Is grading on the model's own 240-frame
   budget a valid proxy for full-clip human grading? *Owner: doc 04.*
9. **⚠️ Sequential / always-valid testing.** No primary source obtained this session
   (the arXiv ID tried was a different paper). Needed before a stopping rule ships.
   *Owner: doc 07.*
10. **⚠️ Cost of human adjudication.** No public benchmark for $/adjudicated example
    at enterprise quality. This is likely the dominant one-time cost (§4.3c) and it
    is currently unpriced. *Owner: doc 03.*
11. **⚠️ Prompt-change invalidation.** Will customers accept "your prompt changed,
    your parity claim is void"? Product decision. *Owner: doc 07.*
12. **⚠️ Right to deletion vs. trained weights.** No cheap un-training mechanism;
    what do we commit to contractually? *Owner: doc 08.*
13. **⚠️ Inkling as a student.** No published benchmarks found for Thinking
    Machines' own model family. *Owner: doc 05.*
14. **⚠️ Databricks Agent Bricks.** The automatic-optimisation and synthetic-data
    claims were not on the doc page fetched; confirm before citing. *Owner: doc 09.*
15. **⚠️ Fireworks specifics.** Pricing, LoRA serving, and any traffic-driven
    customisation workflow were not on the docs index fetched. *Owner: doc 09.*
16. **⚠️ NeMo Platform.** NeMo Microservices sunsets 2026-10-01 in favour of "NeMo
    Platform" — what is it, and does it close the loop? *Owner: doc 09.*
17. **⚠️ DeepSeek-V4.1-Flash vendor price.** This repo's README quotes $0.60/1M
    output (DeepSeek off-peak); Baseten's Model API quotes $1.20
    [[src](https://www.baseten.co/pricing/)]. Both are cited; reconcile which is the
    right comparison row for the platform's pitch. *Owner: doc 06.*
18. **⚠️ Utilisation break-even.** §4.5 gives the GPU hourly floor but not the
    requests/second at which a dedicated replica beats a serverless open model for
    each of the five repo models. That number is what qualifies a customer.
    *Owner: doc 06, from [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) +
    [`scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md).*

---

## Sources

All fetched 2026-09-19 unless the source states its own date.

**Vendor pricing and product pages**
- OpenAI API pricing — https://developers.openai.com/api/docs/pricing
- OpenAI supervised fine-tuning & distillation guide (incl. wind-down notice) — https://developers.openai.com/api/docs/guides/supervised-fine-tuning
- OpenAI Evals guide (incl. sunset dates) — https://developers.openai.com/api/docs/guides/evals
- Claude (Anthropic) pricing — https://claude.com/pricing
- Thinking Machines Tinker — https://thinkingmachines.ai/tinker/
- Tinker docs — https://tinker-docs.thinkingmachines.ai/
- Tinker models & pricing (Inkling, Qwen3.8-27B, GLM-5.3, DeepSeek-V3.1, Nemotron-3) — https://tinker-docs.thinkingmachines.ai/tinker/models/
- Baseten Training (Training Jobs GA, Loops early access) — https://www.baseten.co/products/training/
- Baseten pricing (Model APIs, dedicated per-GPU-minute) — https://www.baseten.co/pricing/
- Fireworks AI docs — https://docs.fireworks.ai/
- Langfuse pricing — https://langfuse.com/pricing
- Braintrust pricing — https://www.braintrust.dev/pricing
- W&B Weave — https://wandb.ai/site/weave/
- Databricks Agent Bricks — https://docs.databricks.com/aws/en/generative-ai/agent-bricks/
- Snorkel AI — https://snorkel.ai/
- NVIDIA NeMo microservices (incl. 2026-10-01 sunset) — https://docs.nvidia.com/nemo/microservices/latest/index.html
- NVIDIA Data Flywheel Blueprint (Apache-2.0; deprecated April 2026) — https://github.com/NVIDIA-AI-Blueprints/data-flywheel
- OpenTelemetry GenAI semantic conventions — https://github.com/open-telemetry/semantic-conventions-genai

**Legal / terms**
- Anthropic Usage Policy, eff. 2025-09-15 — https://www.anthropic.com/legal/aup
- Anthropic Commercial Terms of Service, eff. 2025-06-17 — https://www.anthropic.com/legal/commercial-terms
- Gemini API Additional Terms, eff. 2026-03-23 — https://ai.google.dev/gemini-api/terms
- OpenAI business terms — **not obtained (HTTP 403)**, see Open Question 2

**Papers**
- Orca: Progressive Learning from Complex Explanation Traces of GPT-4 (2023-06-05) — https://arxiv.org/abs/2306.02707
- Textbooks Are All You Need (phi-1) (2023-06-20) — https://arxiv.org/abs/2306.11644
- Zephyr: Direct Distillation of LM Alignment (2023-10-25) — https://arxiv.org/abs/2310.16944
- MiniLLM: On-Policy Distillation of Large Language Models (2023-06-14, ICLR 2024) — https://arxiv.org/abs/2306.08543
- On-Policy Distillation of Language Models: Learning from Self-Generated Mistakes (GKD) (2023-06-23, ICLR 2024) — https://arxiv.org/abs/2306.13649
- STaR: Bootstrapping Reasoning With Reasoning (2022-03-28) — https://arxiv.org/abs/2203.14465
- RLAIF vs. RLHF (2023-09-01, rev. 2024-09-03) — https://arxiv.org/abs/2309.00267
- A Survey on Knowledge Distillation of Large Language Models (2024-02-20, rev. 2024-10-21) — https://arxiv.org/abs/2402.13116
- Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena (2023-06-09) — https://arxiv.org/abs/2306.05685
- The RealHumanEval (2024-04-03, rev. 2024-10-14) — https://arxiv.org/abs/2404.02806
- Investigating Data Contamination in Modern Benchmarks for LLMs (2023-11-16, NAACL 2024) — https://arxiv.org/abs/2311.09783
- The Curse of Recursion: Training on Generated Data Makes Models Forget (2023-05-27, rev. 2024-04-14) — https://arxiv.org/abs/2305.17493
- S-LoRA: Serving Thousands of Concurrent LoRA Adapters (2023-11-06, rev. 2024-06-05) — https://arxiv.org/abs/2311.03285
- Video-MME (2024-05-31, rev. 2025-05-30) — https://arxiv.org/abs/2405.21075

**Vendor engineering blog**
- Thinking Machines, "On-Policy Distillation", 2025-10-27 — https://thinkingmachines.ai/blog/on-policy-distillation/

**This repository**
- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — formulas, legend, blended-cost definition
- [`research/README.md`](../README.md) — headline per-model best-GPU and $/1M table
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md), [`fit-matrix.md`](../matrix/fit-matrix.md), [`gpu-optimizations.md`](../matrix/gpu-optimizations.md), [`recommendations.md`](../matrix/recommendations.md)
- [`research/models/marlin2b/README.md`](../models/marlin2b/README.md) — video student: frame cap, prefill-bound fleet economics, no published measurement
- [`research/models/qwen3827b/`](../models/qwen3827b/README.md), [`deepseek41f/`](../models/deepseek41f/README.md), [`deepseek41fnvfp4/`](../models/deepseek41fnvfp4/README.md), [`kimik3/`](../models/kimik3/README.md)
- [`research/scaling/`](../scaling/) — bare-metal, routing, concurrency, utilisation, autoscaling, cold start, cost engineering, reference architectures
- [`research/cross-cutting/`](../cross-cutting/) — inference engines, quantization formats, serving optimizations, flash attention, cloud pricing, inferencex API

---

## Verification log (2026-09-19)

Adversarial fact-check of this document. 31 load-bearing claims were selected
(paper results, vendor prices and product features, version/date claims, cost
arithmetic, and cross-references into `research/`). Every external claim was
re-fetched from its **primary** source rather than trusted from the citation
already printed here; every derivation was recomputed with `python3`; every
`research/` cross-reference was opened. **12 corrected, 17 confirmed,
2 unverifiable.**

### Corrected

| # | Claim as printed | What the source says | Where fixed |
|---|---|---|---|
| 1 | Thinking Machines: AIME'24 **60 % → 70 %** | **60 % (post-SFT) → 74.4 %** | §3.1 |
| 2 | RL baseline "reaching 68 %" | **67.6 %** at 17,920 GPU-hours (1,800 ✓) | §3.1 |
| 3 | Personalisation "recovered IF-eval **45 % → 83 %**" | IF-eval was **85 %** at baseline, fell to **79 %** after mid-training, recovered to **83 %** — it never was 45 %, and the result is a *partial* recovery, not a gain | §3.1 |
| 4 | "while holding internal-QA at 41 %" | internal-QA **rose 36 % → 41 %**; "holding" understates it | §3.1 |
| 5 | "the **50–100×** figure is a compute-reduction claim" | the blog claims **9–30×** (9× given the SFT set, 30× including teacher cost); 50–100× appears nowhere, and the same row already printed 9–30× — an internal contradiction | §3.1 |
| 6 | NVIDIA: "Llama 3.2 **1B matched 70B accuracy**" | **≈98 % of** Llama 3.1 70B's accuracy, on the *tool-calling* task only, and NVIDIA scopes it to *"simpler tool calling use cases … route between a small set of tools"*. The 98.6 % cost figure ✓ | §3.1, §7.1 |
| 7 | Sora-2 "$0.10/s (720p) and $0.70/s (Sora-2-pro 1080p)" | Sora-2 $0.10/s; **Sora-2-pro $0.30 (720p) / $0.50 (1024p) / $0.70 (1080p)** | §3.4 |
| 8 | Fable 5.1 blended "$16.625¹ … slightly below" | exactly **$16.344** at its real 2.5 % cache read | §4.1 |
| 9 | Tinker Qwen3.8-27B "**$0.372 prefill**" | list prefill is **$1.86**; $0.372 is the *cached*-prefill rate (80 % discount). Train $4.103 ✓ and sample $5.595 ✓, so the $5,554 training estimate is unaffected | §4.3(b) |
| 10 | Tinker range "**$0.37–$14.58**/1M by model" | **$0.44** (Nemotron-3.5-Lightning) to $14.58 (GLM-5.3), train | §6 doc 05 |
| 11 | Serverless blended: GLM-5.3-Flash **$0.187**, DeepSeek V4 Pro **$1.588** | recomputed on each row's **listed** cached price (METHODOLOGY §6 forbids a generic 10 %): **$0.193** and **$1.577** | §4.2 |
| 12 | Opus 5 at 90 % cache hit = "**$6.51**/1M — still 108×" | `0.75 × (0.1×5 + 0.9×0.5) + 0.25×25` = **$6.9625**, i.e. **116×**. $6.51 is below the $6.625 floor that 100 % input caching gives, so it was arithmetically unreachable | §4.4 |

Three further wordings were tightened to the source rather than counted as
errors: Baseten's promotion and weights-ownership sentences (paraphrases printed
inside quotation marks), OpenAI's distillation workflow (it prompt-tunes the
teacher, it does not fine-tune it), and Tinker's model count/size range.

### Confirmed against the primary source

- **All OpenAI list prices** — GPT-6 Astra $10 / $1 / $50; GPT-5.6 Sol $4 / $0.40 / $20; Terra $2 / $0.20 / $12; Luna $0.20 / $0.02 / $1.20; Batch = 50 % off [[src](https://developers.openai.com/api/docs/pricing)].
- **All Anthropic list prices** — Opus 5 $5 / $0.50 read / $6.25 write / $25; Fable 5.1 $10 / $0.25 / $12.50 / $50; Sonnet 5 $2 / $0.20 / $10; Haiku 4.5 $1 / $0.10 / $5; batch 50 % [[src](https://claude.com/pricing)].
- **All Baseten prices** — the five Model API rows, and dedicated B200 $0.16633/GPU-min, H100 $0.10833/GPU-min [[src](https://www.baseten.co/pricing/)]. $9.98/h and $7,285/mo at 730 h recompute exactly.
- **Every blended figure in §4.1**, the four per-request columns, the 138× / 276× / 64× spreads, the $6,560 / $3,280 annotation estimate, the $5,554 training estimate, the whole §4.3 amortisation table (all nine cells, ±1 in the last digit), the batched-Opus $4.16, and all six §5.3 sample sizes (`(1.6449+0.8416)² · 2p(1−p)/δ²`, α=0.05, β=0.20) — recomputed with `python3`, all correct.
- **0.98¹⁰ = 0.817** → "0.82 session-level" ✓ (§5.4).
- **Orca** — 13B, GPT-4 + ChatGPT teachers, ">100 %" over Vicuna-13B on BBH, 42 % on AGIEval, "parity with ChatGPT on BBH", "4 pts gap with optimized system message" on SAT/LSAT/GRE/GMAT [[src](https://arxiv.org/abs/2306.02707)].
- **phi-1** — 1.3B, 6B web + 1B synthetic ≈ 7B tokens, "4 days on 8 A100s", 50.6 % HumanEval / 55.5 % MBPP pass@1 [[src](https://arxiv.org/abs/2306.11644)].
- **Zephyr-7B** — "surpasses Llama2-Chat-70B, the best open-access RLHF-based model" on MT-Bench, via dSFT + AIF + dDPO [[src](https://arxiv.org/abs/2310.16944)].
- **STaR** — "performs comparably to fine-tuning a 30× larger state-of-the-art language model on CommonsenseQA" [[src](https://arxiv.org/abs/2203.14465)].
- **MT-Bench / LLM-as-judge** — "over 80 % agreement, the same level of agreement between humans"; the four biases named are exactly position, verbosity, self-enhancement and limited reasoning [[src](https://arxiv.org/abs/2306.05685)].
- **S-LoRA** — "improve the throughput by up to 4 times" vs HF PEFT and vLLM with naive LoRA, Unified Paging over one memory pool for adapter weights + KV, thousands of adapters [[src](https://arxiv.org/abs/2311.03285)].
- **Contamination** — ChatGPT 52 % / GPT-4 57 % exact match on masked MMLU options, NAACL 2024 [[src](https://arxiv.org/abs/2311.09783)].
- **Video-MME** — 900 videos, 254 hours, 2,700 QA pairs, 11 s to 1 hour, subtitles + audio, "rigorous manual labeling by expert annotators" [[src](https://arxiv.org/abs/2405.21075)]. The §5.5 "≈3 items per video" inference follows (2,700/900 = 3).
- **Anthropic AUP** (eff. 2025-09-15) and **Commercial Terms §D.4** (eff. 2025-06-17) — both quotes verbatim and correctly attributed [[aup](https://www.anthropic.com/legal/aup)] [[terms](https://www.anthropic.com/legal/commercial-terms)].
- **Gemini API Additional Terms**, eff. 2026-03-23 — quote verbatim [[src](https://ai.google.dev/gemini-api/terms)].
- **The three sunsets** — NVIDIA data-flywheel *"Deprecation notice (Apr 2026) … no longer actively maintained, and new production use is not recommended"*, Apache-2.0; NeMo Microservices *"will be sunset on October 1, 2026. All new development has moved to NeMo Platform"*, SDK 2.0.1 / image 26.03.1, Customizer listing LoRA+SFT+DPO and **not** distillation; OpenAI Evals read-only 2026-10-31, shutdown 2026-11-30; OpenAI fine-tuning *"no longer accessible to new users"* with gpt-4.1/-mini/-nano the surviving bases. All four verbatim.
- **Langfuse** $0 (50k units) / $29 / $199 / $2,499 with $6–$8/100k graduated overage; **Braintrust** $0 / $249 / Enterprise on GB-processed + per-1k-scores, on-prem on Enterprise, Loop agent on Pro+; **Snorkel**'s benchmark list (Terminal-Bench 4.0, Senior SWE-bench, OSWorld 2.0) and both quoted phrases; **Databricks Agent Bricks** page dated 2026-09-15, Agent Services "(Beta)", and — confirming this document's own ⚠️ — automatic optimisation and synthetic data are indeed absent from it.
- **OpenTelemetry GenAI semconv** — `open-telemetry/semantic-conventions-genai` exists and is active (380 stars, spans/metrics/events for GenAI clients, MCP and provider-specific conventions). The §6 doc-02 recommendation stands.
- **Repo cross-references** — the five §4.2 rows reproduce [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) exactly ($0.0092/$0.022 marlin2b·B300; $0.0602 qwen3827b·B300 and $0.159 H200; $0.1485/$0.565 deepseek41fnvfp4·B200; $0.190/$0.462 deepseek41f·B200; $2.3811/$7.3926 kimik3·B300); the min-GPU column matches [`README.md`](../README.md) §3 and [`fit-matrix.md`](../matrix/fit-matrix.md) §5; the 240-frame / ~23,560-token cap and "no published throughput or latency measurement on any hardware" are [`models/marlin2b/README.md`](../models/marlin2b/README.md) items 9 and 10 verbatim; open question 17's $0.60/1M is README line 58's DeepSeek off-peak column.

### Unverifiable in this session

- **Open questions 1, 4 and 5 (market coverage, Predibase→Rubrik, OpenPipe) are still open.** This fact-check hit the *same* wall the document's research-method caveat describes: the session's WebSearch budget was exhausted (200/200) before the competitor sweep, so no new platform could be discovered by name and the Predibase acquisition could not be confirmed. §7 remains a floor on the landscape, not a scan of it. ⚠️
- **Tinker's "LoRA from 1B to 1T+"** could not be confirmed on either page fetched; the visible model list runs Qwen3.5-4B → Nemotron-3-Ultra-550B-A55B. Marked ⚠️ in §7.1 rather than removed, since the claim may sit on a page not reachable without search.

### Note on the blended-cost basis

METHODOLOGY §6 requires the vendor's **published** cached-input ratio for vendor
API comparisons, "never a generic 10 %". This document's blended column applies a
flat 10 %, which is exact for GPT-6/GPT-5.6, Opus 5, Sonnet 5, Haiku 4.5 and three
of the five Baseten rows, and wrong for Fable 5.1, GLM-5.3-Flash and DeepSeek V4
Pro — all three now recomputed above. Any future row added to §4.1 or §4.2 must
use the listed cached price, not the 0.4125 shorthand.
