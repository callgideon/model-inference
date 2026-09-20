# No-drop request handling: admission, queueing, backpressure, streaming

**Research date: 2026-09-20.** Every source below was fetched on this date.
Prices, quotas and API surfaces move; re-pin before you build.

## 0. Scope, and what this document does not repeat

This is the **request-path** design for taking `https://marlin2b.callbill.ai`
from one hand-made key to real paying users, on AWS, portable to the bare-metal
cluster later. It covers the contract the client sees, the queue behind it, the
backpressure signal that sizes the queue, the CPU stage that is actually our
bottleneck, retries, routing across more than one worker, and a concrete v2 of
[`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py).

It **links rather than repeats**:

| For | Read |
|---|---|
| Engine-internal queues, `max_num_seqs` / `max_num_batched_tokens` derivation, preemption, chunked prefill, SLO-aware scheduling literature (Sarathi-Serve, DistServe, Llumnix, Andes, VTC), status-code semantics, circuit breakers | [`research/scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md) |
| When to add a replica, signal choice, KEDA/Dynamo Planner/Ray Serve mechanisms, predictive scaling, scale-down thrash | [`research/scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md) |
| Boot, weight load, `torch.compile` cache, warm pools, snapshot/restore | [`research/scaling/06-cold-start.md`](../scaling/06-cold-start.md) |
| SLOs, failure catalogue, draining, observability planes | [`research/scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md) |
| The assembled bare-metal platform | [`research/scaling/10-blueprint.md`](../scaling/10-blueprint.md) |
| Prefix/KV caching, multimodal encoder cost, video token budgets | [`research/cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) |
| GPU instance pricing | [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) |

**Legend** (from [`research/METHODOLOGY.md`](../METHODOLOGY.md)): `[src]` + URL =
primary document; **⚠️ TO BE VERIFIED** = estimate, method stated inline;
`est.` = derived from stated formulas; `meas.` = measured in this repo, cited.

### 0.1 The system as it stands, 2026-09-20

One `g6e.2xlarge` in `us-east-1d` (1× L40S 48 GB, **8 vCPU**), Elastic IP,
Caddy :443 → `gateway.py` :8001 → vLLM :8000. Measured on this box
([`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md),
2026-09-19):

| c | source clip | TTFT p50 | clips/s | derived service time W = c/X |
|---|---|---|---|---|
| 1 | 1080p, 5.5 MB | 0.77 s | 0.50 | 2.00 s (est.) |
| 8 | 1080p, 5.5 MB | 3.35 s | 1.57 | 5.10 s (est.) |
| 8 | 360p, 1 MB | 0.66 s | 3.58 | 2.23 s (est.) |

The whole gap between the last two rows is **per-request video download and
decode on 8 vCPUs**, not the LM: same ~2K prompt tokens, 2.3× the throughput.
Over the internet a 10 s clip takes ~3.8 s end to end with TTFT ~3.2 s because
the 5.5 MB source is downloaded and decoded **twice** — once by `gateway.py`'s
`video_seconds()` for the ffprobe budget, once by vLLM
([`models/marlin2b/README.md`](../../models/marlin2b/README.md), meas.
2026-09-20).

Today the gateway's entire overload policy is three lines:

```python
if inflight >= MAX_INFLIGHT:          # MAX_INFLIGHT = 16
    return JSONResponse({"error": {...}}, status_code=429,
                        headers={"Retry-After": "2"})
```

That is a **drop**. This document replaces it.

### 0.2 TL;DR decision rules

1. **Offer both contracts.** Synchronous `POST /v1/chat/completions` with SSE
   keep-alive comments for an *estimated* wait ≤ `SYNC_MAX_WAIT` (start at
   **30 s**); `202 + job id` on `POST /v1/jobs` beyond that, and always
   available explicitly via `X-Infrx-Mode: async`. Never 429 a request that
   would have fitted in the async contract — redirect it there.
2. **Queue at the gateway, not in the engine.** Hold surplus requests centrally
   so they can still be routed to whichever worker frees up first, and so
   fairness and priority are enforceable. This is exactly llm-d's "no-regret
   scheduling" [src](https://llm-d.ai/docs/well-lit-paths/foundations/flow-control).
