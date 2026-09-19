# Autoscaling, predictive scaling and capacity planning

Research date: **2026-09-19**. Formulas, markers (`[src]`, ⚠️ TO BE VERIFIED,
`est.`, `meas.`) and the pinned GPU/model inputs come from
[`../METHODOLOGY.md`](../METHODOLOGY.md); nothing here re-derives a number that
document already pins. Hardware context: 8×B300 HGX nodes (268 GB/GPU, 2,144 GB
per node — [METHODOLOGY §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)),
NVLink inside the node, InfiniBand/RoCE between nodes, local NVMe, plus AWS p6
for burst.

Docs this one leans on rather than repeats:

| For | Read |
|---|---|
| Which model fits on how many GPUs, and at what parallelism | [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md), [`../matrix/pairs.json`](../matrix/pairs.json) |
| $/1M tokens at each operating point, and the GPU-hour prices | [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md), [`../cross-cutting/cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md) |
| Continuous batching, chunked prefill, the throughput↔TPOT Pareto curve, prefix caching, P/D disaggregation | [`../cross-cutting/serving-optimizations.md` §1, §3.5, §4](../cross-cutting/serving-optimizations.md) |
| Engine versions and per-GPU support | [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) |

Engine versions pinned by that last doc and used throughout here: **vLLM
0.29.0, SGLang 0.5.20, TensorRT-LLM 1.2.1 stable / 1.3.0rc27, Dynamo 1.5.0**
([`../cross-cutting/inference-engines.md` §2](../cross-cutting/inference-engines.md)).
Where a cited doc page is published at a different version (e.g. the Dynamo
planner pages resolve at v1.3.0), that is stated at the citation.

---

## 0. The five sentences

1. **A replica is 1–8 GPUs and takes minutes to become ready**, so CPU-style HPA
   on utilisation is structurally wrong for LLM serving — you must scale on
   *demand* signals (queue, KV pressure, token backlog), not on *saturation*
   signals (GPU busy).
2. **On owned bare metal, scaling down saves nothing by itself.** A reserved or
   owned B300 costs the same idle as busy; the money only appears if the freed
   GPUs are handed to another workload (batch, training, another model). That
   makes *fleet scheduling* (§7) the real cost lever and autoscaling the
   *latency* lever.
3. **Predictive scaling earns its keep exactly when lead time exceeds headroom
   burn time.** For this repo that is true for DeepSeek-V4.1-Flash and Kimi-K3
   (minutes to boot) and false for Marlin-2B and Qwen3.8-27B (tens of seconds).
   The arithmetic is in §4.4 and §8.
4. **Reactive and predictive compose by `max()`**, the way AWS composes dynamic
   and predictive policies [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html);
   never let a forecast scale you *down*.
5. **Kimi-K3 never scales to zero and never scales below 1 node**; Marlin-2B and
   Qwen3.8-27B are the only two models in this repo where scale-to-zero is
   arithmetically defensible (§5.4).

---

## 1. What to scale, and why CPU-style HPA is wrong here

### 1.1 The five scaling axes

| Axis | Unit of change | Time to take effect | Who can change it at runtime |
|---|---|---|---|
| **Replicas (DP)** | +1 replica = +1…8 GPUs | minutes (weight load + compile + warm-up, §4.5) | HPA/KEDA/KPA/Ray/Dynamo planner |
| **Parallelism size (TP/EP/PP)** | reshard the same weights over a different GPU count | full restart of the replica | nobody, in practice — treat as a *deploy*, not a *scale* |
| **P/D pool sizes** | +1 prefill worker or +1 decode worker, independently | minutes | Dynamo Planner, llm-d (per-role `ScaledObject`), AIBrix StormService roles |
| **KV tiers** | GPU HBM → host DRAM → NVMe → remote store | seconds to minutes (cache is cold) | LMCache / Dynamo KVBM / Mooncake configuration |
| **Admission** | max concurrency, queue depth, priority | milliseconds | engine scheduler, Gateway API Inference Extension flow control |

Only the first, third and fifth are genuinely elastic. **Changing TP or EP is a
redeploy**, because the checkpoint is sharded at load time; `TP4 → TP8` for
DeepSeek-V4.1-Flash means tearing down the replica and re-streaming 510.29 GB
([METHODOLOGY §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)).
Autoscalers that advertise "variant" scaling — llm-d's WVA, which chooses among
"multiple model servers in an InferencePool that all serve the same base model
but differ in hardware configuration (e.g. GPU type), serving configuration
(e.g. tensor parallelism, max batch size, quantization), or both" — do it by
running *both* variants and shifting replica counts between them, not by
resharding a live one [src](https://llm-d.ai/docs/architecture/advanced/autoscaling).

### 1.2 Replica granularity for this repo's five models

From [`../matrix/pairs.json`](../matrix/pairs.json), B300 rows:

| Model | Min GPUs | Recommended replica shape on 8×B300 | Replicas per node | Seats (concurrency) per replica @8K |
|---|---:|---|---:|---:|
| Marlin-2B | 1 | TP1 | 8 | 1,934 |
| Qwen3.8-27B | 1 | TP1 | 8 | 325 |
| DeepSeek-V4.1-Flash | 2 | TP4 (TP2 possible with Engram CPU-offload) | 2 (TP4) / 4 (TP2) | 11,184 (TP4 node-level figure) |
| DeepSeek-V4.1-Flash-NVFP4 | 4 | TP4 | 2 | 40,801 |
| Kimi-K3 | 8 | TP8 + DCP8, one whole node | 1 | 101 |

The consequence for autoscaling is blunt: **the quantum of capacity is not
1 GPU, it is one replica**, and for Kimi-K3 one replica is one *node*. A scale
event for Kimi-K3 moves capacity in steps of 100 % (1 node → 2 nodes), so any
autoscaler tuned on percentage thresholds will either never fire or double the
fleet. Kimi-K3 belongs on scheduled/manual capacity with a queue in front, not
on a reactive autoscaler (§8.4).

### 1.3 Why utilisation-based HPA is the wrong control law

Three independent reasons, all sourced:

1. **GPU busy is pinned near 100 % regardless of load.** llm-d states it
   plainly: *"GPU utilization is often pegged near 100% during active batching
   regardless of actual load, making it an entirely unreliable signal"*
   [src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md).
   The DCGM field usually scraped, `DCGM_FI_DEV_GPU_UTIL`, is the "fraction of
   time at least one kernel was running", which a decode loop satisfies at batch
   1 and at batch 256 alike [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html).
2. **Utilisation is a lagging indicator.** *"Traditional autoscaling indicators
   like resource utilization metrics (CPU/GPU) are often lagging indicators —
   they only reflect saturation after it has already occurred, by which point
   latency has spiked and requests may be failing"*
   [src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md).
3. **HPA's control law assumes capacity is linear and instant.** The algorithm
   is `desiredReplicas = ceil[currentReplicas × (currentMetricValue /
   desiredMetricValue)]` with a default 10 % tolerance
   [src](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/).
   With a 4-GPU replica that takes minutes to boot, a doubling of the metric
   asks for a doubling of the fleet that arrives after the burst is over —
   classic integral windup. This is exactly what AIBrix's APA adds a
   "fluctuation tolerance" for, and what its own RFC calls *cascading scale-up*
   when pending replicas are counted as absent
   [src](https://github.com/vllm-project/aibrix/issues/2670).

The one place GPU-level telemetry still belongs is **efficiency accounting**,
not control: `DCGM_FI_PROF_SM_ACTIVE` ("ratio of cycles an SM has at least 1
warp assigned") and `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` tell you whether an idle
*looking* replica is memory-bound or genuinely idle
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html).
Compare against the MBU/MFU bands in
[METHODOLOGY §4](../METHODOLOGY.md#4-throughput-and-latency-roofline) and the
measured achievements in [`../cross-cutting/serving-optimizations.md` §4.4](../cross-cutting/serving-optimizations.md).

---

## 2. Signals: which ones lead, which ones lag

### 2.1 The signal ladder

| Signal | Leads or lags | What it actually measures | Failure mode as a scaling signal |
|---|---|---|---|
| **Token backlog** (uncached prompt tokens dispatched, not yet prefilled) | **leading** | prefill work outstanding, in the unit prefill is bound by | needs a calibrated tokens/s constant per (model, GPU, parallelism) |
| **Request queue depth** | **leading** | requests the scheduler could not admit this step | blind to request *size*: an 8K prompt and a 512-token prompt count 1 each |
| **KV-cache utilisation** | **leading (decode)** | fraction of the KV block pool in use | saturates at 1.0 and stays there; useless above the knee |
| **Batch occupancy / running requests** | coincident | how full the continuous batch is | capped by `max_num_seqs`, so it plateaus before latency does |
| **TTFT / TPOT SLO attainment** | **lagging** | the thing users feel | by the time it moves, you are already violating |
| **tokens/s served** | lagging | delivered throughput | falls when you are *overloaded* as well as when you are *idle* |
| **GPU busy (`DCGM_FI_DEV_GPU_UTIL`)** | neither | whether any kernel ran | pinned ~100 % at any load (§1.3) |

**Decision rule.** Scale *up* on a leading signal and *down* on a lagging one.
Up on queue/backlog/KV gives you lead time; down on sustained low occupancy plus
healthy latency avoids removing a replica that was about to be needed. The
trade-off is that leading signals over-provision on short spikes — which is what
stabilisation windows and cooldowns (§5.2) exist to damp.

**Second decision rule — tokens over requests when prompt sizes vary.** llm-d
states the case exactly: *"An 8192-token prompt is 16× the prefill work of a
512-token one, and a request counter rates them the same… the same request rate
can be a third of a replica or three replicas of prefill work"*
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-token-aware/README.md).
For this repo that matters most for DeepSeek-V4.1-Flash (agentic coding, 4K–128K
prompts, see [`../cross-cutting/serving-optimizations.md` §7.1](../cross-cutting/serving-optimizations.md))
and for Kimi-K3 (1M-context sessions, §7.2 there). It matters least for
Marlin-2B, whose video token budget is fixed by the frame cap
(2 fps, ≤ 240 frames, 200,704 px/frame — [METHODOLOGY §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)).

### 2.2 Engine metric names, verbatim

**vLLM 0.29.0** — v1 metrics, `/metrics` on the API server
[src](https://docs.vllm.ai/en/stable/design/metrics/):

| Metric | Type | Use |
|---|---|---|
| `vllm:num_requests_waiting` | gauge | primary scale-up trigger |
| `vllm:num_requests_running` | gauge | occupancy / target-concurrency trigger |
| `vllm:kv_cache_usage_perc` | gauge, 0–1 | decode-side pressure |
| `vllm:request_queue_time_seconds` | histogram | queueing share of TTFT |
| `vllm:time_to_first_token_seconds` | histogram | SLO attainment (lagging) |
| `vllm:inter_token_latency_seconds` | histogram | TPOT/ITL SLO attainment (lagging) |
| `vllm:prefix_cache_queries` / `vllm:prefix_cache_hits` | counters | cache-hit rate → effective prefill work |
| `vllm:prompt_tokens_total` / `vllm:generation_tokens_total` | counters | token rate, for capacity accounting |

Two naming traps. First, **`vllm:gpu_cache_usage_perc` is the v0 name; v1 calls
it `vllm:kv_cache_usage_perc`** — the v1 page defines `vllm:kv_cache_usage_perc`
("Fraction of used KV cache blocks (0–1)") and marks `vllm:num_requests_swapped`
and `vllm:cpu_cache_usage_perc` as legacy, removed with V0 preemption-by-swap
and V0 CPU offloading [src](https://docs.vllm.ai/en/stable/design/metrics/).
⚠️ TO BE VERIFIED — that page does **not** itself state that
`gpu_cache_usage_perc` was the v0 spelling of the same gauge; the rename is
inferred from the two names' descriptions.
AIBrix's own docs still show `gpu_cache_usage_perc` in the PodAutoscaler example
[src](https://aibrix.readthedocs.io/latest/features/autoscaling/metric-based-autoscaling.html) —
check against your engine build before copying. Second, it measures **occupancy
of vLLM's pre-allocated KV block pool, not physical VRAM**; the pool is carved
from `--gpu-memory-utilization` at start-up, so "80 % KV" is 80 % of a number
*you* chose.

**SGLang 0.5.20** — enable with `--enable-metrics`
[src](https://github.com/sgl-project/sglang/blob/main/docs/docs/references/production_metrics.mdx):

| Metric | Type | Use |
|---|---|---|
| `sglang:num_queue_reqs` | gauge | waiting queue depth — the scale-up trigger |
| `sglang:num_running_reqs` | gauge | occupancy |
| `sglang:token_usage` | gauge | KV fill fraction (SGLang's equivalent of `kv_cache_usage_perc`) |
| `sglang:num_used_tokens` | gauge | absolute KV tokens held |
| `sglang:cache_hit_rate` | gauge | RadixAttention prefix hit rate |
| `sglang:gen_throughput` | gauge | generate throughput, tok/s |
| `sglang:time_to_first_token_seconds`, `sglang:time_per_output_token_seconds`, `sglang:e2e_request_latency_seconds` | histograms | SLO attainment |
| `sglang:spec_num_steps`, `sglang:spec_num_draft_tokens` | gauges | live speculative-decoding shape (relevant when DSpark γ changes under load — see [`../cross-cutting/serving-optimizations.md` §2.5](../cross-cutting/serving-optimizations.md)) |

**TensorRT-LLM 1.3.0rc27** — `trtllm-serve` exposes Prometheus text at
**`/prometheus/metrics`** (confirmed verbatim on the example page as
`http://localhost:8000/prometheus/metrics`)
[src](https://nvidia.github.io/TensorRT-LLM/examples/prometheus_metrics.html).
⚠️ TO BE VERIFIED — the claim that the plain `/metrics` route returns JSON
iteration stats Prometheus cannot parse is **not** on that page; *close by:*
curl both routes on 1.3.0rc27.
Names: `trtllm_num_requests_waiting`, `trtllm_num_requests_running`,
`trtllm_kv_cache_utilization`, `trtllm_kv_cache_hit_rate`,
`trtllm_kv_cache_reused_blocks_total`, `trtllm_kv_cache_missed_blocks_total`,
`trtllm_request_queue_time_seconds`, `trtllm_time_to_first_token_seconds`,
`trtllm_e2e_request_latency_seconds`. The three dynamic gauges require the
server to be started with **both** `return_perf_metrics: true` **and**
`enable_iter_perf_stats: true` (both default false, passed via
`--extra_llm_api_options`) and are only present from **v1.3.0rc12** onward
(PR #12545); 1.2.1 GA exposes request-lifecycle histograms only
[src](https://github.com/NVIDIA/TensorRT-LLM/issues/12298). ⚠️ TO BE VERIFIED —
the PR number and the rc12 cut-off come from an issue thread, not from the
release notes; confirm against the `1.3.0` changelog before depending on it in a
production ScaledObject.

**Dynamo 1.5.0 planner** — publishes its own decisions under `dynamo_planner_*`:
`dynamo_planner_estimated_ttft_ms`, `dynamo_planner_estimated_itl_ms`,
`dynamo_planner_predicted_num_prefill_replicas`,
`dynamo_planner_predicted_num_decode_replicas`, per-engine capacity and queue
depths, and scaling-decision enums; it *reads* `dynamo_frontend_*` or
`dynamo_component_router_*` depending on `throughput_metrics_source`
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide)
(page published at v1.3.0; ⚠️ re-check the metric list on the 1.5.0 docs).

**llm-d router (EPP)** — `llm_d_epp_flow_control_queue_size` (requests waiting
in flow control), `llm_d_epp_request_running`, `llm_d_epp_inflight_tokens`
(uncached prompt tokens dispatched but not yet prefilled), and
`llm_d_epp_ready_endpoints`
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md),
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-token-aware/README.md).

### 2.3 Exposing them to Kubernetes

Three plumbing options, in the order you should prefer them as of 2026-09-19:

1. **KEDA Prometheus scaler** → KEDA's metrics server → `external.metrics.k8s.io`
   → an HPA that KEDA owns. This is now the recommended path in llm-d
   (*"the Prometheus Adapter is planned for deprecation, and it is recommended
   to use KEDA instead"*)
   [src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md)
   and in KServe (`serving.kserve.io/autoscalerClass: "keda"`)
   [src](https://kserve.github.io/website/docs/model-serving/generative-inference/autoscaling).
2. **Prometheus Adapter** → `custom.metrics.k8s.io` / `external.metrics.k8s.io`
   → a hand-written HPA. Still works; deprecated in llm-d's guidance above.
3. **Push-based OTel** → `keda-otel-add-on` gRPC endpoint → KEDA external
   scaler. KServe documents this for "pod-level metrics (including LLM
   metrics)", and notes you must install the add-on **with validation disabled**
   because vLLM's `vllm:`-prefixed names violate the add-on's default naming
   constraints [src](https://kserve.github.io/website/docs/model-serving/generative-inference/autoscaling).

A trap worth printing, from llm-d's OpenShift notes: **KEDA silently serves
`fallback` replicas when a trigger errors**, so an unauthenticated Thanos query
returning 401 "looks healthy while doing nothing"
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-queue/README.md).
Alert on scaler errors, not just on replica counts.

Second trap, same source, worth generalising: a `GaugeVec` whose series are
**not pruned when an endpoint disappears** leaves a scaled-down pod's last value
frozen, so `sum()` never falls and the fleet scales up but never down. That was
`llm_d_epp_inflight_tokens` (llm-d-router#2529, fixed by #2577, merged after
`v0.10.0`) [src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-token-aware/README.md).
Verify the same property for whichever per-pod gauge you scale on.

---

## 3. Mechanisms on Kubernetes

### 3.1 HPA — the substrate everything else sits on

```
desiredReplicas = ceil[ currentReplicas × ( currentMetricValue / desiredMetricValue ) ]
```

with a **default tolerance of 0.1** (no action while the ratio is within
0.9–1.1), configurable per-HPA from Kubernetes **v1.33** (alpha) / **v1.34**
(beta), and a controller sync period defaulting to **15 s**
(`--horizontal-pod-autoscaler-sync-period`)
[src](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/).
Default `behavior`: scale-up stabilisation window **0 s**, scale-down **300 s**
[src](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/).

For LLM replicas the defaults are wrong in both directions. A tuned starting
point for a 4-GPU replica with a ~5–10 min boot (§4.5):

```yaml
behavior:
  scaleUp:
    stabilizationWindowSeconds: 60      # ignore < 1 min of queue; boot is minutes anyway
    policies:
    - type: Pods
      value: 2                          # never add more than 2 replicas (8 GPUs) per step
      periodSeconds: 300                # one step per boot-time window
    selectPolicy: Max
  scaleDown:
    stabilizationWindowSeconds: 1800    # 30 min: a replica costs minutes to get back
    policies:
    - type: Pods
      value: 1
      periodSeconds: 600
```

The rule behind the numbers: **`periodSeconds` on scale-up ≥ the replica's
time-to-ready**, otherwise the controller adds a second batch of replicas while
the first is still loading weights and you overshoot by the boot-time ÷ period
factor. This is the cascading-scale-up failure AIBrix filed an RFC against
[src](https://github.com/vllm-project/aibrix/issues/2670).

### 3.2 KEDA

`ScaledObject` fields and defaults
[src](https://keda.sh/docs/2.20/reference/scaledobject-spec/):

| Field | Default | Meaning for LLM serving |
|---|---:|---|
| `pollingInterval` | 30 s | how often triggers are evaluated; governs the 0→1 transition |
| `cooldownPeriod` | 300 s | wait after last active trigger before scaling **to zero** |
| `initialCooldownPeriod` | 0 s | grace after ScaledObject creation before zero is allowed |
| `minReplicaCount` | 0 | set ≥ 1 for anything you will not accept a cold start on |
| `maxReplicaCount` | 100 | must be clamped to the GPU budget, or you queue Pending pods forever |
| `idleReplicaCount` | unset | only 0 is supported today (HPA limitation) |
| `fallback.replicas` / `.behavior` | — | what to serve when the scaler errors; **set it, and alert on it** |
| `advanced.horizontalPodAutoscalerConfig.behavior` | — | passes §3.1's stabilisation/policies straight through |
| `advanced.scalingModifiers.formula` / `.target` | — | combine several triggers into one composite metric |

Prometheus scaler trigger metadata: `serverAddress`, `query` (must return a
single element), `threshold`, `activationThreshold` (default 0),
`ignoreNullValues` (default `true`), `queryParameters`, `customHeaders`,
`unsafeSsl`, `timeout` [src](https://keda.sh/docs/2.20/scalers/prometheus/).

A working shape for DeepSeek-V4.1-Flash decode replicas, combining a leading
queue signal with a KV-pressure signal — the `max()` composition llm-d uses
(`max(ceil(backlog_s / 1.5), ceil(kv / 0.8))`)
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-token-aware/README.md):

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: deepseek-v41f-decode
spec:
  scaleTargetRef:
    name: deepseek-v41f-decode          # Deployment of TP4 replicas
  pollingInterval: 15
  cooldownPeriod: 1800
  minReplicaCount: 2                    # never below N+1 for a single-node failure
  maxReplicaCount: 12
  fallback:
    failureThreshold: 3
    replicas: 6                         # ~steady-state; never fall back to 1
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:                          # as in §3.1
        scaleUp:   {stabilizationWindowSeconds: 60,   policies: [{type: Pods, value: 2, periodSeconds: 300}]}
        scaleDown: {stabilizationWindowSeconds: 1800, policies: [{type: Pods, value: 1, periodSeconds: 600}]}
  triggers:
  - type: prometheus                     # prefill backlog expressed as SECONDS of wait
    metadata:
      serverAddress: http://prometheus.monitoring.svc:9090
      threshold: '1.5'                   # seconds of prefill backlog tolerated per replica
      query: |
        sum(llm_d_epp_inflight_tokens{model_name="deepseek-ai/DeepSeek-V4.1-Flash"}) / 42000
  - type: prometheus                     # decode occupancy
    metadata:
      serverAddress: http://prometheus.monitoring.svc:9090
      threshold: '0.8'
      query: |
        avg(vllm:kv_cache_usage_perc{model_name="deepseek-ai/DeepSeek-V4.1-Flash"})
```

The `42000` is **`peakPrefillThroughput` (`V_P`), the one hardware-specific
constant this design needs** — prompt tokens one replica prefills per second.
llm-d ships `calibrate.sh`, which computes `V_P = CHUNK_SIZE / median(TTFT)` on
an idle stack and warns that `CHUNK_SIZE` must match vLLM's effective
`--max-num-batched-tokens` "or the result is silently wrong"; its reference
figures are `15928` for Qwen3-32B on H100-80GB at TP=2 and `33821` for a
gpt-oss-120b H200 fleet
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-token-aware/README.md).
⚠️ TO BE VERIFIED — the `42000` above is a **placeholder**, not a measurement.
It must be calibrated on a B300 TP4 DeepSeek-V4.1-Flash replica before use;
llm-d's own warning is that the same model and GPU measured through a different
path varied `V_P` from 2696 to 15928 and changed the replica answer from 8 to 3.

**Cron scaler** for known events: KEDA's cron trigger "can maintain a specified
replica range during scheduled periods… prevent workloads from scaling below a
required capacity during seasonal peaks"
[src](https://keda.sh/docs/2.20/scalers/). This is the cheapest form of
predictive scaling and the first one to deploy (§4.6).

**Predictive**: KEDA has no first-party forecaster. The documented route is
"AI-based predictive scaling based on Prometheus metrics & PredictKube SaaS"
[src](https://keda.sh/docs/2.20/scalers/) — a third-party SaaS scaler, which for
a private bare-metal cluster is usually a non-starter on data-egress grounds.
The realistic alternative is to compute the forecast yourself and publish it as
a Prometheus series, then point a plain Prometheus trigger at it (§4.7).

### 3.3 Knative KPA and KServe

KPA runs two windows: a **stable window** (default `60s`, range 6s–1h,
per-revision via `autoscaling.knative.dev/window`) and a **panic window**
derived as `panic-window-percentage` (default `10.0`) of it, entered when load
crosses `panic-threshold-percentage` (default `200.0`); rate limits are
`max-scale-up-rate` `1000.0` and `max-scale-down-rate` `2.0`
[src](https://knative.dev/docs/serving/autoscaling/kpa-specific/). The default
target is **100 concurrent requests per pod** with
`desired pods = total concurrency / (target × target-utilization)`
[src](https://knative.dev/docs/serving/autoscaling/autoscale-go/). Scale-to-zero
is on by default with `scale-to-zero-grace-period` `30s` and
`scale-to-zero-pod-retention-period` `0s`
[src](https://knative.dev/docs/serving/autoscaling/scale-to-zero/).

**Verdict for this repo:** KPA's concurrency model is a reasonable fit for
Marlin-2B (uniform, short video requests) and a poor fit for everything else,
because a concurrency target treats a 128K-token DeepSeek request and a 4K one
identically (§2.1). The 6 s panic window is also shorter than any replica's boot
time here, so panic mode will command scale-ups that cannot land — combine it
with a `max-scale-up-rate` clamp or do not use it.

KServe: for generative inference the documented path is KEDA, not KPA, via
`serving.kserve.io/autoscalerClass: "keda"` plus an `External` metric whose
`backend: "prometheus"` runs a vLLM query
[src](https://kserve.github.io/website/docs/model-serving/generative-inference/autoscaling)
(docs at KServe **0.20**):

```yaml
annotations:
  serving.kserve.io/autoscalerClass: "keda"
  serving.kserve.io/enable-prometheus-scraping: "true"
spec:
  minReplicas: 1
  maxReplicas: 5
  autoScaling:
    metrics:
    - type: External
      external:
        metric:
          backend: "prometheus"
          query: vllm:num_requests_running
        target:
          type: Value
          value: "2"
```

### 3.4 Ray Serve

`autoscaling_config` fields and defaults, Ray docs
[src](https://docs.ray.io/en/latest/serve/advanced-guides/advanced-autoscaling.html):

| Field | Default | Note |
|---|---:|---|
| `target_ongoing_requests` | 2.0 | requests per replica the autoscaler steers to |
| `max_ongoing_requests` | 5 | hard queue cap per replica; docs advise 20–50 % above target |
| `min_replicas` / `max_replicas` | 1 / 1 | |
| `upscale_delay_s` | 30 | |
| `downscale_delay_s` | 600 | |
| `upscaling_factor` / `downscaling_factor` | 1.0 / 1.0 | multiplicative damping |
| `metrics_interval_s` | 10 | replica → controller reporting |
| `look_back_period_s` | 30 | averaging window |

Ray Serve's signal is *ongoing requests per replica* — the coincident signal in
§2.1, not a leading one. For LLM deployments the defaults of 2 / 5 are wildly
below a vLLM replica's useful concurrency (DeepSeek TP4 on B300 runs at
concurrency 128 at the interactive point, [`../matrix/pairs.json`](../matrix/pairs.json)),
so they must be raised to the batch size you actually operate at or Ray will
scale out at 3 in-flight requests. `downscale_delay_s = 600` is, by contrast, a
sensible default to keep.

### 3.5 Dynamo Planner — the only SLA-native autoscaler in the stack

Dynamo's planner scales prefill and decode pools separately against TTFT/ITL
targets. Four `optimization_target` values — `throughput` (default; static queue
and KV thresholds), `latency`, `load` (user thresholds), `sla` (performance
model + prediction)
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner).

Two loops, independently switchable:

| Loop | Default interval | Default on? | Inputs |
|---|---:|---|---|
| Throughput-based (long horizon) | `throughput_adjustment_interval_seconds` = **180 s** | `enable_throughput_scaling: true` | Prometheus: request counts/durations, TTFT/ITL, ISL/OSL, KV hit rate |
| Load-based (burst) | `load_adjustment_interval_seconds` = **5 s** | `enable_load_scaling: false` | ForwardPassMetrics: per-iteration wall time, scheduled prefill/decode tokens, queued requests |

*"Throughput updates the lower-bound replicas, then load-based scaling can
adjust above that floor"*
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide).

The replica math, from the planner design doc
[src](https://docs.nvidia.com/dynamo/v-0-9-1/design-docs/planner-design):

```
predicted_load   = next_requests × next_isl / interval × min(1, prefill_correction)
prefill_replicas = ceil( predicted_load / interpolated_throughput / gpus_per_engine )

corrected_itl    = target_itl / decode_correction_factor
throughput_per_gpu = decode_interpolator(corrected_itl, context = next_isl + next_osl/2)
decode_replicas  = ceil( next_num_req × next_osl / interval / throughput_per_gpu / gpus_per_engine )

prefill_correction = actual_ttft / expected_ttft
decode_correction  = actual_itl  / expected_itl
```

The correction factors are the interesting part: they are online-learned
multipliers that absorb "queueing, prefix cache hits, chunked prefill in decode,
and metric variance" — i.e. everything the offline profile got wrong. This is
the structure to copy even if you do not run Dynamo: **profile offline, predict,
then correct with a ratio of measured to expected.**

Load predictors: `constant`, `ARIMA` (default for request count, ISL and OSL),
`Kalman` (`kalman_q_level`, `kalman_q_trend`, `kalman_r`, `kalman_min_points`)
and `Prophet` (`prophet_window_size`, default 50 s, needs
`load_predictor_warmup_trace`). KV hit rate and speculative accept length are
**not** predicted — the planner uses "the latest valid observation"
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide).

Profiling modes: `rapid` (~30 s, AIC simulation, no GPUs), `thorough` (2–4 h,
real GPU execution), `none` (cold-start FPM regression); results land in a
ConfigMap mounted as `--config /path/to/planner_config.json`
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide).

Kubernetes shape [src](https://docs.nvidia.com/dynamo/kubernetes/auto-deployment/dynamo-planner):

```yaml
spec:
  features:
    planner:
      mode: disagg
      backend: vllm
      optimization_target: sla
      enable_throughput_scaling: true
      enable_load_scaling: true
      ttft_ms: 500.0
      itl_ms: 50.0
      pre_deployment_sweeping_mode: rapid
      min_endpoint: 1
      max_gpu_budget: 8
      load_scaling_down_sensitivity: 80
      advisory: false            # set true first: observe, do not scale
```

**Two caveats that decide whether you can use it here.** First,
*"When the Planner removes a worker, it terminates it **without draining
in-flight requests**"* — the mitigation NVIDIA documents is to keep
`min_endpoint` above steady-state demand and lower
`load_scaling_down_sensitivity`
[src](https://docs.nvidia.com/dynamo/kubernetes/auto-deployment/dynamo-planner).
For a 128K-context DeepSeek request that is a multi-second generation thrown
away. Second, load-based scaling needs ForwardPassMetrics support (**vLLM,
TensorRT-LLM non-DP, SGLang ≥ 0.5.13**)
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner) — and the design
doc adds that *"the load-based code path exists but is non-functional with
current backends (no prefill queue metrics)"*
[src](https://docs.nvidia.com/dynamo/v-0-9-1/design-docs/planner-design).
⚠️ TO BE VERIFIED — those two statements are published at different doc versions
(v1.3.0 vs v-0-9-1) and contradict each other; test `enable_load_scaling` in
`advisory: true` mode on Dynamo 1.5.0 before believing either.

### 3.6 llm-d and AIBrix

**llm-d** now offers five KEDA-driven paths, and its own comparison table is the
clearest statement of the trade-off in the ecosystem
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md):

| Path | Signal | Best for | Cost-aware? |
|---|---|---|---|
| Queue-based | `llm_d_epp_flow_control_queue_size`, `llm_d_epp_request_running` | homogeneous prompts; absolute thresholds | no |
| Saturation-based | normalised pool saturation 0.0–1.0+ | portable thresholds; may over-provision | no |
| Token-aware (experimental) | `llm_d_epp_inflight_tokens ÷ V_P` (seconds of backlog) + `vllm:kv_cache_usage_perc` | heterogeneous prompt sizes; threshold derived from the TTFT SLO | no |
| SLO-aware | EPP **estimated** TTFT/TPOT ÷ SLO, via a recording rule and an `expr-lang` formula in the ScaledObject | per-request latency SLOs | minimises replicas to meet SLO |
| WVA (legacy) | `wva_desired_replicas` from KV util + queue + performance budgets | multiple hardware variants of one model | **yes** — adds on cheapest variant, removes from most expensive |

The WVA repository on `main` is now **deprecated**, last supported at `v0.9.0`
on `release-0.9`, with the code moved to a frozen `legacy/` directory and KEDA
taking over as the scaling engine
[src](https://github.com/llm-d-incubation/workload-variant-autoscaler). Plan
against the KEDA+EPP paths, not WVA.

**AIBrix** ships a `PodAutoscaler` CRD with three strategies
[src](https://aibrix.readthedocs.io/latest/features/autoscaling/metric-based-autoscaling.html):

```yaml
apiVersion: autoscaling.aibrix.ai/v1alpha1
kind: PodAutoscaler
spec:
  scalingStrategy: APA          # HPA | KPA | APA
  minReplicas: 1
  maxReplicas: 10
  metricsSources:
  - metricSourceType: pod
    targetMetric: gpu_cache_usage_perc   # NB: v0 name — see §2.2
    targetValue: "50"
  scaleTargetRef:
    kind: Deployment
    name: target-deployment
```

with `observeWindowSeconds` (stable window, default **180 s**, range 1–3600),
`panicWindowSeconds` (KPA, default **60 s**, documented range 1–3600 s; ⚠️ the
requirement that it be ≤ the observe window is not stated in the docs), and
`scale-up-tolerance` / `scale-down-tolerance` both default **0.1**. APA is
"similar to HPA but has a fluctuation parameter which acts as a minimum buffer
before triggering scaling up and down to prevent oscillation"
[src](https://aibrix.readthedocs.io/latest/designs/aibrix-autoscaler.html).
It also supports **multi-metric** scaling (highest demand wins), **scheduled
replica bounds** with timezone and day-of-week — the cron primitive again — and
**role-level scaling for StormService prefill/decode roles**
[src](https://aibrix.readthedocs.io/latest/features/autoscaling/metric-based-autoscaling.html).

AIBrix's **optimizer-based** autoscaler is the closest open-source thing to
capacity planning as a control loop: gateway plugins record token statistics in
Redis; `aibrix_benchmark` measures GPU performance across input/output patterns;
`aibrix_gen_profile` builds per-GPU-type capacity profiles; a GPU optimizer
watching `model.aibrix.ai/name` solves for the replica count meeting the SLO at
lowest cost across *heterogeneous* GPU types; the answer is published as the
Prometheus metric `vllm:deployment_replicas` on
`aibrix-gpu-optimizer.aibrix-system.svc.cluster.local:8080/metrics/<ns>/<deployment>`
and consumed by a PodAutoscaler with an `external` metric source
[src](https://aibrix.readthedocs.io/latest/features/autoscaling/optimizer-based-autoscaling.html).
Reported gains vs native HPA: *"reduce latency by 11.5%, increases token
throughput by 11.4%, and minimizes scaling oscillations by 33%"*
[src](https://aibrix.readthedocs.io/latest/designs/aibrix-autoscaler.html) —
vendor-claimed, in their own harness. The paper's headline (50 % throughput,
70 % latency) is for the whole system, not the autoscaler
[src](https://arxiv.org/abs/2504.03648), submitted 2025-02-22.

### 3.7 The bare-metal problem: there is no cluster autoscaler

Every mechanism above changes **pod** counts. On a cloud cluster, Cluster
Autoscaler or Karpenter then changes **node** counts by calling an API. On owned
bare metal there is no such API: the node pool is what you racked. Three
consequences and the three answers:

| Consequence | Answer |
|---|---|
| `maxReplicas` is a real, physical bound. Exceeding it produces `Pending` pods, not capacity | Clamp `maxReplicaCount` to `floor(free_GPUs / gpus_per_replica)` and alert on Pending-with-GPU-request |
| Scaling *down* frees GPUs that nobody claims, so it saves nothing (§0.2) | Give the freed GPUs to a lower-priority queue: Kueue cohorts, Volcano/KAI queues (§7) |
| Peak capacity must exist physically, or overflow off-cluster | Hybrid: reserved bare metal for the base, AWS p6 or a vendor API for the tail (§6.5) |

llm-d ships exactly this pattern as an experimental guide: each model gets a
`ClusterQueue` with a guaranteed GPU floor in a shared cohort, *"so an idle
model's GPUs are lent to a busy one and reclaimed by preemption. Enforcement
happens below the HPA, so KEDA-generated HPAs work unmodified, but over-budget
demand shows up as `Pending` pods rather than a lowered `maxReplicas`"*
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md).
That last clause is the operational gotcha: **the autoscaler will keep asking
for replicas the cluster cannot give**, and the only signal is Pending pods.
Wire an alert on it.

---

## 4. Predictive scaling

### 4.1 Is the workload actually seasonal?

It has to be, or forecasting is noise-fitting. Evidence that LLM inference
traffic is diurnal and weekly:

- Azure's production LLM inference traces were captured **2023-11-11** from two
  services, *"coding and conversation"*, and *"are 20 minutes long and include
  the arrival time, input size (number of prompt tokens), and output size
  (number of output tokens)"* — enough to characterise burstiness, not enough to
  fit a seasonal model on
  [src](https://arxiv.org/html/2311.18677v2) (Splitwise),
  [src](https://github.com/Azure/AzurePublicDataset/blob/master/AzureLLMInferenceDataset2023.md)
  (dataset page: `TIMESTAMP`, `ContextTokens`, `GeneratedTokens`).
  ⚠️ **TO BE VERIFIED (corrected 2026-09-19)** — an earlier draft quoted these
  same two sources for *"the arrival times of inference requests had clear
  diurnal and weekly patterns"*. **That sentence is in neither source**: the
  dataset page makes no temporal-pattern claim at all, and the Splitwise text
  characterises the 20-minute traces without asserting diurnality. Treat the
  seasonality premise of this section as supported by **SageServe and AWS
  below**, and by your own 14 days of `vllm:generation_tokens_total` (open
  question 15) — not by the Azure traces.
- SageServe, built on Microsoft production data, is explicitly a *forecast-aware*
  autoscaler and reports *"up to 25% savings in GPU-hours"* and *"reduces GPU-hour
  wastage due to inefficient auto-scaling by 80%"* — which only makes sense
  against a predictable seasonal shape
  [src](https://arxiv.org/abs/2502.14617) (submitted 2025-02-20, v3 2025-11-12;
  published in *Proc. ACM Meas. Anal. Comput. Syst.* 9(3), Art. 61, Dec 2025).
- AWS built predictive scaling on the same assumption: *"A workload is a good
  fit for predictive scaling if it exhibits recurring load patterns that are
  specific to the day of the week or the time of day"*
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html).

**Decision rule.** Before enabling any forecaster, run it in observe-only mode
(Dynamo's `advisory: true`, AWS's *forecast only* mode) for **at least two full
weeks** and score it. AWS's own data requirements are the right order of
magnitude: *"at least 24 hours of data"* to start, but *"forecasts are more
effective if historical data spans two full weeks"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html).

### 4.2 Forecasting methods, ranked by what they buy you here

| Method | Handles | Cost to run | Where it is already implemented |
|---|---|---|---|
| **Cron / scheduled bounds** | known events, business hours | none | KEDA cron scaler [src](https://keda.sh/docs/2.20/scalers/); AIBrix scheduled replica bounds [src](https://aibrix.readthedocs.io/latest/features/autoscaling/metric-based-autoscaling.html) |
| **Holt-Winters / ARIMA** | trend + one or two seasonalities | seconds | Dynamo `load_predictor: arima` (the default for request count, ISL, OSL) [src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide) |
| **Kalman filter** | bursty traffic, fast level/trend tracking | seconds | Dynamo `load_predictor: kalman` (`kalman_q_level`, `kalman_q_trend`, `kalman_r`, `kalman_min_points`) [src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide) |
| **Prophet** | multiple seasonalities, holidays, changepoints | seconds–minutes | Dynamo `load_predictor: prophet` (`prophet_window_size` default 50 s, needs `load_predictor_warmup_trace`) [src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide) |
| **Quantile / probabilistic forecasts** | gives you the *margin*, not just the mean | minutes | not in any engine's autoscaler today; see §4.4 and the probabilistic-demand paper [src](https://arxiv.org/pdf/2506.14851) |
| **LSTM / Transformer forecasters** | long, complex dependencies | GPU time to train | research only for this use case ⚠️ — no primary source found for a production LLM-serving autoscaler using one as of 2026-09-19 |

**Recommendation and its rule.** Use the *simplest* model whose residual error
is below the safety margin you were going to carry anyway (§4.4). Concretely:
start at cron + reactive; move to ARIMA/Holt-Winters only if the residual at the
lead-time horizon is above ~15 %; reach for Prophet only when you have real
holiday/changepoint structure. The reason is not statistical purity — it is that
a forecaster you cannot debug at 03:00 will be disabled at 03:05.

**Important negative result to plan around**: forecast only the quantities that
are *predictable*. Dynamo deliberately does **not** predict KV hit rate or
speculative accept length, using "the latest valid observation" instead
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide).
Both are workload-mix properties that jump discontinuously when a client
changes prompt templates. Same logic applies to DSpark acceptance for
DeepSeek-V4.1-Flash — see the measured acceptance-rate spread in
[`../cross-cutting/serving-optimizations.md` §2.3](../cross-cutting/serving-optimizations.md).

### 4.3 What AWS predictive scaling teaches (and its one bad default)

Mechanism, verbatim
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html):

- needs ≥ 24 h of metric data; analyses up to **14 days**;
- produces an **hourly forecast for the next 48 h**, refreshed **every 6 h**;
- starts in **forecast only** mode, switched manually to **forecast and scale**;
- **"If the forecast expects a decrease in load, it will not scale in to remove
  capacity"** — removal is left to dynamic policies;
- `SchedulingBufferTime` launches instances *ahead* of the forecast hour: this
  is the **lead-time knob**;
- when several policies are active, *"each policy determines the desired
  capacity independently, and the desired capacity is set to the maximum of
  those"*;
- `MaxCapacityBreachBehavior` / `MaxCapacityBuffer` can raise the ceiling, with
  an explicit warning that the raised ceiling becomes permanent.

The three ideas to port to a bare-metal LLM fleet: **hourly granularity**
(matches replica boot times far better than a 15 s HPA loop), **never scale down
on a forecast**, and **compose by `max()`**. The one default *not* to port is
scaling exactly at the top of the hour; with a 5–10 min DeepSeek boot you need
the equivalent of `SchedulingBufferTime` ≥ that boot time, which §4.4 makes
precise.

### 4.4 Lead time, headroom burn, and the safety margin — the actual math

Define:

```
L         = lead time = time from "decide to add a replica" to "replica serving"
            = schedule + image pull + weight load + compile + warm-up          (§4.5)
C         = seats (concurrent sequences) one replica sustains at the SLO
ρ         = target occupancy (seats used ÷ seats available)
R         = demand ramp rate, seats/second
n         = current replica count
headroom  = n·C·(1 − ρ)         seats of slack the fleet is carrying
```

**Reactive scaling is sufficient iff the headroom outlives the lead time:**

```
headroom / R  ≥  L
```

Substituting a ramp that multiplies demand by `x` over a window `t`
(`R = n·C·ρ·(x−1)/t`) the replica count cancels — **headroom burn time is
scale-invariant**:

```
burn_time = t · (1 − ρ) / ( ρ · (x − 1) )
```

At ρ = 0.70, a **2× burst in 5 minutes burns the headroom in 2.1 min**; a 3×
burst in 5 min burns it in 1.1 min (`est.`, arithmetic above). Since no model in
this repo boots in 2 minutes (§4.5), **reactive scaling alone cannot hold the
SLO through a 2×-in-5-min burst at 70 % occupancy.** Two ways out, and the
trade-off between them is the whole of predictive scaling:

1. **Lower ρ** until `burn_time ≥ L`. Inverting: `ρ ≤ t / (t + L·(x−1))`. For
   L = 8 min, x = 2, t = 5 min → **ρ ≤ 0.385** — you must run at 38 %
   occupancy, i.e. carry 2.6× the steady-state fleet. On owned hardware that
   headroom is free if the idle GPUs run batch work (§7); on burst capacity it
   is paid in full.
2. **Pre-scale from a forecast**, so the replica is already warm when the burst
   arrives. Costs a forecast error margin instead of a constant 2.6×.

**Safety margin from forecast error.** If the forecast's relative error at
horizon `L` has standard deviation σ, and you accept an under-provision
probability ε, provision

```
replicas = ceil[ D̂(t + L) · (1 + z_{1−ε}·σ) / (C · ρ) ]
```

| σ (forecast error) | margin at p90 (z=1.28) | margin at p95 (z=1.64) |
|---:|---:|---:|
| 10 % | ×1.13 | ×1.16 |
| 20 % | ×1.26 | ×1.33 |
| 30 % | ×1.38 | ×1.49 |

(`est.`, normal-error assumption stated.) Read it as a go/no-go test: **if your
forecaster's σ is 30 %, its p95 margin (×1.49) costs more than the headroom you
were carrying anyway (1/0.7 = ×1.43) — do not deploy it.** A forecaster only
pays when σ is small enough that `1 + z·σ < 1/ρ_reactive`. A quantile forecaster
that emits its own p90 is strictly better than a point forecaster plus a guessed
σ, which is why the probabilistic-demand line of work matters
[src](https://arxiv.org/pdf/2506.14851).

**Combining reactive and predictive** — the `max()` rule, as AWS composes
policies [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html)
and as Dynamo composes its two loops (*"throughput updates the lower-bound
replicas, then load-based scaling can adjust above that floor"*)
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide):

```
replicas(t) = max(
    predictive_floor(t)   ,   # forecast at t+L, with margin; never scales down
    reactive(t)           ,   # queue/KV/backlog triggers
    min_replicas_for_HA       # §5.3 / §6.4
)
```

### 4.5 Lead time is mostly cold start — the measured evidence

| Measurement | Model / size | Result | Source |
|---|---|---|---|
| Safetensors loader vs Run:ai Model Streamer, GP3 SSD | Llama 3 8B, 15 GB | 47.99 s → **14.34 s** (concurrency 16) | [NVIDIA, 2025-09-16](https://developer.nvidia.com/blog/reducing-cold-start-latency-for-llm-inference-with-nvidia-runai-model-streamer) |
| same, IO2 SSD | same | 47 s → **7.53 s** (concurrency 8) | same |
| same, S3 | same | Streamer **4.88 s** (concurrency 32) vs Tensorizer 37.36 s | same |
| **vLLM engine ready**, GP3 / IO2 / S3 | same | 66.13 / 62.69 / — s (safetensors) → **35.08 / 28.28 / 23.18 s** (Streamer) | same |
| Total pod start, tuned S3 + compile cache | **67 GiB** model (Qwen3-6-35B-A3B), p5.48xlarge TP=2 | 82 s → 65 s → **16 s** on a subsequent pod (weights 29 s→12 s; `torch.compile` 53 s→4 s) | [AWS, 2026-09-01](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/) |
| same | **203 GiB** model (Llama-4-Scout-17B-16E), p5.48xlarge TP=4 | **457 s → 59 s → 32 s** (weights 423→25→26 s; `torch.compile` 34→34→6 s) | same |
| Cold-start breakdown | Qwen3.6-35B-A3B NVFP4, 1× DGX Spark, vLLM v0.22.1 | weights **142 s** + `torch.compile` **39 s** + profiling/warm-up **136 s** = **317 s**; with streaming + compile cache **167 s** | [Route179, 2026-06-30](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/) |

Three lessons. (a) **Weight loading is the part you can fix**, with a streaming
loader and a fat parallel read. (b) **`torch.compile` is the part you fix once**
— the compiled artefact "depends only on the model's structure — its layer
shapes, data types, and the target GPU — not on the actual weight values", so a
persistent `VLLM_CACHE_ROOT` on local NVMe turns 39–53 s into 4–10 s
[src](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/),
[src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/).
(c) **Profiling/warm-up (CUDA graph capture) is largely irreducible** — 136 s in
the one measured breakdown above.

**Estimated lead times for this repo** (⚠️ TO BE VERIFIED — no measurement
exists in this tree; method: checkpoint bytes from
[METHODOLOGY §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)
divided by an assumed **5–20 GB/s** sustained aggregate local-NVMe read, plus a
compile+warm-up term anchored on the 317 s / 167 s DGX Spark breakdown and the
457 s → 59 s AWS 203 GiB figure, scaled by GPU count):

| Model | Bytes to load | Weight stream @5–20 GB/s | Compile + warm-up (est.) | **Time-to-ready (est.)** |
|---|---:|---:|---:|---:|
| Marlin-2B (BF16, TP1) | 5.44 GB | 0.3–1.1 s | 60–150 s | **1–3 min** |
| Qwen3.8-27B (FP8, TP1) | 30.87 GB | 1.5–6.2 s | 90–210 s | **2–4 min** |
| DeepSeek-V4.1-Flash (TP4) | 510.29 GB + 188.83 GiB Engram → host RAM | 26–102 s | 3–8 min | **5–12 min** |
| DeepSeek-V4.1-Flash-NVFP4 (TP4) | 527.27 GB, same Engram | 26–105 s | 3–8 min | **5–12 min** |
| Kimi-K3 (TP8 + DCP8, whole node) | 1,560.9 GB | 78–312 s | 6–15 min | **8–20 min** |

These are the `L` values that go into §4.4. They are also the reason Kimi-K3
cannot be reactively autoscaled at all: `burn_time` at ρ=0.7 for a 2×-in-5-min
ramp is 2.1 min against an `L` of 8–20 min, a factor of 4–10 short.

**What to do about it**, in order of effort:

1. **Persistent compile cache on local NVMe** (`VLLM_CACHE_ROOT` on a hostPath
   or local PV). Measured **53 s → 4 s** (AWS, 67 GiB TP=2) and **34 s → 6 s**
   (AWS, 203 GiB TP=4)
   [src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/),
   [src](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/).
   Free; do it first. ⚠️ The Route179 post publishes only the **totals**
   (317 s → 167 s) and a 39 s uncached `torch.compile`, not a cached-compile
   figure — an earlier draft's "39 s → 10 s" is not in it.
2. **Streaming loader with tuned concurrency and chunk size** — AWS's tuning was
   **"4 GiB chunks matching shard size"** (one chunk per file, so the lowest
   possible concurrency setting), with the connection count **derived from the
   file count** — ⚠️ the specific figure "17 connections" is not on that page
   [src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/).
3. **Keep weights resident and sleep the engine instead of killing the pod.**
   vLLM sleep **level 1** offloads weights to CPU RAM and discards KV, releasing
   "up to 90%+ of GPU memory"; **level 2** discards weights too, keeping only
   buffers in CPU. Wake-up is selective: `wake_up(tags=["weights"])` then
   `wake_up(tags=["kv_cache"])`, and there is a `release_kv_cache_memory()` that
   drops only the KV pool. Online: `VLLM_SERVER_DEV_MODE=1 vllm serve …
   --enable-sleep-mode`, then `POST /sleep?level=1`, `POST /wake_up`
   [src](https://github.com/vllm-project/vllm/blob/main/docs/features/sleep_mode.md).
   Note level 1 needs enough host RAM to hold the weights — for DeepSeek that is
   510 GB of DRAM *on top of* the 188.83 GiB of Engram tables already offloaded
   there ([`../matrix/pairs.json`](../matrix/pairs.json)), which is the binding
   constraint on this trick for the big MoEs. ⚠️ TO BE VERIFIED — no published
   wake-up latency for a 510 GB level-1 sleep.
4. **Research-grade: scale without waiting for the weights at all.** BlitzScale
   breaks "the scaling abstraction for inference from a coarse-grained
   instance-level to a fine-grained layer-level", letting an overloaded instance
   offload layer computation to a scaling instance before its parameters have
   finished loading, using the GPU compute network for an `O(1)`-host-cache
   multicast; reported **94 % tail-latency reduction vs ServerlessLLM** and
   **49 % GPU-time savings vs non-autoscaling DistServe/vLLM** at equal service
   level (OSDI '25) [src](https://arxiv.org/abs/2412.17246). ServerlessLLM's
   multi-tier (GPU/DRAM/SSD) loading-optimised checkpoint format reports **up to
   8.2× faster initialisation** [src](https://arxiv.org/pdf/2401.14351). Neither
   is in vLLM/SGLang/TRT-LLM/Dynamo as a shipped feature as of 2026-09-19 ⚠️.

### 4.6 Scheduling pre-warm before known events

The cheapest predictive scaling is a calendar. Two implementations already
exist: KEDA's cron scaler, which "can maintain a specified replica range during
scheduled periods… prevent workloads from scaling below a required capacity
during seasonal peaks or business-critical processing windows"
[src](https://keda.sh/docs/2.20/scalers/), and AIBrix's scheduled replica bounds
with timezone and day-of-week support
[src](https://aibrix.readthedocs.io/latest/features/autoscaling/metric-based-autoscaling.html).

The rule for setting the cron **start** time: `event_start − L − margin`, with
`L` from §4.5. For DeepSeek-V4.1-Flash that is **20 minutes early** (12 min L +
8 min margin for image pull and scheduling variance); for Kimi-K3, **40
minutes**. Anything less and the replica is still capturing CUDA graphs when the
traffic arrives.

### 4.7 Building the forecast yourself (the bare-metal-friendly route)

Since KEDA's only documented predictive option is a SaaS
[src](https://keda.sh/docs/2.20/scalers/), the practical pattern on a private
cluster is:

1. A CronJob reads 14 days of `sum(rate(vllm:generation_tokens_total[5m]))` (and
   the prompt-token equivalent) from Prometheus.
2. It fits Holt-Winters/ARIMA/Prophet **per model**, and emits the p90 forecast
   at horizon `L` as a pushed metric, e.g.
   `fleet:forecast_seats:p90{model="…",horizon="15m"}`.
3. A second KEDA trigger scales on that series with a threshold of
   `C × ρ` — so `desiredReplicas = ceil(forecast_seats / (C·ρ))` falls out of
   HPA's own formula.
4. KEDA's `advanced.scalingModifiers.formula` composes it with the reactive
   triggers by `max()` [src](https://keda.sh/docs/2.20/reference/scaledobject-spec/).

This keeps all data on-cluster, keeps the forecaster debuggable in a notebook,
and reuses the HPA control law rather than replacing it.

### 4.8 Where the literature actually is, 2026-09-19

| Work | Date / venue | Contribution relevant here | Headline (authors' own numbers) |
|---|---|---|---|
| **Llumnix** [src](https://www.usenix.org/conference/osdi24/presentation/sun-biao) | OSDI '24 | live **migration** of requests + in-memory state across instances — the mechanism that makes drain-free scale-down possible | tail latency improved "by an order of magnitude"; up to **1.5×** for high-priority requests; **36 %** cost saving at similar tail latency |
| **ServerlessLLM** [src](https://arxiv.org/pdf/2401.14351) | 2024 | loading-optimised checkpoint format over GPU/DRAM/SSD tiers | up to **8.2×** faster initialisation |
| **BlitzScale** [src](https://arxiv.org/abs/2412.17246) | OSDI '25 | layer-level live scaling, `O(1)` host caching, network multicast of parameters | **94 %** lower tail latency vs ServerlessLLM; **49 %** GPU-time saving vs non-autoscaling |
| **Chiron** [src](https://arxiv.org/abs/2501.08090) | 2025-01-14 | hierarchical (request-level + instance-level) autoscaling using queue size, utilisation **and SLOs**; "previous autoscalers for LLM serving do not consider request SLOs leading to unnecessary scaling and resource under-utilization" | up to **90 %** higher SLO attainment, **70 %** better GPU efficiency |
| **SageServe** [src](https://arxiv.org/abs/2502.14617) | POMACS 9(3), Dec 2025 | traffic forecast + ILP co-optimising routing and long-lead-time VM/model placement | **25 %** GPU-hour saving; **80 %** less autoscaling waste |
| **AIBrix** [src](https://arxiv.org/abs/2504.03648) | 2025-02-22 | LLM-specific autoscalers + SLO-driven GPU optimizer across heterogeneous GPUs | system-level: **+50 %** throughput, **−70 %** latency |
| **OpScale** [src](https://arxiv.org/abs/2608.13499) | 2026-08-13 | **operator-level** provisioning — scaling below the whole-model granularity; evaluated on up to 40 A100s and 24 GB200s with production traces | SLOs at **36.3 %** fewer GPUs, **28 %** less power; or **+44 %** throughput at fixed budget |
| **inference-fleet-sim** [src](https://arxiv.org/html/2603.16054v1) | 2026 (arXiv 2603.16054) | M/G/c + Kimura two-moment approximation + discrete-event simulation for fleet sizing against a P99 TTFT SLO | Kimura over-predicts P99 by **8–14 %** vs simulation on chatbot traces (conservative); Erlang-C **under**-estimates the tail on high-variance agent traces |
| **KV-constrained stability** [src](https://arxiv.org/html/2605.04595) | 2026-05-06 | the stability condition for LLM serving **with** a KV memory constraint (§6.2) | within **10 %** prediction error on A100 |

⚠️ These are all authors' own numbers on their own harnesses; none is
independently reproduced here, and none was run on B300.

---

## 5. Scale-down: draining, thrash, and when zero is legal

### 5.1 Draining is the hard part, not the decision

A decode replica holding 128 in-flight DeepSeek sequences at 512 output tokens
has a **worst-case drain time of `OSL × TPOT` ≈ 512 × 23.31 ms ≈ 11.9 s** for
S1, and at S3 (128K in / 2K out) roughly `2000 × TPOT` ≈ 47 s, using the
[`../matrix/pairs.json`](../matrix/pairs.json) B300 operating points. For
Kimi-K3 at 1M context it is minutes. So:

```yaml
terminationGracePeriodSeconds: 300     # ≥ worst-case remaining generation
lifecycle:
  preStop:
    exec:
      command: ["/bin/sh","-c","sleep 15"]   # let endpoint removal propagate first
```

The `preStop` sleep exists because Kubernetes removes the pod from Endpoints and
sends SIGTERM **concurrently**; without the pause the router can still send a
request to a pod that is already shutting down. ⚠️ TO BE VERIFIED — no vLLM,
SGLang or TensorRT-LLM doc found that specifies graceful-shutdown semantics
(whether SIGTERM finishes in-flight requests or aborts them) as of 2026-09-19;
this must be tested per engine version before trusting any scale-down policy.

Three known behaviours that are documented:

- **Dynamo's planner does not drain**: *"When the Planner removes a worker, it
  terminates it without draining in-flight requests"*, with the documented
  mitigations being a higher `min_endpoint` and a lower
  `load_scaling_down_sensitivity`
  [src](https://docs.nvidia.com/dynamo/kubernetes/auto-deployment/dynamo-planner).
  If you run the planner, you are choosing dropped requests on every scale-down
  unless you put your own drain in front of it.
- **llm-d's EPP flow control** buffers requests centrally when the pool is
  saturated, "respecting priority and fairness policies", which is what makes
  scale-from-zero survivable — a queued request raises the queue metric, KEDA
  activates the Deployment, and EPP dispatches once a server is Ready
  [src](https://llm-d.ai/docs/architecture/advanced/autoscaling),
  [src](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/).
- **Llumnix** solves this properly by *migrating* requests and their in-memory
  state between instances instead of waiting or dropping
  [src](https://www.usenix.org/conference/osdi24/presentation/sun-biao) — the
  right long-term answer, not yet a feature of the engines in
  [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) ⚠️.

### 5.2 KV-cache-aware scale-down

Removing a replica throws away its prefix cache. For a workload with the
cache-hit rates in
[`../cross-cutting/serving-optimizations.md` §1.2](../cross-cutting/serving-optimizations.md),
that is a direct TTFT regression on the next requests that would have hit. Two
mitigations:

1. **Evict the coldest replica, not the newest.** Rank candidates by
   `vllm:prefix_cache_hits / vllm:prefix_cache_queries` (or
   `sglang:cache_hit_rate`, or `trtllm_kv_cache_hit_rate`) and scale down the
   one whose cache is doing least work. Kubernetes cannot express this in an
   HPA; it needs a controller or a `controller.kubernetes.io/pod-deletion-cost`
   annotation written by a sidecar. ⚠️ TO BE VERIFIED — no published
   implementation of pod-deletion-cost driven by a cache-hit metric was found.
2. **Make the cache survive the replica.** With a shared KV tier — LMCache as
   "a KV cache management layer… KV cache from a temporary state into reusable
   AI-native knowledge that can be stored persistently, reused across multiple
   serving engines"
   [src](https://github.com/LMCache/LMCache/blob/dev/README.md) — the scaled-down
   replica's blocks stay in host DRAM/NVMe/remote store and the *replacement*
   replica warms from them. Dynamo's KVBM documents the same three offload
   backends (LMCache, HiCache, FlexKV) over host and persistent tiers
   ([`../cross-cutting/inference-engines.md` §2.4](../cross-cutting/inference-engines.md)).
   This changes scale-down from "throw away the cache" to "demote the cache",
   and is the single highest-value thing to build before enabling aggressive
   scale-down on DeepSeek or Kimi.

### 5.3 Cooldowns and anti-thrash, with numbers

| Knob | Default | Set to (this repo) | Why |
|---|---:|---:|---|
| HPA `scaleDown.stabilizationWindowSeconds` | 300 s [src](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) | **1800 s** | re-adding a DeepSeek replica costs 5–12 min (§4.5); 30 min of stability is cheaper than a wrong removal |
| HPA `scaleUp` policy period | — | **≥ L** (300 s for DeepSeek) | prevents cascading scale-up on pending replicas [src](https://github.com/vllm-project/aibrix/issues/2670) |
| KEDA `cooldownPeriod` (to zero) | 300 s [src](https://keda.sh/docs/2.20/reference/scaledobject-spec/) | 900 s (Marlin/Qwen), N/A elsewhere | see §5.4 |
| Ray `downscale_delay_s` | 600 s [src](https://docs.ray.io/en/latest/serve/advanced-guides/advanced-autoscaling.html) | keep | already conservative |
| AIBrix `scale-down-tolerance` | 0.1 [src](https://aibrix.readthedocs.io/latest/features/autoscaling/metric-based-autoscaling.html) | 0.2–0.3 | the "fluctuation parameter" that stops oscillation |
| Knative `max-scale-down-rate` | 2.0 [src](https://knative.dev/docs/serving/autoscaling/kpa-specific/) | ≤ 1.25 | halving a GPU fleet in one tick is never right |

**The asymmetry rule:** scale up fast and in small steps; scale down slowly and
in single replicas. The cost of a late scale-up is an SLO violation lasting `L`;
the cost of a late scale-down is idle GPU-hours — and on owned bare metal, the
latter is zero if §7's queues are in place.

### 5.4 Scale-to-zero economics, per model

Scale-to-zero is worth it when `idle_cost_saved > cold_start_penalty ×
requests_affected`. On **owned** bare metal the left side is zero unless the GPU
goes to another workload, so the real question is whether the *node* can be
released to a batch queue. Per model:

| Model | Min replicas | Scale to zero? | Reasoning |
|---|---:|---|---|
| **Marlin-2B** | 0–1 | **Yes** | 1 GPU, 1–3 min cold start (est.), no vendor API to fall back to; at $0.022/1M output tokens on B300 ([`../matrix/pairs.json`](../matrix/pairs.json)) an idle GPU costs more than the whole workload. Gate behind EPP flow control so the first request queues rather than fails [src](https://llm-d.ai/docs/architecture/advanced/autoscaling) |
| **Qwen3.8-27B** | 0–1 | **Yes, with a cron floor** | 1 GPU, 2–4 min cold start (est.). Keep ≥1 during business hours via a KEDA cron trigger; allow zero overnight |
| **DeepSeek-V4.1-Flash** | **2** | **No** | 5–12 min cold start (est.) on a primary interactive model; and min 2 gives N+1 against a single replica loss (§6.4) |
| **DeepSeek-V4.1-Flash-NVFP4** | 1–2 | **No** | same cold start; treat as a variant of the above, not an independent pool |
| **Kimi-K3** | **1 node** | **Never** | 8–20 min cold start (est.) and a whole 8×B300 node per replica; the fleet cannot absorb a re-load. Front it with a queue and accept the wait instead |

A subtlety for the two "yes" rows: **scale-to-zero with KEDA does not need the
`HPAScaleToZero` feature gate**, because KEDA activates the Deployment itself
before handing over to the HPA
[src](https://llm-d.ai/docs/architecture/advanced/autoscaling). And the 0→1
transition is governed by `pollingInterval` (default 30 s), not the HPA sync
period [src](https://keda.sh/docs/2.20/reference/scaledobject-spec/) — so the
worst-case first-request latency is `pollingInterval + L`, i.e. **90 s–3.5 min
for Marlin-2B**. That number, not the GPU saving, is what to show the product
owner before enabling it.

---

## 6. Capacity planning

### 6.1 Little's Law is the whole of the demand side

```
concurrent_seats = arrival_rate (req/s) × mean_residency (s)
mean_residency   = TTFT + OSL × TPOT            (streaming request)
replicas         = ceil( concurrent_seats / (C · ρ) )
```

Worked, for DeepSeek-V4.1-Flash at the B300 interactive point (TP4, C = 128
seats, TTFT 129 ms, TPOT 23.31 ms, [`../matrix/pairs.json`](../matrix/pairs.json)),
scenario S1 (4K in / 512 out):

```
mean_residency = 0.129 + 512 × 0.02331 = 12.06 s
req/s per replica at full occupancy = 128 / 12.06 = 10.61 req/s
req/s per replica at ρ = 0.70        =  7.43 req/s
```

So **one TP4 DeepSeek replica on B300 serves ≈ 7.4 interactive requests/s at a
70 % occupancy target** (`est.`, from pinned pair numbers). That single figure
converts any product-side "requests per second" target into GPUs. Note it is
*residency*-driven: doubling OSL halves the request rate at the same seat count,
which is why capacity plans must be stated per scenario (S1/S2/S3 from
[METHODOLOGY §6](../METHODOLOGY.md#6-cost)), never as a bare RPS.

### 6.2 Two stability conditions you cannot violate

**Compute/bandwidth stability (classical).** `ρ = λ/(c·μ) < 1`, and tail latency
blows up well before 1 — the fleet simulator caps its analytical sweep at
**0.85 utilisation** before verifying with discrete-event simulation
[src](https://arxiv.org/html/2603.16054v1).

**Memory stability (LLM-specific).** With a KV-cache constraint the condition
tightens to

```
λ < μ · (1 − δ)        where δ = (max single-request memory) / (total KV capacity)
μ = M / ( b̄ · E[g(s,o)] )
g(s,o) = [ (1 + s/ŝ)·s + 2·o·s + (1+o)·o ] / 2
```

with `M` = KV memory in tokens, `b̄` = average batch step time, `s` = prompt
tokens, `o` = output tokens, `ŝ` = chunked-prefill chunk size; Theorem 4.1 says
that above `μ` *no* scheduling policy keeps the queue finite, and Theorem 4.2
gives stability under work-conserving scheduling below `μ(1−δ)`; validated
within 10 % on A100 [src](https://arxiv.org/html/2605.04595) (arXiv 2605.04595,
2026-05-06).

The practical reading of `δ`: **one very long request can destabilise a replica
that is stable on average.** For Kimi-K3 at 1M context, a single sequence's KDA
state alone is 2.25 GB (`S=5` slots — [METHODOLOGY §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)),
and `max_concurrency_8k` is only 101
([`../matrix/pairs.json`](../matrix/pairs.json)), so `δ` is not small. Capacity
plans for Kimi-K3 must be built on the 128K/1M concurrency rows, not the 8K one.

### 6.3 Queueing: Erlang-C, M/G/c, and where each breaks

The reference implementation to copy is inference-fleet-sim's two-phase method
[src](https://arxiv.org/html/2603.16054v1):

1. **Analytical sweep** — M/G/c with **Kimura's two-moment approximation**, with
   `P99 queue wait ≈ [Erlang-C term] × (1 + C_s²)/2 × ln(100)` where `C_s²` is
   the squared coefficient of variation of service time — to enumerate
   candidate fleet shapes meeting the P99 TTFT SLO under a **0.85 utilisation
   cap**.
2. **Discrete-event simulation** of the top candidates against 10,000+ request
   traces, because the analytical layer is only trustworthy in the low-variance
   regime.

Its own validation is the honest part, and the reason not to stop at step 1:
*"For chatbot workloads, Kimura over-predicts P99 by 8–14 % versus simulation
(conservative). For agent workloads with high variance, Erlang-C
under-estimates tail latency; DES provides authoritative results"*
[src](https://arxiv.org/html/2603.16054v1).

**Decision rule.** Use Erlang-C/M/G/c for the *first cut* and for
sensitivity analysis — it is a spreadsheet, and it is conservative on chat. Do
**not** use it to size an agentic workload (the DeepSeek-V4.1-Flash coding case
in [`../cross-cutting/serving-optimizations.md` §7.1](../cross-cutting/serving-optimizations.md)
or Kimi-K3's 1M sessions in §7.2), where output-length variance is high and it
under-estimates the tail; simulate or measure instead.

Service-time variance is also *structural* in LLM serving in a way M/M/c does
not capture: a request's service time depends on the batch it lands in, and the
batch composition depends on the scheduler. The queueing-and-LLMs survey frames
this as an open problem rather than a solved model
[src](https://arxiv.org/pdf/2503.07545).

### 6.4 Headroom and N+1

| Target | Value | Rule |
|---|---:|---|
| Steady-state occupancy ρ | **0.65–0.75** interactive, **0.85–0.95** batch | below the 0.85 analytical cap [src](https://arxiv.org/html/2603.16054v1); lower for interactive because tail latency is the product |
| Replica-level N+1 | `min_replicas ≥ 2` for any model with an SLO | one replica loss must not be a capacity event |
| Node-level N+1 | `+1 node` per 8 serving nodes, minimum 1 | for Kimi-K3 (1 replica = 1 node) this means **2 nodes to run 1 replica safely** |
| Peak/avg ratio | measure it; plan for the measured p99 of hourly demand | §8's worked example uses **10×**, the case the task specifies |

The N+1 cost for Kimi-K3 is the uncomfortable number in this repo: a spare
8×B300 node at the `b300 · low` planning price of **$7.40/GPU-h**
([`../cross-cutting/cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md))
is **$59.20/h = $1,420.80/day** of pure insurance. The alternative is to accept
that a Kimi-K3 node failure is an 8–20 min outage (the reload time from §4.5)
and to fail over to the Moonshot API at $15.00/1M output tokens
([`../matrix/cost-matrix.md` §6.1](../matrix/cost-matrix.md)) — cheaper than a
hot spare unless you are serving more than ~95 M output tokens/day through that
node. (`est.`: $1,420.80 ÷ $15.00 per 1M = 94.7 M tokens.)

### 6.5 Hybrid: reserved bare metal + cloud burst, and the cross-over

Prices, cited by name from
[`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md):
`b300 · low` **$7.40** (Hyperstack), `b300 · high` **$15.00** (OCI
`BM.GPU.B300.8`), `b300 · res1y` **$7.94** (DigitalOcean 12-mo); AWS
`p6-b300.48xlarge` on-demand **$17.802**/GPU-h, spot **$5.591**, Capacity Block
**$14.04**, with **no standard RIs offered on p6-b300**.

A GPU you own or reserve is paid 24/7; a burst GPU is paid only when used.
Owning beats bursting when the duty cycle exceeds the price ratio:

```
duty_cycle_breakeven = C_reserved / C_burst
```

| Burst option | Price/GPU-h | Break-even duty cycle vs `res1y` $7.94 |
|---|---:|---:|
| AWS p6-b300 on-demand | $17.802 | **44.6 %** |
| AWS p6-b300 Capacity Block | $14.04 | **56.6 %** |
| AWS p6-b300 **spot** | $5.591 | **142 %** — spot is always cheaper *if* you can survive interruption |

(`est.`, arithmetic on the cited prices.) Read: **any capacity you will use more
than ~45 % of the time should be reserved, not bursted.** And spot is cheaper
than reserved at *any* duty cycle — the entire question for spot is whether a
2-minute interruption notice is survivable for a replica with a 5–12 min
reload. For DeepSeek and Kimi it is not; for Marlin-2B it plausibly is. ⚠️ TO BE
VERIFIED — p6-b300 spot availability and interruption rates are not published;
the $5.591 figure is a Vantage-reported spot price, not a capacity guarantee.

The third burst option is **not renting GPUs at all** but overflowing to the
vendor API: DeepSeek at $0.60/1M output tokens, Moonshot at $15.00, Qwen Cloud
at $3.00 ([`../matrix/cost-matrix.md` §6.1](../matrix/cost-matrix.md)). Against
our own B300 costs from [`../matrix/pairs.json`](../matrix/pairs.json)
($1.4973/1M interactive for DeepSeek at `low`, $7.3926 for Kimi-K3) the API is
**more** expensive only for **Kimi-K3** ($15.00 vs $7.3926, 2.0×). For
**DeepSeek-V4.1-Flash the API is 2.5× *cheaper* than self-hosting** ($0.60 vs
$1.4973/1M output), which matches
[`../matrix/cost-matrix.md` §6.2](../matrix/cost-matrix.md): DeepSeek's B300
break-even utilisation is **232 % at the low tier** (i.e. unreachable), and its
only sub-100 % cell in the whole matrix is B200 at 92 %. So DeepSeek API
overflow is a *cost-saving* tool as well as a latency one; Kimi API overflow is
a *latency/availability* tool bought at a 2.0× premium. Qwen3.8-27B is the
clearest self-host win — our $0.1649 beats $3.00 by 18× (break-even 6 %).
*(Corrected 2026-09-19: the earlier text claimed the API was more expensive for
DeepSeek too, contradicting both `pairs.json` and cost-matrix §6.2.)*

---

## 7. Multi-model fleet scheduling

### 7.1 Bin-packing models onto nodes

The packing constraint is per-node GPU count and the replica's GPU granularity
(§1.2). On one 8×B300 node the feasible packings include:

| Packing | GPUs used | Fits? |
|---|---:|---|
| 1× Kimi-K3 (TP8+DCP8) | 8 | yes — and nothing else fits |
| 2× DeepSeek-V4.1-Flash (TP4) | 8 | yes |
| 1× DeepSeek (TP4) + 4× Qwen3.8-27B (TP1) | 8 | yes |
| 4× DeepSeek (TP2, Engram CPU-offload) | 8 | yes — the max-throughput shape in [`../matrix/pairs.json`](../matrix/pairs.json) |
| 8× Marlin-2B or 8× Qwen3.8-27B (TP1) | 8 | yes |

Two hard rules that fall out. **(a) A TP4 or TP8 replica must land on GPUs
inside one NVLink domain** — crossing to InfiniBand for TP is the "20–40 % comm
overhead" case in [METHODOLOGY §4](../METHODOLOGY.md#4-throughput-and-latency-roofline),
and the DeepSeek H100 row in `pairs.json` says it explicitly ("16/32 GPUs fit but
cross InfiniBand for no gain; use DP2×TP8 replicas instead of TP16").
**(b) Fragmentation is the enemy**: three Qwen replicas scattered across three
nodes can make a Kimi-K3 replica unschedulable even when 8 GPUs are free in
aggregate. Bin-pack single-GPU models tightly and reserve whole nodes for the
8-GPU model.

Schedulers that implement this: KAI Scheduler offers "Bin Packing & Spread
Scheduling: optimize node usage either by minimizing fragmentation (bin-packing)
or increasing resiliency and load balancing", plus **Workload Consolidation** to
"reallocate running workloads intelligently to reduce fragmentation", hierarchical
queues, DRF fairness and Topology-Aware Scheduling (v0.10.0, 2025-10)
[src](https://github.com/kai-scheduler/KAI-scheduler/blob/main/README.md).
Volcano offers gang scheduling plus network-topology-aware scheduling — the
cited page documents the `HyperNode` CRD, hard/soft topology constraints and
UFM / RoCE / label-based auto-discovery
[src](https://volcano.sh/en/docs/network_topology_aware_scheduling/).
⚠️ **TO BE VERIFIED (corrected 2026-09-19)** — an earlier draft also credited
**v1.14.0** with subgroup-level gang scheduling via a `partitionPolicy` field on
a VolcanoJob. **That page contains neither the version claim nor the field**
(its own Helm example pins 1.12.0). *Close by:* read the Volcano v1.14.0 release
notes and the `VolcanoJob` CRD reference before relying on `partitionPolicy`.

### 7.2 Gang scheduling of multi-GPU replicas

A TP8 Kimi-K3 replica is a gang: 8 pods (or one pod with 8 GPUs) that are
useless unless all are placed. Partial placement is worse than none — *"if only
a subset of the required pods are scheduled, the model is unusable, resources
remain idle, and the system can deadlock waiting for the remaining pods"*
[src](https://github.com/NVIDIA/grove/blob/main/README.md).

**Grove** is the API built for this shape: `PodClique` (a role — leader, worker,
frontend), `PodCliqueScalingGroup` (cliques that "scale and are scheduled
together as a gang… ideal for tightly coupled roles like prefill leader and
worker"), `PodCliqueSet` (the top-level object, with topology-aware spread of
replicas for availability), and `PodGang` (the scheduler-facing gang API). It
also does explicit **startup ordering**, which matters for MPI-style launches
[src](https://github.com/NVIDIA/grove/blob/main/README.md). KAI Scheduler
integrates with Grove and Dynamo for exactly the disaggregated-serving case
[src](https://github.com/kai-scheduler/KAI-scheduler/blob/main/README.md).

The deployment rule for this repo: **any replica larger than 1 GPU goes through
a gang-scheduling path** (Grove `PodCliqueScalingGroup`, Volcano PodGroup with
`minAvailable`, or KAI PodGroup). A plain Deployment with 8 replicas and a GPU
request is not a TP8 replica; it is 8 unrelated pods that will interleave with
other tenants' pods and deadlock the cluster.

### 7.3 Queues, quotas, and preempting batch to serve peak

This is where the bare-metal cost saving actually lives (§0.2). The shape:

- One `ClusterQueue` per model (or per team), all in one **cohort**, each with a
  nominal GPU quota that is its guaranteed floor.
- A low-priority `ClusterQueue` for batch/training/eval that is allowed to
  **borrow** the unused quota of the serving queues.
- Serving workloads **reclaim** their quota by preempting the borrower.

Kueue implements all three of `withinClusterQueue`, `reclaimWithinCohort` and
`borrowWithinCohort` preemption policies, with a classic greedy algorithm and an
optional **Fair Sharing** mode where "ClusterQueues with pending workloads
preempt others until obtaining equal or weighted resource shares", using the
`LessThanOrEqualToFinalShare` / `LessThanInitialShare` strategies
[src](https://kueue.sigs.k8s.io/docs/concepts/preemption/). llm-d ships this as
its Kueue-Based Replica Rebalancing guide, precisely so "an idle model's GPUs
are lent to a busy one and reclaimed by preemption"
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md).

Gang-aware preemption matters here: preemption must be evaluated at gang
granularity on both sides, or you evict 3 of 8 pods of a training job and free
nothing usable. Volcano's gang-aware reclamation does this, "preferring surplus
replicas to avoid per-Pod random eviction"
[src](https://volcano.sh/en/docs/); KAI separates workload **priority** from
**preemptibility** as two independent policies and adds a **min-guaranteed-runtime**
that "ensures a time period in which the scheduler must not preempt or reclaim a
running workload, even if preemptible"
[src](https://github.com/kai-scheduler/KAI-scheduler/blob/main/README.md) — the
knob that stops a 10-minute training step from being killed at minute 9 every
time the diurnal peak arrives.

**Decision rule for which scheduler.** Kueue if you mainly need quota and
admission and your jobs are Kubernetes-native (JobSet, RayJob); Volcano or KAI
if you need gang + topology + consolidation as first-class scheduler behaviour;
Grove on top when the *unit* is a multi-role disaggregated serving system rather
than a job. They compose: Kueue admits, the gang scheduler places.

### 7.4 Routing is half of scaling

Adding replicas does nothing if the router sends the next request to the busiest
one. The Gateway API Inference Extension defines an `InferencePool` (a selector,
target ports, and an `endpointPickerRef`) whose Endpoint Picker "examines live
pod metrics (queue lengths, memory usage, loaded adapters) to choose the ideal
pod", and a flow-control layer whose **Saturation Detector** "acts as the
gatekeeper for the gateway's centralized queues… when the pool is saturated,
the gateway pauses dispatching and safely buffers incoming requests in memory
(respecting priority and fairness policies) until capacity frees up"
[src](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/),
[src](https://kubernetes.io/blog/2025/06/05/introducing-gateway-api-inference-extension/).
`InferenceObjective` carries a `priority` used when flow control queues under
load.

Two scaling consequences: (a) the gateway's queue is the **leading signal**
KEDA should scale on (llm-d's `llm_d_epp_flow_control_queue_size`, §2.2), and
(b) central buffering is what makes **scale-from-zero** possible at all — the
first request waits instead of failing
[src](https://llm-d.ai/docs/architecture/advanced/autoscaling).

---

## 8. Worked example: a scaling policy for this repo

Assumptions, stated so they can be replaced: interactive traffic follows a
diurnal curve with a **10× peak/trough ratio**; the DeepSeek-V4.1-Flash peak is
**1,024 concurrent interactive sessions** (S1: 4K in / 512 out); target
occupancy **ρ = 0.70**; capacity per TP4 B300 replica **C = 128 seats** at
TPOT 23.31 ms / TTFT 129 ms, from
[`../matrix/pairs.json`](../matrix/pairs.json). All arithmetic below is `est.`
from those inputs.

### 8.1 Per-model policy table

| Model | Replica | `L` (est., §4.5) | Scale-up signal + threshold | Scale-down signal | min / max replicas | Predictive? |
|---|---|---:|---|---|---:|---|
| **Marlin-2B** | TP1, 1 GPU | 1–3 min | `vllm:num_requests_waiting > 0` for 60 s, **or** `kv_cache_usage_perc > 0.75` | `num_requests_running` < 10 % of `max_num_seqs` for 15 min | **0** / 8 | cron floor only |
| **Qwen3.8-27B** | TP1, 1 GPU | 2–4 min | backlog-seconds > 1.0 **or** `kv_cache_usage_perc > 0.8` | as above, 20 min window | **0** (night) / **1** (business hours, cron) / 8 | cron floor |
| **DeepSeek-V4.1-Flash** | TP4, 4 GPUs | 5–12 min | `llm_d_epp_inflight_tokens / V_P > 1.5 s` **or** `kv_cache_usage_perc > 0.8` | both below 50 % of threshold for 30 min | **2** / 12 | **yes** — p90 forecast at 15 min horizon |
| **DeepSeek-…-NVFP4** | TP4, 4 GPUs | 5–12 min | same | same | 0 / 4 (variant pool) | follows the base pool |
| **Kimi-K3** | TP8+DCP8, 1 node | 8–20 min | **none — no reactive autoscaling.** Queue depth drives *admission*, not replicas | manual / scheduled | **1** node (+1 spare, §6.4) | **scheduled only** |

The `1.5 s` prefill-backlog threshold is llm-d's own default for the co-located
topology (`max(ceil(backlog_s / 1.5), ceil(kv / 0.8))`)
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-token-aware/README.md);
it should be re-derived from your TTFT SLO as *"seconds of queue budget"* — if
the S1 TTFT SLO is 2 s and measured prefill is 129 ms, 1.5 s of backlog is
already most of the budget, so a tighter 0.8 s is defensible. ⚠️ Calibrate `V_P`
on B300 TP4 before deploying (§3.2).

### 8.2 Capacity model for a 10× diurnal day — DeepSeek-V4.1-Flash

`replicas(h) = ceil( demand(h) / (128 × 0.70) )`, 4 GPUs per replica:

| Hour | Sessions | Replicas | GPUs | Hour | Sessions | Replicas | GPUs |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 00 | 225 | 3 | 12 | 12 | 819 | 10 | 40 |
| 01 | 154 | 2 | 8 | 13 | 850 | 10 | 40 |
| 02 | 113 | 2 | 8 | 14 | 881 | 10 | 40 |
| 03 | 102 | 2 | 8 | 15 | 901 | 11 | 44 |
| 04 | 102 | 2 | 8 | 16 | 922 | 11 | 44 |
| 05 | 123 | 2 | 8 | 17 | 942 | 11 | 44 |
| 06 | 184 | 3 | 12 | 18 | 983 | 11 | 44 |
| 07 | 307 | 4 | 16 | 19 | **1,024** | **12** | **48** |
| 08 | 492 | 6 | 24 | 20 | 1,004 | 12 | 48 |
| 09 | 676 | 8 | 32 | 21 | 922 | 11 | 44 |
| 10 | 799 | 9 | 36 | 22 | 635 | 8 | 32 |
| 11 | 840 | 10 | 40 | 23 | 358 | 4 | 16 |

| Quantity | Value |
|---|---:|
| Peak replicas / GPUs | 12 / **48** (6 nodes' worth of GPUs, but only if packed 2 TP4 per node) |
| Trough replicas / GPUs | 2 / **8** |
| GPU-hours if autoscaled | **696** |
| GPU-hours if statically provisioned at peak | **1,152** |
| Elastic saving | **39.6 %** of GPU-hours |
| At `b300 · low` $7.40/GPU-h | $5,150/day autoscaled vs **$8,525/day** static |

**The bare-metal caveat, restated:** those 456 saved GPU-hours are only money if
the GPUs go to something else. On an owned cluster the correct reading of the
table is *"456 GPU-hours/day are available to the batch queue"* (§7.3), worth
$3,374/day at the same price — but realised only through Kueue/Volcano
preemption, not through the autoscaler.

### 8.3 Reactive-only vs reactive+predictive, on this curve

Peak hourly ramp is hour 08→09: +184 sessions/h = **3.07 sessions/min**. One
replica covers 89.6 seats at ρ=0.70, so the curve demands **one new replica
every 29 minutes** at its steepest. With `L` = 5–12 min, reactive scaling is
comfortably fast enough for the *diurnal* ramp — the forecaster buys nothing
there.

Where it does buy something is the **burst** case of §4.4: a 2× step in 5
minutes burns the ρ=0.70 headroom in 2.1 min, against `L` ≥ 5 min. So:

| Case | Reactive enough? | Policy |
|---|---|---|
| Diurnal ramp (≤ 3 sessions/min) | **Yes** | reactive triggers, ρ = 0.70 |
| Known event (launch, campaign, batch job kickoff) | No | **KEDA cron floor**, starting `event − L − 8 min` (§4.6) |
| Unknown 2×-in-5-min burst | No | either ρ ≤ 0.385 (2.6× over-provision) **or** p90 forecast + `max()` composition (§4.4) |

Given a measured forecast error σ, the forecaster is worth deploying only if
`1 + z₀.₉·σ < 1/0.70 = 1.43`, i.e. **σ < 33 %** at p90 (`est.`, §4.4 table). Run
Dynamo's `advisory: true` or an offline backtest for two weeks and check that
before wiring it to replicas.

### 8.4 Kimi-K3 is a different problem

One replica = one node = 100 % capacity granularity, `L` = 8–20 min,
`max_concurrency_8k` = 101 and `max_concurrency_128k` = 64
([`../matrix/pairs.json`](../matrix/pairs.json)). Autoscaling is not the tool.
The policy is:

1. **Fixed 1 node**, plus the N+1 decision from §6.4 (hot spare at
   $1,420.80/day, or accept an 8–20 min outage and fail over to the Moonshot API
   at $15.00/1M output).
2. **Admission control, not replica control**: the gateway's flow control queues
   and prioritises ([§7.4](#74-routing-is-half-of-scaling)); `InferenceObjective.priority`
   decides who waits.
3. **Scheduled second node** for known heavy windows, started 40 min early.
4. Overflow beyond that goes to the vendor API, which at $15.00/1M output is
   **2.0× our `b300 · low` interactive cost of $7.3926/1M**
   ([`../matrix/cost-matrix.md`](../matrix/cost-matrix.md)) — expensive, but
   available in seconds rather than 20 minutes.

### 8.5 Hybrid sizing for the same curve

Take the §8.2 curve and ask how many replicas to own (paid 24/7 at `b300 ·
res1y` $7.94) versus burst on AWS p6-b300 on-demand ($17.802):

| Owned replicas `K` | Owned GPUs | Owned $/day | Burst $/day | **Total $/day** |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 12,390 | 12,390 |
| 4 | 16 | 3,049 | 6,409 | 9,458 |
| 8 | 32 | 6,098 | 2,279 | 8,377 |
| **10** | **40** | **7,622** | **641** | **8,263** ← minimum |
| 11 | 44 | 8,385 | 142 | 8,527 |
| 12 (all owned) | 48 | 9,147 | 0 | 9,147 |

(`est.`, prices cited by name from
[`../cross-cutting/cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)
and §5's AWS rows.) The optimum owns **10 of the 12 peak replicas** and bursts
the top two, saving **9.7 %** over owning the peak and **33 %** over pure burst.
The curve is *flat* near the optimum — K=8 is **+1.4 %** and K=11 **+3.2 %**
over the K=10 minimum (`est.`, recomputed 2026-09-19) —
which is the real conclusion: **the exact split barely matters; owning roughly
the p90 of demand and bursting the rest is right, and the decision is dominated
by whether burst capacity is available at all.** AWS has no standard RIs on
p6-b300 ([`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md)),
so burst means on-demand, Capacity Blocks (prepaid, fixed-window) or spot —
each with a different availability story, and none of them a guarantee.

### 8.6 The policy, as a deployable summary

```
per model:
  replicas(t) = max(
      cron_floor(t),                                  # known events, L+8min early
      predictive_p90(t + L) / (C · ρ)   [DeepSeek only, once σ < 33%],
      reactive( backlog_seconds > θ_p , kv_usage > θ_d ),
      min_replicas                                    # §5.4 table
  )
  clamp to max_replicas = floor(free_GPUs / gpus_per_replica)
  scale-up:   step ≤ 2 replicas per L-length period
  scale-down: 1 replica per 10 min, 30-min stabilisation, drain first
below the autoscaler:
  Kueue cohort lends idle serving quota to batch; serving reclaims by preemption
  gang-schedule every replica > 1 GPU (Grove / Volcano PodGroup / KAI)
  KV tier (LMCache / KVBM) so a removed replica demotes its cache instead of dropping it
```

---

## Open questions

All ⚠️ items from above, consolidated. Each states the method that would close it.

1. **TensorRT-LLM dynamic-gauge version gate.** `trtllm_num_requests_waiting` /
   `_running` / `trtllm_kv_cache_utilization` are reported to need v1.3.0rc12+
   (PR #12545) and the `return_perf_metrics` + `enable_iter_perf_stats` pair, but
   the source is an issue thread, not release notes
   [src](https://github.com/NVIDIA/TensorRT-LLM/issues/12298). *Close by:* start
   `trtllm-serve` 1.3.0rc27 with those flags and curl `/prometheus/metrics`.
2. **Dynamo load-based scaling: functional or not?** The v1.3.0 planner page
   lists backend support (vLLM, TRT-LLM non-DP, SGLang ≥ 0.5.13) while the
   v-0-9-1 design doc says "the load-based code path exists but is non-functional
   with current backends (no prefill queue metrics)". *Close by:* run Dynamo
   1.5.0 with `enable_load_scaling: true, advisory: true` and read
   `dynamo_planner_*`.
3. **Dynamo 1.5.0 planner metric names and flags.** Everything in §3.5 is quoted
   from docs published at v1.3.0 / v-0-9-1; this tree pins Dynamo **1.5.0**
   ([`../cross-cutting/inference-engines.md` §2.4](../cross-cutting/inference-engines.md)).
   *Close by:* re-fetch the 1.5.0 planner docs.
4. **`peakPrefillThroughput` (`V_P`) for each (model, B300, parallelism).** The
   `42000` in §3.2 is a placeholder. llm-d's own warning: the same model/GPU
   through a different path varied `V_P` 2696→15928 and the replica answer 8→3.
   *Close by:* run llm-d's `calibrate.sh` (`V_P = CHUNK_SIZE / median(TTFT)`,
   `CHUNK_SIZE` = effective `--max-num-batched-tokens`) per model.
5. **Time-to-ready for all five models on B300.** §4.5's table is estimated from
   checkpoint bytes ÷ an assumed 5–20 GB/s NVMe read plus a compile/warm-up term
   anchored on third-party measurements on different hardware. Nothing in this
   tree measures it. *Close by:* time `vllm serve` from pod start to first
   successful `/v1/completions`, cold and with a warm `VLLM_CACHE_ROOT`.
6. **Local NVMe sustained aggregate read bandwidth on the HGX B300 nodes.**
   Assumed 5–20 GB/s. *Close by:* `fio` with the same read pattern the loader
   uses (large sequential, many concurrent files).
7. **vLLM sleep-mode wake-up latency for a 510 GB level-1 sleep**, and whether
   the node has enough DRAM to hold 510 GB of weights *plus* the 188.83 GiB
   Engram tables already offloaded there. *Close by:* measure `POST /wake_up`
   round-trip on a TP4 DeepSeek replica.
8. **Engine graceful-shutdown semantics.** No vLLM / SGLang / TensorRT-LLM doc
   found stating whether SIGTERM completes in-flight requests or aborts them.
   The `terminationGracePeriodSeconds` / `preStop` recipe in §5.1 is therefore
   unvalidated. *Close by:* send SIGTERM mid-generation and observe.
9. **Cache-hit-aware pod deletion.** No published implementation of
   `pod-deletion-cost` driven by `prefix_cache_hits/queries` was found. *Close
   by:* either find one or write the sidecar (≈50 lines).
10. **Live request migration** (Llumnix-style) is not a feature of any engine in
    [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md).
    Until it is, every scale-down either waits out the longest generation or
    drops it. *Close by:* re-check vLLM/SGLang release notes each quarter.
11. **BlitzScale / ServerlessLLM fast-scaling techniques** are research code, not
    shipped engine features, as of 2026-09-19. *Close by:* watch for a KV/weight
    multicast connector in vLLM or Dynamo NIXL.
12. **AWS p6-b300 spot availability and interruption rate.** The $5.591 figure
    is a reported spot price, not a capacity or interruption guarantee, and
    §6.5's "spot always wins on price" conclusion is only actionable if capacity
    exists. *Close by:* query the Spot placement score / interruption frequency
    for `p6-b300.48xlarge` in the target regions.
13. **No published LSTM/Transformer forecaster in a production LLM-serving
    autoscaler.** §4.2 lists them as research-only; if one ships, the σ < 33 %
    test in §8.3 is the gate it must pass.
14. **AIBrix metric naming.** Its docs example uses the v0 `gpu_cache_usage_perc`
    while vLLM v1 exports `kv_cache_usage_perc`. *Close by:* check the
    PodAutoscaler against the engine build in use.
15. **The 10× peak/trough ratio and 1,024-session peak in §8 are assumptions**,
    not measurements of this repo's traffic. Every number in §8.2–§8.5 scales
    with them. *Close by:* 14 days of
    `sum(rate(vllm:generation_tokens_total[5m]))` per model.

---

## Sources

Primary documentation:

- Kubernetes — [Horizontal Pod Autoscaling](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- Kubernetes blog — [Introducing Gateway API Inference Extension](https://kubernetes.io/blog/2025/06/05/introducing-gateway-api-inference-extension/)
- Gateway API Inference Extension — [InferencePool](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/)
- KEDA 2.20 — [ScaledObject specification](https://keda.sh/docs/2.20/reference/scaledobject-spec/), [Prometheus scaler](https://keda.sh/docs/2.20/scalers/prometheus/), [Scalers index](https://keda.sh/docs/2.20/scalers/)
- Knative — [KPA-specific configuration](https://knative.dev/docs/serving/autoscaling/kpa-specific/), [Scale to zero](https://knative.dev/docs/serving/autoscaling/scale-to-zero/), [Autoscale sample](https://knative.dev/docs/serving/autoscaling/autoscale-go/)
- KServe 0.20 — [Generative inference autoscaling](https://kserve.github.io/website/docs/model-serving/generative-inference/autoscaling)
- Ray — [Advanced Serve autoscaling](https://docs.ray.io/en/latest/serve/advanced-guides/advanced-autoscaling.html), [Serve autoscaling guide](https://docs.ray.io/en/latest/serve/autoscaling-guide.html)
- Kueue — [Preemption](https://kueue.sigs.k8s.io/docs/concepts/preemption/)
- Volcano — [Network topology aware scheduling](https://volcano.sh/en/docs/network_topology_aware_scheduling/), [docs index](https://volcano.sh/en/docs/)
- KAI Scheduler — [README (v0.10.0 feature list)](https://github.com/kai-scheduler/KAI-scheduler/blob/main/README.md)
- Grove — [README (PodClique / PodCliqueScalingGroup / PodCliqueSet / PodGang)](https://github.com/NVIDIA/grove/blob/main/README.md)
- vLLM — [Metrics design (v1)](https://docs.vllm.ai/en/stable/design/metrics/), [Sleep mode](https://github.com/vllm-project/vllm/blob/main/docs/features/sleep_mode.md)
- SGLang — [Production metrics](https://github.com/sgl-project/sglang/blob/main/docs/docs/references/production_metrics.mdx)
- TensorRT-LLM — [Prometheus metrics example](https://nvidia.github.io/TensorRT-LLM/examples/prometheus_metrics.html), [issue #12298 (metrics for inference-gateway scheduling)](https://github.com/NVIDIA/TensorRT-LLM/issues/12298)
- NVIDIA Dynamo — [Planner (v1.3.0)](https://docs.nvidia.com/dynamo/v1.3.0/components/planner), [Planner guide (v1.3.0)](https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide), [Planner design (v-0-9-1)](https://docs.nvidia.com/dynamo/v-0-9-1/design-docs/planner-design), [Tune the planner on Kubernetes](https://docs.nvidia.com/dynamo/kubernetes/auto-deployment/dynamo-planner)
- NVIDIA — [DCGM Exporter / GPU telemetry](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html)
- llm-d — [Workload autoscaling guide](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md), [KEDA + EPP queue](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-queue/README.md), [Token-aware autoscaling](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/keda-epp-token-aware/README.md), [Autoscaling architecture](https://llm-d.ai/docs/architecture/advanced/autoscaling), [Workload Variant Autoscaler (deprecated)](https://github.com/llm-d-incubation/workload-variant-autoscaler)
- AIBrix — [Autoscaler design](https://aibrix.readthedocs.io/latest/designs/aibrix-autoscaler.html), [Metric-based autoscaling](https://aibrix.readthedocs.io/latest/features/autoscaling/metric-based-autoscaling.html), [Optimizer-based autoscaling](https://aibrix.readthedocs.io/latest/features/autoscaling/optimizer-based-autoscaling.html), [RFC #2670 pending-replica guard](https://github.com/vllm-project/aibrix/issues/2670)
- LMCache — [README](https://github.com/LMCache/LMCache/blob/dev/README.md)
- AWS — [How predictive scaling works](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html)

Measurements and engineering posts:

- NVIDIA, 2025-09-16 — [Reducing cold-start latency with Run:ai Model Streamer](https://developer.nvidia.com/blog/reducing-cold-start-latency-for-llm-inference-with-nvidia-runai-model-streamer)
- AWS, 2026-09-01 — [Fast model loading for AI inference on Amazon EKS](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/)
- Route179, 2026-06-30 — [Optimizing vLLM cold start with model streaming and compile caching](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/)
- Microsoft Azure — [AzureLLMInferenceDataset2023](https://github.com/Azure/AzurePublicDataset/blob/master/AzureLLMInferenceDataset2023.md)

Papers:

- Sun et al., [Llumnix: Dynamic Scheduling for Large Language Model Serving](https://www.usenix.org/conference/osdi24/presentation/sun-biao), OSDI '24
- Fu et al., [ServerlessLLM: Low-Latency Serverless Inference for LLMs](https://arxiv.org/pdf/2401.14351)
- Zhang et al., [BlitzScale: Fast and Live Large Model Autoscaling with O(1) Host Caching](https://arxiv.org/abs/2412.17246), OSDI '25
- Patke et al., [Chiron: Hierarchical Autoscaling for LLM Serving](https://arxiv.org/abs/2501.08090), 2025-01-14
- [SageServe: Optimizing LLM Serving on Cloud Data Centers with Forecast Aware Auto-Scaling](https://arxiv.org/abs/2502.14617), POMACS 9(3) Art. 61, Dec 2025
- [AIBrix: Towards Scalable, Cost-Effective Large Language Model Inference Infrastructure](https://arxiv.org/abs/2504.03648), 2025-02-22
- Cui et al., [OpScale: Operator-level Provisioning and Autoscaling for LLM Serving](https://arxiv.org/abs/2608.13499), 2026-08-13
- Chen et al., [inference-fleet-sim: A Queueing-Theory-Grounded Fleet Capacity Planner for LLM Inference](https://arxiv.org/html/2603.16054v1), 2026
- Nie, Si & Zhou, [A Queueing-Theoretic Framework for Stability Analysis of LLM Inference with KV Cache Memory Constraints](https://arxiv.org/html/2605.04595), 2026-05-06
- [Queueing, Predictions, and LLMs: Challenges and Open Problems](https://arxiv.org/pdf/2503.07545)
- [Efficient Serving of LLM Applications with Probabilistic Demand Modeling](https://arxiv.org/pdf/2506.14851)
- Patel et al., [Splitwise: Efficient Generative LLM Inference Using Phase Splitting](https://arxiv.org/html/2311.18677v2)

In-tree references (not re-derived here): [`../METHODOLOGY.md`](../METHODOLOGY.md),
[`../matrix/pairs.json`](../matrix/pairs.json),
[`../matrix/fit-matrix.md`](../matrix/fit-matrix.md),
[`../matrix/cost-matrix.md`](../matrix/cost-matrix.md),
[`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md),
[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md),
[`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md).

---

## Verification log (2026-09-19)

Adversarial fact-check. Every primary source below was **opened**, not taken
from this document's own citation; every derivation was **recomputed** with
`python3`; every `research/` cross-reference was **opened and the number found
in the file**. 31 claims checked: **25 CONFIRMED**, **4 CORRECTED**, **2
UNVERIFIABLE**.

### A. Claims about this repo's models and `research/` docs

| # | Claim (§) | Verdict | Checked against |
|---|---|---|---|
| A1 | §1.2 replica shapes & seats @8K: Marlin-2B 1 GPU/1,934; Qwen3.8-27B 1/325; DeepSeek TP4 min 2 /11,184; DeepSeek-NVFP4 min 4 /40,801; Kimi-K3 TP8+DCP8 min 8 /101 | **CONFIRMED** — all five `max_concurrency_8k`, `min_gpus` and parallelism strings match exactly | `research/matrix/pairs.json` (`*/b300` rows) |
| A2 | §3.4 / §6.1 / §8: DeepSeek B300 interactive point C = 128 seats, TPOT 23.31 ms, TTFT 129 ms | **CONFIRMED** — `interactive: {concurrency:128, tpot_ms:23.31, ttft_ms:129}` | `research/matrix/pairs.json` |
| A3 | §6.2 / §8.4 Kimi-K3 `max_concurrency_8k` = 101 and `_128k` = 64 | **CONFIRMED** verbatim | `research/matrix/pairs.json` |
| A4 | §5.4 Marlin-2B $0.022/1M output on B300; §6.5 DeepSeek $1.4973, Kimi $7.3926, Qwen $0.1649 | **CONFIRMED** — all four `cost_per_1m_output_usd_low` cells match | `research/matrix/pairs.json` |
| A5 | §6.5 / §8.5 prices: `b300·low` $7.40 Hyperstack, `·high` $15.00 OCI `BM.GPU.B300.8`, `·res1y` $7.94 DigitalOcean 12-mo; AWS `p6-b300.48xlarge` $17.802 on-demand / $5.591 spot / $14.04 Capacity Block; **no standard RIs** | **CONFIRMED** — §5.7 table and §5.14 planning row; 1-yr and 3-yr columns are literally `N/A` for AWS | `research/cross-cutting/cloud-pricing.md` §5.7, §5.14 |
| A6 | §6.5 / §8.4 vendor API prices: DeepSeek $0.60, Qwen Cloud $3.00, Moonshot $15.00 per 1M output | **CONFIRMED** | `research/matrix/cost-matrix.md` §6.1 |
| A7 | §6.5 "the API is more expensive for Kimi **and DeepSeek**" | **CORRECTED** — true for Kimi ($15.00 vs $7.3926) but **false for DeepSeek**: the API is 2.5× *cheaper* ($0.60 vs $1.4973). cost-matrix §6.2 puts DeepSeek's B300 break-even at **232 % ✗**, its only sub-100 % cell being B200 at 92 %. Text rewritten and the contradiction flagged inline | `research/matrix/cost-matrix.md` §6.2, `pairs.json` |
| A8 | Preamble engine pins: vLLM 0.29.0, SGLang 0.5.20, TRT-LLM 1.2.1 / 1.3.0rc27, Dynamo 1.5.0 | **CONFIRMED** — §2 table row-for-row, incl. rc27 dated 2026-09-17 | `research/cross-cutting/inference-engines.md` §2 |
| A9 | METHODOLOGY-sourced bytes: 510.29 / 527.27 / 1,560.9 / 5.444 / 30.87 GB; 268 GB/GPU, 2,144 GB/node; Kimi KDA 2.25 GB/request (S=5); Marlin 2 fps / ≤240 frames / 200,704 px | **CONFIRMED** — every figure present in §8's pinned tables | `research/METHODOLOGY.md` §8 |
| A10 | §4.5 / §5.4 "188.83 GiB Engram tables" offloaded to host RAM | **CONFIRMED** in `pairs.json` (`deepseek41f/h100`: "202.76 GB / 188.83 GiB"). Note METHODOLOGY §8 says "~183 GiB" for the base checkpoint and 188.8 GiB for the NVFP4 row — a pre-existing tree-level imprecision, not introduced here | `research/matrix/pairs.json`, `research/METHODOLOGY.md` §8 |
| A11 | §7.1 "4× DeepSeek TP2 per node is the max-throughput shape" | **CONFIRMED** — `max_throughput.gpus = 2` on the `deepseek41f/b300` row | `research/matrix/pairs.json` |

### B. Recomputed derivations (all with `python3`)

| # | Derivation (§) | Verdict |
|---|---|---|
| B1 | §4.4 `burn_time = t(1−ρ)/(ρ(x−1))`: 2× in 5 min at ρ=0.70 → **2.143 min**; 3× → **1.071 min** | **CONFIRMED** (doc: 2.1 / 1.1) |
| B2 | §4.4 `ρ ≤ t/(t+L(x−1))` at L=8, x=2, t=5 → **0.3846** | **CONFIRMED** (doc: 0.385) |
| B3 | §4.4 margin table `1+z·σ`: (1.128, 1.164) (1.256, 1.328) (1.384, 1.492) | **CONFIRMED** to 2 dp at z=1.28/1.64 |
| B4 | §4.4 / §8.3 gate `1/0.70 = 1.4286`; `σ < (1/ρ−1)/1.28 = 0.3348` | **CONFIRMED** (doc: ×1.43, σ < 33 %) |
| B5 | §5.1 drain: 512 × 23.31 ms = **11.93 s**; 2,000 × 23.31 ms = **46.6 s** | **CONFIRMED** (doc: 11.9 s, ~47 s) |
| B6 | §6.1 Little's Law: residency 0.129 + 512×0.02331 = **12.064 s**; 128/12.064 = **10.61 req/s**; ×0.70 = **7.43 req/s** | **CONFIRMED** |
| B7 | §6.4 Kimi hot spare: 8×$7.40 = **$59.20/h** = **$1,420.80/day**; ÷ $15.00/1M = **94.72 M tokens/day** | **CONFIRMED** (doc: 94.7 M) |
| B8 | §6.5 duty-cycle break-evens vs $7.94: on-demand **44.60 %**, Capacity Block **56.55 %**, spot **142.01 %** | **CONFIRMED** (doc: 44.6 / 56.6 / 142) |
| B9 | §8.2 the full 24-row curve: `ceil(sessions / 89.6)` reproduces **all 24 replica counts with zero mismatches**; GPU-hours **696**; static-at-peak **1,152**; saving **39.58 %**; $5,150.40 vs $8,524.80; 456 freed GPU-h worth **$3,374.40** | **CONFIRMED** — every cell |
| B10 | §8.3 steepest ramp 676−492 = 184/h = **3.067 sessions/min**; 89.6 seats ÷ that = **29.2 min per replica** | **CONFIRMED** (doc: one replica every 29 min) |
| B11 | §8.5 hybrid table recomputed from the §8.2 curve at $7.94 owned / $17.802 burst: K=0 **12,390**; 4 → 3,049+6,409 = **9,458**; 8 → 6,098+2,279 = **8,377**; 10 → 7,622+641 = **8,263**; 11 → 8,385+142 = **8,527**; 12 → **9,147**. Optimum K=10; saving **9.66 %** vs all-owned and **33.31 %** vs pure burst | **CONFIRMED** (doc: 9.7 % / 33 %) |
| B12 | §8.5 "anything from K=8 to K=11 is within 3 %" | **CORRECTED** — K=8 is +1.38 %, K=11 is **+3.19 %**, i.e. just outside 3 %. Rewritten as +1.4 % / +3.2 % |
| B13 | §8.4 Moonshot $15.00 ÷ $7.3926 = **2.029×**; §6.5 Qwen $3.00 ÷ $0.1649 = **18.19×** | **CONFIRMED** (doc: 2.0×, 18×) |

### C. Engine / operator versions, flags, YAML fields and metric names

| # | Claim (§) | Verdict | URL opened |
|---|---|---|---|
| C1 | §3.1 HPA `desiredReplicas = ceil[…]`, default tolerance **0.1**, sync period **15 s**, default `scaleUp` stabilisation **0 s** / `scaleDown` **300 s** | **CONFIRMED** (formula, 0.1, 15 s, and the default-behavior block `scaleDown: 300 / scaleUp: 0`) | https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/ |
| C2 | §3.1 tolerance configurable per-HPA from **v1.33 alpha / v1.34 beta** | **UNVERIFIABLE** — the version gate is past the truncation point of the fetched page and the session's search budget was exhausted; ⚠️ *close by:* read KEP-4951 / the v1.34 changelog | same page (truncated) |
| C3 | §3.2 KEDA 2.20 `ScaledObject` defaults: `pollingInterval` 30 s, `cooldownPeriod` 300 s, `initialCooldownPeriod` 0 s, `minReplicaCount` 0, `maxReplicaCount` 100, `idleReplicaCount` "only supported value is 0", `scalingModifiers.formula`/`.target` both mandatory when the section is defined | **CONFIRMED** — every default verbatim | https://keda.sh/docs/2.20/reference/scaledobject-spec/ |
| C4 | §3.3 Knative KPA: stable window **60 s** (6 s–1 h), `panic-window-percentage` **10.0** (1–100), `panic-threshold-percentage` **200.0** (110–1000), `max-scale-up-rate` **1000.0**, `max-scale-down-rate` **2.0** | **CONFIRMED** — all five defaults and all three ranges | https://knative.dev/docs/serving/autoscaling/kpa-specific/ |
| C5 | §3.3 / §5.4 Knative scale-to-zero on by default, `scale-to-zero-grace-period` **30 s**, `scale-to-zero-pod-retention-period` **0 s** | **CONFIRMED** (`enable-scale-to-zero` "Default: true") | https://knative.dev/docs/serving/autoscaling/scale-to-zero/ |
| C6 | §3.4 Ray Serve defaults: `target_ongoing_requests` 2.0, `max_ongoing_requests` 5, `min/max_replicas` 1/1, `upscale_delay_s` 30, `downscale_delay_s` 600, `up/downscaling_factor` 1.0, `metrics_interval_s` 10, `look_back_period_s` 30, and the "20–50 % above target" advice | **CONFIRMED** — all ten defaults and the advice sentence | https://docs.ray.io/en/latest/serve/advanced-guides/advanced-autoscaling.html |
| C7 | §2.2 vLLM v1 metric names (`kv_cache_usage_perc`, `request_queue_time_seconds`, `time_to_first_token_seconds`, `inter_token_latency_seconds`, `prefix_cache_queries`/`_hits`, `prompt_tokens_total`, `generation_tokens_total`); `num_requests_swapped` and `cpu_cache_usage_perc` legacy | **CONFIRMED**. `vllm:num_requests_waiting` is not on that page but appears as a PodAutoscaler target metric in AIBrix's docs, so the name is real | https://docs.vllm.ai/en/stable/design/metrics/ |
| C8 | §2.2 "`gpu_cache_usage_perc` is the v0 name; v1 calls it `kv_cache_usage_perc`" | **UNVERIFIABLE** — the v1 metrics page never states the rename. ⚠️ added inline; the *operational* advice (check your engine build) stands | https://docs.vllm.ai/en/stable/design/metrics/ |
| C9 | §2.2 SGLang 0.5.20 metric names — all eleven, plus `--enable-metrics` | **CONFIRMED** — every name present verbatim, including `sglang:spec_num_steps` / `sglang:spec_num_draft_tokens` | https://raw.githubusercontent.com/sgl-project/sglang/main/docs/docs/references/production_metrics.mdx |
| C10 | §2.2 TRT-LLM serves Prometheus text at `/prometheus/metrics`, and the `trtllm_kv_cache_utilization` / `_hit_rate` / `_reused_blocks_total` / `_missed_blocks_total` / `_request_queue_time_seconds` / `_time_to_first_token_seconds` / `_e2e_request_latency_seconds` names | **CONFIRMED** — route quoted as `http://localhost:8000/prometheus/metrics`; seven of the nine names present (`trtllm_num_requests_waiting` / `_running` are absent, consistent with the doc's own rc12 version-gate ⚠️) | https://nvidia.github.io/TensorRT-LLM/examples/prometheus_metrics.html |
| C11 | §2.2 "the plain `/metrics` route returns JSON iteration stats Prometheus cannot parse" | **CORRECTED** → ⚠️ — not stated on the cited page | same page |
| C12 | §3.5 Dynamo planner: `dynamo_planner_estimated_ttft_ms` / `_estimated_itl_ms` / `_predicted_num_prefill_replicas` / `_predicted_num_decode_replicas`; `throughput_adjustment_interval_seconds` **180 s**, `load_adjustment_interval_seconds` **5 s**; `enable_throughput_scaling` default **true**, `enable_load_scaling` default **false**; predictors `constant`/`arima`/`kalman`/`prophet` with `kalman_q_level`/`_q_trend`/`_r`/`_min_points` and `prophet_window_size` **50 s**; profiling `rapid` ~30 s AIC / `thorough` 2–4 h real GPUs / `none` | **CONFIRMED** — every name, default and mode | https://docs.nvidia.com/dynamo/v1.3.0/components/planner/planner-guide |
| C13 | §3.5 / §4.4 quote "Throughput updates the lower-bound replicas, then load-based scaling can adjust above that floor" | **CONFIRMED** verbatim (the source continues "…and apply the global GPU budget clamp") | same page |
| C14 | §4.2 KV hit rate and speculative accept length are **not** predicted; planner uses the latest valid observation | **CONFIRMED** — "runtime engine/router signals, not traffic shape. The planner stores the latest valid observation for each signal and reuses it" | same page |
| C15 | §3.3 KServe: `serving.kserve.io/autoscalerClass: "keda"`, the `External`/`backend: "prometheus"` metrics block with `query: vllm:num_requests_running` and `value: "2"`, docs at **0.20**, and the otel-add-on "validation webhook disabled" requirement because `vllm:`-prefixed names fail the webhook | **CONFIRMED** — all four, the webhook sentence verbatim | https://kserve.github.io/website/docs/model-serving/generative-inference/autoscaling |
| C16 | §3.6 / §5.3 AIBrix `PodAutoscaler` `autoscaling.aibrix.ai/v1alpha1`, strategies HPA/KPA/APA, example `targetMetric: gpu_cache_usage_perc`, `observeWindowSeconds` **180 s** (1–3600), `panicWindowSeconds` **60 s**, tolerances **0.1**; multi-metric ("highest demand wins"), scheduled bounds with timezone + `daysOfWeek`, StormService role-level scaling | **CONFIRMED** — every field and default | https://aibrix.readthedocs.io/latest/features/autoscaling/metric-based-autoscaling.html |
| C17 | §3.6 AIBrix `panicWindowSeconds` "must be ≤ observe window" | **CORRECTED** → ⚠️ — the docs give both as an independent 1–3600 s range and state no ordering constraint | same page |

### D. llm-d, schedulers and gateway

| # | Claim (§) | Verdict | URL opened |
|---|---|---|---|
| D1 | §1.3 "GPU utilization is often pegged near 100% during active batching regardless of actual load, making it an entirely unreliable signal" and the lagging-indicator sentence | **CONFIRMED** — both verbatim | https://raw.githubusercontent.com/llm-d/llm-d/main/guides/workload-autoscaling/README.md |
| D2 | §2.3 "The Prometheus Adapter is planned for deprecation, and it is recommended to use KEDA instead" | **CONFIRMED** verbatim | same |
| D3 | §3.6 the five-path comparison table (queue / saturation / token-aware / SLO-aware / WVA) with signals and cost-awareness column | **CONFIRMED** — all five rows match; WVA's cell reads "Prefers lower-cost hardware variants" (the doc's "adds on cheapest, removes from most expensive" is a paraphrase, not a quote) | same |
| D4 | §3.7 / §7.3 Kueue rebalancing quote: guaranteed GPU floor in a shared cohort, "an idle model's GPUs are lent to a busy one and reclaimed by preemption… over-budget demand shows up as Pending pods" | **CONFIRMED** verbatim | same |
| D5 | §2.1 "An 8192-token prompt is 16× the prefill work of a 512-token one" | **CONFIRMED** verbatim | https://raw.githubusercontent.com/llm-d/llm-d/main/guides/workload-autoscaling/keda-epp-token-aware/README.md |
| D6 | §3.2 / §8.1 the composition `max(ceil(backlog_s / 1.5), ceil(kv / 0.8))`; `V_P = CHUNK_SIZE / median(TTFT)`; the `CHUNK_SIZE` must match `--max-num-batched-tokens` "or the result is silently wrong" warning; reference `V_P` **15928** (Qwen3-32B H100-80GB TP=2) and **33821** (gpt-oss-120b H200); the 2696→15928 / 8→3 replica sensitivity | **CONFIRMED** — the formula template, both reference figures, the warning and the sensitivity example. (The template is published as `max(ceil(backlog_s / threshold), ceil(kv / 0.8))`; **1.5** is the guide's co-located default) | same |
| D7 | §2.3 the `GaugeVec` series-pruning bug: `llm_d_epp_inflight_tokens`, llm-d-router **#2529**, fixed by **#2577**, merged after `v0.10.0` | **CONFIRMED** — both issue numbers and the "on `main` but not in v0.10.0 or earlier tagged releases" status | same |
| D8 | §7.2 Grove: the deadlock sentence; `PodClique` / `PodCliqueScalingGroup` ("scale and are scheduled together as a gang… ideal for tightly coupled roles like prefill leader and worker") / `PodCliqueSet` / `PodGang`; explicit startup ordering | **CONFIRMED** — all four definitions and the ordering feature verbatim | https://raw.githubusercontent.com/NVIDIA/grove/main/README.md |
| D9 | §7.1 / §7.3 KAI Scheduler: bin-packing & spread, Workload Consolidation, hierarchical queues, DRF, topology-aware scheduling, priority-vs-preemptibility separation, min-guaranteed-runtime, Grove + Dynamo integration, **v0.10.0 (2025-10)** | **CONFIRMED** — every quoted phrase and the version/date | https://raw.githubusercontent.com/kai-scheduler/KAI-scheduler/main/README.md |
| D10 | §7.1 Volcano "since v1.14.0 subgroup-level gang scheduling via a `partitionPolicy` on a VolcanoJob" | **CORRECTED** → ⚠️ — the cited network-topology page covers `HyperNode`, hard/soft constraints and UFM/RoCE auto-discovery, and mentions neither v1.14.0 nor `partitionPolicy` (its Helm example pins 1.12.0) | https://volcano.sh/en/docs/network_topology_aware_scheduling/ |
| D11 | §7.3 Kueue: all three of `withinClusterQueue`, `reclaimWithinCohort`, `borrowWithinCohort`; greedy classic algorithm; Fair Sharing quote and the `LessThanOrEqualToFinalShare` / `LessThanInitialShare` strategies | **CONFIRMED** — all three fields, the algorithm description and both strategy names | https://kueue.sigs.k8s.io/docs/concepts/preemption/ |

### E. Measured cold-start numbers, AWS predictive scaling, and the literature

| # | Claim (§) | Verdict | URL opened |
|---|---|---|---|
| E1 | §4.5 Run:ai Model Streamer, Llama 3 8B / 15 GB: GP3 47.99 → **14.34 s** (conc. 16); IO2 47 → **7.53 s** (conc. 8); S3 Streamer **4.88 s** (conc. 32) vs Tensorizer 37.36 s; vLLM engine-ready 66.13 / 62.69 → **35.08 / 28.28 / 23.18 s**; dated **2025-09-16** | **CONFIRMED** — every figure and the date | https://developer.nvidia.com/blog/reducing-cold-start-latency-for-llm-inference-with-nvidia-runai-model-streamer |
| E2 | §4.5 AWS EKS: small model 82 → 65 → **16 s** (weights 29→12 s, compile 53→4 s); large **203 GiB** TP=4 **457 → 59 → 32 s**; 4 GiB chunks; dated **2026-09-01** | **CORRECTED** on one figure — the small model is **67 GiB**, not 64 GiB (it is Qwen3-6-35B-A3B on p5.48xlarge TP=2); all timings, the 203 GiB row and the date confirmed. Large-model component times added (weights 423→25→26 s, compile 34→34→6 s). The "**17** connections" detail is not on the page → ⚠️ | https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/ |
| E3 | §4.5 Route179: Qwen3.6-35B-A3B-NVFP4 on **1× DGX Spark**, vLLM **v0.22.1**, weights **142 s** + compile **39 s** + warm-up **136 s** = **317 s**, improved to **167 s**; and the "depends only on the model's structure — its layer shapes, data types, and the target GPU — not on the actual weight values" quote | **CONFIRMED** — page exists, all four numbers and the quote verbatim. The doc's separate "39 s → 10 s" cached-compile figure is **not** in it → ⚠️ | https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/ |
| E4 | §4.5 vLLM sleep mode: level 1 offloads weights to CPU + discards KV, level 2 discards both; "up to 90%+ of GPU memory"; `wake_up(tags=["weights"])` / `["kv_cache"]`; `release_kv_cache_memory()`; `VLLM_SERVER_DEV_MODE=1` + `--enable-sleep-mode`, `POST /sleep?level=1`, `POST /wake_up` | **CONFIRMED** — every flag, level and endpoint | https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/sleep_mode.md |
| E5 | §0.4 / §4.3 / §4.4 AWS predictive scaling: ≥ **24 h** of data, analyses up to **14 days**, **hourly forecast for the next 48 h**, refreshed **every 6 h**, *forecast only* → *forecast and scale*, "If the forecast expects a decrease in load, it will not scale in to remove capacity", `SchedulingBufferTime` as the pre-launch knob, "each policy determines the desired capacity independently, and the desired capacity is set to the maximum of those", `MaxCapacityBreachBehavior`/`MaxCapacityBuffer` with the permanent-ceiling warning, "recurring load patterns that are specific to the day of the week or the time of day", and "forecasts are more effective if historical data spans two full weeks" | **CONFIRMED** — all ten, verbatim | https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html |
| E6 | §4.1 *"the arrival times of inference requests had clear diurnal and weekly patterns"*, attributed to the Azure dataset page and Splitwise | **CORRECTED** → ⚠️ — **the sentence is in neither source.** What *is* there: the traces are from two services, "coding and conversation", captured 2023-11-11, and "are 20 minutes long and include the arrival time, input size (number of prompt tokens), and output size (number of output tokens)"; the dataset page's schema is `TIMESTAMP` / `ContextTokens` / `GeneratedTokens` and makes no temporal-pattern claim. §4.1's seasonality premise now rests on SageServe and AWS only | https://arxiv.org/html/2311.18677v2 · https://raw.githubusercontent.com/Azure/AzurePublicDataset/master/AzureLLMInferenceDataset2023.md |
| E7 | §4.8 Llumnix (OSDI '24): live migration of requests **and their in-memory states**; tail latency "by an order of magnitude"; up to **1.5×** for high-priority; **36 %** cost saving at similar tail latencies | **CONFIRMED** — all three numbers verbatim in the abstract (the USENIX landing page 403s; verified on arXiv) | https://arxiv.org/abs/2406.03243 |
| E8 | §4.1 / §4.8 SageServe: **25 %** GPU-hour saving, **80 %** less autoscaling waste; v1 **2025-02-20**, v3 **2025-11-12**; **POMACS 9(3) Art. 61, Dec 2025** | **CONFIRMED** — both numbers, both dates and the full venue string | https://arxiv.org/abs/2502.14617 |
| E9 | §3.6 / §4.8 AIBrix paper: **+50 %** throughput, **−70 %** latency, submitted **2025-02-22**; the 11.5 % / 11.4 % / 33 % figures are the *autoscaler's* own, not the abstract's | **CONFIRMED** — the abstract carries only the 50 %/70 % system-level pair and the v1 date, exactly as the doc's caveat says | https://arxiv.org/abs/2504.03648 |
| E10 | §4.8 OpScale (2026-08-13): operator-level provisioning; **36.3 %** fewer GPUs, **28 %** less power, **+44 %** throughput at fixed budget; up to **40 A100s and 24 GB200s** | **CONFIRMED** — every number and both hardware counts | https://arxiv.org/abs/2608.13499 |
| E11 | §4.8 / §6.2 / §6.3 inference-fleet-sim (arXiv 2603.16054, submitted 2026-03-17): M/G/c + Kimura; **"For chatbot workloads (low Cs2), the Kimura model is conservative by 8–14% vs. DES"**; Erlang-C "under-estimates tail latency" on high-variance agent workloads; the **0.85** utilisation cap ("utilization cap ϱ≤0.85"); the P99 formula `W99 ≈ C(c,ϱ)/(c·μ·(1−ϱ)) · (1+Cs²)/2 · ln(100)`; 10⁴-request DES | **CONFIRMED** — paper exists (Chen, Liu, Liu, Jiang, He, Liu), all five specifics verbatim. Note the doc's P99 formula omits the `1/(c·μ(1−ϱ))` factor of the Erlang-C term; it labels that term "[Erlang-C term]", so it is abbreviated rather than wrong | https://arxiv.org/abs/2603.16054 · https://arxiv.org/html/2603.16054v1 |
| E12 | §4.8 / §6.2 KV-constrained stability (arXiv 2605.04595, 2026-05-06, Nie/Si/Zhou): stability & instability conditions under a KV memory constraint, validated **within 10 %** on real GPUs | **CONFIRMED** — authors, date and the 10 % validation; the paper is noted as **accepted to ICML 2026**, which this doc does not yet record | https://arxiv.org/abs/2605.04595 |

### Not re-verified here (carried forward as the document's own ⚠️)

BlitzScale (94 % / 49 %), ServerlessLLM (8.2×), Chiron (90 % / 70 %), the
probabilistic-demand and queueing-survey papers, the DCGM field definitions, the
LMCache README quote, the Gateway API `InferencePool` / Saturation Detector
quotes, the llm-d WVA deprecation status, AIBrix RFC #2670, and TRT-LLM issue
#12298 / PR #12545 — all already carry either an explicit `[src]` the reader can
open or an inline ⚠️ in the text above. The session's web-search budget was
exhausted before they could be opened independently; **⚠️ TO BE VERIFIED** as a
group.
