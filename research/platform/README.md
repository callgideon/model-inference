# research/platform/ — index and reading guide

> **Current product scope (2026-09-21):** [Two-platform architecture](../platforms/README.md) supersedes conflicting product and launch assumptions below. apps/app is the free consumer product with 10,000 credits once per individual; apps/lab is the provider product. Historical research and measurements remain context, not current implementation instructions.


## 1. What this is

Fact-checked research on building a **closed-loop model-replacement platform**:
capture production traffic from a frontier model, annotate it with a much
larger teacher plus RLHF signals, distil/train a smaller specialist, gate it
twice, A/B it online with a defensible statistical claim, and feed
post-deployment traffic into the next round — for text and video. This is the
product research; `research/scaling/` and `research/models/` are the
inference/hosting and per-model/per-GPU research it cites, never re-derives.
Every doc carries its own fact-check verification log.

## 2. Research date and legend

Research date **2026-09-19**, same as the rest of the tree. Every formula,
price tier and marker below is [`../METHODOLOGY.md`](../METHODOLOGY.md) —
not re-derived here.

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source, fetched 2026-09-19. |
| **⚠️ TO BE VERIFIED** | No primary source, or an inference; reasoning stated inline. |
| `est.` | Arithmetic from METHODOLOGY formulas or sourced inputs, not measured. |
| `meas.` | A published or repo measurement, cited. |

## 3. The goal, in five lines

1. A team runs a product feature on a frontier API — GPT-5.6, Opus-5, Fable-5.1
   — and pays frontier prices for a task that is, in practice, narrow.
2. The loop closes it: capture every request/response with full context,
   annotate with a larger teacher plus RLHF signals, train/distil a specialist,
   gate the artifact that actually serves (twice), A/B it online, and feed
   post-deployment traffic into the next round.
3. It serves `main`/`dev` endpoints with instant promote/rollback, and
   versions four things together — base weights, adapter, prompt stack,
   serving config — because a checkpoint alone is not a deployable artifact.
4. The product is not cheaper tokens; it is a defensible, evidence-backed
   permission to switch without risking quality, priced by a rigorous
   A/B + evals confidence protocol.
5. Success is not "it's cheaper" — it's a second loop iteration, trained from
   post-deployment traffic, beating the first on the frozen test set.

## 4. Reading order

1. **[`00`](00-goal-and-problem-statement.md)** first — it frames everything
   else; its §6 table maps each of docs 01–09 to the loop stages S1–S9 it owns.
2. **[`11`](11-thesis-memo.md)** next if you want the funding-memo version —
   every number in it links back to 00–09.
3. Then **01 → 04** (traces, then evals + A/B): the minimum sellable product,
   because it delivers most of doc 00's success criteria with no training.
4. Then **02 → 03 → 05** (annotation, training, optimisation): what makes it
   an actual model swap, not just observability.
5. Then **06 → 07 → 08 → 09** (architecture, competitors, economics, video):
   how it's built, who else sells it, what it costs, and the video-specific
   deltas.
6. **[`10`](10-roadmap-and-mvp.md)** last — the plan, sourced entirely from
   00–09 and `../scaling/`, nothing re-derived.

## 5. The documents

**[00 — Goal and problem statement](00-goal-and-problem-statement.md).** The
nine loop stages (S1–S9) and invariants (I1–I7) every other doc builds on,
why distillation works on narrow tasks (UniversalNER, NVIDIA's tool-calling
flywheel), why the GPU floor breaks the per-token economics above ~10M
req/month, and why confidence, not cost, is the crux. *Worked example:* §4's
incumbent vs. self-hosted cost breakdown across GPT-5.6/Opus-5/Fable-5.1.

**[01 — Observability and tracing](01-observability-and-tracing.md).** The
schema rule — a field earns its place only if a downstream stage consumes it,
and a training trace cannot be lossy — plus OTel GenAI conventions,
storage/retention/PII design, and the six gaps no OSS stack covers. *Worked
example:* §7, 50M requests/month on a GPT-5.6-class endpoint with video —
storage, compression, ingest cost.

