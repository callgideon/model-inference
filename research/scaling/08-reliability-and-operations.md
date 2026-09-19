# Reliability, observability and day-2 operations

Research date: **2026-09-19**. Legend, cost formulas and pinned GPU/model inputs
come from [`../METHODOLOGY.md`](../METHODOLOGY.md); the existing tree covers
per-GPU specs ([`../gpus/`](../gpus/)), per-model fit and cost
([`../models/`](../models/), [`../matrix/`](../matrix/)), engines
([`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)),
quantization ([`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md))
and serving optimizations ([`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md)).
This document does not re-derive any of those numbers; it links to them.

**Scope.** Everything that happens *after* a deployment shape has been chosen:
what you promise (SLOs), what breaks (failure modes), how you notice (detection),
how you recover, what you look at (observability), how you change things safely
(change management), how you re-measure (benchmarking as a discipline), how you
keep it locked down (security hygiene), and the ten runbooks an on-call engineer
actually opens at 03:00.

**Running hardware.** 8×B300 HGX nodes (268 GB/GPU as deployed, 2,144 GB/node —
[`../gpus/b300.md`](../gpus/b300.md)), NVLink within a node, InfiniBand/RoCE
between nodes, local NVMe, plus AWS p6 nodes. Five models, with very different
blast radii:

| Model | Deployment shape on this repo's node | Replica = | Blast radius of one GPU fault |
|---|---|---|---|
| [DeepSeek-V4.1-Flash](../models/deepseek41f/b300.md) | **min TP2** (Engram→host), **rec TP4**; interactive 2 × TP4/node, batch 4 × TP2/node ([`../matrix/recommendations.md` §2.1](../matrix/recommendations.md), [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md)) | 2–4 GPUs | ¼ to ½ node, DSpark γ=5 enabled. **Never TP8, never cross nodes** — wide-EP "runs badly" on an 8-GPU domain |
| [DeepSeek-V4.1-Flash-NVFP4](../models/deepseek41fnvfp4/b300.md) | **min & rec TP4**, 2 replicas/node (TP2 = 264.9 GB/GPU vs 241.2 usable → `infeasible (KV)`) | 4 GPUs | ½ node |
| [Qwen3.8-27B](../models/qwen3827b/b300.md) | 1 GPU | 1 GPU | 1/8 node — the only model where a single Xid costs one replica |
| [Kimi-K3](../models/kimik3/b300.md) | 8 GPUs (195 GB/GPU) | **a whole node** | **1 node.** Any Xid on any of 8 GPUs kills the replica |
| [Marlin-2B](../models/marlin2b/b300.md) | 1 GPU | 1 GPU | 1/8 node |

That table is the single most important operational fact in this document:
**Kimi-K3 has no partial-failure mode.** Every reliability decision below —
hot spares, drain policy, restart budgets, rolling-update surge — is different
for a node-sized replica than for a GPU-sized one.

---

## 1. SLOs for inference

### 1.1 The SLI menu, and which ones are actually good SLIs

| SLI | Definition | Engine metric that measures it | Good SLO? |
|---|---|---|---|
| Availability | fraction of requests not returning 5xx (or not returning at all) | gateway-side; `vllm:request_success_total{finished_reason="abort"}` as a corroborating signal [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md) | **Yes** — the primary SLO |
| TTFT | time from request arrival to first streamed token | `vllm:time_to_first_token_seconds` (Histogram) [[src]](https://docs.vllm.ai/en/stable/design/metrics/), `sglang:time_to_first_token_seconds` [[src]](https://docs.sglang.io/references/production_metrics.html) | **Yes**, but must be bucketed by input length |
| TPOT | `(e2e_latency − TTFT) / (output_tokens − 1)` | `vllm:request_time_per_output_token_seconds`, "recorded once per finished request as `(end-to-end latency - TTFT) / (number of output tokens - 1)`… recorded as zero for requests that generate no more than one token" [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md) | **Yes** — the steady-state UX metric |
| ITL | wall-clock gap between successive streamed outputs | `vllm:inter_token_latency_seconds`, "records one sample per streamed output event" [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md) | For *stutter* detection, not for the SLO |
| E2E latency | request arrival → last token | `vllm:e2e_request_latency_seconds` | Only for non-streaming / batch tiers |
| Queue time | admission delay | `vllm:request_queue_time_seconds` | Leading indicator, not an SLO |
| Goodput | requests/s meeting **all** latency SLOs simultaneously | not an engine metric — computed by the load generator ([`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) emits it, [`docs/goodput.md`](https://github.com/kubernetes-sigs/inference-perf/blob/main/docs/goodput.md)) | **Yes** — the capacity-planning metric |

**ITL ≠ TPOT, and vLLM says so explicitly.** The two metrics are different
aggregations: ITL is per streamed *event*, TPOT is per finished *request*. vLLM's
own design doc: *"It approximates TPOT when each output contains exactly one
token, but differs from request-level TPOT when an output contains multiple
tokens or when aggregation weights differ. Use `vllm:request_time_per_output_token_seconds`
for request-level TPOT and `vllm:inter_token_latency_seconds` when you specifically"*
want per-event stutter [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md).

This distinction is **load-bearing for speculative decoding**, which every
DeepSeek-V4.1-Flash operating point in this tree assumes (`DSpark γ=5`,
[`../matrix/recommendations.md`](../matrix/recommendations.md)). With γ=5 a
single decode step can emit up to 6 tokens, so one "streamed output event" may
carry several tokens. **ITL will look better than TPOT on a speculating replica,
and the gap is proportional to the acceptance rate.** If your SLO dashboard uses
ITL and your capacity model uses TPOT, they will disagree by exactly the
speculation win — the 3.13× output-per-byte figure the recommendations doc pins.
Pick TPOT for the SLO. Use ITL only for the "is the stream stuttering?" panel.

**Goodput, defined.** Throughput counts tokens; goodput counts only the tokens
delivered inside the SLO. The standard definition in the serving literature is
the request rate sustainable while meeting the latency target — ⚠️ **TO BE
VERIFIED** on the exact percentile convention: the DistServe line of work uses
"p99 attainment with ≤1 % violations", but `inference-perf` computes goodput from
a user-supplied per-request SLO predicate rather than a fixed percentile
([README](https://github.com/kubernetes-sigs/inference-perf/blob/main/README.md):
*"Goodput Measurement: Measure rate of requests meeting your SLO constraints"*).
Estimation method: adopt `inference-perf`'s per-request predicate form, because
it composes across TTFT and TPOT, and state the percentile separately.

> **Why goodput and not throughput is the capacity number.** Every $/1M-token
> figure in [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md) §2 is computed
> at "the concurrency that hits TPOT ≤ 50 ms" — that *is* a goodput operating
> point. Reporting max-throughput tokens/s as capacity double-counts the requests
> that blew the SLO and would be retried.

### 1.2 Per-model, per-tier SLO table (proposed for this repo)

SLO targets are a product decision, not a measurement. The table below is a
**proposal** anchored to this tree's S1/S2/S3/S4 scenarios
([`../METHODOLOGY.md` §6](../METHODOLOGY.md)); the TPOT column is inherited
(S1's `TPOT ≤ 50 ms`), the TTFT and availability columns are ⚠️ **TO BE
VERIFIED** product targets, not measurements.

| Model | Tier | Availability | TTFT p95 | TPOT p95 | Scenario | Notes |
|---|---|---|---|---|---|---|
| Qwen3.8-27B | interactive | 99.9 % | 400 ms ⚠️ | 50 ms | S1 (4K/512) | single-GPU replica, cheapest to over-provision |
| Qwen3.8-27B | batch | 99.5 % | none | none | S4 | admit only when interactive has headroom |
| DeepSeek-V4.1-Flash | interactive | 99.9 % | 800 ms ⚠️ | 50 ms | S1 | γ=5 speculation on; **measure TPOT not ITL** |
| DeepSeek-V4.1-Flash | long-context | 99.5 % | 6 s ⚠️ | 50 ms | S2 (32K/1K) | TTFT scales with uncached prefill |
| DeepSeek-V4.1-Flash | very-long | 99.0 % | none | none | S3 (128K/2K) | no TTFT SLO; it is a batch-shaped request |
| Kimi-K3 | interactive | **99.5 %** ⚠️ | 1.5 s ⚠️ | 50 ms | S1 | lower availability target is deliberate — see §1.3 |
| Marlin-2B | video VLM | 99.9 % | ⚠️ per-clip, see below | n/a | — | TTFT is dominated by encode, not prefill |

**Marlin-2B needs a different SLI.** It is a video VLM capped at 2 fps, ≤ 240
frames, 200,704 px/frame ([`../models/marlin2b/architecture.md`](../models/marlin2b/architecture.md)).
TTFT for a 240-frame clip is an *encode* cost, not a prefill cost, and it does
not scale like a text prompt. The right SLI is **time-to-first-token normalised
by input frame count** (ms/frame), with a separate SLO on the encoder queue.
⚠️ **TO BE VERIFIED**: no engine supports Marlin-2B as of 2026-09-19
([`../cross-cutting/inference-engines.md` §6.5](../cross-cutting/inference-engines.md)),
so there is no metric to attach this to yet — this is a design note, not a
configuration.

**Why Kimi-K3 gets a lower availability target.** Its replica is a whole 8×B300
node. Availability of a node-sized replica is bounded by node availability, and
node availability is bounded by the union of 8 GPUs' fault rates, the NVSwitch
fabric, the host, and every driver-level Xid in §2. Promising 99.9 % on a
node-sized replica means you need enough *spare nodes* to cover node MTTR, and
at $15/GPU-hour on-demand B300 (**planning price · `b300` · high**, OCI
`BM.GPU.B300.8` — [`../cross-cutting/cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md);
the `low` tier is $7.40, so the band is $59.20–$120/hour)
one hot-spare node is $120/hour of insurance at the `high` tier. The decision rule:

> **Availability target vs. spare capacity.** For a replica of size *G* GPUs
> drawn from a pool of *N* GPUs, the number of concurrent replicas is ⌊N/G⌋.
> A 99.9 % target with MTTR *T* and per-node fault rate λ requires a hot spare
> whenever λ·T > (1 − SLO) × ⌊N/G⌋. For G = 8 (Kimi-K3) that threshold is hit
> an order of magnitude sooner than for G = 1 (Qwen3.8-27B). **Trade-off:** a
> hot spare node is pure cost when healthy; a cold spare is free but adds the
> full weight-load time (§2.7, Kimi-K3 is 1,560.9 GB on disk) to every MTTR.

### 1.3 Error budgets and burn-rate alerting

Adopt Google's multiwindow, multi-burn-rate scheme unchanged. Burn rate is
*"how fast, relative to the SLO, the service consumes the error budget"*
[[src]](https://sre.google/workbook/alerting-on-slos/). The recommended
parameters for a 99.9 % target (Table 5-8 of the SRE Workbook):

| Severity | Long window | Short window | Burn rate | Error budget consumed |
|---|---|---|---|---|
| Page | 1 hour | 5 minutes | 14.4 | 2 % |
| Page | 6 hours | 30 minutes | 6 | 5 % |
| Ticket | 3 days | 6 hours | 1 | 10 % |

[[src]](https://sre.google/workbook/alerting-on-slos/)

For an availability SLI this is mechanical. For **latency** SLIs, the error
budget is over the *fraction of requests exceeding the threshold*, not over the
percentile itself — you cannot burn-rate-alert on a p95 gauge. Concretely, with
vLLM's histograms:

```promql
# SLI: fraction of requests whose TPOT exceeded 50 ms, 1h window.
# vllm:request_time_per_output_token_seconds is a Histogram, so the
# le="0.05" bucket is the "good events" count.
1 - (
  sum(rate(vllm:request_time_per_output_token_seconds_bucket{le="0.05",model_name="$model"}[1h]))
  /
  sum(rate(vllm:request_time_per_output_token_seconds_count{model_name="$model"}[1h]))
)
```

⚠️ **TO BE VERIFIED**: that `le="0.05"` bucket boundary exists **on the TPOT
histogram**. Re-checked 2026-09-19: the metrics doc's default edges for
`vllm:time_to_first_token_seconds` are
`0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, …`
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)
— **0.05 is not among them** (0.04 and 0.06 are), which confirms the hazard
concretely for TTFT; the doc does not publish the edges for
`vllm:request_time_per_output_token_seconds`, so the 50 ms TPOT threshold
remains unverified against its own bucket set.
and **if your SLO threshold is not a bucket edge the query silently measures the
wrong threshold.** Method to resolve: `curl -s :8000/metrics | grep
request_time_per_output_token_seconds_bucket` on the actual build and pick the
SLO to land on an edge, or override the buckets at launch. This is the single
most common way an inference SLO dashboard lies.

**Per-tenant error budgets.** Availability burned by one tenant's malformed
requests should not page. Split the SLI by the gateway's tenant label before
computing burn rate, and exclude 4xx. The Envoy AI Gateway path gives you
`x-tenant-id` as a rate-limit selector already
[[src]](https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting);
reuse the same header as a metric label.

### 1.4 How vendors publish inference SLOs

Almost nobody publishes a *latency* SLO. The pattern as of 2026-09-19:

| Vendor | Availability | Latency | Notes |
|---|---|---|---|
| OpenAI | status page with per-component monthly uptime | none contractual | OpenAI's own help centre historically said SLAs were forthcoming ⚠️ **TO BE VERIFIED** — not fetched, search-surfaced only |
| Anthropic | enterprise agreement ⚠️ | none published | figures circulating in third-party aggregators are *observed* probe uptime, not contractual — treat as unsourced |
| Cloud-hosted (Azure OpenAI etc.) | platform SLA on the *endpoint*, not the model | none | ⚠️ not fetched |

**The takeaway for a self-hosted cluster:** you are being compared against a
number nobody publishes, so publish yours internally and measure it from outside
the gateway. A blackbox prober issuing the canary prompts from §2.8 against the
public VIP is the only availability SLI that survives a gateway outage — an
SLI computed from engine metrics reports 100 % availability while the gateway is
down, because the engine never saw the requests.

---

## 2. Failure modes and detection

### 2.1 The catalogue, ranked by what actually pages you

| # | Failure | First signal | Mean blast radius here | §|
|---|---|---|---|---|
| 1 | GPU Xid (fatal class) | `DCGM_FI_DEV_XID_ERRORS`, `dmesg` | 1 GPU → 1 replica (Kimi-K3: 1 node) | 2.2 |
| 2 | ECC DBE / row-remap failure | Xid 48 / 63 / 64 / 140 | 1 GPU, needs reset or reboot | 2.3 |
| 3 | NVLink / NVSwitch fault | Xid 74 / 155, SXid | whole TP replica | 2.4 |
| 4 | InfiniBand link flap | fabric counters, NCCL retries | multi-node replicas only | 2.5 |
| 5 | NCCL hang in a TP replica | flat throughput, no error | whole replica, **silent** | 2.6 |
| 6 | KV OOM / preemption storm | `vllm:num_requests_waiting`, preemption log | SLO, not availability | 2.7 |
| 7 | Engine deadlock | requests running, tokens flat | whole replica, **silent** | 2.6 |
| 8 | Tokenizer / chat-template bug | quality, not latency | **all replicas at once** | 2.8 |
| 9 | Quality regression after upgrade | evals only | **all replicas at once** | 2.8 |
| 10 | Slow weight load | pod not Ready for minutes | capacity, during rollouts | 2.9 |

Rows 5, 7, 8 and 9 are the dangerous ones: **no error is emitted**. Everything in
§2.6 and §2.8 exists because Prometheus counters only detect failures that
*count something*.

### 2.2 Xid errors: the catalogue is the runbook

NVIDIA publishes a machine-readable Xid catalogue with a **resolution bucket per
code**, which is exactly what an automated remediation controller needs. The
table columns are `Type (XID) | Code | Mnemonic | Description | Applies to A100 |
Applies to H100 | Applies to B100 | Applies to GB200 | Resolution Bucket
(Immediate Action) | Resolution Bucket (Investigatory Action) | Xid 154 linkage |
Trigger Conditions` [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)
(page last updated 2026-09-09).

The codes that matter for an inference fleet, quoted verbatim from that catalogue:

| Xid | Mnemonic | Description | Immediate action | Investigatory | Applies A100/H100/B100/GB200 |
|---:|---|---|---|---|---|
| 13 | `ROBUST_CHANNEL_GR_EXCEPTION` | Graphics Engine Exception | `RESTART_APP` | `WORKFLOW_XID_13` | Y/Y/Y/Y |
| 31 | `ROBUST_CHANNEL_FIFO_ERROR_MMU_ERR_FLT` | GPU memory page fault | `RESTART_APP` | `WORKFLOW_XID_31` | Y/Y/Y/Y |
| 43 | `ROBUST_CHANNEL_RESETCHANNEL_VERIF_ERROR` | GPU stopped processing | `IGNORE` | `CONTACT_SUPPORT` | Y/Y/Y/Y |
| 48 | `ROBUST_CHANNEL_GPU_ECC_DBE` | Double Bit ECC Error | `WORKFLOW_XID_48` | `WORKFLOW_XID_48` | Y/Y/Y/Y |
| 62 | `PMU_HALT_ERROR` | Internal micro-controller halt | `RESET_GPU` | `CONTACT_SUPPORT` | Y/Y/Y/Y |
| 63 | `INFOROM_DRAM_RETIREMENT_EVENT` | GPU memory remapping event | `IGNORE` | `IGNORE` | Y/Y/Y/Y |
| 64 | `INFOROM_DRAM_RETIREMENT_FAILURE` | GPU memory remapping failure | `RESET_GPU` | `CONTACT_SUPPORT` | Y/Y/Y/Y |
| 74 | `NVLINK_ERROR` | NVLINK Error | `WORKFLOW_NVLINK_ERR` | `CONTACT_SUPPORT` | Y/Y/N/N |
| 79 | `ROBUST_CHANNEL_GPU_HAS_FALLEN_OFF_THE_BUS` | GPU has fallen off the bus | `RESTART_BM` | `CONTACT_SUPPORT` | Y/Y/Y/Y |
| 92 | `EXCESSIVE_SBE_INTERRUPTS` | High single-bit ECC error rate | `IGNORE` | `CONTACT_SUPPORT` | Y/Y/Y/Y |
| 93 | `INFOROM_ERASE_LIMIT_EXCEEDED` | Non-fatal violation of provisioned InfoROM wear limit | `IGNORE` | `CONTACT_SUPPORT` | Y/N/N/N |
| 94 | `ROBUST_CHANNEL_CONTAINED_ERROR` | Contained memory error | `RESTART_APP` | `IGNORE (sympathetic)` | Y/Y/Y/Y |
| 95 | `ROBUST_CHANNEL_UNCONTAINED_ERROR` | Uncontained memory error | `RESET_GPU` | `IGNORE (sympathetic)` | Y/Y/Y/Y |
| 119 | `GSP_RPC_TIMEOUT` | GSP RPC Timeout | `RESET_GPU` | `INVESTIGATE_SW` | Y/Y/Y/Y |
| 120 | `GSP_ERROR` | GSP Error | `RESET_GPU` | `INVESTIGATE_SW` | Y/Y/Y/Y |
| 121 | `C2C_ERROR` | C2C Error | `IGNORE` | `CONTACT_SUPPORT` | N/N/N/**Y** |
| 140 | `UNRECOVERABLE_ECC_ERROR_ESCAPE` | ECC Unrecovered Error | `RESET_GPU` | `CONTACT_SUPPORT` | Y/Y/Y/Y |
| 154 | `GPU_RECOVERY_ACTION_CHANGED` | GPU Recovery Action Changed | `XID_154` | *"N/A Informational only regarding another Xid"* | Y/Y/Y/Y |
| 155 | `NVLINK_SW_DEFINED_ERROR` | NVLINK: SW Defined Error | `RESET_GPU` | `INVESTIGATE_SW_USER` | N/N/**Y**/**Y** |
| 156 | `RESOURCE_RETIREMENT_EVENT` | Resource Retirement Event | `RESET_GPU` | `IGNORE` | N/Y/Y/Y |
| 157 | `RESOURCE_RETIREMENT_FAILURE` | Resource Retirement Failure | `IGNORE` | `CONTACT_SUPPORT` | N/Y/Y/Y |

All rows [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).

⚠️ **TO BE VERIFIED — the catalogue has no B300 or GB300 column.** Its
applicability columns are A100 / H100 / **B100** / **GB200**. This repo runs
**B300 (sm_103)** and references GB300 NVL72. Estimation method: treat the B100
column as the Blackwell-generation proxy for HGX B300 and the GB200 column as
the proxy for GB300 NVL72 (both are Grace-coupled, so Xid 121 `C2C_ERROR`
applies to GB300 and *not* to HGX B300, which has no Grace C2C link). Confirm
against `nvidia-smi -q` output and the driver release notes on the actual fleet
before encoding the mapping into a remediation controller.

**Xid 154 is the one to automate on.** Rather than maintaining your own
code→action mapping, read the action the driver itself computed. From the
catalogue: *"Xid 154 will be seen in conjunction with other Xids and summarizes
the recovery action required for other Xids. The string will be similar to
`Xid 154 GPU recovery action changed from 0x0 (None) to 0x2 (Node Reboot
Required)` where the expected values of the text are: `None`, `Drain P2P`,
`Drain and Reset`, `GPU Reset Required`, `Node Reboot Required`."*
[[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)

The catalogue's "Xid 154 linkage" column records `CUDA 12.7; GPU driver R565` for
most rows that carry it — i.e. the linkage is only emitted from R565 onward
[[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).
**Decision rule:** if your fleet is on R565+ (it must be, for Blackwell), parse
Xid 154's five action strings and drive remediation off those; fall back to the
static per-code table only for codes with no 154 linkage. **Trade-off:** parsing
a human-readable string out of `dmesg` is brittle; NVSentinel (§3.2) does this
for you and exposes a structured `recommendedAction` instead.

**Detection paths, in order of latency:**

1. **DCGM exporter → Prometheus.** `DCGM_FI_DEV_XID_ERRORS` is a gauge holding
   the value of the last Xid seen. ⚠️ **TO BE VERIFIED** (search-surfaced from
   vendor integration docs, primary DCGM field reference not fetched): being a
   *gauge of the last value*, it does not count occurrences, so an alert must
   fire on `changes()` or on the value itself, and two different Xids inside one
   scrape interval collapse to one sample. Estimation method: corroborate every
   DCGM-sourced Xid alert against the syslog signature below before acting.
2. **Kernel log.** NVIDIA's guidance: the messages are logged in the kernel log
   buffer, and you *"Grep for `NVRM: Xid` to find all the Xid messages"*
   [[src]](https://docs.nvidia.com/deploy/xid-errors/working-with-xid-errors.html).
   This is the authoritative source and the only one that carries the Xid 154
   action string.
3. **`nvidia-smi -q`** for ECC counts and remapping state
   [[src]](https://docs.nvidia.com/deploy/xid-errors/working-with-xid-errors.html).

NVIDIA's own triage table is three buckets: suspected user programming issue →
run Compute Sanitizer or CUDA-GDB; suspected hardware problem → contact the
hardware vendor; suspected driver problem → file a bug including the output of
`nvidia-bug-report.sh`
[[src]](https://docs.nvidia.com/deploy/xid-errors/working-with-xid-errors.html).
For a serving fleet the "user programming issue" bucket means *the engine*:
Xid 13 and 31 on a stable engine build point at a kernel bug (a FlashInfer/
FlashMLA path, see [`../cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md)),
not at hardware — which is why §5's engine-upgrade gates matter.

### 2.3 ECC, row remapping, and the "ignore" trap

Three codes look similar and have opposite handling:

- **Xid 63, `INFOROM_DRAM_RETIREMENT_EVENT` → `IGNORE` / `IGNORE`.** The GPU
  successfully remapped a bad row. *"These events are logged when the GPU handles
  ECC memory errors on the GPU. On GPUs that support row remapping, starting with
  NVIDIA Ampere architecture GPUs, these events provide d[etail]…"*
  [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).
  Do **not** page. Do **count** — a GPU emitting 63s at an increasing rate is on
  its way to 64.
- **Xid 64, `INFOROM_DRAM_RETIREMENT_FAILURE` → `RESET_GPU` / `CONTACT_SUPPORT`.**
  The remap failed. The GPU is degraded.
- **Xid 48, `ROBUST_CHANNEL_GPU_ECC_DBE` → `WORKFLOW_XID_48`.** *"This event is
  logged when the GPU detects that an uncorrectable error occurs on the GPU. This
  is also reported back to the user application. A GPU reset or node reboot is
  ne[eded]"* [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).

**The operational rule that falls out:** alert on the *rate* of 63, page on 64,
92 (`EXCESSIVE_SBE_INTERRUPTS`, immediate `IGNORE` but investigatory
`CONTACT_SUPPORT`) and 157, and treat 48/95/140 as "this replica is already
dead, the only question is reset vs reboot".

**Why this matters more on B300 than on H100 here.** Kimi-K3 occupies 195 GB of
268 GB per GPU ([`../models/kimik3/b300.md`](../models/kimik3/b300.md)). Row
remapping retires memory. Enough retirements and the weights no longer fit — the
pod will fail to start with an allocation error that looks nothing like an ECC
problem. Track available framebuffer as a first-class capacity metric on any GPU
running a model that uses > 70 % of HBM, and treat a drop as a hardware ticket.

### 2.4 NVLink and NVSwitch

Two codes, split by generation:

- **Xid 74 `NVLINK_ERROR` → `WORKFLOW_NVLINK_ERR` / `CONTACT_SUPPORT`**, applies
  to A100 and H100 only (`Y/Y/N/N`). *"This event is logged when the GPU detects
  that a problem with a connection from the GPU to another GPU or NVSwitch over
  NVLink. A GPU reset or node reboot is needed to clear this error."*
  [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)
- **Xid 155 `NVLINK_SW_DEFINED_ERROR` → `RESET_GPU` / `INVESTIGATE_SW_USER`**,
  applies to **B100 and GB200 only** (`N/N/Y/Y`), gated at `CUDA 12.7; GPU driver
  R565`. Trigger condition, verbatim: *"Link down events which are flagged as
  'intentional' (including transitions to SLEEP) will trigger this Xid"*
  [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).

That trigger condition is a **false-positive generator on Blackwell**: an
intentional NVLink SLEEP transition raises Xid 155, whose immediate bucket is
`RESET_GPU`. A naive controller that resets a GPU on every Xid-155 will reset
healthy idle GPUs. **Decision rule:** on B300/GB300, gate Xid 155 remediation on
a *corroborating* signal — NCCL failure, DCGM NVLink health `Fail`, or traffic
actually in flight — and never on the Xid alone. ⚠️ **TO BE VERIFIED** that this
generalises from the B100 column to sm_103 B300; method as in §2.2.

**NVSwitch errors are SXids, and they are documented elsewhere.** NVIDIA: the
drivers for NVSwitch *"report error conditions relating to NVSwitch hardware in
kernel logs through a similar mechanism to Xids, and these 'Switch Xids', or
SXids and guidelines for their usage are documented separately in the Fabric
Manager User Guide"* ⚠️ — quoted from a search summary of the Xid docs, the
Fabric Manager User Guide itself was not fetched. Practical consequence: your
log-signature detector needs **two** regexes, `NVRM: Xid` and the SXid form, and
your Prometheus rules need DCGM's NVSwitch health watch (§4.4), because an
NVSwitch fault shows up as *every GPU on the node failing collectives* with no
per-GPU Xid.

**DCGM covers this passively.** The health module's `n` watch fires when
*"An NVLink reported 1 or more errors. This includes datalink CRC, recovery, and
replay errors (pre-Blackwell GPUs) and link recovery events (Blackwell+ GPUs)"*,
when *"One or more NVLinks is being reported as down"*, and on fabric state —
*"A GPU has a non-ready fabric state"* / *"The fabric health mask reports an
unhealthy state"*
[[src]](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/health-monitoring.html)
(corrected 2026-09-19 — the earlier one-line quote is not on that page). **Note
the Blackwell split**: on B300 the NVLink watch counts *link recovery events*,
not datalink CRC/replay errors, which is the same signal class as the Xid 155
false-positive problem above. Enable with the `n` flag:

```bash
# Enable PCIe + memory + InfoROM + thermal/power + NVLink watches on group 1
dcgmi health -g 1 -s pmitn
# Check current health; Healthy / Warning / Failure
dcgmi health -g 1 -c
# Show which watches are enabled
dcgmi health -g 1 --fetch
```

Flags: `p` PCIe, `m` memory, `i` InfoROM, `t` thermal/power, `n` NVLink, `d`
driver, `e` IMEX daemon, `x` ConnectX, `a` all
[[src]](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/health-monitoring.html).
The result states, verbatim (**corrected 2026-09-19 — the page names the
healthy state `Healthy`, not `Pass`**): **Healthy** — *"A `Healthy` result means
that no enabled health rule found an incident in the retained data"*;
**Warning** — *"an issue has been detected that won't prevent current work from
completing, but the issue should be examined and potentially addressed in the
future"*; **Failure** — *"a critical issue has been detected and the current
work is likely compromised or interrupted"*
[[src]](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/health-monitoring.html).

The `e` (IMEX daemon) watch is specific to multi-node NVLink domains — relevant
to GB300 NVL72, not to HGX B300, whose NVLink domain stops at the node boundary
([`../gpus/gb300.md`](../gpus/gb300.md)).

### 2.5 InfiniBand / RoCE link flaps

The inter-node fabric only matters for replicas that span nodes. On this repo's
hardware that is: **nothing in the pinned shapes.** Kimi-K3 fits in one node,
DeepSeek-V4.1-Flash needs 2–4 GPUs (min TP2, rec TP4), Qwen and Marlin are single-GPU
([`../matrix/fit-matrix.md`](../matrix/fit-matrix.md)). The fabric matters for
(a) disaggregated prefill/decode, where KV moves over RDMA
([`../cross-cutting/serving-optimizations.md` §3.5](../cross-cutting/serving-optimizations.md)),
(b) a Mooncake-style KV pool (§3.6), and (c) weight loading from network storage.

Detection is counter-based, not event-based: a flapping link produces symbol
errors, link recovery events, and width/rate renegotiation long before it
produces a hard link-down. ⚠️ **TO BE VERIFIED** — the InfiniBand-specific
thresholds below are from search summaries of vendor and community
troubleshooting material, not from a fetched NVIDIA UFM primary source:

- The tools are `ibdiagnet`, `ibnetdiscover`, `iblinkinfo`, `perfquery` ⚠️.
- A commonly cited threshold derived from the IB spec's 10⁻¹² BER target is
  ~120 symbol errors/hour maximum ⚠️. Estimation method: treat this as an order-
  of-magnitude gate, alert on *slope* rather than absolute count, and confirm the
  number against the UFM Enterprise User Manual before writing it into an alert.
- 400G/800G DAC cables pushed past rated reach produce **pre-FEC errors that never
  surface as a link down** ⚠️ — the failure presents as NCCL slowness, not as a
  fabric alarm.

**The operationally useful framing:** IB degradation in an inference fleet
presents as *§2.6, a NCCL hang or slowdown*, and the fabric counters are how you
distinguish "the fabric is sick" from "the engine is deadlocked". Runbook §8.10.

### 2.6 The silent failures: NCCL hangs and engine deadlocks

This is the failure class that Prometheus counters miss, because the metric that
should fire is a *counter that stopped incrementing*, and nothing alerts on that
by default.

**Signature of a hung TP replica:**

```promql
# Requests are admitted and "running", but no tokens come out.
(vllm:num_requests_running{model_name="$m"} > 0)
and
(rate(vllm:generation_tokens_total{model_name="$m"}[5m]) == 0)
```

That single rule catches NCCL hangs, engine deadlocks, a wedged CUDA context
after a contained Xid 94, and a GPU that fell off the bus mid-request. It is the
highest-value alert in this document and it costs nothing.

**NCCL hang mechanics.** PyTorch converts collective timeouts into exceptions
only when async error handling is on; the canonical env var since PyTorch 2.2 is
`TORCH_NCCL_ASYNC_ERROR_HANDLING=1`, with the pre-2.2 name
`NCCL_ASYNC_ERROR_HANDLING` still accepted ⚠️ **TO BE VERIFIED** (search-surfaced,
PyTorch docs not fetched). Default-off means *"hangs are silent"* ⚠️. The
inference-specific tuning differs sharply from training:

> **Decision rule — NCCL timeout for serving.** Training guidance is 30 minutes
> (`NCCL_TIMEOUT=1800`) because a false-positive timeout costs a checkpoint
> restart ⚠️. **For inference that is catastrophically wrong**: a hung TP replica
> that takes 30 minutes to die burns 30 minutes × G GPUs of capacity and 30
> minutes of error budget. Set the collective timeout to a small multiple of your
> worst legitimate collective — which for decode is a per-step allreduce on the
> order of milliseconds, and for prefill is bounded by `max_num_batched_tokens`.
> **Trade-off:** too low and a long chunked-prefill step or a cold CUDA-graph
> capture trips it. Start at 120 s, measure the p99.99 collective duration from a
> load test at your real `max_num_batched_tokens`, and set the timeout to 10× that.
> ⚠️ **TO BE VERIFIED** — this is a derived recommendation, not a published value.

**Restart-on-hang.** NVIDIA's Resiliency Extension (NVRx) provides hang
detection, in-process restart *"in seconds without touching the container
lifecycle"*, and `ft_launcher` in-job restart ⚠️ **TO BE VERIFIED** — the NVRx
README and docs were not fetched; the project describes itself as *"still
experimental and under active development"* ⚠️, and its published benchmarks are
**training** (H100, 2–8 nodes, 99 %+ training efficiency) ⚠️. **Do not assume it
applies to a vLLM/SGLang serving process.** For serving, the pragmatic
equivalent is: let the hang detector kill the process, let Kubernetes restart the
pod, and accept the weight-load time (§2.9) as the MTTR — which is why §2.9 is a
reliability topic and not a performance topic.

**Engine deadlocks** are indistinguishable from NCCL hangs at the metric level,
and the discriminator is where the threads are. The triage command is a stack
dump per rank, not a metric (runbook §8.2).

**Health endpoints do not catch this.** vLLM's `/health` *"only indicates that
the server process is running, **not** that models are fully loaded and
operational"* [[src]](https://llm-d.ai/docs/0.7/readiness-probes) — and by the
same logic it does not indicate that the model is still *executing*. SGLang
distinguishes the two explicitly: `GET /health` is a basic check, `POST
/health_generate` *"confirms model generation"* ⚠️ (search-surfaced from the
SGLang issue tracker and docs index; the endpoint reference page itself was not
fetched). `/health_generate` as a **readiness** probe is the right shape — it
fails when the model cannot generate — but as a **liveness** probe it is a
footgun: under legitimate saturation it fails too, and Kubernetes kills a healthy
overloaded pod, converting a latency incident into an availability incident.

> **Decision rule — probes.**
> - **Liveness**: process-level only, or omit it entirely. Never a generation probe.
> - **Readiness**: model-aware. vLLM's `/v1/models` *"returns `503` during model
>   loading, then `200 OK` with model metadata once ready"*
>   [[src]](https://llm-d.ai/docs/0.7/readiness-probes); SGLang's
>   `/health_generate` ⚠️.
> - **Startup**: model-aware with a long budget, so a slow weight load does not
>   look like a crash loop.

llm-d's recommended probe block, verbatim
[[src]](https://llm-d.ai/docs/0.7/readiness-probes):

```yaml
startupProbe:
  httpGet:
    path: /v1/models
    port: 8000
  initialDelaySeconds: 15
  periodSeconds: 30
  failureThreshold: 60        # 30s × 60 = 30 minute maximum model-load window
  timeoutSeconds: 5
readinessProbe:
  httpGet:
    path: /v1/models
    port: 8000
  periodSeconds: 5
  timeoutSeconds: 2
  failureThreshold: 3
```

(`timeoutSeconds` added 2026-09-19 — both fields are in the published block and
were missing from the transcription here.)

30 minutes of startup budget is not paranoia for this fleet: Kimi-K3 is
1,560.9 GB on disk ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)).

### 2.7 KV OOM and preemption storms

This is a **SLO** failure, not an availability failure, and it is the one the
engine tells you about clearly.

vLLM preempts when KV blocks run out. The log line, verbatim:

> `WARNING 05-09 00:49:33 scheduler.py:1057 Sequence group 0 is preempted by PreemptionMode.RECOMPUTE mode because there is not enough KV cache space.`

[[src]](https://docs.vllm.ai/en/stable/configuration/optimization/) — the
trailing cause clause (*"because there is not enough KV cache space"*) was
missing from this quote before 2026-09-19; it is what distinguishes this warning
from an abort.

In V1 the default mode is recompute, so a preempted request **reruns prefill from
scratch**. That is the storm mechanism: preemption frees blocks, the freed
request is readmitted, its prefill consumes compute and re-allocates blocks,
which preempts another request. Throughput collapses while `num_requests_running`
stays high — it looks like a slowdown, not an error.

Detection:

```promql
# Leading indicator: queue growing while KV is nearly full
(vllm:num_requests_waiting{model_name="$m"} > 0)
and
(vllm:kv_cache_usage_perc{model_name="$m"} > 0.95)
```

vLLM: *"You can monitor the number of preemption requests through Prometheus
metrics exposed by vLLM"* and *"you can log the cumulative number of preemption
requests by setting `disable_log_stats=False`"*
[[src]](https://docs.vllm.ai/en/stable/configuration/optimization/). ⚠️ **TO BE
VERIFIED**: the exact preemption metric name in 0.29.0 is not listed in the
metrics design doc's active-metric list
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md);
method — `curl :8000/metrics | grep -i preempt` on the deployed build and pin the
name in your dashboard.

The documented knobs, verbatim from the same page: increase
`gpu_memory_utilization`; decrease `max_num_seqs`; decrease
`max_num_batched_tokens`; increase `tensor_parallel_size` (shards weights, freeing
per-GPU memory — *"may introduce synchronization overheads"*); increase
`pipeline_parallel_size` (*"note: may cause latency penalties"*)
[[src]](https://docs.vllm.ai/en/stable/configuration/optimization/). The
`tensor_parallel_size` row was missing here before 2026-09-19 — it matters,
because on this fleet raising TP is usually the *only* one of the four that does
not cost SLO.

**This repo's KV budgets make the arithmetic concrete.** `max_concurrency(ctx)`
from [`../METHODOLOGY.md` §3](../METHODOLOGY.md) is the *hard* ceiling; running
`max_num_seqs` above it guarantees preemption. The consistency rule in the
methodology — *"a throughput or cost table may only contain batch / concurrency
rows that are ≤ `max_concurrency(ctx)`"* — is an **operational** rule too:

> **Decision rule — admission vs. preemption.** Set `max_num_seqs` at or below
> `max_concurrency(ctx_p99)` for the context length at the 99th percentile of
> your traffic, and let the **gateway** queue the overflow, where it is visible,
> fair, and cancellable. Preemption is the engine silently doing admission
> control with the worst possible policy (recompute the work you already did).
> **Trade-off:** a conservative `max_num_seqs` leaves KV on the floor when the
> traffic mix is short-context. Mitigate with per-tier routing rather than with a
> single high limit.

Two model-specific traps from this tree:

- **Hybrid/linear-attention models allocate fixed per-sequence state.**
  Qwen3.8-27B: GDN state 78.4 MB per slot × S = 5 slots = **392.2 MB per
  request**, SGLang's shipped default. Kimi-K3: KDA 428.6 MiB per slot × S = 5 =
  **2.25 GB per request** ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)). That
  cost is paid at *admission*, independent of context length, so a burst of
  1,000-token requests can exhaust memory on a model whose KV/token is tiny.
  Classic KV-usage dashboards miss it.
- **Speculative decoding adds state.** DeepSeek-V4.1-Flash's SWA ring is
  2,906,112 B = 2.77 MiB per sequence with **43 rings (MTP on)** versus
  2,703,360 B = 2.58 MiB with MTP off ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)
  pin log `C2-deepseek-swa-fixed-state`). If you disable DSpark to debug a
  quality issue, per-sequence footprint *falls* and concurrency *rises* — the
  opposite of the intuition, and enough to change which alert fires.

### 2.8 The invisible class: tokenizer, chat-template and quality regressions

These share one property: **every replica is wrong simultaneously**, because they
ship in the image or the config, not in the hardware. No latency metric moves. No
error counter increments. The `request_success_total{finished_reason="stop"}`
counter may even *improve* (a broken template that emits EOS early looks like
clean stops).

Three sub-classes:

1. **Chat-template drift.** The template lives in the tokenizer config and
   controls the exact special-token sequence the model was trained on. A
   whitespace or role-label change is a silent quality cliff. It is also a
   *cache* cliff: prefix caching keys on tokens, so a template change invalidates
   every cached prefix at once — see runbook §8.8.
2. **Tokenizer mismatch.** A tokenizer revision different from the checkpoint
   revision produces plausible but degraded output and wrong token accounting,
   which propagates into billing and into rate-limit costs (§7.5).
3. **Post-upgrade numerical drift.** An engine or kernel upgrade changes
   reduction order and therefore changes outputs. This is *expected*, not a bug —
   see below — but it is indistinguishable from a real regression without an eval.

**Detection is synthetic canaries plus evals, and nothing else works.**

- **Golden-prompt canary.** A small set of fixed prompts, run at temperature 0
  against every replica on a schedule, with the output hashed and compared. A
  mismatch across replicas at the same version = one bad replica. A mismatch
  across versions = a change to gate on (§5.4).
- **Log signatures.** Template and tokenizer failures usually leave a trace:
  a spike in very short generations, a spike in `finished_reason="length"`
  (`vllm:request_success_total{finished_reason=...}`
  [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)),
  or a collapse in the prompt-token histogram
  (`vllm:request_prompt_tokens`) when the template stops expanding.
- **Prefix-cache hit rate as a template tripwire.** `vllm:prefix_cache_hits /
  vllm:prefix_cache_queries` [[src]](https://docs.vllm.ai/en/stable/design/metrics/)
  falling off a cliff at a deploy boundary, with no traffic-mix change, is a
  template change. SGLang exposes the same thing directly as
  `sglang:cache_hit_rate` [[src]](https://docs.sglang.io/references/production_metrics.html).

**Bit-exactness is not free, and you should know why your outputs move.**
Thinking Machines Lab established (2025-09-10) that the dominant cause of
nondeterminism at temperature 0 is **batch-size-dependent reductions**, not
atomics: *"the primary reason nearly all LLM inference endpoints are
nondeterministic is that the load (and thus batch-size) nondeterministically
varies!"* [[src]](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/).
Measured on Qwen3-235B, 1,000 completions at temperature 0: *"we generate 80
unique completions, with the most common of these occurring 78 times"*, and
*"the completions are identical for the first 102 tokens"* before diverging
[[src]](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/).
With batch-invariant RMSNorm, matmul and attention kernels, all 1,000 became
identical. The cost, measured: 26 s → 55 s for 1,000 sequences, improved to 42 s
with a better attention kernel [[src]](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/).

> **Decision rule — determinism in production.** ~1.6× latency (42 s vs 26 s) is
> too expensive for the serving fleet. **Run batch-invariant kernels in the eval
> lane only.** Then a golden-prompt diff is a real signal instead of noise, and
> production keeps its throughput. **Trade-off:** the eval lane is then not
> bit-identical to production, so it catches *logic* regressions (template,
> tokenizer, routing, quantization) and not *numerical* ones. Catch numerical
> drift with a distributional check (§5.4 logprob diffs), not with a hash.
> Reference implementation: [`thinking-machines-lab/batch_invariant_ops`](https://github.com/thinking-machines-lab/batch_invariant_ops)
> ⚠️ repo not fetched; SGLang published a parallel deterministic-inference effort
> ⚠️ ([LMSYS blog, 2025-09-22](https://www.lmsys.org/blog/2025-09-22-sglang-deterministic/),
> search-surfaced, not fetched).

### 2.9 Slow weight load

A cold replica that takes minutes to load is a reliability problem, because
every recovery path in §3 ends with "start a new replica". The MTTR of a node
reboot is dominated by weight load, not by the reboot.

Scale of the problem on this fleet
([`../METHODOLOGY.md` §8](../METHODOLOGY.md)):

| Model | Checkpoint bytes | Time to read at 5 GB/s NVMe (est.) | at 25 GB/s (est.) |
|---|---:|---:|---:|
| Marlin-2B | 5.444 GB | 1.1 s | 0.2 s |
| Qwen3.8-27B (BF16) | 55.56 GB | 11 s | 2.2 s |
| DeepSeek-V4.1-Flash | 510.29 GB | 102 s | 20 s |
| DeepSeek-V4.1-Flash-NVFP4 | 527.27 GB | 105 s | 21 s |
| Kimi-K3 | 1,560.9 GB | **312 s** | **62 s** |

`est.` — `bytes / bandwidth`, per [`../METHODOLOGY.md`](../METHODOLOGY.md)'s
convention. The bandwidth figures are placeholders: ⚠️ **TO BE VERIFIED**, the
actual aggregate NVMe read bandwidth of this repo's B300 nodes is not documented
in this tree. Method: `fio` sequential read across all NVMe devices, then divide.
**This is the most load-bearing unmeasured number in the document** — it sets
MTTR for every model, and for Kimi-K3 it is the difference between a 1-minute
and a 5-minute node-level outage.

Mitigations, in order of effort:

1. **Parallel streaming instead of sequential reads.** vLLM supports the Run:ai
   Model Streamer: `--load-format runai_streamer` (or
   `--load-format runai_streamer_sharded` for sharded checkpoints), tuned through
   `--model-loader-extra-config` as JSON with `concurrency` (*"the level of
   concurrency and number of OS threads reading tensors from the file to the CPU
   buffer"* — corrected 2026-09-19), `memory_limit` (CPU buffer size, example
   `5368709120` bytes) and `distributed` (*"whether distributed streaming should
   be used"*, CUDA and ROCm)
   [[src]](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/).
   The vLLM docs give no measured load times
   [[src]](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/);
   third-party writeups report ~6× versus the HF safetensors loader ⚠️ (Azure/AKS
   engineering blogs, search-surfaced, not fetched — treat as vendor-claimed).
2. **Keep weights on local NVMe, not on object storage.** This repo's nodes have
   local NVMe; use it as the authoritative copy and object storage only as the
   source of truth for *distribution*.
3. **Don't recompile on every start.** vLLM reuses the compile cache via
   `VLLM_CACHE_ROOT` (default `~/.cache/vllm`), and `VLLM_FORCE_AOT_LOAD=1`
   makes a cache miss *fail* rather than silently recompile
   [[src]](https://docs.vllm.ai/en/stable/configuration/optimization/).
   `VLLM_FORCE_AOT_LOAD=1` in production is a correctness control as much as a
   speed one: it turns "this pod is mysteriously 4 minutes slower to start" into
   a loud failure.
4. **`--enforce-eager` only for triage.** It *"skips both compilation and
   CUDA-graph capture for fastest startup"*
   [[src]](https://docs.vllm.ai/en/stable/configuration/optimization/) — useful
   to bisect a startup problem, never in production (it costs decode throughput).

---

## 3. Recovery

### 3.1 The recovery ladder

| Fault class | Cheapest sufficient action | Cost |
|---|---|---|
| Xid 13 / 31 / 94, user-app class | restart the engine process (`RESTART_APP`) | one replica, weight-load time |
| Xid 62 / 64 / 95 / 119 / 120 / 140 / 155 | GPU reset (`RESET_GPU`) | one GPU, needs all its pods gone first |
| Xid 79 | bare-metal reboot (`RESTART_BM`) | whole node |
| Xid 48 | `WORKFLOW_XID_48` — reset or reboot | whole node in practice |
| Xid 63 / 92 / 93 / 121 / 157 | none (`IGNORE`), count it | zero |
| NVSwitch SXid | node out of service | whole node |
| Engine deadlock / NCCL hang | kill process, recreate group | whole replica |

Resolution buckets [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).

**The GPU-reset precondition is the operational crux.** A GPU cannot be reset
while a process holds a CUDA context on it. So "reset the GPU" always decomposes
into *drain the pods using that GPU* → *reset* → *allow scheduling*. That is
precisely the partial-drain problem, and it is why §3.2's tooling exists.

### 3.2 Cordon, drain, remediate: automating the ladder

NVIDIA ships a Kubernetes controller for exactly this loop. **NVSentinel**
(v1.22.0) is *"a GPU fault detection and remediation system for Kubernetes"*;
per its docs it runs *"across AWS, GCP, Azure, and OCI on clusters up to 1,100+
nodes and ~40,000 GPUs"*, and *"Detection, quarantine, drain, and remediation
happen automatically, typically completing in minutes"*
[[src]](https://docs.nvidia.com/nvsentinel/getting-started/overview/).

Components [[src]](https://docs.nvidia.com/nvsentinel/getting-started/overview/):

| Layer | Component | What it watches / does |
|---|---|---|
| Detect | GPU health monitor | thermal, ECC, XID events via DCGM |
| Detect | Syslog monitor | kernel panics, driver crashes, NVLink errors |
| Detect | Cloud provider monitor | scheduled maintenance events |
| Detect | Kubernetes object monitor | custom CEL-based signals |
| Act | Fault quarantine | cordons faulty nodes |
| Act | Node drainer | gracefully evicts workloads |
| Act | Fault remediation | creates maintenance workflows |
| Act | GPU reset | *"recovers individual GPUs in seconds vs. rebooting"* |
| Act | Janitor | executes maintenance via APIs or direct commands |

Validated architectures (**re-read 2026-09-19 — the page names the parts, and
B300 and GB300 are both on the list**): *"Volta (V100), Ampere (A100), Hopper
(H100, H200), Ada Lovelace (L4, L40, L40S), and Blackwell (B200, **B300**,
GB200, **GB300**, RTX Pro 6000)"*, with the caveat *"Architectures and GPUs not
listed above have not been formally validated but may work in your environment"*
[[src]](https://docs.nvidia.com/nvsentinel/getting-started/overview/). **This
repo's B300 and GB300 are explicitly validated** — the earlier ⚠️ here is
resolved, and NVSentinel is the *only* tool in this document whose vendor names
sm_103 hardware, which is a real argument for adopting it over a hand-rolled
controller. (The Xid catalogue gap in §2.2 is unaffected: that is a different
document with different columns.) One documented exclusion: the nccl-loopback
preflight check does not support Volta.

**Partial drain is the feature that matters for a mixed fleet.** The Node Drainer
*"determines drain type based on fault severity: partial drains target pods using
unhealthy GPUs (GPU-reset-recoverable faults), while full drains evacuate all
eligible pods (node-reboot-required faults)"*, and it *"automatically skips
DaemonSets and system namespace pods"*
[[src]](https://docs.nvidia.com/nvsentinel/components/node-drainer/).

On this repo's node that maps directly onto the blast-radius table in the
preamble: a partial drain on a node running eight single-GPU Qwen3.8-27B replicas
costs 1/8 of that node's capacity. A partial drain on a node running **one
Kimi-K3 replica across all 8 GPUs is a full drain**, because the one pod uses the
unhealthy GPU. There is no partial mode to reach for.

Node Drainer configuration keys
[[src]](https://docs.nvidia.com/nvsentinel/components/node-drainer/):
`enabled`, `dryRun`, `evictionTimeoutInSeconds`, `systemNamespaces` (regex, e.g.
`"^(nvsentinel|kube-system|gpu-operator)$"`), `deleteAfterTimeoutMinutes`,
`notReadyTimeoutMinutes`, `drainGPUPods`, `partialDrainEnabled`. Three eviction
modes: **AllowCompletion** (*"Wait for pods to terminate gracefully"*, respecting
`terminationGracePeriodSeconds`), **Immediate** (minimal grace, stateless
workloads), **DeleteAfterTimeout** (*"Wait for configured timeout, then force
delete"*) [[src]](https://docs.nvidia.com/nvsentinel/components/node-drainer/).

```yaml
# Per-namespace default: let inference pods finish their in-flight requests.
userNamespaces:
  - name: "*"
    mode: "AllowCompletion"
```

For finer control, `podDrainPolicies`, where *"The first matching policy wins"*
[[src]](https://docs.nvidia.com/nvsentinel/components/node-drainer/):

```yaml
podDrainPolicies:
  - name: finish-training
    namespace: training-*
    podSelector: "example.com/drain-mode=finish"
    mode: AllowCompletion
```

> **Decision rule — eviction mode per model.**
> - Qwen3.8-27B, Marlin-2B (single-GPU, seconds to reload): `AllowCompletion`
>   with a grace period covering the longest legitimate generation.
> - DeepSeek-V4.1-Flash (TP2–TP4, §preamble): `AllowCompletion`. A forced delete mid-request
>   costs the whole replica's in-flight batch, not one request.
> - Kimi-K3 (whole node): `AllowCompletion` **with a bounded
>   `deleteAfterTimeoutMinutes`**. You cannot let one 128K-context request hold a
>   whole node hostage past the point where the node needs a reboot.
>
> **Trade-off:** `AllowCompletion` with a long grace means a node stays partly
> broken for longer; `Immediate` means every in-flight request on the node 500s.
> The tie-breaker is whether the fault is *progressing* (uncontained ECC, thermal)
> — if it is, take `Immediate` and eat the errors.

**Alternatives if you don't adopt NVSentinel.** Node Problem Detector publishes
node conditions and events but *"doesn't take direct action to remediate
GPU-enabled node issues, and any remediation … must be handled manually, through
external automation, or alerting systems"* ⚠️ (Azure AKS docs, search-surfaced,
not fetched). That is the right split if you already have a remediation
controller: NPD detects, your controller acts. The NVIDIA GPU Operator's
`NoSchedule` taint interaction is a known sharp edge — the taint *"prevents the
Operator from deploying the GPU Driver and other Operand pods, requiring
operators to either remove the taints from the nodes or add the taints as
tolerations to the daemon sets"* ⚠️ (GPU Operator troubleshooting docs,
search-surfaced, not fetched). If your cordon mechanism taints with `NoSchedule`,
you can lock the GPU Operator out of the very node it needs to repair.

### 3.3 Draining requests, not just pods

Evicting a pod gracefully is useless if the engine drops its in-flight requests
on SIGTERM. vLLM has an explicit grace period, and **its default is zero**:

```python
# vllm/config/vllm.py
shutdown_timeout: int = Field(default=0, ge=0)
"""Shutdown grace period for in-flight requests. Shutdown will be delayed for
up to this amount of time to allow already-running requests to complete. Any
remaining requests are aborted once the timeout is reached.
"""
```

[[src]](https://github.com/vllm-project/vllm/blob/main/vllm/config/vllm.py)

**Default 0 means every rolling update aborts every in-flight request.** This is
the single highest-value one-line fix in this document.

llm-d documents the full ordering and the invariant
[[src]](https://llm-d.ai/docs/dev/operations/graceful-shutdown):

> *"terminationGracePeriodSeconds must be greater than --shutdown-timeout (plus a
> small buffer for the preStop hook and process cleanup)."*

and the sequence: *termination triggered → InferencePool update (pod removed from
endpoints) → preStop hook executes → SIGTERM sent → terminationGracePeriodSeconds
window opens (default 30s, adjustable)*
[[src]](https://llm-d.ai/docs/dev/operations/graceful-shutdown). The recommended
pairing is `--shutdown-timeout 90` with `terminationGracePeriodSeconds: 120`
[[src]](https://llm-d.ai/docs/dev/operations/graceful-shutdown).

```yaml
# Pod spec fragment — drain in-flight requests before dying.
spec:
  terminationGracePeriodSeconds: 120      # must exceed --shutdown-timeout
  containers:
    - name: vllm
      args: ["--shutdown-timeout", "90"]  # drain for up to 90s after SIGTERM
      lifecycle:
        preStop:
          exec:
            # Endpoint removal and SIGTERM race; sleep long enough that the
            # gateway has stopped sending new requests before the engine starts
            # refusing them.
            command: ["/bin/sh", "-c", "sleep 15"]
```

On the routing side, llm-d's Endpoint Picker evicts *queued* (not yet started)
requests with *"a retryable 503 Service Unavailable (outcome
rejected-shutting-down)"* [[src]](https://llm-d.ai/docs/dev/operations/graceful-shutdown)
— a retryable 503 is correct here, because the request has not consumed any
prefill and can be safely re-dispatched. **In-flight** requests cannot be
retried transparently (tokens have already been streamed), which is why the
engine-side drain matters at all.

> **Decision rule — how long to drain.** `--shutdown-timeout` ≥ p99 of
> `vllm:e2e_request_latency_seconds` for the tier, capped by your rollout budget.
> For S3-shaped traffic (128K in / 2K out, [`../METHODOLOGY.md` §6](../METHODOLOGY.md))
> that p99 can be minutes, and draining honestly means rollouts take hours.
> **Trade-off:** cap the drain and accept aborting the long tail, *or* route
> long-context traffic to a separate pool that is updated on its own, slower
> cadence. The second is better and costs a pool.

### 3.4 Restart budgets and the rolling-update problem

Kubernetes' `maxUnavailable`/`maxSurge` semantics assume replicas are cheap and
interchangeable. Neither holds here.

| Model | Replica size | Can you surge? | Practical update strategy |
|---|---|---|---|
| Marlin-2B, Qwen3.8-27B | 1 GPU | Yes, if one spare GPU exists | `maxSurge: 1, maxUnavailable: 0` |
| DeepSeek-V4.1-Flash | 2–4 GPUs (TP2 batch / TP4 interactive) | Only with ≥ 1 spare quarter- or half-node | `maxSurge: 1, maxUnavailable: 0` if spare exists, else `maxUnavailable: 1` |
| Kimi-K3 | 8 GPUs = 1 node | **Only with a spare node** | `maxSurge: 1` requires an idle node; otherwise you take capacity down to update |

**`maxSurge: 1` on a node-sized replica means buying a node.** This is the
clearest place where the fit results in [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md)
become a budget line: Kimi-K3's minimum deployable unit is 8 GPUs, so
zero-downtime updates cost 8 more.

**PodDisruptionBudgets are necessary but not sufficient.** A PDB stops the
*eviction API* from taking too many replicas at once, which protects you from
voluntary disruptions (drains, node upgrades). It does nothing about involuntary
ones (§2), and it does nothing about a replica that is "available" in Kubernetes'
eyes but hung (§2.6). Pair a PDB with the hung-replica alert.

**Multi-node replicas need group semantics.** LeaderWorkerSet models a replica as
a leader plus N−1 workers, with three restart policies
[[src]](https://lws.sigs.k8s.io/docs/concepts/leaderworkerset/failure-handling/):

- **`RecreateGroupOnPodRestart`** (default): *"When any pod in a group fails or
  restarts, the entire replica group (leader + all workers) is deleted and
  recreated."* Intended for *"tightly coupled multi-host distributed inference
  and training (e.g., tensor-parallel or pipeline-parallel models)"* (quote
  corrected 2026-09-19).
- **`None`**: *"Only the failed pod is restarted or rescheduled. Other pods in the
  group continue running without interruption."* Intended for *"loosely coupled
  workers or workloads with application-level fault tolerance"* — which a TP
  replica is not.
- **`RecreateGroupAfterStart`** (LWS 0.9.0+): *"When any pod in a group fails,
  the entire group is recreated if and only if there are no pods currently
  pending in the group."* It *"prevents restart cascades during the initial
  rollout"* and targets *"workloads with large container images or long startup
  times"* (quotes corrected 2026-09-19).

> **Decision rule — LWS restart policy.** For a TP replica, `None` is wrong: a
> tensor-parallel group missing a rank cannot serve, and the surviving ranks sit
> idle holding GPUs. Use `RecreateGroupOnPodRestart`. Then, because this fleet has
> *"long startup times"* (§2.9: Kimi-K3 at 1,560.9 GB), switch to
> `RecreateGroupAfterStart` if you observe recreation cascades during rollout.
> **Trade-off:** `RecreateGroupAfterStart` delays legitimate recovery while any
> pod is still pending, which on a busy cluster can be a while.

**Topology matters for multi-node replicas.** Kueue's Topology Aware Scheduling
(v0.14+, beta, enabled by default) lets you require that a group lands inside one
network domain [[src]](https://kueue.sigs.k8s.io/docs/concepts/topology_aware_scheduling/):

```yaml
apiVersion: kueue.x-k8s.io/v1beta2
kind: ResourceFlavor
metadata:
  name: "tas-flavor"
spec:
  topologyName: "default"
```

with PodTemplate annotations `kueue.x-k8s.io/podset-required-topology` (*"Enforces
all pods within a topology domain level"*),
`kueue.x-k8s.io/podset-preferred-topology` (*"Prefers same domain but allows
distributed scheduling if needed"*),
`kueue.x-k8s.io/podset-unconstrained-topology`, and
`kueue.x-k8s.io/podset-group-name`, against node labels of the
`cloud.provider.com/topology-block` / `-rack` / `node-group` shape
[[src]](https://kueue.sigs.k8s.io/docs/concepts/topology_aware_scheduling/).
Kueue also documents node **hot swap**, under its real feature name
**`TASFailedNodeReplacement`** (beta, enabled by default — corrected
2026-09-19): when a node becomes unavailable Kueue looks for a replacement node
matching the original topology assignment rather than rescheduling the whole
workload, and the `TASFailedNodeReplacementFailFast` gate limits that to one
attempt before the workload is evicted and requeued
[[src]](https://kueue.sigs.k8s.io/docs/concepts/topology_aware_scheduling/)
— directly relevant to a multi-node replica losing one node. The published
`ResourceFlavor` example also carries `nodeLabels` alongside `topologyName`.

For this repo: an 8-GPU Kimi-K3 replica is single-node and therefore needs
`podset-required-topology` at the **host** level, which is trivially satisfied.
The annotation earns its keep only if you later split a replica across two nodes
(e.g. wide-EP experiments, [`../cross-cutting/serving-optimizations.md` §3.4](../cross-cutting/serving-optimizations.md)),
where a rack-crossing placement silently converts NVLink-speed collectives into
InfiniBand-speed ones.

### 3.5 Hot spares

| Spare type | Ready in | Cost when idle | Use for |
|---|---|---|---|
| Hot (weights loaded, no traffic) | seconds | full GPU-hour rate | Kimi-K3, DeepSeek interactive tier |
| Warm (pod scheduled, weights on local NVMe, not loaded) | §2.9 load time | GPU held, no compute | DeepSeek long-context tier |
| Cold (node in the pool, nothing scheduled) | boot + schedule + load | node cost only | Qwen3.8-27B, Marlin-2B |

At the B300 `low` price tier ([`../cross-cutting/cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)),
a hot spare node for Kimi-K3 adds 8 GPU-hours per hour to the bill with zero
tokens served. Compare against the alternative: with no spare, Kimi-K3's MTTR
for a node-reboot-class Xid is `reboot + schedule + 1,560.9 GB load` — the last
term alone is 62–312 s (§2.9). **Decision rule:** buy the hot spare only if
Kimi-K3's availability SLO is tighter than `1 − (λ_node × MTTR)`; otherwise run
cold and publish the lower SLO (§1.2). Do the arithmetic with *your measured* λ,
not with a vendor AFR.

### 3.6 Session continuity: KV checkpointing

When a replica dies, in-flight requests die with it, and multi-turn sessions lose
their cached prefix — the next turn pays a full prefill. A shared KV tier turns
that from a *correctness/latency cliff* into a cache miss on one node.

Mooncake Store, integrated into vLLM through the existing `KVConnector`
interface, is the reference design. Architecture (quotes re-checked and
corrected 2026-09-19)
[[src]](https://vllm.ai/blog/2026-05-06-mooncake-store): a **Master Server**
managing *"KV block metadata, service discovery, and client health"*;
**distributed clients** on GPU nodes that *"manage local CPU/DRAM/SSD resources"*
and connect to each other over RDMA; and a **Transfer Engine** doing *"GPUDirect
RDMA reads and writes through the Mooncake client without using SMs or staging
through CPU memory"*, with multi-NIC pooling. Scheduler-side it hashes
prompt tokens and queries the master for matching blocks; worker-side a Mooncake
client per GPU worker moves data on background threads
[[src]](https://vllm.ai/blog/2026-05-06-mooncake-store).

Measured, on Codex agentic traces, 1P1D on **12 GB200 GPUs**
[[src]](https://vllm.ai/blog/2026-05-06-mooncake-store):

| Metric | Result |
|---|---|
| Throughput | **3.8×** |
| P50 TTFT | **46× reduction** |
| End-to-end latency | **8.6× reduction** |
| Cache hit rate | 1.7 % → **92.2 %** |

and scaling to 60 GB200 GPUs with round-robin routing reached *">95 % cache hit
rate and near-linear throughput scaling"*
[[src]](https://vllm.ai/blog/2026-05-06-mooncake-store).

Three caveats before reading those numbers across:

1. **The workload is the result.** Agentic traces have enormous shared prefixes;
   the baseline hit rate was 1.7 %, so the 46× TTFT win is mostly "we stopped
   recomputing a prefix we already had". On traffic without shared prefixes the
   win approaches zero. Cross-reference the hit-rate scenarios in
   [`../cross-cutting/serving-optimizations.md` §1.2](../cross-cutting/serving-optimizations.md)
   rather than assuming 92 %.
2. **The hardware is GB200, not B300.** GB200 has a Grace-coupled memory path
   that HGX B300 does not. ⚠️ **TO BE VERIFIED** whether the HBM→host transfer
   rates that make this work hold on HGX B300's PCIe-attached hosts.
3. **It is a cache, not a checkpoint.** Mooncake preserves *prefix reuse* across
   replica loss. It does **not** resume a half-finished generation. Nothing in
   the open stack does, as of 2026-09-19 ⚠️. Session continuity means "the next
   turn is fast", not "the interrupted turn continues".

**Correctness hazard specific to this tree.** Two of these models are not
append-only in their state: Qwen3.8-27B has 48 linear-attention (GDN) layers and
Kimi-K3 has KDA layers ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)). Recurrent
state is not a KV cache and does not reuse by prefix hash the way attention KV
does — see [`../cross-cutting/serving-optimizations.md` §1.3](../cross-cutting/serving-optimizations.md)
("Caching when the state is not append-only"). **Do not assume a KV pool gives
these models cross-replica session continuity** without confirming the connector
handles recurrent state. ⚠️ **TO BE VERIFIED**; method: check whether the
connector round-trips the `S`-slot state (5 slots × 78.4 MB for Qwen3.8-27B,
5 × 428.6 MiB for Kimi-K3) or silently caches only the attention layers, which
would be a correctness bug, not a performance one.

---

## 4. Observability stack

### 4.1 The four planes

| Plane | Source | Retention | Cardinality risk |
|---|---|---|---|
| **Metrics** | engine `/metrics`, gateway, DCGM exporter, router/EPP | 15–30 d raw, 13 mo downsampled | high — `model_name` × `finished_reason` × bucket |
| **Traces** | OTel from gateway → router → engine | 3–7 d, sampled | low if sampled, ruinous if not |
| **Request logs** | gateway (structured), engine (unstructured) | see §7.4 — **shortest of the four** | n/a |
| **GPU telemetry** | DCGM exporter | 15–30 d | medium — per-GPU × per-field |

The rule that keeps this affordable: **metrics are per-replica and per-model,
traces are per-request and sampled, logs are per-request and redacted.** Putting
`request_id` in a metric label is the classic way to take down Prometheus.

### 4.2 Engine metrics — the complete lists

**vLLM (0.29.0 in this tree, [`../cross-cutting/inference-engines.md` §2.1](../cross-cutting/inference-engines.md)).**
Exposed at `/metrics` with the `vllm:` prefix
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md):

| Metric | Type | Meaning (verbatim where quoted) |
|---|---|---|
| `vllm:num_requests_running` | Gauge | *"Number of requests currently running"* |
| `vllm:num_requests_waiting` | Gauge | queue depth (the autoscaling signal, §4.6) |
| `vllm:kv_cache_usage_perc` | Gauge | *"Fraction of used KV cache blocks (0–1)"* |
| `vllm:prefix_cache_queries` | Counter | *"Number of prefix cache queries"* |
| `vllm:prefix_cache_hits` | Counter | *"Number of prefix cache hits"* |
| `vllm:prompt_tokens_total` | Counter | *"Total number of prompt tokens processed"* |
| `vllm:generation_tokens_total` | Counter | *"Total number of generated tokens"* |
| `vllm:request_success_total` | Counter | *"Number of finished requests (by finish reason)"* — labels `stop` / `length` / `abort` |
| `vllm:request_prompt_tokens` | Histogram | input prompt token counts |
| `vllm:request_generation_tokens` | Histogram | generation token counts |
| `vllm:time_to_first_token_seconds` | Histogram | TTFT |
| `vllm:inter_token_latency_seconds` | Histogram | ITL — per streamed output event |
| `vllm:request_time_per_output_token_seconds` | Histogram | TPOT — per finished request |
| `vllm:e2e_request_latency_seconds` | Histogram | end-to-end latency |
| `vllm:request_queue_time_seconds` | Histogram | queue time |
| `vllm:request_prefill_time_seconds` | Histogram | prefill time |
| `vllm:request_decode_time_seconds` | Histogram | decode time |
| `vllm:kv_block_lifetime_seconds` | Histogram | *"how long each sampled block exists"* |
| `vllm:kv_block_idle_before_evict_seconds` | Histogram | *"idle tail after the final access"* |
| `vllm:kv_block_reuse_gap_seconds` | Histogram | *"time between consecutive touches"* |
| `vllm:cache_config_info` | Gauge (info) | cache config as labels — `block_size`, `cache_dtype`, `enable_prefix_caching`, `gpu_memory_utilization`, … |
| `vllm:lora_requests_info` | Gauge (info) | active LoRA adapters |

Deprecated / removed — **do not build dashboards on these**:
`vllm:num_requests_swapped`, `vllm:cpu_cache_usage_perc`,
`vllm:time_in_queue_requests` (superseded by `vllm:request_queue_time_seconds`),
`vllm:tokens_total` (*"Never implemented"*), `vllm:request_params_n`,
`vllm:request_max_num_generation_tokens`
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md).

**Corrected 2026-09-19 — the `vllm:spec_decode_*` family is not deprecated.**
An earlier revision of this section listed `vllm:spec_decode_draft_acceptance_rate`,
`_efficiency`, `_num_accepted_tokens`, `_num_draft_tokens` and
`_num_emitted_tokens` among the deprecated/removed metrics. Re-read of the
metrics design doc: they appear under **"Future Work" as not yet implemented in
V1**, not under deprecation
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md).
The operational consequence is the same — **they are not emitted** — but the
reason matters for how you plan around it: there is nothing to pin a version
against and no deprecation window to ride out, so the SGLang fallback below is
the only dated answer rather than a temporary one. Note also that
[`../matrix/recommendations.md`](../matrix/recommendations.md) §3 prescribes
measuring `vllm:spec_decode_num_{accepted,draft}_tokens_total` for benchmark B1;
**those counter names are not in the design doc's active list either**, and that
cross-document conflict is unresolved (open question 2).

> ⚠️ **TO BE VERIFIED — and this one is expensive.** The
> `vllm:spec_decode_draft_acceptance_rate` family appears in the metrics design
> doc's **"Future Work" / not-implemented-in-V1** list (corrected 2026-09-19;
> this section previously said "deprecated"). Every DeepSeek-V4.1-Flash operating
> point in this tree is costed with **DSpark γ=5** and depends on the acceptance
> rate ([`../cross-cutting/serving-optimizations.md` §2.3](../cross-cutting/serving-optimizations.md)),
> so **losing the acceptance-rate metric means losing the ability to detect that
> speculation has stopped paying.** Method to resolve: `curl :8000/metrics | grep
> -i -E 'spec|accept|draft'` on vLLM 0.29.0 and pin whatever is actually emitted;
> if nothing is, the fallback is the derived ratio
> `rate(generation_tokens_total) / rate(engine_steps)` ⚠️ — which needs a
> per-step counter that may also not exist. SGLang, by contrast, does expose
> `sglang:spec_num_steps` and `sglang:spec_num_draft_tokens`
> [[src]](https://docs.sglang.io/references/production_metrics.html), which is a
> real argument for SGLang on the speculating models if this gap is confirmed.

**SGLang (0.5.20 in this tree).** Enable with `--enable-metrics`; served on the
same port as the server (default 30000) at `/metrics`, labelled `model_name`
[[src]](https://docs.sglang.io/references/production_metrics.html):

| Metric | Type | Description (verbatim) |
|---|---|---|
| `sglang:prompt_tokens_total` | Counter | *"Number of prefill tokens processed"* |
| `sglang:generation_tokens_total` | Counter | *"Number of generation tokens processed"* |
| `sglang:token_usage` | Gauge | *"The token usage"* |
| `sglang:cache_hit_rate` | Gauge | *"The cache hit rate"* |
| `sglang:time_to_first_token_seconds` | Histogram | *"Histogram of time to first token in seconds"* |
| `sglang:e2e_request_latency_seconds` | Histogram | *"Histogram of End-to-end request latency in seconds"* |
| `sglang:time_per_output_token_seconds` | Histogram | *"Histogram of time per output token in seconds"* |
| `sglang:func_latency_seconds` | Histogram | *"Function latency in seconds"* (label `name`) |
| `sglang:num_running_reqs` | Gauge | *"The number of running requests"* |
| `sglang:num_used_tokens` | Gauge | *"The number of used tokens"* |
| `sglang:gen_throughput` | Gauge | *"The generate throughput (token/s)"* |
| `sglang:num_queue_reqs` | Gauge | *"The number of requests in the waiting queue"* |
| `sglang:spec_num_steps` | Gauge | *"Currently active speculative_num_steps"* |
| `sglang:spec_num_draft_tokens` | Gauge | *"Currently active speculative_num_draft_tokens"* |

With `--enable-mfu-metrics`: `sglang:estimated_flops_per_gpu_total`,
`sglang:estimated_read_bytes_per_gpu_total`,
`sglang:estimated_write_bytes_per_gpu_total` (Counters)
[[src]](https://docs.sglang.io/references/production_metrics.html).

**Those three MFU counters are the cheapest MBU/MFU measurement available**, and
this tree's throughput model is built on MBU and MFU planning defaults
([`../METHODOLOGY.md` §4](../METHODOLOGY.md): MBU 0.5–0.7 on *"Blackwell
first-gen software"*). Turning `--enable-mfu-metrics` on converts those
planning assumptions into measurements:

```promql
# Achieved memory-bandwidth utilisation, per GPU. Denominator from
# METHODOLOGY §8: B300 = 8.0 TB/s.
(
  rate(sglang:estimated_read_bytes_per_gpu_total[5m])
  + rate(sglang:estimated_write_bytes_per_gpu_total[5m])
) / 8.0e12
```

⚠️ **TO BE VERIFIED** that "estimated" here means what the roofline means
(the counters are named *estimated*, so SGLang is modelling the bytes, not
counting hardware events). Method: compare against DCGM's memory-utilisation
field on one replica under a fixed load, and record the ratio.

**Dynamo.** Every `dynamo_*` metric from frontend, backend workers and router is
exposed at `/metrics`, with hierarchy labels ⚠️ (Dynamo metrics-catalog page,
search-surfaced, not fetched). KV-router metrics carry `status` ∈
`{ok, parent_block_not_found, block_not_found, invalid_block, capacity_exhausted,
indexer_invariant_violation}` and `event_type` ∈ `{stored, removed, cleared}`,
and only appear when `--router-kv-overlap-score-credit > 0` (the default) and
workers are publishing KV events ⚠️ (same source). `capacity_exhausted` and
`indexer_invariant_violation` are the two to alert on — the first is a sizing
problem, the second is a bug.

### 4.3 Router / gateway metrics

The gateway is the only place that sees requests the engine rejected, queued, or
never received — i.e. the only place that can compute availability (§1.4).
Minimum set:

- Request count and latency by `route`, `model`, `tenant`, `status_class`.
- **Token counts per request**, both directions. Envoy AI Gateway extracts these
  into request metadata: `llmRequestCosts` entries of type `InputToken`,
  `CachedInputToken`, `OutputToken`, `TotalToken`, or a `CEL` expression
  [[src]](https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting).
- Queue depth at the router, distinct from queue depth at the engine.
- Per-endpoint routing decisions and their cause.

llm-d exposes EPP (inference scheduler) metrics alongside model-server metrics,
with *"Ready-to-use queries for dashboards and alerting"* in a PromQL reference
and *"the default EPP Prometheus alerting rules"* shipped as manifests under
`guides/recipes/observability/` [[src]](https://llm-d.ai/docs/operations/observability)
(llm-d v0.9). Adopt those rules rather than writing your own — they encode the
maintainers' knowledge of which EPP conditions matter.

The Gateway API Inference Extension is the standard this layer is converging on:
*"an official Kubernetes project that optimizes self-hosting Generative Models on
Kubernetes"*, defining `InferencePool` and `InferencePoolImport`, with *"a
lightweight Endpoint Picker (EPP) reference implementation designed for
conformance testing"*, consuming *"Metrics and Capabilities: Data provided by
model serving platforms about performance, availability and capabilities to
optimize routing"* — examples given are *"Prefix Cache status and LoRA Adapters
availability"* [[src]](https://gateway-api-inference-extension.sigs.k8s.io/).
⚠️ **TO BE VERIFIED**: the docs reference both `v1` and `v1alpha1` API versions
and the introduction page does not state a GA milestone
[[src]](https://gateway-api-inference-extension.sigs.k8s.io/); pin the version
you deploy, because the CRD names changed across alpha revisions.

**The feedback loop worth understanding.** GAIE-style routing reads engine
metrics (KV usage, queue depth, prefix-cache state) to pick an endpoint. That
means **your metrics pipeline is in the request path's control loop**, not just
in your dashboards. A scrape-interval-scale staleness in KV usage produces
herding: every router sends to the endpoint that was idle 15 s ago. Symptom: the
"hot node" runbook (§8.6). Mitigation: the router should consume a push/event
stream (Dynamo's KV events, llm-d's EPP feed) rather than polling Prometheus.

### 4.4 GPU telemetry

DCGM exporter as a DaemonSet on GPU nodes, scraped by Prometheus. The fields
that earn their place:

| Field | Why |
|---|---|
| `DCGM_FI_DEV_XID_ERRORS` | *"the value of the last XID error encountered"* ⚠️ — see §2.2 caveat |
| `DCGM_FI_DEV_ECC_SBE_VOL`, `DCGM_FI_DEV_ECC_DBE_VOL` | ⚠️ single/double-bit ECC volatile counts |
| `DCGM_FI_DEV_RETIRED_SBE`, `DCGM_FI_DEV_RETIRED_DBE` | ⚠️ retired pages — the §2.3 capacity tripwire |
| `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_MEMORY_TEMP` | ⚠️ thermal, the leading indicator for throttling |
| `DCGM_FI_DEV_POWER_USAGE` | ⚠️ power capping shows up as unexplained TPOT drift |

All five rows ⚠️ **TO BE VERIFIED** — sourced from vendor integration
documentation surfaced by search (Datadog's DCGM integration, GKE's DCGM metrics
guide); the NVIDIA DCGM field-identifier reference itself was not fetched.
Method: `dcgmi dmon -e <field-id>` against the deployed DCGM version, or read
`/metrics` from the exporter and pin the names.

Field selection is not free: the exporter's field set is configurable, and
enabling everything on 8 GPUs × N nodes is a real cardinality cost. Start with
the five above plus SM/memory utilisation, and add profiling fields only during
an investigation.

**Passive watches vs. active diagnostics.** These are different tools with
different costs:

- **`dcgmi health`** is passive — it reads telemetry DCGM is already collecting,
  and is safe to run continuously on a serving node (§2.4).
- **`dcgmi diag`** is active — it *"runs active tests that exercise deployment,
  hardware, and software components"*
  [[src]](https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgmi/dcgmi-diag.html)
  and **must not be run on a GPU that is serving traffic.** Levels
  [[src]](https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgmi/dcgmi-diag.html):

| Level | Names | Purpose (verbatim) |
|---|---|---|
| 1 | `quick`, `short` | *"Quick deployment checks, normally lasting seconds"* |
| 2 | `medium` | *"Medium validation, normally lasting approximately two minutes"* |
| 3 | `long` | *"Long hardware diagnostics, normally lasting approximately 15 minutes"* |
| 4 | `xlong` | *"Extended, longer-running hardware diagnostics"* |

Named tests: `memory`, `pcie`, `diagnostic`, `memory_bandwidth`,
`targeted_stress`, `targeted_power`, `nvbandwidth`, `memtest`, `pulse_test`,
`context_create` [[src]](https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgmi/dcgmi-diag.html).

Exit statuses worth encoding in automation
[[src]](https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgmi/dcgmi-diag.html):
`0` *"The requested diagnostics completed without a reported error"*, `226`
diagnostic ran but reported error, `217` another diagnostic already running,
`215` DCGM could not launch, `205` **condition requiring isolation reported**,
`204` executable not found, `203` terminated by signal, `198` no matching
diagnostic. **`205` is the one that should cordon a node permanently pending
hardware action**; `226` warrants a retry before escalation.

> **Where each level belongs in the lifecycle.**
> - Level 1 (`quick`): a pre-flight gate in the pod's init container before the
>   engine starts. Seconds, so it costs nothing.
> - Level 2 (`medium`): after any Xid-triggered GPU reset, before returning the
>   GPU to the pool. Two minutes is cheap next to a repeat failure.
> - Level 3 (`long`) / 4 (`xlong`): only on a cordoned node, as post-mortem or
>   before RMA. Fifteen-plus minutes of full-power stress is itself a risk on
>   marginal hardware.
>
> **Trade-off:** a level-1 gate on every pod start adds seconds to MTTR on every
> restart, including the benign ones. Worth it — it catches the "GPU came back
> from reset still broken" case that otherwise produces a crash loop.

```bash
# Post-reset validation before returning a GPU to service.
dcgmi diag --run 2 --entity-id gpu:3 --json
echo "exit=$?"     # 0 → return to pool; 205 → cordon and RMA; 226 → retry once
```

### 4.5 Tracing: gateway → router → engine

vLLM implements OpenTelemetry tracing, *"Configured with `--otlp-traces-endpoint`
and `--collect-detailed-traces`"*
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md).
The working setup, verbatim from vLLM's example
[[src]](https://docs.vllm.ai/en/stable/examples/online_serving/opentelemetry/):

```bash
export OTEL_SERVICE_NAME="vllm-server"
export OTEL_EXPORTER_OTLP_TRACES_INSECURE=true
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=grpc://$JAEGER_IP:4317
vllm serve <model> --otlp-traces-endpoint="$OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
```

gRPC is the default transport; for `http/protobuf`
[[src]](https://docs.vllm.ai/en/stable/examples/online_serving/opentelemetry/):

```bash
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://$JAEGER_IP:4318/v1/traces
```

Note the packaging detail: *"The core OpenTelemetry packages
(`opentelemetry-sdk`, `opentelemetry-api`, `opentelemetry-exporter-otlp`,
`opentelemetry-semantic-conventions-ai`) are bundled with vLLM. Manual
installation is not required."*
[[src]](https://docs.vllm.ai/en/stable/examples/online_serving/opentelemetry/)

**Context propagation is what makes it a distributed trace.** vLLM's example
sends requests *"with trace context from a dummy client"* and the resulting trace
has *"2 spans. One from the dummy client containing the prompt text and one from
vLLM containing metadata about the request"*
[[src]](https://docs.vllm.ai/en/stable/examples/online_serving/opentelemetry/).
Your gateway must forward W3C `traceparent`; without it you get two disconnected
single-span traces and none of the value. llm-d documents the full-path
configuration: *"Configure OpenTelemetry across vLLM, the routing proxy, and the
EPP"* [[src]](https://llm-d.ai/docs/operations/observability).

**Detailed traces are opt-in and costly.** `--collect-detailed-traces=all/model/worker`
enables `vllm:model_forward_time_milliseconds` and
`vllm:model_execute_time_milliseconds`, and vLLM's own flag documentation warns
it *"involves use of possibly costly and or blocking operations and hence might
have a performance impact"*
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md).
The span attributes that appear
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md):

```text
-> gen_ai.latency.time_in_scheduler: Double(0.017550230026245117)
-> gen_ai.latency.time_in_model_forward: Double(3.151565277099609)
-> gen_ai.latency.time_in_model_execute: Double(3.6468167304992676)
```

`time_in_model_execute` minus `time_in_model_forward` is *"block/sync across
workers, cpu-gpu sync time and sampling time"*
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)
— i.e. **the TP communication overhead**, the exact quantity
[`../METHODOLOGY.md` §4](../METHODOLOGY.md) budgets at *"~5–15 % for TP=8 over
NVLink, 20–40 % for multi-node TP over InfiniBand"*. Turning detailed traces on
for a 1-in-1000 sample converts that planning default into a measurement, which
is the only way to know whether a DeepSeek-V4.1-Flash **TP4** replica (the
pinned interactive shape — corrected 2026-09-19 from TP8, which this tree never
recommends on B300) is paying 8 % or 25 %. At TP4 on a single 8-GPU NVLink
domain the METHODOLOGY band is the 5–15 % one; the 20–40 % band applies only to
the multi-node shapes this fleet does not run.

> **Decision rule — sampling.** Head-sample at ~0.1–1 % for the steady state.
> Tail-sample 100 % of: errors, requests exceeding the TTFT or TPOT SLO, and
> requests carrying a debug header. Never sample prompts into span attributes in
> production (§7.4) — the client-side span in vLLM's example carries *"the prompt
> text"*, which is exactly what must not leave the request path.

### 4.6 Dashboards: what goes on the wall

Five panels per model, in this order. Anything else is a drill-down.

1. **Goodput and SLO attainment.** Requests/s meeting TTFT *and* TPOT, plus the
   burn-rate gauges from §1.3. This is the only panel that answers "are we OK?".
2. **Saturation, two-dimensional.** `vllm:num_requests_waiting` (compute-bound
   signal) **and** `vllm:kv_cache_usage_perc` (memory-bound signal) on one axis,
   normalised against their thresholds. Inference saturates in two independent
   ways and a single utilisation number hides one of them.
3. **Liveness of the token stream.** `rate(vllm:generation_tokens_total[5m])`
   next to `vllm:num_requests_running`. The §2.6 hang detector, visualised.
4. **Cache economics.** `vllm:prefix_cache_hits / vllm:prefix_cache_queries`, or
   `sglang:cache_hit_rate`. Directly drives cost per token
   ([`../METHODOLOGY.md` §6](../METHODOLOGY.md): a cache hit *"skips prefill
   compute and costs only the KV load / reuse"*).
5. **GPU health rollup.** Per-node Xid rate, DCGM health state, retired pages,
   and available framebuffer (§2.3).

Cost and per-tenant panels are a second screen:

```promql
# $/hour of GPU currently serving this model, using METHODOLOGY §6:
#   cost_per_hour = n_gpus × price_per_gpu_hour
# $PRICE comes from cross-cutting/cloud-pricing.md §5.14 — never a neighbour's row.
count(count by (instance) (vllm:num_requests_running{model_name="$m"})) * $GPUS_PER_REPLICA * $PRICE

# Realised $/1M output tokens, to compare against matrix/cost-matrix.md §2.
(
  count(count by (instance) (vllm:num_requests_running{model_name="$m"}))
  * $GPUS_PER_REPLICA * $PRICE / 3600
) / sum(rate(vllm:generation_tokens_total{model_name="$m"}[5m])) * 1e6
```

⚠️ The realised figure will **not** match the matrix. The matrix numbers are
computed at a specific operating point with a specific MBU; the realised number
includes idle time, preemption waste, and traffic-mix effects. Treat a large gap
as a signal to re-derive, not as an error in either.

### 4.7 Per-tenant metrics without exploding cardinality

Label with **tenant tier**, not tenant identity, in Prometheus. Keep per-tenant
identity in the request log and the trace, where cardinality is a storage cost
rather than a time-series cost. Envoy AI Gateway's `x-tenant-id` selector
[[src]](https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting)
is a rate-limit dimension, not a metric label — resist the temptation to promote
it. If per-tenant time series are genuinely needed, recording-rule them into a
separate, short-retention series and cap the tenant set to the top-N.

### 4.8 Structured request logs

One JSON line per request, emitted by the **gateway** (which sees the whole
lifecycle), not by the engine:

```json
{
  "ts": "2026-09-19T14:03:11.412Z",
  "request_id": "01JX...",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "tenant_tier": "interactive",
  "tenant_id_hash": "sha256:9f2b...",
  "model": "deepseek-ai/DeepSeek-V4.1-Flash",
  "engine_build": "vllm-0.29.0+cu13.4-fi0.x",
  "replica": "ds41f-tp8-7c4d9",
  "input_tokens": 3871, "cached_input_tokens": 3584, "output_tokens": 512,
  "ttft_ms": 612, "tpot_ms": 41.3, "e2e_ms": 21740,
  "finish_reason": "stop",
  "prefix_cache_hit": true,
  "status": 200
}
```

Design notes, each load-bearing:

- **No prompt, no completion.** §7.4.
- **`cached_input_tokens` separately from `input_tokens`**, because they cost
  differently ([`../METHODOLOGY.md` §6](../METHODOLOGY.md): our own cache hit
  *"skips prefill compute and costs only the KV load / reuse — assume 10 % of the
  uncached prefill cost unless measured"*). Envoy AI Gateway can emit exactly
  this split via `CachedInputToken`
  [[src]](https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting).
- **`engine_build`** on every line. Without it, §5.2's "which version regressed?"
  is unanswerable after the fact.
- **`trace_id`** so a log line joins its trace.
- **`tenant_id_hash`**, not the tenant identifier, so logs are useful for
  debugging without being a directory of customers.

### 4.9 Retention and the metrics that must outlive the incident

| Stream | Retention | Reason |
|---|---|---|
| Raw metrics | 15–30 d | incident forensics |
| Downsampled metrics (5 m) | 13 mo | capacity trends, §6.2 regression tracking |
| Traces (sampled) | 3–7 d | incidents are debugged within days |
| Request logs | §7.4 | shortest — a privacy liability that grows |
| **Benchmark results** | **indefinite** | §6.2 — the regression baseline |
| **Eval / golden-prompt outputs** | **indefinite** | §5.4 — the quality baseline |

The bottom two rows are the ones teams forget. A performance regression is only
detectable against a baseline that still exists.

---

## 5. Change management

### 5.1 What actually changes, and how often

| Layer | Cadence | Blast radius | Rollback cost |
|---|---|---|---|
| Routing / gateway config | daily | all models | seconds |
| Engine flags | weekly | one model | pod restart + §2.9 load |
| Engine version | 2–6 weeks | one model | pod restart + load; **quality risk** |
| Attention kernel (FlashInfer/FlashMLA) | with engine | one (model, GPU) pair | as engine |
| CUDA / container base | quarterly | all models on a node | image pull + restart |
| **Driver** | 2–4×/year | **whole node** | **node reboot** |
| Model weights | per release | one model | §5.6 — the expensive one |

Only the driver row requires a node reboot; only the model-weights row moves
hundreds of GB. Everything else is a pod restart, which means everything else is
gated by §2.9 weight-load time. That is the whole shape of change management
here: **rollout speed is bounded by load time, and rollout safety is bounded by
eval coverage.**

### 5.2 The compatibility matrix

The dependency chain is `driver ↔ CUDA ↔ PyTorch ↔ engine ↔ attention kernel`,
and the constraints run in both directions.

**Driver ↔ CUDA.** Minimum driver versions for Linux x86_64
[[src]](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)
(doc last updated 2026-09-09):

| CUDA Toolkit | Minimum driver |
|---|---|
| CUDA 13.x | ≥ 580 |
| CUDA 12.x | ≥ 525 |

Minor version compatibility means *"applications compiled with a CUDA Toolkit
release from within a CUDA major release family can run, with limited
feature-set, on systems having at least the minimum required driver version"*
[[src]](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).
Three documented limitations, each of which bites inference specifically:

1. *"Sometimes features introduced in a CUDA Toolkit version may actually span
   both the toolkit and the driver"*, surfacing as `cudaErrorCallRequiresNewerDriver`
   [[src]](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).
2. *"Applications that compile device code to PTX will not work on older
   drivers"* [[src]](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).
   **This is the one that breaks LLM serving**: JIT-compiling kernel libraries
   (FlashInfer, Triton) emit PTX. A container that "works" on a newer driver can
   fail on an older one in the same fleet, at kernel-JIT time, i.e. *after* the
   500 GB weight load.
3. Device code must be built for the target architecture — *"the target
   architecture argument to NVCC, for example `nvcc -arch=sm_xx`"*
   [[src]](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).
   For this fleet that is **sm_103** (B300) and **sm_100** (B200), which are *not
   interchangeable* ([`../gpus/b300.md`](../gpus/b300.md)); an image built only
   for sm_100 will fall back to PTX JIT on B300 and hit limitation 2.

⚠️ **TO BE VERIFIED — the datacenter driver matrix does not list CUDA 13.x.** The
NVIDIA Data Center "CUDA Toolkit, Driver, and Architecture Matrix" page (dated
2026-09-09) shows *no CUDA 13.x entries*; the most recent toolkit listed is CUDA
12.8, against Blackwell with "Ongoing" driver support
[[src]](https://docs.nvidia.com/datacenter/tesla/drivers/latest/cuda-toolkit-driver-and-architecture-matrix.html).
Two published NVIDIA pages disagree about whether CUDA 13.x is documented for
datacenter GPUs. Method to resolve: read the CUDA Toolkit release notes for the
exact version you ship and the datacenter driver release notes for the branch you
run, and pin both in the table below rather than trusting either summary page.

**Forward compatibility** via the `cuda-compat-<major>-<minor>` package
*"allow[s] applications built with a newer toolkit to run on older base drivers
across major release families, subject to platform and GPU support"*
[[src]](https://docs.nvidia.com/deploy/cuda-compatibility/latest/). Useful when
you cannot reboot nodes on the container's cadence — which, given that a node
reboot on this fleet means draining a Kimi-K3 replica, is most of the time.

**Engine ↔ kernel.** This tree already documents the sharp edges: vLLM's
`nvfp4_ds_mla` path resolves only where `FLASHMLA_MEGA_ATTN_DSV41` is gated on
`capability.major == 10`, i.e. sm_100/sm_103 ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)),
and FlashInfer PR #4955 opened an SM120 NVFP4 sparse-MLA path *"that vLLM's dtype
gate still refuses — re-check per engine version"*
([`../METHODOLOGY.md` §8](../METHODOLOGY.md)). **The operational consequence is
severe and worth stating plainly: an engine upgrade can silently change the KV
layout from 890 B/token to 1,650 B/token**, which changes `max_concurrency` by
~1.85× and therefore changes whether your `max_num_seqs` causes a preemption
storm (§2.7). A version bump is a capacity change.

**The matrix to maintain** — one row per (GPU, model) pair you actually run,
regenerated on every upgrade, stored in git next to the deployment:

| Field | Example | Source of truth |
|---|---|---|
| Driver branch | R580 / R615 | node image |
| CUDA runtime | 13.4 | container |
| PyTorch | — | container lockfile |
| Engine | vLLM `main`/nightly, pinned digest | container tag — **for DeepSeek-V4.1-Flash the tree's 0.29.0 pin does not apply**: the model is only on `main`/nightly ([`../cross-cutting/inference-engines.md` §2.1](../cross-cutting/inference-engines.md), [`../matrix/recommendations.md` §2.1](../matrix/recommendations.md)) |
| Attention kernel + version | FlashMLA / FlashInfer x.y | engine log line at startup |
| Resolved attention backend | `FLASHMLA_MEGA_ATTN_DSV41` | **engine log, per model, per GPU** |
| Resolved KV dtype and bytes/token | 890 B/token | derived — must match [`../METHODOLOGY.md` §8](../METHODOLOGY.md) |
| `max_concurrency(ctx)` | from [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) | recomputed |

The two bold rows are **captured from the running engine, not declared**. Grep
them out of the startup log into an artifact, and fail the canary if they differ
from the previous release without an approved note. That single check catches the
890/1,650 class of regression before it reaches production.

### 5.3 Canary and shadow traffic

**Shadow first, canary second.** They catch different things:

| Technique | Catches | Misses | Cost |
|---|---|---|---|
| **Shadow** (mirror production traffic, discard responses) | crashes, OOM, latency regressions, throughput regressions under *real* traffic mix | quality (responses discarded), anything user-visible | a full extra replica |
| **Canary** (1–5 % of real traffic, responses served) | quality regressions, error-rate changes, real user impact | rare inputs, slow-burn issues | a replica + real user exposure |

Shadowing is the better first gate on this fleet precisely because it costs a
replica and no users. It is also the only way to see how a new build behaves at
your real prompt-length distribution, which no synthetic benchmark reproduces.

Canary mechanics on Kubernetes: KServe supports percentage traffic splits across
`InferenceService` revisions ⚠️ (KServe canary rollout docs, search-surfaced, not
fetched); with a Gateway API front end, an `HTTPRoute` with weighted
`backendRefs` across two `InferencePool`s does the same thing without an extra
control plane.

> **Decision rule — canary size and duration.** The canary must run long enough
> to accumulate a statistically meaningful count of the *rare* event you care
> about. For a 99.9 % availability SLO, detecting a doubling of the error rate at
> 5 % traffic needs on the order of tens of thousands of canary requests. Size
> the canary by *event count*, not by wall-clock. **Trade-off:** longer canaries
> mean two engine versions and two sets of weights resident, which on Kimi-K3
> means two nodes.

**A canary that shares a cache with production is not a canary.** If both
versions read the same prefix cache or KV pool (§3.6), a tokenizer or template
change in the canary poisons production's cache. Partition the KV namespace by
engine build.

### 5.4 Quality gates

Three gates, escalating in cost, all run before any traffic:

**Gate 1 — golden prompts, bit-exact.** N fixed prompts at temperature 0, output
hashed. Run in the eval lane with batch-invariant kernels (§2.8) so the hash is
meaningful. A diff is not automatically a failure — an engine upgrade *should*
change bits — but it must be *reviewed*, and the reviewer's note becomes the
release record.

**Gate 2 — logprob distribution diff.** Sample M prompts, capture top-k logprobs
per position from old and new builds, compare distributions (KL divergence, or
simply the rate at which the argmax differs). This catches numerical regressions
that a hash flags but cannot size, and it catches quantization mistakes — a
mis-applied scale shows up as a distribution shift long before it shows up as a
benchmark score. Practitioners report that storing top-2 logprobs per position
roughly tripled eval artifact storage, which is cheap at ~800-prompt scale ⚠️
(community writeup, search-surfaced, not fetched).

This gate is the right one for the **NVFP4 vs base** question this tree already
flags: DeepSeek-V4.1-Flash-NVFP4 is *"accuracy-neutral, no published speedup vs
base ⚠️"* ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)). "Accuracy-neutral" is a
claim a logprob diff can check on your traffic, cheaply, without running a
benchmark suite.

**Gate 3 — task evals.** The actual benchmark suite for the model's use cases.
Expensive, slow, and the only gate that catches "the model got worse at the
thing users use it for".

> **Decision rule — which gate blocks a release.**
> - Config-only change (flags, routing): Gate 1 only. Minutes.
> - Engine patch version: Gates 1 + 2. Hours.
> - Engine minor version, kernel change, quantization change, or any change to
>   tokenizer/chat template: **Gates 1 + 2 + 3, no exceptions.** Days.
> - Driver/CUDA: Gates 1 + 2, plus a full §6 capacity re-validation (the numbers
>   move even when the outputs don't).
>
> **Trade-off:** Gate 3 on every engine minor means falling behind upstream by
> weeks, which costs you performance work and bug fixes. The mitigation is not to
> skip the gate; it is to make Gate 3 cheap and automated enough that days become
> hours.

**The non-determinism trap in evals.** Eval scores are not reproducible across
batch compositions, because batch shape changes reductions (§2.8) — so running
the suite on a busy cluster gives a different score than running it on an idle
one ⚠️ (community writeup, search-surfaced). Run evals on a dedicated replica at
fixed concurrency, and record the concurrency in the result artifact.

### 5.5 Immutable images and config-as-code

- **Pin by digest, never by tag.** `image: registry/vllm@sha256:…`. A moving tag
  turns "restart the pod" into "silently upgrade the engine", which defeats every
  gate in §5.4.
- **Pin the kernel library too.** FlashInfer/FlashMLA versions, and any JIT cache
  the image ships. Combined with `VLLM_FORCE_AOT_LOAD=1` (§2.9), a cache miss
  becomes a loud failure rather than a silent recompile with different kernels
  [[src]](https://docs.vllm.ai/en/stable/configuration/optimization/).
- **Every engine flag in git.** The full `vllm serve` / SGLang argv belongs in a
  Helm values file or Kustomize overlay, reviewed, with the per-(model, GPU)
  launch recipes from [`../cross-cutting/inference-engines.md` §6](../cross-cutting/inference-engines.md)
  as the source. Flags set by hand at 03:00 during an incident are the single
  biggest source of config drift; the incident fix is a PR, even a same-day one.
- **GitOps for the rollout.** Argo CD or Flux reconciling the manifests means the
  cluster state is the repo state, and "roll back" is `git revert` ⚠️ (Argo CD
  docs not fetched). The important property is not the tool; it is that **no
  human has `kubectl apply` rights on the serving namespace.**
- **The container images this tree already pins** are consolidated in
  [`../cross-cutting/inference-engines.md` §3.10](../cross-cutting/inference-engines.md);
  digest-pin from there rather than re-deriving.

### 5.6 Rolling back 500 GB of weights

Rollback of code is `git revert`. Rollback of weights is a data-movement problem,
and the numbers from §2.9 dominate: DeepSeek-V4.1-Flash is 510.29 GB, Kimi-K3 is
1,560.9 GB ([`../METHODOLOGY.md` §8](../METHODOLOGY.md)).

The practices that make rollback survivable:

1. **Keep N−1 resident on local NVMe.** Do not delete the previous checkpoint
   when the new one lands. For Kimi-K3 that is 3.1 TB of NVMe committed to
   *versioning*, which is a real capacity decision but far cheaper than a
   re-download during an incident.
2. **Content-address the checkpoint.** Address weights by digest, not by a
   mutable `latest` path, so "roll back" is a pointer change and a restart, and
   so a partially-synced checkpoint can never be served.
3. **Verify before switching.** Reconcile against
   `model.safetensors.index.json`'s `total_size`, which
   [`../METHODOLOGY.md` §1](../METHODOLOGY.md) already establishes as *"the
   ground truth to reconcile against"*. A truncated download that passes a file
   count check will fail at tensor-load time — after minutes of loading.
4. **Roll back the tokenizer and chat template *with* the weights.** They are
   part of the checkpoint's contract. Rolling back weights alone reproduces §2.8.
5. **Pre-stage before you need it.** The distribution of a new checkpoint to
   every node should complete, and be verified, *before* the rollout starts. A
   rollout that pulls weights on demand has its MTTR set by the network.

> **Decision rule — rollback strategy by model size.**
> - < 100 GB (Qwen3.8-27B, Marlin-2B): keep 3 versions on NVMe, roll back by
>   pointer. Cheap.
> - 100 GB–1 TB (both DeepSeek variants): keep 2 versions, pre-stage the new one,
>   roll back by pointer.
> - \> 1 TB (Kimi-K3): keep 2 versions and **accept that rollback takes minutes,
>   not seconds**. Compensate by making the canary (§5.3) longer, because the
>   cost of being wrong is higher. **Trade-off:** 3.1 TB of NVMe per node held
>   for versioning versus a multi-minute node-level outage during rollback.

---

## 6. Benchmarking as an operational discipline

### 6.1 The tool landscape, and what each is for

| Tool | Runs where | Best for | Caveats |
|---|---|---|---|
| `vllm bench serve` | engine repo | quick engine-side sanity, sweeps | same host as engine skews client-side timing; vLLM 0.29.0 *"warns on warm prefix cache"* ⚠️ (release notes, search-surfaced) |
| [GuideLLM](https://github.com/vllm-project/guidellm) | separate client | SLO-shaped sweeps, HTML reports | client can bottleneck at very high QPS |
| [inference-perf](https://github.com/kubernetes-sigs/inference-perf) | separate client, K8s-native | goodput, trace replay, 10k+ QPS, cross-engine comparability | newer; config surface is large |
| NVIDIA GenAI-Perf / AIPerf | separate client | NIM/TRT-LLM parity | ⚠️ GenAI-Perf reportedly being succeeded by AIPerf (search-surfaced, not fetched) — check before standardising |

**Standardise on one client-side tool for the regression baseline.** Mixing tools
across releases makes the time series meaningless; their token counting,
streaming handling and warmup differ. `inference-perf` is the strongest default
here because it exists specifically to *"standardize the benchmark tooling and
the metrics used to measure inference performance across the Kubernetes and model
server communities"* [[src]](https://github.com/kubernetes-sigs/inference-perf/blob/main/README.md),
it measures goodput directly, it has *"Verified support for **vLLM**, **SGLang**,
and **TGI** with server side aggregate metrics and time series metrics"*, and it
documents comparability with other tools explicitly (`docs/comparability.md`)
[[src]](https://github.com/kubernetes-sigs/inference-perf/blob/main/README.md).

Its load generator scales past the client bottleneck that ruins single-process
benchmarks: *"Scalable to very high load due to optimized multi-process
architecture"*, with *"10k+ QPS"*
[[src]](https://github.com/kubernetes-sigs/inference-perf/blob/main/README.md).
Load patterns: *"Constant rate, Poisson arrival, and concurrent user
simulation"*; *"Multi-Stage Runs: Define stages with varying rates and durations
to find saturation points"*; *"Automatic Saturation Detection"*; and trace replay
including OpenTelemetry traces *"with agentic tree-of-thought simulation"*
[[src]](https://github.com/kubernetes-sigs/inference-perf/blob/main/README.md).

```bash
pip install inference-perf
inference-perf --server.type vllm --server.base_url http://localhost:8000 \
  --data.type random --load.type constant \
  --load.stages '[{"rate": 10, "duration": 60}]' --api.streaming true
```

[[src]](https://github.com/kubernetes-sigs/inference-perf/blob/main/README.md)

GuideLLM is the better **exploratory** tool, because its sweep profile finds the
saturation curve for you. Its option grammar is registry-backed
[[src]](https://github.com/vllm-project/guidellm/blob/main/docs/getting-started/benchmark.md):

```bash
guidellm run \
  --backend kind=openai_http,target=http://localhost:8000 \
  --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
  --constraint kind=max_duration,seconds=60
```

which *"Runs a `sweep` profile (default) to find optimal performance points"*
[[src]](https://github.com/vllm-project/guidellm/blob/main/docs/getting-started/benchmark.md).
Profiles: `synchronous`, `throughput` (with `max_concurrency`, `rampup_duration`),
`concurrent` (with `streams`, list-valued), `constant`/`async`, `poisson`,
`sweep` (with `sweep_size`). Constraints, which stop each strategy:
`max_duration`, `max_requests`, `min_requests`, `max_errors`, `max_error_rate`,
`max_global_error_rate`, `over_saturation`
[[src]](https://github.com/vllm-project/guidellm/blob/main/docs/getting-started/benchmark.md).

Two GuideLLM details that matter for reproducibility
[[src]](https://github.com/vllm-project/guidellm/blob/main/docs/getting-started/benchmark.md):

- `--seed kind=static,value=42` — *"The random seed is used for any operation in
  GuideLLM that involves randomness, such as synthetic data generation or Poisson
  strategy scheduling. By default it is a fixed value, so rerunning GuideLLM with
  the same arguments should produce the same results."*
- `--constraint kind=min_requests,count=1000` — *"is like `max_requests`, but
  keeps queuing until 1000 requests have been processed, which avoids throughput
  tail-off at the end of rate-based benchmarks."* **Use `min_requests`, not
  `max_requests`, for any number you intend to compare across releases**;
  tail-off is a systematic bias that varies with server speed, so it corrupts
  exactly the comparison you are making.

### 6.2 Regression tracking

A benchmark you run once is a number. A benchmark you run every release is a
control chart.

**What to store per run** (indefinitely, §4.9):

| Field | Why |
|---|---|
| Full engine argv + image digest | the independent variable |
| Driver / CUDA / kernel versions | §5.2 — these move the numbers |
| GPU model and count, node ID | B200 vs B300 vs GB300 are different rows |
| Benchmark tool + version + full config + **seed** | reproducibility |
| Dataset identity (or synthetic distribution) | ShareGPT ≠ synthetic ≠ your traffic |
| Warmup policy and prefix-cache state | a warm cache inflates results — vLLM 0.29.0 warns about this ⚠️ |
| TTFT/TPOT/ITL **percentiles**, not just means | the SLO lives in the tail |
| Goodput at the SLO | the only comparable capacity number |
| tokens/s per GPU | comparable against [`../matrix/pairs.json`](../matrix/pairs.json)'s `output_tokens_per_s_per_gpu` |

**Beware the decode-only vs sustained distinction.**
[`../METHODOLOGY.md` §4](../METHODOLOGY.md) is explicit: `pairs.json`'s
`output_tokens_per_s_per_gpu` is a **decode-only** rate, and a sustained
prefill+decode rate *"goes in a separate `sustained_output_tokens_per_s_per_gpu`
field and is never compared against a decode-only cell."* A load test measures
the **sustained** rate. Comparing a benchmark result against the decode-only
column is the most likely way to conclude, wrongly, that your cluster
underperforms the model.

**Gates, as CI:**

- Fail the release if goodput at the SLO drops > 5 % versus the previous release
  on the same hardware ⚠️ (threshold is a judgement call; method — set it at 3×
  the run-to-run standard deviation you measure across 5 repeats of the same
  build, so it is above noise).
- Fail if p99 TTFT or p99 TPOT regresses > 10 % ⚠️ (same method).
- **Warn, do not fail, on throughput improvements.** A large unexplained *gain*
  usually means the benchmark changed (warm cache, shorter outputs, a tokenizer
  change shrinking prompts), not that the engine got faster.

### 6.3 Capacity re-validation after upgrades

Re-run capacity validation — not just the latency benchmark — whenever any of
these change: driver, CUDA, engine minor version, attention kernel,
quantization format, speculation config, or model weights.

The four numbers to re-derive, all against
[`../METHODOLOGY.md` §3](../METHODOLOGY.md):

1. **`kv_bytes_per_token` as executed.** Read it from the engine's startup log,
   not from the config. This is where the 890 vs 1,650 B/token gate (§5.2) shows
   up.
2. **`max_concurrency(ctx)`** at your p50 and p99 context lengths. Reconcile
   against [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md).
3. **Achieved MBU/MFU** — from SGLang's MFU counters (§4.2) or from a
   bandwidth-bound decode sweep. Compare against
   [`../METHODOLOGY.md` §4](../METHODOLOGY.md)'s planning defaults and record the
   delta.
4. **Speculation acceptance rate**, for DeepSeek-V4.1-Flash. Against
   [`../cross-cutting/serving-optimizations.md` §2.3](../cross-cutting/serving-optimizations.md).
   Speculation gains shrink at high batch (§2.5 of that doc); an upgrade that
   changes batching changes the speculation economics.

If (1) or (2) moved, **`max_num_seqs` must be re-set before the release ships**,
or you have built a preemption storm (§2.7) into the rollout.

### 6.4 Benchmark plan template

Copy this per (model, GPU, engine-version) triple.

```yaml
# ---------------------------------------------------------------
# Benchmark plan — <model> on <n>×<gpu>, <engine> <version>
# ---------------------------------------------------------------
identity:
  model:            deepseek-ai/DeepSeek-V4.1-Flash
  checkpoint_digest: sha256:...           # content-addressed, §5.6
  gpu:              b300                  # research/gpus/b300.md
  gpus_per_replica: 4                     # TP4 = the pinned interactive shape;
                                          # use 2 for the TP2 max-throughput shape.
                                          # Corrected 2026-09-19 (was 8 — never a
                                          # recommended DeepSeek shape on B300;
                                          # see matrix/recommendations.md §2.1)
  engine:           vllm                  # main/nightly for this model, not 0.29.0
  engine_image:     registry/vllm@sha256:...
  driver:           R___                  # §5.2
  cuda:             13.4
  attention_kernel: FLASHMLA_MEGA_ATTN_DSV41   # captured from startup log
  kv_bytes_per_token_observed: 890        # MUST match METHODOLOGY §8
  speculation:      DSpark gamma=5

environment:
  client_location:  separate node, same fabric   # never co-located
  prefix_cache:     cold                          # state it; warm inflates results
  other_tenants:    none                          # exclusive node
  repeats:          5                             # for the noise floor

scenarios:                # ids from METHODOLOGY §6
  - id: S1               # 4K in / 512 out, TPOT <= 50 ms, interactive
    profile: {kind: concurrent, streams: [1, 8, 32, 64, 128, 256]}
    slo:     {ttft_p95_ms: 800, tpot_p95_ms: 50}
  - id: S2               # 32K in / 1K out
    profile: {kind: concurrent, streams: [1, 8, 32, 64]}
    slo:     {ttft_p95_ms: 6000, tpot_p95_ms: 50}
  - id: S3               # 128K in / 2K out, no SLO
    profile: {kind: concurrent, streams: [1, 8, 32]}
  - id: S4               # max throughput
    profile: {kind: throughput, max_concurrency: 512}

# Concurrency rows above max_concurrency(ctx) are reported as
# "infeasible (KV)", never as numbers — METHODOLOGY §3.
feasibility_source: research/matrix/fit-matrix.md

tool:
  name:    inference-perf
  version: v0.x.y
  seed:    42
  constraint: {kind: min_requests, count: 1000}   # not max_requests — §6.1

report:
  metrics: [ttft_p50, ttft_p95, ttft_p99,
            tpot_p50, tpot_p95, tpot_p99,
            itl_p99, goodput_at_slo,
            output_tokens_per_s_per_gpu_sustained,   # NOT the decode-only field
            prefix_cache_hit_rate, preemptions]
  compare_against:
    - previous_release
    - research/matrix/pairs.json            # decode-only; note the difference
  cost:
    formula: research/METHODOLOGY.md#6
    price_tier_source: research/cross-cutting/cloud-pricing.md#514

gates:
  goodput_regression_pct:  5     # fail
  ttft_p99_regression_pct: 10    # fail
  tpot_p99_regression_pct: 10    # fail
  unexplained_gain_pct:    20    # warn — suspect the benchmark, not the engine
```

---

## 7. Security and ops hygiene

### 7.1 Secrets: HF tokens and the rest

A Hugging Face token with read access to gated repos is a **model-exfiltration
credential**, and on this fleet it is also a *cost* credential (pulling 1.5 TB
repeatedly). Rules:

- **Never in the image, never in argv, never in a ConfigMap.** `argv` is visible
  in `kubectl describe pod` and in every process listing on the node.
- **Mount as a file, not an env var**, where the tool allows it. Env vars leak
  into crash dumps, child processes and error reports.
- **Separate tokens per purpose**: one read-only token for the *pre-staging job*
  that downloads weights to NVMe, and **no token at all on the serving pod**.
  Serving pods read from local NVMe (§5.6); they have no business talking to
  the Hub.
- **Short-lived credentials for object storage.** The Run:ai streamer path
  supports `AWS_ENDPOINT_URL`, `AWS_EC2_METADATA_DISABLED` and
  `RUNAI_STREAMER_S3_USE_VIRTUAL_ADDRESSING`
  [[src]](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/);
  use instance/workload identity rather than static keys wherever the storage
  supports it.
- **Rotate on every operator departure**, and alert on Hub API usage from a
  serving namespace (there should be none).

### 7.2 Network policy

The serving namespace should be able to reach: the gateway (ingress), the
metrics/trace collectors (egress), the KV pool if one exists (§3.6), and its own
peers for collectives. That is the entire list. In particular:

- **Deny egress to the internet by default.** A serving pod with outbound
  internet is a data-exfiltration path and an unreviewed-dependency path (a
  `trust_remote_code` model that pip-installs at load time).
- **Collectives are intra-replica.** NCCL traffic between ranks of one TP replica
  should be allowed between those pods only. On this fleet the pinned shapes are
  single-node, so NVLink carries collectives and *no cross-node policy is needed
  for them at all* — an easy win that disappears the moment you try multi-node EP.
- **The metrics endpoint is not public.** `/metrics` leaks model names, config
  (`vllm:cache_config_info` carries `gpu_memory_utilization`, `block_size`,
  `enable_prefix_caching` as labels
  [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md))
  and traffic volumes. Scrape-only, from the monitoring namespace.
- **Health and admin endpoints are not public either**, especially anything that
  can trigger generation (`/health_generate`) — it is a free-compute endpoint.

### 7.3 Model provenance and signing

The threat is a tampered or substituted checkpoint. Two controls:

1. **`safetensors` only.** Pickle-based formats execute arbitrary code at load;
   safetensors does not ⚠️ (widely documented, primary Hugging Face security page
   not fetched). This tree's models are safetensors-based (every
   `model.safetensors.index.json` reference in
   [`../METHODOLOGY.md` §1](../METHODOLOGY.md)), so a **safetensors-only
   admission policy costs nothing here and closes the largest hole.** Pair it
   with an explicit `trust_remote_code` allowlist — the default must be off.
2. **Sign the checkpoint.** The Sigstore `model-signing` project *"demonstrates
   how to protect the integrity of a model by signing it"*, supporting Sigstore
   keyless signing plus *"traditional signing methods, so models can be signed
   with public keys or signing certificates as well as PKCS #11 enabled devices"*
   [[src]](https://github.com/sigstore/model-transparency). The artifact is *"a
   sigstore bundle … stored as in JSON format"* containing a DSSE envelope with
   an in-toto statement whose *"subjects … are a list of (file path, digest)
   pairs"* and predicate type `https://model_signing/signature/v1.0`
   [[src]](https://github.com/sigstore/model-transparency). Verification *"reads
   the sigstore bundle file and firstly verifies that the signature is valid and
   secondly compute[s] the model's file hashes again to compare against the
   signed ones"* [[src]](https://github.com/sigstore/model-transparency).

> **Decision rule.** Sign at the **ingestion gate**, not at the publisher. You
> cannot make Moonshot or DeepSeek sign for you. What you can do is verify the
> upstream digest once, at the point where the checkpoint enters your NVMe, and
> re-sign it with your own key. Serving pods then verify *your* signature, and
> the (file path, digest) subject list doubles as the §5.6 completeness check.
> **Trade-off:** hashing 1.5 TB takes minutes and must happen during
> pre-staging, not during a rollout.

### 7.4 Prompt logging policy

**Default: do not log prompts or completions.** The exceptions must be
opt-in, scoped, and time-boxed.

This is not only a compliance position; it is the position the market has
converged on. OpenAI's API *"Data sent to the OpenAI API is not used to train or
improve OpenAI models (unless you explicitly opt in to share data with us)"*, and
abuse-monitoring logs are *"retained for up to 30 days, unless longer retention
is required by law"* [[src]](https://developers.openai.com/api/docs/guides/your-data).
Zero Data Retention is available on approval for a specific endpoint list —
`/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/completions`,
`/v1/moderations` among others — and when enabled *"the `store` parameter will
always be treated as `false`"*
[[src]](https://developers.openai.com/api/docs/guides/your-data). Notably,
stateful endpoints (`/v1/conversations`, `/v1/assistants`, `/v1/threads`) are
*"not"* ZDR-eligible, with state retained *"until deleted"*
[[src]](https://developers.openai.com/api/docs/guides/your-data) — **the same
asymmetry applies to your own stateful features**: a server-side conversation
store is a retention decision, whatever your logging policy says.

Anthropic's enterprise retention posture changed in 2026 ⚠️ (news coverage,
search-surfaced, not fetched) — the relevant design lesson is that **retention is
a product commitment customers negotiate**, so build the switch before you need
it.

Practical controls:

| Control | Implementation |
|---|---|
| No prompts in metrics | never a label; enforce in review |
| No prompts in traces | do **not** enable client-side prompt-carrying spans in prod (§4.5) |
| No prompts in engine logs | audit the engine's own log lines at each upgrade — they change |
| Structured request log | token *counts* only (§4.8) |
| Debug capture | explicit per-request header + tenant consent + short TTL bucket + separate access control |
| Retention | shortest of all four planes; delete on a timer, not on a human |
| Deletion | tenant-scoped, and it must actually reach traces and backups |

**The trap specific to inference:** the prefix cache and any KV pool (§3.6)
*contain prompt-derived state*. A "we don't log prompts" claim is false if KV
blocks derived from one tenant's prompt are reusable by another tenant.
**Partition the prefix-cache and KV-pool namespace by tenant** unless you have
explicitly decided that cross-tenant prefix sharing is acceptable — and note that
doing so costs hit rate, which costs money
([`../cross-cutting/serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md)).
⚠️ **TO BE VERIFIED**: whether vLLM 0.29.0 and SGLang 0.5.20 expose a per-tenant
cache-namespace knob, or whether isolation requires separate replicas. Method:
check the prefix-caching configuration surface for a salt/namespace parameter; if
absent, isolation = separate pools, and the cost is a capacity decision.

### 7.5 DoS protection at the gateway

LLM endpoints have an unusual property: **a single request can consume minutes of
GPU time.** Request-rate limiting alone is therefore inadequate — 1 req/s of
128K-context requests will saturate a replica that happily serves 100 req/s of
short ones.

Two orthogonal controls, and you need both. Envoy AI Gateway implements them
separately and says so: `QuotaPolicy` *"manages total consumption budgets"*
(cumulative token spend) while usage-based rate limiting *"controls request
velocity"* [[src]](https://theagentrouter.ai/docs/next/capabilities/traffic/quota-policy/).

**(a) Token-cost-weighted rate limiting.** Extract token usage into metadata
[[src]](https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting):

```yaml
spec:
  llmRequestCosts:
    - metadataKey: llm_input_token
      type: InputToken
    - metadataKey: llm_cached_input_token
      type: CachedInputToken
    - metadataKey: llm_output_token
      type: OutputToken
    - metadataKey: llm_total_token
      type: TotalToken
```

then charge the limit in tokens, not requests:

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

[[src]](https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting).
The documented key principle: *"Always set the request cost number to 0 to ensure
only token usage counts towards the limit."*
[[src]](https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting)

**Cached input should cost less**, matching the economics in
[`../METHODOLOGY.md` §6](../METHODOLOGY.md). A CEL cost expresses that directly
[[src]](https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting):

```yaml
spec:
  llmRequestCosts:
    - metadataKey: custom_cost
      type: CEL
      cel: "(input_tokens - cached_input_tokens) + (cached_input_tokens * 0.1) + output_tokens * 1.5"
```

The `* 0.1` on cached input is exactly the methodology's *"assume 10 % of the
uncached prefill cost unless measured"*; the `* 1.5` weight on output tokens is
a policy choice reflecting that decode is the scarce resource. Both should be
re-derived from [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md) per model
rather than copied — Kimi-K3's output:input cost ratio is nothing like
Qwen3.8-27B's.

**(b) Quota budgets.** `QuotaPolicy` caps cumulative spend; *"When all related
backend's quota are exceeded, requests are rejected with a `429 Too Many
Requests`"* [[src]](https://theagentrouter.ai/docs/next/capabilities/traffic/quota-policy/):

```yaml
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: QuotaPolicy
metadata:
  name: my-quota-policy
spec:
  targetRefs:
    - group: aigateway.envoyproxy.io
      kind: AIServiceBackend
      name: my-backend
  perModelQuotas:
    - modelName: "my-model"
      quota:
        mode: Shared
        defaultBucket:
          limit: 10000
          duration: "1h"
```

[[src]](https://theagentrouter.ai/docs/next/capabilities/traffic/quota-policy/).
`mode` currently supports only `Shared`, and `modelName` *"Must match the
`modelNameOverride` on the AIGatewayRoute"*
[[src]](https://theagentrouter.ai/docs/next/capabilities/traffic/quota-policy/).

⚠️ **Note on these sources:** `aigateway.envoyproxy.io` now 301-redirects to
`theagentrouter.ai`, which is where the fetched documentation lives. Treat the
API group `aigateway.envoyproxy.io/v1alpha1` as correct (it is in the manifests
themselves) but re-verify the project's canonical home and release version before
pinning a chart.

**(c) The controls the gateway must apply that are not rate limits:**

- **Hard caps on `max_tokens` and input length per tier.** A request that cannot
  fit `max_concurrency(ctx)` (§2.7) should be rejected at the gateway with a 400,
  not admitted and preempted.
- **Reject-on-queue-depth rather than queue forever.** A 429 with `Retry-After`
  is a better user experience and a better system behaviour than a 10-minute
  queue wait that ends in a client timeout (the work was done and thrown away).
- **Request timeouts that account for streaming.** An idle-timeout on the stream,
  not a total-duration timeout, or you kill legitimate long generations.
- **Cancel on client disconnect.** vLLM aborts in-progress requests when the
  client disconnects, *"triggering its abort codepath and releasing the KV cache
  and compute resources"* ⚠️ (search-surfaced; the behaviour is visible in
  `vllm:request_success_total{finished_reason="abort"}`
  [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)).
  Verify your gateway actually propagates disconnects — a proxy that buffers the
  response hides the disconnect and the GPU keeps generating into nothing.

### 7.6 Multi-tenancy and blast radius

Noisy-neighbour isolation on GPUs is weak. MIG partitions HBM and SMs but is not
usable for models that need the whole GPU — which is all of them here except
Marlin-2B and Qwen3.8-27B at small context. **Isolation therefore means separate
replicas**, and separate replicas mean the §1.2 tier structure is also the
security boundary. Design the tiers accordingly: an untrusted tenant should never
share a replica (and therefore never share a prefix cache) with a trusted one.

---

## 8. Runbooks

Format for each: **Detection** (the alert), **Diagnosis** (what to run),
**Remediation** (what to do), **Prevention**. Commands assume `kubectl` against
the serving cluster and `NODE`/`POD` set.

### 8.1 GPU Xid 79 — "GPU has fallen off the bus"

**Meaning.** `ROBUST_CHANNEL_GPU_HAS_FALLEN_OFF_THE_BUS`. *"This event is logged
when the GPU driver attempts to access the GPU over its PCI Express connection
and finds that the GPU is not accessible."* Immediate bucket **`RESTART_BM`**
(bare-metal restart), investigatory **`CONTACT_SUPPORT`**
[[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).
Applies to A100/H100/B100/GB200 (and by §2.2's proxy, B300).

**Detection.**
```promql
changes(DCGM_FI_DEV_XID_ERRORS[5m]) > 0 and DCGM_FI_DEV_XID_ERRORS == 79
```
plus the log-signature rule on `NVRM: Xid.*79`. In practice you will also see the
hung-replica alert from §2.6 fire, because the replica stops producing tokens.

**Diagnosis.**
```bash
# 1. The authoritative record, including the Xid 154 action string.
ssh $NODE 'dmesg -T | grep -E "NVRM: Xid" | tail -50'

# 2. Is the GPU even enumerated any more?
ssh $NODE 'nvidia-smi -L; nvidia-smi -q | head -40'
# A fallen-off GPU typically does not appear, or reports as ERR!.

# 3. Is it a PCIe-level disappearance?
ssh $NODE 'lspci | grep -i nvidia'

# 4. What does the driver think the required recovery is?
ssh $NODE 'dmesg -T | grep "Xid 154"'
# e.g. "GPU recovery action changed from 0x0 (None) to 0x2 (Node Reboot Required)"
```

**Remediation.** Xid 79 is `RESTART_BM`: the node goes out of service. There is
no GPU-reset shortcut — the GPU is not reachable to reset.

```bash
kubectl cordon $NODE
# Drain, honouring in-flight requests (§3.3). Grace must exceed --shutdown-timeout.
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data \
  --grace-period=120 --timeout=15m
# Reboot the host.
ssh $NODE 'sudo systemctl reboot'
# After boot: validate before returning to the pool (§4.4).
ssh $NODE 'dcgmi diag --run 2 --json'; echo "exit=$?"
# exit 0 -> uncordon. exit 205 -> leave cordoned, open a hardware ticket.
kubectl uncordon $NODE
```

If NVSentinel is deployed, this whole sequence is automatic and your job is to
confirm it happened: the node carries `dgxc.nvidia.com/nvsentinel-state` and the
`quarantineHealthEvent` annotation, progressing *quarantined → draining →
drain-succeeded → remediating → remediation-succeeded*
[[src]](https://docs.nvidia.com/nvsentinel/runbooks/cordoned-nodes/):

```bash
kubectl get node $NODE -L dgxc.nvidia.com/nvsentinel-state
kubectl get node $NODE -o jsonpath='{.metadata.annotations.quarantineHealthEvent}' | jq
# the event carries errorCode and recommendedAction ∈
# {COMPONENT_RESET, RESTART_VM, RESTART_BM, CONTACT_SUPPORT}
```

`drain-failed` and `remediation-failed` are terminal and *"require manual
intervention"* [[src]](https://docs.nvidia.com/nvsentinel/runbooks/cordoned-nodes/).

**Blast radius, this fleet.** If the node was running Kimi-K3, you have lost a
whole replica and there is no partial recovery. If it was running eight
Qwen3.8-27B replicas, you have lost 8 of N.

**Prevention.** Xid 79 is usually thermal, power-delivery or a seating/riser
problem. Track `DCGM_FI_DEV_GPU_TEMP` and power draw trends per slot; a node that
throws 79 twice is a hardware ticket regardless of whether it comes back.

### 8.2 NCCL timeout / collective hang in a TP replica

**Detection.** The §2.6 rule:
```promql
(vllm:num_requests_running{model_name="$m"} > 0)
and (rate(vllm:generation_tokens_total{model_name="$m"}[5m]) == 0)
```
If `TORCH_NCCL_ASYNC_ERROR_HANDLING=1` is set ⚠️, you also get a watchdog
exception in the logs instead of silence — which is the whole reason to set it.

**Diagnosis.** The question is *hardware, fabric, or software*.

```bash
# 1. Which rank is stuck? All ranks but one usually sit in a collective.
for p in $(kubectl get pods -l app=$MODEL -o name); do
  echo "== $p"; kubectl logs $p --tail=50 | grep -iE "nccl|timeout|watchdog|abort"
done

# 2. Python-level: where are the threads?
kubectl exec $POD -- py-spy dump --pid 1        # if py-spy is in the image
# Otherwise:
kubectl exec $POD -- bash -c 'kill -QUIT 1'      # faulthandler dump, if enabled

# 3. Hardware: is a GPU or a link sick underneath the hang?
kubectl exec $POD -- nvidia-smi
ssh $NODE 'dmesg -T | grep -E "NVRM: Xid|SXid" | tail -30'
ssh $NODE 'dcgmi health -g 1 -c'        # Healthy / Warning / Failure per subsystem

# 4. Fabric, only if the replica spans nodes (§2.5).
ssh $NODE 'ibstat; perfquery'            # ⚠️ thresholds unverified, see §2.5
```

**Discriminator.** A GPU-side Xid (74/155/95) or a DCGM `Fail` → hardware, go to
§8.1's shape. Clean hardware + a stuck collective on a single-node NVLink replica
→ software; capture the stack dump *before* restarting, because it is the only
evidence you will get.

**Remediation.**
```bash
# Restart the whole replica group, not one pod. With LeaderWorkerSet and
# restartPolicy: RecreateGroupOnPodRestart (§3.4), deleting the leader does this.
kubectl delete pod $LEADER_POD
# Verify the group came back together and is Ready before expecting traffic.
kubectl get pods -l leaderworkerset.sigs.k8s.io/group-index=$G -w
```
Deleting a single worker on a `None` restart policy leaves the survivors holding
GPUs and not serving — which is exactly why §3.4 recommends
`RecreateGroupOnPodRestart` for TP replicas.

**Prevention.** Set the collective timeout to a serving-appropriate value, not
the training default (§2.6). Keep the hung-replica alert as a **page**, because
without it a hung TP replica is invisible to every other signal.

### 8.3 KV OOM / preemption storm

**Detection.**
```promql
(vllm:num_requests_waiting{model_name="$m"} > 0)
and (vllm:kv_cache_usage_perc{model_name="$m"} > 0.95)
```
plus the log signature: *"Sequence group 0 is preempted by
PreemptionMode.RECOMPUTE mode"*
[[src]](https://docs.vllm.ai/en/stable/configuration/optimization/).

**Diagnosis.**
```bash
kubectl logs $POD --since=15m | grep -c "is preempted by"
kubectl exec $POD -- curl -s localhost:8000/metrics | grep -E \
  'kv_cache_usage_perc|num_requests_(waiting|running)|prefix_cache_(hits|queries)'
# Has the traffic mix shifted long? Compare prompt-token histogram quantiles
# now vs. 24h ago on the vllm:request_prompt_tokens histogram.
```

Three distinct causes, with different fixes:

| Cause | Signature | Fix |
|---|---|---|
| Traffic mix got longer | `request_prompt_tokens` p95 up | lower `max_num_seqs`, or route long requests to their own pool |
| `max_num_seqs` set above `max_concurrency(ctx)` | steady-state, not bursty | re-derive from [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) and lower it |
| KV layout changed after an upgrade | starts at a deploy boundary | §5.2 — check executed `kv_bytes_per_token` (890 vs 1,650) |
| Fixed per-sequence state underestimated | hybrid models only | §2.7: Qwen3.8-27B 392.2 MB/req, Kimi-K3 2.25 GB/req |

**Remediation (in order of preference).**
1. **Shed load at the gateway** — a 429 now is better than a recompute storm.
2. **Lower `max_num_seqs`** to `max_concurrency(ctx_p99)` and restart. Costs one
   rolling restart; the engine stops thrashing immediately.
3. Raise `gpu_memory_utilization` *only if* you have verified headroom — on
   Kimi-K3 at 195 GB of 268 GB/GPU there is very little
   ([`../models/kimik3/b300.md`](../models/kimik3/b300.md)).
4. Lower `max_num_batched_tokens` — helps ITL, costs TTFT
   [[src]](https://docs.vllm.ai/en/stable/configuration/optimization/).

Do **not** reach for `pipeline_parallel_size` as a live fix; it changes the
parallelism shape and every number in [`../matrix/`](../matrix/) with it.

**Prevention.** Alert on `kv_cache_usage_perc` p95 > 0.85 as a *ticket*, not a
page — it is the early warning that the next traffic shift will storm.

### 8.4 Gateway 429 storm

**Detection.** Rate of 429s crossing a threshold, sliced by tenant and model.
Critically: **is this working as intended, or a failure?** Both look identical on
a status-code graph.

**Diagnosis.**
```bash
# 1. Is it one tenant or all tenants?
#    (per-tenant 429s: gateway metrics, sliced by tenant_tier; identity from logs, §4.7)
# 2. Is the backend actually saturated, or is the limit mis-set?
#    Saturated -> engine queue depth and KV usage are high (§8.3 signature).
#    Mis-set    -> engine is idle while the gateway rejects. That is a config bug.
# 3. Did a quota window roll over? QuotaPolicy buckets are duration-based:
kubectl get quotapolicy -A -o yaml | grep -A4 defaultBucket
```

**The two failure modes and their fixes:**

- **Genuine saturation.** The 429s are correct. Fix capacity, not the limit.
  Check whether autoscaling should have reacted: the scaling signal must be
  queue depth / KV usage, not CPU. Dynamo's planner reads `ttft_ms` (default
  `500.0`) and `itl_ms` (default `50.0`) targets with
  `throughput_adjustment_interval_seconds` default `180` and
  `load_adjustment_interval_seconds` default `5`
  [[src]](https://docs.nvidia.com/dynamo/v1.3.0/components/planner) — if your
  incident is shorter than the adjustment interval, the autoscaler was never
  going to save you, and the answer is headroom, not a faster loop.
- **Mis-set limits.** Token-cost limits are easy to get wrong by an order of
  magnitude because the unit is tokens, not requests. Re-derive the limit from
  the tenant's expected token spend, and remember the request cost must be 0
  (§7.5) or you are charging twice.

**Remediation.**
```bash
# Raise a specific tenant's bucket (config-as-code: this is a PR, §5.5).
# Emergency override, reverted by the next reconcile if you do not also land the PR:
kubectl -n $NS patch quotapolicy my-quota-policy --type=merge \
  -p '{"spec":{"perModelQuotas":[{"modelName":"my-model","quota":{"mode":"Shared","defaultBucket":{"limit":50000,"duration":"1h"}}}]}}'
```

**Prevention.** Always return `Retry-After`. Publish quota headroom to tenants
before they hit the wall. Separate the *protective* limit (stops one tenant
taking the cluster down — should almost never fire) from the *commercial* quota
(billing — fires routinely and is not an incident).

### 8.5 Slow weight load / pod stuck not-Ready

**Detection.** Pod in `Running` but never `Ready`; startup probe counting down;
rollout stalled.

**Diagnosis.**
```bash
kubectl describe pod $POD | sed -n '/Events/,$p'
kubectl logs $POD --tail=100     # look for the loader's progress lines
# Is it I/O bound or compile bound?
kubectl exec $POD -- bash -c 'cat /proc/$(pgrep -f vllm | head -1)/io'
ssh $NODE 'iostat -x 2 5'        # NVMe saturated -> I/O bound
```

Three causes:

| Symptom | Cause | Fix |
|---|---|---|
| Steady read at NVMe line rate | sequential loading | `--load-format runai_streamer` with `concurrency` tuned [[src]](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/) |
| Low I/O, high CPU, no progress | torch.compile / kernel JIT | check `VLLM_CACHE_ROOT`; set `VLLM_FORCE_AOT_LOAD=1` so misses fail loudly [[src]](https://docs.vllm.ai/en/stable/configuration/optimization/) |
| Network reads | pulling from object storage at start | pre-stage to local NVMe (§5.6) |

**Remediation.**
```bash
# Confirm the startup budget is not the problem before blaming the loader:
kubectl get pod $POD -o jsonpath='{.spec.containers[0].startupProbe}'
# llm-d's recommended budget is 30s × 60 = 30 minutes (§2.6).
```
If the budget is too small, the pod is being killed mid-load and restarting,
which looks like a crash loop and is really a timeout. Raise `failureThreshold`
before touching anything else.

**Prevention.** Measure the load time for every model as a first-class number
(§2.9, currently ⚠️ unmeasured on this fleet) and set startup budgets at 3× it.
Bake the compile cache into the image so the first start is not the slow one.

### 8.6 Hot node / load imbalance

**Detection.** One replica's `num_requests_running`, KV usage or TPOT diverging
from its peers under the same offered load.

**Diagnosis.**
```promql
# Spread across replicas of one model — if this is large, routing is the suspect.
max(vllm:num_requests_running{model_name="$m"}) - min(vllm:num_requests_running{model_name="$m"})
max(vllm:kv_cache_usage_perc{model_name="$m"}) - min(vllm:kv_cache_usage_perc{model_name="$m"})
```
```bash
# Is the hot replica actually slower (hardware) or just busier (routing)?
kubectl exec $HOT_POD -- nvidia-smi --query-gpu=index,clocks_throttle_reasons.active,temperature.gpu,power.draw --format=csv
ssh $NODE 'dcgmi health -g 1 -c'
```

Four causes, in descending order of frequency:

1. **Prefix-cache affinity working as designed.** KV-aware routers deliberately
   send requests to the replica holding the prefix. A hot replica with a *high*
   cache hit rate is the system succeeding; the fix is more replicas, not
   different routing.
2. **Stale routing metrics.** §4.3's control-loop problem: routers polling
   metrics on a scrape interval herd onto whoever looked idle. Fix by moving the
   router to an event stream (Dynamo KV events, llm-d EPP feed).
3. **Thermal or power throttling.** The hot node is slower, so its queue grows,
   so a least-queue router sends it *less* — but a round-robin router keeps
   feeding it. Check `clocks_throttle_reasons.active`.
4. **Expert-routing imbalance (MoE only).** DeepSeek-V4.1-Flash and Kimi-K3 are
   MoE; uneven expert load across EP ranks is a real effect with its own
   mitigation (EPLB —
   [`../cross-cutting/serving-optimizations.md` §3.3](../cross-cutting/serving-optimizations.md)).
   This presents as *intra-replica* imbalance, visible in per-GPU utilisation
   within one replica, not as replica-to-replica spread. Different problem,
   different fix.

**Remediation.** For (1): scale out. For (2): shorten the metric path or switch
the router's scoring to something it observes directly (in-flight requests it
dispatched). For (3): cordon the node, `dcgmi diag --run 2`, check cooling. For
(4): consult the EPLB section, not this runbook.

### 8.7 Stuck rolling update

**Detection.** Deployment/LWS stuck partway; old and new revisions both present
for longer than expected.

**Diagnosis.**
```bash
kubectl rollout status deploy/$D --timeout=30s     # or: kubectl get lws $L -o wide
kubectl describe deploy/$D | sed -n '/Conditions/,/Events/p'
kubectl get pods -l app=$MODEL -o wide             # which revision, which node, which phase
kubectl get pdb -A                                 # is a PDB blocking eviction?
kubectl describe pod $PENDING_POD | sed -n '/Events/,$p'   # Insufficient nvidia.com/gpu?
```

The common causes here, in order:

1. **No room to surge.** `maxSurge: 1` on a node-sized replica needs an idle node
   (§3.4). On a full cluster the new pod is `Pending` forever with
   `Insufficient nvidia.com/gpu`. **This is the most common stuck rollout on this
   fleet.**
2. **PDB blocks the eviction.** With `minAvailable` equal to the current replica
   count, nothing can be evicted. Correct, and the answer is more capacity, not a
   weaker PDB.
3. **New pods never go Ready.** → §8.5.
4. **Group-restart cascade.** LWS recreating groups repeatedly during rollout —
   the exact case `RecreateGroupAfterStart` exists to prevent, since it only
   recreates when *"there are no pods currently pending"*
   [[src]](https://lws.sigs.k8s.io/docs/concepts/leaderworkerset/failure-handling/).
5. **Drain never completes** because `--shutdown-timeout` exceeds
   `terminationGracePeriodSeconds`, so SIGKILL lands mid-drain
   [[src]](https://llm-d.ai/docs/dev/operations/graceful-shutdown). Check both
   numbers together, always.

**Remediation.**
```bash
# Free capacity by scaling the old revision down first (accepting reduced capacity),
# or pause and roll back:
kubectl rollout undo deploy/$D
# Verify the drain settings are internally consistent before retrying:
kubectl get deploy $D -o jsonpath='{.spec.template.spec.terminationGracePeriodSeconds}{"\n"}'
kubectl get deploy $D -o jsonpath='{.spec.template.spec.containers[0].args}{"\n"}' | tr ',' '\n' | grep -A1 shutdown-timeout
```

**Prevention.** Decide the surge strategy per model *before* the rollout (§3.4's
table), and keep the spare capacity the strategy assumes.

### 8.8 Cache-hit collapse

**Detection.**
```promql
# vLLM
rate(vllm:prefix_cache_hits{model_name="$m"}[15m])
  / rate(vllm:prefix_cache_queries{model_name="$m"}[15m])
# SGLang
sglang:cache_hit_rate{model_name="$m"}
```
A step change, especially at a deploy boundary, with TTFT rising in lockstep.

**Why this is a *cost* incident, not just a latency one.** The blended-cost model
in [`../METHODOLOGY.md` §6](../METHODOLOGY.md) assumes 50 % of input tokens are
cached; a cached hit costs ~10 % of an uncached prefill. A hit-rate collapse from
50 % to 0 % therefore raises the input-token cost of the fleet by roughly
`(1 − 0.5 × 0.9) → 1.0`, i.e. ~1.8×, immediately. That is a bigger number than
most latency incidents.

**Diagnosis — in this order, because the first two are free:**
```bash
# 1. Did the chat template or tokenizer change? (§2.8 — the most common cause.)
kubectl get deploy $D -o jsonpath='{.spec.template.spec.containers[0].image}'
#    compare the checkpoint digest and tokenizer revision to the previous release

# 2. Is prefix caching even on?
kubectl exec $POD -- curl -s localhost:8000/metrics | grep cache_config_info
#    the info metric carries enable_prefix_caching as a label

# 3. Did routing change so requests no longer land on the replica holding the prefix?
#    (cache-aware routing off, or replica count changed, or a canary split the traffic)

# 4. Did the traffic mix genuinely change? (a new client with unique prefixes)
#    Check vllm:request_prompt_tokens quantiles and the per-tenant mix.

# 5. Is KV being evicted faster than it is reused?
kubectl exec $POD -- curl -s localhost:8000/metrics | grep -E 'kv_block_(lifetime|idle_before_evict|reuse_gap)'
#    reuse_gap > lifetime  ->  blocks die before they are reused: the cache is too small
```

Step 5 is the underrated one: `vllm:kv_block_reuse_gap_seconds` versus
`vllm:kv_block_lifetime_seconds`
[[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)
tells you directly whether the cache is too small for the *temporal* locality of
your traffic — a question no hit-rate number answers.

**Remediation.** Template/tokenizer regression → roll back (§5.6, weights *and*
tokenizer together). Routing → restore cache-aware routing; if a canary split the
traffic, that is expected and temporary. Capacity → raise
`gpu_memory_utilization` if headroom exists, or add a KV tier (§3.6).

**Prevention.** Make prefix-cache hit rate a **release gate** (§5.4): a shadow
run whose hit rate differs materially from production's is a template change,
whatever the diff says.

### 8.9 Quality regression after an engine upgrade

**Detection.** Golden-prompt hash diff (§5.4 Gate 1) — or, if it reached
production, user reports, a shift in `finished_reason` distribution, or an
output-length distribution change with no prompt-length change.

**Diagnosis.**
```bash
# 1. Establish what actually changed. The matrix from §5.2, old vs new:
kubectl logs $NEW_POD | grep -iE 'attention backend|kv cache dtype|quantization|flashinfer|flashmla'
kubectl logs $OLD_POD | grep -iE 'attention backend|kv cache dtype|quantization|flashinfer|flashmla'
# A different resolved attention backend or KV dtype is almost always the answer.

# 2. Size the difference: logprob diff on a sample (§5.4 Gate 2), not a hash.
# 3. Only then run the task eval suite (Gate 3) to decide if it matters.
```

**The discriminator that saves hours:** *expected* numerical drift from a kernel
change produces a **small, unbiased** logprob difference that grows with sequence
position (the Thinking Machines result: identical for the first 102 tokens, then
divergence [[src]](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)).
A *real* regression — wrong quantization scale, wrong template, wrong KV dtype —
produces a **large, biased** difference from the first token. Look at where the
divergence starts.

**Remediation.** Roll back the engine (§5.5 — this is why digests are pinned).
Then bisect: engine version, then kernel version, then flags. Report upstream
with the golden prompts attached.

**Prevention.** Gate 1 + 2 on every engine change, Gate 3 on minors (§5.4). Pin
kernel library versions explicitly. Capture the resolved backend and KV dtype
into the release artifact so "what changed?" is answerable without two live pods.

### 8.10 InfiniBand degradation

**Applicability.** Only bites replicas or data paths that cross nodes: KV pool
traffic (§3.6), disaggregated prefill/decode, weight distribution, or any future
multi-node replica. The pinned shapes in this tree are single-node.

**Detection.** Usually **indirect**: the §2.6 hung/slow-replica alert, or
cross-node KV transfer latency rising, before any fabric alarm. Pre-FEC errors on
a marginal 400G link *"never show up as a hard link down"* ⚠️ (§2.5).

**Diagnosis.**
```bash
# ⚠️ Commands and thresholds below are search-surfaced, not from a fetched
# NVIDIA UFM primary source. Verify against the UFM Enterprise User Manual.
ssh $NODE 'ibstat'                       # port state, rate, width — look for 1X or a down rate
ssh $NODE 'iblinkinfo | grep -v "4X"'    # any link not at full width
ssh $NODE 'perfquery -a'                 # error counters, all ports
# Fabric-wide sweep (run from a management host, not a serving node):
ibdiagnet
```
Read counters as **slopes**, not absolutes: clear them, wait a fixed interval,
re-read. A link accumulating symbol errors or link-recovery events at a steady
rate is failing; a link with a large static count may have failed once a year ago.

**Remediation.**
1. Route around it: cordon the node so no new multi-node replica lands there.
2. Reseat / replace the cable or transceiver. A link that flaps, accumulates
   physical errors, or renegotiates width after reseating is a signal-integrity
   fault ⚠️ — replace, do not tune.
3. Re-validate with `dcgmi diag --run nvbandwidth` before returning the node
   [[src]](https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgmi/dcgmi-diag.html).

**Prevention.** Scrape fabric counters into Prometheus and alert on slope. Verify
cable reach ratings against the actual runs — 400G/800G DAC beyond rated length
is a recurring cause ⚠️. Prefer single-node replicas where the model fits, which
is exactly what [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) already
recommends for every model in this repo.

---

## Open questions

Consolidated ⚠️ items, ordered by how much they would change a decision.

1. **NVMe read bandwidth on this repo's B300 nodes is unmeasured** (§2.9). It
   sets MTTR for every model and is the difference between a 62 s and a 312 s
   Kimi-K3 cold start. Method: `fio` sequential read across all NVMe devices,
   aggregate, then recompute the §2.9 table.
2. **Does vLLM 0.29.0 emit any speculative-decoding acceptance-rate metric?**
   (§4.2). **Reframed 2026-09-19:** the `vllm:spec_decode_*` family is listed
   under **"Future Work" / not implemented in V1** — *not* deprecated, as this
   document previously said
   [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md).
   Separately, [`../matrix/recommendations.md`](../matrix/recommendations.md)
   prescribes `vllm:spec_decode_num_{accepted,draft}_tokens_total` for benchmark
   B1 and **those names are not in the design doc's active list either** — a
   live contradiction between two docs in this tree. Every DeepSeek-V4.1-Flash
   operating point here assumes DSpark γ=5, so without a metric you cannot detect
   speculation degradation. Method:
   `curl :8000/metrics | grep -iE 'spec|accept|draft'` on the nightly build this
   model actually requires, and reconcile with recommendations.md. SGLang does
   expose `sglang:spec_num_steps` / `spec_num_draft_tokens`
   [[src]](https://docs.sglang.io/references/production_metrics.html).
3. **The NVIDIA Xid catalogue has no B300 (sm_103) or GB300 column** (§2.2). Its
   applicability columns are A100 / H100 / B100 / GB200
   [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).
   Proposed mapping: B100 → HGX B300, GB200 → GB300 NVL72 (so Xid 121 `C2C_ERROR`
   applies to GB300 and not to HGX B300). Confirm before encoding into a
   remediation controller.
4. **Do vLLM 0.29.0 / SGLang 0.5.20 support per-tenant prefix-cache namespacing?**
   (§7.4). If not, "we don't log prompts" is undermined by cross-tenant KV reuse,
   and isolation costs separate replicas — a capacity decision, not a config one.
5. **Do SLO histogram bucket edges coincide with the SLO thresholds?** (§1.3).
   **Half-answered 2026-09-19:** the metrics doc's published edges for
   `vllm:time_to_first_token_seconds` are `0.001, 0.005, 0.01, 0.02, 0.04, 0.06,
   0.08, 0.1, …` — **50 ms is not an edge** (0.04 and 0.06 bracket it)
   [[src]](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md).
   The edges for `vllm:request_time_per_output_token_seconds`, which is the
   histogram the TPOT ≤ 50 ms burn-rate query in §1.3 actually reads, are **not
   published** and remain ⚠️. Method: `curl :8000/metrics | grep _bucket` and
   align, or override buckets at launch.
6. **Does a KV pool round-trip recurrent state for hybrid models?** (§3.6).
   Qwen3.8-27B (GDN, 5 × 78.4 MB/req) and Kimi-K3 (KDA, 5 × 428.6 MiB/req) are
   not append-only. A connector that caches only attention KV would be a
   *correctness* bug. Method: inspect the connector's state handling, or test a
   two-turn conversation across a forced replica migration.
7. **Xid 155 false positives on Blackwell** (§2.4). Its trigger includes
   intentional link-down/SLEEP transitions, yet its immediate bucket is
   `RESET_GPU` [[src]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html).
   Confirm the observed rate on idle B300 GPUs before automating on it.
8. **What is the correct NCCL/collective timeout for serving?** (§2.6). The
   30-minute training default is wrong by orders of magnitude for inference.
   Proposed: 10× the measured p99.99 collective duration at production
   `max_num_batched_tokens`. Needs measurement, not a guess.
9. ~~**Does NVSentinel validate sm_103 specifically?**~~ **CLOSED 2026-09-19 —
   yes.** The overview page names the validated parts, and **B300 and GB300 are
   both explicitly listed** under Blackwell (B200, B300, GB200, GB300, RTX Pro
   6000) [[src]](https://docs.nvidia.com/nvsentinel/getting-started/overview/).
   No ⚠️ remains here; §3.2 is updated.
10. **Two NVIDIA pages disagree on CUDA 13.x for datacenter GPUs** (§5.2). The
    compatibility doc gives CUDA 13.x → driver ≥ 580
    [[src]](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)
    while the datacenter driver matrix lists no CUDA 13.x rows at all
    [[src]](https://docs.nvidia.com/datacenter/tesla/drivers/latest/cuda-toolkit-driver-and-architecture-matrix.html).
    Resolve from the specific toolkit and driver-branch release notes.
11. **Do Mooncake's GB200-measured gains transfer to HGX B300?** (§3.6). The
    3.8× throughput / 46× P50 TTFT / 92.2 % hit-rate figures are on 12 GB200 GPUs
    with agentic traces [[src]](https://vllm.ai/blog/2026-05-06-mooncake-store);
    HGX B300 hosts are PCIe-attached, not Grace-coupled, and a non-agentic
    workload has a much higher baseline hit rate.
12. **DCGM exporter field names** (§4.4). `DCGM_FI_DEV_XID_ERRORS`,
    `DCGM_FI_DEV_ECC_SBE_VOL`, `DCGM_FI_DEV_ECC_DBE_VOL`, `DCGM_FI_DEV_RETIRED_*`
    are from vendor integration docs, not the NVIDIA field reference. Also
    unverified: whether `DCGM_FI_DEV_XID_ERRORS` is last-value-only (which would
    collapse two Xids in one scrape interval into one sample).
13. **InfiniBand thresholds** (§2.5, §8.10). The ~120 symbol-errors/hour figure
    and the tool set are search-surfaced; verify against the UFM Enterprise User
    Manual before writing alerts.
14. **SGLang's `sglang:estimated_*` MFU counters** (§4.2) — are the "estimated"
    bytes comparable to the roofline denominators in
    [`../METHODOLOGY.md` §4](../METHODOLOGY.md)? Method: cross-check against DCGM
    memory utilisation at fixed load and record the ratio.
15. **`TORCH_NCCL_ASYNC_ERROR_HANDLING` semantics and default** (§2.6) — not
    verified against fetched PyTorch documentation.
16. **NVRx applicability to serving** (§2.6). Its published benchmarks are
    training; the project describes itself as experimental. Do not assume
    in-process restart works for a vLLM/SGLang process.
17. **Envoy AI Gateway's canonical home and release version** (§7.5).
    `aigateway.envoyproxy.io` 301-redirects to `theagentrouter.ai`; the API group
    in the manifests is still `aigateway.envoyproxy.io/v1alpha1`. Pin a chart
    version before depending on the CRDs.
18. **Gateway API Inference Extension GA status and CRD names** (§4.3). Docs
    reference both `v1` and `v1alpha1` and no GA milestone is stated on the
    introduction page [[src]](https://gateway-api-inference-extension.sigs.k8s.io/).
19. **The exact vLLM preemption metric name in 0.29.0** (§2.7). The docs say
    preemptions are exposed via Prometheus
    [[src]](https://docs.vllm.ai/en/stable/configuration/optimization/) but the
    name is not in the metrics doc's active list.
20. **Vendor availability/latency SLAs** (§1.4). Every figure circulating for
    OpenAI and Anthropic uptime SLAs came from third-party aggregators, not from
    fetched vendor contracts. Treat as unsourced.
21. **Marlin-2B's operational SLI** (§1.2). No engine supports it as of
    2026-09-19 ([`../cross-cutting/inference-engines.md` §6.5](../cross-cutting/inference-engines.md)),
    so the proposed ms-per-frame SLI has nothing to attach to yet.
22. **Regression gate thresholds** (§6.2). The 5 % goodput / 10 % p99 numbers are
    judgement calls; set them at 3× the measured run-to-run standard deviation
    across 5 repeats of an unchanged build.
23. **Third-party claims left unfetched**: Run:ai streamer's ~6× load speedup
    (Azure/AKS blogs), AIPerf succeeding GenAI-Perf, KServe canary mechanics,
    NPD's no-remediation behaviour, the GPU Operator `NoSchedule` interaction,
    SGLang's `/health_generate` reference, `batch_invariant_ops` and the LMSYS
    deterministic-SGLang post, vLLM 0.29.0 release notes, Argo CD. All are
    search-surfaced only and marked ⚠️ in place.

---

## Sources

Primary documents fetched on 2026-09-19 (WebFetch or `curl`), grouped by section
of first use.

**Engine metrics, tracing, tuning**

- vLLM, *Metrics* (design doc, `main`) — <https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md>
- vLLM, *Metrics* (stable docs) — <https://docs.vllm.ai/en/stable/design/metrics/>
- vLLM, *Optimization and Tuning* — <https://docs.vllm.ai/en/stable/configuration/optimization/>
- vLLM, *Loading models with Run:ai Model Streamer* — <https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/>
- vLLM, *Setup OpenTelemetry POC* — <https://docs.vllm.ai/en/stable/examples/online_serving/opentelemetry/>
- vLLM, `vllm/config/vllm.py` (`shutdown_timeout`) — <https://github.com/vllm-project/vllm/blob/main/vllm/config/vllm.py>
- SGLang, *Production Metrics* — <https://docs.sglang.io/references/production_metrics.html>
- NVIDIA Dynamo, *Planner* (v1.3.0) — <https://docs.nvidia.com/dynamo/v1.3.0/components/planner>

**GPU faults, health, diagnostics**

- NVIDIA, *Analyzing Xid Errors with the Xid Catalog* (updated 2026-09-09) — <https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html>
- NVIDIA, *Working with Xid Errors* — <https://docs.nvidia.com/deploy/xid-errors/working-with-xid-errors.html>
- NVIDIA, *Xid Errors* (index) — <https://docs.nvidia.com/deploy/xid-errors/index.html>
- NVIDIA DCGM, *Health Monitoring* — <https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/health-monitoring.html>
- NVIDIA DCGM, *dcgmi diag* — <https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgmi/dcgmi-diag.html>
- NVIDIA NVSentinel, *Overview* (v1.22.0) — <https://docs.nvidia.com/nvsentinel/getting-started/overview/>
- NVIDIA NVSentinel, *Node Drainer* — <https://docs.nvidia.com/nvsentinel/components/node-drainer/>
- NVIDIA NVSentinel, *Cordoned Nodes* (runbook) — <https://docs.nvidia.com/nvsentinel/runbooks/cordoned-nodes/>

**Kubernetes serving, routing, lifecycle**

- llm-d, *Graceful Shutdown & Request Draining* — <https://llm-d.ai/docs/dev/operations/graceful-shutdown>
- llm-d, *vLLM Model-Aware Readiness Probes* (0.7) — <https://llm-d.ai/docs/0.7/readiness-probes>
- llm-d, *Observability* (v0.9) — <https://llm-d.ai/docs/operations/observability>
- LeaderWorkerSet, *Failure Handling and Restart Policies* — <https://lws.sigs.k8s.io/docs/concepts/leaderworkerset/failure-handling/>
- Kueue, *Topology Aware Scheduling* (v0.14+) — <https://kueue.sigs.k8s.io/docs/concepts/topology_aware_scheduling/>
- Kubernetes Gateway API Inference Extension, *Introduction* — <https://gateway-api-inference-extension.sigs.k8s.io/>
- Envoy AI Gateway, *Usage-based Rate Limiting* — <https://theagentrouter.ai/docs/capabilities/traffic/usage-based-ratelimiting>
- Envoy AI Gateway, *Quota Policy* — <https://theagentrouter.ai/docs/next/capabilities/traffic/quota-policy/>

**SLOs, benchmarking, quality**

- Google SRE Workbook, *Alerting on SLOs* — <https://sre.google/workbook/alerting-on-slos/>
- kubernetes-sigs, *inference-perf* README — <https://github.com/kubernetes-sigs/inference-perf/blob/main/README.md>
- vllm-project, *GuideLLM — Run a Benchmark* — <https://github.com/vllm-project/guidellm/blob/main/docs/getting-started/benchmark.md>
- Thinking Machines Lab, *Defeating Nondeterminism in LLM Inference* (2025-09-10) — <https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/>

**KV tiering, change management, security**

- vLLM Blog, *Serving Agentic Workloads at Scale with vLLM × Mooncake* (2026-05-06) — <https://vllm.ai/blog/2026-05-06-mooncake-store>
- NVIDIA, *CUDA Compatibility — Minor Version Compatibility* (2026-09-09) — <https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html>
- NVIDIA, *CUDA Compatibility* (index) — <https://docs.nvidia.com/deploy/cuda-compatibility/latest/>
- NVIDIA, *CUDA Toolkit, Driver, and Architecture Matrix* (2026-09-09) — <https://docs.nvidia.com/datacenter/tesla/drivers/latest/cuda-toolkit-driver-and-architecture-matrix.html>
- Sigstore, *Model Transparency / model-signing* — <https://github.com/sigstore/model-transparency>
- OpenAI, *Data controls in the OpenAI platform* — <https://developers.openai.com/api/docs/guides/your-data>

All of the above were re-fetched and re-read during the 2026-09-19 adversarial
fact-check logged at the end of this document.

**Referenced but not fetched (all claims sourced to them are marked ⚠️)**

NVIDIA Fabric Manager User Guide (SXid catalogue); NVIDIA DCGM field-identifier
reference; NVIDIA UFM Enterprise User Manual (InfiniBand counters); PyTorch
distributed docs (`TORCH_NCCL_ASYNC_ERROR_HANDLING`); NVIDIA/nvidia-resiliency-ext
(NVRx); NVIDIA Dynamo metrics catalogue; NVIDIA GPU Operator troubleshooting;
Azure AKS GPU health monitoring (NPD); KServe canary rollout docs; SGLang
endpoint reference (`/health_generate`); vLLM v0.29.0 release notes;
thinking-machines-lab/batch_invariant_ops; LMSYS *Towards Deterministic Inference
in SGLang*; Hugging Face safetensors security documentation; Argo CD; Azure/AKS
Run:ai Model Streamer benchmarks; NVIDIA AIPerf blog.

**Internal cross-references**

[`../METHODOLOGY.md`](../METHODOLOGY.md) (§1 weights, §3 fit and
`max_concurrency`, §4 roofline/MBU/MFU, §6 cost and scenarios, §8 pinned inputs),
[`../README.md`](../README.md),
[`../matrix/fit-matrix.md`](../matrix/fit-matrix.md),
[`../matrix/cost-matrix.md`](../matrix/cost-matrix.md),
[`../matrix/recommendations.md`](../matrix/recommendations.md),
[`../matrix/pairs.json`](../matrix/pairs.json),
[`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md),
[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md),
[`../cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md),
[`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md),
[`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md),
[`../gpus/b300.md`](../gpus/b300.md), [`../gpus/gb300.md`](../gpus/gb300.md),
[`../models/deepseek41f/`](../models/deepseek41f/),
[`../models/deepseek41fnvfp4/`](../models/deepseek41fnvfp4/),
[`../models/qwen3827b/`](../models/qwen3827b/),
[`../models/kimik3/`](../models/kimik3/),
[`../models/marlin2b/`](../models/marlin2b/).

---

## Verification log (2026-09-19)

Adversarial fact-check. Every claim below was checked by opening the primary
source directly (not by trusting the citation already in the text); every
internal cross-reference was checked by opening the referenced file in this repo
and confirming the number is actually there; every derivation was recomputed with
`python3`. Verdicts: **CONFIRMED** (source says what the document said),
**CORRECTED** (document edited in place, new source cited), **UNVERIFIABLE**
(source could not settle it; ⚠️ **TO BE VERIFIED** left or added).

### External sources

| # | Claim (§) | Verdict | Source opened |
|---:|---|---|---|
| 1 | Google SRE multiwindow burn-rate table for 99.9 %: 14.4/2 % @ 1h+5m; 6/5 % @ 6h+30m; 1/10 % @ 3d+6h; burn-rate definition (§1.3) | **CONFIRMED** — all six cells and the definition match Table 5-8 | <https://sre.google/workbook/alerting-on-slos/> |
| 2 | Xid catalogue **column headers** (`Type (XID) … Trigger Conditions`) (§2.2) | **CONFIRMED** verbatim | <https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html> |
| 3 | All 21 Xid rows (13, 31, 43, 48, 62, 63, 64, 74, 79, 92, 93, 94, 95, 119, 120, 121, 140, 154, 155, 156, 157): mnemonic, description, immediate bucket, investigatory bucket, A100/H100/B100/GB200 applicability (§2.2) | **CONFIRMED** — every cell matches, including 74 `Y/Y/N/N`, 93 `Y/N/N/N`, 121 `N/N/N/Y`, 155 `N/N/Y/Y`, 156/157 `N/Y/Y/Y` | same |
| 4 | Xid 154's five recovery-action strings (`None`, `Drain P2P`, `Drain and Reset`, `GPU Reset Required`, `Node Reboot Required`) (§2.2) | **CONFIRMED** verbatim | same |
| 5 | Xid 155 trigger: *"Link down events which are flagged as 'intentional' (including transitions to SLEEP) will trigger this Xid"* (§2.4) | **CONFIRMED** verbatim — the Blackwell false-positive argument stands | same |
| 6 | DCGM health watch flags `p m i t n d e x a` and the `e` = IMEX meaning (§2.4) | **CONFIRMED** — all nine | <https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/health-monitoring.html> |
| 7 | DCGM health result states named **"Pass"** / Warning / Failure (§2.4, §8.2) | **CORRECTED** — the page names the healthy state **`Healthy`** (*"no enabled health rule found an incident in the retained data"*), not `Pass`; Warning and Failure wordings also restored in full | same |
| 8 | DCGM NVLink watch described as *"NVLink/NVSwitch: Link errors and fabric health"* (§2.4) | **CORRECTED** — that string is not on the page. Replaced with the real conditions, which are more useful: datalink CRC/recovery/replay errors **pre-Blackwell** vs **link recovery events on Blackwell+**, links reported down, non-ready fabric state, unhealthy fabric health mask | same |
| 9 | `dcgmi diag` levels 1–4 (`quick/short`, `medium`, `long`, `xlong`) with verbatim durations (§4.4) | **CONFIRMED** — all four, verbatim | <https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgmi/dcgmi-diag.html> |
| 10 | `dcgmi diag` named tests and exit statuses 0/226/217/215/205/204/203/198 with meanings (§4.4) | **CONFIRMED** — every code and gloss matches; 205 = `DCGM_ST_NVVS_ISOLATE_ERROR` *"requires isolation"*, so the cordon-and-RMA rule is right | same |
| 11 | NVSentinel v1.22.0; *"GPU fault detection and remediation system for Kubernetes"*; AWS/GCP/Azure/OCI; *"1,100+ nodes and ~40,000 GPUs"*; remediation *"in minutes"* (§3.2) | **CONFIRMED** — all four | <https://docs.nvidia.com/nvsentinel/getting-started/overview/> |
| 12 | NVSentinel validated architectures — document claimed only generation-level coverage and left sm_103 as ⚠️ (§3.2, open question 9) | **CORRECTED** — the page enumerates parts and **B300 and GB300 are explicitly listed** (Blackwell: B200, B300, GB200, GB300, RTX Pro 6000). ⚠️ removed; open question 9 closed | same |
| 13 | NVSentinel Node Drainer: partial-vs-full drain rule, DaemonSet/system-namespace skip, the eight config keys, the three eviction modes, *"The first matching policy wins"* (§3.2) | **CONFIRMED** — all present, including the `podDrainPolicies` YAML shape | <https://docs.nvidia.com/nvsentinel/components/node-drainer/> |
| 14 | vLLM `shutdown_timeout` **default 0** and its full docstring (§3.3) | **CONFIRMED** verbatim — the "highest-value one-line fix" claim stands | <https://github.com/vllm-project/vllm/blob/main/vllm/config/vllm.py> |
| 15 | llm-d graceful shutdown: `terminationGracePeriodSeconds` **must exceed** `--shutdown-timeout`; recommended 90 / 120; default tGPS 30 s; six-step termination order; EPP evicts queued requests with *"a retryable `503 Service Unavailable` (outcome `rejected-shutting-down`)"* (§3.3) | **CONFIRMED** — all of it | <https://llm-d.ai/docs/dev/operations/graceful-shutdown> |
| 16 | llm-d probe block quoted *"verbatim"*: `/health` semantics, `/v1/models` 503→200, `initialDelaySeconds 15`, `periodSeconds 30`, `failureThreshold 60` = 30-minute window (§2.6) | **CONFIRMED** on values; **CORRECTED** on completeness — the published block also carries `timeoutSeconds: 5` (startup) and `timeoutSeconds: 2` (readiness), now restored | <https://llm-d.ai/docs/0.7/readiness-probes> |
| 17 | vLLM metrics: ITL-vs-TPOT distinction, `vllm:request_time_per_output_token_seconds` = `(e2e − TTFT)/(out − 1)`, `vllm:inter_token_latency_seconds` = one sample per streamed event (§1.1, §4.2) | **CONFIRMED** on substance — the source's wording is *"These two metrics differ when an output bundles multiple tokens (e.g. speculative decoding) or when mean ITL and mean per-request TPOT are aggregated over differing weights"*, which is the speculative-decoding argument this document builds on | <https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md> |
| 18 | `vllm:spec_decode_*` family listed among **deprecated/removed** metrics (§4.2, open question 2) | **CORRECTED** — the family is under **"Future Work" / not implemented in V1**, not deprecated. Same operational outcome (not emitted), different planning consequence; also flagged the conflict with `matrix/recommendations.md`, which prescribes `vllm:spec_decode_num_{accepted,draft}_tokens_total` | same |
| 19 | `vllm:time_to_first_token_seconds` default bucket edges `0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1` (§1.3) | **CONFIRMED** — and this *settles* the §1.3 hazard for TTFT: **0.05 is not an edge**. The TPOT histogram's edges are not published → that half stays ⚠️ | same |
| 20 | `gen_ai.latency.*` span attribute values and the `--collect-detailed-traces` performance warning (§4.5) | **CONFIRMED** — scheduler 0.0176, forward 3.1516, execute 3.6468; *"possibly costly and or blocking operations"* | same |
| 21 | vLLM preemption WARNING log line quoted verbatim (§2.7, §8.3) | **CORRECTED** — the quote was truncated. The published line ends *"…RECOMPUTE mode **because there is not enough KV cache space.**"*, which is the part that identifies the cause | <https://docs.vllm.ai/en/stable/configuration/optimization/> |
| 22 | The documented anti-preemption knobs (§2.7) | **CORRECTED** — the page documents **four**, not three: the document omitted `tensor_parallel_size` (*"may introduce synchronization overheads"*). Added, with a note that it is the one knob that does not cost SLO here | same |
| 23 | `VLLM_CACHE_ROOT` default `~/.cache/vllm`, `VLLM_FORCE_AOT_LOAD=1` fails loudly, `--enforce-eager` skips compile + CUDA-graph capture (§2.9, §5.5, §8.5) | **CONFIRMED** — all three | same |
| 24 | Run:ai Model Streamer: `runai_streamer` / `runai_streamer_sharded`, `concurrency`, `memory_limit` example `5368709120`, `distributed`, the three S3 env vars, **and that the page publishes no measured load times** (§2.9, §7.1) | **CONFIRMED** on flags and on the no-measurements claim; **CORRECTED** on the `concurrency` quote — the real text is *"the level of concurrency and number of OS threads reading tensors from the file to the CPU buffer"* (the document's "and S3 client instances" is not there) | <https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/> |
| 25 | Thinking Machines: batch-size-dependent reductions as the primary cause; 1,000 completions at T=0 → 80 unique, most common 78×; identical for the first 102 tokens; 26 s → 55 s → 42 s (§2.8, §8.9) | **CONFIRMED** — every number, on Qwen3-235B-A22B-Instruct-2507 (prompt "Tell me about Richard Feynman", 1,000 tokens each); divergence at token 103 (992 "Queens, New York" vs 8 "New York City"). 42/26 = **1.615×**, so the document's "~1.6×" is right | <https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/> |
| 26 | Mooncake: **12 GB200 GPUs**, Codex agentic traces, **3.8×** throughput, **46×** P50 TTFT, **8.6×** e2e, hit rate **1.7 % → 92.2 %**, and 60 GB200 round-robin → **>95 %** with near-linear scaling (§3.6) | **CONFIRMED** — every figure | <https://vllm.ai/blog/2026-05-06-mooncake-store> |
| 27 | Mooncake component descriptions quoted as verbatim (§3.6) | **CORRECTED** — three quotes did not match the published wording (Master Server manages *"KV block metadata, service discovery, and client health"*, not "hashing and cluster-wide service discovery"; Transfer Engine does *"GPUDirect RDMA reads and writes through the Mooncake client without using SMs or staging through CPU memory"*; "topology-aware path selection" is not on the page). Rewritten to the published text; the numbers above are unaffected | same |
| 28 | CUDA minor-version compatibility: **CUDA 13.x ≥ driver 580**, **CUDA 12.x ≥ 525**; the minor-compat definition; the PTX limitation; `cudaErrorCallRequiresNewerDriver`; `nvcc -arch=sm_xx` (§5.2) | **CONFIRMED** — all five, verbatim. The PTX-JIT argument (limitation 2 biting after the 500 GB weight load) rests on a real quote | <https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html> |
| 29 | LeaderWorkerSet restart policies: default, semantics, and LWS 0.9.0+ for `RecreateGroupAfterStart` (§3.4, §8.7) | **CONFIRMED** on semantics and versions; **CORRECTED** on three quotes — the real ones are *"tightly coupled multi-host distributed inference and training (e.g., tensor-parallel or pipeline-parallel models)"*, *"prevents restart cascades during the initial rollout"*, and the full `RecreateGroupAfterStart` sentence. Also added `None`'s stated use case (*"loosely coupled workers or workloads with application-level fault tolerance"*), which strengthens the §3.4 decision rule | <https://lws.sigs.k8s.io/docs/concepts/leaderworkerset/failure-handling/> |
| 30 | Kueue Topology Aware Scheduling: beta, **enabled by default, since v0.14**; the four `podset-*` annotations; `ResourceFlavor` `apiVersion: kueue.x-k8s.io/v1beta2` with `topologyName` (§3.4) | **CONFIRMED** — all of it | <https://kueue.sigs.k8s.io/docs/concepts/topology_aware_scheduling/> |
| 31 | Kueue "hot swap" quoted as *"Replaces failed nodes without full workload rescheduling"* (§3.4) | **CORRECTED** — the feature is **`TASFailedNodeReplacement`** (beta, on by default), with `TASFailedNodeReplacementFailFast` capping it at one attempt before evict-and-requeue. Renamed and described from the page | same |
| 32 | SGLang production metrics: all 14 metric names/types/descriptions, `--enable-metrics`, default port **30000**, `model_name` label, and the three `--enable-mfu-metrics` counters (§4.2) | **CONFIRMED** — every row, verbatim, including `sglang:spec_num_steps` and `sglang:spec_num_draft_tokens` (which is what makes the "real argument for SGLang on the speculating models" argument land) | <https://docs.sglang.io/references/production_metrics.html> |
| 33 | Dynamo planner defaults: `ttft_ms` **500.0**, `itl_ms` **50.0**, `throughput_adjustment_interval_seconds` **180**, `load_adjustment_interval_seconds` **5** (§8.4) | **CONFIRMED** — all four | <https://docs.nvidia.com/dynamo/v1.3.0/components/planner> |
| 34 | inference-perf: the standardization purpose statement, goodput measurement, *"Verified support for vLLM, SGLang, and TGI"*, **10k+ QPS**, load patterns, multi-stage runs, saturation detection, trace replay, and the example CLI (§1.1, §6.1) | **CONFIRMED** — including the `pip install` + `inference-perf --server.type vllm …` invocation verbatim | <https://github.com/kubernetes-sigs/inference-perf/blob/main/README.md> |
| 35 | GuideLLM: `sweep` is the default profile; the six profiles and their sub-options; the seven constraint kinds; the `--seed` text; the `min_requests` tail-off text; the example `guidellm run` command (§6.1) | **CONFIRMED** — all of it, verbatim. The "use `min_requests`, not `max_requests`, for cross-release comparisons" rule is sound | <https://github.com/vllm-project/guidellm/blob/main/docs/getting-started/benchmark.md> |
| 36 | Sigstore model-signing: the integrity-protection purpose, keyless + public-key + certificate + PKCS #11, the sigstore-bundle/DSSE/in-toto structure, subjects as (file path, digest) pairs, predicate type `https://model_signing/signature/v1.0`, and the two-step verification (§7.3) | **CONFIRMED** — all of it | <https://github.com/sigstore/model-transparency> |
| 37 | OpenAI data controls: not used for training unless opted in; abuse logs *"retained for up to 30 days"*; ZDR endpoint list; `store` forced to `false` under ZDR; stateful endpoints not ZDR-eligible, retained *"until deleted"* (§7.4) | **CONFIRMED** — all five. The published ZDR list is longer than the document's excerpt (it also covers images, audio, realtime, live sessions), which the document's "among others" already allows | <https://developers.openai.com/api/docs/guides/your-data> |
| 38 | Envoy AI Gateway `QuotaPolicy`: budgets vs velocity split, **429** on exhaustion, the YAML (apiVersion `aigateway.envoyproxy.io/v1alpha1`, `perModelQuotas`, `mode: Shared`, `defaultBucket` limit/duration), `mode` supports only `Shared`, and `modelName` must match `modelNameOverride` (§7.5) | **CONFIRMED** — every element, including the API group still being `aigateway.envoyproxy.io/v1alpha1` on the `theagentrouter.ai` host | <https://theagentrouter.ai/docs/next/capabilities/traffic/quota-policy/> |
| 39 | Gateway API Inference Extension: *"an official Kubernetes project that optimizes self-hosting Generative Models on Kubernetes"*, `InferencePool` / `InferencePoolImport`, lightweight EPP for conformance testing, Metrics-and-Capabilities wording, **and that no GA milestone is declared** while both `v1` and `v1alpha1` appear (§4.3) | **CONFIRMED** — including the absence of a GA statement, so the ⚠️ there is correctly placed | <https://gateway-api-inference-extension.sigs.k8s.io/> |
| 40 | llm-d observability (v0.9): EPP metrics documented, PromQL reference with *"Ready-to-use queries for dashboards and alerting"*, default EPP alerting rules shipped as manifests under `guides/recipes/observability/`, OTel configured across vLLM + routing proxy + EPP (§4.3, §4.5) | **CONFIRMED** — all four, on the v0.9 page | <https://llm-d.ai/docs/operations/observability> |

### Internal cross-references (opened the file, checked the number is there)

| # | Claim (§) | Verdict | File opened |
|---:|---|---|---|
| 41 | **DeepSeek-V4.1-Flash deployment shape: "TP4–TP8, min 2 GPUs", replica "4–8 GPUs", blast radius "¼ to 1 node"; NVFP4 "4–8 GPUs", "½ to 1 node"** (preamble, §2.5, §3.2, §3.4, §4.5, §6.4) | **CORRECTED — the most consequential error found.** `matrix/recommendations.md` §2.1 pins **interactive 2 × TP4/node, batch 4 × TP2/node, EP1, never cross nodes**; `matrix/fit-matrix.md` prints **min 2 (rec 4)** for the base and **min 4 (rec 4)** for NVFP4. **TP8 is never a recommended DeepSeek shape on B300.** Corrected in six places: the preamble blast-radius table, §2.5's GPU range, §3.2's eviction-mode rule, §3.4's rolling-update table, §4.5's TP-overhead example, and §6.4's `gpus_per_replica: 8` | [`../matrix/recommendations.md`](../matrix/recommendations.md) §2.1, [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) §1 / §6 |
| 42 | Kimi-K3 = 8 GPUs = one node at **195 GB/GPU** of 268 (§preamble, §2.3, §8.3) | **CONFIRMED** — `195.1 GB/GPU, 72.8 % of the 2,144 GB node`, TP8+DCP8, *"1/2/4 GPUs cannot hold the weights at any quantisation that exists"*. Recomputed: 1,560.9 / 8 = **195.11**; 1,560.9 / 2,144 = **72.8 %** | [`../models/kimik3/b300.md`](../models/kimik3/b300.md) §1, §2 |
| 43 | B300 = **268 GB/GPU as deployed, 2,144 GB/node** (§preamble) | **CONFIRMED** — pinned in METHODOLOGY §8 and echoed by `cloud-pricing.md` §3. 268 × 8 = **2,144** | [`../METHODOLOGY.md`](../METHODOLOGY.md) §8 |
| 44 | Checkpoint sizes: Marlin-2B 5.444 GB, Qwen3.8-27B BF16 55.56, DeepSeek 510.29, NVFP4 527.27, Kimi-K3 1,560.9 GB (§2.9, §3.5, §5.6) | **CONFIRMED** — all five match METHODOLOGY §8 exactly | [`../METHODOLOGY.md`](../METHODOLOGY.md) §8 |
| 45 | §2.9 weight-load table: every `bytes / bandwidth` cell at 5 GB/s and 25 GB/s | **CONFIRMED** — recomputed in `python3`: 1.09/0.22, 11.11/2.22, 102.06/20.41, 105.45/21.09, **312.18/62.44**. All ten cells round correctly as printed | recomputed |
| 46 | Fixed per-sequence state: Qwen3.8-27B **78.4 MB × S=5 = 392.2 MB**; Kimi-K3 **428.6 MiB × 5 = 2.25 GB** (§2.7, §3.6) | **CONFIRMED** — matches the METHODOLOGY §8 pin log (`C7-qwen38-gdn-state-slots`). Recomputed: 78,446,592 × 5 = **392,232,960 B = 392.2 MB**; 428.6 MiB × 5 = 2,143 MiB = **2.247 GB** | [`../METHODOLOGY.md`](../METHODOLOGY.md) §8 |
| 47 | DeepSeek SWA ring **2,906,112 B = 2.77 MiB (43 rings, MTP on)** vs **2,703,360 B = 2.58 MiB (MTP off)** (§2.7) | **CONFIRMED** — matches pin log `C2-deepseek-swa-fixed-state`. Recomputed: 128 × 528 = 67,584 B/ring; × 43 = **2,906,112** (2.771 MiB); × 40 = **2,703,360** (2.578 MiB) | [`../METHODOLOGY.md`](../METHODOLOGY.md) §8 |
| 48 | **890 → 1,650 B/token changes `max_concurrency` by ~1.85×** (§5.2) | **CONFIRMED** — 1,650 / 890 = **1.854**. The kernel gate (`FLASHMLA_MEGA_ATTN_DSV41`, `capability.major == 10`) is pinned in METHODOLOGY §8 as stated | [`../METHODOLOGY.md`](../METHODOLOGY.md) §8, recomputed |
| 49 | Cache-hit collapse 50 % → 0 % raises input cost *"roughly … ~1.8×"* (§8.8) | **CONFIRMED** — 1 / (1 − 0.5 × 0.9) = **1.818**. The 10 %-of-uncached-prefill assumption is METHODOLOGY §6's own | [`../METHODOLOGY.md`](../METHODOLOGY.md) §6, recomputed |
| 50 | Hot spare = **$120/hour** at $15/GPU-hour B300 (§1.2) | **CONFIRMED** arithmetically (8 × 15 = 120) but **CORRECTED** for citation discipline: `cloud-pricing.md` §5.14 requires citing a tier **by name**, and $15 is the **`high`** row (OCI `BM.GPU.B300.8`), not `low` ($7.40). §1.2 now names the tier and prints the $59.20–$120 band; §3.5 already said `low` and is consistent | [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §5.14 |
| 51 | Kimi-K3 N−1 versioning = **3.1 TB of NVMe** (§5.6) | **CONFIRMED** — 2 × 1,560.9 GB = **3,121.8 GB = 3.12 TB** | recomputed |
| 52 | **DSpark γ=5 worth 3.13× output per byte** (§1.1) | **CONFIRMED** verbatim — *"worth 3.13× output per byte; removing it takes $/1M from $1.50 → $4.69"* | [`../matrix/recommendations.md`](../matrix/recommendations.md) §2.1 |
| 53 | `cost-matrix.md` §2 is computed *"at the concurrency that hits TPOT ≤ 50 ms"* (§1.1) | **CONFIRMED** — §2 is literally titled *"Interactive — $/1M output tokens at TPOT ≤ 50 ms (S1, 4K in / 512 out)"* | [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md) §2 |
| 54 | Engine versions **vLLM 0.29.0 / SGLang 0.5.20**, and **no engine supports Marlin-2B** (§4.2, §1.2) | **CONFIRMED** — §1's version table pins both; §6.5 is titled *"`NemoStation/Marlin-2B` — no engine supports it"*. **But** §2.1 also records that **DeepSeek-V4.1-Flash is only on vLLM `main`/nightly**, so §5.2's example matrix row and §6.4's template saying "vLLM 0.29.0" for that model were misleading — both corrected | [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) §1, §2.1, §6.5 |
| 55 | Every `serving-optimizations.md` sub-section cited (§1.2, §1.3, §1.5, §2.3, §2.5, §3.3, §3.4, §3.5) (§2.7, §3.6, §5.2, §6.3, §7.4, §8.6) | **CONFIRMED** — all eight headings exist with the described subject matter | [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) |
| 56 | METHODOLOGY planning defaults quoted: MBU *"0.5–0.7 on Blackwell first-gen software"*, comm overhead *"~5–15 % for TP=8 over NVLink, 20–40 % for multi-node TP over InfiniBand"*, `pairs.json`'s decode-only caveat, `total_size` as *"the ground truth to reconcile against"*, cached input *"10 % of the uncached prefill cost"* (§4.2, §4.5, §6.2, §5.6, §4.8) | **CONFIRMED** — all five quoted verbatim from §1, §4 and §6 | [`../METHODOLOGY.md`](../METHODOLOGY.md) |

### Still UNVERIFIABLE (⚠️ left in place or added)

These were pursued and could not be settled from a primary source; each is
already marked ⚠️ **TO BE VERIFIED** in the body and listed under Open questions.

- **`vllm:request_time_per_output_token_seconds` bucket edges** — the metrics doc
  publishes edges for the TTFT histogram only. The TPOT burn-rate query in §1.3
  is therefore still unproven at `le="0.05"`. (Open question 5, now half-answered.)
- **The exact vLLM 0.29.0 preemption metric name** — the tuning page says
  preemptions are exposed via Prometheus but names no metric, and the metrics
  design doc's active list does not contain one. (Open question 19.)
- **DCGM field identifiers** (`DCGM_FI_DEV_XID_ERRORS` last-value semantics,
  `_ECC_*_VOL`, `_RETIRED_*`) — not in the health-monitoring or `dcgmi diag`
  pages; the field-identifier reference remains unfetched. (Open question 12.)
- **Xid applicability for sm_103 / GB300** — the catalogue's columns are still
  A100 / H100 / B100 / GB200 and there is no B300 or GB300 column. The B100→B300
  and GB200→GB300 proxy stands as an estimate. (Open question 3. Note this is
  *not* the same gap as NVSentinel's, which is now closed.)
- **InfiniBand counter thresholds** (the ~120 symbol-errors/hour figure, the tool
  set) — no UFM primary source fetched. (Open questions 13.)
- **`TORCH_NCCL_ASYNC_ERROR_HANDLING`**, **NVRx applicability to serving**,
  **SGLang `/health_generate`**, **`sglang:estimated_*` comparability to the
  roofline**, **per-tenant prefix-cache namespacing**, **KV-pool round-tripping of
  recurrent state**, **NVMe read bandwidth on this fleet**, **vendor SLAs** — all
  unchanged and still ⚠️. (Open questions 1, 4, 6, 14, 15, 16, 20.)
- **Two NVIDIA pages disagreeing on CUDA 13.x for datacenter GPUs** — the
  compatibility page's `CUDA 13.x ≥ 580` row is now **confirmed** (item 28); the
  datacenter driver matrix's silence on 13.x was not re-fetched, so the
  *disagreement* itself remains open. (Open question 10.)
