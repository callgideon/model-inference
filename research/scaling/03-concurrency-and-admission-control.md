# Handling concurrent requests robustly

Research date: **2026-09-19**. Legend, formulas and pinned GPU/model inputs:
[`../METHODOLOGY.md`](../METHODOLOGY.md). Tree index: [`../README.md`](../README.md).

This document covers what happens to a request between "the socket accepted it"
and "the last token streamed out", and how to keep that path well-behaved when
more requests arrive than the cluster can serve. It does **not** re-derive
memory, throughput or cost numbers — those live in
[`../METHODOLOGY.md` §2–§6](../METHODOLOGY.md), the per-pair docs under
[`../models/`](../models/), and the cross-cutting references
([`inference-engines.md`](../cross-cutting/inference-engines.md),
[`serving-optimizations.md`](../cross-cutting/serving-optimizations.md)) — it
links to them and applies them.

**Scope boundary with the sibling scaling docs.** Cluster build-out and
Kubernetes plumbing, and autoscaling policy (when to add or remove a replica),
are separate documents in this folder. This one covers what a **fixed** fleet
does when load exceeds it. The boundary matters because it is a real engineering
boundary: on this repo's hardware a new DeepSeek-V4.1-Flash replica needs
**1.0 minute** just to stream 307.5 GB of resident weights off local NVMe at
5 GB/s ([`../models/deepseek41f/b300.md` §5.3](../models/deepseek41f/b300.md)),
so **no autoscaler can respond inside a 60-second burst**. Admission control is
the only lever that acts on burst timescales.

### Software versions used in this document

Software moves fast; every flag below is dated.

