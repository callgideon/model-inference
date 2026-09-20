# Automatic scale-up and scale-down on AWS with scarce GPU capacity

**Research date: 2026-09-20.** Everything below is pinned to that date. AWS
prices, quotas, Spot placement scores and instance-type offerings were pulled
live from this account (641134885443, us-east-1) on 2026-09-20 and are marked
`meas. 2026-09-20`. Infrastructure software in this space moves on a ~3-month
cadence; re-pin before you buy or commit a quota request.

**Scope.** How to add and remove Marlin-2B serving capacity automatically on
AWS when the instance family we need (`g6e`, 1× L40S 48 GB) is capacity-scarce
and, as of today, has a **Spot quota of zero** in this account. This is the
autoscaling half of the "no dropped requests" goal; the queue that absorbs the
gap while capacity arrives is `03` in this directory, and the workload/SLO
definitions are `01`. The bare-metal-oriented treatments of the same topics
live in [`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md)
and [`../scaling/06-cold-start.md`](../scaling/06-cold-start.md) — this document
**links to them rather than repeating them**, and departs from them wherever
AWS's mechanics differ from a fixed bare-metal node set.

**The five sentences this reduces to.**

1. Scale on **queue backlog per replica** and **oldest-queued-request age**, never on GPU utilisation — and use AWS's own backlog-per-instance target-tracking pattern, corrected for the fact that one replica serves 8 requests at once, not one.
2. `g6e` capacity in us-east-1 is thin enough that the scaling mechanism is not the constraint — **capacity acquisition is**: Spot quota is 0, Spot Placement Score is 1/10 in every AZ that offers `g6e`, and Capacity Blocks do not cover the `g` family at all.
3. Therefore the reliable levers are, in order: an **On-Demand Capacity Reservation** in us-east-1d (costs exactly what running the instance costs, so a "stopped warm pool" behind an ODCR saves nothing), a **mixed instances policy across `g6e.2xl/4xl/8xl` in four AZs** so a scale-out attempt has 12 pools instead of 1, and a **bounded queue in front** that turns a capacity miss into a longer wait instead of a 429.
4. A Marlin worker's cold start is ~5–8 minutes today and can be cut to roughly **100–150 s** by baking the vLLM image, the weights and the `torch.compile` cache into the AMI's **EBS root** (not `/opt/dlami/nvme` — instance store is cryptographically erased on stop) with a provisioned volume-initialization rate.
5. Scale-down is the easy half — a termination lifecycle hook that lets in-flight requests finish, `min_size = 1`, no scale-to-zero for the pilot, and an asymmetric window (fast out, slow in) — and a `N+1` pre-warm rule is **not sufficient** for a 2×-or-worse step burst; the policy must jump straight to the target replica count.

---

## 1. Signals: what to scale on

### 1.1 The signal ladder, ordered by how early it tells you the truth

| # | Signal | Where it lives today | Leads or lags | Use |
|---|---|---|---|---|
| 1 | **Oldest queued request age** (`now − enqueued_at` of the head of the queue) | does not exist yet — the gateway returns 429 at `MAX_INFLIGHT` instead of queueing ([`gateway.py`](../../apps/infrx-api/gateway.py)) | **leads** | primary scale-up trigger; it *is* the SLO |
| 2 | **Backlog per replica** = queue depth ÷ `InService` instances | does not exist yet | **leads** | target-tracking control variable (§1.3) |
| 3 | `vllm:num_requests_waiting` | vLLM `/metrics`, not scraped today | leads, but only once the engine is already saturated | secondary / sanity |
| 4 | `vllm:num_requests_running` | same | coincident | occupancy, used for the wait estimate |
| 5 | `vllm:kv_cache_usage_perc` | same | coincident | irrelevant for Marlin (§1.4) |
| 6 | `vllm:time_to_first_token_seconds` | same | **lags** | SLO attainment alarm, never a scaling trigger |
| 7 | GPU busy % (`DCGM_FI_DEV_GPU_UTIL`, or CloudWatch agent `nvidia_smi_utilization_gpu`) | not collected today | **lags badly** | dashboard only |
| 8 | Scheduled / predictive (diurnal) | no history yet | leads by hours | §1.5 |

The exact vLLM v1 metric names are `vllm:num_requests_running`
("Number of requests currently running"), `vllm:num_requests_waiting`,
`vllm:kv_cache_usage_perc` ("Fraction of used KV cache blocks (0–1)"),
`vllm:request_queue_time_seconds`, `vllm:time_to_first_token_seconds`,
`vllm:prefix_cache_queries` / `vllm:prefix_cache_hits`
[src](https://docs.vllm.ai/en/stable/design/metrics/). Re-verified 2026-09-20:
`vllm:num_requests_running` (*"Number of requests currently running"*) and
`vllm:kv_cache_usage_perc` (*"Fraction of used KV cache blocks (0–1)"*) are quoted
verbatim from that page. ⚠️ Two smaller claims are **not** carried by it:
`vllm:num_requests_waiting` appears only in the page's Grafana-dashboard prose, not
in its metric list, and the page does not document `vllm:gpu_cache_usage_perc` as
the v0 spelling at all — that rename is folklore here, not a citation. The v0
spelling is still in circulation in third-party examples; check your build. (The naming trap is documented in
[`../scaling/05` §2.2](../scaling/05-autoscaling-and-predictive-scaling.md).)

### 1.2 Why GPU utilisation is the wrong signal *specifically for Marlin*

This is not the generic "GPU util is a bad HPA signal" argument (that one is made
at length in [`../scaling/05` §1.3](../scaling/05-autoscaling-and-predictive-scaling.md)).
For Marlin the argument is sharper and it is **measured**: at concurrency 8 the box
does 1.57 clips/s on a 1080p source and 3.58 clips/s on a 360p source at the
*same* prompt-token budget (~1,930–2,061 tokens), with TTFT p50 of 3.35 s vs
0.66 s [meas. 2026-09-19, [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) §7].
The 2.3× difference is video download, decode and resize on 8 vCPUs — **not GPU
work**. A GPU-utilisation signal is therefore not merely lagging here, it is
measuring the wrong device: the box can be at 100 % of its real capacity with the
L40S half idle.

The same measurement says the useful per-replica capacity constant is a
**request rate**, not a token rate:

```
X_replica = 1.57 req/s        # g6e.2xlarge, 1080p sources, in-engine concurrency 8
X_replica = 3.58 req/s        # same box, 360p sources
S_seat    = 8 / 1.57 = 5.1 s  # mean residence time per request when all 8 seats are busy
TTFT_c=1  = 0.77 s            # steady state, c=1
```
[meas. 2026-09-19, [`models/marlin2b/README.md`](../../models/marlin2b/README.md) results table]

⚠️ **TO BE VERIFIED** — both benchmark rows used a single repeated clip, so vLLM's
multimodal processor cache (enabled by default, `mm_processor_cache_gb` defaults
to 4 GiB [src](https://docs.vllm.ai/en/latest/configuration/optimization.html))
may have absorbed part of the decode cost. `notes.md` flags this itself. Until a
distinct-clip benchmark exists, treat `X_replica = 1.57 req/s` as an **upper**
bound and size with the 360p/1080p pair as a band. *Close by:* re-run `bench.py`
with `-n 32` distinct clips.

### 1.3 Target tracking on backlog per instance — AWS's own pattern, corrected

AWS documents the canonical queue-driven scaling law for exactly this shape of
workload. The formula, verbatim:

> **Backlog per instance**: To calculate your backlog per instance, start with the
> `ApproximateNumberOfMessages` queue attribute to determine the length of the SQS
> queue (number of messages available for retrieval from the queue). Divide that
> number by the fleet's running capacity, which for an Auto Scaling group is the
> number of instances in the `InService` state, to get the backlog per instance.
>
> **Acceptable backlog per instance**: To calculate your target value, first
> determine what your application can accept in terms of latency. Then, take the
> acceptable latency value and divide it by the average time that an EC2 instance
> takes to process a message.

[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-using-sqs-queue.html)

AWS's worked example: 10 instances, 1,500 messages, 0.1 s per message, 10 s
acceptable latency → target = 10 / 0.1 = **100 messages per instance**; current
backlog is 150, so the group scales out by five instances [same src].

**The correction we must make.** AWS's denominator assumes *one message at a time
per instance*. A Marlin replica runs 8 concurrently. Dividing acceptable latency
by the 5.1 s per-request residence time would give 30/5.1 ≈ 6 and would
over-provision by 8×. The right denominator is the replica's **throughput**:

```
acceptable_backlog_per_replica = acceptable_queue_wait_seconds × X_replica
```

| acceptable queue wait | target backlog/replica, 1080p (X=1.57) | target, 360p (X=3.58) |
|---|---:|---:|
| 10 s | 16 | 36 |
| **30 s** | **47** | 107 |
| 60 s | 94 | 215 |
| 120 s | 188 | 430 |

(derived here; `python3`: `30*1.57 = 47.1`, `30*3.58 = 107.4`)

Because Marlin sees a *mix* of source resolutions, the honest target is the
conservative end of the band: **47 at a 30 s wait SLO**. If we adopt the
server-side transcode-to-480p optimisation that `notes.md` §7 recommends, X rises
toward 3.58 and the same target buys a shorter wait for free — which is the
correct direction (the cheap fix improves the SLO without touching the
autoscaler).

AWS now recommends expressing this with CloudWatch **metric math** rather than
publishing a custom metric, and gives the exact expression
`m1 / m2` over `ApproximateNumberOfMessagesVisible` (Sum, 1 min) and
`GroupInServiceInstances` (Average, 1 min)
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-target-tracking-metric-math.html).
Full policy JSON for our case is in §7.2.

### 1.4 Why KV-cache occupancy is not a signal here

Marlin-2B is 5.4 GB BF16 on a 48 GB L40S with `--max-model-len 32768`
([`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh)). Even 32 concurrent
sequences at the full 23.5 K-token video budget cannot approach the KV pool
carved from `--gpu-memory-utilization 0.90`. `vllm:kv_cache_usage_perc` will sit
near zero at any concurrency this box can physically reach, because the vCPU-bound
preprocessing stage gates admission long before KV does. Scale on it and the fleet
never grows. (The general form of this trap — "it measures occupancy of vLLM's
pre-allocated block pool, not physical VRAM" — is in
[`../scaling/05` §2.2](../scaling/05-autoscaling-and-predictive-scaling.md).)

Prefix caching is likewise not a scaling signal for us: vLLM's automatic prefix
caching is not enabled by default and requires `enable_prefix_caching=True`
[src](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html), and
video prompts share no meaningful prefix
([`../scaling/02` §8](../scaling/02-serving-stack-and-routing.md) reaches the same
verdict for Marlin). The cache that matters for Marlin is the **multimodal
processor cache**, which is on by default and is a per-replica decode-cost saving,
not a fleet-size signal.

### 1.5 Scheduled and predictive scaling

AWS predictive scaling needs **at least 24 hours of data** to forecast at all,
analyses **up to the past 14 days**, produces an **hourly forecast for the next 48
hours**, and **updates the forecast every 6 hours**
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html).
It only ever scales **out**: *"If the forecast expects a decrease in load, it will
not scale in to remove capacity. If you want to remove capacity that is no longer
needed, you must create dynamic scaling policies."* [same src]

Three things make it a phase-3 item, not a launch item, for us:

- **No history exists.** The API has one hand-made key and no real users
  ([`apps/README.md`](../../apps/README.md) §1). There is nothing to forecast.
- **It assumes a homogeneous group.** Verbatim: *"A core assumption of predictive
  scaling is that the Auto Scaling group is homogenous and all instances are of
  equal capacity… use caution when creating predictive scaling policies for mixed
  instances groups"* [same src]. Our capacity-scarcity answer (§2.4) is precisely a
  mixed instances group spanning `g6e.2xlarge`/`4xlarge`/`8xlarge`, whose
  capacities differ by ~4× in vCPU and therefore in video-decode throughput. If we
  adopt predictive scaling later it must be on a **custom load metric expressed in
  requests**, not CPU or network, and even then the forecast-to-replicas conversion
  is wrong whenever the mix shifts.
- **`SchedulingBufferTime` is the one setting that matters for us.** By default
  predictive scaling acts "at the start of each hour"; `SchedulingBufferTime`
  (console: **Pre-launch instances**) launches earlier *"giving them time to boot
  and become ready to handle traffic"* [same src]. Set it to the measured p95 cold
  start (§3) plus a margin, not to a round number.

What we *can* do at launch, with no history, is **scheduled scaling**: hold
`min_size = 2` during the hours we demo to design partners, `min_size = 1`
otherwise. Scheduled scaling is also AWS's own recommendation for driving an ASG
off a reservation window: *"If you use Amazon EC2 Auto Scaling or Amazon EKS, you
can schedule scaling to run at the start of the Capacity Block reservation. With
scheduled scaling, AWS automatically handles retries for you, so you don't need to
worry about implementing retry logic to handle transient failures."*
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html)

### 1.6 Target tracking vs step scaling vs custom: the decision rule

| | Use when | Our verdict |
|---|---|---|
| **Target tracking** on backlog/replica | the metric changes proportionally with capacity | **Primary.** Backlog/replica halves when replicas double — it is exactly the class AWS says target tracking is for: *"Metrics that decrease when capacity increases and increase when capacity decreases can be used to proportionally scale out or in"* [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-simple-step.html) |
| **Step scaling** on oldest-request age | you want a *disproportionate* response to a large breach | **Secondary, scale-out only.** AWS: *"You still have the option to use step scaling as an additional policy for a more advanced configuration. For example, you can configure a more aggressive response when utilization reaches a certain level."* [same src] |
| **Simple scaling** | never | AWS: *"In general, we recommend against using simple scaling policies if you can use either step scaling or target tracking scaling policies instead."* [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) |
| **Custom controller** (Lambda on queue age → `SetDesiredCapacity`) | the control law is not expressible as either | **No** — until we have evidence the two above fail. It is a pager at 3 a.m. that AWS would otherwise own. |

When several policies are active the group takes the **maximum** of what each
policy independently computes: *"When multiple scaling policies are active, each
policy determines the desired capacity independently, and the desired capacity is
set to the maximum of those"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html).
That is what makes "target tracking as the baseline + step scaling as the panic
button" safe to run together.

---

## 2. Mechanics on an EC2 Auto Scaling group

### 2.1 The shape of the change to `apps/infrx-api/deploy/`

Today there is exactly one pet: `i-0e8449a4ffca29bab`, a `g6e.2xlarge` in
us-east-1d with an Elastic IP, Caddy terminating TLS on the box itself, and
`install.sh` run by hand over SSM
([`models/marlin2b/README.md`](../../models/marlin2b/README.md),
[`apps/infrx-api/deploy/install.sh`](../../apps/infrx-api/deploy/install.sh)).
Three structural changes are unavoidable before anything can autoscale:

1. **TLS moves off the box.** Caddy-per-instance cannot work behind an ASG
   (per-instance Let's Encrypt challenges, EIP-per-instance). An ALB terminates
   TLS with ACM; the box keeps only the gateway on :8001.
   `deploy/Caddyfile` is deleted; `install.sh` stops running Caddy.
   ALB's connection idle timeout is `idle_timeout.timeout_seconds`, **default 60
   seconds**, and `client_keep_alive.seconds` defaults to 3600
   [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html).
   60 s is below our p50 end-to-end for a queued request, so this attribute must be
   raised (§7.1). Today's Caddyfile already sets `read_timeout 600s` and
   `flush_interval -1` for streaming — the ALB needs the equivalent.
2. **The queue moves off the box.** `MAX_INFLIGHT=16` with an immediate 429 is a
   per-process counter (`inflight` in `gateway.py`). A fleet needs one shared
   queue; `03` picks the technology. Whatever it is, the autoscaler reads its
   depth and head-age.
3. **State moves off the instance store.** Everything currently lives under
   `/opt/dlami/nvme` — weights, logs, `usage.jsonl`. That is instance store, and
   AWS is unambiguous: *"The data on an instance store volume persists even if the
   instance is rebooted. However, the data does not persist if the instance is
   stopped, hibernated, or terminated. When the instance is stopped, hibernated, or
   terminated, every block of the instance store volume is cryptographically
   erased."*
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-store-lifetime.html)
   This kills two things at once: any warm pool that stops instances (§2.5), and
   `usage.jsonl` as a durable fallback (it must ship to S3 or CloudWatch Logs
   before termination, via the termination lifecycle hook).

