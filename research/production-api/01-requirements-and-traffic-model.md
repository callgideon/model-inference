# Requirements, SLOs and traffic model for the public API

> **Implementation amendment — 2026-09-20.** The current implementation authority is [the unified plan](../plan/README.md), especially [contracts](../plan/01-contracts.md) and [durable protocols](../plan/02-durable-protocols.md). The text below is historical research where it conflicts with those documents. Pilot is free with promotional holds/settlement; ordinary chat never automatically returns 202; PG owns jobs, admission, leases, output journal and terminal accounting. Memory/Valkey queues are rebuildable indices. Admission stages immutable input and commits job/hold/outbox before acknowledgment. Output commits before relay, terminal success after settlement; no retry after publication. Existing A0 fixes are preserved, not repeated. Per-request context is not aggregate concurrency; pixel area 200704 is an area limit, not a 448px long edge. Old Lua/layout/schema snippets require contract tests and must not be copied verbatim. New launch, ownership and test gates are in the plan package.


Research date **2026-09-20**. This is document 01 of `research/production-api/`, the
tree that turns the single-box Marlin-2B endpoint into a service real developers
can depend on. It fixes **what we promise** and **how much hardware that costs**;
the documents that follow specify how to build it.

**Legend** ([`../METHODOLOGY.md`](../METHODOLOGY.md#legend)): `[src]` + URL =
primary source; `meas.` = measured in this repo, with the run cited; `est.` =
derived by stated arithmetic from measured or sourced inputs; **⚠️ TO BE
VERIFIED** = best estimate, method stated, no primary source.

**What this document assumes as already established, and links rather than
repeats:**

| Topic | Where |
|---|---|
| Engine queues, admission ladder, status codes, preemption, video concurrency | [`../scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md) — §3.5 status codes, §3.10 ladder, §6.4 video |
| Autoscaling signals, lead time, scale-down thrash, Little's Law capacity planning | [`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md) — §2 signals, §4.4 lead-time math, §6.1–6.4 |
| Cold-start anatomy and pre-warming | [`../scaling/06-cold-start.md`](../scaling/06-cold-start.md) |
| SLI menu, failure catalogue, runbooks, burn-rate alerting | [`../scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md) — §1.1–1.4, §2, §8 |
| Prefix/multimodal caching, encoder placement, video token budgets | [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) — §1, §6.1–6.3, §7.4 |
| Bare-metal target architecture (the destination, not today) | [`../scaling/10-blueprint.md`](../scaling/10-blueprint.md) |
| GPU-hour prices | [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §5.14 — **but note §3.9 below: g6e has no row there, so this document sources it from the AWS Price List directly** |

**What is different here from `research/scaling/`.** That tree plans an 8×B300
bare-metal cluster and its Marlin-2B numbers are `est.` throughout. This tree
plans **one L40S on AWS today, growing to a handful**, and its Marlin numbers are
**measured** ([`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md),
2026-09-19). Where the two disagree, the measurement wins and this document says so.

---

## 0. The summary, in ten sentences

1. Two operations — **caption** and **find** — one video per request, and the
   request is dominated end-to-end by **fetching and decoding the video**, not by
   the language model ([`notes.md` finding 7](../../models/marlin2b/results/notes.md)).
2. Measured on one L40S at engine-concurrency 8: **1.57 clips/s** for 1080p
   sources and **3.58 clips/s** for 360p sources, same token budget — a **2.3×
   throughput swing that depends entirely on the customer's source resolution**,
   which is the single most important fact in this document.
3. Therefore the first SLO must be expressed **per clip-second**, not per
   request, and the first capacity lever is **pre-transcoding inputs to ≤480p**,
   not more GPUs.
4. "No dropped requests" means: every accepted request is **durably admitted**
   before the caller is told "accepted", and then either completes or is
   rejected **explicitly with a `Retry-After`** — never a silent socket timeout.
5. That forces a **bounded queue with an honest wait estimate** in front of the
   engine, and a **202 + job-polling surface** alongside the held-connection
   surface, because a held HTTP connection cannot survive a 7-minute scale-up
   (§3.7) and AWS load balancers default to a **60 s idle timeout**
   [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html).
6. The gateway's current behaviour — **429 above `MAX_INFLIGHT=16` with no
   queue** ([`gateway.py`](../../apps/infrx-api/gateway.py)) — is exactly the
   behaviour this program exists to replace.
7. Capacity: **1 GPU** covers the pilot scenario, **6–10 GPUs** the growth
   scenario at peak, **29–46 GPUs** a 50 req/s burst — and since scale-up takes
   **~5–7 minutes** (§3.7), a 5-minute burst **cannot** be met by scaling; it must
   be met by queue plus headroom, or refused honestly.
8. CPU binds before GPU on `g6e.2xlarge` (8 vCPU per L40S); **`g6e.4xlarge`
   doubles the vCPUs for +34 % on price** ($2.24208 → $3.00424/h, us-east-1
   Linux on-demand) and is the cheapest experiment in this whole program.
9. Security is not a later concern: the gateway today fetches **any URL a caller
   supplies, follows redirects, and buffers the whole body before checking the
   size limit** — a textbook SSRF and a memory-exhaustion path (§5.1–5.2).
10. The cost sketch in [`notes.md`](../../models/marlin2b/results/notes.md)
    (**"$0.02–0.04 per video-hour"**) is **3.6× too cheap** — a seconds/hours
    units slip, corrected in §3.8 to **$0.06–0.14** at 100 % utilisation. Pricing
    decisions must use the corrected figure.

---

## 1. Who the users are and what they send

### 1.1 The three populations, in the order they arrive

| # | Population | How they get a key | When | What they do to the traffic shape |
|---|---|---|---|---|
| 1 | **Invited developers** via the console at `app.callbill.ai` | self-serve after invite; key created in the console, validated by the gateway against Supabase `api_keys` ([`apps/README.md`](../../apps/README.md) §3 F4/F9) | now | Small population, hand-driven and bursty: a developer pastes 200 clips into a script at 2 a.m. Arrival is **not** Poisson; it is a few large batches. |
| 2 | **Their production apps** | same keys, called from servers | +1–3 months | Steadier, diurnal, correlated with the customer's own users. This is the population the growth scenario models. |
| 3 | **OpenRouter** (or a similar aggregator) | one upstream key, fanning in many end users | later; the provider document already exists at `/v1/models` ([`apps/infrx-api/openrouter/`](../../apps/infrx-api/)) | Fan-in of many small tenants behind one key, with a **published capacity number we must honour**, and automatic failover away from us when we look unhealthy. |

Population 3 changes the requirements more than its volume suggests. OpenRouter's
provider document has a `capacity` array with entries such as
`{"type": "request", "unit": "request", "per": "minute", "value": 1000}`
[src](https://openrouter.ai/docs/use-cases/for-providers), i.e. **we publish a
rate we are expected to sustain**, and OpenRouter *"continuously monitors the
health and availability"* of providers, colour-coding uptime **green ≥95 %,
yellow ≥85 %, red <85 %** and routing away from unhealthy providers
[src](https://openrouter.ai/docs/features/uptime-optimization).

> **CORRECTED 2026-09-20.** This paragraph previously ended *"A 429 storm is
> therefore not just a bad customer experience; it is a **demotion in a
> marketplace**, which is a commercial reason to queue rather than reject,"*
> followed by a ⚠️ saying the uptime page *"does not say"* whether a 429 counts
> against the uptime score. **The provider document does say, and it says the
> opposite.** OpenRouter instructs providers to *"Return early 429s if under
> load, rather than queueing requests"*, and the errors it lists as affecting
> uptime are *"Authentication issues (401), Payment failures (402), Model not
> found (404), All server errors (500+), Mid-stream errors, Successful requests
> with error finish reasons"* — **429 is not among them**
> [src](https://openrouter.ai/docs/use-cases/for-providers). The only penalty
> named for rate limiting is indirect and evaluative, not availability:
> *"Consistent rate limiting (429s) can reduce the volume of successful requests
> available for evaluation, making it harder for us to collect enough benchmark
> data … Returning early 429s is still preferred over queueing, but minimizing
> rate limits where possible helps ensure your endpoint has sufficient data for
> a fair evaluation"* (ibid.).
>
> **What changes.** For **population 3 specifically**, the marketplace's own
> guidance is *reject fast, do not queue* — a deep queue that turns a 429 into a
> slow 200 degrades the TTFT and throughput OpenRouter measures without buying
> any uptime credit. For **populations 1 and 2** the argument for queueing is
> unchanged, because it never rested on OpenRouter: it rests on §2.4's
> definition of a drop. The design consequence is that **the queue depth is a
> per-tenant-tier property, not a fleet-wide one**: the `interactive` deadline
> (§2.3, queue-wait p99 ≤ 10 s) is already short enough to satisfy "early 429",
> and an OpenRouter-fronted key should be pinned to it rather than promoted to
> `bulk`. Publishing a conservative `capacity` number is still the right call,
> but for the throughput-benchmark reason above, not for fear of an uptime
> demotion.

### 1.2 The request mix: two operations, very different shapes

Both operations are one `POST /v1/chat/completions` with exactly one video part
(the gateway rejects a second: *"one video per request"*,
[`gateway.py`](../../apps/infrx-api/gateway.py)).

| | **caption** | **find** (temporal grounding) |
|---|---|---|
| Prompt | dense scene description + time-ranged events | `GROUNDING_PROMPT_TEMPLATE` + a query phrase |
| Prompt tokens | video tokens + ~100 text | video tokens + ~120 text |
| Output tokens | **~200 per 10 s clip** (`meas.`: 100 out tok/s ÷ 0.50 clips/s at c=1; 310 ÷ 1.57 at c=8; 760 ÷ 3.58 at c=8 360p → 197–212) | **12** (`meas.`, [`notes.md` finding 6](../../models/marlin2b/results/notes.md)) |
| Wall time, 10 s clip | 2.0 s at c=1 (`meas.`) | 0.4–0.8 s (`meas.`) |
| Dominated by | video fetch + decode, then ~200 decode steps at 6–8 ms TPOT | video fetch + decode, almost nothing else |
| Streaming useful? | yes — 200 tokens at 6 ms is 1.2 s of visible progress | **no** — 12 tokens arrive in one breath |

**Consequence for the SLO.** `find` is **pure prefill**: its entire latency is
video handling. `caption` adds ~1.2–1.6 s of decode. A single latency SLO over
both operations would be dominated by whichever mix happens to arrive. **Set the
SLO on TTFT and on a normalized per-clip-minute end-to-end, and report the two
operations separately.**

**Assumed mix** ⚠️ **TO BE VERIFIED** (no production data exists — `usage.jsonl`
has only our own smoke and bench runs): **70 % caption / 30 % find** at launch,
drifting toward find as search-style products are built on it. The acceptance
test in §6 (A1) is "measure it from `usage_events` after 2 weeks and replace this
line".

### 1.3 Clip length distribution

The hard limits are set by the model and enforced by the gateway:
`MAX_VIDEO_SECONDS=120`, 2 fps, 4–240 frames, 200,704 px/frame
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)).

The prompt-token function is exact and reconciles with measurement:

```
frames(s)       = clamp(round(2.0 × s), 4, 240), rounded up to even
video_tokens(s) = frames(s) / 2 × 196          # one 196-token patch per 2-frame temporal pair
prompt_tokens   = video_tokens(s) + ~100 text
```

| Clip | frames | video tokens | prompt tokens | check |
|---|---|---|---|---|
| 10.1 s | 20 | 1,960 | **2,060** | `meas.` **2,061** ✅ the formula is exact |
| 30 s | 60 | 5,880 | 5,980 | `est.` |
| 60 s | 120 | 11,760 | 11,860 | `est.` |
| 120 s | 240 | 23,520 | **23,620** | matches the README's *"~23.5K"* |

**Assumed distribution** ⚠️ **TO BE VERIFIED** — chosen to be pessimistic on
tail, lognormal-ish, mean ≈ 22 s:

| Bucket | Share | Prompt tokens | Why |
|---|---|---|---|
| 10–15 s | 55 % | 2.0–3.0 K | social clips, the demo path, most eval sets |
| 15–45 s | 30 % | 3.0–8.9 K | ad creative, short scenes |
| 45–90 s | 12 % | 8.9–17.7 K | full scenes |
| 90–120 s | 3 % | 17.7–23.6 K | the cap; every clip ≥120 s hits the *same* 23,620-token operating point |

Two design consequences:

- **Above 120 s everything is identical.** The 240-frame cap means one operating
  point for all long clips (this is [`03` §6.4](../scaling/03-concurrency-and-admission-control.md)'s
  observation, and it holds on L40S as much as on B300). Long-clip capacity is a
  *single* number to measure, not a curve.
- **The token mean is ~4.4 K, the token p97 is 23.6 K — a 5.4× spread.** Admission
  by request count (today's `MAX_INFLIGHT=16`) mis-prices a 120 s clip by 11.5×
  against a 10 s clip. **Admission must be by clip-seconds (or tokens), not by
  request count** — see §3 and [`03` §3.7](../scaling/03-concurrency-and-admission-control.md).

### 1.4 Source resolution — the variable that actually sets throughput

This is the measurement that should drive the whole design
([`notes.md` finding 7](../../models/marlin2b/results/notes.md), 2026-09-19,
same ~1,930–2,061 prompt tokens, concurrency 8, one L40S with 8 vCPU):

| Source | Size | clips/s | TTFT p50 | out tok/s |
|---|---|---|---|---|
| `Big_Buck_Bunny_360_10s_1MB.mp4` (360p) | 1 MB | **3.58** | **0.66 s** | 760 |
| `sample-10s.mp4` (1080p) | 5.5 MB | **1.57** | **3.35 s** | 310 |

Same tokens. Same model. **2.3× throughput, 5.1× TTFT.** The delta is *"decode and
resize of 1080p source frames on 8 vCPUs, plus the base64 upload"*. The model
never sees more than 448×448 anyway.

**Assumed resolution distribution** ⚠️ **TO BE VERIFIED**: 45 % ≤480p, 30 %
720p, 25 % 1080p+ at launch, worsening as real phone footage arrives (phone video
is 1080p or 4K by default). Plan the **all-1080p** column as the honest worst
case; treat the 360p column as what a transcode tier buys.

### 1.5 URL vs base64

| | URL (`video_url.url = https://…`) | base64 (`data:video/mp4;base64,…`) |
|---|---|---|
| Expected share ⚠️ | 70 % — it is what the docs snippet shows | 30 % — private media, no public bucket |
| Who fetches | **today: both the gateway and vLLM, independently** | the caller uploads once |
| Failure modes we inherit | slow origin, 403, redirect chains, DNS, SSRF | request-body size, memory |
| Cost | 2× egress-side download of the same bytes | ~1.37× wire size (base64 overhead) on our ingress |

**Measured symptom of the double fetch** ([`models/marlin2b/README.md`](../../models/marlin2b/README.md),
2026-09-20): a 10 s public-URL clip takes **~3.8 s end to end, TTFT ~3.2 s**,
*"dominated by downloading and decoding the 5.5 MB source twice, gateway and
vLLM"* — against **0.77 s TTFT** for the same clip served locally at c=1. The
gateway downloads the whole file to a temp file just to run `ffprobe` for the
duration, deletes it, and then passes the *original URL* to vLLM, which fetches it
again ([`gateway.py:video_seconds`](../../apps/infrx-api/gateway.py)).

**R-FETCH: the video must cross our network boundary exactly once per request.**
This is a requirement, not an optimisation: it is ~2.4 s of the 3.2 s TTFT on the
measured path.

### 1.6 Streaming share

`caption` streams usefully (200 tokens × 6–8 ms TPOT ≈ 1.2–1.6 s of visible
progress after TTFT); `find` does not (12 tokens). The gateway already handles
both and sets `stream_options.include_usage` when streaming.

⚠️ **TO BE VERIFIED**: assume **40 % of caption calls stream, 0 % of find**, i.e.
~28 % of all requests, until `usage_events.stream` says otherwise. The field is
already recorded ([`apps/README.md`](../../apps/README.md) §6), so this is a
one-query question after launch (test A1).

Streaming matters far beyond UX: a streaming response **puts bytes on the wire
within TTFT**, which is what keeps a load balancer's idle timer from firing
(§2.5). It is the cheapest way to make a long request survive the network.

### 1.7 The three traffic scenarios

Fixed by the program brief; the derived columns are computed in §3.5.

| | **S1 pilot** | **S2 growth** | **S3 burst** |
|---|---|---|---|
| Users | 10 invited developers | 100 (mostly their apps) | any — one customer's batch job |
| Peak rate | **1 req/s** | **10 req/s** | **50 req/s for 5 min** |
| Diurnal swing | irregular; long idle nights | **10×** (trough 1 req/s, peak 10 req/s) | n/a, it is the spike |
| Arrival process | batchy, low count — **do not model as Poisson**, model as a few bursts of 50–500 | Poisson-ish per tenant, summed and modulated by a diurnal envelope | deterministic ramp; one tenant |
| Daily volume | ~10 K req/day `est.` | **475,200 req/day** `est.` (sinusoid, mean 5.5 req/s) | 15,000 clips in 300 s |
| Monthly volume | ~0.3 M | **14.3 M** `est.` | — |
| GPUs at ρ=0.8, 1080p mix | **1** | **8** | **40** |
| GPUs at ρ=0.8, 50/50 mix | **1** | **6** | **29** |
| What it stresses | cold start, scale-to-zero economics, correctness | autoscaling, fairness, cost | **admission control and honesty** |

On the burst: **the correct answer to S3 is not "scale to 40 GPUs"**. Scale-up
lead time is ~5–7 minutes (§3.7) and the burst is 5 minutes long. S3 is a test of
whether the queue is bounded, the wait estimate is honest, and the rejection is
explicit. See §2.4 and §3.7.

---

## 2. SLOs

### 2.1 Why the obvious latency SLI is wrong here

A flat "p95 end-to-end < X s" SLO is unmeasurable-in-a-useful-way for this API,
because end-to-end latency legitimately varies **11.5× with clip length** and
**2.3× with source resolution**, both of which the caller controls. The SRE book's
guidance applies directly: *"It's better to start with a loose target that you
tighten than to choose an overly strict target that has to be relaxed"*, and
*"averaging request latencies … obscures an important detail: it's entirely
possible for most of the requests to be fast, but for a long tail of requests to
be much, much slower"* [src](https://sre.google/sre-book/service-level-objectives/).

So the latency SLI is **normalized**: **seconds of wall clock per minute of
video**. Measured today:

| Operating point | E2E per 10.1 s clip | **s per clip-minute** |
|---|---|---|
| 1080p, c=1 | 2.00 s (`= 1/0.50`, Little's Law on the measured row) | **11.9** |
| 360p, c=8 | 2.23 s (`= 8/3.58`) | **13.3** |
| 1080p, c=8 | 5.10 s (`= 8/1.57`) | **30.3** |

Little's Law cross-check on the c=1 row: TTFT 0.77 s + 200 tokens × 6 ms = 1.97 s
against 1/0.50 = 2.00 s ✅ — the measured table is internally consistent.

### 2.2 The SLI definitions we commit to

Every one of these is already recorded per request in `usage_events`
([`apps/README.md`](../../apps/README.md) §6) or is one field away.

| SLI | Definition (precise) | Measured from |
|---|---|---|
| **TTFT** | gateway receipt of the last request byte → first content byte of the response written to the client socket. **Includes** queue wait, video fetch, decode, prefill. Excludes TLS handshake. | `usage_events.ttft_ms`; engine-side cross-check `vllm:time_to_first_token_seconds` [src](https://docs.vllm.ai/en/latest/design/metrics.html) |
| **Queue wait** | admission (durable record written) → the request is handed to an engine. **A new field**; today it is folded into `ttft_ms` and invisible. | new `usage_events.queue_ms`; engine-side `vllm:request_queue_time_seconds` |
| **E2E per clip-minute** | `latency_ms × 60 / video_seconds` | `usage_events.latency_ms`, `video_seconds` — both already stored |
| **Availability** | fraction of *admitted* requests that reach a terminal state with a non-5xx outcome, per 30 d | `usage_events.status` |
| **Admission success** | fraction of *arriving* requests that are durably admitted (i.e. not 429/503-at-the-door), per 30 d | gateway counter — **new**, today a 429 leaves no `usage_events` row at all |
| **Error budget** | `(1 − SLO) × window`; burn-rate alerted, §2.8 | derived |

The **admission success** SLI is the one this whole program is about, and it does
not exist today: `gateway.py` returns 429 above `MAX_INFLIGHT` *before* writing
anything, so a rejection is invisible in the console and in Supabase. **R-OBS:
every arrival gets a row, including rejections.**

### 2.3 The SLO table

Three tiers. The tier is a property of the API key (a column on `api_keys`), not
of the request, so a caller cannot self-promote.

| | **interactive** (default) | **bulk** | **batch** |
|---|---|---|---|
| Surface | held connection or SSE stream | `202` + job polling | `202` + job polling, discounted |
| TTFT p95 | **≤ 6 s** for clips ≤30 s at ≤720p; ≤ 15 s for 120 s clips | n/a (report `queue_ms` instead) | n/a |
| TTFT p99 | ≤ 12 s / ≤ 30 s | n/a | n/a |
| **Queue wait p95** | **≤ 3 s** | ≤ 120 s | ≤ 60 min |
| **Queue wait p99** | ≤ 10 s | ≤ 600 s | ≤ 4 h |
| **E2E p95, s per clip-minute** | **≤ 45** | ≤ 90 | — |
| Availability, 30 d | **99.5 %** | 99.5 % | 99.0 % |
| Admission success, 30 d | **99.9 %** | 99.95 % | 99.99 % |
| Error budget | 216 min/30 d; 50 min/7 d | same | 432 min/30 d |
| Price | list | ⚠️ propose −30 % | ⚠️ propose −50 % |

Justification for each number:

- **TTFT p95 ≤ 6 s.** Measured TTFT p50 at c=8 on the bad path (1080p through
  the double fetch) is 3.35 s; on the good path (360p) it is 0.66 s. 6 s leaves
  room for one queue slot and a slow origin without being a target we cannot hit
  today. It is deliberately **loose**, per the SRE-book guidance above.
- **E2E ≤ 45 s per clip-minute.** Measured worst operating point is 30.3
  (1080p, c=8). 45 gives 1.5× headroom. A 120 s clip may therefore take 90 s,
  which is *why* the 120 s clip belongs on the job surface (§2.5).
- **Availability 99.5 % not 99.9 %.** We run **one instance in one AZ** with an
  Elastic IP and a systemd `Restart=always` ([`marlin2b-vllm.service`](../../apps/infrx-api/deploy/)).
  A vLLM restart alone is *"~2-3 min incl. torch.compile"* ⚠️ **TO BE VERIFIED
  — this quotation has no source anywhere in this repo** (2026-09-20 fact-check:
  the string appears in no `models/marlin2b/*`, `apps/infrx-api/*` or
  `results/notes.md` file; the nearest figures are
  [`06` §8](../scaling/06-cold-start.md)'s ~2–3 min for **DeepSeek-V4.1-Flash
  TP4 on B300** and [`05` §4.5](../scaling/05-autoscaling-and-predictive-scaling.md)'s
  *"no model in this repo boots in 2 minutes"* — neither is Marlin-2B on L40S).
  Treat it as `est.`, and measure it in **A3**. Two of those a month
  is 6 minutes, already 14 % of a 99.9 % budget (43.2 min/30 d). Promising 99.9 %
  before there is a second node would be, in the SRE book's words, locking
  ourselves into *"heroic efforts"*. For comparison, our own dependency Supabase
  publishes **99.9 %** and only to Enterprise customers, with third-party outages
  (AWS, Cloudflare) explicitly excluded [src](https://supabase.com/sla).
- **Admission success 99.9 % is *stricter* than availability.** This is the
  point: we would rather take a request and be slow than refuse it. 99.9 %
  admission on 14.3 M requests/month = 14,300 explicit refusals/month, all of them
  carrying a `Retry-After`.

**What we deliberately do not promise:** an output-quality SLO, a token-per-second
floor, exactly-once semantics without an idempotency key, or availability during
a declared maintenance window announced ≥24 h ahead.

### 2.4 "No dropped requests", defined precisely

This is the load-bearing definition of the whole program. A request is **not
dropped** if and only if all six hold:

**D1 — Accept or refuse, never hang.** Every arrival gets an HTTP status within
**2 s** of the last request byte. If we cannot decide in 2 s, we accept.

**D2 — Acceptance is durable before it is announced.** The caller is told
"accepted" (200 headers on a held connection, or `202` with a job id) only after
the job record is committed to storage that survives the gateway process dying.
Today nothing is durable: a `MAX_INFLIGHT` slot is a Python integer. Consequence:
`systemctl restart marlin2b-gateway` currently drops up to 16 in-flight requests
with no record anywhere. **This is the single biggest gap between today and the
promise.**

**D3 — An accepted request reaches a terminal state.** `succeeded`, `failed`
(with a typed error), or `expired` (past its deadline). Never "the connection
closed and nobody knows". Borrowed vocabulary: Anthropic's Message Batches API
uses exactly this shape — *"Batches expire if processing does not complete within
24 hours"* and a per-request result type of `expired`, *"You will not be billed
for these requests"*
[src](https://platform.claude.com/docs/en/build-with-claude/batch-processing).
**We adopt "expired is a first-class terminal state and is never billed."**

**D4 — Refusal is explicit, typed, and retryable.** A refusal carries a status,
a machine-readable `type`, and a `Retry-After`. RFC 9110: *"The Retry-After field
value can be either an HTTP-date or a number of seconds to delay after receiving
the response"* [src](https://www.rfc-editor.org/rfc/rfc9110.txt) §10.2.3, and for
503, *"The server MAY send a Retry-After header field … to suggest an appropriate
amount of time for the client to wait before retrying"* (ibid. §15.6.4). RFC 6585
on 429: *"The response representations SHOULD include details explaining the
condition, and MAY include a Retry-After header"*
[src](https://www.rfc-editor.org/rfc/rfc6585.txt) §4.

**D5 — The wait estimate is honest.** When we queue, we return the estimated
wait, and the estimate is computed from live backlog and drain rate (§3.6), not a
constant. A constant `Retry-After` synchronises every rejected client into a
thundering herd — [`03` §3.5](../scaling/03-concurrency-and-admission-control.md)
already prescribes server-side jitter, `ceil(drain_estimate × U(1.0, 1.5))`; we
adopt it verbatim.

**D6 — Retries are safe.** A retry with the same `Idempotency-Key` never runs the
model twice and never bills twice. Stripe's semantics are the reference:
*"Stripe's idempotency works by saving the resulting status code and body of the
first request made for any given idempotency key, regardless of whether it
succeeds or fails. Subsequent requests with the same key return the same result,
including `500` errors"*, keys are *"up to 255 characters"*, and *"You can remove
keys from the system automatically after they're at least 24 hours old"*
[src](https://docs.stripe.com/api/idempotent_requests). **We adopt: header
`Idempotency-Key`, 24 h retention, same-key-different-body → 400.**

**What "dropped" therefore means, operationally:** a request is dropped if it
arrives and (a) gets neither a status nor a durable job id within
`MEDIA_DECISION_S` = 2 s ([`09` §2.1](09-blueprint.md) — a `preparing` job id is
not a drop, silence is), or (b) gets a 5xx with no
`Retry-After`, or (c) is accepted and never reaches a terminal state, or (d)
completes but leaves no `usage_events` row. Each of these is an alert (§6).

**What "no dropped requests" does *not* mean.** It does not mean unbounded
queueing. An unbounded queue converts a throughput problem into a latency
problem and then into a memory problem. The queue is **bounded by a deadline, not
by a count**: we admit a request only if the estimated completion time is inside
the tier's queue-wait p99. Above that we refuse with 429 + honest `Retry-After` —
and *that refusal is not a drop*, because it is explicit, typed, timely and
retryable.

### 2.5 Which HTTP semantics we promise

Three surfaces. **All three are required**; the decision rule below picks one per
request.

**(a) Held connection, non-streaming** — `POST /v1/chat/completions`,
`stream: false`. Simplest, what the docs snippet shows, what every OpenAI client
does by default.

*The constraint nobody sees coming:* the moment we put an ALB in front of the
fleet (which §4 requires, for AZ failover), the default **connection idle timeout
is 60 seconds** and *"the period of time an existing client or target connection
can remain inactive, with no data being sent or received, before the load
balancer closes the connection"*; the valid range is **1 to 4000 seconds**, and
AWS's own advice is *"To ensure that lengthy operations such as file uploads have
time to complete, send at least 1 byte of data before each idle timeout period
elapses"* [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html).
A non-streaming 120 s clip behind a queue will blow through 60 s of silence.

Therefore: **`idle_timeout.timeout_seconds` = 180**, and the gateway **must emit
a byte before the timer expires** even on non-streaming requests. There is no
clean way to do that in JSON, which is the real argument for (b) and (c).

Also note ALBs *"do not support HTTP/2 PING frames. These do not reset the
connection idle timeout"* (ibid.) — so an HTTP/2 keepalive does **not** save us.

**(b) Held connection, streaming (SSE)** — `stream: true`. The gateway already
implements it, and Caddy is already configured for it (`flush_interval -1`,
which *"disable[s] buffering entirely and flush[es] after each write"*
[src](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)).

**R-SSE: while a streamed request is queued or prefilling, emit an SSE comment
line (`: keepalive\n\n`) every 15 s.** This is the one-line fix for the ALB idle
timer, is invisible to compliant SSE clients, and — bonus — turns queue position
into something we can push: `: queued position=7 eta=12s`.

**(c) `202 Accepted` + job polling** — `POST /v1/jobs` → `202` + `{"id": …,
"status": "queued", "estimated_wait_seconds": …}`; `GET /v1/jobs/{id}`; optional
`webhook` on completion.

RFC 9110 on 202: *"the request has been accepted for processing, but the
processing has not been completed … The 202 response is intentionally
noncommittal. Its purpose is to allow a server to accept a request for some other
process … without requiring that the user agent's connection to the server
persist"* [src](https://www.rfc-editor.org/rfc/rfc9110.txt) §15.3.3. Note also
its warning: *"There is no facility in HTTP for re-sending a status code from an
asynchronous operation"* — which is exactly why the job resource must carry the
final status and why the webhook is a convenience, not the contract.

Google's AIP-151 puts the threshold at exactly our number: use a long-running
operation for methods requiring *"a significant amount of time to complete"*, and
*"A good rule of thumb is 10 seconds"* [src](https://google.aip.dev/151). A 120 s
clip at our own SLO (≤45 s per clip-minute) is 90 s. **Every clip over ~20 s is
past AIP-151's threshold.**

Two implementations worth copying:

- **Replicate** offers both from one endpoint: async by default, and *"`Prefer:
  wait` … Synchronous predictions hold the request open for a specified
  duration, which defaults to 60 seconds"*, `Prefer: wait=X` for a custom
  timeout, plus webhooks and polling on `urls.get`
  [src](https://replicate.com/docs/topics/predictions/create-a-prediction).
  One resource, two behaviours, chosen by a request header. **We adopt
  `Prefer: wait=<seconds>` verbatim.**
- **SageMaker Asynchronous Inference** *"queues incoming requests and processes
  them asynchronously … ideal for requests with large payload sizes (up to 1GB),
  long processing times (up to one hour) … autoscaling the instance count to
  zero"* [src](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html).
  The shape (queue + poll + SNS notification + scale-to-zero) is the same one we
  need; the reason not to simply use it is covered in a sibling document.

**Decision rule — which surface a request gets:**

```
if request has Prefer: wait=N        -> hold the connection up to N seconds (max 300);
                                        on timeout return 202 + job id, do NOT cancel the work
elif stream == true                  -> SSE, with 15 s keepalive comments while queued
elif video_seconds <= 30
     and estimated_wait <= 5 s       -> hold the connection (non-streaming)
else                                 -> 202 + job id
```

The middle branch is the important one: **a request that would have been a simple
200 becomes a 202 when the fleet is busy**. That is the mechanism by which a
burst does not become a drop. It is also a behaviour change for existing clients,
so it must be opt-in at first: a key-level flag `async_upgrade_enabled`, default
off for the pilot, default on before OpenRouter.

### 2.6 Status codes and headers, as a contract

Builds on [`03` §3.5](../scaling/03-concurrency-and-admission-control.md)'s table;
this is the public-API-facing version.

| Code | `error.type` | When | Headers |
|---|---|---|---|
| **200** | — | completed (held connection or stream) | `Inference-Id` |
| **202** | — | durably queued | `Inference-Id`, `Location: /v1/jobs/{id}`, `Retry-After` = poll interval |
| **400** | `invalid_request_error` | >1 video, clip >120 s, >64 MB, unparseable media, bad `video_url` scheme | — |
| **401** | `authentication_error` | unknown or revoked key | — |
| **402** | `insufficient_credits` | org balance ≤ 0 (`credit_ledger` sum) | — **new**; today there is no spend protection at all |
| **413** | `payload_too_large` | body over the limit, **decided from `Content-Length` before reading** | — |
| **422** | `unprocessable_media` | fetched fine, `ffprobe` says it is not decodable video | — |
| **429** | `rate_limit_error` | **tenant** over quota | `Retry-After`, `X-RateLimit-*` |
| **429** | `queue_full` | **fleet** backlog exceeds the tier deadline | `Retry-After` = jittered drain estimate |
| **499** | — | client disconnected; logged, never returned | — |
| **502/504** | `upstream_error` | vLLM failed or hung; **retried once on another replica before surfacing** | `Retry-After` |
| **503** | `server_error` | cannot verify the key (Supabase down) — the gateway already does this correctly | `Retry-After: 5` |

**Rate-limit headers.** Adopt OpenAI's names, because every client library
already understands them: `x-ratelimit-limit-requests`,
`x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests`,
`x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens`,
`x-ratelimit-reset-tokens`, plus `Retry-After`
[src](https://developers.openai.com/api/docs/guides/rate-limits). OpenAI's own
retry advice is the advice we put in our docs: *"Follow `Retry-After` when it's
present, reduce your request rate, and then increase it gradually"*, with
*"exponential backoff with jitter"* (ibid.).

**One addition of our own**, because tokens are the wrong unit for a video API:
`x-ratelimit-limit-video-seconds` / `-remaining-video-seconds` /
`-reset-video-seconds`. Clip-seconds are what we actually meter (§3.4), and a
caller who knows their video-second budget can pace themselves without guessing
our tokenizer.

**Queue-transparency headers** on 202 and on queued SSE streams:
`X-Queue-Position`, `X-Queue-Estimated-Wait-Seconds`, `X-Queue-Depth`.

### 2.7 Error budget and burn-rate alerting

Availability 99.5 % over 30 days = **216 minutes**; over 7 days = **50.4
minutes**. Admission 99.9 % = 43.2 min-equivalent, but measured in requests:
14,300 refusals per 14.3 M.

Alert with the multiwindow, multi-burn-rate scheme from the SRE workbook — long
and short windows must *both* exceed threshold, with *"the short window 1/12 the
duration of the long window"*, giving *"better reset time by ceasing to fire five
minutes later, rather than one hour later"*
[src](https://sre.google/workbook/alerting-on-slos/). Its table for a 99.9 % SLO:

| Severity | Long window | Short window | Burn rate | Budget consumed |
|---|---|---|---|---|
| Page | 1 hour | 5 minutes | **14.4×** | 2 % |
| Page | 6 hours | 30 minutes | **6×** | 5 % |
| Ticket | 3 days | 6 hours | **1×** | 10 % |

Rescaled to our 99.5 % availability SLO the burn rates are unchanged (they are
multiples of the budget, not of the target); only the absolute error rate that
triggers them moves. [`08` §1.3](../scaling/08-reliability-and-operations.md)
already carries the PromQL shape for this against vLLM histograms.

---

## 3. Capacity, from the measured numbers

### 3.1 The measured baseline

One `g6e.2xlarge`: 1× L40S 48 GB, **8 vCPU**, 64 GiB RAM, 1×450 GB NVMe, up to 20
Gbit [src](https://aws.amazon.com/ec2/instance-types/g6e/) — confirmed against the
AWS Price List row for `g6e.2xlarge` in us-east-1 (§3.9). vLLM nightly pulled
2026-09-19, `--max-model-len 32768`, `--max-num-seqs 32`, `--gpu-memory-utilization
0.90`.

| Clip | c | prompt tok | TTFT p50 | TPOT p50 | clips/s | out tok/s | **video-s/s** | **E2E (Little)** |
|---|---|---|---|---|---|---|---|---|
| 1080p 10.1 s | 1 | 2,061 | 0.77 s | 6 ms | 0.50 | 100 | 5.05 | 2.00 s |
| 1080p 10.1 s | 8 | 2,061 | 3.35 s | 8 ms | 1.57 | 310 | **15.86** | 5.10 s |
| 1080p, processor default | 8 | 12,221 | 3.70 s | 8 ms | 1.47 | 290 | 14.85 | 5.44 s |
| 360p 10.1 s | 8 | 1,928 | 0.66 s | 7 ms | **3.58** | 760 | **36.16** | 2.23 s |

`video-s/s = clips/s × 10.1`. `E2E = c / clips/s` (Little's Law).

**The row that should decide the architecture** is rows 2 and 3 together: **6×
the prompt tokens costs 6 % of the throughput.** The LM is not the bottleneck;
per-request video handling is ([`notes.md` finding 5](../../models/marlin2b/results/notes.md)).

**The caveat that must be discharged before any of this is load-bearing**
([`notes.md` finding 5](../../models/marlin2b/results/notes.md), verbatim):
*"every request used the same clip, so vLLM's multimodal processor cache may have
absorbed decode cost; rerun with distinct clips before quoting preprocessing
cost."* vLLM's `mm_processor_cache_gb` defaults to **4 GiB**
[src](https://docs.vllm.ai/en/latest/configuration/optimization.html), which is
plenty to hold one clip's processed tensors forever. **Every clips/s number above
is potentially optimistic on a real, all-distinct-clips workload.** Acceptance
test A2 closes this.

### 3.2 KV and state budget on the L40S

Marlin-2B is a hybrid: **6 full-attention layers of 24** at `head_dim 256`, GQA
4:1, plus **18 GDN linear-attention layers** whose state is a fixed per-sequence
allocation, not a per-token one. From
[`../models/marlin2b/b300.md`](../models/marlin2b/b300.md) §1:

- **12,288 B/token BF16 KV** (6 of 24 layers)
- **19,537,920 B (18.63 MiB) GDN state per sequence**, `S = 1` slot,
  **independent of sequence length**
- weights **4.426 GB BF16 resident**

Same formula, L40S inputs (48 GB nominal, `--gpu-memory-utilization 0.90`, 4 GiB
activation/workspace reserve):

```
max_concurrency(ctx) = floor( (0.90 × 48e9 − 4.426e9 − 4 GiB) / (ctx × 12,288 + 19,537,920) )
                     = floor( 34.48e9 / (ctx × 12,288 + 19,537,920) )
```

| Context | Clip length | **Max concurrent sequences (KV-bound)** |
|---|---|---|
| 2,048 | 10 s | **771** |
| 4,096 | 20 s | 493 |
| 8,192 | 40 s | 286 |
| 16,384 | 80 s | 156 |
| 23,616 | **120 s (the cap)** | **111** |
| 32,768 | `--max-model-len` | 81 |

All `est.` ⚠️ — 48e9 is the nominal L40S figure, not an `nvidia-smi` reading from
our box; the true usable number is a one-command check (test A3). There is no
`research/gpus/l40s.md`, so unlike every other GPU in this repo the L40S has no
pinned usable-memory row.

**The gap that matters.** KV allows **111 concurrent 120 s videos**. Measured
throughput at c=8 is 1.57 clips/s. Running 111 concurrent video prefills on one
L40S would mean a queue of serial prefill work measured in minutes. This is the
same 75× gap [`03` §6.4](../scaling/03-concurrency-and-admission-control.md)
identified on B300 (753 KV-max vs ~10 for a 2 s TTFT), and the same conclusion
applies here:

> **Admit on prefill throughput, not on KV.** `max_num_seqs` must sit far below
> the KV cap. The measured sweet spot on this box is **c ≈ 8**; the deployed
> engine runs at `--max-num-seqs 32` and the gateway caps at `MAX_INFLIGHT=16`,
> both above it.

> **CORRECTED 2026-09-20 (this repo's code).** The line above said *"`serve.sh`
> currently passes `--max-num-seqs 32`"*. It does not.
> [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh) passes
> `--served-model-name`, `--hf-overrides`, `--max-model-len`,
> `--gpu-memory-utilization "${GPU_MEM:-0.90}"`, `--limit-mm-per-prompt` and
> `--dtype bfloat16`, then forwards `"$@"`; it sets **no** `--max-num-seqs`, so
> a bare `serve.sh` inherits vLLM's own default. The flag is supplied by the
> **systemd unit**, at
> [`apps/infrx-api/deploy/marlin2b-vllm.service`](../../apps/infrx-api/deploy/):
> `ExecStart=/home/ubuntu/model-inference/models/marlin2b/serve.sh --max-num-seqs 32`.
> That matters operationally: **the one-line fix is to the unit file, not to
> `serve.sh`**, and editing `serve.sh` would change nothing on the running box.
> (`apps/infrx-api/openrouter/PLAN.md` line 63 carries the same 32 in its
> suggested command line and should move with it.) Implications item 11 is
> corrected to match.

**KV is not the binding constraint on this box at any clip length.** Do not spend
effort on FP8 KV cache here (it would take 111 → ~210 at 120 s, a ceiling we
never approach). The B300 documents' KV-quantisation advice does not transfer.

### 3.3 Where CPU binds and where GPU binds

| Stage | Runs on | Cost, 10 s 1080p | Cost, 10 s 360p | Scales with |
|---|---|---|---|---|
| Fetch (gateway) | network + 1 CPU thread | 5.5 MB | 1 MB | file size |
| `ffprobe` duration | 1 CPU process | ~50 ms ⚠️ | ~50 ms ⚠️ | container parse only |
| **Fetch again (vLLM)** | network + CPU | 5.5 MB **again** | 1 MB again | file size |
| **Decode + resize to 448×448** | **CPU (8 vCPU)** | **the bottleneck** | much cheaper | **pixels × frames** |
| ViT encode | GPU | 20 frames → 10 patches | same | frames |
| LM prefill | GPU | 2,061 tok | 1,928 tok | tokens |
| LM decode | GPU | 200 tok × 6–8 ms | 212 tok × 7 ms | output tokens |

**The evidence that CPU binds:** identical token counts, identical GPU work,
2.3× throughput difference driven purely by source pixels
([`notes.md` finding 7](../../models/marlin2b/results/notes.md)). And 6× the
*tokens* (finding 5) costs only 6 % throughput — the GPU has slack.

**Three ways to unbind it, cheapest first:**

1. **Fetch once** (R-FETCH, §1.5). Removes one full download and one container
   parse. Worth ~2.4 s of the measured 3.2 s remote TTFT. **Cost: zero.**
2. **Pre-transcode to ≤480p before the engine sees it.** The model never exceeds
   448×448. Upper bound on the win is the measured 360p-vs-1080p ratio:
   **1.57 → 3.58 clips/s, 2.28×**. This is the single largest throughput lever in
   the system and it costs a CPU pipeline, not a GPU. ⚠️ The transcode itself
   consumes vCPU, so the *net* win is smaller than 2.28× on the same box — which
   is precisely why the transcode belongs on separate, cheap, CPU-only capacity.
3. **More vCPU per GPU.** `g6e.4xlarge` = 16 vCPU for **$3.00424/h** vs
   `g6e.2xlarge` = 8 vCPU for **$2.24208/h** — **+34 % price for 2× the vCPU**
   (§3.9). If throughput scales anywhere near linearly with vCPU on the 1080p
   path, this is a **1.49× improvement in $/clip** for one `sed` in a launch
   template. **Run this experiment first** (test A4).

   > **CORRECTED 2026-09-20 (arithmetic).** This line read **1.7×**. Under its
   > own stated premise — throughput scales linearly with vCPU, i.e. 2× — the
   > gain is `2 / (3.00424 / 2.24208)` = `2 / 1.34` = **1.49×**, not 1.7×.
   > Per-clip: $2.24208 / (1.57 × 3600) = **$0.0003967** on `g6e.2xlarge`
   > against $3.00424 / (3.14 × 3600) = **$0.0002658** on `g6e.4xlarge`, a ratio
   > of **1.493**. The 1.7 figure is `2.28 / 1.34`, which substitutes the
   > **resolution** ratio (3.58/1.57, §1.4) for the **vCPU** ratio — a different
   > lever, and one `g6e.4xlarge` does not buy. Break-even is unchanged at
   > **1.34×**, so the experiment is still the highest-expected-value change in
   > the program; its upside is one third smaller than stated.
4. **GPU video decode.** [`03` §6.4](../scaling/03-concurrency-and-admission-control.md)
   records that GPU decode *"more than double[s] the throughput"* vs the CPU
   decoder, via `--media-io-kwargs '{"video":{"backend":"pynvvideocodec"}}'`
   [src](https://vllm.ai/blog/2026-09-18-pynvvideocodec). ⚠️ That measurement is
   on 8×H100 for a different workload, and L40S NVDEC capability and vLLM's
   support on this image are unverified here. Cheap to try, high variance.

### 3.4 The meter: clip-seconds, not requests

Because prompt tokens are an exact function of clip length (§1.3) and output
tokens are ~200 (caption) or 12 (find), **video-seconds is a complete billing and
admission unit** for this model. Admission by request count is wrong by 11.5×
between a 10 s and a 120 s clip.

```
cost_units(request) = video_seconds × resolution_factor
resolution_factor   = 1.0 if the source is already ≤480p after our transcode
                      2.28 if we must decode a 1080p source at request time   # = 3.58/1.57, meas.
```

Per-GPU capacity in those units, at the measured operating point:

| Path | video-s/s per GPU | video-s per GPU-hour |
|---|---|---|
| 1080p at request time | **15.86** | 57,085 |
| ≤480p (pre-transcoded) | **36.16** | 130,169 |

This is the number the queue's wait estimator and the tenant quota both use
(§3.6, §5.4).

### 3.5 GPUs per scenario

`GPUs = ceil(peak_rate / (per-GPU clips/s × ρ))`, 10 s clips, ρ = target
utilisation. N+1 is **not** included — add it in §4.

| Scenario | peak req/s | **all 1080p** (1.57/GPU) | **50/50 blend** (2.18/GPU) | **all ≤480p** (3.58/GPU) |
|---|---|---|---|---|
| | | ρ=0.7 / ρ=0.8 | ρ=0.7 / ρ=0.8 | ρ=0.7 / ρ=0.8 |
| **S1 pilot** | 1 | 1 / 1 | 1 / 1 | 1 / 1 |
| **S2 growth** | 10 | **10 / 8** | **7 / 6** | **4 / 4** |
| **S3 burst** | 50 | 46 / 40 | 33 / 29 | 20 / 18 |

The 50/50 blend rate is the harmonic mean, `1 / (0.5/1.57 + 0.5/3.58) = 2.18`,
because a mixed stream's throughput is set by time spent, not by rate averaged.

**S2 cost, with and without autoscaling** (`g6e.2xlarge` $2.24208/h, 50/50 blend,
ρ=0.8, sinusoidal 10× diurnal with mean 5.5 req/s):

| Policy | GPU-hours/day | $/month |
|---|---|---|
| Static, sized for peak (6 GPU) | 144 | **$9,686** |
| Hourly autoscale, `ceil` per hour, no floor | 85 | **$5,717** (−41 %) |

*Both `$/month` cells reproduce on a **30-day month** (`144 × 2.24208 × 30 =
9,685.79`; `85 × 2.24208 × 30 = 5,717.30`), as do §3.5's `$1,614` (`24 ×
2.24208 × 30`) and `$538` (`8 × 2.24208 × 30`) and §1.7's `14.3 M` monthly
requests (`475,200 × 30`). Stated here because a 30.44-day average month would
put the first row at $9,828 — basis confirmed 2026-09-20, no number changed.*

`est.`, and the autoscaled row is an **upper bound on the saving**: it assumes
instant scaling. With a ~5–7 min lead time (§3.7) and an anti-thrash cooldown
([`05` §5.3](../scaling/05-autoscaling-and-predictive-scaling.md)), realistic
saving is smaller. The point stands: **autoscaling is worth roughly 40 % of the
GPU bill at a 10× diurnal swing**, which is the order of magnitude
[`05` §8.3](../scaling/05-autoscaling-and-predictive-scaling.md) predicts.

**S1 cost:** 1 GPU always-on = **$1,614/month**. Scale-to-zero outside an 8 h
window = **$538/month**. With a 2–3 min vLLM boot, scale-to-zero costs the first
caller of the day a multi-minute wait — acceptable on the `batch` tier, not on
`interactive`. [`06` §5.1](../scaling/06-cold-start.md) on warm pools applies.

### 3.6 The queue, and the wait estimate we return

**The estimator the gateway should actually use** — deliberately not Erlang-C,
because we have the real backlog:

```
# all quantities in video-seconds
backlog       = Σ over queued requests of cost_units(r)         # §3.4
in_flight     = Σ over running requests of remaining_cost_units(r)
drain_rate    = Σ over healthy replicas of measured_video_s_per_s   # EWMA, 60 s window
eta_seconds   = (backlog + in_flight) / max(drain_rate, epsilon)
               + (pending_scale_up ? 0 : 0)                      # scale-up does NOT reduce eta
                                                                 # until the replica is healthy
admit  if eta_seconds + own_cost/drain_rate <= tier.queue_wait_p99
else   429 queue_full, Retry-After = ceil(eta_seconds × U(1.0, 1.5))
```

Three properties that matter: it is **measured, not modelled** (drain rate is an
EWMA of what actually happened); it **does not count capacity that has not booted**
(the single most common way an ETA lies); and it **refuses by deadline, not by
depth**, so a queue of 400 fast 10 s clips is fine while a queue of 40 slow 120 s
clips is not.

**Erlang-C sanity check**, to confirm the deadline targets in §2.3 are reachable
(M/M/c, per-GPU μ = 1.57 clips/s at the 1080p operating point):

| λ (req/s) | GPUs | offered (erlangs) | ρ | P(wait) | E[Wq] | p95 Wq | p99 Wq |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 0.64 | 0.64 | 0.637 | 1.12 s | 4.46 s | 7.29 s |
| 1 | 2 | 0.64 | 0.32 | 0.154 | 0.07 s | 0.53 s | 1.28 s |
| 6 | 5 | 3.82 | 0.76 | 0.487 | 0.26 s | 1.23 s | 2.10 s |
| 10 | 8 | 6.37 | 0.80 | 0.449 | 0.18 s | 0.86 s | 1.49 s |
| 12 | 9 | 7.64 | 0.85 | 0.548 | 0.26 s | 1.12 s | 1.88 s |
| 50 | 33 | 31.85 | 0.97 | 0.777 | 0.43 s | 1.52 s | 2.40 s |
| 50 | 40 | 31.85 | 0.80 | 0.115 | 0.01 s | 0.06 s | 0.19 s |

`est.`, M/M/c with `P(Wq > t) = C(c,a)·e^−(cμ−λ)t`. **The p95/p99 queue-wait SLOs
in §2.3 (≤3 s / ≤10 s) are comfortably met at ρ ≤ 0.85 once there is more than one
replica.** The killer row is the first one: **at ρ=0.64 on a single server,
p99 queue wait is already 7.3 s.** One replica cannot hit an interactive
queue-wait SLO at any useful utilisation — not because it is slow, but because
c=1 has no statistical multiplexing. **N=2 is a latency requirement, before it is
an availability requirement.**

[`05` §6.1–6.3](../scaling/05-autoscaling-and-predictive-scaling.md) carries the
general Little's-Law and Erlang-C treatment; the numbers above are that machinery
applied to this box's measured μ.

### 3.7 The burst, and why scaling cannot solve it

**S3: 50 req/s × 300 s = 15,000 clips.**

| Fleet during the burst | Served in 300 s | Backlog at t=300 s | Drain at 1 req/s residual |
|---|---|---|---|
| 1 GPU (today) | 471 | **14,529** | ~7 hours |
| 4 GPU | 1,884 | 13,116 | 41 min |
| 8 GPU | 3,768 | 11,232 | 16 min |
| 16 GPU | 7,536 | 7,464 | 5.2 min |
| 29 GPU (50/50 blend, ρ=0.8) | 18,966 | **0** | — |

Now the lead time. Stage budget for a new `g6e.2xlarge` joining the fleet:

| Stage | Seconds | Source |
|---|---|---|
| `RunInstances` → SSH/user-data running | ~90 ⚠️ | `est.`; unmeasured on our AMI |
| docker image cached + vLLM boot incl. `torch.compile` | ~165 ⚠️ | ~~`meas.`~~ **`est.`** — *"vLLM boots in ~2–3 min incl. torch.compile"* is **unsourced in this repo** (2026-09-20 fact-check; see §2.3). It is the largest single stage in this budget, so the 438 s total is `est.` end to end, not a measured figure. Closes with **A3**. |
| weights from local NVMe (5.4 GB) | ~15 ⚠️ | `est.` |
| first request with a new kwargs set | **18** | `meas.`, [`notes.md` finding 4](../../models/marlin2b/results/notes.md) |
| ALB target healthy at defaults (`HealthyThresholdCount` **5** × `HealthCheckIntervalSeconds` **30**) | **150** | [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html) |
| **Total** | **~438 s ≈ 7.3 min** | |

**During 438 seconds at 50 req/s, 21,900 requests arrive.** The burst is over
before the first new GPU takes traffic.

Three of those stages are directly attackable and each is worth naming:

- **Health-check defaults cost 150 s of the 438.** ALB defaults are
  `HealthCheckIntervalSeconds` 30, `HealthyThresholdCount` 5,
  `HealthCheckTimeoutSeconds` 5, `UnhealthyThresholdCount` 2, `Matcher` 200
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html).
  Setting interval 5 s / healthy threshold 2 takes it to **10 s** — a 140 s saving
  for one CLI flag. Also set `slow_start.duration_seconds` (range **30–900**,
  default **0 = disabled**) so a fresh replica is not immediately given its full
  share while its caches are cold, and keep `deregistration_delay.timeout_seconds`
  (default **300 s**) at or above the longest request
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html).
- **The 165 s vLLM boot is attackable by warm pools.** EC2 Auto Scaling warm
  pools hold *"pre-initialized EC2 instances"* in `Stopped`, `Running` or
  `Hibernated` state; *"Keeping instances in a `Stopped` state is an effective way
  to minimize costs. With stopped instances, you pay only for the volumes that you
  use and the Elastic IP addresses"*, while `Hibernated` *"signals the operating
  system to save the contents of your RAM to your Amazon EBS root volume"* and on
  restart *"the RAM contents are reloaded"*
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html).
  ⚠️ **Hibernation of a GPU instance with CUDA context and a loaded model is not
  something AWS documents as working** — the warm-pool page lists hibernation
  prerequisites but says nothing about accelerators. Do not plan on it; a
  `Stopped` warm pool still saves the ~90 s launch stage but not the 165 s boot.
  [`06` §4–5](../scaling/06-cold-start.md) covers the engine-side alternatives
  (vLLM sleep mode, compile-cache shipping).
- **The 18 s first-request JIT is free to remove**: send one synthetic warm-up
  request per kwargs shape at boot, before reporting healthy
  ([`06` §3.5](../scaling/06-cold-start.md)).

Even fully optimised — 90 s launch + 165 s boot + 10 s health = **~265 s** — the
burst is 300 s. **Conclusion: S3 is met by admission control and headroom, not by
autoscaling.** Concretely:

- **Keep N+1 warm** so the fleet absorbs the first ~30 s of any spike.
  ⚠️ **Superseded at pilot scale by [`09` §0.2 R6](09-blueprint.md).** The spare
  costs 1/N, and at N = 1 that is +93.4 % of the fixed floor, so the pilot covers
  the window with the 202 upgrade instead; [`09` §3.4a](09-blueprint.md) also
  shows the absorbed window is **90–170 s, not ~30 s**. Applies as written at
  N ≥ 3.
- **Queue the rest with an honest ETA**, and let `Prefer: wait` callers convert
  to 202 automatically.
- **Refuse above the deadline** with `Retry-After` and the queue headers — which
  under §2.4 is *not* a drop.
- For a *known* burst (a customer's nightly batch), **pre-warm on a schedule**;
  [`05` §4.6](../scaling/05-autoscaling-and-predictive-scaling.md) covers it, and
  AWS's own note applies: *"If you use Amazon EC2 Auto Scaling or Amazon EKS, you
  can schedule scaling to run at the start of the Capacity Block reservation"*
  [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html).

### 3.8 Cost per video-hour — and a correction

AWS Price List, us-east-1, Shared tenancy, Linux, `BoxUsage:g6e.2xlarge`,
publication version 2026-09-18 ⇒ **$2.24208/hour**
[src](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.csv).

| Path | video-s/s | video-**hours** per GPU-hour | $/video-hour @100 % | @60 % |
|---|---|---|---|---|
| 1080p at request time | 15.86 | **15.86** | **$0.1414** | $0.2357 |
| ≤480p pre-transcoded | 36.16 | **36.16** | **$0.0620** | $0.1033 |

> ⚠️ **Correction to [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md)
> §"Cost sketch".** It reads: *"16–36 video-seconds per second → one GPU-hour
> processes 57–129 video-hours → $0.02–0.04 per video-hour"*. 15.86 video-s/s ×
> 3600 s = **57,085 video-seconds** = **15.86 video-hours**, not 57. The
> conversion divides by 1,000 instead of 3,600. The true figure is **$0.06–0.14
> per video-hour**, i.e. **3.6× more expensive** than the note says. Every
> pricing decision must use the corrected number; the note should be fixed in
> place.

Sanity: at ~200 output tokens per 10 s clip, 1.57 clips/s = 310 out tok/s, so
one GPU-hour yields 1.116 M output tokens at $2.24208 → **$2.01 per 1M output
tokens** at 100 % utilisation. That is the row to compare against the
$/1M figures in [`07-cost-engineering.md`](../scaling/07-cost-engineering.md);
it is far above the $0.026 estimated there for Marlin on B300 at 85 %, which is
expected — an L40S with 8 vCPU decoding 1080p is not a B300, and the L40S rows
here are measured while the B300 rows are `est.`

### 3.9 Instance-size decision

Full `g6e` on-demand, us-east-1, Linux, from the same price-list pull, with the
derived $/vCPU-hour that the CPU-bound finding makes relevant:

| Instance | GPUs | vCPU | RAM | NVMe | Network | $/h | $/h per GPU | vCPU per GPU |
|---|---|---|---|---|---|---|---|---|
| `g6e.2xlarge` | 1 | 8 | 64 GiB | 450 GB | up to 20 Gb | **$2.24208** | $2.242 | 8 |
| **`g6e.4xlarge`** | 1 | **16** | 128 GiB | 600 GB | 20 Gb | **$3.00424** | $3.004 | **16** |
| `g6e.8xlarge` | 1 | 32 | 256 GiB | 900 GB | 25 Gb | $4.52856 | $4.529 | 32 |
| `g6e.16xlarge` | 1 | 64 | 512 GiB | 1,900 GB | 35 Gb | $7.57719 | $7.577 | 64 |
| `g6e.12xlarge` | 4 | 48 | 384 GiB | 3,800 GB | 100 Gb | $10.49264 | $2.623 | 12 |
| `g6e.24xlarge` | 4 | 96 | 768 GiB | 3,800 GB | 200 Gb | $15.06559 | $3.766 | 24 |
| `g6e.48xlarge` | 8 | 192 | 1,536 GiB | 7,600 GB | 400 Gb | $30.13118 | $3.766 | 24 |

Reading:

- **`g6e.4xlarge` is the experiment.** +34 % price, 2× vCPU. Break-even needs only
  a **1.34× throughput gain** on the 1080p path. Given that 8 vCPU is provably the
  bottleneck, this is the highest-expected-value change in the program and costs
  one launch-template edit (test A4).
- **`g6e.12xlarge` is the cheapest $/GPU in the family** ($2.623 vs $2.242 for
  2xlarge — wait, it is *higher*; the cheapest $/GPU is `g6e.2xlarge` itself).
  Correct reading: **multi-GPU g6e sizes are more expensive per GPU, not less.**
  Their advantage is 12 vCPU/GPU (vs 8) and one instance to manage, plus 100–400
  Gb networking. `g6e.12xlarge` gives 4 GPUs at 12 vCPU each for $2.623/GPU-h —
  **+17 % over 2xlarge for +50 % vCPU**, a better ratio than 4xlarge if we need
  4 GPUs anyway.
- **Capacity reality check.** g6e capacity in us-east-1 is scarce: only `us-east-1d`
  had capacity, others returned `InsufficientInstanceCapacity`. ⚠️ **TO BE
  VERIFIED** — 2026-09-20 fact-check: no capacity survey exists in this repo.
  The only supporting fact on record is that the dev box happens to run in
  `us-east-1d` ([`CLAUDE.md`](../../CLAUDE.md): *"`g6e.2xlarge`
  (`i-0e8449a4ffca29bab`, us-east-1d)"*), which is consistent with the claim but
  does not establish that the other AZs refused. **A5's second command
  (`describe-instance-type-offerings`) answers the AZ-coverage half; the
  refusal half needs an actual launch attempt per AZ.** Do not size the
  multi-AZ ASG (F3) on this sentence until it is measured. That is an
  availability requirement, not a footnote — see §4. On-Demand Capacity
  Reservations *"allow you to reserve compute capacity for your Amazon EC2
  instances in a specific Availability Zone for any duration"* with *"no term
  commitment"* for immediate use, but *"No billing discount"* and *"Active and
  unused Capacity Reservations count toward your On-Demand Instance limits"*
  [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html).
  An ODCR for N+1 g6e in `us-east-1d` costs full on-demand rate whether or not we
  run it — **$2.24/h = $1,614/month for the insurance**. That is the honest price
  of "we do not lose the fleet to a capacity shortage".
- **Capacity Blocks for ML do not help.** The supported instance list is
  `p6-b300`, `p6-b200`, `p5*`, `p4d*`, `trn*` — **no `g6e`**
  [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html).
- **Quotas are per-Region and in vCPUs.** *"there is a maximum number of Amazon
  EC2 vCPUs that you can provision for On-Demand Instances in a Region"*
  [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html).
  The S3 burst fleet of 29–46 `g6e.2xlarge` is **232–368 vCPU** of G-family quota.
  ⚠️ Our current quota is unknown; raising it takes a support case with lead time,
  so **check and raise it before the pilot, not during the burst** (test A5).

---

## 4. Failure modes to design against

Ranked by expected pain today. Each row states the requirement, not just the
risk. [`08` §2](../scaling/08-reliability-and-operations.md) carries the general
catalogue and the runbooks; these are the ones specific to *this* deployment.

| # | Failure | Today | Blast radius | Requirement |
|---|---|---|---|---|
| **F1** | **Gateway process restart / deploy** | 16 in-flight requests vanish; no record anywhere (`inflight` is a Python int, `install.sh` restarts the unit) | every in-flight caller | **R-DURABLE**: admission writes a durable job record before the caller is told "accepted" (D2). Drain on `SIGTERM`: stop accepting, finish in-flight, exit. |
| **F2** | **GPU node loss** (hardware, Xid, spot-like reclaim, AZ event) | total outage; one box, one AZ, one EIP | 100 % | **R-N+1**: ≥2 replicas behind an ALB across ≥2 AZs; queued jobs survive node loss and are re-dispatched. N=2 is also a *latency* requirement (§3.6). |
| **F3** | **AZ capacity shortage** — `InsufficientInstanceCapacity` on g6e | scale-up silently fails; the ASG keeps trying | scale-up impossible during a burst | **R-CAPACITY**: multi-AZ ASG + a second instance type in the mixed-instances policy (`g6e.4xlarge` is a drop-in) + an ODCR for the N+1 replica (§3.9). Warm-pool docs confirm the failure shape: *"If your warm pool is depleted … instances will launch directly into the Auto Scaling group (a cold start). You could also experience cold starts if an Availability Zone is out of capacity"* [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html). Alarm on launch failures, not just on capacity. |
| **F4** | **Slow or failing video origin** | `httpx.AsyncClient(timeout=60, follow_redirects=True)` in the gateway **and** an independent fetch by vLLM; a 60 s stall holds a `MAX_INFLIGHT` slot | one slow origin can consume all 16 slots | **R-FETCH-BUDGET**: connect timeout 3 s, total fetch timeout 20 s, max 3 redirects, per-host concurrency cap, and **fetch outside the engine slot** — a request must not hold GPU admission while downloading. Return **424** `source_unavailable` (typed, retryable) rather than a generic 400. |
| **F5** | **Malformed / non-video media** | `ffprobe` fails → 400 `could not read video: …` with the exception text | one caller | **R-MEDIA**: 422 `unprocessable_media`, bounded `ffprobe` (already `timeout=30`, tighten to 10 s), never echo exception text (it leaks internal paths). |
| **F6** | **Poison request** — a clip that reliably crashes or hangs the decoder/engine | the job would be retried forever by any queue | the whole fleet, repeatedly | **R-POISON**: attempt counter on the job; after 2 attempts → terminal `failed` with `poison_suspected`, and quarantine the content hash. SQS's dead-letter pattern is the reference: *"configure a Dead-Letter Queue (DLQ) … preventing them from repeatedly circulating in the main queue"* [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html). |
| **F7** | **vLLM hang** (no tokens, requests "running") | `/health` checks vLLM's `/health`, which can pass while the scheduler is wedged | fleet-wide stall with green health checks | **R-LIVENESS**: health must be a **synthetic inference** on a tiny cached clip with a deadline, not a `/health` ping. Watchdog: `vllm:num_requests_running > 0` and `vllm:generation_tokens_total` flat for 60 s → restart the engine. [`08` §2.6](../scaling/08-reliability-and-operations.md) is the runbook. |
| **F8** | **Supabase outage** | handled correctly already: cached keys keep working, unknown keys get **503 + `Retry-After: 5`**, `usage_events` queue to disk with retry then `usage_failed.jsonl` | new keys only | **Keep it.** Extend the key cache TTL during a detected outage (serve stale for up to 15 min) and alarm on `usage_failed.jsonl` growth. Note Supabase's own SLA excludes *"Third-party vendors (AWS, Cloudflare, GCP, Azure, GitHub)"* [src](https://supabase.com/sla) — their outage can be our outage with no credit. |
| **F9** | **Certificate / TLS** | Caddy auto-renews; needs :80 and :443 reachable and DNS pointing at the EIP | total outage at expiry | **R-TLS**: move TLS to ACM on the ALB once there is >1 node (Caddy-per-node cannot renew behind an LB without shared storage). Alarm 14 days before expiry independently of Caddy. |
| **F10** | **Deploy rollout** | `install.sh` restarts both units in place; no canary, no drain | every in-flight request | **R-ROLLOUT**: replace-then-shift. New replica joins, passes a synthetic-inference health check and a parity check against a golden clip, takes traffic via `slow_start`, old replica drains (`deregistration_delay` ≥ longest request). [`08` §5.3](../scaling/08-reliability-and-operations.md), and `serve.sh`'s own warning that engine-version parity *"is the first thing to check on a new engine version"*. |
| **F11** | **`inflight` counter leak** | a client that disconnects before consuming an SSE stream may never run the generator's `finally`, so `inflight` never decrements; the box silently drops to a lower effective limit and eventually 429s everything | fleet, silently | **R-COUNTER**: admission state lives in the durable job record with a lease/heartbeat, not in a process-local integer. SQS's visibility-timeout model is the shape to copy — a lease that expires and returns the work, with *"a heartbeat mechanism to periodically extend the visibility timeout"* [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html). |
| **F12** | **Load-balancer idle timeout** | no ALB today; Caddy `read_timeout 600s` | every long request, after we add an ALB | **R-KEEPALIVE**: ALB `idle_timeout.timeout_seconds` 180 (range 1–4000) + SSE keepalives every 15 s + route clips >30 s to the job surface (§2.5). |
| **F13** | **Thundering herd after an outage** | constant `Retry-After: 2` on 429 and `5` on 503 | self-inflicted second outage | **R-JITTER**: `Retry-After = ceil(eta × U(1.0, 1.5))`, and on recovery admit at a ramped rate, not all at once. |
| **F14** | **Cost runaway** | no balance check; `credit_ledger` exists but the gateway never reads it | unbounded spend | **R-BUDGET**: 402 on exhausted balance, a fleet-wide max-replica ceiling, and a daily spend alarm (§5.5). |

---

## 5. Non-functional requirements

### 5.1 SSRF — the most urgent item in this document

Current behaviour, verbatim from [`gateway.py`](../../apps/infrx-api/gateway.py):

```python
elif url.startswith(("http://", "https://")):
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        data = r.content
```

Any authenticated caller can make the gateway issue an arbitrary GET **from
inside our VPC, with redirects followed**, and learn from the error message
whether it succeeded. The instance has an IAM role that can read SSM
SecureStrings including `/model-inference/supabase_service_role_key`. The EC2
Instance Metadata Service at `169.254.169.254` is the canonical target, and OWASP
lists it explicitly in the minimum blocklist alongside `127.0.0.0/8`, `::1/128`,
`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `224.0.0.0/4`
[src](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).
Note that vLLM performs its own fetch of the same URL, so **fixing only the
gateway is not enough** — R-FETCH (fetch once, hand the engine bytes) is a
security requirement as well as a performance one.

**R-SSRF**, following OWASP's cheat sheet:

1. Parse and validate the URL; **scheme allowlist `https` only** at launch
   (`http` is an option to revisit).
2. **Resolve the hostname ourselves** — the cheat sheet's wording, re-fetched
   2026-09-20, is *"retrieve all the IP addresses behind the domain name
   provided (taking records A + AAAA for IPv4 + IPv6)"* and then *"apply the
   same verification"* used for a literal IP (earlier drafts of this document
   paraphrased it as *"all A and AAAA records behind domain names"*) — and
   reject if **any** resolved address is loopback,
   link-local, private, CGNAT, multicast or reserved — v4 and v6.
3. **Connect to the validated IP**, pinning it, so the TOCTOU/DNS-rebinding
   window between validation and connection is closed. (The cheat sheet's
   rebinding guidance is to *"detect when any of them resolves to a/an: Local IP
   address (V4 + V6)"*.)
4. **Do not follow redirects**: *"Disable the support for the following of the
   redirection in your web client"*. If we must, follow at most 3 and re-validate
   every hop.
5. **Never echo the upstream error.** One generic `424 source_unavailable`, with
   detail only in our logs. Today the exception text is returned to the caller,
   which is the blind-SSRF oracle.
6. Egress security-group / NACL rules that deny the metadata range and RFC1918
   from the fetch path, as defence in depth. IMDSv2 (hop limit 1, tokens
   required) is necessary but **not sufficient** — a server-side fetcher can do a
   PUT for a token.
7. Optional, strongest: **a per-tenant origin allowlist**. OWASP is unambiguous:
   *"Deny-lists are bypass-prone. Prefer allow-lists"* (current wording,
   re-fetched 2026-09-20; the *"The allowlist approach is a viable option"*
   phrasing quoted in earlier drafts is not on the page today — the substance
   is unchanged and strengthened). Offer it as a key setting; enterprise
   customers will want it anyway.

### 5.2 Size, time and shape limits

| Limit | Today | Proposed | Why |
|---|---|---|---|
| Clip duration | 120 s (`MAX_VIDEO_SECONDS`) | keep | model cap (240 frames @ 2 fps) |
| File size | 64 MB (`MAX_VIDEO_MB`), **checked after buffering the whole body in RAM** | **stream with a hard byte counter; abort at 64 MB; prefer `Content-Length` pre-check → 413** | 16 concurrent × a multi-GB URL is an OOM on a 64 GiB box today |
| Request body (base64) | unbounded | 96 MB (64 MB × 1.37 base64 overhead + slack), enforced by Caddy/ALB and the gateway | base64 inflates by ~4/3 |
| Fetch time | 60 s total, unlimited redirects | connect 3 s, total 20 s, ≤3 redirects | F4 |
| `ffprobe` | 30 s | 10 s | F5 |
| Videos per request | 1 | keep | engine `--limit-mm-per-prompt '{"video":1,…}'` |
| `max_tokens` | unbounded → engine cap | cap at 2,048 | caption is ~200; anything much larger is abuse or a loop |
| Per-key in-flight | none | **per-tenant concurrency cap** (§5.4) | one tenant must not own the queue |

### 5.3 Privacy: no content retention

The existing commitment is strong and must be preserved verbatim:
*"No prompt or video content is stored anywhere; usage rows are metadata only"*
([`apps/README.md`](../../apps/README.md) §4), and the gateway's own note:
*"Nothing about the request body or response is stored."*

Everything this program adds threatens it. **R-PRIVACY:**

- A job record stores the **video URL or a content hash, never the bytes**, and
  the prompt **hash, never the text**. If the design needs bytes at rest for
  retry-after-node-loss, they live in S3 with **SSE-KMS, a ≤24 h lifecycle rule,
  and a per-tenant prefix** — and that fact is documented publicly before launch.
- A response cache (§ sibling document) is keyed by
  `sha256(video bytes) ‖ sha256(prompt) ‖ params` and is **per-org by default**.
  A global cache would let tenant B learn that tenant A processed a given clip —
  a cross-tenant oracle. Cross-org sharing is opt-in only.
- Temp files: the gateway already uses `NamedTemporaryFile` + `os.unlink` in a
  `finally`; keep that, and put the temp dir on instance-store NVMe (already the
  case) so nothing survives a stop.
- Logs must never contain URLs with signed-query credentials. Redact
  `?X-Amz-Signature=`, `?token=`, `?sig=` before logging.
- A ZDR-equivalent claim is table stakes for OpenRouter, whose provider document
  carries a `compliance: {"zdr": true|false}` field
  [src](https://openrouter.ai/docs/use-cases/for-providers). We should be able to
  set `zdr: true` truthfully.

### 5.4 Multi-tenant fairness

Rate limits alone are the wrong tool. VTC's framing is the one to adopt: *"To
ensure that all client requests are processed fairly, most major LLM inference
services have request rate limits, to ensure that no client can dominate the
request queue"* — and that approach *"causes resource under-utilisation and poor
user experience"* when spare capacity exists
[src](https://arxiv.org/abs/2401.00588). [`03` §4.7](../scaling/03-concurrency-and-admission-control.md)
covers VTC in depth.

**R-FAIR**, three layers:

1. **Per-tenant concurrency cap** — `min(tier_cap, ceil(fleet_capacity / active_tenants))`,
   floored at 1. A single tenant can use the whole fleet when nobody else wants
   it (work-conserving), and is squeezed within seconds when others arrive.
2. **Weighted-fair queue keyed by org**, with the weight being **video-seconds
   served** (§3.4), not request count. This is VTC with clip-seconds as the
   counter. Without it, a tenant sending 120 s clips gets 11.5× the service of one
   sending 10 s clips at the same request rate.
3. **Published quotas** as `x-ratelimit-*-video-seconds`, so a well-behaved client
   can pace itself. Per §2.6.

**The pilot exception.** With 10 tenants and 1 GPU, layer 1 alone is enough;
layers 2–3 matter at S2. Do not build the weighted queue before there is
contention to measure — but **record `org_id` on every queue entry from day one**
so the data exists when it is needed.

### 5.5 Cost ceiling

**R-COST**, four independent brakes, because any one of them can fail:

| Brake | Mechanism | Value |
|---|---|---|
| Per-org | `402 insufficient_credits` when `sum(credit_ledger) ≤ 0`, checked on the same 60 s cache as the key | hard |
| Per-org rate | `x-ratelimit-*-video-seconds`, per minute and per day | soft (429) |
| Fleet | ASG `MaxSize` — the number that stops a runaway scale-up | S1 **2**, S2 **12**, S3 **40** |
| Budget | AWS Budgets alarm + a daily `usage_events` cost-vs-spend reconciliation | alerting |

At the corrected $0.06–0.14 per video-hour (§3.8), plus a target gross margin,
**list price must be set from the 1080p column** — we do not control the
customer's source resolution, and a pre-transcode tier is an optimisation we
capture, not a discount we are obliged to pass on. ⚠️ The price in the `models`
table is a placeholder; setting it is a decision this document unblocks but does
not make.

---

## 6. Acceptance tests

Each test names the requirement it closes, the command, and the pass criterion.
Tests A1–A5 are prerequisites — they replace the ⚠️ assumptions this document is
built on. **Run A1–A5 before building anything else in this program.**

### Prerequisites — replace the assumptions

**A1 — measure the real request mix.** After 2 weeks of pilot traffic:
```sql
select
  count(*)                                              as n,
  avg(video_seconds)                                    as mean_clip_s,
  percentile_cont(0.5)  within group (order by video_seconds) as p50_clip_s,
  percentile_cont(0.97) within group (order by video_seconds) as p97_clip_s,
  avg(case when stream then 1.0 else 0 end)             as stream_share,
  avg(case when completion_tokens < 50 then 1.0 else 0 end) as find_share
from usage_events where created_at > now() - interval '14 days';
```
**Pass:** §1.2–§1.6's assumed distributions are replaced with measured ones, and
§3.5's GPU counts are recomputed. **Fail condition: the table is empty**, which
would mean requests are completing without a `usage_events` row (F-drop case (d)).

**A2 — re-benchmark with all-distinct clips.** `bench.py` with a corpus of ≥64
distinct clips spanning 10/30/60/120 s at 360p/720p/1080p, `-c 8`, and
`mm_processor_cache_gb=0` in one arm to isolate the cache.
**Pass:** clips/s within 20 % of the single-clip numbers, or the tables in §3.1
and §3.5 are revised downward. This discharges
[`notes.md` finding 5](../../models/marlin2b/results/notes.md)'s own caveat.

**A3 — pin the L40S usable memory.** `nvidia-smi --query-gpu=memory.total --format=csv`
on the box, and vLLM's reported KV blocks at boot.
**Pass:** §3.2's 48e9 is replaced with the real number and a
`research/gpus/l40s.md` stub exists so the rest of the repo can cite it by name.

**A4 — the `g6e.4xlarge` experiment.** Same AMI, same `serve.sh`, same corpus as
A2, on `g6e.4xlarge`.
**Pass:** if 1080p throughput ≥ 1.34× the `g6e.2xlarge` result, switch the launch
template — it is cheaper per clip. Record the number either way.

**A5 — quota and capacity check.**
```bash
aws service-quotas get-service-quota --service-code ec2 \
  --quota-code L-DB2E81BA --region us-east-1     # Running On-Demand G and VT instances (vCPUs) -- quota code ⚠️, see below
aws ec2 describe-instance-type-offerings --location-type availability-zone \
  --filters Name=instance-type,Values=g6e.2xlarge,g6e.4xlarge --region us-east-1
```
**Pass:** quota ≥ 400 vCPU (covers the S3 burst fleet with slack) and ≥2 AZs offer
g6e. Raise the quota and open an ODCR before the pilot if not.

⚠️ **The quota code `L-DB2E81BA` is unverified** (2026-09-20 fact-check). The
EC2 quota pages confirm the *shape* of the claim — *"there is a maximum number
of Amazon EC2 vCPUs that you can provision for On-Demand Instances in a
Region"* [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html)
— but neither that page nor
[Amazon EC2 endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/ec2-service.html)
publishes per-instance-family quota **codes**; both defer to the Service Quotas
console and to the separate *EC2 instance type quotas* page. If the command
above returns `NoSuchResourceException`, do not conclude the quota is zero —
list the codes first with
`aws service-quotas list-service-quotas --service-code ec2 --region us-east-1 \
  --query "Quotas[?contains(QuotaName,'G and VT')].[QuotaCode,QuotaName,Value]"`
and use whatever code that returns.

### Requirement tests

**A6 — R-DURABLE / D2 (kill the gateway mid-flight).** Send 32 concurrent 30 s
clips; at t=5 s `systemctl restart marlin2b-gateway`.
**Pass:** zero requests end without a terminal state. Every in-flight request
either completes, or returns a typed retryable error, or is retrievable via its
job id afterwards. **This test fails today, by construction.**

**A7 — R-N+1 / F2 (kill a node mid-flight).** Two replicas, steady 6 req/s;
`aws ec2 terminate-instances` on one.
**Pass:** no 5xx reaches a client; queued jobs on the dead node are re-dispatched
within 30 s; p99 latency stays inside the tier SLO for the remaining fleet.

**A8 — D1/D4/D5 (the burst).** Replay S3: 50 req/s for 300 s against the pilot
fleet.
**Pass:** (i) within `MEDIA_DECISION_S` = **2 s** every request holds either a
final status **or** a durable job id — a `202 + Location: /v1/jobs/{id}`, or an
SSE stream opened with `state:"preparing"` — and **every `preparing` record
reaches a terminal state** by the end of the run (none left hanging; see
[`09` §2.1](09-blueprint.md) for why the original "a status within 2 s" is
unsatisfiable on the cache-miss path, where the pre-admission media stage alone
runs to 90 s); (ii) every refusal carries `Retry-After` and a typed
`error.type` — `queue_full`, `rate_limit` or `media_stage_busy`; (iii) the
returned `X-Queue-Estimated-Wait-Seconds` is within **±30 %** of the actual wait
at p50 and never **understates** at p95; (iv) no request is accepted and then
abandoned.

**A9 — D6 (idempotency).** Same `Idempotency-Key`, sent twice concurrently and
once after completion.
**Pass:** one inference, one `usage_events` row, one charge; the second and third
calls return the stored status and body. Mismatched body with the same key → 400.

**A10 — R-SSRF.** Attempt `video_url` of `http://169.254.169.254/latest/meta-data/`,
`http://127.0.0.1:8000/v1/models`, `http://[::1]:8001/health`, a hostname whose A
record is `10.0.0.1`, and a public URL that 302s to `169.254.169.254`.
**Pass:** all five return an identical `424 source_unavailable` with no
distinguishing timing or body; nothing appears in the vLLM log; an alert fires.

**A11 — R-FETCH (fetch once).** One 1080p clip via public URL, packet-counted at
the instance.
**Pass:** the source is downloaded **once**, and TTFT is within 20 % of the
local-file TTFT for the same clip (0.77 s at c=1). Today: two downloads, 3.2 s.

**A12 — R-FETCH-BUDGET / F4 (slow origin).** Point 8 concurrent requests at an
origin that accepts the connection and sends 1 byte every 10 s.
**Pass:** each fails at 20 s with `424`; GPU admission slots are never occupied by
a downloading request; a ninth request to a fast origin is unaffected.

**A13 — R-POISON / F6.** Submit a crafted clip that reliably kills the decoder,
twice.
**Pass:** terminal `failed` with `poison_suspected` after ≤2 attempts; the content
hash is quarantined; no engine restart loop.

**A14 — R-LIVENESS / F7 (hung engine).** `SIGSTOP` the vLLM container.
**Pass:** the synthetic-inference health check fails within 30 s, the replica
leaves the target group, queued work re-dispatches. A plain `/health` ping would
pass — verify ours does not.

**A15 — F12 / R-KEEPALIVE (idle timeout).** Behind an ALB at
`idle_timeout.timeout_seconds` 180, a 120 s clip on the `interactive` surface with
a 90 s queue wait, both `stream: true` and `stream: false`.
**Pass:** streaming survives via keepalive comments; non-streaming is upgraded to
`202` per §2.5's decision rule rather than being cut at 180 s.

**A16 — R-ROLLOUT / F10 (zero-downtime deploy).** Rolling deploy under steady
6 req/s.
**Pass:** zero 5xx; a golden-clip parity check runs on the new replica before it
takes traffic and its caption events match the reference exactly
([`notes.md` finding 1](../../models/marlin2b/results/notes.md)).

**A17 — R-FAIR.** Tenant A floods with 120 s clips; tenant B sends 1 req/s of 10 s
clips.
**Pass:** B's p95 queue wait stays inside the interactive SLO; A's share of
**video-seconds served** (not requests) converges to its fair share within 60 s;
with B idle, A gets the whole fleet.

**A18 — R-COST / F14.** Set an org's `credit_ledger` sum to 0.
**Pass:** `402 insufficient_credits` within 60 s (the key-cache TTL); no GPU work
performed; the ASG `MaxSize` ceiling is never exceeded during A8.

**A19 — R-OBS.** Run A8, then reconcile.
**Pass:** `count(usage_events) + count(rejections) == count(arrivals)` exactly;
`queue_ms` is populated; the console's Usage page reconciles to the gateway's own
counters.

**A20 — error-budget wiring.** Inject a 10-minute 5 % error rate.
**Pass:** the 14.4×/1 h burn-rate alert pages, the 6× alert does not, and both
reset within 5 minutes of the injection stopping — the behaviour the SRE
workbook's multiwindow scheme is chosen for.

---

## Implications for our system

In order. Each names the file or component that changes.

1. **Fix the units error in `models/marlin2b/results/notes.md`.** *"57–129
   video-hours"* is *"15.9–36.2 video-hours"*; *"$0.02–0.04 per video-hour"* is
   **"$0.06–0.14"**. One-line edit, but every pricing conversation downstream
   depends on it. (§3.8)

2. **Close the SSRF in `apps/infrx-api/gateway.py:video_seconds`.** Scheme
   allowlist, resolve-then-pin, no redirects, generic `424`, streamed size check.
   This is the only item here that is a live security bug, and vLLM's independent
   fetch means it is not fixed until item 3 is done too. (§5.1)

3. **Fetch the video once, in `gateway.py`, and hand vLLM bytes** (a `data:` URL,
   or a path on a shared cache). Removes ~2.4 s of the measured 3.2 s remote
   TTFT, halves egress, and is prerequisite to item 2. (§1.5, §3.3)

4. **Bound the download before it is read.** Stream with a byte counter, reject
   at `Content-Length` when present. Today `r.content` buffers first and checks
   after — an OOM path. (§5.2)

5. **Replace `MAX_INFLIGHT` with a durable, deadline-bounded queue.** The Python
   integer becomes a job record with a lease; admission is by **video-seconds
   against a measured drain rate**, not by request count; `429 queue_full` carries
   a computed, jittered `Retry-After`. This is the core of the program and it
   changes `gateway.py`, adds a store, and adds `usage_events.queue_ms`. (§2.4,
   §3.4, §3.6)

6. **Add the `202` + job surface and `Prefer: wait`.** New routes
   `POST /v1/jobs`, `GET /v1/jobs/{id}`; the decision rule in §2.5 routes clips
   >30 s and busy-fleet requests to it. `apps/README.md` §7 and the console Docs
   page change with it. (§2.5)

7. **Emit SSE keepalive comments every 15 s while queued or prefilling.** Three
   lines in `gateway.py:gen()`, and the thing that makes long requests survive any
   load balancer. (§2.5, F12)

8. **Run A1–A5 before building 9–14.** They replace every ⚠️ this document's
   capacity numbers rest on, and A4 (`g6e.4xlarge`) may change the instance type
   the rest of the program is built on. (§6)

9. **Go to two replicas behind an ALB across two AZs.** Required for the
   queue-wait SLO (§3.6: p99 is 7.3 s at c=1 even at ρ=0.64), for F2, and for
   F10's rolling deploy. Brings `idle_timeout.timeout_seconds` 180, health-check
   interval 5 / healthy threshold 2, `slow_start` 60 s,
   `deregistration_delay` ≥ longest request, and TLS moving from Caddy to ACM.
   `apps/infrx-api/deploy/` gains a launch template and an ASG; the `Caddyfile`
   shrinks to a local reverse proxy or disappears. (§3.7, §4)

10. **Make health a synthetic inference, not a `/health` ping**, with a watchdog
    on `vllm:num_requests_running` vs `vllm:generation_tokens_total`. `gateway.py`
    `/health` and the target-group health check both change. (F7)

11. **Set `--max-num-seqs` to the measured sweet spot (≈8), not 32**, in
    `apps/infrx-api/deploy/marlin2b-vllm.service` — that unit's `ExecStart` is
    the *only* place the 32 is set; `serve.sh` does not set it (§3.2 correction),
    so edit the unit, not the script. KV allows 111–771 concurrent sequences on this box;
    prefill throughput allows ~8. The current 32 buys queueing latency, not
    throughput. (§3.2)

12. **Build the pre-transcode tier to ≤480p.** Upper bound 2.28× throughput, the
    largest single lever measured. It belongs on cheap CPU capacity, not on the
    GPU box whose vCPUs are the bottleneck. (§3.3)

13. **Record `org_id`, `queue_ms` and rejections on every arrival.** Fairness
    (§5.4) and the admission-success SLI (§2.2) are both unmeasurable without it,
    and a rejection currently leaves no trace at all. `usage_events` gains
    columns; `apps/README.md` §6 changes.

14. **Add `402 insufficient_credits` and an ASG `MaxSize` ceiling.** There is no
    spend protection of any kind today. (§5.5, F14)

15. **Publish the SLO table and the error taxonomy in the console Docs page**
    before the first external key is issued, and set OpenRouter's `capacity`
    number from §3.5 — not aspirationally. (§2.3, §2.6, §1.1)

---

## Open questions

⚠️ Consolidated. Each is load-bearing for at least one number above.

1. **Is the measured throughput an artefact of the multimodal processor cache?**
   Every clips/s figure comes from runs that reused one clip, and
   `mm_processor_cache_gb` defaults to 4 GiB. Closes with **A2**. If it is, §3.1,
   §3.5 and §3.8 all move against us. *(Highest priority — it is the foundation
   of every capacity number here.)*

2. **What is the throughput of a 120 s clip?** Everything above extrapolates
   linearly in video-seconds from 10 s clips. At 120 s the prompt is 11.5× larger,
   KV per sequence 11.5×, and CPU decode 12× — there is no reason to expect
   linearity, and [`notes.md`](../../models/marlin2b/results/notes.md) lists it as
   its own open item. Closes with **A2**.

3. **Does throughput scale with vCPU?** The whole CPU-bound thesis predicts
   `g6e.4xlarge` is **~1.49×** cheaper per clip (corrected 2026-09-20 from
   ~1.7×; see §3.3). Unmeasured. Closes with **A4**.

4. **L40S usable memory and the real KV block count.** §3.2 uses nominal 48e9;
   there is no `research/gpus/l40s.md` in this repo. Closes with **A3**.

5. **Does GPU video decode (`pynvvideocodec`) work on L40S with this vLLM image?**
   The *"more than double[s] the throughput"* figure is from 8×H100 on a different
   workload. If it works here it competes directly with item 12 above.

6. ~~**Does OpenRouter count 429 against provider uptime?**~~ **CLOSED
   2026-09-20: no.** The for-providers page lists the errors that affect uptime
   (401, 402, 404, 500+, mid-stream errors, error finish reasons) and 429 is not
   among them; it further instructs providers to *"Return early 429s if under
   load, rather than queueing requests"*
   [src](https://openrouter.ai/docs/use-cases/for-providers). The residual
   question is narrower and is **evaluative, not availability**: how much 429
   volume starts to starve OpenRouter's benchmark sampling (*"Consistent rate
   limiting (429s) can reduce the volume of successful requests available for
   evaluation"*), which is unquantified on the page. Set the published
   `capacity` conservatively for that reason. See the corrected §1.1.

7. **What is our actual EC2 G-instance vCPU quota, and which AZs offer g6e?**
   The S3 fleet needs 232–368 vCPU. Closes with **A5**.

8. **Can a GPU instance with a loaded CUDA context hibernate into a warm pool?**
   AWS documents warm-pool hibernation generally but says nothing about
   accelerators. If yes, the 165 s vLLM boot mostly disappears and the burst math
   in §3.7 changes materially. (§3.7)

9. **What is the real end-to-end scale-up lead time on our AMI?** Two of the five
   stages in §3.7 (`RunInstances`→SSH, weights from NVMe) are `est.` This is the
   same gap [`06` OQ1](../scaling/06-cold-start.md) flags repo-wide: **no model in
   this repo has a measured end-to-end cold start.**

10. **The real request mix.** Operation split, clip-length distribution, source
    resolution distribution, streaming share, URL-vs-base64 share — every one is
    an assumption in §1. Closes with **A1**.

11. **What is the fair-share unit customers will accept?** We propose
    video-seconds. If the market prices in tokens (because everything else does),
    the meter and the `x-ratelimit-*` headers change shape. (§3.4, §5.4)

12. **Response-cache hit rate.** At temperature 0 the model is deterministic, so a
    `(video hash, prompt, params)` cache is exact — but the hit rate on real
    traffic is unknown, and a per-org cache (required by §5.3) will hit less often
    than a global one. Decides whether caching is a rounding error or a tier.

13. **Is `expired` billable?** We adopt Anthropic's "not billed" stance in §2.4,
    but a clip that consumed GPU work before being abandoned has a real cost. The
    commercially honest answer may be "bill partial work, cap at the quoted
    price"; it is a pricing decision, not a technical one.

---

## Sources

Fetched and read on **2026-09-20** unless noted. Every URL below was retrieved in
full (WebFetch or `curl`) for this document.

**Method note.** This session's WebSearch budget (200 queries) was already
exhausted by earlier agents in the program before this document began, so the
brief's "≥15 distinct WebSearch queries" could not be executed. Every source
below was instead reached **directly by URL and fetched in full**, which is the
stronger half of the requirement (primary documents, not search snippets); the
weaker half — discovering sources we did not already know to look for — was not
possible. Items in **Open questions** 5, 6 and 8 are the ones most likely to have
been closed by search and were not.

**HTTP and API semantics**
- RFC 9110, HTTP Semantics — §15.3.3 (202 Accepted), §15.6.4 (503), §10.2.3 (Retry-After). https://www.rfc-editor.org/rfc/rfc9110.txt
- RFC 6585 §4, 429 Too Many Requests. https://www.rfc-editor.org/rfc/rfc6585.txt
- Google AIP-151, Long-running operations (the "10 seconds" rule of thumb). https://google.aip.dev/151
- Stripe, Idempotent requests (key semantics, 24 h retention, 255 chars). https://docs.stripe.com/api/idempotent_requests
- OpenAI, Rate limits (header names, 429/`slow_down`, 503/`server_is_overloaded`, backoff advice). https://developers.openai.com/api/docs/guides/rate-limits
- Replicate, Create a prediction (`Prefer: wait`, 60 s default, webhooks, polling). https://replicate.com/docs/topics/predictions/create-a-prediction
- Anthropic, Message Batches API (24 h expiry, `expired` unbilled, 100,000 requests / 256 MB, 29 d results, 50 % discount). https://platform.claude.com/docs/en/build-with-claude/batch-processing

**AWS**
- ALB attributes — connection idle timeout (default 60 s, range 1–4000), HTTP client keepalive (default 3600 s, range 60–604800), no HTTP/2 PING. https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html
- ALB target group health checks — defaults (interval 30 s, timeout 5 s, healthy 5, unhealthy 2, matcher 200). https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html
- ALB target groups — `deregistration_delay.timeout_seconds` 300 s, `slow_start.duration_seconds` 30–900 (default 0), `least_outstanding_requests`. https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html
- EC2 Auto Scaling warm pools — Stopped/Running/Hibernated, `MaxGroupPreparedCapacity`, `MinSize`, instance reuse policy, AZ-out-of-capacity cold starts. https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html
- EC2 Auto Scaling target tracking — instance warmup, scale-in conservatism, metric guidance. https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html
- EC2 On-Demand Capacity Reservations — AZ-scoped, no term commitment, no billing discount, count against On-Demand limits. https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html
- EC2 Capacity Blocks for ML — supported instance types (no g6e), 8-week horizon, 64/256 instance caps. https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html
- EC2 service quotas — per-Region vCPU quotas for On-Demand Instances. https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html
- Amazon SQS visibility timeout — default 30 s, 12 h max, heartbeat extension, DLQ for poison messages, ~120,000 in-flight limit. https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html
- SageMaker Asynchronous Inference — queue + poll + SNS, 1 GB payload, 1 h processing, scale to zero. https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html
- Amazon EC2 G6e instances — family table (g6e.2xlarge: 1×L40S 48 GB, 8 vCPU, 64 GiB, 450 GB NVMe). https://aws.amazon.com/ec2/instance-types/g6e/
- AWS Price List, EC2 us-east-1, publication 2026-09-18 — `BoxUsage:g6e.*` Shared/Linux on-demand rates used throughout §3.8–3.9. https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.csv

**Engine and serving**
- vLLM metrics design — `vllm:request_queue_time_seconds`, `vllm:time_to_first_token_seconds`, `vllm:request_time_per_output_token_seconds`, `vllm:e2e_request_latency_seconds`, `vllm:num_requests_running`/`_waiting`, `vllm:kv_cache_usage_perc`, `vllm:prefix_cache_queries`/`_hits`. https://docs.vllm.ai/en/latest/design/metrics.html
- vLLM optimization guide — chunked prefill on by default, `max_num_batched_tokens` TTFT/ITL trade-off, `mm_processor_cache_gb` default 4 GiB, `mm_encoder_tp_mode`, RECOMPUTE preemption. https://docs.vllm.ai/en/latest/configuration/optimization.html
- vLLM engine arguments — `--gpu-memory-utilization` default 0.92, `--limit-mm-per-prompt`, `--max-model-len`, `--long-prefill-token-threshold`. https://docs.vllm.ai/en/latest/configuration/engine_args.html
- Caddy `reverse_proxy` — `flush_interval` (-1 disables buffering), transport timeouts (`dial_timeout` 3s; others no default), active/passive health checks, `lb_try_duration`/`lb_try_interval` (250ms). https://caddyserver.com/docs/caddyfile/directives/reverse_proxy

**SLOs, fairness, marketplace**
- Google SRE Book, Service Level Objectives — SLI/SLO/SLA/error-budget definitions, percentile guidance, "start with a loose target". https://sre.google/sre-book/service-level-objectives/
- Google SRE Workbook, Alerting on SLOs — multiwindow multi-burn-rate table (14.4×/1 h/2 %, 6×/6 h/5 %, 1×/3 d/10 %; short window = 1/12 long). https://sre.google/workbook/alerting-on-slos/
- Zheng et al., *Fairness in Serving Large Language Models* (VTC). https://arxiv.org/abs/2401.00588
- OWASP, SSRF Prevention Cheat Sheet — allowlist preference, resolve-all-records validation, minimum blocklist incl. 169.254.169.254, disable redirects. https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- OpenRouter, For providers — model document shape, `capacity` entries, `compliance.zdr`. https://openrouter.ai/docs/use-cases/for-providers
- OpenRouter, Uptime optimization — continuous health monitoring, failover, ≥95 %/≥85 % uptime bands. https://openrouter.ai/docs/features/uptime-optimization
- Supabase SLA — 99.9 % monthly for Enterprise, third-party (AWS/Cloudflare) exclusions, credit schedule. https://supabase.com/sla

**This repo (measurements and specs, not external sources)**
- [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) — the 2026-09-19 L40S measurements; findings 1–8; the cost sketch corrected in §3.8.
- [`models/marlin2b/README.md`](../../models/marlin2b/README.md) — box layout, public endpoint, video budget, results table, the 2026-09-20 remote-call timing.
- [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh) — engine flags in production.
- [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) and [`apps/infrx-api/README.md`](../../apps/infrx-api/README.md) — auth cache, video budget, `MAX_INFLIGHT`, usage ingestion, the fetch path analysed in §1.5 and §5.1.
- [`apps/infrx-api/deploy/`](../../apps/infrx-api/deploy/) — systemd units, Caddyfile, install.sh.
- [`apps/README.md`](../../apps/README.md) — console + gateway spec, data model, non-functional requirements including the no-content-retention commitment.
- [`research/models/marlin2b/b300.md`](../models/marlin2b/b300.md) — 12,288 B/token BF16 KV, 19,537,920 B GDN state, 4.426 GB resident weights, and the `max_concurrency` formula re-applied to L40S in §3.2.
- [`research/scaling/03`](../scaling/03-concurrency-and-admission-control.md), [`05`](../scaling/05-autoscaling-and-predictive-scaling.md), [`06`](../scaling/06-cold-start.md), [`08`](../scaling/08-reliability-and-operations.md), [`10`](../scaling/10-blueprint.md), [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md), [`cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) — linked throughout; not re-derived here.

---

## Verification log (2026-09-20)

Adversarial fact-check of this document. Method: the 25 most consequential
claims were selected across AWS service limits/quotas/timeouts/prices, vLLM
flags and metric names, engine and paper citations, arithmetic, and statements
about this repo's own code and measurements. Every external claim was checked
against its **primary source opened in full** (WebFetch, or `curl` for the RFC
and the price-list CSV — no search snippets); every repo claim was checked by
opening the file; every derivation was recomputed with `python3`. 31 claims
resolved: **23 CONFIRMED, 4 CORRECTED, 4 UNVERIFIABLE** (marked ⚠️ in place).

Note on method: this session's WebSearch budget was exhausted before the check
began — the same constraint this document's own **Sources → Method note**
records. Every verdict below therefore rests on a document reached directly by
URL. The one claim that would have been settled fastest by search (the EC2
quota code, U3) is the one left unverifiable.

### CORRECTED — 4

| # | § | Was | Is | Source |
|---|---|---|---|---|
| **C1** | **§1.1**, Open question 6 | *"A 429 storm … is a **demotion in a marketplace**, which is a commercial reason to queue rather than reject"*, plus a ⚠️ that the uptime page *"does not say"* whether 429 counts against uptime | **The provider document says the opposite.** *"Return early 429s if under load, rather than queueing requests."* The errors that affect uptime are *"Authentication issues (401), Payment failures (402), Model not found (404), All server errors (500+), Mid-stream errors, Successful requests with error finish reasons"* — **429 is not among them.** The only cost named is evaluative: *"Consistent rate limiting (429s) can reduce the volume of successful requests available for evaluation."* | [openrouter.ai/docs/use-cases/for-providers](https://openrouter.ai/docs/use-cases/for-providers) |
| **C2** | **§3.3** item 3, Open question 3 | `g6e.4xlarge` is a *"**1.7× improvement in $/clip**"* | **1.49×.** Under the line's own premise (throughput linear in vCPU, so 2×) at +34 % price: `2 / 1.34 = 1.493`. Per clip $0.0003967 → $0.0002658. **1.7 is `2.28 / 1.34`** — it substitutes the *resolution* ratio (§1.4) for the *vCPU* ratio. Break-even unchanged at 1.34×. | recomputed, `python3` |
| **C3** | **§3.2**, Implications 11 | *"`serve.sh` currently passes `--max-num-seqs 32`"* | **It does not.** `serve.sh` sets no `--max-num-seqs`; the flag is in the systemd unit's `ExecStart` (`apps/infrx-api/deploy/marlin2b-vllm.service`). **The fix is to the unit file**; editing `serve.sh` would change nothing on the running box. `openrouter/PLAN.md:63` carries the same 32. | repo files opened |
| **C4** | **§5.1** items 2 and 7 | OWASP quoted as *"all A and AAAA records behind domain names"* and *"The allowlist approach is a viable option"* | Neither is verbatim on the current cheat sheet. Actual: *"retrieve all the IP addresses behind the domain name provided (taking records A + AAAA for IPv4 + IPv6)"* and *"Deny-lists are bypass-prone. Prefer allow-lists."* Substance unchanged (and item 7 is strengthened). | [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) |

**C1 is the consequential one.** It does not overturn the program: §2.4's
definition of a drop, and the case for queueing populations 1 and 2, never
rested on OpenRouter. It does invert the *stated* justification and it changes a
design parameter — see §1.1, where the queue deadline becomes a per-tier
property and an OpenRouter-fronted key is pinned to `interactive`.

### UNVERIFIABLE — 4, each now marked ⚠️ in place

| # | § | Claim | Why it did not resolve |
|---|---|---|---|
| **U1** | §2.3, §3.7 | vLLM boot *"~2-3 min incl. torch.compile"*, labelled **`meas.`** and worth **165 s of the 438 s** scale-up budget | **The string appears nowhere in this repo.** Not in `models/marlin2b/*`, `apps/infrx-api/*`, `results/notes.md` or the deploy units. Nearest: [`06` §8](../scaling/06-cold-start.md)'s ~2–3 min for **DeepSeek-V4.1-Flash TP4 on B300**, and [`05` §4.5](../scaling/05-autoscaling-and-predictive-scaling.md)'s *"no model in this repo boots in 2 minutes"*. Relabelled `est.`; the 438 s total is therefore `est.` end to end. Closes with A3. |
| **U2** | §3.9 | *"only `us-east-1d` had capacity, others returned `InsufficientInstanceCapacity`"* — load-bearing for F3/R-CAPACITY | No capacity survey in the repo. `CLAUDE.md` records only that the dev box runs in `us-east-1d` — consistent, not probative. A5 answers AZ *coverage*; the *refusal* half needs a launch attempt per AZ. |
| **U3** | §6 A5 | quota code `L-DB2E81BA` | Confirmed the *shape* (*"there is a maximum number of Amazon EC2 vCPUs that you can provision for On-Demand Instances in a Region"*) but **no AWS page publishes per-family quota codes** — both `ec2-resource-limits` and `general/latest/gr/ec2-service` defer to the console. A `list-service-quotas` fallback is added to A5. |
| **U4** | §2.5 | `Prefer: wait=<seconds>` *"(max 300)"* adopted *"verbatim"* from Replicate | Replicate documents the **60 s default** and the `wait=X` syntax but **states no maximum**. The 300 is ours, not Replicate's; the word "verbatim" overreaches on that one parameter. |

### CONFIRMED — 23

**AWS (9).** Every figure exact.
- ALB **connection idle timeout default 60 s, valid range 1–4000 s**; the *"period of time an existing client or target connection can remain inactive"* and *"send at least 1 byte of data before each idle timeout period elapses"* quotes are verbatim; *"Application Load Balancers do not support HTTP/2 PING frames. These do not reset the connection idle timeout"* verbatim. [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html)
- ALB target-group health-check defaults: **interval 30 s, timeout 5 s, healthy 5, unhealthy 2, matcher 200** — all four plus the matcher exact, so §3.7's **150 s** (5 × 30) and the **10 s** optimised figure (2 × 5) both hold. [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html)
- `deregistration_delay.timeout_seconds` **default 300 s**; `slow_start.duration_seconds` **range 30–900, default 0 (disabled)**. [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html)
- **g6e family table** — every row in §3.9 matches AWS: `2xlarge` 1×L40S 48 GB / 8 vCPU / 64 GiB / 450 GB / up to 20 Gb; `4xlarge` 16/128/600/20; `8xlarge` 32/256/900/25; `16xlarge` 64/512/1900/35; `12xlarge` 4 GPU/48/384/3800/100; `24xlarge` 4/96/768/3800/200; `48xlarge` 8/192/1536/7600/400. [src](https://aws.amazon.com/ec2/instance-types/g6e/)
- **Price List, us-east-1, Shared/Linux `BoxUsage:g6e.*`, publication `2026-09-18T21:27:57Z`** — all seven rates exact to five decimals: 2xl **2.24208**, 4xl **3.00424**, 8xl **4.52856**, 12xl **10.49264**, 16xl **7.57719**, 24xl **15.06559**, 48xl **30.13118**. (CSV pulled with `curl`, 303 MB.)
- **Capacity Blocks for ML**: supported list is `p6-b300.48xlarge`, `p6-b200.48xlarge`, `p5.4xlarge`, `p5.48xlarge`, `p5e`, `p5en`, `p4d`, `p4de`, `trn1`, `trn2` — **no `g6e`, no G family at all**; 8-week horizon and 64/256 instance caps confirmed. [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)
- **ODCR**: *"reserve compute capacity … in a specific Availability Zone for any duration"*, *"there is no term commitment"*, *"No billing discount"*, *"Active and unused Capacity Reservations count toward your On-Demand Instance limits"* — all four verbatim. [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html)
- **Warm pools**: *"pre-initialized EC2 instances"*; `Stopped`/`Running`/`Hibernated`; the *"effective way to minimize costs"* and RAM-to-EBS quotes; and F3's *"If your warm pool is depleted … a cold start. You could also experience cold starts if an Availability Zone is out of capacity"* — all verbatim. **§3.7's ⚠️ on GPU hibernation is correct and well-placed**: the page's hibernation prerequisites say nothing about accelerators. [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html)
- **SQS** (30 s default, 12 h max, *"heartbeat mechanism to periodically extend the visibility timeout"*, the DLQ *"preventing them from repeatedly circulating in the main queue"* quote, ~120,000 in-flight) and **SageMaker Asynchronous Inference** (*"queues incoming requests … large payload sizes (up to 1GB), long processing times (up to one hour) … autoscaling the instance count to zero"*) — both verbatim. [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html), [src](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html)

**Engine (4).**
- `mm_processor_cache_gb` **default 4 GiB** — so §3.1's caveat and A2 stand. Chunked prefill *"enabled by default whenever possible"* in V1. [src](https://docs.vllm.ai/en/latest/configuration/optimization.html)
- `--gpu-memory-utilization` **default 0.92** — and the bench's `0.90` is `serve.sh`'s explicit `${GPU_MEM:-0.90}`, so §3.2's formula uses the right number. [src](https://docs.vllm.ai/en/latest/configuration/engine_args.html)
- **All nine vLLM metric names** cited in §2.2, F7 and Sources exist verbatim: `vllm:request_queue_time_seconds`, `vllm:time_to_first_token_seconds`, `vllm:num_requests_running`, `vllm:generation_tokens_total`, `vllm:kv_cache_usage_perc`, `vllm:prefix_cache_queries`, `vllm:prefix_cache_hits`, `vllm:e2e_request_latency_seconds`, `vllm:request_time_per_output_token_seconds`. [src](https://docs.vllm.ai/en/latest/design/metrics.html)
- The **pynvvideocodec blog exists** and says *"at 8xH100, GPU-based video decoding provides more than double the throughput compared to the CPU-based video decoder"*, with the `--media-io-kwargs '{"video":{"backend":"pynvvideocodec"}}'` flag. **§3.3 item 4's ⚠️ is correct**: the measurement is 8×H100, not L40S. [src](https://vllm.ai/blog/2026-09-18-pynvvideocodec)
- Caddy `flush_interval` −1: current wording is *"low-latency mode … disables response buffering completely and flushes immediately after each write to the client"*; §2.5's bracketed *"disable[s] buffering entirely and flush[es] after each write"* is a faithful alteration. [src](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

**Standards and vendors (6).** `curl`'d the RFCs rather than trusting a rendering.
- **RFC 9110 §15.3.3** — *"There is no facility in HTTP for re-sending a status code from an asynchronous operation"* (line 6987) and *"The 202 response is intentionally noncommittal"* (line 6991) are both present and both in §15.3.3. **§10.2.3** Retry-After and **§15.6.4** 503 *"The server MAY send a Retry-After header field … to suggest an appropriate amount of time"* verbatim. All three section numbers correct.
- **RFC 6585 §4** — *"The response representations SHOULD include details explaining the condition, and MAY include a Retry-After header"* verbatim.
- **Stripe** — the status-code/body quote including *"including `500` errors"*, *"Idempotency keys are up to 255 characters long"*, *"remove keys from the system automatically after they're at least 24 hours old"*, and the different-parameters error. D6 is sound. [src](https://docs.stripe.com/api/idempotent_requests)
- **AIP-151** — *"A good rule of thumb is 10 seconds"* verbatim.
- **Anthropic Message Batches** — *"Batches expire if processing does not complete within 24 hours"*; `expired` = *"Batch reached its 24-hour expiration … You will not be billed for these requests"*; 100,000 / 256 MB; results 29 days; 50 % discount. D3's borrowed vocabulary is accurate.
- **Supabase SLA** 99.9 % monthly, Enterprise only, excluding *"AWS, Cloudflare, GCP, Azure, GitHub"* — so §2.3's and F8's use of it is fair. **SRE workbook** table exact (14.4×/1 h/2 %, 6×/6 h/5 %, 1×/3 d/10 %) with *"make the short window 1/12 the duration of the long window"*. **OpenAI** header names all six present, and *"Follow `Retry-After` when it's present, reduce your request rate, and then increase it gradually"* is verbatim, as are `slow_down` and `server_is_overloaded`. **VTC** (arXiv 2401.00588, *Fairness in Serving Large Language Models*, Sheng, Cao, Li, Zhu, Li, Zhuo, Gonzalez, Stoica) — the rate-limit sentence quoted in §5.4 is in the abstract. **OpenRouter uptime** bands ≥95 % green / ≥85 % yellow and *"continuously monitors the health and availability"* — verbatim (it is the *inference drawn from them* in §1.1 that C1 corrects, not the quotes).

**Arithmetic (recomputed with `python3`) — every table reproduces.**
- §3.2 `max_concurrency` — numerator `0.90 × 48e9 − 4.426e9 − 4 GiB = 34.479 e9`; **771 / 493 / 286 / 156 / 111 / 81** all exact.
- §1.3 token function — 20 f → 2,060; 60 → 5,980; 120 → 11,860; 240 → **23,620**. The `meas.` 2,061 vs formula 2,060 is a 1-token rounding, so *"the formula is exact"* holds. Derived spreads check: 11.5× (`23,620/2,060`), 5.4× (`23.6/4.4`), mean 4.4 K ↔ a 22 s mean clip.
- §2.1 s-per-clip-minute **11.9 / 13.3 / 30.3**; Little's-Law cross-check `0.77 + 200 × 6 ms = 1.97` vs `2.00`.
- §1.2 output tokens 197–212 (`100/0.50`, `310/1.57`, `760/3.58`).
- §3.4/§3.8 — 15.857 and 36.158 video-s/s; **57,085** and **130,169** video-s per GPU-hour; **$0.1414 / $0.0620** at 100 %, **$0.2357 / $0.1033** at 60 %; `$2.01/1M` output (`2.24208 / 1.116`). **The §3.8 correction to `notes.md` is itself correct**: `notes.md` says *"57–129 video-hours → $0.02–0.04"*; 57,085/1,000 = 57.1 and 130,169/1,000 = 130.2, so the ÷1,000-instead-of-÷3,600 diagnosis is exactly right and the 3.6× understatement stands.
- §3.5 — harmonic blend **2.1828**; all 18 GPU cells reproduce (S2 10/8, 7/6, 4/4; S3 46/40, 33/29, 20/18). Cost rows on a 30-day month (now stated in place).
- §3.6 **Erlang-C — all seven rows reproduce to the printed precision**, including the load-bearing first row (λ=1, c=1: ρ 0.637, P(wait) 0.637, E[Wq] 1.12 s, p95 4.46 s, **p99 7.29 s**) and the λ=50/c=40 row (0.115, 0.01, 0.06, 0.19). The **N=2-is-a-latency-requirement** conclusion is sound.
- §3.7 burst — 471 / 1,884 / 3,768 / 7,536 served and 14,529 / 13,116 / 11,232 / 7,464 backlog exact; residual drains 7.08 h / 41.4 min / 16.2 min / 5.2 min; 29-GPU row clears at 18,966 (2.18 × 29 × 300); **438 s stage sum**, **21,900 arrivals**, **265 s optimised** all correct.
- §2.7 error budgets — 99.5 % → **216.0 min/30 d** and **50.4 min/7 d**; 99.9 % → 43.2; 99.0 % → 432; 0.1 % of 14.3 M = **14,300**.
- §1.7 — 5.5 req/s × 86,400 = **475,200/day**; × 30 = **14.26 M ≈ 14.3 M**.

**This repo (4).** Opened and matched: `gateway.py` — `MAX_INFLIGHT = 16`, `httpx.AsyncClient(timeout=60, follow_redirects=True)` with `data = r.content` **then** the size check (§5.2's OOM path, confirmed), `ffprobe … timeout=30`, *"one video per request"*, 429-before-any-record, 503-on-Supabase-down. `results/notes.md` — findings 4–7 and the *"57–129 video-hours … $0.02–0.04"* cost sketch, verbatim as quoted. `models/marlin2b/README.md` — all four measured rows (0.50/1.57/1.47/3.58 clips/s, TTFT 0.77/3.35/3.70/0.66) and the *"~3.8 s end to end (TTFT ~3.2 s … twice, gateway and vLLM)"* remote timing. `research/models/marlin2b/b300.md` §1 — **12,288 B/token BF16 KV, 19,537,920 B GDN state at S = 1, 4.426 GB resident weights**, which is what §3.2 re-applies to the L40S. `apps/README.md` §4 — the no-content-retention commitment quoted in §5.3.

### Not re-derived

§3.2's **48e9 nominal L40S HBM** is left as the document already has it: flagged
⚠️ with no `research/gpus/l40s.md` to pin it, closing with A3. This is the one
`⚠️` in the capacity chain that the check could not reduce, because it needs
`nvidia-smi` on the box, not a source.

### Post-check amendments

- **2026-09-20** — §3.7's *"Keep N+1 warm"* bullet annotated as superseded at
  pilot scale by [`09` §0.2 R6](09-blueprint.md); the *"~30 s"* absorbed window
  is recomputed as 90–170 s in [`09` §3.4a](09-blueprint.md). No number in this
  document changed; §3.7's burst table and 438 s / 265 s stage budgets stand.
- **2026-09-20** — Gap G5: A8's pass criterion (i) restated from *"every request
  gets a status within 2 s"* to *"a final status **or** a durable job id within
  `MEDIA_DECISION_S` = 2 s, and every `preparing` record reaches a terminal
  state"*, and (ii) widened to any typed refusal code. Reason: the criterion as
  written is unsatisfiable on the cache-miss path, where the pre-admission media
  stage runs `FETCH_TIMEOUT_S + PROBE_TIMEOUT_S + TRANSCODE_TIMEOUT_S = 20 + 10 +
  max(15, 0.5×duration)` = **90 s** for a 120 s clip before `eta_s` exists.
  D1 restated to match in [`09` §2.1](09-blueprint.md); mechanism in
  [`10` §4.2, §5, §6](10-implementation-spec.md). No traffic-model number in this
  document changed.

### Implementation-plan amendment log — 2026-09-20

Documentation reconciliation only: incorporated the unified plan and review corrections above. Historical measurements and previous verification entries remain unchanged; new implementation/live tests are pending.