| Component | Version this doc reads | How obtained | Note |
|---|---|---|---|
| vLLM | `main` branch, fetched 2026-09-19 (`vllm/config/scheduler.py`, `vllm/engine/arg_utils.py`, `docs/`) | `curl raw.githubusercontent.com` | The repo pins the **released** engine at **0.29.0 (2026-09-09)** ([`inference-engines.md` §2.1](../cross-cutting/inference-engines.md)). ⚠️ **TO BE VERIFIED** which of `max_num_queued_reqs`, `max_num_queued_tokens`, `max_num_active_seqs`, `watermark`, `scheduler_reserve_full_isl` shipped in 0.29.0 vs. only on `main`. Method: `vllm serve --help \| grep` on the deployed image before writing them into a manifest. |
| SGLang | `main` branch, fetched 2026-09-19 (`python/sglang/srt/arg_groups/fields/schedule.py`, `managers/scheduler.py`, `managers/schedule_batch.py`) | `curl raw.githubusercontent.com` | The repo pins **0.5.20** ([`inference-engines.md` §2.2](../cross-cutting/inference-engines.md)). Same ⚠️ applies to `--max-queued-requests`, `--retraction-policy`, `--min-free-slots-delay`, the `hrrn` / `shortest-prefill-first` policies. |
| TensorRT-LLM | docs at `nvidia.github.io/TensorRT-LLM` (undated page), repo pins **1.2.1 stable / 1.3.0rc27** ([`inference-engines.md` §2.3](../cross-cutting/inference-engines.md)) | WebFetch | Not applicable to any DeepSeek-V4.1 model here — TRT-LLM has no V4.1 support ([`../models/deepseek41f/b300.md` §5.2](../models/deepseek41f/b300.md)). |
| NVIDIA Dynamo | docs site says **latest = v1.4.2** [src](https://docs.nvidia.com/dynamo/llms.txt); doc pages fetched from `ai-dynamo/dynamo@main` 2026-09-19 | `curl` + WebFetch | **Resolved — no conflict.** The two numbers name different artefacts: **v1.4.2** is the last numbered stable *release / container tag* (GitHub releases, 2026-08-29) and **1.5.0** is the current *PyPI `ai-dynamo`* stable (uploaded 2026-09-19 04:21 UTC); the docs site's `latest` index tracks the **container** release, hence v1.4.2. Both pinned from primary sources in [`02` §2.1 line 150](02-serving-stack-and-routing.md#21-version-pin) and its [verification log entry 5](02-serving-stack-and-routing.md#verification-log-2026-09-19) ([GitHub releases](https://github.com/ai-dynamo/dynamo/releases), [PyPI](https://pypi.org/pypi/ai-dynamo/json)). Which tag to deploy is a deployment decision: [`09` §2.1](09-reference-architectures.md#21-nvidia-dynamo) pins container **1.4.2** with model dev tags `1.5.0-kimi-k3-dev.1` and `1.6.0-deepseek-v4.1-flash-dev.1`. |
| Gateway API Inference Extension | InferencePool `inference.networking.k8s.io/v1`; `endpointPickerRef` "became optional as of v1.5.0" [src](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/) | WebFetch | |
| Envoy AI Gateway | renamed / redirects to **Agent Router** (`theagentrouter.ai`), `next` docs [src](https://theagentrouter.ai/docs/next/capabilities/traffic/usage-based-ratelimiting/) | WebFetch (301 followed) | The CRD group is still `gateway.envoyproxy.io/v1alpha1`. |
| AIBrix | `aibrix.readthedocs.io/latest` | WebFetch | |
| inference-perf | `kubernetes-sigs/inference-perf@main` README + `docs/goodput.md`, `docs/loadgen.md` | `curl` | |
| GuideLLM | `vllm-project/guidellm@main` `docs/getting-started/benchmark.md` | `curl` | |
| AIPerf | NVIDIA blog **2026-09-18** [src](https://developer.nvidia.com/blog/benchmarking-llm-inference-at-scale-with-aiperf/) | WebFetch | "the designated successor to GenAI-Perf". |

### TL;DR decision rules

| Question | Rule | Where |
|---|---|---|
| What sets my concurrency cap? | `min(KV budget, SLO batch, graph-capture ladder)` — and on this hardware it is almost never KV. | §2 |
| `max_num_seqs`? | The largest batch whose TPOT still meets SLO, **not** `max_concurrency(ctx)`. For DS-V4.1-Flash on 4×B300 at 4K that is **256** (the recipe default; 128 only if the real SLO is TPOT ≤ 25 ms), against a KV cap of **17,408** — a 68× gap (136× at 128). | §2.3, §8 |
| `max_num_batched_tokens`? | Start at 8192; lower it only if p99 ITL is the binding SLO, and price the cost first (−64 % throughput for 342→194 ms p99 ITL, measured). | §2.4, §4.9 |
| Where do I reject? | At the **engine** (`max_num_queued_reqs`/`--max-queued-requests` → 503) as the backstop, at the **router** as the policy layer, at the **gateway** for per-tenant quota. Never only one layer. | §3 |
| 429 or 503? | 429 = *this tenant* sent too much. 503 = *the fleet* is full. Both carry `Retry-After`. | §3.5 |
| Hedged requests? | **No**, by default, for LLM generation. A hedge doubles prefill cost — the expensive half. | §3.8 |
| What do I alert on? | `vllm:request_queue_time_seconds` p99 first, `num_requests_waiting` second, `num_preemptions_total` third. | §5.8, §7.6 |
| How do I test it? | **Open-model** load (arrival rate), not closed-model (fixed VUs), or you measure your own client's backpressure. | §7.2 |

---

## 1. The request lifecycle inside an engine

### 1.1 The stages, and where each queue is

A request in a modern continuous-batching engine passes through these stages.
Queues (the places where concurrency becomes latency) are marked **Q**.

| # | Stage | What happens | Queue? | Bounded by |
|---|---|---|---|---|
| 0 | Socket / HTTP frontend | TLS, HTTP parse, JSON decode, OpenAI schema validation | **Q0** — kernel accept backlog + framework concurrency | OS `somaxconn`, uvicorn/Rust-frontend worker count |
| 1 | Tokenization + multimodal preprocessing | text → token ids; images/video → pixel tensors | **Q1** — frontend process pool | CPU. A large image or a 240-frame video here stalls unrelated requests (§6.4) |
| 2 | Admission | engine accepts the request into its waiting queue, or rejects it | **Q2** — the waiting queue | `max_num_queued_reqs` / `--max-queued-requests` (§3.2) |
| 3 | Scheduling | each engine step picks which waiting/running requests get tokens | **Q2 drains here** | `max_num_seqs`, `max_num_batched_tokens`, policy |
| 4 | KV block allocation | paged KV blocks reserved for the tokens about to be computed | — (failure here → preemption) | KV pool size; `watermark` |
| 5 | Prefill (possibly chunked) | prompt tokens computed, KV written | — | `max_num_batched_tokens` / `chunked_prefill_size` |
| 6 | Decode | one step per token across the whole running batch | — | KV reads; preemption if pool fills |
| 7 | Detokenize + stream out | tokens → text chunks → SSE frames | **Q3** — output handler | `stream_interval` |

Two structural facts follow, and they drive everything in §2–§4:

1. **The engine's step is synchronous across the whole running batch.** Adding a
   request to the running batch raises *every* running request's TPOT. This is
   the throughput/latency trade at its root: *"if we process 16 user queries
   concurrently, we'll have higher throughput compared to running the queries
   sequentially, but we'll take longer to generate output tokens for each user"*
   [src](https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices).
2. **Prefill and decode compete for the same step.** Without chunked prefill a
   long prompt monopolises a step and stalls every decoding request — the
   "generation stall" Sarathi-Serve names and fixes (§4.2).

### 1.2 vLLM V1 — what each knob actually does

All quotes are the docstrings in `vllm/config/scheduler.py`, `main`, fetched
2026-09-19
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py).
Class defaults are `DEFAULT_MAX_NUM_BATCHED_TOKENS = 2048`,
`DEFAULT_MAX_NUM_SEQS = 128`, but note the docstring caveat: *"The default value
here is mainly for convenience when testing. In real usage, this should be set
in `EngineArgs.create_engine_config`."*

| Field / CLI | Default | Docstring (verbatim) | Concurrency meaning |
|---|---|---|---|
| `max_num_batched_tokens` / `--max-num-batched-tokens` | 2048 (class), computed in practice | *"Maximum number of tokens that can be processed in a single iteration."* | The **step token budget**. Prefill chunk size and prefill/decode mixing both come out of it. |
| `max_num_seqs` / `--max-num-seqs` | 128 (class) | *"Maximum number of sequences to be processed in a single iteration."* | The **running-batch cap**. Also sizes runner buffers and CUDA-graph capture. |
| `max_num_active_seqs` | `None` | *"Maximum number of requests the scheduler admits into RUNNING. `max_num_seqs` sizes the model runner (per-request buffers and CUDA graph capture) and is also the default admission limit. Setting this lowers only the number of requests that may occupy RUNNING, so decode batches stay smaller without shrinking runner or graph capacity. Must be `<= max_num_seqs`."* | Lets you **shrink the decode batch without re-capturing graphs** — the cheapest live TPOT lever there is. |
| `policy` / `--scheduling-policy` | `"fcfs"` | *"'fcfs' means first come first served, i.e. requests are handled in order of arrival. 'priority' means requests are handled based on given priority (lower value means earlier handling) and time of arrival deciding any ties)."* | **Lower value = earlier** in vLLM. Opposite convention to SGLang's default (§1.3). |
| `long_prefill_token_threshold` | 0 | *"For chunked prefill, a request is considered long if the prompt is longer than this number of tokens. 0 disables the cap (default)."* | Request-size-aware scheduling (§3.7). |
| `max_num_queued_reqs` / `--max-num-queued-reqs` | `None` | *"Maximum number of requests that can be in-flight (waiting or running) at the same time, or None for no limit. When the limit is reached, new requests are rejected with HTTP 503 so the client can retry on another instance. This bounds vLLM's otherwise unbounded request queue and is primarily a coarse capacity valve."* + *"Size it as roughly `data_parallel_size * max_num_seqs` plus the desired queue depth"* | **The engine-level admission valve.** Enforced in the API-server process, counts across DP ranks. |
| `max_num_queued_tokens` / `--max-num-queued-tokens` | `None` | *"Maximum total prompt tokens of requests currently in the prefill phase… This is a TTFT QoS mechanism: by setting it to `target_TTFT * prefill_throughput` you reject requests when the prefill backlog would exceed the latency target."* | **The formula to use.** Docstring also warns the count is deliberately conservative: a partially prefilled request *"still contributes its full `prompt_len`"*, so it *"overestimates the real backlog, causing earlier rejection than strictly necessary — the safe direction for QoS."* |
| `watermark` | 0.0 | *"Fraction of total KV cache blocks to keep free (the watermark) when admitting waiting or preempted requests… This headroom helps avoid frequent KV cache eviction and the resulting repeated preemption of requests when GPU memory is scarce."* | **Anti-thrash headroom.** Default 0.0 = disabled. |
| `scheduler_reserve_full_isl` | `True` | *"If True, the scheduler checks whether the full input sequence length fits in the KV cache before admitting a new request, rather than only checking the first chunk. Prevents over-admission and KV cache thrashing with chunked prefill."* | vLLM's answer to the `GUARANTEED_NO_EVICT` vs `MAX_UTILIZATION` question (§1.4). Default is the conservative one. |
| `enable_chunked_prefill` | `True` | *"If True, prefill requests can be chunked based on the remaining `max_num_batched_tokens`."* | On by default in V1. |
| `async_scheduling` | `None` | *"Async scheduling helps to avoid gaps in GPU utilization, leading to better latency and throughput."* | |
| `prefill_schedule_interval` | 1 | *"For data-parallel deployments, only admit new prefill requests once every N engine steps, aligned across DP ranks, to better balance per-step forward-pass times."* | DP-rank step-time skew control. |
| `stream_interval` | 1 | *"A smaller value (1) makes streaming smoother by sending each token immediately, while a larger value (e.g., 10) reduces host overhead and may increase throughput by batching multiple tokens before sending."* | Trades **measured ITL** for host CPU (§7.4 — it also changes what your benchmark reports). |
| `disable_chunked_mm_input` | `False` | *"…we do not want to partially schedule a multimodal item"* — a mixed text+image prompt is split at the item boundary instead | §6.5. |

**Scheduling order with chunked prefill on** (V1): *"the scheduling policy
prioritizes decode requests. It batches all pending decode requests before
scheduling any prefill operations. When there are available tokens in the
max_num_batched_tokens budget, it schedules pending prefills."* — decode-first,
prefill fills the remainder.

Validation constraints worth knowing before writing a manifest (same file):
`max_num_batched_tokens >= max_num_seqs`; with chunked prefill **off**,
`max_num_batched_tokens >= max_model_len` or the server refuses to start;
`long_prefill_token_threshold <= max_model_len`; and a warning if
`max_num_batched_tokens > max_num_seqs * max_model_len`.

### 1.3 SGLang — the `schedule` namespace

Verbatim from `python/sglang/srt/arg_groups/fields/schedule.py`, `main`, fetched
2026-09-19
[src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/arg_groups/fields/schedule.py).

| Flag | Default | Help text (verbatim) |
|---|---|---|
| `--max-running-requests` | `None` (derived) | *"The maximum number of running requests."* |
| `--max-queued-requests` | `None` | *"The maximum number of queued requests. This option is ignored when using disaggregation-mode."* |
| `--chunked-prefill-size` | `None` (derived) | *"The maximum number of tokens in a chunk for the chunked prefill. Setting this to -1 means disabling chunked prefill."* |
| `--max-prefill-tokens` | **16384** | *"The maximum number of tokens in a prefill batch. The real bound will be the maximum of this value and the model's maximum context length."* |
| `--prefill-max-requests` | `None` | *"The maximum number of requests in a prefill batch. If not specified, there is no limit."* |
| `--schedule-policy` | **`fcfs`** | choices: `lpm`, `random`, `fcfs`, `dfs-weight`, `lof`, `priority`, `routing-key`, `hrrn`, `shortest-prefill-first` |
| `--enable-priority-scheduling` | `False` | *"Requests with higher priority integer values will be scheduled first by default."* |
| `--schedule-low-priority-values-first` | `False` | flips the polarity to match vLLM's |
| `--priority-scheduling-preemption-threshold` | **10** | *"Minimum difference in priorities for an incoming request to have to preempt running request(s)."* |
| `--disable-priority-preemption` | `False` | *"Disable priority scheduling preemption."* |
| `--retraction-policy` | **`length`** | *"The decode retraction policy to use when the KV cache is full. 'length' preserves the existing behavior and retracts short-output, long-input requests first. 'priority' retracts lower-priority requests first."* |
| `--schedule-conservativeness` | **1.0** | *"A larger value means more conservative scheduling. Use a larger value if you see requests being retracted frequently."* |
| `--mem-fraction-static` | `None` | *"The fraction of the memory used for static allocation (model weights and KV cache memory pool). Use a smaller value if you see out-of-memory errors."* |
| `--enable-mixed-chunk` | `False` | *"Enabling mixing prefill and decode in a batch when using chunked prefill."* |
| `--num-continuous-decode-steps` | 1 | *"Run multiple continuous decoding steps to reduce scheduling overhead. This can potentially increase throughput but may also increase time-to-first-token latency."* |
| `--prefill-decode-interval` | `None` | *"The number of decode rounds to run after a prefill batch before scheduling the next prefill."* — a direct TTFT↔ITL dial |
| `--enable-prefill-delayer` + `--prefill-delayer-queue-min-ratio` / `--prefill-delayer-max-delay-ms` | off | *"Delays prefill until the waiting queue reaches min(running_req * ratio, prefill_max_requests)"*; the ms cap is a *"Wall-clock cap (ms) on a single queue-trigger delay; once exceeded, prefill is force-released to bound worst-case TTFT. Typical: 1000 ~ 5000."* |
| `--min-free-slots-delay` | `None` | *"Hold new prefills until at least N running-request slots have freed up, so they are admitted in one batch instead of one at a time. Useful when each admission is disproportionately expensive, e.g. speculative decoding with a separate draft prefill pass."* |
| `--max-mamba-cache-size` / `--mamba-full-memory-ratio` | `None` / 0.9 | sizes the linear-attention state pool — **the binding capacity knob for Qwen3.8-27B and Kimi-K3** (§2.6) |

The operator-facing tuning guidance, verbatim
[src](https://docs.sglang.io/advanced_features/hyperparameter_tuning.html):

- *"If you frequently see `#queue-req: 0`, it suggests that your client code is
  submitting requests too slowly. A healthy range for `#queue-req` is
  `100 - 2000`."* — SGLang deliberately wants a **non-empty** queue.
- *"If the workload has many shared prefixes, try `--schedule-policy lpm`. Here,
  `lpm` stands for longest prefix match. It reorders requests to encourage more
  cache hits but introduces more scheduling overhead."*
- OOM in prefill → *"try reducing `--chunked-prefill-size` to `4096` or `2048`.
  This saves memory but slows down the prefill speed for long prompts."*
- OOM in decode → *"try lowering `--max-running-requests`."*
- *"To support higher concurrency, you should maximize the KV cache pool capacity
  by setting `--mem-fraction-static` as high as possible while still reserving
  enough memory for activations and CUDA graph buffers."*

### 1.4 TensorRT-LLM — capacity scheduler policies

[src](https://nvidia.github.io/TensorRT-LLM/performance/performance-tuning-guide/useful-runtime-flags.html)
(page undated; TRT-LLM version in this tree: 1.2.1 stable / 1.3.0rc27).

| `capacity_scheduler_policy` | Behaviour (verbatim) | When to pick it |
|---|---|---|
| `GUARANTEED_NO_EVICT` (**default**) | *"guarantee that a started request is never paused"* | Interactive SLOs. A started request's TPOT is not hostage to later arrivals. |
| `MAX_UTILIZATION` | *"pack as many requests as possible at each iteration"* to maximise GPU utilisation, *"though this may negatively impact latency"* | Batch/offline. Requires eviction logic; a request can be paused. |
| `STATIC_BATCH` | *"A legacy mode not recommended for production"* | Never. |

Context chunking policy (same page):

| `context_chunking_policy` | Behaviour |
|---|---|
| `FIRST_COME_FIRST_SERVED` (**default**) | *"prioritize scheduling all the context chunks of a request that comes in first"*; *"should achieve overall better performance"* |
| `EQUAL_PROGRESS` | schedules one chunk from every request before the next chunk — *"potentially balancing time-to-first-token across requests"* |

That pair is the cleanest statement of the TTFT-fairness trade in any engine's
docs: FCFS minimises mean TTFT, EQUAL_PROGRESS minimises TTFT **variance**.
NVIDIA also states: *"It's recommended that you always enable context chunking
since in the worst case scenario it has minimal impact on performance but can
significantly benefit it in many scenarios."*

KV sizing: `kv_cache_free_gpu_mem_fraction` is *"the maximum fraction of GPU
memory (after loading the model) that will be used for the KV cache"*, default
**0.90**, *"testing up to 0.95 is recommended for high throughput scenarios"*.

**Not applicable to three of the five repo models**: TRT-LLM has no
DeepSeek-V4.1 support (V4 only)
([`../models/deepseek41f/b300.md` §5.2](../models/deepseek41f/b300.md)), and no
engine supports Marlin-2B
([`inference-engines.md` §6.5](../cross-cutting/inference-engines.md)).

### 1.5 Preemption: recompute vs swap vs retract

This is the single most important failure mode under load, because it is
*silent* — throughput degrades but nothing errors.

**vLLM.** Two modes historically (swap KV blocks to host, or discard and
recompute); **V1 defaults to RECOMPUTE**. The user-visible signal is the log
line, quoted verbatim in the docs
[src](https://docs.vllm.ai/en/stable/configuration/optimization/):

> *"Sequence group 0 is preempted by PreemptionMode.RECOMPUTE mode because there
> is not enough KV cache space."*

The documented remedies, in the order the docs list them (same page):

- *"Increase `gpu_memory_utilization`"* — more KV blocks.
- *"Reduce `max_num_seqs` or `max_num_batched_tokens`"* — fewer concurrent
  requests. **This is the correct one under burst**; the others are deploy-time.
- *"Raise `tensor_parallel_size`"* — shards weights, frees cache. **Note: this
  does not help DeepSeek-V4.1-Flash or Kimi-K3's MLA KV**, which is *replicated*
  per rank under plain TP
  ([`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) §6.3,
  [`../METHODOLOGY.md` §3](../METHODOLOGY.md)).
- *"Raise `pipeline_parallel_size`"*.

Monitor with `vllm:num_preemptions_total` (§7.6).

**SGLang** calls it **retraction**, and it is implemented as a requeue rather
than a pause. From `schedule_batch.py`, `main`, 2026-09-19
[src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/schedule_batch.py):

```python
def retract_decode(self) -> Tuple[List[Req], float, List[Req]]:
    """Retract the decoding requests when there is not enough memory."""
    sorted_indices = self._get_decode_retraction_order(self.reqs)
    ...
        if len(sorted_indices) == 1:
            # Always keep at least one request
            break
```

and the ordering function's own docstring:

```python
@staticmethod
def _get_decode_retraction_order(reqs: List[Req]) -> List[int]:
    """Return indices ordered from most-preferred to least-preferred to keep.

    The retraction loop pops from the end of this list, so the least-preferred
    request is retracted first.
    """
    def length_key(req: Req) -> Tuple[int, int]:
        return (len(req.output_ids), -len(req.origin_input_ids))
```

So the default `--retraction-policy length` retracts **short-output,
long-input** requests first (they have generated least, so least work is lost —
but they are also the most expensive to re-prefill). `--retraction-policy
priority` retracts by the priority field instead. `--schedule-conservativeness`
is the preventive dial: *"Use a larger value if you see requests being retracted
frequently."*

One hard edge: beam-search groups **cannot** be retracted and are **aborted**
with HTTP 500 instead — verbatim from the same file: *"Beam search group
aborted: KV cache pool is full. Beam groups cannot be retracted, so they are
aborted instead of being requeued."*

**TRT-LLM** avoids the question entirely under `GUARANTEED_NO_EVICT`.

**Decision rule.** Preemption rate > ~0 sustained means you have admitted more
concurrency than the KV pool supports at the *observed* context lengths. Fix it
by lowering `max_num_active_seqs` (vLLM, no graph re-capture) or
`--max-running-requests` (SGLang), **not** by raising `gpu_memory_utilization`
into the activation workspace. Trade-off: lowering the running batch costs
aggregate throughput linearly until you hit the MBU ceiling, and on this repo's
DeepSeek-V4.1-Flash/B300 pair the aggregate rate is *flat* above batch 32
anyway ([`../models/deepseek41f/b300.md` §3.4](../models/deepseek41f/b300.md) —
column "A agg" is 7,191 tok/s at batch 32, 64, 128 and 256 alike), so the cost
of lowering it is near zero there.

### 1.6 Streaming out

`stream_interval` (vLLM, default 1) buffers N tokens per SSE frame. Raising it
reduces host overhead and **changes what ITL means**: vLLM's own docs warn that
`vllm:inter_token_latency_seconds` *"records one sample per streamed output
event (the wall-clock gap between successive outputs)"* whereas
`vllm:request_time_per_output_token_seconds` is *"(end-to-end latency - TTFT) /
(number of output tokens - 1)"*, and *"These two metrics differ when an output
bundles multiple tokens (e.g. speculative decoding)"*
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/metrics.md).
With **DSpark γ=5** — this repo's pinned default for DeepSeek-V4.1-Flash
([`../matrix/recommendations.md`](../matrix/recommendations.md)) — a single
streamed output can carry up to 6 tokens, so **ITL and TPOT diverge by the
acceptance rate**. Pick one and state it in every SLO document; this tree uses
TPOT (§7.4).

### 1.7 The queue map — what to watch per queue

| Queue | vLLM signal | SGLang signal | Bound by | Symptom when full |
|---|---|---|---|---|
| Q0 HTTP accept | `http_request_duration_seconds` (via `prometheus_fastapi_instrumentator`) | — | uvicorn/Rust frontend | connection resets, TTFT spikes with no engine-side change |
| Q1 tokenize/mm | — ⚠️ (use `--probe-request-rate` to measure, §7) | — | CPU | unrelated requests stall behind one big video (§6.4) |
| Q2 waiting | `vllm:num_requests_waiting`, `vllm:request_queue_time_seconds` | `#queue-req` in the log | `max_num_queued_reqs` | TTFT grows, TPOT flat |
| running batch | `vllm:num_requests_running` | `#running-req` | `max_num_seqs` | TPOT grows with batch |
| KV pool | `vllm:kv_cache_usage_perc`, `vllm:num_preemptions_total` | retraction counters | pool size | preemption/retraction storm |
| Q3 output | `vllm:inter_token_latency_seconds` vs `..._time_per_output_token_...` | — | `stream_interval` | ITL sawtooth |

**The diagnostic rule** (Red Hat, 2026-03-09, authors Whyte-Gray, Ibrahim
Bathusha, Goin, Kamra
[src](https://developers.redhat.com/articles/2026/03/09/5-steps-triage-vllm-performance)):
*"Isolate the symptom (TTFT vs. ITL)"* first. **TTFT bad, ITL fine → a queueing
problem (Q0–Q2). ITL bad → a batch-size or memory problem (running batch, KV
pool).** Their step 2: *"If `num_requests_waiting` is consistently above zero,
the time requests spend in the queue leads to higher TTFT"*; step 3: on
`vllm:kv_cache_usage_perc`, *"If this value is consistently near 100%, new
requests are forced to wait."*

---

## 2. Capacity limits and how to set them

### 2.1 The two budgets

[`../METHODOLOGY.md` §3](../METHODOLOGY.md) gives the memory budget:

```
usable_hbm      = hbm_capacity × 0.90
kv_budget       = usable_hbm − per_gpu_weights − activation_ws
max_concurrency(ctx) = floor( kv_budget × n_gpus / (ctx × kv_bytes_per_token + fixed_state) )
```

with the sharding caveat that `× n_gpus` is valid **only when KV is sharded
across ranks** — which for DeepSeek-V4.1-Flash and Kimi-K3 (both
`num_key_value_heads = 1`, one shared MLA latent) it is **not**, under plain TP
([`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) §6.3).

There is a second, independent budget that METHODOLOGY does not name because it
is a scheduling rather than a memory quantity:

```
batched-token budget  B = max_num_batched_tokens   (vLLM)
                        = chunked_prefill_size      (SGLang)
step_tokens = decode_tokens + prefill_chunk_tokens ≤ B
decode_tokens ≈ running_batch × (1 + num_speculative_tokens)
prefill_headroom(step) = B − decode_tokens
```

**Prefill interference is exactly `prefill_headroom`.** If `B` is barely larger
than the decode batch, prefill starves and TTFT explodes; if `B` is much larger,
each step does a big slab of prefill and every decoding request's ITL jumps by
the prefill time. That is the whole Sarathi-Serve result (§4.2) reduced to one
inequality.

Three caps therefore bind, and the smallest wins:

```
effective_concurrency = min(
    max_concurrency(ctx),              # KV / state memory        (§2.2)
    batch_at_SLO(TPOT_target),         # step-time SLO            (§2.3)
    cudagraph_capture_ladder_max       # graph capture            (§2.3)
)
```

### 2.2 Worked numbers — the five repo models on B300

All values below are **taken from** the pair docs and
[`../matrix/pairs.json`](../matrix/pairs.json), not re-derived. B300 = 268 GB as
deployed, 2,144 GB per 8-GPU node ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)).

| Model | Shape | `max_concurrency` @8K | @128K | Per-request cost breakdown | What actually binds |
|---|---|--:|--:|---|---|
| [DeepSeek-V4.1-Flash](../models/deepseek41f/b300.md) | TP4, Engram host-offloaded, KV **replicated** per rank (binding reading), FP4 KV 890 B/tok | **11,184** | **953** | 8K: 0.0095 GiB KV + 2.77 MiB SWA ring | **Not KV.** vLLM's own recipe: *"Weights and batch size, not cache, set the capacity limit"* [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). Measured runs used **2–26 %** of the pool ([`b300.md` §1.3](../models/deepseek41f/b300.md)). |
| [DeepSeek-V4.1-Flash-NVFP4](../models/deepseek41fnvfp4/b300.md) | TP4, full ckpt resident, **replicated** reading | **10,199** | **869** | as above + 17 GB more weights | Same. ⚠️ `pairs.json` carries **40,801 / 3,479** for this pair — those are the **optimistic (KV-sharded, DP-attention/DCP) reading**, printed in [`b300.md` §1.3](../models/deepseek41fnvfp4/b300.md). The two rows are **different bases, not a contradiction**; plan on the replicated numbers unless DCP is configured. |
| [Qwen3.8-27B](../models/qwen3827b/b300.md) | TP1, 8 replicas/node | **325** | **45** | GDN state **392.2 MB/request** (S=5 × 78.4 MB bf16, SGLang default) + 32 KiB/token FP8 over 16 full-attn layers | **The GDN state pool.** At 8K, state is 392 MB vs KV 256 MB — state is the majority. |
| [Kimi-K3](../models/kimik3/b300.md) | TP8 + DCP8, one node | **101** | **64** | KDA state **2.25 GB/request node-wide** + MLA 13.5 KiB/tok FP8 | **The KDA state pool, absolutely.** SGLang, verbatim: *"The KDA state pool is the concurrency ceiling — DP, EP, and DCP do not shard it; only attention-TP width, SSM dtype, and cache strategy change the per-GPU bill."* At 8K it is **95 % of the per-request bill**; KV only overtakes it at **~163 K tokens at FP8 KV** ([`kimik3/b300.md` §1.3](../models/kimik3/b300.md)). |
| [Marlin-2B](../models/marlin2b/b300.md) | TP1, 8 replicas/node | **1,934** | **142** | 12 KiB/token BF16 (6 GQA layers) + GDN 18.63 MiB/seq | At the **video** operating point (23,520 ctx) the cap is **753** concurrent 2-minute videos, and *"the 23,520 row is the only one a video fleet ever occupies: the 240-frame cap bounds every video"* ([`marlin2b/b300.md`](../models/marlin2b/b300.md)). §6.4. |

**The headline for capacity planning on this hardware: KV is not the binding
constraint for three of five models, and for the other two the binding
constraint is a *fixed per-sequence state*, not context length.** A capacity
plan written as "concurrency = KV budget ÷ context" is wrong for four of these
five models.

### 2.3 Setting `max_num_seqs` — the SLO batch, not the KV batch

**Decision rule.** Sweep batch against TPOT at your target context, take the
largest batch meeting the TPOT SLO, then set:

```
max_num_seqs            = batch_at_SLO                     (sizes graphs + buffers)
max_num_active_seqs     = batch_at_SLO   (vLLM, if available; lets you lower it live)
cudagraph capture ladder ≥ max_num_seqs × (1 + num_speculative_tokens)
```

The capture-ladder rule is quoted in this tree already: *"capture size must cover
`max_num_seqs × (1 + num_speculative_tokens)` — DS-V4.1-Flash rounds
`128 × 6 = 768` up to 1024"*
([`serving-optimizations.md` §4.2](../cross-cutting/serving-optimizations.md),
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)).

Worked, DeepSeek-V4.1-Flash on 4×B300, S1 (4K in / 512 out), using the
**measured-capped ("A") column** of
[`../models/deepseek41f/b300.md` §3.4](../models/deepseek41f/b300.md):

| batch | A TPOT (ms) | A agg (tok/s, 4 GPUs) | ≤ 50 ms SLO? | ≤ 25 ms SLO? |
|--:|--:|--:|---|---|
| 32 | 4.45 | 7,191 | ✅ | ✅ |
| 64 | 8.90 | 7,191 | ✅ | ✅ |
| 128 | 17.80 | 7,191 | ✅ | ✅ |
| 256 | 35.60 | 7,191 | ✅ | ❌ |
| 512 (extrapolated, `est.`) | 71.2 | 7,191 | ❌ | ❌ |
| 17,408 (`max_concurrency` @4K, FP4 KV) | **~2,420** | 7,191 | ❌❌ | ❌❌ |

Two things fall out and both are load-bearing:

1. **Aggregate throughput is flat from batch 32 upward** (7,191 tok/s), because
   the measured decode-MFU ceiling of 0.0217 binds before bandwidth does
   ([`b300.md` §3.3/§3.5](../models/deepseek41f/b300.md)). Above batch 32 you buy
   **no throughput** and pay TPOT linearly. The only reason to run batch 128
   rather than 32 is that it holds more *concurrent users* at the same
   aggregate rate — i.e. it converts TPOT headroom into seats.
2. **The KV cap is 136× the SLO cap** (17,408 vs 128). A manifest that sets
   `max_num_seqs` from `max_concurrency` produces a server that never errors, is
   never OOM, and is useless.

**Trade-off to state explicitly.** Choosing batch 128 over 256 costs nothing in
throughput here but halves the number of seats; choosing 256 over 128 doubles
seats and doubles TPOT to 35.6 ms, which still meets METHODOLOGY's S1 SLO of
≤ 50 ms. On this pair, **256 is the right S1 setting and 128 is the right choice
if the real SLO is 25 ms** — and the recipe's default of `--max-num-seqs 256`
([`b300.md` §5.2](../models/deepseek41f/b300.md)) agrees.

### 2.4 Setting `max_num_batched_tokens`

vLLM's documented guidance, verbatim
[src](https://docs.vllm.ai/en/stable/configuration/optimization/): *"Lower values
(2048) improve inter-token latency"*, *"Higher values boost time-to-first-token"*,
and *"For optimal throughput on smaller models: `max_num_batched_tokens >
8192`"*.

The price of that dial is measured, on GLM-5.3-Flash FP8 / 4×GB200 TP4 /
8192-in 1024-out, concurrency 32/64/128, already recorded in this tree
([`serving-optimizations.md` §4.1](../cross-cutting/serving-optimizations.md),
[src](https://github.com/vllm-project/vllm/issues/56975)):

| Chunk budget | Throughput (tok/s) | p99 ITL (ms) |
|---|---|---|
| 16384 (default) | 1892 / 2607 / 3377 | 199 / 337 / 342 |
| 4096 | 1349 / 1679 / 1979 | 207 / 211 / 214 |
| 2048 | 956 / 1112 / 1212 | 189 / 193 / 194 |

**−64 % throughput at c=128 to take p99 ITL from 342 ms to 194 ms.** That is an
expensive dial. The cheaper dial for the same objective is the CUDA-graph
capture size (§4.9).

**Decision rule.** Set `max_num_batched_tokens` so that
`B ≥ decode_tokens + one useful prefill chunk`:

```
B ≥ max_num_seqs × (1 + γ) + chunk_min
```

For DeepSeek-V4.1-Flash at `max_num_seqs=256`, DSpark γ=5: decode floor is
`256 × 6 = 1,536` tokens; the vLLM recipe ships `--max-num-batched-tokens 8192`
([`b300.md` §5.2](../models/deepseek41f/b300.md)), leaving ~6,656 tokens of
prefill headroom per step — i.e. a 4K prompt prefills in **one step**, a 128K
prompt in **~20 steps**. That is the right shape: no request ever monopolises a
step, and a short prompt is never chunked at all.

### 2.5 Let vLLM tell you the answer

vLLM prints both budgets at startup, quoted in its benchmarking docs
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/benchmarking/cli.md):

```text
GPU KV cache size: 15,728,640 tokens
Maximum concurrency for 8,192 tokens per request: 1920
```

Read `Maximum concurrency` as the **KV** cap only, then apply §2.3. If this line
disagrees with the pair doc's `max_concurrency` by more than ~10 %, the pair
doc's assumption about KV sharding or `fixed_state` is wrong for your build —
which is exactly how [`../models/deepseek41f/b300.md` §1.4](../models/deepseek41f/b300.md)
validated the replicated reading against measured KV pools.

### 2.6 The hybrid-model special case: state, not context

For **Qwen3.8-27B** (48 linear-attention layers) and **Kimi-K3** (KDA), a
request costs a **fixed** slab the moment it is admitted, regardless of prompt
length. Consequences for admission control:

- `max_concurrency` is **almost flat in context** — Kimi-K3 on 8×B300 goes
  101 → 64 from 8K to 128K, a factor of 1.6, where a pure-KV model would drop
  16× ([`pairs.json`](../matrix/pairs.json)).
- **Admitting a 200-token request costs nearly as much as a 100K one.** So
  request-size-aware admission (§3.7) buys much less here, and a per-tenant
  *request-count* limit is a better proxy for cost than a token limit.
- The knob is `--max-mamba-cache-size` / `--mamba-full-memory-ratio` (SGLang) —
  and the `S` slot count: SGLang allocates **S = 5** by default; `S = 1` only
  with `--disable-radix-cache` ([`../METHODOLOGY.md` §8](../METHODOLOGY.md) pin
  log). Dropping S from 5 to 1 would multiply Qwen3.8-27B's seat count by ~5 at
  the cost of prefix caching. ⚠️ vLLM's `S` is unpublished
  ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)) — **TO BE VERIFIED**; method:
  start the server, read the reported cache-pool size, divide by the documented
  per-slot bytes.

### 2.7 The multimodal special case: encoder budget, not KV

Marlin-2B's concurrency ceiling at the video operating point is set by the ViT
burst, not the KV pool: *"240 frames × 784 patches = 94,080 ViT tokens in a
single varlen forward… it is per concurrent prefill, not per sequence"*
([`../models/marlin2b/b300.md` §1.4](../models/marlin2b/b300.md)). vLLM sizes the
encoder budget off the same knob:
`max_num_encoder_input_tokens = max_num_batched_tokens` and
`encoder_cache_size = max_num_batched_tokens`, both marked
`# TODO (ywang96): Make this configurable.`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py).
So on a video fleet, `max_num_batched_tokens` is simultaneously the prefill
chunk size **and** the encoder admission budget — you cannot tune them apart
today. §6.4.

---

## 3. Admission control and backpressure

### 3.1 The layered model

Four places can say no. Each has a different piece of information, and a robust
deployment uses all four.

| Layer | Knows | Should enforce | Failure if missing |
|---|---|---|---|
| **Client SDK** | its own retry history | backoff + jitter, retry budget, idempotency key | retry storms amplify the incident (§3.8) |
| **Gateway** (Agent Router / Envoy, AIBrix) | tenant identity, token quota, cost | per-tenant RPM/TPM, auth, circuit breaking | one tenant starves the fleet |
| **Router / EPP** (Dynamo, GIE EndpointPicker, llm-d) | fleet-wide load, KV overlap, per-replica queue depth | which replica, whether to queue, which class dispatches | hot-spotting; cache-blind routing |
| **Engine** (vLLM / SGLang) | exact KV occupancy, running batch, real step time | the hard backstop: queue caps → 503 | unbounded queue → infinite TTFT |

**Rule: the engine-level cap is not optional.** Every other layer can be
bypassed, misconfigured, or lag behind reality by a scrape interval; the engine
cap is the only one that cannot be wrong about its own KV pool.

### 3.2 Engine-level admission

**vLLM** (`main`, 2026-09-19): `--max-num-queued-reqs` and
`--max-num-queued-tokens`, both *"rejected with HTTP 503"*, both enforced in the
API-server process across DP ranks (§1.2 table for the verbatim docstrings). The
sizing formulas come straight from those docstrings:

```
max_num_queued_reqs   ≈ data_parallel_size × max_num_seqs + desired_queue_depth
max_num_queued_tokens  = target_TTFT × prefill_throughput
```

**SGLang**: `--max-queued-requests`. The implementation, verbatim from
`managers/scheduler.py`, `main`, 2026-09-19
[src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/scheduler.py):

```python
def _abort_on_queued_limit(self, recv_req: Req) -> bool:
    """Abort an incoming or existing request if the waiting queue is full.
       Returns True if the incoming request is aborted."""
    if (
        self.max_queued_requests is None
        or len(self.waiting_queue) + 1 <= self.max_queued_requests
    ):
        return False

    # Reject the incoming request by default.
    req_to_abort = recv_req
    message = "The request queue is full."
    if self.enable_priority_scheduling:
        # With priority scheduling, consider aboritng an existing request based on the priority.
        ...
        message = "The request is aborted by a higher priority request."
    ...
        finished_reason={
            "type": "abort",
            "status_code": HTTPStatus.SERVICE_UNAVAILABLE,
            "message": message,
        },
```

Two behaviours worth knowing: with `--enable-priority-scheduling`, a
**higher-priority arrival evicts the least-preferred *waiting* request** rather
than being rejected itself — an admission-time priority inversion fix that most
gateways cannot do because they cannot see the queue. And there is a separate
**waiting timeout**, `SGLANG_REQ_WAITING_TIMEOUT` (env, seconds; `0` = off),
which aborts queued requests with 503 *"Request waiting timeout reached."* —
that is a deadline-based shed, and it is the cheapest correct answer to "the
queue drained too slowly to be useful anyway".

**Decision rule for queue depth.** The queue exists to absorb *arrival jitter*,
not *sustained overload*. Size it so the expected wait is inside the TTFT
budget:

```
queue_depth ≤ completion_rate_per_replica × (TTFT_budget − TTFT_service)
```

Anything deeper converts a fast rejection (which a client can retry elsewhere)
into a slow timeout (which it cannot). SGLang's guidance that a healthy
`#queue-req` is *"100 - 2000"*
[src](https://docs.sglang.io/advanced_features/hyperparameter_tuning.html) is
about **client-side supply**, not about a desirable steady-state wait — read it
as "your load generator should be able to keep the engine fed", and do not
mistake it for an SLO target.

### 3.3 Router-level admission and backpressure

Dynamo separates three things most systems conflate — worker **eligibility**,
request **queueing**, and request **ordering**. Verbatim
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/worker-filtering.md):

> *"`--router-queue-threshold` is not candidate eligibility. It is admission
> backpressure. When queueing is enabled, the router checks active prefill
> tokens for the request's eligible workers. If all eligible workers are above
> the configured fraction of `max_num_batched_tokens`, the request waits in the
> router queue. The request is scored only after capacity frees up, so dispatch
> uses fresh load and cache state."*

That last clause is the reason a router queue can beat an engine queue: a
request parked at the router can still be sent to whichever replica is *best*
when capacity appears, whereas a request parked in an engine's waiting queue is
already committed to that replica.

Busy thresholds are the separate, harder filter (same page):

- `--active-decode-blocks-threshold`
- `--active-prefill-tokens-threshold`
- `--active-prefill-tokens-threshold-frac`

with *"The thresholds can be updated at runtime through the `/busy_threshold`
HTTP endpoint"* — a live incident lever. Error classification is preserved:
*"If compatible workers exist but all are overloaded, routing fails as
overload"* (`AllEligibleWorkersOverloaded`), distinct from "no endpoint".

The **Gateway API Inference Extension** EndpointPicker does the equivalent by
scraping each replica's Prometheus endpoint, reading `vllm:kv_cache_usage_perc`
and queue-depth counters. ⚠️ **TO BE VERIFIED**: the sentence previously quoted
here — *"The EPP receives request metadata, scores candidate model server pods by
using configurable plugins (for example, queue depth, KV cache utilization, and
prefix-cache affinity), and returns the selected endpoint"* — was attributed to
the CNCF 2025-04-21 blog, but a re-fetch on 2026-09-19 found that article does
**not** contain it; it describes an *Endpoint Selection Extension (ESE)* applying
**sequential filters** (criticality, queue depth, LoRA affinity, KV-cache usage),
not a plugin-scoring EPP. *Method:* locate the wording in the GIE EPP
architecture docs and re-cite, or drop it. The shedding rule below **is** from
that blog and is quoted verbatim: *"If the filter sees a request that is Sheddable
then it will find LLMs that have lower than 80% KV cache utilization and fewer
than 5 requests waiting in its queue"* (same source) — i.e. **sheddable traffic
is admitted only to demonstrably idle replicas**. That is the cleanest
formulation of priority-based load shedding in any of these projects.

A minimal `InferencePool`, verbatim
[src](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/):

```yaml
apiVersion: inference.networking.k8s.io/v1
kind: InferencePool
metadata:
  name: vllm-qwen3-32b
spec:
  selector:
    matchLabels:
      app: vllm-qwen3-32b
  targetPorts:
    - number: 8000
  endpointPickerRef:
    name: vllm-qwen3-32b-epp
    port:
      number: 9002
    failureMode: FailOpen
```

⚠️ `failureMode: FailOpen` means that if the EPP dies, traffic is load-balanced
**without** any inference-aware scoring or shedding. For a fleet whose safety
depends on EPP-side shedding, `FailClose` is the safer default — but it turns an
EPP outage into a full outage. Decide which, explicitly. ⚠️ **TO BE VERIFIED**:
whether `FailClose` is supported in the pinned version; the doc page quoted
shows only `FailOpen`.

**AIBrix** exposes the same idea as a per-request header
[src](https://aibrix.readthedocs.io/latest/features/gateway-plugins.html):

```bash
curl http://${ENDPOINT}/v1/chat/completions \
  -H "routing-strategy: least-request" \
  -H "Content-Type: application/json" \
  -d '{"model": "your-model-name", "messages": [...]}'
```

with strategies including *"least-request: routes to the pod with the fewest
in-flight requests"*, *"least-kv-cache: routes to the pod with the smallest KV
cache occupancy"*, *"throughput: routes to the pod that has processed the fewest
total weighted tokens"*, *"prefix-cache"*, *"power-of-two"*, and — directly
relevant to §4.7 — *"vtc-basic: hybrid scoring balancing per-user token fairness
and pod utilization"*, plus an `slo` family.

**Dynamo's router cost model**, verbatim, because it is the only published
formula of its kind
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/routing-concepts.md):

```text
raw_prefill_blocks = active_prefill_blocks + incoming_prompt_blocks
adjusted_prefill_blocks = max(0, raw_prefill_blocks - overlap_credit_blocks)
potential_decode_blocks = active_decode_blocks + incoming_active_blocks
active_request_blocks = decode_active_request_weight * active_requests
cost = prefill_load_scale * adjusted_prefill_blocks + potential_decode_blocks + active_request_blocks
```

with *"`decode_active_request_weight` defaults to `0`, so the active-request term
is opt-in"* and *"The router selects the lowest-cost eligible worker."* Setting
`--router-decode-active-request-weight > 0` is the lever for decode regimes
where *"step latency follows batch size more closely than resident KV
footprint"* — which is **exactly** the DeepSeek-V4.1-Flash/B300 regime, where
aggregate throughput is flat in batch and TPOT is linear in it (§2.3). The doc
warns of the trade: it *"can reduce cache locality or over-penalize batches when
KV-memory traffic remains the dominant cost."*

### 3.4 Gateway-level: token-bucket and quota

Token-based (not request-based) quota is the only kind that is fair for LLMs,
because request cost varies by three orders of magnitude between a 200-token
chat turn and a 1M-token agentic prompt.

**Agent Router / Envoy AI Gateway**, verbatim
[src](https://theagentrouter.ai/docs/next/capabilities/traffic/usage-based-ratelimiting/):

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: model-specific-token-limit-policy
  namespace: default
spec:
  targetRefs:
    - name: envoy-ai-gateway-token-ratelimit
      kind: Gateway
      group: gateway.networking.k8s.io
  rateLimit:
    type: Global
    global:
      rules:
        - clientSelectors:
            - headers:
                - name: x-tenant-id
                  type: Distinct
                - name: x-ai-eg-model
                  type: Exact
                  value: gpt-4
          limit:
            requests: 1000
            unit: Hour
          cost:
            request:
              from: Number
              number: 0
            response:
              from: Metadata
              metadata:
                namespace: io.envoy.ai_gateway
                key: llm_total_token
```

and the cost definition, with a CEL expression that can weight cached input
differently — which maps directly onto this tree's cached-input economics
([`serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md)):

```yaml
spec:
  llmRequestCosts:
    - metadataKey: llm_input_token
      type: InputToken
    - metadataKey: llm_output_token
      type: OutputToken
    - metadataKey: llm_total_token
      type: TotalToken
    - metadataKey: custom_cost
      type: CEL
      cel: "(input_tokens - cached_input_tokens) + (cached_input_tokens * 0.1) + output_tokens * 1.5"
```

**The critical caveat**, verbatim from the same page: *"AI Gateway automatically
extracts token usage from LLM responses"* and charges *"after responses
complete. Requests are evaluated against already-charged usage, and new token
counts are added post-response."*

**So token quota is a lagging control.** A tenant can launch 500 concurrent
1M-token requests against a quota of 1,000 tokens/hour and every one of them is
admitted, because none has completed yet. **Token quota bounds cost, not
concurrency.** You still need a *concurrency* limit per tenant — AIBrix's
per-replica in-flight/RPS limits, an Envoy `max_pending_requests` /
`max_requests` circuit breaker (§5.7), or a gateway-held semaphore.

**AIBrix** enforces RPM and TPM keyed on the `user` header
[src](https://aibrix.readthedocs.io/latest/features/gateway-plugins.html),
backed by a *"distributed, Redis-backed fixed-window rate limiter"*
[src](https://pkg.go.dev/github.com/vllm-project/aibrix/pkg/plugins/gateway/ratelimiter).
⚠️ A **fixed-window** limiter admits up to 2× the limit across a window
boundary; a token bucket or sliding window does not. For burst-sensitive
capacity this matters — **TO BE VERIFIED** whether the pinned AIBrix release
offers a sliding-window mode. Method: read the `ratelimiter` package's
implementation for the deployed tag.

### 3.5 Status codes, and what to put in them

| Code | Means | Source | Carry `Retry-After`? |
|---|---|---|---|
| **429 Too Many Requests** | *"the user has sent too many requests in a given amount of time ('rate limiting')"*; *"The response representations SHOULD include details explaining the condition, and MAY include a Retry-After header indicating how long to wait before making a new request"* [src](https://www.rfc-editor.org/rfc/rfc6585.txt) §4 | per-tenant quota | Yes |
| **503 Service Unavailable** | *"the server is currently unable to handle the request due to a temporary overload or scheduled maintenance, which will likely be alleviated after some delay. The server MAY send a Retry-After header field… to suggest an appropriate amount of time for the client to wait before retrying"* [src](https://www.rfc-editor.org/rfc/rfc9110.txt) §15.6.4 | fleet full — what vLLM and SGLang emit (§3.2) | Yes |
| **408 / 504** | the request timed out | avoid: for a streamed response the headers are already sent | — |

`Retry-After` syntax, verbatim
[src](https://www.rfc-editor.org/rfc/rfc9110.txt) §10.2.3: *"The Retry-After
field value can be either an HTTP-date or a number of seconds to delay after
receiving the response. `Retry-After = HTTP-date / delay-seconds`"*.

RFC 9110 also notes, and it is worth quoting to settle arguments: *"The existence
of the 503 status code does not imply that a server has to use it when becoming
overloaded. Some servers might simply refuse the connection."*

**Decision rule.** Emit 429 when the *tenant* is over quota (their problem,
fixable by them), 503 when the *fleet* is full (your problem, they should try
later or elsewhere). Always set `Retry-After`; compute it as the estimated
drain time, not a constant — a constant makes every rejected client return in
lockstep, which is a self-inflicted thundering herd. Add jitter on the
**server** side too: `Retry-After: ceil(drain_estimate × U(1.0, 1.5))`.

Return the reason in the body. Rejecting with a bare 503 makes it impossible for
a client to distinguish "fleet full, retry" from "this model is down, fail over".

### 3.6 Load shedding by priority

Priority is only meaningful where there is contention, and it must be configured
at every layer that queues. Dynamo documents the layering better than anyone
else, verbatim
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/use-cases/agents/priority-scheduling.md):

| Layer | What it controls | Required configuration |
|---|---|---|
| Frontend API | request schema and polarity | `nvext.agent_hints.priority` (soft) or `strict_priority` (router-only tier); HTTP headers `x-dynamo-request-priority` / `x-dynamo-request-strict-priority` |
| Router queue | which waiting request dispatches first | KV routing **plus** `--router-queue-threshold` *"set to a value that actually causes queueing"* |
| Backend engine | which admitted request the engine schedules first | vLLM `--scheduling-policy priority`; SGLang `--enable-priority-scheduling` |
| KV cache policy | which blocks survive memory pressure | SGLang `--radix-eviction-policy priority` |

The router queue key, verbatim: `(strict_priority, due_at, configured_policy_key)`,
with the warning that *"Sustained deadline-bearing traffic can therefore starve
non-deadline requests in the same class."*

And the crucial caveat, verbatim, which should be pasted into any design doc
that proposes priority as a capacity solution:

> *"Priority is not Kubernetes `PriorityClass`, GPU preemption, or a hard
> admission control policy. **It does not reserve capacity for high-priority
> requests.**"*
> *"Strict priority applies only to requests already parked in one scheduler
> queue. It does not preempt admitted work, impose ordering across router
> replicas or upstream queues, or guarantee backend engine execution order. An
> eligible new arrival can still be admitted directly while other requests are
> pending."*

**Therefore: to actually reserve capacity for a critical tier you must shed the
others**, which is what the GIE `Sheddable` rule does (§3.3), or run separate
replica pools. Priority alone reorders a queue; it does not create headroom.

**Weighted fair service across classes.** Dynamo's router arbitrates classes
with Deficit Round Robin, measured in *uncached tokens* rather than requests —
verbatim
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/deficit-round-robin.md):

```text
uncached_tokens = raw_isl_tokens - cached_tokens
scheduling_cost = max(1, uncached_tokens)
```

> *"Each physical policy class defines a positive `quantum`, measured in uncached
> tokens. A class with quantum `4096` earns four times as much DRR credit per
> round as a class with quantum `1024`. This weighting controls token service,
> not request count."*

Charging **uncached** tokens is the right unit: a request whose prefix is already
resident is genuinely cheaper, and charging it full price would penalise exactly
the traffic pattern you want to encourage.

### 3.7 Request-size-aware admission

A 1M-token prompt is not 250 × a 4K prompt: for DeepSeek-V4.1-Flash,
**prefill cost per token rises 37 %** from 128K to 1M because the CSA2 indexer's
quadratic term grows to 41 % of the GEMM term, so a 1M prompt is *"not 8× a
128 K prompt but ~10.5×"*
([`../models/deepseek41f/b300.md` §3.4](../models/deepseek41f/b300.md)).
Size-blind admission therefore under-charges the most expensive requests.

Levers, in increasing order of bluntness:

1. **`long_prefill_token_threshold`** (vLLM) — *"a request is considered long if
   the prompt is longer than this number of tokens"*; classifies rather than
   rejects, letting the scheduler cap how many long prefills run at once.
2. **`--prefill-max-requests`** (SGLang) — *"The maximum number of requests in a
   prefill batch. If not specified, there is no limit."*
3. **`--max-num-queued-tokens`** (vLLM) — `target_TTFT × prefill_throughput`,
   the single best formula in this document (§3.2).
4. **`--shortest-prefill-first`** (SGLang `--schedule-policy`) — SJF for prefill.
   Minimises mean TTFT; **starves long prompts** under sustained load. Use only
   with a deadline or aging mechanism.
5. **`max_model_len`** — the hard cap. Enforce it at the **gateway** as well, so
   an oversized prompt is rejected with a clear 400 before it consumes
   tokenization CPU (Q1, §1.1) on a GPU node.
6. **Per-tenant max prompt length** — different tiers, different caps.

**`max_model_len` is also a memory contract.** vLLM refuses to start if
`max_num_batched_tokens < max_model_len` with chunked prefill disabled
([`vllm/config/scheduler.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py),
`verify_max_model_len`), and `scheduler_reserve_full_isl=True` means the
scheduler *"checks whether the full input sequence length fits in the KV cache
before admitting a new request"*. Setting `max_model_len` to the model's maximum
when your traffic never approaches it wastes admission headroom and inflates
per-request reservations; set it to **p99.9 of observed prompt length, rounded
up**, and reject the tail explicitly.

### 3.8 Client retries, backoff and hedging

**Retries amplify.** At the moment your fleet is at 100 % capacity and rejecting
20 % of traffic, a client policy of "retry 3× immediately" turns 1.0× offered
load into up to **1.25×** (`1 + 0.2 + 0.2² + 0.2³ = 1.248`; the infinite-retry
limit `1/(1 − 0.2)` is 1.25 — recomputed 2026-09-19, this previously read 1.6×) and can convert a brief saturation into a sustained one. Mitigations, in
priority order:

1. **Exponential backoff with full jitter** on the client:
   `sleep = random_uniform(0, min(cap, base × 2^attempt))`. Jitter matters more
   than the backoff: without it, all rejected clients return simultaneously.
2. **Honour `Retry-After`** when present; it is better information than the
   client's own estimate.
3. **A retry budget (token bucket) rather than a per-request retry count.**
   Envoy's docs recommend exactly this over static circuit breaking: *"retry
   budgets"* as an alternative *"allowing sporadic failures while preventing
   cascading system-wide failures from retry volume explosion"*
   [src](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking).
   A budget of ~10–20 % of the success rate is the usual starting point.
   ⚠️ **TO BE VERIFIED** — no primary source found giving a recommended retry
   budget percentage for LLM inference specifically; 10–20 % is the general
   service-mesh convention. Method: measure your own 503 rate under a controlled
   burst and set the budget below the point where amplification is visible.
4. **Do not retry a partially streamed response.** The first token has shipped;
   a retry re-runs prefill and produces a different continuation.

⚠️ The AWS Builders' Library article "Timeouts, retries, and backoff with
jitter", the canonical primary source here, **redirects to
`builder.aws.com` and returned no article body to WebFetch on 2026-09-19** — so
the four points above are stated from general practice and the Envoy citation,
not quoted from it. **TO BE VERIFIED.**

**Hedged requests: do not, by default.** A hedge (send request 2 at p95 of the
latency distribution, take whichever answers first) is a good pattern for cheap
idempotent reads. For LLM generation it is usually wrong:

- The expensive half is **prefill**, and the hedge pays it in full. For
  DeepSeek-V4.1-Flash at 128K that is 2.37 PFLOP per hedged prompt
  ([`b300.md` §3.4](../models/deepseek41f/b300.md)).
- Hedging *adds* load precisely when the system is slow — the same
  amplification as retries, but unconditional.
- It breaks prefix-cache locality: the hedge lands on a different replica by
  construction, so it is guaranteed to be a cache miss, converting a ~90 %-hit
  request into a 0 %-hit one (this tree's measured agentic traces run 90–97 %
  GPU cache hit, [`b300.md` §3.2](../models/deepseek41f/b300.md)).

**When hedging is defensible:** short prompts (< ~1K tokens), a hard TTFT
deadline, a hedge fraction capped at a few percent of traffic by a budget, and
**cancellation of the loser the instant the winner's first token arrives** (§5.3).
Otherwise prefer *migration* (§5.4), which moves an in-flight request without
recomputing it.

### 3.9 Idempotency keys

For non-idempotent HTTP methods (`POST /v1/chat/completions`), a retried request
that the server actually completed is a duplicate charge and possibly a
duplicate side effect. The IETF draft
`draft-ietf-httpapi-idempotency-key-header-07` (2025-10-15, expired 2026-04-18)
defines the header
[src](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header):

- `Idempotency-Key` is *"an Item Structured Header"* whose value must be a
  string; it is recommended *"that a UUID or a similar random identifier be
  used"*.
- On a **duplicate of a completed** request the server should *"respond with the
  result of the previously completed operation, success or an error."*
- On a **concurrent, still-in-progress** duplicate the server *"SHOULD respond
  with a resource conflict error"* (HTTP 409).

For a streaming LLM endpoint this is harder than for a CRUD API, because
"replay the previous response" means replaying a token stream. Pragmatic
positions, in order of cost:

| Approach | Behaviour on duplicate | Cost |
|---|---|---|
| **Reject** | 409 while in flight; after completion, 409 too | Cheapest; forces the client to treat generation as non-retryable |
| **Dedupe only** | 409 while in flight; after completion, re-run | Prevents the *concurrent* double-spend, which is the common retry-storm case |
| **Replay** | store the full completion keyed by the idempotency key for a TTL and replay it | Correct per the draft; costs a response store. Useful mainly for non-streaming calls |

⚠️ **TO BE VERIFIED**: none of vLLM, SGLang, Dynamo, AIBrix or Agent Router was
found to implement `Idempotency-Key` natively as of 2026-09-19 (no reference in
any fetched doc). Method: grep the deployed gateway's config reference. If it
is not there, it belongs in your own API layer.

### 3.10 The admission-control ladder

Enable in this order; each rung is cheap and each one prevents the next
incident class.

| # | Control | Layer | Config | Prevents |
|---|---|---|---|---|
| 1 | `max_num_seqs` / `--max-running-requests` at the SLO batch | engine | §2.3 | TPOT collapse |
| 2 | `--max-num-queued-reqs` / `--max-queued-requests` | engine | §3.2 | unbounded TTFT |
| 3 | `--max-num-queued-tokens` = `target_TTFT × prefill_throughput` | engine | §3.2 | one 1M prompt blowing everyone's TTFT |
| 4 | `max_model_len` + gateway prompt-length cap | both | §3.7 | oversized-prompt OOM and wasted tokenizer CPU |
| 5 | Per-tenant RPM/TPM | gateway | §3.4 | noisy-neighbour |
| 6 | Per-tenant **concurrency** cap | gateway | §3.4 caveat | the lagging-quota hole |
| 7 | Priority classes + sheddable tier | router | §3.6 | critical traffic dying with batch traffic |
| 8 | `watermark` > 0 (vLLM) / `--schedule-conservativeness` > 1 (SGLang) | engine | §1.5 | preemption thrash |
| 9 | Circuit breakers + outlier ejection | gateway | §5.7 | one sick replica absorbing traffic |
| 10 | Client backoff+jitter, retry budget, `Retry-After` honoured | client | §3.8 | retry storms |

---

## 4. SLO-aware scheduling

### 4.1 Goodput

**Goodput = requests per second that *meet their SLO*.** Throughput counts
requests; goodput counts useful ones. Under overload throughput can stay flat
while goodput goes to zero — which is precisely the failure mode admission
control exists to prevent.

inference-perf's definition, verbatim
[src](https://raw.githubusercontent.com/kubernetes-sigs/inference-perf/main/docs/goodput.md):

> *"a request is considered 'good' if it completes successfully AND meets all
> specified constraints."*
> - *"Request Goodput: Number of good requests / Total benchmark time."*
> - *"Token Goodput: Total tokens (input + output) generated by good requests /
>   Total benchmark time."*
> - *"Goodput %: Percentage of total successful requests that met the
>   constraints."*

```yaml
report:
  goodput:
    constraints:
      ttft: 0.2
      tpot: 0.02
```

vLLM's benchmark implements the same idea, and its help text names the origin
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/benchmarks/serve.py):

> *"Specify service level objectives for goodput as "KEY:VALUE" pairs, where the
> key is a metric name, and the value is in milliseconds… Allowed request level
> metric names are "ttft", "tpot", "e2el". For more context on the definition of
> goodput, refer to DistServe paper: https://arxiv.org/pdf/2401.09670"*

Use `--goodput ttft:500 tpot:50` on `vllm bench serve` and stop reporting raw
throughput in capacity reviews.

### 4.2 Sarathi-Serve — chunked prefill and stall-free batching

arXiv **2403.02310**, OSDI '24 (Agrawal, Kedia, Panwar, Mohan, Kwatra, Gulavani,
Tumanov, Ramjee). Mechanism, verbatim from the abstract
[src](https://arxiv.org/abs/2403.02310): it *"introduces chunked-prefills which
splits a prefill request into near equal sized chunks and creates stall-free
schedules that adds new requests in a batch without pausing ongoing decodes."*

**Measured** serving-capacity gains vs vLLM (abstract, verbatim):

| Model | Hardware | Gain |
|---|---|---|
| Mistral-7B | single A100 | *"2.6x higher serving capacity"* |
| Yi-34B | two A100s | *"up to 3.7x higher serving capacity"* |
| Falcon-180B | with pipeline parallelism | *"up to 5.6x gain in the end-to-end serving capacity"* |

Status in 2026: **this is no longer a research technique, it is the default.**
vLLM V1: `enable_chunked_prefill: bool = True`. TRT-LLM: *"always enable context
chunking."* SGLang: on by default (`--chunked-prefill-size -1` disables it). The
paper's contribution survives as the *sizing question*, not the on/off question
(§4.9).

### 4.3 DistServe — prefill/decode disaggregation

arXiv **2401.09670**, OSDI '24 (Zhong et al.). Verbatim
[src](https://arxiv.org/abs/2401.09670): DistServe *"assigns prefill and
decoding computation to different GPUs, hence eliminating prefill-decoding
interferences"* and *"co-optimizes the resource allocation and parallelism
strategy tailored for each phase."*

**Measured**: *"serve 7.4x more requests or 12.6x tighter SLO, compared to
state-of-the-art systems, while staying within latency constraints for > 90% of
requests."*

**But the economics at real scale disagree with the paper's framing**, and this
tree already records it: PD disaggregation moves the frontier *"out above ~1000
GPUs; **inward** below"*, at **−20–30 % on small/untuned** deployments
([`serving-optimizations.md` §4.3](../cross-cutting/serving-optimizations.md),
[src](https://towardsdatascience.com/disaggregation-is-a-thousand-gpu-problem/)).

**Decision rule for this repo's fleet (2 nodes, 16 GPUs): do not disaggregate.**
16 GPUs is an order of magnitude below the break-even, and chunked prefill
already removes most of the interference DistServe targets. Revisit if (a) the
fleet exceeds ~1,000 GPUs, or (b) prefill and decode want genuinely different
parallelism — which for DeepSeek-V4.1-Flash they arguably do (7.89 B active
prefill vs 16.11 B active decode,
[`../METHODOLOGY.md` §8](../METHODOLOGY.md)) ⚠️ **TO BE VERIFIED** whether that
2.04× active-param asymmetry is large enough to justify disaggregation below the
1,000-GPU threshold; no published result for this model exists. Method: run
`vllm bench serve` with `--goodput` on an aggregated TP4 replica vs a
1P1D Dynamo shape at matched GPU count.

### 4.4 Llumnix — live migration as a scheduling primitive

arXiv **2406.03243**, OSDI '24 (Sun et al.). Verbatim
[src](https://arxiv.org/abs/2406.03243): a *"live migration mechanism for
requests and their in-memory states"*, used to *"improve load balancing and
isolation, mitigate resource fragmentation, and differentiate request priorities
and SLOs."*

**Measured**: tail latencies improved by *"an order of magnitude"*, high-priority
requests accelerated *"by up to 1.5x"*, and *"up to 36% cost savings while
achieving similar tail latencies."*

The production descendant of this idea is Dynamo's **request migration** (§5.4)
— though note the difference: Llumnix migrates *KV state* for load balancing;
Dynamo migrates *token state* for fault tolerance and replays it, which is
cheaper to implement and more expensive to execute.

### 4.5 Andes — QoE as the objective

arXiv **2404.16283** (2024-04-25, rev. 2024-12-13). Verbatim
[src](https://arxiv.org/abs/2404.16283): QoE for text streaming means
*"users receive the first token promptly and subsequent tokens at a smooth,
digestible pace, even during surge periods"*, delivered by a *"preemptive request
scheduler that dynamically prioritizes requests at the token granularity based
on each request's expected QoE gain and GPU resource usage."*

**Measured**, verbatim: *"Andes improves the average QoE by up to 4.7× given the
same GPU resource, or saves up to 61% GPU resources while maintaining the same
high QoE."* (The "up to" qualifiers were missing from the earlier paraphrase.)

The practical takeaway needs no research system: **there is no value in
generating tokens faster than the user consumes them.** A human reads at roughly
5–20 tokens/s; a 400 tok/s/user TPOT (which this tree measures for
DeepSeek-V4.1-Flash at low concurrency, [`b300.md` §3.2](../models/deepseek41f/b300.md))
is ~20–80× faster than needed for a human reader. Under burst, **deliberately
slowing human-facing streams to ~30 tok/s and reallocating the step budget to
queued requests raises goodput with zero perceived degradation** — the single
highest-leverage degradation lever in §5.6. ⚠️ Neither vLLM nor SGLang exposes a
per-request output-rate cap as of 2026-09-19 (none found in the fetched arg
definitions) — **TO BE VERIFIED**; today this must be implemented in the
gateway by pacing the SSE relay, which frees no GPU time unless the engine
backs off. That caveat is important: **gateway-side pacing alone does not
increase goodput**; it only smooths the user experience.

### 4.6 Aladdin — joint placement and scaling

arXiv **2405.06856**. Verbatim
[src](https://arxiv.org/abs/2405.06856): it *"co-adaptively places queries and
scales computing resources with SLO awareness"*, predicting minimal resources
and placing queries *"according to the prefill and decode latency models of
batched LLM inference."* **Measured**: *"reduces the serving cost of a single
model by up to 71% for the same SLO level compared with the baselines."*

Aladdin's transferable idea is the **fitted latency model**, not the scheduler:
predict `TTFT(prompt_len, batch)` and `TPOT(batch, ctx)` from measurements, then
admit only requests whose predicted completion meets the SLO. That is
implementable today against any engine using the per-pair tables in this tree
(e.g. [`../models/deepseek41f/b300.md` §3.4](../models/deepseek41f/b300.md)) as
the model, refitted from production metrics.

### 4.7 VTC — fairness between tenants

arXiv **2401.00588** (Sheng et al., OSDI '24). Verbatim
[src](https://arxiv.org/abs/2401.00588): fairness is defined on *"a cost function
that accounts for the number of input and output tokens processed"*, served by
*"the Virtual Token Counter (VTC), a fair scheduler based on the continuous
batching mechanism"*, with a proved *"2x tight upper bound on the service
difference between two backlogged clients, adhering to the requirement of
work-conserving."*

**Why this matters more than rate limiting**: the paper's own framing is that
*"current rate-limiting fairness mechanisms lead to resource underutilization
when capacity is available"*. A static per-tenant RPM cap wastes the fleet when
only one tenant is active; VTC gives that tenant everything and still bounds
unfairness when a second arrives.

Available in production today as AIBrix's `vtc-basic` routing strategy —
*"hybrid scoring balancing per-user token fairness and pod utilization"*
[src](https://aibrix.readthedocs.io/latest/features/gateway-plugins.html) — and
as Dynamo's DRR class quanta measured in uncached tokens (§3.6), which is a
coarser but equivalent-in-spirit mechanism.

**Decision rule.** Multi-tenant fleet with bursty per-tenant load → prefer
work-conserving fairness (VTC/DRR) over static quotas, and keep static quotas
only as the **cost** ceiling (§3.4).

### 4.8 What actually moves TTFT vs TPOT

This is the table to consult before touching a knob. Evidence columns are from
this tree's existing measurements; nothing here is new.

| Knob | TTFT | TPOT / ITL | Throughput | Evidence |
|---|---|---|---|---|
| ↑ `max_num_seqs` | ↑ (more queueing before admission relieved) | ↑ **linearly** | ↑ until the MFU/MBU ceiling, then flat | [`b300.md` §3.4](../models/deepseek41f/b300.md): flat at 7,191 tok/s from batch 32 |
| ↑ `max_num_batched_tokens` | ↓ | ↑ | ↑ | 16384→2048: −64 % throughput, p99 ITL 342→194 ms [src](https://github.com/vllm-project/vllm/issues/56975) |
| ↑ CUDA-graph capture size | ↑ p99 **+19–31 %** | ↓ p99 ITL **196–340 → 112–120 ms** | −5 to −10 % | [`serving-optimizations.md` §4.2](../cross-cutting/serving-optimizations.md) |
| Prefix caching | ↓↓ | — | ↑ unless overlap ≈ 0 | +13–49 % vs −36.7 % [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |
| Speculative decoding, small γ (MTP) | — | ↓ | ~neutral | GB200 +14 % TPS/GPU; GB300 −0.9 % [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| Speculative decoding, large γ (DSpark) | — | ↓↓ at low concurrency | **↓ at high concurrency unless trimmed** | [`serving-optimizations.md` §2.5](../cross-cutting/serving-optimizations.md) |
| Adaptive verification | — | ↓ | keeps the frontier across the sweep | [src](https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification) |
| Dynamic chunking at long ctx | **↓↓ (15.2 s → 8.6 s at 128K)** | — | — | [`serving-optimizations.md` §5.4](../cross-cutting/serving-optimizations.md), [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| Adaptive scheduling budget (K3) | **↓ 55–65 %** | — | **+41.5 %** | [src](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization) |
| DCP | — | ↓ at high concurrency | ↑↑ at c=512 (6,091 vs 1,863 tok/s/GPU) | [src](https://vllm.ai/blog/2026-08-07-decode-context-parallelism) |
| PD disaggregation | ↓ | ↓ | **↓ below ~1000 GPUs** | §4.3 |
| `--prefill-decode-interval` N (SGLang) | ↑ | ↓ | ~neutral | [`schedule.py`](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/arg_groups/fields/schedule.py) |

One engine-choice datum that dwarfs most of these, already in this tree
[src](https://github.com/vllm-project/vllm/issues/56975): on the same job, SGLang
ran *"p50 ITL 13–16 ms and p99 ITL 13/16/20 ms (decode-only batching), at the
cost of 1.2–4.5× higher TTFT"* versus vLLM's chunked-prefill mixing. **That is
the whole TTFT↔ITL trade expressed as an engine choice**, and it is larger than
any single flag. If your SLO is ITL-shaped, evaluate SGLang; if TTFT-shaped,
vLLM's default mixing.

### 4.9 Chunked prefill sizing — the decision rule

```
1. Measure p99 ITL and p99 TTFT at the target concurrency.
2. If p99 TTFT is the violation  → raise max_num_batched_tokens (cheap).
3. If p99 ITL is the violation   → try, in this order:
     a. raise the CUDA-graph capture size to cover prefill-containing steps
        (~5-10 % throughput for ~2-3x better p99 ITL)
     b. lower max_num_active_seqs (no graph recapture, linear TPOT win)
     c. lower max_num_batched_tokens  (LAST - up to -64 % throughput)
4. If both violate → you are over capacity. Shed (§3), do not tune.
```

Step 3a is justified by the mechanism: *"prefill-containing steps run outside
CUDA graphs unless the capture size covers them"*
([`serving-optimizations.md` §4.2](../cross-cutting/serving-optimizations.md)).
Step 4 is the one people skip, and it is the only correct answer when both
percentiles are out: no scheduler setting creates FLOPs.

### 4.10 When PD disaggregation removes the interference

Chunked prefill **bounds** prefill/decode interference; disaggregation
**removes** it. The residual interference under chunked prefill is exactly the
per-step prefill slab:

```
ITL_penalty_per_step ≈ prefill_chunk_tokens / prefill_throughput
```

For DeepSeek-V4.1-Flash at TP4 with `B = 8192`, `max_num_seqs = 256`, γ = 5:
`prefill_chunk ≤ 8192 − 1536 = 6,656` tokens; at the measured prefill rate
implied by TTFT 129 ms for 4,096 tokens (≈ 31,750 prompt tok/s per replica,
`est.` from [`b300.md` §3.4](../models/deepseek41f/b300.md)), that is **≈ 210 ms
of prefill in a step whose decode part is 17.8 ms** — a **12× ITL spike** on any
step that carries a full prefill chunk.

**That is the number that decides whether you need disaggregation.** If your ITL
SLO tolerates occasional 210 ms steps (a human reader does; a real-time voice
pipeline does not), chunked prefill suffices. If it does not, and the fleet is
too small for disaggregation to pay (§4.3), the options are: shrink the chunk
(§4.9 step 3c, expensive), or split traffic into separate prefill-heavy and
decode-heavy **replica pools** — a poor-man's disaggregation with no KV transfer,
which costs cache locality but needs no new machinery.

---

## 5. Failure handling under load

### 5.1 KV OOM and the preemption storm

The dynamics: KV fills → scheduler preempts → preempted request re-enters the
waiting queue → it is re-admitted → it re-prefills (RECOMPUTE) → KV fills again.
Each cycle *adds* prefill work, so the system does **more** work at **lower**
goodput. Left alone it does not recover until arrivals drop.

| Signal | Threshold | Action |
|---|---|---|
| `vllm:num_preemptions_total` rate | > 0 sustained | lower `max_num_active_seqs`; raise `watermark` |
| `vllm:kv_cache_usage_perc` | *"consistently near 100%"* → *"new requests are forced to wait"* [src](https://developers.redhat.com/articles/2026/03/09/5-steps-triage-vllm-performance) | same |
| SGLang retraction rate | any | raise `--schedule-conservativeness` above 1.0 |

The **preventive** settings are `watermark > 0` (*"headroom helps avoid frequent
KV cache eviction and the resulting repeated preemption"*) and
`scheduler_reserve_full_isl = True` (*"Prevents over-admission and KV cache
thrashing with chunked prefill"*) — both from
[`vllm/config/scheduler.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py).
Note `watermark` defaults to **0.0**, i.e. disabled; a fleet running near
capacity should set it. ⚠️ No published guidance on a good value was found —
**TO BE VERIFIED**. Method: set it to
`(largest expected single-request KV) / (total KV blocks)` so one arrival can
always be admitted without eviction, then measure the preemption rate.

**For this repo's DeepSeek-V4.1-Flash and Kimi-K3 pairs, raising
`tensor_parallel_size` does not fix KV OOM**, because the MLA latent KV is
replicated per rank under plain TP
([`../matrix/fit-matrix.md` §6.3](../matrix/fit-matrix.md)). DCP is the only
parallelism that shards it — 6,091 vs 1,863 tok/s/GPU at c=512
([`serving-optimizations.md` §4.3](../cross-cutting/serving-optimizations.md)).
Following the generic vLLM advice here would waste a maintenance window.

### 5.2 CUDA errors mid-batch and engine death

A CUDA fault (illegal memory access, NCCL timeout, ECC error) is **not**
recoverable per-request: the CUDA context is poisoned and every request sharing
the process dies. Design consequences:

- **Blast radius = the engine process = one TP group.** At TP4 on B300, one
  fault kills 4 GPUs' worth of in-flight work; at TP8 for Kimi-K3, a whole node.
  That is a direct argument for the **smallest TP that meets the SLO** — which
  for DeepSeek-V4.1-Flash on B300 is TP2 by throughput and TP4 by interactivity
  ([`b300.md` §3.5](../models/deepseek41f/b300.md)) — 4 independent TP2 replicas
  per node quarter the blast radius of one TP8 replica.
- **Fail fast and restart**, do not try to continue. The liveness/readiness split
  matters: Dynamo's frontend, verbatim, *"`/health` returns 503 so readiness
  routing stops, while `/live` remains 200 so Kubernetes does not restart the
  process during the drain"*
  [src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/kubernetes/fault-tolerance/graceful-shutdown.md).
  Copy that split: **readiness sheds traffic, liveness kills the pod.** Wiring an
  overload condition into the *liveness* probe turns a load spike into a crash
  loop.
- **Restart cost is the recovery SLO.** DeepSeek-V4.1-Flash: 307.5 GB resident
  (Engram host-offloaded) = **1.0 min at 5 GB/s**, plus CUDA-graph capture across
  26 sizes; Kimi-K3: 1,560.9 GB. The cluster has 30.72 TB of local NVMe per
  `p6-b300.48xlarge` node, so the checkpoint should be staged locally, never
  pulled from object storage on the restart path
  ([`b300.md` §5.3](../models/deepseek41f/b300.md)).

⚠️ vLLM's behaviour on engine-core death (whether in-flight requests receive an
error response or hang until client timeout) was not confirmed from a primary
source on 2026-09-19 — **TO BE VERIFIED**. Method: kill the engine-core process
under load and observe the client-visible result and the HTTP status.

### 5.3 Request cancellation

Cancellation is a **capacity feature**, not a politeness feature. A user who
closes the tab while a 2,000-token generation runs is holding a KV slot and a
step-budget share for as long as you let them.

- Both engines support abort (`AbortReq` in SGLang's scheduler; vLLM aborts on
  client disconnect for streaming responses). SGLang reports aborted requests
  under `vllm`-style finish reasons with `"type": "abort"`.
- vLLM's request-success counter is labelled by finish reason, with values
  including `"abort"`
  [src](https://docs.vllm.ai/en/latest/design/metrics/) — **track it**: a rising
  abort share means users are giving up, which is a leading indicator of an SLO
  breach that latency percentiles may not yet show (slow requests that are
  abandoned never contribute a completed-request latency sample, so p99 can look
  *better* as service degrades).
- **The disconnect must propagate end to end.** A gateway that buffers the SSE
  stream, or a router that does not forward cancellation, silently defeats this.
  Verify it explicitly in a test: start a long generation, kill the client,
  watch `vllm:num_requests_running` drop.

### 5.4 Engine restarts with in-flight requests

Dynamo implements **request migration**: continue an in-flight generation on a
healthy worker *"from the exact point of failure — no tokens lost or duplicated,
and no interruption visible to the client"*
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/kubernetes/fault-tolerance/request-migration.md).

```yaml
  - name: Frontend
    type: frontend
    replicas: 1
    podTemplate:
      spec:
        containers:
        - name: main
          image: ${RUNTIME_IMAGE}
          env:
          - name: DYN_MIGRATION_LIMIT
            value: "3"                 # allow up to 3 migration attempts
```

Verbatim: migration is *"**off by default** (`--migration-limit 0`)"*, and
*"Start with `3` — enough to survive transient worker loss without retrying
indefinitely."* Memory is bounded by `DYN_MIGRATION_MAX_SEQ_LEN`: *"Once a
request's total sequence length (prompt + generated tokens) **strictly exceeds**
this limit, migration is disabled for that request and token tracking stops."*

**Three limitations you must design around**, verbatim from the same page:

1. *"Request migration is **not supported** for OpenAI-compatible requests that
   ask for multiple generated choices with `n > 1`."*
2. *"Request migration is **not supported** for requests that use guided decoding
   (structured output / JSON schema)… a migrated worker replays prior tokens as
   context but starts the FSM from the schema root, and that mismatch produces
   corrupted output (typically duplicated or nested JSON). This applies equally
   to all backends (vLLM, SGLang, TRT-LLM)."*
3. `--migration-max-seq-len` silently disables migration past the cap; watch
   `dynamo_frontend_model_migration_max_seq_len_exceeded_total`.

**Limitation 2 is a big deal for agentic traffic**, which is overwhelmingly
JSON-structured tool calling — exactly the workload the InferenceX measurements
for DeepSeek-V4.1-Flash on B300 use (`benchmark_type: agentic_traces`,
[`b300.md` §3.2](../models/deepseek41f/b300.md)). **For a structured-output
fleet, migration provides no protection and drain time is the only defence.**

Migration counters to alert on (same page):
`dynamo_frontend_model_migration_total` (labels `model`, `migration_type` =
`new_request` | `ongoing_request`) and
`dynamo_frontend_model_migration_duration_seconds` (labels include `outcome` =
`success` | `failure` | `cancelled`).

The local-inhibition window is worth knowing for incident analysis: after a
failure, *"each runtime client temporarily removes the failed worker from its
local routing set… The default local inhibition window is `5` seconds"*
(`DYN_RUNTIME_INHIBITED_DURATION_SECS`).

### 5.5 Draining

Kubernetes' documented termination sequence
[src](https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/concepts/workloads/pods/pod-lifecycle.md):

1. Pod marked Terminating; kubelet begins local shutdown.
2. *"If one of the Pod's containers has defined a `preStop` hook and the
   `terminationGracePeriodSeconds` in the Pod spec is not set to 0, the kubelet
   runs that hook inside of the container. The default
   `terminationGracePeriodSeconds` setting is 30 seconds."* If the hook overruns,
   *"the kubelet requests a small, one-off grace period extension of 2 seconds."*
3. *"The kubelet triggers the container runtime to send a TERM signal to process
   1 inside each container."*
4. **In parallel**, the control plane removes the pod from EndpointSlices —
   *"Any endpoints that represent the terminating Pods are not immediately
   removed from EndpointSlices, and a status indicating terminating state is
   exposed… Terminating endpoints always have their `ready` status as `false`…
   so load balancers will not use it for regular traffic."*
5. After the grace period, SIGKILL.

**30 seconds is far too short for LLM inference.** A 2,000-token generation at
TPOT 35.6 ms takes 71 s of decode alone; at the Kimi-K3 interactive operating
point TTFT alone is 10,139 ms ([`pairs.json`](../matrix/pairs.json)). Dynamo's
own guidance and defaults
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/kubernetes/fault-tolerance/graceful-shutdown.md):

| Workload | Suggested `terminationGracePeriodSeconds` |
|---|---|
| Short requests (< 10s) | 60s |
| Long generation (> 30s) or high utilization | 120s+ |

```yaml
apiVersion: nvidia.com/v1alpha1
kind: DynamoGraphDeployment
spec:
  services:
    worker:
      extraPodSpec:
        terminationGracePeriodSeconds: 180  # allow time for request draining
```

| Variable | Default | Purpose (verbatim) |
|---|---|---|
| `DYN_HTTP_GRACEFUL_SHUTDOWN_TIMEOUT_SECS` | `5` | *"How long the Frontend waits for admitted HTTP and WebSocket inference requests to finish before it cancels runtime state."* |
| `DYN_GRACEFUL_SHUTDOWN_GRACE_PERIOD_SECS` | `5` | *"How long workers keep serving after endpoints unregister from discovery, before endpoints are invalidated."* |
| `DYN_RUNTIME_GRACEFUL_SHUTDOWN_TIMEOUT_SECS` | `900` | *"Upper bound on waiting for in-flight requests to finish."* |

And the ordering rule, verbatim: *"Keep every internal timeout below
`terminationGracePeriodSeconds` so Dynamo can finish its own cleanup before
Kubernetes force-kills the pod."*

⚠️ Note the default `DYN_HTTP_GRACEFUL_SHUTDOWN_TIMEOUT_SECS = 5` is **shorter
than a single long generation**, so the out-of-box behaviour cancels in-flight
requests 5 seconds into a rollout. Raise it (or accept that migration, §5.4, is
doing the work — and remember migration does not cover structured output).

**Drain checklist:**
1. Readiness → false first; wait ≥ 2 × the endpoint-propagation interval before
   SIGTERM (a `preStop: sleep 10` is the standard hack, and is still the standard
   hack in 2026).
2. Stop admitting (503 on new requests) but keep streaming admitted ones.
3. Wait for `num_requests_running == 0` or the drain timeout.
4. Exit 0.

### 5.6 Graceful degradation ladder

Apply in order; each step is reversible and each buys more than the last.

| # | Lever | Effect | Cost | Reversible live? |
|---|---|---|---|---|
| 1 | Shed `Sheddable` tier at the router | frees the whole batch budget that tier used | batch jobs wait | Yes |
| 2 | Lower `max_num_active_seqs` (vLLM) | TPOT ↓ linearly, no graph recapture | fewer seats | **Yes** — the best live lever |
| 3 | Cap `max_tokens` server-side (e.g. 4096 → 1024) | frees KV slots ~4× sooner | truncated answers; must be signalled via `finish_reason: "length"` | Yes |
| 4 | Disable speculative decoding at high batch | frees verification compute and shrinks the graph ladder | TPOT ↑ at low batch | ⚠️ restart usually required |
| 5 | Lower `max_num_batched_tokens` | p99 ITL ↓ | up to −64 % throughput | ⚠️ restart |
| 6 | Reject at the engine (`max_num_queued_reqs`) | bounded TTFT for whoever is admitted | rejections | Yes |

Step 4's justification is this tree's own finding: speculative decoding with
large γ moves the frontier *"strongly out at low concurrency, **inward** at high
concurrency unless trimmed"*
([`serving-optimizations.md` §4.3/§2.5](../cross-cutting/serving-optimizations.md)).
Under burst you are, by definition, at high concurrency. The better answer is
vLLM's **adaptive verification**, which *"keeps you on the frontier across the
whole sweep"* [src](https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification)
and is already in this repo's pinned DeepSeek launch config
(`"enable_adaptive_verification": true`,
[`b300.md` §5.2](../models/deepseek41f/b300.md)) — **so step 4 should already be
automatic on this fleet**, and step 4 is listed only for deployments that have
not enabled it.

### 5.7 Circuit breakers at the gateway

Envoy's are concurrency limits, not classic trip-and-reset breakers. Verbatim
[src](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking):

| Threshold | Definition |
|---|---|
| Cluster maximum connections | *"The maximum number of connections that Envoy will establish to all hosts in an upstream cluster."* |
| Cluster maximum pending requests | *"The maximum number of requests that will be queued while waiting for a ready connection pool connection."* |
| Cluster maximum requests | *"The maximum number of requests that can be outstanding to all hosts in a cluster at any given time."* |
| Cluster maximum active retries | *"The maximum number of retries that can be outstanding to all hosts in a cluster at any given time."* |
| Cluster maximum concurrent connection pools | *"The maximum number of connection pools that can be concurrently instantiated."* |

The design note: *"fully distributed (not coordinated) circuit breaking"* and
*"Worker threads share circuit breaker limits."*

**Settings for an LLM backend** (long-lived streaming connections, tiny request
rate, enormous per-request duration):

- `max_requests` ≈ the fleet's **SLO** concurrency (§2.3), not its KV
  concurrency. For the §8 fleet: `4 replicas × 256 = 1,024`.
- `max_pending_requests` ≈ the queue depth you sized in §3.2 — this is the
  gateway's own version of `max_num_queued_reqs`, and it fails **fast**
  (Envoy overflow) rather than slow.
- `max_retries` low, plus a **retry budget** (§3.8).
- **Outlier detection** for the "sick but alive" replica — one that answers
  slowly because its KV pool has collapsed. Envoy ejects on consecutive 5xx /
  gateway failures; a replica that is merely *slow* will not trip it, so pair it
  with the router's busy thresholds (§3.3), which are the only mechanism here
  that reacts to load rather than to errors.

### 5.8 Failure-mode table

| Failure | First signal | Automatic response | Operator response |
|---|---|---|---|
| Arrival burst | `vllm:request_queue_time_seconds` p99 ↑ | queue absorbs; then 503 | verify shed tiers; do not scale (too slow, §8.4) |
| KV pressure | `kv_cache_usage_perc` → 1.0, then `num_preemptions_total` ↑ | preempt/retract | lower `max_num_active_seqs`; raise `watermark` |
| One slow replica | per-replica TTFT divergence | router busy-threshold ejection | check for preemption on that replica only |
| Engine death | `/health` 503, liveness OK during drain | pod restart; migration if enabled and not structured-output | confirm it is not OOM-looping |
| Node loss | endpoints removed | migration ×3, then client error | capacity is now N−8 GPUs; shed a tier |
| Retry storm | request rate ↑ while success rate ↓ | retry budget exhausts | raise `Retry-After`; confirm client jitter |
| Tokenizer/mm stall | probe-request p99 ↑ with engine metrics flat | — | cap prompt/video size (§6.4) |
| Cache-hit collapse | `vllm:prefix_cache_hits` / `..._queries` ↓ | — | check routing; a failover often destroys locality |

---

## 6. Long-context and multimodal concurrency

### 6.1 The concurrency wall

Per-sequence cost scales with context, so seats collapse as prompts grow. From
[`pairs.json`](../matrix/pairs.json) and the pair docs, on B300:

| Model | @8K | @128K | ratio | Reason the ratio is not 16× |
|---|--:|--:|--:|---|
| DeepSeek-V4.1-Flash (TP4, replicated KV) | 11,184 | 953 | 11.7× | fixed 2.77 MiB SWA ring amortises away |
| DeepSeek-V4.1-Flash-NVFP4 (TP4, replicated) | 10,199 | 869 | 11.7× | same |
| Qwen3.8-27B (TP1) | 325 | 45 | 7.2× | 392 MB GDN state is a large constant |
| **Kimi-K3 (TP8+DCP8)** | **101** | **64** | **1.6×** | **2.25 GB KDA state dominates until ~163 K tokens** |
| Marlin-2B (TP1) | 1,934 | 142 | 13.6× | small constant state |

**Two different capacity regimes, and they need different admission policies:**

- **KV-dominated** (DeepSeek, Marlin): admit on a **token** budget. A long prompt
  genuinely costs proportionally more. `max_num_queued_tokens` and token quotas
  work.
- **State-dominated** (Kimi-K3, Qwen3.8-27B at short context): admit on a
  **request** budget. A long prompt costs barely more than a short one, so a
  token quota mis-prices everything. A per-tenant *concurrency* cap is the
  correct control.

### 6.2 Prefill chunk scheduling at long context

Already measured in this tree, DeepSeek-R1-NVFP4 at 128K prefill
([`serving-optimizations.md` §5.4](../cross-cutting/serving-optimizations.md),
[src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/)):

| Strategy | GB300 TTFT | GB200 TTFT |
|---|---|---|
| No chunking | 15.2 s | 18.6 s |
| **32K dynamic chunking** | **8.6 s** | — |

*"Dynamic chunking with a 32K initial chunk nearly halves 128K TTFT… most of the
8.6 s figure is scheduling, not silicon."* SGLang exposes this as
`--enable-dynamic-chunking`: *"chunk sizes are dynamically calculated based on
fitted function to maintain consistent execution time across chunks"*
[src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/arg_groups/fields/schedule.py)
— note its documented scope is *"for pipeline parallelism"*, ⚠️ so whether it
applies to a TP-only shape is **TO BE VERIFIED**. Method: enable it on a TP4
DeepSeek replica and compare 128K TTFT.

vLLM's equivalent for Kimi-K3 is the **adaptive scheduling budget**: *"TTFT down
55–65 %, throughput up to 41.5 %"*
[src](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization).

**The concurrency consequence of a long prefill is queueing, not memory.** A
128K prompt at TP4 occupies ~20 consecutive steps of prefill headroom (§2.4).
At 32 concurrent 128K arrivals that is 640 steps — roughly 11 s at 17.8 ms/step
— during which every decoding request's ITL is elevated. This is why
`max_num_queued_tokens` (§3.2) exists and why it is expressed in **tokens**.

### 6.3 KV offload to CPU/NVMe — keeping sessions alive

Offload does not raise *running* concurrency (a decoding request's KV must be
resident); it raises the number of **paused sessions** you can resume cheaply,
which is what long multi-turn agentic traffic actually needs.

vLLM's `OffloadingConnector`, verbatim
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/kv_offloading_usage.md):
it *"extends the prefix cache by offloading completed KV blocks to slower but
larger tiers (CPU host memory, plus optional secondary tiers) as they are
produced. Hits in the offload tiers are promoted back to GPU on demand.
Transfers between GPU and CPU use DMA (`cudaMemcpyAsync`) and run asynchronously
alongside model computation, so offloading adds minimal CPU- and GPU-core
overhead."*

```bash
vllm serve <model> \
  --kv-transfer-config '{
    "kv_connector": "OffloadingConnector",
    "kv_role": "kv_both",
    "kv_connector_extra_config": {
      "spec_name": "TieringOffloadingSpec",
      "cpu_bytes_to_use": 10737418240,
      "block_size": 16,
      "eviction_policy": "lru",
      "secondary_tiers": [
        {
          "type": "fs",
          "root_dir": "/mnt/kv_cache",
          "n_read_threads": 32,
          ...
```

Structural constraint, verbatim: *"Only the CPU primary tier has direct GPU
access. Secondary tiers cannot read from or write to GPU memory; all
GPU↔secondary transfers are staged through the CPU primary tier."* So NVMe
capacity is real but every NVMe hit pays two hops.

The **per-request admission control** for offload is `max_load_tokens`:

```json
{ "kv_transfer_params": { "max_load_tokens": 0 } }
```

*"The cap applies to tokens beyond those already available in the GPU prefix
cache. Set it to `0` to disable external loading for the request."* Marked
*"experimental and subject to change"*. Under burst this is the lever that stops
a flood of cache-restore traffic from saturating the PCIe path: **cap or disable
offload loads for low-priority tenants while keeping GPU prefix-cache reuse.**

LMCache offers the same tiering vendor-neutrally: *"move KV caches out of GPU
memory into a tiered storage hierarchy spanning CPU memory, local storage, and
remote backends"* [src](https://docs.lmcache.ai/), integrated with vLLM and (as
of 2025/09 per its own timeline) Dynamo. Mooncake's contribution is the
overload-specific one — a *"KVCache-centric disaggregated architecture"* plus
*"a prediction-based early rejection policy"* — claiming *"up to a 525 % increase
in throughput in certain simulated scenarios"* and *"75 % more requests"* under
real workloads while meeting SLOs [src](https://arxiv.org/abs/2407.00079).
**Prediction-based early rejection is exactly §3 done well**: reject at arrival
using a *predicted* completion time rather than a static queue-depth threshold.

This fleet's hardware supports the whole hierarchy: `p6-b300.48xlarge` carries
**8 × 3.84 TB = 30.72 TB local NVMe and 4 TB of system RAM**
([`b300.md` §5.3](../models/deepseek41f/b300.md)) — enough to hold thousands of
paused 128K sessions. ⚠️ The end-to-end resume latency from NVMe for these
models is unmeasured — **TO BE VERIFIED**. Method: `TieringOffloadingSpec` with
an `fs` tier on local NVMe, then compare TTFT for a cold, a CPU-resident and an
NVMe-resident prefix at 128K.

Also in flight: vLLM's **Hybrid HiSparse** (targeted at v0.30, **not released**
as of 0.29.0), which keeps sparse-MLA KV on GPU *"while capacity allows and
offloads under pressure"* with three residency states, measured on 8×H200 with
GLM 5.3 as *"substantially higher concurrency at all context lengths vs standard
KV offloading"*
([`serving-optimizations.md` §5.5](../cross-cutting/serving-optimizations.md),
[src](https://vllm.ai/blog/2026-09-08-glm53-part1-hybrid-sparse-offloading)).

### 6.4 Video concurrency: Marlin-2B and the 240-frame cap

Marlin-2B accepts up to **240 frames at 2 fps, 200,704 px/frame**
([`../METHODOLOGY.md` §8](../METHODOLOGY.md)), which means:

- Every clip ≥ 2 minutes produces the **same** context, 23,520 tokens, so the
  fleet sits permanently on one operating point: **753 concurrent 2-minute
  videos per GPU at BF16 KV**, 1,417 at FP8 KV, 2,532 at NVFP4 KV
  ([`../models/marlin2b/b300.md`](../models/marlin2b/b300.md)).
- The ViT burst is **94,080 ViT tokens in a single varlen forward**, and it is
  *"per concurrent prefill, not per sequence"* (ibid. §1.4) — an
  **activation-workspace** cost that scales with the number of prefills running
  together, not with the number of sessions. The correct control is therefore
  `--prefill-max-requests` (SGLang) or the `max_num_batched_tokens`-derived
  encoder budget (vLLM, §2.7), **not** `max_num_seqs`.
- TTFT at the video operating point is **186.0 ms at 0 % prefix hit**, and *"the
  90 % column is fiction for this workload"* because *"every request carries a
  different video and the video precedes the prompt"* (ibid. §3.3). **Assume 0 %
  cache hit for video admission**, unlike the 90–97 % this tree measures for
  agentic text.
- The pair doc's own warning is the §6 thesis in one sentence: *"At the
  concurrencies in §3.2, queueing dominates TTFT. 753 concurrent video prefills
  at 186 ms each on one GPU is 140 seconds of serial prefill work. The real TTFT
  at the KV-max operating point is a scheduling question, not a roofline one."*

**Therefore, for a video fleet, admit on prefill throughput, not on KV.** The
admission formula is `max_num_queued_tokens = target_TTFT × prefill_throughput`
(§3.2) with prefill throughput measured in *video-tokens per second including
the ViT*, and `max_num_seqs` set well below the KV cap. Concretely: to hold
p99 TTFT ≤ 2 s at 186 ms per prefill, no more than ~10 video prefills may be
in flight or queued on a GPU — **against a KV cap of 753**, a 75× gap in the
same direction as §2.3's 136× gap.

One more capacity lever specific to video: hardware decode. This tree records
that GPU video decode *"more than double[s] the throughput"* vs the CPU decoder
for video captioning on 8×H100, enabled with
`--media-io-kwargs '{"video":{"backend":"pynvvideocodec"}}'` and
`--mm-ipc-gpu-memory-gb 2`
([`../models/marlin2b/b300.md`](../models/marlin2b/b300.md),
[src](https://vllm.ai/blog/2026-09-18-pynvvideocodec)). Without it, **Q1 (§1.1)
is the bottleneck and no engine tuning will help** — the video decode of a
2-minute clip is happening on a CPU core while a B300 idles.

### 6.5 Encoder placement

Three placements, increasing in separation:

1. **In-process encoder** (default). Simple; the ViT burst competes with LLM
   prefill for the same step budget and the same activation workspace.
2. **Data-parallel encoder within the TP group** — `--mm-encoder-tp-mode data`,
   which this repo's DeepSeek-V4.1-Flash recipe ships
   ([`b300.md` §5.2](../models/deepseek41f/b300.md)). One full encoder copy per
   rank (costing 970.5 MB replicated on the NVFP4 build,
   [`../models/deepseek41fnvfp4/b300.md` §1.3](../models/deepseek41fnvfp4/b300.md))
   in exchange for no encoder all-reduce.
3. **Disaggregated encoder** — a separate vLLM instance. Verbatim benefits
   [src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/disagg_encoder.md):
   *"Independent, fine-grained scaling"*, *"Lower time-to-first-token (TTFT)"*
   (*"Language-only requests bypass the vision encoder entirely"*), and
   *"Cross-process reuse and caching of encoder outputs"* — *"A remote, shared
   cache lets any worker retrieve existing embeddings, eliminating redundant
   computation."* Reference pathway is `ExampleConnector` with `NixlConnector`
   examples; shapes are `E→PD` and `E→P→D`.

**Decision rule.** Mixed text+video traffic where text requests must not pay the
encoder's TTFT → disaggregate the encoder (it is a much smaller, cheaper scaling
unit than the LLM, and unlike PD disaggregation it does not need a 1,000-GPU
fleet to pay off, because the encoder is not the memory-bound half). Pure video
traffic → keep it in-process and spend the effort on hardware decode instead.
Also set `disable_chunked_mm_input` deliberately: leaving it `False` lets a
multimodal item be split across steps, which is better for ITL and worse for
encoder-cache behaviour.

---

## 7. Testing concurrency

### 7.1 The tools

| Tool | Owner | Load model | Goodput? | Best for | Source |
|---|---|---|---|---|---|
| `vllm bench serve` | vLLM | rate (Poisson/Gamma via `--burstiness`) or `inf` + `--max-concurrency` | ✅ `--goodput ttft:.. tpot:.. e2el:..` | engine-level A/B on one replica; **`--probe-request-rate` for interference** | [docs/benchmarking/cli.md](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/benchmarking/cli.md) |
| **GuideLLM** | vLLM project | profiles: `synchronous`, `concurrent`, `throughput`, `constant`, `poisson`, `sweep` | SLO-aware | **finding the saturation point** in one run; has an `over_saturation` constraint | [docs](https://raw.githubusercontent.com/vllm-project/guidellm/main/docs/getting-started/benchmark.md) |
| **inference-perf** | Kubernetes SIG (wg-serving) | `constant`, `poisson`, `concurrent`; multi-stage; trace replay | ✅ global + **per-request via headers** | **fleet-level** testing through a gateway; 10k+ QPS; agentic/multi-turn traffic | [README](https://raw.githubusercontent.com/kubernetes-sigs/inference-perf/main/README.md) |
| **AIPerf** (successor to GenAI-Perf) | NVIDIA | constant, *"Poisson, and gamma arrival patterns with tunable burstiness"*, ramping | percentiles p25–p99 + GPU telemetry | NVIDIA stacks, Dynamo priority testing | [blog 2026-09-18](https://developer.nvidia.com/blog/benchmarking-llm-inference-at-scale-with-aiperf/) |
| **k6** | Grafana | open (`constant-arrival-rate`, `ramping-arrival-rate`) or closed (`constant-vus`, `ramping-vus`) | via thresholds | end-to-end through auth, gateway, quota — **the only one that tests your 429/503 path** | [docs](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/) |

**Use at least two**: one engine-level (`vllm bench serve` or GuideLLM) to find
the operating point, and one fleet-level (inference-perf or k6) to validate that
the gateway, router and admission ladder behave. A test that bypasses the
gateway never exercises §3.

### 7.2 Traffic models — and the one mistake everyone makes

**Use an open model.** k6's own explanation, verbatim
[src](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/):

> *"In the closed model, VU iterations start only when the last iteration
> finishes. In the open model, on the other hand, VUs arrive independently of
> iteration completion."*
> *"When the target system is stressed and starts to respond more slowly, a
> closed model load test will wait, resulting in increased iteration durations
> and a tapering off of the arrival rate of new VU iterations."*

That tapering is **coordinated omission**: a closed-model test of an overloaded
LLM server silently reduces its own offered load, so you never see the failure
you are trying to test. Every "we couldn't reproduce the incident in staging"
story about queue-depth blowups is this bug.

`vllm bench serve` parameterises burstiness by a Gamma shape, verbatim
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/benchmarking/cli.md):

- Shape parameter: `burstiness`; *"Coefficient of Variation (CV): 1/√burstiness"*
- *"`burstiness = 0.1`: Highly bursty traffic (CV ≈ 3.16) - stress testing"*
- *"`burstiness = 1.0`: Natural Poisson traffic (CV = 1.0) - realistic simulation"*
- *"`burstiness = 5.0`: Uniform traffic (CV ≈ 0.45) - controlled load testing"*

and its own recommendation table, verbatim:

| Use Case | Burstiness | Request Rate | Max Concurrency |
|---|---|---|---|
| Maximum Throughput | N/A | Infinite | Limited |
| Realistic Testing | 1.0 | Moderate (5-20) | Infinite |
| Stress Testing | 0.1-0.5 | High (20-100) | Infinite |
| Latency Profiling | 2.0-5.0 | Low (1-10) | Infinite |
| Capacity Planning | 1.0 | Variable | Limited |
| SLA Validation | 1.0 | Target rate | SLA limit |

Note the documented caveat on the most-used mode: with `--request-rate inf`,
*"`--burstiness` has no effect since request timing is not controlled when rate
is infinite"*.

**Session-based traffic** is the one this repo's workload actually is. A
multi-turn agentic session has a growing shared prefix, which is why
`server_gpu_cache_hit_rate` runs **0.902–0.970** in the measured B300 rows
([`b300.md` §3.2](../models/deepseek41f/b300.md)). A benchmark of independent
random prompts measures a ~0 %-hit world and will **understate** your capacity
by a large factor. inference-perf supports *"Shared prefix and multi-turn chat
conversations"*, *"Trace Replay"* and *"Conversation Replay"* for exactly this.

And the inverse trap, from vLLM's own docs: *"Repeating `vllm bench serve`
against the same server can reuse prompts left in the prefix cache and inflate
throughput… If cache reuse is not intended, vary `--seed`, reset or restart the
server, or use `vllm bench sweep serve`, which resets server caches between
runs."* **Always state the cache-hit rate alongside any throughput number**, as
this tree's pair docs do.

### 7.3 What to measure

| Metric | Percentiles | Why |
|---|---|---|
| TTFT | p50, p95, p99 | the user-visible responsiveness; the first thing queueing destroys |
| TPOT | p50, p95, p99 | the streaming rate; degrades with batch |
| ITL | p50, p99 | differs from TPOT under speculation (§1.6); the real "smoothness" metric |
| **Queue time** | p50, p99 | `vllm:request_queue_time_seconds` — *the earliest true saturation signal*, and it separates "server slow" from "server busy" |
| **Goodput** (req/s and tok/s) | — | the only number that means anything under overload (§4.1) |
| **Rejection rate**, by code | — | 429 vs 503 vs 5xx; 503 is *success* for admission control |
| Abort / `finish_reason` mix | — | rising `abort` = users giving up (§5.3) |
| E2E latency | p50, p99 | contractual |
| Preemption rate | — | `vllm:num_preemptions_total` |
| Prefix-cache hit rate | — | `rate(vllm:prefix_cache_hits_total) / rate(vllm:prefix_cache_queries_total)` — the counters register as `vllm:prefix_cache_hits` / `_queries` ([`vllm/v1/metrics/loggers.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/metrics/loggers.py)) and `prometheus_client` appends `_total` in the exposition, exactly as for `vllm:request_success_total` and `vllm:num_preemptions_total` |
| Per-replica skew | max/median | routing health |

Per-request attribution is available without a benchmark harness:
`--enable-per-request-metrics` returns `time_to_first_token_ms`,
`generation_time_ms`, `queue_time_ms`, `mean_itl_ms`, `tokens_per_second` in the
response body, *"useful for billing, SLA monitoring, and latency analysis at the
individual request level"* — with the documented warning: *"At high concurrency,
enabling per-request metrics computation may introduce non-negligible CPU
overhead"*
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/per_request_metrics.md).

### 7.4 Metric definitions differ between tools — reconcile before comparing

This is the most common source of bogus comparisons.

| Tool | ITL | TPOT |
|---|---|---|
| vLLM bench | *"the time between consecutive streamed outputs"*, aggregated across requests | `(e2e − TTFT) / (output_tokens − 1)`, per request |
| vLLM Prometheus | `vllm:inter_token_latency_seconds` — one sample per streamed **output event** | `vllm:request_time_per_output_token_seconds` — once per finished request |
| GenAI-Perf / AIPerf | *"ITL… is also known as time per output token (TPOT)"*, defined as `(e2e − TTFT)/(output_tokens − 1)` | same thing |
| inference-perf | `itl` and `tpot` are **separate** goodput constraints, plus `ntpot` | separate |

So **GenAI-Perf's "ITL" is vLLM's "TPOT"**. vLLM's own docs state the rule:
*"Metric terminology is not standardized across benchmarking tools. When
comparing results, use the measurement points and formulas rather than the
metric names alone."* Two further gotchas from the same page: requests generating
≤ 1 token are *"recorded with a TPOT of zero"* in Prometheus but excluded from
`vllm bench serve`'s TPOT statistics; and `stream_interval > 1` or speculation
bundles tokens per event, so ITL rises while TPOT does not.

**This tree uses TPOT** ([`../METHODOLOGY.md` §4](../METHODOLOGY.md):
`TPOT = decode_step_time`), so compare against `vllm:request_time_per_output_token_seconds`
and `vllm bench serve`'s TPOT, never against GenAI-Perf's ITL label without
restating the formula.

### 7.5 Test plan template

```
# Concurrency & admission test plan — <model> on <shape>
# Fill every field. An unfilled field is a result you cannot interpret.

## 0. Fixed inputs
engine + exact version/image :
model + revision             :
parallelism (TP/EP/DP/DCP)   :
KV dtype                     :
speculative config (method, γ, adaptive?) :
max_num_seqs / max_num_active_seqs :
max_num_batched_tokens       :
max_num_queued_reqs / _tokens:
max_model_len                :
cudagraph capture ladder     :
prefix cache                 : on/off   expected hit rate:
GPU-hour price row cited     : cross-cutting/cloud-pricing.md §5.14 <row>

## 1. Workload
prompt len distribution      : p50/p90/p99  (from production, not synthetic)
output len distribution      : p50/p90/p99
session structure            : single-turn / multi-turn (turns, shared-prefix %)
modality                     : text / image / video (frames, fps)
arrival model                : open, Poisson (burstiness=1.0)  <-- MUST be open

## 2. SLO
TTFT p99 <=                  ms
TPOT p99 <=                  ms   (state TPOT, not ITL - see §7.4)
goodput target               req/s
acceptable rejection rate    %

## 3. Runs
R1 latency floor   : concurrency 1, 100 requests           -> TTFT/TPOT floor
R2 capacity sweep  : GuideLLM `--profile kind=sweep,sweep_size=10`
                     or inference-perf `load.sweep.type: linear`
                     -> knee: max req/s at goodput >= 95 %
R3 SLO validation  : constant rate at 0.8 x knee, 30 min   -> p99s, preemption=0
R4 burst           : 5 x knee for 60 s, then back to 0.8 x -> §8
R5 shed            : R4 with the sheddable tier at 80 % of traffic
R6 drain           : R3 + rolling restart of 1 replica     -> 0 client errors?
R7 kill            : R3 + SIGKILL one engine pod           -> migration counters
R8 interference    : R3 + `--probe-request-rate 20`        -> probe p99 vs baseline
R9 cancellation    : R3 + disconnect 20 % of clients at t=2 s
                     -> num_requests_running must drop within one step

## 4. Record per run
p50/p95/p99 TTFT, TPOT, ITL, queue time, e2e
goodput (req/s, tok/s), rejection rate by status code
kv_cache_usage_perc p99, num_preemptions_total delta
prefix_cache hit rate, per-replica skew (max/median TTFT)
$/1M output at the measured rate (METHODOLOGY §6)

## 5. Pass criteria
R3: p99 within SLO, preemptions == 0, rejections == 0
R4: goodput >= knee throughout; rejections are 503 with Retry-After; NO 5xx;
    recovery to R3 p99 within 60 s of burst end
R6: zero client-visible errors
R7: migrations succeed, or documented as unsupported (n>1 / guided decoding)
```

### 7.6 Alerts

Metric names verbatim from
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/metrics.md).

| Alert | Expression (sketch) | Meaning |
|---|---|---|
| Queue saturation | `histogram_quantile(0.99, rate(vllm:request_queue_time_seconds_bucket[5m])) > SLO_ttft * 0.5` | **page** — the earliest true saturation signal |
| Waiting backlog | `vllm:num_requests_waiting > 0` for 5 m | capacity exhausted |
| KV pressure | `vllm:kv_cache_usage_perc > 0.95` for 5 m | preemption imminent |
| Preemption storm | `rate(vllm:num_preemptions_total[5m]) > 0` | over-admission |
| TTFT SLO | `histogram_quantile(0.99, rate(vllm:time_to_first_token_seconds_bucket[5m]))` | contractual |
| TPOT SLO | `histogram_quantile(0.99, rate(vllm:request_time_per_output_token_seconds_bucket[5m]))` | contractual |
| Cache collapse | `rate(vllm:prefix_cache_hits_total[5m]) / rate(vllm:prefix_cache_queries_total[5m]) < 0.5` | routing broke — note the `_total` suffix `prometheus_client` adds to these counters (§7.3) |
| Abort surge | `rate(vllm:request_success_total{finished_reason="abort"}[5m])` rising | users giving up |
| Migration failure (Dynamo) | `rate(dynamo_frontend_model_migration_duration_seconds_count{outcome!="success"}[5m])` | fault tolerance not working |

---

## 8. Worked example: DeepSeek-V4.1-Flash on 2× 8×B300 under a 5× burst

### 8.1 The deployment

| Item | Value | Source |
|---|---|---|
| Hardware | 2 × HGX B300, 8 GPUs each = **16 GPUs**, 268 GB/GPU, NVLink in-node, IB/RoCE between | [`../METHODOLOGY.md` §8](../METHODOLOGY.md) |
| Model | `deepseek-ai/DeepSeek-V4.1-Flash`, 510.29 GB checkpoint | ibid. |
| Shape | **TP4, 4 replicas** (2 per node), **Engram tables host-offloaded** (`--engram-config '{"cpu_offload":true}'`) → 307.5 GB resident, 76.9 GB/GPU | [`b300.md` §5.1–5.3](../models/deepseek41f/b300.md). *Corrected 2026-09-19: this row previously read "resident and sharded", contradicting §1, §5.2 and §8.4(d), which all cost the replica at the host-offloaded 307.5 GB. TP4 **can** hold the tables resident (127.8 GB of 241.2 GB/GPU), but that is not the shape the rest of §8 prices.* |
| Spec decoding | DSpark γ=5, adaptive verification on | [`b300.md` §5.2](../models/deepseek41f/b300.md), [`recommendations.md`](../matrix/recommendations.md) |
| Workload | S1: 4K in / 512 out, interactive | [`../METHODOLOGY.md` §6](../METHODOLOGY.md) |
| SLO | TPOT p99 ≤ 50 ms, TTFT p99 ≤ 2 s | S1 + a chosen TTFT budget |

Nothing crosses the inter-node fabric: TP4 fits in one NVLink domain and the
replicas are independent, so IB is used only for control plane and (optionally)
cross-node KV cache sharing.

### 8.2 Steady state, per replica (4 GPUs)

From [`b300.md` §3.4](../models/deepseek41f/b300.md), the **measured-capped "A"**
column at S1 (the column the pair doc says to plan with):

| batch | A TPOT | A agg (4 GPUs) | TTFT @0 % hit |
|--:|--:|--:|--:|
| 128 | 17.80 ms | 7,191 tok/s | 129 ms |
| **256** | **35.60 ms** | **7,191 tok/s** | **129 ms** |

Pick **batch 256** (largest batch inside the 50 ms SLO; aggregate throughput is
flat so the larger batch is free seats). This matches the vLLM recipe's shipped
`--max-num-seqs 256`.

Derived, `est.`, using METHODOLOGY §4 definitions:

```
residency(request) = TTFT + (out_len − 1) × TPOT
                   = 0.129 s + 511 × 0.0356 s = 18.32 s
rate_per_replica   = max_num_seqs / residency = 256 / 18.32 = 13.97 req/s
rate_fleet         = 4 × 13.97                                = 55.9 req/s
out_tok_fleet      = 55.9 × 512                               = 28,621 tok/s
cross-check        = 4 replicas × 7,191 tok/s                 = 28,764 tok/s   ✓ (0.5 %)
```

**Baseline: ~56 req/s at 256 concurrent-per-replica × 4 replicas = 1,024 fleet
seats.** *(Corrected 2026-09-19: previously "512 concurrent-per-replica × 4 =
1,024" — inconsistent with the `max_num_seqs = 256` chosen just above, and
arithmetically wrong: 512 × 4 = 2,048.)*

Cost check against [`pairs.json`](../matrix/pairs.json): the interactive row for
this pair is $1.4973–$3.0352 per 1M output tokens at 4 GPUs — unchanged by
anything in this document, which changes *how many* of those tokens are useful,
not their unit cost.

### 8.3 Deriving the admission settings

```
max_num_seqs            = 256                     # §2.3, SLO batch, = recipe default
max_num_active_seqs     = 256                     # live lever, can be lowered to 128 under stress
cudagraph capture       ≥ 256 × (1 + 5) = 1536    # §2.3; recipe uses 8190 for a wider ladder

# queue depth: keep the wait inside the TTFT budget (§3.2)
queue_depth ≤ rate_per_replica × (TTFT_budget − TTFT_service)
            = 13.97 × (2.0 − 0.129) = 26.1  →  26

max_num_queued_reqs     = data_parallel_size × max_num_seqs + queue_depth
                        = 1 × 256 + 26 = 282     → 280
# NB: rounding 282 down to 280 spends the queue, not the running batch:
# the real per-replica queue is 280 − 256 = 24 (fleet 96), so the TTFT-by-
# construction below is 0.129 + 24/13.97 = 1.85 s, not 2.0 s. Round *up* to
# 282 (or 288) if you want the full 26-deep queue the budget allows.

# prefill backlog (§3.2): target_TTFT × prefill_throughput
prefill_throughput ≈ 4096 tok / 0.129 s = 31,752 prompt tok/s per replica   (est.)
max_num_queued_tokens   = 2.0 s × 31,752 = 63,504 → 64000
# docstring caveat: the count is conservative (a partially prefilled request still
# contributes its full prompt_len), so real rejection happens slightly earlier.

max_model_len           = 131072                  # p99.9 of observed prompts, not the model max
watermark               = 0.02                    # §5.1; ~1 request's KV of headroom
```

Sanity check against capacity: `max_concurrency(4K)` at TP4 FP4 KV is **17,408**
([`b300.md` §3.4](../models/deepseek41f/b300.md)). We are admitting **282**.
**The KV pool is 62× larger than the admission limit** — and that is correct,
not wasteful, because TPOT binds first.

### 8.4 The 5× burst

Offered load jumps to **5 × 55.9 = 279.5 req/s** for 60 seconds, then returns to
baseline.

**(a) No admission control** (`max_num_queued_reqs` unset — vLLM's default is
`None`, *"or None for no limit"*):

```
excess           = 279.5 − 55.9 = 223.6 req/s
queued after 60s = 223.6 × 60 = 13,416 requests
drain time       = 13,416 / 55.9 = 240 s
TTFT of the last queued request ≈ 240 s
goodput during and after the burst ≈ 0   (every request misses TTFT p99 ≤ 2 s)
```

The 60-second burst produces a **4-minute** outage, and the queue is invisible to
every client — they see silence, not an error. If a client library has a 60 s
timeout and retries, offered load rises further and the drain never completes.
This is the failure mode.

**(b) With the §8.3 settings:**

```
fleet admission capacity = 4 × 280 = 1,120 in-flight (1,024 running + 96 queued)
served                   = 55.9 req/s   (unchanged — the GPUs did not get faster)
rejected                 = 223.6 req/s  → HTTP 503 + Retry-After
rejection rate           = 223.6 / 279.5 = 80 %
goodput                  = 55.9 req/s, 100 % inside SLO
TTFT p99 during burst    ≈ 0.129 + (24 / 13.97) = 1.85 s  (by construction)
recovery                 = immediate; the queue never exceeded 24 per replica
```

**(c) With priority shedding** (§3.6), baseline mix 20 % Critical / 80 %
Standard+Sheddable:

```
Critical baseline  = 0.20 × 55.9 = 11.2 req/s
Critical at 5x     = 55.9 req/s  ← exactly fleet capacity
Standard/Sheddable = shed entirely at the router (GIE: sheddable only to replicas
                     with <80 % KV usage and <5 queued — none qualify)
result             : Critical tier 100 % served inside SLO; 80 % of offered load
                     rejected, all of it from the lower tiers.
```

**(d) Why you cannot scale out of it.** A fifth replica needs the weights
resident: **307.5 GB at 1.0 min / 5 GB/s** (Engram host-offloaded) or 76.9 GB per
GPU at TP4, plus CUDA-graph capture across 26 sizes
([`b300.md` §5.2–5.3](../models/deepseek41f/b300.md)). Even with the checkpoint
already on local NVMe, **the replica is not serving until after the burst has
ended.** Autoscaling is the answer to a *shifted mean*, not to a burst;
admission control is the answer to a burst. (Scale-to-zero and pre-warming
strategies belong to the autoscaling document in this folder.)

### 8.5 Config

vLLM, per replica — built on the recipe's TP4 variant
([`b300.md` §5.2](../models/deepseek41f/b300.md)) with the §8.3 admission
settings added. ⚠️ Confirm each admission flag exists in your image before
deploying (see the version table at the top).

```bash
# image: vllm/vllm-openai:nightly   (recipe baseline; pin a digest in production)
VLLM_ENGINE_READY_TIMEOUT_S=3600 VLLM_USE_RUST_FRONTEND=1 \
vllm serve deepseek-ai/DeepSeek-V4.1-Flash \
  --tokenizer-mode deepseek_v41 \
  --tensor-parallel-size 4 \
  --max-cudagraph-capture-size 8190 \
  --max-num-batched-tokens 8192 \
  --max-num-seqs 256 \
  --max-num-active-seqs 256 \
  --max-num-queued-reqs 280 \
  --max-num-queued-tokens 64000 \
  --max-model-len 131072 \
  --scheduling-policy priority \
  --speculative-config '{"method":"dspark","num_speculative_tokens":5,
    "draft_sample_method":"probabilistic","rejection_sample_method":"block",
    "enable_adaptive_verification":true}' \
  --tool-call-parser deepseek_v41 --enable-auto-tool-choice \
  --reasoning-parser deepseek_v41 \
  --mm-encoder-tp-mode data \
  --enable-per-request-metrics
```

Pod spec (drain, §5.5):

```yaml
spec:
  terminationGracePeriodSeconds: 180     # > one full 512-token generation at 35.6 ms TPOT
  containers:
    - name: vllm
      lifecycle:
        preStop:
          exec:
            command: ["/bin/sh", "-c", "sleep 10"]   # let EndpointSlice propagate
      readinessProbe:                                 # sheds traffic
        httpGet: { path: /health, port: 8000 }
      livenessProbe:                                  # kills the pod - never wire overload here
        httpGet: { path: /health, port: 8000 }
        initialDelaySeconds: 600                      # > weight load + graph capture
        failureThreshold: 3
```

Dynamo frontend (migration + graceful shutdown, §5.4–5.5) — verbatim shape from
the Dynamo docs:

```yaml
  - name: Frontend
    type: frontend
    replicas: 2
    podTemplate:
      spec:
        containers:
        - name: main
          image: ${RUNTIME_IMAGE}
          env:
          - name: DYN_MIGRATION_LIMIT
            value: "3"
          - name: DYN_MIGRATION_MAX_SEQ_LEN
            value: "32000"
          - name: DYN_HTTP_GRACEFUL_SHUTDOWN_TIMEOUT_SECS
            value: "120"     # default 5 is shorter than one generation
```

⚠️ With structured outputs (tool calling), migration is skipped — *"the error is
propagated to the client instead of migrating"* — so the 180 s grace period, not
migration, is what protects an agentic fleet during a rollout (§5.4).

Router backpressure (§3.3):

```bash
python -m dynamo.frontend \
    --router-mode kv \
    --router-queue-threshold 0.9 \        # queue when eligible workers are >90 % of max_num_batched_tokens
    --router-queue-policy fcfs \
    --active-prefill-tokens-threshold-frac 0.95 \
    --http-port 8000
```

### 8.6 Expected timeline

| t | Offered | `num_requests_running` (fleet) | `num_requests_waiting` | p99 TTFT | 503/s | Goodput |
|---|--:|--:|--:|--:|--:|--:|
| −60 s | 55.9 | ~1,024 | ~0 | 0.15 s | 0 | 55.9 |
| 0 s | 279.5 | 1,024 | ramping | 0.3 s | 0 | 55.9 |
| +2 s | 279.5 | 1,024 | **96 (cap)** | **~1.85 s** | ~224 | 55.9 |
| +60 s | 279.5 | 1,024 | 96 | ~1.85 s | ~224 | 55.9 |
| +61 s | 55.9 | 1,024 | draining | ~1.5 s | ~0 | 55.9 |
| +70 s | 55.9 | ~1,024 | ~0 | 0.15 s | 0 | 55.9 |

Contrast with case (a): `num_requests_waiting` climbs past 13,000, p99 TTFT
passes 240 s, 503/s stays 0 the whole time — **zero errors and zero goodput**,
the worst possible observability posture.

### 8.7 What to verify before trusting any of this

1. **The admission flags exist in your image** — `vllm serve --help | grep
   max-num-queued`. ⚠️ They are read from `main`, not from the pinned 0.29.0
   release.
2. **`Maximum concurrency for N tokens per request` at startup** matches §2.2's
   `max_concurrency` within ~10 %; if not, the KV-sharding assumption is wrong
   for your build (§2.5).
3. **Run R4 of the §7.5 plan** and confirm: 503s appear, no 5xx, p99 TTFT ≤ 2 s
   throughout, and recovery within 60 s.
4. **Measured TTFT for a 4K prompt** — the 129 ms figure is `est.` from the
   measured prefill MFU of 0.0307, and the whole `max_num_queued_tokens`
   derivation rests on it. Re-derive `prefill_throughput` from production
   `vllm:request_prefill_time_seconds`.
5. **The 5× multiplier itself.** ⚠️ It is assumed here. Take it from your own
   traffic history (peak-to-mean ratio at 1-minute granularity); a fleet with a
   20× diurnal ratio needs either 20× the shed capacity or a different tier mix.

---

## Open questions

All ⚠️ items in this document, consolidated. Each states the estimation method
or the experiment that would close it.

1. **Which admission flags are in the pinned releases.** `max_num_queued_reqs`,
   `max_num_queued_tokens`, `max_num_active_seqs`, `watermark`,
   `scheduler_reserve_full_isl` were read from vLLM `main` on 2026-09-19; this
   tree pins vLLM **0.29.0**. Same for SGLang `--max-queued-requests`,
   `--retraction-policy`, `--min-free-slots-delay`, `hrrn` /
   `shortest-prefill-first` against the pinned **0.5.20**. *Method:*
   `--help | grep` on the deployed image. (§1.2, §1.3, §8.7)
2. **Dynamo version contradiction — RESOLVED 2026-09-19, no longer open.**
   The two numbers are different artefacts: **v1.4.2** is the last numbered
   stable release / container tag (GitHub releases, 2026-08-29) and **1.5.0**
   is the current PyPI `ai-dynamo` stable (uploaded 2026-09-19 04:21 UTC); the
   docs site's `latest` index tracks the container release. Primary sources and
   pins in [`02` §2.1 line 150](02-serving-stack-and-routing.md#21-version-pin)
   and its [verification log entry 5](02-serving-stack-and-routing.md#verification-log-2026-09-19).
   Deploy the tag [`09` §2.1](09-reference-architectures.md#21-nvidia-dynamo) pins (container
   1.4.2 + model dev tags), not the PyPI number.
3. **A good `watermark` value.** vLLM's default is 0.0 (disabled) and no
   published guidance was found. *Method:* set it to
   `(largest expected single-request KV) / (total KV blocks)` and measure the
   preemption rate against 0.0. (§5.1)
4. **vLLM's `S` (state slots per request) for hybrid models.** SGLang's is 5 by
   default; vLLM's is unpublished ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)).
   It directly sets Qwen3.8-27B and Kimi-K3 seat counts. *Method:* start the
   server, read the reported state-pool size, divide by the per-slot bytes from
   the architecture doc. (§2.6)
5. **`InferencePool` `failureMode: FailClose`.** Only `FailOpen` appears in the
   fetched doc; whether `FailClose` is supported in the pinned GIE version is
   unverified, and it determines whether an EPP outage disables shedding or
   disables serving. *Method:* `kubectl explain
   inferencepool.spec.endpointPickerRef.failureMode`. (§3.3)
6. **AIBrix rate-limiter window semantics.** The package is documented as a
   *"fixed-window rate limiter"*, which admits up to 2× the limit across a
   boundary. *Method:* read the `pkg/plugins/gateway/ratelimiter` implementation
   for the deployed tag; test with a boundary-straddling burst. (§3.4)
7. **A retry-budget percentage for LLM inference.** 10–20 % is the general
   service-mesh convention; no LLM-specific source found. *Method:* drive a
   controlled burst and raise the budget until retry amplification is visible in
   the offered-rate metric. (§3.8)
8. **AWS Builders' Library "Timeouts, retries, and backoff with jitter"** — the
   canonical primary source for §3.8 — redirected to `builder.aws.com` and
   returned no article body on 2026-09-19. Those four points are therefore
   stated from general practice plus the Envoy citation, not quoted. *Method:*
   re-fetch; if still unavailable, cite the Envoy retry-budget docs and the
   original "Exponential Backoff and Jitter" post instead. (§3.8)
9. **Idempotency-key support in the serving stack.** No reference found in vLLM,
   SGLang, Dynamo, AIBrix or Agent Router docs as of 2026-09-19. *Method:* grep
   the deployed gateway's config reference; if absent, implement in the API
   layer. (§3.9)
10. **PD disaggregation below 1,000 GPUs for DeepSeek-V4.1-Flash specifically.**
    The general result is that it moves the frontier inward below ~1,000 GPUs,
    but this model's 2.04× prefill/decode active-parameter asymmetry (7.89 B vs
    16.11 B) is unusually large. *Method:* `vllm bench serve --goodput` on an
    aggregated TP4 replica vs. a 1P1D Dynamo shape at matched GPU count. (§4.3)
11. **A per-request output-rate cap.** Neither vLLM nor SGLang was found to
    expose one; gateway-side SSE pacing smooths UX but frees no GPU time, so the
    Andes-style goodput win is not available today. *Method:* check for an
    engine-side token-pacing or QoE-scheduler feature in newer releases; failing
    that, prototype via a custom `scheduler_cls`. (§4.5)
12. **vLLM's behaviour on engine-core death with in-flight requests** — error
    response vs hang-until-client-timeout. *Method:* kill the engine-core process
    under load and observe the client-visible status. (§5.2)
13. **`--enable-dynamic-chunking` outside pipeline parallelism.** SGLang
    documents it as *"for pipeline parallelism"*; the 15.2 s → 8.6 s 128K TTFT
    result was measured on a different stack. *Method:* enable on a TP4 DeepSeek
    replica and compare 128K TTFT. (§6.2)
14. **NVMe-tier KV resume latency** for these models via
    `TieringOffloadingSpec`. *Method:* an `fs` secondary tier on local NVMe,
    compare TTFT at 128K for cold / CPU-resident / NVMe-resident prefixes. (§6.3)
15. **Peak ViT activation footprint at 240 frames** under vLLM's chunked-prefill
    scheduler — already open in
    [`../models/marlin2b/b300.md` §1.4](../models/marlin2b/b300.md); it sets the
    real video-prefill concurrency limit. *Method:*
    `--max-num-batched-tokens` sweep with `nvidia-smi` peak-reserved watching.
    (§2.7, §6.4)
16. **Measured TTFT distribution for Marlin-2B at the video operating point** —
    none exists at any concurrency
    ([`../models/marlin2b/b300.md` §3.3](../models/marlin2b/b300.md)), so the
    ~10-in-flight admission figure in §6.4 is derived from a single-request
    186 ms estimate. *Method:* R2 of the §7.5 plan with real clips. (§6.4)
17. **The 5× burst multiplier in §8.** Assumed, not measured. *Method:* compute
    peak-to-mean at 1-minute granularity from production traffic. (§8.7)
18. **The `est.` prefill throughput (31,752 prompt tok/s/replica)** underpinning
    `max_num_queued_tokens = 64000`. Derived from the pair doc's estimated
    129 ms TTFT at 4K, itself from a measured prefill MFU of 0.0307. *Method:*
    re-derive from production `vllm:request_prefill_time_seconds` and
    `vllm:request_prompt_tokens`. (§8.3, §8.7)

Inherited, not re-opened here (see the linked docs): the replicated-vs-sharded
KV reading for DeepSeek-V4.1-Flash and Kimi-K3
([`../matrix/fit-matrix.md` §6.3](../matrix/fit-matrix.md)); the BF16-container
SWA ring size ([`../METHODOLOGY.md` §8](../METHODOLOGY.md) pin log); and
per-GPU-generation MBU/MFU tables
([`serving-optimizations.md` §4.4](../cross-cutting/serving-optimizations.md)).

---

## Sources

Fetched 2026-09-19 unless stated. "raw" = `curl` of the raw file (WebFetch
summaries were lossy for source files).

### Engines — primary source files and docs

1. vLLM `SchedulerConfig` — https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py (raw, `main`)
2. vLLM engine args / CLI names — https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/engine/arg_utils.py (raw, `main`)
3. vLLM Optimization and Tuning (preemption, chunked prefill) — https://docs.vllm.ai/en/stable/configuration/optimization/
4. vLLM scheduler config API reference — https://docs.vllm.ai/en/latest/api/vllm/config/scheduler/
5. vLLM Metrics design — https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/metrics.md (raw) and https://docs.vllm.ai/en/latest/design/metrics/
6. vLLM per-request metrics — https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/per_request_metrics.md (raw)
7. vLLM benchmarking CLI (latency metric definitions, load patterns, KV log line) — https://raw.githubusercontent.com/vllm-project/vllm/main/docs/benchmarking/cli.md (raw)
8. vLLM `vllm/benchmarks/serve.py` (`--goodput` help text) — https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/benchmarks/serve.py (raw)
9. vLLM KV offloading usage — https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/kv_offloading_usage.md (raw)
10. vLLM disaggregated encoder — https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/disagg_encoder.md (raw)
11. vLLM server args / config file — https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/serve_args.md (raw)
12. SGLang `schedule` arg fields — https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/arg_groups/fields/schedule.py (raw, `main`)
13. SGLang scheduler (`_abort_on_queued_limit`, waiting timeout) — https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/scheduler.py (raw)
14. SGLang `schedule_batch.py` (`retract_decode`, `_get_decode_retraction_order`) — https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/schedule_batch.py (raw)
15. SGLang hyperparameter tuning — https://docs.sglang.io/advanced_features/hyperparameter_tuning.html
16. TensorRT-LLM useful runtime flags (capacity scheduler, context chunking, KV fraction) — https://nvidia.github.io/TensorRT-LLM/performance/performance-tuning-guide/useful-runtime-flags.html

### Routers, gateways, orchestration

17. Dynamo routing concepts (cost model) — https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/routing-concepts.md (raw)
18. Dynamo router filtering / queue backpressure / busy thresholds — https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/worker-filtering.md (raw)
19. Dynamo Deficit Round Robin queue scheduling — https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/deficit-round-robin.md (raw)
20. Dynamo priority scheduling — https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/use-cases/agents/priority-scheduling.md (raw)
21. Dynamo request migration — https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/kubernetes/fault-tolerance/request-migration.md (raw)
22. Dynamo graceful shutdown — https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/kubernetes/fault-tolerance/graceful-shutdown.md (raw)
23. Dynamo standalone router README — https://raw.githubusercontent.com/ai-dynamo/dynamo/main/components/src/dynamo/router/README.md (raw)
24. Dynamo SGLang agentic guide (priority scheduling, radix eviction policy) — https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/backends/sglang/agents-on-sglang.md (raw)
25. Dynamo docs version index — https://docs.nvidia.com/dynamo/llms.txt
26. Gateway API Inference Extension — InferencePool — https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/
27. CNCF, "Deep Dive into the Gateway API Inference Extension", 2025-04-21 — https://www.cncf.io/blog/2025/04/21/deep-dive-into-the-gateway-api-inference-extension/
28. Agent Router (formerly Envoy AI Gateway) usage-based rate limiting — https://theagentrouter.ai/docs/next/capabilities/traffic/usage-based-ratelimiting/
29. AIBrix gateway routing strategies and RPM/TPM — https://aibrix.readthedocs.io/latest/features/gateway-plugins.html
30. AIBrix rate-limiter package — https://pkg.go.dev/github.com/vllm-project/aibrix/pkg/plugins/gateway/ratelimiter
31. Envoy circuit breaking — https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking
32. Kubernetes Pod lifecycle / termination — https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/concepts/workloads/pods/pod-lifecycle.md (raw)
33. llm-d intelligent inference scheduling — https://llm-d.ai/docs/guide/Installation/inference-scheduling
34. LMCache documentation — https://docs.lmcache.ai/

### Papers

35. Sarathi-Serve, arXiv 2403.02310 (OSDI '24) — https://arxiv.org/abs/2403.02310
36. DistServe, arXiv 2401.09670 (OSDI '24) — https://arxiv.org/abs/2401.09670
37. Llumnix, arXiv 2406.03243 (OSDI '24) — https://arxiv.org/abs/2406.03243
38. Andes, arXiv 2404.16283 — https://arxiv.org/abs/2404.16283
39. Aladdin, arXiv 2405.06856 — https://arxiv.org/abs/2405.06856
40. VTC / Fairness in Serving LLMs, arXiv 2401.00588 (OSDI '24) — https://arxiv.org/abs/2401.00588
41. Mooncake, arXiv 2407.00079 — https://arxiv.org/abs/2407.00079

### Benchmarking tools

42. inference-perf README — https://raw.githubusercontent.com/kubernetes-sigs/inference-perf/main/README.md (raw)
43. inference-perf goodput — https://raw.githubusercontent.com/kubernetes-sigs/inference-perf/main/docs/goodput.md (raw)
44. inference-perf load generation — https://raw.githubusercontent.com/kubernetes-sigs/inference-perf/main/docs/loadgen.md (raw)
45. GuideLLM benchmark guide — https://raw.githubusercontent.com/vllm-project/guidellm/main/docs/getting-started/benchmark.md (raw)
46. NVIDIA, "Benchmarking LLM Inference at Scale with AIPerf", 2026-09-18 — https://developer.nvidia.com/blog/benchmarking-llm-inference-at-scale-with-aiperf/
47. NVIDIA GenAI-Perf — https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/perf_analyzer/genai-perf/README.html
48. k6 executors — https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/
49. k6 open vs closed models — https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/

### Standards

50. RFC 6585 §4 — 429 Too Many Requests — https://www.rfc-editor.org/rfc/rfc6585.txt
51. RFC 9110 §15.6.4, §10.2.3 — 503 Service Unavailable, Retry-After — https://www.rfc-editor.org/rfc/rfc9110.txt
52. `draft-ietf-httpapi-idempotency-key-header-07` (2025-10-15) — https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header

### Engineering blogs and measurements

53. Red Hat Developer, "5 steps to triage vLLM performance", 2026-03-09 — https://developers.redhat.com/articles/2026/03/09/5-steps-triage-vllm-performance
54. Databricks, "LLM Inference Performance Engineering: Best Practices" — https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices
55. vLLM issue #56975 (chunk budget and CUDA-graph capture vs p99 ITL, measured) — https://github.com/vllm-project/vllm/issues/56975
56. vLLM recipe, DeepSeek-V4.1-Flash — https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
57. LMSYS, GB300 long-context (dynamic chunking, 128K TTFT) — https://www.lmsys.org/blog/2026-02-19-gb300-longctx/
58. vLLM blog, Kimi-K3 performance optimization (adaptive scheduling budget) — https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization
59. vLLM blog, DSpark adaptive verification — https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification
60. vLLM blog, decode context parallelism — https://vllm.ai/blog/2026-08-07-decode-context-parallelism
61. vLLM blog, GLM-5.3 hybrid sparse offloading — https://vllm.ai/blog/2026-09-08-glm53-part1-hybrid-sparse-offloading
62. vLLM blog, PyNvVideoCodec video captioning — https://vllm.ai/blog/2026-09-18-pynvvideocodec
63. SqueezeBits, vLLM vs TensorRT-LLM automatic prefix caching — https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189
64. Towards Data Science, "Disaggregation is a thousand-GPU problem" — https://towardsdatascience.com/disaggregation-is-a-thousand-gpu-problem/

### This repository

65. [`../METHODOLOGY.md`](../METHODOLOGY.md) — §2 KV, §3 fit and `max_concurrency`, §4 roofline and TPOT, §6 cost, §8 pinned inputs
66. [`../README.md`](../README.md) — tree index and confidence labels
67. [`../matrix/pairs.json`](../matrix/pairs.json) — the 40 pair analyses as data
68. [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) — §6.3 replicated-vs-sharded KV
69. [`../matrix/recommendations.md`](../matrix/recommendations.md) — DSpark γ=5 default
70. [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) — §4 batching/SLOs, §5.4–5.5 long context, §6 multimodal
71. [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) — engine versions and per-model support
72. [`../models/deepseek41f/b300.md`](../models/deepseek41f/b300.md) — §1.3 concurrency, §3.2 measured, §3.4 estimated, §5.2 launch, §5.3 warm-up
73. [`../models/deepseek41fnvfp4/b300.md`](../models/deepseek41fnvfp4/b300.md) — §1.3 both KV readings
74. [`../models/kimik3/b300.md`](../models/kimik3/b300.md) — §1.3 KDA state as the concurrency ceiling
75. [`../models/qwen3827b/b300.md`](../models/qwen3827b/b300.md) — single-GPU shape
76. [`../models/marlin2b/b300.md`](../models/marlin2b/b300.md) — §1.4 ViT burst, §3.3 video TTFT and queueing

---

## Verification log (2026-09-19)

Adversarial fact-check. Every claim below was re-checked by opening the primary
source itself (not the doc's rendering of it) and, where a number is derived, by
recomputing it in `python3`. Repo cross-references were checked by opening the
target file and finding the number in it. **26 claims checked: 15 CONFIRMED,
10 CORRECTED, 1 UNVERIFIABLE** (rows 18 and 19 share one root cause — the
`max_num_queued_reqs` rounding — but are two separate wrong numbers in two
separate places).

### CONFIRMED

| # | Claim (§) | Verdict | Source opened |
|---|---|---|---|
| 1 | vLLM `SchedulerConfig` field names, defaults and docstrings — `DEFAULT_MAX_NUM_BATCHED_TOKENS = 2048`, `DEFAULT_MAX_NUM_SEQS = 128`, `max_num_active_seqs=None`, `max_num_queued_reqs=None`, `max_num_queued_tokens=None`, `watermark=0.0`, `scheduler_reserve_full_isl=True`, `enable_chunked_prefill=True`, `policy="fcfs"`, `prefill_schedule_interval=1`, `stream_interval=1`, `long_prefill_token_threshold=0` (§1.2) | **CONFIRMED** — every docstring quoted in §1.2 is verbatim, including the `max_num_queued_tokens` "conservative count / full `prompt_len`" caveat and the `data_parallel_size * max_num_seqs` sizing hint | https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py |
| 2 | vLLM start-up validation: `max_num_batched_tokens >= max_num_seqs`; `>= max_model_len` with chunked prefill off; `max_num_active_seqs <= max_num_seqs`; `long_prefill_token_threshold <= max_model_len`; warning at `> max_num_seqs * max_model_len` (§1.2, §3.7) | **CONFIRMED** — all four in `verify_max_model_len` | same file, `verify_max_model_len` |
| 3 | `max_num_encoder_input_tokens = max_num_batched_tokens` and `encoder_cache_size = max_num_batched_tokens`, both carrying `# TODO (ywang96): Make this configurable.` (§2.7) | **CONFIRMED** — both comments and both assignments present | same file |
| 4 | SGLang `schedule` namespace flags and defaults: `--max-prefill-tokens 16384`, `--schedule-policy fcfs` with the 9 choices incl. `hrrn` / `shortest-prefill-first`, `--retraction-policy length`, `--schedule-conservativeness 1.0`, `--priority-scheduling-preemption-threshold 10`, `--num-continuous-decode-steps 1`, `--mamba-full-memory-ratio 0.9`, `--min-free-slots-delay`, the prefill-delayer help text (§1.3) | **CONFIRMED** — every help string verbatim, every default matches | https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/arg_groups/fields/schedule.py |
| 5 | SGLang `--enable-dynamic-chunking` is scoped to pipeline parallelism (§6.2's ⚠️) | **CONFIRMED** — help text reads *"Enable dynamic chunk size adjustment for pipeline parallelism"*; the ⚠️ is correctly placed | same file |
| 6 | SGLang `_abort_on_queued_limit` returns 503 with *"The request queue is full."*, evicts a lower-priority **waiting** request under `--enable-priority-scheduling`; `SGLANG_REQ_WAITING_TIMEOUT` aborts with 503 *"Request waiting timeout reached."* (§3.2) | **CONFIRMED** — code verbatim incl. the `aboritng` typo | https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/scheduler.py |
| 7 | SGLang `retract_decode` / `_get_decode_retraction_order` / `length_key = (len(output_ids), -len(origin_input_ids))`; beam groups aborted with HTTP 500 instead of retracted (§1.5) | **CONFIRMED** — all three verbatim; the beam abort really is `HTTPStatus.INTERNAL_SERVER_ERROR` | https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/schedule_batch.py |
| 8 | SGLang tuning guidance: healthy `#queue-req` is *"100 - 2000"*; `lpm` = longest prefix match; prefill OOM → `--chunked-prefill-size 4096`/`2048`; decode OOM → lower `--max-running-requests` (§1.3, §3.2) | **CONFIRMED** verbatim | https://docs.sglang.io/advanced_features/hyperparameter_tuning.html |
| 9 | TRT-LLM `capacity_scheduler_policy`: `GUARANTEED_NO_EVICT` default, `MAX_UTILIZATION`, `STATIC_BATCH` legacy; `context_chunking_policy` FCFS default vs `EQUAL_PROGRESS`; "always enable context chunking"; `kv_cache_free_gpu_mem_fraction` default **0.90**, testing up to **0.95** recommended (§1.4) | **CONFIRMED** — all policies, defaults and the 0.90/0.95 numbers | https://nvidia.github.io/TensorRT-LLM/performance/performance-tuning-guide/useful-runtime-flags.html |
| 10 | vLLM preemption: log line *"Sequence group 0 is preempted by PreemptionMode.RECOMPUTE mode because there is not enough KV cache space."*, and the four remedies **in the documented order** (`gpu_memory_utilization` → `max_num_seqs`/`max_num_batched_tokens` → `tensor_parallel_size` → `pipeline_parallel_size`); chunked-prefill guidance "lower (2048) improves ITL", "higher boosts TTFT", "> 8192 for smaller models" (§1.5, §2.4) | **CONFIRMED** — order and wording match | https://docs.vllm.ai/en/stable/configuration/optimization/ |
| 11 | vLLM burstiness model: CV = 1/√burstiness, 0.1 → CV ≈ 3.16, 1.0 → CV = 1.0, 5.0 → CV ≈ 0.45; the six-row use-case table; *"`--burstiness` has no effect"* at `--request-rate inf`; the `GPU KV cache size: 15,728,640 tokens / Maximum concurrency for 8,192 tokens per request: 1920` startup lines; the prefix-cache-reuse warning (§2.5, §7.2) | **CONFIRMED** — table row-for-row; and 15,728,640 ÷ 8,192 = 1,920 exactly | https://raw.githubusercontent.com/vllm-project/vllm/main/docs/benchmarking/cli.md |
| 12 | Dynamo router: `--router-queue-threshold` *"is not candidate eligibility. It is admission backpressure."*; the three busy thresholds; runtime `/busy_threshold` endpoint; `AllEligibleWorkersOverloaded`; the five-line cost model with `decode_active_request_weight` defaulting to `0`; DRR `uncached_tokens = raw_isl_tokens − cached_tokens`, `scheduling_cost = max(1, uncached_tokens)`, quantum 4096 = 4× quantum 1024 (§3.3, §3.6) | **CONFIRMED** — every formula line and quote verbatim | dynamo `docs/fern/.../router/{worker-filtering,routing-concepts,deficit-round-robin}.md` (raw, `main`) |
| 13 | Dynamo fault tolerance: migration off by default (`--migration-limit 0`), *"Start with `3`"*, `DYN_MIGRATION_MAX_SEQ_LEN` *"strictly exceeds"*, 5-second `DYN_RUNTIME_INHIBITED_DURATION_SECS`, the `n > 1` and guided-decoding limitations (incl. *"applies equally to all backends (vLLM, SGLang, TRT-LLM)"*), the three migration counters; graceful shutdown env defaults **5 / 5 / 900**, the 60s / 120s+ workload table, the `/health` 503 vs `/live` 200 split, *"Keep every internal timeout below `terminationGracePeriodSeconds`"* (§5.2, §5.4, §5.5) | **CONFIRMED** — all verbatim, all three defaults correct | dynamo `docs/fern/pages/kubernetes/fault-tolerance/{request-migration,graceful-shutdown}.md` (raw, `main`) |
| 14 | Kubernetes termination sequence: `terminationGracePeriodSeconds` default **30 seconds**; *"a small, one-off grace period extension of 2 seconds"*; TERM to PID 1; terminating endpoints kept in EndpointSlices with `ready: false` (§5.5) | **CONFIRMED** verbatim (pod-lifecycle.md lines 916/919/926/947–950) | https://raw.githubusercontent.com/kubernetes/website/main/content/en/docs/concepts/workloads/pods/pod-lifecycle.md |
| 15 | Papers, measured numbers: Sarathi-Serve 2.6× / up to 3.7× / up to 5.6×; DistServe 7.4× requests or 12.6× tighter SLO at > 90 %; Llumnix order-of-magnitude tail, up to 1.5×, up to 36 % cost; Aladdin up to 71 %; VTC *"2x tight upper bound"*; Mooncake 525 % simulated / 75 % more requests + prediction-based early rejection (§4.2–§4.7, §6.3) | **CONFIRMED** — six of seven abstracts match verbatim (Andes corrected below) | arXiv 2403.02310, 2401.09670, 2406.03243, 2405.06856, 2401.00588, 2407.00079 |
| — | *Also confirmed, not counted separately:* RFC 9110 §15.6.4 503 text and the *"does not imply that a server has to use it"* note; RFC 9110 §10.2.3 `Retry-After = HTTP-date / delay-seconds`; `draft-ietf-httpapi-idempotency-key-header-07`, 2025-10-15, expires 2026-04-18, Item Structured Header, UUID recommendation, 409 on in-flight duplicate; k6 open-vs-closed quotes; GIE `InferencePool` YAML and `endpointPickerRef` optional since v1.5.0; the CNCF *Sheddable* rule (< 80 % KV, < 5 queued); AIBrix routing strategies and the `user`-header RPM/TPM limiter; inference-perf goodput definitions and the `ttft: 0.2 / tpot: 0.02` YAML; vLLM metric names `vllm:request_queue_time_seconds`, `vllm:kv_cache_usage_perc`, `vllm:num_requests_waiting/running`, `vllm:request_success_total{finished_reason="abort"}`, and the ITL-vs-TPOT divergence note | **CONFIRMED** | rfc-editor.org/rfc/rfc9110.txt · datatracker.ietf.org · grafana.com/docs/k6 · gateway-api-inference-extension.sigs.k8s.io · cncf.io blog 2025-04-21 · aibrix.readthedocs.io · inference-perf `docs/goodput.md` · vllm `docs/design/metrics.md` |
| — | *Repo cross-references, all opened and all found:* 7,191 tok/s flat at batch 32–256 and TPOT 4.45/8.90/17.80/35.60 ms (`deepseek41f/b300.md` §3.4 table); `max_concurrency` 17,408 FP4 @4K and 11,184 @8K / 953 @128K (§1.3 / §3.4); measured MFU 0.0217 decode / 0.0307 prefill (§3.3); 307.5 GB resident Engram-offloaded at 1.0 min / 5 GB/s (§5.3); 26-size CUDA-graph ladder and `--max-cudagraph-capture-size 8190`, `--max-num-seqs 256` (§5.2); 2.3735 PF at 128K ⇒ the "2.37 PFLOP per hedged prompt" of §3.8, and the 37 % / ~10.5× 1M figures (§3.4); 8 × 3.84 TB = 30.72 TB NVMe + 4 TB RAM; TP2-throughput-vs-TP4-interactivity (§3.5); Marlin-2B 753 / 1,417 / 2,532 @23,520, 142 @128K, 1,934 @8K, 94,080 ViT tokens, 186.0 ms; Qwen3.8-27B 325 / 45 and Kimi-K3 101 / 64 in `pairs.json`; Kimi-K3 batch-wave TTFT 10,139 ms; `$1.4973–$3.0352` per 1M output; the 16384/4096/2048 chunk-budget table and the CUDA-graph 196–340 → 112–120 ms row in `serving-optimizations.md` | **CONFIRMED** | `models/deepseek41f/b300.md`, `models/marlin2b/b300.md`, `matrix/pairs.json`, `cross-cutting/serving-optimizations.md` |

### CORRECTED

| # | Claim (§) | Was | Now | Why |
|---|---|---|---|---|
| 16 | Retry amplification (§3.8) | *"turns 1.0× offered load into up to **1.6×** (`1 + 0.2 + 0.2² + 0.2³`)"* | **1.25×** | The series the doc itself prints sums to **1.248**, and the infinite-retry limit `1/(1 − 0.2)` is **1.25**. 1.6 matches no reading of a 20 % rejection rate. Recomputed in `python3`. |
| 17 | §8.2 baseline seats | *"~56 req/s at **512 concurrent-per-replica** × 4 = 1,024 fleet seats"* | *"256 concurrent-per-replica × 4 replicas = 1,024"* | Two errors in one sentence: it contradicts the `max_num_seqs = 256` chosen in the paragraph above, and 512 × 4 = **2,048**, not 1,024. The 1,024 total is right; the per-replica figure was wrong. |
| 18 | §8.6 burst timeline, waiting queue | `num_requests_waiting` = **104 (cap)**, p99 TTFT **~2.0 s** | **96 (cap)**, **~1.85 s** | 104 = 4 × the *unrounded* `queue_depth` 26. §8.3 rounds `max_num_queued_reqs` 282 **down** to 280, which spends the rounding out of the queue, not the running batch: 280 − 256 = 24/replica = **96** fleet, giving `0.129 + 24/13.97 = 1.85 s`. §8.4(b)'s own line said 96 — the two were inconsistent. A note in §8.3 now says round *up* to 282/288 to keep the full 26-deep queue. |
| 19 | §8.4(b) TTFT by construction | `0.129 + (26 / 13.97) = 2.0 s` | `0.129 + (24 / 13.97) = 1.85 s` | Same rounding; recomputed. |
| 20 | §8.1 deployment shape | *"Engram tables **resident and sharded**"* | *"Engram tables **host-offloaded** (`cpu_offload: true`) → 307.5 GB resident, 76.9 GB/GPU"* | Contradicted §1's scope paragraph, §5.2 and §8.4(d), which all price the replica at the host-offloaded **307.5 GB** (`b300.md` §5.3 line 754, §5.2 line 757). TP4 *can* hold the tables resident (127.8 of 241.2 GB/GPU, `b300.md` line 117) but that is not the costed shape. |
| 21 | TL;DR `max_num_seqs` row | *"For DS-V4.1-Flash on 4×B300 at 4K that is **128** … a 136× gap"* | *"**256** (the recipe default; 128 only at a 25 ms SLO) … a **68×** gap (136× at 128)"* | §2.3 concludes *"256 is the right S1 setting"* and §8.3/§8.5 both ship 256; the TL;DR contradicted the body. 17,408 ÷ 256 = **68**. |
| 22 | §7.3 / §7.6 prefix-cache alert | `rate(vllm:prefix_cache_hits[5m]) / rate(vllm:prefix_cache_queries[5m])` | `..._hits_total[5m] / ..._queries_total[5m]` | The counters are registered as `vllm:prefix_cache_hits` / `vllm:prefix_cache_queries` in `vllm/v1/metrics/loggers.py` (lines 600/611) and `prometheus_client` appends `_total` in the exposition — exactly as for `vllm:request_success` → `vllm:request_success_total` (line 730) and `vllm:num_preemptions` → `vllm:num_preemptions_total` (line 676). As written, both expressions match **nothing**. (§7.6's `vllm:num_preemptions_total` and `vllm:request_success_total` were already right.) |
| 23 | §5.7 Envoy circuit breaker | *"The maximum number of **pools** that can be concurrently instantiated."* | *"The maximum number of **connection pools** that can be concurrently instantiated."* | Quoted verbatim but the words "connection" were dropped. |
| 24 | §3.5 RFC 6585 §4 quote | *"…MAY include a Retry-After header indicating how long to wait"* | *"…how long to wait **before making a new request**"* | Quotation truncated mid-sentence. |
| 25 | §4.5 Andes | *"4.7× average QoE improvement"* / *"61% GPU resource savings"* presented as quotes | Full abstract sentence with the **"up to"** qualifiers restored | The abstract reads *"improves the average QoE by **up to** 4.7× … or saves **up to** 61% GPU resources"*. Quoting without "up to" states a typical gain where the paper claims a best case. |

### UNVERIFIABLE

| # | Claim (§) | Verdict | Note |
|---|---|---|---|
| 26 | §3.3, the EPP description *"The EPP receives request metadata, scores candidate model server pods by using configurable plugins (for example, queue depth, KV cache utilization, and prefix-cache affinity), and returns the selected endpoint"*, cited to the CNCF blog of 2025-04-21 | **UNVERIFIABLE at the cited source** — marked ⚠️ **TO BE VERIFIED** in place | Re-fetched https://www.cncf.io/blog/2025/04/21/deep-dive-into-the-gateway-api-inference-extension/ on 2026-09-19: the article describes an *Endpoint Selection Extension (ESE)* applying **sequential filters** (criticality, queue depth, LoRA affinity, KV-cache usage) and contains no plugin-**scoring** sentence. The *Sheddable* quote in the same paragraph **is** from that article and is verbatim, so only the first quote is in doubt. *Method:* find the wording in the GIE EPP architecture docs (or Google Cloud's GKE Inference Gateway docs) and re-cite, or drop it. The §3.3 claim that the EPP reads `vllm:kv_cache_usage_perc` and queue depth is independently supported by the *Sheddable* rule and stands. |

### Checked and found sound — no change

`max_concurrency` § 2.2 and the §6.1 ratio column (11,184/953 = 11.74, 10,199/869 = 11.74, 325/45 = 7.22, 101/64 = 1.58, 1,934/142 = 13.62 — all as printed); §2.3's batch/TPOT ladder (4.45 × batch/32 reproduces 8.90 / 17.80 / 35.60 / 71.2 / ~2,420 exactly) and the 136× gap *at batch 128*; §2.4's `256 × 6 = 1,536` decode floor, 8,192 − 1,536 = 6,656 headroom and ~20 steps for 128K; §4.10's 6,656 ÷ 31,750 = 210 ms and 210 ÷ 17.8 = **12×**; §6.2's 32 × 20 = 640 steps ≈ 11 s at 17.8 ms; §6.4's 753 × 186 ms = 140 s, 2 s ÷ 186 ms ≈ 10.75 in flight, 753/10.75 ≈ **75×**; §5.5's 2,000 × 35.6 ms = 71.2 s; §4.3's 16.11/7.89 = **2.04×**; §8.2's `0.129 + 511 × 0.0356 = 18.32 s`, `256/18.32 = 13.97 req/s`, `4 × 13.97 = 55.9`, `55.9 × 512 = 28,621` vs `4 × 7,191 = 28,764` (0.5 %); §8.3's `13.97 × 1.871 = 26.1`, `4096/0.129 = 31,752`, `2.0 × 31,752 = 63,504`, `17,408/282 = 62×`; §8.4's `279.5 − 55.9 = 223.6`, `× 60 = 13,416`, `/55.9 = 240 s`, `223.6/279.5 = 80 %`, `0.20 × 55.9 = 11.2` and `5 × 11.2 = 55.9`; §2.2's Qwen3.8-27B "state 392 MB vs KV 256 MB at 8K" (8,192 × 32 KiB = 256 MiB); §4.8's −64 % (3,377 → 1,212 = −64.1 %). All recomputed in `python3`.

### Not re-opened

Everything already carrying a ⚠️ in §1 (which flags shipped in 0.29.0 / 0.5.20) and the sixteen entries in *Open questions* (of which the Dynamo 1.4.2-vs-1.5.0 entry is now resolved — see the dated line below) — those state their own method and are unchanged by this pass. This document's claims about the five repo models were checked **against this repository's own pair docs**, which are the tree's authority for them; they have no external primary source and none was sought here.

**2026-09-19 (follow-up pass).** Resolved the Dynamo **1.4.2 vs. 1.5.0** contradiction left open by the pass above, using the primary sources already in this tree rather than a new fetch: the numbers name different artefacts — v1.4.2 is the last numbered stable release / container tag (GitHub releases, 2026-08-29), 1.5.0 is the current PyPI `ai-dynamo` stable (uploaded 2026-09-19 04:21 UTC), and the docs site's `latest` index tracks the container release — per [`02` §2.1 line 150](02-serving-stack-and-routing.md#21-version-pin) and its verification log entry 5 ([GitHub releases](https://github.com/ai-dynamo/dynamo/releases), [PyPI](https://pypi.org/pypi/ai-dynamo/json)), matching the pin in [`10` §2](10-blueprint.md). The §1 version table row, *Open questions* item 2 and the *Not re-opened* paragraph were updated to say so; for which tag to actually deploy, follow [`09` §2.1](09-reference-architectures.md#21-nvidia-dynamo).