### 2.2 Launch template and AMI: where the 36 GB of dependencies live

A Marlin worker needs, before it can serve: the NVIDIA driver, docker + NVIDIA
runtime, the `vllm/vllm-openai:nightly` image (~30 GB per the task brief), the
5.4 GB weights, `ffmpeg`/`ffprobe`, the `/opt/pytorch` env with
`fastapi uvicorn httpx`, and the repo itself.

Today `install.sh` installs the Python deps and `ffmpeg` at *run* time and
`download.sh` pulls weights at run time. Both belong in the AMI.

| Where the bits live | Cold-start cost | Persists across a stop? | Verdict |
|---|---|---|---|
| Pulled at boot from Docker Hub / HF | image pull dominates; unpredictable | n/a | **no** |
| Pulled at boot from S3 (`s3://llm-bootcamp-641134885443`, already the weights mirror per [`CLAUDE.md`](../../CLAUDE.md)) | weights only: 5.4 GB; at 1 GB/s ≈ 6 s, at 300 MB/s ≈ 18 s | n/a | acceptable for weights, not for the image |
| **Baked into the AMI's EBS root** | paid once at build; at boot, lazy block download from S3 (§2.3) | yes | **yes — image, weights and compile cache** |
| On `/opt/dlami/nvme` (instance store) | free to read | **no — erased on stop** | only as a runtime scratch dir |
| EFS / FSx mount | network read on every boot, extra service | yes | no — buys nothing over a baked AMI for a single 5.4 GB model |

The current DLAMI (`ami-0a4870b172edcb0f2`, *Deep Learning OSS Nvidia Driver AMI
GPU PyTorch 2.12 (Ubuntu 24.04) 20260827*) has a **30 GiB gp3 root**; the live
instance's root volume has been grown to **300 GiB gp3, 3000 IOPS, 125 MB/s**
[meas. 2026-09-20, `describe-images` / `describe-volumes`]. A baked AMI with the
vLLM image and weights will hold roughly 30 (base) + 30 (image) + 5.4 (weights)
+ compile cache ≈ **70 GiB of snapshot data**, which is the number that sets boot
cost in §3.

### 2.3 EBS volume initialization is a real cold-start term

A volume created from a snapshot is lazily hydrated: *"the data blocks must be
downloaded from Amazon S3 to the new volume… During this time, the volume being
initialized might experience increased I/O latency and decreased performance"*, and
*"The default volume initialization rate fluctuates throughout the initialization
process, which could make completion times unpredictable"*
[src](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-initialize.html).

Two fixes, both primary-sourced:

- **Provisioned Rate for Volume Initialization**: *"you can optionally specify an
  Amazon EBS Provisioned Rate for Volume Initialization (volume initialization
  rate) that ranges from 100 to 300 MiB/s"*, charged per GiB of **full snapshot
  data size**, with *"a limit of 5,000 MiB/s on the cumulative volume
  initialization rate that you can request across concurrent volume creation
  requests"* per Region. It can be set *"For EBS volume block device mappings in
  launch templates"* [same src].
- **Fast Snapshot Restore**: *"If you create a volume from a snapshot that is
  enabled for fast snapshot restore, the volume is fully initialized at creation
  and it immediately delivers its full performance."* Note the interaction:
  *"If you specify a volume initialization rate and use a snapshot that is enabled
  for fast snapshot restore, Amazon EBS uses the specified rate instead of fast
  snapshot restore."* [same src]

Arithmetic for our AMI, at the documented rate bounds (derived here):

| snapshot data | @100 MiB/s | @300 MiB/s |
|---|---:|---:|
| 30 GiB (DLAMI as-is) | 307 s | 102 s |
| **70 GiB (baked: image + weights + cache)** | **717 s** | **239 s** |

This is the trap in "just bake a bigger AMI": a 70 GiB baked root at the maximum
provisioned rate still takes ~4 minutes to become fully performant, and at the
default (unpredictable) rate it may be worse. The engine does **not** have to wait
for full initialization — it reads what it needs and those blocks are fetched on
demand — but every first read of a cold block pays an S3 round trip, which is
exactly what makes a cold `docker run` of a 30 GB image slow on a fresh volume.

⚠️ **TO BE VERIFIED** — we have **no measurement** of how long `marlin2b-vllm.service`
takes to reach `/health` on a freshly launched instance from a baked AMI at
100 / 300 MiB/s vs. the default rate. Every number in §3 that depends on it is
`est.`. *Close by:* build the AMI, launch three instances (default rate, 100, 300)
and record `cloud-init` finish → first 200 from `/health`. This is the single
highest-value missing measurement in this document.

### 2.4 Mixed instance types and multiple AZs — the actual answer to `InsufficientInstanceCapacity`

AWS's own remedy list for `InsufficientInstanceCapacity` is, verbatim:

> - Wait a few minutes and then submit your request again; capacity can shift frequently.
> - Submit a new request with a reduced number of instances…
> - If you're launching an instance, submit a new request without specifying an Availability Zone.
> - If you're launching an instance, submit a new request using a different instance type…

[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html)

Note the sentence that opens that section: *"You get the
`InsufficientInstanceCapacity` error when you try to launch a new instance **or
restart a stopped instance**"* [same src, emphasis added]. That single clause
decides §2.5.

**What capacity actually exists for us, measured today.** `g6e` is offered in four
of the six us-east-1 AZs; `g6` and `g5` in five:

| type | GPU | offered in (2026-09-20) |
|---|---|---|
| `g6e.2xl` / `4xl` / `8xl` / **`16xl`** | L40S 48 GB, **1 GPU** | us-east-1a, 1b, 1c, 1d |
| `g6e.12xl` | L40S 48 GB, **4 GPUs** (192 GB total) | us-east-1a, 1b, 1c, 1d |
| `g6.2xl` / `4xl` | L4 24 GB | us-east-1a, 1b, 1c, 1d, **1f** |
| `g5.2xl` / `4xl` | A10G 24 GB | us-east-1a, 1b, 1c, 1d, **1f** |

[meas. 2026-09-20, `aws ec2 describe-instance-type-offerings --location-type availability-zone --region us-east-1`].
AZ-name → AZ-ID for this account: `1a=use1-az1, 1b=use1-az2, 1c=use1-az4,
1d=use1-az6, 1e=use1-az3, 1f=use1-az5` [meas. 2026-09-20,
`describe-availability-zones`]. This matters because the observed scarcity
(`CLAUDE.md`: *"`g6e` needs AZ retries (1d worked)"*) is an **on-demand pool**
property, not an offering property — the type is offered in four AZs; on a given
day only some have free capacity.

**The mixed instances policy.** An ASG with a mixed instances policy and four
subnets gives a scale-out attempt **3 instance types × 4 AZs = 12 pools** instead
of 1. AWS's On-Demand allocation strategies are `lowest-price` and `prioritized`;
with `prioritized`, *"Amazon EC2 Auto Scaling determines which instance type to
use first based on the order of instance types in the list of launch template
overrides"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/allocation-strategies.html).

Our priority order should **not** be cheapest-first. Because Marlin is vCPU-bound
on video decode, the larger sizes are better value per unit of throughput:

| type | on-demand $/h (us-east-1, 2026-09-20) | vCPU | $/vCPU-h | L40S |
|---|---:|---:|---:|---|
| `g6e.2xlarge` | **$2.24208** | 8 | $0.2803 | 1 |
| `g6e.4xlarge` | **$3.00424** | 16 | $0.1878 | 1 |
| `g6e.8xlarge` | **$4.52856** | 32 | $0.1415 | 1 |
| **`g6e.16xlarge`** | **$7.57719** | 64 | **$0.1184** | 1 |
| `g6.2xlarge` (L4 24 GB) | $0.97760 | 8 | $0.1222 | 1 |
| `g5.2xlarge` (A10G 24 GB) | $1.21200 | 8 | $0.1515 | 1 |

