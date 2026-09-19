# Training: SFT, preference optimization, RL and distillation recipes

Research date **2026-09-19**. This document is stage **S5** of the loop defined in
[`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) §1.2: it takes
versioned datasets from S4 and a base model, and produces a candidate checkpoint (S6)
that the offline gate (S7) will judge and the hardware-optimisation stage (S8) will
re-shape. It does not re-derive the loop, the invariants, the commercial thesis or the
cost formulas — those are doc 00 and [`research/METHODOLOGY.md`](../METHODOLOGY.md).

> **Numbering note.** Doc 00 §6 assigns "Training (SFT / DPO / RL / distillation)" to
> *doc 05* and data/annotation to *doc 03*, while this directory's file numbering puts
> training at **03**. This file is the training document; every "doc 05" reference in
> doc 00 (including Open Question 6, teacher logprobs, and Open Question 13, Inkling as
> a student) is answered here. Where this document says "the annotation doc" it means
> whichever file carries doc 00 §6's row 03 (data capture, analysis, annotation).

**Conventions** (same legend as doc 00 and METHODOLOGY):

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source, fetched 2026-09-19 unless the source states its own date. |
| **⚠️ TO BE VERIFIED** | No primary source found, or the claim is an inference; reasoning stated inline. |
| `est.` | Arithmetic from METHODOLOGY formulas or sourced inputs. Shown, not measured. |
| `meas.` | A published measurement, cited. |
| `vendor` | A vendor's own claim about its own product. Not independently verified. |

> **Research-method caveat.** As with doc 00, **this session's WebSearch budget was
> exhausted before this agent started** (200/200 calls consumed by earlier agents in the
> programme). Every source below was reached by WebFetch or `curl` against a known or
> link-followed URL. Consequence: the framework and vendor survey in §3 is a survey of
> things I could name and fetch, not a market scan. Absence from §3 is not evidence of
> absence. Where a paper's abstract page did not contain a number I wanted, I fetched the
> HTML full text or said so rather than recalling it.

**Cross-references used throughout.** Student and teacher hardware fits, KV budgets and
$/GPU-hour tiers come from [`research/METHODOLOGY.md`](../METHODOLOGY.md) §8 (pinned
model inputs), [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) §3 (the
`low`/`high`/`res1y` price table) and [`research/matrix/fit-matrix.md`](../matrix/fit-matrix.md).
The five repo models are the candidate students and self-hosted teachers:
DeepSeek-V4.1-Flash (763.2 B total / 7.89 B prefill / 16.11 B decode active),
its NVFP4 build, **Qwen3.8-27B** (27.78 B dense, 55.56 GB BF16),
**Kimi-K3** (2,779.9 B total / 104.19 B active, fits 8×B300 at 195 GB/GPU) and
**Marlin-2B** (2.21 B unique, 5.444 GB BF16, video at 2 fps ≤ 240 frames).

---

## 1. Method map: what each recipe needs and what it yields

### 1.0 The one-page map

Read this table as a *ladder*, cheapest first, exactly as doc 00 §3.2 frames it. The
platform should climb only as far as the eval gate demands, and every rung above 3
should be justified by a measured failure of the rung below it.

| # | Method | Needs from S4 | Needs from teacher | Yields | Marginal cost driver | Primary failure mode |
|---:|---|---|---|---|---|---|
| 0 | Prompt + model swap | Nothing | Nothing | A cheaper incumbent tier, sometimes at parity | Zero | Doesn't work often enough to be a business |
| 1 | **SFT on teacher completions** (sequence-level KD) | Prompts + teacher outputs | Sampling only | Format, style, tool syntax, task behaviour | Teacher output tokens | Inherits teacher errors; exposure bias |
| 2 | **Rejection-sampling FT** (RFT / STaR / ReST-EM) | Prompts + a verifier or rubric | N samples per prompt | Correctness-filtered behaviour; can exceed the teacher on verifiable tasks | Teacher samples × N | Needs a verifier; filtering can collapse diversity |
| 3 | **Preference optimisation** (DPO / IPO / KTO / ORPO / SimPO) | Preference pairs or binary labels | Ranking or pairwise judgements | Tone, refusals, "which of two plausible answers" | Judge calls | Reward hacking at the margin; length bias |
| 4 | **On-policy distillation** (GKD / MiniLLM / DistiLLM / TM) | Prompts only | **Per-token logprobs on the student's own samples** | Dense per-token supervision; best sample-efficiency published | Teacher logprob calls **per training step** | Needs a teacher that scores arbitrary continuations (§1.4 — frontier APIs do not) |
| 5 | **RLVR / GRPO** against a verifiable reward | Prompts + a programmatic checker | None | Genuinely new capability on checkable tasks | Rollouts (20× the GPU-time per token of SFT, §6.4) | Reward hacking; spurious gains (§5.5) |
| 6 | **RLHF with a learned RM (PPO)** | Preference data + an RM | Preference labels | Open-ended quality where no verifier exists | RM training + rollouts + RM inference | RM overoptimisation (§5.3) |

**The load-bearing asymmetry for this platform**: rungs 1–3 spend teacher tokens
proportional to **dataset size**; rungs 4–6 spend teacher/verifier calls proportional to
**training steps**. A customer's annotation budget therefore behaves completely
differently above and below rung 4, and the annotation doc's cost model must branch on it.

### 1.1 SFT: full fine-tuning vs LoRA / QLoRA / DoRA

**Mechanism.** Minimise cross-entropy of the target completion given the prompt, with
loss masked to completion tokens. Everything else in this document is a modification of
this objective.

**LoRA** [[src](https://arxiv.org/abs/2106.09685)] freezes the base weights and trains
low-rank update matrices. The 2024–2026 evidence has converged on a clear picture that
matters more to this platform than any other single result:

- **LoRA underperforms full fine-tuning when the data exceeds adapter capacity, and
  matches it when it does not.** Biderman et al. found LoRA "substantially underperforms
  full finetuning" for programming and maths in both instruction tuning (~100 K
  prompt-response pairs) and continued pretraining (20 B tokens), and that "full
  finetuning learns perturbations with a rank that is 10-100X greater than typical LoRA
  configurations" — but that LoRA "better maintains the base model's performance on tasks
  outside the target domain" and beats weight decay and dropout as a forgetting
  regulariser [[src](https://arxiv.org/abs/2405.09673), TMLR 2024].
- Thinking Machines' "LoRA Without Regret" (2025-09-29) identifies the **low-regret
  regime**: LoRA matches full FT when it is "applied to all weight matrices, particularly
  MLP and MoE layers" and is "not capacity-constrained"; attention-only LoRA
  "significantly underperforms" MLP-only; the optimal LoRA learning rate is
  **~10× the full-FT learning rate** (≈15× for short ~100-step runs); and **LoRA is less
  tolerant of large batch sizes**, with a gap that grows independently of rank
  [[src](https://thinkingmachines.ai/blog/lora/)].
- Tinker's own docs turn that into a **capacity rule of thumb you can plan against**:
  *"LoRA will give good results as long as the number of LoRA parameters is at least as
  large as the number of completion tokens"*, with default rank 32 and the same ~10× LR
  multiplier [[src](https://tinker-docs.thinkingmachines.ai/tinker/lora-primer/)]. They
  also state *"For supervised fine-tuning on small-to-medium-sized instruction-tuning and
  reasoning datasets, LoRA performs the same as full fine-tuning"* and *"LoRA performs
  equivalently to FullFT for reinforcement learning even with small ranks"* — the latter
  backed by the blog's finding that policy-gradient RL matched FullFT even at rank 1,
  because the MATH example's ~10,000 problems × 32 samples means "the whole training
  process only needs to absorb 320,000 bits" (⚠️ the "~3 M LoRA parameters" comparand is
  not stated on that page; the bits figure is) [[src](https://thinkingmachines.ai/blog/lora/)].

> **Decision rule (LoRA vs full FT), stated for the platform.**
> Compute `completion_tokens = Σ len(target)` over the training set. Compute
> `lora_params = 2 · r · Σ (d_in + d_out)` over the adapted matrices. **If
> `lora_params ≥ completion_tokens`, use LoRA** at rank 32 (default) on all matrices
> including MLP/MoE, LR ≈ 10× the full-FT LR, and keep the batch size modest. **Else**
> raise the rank until the inequality holds, or fall back to full FT. Worked for
> Qwen3.8-27B in §6.3.

Why the platform should *want* LoRA to be sufficient, beyond the training bill: an
adapter is what makes **multi-tenant serving** work. Doc 00 §4.5 already names S-LoRA-style
unified paging — thousands of adapters over shared base weights, "up to 4×" throughput
over HF PEFT and vLLM [[src](https://arxiv.org/abs/2311.03285)] — as the single
highest-leverage architecture decision in the platform. A full fine-tune forecloses it.
**That is a serving argument that should outrank a small quality delta**, and it is the
main reason to pay the rank-raising cost rather than switch to full FT.

**QLoRA** [[src](https://arxiv.org/abs/2305.14314)] adds 4-bit NormalFloat, double
quantisation and paged optimizers, enabling "a 65B parameter model on a single 48GB GPU
while preserving full 16-bit finetuning task performance" (vendor/author claim; Guanaco
reached "99.3% of the performance level of ChatGPT while only requiring 24 hours of
finetuning on a single GPU"). On 268 GB B300s, QLoRA's memory argument is close to moot
for a 27 B student (§6.2) — it matters for a customer who wants to train on their own
single-GPU box, not for the platform's fleet.

**DoRA** [[src](https://arxiv.org/abs/2402.09353), ICML 2024 oral] decomposes weights
into magnitude and direction and applies LoRA only to the direction, reporting
consistent gains over LoRA on LLaMA, LLaVA and VL-BART across commonsense reasoning,
visual instruction tuning and image/video-text understanding, and — the part that matters
operationally — it "avoid[s] any additional inference overhead". DoRA is a low-risk
default upgrade *if* the training framework supports it for the target architecture;
LLaMA-Factory lists DoRA among supported algorithms
[[src](https://raw.githubusercontent.com/hiyouga/LLaMA-Factory/main/README.md)].

### 1.2 Sequence-level KD (the Orca pattern)

**Mechanism.** Sample completions from the teacher on the customer's prompts; SFT the
student on them. No logits needed, so any teacher with an API works.

**Evidence.** Orca trained a 13 B student on GPT-4 "explanation traces; step-by-step
thought processes; and other complex instructions", reaching >100 % improvement over
Vicuna-13B on Big-Bench Hard and 42 % on AGIEval, and **parity with ChatGPT on BBH**
while trailing GPT-4 [[src](https://arxiv.org/abs/2306.02707)]. "Distilling step-by-step"
showed a **770 M T5 beating a few-shot-prompted 540 B PaLM using 80 % of the available
training data**, by training on teacher *rationales* as an auxiliary multi-task target
rather than only the answer [[src](https://arxiv.org/abs/2305.02301), ACL Findings 2023].

**The mechanism inside that result is directly reusable here**: if the teacher emits a
rationale and a final answer, train the student with two heads/targets (rationale and
answer) rather than concatenating them, and serve only the answer. It buys accuracy at
zero serving cost — but see §4.6 on whether the customer's contract allows a different
output shape at serve time (invariant I4).

**Failure modes.** (a) The student cannot exceed the teacher on anything the teacher gets
wrong, unless a verifier filters (rung 2). (b) **Exposure bias**: the student is trained
only on teacher-distribution prefixes and is evaluated on its own, which is exactly the
gap the next rung closes [[src](https://arxiv.org/abs/2306.13649)].

### 1.3 Logit / on-policy KD: GKD, MiniLLM, DistiLLM, on-policy distillation

Four related lines, all fixing the same train/serve distribution mismatch.

| Method | Core idea | Divergence | Source |
|---|---|---|---|
| **MiniLLM** | Train on **reverse KLD** so the student does not "overestimate the low-probability regions" of the teacher; 120 M–13 B | Reverse KL | [[src](https://arxiv.org/abs/2306.08543)] ICLR 2024 |
| **GKD** | Train the student on **its own self-generated sequences** with teacher feedback on those sequences; composes with RL fine-tuning | Generalised (JSD family) | [[src](https://arxiv.org/abs/2306.13649)] ICLR 2024 |
| **DistiLLM** | **Skew KL** loss + an "adaptive off-policy approach" to reuse student generations; "up to 4.3× speedup compared to recent KD methods" | Skew KL | [[src](https://arxiv.org/abs/2402.03898)] ICML 2024 |
| **TM on-policy distillation** | Sample from the student, take **per-token teacher logprobs on those exact tokens**, set the per-token advantage to the negative reverse KL, train with policy gradient | Reverse KL, per token | [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)] 2025-10-27 |

**The Thinking Machines numbers are the strongest published case for this rung and are
worth quoting exactly** (all `meas.`, vendor-published, Qwen3-8B-Base student, Qwen3-32B
teacher, OpenThoughts-3 prompts):

| Configuration | AIME'24 | Compute |
|---|---:|---|
| SFT-400K baseline | 60 % | ⚠️ 3.8 × 10²⁰ FLOPs (not found on the page re-fetched 2026-09-19; the page states 8.4 × 10¹⁹ teacher / 8.2 × 10¹⁹ student FLOPs for the distillation run) |
| SFT-2M (extrapolated) | ~70 % | ⚠️ 1.5 × 10²¹ FLOPs (same — unconfirmed on re-fetch) |
| RL (Qwen3 report) | 67.6 % | **17,920 GPU-hours** |
| **On-policy distillation** | **74.4 %** | **1,800 GPU-hours** |

— with the additional claims that distillation reached teacher performance in "7-10x
fewer gradient steps", an overall "50-100x" compute reduction versus RL on DeepMath, and
"9-30x" cheaper than off-policy distillation depending on whether teacher compute is
amortised [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)]. Tinker's own
distillation recipe page reproduces the shape at smaller scale: **~65 % AIME'24 from SFT
(rank-128 LoRA, 3,000 steps) versus ~76.7 % from on-policy distillation (rank-128 LoRA,
200 steps, 16 K-token rollouts)** — student `Qwen3.5-9B-Base`, teacher `Qwen3.5-9B`, i.e.
**a same-size post-trained teacher, not a larger one** [[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/)].

**The second result in that blog is the one this platform should actually build around**,
because it is about *not forgetting*, which is the loop's recurring problem (§1.8, §4.5).
Qwen3-8B mid-trained on internal company documents went from 18 %→36 % on internal QA but
collapsed from 85 %→79 % IF-eval at a 70 % document mix (⚠️ the further "45 % at 100 %
documents" figure was not found on re-fetch 2026-09-19); on-policy distillation
**against the model's own pre-midtrain checkpoint as teacher**, on Tulu3 prompts,
recovered IF-eval to 83 % while *keeping* 41 % internal QA
[[src](https://thinkingmachines.ai/blog/on-policy-distillation/)]. That is a
self-contained, teacher-cost-free mechanism for repairing behaviour regressions after a
knowledge update — precisely the operation the closed loop performs every cycle.

**⚠️ The blocking constraint, and the answer to doc 00 Open Question 6.**
On-policy distillation requires per-token teacher logprobs **for tokens the student
chose**, not for tokens the teacher generated. As of 2026-09-19:

| Teacher | Logprobs for arbitrary supplied continuations? | Source |
|---|---|---|
| **OpenAI chat/Responses API** | **No.** `logprobs` returns "the log probabilities of each output token returned in the `content` of `message`" — *output tokens only*; `top_logprobs` is capped at 20; **no `echo` parameter exists** in the chat API | [[src](https://developers.openai.com/api/docs/api-reference/chat/create)] |
| **OpenAI legacy Completions API** | **Partially.** `echo` ("Echo back the prompt in addition to the completion") plus `logprobs` (max **5**) — but only for `gpt-3.5-turbo-instruct`, `davinci-002`, `babbage-002`, i.e. **not** any frontier model | [[src](https://developers.openai.com/api/docs/api-reference/completions/create)] |
| **Anthropic Messages API** | **No.** A search of the Messages reference returns zero occurrences of `logprob`; no request parameter and no response field | [[src](https://platform.claude.com/docs/en/api/messages)] |
| **Google Gemini** | **⚠️ TO BE VERIFIED.** The `generateContent` reference I fetched documents `candidates`, `promptFeedback` and `usageMetadata` with no logprobs field; I could not confirm a `responseLogprobs` option on that page, and will not assert one from memory | [[src](https://ai.google.dev/api/generate-content)] |
| **Any open-weights teacher under vLLM** | **Yes.** `prompt_logprobs` = "Number of log probabilities to return per prompt token. When set to -1, return all `vocab_size` log probabilities" — put the student's trajectory in the prompt and read the teacher's per-token scores | [[src](https://docs.vllm.ai/en/latest/api/vllm/sampling_params.html)] |

**Conclusion, and it is an architecture conclusion, not a preference**: the strongest
distillation rung is **only available with an open-weights teacher you host** (or a
service that hosts one for you, e.g. Tinker's `teacher_model` argument
[[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/)]). This
converges with doc 00 §8.1's *legal* conclusion — all three frontier vendors' terms
prohibit distillation — from a completely independent direction. Two independent reasons
to make the default teacher **Kimi-K3 on 8×B300 or DeepSeek-V4.1-Flash on 4** (TP4 is
[`matrix/fit-matrix.md`](../matrix/fit-matrix.md)'s **recommended** shape; its "min 2" is
TP2 *plus mandatory* `--engram-config '{"cpu_offload":true}'`, because TP2-resident is
255.3 GB against a 241.2 GB budget — arithmetically impossible without the offload, and a
host-resident Engram table is the wrong shape for a prefill-heavy `prompt_logprobs`
teacher; corrected 2026-09-19) is as close to a settled design
decision as this programme has produced.

**Tokenizer coupling.** Per-token KD requires a shared tokenizer, or a cross-tokenizer
alignment step. Tinker's recipe exposes `model_name` and `teacher_model` independently but
"does not explicitly specify tokenizer compatibility requirements"
[[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/)]; NeMo RL
lists "Cross-tokenizer" as a v0.7.0 (2026-07-25) feature
[[src](https://raw.githubusercontent.com/NVIDIA-NeMo/RL/main/README.md)]. **⚠️ TO BE
VERIFIED**: the quality cost of cross-tokenizer KD. Until verified, the platform should
prefer teacher/student pairs from the same family (e.g. a large Qwen teaching
Qwen3.8-27B) whenever the choice is free.

**Prompt distillation** is the cheap cousin worth a line: the teacher runs *with* the
customer's 3–20 K-token system prompt, the student is trained to reproduce the behaviour
*without* it — "internalize the prompt into its parameters"
[[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/prompt-distillation/)].
For a customer whose bill is dominated by a huge cached system prompt (doc 00 §2.1), this
alone can be most of the cost win, and it is a pure SFT job. No quantitative results are
published on that page (⚠️), so treat the size of the win as unmeasured.

### 1.4 Rejection-sampling FT: RFT, STaR, ReST-EM

**Mechanism.** Sample N completions per prompt from the current policy (or the teacher),
keep the ones a verifier or rubric accepts, SFT on those. Iterate.

- **STaR** bootstraps rationales from correct answers, with rationalisation for failures
  [[src](https://arxiv.org/abs/2203.14465)].
- **RFT**: LLaMA-7B on GSM8K went from **35.9 % (SFT) → 49.3 %** using rejection samples
  aggregated from several models, and the paper's broader finding is that "pre-training
  loss is a better indicator of the model's performance than the model's parameter count"
  [[src](https://arxiv.org/abs/2308.01825)].
- **ReST-EM** frames it as expectation-maximisation — generate, filter on binary feedback,
  retrain — and reports that it "scales favorably with model size and significantly
  surpasses fine-tuning only on human data" on MATH and APPS with PaLM-2
  [[src](https://arxiv.org/abs/2312.06585), TMLR].

**Why this rung matters commercially**: it is the *only* cheap rung that can make the
student **exceed** the teacher, and it needs no logprobs — so it is the fallback when
§1.3's constraint bites. For this platform the verifier is usually free and already
exists: JSON-schema validity, tool-name validity, argument-type checking, SQL that
parses, a retrieval citation that resolves, a test that passes. **Build the verifier
before building the judge.**

**Failure mode.** Filtering on a verifier collapses output diversity and can over-fit the
verifier's blind spots (a tool call that is *valid* but *wrong*). Mitigation: keep the
verifier a gate, not a reward, and keep a rubric/judge check on a sample of what the gate
passes.

### 1.5 Preference optimisation: DPO, IPO, KTO, ORPO, SimPO

| Method | What it needs | Reference model? | Headline claim | Source |
|---|---|---|---|---|
| **DPO** | Preference pairs | Yes (frozen) | "stable, performant, and computationally lightweight", removing RM fitting and sampling during fine-tuning; exceeds PPO-based RLHF on sentiment control, matches/improves on summarisation and single-turn dialogue | [[src](https://arxiv.org/abs/2305.18290)] |
| **IPO (ΨPO)** | Preference pairs | Yes | Identifies DPO's reliance on substituting "pairwise preferences … with pointwise rewards" as a source of overfitting under distribution shift; ΨPO "bypasses both approximations" | [[src](https://arxiv.org/abs/2310.12036)] |
| **KTO** | **Binary** desirable/undesirable labels — *no pairs* | Yes | "matches or exceeds the performance of preference-based methods" from 1 B to 30 B | [[src](https://arxiv.org/abs/2402.01306)] ICML 2024 |
| **ORPO** | Preference pairs | **No** | Folds preference into SFT with an odds-ratio penalty; UltraFeedback only: AlpacaEval 2.0 up to **12.20 %**, MT-Bench **7.32**, IFEval **66.19 %** (125 M–7 B) | [[src](https://arxiv.org/abs/2403.07691)] |
| **SimPO** | Preference pairs | **No** | Average log-prob as implicit reward (length-normalised) + target margin; beats DPO by up to **6.4 pts** on AlpacaEval 2 and **7.5 pts** on Arena-Hard; Gemma-2-9B-it at **72.4 %** LC win rate | [[src](https://arxiv.org/abs/2405.14734)] NeurIPS 2024 |

**KTO is the one that fits this platform's data best, and that is not obvious.** Production
traffic yields *binary* signals for free — thumbs, retries, escalations, ticket resolved,
transaction completed — and almost never yields matched pairs. Constructing pairs requires
a second generation and a judge call; KTO does not. Doc 00 §5.1 already argues that an
outcome signal beats any judge; **KTO is the training objective that consumes that signal
directly**, so the annotation doc should be told to preserve per-response binary outcomes
and *not* to discard unpaired examples.

**On DPO vs PPO.** The academic default flipped twice. Xu et al. find "PPO surpassed
competing methods across all tested scenarios", reaching state of the art on code
competition, and attribute DPO's apparent edge on academic benchmarks to PPO
implementation detail rather than algorithmic superiority
[[src](https://arxiv.org/abs/2404.10719), ICML 2024]. **Decision rule for the platform**:
use DPO/KTO family when the signal is offline preference data and the budget is small (one
extra forward pass, no rollouts); reserve PPO/GRPO for when you have a *reward* (learned
or programmatic) and enough GPU budget to generate (§6.4), and never adopt PPO because a
paper says it wins — it wins when it is implemented well, which is a staffing claim.

**⚠️ Length bias is a first-order product risk here**, not an academic footnote. Doc 00
§5.1 warns that a verbose-biased judge rewards a verbose student. SimPO's entire
contribution is length normalisation of the implicit reward
[[src](https://arxiv.org/abs/2405.14734)]. **Rule: any preference-optimisation run in this
platform reports the mean output-length delta alongside the win rate, and a win that
comes with >15 % length inflation is treated as unproven until length-controlled.** ⚠️ The
15 % threshold is my proposal, not a sourced value.

### 1.6 RLHF with reward models (PPO), and RLAIF

Classical RLHF: train an RM on preferences, optimise the policy against it with PPO under
a KL penalty to a reference. **RLAIF** replaces the human labeller with an LLM and
"achieves comparable performance to RLHF" on summarisation and dialogue; its **direct
RLAIF (d-RLAIF)** variant "circumvents RM training by obtaining rewards directly from an
off-the-shelf LLM", and the paper reports that RLAIF beats supervised baselines even when
the feedback model is the same size as, or the identical checkpoint to, the policy
[[src](https://arxiv.org/abs/2309.00267), ICML 2024].

For this platform, d-RLAIF is tempting (no RM to train) and dangerous (the judge becomes
the reward, so every judge pathology in doc 00 §5.1 becomes a *training* pathology rather
than only a *measurement* one). See §5.

### 1.7 GRPO / RLVR and their 2025–2026 variants

- **GRPO** (DeepSeekMath) is "a variant of Proximal Policy Optimization (PPO)" that
  "enhances mathematical reasoning abilities while concurrently optimizing the memory
  usage of PPO" by replacing the value network with a group-relative baseline; DeepSeekMath
  7B reached **51.7 % on MATH** without tools or voting, 60.9 % with self-consistency over
  64 samples [[src](https://arxiv.org/abs/2402.03300)].
- **RLVR** (Tülu 3) — "Reinforcement Learning with Verifiable Rewards" — is the third stage
  after SFT and DPO in a fully open recipe over Llama 3.1 bases, with released data, code
  and infrastructure [[src](https://arxiv.org/abs/2411.15124)]. The open recipe matters
  more than the scores: it is the closest published thing to a reference post-training
  pipeline the platform can copy.
- **DAPO** adds Clip-Higher, Dynamic Sampling, Token-Level Policy Gradient Loss and
  Overlong Reward Shaping, "achieves 50 points on AIME 2024 using Qwen2.5-32B base model"
  and is open-sourced on verl [[src](https://arxiv.org/abs/2503.14476)]; verl's README
  states it surpassed the previous SOTA set by DeepSeek's GRPO on DeepSeek-R1-Zero-Qwen-32B
  [[src](https://raw.githubusercontent.com/volcengine/verl/main/README.md)].
- **GSPO** moves clipping/rewarding/optimisation from token level to **sequence level** and
  "notably stabilizes Mixture-of-Experts (MoE) RL training"
  [[src](https://arxiv.org/abs/2507.18071)]. **If the platform ever RLs an MoE student —
  and doc 00's model list includes three MoEs — this is the default, not GRPO.**

**Decision rule.** RLVR is worth its cost only where a *programmatic* checker exists whose
pass/fail the customer would accept as ground truth. For the support-agent task in §8 that
means: schema validity, tool-argument validity, retrieval-citation resolution, and (if the
customer has it) ticket-resolution outcome. For open-ended answer quality it does not, and
rung 3 or rung 4 is the right answer.

### 1.8 Process reward models

**Let's Verify Step by Step**: process supervision "significantly outperforms outcome
supervision" on MATH, with the process-supervised model solving **78 %** of a
representative MATH test subset, and the release of **PRM800K** — 800,000 step-level human
annotations [[src](https://arxiv.org/abs/2305.20050)]. The obvious follow-on question for a
platform is whether PRM labels can be synthesised instead of hand-written, and the answer
from Qwen's team is a qualified no: "commonly used Monte Carlo (MC) estimation-based data
synthesis for PRMs typically yields inferior performance" relative to human annotation or
LLM-as-judge, because MC estimation relies on a completion model to verify steps; they also
document that best-of-N evaluation of PRMs is biased (responses with correct answers but
flawed steps), and propose a consensus filter combining MC estimation with LLM-as-judge
[[src](https://arxiv.org/abs/2501.07301)].

**Platform verdict: do not build PRMs in the first two years.** PRM800K is 800 K *human*
step annotations for one domain; the customer tasks here are support agents and video QA,
where the analogous label does not exist and the synthesis shortcut is documented not to
work. The cheap substitute is **per-step programmatic verification of the agent
trajectory** (did the tool exist, did the arguments type-check, did the retrieval resolve),
which is a PRM in the only sense that pays.

### 1.9 Self-play and self-distillation

- **Self-Rewarding LMs**: the model judges its own outputs to generate its own preference
  data; three iterations on Llama 2 70B produced a model that "outperforms many existing
  systems … including Claude 2, Gemini Pro, and GPT-4 0613" on AlpacaEval 2.0
  [[src](https://arxiv.org/abs/2401.10020)].
- **Self-distillation as repair** is the variant this platform should actually use: the
  pre-update checkpoint teaching the post-update one, per §1.3's IF-eval recovery result
  [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)]. Tinker ships an SDFT
  recipe [[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/sdft/)].

**Hard rule, inherited from doc 00's "what to avoid" list**: never train on the *student's
own outputs as labels* without a verifier or an external teacher in the loop. §4.5 gives
the mechanism by which that goes wrong.

### 1.10 Multi-task and continual fine-tuning without forgetting

This is the part of training that the closed loop stresses hardest, because every cycle is
a continual-learning step on a model that already passed a gate.

**The damage is real and scale does not save you.** Luo et al. measure catastrophic
forgetting across 1 B–7 B during continual instruction tuning, with degradation in domain
knowledge, reasoning and reading comprehension, and — counterintuitively — "as the model
scale increases, the severity of forgetting intensifies" in that range
[[src](https://arxiv.org/abs/2308.08747)]. The TM personalisation experiment above is the
same phenomenon at 8 B, measured on IF-eval [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)].

Three mitigations, in the order the platform should try them:

1. **Replay.** Ibrahim et al. show that LR re-warming + re-decaying + **replaying previous
   data** is "sufficient to match the performance of fully re-training from scratch" across
   English→English and English→German shifts at 405 M and 10 B, "using only a fraction of
   the compute" [[src](https://arxiv.org/abs/2403.08763)]. **⚠️** the specific replay
   percentage was not on the abstract page I fetched; the platform should treat the *shape*
   as sourced and calibrate the fraction empirically per customer.
2. **Regularisation by parameterisation.** LoRA is itself the cheapest regulariser: it
   "better maintains the base model's performance on tasks outside the target domain" and
   beats weight decay and dropout at it [[src](https://arxiv.org/abs/2405.09673)]. Note the
   TM blog's caveat that LoRA "showed similar forgetting" in *their* midtrain experiment
   [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)] — i.e. LoRA reduces
   forgetting but does not abolish it, and the two sources disagree in emphasis. **⚠️ Treat
   "LoRA prevents forgetting" as unsettled; measure it per task.**
3. **Model merging.** TIES-merging trims low-magnitude updates, elects a consensus sign and
   merges only sign-aligned parameters, addressing the two interference modes (redundant
   values and sign disagreement) that make naive averaging drop performance
   [[src](https://arxiv.org/abs/2306.01708), NeurIPS 2023]. For this platform the natural
   use is merging **per-slice adapters** (one per hard slice from doc 00 §5.2) into one
   served adapter — cheap to try, and it fails loudly on the eval rather than silently.

**And the loop-specific mitigation that beats all three**: keep the *frozen previous
production checkpoint* as a distillation teacher on a fixed "behaviour-preservation" prompt
set drawn from the customer's own traffic, and add its per-token reverse KL to every
training run as an auxiliary loss. This is the IF-eval recovery result turned into a
standing pipeline stage, it costs only student-side compute (the teacher is a model you
already host), and it directly defends invariants I4 (contract fidelity) and I5 (safety),
which are the two things a quality-only gate is worst at catching.

---

## 2. Which recipe for which target

The mapping below is a **decision table**, not a description. Columns: the default rung,
what upgrade to try if the gate fails, what the verifier is (because the verifier decides
whether the expensive rungs are even available), and the student-size guidance.

| Target | Default rung | Upgrade if gate fails | Verifier available? | Student size guidance |
|---|---|---|---|---|
| **Chat / assistant** | 1 (SFT on teacher completions) + prompt distillation | 3 (KTO on production binary signals) → 4 | Weak: judge + rubric only | 8–30 B dense; below 8 B expect persona/format drift |
| **Extraction / structured output** | 1, loss-masked to the JSON only | 2 (RFT with schema verifier — nearly free) | **Yes: schema validation, 100 % automatable** | 2–8 B is usually enough; this is the cheapest win in the catalogue |
| **Classification / routing** | 1, often with prompt distillation to drop a long taxonomy prompt | 2, then 5 if labels are exact | **Yes: label match** | **1–4 B.** NVIDIA measured a fine-tuned `llama-3.2-1b-instruct` at "~98% accuracy relative to the 70b model" on an internal HR chatbot's tool-routing task, with up to 98.6 % inference-cost reduction [[src](https://raw.githubusercontent.com/NVIDIA-AI-Blueprints/data-flywheel/main/README.md)] |
| **Agents / tool use** | 1 on full trajectories (not turns) | 2 with a trajectory verifier → 5 (RLVR on tool validity) | **Partial**: tool existence, argument types, call ordering | 8–30 B. Per doc 00 §3.3 the failure mode is *tool-surface breadth*; cap the student's tool count and route the rest |
| **Coding** | 1 + 2 (tests are the verifier) | 5 (RLVR on test pass) | **Yes: unit tests** | 8–30 B; this is where RL genuinely earns its cost |
| **Reasoning** | 1 on teacher traces | **4 (on-policy distillation)** — the published sample-efficiency gap is largest here | Yes on maths/code | 4–32 B; see the 74.4 % vs 67.6 % AIME result at 8 B [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)] |
| **Video understanding** | 1 on teacher video-QA | 2 with MCQ answer-matching as verifier | Partial: MCQ match yes, open captions no | 2–7 B. See §2.4 |

### 2.1 Structured output and classification: the cheap, high-margin cases

Three independent results say the same thing: small models win *early* on narrow,
verifiable tasks. Distilling step-by-step (770 M > 540 B few-shot, 80 % of the data)
[[src](https://arxiv.org/abs/2305.02301)]; NVIDIA's flywheel (1 B at ~98 % of 70 B on tool
routing) [[src](https://raw.githubusercontent.com/NVIDIA-AI-Blueprints/data-flywheel/main/README.md)];
and AlpaGasus (9 K of 52 K Alpaca examples, filtered by a strong LLM, beating the full set
with 5.7× faster training — 80 min → 14 min for 7 B)
[[src](https://arxiv.org/abs/2307.08701)]. **The platform's qualification question to a new
customer should therefore be "how much of your traffic is routing, extraction or
classification?", because that fraction is where the loop pays back in weeks rather than
quarters.** NVIDIA's own scoping caveat applies and should be repeated to the customer
verbatim: these wins centre on "simpler tool calling use cases where an agent is using a
tool call to route between a small set of tools".

### 2.2 Agents and tool use

Train on **trajectories, not turns** — doc 00 §5.4's arithmetic (0.98 per-turn → 0.82 over
10 turns if independent, worse if compounding) is a training instruction as much as an eval
one. xLAM's contribution was precisely a **unified data format** across heterogeneous
agent datasets, yielding models from 1 B to 176 B that topped the Berkeley
Function-Calling Leaderboard, surpassing GPT-4 and Claude-3 on tool use
[[src](https://arxiv.org/abs/2409.03215)]. The transferable lesson is not the models, it is
that **format unification was the work**: the customer's tool-call encoding, parallel-call
packaging and error-result convention must be normalised into one schema before any
training, and that schema is part of the versioned artifact (doc 00 §2.2, invariant I4).

Frameworks have caught up here in 2026: SkyRL ships `skyrl-gym` (math, coding, search, SQL
environments in the Gymnasium API) and `skyrl-agent` for "long-horizon, real-world agents"
[[src](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/main/README.md)]; Oumi added
"tools, environments (simulated, lookup, database), and agentic data synthesis" in 2026-07
and extended GRPO to tool use in 2026-08
[[src](https://raw.githubusercontent.com/oumi-ai/oumi/main/README.md)]; Tinker ships
`agent-rl`, `search-tool` and `harbor-rl` recipes
[[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/agent-rl/)]. The platform
should not write an environment framework.

### 2.3 Student sizes: dense 2–30 B vs MoE students

| Consideration | Dense 2–30 B (Qwen3.8-27B, Marlin-2B) | MoE student |
|---|---|---|
| Training memory | Predictable; §6.2 table applies | Expert-parallel complexity; NeMo RL needed dedicated LoRA/EP recipes [[src](https://raw.githubusercontent.com/NVIDIA-NeMo/RL/main/README.md)] |
| RL stability | Standard GRPO fine | **Documented instability**; GSPO exists specifically to fix it [[src](https://arxiv.org/abs/2507.18071)] |
| LoRA on experts | N/A | Needs kernels: Axolotl added ScatterMoE LoRA (2026-02) and NVFP4 MoE LoRA via ScatterMoE/SonicMoE (2026-07) [[src](https://raw.githubusercontent.com/axolotl-ai-cloud/axolotl/main/README.md)] |
| Serving | Simple; multi-adapter packing straightforward | Active-param economics are attractive but adapter packing is harder |

**Default: dense.** An MoE student is a serving optimisation that buys active-parameter
efficiency at the price of training-stack risk on *every* rung above SFT. Take it only
after the dense student has passed a gate and the cost model says the MoE saves enough to
justify re-qualifying the whole training path.

### 2.4 Video understanding (Marlin-2B, and Qwen3.8-27B's vision path)

The repo's Marlin-2B caps every request at ≤ 240 frames at 2 fps, 200,704 px/frame
([`METHODOLOGY.md`](../METHODOLOGY.md) §8, [`models/marlin2b/architecture.md`](../models/marlin2b/architecture.md)),
which bounds prefill at ~23,560 tokens per clip regardless of clip length
([`models/marlin2b/README.md`](../models/marlin2b/README.md) §9 via doc 00 §2.1). That cap
is a *training* parameter as much as a serving one: **the student must be trained at the
same frame budget it will serve at**, or S5 and S9 differ in a way the gate cannot see.

The closest published analogue for the recipe is **LLaVA-Video**: 178,510 videos and
**1.3 M instruction samples** (178 K captions, 960 K open-ended QA, 196 K multiple-choice
QA), all annotated by **GPT-4o**, at **1 fps** — a sampling rate the authors contrast with
LLaVA-Hound (0.008 fps) and ShareGPT4Video (0.15 fps). Results: LLaVA-Video-7B scores
**63.3 / 69.7** on Video-MME (without / with subtitles), the 72 B **70.5 / 76.9**
[[src](https://arxiv.org/html/2410.02713v3)].

Read that carefully for this platform:

- It is **teacher-annotated video SFT working at 7 B** — the single best existing evidence
  for the video half of the thesis, and it is a *published benchmark* result, not a
  customer-task parity result. Doc 00 Open Question 7 (no published result showing a small
  VLM at parity with a frontier model on a *customer* video task) stands.
- Marlin-2B is **2.21 B unique parameters** — roughly 3× smaller than the 7 B that scored
  63.3. ⚠️ **Do not extrapolate the 7 B number to 2 B.** The honest planning position is
  that Marlin-2B is a candidate for *narrow* video tasks (one question type, one domain,
  MCQ-verifiable) and an unproven candidate for open-ended video QA.
- The dataset ratio is instructive for budgeting: **~7.3 instruction samples per video**,
  which is 2.4× the ~3 items/video that Video-MME's human construction implies (2,700 QA
  over 900 videos [[src](https://arxiv.org/abs/2405.21075)], per doc 00 §5.5). Synthetic
  teacher annotation buys volume, not necessarily validity.

**Frameworks for VLM post-training as of 2026-09-19** (all vendor claims from READMEs):
OpenRLHF added VLM RLHF and multi-turn VLM RL in 2026-04
[[src](https://raw.githubusercontent.com/OpenRLHF/OpenRLHF/main/README.md)]; NeMo RL v0.7.0
(2026-07-25) added Qwen3-Omni and model support for VLM GRPO
[[src](https://raw.githubusercontent.com/NVIDIA-NeMo/RL/main/README.md)]; Axolotl shipped a
"multimodal assistant-only loss-masking fix" in 2026-06 — a detail worth noting because
mis-masked multimodal loss is exactly the silent bug that produces a model that trains
smoothly and serves badly
[[src](https://raw.githubusercontent.com/axolotl-ai-cloud/axolotl/main/README.md)]; Tinker
lists a `vlm-classifier` recipe [[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/vlm-classifier/)]
and offers vision-capable models including **Qwen3.8-27B** ("Dense, Hybrid + Vision")
[[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)] — but **not** Marlin-2B, so
the video student must be trained on the platform's own hardware (§3.6).

### 2.5 When a LoRA suffices — the short answer

Apply §1.1's decision rule. In practice, for the workloads in §2's table:

| Task shape | Typical completion tokens in the training set | LoRA verdict |
|---|---:|---|
| Routing / classification | 10 K – 1 M | **LoRA, rank 8–32, trivially** |
| Extraction / structured output | 1 M – 20 M | **LoRA, rank 32** |
| Support agent with tools | 20 M – 200 M | LoRA rank 64–256; check the inequality |
| Reasoning with long traces | 100 M – 1 B+ | Rank 128+ or full FT; Tinker's own distillation recipe used **rank 128** [[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/)] |
| Knowledge injection / midtraining | > 1 B | Full FT; and expect the forgetting problem of §1.10 |

---

## 3. Frameworks and their maturity as of 2026-09-19

### 3.1 The open-source training stacks

| Stack | What it is | Methods | Maturity signal (dated) | Fit for this platform |
|---|---|---|---|---|
| **TRL** (HuggingFace) | "A comprehensive library to post-train foundation models" — `SFTTrainer`, `GRPOTrainer`, `DPOTrainer`, `KTOTrainer` and more; Accelerate/DeepSpeed scaling, PEFT and Unsloth integration, CLI | SFT, GRPO, DPO, KTO, … | Ships a long-context guide that "ends on an example that trains Qwen3-8B on million-token sequences on one 8-GPU node" [[src](https://raw.githubusercontent.com/huggingface/trl/main/README.md)] | **The default for everything up to rung 3.** Broadest method coverage per line of glue code |
| **Axolotl** | Config-driven fine-tuning framework | SFT, DPO/GDPO, GRPO (async, "up to 58% faster steps", 2026-04), QAT | 2026-07 **NVFP4 MoE LoRA** via ScatterMoE (W4A16) / SonicMoE (W4A4) "including adapter merge back into a plain NVFP4 checkpoint"; 2026-06 Expert Parallelism via DeepEP, Tinker-compatible remote training, Context Parallelism for hybrid SSM models (Nemotron-H, Falcon-H1, Bamba) [[src](https://raw.githubusercontent.com/axolotl-ai-cloud/axolotl/main/README.md)] | **The best match for this repo's models.** Hybrid-SSM context parallelism matters for Qwen3.8-27B (48 linear-attention layers) and NVFP4 adapter-merge matters for the S8 gate-twice rule |
| **LLaMA-Factory** | Zero-code CLI + Web UI over 100+ models | Pretraining, multimodal SFT, reward modelling, PPO, DPO, KTO, ORPO | Megatron-core backend added 2025-10-26; publishes the hardware table reproduced in §6.2 [[src](https://raw.githubusercontent.com/hiyouga/LLaMA-Factory/main/README.md)] | Good for breadth and for customer-facing "try it" flows; less good as a programmatic backend |
| **torchtune** | PyTorch-native recipes | SFT, KD, DPO, PPO, GRPO, QAT | **⚠️ DEPRECATED**: *"Torchtune is no longer actively maintained: torchtune development wound down in 2025"* [[src](https://raw.githubusercontent.com/pytorch/torchtune/main/README.md)] | **Do not adopt.** Fourth sunset in doc 00's running tally (§8.6) |
| **Unsloth** | Optimised kernels + desktop app | SFT, GRPO/GSPO notebooks | Claims "2× faster with 70% less VRAM with no accuracy loss"; "Train **MoE LLMs 12x faster** with 35% less VRAM"; "3x faster training & 30% less VRAM" from RoPE/MLP Triton kernels + "Padding Free + Packing" [[src](https://raw.githubusercontent.com/unslothai/unsloth/main/README.md)] — all **vendor** claims, none independently verified here | Single-GPU/small-scale accelerator; integrated by TRL. Useful for the dev-loop, not the fleet |
| **OpenRLHF** | Ray + vLLM + DeepSpeed RLHF | PPO, REINFORCE++, GRPO, RLOO; async RLHF (`--train.async_enable`, 2025-05); agent RLHF; **VLM RLHF and multi-turn VLM RL (2026-04)**; FlashREINFORCE (2026-09) | Actively released through 2026-09 [[src](https://raw.githubusercontent.com/OpenRLHF/OpenRLHF/main/README.md)] | Strong VLM-RL option — the only stack in this table whose README explicitly dates multi-turn *vision* RL |
| **verl** (ByteDance/community) | HybridFlow RL library [[paper](https://arxiv.org/abs/2409.19256)] | GRPO, PPO, DAPO, LoRA on Megatron backend | 2026-08 **VeRL-Tinker**: "keep the Tinker Cookbook loop you know, and run SFT, RL, and distillation on verl-managed GPU workers you control"; 2026-07 RL-Insight observability; Megatron-Bridge trillion-param GRPO-LoRA on 64 H800 (Mind Lab, 2025-12) [[src](https://raw.githubusercontent.com/volcengine/verl/main/README.md)] | **The scale option.** Its 3D-HybridEngine resharding between train and generate phases is the thing that makes RL affordable |
| **SkyRL** | Modular full-stack RL: `skyrl-train`, `skyrl-tx`, `skyrl-agent`, `skyrl-gym` | RL, agent RL, on-policy distillation (blog recipe) | **`skyrl-tx` is "a cross-platform library implementing a backend for the Tinker API"** — i.e. run Tinker-API code on your own GPUs (2025-10, blog "SkyRL Brings Tinker to Your GPUs"); 2026-09-01 397 B knowledge-work agent recipe with Mercor [[src](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/main/README.md)] | **Strategically the most important row in this table — see §3.4** |
| **Oumi** | End-to-end foundation-model platform | SFT, DPO, GRPO (+tool use 2026-08), data synthesis, judging, **hyperparameter tuning automation** (v0.5.0, 2025-11), `oumi deploy` to Fireworks/Parasail (2026-03) | Partial-failure support across inference/judging/synthesis (2026-06) [[src](https://raw.githubusercontent.com/oumi-ai/oumi/main/README.md)] | Closest OSS analogue to the platform's own orchestration layer; worth reading before writing ours |
| **NeMo RL** (NVIDIA) | Post-training library | GRPO, PPO, DPO, SFT, DAPO, GDPO, MOPD, CISPO, cross-tokenizer, router replay; LoRA for SFT/GRPO/DPO on DTensor and Megatron (2026-02) | v0.7.0 2026-07-25; trained Nemotron-3-Nano/Super/Ultra; publishes the throughput table in §6.4 [[src](https://raw.githubusercontent.com/NVIDIA-NeMo/RL/main/README.md)] | The Blackwell-native option, and the only one publishing GB200-class RL throughput |

**Note the ecosystem-level fact hidden in that table**: four separate projects (Axolotl,
verl, SkyRL, and Tinker itself) now speak the **Tinker API**. A training API has become a
de-facto interface in under a year.

### 3.2 Tinker (Thinking Machines) — what it is, what it costs, what it does not do

**What it abstracts.** Four primitives — `forward_backward`, `optim_step`, `sample`,
`save_state` — behind `TrainingClient` / `SamplingClient` / `ServiceClient`, plus a
Cookbook layer with `SupervisedDataset`, `RLDataset`, `PreferenceModel`, `Env`/`MessageEnv`
[[src](https://tinker-docs.thinkingmachines.ai/)]. Under the hood it is explicitly
**multi-tenant LoRA time-sharing**: "a pool of machines that work together" where "multiple
different LoRA models" share the pool and "In each clock cycle, we do forward-backward and
an optimizer step operation, each of which may involve multiple LoRA models"; it is
"optimized for throughput rather than latency, and request latency depends on the overall
load on the system", and "we'll only charge you for the compute you use"
[[src](https://tinker-docs.thinkingmachines.ai/tinker/under-the-hood/)].

**That paragraph is the S-LoRA architecture from doc 00 §4.5, applied to training instead
of serving, by a research lab that decided it was the product.** It is the single best
piece of evidence that the platform's planned multi-adapter design is right.

**Pricing (list, per 1M tokens, prefill / sample / train)** — the rows that matter here
[[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]:

| Model | Context | Prefill | Sample | **Train** |
|---|---|---:|---:|---:|
| **Qwen3.8-27B** (dense, hybrid + vision) | 64 K | $1.86 | $5.595 | **$4.103** |
| Qwen3.8-27B | 256 K | $2.48 | $7.46 | $7.46 |
| Qwen3.5-4B | 64 K | $0.33 | $1.005 | $0.737 |
| Qwen3.5-9B | 64 K | $0.66 | $1.995 | $1.463 |
| Qwen3.5-397B-A17B | 64 K | $3.00 | $7.50 | $6.60 |
| Nemotron-3.5-Lightning-30B-A3B | 64 K | $0.39 | $0.99 | $0.88 — **these are the list prices** (row carries a "Limited-time 50 % discount" label; promo is half: $0.195 / $0.495 / $0.44) [re-verified 2026-09-19; matches [`07` §15.2](07-competitor-analysis.md), [`00` §7](00-goal-and-problem-statement.md)] |
| Nemotron-3-Ultra-550B-A55B | 64 K | $4.98 | $12.45 | $10.956 — **list price; row carries a "Limited-time 50 % discount" label** (re-confirmed 2026-09-19), promo is $5.478 [matches [`07` §15.2](07-competitor-analysis.md)] |
| GPT-OSS-120B | 32 K | $0.33 | $0.84 | $0.737 |
| DeepSeek-V3.1 | 32 K | $1.695 | $4.215 | $3.718 |
| GLM-5.3 | 256 K | $4.86 | $12.15 | $14.58 |
| Kimi-K2.6 | **32 K** | **$2.205** | **$5.49** | **$4.84** (corrected 2026-09-19; a separate 128 K variant is listed, ⚠️ its prices — previously printed here as $5.15 / $12.81 / $15.40 — were not confirmed on re-fetch) |
| **Inkling** (TM's own; MoE, hybrid + audio + vision) | 64 K | $3.74 | $9.36 | $11.22 — **these are the list prices**; 50 %-promo is $1.87 / $4.68 / $5.61 [re-verified 2026-09-19; matches [`07` §15.2](07-competitor-analysis.md), [`00` §7](00-goal-and-problem-statement.md)] |
| **Inkling-Small** | 64 K | $1.16 | $2.88 | $3.46 — **these are the list prices**; 50 %-promo is $0.58 / $1.44 / $1.73 [re-verified 2026-09-19; matches [`07` §15.2](07-competitor-analysis.md)] |

"All prices are per million tokens" with an "80% discount on cached prefill tokens".

**Limits.** Default LoRA rank 32; LoRA-only (no full fine-tuning is offered);
**rate limits, max rank, max context and storage quotas are not stated** in the docs I
fetched [[src](https://tinker-docs.thinkingmachines.ai/)] — ⚠️ obtain these in writing
before any customer commitment. It has a model-deprecation page
[[src](https://tinker-docs.thinkingmachines.ai/tinker/model-deprecations/)], which is
honest and also a warning: your student base can be retired under you.

**Answer to doc 00 Open Question 13 (Inkling as a student).** Inkling is "Thinking Machines
Lab's model tailored for Tinker … a general-purpose model that can code, reason, and call
tools", multimodal over text/images/audio with an adjustable "effort" parameter, and the
docs recommend "sweeping learning rate and thinking effort"
[[src](https://tinker-docs.thinkingmachines.ai/cookbook/inkling/)]. **No parameter count,
no context length and no benchmark numbers are published on the pages I fetched.** The
question stays open, and the platform should not select Inkling as a customer's student
base without independent evaluation — a closed-weights student trainable only on one
vendor's service is the opposite of doc 00's "full ownership of your trained weights"
requirement.

### 3.3 Managed fine-tuning APIs — the rest of the field

| Vendor | Status 2026-09-19 | Key facts |
|---|---|---|
| **Baseten** | Training Jobs **GA**, Loops **early access** | Loops: "256K+ sequence length and 2T+ parameter model training. Qwen, Kimi, GLM, Deepseek and Nemotron models supported"; SFT + async RL via `forward_backward`, `optim_step`, `sample`; "Models trained with Loops promote directly to Baseten Dedicated Inference with one command" [[src](https://www.baseten.co/products/training/)]. Training is billed at the **same per-GPU-minute rates as dedicated inference** — H100 80 GB **$0.10833/min** ($6.50/hr), B200 180 GB **$0.16633/min** ($9.98/hr), H100 MIG 40 GB $0.0625/min — and "you do not pay for idle time" [[src](https://www.baseten.co/pricing/)]. **H200 and B300 are not listed.** |
| **Fireworks** | Managed SFT, DPO and RFT | Output is a **LoRA**: "LoRA rank must be a power of 2 up to 32. Our default value is 8"; dataset limits "Minimum examples: 3", "Maximum examples: 3 million per dataset"; trained models serve only on "on-demand (dedicated) deployment, which is the only supported method for serving trained models" [[src](https://docs.fireworks.ai/fine-tuning/fine-tuning-models)]. ⚠️ Per-token fine-tuning pricing was not on that page |
| **Together** | SFT and DPO per token | **corrected 2026-09-19** — the page lists one price per band, not ranges: **0.8–9 B $0.34 SFT / $0.84 DPO; 27–35 B $1.05 SFT / $2.62 DPO; 70 B+ $2.03–$7.00 SFT / $5.08–$17.50 DPO**; per-job minimum charge **$4.00–$22.00** (not $4–$100). "Price is based on the sum of tokens processed in the fine-tuning training dataset". Clusters: H100 $3.99, H200 $5.99, B200 $8.19 per GPU-hour on demand [[src](https://www.together.ai/pricing)] |
| **OpenPipe** | ⚠️ **UNVERIFIABLE on re-check 2026-09-19.** `openpipe.ai` returned a JS shell containing only the word "OpenPipe" — no migration notice, no text. The quoted W&B/CoreWeave sentence **could not be re-confirmed**, and this session's WebSearch budget is exhausted, so it is carried as an unverified prior claim, not a sourced fact. Doc 00 Open Question 5 should be treated as **still open** until someone with search settles it [[src](https://openpipe.ai/)] | **This answers doc 00 Open Question 5.** The company whose pitch was closest to this platform's — capture production traffic, fine-tune a smaller model, deploy, compare — was absorbed by an observability vendor and a neocloud. Read alongside doc 00 §6 doc 02's note that W&B Weave already ships agent-native tracing plus an MCP server that lets coding agents "read live production data, run evaluations, and execute automatic iteration loops" |
| **Predibase** | **⚠️ Still unresolved.** `predibase.com` 301-redirects to `rubrik.com/products/rubrik-agent-cloud`, which returns HTTP 403 to `curl` (re-checked 2026-09-19). Doc 00 Open Question 4 stands |
| **OpenAI** | Winding down (doc 00 §7.1) | Fine-tuning "no longer accessible to new users"; Evals read-only 2026-10-31, shutdown 2026-11-30 [[src](https://developers.openai.com/api/docs/guides/evals)] |
| **NVIDIA NeMo microservices** | Sunset 2026-10-01 (doc 00 §7.1) | Customizer supports LoRA/SFT/DPO — **not** distillation |

### 3.4 The build/buy conclusion that the framework survey actually supports

Doc 00 §6 says "buy first: Tinker or Baseten or Fireworks; build only the orchestration".
The 2026 evidence sharpens that into something better:

> **Adopt the Tinker API as the platform's internal training interface, and keep two
> interchangeable backends behind it: Tinker's hosted service, and `skyrl-tx` (or
> VeRL-Tinker) on our own B300 fleet.**

Why this beats both pure-buy and pure-build:

- **It is real, not aspirational.** `skyrl-tx` is "a cross-platform library implementing a
  backend for the Tinker API, with a unified engine for training and inference"
  [[src](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/main/README.md)]; verl shipped
  VeRL-Tinker in 2026-08 to "run SFT, RL, and distillation on verl-managed GPU workers you
  control" [[src](https://raw.githubusercontent.com/volcengine/verl/main/README.md)];
  Axolotl added "remote training through Tinker-compatible APIs" in 2026-06
  [[src](https://raw.githubusercontent.com/axolotl-ai-cloud/axolotl/main/README.md)].
- **It prices the buy/build boundary continuously instead of discretely.** §6.5 computes
  the multiple: Tinker's Qwen3.8-27B train price is roughly **7.1–14.3× the estimated raw
  self-hosted LoRA compute cost** on B300 (corrected 2026-09-19 to match §6.5's own
  arithmetic — $4.103 ÷ $0.286 = 14.3× at `low`, ÷ $0.579 = 7.1× at `high`; the previously
  printed "9–14×" matched neither end). That is a perfectly reasonable premium at low
  volume and an unacceptable one at high volume, and with one API you move a customer
  across the line without a rewrite.
- **It survives a vendor sunset.** Doc 00 counted three sunsets in twelve months; this
  document adds torchtune. An interface with two implementations is the only structure
  that has survived that rate of churn.
- **⚠️ Caveats to verify before committing**: how complete `skyrl-tx`'s API coverage is
  versus the hosted service; whether Tinker's API is stable or versioned in a way we can
  pin; and whether Tinker's terms permit us to resell access as part of a platform.

### 3.5 Multi-node training on 8×B300 nodes

The three mechanisms, and when each is needed for the students in scope:

| Mechanism | What it does | When Qwen3.8-27B needs it | Source |
|---|---|---|---|
| **FSDP / ZeRO-3** | Shards params, grads and optimizer state across ranks | **Full FT**: yes (≈500 GB of state, §6.2) — but that fits *within one 8×B300 node* (2,144 GB) | [[src](https://arxiv.org/abs/2304.11277)] |
| **Tensor + pipeline + data parallelism (Megatron)** | Composes 3 axes; "1 trillion parameters at 502 petaFLOP/s on 3072 GPUs with achieved per-GPU throughput of 52% of theoretical peak"; interleaved pipeline schedule "+10 %" | Only for teachers (Kimi-K3, DeepSeek-V4.1-Flash), not for a 27 B student | [[src](https://arxiv.org/abs/2104.04473)] |
| **Selective activation recomputation + sequence parallelism** | Cuts recomputation overhead "by over 90%", 5× less activation memory; 530 B at **54.2 % MFU** vs 42.1 % with full recompute, on 2,240 A100s | Yes at long context (agent trajectories, 240-frame video) | [[src](https://arxiv.org/abs/2205.05198)] |
| **Expert parallelism** | Shards MoE experts | Only for MoE students/teachers; Axolotl via DeepEP (2026-06), NeMo RL recipes at EP16 | [[src](https://raw.githubusercontent.com/axolotl-ai-cloud/axolotl/main/README.md)] |

**The practical headline for this platform: a 27 B dense student never needs more than one
8×B300 node for any rung of the ladder.** Multi-node appears only when (a) the *teacher* is
self-hosted for on-policy distillation — Kimi-K3 already occupies a full 8×B300 node at
195 GB/GPU ([`METHODOLOGY.md`](../METHODOLOGY.md) §8) — or (b) RL rollout throughput, not
memory, is the constraint. Plan for **two nodes when doing on-policy distillation with a
self-hosted large teacher**: one serving the teacher under vLLM with `prompt_logprobs`, one
training the student.

### 3.6 What must be trained on our own hardware regardless

- **Marlin-2B**: not on Tinker's model list [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)],
  not on Baseten Loops' named families ("Qwen, Kimi, GLM, Deepseek and Nemotron")
  [[src](https://www.baseten.co/products/training/)]. Every video experiment is
  self-hosted.
- **Anything requiring full fine-tuning** (knowledge injection, §2.5's last row): Tinker is
  LoRA-only; Fireworks outputs LoRA at rank ≤ 32
  [[src](https://docs.fireworks.ai/fine-tuning/fine-tuning-models)].
- **Anything requiring a self-hosted teacher's logprobs** (§1.3).

---

## 4. Data: mixture, quantity, fidelity

### 4.1 Quantity vs quality — the settled part and the unsettled part

**Settled**: for *eliciting* a behaviour the base model already has, small curated sets
work and large noisy ones hurt.

| Result | Data | Outcome |
|---|---|---|
| **LIMA** | **1,000** curated prompt-response pairs, 65 B LLaMA, plain SFT, no RL | Responses "equivalent or strictly preferred to GPT-4 in 43% of cases", 58 % vs Bard, 65 % vs DaVinci003; conclusion: "almost all knowledge … is learned during pretraining, and only limited instruction tuning data is necessary" [[src](https://arxiv.org/abs/2305.11206)] |
| **AlpaGasus** | **9 K of 52 K** Alpaca examples, filtered by ChatGPT scoring | Beats full-Alpaca on GPT-4 evaluation; 13 B reaches ">90 % performance of its teacher"; **5.7× faster training** (7 B: 80 min → 14 min) [[src](https://arxiv.org/abs/2307.08701)] |
| **s1** | **1,000** questions with reasoning traces (s1K), Qwen2.5-32B-Instruct | "exceeds o1-preview on competition math questions by up to 27% (MATH and AIME24)"; budget forcing lifts AIME24 50 %→57 % [[src](https://arxiv.org/abs/2501.19393)] |
| **LIMO** | ~1 % of the data prior approaches used | AIME24 **63.3 %** (vs 6.5 %), MATH500 **95.6 %** (vs 59.2 %), "outperforming models trained on 100x more data" [[src](https://arxiv.org/abs/2502.03387)] |

**Unsettled**: none of these is a *distillation-to-replace-an-incumbent* result on a
customer's private task distribution with tool calls and a 20 K-token prompt stack. The
platform should use them as evidence that **the first iteration should be small** —
1 K–10 K curated examples, not 1 M — because the marginal information in the next 100 K
teacher completions is probably zero, and because a small first run makes the loop's cycle
time short enough to actually iterate.

**The corresponding rule**: budget annotation in *rounds*. Round 1: 2 K examples, stratified
across the customer's slices. Gate. Round 2: 10 K, weighted toward the slices that failed
and the disagreements (doc 00 §7.2's "mine the disagreements, not the average"). This is
strictly better than one large annotation run, and it is also what makes the annotation
cost controllable when §8 shows it dominating the GPU cost by an order of magnitude.

### 4.2 Mixture design

Two automated approaches are cheap enough to be worth copying, and both come from
pretraining but apply to post-training mixtures:

- **DoReMi**: a 280 M proxy model trained with Group DRO produces domain weights for an 8 B
  target — **+6.5 points** average few-shot accuracy and baseline accuracy reached with
  **2.6× fewer steps** [[src](https://arxiv.org/abs/2305.10429)].
- **RegMix**: train many tiny proxies (512 models, 1 M params, 1 B tokens), fit a
  regression from mixture → loss, pick the predicted optimum. "consistently outperforms
  human selection" at **10 % of DoReMi's compute**, and finds that web data correlates with
  downstream performance better than conventionally "high-quality" sources like Wikipedia
  [[src](https://arxiv.org/abs/2407.01492)].

**For this platform the mixture axes are not "web vs Wikipedia" but**: (a) teacher-labelled
production traffic by slice; (b) human-adjudicated gold examples; (c) behaviour-preservation
replay from the previous checkpoint (§1.10); (d) safety data (doc 00 invariant I5, and §4.7
below); (e) synthetic edge cases. RegMix's method — regress mixture weights against a
validation objective using cheap proxy runs — transfers directly and is the most defensible
automated knob to give the auto-research loop (§7).

### 4.3 Sequence packing

Padding wastes a large fraction of training FLOPs: "up to 50% of all tokens can be padding"
in common datasets, "up to 89%" in extreme cases like GLUE-cola; packing with
cross-contamination-free attention gives "a 2x speedup for phase 2 pre-training in BERT"
while keeping "mathematical equivalence" [[src](https://arxiv.org/abs/2107.02027)].

**The failure mode is the reason to be careful**: naive packing lets tokens attend across
document boundaries. Modern stacks handle this (Unsloth advertises "Padding Free + Packing"
kernels [[src](https://raw.githubusercontent.com/unslothai/unsloth/main/README.md)]), but
**the platform must assert it in a test**, not assume it: pack two known documents, check
that the loss on document 2 is unchanged when document 1 is replaced. That is a five-line
regression test and it catches a class of silent quality loss that no eval will attribute
correctly.

For this platform's data, packing matters most for the **short** tasks (routing,
extraction, classification) where examples are 100–500 tokens against a 4–32 K sequence
length — exactly the tasks §2.1 identifies as the commercial sweet spot.

### 4.4 Chat-template fidelity, and why it is a production-severity issue

HuggingFace's own docs state the problem plainly: two models fine-tuned from the same
Mistral-7B base use entirely different control tokens (`[INST]…[/INST]` vs
`<|user|>`/`<|assistant|>`), and "with the wrong control tokens, these models would have
drastically worse performance". Two concrete traps they document:

- **Double special tokens**: "Chat templates should already include all the necessary
  special tokens, and adding additional special tokens is often incorrect or duplicated,
  hurting model performance. When you format text with `apply_chat_template(tokenize=False)`,
  make sure you set `add_special_tokens=False` if you tokenize later."
- **`add_generation_prompt`**: must be `True` at inference and **`False` during training** —
  "Set `add_generation_prompt=False` because the additional tokens to prompt an assistant
  response aren't helpful during training." Get this backwards and the model learns to
  emit the assistant header itself.
- **Reasoning-field prefill**: "Reasoning models often expose a separate field, like
  `reasoning_content` on Qwen or `thinking` on Gemma. Prefilling `content` closes the
  reasoning block before generation starts, so the model can't continue inside it."
  [[src](https://huggingface.co/docs/transformers/main/en/chat_templating)]

**Platform requirement (invariant I4, stated as a training requirement).** The *exact*
rendered token sequence used in training must be byte-identical to the one the serving
stack will produce, and this must be **asserted in CI**, not reviewed by eye:

```
assert train_renderer(example) == serving_renderer(example)   # byte equality, per example
assert tokenizer(train_text, add_special_tokens=False) == train_token_ids
```

Store the template, the tokenizer and the renderer version **inside the versioned artifact**
(doc 00 §6 doc 01), alongside the prompt stack. A template change is a model change.

### 4.5 Synthetic-data risk: model collapse, and the mitigation that actually works

The risk is real but the framing in the popular account is wrong, and the correction is
operationally decisive:

- **The Curse of Recursion** showed degeneration when each generation's synthetic data
  *replaces* the previous data [[src](https://arxiv.org/abs/2305.17493)].
- **Gerstgrasser et al.** show the condition matters: "accumulating the successive
  generations of synthetic data alongside the original real data avoids model collapse",
  across model sizes, architectures and hyperparameters, and extending to diffusion models
  and VAEs; with accumulated data, "the test error has a finite upper bound independent of
  the number of iterations" [[src](https://arxiv.org/abs/2404.01413)].

**Therefore the closed loop's data policy is: append, never replace.** Every cycle's
dataset is the union of all prior cycles' real traffic plus that cycle's new labels, with
deduplication but without eviction. This is cheap (text storage is nothing next to GPU
time), it has a theoretical guarantee behind it, and it is the opposite of what a naive
"retrain on last month's traffic" pipeline does.

Two further mitigations the platform should adopt:

1. **Never close the loop without real traffic.** Self-Instruct works
   [[src](https://arxiv.org/abs/2212.10560)] but it is a bootstrap, not a flywheel; the
   platform's synthetic data should always be *conditioned on real production prompts*.
2. **Never train on the student's own unverified outputs.** Doc 00's "what to avoid" list
   already says this; §1.9's self-distillation exception requires the *teacher* to be a
   different (earlier, or larger) checkpoint, and rung 2's exception requires a verifier.

### 4.6 Tool-call formatting and multimodal packing

- **Tool calls**: normalise to one schema before training (§2.2, xLAM's lesson
  [[src](https://arxiv.org/abs/2409.03215)]), and hold the encoding fixed between training
  and serving. Doc 00 §2.3's warning — arguments that parse but with different escaping →
  downstream corruption, `json.loads` rather than string-match — is a *training data
  validation rule* too: parse every teacher tool call before it enters the dataset, and drop
  (do not repair) the ones that fail.
- **Multimodal packing**: the loss must be masked to assistant tokens only, across modality
  boundaries. Axolotl shipped a "multimodal assistant-only loss-masking fix" in 2026-06
  [[src](https://raw.githubusercontent.com/axolotl-ai-cloud/axolotl/main/README.md)] — i.e.
  a mainstream framework had this wrong within the last four months. **Assert the mask in a
  test**: the number of loss-bearing tokens must equal the number of assistant text tokens,
  exactly, for a hand-checked example.
- **Video packing**: at ≤ 240 frames the per-example token count is nearly constant
  (~23,560), so packing buys little and sequence-length-driven memory dominates. Plan video
  training around activation checkpointing and context parallelism
  [[src](https://arxiv.org/abs/2205.05198)], not packing.

### 4.7 Safety data in the mixture

Doc 00 invariant I5 makes safety a separate, non-negotiable gate. The training-side
corollary: refusal behaviour is a tiny fraction of tokens in production traffic, so it is
under-represented in any distillation set by construction (doc 00 §3.3). **The mixture must
oversample it deliberately** — a fixed safety slice carried forward in every cycle's dataset
(the "append, never replace" rule applies here first) — and the behaviour-preservation
teacher of §1.10 should include the previous checkpoint's refusal behaviour on a fixed
adversarial prompt set. ⚠️ No published guidance was found on what *fraction* of a
distillation mixture safety data should be; this needs an experiment, not a guess.

---

## 5. Reward models and judges for RL

### 5.1 Training an RM from teacher preferences

**Mechanism.** Collect (prompt, response A, response B, preference) triples where the
preference comes from a teacher/judge; train a scalar head on the Bradley-Terry loss;
use it as the reward in PPO/GRPO under a KL penalty to the reference policy.

**When the platform should do this at all**: only when (a) no programmatic verifier exists,
(b) the volume of preference judgements is high enough that calling the judge inside the RL
loop is too expensive, and (c) the task is open-ended enough that rung 3 (DPO/KTO) is
demonstrably insufficient. Condition (c) is rarely met in the first year. **Default: skip
the RM.** Use DPO/KTO on offline preferences, or d-RLAIF with the judge called directly
[[src](https://arxiv.org/abs/2309.00267)] if you must have on-policy optimisation.

### 5.2 Rubric-based rewards — the most promising 2025-26 development for this platform

**Rubrics as Rewards (RaR)**: instance-specific, multi-criterion rubrics converted into a
reward signal for on-policy RL, rather than a single Likert score from a judge. Gains of
**up to 31 % relative on HealthBench** and **7 % relative on GPQA-Diamond** over
Likert-based LLM-judge baselines, with two properties that matter operationally:
**rubric-based signals help smaller judge models** and **reduce variance**
[[src](https://arxiv.org/abs/2507.17746)]. Tinker ships a `rubric` recipe
[[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/rubric/)].

Why this is the right shape for this product specifically: a rubric is **auditable by the
customer**. Doc 00 §5.6 says the customer's minimum evidence includes a held-out set they
can inspect with hard slices visible. A per-criterion rubric ("did it cite a source? did it
call the refund tool with the right order ID? did it avoid promising a delivery date?") is
simultaneously (i) the training reward, (ii) the eval rubric, and (iii) a document the
customer's SME can argue with. A scalar RM is none of those.

**The trap this creates, and the rule that defuses it**: if the rubric is both the training
reward and the eval metric, the eval measures nothing (doc 00 §5.1's judge-is-the-teacher
problem, one level up). **Rule: split the rubric.** A *training* rubric and a *held-out
evaluation* rubric, authored separately, with the evaluation rubric never visible to any
training run or to the auto-research loop. Overlap between them is measured and reported.

### 5.3 RM overoptimisation

Gao, Schulman and Hilton measured this directly with a gold-standard RM: the gold score
"follows a different functional form depending on the method of optimization" for RL versus
best-of-n, and the coefficients "scale smoothly with the number of reward model parameters",
with dependence on RM dataset size, policy size and the KL penalty coefficient
[[src](https://arxiv.org/abs/2210.10760)].

**Operational translation.** Distance from the reference policy (measured in KL) is the
x-axis along which a proxy reward stops predicting true quality. So:

1. **Log KL-to-reference on every RL run** and plot the eval score against it. The peak is
   the stopping point, and it is *observable* — this is the cheapest guardrail in the
   document.
2. **Early-stop on the held-out gate**, not on reward.
3. **Best-of-n and RL overoptimise differently** — do not transfer a KL budget from one to
   the other.

### 5.4 Judge quality and RM evaluation

RewardBench evaluates RMs on "prompt-chosen-rejected trios spanning chat, reasoning, and
safety" and identifies the recurring failure modes: "propensity for refusals, reasoning
limitations, and instruction following shortcomings"
[[src](https://arxiv.org/abs/2403.13787)]. Those are precisely the three axes this platform
cares about (I4 contract fidelity, task quality, I5 safety), which makes a
RewardBench-style *per-axis* RM report card the right artifact, not an aggregate accuracy.

**RM-as-eval linkage — the rule.** Doc 00 §5.1 establishes: the judge must not be the
teacher; validate the judge against ≥200 human-adjudicated examples; report the judge's
noise floor next to every delta. This document adds the training-side constraints:

| Constraint | Why |
|---|---|
| The **RM/judge used for training must not be the judge used at the S7 gate** | Otherwise the gate scores the optimiser's own objective (doc 00 §5.1, one level up) |
| The training judge may be the teacher; the **gate judge may not** | The teacher is the training signal by design; making it the gate collapses gate and objective |
| Judge **model version is pinned in the artifact** and re-validated on version change | Judge drift silently invalidates historical comparisons |
| Every RL run logs **KL-to-reference, mean output length, and per-criterion rubric scores** | The three known reward-hacking channels |

### 5.5 Verifiable rewards, and the result that should scare the auto-research loop

RLVR is the safest reward because a checker is not gameable in the same way. But
**"Spurious Rewards"** shows that even verifiable-reward *experiments* can produce fake
gains: on Qwen2.5-Math, **randomly assigned rewards yielded a 21.4-point improvement on
MATH-500 against 29.1 points from correct rewards**, via a clipping bias that amplified
pre-existing "code reasoning" behaviour (65 % → >90 % of outputs) — and "spurious rewards
that are effective for Qwen models often fail to produce gains for other model families,
such as Llama3 or OLMo2". The authors' conclusion is a methodology requirement: validate RL
methods "across diverse models rather than relying on a single de facto choice"
[[src](https://arxiv.org/abs/2506.10947)].

**Two mandatory guardrails follow, and §7 depends on them:**

1. **Every RL recipe change is validated on at least two base-model families** before it
   enters the platform's default recipe library. A gain reproduced only on one family is
   labelled as such and never auto-promoted.
2. **Every RL run includes a random-reward control** when the run is establishing a *new*
   recipe (not for routine customer runs). If the random-reward arm moves the metric
   materially, the metric is measuring the prior, not the training.

---

## 6. Compute planning

### 6.1 Formulas and the anchor measurements

**FLOPs.** For a dense model of `N` parameters over `D` training tokens:

```
full fine-tune      C ≈ 6 · N · D          (2ND forward, 4ND backward)
LoRA / frozen base  C ≈ 4 · N · D    est.  (no weight-gradient pass for frozen weights;
                                            adapter grads are negligible at rank ≤ 256)
DPO (full FT)       C ≈ 16 · N · D   est.  (chosen + rejected through the policy at 6ND
                                            each, plus 2 × 2ND reference forwards)
DPO (LoRA)          C ≈ 12 · N · D   est.  (reference = base with adapters disabled;
                                            no extra memory, same extra forward compute)
GRPO / RLVR         dominated by generation, not by the gradient — use §6.4's measured
                    tokens/sec/GPU rows rather than a FLOPs estimate
```

⚠️ The 4ND, 16ND and 12ND figures are standard arithmetic from the 6ND decomposition, not
quoted from a paper; they ignore attention's sequence-quadratic term (≤ 10 % at 4–8 K
sequence lengths for these model shapes, larger at 32 K+) and ignore the vision encoder for
VLMs (§8.2).

**Achieved throughput anchor (`meas.`, vendor-published).** NVIDIA publishes per-GPU
achieved "Model TFLOP/sec/GPU" for Megatron-Bridge pre-training on Blackwell
(NeMo container **26.08.01**) [[src](https://docs.nvidia.com/nemo-framework/user-guide/latest/performance/performance-summary.html)]:

| Model | System | GPUs | Precision | Seq len | Tokens/s/GPU | **Model TFLOP/s/GPU** |
|---|---|---:|---|---:|---:|---:|
| DeepSeekV3 | DGX-GB300 | 256 | MXFP8 | 4,096 | 6,288 | **1,636** |
| DeepSeekV3 | DGX-GB200 | 256 | MXFP8 | 4,096 | 4,912 | 1,277 |
| **DeepSeekV4 Flash** | DGX-GB300 | 128 | MXFP8 | 4,096 | 9,184 | **834** |
| DeepSeekV4 Flash | DGX-GB200 | 128 | MXFP8 | 4,096 | 7,968 | 725 |
| Qwen3_30B_a3B | DGX-GB300 | 8 | MXFP8 | 4,096 | 44,544 | **1,023** |
| Qwen3_235B_a22B | DGX-GB300 | 256 | MXFP8 | 4,096 | 8,832 | 1,306 |
| Nemotron_3_5_Lightning | DGX-GB300 | 8 | MXFP8 | 8,192 | 34,816 | 973 |
| Nemotron_3_Super | DGX-GB300 | 64 | **NVFP4** | 8,192 | 10,240 | 871 |
| Nemotron_3_Ultra | DGX-GB300 | 256 | **NVFP4** | 8,192 | 3,744 | **1,348** |

Against GB300's pinned dense peaks (BF16 2,500 / FP8 5,000 / FP4 15,000 TFLOPS,
[`METHODOLOGY.md`](../METHODOLOGY.md) §8) that is **16.7–32.7 % MFU at MXFP8** (834/5,000
for DeepSeekV4 Flash up to 1,636/5,000 for DeepSeekV3) and **5.8–9.0 % of FP4 peak at
NVFP4** (871/15,000 and 1,348/15,000) — both recomputed 2026-09-19; the previously printed
"20–33 %" excluded the two sub-20 % rows in its own table and the "9–9.7 %" silently
divided one row by **B200's** 9,000 TFLOPS instead of GB300's 15,000 — i.e. **FP4 training does not deliver FP4 throughput**,
a fact worth carrying into doc 06's optimisation work. Note also that these are
*pre-training* rows; **no SFT or LoRA throughput table is published on that page** (⚠️),
so the numbers below are estimates anchored to these.

**Planning constant used below** (`est.`): **800 Model TFLOP/s/GPU achieved on HGX B300 for
dense BF16/FP8 post-training**, i.e. ~36 % of B300's 2,250 TFLOPS dense BF16 peak or ~18 %
of its 4,500 FP8 peak ([`METHODOLOGY.md`](../METHODOLOGY.md) §8). This sits just below the
measured GB300 MXFP8 range because (i) HGX B300 is the lower-clocked part, (ii)
post-training runs are shorter and less tuned than pre-training benchmark runs, and (iii)
small dense models under-utilise more than large MoEs. **Every cost below scales inversely
with this number — halve it and double the cost.**

### 6.2 Memory

LLaMA-Factory publishes an estimated requirement table that is the most citable rule of
thumb available [[src](https://raw.githubusercontent.com/hiyouga/LLaMA-Factory/main/README.md)]:

| Method | Bits | 7 B | 14 B | 30 B | 70 B | `x` B |
|---|---:|---:|---:|---:|---:|---|
| Full (`bf16`/`fp16` + fp32 states) | 32 | 120 GB | 240 GB | 600 GB | 1,200 GB | **`18x` GB** |
| Full (`pure_bf16`) | 16 | 60 GB | 120 GB | 300 GB | 600 GB | `8x` GB |
| Freeze / LoRA / GaLore / DoRA | 16 | 16 GB | 32 GB | 64 GB | 160 GB | **`2x` GB** |
| QLoRA | 4 | 6 GB | 12 GB | 24 GB | 48 GB | `x/2` GB |

Applied to this repo's students, against B300's **268 GB/GPU, 2,144 GB/node**
([`METHODOLOGY.md`](../METHODOLOGY.md) §8):

| Student | Full FT (`18x`) | Full FT pure-bf16 (`8x`) | LoRA (`2x`) | QLoRA (`x/2`) | Verdict |
|---|---:|---:|---:|---:|---|
| **Qwen3.8-27B** (27.78 B) | ~500 GB | ~222 GB | **~56 GB** | ~14 GB | Full FT fits **one 8×B300 node** with FSDP, and pure-bf16 (~222 GB) **fits inside one 268 GB B300 before activations**, not merely "nearly"; LoRA fits one GPU with room for long sequences |
| **Marlin-2B** (2.21 B) | ~40 GB | ~18 GB | **~4.4 GB** | ~1.1 GB | Everything fits one GPU; the binding constraint is **video activations**, not weights |

**Consequences.** (1) Memory is not the reason to choose LoRA for a 27 B student on B300s —
serving multi-tenancy is (§1.1). (2) Activation memory, not parameter memory, sets the
sequence-length ceiling, which is what selective recomputation and sequence parallelism are
for [[src](https://arxiv.org/abs/2205.05198)]. (3) QLoRA's relevance here is customer-side
(a team wanting to reproduce on their own hardware), not fleet-side.

### 6.3 Cost per 1B tokens trained

`est.`, at 800 Model TFLOP/s/GPU achieved, priced from
[`matrix/cost-matrix.md`](../matrix/cost-matrix.md) §3's B300 tiers —
**`low` $7.40/GPU-hr (Hyperstack), `high` $15.00 (OCI `BM.GPU.B300.8`), `res1y` $7.94
(DigitalOcean 12-mo)**:

| Student | Method | FLOPs / 1B tok | **GPU-hours / 1B tok** | `low` | `high` | `res1y` | **$ / 1M tokens (`low`)** |
|---|---|---:|---:|---:|---:|---:|---:|
| Qwen3.8-27B | Full FT (6ND) | 1.667 × 10²⁰ | **57.9** | $428 | $868 | $460 | **$0.428** |
| Qwen3.8-27B | LoRA (≈4ND) | 1.111 × 10²⁰ | **38.6** | $286 | $579 | $306 | **$0.286** |
| Qwen3.8-27B | DPO, LoRA (≈12ND) | 3.334 × 10²⁰ | **115.8** | $857 | $1,737 | $919 | $0.857 |
| Marlin-2B | Full FT (6ND) | 1.326 × 10¹⁹ | **4.60** | $34 | $69 | $37 | **$0.034** |
| Marlin-2B | LoRA (≈4ND) | 8.84 × 10¹⁸ | **3.07** | $23 | $46 | $24 | $0.023 |

Wall-clock on one 8×B300 node: divide GPU-hours by 8. **Qwen3.8-27B full FT on 1 B tokens
is ~7.2 hours on one node.** Most customer SFT runs are 0.05–0.5 B tokens, i.e.
**20 minutes to 4 hours**.

**The conclusion that should reshape the product roadmap: GPU cost is not the constraint on
this platform's training stage.** A complete SFT iteration for a 27 B student costs tens to
low hundreds of dollars of B300 time. §8 shows teacher annotation costing 5–30× that, and
doc 00 §5.3 shows the eval sample sizes costing more again. **Optimising training compute is
close to a waste of engineering effort at this scale; optimising annotation spend and eval
throughput is not.**

### 6.4 RL is a different economy

The measured comparison, from NVIDIA's RL library (NeMo RL v0.6)
[[src](https://docs.nvidia.com/nemo/rl/latest/about/performance-summary.html)]:

| Model | Algorithm | System | GPUs | Policy | **Tokens/s/GPU** | Step time |
|---|---|---|---:|---|---:|---:|
| Qwen3-30B3A | GRPO | GB200 BF16 | 16 | on-policy | **1,910** | 221 s |
| Qwen3-30B3A | GRPO | GB200 BF16 | 16 | 1-step off | 1,406 | 301 s |
| Qwen3-30B3A | GRPO | H100 BF16 | 32 | on-policy | 1,102 | 192 s |
| Qwen3-30B3A | GRPO | H100 BF16 | 192 | 8-step off | 1,025 | 34.5 s |
| Qwen3-235B | GRPO | GB200 BF16 | 64 | on-policy | 163 | 286 s |
| DeepSeek V3 | GRPO | GB200 BF16 | 128 | on-policy | 30.2 | 108 s |
| DeepSeek V3 | GRPO | H100 **BF16** | 512 | 1-step off | **12.8** | **64.1 s** |
| Nemotron-3-Nano-30B-A3B | SWE (agentic) | H100 BF16 | 128 | 1-step off | **37.5** | 430 s |

Compare the same-family pre-training row: **Qwen3_30B_a3B at 44,544 tokens/s/GPU** on
DGX-GB300 [[src](https://docs.nvidia.com/nemo-framework/user-guide/latest/performance/performance-summary.html)].
Different systems and different token definitions, but the magnitude is unambiguous:
**RL processes roughly 20–30× fewer tokens per GPU-second than SFT/pre-training for a
comparable model** — and then needs far more tokens to reach the same place. The
agentic-RL row is the one to plan against for tool-use tasks: **37.5 tokens/s/GPU with 430 s
steps**, three orders of magnitude below SFT throughput.

That is the quantitative content behind the on-policy distillation headline (1,800 vs
17,920 GPU-hours [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)]) and
behind this document's central recommendation: **exhaust rungs 1–4 before rung 5.**

At B300 `low` $7.40/GPU-hr, a 17,920 GPU-hour RL run is **$132,608**; the 1,800-hour
distillation run is **$13,320** (`est.`, applying this repo's price tier to the blog's
published GPU-hours — the blog does not state its own prices or GPU type ⚠️).

### 6.5 Buy vs build, priced

| Route | Unit | Price | Notes |
|---|---|---|---|
| **Self-host, B300 `low`** | Qwen3.8-27B LoRA | **$0.286 / 1M tokens** `est.` | §6.3; excludes ops, failed runs, idle, and the node you cannot fill |
| **Self-host, B300 `high`** | same | $0.579 / 1M `est.` | |
| **Tinker** | Qwen3.8-27B train, 64 K | **$4.103 / 1M** [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)] | LoRA only; zero ops; multi-tenant time-shared |
| **Together** | 27–35 B SFT | **$1.05 / 1M** [[src](https://www.together.ai/pricing)] | one price, not a range (corrected 2026-09-19); plus per-job minimum **$4.00–$22.00** |
| **Together** | 27–35 B DPO | **$2.62 / 1M** [[src](https://www.together.ai/pricing)] | corrected 2026-09-19 |
| **Baseten** | B200 dedicated/training | $0.16633/GPU-min = **$9.98/GPU-hr** [[src](https://www.baseten.co/pricing/)] | No idle charge; B300 not listed |

**Ratios**: Tinker's $4.103/1M is **14.3×** the self-hosted `low` LoRA estimate ($0.286) and
**7.1×** the `high` estimate ($0.579); Together's 27–35 B SFT price ($1.05) is **3.7×** `low`
and **1.8×** `high` (corrected 2026-09-19 — the old range rested on an unsourced $1.16 upper bound), the most competitive managed number in the table. Baseten's B200 hourly rate is
**1.35×** the B300 `low` rate — i.e. buying managed *capacity* is far cheaper than buying
managed *tokens*, which is the expected shape and the reason §3.4 recommends one API over
two backends rather than a single procurement decision.

**⚠️ The self-hosted numbers assume the node is busy.** [`scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md)
owns utilisation economics; a training fleet that is 20 % utilised costs 5× the table
above, at which point every managed option wins. **Rule: buy tokens until training
utilisation would exceed ~40 %, then buy capacity.** ⚠️ The 40 % threshold is an inference
from the **1.35–14.3×** price ratios in this table (⚠️ the previously printed "2.4–14×" matched no pair of figures in it), not a measured break-even; doc 06 should compute it properly.

