# AWS architecture options for a GPU model API

Research date: **2026-09-20**. Prices and quotas are dated inline; the EC2
on-demand rates come from the AWS pricing feed published **2026-09-18**
(§13.1). Every non-trivial claim carries `[src]` or **⚠️ TO BE VERIFIED**.

This document answers one question: *what should the infrastructure under
`https://marlin2b.callbill.ai` look like when real, paying users hit it?* It
compares five AWS shapes (A–E), picks one for the next 3 months and one for
12, and names the files in this repo that change.

It does **not** re-derive engine-level admission control, cold-start anatomy,
autoscaling control theory or SLO practice. Those are already written:

| topic | read |
|---|---|
| queues inside vLLM, `max_num_seqs`, backpressure, 429 vs 503, retries | [`../scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md) §1–§3 |
| which signal to scale on, lead time vs cold start, scale-to-zero economics | [`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md) §2, §4.4–§4.5, §5.4 |
| boot → weights → compile → warm-up, snapshotting, pre-warming | [`../scaling/06-cold-start.md`](../scaling/06-cold-start.md) §1–§5 |
| SLOs, failure modes, drain, runbooks | [`../scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md) |
| the bare-metal target architecture this must eventually converge on | [`../scaling/10-blueprint.md`](../scaling/10-blueprint.md) §1, §5 |
| prefix/KV caching, multimodal serving, batching | [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) §1, §4, §6 |
| $/GPU-hour across clouds — **the price source of truth** | [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §5, §12 |

---

## 1. What we are actually building on top of

### 1.1 Today's system, in one diagram

```
client ──HTTPS──▶ Caddy :443 (docker, Let's Encrypt)
                     │        one box: AWS g6e.2xlarge, us-east-1d, EIP 100.57.145.167
                     ▼
                  gateway.py :8001 (uvicorn, marlin2b-gateway.service)
                     │  auth      : sha256(key) → Supabase api_keys, 60 s cache
                     │  admission : 429 above MAX_INFLIGHT=16, no queue
                     │  budget    : download video → ffprobe → mm_processor_kwargs
                     │  usage     : usage.jsonl + background queue → Supabase usage_events
                     ▼
                  vLLM :8000 (docker, vllm/vllm-openai:nightly, --max-model-len 32768)
```

Sources in-repo: [`apps/infrx-api/README.md`](../../apps/infrx-api/README.md),
[`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py),
[`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh),
[`apps/README.md`](../../apps/README.md) §5–§7.

### 1.2 The measured facts that constrain every option

From [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md)
(measured 2026-09-19, one L40S / 8 vCPU, vLLM nightly):

| # | fact | consequence for architecture |
|---|---|---|
| M1 | c=1: **0.50 clips/s**, TTFT p50 **0.77 s** | a single request is *fast*; the product is interactive, not batch |
| M2 | c=8, 1080p 5.5 MB source: **1.57 clips/s**, TTFT p50 **3.35 s** | one replica's honest capacity is ~1.6 clips/s |
| M3 | c=8, 360p 1 MB source: **3.58 clips/s**, TTFT p50 **0.66 s** | the same GPU does **2.3× more** when decode is cheap |
| M4 | 2K vs 12K prompt tokens changes throughput by 6 % (1.57 → 1.47) | **the LM is not the bottleneck**; video I/O is |
| M5 | first request with a new kwargs set costs ~18 s | warm-up must send a real request, not just `/health` |
| M6 | vLLM boot ≈ 2–3 min incl. `torch.compile` ⚠️ **TO BE VERIFIED** — no such figure exists in `models/marlin2b/README.md` or `results/notes.md`; the only in-repo statement is `apps/infrx-api/openrouter/PLAN.md` "keep `torch.compile` cache on NVMe so restarts take seconds". Treat 2–3 min as an unmeasured estimate (fact-check 2026-09-20) | scale-out lead time is dominated by this, not by EC2 |
| M7 | end-to-end 3.8 s for a public URL clip, TTFT 3.2 s, "dominated by downloading and decoding the 5.5 MB source **twice**, gateway and vLLM" ([`models/marlin2b/README.md`](../../models/marlin2b/README.md)) | the gateway currently doubles the most expensive part of the request |

**The architectural headline: this is a CPU-I/O-bound multimodal service
wearing an LLM costume.** Every option below is judged first on whether it
lets us fix M3/M4/M7, and only second on how prettily it autoscales.

### 1.3 The requirements, restated as testable properties

| id | requirement | test |
|---|---|---|
| R1 | **no dropped requests** | at 5× the steady rate for 10 min, zero non-2xx other than deliberate 4xx |
| R2 | **queue, don't fail, at the concurrency limit** | above the in-flight limit the request waits; the client sees a `Retry-After`-free 200 and a wait header |
| R3 | **honest wait estimates** | `X-Queue-Position` / `X-Estimated-Wait-Seconds` within ±30 % of realised wait at p90 |
| R4 | **bounded queue** | above the bound: 429 with `Retry-After` computed from real drain rate, never an unbounded wait |
| R5 | **automatic scale-up/down** | fleet tracks load with a stated lead time; scale-in never kills an in-flight request |
| R6 | **caching** | a repeated (video, prompt, params) triple is served without re-decoding the video |
| R7 | **throughput/latency optimisation** | ≥2× clips/s per dollar vs the 2026-09-19 baseline |
| R8 | **portable to bare metal** | the control plane is not an AWS-only API we cannot re-implement on the cluster |

R2+R3 are the interesting ones, and they are the reason this document keeps
coming back to *where the queue lives*. See §10.

---

## 2. The five options at a glance

| | A. EC2 ASG + ALB | B. EKS + Karpenter + KEDA | C. SageMaker AI | D. ECS on EC2 GPU | E. Serverless GPU (overflow) |
|---|---|---|---|---|---|
| request path | client → ALB → gateway → vLLM | client → ALB/Gateway API → (llm-d EPP) → vLLM pod | client → SigV4/bearer → SM endpoint → container | client → ALB → task → vLLM | client → our gateway → provider HTTPS |
| where the queue lives | **our gateway** (or SQS) | our gateway, or llm-d router | **built in** (async only) | our gateway | provider's |
| streaming (SSE) | yes, ALB idle ≤ 4000 s [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) | yes | real-time yes (`/openai/v1/chat/completions`) [src](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-openai-compatible.html); **async no** | yes | yes |
| scale to zero | yes (min 0 + real-0 metric) [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html) | yes (KEDA `idleReplicaCount`) [src](https://keda.sh/docs/2.17/reference/scaledobject-spec/) | **async: yes, built in** [src](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference-autoscale.html) | yes | inherent |
| scale-out lag | alarm 2 min + launch + **vLLM 2–3 min** | Karpenter node + pod pull + **vLLM 2–3 min** | SM provisioning + container | ≈ A | seconds–1 min |
| capacity assurance for g6e | ODCR (open/targeted) | ODCR via `capacityReservationSelectorTerms` [src](https://karpenter.sh/docs/concepts/nodeclasses/) | ⚠️ SM manages capacity; per-instance-type quota starts at **0** | ODCR | provider's |
| ops burden | low-ish (we already run it) | **high** | medium (opaque) | medium | very low |
| portability to bare metal | high (systemd + Caddy ≈ anywhere) | **highest** (same manifests on the cluster) | **none** | medium | none |
| marginal cost at 1 replica | $2.24/h + $0.0225/h ALB base | + **$0.10/h** cluster [src](https://aws.amazon.com/eks/pricing/) | ⚠️ ml.g6e premium unsourced | $2.24/h + ALB | pay-per-second |
| verdict | **3-month target** | **12-month target** | async-only lane, later | no reason to prefer over A or B | overflow valve only |

---

## 3. Option A — EC2 Auto Scaling group behind an ALB, with a queue in front

This is the straight-line evolution of today's box: same AMI, same systemd
units, same `gateway.py`, plus an ALB, an ASG, and a queue.

### 3.1 Request path

```
client ──TLS──▶ ALB :443 (ACM cert, target group :8001, least_outstanding_requests)
                   │
                   ├─▶ EC2 #1  gateway.py :8001 ──▶ vLLM :8000   ┐
                   ├─▶ EC2 #2  gateway.py :8001 ──▶ vLLM :8000   ├ ASG, min 1 max N
                   └─▶ EC2 #n  …                                  ┘
```

Caddy disappears — ACM on the ALB terminates TLS, and the gateway binds
`0.0.0.0:8001` inside the security group. That deletes
[`apps/infrx-api/deploy/Caddyfile`](../../apps/infrx-api/deploy/Caddyfile)
and the Let's Encrypt renewal failure mode with it.

### 3.2 The load balancer is not a queue — and the ALB knows it

An ALB does not hold requests waiting for capacity; it picks a target and
forwards. Its only relevant controls:

- **Routing algorithm.** Default is round robin; `least_outstanding_requests`
  ("routes requests to the targets with the lowest number of in progress
  requests") and `weighted_random` are the alternatives
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html).
  For LLM serving, where request durations vary 10× (a 10 s clip vs a 120 s
  clip), **least outstanding requests is the correct default** — round robin
  hands a long request and a short request to the same target with equal
  probability.

  ```bash
  aws elbv2 modify-target-group-attributes --target-group-arn "$TG" \
    --attributes Key=load_balancing.algorithm.type,Value=least_outstanding_requests
  ```

- **Slow start** (`slow_start.duration_seconds`) linearly ramps a newly
  healthy target's share, which is exactly what a vLLM replica that just
  finished `torch.compile` wants — **but** "the least outstanding requests
  routing algorithm can not be used with slow start mode"
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html).
  Decision rule: **prefer LOR and make the health check honest** (see §3.5)
  rather than taking round-robin-plus-slow-start.

- **Deregistration delay** defaults to 300 s
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html).
  That is the drain window for scale-in; it must exceed the longest expected
  request (a 120 s clip at 2048 output tokens). 300 s is fine; do not lower
  it below 180 s.

### 3.3 Idle timeout: the number that decides whether streaming works

| load balancer | idle timeout | adjustable? | source |
|---|---|---|---|
| ALB | default **60 s**, valid range **1–4000 s** | yes, `idle_timeout.timeout_seconds` | [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) |
| ALB client keepalive | default **3600 s** (`client_keep_alive.seconds`) | yes | [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html) |
| NLB, TCP listener | default **350 s**, range **60–6000 s** | yes | [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/network-load-balancers.html) |
| NLB, **TLS listener** | **350 s, cannot be modified** | **no** | [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/network-load-balancers.html) |
| NLB, UDP | 120 s, cannot be changed | no | same |
| API Gateway HTTP API | **max integration timeout 30 s**, payload **10 MB**, neither increasable | **no** | [src](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-quotas.html) |

Three conclusions, all load-bearing:

1. **The ALB idle timeout is an *idle* timeout, not a request timeout.** It is
   "the period of time an existing client or target connection can remain
   inactive, with no data being sent or received"
   [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html).
   A streaming SSE response emits a token every ~8 ms at c=8 (M2), so it never
   goes idle. **SSE works through an ALB with the default 60 s.** The timeout
   only matters for the *non-streaming* path, where the connection is silent
   from request end to response start. Set it to **900 s** — long enough for a
   120 s clip queued behind a full fleet, short enough that a wedged target
   does not hold a connection for an hour.

   ```bash
   aws elbv2 modify-load-balancer-attributes --load-balancer-arn "$ALB" \
     --attributes Key=idle_timeout.timeout_seconds,Value=900
   ```

   AWS's own advice matches: "to ensure that lengthy operations such as file
   uploads have time to complete, send at least 1 byte of data before each
   idle timeout period elapses". For a queued non-streaming request our
   gateway should emit an HTTP heartbeat — which is precisely what §10.4
   proposes anyway, for a different reason.

2. **Do not put API Gateway in front of this.** 30 s integration timeout and
   10 MB payload kill both a 120 s clip's inference and a 64 MB
   `MAX_VIDEO_MB` upload. API Gateway is off the table for the inference path;
   it remains fine for a future control-plane API.

3. **An NLB with a TLS listener caps every request at 350 s, permanently.**
   If we ever want NLB (for a static IP, or to skip HTTP parsing), we must
   terminate TLS on the instance (TCP listener, adjustable to 6000 s) — which
   means keeping Caddy. Given that the ALB gives us 4000 s and ACM, **ALB
   wins** for this workload. NLB's only real pull is the Elastic IP: the ALB
   allocates AWS-managed public IPv4 addresses per AZ and they can change
   [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html),
   so customers who firewall by IP need either an NLB-in-front-of-ALB or
   Global Accelerator. ⚠️ **TO BE VERIFIED** whether any current caller
   depends on `100.57.145.167` being stable; if yes, that is the one argument
   for keeping the EIP and putting the ALB behind it.

### 3.4 Autoscaling mechanism and its lag

EC2 Auto Scaling offers target tracking, step, simple, scheduled and
predictive. For this workload:

**Signal.** Not CPU (the GPU is the resource; see
[`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md)
§1.3 for why utilisation-based HPA is the wrong control law). Use the AWS
**backlog-per-instance** pattern, which AWS documents for SQS but which is
just Little's Law:

> "**Backlog per instance**: … the length of the SQS queue … Divide that number
> by the fleet's running capacity … **Acceptable backlog per instance**: …
> take the acceptable latency value and divide it by the average time that an
> EC2 instance takes to process a message."
> [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-using-sqs-queue.html)

Our numbers: mean service time per clip on one replica at c=8 is
1 / 1.57 = **0.637 s** (M2). If the queue-wait SLO is **30 s**, then

```
acceptable_backlog_per_instance = 30 s / 0.637 s ≈ 47 clips
```

Round **down** to 40 (rounding down spends the safety margin on the operator's
side, not the customer's). That is the target value of the target-tracking
policy.

**Publishing it without a custom metric.** AWS's metric-math form avoids a
`PutMetricData` job entirely — `e1 = m1/m2` where `m2` is
`AWS/AutoScaling GroupInServiceInstances`
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-target-tracking-metric-math.html).
If the queue is SQS, `m1` is `ApproximateNumberOfMessagesVisible` and no code
is needed. If the queue is in our gateway (§10), the gateway must publish one
gauge, `Marlin/QueueDepth`, and the same expression applies:

```json
{
  "CustomizedMetricSpecification": {
    "Metrics": [
      { "Id": "m1", "ReturnData": false, "Label": "fleet queue depth",
        "MetricStat": { "Stat": "Sum",
          "Metric": { "Namespace": "Marlin", "MetricName": "QueueDepth",
                      "Dimensions": [{"Name":"Model","Value":"nemostation/marlin-2b"}] } } },
      { "Id": "m2", "ReturnData": false, "Label": "in-service instances",
        "MetricStat": { "Stat": "Average",
          "Metric": { "Namespace": "AWS/AutoScaling", "MetricName": "GroupInServiceInstances",
                      "Dimensions": [{"Name":"AutoScalingGroupName","Value":"marlin2b-asg"}] } } },
      { "Id": "e1", "Expression": "m1 / m2", "Label": "backlog per instance", "ReturnData": true }
    ]
  },
  "TargetValue": 40.0
}
```