[meas. 2026-09-20, AWS price sheet
[us-east-1 Linux on-demand JSON](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json),
fetched with `curl`. This confirms the `≈ $2.24/h` used in
[`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md).
These rows are **not yet in** [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md),
which has no `g6e` entry — see §"Implications".]

⚠️ **Corrected 2026-09-20**, two errors in the rows above as first written.
(a) `g6e.12xlarge` was listed alongside the single-GPU sizes; it carries **4× L40S
and 48 vCPU** at $10.49264/h, a different shape entirely and not an override
candidate for a one-GPU-per-replica design.
(b) **`g6e.16xlarge` was missing**: 1× L40S, 64 vCPU, $7.57719/h — the **best
$/vCPU-h of any `g6e` size** and therefore, on this document's own vCPU-bound
argument, the row that should lower $/clip the most. Adding it as a fourth override
makes the scale-out surface **4 types × 4 AZs = 16 pools** rather than 12, and it
caps at **3 replicas** inside the 192-vCPU quota (§2.10). Both facts re-fetched
2026-09-20 from the same price sheet and the
[G6e product page](https://aws.amazon.com/ec2/instance-types/g6e/) (vCPU / GPU-count
/ EBS-bandwidth table). ⚠️ Per-replica throughput on `16xlarge` is unmeasured — the
same gap open question 4 records for `4xl` / `8xl`.

`g6e.4xlarge` doubles the vCPUs for **1.34×** the price; `g6e.8xlarge` quadruples
them for **2.02×**; `g6e.16xlarge` gives 8× the vCPUs for **3.38×**. If video decode is the bottleneck (it is), those are the rows
that lower $/clip. Priority order: **`g6e.4xlarge` → `g6e.2xlarge` →
`g6e.8xlarge`**, i.e. best-value-first with the small size second because it is
the most likely to exist.

⚠️ **TO BE VERIFIED** — the claim that `g6e.4xlarge` delivers ~2× the clips/s of
`g6e.2xlarge` is an inference from `notes.md` §7 ("more vCPUs per GPU" is the
second recommended optimisation), not a measurement. It could saturate earlier on
the vision encoder or on EBS/network. *Close by:* one `bench.py -c 8 -n 32` run on
a `g6e.4xlarge`. Until then, do not weight the mixed instances policy — see the
warning below.

**`g6`/`g5` (L4 / A10G, 24 GB) are a separate pool, not an override.** Marlin's
5.4 GB BF16 weights fit in 24 GB, so it would *load*, but throughput and TTFT are
unmeasured on those GPUs and would break the single `X_replica` constant the whole
control law rests on. Put them in a **second ASG behind a second target group at
lower weight** if we ever need them, never as overrides in the same group.

**Two traps with mixed instances groups, both load-bearing here:**

- **Instance weighting breaks warm pools.** *"Warm pools aren't supported with
  weighted mixed instance groups. If your Auto Scaling group uses instance
  weighting, you can't add a warm pool."*
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html)
  So we cannot both weight `g6e.8xlarge` as "4 units" and keep a warm pool. Pick
  one. Given §2.5's conclusion, the warm pool loses.
- **AZ distribution strategy.** ⚠️ The cited page describes three strategies but does
  **not** state which is the default; "balanced best effort is the default" is our
  inference, unconfirmed as of 2026-09-20. The three are: **balanced best effort** —
  *"If launch attempts fail in an Availability Zone, Auto Scaling attempts to launch
  instances in another healthy Availability Zone"* — as opposed to **balanced only**,
  which *"will continue to attempt to launch instances in the Availability Zone"*
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-availability-zone-balanced.html).
  For scarce GPU capacity we want best-effort, and if we hold a reservation we want
  the third option, **reservations then balanced**, which *"first launches instances
  into the Capacity Reservations associated with the group, prioritizing reservation
  use over even distribution"* and is explicitly recommended *"for cost-sensitive
  workloads that use Capacity Reservations, such as GPU, high performance computing
  (HPC), or machine learning workloads"* [same src].

### 2.5 Warm pools: the mechanism, and why it does not help us

A warm pool is *"a pool of pre-initialized EC2 instances that sits alongside an
Auto Scaling group"*; instances can be held `Stopped`, `Running` or `Hibernated`,
and *"Keeping instances in a `Stopped` state is an effective way to minimize
costs. With stopped instances, you pay only for the volumes that you use and the
Elastic IP addresses attached to the instances."*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html)
The measured payoff in AWS's own launch blog: *"launching an instance from the
Warm Pool decreased our launch time from over 4 minutes, to just 36 seconds"*
(243 s → 36 s), and *"Pre-initialized instances in a Warm Pool can be launched to
serve traffic in as little as 30 seconds"*
[src](https://aws.amazon.com/blogs/compute/scaling-your-applications-faster-with-ec2-auto-scaling-warm-pools/).

That is a compelling number. **It does not apply to us**, for four independently
fatal reasons:

1. **`g6e` cannot hibernate.** `HibernationSupported` is `false` for
   `g6e.2xlarge`, `g6e.4xlarge`, `g6e.8xlarge` and `g6.2xlarge`
   [meas. 2026-09-20, `aws ec2 describe-instance-types`], and the hibernation
   prerequisites page lists only General purpose / Compute optimized / Memory
   optimized / Storage optimized families — **no accelerated-computing family at
   all**
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/hibernating-prerequisites.html).
   So the only usable warm-pool state is `Stopped`, which saves nothing of the
   application's startup work — no GPU context, no loaded weights, no captured
   CUDA graphs. It saves the EC2 boot and cloud-init, not the 2–3 minutes that
   actually hurt.
2. **A stopped instance does not hold capacity.** *"You get the
   `InsufficientInstanceCapacity` error when you try to launch a new instance **or
   restart a stopped instance**"*
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html),
   and the warm-pool page concedes the same failure mode: *"If your warm pool is
   depleted when there is a scale-out event, instances will launch directly into the
   Auto Scaling group (a cold start). You could also experience cold starts if an
   Availability Zone is out of capacity."* [warm-pool src]. In a scarce family, a
   stopped warm pool is a promise the platform cannot keep.
3. **The instance store is erased on stop** [instance-store-lifetime src], so a
   stopped `g6e` loses `/opt/dlami/nvme` entirely. Any warm-pool design forces the
   EBS-root layout of §2.2 anyway — at which point the AMI is already doing the work
   the warm pool was supposed to do.
4. **If we hold an ODCR to fix (2), the warm pool's cost saving evaporates.**
   *"Capacity Reservations are charged at the equivalent On-Demand rate whether you
   run instances in reserved capacity or not… If you do not use the reservation,
   this shows up as unused reservation on your Amazon EC2 bill."*
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)
   A reserved-but-stopped `g6e.2xlarge` costs $2.24208/h plus EBS. A reserved-and-
   **running** one costs $2.24208/h and has zero cold start. **There is no version
   of a stopped warm pool that is cheaper than simply running the instance.**

**Decision rule.** Where capacity is scarce and hibernation is unavailable, the
warm pool degenerates into: *either pay for a reservation and run the instance warm
(zero cold start), or don't reserve and accept that scale-out may fail.* The middle
ground that warm pools normally occupy does not exist for `g6e`.

Two more limitations to record for whoever revisits this: *"Warm pools don't
support Spot Instances within mixed instance groups. Your mixed instances policy
must be configured for On-Demand instances only when using warm pools"*
[warm-pool src]; and *"Warm pools are not supported when your launch template
targets a Capacity Block or an interruptible Capacity Reservation"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/launch-template-capacity-blocks.html).
⚠️ Neither page states a restriction for an **ordinary ODCR** consumed via
`--capacity-reservation-specification`; that combination appears legal by
exclusion, but the inference is ours, not AWS's.

### 2.6 Lifecycle hooks: the launch gate and the drain gate

A lifecycle hook *"provides a specified amount of time (one hour by default) to
wait for the action to complete before the instance transitions to the next
state"*; *"The default timeout for a lifecycle hook is one hour (heartbeat
timeout). There is also a global timeout… The global timeout is 48 hours or 100
times the heartbeat timeout, whichever is smaller."*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html)

Two hooks, both mandatory for us:

- **`EC2_INSTANCE_LAUNCHING`** — hold the instance out of the target group until
  `curl localhost:8001/health` returns `{"ok": true}`, i.e. until vLLM has finished
  `torch.compile` and CUDA-graph capture. Without it the ALB will register the
  instance and route a request into a vLLM that is not up, which is a dropped
  request. AWS states this use case exactly: *"A popular use of lifecycle hooks is
  to control when instances are registered with Elastic Load Balancing… you can
  ensure that your bootstrap scripts have completed successfully and the
  applications on the instances are ready to accept traffic before they are
  registered to the load balancer at the end of the lifecycle hook."* [same src]
  Set the heartbeat to ~600 s, matching `TimeoutStartSec=900` in
  [`marlin2b-vllm.service`](../../apps/infrx-api/deploy/marlin2b-vllm.service).
- **`EC2_INSTANCE_TERMINATING`** — drain (§4.1). Note the caveat:
  *"By default, termination lifecycle hooks operate on a best-effort basis. If a
  termination lifecycle hook times out, or is abandoned, Amazon EC2 Auto Scaling
  proceeds with terminating the instance immediately."* [same src]

And the interaction that bites: *"Amazon EC2 Auto Scaling limits the rate at which
it allows instances to launch if the lifecycle hooks are failing consistently, so
make sure to test and fix any permanent errors in your lifecycle actions."*
[same src] A bug in the health-gate script therefore *throttles the whole
autoscaler*, not just one launch.

**Default instance warmup** is the companion setting: it is *"not enabled or
configured by default"*, AWS *"strongly recommend[s]"* enabling it, and
*"If you're not sure how much time you need for the warmup time, you could start
with 300 seconds"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-default-instance-warmup.html).
It is what stops a newly launched replica — whose in-flight count is zero and whose
backlog share is therefore artificially good — from immediately triggering a scale-in.
With a launch lifecycle hook gating readiness, AWS says *"you can reduce the value of
the default instance warmup"* [same src]; 120 s is defensible once the hook is
doing the real waiting.

### 2.7 Instance refresh, and the GPU-scarcity feature almost nobody knows about

Rolling a new AMI or a new `gateway.py` through an ASG is an **instance refresh**.
The default strategy replaces instances, which means every replacement is a fresh
`RunInstances` against a scarce pool — a deploy can *lose* capacity it already had.

AWS shipped the fix, and the doc says out loud what it is for: instance refresh
helps with *"Applying security patches or software updates while preserving
long-running instance state and avoiding capacity constraints with specialized
instance types like GPU or Mac instances"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/asg-instance-refresh.html).

**Replace Root Volume**: *"Root volume replacement updates your instances by
replacing only the root EBS volume while keeping the instance running. This removes
the need to launch new instances and avoids potential capacity constraints."* It
preserves *"Network interfaces and IP addresses, Non-root EBS volumes, Instance
store volumes and data, Security groups and IAM roles"*, and *"The original root
volume is detached, a new root volume is created from your specified AMI, and then
attached to the same instance."*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/replace-root-volume.html)

Requirements and limits that shape our design [same src]:

- *"Your Auto Scaling group must use a mixed instances policy"* and *"All overrides
  in the mixed instances policy must specify an `ImageId`"* — so the mixed instances
  policy we already want for §2.4 is also the precondition for this.
- *"You can't start an instance refresh with Replace Root Volume if the EC2 Auto
  Scaling group or the instance refresh desired configuration uses the `$Latest` or
  `$Default` launch template version."* Pin versions.
- *"You can't start an instance refresh with Replace Root Volume on an EC2 Auto
  Scaling Group that has a warm pool."* Another nail in §2.5's coffin — and a real
  trade: warm pool **or** capacity-preserving deploys, not both. Take the deploys.
- Lifecycle states are `ReplacingRootVolume → :Wait → :Proceed → RootVolumeReplaced`,
  and a termination hook fires at `ReplacingRootVolume:Wait` so we can drain before
  the reboot.

**Caveat:** root-volume replacement reboots the instance, so it does *not* preserve
GPU state or a running vLLM — it only preserves the *allocation*. For us that is the
whole point: the expensive, unobtainable thing is the `g6e` slot, not the process.

### 2.8 Reserved capacity: ODCR yes, Capacity Blocks no

**Capacity Blocks for ML do not cover the `g` family.** The supported-instance-type
table lists `p6-b300.48xlarge`, `p6-b200.48xlarge`, `p5.4xlarge`, `p5.48xlarge`,
`p5e.48xlarge`, `p5en.48xlarge`, `p4d.24xlarge`, `p4de.24xlarge`, `trn1.32xlarge`,
`trn2.3xlarge`, `trn2.48xlarge` — and **no `g5`/`g6`/`g6e`/`g7e` row at all**
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html).
So the mechanism this repo already uses for Blackwell (there is a scheduled
`p6-b300.48xlarge` block in us-east-1b, `cr-04397f3102a3955b7`
[meas. 2026-09-20, `describe-capacity-reservations`]) is **unavailable for Marlin's
GPU**. Record the other facts anyway for Qwen3.8-27B and DeepSeek-V4.1-Flash, which
*will* land on `p5`/`p6`: *"You can reserve a Capacity Block with a reservation start
time up to eight weeks in the future. Each Capacity Block can have up to 64
instances, and you can have up to 256 instances across Capacity Blocks"*;
*"Capacity Blocks end at 11:30AM Coordinated Universal Time (UTC)"*; *"The
termination process for instances running in a Capacity Block begins at 11:00AM
(UTC) on the final day"*; *"Capacity Block cancellations aren't allowed"*
[same src].

**On-Demand Capacity Reservations do cover `g6e`** and are the only guaranteed-
capacity instrument available to us. *"Amazon EC2 Capacity Reservations allow you to
reserve compute capacity for your Amazon EC2 instances in a specific Availability
Zone for any duration"*, with *"no term commitment"* for immediate-use reservations,
which *"you can cancel… at any time to release the reserved capacity and to stop
incurring charges"*
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html).
Billing, again: *"charged at the equivalent On-Demand rate whether you run instances
in reserved capacity or not"*, *"billed at per-second granularity, with a minimum of
60 seconds"*
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html).

Consumption from an ASG uses `CapacityReservationPreference`, whose values are
`default`, `capacity-reservations-only` (*"If no reserved capacity is available,
instances fail to launch"*), `capacity-reservations-first` (*"If no reserved
capacity is available, instances launch as On-Demand"*), and `none`
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/target-capacity-reservations.html).
**`capacity-reservations-first` is the right setting for us**: use the guaranteed
slots first, then try the open market, never fail the launch on principle.

Three more facts that constrain the design:

- *"Active and unused Capacity Reservations count toward your On-Demand Instance
  limits"* and *"Capacity Reservations in the assessing, scheduled, pending, active,
  and delayed state count towards your On-Demand Instance quota"*
  [ODCR src]. Our quota is 192 vCPUs (§2.9), so reservations eat the same budget as
  running instances.
- *"Capacity Reservations do not ensure that a hibernated instance can resume after
  you try to start it"* [ODCR src] — moot for `g6e` (no hibernation) but worth
  recording.
- Future-dated reservations are restricted to *"instance types in the following
  families: C, G, I, M, R, T, U, and X"* with *"a minimum of 32 vCPUs"* [ODCR src].
  `G` is in the list, so a future-dated reservation for **4× `g6e.2xlarge`** (32
  vCPU) is the smallest legal one — relevant if we want capacity guaranteed for a
  launch date rather than held continuously.

### 2.9 Spot: not available to us today, and not attractive when it is

Two measurements settle this:

- **`All G and VT Spot Instance Requests` quota = `0.0` vCPUs**
  [meas. 2026-09-20, `aws service-quotas get-service-quota --service-code ec2 --quota-code L-3819A6DF --region us-east-1`].
  We literally cannot launch a Spot `g6e` today. A quota increase is a support
  ticket, not a code change.
- **Spot Placement Score = 1 (out of 10) in all four `g6e` AZs** for a target
  capacity of 4 units of `g6e.2xlarge`/`4xlarge`/`8xlarge`, single-AZ
  [meas. 2026-09-20, `aws ec2 get-spot-placement-scores` → `use1-az1`, `use1-az2`,
  `use1-az4`, `use1-az6` all scored 1]. AWS's score is its own estimate of how
  likely a Spot request of that shape is to be fulfilled; 1/10 is the floor.

For the record, current Spot prices are genuinely attractive
[meas. 2026-09-20, `aws ec2 describe-spot-price-history`, last hour]:

| type | cheapest AZ | Spot $/h | on-demand $/h | discount |
|---|---|---:|---:|---:|
| `g6e.2xlarge` | us-east-1d | $2.0354 | $2.24208 | 9 % |
| `g6e.4xlarge` | us-east-1b | $1.5562 | $3.00424 | 48 % |
| **`g6e.8xlarge`** | us-east-1d | **$1.8147** | $4.52856 | **60 %** |
| `g6.2xlarge` | us-east-1d | $0.8346 | $0.97760 | 15 % |
| `g5.2xlarge` | us-east-1d | $0.7358 | $1.21200 | 39 % |

`g6e.8xlarge` on Spot at $1.81/h is **cheaper in absolute terms than a
`g6e.2xlarge` on demand** while carrying 4× the vCPUs on the same single L40S —
which, for a vCPU-bound video workload, would be the single largest cost lever in
this document. It is gated entirely behind a quota increase and a 1/10 placement
score.

**If and when Spot becomes usable**, the design that makes it safe is the
**queue-pull worker**, and it is worth stating now because it changes `gateway.py`:
a worker that *pulls* its next unit of work from the shared queue and only deletes
it on success can be killed at any moment without losing a request — the message
simply becomes visible again. SQS gives this for free: *"If you don't delete it
before the timeout expires, the message becomes visible again in the queue and can
be retrieved by another consumer"*, with a default visibility timeout of 30 s, a
*"maximum limit of 12 hours from when the message is first received"*, and a
recommended *"heartbeat mechanism to periodically extend the visibility timeout"*
via `ChangeMessageVisibility`
[src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html).
A push model (ALB → gateway) cannot offer this: a Spot reclaim mid-request is a
dropped request unless the client retries.

The AWS-side machinery is mature. The interruption notice is *"a warning that is
issued two minutes before Amazon EC2 stops or terminates your Spot Instance"*,
delivered as an EventBridge event and at
`http://169.254.169.254/latest/meta-data/spot/instance-action`, and AWS
*"recommend[s] that you check for these interruption notices every 5 seconds"*
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html).
Capacity Rebalancing goes further, acting on the earlier **rebalance
recommendation**: *"When the new Spot Instance launches, Amazon EC2 Auto Scaling
waits until the new instance passes its health check before it terminates the
previous instance"*, and *"Amazon EC2 Auto Scaling can temporarily exceed the
group's maximum size by up to 10 percent of the desired capacity"* to do so
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-capacity-rebalancing.html).
The recommended allocation strategy is `price-capacity-optimized` — AWS says
`lowest-price` is *"not recommended"* because it *"has the highest risk of
interruption"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/allocation-strategies.html).
Also note *"a lifecycle hook does not prevent an instance from terminating in the
event that capacity is no longer available"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) —
the 2-minute budget is hard, and AWS says *"It's critical to design the custom
action to finish in under two minutes"* [capacity-rebalancing src].

Marlin's p99 request is a 120 s clip; a 2-minute drain is marginal for a push
model and comfortable for a pull model. That asymmetry is the argument, more than
the price.

### 2.10 Quota is a first-class capacity constraint

`Running On-Demand G and VT instances` = **192.0 vCPUs**
[meas. 2026-09-20, `aws service-quotas get-service-quota --quota-code L-DB2E81BA --region us-east-1`].
That is the fleet ceiling, and it is shared with reservations:

| shape | max replicas within 192 vCPU | aggregate clips/s at 1.57/replica |
|---|---:|---:|
| all `g6e.2xlarge` (8 vCPU) | 24 | 37.7 |
| all `g6e.4xlarge` (16 vCPU) | 12 | 18.8 (⚠️ per-replica X unmeasured) |
| all `g6e.8xlarge` (32 vCPU) | 6 | 9.4 (⚠️ same) |

Set the ASG `max_size` **below** the quota, in the instance units the mix actually
uses, or a scale-out will fail with `InstanceLimitExceeded` — which, unlike
`InsufficientInstanceCapacity`, is entirely self-inflicted and entirely avoidable.

---

## 3. Cold-start budget for a Marlin worker

### 3.1 The stages, and what we actually know

The generic anatomy is in [`../scaling/06` §1](../scaling/06-cold-start.md). For a
`g6e.2xlarge` running [`serve.sh`](../../models/marlin2b/serve.sh) under
[`marlin2b-vllm.service`](../../apps/infrx-api/deploy/marlin2b-vllm.service):

| # | Stage | Today (pull at boot) | Baked AMI, EBS root | Evidence |
|---|---|---:|---:|---|
| 1 | EC2 provision + boot (BIOS→cloud-init) | ~40–60 s | ~40–60 s | ⚠️ est.; AWS warm-pool blog's non-warm baseline was 243 s end-to-end for a generic app [src](https://aws.amazon.com/blogs/compute/scaling-your-applications-faster-with-ec2-auto-scaling-warm-pools/) |
| 2 | NVIDIA driver / docker ready | ~10–20 s | ~10–20 s | ⚠️ est.; DLAMI ships both |
| 3 | `docker pull vllm/vllm-openai:nightly` (~30 GB) | **~120–400 s** | **0 s** (in the AMI) | ⚠️ est. from image size ÷ instance bandwidth (up to 20 Gbit on `g6e.2xlarge` [meas. `describe-instance-types`]) |
| 4 | `install.sh`: `pip install fastapi uvicorn httpx`, `apt-get install ffmpeg` | ~20–40 s | **0 s** (in the AMI) | ⚠️ est.; both are in [`install.sh`](../../apps/infrx-api/deploy/install.sh) today |
| 5 | Weight load (5.4 GB) | ~20–60 s from HF/S3 | ~10–20 s from EBS (cold blocks) | ⚠️ est. |
| 6 | **`torch.compile` + CUDA-graph capture** | **~120–180 s** | **~10–30 s** with compile cache | **the 2–3 min figure is the repo's own** (task brief; consistent with `TimeoutStartSec=900`); cached-vs-cold ratios below |
| 7 | vLLM profiling / warm-up, first request with a new `mm_processor_kwargs` set | **+18 s** on the first call | +18 s unless pre-warmed | **meas.** — *"The first request with a new kwargs set pays ~18 s"* [[`notes.md`](../../models/marlin2b/results/notes.md) §4] |
| 8 | Lifecycle-hook health gate → ALB registration | ~10–30 s | ~10–30 s | ⚠️ est. |
| | **Total** | **≈ 360–800 s (6–13 min)** | **≈ 100–200 s** | |

### 3.2 The compile cache is the single biggest lever, and it is one env var