**[02 — Annotation and teacher labeling](02-annotation-and-teacher-labeling.md).**
Eight label types (T1–T8) ranked by cost/value, the on-policy vs. off-policy
split and its logprob-availability constraint, per-vendor teacher ToS
restrictions, and human review as 96–98% of the annotation budget, unsourced
per example. *Worked example:* §6's cost model — Gemini 3.8 Flash cheapest
frontier teacher at $492/100k examples, self-hosted DeepSeek-V4.1-Flash $90.

**[03 — Training: SFT, RLHF, distillation](03-training-sft-rlhf-distillation.md).**
The method ladder from prompt-swap through on-policy distillation to
RLHF/PPO, climbed only as far as the gate demands; Tinker as the recommended
buy for round 1; LoRA vs. full fine-tuning. *Worked example:* §8.1,
distilling a GPT-5.6-class support agent into Qwen3.8-27B — round 1 (2,000
examples) ~$24, round 2 (60,000 examples) ~$716.

**[04 — Evals and A/B testing](04-evals-and-ab-testing.md).** Eval tiers
organised by what decision they authorise (T0 contract check through T5
monitor); the statistics of parity — judge error inflates sample sizes
2.78×, clustering triples naive SEs, peeking turns a 5% test into a 32% one.
*Worked example:* §8.1, a 2M-req/month support chat — the offline gate costs
a few hundred dollars; the full evidence package is ≈$1,500 list/$1,020 batch.

**[05 — Model and inference optimization](05-model-and-inference-optimization.md).**
Gate-twice is not enough because S8 is five or six independent
transformations (quantise weights, quantise KV, swap attention kernel, attach
a draft head, merge a LoRA, change engine flags), each needing a cheap
screen; auto-research is safe only for judge-free objectives. *Worked
example:* §7, distilled Qwen3.8-27B on 1×B300 — the full config sweep costs
$118–$296.

**[06 — Platform architecture](06-platform-architecture.md).** Two systems
(low-latency serving, batch loop) coupled only through a trace stream and an
artifact registry; four things (weights, adapter, prompt stack, serving
config) version together; Temporal owns the month-long loop, Ray-on-Kueue
owns hour-long jobs; cost of goods is idle GPU and human adjudication, not
storage. *Worked example:* §8, trace storage for 100 tasks is $72/month vs.
$540k/month for one unpacked replica per task.

**[07 — Competitor analysis](07-competitor-analysis.md).** Scores 41+
products against the nine loop stages; **no product scores ● on S2 (traces)
and S5 (train) and S9 (A/B) simultaneously**, S9 is whitespace almost
everywhere. Documents seven vendor retirements from the loop as a live
counter-risk. No formal worked example — its deliverable is the coverage
matrix itself (§12).

**[08 — Economics and business case](08-economics-and-business-case.md).**
Five results: the honest multiple is single- to low-double-digit against a
batched/cached incumbent, not 138×; the GPU floor dominates above ~10M
req/month; the confidence protocol is worth an order of magnitude more than
the token saving; video inverts the usual cost shape; observability pricing
can eat the whole saving. *Worked example:* §7, three customer profiles
(support chat, extraction, video) with payback periods.

**[09 — Video and multimodal loop](09-video-and-multimodal-loop.md).** Five
findings: "GPT-5.6/Fable-5.1 video" don't exist (client-side frame
extraction only); TimeLens-8B beats GPT-5 and Gemini-2.5-Flash on temporal
grounding; temporal grounding needs no judge (verifiable reward); the
240-frame cap is both a serving fact and an eval-design constraint; Gemini's
agentic mode is a severe counter-move. *Worked example:* §8, 50,000
videos/month, Gemini-class captioning → Marlin-2B.

**[10 — Roadmap and MVP](10-roadmap-and-mvp.md).** MVP is one customer, one
text task, L0 automation plus one manual round; ten exit criteria, of which
criterion 10 (a second iteration beating the first) proves it's a platform
and not a consultancy; six phases (M0–M6), 80–104 engineer-weeks; top ten
risks ranked by how much each can end the product. *Worked example:* §4, two
lighthouse runs (text support agent, video captioning) end to end.