```bash
aws autoscaling put-scaling-policy --auto-scaling-group-name marlin2b-asg \
  --policy-name marlin2b-backlog --policy-type TargetTrackingScaling \
  --target-tracking-configuration file://config.json
```

**Lag budget.** Add the pieces:

| stage | time | source |
|---|---|---|
| gateway publishes the gauge (1-min resolution) | 0–60 s | CloudWatch standard resolution |
| CloudWatch alarm: `Period` 60 s × `EvaluationPeriods` 2 | 120 s | AWS's own async example uses exactly this shape [src](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference-autoscale.html) |
| `RunInstances` → instance running (g6e.2xlarge) | ⚠️ **TO BE VERIFIED** — measure; budget 60–90 s |
| DLAMI boot, docker pull/start, weight load, `torch.compile` | **120–180 s** | M6, [`models/marlin2b/README.md`](../../models/marlin2b/README.md) |
| first-request warm-up at the real kwargs | **~18 s** | M5 |
| ALB health check: 2 × 30 s default interval | 0–60 s (tunable to 2 × 5 s) | ALB target group defaults |
| **total** | **≈ 5.3–8.8 min** (recomputed 2026-09-20: min 0+120+60+120+18+0 = 318 s; max 60+120+90+180+18+60 = 528 s) | |

**That 5.3–8.8 min figure is the single most important number in this
document.** It is why "queue when the concurrency limit is hit" is not a nice
extra — it is the *only* way to honour R1 without permanently over-provisioning.

**Instance warmup.** Set `DefaultInstanceWarmup` on the group to cover the
boot: until it expires "an instance is not counted toward the aggregated EC2
instance metrics"
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html).
Set it to **240 s**. Without it, `GroupInServiceInstances` counts a
still-compiling box, the computed backlog-per-instance drops, and the policy
stops scaling while the queue is still growing.

**Cooldown.** Default cooldown is 300 s
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-scaling-cooldowns.html),
and that page opens by recommending you *not* use simple scaling policies and
cooldowns at all. **Corrected 2026-09-20:** an EC2 Auto Scaling
target-tracking policy has **no scale-in-specific cooldown knob** —
`TargetTrackingConfiguration` accepts only `TargetValue`,
`CustomizedMetricSpecification` / `PredefinedMetricSpecification` and
`DisableScaleIn`
[src](https://docs.aws.amazon.com/autoscaling/ec2/APIReference/API_TargetTrackingConfiguration.html)
(the `ScaleInCooldown` / `ScaleOutCooldown` pair belongs to *Application* Auto
Scaling, which is what SageMaker in §5.1 uses, not to an EC2 ASG). Target
tracking instead uses `DefaultInstanceWarmup`, falling back to the default
cooldown when warmup is null
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html).
So the intent — a GPU that took 8 min to arrive should not be terminated 3 min
later — must be bought differently: either set the **group's**
`DefaultCooldown` to 900 s (it is what target tracking falls back to) and
leave `DefaultInstanceWarmup` at 240 s for the scale-out side, or set
`DisableScaleIn: true` on the backlog policy and do scale-in from a separate,
slow step policy. Decide which before writing `asg.yaml` (§3.8).

**Scale to zero** is legal: "when a metric emits real 0 values to CloudWatch
… an Auto Scaling group can scale in to 0 … the group's minimum capacity must
be set to 0"
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html).
For a paid API with human users, **don't** — min 1. A cold customer request
would wait 5–8 min. Reserve scale-to-zero for the second model
(Qwen3.8-27B) before it has traffic.

**Predictive scaling** "analyzing historical load data to detect daily or
weekly patterns" and is recommended for "applications that take a long time
to initialize, causing a noticeable latency impact on scale-out events"
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-predictive-scaling.html)
— a literal description of us. But it needs history we do not have.
**Decision rule: revisit at 14 days of `usage_events`.** See
[`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md)
§4.1 for the seasonality test to run first, and §4.3 for the bad default it
ships with.

### 3.5 Health checks must be honest

The ALB target group health check currently would hit `gateway.py /health`,
which proxies vLLM's `/health`. That returns 200 as soon as the engine is up —
**before** the 18 s first-kwargs penalty (M5) is paid. Change
[`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) so `/health`
returns 503 until a synthetic warm-up request at the production
`mm_processor_kwargs` has completed once. Concretely: on startup, fire one
4-frame generate against a bundled 2 s clip, set a module-level `READY` flag,
and gate `/health` on it. ~8 lines. Without this, the ALB routes real traffic
into an 18 s stall on every scale-out.

### 3.6 Warm pools — and why they mostly don't help us

A warm pool is "a pool of pre-initialized EC2 instances that sits alongside an
Auto Scaling group"
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html),
in `Stopped`, `Running` or `Hibernated` state. For a 2–3 min vLLM boot this
looks perfect. Two documented facts largely kill it:

1. **G-family instances cannot hibernate.** The supported-families list
   covers general purpose, compute optimized, memory optimized and storage
   optimized only — **no G or P families**
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/hibernating-prerequisites.html).
   The same page caps Linux hibernation at "less than 150 GiB" of RAM, which
   would exclude g6e.8xlarge and up regardless. So the `Hibernated` state —
   the one that would have preserved a compiled, weight-loaded vLLM in RAM —
   is unavailable.

2. **`Stopped` loses the instance store.** Our weights, samples and logs all
   live on `/opt/dlami/nvme`
   ([`models/marlin2b/README.md`](../../models/marlin2b/README.md)), which is
   the ephemeral NVMe. Warm pools further require an EBS root:
   "Amazon EC2 Auto Scaling can put an instance in a `Stopped` or `Hibernated`
   state only if it has an Amazon EBS volume as its root device"
   [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html).
   A stopped-then-started warm instance therefore has to re-stage the 5.4 GB
   checkpoint and re-run `torch.compile` — i.e. it saves the EC2 launch
   (~60–90 s) and nothing else.

Two further limitations worth knowing: warm pools "aren't supported with
weighted mixed instance groups" and "don't support Spot Instances within mixed
instance groups"; and "if your warm pool is depleted when there is a scale-out
event, instances will launch directly into the Auto Scaling group (a cold
start). You could also experience cold starts if an Availability Zone is out
of capacity" — which, for g6e in us-east-1, is our normal condition.

**Decision rule.** Skip warm pools. Buy the same insurance with **N+1 warm
headroom**: keep `desired = ceil(load) + 1` via the target-tracking target
value, and pay for one idle L40S ($2.24/h = $1,635/month) instead of
engineering around a state g6e cannot enter. Revisit only if the checkpoint
moves to the EBS root and we confirm the compile cache survives a stop
(see [`../scaling/06-cold-start.md`](../scaling/06-cold-start.md) §3 for the
compile-cache mechanics).

### 3.7 Capacity flexibility and the g6e scarcity problem

We observed that only `us-east-1d` had g6e capacity; other AZs returned
`InsufficientInstanceCapacity`. Three mitigations, in order of effectiveness:

1. **On-Demand Capacity Reservations (ODCR).** "Capacity Reservations allow
   you to reserve compute capacity for your Amazon EC2 instances in a specific
   Availability Zone for any duration … there is no term commitment"
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html).
   ASG-launched instances are explicitly eligible ("managed instances launched
   on your behalf by … Amazon EC2 Auto Scaling"). Caveats from the same page:
   *no billing discount* (an ODCR for 2 g6e.2xlarge costs the same $4.48/h
   whether or not you run in it), *active and unused reservations count toward
   On-Demand limits*, and — important for §3.6 — "**Capacity Reservations do
   not ensure that a hibernated instance can resume**".

   **Recommendation: hold an ODCR for `desired_min + 1` g6e.2xlarge in
   us-east-1d today.** At 2 instances that is $4.48/h = $3,270/month, of which
   we already pay $1,635 for the running box. The $1,635 delta buys the
   guarantee that the *second* replica exists when the first saturates. This
   is the cheapest insurance in the document.

   Future-dated CRs are also available for the G family, with a floor:
   "You can request future-dated Capacity Reservations for an instance count
   with a minimum of 32 vCPUs" and "for instance types in the following
   families: C, G, I, M, R, T, U, and X". 32 vCPU = 4 × g6e.2xlarge or
   1 × g6e.8xlarge.

2. **Capacity Blocks for ML — not available to us.** The supported instance
   type table lists only `p6-b300`, `p6-b200`, `p5`, `p5e`, `p5en`, `p4d`,
   `p4de`, `trn1`, `trn2` (plus UltraServer variants)
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html).
   **No G family.** Capacity Blocks become relevant only when
   DeepSeek-V4.1-Flash lands on p5/p5e. Note then: reservations start up to
   8 weeks out, up to 64 instances per block, 256 across blocks, "Capacity
   Block cancellations aren't allowed", and blocks end at 11:30 UTC with
   termination beginning at 11:00 UTC — so a Capacity Block is a *batch*
   instrument, not a way to run a 24/7 endpoint.

3. **Attribute-based instance selection (ABS) + multi-AZ.** An ASG mixed
   instances policy can express "any instance with 1 accelerator, ≥ 40 GiB
   accelerator memory, ≥ 16 vCPU" and let EC2 pick across g6e sizes and AZs
   [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/create-mixed-instances-group-attribute-based-instance-type-selection.html).
   This is how you convert "us-east-1d only" into "whichever of 1a/1b/1c/1d/1f
   has an L40S right now". **But:** heterogeneous instance sizes mean
   heterogeneous throughput per replica, and the backlog-per-instance policy
   assumes homogeneity. Use ABS with a *narrow* allow-list
   (`g6e.4xlarge`, `g6e.8xlarge` only, same GPU count) rather than open
   attributes, and accept the ~20 % capacity-estimate error. And remember
   warm pools are incompatible with weighted mixed groups — a non-issue once
   we skip warm pools.

**Spot.** From the AWS Spot Instance Advisor feed, us-east-1 Linux, fetched
2026-09-20 [src](https://aws.amazon.com/ec2/spot/instance-advisor/) (data at
`https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json`):

| type | savings vs on-demand | frequency of interruption |
|---|---|---|
| `g6e.2xlarge` | 54 % (≈ $1.03/h) | 5–10 % |
| `g6e.4xlarge` | 62 % (≈ $1.14/h) | **> 20 %** |
| `g6e.8xlarge` | 62 % (≈ $1.72/h) | 15–20 % |
| `g6.2xlarge` | 46 % (≈ $0.53/h) | 10–15 % |
| `g5.2xlarge` | 62 % (≈ $0.46/h) | 15–20 % |
| `p5.48xlarge` | 57 % (≈ $23.67/h) | < 5 % |

A >20 % interruption rate on `g6e.4xlarge` — our preferred size (§13) — means
spot is **not** a base-capacity instrument here. Use it for a *third tier*
only: on-demand N+1 for the SLO, spot beyond that for burst, with the gateway
draining spot targets on the 2-minute interruption notice. That is real work;
defer past the 3-month horizon.

### 3.8 What changes in `gateway.py` and the deploy files

| file | change |
|---|---|
| `apps/infrx-api/gateway.py` | bounded queue + wait estimates (§10); `/health` gated on warm-up (§3.5); publish `QueueDepth` + `Inflight` to CloudWatch; drain on SIGTERM |
| `apps/infrx-api/deploy/Caddyfile` | **delete** — ACM on the ALB |
| `apps/infrx-api/deploy/install.sh` | bind `0.0.0.0:8001`; register/deregister with the target group is implicit via the ASG |
| `apps/infrx-api/deploy/marlin2b-gateway.service` | `TimeoutStopSec` > deregistration delay so drain finishes |
| `models/marlin2b/serve.sh` | NVDEC backend + `--mm-processor-cache-gb` (§12) |
| new `apps/infrx-api/deploy/asg.yaml` | launch template, ASG, target group, ALB, scaling policy |

---

## 4. Option B — EKS with Karpenter + KEDA (and llm-d / NVIDIA Dynamo)

### 4.1 Request path

```
client ──▶ ALB (AWS LB Controller)
              └─▶ Gateway API / llm-d Router (Proxy + Endpoint Picker via ext-proc)
                     └─▶ InferencePool ──▶ vLLM pods (Karpenter-provisioned g6e nodes)
```

### 4.2 What it buys that A does not