vLLM's own documentation states the reuse rule verbatim: *"you can directly copy
the whole `~/.cache/vllm/torch_compile_cache` directory in your deployment scenario
to save a great amount of compilation time, and hence accelerating the starting
time of the vLLM instance"*
[src](https://docs.vllm.ai/en/latest/design/torch_compile.html). The cache key
covers *"all the related configs"*, PyTorch settings, and *"the model's forward
function and the relevant functions called by the forward"* [same src] — so it
survives a weight swap of the same architecture but not a vLLM version bump. Since
`serve.sh` pins `IMAGE=vllm/vllm-openai:nightly`, **the cache is invalidated every
time the nightly tag moves**. Pinning a digest is therefore not a hygiene nicety,
it is a cold-start requirement.

Published cached-vs-cold numbers, all primary or repo-cited:

| Source | Model / hardware | cold compile | cached | Δ |
|---|---|---:|---:|---|
| AWS Containers blog [src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/) | Qwen3.6-35B-A3B (67 GiB), TP=2, `p5.48xlarge` | 82 s total (29 s weights + 53 s compile) | **16 s** (12 s weights + 4 s compile) | compile term alone **53 s → 4 s (−92 %)**; *"The second pod on the same node loads those artifacts in 4s"* |
| same | Llama-4-Scout-17B-16E (203 GiB), TP=4, `p5.48xlarge` | 457 s (423 s weights + 34 s compile) | **32 s** (26 s weights + 6 s compile) | compile term alone **34 s → 6 s (−82 %)**; weights dominate at that size |
| practitioner, via [`../scaling/06` §3.1](../scaling/06-cold-start.md) | Qwen3.6-35B-A3B-NVFP4, GB10 | 317 s | 167 s with `VLLM_CACHE_ROOT` on persistent disk | compile 39 s → 10 s |

⚠️ **Corrected 2026-09-20.** The 82 → 16 s and 457 → 32 s end-to-end improvements
are **not** the compile cache alone. The blog's "after" column combines the Run:ai
Model Streamer, which cuts the *weights* term (29 → 12 s and 423 → 26 s), with the
`torch.compile` cache, which cuts the *compile* term (53 → 4 s and 34 → 6 s). Only
the compile column transfers to a baked-AMI design that does not also change how
weights are read. This table previously credited the whole −80 % / −93 % to the
compile cache; the per-term figures above are the honest ones.

Marlin-2B is far smaller than any of these — 5.4 GB — so **compile, not weight
load, is its dominant term**, exactly the regime where the compile cache *on its
own* was worth **−92 %** of that term.
⚠️ **TO BE VERIFIED** — no cold-start stage timing has ever been recorded for
Marlin on an L40S; §3.1's rows 1–6 are `est.`. This is the same gap
[`../scaling/06` OQ1](../scaling/06-cold-start.md#open-questions) flags for all five
repo models. *Close by:* `journalctl -u marlin2b-vllm --since` around a restart,
with and without a pre-seeded cache directory.

Two mechanical notes:

- The cache location documented today is `~/.cache/vllm/torch_compile_cache/…`;
  `VLLM_DISABLE_COMPILE_CACHE=1` turns it off, and the current page does **not**
  document `VLLM_CACHE_ROOT` [vLLM torch_compile src] — [`../scaling/06`](../scaling/06-cold-start.md)
  records `VLLM_CACHE_ROOT` from a practitioner deployment. Verify which your build
  honours before relying on it.
- `serve.sh` runs vLLM in docker with **no cache volume mounted at all**. Today
  every restart recompiles. Adding `-v /opt/vllm-cache:/root/.cache/vllm` to
  `serve.sh`, with that path on the **EBS root** and pre-seeded in the AMI, is a
  one-line change with the largest single effect in this section. Mind the known
  failure mode: mounting the cache at a path owned by another user *"crash[es]
  **every** cold start"* ([`../scaling/06` §3.1](../scaling/06-cold-start.md), citing
  giantswarm/agent-platform#541) — mount a dedicated subpath the container's UID owns.

### 3.3 Getting under 90 s: what it would actually take

| Lever | Saves | Cost / risk |
|---|---:|---|
| Bake image + weights + compile cache into the AMI (EBS root) | ~150–450 s | AMI build pipeline; 70 GiB snapshot; re-bake on every vLLM bump |
| Provisioned volume-initialization rate 300 MiB/s on the root BDM | makes boot **predictable**, ~239 s to fully hydrate 70 GiB | per-GiB charge on full snapshot data [src](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-initialize.html) |
| Pin the vLLM image by digest, not `:nightly` | keeps the compile cache valid | must re-bake deliberately |
| Trim `cudagraph_capture_sizes` to the batch sizes we actually serve (`--max-num-seqs 32` today) | part of stage 6; capture cost is *(number of captured batch sizes) × (one model forward)* ([`../scaling/06` §3.2](../scaling/06-cold-start.md)) | slightly worse perf at unlisted batch sizes |
| Fire one warm-up request with the production `mm_processor_kwargs` from the launch lifecycle hook, before signalling `CONTINUE` | removes the measured **18 s** first-kwargs penalty from the first *user* request | hook must ship a tiny sample clip in the AMI |
| Elastic-IP-free, ALB-fronted design | removes manual EIP association from the critical path | (already required) |

**Verdict on <90 s: not achievable on EC2 for a docker-based vLLM worker, and we
should stop trying.** Even with everything baked, stages 1+2 (EC2 boot, driver,
docker) are ~50–80 s before a single line of our code runs, and stage 6 with a
warm cache is ~10–30 s. A realistic, defensible target is **p50 ≈ 120 s, p95 ≈ 200 s**.
Designs that assume sub-90 s scale-out — including "just let the queue absorb it"
with a tight wait SLO — must be sized against 200 s, not 90 s.

The mechanisms that *do* reach sub-90 s on GPUs are snapshot/restore
(`cuda-checkpoint`/CRIU, Modal/Cerebrium/ServerlessLLM-style platform
snapshotting) and vLLM sleep mode; all of them are surveyed with their maturity
caveats in [`../scaling/06` §4](../scaling/06-cold-start.md), and none is available
as a managed EC2 feature today. **vLLM sleep mode is the one worth revisiting**: it
keeps a replica that has paid every cold-start stage but has released weights to
host RAM (`POST /sleep?level=1|2`, `POST /wake_up`,
[`../scaling/06` §4.4](../scaling/06-cold-start.md)). On a reserved-and-running
`g6e` it converts an idle replica into a near-instant one, which is the *only*
"warm pool" that actually works for us (§2.5).

### 3.4 The pre-warm rule, and why `N+1` is not enough

The usual formulation — "pre-warm one extra replica when queue age crosses a
threshold" — is arithmetically inadequate for anything but a mild ramp. Suppose
load steps from the capacity of `N` replicas to `k×` that, and a new replica takes
`T` seconds to be ready. At the moment it is ready the backlog is
`(k−1)·N·X·T` requests, and `N+1` replicas drain it only if `(N+1)·X > k·N·X`,
i.e. only if `k < 1 + 1/N`. Numbers, at `X = 1.57 req/s` (recomputed with `python3`,
2026-09-20):

| N | k (step multiple) | T = 90 s | T = 240 s | T = 420 s | can N+1 drain it? |
|---|---|---:|---:|---:|---|
| 1 | 2× | 141 queued → 45 s wait | 377 → 120 s | 659 → 210 s | **no — exactly break-even** |
| 1 | 3× | 283 | 754 | 1,319 | **no** — N+1 is still short of arrivals |
| 2 | 2× | 283 | 754 | 1,319 | **no** |
| 4 | 2× | 565 | 1,507 | 2,638 | **no** |

⚠️ **Corrected 2026-09-20.** The first row was marked "yes (barely)". It is not: at
`N = 1`, `k = 2` the condition is `k < 1 + 1/N = 2`, which `k = 2` **fails**. Two
replicas serve `2 × 1.57 = 3.14 req/s` against arrivals of exactly `3.14 req/s`, so
the backlog is *held* at 141, not drained — the 45 s wait is a permanent floor, not
a transient. Every row in the table is therefore a "no", which strengthens rather
than weakens the rule below.

**Decision rule.** Do not write an `N+1` rule. Let the target-tracking policy on
backlog-per-replica compute the replica count directly — it naturally asks for
`ceil(backlog / target)` replicas, which for a `k×` step is `≈ k·N` — and add a
step-scaling policy on **oldest-request age** as the aggressive tier, so that a
large breach produces a large jump in one action rather than N sequential
cold starts. AWS's step-scaling semantics make this the intended usage: *"You can
define different step adjustments based on the breach size of the alarm"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-simple-step.html).

The one thing `N+1` is right for is the **idle floor**: keep one replica warm above
the count the current load needs, so that the *first* burst of a quiet period does
not start from a cold start. That is `min_size`, not a policy (§4.3).

---

## 4. Scale-down

### 4.1 Draining without dropping a request

The termination lifecycle hook is the whole mechanism. Sequence:

1. ASG decides to terminate instance *i*; instance enters `Terminating:Wait`.
2. EventBridge fires; the instance's own shutdown script (or a Lambda) sees the
   state via IMDS.
3. The gateway on *i* flips to **draining**: it stops accepting new work — for a
   push model, `/health` starts returning 503 so the ALB deregisters it; for a pull
   model, the worker simply stops calling `ReceiveMessage`.
4. In-flight requests finish. Marlin's worst case is bounded and known:
   `MAX_VIDEO_SECONDS = 120` ([`gateway.py`](../../apps/infrx-api/gateway.py), verified
   2026-09-20), `max_tokens ≤ 2048` (a documented API limit in
   [`openrouter/PLAN.md`](../../apps/infrx-api/openrouter/PLAN.md) and
   [`client_example.py`](../../apps/infrx-api/client_example.py) — ⚠️ **not** enforced
   in `gateway.py`), and the measured wall time for a 10 s clip is **0.4–0.8 s** in
   find mode, 12 output tokens [meas. 2026-09-19,
   [`notes.md`](../../models/marlin2b/results/notes.md) §6]. ⚠️ **Corrected
   2026-09-20:** the range previously printed here, "0.4–3.8 s", matches no
   measurement in this repo (the nearest 3.x figure is a *TTFT* p50 of 3.70 s at
   c = 8, not a wall time). The number that should size a drain window is not the
   single-request wall time anyway but the **saturated residence time
   `S_seat = 5.1 s` at c = 8** (§1.2), across the 8 seats a replica holds. A **180 s**
   drain window still covers the p99 comfortably — ~35× `S_seat` — but a 120 s clip
   has never been benchmarked at all (`notes.md` "Open"), so the true worst case is
   ⚠️ unmeasured.
5. `usage.jsonl` and `usage_failed.jsonl` are flushed to S3 (they are on instance
   store and will be erased).
6. `complete-lifecycle-action --lifecycle-action-result CONTINUE`.

Set the heartbeat timeout to 300 s (drain 180 s + flush + margin), well inside the
one-hour default. Remember the best-effort caveat from §2.6 — if the script wedges,
AWS terminates anyway, so the flush must be incremental, not a single end-of-life
upload.

AWS also documents **instance scale-in protection** for exactly the long-task
case, with pseudocode:

```
while (true)
{
  SetInstanceProtection(False);
  Work = GetNextWorkUnit();
  SetInstanceProtection(True);
  ProcessWorkUnit(Work);
  SetInstanceProtection(False);
}
```
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-using-sqs-queue.html)

That pattern assumes one work unit at a time. Our replica holds up to 8. The
adaptation is to set protection when `inflight` goes 0→1 and clear it when it
returns to 0 — a ~6-line change to `gateway.py`'s `inflight` accounting. It is
strictly better than a drain hook for correctness (the ASG picks a different victim
instead of waiting), and strictly worse for responsiveness (scale-in stalls while
any replica is busy). **Use the drain hook as the primary and skip scale-in
protection** until we see requests actually killed mid-flight; protection is the
upgrade path, not the starting point.

### 4.2 Cooldowns, thrash and the asymmetric window

Two reasons to make scale-in much slower than scale-out:

- **The cost of being wrong is asymmetric.** Scaling out one replica too many
  costs $2.24/h. Scaling in one too many costs a 120–200 s cold start during which
  the queue grows at the full arrival rate.
- **AWS already biases this way.** *"While instances are warming up, your dynamic
  scaling policies scale out only if the metric value from instances that are not
  warming up is greater than the policy's alarm high threshold… If demand decreases,
  dynamic scaling becomes more conservative to protect your application's
  availability. This blocks the scale in activities for dynamic scaling until the new
  instances finish warming up."*
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-default-instance-warmup.html)

Our settings, with reasoning:

| Knob | Value | Why |
|---|---|---|
| target-tracking `TargetValue` (backlog/replica) | **47** | 30 s wait SLO ÷ 1.57 req/s (§1.3) |
| `DisableScaleIn` on the target-tracking policy | **true** | scale-in gets its own, slower policy |
| scale-in: step policy on backlog/replica < 15 for **15 consecutive minutes** | −1 replica | 15 min ≫ 200 s cold start, so a false negative costs at most one extra cold start per 15 min |
| `DefaultInstanceWarmup` | **120 s** | with the launch hook gating readiness, AWS says this can be reduced from the 300 s starting point [same src] |
| step-scaling scale-out tiers on oldest-request age | 60 s → +1, 120 s → +2, 300 s → +4 | disproportionate response to a large breach |

Also worth knowing: *"In most cases when lifecycle hooks are invoked, scaling
activities due to simple scaling policies are paused until the lifecycle actions
have completed and the cooldown period has expired"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) —
another reason simple scaling is banned here, since our hooks take minutes.

### 4.3 `min_size`: one warm replica, and no scale-to-zero for the pilot

Scale-to-zero is legal for a Marlin-class model in the bare-metal blueprint
([`../scaling/05` §8.1](../scaling/05-autoscaling-and-predictive-scaling.md) has
Marlin-2B scaling to zero with a cron floor) because there the node exists and
only the replica goes away. **On AWS it is a different bet entirely**, because
returning from zero requires `RunInstances` against a scarce pool. The failure mode
is not "slow first request"; it is "the API is down until `g6e` capacity appears".