3. **Three limits, not one** (llm-d's 3-layer model, §3.2): engine
   `--max-num-seqs` = N; gateway dispatch gate per worker = N + B; central
   burst queue = hundreds.
4. **Size the queue from measured service time, not from a guess.**
   `MAX_QUEUE = floor(MAX_PROMISED_WAIT × Σ_workers μ̂)` with μ̂ an EWMA of
   observed completions/s. Reject only above that, with an honest
   `Retry-After` computed from the drain estimate.
5. **Transcode before the GPU box ever sees the bytes.** ≤448 px short edge,
   2 fps, content-addressed in S3 + local NVMe. Our own measurement says this is
   worth **~2.3×** throughput on 1080p sources.
6. **Move video fetch/transcode off the GPU host's vCPUs.** vLLM's own docs put
   the floor at `2 + N` *physical* cores and warn that 1 vCPU = ½ physical core
   [src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md);
   `g6e.2xlarge` gives us 4 physical cores for a 3-core floor.
7. **Queue-pull, not push.** Workers lease work from the queue. A worker that
   dies loses a lease, not a request.
8. **Idempotency keys on every POST**, Stripe semantics: store status + body,
   replay on repeat, error on parameter mismatch
   [src](https://docs.stripe.com/api/idempotent_requests).

---

## 1. Contract options: how long may a client wait?

### 1.1 What "no drop" has to mean, precisely

"No dropped requests" cannot mean "infinite queue": an unbounded queue converts
a load spike into an unbounded latency spike and an OOM. The honest definition,
and the one this design implements:

> **A request that the system accepts is never lost.** It is either completed,
> or it fails with a terminal, explained error. A request the system cannot
> promise to serve within its stated wait bound is *refused at admission* with a
> computed `Retry-After` and an offer of the async contract — before the client
> has spent anything and before any work is committed.

A refusal at admission is not a drop; a 504 after 90 s of holding a connection
is. RFC 9110 is explicit that 503 *"MAY send a Retry-After header field… to
suggest an appropriate amount of time for the client to wait"*
[src](https://www.rfc-editor.org/rfc/rfc9110.txt) §15.6.4 — see
[`03-concurrency-and-admission-control.md` §3.5](../scaling/03-concurrency-and-admission-control.md)
for the full status-code table; this document does not re-derive it.

### 1.2 The timeout ladder — how long a synchronous request can actually live

Every hop has its own idle timeout. The binding constraint is the **smallest**
one on the path, and all of them (except CloudFront's response-completion
timeout) are *idle* timeouts: they reset when bytes flow. That is the whole
reason SSE keep-alive comments exist.

| Hop | Timeout | Default | Range | Resets on bytes? |
|---|---|---|---|---|
| **ALB** | `idle_timeout.timeout_seconds` | **60 s** | 1–4000 s | Yes — *"the period of time an existing client or target connection can remain inactive, with no data being sent or received"* [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) |
| **ALB** | `client_keep_alive.seconds` | **3600 s** | 60–604800 s | **No** — *"The duration period continues when there's no traffic, and does not reset until a new connection is established."* [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) |
| **NLB** (TCP) | connection idle timeout | **350 s** | 60–6000 s | Yes; TCP keepalives count [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/network-load-balancers.html) |
| **NLB** (TLS listener) | idle timeout | **350 s, not modifiable**; LB injects keepalives every 20 s | — | Yes [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/network-load-balancers.html) |
| **CloudFront** | origin response timeout | **30 s** | 1–120 s (quota increase) [src](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cloudfront-limits.html) | Yes — *"How long… CloudFront waits after receiving a packet of a response from the origin and before receiving the next packet"* [src](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesOrigin.html) |
| **CloudFront** | origin keep-alive timeout | **5 s** | 1–300 s | n/a |
| **CloudFront** | *response completion timeout* | — | — | **No** — *"The time (in seconds) that a request from CloudFront to the origin can stay open and wait for a response"* [src](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesOrigin.html) |
| **Caddy** (ours today) | `transport http { read_timeout 600s }` | no default | any | Yes; `flush_interval -1` disables response buffering entirely [src](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) |
| **Caddy** | `stream_timeout` | none (unlimited) | any | **No** — forcibly closes streaming requests after the duration [src](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) |

Three consequences for us:

- **Do not put CloudFront in front of the inference path.** A 120 s ceiling on
  the response timeout out of the box, and the response-completion timeout is
  not idle-based at all. CloudFront is for the console's static assets, not for
  `/v1/chat/completions`. *(Corrected 2026-09-20: the quotas page lists
  "Response timeout per origin | 1-120 seconds" as the **default quota** and
  carries a "Request a higher quota" link, and the origin-settings page says
  "If you request a timeout increase for your AWS account, update your
  distribution origins…" — so 120 s is the default cap, **not** a hard ceiling
  after a quota increase, as an earlier draft asserted
  [src](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cloudfront-limits.html),
  [src](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesOrigin.html).
  The recommendation stands on the response-completion timeout, which is not
  idle-based, and on CloudFront having no role on this path.)*
- **If we move from the Elastic IP to an ALB** (§6.3 argues we should, for
  draining and multi-AZ), raise `idle_timeout.timeout_seconds` to at least
  `SYNC_MAX_WAIT + p99 generation time + margin`. 60 s default would cut a
  queued request off mid-wait. Also note ALB *"do not support HTTP/2 PING
  frames. These do not reset the connection idle timeout"*
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html)
  — application-level bytes are the only heartbeat that works.
- **Our current Caddy config is already correct for streaming**
  (`flush_interval -1`, `read_timeout 600s`, no `stream_timeout`). Keep it;
  the change in §7 is behind it, not in it.

### 1.3 Synchronous HTTP + SSE keep-alives

For `"stream": true`, SSE comment lines are the keep-alive. MDN, quoting the
spec's intent: *"The comment line can be used to prevent connections from timing
out; a server can send a comment periodically to keep the connection alive"*
[src](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events).
Comments are lines beginning `:` and are ignored by every compliant client.

vLLM ships exactly this, and its docstring names our problem verbatim
[src](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/utils/sse_keep_alive.py):

> *"Reverse proxies and tunnels (Cloudflare Tunnel, NGINX `proxy_read_timeout`,
> AWS ALB, ...) can close a streaming connection when no bytes are sent for a
> while. That happens **while a request is queued**, during prefill, or in gaps
> between tokens. `with_sse_keep_alive` wraps the final SSE generator and emits
> a comment line (`": keep-alive\n\n"`) whenever it has been idle for
> `interval` seconds. Comments are ignored by SSE-compliant clients but still
> count as bytes for proxy read-timeout logic."*

The flag is `--sse-keep-alive-interval`, read as
`getattr(args, "sse_keep_alive_interval", 0)` — **0, i.e. off, by default**
[src](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/chat_completion/api_router.py).
We should not rely on vLLM's copy anyway, because our keep-alives must start
*at the gateway*, while the request is still in **our** queue and vLLM has never
heard of it.

**Non-streaming (`"stream": false`) has no keep-alive channel at all.** This is
the single strongest argument for a queue-aware contract: a non-streaming
request that waits 90 s in our queue is invisible to every proxy on the path and
will be cut. Two options, and we take both:

1. **Emit a `102 Processing`/early-hints-style progress channel** — not
   portable, most OpenAI SDKs ignore it. Rejected.
2. **Answer with the async contract when the predicted wait exceeds
   `SYNC_NOSTREAM_MAX_WAIT`** (start at 10 s for non-streaming, 30 s for
   streaming). This is §1.5.

We can also make the *queue wait itself* observable inside the SSE stream, which
is strictly better than a bare comment, by emitting a comment carrying JSON that
curious clients can parse and everyone else ignores:

```
: {"queue_position":7,"eta_s":11.2}

: keep-alive

data: {"id":"chatcmpl-...","choices":[{"delta":{"content":"A"}}], ...}
```

### 1.4 Asynchronous jobs

The async contract is the one that actually delivers "no drop" at arbitrary
load, because it decouples the client's connection lifetime from the queue's.

```
POST /v1/jobs                 → 202 Accepted
                                Location: /v1/jobs/{id}
                                { "id": "...", "status": "queued",
                                  "queue_position": 41, "eta_s": 26.1 }
GET  /v1/jobs/{id}            → 200 {status: queued|running|succeeded|failed, ...}
                                Retry-After: <poll hint>
GET  /v1/jobs/{id}/events     → SSE stream of the same job (optional, nicer)
DELETE /v1/jobs/{id}          → cancel (§3.4)
```

Plus two transport affordances:

- **Uploads.** `POST /v1/uploads` returns a presigned S3 `PUT` URL and an
  `infrx://upload/{sha256}` handle that can be used as a `video_url`. This kills
  the base64 data-URL path (which costs 33 % on the wire and pins the whole clip
  in the gateway's memory) and gives us the content hash **for free**, because
  the client tells us the digest it uploaded and we verify it. It also removes
  the SSRF surface for those clients entirely (§4.2).
- **Webhooks.** Optional `"webhook": {"url": ..., "secret": ...}` on the job;
  POST the terminal state, HMAC-signed, with at-least-once retries. Polling
  stays the contract of record; the webhook is an optimisation.

**Result storage.** Marlin outputs are ≤2048 tokens — a few KB. Keep results in
Postgres (Supabase), not S3; S3 is for *inputs*. Retain results 24 h by default,
then delete. That keeps the "no prompt or video content is stored anywhere"
promise in [`apps/README.md` §4](../../apps/README.md) *almost* intact — the
async contract necessarily stores the completion until the client collects it,
and that must be stated in the docs and made configurable per org.

### 1.5 Both — and the rule that picks

```
predicted_wait = queue_depth_ahead_of_me / Σ_workers μ̂        # §2.4
```

| Condition | Response |
|---|---|
| `predicted_wait ≤ SYNC_MAX_WAIT` and `stream=true` | 200, SSE, keep-alives every `SSE_KEEPALIVE_S` |
| `predicted_wait ≤ SYNC_NOSTREAM_MAX_WAIT` and `stream=false` | 200, plain JSON |
| `predicted_wait ≤ MAX_PROMISED_WAIT` | **202** with `Location: /v1/jobs/{id}`, plus `X-Infrx-Queue-Position` and `X-Infrx-ETA-Seconds`. The request is *accepted*, not dropped |
| `predicted_wait > MAX_PROMISED_WAIT` | **429** + `Retry-After: ceil(drain_estimate × U(1.0,1.5))` + body explaining the async endpoint |
| tenant over token-bucket quota | **429** + `Retry-After` from bucket refill; never enters the queue |

A 202 in response to `POST /v1/chat/completions` will confuse OpenAI SDKs, so
the auto-upgrade only fires when the client opts in with
`X-Infrx-Accept-Async: 1` (documented, and set by our own SDK snippets in the
console). Without it, an over-`SYNC_MAX_WAIT` request gets the 429 with the
pointer to `/v1/jobs`. This is the honest behaviour: we never silently change the
contract under a client that cannot parse it.

**Starting values, from our measurements** (§0.1): `SYNC_MAX_WAIT = 30`,
`SYNC_NOSTREAM_MAX_WAIT = 10`, `MAX_PROMISED_WAIT = 600`. At one worker doing
μ̂ = 1.57 clips/s on 1080p sources, 30 s of promised wait is a queue of 47 and
600 s is a queue of 942 — see §2.5 for why the second number is *not* the right
cap.

---

## 2. Queue design

### 2.1 Where the queue lives

| Option | Survives gateway restart | Shared across workers | Ordering / fairness control | Ops cost | Verdict |
|---|---|---|---|---|---|
| **In-process `asyncio.Queue`** | No | No | Full (it's our code) | Zero | Fine for **one** worker and a gateway that never restarts. Loses the whole queue on deploy. Today's `MAX_INFLIGHT` is a degenerate version of this |
| **Redis list / sorted set (ElastiCache)** | Yes (with AOF/replica) | Yes | Full — we write the dispatcher | One cluster | **Recommended.** Sub-ms ops, blocking pop (`BLMOVE`, *"Pops an element from a list, pushes it to another list and returns it. Blocks until an element is available otherwise"*, O(1), since 6.2.0 [src](https://redis.io/docs/latest/commands/blmove/)) gives lease semantics for free |
| **Redis Streams + consumer groups** | Yes | Yes | Full, plus `XAUTOCLAIM` for crashed consumers | One cluster | Use if we want the pending-entries list as the lease ledger instead of hand-rolling it. Heavier API, same guarantees |
| **SQS FIFO** | Yes, managed | Yes | Ordering yes; **priority no** | Zero ops | Good for the *async* tier only. See constraints below |
| **SQS standard + fair queues** | Yes, managed | Yes | Per-tenant fairness **built in** | Zero ops | Genuinely interesting; see below |

**SQS constraints that matter to us**
[src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.html):

- Max message **1,048,576 bytes (1 MiB)**. Our job envelope is a few KB once the
  video is an S3 handle, so this is fine — but it is a hard "never put base64
  video in the queue" rule.
- Retention default **4 days**, max **1,209,600 s (14 days)**; minimum 60 s.
- Visibility timeout default **30 s**, max **12 hours** — this is the lease
  timer, and it must exceed our p99 service time plus cold-start (§6.2).
- FIFO: **300 TPS per partition** per API action non-batched, **3,000 msg/s**
  with batching; high-throughput FIFO reaches **70,000 TPS** non-batched in
  us-east-1 [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/high-throughput-fifo.html).
  We are nowhere near these; TPS is not the reason to avoid FIFO.
- Standard queues cap at **~120,000 in-flight messages**; with long polling SQS
  *"does not return an error when the in-flight message limit is reached"* — it
  simply returns nothing [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html).

**SQS fair queues** deserve a serious look, because they implement the exact
per-tenant policy §2.3 describes, as a managed feature
[src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-fair-queues-detailed.html):

> *"Fair queues identify tenants by the `MessageGroupId` property on each
> message… A tenant is marked as a noisy neighbor when either of the following
> is true: **Concurrency share**: the tenant accounts for more than 10% of
> in-flight messages in the queue and has at least 30 of its own messages in
> flight. **Processing time share**: the tenant's recent share of total consumer
> processing time exceeds 10%… Noisy neighbor messages are not dropped or
> throttled. Their dwell time rises while quiet-tenant messages are prioritized,
> and returns to normal once their backlog clears."*

And the caveat that decides it for us:

> *"For the concurrency share measure to work effectively, your consumers need
> to process enough messages concurrently that one tenant's share of in-flight
> messages can stand out."*

With one L40S at c=8, we have **8** in-flight messages. The 30-message threshold
never fires; only the processing-time-share measure would, and AWS says it works
best when both do. SQS fair queues become viable at ~4+ workers (c≈32) and are
worth revisiting then. Until then the fairness logic is ours.

**Recommendation: Redis (ElastiCache Valkey/Redis OSS), one queue per model,
with the dispatcher in the gateway.** Reasons, in order: (a) we need
*priority + fairness + wait estimation + position reporting*, all of which are
reads and writes against the queue that SQS does not expose; (b) sub-millisecond
latency matters because the sync path holds a connection while we enqueue;
(c) we already need Redis for the shared preprocessing cache index (§4.3) and
the distributed token buckets (§2.3), so it is not a new dependency by the time
we need the queue. **Keep the in-process path as the degenerate single-worker
case** — `REDIS_URL` unset ⇒ in-process `asyncio` queue, exactly as
`SUPABASE_URL` unset today means "serve unauthenticated" (that pattern is
already in `gateway.py` and the tests rely on it).

### 2.2 Data structures

Per model `m`, per priority band `p` (see §7.2 for the full key list):

```
q:{m}:{p}:{tenant}      LIST    job ids, RPUSH tail / LMOVE head          (FIFO within a tenant)
q:{m}:{p}:active        ZSET    tenant -> virtual finish time             (WFQ across tenants)
lease:{m}               ZSET    job id -> lease expiry (unix s)           (in-flight ledger)
job:{id}                HASH    envelope, state, attempts, result ref
svc:{m}                 HASH    EWMA service time, EWMA arrival rate
tb:{tenant}             HASH    token bucket {tokens, ts}
idem:{tenant}:{key}     STRING  job id, EX 86400                          (§2.6)
```

Three levels of nesting (band → tenant → FIFO) is deliberately the same shape as
llm-d's, which is the only production system with this exact policy documented
[src](https://llm-d.ai/docs/well-lit-paths/foundations/flow-control). The
source's own wording (*re-quoted verbatim 2026-09-20; the block previously
printed here was a paraphrase set in quotation marks*):

> *"EPP leverages these headers to assign a `FlowKey` (tuple of `FairnessID`
> and `Priority`)"*, it *"maintains separate in-memory queues for each
> `FlowKey`"*, and *"in each scheduling cycle, the EPP traverses the queues in
> 3 tiers: Priority… Fairness… Ordering"*.

### 2.3 Priority tiers and per-tenant fairness

**Bands.** Three is enough, and they map to things the console already knows:

| Band | Who | Policy |
|---|---|---|
| `interactive` (100) | paid orgs, `stream=true` | served first; never preempted |
| `standard` (0) | paid orgs, async jobs | default |
| `free` (-10) | trial/granted-credit orgs | served only when no higher band is waiting; first to be shed |

Priority is strict between bands (llm-d: *"the system always services highest
`PriorityBand` first"*). Strict priority starves the bottom band under sustained
overload — that is the intent for `free`, and it is why `free` must carry a
lower `MAX_PROMISED_WAIT` so its requests are refused at admission rather than
aging forever.

**Fairness within a band: weighted fair queuing by *service time*, not request
count.** Marlin requests vary **~11× in prompt tokens** (a 10 s clip vs a 120 s
clip is 2,061 vs ~23.5K prompt tokens — `models/marlin2b/README.md`,
`results/notes.md`; 23,500 / 2,061 = 11.4, *recomputed 2026-09-20; an earlier
draft said "~4×", which no source in this repo supports*), so counting requests
is wrong. Virtual-time WFQ, one line per dispatch:

```
on enqueue(job, tenant t):
    V[t] = max(V[t], now) + predicted_service_seconds(job) / weight[t]
    zadd q:{m}:{p}:active  V[t]  t            # tenant's virtual finish time
    rpush q:{m}:{p}:{t}    job.id

on dispatch():
    for band p in [100, 0, -10]:
        t = zrangebyscore(q:{m}:{p}:active, limit 1)   # smallest virtual time
        if t: return lpop(q:{m}:{p}:{t}), t
```

`weight[t]` defaults to 1 and is the natural knob for a future "dedicated
capacity" SKU (the console already has a Dedicated page). This is the
deficit-round-robin/WFQ family; the LLM-specific variant with the fairness
analysis is VTC, already summarised in
[`03-concurrency-and-admission-control.md` §4.7](../scaling/03-concurrency-and-admission-control.md).

**Token buckets are a separate, earlier gate.** Fairness decides *order among
admitted requests*; the bucket decides *whether a tenant may enqueue at all*.
Two buckets per org, both refilled continuously:

- requests/min (cheap abuse protection)
- **video-seconds/min** (the actual cost driver: `usage_events.video_seconds`
  already exists in the schema, `apps/README.md` §6)

Implement as a Lua script on Redis so check-and-consume is atomic. Over-bucket ⇒
429 with `Retry-After = ceil((needed - tokens) / refill_per_s)`. This never
enters the queue, so a runaway client cannot displace anyone.

### 2.4 Wait-time estimation from measured service time

Little's Law is the whole of it: `L = λW`. Per model:

```
μ̂        = EWMA of completions per second across all healthy workers   (Redis: svc:{m})
ŝ        = EWMA of per-request service time, by size bucket            (see below)
depth(p,t) = jobs ahead of this one under the WFQ order
eta       = depth_ahead / μ̂  +  residual_of_in_flight
```

Two refinements that make the estimate honest rather than decorative:

1. **Bucket ŝ by input size.** Our own numbers make the case: 8-concurrency
   service time is 5.10 s for a 1080p source and 2.23 s for a 360p one (est.
   from §0.1). Once §4's transcode stage lands, the *post-transcode* clips are
   uniform and this collapses to bucketing by **clip duration**, which we know
   from `ffprobe` before we enqueue. Buckets: ≤15 s, ≤45 s, ≤120 s.
2. **Add the residual.** A request arriving when 8 are running waits for a
   partial service time, not a whole one. For an M/G/c approximation the mean
   residual is `E[S²]/(2E[S])`; with our measured coefficient of variation
   unknown, start with `residual = 0.5 × ŝ` and correct against observed
   `eta_error = actual_wait − predicted_wait`, logged per request. **⚠️ TO BE
   VERIFIED:** we have no measured service-time distribution yet, only three
   means; the 0.5 factor is the exponential-service assumption and should be
   replaced by the measured `E[S²]/(2E[S])` after the first week of production
   `usage_events`.

**Publish the estimate and then grade yourself on it.** Add `eta_s_predicted`
and `queue_wait_ms` to `usage_events`; a dashboard tile of
`p90(|predicted − actual|)` is the only thing that keeps the number honest. An
ETA nobody checks becomes a lie within a month.

### 2.5 Admission: the rule, and the number it produces

```
MAX_QUEUE(p) = floor(MAX_PROMISED_WAIT(p) × μ̂ × SAFETY)      # SAFETY = 0.8
admit if depth(p) + 1 ≤ MAX_QUEUE(p)   else 429 + Retry-After
```

With one worker, μ̂ = 1.57 clips/s (1080p) and `MAX_PROMISED_WAIT = 600 s`, that
is a queue of 753. **That is too many**, for two reasons the formula does not
know about:

- **Memory.** Each queued job holds an envelope. Bound it separately — llm-d
  does exactly this and says why
  [src](https://github.com/llm-d/llm-d/blob/main/guides/flow-control/tuning.md):
  *"The `maxRequests` and `maxBytes` parameters in the `flowControl` section are
  static bounds intended to manage host memory pressure and protect the Gateway
  proxy from running out of memory during extreme traffic bursts."* Their
  defaults: `maxRequests: 200`, `maxBytes: "10Gi"`. Take `MAX_QUEUE_HARD = 500`
  and `MAX_QUEUE_BYTES = 256Mi` (our envelopes are KB once video is an S3
  handle, so bytes will never bind — which is itself the argument for §1.4's
  upload endpoint).
- **Staleness.** A job that has waited 10 minutes is usually a job whose client
  gave up. Expire queued jobs at `MAX_PROMISED_WAIT` with a terminal
  `504 job_expired` state (not a silent delete), and count it as an SLO
  violation.

So the effective rule is `MAX_QUEUE = min(formula, MAX_QUEUE_HARD)`, and the
`Retry-After` on refusal is the drain estimate, jittered server-side:

```
retry_after = ceil((depth − MAX_QUEUE + 1) / μ̂ × uniform(1.0, 1.5))
```

Server-side jitter matters: a constant `Retry-After: 2` — what `gateway.py`
returns today — synchronises every rejected client into a thundering herd
exactly 2 s later.

**One more admission gate, specific to us: scale-up lead time.** vLLM on this
box boots in ~2–3 min including `torch.compile`
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)). If the
autoscaler has fired and a worker is `booting`, μ̂ should be projected forward:
`μ̂_effective = μ̂_now + Σ_booting μ̂_per_worker × P(ready before my ETA)`.
Simplest correct version: count a booting worker at full rate only for jobs
whose predicted wait already exceeds its remaining boot time. Without this, we
refuse requests we will in fact be able to serve. See
[`06-cold-start.md`](../scaling/06-cold-start.md) for shortening that 2–3 min
(compile cache, warm pools —
[`ec2-auto-scaling-warm-pools`](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html)
keeps instances `Stopped`, `Running` or `Hibernated` with `MinSize` /
`MaxGroupPreparedCapacity`).

### 2.6 Idempotency keys

Take Stripe's semantics wholesale; they are the de-facto standard and clients
already know them [src](https://docs.stripe.com/api/idempotent_requests):

> *"Stripe's idempotency works by saving the resulting status code and body of
> the first request made for any given idempotency key, regardless of whether it
> succeeds or fails. Subsequent requests with the same key return the same
> result, including `500` errors… Idempotency keys are up to 255 characters
> long… You can remove keys from the system automatically after they're at least
> 24 hours old… The idempotency layer compares incoming parameters to those of
> the original request and errors if they're not the same… All `POST` requests
> accept idempotency keys."*

Concretely:

| Situation | Behaviour |
|---|---|
| `Idempotency-Key` absent | Treated as unique; no replay protection. Documented as such |
| Key unseen | `SET idem:{tenant}:{key} {job_id} NX EX 86400`; proceed |
| Key seen, **same** request hash, job terminal | Replay the stored status + body, header `Idempotency-Replayed: true` |
| Key seen, **same** request hash, job still running | For sync: attach to the running job's stream. For async: return the same `202` + job id |
| Key seen, **different** request hash | `409 idempotency_key_reuse` |
| `SET NX` lost the race | `409 idempotency_conflict`, retryable — Stripe's *"the request conflicts with another request that's executing concurrently"* case |

Request hash = `sha256(canonical_json(body_without_stream_flag))`. Store it
beside the job id. **Streaming replay is a real decision**: we do *not* store
token-by-token output for replay; a replayed streaming request gets the final
completion as a single SSE `data:` frame plus `[DONE]`. Document it.

### 2.7 Exactly-once usage accounting

`usage_events.id` is already the `Inference-Id` and the insert is already
idempotent on the primary key (`README.md`: *"id = the `Inference-Id` header, so
inserts are idempotent"`, and a 409 is counted as success). The v2 additions:

1. **One usage row per *job*, not per attempt.** Use the job id as
   `usage_events.id`. Retries (§5) reuse it, so a request retried three times
   bills once. Add `attempts int` so the ops view can see rework.
2. **Bill on terminal state only**, written by the worker that completed the job
   under its lease — never by the gateway, which may have lost the connection.
3. **Keep the local `usage.jsonl` + `usage_failed.jsonl` spill and
   `deploy/replay_usage.py`.** It already works, it is the durable fallback, and
   it is the only thing that survives Supabase being down. Do not replace it
   with Redis; Redis is not the billing ledger.
4. **A replayed idempotent request writes no new usage row** — that is the whole
   point of the key. Emit an `idempotency_replay` counter instead.

---

## 3. Backpressure from vLLM

### 3.1 The signals

vLLM exposes these on `/metrics` with the `vllm:` prefix
[src](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md):

| Metric | Type | Use |
|---|---|---|
| `vllm:num_requests_running` | Gauge | *"Number of requests in model execution batches"* — the engine's real active batch |
| `vllm:num_requests_waiting` | Gauge | engine-local queue; **this should be ~B and never grow** if §3.2 is configured right |
| `vllm:kv_cache_usage_perc` | Gauge | *"Fraction of used KV cache blocks (0–1)"* — the memory ceiling |
| `vllm:request_queue_time_seconds` | Histogram | engine-side queue time; distinguishes "our queue" from "their queue" |
| `vllm:time_to_first_token_seconds` | Histogram | TTFT SLI |
| `vllm:request_time_per_output_token_seconds` | Histogram | TPOT SLI, *"computed as `(end-to-end latency - TTFT) / (number of output tokens - 1)`"* |
| `vllm:request_success_total{finished_reason="abort"}` | Counter | **cancellations** — the number that tells us clients are hanging up |
| `vllm:prefix_cache_hits` / `_queries` | Counter | cache effectiveness |
| `vllm:cache_config_info` | Info | total KV blocks + block size, for the §3.2 arithmetic |

**Open-loop vs closed-loop.** llm-d ships both and is unusually clear about the
trade-off [src](https://github.com/llm-d/llm-d/blob/main/guides/flow-control/tuning.md):

> *"`utilization-detector` (Default / Closed-Loop): Reacts to the true physical
> state of the hardware (KV cache utilization, engine queue depth)… It relies on
> a periodic (50ms) telemetry scrape interval and is optimized for sustained,
> organic traffic. `concurrency-detector` (Open-Loop): Evaluates capacity using
> zero-latency optimistic accounting of in-flight requests. Because it does not
> wait for engine telemetry, its instantaneous reaction time makes it a great
> choice for workloads that experience sudden, massive traffic bursts."*

…and the guide's own recommendation: *"While `utilization-detector` is the
out-of-the-box system default listed here, production deployments should switch
to `concurrency-detector` to avoid telemetry lag risks, as detailed in the
[Tuning Guide]."*
[src](https://github.com/llm-d/llm-d/blob/main/guides/flow-control/README.md)
(*citation corrected 2026-09-20: the recommendation is in the guide's
`README.md`; `tuning.md` carries the detector descriptions quoted above*).

**For us: open-loop primary, closed-loop as the safety valve.** The gateway's
own semaphore count is the admission signal (zero latency, no scrape); scraped
`vllm:kv_cache_usage_perc` and `vllm:num_requests_waiting` only *reduce* the
effective limit when the engine is in trouble. Concretely, scrape every 2 s and:

```
if kv_cache_usage_perc > 0.90 or num_requests_waiting > 2*B:   effective_N = max(1, N - 2)
if kv_cache_usage_perc < 0.70 and num_requests_waiting <= B:   effective_N = min(N, effective_N + 1)
```

Additive-increase/multiplicative-decrease on the dispatch gate. Log every change.

### 3.2 Three limits, applied to Marlin on one L40S

llm-d's 3-layer architecture, quoted because the reasoning is exactly ours
[src](https://github.com/llm-d/llm-d/blob/main/guides/flow-control/tuning.md):

> *"1. **The Compute Limit (Engine Active Batch Limit = N)** … the GPU never
> actively executes more than N requests at a time (e.g. `--max-num-seqs` in
> vLLM). 2. **The Dispatch Gate (EPP `maxConcurrency` = N + B)** … By allowing a
> small, mathematically sound subset of requests to sit in the model server's
> local waiting queue, the engine has enough continuous work to maximize
> throughput. 3. **The Burst Queue (EPP Flow Control `maxRequests` = 200+)** …
> By holding the burst centrally in the Gateway instead of pushing it to the
> engine, the EPP can enforce multi-tenant Fairness, sort by Priority, and
> dynamically route requests to other replicas if they free up (Late-Binding)."*

Mapped onto our box:

| Layer | Knob | Where | Today | Proposed |
|---|---|---|---|---|
| Compute limit N | `--max-num-seqs` | `apps/infrx-api/deploy/marlin2b-vllm.service` (**not** `serve.sh`) | **32** — *corrected 2026-09-20* | **re-pin at the measured point**, start at 8 |
| Dispatch gate N+B | per-worker semaphore | `gateway.py` | `MAX_INFLIGHT=16`, global, and a *rejection* not a gate | `WORKER_CONCURRENCY = N + B`, B = 2 ⇒ 10 |
| Burst queue | `MAX_QUEUE` | Redis | none | 500 hard / formula soft (§2.5) |

Why N = 8 and not 32: **8 is the only concurrency we have measured** (0.50
clips/s at c=1 → 1.57 at c=8 for 1080p, 3.58 for 360p). Neither the engine's
`--max-num-seqs 32` nor the gateway's `MAX_INFLIGHT=16` was ever measured, and
they are not the same kind of limit anyway.

**⚠️ Corrected 2026-09-20 — an earlier draft of this document said
`--max-num-seqs` was "not set, so we are on vLLM's default". It is set, to 32.**
`serve.sh` genuinely does not pass the flag, but it forwards `"$@"`, and
`apps/infrx-api/deploy/marlin2b-vllm.service` runs
`ExecStart=…/models/marlin2b/serve.sh --max-num-seqs 32` — so the box has been
serving at **N = 32** all along, behind a gateway that refuses above 16. The
effective compute limit today is therefore `min(32, 16) = 16`, set by the wrong
layer: the gateway *rejects* at 16 rather than gating, and the engine would
happily admit twice that. `apps/infrx-api/openrouter/PLAN.md:63` carries the
same 32. **The fix is to the unit file, not to `serve.sh`** — editing `serve.sh`
would change nothing on the running box. This matches the independent finding in
[`01-requirements-and-traffic-model.md` §3.2 / correction C3](01-requirements-and-traffic-model.md),
which this document previously contradicted.
The tuning procedure to replace this guess is llm-d's, and it is a good one:
serve with `--max-num-seqs 2048`, step-load until the SLO breaks, take the
concurrency just below the breach; separately compute the memory bound from KV
blocks and ISL/OSL statistics; take `min(compute, memory)`. Their
`tuning_wizard.py` does the arithmetic. Our `models/marlin2b/bench.py` already
does the load side — it needs a step-load mode and the `vllm:cache_config_info`
read.

**The multimodal wrinkle**: for Marlin the binding budget is very likely the
*encoder/CPU* stage, not KV. A 2-minute clip is ~23.5K prompt tokens against
`--max-model-len 32768`, so **N is capped at 1 for maximum-length clips** by
model length alone, while 10 s clips at 2,061 tokens could run **15** wide on
tokens (15 × 2,061 = 30,915 ≤ 32,768; 16 × 2,061 = 32,976 **overruns** it —
*recomputed 2026-09-20, was "16 wide"*).
This is the concurrency wall described in
[`03-concurrency-and-admission-control.md` §6.1 and §6.4](../scaling/03-concurrency-and-admission-control.md).
Practical consequence: **size the dispatch gate in video-seconds, not
requests.**

```
WORKER_BUDGET_VIDEO_SECONDS = 8 × 10 = 80       # what c=8 on 10 s clips means
admit to worker w if  inflight_video_seconds(w) + job.video_seconds ≤ WORKER_BUDGET_VIDEO_SECONDS
```

We already know `job.video_seconds` before dispatch — `ffprobe` runs at
admission. A 30 s clip then occupies 3 of the 8 "slots' worth" of budget and
blocks the right amount of concurrency, instead of counting as 1 of 8 and
blowing the KV budget. **⚠️ Corrected 2026-09-20 — the budget as written
rejects the largest legal request:** `MAX_VIDEO_SECONDS = 120`
(`gateway.py`, confirmed) but `WORKER_BUDGET_VIDEO_SECONDS = 80`, so
`120 + 0 > 80` and a 120 s clip can never be admitted to an idle worker. An
earlier draft called this "1.5 slots' worth", conflating 120/80 = 1.5 *budgets*
with 120/10 = 12 *slots*. The gate needs
`WORKER_BUDGET_VIDEO_SECONDS ≥ MAX_VIDEO_SECONDS` (≥ 120), or an explicit
"a single job always admits to an empty worker" carve-out. Note 120 s ≈ 23.5K
tokens is already N = 1 by model length (32,768 / 23,500 = 1.39), so the
carve-out and the KV bound agree. **⚠️ TO BE VERIFIED:** the 80 video-seconds figure is derived from the
one measured point (c=8 × 10 s clips); it needs a mixed-length benchmark, which
`results/notes.md` already lists as open.

### 3.3 Cancellation propagation

Today a client that hangs up mid-generation leaves the request running on the
GPU to completion. At c=8 that is up to 12.5 % of the box burned on output
nobody will read.

vLLM solves this with `with_cancellation`, and the docstring explains the one
non-obvious part
[src](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/utils/api_utils.py):

> *"Decorator that allows a route handler to be cancelled by client
> disconnections. This does _not_ use `request.is_disconnected`, which does not
> work with middleware. Instead this follows the pattern from
> `starlette.StreamingResponse`, which simultaneously awaits on two tasks — one
> to wait for an http disconnect message, and the other to do the work that we
> want done. When the first task finishes, the other is cancelled… In the case
> where a `StreamingResponse` is returned by the handler, this wrapper will stop
> listening for disconnects and instead the response object will start listening
> for disconnects."*

Copy it. The chain we need is three links long, and each one is a separate fix:

1. **Client → gateway.** `with_cancellation` on `chat()`. For streaming,
   Starlette's `StreamingResponse` already cancels the generator on disconnect —
   `gateway.py`'s `gen()` has a `finally:` block, which is where the cancel
   currently lands, and it correctly decrements `inflight` there.
2. **Gateway → vLLM.** `httpx`'s `client.stream(...)` context manager closes the
   upstream connection when the generator is closed; vLLM aborts the request on
   upstream disconnect and increments
   `vllm:request_success_total{finished_reason="abort"}`. **⚠️ TO BE VERIFIED
   (2026-09-20):** the metric and its `abort` label are confirmed in vLLM's
   metrics doc, and `with_cancellation` confirms the *server-side* disconnect
   path, but that an `httpx` client-side stream close reliably reaches the
   engine as an abort — rather than the generation running to completion on a
   half-closed socket — was **not** confirmed against vLLM source in this pass.
   Method to settle it: hang up mid-stream against the running box and diff
   `vllm:request_success_total{finished_reason="abort"}`. Until then, treat the
   12.5 %-of-the-box figure above as the *upper bound on the saving*, not a
   measured one. For the **non**-stream
   path this does **not** happen today, because `await client.post(...)` is not
   cancellable from the client side once issued — wrap it in an
   `asyncio.Task` and cancel it.
3. **Gateway → queue.** A job cancelled while still *queued* must be removed
   from the Redis list and marked `cancelled`, or we dispatch work for a client
   that left. `DELETE /v1/jobs/{id}` does the same thing explicitly.

Add a `cancelled` terminal status to `usage_events.status` handling and bill
`completion_tokens` actually produced — not zero, not the full request.

### 3.4 Per-phase timeouts

A single 600 s timeout, which is what `gateway.py` has
(`httpx.Timeout(600, connect=10)`), cannot distinguish "the source URL is a
tarpit" from "the model is generating a long answer". Budget each phase:

| Phase | Knob | Start value | Failure |
|---|---|---|---|
| Source fetch (HTTP GET of `video_url`) | `FETCH_TIMEOUT_S` + byte cap | 30 s, 64 MB (= `MAX_VIDEO_MB`) | `400 video_fetch_failed`, **safe to retry** |
| `ffprobe` | `PROBE_TIMEOUT_S` | 10 s (currently 30) | `400 video_unreadable`, terminal |
| Transcode (§4) | `TRANSCODE_TIMEOUT_S` | `max(15, 0.5 × duration)` | retry once, then terminal |
| Queue wait | `MAX_PROMISED_WAIT` | 600 s | `504 job_expired` |
| Prefill / TTFT | `TTFT_TIMEOUT_S` | 60 s | **safe to retry on another worker** (§5.1) |
| Inter-token gap | `TPOT_STALL_S` | 20 s | abort; **not** safely retryable mid-stream |
| Total generation | `GENERATION_TIMEOUT_S` | 300 s | abort with partial usage |

The inter-token stall timer is the important one and does not exist today:
vLLM's own TPOT at c=8 is 8 ms
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)), so a 20 s gap
means the engine is wedged, and 300 s of a wedged engine holding a slot is worse
than a clean failure. This is the NCCL-hang / engine-deadlock silent failure
class catalogued in
[`08-reliability-and-operations.md` §2.6](../scaling/08-reliability-and-operations.md).

Note `MAX_VIDEO_MB` is currently enforced **after** the whole body is in memory
(`r.content` then `len(data) > MAX_VIDEO_MB * 2**20`). A hostile or merely large
URL OOMs the gateway before the check runs. Stream the body and abort at the cap
(§4.2).

---

## 4. Video preprocessing as a separate CPU stage

### 4.1 Why this section is the highest-leverage one in the document

Our measurement, restated because everything here follows from it: same token
budget, concurrency 8 — **1080p source 1.57 clips/s / TTFT 3.35 s; 360p source
3.58 clips/s / TTFT 0.66 s** (`results/notes.md` finding 7, meas. 2026-09-19).
The model never sees more than 448×448 anyway
(`models/marlin2b/README.md`, "Video budget"). We are spending **2.3× our
throughput** decoding pixels we then throw away.

The CPU budget makes it worse. vLLM's own guidance
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md):

> *"The minimum is `2 + N` physical cores (1 for the API server, 1 for the
> engine core, and 1 per GPU worker)… Please note we are referring to **physical
> CPU cores** here. If your system has hyperthreading enabled, then 1 vCPU =
> 1 hyperthread = 1/2 physical CPU core, so you need `2 x (2 + N)` minimum
> vCPUs."*

`g6e.2xlarge` = 8 vCPU = **4 physical cores**. vLLM's floor for 1 GPU is
**3 physical cores**. That leaves **one** physical core for Caddy, `gateway.py`,
its `ffprobe` subprocesses, and the duplicate download — and vLLM warns *"The
engine core process runs a busy loop and is particularly sensitive to CPU
starvation."* We are not slightly under-provisioned on CPU; we are running the
preprocessing stage inside vLLM's minimum.

vLLM also documents that each API server spawns **8 media-loading threads** by
default (`VLLM_MEDIA_LOADING_THREAD_COUNT`) — 8 threads on 4 physical cores,
before our own `ffprobe` calls — and offers `--api-server-count` to scale the
frontend out, with the explicit warning to retune the thread count when you do
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md).

### 4.2 The pipeline

```
video_url ──▶ [1] fetch (SSRF-guarded, streamed, capped)
          ──▶ [2] ffprobe: duration, codec, dimensions
          ──▶ [3] key = sha256(bytes) ; cache lookup
          ──▶ [4] transcode → 448px short edge, 2 fps, H.264, faststart
          ──▶ [5] put to cache (NVMe + S3), record in Redis
          ──▶ [6] enqueue job with cache handle + video_seconds + mm budget
```

**[1] Fetch, with the SSRF guard we do not have today.** `video_seconds()`
currently does `httpx.AsyncClient(follow_redirects=True).get(url)` on any
user-supplied `http(s)` URL. On EC2 that reaches `169.254.169.254`, the VPC, and
anything else routable from the instance. OWASP's cheat sheet frames our case as
*"Application can send requests to ANY external IP address or domain name: Case
when allowlist approach is unavailable"*
[src](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).
Required controls, all of which are small:

- Resolve the hostname **ourselves**, reject any address in a private/reserved
  range (`10/8`, `172.16/12`, `192.168/16`, `127/8`, `169.254/16`, `::1`, ULA,
  and IPv4-mapped forms of all of them), then **connect to the resolved IP**
  with the `Host` header preserved — otherwise DNS rebinding defeats the check
  between resolution and connect.
- `follow_redirects=False`; follow manually, re-validating each hop, max 3.
- Scheme allowlist `https`, `http`; no `file:`, `gopher:`, `ftp:`, `data:` for
  URLs (the separate `data:` base64 path stays, it has no SSRF surface).
- **Stream the body with a running byte counter**, abort at `MAX_VIDEO_MB`.
- Egress security-group restriction as defence in depth, and IMDSv2 enforced
  (hop limit 1) so a leak of the guard is not a credential leak.
- The `/v1/uploads` presigned-S3 path (§1.4) removes the whole class for clients
  that use it. Make it the documented default in the console snippets.

**[2] ffprobe** as today, but with a 10 s timeout and on a **bounded** executor.
Today it uses `run_in_executor(None, ...)`, the default thread pool, which on
this box is `min(32, cpu+4)` = 12 threads of `ffprobe` competing with vLLM's
engine core. Bound it explicitly to 2.

**[3]/[4] Transcode.** One ffmpeg invocation, matched to the model's grid
(2 fps, ≤240 frames, 200,704 px/frame ≈ 448×448 — `models/marlin2b/README.md`):

```bash
ffmpeg -nostdin -v error -y \
  -analyzeduration 10M -probesize 10M \
  -i in.mp4 \
  -t 120 \
  -vf "fps=2,scale='if(gt(iw,ih),-2,448)':'if(gt(iw,ih),448,-2)':flags=bilinear" \
  -an -sn -dn \
  -c:v libx264 -preset veryfast -crf 28 -pix_fmt yuv420p \
  -movflags +faststart \
  out.mp4
```

`-an -sn -dn` drops audio/subtitle/data streams — Marlin uses none of them and
they cost decode time downstream. `fps=2` at the *transcode* step means vLLM's
decoder reads 240 frames instead of 3,000 for a 120 s 25 fps source. The output
of a 10 s 1080p clip at these settings is on the order of 100–200 KB against the
5.5 MB source (**⚠️ TO BE VERIFIED** — measure on `sample-10s.mp4`; the claim
that matters, 360p-class input ⇒ ~3.58 clips/s, is measured).

**[6] Budget.** `budget_kwargs()` already computes
`size.longest_edge = frames × 200,704` from the duration and is correct
(`results/notes.md` finding 3). Keep it; it now runs on the *transcoded*
duration, which equals the source duration after `-t 120`.

### 4.3 Cache by content hash

Two layers, both keyed by `sha256` of the **source** bytes:

| Layer | Where | TTL | Holds |
|---|---|---|---|
| L1 | `/opt/dlami/nvme/cache/{h[:2]}/{h}.mp4` on each GPU box | LRU to a byte cap | transcoded clip |
| L2 | `s3://infrx-media/transcoded/{h}.mp4` | lifecycle 7 d | transcoded clip |
| Index | Redis `mm:{h}` → `{duration, frames, bytes, s3key, mm_kwargs}` | 7 d | metadata, so a hit skips ffprobe too |

A hit skips fetch, probe **and** transcode. For the "same clip, many prompts"
pattern — which is the dominant real pattern for a captioning API, and is
exactly what `results/notes.md` finding 5 warns our benchmark accidentally
measured — this is the difference between 3.8 s and ~1 s end to end.

**Then hand the same identity to vLLM.** vLLM hashes multimodal items by content
for its own cache, and lets the client supply stable IDs instead
[src](https://github.com/vllm-project/vllm/blob/main/docs/features/multimodal_inputs.md):

> *"When using multi-modal inputs, vLLM normally hashes each media item by
> content to enable caching across requests. You can optionally pass
> `multi_modal_uuids` to provide your own stable IDs for each item so caching
> can reuse work across requests without rehashing the raw content… Using UUIDs,
> you can also skip sending media data entirely if you expect cache hits for
> respective items. Note that the request will fail if the skipped media doesn't
> have a corresponding UUID, or if the UUID fails to hit the cache."*

So the gateway sends `"uuid": "sha256:<h>"` on the `video_url` part. On a vLLM
processor-cache hit this removes the **second** decode entirely. The cache is
sized by `mm_processor_cache_gb`, **default 4 GiB**, with
`mm_processor_cache_type="shm"` for the multi-process case, and
`mm_processor_cache_gb=0` to disable
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md).
For 448×448×240-frame tensors, 4 GiB is a small number of clips — worth raising
on the L40S, where we have 48 GB and only 5.4 GB of weights.

Do **not** use the "skip sending media entirely" mode from the gateway: it fails
the request on a cache miss, and a cache miss is exactly what happens after
every vLLM restart. The `uuid` alone is free and safe.

### 4.4 Where the stage runs

| Placement | Throughput effect | Cost | Complexity | When |
|---|---|---|---|---|
| **A. In the gateway process pool** (today, unbounded) | Negative — steals vLLM's cores | 0 | none | never, past one user |
| **B. In the gateway, bounded pool + transcode** | Positive but capped by 1 spare physical core | 0 | tiny | **now** — `TRANSCODE_WORKERS=2`, ship this week |
| **C. Sidecar on a bigger GPU host** (`g6e.4xlarge`, 16 vCPU = 8 physical cores) | Doubles CPU headroom; keeps L1 cache local to the GPU | ~2× instance cost ⚠️ (price not in [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md), which has no g6e row) | small | when 1 GPU is CPU-bound but not GPU-bound |
| **D. Separate CPU fleet** (`c7i`/`c8g` ASG) + S3 hand-off | Scales independently; GPU boxes do only inference | cheap per unit, new failure domain | medium | at 3+ GPU workers, or when the first non-video model lands |
| **E. GPU decode (NVDEC) on the GPU box** | Removes CPU decode entirely | 0 extra instances, some VRAM | medium-high | see below |

**E deserves its own note**, because vLLM now supports it directly
[src](https://github.com/vllm-project/vllm/blob/main/docs/features/multimodal_inputs.md):
backends `opencv` (default, CPU), `torchcodec` (CPU), `pynvvideocodec` (GPU,
NVDEC) and `deepstream` (GPU, NVDEC). *"For workloads with large videos and
relatively light inference, such as video tagging, this can alleviate
bottlenecks in CPU-based video decoders"* — which is a precise description of
Marlin-2B. The costs are real, though: `pynvvideocodec` *"requires"* CUDA MPS
*("Video decoding runs in the API server process while model serving runs in the
engine process")*, and a positive `--mm-ipc-gpu-memory-gb` **carved out of the
KV cache budget**. The L40S has room (5.4 GB of weights in 48 GB), so 1–2 GiB is
affordable.

**Recommended order: B now → D at 3 workers, with E benchmarked in parallel.**
B is a day's work and captures most of the measured 2.3×. D is the shape that
survives contact with the next model (`Qwen3.8-27B` has no video stage at all,
so a shared CPU fleet stops being video-specific and becomes "the preprocessing
tier"). E is the one that could make the CPU stage disappear, but it changes the
serving stack and needs its own benchmark against `models/marlin2b/bench.py`.

A fourth lever, orthogonal and cheap to try:
`--video-pruning-rate <q>` with `--video-pruning-method evs|vidcom2` prunes
video tokens *after* the vision encoder *"to reduce prefill time and KV cache
usage, at some cost in accuracy"*
[src](https://github.com/vllm-project/vllm/blob/main/docs/features/multimodal_inputs.md).
Accuracy cost must be measured against `reference.py` before it goes near
production — `models/marlin2b/README.md` already makes parity-with-the-vendor-path
the first check on any engine change. **⚠️ Added 2026-09-20:** the same page
states *"`evs` is supported by all models implementing multimodal pruning;
`vidcom2` is currently supported by Qwen3-VL only"*, so for Marlin — loaded via
`--hf-overrides` as `Qwen3_5ForConditionalGeneration` — only `evs` is safe to
assume; whether the `vidcom2` gate accepts the remapped architecture is
untested here.

### 4.5 What the numbers become

| Scenario | clips/s per L40S | source |
|---|---|---|
| Today, 1080p sources, c=8 | **1.57** | meas. 2026-09-19 |
| After transcode (B), warm cache miss | **~3.58** | meas. — the 360p row is the same token budget |
| After transcode, cache **hit** | > 3.58, unmeasured | ⚠️ TO BE VERIFIED — fetch+probe+transcode all skipped; the residual is vLLM decode of a small file |
| With NVDEC (E) | unknown | ⚠️ TO BE VERIFIED |

At **$2.24208/h** for the box — AWS Price List, us-east-1, Shared tenancy,
Linux, `BoxUsage:g6e.2xlarge`, publication version 2026-09-18
[src](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.csv),
pinned in [`01-requirements-and-traffic-model.md` §3.8–3.9](01-requirements-and-traffic-model.md)
and [`05-caching.md`](05-caching.md) *(sourced 2026-09-20; this document
previously carried it as an unsourced ⚠️ "$2.24/h from `results/notes.md`")* —
1.57 → 3.58 clips/s takes dense captioning from
**~$0.14 to ~$0.06 per video-hour**. *(Recomputed 2026-09-20. The earlier
"$0.04 → $0.02" copied an arithmetic error in `results/notes.md`, which reads
"one GPU-hour processes 57–129 video-hours": 1.57 clips/s × 10 s × 3,600 s =
**56,520 video-seconds = 15.7 video-hours**, and 3.58 → 128,880 s = 35.8
video-hours. $2.24 / 15.7 = $0.143; $2.24 / 35.8 = $0.063. The notes' 57–129
figure is video-**seconds** in thousands, read as video-hours — a 3,600×
unit slip that made every $/video-hour number in this tree 3.5× too cheap.
`models/marlin2b/results/notes.md` needs the same fix.)* The transcode work itself is not free, but it
happens once per distinct clip and off the critical path on a cache hit.

---

## 5. Retries and hedging

### 5.1 What is safe to retry

The rule is **phase**, not status code: a request is safely retryable until the
first output token has been delivered to the client.

| Failure | Phase | Retry? | Where |
|---|---|---|---|
| Source fetch 5xx / timeout | pre-queue | Yes, ×2, exponential | same gateway |
| `ffprobe` failure | pre-queue | No — bad input | terminal `400` |
| Transcode failure | pre-queue | Once | same gateway |
| Redis unavailable at enqueue | pre-queue | Yes — and **fail closed to 503 + `Retry-After`**, never 200 | client |
| Worker died holding the lease, no tokens sent | pre-prefill | **Yes, automatically** | another worker |
| vLLM 5xx before first token | pre-prefill | **Yes, automatically** ×2 | another worker |
| `TTFT_TIMEOUT_S` exceeded | pre-prefill | Yes, once, on another worker | another worker |
| Any failure **after** the first SSE `data:` frame | mid-stream | **No** | see §5.2 |
| `finished_reason="abort"` from client disconnect | any | No — the client left | — |
| 429 from our own admission | — | client's call, honouring `Retry-After` | client |

Automatic retries are **capped and budgeted**: at most 2 per job, and a global
retry-budget circuit breaker (retries ≤ 10 % of requests over a 1-minute window)
so that a systematic failure does not triple the offered load at the exact
moment the fleet is unhealthy. This is the standard retry-amplification guard and
is not optional at this layer.

### 5.2 Worker failover mid-stream: the honest policy

**You cannot resume a partially-streamed completion on another worker.** The KV
state is on the dead GPU; re-prefilling on a new worker with the already-sent
prefix as context produces a *different* continuation (different sampling state,
and for Marlin a different `<think>` prefix), so stitching would silently corrupt
the answer. The options are: fail, or replay-from-scratch and discard what the
client already saw (impossible — they saw it).

Policy, stated in the docs and implemented in the SDK:

1. Send the terminal SSE error frame and close:
   `data: {"error":{"type":"server_error","code":"stream_interrupted","message":"..."}}`
   then `data: [DONE]`. Never leave a hung stream.
2. Bill only tokens actually delivered.
3. Mark the job `failed` with `attempts` recorded, and make the **client**
   responsible for the retry decision, because only the client knows whether a
   partial caption is useful.
4. Expose `stream_interrupted_total` as an SLI; it should be ~0, and a non-zero
   rate is a worker-health problem, not a retry-policy problem.

The async contract does not have this problem at all: a job whose worker dies
pre-completion goes back on the queue via lease expiry (§6.2), invisibly. That
is a second, quiet argument for pushing long work to `/v1/jobs`.

### 5.3 Hedging

Hedged requests (issue a duplicate to a second worker at p95 latency, take the
first response, cancel the loser) are a genuine p99 win in the literature
— and a genuinely bad idea for us right now:

- One worker. There is nowhere to hedge to.
- Our service time is **5.1 s at c=8** and dominated by a *deterministic* CPU
  stage, not by a heavy tail. Hedging pays when the tail is caused by unlucky
  scheduling, not by the request genuinely being expensive.
- A hedge doubles GPU cost for the hedged fraction, on the most contended
  resource we own.

**Decision: no hedging until (a) ≥3 workers, (b) measured p99/p50 > 3, and (c)
the §4 transcode stage has landed** (which removes most of the variance that
would tempt us). Revisit with the measured distribution, not with the
assumption. See
[`03-concurrency-and-admission-control.md` §3.8](../scaling/03-concurrency-and-admission-control.md)
for the client-side retry/backoff/hedging treatment.

### 5.4 Poison-request quarantine

A request that crashes or wedges a worker, and is then retried onto the next
worker, takes the fleet down one box at a time. The guard is small and must
exist before we have more than one worker:

```
on job dispatch:      HINCRBY job:{id} attempts 1
on worker crash/OOM:  if attempts ≥ 2 → status = quarantined, do not requeue
                      publish to poison stream with the request *shape*
                      (duration, resolution, codec, prompt length — never content)
```

Two attempts, not three: the second failure on a *different* worker is already
strong evidence the request is the cause. Quarantined jobs return
`422 request_rejected` with an id the user can quote to support. A daily digest
of poison shapes is how we discover, e.g., that AV1 clips or 0-duration files
kill the decoder.

Complementary engine-level guard: `--video-pruning` and `frame_recovery`
(*"if a target frame fails to load during sequential reading, the next
successfully grabbed frame… will be used in its place"*
[src](https://github.com/vllm-project/vllm/blob/main/docs/features/multimodal_inputs.md),
default `false`) make truncated/corrupt files survivable rather than fatal.
Enable `frame_recovery: true` — for a captioning API a slightly wrong frame beats
a 500.

---

## 6. Multi-worker routing

### 6.1 Least-outstanding-requests vs queue-pull

| | Push / LOR | Queue-pull |
|---|---|---|
| Who decides placement | router, at arrival | worker, when free |
| A request is committed to a worker… | immediately | never until it starts |
| Worker dies with queued work | that work is **lost** unless the router tracks it | lease expires, work returns |
| Late-binding (send to the best worker at dispatch time) | no | **yes** |
| Prefix-cache-aware placement | natural (router knows cache state) | needs a scorer at pull time |
| Scale-down | must drain the worker's local queue | worker just stops pulling |
| Implementation | simple | needs a lease ledger |

**Recommend queue-pull for no-drop semantics.** The reason is not load
balancing, it is failure semantics: with pull, the *only* thing a worker owns is
what it is actively executing, so "no drop" reduces to "leases expire", which is
a single timer. With push, every worker-local queue is a small pool of requests
that a crash deletes, and the router must shadow all of them to recover — which
is queue-pull with extra steps.

This is also llm-d's conclusion, framed as *"No-Regret Scheduling: Hold requests
during peak saturation instead of committing them to a server's local queue
where they become stuck"*
[src](https://github.com/llm-d/llm-d/blob/main/guides/flow-control/README.md)
(*citation corrected 2026-09-20: this sentence is in the flow-control guide's
`README.md`, not `tuning.md`*),
and Dynamo's, whose router queue exists so that *"a request parked at the router
can still be sent to whichever replica is best when capacity appears, whereas a
request parked in an engine's waiting queue is already committed to that
replica"* (quoted from
[`03-concurrency-and-admission-control.md` §3.3](../scaling/03-concurrency-and-admission-control.md)).

**The one thing pull loses is cache-affinity routing**, which matters a lot for
text LLMs with long shared prefixes and rather little for us: Marlin's prompt is
~2K tokens of video with a fixed short instruction, so cross-request prefix reuse
is small; the reuse that matters is the *multimodal* cache, which §4.3 handles by
content hash. Recover affinity cheaply when we want it by having the dispatcher
prefer a worker whose L1 cache already holds the clip's hash — a set membership
test, not a scheduler.

### 6.2 The lease protocol

```
worker loop:
    if inflight_video_seconds >= WORKER_BUDGET_VIDEO_SECONDS: sleep(10ms); continue
    job = BLMOVE q:{m}:ready  q:{m}:leased:{worker}  LEFT RIGHT  timeout=5
    ZADD lease:{m}  now + LEASE_TTL  job.id
    HSET job:{job.id} worker {id} state running started_at now
    ... execute ...
    LREM q:{m}:leased:{worker} 1 job.id ; ZREM lease:{m} job.id ; HSET job state ...

worker heartbeat (every LEASE_TTL/3):
    ZADD lease:{m} now + LEASE_TTL  <each in-flight job id>      # extend

reaper (in every gateway, leader-elected via SET NX):
    for id in ZRANGEBYSCORE lease:{m} -inf now:
        attempts = HINCRBY job:{id} attempts 1
        if attempts >= MAX_ATTEMPTS: quarantine(id)              # §5.4
        elif tokens_delivered(id) > 0: fail(id, "stream_interrupted")   # §5.2
        else: LPUSH q:{m}:ready id ; ZREM lease:{m} id           # requeue at the head
```

`BLMOVE` is the primitive: *"Pops an element from a list, pushes it to another
list and returns it. Blocks until an element is available otherwise"*, O(1),
since Redis 6.2.0 [src](https://redis.io/docs/latest/commands/blmove/). The
destination list is the worker's lease list, so the move is atomic and a crash
between pop and `ZADD` still leaves the job discoverable in
`q:{m}:leased:{worker}`.

`LEASE_TTL` must exceed p99 total service time. From §0.1, p99 service is well
under 30 s for ≤120 s clips; **`LEASE_TTL = 120 s`** with heartbeats every 40 s.
If we ever move this to SQS, the same number becomes the visibility timeout
(default 30 s, max 12 h
[src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.html)).

**Priority and fairness live in the dispatcher, not in `BLMOVE`.** A Redis list
is FIFO only. So: a small dispatcher coroutine in the gateway pops from the WFQ
structure (§2.3) and `RPUSH`es onto `q:{m}:ready`, keeping `ready` short
(≤ `Σ workers × (N+B)`) so that ordering decisions stay late. `ready` is the
late-binding window; everything behind it is still re-orderable.

### 6.3 Health checks and draining

**Health.** `/health` today proxies vLLM's `/health` and reports `inflight`.
Split it, because an ALB needs a different question than an operator:

| Endpoint | Answers | Used by |
|---|---|---|
| `/livez` | is the process alive | systemd `Restart=always` |
| `/readyz` | vLLM up **and** model loaded **and** a real 1-token generation succeeded in the last 60 s | ALB target-group health check, and the worker's own "should I pull?" gate |
| `/health` | the current rich JSON, plus queue depth, μ̂, worker states | operators, dashboards |

A vLLM that answers `/health` but cannot generate is the NCCL-hang failure mode
([`08-reliability-and-operations.md` §2.6](../scaling/08-reliability-and-operations.md));
only a synthetic generation catches it. Run the canary from the worker loop, not
from the ALB, so it costs one request a minute rather than one per health check.

**Draining, on scale-down or deploy.** Order matters:

1. Mark the worker `draining` in Redis. It stops pulling. **It keeps
   heartbeating its existing leases.**
2. Deregister from the ALB target group. ALB waits
   `deregistration_delay.timeout_seconds`, *"By default… 300 seconds"*, and
   *"If a deregistering target has no in-flight requests and no active
   connections, Elastic Load Balancing immediately completes the deregistration
   process"* — but also, critically, *"If a deregistering target terminates the
   connection before the deregistration delay elapses, the client receives a
   500-level error response"*
   [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html).
   So the SIGTERM handler must outlive the in-flight requests, not race them.
3. `systemd` `TimeoutStopSec` ≥ `GENERATION_TIMEOUT_S` + margin (currently
   unset in `deploy/marlin2b-gateway.service` ⇒ the 90 s default ⇒ **we SIGKILL
   mid-generation today**). Set `KillSignal=SIGTERM`, `TimeoutStopSec=930` —
   > ALB `deregistration_delay` 900 ([`09` §0.2, §1.3](09-blueprint.md));
   `DRAIN_TIMEOUT_S=180` bounds the normal case, 930 bounds the worst case.
   *(Note: [`09` §0.2](09-blueprint.md) supersedes this document's earlier 330,
   which sat 570 s inside the deregistration window and so produced exactly the
   500-level errors quoted in point 2.)*
4. On SIGTERM: stop pulling, finish in-flight, then exit. Any job still running
   at the hard deadline is released (lease `ZREM` + requeue) rather than left to
   expire — a clean handover instead of a 120 s stall.
5. Only then terminate the instance. With an ASG, this is a lifecycle hook, not
   a hope.

**Scale-down anti-thrash** (cooldowns, KV-aware selection) is
[`05-autoscaling-and-predictive-scaling.md` §5](../scaling/05-autoscaling-and-predictive-scaling.md);
not repeated here. The queue-native scale signal, when we get to Kubernetes, is
KEDA's Redis list scaler (`listName`, `listLength` as the *average target value*,
`activationListLength` for scale-from-zero
[src](https://keda.sh/docs/2.17/scalers/redis-lists/)) or its SQS equivalent
[src](https://keda.sh/docs/2.17/scalers/aws-sqs/); on plain EC2 today, a
CloudWatch custom metric `infrx_queue_depth_per_worker` published by the gateway
feeds an ASG target-tracking policy with the same arithmetic.

---

## 7. `gateway.py` v2 — concrete design

### 7.1 Components

```
apps/infrx-api/
  gateway.py            FastAPI app: auth, admission, sync + async endpoints, SSE
  queue.py              Redis queue: enqueue, dispatch (WFQ), lease, reap, estimate
  media.py              fetch (SSRF-guarded) → probe → transcode → content cache
  worker.py             the pull loop: BLMOVE → media → vLLM → result + usage
  usage.py              usage_events queue + jsonl spill (moved out of gateway.py)
  config.py             every knob in §7.4, one place, env-driven
  deploy/
    marlin2b-gateway.service   (+ TimeoutStopSec, + MemoryMax)
    marlin2b-worker.service    NEW
    Caddyfile                  unchanged
```

`gateway.py` is currently **346 lines** (`wc -l`, 2026-09-20; an earlier draft
said 380) doing seven jobs. The split is not
architecture astronautics — it is the minimum needed for `worker.py` to run on a
*different machine* than `gateway.py`, which is the whole point of §6.

**Single-box mode must keep working.** With `REDIS_URL` unset, `queue.py` uses an
in-process `asyncio` implementation and `worker.py` runs as a task inside the
gateway process. This preserves the existing property that the tests run with no
env vars and no network (`tests/test_gateway_auth.py`), and it means the first
deploy of v2 changes no infrastructure.

### 7.2 Redis keys

| Key | Type | TTL | Contents |
|---|---|---|---|
| `q:{model}:{band}:{org}` | LIST | — | job ids, FIFO within tenant |
| `q:{model}:{band}:active` | ZSET | — | org → virtual finish time (WFQ) |
| `q:{model}:ready` | LIST | — | dispatched, awaiting `BLMOVE` by a worker |
| `q:{model}:leased:{worker}` | LIST | — | this worker's in-flight job ids |
| `lease:{model}` | ZSET | — | job id → lease expiry (unix seconds) |
| `job:{id}` | HASH | 24 h | envelope, state, attempts, worker, result ref, timings |
| `job:{id}:stream` | STREAM | 1 h | SSE chunks, for attach/reconnect (§7.3) |
| `svc:{model}` | HASH | — | `mu_ewma`, `s_ewma_{bucket}`, `lambda_ewma` |
| `tb:{org}` | HASH | 1 h | token bucket `{tokens, ts}` (requests, video-seconds) |
| `idem:{org}:{key}` | STRING | 24 h | job id + request hash |
| `mm:{sha256}` | HASH | 7 d | transcode cache index (§4.3) |
| `workers` | HASH | — | worker id → `{state, budget, inflight, last_seen}` |
| `lock:reaper` | STRING | 30 s | `SET NX` leader election |

### 7.3 Endpoints

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/v1/chat/completions` | auth → bucket → media stage → admission → enqueue → **wait inline**. Streaming: SSE with keep-alives from the moment of enqueue. Non-streaming: hold until done, or 202/429 per §1.5 |
| `POST` | `/v1/jobs` | same pipeline, always returns **202** + `Location` |
| `GET` | `/v1/jobs/{id}` | job state; `Retry-After` hint; result inline when terminal |
| `GET` | `/v1/jobs/{id}/events` | SSE over `job:{id}:stream`, resumable via `Last-Event-ID` (the MDN/EventSource reconnection mechanism [src](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)) |
| `DELETE` | `/v1/jobs/{id}` | cancel: dequeue if queued, abort if running (§3.3) |
| `POST` | `/v1/uploads` | presigned S3 `PUT` + `infrx://upload/{sha256}` handle |
| `GET` | `/v1/models` | unchanged (OpenRouter provider document) |
| `GET` | `/livez` `/readyz` `/health` | §6.3 |
| `GET` | `/metrics` | Prometheus: our queue + latency + admission counters |

Streaming a job through `job:{id}:stream` rather than straight from the worker
socket is what makes `/v1/jobs/{id}/events` resumable and lets a *sync* request
survive a gateway restart (the client reconnects with `Last-Event-ID` and picks
up). It costs one Redis stream write per token batch; batch chunks at ~50 ms to
keep that cheap.

### 7.4 Config knobs

| Env var | Default | Meaning |
|---|---|---|
| `REDIS_URL` | *(unset)* | unset ⇒ in-process queue, single-box mode |
| `WORKER_CONCURRENCY` | `10` | dispatch gate per worker = N + B |
| `WORKER_BUDGET_VIDEO_SECONDS` | `80` | admission budget in video-seconds (§3.2) |
| `MAX_QUEUE_HARD` | `500` | absolute queue cap |
| `MAX_QUEUE_BYTES` | `268435456` | envelope memory cap |
| `SYNC_MAX_WAIT_S` | `30` | streaming sync ceiling |
| `SYNC_NOSTREAM_MAX_WAIT_S` | `10` | non-streaming sync ceiling |
| `MAX_PROMISED_WAIT_S` | `600` | async ceiling; beyond ⇒ 429 |
| `SSE_KEEPALIVE_S` | `10` | keep-alive comment interval |
| `LEASE_TTL_S` | `120` | lease expiry; heartbeat at TTL/3 |
| `MAX_ATTEMPTS` | `2` | before quarantine |
| `RETRY_BUDGET_FRAC` | `0.10` | global retry circuit breaker |
| `FETCH_TIMEOUT_S` / `PROBE_TIMEOUT_S` / `TRANSCODE_TIMEOUT_S` | `30` / `10` / `auto` | §3.4 |
| `TTFT_TIMEOUT_S` / `TPOT_STALL_S` / `GENERATION_TIMEOUT_S` | `60` / `20` / `300` | §3.4 |
| `TRANSCODE_WORKERS` | `2` | bounded executor; **not** the default pool |
| `TRANSCODE_TARGET_PX` | `448` | short edge |
| `MEDIA_CACHE_DIR` / `MEDIA_CACHE_BYTES` / `MEDIA_CACHE_S3` | `/opt/dlami/nvme/cache` / `50Gi` / *(unset)* | §4.3 |
| `ALLOW_PRIVATE_FETCH` | `false` | SSRF guard escape hatch, for tests only |
| `MAX_VIDEO_SECONDS` / `MAX_VIDEO_MB` | `120` / `64` | unchanged |
| `MAX_INFLIGHT` | **removed** | replaced by the three limits of §3.2 |

### 7.5 Behaviour under overload — the staircase

One worker, `WORKER_CONCURRENCY=10`, μ̂ = 3.58 clips/s (post-transcode), and
increasing offered load λ:

| λ (req/s) | Queue depth | Sync streaming | Sync non-streaming | Async | GPU |
|---|---|---|---|---|---|
| < 3.58 | 0 | 200, TTFT ≈ 0.7 s | 200 | 202, ETA ≈ 0 | not saturated |
| 3.6–4 | grows slowly | 200 + keep-alives | 200 | 202 | saturated, N=8 running |
| 4–8 | ~100 in 30 s | 200 while ETA ≤ 30 s, then **429 → use `/v1/jobs`** (or 202 with `X-Infrx-Accept-Async`) | **429** above ETA 10 s | 202, honest ETA | saturated |
| > 8 sustained | hits `MAX_QUEUE` = min(600×3.58×0.8, 500) = 500 | 429 + `Retry-After` = drain estimate, jittered | 429 | 429 | saturated; **autoscaler already firing at depth/worker > 30** |
| any, worker dies | leases expire within 120 s | in-flight streams get `stream_interrupted`; queued work untouched | ditto | invisible; jobs requeue | — |
| any, Redis down | — | **503 + `Retry-After: 5`** (never a 200 we cannot honour) | 503 | 503 | — |
| any, Supabase down | — | cached keys keep working; unknown keys 503 (**unchanged behaviour**) | — | — | — |

Nothing in that table is a drop: every cell is either served, queued with a
stated ETA, or refused before any work was committed with a computed retry time.

The one honest failure is `stream_interrupted`, and it is bounded by
worker-death rate, not by load.

### 7.6 Pseudo-code

```python
# ---- admission -------------------------------------------------------------
@app.post("/v1/chat/completions")
@with_cancellation                      # vLLM's pattern, §3.3
async def chat(request: ChatRequest, raw_request: Request):
    org = await authenticate(raw_request)          # unchanged, 60 s cache
    band = band_for(org)                           # interactive|standard|free

    if not await buckets.consume(org, requests=1, video_seconds=None):
        return err(429, "rate_limit_error", retry_after=buckets.retry_after(org))

    # idempotency, Stripe semantics (§2.6)
    if key := raw_request.headers.get("idempotency-key"):
        if prior := await idem.lookup(org, key, request_hash(request)):
            return await replay(prior)

    # media stage runs BEFORE admission: we need video_seconds to size the job,
    # and a cache hit makes the job nearly free.  (§4)
    try:
        media = await media_stage(request)         # fetch→probe→transcode→cache
    except MediaError as e:
        return err(e.status, e.type, message=str(e))

    if not await buckets.consume(org, video_seconds=media.duration):
        return err(429, "rate_limit_error", retry_after=buckets.retry_after(org))

    # admission (§2.5)
    est = await q.estimate(model, band)            # (eta_s, depth, mu_hat)
    if est.depth + 1 > q.max_queue(band) or est.eta_s > MAX_PROMISED_WAIT_S:
        return err(429, "rate_limit_error",
                   retry_after=jitter(est.drain_s),
                   message=f"queue full; retry, or POST /v1/jobs "
                           f"(current wait ~{est.eta_s:.0f}s)")

    job = await q.enqueue(org, band, model, request, media, idem_key=key)

    wants_stream = bool(request.stream)
    ceiling = SYNC_MAX_WAIT_S if wants_stream else SYNC_NOSTREAM_MAX_WAIT_S
    if est.eta_s > ceiling:
        if raw_request.headers.get("x-infrx-accept-async") == "1":
            return accepted_202(job, est)
        await q.cancel(job.id)                     # never leave an orphan
        return err(429, "rate_limit_error", retry_after=jitter(est.eta_s),
                   message="predicted wait exceeds the synchronous limit; "
                           "use POST /v1/jobs")

    if wants_stream:
        return StreamingResponse(
            with_sse_keep_alive(stream_job(job, est), SSE_KEEPALIVE_S),
            media_type="text/event-stream",
            headers={"Inference-Id": job.id, "X-Infrx-ETA-Seconds": f"{est.eta_s:.1f}"})
    return await await_job(job, timeout=GENERATION_TIMEOUT_S)


async def stream_job(job, est):
    # position updates while queued; comments are ignored by SSE clients
    yield f": {json.dumps({'queue_position': est.depth, 'eta_s': est.eta_s})}\n\n"
    async for chunk in q.follow(job.id):           # reads job:{id}:stream
        yield chunk
    # the worker writes the terminal frame, including any error


# ---- dispatcher: WFQ -> ready list (§2.3, §6.2) ----------------------------
async def dispatch_loop(model):
    while True:
        if await q.ready_len(model) >= ready_high_water(model):
            await asyncio.sleep(0.01); continue
        picked = None
        for band in (100, 0, -10):                 # strict priority
            if org := await q.least_virtual_time(model, band):
                picked = await q.pop_tenant(model, band, org); break
        if picked is None:
            await asyncio.sleep(0.02); continue
        await q.push_ready(model, picked)


# ---- worker: pull, execute, account (§6.2) ---------------------------------
async def worker_loop(worker_id, model):
    while not draining:
        if inflight_video_seconds >= WORKER_BUDGET_VIDEO_SECONDS:
            await asyncio.sleep(0.01); continue
        job = await q.lease(model, worker_id, ttl=LEASE_TTL_S, block=5)
        if job is None: continue
        asyncio.create_task(run(job, worker_id))

async def run(job, worker_id):
    t0 = time.monotonic(); first = None; tokens = 0
    try:
        body = build_vllm_body(job)                # + "uuid": f"sha256:{job.media.hash}"
        async with vllm.stream("POST", "/v1/chat/completions", json=body) as r:
            async for chunk in with_stall_timeout(r.aiter_lines(), TPOT_STALL_S,
                                                  first_token_timeout=TTFT_TIMEOUT_S):
                first = first or time.monotonic()
                tokens += 1
                await q.publish(job.id, strip_think(chunk))
        await q.complete(job.id, worker_id, usage=...)
    except FirstTokenTimeout:
        await q.release_for_retry(job.id, worker_id)          # safe: pre-prefill (§5.1)
    except Exception as e:
        if tokens: await q.fail(job.id, "stream_interrupted") # not resumable (§5.2)
        else:      await q.release_for_retry(job.id, worker_id)
    finally:
        usage.enqueue(row_for(job, t0, first, tokens))        # one row per JOB (§2.7)


# ---- reaper: the entire no-drop guarantee, in eight lines (§6.2) -----------
async def reaper(model):
    while await redis.set("lock:reaper", me, nx=True, ex=30):
        for job_id in await redis.zrangebyscore(f"lease:{model}", "-inf", now()):
            attempts = await redis.hincrby(f"job:{job_id}", "attempts", 1)
            if attempts >= MAX_ATTEMPTS:      await q.quarantine(job_id)      # §5.4
            elif await q.tokens_sent(job_id): await q.fail(job_id, "stream_interrupted")
            else:                             await q.requeue_head(job_id)
        await asyncio.sleep(5)
```

### 7.7 Build order

| Step | Change | Unlocks | Size |
|---|---|---|---|
| 1 | Fix the `inflight` accounting bug (§8 item 1), bounded `ffprobe` executor, SSRF guard, streamed size cap | correctness + security, today, no new deps | hours |
| 2 | SSE keep-alives + `with_cancellation` + per-phase timeouts | long waits survive proxies; hung clients stop burning GPU | hours |
| 3 | `media.py`: transcode + content-hash cache + `uuid` to vLLM | **~2.3× throughput** (meas.) | 1–2 days |
| 4 | In-process bounded queue + wait estimation + honest 429; `MAX_INFLIGHT` removed | no-drop semantics on one box | 1–2 days |
| 5 | `/v1/jobs`, `/v1/uploads`, `job:{id}:stream` | unbounded-wait workloads; presigned uploads | 2–3 days |
| 6 | Redis queue + `worker.py` + leases + reaper + WFQ + buckets | multi-worker, fairness, priority | ~1 week |
| 7 | ALB + ASG + `/readyz` + drain hooks + queue-depth CloudWatch metric | autoscaling, multi-AZ | ~1 week |

Steps 1–4 are worth doing even if we never add a second GPU. Step 3 alone
probably buys more capacity than the first extra instance would, at zero
marginal cost — which is the argument for doing it before any autoscaling work.

---

## Implications for our system

In the order the changes should land.

1. **`apps/infrx-api/gateway.py` has a concurrency-accounting defect that
   disables the only limiter we have.** In the non-streaming path,
   `inflight -= 1` runs *before* `data = r.json()`; if `r.json()` raises (a
   truncated or non-JSON upstream response), the outer `except` decrements
   again. `inflight` then drifts negative and `if inflight >= MAX_INFLIGHT`
   never fires again until restart — unbounded concurrency after the first such
   error. Fix with a single `try/finally` around the whole request, or a
   `contextlib.asynccontextmanager` slot, and add a regression test to
   `tests/test_gateway_auth.py`. **Do this first, it is a one-line class of
   fix.**

2. **`gateway.py`'s `video_seconds()` is an unauthenticated SSRF primitive and
   an OOM.** It fetches any user-supplied URL with `follow_redirects=True` and
   checks `MAX_VIDEO_MB` only after buffering the whole body. On EC2 that
   reaches `169.254.169.254` and the VPC. Add the §4.2 guard (resolve → validate
   → connect-by-IP, manual redirects, streamed cap), enforce IMDSv2 hop-limit 1
   on the instance, and restrict the egress security group.

3. **Replace the `MAX_INFLIGHT=16` early-429 with the three limits of §3.2.**
   Re-pin `--max-num-seqs` **in `apps/infrx-api/deploy/marlin2b-vllm.service`**,
   where it is currently **32** — *corrected 2026-09-20; an earlier draft said it
   was unset and pointed at `serve.sh`, which forwards the flag but does not set
   it.* 32 was never measured, and it sits behind a gateway that hard-refuses at
   16, so the engine's real ceiling is the gateway's. Set the gateway's
   per-worker gate to N + 2 and put the burst queue behind it; `openrouter/PLAN.md:63`
   needs the same edit. Run llm-d's tuning procedure with a step-load mode
   added to `models/marlin2b/bench.py` to replace the guessed N.

4. **Add the video transcode + content-hash cache stage (`media.py`).** Our own
   measurement says ≤448 px inputs are worth 1.57 → 3.58 clips/s on this box,
   and the cache removes the duplicate download that currently makes a 10 s clip
   take 3.8 s end to end. Send `"uuid": "sha256:<h>"` on the `video_url` part so
   vLLM's processor cache keys on the same identity; consider raising
   `mm_processor_cache_gb` above its 4 GiB default on the 48 GB L40S.

5. **Bound the `ffprobe` executor and move transcode off `run_in_executor(None)`.**
   `g6e.2xlarge` gives 4 physical cores against vLLM's documented 3-core floor
   for one GPU. Until the CPU tier moves off-box (§4.4 option D), every
   unbounded thread pool in the gateway is stealing from the engine core that
   vLLM says is *"particularly sensitive to CPU starvation."*

6. **Add SSE keep-alive comments and client-disconnect cancellation.** Copy
   vLLM's `with_sse_keep_alive` and `with_cancellation` (Apache-2.0, ~60 lines
   each). Without the first, any queue wait past the proxy idle timeout is a
   dropped request; without the second, a client that hangs up keeps burning the
   GPU to completion.

7. **Split `gateway.py` into gateway / queue / media / worker / usage.** Not for
   tidiness — so `worker.py` can run on a different machine, which is the
   prerequisite for every multi-worker item below. Keep the `REDIS_URL`-unset
   in-process path so the existing no-network tests keep passing.

8. **Add `/v1/jobs` (+ `/v1/uploads`, `/v1/jobs/{id}/events`) and document both
   contracts** in the console's Docs page (`apps/app`, F7) and the model's
   `limits` JSON in the `models` table (`apps/README.md` §6) — clients need to
   see `max_video_seconds`, `sync_max_wait_s` and the async endpoint. The
   console's Usage page gains `queue_wait_ms` and `eta_error`.

9. **Extend `usage_events`:** `queue_wait_ms`, `attempts`, `eta_s_predicted`,
   `cached boolean` set for real (it is hard-coded `False` today), and a
   `cancelled` status. Keep one row per **job**, not per attempt, so retries
   bill once. The `usage.jsonl` + `replay_usage.py` durable path stays exactly
   as it is.

10. **Fix draining before adding a second worker.**
    `deploy/marlin2b-gateway.service` sets no `TimeoutStopSec`, so systemd's 90 s
    default SIGKILLs mid-generation. Set `TimeoutStopSec=930` (> ALB
    `deregistration_delay` 900, [`09` §0.2, §1.3](09-blueprint.md);
    `DRAIN_TIMEOUT_S=180` bounds the normal case, 930 bounds the worst case —
    `09` §0.2 supersedes this document's earlier 330), add a SIGTERM
    handler that stops pulling and finishes in-flight, and — when the Elastic IP
    becomes an ALB — align `deregistration_delay.timeout_seconds` (default 300 s)
    with it, because a target that closes early gives the client a 500.

11. **Move to an ALB (or at least raise the timeouts consciously) before
    multi-AZ.** Set `idle_timeout.timeout_seconds` well above 60 s. Do **not**
    put CloudFront in the inference path: its origin response timeout is 30 s by
    default, with a **default quota** of 1–120 s that AWS will raise on request
    (*corrected 2026-09-20 — an earlier draft called 120 s a hard ceiling*), and
    its response-completion timeout is not idle-based at all.

12. **Redis (ElastiCache) becomes a dependency at multi-worker, and it must fail
    closed.** Redis down ⇒ 503 + `Retry-After`, never a 200 we cannot honour —
    the same discipline `authenticate()` already applies to Supabase. Single-box
    mode must keep working with `REDIS_URL` unset.

13. **Publish `infrx_queue_depth_per_worker` to CloudWatch from the gateway** and
    drive the ASG target-tracking policy from it, not from GPU utilisation. On
    Kubernetes later this becomes the KEDA Redis-list scaler with the same
    arithmetic. Capacity planning, warm pools and the `g6e` scarcity problem are
    [`05`](../scaling/05-autoscaling-and-predictive-scaling.md) and
    [`06`](../scaling/06-cold-start.md), not this document.

---

## Open questions

- ⚠️ **`--max-num-seqs` is 32 today, and 32 was never measured.** *(Corrected
  2026-09-20: this question previously read "what is it actually set to?" on the
  premise that `serve.sh` does not pass it. `serve.sh` does not, but
  `apps/infrx-api/deploy/marlin2b-vllm.service` does —
  `ExecStart=… serve.sh --max-num-seqs 32`.)* The open part is now narrower and
  sharper: **32 is 4× the only concurrency we have benchmarked**, and it is
  masked by `MAX_INFLIGHT=16`, so we have never observed the engine at its own
  limit. Every capacity number in §3.2 stays provisional until N is re-measured
  by step-load and re-pinned in the **unit file**. *On reading the effective
  value back, corrected 2026-09-20:* `vllm:cache_config_info` carries the **`CacheConfig`** labels
  (`block_size`, `cache_dtype`, `cpu_offload_gb`, `enable_prefix_caching`,
  `gpu_memory_utilization`, …) — `max_num_seqs` is a **`SchedulerConfig`**
  field and is **not** in that metric
  [src](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md).
  Read the effective value from the engine's startup log line, from
  `GET /v1/models`' served config, or by passing it explicitly; use
  `vllm:cache_config_info` for the KV-block half of the §3.2 arithmetic only.
- ⚠️ **The service-time distribution is unknown.** We have three means and no
  variance, so the §2.4 residual term uses the exponential assumption
  (`0.5 × ŝ`). Replace with measured `E[S²]/(2E[S])` after a week of
  `usage_events`. Until then every published ETA is optimistic by an unknown
  amount.
- ⚠️ **Mixed-length concurrency is unmeasured.** `results/notes.md` already lists
  this as open. The `WORKER_BUDGET_VIDEO_SECONDS = 80` figure is derived from a
  single measured point (c=8 × 10 s clips) and could be badly wrong for a batch
  of 120 s clips, which are ~23.5K tokens each against a 32,768-token window.
- ⚠️ **Transcoded file size and transcode wall time are unmeasured.** §4.2 asserts
  a 5.5 MB 1080p source becomes ~100–200 KB; that needs one `ffmpeg` run on
  `sample-10s.mp4`, and the transcode's own CPU cost must be subtracted from the
  2.3× before it is quoted to anyone.
- ⚠️ **Cache hit rate in real traffic is unknown**, and it is the number that
  decides whether §4.3 is a 2× or a 5×. Instrument `mm:{h}` hit/miss from day
  one of the transcode stage, before optimising anything else.
- ⚠️ **NVDEC (`pynvvideocodec` / `deepstream`) is untested here.** It requires
  CUDA MPS and `--mm-ipc-gpu-memory-gb` carved out of the KV cache. It could make
  §4's whole CPU tier unnecessary, or it could cost more KV than it saves CPU.
  One benchmark decides it.
- ~~⚠️ **`g6e` pricing is not in [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md).**~~
  **Closed 2026-09-20.** `g6e.2xlarge` = **$2.24208/h** and `g6e.4xlarge` =
  **$3.00424/h**, us-east-1, Shared tenancy, Linux, from the AWS Price List
  [src](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.csv)
  — pinned in [`01` §3.8–3.9](01-requirements-and-traffic-model.md) and
  [`05` sources](05-caching.md). The residual action is unchanged and belongs to
  the other tree: *add a `g6e` row to
  [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md)*, which still has
  none, so §4.4 option C's "~2× instance cost ⚠️" can be replaced by the
  measured **+34 %** ($2.24208 → $3.00424) that `01` §3.9 computes.
- ⚠️ **Does `g6e.4xlarge` (16 vCPU) exist with capacity in `us-east-1d`?** The
  whole "just add vCPUs" option (§4.4 option C) depends on it, and we already
  know `g6e` capacity in `us-east-1` is scarce enough that only one AZ had any.
  Check before designing around it.
- ⚠️ **SQS fair queues at our scale.** AWS's 30-in-flight-message threshold means
  the concurrency-share detector cannot fire below ~4 workers. Worth re-testing
  when we get there; until then the fairness logic is ours and unproven.
- ⚠️ **Streaming replay under an idempotency key** is specified here as "final
  completion in one frame", which is a behaviour no other API we know of
  documents. Worth checking what OpenAI/Anthropic actually do before we commit
  it to our docs.
- ⚠️ **Result retention breaks the "no content stored" promise** in
  `apps/README.md` §4 for the async contract. 24 h default is proposed; it needs
  an explicit product decision and a per-org override before `/v1/jobs` ships.
- ⚠️ **Does vLLM's `uuid` field survive our `--hf-overrides` remap?** Marlin is
  loaded as `Qwen3_5ForConditionalGeneration`; the multimodal cache path should
  be identical, but "parity between the two is the first thing to check on a new
  engine version" (`models/marlin2b/README.md`) applies here too.

---

## Sources

All fetched **2026-09-20**.

**AWS — edge, load balancing, queueing, scaling**

1. [Edit attributes for your Application Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) — connection idle timeout (default 60 s, range 1–4000 s), HTTP client keepalive duration (default 3600 s, range 60–604800 s), no HTTP/2 PING support.
2. [Edit target group attributes for your Application Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html) — `deregistration_delay.timeout_seconds` default 300 s; early connection close ⇒ 500-level error to the client.
3. [Network Load Balancers](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/network-load-balancers.html) — TCP idle timeout default 350 s (60–6000 s), TLS listener 350 s fixed with 20 s injected keepalives, UDP 120 s fixed, ENI `TcpEstablishedTimeout` interaction.
4. [CloudFront origin settings](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesOrigin.html) — response timeout default 30 s and its idle semantics, connection timeout default 10 s, keep-alive default 5 s, response completion timeout.
5. [CloudFront quotas](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cloudfront-limits.html) — response timeout per origin 1–120 s, keep-alive 1–300 s, both quota-increase-able.
6. [Amazon SQS message quotas](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.html) — 1 MiB max message, retention 60 s–14 d (default 4 d), visibility 0 s–12 h (default 30 s), FIFO 300 TPS/partition non-batched and 3,000 msg/s batched.
7. [Amazon SQS visibility timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) — ~120,000 in-flight standard-queue cap and long-polling behaviour at the cap.
8. [High throughput for FIFO queues](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/high-throughput-fifo.html) — partitions, message-group hashing, 3,000 msg/s per partition with batching.
9. [How Amazon SQS fair queues work](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-fair-queues-detailed.html) — `MessageGroupId` as tenant identity; noisy-neighbour thresholds (>10 % concurrency share **and** ≥30 in-flight, or >10 % processing-time share); 5-minute quiet period; the consumer-concurrency caveat.
10. [Amazon SQS short and long polling](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-short-and-long-polling.html).
11. [Decrease latency for applications with long boot times using warm pools](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html) — `Stopped` / `Running` / `Hibernated` states, `MinSize`, `MaxGroupPreparedCapacity`, lifecycle hooks.

**vLLM**

12. [vLLM metrics design doc](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md) — exact `vllm:` metric names and types, including `num_requests_running` / `_waiting`, `kv_cache_usage_perc`, `request_queue_time_seconds`, `request_success_total{finished_reason="abort"}`, and the TPOT definition.
13. [vLLM optimization and tuning](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md) — preemption and `RECOMPUTE` default; multimodal processor / IPC caching with `mm_processor_cache_gb` (default 4 GiB) and `mm_processor_cache_type="shm"`; `--api-server-count` and `VLLM_MEDIA_LOADING_THREAD_COUNT` (8 per API server); the `2 + N` physical-core floor and the "1 vCPU = ½ physical core" warning.
14. [vLLM multimodal inputs](https://github.com/vllm-project/vllm/blob/main/docs/features/multimodal_inputs.md) — `VLLM_VIDEO_FETCH_TIMEOUT` default 30 s; decoding backends `opencv` (default) / `torchcodec` / `pynvvideocodec` / `deepstream` and their parameters; `--mm-ipc-gpu-memory-gb` and the CUDA-MPS requirement; `multi_modal_uuids` / per-part `uuid` and skip-send semantics; `--video-pruning-rate` with `evs` / `vidcom2`; `frame_recovery`.
15. [vLLM `sse_keep_alive.py`](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/utils/sse_keep_alive.py) — the keep-alive-comment implementation and the docstring naming ALB / NGINX `proxy_read_timeout` as the motivating failure.
16. [vLLM `api_utils.py` (`with_cancellation`)](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/utils/api_utils.py) — why `request.is_disconnected` does not work behind middleware, and the two-task cancellation pattern.
17. [vLLM chat-completion API router](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/chat_completion/api_router.py) — `@with_cancellation`, `with_sse_keep_alive`, and `sse_keep_alive_interval` defaulting to 0.

**llm-d / gateway-layer queueing**

18. [llm-d — Flow Control](https://llm-d.ai/docs/well-lit-paths/foundations/flow-control) — `FlowKey` = Fairness ID + Priority; per-flow queues; priority → fairness → ordering; saturation-triggered queueing; "no-regret scheduling".
19. [llm-d — Flow Control guide](https://github.com/llm-d/llm-d/tree/main/guides/flow-control) — default plugins (`global-strict-fairness-policy`, `fcfs-ordering-policy`, `utilization-detector`), `maxRequests: 200`, `maxBytes: "10Gi"`, work-conserving behaviour.
20. [llm-d — Production tuning: deriving `maxConcurrency`](https://github.com/llm-d/llm-d/blob/main/guides/flow-control/tuning.md) — the 3-layer architecture (N / N+B / burst queue), `concurrency-detector` vs `utilization-detector` (50 ms scrape), `headroom`, the step-load tuning procedure and `tuning_wizard.py`.
21. [llm-d README](https://github.com/llm-d/llm-d) — project scope and the published routing/disaggregation benchmark claims.

**Queue and autoscaling primitives**

22. [Redis `BLMOVE`](https://redis.io/docs/latest/commands/blmove/) — *"Pops an element from a list, pushes it to another list and returns it. Blocks until an element is available otherwise."* O(1), since 6.2.0.
23. [KEDA — Redis Lists scaler](https://keda.sh/docs/2.17/scalers/redis-lists/) — `listName`, `listLength` as average target value, `activationListLength`.
24. [KEDA — AWS SQS Queue scaler](https://keda.sh/docs/2.17/scalers/aws-sqs/).
25. [KEDA — Scaling Deployments, StatefulSets & Custom Resources](https://keda.sh/docs/2.17/concepts/scaling-deployments/) — polling interval, `useCachedMetrics`, scale-to-zero on an empty queue.

**Protocol and API semantics**

26. [Stripe — Idempotent requests](https://docs.stripe.com/api/idempotent_requests) — stored status + body replay including 500s, ≤255-char keys, ≥24 h pruning, parameter-mismatch error, concurrent-execution conflict, POST-only.
27. [MDN — Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events) — comment lines as keep-alive, automatic reconnection, `retry`, `id` / Last-Event-ID.
28. [OWASP — Server Side Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) — the "requests to ANY external IP address or domain" case that ours is.

**Repo sources (measured, not external)**

29. [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) — the c=1 / c=8 / 1080p-vs-360p measurements, the video-budget finding, and the cost sketch. Measured 2026-09-19.
30. [`models/marlin2b/README.md`](../../models/marlin2b/README.md) — the results table, the 2 fps / 240-frame / 200,704 px budget, the 32,768-token window rationale, and the 3.8 s end-to-end / double-download measurement of 2026-09-20.
31. [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py), [`apps/infrx-api/README.md`](../../apps/infrx-api/README.md), [`apps/README.md`](../README.md), [`apps/infrx-api/deploy/`](../../apps/infrx-api/deploy/) — the current auth, video-budget, usage and deployment behaviour this document proposes to change.
32. [`research/scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md) — engine-internal queues, the status-code table (RFC 6585 / RFC 9110), Dynamo's `--router-queue-threshold` late-binding argument, the SLO-scheduling literature, and the multimodal concurrency wall. Research date 2026-09-19.

---

## Verification log (2026-09-20)

Adversarial fact-check of the 32 most consequential claims: AWS service limits
and quotas, vLLM flag/metric/file names, llm-d citations, protocol semantics,
every statement about this repo's code and measurements, and every derivation
(recomputed with `python3`). **22 CONFIRMED, 9 CORRECTED, 1 UNVERIFIABLE.**
Corrections are applied in place above and marked inline; nothing was removed.

### AWS — all CONFIRMED except the CloudFront ceiling

| # | Claim (§) | Verdict | Primary source |
|---|---|---|---|
| 1 | ALB `idle_timeout.timeout_seconds` default **60 s**, range **1–4000 s**; idle-resetting; *"Application Load Balancers do not support HTTP/2 PING frames. These do not reset the connection idle timeout."* (§1.2) | **CONFIRMED** verbatim | [ALB attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) |
| 2 | ALB `client_keep_alive.seconds` default **3600 s**, range **60–604800 s**, not idle-resetting (§1.2) | **CONFIRMED** verbatim, incl. *"does not reset until a new connection is established"* | same |
| 3 | NLB TCP idle **350 s default, 60–6000 s**; TLS listener **350 s not modifiable** with LB-injected keepalives **every 20 s**; UDP 120 s fixed (§1.2) | **CONFIRMED** verbatim | [NLB](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/network-load-balancers.html) |
| 4 | CloudFront origin **response timeout default 30 s**, **keep-alive 5 s (1–300 s)**, and the *response completion timeout* is not idle-based (§1.2) | **CONFIRMED** verbatim (*"The time (in seconds) that a request from CloudFront to the origin can stay open and wait for a response"*; *"If you don't set a value… CloudFront doesn't enforce a maximum value"*) | [origin settings](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesOrigin.html) |
| 5 | *"A hard 120 s ceiling on the response timeout even after a quota increase"* (§1.2, Implications 11) | **CORRECTED** — 1–120 s is the **default quota**, with a "Request a higher quota" link; the origin page documents requesting a timeout increase for the account. Reworded in both places; the do-not-use-CloudFront recommendation is unchanged and now rests on the response-completion timeout | [quotas](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cloudfront-limits.html) |
| 6 | SQS max message **1,048,576 B (1 MiB)**; retention **default 4 d, min 60 s, max 1,209,600 s**; visibility **default 30 s, max 12 h** (§2.1, §6.2) | **CONFIRMED** verbatim | [SQS message quotas](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.html) |
| 7 | FIFO **300 TPS per partition per API action**, **3,000 msg/s** batched; high-throughput FIFO **70,000 TPS** non-batched in **us-east-1** (§2.1) | **CONFIRMED** — us-east-1/us-west-2/eu-west-1 are the 70,000 TPS tier; all other regions are lower (2,400–19,000). Our conclusion ("TPS is not the reason to avoid FIFO") holds | same + [high-throughput FIFO](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/high-throughput-fifo.html) |
| 8 | Standard queues **~120,000 in-flight**; long polling returns nothing rather than an error at the cap (§2.1) | **CONFIRMED** — *"a limit of approximately 120,000 in-flight messages"*; short polling returns `OverLimit`, long polling *"does not return an error"* | [visibility timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) |
| 9 | Fair queues: **>10 % concurrency share AND ≥30 in-flight**, or **>10 % processing-time share**; the consumer-concurrency caveat (§2.1) | **CONFIRMED** verbatim, including *"Noisy neighbor messages are not dropped or throttled"* and the 5-minute quiet period | [fair queues](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-fair-queues-detailed.html) |
| 10 | `deregistration_delay.timeout_seconds` **default 300 s**; immediate completion with no in-flight requests; **500-level error** if the target closes early (§6.3) | **CONFIRMED** verbatim | [target group attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html) |

### vLLM — all CONFIRMED except one open-question action

| # | Claim (§) | Verdict |
|---|---|---|
| 11 | Metric names `vllm:num_requests_running` (*"Number of requests in model execution batches"*), `num_requests_waiting`, `kv_cache_usage_perc` (*"Fraction of used KV cache blocks (0–1)"*), `request_queue_time_seconds`, `time_to_first_token_seconds`, `request_time_per_output_token_seconds`, `request_success_total{finished_reason="abort"}`, `prefix_cache_hits` / `_queries`, `cache_config_info` (§3.1) | **CONFIRMED** — every name exists with that spelling, and the TPOT definition *"(end-to-end latency - TTFT) / (number of output tokens - 1)"* is verbatim. `finished_reason="abort"` appears in the doc's own sample scrape ([metrics.md](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)) |
| 12 | *Action: read `vllm:cache_config_info` off `/metrics`* to discover the effective `--max-num-seqs` (Open questions) | **CORRECTED** — that metric carries `CacheConfig` labels only (`block_size`, `cache_dtype`, `cpu_offload_gb`, `enable_prefix_caching`, `gpu_memory_utilization`, …). `max_num_seqs` is a `SchedulerConfig` field and is absent. Action rewritten |
| 13 | `--sse-keep-alive-interval` exists and defaults to **0 (off)** (§1.3) | **CONFIRMED** — `sse_keep_alive_interval: int = 0` in `vllm/entrypoints/launchers/cli_args.py`, *"Defaults to 0, which disables keep-alive comments entirely"*; the `getattr(args, "sse_keep_alive_interval", 0)` cited from `openai/chat_completion/api_router.py` is verbatim |
| 14 | The `sse_keep_alive.py` docstring quote and the file path `vllm/entrypoints/serve/utils/sse_keep_alive.py` (§1.3) | **CONFIRMED** — path returns 200 and the docstring matches word for word, including *"while a request is queued"* |
| 15 | The `with_cancellation` docstring and the path `vllm/entrypoints/serve/utils/api_utils.py` (§3.3) | **CONFIRMED** — verbatim, including the `StreamingResponse` hand-off paragraph |
| 16 | *"The minimum is `2 + N` physical cores"*, *"1 vCPU = 1 hyperthread = 1/2 physical CPU core"*, 8 media-loading threads per API server (`VLLM_MEDIA_LOADING_THREAD_COUNT`), `--api-server-count`, *"The engine core process runs a busy loop and is particularly sensitive to CPU starvation"* (§0.2, §4.1) | **CONFIRMED** verbatim ([optimization.md](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md)) |
| 17 | `mm_processor_cache_gb` **default 4 GiB**, `mm_processor_cache_type="shm"` (§4.3) | **CONFIRMED** |
| 18 | Video backends `opencv` (default) / `torchcodec` / `pynvvideocodec` / `deepstream`; the *"video tagging"* quote; CUDA MPS **required** for `pynvvideocodec`; `--mm-ipc-gpu-memory-gb` *"carves this budget out of the memory available to the KV cache"*; `frame_recovery` **default `false`**; `multi_modal_uuids` / per-part `uuid`; `VLLM_VIDEO_FETCH_TIMEOUT` **30 s** (§4.2–4.4, §5.4) | **CONFIRMED** verbatim ([multimodal_inputs.md](https://github.com/vllm-project/vllm/blob/main/docs/features/multimodal_inputs.md)) |
| 19 | `--video-pruning-rate` with `--video-pruning-method evs\|vidcom2` (§4.4) | **CONFIRMED**, with a **⚠️ added**: the same page restricts `vidcom2` to Qwen3-VL, so only `evs` is safe to assume for Marlin's remapped architecture |

### llm-d, Redis, KEDA, protocol semantics

| # | Claim (§) | Verdict |
|---|---|---|
| 20 | 3-layer model (N / N+B / burst queue ≥ 200), `maxRequests: 200`, `maxBytes: "10Gi"`, the `maxRequests`/`maxBytes` host-memory quote, 50 ms telemetry scrape, `concurrency-detector` vs `utilization-detector` descriptions, `--max-num-seqs 2048` step-load procedure, `tuning_wizard.py` (§2.5, §3.1, §3.2) | **CONFIRMED** verbatim in [`guides/flow-control/tuning.md`](https://github.com/llm-d/llm-d/blob/main/guides/flow-control/tuning.md) |
| 21 | *"production deployments should switch to `concurrency-detector`…"* (§3.1) and *"No-Regret Scheduling: Hold requests during peak saturation…"* (§6.1), both cited to `tuning.md` | **CORRECTED** — both sentences live in [`guides/flow-control/README.md`](https://github.com/llm-d/llm-d/blob/main/guides/flow-control/README.md). Text is verbatim; citations re-pointed, and the detector recommendation is now quoted in full |
| 22 | The `FlowKey` / priority-fairness-ordering block quoted in §2.2 | **CORRECTED** — substance holds, but the block was a **paraphrase set in quotation marks**. Replaced with the page's own wording: *"assign a `FlowKey` (tuple of `FairnessID` and `Priority`)"*, *"maintains separate in-memory queues for each `FlowKey`"*, *"traverses the queues in 3 tiers: Priority… Fairness… Ordering"* ([llm-d flow control](https://llm-d.ai/docs/well-lit-paths/foundations/flow-control)) |
| 23 | `BLMOVE`: *"Pops an element from a list, pushes it to another list and returns it. Blocks until an element is available otherwise."* **O(1)**, **since 6.2.0** (§2.1, §6.2) | **CONFIRMED** verbatim ([redis.io](https://redis.io/docs/latest/commands/blmove/)) |
| 24 | KEDA 2.17 Redis-lists scaler: `listName`, `listLength` as *"Average target value to trigger scaling actions"*, `activationListLength` (§6.4) | **CONFIRMED** ([keda.sh](https://keda.sh/docs/2.17/scalers/redis-lists/)) |
| 25 | Stripe idempotency: stored status+body replay incl. 500s, **≤255-char keys**, **≥24 h** pruning, parameter-mismatch error, **all POST requests** (§2.6) | **CONFIRMED** verbatim ([docs.stripe.com](https://docs.stripe.com/api/idempotent_requests)) |
| 26 | **RFC 9110 §15.6.4** is 503 Service Unavailable and *"MAY send a Retry-After header field… to suggest an appropriate amount of time for the client to wait"* (§1.1) | **CONFIRMED** — correct section number and verbatim text ([rfc9110.txt](https://www.rfc-editor.org/rfc/rfc9110.txt)) |
| 27 | MDN: *"The comment line can be used to prevent connections from timing out; a server can send a comment periodically to keep the connection alive"* (§1.3); OWASP's *"Case when allowlist approach is unavailable"* framing (§4.2) | **CONFIRMED** verbatim, both |

### This repo — three claims corrected

| # | Claim (§) | Verdict |
|---|---|---|
| 28 | `gateway.py` is **380 lines**; `inflight -= 1` runs before `data = r.json()`; `httpx.Timeout(600, connect=10)`; `MAX_INFLIGHT = 16`; `follow_redirects=True` with the size check after `r.content`; `run_in_executor(None, probe_seconds)` with `timeout=30`; `serve.sh` sets `--max-model-len 32768`; `marlin2b-gateway.service` sets no `TimeoutStopSec`; `tests/test_gateway_auth.py` exists | **CORRECTED on the line count** — `wc -l` gives **346**, not 380. **Every other item CONFIRMED against the source**, including the `inflight` defect: line 288 `inflight -= 1` precedes line 289 `data = r.json()`, and the outer `except` at line 344 decrements again, so a non-JSON upstream response drives `inflight` negative permanently. Implications item 1 stands as written |
| 28b | **`--max-num-seqs` is "not set — vLLM default"** (§3.2 table, Implications 3, Open question 1) | **CORRECTED — the most consequential error in the document.** `serve.sh` does not pass the flag, but it forwards `"$@"` and `apps/infrx-api/deploy/marlin2b-vllm.service` runs `ExecStart=…/serve.sh --max-num-seqs 32`. The box has been serving at **N = 32** behind a gateway that hard-refuses at 16, so the effective compute limit is the gateway's, set at the wrong layer. `openrouter/PLAN.md:63` repeats the 32. Three passages rewritten and the open question re-scoped from "what is it?" to "32 is 4× the only concurrency we ever benchmarked, and `MAX_INFLIGHT` has hidden it". **This also resolves a direct contradiction with [`01` correction C3](01-requirements-and-traffic-model.md)**, which had already found it; `03` was the doc that was wrong |
| 28c | **`g6e` pricing "is not sourced to an AWS price list"** (§4.5, Open questions) | **CORRECTED** — it is sourced, in two sibling documents this one did not reconcile with: `g6e.2xlarge` **$2.24208/h** (and `g6e.4xlarge` $3.00424/h) from the AWS Price List, us-east-1, Shared tenancy, Linux, publication version 2026-09-18, pinned in [`01` §3.8–3.9](01-requirements-and-traffic-model.md) and [`05`](05-caching.md). §4.5 now carries the sourced price; the open question is closed and narrowed to the *real* residual — `cloud-pricing.md` still has no `g6e` row — which also lets §4.4 option C's "~2× instance cost ⚠️" become the computed **+34 %** |
| 29 | §0.1 measurement table and the measured figures reused throughout (0.77 s / 0.50 clips/s at c=1; 3.35 s / 1.57 at c=8 1080p; 0.66 s / 3.58 at c=8 360p; TPOT 8 ms at c=8; 2,061 vs ~23.5K prompt tokens; 3.8 s end-to-end with TTFT ~3.2 s and the double download; 2 fps / 240 frames / 200,704 px) | **CONFIRMED** against `models/marlin2b/README.md` and `results/notes.md`. Derived service times recomputed: 1/0.50 = 2.00 s, 8/1.57 = 5.10 s, 8/3.58 = 2.23 s ✓; 2.3× = 3.58/1.57 = 2.28 ✓; 30 × 1.57 = 47 and 600 × 1.57 = 942 ✓; `floor(600 × 1.57 × 0.8)` = 753 ✓; `min(600 × 3.58 × 0.8, 500)` = 500 ✓; 1/8 = 12.5 % ✓; `min(32, 8+4)` = 12 ffprobe threads ✓; 8 vCPU = 4 physical vs a 2+1 = 3-core floor ✓; 32,768 / 23,500 = 1.39 ⇒ N = 1 ✓ |
| 30 | Three derivations that do **not** check out (§2.3, §3.2, §4.5) | **CORRECTED, all three.** (a) "~4× in cost" is **11.4×** (23,500 / 2,061). (b) "16 wide on tokens" overruns the window: 16 × 2,061 = 32,976 > 32,768 ⇒ **15**. (c) "$0.04 → $0.02 per video-hour" is **$0.14 → $0.06**: 1.57 clips/s × 10 s × 3,600 = 56,520 video-**seconds** = 15.7 video-**hours**, so $2.24 / 15.7 = $0.143 and $2.24 / 35.8 = $0.063. The error originates in `models/marlin2b/results/notes.md` ("one GPU-hour processes 57–129 video-hours" — those are thousands of video-seconds), which **still needs the same fix**, as does anything downstream that quotes $/video-hour. Separately, the §3.2 video-seconds gate is **internally inconsistent**: `WORKER_BUDGET_VIDEO_SECONDS = 80` < `MAX_VIDEO_SECONDS = 120`, so the largest legal request can never be admitted — flagged ⚠️ in place |

### UNVERIFIABLE

| # | Claim | Why |
|---|---|---|
| 31 | **A gateway-side `httpx` stream close aborts the request inside vLLM** and increments `vllm:request_success_total{finished_reason="abort"}` (§3.3) | The metric and its `abort` label are confirmed, and `with_cancellation` confirms the server-side disconnect path — but the causal step from *client closes the upstream socket* to *engine aborts* was not confirmed against vLLM source, and the session's web-search budget (200/200) was exhausted before it could be chased further. **⚠️ added in place**, with the one-line experiment that settles it. The §3.3 claim that cancellation saves "up to 12.5 % of the box at c=8" is arithmetically right (1/8) but is an upper bound until this is measured |

### Contradictions with the rest of `research/`

1. **`--max-num-seqs` (resolved above, in `03`'s disfavour).**
   [`01` correction C3](01-requirements-and-traffic-model.md) already recorded
   that the flag lives in the systemd unit, not `serve.sh`. `03` asserted the
   opposite and built §3.2's "pin it explicitly" recommendation and an entire
   open question on it. `03` is now aligned with `01`.
2. **Cost per video-hour (resolved above, converging).** `01` §3.8 had
   independently caught the `results/notes.md` seconds-vs-hours slip and
   published **$0.1414 / $0.0620**; this pass recomputed **$0.1427 / $0.0626**
   from the README's round 10 s clip against `01`'s 10.1 s. `03` was still
   quoting the uncorrected **$0.04 / $0.02**. Aligned; the residual is that
   **`models/marlin2b/results/notes.md` itself is still wrong** and is the
   upstream source both documents cite.
3. **Request-count vs cost-weighted admission — agreement worth noting.** `01`
   §1.5 puts the mis-pricing of a 120 s clip under request-counting at
   **11.5×**; `03` §2.3 said "~4×" and is now **11.4×**. The two trees agree on
   the mechanism; only `03`'s number was wrong.
4. **Video-fetch timeout.** `01` F4 prescribes *connect 3 s / total 20 s / max
   3 redirects*; `03` §3.4 prescribes `FETCH_TIMEOUT_S = 30`. Not a factual
   error in either, but two documents are specifying the same knob at different
   values and neither cites the other. **Reconcile before implementation.**
5. **`research/scaling/` vs this tree.** `scaling/07-cost-engineering.md`
   estimates **$0.026 per 1M output tokens** for Marlin on B300 at 85 %;
   `01` §3.8 derives **$2.01 per 1M output tokens** measured on the L40S. Both
   can be true (different silicon, different utilisation), but `03` cites
   neither, and any $/token figure it grows later must say which basis it is on.

### Structural gaps against the brief

The brief asks for *robust request handling, no drop, queueing before scale-up,
automatic scale-ups, caching, optimization*. Against that:

- **No-drop, queueing, backpressure, retries, routing: covered thoroughly.**
  §1–§7 is a complete request-path design and §7.5's overload staircase is the
  document's strongest section.
- **Automatic scale-up is the one brief item this document does not deliver.**
  It defers to [`../scaling/05`](../scaling/05-autoscaling-and-predictive-scaling.md)
  and [`06`](../scaling/06-cold-start.md), both of which plan an 8×B300
  bare-metal cluster, not an AWS ASG. §2.5's `μ̂_effective` projection and
  §6.4's CloudWatch/KEDA paragraph are the only AWS-shaped scaling content in
  the whole `production-api/` tree, and neither specifies the policy, the
  thresholds, the cooldowns, or the `g6e` capacity reservation that `us-east-1d`
  scarcity demands. **This is document `04`'s job and `04` does not exist** —
  `production-api/` holds only `01`, `03` and `05`, so `02` (presumably the API
  contract / auth / tenancy) and `04` (presumably scaling and capacity) are both
  missing, and `03` forward-references neither.
- **Caching:** §4.3 and the `uuid` hand-off are correct and now corroborated by
  `05`, but `03` was written without reading `05` — it proposes raising
  `mm_processor_cache_gb` "on the L40S" while `05` §1.3 documents that the cache
  is *duplicated per API process and engine core*, which changes the arithmetic.
  **Cross-link them.**
- **Multi-model:** the queue is keyed per model throughout, which is right, but
  every number is Marlin's. The first non-video model (`Qwen3.8-27B` is named
  once, in §4.4) will need §2.4's size-bucketing and §3.2's video-seconds gate
  generalised, and nothing states how.
- **Unmeasured foundations:** five of the document's load-bearing numbers
  (`WORKER_BUDGET_VIDEO_SECONDS`, the service-time variance, the transcoded file
  size, the cache hit rate, N itself) rest on a **single** benchmark point, and
  the document says so honestly in its open questions. The mixed-length
  benchmark is the cheapest thing on the list and unblocks three of them.

*Method: primary sources only (AWS docs pages, `raw.githubusercontent.com` for
vLLM and llm-d source and docs, redis.io, keda.sh, docs.stripe.com,
rfc-editor.org, MDN, OWASP), repo files read directly, and every derivation
re-run in `python3`. Claims were selected for blast radius: anything that sizes
a queue, sets a timeout, names a flag or metric the implementation would type
verbatim, or feeds a price.*

**2026-09-20** — CORRECTED, gateway `TimeoutStopSec` 330 → **930** in §6.3 and
§8 item 10: 330 SIGKILLs the gateway 570 s inside the ALB's 900 s
`deregistration_delay`, producing the 500-level early-close errors this
document's own §6.3 point 2 quotes from AWS. [`09` §0.2](09-blueprint.md) pins
`deregistration_delay = 900` as the number `TimeoutStopSec` must exceed and
supersedes 330 everywhere; [`10` §1, §7.6, §13](10-implementation-spec.md)
carried the same stale 330 and were corrected with it. vLLM unit unchanged at
180.