- **Karpenter** replaces the ASG. NodePools express `disruption.consolidationPolicy`
  (`WhenEmpty` / `WhenEmptyOrUnderutilized` / `Balanced`), `consolidateAfter`,
  `expireAfter` and `terminationGracePeriod` — and disruption `budgets` that
  can be scheduled ("On Weekdays during business hours, don't do any
  deprovisioning")
  [src](https://karpenter.sh/docs/concepts/nodepools/). For GPU nodes,
  `consolidationPolicy: WhenEmpty` plus a 48 h `terminationGracePeriod` is the
  safe starting point — `WhenEmptyOrUnderutilized` will happily evict a vLLM
  pod mid-stream to save money.

- **`EC2NodeClass.spec.capacityReservationSelectorTerms`** (beta) lets
  Karpenter "select on-demand capacity reservations (ODCRs) … Karpenter will
  prioritize utilizing the capacity in these reservations before falling back
  to on-demand and spot"
  [src](https://karpenter.sh/docs/concepts/nodeclasses/). This is the cleanest
  answer to §3.7 anywhere in AWS: the ODCR is discovered by tag, and nodes
  land in it automatically.

  ```yaml
  apiVersion: karpenter.k8s.aws/v1
  kind: EC2NodeClass
  metadata: { name: l40s }
  spec:
    capacityReservationSelectorTerms:
      - tags: { "model-inference/pool": "marlin2b" }
    instanceStorePolicy: RAID0          # stripe the g6e NVMe for the weight cache
  ```

  `instanceStorePolicy: RAID0` is the other useful knob — it RAID0s the
  instance-store NVMe and points the kubelet at it, which is where the 5.4 GB
  checkpoint should live (see
  [`../scaling/06-cold-start.md`](../scaling/06-cold-start.md) §2 for weight
  staging, and §5 for the DaemonSet pre-stage pattern).

- **KEDA** scales pods on a Prometheus query rather than CPU. Defaults that
  matter: `pollingInterval: 30` s, `cooldownPeriod: 300` s,
  `initialCooldownPeriod: 0`, `minReplicaCount: 0`, `idleReplicaCount`
  (scale to a distinct idle floor)
  [src](https://keda.sh/docs/2.17/reference/scaledobject-spec/). The 30 s poll
  is 4× tighter than a CloudWatch 2×60 s alarm — one of B's real wins.

  ```yaml
  apiVersion: keda.sh/v1alpha1
  kind: ScaledObject
  metadata: { name: marlin2b }
  spec:
    scaleTargetRef: { name: marlin2b }
    pollingInterval: 15
    cooldownPeriod: 900          # GPUs are expensive to re-acquire; scale in slowly
    minReplicaCount: 1
    maxReplicaCount: 8
    triggers:
      - type: prometheus
        metadata:
          serverAddress: http://prometheus.monitoring:9090
          # backlog per replica, the same control law as §3.4
          query: |
            sum(vllm:num_requests_waiting{model="marlin2b"})
              / count(vllm:num_requests_running{model="marlin2b"})
          threshold: "40"
  ```

  `vllm:num_requests_waiting` and `vllm:num_requests_running` are real, current
  metric names
  [src](https://docs.vllm.ai/en/latest/usage/metrics.html); the page also
  exposes `vllm:num_requests_waiting_by_reason` with a `capacity` label, which
  is strictly better for this purpose because it excludes requests deferred
  for LoRA/KV-transfer reasons. Also available and directly useful here:
  `vllm:kv_cache_usage_perc`, `vllm:request_queue_time_seconds` (histogram —
  the actual SLI for R3), `vllm:prefix_cache_hits` / `_queries`, and
  **`vllm:mm_cache_hits` / `vllm:mm_cache_queries`** — the multimodal cache
  hit rate, which is the metric that tells us whether §12's caching is working.

- **llm-d** adds what neither A nor plain EKS has: an **Endpoint Picker**
  that "scores and selects model server pods based on real-time metrics,
  KV-cache affinity, and configured policies", with "Prefix-Cache Aware
  Routing", "KV-Cache Indexing", and a Latency Predictor that "trains an
  XGBoost model online to predict request latency"
  [src](https://llm-d.ai/docs/architecture). Its proxy is conformant with the
  Gateway API Inference Extension
  [src](https://gateway-api-inference-extension.sigs.k8s.io/).

  **How much of this do we need?** Prefix-cache-aware routing optimises reuse
  of a shared *text* prefix. Our prompt prefix is the two canonical Marlin
  prompts — a few dozen tokens — against ~2,061 video tokens per clip (M2).
  Prefix-aware routing is therefore nearly worthless for us **as text**. What
  *is* worth routing on is the **multimodal cache**: the same clip re-submitted
  should land on the replica that already decoded it (§12.3). llm-d does not
  do that out of the box. ⚠️ **TO BE VERIFIED** whether the EPP's scorer
  plugin interface can be fed a `uuid` from the request body; if it can, this
  is a ~100-line plugin and the single best argument for B.

- **NVIDIA Dynamo** covers similar ground — "Disaggregated serving, KV-aware
  routing, cache management, and autoscaling", with a **Planner** component
  and engine interop across vLLM/SGLang/TensorRT-LLM
  [src](https://docs.nvidia.com/dynamo/latest/). See
  [`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md)
  §3.5, which calls the Planner "the only SLA-native autoscaler in the stack".
  Both llm-d and Dynamo are designed for large disaggregated MoE deployments.
  For **one 2B model on one GPU per replica**, both are strictly overhead.

### 4.3 Cost and burden

EKS is **$0.10 per cluster per hour** on standard Kubernetes version support,
rising to **$0.60/h** once a version enters extended support (14 months of
standard, then 12 of extended) [src](https://aws.amazon.com/eks/pricing/).
$73/month is noise against a $1,635/month GPU. The cost is not money; it is
that a two-person team now operates a Kubernetes control plane, Karpenter,
KEDA, Prometheus, a Gateway API implementation, and possibly llm-d — five more
things that can page you at 3am — to serve one 5.4 GB model.

**Decision rule.** Move to B when **any** of these becomes true:
(a) we run ≥ 3 models with different GPU shapes and need bin-packing; (b) we
need cache-aware routing badly enough to write an EPP plugin; (c) we need
multi-GPU replicas (DeepSeek-V4.1-Flash) where gang scheduling matters; or
(d) the bare-metal cluster arrives and we want one set of manifests for both.
(c) and (d) are both on the 12-month horizon, which is why B is the 12-month
answer and not the 3-month one.

---

## 5. Option C — SageMaker AI endpoints

### 5.1 Asynchronous inference: the built-in queue

"SageMaker Asynchronous Inference is a capability … that **queues incoming
requests** and processes them asynchronously … ideal for requests with large
payload sizes (**up to 1GB**), long processing times (**up to one hour**) …
enables you to save on costs by **autoscaling the instance count to zero**"
[src](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html).

The mechanics are exactly what R2 asks for, already built:

- You `PutObject` the payload to S3, call `InvokeEndpointAsync` with a pointer,
  and get back an identifier and an output S3 location. Results land in S3;
  optional SNS success/error notifications.
- Autoscaling is Application Auto Scaling target tracking on
  **`ApproximateBacklogSizePerInstance`** (AWS's recommended metric, example
  `TargetValue: 5.0`) with `MinCapacity=0`
  [src](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference-autoscale.html).
- Scale-from-zero needs a second, step-scaling policy on the
  **`HasBacklogWithoutCapacity`** metric, otherwise "your endpoint won't scale
  up again until the number of requests in the queue exceeds the target …
  This can result in long waiting times for requests in the queue" (same
  source; its example alarm uses `Period=60`, `EvaluationPeriods=2`).

**And the disqualifier:** "The presence of an asynchronous inference
configuration (`AsyncInferenceConfig`) object in the endpoint configuration
implies that **the endpoint can only receive asynchronous invocations**."
There is no streaming, no TTFT, no OpenAI-compatible `stream: true`. Our
measured TTFT at c=1 is 0.77 s (M1). Turning a sub-second interactive API into
an S3-round-trip job queue to gain a managed queue is a bad trade for the
**interactive** product.

It is a *good* trade for a **batch lane**: "caption these 4,000 clips
overnight". Async inference gives that lane a 1 GB payload ceiling, a 1 hour
per-request ceiling, a real queue, scale-to-zero, and SNS completion — all for
free. **Recommendation: keep C in the back pocket as `POST /v1/batch`, not as
the main path.**

### 5.2 Real-time endpoints

Real-time endpoints now expose "an `/openai/v1/chat/completions` path that
accepts Chat Completions requests and returns responses directly from the
container, **including streaming**"
[src](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-openai-compatible.html).
So the streaming objection is gone. What replaces it:

- **Auth is AWS's, not ours.** The OpenAI-compatible path uses SageMaker
  bearer tokens generated from AWS credentials ("short-lived tokens (valid up
  to 12 hours)", requiring `sagemaker:CallWithBearerToken` and
  `sagemaker:InvokeEndpoint`). Our customers have `sk-infrx-…` keys in
  Supabase ([`apps/README.md`](../../apps/README.md) F4, F9), not IAM roles.
  So `gateway.py` stays in front regardless — SageMaker replaces the *GPU
  box*, not the gateway. That removes most of the "managed" benefit while
  keeping all of the lock-in.
- **Quotas start at zero.** In the SageMaker service quotas table, every
  `ml.g6e.*` "for endpoint usage" row reads "Each supported Region: **0**"
  (adjustable), and there are hard-ish defaults of "Maximum number of
  instances per endpoint: **4**" and "Number of instances across active
  endpoints: **4**", both adjustable
  [src](https://docs.aws.amazon.com/general/latest/gr/sagemaker.html). Every
  one of those is a support ticket before we can serve anything.
- **Price. Resolved 2026-09-20 — it *is* machine-readable, just not from the
  `meteredUnitMaps` endpoint this document used for EC2 and SQS.** The Price
  List API offer file (`publicationDate` 2026-09-20T02:56:06Z) gives
  `USE1-Host:ml.g6e.2xlarge` = **$2.8026/h** for real-time hosting and
  `USE1-AsyncInf:ml.g6e.2xlarge` = **$2.8026/h** for asynchronous inference
  [src](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonSageMaker/current/us-east-1/index.json).
  Against EC2's $2.24208 that is a **+25.0 % premium on every GPU-hour** — the
  market price of "someone else builds the queue". Same figure, independently
  derived, in
  [`08-cost-model-and-unit-economics.md`](08-cost-model-and-unit-economics.md)
  §1.3.
- **We would not control the container's vLLM flags** as directly — the NVDEC
  backend (§12.1) needs CUDA MPS configured on the host, which is a
  `serve.sh`-level concern on EC2 and a BYOC-image concern on SageMaker.
  ⚠️ **TO BE VERIFIED** whether MPS can be enabled in a SageMaker inference
  container.

**Verdict: C is not the main path.** It is a managed queue we would be buying
at the cost of our own auth, our own vLLM tuning, and a new quota regime.

---

## 6. Option D — ECS on EC2 GPU instances

ECS supports our hardware: "Amazon EC2 GPU-based container instances that use
the p2, p3, p4d, p5, g3, g4, g5, **g6, g6e**, and g6f instance types provide
access to NVIDIA GPUs", with a GPU-optimized AMI carrying the drivers and
Docker GPU runtime, `ECS_ENABLE_GPU_SUPPORT=true`, and per-container
`resourceRequirements`
[src](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-gpu.html).
Capacity providers with managed scaling let the ASG desired count go to 0, and
"Amazon ECS supports Amazon EC2 Auto Scaling warm pools"
[src](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html)
(with the caveat that instances can register before they finish initialising,
requiring a special agent config variable).

So D works. The question is what it adds over A:

- **Pro:** a task definition is a better deploy artefact than `install.sh` +
  systemd; rolling deploys and rollbacks are free; two models can share one
  large instance by GPU count.
- **Con:** it adds a scheduler layer (ECS) *and* keeps the ASG layer, so
  scale-out lag is ECS-capacity-provider lag **plus** ASG lag **plus** the
  2–3 min vLLM boot; managed scaling is documented as reactive with its own
  target-capacity control loop. It has none of B's cache-aware routing, none
  of B's portability, and none of C's built-in queue.

**Verdict: no.** ECS is the option that is worse than A on lag and worse than
B on everything else. Its only real niche — "we want containers but not
Kubernetes" — is not a constraint we have, because we are already running a
container (vLLM in docker) under systemd perfectly happily.

---

## 7. Option E — serverless GPU providers as an overflow valve

Not as the platform. As the thing that absorbs the 5–8 minute hole in §3.4.

| provider | L40S rate | shape | billing | notes |
|---|---|---|---|---|
| Modal | **$0.000542/s = $1.951/h** | + CPU $0.0000131/physical-core/s ($0.047/core-h), memory $0.00000222/GiB/s ($0.0080/GiB-h) | per second | Starter plan: "100 containers + 10 GPU concurrency"; Team: "5000 containers + 50 GPU concurrency" [src](https://modal.com/pricing) |
| RunPod (Pods) | **$1.09/h** Secure Cloud (**$0.79/h** Community Cloud), 48 GB VRAM, 94 GB RAM, 16 vCPU | Secure/Community toggle | per hour or per second | page "Updated September 13, 2026" [src](https://www.runpod.io/pricing) |
| RunPod (Serverless) | Flex workers "scale to zero when idle", Active workers "always running"; "billed from when a worker starts until it fully stops, rounded up to the nearest second"; container disk ~$0.10/GB/mo, network volume $0.07/GB/mo (<1 TB) | | per second | [src](https://docs.runpod.io/serverless/pricing) |
| Baseten (Dedicated Deployments) | **no L40S listed** — T4 $0.01052, L4 $0.01414, A10G $0.02012, A100 80 GB $0.06667, H100 $0.10833, B200 $0.16633, all **per minute** | | per minute | [src](https://www.baseten.co/pricing/) |

A like-for-like Modal L40S container matching `g6e.2xlarge` (1 L40S, 4 physical
cores ≈ 8 vCPU, 32 GiB) costs
`1.951 + 4×0.047 + 32×0.0080 = $2.40/h` — **7 % more than EC2 on-demand
($2.2421/h)**, billed per second with scale-to-zero. RunPod's L40S at $1.09/h (Secure Cloud;
Community Cloud is $0.79/h, verified 2026-09-20) with 16 vCPU and 94 GB RAM is
**51 % cheaper than the g6e.2xlarge and has twice the vCPU** — which, given M3/M4, is the shape we actually want.

**Why still not the platform:**
- ⚠️ No SOC 2 / data-residency story established for any of them in this repo;
  customer video would leave AWS.
- Cold start on a provider still includes our 2–3 min vLLM boot unless the
  provider snapshots (Modal's memory snapshots and RunPod FlashBoot both claim
  to help — ⚠️ **TO BE VERIFIED** for a vLLM container with `torch.compile`).
- Egress and the second video download reappear.

**Recommendation: build the *seam*, not the integration.** `gateway.py` should
route by an `UPSTREAMS` list rather than a single `UPSTREAM`. Once that exists,
adding a Modal or RunPod endpoint as a last-resort upstream when
`queue_wait_estimate > 60 s` is a config change, not a rewrite. That is ~30
lines today and buys the option cheaply. **Do the seam in the 3-month plan;
defer the provider itself.**

---

## 8. The front door, decided

| concern | answer |
|---|---|
| TLS | ACM on the ALB. Delete Caddy. |
| streaming | SSE survives any idle timeout because it is never idle; set ALB idle to **900 s** for the non-streaming path |
| non-streaming long requests | 900 s idle timeout + a gateway heartbeat while queued (§10.4) |
| max payload | ALB has no documented body limit for our sizes; `MAX_VIDEO_MB=64` stays the real cap. **Not** API Gateway (10 MB / 30 s) |
| static IP for customer firewalls | ⚠️ open — ALB IPs are AWS-managed and change; see §3.3 |
| routing | `least_outstanding_requests`, not round robin |
| draining | `deregistration_delay.timeout_seconds` ≥ 300 s, `TimeoutStopSec` larger still |
| LCU burst | ALB LCU reservation exists (quota: 15,000 reserved LCUs per ALB, adjustable) [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-limits.html); irrelevant at our volumes |

---

## 9. Where the queue lives — the central design decision

Three candidate homes. This is the R2/R3/R4 decision.

### 9.1 Candidate 1 — in `gateway.py` (recommended)

An `asyncio.Semaphore(MAX_CONCURRENCY)` plus an `asyncio.Queue(MAX_QUEUE)` in
front of the vLLM call, replacing today's `if inflight >= MAX_INFLIGHT: 429`.

**Pros.** Zero new infrastructure. The gateway already knows everything the
wait estimate needs (in-flight count, measured recent throughput, the clip's
duration). It keeps the HTTP connection open, so streaming still works. It is
portable to bare metal verbatim (R8).

**Cons.** The queue is per-instance, so a fleet of N gateways has N queues; a
request that lands on a busy box waits while another box is idle. Mitigation:
the ALB's `least_outstanding_requests` already balances by in-flight count,
which for a gateway that counts queued requests as in-flight is exactly the
right signal. A per-instance queue behind LOR is a good approximation of a
global queue. **Second con:** a gateway crash loses its queue — acceptable,
because a queued request has not been billed and the client retries.

### 9.2 Candidate 2 — SQS in front of a worker fleet

The classic AWS shape (§3.4), and the one AWS documents scaling for. Standard
queues hold ~120,000 in-flight messages; visibility timeout defaults to 30 s,
is extendable with `ChangeMessageVisibility`, and is hard-capped: "the
visibility timeout has a maximum limit of **12 hours** from when the message
is first received. Extending the timeout doesn't reset this 12-hour limit"
[src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html).
Price (us-west-2 metered unit map, published 2026-09-11): per-request rates of
$0.0000004 and $0.0000005 — **$0.40 and $0.50 per million** for standard and
FIFO — with fair queues at $0.10/million
[src](https://aws.amazon.com/sqs/pricing/). Free for the first 1M/month.
**Corrected 2026-09-20** (re-fetched the metered unit map,
`hawkFilePublicationDate` 2026-09-11T12:46:07Z): the assignment is **not**
inferred — the map labels the rows explicitly as `Standard per Requests` =
`0.0000004000` and `FIFO first-in first-out per Requests` = `0.0000005000`,
plus `Free tier per Requests` = 0. It is the **$0.10/M rate that carries no
label** (rate code `2ZJX6F22XVYTFA8V…`), i.e. the opposite of what open
question 14 below used to say. The map also carries volume tiers this
document omitted: us-west-2 `Standard per Requests Tier2` $0.30/M and `Tier3`
$0.24/M, `FIFO … Tier2` $0.40/M and `Tier3` $0.35/M.

**This breaks streaming.** A request that goes into SQS cannot hold an HTTP
connection to the client. You end up rebuilding SageMaker async inference by
hand. **Use SQS only for the batch lane (§5.1), where C already does it
better.**

FIFO's message-group semantics ("when a message with a message group ID is
in-flight, subsequent messages in that group are not made available") would be
a neat per-customer fairness primitive — one group per `org_id` — but the
streaming problem dominates.

### 9.3 Candidate 3 — let vLLM queue

vLLM already has a waiting queue and reports `vllm:num_requests_waiting`. Why
not just raise `max_num_seqs` and let it absorb everything?

Because vLLM's queue is **unbounded and blind**: it will accept 500 requests
and give every one of them a 5-minute TTFT, and it has no idea how long the
30th request will wait. R3 (honest wait estimates) and R4 (bounded) both fail.
See [`../scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md)
§3.1–§3.2 for the layered model: the engine queue should be *short*
(running batch + a little), and the *admission* queue should be ours.

### 9.4 The decision

**Queue in `gateway.py`, with vLLM's own queue kept short.** Concretely:

```
client → ALB (LOR) → gateway: [bounded FIFO queue] → semaphore(N) → vLLM (--max-num-seqs ≈ N)
```

Set `N = MAX_CONCURRENCY` to the measured best concurrency, not to a guess.
Today's `MAX_INFLIGHT=16` is above the measured c=8 sweet spot (M2/M3) — c=8
is where we have data, and going to 16 buys throughput we have not measured
while doubling TTFT. **Set `MAX_CONCURRENCY=8` until a sweep says otherwise**
(see [`../scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md)
§2.3 and §7 for the sweep design).

---

## 10. Queue mechanics: sizing, estimates, and the API contract

### 10.1 Queue bound

```
MAX_QUEUE = MAX_CONCURRENCY × acceptable_wait_s / mean_service_s
```

With `MAX_CONCURRENCY=8`, `acceptable_wait_s=30`, and mean service time
0.637 s per clip at c=8 (M2):

```
MAX_QUEUE = 8 × 30 / 0.637 ≈ 376   # per replica… but that is the wrong unit
```

Careful — 1.57 clips/s is the *replica's* throughput at c=8, i.e. service time
per clip *as seen by the fleet* is 0.637 s and the queue that keeps the wait
under 30 s is `30 / 0.637 ≈ 47` clips *per replica*, not per slot. Use:

```
MAX_QUEUE = acceptable_wait_s × replica_throughput_clips_per_s
          = 30 × 1.57 ≈ 47  →  round down to 40
```

which is the same 40 as the autoscaling target in §3.4, and that is not a
coincidence: **the queue bound and the scale-out target should be the same
number.** When the queue is full, the fleet is already scaling.

### 10.2 The wait estimate

```python
# gateway.py, at admission
eta_s = position_in_queue / max(recent_throughput_clips_per_s, 1e-3) + expected_ttft_s
```

`recent_throughput_clips_per_s` is an EWMA over completions in the last 60 s —
not a constant, so it degrades correctly when 120 s clips arrive.
`expected_ttft_s` scales with the clip: 0.77 s at c=1, 3.35 s at c=8 (M1/M2),
and the video budget is already computed in `budget_kwargs()`.

Honesty test (R3): log `eta_s` alongside the realised wait in `usage.jsonl`,
and gate the launch on p90 |error| ≤ 30 %.

### 10.3 Status codes and headers

Extending
[`../scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md)
§3.5:

| condition | response |
|---|---|
| slot free | 200, `Inference-Id`, `X-Queue-Wait-Seconds: 0` |
| queued, under bound | 200 (held open), `X-Queue-Position: 12`, `X-Estimated-Wait-Seconds: 8` |
| queue full | **429**, `Retry-After: ceil(MAX_QUEUE / recent_throughput)`, body naming the real drain rate |
| fleet scaling out | 429 with `Retry-After` from the §3.4 lag budget, not a fixed `2` |
| upstream dead | 503, `Retry-After: 30` |

Today's gateway returns `Retry-After: 2` unconditionally. A client that obeys
it during a 6-minute scale-out retries 180 times. **`Retry-After` must be
computed, not constant** — that is a two-line change with a large effect on R1.

### 10.4 Holding the connection open while queued

A non-streaming request queued for 40 s is 40 s of silence on the wire. Two
fixes, in order of preference:

1. **If the client sent `stream: true`** (which our docs should push as the
   default), emit an SSE comment line `: queued position=12 eta=8\n\n` every
   5 s. Comments are ignored by every conformant SSE client, the connection
   never idles, and the caller can surface a progress bar.
2. **If not**, rely on the 900 s ALB idle timeout and accept the silence, but
   cap `MAX_QUEUE` so the worst wait stays under ~120 s.

### 10.5 Per-customer fairness

One org submitting 500 clips must not starve everyone else. The cheapest
correct thing is a **per-org cap on queued requests** (`MAX_QUEUE_PER_ORG`,
say `MAX_QUEUE / 4`), enforced with the `org_id` the gateway already gets from
`authenticate()`. Full VTC-style fairness
([`../scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md)
§4.7) is not worth it at this scale.

---

## 11. Caching — what is actually cacheable here

M4 says the LM is not the bottleneck, so **text prefix caching is not where
the wins are**. Four caches, ranked by measured value:

### 11.1 Stop downloading the video twice (biggest single win)

Measured: end-to-end 3.8 s, TTFT 3.2 s, "dominated by downloading and decoding
the 5.5 MB source **twice**, gateway and vLLM"
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)). The gateway
already downloads the clip to a `NamedTemporaryFile` to run `ffprobe`, then
throws it away and lets vLLM fetch the URL again.

Fix: keep the bytes and hand them to vLLM as a `data:` URL, or write them to a
path both processes can read and pass a local URL. This is a change inside
`video_seconds()` / `chat()` in
[`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py), maybe 20 lines.
**Expected: ~40 % off TTFT for URL-sourced clips, for free.**

### 11.2 GPU video decoding (NVDEC) — the fix for M3

vLLM supports decoding video on the GPU's dedicated video engines:

> "The `pynvvideocodec` backend uses NVIDIA NVDEC to decode the sampled video
> frames on the GPU before copying them into host memory for multimodal
> preprocessing. **For workloads with large videos and relatively light
> inference, such as video tagging, this can alleviate bottlenecks in
> CPU-based video decoders.**"
> [src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)

That sentence describes our workload exactly. An L40S has **3 NVENC and 3
NVDEC engines** (including AV1)
[src](https://www.nvidia.com/en-us/data-center/l40s/); an L4 has **2 NVENC,
4 NVDEC, 4 JPEG decoders** [src](https://www.nvidia.com/en-us/data-center/l4/).
These sit idle today while 8 vCPUs thrash.

Two backends:

```bash
# PyNvVideoCodec — requires CUDA MPS, reserves VRAM out of the KV budget
export VLLM_VIDEO_LOADER_BACKEND=pynvvideocodec
vllm serve /model --mm-ipc-gpu-memory-gb 1 \
  --media-io-kwargs '{"video": {"backend": "pynvvideocodec", "hw_decoders": 2}}'

# DeepStream — "the recommended GPU backend for streaming video sources"
export VLLM_VIDEO_LOADER_BACKEND=deepstream
vllm serve /model --media-io-kwargs '{"video": {"backend": "deepstream", "pool_size": 12}}'
```

Documented constraints [src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html):
- PyNvVideoCodec **requires CUDA MPS** ("Video decoding runs in the API server
  process while model serving runs in the engine process… Configure and start
  MPS before starting vLLM") and a positive `--mm-ipc-gpu-memory-gb`, carved
  out of the KV cache. `hw_decoders` defaults to 2, "the recommended starting
  point for concurrent video workloads"; each extra slot costs VRAM.
- DeepStream needs `pip install vllm[deepstream]` plus GStreamer system
  packages; `pool_size` is clamped to [1,16] and defaults to
  `VLLM_MEDIA_LOADING_THREAD_COUNT` (default **8**).
- TorchCodec has `seek_mode: "approximate"` which "skips that scan for faster
  decoder creation" — a cheap intermediate win if NVDEC proves fiddly.

**This is the highest-leverage change in the document.** If it moves 1080p
throughput from 1.57 toward the 3.58 clips/s that the GPU achieves on cheap
sources (M3), it is a **2.3× throughput-per-dollar improvement on the hardware
we already have** — R7 satisfied by a flag in
[`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh). ⚠️ **TO BE
VERIFIED by measurement**, on distinct clips (notes.md caveat 5: identical
clips let the mm cache absorb decode cost).

### 11.3 vLLM's multimodal cache, and why it forces a routing decision

vLLM caches processed multimodal inputs: processor caching "is automatically
enabled to avoid repeatedly processing the same multi-modal inputs", sized by
`mm_processor_cache_gb` (**default 4 GiB**), with `mm_processor_cache_type="shm"`
for shared memory across TP workers
[src](https://docs.vllm.ai/en/latest/configuration/optimization.html).

And clients can address it explicitly — the API accepts a **`uuid`** with a
null payload, so a repeat submission skips the upload entirely:

```json
{ "type": "video_url", "video_url": {}, "uuid": "<sha256 of the clip>" }
```

[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)

**The catch:** this cache lives in one engine process. With N replicas behind
a round-robin or least-outstanding ALB, a repeated clip has a 1/N chance of
hitting the replica that holds it. To exploit it we need **content-affinity
routing**: hash the clip and prefer the replica that saw it.

That is not something an ALB can do (it offers cookie stickiness, not
content-based hashing), and llm-d's prefix-cache-aware routing is aimed at text
(§4.2). **It belongs in `gateway.py`**: a rendezvous/consistent hash over
`sha256(video_bytes)` selecting among the `UPSTREAMS` list, with fallback to
least-loaded when the preferred upstream is saturated. ~40 lines. This is also
the single cleanest reason to run *one gateway fleet fronting many vLLM
replicas* rather than one gateway per vLLM.

Raise `mm_processor_cache_gb` well above 4 GiB on g6e (48 GB VRAM for a 5.4 GB
model leaves room), and watch `vllm:mm_cache_hits / vllm:mm_cache_queries`
[src](https://docs.vllm.ai/en/latest/usage/metrics.html) to prove it is
working.

### 11.4 A response cache in the gateway

The cheapest cache of all: `sha256(video_bytes ‖ prompt ‖ sampling_params ‖
mm_kwargs) → completion`, in Redis/Valkey, short TTL. A repeated identical
request costs one Redis GET instead of a GPU-second. Given that users will
iterate on prompts against the same clip, and that `temperature: 0` is in our
own documented snippet
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)), the hit rate
could be material.

ElastiCache Serverless bills in **ECPUs** — "Reads and writes require 1 ECPU
for each kilobyte (KB) of data transferred" — with Valkey offering "33% lower
pricing and 90% lower minimum data storage of 100 MB compared to other
supported engines" [src](https://aws.amazon.com/elasticache/pricing/).
**Resolved 2026-09-20** (the page's own metered unit map,
`https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/elasticache/USD/current/elasticache.json`,
`hawkFilePublicationDate` 2026-09-14T06:37:14Z, us-east-1): Serverless
**Valkey $0.084 / GB-hour and $0.0000000023 / ECPU** ($2.30 per billion
ECPUs); Redis and Memcached $0.125 / GB-hour and $0.0000000034 / ECPU — which
is where the "33 % lower" claim comes from (0.084/0.125 = 0.672,
0.0023/0.0034 = 0.676). Node-based `cache.t4g.micro` Valkey is
**$0.0128/h = $9.34/month**.

**And the node-vs-serverless guess in this section was backwards.** At the
100 MB serverless minimum, Valkey Serverless floors at
`0.1 GB × $0.084 × 730 h = $6.13/month` plus ECPUs — *cheaper* than the
$9.34/month `cache.t4g.micro`, and it scales to zero-ish. At our request
volume the ECPU term is noise: a 10 M-clip month at ~4 KB per cached
completion is ≈ 8 × 10⁷ ECPUs ≈ **$0.18/month**. **Take Serverless Valkey.**

**Billing note:** a cache hit must still write a `usage_events` row, with
`cached: true` (the column already exists in the schema —
[`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) writes
`"cached": False` today) and, presumably, `cost_usd = 0` or a reduced rate.
That is a product decision to make before shipping the cache, not after.

### 11.5 Video token pruning (cuts prefill, not decode)

vLLM can "prune video tokens after the vision encoder to reduce prefill time
and KV cache usage, at some cost in accuracy" via `--video-pruning-rate <q>`
with `--video-pruning-method evs` (default, Efficient Video Sampling) or
`vidcom2` [src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html).
Note "Enabling video pruning also disables encoder CUDA graphs, since the
retained token count becomes data-dependent", and `vidcom2` is Qwen3-VL-only.

Given M4 (6 % throughput difference between 2K and 12K prompt tokens),
**pruning is not where our wins are** — it attacks the part that is already
cheap. It becomes interesting only for 120 s clips (23.5 K tokens) where KV
pressure is real. Park it; revisit with the long-clip benchmark that
`notes.md` lists as open.

---

## 12. Autoscaling signals: what to scale on, per option

| option | signal | publish path | poll interval |
|---|---|---|---|
| A | `QueueDepth / GroupInServiceInstances` | gateway → `PutMetricData` → CloudWatch metric math | 60 s metric + 2×60 s alarm |
| B | `sum(vllm:num_requests_waiting) / count(vllm:num_requests_running)` | vLLM `/metrics` → Prometheus → KEDA | 15–30 s |
| C (async) | `ApproximateBacklogSizePerInstance`, `HasBacklogWithoutCapacity` | built in | 60 s × 2 |
| D | same as A | same | same |

Better signals than raw queue depth, available in vLLM today
[src](https://docs.vllm.ai/en/latest/usage/metrics.html):

- `vllm:num_requests_waiting_by_reason{reason="capacity"}` — excludes requests
  waiting for non-capacity reasons, so it does not scale out for the wrong
  cause.
- `vllm:request_queue_time_seconds` (histogram) — **this is the SLI**, and a
  target-tracking policy on its p90 is closer to the SLO than a proxy count.
- `vllm:kv_cache_usage_perc` and `vllm:num_preemptions` — the *saturation*
  signals. A rising preemption count with a full KV cache means the replica is
  thrashing; see
  [`../scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md)
  §2 for the preemption-storm detection pattern.

Note the gateway's own queue is *invisible* to vLLM's metrics — by design
(§9.4), vLLM's queue stays short. So on option A the gateway must publish
`QueueDepth` itself; on option B a sidecar or the gateway must expose it to
Prometheus. **This is an argument for exposing `/metrics` from `gateway.py` in
Prometheus format regardless of option** — it makes A and B share one
observability surface, which is most of R8.

---

## 13. Instance selection for Marlin-2B

### 13.1 The price table (us-east-1, Linux, on-demand)

From the AWS pricing feed at
`https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json`,
`hawkFilePublicationDate` **2026-09-18T20:33:44Z**
[src](https://aws.amazon.com/ec2/pricing/on-demand/). GPU counts and vCPU from
the EC2 instance types guide
[src](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html).

| type | GPU | GPUs | vCPU | vCPU/GPU | RAM GiB | $/h | $/GPU-h | local NVMe |
|---|---|---|---|---|---|---|---|---|
| `g6e.xlarge` | L40S 48 GB | 1 | 4 | 4 | 32 | 1.8610 | 1.8610 | 250 GB |
| **`g6e.2xlarge`** (today) | L40S 48 GB | 1 | 8 | 8 | 64 | **2.2421** | 2.2421 | 450 GB |
| **`g6e.4xlarge`** | L40S 48 GB | 1 | 16 | **16** | 128 | **3.0042** | 3.0042 | 600 GB |
| `g6e.8xlarge` | L40S 48 GB | 1 | 32 | **32** | 256 | 4.5286 | 4.5286 | 900 GB |
| `g6e.16xlarge` | L40S 48 GB | 1 | 64 | 64 | 512 | 7.5772 | 7.5772 | 1900 GB |
| `g6e.12xlarge` | L40S 48 GB | 4 | 48 | 12 | 384 | 10.4926 | **2.6231** | 2×1900 GB |
| `g6e.24xlarge` | L40S 48 GB | 4 | 96 | 24 | 768 | 15.0656 | 3.7664 | 2×1900 GB |
| `g6e.48xlarge` | L40S 48 GB | 8 | 192 | 24 | 1536 | 30.1312 | 3.7664 | 4×1900 GB |
| `g6.2xlarge` | L4 24 GB | 1 | 8 | 8 | 32 | 0.9776 | 0.9776 | 450 GB |
| `g6.4xlarge` | L4 24 GB | 1 | 16 | 16 | 64 | 1.3232 | 1.3232 | 600 GB |
| `g6.8xlarge` | L4 24 GB | 1 | 32 | 32 | 128 | 2.0144 | 2.0144 | 2×450 GB |
| `g5.2xlarge` | A10G 24 GB | 1 | 8 | 8 | 32 | 1.2120 | 1.2120 | 450 GB |
| `g5.4xlarge` | A10G 24 GB | 1 | 16 | 16 | 64 | 1.6240 | 1.6240 | 600 GB |
| `g5.8xlarge` | A10G 24 GB | 1 | 32 | 32 | 128 | 2.4480 | 2.4480 | 900 GB |
| `p5.4xlarge` | H100 80 GB | 1 | 16 | 16 | 256 | 6.8800 | 6.8800 | 3840 GB |
| `p5.48xlarge` | H100 80 GB | 8 | 192 | 24 | 2048 | 55.0400 | 6.8800 | 8×3840 GB |
| `p6-b200.48xlarge` | B200 | 8 | 192 | 24 | 2048 | 113.9328 | 14.2416 | 8×3840 GB |
| `p6-b300.48xlarge` | B300 | 8 | 192 | 24 | 4096 | 142.4160 | 17.8020 | 8×3840 GB |

`p5e.48xlarge` did **not** appear in the us-east-1 Linux on-demand feed;
`p5en.48xlarge` is $63.2960/h. ⚠️ **TO BE VERIFIED** whether p5e is offered in
us-east-1 at all.

Cross-check against [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md)
§5: that document records OCI L40S at $3.50/GPU-h and several neoclouds in the
$0.74–$1.57 range, which brackets RunPod's $1.09 and Modal's $1.95 above. AWS
at $2.24 for a 1-GPU L40S node is mid-pack for the GPU and expensive for the
CPU that comes with it — which is exactly our problem.

### 13.2 Two notes on the table

- **`g6e.xlarge` at 4 vCPU is a trap.** $0.38/h cheaper than the 2xlarge but
  half the CPU that is already the bottleneck. Do not use it for this model.
- **`g6e.12xlarge` is the cheapest L40S on AWS at $2.6231/GPU-h** *if* you can
  use 4 GPUs. It gives only 12 vCPU/GPU, though — worse than the 4xlarge's 16.
  It becomes right once one node hosts 4 replicas *and* decode has moved to
  NVDEC (§11.2), because then vCPU stops mattering.

### 13.3 Throughput per dollar, from the measurement

Anchors (M2/M3, one L40S with 8 vCPU): **1.57 clips/s** on 1080p sources,
**3.58 clips/s** on 360p sources at the same ~2K-token budget. The delta is
pure CPU decode. So:

- 8 vCPU sustains ≈ 1.57 clips/s of 1080p decode.
- The GPU-side ceiling at this token budget is **≥ 3.58 clips/s**.

Model the 1080p case as `min(1.57 × vCPU/8, 3.58)` — linear in CPU until the
GPU caps it. **This is an extrapolation, `est.`, not a measurement:**

| type | $/h | vCPU | est. clips/s (1080p) | est. $/1,000 clips | vs today |
|---|---|---|---|---|---|
| `g6e.2xlarge` | 2.2421 | 8 | **1.57 (meas.)** | **$0.397** | baseline |
| `g6e.4xlarge` | 3.0042 | 16 | 3.14 (est.) | **$0.266** | **−33 %** |
| `g6e.8xlarge` | 4.5286 | 32 | 3.58 (GPU cap) | $0.351 | −12 % |
| `g6e.16xlarge` | 7.5772 | 64 | 3.58 (GPU cap) | $0.588 | +48 % |

**Predicted optimum: `g6e.4xlarge`** — the largest size whose CPU is still the
binding constraint. Beyond it you pay for vCPU the GPU cannot use.

Now the same table assuming §11.2 works and decode moves to NVDEC, so every
size runs at the GPU ceiling:

| type | $/h | est. clips/s | est. $/1,000 clips |
|---|---|---|---|
| **`g6e.2xlarge`** | 2.2421 | 3.58 | **$0.174** |
| `g6e.4xlarge` | 3.0042 | 3.58 | $0.233 |
| `g6e.12xlarge` (÷4) | 2.6231/GPU | 3.58 | $0.204 |

**If NVDEC works, staying on `g6e.2xlarge` is 2.3× better than today and 35 %
better than moving to the 4xlarge.** That ordering inversion is the whole
reason §11.2 must be measured *before* any instance-size decision. Do not
resize the fleet first.

Sanity check against `notes.md`'s own cost sketch — **and it does not check
out.** 1.57–3.58 clips/s × 10.1 s = 15.9–36.2 video-seconds per wall-second.
One GPU-hour is 3,600 wall-seconds, so that is 57,100–130,200 **video-seconds**
= **15.9–36.2 video-hours** per GPU-hour, not "57–129 video-hours":
`notes.md` printed the video-*seconds* figure with an "hours" label, and this
document copied it. At $2.2421/h the honest number is

```
$2.2421 / 36.2 video-h  = $0.062 per video-hour   (360p source, 3.58 clips/s)
$2.2421 / 15.9 video-h  = $0.141 per video-hour   (1080p source, 1.57 clips/s)
```

**$0.062–$0.141 per video-hour, i.e. 3.6× the $0.017–$0.039 previously
printed here and in [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md)
("Cost sketch"), which still carries the error and should be recut.**
([`08-cost-model-and-unit-economics.md`](08-cost-model-and-unit-economics.md)
§1.2 found the same bug independently and prints **$0.063–$0.143** — the
0.3–1.4 % gap is only the clip length: 08 uses the nominal 10 s, this uses
`sample-10s.mp4`'s actual 10.1 s. Both are right; pick 10.1 s when quoting
this clip.)
Corrected 2026-09-20. Nothing else in §13 depends on it — the $/1,000-clips
columns above were computed independently and are right.

### 13.4 L4 and A10G — worth a benchmark, not a migration

| GPU | memory | bandwidth | video engines | source |
|---|---|---|---|---|
| L40S | 48 GB GDDR6 ECC | 864 GB/s | 3 NVENC / 3 NVDEC (incl. AV1); **no MIG, no NVLink** | [src](https://www.nvidia.com/en-us/data-center/l40s/) |
| L4 | 24 GB | 300 GB/s | 2 NVENC / 4 NVDEC / 4 JPEG; 72 W | [src](https://www.nvidia.com/en-us/data-center/l4/) |
| A10G | 24 GB | ⚠️ **TO BE VERIFIED** (AWS does not publish it; the NVIDIA A10 datasheet is the closest proxy) | ⚠️ | — |

Marlin-2B is 5.4 GB BF16, so it fits in 24 GB with ~18 GB left for KV — plenty
for 32K context at 2B scale. **L4 has more NVDEC engines than L40S (4 vs 3)
and `g6.8xlarge` costs $2.0144/h — 10 % less than today's box with 4× the
vCPU.** If the workload is decode-bound and the LM is cheap (M4), a fleet of
`g6.4xlarge`/`g6.8xlarge` could beat g6e outright, and L4 capacity is far less
scarce than L40S.

⚠️ **TO BE VERIFIED, and this is the highest-value benchmark in the plan:**
run `models/marlin2b/bench.py -c 8` on `g6.4xlarge` and `g6.8xlarge` and
compare $/1,000 clips against the g6e rows above. L4's 300 GB/s (vs L40S's
864) will hurt decode-phase token throughput, but at ~200 output tokens per
clip, decode is a small share of the request. **If L4 lands within 30 % of
L40S throughput, it wins on both price and availability.**

### 13.5 The next two models

- **Qwen3.8-27B** (single H100/H200/L40S-FP8 class). At FP8, 27B ≈ 27 GB —
  fits one L40S 48 GB with room for KV. Serving it on `g6e.4xlarge`/`8xlarge`
  keeps one instance family for the whole fleet, which is worth real money in
  ODCR flexibility. `p5.4xlarge` (1 H100, $6.88/h) is the alternative if
  latency demands it.
- **DeepSeek-V4.1-Flash** (multi-GPU). Needs `g6e.12xlarge`/`24xlarge`/`48xlarge`
  or p5-class. This is the model that forces option B: multi-GPU replicas mean
  gang scheduling, and gang scheduling means Kubernetes (see
  [`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md)
  §7.2). It is also the model that makes Capacity Blocks relevant (§3.7).

---

## 14. Cost of each option at three traffic levels

Assumptions: `g6e.2xlarge` at $2.2421/h; 1.57 clips/s per replica (today's
measured 1080p number); `MAX_CONCURRENCY=8`; N+1 headroom; 730 h/month; ALB
base $0.0225/h ≈ $16/month plus LCU (negligible at these rates).

| monthly clips | peak clips/s (3× mean, 8 h/day active) | replicas needed | A: $/month | B: $/month | E overflow at RunPod $1.09/h |
|---|---|---|---|---|---|
| 100 K | ~0.35 | 1 (+0 headroom) | 1,635 + 16 = **1,651** | +73 = **1,724** | — |
| 1 M | ~3.5 | 3 (2 + 1) | 4,906 + 16 = **4,922** | **4,995** | 2 spare workers ≈ $1,590 if always on; far less per-second |
| 10 M | ~35 | 23 ⚠️ (see note) | 37,610 + ~50 = **~37,660** | **~37,730** | — |

**Corrected 2026-09-20 (arithmetic).** The 10 M row previously read
"51,563 + ~50 = ~51,600", which is 31.5 replicas at $1,636.7/month, not 23:
`23 × $2.24208 × 730 = $37,610`. Both EKS columns move with it.

⚠️ The `(N + 1)` decompositions were also wrong in the same direction and are
dropped rather than silently repaired: at 1.57 clips/s per replica, 2 replicas
serve 3.14 clips/s < the 3.5 peak and 22 serve 34.5 < 34.7, so a genuine N+1
fleet is **4** replicas at 1 M ($6,547/month) and **24** at 10 M
($39,281/month). The costs tabulated above are for 3 and 23 — the smallest
fleets that *meet* peak with no headroom. Pick one convention before §16.3
step 8 sets `min`/`max` on the ASG.

The EKS premium is **4.4 % at 100 K clips/month and 0.19 % at 10 M** ($73 of
a corrected $37,660). Money is not the reason to pick A over B; operational
surface area is.

At 10 M clips/month (~$37.7 K), the §11.2 NVDEC win alone — if it delivers the
modelled 2.28× ($0.397 → $0.174 per 1,000 clips, §13.3) — is worth roughly
**$21 K/month**. That is still the entire justification for doing the
measurement before anything else.

Reserved-instance and Savings Plan rates for g6e were not retrieved
machine-readably during this research; ⚠️ **TO BE VERIFIED** before committing
to steady-state capacity, since a 1-year Compute Savings Plan would materially
change the ODCR arithmetic in §3.7.

---

## 15. Portability to the bare-metal cluster

R8 in one paragraph. Option A ports almost perfectly: `gateway.py` +
systemd + a reverse proxy is the same on a rack as on EC2; only the ASG and
ALB are AWS-specific, and on bare metal they become "a fixed number of boxes"
and "HAProxy/Envoy". The scaling policy has no analogue — but
[`../scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md)
§3.7 already establishes that "there is no cluster autoscaler" on bare metal,
so the queue-and-admit logic in the gateway is what carries over, and it is
exactly what carries the most value. Option B ports best of all — the same
Deployment/ScaledObject manifests run on the cluster with Karpenter swapped
for a fixed node pool. Options C and E do not port at all, which is the reason
neither can own the request path.

---

## 16. Recommendation

### 16.1 Next 3 months — **Option A, hardened**

**One ALB, one ASG of `g6e.2xlarge` (or `4xlarge` after §11.2 is measured),
min 2 / max 8, backed by an ODCR, with the queue and the cache in
`gateway.py`.**

Why, in order:
1. The 5.3–8.8 min scale-out lag (§3.4) means **the queue matters ten times more
   than the orchestrator**, and the queue is the same code in A and B.
2. The biggest available wins — download-once (§11.1), NVDEC (§11.2),
   content-affinity routing (§11.3), response cache (§11.4) — are all in
   `gateway.py` and `serve.sh`. None of them is easier on Kubernetes.
3. g6e scarcity is solved by an ODCR, which works identically under A and B.
4. Two people should not operate a Kubernetes control plane to serve one 2B
   model on one GPU per replica.

### 16.2 Next 12 months — **Option B, when a second GPU shape arrives**

Move to EKS + Karpenter + KEDA when DeepSeek-V4.1-Flash (multi-GPU, gang
scheduling) or a third model shape lands, or when the bare-metal cluster
arrives and we want one set of manifests. Karpenter's
`capacityReservationSelectorTerms` and `instanceStorePolicy: RAID0` are worth
the migration on their own once there is more than one node shape to manage.
Hold llm-d and Dynamo until there is a measured cache-affinity win to route on.

### 16.3 Migration path from the single box

Each step is independently shippable and independently reversible.

| step | change | risk | expected effect |
|---|---|---|---|
| 0 | **Measure.** `bench.py -c 8` with distinct clips, with and without `VLLM_VIDEO_LOADER_BACKEND=pynvvideocodec`; also on `g6.8xlarge` | none | decides §13's instance question |
| 1 | Download the video once (§11.1) | low | ~40 % off TTFT |
| 2 | Bounded queue + honest wait headers + computed `Retry-After` (§10) | low | R1–R4 |
| 3 | `/health` gated on a real warm-up request (§3.5) | low | no 18 s stalls on scale-out |
| 4 | `/metrics` in Prometheus format from `gateway.py` (§12) | low | one observability surface for A and B |
| 5 | ALB + ACM in front of the existing box; delete Caddy; EIP decision (§3.3, §8) | medium | TLS off the box; second box becomes possible |
| 6 | ASG of 1, instance refresh as the deploy mechanism | medium | replaces `install.sh`-on-a-box |
| 7 | **ODCR for `min+1`** g6e in the AZ that has capacity (§3.7) | low | the second replica is guaranteed to exist |
| 8 | Scale to min 2, target-tracking on backlog-per-instance (§3.4) | medium | R5; survives one AZ/instance failure |
| 9 | `UPSTREAMS` list + content-affinity routing (§11.3) | medium | mm-cache hits at N>1; also the overflow seam (§7) |
| 10 | Response cache in Redis/Valkey + `cached: true` billing rule (§11.4) | medium | R6 |
| 11 | Resize per step 0's answer; adopt NVDEC in `serve.sh` | medium | R7 |
| 12 | *(optional, later)* `POST /v1/batch` on SageMaker async (§5.1) | low | batch lane without touching the interactive path |

Steps 0–4 need no AWS resource changes at all and deliver most of R1–R4, R6
and R7.

---

## Implications for our system

In the order they should be done.

1. **Run the decode benchmark before anything else.**
   `models/marlin2b/bench.py` with distinct clips, `c=8`, comparing the default
   CPU decoder against `VLLM_VIDEO_LOADER_BACKEND=pynvvideocodec` and
   `deepstream`, on `g6e.2xlarge` and `g6.8xlarge`. Everything in §13 branches
   on the result, and the modelled prize is 2.3× throughput per dollar. Record
   in `models/marlin2b/results/notes.md`.

2. **`apps/infrx-api/gateway.py`: download the video once.** `video_seconds()`
   already has the bytes; hand them to vLLM rather than letting it re-fetch
   the URL. ~20 lines, ~40 % off TTFT for URL-sourced clips (M7).

3. **`apps/infrx-api/gateway.py`: replace the 429-at-16 with a bounded queue.**
   `MAX_CONCURRENCY=8` (the measured sweet spot, down from `MAX_INFLIGHT=16`),
   `MAX_QUEUE=40` per replica (§10.1), `X-Queue-Position` and
   `X-Estimated-Wait-Seconds` headers, SSE `: queued …` heartbeats, and a
   **computed** `Retry-After` from the recent drain rate instead of the
   constant `2`. Per-org queue cap at `MAX_QUEUE/4`.

4. **`apps/infrx-api/gateway.py`: `/health` must fail until warm.** Fire one
   synthetic request at the production `mm_processor_kwargs` on startup (M5's
   18 s penalty) and gate `/health` on it, or the ALB will route into a stall
   on every scale-out.

5. **`apps/infrx-api/gateway.py`: expose `/metrics`.** `QueueDepth`,
   `Inflight`, `QueueWaitSeconds` histogram, cache hit counters. Prometheus
   text format, scrapeable by both a CloudWatch agent (option A) and
   Prometheus (option B) — this is most of R8.

6. **`models/marlin2b/serve.sh`: add the decode and cache flags.**
   `VLLM_VIDEO_LOADER_BACKEND` per the benchmark, `--mm-ipc-gpu-memory-gb`,
   `--mm-processor-cache-gb` well above the 4 GiB default, and keep
   `--max-num-seqs` aligned with the gateway's `MAX_CONCURRENCY`.

7. **Front door: ALB + ACM, delete `apps/infrx-api/deploy/Caddyfile`.** Set
   `idle_timeout.timeout_seconds=900`,
   `load_balancing.algorithm.type=least_outstanding_requests`,
   `deregistration_delay.timeout_seconds=300`. Decide the Elastic IP question
   (§3.3) before cutting DNS over. Do **not** put API Gateway anywhere near
   this path (30 s / 10 MB).

8. **New `apps/infrx-api/deploy/asg.yaml`.** Launch template from the current
   AMI, ASG min 2 / max 8, `DefaultInstanceWarmup=240`, a 900 s group
   `DefaultCooldown` **or** `DisableScaleIn` + a separate step policy (§3.4 —
   EC2 target tracking has no scale-in cooldown of its own),
   target-tracking on the metric-math backlog-per-instance expression with
   `TargetValue: 40` (§3.4). No warm pool — g6e cannot hibernate
   (§3.6). Narrow ABS allow-list across AZs instead.

9. **Buy an ODCR for `min+1` g6e.2xlarge in the AZ that has capacity.**
   ≈ $1,635/month of marginal cost for the guarantee that replica #2 exists.
   Capacity Blocks are not available for the G family and are irrelevant until
   p5-class lands.

10. **`UPSTREAMS` instead of `UPSTREAM`, with content-affinity routing.**
    Rendezvous hash on `sha256(video_bytes)` over healthy upstreams, falling
    back to least-loaded. This is what makes vLLM's per-process multimodal
    cache (and the `uuid` API) usable at N>1, and it is simultaneously the
    seam for a serverless overflow upstream (§7).

11. **Response cache in ElastiCache (Valkey).** Key on
    `sha256(video ‖ prompt ‖ params ‖ mm_kwargs)`. Decide the billing rule for
    `cached: true` rows in `usage_events` *before* shipping, and reflect it in
    `apps/README.md` §6 and the console's Usage page (F5 already has a "cache
    hit" tile).

12. **`apps/README.md` and the console docs** need a new section: queueing
    behaviour, the new headers, what a 429 now means, and the recommendation
    to use `stream: true`.

13. **Defer:** EKS/Karpenter/KEDA until a second GPU shape or the bare-metal
    cluster (§16.2); SageMaker async as a batch lane only (§5.1); spot as a
    third tier only (§3.7, >20 % interruption on `g6e.4xlarge`); video token
    pruning until long clips are benchmarked (§11.5).

---

## Open questions

⚠️ Consolidated. Each names how to resolve it.

1. **Does NVDEC actually move the number?** §11.2 predicts 1.57 → ~3.58
   clips/s on 1080p sources. Requires CUDA MPS on the host and VRAM carved out
   of the KV budget. **Resolve:** benchmark with distinct clips (the
   `notes.md` caveat about the mm cache absorbing decode cost applies).

2. **How long does a `g6e.2xlarge` take from `RunInstances` to `running`?**
   §3.4 budgets 60–90 s with no source. **Resolve:** launch ten and time them;
   the number sets `DefaultInstanceWarmup` and the `Retry-After` the gateway
   reports during scale-out.

3. **Does any current caller depend on the Elastic IP `100.57.145.167`?** ALB
   IPs are AWS-managed and change (§3.3). **Resolve:** ask the current users
   before cutting DNS to an ALB; if yes, NLB-in-front or Global Accelerator.

4. **L4 vs L40S for this model.** `g6.8xlarge` is $2.0144/h with 32 vCPU and
   4 NVDEC engines, vs `g6e.2xlarge` at $2.2421/h with 8 vCPU and 3 NVDEC.
   L4's 300 GB/s vs L40S's 864 GB/s will hurt decode-phase throughput by an
   unknown amount. **Resolve:** §13.4's benchmark. Potentially the largest
   single cost win in the document, and L4 capacity is less scarce.

5. ~~**SageMaker `ml.g6e.*` real-time and async hourly rates.**~~ **RESOLVED
   2026-09-20** from the Price List API offer file: real-time hosting and
   async inference are both **$2.8026/h** for `ml.g6e.2xlarge`, **+25.0 %**
   over EC2. See §5.2.

6. ~~**ElastiCache Serverless ECPU and GB-hour rates.**~~ **RESOLVED
   2026-09-20** from the pricing page's own metered unit map (published
   2026-09-14): Serverless Valkey **$0.084/GB-hour, $0.0000000023/ECPU**;
   Redis/Memcached $0.125 and $0.0000000034. The suspected node-based win was
   backwards — the 100 MB Serverless Valkey floor is ~$6.13/month against
   $9.34/month for `cache.t4g.micro`. See §11.4.

7. **g6e Savings Plan / Reserved Instance rates.** Not retrieved *here* —
   but **[`08-cost-model-and-unit-economics.md`](08-cost-model-and-unit-economics.md)
   §1.3–§1.4 already carries them**: `g6e.2xlarge` at **$1.41251/h** (1-year
   no-upfront g6e Instance Savings Plan) and **$0.84302/h** (3-year
   all-upfront), against $2.24208 on demand. Folding those in changes the ODCR
   arithmetic in §3.7 and every row of §14 — **this document's cost tables are
   on-demand-only and therefore overstate steady-state cost by up to 62 %.**
   **Resolve:** recut §3.7 and §14 against doc 08's tiers, then validate with
   Cost Explorer's Savings Plans recommendations at 30 days of steady load.

8. **Can llm-d's Endpoint Picker route on a request-body `uuid`?** If yes,
   multimodal-cache-affinity routing is a ~100-line plugin and becomes the
   strongest argument for option B (§4.2). **Resolve:** read the GAIE scorer
   plugin interface.

9. **Can CUDA MPS be enabled inside a SageMaker inference container?** If not,
   option C can never use the NVDEC backend, which alone disqualifies it even
   for the batch lane (§5.2).

10. **Does the `torch.compile` cache survive an EC2 stop/start on an EBS
    root?** If it does, and we move weights off the instance store, a
    `Stopped` warm pool recovers some of the 2–3 min boot after all (§3.6).
    See [`../scaling/06-cold-start.md`](../scaling/06-cold-start.md) §3 for
    the cache mechanics.

11. **Is `p5e.48xlarge` offered in us-east-1?** It is absent from the
    us-east-1 Linux on-demand feed but present in the Capacity Blocks table
    for us-east-1 (§3.7, §13.1). **Resolve:** `describe-instance-type-offerings`.

12. **Which AZs currently offer g6e in us-east-1?** We observed 1d only; this
    changes over time and drives the ABS allow-list. **Resolve:**
    `aws ec2 describe-instance-type-offerings --location-type availability-zone
    --filters Name=instance-type,Values=g6e.2xlarge,g6e.4xlarge` on the admin
    host (this research sandbox had no AWS credentials).

13. **Billing policy for cache hits.** `usage_events.cached` exists but nothing
    sets it true. Free? Discounted? This is a product decision that blocks
    §11.4.

14. ~~**The SQS standard-vs-FIFO rate-code mapping**~~ **RESOLVED 2026-09-20,
    and the honesty note was itself backwards.** The metered unit map labels
    `Standard per Requests` $0.40/M and `FIFO first-in first-out per Requests`
    $0.50/M explicitly; the **$0.10/M** rate code is the one with no label, so
    the "fair queue" attribution is the unverified half. §9.2 now records the
    volume tiers too. Low stakes either way (SQS is not on the main path).

15. **A10G memory bandwidth** is not published by AWS and the A10G is not the
    same part as the NVIDIA A10. §13.4 leaves the cell blank rather than
    guess.

16. **Where does the "vLLM boot ≈ 2–3 min" number come from?** M6 cites
    `models/marlin2b/README.md`, which does not contain it, and neither does
    `results/notes.md`. It sets the dominant term of the §3.4 lag budget and
    therefore `DefaultInstanceWarmup`, `Retry-After` during scale-out, and the
    whole "queue matters more than the orchestrator" argument in §16.1.
    **Resolve:** time `serve.sh` from `docker run` to first 200 on the box,
    ten times, and record it in `results/notes.md` next to M2 in the same
    pass as open question 2.

17. **Which scale-in brake does the ASG actually get?** §3.4 previously
    specified a knob that does not exist on EC2 target tracking. Choose
    between a 900 s group `DefaultCooldown` and `DisableScaleIn: true` + a
    separate step policy, and write the choice into `asg.yaml` (§16.3 step 8).

---

## Sources

All fetched 2026-09-20 unless a publication date is noted.

**Elastic Load Balancing**
1. [Application Load Balancers — attributes, IP addressing, connections](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html) — `idle_timeout.timeout_seconds` default 60 s; `client_keep_alive.seconds` default 3600 s; ALB uses AWS-managed public IPv4
2. [Edit Application Load Balancer attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) — **idle timeout valid range 1–4000 s**; idle-timeout semantics; "send at least 1 byte"
3. [Quotas for Application Load Balancers](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-limits.html) — 1,000 targets/ALB; 15,000 reserved LCUs per ALB
4. [Edit target group attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html) — deregistration delay default 300 s; round robin / least outstanding requests / weighted random; slow start incompatible with LOR
5. [Network Load Balancers](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/network-load-balancers.html) — TCP idle 350 s default, 60–6000 s; **TLS listener fixed at 350 s**; UDP 120 s fixed
6. [Quotas for Network Load Balancers](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-limits.html)

**EC2 Auto Scaling**
7. [Warm pools](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html) — states, sizing, instance reuse policy, EBS-root requirement, mixed-instance and depletion limitations
8. [Target tracking scaling policies](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html) — instance warmup semantics; scale-in-to-0 with real-0 metrics
9. [Step and simple scaling policies](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-simple-step.html) — warmup, step adjustment semantics
10. [Predictive scaling](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-predictive-scaling.html) — fit for long-initialisation applications
11. [Attribute-based instance type selection](https://docs.aws.amazon.com/autoscaling/ec2/userguide/create-mixed-instances-group-attribute-based-instance-type-selection.html)
12. [Scaling cooldowns](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-scaling-cooldowns.html) — default 300 s
13. [Default instance warmup](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-default-instance-warmup.html)
14. [Lifecycle hooks](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html)
15. [Scaling policy based on Amazon SQS](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-using-sqs-queue.html) — **backlog-per-instance formula**, instance scale-in protection
16. [Target tracking with metric math](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-target-tracking-metric-math.html) — `e1 = m1/m2` with `GroupInServiceInstances`

**EC2 capacity and instances**
17. [On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html) — ASG eligibility, no billing discount, quota interaction, hibernation caveat, future-dated CR 32-vCPU floor and C/G/I/M/R/T/U/X families
18. [Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html) — **supported types are p/trn only**; 64 per block, 256 across blocks; 8-week horizon; no cancellation; 11:30 UTC end
19. [Hibernation prerequisites](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/hibernating-prerequisites.html) — **supported families exclude G and P**; Linux RAM < 150 GiB; encrypted EBS root required
20. [Accelerated computing instance types](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) — g5/g6/g6e GPU counts, vCPU, GPU memory
21. [Amazon EC2 On-Demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/) — via the pricing feed `b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US East (N. Virginia)/Linux/index.json`, published **2026-09-18T20:33:44Z**
22. [Amazon EC2 Spot Instance Advisor](https://aws.amazon.com/ec2/spot/instance-advisor/) — via `spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json`, fetched 2026-09-20

**SQS**
23. [Amazon SQS visibility timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) — 30 s default, 12 h maximum, ~120,000 in-flight, FIFO message-group semantics
24. [Amazon SQS pricing](https://aws.amazon.com/sqs/pricing/) — via `b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/queueservice/USD/current/queueservice.json`, published **2026-09-11T12:46:07Z**

**SageMaker AI**
25. [Asynchronous inference](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html) — 1 GB payload, 1 h processing, built-in queue, scale-to-zero, async-only endpoints
26. [Autoscale an asynchronous endpoint](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference-autoscale.html) — `ApproximateBacklogSizePerInstance`, `HasBacklogWithoutCapacity`, `MinCapacity=0`
27. [Monitor asynchronous endpoints](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference-monitor.html)
28. [Real-time inference](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints.html)
29. [Invoke endpoints with OpenAI-compatible APIs](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-openai-compatible.html) — `/openai/v1/chat/completions` with streaming; bearer tokens ≤ 12 h; `sagemaker:CallWithBearerToken`
30. [InvokeEndpointWithResponseStream](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_runtime_InvokeEndpointWithResponseStream.html)
31. [SageMaker AI endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/sagemaker.html) — `ml.g6e.* for endpoint usage` default **0**; 4 instances per endpoint; 4 across active endpoints

**API Gateway, ECS, EKS**
32. [Amazon API Gateway quotas](https://docs.aws.amazon.com/apigateway/latest/developerguide/limits.html)
33. [Quotas for an HTTP API](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-quotas.html) — **30 s max integration timeout, 10 MB payload, neither increasable**
34. [ECS task definitions for GPU workloads](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-gpu.html) — g6/g6e supported; GPU-optimized AMI; `ECS_ENABLE_GPU_SUPPORT`
35. [ECS Auto Scaling group capacity providers](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html) — managed scaling to 0; warm pool support and its caveat
36. [ECS service auto scaling](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-auto-scaling.html)
37. [Amazon EKS pricing](https://aws.amazon.com/eks/pricing/) — **$0.10/cluster/h** standard, $0.60/h extended; 14 months standard support
38. [Amazon ElastiCache pricing](https://aws.amazon.com/elasticache/pricing/) — ECPU definition; Valkey 33 % lower

**vLLM**
39. [vLLM production metrics](https://docs.vllm.ai/en/latest/usage/metrics.html) (page dated 2026-09-20) — `num_requests_waiting`, `num_requests_waiting_by_reason`, `kv_cache_usage_perc`, `request_queue_time_seconds`, `prefix_cache_hits/queries`, **`mm_cache_hits`/`mm_cache_queries`**, `num_preemptions`
40. [vLLM optimization and tuning](https://docs.vllm.ai/en/latest/configuration/optimization.html) — `mm_processor_cache_gb` default **4 GiB**, `mm_processor_cache_type="shm"`, `mm_encoder_tp_mode`, chunked prefill default and `max_num_batched_tokens` guidance, RECOMPUTE preemption
41. [vLLM multimodal inputs](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html) (page dated 2026-09-07) — `VLLM_VIDEO_LOADER_BACKEND`, **pynvvideocodec (NVDEC) + CUDA MPS + `--mm-ipc-gpu-memory-gb`**, DeepStream `pool_size`, TorchCodec `seek_mode`, `VLLM_MEDIA_LOADING_THREAD_COUNT` default 8, `--video-pruning-rate` / `--video-pruning-method` (evs, vidcom2), **`uuid` cached inputs**
42. [vLLM automatic prefix caching (design)](https://docs.vllm.ai/en/latest/design/prefix_caching.html)

**Kubernetes ecosystem**
43. [Karpenter NodePools](https://karpenter.sh/docs/concepts/nodepools/) — `consolidationPolicy`, `consolidateAfter`, `expireAfter`, `terminationGracePeriod`, disruption budgets and schedules
44. [Karpenter NodeClasses](https://karpenter.sh/docs/concepts/nodeclasses/) — **`capacityReservationSelectorTerms`** (beta, ODCR priority), `instanceStorePolicy: RAID0`
45. [KEDA ScaledObject specification](https://keda.sh/docs/2.17/reference/scaledobject-spec/) — `pollingInterval` 30 s, `cooldownPeriod` 300 s, `idleReplicaCount`, `fallback`
46. [KEDA Prometheus scaler](https://keda.sh/docs/2.17/scalers/prometheus/)
47. [llm-d architecture](https://llm-d.ai/docs/architecture) — Router (Proxy + Endpoint Picker via ext-proc), InferencePool, prefix-cache-aware routing, KV indexing, disaggregation, latency predictor
48. [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/)
49. [NVIDIA Dynamo documentation](https://docs.nvidia.com/dynamo/latest/) — frontend, KV router, Planner, cache manager; vLLM/SGLang/TensorRT-LLM backends

**Hardware**
50. [NVIDIA L40S](https://www.nvidia.com/en-us/data-center/l40s/) — 48 GB GDDR6 ECC, **864 GB/s**, 91.6 FP32 TFLOPS, **3× NVENC / 3× NVDEC**, no MIG, no NVLink
51. [NVIDIA L4](https://www.nvidia.com/en-us/data-center/l4/) — 24 GB, **300 GB/s**, BF16 242 TFLOPS (sparse), **2 NVENC / 4 NVDEC / 4 JPEG**, 72 W

**Serverless GPU providers**
52. [Modal pricing](https://modal.com/pricing) — L40S $0.000542/s, CPU $0.0000131/core/s, memory $0.00000222/GiB/s; plan GPU-concurrency caps
53. [Runpod pricing](https://www.runpod.io/pricing) (page "Updated September 13, 2026") — L40S $1.09/h, 94 GB RAM, 16 vCPU
54. [Runpod Serverless pricing](https://docs.runpod.io/serverless/pricing) — Flex vs Active workers, per-second billing, storage rates
55. [Baseten pricing](https://www.baseten.co/pricing/) — Dedicated Deployments per minute; T4/L4/A10G/A100/H100/B200; **no L40S listed**

**In-repo (measurements and current design)**
56. [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) — the 2026-09-19 L40S measurements this document reasons from
57. [`models/marlin2b/README.md`](../../models/marlin2b/README.md) — box layout, public endpoint, the "downloaded twice" TTFT observation
58. [`apps/infrx-api/README.md`](../../apps/infrx-api/README.md) and [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) — current auth, admission, usage design
59. [`apps/README.md`](../../apps/README.md) — console and API requirements, data model
60. [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) — the price source of truth these AWS rates are cross-checked against

---

## Verification log (2026-09-20)

Adversarial fact-check of the 25 most consequential claims. Every row was
checked against the primary source named, not against a secondary summary.
Verdicts: **CONFIRMED** (source says exactly this), **CORRECTED** (source
disagrees; the document has been edited), **UNVERIFIABLE** (no primary source
reachable; marked ⚠️ in place).

### Corrected

| # | claim as written | what the source says | where fixed |
|---|---|---|---|
| C1 | §13.3 "57–129 video-hours per GPU-hour → **$0.017–$0.039 per video-hour** … Consistent." | Units bug, ×3.6. 1.57–3.58 clips/s × 10.1 s = 15.9–36.2 video-s per wall-second; one GPU-hour = 3,600 wall-seconds = 57,100–130,200 video-**seconds** = **15.9–36.2 video-hours**. At $2.24208/h that is **$0.062–$0.141 per video-hour**. Recomputed with `python3`. | §13.3. **The error originates upstream in [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) "Cost sketch" ($0.02–0.04/video-hour), which is still wrong and must be recut.** |
| C2 | §14 "10 M … 23 (22 + 1) … 51,563 + ~50 = **~51,600**" | `23 × $2.24208 × 730 = $37,610`. $51,563 is 31.5 replicas. The 100 K and 1 M rows are arithmetically right ($1,635 = 1 replica, $4,906 = 3). | §14 table and prose |
| C3 | §14 "EKS premium … 0.14 % at 10 M"; "the NVDEC win … worth roughly **$29 K/month**" | Both were derived from C2's inflated base. $73 / $37,660 = **0.19 %**; $37,660 × (1 − 1/2.28) = **$21 K/month**. | §14 |
| C4 | §14 "(22 + 1)", "(2 + 1)" N+1 decompositions | 2 × 1.57 = 3.14 < 3.5 peak; 22 × 1.57 = 34.5 < 34.7. A true N+1 fleet is 4 replicas at 1 M and 24 at 10 M. The tabulated costs are for the *no-headroom* counts. | §14, flagged ⚠️ rather than silently re-tabulated |
| C5 | §3.4 "Use a **scale-in-specific cooldown of 900 s**" | EC2 Auto Scaling's `TargetTrackingConfiguration` accepts only `TargetValue`, `CustomizedMetricSpecification`, `PredefinedMetricSpecification`, `DisableScaleIn` — no cooldown of either direction [src](https://docs.aws.amazon.com/autoscaling/ec2/APIReference/API_TargetTrackingConfiguration.html). `ScaleInCooldown`/`ScaleOutCooldown` are *Application* Auto Scaling (what §5.1's SageMaker path uses). Target tracking falls back to the group's `DefaultCooldown` only when `DefaultInstanceWarmup` is null [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html). | §3.4, §16.3 step 8, new open question 17 |
| C6 | §9.2 / open question 14 "the standard-vs-FIFO assignment … was inferred; the $0.10/M fair-queue mapping is explicit" | Exactly inverted. The metered unit map (`queueservice.json`, `hawkFilePublicationDate` 2026-09-11T12:46:07Z) labels `Standard per Requests` = `0.0000004000`, `FIFO first-in first-out per Requests` = `0.0000005000` and `Free tier per Requests` = 0. The **$0.0000001000** row (rate code `2ZJX6F22XVYTFA8V…`) carries **no label**. Volume tiers omitted by the document: us-west-2 Standard Tier2 $0.30/M, Tier3 $0.24/M; FIFO Tier2 $0.40/M, Tier3 $0.35/M. | §9.2, open question 14 |
| C7 | §11.4 / open question 6 "per-ECPU and per-GB-hour rates did not render … a single `cache.t4g.micro` … is likely cheaper than serverless" | Both halves wrong. The rates are in the page's own metered unit map (`elasticache.json`, published 2026-09-14): Serverless **Valkey $0.084/GB-h, $0.0000000023/ECPU**; Redis/Memcached $0.125 and $0.0000000034 (confirming the "33 % lower" claim: 0.672 and 0.676). And `cache.t4g.micro` Valkey is **$0.0128/h = $9.34/month** against a **$6.13/month** 100 MB Serverless Valkey floor — serverless is the cheaper one. | §11.4, open question 6 |
| C8 | §3.4 lag budget "**total ≈ 5.5–8 min**" | Summing the document's own rows: 0+120+60+120+18+0 = **318 s = 5.3 min**; 60+120+90+180+18+60 = **528 s = 8.8 min**. | §3.4, §16.1 |
| C9 | M6 "vLLM boot ≈ 2–3 min incl. `torch.compile`", sourced to `models/marlin2b/README.md` | Not in that file, nor in `results/notes.md`. The only in-repo remark on compile time is `apps/infrx-api/openrouter/PLAN.md`: "keep `torch.compile` cache on NVMe so restarts take seconds". Unsourced estimate. | §1.2 M6 marked ⚠️; new open question 16 |
| C10 | §7 "RunPod's L40S at $1.09/h … **52 % cheaper**" | $1.09/h is Secure Cloud; **Community Cloud is $0.79/h** (page "Updated September 13, 2026"). 1 − 1.09/2.24208 = **51.4 %**. | §7 table and prose |
| C11 | §5.2 / open question 5 "the `ml.g6e.2xlarge` real-time hourly rate was not retrievable from a machine-readable AWS feed" | It is, from the Price List API offer file (not the `meteredUnitMaps` endpoint the document used elsewhere): `publicationDate` 2026-09-20T02:56:06Z, `USE1-Host:ml.g6e.2xlarge` **$2.8026/h** and `USE1-AsyncInf:ml.g6e.2xlarge` **$2.8026/h**, a **+25.0 %** premium over EC2's $2.24208. Matches [`08-cost-model-and-unit-economics.md`](08-cost-model-and-unit-economics.md) §1.3. | §5.2, open question 5 |
| C12 | Open question 7 "g6e Savings Plan / Reserved Instance rates. **Not retrieved.**" | Its sibling [`08-cost-model-and-unit-economics.md`](08-cost-model-and-unit-economics.md) §1.3–§1.4 already carries them — $1.41251/h (1y NU) and $0.84302/h (3y AU) against $2.24208 on demand. This document's §3.7 and §14 are on-demand-only and therefore overstate steady state by up to 62 %. | Open question 7, now a cross-reference and a recut instruction rather than a research gap |

### Confirmed

| claim | source checked |
|---|---|
| ALB idle timeout default **60 s**, valid range **1–4000 s**; the "send at least 1 byte" advice and the "no data being sent or received" definition, quoted verbatim | [edit-load-balancer-attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) |
| ALB `client_keep_alive.seconds` default **3600 s** (range 60–604800, cannot be turned off — an extra the document omits but does not misstate) | same |
| NLB TCP idle **350 s** default, **60–6000 s**; **TLS listener 350 s and "can't be modified"**; UDP **120 s**, "This cannot be changed" | [network-load-balancers](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/network-load-balancers.html) |
| API Gateway HTTP API: maximum integration timeout **30 seconds**, payload **10 MB**, **both "Can be increased: No"** | [http-api-quotas](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-quotas.html) |
| Deregistration delay default **300 s**; round robin default; "The least outstanding requests routing algorithm can not be used with slow start mode" | [edit-target-group-attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html) |
| ALB quotas: **15,000** reserved LCUs per ALB (adjustable), 1,000 targets per ALB | [load-balancer-limits](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-limits.html) |
| ASG default cooldown **300 s** (the page also opens by recommending against simple scaling policies + cooldowns) | [ec2-auto-scaling-scaling-cooldowns](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-scaling-cooldowns.html) |
| Instance warmup: "an instance is not counted toward the aggregated EC2 instance metrics"; scale-in to 0 requires "the group's minimum capacity must be set to 0" with real-0 metrics | [as-scaling-target-tracking](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html) |
| Backlog-per-instance formula and the "acceptable latency ÷ average time to process a message" definition, quoted correctly (the *queue attribute* is `ApproximateNumberOfMessages`; `ApproximateNumberOfMessagesVisible` is the CloudWatch metric — both readings are legitimate) | [as-using-sqs-queue](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-using-sqs-queue.html) |
| Warm pools: EBS-root requirement verbatim; "aren't supported with weighted mixed instance groups"; "don't support Spot Instances within mixed instance groups"; the depletion → cold-start paragraph | [ec2-auto-scaling-warm-pools](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html) |
| Hibernation supported families are general purpose / compute / memory / storage optimized **only — no G, no P**; Linux RAM "Must be less than 150 GiB"; encrypted EBS root | [hibernating-prerequisites](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/hibernating-prerequisites.html) |
| ODCR: "No billing discount"; "Active and unused Capacity Reservations count toward your On-Demand Instance limits"; Amazon EC2 Auto Scaling listed as an eligible managed-instance service; "Capacity Reservations do not ensure that a hibernated instance can resume"; future-dated CR **32 vCPU minimum** and families **C, G, I, M, R, T, U, and X** | [ec2-capacity-reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html) |
| Capacity Blocks: supported types are `p6-b300.48xlarge`, `p6-b200.48xlarge`, `p5.4xlarge`, `p5.48xlarge`, `p5e.48xlarge`, `p5en.48xlarge`, `p4d`, `p4de`, `trn1.32xlarge`, `trn2.3xlarge`, `trn2.48xlarge` + Trn2/P6e-GB200 UltraServers — **no G family**; 64 per block, 256 across; 8-week horizon; "Capacity Block cancellations aren't allowed"; end 11:30 UTC, termination from 11:00 UTC. Open question 11's premise also holds: `p5e.48xlarge` **is** listed for us-east-1 here while absent from the on-demand feed | [capacity-blocks-using](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html) |
| **The whole §13.1 price table, every row, to the digit** — re-pulled the feed (`hawkFilePublicationDate` **2026-09-18T20:33:44Z**, as stated): g6e.xlarge 1.8610, **2xlarge 2.24208**, 4xlarge 3.00424, 8xlarge 4.52856, 16xlarge 7.57719, 12xlarge 10.49264, 24xlarge 15.06559, 48xlarge 30.13118; g6.2xlarge 0.9776, 4xlarge 1.3232, 8xlarge 2.0144; g5.2xlarge 1.2120, 4xlarge 1.6240, 8xlarge 2.4480; p5.4xlarge 6.8800, p5.48xlarge 55.0400; **p5e.48xlarge absent**; p5en.48xlarge 63.2960; p6-b200.48xlarge 113.9328; p6-b300.48xlarge 142.4160. vCPU, RAM and NVMe columns match too. | [EC2 on-demand feed](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) |
| Spot table, exactly: g6e.2xlarge s=54 r=1 (5–10 %), g6e.4xlarge s=62 r=4 (**>20 %**), g6e.8xlarge s=62 r=3 (15–20 %), g6.2xlarge s=46 r=2 (10–15 %), g5.2xlarge s=62 r=3 (15–20 %), p5.48xlarge s=57 r=0 (<5 %). The derived $/h (1.03/1.14/1.72/0.53/0.46/23.67) all recompute. | [spot-advisor-data.json](https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json) |
| SQS visibility timeout: 30 s default, "maximum limit of 12 hours from when the message is first received. Extending the timeout doesn't reset this 12-hour limit"; "approximately 120,000 in-flight messages"; FIFO message-group in-flight semantics, quoted verbatim | [sqs-visibility-timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) |
| SageMaker async: "queues incoming requests", payload "up to 1GB", "up to one hour", "autoscaling the instance count to zero", and the disqualifier "the endpoint can only receive asynchronous invocations" — all verbatim | [async-inference](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html) |
| SageMaker async autoscaling: `ApproximateBacklogSizePerInstance` with `'TargetValue': 5.0`, `MinCapacity=0`, and the `HasBacklogWithoutCapacity` step policy whose alarm uses `EvaluationPeriods=2, Period=60` | [async-inference-autoscale](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference-autoscale.html) |
| SageMaker quotas: **every** `ml.g6e.* for endpoint usage` row reads "Each supported Region: 0", adjustable; "Maximum number of instances per endpoint" 4 and "Number of instances across active endpoints" 4, both adjustable. (A first pass with a summarising fetcher wrongly reported these rows absent; re-checked against the raw page.) | [gr/sagemaker](https://docs.aws.amazon.com/general/latest/gr/sagemaker.html) |
| SageMaker real-time OpenAI-compatible path `/openai/v1/chat/completions` "including streaming"; bearer tokens "valid up to 12 hours"; `sagemaker:CallWithBearerToken` + `sagemaker:InvokeEndpoint` | [realtime-endpoints-openai-compatible](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-openai-compatible.html) |
| EKS **$0.10 per cluster per hour** standard, **$0.60** extended; 14 months standard then 12 months extended | [eks/pricing](https://aws.amazon.com/eks/pricing/) |
| ECS GPU: "p2, p3, p4d, p5, g3, g4, g5, **g6, g6e**, and g6f instance types" | [ecs-gpu](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-gpu.html) |
| **All ten vLLM metric names** exist as printed, including `vllm:num_requests_waiting_by_reason` with label values `capacity` and `deferred`, `vllm:request_queue_time_seconds` (histogram), `vllm:mm_cache_hits` / `vllm:mm_cache_queries`, `vllm:num_preemptions` | [vllm metrics](https://docs.vllm.ai/en/latest/usage/metrics.html) |
| `mm_processor_cache_gb` **default 4 GiB**; `mm_processor_cache_type="shm"` shared-memory cache | [vllm optimization](https://docs.vllm.ai/en/latest/configuration/optimization.html) |
| vLLM video: `VLLM_VIDEO_LOADER_BACKEND`; PyNvVideoCodec "CUDA Multi-Process Service (MPS) is required"; positive `--mm-ipc-gpu-memory-gb`; `hw_decoders` default **2**; DeepStream `pool_size` "clamped to [1, 16]" defaulting to `VLLM_MEDIA_LOADING_THREAD_COUNT` (default **8**); TorchCodec `seek_mode` exact/approximate; `--video-pruning-rate` with `evs` (default) / `vidcom2`; the `uuid` cached-media field | [vllm multimodal inputs](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html) |
| KEDA defaults `pollingInterval: 30`, `cooldownPeriod: 300`, `initialCooldownPeriod: 0`, `minReplicaCount: 0`; `idleReplicaCount` exists (with the undocumented-here constraint that HPA supports only 0) | [KEDA ScaledObject spec](https://keda.sh/docs/2.17/reference/scaledobject-spec/) |
| Karpenter `capacityReservationSelectorTerms` is **Beta** and "will prioritize utilizing the capacity in these reservations before falling back to on-demand and spot"; `instanceStorePolicy: RAID0` sets allocatable ephemeral-storage to the instance-store total | [Karpenter NodeClasses](https://karpenter.sh/docs/concepts/nodeclasses/) |
| llm-d EPP "scores and selects model server pods based on real-time metrics, KV-cache affinity, and configured policies"; prefix-cache-aware routing; KV-cache indexing; the XGBoost Latency Predictor; GAIE-conformant proxy — all four quotes verbatim | [llm-d architecture](https://llm-d.ai/docs/architecture) |
| L40S **48 GB GDDR6 with ECC, 864 GB/s, 3× NVENC / 3× NVDEC (incl. AV1), 91.6 FP32 TFLOPS, MIG No, NVLink No** | [nvidia.com/l40s](https://www.nvidia.com/en-us/data-center/l40s/) |
| L4 **24 GB, 300 GB/s, 2 NVENC / 4 NVDEC / 4 JPEG, 72 W** | [nvidia.com/l4](https://www.nvidia.com/en-us/data-center/l4/) |
| Modal L40S **$0.000542/sec**, CPU **$0.0000131/core/sec**, memory **$0.00000222/GiB/sec**; Starter "100 containers + 10 GPU concurrency", Team "5000 containers + 50 GPU concurrency". The derived like-for-like `1.951 + 4×0.047 + 32×0.008 = $2.395/h` and "7 % more than $2.2421" both recompute (6.8 %). | [modal.com/pricing](https://modal.com/pricing) |
| Baseten Dedicated per-minute rates T4 $0.01052, L4 $0.01414, A10G $0.02012, A100 80 GB $0.06667, H100 $0.10833, B200 $0.16633; **L40S is not listed** | [baseten.co/pricing](https://www.baseten.co/pricing/) |
| ElastiCache "1 ECPU for each kilobyte (KB) of data transferred"; Valkey "33% lower pricing and 90% lower minimum data storage of 100 MB" | [elasticache/pricing](https://aws.amazon.com/elasticache/pricing/) |
| **In-repo measurements M1–M5 and M7.** M1 c=1 0.50 clips/s / TTFT 0.77 s and M2 c=8 1.57 clips/s / TTFT 3.35 s / **TPOT 8 ms** (§3.3's "a token every ~8 ms at c=8") are the README results table; M3 3.58 clips/s / 0.66 s and M4 1.57→1.47 (−6.4 %) are notes.md findings 7 and 5; M5's ~18 s is finding 3/4; M7's "3.8 s end to end (TTFT ~3.2 s) … downloading and decoding the 5.5 MB source twice" is README line 62 verbatim | [`models/marlin2b/README.md`](../../models/marlin2b/README.md), [`results/notes.md`](../../models/marlin2b/results/notes.md) |
| **In-repo code claims.** `MAX_INFLIGHT = 16` with `if inflight >= MAX_INFLIGHT: … status_code=429, headers={"Retry-After": "2"}` — the constant `Retry-After: 2` §10.3 complains about is real (gateway.py:244–246); `/health` proxies vLLM's `/health` and returns 200 the moment the engine answers, with no warm-up gate (gateway.py:216–222); `"cached": False` is hard-coded in the usage row (gateway.py:282); `MAX_VIDEO_MB` default 64; the 5.4 GB BF16 checkpoint matches METHODOLOGY §8 (5.444 GB) | [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) |
| Derived arithmetic re-run with `python3` and correct as printed: `30/0.637 ≈ 47` → 40 (§3.4); `MAX_QUEUE = 8 × 30 / 0.637 ≈ 376` and `30 × 1.57 ≈ 47` (§10.1); ODCR `2 × $2.2421 = $4.48/h = $3,270/month` and `$2.24 × 730 = $1,635` (§3.6/§3.7); every $/1,000-clips cell in both §13.3 tables ($0.397 / $0.266 / $0.351 / $0.588 and $0.174 / $0.233 / $0.204) and the −33 %/−12 %/+48 %, 2.28× and 35 % deltas; ALB base `$0.0225 × 730 ≈ $16` (§14); all six spot $/h; every $/GPU-h in §13.1 | — |

### Unverifiable (left ⚠️ in place, not removed)

| claim | why |
|---|---|
| "`RunInstances` → instance running (g6e.2xlarge) 60–90 s" (§3.4) | No AWS-published launch-latency figure exists for any instance type; the document already marks it ⚠️ and open question 2 names the right experiment. |
| vLLM boot 2–3 min (M6) | Now shown to have no in-repo source either — see C9 and new open question 16. |
| SageMaker `ml.g6e.*` **discounted** rates (Savings Plans for SageMaker) | Only the on-demand `USE1-Host` / `USE1-AsyncInf` rates are in the offer file; the SageMaker Savings Plans rate for `ml.g6e.2xlarge` is not. (The *on-demand* rate is now resolved — see C11 — and the *quota* claim is confirmed above.) |
| g6e Savings Plan hourly rates, **independently** | `https://pricing.us-east-1.amazonaws.com/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/current/us-east-1/index.json` returns 404, so this check could not reproduce doc 08's $1.41251 / $0.84302 from a primary feed. They are taken on doc 08's citation (see C12), not re-verified here. |
| Whether llm-d's EPP can score on a request-body `uuid` (§4.2, open question 8) | Requires reading the GAIE scorer plugin Go interface, not a doc page; the architecture page does not say. ⚠️ stands. |
| Whether CUDA MPS can be enabled in a SageMaker inference container (§5.2, open question 9) | No AWS documentation addresses MPS in inference containers either way. ⚠️ stands. |
| A10G memory bandwidth (§13.4, open question 15) | Confirmed *absent*: AWS publishes no bandwidth figure for A10G and the part is not the NVIDIA A10. Leaving the cell blank is the right call. |
| Modal memory-snapshot / RunPod FlashBoot behaviour for a `torch.compile`d vLLM container (§7) | Vendor claims only; nothing measured. ⚠️ stands. |

### Structural notes

- **A claim per section, checked:** every `[src]` URL in §3, §5, §9, §11, §12
  and §13 that carries a quoted string was opened; no quotation was found
  misquoted. The failures in this document are **arithmetic and provenance**,
  not misreading of sources — C1–C4 and C8 are all the author's own numbers,
  and C9 is a citation to a file that does not contain the claim.
- **Contradiction with [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md):**
  its "Cost sketch" still says "57–129 video-hours … $0.02–0.04 per
  video-hour". §13.3 here is now corrected and they disagree. The notes file
  is the older of the two and is wrong; recut it.
- **The two biggest structural gaps against the brief.** (a) **Every cost
  number in this document is on-demand.** The brief asks for infrastructure
  for real paying users, i.e. steady state, and the steady-state instrument —
  a g6e Instance Savings Plan at $1.41251 (1y NU) or $0.84302 (3y AU) — is
  named nowhere in §3.7, §13 or §14 even though the sibling doc 08 has the
  rates. The ODCR recommendation in §3.7 ("$1,635/month delta buys the
  guarantee") is priced against the wrong baseline, and ODCRs *stack* with
  Savings Plans ("You can combine Capacity Reservations with Savings Plans or
  Regional Reserved Instances to receive a discount"
  [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html)),
  so the real comparison was never made. (b) **R1 ("no dropped requests") has
  no answer for the instance-loss case.** §3 covers scale-*in* draining via
  deregistration delay, but nothing in the document says what happens to the
  8 in-flight requests on a target that fails its health check, is terminated
  by an ASG health-check replacement, or takes a spot interruption — the gap
  is visible in §10.3, whose table has no row for "target died mid-request".
  ASG instance scale-in protection is named in the §15 source list but never
  used in the text.
- **Consistency with [`../METHODOLOGY.md`](../METHODOLOGY.md):** this document
  is the only one in `research/` that costs a *clip-shaped* workload rather
  than tokens, so §6's `cost_per_1M_output_tokens` formula does not apply and
  the document does not pretend it does. Marlin-2B's 5.444 GB BF16 checkpoint
  and the 2 fps / ≤240 frames / 200,704 px-per-frame budget match §8's pinned
  model row exactly. No GPU in §8's table is an L40S, so §13.1/§13.4 are
  unpinned by construction — worth adding an L40S row to METHODOLOGY §8
  (48 GB, 864 GB/s, 91.6 dense FP32 TFLOPS) now that a production fleet
  depends on it.