SageMaker's managed version of scale-to-zero is worth reading precisely because it
states the consequence plainly: *"after scaling in to zero instances, your endpoint
can't respond to any incoming inference requests until it provisions at least one
instance… Be aware that the provisioning process takes several minutes. During that
time, any attempts to invoke the endpoint will produce an error."*
[src](https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-auto-scaling-zero-instances.html)
Its mechanism — a target-tracking policy on
`SageMakerInferenceComponentInvocationsPerCopy` with `ScaleInCooldown`/
`ScaleOutCooldown` of 300 s for scale-in, plus a **step** policy triggered by a
CloudWatch alarm on `NoCapacityInvocationFailures` (*"SageMaker AI emits this metric
when an endpoint receives an inference request, but the endpoint has no active
instances to serve the request"*), `Cooldown: 60` — is a good template for a
*queue*-triggered wake-up, and is the pattern to copy if we ever do scale to zero.
The `NoCapacityInvocationFailures` idea maps exactly onto "queue is non-empty and
`InService == 0`".

**Pilot decision: `min_size = 1`, `max_size` set by §2.10, no scale to zero.** At
$2.24208/h that floor is **$1,614/month** on a 720-hour month (**$1,637** on AWS's
own 730-hour billing-month convention — flagged 2026-09-20; the document is
internally consistent at 720 h, so both are printed rather than silently recut) — the price of the API existing. Revisit
only when (a) cold start is measured and p95 < 200 s, (b) an ODCR guarantees the
capacity exists, and (c) a customer-facing SLO explicitly allows a multi-minute
first-request latency. Note that (b) makes scale-to-zero pointless anyway, since
the ODCR bills at the full on-demand rate whether or not anything runs
[capacity-reservations-pricing src] — **scale-to-zero and guaranteed capacity are
mutually exclusive on AWS.** That is the cleanest statement of the trade in this
document.

---

## 5. The EKS alternative

### 5.1 When it is worth it

Not for one model on one GPU. The honest rule:

| Condition | EC2 ASG | EKS |
|---|---|---|
| 1 model, 1–8 replicas, one instance family | **yes** | no — a control plane, Karpenter, KEDA and a Prometheus stack to run one process |
| 3+ models (Marlin + Qwen3.8-27B + DeepSeek-V4.1-Flash) with different GPU shapes | painful: 3 ASGs, 3 target groups, no bin-packing | **yes** |
| Need per-pod scheduling on a multi-GPU node | impossible | yes |
| Want the same manifests to run on the bare-metal cluster later | no | **yes** |

The last row is the strategic argument: [`../scaling/10-blueprint.md`](../scaling/10-blueprint.md)
targets a Kubernetes-based platform on 8×B300 nodes, and everything written for
KEDA/Karpenter on EKS transfers, whereas ASG scaling policies do not. **Recommendation:
ship the pilot on an EC2 ASG; write the EKS manifests in the same PR as documentation
so the migration is a port, not a redesign.**

### 5.2 Karpenter NodePool for GPU

Karpenter provisions nodes directly from pending pods, which removes the
ASG-per-shape problem. The requirement keys we need are
`karpenter.k8s.aws/instance-family`, `node.kubernetes.io/instance-type`,
`karpenter.sh/capacity-type` and `topology.kubernetes.io/zone`, with `minValues` to
force diversity
[src](https://karpenter.sh/docs/concepts/nodepools/). Disruption is governed by
`consolidationPolicy` (`WhenEmpty`, `Balanced`, `WhenEmptyOrUnderutilized`),
`consolidateAfter` — *"Karpenter resets this timer whenever a pod is added to or
removed from the node, so a node only becomes a consolidation candidate once it has
been stable for the full `consolidateAfter` duration"* — `expireAfter` (*"By
default, `expireAfter` is set to `720h` (30 days)"*), `terminationGracePeriod`, and
`disruption.budgets`
[src](https://karpenter.sh/docs/concepts/disruption/).

For Spot, Karpenter *"handles most interruption events by watching an SQS queue
which receives critical events from AWS services"* and, on a Spot Interruption
Warning, *"it will begin draining the node while in parallel provisioning a new
node"* [same src] — the wording printed here previously was a paraphrase, corrected
to the verbatim text 2026-09-20.
The `karpenter.sh/do-not-disrupt` annotation (boolean or a Go duration such as
`"30m"`) protects a pod mid-request — but note *"The `karpenter.sh/do-not-disrupt`
annotation does **not** exclude nodes from the forceful disruption methods:
Expiration, Interruption, Node Repair, and manual deletion"* [same src].

Manifests in §7.4.

### 5.3 KEDA on queue length

KEDA is now the recommended metrics path in this ecosystem —
[`../scaling/05` §2.3](../scaling/05-autoscaling-and-predictive-scaling.md) records
llm-d's *"the Prometheus Adapter is planned for deprecation, and it is recommended
to use KEDA instead"* and KServe's `autoscalerClass: "keda"`. llm-d's own workload-
autoscaling guide ranks the signals the same way this document does — queue-based,
saturation-based, token-aware and SLO-aware — with `llm_d_epp_request_running`,
`vllm:num_requests_waiting` and `vllm:kv_cache_usage_perc` as the primary series,
and warns that *"WVA scales on scraped Prometheus data. Stale inputs produce stale
decisions"*
[src](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md).

ScaledObject defaults worth pinning: `pollingInterval: 30`, `cooldownPeriod: 300`,
`initialCooldownPeriod: 0`, `minReplicaCount: 0`, `maxReplicaCount: 100`, plus
`fallback` (`failureThreshold`, `replicas`) and
`advanced.horizontalPodAutoscalerConfig.behavior`
[src](https://keda.sh/docs/2.17/reference/scaledobject-spec/). Note
`minReplicaCount` defaults to **0** — the opposite of §4.3's decision, so it must be
set explicitly.

Two scalers are relevant. The **Redis Lists** scaler takes `address`, `listName`,
`listLength` ("Average target value to trigger scaling actions"),
`activationListLength` (default 0), `enableTLS`, `databaseIndex`
[src](https://keda.sh/docs/2.17/scalers/redis-lists/) and is the natural fit if `03`
picks Redis. The **Prometheus** scaler takes `serverAddress`, `query`, `threshold`,
`activationThreshold` (default 0), `ignoreNullValues` (default `true`), `unsafeSsl`
[src](https://keda.sh/docs/2.17/scalers/prometheus/) and is what we would use to
scale on *oldest-request age*, which no queue scaler exposes directly.

**The failure mode to alert on** is recorded in
[`../scaling/05` §2.3](../scaling/05-autoscaling-and-predictive-scaling.md) from
llm-d's OpenShift notes: *"KEDA silently serves `fallback` replicas when a trigger
errors"*, so a broken query *"looks healthy while doing nothing"*. Alert on scaler
errors, not on replica counts.

### 5.4 Warm capacity on Kubernetes: overprovisioning pause pods

This is the one place Kubernetes genuinely beats the EC2 ASG for us. **Karpenter has
no native warm-capacity feature** — its FAQ documents only the low-priority pause
container as a workaround for topology spread, and *"contains no documentation of
native warm capacity, overprovisioning features, or headroom reservations in
Karpenter itself"*
[src](https://karpenter.sh/docs/faq/) ⚠️ (this is a negative finding from one page;
absence of documentation is not proof of absence). The standard community pattern
is nonetheless well understood: a Deployment of `pause` pods at a
`PriorityClass` with negative value, sized to one replica's resource request, so a
node is already provisioned; when a real pod is scheduled it **preempts** the pause
pod, which is then re-scheduled and triggers Karpenter to provision the next node.

For us that converts a 120–200 s cold start into a pod start against a node that is
already running — but **only the EC2 provisioning half**. The vLLM container still
has to start, load weights and capture graphs on that node unless the image and
compile cache are pre-pulled there. Which means the AMI/EBS work in §2.2–§3.2 is
required on EKS too; it is not an EC2-only tax.

### 5.5 Dynamo Planner: the shape to aim at, not to deploy

NVIDIA's Dynamo Planner is the most complete SLA-native autoscaler in this space and
is worth reading for its vocabulary even though it is far beyond a 2 B video model
on one L40S. It *"adjusts prefill and decode engine replica counts at runtime to
meet latency SLAs"* from *"Forward Pass Metrics (FPM)… per-iteration scheduler
records from inference workers"*, with four optimization targets —
`throughput` (default, *"static thresholds on queue depth and KV cache
utilization"*), `latency`, `load`, and `sla` (*"target specific TTFT/ITL values"*) —
and defaults `ttft_ms: 500.0`, `itl_ms: 50.0`,
`throughput_adjustment_interval_seconds: 180`, `load_adjustment_interval_seconds: 5`,
`load_scaling_down_sensitivity: 80`, `load_predictor: arima` (with `constant`,
`kalman`, `prophet` alternatives)
[src](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/planner/planner-guide.md).

Three ideas transfer today at near-zero cost:

- **`advisory: true`** — *"Advisory mode is suggestion-only. The Planner computes
  recommended replica counts, logs them, exports them as diagnostics… The
  recommendations are not applied"* [same src]. This is the same idea as AWS
  predictive scaling's *forecast only* mode, and it is how any new control law should
  be introduced here: run it against the real queue for two weeks and compare its
  recommendations to what actually happened before letting it act.
- **Asymmetric decision intervals** — 5 s for reactive load, 180 s for
  throughput/predictive. Our equivalent is a 1-minute metric period for scale-out and
  a 15-minute window for scale-in (§4.2).
- **Separate the lower bound from the adjustment** — *"throughput updates the
  lower-bound replicas, then load-based scaling can adjust above that floor"*. Our
  version is scheduled `min_size` as the floor and target tracking above it.

The academic anchor for the queue-first framing is **QLM** (*Queue Management for
SLO-Oriented Large Language Model Serving*, Patke et al., arXiv 2407.00047, revised
2025-02-25), which reports *"40-90%"* SLO-attainment improvement and *"20-400%"*
throughput improvement from a *"Request Waiting Time Estimator and global
scheduler"* over state-of-the-art baselines
[src](https://arxiv.org/abs/2407.00047). The transferable claim is not the numbers —
they are for interactive+batch multiplexing on much larger models — but the
architecture: **a waiting-time estimator is a first-class component**, and it is the
same object we need to return an honest "you are ~42 s from being served" to a
queued caller. Further SLO-scheduling literature (Sarathi-Serve, DistServe,
Llumnix, Andes, Aladdin) is surveyed in
[`../scaling/03` §4](../scaling/03-concurrency-and-admission-control.md); none of it
changes the arithmetic for a 2 B model whose bottleneck is `ffmpeg`.

---

## 6. Capacity planning

### 6.1 Little's Law and the two constants

Everything reduces to two measured numbers and one choice:

```
X_replica = 1.57 req/s    (1080p sources, c=8)  ... 3.58 req/s (360p)   [meas. 2026-09-19]
S_seat    = 5.1 s         (residence time at c=8)
seats     = 8 per replica (in-engine concurrency; MAX_INFLIGHT=16 today, see below)
```

Offered load in erlangs is `a = λ · S_seat`. Replicas needed at a target seat
utilisation `ρ`:

```
N = ceil( λ / (X_replica · ρ) )
```

At `ρ = 0.8` (recomputed with `python3`, 2026-09-20 — the λ=10 / 360p cell read
**3** and is corrected to **4**: `ceil(10 / (3.58 × 0.8)) = ceil(3.49) = 4`):

| λ (req/s) | replicas @1080p | replicas @360p | clips/hour served |
|---:|---:|---:|---:|
| 0.5 | 1 | 1 | 1,800 |
| 1.0 | 1 | 1 | 3,600 |
| 2.5 | 2 | 1 | 9,000 |
| 5.0 | 4 | 2 | 18,000 |
| 10.0 | 8 | **4** | 36,000 |

### 6.2 Erlang-C: what the queue looks like at those sizes

Modelling the fleet as `c = 8N` seats with exponential service at `S_seat = 5.1 s`
(M/M/c), computed here with `python3`:

| λ | a (erlang) | N | seats c | ρ | mean queue wait | P(wait > 10 s) |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 2.55 | 1 | 8 | 0.32 | 0.00 s | 0.0000 |
| 1.0 | 5.10 | 1 | 8 | 0.64 | 0.32 s | 0.0006 |
| 3.0 | 15.30 | 3 | 24 | 0.64 | 0.02 s | <1e-4 |
| 5.0 | 25.50 | 4 | 32 | 0.80 | 0.12 s | <1e-4 |
| 12.0 | 61.20 | 10 | 80 | 0.76 | 0.00 s | <1e-4 |

And the inverse — minimum seats for `P(wait > 10 s) < 1 %`:

| λ | offered load a | seats needed | replicas (8 seats) | achievable ρ |
|---:|---:|---:|---:|---:|
| 0.5 | 2.6 | 5 | 1 | 0.51 |
| 1.0 | 5.1 | 7 | 1 | 0.73 |
| 2.0 | 10.2 | 13 | 2 | 0.78 |
| 4.0 | 20.4 | 23 | 3 | 0.89 |
| 8.0 | 40.8 | 43 | 6 | 0.95 |

**Read this table for its shape, not its digits.** Three caveats, all real:

1. **M/M/c is optimistic on variance.** Marlin's service time is not exponential;
   it is roughly proportional to clip length, which is bounded at 120 s and probably
   bimodal (short demo clips vs. long real ones). An M/G/c approximation with the
   measured coefficient of variation would widen every tail. [`../scaling/05`
   §6.3](../scaling/05-autoscaling-and-predictive-scaling.md) covers where each
   queueing model breaks.
2. **It is pessimistic on service time at low load.** `S_seat = 5.1 s` is the
   *saturated* residence time; at `c = 1` a request finishes in ~2.0 s. So the low-λ
   rows understate performance.
3. **Economies of scale are real and large.** Going from λ=1 to λ=8 lets us run at
   ρ=0.95 instead of ρ=0.73 for the same tail. That is the single strongest argument
   for **one pooled fleet with one queue** rather than per-customer capacity.

### 6.3 Headroom, and where the gateway's current limit sits

`MAX_INFLIGHT = 16` in [`install.sh`](../../apps/infrx-api/deploy/install.sh), while
`serve.sh` is started with `--max-num-seqs 32`
([`marlin2b-vllm.service`](../../apps/infrx-api/deploy/marlin2b-vllm.service)) and
the benchmark that produced `X_replica` used `c = 8`. Three different concurrency
numbers, none derived from the others:

- **8** is where throughput was measured;
- **16** is where the gateway starts refusing;
- **32** is where vLLM would start queueing internally.

The gap between 8 and 16 is unmeasured — we do not know whether `X_replica` rises,
flattens or falls between them (on a vCPU-bound preprocessing stage it may well
fall, as more concurrent `ffprobe`/decode threads contend for 8 vCPUs).
⚠️ **TO BE VERIFIED** — *close by:* `bench.py -c 4,8,12,16,24 -n 32` on distinct
clips, and set `MAX_INFLIGHT` to the knee, not to a round number. Everything in §1.3
and §6.1–6.2 scales linearly with whatever that knee turns out to be.

Headroom rule for the pilot, given a 120–200 s cold start:

```
provisioned_capacity ≥ peak_forecast × 1.25      # 25 % headroom, N+1 minimum
min_size             = 1                          # never zero (§4.3)
max_size             ≤ quota_vCPU / vCPU_per_instance   # 24 for all-2xlarge (§2.10)
```

25 % is chosen so that a 1.25× surprise is absorbed by headroom rather than by the
queue, and anything larger is absorbed by the queue for the ~200 s it takes a
replica to arrive — which at N=1 means a 45 s worst-case wait for a 2× step (§3.4),
inside a 60 s SLO.

### 6.4 Multi-region: not yet, and what would trigger it

The trigger is **capacity, not latency**. Today `g6e` exists in 4 of 6 us-east-1
AZs; if a sustained `InsufficientInstanceCapacity` rate makes us unable to reach
`desired`, the cheapest fix is a second Region in the same ASG-per-Region pattern
with Route 53 latency or weighted records. Note from the pricing sheet that AWS
prices are *"identical in us-east-1, us-east-2 and us-west-2 for every P/G instance
checked"* ([`../cross-cutting/cloud-pricing.md` §3.1](../cross-cutting/cloud-pricing.md)),
so a second Region costs nothing extra per hour — the cost is operational
(Supabase round-trip latency from the gateway, a second ALB, certificate and DNS
work, and usage-event ordering). Defer until the first month where we log
`InsufficientInstanceCapacity` more than once.

---

## 7. Concrete configuration

All of the following is written against the current repo and is intended to be
checked into `apps/infrx-api/deploy/`. `{{…}}` marks a value to fill in.

### 7.1 Launch template

```bash
aws ec2 create-launch-template \
  --launch-template-name marlin2b-worker \
  --region us-east-1 \
  --launch-template-data '{
    "ImageId": "{{ami-marlin2b-baked}}",
    "InstanceType": "g6e.2xlarge",
    "IamInstanceProfile": {"Name": "{{marlin2b-worker-profile}}"},
    "SecurityGroupIds": ["{{sg-marlin2b-worker}}"],
    "MetadataOptions": {"HttpTokens": "required", "HttpPutResponseHopLimit": 2},
    "Monitoring": {"Enabled": true},
    "BlockDeviceMappings": [{
      "DeviceName": "/dev/sda1",
      "Ebs": {
        "VolumeSize": 120,
        "VolumeType": "gp3",
        "Iops": 6000,
        "Throughput": 500,
        "DeleteOnTermination": true,
        "VolumeInitializationRate": 300
      }
    }],
    "TagSpecifications": [{"ResourceType":"instance","Tags":[{"Key":"Name","Value":"marlin2b-worker"}]}],
    "UserData": "{{base64 of the cloud-init below}}"
  }'
```

- `VolumeInitializationRate: 300` is the documented maximum (100–300 MiB/s) and is
  settable *"For EBS volume block device mappings in launch templates"*
  [src](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-initialize.html). It is
  billed per GiB of full snapshot data.
- gp3 at 500 MB/s is below `g6e.2xlarge`'s 625 MB/s EBS ceiling and well below
  `g6e.8xlarge`'s 2,000 MB/s [meas. 2026-09-20, `describe-instance-types`] — size it
  per instance type if the mix is used.
- `HttpTokens: required` because the account may enforce IMDSv2; `HopLimit: 2` so a
  container can reach IMDS for the lifecycle state.

User data, replacing what `install.sh` does at boot today:

```bash
#!/bin/bash
set -euo pipefail
TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
IID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
ASG=marlin2b-asg

# secrets from SSM, exactly as deploy/install.sh does today
/usr/local/bin/marlin2b-write-env      # writes /etc/marlin2b-gateway.env from SSM
systemctl start marlin2b-vllm marlin2b-gateway

# gate readiness: vLLM must answer /health AND one warm-up clip must complete,
# so the first real request does not pay the measured ~18 s first-kwargs penalty
for i in $(seq 1 120); do
  curl -sf localhost:8001/health >/dev/null && break || sleep 5
done
/opt/pytorch/bin/python /opt/marlin2b/warmup.py /opt/marlin2b/samples/sample-10s.mp4 || true

aws autoscaling complete-lifecycle-action --region us-east-1 \
  --lifecycle-hook-name marlin2b-launch --auto-scaling-group-name "$ASG" \
  --instance-id "$IID" --lifecycle-action-result CONTINUE
```

### 7.2 Auto Scaling group, mixed instances, ODCR-first

```bash
aws autoscaling create-auto-scaling-group \
  --auto-scaling-group-name marlin2b-asg \
  --region us-east-1 \
  --min-size 1 --max-size 12 --desired-capacity 1 \
  --vpc-zone-identifier "{{subnet-1a}},{{subnet-1b}},{{subnet-1c}},{{subnet-1d}}" \
  --availability-zone-distribution '{"CapacityDistributionStrategy":"reservations-then-balanced"}' \
  --health-check-type ELB --health-check-grace-period 600 \
  --default-instance-warmup 120 \
  --target-group-arns "{{arn:...:targetgroup/marlin2b/...}}" \
  --capacity-reservation-specification '{
      "CapacityReservationPreference": "capacity-reservations-first",
      "CapacityReservationTarget": {"CapacityReservationIds": ["{{cr-marlin2b-1d}}"]}
  }' \
  --mixed-instances-policy '{
    "LaunchTemplate": {
      "LaunchTemplateSpecification": {"LaunchTemplateName":"marlin2b-worker","Version":"1"},
      "Overrides": [
        {"InstanceType":"g6e.4xlarge","ImageId":"{{ami-marlin2b-baked}}"},
        {"InstanceType":"g6e.2xlarge","ImageId":"{{ami-marlin2b-baked}}"},
        {"InstanceType":"g6e.8xlarge","ImageId":"{{ami-marlin2b-baked}}"}
      ]
    },
    "InstancesDistribution": {
      "OnDemandAllocationStrategy": "prioritized",
      "OnDemandBaseCapacity": 0,
      "OnDemandPercentageAboveBaseCapacity": 100,
      "SpotAllocationStrategy": "price-capacity-optimized"
    }
  }'
```

Notes on every non-obvious choice:

- `CapacityDistributionStrategy: reservations-then-balanced` — ⚠️ **corrected
  2026-09-20** from `balanced-best-effort`, which contradicted §2.4. This group targets
  an ODCR, and AWS is explicit that AZ balance otherwise wins: *"By default, Auto
  Scaling prioritizes Availability Zone balance when consuming Capacity Reservations…
  To prioritize Capacity Reservation utilization over Availability Zone balance, use
  the `reservations-then-balanced` Availability Zone distribution strategy"*
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/target-capacity-reservations.html),
  and recommends that strategy *"for cost-sensitive workloads that use Capacity
  Reservations, such as GPU, high performance computing (HPC), or machine learning
  workloads"*
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-availability-zone-balanced.html).
  Drop back to `balanced-best-effort` only if the ODCR is cancelled.
- `OnDemandPercentageAboveBaseCapacity: 100` because the **Spot quota is 0**
  (§2.9). The `SpotAllocationStrategy` is set anyway so that raising the quota is a
  one-field change, and `price-capacity-optimized` is AWS's recommendation
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/allocation-strategies.html).
- `ImageId` on **every** override — required by Replace Root Volume
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/replace-root-volume.html).
- Launch template **`Version: "1"`, not `$Latest`** — also required by Replace Root
  Volume, and recommended for reservation-targeted groups: *"Point your Auto Scaling
  group to a specific launch template version instead of the `$Default` or `$Latest`
  version"*
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/launch-template-capacity-blocks.html).
- **No instance weighting**, because weighting would forfeit a warm pool
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html)
  — and, more importantly here, because we have no measurement of `g6e.4xlarge`'s
  relative capacity to weight *with* (§2.4).
- **`--max-size` must be set in the worst-case instance unit, not the expected one.**
  With `g6e.8xlarge` in the overrides, 12 replicas would be 384 vCPU — double the
  192-vCPU quota (§2.10), and the group would fail with `InstanceLimitExceeded`
  rather than `InsufficientInstanceCapacity`. **Use `--max-size 6` while
  `g6e.8xlarge` is an override and the quota is 192**; raise it only in lockstep
  with the quota.

Lifecycle hooks:

```bash
aws autoscaling put-lifecycle-hook --region us-east-1 \
  --lifecycle-hook-name marlin2b-launch --auto-scaling-group-name marlin2b-asg \
  --lifecycle-transition autoscaling:EC2_INSTANCE_LAUNCHING \
  --heartbeat-timeout 600 --default-result ABANDON

aws autoscaling put-lifecycle-hook --region us-east-1 \
  --lifecycle-hook-name marlin2b-drain --auto-scaling-group-name marlin2b-asg \
  --lifecycle-transition autoscaling:EC2_INSTANCE_TERMINATING \
  --heartbeat-timeout 300 --default-result CONTINUE
```

`ABANDON` on launch so a worker that never reaches `/health` is terminated and
replaced rather than silently registered; `CONTINUE` on terminate so a wedged drain
script cannot hold an instance for an hour.

### 7.3 Scaling policies

**(a) Primary: target tracking on backlog per replica.** `config-tt.json` —
structurally identical to AWS's metric-math example
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-target-tracking-metric-math.html),
with our queue's depth metric substituted:

```json
{
  "CustomizedMetricSpecification": {
    "Metrics": [
      {
        "Label": "Queue depth (requests waiting for a replica)",
        "Id": "m1",
        "MetricStat": {
          "Metric": {
            "MetricName": "QueueDepth",
            "Namespace": "Infrx/Marlin2B",
            "Dimensions": [{"Name": "Model", "Value": "nemostation/marlin-2b"}]
          },
          "Stat": "Average"
        },
        "ReturnData": false
      },
      {
        "Label": "Group size (InService instances)",
        "Id": "m2",
        "MetricStat": {
          "Metric": {
            "MetricName": "GroupInServiceInstances",
            "Namespace": "AWS/AutoScaling",
            "Dimensions": [{"Name": "AutoScalingGroupName", "Value": "marlin2b-asg"}]
          },
          "Stat": "Average"
        },
        "ReturnData": false
      },
      {
        "Label": "Backlog per replica",
        "Id": "e1",
        "Expression": "m1 / m2",
        "ReturnData": true
      }
    ]
  },
  "TargetValue": 47,
  "DisableScaleIn": true
}
```

```bash
aws autoscaling put-scaling-policy --region us-east-1 \
  --policy-name marlin2b-backlog-tt --auto-scaling-group-name marlin2b-asg \
  --policy-type TargetTrackingScaling --target-tracking-configuration file://config-tt.json
```

`TargetValue: 47` = 30 s acceptable wait × 1.57 req/s per replica (§1.3). If we
adopt server-side transcode-to-480p, re-derive it — do not leave 47 in place with a
3.58 req/s replica, or the fleet will run at a third of the wait SLO and cost 3×.

**(b) Aggressive tier: step scaling on oldest-request age.**

```json
{
  "AdjustmentType": "ChangeInCapacity",
  "MetricAggregationType": "Maximum",
  "StepAdjustments": [
    {"MetricIntervalLowerBound": 0,   "MetricIntervalUpperBound": 60,  "ScalingAdjustment": 1},
    {"MetricIntervalLowerBound": 60,  "MetricIntervalUpperBound": 240, "ScalingAdjustment": 2},
    {"MetricIntervalLowerBound": 240, "ScalingAdjustment": 4}
  ]
}
```

attached to a CloudWatch alarm on `Infrx/Marlin2B QueueOldestAgeSeconds > 60`,
period 60 s, 1 evaluation period, 1 datapoint to alarm. The bounds are *relative to
the breach threshold* when set via the CLI — AWS: *"If you use the AWS CLI or an SDK,
you specify the upper and lower bounds relative to the breach threshold"*
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-simple-step.html) —
so these tiers fire at 60 s, 120 s and 300 s of head-of-queue age.