**[11 — Thesis memo](11-thesis-memo.md).** The funding-memo compression of
00–09: the problem, why now, whether distillation works (yes on narrow
tasks, unproven on agents/video), the crux (confidence, priced at 18–46× the
saving), the competitive landscape, three customer-profile economics, the
MVP, and the eight biggest unknowns.

## 6. How this connects to research/scaling and research/models

`research/scaling/` is the inference/hosting leg this platform runs on:
bare-metal cluster, serving stack, autoscaling, cold start, cost engineering
and reference architectures for the five models in `research/models/`. The
platform docs cite it rather than re-deriving it — doc 06's MVP reference
stack and doc 10's deployment (§2.2) both adopt
[`../scaling/10-blueprint.md`](../scaling/10-blueprint.md)'s four-node floor
unchanged, and every `$/1M`, utilisation and GPU-floor figure in 00/05/06/08/
09/10 is a named row from [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md),
[`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) or a
`research/models/<exp>/` doc. `research/METHODOLOGY.md` is the single formula
source for all three trees: `research/models`/`matrix` answer *does it fit and
what does it cost*, `research/scaling` answers *how do you run it in
production*, and `research/platform` answers *how do you replace a frontier
model with one of these, defensibly, and keep replacing it*.

## 7. Top 15 open questions, deduplicated

1. **Market coverage is search-limited nearly everywhere** — docs 00–04, 06,
   07, 09 were produced with no working web search; every competitor/tooling
   survey is a floor, not a scan (00, 01, 02, 03, 04, 06, 07, 09).
2. **Cost per SME-adjudicated example is unsourced** — 77–98% of the
   annotation budget, the biggest unpriced number in the economics (00, 02,
   06, 08, 10 §8).
3. **Teacher ToS can end the product**: Anthropic/Google restrict
   distillation without authorisation; AWS withdrew Claude distillation with
   no restoration timeline (00, 02, 09, 11). **OpenAI's clause: RESOLVED for
   the Business Terms, narrower than hoped** — the live pages still 403, but
   a 2026-09-12 Wayback Machine snapshot of the Services Agreement yields
   §3.3(e)'s restriction and the Permitted Exception verbatim
   ([`02` §5.1](02-annotation-and-teacher-labeling.md), addendum in
   [`00`](00-goal-and-problem-statement.md)). OpenAI's Usage Policies,
   Service Terms and regional Terms of Use remain unread — an archive
   capture is not the executed contract, so the customer-facing gate stays
   until counsel confirms it against the real document.
4. **No published video-distillation-at-parity result exists**, and a
   frame-sampled proxy eval's validity vs. full-clip human grading is
   unmeasured (00, 04, 09, 10 §8).
5. **Do batch discounts and caching compose on OpenAI/Anthropic?** Google
   confirmed yes; if not, every incumbent floor in doc 08 rises 20–40%.
6. **Has any second loop iteration ever beaten the first?** No published
   precedent, and it's the MVP's decisive exit criterion (08, 10, 11).
7. **No judge–human agreement measurement exists for any 2026-generation
   judge** — every number in the tree is 2023–2025 (02, 10 §8).
8. **Whether GPU run-to-run nondeterminism inflates eval noise** is
   unmeasured — if it does, every sample-size table is optimistic (06).
9. **Predibase's acquisition by Rubrik** — date/terms unconfirmed (403);
   LoRAX's fate resolved (Apache-2.0), Turbo LoRA's is not (00, 07, 08).
10. **Teacher logprobs for on-policy distillation**: negative for
    OpenAI/Anthropic, positive for vLLM; Gemini unconfirmed (00, 02, 03, 09).
11. **What a bad outcome is worth per segment is assumed, not sourced** — the
    risk-pricing argument (18–46× the saving) rests on it (08).
12. **Does quantization regress refusal/safety behaviour?** No published
    measurement, and safety is a non-negotiable gate (05).
13. **Marlin-2B has no measured throughput on any hardware**; video-decode
    cost may be the real bottleneck behind every video figure (08, 09).
14. **Requests/second break-even between dedicated, shared pool and
    serverless** not yet computed per model (00, 05).
15. **Why are four-plus vendors retreating from the loop?** If customers
    won't pay for it, the pricing model is wrong (08, 11).
