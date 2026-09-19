# Competitor and adjacent-platform analysis

Research date **2026-09-19**. Every URL in this document was fetched on that
date unless the source states its own date; the "checked" column in §11 records
it per product.

This document is the market scan that
[`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) §7 called
a floor rather than a survey. It owns Open Questions **1, 3, 4, 5, 14, 15 and 16**
from that document. Conventions (legend, `est.`/`meas.`, blended-cost formula,
price tiers) are [`research/METHODOLOGY.md`](../METHODOLOGY.md); the loop stage
names **S1–S9** are doc 00 §1.2 and are not redefined here.

> **Numbering note.** Doc 00 §6 maps "doc 07" to *A/B testing + rollout* and
> assigns the market-scan open questions to "doc 09". The platform owner's
> request and the work plan both name this file `07-competitor-analysis.md`.
> This document therefore occupies slot 07 and the §6 map needs one edit; I have
> not edited doc 00. Whoever reconciles the index should decide whether the
> A/B-testing document becomes 09 or whether competitor analysis becomes 10.

---

## 1. Method, and how much to trust this document

### 1.1 What was actually done

**WebSearch was unavailable for this document.** The session's search budget
(200/200 calls) was consumed before this agent ran, exactly as it was for doc 00.
Every source below was therefore reached by **WebFetch or `curl` against a known
or link-followed URL**. Concretely:

- ~55 distinct URLs fetched; 41 returned usable content.
  ⚠️ **Corrected 2026-09-19:** this count contradicts §11, which lists **74**
  URLs all marked "checked 2026-09-19". Either the ~55 figure or the §11 table is
  wrong; the fact-check could not reconstruct which, so treat "~55" as unreliable
  and §11's row count as the auditable number. The "41 products" claim in §1.2 is
  likewise not reconstructible from §11 (74 URLs covering fewer, uncounted
  products).
- Where a marketing page was a JavaScript shell (OpenPipe), the site's own JS
  bundle was downloaded with `curl` and its string table mined — this is still
  the vendor's own copy, served from the vendor's own origin, and is cited as such.
- Where a page 403'd or the domain failed to resolve, that fact is reported as a
  finding rather than filled in from memory. **No vendor claim in this document
  is recalled; each was read on 2026-09-19.**

### 1.2 The consequences, stated plainly

1. **This is a survey of platforms that could be named and fetched.** It is
   materially better than doc 00 §7 (41 products vs 13), and it resolves five of
   doc 00's open questions outright. It is still not a market scan. A company
   founded in the last twelve months with no link from any page fetched here is
   invisible to this method.
2. **Funding and "recent news" are the weakest column in every table below.**
   Press coverage is exactly the kind of fact a search engine returns and a
   vendor's own site does not. Where a funding or acquisition claim appears, it
   is either from a vendor/acquirer press page (strong) or from a banner on the
   company's own homepage (medium, marked), or it is absent. Do not treat the
   funding column as evidence of anything.
3. **Negative findings are load-bearing and I have kept them.** Four vendors
   named in the brief — Pi Labs, Atla, Lamini, Zenbase — could not be reached at
   all on 2026-09-19. That is itself the most important thing this survey learned
   about them (§8.6), and it is reported with the exact failure mode so someone
   with search can confirm or refute it in five minutes.

### 1.3 Claim typing used throughout

| Marker | Meaning |
|---|---|
| `[src](url)` | Read on the cited page on 2026-09-19 |
| **vendor-claimed** | The number is the vendor's, about their own product, with no independent replication |
| **meas.** | A published measurement with a stated method |
| `est.` | Arithmetic performed here from sourced inputs |
| ⚠️ | No primary source obtained, or an inference; reasoning stated inline |

**A vendor-claimed number is evidence of what a vendor is willing to say in
public, and nothing more.** That is not worthless — it bounds what we can say
without being laughed at — but §15 deliberately separates "what they claim" from
"what is proven".

---

## 2. The coverage vocabulary

Every product is scored against the nine loop stages from doc 00 §1.2. The
scoring rule is deliberately harsh, because a generous rubric would make ten
companies look like full competitors and obscure the actual whitespace:

| Symbol | Meaning |
|:--:|---|
| **●** | A first-class, documented, generally-available product surface for this stage |
| **◐** | Present but partial: preview/beta, or only for models/data the vendor hosts, or requires the customer to build the glue |
| **○** | Absent |

| Stage | Short name | What earns a ● |
|---|---|---|
| **S1** | Endpoints | Serves a production inference endpoint the customer's app calls |
| **S2** | Traces | Captures request/response + context from *production*, durably, searchably |
| **S3** | Annotate | Labels that traffic — teacher completions, preference pairs, rubric scores, human review |
| **S4** | Datasets + evals | Versioned splits, a frozen eval suite, judge management |
| **S5** | Train | SFT / DPO / RL / distillation producing a checkpoint |
| **S6** | Checkpoints | Immutable, versioned, comparable, exportable model artifacts |
| **S7** | Offline gate | Runs the eval suite against a candidate and gates on it |
| **S8** | HW optimize | Quantisation / engine / kernel / speculative-decoding work on target hardware |
| **S9** | A/B + rollout | Shadow → canary → % split → promote, with a statistical claim |

The single most useful output of this document is §12: **no product scores ● on
S2 *and* S5 *and* S9.** Everything else follows from that.

---

## 3. The near-competitors — platforms attempting the whole loop

### 3.1 OpenPipe → Weights & Biases → CoreWeave (the closest historical analogue)

**Status: the original product is dead; its thesis and its team are now inside
CoreWeave.** This is the most important entry in the document, because OpenPipe
ran the exact business the platform owner is proposing, for roughly three years,
and we can read what happened.

**What it was.** From OpenPipe's own archived copy, still served at openpipe.ai:
*"OpenPipe is the fully-managed fine-tuning platform for developers"*, and
*"OpenPipe lets you replace your existing prompt with a fine-tuned model with just
a few minutes of work. We capture your existing prompts and completions,
synthesize them into a dataset, and fine-tune models that are a drop-in
replacement for your prompt"*
[[src](https://openpipe.ai/)]. The capture mechanism was an SDK that was
*"a drop-in replacement for the OpenAI SDK, with the added difference that it
records your LLM input/output pairs to use as a fine-tuning dataset"*
[[ibid.](https://openpipe.ai/)]. That is S1+S2+S3+S5 in one product, in 2023.

**What it claimed** (all **vendor-claimed**, from the archive). ⚠️ **Corrected
2026-09-19 — these are dated claims, not current ones.** The savings, seed and
cost-multiple rows below all come from one blog post, *"We Raised $6.7M to
Replace GPT-4 with Your Own Fine-Tuned Models"*, dated **2024-03-25**; the DPO
row is from a post dated **2024-10-01**. Verified by reading the site's own JS
bundle (`/assets/index-CPC2DrwX.js`) on 2026-09-19, which carries each post's
`date:` frontmatter. A 2024 claim benchmarked against GPT-4 is two model
generations stale:

| Claim | Verbatim |
|---|---|
| Aggregate customer saving | *"Our users have already saved over $7M while lowering latency and improving quality by switching from GPT-4 to fine-tuned models on our platform"* |
| Cost multiple | *"Fine-tuned models are generally much smaller than generalist models. This leads to much lower inference costs (often a 10-100x improvement)"* |
| A specific multiple | *"fine-tuning a 7B model on OpenPipe leads to an average 14x savings compared to GPT-4-Turbo (that's a whopping 32x compared to GPT-4-0613)"* — ⚠️ the doc previously quoted only the 32x half; the headline multiple is **14x vs GPT-4-Turbo**, and both are conditional on a 7B student |
| Quality | *"Across dozens of cases evaluated by our customers using LLM-as-judge, expert fine-tuned models reliably beat GPT-3.5-turbo and exhibit similar or better performance than the GPT-4 variants"* |
| Time-to-value | *"We've seen organizations go from a prompt to a fine-tuned model with less than an hour of engineering time, if they were already collecting their data"* |
| Method firsts | *"OpenPipe is the first fine-tuning platform to support Direct Preference Optimization (DPO)"* |
| Funding | *"the close of our $6.7M seed round … led by Costanoa Ventures with participation from Y Combinator"* |

[all [src](https://openpipe.ai/)]

**What happened.** CoreWeave announced the acquisition on **2025-09-03**, terms
undisclosed: OpenPipe is described as *"a reinforcement learning platform enabling
developers to train AI agents that improve from experience"*, and the deal
*"follows CoreWeave's recent acquisition of Weights & Biases"*
[[src](https://www.coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning)].
Then, from OpenPipe's own migration notice:

> *"OpenPipe training and inference are migrating to W&B, and the legacy OpenPipe
> platform will stop supporting new training and inference on **July 30, 2026**"*
> — notice dated **2026-05-18**; ⚠️ **corrected 2026-09-19: that date is in the
> past.** The legacy platform's shutdown is not pending, it has already happened.
> … *"we successfully migrated OpenPipe's core functionality, including model
> distillation, to the"* [W&B platform] … *"we'll continue maintaining managed SFT
> workflows as part of CoreWeave"* … *"While the core training and inference
> workloads can be fully migrated, other infrequently-used platform features will
> be sunset with the shut down of OpenPipe"*
> [[src](https://openpipe.ai/)]

**The reading that matters.** The closest product to this platform's thesis was
bought by a GPU cloud, folded into an observability company that the same GPU
cloud also bought, and its standalone surface was switched off. Two interpretations,
and they are not exclusive:

- **(a) The loop is a feature of infrastructure, not a product.** A company with
  no GPUs and no trace store has to rent both; a GPU cloud that also owns the
  trace store can run the loop at cost. This is a structural argument against the
  standalone version of our business, and §16 answers it.
- **(b) The loop is a good business that got acqui-hired at seed-stage scale.**
  $6.7M seed, ~$7M of *customer* savings claimed — these are small numbers. The
  company was bought for its team and its RL library, not because the loop had
  compounded into a large ARR line.

⚠️ **TO BE VERIFIED:** which of (a) or (b) dominates. The deal terms were not
disclosed [[src](https://www.coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning)],
so revenue at acquisition is unknown. This is the single most decision-relevant
unknown in the whole document.

**What exists now, and it is a real competitor.** W&B **Serverless Training
(Serverless RL)**: *"Post-train models with reinforcement learning so they learn
new behaviors and improve reliability, speed, and costs when performing
multi-turn agentic tasks"*, which *"splits RL workflows into inference and
training phases and multiplexes them across jobs to increase GPU utilization and
reduce your training time and costs"*, provisioned *"on CoreWeave"*, and —
critically — *"Serverless Inference also automatically hosts models that you
train through Serverless Training"*
[[src](https://docs.wandb.ai/guides/training/)]. Pricing during preview:
*"During the preview, W&B charges you only for inference usage and artifact
storage. W&B does not charge for adapter training during the preview period"*
[[ibid.](https://docs.wandb.ai/guides/training/)]. The OpenPipe ART README puts
**vendor-claimed** numbers on the multiplexing: *"40% lower cost - Multiplexing on
shared production-grade inference cluster"*, *"28% faster training - Scale to
2000+ concurrent requests across many GPUs"*, *"Every checkpoint instantly
available via W&B Inference"*
[[src](https://raw.githubusercontent.com/OpenPipe/ART/main/README.md)].

Note what that multiplexing claim *is*: it is the S-LoRA-style argument from doc
00 §4.5 — turn many customers' idle GPUs into one busy one — shipped as a
product, by the competitor, with a number attached.

**Coverage:** S1 ● · S2 ● (Weave) · S3 ◐ · S4 ● (Weave evals) · S5 ● · S6 ● ·
S7 ◐ · S8 ○ · S9 ○
**Target customer:** ML engineers already using W&B; agent teams doing RL.
**Hosting:** SaaS on CoreWeave; W&B has single-tenant/on-prem enterprise tiers
[[src](https://wandb.ai/site/pricing/)].
**Gap:** no online A/B or rollout control, no hardware optimisation, annotation
is scorers-and-human-feedback rather than a teacher-distillation pipeline.

---

### 3.2 Distil Labs — the most direct competitor found

**This is the company building the product described in the brief.** Their own
four-step description, verbatim in structure:

1. **Observe** — *"Route 1% of production traffic"* to capture real workload traces
2. **Build** — the platform *"generates synthetic datasets, fine-tunes, quantizes,
   and deploys an optimized SLM"*, stated as **~1 day** of execution
3. **Approve** — automatic evaluation showing *"cost, accuracy, and latency"*
   before scaling to 100 %
4. **Improve** — continuous retraining and redeployment

[[src](https://distillabs.ai/)]

Required input: *"Export or one day of traffic"*. Output: *"one OpenAI-compatible
endpoint"*, with private/on-prem deployment and data-residency options
[[ibid.](https://distillabs.ai/)]. Headline **vendor-claimed**: cost reduction
*"up to 80%"* with accuracy comparable to frontier models.

**Published case studies (vendor-claimed):**

| Customer | Claim |
|---|---|
| **Knowunity** | *"Cut their LLM bill by 68%"*; classification accuracy *"from 81% to 93%"* |
| **Rocketgraph** | Deployed entirely on-premises for regulated industries |

[[src](https://distillabs.ai/)]

**Why this entry should change the plan.** Every structural decision in doc 00 is
mirrored here: capture a traffic sample rather than all of it; quantise as part
of the pipeline (doc 00 §1.2 S8); gate on cost *and* accuracy *and* latency
before ramp (doc 00 invariants I1–I3); OpenAI-compatible endpoint (I4);
on-prem for regulated customers (I6). The "1 % of traffic" and "one day" framing
is also a sharper *sales* answer than doc 00 has: it converts an ambiguous
"let us capture your traffic" ask into a bounded, cheap, reversible pilot.

**Gaps — and they are the same gaps everyone has.** Nothing on the page describes
an **online A/B test with a statistical claim** (step 3 is an *offline* automatic
evaluation before ramp), nothing describes **judge validation against human
labels** (doc 00 §5.1), and there is no pricing. The Knowunity accuracy figure
(81 % → 93 %) is a classification task — the easiest possible case, where a
programmatic verifier exists and the judge problem disappears.

**Coverage:** S1 ● · S2 ◐ · S3 ◐ (synthetic generation; no described human loop)
· S4 ◐ · S5 ● · S6 ⚠️ · S7 ● · S8 ● (quantisation in-pipeline) · S9 ◐
**Target customer:** teams with an existing high-volume LLM bill, including
regulated/on-prem.
**Pricing:** not published; "contact sales" [[src](https://distillabs.ai/)].
**Funding/news:** ⚠️ none obtainable without search.

---

### 3.3 LangChain (LangSmith) + Baseten — the loop assembling itself in public

This is the most important *dynamic* in the market, and it was found by accident.

- LangSmith is now *"an Agent & LLM Observability Platform"* with tracing,
  *"Online LLM-as-judge and code evals"*, *"Tool and agent trajectory
  monitoring"*, unsupervised trace clustering (*"Insights"*), a purpose-built
  trace store (**SmithDB**, *"Sub-second performance across millions of traces"*,
  self-hostable in a VPC), an **LLM Gateway**, sandboxes, and deployment
  [[src](https://www.langchain.com/langsmith)]. That is S1, S2 and S4 under one roof.
- **LangSmith Engine** is *"its in-platform agent that helps users debug and
  improve their agents autonomously"*
  [[src](https://www.baseten.co/blog/langchain-trains-custom-models-langsmith-engine-baseten-loops/),
  last updated **2026-09-15**].
- And LangChain now trains the models behind Engine on Baseten Loops:
  *"Fine-tuning a large open-weight model on agent traces specializes it for
  difficult tasks"*, using *"supervised fine-tuning, reinforcement learning, and
  long-context workloads"*, for tasks including *"categorizing traces by failure
  mode and severity"* and *"mapping traces to existing open issues"*, and
  *"training smaller models like Qwen for specialized tasks"*. LangChain's quote:
  *"Loops gives us the control and iteration speed we need while training, backed
  by a team that works closely with us"* [[ibid.](https://www.baseten.co/blog/langchain-trains-custom-models-langsmith-engine-baseten-loops/)].

**Read that carefully.** An observability vendor that already owns S1, S2 and S4
went to an inference vendor that owns S5, S6 and S8, and built S3-on-traces.
Neither company had to acquire the other. **The loop is being assembled by
partnership, not by a single platform** — which means the barrier to a competitor
appearing is a contract, not a build.

No quality, latency or cost numbers are published in that post
[[ibid.](https://www.baseten.co/blog/langchain-trains-custom-models-langsmith-engine-baseten-loops/)] —
worth noting, since it is a joint marketing piece and the absence of a number in
a marketing piece is informative.

**LangSmith pricing** [[src](https://www.langchain.com/pricing-langsmith)]:
Developer $0/seat (1 seat, 5,000 base traces/mo), **Plus $39/seat/month**
(10,000 base traces/mo, unlimited seats), Enterprise custom with self-hosted and
hybrid. Usage: **LCU $1.50/unit** (compute), **LSU $1.00/unit** (storage). Base
traces retain 14 days; *"extended traces have a longer retention period of 400
days"* at extra cost.

**Coverage (LangSmith alone):** S1 ◐ (gateway + deployment) · S2 ● · S3 ◐ ·
S4 ● · S5 ○ · S6 ○ · S7 ◐ · S8 ○ · S9 ○
**Coverage (LangSmith + Baseten together):** everything except S9.

---

### 3.4 Databricks — the "your data is already here" wedge, now with auto-tuning

Two distinct things, and doc 00 §7 under-reported both.

**(a) Agent Bricks Custom LLM** (docs page dated **2026-09-15**) is an automatic
optimisation loop, and this is the claim doc 00 §7 flagged as unverified — it is
now verified. It *"optimizes prompts on behalf of users, automatically infers
evaluation criteria, evaluates the system from provided data, and deploys"* the
result as a production endpoint. The optimisation *"compares multiple
strategies, including Foundation Model Fine-tuning"*, ⚠️ **recommends** — the
doc previously wrote "requires a minimum of 100 inputs"; the page's actual
sentence, re-read 2026-09-19, is *"Databricks recommends at least 100 inputs
(either 100 rows in your Unity Catalog table or 100 manually-provided samples)
to optimize your agent"* — and uses *"automated evaluation capabilities, including MLflow and Agent
Evaluation, to enable rapid assessment of the cost-quality tradeoff"*. Inputs may
be labelled datasets, unlabelled datasets, or manual examples from Unity Catalog.
Limits: *"100k input and output tokens per minute"*; not supported in workspaces
with Enhanced Security and Compliance
[[src](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/custom-llm)].
The wider platform page confirms the positioning: *"Databricks Agent Bricks lets
you optimize quality and cost with synthetic data, custom evaluation, and
automated tuning"* [[src](https://www.databricks.com/product/artificial-intelligence)].

**The `100` is the number to argue with — but argue with the right number.**
⚠️ **Corrected 2026-09-19:** Databricks *recommends* at least 100 inputs; it does
not impose 100 as a gate, and the page does not claim 100 examples suffice for
anything. The defensible version of this argument is weaker and still holds: doc
00 §5.3 sizes the statistical requirement for a parity claim, a recommendation of
~100 examples is two orders of magnitude below it, and nothing on the page
describes a non-inferiority test. Databricks is selling *optimisation*, not
*confidence* — which is the gap this platform claims (§13).

**(b) TAO — Test-time Adaptive Optimization** (blog dated **2025-03-25**). Four
stages: response generation with diverse sampling; response scoring *"through
reward modeling, preference-based scoring, or LLM judges"*; RL training toward
high-scoring responses; and *"Continuous Improvement — leveraging naturally
collected usage data from deployed applications"*. Crucially, *"TAO uses test-time
compute during the tuning phase only, not inference"*. **Vendor-claimed** results
[[src](https://www.databricks.com/blog/tao-using-test-time-compute-train-efficient-llms-without-labeled-data)]:

| Benchmark | Model | Claim |
|---|---|---|
| FinanceBench | Llama 3.1 8B | TAO beat fine-tuning on **7,200 labeled examples** |
| DB Enterprise Arena | Llama 3.1 8B | TAO beat fine-tuning on **4,800 human-written inputs** |
| BIRD-SQL | Llama 3.3 70B | TAO beat fine-tuning on **8,137 labeled examples** |
| Multitask enterprise | Llama 3.3 70B | **+2.4 points**, *"significantly closer to GPT-4o"* |

TAO is the strongest published support for the **unlabelled-data** path: the loop
does not strictly need a teacher's *completions*, only a *scorer*. That matters
enormously for the teacher-ToS risk in doc 00 §8.1, because a scorer can be an
open-weights judge.

**Coverage:** S1 ● · S2 ◐ (MLflow tracing) · S3 ◐ · S4 ● · S5 ● · S6 ● · S7 ● ·
S8 ○ · S9 ○
**Target customer:** enterprises whose data is already in a lakehouse.
**Hosting:** the customer's cloud account, governed by Unity Catalog.
**Gap:** no online A/B; TAO in preview since 2025-03; Agent Bricks constrained to
Databricks-hosted models and regions.

---

### 3.5 NVIDIA — the reference design that keeps being cancelled and rebuilt

Three artifacts, three different lifecycle states, and the pattern is the lesson.

1. **Data Flywheel Blueprint** — *"This project is **deprecated**. It is no longer
   actively maintained, and new production use is not recommended"* (April 2026).
   Apache-2.0. Architecture: production LLM logs → datasets with stratified
   splitting → LoRA fine-tuning across candidate models → LLM-as-judge evaluation,
   orchestrated by Celery over NeMo services. **Vendor-claimed** results: a
   fine-tuned **Llama 3.2 1B at ≈98 % of Llama 3.1 70B's accuracy** on a
   tool-calling task (verbatim: *"a fine-tuned `llama-3.2-1b-instruct` was able to
   achieve ~98% accuracy relative to the 70b model being used in production"* —
   confirmed 2026-09-19), cost reduction *"up to 98.6%"*; and
   **`Qwen-2.5-32b-coder`** (⚠️ corrected 2026-09-19 — the README names the
   *coder* variant, not plain Qwen 2.5 32B) at parity with Llama 3.1 70B
   **un-fine-tuned**, with *">50%"* off **both** inference cost **and** time to
   first token (the doc previously cited cost only)
   [[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)].
2. **NeMo microservices** — *"NeMo Microservices will be sunset on **October 1,
   2026**. All new development has moved to NeMo Platform."* Seven services:
   Customizer (LoRA/SFT/DPO), Evaluator, Guardrails, Data Designer, Safe
   Synthesizer, Auditor, Inference. SDK 2.0.1, image 26.03.1
   [[src](https://docs.nvidia.com/nemo/microservices/latest/index.html)].
3. **NeMo Platform** — the successor, and it is *not* a retreat. *"an agent-first,
   open suite of libraries with skills for accelerating AI agent specialization,
   optimization, and governance"*, organised Build / Deploy / Optimize, with
   **NeMo Relay** for monitoring, **Customizer** for fine-tuning, **NeMo RL** and
   **NeMo Gym**, **Evaluator** for benchmarking, and the explicit instruction to
   *"continuously improve with data flywheels"* using *"feedback from production
   agents to create iterative retraining cycles"*. Named adopters: AT&T, Shell,
   ServiceNow, Dropbox. Enterprise support via NVIDIA AI Enterprise, per-GPU
   pricing [[src](https://www.nvidia.com/en-us/ai-data-science/products/nemo/)].

**The pattern:** NVIDIA has now shipped the *same box diagram* three times under
three names in about eighteen months. Doc 00 §6 arrived at nearly the same
decomposition independently. That convergence is reassuring about the
architecture and alarming about the moat: **the architecture is not the product.**

**Coverage (NeMo Platform):** S1 ● (NIM) · S2 ◐ (Relay) · S3 ◐ (Data Designer /
Safe Synthesizer) · S4 ● (Evaluator) · S5 ● · S6 ◐ · S7 ◐ · S8 ● (TensorRT-LLM /
NIM) · S9 ○

---

### 3.6 Adaptive ML — the only competitor that names A/B testing as a pillar

Adaptive Engine, an *"RLOps platform for building, owning, and deploying
specialized LLMs through reinforcement learning"*, structured as three
capabilities [[src](https://www.adaptive-ml.com/)]:

- **ADAPT** — *"Bootstrap with Reinforcement Learning. Generate synthetic data.
  Fine-tune with reinforcement learning."*
- **EVALUATE** — bespoke AI judges and A/B testing; *"Guarantee performance with
  A/B testing"*
- **SERVE & ADAPT** — *"Optimize with production feedback. Track business metrics
  in real time and feed production signals back into training."*

**Published customers and vendor-claimed numbers** [[ibid.](https://www.adaptive-ml.com/)]:

| Customer | Claim |
|---|---|
| **AT&T** | Adaptive Engine across *"50+ use cases"*; a fine-tuned Llama 3.1 8B *"achieved a 51% win rate vs GPT-4o"* on document RAG |
| **SK Telecom** | Tuned Gemma 3 4B *"outperforming the largest proprietary models in both Korean and English"* |
| **Aïkan** | Tuned Llama 3.1 8B, hallucinations **−25 % vs GPT-4o**, **−42 % vs base** |

They also sell *"Kickstart Implementation Services"* with forward-deployed
engineers — i.e. the expert-in-the-loop that doc 00 §3.1 identified as the
unautomated part is here sold as a line item, not automated away.

The AT&T "51 % win rate vs GPT-4o" is the most honest parity claim on any vendor
site surveyed, because a win rate straddling 50 % is exactly what non-inferiority
looks like and they published it as a win rather than inflating it.

**Adaptive ML was acquired by Datadog.** ⚠️ **Upgraded 2026-09-19** from
"reported, unconfirmed" to *confirmed on the vendor's own dated announcement*:
the homepage banner *"Adaptive ML has been acquired by Datadog"* links to a post
dated **2026-06-30** stating *"Adaptive ML is joining Datadog, the leading
AI-powered observability and security platform"*, with the team moving into
Datadog's AI research lab; **no terms disclosed**
[[src](https://www.adaptive-ml.com/post/joining-datadog)]. Still **unconfirmed by
the acquirer** — `datadoghq.com`'s press-release index rendered without article
content on fetch
[[src](https://www.datadoghq.com/about/latest-news/press-releases/)] — so this is
one-sided, but no longer a bare rumour.
It is the second observability company to buy a training company in a year
(after CoreWeave→W&B+OpenPipe) and the strategic read in §16 hardens
considerably.

**Coverage:** S1 ● · S2 ◐ · S3 ◐ · S4 ● · S5 ● · S6 ◐ · S7 ● · S8 ○ · S9 ●
**Hosting:** ⚠️ VPC/self-hosted emphasis inferred from the enterprise customer
list (AT&T, SKT); not stated verbatim on the page fetched.

---

### 3.7 AWS Bedrock Model Distillation — the incumbent cloud shipping our core loop

This is the most under-appreciated competitor in the brief, because it does the
one thing everyone else's docs are vague about: **it trains on production
invocation logs.**

> *"If you enable CloudWatch Logs invocation logging, you can use existing teacher
> responses from invocation logs stored in Amazon S3 as training data."*
> … *"you can continue to use Amazon Bedrock's inference API operations, such as
> InvokeModel or Converse API, and collect the invocation logs, model input data
> (prompts), and model output data (responses) for all invocations"*
> [[src](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html)]

Two sub-modes, and the distinction is exactly the one doc 00 §3.2 draws between
rungs 2 and 3 of the ladder:

- **prompts only** from the logs → Bedrock re-generates teacher responses, and
  *"might add proprietary data synthesis techniques to generate diverse and
  higher-quality responses"*;
- **prompt-response pairs** from the logs → no regeneration, but *"the teacher
  model specified in your model distillation job must match the model used in the
  invocation log. If they don't match, the invocation logs aren't used."*

Requests can carry `requestMetadata`, and a distillation job can **filter by that
metadata** — i.e. per-use-case slicing of production traffic is a first-class
input [[ibid.](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html)].
Costs: teacher inference for synthesis is billed at on-demand rates, and *"Data
synthesis techniques may increase the size of the fine-tuning dataset to a maximum
of 15k prompt-response pairs"* [[ibid.](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html)].

**The two facts that matter most:**

1. **15,000 prompt-response pairs is the ceiling.** That is a small dataset. It
   bounds what Bedrock's product can do and leaves the high-volume case open.
2. **The teacher matrix is a licensing map, not a technical one.** Verbatim:
   > *"**Distillation is not currently available for Anthropic models on Amazon
   > Bedrock. There is no confirmed timeline for when Anthropic distillation will
   > be restored.**"*
   > [[src](https://docs.aws.amazon.com/bedrock/latest/userguide/prequisites-model-distillation.html)]

   The surviving pairs are entirely first-party-or-open:

   | Teacher | Students | Region |
   |---|---|---|
   | Amazon Nova Pro | Nova Lite, Nova Micro | us-east-1 |
   | Amazon Nova Premier | Nova Lite, Nova Micro, Nova Pro | us-east-1 |
   | Llama 3.1 405B | Llama 3.1 8B / 70B, Llama 3.2 1B, Llama 3.3 70B | us-west-2 |
   | Llama 3.1 70B | Llama 3.1 8B, Llama 3.2 1B / 3B | us-west-2 |
   | Llama 3.3 70B | Llama 3.1 8B, Llama 3.2 1B / 3B | us-west-2 |

   [[src](https://docs.aws.amazon.com/bedrock/latest/userguide/prequisites-model-distillation.html)]

**This is the empirical answer to doc 00 §8.1.** Amazon — a company with a
commercial relationship with Anthropic measured in billions — *shipped* Claude
distillation and then *withdrew* it, with no restoration timeline. Any plan that
assumes a Claude-family teacher can be used for distillation at scale is
contradicted by the behaviour of the best-positioned party in the market. See §16.

**Coverage:** S1 ● · S2 ◐ (invocation logs, not a trace product) · S3 ● (teacher
generation + synthesis) · S4 ◐ · S5 ● · S6 ◐ · S7 ○ · S8 ○ · S9 ○

---

### 3.8 Azure AI Foundry — stored completions → distillation → evals, and it is retiring

Azure shipped the tightest first-party version of the capture→distil→eval flow:
set `store: True` on a chat completion, enrich with `metadata`, then from the
Stored Completions pane **Filter** and press **Distill** (minimum 10 stored
completions, *"recommended to provide hundreds to thousands"*) or press
**Evaluate** to build an eval dataset from the same traffic
[[src](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/concept-model-distillation)].
Limits: max **10 GB** stored; supported for all Azure OpenAI models in all regions
via the Chat Completions API; *"Stored completion distillation training files
cannot be accessed directly and cannot be exported externally/downloaded"*
[[ibid.](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/concept-model-distillation)].

And:

> **Warning** — *"Stored completions retire on **October 15, 2026**. For migration
> guidance, see Migrate from stored completions to Responses API and Agent Traces."*
> [[ibid.](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/concept-model-distillation)]
> (doc dated 2026-07-06, updated 2026-07-07)

**Two lessons, and the second is the valuable one.**
(a) The non-exportable-training-file design is pure lock-in and a customer should
hate it — doc 00 §8.6 already names this; here is a concrete instance to point at
in a sales conversation.
(b) The retirement is *not* a retreat: it is a migration to *Responses API +
Agent Traces*, i.e. from "stored completions" to a richer agent-trace substrate.
That is the same schema move doc 02 has to make (OpenTelemetry GenAI conventions,
sessions/turns/steps). Microsoft is re-platforming capture, not abandoning it.

**Coverage:** S1 ● · S2 ● · S3 ◐ · S4 ● · S5 ● · S6 ◐ · S7 ◐ · S8 ○ · S9 ○

---

## 4. Training-and-serving platforms (S5–S8, no loop)

### 4.1 Baseten

Closest inference vendor to the platform's serving layer, and the one whose price
list doc 00 already uses as its anchor.

- **Training Jobs (GA):** *"bring your own container and training code, run it on
  managed GPUs, and deploy any checkpoint to production inference"*. `baseten
  train push` provisions H100/H200; checkpoints *"sync automatically to Baseten
  storage during training"*; `baseten train checkpoint deploy` turns any
  checkpoint into an endpoint. Frameworks: **Axolotl** (LoRA/QLoRA), **TRL**
  (SFT/DPO/GRPO), **VeRL** (RL with custom rewards), **MS-Swift**. Multi-node over
  InfiniBand; weights loadable *"from Hugging Face, S3, GCS, R2, or any HTTPS
  URL"*; SSH / VS Code Remote Tunnels into the running container
  [[src](https://docs.baseten.co/training/overview)].
- **Loops (early access):** *"256K+ sequence length and 2T+ parameter model
  training"* with *"Qwen, Kimi, GLM, Deepseek and Nemotron models supported"*;
  asynchronous RL enabling *"bounded off-policy learning"*; *"Models trained with
  Loops promote directly to Baseten Dedicated Inference with one command"*;
  *"Full ownership of your trained weights, no lock-in"*
  [[src](https://www.baseten.co/products/training/)].
- **Chains / Truss:** Chains orchestrates multi-model workflows in pure Python
  with **Chainlets** that each *"specify hardware resources, dependencies, and
  scaling settings"*, so teams *"select the right hardware for each component"*
  [[src](https://www.baseten.co/products/chains/)]. For this platform, Chains is
  the natural shape for a *shadow-and-compare* topology (student + incumbent +
  judge in one graph) — see §14.
- **Model APIs** and **dedicated GPU pricing**: §15.
- **Post-training model guidance** (dated 2026-09-15): recommends
  DeepSeek-V4-Flash (284B total / 13B active, *"~4.8 KB per token"* KV cache) for
  long-context cost-sensitive work, GLM-5.2 for RL-heavy, Kimi K2.6 for vision +
  agents, Kimi K2.7 Code (*"~30% fewer tokens per coding task"*),
  Nemotron-3-Super-120B for efficient dense, and the Qwen3 family as the
  *"safe default"*. ⚠️ **Two corrections, 2026-09-19.** (a) The design rule was
  quoted wrongly: *"Total parameters limit speed"* is verbatim (a section
  heading), but *"active parameters drive compute cost"* is **not on the page** —
  the actual sentences are *"Every token the model generates requires math
  proportional to its active parameters"* and *"More active parameters means more
  floating point operations (FLOPs) per generated token"*. The rule survives; the
  quotation marks do not. (b) The page does **not** recommend GLM-5.2 as an
  RL-heavy *student*; what it says is *"GLM 5.2's slime (an open-source RL
  training framework) provides strong, fast RL support"* — a claim about a
  training framework, not a model choice. The model it ties to cheap RL is
  DeepSeek-V4-Flash (*"13B active parameters and a light KV cache make large RL
  (Reinforcement Learning) runs cost less"*)
  [[src](https://www.baseten.co/blog/best-open-source-models-for-post-training/)].
- **Published customer numbers** (all **vendor-claimed**): Speechify
  *"161B+ characters per month for 60M+ users"* with *"44% lower cost per million
  characters"*, *"30-50% lower p99 inference latency"*, *"4.5x faster cold
  starts"*; Notion *"2-3x lower latency"*; Zed *"2x faster code completions"*;
  Parallel Web Systems *"3x higher throughput"* and *"2x improvement in latency"*
  [[src](https://www.baseten.co/customers/)].

**Coverage:** S1 ● · S2 ○ · S3 ○ · S4 ○ · S5 ● · S6 ● · S7 ○ · S8 ● · S9 ◐
(canary/rollout primitives exist around deployments ⚠️ not verified on a fetched
page).
**Strength:** the train→deploy seam, genuinely closed, with weight portability
stated in writing.
**Gap:** nothing upstream of S5. They need a LangChain to bring them traces —
which is exactly what happened (§3.3).

### 4.2 Thinking Machines — Tinker, and the resolution of "Inkling"

**Tinker** is a training API with four primitives — `forward_backward`,
`optim_step`, `sample`, `save_state` — LoRA over dense and MoE, and the explicit
philosophy that the primitives, not the pipeline, are the product
[[src](https://thinkingmachines.ai/tinker/)]. Model families listed: Qwen
(3.5-4B → 3.5-397B-A17B), NVIDIA Nemotron (30B → 550B), DeepSeek-V3.1, GPT-OSS
(20B–120B), Moonshot Kimi-K2.6 [[ibid.](https://thinkingmachines.ai/tinker/)].

**"Inkling" is resolved, and doc 00 §7 had it right.** It is Thinking Machines'
**own model family**, listed on the Tinker price page as premium models with a
*"Limited-time 50% discount"*, in 64K and 256K variants, plus a **serverless
inference beta** available *"for Inkling models only"*
[[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]. Full price table
in §15.

⚠️ **Alternative candidate, for completeness.** "Inkling" is also the name of the
machine-teaching DSL in Microsoft's Project Bonsai. I could not fetch
`learn.microsoft.com/en-us/bonsai/inkling/` (HTTP 404 on 2026-09-19), which is
weak evidence that the Bonsai docs have been retired. Given the brief lists
"Inkling" in the same breath as Tinker, and given Inkling ships *on Tinker*, the
Thinking Machines reading is almost certainly the intended one. Flagging the
Bonsai homonym only so nobody re-discovers it later and thinks it was missed.

**On-policy distillation** (published **2025-10-27**) is the methodological
anchor doc 00 §3.2 builds on, and the numbers re-verified here
[[src](https://thinkingmachines.ai/blog/on-policy-distillation/)]:

| Measure | Value |
|---|---|
| AIME'24, SFT baseline (400k prompts) | 60 % |
| AIME'24, RL (Qwen3 report) | 67.6 % |
| AIME'24, on-policy distillation | **74.4 %** |
| GPQA-Diamond: SFT / RL / distillation | 55.6 % / 61.3 % / **63.3 %** |
| GPU-hours: RL vs distillation | 17,920 vs **1,800** (≈9.9×) |
| FLOPs vs SFT-2M | *"9-30x"* reduction |
| vs direct RL | learned *"7-10x faster"*, *"50-100x"* cumulative compute saving |

Personalisation case (Qwen3-8B): internal QA 18 % → 43 % after 100 %-docs
midtrain but IF-eval collapsed 85 % → 45 %; a 70/30 mix gave 36 % / 79 %; adding
on-policy distillation recovered to **41 % / 83 %** — *"recovers nearly full
performance on IF-eval without losing any knowledge"*
[[ibid.](https://thinkingmachines.ai/blog/on-policy-distillation/)].

**The cookbook is the giveaway.** `tinker-cookbook` (Apache-2.0, 4.1k stars)
ships recipes for chat SFT, math/code RL, DPO, a three-stage RLHF pipeline,
*"on-policy and off-policy knowledge distillation with single- and multi-teacher
configurations"*, tool use for RAG, multi-agent self-play, audio, and VLM
classification, plus an experimental suite of **12 verified benchmarks** including
GSM8K, MMLU-Pro and GPQA [[src](https://github.com/thinking-machines-lab/tinker-cookbook)].
**Multi-teacher distillation as a shipped recipe** is a capability doc 00 §3.2
does not contemplate and should.

**Coverage:** S1 ◐ (beta, own models only) · S2 ○ · S3 ○ · S4 ◐ (benchmark
harness) · S5 ● · S6 ● (export to HuggingFace) · S7 ○ · S8 ○ · S9 ○
**Gap:** the entire production half of the loop. Tinker is a supplier, not a rival.

### 4.3 Fireworks AI

SFT, DPO and RFT as managed training; LoRA is the primary mechanism and *"Trained
models deploy as LoRA adapters on dedicated capacity"*; datasets in OpenAI chat
JSONL, **min 3 / max 3,000,000 examples**; context default ≥32,768; LoRA rank
powers of two up to 32 (default 8); *"Automatic evaluation carving"* or a custom
eval set; vision-language SFT for multimodal bases; trained models serve only on
*"on-demand (dedicated) deployment, which is the only supported method for serving
trained models"* [[src](https://docs.fireworks.ai/fine-tuning/fine-tuning-models)].

Pricing is unusually transparent and is the best per-token training benchmark
available (§15), including the structural fact that **RFT is billed per GPU-hour,
not per token** [[src](https://fireworks.ai/pricing)].

**Coverage:** S1 ● · S2 ○ · S3 ○ · S4 ◐ · S5 ● · S6 ◐ · S7 ○ · S8 ● · S9 ○
**Gap:** LoRA-only tuning caps how far a student can move from its base; dedicated
deployment for every tuned model removes the serverless cost floor advantage.

### 4.4 Together AI (and Refuel)

LoRA default (*"a small set of adapter weights on top of the frozen base model"*)
or full fine-tuning *"when the base behavior needs to shift substantially"*; SFT
and DPO; specialised tracks for vision, function calling and *"reasoning
fine-tuning using chain-of-thought"*; bring-your-own base model from the HF Hub;
tuned models deploy to dedicated endpoints **or download for local deployment**
[[src](https://docs.together.ai/docs/fine-tuning-overview)]. Pricing in §15.

**Refuel is now part of Together**: *"Refuel.ai is joining Together AI"*, stated on
Refuel's homepage [[src](https://www.refuel.ai/)]. Refuel's product was
LLM-based labelling and enrichment with *"prompt engineering, model evaluation,
dynamic few-shot prompting and hyper-parameter optimization"* managed for you;
**vendor-claimed** *"30% increase in accuracy compared to GPT-4 on every task"*,
*"15 billion+ data points processed to date"*, and customer results including Beni
(46 % → 87 % accuracy) and Enigma (*"1 day compared to 5 weeks"*)
[[ibid.](https://www.refuel.ai/)].

**That acquisition is the S3 move.** An inference+training vendor bought an
annotation vendor. Together now has S3 ◐ + S5 ● + S1 ● and is one trace product
away from the loop.

**Coverage:** S1 ● · S2 ○ · S3 ◐ (via Refuel) · S4 ○ · S5 ● · S6 ● · S7 ○ ·
S8 ● · S9 ○

### 4.5 Predibase — status resolved, product gone

Doc 00 §7 could not resolve this. Resolved now, as far as the public web allows:

- `predibase.com` returns **HTTP 301 → `https://www.rubrik.com/products/rubrik-agent-cloud`**
  (verified by `curl -I`, `server: AkamaiGHost`, on 2026-09-19).
- `docs.predibase.com` returns **HTTP 301 → `https://www.rubrik.com/`**.
- Both Rubrik destination pages return **HTTP 403** to WebFetch *and* to `curl`
  with a browser user-agent (`Access Denied … Reference #18.47fed417…`), as does
  `rubrik.com/company/newsroom`.

So: the domains are demonstrably controlled by Rubrik, and Rubrik's edge blocks
automated reads. **The acquisition is effectively confirmed by DNS/HTTP control;
the date, terms and product fate are not obtainable without search.** ⚠️

**What survives, and it matters:** **LoRAX** is still live and open source —
*"Multi-LoRA inference server that scales to 1000s of fine-tuned LLMs"*, with
dynamic adapter loading *"from HuggingFace, Predibase, or local filesystems
without blocking concurrent requests"*, *"heterogeneous continuous batching"* that
packs requests for different adapters together, and the claim that latency and
throughput stay *"nearly constant with the number of concurrent adapters"* through
adapter exchange between GPU and CPU memory. Apache-2.0, *"Free for Commercial
Use"*, 3.8k stars, 901 commits [[src](https://github.com/predibase/lorax)].

LoRAX is the production-grade instantiation of the S-LoRA argument doc 00 §4.5
calls *"the single highest-leverage architecture decision in the whole platform"*.
It is available, permissively licensed, and now orphaned by an acquisition —
which is both an opportunity and a maintenance risk. **Commit recency resolved
2026-09-19** (the doc previously left this ⚠️): the GitHub API reports **3,832
stars**, **Apache-2.0**, **901 commits** (a lifetime count, as the doc suspected)
and a last push of **2026-05-28** — i.e. **no commit in ~3.7 months**, and the
repository is *not* archived. Treat LoRAX as usable-but-unmaintained: the
maintenance risk is now measured, not hypothetical.

### 4.6 The rest of the training/compute layer, briefly

| Vendor | What it is | Notable |
|---|---|---|
| **Modal** | Serverless GPU compute, per-second billing. *"you always pay for what you use and nothing more. You never pay for idle resources"* | B300 $0.001972/s (**$7.10/GPU-h** `est.`), H100 SXM5 $0.001097/s (**$3.95/h** `est.`). Team $250/mo. [[src](https://modal.com/pricing)] |
| **Anyscale** | Managed Ray: *"multimodal data curation, distributed model training, batch embedding generation, and post-training"*, scaling PyTorch/vLLM/SGLang across *"thousands of nodes"*, multi-cloud incl. CoreWeave | Infrastructure, not a loop. No public price list beyond a $100 trial credit. [[src](https://www.anyscale.com/)] |
| **Hugging Face** | AutoTrain (no-code, ten task types, per-minute hardware billing, deploys to Hub) [[src](https://huggingface.co/autotrain)]; Inference Endpoints; **TRL** | TRL is the important one — see §9.1 |
| **Arcee AI** | Open-weight **Trinity** family (Large Thinking / Mini / Nano) + Genesis-Science-1 (2026-07-22); *"inspect, fine-tune, and go to production on your terms"* | Now a **model** company, not a platform company. ⚠️ Press-reported on their page: four models for **$20M**, **$1B** valuation (September 2026) — vendor-page-reported, unconfirmed. [[src](https://www.arcee.ai/)] |
| **Mistral** | Fine-tuning **deprecated and no longer actively supported**; had SFT + Classifier Factory; *"minimum fee of $4"* per job, *"$2 for each model"* per month storage; explicitly supported teaching a small model to *"mimic the behavior of the larger model"* | The fourth vendor in this document to retreat from fine-tuning-as-a-product. [[src](https://docs.mistral.ai/capabilities/finetuning/)] |
| **Lamini** | ⚠️ **Unreachable on 2026-09-19.** `lamini.ai` resolves but TLS handshake fails (`TLSV1_ALERT_INTERNAL_ERROR`); `http://lamini.ai/` 301s to the failing HTTPS origin | Status unknown. Do not assert it is dead; assert only that it did not serve. |

---

## 5. Frontier labs' first-party distillation — the retreat, documented

This is the section that most changes the strategic picture, and every line is
from the vendor's own docs on 2026-09-19.

### 5.1 OpenAI

**Fine-tuning:** *"OpenAI is winding down the fine-tuning platform. The platform is
no longer accessible to new users, but existing users of the fine-tuning platform
will be able to create training jobs for the coming months."* And: *"All
fine-tuned models will remain available for inference until their base models are
deprecated."* Supported bases remain `gpt-4.1`, `-mini`, `-nano` (2025-04-14)
[[src](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)].

**Distillation**, verbatim workflow: tune a prompt on a larger model *"like
GPT-4.1"*, capture results via the Responses API (which *"stores model responses
for 30 days by default"*), build a dataset, fine-tune a smaller model *"like
GPT-4.1-mini"* so it can *"perform similarly on a specific task to a larger, more
costly model"*. With the strongest sentence in any vendor's docs on this subject:
*"**Good evals first!** Only invest in fine-tuning after setting up evals. You need
a reliable way to determine whether your fine-tuned model is performing better
than a base model"* [[src](https://developers.openai.com/api/docs/guides/distillation)].

**RFT:** *"Reinforcement fine-tuning is supported on o-series reasoning models
only, and currently only for o4-mini"* (`o4-mini-2025-04-16`), graded by the evals
grader framework (`string_check`, `score_model`, `multi`, Python graders, tool-call
graders); billed on training time and token usage *"during the core training
loop"*, with a discount for sharing eval and fine-tuning data. Carries the same
wind-down banner [[src](https://developers.openai.com/api/docs/guides/reinforcement-fine-tuning)].

**Evals:** *"Evals will become read-only for existing users on **October 31,
2026**, and the platform is scheduled to shut down on **November 30, 2026**."*
Users are pointed at "Datasets"
[[src](https://developers.openai.com/api/docs/guides/evals)].

**Answering doc 00's Open Question 3 — why?** The docs do not say. ⚠️ Still
unanswered. But the *shape* is now legible: OpenAI shipped fine-tuning, RFT,
stored completions, distillation and evals, and is retiring **all of them**, while
keeping a cheap small model in-family (doc 00 §4.4: GPT-5.6 Luna at $0.3825
blended `est.`). That is consistent with a decision that **tiering down inside the
family is a better business than helping customers distil out of it.** It is
equally consistent with these being low-revenue products that cost more to
maintain than they earn. The two hypotheses have opposite implications for us and
cannot be separated from the docs alone.

### 5.2 Anthropic

No first-party distillation product found. The Usage Policy (effective
**2025-09-15**) prohibits the mechanism under "Do Not Abuse our Platform":

> *"Utilization of inputs and outputs to train an AI model (e.g., 'model scraping'
> or 'model distillation') without prior authorization from Anthropic"*
> [[src](https://www.anthropic.com/legal/aup)]

And the market has already priced that in: Bedrock withdrew Claude distillation
with no restoration timeline (§3.7). Humanloop's team went to Anthropic and the
platform is being sunset (§6.6). Anthropic is not a competitor in this market; it
is a *constraint* on it.

### 5.3 Google Vertex AI

⚠️ **Could not verify.** `cloud.google.com/vertex-ai/generative-ai/docs/models/distill-text-models`
301s to `docs.cloud.google.com/...`, and that URL returns **HTTP 404** on
2026-09-19. The most likely readings are that the distillation doc was removed or
relocated. **No claim about Vertex distillation should be made until someone with
search confirms the current state.** This is a genuine hole in the survey.

### 5.4 AWS and Azure

Covered in §3.7 and §3.8 — they are near-competitors, not merely adjacent, because
both train on captured production traffic.

### 5.5 The pattern across §5

Five first-party fine-tuning/distillation products, surveyed on one day:

| Vendor | Product | State on 2026-09-19 |
|---|---|---|
| OpenAI | Fine-tuning, RFT, Distillation | **Winding down**; closed to new users |
| OpenAI | Evals | **Read-only 2026-10-31, shutdown 2026-11-30** |
| Azure | Stored completions → Distill/Evaluate | **Retires 2026-10-15**, migrating to Responses API + Agent Traces |
| AWS Bedrock | Model Distillation | **Live**, but Anthropic teachers withdrawn, 15k-pair cap |
| Mistral | Fine-tuning | **Deprecated, no longer actively supported** |
| NVIDIA | Data Flywheel Blueprint | **Deprecated April 2026** |
| NVIDIA | NeMo microservices | **Sunset 2026-10-01** → NeMo Platform |

Seven retirements or withdrawals. **This is either a market that does not work, or
a market where the wrong people were trying to serve it.** §13 and §16 take a
position.

---

## 6. The observability and evaluation layer (S2 + S4)

This layer is commoditised, cheap, and — critically — **it is the layer that is
currently moving upward into training** (§3.1, §3.3, §3.6). It is the layer we
should buy, and the layer whose vendors will become our competitors.

### 6.1 Langfuse

Open source, self-hostable free. Cloud: Hobby free (50k units/mo, 30-day access,
2 users), **Core $29**, **Pro $199**, **Enterprise $2,499** — each with 100k units
included and graduated overage **$8 → $7 → $6.50 → $6 per 100k** across the
100k–1M / 1M–10M / 10M–50M / 50M+ bands. A billable unit is *"any tracing data
point sent to the platform -- including traces, observations (spans, events,
generations), and scores (evaluations)"*. Pro adds unlimited annotation queues,
SOC2/ISO27001 reports and a BAA; a Teams add-on is $300/mo
[[src](https://langfuse.com/pricing)].

**This is the cheapest per-unit trace store surveyed and it self-hosts for free.**
Doc 00 §6 already says do not build doc 02 from scratch; this confirms the price
of not building it is near zero.

### 6.2 Braintrust

Observe + Evaluate + Human Review + Playground + a **Loop agent** for autonomous
eval and test-case generation. Starter $0 (1 GB/mo processed, +$4/GB; 10k
scores/mo, +$2.50/1k; 14-day retention; **1 human-review score per project**),
**Pro $249/mo** (5 GB, +$3/GB; 50k scores, +$1.50/1k; 30-day retention then
+$0.50/GB/mo; unlimited human review; RBAC; environments; custom charts),
Enterprise custom with on-prem [[src](https://www.braintrust.dev/pricing)].

**Billing on *scores* is the interesting bit.** It prices the judge as a metered
resource, which is exactly the economic shape doc 00 §5.1 implies: judge calls are
a real, recurring, non-trivial cost line and should be budgeted, not assumed free.

### 6.3 Arize

AX Free $0 (1 GB/mo, 15-day retention), **AX Pro $50/mo** (10 GB/mo, 30-day),
Enterprise custom with unlimited volume, custom retention, and self-hosted or
SaaS. **No per-seat charge; unlimited users on all tiers.** Tracing is
OpenTelemetry-compliant with token/latency/cost tracking; online **and** offline
evals on traces, datasets and experiments, with multi-modal evaluation support;
unlimited playgrounds/datasets/experiments/prompt versioning; an "Alyx" agent and
*"Swarm observability across managed and third-party agents"* on Enterprise.
**Phoenix** is the open-source, local-first sibling
[[src](https://arize.com/pricing/)].

**Multi-modal evaluation support matters for us** — doc 00 §3.4 names video evals
as the largest evidence gap in the programme. **Resolved 2026-09-19, and the
answer is negative:** the pricing page enumerates the modalities as
*"Multi-modal evaluation (image, voice, pdf)"* [[src](https://arize.com/pricing/)].
**Video is not in the list.** Arize does not close doc 00 §3.4's gap, and §13.2's
video row is empty on the evidence rather than merely for want of it.

### 6.4 W&B Weave

Agent-native tracing where *"sessions, turns, steps, tools, and sub-agents"* are
first-class; an imperative eval API with comparison views to *"catch regressions
before they reach users"*; **Guardrails** scorers for toxicity, bias, **PII**,
hallucination, coherence, fluency and context relevance; a Playground; and an MCP
server letting coding agents *"read live production data, run evaluations, and
execute automatic iteration loops"* [[src](https://wandb.ai/site/weave/)].

That last clause is doc 00 §6's doc-09 idea — an agent that reads traces and
iterates — already shipping as a feature, by the company that also owns
Serverless RL (§3.1) and belongs to a GPU cloud.

Pricing: Free (1 GB/mo Weave ingestion), **Pro from $60/mo** (1.5 GB/mo), overage
**$0.10/MB**, storage $0.03/GB, Enterprise single-tenant/HIPAA/SSO
[[src](https://wandb.ai/site/pricing/)]. **$0.10/MB is $100/GB** (`est.`) — see
§15.4, because this is the most extreme number in the entire pricing survey.

### 6.5 Galileo — and the one idea worth stealing outright

Galileo distils *"expensive LLM-as-judge evaluators"* into *"compact Luna models
that run with low-latency and low-cost"* at **vendor-claimed** *"96% lower cost"*,
which lets them score **100 % of traffic** rather than a sample. Plus *"20+
out-of-box evals for RAG, agents, safety, and security"* with auto-tuning for
domain-specific metrics; an insights engine to *"identify failure modes, surface
hidden patterns, and prescribe fixes"*; and guardrails where *"eval scores
automatically control agent actions, tool access, and escalation paths. No
glue-code required"*. SaaS / VPC / on-prem. Page dated **2026-09-11**
[[src](https://galileo.ai/)].

**Distil the judge, not just the policy.** Doc 00 treats the judge as a frontier
API call and then discovers in §5.1 that judge cost and judge validity are both
binding constraints. Galileo's move — train a small judge against the big judge,
validate it, then run it on everything — collapses the sampling problem *and* the
cost problem, and it is the same technique the platform already sells. See §14.

### 6.6 The rest of the eval layer

| Vendor | Finding (2026-09-19) |
|---|---|
| **Confident AI / DeepEval** | Open-source DeepEval + DeepTeam. Free $0 (2 seats, 1 project, 5 weekly runs, 1 GB-month spans), **Starter $200/mo** (5 projects, unlimited seats, 5 GB-months), **Team $2,000/mo** (75 GB-months, metric versioning, Git-based prompt workflows, RBAC, SOC2, SSO), Enterprise custom. *"the cheapest tracing on the market starting from $1/GB-month … at least 3 times cheaper than alternatives"*; overage $1/GB-month [[src](https://www.confident-ai.com/pricing)] |
| **Patronus AI** | **Has pivoted.** Now *"a frontier lab"* doing *"Digital World Models"* that simulate agent behaviour; research models Lynx (hallucination detection, *"beats GPT-4 on hallucination tasks"*), GLIDER, BLUR, and FinanceBench remain published. **Vendor-claimed** *"30–40% model lift"* on long-horizon tasks, *"1M+ world data artifacts"*, *"85% UI/UX feature parity"* with real products. No pricing, no tracing product on the page [[src](https://www.patronus.ai/)] |
| **Humanloop** | **Dead.** *"we are thrilled to announce that the Humanloop team is joining Anthropic!"* and *"As we sunset the Humanloop platform, we will continue to work closely with our customers to make their transition as smooth as possible."* No dates given on the page [[src](https://humanloop.com/)] |
| **Atla** | ⚠️ **`atla-ai.com` and `www.atla-ai.com` both return a 404 page** ("The page you are looking for doesn't exist or has been moved") on 2026-09-19. Status unknown; the Selene evaluator model could not be verified |
| **Pi Labs (Pi Scorer)** | ⚠️ **`withpi.ai` does not resolve** (NXDOMAIN) on 2026-09-19. Status unknown |

---

## 7. The data and annotation layer (S3)

### 7.1 Scale AI

**Scale GenAI Platform (SGP)** spans the agent lifecycle: *"Data Connection"*
(Confluence, SharePoint, S3, kept in place), *"Build & Execute"* (long-running
async, multi-agent, *"any model without vendor lock-in"*), *"Evaluate & Monitor"*
(automated + human feedback, *"semantic layer monitoring and full trace
transparency"*), and *"Learn & Improve"* — *"Incorporates human feedback as
learning signals, enabling self-improving systems over time"*. A differentiator
called **Dialect**, *"a decision map"* encoding expert judgment. Customers named:
Mayo Clinic, DLA Piper, EY, Paramount, TIME, Global Atlantic, Howard Hughes.
Compliance: DoD IL4, SOC 2 Type II, ISO 27001, FedRAMP High
[[src](https://scale.com/genai-platform)].

The **Data Engine** is the older, larger business: *"the process of improving
machine learning models with high quality, diverse and large datasets powered by
experts"*, with prompt-response generation, **RLHF**, red teaming via prompt
injection, and model evaluation; across text, image, video and 3D/LiDAR; customers
Square, Pinterest, Meta, Instacart [[src](https://scale.com/data-engine)].

**Scale is the only vendor in this survey with a first-class *video* annotation
heritage** (LiDAR, video, sensor fusion). Doc 00 §3.4 calls video the thinnest
part of the thesis. If video-understanding distillation becomes a real line of
business, Scale is both the most credible partner and the most credible threat.

### 7.2 Snorkel AI

Three offerings: **Snorkel Flow** (data curation and evaluation platform),
**Evaluate** (benchmarking), and **Expert Data-as-a-Service**. Positioning:
*"Better data is built, not collected"*; *"Programmatic scale. Human precision.
Together"*; *"1,000+ expert domains"*; research provenance of *"200+ peer-reviewed
papers"*. Three-step cycle — **Evaluate** behaviour against specialised
benchmarks, **Curate** with expert-guided pipelines, **Refine** from failure
analysis and coverage gaps. Customers/partners named: Google, OpenAI, Anthropic,
Microsoft, Stanford. Published benchmarks: Terminal-Bench 4.0, Senior SWE-bench,
OSWorld 2.0, *"Agents' Last Exam"*. No pricing published
[[src](https://snorkel.ai/), [src](https://snorkel.ai/platform/)].

Note the customer list. Snorkel sells **to frontier labs**. That is a different
business from ours, and it means Snorkel is a supplier we could buy from rather
than a competitor — at frontier-lab prices.

### 7.3 Refuel → Together AI

See §4.4. The important structural point: S3 is being absorbed by S5 vendors.

### 7.4 Kiln

An open-source workbench that does a surprising amount of the loop on a laptop:
LLM-as-Judge scoring, synthetic data generation *"with filtering and labeling"*,
fine-tuning *"to distill models into smaller versions"*, prompt optimisation, an
AI assistant that runs experiments conversationally, golden datasets, human
ratings, and multi-dimensional quality tracking. Supports RAG, structured outputs,
reasoning, skills, sub-agents and tools. ⚠️ **Licence corrected 2026-09-19: not
simply "MIT".** GitHub's licence API returns `NOASSERTION` for `Kiln-AI/Kiln`, and
the repo's `LICENSE.txt` opens *"Kiln AI - License Information … This repository
contains components under different licenses"* (Copyright Chesterfield
Laboratories Inc.). Parts are permissive; the repository as a whole is not a
single MIT grant, and anyone planning to vendor code from it must read the
per-component terms. **5,076 GitHub stars** (API, 2026-09-19), 200+ models across OpenAI, Anthropic, Gemini, Ollama, OpenRouter,
Bedrock, Azure OpenAI, Groq, Fireworks, Together, Cerebras and HF
[[src](https://kiln.tech/)] (`getkiln.ai` 301s here).

**Kiln is the free version of the product's single-player mode.** Anyone
evaluating us will find it. What it cannot do is production traces, multi-tenant
serving, hardware optimisation or an online A/B — which is a clean statement of
where our value must live.

---

## 8. Routing and gateways — the alternative to distillation

The honest competitive framing: a customer who wants a cheaper bill has a much
lazier option than distillation, and it works on day one.

### 8.1 Not Diamond

Model routing that selects the right model per task rather than defaulting to the
most expensive. **Vendor-claimed**: *"5% + Accuracy gains"*, *"20% + Cost
savings"*, *"2x Faster dev cycles"*, with a worked calculator taking 1,000
engineers at $300/mo from $4.8M to $3.6M annually (−25 %). Vendor-agnostic:
integrates *"with your existing harnesses and gateway"*. SOC-2 and ISO 27001.
Named users: Hugging Face, Dropbox, IBM, OpenRouter, Replicated. No public pricing
[[src](https://www.notdiamond.ai/)].

**20 % for zero engineering effort and zero risk** is the number every distillation
pitch is measured against. Doc 00 §4.4 identifies tiering down within the
incumbent family as the honest comparison; routing *automates* that tier-down.
Our pitch must be built for the customer who has already routed and still has a
bill.

### 8.2 Martian

**Has pivoted away from routing-as-a-product.** Now self-described as *"a team of
researchers who've left the big labs to focus on understanding machine
intelligence"*, working on measurement, explanation and application — ARES (RL for
coding agents), Code Review Bench, mechanistic interpretability, K-Steering. They
state they intend to commercialise *"not through consulting or one-off projects,
but through products that scale with global LLM usage"*, without naming a product.
A banner references *"Thesean AI: A Lab Building Best Execution for LLMs"*. No
pricing [[src](https://withmartian.com/)]. ⚠️ The relationship between Martian and
"Thesean AI" was not established.

### 8.3 Portkey

AI Gateway + observability + guardrails + prompt management, with routing,
fallbacks, load balancing and retries. Open Source (self-hosted, free) / Developer
(free, *"10k recorded logs per month"*, 3-day retention) / **Production $49/mo**
(*"100k recorded logs per month"*, *"$9 per additional 100k requests"*, semantic
caching, alerts, RBAC) / Enterprise (*"10 Mn Plus Recorded logs per month"*, custom
retention, private cloud, *"Advanced Compliance (SOC2 Type 2, GDPR, HIPAA)"*).
VPC-managed hosting and private tenancy available
[[src](https://portkey.ai/pricing)].

### 8.4 LiteLLM

Open-source gateway to *"140+ providers"* and *"1,892 unique models"* behind one
OpenAI-compatible API; spend tracking with hard per-team budgets and automatic
blocking; guardrails including **PII masking** and prompt-injection detection;
audit trails. **meas.** performance claims: the Rust gateway adds *"0.66 ms at
p99"* (*"3.5× lower than competitors"*) at *"2,800+ requests per second"*; *"over
240 million Docker pulls"*. Free tier includes virtual keys, budgets, teams, load
balancing and guardrails; Enterprise adds SSO, JWT, SCIM, audit logs. Customer
datum: AT&T costs for advanced tasks *"fell by as much as 56%"*
[[src](https://www.litellm.ai/)].

**The gateway layer is where S1+S2 capture is cheapest to implement and hardest to
defend.** LiteLLM is free, self-hosted, adds 0.66 ms, already masks PII, and is
already in the customer's stack. Doc 00 §1.2's S1 failure mode — "gateway adds
latency or becomes a SPOF" — has a published answer here, and it is not ours.
**Strong implication: do not build a gateway. Ship a LiteLLM/Portkey callback.**

---

## 9. Open-source building blocks (the "don't buy, don't build" column)

### 9.1 HuggingFace TRL

The decisive find for doc 05's build/buy decision. TRL's trainer taxonomy now
includes a **dedicated knowledge-distillation family**
[[src](https://huggingface.co/docs/trl/index)]:

| Category | Trainers |
|---|---|
| Online | `GRPOTrainer`, `RLOOTrainer` |
| Reward modelling | `RewardTrainer`, `PRMTrainer` (exp.) |
| Offline | `SFTTrainer`, `DPOTrainer`, `KTOTrainer`, + `BCO`/`CPO`/`ORPO`/`TPO` (exp.) |
| **Knowledge distillation** | **`DistillationTrainer`** (stable); experimental: **`GKDTrainer`**, **`MiniLLMTrainer`**, `GOLDTrainer`, `IWOPDTrainer`, `SDFTTrainer`, `SDPOTrainer`, `SSDTrainer`, `AsyncDistillationTrainer` |

Doc 00 §3.1 cites GKD [[arXiv](https://arxiv.org/abs/2306.13649)] and MiniLLM
[[arXiv](https://arxiv.org/abs/2306.08543)] as papers. **They are trainers you can
`pip install`.** TRL also ships a long-context guide that trains Qwen3-8B on
million-token sequences on one 8-GPU node
[[src](https://huggingface.co/docs/trl/index)].

**Consequence for doc 05:** the §3.2 ladder's rungs 2–6 are all covered by one
Apache-licensed library that Baseten's own training product already supports
[[src](https://docs.baseten.co/training/overview)]. Buying a training API (Tinker,
Fireworks) buys *managed infrastructure*, not *methods*. That changes the
build/buy calculus: the method risk is zero, the infrastructure risk is the
whole cost.

### 9.2 DSPy

*"programming—rather than prompting—language models"*; modular Python systems with
optimisers for *"their prompts and weights"*, including GEPA (reflective prompt
evolution) and BootstrapFinetune. **MIT**, **38.1k stars**, 4,715 commits
[[src](https://github.com/stanfordnlp/dspy)]. ⚠️ The optimiser docs pages
(`dspy.ai/learn/optimization/optimizers/`, `docs.dspy.ai`) did not render usable
content on fetch, so per-optimiser mechanics are not sourced here.

DSPy matters for one reason: **prompt optimisation is a cheaper rung than
training, and doc 00 §3.2's ladder does not have it.** Before rung 2
(rejection-sampling SFT), there is "auto-optimise the prompt for the small model",
which is free of GPU cost and of teacher-ToS risk. Databricks ships exactly this
inside Agent Bricks (§3.4). It belongs on the ladder as rung 1b.

### 9.3 OpenPipe ART

*"an open-source RL framework that improves agent reliability by allowing LLMs to
learn from experience"*, GRPO-based, client/server split where completions route
to *"the ART server, which runs the model's latest LoRA in vLLM"*. Notable
recipes: **RULER** for automatic reward generation, **AutoRL** (*"Zero-Data
Training for Any Task"* — *"Train custom AI models without labeled data using
automatic input generation and RULER evaluation"*), **MCP•RL**, and an explicit
**Distillation (SFT)** notebook (*"Distill text-to-SQL from Qwen 3 235B to Qwen
3.6 27B"*). Headline result: **ART·E**, *"a Qwen 2.5 14B email agent outperforming
OpenAI's o3"*, with 15 tracked metrics including *"answer accuracy, number of
turns, and number of hallucinations"*
[[src](https://raw.githubusercontent.com/OpenPipe/ART/main/README.md)].

### 9.4 LoRAX

See §4.5. Apache-2.0 multi-adapter serving, the direct implementation of doc 00
§4.5's highest-leverage architecture decision.

---

## 10. "Inkling" — candidates and verdict

The brief asks explicitly. Answer with evidence:

| Candidate | Evidence | Verdict |
|---|---|---|
| **Thinking Machines' Inkling model family** | Listed as premium trainable models on Tinker's price page — Inkling (64K/256K) and Inkling-Small (64K/256K) — with a *"Limited-time 50% discount"* and a **serverless inference beta** *"for Inkling models only"* at $0.30/$0.06/$1.20 per 1M (Inkling-Small 256K) and $1.00/$0.17/$4.05 (Inkling 256K) [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]. The cookbook names *"Thinking Machines Lab's proprietary Inkling model"* [[src](https://github.com/thinking-machines-lab/tinker-cookbook)] | **Almost certainly what the owner meant.** It sits next to Tinker in the brief, and it *is* a Tinker product |
| **Microsoft Project Bonsai's Inkling DSL** | A machine-teaching language, historically. ⚠️ `learn.microsoft.com/en-us/bonsai/inkling/` returned **404** on 2026-09-19 | Homonym. Unrelated to LLM distillation. Noted only so it is not rediscovered |
| **A separate startup named Inkling** | No evidence found. (An unrelated corporate-learning company of that name predates all of this. ⚠️ Not verified in this session) | No evidence |

⚠️ **Open question retained from doc 00 (#13):** no published benchmarks for
Inkling as a *student* were found. Its train price ($5.61/1M at 64K, $11.23 at
256K, both at the 50 % promotional discount) makes it the most expensive student
on Tinker's list except GLM-5.3 — a house-brand premium with no published
justification. ⚠️ **Corrected 2026-09-19: that ranking only holds at the promo
price.** Tinker's table prints list *and* discounted prices side by side; Inkling
64K lists at **$11.22** and Inkling 256K at **$22.46**
[[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]. At **list**,
Inkling 256K is the most expensive student on the catalogue, above GLM-5.3's
$14.58 (which carries no discount). When the limited-time discount ends, the
house-brand premium roughly doubles.

---

## 11. Master table — every product, URL, and check date

All checked **2026-09-19**. "State" is what the fetched page said on that date.

| # | Product | URL | State | Loop role |
|---:|---|---|---|---|
| 1 | OpenPipe (archive) | https://openpipe.ai/ | Migrated to W&B + CoreWeave; legacy platform **stopped** new training/inference **2026-07-30 — already past** (notice dated 2026-05-18) ⚠️ corrected | The original full loop |
| 2 | CoreWeave — OpenPipe acquisition | https://www.coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning | Announced **2025-09-03**, terms undisclosed | — |
| 3 | W&B Serverless Training (RL) | https://docs.wandb.ai/guides/training/ | **Public preview**; training free during preview | S5 + S1 |
| 4 | W&B Weave | https://wandb.ai/site/weave/ | Live | S2 + S4 |
| 5 | W&B pricing | https://wandb.ai/site/pricing/ | Pro from $60/mo; Weave $0.10/MB overage | — |
| 6 | W&B Inference | https://wandb.ai/site/inference/ | Live; hosts trained adapters. ⚠️ per-token prices not on page | S1 |
| 7 | OpenPipe ART | https://github.com/OpenPipe/ART | Active; Serverless RL front-end | S5 (OSS) |
| 8 | Distil Labs | https://distillabs.ai/ | Live | **Nearest full-loop competitor** |
| 9 | Baseten Training (Jobs GA / Loops EA) | https://www.baseten.co/products/training/ | Live / early access | S5–S6 |
| 10 | Baseten Training docs | https://docs.baseten.co/training/overview | Live | S5–S6 |
| 11 | Baseten pricing | https://www.baseten.co/pricing/ | Live | — |
| 12 | Baseten Chains | https://www.baseten.co/products/chains/ | Live | S1 |
| 13 | Baseten customers | https://www.baseten.co/customers/ | Live | — |
| 14 | Baseten — post-training models | https://www.baseten.co/blog/best-open-source-models-for-post-training/ | Dated **2026-09-15** | — |
| 15 | Baseten × LangChain Loops | https://www.baseten.co/blog/langchain-trains-custom-models-langsmith-engine-baseten-loops/ | Updated **2026-09-15** | Loop-by-partnership |
| 16 | LangSmith | https://www.langchain.com/langsmith | Live; SmithDB, Engine, Gateway | S1–S4 |
| 17 | LangSmith pricing | https://www.langchain.com/pricing-langsmith | Plus $39/seat/mo | — |
| 18 | Tinker | https://thinkingmachines.ai/tinker/ | Live | S5 |
| 19 | Tinker models + pricing | https://tinker-docs.thinkingmachines.ai/tinker/models/ | Live; Inkling 50 % promo | — |
| 20 | Tinker cookbook | https://github.com/thinking-machines-lab/tinker-cookbook | Apache-2.0, 4.1k ★ | S5 (OSS) |
| 21 | On-policy distillation | https://thinkingmachines.ai/blog/on-policy-distillation/ | Published **2025-10-27** | Method |
| 22 | Fireworks pricing | https://fireworks.ai/pricing | Live | — |
| 23 | Fireworks fine-tuning | https://docs.fireworks.ai/fine-tuning/fine-tuning-models | Live; SFT/DPO/RFT, LoRA | S5 |
| 24 | Together pricing | https://www.together.ai/pricing | Live; B200 on-demand **$8.19** (doc had $8.99) ⚠️ corrected; H100 $3.99 promo expires **09/30/26** → $5.49 | — |
| 25 | Together fine-tuning | https://docs.together.ai/docs/fine-tuning-overview | Live; LoRA + full, DPO | S5 |
| 26 | Refuel | https://www.refuel.ai/ | *"Refuel.ai is joining Together AI"* | S3 |
| 27 | Databricks AI | https://www.databricks.com/product/artificial-intelligence | Live | S1–S7 |
| 28 | Agent Bricks (index) | https://docs.databricks.com/aws/en/generative-ai/agent-bricks/ | Dated **2026-09-15** | — |
| 29 | Agent Bricks Custom LLM | https://docs.databricks.com/aws/en/generative-ai/agent-bricks/custom-llm | Dated **2026-09-15**; auto prompt-opt + FT; *"recommends at least 100 inputs"* (not a required minimum) ⚠️ corrected | S3–S7 |
| 30 | Databricks TAO | https://www.databricks.com/blog/tao-using-test-time-compute-train-efficient-llms-without-labeled-data | Dated **2025-03-25**; preview | Method |
| 31 | NVIDIA Data Flywheel | https://github.com/NVIDIA-AI-Blueprints/data-flywheel | **Deprecated April 2026**, Apache-2.0 | Reference loop |
| 32 | NeMo microservices | https://docs.nvidia.com/nemo/microservices/latest/index.html | **Sunset 2026-10-01** | S3–S5 |
| 33 | NeMo Platform | https://www.nvidia.com/en-us/ai-data-science/products/nemo/ | Live; data-flywheel framing | S1–S8 |
| 34 | Adaptive ML | https://www.adaptive-ml.com/ · https://www.adaptive-ml.com/post/joining-datadog | Live; **acquired by Datadog**, vendor post dated **2026-06-30**, terms undisclosed; unconfirmed by acquirer ⚠️ upgraded | S1, S4, S5, S7, S9 |
| 35 | Bedrock Model Distillation | https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html | Live; trains on invocation logs | S3 + S5 |
| 36 | Bedrock distillation prerequisites | https://docs.aws.amazon.com/bedrock/latest/userguide/prequisites-model-distillation.html | **Anthropic teachers withdrawn, no timeline** | — |
| 37 | Azure stored completions + distillation | https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/concept-model-distillation | **Retires 2026-10-15** → Responses API + Agent Traces. ⚠️ the cited URL now redirects to `…/foundry-classic/openai/how-to/stored-completions` ("classic" portal); every quoted fact re-confirmed there | S2, S3, S5 |
| 38 | OpenAI SFT | https://developers.openai.com/api/docs/guides/supervised-fine-tuning | **Winding down** | S5 |
| 39 | OpenAI RFT | https://developers.openai.com/api/docs/guides/reinforcement-fine-tuning | **Winding down**; o4-mini only | S5 |
| 40 | OpenAI distillation | https://developers.openai.com/api/docs/guides/distillation | **Winding down** | S3 + S5 |
| 41 | OpenAI Evals | https://developers.openai.com/api/docs/guides/evals | **Read-only 2026-10-31; shutdown 2026-11-30** | S4 |
| 42 | Anthropic AUP | https://www.anthropic.com/legal/aup | Effective **2025-09-15**; distillation prohibited without authorisation | Constraint |
| 43 | Vertex AI distillation | https://cloud.google.com/vertex-ai/generative-ai/docs/models/distill-text-models | ⚠️ 301 → `docs.cloud.google.com/...` → **404** (independently re-verified by `curl -IL` 2026-09-19); still unresolvable without search | ⚠️ unknown |
| 44 | Mistral fine-tuning | https://docs.mistral.ai/capabilities/finetuning/ | **Deprecated** | S5 |
| 45 | Predibase | https://predibase.com/ | **301 → rubrik.com** (403 at destination) | Gone |
| 46 | LoRAX | https://github.com/predibase/lorax | Apache-2.0, **3,832 ★**, 901 commits, **last push 2026-05-28** (~3.7 mo stale), not archived | S1 (OSS) |
| 47 | Scale GenAI Platform | https://scale.com/genai-platform | Live; FedRAMP High | S2–S5 |
| 48 | Scale Data Engine | https://scale.com/data-engine | Live; RLHF, video/3D | S3 |
| 49 | Snorkel | https://snorkel.ai/ · https://snorkel.ai/platform/ | Live; Flow / Evaluate / Expert DaaS | S3 + S4 |
| 50 | Kiln | https://kiln.tech/ (from getkiln.ai) · https://github.com/Kiln-AI/Kiln | ⚠️ **mixed licence** (`NOASSERTION`; LICENSE.txt: *"components under different licenses"*), **5,076 ★** | S3–S5 (local) |
| 51 | Langfuse pricing | https://langfuse.com/pricing | Live; OSS self-host free | S2 + S4 |
| 52 | Braintrust pricing | https://www.braintrust.dev/pricing | Live; Loop agent on Pro | S2 + S4 |
| 53 | Arize pricing | https://arize.com/pricing/ | Live; Phoenix OSS; multi-modal evals are **image, voice, pdf — not video** ⚠️ resolved | S2 + S4 |
| 54 | Galileo | https://galileo.ai/ | Dated **2026-09-11**; Luna distilled judges | S2 + S4 |
| 55 | Confident AI / DeepEval | https://www.confident-ai.com/pricing | Live; $1/GB-month | S2 + S4 |
| 56 | Patronus AI | https://www.patronus.ai/ | **Pivoted** to Digital World Models | S4 (research) |
| 57 | Humanloop | https://humanloop.com/ | **Acquired by Anthropic; platform sunset** | Dead |
| 58 | Atla | https://www.atla-ai.com/ | ⚠️ **404** | ⚠️ unknown |
| 59 | Pi Labs | https://withpi.ai/ | ⚠️ **NXDOMAIN** | ⚠️ unknown |
| 60 | Lamini | https://lamini.ai/ | ⚠️ **TLS handshake failure** | ⚠️ unknown |
| 61 | Zenbase | https://zenbase.ai/ | ⚠️ **Redirects to thesynthesis.company** (an unrelated literature-review product) — pivoted or domain reassigned | ⚠️ gone |
| 62 | DSPy | https://github.com/stanfordnlp/dspy | MIT, 38.1k ★ | Prompt+weight opt (OSS) |
| 63 | Not Diamond | https://www.notdiamond.ai/ | Live; routing | Alternative to distillation |
| 64 | Martian | https://withmartian.com/ | **Pivoted** to interpretability research | — |
| 65 | Portkey pricing | https://portkey.ai/pricing | Live; $49/mo Production | S1 + S2 |
| 66 | LiteLLM | https://www.litellm.ai/ | OSS + Enterprise | S1 + S2 |
| 67 | Modal pricing | https://modal.com/pricing | Live | Compute |
| 68 | Anyscale | https://www.anyscale.com/ | Live | Compute |
| 69 | HF pricing | https://huggingface.co/pricing | Live | Compute + hosting |
| 70 | HF AutoTrain | https://huggingface.co/autotrain | Live | S5 |
| 71 | HF TRL | https://huggingface.co/docs/trl/index | Live; full distillation trainer family | S5 (OSS) |
| 72 | Arcee AI | https://www.arcee.ai/ | Live; open-weight models | Student supply |
| 73 | Rubrik Agent Cloud | https://www.rubrik.com/products/rubrik-agent-cloud | **HTTP 403** | ⚠️ Predibase destination |
| 74 | Datadog press | https://www.datadoghq.com/about/latest-news/press-releases/ | Rendered without articles | ⚠️ Adaptive ML unconfirmed |

---

## 12. Coverage matrix

**●** first-class · **◐** partial/preview/constrained · **○** absent.
Stage definitions in §2.

| Platform | S1 Endpoints | S2 Traces | S3 Annotate | S4 Data+Evals | S5 Train | S6 Ckpt | S7 Gate | S8 HW opt | S9 A/B |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **Distil Labs** | ● | ◐ | ◐ | ◐ | ● | ⚠️ | ● | ● | ◐ |
| **W&B + CoreWeave (ex-OpenPipe)** | ● | ● | ◐ | ● | ● | ● | ◐ | ○ | ○ |
| **LangSmith + Baseten** | ● | ● | ◐ | ● | ● | ● | ◐ | ● | ○ |
| **Databricks (Agent Bricks + TAO)** | ● | ◐ | ◐ | ● | ● | ● | ● | ○ | ○ |
| **NVIDIA NeMo Platform** | ● | ◐ | ◐ | ● | ● | ◐ | ◐ | ● | ○ |
| **Adaptive ML** | ● | ◐ | ◐ | ● | ● | ◐ | ● | ○ | ● |
| **AWS Bedrock Distillation** | ● | ◐ | ● | ◐ | ● | ◐ | ○ | ○ | ○ |
| **Azure AI Foundry** | ● | ● | ◐ | ● | ● | ◐ | ◐ | ○ | ○ |
| **Scale GenAI Platform** | ◐ | ● | ● | ● | ◐ | ○ | ◐ | ○ | ○ |
| **Baseten** | ● | ○ | ○ | ○ | ● | ● | ○ | ● | ◐ |
| **Fireworks** | ● | ○ | ○ | ◐ | ● | ◐ | ○ | ● | ○ |
| **Together (+Refuel)** | ● | ○ | ◐ | ○ | ● | ● | ○ | ● | ○ |
| **Tinker** | ◐ | ○ | ○ | ◐ | ● | ● | ○ | ○ | ○ |
| **OpenAI (winding down)** | ● | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ○ | ○ |
| **LangSmith alone** | ◐ | ● | ◐ | ● | ○ | ○ | ◐ | ○ | ○ |
| **Langfuse** | ○ | ● | ◐ | ● | ○ | ○ | ◐ | ○ | ○ |
| **Braintrust** | ○ | ● | ◐ | ● | ○ | ○ | ◐ | ○ | ○ |
| **Arize** | ○ | ● | ◐ | ● | ○ | ○ | ◐ | ○ | ○ |
| **Galileo** | ○ | ● | ◐ | ● | ○ | ○ | ● | ○ | ○ |
| **Confident AI** | ○ | ● | ◐ | ● | ○ | ○ | ◐ | ○ | ○ |
| **Snorkel** | ○ | ○ | ● | ● | ○ | ○ | ◐ | ○ | ○ |
| **Kiln (OSS)** | ○ | ○ | ● | ● | ● | ◐ | ● | ○ | ○ |
| **LiteLLM / Portkey** | ● | ● | ○ | ○ | ○ | ○ | ○ | ○ | ◐ |
| **Not Diamond** | ◐ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ◐ |
| **Modal / Anyscale** | ● | ○ | ○ | ○ | ◐ | ◐ | ◐ | ◐ | ○ |
| **This platform (target)** | ● | ● | ● | ● | ● | ● | ● | ● | ● |

**The two columns that decide the strategy:**

- **S9 (A/B + rollout): one ●, from Adaptive ML.** Nobody else in this survey
  sells a defensible online non-inferiority test. Databricks optimises *offline*
  against a dataset it merely *recommends* be ≥100 inputs (⚠️ corrected
  2026-09-19 from "gates on 100 inputs" — it is a recommendation, not a gate, and
  no gate is described at all, which is the stronger finding); Distil Labs
  approves *offline* before ramping; the eval vendors compare experiments, not
  traffic.
- **S8 (hardware optimisation): five ●, all of them inference vendors.** And only
  **Distil Labs** couples S8 to the training loop (*"fine-tunes, quantizes, and
  deploys"*) [[src](https://distillabs.ai/)]. Doc 00 §1.2's **gate-twice**
  invariant — re-run the eval on the *served, quantised* artifact — is not visible
  as a product feature anywhere in this survey.

---

## 13. Positioning map

### 13.1 Axis 1 — loop coverage vs infrastructure ownership

Vertical: how much of S1–S9 the vendor sells. Horizontal: who owns the GPUs and
the data plane (left = the vendor's SaaS; right = the customer's own
infrastructure/VPC/on-prem).

```
 full loop  │ W&B+CoreWeave      Databricks          NeMo Platform
 (S1–S9)    │ Distil Labs        Adaptive ML ──────► (VPC/on-prem)
            │ Azure Foundry      ▲
            │ Bedrock            │  ◄── ** THIS PLATFORM aims here **
            │                    │      (full loop, customer-chosen infra)
 ───────────┼──────────────────────────────────────────────────────────
 train+     │ Baseten   Fireworks   Together                  Modal
 serve      │ Tinker                                          Anyscale
 (S5–S8)    │                                                 HF Endpoints
 ───────────┼──────────────────────────────────────────────────────────
 capture+   │ Braintrust  Galileo   LangSmith    Arize     Langfuse (OSS)
 eval       │ Confident AI          Scale        Snorkel   Phoenix (OSS)
 (S2+S4)    │                                              Kiln (local)
 ───────────┼──────────────────────────────────────────────────────────
 gateway    │ Portkey                                       LiteLLM (OSS)
 only (S1)  │ Not Diamond
            └──────────────────────────────────────────────────────────
              vendor-owned infra ─────────────────► customer-owned infra
```

Three readings:

1. **The top-left quadrant is crowded and the top-right is nearly empty.** Full
   loop *plus* customer-owned infrastructure is occupied only by NeMo Platform
   (which is a toolkit, not a service) and Adaptive ML (which pairs it with
   forward-deployed engineers). For a customer who will not send production
   traffic to a third party — the regulated buyer — there is almost nothing.
2. **Every row below the top is commoditised**, with a free open-source option in
   the right-hand column of each. Building in any of those rows is building a
   worse Langfuse, a worse LiteLLM, or a worse Baseten.
3. **Movement is vertical and upward from both ends.** Observability vendors are
   acquiring or partnering *up* into training (W&B, LangSmith, Datadog?); training
   vendors are acquiring *up* into data (Together←Refuel). The middle will be
   squeezed from both sides within a year. ⚠️ Inference.

### 13.2 Axis 2 — text vs multimodal

| Capability | Text | Images | **Video understanding** |
|---|---|---|---|
| Trace capture | Everyone | ◐ (Arize *image/voice/pdf*, Weave claims multimodal) | ⚠️ nobody verified |
| Annotation at scale | Scale, Snorkel, Refuel | Scale | **Scale only** (LiDAR/video heritage) [[src](https://scale.com/data-engine)] |
| Training | Everyone | Fireworks VLM SFT [[src](https://docs.fireworks.ai/fine-tuning/fine-tuning-models)]; Together vision track [[src](https://docs.together.ai/docs/fine-tuning-overview)]; Tinker VLM classification recipe [[src](https://github.com/thinking-machines-lab/tinker-cookbook)] | ⚠️ none found explicitly for video |
| Evaluation | Everyone | Arize *"Multi-modal evaluation (image, voice, pdf)"* [[src](https://arize.com/pricing/)] | **none — Arize's own modality list excludes video** (⚠️ resolved 2026-09-19, §6.3) |
| Distillation, proven at parity | Yes (§3, §5) | ⚠️ thin | **⚠️ no public result found** |

**This is unchanged from doc 00 §3.4, and a full market scan did not move it.**
Nobody surveyed sells video-understanding distillation. Two readings, and the
platform must pick one deliberately:

- **It is whitespace.** The economics are unusually good (doc 00 §3.4: the
  240-frame cap makes a 10-minute clip cost the same as a 2-minute one), and a
  first credible product wins a category.
- **It is a graveyard.** Nobody sells it because evaluating it is brutally hard
  and the buyers are few.

The cheap way to find out is §17, item 3.

---

## 14. The whitespace — what nobody offers well

Ordered by how defensible the gap is, with the evidence for each.

### 14.1 Online A/B with a defensible statistical claim (S9)

**Evidence of absence:** one ● in the entire S9 column (§12), from Adaptive ML,
whose page says *"Guarantee performance with A/B testing"*
[[src](https://www.adaptive-ml.com/)] without describing a design, a power
calculation or a stopping rule. Databricks *"recommends at least 100 inputs"* and
describes no gate at all (⚠️ corrected 2026-09-19 from *"a minimum of 100
inputs"*, which the page does not say)
[[src](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/custom-llm)].
Distil Labs approves offline, then *"scal[es] to 100%"* [[src](https://distillabs.ai/)].
Braintrust, Arize, Langfuse and Confident AI all sell *experiment comparison*, not
traffic allocation.

**Why it stays empty:** it is the least glamorous and most statistically
demanding part of the loop, it requires being in the production request path
(which eval vendors are not), and getting it wrong is a customer incident rather
than a bad chart.

**Why it is the right wedge:** doc 00 §5 already establishes that the customer is
buying *permission to switch*, and permission is manufactured by S9, not by S5.
Everything else in the loop can be bought (§16).

### 14.2 Gate-twice — evaluating the artifact that actually serves (S7 ∘ S8)

**Evidence of absence:** Distil Labs is the only vendor whose page couples
quantisation to the pipeline at all (*"fine-tunes, quantizes, and deploys"*)
[[src](https://distillabs.ai/)], and even there the evaluation is described as
happening at the "Approve" step without stating whether it runs on the quantised
artifact. Every training vendor (Baseten, Fireworks, Together, Tinker) hands back
a checkpoint; every eval vendor scores a *model endpoint* without knowing its
serving config.

**Why it matters commercially:** this repo already documents per-GPU format
substitutions that change what executes
([`matrix/fit-matrix.md`](../matrix/fit-matrix.md),
[`matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md)). A parity claim
made on a BF16 checkpoint and then served in NVFP4 is a claim about a different
model. Nobody surveyed makes this promise, and we can — from work already done.

### 14.3 The prompt-stack as a versioned artifact

**Evidence of absence:** no vendor surveyed treats the customer's system prompt as
part of the model artifact. Azure's distillation trains on stored completions and
explicitly makes the training file non-exportable
[[src](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/concept-model-distillation)];
Bedrock filters logs by `requestMetadata`
[[src](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html)]
but says nothing about prompt versioning; LangSmith has a Prompt Hub and
LangSmith separately has training, but the link between "this prompt version" and
"this checkpoint" is the customer's problem.

Doc 00 §2.2 identified this as the problem nobody budgets for. It remains
unaddressed by the market. A prompt-hash in every trace, carried into the
artifact, and a hard refusal to claim parity across a prompt change, is a
differentiator that costs almost nothing to build.

### 14.4 Judge validation as a priced, auditable stage

**Evidence of absence:** Braintrust *meters* scores ($1.50–$2.50/1k)
[[src](https://www.braintrust.dev/pricing)], Galileo *distils* judges (§6.5),
Databricks *infers* evaluation criteria automatically
[[src](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/custom-llm)].
**Nobody publishes judge-vs-human agreement as a product artifact.** Doc 00 §5.1
requires ≥200 human-adjudicated examples per task and a κ floor before a judge
ships. That is a sellable, auditable deliverable and no competitor offers it.

### 14.5 Regulated / on-prem full loop

**Evidence:** §13.1's empty top-right. Distil Labs offers on-prem
[[src](https://distillabs.ai/)] and Galileo offers on-prem for the eval half
[[src](https://galileo.ai/)]; Scale has FedRAMP High for the data half
[[src](https://scale.com/genai-platform)]. **No single vendor offers the whole
loop inside the customer's boundary.** This is also the segment where the teacher
problem is least severe, because an on-prem customer is likelier to accept an
open-weights teacher.

### 14.6 Video understanding

§13.2. Unclaimed, unproven, and possibly unbuildable. Treat as a research bet, not
a product line, until §17 item 3 resolves.

### 14.7 What is emphatically *not* whitespace

State this so nobody wastes a quarter on it: **trace capture, gateways, eval
harnesses, LLM-judge libraries, LoRA training APIs, multi-adapter serving and
prompt optimisation are all solved, cheap, and often free.** Langfuse self-hosts
for $0, LiteLLM adds 0.66 ms, TRL ships nine distillation trainers, LoRAX is
Apache-2.0, DSPy is MIT. Building any of these is a negative-value activity.

---

## 15. Pricing benchmarks

### 15.1 GPU-hour — dedicated inference/training

`est.` conversions from per-minute and per-second list prices; all list, on-demand,
no commit.

| GPU | Baseten [[src](https://www.baseten.co/pricing/)] | Modal [[src](https://modal.com/pricing)] | Fireworks [[src](https://fireworks.ai/pricing)] | Together [[src](https://www.together.ai/pricing)] | HF Endpoints [[src](https://huggingface.co/pricing)] |
|---|---:|---:|---:|---:|---:|
| T4 | $0.631 | $0.590 | — | — | $0.50 |
| L4 | $0.848 | $0.799 | — | — | $0.80 |
| A10G | $1.207 | — | — | — | — |
| A100 80 GB | $4.000 | $2.498 | — | — | $2.50 |
| H100 80 GB | **$6.500** | **$3.949** | **$8.00** | **$3.99**¹ | **$4.50** |
| H200 | — | — | $8.00 | contact sales | — |
| B200 | **$9.980** | — | **$13.00** | **$8.19**² | **$9.25** |
| B300 / GB300 | — | **$7.099** | $15.00–$20.00 | contact sales | — |

¹ Together states H100 on-demand is *"promotional pricing"*, **$3.99 down from
$5.49, valid through 09/30/26** (re-read 2026-09-19 — the promotion expires in
eleven days, after which this column's H100 row reverts to $5.49); their reserved
clusters are **$3.19–$3.69/h** (H100) and **$6.79–$7.99/h** (B200) on 7–180+ day
terms [[src](https://www.together.ai/pricing)]. Fireworks charges **1.5×** for
region-restricted deployments [[src](https://fireworks.ai/pricing)].

² ⚠️ **Corrected 2026-09-19:** the doc printed **$8.99** for Together B200
on-demand. The page lists **$8.19/h** [[src](https://www.together.ai/pricing)].
Both the cell and the B200 spread below are recut.

**Decision rule.** The H100 spread is **$3.95 → $8.00 (2.03×)** and the B200
spread is **$8.19 → $13.00 (1.59×)** (`est.`, recomputed 2026-09-19; the doc
previously said 1.45× off the wrong Together price) for nominally the same
silicon. ⚠️ The H100 low end is doubly promotional — Modal's $3.95 is list, but
Together's $3.99 expires 09/30/26 and reverts to $5.49, which would narrow the
H100 spread to **1.47×** on the same table. At the ~$7.3k/month
per-replica floor doc 00 §4.5 computes, a 2× price difference on the serving tier
is worth more than most inference optimisations. **Concretely: never quote a
customer a self-hosting number without naming the vendor tier it assumes**, and
compare against this repo's bare-metal figures
([`research/scaling/`](../scaling/)) before choosing.

⚠️ These are list prices for on-demand, single-GPU instances. Node-level pricing,
interconnect, committed-use discounts and egress are not comparable across these
five vendors from their public pages.

### 15.2 Training, per 1M tokens

| Vendor | Method | Price/1M | Source |
|---|---|---:|---|
| **Fireworks** | LoRA SFT ≤16B | $0.50 | [[src](https://fireworks.ai/pricing)] |
| | LoRA SFT 16.1–80B | $3.00 | |
| | LoRA SFT 80–300B | $6.00 | |
| | LoRA SFT >300B | $10.00 | |
| | Full-param SFT | **2× LoRA** ($1.00–$20.00) | |
| | DPO | **2× SFT rates** | |
| | **RFT** | **billed per GPU-hour** at on-demand rates | |
| **Together** | SFT | $0.34–$7.00 (min charge $4–$60) | [[src](https://www.together.ai/pricing)] |
| | DPO | $0.84–$17.50 | |
| **Tinker** | train, Nemotron-3.5-Lightning-30B | **$0.44** (50 % promo; **list $0.88**) ⚠️ | [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)] |
| | train, Qwen3-8B (32K) | **$0.44** (no discount) — ties for cheapest ⚠️ | |
| | train, Qwen3.5-4B | $0.737 | |
| | train, Qwen3.6-35B-A3B | $1.177 | |
| | train, Nemotron-3-Super-120B | $1.276 (50 % promo; **list $2.552**) ⚠️ | |
| | train, DeepSeek-V3.1 | $3.718 | |
| | train, **Qwen3.8-27B (64K)** | **$4.103** | |
| | train, Kimi-K2.6 (32K) | $4.84 | |
| | train, Nemotron-3-Ultra-550B | $5.478 (50 % promo; **list $10.956**) ⚠️ | |
| | train, Inkling (64K) | $5.61 (50 % promo; **list $11.22**) | |
| | train, Inkling (256K) | $11.23 (50 % promo; **list $22.46**) | |
| | train, GLM-5.3 (256K) | **$14.58** (no discount; most expensive **at promo prices only** — see below) | |
| **Mistral** | SFT (deprecated) | min **$4/job** + **$2/model/month** storage | [[src](https://docs.mistral.ai/capabilities/finetuning/)] |
| **W&B Serverless RL** | adapter training | **$0 during preview** (inference + artifact storage billed) | [[src](https://docs.wandb.ai/guides/training/)] |

**Two structural facts worth more than the numbers.**
(a) **RFT is metered by GPU-hour, not tokens** at Fireworks — because rollouts
make token counts unpredictable. Any RL-based rung on doc 00 §3.2's ladder has an
open-ended cost, and must be sold with a cap.
(b) **Tinker's train price spans 33×** ($0.44 → $14.58) across its catalogue.
⚠️ **Corrected 2026-09-19 — that span mixes two price bases.** The doc treated
only the Inkling rows as promotional; in fact **every Nemotron row also carries
"Limited-time 50% discount"**, so $0.44, $1.276 and $5.478 are all discounted
prices while $0.737, $1.177, $3.718, $4.103, $4.84 and $14.58 are list. On a
consistent **list** basis the catalogue runs **$0.44 (Qwen3-8B, undiscounted) →
$22.46 (Inkling 256K) = 51×**, or **$0.88 → $22.46 = 25.5×** among discounted-row
list prices. The structural point survives on any basis — the choice of student
base is a first-order cost decision, not a modelling detail — but **every Tinker
figure in this document that relies on a Nemotron or Inkling row is a
promotional price that can double without notice.** This is the same rule
Baseten states as *"Total parameters limit speed"* and *"Every token the model
generates requires math proportional to its active parameters"*
[[src](https://www.baseten.co/blog/best-open-source-models-for-post-training/)].

Re-checking doc 00 §4.3(b) against this table: 100k examples × 4,512 tokens × 3
epochs = **1,353.6M tokens**; at Tinker's Qwen3.8-27B rate of $4.103/1M that is
**≈$5,554** (`est.`; exactly $5,553.79 — ⚠️ **corrected 2026-09-19 from $5,555**,
recomputed with `python3`), which matches doc 00 §4.3(b)'s ≈$5,554 rather than
disagreeing with it by $1 as the doc's own figure did. At Fireworks' LoRA SFT
rate for a 16–80B model ($3.00/1M) the same job is **≈$4,061** (`est.`; exactly
$4,060.80 — ⚠️ corrected from $4,062); at Nemotron-3.5-Lightning-30B on Tinker
($0.44/1M **promotional**, $0.88 list), **≈$596** (`est.`) — or **≈$1,191 at
list**. **The training line item can vary ~9× on student choice alone** (9.33×;
**4.7× if the Nemotron promotion ends**), and it is still not the dominant cost
(doc 00 §4.3c).

### 15.3 Inference, per 1M tokens (serverless open models)

| Model | Input | Cached | Output | Blended `est.`¹ | Source |
|---|---:|---:|---:|---:|---|
| GLM-5.3-Flash | $0.15 | $0.03 | $0.50 | $0.193 | [[Baseten](https://www.baseten.co/pricing/)] |
| DeepSeek V4.1 Flash | $0.30 | $0.03 | $1.20 | $0.424 | [[ibid.](https://www.baseten.co/pricing/)] / [[Together](https://www.together.ai/pricing)] (same list) |
| NVIDIA Nemotron 3 Ultra | $0.60 | $0.12 | $2.40 | **$0.870** ⚠️ | [[Baseten](https://www.baseten.co/pricing/)] |
| DeepSeek V4 Pro | $1.74 | $0.145 | $3.48 | $1.577 | [[ibid.](https://www.baseten.co/pricing/)] |
| GLM-5.3 | $1.40 | $0.14 | $4.40 | $1.678 | [[ibid.](https://www.baseten.co/pricing/)] |
| Kimi K3 | $3.00 | $0.30 | $15.00 | $4.988 | [[ibid.](https://www.baseten.co/pricing/)] / [[Together](https://www.together.ai/pricing)] |
| **Inkling-Small (256K)** | $0.30 | $0.06 | $1.20 | **$0.435** ⚠️ | [[Tinker](https://tinker-docs.thinkingmachines.ai/tinker/models/)] |
| **Inkling (256K)** | $1.00 | $0.17 | $4.05 | **$1.451** ⚠️ | [[ibid.](https://tinker-docs.thinkingmachines.ai/tinker/models/)] |
| Qwen3.5 9B | $0.17 | — | $0.25 | ⚠️ | [[Together](https://www.together.ai/pricing)] |
| Llama 3.3 70B | $1.04 | — | $1.04 | ⚠️ | [[ibid.](https://www.together.ai/pricing)] |

¹ METHODOLOGY §6 blend, using each row's **listed** cached rate:
`0.375×c_in + 0.375×c_cached + 0.25×c_out`. Rows without a listed cached price are
left blank rather than assuming 10 %.

⚠️ **Three cells corrected 2026-09-19.** Every input price in this table was
re-verified against its source page and is right; three *blended* cells were
arithmetically wrong and are recut with `python3`: Nemotron 3 Ultra
**$0.848 → $0.870**, Inkling-Small (256K) **$0.437 → $0.435**, Inkling (256K)
**$1.427 → $1.451**. The other five sourced rows recompute exactly. The Inkling
serverless prices themselves are confirmed ($0.30/$0.06/$1.20 and
$1.00/$0.17/$4.05, beta, Inkling-family only)
[[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)].

**Baseten and Together list identical prices** for DeepSeek V4.1 Flash, GLM-5.3-
Flash and Kimi K3. That is a commodity price, not a competitive one — and it means
the serverless middle option in doc 00 §4.5 is stable across vendors.

### 15.4 Observability and eval — the widest spread in the survey

| Vendor | Entry | Mid | Enterprise | Usage metric | Source |
|---|---|---|---|---|---|
| **Langfuse** | $0 (50k units/mo) | $29 / $199 | $2,499 | **$6.00–$8.00 per 100k units** | [[src](https://langfuse.com/pricing)] |
| **Confident AI** | $0 | $200 | $2,000 | **$1.00/GB-month** of trace spans | [[src](https://www.confident-ai.com/pricing)] |
| **Arize** | $0 (1 GB, 15 d) | $50 (10 GB, 30 d) | custom | volume; **no per-seat** | [[src](https://arize.com/pricing/)] |
| **Portkey** | $0 (10k logs) | $49 (100k logs) | custom (10M+) | **$9 per 100k requests** | [[src](https://portkey.ai/pricing)] |
| **Braintrust** | $0 (1 GB, 10k scores) | $249 | custom | **$3–$4/GB + $1.50–$2.50 per 1k scores** | [[src](https://www.braintrust.dev/pricing)] |
| **LangSmith** | $0 (1 seat, 5k traces) | **$39/seat/mo** (10k traces) | custom | **LCU $1.50 / LSU $1.00** | [[src](https://www.langchain.com/pricing-langsmith)] |
| **W&B** | $0 (1 GB Weave) | $60/mo (1.5 GB) | custom | **$0.10/MB = $100/GB** `est.` | [[src](https://wandb.ai/site/pricing/)] |
| **HF** | $9/mo Pro | $20/seat Team | $50/seat Enterprise | storage $8–$18/TB/mo | [[src](https://huggingface.co/pricing)] |

**The spread on trace ingestion is ~100×**: Confident AI's $1/GB-month against
W&B Weave's $0.10/MB ($100/GB) `est.`. They are not measuring identically —
GB-month of retained spans vs GB ingested — but even generously reconciled this is
an order of magnitude, and Confident AI says so out loud: *"at least 3 times
cheaper than alternatives"* [[src](https://www.confident-ai.com/pricing)].

**Implication for our COGS.** Doc 00 §4.5 says our cost of goods is dominated by
idle GPU. That is true for *serving*. For **S2 at scale it is trace storage**, and
the pricing here shows the difference between a good and a bad choice is 100×.
Self-hosted Langfuse at $0 licence (we pay only storage) is the default; anything
else needs a reason.

### 15.5 Per-seat

Only three vendors in the survey price per seat: LangSmith **$39/seat/mo**
[[src](https://www.langchain.com/pricing-langsmith)], HF **$20/$50 per user**
[[src](https://huggingface.co/pricing)], and W&B implicitly via Pro
[[src](https://wandb.ai/site/pricing/)]. Langfuse (unlimited users from $29),
Arize (*"unlimited users across all tiers"*), Braintrust (*"Unlimited users"* on
free) and Confident AI (unlimited seats from $200) have all moved off seats to
volume. **Do not price per seat.** The market has already decided; a seat-priced
product in 2026 reads as legacy, and seats actively discourage the trace volume
we need the customer to send us.

---

## 16. What to learn, copy, or buy from each

| Source | The one thing to take | Why | Cost to adopt |
|---|---|---|---|
| **Distil Labs** | *"Route 1% of production traffic"* + *"one day of traffic"* as the pilot ask | Converts an alarming data-access request into a bounded, cheap, reversible experiment. This is a **sales mechanic**, and it is better than anything in doc 00 | Free |
| **Distil Labs** | Approve-gate shows **cost, accuracy and latency together** before ramp | Matches invariants I1/I2/I3 exactly; one screen, three numbers, one decision | Low |
| **Galileo** | **Distil the judge.** Train a small evaluator against the expensive judge, validate it, run it on 100 % of traffic (*"96% lower cost"* vendor-claimed) | Solves doc 00 §5.1's cost problem *and* the sampling problem, using the technique we already sell. Highest-leverage idea in this document after §14.1 | Medium — needs the judge-validation gold set anyway (§14.4) |
| **Databricks TAO** | Scoring-based improvement **without labelled data**, beating fine-tuning on 4,800–8,137 labelled examples (vendor-claimed) | Removes the teacher from the critical path, which defuses doc 00 §8.1. Add as a rung on the §3.2 ladder | Medium |
| **Databricks Agent Bricks** | **Automatic prompt optimisation before training**; auto-infer eval criteria from data | The cheapest rung. Doc 00 §3.2's ladder starts at "swap the model"; it should start at "optimise the prompt for the small model" (DSPy, §9.2) | Low — DSPy is MIT |
| **AWS Bedrock** | `requestMetadata` filtering of invocation logs into a distillation job | The right primitive for per-use-case slicing of a customer's traffic. Copy the shape | Low |
| **AWS Bedrock** | The **absence** of Anthropic teachers, with *"no confirmed timeline"* | The strongest available evidence on doc 00 §8.1. Design for open-weights and first-party teachers; never build a roadmap on a Claude teacher | Free (it's a constraint) |
| **Thinking Machines** | **On-policy distillation**, and the cookbook's **multi-teacher** recipes | The default method (doc 00 §3.2), with the compute case measured: 1,800 vs 17,920 GPU-h | Free (Apache-2.0 cookbook) |
| **HF TRL** | `DistillationTrainer`, `GKDTrainer`, `MiniLLMTrainer`, `GOLDTrainer`, `SDFTTrainer` | Every method on the ladder, already implemented, already supported by Baseten Training | Free (Apache-2.0) |
| **LoRAX** | Multi-adapter serving with *"heterogeneous continuous batching"*, latency *"nearly constant with the number of concurrent adapters"* | Doc 00 §4.5's highest-leverage architecture decision, available today | Free (Apache-2.0), ⚠️ orphaned |
| **W&B / CoreWeave** | **Multiplex training and inference on one cluster** (*"40% lower cost"*, *"28% faster"* vendor-claimed) | Directly attacks our dominant COGS (idle GPU) | High — scheduler work |
| **Baseten Chains** | Chainlets with per-component hardware | The natural topology for shadow-mode: student + incumbent + judge in one graph, each on right-sized hardware | Low (pattern, not code) |
| **Baseten** | *"Full ownership of your trained weights, no lock-in"*, stated in writing | Counter-positions Azure's non-exportable training files. Put it in the contract, not the marketing page | Free |
| **Adaptive ML** | Ship **forward-deployed engineers** as a priced product | Doc 00 §3.1: the unautomated part is the expert. Stop pretending otherwise; price it | Free (a decision) |
| **LiteLLM / Portkey** | Capture via a **gateway callback**, not our own proxy | 0.66 ms p99, 240M Docker pulls, PII masking included. We will not beat this and should not try | Free |
| **Langfuse** | Self-host the trace store | $0 licence vs up to $100/GB elsewhere | Free |
| **Not Diamond** | The **20 % cost saving for zero effort** benchmark | Every pitch is measured against this. Qualify customers who have already routed | Free |
| **OpenAI** | *"Good evals first! Only invest in fine-tuning after setting up evals."* | The incumbent's own docs make our sequencing argument for us. Quote it | Free |
| **Snorkel / Scale** | Buy expert data rather than build an annotation workforce | Both are established businesses selling exactly this | High ($) |
| **Kiln** | It is the free single-player version of our product | Know what a prospect will compare us to | Free |

---

## 17. Threats, and the counter-positioning

### 17.1 Threat — the frontier labs make distillation unnecessary by cutting prices

Doc 00 §4.4 already establishes the mechanics: cached input at 10 % (2.5 % on
Claude Fable 5.1), batch at −50 %, and tier-down inside the family. Reasserting the
conclusion with the market data now in hand: the honest comparison for a distilled
specialist is **not** Astra/Opus-5 at $8–$17 blended but **Luna/Haiku-class at
$0.38–$1.66 blended, batched, with cached prompts** — against which a self-hosted
Qwen3.8-27B at $0.0602 blended is **6–28×**, not 138×.

**Counter-position.** Sell against the *floor*, not the list price, in the first
meeting. A pitch that survives the customer discovering caching is a pitch that
closes; one that does not, is a pitch that dies in procurement. And note that
**caching cannot touch output tokens**, which are 25 % of the *token* mix but
~75 % of the *cost* at the Opus-5 floor (⚠️ corrected 2026-09-19 — the doc said
"25 % of the blend", which contradicted doc 00 §4.4's "output is 75 % of the
blend"; both are true of different denominators and the doc conflated them:
doc 00 §4.4 gives the Opus-5 floor at 100 % cached input as $6.625/1M, of which
output is the overwhelming majority).

### 17.2 Threat — the frontier labs ship their own distillation

**Already happened, and already reversed.** OpenAI shipped SFT, RFT, distillation,
stored completions and evals — **all winding down**
[[src](https://developers.openai.com/api/docs/guides/supervised-fine-tuning),
[src](https://developers.openai.com/api/docs/guides/evals)]. Azure shipped
stored-completions distillation — **retires 2026-10-15**
[[src](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/concept-model-distillation)].
Mistral shipped fine-tuning — **deprecated**
[[src](https://docs.mistral.ai/capabilities/finetuning/)]. Bedrock still ships it
but **withdrew Anthropic teachers with no timeline**
[[src](https://docs.aws.amazon.com/bedrock/latest/userguide/prequisites-model-distillation.html)].

**Counter-position.** This is the strongest argument in our favour *and* the
strongest argument against us, and we should say both out loud:

- *For us:* the incumbents have demonstrated they will not maintain the tooling
  that helps customers leave their most expensive models. A customer who builds on
  a frontier lab's distillation product is building on something with a published
  end-of-life. **Our neutrality is the product.**
- *Against us:* seven retirements in one survey is not a coincidence. Either the
  demand is small, or it is hard to serve profitably at a platform's cost
  structure. §17.5 is the honest response.

### 17.3 Threat — the observability vendors move up into training

**In progress, with three data points in this survey.** CoreWeave bought W&B *and*
OpenPipe and shipped Serverless RL
[[src](https://www.coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning),
[src](https://docs.wandb.ai/guides/training/)]. LangChain built LangSmith Engine
and trains its models on Baseten Loops
[[src](https://www.baseten.co/blog/langchain-trains-custom-models-langsmith-engine-baseten-loops/)].
Datadog reportedly bought Adaptive ML ⚠️ [[src](https://www.adaptive-ml.com/)].

**They start with the traces, which is the hardest asset to acquire.** We start
with the GPUs and the optimisation work (this repo), which is the *easiest* asset
to rent. That asymmetry is unfavourable and must be answered directly.

**Counter-position.** Do not compete for the trace store — **integrate with it.**
Ship first-class ingestion from Langfuse, LangSmith, Braintrust, Arize and Weave,
and from LiteLLM/Portkey callbacks. Our claim becomes: *bring your traces from
wherever they already live; we do the part none of those vendors do* (S5→S9, and
specifically S8+S9). This also removes the single biggest objection in the sale —
"we are not moving our observability stack for you."

### 17.4 Threat — the GPU clouds vertically integrate

CoreWeave now owns compute + experiment tracking + traces + RL training + serving
+ a customer base. Together owns compute + training + annotation (Refuel). Baseten
owns serving + training + a partnership that supplies traces. **A GPU cloud can
run the loop at marginal cost; we rent from one.**

**Counter-position.** Three answers, in increasing order of conviction:

1. **Be multi-cloud on purpose.** The §15.1 spread is 2× on H100. A platform that
   places each workload on the cheapest adequate tier beats a single-cloud loop on
   COGS, and this repo's matrix work is exactly that capability.
2. **Own the part they structurally will not build.** A GPU cloud has no incentive
   to build an A/B system whose *successful outcome* is the customer buying fewer
   GPU-hours. We do, because we are selling the switch, not the silicon.
3. **⚠️ Or be acquired by one.** That is what happened to OpenPipe. It is a
   legitimate outcome and should be stated in the plan rather than discovered.

### 17.5 Threat — the honest one: seven retirements

Stated plainly because a document that only finds encouraging facts is not
research. In one day's survey: OpenAI FT/RFT/distillation/evals, Azure stored
completions, Mistral FT, NVIDIA Data Flywheel, NVIDIA NeMo microservices — plus
OpenPipe acquired-and-sunset, Humanloop acquired-and-sunset, Predibase absorbed,
Refuel absorbed, Patronus pivoted, Martian pivoted, Zenbase gone, and three
vendors (Atla, Pi Labs, Lamini) whose sites did not respond.

**The pattern is not "distillation does not work."** The evidence that it works is
strong (doc 00 §3.1; §3.4 TAO; §3.5 NVIDIA's 98 %/98.6 %). The pattern is that
**tooling-only businesses in this space do not survive as standalone companies.**
The survivors are (a) infrastructure that also does this, and (b) services with
engineers attached.

**Counter-position, and it should shape the company:** this platform must be
either infrastructure with a loop on top, or a services business with a product
underneath. Doc 00 §9's MVP definition leans toward the second, and Adaptive ML's
forward-deployed-engineer line item is the market's confirmation that it works.
**The pure-SaaS, self-serve, no-humans version is the configuration that every
comparable company in this survey failed to sustain.**

---

## Implications for the platform

### Build

1. **S9 — online A/B and rollout with a real statistical claim.** One vendor in
   this survey scores ● and does not describe a method (§14.1). It is the stage
   that manufactures the *permission to switch* doc 00 §5 says the customer is
   actually buying. This is the wedge. Everything else is table stakes.
2. **Gate-twice (S7 ∘ S8).** Re-run the frozen eval on the **quantised, engine-
   specific serving artifact**, not the BF16 checkpoint. No competitor promises
   this; this repo already has the per-GPU format analysis to do it
   ([`matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md),
   [`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md)).
3. **Prompt-stack-in-the-artifact, with prompt-hash on every trace** (§14.3), and
   an explicit contractual rule that a prompt change voids the parity claim.
4. **Judge validation as a priced, auditable stage** (§14.4): ≥200 human-
   adjudicated examples per task, published κ, re-validation on judge-model change.
   Then **distil the judge** (Galileo's move, §6.5) so it can score 100 % of
   traffic rather than a sample.
5. **Multi-tenant adapter serving**, on LoRAX or an equivalent, because it is the
   only thing that converts N customers' idle GPUs into one busy one (doc 00 §4.5;
   W&B's *"40% lower cost"* multiplexing claim is the same idea shipped).
6. **Multi-cloud placement** across the §15.1 price surface. A 2× H100 spread is
   larger than most engine-level optimisations.

### Buy / adopt

7. **Traces: Langfuse, self-hosted** ($0 licence) — and ingestion adapters for
   LangSmith, Braintrust, Arize and Weave, so a customer never has to move their
   observability stack to buy from us (§17.3).
8. **Capture: a LiteLLM or Portkey callback**, never our own proxy. 0.66 ms p99,
   PII masking included, already in the customer's stack (§8.4).
9. **Training methods: TRL** (Apache-2.0), which already implements every rung of
   doc 00 §3.2's ladder including GKD and MiniLLM (§9.1). Buy *managed
   infrastructure* (Baseten Training, Tinker) if it is faster; do not buy methods.
10. **Prompt optimisation: DSPy** (MIT) as a new rung *below* model-swap on the
    ladder — free of GPU cost and of teacher-ToS risk (§9.2).
11. **Expert data: Snorkel or Scale** when correctness is hard to define, rather
    than building an annotation workforce (§7).

### Avoid

12. **Do not build a gateway, a trace store, an eval harness, a judge library, a
    LoRA training API or a prompt playground.** All commoditised, several free
    (§14.7).
13. **Do not price per seat.** The market moved to volume; seats discourage the
    trace volume the loop needs (§15.5).
14. **Do not build a roadmap on an Anthropic teacher.** The AUP forbids it
    [[src](https://www.anthropic.com/legal/aup)] and AWS — the best-positioned
    party in the market — *withdrew* the capability with *"no confirmed timeline"*
    [[src](https://docs.aws.amazon.com/bedrock/latest/userguide/prequisites-model-distillation.html)].
    Design for open-weights and first-party teachers, and for the **scorer-only**
    path (TAO, §3.4) that needs no teacher completions at all.
15. **Do not depend on a vendor eval product.** OpenAI's shuts down 2026-11-30;
    Azure's stored completions retire 2026-10-15; NeMo microservices sunset
    2026-10-01 (§5.5).
16. **Do not quote list prices against a customer who caches or batches** (§17.1).
17. **Do not plan the pure self-serve SaaS configuration.** Every comparable
    standalone tooling company in this survey was acquired, sunset or pivoted
    (§17.5). Price the forward-deployed engineer, as Adaptive ML does.

### Sequence

18. Doc 00 §6 says 02 → 04 → 07 (traces, evals, A/B) is the minimum sellable
    product. **This survey strengthens that ordering and sharpens it**: since 02
    and 04 are cheap-to-buy commodities (§7, §15.4) and 07/S9 is empty whitespace
    (§14.1), the *differentiated* minimum sellable product is **ingest someone
    else's traces → validated eval + validated judge → online A/B with a defensible
    claim**, with no training at all in v1. Training is what we sell second.

---

## Open questions

⚠️ Consolidated. Each names the owner. Items 1–5 answer or supersede doc 00's
open questions 1, 3, 4, 5, 14, 15 and 16; the rest are new.

1. **⚠️ Market coverage — partially closed, not closed.** Doc 00 OQ#1 is now 41
   products deep, but **WebSearch was unavailable for this document too** (§1.1).
   Any company not linked from a page fetched here is still invisible. **One
   search-enabled pass over "LLM distillation platform", "production traffic
   fine-tuning", "model replacement A/B" would close this in an hour.** *Owner:
   whoever next has search.*
2. **⚠️ Why is OpenAI winding down fine-tuning?** (doc 00 OQ#3.) The docs state the
   wind-down but not the reason
   [[src](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)].
   Strategy (protect the family), economics (low revenue), or replacement
   (something unannounced)? The three have opposite implications for us. *Owner:
   this doc, needs search.*
3. **⚠️ Predibase — date, terms, and product fate.** (doc 00 OQ#4.) Rubrik's
   control of the domains is proven (§4.5); everything else is blocked by a 403.
   Also: **is LoRAX still maintained?** 901 commits is a lifetime count, and we are
   considering depending on it. *Owner: doc 01 (serving) + this doc.*
4. **⚠️ OpenPipe's revenue at acquisition.** (doc 00 OQ#5, now sharpened.) The
   status is resolved — migrated to W&B, legacy platform off 2026-07-30 — but the
   *business* question is not. $6.7M seed and ~$7M of claimed customer savings
   suggests small. **This is the single most decision-relevant unknown in the
   document** (§3.1). *Owner: whoever owns the business plan.*
5. **⚠️ Databricks Agent Bricks and Fireworks specifics** (doc 00 OQ#14, #15) are
   **closed**: Agent Bricks' automatic-optimisation and synthetic-data claims are
   now sourced [[src](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/custom-llm)],
   as is Fireworks' pricing and fine-tuning surface
   [[src](https://fireworks.ai/pricing)]. Doc 00 OQ#16 (NeMo Platform) is
   **closed**: it exists, it is agent-first, and it keeps the data-flywheel framing
   [[src](https://www.nvidia.com/en-us/ai-data-science/products/nemo/)].
6. **⚠️ Google Vertex AI distillation — state unknown.** The documented URL 301s
   then 404s (§5.3). No claim about Vertex may be made until this is read. *Owner:
   this doc, needs search.*
7. **⚠️ Adaptive ML / Datadog.** Reported on Adaptive's own page, unconfirmed by
   Datadog (§3.6). If true, it is the second observability-buys-training deal in a
   year and §17.3 hardens. *Owner: this doc.*
8. **⚠️ Four vendors did not respond on 2026-09-19** — Atla (404), Pi Labs
   (NXDOMAIN), Lamini (TLS failure), Zenbase (redirects to an unrelated product).
   Dead, rebranded, or transient? Three of the four were named in the brief.
   *Owner: this doc.*
9. **⚠️ Distil Labs' commercial terms and scale.** The nearest competitor publishes
   no pricing, no funding and two case studies. Are they five people or fifty?
   *Owner: this doc.*
10. **⚠️ Does anyone sell video-understanding distillation?** A full market scan
    found nothing (§13.2), consistent with doc 00 §3.4. Still the largest evidence
    gap in the programme. The cheap test: **run one internal video case on
    Marlin-2B against a frontier VLM and publish the delta**, rather than
    researching it further. *Owner: doc 04.*
11. **⚠️ Baseten's rollout primitives.** Scored S9 ◐ on inference from their
    deployment product; not verified on a fetched page. If Baseten already ships
    canary/percentage rollout, §14.1's whitespace narrows. *Owner: doc 01.*
12. **⚠️ Arize "multi-modal evaluation support" — does it cover video?**
    [[src](https://arize.com/pricing/)] If yes, it is a buy candidate for doc 04's
    hardest problem. *Owner: doc 04.*
13. **⚠️ W&B Inference per-token pricing** was not on the page fetched
    [[src](https://wandb.ai/site/inference/)]. Needed to price the closest
    competitor's serving tier. *Owner: this doc.*
14. **⚠️ Do any of these vendors publish a judge-validation methodology?** None
    surveyed does, which is the basis of §14.4's whitespace claim — but absence
    from a marketing page is weak evidence. Check Braintrust's and Galileo's docs
    (not their pricing pages) before claiming the gap in front of a customer.
    *Owner: doc 04.*
15. **⚠️ Funding and headcount across the board.** Deliberately thin (§1.2). Not
    needed for the build/buy decisions above; needed for the competitive slide in
    a fundraise. *Owner: business plan.*

---

## Sources

All fetched **2026-09-19** unless the source carries its own date. Grouped by
role; the per-product check dates are in §11.

**Near-competitors (full or near-full loop)**
- OpenPipe archive and migration notice — https://openpipe.ai/
- CoreWeave acquires OpenPipe (2025-09-03) — https://www.coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning
- W&B Serverless Training / Serverless RL — https://docs.wandb.ai/guides/training/
- OpenPipe ART — https://github.com/OpenPipe/ART (README via https://raw.githubusercontent.com/OpenPipe/ART/main/README.md)
- Distil Labs — https://distillabs.ai/
- LangChain × Baseten Loops (2026-09-15) — https://www.baseten.co/blog/langchain-trains-custom-models-langsmith-engine-baseten-loops/
- Databricks Agent Bricks Custom LLM (2026-09-15) — https://docs.databricks.com/aws/en/generative-ai/agent-bricks/custom-llm
- Databricks Agent Bricks index (2026-09-15) — https://docs.databricks.com/aws/en/generative-ai/agent-bricks/
- Databricks TAO (2025-03-25) — https://www.databricks.com/blog/tao-using-test-time-compute-train-efficient-llms-without-labeled-data
- Databricks AI platform — https://www.databricks.com/product/artificial-intelligence
- NVIDIA Data Flywheel Blueprint (deprecated 2026-04) — https://github.com/NVIDIA-AI-Blueprints/data-flywheel
- NVIDIA NeMo microservices (sunset 2026-10-01) — https://docs.nvidia.com/nemo/microservices/latest/index.html
- NVIDIA NeMo Platform — https://www.nvidia.com/en-us/ai-data-science/products/nemo/
- Adaptive ML — https://www.adaptive-ml.com/
- Amazon Bedrock Model Distillation — https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html
- Bedrock distillation prerequisites / teacher-student matrix — https://docs.aws.amazon.com/bedrock/latest/userguide/prequisites-model-distillation.html
- Azure stored completions + distillation (retires 2026-10-15; doc 2026-07-06) — https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/concept-model-distillation

**Training and serving**
- Baseten Training (Jobs / Loops) — https://www.baseten.co/products/training/
- Baseten training docs — https://docs.baseten.co/training/overview
- Baseten pricing — https://www.baseten.co/pricing/
- Baseten Chains — https://www.baseten.co/products/chains/
- Baseten customers — https://www.baseten.co/customers/
- Baseten, best open-source models for post-training (2026-09-15) — https://www.baseten.co/blog/best-open-source-models-for-post-training/
- Tinker — https://thinkingmachines.ai/tinker/
- Tinker models and pricing — https://tinker-docs.thinkingmachines.ai/tinker/models/
- Tinker cookbook — https://github.com/thinking-machines-lab/tinker-cookbook
- Thinking Machines, on-policy distillation (2025-10-27) — https://thinkingmachines.ai/blog/on-policy-distillation/
- Fireworks pricing — https://fireworks.ai/pricing
- Fireworks fine-tuning — https://docs.fireworks.ai/fine-tuning/fine-tuning-models
- Together pricing — https://www.together.ai/pricing
- Together fine-tuning — https://docs.together.ai/docs/fine-tuning-overview
- Modal pricing — https://modal.com/pricing
- Anyscale — https://www.anyscale.com/
- Hugging Face pricing — https://huggingface.co/pricing
- Hugging Face AutoTrain — https://huggingface.co/autotrain
- Arcee AI — https://www.arcee.ai/
- Mistral fine-tuning (deprecated) — https://docs.mistral.ai/capabilities/finetuning/
- Predibase → Rubrik (301, verified by curl) — https://predibase.com/ , https://docs.predibase.com/
- LoRAX — https://github.com/predibase/lorax

**Frontier-lab first-party**
- OpenAI supervised fine-tuning (winding down) — https://developers.openai.com/api/docs/guides/supervised-fine-tuning
- OpenAI reinforcement fine-tuning — https://developers.openai.com/api/docs/guides/reinforcement-fine-tuning
- OpenAI model distillation — https://developers.openai.com/api/docs/guides/distillation
- OpenAI Evals (read-only 2026-10-31; shutdown 2026-11-30) — https://developers.openai.com/api/docs/guides/evals
- Anthropic Usage Policy (effective 2025-09-15) — https://www.anthropic.com/legal/aup
- Google Vertex AI distillation — https://cloud.google.com/vertex-ai/generative-ai/docs/models/distill-text-models (301 → 404 on 2026-09-19)

**Observability and evaluation**
- Langfuse pricing — https://langfuse.com/pricing
- Braintrust pricing — https://www.braintrust.dev/pricing
- LangSmith — https://www.langchain.com/langsmith
- LangSmith pricing — https://www.langchain.com/pricing-langsmith
- Arize pricing — https://arize.com/pricing/
- W&B Weave — https://wandb.ai/site/weave/
- W&B pricing — https://wandb.ai/site/pricing/
- W&B Inference — https://wandb.ai/site/inference/
- Galileo (2026-09-11) — https://galileo.ai/
- Confident AI / DeepEval pricing — https://www.confident-ai.com/pricing
- Patronus AI — https://www.patronus.ai/
- Humanloop (acquired by Anthropic; platform sunset) — https://humanloop.com/
- Atla — https://www.atla-ai.com/ (404 on 2026-09-19)
- Pi Labs — https://withpi.ai/ (NXDOMAIN on 2026-09-19)
- Lamini — https://lamini.ai/ (TLS failure on 2026-09-19)

**Data and annotation**
- Scale GenAI Platform — https://scale.com/genai-platform
- Scale Data Engine — https://scale.com/data-engine
- Snorkel — https://snorkel.ai/ and https://snorkel.ai/platform/
- Refuel (joining Together AI) — https://www.refuel.ai/
- Kiln — https://kiln.tech/ (from https://getkiln.ai/)

**Routing, gateways, open-source building blocks**
- Not Diamond — https://www.notdiamond.ai/
- Martian — https://withmartian.com/
- Portkey pricing — https://portkey.ai/pricing
- LiteLLM — https://www.litellm.ai/
- HuggingFace TRL — https://huggingface.co/docs/trl/index
- DSPy — https://github.com/stanfordnlp/dspy
- Zenbase — https://zenbase.ai/ (redirects to https://www.thesynthesis.company/ on 2026-09-19)

**Repo cross-references (not external sources)**
- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — legend, cost formulas, price tiers
- [`research/platform/00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) — the frame, stages S1–S9, invariants I1–I7
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md), [`research/matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md), [`research/matrix/fit-matrix.md`](../matrix/fit-matrix.md)
- [`research/scaling/`](../scaling/) — hosting, autoscaling, concurrency, cold start, cost engineering
- [`research/models/`](../models/) — per-model architecture, including [`marlin2b`](../models/marlin2b/README.md) for video

**Papers referenced via doc 00 (not re-fetched here)**
- GKD / on-policy distillation — https://arxiv.org/abs/2306.13649
- MiniLLM (reverse-KL distillation) — https://arxiv.org/abs/2306.08543
- S-LoRA (multi-adapter serving) — https://arxiv.org/abs/2311.03285

---

## Verification log (2026-09-19)

Adversarial re-check of this document. Method: for each claim, open the **primary
source** (never the doc's own citation text), re-read the sentence the doc quotes,
and recompute every derivation with `python3`. `research/` cross-references were
checked by opening the file. **WebSearch was again unavailable** (session budget
200/200 exhausted), so this pass hit the same wall §1.1 describes: it could
verify what the document cites, and could not discover what it missed.

**25 claims checked · 10 confirmed · 13 corrected · 2 unverifiable.**

### Corrected

1. **§15.3 — three blended-cost cells were arithmetically wrong.** METHODOLOGY §6's
   blend `0.375×in + 0.375×cached + 0.25×out` gives **$0.870** for Nemotron 3 Ultra
   (doc: $0.848), **$0.435** for Inkling-Small 256K (doc: $0.437) and **$1.451**
   for Inkling 256K (doc: $1.427). The other five sourced rows recompute exactly,
   and every *input* price in the table was re-verified against Baseten's and
   Tinker's pages and is correct — the errors are the doc's own arithmetic.
2. **§15.2, §10 — the Tinker price basis is inconsistent.** The doc labels only
   the Inkling rows "50 % promo". Tinker's table shows **Limited-time 50%
   discount** on **every Nemotron row too**: Nemotron-3.5-Lightning-30B lists at
   **$0.88** (doc printed the discounted $0.44 as if list), Nemotron-3-Super-120B
   at **$2.552** (doc: $1.276), Nemotron-3-Ultra-550B at **$10.956** (doc:
   $5.478), Inkling 64K at **$11.22** and Inkling 256K at **$22.46**
   [[src](https://tinker-docs.thinkingmachines.ai/tinker/models/)]. Consequences:
   the "**33× span**" mixes bases (list-consistent: **51×**, or **25.5×**); the
   "$596 training job" is **$1,191 at list**; the "~9× variation on student
   choice" is **4.7×** if the promotion ends; and **Inkling 256K at list
   ($22.46) is the most expensive student on the catalogue, above GLM-5.3's
   $14.58** — inverting §10's "most expensive except GLM-5.3".
   **$0.44 is also not uniquely cheapest**: `Qwen3-8B` (32K) trains at $0.44 with
   no discount.
3. **§15.2 — derivation drift.** 100k × 4,512 × 3 = 1,353.6M tokens → $4.103/1M =
   **$5,553.79 ≈ $5,554**, not $5,555; at $3.00/1M = **$4,060.80 ≈ $4,061**, not
   $4,062. The doc claimed $5,555 "confirm[ed] doc 00's figure" while doc 00
   §4.3(b) says ≈$5,554 — the corrected value actually agrees.
4. **§15.1 — Together's B200 on-demand price is $8.19/h, not $8.99.** The B200
   spread is therefore **1.59×**, not 1.45× [[src](https://www.together.ai/pricing)].
   Also newly recorded: Together's H100 $3.99 is **promotional, $5.49 list,
   expiring 09/30/26** — eleven days out, which would narrow the H100 spread the
   §15.1 decision rule rests on from 2.03× to **1.47×**.
5. **§3.4, §12, §14.1 — Databricks does not require 100 inputs.** Verbatim:
   *"Databricks recommends at least 100 inputs (either 100 rows in your Unity
   Catalog table or 100 manually-provided samples) to optimize your agent"*
   [[src](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/custom-llm)].
   The doc's "requires a minimum of 100 inputs" / "gates on 100 inputs" made the
   competitive argument depend on a gate that does not exist. Recut in all three
   places — the honest finding is stronger: **no gate is described at all.**
6. **§4.1 — a Baseten quotation is fabricated and a model recommendation is
   misread.** *"active parameters drive compute cost"* appears nowhere on the
   cited page; the page says *"Every token the model generates requires math
   proportional to its active parameters."* Separately, the page does not name
   GLM-5.2 as the RL-heavy *student*; it praises **slime, GLM 5.2's RL training
   framework**, and ties cheap RL to DeepSeek-V4-Flash's 13B active params
   [[src](https://www.baseten.co/blog/best-open-source-models-for-post-training/)].
7. **§6.3, §13.2 — Arize's multi-modal evals exclude video.** The page enumerates
   *"Multi-modal evaluation (image, voice, pdf)"* [[src](https://arize.com/pricing/)].
   The doc's ⚠️ ("what it covers for video was not established") is now resolved
   **negatively**, which strengthens §13.2's whitespace claim on evidence rather
   than on ignorance.
8. **§7.4 — Kiln is not MIT-licensed.** GitHub's licence API returns
   `NOASSERTION`; `LICENSE.txt` opens *"Kiln AI - License Information … This
   repository contains components under different licenses"* (© Chesterfield
   Laboratories Inc.). §14.7's "Kiln is MIT" shorthand should not be relied on for
   a vendoring decision. Stars: **5,076** (API).
9. **§3.5 — NVIDIA's second result names `Qwen-2.5-32b-coder`, not Qwen 2.5 32B**,
   and the *">50%"* covers **cost *and* time-to-first-token**
   [[src](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)]. The headline
   result is confirmed verbatim: *"a fine-tuned `llama-3.2-1b-instruct` was able
   to achieve ~98% accuracy relative to the 70b model"* — so the doc's "≈98 % of
   Llama 3.1 70B's accuracy" reading was right.
10. **§3.1, §11 — the OpenPipe shutdown date has passed.** The migration notice is
    dated **2026-05-18**; **July 30, 2026 is seven weeks before this research
    date.** The doc's tables present it as pending. Separately, the archive's
    headline claims are dated: the $7M-saved / $6.7M-seed / *"10-100x"* claims
    all come from one post of **2024-03-25** and the DPO-first claim from
    **2024-10-01** (verified in the site's own JS bundle). The "32x" is also
    quoted out of context — full sentence: *"fine-tuning a 7B model on OpenPipe
    leads to an average 14x savings compared to GPT-4-Turbo (that's a whopping
    32x compared to GPT-4-0613)."*
11. **§4.5, §11 — LoRAX's maintenance status is now measured.** GitHub API:
    **3,832 stars, Apache-2.0, 901 commits, last push 2026-05-28**, not archived.
    The doc's ⚠️ ("commit recency not established") is closed: **~3.7 months with
    no commit.** The "orphaned by acquisition" reading holds.
12. **§17.1 — output share of the blend contradicted doc 00 §4.4.** Doc 07 said
    output is "25 % of the blend"; doc 00 §4.4 says "output is 75 % of the
    blend". 25 % is the **token** share, ~75 % the **cost** share at the Opus-5
    100 %-cached floor. Restated with both denominators named.
13. **§1.1 — the source-count claim contradicts §11.** §1.1 says "~55 distinct
    URLs fetched"; §11 lists **74** URLs all marked checked on 2026-09-19. §1.2's
    "41 products" is likewise not reconstructible from §11. Flagged ⚠️ in place;
    §11 is the auditable artifact.

### Confirmed against the primary source

- **CoreWeave → OpenPipe**, announced **2025-09-03**, *"Terms of the acquisition
  are not being disclosed"*, *"follows the recent acquisition of Weights &
  Biases"* [[src](https://www.coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning)].
  The doc's §3.1(b) "⚠️ revenue at acquisition unknown" stands.
- **Thinking Machines on-policy distillation — every number exact.** AIME'24
  60 / 67.6 / **74.4**; GPQA-Diamond 55.6 / 61.3 / **63.3**; GPU-hours **17,920 vs
  1,800** (9.96×); the Qwen3-8B personalisation case 18→43 / 85→45, 36/79, and
  **41/83** after recovery [[src](https://thinkingmachines.ai/blog/on-policy-distillation/)].
  One nuance the doc gets right and is worth keeping: the AIME rows are *from the
  Qwen3 Technical Report (Table 21)*, not a Thinking Machines measurement. The
  FLOPs figure is a ladder, not a range: **9× baseline, 18× with GPU
  parallelisation, 30× including teacher sampling** — the doc's *"9-30x"* is fair.
- **Databricks TAO**, dated **2025-03-25**: FinanceBench **7,200**, DB Enterprise
  Arena **4,800**, BIRD-SQL **8,137** labelled examples beaten; multitask
  **+2.4 points**; test-time compute *"as part of the process to train a model …
  not requiring additional compute at inference time"*
  [[src](https://www.databricks.com/blog/tao-using-test-time-compute-train-efficient-llms-without-labeled-data)].
- **AWS Bedrock** — the Anthropic withdrawal is verbatim (*"Distillation is not
  currently available for Anthropic models on Amazon Bedrock. There is no
  confirmed timeline for when Anthropic distillation will be restored."*) and the
  **entire teacher→student→region matrix reproduces exactly**, Nova in us-east-1
  and all three Llama teachers in us-west-2
  [[src](https://docs.aws.amazon.com/bedrock/latest/userguide/prequisites-model-distillation.html)].
  This is the load-bearing evidence for §16's "never build a roadmap on a Claude
  teacher" and it survives adversarial reading.
- **Azure** — retirement **2026-10-15**, minimum **10** stored completions
  ("recommended to provide hundreds to thousands"), **10 GB** cap, and
  *"cannot be accessed directly and cannot be exported externally/downloaded"* for
  **both** training **and** evaluation files; doc dated 2026-07-06, updated
  2026-07-07. The cited URL redirects to the "classic" portal path; all facts
  re-confirmed there.
- **OpenAI Evals** — *"read-only for existing users on October 31, 2026 … shut
  down on November 30, 2026"* [[src](https://developers.openai.com/api/docs/guides/evals)].
- **Anthropic AUP** — effective **2025-09-15**, clause verbatim
  [[src](https://www.anthropic.com/legal/aup)].
- **NVIDIA** — Data Flywheel **deprecated April 2026**, Apache-2.0, *"up to
  98.6%"*; NeMo microservices **sunset 2026-10-01**, seven services named, SDK
  **v2.0.1** / image **26.03.1**.
- **HF TRL** — the distillation family reproduces exactly: **`DistillationTrainer`
  stable**, plus eight experimental (`AsyncDistillationTrainer`, `GKDTrainer`,
  `GOLDTrainer`, `IWOPDTrainer`, `MiniLLMTrainer`, `SDFTTrainer`, `SDPOTrainer`,
  `SSDTrainer`) = **nine**, matching §14.7. The million-token Qwen3-8B
  single-node long-context guide is on the page [[src](https://huggingface.co/docs/trl/index)].
- **Vendor pricing, re-read cell by cell and all correct:** Langfuse ($0/50k,
  $29/$199/$2,499 at 100k, $8→$7→$6.50→$6 per 100k, $300 Teams add-on, unit
  definition verbatim); Braintrust ($0/$249, 1/5 GB at $4/$3, 10k/50k scores at
  $2.50/$1.50 per 1k, 14/30-day, human review 1-per-project vs unlimited); Arize
  ($0/$50, 1/10 GB, 15/30-day, no per-seat); W&B ($60/mo, 1.5 GB, **$0.10/MB**,
  $0.03/GB storage); Baseten (all six GPU rows and all six Model API rows);
  Modal (B300 $0.001972/s → **$7.099/h**, H100 $0.001097/s → **$3.949/h**, Team
  $250/mo); Fireworks (LoRA SFT $0.50/$3/$6/$10, full-param **2×**, DPO **2×
  SFT**, **RFT per GPU-hour**, **1.5×** region premium, H100/H200 $8, B200 $13,
  B300/GB300 $15–20).
- **Vendor claims, verbatim:** LiteLLM (140+ providers, **1,892** models,
  **0.66 ms p99**, **3.5×**, **2,800+ rps**, **240M+** Docker pulls, AT&T
  *"as much as 56%"*); Distil Labs (**1 %** of traffic, *"roughly 10 minutes of
  your effort and a day to execute"*, **80 %** lower cost, Knowunity **68 %** and
  **81 % → 93 %**, Rocketgraph on-prem, no pricing); Adaptive ML (AT&T **51 %**
  win rate / **50+** use cases, SKT, Aïkan **−25 %** / **−42 %**, A/B pillar,
  FDEs as a line item); Galileo (**96 %** lower cost, **20+** evals, SaaS/VPC/
  on-prem, page dated 2026-09-11); Not Diamond (5 % / 20 % / 2×, **$4.8M → $3.6M
  = −25 %** at 1,000 engineers × $300/mo — the doc's arithmetic checks);
  OpenPipe ART (**40 %** lower cost, **28 %** faster, **2000+** concurrent
  requests, *"Qwen 2.5 14B email agent outperforming OpenAI's o3"*); W&B
  Serverless Training (all four quotes, including *"does not charge for adapter
  training during the preview period"* and provisioning *"on CoreWeave"*).
- **Negative findings re-verified by `curl` on 2026-09-19:** `predibase.com`
  **301 → rubrik.com/products/rubrik-agent-cloud** (`server: AkamaiGHost`),
  `docs.predibase.com` **301 → rubrik.com**; `www.atla-ai.com` **404**;
  `zenbase.ai` **307 → thesynthesis.company**; `withpi.ai` and `lamini.ai` return
  nothing. §8.6's negative findings hold exactly as written.
- **Repo cross-references, opened and checked:** doc 00 §6's doc-map does assign
  slot 07 to A/B testing and route market-scan questions to "doc 09" (§1's
  numbering note is accurate, and the conflict is real — doc 00 §6 separately
  maps doc 09 to the auto-research loop). Doc 00's open questions **1, 3, 4, 5,
  14, 15, 16** are the ones this document claims to own; they are. Doc 00 §5.1's
  *"≥200 human-adjudicated examples … Cohen's κ"* matches §14.4. Doc 00
  §4.3(b)'s Tinker inputs ($1.86 prefill, $0.372 cached, $5.595 sample, $4.103
  train, $0.10/GB-month storage) all reproduce on Tinker's page.
- **Derivations recomputed and correct:** §15.4's *"$0.10/MB = $100/GB"* (`est.`,
  decimal GB; the page's own MB/GB convention is unstated, so a MiB reading gives
  $102.40 — the ~100× spread claim is unaffected); §17.1's **6–28×**
  ($0.38/$1.66 ÷ $0.0602 = 6.31 / 27.57); Baseten's per-minute→per-hour
  conversions (T4 $0.631, L4 $0.848, A10G $1.207).

### Unverifiable in this session

- **Vertex AI distillation (§5.3).** Independently re-verified that
  `cloud.google.com/vertex-ai/generative-ai/docs/models/distill-text-models`
  **301s to `docs.cloud.google.com/...` which returns 404** — so the doc's report
  of the failure is accurate — but the *current state* of the product still
  cannot be determined without search. This remains a genuine hole.
- **Which of §3.1's readings (a) or (b) dominates for OpenPipe.** Terms were not
  disclosed (re-confirmed on CoreWeave's page), so revenue at acquisition is
  unknowable from public sources. The doc correctly calls this the single most
  decision-relevant unknown, and it stays open.