### 6.6 Hyperparameter defaults from the literature

Starting points, not recommendations — every one should be swept once per customer task and
then frozen in the artifact.

| Knob | Default | Source / reasoning |
|---|---|---|
| LoRA rank | **32** | Tinker's default [[src](https://tinker-docs.thinkingmachines.ai/tinker/lora-primer/)]; raise until `lora_params ≥ completion_tokens` |
| LoRA target modules | **All matrices, incl. MLP/MoE** | Attention-only "significantly underperforms" [[src](https://thinkingmachines.ai/blog/lora/)] |
| LoRA LR | **10× the full-FT LR** (≈15× for ~100-step runs) | [[src](https://thinkingmachines.ai/blog/lora/)], [[src](https://tinker-docs.thinkingmachines.ai/tinker/lora-primer/)] |
| Batch size (LoRA) | **Smaller than you would use for full FT** | "LoRA is less tolerant of large batch sizes" [[src](https://thinkingmachines.ai/blog/lora/)] |
| SFT epochs | **1–3** over a curated set | LIMA/LIMO/s1 all use small sets [[src](https://arxiv.org/abs/2305.11206)] [[src](https://arxiv.org/abs/2502.03387)] [[src](https://arxiv.org/abs/2501.19393)] |
| Fireworks-style rank ceiling | rank power-of-2 ≤ 32, default 8 | [[src](https://docs.fireworks.ai/fine-tuning/fine-tuning-models)] — a useful sanity check that low ranks are commercially normal |
| Continual runs | LR re-warm + re-decay + replay | [[src](https://arxiv.org/abs/2403.08763)] |
| RL: KL-to-reference | **Log it; early-stop on the gate, not the reward** | [[src](https://arxiv.org/abs/2210.10760)] |
| MoE RL | **GSPO, not GRPO** | [[src](https://arxiv.org/abs/2507.18071)] |
| On-policy distillation | rank 128, ~200 steps, 16 K rollouts (published example) | [[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/)] |

---

## 7. The auto-research loop (S5's slice of doc 09)

Doc 00 is emphatic: **do not build this first**, because "an auto-research loop over an
unvalidated eval is a machine for overfitting". This section scopes what is realistic
*inside the training stage* once the eval is trusted.

### 7.1 What is realistically automatable, ranked by evidence

| Automation | Mechanism | Cost | Evidence it works | Verdict |
|---|---|---|---|---|
| **Hyperparameter search** (LR, rank, epochs, batch) | Random/Bayesian search over a small grid; each trial is §6.3-cheap | 5–20 × one SFT run; one 0.5 B-token 27 B LoRA run = 19.3 GPU-hr = $143 at B300 `low` (§6.3), so a sweep is **$0.7 K–$2.9 K** `est.` | Oumi ships "hyperparameter tuning automation" (v0.5.0, 2025-11) [[src](https://raw.githubusercontent.com/oumi-ai/oumi/main/README.md)] | **Build first. Highest value per line of code** |
| **Data-mixture optimisation** | Fit a regression from mixture weights → validation objective on cheap proxy runs | RegMix used 512 × 1 M-param proxies and ran at **10 % of DoReMi's compute** [[src](https://arxiv.org/abs/2407.01492)] | RegMix, DoReMi [[src](https://arxiv.org/abs/2305.10429)] — both from pre-training | **Build second.** ⚠️ transfer to post-training mixtures is unverified |
| **Recipe/ladder selection** (which rung) | Decision rules from §1–§2, executed automatically with a budget cap | Negligible | The rules themselves are sourced; the automation is not | **Build as a rules engine, not a learner** |
| **Prompt / instruction evolution** | Reflective evolution of prompts and recipes | GEPA beat GRPO "by an average of 6%… up to 20%" while using "up to 35x fewer rollouts", and beat MIPROv2 "by over 10%" [[src](https://arxiv.org/abs/2507.19457)] | Strong, and cheap | **Build third — and note it may remove the need for training at all on some tasks** |
| **Evolutionary code/kernel search** | LLM proposes code edits, evaluator scores, population evolves | AlphaEvolve found a 48-multiplication 4×4 complex matmul — "the first improvement, after 56 years, over Strassen's algorithm in this setting" — and optimised datacenter scheduling and LLM training [[src](https://arxiv.org/abs/2506.13131)] | Real but enormous; needs a fast, exact evaluator | **Relevant to doc 06 (kernels), not to S5** |
| **Fully autonomous "AI scientist" experiment design** | Idea → code → experiment → paper → automated review, "<$15 per paper" [[src](https://arxiv.org/abs/2408.06292)] | Cheap per unit, expensive in trust | The same paper's quality claim rests on its **own automated reviewer** | **Do not build.** A loop whose success criterion is its own judge is the exact failure doc 00 §5.1 forbids |

The one public precedent for the whole loop remains NVIDIA's Data Flywheel blueprint, which
automated "the exploration of various configurations using sensible defaults, and then
present[s] the most promising candidates to a research engineer or machine learning
engineer for further analysis", with **class-aware stratified splitting** by `workload_id`,
LoRA fine-tunes and LLM-as-judge similarity scores in `[0,1]`
[[src](https://raw.githubusercontent.com/NVIDIA-AI-Blueprints/data-flywheel/main/README.md)].
It is **deprecated as of April 2026** ("no longer actively maintained, and new production
use is not recommended") — the design is the asset, not the code.

Two details in that README are worth copying verbatim into the platform's data model:
`workload_id` as a **strict per-request-type primary key** ("If your application is an agent
with several nodes you **must assign a different `workload_id` to every node**"), and
keeping the **full request/response** because it "allows the Flywheel to replay prompts,
build few-shot demonstrations, and fine-tune without lossy conversions". Both are decisions
the trace schema (doc 02) has to make before any training is possible.

### 7.2 Guardrails against eval overfitting

The searcher is an optimiser pointed at a metric; doc 00's warning is about what happens
when the metric is wrong. Concretely:

1. **Three-way split, and the searcher never sees the third.** Train / search-validation /
   **frozen gate test**. The auto-research loop optimises against search-validation only.
   The gate test is opened once per promotion decision, by the gate, and its per-example
   results are never fed back into the searcher.
2. **Budget the number of looks.** Every evaluation of the gate test is a multiple-comparison
   event. Pre-register the number of candidates, apply a correction, or the "best of 40
   runs" is noise. Doc 00 §5.3's sample-size table already prices the margin; searching 40
   candidates against it without correction silently widens the effective margin.
3. **Fresh-eval canary.** Build a small, continuously-refreshed eval slice from the most
   recent week of production traffic that has never been available to any training or
   search run. GSM1k exists precisely because static benchmarks decay: "accuracy drops of up
   to 8%" moving from GSM8k to a same-distribution fresh set, with "systematic overfitting
   across almost all model sizes" in several families, and a Spearman's r² = 0.36 between
   the probability of generating GSM8k examples and the gap
   [[src](https://arxiv.org/abs/2405.00332)]. **The platform's version of GSM1k is last
   week's traffic.**
4. **Two-family validation for recipe changes** and a **random-reward control** for new RL
   recipes (§5.5) [[src](https://arxiv.org/abs/2506.10947)].
5. **Cost cap per customer per cycle**, enforced by the orchestrator. §6.3 makes individual
   runs cheap, which is exactly how an automated searcher runs up a bill.
6. **Every automated choice must be explainable to the customer in one sentence** ("we chose
   rank 64 because your completion set is 40 M tokens"). Doc 00 §6 doc 09 lists this as a
   hard problem; §1.1's capacity rule and §6.6's defaults exist to make most choices
   explainable by rule rather than by search.

### 7.3 What "auto" should mean in year one

A rules engine with a search inside it, not a research agent:

```
given (task_type, traffic_volume, verifier_available, teacher_logprobs_available, budget):
    rung  = ladder_rule(task_type, verifier_available, teacher_logprobs_available)
    rank  = smallest power-of-2 rank with lora_params >= completion_tokens   # §1.1
    lr    = 10 * base_lr(model_size)                                          # §6.6
    sweep = {lr × [0.5, 1, 2], epochs × [1, 2, 3]}  →  9 runs, gate-validated  # §7.1
    emit  candidate + a one-sentence rationale per choice
```

Everything in that block is sourced above. It will capture most of the available gain, it
costs a few thousand dollars per customer per cycle, and it cannot overfit the gate because
it never sees it.

---

## 8. Worked examples

### 8.1 Distilling a GPT-5.6-class support agent into Qwen3.8-27B

**Setting** (illustrative but arithmetically consistent; the traffic shape is an assumption
marked ⚠️, everything priced is sourced): a support-agent feature on **GPT-5.6 Sol**
($4/$20 per 1M, $0.40 cached input; doc 00 §2.1 [[src](https://developers.openai.com/api/docs/pricing)]),
**3,000-token prompt stack** (system + 18 tools + RAG), **400-token average output**,
**12 tool definitions**, **500 K requests/month**.

**Stage 0 — swap test (day 1, $0).** Run the customer's existing prompt against
GPT-5.6 Luna ($0.20/$1.20) and Claude Haiku 4.5 ($1/$5) on a 500-example paired sample.
Sometimes this ends the engagement at a win for the customer (doc 00 §3.2 rung 1). It also
calibrates the eval harness before any training risk is taken.

**Stage 1 — trace mining and round-1 dataset.** From 30 days of traces (doc 02), take
**2,000 examples** stratified by `workload_id` and slice
[[src](https://raw.githubusercontent.com/NVIDIA-AI-Blueprints/data-flywheel/main/README.md)],
weighted toward disagreements and retries (doc 00 §7.2). Teacher relabels them.

| Line item | Quantity | Cost |
|---|---:|---:|
| Teacher input | 2,000 × 3,000 = 6 M tokens @ $4/1M | $24 |
| Teacher output | 2,000 × 400 = 0.8 M tokens @ $20/1M | $16 |
| Batch API (50 % off, doc 00 §2.1) | — | **$20 total** |
| Training tokens (2 epochs) | 2,000 × 3,400 × 2 = 13.6 M | |
| GPU (LoRA, §6.3 @ $0.286/1M) | 0.53 GPU-hr | **$4** |
| **Round 1 total compute + teacher** | | **~$24** |

Round 1 costs less than lunch. **This is the point**: the first iteration should be run
before any contract negotiation about annotation budgets, because it produces a real
checkpoint and a real gate result for ~$25 of variable cost.

**Stage 2 — round 2, sized by the gate.** Suppose round 1 lands 6 points below the
incumbent on the two hardest slices. Scale to **60,000 examples**, still stratified, still
disagreement-weighted:

| Line item | Quantity | Cost (batch, 50 % off) |
|---|---:|---:|
| Teacher input | 180 M tokens @ $4/1M | $360 |
| Teacher output | 24 M tokens @ $20/1M | $240 |
| **Teacher subtotal** | | **$600** |
| Training tokens (2 epochs) | 408 M | |
| GPU — LoRA rank 128 | 15.7 GPU-hr → **2.0 h on one 8×B300 node** | **$116** (`low`) / $236 (`high`) |
| GPU — full FT alternative | 23.6 GPU-hr → 3.0 h | $175 / $354 |
| Judge calls for the S7 gate | per doc 00 §5.3 (≥1,752 paired examples for a 3 pp margin at p=0.85) | owned by doc 04 |
| **Round 2 training subtotal** | | **~$716** |

**LoRA rank check (§1.1):** completion tokens = 60,000 × 400 = **24 M**. Qwen3.8-27B is
27.78 B dense; at rank `r` over all matrices, LoRA parameters are on the order of
`r × 10⁷` for a model this size (`est.`, from typical hidden dims — ⚠️ compute exactly from
the config rather than trusting this), so **rank ≈ 64–128 satisfies `lora_params ≥
completion_tokens`**, which matches the rank-128 setting in Tinker's own distillation recipe
[[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/)].

**Stage 3 — rejection-sampling FT on the verifiable part (free lift).** The tool layer gives
a verifier for nothing: tool exists, arguments type-check against the JSON schema, required
fields present, retrieval citation resolves. Sample N = 4 completions per prompt from the
Stage-2 student, keep the ones that pass, SFT again. This is rung 2 — the RFT result
(35.9 % → 49.3 % on GSM8K [[src](https://arxiv.org/abs/2308.01825)]) is the analogous
curve, and it needs **no teacher tokens at all**, only student sampling. ⚠️ **The analogy
is optimistic**: the paper's 49.3 % comes from *"rejection samples from multiple models"*,
not from a single policy sampling itself (verified 2026-09-19), so a single-student RFT
loop should be budgeted for less than that spread.

**Stage 4 — on-policy distillation, with the teacher constraint respected.** GPT-5.6 cannot
supply per-token logprobs on the student's tokens (§1.3). Two options:

- **Preferred**: switch the teacher to a self-hosted open-weights model —
  **DeepSeek-V4.1-Flash on 4 B300s** (TP4, the recommended shape; TP2 only with mandatory
  Engram CPU offload) or **Kimi-K3 on 8** ([`matrix/fit-matrix.md`](../matrix/fit-matrix.md)) —
  served under vLLM with `prompt_logprobs` [[src](https://docs.vllm.ai/en/latest/api/vllm/sampling_params.html)].
  This also removes the legal dependency of doc 00 §8.1. Cost: the teacher node's hours for
  the duration of the run, plus student training; for a 200-step run at 16 K rollouts,
  ~$1–3 K `est.` at B300 `low` depending on rollout length.
- **Fallback**: stay at rungs 2–3. KTO on the customer's binary production signals
  [[src](https://arxiv.org/abs/2402.01306)] is the cheapest next lift and needs no pairs.

**Stage 5 — behaviour preservation (every cycle, forever).** Add the previous production
checkpoint as a distillation teacher on a fixed 1–2 K prompt set covering format compliance,
tool syntax, refusals and the safety suite (§1.10, §4.7). Published analogue: IF-eval
recovered 79 % → 83 % while *keeping* the new knowledge
[[src](https://thinkingmachines.ai/blog/on-policy-distillation/)].

**Expected quality trajectory, with the published curves it is analogised from** — ⚠️ these
are *analogies*, not predictions for this task:

| Stage | Published analogue | What it showed |
|---|---|---|
| Stage 1 (2 K SFT) | LIMA (1 K examples) [[src](https://arxiv.org/abs/2305.11206)]; AlpaGasus (9 K > 52 K) [[src](https://arxiv.org/abs/2307.08701)] | Format and style transfer almost immediately; task accuracy lags |
| Stage 2 (60 K SFT) | Orca 13 B reaching ChatGPT parity on BBH [[src](https://arxiv.org/abs/2306.02707)]; NVIDIA flywheel 1 B at ~98 % of 70 B on tool routing [[src](https://raw.githubusercontent.com/NVIDIA-AI-Blueprints/data-flywheel/main/README.md)] | Tool-routing-like sub-tasks reach near-parity; open-ended sub-tasks do not |
| Stage 3 (RFT) | RFT 35.9 % → 49.3 % GSM8K [[src](https://arxiv.org/abs/2308.01825)] | Verifiable sub-behaviour improves sharply; unverifiable does not move |
| Stage 4 (on-policy KD) | 65 % → 76.7 % AIME'24 at rank 128, 200 steps [[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/)]; 60 % → 74.4 % at 1,800 vs 17,920 GPU-hr [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)] | The largest single jump per unit compute in the published literature |
| Stage 5 (preservation) | IF-eval 79 % → 83 % with knowledge retained [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)] | Regression repair without losing the new capability |

**Total variable cost of one full cycle: roughly $1 K–$4 K** (teacher + GPU; the range's
top end assumes a self-hosted-teacher on-policy phase per Stage 4), against an
incumbent spend of `500,000 × (3,000 × $4 + 400 × $20) / 1e6 = $10,000/month` at list. Doc 00
§4.4 insists on comparing against the customer's *batched, cached, tier-downed* price
instead, which it puts at **$0.38–$1.66 blended per 1M**; on this feature's
`500,000 × 3,400 = 1.7 B` monthly tokens that is **$646–$2,822/month**, and the midpoint
~$1,700 is the number the pitch must beat. **That ratio — a cycle costing roughly one to two months of the honest incumbent
baseline — is the actual unit economics of the product**, and it says the loop must reach
parity within a small number of cycles or the arithmetic never closes. It also says the
annotation and eval budget, not the GPU budget, is what the contract must cover.

### 8.2 Distilling a video-QA behaviour into Marlin-2B

**Setting**: customer has a video-understanding feature on a frontier multimodal model;
20,000 clips available; the served configuration is Marlin-2B's fixed budget of **2 fps,
≤ 240 frames, 200,704 px/frame ⇒ ~23,560 prefill tokens/clip**
([`METHODOLOGY.md`](../METHODOLOGY.md) §8, [`models/marlin2b/README.md`](../models/marlin2b/README.md) §9).

**Non-negotiable first rule: train at the serving frame budget.** If the teacher sees the
full clip and the student sees 240 frames, the student is being trained to answer questions
about information it will not have at serve time. The teacher's annotation pass should
either be run on the same 240-frame sample, or the resulting questions filtered to those
answerable from it. ⚠️ Doc 00 Open Question 8 (is frame-sampled grading a valid proxy for
full-clip human grading?) is the *evaluation* half of this same question and is owned by
doc 04; the training half is owned here and the two must be answered consistently.

**Dataset sizing, anchored to the best published analogue.** LLaVA-Video-178K used
**178,510 videos → 1.3 M instruction samples (178 K captions, 960 K open QA, 196 K MCQ) at
1 fps, annotated by GPT-4o**, producing a 7 B at **63.3 Video-MME (no subs)**
[[src](https://arxiv.org/html/2410.02713v3)]. That is ~7.3 samples/video. Scaled to 20,000
customer clips: **~145 K instruction samples**, of which the MCQ portion (~15 %) is
**automatically verifiable** and therefore RFT-eligible (rung 2).

| Line item | Quantity | Cost |
|---|---:|---:|
| Teacher annotation input | 20,000 × ~23,560 ≈ **471 M tokens** | **⚠️ unpriceable here** — frontier video token accounting differs per vendor and I have no sourced video-token price; doc 00 §2.1's text prices do not apply. At a nominal $10/1M input this would be ~$4,700 |
| Training tokens | 145 K samples; at ~23.6 K tokens/sample if each carries its own clip → **3.4 B tokens/epoch** | The dominant number, and the reason to **batch multiple QA targets per clip forward pass** |
| Training tokens, batched (7.3 QA per clip in one pass) | 20,000 × 23,560 × 3 epochs ≈ **1.41 B** | |
| GPU, Marlin-2B full FT (§6.3: 4.60 GPU-hr/1B) | **6.5 GPU-hr** | $48 `low` |
| GPU with a 2× VLM penalty (vision encoder FLOPs not in 6ND; lower MFU on a 2 B model) `est.` | **~13 GPU-hr** | **~$96** `low` / $195 `high` |

**The structural insight this example produces**: for video, **the sample-packing decision is
worth more than every other optimisation combined**. Training one QA pair per forward pass
costs 3.4 B tokens per epoch; sharing one clip's vision encoding across its ~7 questions
costs 0.47 B. That is a **7× training-cost difference** from a data-loader choice, and it
does not exist for text. ⚠️ Verify that the chosen framework supports multi-target sharing
of a single visual encoding before committing to a budget.

**Expected trajectory — stated as a warning, not a forecast.** The 7 B analogue scored 63.3
on Video-MME; **Marlin-2B is 2.21 B unique parameters, ~3× smaller**. There is no published
result showing a ~2 B VLM at parity with a frontier model on a customer video task (doc 00
Open Question 7). The defensible plan is:

1. Scope the video task to **one question type** (e.g. "does this clip contain event X?"),
   which is MCQ-verifiable and therefore supports rungs 1–2 and a cheap eval.
2. Gate on that scope before widening. If a narrow, verifiable video task cannot reach
   parity at 2.21 B, an open-ended one certainly cannot, and the customer should be told so
   at the end of cycle 1 rather than cycle 4.
3. Budget the **evaluation**, not the training: doc 00 §5.5 notes a video judge is a
   frontier multimodal call per example, so doc 00 §5.3's sample sizes translate into a
   materially larger eval bill than for text. **For video, eval cost exceeds training cost
   by a wide margin**, which inverts the usual intuition about where to economise.

---

## Implications for the platform

### What to build

1. **The ladder as a rules engine (§1.0, §2, §7.3).** Task type + verifier availability +
   teacher-logprob availability + budget → a recipe, with a one-sentence rationale per
   choice. Every rule in it is sourced in §1–§6. This is a week of work and it replaces most
   of what "auto-research" is imagined to be.
2. **The LoRA capacity rule as a first-class product decision (§1.1).**
   `lora_params ≥ completion_tokens` at rank 32+, all matrices, LR ×10. Keeping the student
   an *adapter* is what makes multi-tenant serving (doc 00 §4.5) possible, so the platform
   should pay rank rather than switch to full FT unless a gate forces it.
3. **A self-hosted open-weights teacher with `prompt_logprobs` exposed (§1.3).** This is the
   single highest-leverage build in this document: it unlocks the best rung on the ladder,
   and it removes the legal single-point-of-failure of doc 00 §8.1. Two independent
   arguments, one conclusion. Kimi-K3 on 8×B300 or DeepSeek-V4.1-Flash on 4 (TP4 recommended;
   TP2 only with mandatory Engram CPU offload).
4. **The behaviour-preservation stage (§1.10, §8.1 Stage 5).** The previous production
   checkpoint as a standing distillation teacher on a fixed prompt set, in every cycle.
   Defends I4 and I5, costs only student compute, and is published to work.
5. **Chat-template and loss-mask CI (§4.4, §4.6).** Byte-equality between the training
   renderer and the serving renderer; assistant-only loss masking asserted numerically;
   packing non-contamination asserted by test. Three small tests that prevent three classes
   of silent quality loss. A mainstream framework shipped a multimodal masking fix four
   months ago; assume ours is wrong until tested.
6. **Append-never-replace datasets (§4.5).** Union of all cycles' data, deduplicated, never
   evicted. Theoretically grounded and nearly free.
7. **The split-rubric discipline (§5.2, §5.4).** A training rubric and a separate held-out
   evaluation rubric; the training judge may be the teacher, the gate judge may not; judge
   version pinned in the artifact; KL-to-reference, output length and per-criterion scores
   logged on every RL run.
8. **Round-based annotation budgeting (§4.1, §8.1).** 2 K → gate → 10 K → gate → 60 K.
   Round 1 costs ~$25 and produces a real checkpoint and a real gate result.

### What to buy / adopt

- **The Tinker API as the internal training interface, with two backends** — hosted Tinker
  and `skyrl-tx`/VeRL-Tinker on our own B300s (§3.4). Four independent projects now
  implement it; it prices the buy/build boundary continuously; it survives a vendor sunset.
- **TRL** for everything through rung 3, **Axolotl** for this repo's specific models (hybrid
  SSM context parallelism, NVFP4 MoE LoRA with adapter merge-back), **verl or NeMo RL** when
  a rung-5 run needs scale, **OpenRLHF** for multi-turn VLM RL.
- **Managed training tokens while utilisation is low** (§6.5): Together at **$1.05/1M**
  at **$1.05/1M** for 27–35 B SFT is the most competitive managed number found; Tinker at $4.103/1M for
  Qwen3.8-27B buys zero-ops LoRA; Baseten sells B200 capacity at $9.98/GPU-hr with no idle
  charge. Cross to self-hosting when training utilisation would exceed ~40 % (⚠️ threshold
  is inferred, not measured).
- **Nothing for reward modelling.** Skip RMs in year one (§5.1).

### What to avoid

- **torchtune** — explicitly unmaintained since 2025 [[src](https://raw.githubusercontent.com/pytorch/torchtune/main/README.md)].
  Fourth sunset in doc 00's tally.
- **Depending on a frontier teacher's logprobs.** They do not exist (§1.3). Any roadmap item
  that assumes on-policy distillation from GPT-5.6, Opus-5 or Fable-5.1 is not buildable
  today, independent of the legal question.
- **Optimising training compute.** §6.3: a full SFT cycle for a 27 B student is tens to
  hundreds of dollars. Annotation and eval are 5–30× that. Engineering effort should follow
  the money.
- **RL before rung 4 is exhausted.** §6.4's 20–30× throughput gap plus the 1,800-vs-17,920
  GPU-hour published comparison.
- **GRPO on an MoE student.** Use GSPO [[src](https://arxiv.org/abs/2507.18071)].
- **PRMs.** §1.8 — the human-label cost is PRM800K-scale and the synthesis shortcut is
  documented not to work.
- **A self-judging research loop** (AI-Scientist pattern). §7.1.
- **Training video at a different frame budget than serving** (§8.2).
- **Aggregate win rates without a length control** (§1.5).
- **Closed-weights students trainable on only one vendor's service** (§3.2, Inkling) — it
  contradicts the weight-ownership requirement that the training vendors themselves
  advertise.

---

## Open questions

⚠️ Consolidated. Items 1–4 are answers this document produced to doc 00's open questions;
the rest are new or inherited.

1. **RESOLVED (doc 00 OQ6) — teacher logprobs.** OpenAI chat: output tokens only, no `echo`
   [[src](https://developers.openai.com/api/docs/api-reference/chat/create)]; legacy
   Completions has `echo` + `logprobs` ≤ 5 but only for `gpt-3.5-turbo-instruct`,
   `davinci-002`, `babbage-002` [[src](https://developers.openai.com/api/docs/api-reference/completions/create)];
   Anthropic Messages: none [[src](https://platform.claude.com/docs/en/api/messages)];
   vLLM `prompt_logprobs`: yes, up to full vocab
   [[src](https://docs.vllm.ai/en/latest/api/vllm/sampling_params.html)]. **Gemini remains
   ⚠️ unconfirmed** — the `generateContent` reference I fetched shows no logprobs field
   [[src](https://ai.google.dev/api/generate-content)]; someone with search should settle it.
2. **RESOLVED (doc 00 OQ5) — OpenPipe.** "The OpenPipe platform has migrated to Weights &
   Biases and CoreWeave" [[src](https://openpipe.ai/)]. What survived, and under what name,
   is still open. *Owner: doc 09.*
3. **PARTIALLY RESOLVED (doc 00 OQ13) — Inkling as a student.** Described as a general-purpose
   multimodal model "tailored for Tinker" with tool use and an effort parameter
   [[src](https://tinker-docs.thinkingmachines.ai/cookbook/inkling/)]; **no parameter count,
   context length or benchmark published**. Pricing is known
   [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]. Still not selectable as
   a customer student without independent evaluation.
4. **UNCHANGED (doc 00 OQ4) — Predibase.** `predibase.com` → `rubrik.com` 403, re-checked
   2026-09-19. *Owner: doc 09.*
5. **⚠️ Tinker's operational limits.** Rate limits, max LoRA rank, max context, storage
   quotas and concurrency are **not documented** on the pages fetched
   [[src](https://tinker-docs.thinkingmachines.ai/)]. Required in writing before any
   customer-facing SLA depends on it. *Owner: this doc, next revision.*
6. **⚠️ `skyrl-tx` API coverage versus hosted Tinker.** §3.4's two-backend recommendation
   assumes near-parity. Unverified. Also unverified: whether Tinker's terms permit reselling
   access inside a platform. *Owner: this doc + doc 08 (terms).*
7. **⚠️ Cross-tokenizer distillation quality cost.** NeMo RL lists cross-tokenizer support
   [[src](https://raw.githubusercontent.com/NVIDIA-NeMo/RL/main/README.md)]; no quality
   measurement found. Decides whether teacher and student must share a family.
8. **⚠️ The 800 TFLOP/s/GPU planning constant (§6.1).** Anchored to NVIDIA's *pre-training*
   rows on GB300; **no published SFT/LoRA throughput table exists** for B300
   [[src](https://docs.nvidia.com/nemo-framework/user-guide/latest/performance/performance-summary.html)].
   Every cost in §6.3 scales inversely with it. **Measure it on the first real run and
   re-cut the table.** *Owner: this doc.*
9. **⚠️ LoRA parameter count for Qwen3.8-27B at a given rank.** §8.1 uses an order-of-magnitude
   estimate; compute it exactly from the config (hidden dims × adapted matrices) and publish
   the rank↔completion-token table per repo model. Trivial work, load-bearing rule.
10. **⚠️ Does LoRA prevent forgetting?** Biderman et al. say it regularises better than weight
    decay/dropout [[src](https://arxiv.org/abs/2405.09673)]; the TM blog reports "LoRA showed
    similar forgetting" in their midtrain experiment
    [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)]. Unsettled; measure per
    task before relying on it.
11. **⚠️ Replay fraction for continual cycles.** The mechanism is sourced
    [[src](https://arxiv.org/abs/2403.08763)]; the percentage is not. Needs an experiment on
    real customer cycles.
12. **⚠️ Safety-data fraction in a distillation mixture.** No published guidance found (§4.7).
13. **⚠️ RegMix/DoReMi transfer to post-training mixtures.** Both results are from
    pre-training [[src](https://arxiv.org/abs/2407.01492)] [[src](https://arxiv.org/abs/2305.10429)].
    The automated-mixture recommendation in §7.1 rests on an untested transfer.
14. **⚠️ Multi-target sharing of one visual encoding (§8.2).** A 7× video training-cost
    difference depends on it. Verify framework support before budgeting.
15. **⚠️ Frontier video token pricing.** §8.2's teacher annotation line is unpriceable with
    sourced numbers. *Owner: the annotation doc.*
16. **⚠️ The 40 % utilisation buy/build threshold (§6.5).** Inferred from price ratios, not
    computed. *Owner: doc 06, with [`scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md).*
17. **⚠️ Prompt-distillation win size.** Tinker documents the mechanism with no quantitative
    results [[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/prompt-distillation/)].
    For customers whose bill is dominated by a large system prompt this could be the whole
    saving; measure it early.
18. **⚠️ Market coverage (inherited, doc 00 OQ1).** This session also had no web search. The
    framework table in §3 is a floor on the landscape, not a scan.

---

## Sources

All fetched **2026-09-19** unless the source states its own date.

**Distillation**
- MiniLLM: On-Policy Distillation of Large Language Models (2023-06-14, ICLR 2024) — https://arxiv.org/abs/2306.08543
- GKD / On-Policy Distillation of Language Models (2023-06-23, ICLR 2024) — https://arxiv.org/abs/2306.13649
- DistiLLM: Towards Streamlined Distillation for LLMs (2024-02-06, rev. 2024-07-03, ICML 2024) — https://arxiv.org/abs/2402.03898
- Thinking Machines, "On-Policy Distillation" (2025-10-27) — https://thinkingmachines.ai/blog/on-policy-distillation/
- Orca: Progressive Learning from Complex Explanation Traces of GPT-4 (2023-06-05) — https://arxiv.org/abs/2306.02707
- Distilling Step-by-Step! (2023-05-03, ACL Findings 2023) — https://arxiv.org/abs/2305.02301
- LLaVA-Video: Video Instruction Tuning With Synthetic Data (2024-10-03, rev. 2025-08-01) — https://arxiv.org/html/2410.02713v3
- Video-MME (2024-05-31, rev. 2025-05-30) — https://arxiv.org/abs/2405.21075

**PEFT**
- LoRA: Low-Rank Adaptation of Large Language Models — https://arxiv.org/abs/2106.09685
- QLoRA: Efficient Finetuning of Quantized LLMs (2023-05-23) — https://arxiv.org/abs/2305.14314
- DoRA: Weight-Decomposed Low-Rank Adaptation (2024-02-14, ICML 2024 oral) — https://arxiv.org/abs/2402.09353
- LoRA Learns Less and Forgets Less (2024-05-15, TMLR 2024) — https://arxiv.org/abs/2405.09673
- Thinking Machines, "LoRA Without Regret" (2025-09-29) — https://thinkingmachines.ai/blog/lora/
- S-LoRA: Serving Thousands of Concurrent LoRA Adapters — https://arxiv.org/abs/2311.03285

**Rejection sampling / self-training**
- STaR: Bootstrapping Reasoning With Reasoning (2022-03-28) — https://arxiv.org/abs/2203.14465
- Scaling Relationship on Learning Mathematical Reasoning (RFT) (2023-08-03) — https://arxiv.org/abs/2308.01825
- Beyond Human Data: Scaling Self-Training (ReST-EM) (2023-12-11, TMLR) — https://arxiv.org/abs/2312.06585
- Self-Rewarding Language Models (2024-01-18) — https://arxiv.org/abs/2401.10020
- Self-Instruct (2022-12-20, rev. 2023-05-25) — https://arxiv.org/abs/2212.10560

**Preference optimisation and RLHF**
- DPO (2023-05-29, rev. 2024-07-29) — https://arxiv.org/abs/2305.18290
- IPO / ΨPO: A General Theoretical Paradigm (2023-10-18) — https://arxiv.org/abs/2310.12036
- KTO: Model Alignment as Prospect Theoretic Optimization (2024-02-02, ICML 2024) — https://arxiv.org/abs/2402.01306
- ORPO (2024-03-12) — https://arxiv.org/abs/2403.07691
- SimPO (2024-05-23, NeurIPS 2024) — https://arxiv.org/abs/2405.14734
- Is DPO Superior to PPO for LLM Alignment? (ICML 2024) — https://arxiv.org/abs/2404.10719
- RLAIF vs. RLHF (2023-09-01, ICML 2024) — https://arxiv.org/abs/2309.00267

**RL with verifiable rewards**
- DeepSeekMath / GRPO (2024-02-05) — https://arxiv.org/abs/2402.03300
- Tulu 3: Pushing Frontiers in Open Language Model Post-Training (2024-11-22) — https://arxiv.org/abs/2411.15124
- DAPO: An Open-Source LLM RL System at Scale (2025-03-18) — https://arxiv.org/abs/2503.14476
- GSPO: Group Sequence Policy Optimization (2025-07-24) — https://arxiv.org/abs/2507.18071
- Spurious Rewards: Rethinking Training Signals in RLVR (2025-06-12, rev. 2026-02-25) — https://arxiv.org/abs/2506.10947
- DeepSeek-R1 (2025-01-22, v2 2026-01-04) — https://arxiv.org/abs/2501.12948

**Reward models, judges, process supervision**
- Scaling Laws for Reward Model Overoptimization (2022-10-19) — https://arxiv.org/abs/2210.10760
- RewardBench (2024-03-20) — https://arxiv.org/abs/2403.13787
- Let's Verify Step by Step / PRM800K (2023-05-31) — https://arxiv.org/abs/2305.20050
- The Lessons of Developing Process Reward Models in Mathematical Reasoning (2025-01-13) — https://arxiv.org/abs/2501.07301
- Rubrics as Rewards (2025-07-23, rev. 2025-10-03) — https://arxiv.org/abs/2507.17746

**Data**
- LIMA: Less Is More for Alignment (2023-05-18) — https://arxiv.org/abs/2305.11206
- AlpaGasus (2023-07-17) — https://arxiv.org/abs/2307.08701
- s1: Simple Test-Time Scaling (2025-01-31) — https://arxiv.org/abs/2501.19393
- LIMO: Less is More for Reasoning (2025-02-05) — https://arxiv.org/abs/2502.03387
- Efficient Sequence Packing without Cross-contamination (2021-06-29, rev. 2022-10-05) — https://arxiv.org/abs/2107.02027
- DoReMi (2023-05-17) — https://arxiv.org/abs/2305.10429
- RegMix (2024-07-01, rev. 2025-01-23) — https://arxiv.org/abs/2407.01492
- The Curse of Recursion (2023-05-27) — https://arxiv.org/abs/2305.17493
- Is Model Collapse Inevitable? (2024-04-01) — https://arxiv.org/abs/2404.01413
- xLAM: A Family of Large Action Models (2024-09-05) — https://arxiv.org/abs/2409.03215
- HuggingFace Transformers, chat templating — https://huggingface.co/docs/transformers/main/en/chat_templating

**Continual learning and merging**
- Catastrophic forgetting in LLMs during continual fine-tuning (2023-08-17) — https://arxiv.org/abs/2308.08747
- Simple and Scalable Strategies to Continually Pre-train LLMs (2024-03-13) — https://arxiv.org/abs/2403.08763
- TIES-Merging (2023-06-02, NeurIPS 2023) — https://arxiv.org/abs/2306.01708

**Systems and compute**
- Efficient Large-Scale Training on GPU Clusters Using Megatron-LM (2021-04-09) — https://arxiv.org/abs/2104.04473
- Reducing Activation Recomputation in Large Transformer Models (2022-05-10) — https://arxiv.org/abs/2205.05198
- PyTorch FSDP (2023-04-21) — https://arxiv.org/abs/2304.11277
- HybridFlow / verl paper — https://arxiv.org/abs/2409.19256
- NVIDIA NeMo Framework performance summary (container 26.08.01) — https://docs.nvidia.com/nemo-framework/user-guide/latest/performance/performance-summary.html
- NVIDIA NeMo RL performance summary (v0.6) — https://docs.nvidia.com/nemo/rl/latest/about/performance-summary.html

**Frameworks (READMEs fetched raw via curl, 2026-09-19)**
- TRL — https://raw.githubusercontent.com/huggingface/trl/main/README.md
- Axolotl — https://raw.githubusercontent.com/axolotl-ai-cloud/axolotl/main/README.md
- LLaMA-Factory — https://raw.githubusercontent.com/hiyouga/LLaMA-Factory/main/README.md
- torchtune (**deprecated**) — https://raw.githubusercontent.com/pytorch/torchtune/main/README.md
- Unsloth — https://raw.githubusercontent.com/unslothai/unsloth/main/README.md
- OpenRLHF — https://raw.githubusercontent.com/OpenRLHF/OpenRLHF/main/README.md
- verl — https://raw.githubusercontent.com/volcengine/verl/main/README.md
- SkyRL — https://raw.githubusercontent.com/NovaSky-AI/SkyRL/main/README.md
- Oumi — https://raw.githubusercontent.com/oumi-ai/oumi/main/README.md
- NVIDIA NeMo RL — https://raw.githubusercontent.com/NVIDIA-NeMo/RL/main/README.md
- NVIDIA Data Flywheel Blueprint (**deprecated Apr 2026**) — https://raw.githubusercontent.com/NVIDIA-AI-Blueprints/data-flywheel/main/README.md

**Vendor product, pricing and API docs**
- Tinker docs (index) — https://tinker-docs.thinkingmachines.ai/
- Tinker models & pricing — https://tinker-docs.thinkingmachines.ai/tinker/models/
- Tinker LoRA primer — https://tinker-docs.thinkingmachines.ai/tinker/lora-primer/
- Tinker under the hood — https://tinker-docs.thinkingmachines.ai/tinker/under-the-hood/
- Tinker distillation recipe — https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/
- Tinker prompt-distillation recipe — https://tinker-docs.thinkingmachines.ai/cookbook/recipes/prompt-distillation/
- Tinker rubric recipe — https://tinker-docs.thinkingmachines.ai/cookbook/recipes/rubric/
- Tinker SDFT recipe — https://tinker-docs.thinkingmachines.ai/cookbook/recipes/sdft/
- Tinker agent-RL recipe — https://tinker-docs.thinkingmachines.ai/cookbook/recipes/agent-rl/
- Tinker VLM-classifier recipe — https://tinker-docs.thinkingmachines.ai/cookbook/recipes/vlm-classifier/
- Tinker Inkling cookbook — https://tinker-docs.thinkingmachines.ai/cookbook/inkling/
- Tinker model deprecations — https://tinker-docs.thinkingmachines.ai/tinker/model-deprecations/
- Baseten Training (Training Jobs GA, Loops early access) — https://www.baseten.co/products/training/
- Baseten pricing (per-GPU-minute) — https://www.baseten.co/pricing/
- Fireworks fine-tuning docs — https://docs.fireworks.ai/fine-tuning/fine-tuning-models
- Together pricing (fine-tuning + clusters) — https://www.together.ai/pricing
- OpenPipe (migration notice) — https://openpipe.ai/
- OpenAI API pricing — https://developers.openai.com/api/docs/pricing
- OpenAI chat completions reference (logprobs) — https://developers.openai.com/api/docs/api-reference/chat/create
- OpenAI legacy completions reference (echo + logprobs) — https://developers.openai.com/api/docs/api-reference/completions/create
- OpenAI Evals guide (sunset dates) — https://developers.openai.com/api/docs/guides/evals
- Anthropic Messages API reference — https://platform.claude.com/docs/en/api/messages
- Google Gemini generateContent reference — https://ai.google.dev/api/generate-content
- vLLM SamplingParams (`prompt_logprobs`) — https://docs.vllm.ai/en/latest/api/vllm/sampling_params.html

**Auto-research**
- GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning (2025-07-25, rev. 2026-02-14) — https://arxiv.org/abs/2507.19457
- AlphaEvolve (2025-06-16) — https://arxiv.org/abs/2506.13131
- The AI Scientist (2024-08-12) — https://arxiv.org/abs/2408.06292
- A Careful Examination of LLM Performance on Grade School Arithmetic (GSM1k) (2024-05-01) — https://arxiv.org/abs/2405.00332

**This repository**
- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — §8 pinned model/GPU inputs, price conventions
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) — §3 `low`/`high`/`res1y` GPU price tiers
- [`research/matrix/fit-matrix.md`](../matrix/fit-matrix.md) — per-model GPU fits
- [`research/platform/00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) — the loop, invariants, teacher ToS, judge validity, sample sizes
- [`research/scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md) — utilisation economics

---

## Verification log (2026-09-19)

Adversarial fact-check. **36 consequential claims** were selected (paper results and
numbers, product features and pricing, version numbers, cost arithmetic, statements about
competitors, statements about this repo's models and `research/` docs). **Every primary
source was opened; no citation was trusted as printed.** Derivations were recomputed with
`python3`. Verdicts: **21 CONFIRMED, 13 CORRECTED, 2 UNVERIFIABLE**. Where a source
confirmed part of a claim and broke another part, the claim is listed once, under its
worst verdict, with the confirmed half stated. The document body was edited in place;
nothing was removed.

### CORRECTED

| # | Claim as printed | What the source says | Where fixed |
|---|---|---|---|
| 1 | §1.1 quotes Biderman et al. as *"full finetuning learns parameter updates requiring 10-100X greater rank than typical LoRA settings"* | The abstract reads *"full finetuning learns perturbations with a rank that is 10-100X greater than typical LoRA configurations"*. The substance (10–100×, TMLR 2024, "substantially underperforms" on programming and maths, ~100 K pairs / 20 B tokens, better out-of-domain retention, beats weight decay and dropout) is **confirmed verbatim** — only the quoted wording was wrong [[src](https://arxiv.org/abs/2405.09673)] | §1.1 |
| 2 | §3.2 Tinker row: **Kimi-K2.6, 128 K, $5.15 / $12.81 / $15.40** | The models page lists **Kimi-K2.6 at 32 K: $2.205 prefill ($0.441 cached) / $5.49 sample / $4.84 train**, with a 128 K variant listed separately whose prices did not come back on re-fetch [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)] | §3.2 |
| 3 | §3.2 **Inkling** "$11.22 (50 % promo: $1.87 / $4.68 / $5.61)" and **Inkling-Small** "(promo: $0.58 / $1.44 / $1.73)" | The $3.74 / $9.36 / $11.22 and $1.16 / $2.88 / $3.46 figures **are already the 50 %-discounted prices**; the struck-through list prices are double them. The document halved an already-halved number, understating Inkling's train price by 2× [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)] | §3.2 |
| 4 | §3.2 **Nemotron-3.5-Lightning** "$0.88 (disc. $0.44)" | Same error: $0.39 / $0.99 / $0.88 carry the "Limited-time 50 % discount" label; list is $0.78 / $1.98 / $1.76 [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)] | §3.2 |
| 5 | §3.2 **Nemotron-3-Ultra-550B-A55B** "$10.956 (disc. $5.478)" | No discount label was found on that row on re-fetch. Downgraded to ⚠️ [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)] | §3.2 |

**Re-verification, 2026-09-19 (platform consistency pass): items 3–5 above were themselves wrong and have been reversed in §3.2.** A fresh fetch of the same page shows $3.74 / $9.36 / $11.22 (Inkling 64K), $1.16 / $2.88 / $3.46 (Inkling-Small 64K), $0.39 / $0.99 / $0.88 (Nemotron-3.5-Lightning) and $4.98 / $12.45 / $10.956 (Nemotron-3-Ultra) are the **list** prices, each row carrying a "Limited-time 50 % discount" label; the promo (50 %-off) prices are $1.87 / $4.68 / $5.61, $0.58 / $1.44 / $1.73, $0.195 / $0.495 / $0.44 and $5.478 respectively — i.e. the *original* pre-2026-09-19 figures this log's items 3–5 struck through were correct all along, and [`07` §15.2](07-competitor-analysis.md), [`00` §7](00-goal-and-problem-statement.md) and [`06` §6.2](06-platform-architecture.md) had it right throughout. §3.2's table is corrected accordingly.

| 6 | §3.3 / §6.5 Together: *"~$0.34–$0.84/1M (0.8–9 B) SFT … $1.05–$1.16 SFT / $2.62–$2.88 DPO for 27–35 B … $2.03–$5.60 SFT / $5.08–$14.00 DPO for 70 B+ … per-job minimum $4–$100"* | The page lists **one price per band**: 0.8–9 B **$0.34 SFT / $0.84 DPO**; 27–35 B **$1.05 SFT / $2.62 DPO**; 70 B+ **$2.03–$7.00 SFT / $5.08–$17.50 DPO**; per-job minimum **$4.00–$22.00**. The 70 B+ ceilings were understated by 25 % and 25 %; the $1.16 / $2.88 upper bounds and the $100 minimum do not exist [[src](https://www.together.ai/pricing)] | §3.3, §6.5, "What to buy" |
| 7 | §6.5 *"Together's 27–35 B SFT price is 3.7–4.1× `low` and 1.8–2.0× `high`"* | With the single sourced price, **3.7×** `low` and **1.8×** `high`. Recomputed: 1.05 ÷ 0.286 = 3.671; 1.05 ÷ 0.579 = 1.813 | §6.5 |
| 8 | §6.5 *"inference from the 2.4–14× price ratios"* | No pair of figures in that table yields 2.4×. The actual span is **1.35×** (Baseten B200 $9.98 ÷ B300 `low` $7.40) to **14.3×** (Tinker ÷ self-hosted `low`) | §6.5 |
| 9 | §3.4 *"Tinker's Qwen3.8-27B train price is roughly **9–14×** the … self-hosted LoRA compute cost"* | §6.5's own arithmetic gives **7.1–14.3×** ($4.103 ÷ $0.286 = 14.346; ÷ $0.579 = 7.086). The two sections contradicted each other | §3.4 |
| 10 | §6.1 *"**20–33 % MFU** at MXFP8 and **9–9.7 %** of FP4 peak at NVFP4"* | The NeMo rows themselves (all nine **confirmed exactly**, container 26.08.01) give **16.7–32.7 %** at MXFP8 — the 834 (DeepSeekV4 Flash) and 973 (Nemotron-3.5-Lightning) rows are both under 20 % — and **5.8–9.0 %** at NVFP4 against GB300's pinned 15,000 TFLOPS. The "9.7 %" figure is 871 ÷ **9,000**, i.e. it silently used **B200's** FP4 peak on a **GB300** row, exactly the "no silent … mixing" failure METHODOLOGY §7 forbids [[src](https://docs.nvidia.com/nemo-framework/user-guide/latest/performance/performance-summary.html)] | §6.1 |
| 11 | §6.4 table row *"DeepSeek V3 \| GRPO \| H100 **FP8** \| 512 \| — \| **14.1** \| **59.2 s**"* | The NeMo RL v0.6 page gives **H100 BF16, 512 GPUs, 1-step off-policy, 12.8 tokens/s/GPU, 64.1 s**. Every other row in that table (including the load-bearing Qwen3-30B3A 1,910 and the agentic 37.5 / 430 s) is **confirmed exactly**; an extra H100-32 1-step-off row (1,414 / 152 s) exists that the document omits [[src](https://docs.nvidia.com/nemo/rl/latest/about/performance-summary.html)] | §6.4 |
| 12 | §6.2 *"pure-bf16 **nearly fits** one GPU"* | 27.78 × 8 = **222.2 GB**, against B300's pinned **268 GB/GPU** (METHODOLOGY §8). It fits, with 46 GB spare before activations — the hedge understated the result | §6.2 |
| 13 | §1.3 / §8.1 / Implications: teacher = *"DeepSeek-V4.1-Flash on **2** B300s"* | [`matrix/fit-matrix.md`](../matrix/fit-matrix.md) §5 states TP2-resident is **255.3 GB against a 241.2 GB budget — "arithmetically impossible"** without mandatory `--engram-config '{"cpu_offload":true}'`; **TP4 is the recommended shape**. Host-resident Engram tables are the wrong shape for a prefill-heavy `prompt_logprobs` teacher, which is exactly the role §1.3 assigns it | §1.3, §8.1 Stage 4, Implications item 3 |

### UNVERIFIABLE

| # | Claim | Why |
|---|---|---|
| 14 | §3.3 / OQ 2: *"openpipe.ai now states: 'The OpenPipe platform has migrated to Weights & Biases and CoreWeave …'"* | `openpipe.ai` returns a JS shell whose extracted text is the single word "OpenPipe" — no migration notice, no body copy. This session's **WebSearch budget is exhausted (200/200)**, so no corroborating source could be reached. The quote is carried as an **unverified prior claim**; doc 00 Open Question 5 is downgraded back to **open** |
| 15 | §1.3 TM blog table: SFT-400K at **3.8 × 10²⁰ FLOPs**, SFT-2M at **1.5 × 10²¹** | The blog re-fetched 2026-09-19 gives **8.4 × 10¹⁹ teacher / 8.2 × 10¹⁹ student** FLOPs for the distillation run and did not surface the two SFT FLOP figures. The **AIME'24 column is confirmed exactly** (60 / ~70 / 67.6 at 17,920 GPU-hr / 74.4 at 1,800 GPU-hr), as are "7-10x fewer gradient steps", "50-100x" and "9-30x"; only the FLOPs cells are marked ⚠️. Likewise the *"45 % at 100 % documents"* datapoint did not re-surface (18 %→36 % QA and 85 %→79 %→83 % IF-eval with 41 % QA retained all **confirmed**) |

### CONFIRMED (opened, matched)

- **LoRA Without Regret** (2025-09-29): all-matrices/MLP-and-MoE low-regret regime; *"Attention-only LoRA significantly underperforms MLP-only"*; *"The optimal learning rate for FullFT is lower by a factor of 10"* with ~15× at ~100 steps; *"LoRA is less tolerant of large batch sizes than FullFT"*, gap independent of rank; *"LoRA fully matches … even with ranks as low as 1"*; *"the whole training process only needs to absorb 320,000 bits"* (10,000 problems × 32 samples). ⚠️ The "~3 M LoRA parameters" comparand §1.1 prints is **not** on that page and is now marked inline [[src](https://thinkingmachines.ai/blog/lora/)]
- **Tinker LoRA primer**: *"LoRA will give good results as long as the number of LoRA parameters is at least as large as the number of completion tokens"*; default rank **32**; *"typically about 10x larger"* LR; both the SFT-parity and the RL-parity sentences [[src](https://tinker-docs.thinkingmachines.ai/tinker/lora-primer/)]
- **Tinker models page**, the rows §6.5 depends on: **Qwen3.8-27B 64 K $1.86 / $5.595 / $4.103**, labelled *"Dense"* + *"Hybrid + Vision"*; 256 K $2.48 / $7.46 / $7.46; Qwen3.5-4B, -9B and -397B-A17B as printed; GPT-OSS-120B, DeepSeek-V3.1, GLM-5.3 as printed; **80 % cached-prefill discount**; **Marlin-2B is absent**, so §3.6's "train it ourselves" conclusion holds [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]
- **Tinker distillation recipe**: ~65 % AIME'24 from SFT (rank-128 LoRA, 3,000 steps) vs ~76.7 % from on-policy distillation (rank-128 LoRA, 200 steps, 16 K rollouts); `teacher_model` argument present; no tokenizer-compatibility statement. **New fact added to §1.3**: student `Qwen3.5-9B-Base`, teacher `Qwen3.5-9B` — a **same-size post-trained teacher**, which strengthens §1.10's self-distillation-as-repair argument and weakens any reading of that row as a large-teacher result [[src](https://tinker-docs.thinkingmachines.ai/cookbook/recipes/distillation/)]
- **Baseten pricing**: H100 80 GB **$0.10833/min** ($6.50/hr), B200 180 GB **$0.16633/min** ($9.98/hr), H100 MIG 40 GB $0.0625/min; *"you do not pay for idle time"*; **H200 and B300 absent**. (Also listed, and missing from §3.3: **A100 80 GB $0.06667/min = $4.00/hr**) [[src](https://www.baseten.co/pricing/)]
- **Fireworks**: *"LoRA rank must be a power of 2 up to 32"*, default 8; min 3 / max 3 M examples; dedicated-deployment-only serving; SFT + DPO + RFT; no per-token price on the page [[src](https://docs.fireworks.ai/fine-tuning/fine-tuning-models)]
- **vLLM `prompt_logprobs`**: *"Number of log probabilities to return per prompt token. When set to -1, return all `vocab_size` log probabilities."* The §1.3 architecture conclusion stands on a verified sentence [[src](https://docs.vllm.ai/en/latest/api/vllm/sampling_params.html)]
- **OpenAI chat/completions**: `logprobs` = *"log probabilities of each output token returned in the `content` of `message`"*; `top_logprobs` capped at **20**; **no `echo` parameter**; no prompt-token logprobs. The blocking constraint in §1.3 is real [[src](https://developers.openai.com/api/docs/api-reference/chat/create)]
- **NeMo Framework performance summary**, container **26.08.01**: all nine rows in §6.1 match exactly (6,288/1,636; 4,912/1,277; 9,184/834; 7,968/725; 44,544/1,023; 8,832/1,306; 34,816/973; 10,240/871; 3,744/1,348), and **no SFT or LoRA throughput table exists on that page** — Open Question 8 is correctly stated [[src](https://docs.nvidia.com/nemo-framework/user-guide/latest/performance/performance-summary.html)]
- **Orca**: 13 B student, **>100 %** over Vicuna-13B on BBH, **42 %** on AGIEval, *"reaches parity with ChatGPT on the BBH benchmark"* [[src](https://arxiv.org/abs/2306.02707)]
- **RFT / scaling relationship**: LLaMA-7B **35.9 % SFT → 49.3 %**, and the 49.3 % is explicitly *"rejection samples from multiple models"*, not single-model RFT — §8.1 Stage 3 analogises a single-model loop to a multi-model number, so the analogy is optimistic and is now the weaker half of that row; *"pre-training loss is a better indicator … than the model's parameter count"* verbatim [[src](https://arxiv.org/abs/2308.01825)]
- **SimPO**: up to **6.4** points over DPO on AlpacaEval 2, **7.5** on Arena-Hard, Gemma-2-9B-it **72.4 %** LC win rate, NeurIPS 2024 [[src](https://arxiv.org/abs/2405.14734)]
- **DeepSeekMath / GRPO**: *"a variant of Proximal Policy Optimization (PPO), that enhances mathematical reasoning abilities while concurrently optimizing the memory usage of PPO"*; **51.7 %** MATH, **60.9 %** with self-consistency over 64 samples [[src](https://arxiv.org/abs/2402.03300)]
- **Spurious Rewards**: **21.4** points from random rewards vs **29.1** from ground truth on MATH-500, on **Qwen2.5-Math-7B** (§5.5 says only "Qwen2.5-Math"); 65 % → >90 % code reasoning; fails on Llama3 / OLMo2; the cross-family validation recommendation verbatim [[src](https://arxiv.org/abs/2506.10947)]
- **Let's Verify Step by Step**: *"process supervision significantly outperforms outcome supervision"*; **78 %** of a representative MATH test subset; **PRM800K = 800,000 step-level human feedback labels** [[src](https://arxiv.org/abs/2305.20050)]
- **GEPA**: *"outperforms GRPO by 6% on average and by up to 20%, while using up to 35x fewer rollouts"*; >10 points over MIPROv2 [[src](https://arxiv.org/abs/2507.19457)]
- **LLaVA-Video-178K**: 178 K videos → **1.3 M** samples (**178 K captions / 960 K open QA / 196 K MCQ**) at **1 FPS**, annotated by **GPT-4o**; LLaVA-Hound **0.008** fps, ShareGPT4Video **0.15** fps; LLaVA-Video-7B **63.3 / 69.7**, 72 B **70.5 / 76.9** on Video-MME. 1.3 M ÷ 178,510 = **7.28** samples/video, so §2.4's "~7.3" is right [[src](https://arxiv.org/html/2410.02713v3)]
- **Reducing Activation Recomputation**: overhead cut *"by over 90%"*, **5×** less activation memory, 530 B at **54.2 %** MFU vs **42.1 %**, on **2,240** A100s [[src](https://arxiv.org/abs/2205.05198)]
- **§6.3 cost table** — every cell recomputed and **exact**: 6 × 27.78e9 × 1e9 = 1.6668e20 FLOPs ÷ 800e12 = 57.88 GPU-hr → $428 / $868 / $460 and $0.428/1M; LoRA 38.58 hr → $286 / $579 / $306; DPO-LoRA 115.75 hr → $857 / $1,736 / $919; Marlin-2B 4.60 and 3.07 hr → $34 / $23. Wall clock 57.88 ÷ 8 = **7.24 h**. Memory table: 27.78 × {18, 8, 2, 0.5} = 500.0 / 222.2 / 55.6 / 13.9 GB; 2.21 × same = 39.8 / 17.7 / 4.4 / 1.1 GB. §6.1's planning constant: 800 ÷ 2,250 = **35.6 %**, 800 ÷ 4,500 = **17.8 %**
- **§6.4 and §7.1 derivations**: 44,544 ÷ 1,910 = **23.3×**; 44,544 ÷ 37.5 = **1,188×** (three orders of magnitude, as claimed); 17,920 × $7.40 = **$132,608**; 1,800 × $7.40 = **$13,320**; 0.5 B-token LoRA run = 19.29 GPU-hr = **$142.8**, sweep 5–20× = **$714–$2,856**
- **§8.1 worked example** — all lines exact: 6 M in @ $4 = $24, 0.8 M out @ $20 = $16, batch 50 % → **$20**; 13.6 M training tokens = 0.525 GPU-hr = **$3.89**; round 2: 180 M/24 M → **$360 / $240 = $600**; 408 M tokens → 15.74 GPU-hr = 1.97 h = **$116** (`low`) / $236 (`high`), full-FT 23.6 hr = 2.95 h = **$175 / $354**; incumbent 500,000 × (3,000 × $4 + 400 × $20) ÷ 1e6 = **$10,000/mo**; 1.7 B tokens × $0.38–$1.66/1M = **$646–$2,822**, midpoint **$1,734**
- **§8.2 worked example** — exact: 20,000 × 23,560 = **471.2 M** tokens (× $10/1M = $4,712); 20,000 × 7.3 ≈ **146 K** samples; 146 K × 23,560 = **3.44 B**/epoch; batched 20,000 × 23,560 × 3 = **1.414 B**; 1.414 × 4.60 = **6.5 GPU-hr** = $48; 2× VLM penalty **13 GPU-hr** = $96 / $195; the packing ratio 3.44 ÷ 0.471 = **7.3×**
- **Repo cross-references, opened**: [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) §3 B300 = **$7.40 (Hyperstack) / $15.00 (OCI `BM.GPU.B300.8`) / $7.94 (DigitalOcean 12-mo)** ✓; [`METHODOLOGY.md`](../METHODOLOGY.md) §8 B300 268 GB, 2,144 GB/node, 2,250 / 4,500 dense, GB300 2,500 / 5,000 / 15,000, Qwen3.8-27B 27.78 B / 55.56 GB BF16, Marlin-2B 2.21 B unique / 5.444 GB / 2 fps ≤ 240 frames / 200,704 px, Kimi-K3 2,779.9 B / 104.19 B active / 195 GB per GPU on 8×B300 ✓; [`matrix/fit-matrix.md`](../matrix/fit-matrix.md) Kimi-K3 **min 8 rec 8** on B300 ✓ (DeepSeek-V4.1-Flash corrected, row 13 above); doc 00 §2.1 **GPT-5.6 Sol $4.00 / $0.40 cached / $20.00** ✓, §4.4 honest incumbent baseline **$0.38–$1.66 blended** ✓, §5.3 **1,752 paired examples at p = 0.85, 3 pp** ✓

### Not re-checked (budget), flagged for the next pass

DoReMi (+6.5 pts, 2.6× fewer steps), RegMix (512 × 1 M proxies, 10 % of DoReMi compute),
LIMA (43 / 58 / 65 %), AlpaGasus (9 K of 52 K, 5.7×, 80 → 14 min), s1 (27 %, 50 → 57 %),
LIMO (63.3 / 95.6 %), ORPO (12.20 / 7.32 / 66.19), KTO (1 B–30 B), DAPO (50 AIME'24),
GSPO, Tülu 3, xLAM (1 B–176 B, BFCL), QLoRA (65 B on 48 GB, Guanaco 99.3 %), DoRA,
TIES-merging, Gerstgrasser accumulate-vs-replace, Ibrahim replay, Luo forgetting,
sequence packing (50 % / 89 % / 2×), Megatron (1 T at 502 PF/s on 3,072 GPUs, 52 %),
AlphaEvolve (48-multiplication 4×4, 56 years), AI Scientist (<$15/paper), GSM1k (8 %,
Spearman r² = 0.36 — note the published figure is usually quoted as r² = 0.32, ⚠️ worth
checking), RewardBench, Gao et al. RM overoptimisation, RLAIF, Qwen PRM lessons,
Anthropic Messages (no logprobs), Gemini `generateContent`, and every framework README
in §3.1 (Axolotl, verl, SkyRL, Oumi, NeMo RL, OpenRLHF, TRL, LLaMA-Factory, Unsloth,
torchtune, NVIDIA Data Flywheel). **This session's WebSearch budget was exhausted
(200/200) before the check began**, so no claim could be settled by search — only by
fetching a URL already named in the document.