**(c) Scale-in: a separate, slow step policy.** Alarm on
`backlog per replica < 15` for **15 consecutive 1-minute periods**, adjustment
`-1`. The 15-minute window is deliberately ≫ the 200 s cold start, so the worst case
of a false scale-in is one extra cold start per quarter hour.

**(d) Scheduled floor during demo hours.**

```bash
aws autoscaling put-scheduled-update-group-action --region us-east-1 \
  --auto-scaling-group-name marlin2b-asg --scheduled-action-name demo-hours-up \
  --recurrence "0 13 * * MON-FRI" --min-size 2 --time-zone "UTC"
aws autoscaling put-scheduled-update-group-action --region us-east-1 \
  --auto-scaling-group-name marlin2b-asg --scheduled-action-name demo-hours-down \
  --recurrence "0 23 * * MON-FRI" --min-size 1 --time-zone "UTC"
```

**(e) Reserved capacity.**

```bash
aws ec2 create-capacity-reservation --region us-east-1 \
  --instance-type g6e.2xlarge --instance-platform Linux/UNIX \
  --availability-zone us-east-1d --instance-count 2 \
  --instance-match-criteria targeted --end-date-type unlimited \
  --tag-specifications 'ResourceType=capacity-reservation,Tags=[{Key=Name,Value=marlin2b-floor}]'
```

`--instance-match-criteria targeted` so nothing else in the account drifts into it.
Cost: **2 × $2.24208/h = $3,228/month** (720 h; **$3,273** at the 730-hour
convention), billed whether or not the instances run
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html).
That buys the guarantee that `min_size = 1` can always be met and that the first
scale-out step always succeeds.

**(f) ALB.** Raise the idle timeout above the longest streamed response:

```bash
aws elbv2 modify-load-balancer-attributes --region us-east-1 \
  --load-balancer-arn {{arn}} \
  --attributes Key=idle_timeout.timeout_seconds,Value=600 \
               Key=client_keep_alive.seconds,Value=3600
```

Default is 60 s, which would cut a queued request loose
[src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html).
600 s matches the `read_timeout 600s` already in
[`deploy/Caddyfile`](../../apps/infrx-api/deploy/Caddyfile).

**(g) Deploys use Replace Root Volume, not instance replacement.**

```bash
aws autoscaling start-instance-refresh --region us-east-1 --cli-input-json '{
  "AutoScalingGroupName": "marlin2b-asg",
  "Strategy": "ReplaceRootVolume",
  "DesiredConfiguration": {
    "MixedInstancesPolicy": {
      "LaunchTemplate": {
        "LaunchTemplateSpecification": {"LaunchTemplateName":"marlin2b-worker","Version":"2"},
        "Overrides": [
          {"InstanceType":"g6e.4xlarge","ImageId":"{{ami-new}}"},
          {"InstanceType":"g6e.2xlarge","ImageId":"{{ami-new}}"},
          {"InstanceType":"g6e.8xlarge","ImageId":"{{ami-new}}"}
        ]
      }
    }
  },
  "Preferences": {"InstanceWarmup": 120, "MinHealthyPercentage": 50, "AutoRollback": true}
}'
```

`MinHealthyPercentage: 50` because at `N = 1` any higher value blocks the refresh
entirely; `AutoRollback: true` so a bad AMI reverts without a human.

### 7.4 The EKS equivalents

```yaml
# NodePool: one L40S per node, four AZs, three sizes, on-demand only until the
# Spot quota is raised. Field names per karpenter.sh/docs/concepts/nodepools/
apiVersion: karpenter.sh/v1
kind: NodePool
metadata: {name: marlin2b-l40s}
spec:
  template:
    metadata:
      labels: {workload: marlin2b}
    spec:
      nodeClassRef: {group: karpenter.k8s.aws, kind: EC2NodeClass, name: marlin2b}
      requirements:
        - {key: "kubernetes.io/arch",              operator: In, values: ["amd64"]}
        - {key: "karpenter.sh/capacity-type",      operator: In, values: ["on-demand"]}
        - {key: "node.kubernetes.io/instance-type",operator: In,
           values: ["g6e.2xlarge","g6e.4xlarge","g6e.8xlarge"], minValues: 2}
        - {key: "topology.kubernetes.io/zone",     operator: In,
           values: ["us-east-1a","us-east-1b","us-east-1c","us-east-1d"], minValues: 2}
      expireAfter: 720h
      terminationGracePeriod: 10m          # ≥ the 180 s drain of §4.1
  disruption:
    consolidationPolicy: WhenEmpty         # never consolidate a busy GPU node
    consolidateAfter: 15m                  # matches the ASG scale-in window
    budgets:
      - nodes: "1"                         # at most one node disrupted at a time
  limits:
    cpu: "192"                             # the account's G/VT vCPU quota (§2.10)
  weight: 10
```

```yaml
# KEDA: queue depth as the primary trigger, head-of-queue age as the panic trigger.
# Defaults per keda.sh/docs/2.17/reference/scaledobject-spec/
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata: {name: marlin2b, namespace: inference}
spec:
  scaleTargetRef: {name: marlin2b-vllm}
  pollingInterval: 15                      # default 30; we want faster scale-out
  cooldownPeriod: 900                      # default 300; ours is deliberately slow
  minReplicaCount: 1                       # default is 0 — must be set (§4.3)
  maxReplicaCount: 6
  fallback:
    failureThreshold: 3
    replicas: 2                            # ALERT on scaler errors; see §5.3
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 0
          policies: [{type: Percent, value: 100, periodSeconds: 60}]
        scaleDown:
          stabilizationWindowSeconds: 900
          policies: [{type: Pods, value: 1, periodSeconds: 300}]
  triggers:
    - type: redis
      metadata:
        address: {{redis-host}}:6379
        listName: marlin2b:queue
        listLength: "47"                   # backlog per replica, §1.3
    - type: prometheus
      metadata:
        serverAddress: http://prometheus.monitoring:9090
        query: max(infrx_queue_oldest_age_seconds{model="nemostation/marlin-2b"})
        threshold: "60"
        activationThreshold: "5"
```

```yaml
# Warm capacity: one replica's worth of pre-provisioned node, preempted on demand.
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata: {name: overprovision}
value: -10
globalDefault: false
description: "Placeholder pods; preempted by real Marlin pods."
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: marlin2b-headroom, namespace: inference}
spec:
  replicas: 1
  selector: {matchLabels: {app: marlin2b-headroom}}
  template:
    metadata: {labels: {app: marlin2b-headroom}}
    spec:
      priorityClassName: overprovision
      terminationGracePeriodSeconds: 0
      nodeSelector: {workload: marlin2b}
      containers:
        - name: pause
          image: registry.k8s.io/pause:3.10
          resources:
            requests: {cpu: "7", memory: 56Gi, nvidia.com/gpu: "1"}
```

