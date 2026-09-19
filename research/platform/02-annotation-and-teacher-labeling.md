# Data annotation with larger teacher models, RLHF signals and human review

Research date **2026-09-19**. This document owns stage **S3 (Annotation)** of the
loop defined in
[`00-goal-and-problem-statement.md` §1.2](00-goal-and-problem-statement.md): the
transform from sampled traces (S2) into labelled examples that S4 turns into
versioned datasets and evals, and that S5 turns into checkpoints.

**Numbering note.** Doc 00 §6's component map calls this component "doc 03"
(*Data capture, analysis, annotation*) and reserves "doc 02" for observability and
traces. The file numbering in `research/platform/` puts annotation at **02**. They
are the same component; where doc 00 says "doc 03 owns X", read "this document".
Trace capture, sampling and PII-at-rest belong to the observability document, not
here. This document begins **after** a trace has been selected and redacted, and
ends when a labelled, versioned record is handed to S4.

**Conventions.** Markers, cost formulas and the `low`/`high`/`res1y` price tiers
are [`research/METHODOLOGY.md`](../METHODOLOGY.md). Self-hosting $/1M figures are
named cells from [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md).
Incumbent/teacher API prices, the blended-cost definition, the distillation
evidence base and the judge-validity crux are doc 00 §2.1, §3.1, §4.1 and §5.1 and
are **linked, not restated**.

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source fetched 2026-09-19 (paper, official doc, vendor page, repo). |
| **⚠️ TO BE VERIFIED** | No primary source found, or the claim is an inference; reasoning stated inline. |
| `est.` | Arithmetic from sourced inputs; shown, not measured. |
| `meas.` | A published measurement, cited. |

> **Research-method caveat, stated up front.** The session's **web-search budget
> was exhausted before this agent started** (200/200 `WebSearch` calls consumed by
> earlier agents in the programme). Every source below was reached by *known URL*
> — direct `WebFetch`, `curl` against arXiv/HuggingFace/GitHub raw endpoints, and
> in one case the Wayback Machine. Consequences, which the reader must hold:
> 1. **The annotation-tooling survey in §4.3 and §7.6 is a survey of vendors I
>    could name and fetch, not a market scan.** New entrants since May 2026 are
>    missing, not disproven.
> 2. **§2.6 could not find human-agreement numbers for 2026-generation judges**
>    (GPT-5.6/GPT-6 Astra, Opus 5, Fable 5.1). The anchors are 2023–2025-era
>    results. This is flagged inline and in Open Questions, and it is a real gap
>    because the platform's whole eval rests on it.
> 3. Where an arXiv ID I tried returned a *different* paper than intended, the
>    claim was **dropped rather than approximated** — see the Verification note at
>    the end of §4.2.

**One thing this document settles that doc 00 could not.** Doc 00's Open Question
#2 — the verbatim OpenAI clause on using Output to train competing models — is
**resolved in §5.1**. The pages still return HTTP 403 to `WebFetch` and to `curl`
with a browser UA, but a 2026-09-12 Wayback snapshot of the *OpenAI Services
Agreement* (effective 2026-01-01) is readable, and the clause plus its
**"Permitted Exception"** definition are quoted below. The exception is narrower
than the optimistic reading, and it changes the teacher-choice decision.

---

## 1. Annotation types the loop needs

### 1.1 The catalogue

Eight label types carry the loop. They are not interchangeable: each feeds a
different training rung on doc 00 §3.2's ladder, each has a different cost class,
and each fails differently. The platform should think in terms of *a trace row
acquiring labels over time*, not "an annotation job".

| # | Type | Concretely | Produced by | Feeds | Cost class (§6) | Primary failure mode |
|---|---|---|---|---|---|---|
| **T1** | **Gold output** (teacher rewrite) | The teacher's own completion for the *same* prompt stack, same tools, same schema | Teacher API or self-hosted open-weight teacher | Off-policy SFT (Orca pattern, doc 00 §3.2 rung 3) | 1 teacher call/example | Teacher errors become ground truth silently; student inherits teacher's style *and* its mistakes |
| **T2** | **Rubric score** | Per-criterion grade (0/1 or Likert) against a written, versioned rubric, with a rationale | Judge model, optionally panel | Rejection-sampling filter (rung 2); reward signal for RaR-style RL; eval gate (S7) | 1–3 judge calls/example | Rubric drifts; judge lenient (§2.4); criteria not independent |
| **T3** | **Pairwise preference** | (prompt, response A, response B, winner, margin) | Judge model in *both* orders; or human | DPO / RLAIF (rung 4) | 2–6 judge calls/example | Position + verbosity bias (§2.4); ties mishandled; self-preference when judge = teacher |
| **T4** | **Rationale / critique** | Chain-of-thought, explanation trace, or a critique+revision pair | Teacher | Orca-style explanation-trace SFT; Constitutional-AI critique–revise (§3.5) | 1 teacher call, longer output | Rationale is post-hoc confabulation uncorrelated with the answer; trains verbosity |
| **T5** | **Structured label** | Intent, task type, difficulty tier, language, safety category, slice tag, tool-required flag | Small classifier or cheap judge; sometimes deterministic from the trace | Slicing (doc 00 §5.2), stratified sampling, routing, curriculum | Fractions of a cent | Label taxonomy invented before the traffic is understood; unstable across versions |
| **T6** | **Tool-call trace label** | Correct tool name, correct arguments, correct call *order*, whether the call was necessary | Verifier (schema + replay) where possible; teacher where not | Tool-use SFT; per-tool eval slices (doc 00 §3.3) | Near-zero if verifiable | Verifiable-looking but environment-dependent; a flaky tool makes the label wrong |
| **T7** | **Outcome / implicit signal** | Ticket resolved, code merged, user retried, user edited the output, thumbs-up, conversion | The customer's product, joined to the trace | The best available target — beats every judge (doc 00 §5.1) | ~$0 marginal | Sparse, delayed, confounded; absent in most features |
| **T8** | **Video/temporal label** | Caption, event boundary (start/end), temporal ordering, frame-referenced answer, per-clip QA pair | Teacher VLM; human for gold | Video SFT; video eval (doc 00 §5.5) | Dominated by video-token count (§8) | Temporal confusion at low effective fps; ambiguous ground truth |

**The ordering that matters.** T7 first (it is free and it is the only label type
that is not a proxy), then T5 (cheap, and it is what makes every other label
*sliceable*), then T2, then T1/T3, then T4, then T6, then T8. A platform that
starts at T1 has bought a distillation project; a platform that starts at T7+T5
has bought an understanding of the customer's traffic, which is what the first
conversation with the buyer needs.

### 1.2 Which label type each training rung actually consumes

Mapping doc 00 §3.2's ladder onto the catalogue, because "we need annotation" is
not a requirement:

| Rung (doc 00 §3.2) | Needs | Does **not** need | Note |
|---|---|---|---|
| 1. Prompt + model swap | T5 (slices), T2 or T7 (to measure) | T1, T3, T4 | Zero training. Often ends the engagement, profitably |
| 2. Rejection-sampling SFT | T1 ×k samples + T2 or T6 as the filter | T3, T4 | The **cheapest rung that produces a model**; works with any teacher, needs no logprobs |
| 3. Off-policy SFT on teacher traces | T1, optionally T4 | T2 (though filtering helps a lot) | The Orca pattern [[src](https://arxiv.org/abs/2306.02707)]; Orca 2 argues the student should be taught *strategy selection*, not pure imitation [[src](https://arxiv.org/abs/2311.11045)] |
| 4. DPO / preference distillation | T3 | T1 (one side can be the student's own output) | Zephyr showed dDPO on AI preferences beats a 70B RLHF model on MT-Bench (doc 00 §3.1) |
| 5. On-policy distillation | Teacher **per-token logprobs on the student's own rollouts** | T1, T3 | **Blocked on most APIs — see §3.2.** This is the finding that reshapes the annotation plan |
| 6. RL against a verifiable reward | T6/T7 or a programmatic verifier | Everything else | Where a verifier exists, use it; judges are a fallback, not a preference |

**Decision rule.** Produce the label types that the *rung you can actually reach*
consumes, and no others. The commonest waste in this space is generating T4
rationales for a pipeline that is going to run rung 2.

### 1.3 What production traffic gives you for free

Before paying any teacher, harvest what S2 already holds. These are T7/T5 labels
obtainable with SQL, not inference:

| Free signal | How derived | What it labels |
|---|---|---|
| **Retry / regenerate** | Same user, near-identical prompt within N seconds | Negative on the first response |
| **User edit** | Downstream app writes back an edited version | A gold output (T1) authored by the *user*, which is better than a teacher's |
| **Abandonment** | Session ends without the downstream action | Weak negative |
| **Downstream success** | Join to the customer's business event | T7, the real thing |
| **Schema/parse failure** | Structured output did not validate | Hard negative, and a contract-fidelity bug (doc 00 I4) |
| **Tool error** | Tool returned an error for the model's arguments | T6 negative, verifiable |
| **Latency / length outliers** | p99 output length, truncation at `max_tokens` | Slice tag (T5) and a likely failure cluster |
| **Prompt-stack hash** | Hash of system prompt + tool schemas | Version boundary; doc 00 §2.2's invalidation trigger |

This is the platform's version of doc 00 §7.2's "mine the disagreements, not the
average", and it is the only annotation source with *zero* marginal cost and *no*
teacher ToS exposure (§5). Build it first.

### 1.4 The disagreement-mining primitive

Doc 00 §7.2 names disagreement mining as the highest-value data. Concretely, four
disagreement sources, in descending value per dollar:

1. **Student vs incumbent** on the same shadowed request (doc 00 §5.4's shadow
   mode). Free to produce once shadowing exists; directly demoable to the buyer.
2. **Judge vs judge** within a panel (§2.7). Free once a panel runs; flags the
   examples where the rubric itself is underspecified.
3. **Judge vs human** on the calibration set (§2.5). Expensive but it is the
   thing that validates the judge.
4. **Teacher vs student per-token divergence**, where logprobs are available
   (§3.2). Dense and diagnostic, but see the availability finding.

Each produces a queue. The platform's annotation UI is, in effect, four queues and
a router (§7.4).

### 1.5 Video additions

T8 decomposes further, and the decomposition matters because the cost of each is
wildly different (§8):

- **Free-form caption** — one teacher call, output-token dominated.
- **Closed QA pair** — teacher writes both question and answer; cheap to grade
  later, which is its point.
- **Temporal grounding** — (event description → start/end timestamps). The
  hardest and the one small VLMs fail at; VTimeLLM's premise is exactly that
  general video LLMs "can only provide a coarse description of the entire video,
  failing to capture the precise start and end time boundary of specific events"
  [[src](https://arxiv.org/abs/2311.18445)].
- **Fine-grained temporal attribute** — action frequency, motion magnitude, event
  order. TemporalBench built ~10K QA pairs from ~2K human annotations for exactly
  this and found **GPT-4o at 38.5 %** against a ~30-point human gap
  [[src](https://arxiv.org/abs/2410.10818)].

⚠️ **TO BE VERIFIED** — no 2026-dated equivalent of the TemporalBench frontier
number was obtainable this session, so the "~30-point human gap" should be treated
as a 2024 measurement, not a current one.

---

## 2. LLM-as-judge and LLM-as-annotator

### 2.1 Two different jobs, routinely conflated

| | **LLM-as-annotator** | **LLM-as-judge** |
|---|---|---|
| Output | A label that becomes a *training target* | A score that becomes a *decision* |
| Error mode | Bias propagates into the student's weights | Bias propagates into a promotion decision |
| Can it be wrong and still useful? | Yes — noisy labels average out over 100k examples | **No** — a biased judge is a systematically wrong gate |
| Validation requirement | Aggregate quality filters | Per-slice agreement against humans, with a stated floor (doc 00 MVP criterion 4) |
| Who may be the teacher? | The teacher, by definition | **Never the teacher** (doc 00 §5.1) |

The platform must keep these as separate roles with separate model configs and
separate audit records, because the rule "judge ≠ teacher" is unenforceable if
they are the same code path.

### 2.2 Rubric design

The evidence says rubrics beat bare preference, and that instance-specific rubrics
beat generic ones.

- **HealthBench** is the strongest published demonstration of rubric grading at
  scale: 5,000 multi-turn conversations graded against **conversation-specific
  rubrics created by 262 physicians**, with **48,562 unique rubric criteria**
  spanning accuracy, instruction-following and communication
  [[src](https://arxiv.org/abs/2505.08775)]. Two things to steal: rubrics are
  *per-example*, not per-task; and criteria are split across behavioural
  dimensions so a model can fail communication while passing accuracy.
- **Rubrics as Rewards (RaR)** shows rubric feedback works as an RL *reward*, not
  only as an eval: "relative improvements of up to 31 % on HealthBench and 7 % on
  GPQA-Diamond over popular LLM-as-judge baselines that rely on direct
  Likert-based rewards", and — directly relevant to a platform that wants cheap
  judges — "using rubrics as structured reward signals yields better alignment for
  smaller judges and reduces performance variance across judge scales"
  [[src](https://arxiv.org/abs/2507.17746)].
- **Reference-guided grading** is the mechanism that makes a *small* judge work.
  Prometheus's own conclusion is that it performs "when the appropriate reference
  materials (reference answer, score rubric) are accompanied"
  [[src](https://arxiv.org/abs/2310.08491)], and JudgeLM's bag of techniques is
  literally "swap augmentation, reference support, and reference drop"
  [[src](https://arxiv.org/abs/2310.17631)].

**Concrete rubric spec for the platform** (mechanism, inputs/outputs, failure
modes as the brief requires):

```
rubric_version: str          # semver; any change invalidates historical scores
task_id: str
criteria: [
  { id: "c1",
    text: "The response cites at least one retrieved document by id",
    type: "binary" | "likert5",
    weight: float,
    verifiable_by: "regex" | "schema" | "judge",   # prefer the first two
    applies_when: <SQL-ish predicate over the trace>  # instance-specific gating
  }, ...
]
aggregation: "weighted_sum" | "all_must_pass" | "gated"   # safety uses all_must_pass
reference_answer: optional[str]   # from T1 or from a human gold
```

Rules that fall out of the sources:
1. **Any criterion that can be checked programmatically must be**
   (`verifiable_by != "judge"`). Doc 00 §5.4 already notes the industry answer to
   agentic eval is verifiable environments, not judges.
2. **Criteria are graded independently**, in separate calls or separate structured
   fields, never as one holistic score — that is what HealthBench's 48,562
   criteria over 5,000 conversations implies structurally.
3. **Safety criteria aggregate with `all_must_pass`** and never enter the weighted
   sum (doc 00 invariant I5).
4. **`rubric_version` is part of every score's identity** (§7.3). A rubric edit is
   a dataset-invalidating event.

### 2.3 The lineage, and what each step actually contributed

| Work | Date | Contribution the platform can use | Headline number |
|---|---|---|---|
| **Large Language Models are not Fair Evaluators** [[src](https://arxiv.org/abs/2305.17926)] | May 2023 | Position bias is *exploitable*, and the fix is Balanced Position Calibration + Multiple Evidence Calibration + human-in-the-loop on high-entropy items | "Vicuna-13B could beat ChatGPT on 66 over 80 tested queries with ChatGPT as an evaluator" purely by reordering |
| **G-Eval** [[src](https://arxiv.org/abs/2303.16634)] | Mar 2023 | CoT + form-filling + probability-weighted scoring as the judge *prompt pattern* | Spearman **0.514** with humans on summarisation; also the first clear statement that LLM evaluators "favor texts generated by language models themselves" |
| **MT-Bench / Chatbot Arena** [[src](https://arxiv.org/abs/2306.05685)] | Jun 2023 | The canonical agreement claim and the canonical bias list | GPT-4 judge **>80 %** agreement with humans, "the same level of agreement between humans"; 30,000 conversations + 3,000 expert votes released |
| **JudgeLM** [[src](https://arxiv.org/abs/2310.17631)] | Oct 2023 | Fine-tuned open judges at 7B/13B/33B; **swap augmentation, reference support, reference drop**; names position, *knowledge* and *format* bias | ">90 %" agreement with the teacher judge; JudgeLM-7B judges **5K samples in 3 minutes on 8×A100** |
| **Prometheus** [[src](https://arxiv.org/abs/2310.08491)] | Oct 2023 | A 13B open judge trained on the Feedback Collection (1,000 rubrics / 20k instructions / 100k GPT-4 responses+feedback) | Pearson **0.897** with humans across 45 custom rubrics, vs GPT-4's **0.882** and ChatGPT's **0.392** |
| **Prometheus 2** [[src](https://arxiv.org/abs/2405.01535)] | May 2024 | One open model doing *both* direct assessment and pairwise ranking, with user-defined criteria | "highest correlation and agreement with humans and proprietary LM judges among all tested open evaluator LMs" on 4+4 benchmarks |
| **Replacing Judges with Juries (PoLL)** [[src](https://arxiv.org/abs/2404.18796)] | Apr 2024 | **A panel of smaller disjoint-family judges beats one big judge**, with less intra-model bias | "over seven times less expensive" than a single large judge, across 3 judge settings and 6 datasets |
| **Length-Controlled AlpacaEval** [[src](https://arxiv.org/abs/2404.04475)] | Apr 2024 | A *regression* debias: fit a GLM on the confounder (length difference) and predict at zero difference | Spearman with Chatbot Arena **0.94 → 0.98** |
| **LLM Evaluators Recognize and Favor Their Own Generations** [[src](https://arxiv.org/abs/2404.13076)] | Apr 2024 | Self-preference is *caused* by self-recognition — a "linear correlation between self-recognition capability and the strength of self-preference bias" | Makes "judge ≠ teacher" a mechanism-backed rule, not a hygiene preference |
| **Self-Taught Evaluators** [[src](https://arxiv.org/abs/2408.02666)] | Aug 2024 | An open judge trained with **no human preference labels**, iteratively on synthetic contrasts | Llama3-70B-Instruct **75.4 → 88.3** on RewardBench (88.7 with majority vote), "outperforms commonly used LLM judges such as GPT-4" |
| **Judging the Judges** [[src](https://arxiv.org/abs/2406.12624)] | Jun 2024 | The sobering control: 13 judges × 9 exam-takers *in a clean high-inter-human-agreement setting* | "only the best (and largest) models achieve reasonable alignment with humans… their assigned scores may still differ with up to 5 points from human-assigned scores"; judges are sensitive to prompt complexity and length and **tend toward leniency**; "judges with high percent agreement can still assign vastly different scores" |
| **A Survey on LLM-as-a-Judge** [[src](https://arxiv.org/abs/2411.15594)] (v6, Oct 2025) | Nov 2024– | The consolidated reliability framing | Survey |

**The synthesis for the platform.** Everything needed to build a *defensible*
judge exists and is public: rubrics with references (Prometheus), swap
augmentation (JudgeLM), length control (LC-AlpacaEval), panels of disjoint
families (PoLL), and a non-self judge (self-preference paper). What does **not**
exist publicly is a validated judge for *this customer's* task, which is why §2.5
is a budgeted stage rather than a config value.

### 2.4 Known biases, and the concrete fix for each

| Bias | Evidence | Mechanism of the fix | Cost of the fix | Residual risk |
|---|---|---|---|---|
| **Position** | Reordering flipped 66/80 queries [[src](https://arxiv.org/abs/2305.17926)] | Run both orders, average; treat a disagreement between orders as a *tie*, and route ties to the disagreement queue | **2× judge calls** | None material. Not doing this is negligence (doc 00 §5.1) |
| **Verbosity / length** | Named in MT-Bench [[src](https://arxiv.org/abs/2306.05685)]; LC-AlpacaEval quantifies the correction as 0.94→0.98 Spearman [[src](https://arxiv.org/abs/2404.04475)] | Fit a GLM with length-difference as a mediator, report the length-controlled preference; *and* separately report raw | ~0 (a regression on data you already have) | A distilled student trained on a verbose teacher is *genuinely* more verbose; length control removes the judge's bias, not the product problem |
| **Self-preference** | Caused by self-recognition [[src](https://arxiv.org/abs/2404.13076)] | Judge family ≠ teacher family, enforced in the artifact schema; report both directions (doc 00 §5.1) | Possibly a second judge | If both teacher and judge are, say, OpenAI models of different sizes, the shared pretraining makes "different family" a weaker guarantee than it sounds. ⚠️ |
| **Leniency** | "a tendency toward leniency" [[src](https://arxiv.org/abs/2406.12624)] | Calibrate the *threshold*, not the score: choose the pass cut-off on the human gold set, per slice | Comes free with §2.5 | Leniency drifts with judge model version |
| **Prompt-complexity / length sensitivity** | Same source | Hold judge-prompt length constant; truncate references deterministically | Low | Long enterprise prompt stacks (doc 00 §2.1) are exactly the hard case |
| **Knowledge bias, format bias** | Named by JudgeLM [[src](https://arxiv.org/abs/2310.17631)] | Reference support + reference drop augmentation when training/prompting the judge | Low | — |
| **Intra-model bias across the panel** | PoLL [[src](https://arxiv.org/abs/2404.18796)] | Panel members from **disjoint model families** | Panel cost (§6) | Families increasingly share data and techniques ⚠️ |
| **High percent-agreement masking score divergence** | [[src](https://arxiv.org/abs/2406.12624)] | Report κ/α *and* mean absolute score difference, never percent agreement alone | ~0 | — |

### 2.5 Calibrating against humans: the metrics, and the trap

**The required stage.** Doc 00's MVP criterion 4 sets the bar: judge-vs-human
agreement ≥ 80 % on a ≥200-example gold set, judge ≠ teacher, measured with
Cohen's κ and per-slice accuracy. This section says how, and where the metric
misleads.

**Cohen's κ** for two raters: `κ = (p_o − p_e) / (1 − p_e)`, where `p_o` is
observed agreement and `p_e` is agreement expected by chance from the marginals.
**Krippendorff's α** generalises to >2 raters, missing data and ordinal/interval
scales: `α = 1 − D_o/D_e`, observed vs expected disagreement — which is the right
choice for rubric Likert scores and for a panel with partial coverage.

**The trap, and it will bite this platform.** κ collapses when one class dominates
— the *kappa paradox*. Worked example (`est.`, standard 2×2 arithmetic):

| | Human = pass | Human = fail |
|---|---:|---:|
| Judge = pass | 940 | 40 |
| Judge = fail | 10 | 10 |

`p_o = 0.95`. Marginals: judge-pass 0.98, human-pass 0.95, so
`p_e = 0.98×0.95 + 0.02×0.05 = 0.932`. `κ = (0.95 − 0.932)/(1 − 0.932) = **0.265**`
— "fair" agreement from a judge that agrees 95 % of the time, because almost
everything passes. **Safety labels, schema-validity labels and any rare-failure
slice live in exactly this regime**, which is where the platform most needs the
judge to be trustworthy.

Consequences the platform must implement, not merely note:

1. **Report per-class recall, not only agreement.** In the table above the judge
   catches 10 of 50 human-fails: recall 0.20. That is the number a customer cares
   about and κ buries it.
2. **Stratify the gold set by label, not by traffic.** Oversample the rare class
   so the calibration set has enough negatives to estimate recall at all. 200
   examples drawn i.i.d. from 5 %-failure traffic contain ~10 failures and
   estimate recall with a CI of roughly ±30 points (`est.`, binomial).
3. **Prefer a prevalence-robust statistic alongside κ** for rare-class slices
   (Gwet's AC1 or a bootstrap CI on recall). ⚠️ **TO BE VERIFIED** — I did not
   fetch a primary source for AC1 in this session; it is named here as standard
   practice and should be sourced before it appears in a customer-facing metric
   definition.
4. **Re-validate on every judge model-version change**, and treat the judge model
   id + prompt hash as part of the score's identity (§7.3). Doc 00 §8.6's three
   vendor sunsets in twelve months are the reason this is not paranoia.

**Sizing the gold set.** Doc 00 §5.3 gives the sample-size arithmetic for the
*product* claim. The *judge-calibration* set is a different and smaller problem:
you are estimating one agreement proportion per slice. For a ±5 pp CI at 95 % on a
proportion near 0.85, `n ≈ 1.96² × 0.85 × 0.15 / 0.05² ≈ **196**` (`est.`), which
is where doc 00's "≥200" comes from. **Per slice.** Five slices is ~1,000
human-adjudicated examples, and §6.2 prices that.

### 2.6 Published human-agreement numbers, as of 2026-09-19

| Judge | Setting | Agreement with humans | Source & date |
|---|---|---|---|
| GPT-4 | MT-Bench / Chatbot Arena pairwise | **>80 %**, stated as "the same level of agreement between humans" | [arXiv 2306.05685](https://arxiv.org/abs/2306.05685), Jun 2023 |
| GPT-4 + G-Eval | SummEval, summarisation | Spearman **0.514** | [arXiv 2303.16634](https://arxiv.org/abs/2303.16634), Mar 2023 |
| GPT-4 | 45 custom rubrics, direct assessment | Pearson **0.882** with humans | [arXiv 2310.08491](https://arxiv.org/abs/2310.08491), Oct 2023 |
| Prometheus-13B | same 45 rubrics | Pearson **0.897** (above GPT-4) | [ibid.](https://arxiv.org/abs/2310.08491) |
| ChatGPT | same 45 rubrics | Pearson **0.392** | [ibid.](https://arxiv.org/abs/2310.08491) |
| ChatGPT (zero-shot) | tweet relevance/stance/topic/frames, 2,382 tweets | Beat crowd workers on 4 of 5 tasks; **intercoder agreement exceeded both crowd workers and trained annotators on all tasks**; **<$0.003/annotation, ~20× cheaper than MTurk** | [arXiv 2303.15056](https://arxiv.org/abs/2303.15056), Mar 2023 |
| JudgeLM-7B/13B/33B | PandaLM + own benchmark | ">90 %" agreement with the *teacher judge* — note: not with humans | [arXiv 2310.17631](https://arxiv.org/abs/2310.17631), Oct 2023 |
| Llama3-70B-Instruct, self-taught | RewardBench | **75.4 → 88.3** (88.7 majority vote) | [arXiv 2408.02666](https://arxiv.org/abs/2408.02666), Aug 2024 |
| 13 judges incl. the largest | clean, high-inter-human-agreement exam setting | Best judges "still quite far behind inter-human agreement"; scores differ by **up to 5 points** | [arXiv 2406.12624](https://arxiv.org/abs/2406.12624), Jun 2024 (v6 Aug 2025) |

⚠️ **TO BE VERIFIED — and this is the most commercially important gap in this
document.** I found **no published human-agreement measurement for any
2026-generation judge** (GPT-6 Astra, GPT-5.6 Sol/Terra/Luna, Claude Opus 5, Fable
5.1, Gemini 3.8 Flash) in this session. The reason is procedural, not evidential:
web search was unavailable, and none of the vendor pricing/docs pages fetched
carries a judge-agreement figure. **Do not assume the >80 % figure transfers.** Two
arguments that it may *not*, both of which the platform should test rather than
assume:

- Judges have got better *and* the outputs they grade have got better, so the
  discriminating cases are harder. "Judging the Judges" found the gap persisted
  even in a *clean* setting [[src](https://arxiv.org/abs/2406.12624)].
- Agreement is task-conditional. The >80 % is on open-ended chat preference. An
  enterprise task with a 15k-token policy prompt and a strict output schema is a
  different measurement, and it is the only one that matters for the customer.

**Therefore: the platform must measure judge-human agreement per customer, per
task, per slice, and put the number on the dashboard next to every delta** (doc 00
§5.1). Treat the literature as evidence that the method *can* work, never as a
substitute for the measurement.

### 2.7 Ensembling, panels, and cascades

Three cost/quality structures, and when each is right:

| Structure | Mechanism | Evidence | When |
|---|---|---|---|
| **Both-orders averaging** | Same judge, A/B and B/A, average; disagreement → tie | [2305.17926](https://arxiv.org/abs/2305.17926) | **Always**, for every pairwise call |
| **Panel of LLMs (PoLL)** | 3+ judges from *disjoint families*, majority or mean | "over seven times less expensive" than one large judge, less intra-model bias [[src](https://arxiv.org/abs/2404.18796)] | Default for eval-gate scoring; also gives you a free disagreement queue (§1.4) |
| **Self-consistency / majority vote** | Same judge, k samples, vote | Self-Taught Evaluator: 88.3 → **88.7** with majority vote [[src](https://arxiv.org/abs/2408.02666)] | Marginal; buy a panel instead of k samples of one judge |
| **Judge cascade** | Cheap judge first; escalate only low-confidence items to an expensive judge, then to a human | FrugalGPT's LLM-cascade "can match the performance of the best individual LLM (e.g. GPT-4) with up to 98 % cost reduction" [[src](https://arxiv.org/abs/2305.05176)] | Annotation at 100k+ scale, where the expensive judge on everything is not affordable |

**The cascade is the structure the platform should ship**, because it is also the
routing mechanism for human review (§4.2): one confidence threshold governs
*escalate to a bigger judge* and a second governs *escalate to a human*. Concrete
mechanism:

```
score, confidence := cheap_panel(example)          # 3 small judges, both orders
if panel_disagrees or confidence < τ1:  score := strong_judge(example)
if still_uncertain or safety_flag or slice ∈ hard_slices: → human queue
```

Inputs: the example plus rubric. Outputs: a score, a confidence, and a *route*.
Failure mode: `τ1` tuned on the same set used to report agreement — which is
self-validation. Tune thresholds on a split of the gold set that is held out from
the agreement report, exactly as doc 00 §8.4 requires for the model.

### 2.8 Decision rules for §2

1. **Judge family ≠ teacher family.** Enforced in the artifact schema, not by
   convention [[src](https://arxiv.org/abs/2404.13076)].
2. **Both orders, always.** A single-order pairwise number is not a number.
3. **Rubric with reference beats bare preference**, and a small judge with a
   reference beats a big judge without one [[src](https://arxiv.org/abs/2310.08491)].
4. **Programmatic before judged.** Any criterion checkable by schema, regex or
   replay is not a judge's job.
5. **Report κ/α *and* per-class recall *and* mean score difference.** Percent
   agreement alone is disqualified by [2406.12624](https://arxiv.org/abs/2406.12624).
6. **A judge-measured delta smaller than the judge's own noise floor is not a
   result** — print the floor next to the delta (doc 00 §5.1).
7. **Panel of small disjoint judges before one big judge** — cheaper and less
   biased [[src](https://arxiv.org/abs/2404.18796)].

---

## 3. Teacher-generated training data

### 3.1 The generation families

| Family | Mechanism | Inputs → outputs | Evidence | Where it fits here |
|---|---|---|---|---|
| **Response distillation** | Teacher answers the customer's real prompts | trace prompts → T1 | Orca (doc 00 §3.1) | The default. Real prompts, so no distribution invention |
| **Rationale distillation** | Teacher answers *and* explains | prompts → T1+T4 | Distilling step-by-step: a **770M T5 beat a 540B PaLM using 80 % of the data**, and outperformed both fine-tuning and standard distillation with far fewer examples [[src](https://arxiv.org/abs/2305.02301)]; Orca's explanation traces [[src](https://arxiv.org/abs/2306.02707)] | Use when the task has reasoning depth; costs more output tokens |
| **Self-Instruct** | Bootstrap new instructions from seeds, filter invalid/similar | seeds → synthetic prompts | "33 % absolute improvement over the original model on Super-NaturalInstructions", within "5 % absolute" of InstructGPT-001 [[src](https://arxiv.org/abs/2212.10560)] | **Only** for coverage of prompt regions production traffic lacks — see §3.7 warning |
| **Evol-Instruct** | LLM rewrites instructions into progressively harder ones | prompts → harder prompts | WizardLM: evolved instructions "superior to human-created ones"; ">90 % capacity of ChatGPT on 17 of 29 skills" [[src](https://arxiv.org/abs/2304.12244)] | Building the *hard slice* that production traffic under-samples (doc 00 §3.3) |
| **Magpie** | Prompt an aligned model with only the left-side chat template; it emits a user query | nothing → (query, response) | 4M instructions synthesised from Llama-3-Instruct; 300K selected; fine-tunes "comparably to the official Llama-3-8B-Instruct" [[src](https://arxiv.org/abs/2406.08464)] | Cheap prompt-diversity generator. **Legally it is model extraction by construction** — see §5 |
| **Rejection sampling (RFT/best-of-n)** | Teacher or student samples k; a verifier/judge keeps the good ones | prompts → filtered T1 | RFT pushed LLaMA-7B GSM8K **35.9 → 49.3 %**, and helps weaker models more [[src](https://arxiv.org/abs/2308.01825)] | **The workhorse rung.** No logprobs needed |
| **STaR** | Self-bootstrapped rationales, kept only when the answer verifies | prompts + answers → T4 | "comparably to fine-tuning a 30× larger model" (doc 00 §3.1) | Where a verifier exists |
| **Constitutional AI / critique–revise** | Model critiques its own output against written principles, then revises; revisions become SFT targets; AI preferences become a reward model | outputs + principles → T4 + T3 | "train a harmless but non-evasive AI assistant"; "far fewer human labels" [[src](https://arxiv.org/abs/2212.08073)] | The **safety** data path (doc 00 I5) |
| **AI-feedback preference sets** | Teacher scores many responses on multiple aspects | responses → T3 | UltraFeedback: >1M GPT-4 feedback over 250k conversations, with "a series of techniques to mitigate annotation biases" [[src](https://arxiv.org/abs/2310.01377)] | The DPO fuel |
| **Self-rewarding** | The model judges itself during iterative DPO | — | Llama-2-70B beat Claude 2 / Gemini Pro / GPT-4-0613 on AlpacaEval 2.0 after 3 iterations [[src](https://arxiv.org/abs/2401.10020)] | **Avoid in this platform** — it is doc 00 §8.3's collapse risk by design |

### 3.2 On-policy vs off-policy — and the availability finding that decides it

Doc 00 §3.2 recommends **on-policy distillation** as the default and flags "⚠️ TO
BE VERIFIED per vendor" whether teachers expose per-token logprobs for arbitrary
continuations. Partially resolved:

| Teacher | Logprobs on generated tokens | Logprobs on a **supplied** continuation | Source |
|---|---|---|---|
| **Anthropic (Claude, all models)** | **No** | **No** | The Messages API parameter list has no `logprobs`/`top_logprobs`; `output_config` supports only `effort` and `format` [[src](https://platform.claude.com/docs/en/api/messages)] |
| **OpenAI (chat completions)** | **Yes** — `logprobs`, `top_logprobs` (integer 0–20) | **No** — documented as applying to "output tokens … returned in the `content` of `message`", i.e. model-generated completions, not supplied ones | [[src](https://developers.openai.com/api/docs/api-reference/chat/create)] |
| **Google Gemini** | ⚠️ not checked this session | ⚠️ | — |
| **Open-weight teacher, self-hosted** (Kimi-K3, DeepSeek-V4.1-Flash, Qwen3.8-27B) | **Yes, fully** | **Yes** — you own the forward pass; teacher-forcing the student's rollout and reading per-token logits is a local operation | Repo's own serving stack, [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) |

**This is a structural argument for open-weight teachers that is independent of the
legal one in §5.** The best distillation method in doc 00's evidence base — dense
per-token reverse-KL on the student's own rollouts, 1,800 vs 17,920 GPU-hours
[[src](https://thinkingmachines.ai/blog/on-policy-distillation/)] — is **not
purchasable from a frontier API at all**. With a frontier teacher you are
restricted to rungs 2–4 (rejection sampling, off-policy SFT, preference pairs).
With a self-hosted open-weight teacher you can run rung 5.

**Degradation ladder when logprobs are unavailable**, cheapest-effective first:

1. **Rejection-sampling FT on on-policy prompts** — student generates, teacher or
   verifier grades, keep the winners. Retains the *prompt* distribution match,
   loses the dense signal. This is GKD's cheap cousin; GKD's own framing is that
   the problem being solved is "distribution mismatch between output sequences
   seen during training and those generated by the student during inference"
   [[src](https://arxiv.org/abs/2306.13649)], and sampling prompts on-policy fixes
   half of that for free.
2. **Teacher-rewrites-the-student's-answer** (T1 conditioned on the student's
   attempt). Gives a minimal-edit target, which is a sparse approximation of a
   per-token signal, at one teacher call.
3. **Preference pairs (student vs teacher) → DPO.** One bit per example.
4. **Rubric scalar → RL.** Rubrics as Rewards shows this is viable beyond
   verifiable domains [[src](https://arxiv.org/abs/2507.17746)].

Information per teacher dollar falls monotonically down that list. Quantifying the
fall would need an experiment; ⚠️ **TO BE VERIFIED** — no published comparison of
information-per-teacher-dollar across these four was found.

### 3.3 Rejection sampling with a verifier or judge — the rung to build first

**Mechanism.** For each selected prompt `p` from S2: sample `k` completions
(from the *student* if it exists, else from the teacher); score each with the
verifier stack (schema check → tool replay → programmatic rubric criteria → judge
for the rest); keep the argmax if it clears the threshold; discard the prompt
entirely if nothing clears.

**Inputs.** Prompts (with the full prompt stack, per doc 00 §2.2), `k`, a
threshold, a rubric version, a verifier bundle.
**Outputs.** T1 records with provenance (`generator`, `k`, `selected_index`,
`score`, `rubric_version`), plus the *rejected* completions — which are free T3
preference pairs and free hard-negatives. Do not throw them away.

**Cost.** `k` × output tokens + 1 × input tokens if the k samples share one
prefill. §6.1 prices this; the key structural point is that **a self-hosted
teacher amortises the prefill across k by construction** (`n=k` in one request),
while an API charges input per request unless a prompt cache hits.

**Failure modes.**
- **Threshold set too high** → only easy prompts survive → the dataset is a
  curriculum of easy cases and the student never sees the hard slice. Detect by
  plotting survival rate *per slice*; a slice with <20 % survival is being deleted
  from the training distribution, not taught.
- **Judge is also the filter and the eval** → the filter and the gate are the same
  function, and the gate measures nothing. Use different judge configurations for
  filtering and for gating, and hold a slice of the eval judge out of filtering
  entirely.
- **`k` too small on hard prompts** → best-of-4 on a 10 %-solve-rate prompt finds
  a solution 34 % of the time (`est.`, `1−0.9⁴`). Vary `k` by predicted
  difficulty (T5) rather than using one global `k`.

### 3.4 Preference pairs for DPO / RLAIF

**Where the pairs come from**, in descending order of usefulness to this platform:

1. **Student vs incumbent, on shadowed production requests** — the pair the
   customer actually cares about, graded once. This is the only preference source
   that is simultaneously training data and sales evidence.
2. **Student vs teacher.**
3. **Student vs its own rejected samples** (free from §3.3).
4. **Synthetic aspect-scored sets** in the UltraFeedback style, when coverage is
   thin [[src](https://arxiv.org/abs/2310.01377)].

**Mechanics that matter.**
- Grade every pair **in both orders** and convert order-disagreements to *ties*;
  drop ties from DPO rather than assigning an arbitrary winner.
- **Length-match or length-control** before training, or DPO will learn "longer"
  [[src](https://arxiv.org/abs/2404.04475)].
- Record the *margin*, not only the winner — it lets S5 filter to
  high-confidence pairs without regenerating.
- **RLAIF vs RLHF:** doc 00 §3.1 already cites "RLAIF achieves comparable
  performance to RLHF", with `d-RLAIF` skipping the reward model entirely
  [[src](https://arxiv.org/abs/2309.00267)]. The corollary for this document is
  that the *annotation* budget, not the RL budget, is where the quality is
  decided.

### 3.5 Constitutional-AI-style critique–revise, for the safety path

Doc 00's invariant I5 makes safety a separate, non-negotiable gate, and §3.3 notes
refusal behaviour is under-represented in the distillation signal because it is a
small fraction of tokens. Constitutional AI is the published mechanism for
manufacturing that signal cheaply: sample from the model, "generate self-critiques
and revisions, and then finetune the original model on revised responses", then
build a preference model from *AI* preferences over the principles
[[src](https://arxiv.org/abs/2212.08073)].

**Adapted to this platform:**
- The "constitution" is the **customer's** policy document — the part of their
  system prompt that encodes refusals, escalation rules and tone — extracted and
  versioned as a first-class artifact.
- Critique–revise runs on the **student's** outputs on adversarial and red-team
  prompts, not on general traffic, because that is where the rare behaviour lives.
- The revised outputs are oversampled into the SFT mix at a rate set by the safety
  eval's failure rate, not by their natural frequency.
- ⚠️ **TO BE VERIFIED** — the CAI result is Anthropic's own on their own models;
  no public replication at the 2–30B student scale on an enterprise policy document
  was found this session.

### 3.6 Quality filters and dedup

Order matters: cheapest filter first, because each stage shrinks the input to the
next.

| Stage | Mechanism | Evidence / anchor | Drop rate to expect |
|---|---|---|---|
| **1. Format/validity** | Schema validation, tool-arg `json.loads`, truncation check, EOS check | Doc 00 §2.3; and Marlin-2B's dual-EOS trap is a live example of a format bug that silently burns tokens ([`models/marlin2b/README.md`](../models/marlin2b/README.md)) | 1–10 % ⚠️ task-dependent |
| **2. Exact dedup** | Substring/hash dedup | Removing a 61-word sentence repeated **>60,000 times** from C4; dedup made models "emit memorized text ten times less frequently" and reduced train-test overlap "affecting over 4 % of the validation set" [[src](https://arxiv.org/abs/2107.06499)] | Production traffic is *heavily* duplicated (retries, templates) — expect much more than web-scale rates |
| **3. Semantic dedup** | Embed, cluster, drop near-duplicates within clusters | SemDeDup removed **50 % of LAION with minimal performance loss**, "effectively halving training time", and improved out-of-distribution performance [[src](https://arxiv.org/abs/2303.09540)] | 20–50 % ⚠️ |
| **4. Quality/rubric filter** | §3.3's threshold | RFT [[src](https://arxiv.org/abs/2308.01825)] | Task-dependent |
| **5. Curation to a small high-quality set** | Keep the best N | LIMA: **1,000 curated examples**, no RL, responses "equivalent or strictly preferred to GPT-4 in 43 % of cases" [[src](https://arxiv.org/abs/2305.11206)]; phi-1's textbook-quality thesis (doc 00 §3.1) | The direction of travel |

**The LIMA/phi-1 implication for the budget.** If 1,000 curated examples can carry
a format and style, then the 100,000-example annotation budget in §6.2 is being
spent on *coverage of the hard slices and the tail*, not on volume. That reframes
the spend: the marginal dollar should go to hard-slice acquisition and human
adjudication, **not** to another 100k easy teacher completions. ⚠️ LIMA is a
general-alignment result at 65B; whether 1,000 examples suffice for a 2–30B student
on a specific enterprise task is exactly what the platform's first iteration
measures.

**Semantic dedup is also a leak detector.** The same embedding index that finds
near-duplicates within the training set finds near-duplicates *across* the
train/test boundary, which is §3.7's mechanism.

### 3.7 Contamination avoidance

Doc 00 §8.4 states the requirements (split by stable entity, hold the test split
out of annotation, hash-dedup across splits, timestamp splits, treat eval refresh
as invalidating history) and cites the masked-MMLU result — ChatGPT and GPT-4 at
**52 % and 57 % exact-match guessing masked answer options**
[[src](https://arxiv.org/abs/2311.09783)]. This section adds the three
contamination paths **specific to an annotation pipeline**, which doc 00 does not
enumerate:

1. **Teacher-side contamination of the eval.** If the eval's reference answers
   were written by the teacher, and the judge is asked to compare student output
   to that reference, the eval rewards teacher-mimicry rather than correctness.
   *Fix:* references for the **frozen test split** must be human-authored or
   human-adjudicated, never teacher-generated. This is a hard cost the eval budget
   must carry (§6.2).
2. **Synthetic-prompt contamination.** Self-Instruct/Evol-Instruct/Magpie generate
   prompts from seeds. If the seeds were drawn from the whole trace store, evolved
   descendants of test prompts end up in training. *Fix:* seed **only** from the
   train split, and run semantic dedup of every synthetic prompt against the test
   split before it enters the dataset.
3. **Round-over-round leakage.** Iteration N's test set is next quarter's traffic,
   which iteration N+1 annotates. *Fix:* the test split is a *permanent* exclusion
   list keyed by stable entity id, carried forward across iterations, and the eval
   refresh cadence is decoupled from the training cadence (doc 00 §8.3 guard 3).

**Mechanism to implement:** one `split_assignment` table keyed by
`(tenant, task, entity_id)` with an append-only history; every annotation job takes
the split as an input filter, and the pipeline *refuses to run* on rows whose split
is `test`. Make it a hard failure, not a warning.

### 3.8 Failure-mode summary for §3

| Failure | Detection | Mitigation |
|---|---|---|
| Teacher error becomes gold | Human spot-check of a random 1 % stratified by teacher confidence | Verifier before teacher wherever a verifier exists; T1 provenance records the teacher and version |
| Student trained on its own outputs | Provenance field `generator` on every record | Doc 00 §8.3 guard 1, enforced by the schema: a record whose generator is a student checkpoint cannot be a target unless `verified_by != null` |
| Easy-slice curriculum | Per-slice survival rate in the rejection sampler | Slice-aware `k` and thresholds |
| Verbose targets | Token-length distribution of T1 vs incumbent outputs | Length control in judging; length as an explicit rubric criterion where the product cares |
| Rationale confabulation | Does the rationale's answer match the answer field? | Reject rationale/answer mismatches automatically — a free filter |
| Style transfer without substance | Judge validation (§2.5) + programmatic criteria | Doc 00 §3.3 |
| Diversity collapse round over round | Distinct-n, embedding-cluster entropy, refusal rate, per round | Doc 00 §8.3 guard 4 |

---

## 4. Human-in-the-loop

### 4.1 When a human is structurally required

Humans are not a quality upgrade applied everywhere; they are required at four
specific points, and nowhere else. Spending outside these four is the single
easiest way to make the loop uneconomic (§6.2 shows humans are 67–95 % of the
annotation budget).

| # | Point | Why a model cannot do it | Volume | Who |
|---|---|---|---|---|
| **H1** | **Judge calibration gold set** | The judge is the thing being measured; a model cannot validate itself (§2.1) | ~200/slice (§2.5) | Customer SME, adjudicated |
| **H2** | **Frozen eval-set references** | §3.7 path 1 — teacher-written references make the eval measure mimicry | The test split, once | Customer SME |
| **H3** | **Safety and policy adjudication** | Doc 00 I5 makes safety non-tradeable; an AI-graded safety gate that the customer has not signed is not a gate they will accept | The safety suite + every incident | Customer's policy owner |
| **H4** | **Disagreement adjudication** | §1.4's queues — by construction these are the cases where models disagree | 1–3 % of traffic, sampled | SME or trained annotator |

Everything else — bulk T1, T2, T3, T5 — should be model-produced and
model-filtered. The published support for that division is blunt: ChatGPT's
zero-shot accuracy exceeded crowd workers on 4 of 5 annotation tasks, its
intercoder agreement exceeded **both crowd workers and trained annotators on all
tasks**, at **<$0.003 per annotation, ~20× cheaper than MTurk**
[[src](https://arxiv.org/abs/2303.15056)]. That result is from March 2023 on
tweet-classification; it is weaker evidence for long-context enterprise tasks, but
the direction has only strengthened since. ⚠️ No 2026 replication was obtainable
this session.

**The asymmetry to state to a customer.** Humans are not more accurate than a good
judge on average. Humans are *necessary* because they are the only source of a
label the customer will accept as ground truth when the decision is contested.
That is a governance property, not an accuracy property — and it is why H1–H3 are
non-negotiable while bulk labelling is not.

### 4.2 Choosing which examples a human sees

The selection policy is worth more than the annotator quality, because it sets how
many labels you need at all.

| Policy | Mechanism | Evidence / note |
|---|---|---|
| **Stratified by slice** | Fixed quota per slice from T5 | Required for §2.5's per-slice agreement; without it the rare slices have no estimate |
| **Disagreement-driven** | The four queues of §1.4 | The platform's highest-value queue; also directly demoable (doc 00 §5.6 item 3) |
| **Uncertainty/entropy** | Judge score near the decision threshold; high variance across panel members | Position-bias entropy is the published instance: "balanced position diversity entropy to measure the difficulty of each example and seeks human assistance when needed" [[src](https://arxiv.org/abs/2305.17926)] |
| **Diversity/coverage (selective annotation)** | Pick a diverse, representative pool *before* labelling | `vote-k` graph-based selective annotation gave "12.9 %/11.4 % relative gain under an annotation budget of 18/100" and matched supervised fine-tuning "with 10-100× less annotation cost across 10 tasks" [[src](https://arxiv.org/abs/2209.01975)] |
| **Incident-driven** | Every production incident becomes a permanently-retained labelled case | Doc 00 §8.5; the regression suite only grows |
| **Random** | i.i.d. sample | **Still required** — a small unbiased sample is the only way to estimate the *population* error rate, since every policy above is biased by construction |

**Decision rule.** Run a fixed budget split: ~60 % disagreement/uncertainty
queues, ~25 % stratified-by-slice, ~15 % pure random. The random tranche is what
lets you say "the judge agrees with humans 87 % of the time *on production
traffic*" rather than "on the hard cases we chose".

> **Verification note.** I attempted to cite a published active-learning-for-LLM-
> annotation result via arXiv 2310.15205; that ID returns **DISC-FinLLM**, an
> unrelated financial-LLM paper. The intended claim was therefore **dropped**
> rather than approximated, and selective annotation ([2209.01975](https://arxiv.org/abs/2209.01975))
> is cited in its place. ⚠️ A proper active-learning source for this setting
> remains an open question.

### 4.3 Tools

Fetched 2026-09-19. Split by what they are *for*, because the market conflates
"annotation tool" with "annotation workforce" and the platform needs both.

**Annotation surfaces (software you run):**

| Tool | What it gives the loop | Licence / price | Status note |
|---|---|---|---|
| **Argilla** | Collaboration surface for "AI engineers and domain experts to build high-quality datasets"; explicitly targets "language model fine-tuning, RLHF, and evaluation" and supports active-learning workflows [[src](https://argilla.io/)] | Open source | **Joining Hugging Face** — the site's own banner reads *"Argilla is joining Hugging Face"* [[src](https://argilla.io/)]; good for longevity, but it is a second party's roadmap |
| **Label Studio / HumanSignal** | General labelling across modalities incl. video; Enterprise adds SSO/SAML, SOC2/HIPAA, 99.9 % SLA, programmable interfaces and **LLM-as-a-Judge** [[src](https://humansignal.com/pricing/)] | Community free self-hosted; **Starter Cloud $99/mo + $49/mo per additional user**, up to 12 users; Enterprise custom [[src](https://humansignal.com/pricing/)] | The only listed option with first-class **video** labelling, which matters for §8 |
| **Langfuse** | Annotation queues over traces/sessions/observations; score configs as a prerequisite; annotation from experiment-comparison views with summary metrics updating live [[src](https://langfuse.com/docs/evaluation/evaluation-methods/annotation)] | Open source, self-hostable; cloud tiers in doc 00 §6 | Already the doc-00-recommended trace store — **annotating where the traces already are removes an export step and a PII egress** |
| **Braintrust Human Review** | Categorical (0–100 %, stored 0–1), continuous sliders, and free-form string feedback that can write to the `expected` field; per-group **score visibility** (explicitly "a display filter, not an access control rule"); **blind review** so reviewers cannot see peers' scores before submitting; reviewed logs become eval datasets [[src](https://www.braintrust.dev/docs/guides/human-review)] | "Unlimited human review scorers are only available on Pro and Enterprise plans" [[ibid.](https://www.braintrust.dev/docs/guides/human-review)] | **Blind review is the feature to copy** — it is the only cheap defence against annotator anchoring (§4.5) |
| **W&B Weave** | Agent-native tracing with sessions/turns/tools as first-class, plus PII/toxicity/hallucination scorers (doc 00 §6) | Bound to W&B | Scorers are the reusable part |

**Annotation workforces (people you buy):**

| Vendor | What they sell | Stated numbers | Note |
|---|---|---|---|
| **Prolific** | Participant marketplace, pay-as-you-go | "we recommend you pay participants at least **£9.00 / $12.00 per hour**, while the minimum pay allowed is **£6.00 / $8.00 per hour**"; platform fee "usually **42.8 % for corporate** customers, and a discounted 33.3 % for academic or non-profit" [[src](https://www.prolific.com/pricing)] | **The only vendor here that publishes a rate.** It is therefore the anchor for every human-cost estimate in §6 |
| **Toloka** | Data labelling, domain-expert annotation, RLHF preference data, evals, red-teaming, agent trajectories and RL environments | "90+ domains of expertise", "70 %+ people with advanced degrees", "6000+ active contributors", 100+ countries, "50+ methods of automated quality control", ISO 27001/27701, SOC 2, GDPR, HIPAA [[src](https://toloka.ai/)] | Repositioned from crowdsourcing marketplace to managed service; **pricing not disclosed** |
| **Surge AI** | "data and RL environments, off the shelf"; an "Expert Workforce"; publishes its own benchmarks (Hemingway-bench, EnterpriseBench, Riemann-bench) [[src](https://www.surgehq.ai/)] | **No pricing, throughput or QA process disclosed on the page fetched** | ⚠️ Cannot be priced into a budget from public information |
| **Scale AI** | Scale Data Engine, GenAI Portfolio, Donovan; private benchmarks and leaderboards; "25 % have advanced degrees" [[src](https://scale.com/)] | No pricing; new CEO Francis deSouza; "10 years" since 2016 [[ibid.](https://scale.com/)] | ⚠️ The Meta-investment/ownership question was **not** answerable from the page fetched |
| **Snorkel AI** | Expert data-as-a-service with "curriculum-structured datasets… rubrics, reviewer guidance, difficulty tiers, and evaluation slices"; verifiable agent benchmarks (doc 00 §7.1) | — | The rubric/difficulty-tier/slice vocabulary is exactly §2.2's, sold as a service |

**Build/buy call.** Buy the *workforce*; adopt the *surface* that sits closest to
the traces. Concretely: Langfuse annotation queues for trace-attached review
(H4), Label Studio Enterprise for video (H2/H3 in §8), and a workforce contract
for H1/H2 volume. **Do not build an annotation UI** beyond the routing and
provenance layer (§7.4) — the surfaces above are commodity and the differentiator
is the queue-selection policy (§4.2), not the widget.

### 4.4 Cost per label and throughput

The only published rate in the survey is Prolific's, so build the model from it
and label everything else as inference.

**Loaded hourly cost** (`est.`, from [[src](https://www.prolific.com/pricing)]):

| Participant rate | + 42.8 % corporate fee | Loaded $/hour |
|---:|---:|---:|
| $8.00 (platform minimum) | $3.42 | **$11.42** |
| $12.00 (Prolific's recommendation) | $5.14 | **$17.14** |

**Throughput assumptions and the resulting $/label** (`est.`; throughput is the
inference, and it is the number most likely to be wrong):

| Task | Time/item | Items/hour | $/label @ $11.42/h | $/label @ $17.14/h |
|---|---:|---:|---:|---:|
| Binary flag on a short output | 30 s | 120 | $0.10 | $0.14 |
| Rubric grade, short prompt (<500 tok) | 3 min | 20 | $0.57 | $0.86 |
| **Rubric grade, enterprise prompt stack (4,000 tok)** | 12 min | 5 | **$2.28** | **$3.43** |
| Pairwise preference, enterprise prompt | 15 min | 4 | $2.86 | $4.29 |
| **Gold rewrite of a 512-token answer** | 20 min | 3 | **$3.81** | **$5.71** |
| Video clip QA authoring (2-min clip) | 25 min | 2.4 | $4.76 | $7.14 |

The 12-minute figure for a 4,000-token prompt is not padding: 4,000 tokens is
~3,000 words, which is ~12 minutes of *reading alone* at 250 wpm before any
judgement is made. **Any cost model that assumes a crowd worker grades an
enterprise prompt in under 5 minutes is wrong**, and this is the most common way
annotation budgets are under-estimated.

**Domain-expert (SME) rates.** ⚠️ **TO BE VERIFIED — this is doc 00's Open
Question #10 and it remains unresolved: no vendor in §4.3 publishes an
adjudicated-example price.** Snorkel, Surge, Toloka and Scale all sell expert data
and none quotes a number. The honest substitute is a reasoned floor: a customer
SME (support lead, clinician, lawyer, engineer) at a fully-loaded internal cost of
**$75–$150/hour** (⚠️ assumption, not sourced) at 10 minutes per adjudication is
**$12.50–$25.00 per adjudicated example**. Everything in §6.2 that involves H1–H3
uses that band, and it should be replaced with a quoted number before it is used
in a customer proposal.

**The scaling consequence.** At $12.50–$25/example, the 1,000-example judge
calibration set of §2.5 costs **$12,500–$25,000** — ⚠️ *corrected 2026-09-19*: **2.3–4.5×** doc 00
§4.3(b)'s entire $5,554 training bill (not "comparable to" it), and 3.8–7.6× its
$3,280 batched annotation bill, and
therefore **the dominant one-time cost of a loop iteration**. Doc 00 §4.3 asserted
this; §6.2 now shows it arithmetically.

### 4.5 QA of annotators

Human labels are not ground truth by virtue of being human; they are ground truth
by virtue of a measured process. Minimum viable QA, all of it cheap:

1. **Overlap.** Route 10–20 % of items to ≥2 annotators; compute Krippendorff's α
   across raters continuously, per annotator and per slice. An annotator whose α
   against the consensus drops below a floor is paused, not silently averaged in.
2. **Embedded gold.** Seed 5 % of every queue with items whose answer is already
   adjudicated. This measures accuracy, not just agreement, and it catches
   drift within a session.
3. **Blind review.** Reviewers must not see peers' scores or comments before
   submitting — Braintrust ships this as a toggle
   [[src](https://www.braintrust.dev/docs/guides/human-review)]; without it,
   "agreement" partly measures anchoring.
4. **Adjudication, not majority vote, on H1–H3.** For the gold sets that define
   truth, disagreements go to a named adjudicator whose decision is recorded with
   a rationale. Majority vote on three annotators who each misread the rubric
   produces a confidently wrong gold set.
5. **Training and a rubric quiz before queue access**, with the quiz itself
   versioned alongside the rubric. A rubric change requires re-qualification —
   this is the human-side analogue of §7.3's version invalidation.
6. **Do not show the model's answer first** when collecting an independent label.
   Where the task *is* to correct the model (T1 rewrite), that is a different
   queue with a different provenance tag, and its outputs must never be pooled
   with independent labels in an agreement statistic.

⚠️ **TO BE VERIFIED** — items 1–6 are standard practice assembled from the tool
capabilities fetched (Braintrust blind review, Langfuse score configs, Toloka's
"50+ methods of automated quality control") plus the inter-rater literature; I
could not fetch a single authoritative source prescribing this exact QA bundle.

---

## 5. Terms of service and legal

This section carries the risk doc 00 §8.1 calls "the risk that can end the
product". It is written to be quotable to a lawyer: every clause below is verbatim
from a source fetched 2026-09-19, with the effective date attached. **It is not
legal advice and the platform must not ship a customer-facing claim on it without
counsel.**

### 5.1 OpenAI — resolved for the Business Terms, and narrower than hoped

Doc 00 could not read these pages (HTTP 403 to both `WebFetch` and `curl`), and
correctly refused to paraphrase. The pages still 403 today. However, a **Wayback
Machine snapshot dated 2026-09-12** of the *OpenAI Services Agreement* is readable
[[snapshot](http://web.archive.org/web/20260912202946/https://openai.com/policies/business-terms)],
and the live URL it mirrors is
[openai.com/policies/business-terms](https://openai.com/policies/business-terms/).
The document is headed **"Updated: December 1, 2025 … Effective: January 1, 2026"**
and states it "only applies to use of OpenAI's APIs, ChatGPT Enterprise, ChatGPT
Business, ChatGPT for Clinicians, and other services for customers who are
businesses and developers".

**What was, and was not, obtained.** Only the *Services Agreement* (the
"Business Terms") was obtained, and only through an archive capture — which is
evidence of what the page published on 2026-09-12, **not** the executed contract.
OpenAI's **Usage Policies**, the separate **Service Terms**, and the **ROW/EU
Terms of Use** remain unread in every session of this programme; nothing below is
sourced from them, and the consumer-ChatGPT surface they govern is out of scope
here. The routes to the real document are in *How to obtain the clause without the
web*, below.

> **§3.3 Restrictions.** "Customer will not, and will not permit End Users to:
> … (d) **Reverse Engineer** any aspect of the Services or the systems used to
> provide the Services; **(e) except for a Permitted Exception, use Output to
> develop artificial intelligence models that compete with OpenAI's products and
> services**; (f) extract data from the Services other than as permitted through
> the Services; …"

> **Definitions.** "**Permitted Exception**" means Customer using Output to:
> **(a) develop artificial intelligence models primarily intended to categorize,
> classify, or organize data (e.g., embeddings or classifiers), if these models are
> not distributed or made commercially available to third parties;** and **(b) fine
> tune or customize models provided as part of OpenAI's fine-tuning or other
> Services set forth on the Pricing Page.**"

> **"Reverse Engineer"** means "reverse assemble, reverse compile, decompile,
> translate, **engage in model extraction or stealing attacks**, or otherwise
> attempt to discover the source code or underlying components of the Services,
> algorithms, and systems of the Services (except to the extent these restrictions
> are contrary to applicable law)."

**Reading it against what the platform does.** Four distinct findings:

1. **The Permitted Exception does not cover generative distillation.** Limb (a) is
   for classifiers and embeddings *and* only if undistributed; a distilled
   generative student served to the customer fails both halves. Limb (b) covers
   fine-tuning **OpenAI's own** models — which is precisely the OpenAI-documented
   workflow doc 00 §7.1 quotes ("Tune a prompt for a larger model… then tune a
   smaller model"), and precisely **not** distilling into DeepSeek/Qwen/Kimi
   weights served on our GPUs.
2. **The restriction is conditioned on competition, and that word does the work.**
   §3.3(e) prohibits developing models "that compete with OpenAI's products and
   services" — it is not a flat ban on all training. Whether a narrow single-task
   specialist serving one enterprise's internal feature "competes" with a general
   API is a genuine legal question with a real argument on each side. It is not a
   question an engineering document can close, and it is not a risk to carry
   silently.
3. **"Model extraction" is named inside the Reverse Engineer definition.** That is
   a separate prohibition from (e), it has **no** Permitted Exception, and it is
   the clause that bears on high-volume logprob harvesting and on Magpie-style
   template-prefix extraction (§3.1). A T5 classifier trained on Outputs may sit
   inside the Permitted Exception; **systematically harvesting the model's own
   distribution does not**.
4. **The liability exposure is asymmetric.** §14.1 excludes indirect/consequential
   damages "EXCEPT FOR… (B) CUSTOMER'S BREACH OF SECTION 3.3 (RESTRICTIONS)" —
   i.e. for a §3.3 breach, lost profits are *not* excluded. §14.2's overall cap
   (twelve months of fees) lists only gross negligence/wilful misconduct,
   indemnification obligations and payment obligations as exceptions, so the
   amount cap still applies — but the *kind* of damages available to OpenAI is
   broadened specifically for this clause. A drafter singles out a clause like
   that for a reason.

#### How to obtain the clause without the web

Every doc in this tree treated the clause as simply unobtainable because
`openai.com` 403s. That was a wrong conclusion from a true observation: four
routes to the document do not pass through `openai.com`'s edge. Each is an action
with an owner, in the order they are worth trying.

- **(a) In-product.** The Business Terms and Service Terms are served inside the
  OpenAI platform console's legal/settings surface to any signed-in account
  holder; the edge that 403s anonymous fetches does not gate an authenticated
  console session. **Action:** whoever holds our OpenAI org account opens the
  console and exports the PDF. *Owner: whoever holds the org account.*
  ⚠️ **TO BE VERIFIED** — that the console exposes a downloadable copy has not
  been confirmed in this session.
- **(b) The first lighthouse customer's executed MSA.** An enterprise customer's
  signed agreement incorporates the Business Terms and Service Terms by
  reference, and that customer's counsel already holds the copy that binds
  *them* — which is the copy that matters, since the tenant, not the platform, is
  the contracting party. **Action:** request it during legal onboarding of
  lighthouse A; it is an ordinary diligence ask.
  *Owner: doc 08 / the lighthouse-A owner ([`10` §4.1](10-roadmap-and-mvp.md)).*
- **(c) Ask OpenAI.** Sales and legal will send the current terms on request.
  **Action:** one email, sent before any customer-facing claim is drafted, not
  after. *Owner: doc 08, with counsel.*
- **(d) The Wayback Machine.** `web.archive.org` is not served by `openai.com`'s
  edge. **This is the route that worked** — the 2026-09-12 snapshot quoted above.
  **Action:** re-run it for the pages still missing (Usage Policies, Service
  Terms, ROW/EU Terms of Use). *Owner: doc 08.*

**Do not spend another attempt on the live URLs.** All five were re-tested on
**2026-09-19** with a browser user-agent
(`curl -sL -A 'Mozilla/5.0 … Chrome/140.0.0.0 Safari/537.36'`):
`/policies/business-terms/`, `/policies/usage-policies/`,
`/policies/services-agreement/`, `/policies/row-terms-of-use/` and
`/policies/eu-terms-of-use/` each returned **HTTP 403**, and
`platform.openai.com/docs/guides/distillation` 200s but redirects into the
supervised-fine-tuning guide this tree already cites, at its
`#distilling-from-a-larger-model` anchor
[[src](https://developers.openai.com/api/docs/guides/supervised-fine-tuning#distilling-from-a-larger-model)]
— there is no separate distillation guide to obtain. The
block is at the edge, not in the tool; a sixth fetch attempt buys nothing.

### 5.2 Anthropic

> **Usage Policy, effective 2025-09-15**, under "Do Not Abuse our Platform":
> *"Utilization of inputs and outputs to train an AI model (e.g., 'model scraping'
> or 'model distillation') **without prior authorization from Anthropic**"*
> [[src](https://www.anthropic.com/legal/aup)]

> **Commercial Terms of Service, effective 2025-06-17, §D.4** — *"Customer may not
> and must not attempt to (a) access the Services to build a competing product or
> service, including to train competing AI models or resell the Services **except
> as expressly approved by Anthropic**"*, and *"(b) reverse engineer or duplicate
> the Services"* (as quoted in doc 00 §8.1
> [[src](https://www.anthropic.com/legal/commercial-terms)]).

Anthropic's is the **broadest** prohibition of the three — it names distillation
explicitly and does not condition on competition — and simultaneously the most
**negotiable**, because both clauses contemplate authorisation/approval existing.
Doc 00 already draws the right conclusion: this is a licensing problem, and a
per-tenant authorisation is a configuration flag with a document attached.

### 5.3 Google

> **Gemini API Additional Terms of Service, effective 2026-03-23** — *"You may not
> use the Services to develop models that compete with the Services (e.g., Gemini
> API or Google AI Studio). You also may not attempt to reverse engineer, extract
> or replicate any component of the Services, including the underlying data or
> models (e.g., parameter weights)."* [[src](https://ai.google.dev/gemini-api/terms)]

Two further clauses from the same page that bear on the platform's *privacy*
posture (doc 00 §8.2 / invariant I6), and that make Gemini the most usable frontier
teacher for a privacy-sensitive tenant **if** the competition clause is cleared:

- **Paid services:** *"Google doesn't use your prompts (including associated system
  instructions, cached content, and files such as images, videos, or documents) or
  responses to improve our products"*; paid data is retained only briefly for
  Prohibited-Use-Policy enforcement and legal compliance
  [[src](https://ai.google.dev/gemini-api/terms)].
- **Unpaid services:** *"Google uses the content you submit to the Services and any
  generated responses to provide, improve, and develop Google products and services
  and machine learning technologies"*, with human reviewers possible
  [[ibid.](https://ai.google.dev/gemini-api/terms)].

**The free tier is disqualified for customer traffic outright.** That is a
one-line policy the platform must enforce in code: teacher credentials for a tenant
must be paid-tier, verified, not merely configured.

### 5.4 Open-weight teachers — the clean path

| Model | Licence | Does it restrict using outputs to train another model? | Source |
|---|---|---|---|
| **DeepSeek-V4.1-Flash** | **MIT**, on repo *and* weights | **No.** MIT places no restriction on outputs | `license:mit` in the HF model tags [[src](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash)]; [`models/deepseek41f/architecture.md` §1](../models/deepseek41f/architecture.md) |
| **DeepSeek-V4.1-Flash-NVFP4** | MIT | No | [`models/deepseek41fnvfp4/architecture.md`](../models/deepseek41fnvfp4/architecture.md) |
| **Qwen3.8-27B** | **Apache-2.0** | **No** | `license:apache-2.0` in the HF model tags [[src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B)] |
| **Marlin-2B** | Apache-2.0 | No | [`models/marlin2b/MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md) |
| **Kimi-K3** | **Kimi K3 License** (MIT-shaped + 2 riders) | **No distillation restriction of any kind.** The grant expressly includes the right to "run, deploy, fine-tune, or otherwise modify the Software and create derivative works from it" | [[LICENSE](https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE)] |

**The Kimi-K3 riders, read precisely** — because they *can* bite, just not on
distillation. Verbatim from the licence:

> **§2.** *"'Model as a Service' means giving a third party access to language model
> inference or fine-tuning (e.g., via API) in a manner that allows such third party
> to exercise meaningful control over the inputs, parameters, or training data. This
> does not include (a) end-user products with model capabilities solely embedded
> within specific features or harnesses, or (b) mere relaying of requests to models
> hosted by others.* … *If the Licensee or any of its affiliates operates a Model as
> a Service business, and the aggregate revenue … exceeds 20 million US dollars …
> over any consecutive 12 months, the Licensee must enter into a separate agreement
> with Moonshot AI before using the Software or its derivative works for any
> commercial purpose."*

> **§3.** Above 100 M MAU or $20 M monthly revenue, *"'Kimi K3' must be prominently
> displayed on the user interface"*.

> **§4.** §2 and §3 *"do not apply to: (a) internal use of the Software, defined as
> any use that does not make the Software, its outputs, or its underlying
> capabilities available to third parties"*.

⚠️ **TO BE VERIFIED — a genuine ambiguity the platform must resolve with counsel,
not assume away.** Using Kimi-K3 as an *annotation teacher* for a customer's data
sits awkwardly between these:
- It is arguably **not** "Model as a Service" under §2, because the customer does
  not "exercise meaningful control over the inputs, parameters, or training data"
  of Kimi-K3 — they supply traffic, we supply the annotation program.
- But it is arguably **not** covered by §4(a)'s internal-use exemption either,
  because handing the customer teacher-generated labels *does* make "its outputs …
  available to third parties".

If §2 does not apply, §4(a) does not need to. That is the likely reading, and it
is the one to put to counsel — but the platform's own revenue crossing $20 M makes
this a question worth answering *before* it is expensive, and **doc 08 should carry
it**. Note also the safe fallback: DeepSeek-V4.1-Flash (MIT) and Qwen3.8-27B
(Apache-2.0) carry **no rider at all**, so a tenant with any doubt can be routed to
those teachers with a configuration change.

⚠️ **Meta / Llama licences were not fetched this session** and are therefore absent
from the table. Historically the Llama community licence has carried an
outputs-to-improve-other-models clause materially different from MIT/Apache, so
**do not assume an open-weights model is unencumbered because it is open-weights.**
Read each licence.

### 5.5 Teacher choice as a decision table

| Teacher | Legal posture | Logprobs (§3.2) | Rungs reachable | Cost/100k gold (§6) | Verdict |
|---|---|---|---|---|---|
| **DeepSeek-V4.1-Flash** (self-hosted, MIT) | **Clean** | ✅ full | 1–6 | **$90** | **Default teacher.** Clean licence, cheap, full method access |
| **Qwen3.8-27B** (self-hosted, Apache-2.0) | **Clean** | ✅ full | 1–6 | **$26** | Default *judge* and default **video** teacher (§8) |
| **Kimi-K3** (self-hosted, Kimi K3 Licence) | Clean on distillation; §2 MaaS rider ⚠️ | ✅ full | 1–6 | **$702** | The strong open teacher. Resolve §2 with counsel first |
| **Gemini 3.8 Flash** (API) | Flat competition prohibition; **paid tier only** | ⚠️ unchecked | 2–4 | **$492** (batch $246) | Cheapest frontier teacher by far; legal risk identical in kind to the others |
| **GPT-6 Astra / GPT-5.6 Sol** (API) | §3.3(e), narrow Permitted Exception, model-extraction clause, broadened damages | Generated tokens only | 2–4 | $6,560 / $2,624 (batch: $3,280 / $1,312) | Per-tenant exception only, with the customer's own agreement and a documented decision |
| **Claude Opus 5 / Fable 5.1** (API) | Broadest prohibition, but **authorisation contemplated** | ❌ none | 2–4 | $3,280 / $6,560 (batch: $1,640 / $3,280) | Only with written Anthropic authorisation. Excellent *judge* candidate where the teacher is OpenAI/Google (§2.8 rule 1) |

**The architecture rule doc 00 §8.1 states, restated with the new evidence.** The
platform must not *require* a frontier teacher. Three independent reasons now
converge on that: (i) all three vendors prohibit the mechanism in terms; (ii)
OpenAI's Permitted Exception explicitly does not cover it and its damages carve-out
singles the clause out; (iii) **the best method in the evidence base needs
logprobs, and no frontier API sells them** (§3.2). Open-weights-teacher-first is
not a risk hedge; it is also the technically superior path.

### 5.6 What the platform must implement

| Requirement | Mechanism | Owner |
|---|---|---|
| **Per-tenant teacher policy** | An allowlist of `(teacher_model, legal_basis, document_ref, approved_by, expires_at)` per tenant; any annotation job naming a teacher outside it fails closed | Doc 08 |
| **Provenance on every label** | `teacher_model`, `teacher_version`, `legal_basis`, `job_id` recorded on the record itself, not in a job log (§7.2) | This doc §7 |
| **Paid-tier enforcement** | Credential check per provider; free-tier Gemini keys rejected for customer traffic [[src](https://ai.google.dev/gemini-api/terms)] | Doc 08 |
| **Redaction before egress** | PII removed on the edge from trace store → teacher, not before storage (doc 00 §8.2). **Presidio** (analyzer / anonymizer / image-redactor / structured, MIT) is the obvious component — note it has **moved from Microsoft to a community org**, `data-privacy-stack/presidio`, and its own README warns "there is no guarantee that Presidio will find all sensitive information" [[src](https://github.com/data-privacy-stack/presidio)] | Doc 08 |
| **Customer-account teachers** | Support calling the teacher with the *customer's* key under the *customer's* agreement — a different legal posture (doc 00 §8.1) and a different audit record | Doc 08 + this doc's schema |
| **Exportability** | Labels, rubrics, gold sets and provenance exportable in open formats on demand (doc 00 §8.6) | This doc §7 |

---

## 6. Cost model

### 6.1 Unit costs by method

**The reference example.** 4,000 input tokens (prompt stack + user turn) and 512
output tokens, identical to doc 00 §4.3(a) so the numbers compose. All figures
`est.`, arithmetic on the sourced prices below.

**Price inputs, all fetched 2026-09-19:**

| Source | Prices used |
|---|---|
| [OpenAI pricing](https://developers.openai.com/api/docs/pricing) | GPT-6 Astra $10/$1 cached/$50; GPT-5.6 Sol $4/$0.40/$20; Terra $2/$0.20/$12; Luna $0.20/$0.02/$1.20; batch **50 %** |
| [Claude pricing](https://claude.com/pricing) | Fable 5.1 $10/$0.25 read/$50; Opus 5 $5/$0.50/$25; Sonnet 5 $2/$0.20/$10; Haiku 4.5 $1/$0.10/$5; batch **50 %** |
| [Anthropic Batches docs](https://platform.claude.com/docs/en/build-with-claude/batch-processing) | Confirms the 50 % table: Opus 5 batch **$2.50/$12.50**, Sonnet 5 **$1/$5**, Fable 5.1 **$5/$25** |
| [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) (page dated 2026-09-16) | Gemini 3.8 Flash **$0.75 in / $3.75 out** through 2026-12-31 (then $1.50/$7.50); batch **50 %** |
| [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) §5 (input) and §3 (S4 output, no SLO) | Kimi-K3 B300 `low` $1.2921 in / $3.61 out; DeepSeek-V4.1-Flash B200 `low` $0.1806 / $0.355; Qwen3.8-27B B300 `low` $0.046 / $0.1527; Marlin-2B B300 `low` $0.009 / $0.022 |
| [Prolific pricing](https://www.prolific.com/pricing) | $8–$12/h participant + 42.8 % corporate fee |

> **Basis note on the self-hosted rows.** `cost-matrix.md` §5's input grid is
> recovered by inverting METHODOLOGY §6's blended identity, which uses the **S1**
> (interactive) output rate, while §3's output grid is **S4** (max throughput, no
> SLO). Annotation is an offline batch workload with no latency SLO, so S4 is the
> right output basis — but combining an S1-derived input figure with an S4 output
> figure is a **mixed basis**, and the self-hosted rows below should be read as
> ±20 % rather than exact. The alternative — using the §4 blended figure straight
> — *overstates* annotation slightly, because the blend assumes 75 % input while
> this workload is 88.7 % input. Both are shown for Kimi-K3 as a cross-check.

**T1 — gold output, one teacher call per example (100,000 examples):**

| Teacher | $/example | **$/100k** | Batch (50 %) | Notes |
|---|---:|---:|---:|---|
| GPT-6 Astra | $0.0656 | **$6,560** | $3,280 | Matches doc 00 §4.3(a) exactly |
| Claude Fable 5.1 | $0.0656 | **$6,560** | $3,280 | |
| Claude Opus 5 | $0.0328 | **$3,280** | $1,640 | |
| GPT-5.6 Sol | $0.02624 | **$2,624** | $1,312 | |
| GPT-5.6 Terra | $0.01414 | **$1,414** | $707 | |
| **Gemini 3.8 Flash** | $0.00492 | **$492** | **$246** | Cheapest frontier teacher by 5× |
| GPT-5.6 Luna | $0.00141 | **$141** | $71 | Too small to be a useful *teacher*; a fine judge |
| **Kimi-K3** self-hosted (8×B300, `low`) | $0.00702 | **$702** | n/a | Blended-basis cross-check: $0.01074 → $1,074 |
| **DeepSeek-V4.1-Flash** self-hosted (2×B200, `low`) | $0.00090 | **$90** | n/a | |
| **Qwen3.8-27B** self-hosted (1×B300, `low`) | $0.00026 | **$26** | n/a | |

**T2 — rubric grade.** Judge input = 4,000 (prompt) + 512 (response) + 300
(rubric) = 4,812; output = 300 (per-criterion verdicts + rationale).

| Judge | $/example | **$/100k** | Batch |
|---|---:|---:|---:|
| GPT-5.6 Sol | $0.02525 | $2,525 | $1,262 |
| Claude Opus 5 | $0.03156 | $3,156 | $1,578 |
| Claude Haiku 4.5 | $0.00631 | **$631** | $316 |
| Gemini 3.8 Flash | $0.00473 | **$473** | $237 |
| GPT-5.6 Luna | $0.00132 | **$132** | $66 |
| Qwen3.8-27B self-hosted (B300 `low`) | $0.00027 | **$27** | n/a |

**T3 — pairwise preference, both orders (mandatory, §2.4).** Judge input =
4,000 + 2×512 + 300 = 5,324; output = 300; **×2 for order swap**.

| Judge configuration | $/example | **$/100k** |
|---|---:|---:|
| Single GPT-5.6 Sol, both orders | $0.05459 | **$5,459** |
| Single Claude Opus 5, both orders | $0.06824 | $6,824 ⚠️ |
| **PoLL: Haiku 4.5 + Gemini 3.8 Flash + GPT-5.6 Luna, all both orders** | $0.02673 | **$2,673** |
| PoLL, all three via batch APIs | $0.01337 | **$1,337** |
| Qwen3.8-27B self-hosted, both orders | $0.00058 | **$58** |

**Read the PoLL row.** Three disjoint-family judges, each run in both orders — six
judgements per example — costs **less than half** of one mid-tier judge run twice,
and carries less intra-model bias [[src](https://arxiv.org/abs/2404.18796)]. There
is no configuration in which a single expensive judge is the right default.

**Rejection sampling (§3.3), k=8, one filter pass:**

| Setup | Arithmetic | **$/100k** |
|---|---|---:|
| GPT-6 Astra, k=8, **no** prompt cache | 8 × $0.0656 | $52,480 |
| GPT-6 Astra, k=8, **100 % prompt-cache hit on the 7 repeats** (cache-optimistic) | $0.04 + 7×$0.004 + 8×$0.0256 | **$27,280** |
| Kimi-K3 self-hosted, k=8 via `n=8` (one prefill) | $0.00517 + 8×$0.00185 | **$1,995** |
| Qwen3.8-27B self-hosted, k=8 via `n=8` | $0.000184 + 8×$0.0000782 | **$81** |
| + filter pass (Qwen3.8-27B judge over 8 candidates) | ~8× T2 self-hosted | +$214 |

⚠️ **Two corrections to this section, 2026-09-19 fact-check.**
> (i) The T3 **single Claude Opus 5, both orders** cell read `$0.06412 / $6,412`. The
> stated inputs give `2 × (5,324 × $5 + 300 × $25) / 1e6 = $0.06824` → **$6,824**
> (recomputed with `python3`; Opus 5 at **$5 in / $25 out** confirmed live at
> [[src](https://claude.com/pricing)]). The PoLL conclusion is unaffected — PoLL
> ($2,673) is still under half of single-Sol ($5,459) and now 39 % of single-Opus-5.
> (ii) The rejection-sampling row above was **labelled** "90 % prompt-cache hit" but
> its arithmetic charges the **cached** rate ($1/MTok) on all seven repeats, i.e. a
> **100 %** hit. It is relabelled rather than repriced, because the prose below
> reads it as the cache-optimistic bound. At a *true* 90 % hit the row is
> `$0.04 + 7×(0.1×$0.04 + 0.9×$0.004) + 8×$0.0256` = **$29,800**, and the
> self-hosted advantage becomes **14.9×**, not 13.7×. Every other cell in §6.1 was
> recomputed and reproduces exactly.

**The structural point.** A self-hosted teacher amortises the 4,000-token prefill
across all `k` samples in a single request; an API charges input per request unless
a cache hits. Rejection sampling is therefore **13.7× cheaper self-hosted** than
even the cache-optimistic frontier row, and the ratio widens with `k`. Since
rejection-sampling SFT is the cheapest rung that produces a model (§1.2 rung 2),
this is a first-order platform-economics fact, not a detail.

**Human labels (§4.4), per label:**

| Label | Crowd @ $11.42–$17.14/h loaded | SME @ $75–$150/h ⚠️ assumption |
|---|---:|---:|
| Binary flag | $0.10–$0.14 | — |
| Rubric grade, enterprise prompt (12 min) | $2.28–$3.43 | $15.00–$30.00 |
| Gold rewrite (20 min) | $3.81–$5.71 | $25.00–$50.00 |
| **Adjudication (10 min)** | $1.90–$2.86 | **$12.50–$25.00** |

### 6.2 Worked budget — 100,000 examples, one loop iteration

Three scenarios. All `est.`; all exclude GPU idle (§6.4) and the training cost of
doc 00 §4.3(b).

**Scenario A — open-weights loop (the recommended default, §5.5):**

| Line | Method | Cost |
|---|---|---:|
| T5 structured labels, 100k | Qwen3.8-27B self-hosted, short prompts | ~$10 |
| T1 gold outputs, 100k | Kimi-K3 self-hosted (8×B300 `low`) | $702 |
| T2 rubric filter, 100k | Qwen3.8-27B self-hosted judge | $27 |
| Semantic dedup pass | Embedding + clustering over 451M tokens | ⚠️ not priced this session; ~$10²–10³ |
| **H1 judge calibration**, 1,000 SME adjudications (5 slices × 200) | §2.5 sizing, SME rate | **$12,500–$25,000** |
| **H4 disagreement adjudication**, 2,000 (2 % of traffic) | Crowd rate, enterprise prompt | **$3,800–$5,720** |
| **Total** | | **$17,000–$31,500** |
| — of which human | | **96–98 %** |

**Scenario B — frontier teacher (per-tenant exception, §5.5):**

| Line | Method | Cost |
|---|---|---:|
| T1 gold outputs, 100k | GPT-6 Astra, **Batch API** | $3,280 |
| T2 rubric grading, 100k | Claude Opus 5 batch (cross-family judge, §2.8 rule 1) | $1,578 |
| **H1 judge calibration**, 1,000 SME | | $12,500–$25,000 |
| **H4 adjudication**, 2,000 crowd | | $3,800–$5,720 |
| **Total** | | **$21,200–$35,600** |
| — of which human | | **77–86 %** |

**Scenario C — preference loop on shadowed traffic (§3.4 source 1):**

| Line | Method | Cost |
|---|---|---:|
| Candidate responses, 100k | The student itself, on the dev endpoint | ~$26 (Qwen3.8-27B self-hosted) |
| Incumbent responses, 100k | Already paid by the customer in production — **free to the loop** | $0 |
| T3 pairwise, both orders, PoLL ×3 batch | §6.1 | $1,337 |
| **H4 adjudication of panel disagreements**, ~5,000 | Crowd | $9,500–$14,300 |
| **Total** | | **$10,900–$15,700** |

**Three conclusions from the arithmetic:**

1. **Model annotation is a rounding error; human adjudication is the budget.**
   Even the most expensive frontier-teacher line ($3,280) is **26 % of** the
   cheapest human line ($12,500) — ⚠️ *corrected 2026-09-19*: that is just **over**
   a quarter ($12,500 / 4 = $3,125), not under one. The order-of-magnitude point
   stands; the phrase did not. Doc 00 §4.3 asserted that annotation is "the
   least of the three" one-time costs and that the intuition that it dominates is
   wrong — that is right for the *model* half and exactly wrong for the human half.
   **Doc 00's Open Question #10 is now answerable in shape if not in price: human
   adjudication is 77–98 % of annotation spend.**
2. **Against doc 00 §4.3's amortisation table, a $17k–$36k iteration pays back at
   roughly 1–4 billion blended tokens** replaced. ⚠️ *Recomputed 2026-09-19*:
   at doc 00 §4.3's savings of **$16.56/1M** vs GPT-6 Astra and **$8.25/1M** vs
   Claude Opus 5, a $17k–$36k iteration needs **1.03–2.17 B** tokens (Astra) or
   **2.06–4.36 B** tokens (Opus 5). The parenthetical previously cited here —
   "0.6B for GPT-6 Astra, 1.21B for Opus 5" — is doc 00's **$10k** one-time row
   ([`00-goal-and-problem-statement.md` §4.3](00-goal-and-problem-statement.md)),
   which is *below* this document's own $17k–$36k iteration cost and must not be
   quoted for it. A customer under ~600k requests/quarter should still be sold the
   eval harness and a tier-down, not the loop — doc 00 §4.3's qualification filter,
   unchanged and now better supported.
3. **The lever that moves the budget is the human sampling policy, not the teacher
   choice.** Halving H1+H4 saves more than switching from GPT-6 Astra to a
   self-hosted Kimi-K3. That is why §4.2 (which examples a human sees) is the
   highest-leverage design decision in this entire document.

### 6.3 Batch APIs and cached-token pricing — and where they interact badly

Both major vendors discount asynchronous work by 50 %, and annotation is
asynchronous by nature, so **every teacher and judge call that is not in a user's
critical path should go through a batch API**. The operational parameters:

| | OpenAI Batch | Anthropic Message Batches |
|---|---|---|
| Discount | **50 %** vs synchronous [[src](https://developers.openai.com/api/docs/guides/batch)] | **50 %** of standard prices [[src](https://platform.claude.com/docs/en/build-with-claude/batch-processing)] |
| Window | "within 24 hours (and often more quickly)"; completion window "can only be set to `24h`" [[ibid.](https://developers.openai.com/api/docs/guides/batch)] | "most batches finishing in less than 1 hour"; results at completion **or after 24 hours, whichever comes first**; batches **expire** if not done in 24 h [[ibid.](https://platform.claude.com/docs/en/build-with-claude/batch-processing)] |
| Size cap | **50,000 requests**; input file **200 MB** [[ibid.](https://developers.openai.com/api/docs/guides/batch)] | **100,000 requests or 256 MB**, whichever first [[ibid.](https://platform.claude.com/docs/en/build-with-claude/batch-processing)] |
| Result retention | — | **29 days** [[ibid.](https://platform.claude.com/docs/en/build-with-claude/batch-processing)] |
| Rate limits | Separate pool, "substantially more headroom" [[ibid.](https://developers.openai.com/api/docs/guides/batch)] | Separate limits; "processing may be slowed down based on current demand" [[ibid.](https://platform.claude.com/docs/en/build-with-claude/batch-processing)] |
| Endpoints | responses, chat/completions, embeddings, completions, moderations, images [[ibid.](https://developers.openai.com/api/docs/guides/batch)] | Vision, tool use incl. server tools, prompt caching, extended thinking all supported [[ibid.](https://platform.claude.com/docs/en/build-with-claude/batch-processing)] |
| SLA | None beyond the 24 h window; unfinished requests cancelled, only completed ones charged [[ibid.](https://developers.openai.com/api/docs/guides/batch)] | Expired requests "You will not be billed for these requests" [[ibid.](https://platform.claude.com/docs/en/build-with-claude/batch-processing)] |

**The interaction that costs money if ignored.** Batch and prompt caching pull in
opposite directions: a cache entry is ephemeral, and a batch may not run for an
hour. Anthropic's docs say so explicitly — *"Because batches can take longer than 5
minutes to process, consider using the 1-hour cache duration with prompt caching
for better cache hit rates when processing batches with shared context"* — and
separately disallow `max_tokens: 0` cache pre-warming inside a batch, "because an
ephemeral cache entry written during batch processing would likely expire before
the follow-up request runs"
[[src](https://platform.claude.com/docs/en/build-with-claude/batch-processing)].

Design rules that follow:
1. **Sort every batch by prompt-stack hash** before submission, so shared prefixes
   are contiguous and a cache entry is reused while it is warm.
2. **Use the 1-hour cache TTL for batch jobs**, and account for the cache-*write*
   premium — Anthropic's writes are priced *above* base input (Opus 5 $6.25/MTok,
   Fable 5.1 $12.50/MTok [[src](https://claude.com/pricing)]), so a one-shot
   prompt written to cache and never re-read is a net loss.
3. **Do not *budget* batch and cache savings together — even though they do
   stack.** ⚠️ *Corrected 2026-09-19*: the earlier wording ("never assume batch +
   cache discounts multiply") is contradicted by the primary source. Anthropic's
   batch doc states *"The pricing discounts from prompt caching and Message Batches
   can stack, providing even greater cost savings when both features are used
   together"* — but immediately adds that *"because batch requests are processed
   asynchronously and concurrently, cache hits are provided on a best-effort
   basis"*, with *"cache hit rates ranging from 30% to 98%, depending on their
   traffic patterns"*
   [[src](https://platform.claude.com/docs/en/build-with-claude/batch-processing)].
   The saving is real but **not schedulable**: the §6.1 batch columns assume
   cache-less pricing, and any cache saving on top is upside, not budget.
4. **Chunk to 50,000 requests** (the lower of the two caps) so the same job shape
   works on either vendor.
5. **Retry expiry, don't re-run the job.** Both vendors cancel rather than charge
   for expired requests; §7.3's idempotency makes the retry a no-op for the
   already-completed rows.
6. **Anthropic's 29-day result retention is a data-loss deadline.** Pull results
   into the platform's own store on completion, never treat the vendor as storage.

**US-only inference and fast modes are surcharges, not features, for this
workload.** Anthropic prices US-only inference at a **1.1× multiplier** and Opus 5
"Fast Mode" at **2× for up to 2.5× faster speeds** [[src](https://claude.com/pricing)].
Annotation has no latency requirement; both should be off by default and the
US-only multiplier enabled only where a tenant's residency policy demands it (doc
00 §8.2).

### 6.4 Where annotation sits in the platform's cost of goods

Doc 00 §4.5's conclusion — "the platform's own cost of goods is dominated by idle
GPU, not by tokens" — has a direct and pleasant consequence here:

**Annotation is the ideal backfill workload for idle GPUs.** It has no latency
SLO, it is perfectly preemptible, it is throughput-bound rather than
concurrency-bound, and it runs at the S4 operating point, which the cost matrix
prices **1.0–4.3× cheaper** per output token than the S1 interactive point,
strongly pair-dependent [[src](../matrix/cost-matrix.md) §3 vs §2]:
Marlin-2B/B300 **1.00×** ($0.022 S4 vs $0.022 S1), Qwen3.8-27B/B300 **1.08×**
($0.1527 vs $0.1649), DeepSeek-V4.1-Flash/B200 **1.30×** ($0.355 vs $0.462),
Kimi-K3/B300 **2.05×** ($3.61 vs $7.3914), Kimi-K3/B200 **4.28×** ($3.814 vs
$16.339).

⚠️ **Corrected 2026-09-19.** This paragraph previously claimed "2–6× cheaper" and
cited "Qwen3.8-27B on B300: $0.1527/1M at S4 vs $0.3095–$0.9 at S1". Both halves
were wrong against the cited file: **$0.3095 is Qwen3.8-27B's S4 `high`-tier cell**
(cost-matrix §3), not an S1 cell — its S1 range is **$0.1649–$0.3343** (§2) — and
**$0.9 appears nowhere in the matrix** for this pair. The corrected spread is
1.0–4.3×, and for the document's own default judge/teacher (Qwen3.8-27B on B300)
the S4 saving is only **8 %**. **The backfill conclusion survives the correction**,
because it rests on doc 00 §4.5's *idle GPU* — capacity already paid for — not on
the S4-vs-S1 spread. A fleet sized for a customer's peak serving load has, by
construction, spare capacity at trough, and annotation is exactly the job that
fills it.

The design implication for doc 01/06: **the annotation scheduler must be a
lowest-priority tenant of the same GPU pool as serving**, with preemption on
serving-queue pressure, not a separate reserved cluster. This is the mechanism that
turns doc 00 §4.5's idle-GPU problem into doc 00 §4.3's annotation line item at
near-zero marginal cost.

### 6.5 Decision rules for §6

1. **Batch everything.** 50 % off, both vendors, no quality difference.
2. **Panel of small judges over one big judge** — cheaper *and* less biased.
3. **Self-host the teacher when `k` > 1.** Rejection sampling's prefill
   amortisation is a 13.7× effect.
4. **Budget humans first and models second**, because humans are 77–98 % of it.
5. **Never quote an annotation budget without the human line.** A proposal that
   says "annotation: $3,280" is off by an order of magnitude and will be caught.
6. **Run annotation on idle serving capacity**, at S4, preemptible.
7. **Re-price at every judge/teacher version change** — the vendor price tables
   fetched here carry scheduled increases (Gemini 3.8 Flash doubles on 2027-01-01
   [[src](https://ai.google.dev/gemini-api/docs/pricing)]).

---

## 7. Pipeline design for the platform

### 7.1 The annotation DAG

Annotation is not a job; it is a directed graph of *idempotent, versioned
transforms* over an append-only label store. Nodes are pure functions of
(input rows, config version); edges carry row sets.

```
                    ┌─── split_assignment (train/dev/test, by stable entity) ───┐
                    │            REFUSES to emit rows where split = test        │
                    v                                                           │
  [S2 traces] → SELECT → REDACT → ─┬─→ T5 classify ──────────────────┐          │
   (sampled)   (policy)  (Presidio │                                 │          │
                         + egress  ├─→ T7 join outcome signals ──────┤          │
                         boundary) │                                 │          │
                                   ├─→ T1 teacher generate (k=1..n) ─┤          │
                                   │        │                        │          │
                                   │        v                        │          │
                                   │   VERIFY (schema → tool replay → regex)    │
                                   │        │                        │          │
                                   │        v                        │          │
                                   │   T2 judge panel (both orders) ─┤          │
                                   │        │                        │          │
                                   │        ├─ agree → accept        │          │
                                   │        └─ disagree ──→ [HUMAN QUEUE] ──────┤
                                   │                             │              │
                                   └─→ T3 pairwise (both orders) ─┘              │
                                            │                                   │
                                            v                                   │
                              DEDUP (exact → semantic) → FILTER → PACK ──────────┘
                                            │
                                            v
                                   [S4 versioned dataset]
```

Six properties this shape enforces:

| Property | Mechanism |
|---|---|
| **Test split can never be annotated** | `split_assignment` is an input filter on the *source* node and a hard failure, not a warning (§3.7) |
| **Redaction is an edge, not a stage** | The REDACT node is the egress boundary; any node that calls an external teacher must consume only REDACT's output (doc 00 §8.2) |
| **Verifiable before judged** | VERIFY runs before the judge panel and short-circuits it (§2.8 rule 4) |
| **Human queues are a node, not a side-channel** | Adjudications re-enter the graph as first-class labels with their own provenance |
| **Dedup runs last** | Dedup after labelling, so the label cost of a duplicate is paid once but the *choice* of which duplicate survives can be quality-weighted |
| **Every node is resumable** | §7.3's idempotency key |

### 7.2 Schema

One append-only `annotations` table (plus the artifacts it references). The rule
is: **provenance lives on the record, never only in a job log**, because the job
log is not what S5 reads.

```jsonc
{
  // identity
  "annotation_id":    "ann_01J...",          // ULID
  "idempotency_key":  "sha256:...",          // §7.3
  "tenant_id":        "t_acme",
  "task_id":          "support_triage_v2",

  // what was annotated
  "trace_id":         "tr_...",              // FK into the trace store (doc 02/observability)
  "entity_id":        "user_8841",           // the split-assignment unit; NEVER the row id
  "split":            "train",               // train | dev | test  (test is never annotated)
  "prompt_stack_hash":"sha256:...",          // doc 00 §2.2 — invalidation trigger
  "input_digest":     "sha256:...",          // redacted input actually sent

  // the label
  "label_type":       "T1|T2|T3|T4|T5|T6|T7|T8",
  "value":            { /* type-specific; see below */ },
  "confidence":       0.82,                  // judge/annotator confidence, if any

  // provenance — the legally and scientifically load-bearing block
  "generator": {
    "kind":           "teacher|judge|verifier|human|derived",
    "model":          "moonshotai/Kimi-K3",  // or "human:annotator_4471"
    "model_version":  "sha:dba1be0a…",       // checkpoint sha or vendor model id+date
    "decoding":       { "temperature": 0.0, "top_p": 1.0, "seed": 7 },
    "prompt_template_id": "tmpl_teacher_rewrite@3.1.0",
    "rubric_version": "rub_triage@2.4.0",    // null for T1
    "panel_member":   1,                     // for PoLL; null otherwise
    "order":          "AB",                  // for T3; the other order is a sibling record
    "legal_basis":    "open_weights_apache2" // §5.6 — per-tenant teacher policy
  },

  // lineage and review
  "derived_from":     ["ann_..."],           // e.g. an adjudication supersedes a judge score
  "superseded_by":    null,                  // append-only: corrections add rows, never UPDATE
  "review_state":     "accepted|queued|adjudicated|rejected",
  "cost_usd":         0.0070,                // per-record actual, for §6 reconciliation
  "created_at":       "2026-09-19T11:04:22Z"
}
```

Type-specific `value` shapes:

| Type | `value` |
|---|---|
| T1 | `{ "output": str, "stop_reason": str, "tool_calls": [...] }` |
| T2 | `{ "criteria": [{"id":"c1","score":1,"rationale":str}], "aggregate": float, "passed": bool }` |
| T3 | `{ "a_ref": ann_id, "b_ref": ann_id, "winner": "a"\|"b"\|"tie", "margin": float }` |
| T4 | `{ "rationale": str, "critique": str, "revision": str }` |
| T5 | `{ "intent": str, "difficulty": 1..5, "safety_category": str, "slices": [str] }` |
| T6 | `{ "expected_calls": [...], "verified_by": "replay"\|"schema"\|"judge" }` |
| T7 | `{ "signal": "ticket_resolved", "value": true, "lag_seconds": 3600 }` |
| T8 | `{ "caption": str, "spans": [{"t0":12.4,"t1":18.9,"label":str}], "frame_budget": 240, "fps_effective": 0.40 }` |

**Three schema rules that are not negotiable:**
1. **Append-only.** A correction is a new row with `derived_from` set and the old
   row's `superseded_by` written; nothing is ever updated in place. This is what
   makes a dataset version reproducible and an audit answerable.
2. **`generator.legal_basis` is mandatory and enumerated.** A null fails the write.
   This is §5.6's audit trail, and it is the field a lawyer will ask for.
3. **Both orders of a T3 comparison are sibling records**, not one record with two
   fields — so that an order-level disagreement is queryable rather than silently
   averaged (§2.4).

### 7.3 Versioning and idempotency

**Idempotency key.** Every annotation node computes:

```
idempotency_key = sha256(
    node_id            ‖ input_digest        ‖ prompt_template_id@version
  ‖ rubric_version     ‖ generator.model     ‖ generator.model_version
  ‖ canonical(decoding_params)               ‖ legal_basis
)
```

Insert-if-absent on that key. Consequences, all of which the platform needs:
- **Resumability.** A 100k-example batch that dies at 60 % resumes by re-submitting
  everything; 60k rows are no-ops. Given the batch-expiry behaviour in §6.3 this is
  not a nicety.
- **Cost control.** Re-running a DAG after changing *one* node re-pays only that
  node's downstream cone.
- **Honest invalidation.** Changing the rubric changes the key, so old scores
  survive as history but do not masquerade as current. A rubric edit that silently
  overwrote scores would make every historical eval uninterpretable.

**What a version change invalidates** — the table the platform must implement as
actual cascade logic, not documentation:

| Changed | Invalidates | Survives |
|---|---|---|
| Prompt template | All labels produced with it | The traces |
| **Rubric** | All T2 scores, all judge-agreement statistics, **the eval gate's thresholds** | T1, T3, human adjudications keyed to criteria that did not change |
| Judge model or version | All T2/T3 scores from that judge; **§2.5 calibration must re-run** | Human labels |
| Teacher model or version | Nothing already produced — but the dataset is now *mixed*, and the mix must be recorded | — |
| Customer's prompt stack (`prompt_stack_hash`) | **The parity claim itself** (doc 00 §2.2) | Historical labels, as history |
| Split assignment | Everything downstream of the moved rows — treat as a new dataset version | — |

**Dataset versioning.** A dataset version is a *manifest*: an ordered list of
`annotation_id`s plus the set of config versions that produced them plus the split
assignment digest. Manifests are immutable and cheap; the rows they point at are
already immutable. This makes "reproduce the dataset that trained checkpoint 47" a
lookup rather than an archaeology project — which doc 00 §1.2 lists as S5's primary
failure mode.

### 7.4 Review UI — what to build and what to adopt

**Adopt** the annotation surface (§4.3). **Build** only these four things, because
they are the ones no vendor ships and they are the ones that carry the product:

1. **The queue router.** §4.2's budget split across disagreement / stratified /
   random, implemented as a policy that emits *into* whichever surface the tenant
   uses. This is where the intelligence is.
2. **The disagreement diff view.** Side-by-side student vs incumbent on a real
   production request, with the judge's per-criterion verdicts and the ability to
   adjudicate in one click. Doc 00 §5.6 item 3 says this does more selling than the
   statistics; it is also the H4 queue's working surface. Build it once, use it for
   both.
3. **Provenance display.** Every score shows which model, which version, which
   rubric version, under which legal basis. A reviewer who cannot see that the
   score came from a judge they do not trust cannot correct for it.
4. **The judge-noise-floor banner.** Doc 00 §5.1: "if judge-human agreement is
   80 %, a judge-measured 2-point delta is inside the judge's own noise floor — a
   fact that must be stated on the customer's dashboard, not buried." That is a UI
   requirement with a number behind it, and it belongs next to every delta.

Not to build: the labelling widgets, the keyboard shortcuts, the media player, the
bounding-box tool, SSO. Label Studio Enterprise and Braintrust already have them
(§4.3).

### 7.5 Quality dashboards

Five panels, each tied to a failure mode already named in this document:

| Panel | Metric | Fires when | Failure it detects |
|---|---|---|---|
| **Judge validity** | κ / Krippendorff α **and per-class recall** vs the human gold set, **per slice**, with CIs | Agreement below the agreed floor on any slice | §2.5, and the κ paradox |
| **Annotator quality** | Per-annotator α vs consensus; embedded-gold accuracy; throughput | Annotator below floor | §4.5 |
| **Pipeline health** | Per-slice rejection-sampler survival rate; verify-stage failure rate; dedup drop rate | Survival <20 % on any slice | §3.3's easy-slice curriculum |
| **Diversity / collapse** | Distinct-n, embedding-cluster entropy, refusal rate, output-length distribution, **round over round** | Narrowing before scores drop | Doc 00 §8.3 |
| **Cost reconciliation** | Actual `cost_usd` summed per label type vs the §6.2 budget; $/accepted-label | >20 % over | §6, and it is the number that tells you the loop is uneconomic *before* the quarter ends |

The cost panel is the one most often omitted and the one a platform operator needs
most: $/accepted-label (not $/call) is the unit that determines whether a tenant's
loop is profitable.

### 7.6 OSS building blocks — the buy/build call

| Component | Adopt | Why / caveat |
|---|---|---|
| **Synthetic-data & AI-feedback pipelines** | **Bespoke Curator** [[src](https://github.com/bespokelabsai/curator)] | Actively developed — README news entries dated **2026-06-09** (Fireworks managed-SFT integration) and **2026-03-14** (Tinker integration, "curated data to a LoRA fine-tuned model in a few lines"); batch-API support for OpenAI/Anthropic/Gemini; structured outputs; caching and fault recovery; code execution backends. Directly covers §3 and §6.3 |
| | **distilabel** [[src](https://github.com/argilla-io/distilabel)] — *with a warning* | "framework for synthetic data and AI feedback… based on verified research papers", and the research-method-per-step design is exactly §3.1's taxonomy. **But its README opens: "The original authors have moved on to other projects. A group of community members have recently joined the GitHub project as collaborators to maintain the project."** Treat as reference implementation, not as a dependency |
| **Human annotation surface** | **Argilla** (joining Hugging Face) [[src](https://argilla.io/)]; **Label Studio / HumanSignal** for video [[src](https://humansignal.com/pricing/)]; **Langfuse annotation queues** for trace-attached review [[src](https://langfuse.com/docs/evaluation/evaluation-methods/annotation)] | Adopt the one closest to where the traces already live; §4.3 |
| **Judge prompt optimisation** | **DSPy 3.4.0b1** [[src](https://dspy.ai/current/)] | Signatures / Modules / Optimizers; optimizers named on the page include **GEPA** (reflective prompt evolution), **MIPROv2** (instructions + demos), **BetterTogether** (fine-tuning + prompt optimisation), the **BootstrapFewShot** family, COPRO, SIMBA, Ensemble, KNN. They "tune prompts and demonstrations against defined metrics"; a production example on the page cites **62 % → 89 %** on the same model. **Use it to optimise the judge prompt against the human gold set** — that is a metric DSPy can legitimately optimise, and it is a cheaper lever than a bigger judge. ⚠️ Optimising a judge against gold risks overfitting the gold set; hold out a calibration split (§2.7) |
| **Scoring / eval harness** | **Inspect** (UK AI Security Institute + Meridian Labs) [[src](https://inspect.aisi.org.uk/)] | Dataset / Solver / Scorer decomposition matches §7.1; built-in scorers `includes()`, `match()`, `pattern()`, `answer()`, `exact()`, `f1()`, `choice()`, `math()`, `perplexity()`, `target_perplexity()`, and model-graded `model_graded_qa()` / `model_graded_fact()` [[src](https://inspect.aisi.org.uk/scorers.html)]; multiple scorers with reducers; human-in-the-loop via Tool Approval, Tool Result Review, Intervention and a **Human Agent** type [[src](https://inspect.aisi.org.uk/)]. **The programmatic scorers are §2.8 rule 4 in library form.** ⚠️ Licence not stated on the pages fetched |
| **PII redaction** | **Presidio** — analyzer, anonymizer, image-redactor, structured [[src](https://github.com/data-privacy-stack/presidio)] | MIT; supports custom recognisers, NER + regex + checksum, multiple languages, image and DICOM redaction, Python/PySpark/Docker/K8s. **Ownership moved from `microsoft/` to `data-privacy-stack/`**; its own warning — "no guarantee that Presidio will find all sensitive information" — means it is a layer, not a control |
| **Expert data as a service** | Snorkel / Toloka / Surge / Scale (§4.3) | Buy, do not build a labelling org (doc 00) |
| **Teacher inference at scale** | vLLM / SGLang on the platform's own fleet ([`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)) | The `n=k` prefill amortisation of §6.1 is the reason |

**What not to adopt:** any vendor's end-to-end annotation *product* as a
load-bearing dependency. Doc 00 §8.6 counts three sunsets in twelve months; §4.3
and §7.6 add three more ownership changes in this narrower space alone — Argilla
into Hugging Face, Presidio out of Microsoft, distilabel's original authors gone.
The pattern is not vendor-specific; it is what this layer of the market does.
Own the schema (§7.2) and the DAG (§7.1); rent everything that plugs into them.

---

## 8. Video and multimodal annotation specifics

Doc 00 §3.4 calls video-understanding distillation "the part of the thesis with
the thinnest public support" and §5.5 says everything gets harder. This section
adds what is specific to *annotating* video, and finds one asymmetry that is both
a cost problem and a correctness problem, with the same fix.

### 8.1 The frame-budget asymmetry — the central fact

The teacher and the student do not see the same video, and they are not billed the
same way.

| | **Teacher (Gemini 3.8 Flash)** | **Student (Marlin-2B)** |
|---|---|---|
| Sampling | **1 frame/second by default**, `fps` customisable (e.g. 0.5) [[src](https://ai.google.dev/gemini-api/docs/video-understanding)] | **240-frame hard cap**, whatever the clip length ([`models/marlin2b/README.md`](../models/marlin2b/README.md) §9) |
| Token cost | **~100 tokens per second of video** at default (low) media resolution [[ibid.](https://ai.google.dev/gemini-api/docs/video-understanding)] | **~23,560 prefill tokens, flat**, Path A ([`models/marlin2b/architecture.md` §6.3](../models/marlin2b/architecture.md)) ⚠️ the repo itself prints **23,560** in [`marlin2b/README.md`](../models/marlin2b/README.md) §9 and method item 2 but **23,520** in its open-questions item 2 — and the quoted 1.914× Path-A/B ratio matches 23,520/12,288, not 23,560/12,288 (1.917×). 0.2 %, moves nothing here, but the repo should settle it |
| Scaling with clip length | **Linear** | **Constant** |
| Max length | 3 h at low media resolution, 1 h at high (1M-context models) [[ibid.](https://ai.google.dev/gemini-api/docs/video-understanding)] | Unbounded in wall-clock, but effective fps collapses |
| Effective fps at 10 min | 1.0 | **0.40** |
| Effective fps at 1 hour | 1.0 | **0.067** |

**Cost of teacher captioning, 100,000 clips** (`est.`, Gemini 3.8 Flash
$0.75 in/$3.75 out, ~200-token prompt, 400-token caption):

| Clip length | Video tokens | $/clip | **$/100k** | Batch (50 %) |
|---|---:|---:|---:|---:|
| 2 min @ 1 fps | 12,000 | $0.01065 | **$1,065** | $533 |
| 10 min @ 1 fps | 60,000 | $0.04665 | **$4,665** | $2,333 |
| 1 hour @ 1 fps | 360,000 | $0.27165 | **$27,165** | $13,583 |
| **10 min @ 0.40 fps (student-matched)** | 24,000 | **$0.01965** | **$1,965** | **$983** |

**Cost of the student, for comparison:** the repo measures Marlin-2B at
**$0.432 per 1,000 two-minute captions on B200** and $0.461 on H100, with reserved
H200 at **$0.38/1k** ([`models/marlin2b/README.md`](../models/marlin2b/README.md)
§5) — i.e. **$43 per 100,000 captions**, *independent of clip length*. Against the
10-min @ 1 fps teacher row that is a **108× ratio**; at 1 hour, **629×**. This is
the most favourable distillation economics anywhere in the programme, and doc 00
§3.4's intuition ("the frame budget dominates cost… so the economics are unusually
favourable *if* quality holds") is confirmed arithmetically.

### 8.2 The correctness problem hiding inside the cost problem

A teacher sampling a 10-minute clip at 1 fps sees **600 frames**. The student sees
**240**. If the teacher's caption references an event visible only in a frame the
student's sampler skipped, the training target is **physically unachievable** —
the student is being taught to hallucinate, and the loss will happily teach it to.
This is a *silent* failure: nothing errors, the label looks good, and the student
learns to confabulate plausible events.

**The fix, and it is cheap because Gemini exposes the knob:** set the teacher's
`fps` argument to the student's *effective* fps for that clip
[[src](https://ai.google.dev/gemini-api/docs/video-understanding)], i.e.
`fps_teacher = min(fps_native, 240 / clip_duration_seconds)`. The 10-minute row
above shows the consequence: cost falls from $4,665 to $1,965 per 100k **and** the
label becomes achievable. Record `frame_budget` and `fps_effective` on every T8
record (§7.2) so a later audit can tell which labels were produced under a matched
budget and which were not.

⚠️ **TO BE VERIFIED** — two things this reasoning assumes:
1. That Gemini's frame *selection* at a given fps lands on the same frames
   Marlin-2B's sampler selects. It almost certainly does not (different decoders,
   different rounding). Matching *rate* narrows the gap; it does not close it. The
   rigorous version is to **extract the student's actual sampled frames and send
   those as images**, which both eliminates the mismatch and makes the teacher's
   input exactly reproducible — at the cost of losing the teacher's native video
   path. Measure both.
2. That ~100 tokens/second is per-frame-linear at reduced fps. The docs give the
   figure for default sampling; the per-frame rate at custom fps was not stated on
   the page fetched.

**This compounds a trap the repo already documents.** Marlin-2B's Path A vs Path B
ambiguity — 23,560 vs 12,288 prefill tokens, a **1.914× gap**, where
`qwen-vl-utils` bypasses the HF processor's `size` and a deployment can "silently
land on Path B", giving "the model half the frames or half the resolution it was
trained on — a silent quality regression, not an error"
([`models/marlin2b/README.md`](../models/marlin2b/README.md) §§ open questions) —
means **the annotation pipeline must pin and record the student's video
preprocessing path**, not just the model id. A dataset annotated against a Path A
student and served on a Path B student is mislabelled by construction.

### 8.3 What to annotate for video, and with what

| Label | Teacher option | Cost driver | Note |
|---|---|---|---|
| **Dense caption** | Gemini 3.8 Flash video, or a self-hosted VLM | Video tokens (input) | ShareGPT4Video's central finding: "using GPT4V to caption video with a naive multi-frame or frame-concatenation input strategy leads to less detailed and sometimes **temporal-confused** results", fixed by a **differential** captioning strategy (describe inter-frame *change*, not each frame) [[src](https://arxiv.org/abs/2406.04325)]. **Copy the differential strategy; do not concatenate frames and hope** |
| **Closed QA pairs** | Teacher writes both Q and A from the clip | One teacher call | LLaVA-Video-178K's composition — "detailed captioning, open-ended question-answering (QA), and multiple-choice QA" — is the template [[src](https://arxiv.org/abs/2410.02713)]. **Closed QA is the cheap-to-grade label**, which is what makes the eval affordable (doc 00 §5.5) |
| **Temporal grounding** (event → t0/t1) | Teacher with `MM:SS` timestamp prompting; Gemini supports MM:SS references and `start_offset`/`end_offset` clipping [[src](https://ai.google.dev/gemini-api/docs/video-understanding)] | Video tokens + careful prompting | The hardest and the one most likely to need humans — VTimeLLM exists because general video LLMs fail precisely here [[src](https://arxiv.org/abs/2311.18445)] |
| **Fine-grained temporal attributes** (order, frequency, motion magnitude) | Teacher, but with low confidence | — | TemporalBench: GPT-4o at **38.5 %**, ~30 points below humans, from ~2K human annotations → ~10K QA pairs [[src](https://arxiv.org/abs/2410.10818)]. **A teacher that scores 38.5 % is not a teacher for this label type** — it is a candidate generator whose output a human must adjudicate |
| **Long-context / referring reasoning** | Frontier only | Video tokens, large | LongVideoBench: 3,763 videos up to an hour, 6,678 human-annotated MCQs in 17 categories; "model performance on the benchmark improves only when they are capable of processing more frames" [[src](https://arxiv.org/abs/2407.15754)] — which is exactly what the student's 240-frame cap forbids |

**Open-weight video teachers, and the repo-internal option.** Qwen3.8-27B is a
vision-language model with genuine video support — `video_token_id` 248,057,
`<|video_pad|>` in the chat template, `temporal_patch_size: 2` (a 3-D patch-embed
conv, 2 frames per temporal patch) and **M-RoPE splitting rotary dimensions across
temporal/height/width axes, which "is what lets video frames carry a real temporal
position"** ([`models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md)
§§1.1, 2.3–2.4). It is **Apache-2.0** (§5.4), it is already in the repo's cost
matrix, and it therefore makes the *legally clean, logprob-capable* video teacher
for Marlin-2B.

⚠️ **TO BE VERIFIED — the number that blocks a video annotation budget.** I could
not determine **how many tokens Qwen3.8-27B consumes for a 240-frame clip**. Its
ViT runs **full bidirectional attention with no windowing**, which the repo notes
"is O(N²) in patches and becomes the dominant cost at high resolution"
([`models/qwen3827b/architecture.md` §2.4](../models/qwen3827b/architecture.md)),
so the answer is resolution-dependent and cannot be guessed. **Method to close it:**
run one 240-frame clip through vLLM and read the reported prefill token count —
the same procedure the Marlin-2B doc prescribes for its own Path A/B question. As
an order-of-magnitude placeholder only: *if* it were Marlin-like at ~23,560 tokens,
then at B300 `low` ($0.046/1M in, $0.1527/1M out) a 400-token caption costs
**$0.00115/clip → ~$115 per 100k** — roughly **4.6× cheaper than Gemini 3.8 Flash's
batch price for a 2-minute clip** ($533) and **9× cheaper than its list price**, with no
ToS exposure and full logprob access. That ratio is the
reason to measure the number rather than default to the API.

Tarsier2-7B is the published evidence that a **small open VLM can beat frontier
models at detailed video description**: +2.8 % F1 over GPT-4o and +5.8 % over
Gemini-1.5-Pro on DREAM-1K, **+8.6 % / +24.9 % in human side-by-side**, achieved
partly by "model-based sampling to automatically construct preference data and
applying DPO" [[src](https://arxiv.org/abs/2501.07888)]. Two things to take: a 7B
student *can* exceed a frontier teacher on a narrow video task (which is doc 00
§3.4's missing evidence, partially — it is not a *task-specific distillation at
parity on a customer task*, but it is the closest public analogue found); and the
preference-pair path (§3.4) is the one that got them there.

### 8.4 Human video annotation cost

Video-MME is the honest anchor for what a *good* video eval costs: 900 videos /
254 hours / 2,700 QA pairs built by "rigorous manual labeling by expert
annotators" — **3 human-authored items per video**
[[src](https://arxiv.org/abs/2405.21075)] (doc 00 §5.5). TemporalBench needed
**~2,000 high-quality human annotations** to yield ~10K QA pairs
[[src](https://arxiv.org/abs/2410.10818)]; LongVideoBench needed **6,678
human-annotated MCQs** across 3,763 videos
[[src](https://arxiv.org/abs/2407.15754)].

`est.`, at §4.4 rates:

| Deliverable | Volume | Crowd ($11.42–$17.14/h) | SME ($75–$150/h) ⚠️ |
|---|---|---:|---:|
| 3 QA items per 2-min clip, 500-clip gold eval | 1,500 items @ 25 min | **$7,140–$10,710** | $46,900–$93,800 |
| Temporal-boundary adjudication, 1,000 spans @ 15 min | 1,000 | $2,860–$4,290 | $18,800–$37,500 |
| Caption gold set, 200 clips @ 30 min | 200 | $1,140–$1,714 | $7,500–$15,000 |

**The conclusion doc 00 §9.2 already reached, now priced:** a video eval gold set
costs **$7k–$94k** depending on whether the labels need domain expertise, against
a *teacher* captioning bill of **$533–$1,965 per 100,000 clips** (§8.1, batch
tier). ⚠️ *Corrected 2026-09-19*: those two figures are **not the same volume** —
the human rows above are 200–1,500 items, the teacher figures are per 100k clips.
Matched on volume the asymmetry is far larger, not smaller: the teacher side of a
500-clip, 2-minute gold set is **~$5** (500 × $0.01065) against $7,140–$93,800 of
human time. The point is sharpened by the correction, not weakened. Video does not
change the shape of §6.2's finding; it sharpens it. Sequencing video second, with
its own eval budget, is the right call.

### 8.5 Video-specific failure modes

| Failure | Detection | Mitigation |
|---|---|---|
| **Teacher references frames the student cannot see** (§8.2) | Compare `fps_effective` on the T8 record against the teacher's sampling rate; sample-audit captions for events outside the student's frames | Match teacher `fps` to the student's effective fps; or send the student's actual frames as images |
| **Temporal confusion from frame concatenation** | Human spot-check for event-order errors in captions | Differential captioning [[src](https://arxiv.org/abs/2406.04325)] |
| **Preprocessing-path drift** (Path A vs Path B, 1.914×) | Assert the prefill token count at annotation time and at serve time; alert on mismatch | Pin the preprocessing path in the artifact (doc 00 §1.2's gate-twice rule, extended to the *input* pipeline) |
| **Teacher is bad at the label type** | Benchmark the teacher on a public proxy first (TemporalBench-style) before buying 100k labels | Route fine-grained temporal labels to humans; use the teacher only as a candidate generator |
| **Cost blowout on long clips** | Cost-per-accepted-label panel (§7.5) segmented by clip duration | Clip with `start_offset`/`end_offset` to the relevant window [[src](https://ai.google.dev/gemini-api/docs/video-understanding)] rather than sending the whole video |
| **Ambiguous ground truth** | Inter-annotator α on a video pilot *before* committing the budget | If α is low, the task is not evaluable as specified — renegotiate the rubric, not the annotator pool |

---

## Implications for the platform

**What to build** — the parts that are the product and that nobody sells:

1. **The queue router (§4.2).** The 60/25/15 split across disagreement,
   stratified and random queues. This one policy determines how many human labels
   the loop needs, and human labels are **77–98 % of the annotation budget**
   (§6.2). It is the highest-leverage component in this document.
2. **The annotation schema and its provenance block (§7.2).** `generator.model`,
   `model_version`, `rubric_version`, `legal_basis`, append-only. This is
   simultaneously the reproducibility mechanism (S5 can rebuild any dataset), the
   audit trail a lawyer will ask for (§5.6), and the thing that makes "judge ≠
   teacher" enforceable rather than aspirational.
3. **Idempotency keyed on the full config (§7.3)**, with real cascade-invalidation
   logic. A rubric edit that silently kept old scores makes every historical eval
   uninterpretable.
4. **The disagreement diff view (§7.4 item 2).** Student vs incumbent on a real
   production request, adjudicable in one click. Doc 00 §5.6 says this does more
   selling than the statistics; it is also the H4 working surface. One build, two
   jobs.
5. **The judge-validity dashboard (§7.5), reporting κ/α *and* per-class recall.**
   The κ paradox (§2.5) means an agreement-only dashboard will show 95 % while the
   judge catches 1 failure in 5. Print the judge's noise floor next to every delta.
6. **The judge cascade (§2.7)**, with one threshold escalating to a stronger judge
   and a second escalating to a human. This is the cost-control mechanism and the
   human-routing mechanism in one object.
7. **Per-tenant teacher policy, fail-closed (§5.6).** An annotation job naming a
   teacher outside the tenant's allowlist must not run. Paid-tier credential
   enforcement for Gemini is part of this.
8. **Frame-budget matching for video (§8.2).** `fps_teacher = min(native, 240/dur)`,
   recorded on every T8 record. Cuts the 10-minute-clip teacher bill by **58 %**
   ($4,665 → $1,965 per 100k — "halves" understated it) *and* removes an entire
   class of unachievable training targets.

**What to buy / adopt:**

- **Teacher and judge tokens from self-hosted open-weight models** —
  DeepSeek-V4.1-Flash (MIT) and Qwen3.8-27B (Apache-2.0) as defaults, Kimi-K3 when
  strength is needed and §2 of its licence is cleared. This is now a *three-way*
  argument: clean licence (§5.4), full logprob access (§3.2), and 13.7× cheaper
  rejection sampling (§6.1).
- **Batch APIs for every frontier call** — 50 % off on both vendors, no quality
  difference, and annotation has no latency requirement (§6.3).
- **Bespoke Curator** for the synthetic-data/AI-feedback pipeline (actively
  developed, batch-API-native, 2026-dated releases).
- **Inspect** for programmatic and model-graded scorers — `includes`, `match`,
  `pattern`, `f1`, `choice`, `math`, `model_graded_qa`, `model_graded_fact` — so
  "programmatic before judged" is a library call, not a project.
- **DSPy** to optimise the *judge prompt* against the human gold set (with a
  held-out calibration split).
- **An annotation surface** — Langfuse queues where the traces already are,
  Label Studio Enterprise for video, Braintrust where blind review matters.
- **A human workforce contract** — Prolific for volume at a published rate,
  Toloka/Snorkel/Surge/Scale for domain expertise. Do not build a labelling org.
- **Presidio** as one layer of PII redaction on the egress edge — never as the
  only control.

**What to avoid:**

- **Making the judge the teacher.** Self-preference is *caused* by
  self-recognition [[src](https://arxiv.org/abs/2404.13076)]; this is a mechanism,
  not a hygiene preference, and it makes the eval measure nothing.
- **Single-order pairwise judging.** Reordering alone flipped 66 of 80 queries
  [[src](https://arxiv.org/abs/2305.17926)].
- **One large judge as the default.** A panel of small disjoint-family judges is
  cheaper *and* less biased [[src](https://arxiv.org/abs/2404.18796)]; §6.1 prices
  it at half a single mid-tier judge.
- **Percent-agreement as the headline metric.** Judges with high percent agreement
  "can still assign vastly different scores"
  [[src](https://arxiv.org/abs/2406.12624)], and κ collapses on rare classes.
- **Quoting an annotation budget without the human line.** "$3,280" is off by an
  order of magnitude (§6.2).
- **Assuming the >80 % judge-agreement figure transfers to 2026 models on
  enterprise tasks.** It is a 2023 chat-preference measurement (§2.6). Measure it
  per customer or do not claim it.
- **Self-rewarding / training on the student's own outputs as targets.** Doc 00
  §8.3's collapse risk, by design.
- **Teacher-written references in the frozen test split.** It turns the eval into
  a mimicry contest (§3.7 path 1).
- **Seeding synthetic-prompt generation from the whole trace store.** Evolved
  descendants of test prompts land in training (§3.7 path 2).
- **Treating video as text with extra tokens.** The teacher's cost is linear in
  clip length and the student's is flat; unmatched frame budgets produce
  unachievable labels (§8.2).
- **Building an annotation UI.** Build the router, the diff view, the provenance
  display and the noise-floor banner; rent the widgets.
- **A hard dependency on any annotation vendor's product.** Three sunsets in doc
  00 §8.6, plus three ownership changes in this document's own narrower survey
  (Argilla → Hugging Face, Presidio → community org, distilabel's authors departed).

---

## Open questions

⚠️ Consolidated. Each names the doc that should close it.

1. **⚠️ Judge-human agreement for 2026-generation judges.** **No published
   measurement was found this session for GPT-6 Astra, GPT-5.6 Sol/Terra/Luna,
   Claude Opus 5, Fable 5.1 or Gemini 3.8 Flash as judges.** Every agreement
   number in §2.6 is 2023–2025. The whole eval gate rests on this. *Owner: doc 04,
   with web search available; and in the meantime, measure it per customer.*
   ⚠️ **Still open after the 2026-09-19 adversarial fact-check** — that session's
   `WebSearch` budget was also exhausted (200/200), so the gap is confirmed
   unresolved, not confirmed absent. See the Verification log.
2. **⚠️ Cost per SME-adjudicated example.** Doc 00's Open Question #10 remains
   open: **no vendor in §4.3 publishes a price** (Snorkel, Surge, Toloka and Scale
   all decline to). §4.4's $12.50–$25.00 band is built on an assumed $75–$150/h
   loaded SME cost, not a quote. Since this is 77–98 % of the annotation budget
   (§6.2), it is the least-sourced number with the largest effect. *Owner: this
   doc, via an actual RFQ.*
3. **⚠️ Gemini logprob availability.** §3.2 resolved Anthropic (none) and OpenAI
   (generated tokens only, not supplied continuations). Gemini was not checked.
   If Gemini exposes scoring of supplied continuations, it becomes the only
   frontier teacher that can drive on-policy distillation. *Owner: doc 05.*
   ⚠️ **Attempted and still unresolved 2026-09-19**: a direct fetch of
   [ai.google.dev/api/generate-content](https://ai.google.dev/api/generate-content)
   returned a `GenerationConfig` section that did not surface `responseLogprobs` /
   `logprobs` in the visible schema — inconclusive, not negative. Method to close:
   fetch the full REST `GenerationConfig` reference, and check whether log
   probabilities can be obtained for a **caller-supplied** continuation (the only
   variant that unlocks rung 5), not merely for generated tokens.
4. **⚠️ Qwen3.8-27B video token count for a 240-frame clip.** Blocks any video
   annotation budget using the clean open-weight teacher (§8.3). Its ViT runs full
   bidirectional attention with no windowing, so the number is resolution-dependent
   and must be measured, not estimated. Method: one clip through vLLM, read the
   prefill count. *Owner: this doc / doc 06.*
5. **⚠️ Does Gemini's per-second token rate hold at custom `fps`?** §8.2's
   frame-matching saving (halving the 10-minute clip bill) assumes per-frame
   linearity; the docs state ~100 tokens/second only for default sampling.
   *Owner: this doc.*
6. **⚠️ Frame-selection alignment between teacher and student.** Matching *rate*
   is not matching *frames*. The rigorous alternative — sending the student's
   actual sampled frames as images — loses the teacher's native video path. Which
   produces better labels is unmeasured. *Owner: doc 04 (it is doc 00's Open
   Question #8 in a different guise).*
7. **⚠️ Does a narrow single-task specialist "compete with OpenAI's products and
   services"?** §5.1's §3.3(e) is conditioned on competition, and the Permitted
   Exception does not cover generative distillation. This is a legal question with
   a real argument on each side and an unusual damages carve-out attached
   (§5.1 finding 4). *Owner: doc 08, with counsel.*
8. **⚠️ Kimi-K3 licence §2 vs §4(a) for annotation-as-a-service.** Handing a
   customer Kimi-K3-generated labels is arguably not "Model as a Service" (§2) but
   also arguably not "internal use" (§4(a)). The likely reading is that §2 simply
   does not apply, but the platform crossing $20 M revenue makes this worth
   answering early. *Owner: doc 08, with counsel.*
9. **⚠️ Meta/Llama and other open-weight licences.** Not fetched this session.
   Open weights do **not** imply unencumbered outputs (§5.4). *Owner: doc 08.*
10. **⚠️ Constitutional-AI critique–revise at 2–30B student scale on an enterprise
    policy document.** The CAI result is Anthropic's own on their own models; no
    public replication in this setting was found (§3.5). It is the proposed
    mechanism for the safety-data path, so it needs evidence. *Owner: doc 05.*
11. **⚠️ Information per teacher dollar across the four degradation rungs**
    (rejection sampling / teacher-rewrite / preference pair / rubric scalar,
    §3.2). No published comparison found; it determines which rung to buy when
    logprobs are unavailable. *Owner: doc 05.*
12. **⚠️ Does LIMA-scale curation transfer?** If ~1,000 curated examples carry a
    task at 2–30B, the 100k-example budget of §6.2 is misallocated and the money
    should go to hard-slice acquisition and adjudication (§3.6). This is cheap to
    test in the first iteration and would materially change the cost model.
    *Owner: doc 05.*
13. **⚠️ A prevalence-robust agreement statistic.** Gwet's AC1 is named in §2.5 as
    standard practice but was not sourced this session; it must be before it
    appears in a customer-facing metric definition. *Owner: doc 04.*
14. **⚠️ Active learning for LLM annotation.** The intended citation resolved to an
    unrelated paper and was dropped (§4.2 verification note). Selective annotation
    [[2209.01975](https://arxiv.org/abs/2209.01975)] is cited instead, but a
    proper active-learning source for judge-in-the-loop selection is missing.
    *Owner: this doc.*
15. **⚠️ Annotation-tooling market coverage.** §4.3 and §7.6 surveyed only vendors
    reachable by known URL, because web search was exhausted. Mercor-class expert
    marketplaces, newer eval/annotation entrants and anything launched since May
    2026 are missing, not disproven. *Owner: doc 09.*
16. **⚠️ Embedding/dedup pass cost at 451M tokens.** Left unpriced in §6.2's
    Scenario A. Small relative to the human line, but it should be a number.
    *Owner: this doc.*
17. **⚠️ Inspect's licence.** Named as an adopted dependency in §7.6; the licence
    type was not stated on the pages fetched. *Owner: doc 08.*
18. **⚠️ Does the 2023 "LLM beats crowd workers" result hold for long-context
    enterprise annotation?** §4.1's division of labour rests on a tweet-
    classification study [[2303.15056](https://arxiv.org/abs/2303.15056)]. The
    4,000-token prompt stack is a different regime. *Owner: doc 04.*

---

## Sources

All fetched **2026-09-19** unless the source states its own date. Web *search* was
unavailable this session; every URL below was reached directly (`WebFetch`, `curl`,
or — for the OpenAI terms — the Wayback Machine).

**Judges, rubrics and evaluation methodology**
- Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena — https://arxiv.org/abs/2306.05685
- G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment — https://arxiv.org/abs/2303.16634
- Prometheus: Inducing Fine-grained Evaluation Capability in Language Models — https://arxiv.org/abs/2310.08491
- Prometheus 2: An Open Source Language Model Specialized in Evaluating Other Language Models — https://arxiv.org/abs/2405.01535
- JudgeLM: Fine-tuned Large Language Models are Scalable Judges — https://arxiv.org/abs/2310.17631
- Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models (PoLL) — https://arxiv.org/abs/2404.18796
- Length-Controlled AlpacaEval: A Simple Way to Debias Automatic Evaluators — https://arxiv.org/abs/2404.04475
- LLM Evaluators Recognize and Favor Their Own Generations — https://arxiv.org/abs/2404.13076
- Large Language Models are not Fair Evaluators — https://arxiv.org/abs/2305.17926
- Self-Taught Evaluators — https://arxiv.org/abs/2408.02666
- Judging the Judges: Evaluating Alignment and Vulnerabilities in LLMs-as-Judges — https://arxiv.org/abs/2406.12624
- A Survey on LLM-as-a-Judge — https://arxiv.org/abs/2411.15594
- HealthBench: Evaluating Large Language Models Towards Improved Human Health — https://arxiv.org/abs/2505.08775
- Rubrics as Rewards: Reinforcement Learning Beyond Verifiable Domains — https://arxiv.org/abs/2507.17746
- FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance — https://arxiv.org/abs/2305.05176

**Teacher-generated data, distillation and data quality**
- Self-Instruct: Aligning Language Models with Self-Generated Instructions — https://arxiv.org/abs/2212.10560
- WizardLM / Evol-Instruct — https://arxiv.org/abs/2304.12244
- Orca 2: Teaching Small Language Models How to Reason — https://arxiv.org/abs/2311.11045
- Distilling Step-by-Step! — https://arxiv.org/abs/2305.02301
- Magpie: Alignment Data Synthesis from Scratch by Prompting Aligned LLMs with Nothing — https://arxiv.org/abs/2406.08464
- Scaling Relationship on Learning Mathematical Reasoning with LLMs (RFT) — https://arxiv.org/abs/2308.01825
- Constitutional AI: Harmlessness from AI Feedback — https://arxiv.org/abs/2212.08073
- UltraFeedback: Boosting Language Models with Scaled AI Feedback — https://arxiv.org/abs/2310.01377
- Self-Rewarding Language Models — https://arxiv.org/abs/2401.10020
- On-Policy Distillation of Language Models: Learning from Self-Generated Mistakes (GKD) — https://arxiv.org/abs/2306.13649
- LIMA: Less Is More for Alignment — https://arxiv.org/abs/2305.11206
- Deduplicating Training Data Makes Language Models Better — https://arxiv.org/abs/2107.06499
- SemDeDup: Data-efficient learning at web-scale through semantic deduplication — https://arxiv.org/abs/2303.09540
- Selective Annotation Makes Language Models Better Few-Shot Learners — https://arxiv.org/abs/2209.01975
- ChatGPT Outperforms Crowd-Workers for Text-Annotation Tasks — https://arxiv.org/abs/2303.15056

**Video and multimodal**
- Video-MME — https://arxiv.org/abs/2405.21075
- LLaVA-Video: Video Instruction Tuning With Synthetic Data — https://arxiv.org/abs/2410.02713
- ShareGPT4Video: Improving Video Understanding and Generation with Better Captions — https://arxiv.org/abs/2406.04325
- VTimeLLM: Empower LLM to Grasp Video Moments — https://arxiv.org/abs/2311.18445
- TemporalBench — https://arxiv.org/abs/2410.10818
- LongVideoBench — https://arxiv.org/abs/2407.15754
- Tarsier2 — https://arxiv.org/abs/2501.07888
- Gemini API video understanding (tokens/second, fps, offsets, MM:SS) — https://ai.google.dev/gemini-api/docs/video-understanding

**Terms, licences and legal**
- OpenAI Services Agreement, updated 2025-12-01, effective 2026-01-01 — https://openai.com/policies/business-terms/ (live URL returns HTTP 403; read via Wayback snapshot 2026-09-12: http://web.archive.org/web/20260912202946/https://openai.com/policies/business-terms)
- Anthropic Usage Policy, effective 2025-09-15 — https://www.anthropic.com/legal/aup
- Anthropic Commercial Terms of Service, effective 2025-06-17 — https://www.anthropic.com/legal/commercial-terms
- Google Gemini API Additional Terms of Service, effective 2026-03-23 — https://ai.google.dev/gemini-api/terms
- Kimi K3 License — https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE
- DeepSeek-V4.1-Flash model metadata (`license:mit`) — https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash
- Qwen3.8-27B model metadata (`license:apache-2.0`) — https://huggingface.co/api/models/Qwen/Qwen3.8-27B

**Pricing and batch APIs**
- OpenAI API pricing — https://developers.openai.com/api/docs/pricing
- OpenAI Batch API guide — https://developers.openai.com/api/docs/guides/batch
- OpenAI chat completions reference (`logprobs`, `top_logprobs`) — https://developers.openai.com/api/docs/api-reference/chat/create
- OpenAI Evals guide (sunset dates) — https://developers.openai.com/api/docs/guides/evals
- Claude pricing — https://claude.com/pricing
- Anthropic Message Batches API — https://platform.claude.com/docs/en/build-with-claude/batch-processing
- Anthropic Messages API reference (no logprobs) — https://platform.claude.com/docs/en/api/messages
- Gemini API pricing (page dated 2026-09-16) — https://ai.google.dev/gemini-api/docs/pricing
- Prolific pricing — https://www.prolific.com/pricing
- HumanSignal / Label Studio pricing — https://humansignal.com/pricing/

**Tooling**
- Bespoke Curator — https://github.com/bespokelabsai/curator
- distilabel (maintenance notice) — https://github.com/argilla-io/distilabel
- Argilla — https://argilla.io/
- Langfuse annotation — https://langfuse.com/docs/evaluation/evaluation-methods/annotation
- Braintrust Human Review — https://www.braintrust.dev/docs/guides/human-review
- DSPy — https://dspy.ai/current/
- Inspect (UK AI Security Institute) — https://inspect.aisi.org.uk/ and https://inspect.aisi.org.uk/scorers.html
- Presidio (moved to `data-privacy-stack`) — https://github.com/data-privacy-stack/presidio
- Toloka — https://toloka.ai/
- Surge AI — https://www.surgehq.ai/
- Scale AI — https://scale.com/

**Repo-internal (linked, not restated)**
- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — legend, cost formulas, price tiers
- [`research/platform/00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) — the loop, invariants, economics, the judge-validity crux, teacher ToS framing
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) — §2 interactive, §3 max-throughput, §5 input $/1M grids
- [`research/models/marlin2b/README.md`](../models/marlin2b/README.md) and [`architecture.md`](../models/marlin2b/architecture.md) — 240-frame cap, 23,560-token Path A, Path A/B ambiguity
- [`research/models/qwen3827b/architecture.md`](../models/qwen3827b/architecture.md) — video token ids, M-RoPE temporal axis, ViT without windowing
- [`research/models/kimik3/architecture.md`](../models/kimik3/architecture.md) — licence §1.1
- [`research/cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) — vLLM/SGLang serving for self-hosted teachers
- [`research/scaling/07-cost-engineering.md`](../scaling/07-cost-engineering.md) — idle-GPU economics behind §6.4


---

## Verification log (2026-09-19)

Adversarial re-check of this document against primary sources. Every citation below
was **opened**, never trusted from the text; every derivation was **recomputed with
`python3`**; every `research/` cross-reference was **read in the file**. 47 claims
checked: **35 CONFIRMED, 10 CORRECTED, 2 UNVERIFIABLE**. Nothing was removed.

**Standing caveat that changed.** The original session had no web search *and* no
`WebFetch` reach beyond known URLs. This session had working `WebFetch` (used
throughout below) but its `WebSearch` budget was also exhausted (200/200) before it
started. So the survey gaps in §4.3, §7.6 and Open Questions 1 and 15 are
**re-confirmed as gaps**, not closed and not disproven.

### CORRECTED (10)

| # | Where | Was | Is | Basis |
|---|---|---|---|---|
| C1 | §6.1, T3 table | Single Claude Opus 5, both orders `$0.06412` → **$6,412** | `$0.06824` → **$6,824** | `2 × (5,324 × $5 + 300 × $25)/1e6`, `python3`; Opus 5 $5 in / $25 out confirmed at [claude.com/pricing](https://claude.com/pricing). PoLL conclusion unaffected |
| C2 | §6.1, rejection sampling | Row **labelled** "90 % prompt-cache hit" | Relabelled **100 %** (cache-optimistic); a true 90 % hit is **$29,800** and the self-hosted advantage **14.9×**, not 13.7× | The row's own arithmetic charges the cached rate ($1/MTok, [OpenAI pricing](https://developers.openai.com/api/docs/pricing)) on all 7 repeats |
| C3 | §6.2, conclusion 1 | "$3,280 is **under a quarter** of $12,500" | **26 %** — just *over* a quarter ($12,500/4 = $3,125) | Arithmetic |
| C4 | §6.2, conclusion 2 | "pays back at roughly **1–2 billion** tokens (0.6B Astra, 1.21B Opus 5)" | **1.03–2.17 B** (Astra), **2.06–4.36 B** (Opus 5); the 0.6B/1.21B cells are doc 00's **$10k** row, not this doc's $17k–$36k iteration | `$17,000–$36,000 ÷ $16.56` and `÷ $8.25` per 1M, savings read from [doc 00 §4.3](00-goal-and-problem-statement.md) |
| C5 | §6.4 | "S4 is **2–6× cheaper** than S1 … Qwen3.8-27B on B300 **$0.1527 S4 vs $0.3095–$0.9 S1**" | **1.0–4.3×**, pair-specific; Qwen3.8-27B/B300 S1 is **$0.1649–$0.3343**, so the saving is **8 %** | [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) §2 (S1, lines 122–175) vs §3 (S4, lines 176–220). **$0.3095 is the S4 `high` cell**, not an S1 cell; **$0.9 appears nowhere** in the matrix for this pair. Backfill conclusion survives — it rests on idle GPU, not this spread |
| C6 | §6.3, design rule 3 | "**Never assume** batch + cache discounts multiply" | They **do** stack per the vendor; the reason not to budget them is that batch cache hits are *best-effort* (30–98 %) | [Anthropic batch doc](https://platform.claude.com/docs/en/build-with-claude/batch-processing): *"The pricing discounts from prompt caching and Message Batches can stack"*; *"cache hits are provided on a best-effort basis"*; *"cache hit rates ranging from 30% to 98%"* |
| C7 | §4.4 | SME calibration set "**comparable to** doc 00 §4.3(b)'s $5,554 training bill" | **2.3–4.5×** it (and 3.8–7.6× the $3,280 batched annotation bill) | $12,500–$25,000 ÷ $5,554 |
| C8 | §8.4 | Gold set "$7k–$94k … against a teacher bill of **$500–$2,000 for the same volume**" | $533–$1,965 are **per 100,000 clips**; volume-matched the teacher side of a 500-clip gold set is **~$5** | §8.1's own table; the correction *sharpens* the asymmetry |
| C9 | Implications item 8 | Frame-matching "**halves** the teacher bill" | Cuts it **58 %** ($4,665 → $1,965 per 100k) | §8.1 table, recomputed |
| C10 | §4.3 / §7.6 | Argilla "**now part of** Hugging Face" | Site banner reads *"Argilla is **joining** Hugging Face"* | [argilla.io](https://argilla.io/) |

Plus one ⚠️ added, not a correction: §8.1's Marlin-2B cell now flags that the repo
prints **23,560** prefill tokens in [`marlin2b/README.md`](../models/marlin2b/README.md)
§9 and method item 2 but **23,520** in its open-questions item 2, and that the quoted
**1.914×** Path-A/B ratio matches 23,520/12,288 (23,560/12,288 = 1.917×). 0.2 %; it
moves nothing in this document, but the repo should settle it.

### CONFIRMED — pricing, ToS and vendor operational facts (all opened live)

- **[claude.com/pricing](https://claude.com/pricing)** — Fable 5.1 **$10 / $0.25 read / $50** (write $12.50); Opus 5 **$5 / $0.50 / $25** (write **$6.25**); Sonnet 5 $2/$0.20/$10; Haiku 4.5 $1/$0.10/$5; *"Save 50% with batch processing"*; **US-only inference 1.1×**; **Opus 5 Fast Mode 2× for up to 2.5× faster**. Every §6.1/§6.3 Claude figure reproduces.
- **[OpenAI pricing](https://developers.openai.com/api/docs/pricing)** — GPT-6 Astra **$10/$1/$50**; GPT-5.6 Sol $4/$0.40/$20; Terra $2/$0.20/$12; Luna $0.20/$0.02/$1.20; Batch **50 %**.
- **[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)** — Gemini 3.8 Flash **$0.75 in / $3.75 out through 2026-12-31**, **$1.50/$7.50 from 2027-01-01**, batch exactly half; page *"Last updated 2026-09-16 UTC"*. §6.5 rule 7's "doubles on 2027-01-01" is right.
- **[OpenAI Batch guide](https://developers.openai.com/api/docs/guides/batch)** — 50 % discount; completion window **only `24h`**; **50,000 requests**, **200 MB** input file; separate rate-limit pool; expired batches cancelled, only completed requests charged. (The supported-endpoint list has since added `/v1/videos` and splits images into generations/edits — the doc's list is a subset, not an error.)
- **[Anthropic Message Batches](https://platform.claude.com/docs/en/build-with-claude/batch-processing)** — *"most batches finishing in less than 1 hour"*; **100,000 requests or 256 MB**; results *"when all messages have completed or after 24 hours, whichever comes first"*; **29 days** retention; expired requests *"You will not be billed"*; batch prices **Opus 5 $2.50/$12.50, Sonnet 5 $1/$5, Fable 5.1 $5/$25**; `max_tokens: 0` pre-warming unsupported in batch for exactly the reason §6.3 gives; the 1-hour-cache guidance quoted verbatim.
- **[Anthropic Messages API](https://platform.claude.com/docs/en/api/messages)** — parameter list carries **no `logprobs` / `top_logprobs`**; `output_config` supports only `effort` and `format`. §3.2's "Anthropic: No / No" row stands.
- **[OpenAI chat/create](https://developers.openai.com/api/docs/api-reference/chat/create)** — `logprobs` returns *"the log probabilities of each output token returned in the `content` of `message`"*; `top_logprobs` is *"An integer between 0 and 20"*. §3.2's "generated tokens only, not a supplied continuation" stands, and with it §5.5's structural argument for open-weight teachers.
- **[OpenAI Services Agreement](https://openai.com/policies/business-terms/)** — live URL still **HTTP 403** (re-tested with a browser UA); the **2026-09-12 Wayback snapshot** is readable and every §5.1 quotation is **verbatim and complete**: header *"Updated: December 1, 2025 … Effective: January 1, 2026"*; §3.3(d)(e)(f); the **"Permitted Exception"** definition (limbs (a) classifiers/embeddings *not distributed*, and (b) fine-tuning **OpenAI's own** models); **"Reverse Engineer"** including *"engage in model extraction or stealing attacks"*; **§14.1's carve-out (B) CUSTOMER'S BREACH OF SECTION 3.3 (RESTRICTIONS)** from the indirect/consequential-damages exclusion; and §14.2's amount cap whose exceptions are only gross negligence/wilful misconduct, indemnification and payment. All four of §5.1's findings hold as written.
- **[Anthropic AUP](https://www.anthropic.com/legal/aup)** — effective **2025-09-15**; *"Utilization of inputs and outputs to train an AI model (e.g., 'model scraping' or 'model distillation') without prior authorization from Anthropic"* — verbatim.
- **[Gemini API terms](https://ai.google.dev/gemini-api/terms)** — effective **2026-03-23**; competition + reverse-engineering clause verbatim; paid-tier *"Google doesn't use your prompts … or responses to improve our products"* vs unpaid-tier *"Google uses the content you submit … to provide, improve, and develop Google products"*. §5.3's free-tier disqualification is correctly grounded.
- **[Kimi K3 LICENSE](https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE)** — grant includes *"run, deploy, fine-tune, or otherwise modify … and create derivative works"*; §2 "Model as a Service" definition and the **$20 M / 12-month** threshold; §3's 100 M MAU / $20 M monthly-revenue attribution; §4(a) internal-use exemption. **No distillation restriction of any kind** — §5.4 is right, and Open Question 8's §2-vs-§4(a) ambiguity is real.
- **HF model metadata** — `license:mit` for [deepseek-ai/DeepSeek-V4.1-Flash](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) (sha `dba1be0a…`, which is also the sha used in §7.2's schema example) and `license:apache-2.0` for [Qwen/Qwen3.8-27B](https://huggingface.co/api/models/Qwen/Qwen3.8-27B).
- **[Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding)** — **1 fps default**, custom `fps` in the processing config, **~100 tokens/second at low media resolution**, **3 h low / 1 h high** on 1M-context models, `start_offset`/`end_offset`, MM:SS references. §8.1–§8.5 are correctly sourced, and Open Question 5 (per-frame linearity at custom fps) is genuinely open — the page states the rate for default sampling only.
- **[Prolific pricing](https://www.prolific.com/pricing)** — *"at least £9.00 / $12.00 per hour"*, *"minimum pay allowed is £6.00 / $8.00 per hour"*, *"usually 42.8% for corporate … 33.3% for academic or non-profit"*. §4.4's $11.42 / $17.14 loaded rates (`×1.428`) and every $/label cell reproduce exactly.
- **[HumanSignal pricing](https://humansignal.com/pricing/)** — Starter Cloud **$99/mo + $49/mo per additional user, up to 12 users**; Enterprise custom with SAML/LDAP SSO, SOC2+HIPAA, highest-priority SLAs, *"LLM-as-a-judge, auto-labeling & bulk labeling"*.
- **Tooling** — [Presidio](https://github.com/data-privacy-stack/presidio) MIT, moved out of `microsoft/`, README warns *"there is no guarantee that Presidio will find all sensitive information"*; [distilabel](https://github.com/argilla-io/distilabel) maintenance notice verbatim; [Bespoke Curator](https://github.com/bespokelabsai/curator) **2026-06-09** Fireworks SFT and **2026-03-14** Tinker entries; [DSPy](https://dspy.ai/current/) **3.4.0b1** with GEPA/MIPROv2/BetterTogether/BootstrapFewShot/COPRO/SIMBA/Ensemble/KNN and the **62 % → 89 %** GEPA example ($2.18 over 200 examples, same base model); [Inspect](https://inspect.aisi.org.uk/) UK AISI + Meridian Labs, Dataset/Solver/Scorer, the four human-in-the-loop features — **licence still not stated on the pages fetched, so Open Question 17 stays open**; [Braintrust human review](https://www.braintrust.dev/docs/guides/human-review) categorical/continuous/free-form, *"a display filter, not an access control rule"*, blind review, and the Pro/Enterprise gate on unlimited scorers; [Langfuse annotation](https://langfuse.com/docs/evaluation/evaluation-methods/annotation) queues over traces/sessions/observations, score configs required, live-updating experiment-comparison annotation; [Toloka](https://toloka.ai/) 90+ domains / 70 %+ advanced degrees / 6000+ contributors / 100+ countries / 50+ QC methods / ISO 27001+27701, SOC 2, GDPR, HIPAA, **no pricing**; [Scale](https://scale.com/) Data Engine, GenAI Portfolio, Donovan, *"25% have advanced degrees"*, Francis deSouza named as CEO, *"10 years … Since 2016"*, and the Meta ownership question is indeed **not** answerable from the page.

### CONFIRMED — papers (every number read in the abstract at the cited arXiv id)

2306.05685 (>80 %, *"the same level of agreement between humans"*, 30,000 conversations + 3,000 expert votes) · 2310.08491 (Prometheus **0.897** / GPT-4 **0.882** / ChatGPT **0.392** over 45 rubrics; Feedback Collection 1,000 rubrics / 20K instructions / 100K responses) · 2404.18796 (*"over seven times less expensive"*, 3 judge settings, 6 datasets) · 2406.12624 (13 judges × 9 exam-takers, *"up to 5 points"*, leniency, *"judges with high percent agreement can still assign vastly different scores"*) · 2305.17926 (**66 over 80**; MEC + BPC + human-in-the-loop) · 2303.16634 (Spearman **0.514**; LLM-evaluator self-favouring) · 2310.17631 (>90 % vs the *teacher* judge; **5K samples in 3 minutes on 8 A100**; position/knowledge/format bias; swap augmentation, reference support, reference drop) · 2404.04475 (**0.94 → 0.98**) · 2404.13076 (linear correlation between self-recognition and self-preference) · 2408.02666 (**75.4 → 88.3**, **88.7** majority vote) · 2505.08775 (**5,000** conversations, **262** physicians, **48,562** criteria) · 2507.17746 (**31 %** HealthBench / **7 %** GPQA-Diamond; better alignment for smaller judges, reduced variance) · 2305.05176 (*"up to 98% cost reduction"*) · 2305.02301 (**770M T5 > 540B PaLM** using **80 %** of the data) · 2212.10560 (**33 %** absolute; **5 %** gap to InstructGPT-001) · 2304.12244 (evolved instructions *"superior to human-created ones"*; **17 of 29** skills) · 2406.08464 (**4M** synthesised, **300K** selected, comparable to official Llama-3-8B-Instruct) · 2308.01825 (**35.9 → 49.3**; more improvement for weaker models) · 2310.01377 (**>1M** GPT-4 feedback over **250k** conversations; bias-mitigation techniques) · 2401.10020 (Llama-2-70B, 3 iterations, beats Claude 2 / Gemini Pro / GPT-4 0613 on AlpacaEval 2.0) · 2107.06499 (**61-word** sentence **>60,000** times in C4; *"ten times less frequently"*; *"over 4%"* of the validation set) · 2303.09540 (**50 %** of LAION, *"effectively halving training time"*, better OOD) · 2305.11206 (**1,000** curated examples; **43 %**) · 2311.09783 (ChatGPT **52 %**, GPT-4 **57 %**) · 2209.01975 (**12.9 %/11.4 %** at budget **18/100**; **10-100×** less across **10** tasks) · 2303.15056 (**2,382** tweets; 4 of 5 tasks; intercoder agreement above crowd *and* trained annotators; **<$0.003**, ~**20×** cheaper than MTurk) · 2405.21075 (**900** videos / **254** hours / **2,700** QA) · 2407.15754 (**3,763** videos, **6,678** human MCQs, **17** categories; performance improves only with more frames) · 2410.10818 (GPT-4o **38.5 %**, ~30-point human gap, ~2K annotations → ~10K QA) · 2501.07888 (**+2.8 %** F1 over GPT-4o, **+5.8 %** over Gemini-1.5-Pro; **+8.6 % / +24.9 %** human; model-based sampling + DPO) · 2406.04325 (*"temporal-confused"* quote and the differential captioning strategy).

### CONFIRMED — `research/` cross-references (read in file)

- [`matrix/cost-matrix.md`](../matrix/cost-matrix.md): Kimi-K3/B300 `low` **$1.2921** in (§5) / **$3.6101** out (§3); DeepSeek-V4.1-Flash/B200 **$0.1806** / **$0.355**; Qwen3.8-27B/B300 **$0.046** / **$0.1527**; Marlin-2B/B300 **$0.0092** / **$0.022**. §6.1's price-input block is right; only §6.4's S1 comparison was wrong (C5).
- [`models/marlin2b/README.md`](../models/marlin2b/README.md): **$0.432 per 1,000 two-minute captions on B200**, **$0.461** H100, reserved H200 **$0.38/1k** — so **$43.2 per 100k**, and §8.1's **108×** (10-min) and **629×** (1-hour) ratios reproduce exactly; 240-frame cap and the Path A/B **1.914×** silent-regression trap confirmed (see the ⚠️ on the token count above).
- [`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) §4.3: **$6,560 / $3,280** annotation, **$5,554** training, and the amortisation table's $16.56 / $8.25 / $3.76 savings — all present as cited (C4 concerns which *row* was quoted, not the numbers).

### Derivations recomputed with `python3` (all reproduce unless flagged)

T1 all ten rows · T2 all six rows · T3 (C1 is the one failure) · PoLL both-orders sum and the "less than half a mid-tier judge" claim (48.97 %) · rejection sampling k=8 all four setups and the 13.7× ratio (C2 on the label) · Scenarios A/B/C totals **$17,039–$31,459**, **$21,158–$35,578**, **$10,863–$15,663** and the **96–98 % / 77–86 %** human shares · the κ-paradox 2×2 (`p_o` 0.95, `p_e` 0.932, **κ = 0.2647**, recall **0.20**) · `n ≈ 196` · best-of-4 at a 10 % solve rate = **34.39 %** · loaded crowd rates and all six $/label rows · the whole §8.1 video-cost table · the Qwen3.8-27B video placeholder ($0.00115/clip → **$114.5**/100k, **4.66×** and **9.30×**) · §8.4's three human rows · 100k × 4,512 = **451.2M** tokens.

### UNVERIFIABLE (2) — both already flagged in the document, both re-attempted

1. **Judge-human agreement for 2026-generation judges** (Open Question 1). Re-attempted; `WebSearch` unavailable in this session too. No vendor pricing or API page fetched carries an agreement figure. The gap is **confirmed open**, and §2.6's instruction — measure it per customer, per task, per slice — remains the only defensible position.
2. **Gemini logprob availability** (Open Question 3). A direct fetch of the `generate-content` REST reference did not surface `responseLogprobs`/`logprobs` in the visible `GenerationConfig` schema. **Inconclusive, not negative** — §3.2's "⚠️ not checked" row is correct to stay ⚠️.

### Addendum 2026-09-19 — OpenAI policy URLs re-tested, and §5.1's heading reconciled

All five `openai.com` policy URLs (`/policies/business-terms/`, `/usage-policies/`, `/services-agreement/`, `/row-terms-of-use/`, `/eu-terms-of-use/`) were re-fetched with `curl -sL` under a browser user-agent (`Mozilla/5.0 … Chrome/140.0.0.0 Safari/537.36`) and **all five returned HTTP 403**; the 2026-09-12 Wayback capture of the Business Terms returned **200** and still serves the text quoted in §5.1. §5.1's heading was overclaiming relative to [`00` §8.1](00-goal-and-problem-statement.md) and [`10` §7](10-roadmap-and-mvp.md), which still called the clause unread: it is now **"resolved for the Business Terms"**, with an explicit note that the Usage Policies, Service Terms and ROW/EU Terms of Use remain unread and that an archive capture is not the executed contract. §5.1 gains *How to obtain the clause without the web* — four off-web routes, each an action with an owner. `00` §8.1, `00` Open Question 2, `00`'s legal source list and `10` §7 row 1 now point at that subsection instead of restating "unread (403)". ⚠️ Route (a) (console export) is asserted, not verified in this session.