⚠️ The pause-pod pattern is the community workaround, not a documented Karpenter
feature; the Karpenter FAQ mentions low-priority pause containers only as a legacy
topology-spread workaround [src](https://karpenter.sh/docs/faq/). Validate it end to
end before depending on it.

### 7.5 Expected time to capacity, per mechanism

| Mechanism | Time to first served request | Capacity guaranteed? | Cost when idle | Evidence |
|---|---:|---|---|---|
| Already-running replica with free seats | **0 s** | n/a | $2.24/h | — |
| vLLM sleep-mode replica woken (`POST /wake_up`) | **seconds** ⚠️ unmeasured | yes (instance held) | $2.24/h | [`../scaling/06` §4.4](../scaling/06-cold-start.md) |
| ASG scale-out into an **ODCR** | **~120–200 s** ⚠️ est. (§3) | **yes** | $2.24/h per reserved slot | [ODCR billing src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html) |
| ASG scale-out, on-demand, mixed types × 4 AZs | **~120–200 s** ⚠️ est., **or never** | no | $0 | [allocation-strategies src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/allocation-strategies.html) |
| ASG scale-out, single type, single AZ (today's shape) | ~120–200 s, or `InsufficientInstanceCapacity` | no | $0 | [troubleshooting src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html) |
| Warm pool, `Stopped` (hypothetical) | ~36–60 s **if the restart succeeds** | **no** — restart can fail | EBS only | 243 s→36 s [warm-pool blog](https://aws.amazon.com/blogs/compute/scaling-your-applications-faster-with-ec2-auto-scaling-warm-pools/); restart-fails [troubleshooting src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html) |
| Warm pool, `Hibernated` | — | **impossible on `g6e`** | — | `HibernationSupported=false` [meas. 2026-09-20]; family list [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/hibernating-prerequisites.html) |
| Spot scale-out | ~120–200 s | **no** — quota 0, SPS 1/10 | $0 | [meas. 2026-09-20] |
| Capacity Block | — | **not offered for `g`** | — | [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html) |
| EKS + Karpenter, headroom pod present | **~30–60 s** (pod start on a warm node) ⚠️ est. | no (node already exists) | one idle GPU node | [src](https://karpenter.sh/docs/faq/) ⚠️ |
| EKS + Karpenter, no headroom | ~150–250 s ⚠️ est. (node + image + compile) | no | $0 | — |
| New Region | hours–days | no | $0 | §6.4 |

---

## Implications for our system

In the order the work should be done.

1. **Measure the cold start and the concurrency knee before writing a single
   scaling policy.** Two runs, both cheap: (a) `journalctl -u marlin2b-vllm` around
   a restart, with and without a pre-seeded `torch_compile_cache`, to get the real
   stage-6 numbers; (b) `bench.py -c 4,8,12,16,24 -n 32` on **distinct** clips to
   find the knee and retire the `MAX_INFLIGHT=16` / `--max-num-seqs 32` /
   `benchmarked at c=8` inconsistency. Every threshold in §7 is a function of these
   two numbers, and today all of them are estimates.
   *Changes:* `models/marlin2b/results/notes.md`, a new row in
   `models/marlin2b/README.md`'s results table.

2. **Mount a persistent `torch.compile` cache in `serve.sh`.** One `-v` flag,
   the largest single cold-start win available, and it costs nothing to do now on
   the existing single box.
   *Changes:* [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh) (add
   `-v /opt/vllm-cache:/root/.cache/vllm`), and pin `IMAGE` to a digest rather than
   `:nightly` so the cache stays valid.

3. **Move state off the instance store.** `/opt/dlami/nvme` is erased on stop and on
   termination [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-store-lifetime.html),
   so weights, the compile cache and `usage.jsonl` must move to the EBS root or to
   S3 before any ASG exists. `usage_failed.jsonl` in particular is today's only
   durability net for billing data
   ([`apps/infrx-api/README.md`](../../apps/infrx-api/README.md)) and it lives on
   ephemeral disk.
   *Changes:* `models/marlin2b/model.env` (`WEIGHTS_ROOT`),
   `apps/infrx-api/deploy/marlin2b-gateway.service` (`USAGE_LOG`),
   `apps/infrx-api/deploy/install.sh`.

4. **Build a baked AMI and an AMI pipeline.** Image + weights + compile cache +
   `ffmpeg` + the Python deps, on a gp3 root with
   `VolumeInitializationRate: 300`. This deletes most of what `install.sh` does at
   boot and turns it into a build-time step.
   *Changes:* new `apps/infrx-api/deploy/ami/` (Packer or an EC2 Image Builder
   recipe); `install.sh` shrinks to "write `/etc/marlin2b-gateway.env` from SSM and
   start the units".

5. **Terminate TLS at an ALB and delete per-instance Caddy.** With
   `idle_timeout.timeout_seconds = 600`. This is a precondition for more than one
   replica and it removes the Elastic IP from the critical path.
   *Changes:* delete `apps/infrx-api/deploy/Caddyfile` and the `docker run caddy`
   block in `install.sh`; `models/marlin2b/README.md`'s "Public endpoint" section;
   Route 53 record for `marlin2b.callbill.ai` becomes an ALB alias.

6. **Replace the 429 with a bounded queue and publish two CloudWatch metrics.**
   `gateway.py` today returns 429 above `MAX_INFLIGHT` with `Retry-After: 2`. The
   autoscaler needs `Infrx/Marlin2B QueueDepth` and
   `Infrx/Marlin2B QueueOldestAgeSeconds` to exist at all; `03` owns the queue's
   design, this document owns the two metrics it must emit and the honest wait
   estimate `position ÷ (N × X_replica)` that QLM calls a "Request Waiting Time
   Estimator" [src](https://arxiv.org/abs/2407.00047).
   *Changes:* `apps/infrx-api/gateway.py`, and the error-codes section of the console
   docs page (`apps/app/app/(console)/docs/`), since 429 stops being the normal
   overload response.

7. **Create the ASG, mixed across `g6e.2xl/4xl/8xl` in four AZs, with
   `capacity-reservations-first` against a 2-instance ODCR in us-east-1d.** Set
   `max-size 6` — **not 12** — until the 192-vCPU G quota is raised, or a scale-out
   will hit `InstanceLimitExceeded`.
   *Changes:* new `apps/infrx-api/deploy/asg/` with the JSON of §7.1–§7.3.

8. **Raise two quotas, in this order:** `Running On-Demand G and VT instances`
   (L-DB2E81BA, currently 192 vCPU) to whatever `max_size` demands, and
   `All G and VT Spot Instance Requests` (L-3819A6DF, **currently 0**) so that
   `g6e.8xlarge` Spot at $1.81/h becomes reachable. The second is the largest cost
   lever in this document and is blocked purely on a support ticket.

9. **Add `g6e`, `g6` and `g5` rows to
   [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §3.1.**
   That file is the repo's declared price source of truth
   ([`CLAUDE.md`](../../CLAUDE.md): *"Prices come only from
   `research/cross-cutting/cloud-pricing.md`"*) and it has **no `g6e` row**, so every
   Marlin cost figure in the repo — including `notes.md`'s `$0.02–0.04 per
   video-hour` — currently cites nothing. The measured values are in §2.4 and §2.9 of
   this document.

10. **Deploy with `Strategy: ReplaceRootVolume`, never plain instance refresh.**
    In a scarce family, replacing instances risks losing allocations we cannot get
    back. This forces two upstream choices we want anyway: a mixed instances policy
    with `ImageId` on every override, and pinned launch-template versions.

11. **Write the EKS manifests now, deploy them later.** §7.4 is ~100 lines that
    make the eventual move to the bare-metal cluster
    ([`../scaling/10-blueprint.md`](../scaling/10-blueprint.md)) a port rather than a
    redesign. Do not run EKS for one model on one GPU.

12. **Do not build predictive scaling, a custom controller, scale-to-zero, or a
    warm pool.** Predictive scaling has no history to learn from and explicitly
    distrusts mixed instances groups; a custom controller is a pager; scale-to-zero
    is mutually exclusive with the ODCR that makes scale-up reliable; and the warm
    pool is defeated by `HibernationSupported = false`, by instance-store erasure, by
    `InsufficientInstanceCapacity` on restart, and by its incompatibility with
    Replace Root Volume. Each of those is a documented AWS fact, not a preference.

---

## Open questions

Consolidated ⚠️ items, each with the measurement that closes it.

1. **No end-to-end cold-start measurement exists for Marlin on an L40S.** Every
   stage figure in §3.1 except the 18 s first-kwargs penalty is `est.`, and the
   §7.5 time-to-capacity table inherits that. *Close by:* time a restart with and
   without a seeded compile cache, and a fresh launch from a baked AMI at default /
   100 / 300 MiB/s initialization rates. (This is the AWS-specific instance of
   [`../scaling/06` OQ1](../scaling/06-cold-start.md#open-questions).)

2. **`X_replica = 1.57 req/s` may be optimistic.** Both benchmark rows reused one
   clip, so vLLM's multimodal processor cache may have absorbed decode cost
   ([`notes.md`](../../models/marlin2b/results/notes.md) §5 flags this itself).
   Everything in §1.3, §6.1 and §6.2 scales linearly with it. *Close by:*
   `bench.py -c 8 -n 32` with distinct clips.

3. **The concurrency knee is unknown.** `MAX_INFLIGHT = 16`, `--max-num-seqs 32`,
   benchmarked at `c = 8` — three numbers, no measurement between them. On an
   8-vCPU box more concurrency may *reduce* throughput. *Close by:* a concurrency
   sweep; then set `MAX_INFLIGHT` to the knee.

4. **`g6e.4xlarge` / `8xlarge` per-replica throughput is unmeasured.** The whole
   $/clip argument for preferring larger sizes (§2.4) and the instance-weighting
   question rest on the assumption that clips/s scales with vCPU. *Close by:* one
   `bench.py` run per size.

5. **Whether an ordinary ODCR is compatible with an ASG warm pool is inferred, not
   documented.** AWS documents the incompatibility for Capacity Blocks and
   interruptible Capacity Reservations
   [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/launch-template-capacity-blocks.html)
   and says nothing about plain ODCRs. Moot under §2.5's recommendation, but it
   would matter if hibernation ever reaches the `g` family. *Close by:* AWS Support,
   or an experiment.

6. **Karpenter's pause-pod headroom pattern is a community workaround.** The
   Karpenter FAQ documents no native warm-capacity feature
   [src](https://karpenter.sh/docs/faq/); absence from one page is not proof of
   absence. *Close by:* test the preemption path end to end on a GPU NodePool before
   relying on §7.4's third manifest.

7. **The `g6e` Spot quota of 0 and the Spot Placement Score of 1/10 are a snapshot.**
   Both are account- and day-specific [meas. 2026-09-20]. The 60 % discount on
   `g6e.8xlarge` is the largest unexploited cost lever here. *Close by:* file the
   quota increase and re-run `get-spot-placement-scores` weekly.

8. **The traffic scenarios this document sizes against are placeholders.** §6.1–6.2
   use λ ∈ {0.5 … 12} req/s because the API has no users yet. They must be
   reconciled with the three scenarios in sibling document `01` in this directory
   once it lands; if `01`'s peak exceeds 12 req/s the quota work in Implications #8
   becomes urgent rather than opportunistic.

9. **ALB behaviour on a long streamed SSE response is assumed, not tested.** We set
   `idle_timeout.timeout_seconds = 600` on the strength of the attribute
   documentation
   [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html),
   but have not verified that a Marlin stream with a 30 s gap between tokens (a
   queued request that has not started generating) survives. *Close by:* one test
   through the ALB with an artificially delayed first token.

10. **Whether `VLLM_CACHE_ROOT` is honoured by the pinned vLLM build is unverified.**
    The current `torch_compile` design page documents the cache directory and
    `VLLM_DISABLE_COMPILE_CACHE` but **not** `VLLM_CACHE_ROOT`
    [src](https://docs.vllm.ai/en/latest/design/torch_compile.html), while
    [`../scaling/06` §3.1](../scaling/06-cold-start.md) records it from a
    practitioner deployment. *Close by:* `docker run … vllm --help` / env probe on
    the pinned digest.

---

## Sources

All fetched or executed 2026-09-20 unless stated.

### AWS — EC2 Auto Scaling
- [Scaling policy based on Amazon SQS](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-using-sqs-queue.html) — backlog-per-instance formula, acceptable-backlog derivation, scale-in protection pseudocode
- [Create a target tracking scaling policy using metric math](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-target-tracking-metric-math.html) — the `m1/m2` policy JSON
- [Step and simple scaling policies](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-simple-step.html) — step adjustments, adjustment types, CLI-relative bounds, flapping
- [Set the default instance warmup](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-default-instance-warmup.html) — 300 s starting point, scale-in blocking during warmup
- [Predictive scaling](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-predictive-scaling.html) and [How predictive scaling works](https://docs.aws.amazon.com/autoscaling/ec2/userguide/predictive-scaling-policy-overview.html) — 24 h minimum, 14 d window, 48 h hourly forecast, 6 h refresh, `SchedulingBufferTime`, `MaxCapacityBreachBehavior`, mixed-group warning, max-of-policies rule
- [Lifecycle hooks](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) — 1 h default heartbeat, 48 h / 100× global timeout, best-effort terminate, launch-rate throttling on failing hooks
- [Warm pools](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html) — states, sizing, `MaxGroupPreparedCapacity`, instance reuse policy, EBS-root requirement, weighting and Spot limitations, depleted-pool cold start
- [Mixed instances groups](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-mixed-instances-groups.html) and [Allocation strategies](https://docs.aws.amazon.com/autoscaling/ec2/userguide/allocation-strategies.html) — `price-capacity-optimized` recommended, `lowest-price` not recommended, `prioritized` for On-Demand
- [Availability Zone distribution](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-availability-zone-balanced.html) — balanced-best-effort / balanced-only / reservations-then-balanced
- [Capacity Rebalancing](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-capacity-rebalancing.html) — launch-before-terminate, 10 % max-size overshoot, under-two-minutes rule
- [Instance refresh](https://docs.aws.amazon.com/autoscaling/ec2/userguide/asg-instance-refresh.html) and [Replace root volumes](https://docs.aws.amazon.com/autoscaling/ec2/userguide/replace-root-volume.html) — the GPU/Mac capacity-constraint use case, requirements, warm-pool incompatibility, lifecycle states
- [Target Capacity Reservations from your ASG](https://docs.aws.amazon.com/autoscaling/ec2/userguide/target-capacity-reservations.html) — preference values and AZ-balance behaviour
- [Configure your ASG to launch with Capacity Reservations](https://docs.aws.amazon.com/autoscaling/ec2/userguide/launch-template-capacity-blocks.html) — `capacity-reservation-specification` CLI, market-type targeting, warm-pool exclusion, scale-in-before-block-end guidance

### AWS — EC2, EBS, ELB, SQS, SageMaker
- [Troubleshoot instance launch issues](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html) — `InsufficientInstanceCapacity` including on restart of a stopped instance; remedies
- [On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html) — AZ-specific assurance, quota interaction, future-dated G-family minimum 32 vCPU, hibernation caveat
- [Capacity Reservation pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html) — charged at On-Demand rate whether used or not
- [Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html) — supported instance types (no `g` family), 8-week horizon, 64/256 limits, 11:30 UTC end
- [Spot Instance interruption notices](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html) — two-minute warning, IMDS path, EventBridge event, 5 s polling
- [Prerequisites for instance hibernation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/hibernating-prerequisites.html) — supported families (no accelerated computing), <150 GiB RAM, encrypted EBS root
- [Instance store data persistence](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-store-lifetime.html) — erased on stop / hibernate / terminate
- [Initialize Amazon EBS volumes](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-initialize.html) — 100–300 MiB/s provisioned rate, 5,000 MiB/s regional cap, FSR interaction, launch-template BDM support
- [Application Load Balancers](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html) — `idle_timeout.timeout_seconds` default 60 s, `client_keep_alive.seconds` default 3600 s
- [Amazon SQS visibility timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) — 30 s default, 12 h ceiling, heartbeat via `ChangeMessageVisibility`, ~120,000 in-flight standard-queue limit
- [Scale a SageMaker endpoint to zero instances](https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-auto-scaling-zero-instances.html) — the `NoCapacityInvocationFailures` wake-up pattern, 300 s cooldowns, "any attempts to invoke the endpoint will produce an error"
- [Automatic scaling of SageMaker models](https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-auto-scaling.html) — index of the managed options
- [Scaling your applications faster with EC2 Auto Scaling Warm Pools](https://aws.amazon.com/blogs/compute/scaling-your-applications-faster-with-ec2-auto-scaling-warm-pools/) — measured 243 s → 36 s
- [Fast model loading for AI inference on Amazon EKS](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/) — 82 s → 16 s and 457 s → 32 s on cached relaunch; "the second pod on the same node loads those artifacts in 4s"
- [us-east-1 Linux on-demand price sheet](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) — `g6e.2xlarge` $2.24208, `g6e.4xlarge` $3.00424, `g6e.8xlarge` $4.52856, `g6.2xlarge` $0.97760, `g5.2xlarge` $1.21200 (fetched 2026-09-20)

### Live account measurements (account 641134885443, us-east-1, 2026-09-20)
- `aws ec2 describe-instance-type-offerings --location-type availability-zone` — `g6e` in us-east-1a/b/c/d only; `g6`/`g5` also in 1f
- `aws ec2 describe-availability-zones` — `1a=use1-az1, 1b=use1-az2, 1c=use1-az4, 1d=use1-az6, 1e=use1-az3, 1f=use1-az5`
- `aws ec2 describe-instance-types` — `HibernationSupported=false` for `g6e.2/4/8xlarge` and `g6.2xlarge`; instance store 450/600/900 GB; EBS max throughput 625/1000/2000 MB/s; 8/16/32 vCPU; L40S 45,776 MiB
- `aws ec2 get-spot-placement-scores --target-capacity 4 --single-availability-zone` — score **1** in all four `g6e` AZs
- `aws ec2 describe-spot-price-history` (last hour) — `g6e.8xlarge` $1.8147 in 1d; `g6e.4xlarge` $1.5562 in 1b; `g6e.2xlarge` $2.0354 in 1d
- `aws service-quotas get-service-quota` — L-DB2E81BA (`Running On-Demand G and VT instances`) = **192 vCPU**; L-3819A6DF (`All G and VT Spot Instance Requests`) = **0**
- `aws ec2 describe-capacity-reservations` — one `p6-b300.48xlarge` reservation `cr-04397f3102a3955b7` in us-east-1b, state `scheduled`
- `aws ec2 describe-instances / describe-volumes / describe-images` — `i-0e8449a4ffca29bab`, `g6e.2xlarge`, us-east-1d, EBS root `vol-091e45c92f7426291` gp3 300 GiB / 3000 IOPS / 125 MB/s, AMI `ami-0a4870b172edcb0f2` (*Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.12 (Ubuntu 24.04) 20260827*, 30 GiB gp3 root)

### Engines, autoscalers, papers
- [vLLM — Metrics design](https://docs.vllm.ai/en/stable/design/metrics/) — v1 metric names and descriptions
- [vLLM — torch.compile integration](https://docs.vllm.ai/en/latest/design/torch_compile.html) — cache directory, cache key, "directly copy the whole … directory", `VLLM_DISABLE_COMPILE_CACHE`, piecewise CUDA graphs
- [vLLM — Automatic prefix caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html) — not on by default; `enable_prefix_caching`
- [vLLM — Optimization and tuning](https://docs.vllm.ai/en/latest/configuration/optimization.html) — multimodal processor cache on by default, `mm_processor_cache_gb` default 4 GiB, `max_num_batched_tokens` guidance
- [Karpenter — NodePools](https://karpenter.sh/docs/concepts/nodepools/), [Disruption](https://karpenter.sh/docs/concepts/disruption/), [FAQ](https://karpenter.sh/docs/faq/) — requirement keys and `minValues`, consolidation policies, `consolidateAfter`, `expireAfter` 720 h default, disruption budgets, SQS interruption queue, `do-not-disrupt`, absence of native warm capacity
- [KEDA — ScaledObject specification](https://keda.sh/docs/2.17/reference/scaledobject-spec/) — `pollingInterval` 30, `cooldownPeriod` 300, `minReplicaCount` 0, `fallback`, HPA `behavior`
- [KEDA — Redis Lists scaler](https://keda.sh/docs/2.17/scalers/redis-lists/) and [Prometheus scaler](https://keda.sh/docs/2.17/scalers/prometheus/) — trigger fields and defaults
- [llm-d — workload autoscaling guide](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md) — queue / saturation / token-aware / SLO-aware signal families, KEDA as the recommended path, metric-staleness warning
- [NVIDIA Dynamo — Planner guide](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/planner/planner-guide.md) — four optimization targets, `advisory` mode, adjustment intervals, `ttft_ms`/`itl_ms` defaults, ARIMA/Kalman/Prophet predictors, `dynamo_planner_*` metrics
- [Patke et al., *Queue Management for SLO-Oriented Large Language Model Serving*, arXiv 2407.00047](https://arxiv.org/abs/2407.00047) — Request Waiting Time Estimator + global scheduler; 40–90 % SLO attainment and 20–400 % throughput improvement

### This repository
- [`apps/README.md`](../../apps/README.md) — console + gateway spec, data model, non-functional requirements
- [`apps/infrx-api/README.md`](../../apps/infrx-api/README.md) — gateway architecture, auth cache, usage ingestion, replay
- [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) — `MAX_INFLIGHT`, the 429 path, `inflight` accounting, video budget
- [`apps/infrx-api/deploy/`](../../apps/infrx-api/deploy/) — `install.sh`, `marlin2b-vllm.service` (`TimeoutStartSec=900`, `--max-num-seqs 32`), `marlin2b-gateway.service`, `Caddyfile` (`read_timeout 600s`)
- [`models/marlin2b/README.md`](../../models/marlin2b/README.md) — box layout, public endpoint, results table
- [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) — the measured findings this document's capacity constants come from
- [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh) — `--hf-overrides`, `--max-model-len 32768`, no cache volume
- [`../scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md) — admission-control ladder, SLO scheduling literature, video concurrency
- [`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md) — signal ladder, engine metric names, KEDA traps, queueing models, scale-to-zero economics
- [`../scaling/06-cold-start.md`](../scaling/06-cold-start.md) — cold-start anatomy, compile caches, snapshot/restore maturity, vLLM sleep mode, warm-pool sizing formula
- [`../scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md) — runbooks, alerting baseline
- [`../scaling/10-blueprint.md`](../scaling/10-blueprint.md) — the bare-metal target this AWS design must port to
- [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) — engine-level levers
- [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) — the repo's price source of truth (currently **missing** every `g`-family row)

---

## Verification log (2026-09-20)

Adversarial fact-check of the 25 most consequential claims in this document —
AWS quotas, limits, timeouts and prices; vLLM flags and metric names; engine and
paper numbers; every derivation; and every statement about this repo's own code
and measurements (checked by opening the files, not by trusting the citation).
Primary sources were re-fetched on 2026-09-20. Derivations were recomputed with
`python3`. **6 CORRECTED, 17 CONFIRMED, 2 UNVERIFIABLE.**

### Corrected

| # | § | Was | Is | Source |
|---|---|---|---|---|
| C1 | §6.1 | λ = 10 req/s, 360p → **3** replicas | **4** — `ceil(10 / (3.58 × 0.8)) = ceil(3.4916) = 4` | recomputed, `python3` |
| C2 | §3.4 | `N = 1`, `k = 2×` → "yes (barely)" drains | **no — exactly break-even.** `k < 1 + 1/N = 2` fails at `k = 2`; 2 replicas serve 3.14 req/s against 3.14 req/s of arrivals, so the 141-request backlog is held, not drained, and the 45 s wait is a floor | the document's own inequality |
| C3 | §4.1 | "measured wall time for a 10 s clip is **0.4–3.8 s**" | **0.4–0.8 s** (find mode, 12 output tokens); 3.8 s appears in no repo measurement — the nearest 3.x figure is a *TTFT* p50 of 3.70 s at c = 8. The number that sizes a drain window is `S_seat = 5.1 s` | [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) §6, [`README.md`](../../models/marlin2b/README.md) results table |
| C4 | §3.2 | AWS EKS blog: compile cache worth **−80 %** / **−93 %** end to end | those deltas are **Run:ai Model Streamer + compile cache**. Compile cache alone: **53 s → 4 s (−92 %)** and **34 s → 6 s (−82 %)**; the weights term (29 → 12 s, 423 → 26 s) is the streamer's | [AWS Containers blog](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/) |
| C5 | §7.2 | `CapacityDistributionStrategy: balanced-best-effort` on a group that targets an ODCR | **`reservations-then-balanced`** — §7.2 contradicted §2.4. AWS: *"By default, Auto Scaling prioritizes Availability Zone balance when consuming Capacity Reservations"*, and `reservations-then-balanced` is the documented way to invert that, recommended *"for … GPU, high performance computing (HPC), or machine learning workloads"* | [target-capacity-reservations](https://docs.aws.amazon.com/autoscaling/ec2/userguide/target-capacity-reservations.html), [AZ distribution](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-availability-zone-balanced.html) |
| C6 | §2.4 | `g6e.12xl` listed with the single-GPU sizes; `g6e.16xlarge` absent from the offering and price tables | `g6e.12xlarge` is **4× L40S / 48 vCPU / $10.49264 h**. **`g6e.16xlarge`** is 1× L40S, 64 vCPU, **$7.57719/h = $0.1184/vCPU-h — the best $/vCPU-h of any `g6e` size**, 3 replicas inside the 192-vCPU quota, and a fourth override taking the scale-out surface to 16 pools | [G6e product page](https://aws.amazon.com/ec2/instance-types/g6e/); [us-east-1 price sheet](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) re-fetched 2026-09-20 |

### Confirmed

AWS Auto Scaling and EC2 — every quoted string matched the live page verbatim:

1. **§1.3 backlog-per-instance formula and worked example** — the two definition
   paragraphs, and *10 instances / 1,500 messages / 0.1 s per message / 10 s
   acceptable latency → target 100, backlog 150, scales out by five* [[SQS
   scaling](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-using-sqs-queue.html)].
   The scale-in-protection pseudocode in §4.1 is verbatim from the same page.
2. **§2.3 / §3.3 / §7.1 EBS volume initialization** — 100–300 MiB/s range, per-GiB
   billing on **full snapshot data size**, 5,000 MiB/s cumulative regional cap,
   settable on launch-template block device mappings, and the FSR override. The
   §2.3 table recomputes exactly: 30 GiB → 307.2 s / 102.4 s, 70 GiB → 716.8 s /
   238.9 s. New supporting facts: the rate is also settable on **root volume
   replacement tasks** (relevant to §2.7/§7.3g), and AWS commits to ±10 % of the
   requested rate 99 % of the time.
3. **§2.1 / §7.3f ALB attributes** — `idle_timeout.timeout_seconds` default **60
   seconds**, `client_keep_alive.seconds` default **3600 seconds**.
4. **§1.5 predictive scaling** — ≥24 h of data, up to the past 14 days, hourly
   forecast for the next 48 hours, refreshed every 6 hours; the scale-out-only
   paragraph, the homogeneous-group warning, `SchedulingBufferTime` / **Pre-launch
   instances**, and the max-of-active-policies rule quoted in §1.6.
5. **§2.6 / §4.2 lifecycle hooks** — one hour default heartbeat; global timeout
   *"48 hours or 100 times the heartbeat timeout, whichever is smaller"*;
   best-effort termination hooks; launch-rate throttling on consistently failing
   hooks; the ELB-registration use case; *"a lifecycle hook does not prevent an
   instance from terminating in the event that capacity is no longer available"*;
   the simple-scaling pause. The §1.6 quote *"In general, we recommend against
   using simple scaling policies…"* is on the lifecycle-hooks page as cited.
6. **§2.5 hibernation** — the supported-family list is General purpose / Compute
   optimized / Memory optimized / Storage optimized. **No accelerated-computing
   family appears**, so `g6e` cannot hibernate for reasons of family, not RAM (the
   <150 GiB Linux limit would not have bound a 64 GiB `g6e.2xlarge`).
7. **§2.5 warm pools** — the definition, the `Stopped`-state cost sentence, the
   weighted-mixed-group exclusion, the Spot-in-mixed-group exclusion, the
   depleted-pool cold start, and the EBS-root requirement. A fifth fact the
   document does not yet cite strengthens reason 2: *"If an instance within the
   warm pool encounters an issue during the launch process… the instance will be
   considered a failed launch and terminated. This applies regardless of the
   underlying cause, such as an insufficient capacity error."*
8. **§2.1 / §2.5 instance store** — *"every block of the instance store volume is
   cryptographically erased"* on stop, hibernate or terminate.
9. **§2.8 Capacity Blocks** — the supported-type table is exactly the eleven types
   listed (`p6-b300.48xlarge`, `p6-b200.48xlarge`, `p5.4xlarge`, `p5.48xlarge`,
   `p5e.48xlarge`, `p5en.48xlarge`, `p4d.24xlarge`, `p4de.24xlarge`,
   `trn1.32xlarge`, `trn2.3xlarge`, `trn2.48xlarge`) — **no `g` family**. 8-week
   horizon, 64 per block / 256 across blocks, 11:30 UTC end, 11:00 UTC termination
   start, no cancellations, and the §1.5 scheduled-scaling note.
10. **§2.8 / §4.3 ODCRs** — the definition, no term commitment for immediate-use
    reservations, *"Active and unused Capacity Reservations count toward your
    On-Demand Instance limits"*, the `assessing/scheduled/pending/active/delayed`
    state list, the hibernation caveat, and future-dated reservations restricted to
    families **C, G, I, M, R, T, U, X** with a **32 vCPU minimum**.
11. **§2.8 / §7.2 `CapacityReservationPreference`** — all four values and their
    behaviour, including *"If no reserved capacity is available, instances launch
    as On-Demand"* for `capacity-reservations-first`.
12. **§2.7 / §7.3g Replace Root Volume** — the capacity-constraint rationale, the
    preserved-resources list, the mixed-instances-policy and per-override `ImageId`
    requirements, the `$Latest`/`$Default` prohibition, the warm-pool prohibition,
    and the four lifecycle states. Two requirements the document omits: *"AMIs must
    contain only a single root volume"* and *"All instances must match the group's
    launch template configuration"*.
13. **§2.6 / §4.2 default instance warmup** — *"It is not enabled or configured by
    default"*, the strong recommendation, the 300 s starting point, the
    reduce-it-when-you-have-a-lifecycle-hook guidance, and the scale-in-blocking
    paragraph.
14. **§1.6 / §7.3b step scaling** — proportional-metric rationale, step scaling as
    an additional aggressive policy, per-breach-size step adjustments, and
    *"If you use the AWS CLI or an SDK, you specify the upper and lower bounds
    relative to the breach threshold"* — so §7.3b's 60/120/300 s reading is right.
15. **§2.4 / §2.9 / §2.10 prices** — the live us-east-1 Linux on-demand sheet gives
    `g6e.2xlarge` **$2.24208**, `g6e.4xlarge` **$3.00424**, `g6e.8xlarge`
    **$4.52856**, `g6.2xlarge` **$0.97760**, `g5.2xlarge` **$1.21200**, all exact.
    Derived columns recompute: $/vCPU-h 0.2803 / 0.1878 / 0.1415 / 0.1222 / 0.1515;
    ratios 1.34× and 2.02×; Spot discounts 9 / 48 / 60 / 15 / 39 %; quota rows
    24 / 12 / 6 replicas and 37.7 / 18.8 / 9.4 clips/s.
16. **§3.2 / §5.2 / §5.3 / §5.5 engine and autoscaler facts** — vLLM's
    *"you can directly copy the whole `~/.cache/vllm/torch_compile_cache`
    directory…"*, the cache key, `VLLM_DISABLE_COMPILE_CACHE`, and the confirmed
    **absence** of `VLLM_CACHE_ROOT` from that page (open question 10 stands);
    multimodal processor caching on by default with `mm_processor_cache_gb`
    **default 4 GiB**; Karpenter `expireAfter` **720h**, the three consolidation
    policies, the `consolidateAfter` reset sentence, the `do-not-disrupt` forceful
    exclusions, and the SQS interruption queue; KEDA `pollingInterval` 30,
    `cooldownPeriod` 300, `initialCooldownPeriod` 0, `minReplicaCount` **0**,
    `maxReplicaCount` 100, and `fallback` fields mandatory when the section is
    present; the warm-pool blog's **243 s → 36 s** and *"as little as 30 seconds"*.
17. **§5.5 QLM** — arXiv 2407.00047, Patke et al., v1 2024-06-05, v2 **2025-02-25**,
    abstract states **"40-90%"** SLO attainment and **"20-400%"** throughput, with a
    *"Request Waiting Time (RWT) Estimator"* and a global scheduler.

Repo claims — all opened and checked, all confirmed:
`MAX_INFLIGHT = 16` (default in [`gateway.py`](../../apps/infrx-api/gateway.py)
line 39 *and* written by [`install.sh`](../../apps/infrx-api/deploy/install.sh), so
§2.1 and §6.3 are both right); the 429 path with `Retry-After: 2`;
`MAX_VIDEO_SECONDS = 120`; `serve.sh` at `--max-model-len 32768`,
`--gpu-memory-utilization 0.90`, `IMAGE=vllm/vllm-openai:nightly`, **no cache
volume mounted**; `marlin2b-vllm.service` with `TimeoutStartSec=900` and
`--max-num-seqs 32`; `WEIGHTS_ROOT=/opt/dlami/nvme` (instance store);
`Caddyfile` with `read_timeout 600s` and `flush_interval -1`;
`usage.jsonl` / `usage_failed.jsonl` under `/opt/dlami/nvme/logs`;
`X_replica` 1.57 / 3.58 clips/s, TTFT p50 3.35 s / 0.66 s / 0.77 s at c = 1,
prompt tokens 1,928–2,061, the ~18 s first-kwargs penalty, and the
distinct-clip caveat (`notes.md` §5) — every cross-reference to a `notes.md`
finding number is correct; `CLAUDE.md`'s *"`g6e` needs AZ retries (1d worked)"*
and *"Prices come only from `research/cross-cutting/cloud-pricing.md`"*;
[`cloud-pricing.md`](../cross-cutting/cloud-pricing.md) indeed has **no `g`-family
row**, so Implications #9 stands. All Erlang-C cells in §6.2 reproduce
(mean waits 0.00 / 0.32 / 0.02 / 0.12 / 0.00 s; `P(wait > 10 s)` 1.2e-7 / 6.2e-4 /
1.1e-9 / 4.5e-7 / 1.3e-18), as does the inverse table (5 / 7 / 13 / 23 / 43 seats;
ρ 0.51 / 0.73 / 0.78 / 0.89 / 0.95) and every row of §1.3, §2.3, §2.10 and §3.4.

### Unverifiable

- **U1 — the account-local measurements.** Spot quota `L-3819A6DF = 0`, on-demand
  quota `L-DB2E81BA = 192 vCPU`, Spot Placement Score 1/10 in four AZs, the spot
  price history, the AZ-name→AZ-ID map, the `g6e` offering set, `cr-04397f3102a3955b7`
  and `i-0e8449a4ffca29bab`. These are `meas. 2026-09-20` against account
  641134885443 and cannot be re-derived from a public source; they are also
  account- and day-specific, as open question 7 already says. **No corroborating
  evidence found and none possible from outside the account** — they are accepted
  as stated and remain the document's largest single-point-of-failure inputs.
- **U2 — the 720-hour month.** §4.3's `$1,614/month` and §7.3e's `$3,228/month`
  are arithmetically correct at 720 h/month but AWS bills on a **730-hour**
  convention, which gives **$1,637** and **$3,273**. Both are now printed; nothing
  downstream was recut, because the document is internally consistent at 720 h.

### Two ⚠️ markers added, not corrections

- **§2.4** — "the default is balanced best effort" is not stated on the cited AZ
  distribution page. Marked as our inference.
- **§1.1** — the cited vLLM metrics page carries the two quoted descriptions
  verbatim, but lists `vllm:num_requests_waiting` only in its Grafana prose and
  does **not** document `vllm:gpu_cache_usage_perc` as the v0 spelling.
