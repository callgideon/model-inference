# Reliability, observability and operations for the API

Research date: **2026-09-20**. Every price, quota, default and flag below was
read from a primary document on this date and is cited inline. Anything without
a citation is marked **⚠️ TO BE VERIFIED** with the reasoning stated.

## 0. Scope, and what this document is not

This is the operations half of taking `https://marlin2b.callbill.ai` from "one
box with one hand-made key" to "an API real users pay for". It covers **§1**
the metrics and alerts, **§2** health/readiness/deploy/rollback, **§3** failure
handling, **§4** security, **§5** eight runbooks, **§6** testing, **§7** paging.

It is deliberately AWS-shaped and **specific to the code in this repo**. Every
recommendation names the file that changes:

| component | file today | what it is |
|---|---|---|
| gateway | [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) | FastAPI, uvicorn `--workers 1` on `127.0.0.1:8001`, Supabase key auth (60 s cache), ffprobe video budget, `MAX_INFLIGHT=16` → 429, `usage_events` ingestion, `Inference-Id` |
| engine | [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh) | vLLM nightly in docker on `127.0.0.1:8000`, `--hf-overrides`, `--max-model-len 32768`, `--gpu-memory-utilization 0.90`, `--limit-mm-per-prompt '{"video":1,"image":4}'`, `--dtype bfloat16`. **`--max-num-seqs 32` is not in `serve.sh`** — `marlin2b-vllm.service` appends it on the `ExecStart` line (corrected 2026-09-20, verification log #30; [`03` §2.2](03-request-handling-and-queueing.md) carries the same correction) |
| units | [`apps/infrx-api/deploy/*.service`](../../apps/infrx-api/deploy/) | `marlin2b-vllm.service`, `marlin2b-gateway.service`, both `Restart=always` |
| TLS | [`apps/infrx-api/deploy/Caddyfile`](../../apps/infrx-api/deploy/Caddyfile) | Caddy in docker, host network, `flush_interval -1`, `read_timeout 600s` |
| install | [`apps/infrx-api/deploy/install.sh`](../../apps/infrx-api/deploy/install.sh) | idempotent root installer, reads SSM |
| console | `apps/app` + Supabase `fcbnscgsymzdykendbrc` | Vercel, `app.callbill.ai`; [`apps/README.md`](../../apps/README.md) §6 has the schema |
| host | one `g6e.2xlarge` (L40S 48 GB, 8 vCPU), `us-east-1d`, Elastic IP | **$2.24208/hr** on-demand, us-east-1, Linux [src](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) (read 2026-09-20) |

The measured behaviour this document plans around comes from
[`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md)
(2026-09-19, one L40S): 0.50 clips/s at c=1 with TTFT 0.77 s; 1.57 clips/s at
c=8 on a 1080p source vs **3.58 clips/s on a 360p source** — per-request video
download and decode on 8 vCPUs is the bottleneck, not the LM; the first request
with a new `mm_processor_kwargs` set pays ~18 s; vLLM boots in ~2–3 min
including `torch.compile` ⚠️ **TO BE VERIFIED** — the 2–3 min figure appears in
no repo file (`models/marlin2b/README.md`, `results/notes.md` and `bench.jsonl`
record no boot time); the only related datum is `TimeoutStartSec=900` in
`marlin2b-vllm.service`. Everything downstream that is sized against it
(`InstanceWarmup: 420` §2.5, `--health-check-grace-period 480` §2.6, the
lifecycle-hook `--heartbeat-timeout 900` §2.6, the 600 s boot loop in §6.4) is a
guess until open question 5 is measured.

**What this does not repeat.** The bare-metal-oriented tree already covers the
general theory and is the reference for anything not AWS-specific:

- SLO taxonomy, the Xid/SXid catalogue, NCCL-hang detection, the recovery
  ladder, the four observability planes, change management and benchmarking as
  a discipline → [`research/scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md).
- Queue shape, admission ladders, status codes, retries, idempotency keys,
  circuit breakers, video concurrency → [`research/scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md).
- Which signals lead and which lag when scaling → [`research/scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md).
- Cold-start anatomy and pre-warming → [`research/scaling/06-cold-start.md`](../scaling/06-cold-start.md).
- Caching, CUDA graphs, multimodal cost → [`research/cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md).
- The assembled platform → [`research/scaling/10-blueprint.md`](../scaling/10-blueprint.md).
- GPU-hour prices → [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) (source of truth).

Legend as in [`research/METHODOLOGY.md`](../METHODOLOGY.md): `[src]` = primary
document, `est.` = derived, `meas.` = measured, **⚠️ TO BE VERIFIED** = no
primary source.

---

## 1. SLO dashboard and alerts

### 1.1 Decide the promise before the dashboard

A dashboard without a promise is decoration. Three numbers, published in the
console's docs page (`apps/app/(console)/docs`), are what the alerts defend:

| SLO | target | window | measured where |
|---|---|---|---|
| **Availability** — non-5xx, non-429-after-queue share of authenticated requests | 99.5 % | 30 d rolling | gateway counter, `usage_events.status` |
| **TTFT** — first token for a ≤ 30 s clip from a URL | p95 ≤ 8 s ⚠️ **conflicts with [`01` §2.7](01-requirements-and-traffic-model.md), which publishes p95 ≤ 6 s** — noted 2026-09-20; one of the two must move before either is written into the docs page, and `01` owns the SLO | 30 d rolling | gateway `ttft_ms` |
| **Admitted-request completion** — a request the gateway accepted (200 or queued) eventually returns tokens | 99.9 % | 30 d rolling | gateway + queue DLQ depth |

The third is the one the user actually asked for ("no dropped requests"). It is
deliberately *separate* from availability: a request that waits 40 s in a bounded
queue and then succeeds is **not** an SLO violation; a request that the gateway
accepted and then lost **is**, and it is the only one that pages at burn rate 1.

TTFT ≤ 8 s at p95 is chosen against the measurement, not against ambition: TTFT
p50 at c=8 on a 1080p source is 3.35 s `meas.`, and the 18 s first-kwargs cost
is a cold-start artefact §2.6 removes. Do not promise the 0.77 s c=1 number.

### 1.2 Where the numbers come from

Four producers, one scrape path:

```
vLLM :8000/metrics ──┐
gateway :8001/metrics ┤
dcgm-exporter :9400 ──┼─▶ prometheus (agent mode) ─remote_write─▶ Grafana Cloud / AMP
node_exporter :9100 ──┘
ALB ─────────────────────▶ CloudWatch (AWS/ApplicationELB)
```

vLLM exposes Prometheus metrics on `/metrics` of the OpenAI-compatible server
[src](https://docs.vllm.ai/en/latest/usage/metrics.html). The names this
document uses — `vllm:num_requests_running`, `vllm:num_requests_waiting`,
`vllm:kv_cache_usage_perc`, `vllm:request_queue_time_seconds`,
`vllm:time_to_first_token_seconds`, `vllm:e2e_request_latency_seconds`,
`vllm:request_success`, `vllm:num_preemptions`, `vllm:mm_cache_hits`,
`vllm:mm_cache_queries`, `vllm:prefix_cache_hits`, `vllm:prefix_cache_queries`,
`vllm:iteration_tokens_total`, `vllm:corrupted_requests` — are all on that page
with their types [src](https://docs.vllm.ai/en/latest/usage/metrics.html).
Deprecated metrics are hidden one minor version after deprecation and removed
the version after, with `--show-hidden-metrics-for-version=X.Y` as the escape
hatch [src](https://docs.vllm.ai/en/latest/usage/metrics.html) — since
`serve.sh` pins `vllm/vllm-openai:nightly`, **a metric can vanish between two
`docker pull`s**. §6.4 makes that a gate, not a surprise.

**The gateway has no `/metrics` today.** That is the first change: add
`prometheus_client` and export the eight series in §1.3 marked *gateway*.
`gateway.py` already computes every one of them for `usage.jsonl`; the work is a
counter/histogram object, not new instrumentation.

DCGM exporter publishes GPU telemetry in Prometheus text format
[src](https://github.com/NVIDIA/dcgm-exporter) with the metric list at
[NVIDIA's reference](https://docs.nvidia.com/datacenter/dcgm/latest/reference/dcgm-exporter-metrics.html).

ALB metrics are CloudWatch-only, namespace `AWS/ApplicationELB`, emitted in
60-second intervals and **only when requests are flowing** — "if there are no
requests flowing through the load balancer or no data for a metric, the metric
is not reported" [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html).
That last clause matters: an alert of the form `HTTPCode_ELB_5XX_Count > 0`
never fires during a total outage where no request reaches the LB. Pair every
CloudWatch alert with a synthetic canary (§6.3), whose traffic guarantees the
metric exists.

### 1.3 The fifteen metrics, with thresholds

Fifteen is the budget. Everything else is a dashboard panel, not an alert.
"Page" means wake someone; "ticket" means it waits for the morning.

| # | metric | source | threshold | why this number |
|---|---|---|---|---|
| 1 | `slo:availability:ratio_rate1h` (gateway 5xx ÷ total, excluding queued-then-served) | gateway | burn rate > 14.4 for 1 h **and** > 14.4 for 5 m → **page** | Google SRE multiwindow: 2 % of a 30-day budget in 1 h, short window 1/12 of long [src](https://sre.google/workbook/alerting-on-slos/) |
| 2 | same, 6 h window | gateway | burn rate > 6 for 6 h and > 6 for 30 m → **page** | 5 % in 6 h [src](https://sre.google/workbook/alerting-on-slos/) |
| 3 | same, 3 d window | gateway | burn rate > 1 → **ticket** | 10 % in 3 d [src](https://sre.google/workbook/alerting-on-slos/) |
| 4 | `gateway_admitted_lost_total` (accepted, never answered: queue DLQ + crash-loss) | gateway + SQS `ApproximateNumberOfMessagesVisible` on the DLQ | **> 0 for 5 m → page** | this is the "no dropped requests" promise; there is no acceptable rate |
| 5 | `gateway_queue_depth` | gateway | > 0.8 × cap for 5 m → ticket; **= cap for 2 m → page** | at cap the gateway is shedding, i.e. dropping |
| 6 | `gateway_queue_wait_seconds` p95 | gateway | > 60 s for 10 m → ticket; > 180 s for 5 m → **page** | 180 s is where an ALB idle timeout of 300 s (§2.3) starts to bite |
| 7 | `vllm:num_requests_waiting` | vLLM | > 8 for 10 m → ticket | engine-side queue; > `max_num_seqs`/4 means the batch is saturated. `marlin2b-vllm.service` passes `--max-num-seqs 32` to `serve.sh` |
| 8 | `vllm:time_to_first_token_seconds` p95 | vLLM | > 8 s for 10 m → **page** | the published SLO |
| 9 | `histogram_quantile(0.95, gateway_ttft_seconds)` | gateway | > 8 s for 10 m → **page** | end-to-end incl. the gateway's own video download; the number the customer sees |
| 10 | `vllm:kv_cache_usage_perc` | vLLM | > 0.9 for 10 m → ticket | Marlin at 2 K prompt tokens should never approach this; if it does, a 240-frame clip mix arrived |
| 11 | `rate(vllm:num_preemptions[5m])` | vLLM | > 0 for 10 m → ticket | preemption storm precursor; see [`03` §5.1](../scaling/03-concurrency-and-admission-control.md) |
| 12 | `up{job=~"vllm\|gateway"}` and `HealthyHostCount` | Prometheus + CloudWatch | `HealthyHostCount` **Minimum** < desired for 2 datapoints → **page** | AWS's own recommendation is to alarm on the `Minimum` statistic of `UnHealthyHostCount`, non-zero over more than one datapoint [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html) |
| 13 | `DCGM_FI_DEV_XID_ERRORS` / `dmesg` Xid counter | DCGM | any value in the fatal set → **page** | the catalogue and which codes are fatal is in [`08` §2.2](../scaling/08-reliability-and-operations.md) and [NVIDIA Xid errors](https://docs.nvidia.com/deploy/xid-errors/) |
| 14 | `gateway_supabase_errors_total` / `gateway_auth_503_total` | gateway | > 0 for 5 m → ticket; > 10/min for 5 m → **page** | a 503 here means new customers cannot authenticate at all (§3.5) |
| 15 | `gateway_usage_spill_total` (rows written to `usage_failed.jsonl`) | gateway | > 0 for 15 m → ticket | silent revenue loss: the request served, the row never landed |

Two more that are **panels, not alerts**, because they are diagnostics rather
than symptoms: `vllm:mm_cache_hits / vllm:mm_cache_queries` (the multimodal
cache hit rate — note [`results/notes.md`](../../models/marlin2b/results/notes.md)
finding 5 warns the c=8 benchmark reused one clip, so the measured throughput is
optimistic on decode cost) and `DCGM_FI_DEV_GPU_UTIL`. GPU utilisation is the
classic wrong autoscaling signal ([`05` §1](../scaling/05-autoscaling-and-predictive-scaling.md));
it is also the wrong alerting signal.

### 1.4 The burn-rate rules, concretely

For a 99.5 % availability SLO the error budget is 0.005. Recording rules:

```yaml
# /etc/prometheus/rules/slo.yml
groups:
- name: infrx-slo
  interval: 30s
  rules:
  - record: slo:errors:ratio_rate5m
    expr: |
      sum(rate(gateway_requests_total{outcome="error"}[5m]))
        / sum(rate(gateway_requests_total[5m]))
  - record: slo:errors:ratio_rate1h
    expr: |
      sum(rate(gateway_requests_total{outcome="error"}[1h]))
        / sum(rate(gateway_requests_total[1h]))
  - record: slo:errors:ratio_rate30m
    expr: |
      sum(rate(gateway_requests_total{outcome="error"}[30m]))
        / sum(rate(gateway_requests_total[30m]))
  - record: slo:errors:ratio_rate6h
    expr: |
      sum(rate(gateway_requests_total{outcome="error"}[6h]))
        / sum(rate(gateway_requests_total[6h]))
  # corrected 2026-09-20: ErrorBudgetBurnTicket below alerted on
  # slo:errors:ratio_rate3d, which no rule recorded — the alert would have
  # evaluated to "no data" forever, which most configurations treat as
  # "not firing". The 6h short window is the SRE workbook's own pairing for
  # the 3-day ticket row (Table 5-8), not an invention here.
  - record: slo:errors:ratio_rate3d
    expr: |
      sum(rate(gateway_requests_total{outcome="error"}[3d]))
        / sum(rate(gateway_requests_total[3d]))

- name: infrx-slo-alerts
  rules:
  - alert: ErrorBudgetBurnFast          # 2 % of 30 d budget in 1 h
    expr: |
      slo:errors:ratio_rate1h  > (14.4 * 0.005)
      and
      slo:errors:ratio_rate5m  > (14.4 * 0.005)
    labels: {severity: page}
    annotations:
      summary: "burning 30-day error budget 14.4x; ~2% gone in the last hour"
      runbook: "research/production-api/07-reliability-observability-operations.md#51-overload"
  - alert: ErrorBudgetBurnSlow          # 5 % in 6 h
    expr: |
      slo:errors:ratio_rate6h  > (6 * 0.005)
      and
      slo:errors:ratio_rate30m > (6 * 0.005)
    labels: {severity: page}
  - alert: ErrorBudgetBurnTicket        # 10 % in 3 d
    expr: |
      slo:errors:ratio_rate3d > (1 * 0.005)
      and
      slo:errors:ratio_rate6h > (1 * 0.005)
    labels: {severity: ticket}
```

The `outcome` label is the load-bearing design decision. `gateway.py` must
classify each request into exactly one of:

| `outcome` | HTTP | counts against SLO? |
|---|---|---|
| `ok` | 200 | no |
| `queued_ok` | 200 after waiting | no |
| `client_error` | 400 (bad video, too long, two videos), 401 | no |
| `rate_limited` | 429 with `Retry-After`, **only when the queue is full** | yes (it is a drop) |
| `error` | 500/502/503 | yes |
| `lost` | accepted then never answered | yes, and metric #4 |

Counting 429s as errors is a choice with teeth: it makes "the queue was too
small" an SLO violation rather than a shrug. That is the correct incentive for
this system, whose stated goal is that requests queue instead of failing.

**Do not alert on 429 rate directly.** Alert #1 already covers it, and the
`Retry-After` semantics are in [`03` §3.5](../scaling/03-concurrency-and-admission-control.md).

### 1.5 Where the stack lives — and the decision rule

Three viable homes, priced 2026-09-20:

| option | cost basis | when it wins |
|---|---|---|
| **Grafana Cloud Free** | 10k active series, 14-day retention, community support, $0 [src](https://grafana.com/pricing/) | 1–4 GPU boxes. **Start here.** |
| **Grafana Cloud Pro** | $19/mo platform fee including 10k active series and 13-month retention, then **from** $6.50 per 1k series with automatic volume discounts [src](https://grafana.com/pricing/) — corrected 2026-09-20: the page publishes no 10k–100k band, only "starts at $6.50", so treat $6.50/1k as the *worst-case* rate ⚠️ the discount schedule is unpublished | > 4 boxes, or when 14-day retention stops covering a postmortem |
| **AMP + Amazon Managed Grafana** | AMP: tiered per-sample ingest + GB-month storage + **$0.10 per billion query samples processed**; native histogram buckets count 0.25 of a sample [src](https://aws.amazon.com/prometheus/pricing/). AMG: **$9 per active editor/admin, $5 per active viewer, per workspace per month**, minimum one editor [src](https://aws.amazon.com/grafana/pricing/) | when metrics must not leave the AWS account, or IAM/SSO is the requirement |

A fourth appeared in CloudWatch in 2026 and is worth knowing: CloudWatch now
ingests OpenTelemetry metrics at **$0.50/GB ingested** with 15 months of storage
included and no separate per-series charge, queryable with PromQL at **$0.01 per
million samples scanned**; console and dashboard queries are free
[src](https://aws.amazon.com/cloudwatch/pricing/). For a low-series, long-retention
workload that is cheaper than per-series pricing. It is the right answer only if
the fleet's series count grows faster than its data volume — not our case yet.

**Decision rule.** Ship on Grafana Cloud Free. Move when *either* the active
series count crosses ~8k *or* an incident needs data older than 14 days. Do not
start on AMP+AMG: the $9 minimum editor seat plus per-sample billing buys
nothing at one box, and `remote_write` makes the migration a one-line change.

**Series budget, so the free tier is not a surprise:** `est.`

| exporter | series per box | method |
|---|---|---|
| vLLM | ~600 `est.` | ~14 histograms × ~15 buckets + ~15 counters/gauges, ×1 model label |
| node_exporter | ~800 ⚠️ TO BE VERIFIED | community default is commonly quoted near this; no primary source found. Measure with `curl -s :9100/metrics \| grep -vc '^#'` before trusting it |
| dcgm-exporter | ~60 `est.` | default counter set × 1 GPU |
| gateway | ~120 `est.` | 8 series × label cardinality (status × stream × outcome), see §1.7 |

≈ **1.6k series per box** (600 + 800 + 60 + 120 = 1,580), so Free's 10k ceiling
holds **~6 boxes** `calc` (10,000 / 1,580 = 6.3) — corrected 2026-09-20 from
"~5 boxes". Plan the move at 5, because the headroom is one box, not two. The thing that destroys this
is a per-`api_key` or per-`org_id` label on a histogram. **Never put a tenant
identifier on a Prometheus label** — it belongs in `usage_events` (Postgres),
which the console already queries per org
([`apps/README.md`](../../apps/README.md) §6, `org_usage_summary`). The same
rule and its rationale: [`08` §4.7](../scaling/08-reliability-and-operations.md).

### 1.6 The wall dashboard

Four rows, in this order, because that is the order of an incident:

1. **Promise** — availability burn-down (30 d), TTFT p50/p95/p99, requests/min,
   admitted-but-lost (should be a flat zero line).
2. **Queue** — `gateway_queue_depth` and `gateway_queue_wait_seconds` p50/p95
   stacked with `vllm:num_requests_waiting` and `vllm:num_requests_running`;
   desired vs in-service instance count on the same time axis. One glance
   answers "is it the queue, the engine, or the fleet size".
3. **Engine** — `vllm:kv_cache_usage_perc`, preemptions/s, output tokens/s,
   `mm_cache` hit rate, `iteration_tokens_total` p50.
4. **Box** — GPU util/SM clocks/temperature/power (DCGM), CPU (the 8 vCPUs are
   the measured bottleneck), disk free on `/opt/dlami/nvme`, network out.

Row 4 exists only because of finding 7 in
[`results/notes.md`](../../models/marlin2b/results/notes.md): "Decode and resize
of 1080p source frames on 8 vCPUs, plus the base64 upload, cost more than 2K
tokens of prefill on the L40S." On this system CPU saturation *is* a latency
incident, and it is invisible from GPU metrics.

### 1.7 Structured logs, and the content rule

`gateway.py` already writes one JSON line per request to `USAGE_LOG` and one
`usage_events` row per authenticated request; the README states "Nothing about
the request body or response is stored." That property is a feature and must be
defended in §4.6. Three changes:

1. **Rotate.** `USAGE_LOG` is `/opt/dlami/nvme/logs/usage.jsonl`, appended
   forever, on the instance store. Add a `logrotate` drop-in (daily, `rotate 7`,
   `compress`, `copytruncate`) in `install.sh`. A full instance store takes down
   the weights directory too (§5.8).
2. **Stop opening the file on the event loop.** `log()` does a blocking
   `open()/write()` inside the request path of a single-worker uvicorn. Under
   c=16 that is 16 serialised syscalls per second competing with the video
   decode. Hand the line to the same background queue that already carries
   `usage_events`.
3. **Add `outcome` and `queue_wait_ms`** to both the JSONL line and the
   `usage_events` row (a migration in `apps/app/supabase/migrations/`), so the
   console's Usage page can show wait time and the SLO can be recomputed from
   Postgres when Prometheus retention has expired.

Log cardinality for Prometheus labels: `status` (5 values), `stream` (2),
`outcome` (6) → at most 60 label combinations per counter. That is the ~120
series in §1.5.

---

## 2. Health checks, readiness, drain, rollout, rollback

### 2.1 What vLLM's `/health` actually tests — and what it does not

This is the single most load-bearing fact in this section, so it is quoted from
source rather than summarised. `vllm/entrypoints/serve/instrumentator/health.py`
on `main`:

```python
@router.get("/health", response_class=Response)
async def health(raw_request: Request) -> Response:
    client = engine_client(raw_request)
    if client is None:
        return Response(status_code=200)     # render-only servers
    try:
        await client.check_health()
        return Response(status_code=200)
    except EngineDeadError:
        return Response(status_code=503)
```

[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/instrumentator/health.py)

So `/health` distinguishes exactly two states: **engine dead** and **everything
else**. It returns 200 when the engine is:

- still loading weights and compiling (the ~2–3 min boot in
  [`models/marlin2b/README.md`](../../models/marlin2b/README.md)) — ⚠️ TO BE
  VERIFIED whether the HTTP server binds before the engine is ready on the
  nightly image; the server is normally started after engine construction, so
  the practical failure is *connection refused*, not a 200. Test it (§6.2).
- saturated: 32 running, 40 waiting, TTFT 90 s.
- wedged in a way that does not raise `EngineDeadError` (a hung CUDA kernel, a
  deadlocked media-loading thread pool).

`/ping` is the SageMaker-compatible alias and `/load` reports server load
metrics for the generate routes; both are documented server endpoints
[src](https://docs.vllm.ai/en/latest/serving/online_serving/), and `/load` is
implemented in `basic.py` as a read of `app.state.server_load_metrics`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/instrumentator/basic.py).
⚠️ TO BE VERIFIED: the CLI flag that enables server-load tracking — the
middleware registry does not attach it unconditionally, so `/load` may return a
static zero unless a flag is passed. Check with `curl :8000/load` after a real
request before building anything on it.

**Conclusion: `/health` is a liveness probe, never a readiness probe.**

### 2.2 The three endpoints the gateway must expose

`gateway.py` today has one `/health` that proxies vLLM's and reports `inflight`.
Split it, because a load balancer, a deploy script and a human need different
answers:

| endpoint | question | implementation | consumer |
|---|---|---|---|
| `/healthz` | is this process alive? | return 200 unconditionally, no upstream call | systemd, `docker healthcheck` |
| `/readyz` | should the LB send me traffic? | 200 iff vLLM `/health` is 200 **and** the last successful generation was < 120 s ago or a warm probe has succeeded since boot **and** `queue_depth < cap` | ALB target group |
| `/warm` | can this box actually caption a clip? | run one real 4-second 360p clip through `/v1/chat/completions` end to end, cache the result 60 s | deploy gate, canary, weekly re-validation |

`/warm` is the answer to "vLLM `/health` vs a real warm request". It is the only
probe that exercises the parts that actually break on this system: ffprobe
present and executable, the `mm_processor_kwargs` path, the vision encoder, the
`<think>`-stripping regex, and the 8 vCPUs. Keep the probe clip **on the box**
(`/opt/dlami/nvme/samples/Big_Buck_Bunny_360_10s_1MB.mp4`, already there) and
pass it as a `data:` URL so the probe does not depend on the public internet.

Cost of `/warm`: at 3.58 clips/s for a 360p source `meas.`, one probe is ~0.3 s
of GPU. Once a minute that is 0.5 % of one GPU — acceptable. Once every 10 s is
not; use the 60 s cache.

`/readyz` returning **503 when the local queue is full** is what turns a
"busy box" into "route me elsewhere" at the ALB. Combined with
`target_group_health.unhealthy_state_routing.minimum_healthy_targets.count = 1`
(the default), if *every* box is full the ALB still sends traffic to all of them
rather than to none [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html) —
which is exactly right: a full queue should return a considered 429 with
`Retry-After`, not an ALB-generated 503 with no body.

### 2.3 ALB health-check settings for a 2–3 minute cold start

Defaults and ranges, all from
[src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html):
`HealthCheckIntervalSeconds` default 30, range 5–300; `HealthCheckTimeoutSeconds`
default 5, range 2–120; `HealthyThresholdCount` default 5, range 2–10;
`UnhealthyThresholdCount` default 2, range 2–10; `Matcher` default 200.

Two behaviours matter more than the numbers:

- **A newly registered target starts receiving traffic after one passing check**,
  "irrespective of the configured threshold"
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html).
  `HealthyThresholdCount` therefore does not protect a cold box; only `/readyz`
  being honest does.
- **If every target is unhealthy the ALB fails open** and routes to all of them
  regardless of health [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html).
  Plan for it: a box that fails `/readyz` must still return a *well-formed* 429
  or 503 with `Retry-After`, because it will be asked.

The settings:

```bash
aws elbv2 create-target-group \
  --name infrx-marlin2b --protocol HTTP --port 8001 --vpc-id vpc-... \
  --target-type instance \
  --health-check-path /readyz --health-check-interval-seconds 10 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 --unhealthy-threshold-count 3 \
  --matcher HttpCode=200

aws elbv2 modify-target-group-attributes --target-group-arn $TG --attributes \
  Key=deregistration_delay.timeout_seconds,Value=900 \
  Key=load_balancing.algorithm.type,Value=least_outstanding_requests \
  Key=slow_start.duration_seconds,Value=120
```

Reasoning per attribute:

- `deregistration_delay.timeout_seconds=900` — the default is 300 s, range
  0–3600 [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html).
  900 s covers a 120 s clip's generation plus a queue drain. The ALB waits for
  in-flight requests during `draining` [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-register-targets.html).
- `least_outstanding_requests` over the default `round_robin`
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html) —
  video requests have wildly unequal cost (a 10 s 360p clip vs a 120 s 1080p
  clip differ by more than 10× in CPU), so round-robin systematically overloads
  whichever box drew the long clips. This matches the per-model routing verdict
  for Marlin-2B in [`02` §8](../scaling/02-serving-stack-and-routing.md):
  least-outstanding-requests, because video prompts share no prefix.
- `slow_start.duration_seconds=120`, range 30–900, default 0/disabled
  [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html) —
  ramps a fresh box's share linearly, which absorbs the ~18 s first-kwargs
  penalty `meas.` instead of concentrating it on the first 16 unlucky requests.

**Load balancer attributes** (from
[src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html)):

```bash
aws elbv2 modify-load-balancer-attributes --load-balancer-arn $LB --attributes \
  Key=idle_timeout.timeout_seconds,Value=600 \
  Key=routing.http.drop_invalid_header_fields.enabled,Value=true \
  Key=routing.http.desync_mitigation_mode,Value=strictest \
  Key=access_logs.s3.enabled,Value=true \
  Key=access_logs.s3.bucket,Value=infrx-alb-logs \
  Key=waf.fail_open.enabled,Value=false \
  Key=deletion_protection.enabled,Value=true
```

⚠️ **The 600 s above is this document's third value for one setting**
(noted 2026-09-20): [`01` §F12](01-requirements-and-traffic-model.md) specifies
`idle_timeout.timeout_seconds` **180** plus a mandatory pre-timeout byte on
non-streaming requests, and [`02`](02-aws-architecture-options.md) specifies
**900**. The range is 1–4000 s. Pick one before writing any Terraform: 180 with
an enforced heartbeat is the safer reading, because a 600 s idle timeout also
means a wedged stream holds a connection for ten minutes.

`idle_timeout` defaults to **60 seconds** and that is fatal for this design: a
request that waits 90 s in the queue and then streams has no bytes on the wire
for 90 s, and the ALB closes it. Two defences, both needed — raise the idle
timeout to 600 s, *and* have the gateway emit an SSE comment heartbeat
(`: queued position=3 eta=42s\n\n`) every 15 s while a request is queued. The
heartbeat doubles as the honest wait estimate the user asked for, and it is
valid SSE that every OpenAI-compatible client ignores.

`waf.fail_open.enabled=false` is the default and should stay: if WAF is
unreachable, fail closed rather than let unfiltered traffic through
[src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html).

**Subnets:** each AZ subnet needs at least a `/27` and eight free IPs or the ALB
cannot scale, and a subnet that runs out of addresses while scaling makes the
ALB "run with insufficient capacity" — old nodes keep serving, but "the stalled
scaling attempt might cause 5xx errors or timeouts when attempting to establish
a connection" (the `active_impaired` state is defined as "routing traffic but
does not have the resources it needs to scale"; the doc names it explicitly only
for the Outpost case — precision corrected 2026-09-20)
[src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html).
Given §3.4's AZ constraint (g6e capacity exists only in `us-east-1d` today), the
ALB must still be created across ≥ 2 AZ subnets — enable the AZs, accept that
targets live in one.

**Target Optimizer — read before you reach for it.** ALB now supports a
sidecar agent that enforces a hard per-target concurrency: `TARGET_CONTROL_MAX_CONCURRENCY`,
0–1000, default 1, with the agent shipped at
`public.ecr.aws/aws-elb/target-optimizer/target-control-agent:latest`, enabled
only at target-group creation via a target control port that cannot be changed
afterwards [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-register-targets.html).
It looks like exactly what "queue at the LB instead of dropping" wants. It is
not: the metric `TargetControlRequestRejectCount` is documented as "Number of
requests **rejected** by ALB due to no targets being ready to receive requests"
[src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html).
The ALB holds requests only while some target has capacity; when none does, it
rejects. **Target Optimizer replaces the gateway's `MAX_INFLIGHT` 429, it does
not replace the durable queue.** Use it for what it is genuinely good at —
keeping `least_outstanding_requests` honest across boxes with different clip
mixes — and keep the queue in §3.6.

### 2.4 Drain: pods are easy, streams are not

The order that avoids 5xx, from AWS's own wording — "When shutting down an
application on a target you must first deregister the target from its target
group and allow time for existing connections to drain… This sequence prevents
users from experiencing 5XX errors"
[src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-register-targets.html):

```
1. /readyz starts returning 503            (gateway flips a flag)
2. ALB marks target unhealthy              (≤ 3 × 10 s = 30 s)
3. deregister-targets                      (state → draining)
4. gateway stops accepting from the queue, finishes in-flight
5. SIGTERM gateway  → uvicorn graceful, then SIGKILL after grace
6. SIGTERM vLLM container
7. terminate instance
```

Steps 4–6 need systemd changes in
[`marlin2b-gateway.service`](../../apps/infrx-api/deploy/marlin2b-gateway.service)
and [`marlin2b-vllm.service`](../../apps/infrx-api/deploy/marlin2b-vllm.service):

```ini
# marlin2b-gateway.service
[Service]
KillSignal=SIGTERM
TimeoutStopSec=930          # > deregistration_delay (900) + slack
ExecStop=/usr/bin/curl -fsS -XPOST http://127.0.0.1:8001/admin/drain || true
Restart=always
RestartSec=3
```

```ini
# marlin2b-vllm.service
[Service]
ExecStop=/usr/bin/docker stop -t 120 marlin2b-8000
TimeoutStopSec=180
```

`docker stop` defaults to a 10-second grace; a 120 s clip's generation does not
fit in 10 s. vLLM's own worker shutdown timeout is
`VLLM_WORKER_SHUTDOWN_TIMEOUT_SECONDS=5`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/envs.py),
which is the *worker* teardown after the server has stopped accepting, not a
request-drain budget — do not confuse the two.

The `Requires=`/`After=` relationship is also wrong today: the gateway unit has
`After=network-online.target marlin2b-vllm.service` but no `Requires=`, so a
`systemctl stop marlin2b-vllm` leaves the gateway up answering 502. Add
`BindsTo=marlin2b-vllm.service` only if you want them to die together; prefer
**not** to — the gateway surviving a vLLM restart is how queued requests
survive it too (§3.3). Leave the dependency as-is and let `/readyz` carry the
signal.

### 2.5 Rollout: immutable AMI + instance refresh

Two changes ship: **code** (this repo, pulled by `install.sh`) and **image**
(vLLM nightly tag, DLAMI, driver). They have different blast radii and deserve
different mechanisms.

**Code-only** (gateway change, Caddyfile, prompts): `install.sh` on the box,
gateway restart, ~3 s of 502 absorbed by the ALB retrying? No — ALBs do not
retry. Drain first (§2.4). For a single box that is a 30-second outage; for a
fleet it is zero. This is the argument for getting to two boxes before getting
to fancy deploys.

**Image change** (vLLM version, driver, DLAMI): build an AMI, then instance
refresh. Defaults, from
[src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/understand-instance-refresh-default-values.html):
`MinHealthyPercentage` 90 %, `MaxHealthyPercentage` 100 % (null), checkpoints
disabled, `CheckpointDelay` 3600 s, auto rollback **disabled**, bake time zero,
skip matching disabled on the CLI (enabled in the console), instance warmup
falls back to the default instance warmup or the health check grace period.

At a fleet of 2, `MinHealthyPercentage: 90` means "keep 2 in service while
replacing", which deadlocks unless `MaxHealthyPercentage` allows going over
desired. Set both explicitly:

```bash
aws autoscaling start-instance-refresh --auto-scaling-group-name infrx-marlin2b \
  --strategy Rolling \
  --desired-configuration '{"LaunchTemplate":{"LaunchTemplateId":"lt-...","Version":"$Latest"}}' \
  --preferences '{
    "MinHealthyPercentage": 100,
    "MaxHealthyPercentage": 200,
    "InstanceWarmup": 420,
    "CheckpointPercentages": [50, 100],
    "CheckpointDelay": 600,
    "AutoRollback": true,
    "BakeTime": 900,
    "SkipMatching": true,
    "AlarmSpecification": {"Alarms": ["infrx-5xx-high", "infrx-ttft-p95-high"]}
  }'
```

- `MinHealthyPercentage 100` + `MaxHealthyPercentage 200` = launch-before-terminate.
  With g6e capacity as scarce as §3.4 describes, this can *fail to launch*. That
  is the correct failure: it stops the refresh with the old fleet intact rather
  than terminating a box you cannot replace.
- `InstanceWarmup: 420` — 2–3 min vLLM boot `meas.`, plus weight staging, plus
  the first-kwargs 18 s, plus slack.
- `AutoRollback: true` with an `AlarmSpecification`: CloudWatch alarms can fail
  the operation and roll it back if they enter `ALARM`
  [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/understand-instance-refresh-default-values.html).
  Wire alerts #1 and #9 from §1.3 here. This is the cheapest safety net in the
  whole document: it turns "bad deploy" from a human runbook into an automatic
  one.
- `BakeTime: 900` so the refresh is not "complete" until 15 minutes of real
  traffic has run on the new AMI.
- `CheckpointPercentages: [50, 100]` with a 600 s delay gives a human the chance
  to look at the dashboard halfway through.

**Blue/green** (two target groups, weighted listener rule) is the alternative
and is better for anything that changes model *output* rather than plumbing —
a new vLLM version that might break the `--hf-overrides` remap, a new
`mm_processor_kwargs` budget, a Marlin weight update. Instance refresh replaces
in place and has no traffic-shaping knob; a weighted forward action does:

```bash
aws elbv2 modify-rule --rule-arn $RULE --actions '[{
  "Type":"forward","ForwardConfig":{"TargetGroups":[
    {"TargetGroupArn":"'$TG_BLUE'","Weight":95},
    {"TargetGroupArn":"'$TG_GREEN'","Weight":5}]}}]'
```

Decision rule: **plumbing → instance refresh with auto-rollback; anything that
can change tokens → blue/green at 5 % with the parity check from
[`models/marlin2b/README.md`](../../models/marlin2b/README.md) ("Parity between
the two is the first thing to check on a new engine version") run against the
green pool before the weight moves.**

### 2.6 The instance-store trap, and what it costs

`WEIGHTS_ROOT=/opt/dlami/nvme` — weights, samples and logs all live on the
**instance store**, which is erased on stop and on terminate. Three consequences
that are easy to get wrong:

1. **Warm pools cannot help as configured.** EC2 Auto Scaling can only put an
   instance into `Stopped` or `Hibernated` if its root device is EBS
   [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html)
   — true here, the DLAMI root is EBS — but the 5.4 GB of Marlin weights on
   `/opt/dlami/nvme` are gone when it stops. A warm-pool start would re-download
   from Hugging Face on every scale-out.
2. **The fix is to bake the weights into the AMI** (onto the EBS root or a
   snapshot-backed volume) and have user-data copy or symlink them onto the NVMe
   at boot, or serve them from the EBS copy directly. 5.4 GB from a warm EBS
   gp3 volume is far cheaper than an HF download, and it removes Hugging Face
   from the scale-out critical path entirely. Cold-start arithmetic for the
   general case: [`06` §8](../scaling/06-cold-start.md).
3. **Warm pool caveat:** "Amazon EC2 Auto Scaling stops or hibernates instances
   as they enter the warm pool, and does not wait for user data to finish
   running", which can interrupt long-running user data
   [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html).
   A warm pool without a launch lifecycle hook will stop the box mid-download.
   Lifecycle hooks default to a one-hour heartbeat timeout, with a global cap of
   48 hours or 100× the heartbeat, whichever is smaller
   [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html).

The warm-pool launch hook, completed by user-data once `/warm` passes:

```bash
aws autoscaling put-lifecycle-hook --auto-scaling-group-name infrx-marlin2b \
  --lifecycle-hook-name warmup --lifecycle-transition autoscaling:EC2_INSTANCE_LAUNCHING \
  --heartbeat-timeout 900 --default-result ABANDON
# at the end of user-data, after /warm returns 200:
aws autoscaling complete-lifecycle-action --lifecycle-hook-name warmup \
  --auto-scaling-group-name infrx-marlin2b --lifecycle-action-result CONTINUE \
  --instance-id "$(curl -sH "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/instance-id)"
```

`--default-result ABANDON` means a box that cannot warm up is terminated and
retried rather than silently joining the fleet. Termination hooks are
best-effort by contrast: "If a termination lifecycle hook times out, or is
abandoned, Amazon EC2 Auto Scaling proceeds with terminating the instance
immediately" [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) —
so the *drain* must be driven by the deregistration delay (§2.3), not by the
hook.

Health check grace period: console default 300 s, **CLI default 0**, where 0
disables it [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/health-check-grace-period.html).
An ASG created from the CLI with the default will kill every box during its
2–3 minute vLLM boot, forever. Set `--health-check-grace-period 480`.

### 2.7 Rollback

| what broke | rollback | time |
|---|---|---|
| gateway code | `git checkout <sha> && install.sh`, drain-restart | ~2 min |
| AMI / vLLM version | `aws autoscaling rollback-instance-refresh`, or re-refresh to the previous launch template version | one refresh cycle (~10 min/box) |
| automatic | `AutoRollback: true` + alarm spec (§2.5) | no human |
| Supabase migration | `supabase migration repair` + down migration; keep every migration additive so the old gateway still reads the new schema | minutes |
| prices | `models` table is read every 5 min by the gateway; correcting the row fixes new rows only — history is already stamped into `usage_events.cost_usd` by design ([`apps/README.md`](../../apps/README.md) §5) | 5 min |

The rule that makes all of these safe: **never ship a gateway change and a
schema change that depend on each other in the same deploy.** Expand the schema,
deploy the gateway, then contract — the gateway's Supabase reads are a narrow
`select id,org_id,revoked_at` and a narrow `insert`, so this costs nothing.

---

## 3. Failure handling

### 3.1 The table

Ranked by expected annual pain on *this* system, not by drama.

| # | failure | detection | blast radius | first action | automated? |
|---|---|---|---|---|---|
| 1 | vLLM OOM / crash-loop | `up{job="vllm"}==0`, systemd restart counter | whole box | `Restart=always` already; `/readyz` sheds | yes |
| 2 | CPU saturation from 1080p clips | TTFT p95 up, GPU util *down*, load avg > 8 | latency, all users | pre-transcode (§3.7), scale out | partly |
| 3 | vLLM hung but alive | no `vllm:iteration_tokens_total` increment while `num_requests_running > 0` | whole box, silent | watchdog restart (§3.3) | **needs building** |
| 4 | Supabase unreachable | `gateway_supabase_errors_total` | new keys only, if §3.5 is done | fail-open cached, fail-closed new | yes, partly |
| 5 | g6e capacity unavailable | ASG `Failed to launch` events, AWS Health | cannot scale or replace | ODCR / other region (§3.4) | no |
| 6 | GPU Xid | DCGM / `dmesg` | whole box | drain, reboot, validate, or replace | scriptable |
| 7 | poison video input | ffprobe timeout, 400 rate | one request, or one worker thread | hard limits (§3.7) | yes once built |
| 8 | AZ loss | `HealthyHostCount` → 0 in that AZ | total, today | see §3.4 | no |
| 9 | queue store loss | SQS/Redis metrics | queued requests | durability choice (§3.6) | design-time |
| 10 | cert expiry | canary TLS error, cert-expiry alert | total | §5.5 | mostly |

### 3.2 GPU and Xid errors

The catalogue, the fatal set, the ECC/row-remap trap and the
`nvidia-smi -q -d PAGE_RETIREMENT` checks are all in
[`08` §2.2–2.4](../scaling/08-reliability-and-operations.md) and are not
repeated. The upstream reference is
[NVIDIA Xid Errors](https://docs.nvidia.com/deploy/xid-errors/).

What changes on AWS with an ASG is the *recovery*, which is cheap here in a way
it is not on bare metal: **do not repair, replace.** One `g6e.2xlarge` is
$2.24208/hr [src](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json);
a GPU reset plus validation costs more engineer-minutes than the instance costs
dollars. The ladder collapses to:

```bash
# custom health check → ASG replaces the instance
aws autoscaling set-instance-health --instance-id i-... \
  --health-status Unhealthy --no-should-respect-grace-period
```

with a systemd timer that greps `dmesg` for `NVRM: Xid` and calls it. The one
exception is §3.4: when capacity is scarce, replacing is not guaranteed to
succeed, so **check that a replacement can launch before terminating the
incumbent** — which is precisely what `MinHealthyPercentage: 100` /
`MaxHealthyPercentage: 200` enforces during refreshes and what the custom health
check bypasses. Gate the automation on `DesiredCapacity == InServiceInstances`.

### 3.3 vLLM hang detection

`Restart=always` handles crashes. It does nothing for a hang, and vLLM's
`/health` will answer 200 throughout (§2.1). The engine's own timeouts, all from
[`vllm/envs.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/envs.py):

| env var | default | meaning |
|---|---|---|
| `VLLM_ENGINE_ITERATION_TIMEOUT_S` | 60 | per engine-step watchdog |
| `VLLM_ENGINE_READY_TIMEOUT_S` | 600 | startup |
| `VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS` | 300 | model execution |
| `VLLM_WORKER_SHUTDOWN_TIMEOUT_SECONDS` | 5 | worker teardown |
| `VLLM_VIDEO_FETCH_TIMEOUT` | 30 | vLLM's own video download |
| `VLLM_MEDIA_FETCH_MAX_RETRIES` | 3 | retries on that download |
| `VLLM_MEDIA_LOADING_THREAD_COUNT` | 8 | media decode pool — equals our vCPU count |

These cover engine-internal stalls, but not "the media loading thread pool is
wedged on eight slow URLs and nothing is being scheduled". Build the external
watchdog — it is ~25 lines and it is the difference between a 2-minute blip and
a 40-minute silent outage:

```bash
# /usr/local/bin/marlin2b-watchdog  (systemd timer, OnUnitActiveSec=30s)
#!/usr/bin/env bash
set -uo pipefail
m() { curl -fsS --max-time 5 http://127.0.0.1:8000/metrics | awk "/^$1 /{print \$2}"; }
running=$(m 'vllm:num_requests_running')
toks=$(m 'vllm:iteration_tokens_total_count')
state=/run/marlin2b-watchdog
prev_toks=$(cat $state 2>/dev/null || echo 0); echo "${toks:-0}" > $state

# stuck = work in the batch, but the engine has not completed a step since last check
if [ "${running:-0%.*}" != "0" ] && [ "${toks:-0}" = "$prev_toks" ]; then
  n=$(( $(cat ${state}.n 2>/dev/null || echo 0) + 1 )); echo $n > ${state}.n
  if [ "$n" -ge 4 ]; then           # 4 x 30 s = 2 min of no forward progress
    logger -t marlin2b-watchdog "no engine progress for 2m; restarting vLLM"
    curl -fsS -XPOST http://127.0.0.1:8001/admin/drain || true
    systemctl restart marlin2b-vllm
    rm -f ${state}.n
  fi
else
  rm -f ${state}.n
fi
```

Two minutes is chosen against the workload: the longest legitimate single
request is a 120 s clip at ≤ 2048 output tokens, and at a measured TPOT of
6–8 ms `meas.` that is ~16 s of generation. Two minutes of *zero engine steps*
while requests are running cannot be legitimate.

Pair it with the matching alert so the restart is visible, not silent:

```yaml
- alert: VLLMNoForwardProgress
  expr: |
    increase(vllm:iteration_tokens_total_count[3m]) == 0
    and vllm:num_requests_running > 0
  for: 2m
  labels: {severity: page}
```

`vllm:corrupted_requests` (NaNs in logits) is the adjacent silent failure
[src](https://docs.vllm.ai/en/latest/usage/metrics.html) — alert on any non-zero
rate; it usually means the GPU is sick, and the response is §3.2 not §3.3.

### 3.4 Node replacement and AZ loss — the binding constraint

Today: one instance, one Elastic IP, one AZ (`us-east-1d`), because g6e capacity
in us-east-1 was only available there — other AZs returned
`InsufficientInstanceCapacity`. This is not a nuisance, it is **the** reliability
constraint of the system. An AZ event today is a total outage with no recovery
path, because the replacement instance cannot be launched elsewhere.

Three mitigations, in order of what to do first:

1. **On-Demand Capacity Reservation in `us-east-1d`, now.** An ODCR reserves
   capacity in a specific AZ for any duration, can be created for immediate use
   with no term commitment, and can be cancelled at any time
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html).
   Reserve **N+1** — the extra slot is what makes launch-before-terminate (§2.5)
   and Xid replacement (§3.2) actually work. Billing note: a reservation is
   charged whether or not it is used
   [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html),
   so N+1 costs a continuous $2.24208/hr ≈ **$1,636/month** for the spare
   [`calc`]. That is the price of being able to replace a box. Use a *targeted*
   reservation (`InstanceMatchCriteria: targeted`) so unrelated launches cannot
   consume it.
2. **A second region or a second family as the declared fallback.** Prices for
   L40S-class capacity across providers are tabulated in
   [`cloud-pricing.md` §5](../cross-cutting/cloud-pricing.md); the fallback does
   not have to be AWS. Write the fallback into the runbook (§5.4) *before* the
   incident, including where the weights come from and what DNS change is
   needed.
3. **Move off the Elastic IP to an ALB across ≥ 2 AZ subnets** even while all
   targets sit in one. It costs nothing to enable the second AZ, and it means
   adding a box in a second AZ is a target registration rather than a DNS and
   TLS migration. It also removes the single-point Caddy/Let's Encrypt
   dependency (§5.5).

**Test it.** AWS FIS has actions for exactly this failure:
`aws:ec2:asg-insufficient-instance-capacity-error` and
`aws:ec2:api-insufficient-instance-capacity-error`, alongside
`aws:ec2:stop-instances`, `aws:ec2:terminate-instances` and
`aws:network:disrupt-connectivity`
[src](https://docs.aws.amazon.com/fis/latest/userguide/fis-actions-reference.html).
FIS experiments take CloudWatch-alarm stop conditions as guardrails
[src](https://docs.aws.amazon.com/fis/latest/userguide/what-is.html). §6.2 turns
this into a quarterly drill.

### 3.5 Supabase outage

The current behaviour, from
[`gateway.py`](../../apps/infrx-api/gateway.py) and its README, is already the
right shape and should be kept: cache hits 60 s, misses 10 s; a revoked key
stops working within a minute; if Supabase is unreachable, **keys already in the
cache keep working** (stale entries are served) and unknown keys get **503, not
401**, so the caller retries instead of rotating a key that is fine.

Supabase publishes a 99.9 % monthly uptime commitment **for Enterprise-tier
customers only** [src](https://supabase.com/sla), with third-party providers
(including AWS) explicitly excluded, and notes that project disks offer
99.8–99.9 % durability by default with PITR as the RPO improvement
[src](https://supabase.com/docs/guides/platform/going-into-prod). At our tier
there is no contractual uptime at all. Design accordingly — four gaps in the
current code:

1. **The cache never evicts.** `_keys` and `_last_used` are plain dicts. Any
   client can send unlimited random bearer tokens; each miss inserts an entry
   that lives 10 s logically but forever physically. That is a memory-exhaustion
   vector and it also means "cached keys keep working" degrades into "the
   process eventually OOMs". Fix: bound both with an LRU
   (`functools.lru_cache` is the wrong shape here because of the TTL — a small
   `OrderedDict` capped at, say, 10,000 entries with a purge on insert is the
   lazy correct version), and count evictions.
2. **Cache misses are on the critical path** with a 5 s Supabase timeout. A slow
   Supabase adds up to 5 s to TTFT for any key not seen in 60 s. Fix: keep the
   5 s timeout but serve a *stale* entry immediately and refresh in the
   background whenever one exists — extend the TTL semantics from
   "expire at 60 s" to "refresh at 60 s, serve stale until 24 h, hard-fail
   after".
3. **Nothing survives a restart.** A gateway restart during a Supabase outage
   empties the cache and every customer gets 503. Fix: persist the key cache to
   `/opt/dlami/nvme/keycache.json` on write and load it at boot, with entries
   older than 24 h ignored. This is ~15 lines and it converts "Supabase down +
   deploy" from a total outage into a non-event.
4. **The legacy key comparison is not constant-time.** `token == LEGACY_KEY` on
   a secret. Use `hmac.compare_digest`. (The Supabase path already hashes first,
   so it is fine.)

Set the staleness ceiling deliberately: **24 hours**. Longer and a revoked key
outlives a real security incident; shorter and a weekend Supabase outage takes
the API down. Write the number in the docs page so customers know the revocation
guarantee is "within 60 s normally, within 24 h worst case".

`usage_events` ingestion already degrades correctly — bounded queue of 10,000,
retries at 1/3/9 s, then spill to `usage_failed.jsonl`, replayed by
[`deploy/replay_usage.py`](../../apps/infrx-api/deploy/replay_usage.py). Two
additions: alert #15 on spill (§1.3), and make replay automatic via a systemd
timer rather than a documented manual command — an operator who has to remember
to replay will not.

### 3.6 Queue durability: SQS or Redis

The queue is what makes "no dropped requests" true, so its failure mode is the
whole point. The choice:

| | **SQS** | **ElastiCache (Redis/Valkey)** | **in-process (today)** |
|---|---|---|---|
| durability | replicated, managed; retention default 4 d, min 60 s, max 14 d [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.md) | snapshots + Multi-AZ failover; AOF is not the durability story on ElastiCache ⚠️ TO BE VERIFIED per engine version | none — a restart loses everything |
| in-flight semantics | visibility timeout, default 30 s, max **12 h from first receive**, extendable with `ChangeMessageVisibility` but the 12 h cap does not reset [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.md) | you build it | n/a |
| poison handling | DLQ via `maxReceiveCount`; DLQ retention must exceed the source queue's because the enqueue timestamp is preserved [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.md) | you build it | none |
| ordering | FIFO: 300 TPS per partition non-batched, 3,000 msg/s with batching; high-throughput mode up to 70,000 TPS in us-east-1 [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.md) | list ops, trivially ordered | trivially ordered |
| payload | max **1 MiB**; larger needs the S3-backed extended client [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.md) | RAM-bound | RAM-bound |
| ops burden | none | a cluster to patch, fail over, monitor | none |
| added latency | a poll | sub-ms | none |

**Recommendation: SQS standard, not FIFO, and do not put the video in it.**

- *Standard, not FIFO*, because fairness across customers matters and ordering
  does not — nobody cares whether their third clip is captioned before their
  fourth, and FIFO's 300 TPS/partition is a ceiling bought for nothing.
- *Not the video*: 1 MiB is below our 64 MB `MAX_VIDEO_MB`. Enqueue
  `{inference_id, org_id, api_key_id, s3_key_or_url, mm_kwargs, max_tokens,
  enqueued_at, reply_to}` — a few hundred bytes. The clip itself goes to S3
  (uploaded once by the gateway, which already downloads it) or stays as the
  customer's URL.
- *Visibility timeout 900 s* with a heartbeat: the worker extends it while
  generating. The 12-hour ceiling is irrelevant at our 120 s clip limit.
- *DLQ with `maxReceiveCount: 3`* and a **14-day** retention on the DLQ against
  4 days on the source, per the enqueue-timestamp rule above. Alert #4 is
  `ApproximateNumberOfMessagesVisible` on that DLQ.

Redis wins only if the queue must also hold *streaming state* — which is the
real complication. A queued streaming request needs the HTTP connection held
open (§2.3's idle timeout and heartbeat). SQS does not help there; the gateway
process that accepted the connection must be the one that serves it, or the
architecture must switch to **submit-then-poll** (`POST /v1/jobs` → 202 with an
id, `GET /v1/jobs/{id}` streams). Non-streaming callers get a queue for free;
streaming callers need one of: a held connection with heartbeats (simple,
caps concurrency at the gateway's file descriptors), or a job API (honest, but
it is a second API surface and the OpenAI clients do not speak it).

**Decision for now:** hold the connection, heartbeat every 15 s, cap the queue
at `4 × MAX_INFLIGHT` (64) per box with `Retry-After` computed from measured
service rate, and put SQS behind it only for the *durability* leg — i.e. write
the job to SQS at accept time and delete it on success, so a gateway crash
leaves a recoverable record rather than a silent loss. That gives metric #4 a
real denominator. The full queueing design belongs to the sibling document on
admission control in this directory; what this section owns is that **losing
the queue must be detectable**, and a DLQ plus alert #4 is how.

### 3.7 Poison inputs

The video path is the attack surface and the reliability surface at once. Five
concrete problems in `gateway.py` today, with fixes:

1. **Unbounded download before the size check.**
   ```python
   r = await c.get(url); r.raise_for_status(); data = r.content
   if len(data) > MAX_VIDEO_MB * 2**20: ...
   ```
   `r.content` buffers the *entire* body first. A URL that serves 10 GB fills
   RAM on a 64 GB box before the check runs. Fix — stream with a hard cap:
   ```python
   async with c.stream("GET", url) as r:
       r.raise_for_status()
       cl = int(r.headers.get("content-length") or 0)
       if cl > MAX_VIDEO_MB * 2**20:
           return None, f"video larger than {MAX_VIDEO_MB} MB"
       n = 0
       async for chunk in r.aiter_bytes(1 << 20):
           n += len(chunk)
           if n > MAX_VIDEO_MB * 2**20:
               return None, f"video larger than {MAX_VIDEO_MB} MB"
           f.write(chunk)
   ```
   `Content-Length` is a hint, not a guarantee — check both.
2. **No request body limit.** A `data:` URL is base64 inside the JSON body;
   FastAPI reads the whole body before the handler sees it. A 500 MB body is
   accepted, decoded, *then* rejected by `MAX_VIDEO_MB`. Fix: reject on
   `Content-Length` in middleware at `MAX_VIDEO_MB × 4/3 + 64 KB` ≈ 86 MB, and
   set the same ceiling at the ALB/WAF (§4.2).
3. **The gateway's validation is not binding on vLLM.** The gateway downloads
   the clip, probes it, computes `mm_processor_kwargs`, discards the file, and
   passes the *URL* to vLLM, which downloads it again — the measured "TTFT ~3.2 s,
   dominated by downloading and decoding the 5.5 MB source twice"
   [`models/marlin2b/README.md`](../../models/marlin2b/README.md). A server can
   return a 3-second clip to the gateway and a 10-minute clip to vLLM. The size,
   duration and SSRF checks are all TOCTOU. **Fix: the gateway must hand vLLM
   the bytes it validated**, either as a `data:` URL (simple, doubles the
   in-process memory) or by writing to a local path plus
   `--allowed-local-media-path` ⚠️ TO BE VERIFIED that this flag exists on the
   nightly image, or by re-uploading to a presigned S3 URL. This fix removes the
   duplicate download too, which is the single largest TTFT win available: it
   should take TTFT for a 10 s 1080p URL clip from ~3.2 s toward the ~0.77 s
   floor `est.`
4. **ffprobe on hostile input.** `probe_seconds` runs
   `ffprobe -v error -show_entries format=duration` with `timeout=30` in the
   default executor. That is correct as far as it goes, but the default
   `ThreadPoolExecutor` on 8 vCPUs has `min(32, 8+4) = 12` threads: 12
   concurrent hostile files and every subsequent probe — including healthy ones
   — queues behind them. Fix: a dedicated bounded executor of 4, `nice -n 10`,
   and an explicit `-analyzeduration`/`-probesize` cap. Reject on timeout with
   400, and count it (a spike in ffprobe timeouts is an attack signature).
5. **Pre-transcode to ≤ 480p.** Finding 7 of
   [`results/notes.md`](../../models/marlin2b/results/notes.md) says it plainly:
   360p gives 3.58 clips/s vs 1.57 for 1080p at the same token budget, and
   "pre-transcoding inputs to ≤480p (the model never sees more than 448×448
   anyway) is the first optimization to try". That is a **2.3× throughput
   change** for a workload where the CPU is the bottleneck — it belongs in this
   document because it is also the cheapest overload mitigation we have. Do it
   in the same ffmpeg pass that validates:
   ```bash
   ffmpeg -nostdin -v error -t 120 -i in.mp4 \
          -vf "fps=2,scale='min(854,iw)':-2" -an -c:v libx264 -preset veryfast \
          -f mp4 -movflags +faststart out.mp4
   ```
   `-t 120` enforces `MAX_VIDEO_SECONDS` in the decoder rather than trusting the
   container header, `-an` drops audio the model never sees, `fps=2` matches the
   model's training grid so vLLM has nothing to resample. ⚠️ TO BE VERIFIED that
   captions are unchanged after transcode — run the parity check from
   [`models/marlin2b/README.md`](../../models/marlin2b/README.md) on both
   sample clips before enabling it.

---

## 4. Security

### 4.1 SSRF: the gateway is a fetch-anything proxy

`{"video_url": {"url": "..."}}` makes every customer able to make the GPU box
issue an arbitrary HTTP GET. Today both fetchers follow redirects: the gateway
uses `httpx.AsyncClient(follow_redirects=True)`, and vLLM's
`VLLM_MEDIA_URL_ALLOW_REDIRECTS` defaults to `1`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/envs.py).
OWASP's guidance for the case where the target can be any external address is
an allowlist where possible, network-layer egress control, IMDSv2, and — stated
explicitly — "Disable the support for the following of the redirection in your
web client in order to prevent the bypass of the input validation"
[src](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).

Defence in depth, four layers, all of which we need because the first two can be
bypassed by DNS rebinding:

**(a) Application: resolve, validate, then connect to the resolved IP.**

```python
import ipaddress, socket
BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "224.0.0.0/4", "240.0.0.0/4",
    "::1/128", "fc00::/7", "fe80::/10", "::ffff:0:0/96", "fd00:ec2::/32")]

def safe_addrs(host, port):
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    addrs = {i[4][0] for i in infos}
    if not addrs:
        raise ValueError("no address")
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if any(ip in n for n in BLOCKED) or not ip.is_global:
            raise ValueError(f"blocked address {a}")
    return addrs     # connect to one of THESE, not to the hostname again
```

`fd00:ec2::/32` is there because IMDS is reachable over IPv6 at
`[fd00:ec2::254]` on Nitro instances in IPv6-enabled subnets
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.md) —
an IPv4-only blocklist misses it. Scheme allowlist: `https` and `http` only,
never `file:`, `gopher:`, `dict:`, `ftp:`.

**(b) Redirects off, or validated per hop.** Set
`follow_redirects=False` in the gateway and re-run `safe_addrs` on each `Location`
with a cap of 3 hops. Set `VLLM_MEDIA_URL_ALLOW_REDIRECTS=0` in
`marlin2b-vllm.service` — and note this only matters until §3.7 fix 3 stops vLLM
from fetching URLs at all, which is the real fix.

**(c) IMDSv2 required, hop limit 1.** IMDSv2's session-oriented `PUT`/`GET`
scheme is the documented defence against open reverse proxies and SSRF
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.md).
In the launch template:
```json
"MetadataOptions": {"HttpTokens": "required", "HttpPutResponseHopLimit": 1,
                    "HttpEndpoint": "enabled"}
```
Hop limit 1 also stops a containerised process from reaching IMDS through the
docker bridge.

**(d) Egress security group.** The box needs: 443 to Supabase, 443 to Hugging
Face (only if weights are not baked, §2.6), 443 to AWS endpoints, 443/80 for
ACME if Caddy stays, and 80/443 to the world for customer video URLs. That last
rule is unavoidable for arbitrary URLs — which is the strongest argument for
**requiring customers to upload to a presigned S3 URL instead of supplying their
own**. If the product can make that trade, the egress rule collapses to AWS
endpoints only and the entire SSRF class disappears. Raise it as a product
question (§Open questions).

### 4.2 Request size limits

Three ceilings, at three layers, all of which must agree:

| layer | limit | how |
|---|---|---|
| WAF | body > 86 MB → block | `SizeConstraintStatement` on `BODY` ⚠️ TO BE VERIFIED that WAF body inspection covers bodies of this size; WAF historically inspects only the first N KB, so treat this as a *coarse* guard and not the enforcement point |
| gateway middleware | `Content-Length` > 86 MB → 413 before reading | FastAPI middleware, ~8 lines |
| gateway video | 64 MB decoded, 120 s duration | `MAX_VIDEO_MB`, `MAX_VIDEO_SECONDS` (already present) |

86 MB = 64 MB × 4/3 (base64) + headroom. Also cap `max_tokens` server-side at
2048 so a customer cannot request 32,000 output tokens and hold a slot for ten
minutes, and reject `n > 1`. Corrected 2026-09-20: 2048 is **not** a model
ceiling documented in [`models/marlin2b/README.md`](../../models/marlin2b/README.md)
(that file names no `max_tokens` limit; its own curl example uses 512). 2048 is
the **vendor helper's default** `max_new_tokens` — `models/marlin2b/smoke.py`
`--max-tokens` default 2048, `models/marlin2b/reference.py` `--max-new-tokens`
default 2048, and `.caption()`'s `max_new_tokens=2048`
([`research/models/marlin2b/mi355x.md` §](../models/marlin2b/mi355x.md)). The
only *hard* ceiling on the box is `--max-model-len 32768` in `serve.sh`, which
a 2-minute clip's ~23.5K video tokens already mostly consumes. 2048 is therefore
a **policy** cap, chosen because it is what the model was tuned to emit — say so
in the docs page rather than claiming the model cannot exceed it.

### 4.3 Per-key rate limits

`MAX_INFLIGHT=16` is a *global* limit today: one customer's 16 concurrent
requests starve every other customer. That is the fairness failure described in
[`03` §4.7](../scaling/03-concurrency-and-admission-control.md). Minimum viable
fix, in `gateway.py`, keyed by `api_keys.id`:

- **Concurrency**: at most `max(2, MAX_INFLIGHT // 4)` in-flight per key.
- **Rate**: a token bucket per key, refill = the org's tier (a `limits` jsonb
  column already exists on `models`; add one on `organizations`).
- **Queue share**: at most 25 % of the queue per key, so one customer cannot
  fill it.
- Return **429 with `Retry-After`** and a body that names which limit was hit —
  `{"error":{"type":"rate_limit_error","message":"per-key concurrency limit
  (4); 3 queued","param":"concurrency"}}`. An opaque 429 generates a support
  ticket; a specific one generates a client-side fix.

At the edge, AWS WAF rate-based rules are the blunt instrument: evaluation
window of 60, 120, 300 or 600 seconds (default 300), minimum limit 10, with
aggregation on IP, a header, or custom keys, and an optional scope-down
statement [src](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based-high-level-settings.md).
Aggregate on the `Authorization` header (not IP — customers are behind NATs and
CI runners) at a limit far above any legitimate tier, e.g. 3,000 per 300 s. WAF
is the anti-abuse floor; the gateway's per-key buckets are the product limits.
Note the caveat in AWS's own docs: "AWS WAF applies rate limiting near the limit
that you set, but does not guarantee an exact limit match"
[src](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based-high-level-settings.md) —
never build billing or quota enforcement on it.

### 4.4 WAF and Shield

Shield Standard is "provided automatically and at no extra charge when you use
AWS" and covers L3/L4
[src](https://docs.aws.amazon.com/waf/latest/developerguide/ddos-overview.md);
Shield Advanced is a subscription. **Do not buy Shield Advanced yet** — at this
revenue the cost dominates the risk, and Standard plus the ALB's built-in
low-reputation packet handling (visible as `LowReputationPacketsDropped` and
`LowReputationRequestsDenied`
[src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html))
covers the realistic threat.

A minimal web ACL on the ALB:

```json
{"Name":"infrx-api","Scope":"REGIONAL","DefaultAction":{"Allow":{}},"Rules":[
 {"Name":"AWSManagedCommon","Priority":0,"OverrideAction":{"None":{}},
  "Statement":{"ManagedRuleGroupStatement":{"VendorName":"AWS","Name":"AWSManagedRulesCommonRuleSet",
    "ExcludedRules":[{"Name":"SizeRestrictions_BODY"},{"Name":"NoUserAgent_HEADER"}]}},
  "VisibilityConfig":{"SampledRequestsEnabled":true,"CloudWatchMetricsEnabled":true,"MetricName":"common"}},
 {"Name":"AWSManagedIPRep","Priority":1,"OverrideAction":{"None":{}},
  "Statement":{"ManagedRuleGroupStatement":{"VendorName":"AWS","Name":"AWSManagedRulesAmazonIpReputationList"}},
  "VisibilityConfig":{"SampledRequestsEnabled":true,"CloudWatchMetricsEnabled":true,"MetricName":"iprep"}},
 {"Name":"PerKeyRate","Priority":2,"Action":{"Block":{}},
  "Statement":{"RateBasedStatement":{"Limit":3000,"EvaluationWindowSec":300,
    "AggregateKeyType":"CUSTOM_KEYS","CustomKeys":[{"Header":{"Name":"authorization",
      "TextTransformations":[{"Priority":0,"Type":"NONE"}]}}]}},
  "VisibilityConfig":{"SampledRequestsEnabled":true,"CloudWatchMetricsEnabled":true,"MetricName":"perkey"}}]}
```

`SizeRestrictions_BODY` **must** be excluded or every legitimate `data:`-URL
request is blocked by the managed rule set's default body size limit. That
exclusion is why §4.2 puts the real size enforcement in the gateway.

### 4.5 Secrets and IAM

Today `install.sh` reads `/model-inference/supabase_service_role_key` and
`/model-inference/marlin2b_api_key` from SSM Parameter Store as SecureString and
writes them into `/etc/marlin2b-gateway.env` (mode 600, owner ubuntu). Two
observations:

1. **AWS's own guidance disagrees with the current choice for these two values.**
   Parameter Store's docs say to use `SecureString` for "configuration values
   that require encryption, such as service endpoints and account identifiers",
   and "For secrets such as database credentials, API keys, or tokens, we
   recommend AWS Secrets Manager, which provides purpose built security controls
   including automatic rotation and cross-region replication"
   [src](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.md).
   The Supabase service-role key bypasses RLS on every table; it is exactly the
   thing that should be rotatable on a schedule. Move `supabase_service_role_key`
   to Secrets Manager; leave `supabase_url` in Parameter Store (standard tier is
   free, 10,000 parameters, 4 KB values
   [src](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.md)).
2. **Drop `GATEWAY_API_KEY` entirely** once console keys exist, as the README
   already plans. A shared static key that produces no `usage_events` row is
   both an audit hole and a billing hole.

Instance role, least privilege — the whole policy:

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"ReadOwnConfig","Effect":"Allow","Action":["ssm:GetParameter","ssm:GetParameters"],
  "Resource":"arn:aws:ssm:us-east-1:<acct>:parameter/model-inference/*"},
 {"Sid":"ReadOwnSecrets","Effect":"Allow","Action":["secretsmanager:GetSecretValue"],
  "Resource":"arn:aws:secretsmanager:us-east-1:<acct>:secret:model-inference/supabase-service-role-*"},
 {"Sid":"DecryptThose","Effect":"Allow","Action":["kms:Decrypt"],
  "Resource":"arn:aws:kms:us-east-1:<acct>:key/<cmk>",
  "Condition":{"StringEquals":{"kms:ViaService":["ssm.us-east-1.amazonaws.com",
                                                 "secretsmanager.us-east-1.amazonaws.com"]}}},
 {"Sid":"QueueWork","Effect":"Allow",
  "Action":["sqs:SendMessage","sqs:ReceiveMessage","sqs:DeleteMessage",
            "sqs:ChangeMessageVisibility","sqs:GetQueueAttributes"],
  "Resource":"arn:aws:sqs:us-east-1:<acct>:infrx-jobs*"},
 {"Sid":"ClipStaging","Effect":"Allow","Action":["s3:PutObject","s3:GetObject"],
  "Resource":"arn:aws:s3:::infrx-clips/*"},
 {"Sid":"SelfHeal","Effect":"Allow","Action":["autoscaling:SetInstanceHealth"],
  "Resource":"*","Condition":{"StringEquals":{"autoscaling:ResourceTag/app":"infrx-marlin2b"}}},
 {"Sid":"Telemetry","Effect":"Allow",
  "Action":["cloudwatch:PutMetricData","logs:CreateLogStream","logs:PutLogEvents"],
  "Resource":"*"}]}
```

What is deliberately absent: `ec2:*`, `ssm:SendCommand` (the box is a target of
SSM, not an invoker), `s3:DeleteObject`, `autoscaling:TerminateInstance*`,
`secretsmanager:*` beyond one secret, and any `ssm:GetParametersByPath` on `/`.
A compromised gateway should be able to serve requests and mark itself
unhealthy — nothing else.

The current `install.sh` also runs `pip install -q fastapi uvicorn httpx` and
`apt-get install ffmpeg` at deploy time, which means a deploy can be broken by
an upstream package change and the box's software state is not reproducible.
Pin them (`pip install -r requirements.txt` with hashes) and, better, move them
into the AMI (§2.5) so `install.sh` only writes config and restarts units.

### 4.6 No content logging — keep it true

The property to defend: no prompt text, no video bytes, no completion text is
written anywhere. Checks:

- `gateway.py` logs `id, ts, video_seconds, stream, status, ttft_s, wall_s,
  prompt_tokens, completion_tokens`. Clean. **But** the `except` path logs
  `f"upstream error: {e}"` into the *response body* — an httpx exception can
  contain the full request URL, which is customer-controlled and may hold a
  signed S3 URL with credentials in the query string. Fix: log the exception
  type and a correlation id to the journal; return a generic message with the
  `Inference-Id`.
- `VLLM_DEBUG_LOG_API_SERVER_RESPONSE` must stay off; vLLM itself warns
  "CAUTION: Enabling log response in the API Server. This can include sensitive
  information and should be avoided in production"
  [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/middleware/register.py).
  Add an assertion to `install.sh` that the variable is unset.
- `VLLM_SERVER_DEV_MODE` must stay off — it registers cache/RLHF/RPC dev
  routers and logs "SECURITY WARNING: Development endpoints are enabled! This
  should NOT be used in production!"
  [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/__init__.py).
- **ALB access logs record the request line, not the body** — safe for JSON
  POSTs, but the `X-Forwarded-For` client IP is personal data. Set an S3
  lifecycle rule to expire the bucket at 30 days.
- **The temp file.** `video_seconds` writes to `tempfile.NamedTemporaryFile(...,
  delete=False)` and unlinks in a `finally`. If the process is `SIGKILL`ed
  mid-request the clip stays on disk in `/tmp`. Point it at a tmpfs or at a
  dedicated directory cleaned by `systemd-tmpfiles` with `Age=1h`.

---

## 5. Runbooks

Each is: **symptom → confirm in 60 s → act → verify → follow up.** Link each
alert's `runbook` annotation to the anchor.

### 5.1 Overload

**Symptom.** Alert #1 or #9. TTFT p95 > 8 s, 429 rate rising.

**Confirm (60 s).**
```bash
curl -s localhost:8001/metrics | grep -E 'gateway_(queue_depth|inflight)'
curl -s localhost:8000/metrics | grep -E 'vllm:num_requests_(running|waiting)|kv_cache_usage'
uptime; nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv
```
Read the pair: **GPU busy + queue deep** = genuinely out of GPU → scale out.
**GPU idle + load average ≥ 8 + queue deep** = the CPU/video path is the
bottleneck (the measured failure mode) → §3.7 fix 5, and scale out anyway
because more vCPUs come attached to more GPUs on this instance family.

**Act.** (1) `aws autoscaling set-desired-capacity --desired-capacity N+1`.
(2) If capacity is refused, go to §5.4. (3) Confirm the queue is *queueing*, not
shedding: `gateway_queue_depth < cap`. If it is at cap, raise the cap only if
`idle_timeout` (600 s) still covers the projected wait —
`wait ≈ depth / service_rate`, with service rate ~1.57 clips/s per box on 1080p
sources `meas.` (3.58 after transcode `meas.`).

**Verify.** TTFT p95 back under 8 s for 10 minutes; 429 rate zero.

**Follow up.** If the trigger was a single customer, §4.3's per-key limits were
too loose — tighten and tell them.

### 5.2 Queue growing

**Symptom.** Alert #5 or #6. Depth rising monotonically, no matching rise in
completions.

**Confirm.** Is anything completing? `rate(gateway_requests_total{outcome="ok"}[5m])`.
If **zero while depth rises**, this is not overload, it is a stall → §5.x below
and §3.3's watchdog should already have fired; check `journalctl -u
marlin2b-watchdog`.

**Act.** Depth rising *with* completions: scale out (§5.1). Depth rising
*without* completions: restart vLLM, then look for the poison input — the first
request in the queue at the moment it stopped draining is the suspect, and its
`Inference-Id` is in `usage.jsonl`.

**Verify.** Depth falls to zero within `depth / service_rate` seconds.

**Follow up.** If a single clip wedged the engine, add it (hashed, not stored) to
a reject list and file it as a §3.7 case.

### 5.3 Cold-start storm

**Symptom.** Several boxes launching at once after a scale-out or refresh; TTFT
spikes; `HealthyHostCount` oscillates.

**Confirm.** `aws autoscaling describe-scaling-activities --max-records 10`.

**Act.** (1) Check `slow_start.duration_seconds` is non-zero — without it a
fresh box takes an equal share immediately and pays the ~18 s first-kwargs cost
on customer traffic. (2) Check the launch lifecycle hook is completing only
after `/warm` passes (§2.6) — a box that joins before its first compile is a
box that serves 18-second TTFTs. (3) If weights are being downloaded from
Hugging Face at boot, that is the bug; bake them (§2.6).

**Verify.** New boxes reach `/warm` 200 within `InstanceWarmup` (420 s) and TTFT
does not spike when they join.

**Follow up.** Record actual boot-to-warm time. The cold-start budget in
[`06` §8](../scaling/06-cold-start.md) is entirely `est.` — **its own open
question 1 is that no model in this repo has a measured end-to-end cold start.**
This runbook is where that number finally gets measured; put it in
`results/notes.md`.

### 5.4 Capacity unavailable

**Symptom.** ASG scaling activity fails with `InsufficientInstanceCapacity`; or
an Xid replacement cannot launch.

**Confirm.**
```bash
aws autoscaling describe-scaling-activities --auto-scaling-group-name infrx-marlin2b \
  --max-records 5 --query 'Activities[].[StatusCode,StatusMessage]' --output text
aws ec2 describe-capacity-reservations --filters Name=state,Values=active
aws health describe-events --filter eventTypeCategories=issue,scheduledChange \
  --query 'events[?contains(service,`EC2`)]'
```

**Act, in order.** (1) Consume the ODCR (§3.4) — that is what it is for.
(2) Try adjacent sizes in the *same* family and AZ: `g6e.4xlarge` at
**$3.00424/hr** with 16 vCPU [src](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json)
is not just a fallback — it doubles the vCPUs per L40S, which is *the* measured
bottleneck, at 1.34× the price. A mixed-instances policy listing
`g6e.2xlarge, g6e.4xlarge, g6e.xlarge` makes this automatic. (3) Try another
region. (4) Tell customers: the queue is deeper and waits are longer, via the
`Retry-After` and the queued-SSE heartbeat, not via silence.

**Verify.** `DesiredCapacity == InServiceInstances`.

**Follow up.** This runbook firing means the ODCR was too small. Resize it.

### 5.5 Certificate expiry

**Symptom.** Canary fails with a TLS error, or the cert-expiry alert fires.

**Today's exposure.** Caddy obtains and renews certificates itself and "keeps
all managed certificates renewed"
[src](https://caddyserver.com/docs/automatic-https), but it needs ports 80 and
443 reachable and DNS pointing at the box, and — critically — its storage
(`caddy_data` docker volume) must survive. An instance replacement without that
volume re-issues from scratch, and Let's Encrypt rate limits "can block your
access to HTTPS for up to a week"
[src](https://caddyserver.com/docs/automatic-https). A scale-out storm of five
boxes each requesting a cert for the same name is the realistic way to hit that.

**Confirm.**
```bash
echo | openssl s_client -servername marlin2b.callbill.ai \
  -connect marlin2b.callbill.ai:443 2>/dev/null | openssl x509 -noout -dates
docker logs caddy --since 1h | grep -iE 'obtain|renew|error|rate'
```

**Act.** Short term: confirm 80/443 ingress on the `marlin2b-gateway` security
group and that `caddy_data` is a named volume (it is). **Permanent fix: move TLS
to the ALB with an ACM certificate** — ACM renews managed certificates
automatically and removes ACME, port 80, rate limits and per-box cert storage
from the design at once. This is the second-strongest reason to adopt the ALB,
after §3.4.

**Verify.** Canary green; `NotAfter` > 30 days out.

**Follow up.** Add a synthetic check on days-to-expiry with a 21-day ticket
threshold, so this never becomes a page.

### 5.6 Bad deploy

**Symptom.** Errors or TTFT spike within minutes of an instance refresh or an
`install.sh` run.

**Confirm.**
```bash
aws autoscaling describe-instance-refreshes --auto-scaling-group-name infrx-marlin2b \
  --max-records 1 --query 'InstanceRefreshes[0].[Status,PercentageComplete,StatusReason]'
journalctl -u marlin2b-gateway -u marlin2b-vllm --since "15 min ago" -p warning
```

**Act.** If `AutoRollback: true` and the alarm spec is wired (§2.5), this has
already started rolling back — confirm and stay out of the way. Otherwise:
`aws autoscaling rollback-instance-refresh --auto-scaling-group-name infrx-marlin2b`.
For a code-only change: `git checkout <last-good-sha> && sudo ./apps/infrx-api/deploy/install.sh`.

**Verify.** `/warm` 200 on every box; parity check against
`models/marlin2b/reference.py` output for both sample clips.

**Follow up.** A deploy that needed a human rollback means the alarm spec was
missing or its thresholds were too loose. Fix the spec, not the process.

### 5.7 Supabase down

**Symptom.** Alert #14. `gateway_auth_503_total` rising; existing customers
unaffected.

**Confirm.**
```bash
set -a; . /etc/marlin2b-gateway.env; set +a
curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" "$SUPABASE_URL/rest/v1/models?select=id&limit=1"
```
Cross-check [status.supabase.com](https://status.supabase.com/). Remember there
is no uptime commitment below Enterprise [src](https://supabase.com/sla).

**Act.** (1) Do nothing to the gateway — the fail-open cache is the plan.
(2) Confirm the spill file is growing rather than rows being lost:
`wc -l /opt/dlami/nvme/logs/usage_failed.jsonl`. (3) **Do not restart the
gateway** — that empties the key cache and turns a partial outage into a total
one (until §3.5 fix 3 ships). (4) If the console is also down, say so on the
status page.

**Verify.** On recovery: run `deploy/replay_usage.py`, confirm `usage_events`
count matches `usage.jsonl` for the window.

**Follow up.** Every hour of this runbook is an argument for §3.5's four fixes.

### 5.8 Cost spike

**Symptom.** A budget alarm, or `ConsumedLCUs`/instance-hours jumping.

**Confirm.** Which dimension?
```bash
aws ce get-cost-and-usage --time-period Start=$(date -d -7days +%F),End=$(date +%F) \
  --granularity DAILY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE
```
Then localise: EC2 hours (scale-out, or scale-in never happening), ALB LCUs (new
connections, active connections, processed bytes and rule evaluations are the
LCU dimensions [src](https://aws.amazon.com/elasticloadbalancing/pricing/)),
data transfer out (customers pulling from S3), CloudWatch/metrics ingest
(cardinality — see §1.5's tenant-label rule), or Synthetics canary runs (free
tier is 100 runs/month [src](https://aws.amazon.com/cloudwatch/pricing/), so a
1-minute canary is ~43,200 runs/month and is **not** free — budget it).

**Act.** Confirm scale-in is working: an ASG that scaled out at noon and is
still at 6 boxes at 3 a.m. is the usual cause. Check the scale-in cooldown and
that `/readyz` is not pinning boxes in service. Cross-check realised
$/video-hour against **$0.063–$0.143** — ⚠️ **do not use the $0.02–0.04 sketch
printed in [`results/notes.md`](../../models/marlin2b/results/notes.md)**: that
line converts video-seconds per GPU-hour by dividing by 1,000 instead of 3,600
and is low by **3.6×**. Recomputed 2026-09-20 and matching the sibling cost
document's independent recomputation
([`08` §1.2](08-cost-model-and-unit-economics.md)): 1.57 clips/s (1080p) and
3.58 clips/s (360p) × a 10 s clip = 15.7–35.8 video-seconds per wall-second,
× 3,600 = 56,520–128,880 video-seconds = **15.7–35.8 video-hours per GPU-hour**
(not 57–129), so at $2.24208/hr the honest range is $2.24208 / 35.8 =
**$0.0626** to $2.24208 / 15.7 = **$0.1428** per video-hour `calc`. That range
straddles the **$0.0945/video-hour** of revenue at current list prices
([`08` §1.1](08-cost-model-and-unit-economics.md)), so the sign of the margin is
decided by whether the §3.7 transcode ships — which makes this the most
consequential number in the runbook, not a footnote. A 10× gap against *this*
figure means the throughput assumption broke; a 3.6× gap just means someone is
still quoting `notes.md`, which still needs the same fix.

**Verify.** Daily cost back on trend.

**Follow up.** Set an AWS Budgets alert at 1.5× the trailing 7-day mean, and add
a revenue-vs-cost panel driven by `SUM(usage_events.cost_usd)` against
instance-hours — the only number that says whether the API is profitable.

---

## 6. Testing

### 6.1 Load tests

Two tools, two purposes.

**In-repo, for parity with what we already measured:**
[`models/marlin2b/bench.py`](../../models/marlin2b/bench.py) writes
`results/bench.jsonl` and produced every number in
[`results/notes.md`](../../models/marlin2b/results/notes.md). It stays the
source of truth for engine throughput because changing the harness invalidates
the comparison.

**End-to-end, through the ALB and the queue**, which `bench.py` does not test:
a small `k6` or `locust` script hitting `https://marlin2b.callbill.ai` with real
keys. What it must cover, and what the existing benchmark cannot:

| scenario | what it proves |
|---|---|
| ramp 1 → 64 concurrent, 10 s 1080p clips | where the queue starts filling, and that it queues rather than 429s |
| sustained at 1.2 × capacity for 15 min | that the queue is *bounded* and `Retry-After` is honest |
| mixed clip lengths (10 s / 60 s / 120 s) and resolutions (360p / 1080p) | head-of-line blocking; the existing benchmark used one clip repeatedly, which [`results/notes.md`](../../models/marlin2b/results/notes.md) finding 5 flags as optimistic on decode cost |
| 20 % of clients disconnecting mid-stream | that `inflight` is decremented and slots are reclaimed — see the double-decrement bug in §Implications |
| one key sending 100 concurrent | per-key fairness (§4.3) |
| streaming held through a 90 s queue wait | ALB idle timeout + SSE heartbeat (§2.3) |

The metric definitions differ between harnesses; reconcile before comparing, per
[`03` §7.4](../scaling/03-concurrency-and-admission-control.md). The fill-in
test-plan template is [`03` §7.5](../scaling/03-concurrency-and-admission-control.md) —
use it rather than inventing a format.

### 6.2 Chaos drills

Quarterly, in production, during business hours, announced. AWS FIS is the tool;
every action below is in the reference
[src](https://docs.aws.amazon.com/fis/latest/userguide/fis-actions-reference.html),
and every experiment gets a CloudWatch-alarm stop condition
[src](https://docs.aws.amazon.com/fis/latest/userguide/what-is.html).

| drill | FIS action | expected |
|---|---|---|
| kill a box | `aws:ec2:terminate-instances` | ALB drains, ASG replaces, zero lost requests (metric #4 stays 0) |
| reboot a box | `aws:ec2:reboot-instances` | same, faster |
| capacity refused | `aws:ec2:asg-insufficient-instance-capacity-error` | §5.4 runbook triggers; ODCR consumed; no cascading failure |
| API capacity refused | `aws:ec2:api-insufficient-instance-capacity-error` | launch-before-terminate refuses to terminate the incumbent |
| network partition | `aws:network:disrupt-connectivity` (to the Supabase endpoint's prefix) | fail-open cache holds; §5.7 |
| engine hang | `aws:ssm:send-command` sending `docker pause marlin2b-8000` | watchdog (§3.3) restarts within 2.5 min |
| queue store loss | purge the SQS queue manually (not FIS) | metric #4 fires; DLQ replay recovers |

The `docker pause` drill is the important one, because it is the only way to
test the failure that vLLM's own `/health` cannot see (§2.1).

### 6.3 Synthetic canaries, every minute

CloudWatch Synthetics canaries "can run as often as once per minute" and support
both cron and rate expressions, in Node.js, Python or Java
[src](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Synthetics_Canaries.md).
Two canaries:

1. **`infrx-api-canary`** — every minute, from a Lambda in `us-east-1`, does a
   real `POST /v1/chat/completions` with the on-box 360p sample as a `data:`
   URL, a dedicated canary API key, `max_tokens: 64`, and asserts: HTTP 200, an
   `Inference-Id` header present, a non-empty completion, no leading `<think>`,
   and end-to-end < 10 s. Cost: ~0.3 s of GPU per minute (§2.2) plus the
   canary's own run cost — the CloudWatch free tier includes 100 canary runs per
   month [src](https://aws.amazon.com/cloudwatch/pricing/), so a 1-minute canary
   is a real line item; budget it in §5.8.
2. **`infrx-tls-canary`** — every 15 minutes, TLS handshake plus days-to-expiry
   assertion (§5.5).

The canary is the only monitor that proves the *whole* path — DNS, TLS, ALB,
auth, ffprobe, vLLM, `<think>` stripping — and, per §1.2, it is what keeps
CloudWatch ALB metrics reporting during a traffic drought so the 5xx alarms can
fire at all.

Canary failures page on **3 consecutive failures**, not 1: a single Lambda cold
start plus an 8 s TTFT is a false positive waiting to happen.

### 6.4 Weekly capacity re-validation

Because `serve.sh` pins `vllm/vllm-openai:nightly`, the engine changes under us
continuously. Every Monday, automatically, on a canary instance *outside* the
serving ASG:

```bash
#!/usr/bin/env bash
set -euo pipefail
docker pull vllm/vllm-openai:nightly
DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' vllm/vllm-openai:nightly)
./models/marlin2b/serve.sh --max-num-seqs 32 &
timeout 600 bash -c 'until curl -fsS localhost:8000/health; do sleep 5; done'
# 1. parity — the check models/marlin2b/README.md names as "the first thing to check"
python models/marlin2b/reference.py --dump-prompts > /tmp/prompts.txt
python models/marlin2b/smoke.py /opt/dlami/nvme/samples/sample-10s.mp4 > /tmp/new.json
diff <(jq -r .events /tmp/baseline.json) <(jq -r .events /tmp/new.json)
# 2. throughput — must stay within 10% of the recorded row
python models/marlin2b/bench.py /opt/dlami/nvme/samples/sample-10s.mp4 -c 8 -n 32
# 3. metric names — an alert that silently stops matching is worse than a broken one
for m in vllm:num_requests_waiting vllm:kv_cache_usage_perc \
         vllm:time_to_first_token_seconds_bucket vllm:iteration_tokens_total_count \
         vllm:mm_cache_hits vllm:num_preemptions; do
  curl -fsS localhost:8000/metrics | grep -q "^$m" || { echo "MISSING METRIC $m"; exit 1; }
done
echo "$DIGEST validated $(date -Is)" >> models/marlin2b/results/validated-digests.txt
```

Step 3 is the one people forget. vLLM's deprecation policy hides a metric one
minor version after deprecation
[src](https://docs.vllm.ai/en/latest/usage/metrics.html); an alert whose PromQL
matches nothing evaluates to "no data", and most alerting configurations treat
"no data" as "not firing". **Pin `serve.sh` to the validated digest** rather
than `:nightly` and promote digests through this gate — that single change
converts an entire class of silent outages into a Monday-morning ticket.

---

## 7. On-call and paging, minimally

Two people, no rotation software, no incident-management platform. What is
actually needed:

| piece | choice | why |
|---|---|---|
| alert evaluation | Grafana Cloud alerting (included in Free) + CloudWatch alarms for ALB/ASG | two evaluators is fine; each owns the metrics it can see |
| routing | Grafana contact point → one shared email + one webhook | the webhook is the escalation path |
| paging | a phone. ⚠️ TO BE VERIFIED which provider — Grafana OnCall's status has changed and PagerDuty's free tier terms change; check both before committing | the only requirement is that a `severity: page` label reaches a phone that rings when it is on silent |
| chat | one `#infrx-alerts` channel, alerts only, no chatter | a channel with noise is a channel nobody reads |
| status page | a static page on Vercel next to `app.callbill.ai`, updated by hand | customers need somewhere to look during §5.4 and §5.7 |
| escalation | page → if unacked in 10 min → the other person | two people is a rotation |

**The two rules that matter more than the tooling:**

1. **Every paging alert has a runbook link in its annotation**, pointing at an
   anchor in §5. An alert without a runbook is a request that someone reinvent
   the response at 3 a.m.
2. **A page that fires and needs no action gets deleted or demoted that week.**
   Fifteen metrics (§1.3) is the budget precisely so that this stays possible.
   The multiwindow burn-rate design exists to make pages rare and real
   [src](https://sre.google/workbook/alerting-on-slos/); adding a
   "GPU utilisation > 90 %" page undoes all of it.

What is paged versus ticketed, restated as a single rule: **page only when a
customer is currently losing requests or waiting past the SLO, or when a request
we accepted has been lost.** Everything else — cache hit rate, preemptions, KV
pressure, spilled usage rows, cost — is a ticket. Metric #4
(`admitted_lost > 0`) is the one exception to any noise-reduction instinct: it
is the promise, and it pages at any non-zero value.

---

## Implications for our system

In the order they should be done. The first four are the ones that make the
current single box honest; the rest make the fleet possible.

1. **Fix the four bugs in `gateway.py` that break the concurrency limit and the
   input validation.** These are not hardening, they are correctness:
   - `inflight` can be **double-decremented**. In the non-streaming path,
     `inflight -= 1` runs immediately after `await client.post(...)`; if the
     following `r.json()` or `log()` raises, control reaches the outer `except`,
     which decrements again. `inflight` drifts negative and `MAX_INFLIGHT` stops
     being a limit — the box accepts unbounded concurrency until it dies. Fix
     with a single `try/finally`, or better, `async with a semaphore`.
   - `video_seconds` buffers the **entire** response body before checking the
     size (§3.7 fix 1).
   - The `_keys` / `_last_used` caches are **unbounded dicts** keyed by
     attacker-controlled hashes (§3.5 fix 1).
   - The `except` handler returns the upstream exception string to the client,
     which can contain a customer-supplied URL with credentials (§4.6).
2. **Make the gateway observable.** Add `/metrics` with the eight gateway series
   in §1.3, split `/health` into `/healthz` `/readyz` `/warm` (§2.2), add
   `outcome` and `queue_wait_ms` to the JSONL line and to `usage_events` (a
   migration in `apps/app/supabase/migrations/`), rotate `usage.jsonl`, and move
   the log write off the event loop (§1.7).
3. **Stop downloading the video twice** (§3.7 fix 3). It is simultaneously the
   largest TTFT win available (~3.2 s → toward 0.77 s `est.`), the fix that makes
   the size/duration/SSRF checks actually binding, and a prerequisite for the
   transcode in fix 5. Changes `gateway.py` and possibly
   `marlin2b-vllm.service` (an `--allowed-local-media-path` or a data-URL hand-off).
4. **Transcode to ≤ 480p / 2 fps in the validation pass** (§3.7 fix 5). Measured
   headroom: 1.57 → 3.58 clips/s `meas.` on the same token budget. Gate it on
   the parity check in `models/marlin2b/README.md`.
5. **Buy the reliability floor that does not require new code**: an N+1 targeted
   ODCR in `us-east-1d` (§3.4), the vLLM watchdog timer (§3.3), a `logrotate`
   drop-in, and a systemd timer that replays `usage_failed.jsonl` (§3.5).
6. **Add the ALB** across ≥ 2 AZ subnets with ACM TLS, `/readyz` health checks
   at 10 s, `deregistration_delay=900`, `least_outstanding_requests`,
   `slow_start=120`, `idle_timeout=600`, access logs on, WAF attached
   (§2.3, §4.4). This retires Caddy, retires the Let's Encrypt rate-limit risk
   (§5.5), and turns "add a second box" into a target registration. New files:
   `apps/infrx-api/deploy/alb.tf` or a documented CLI sequence beside
   `install.sh`.
7. **Bake the AMI** — weights, pinned Python deps, ffmpeg, the vLLM image at a
   validated digest, exporters — and move `install.sh` to config-and-restart
   only (§2.5, §2.6, §4.5, §6.4). Instance-store weights are the reason warm
   pools cannot help today.
8. **ASG with a launch lifecycle hook gated on `/warm`**, `--health-check-grace-period 480`,
   and instance refresh with `AutoRollback` wired to the TTFT and 5xx alarms
   (§2.5, §2.6). A mixed-instances policy listing `g6e.4xlarge` is both a
   capacity fallback and a throughput upgrade (§5.4).
9. **Per-key concurrency, rate and queue-share limits** in `gateway.py`, with
   the limits stored on `organizations` and surfaced on the console's docs page
   (§4.3). `MAX_INFLIGHT` as a single global number is a fairness bug the moment
   there are two customers.
10. **SQS job record + DLQ** behind the in-process queue, so metric #4 has a
    denominator and a gateway crash is recoverable rather than silent (§3.6).
11. **Supabase resilience**: stale-while-revalidate key cache with a 24-hour
    ceiling, persisted across restarts, plus `hmac.compare_digest` and the LRU
    bound (§3.5). Move the service-role key to Secrets Manager (§4.5).
12. **SSRF hardening**: resolve-then-connect with the blocklist including
    `fd00:ec2::/32`, redirects off in both fetchers, IMDSv2 required with hop
    limit 1, and the least-privilege instance role in §4.5.
13. **Canary every minute, chaos quarterly, capacity re-validation weekly**
    (§6.2–6.4), with `serve.sh` pinned to a validated digest instead of
    `:nightly`.
14. **The observability stack**: Prometheus in agent mode on each box,
    `remote_write` to Grafana Cloud Free, the four-row dashboard in §1.6, the
    fifteen alerts in §1.3, and the burn-rate rules in §1.4.

## Open questions

Consolidated. Each is load-bearing for something above.

1. ⚠️ **Does vLLM's HTTP server bind before the engine is ready** on the nightly
   image, and does `/health` return 200 during weight load and `torch.compile`?
   §2.1 assumes connection-refused rather than a premature 200; if it is a 200,
   `/readyz` must add an explicit "have I ever completed a generation" gate
   before any box joins an ALB. One test: start `serve.sh` and poll
   `:8000/health` from t=0.
2. ⚠️ **Which flag enables server-load tracking for `/load`?** The endpoint is
   documented and implemented as a read of `app.state.server_load_metrics`, but
   the middleware registry does not attach the tracker unconditionally. Until
   answered, `/readyz` must not depend on `/load`.
3. ⚠️ **Does `--allowed-local-media-path` (or an equivalent) exist on the pinned
   nightly**, and does it accept a `file://` video URL? This decides whether
   Implication 3 is a local path hand-off or a `data:` URL (which doubles
   in-process memory for a 64 MB clip).
4. ⚠️ **Does the ≤ 480p / 2 fps transcode change captions or grounding output?**
   The model never sees more than 448×448, so it should not, but the parity
   check has not been run. This gates a 2.3× throughput change.
5. ⚠️ **What is the real end-to-end cold start** (instance launch → `/warm`
   200), with weights baked into the AMI? Every figure in
   [`06` §8](../scaling/06-cold-start.md) is `est.`, and that document's own
   open question 1 is that no model in this repo has a measured cold start.
   `InstanceWarmup: 420` and the lifecycle-hook heartbeat are guesses until this
   is measured.
6. ⚠️ **Actual Prometheus series count per box.** §1.5's ~1.6k is `est.` and
   node_exporter's ~800 has no primary source. The Grafana Cloud Free
   10k-series ceiling turns into a surprise bill if this is wrong by 3×. One
   `curl -s :9100/metrics | grep -vc '^#'` per exporter closes it.
7. ⚠️ **Does AWS WAF body inspection cover an 86 MB request body**, or does it
   silently inspect only a prefix? §4.2 treats the WAF size rule as coarse and
   puts real enforcement in the gateway for this reason, but the exact limit
   determines whether the WAF rule is worth having at all.
8. ⚠️ **ElastiCache durability semantics per engine version** (Valkey vs Redis
   OSS, AOF availability, Multi-AZ failover RPO). §3.6 chose SQS partly to avoid
   needing this answer; if a Redis queue is ever revisited, answer it first.
9. ⚠️ **Which paging provider**, given that Grafana OnCall's status and
   PagerDuty's free-tier terms have both moved. §7's only hard requirement is
   that a `severity: page` label rings a silenced phone.
10. ~~⚠️ **AMP and CloudWatch exact per-sample / per-GB rates**~~ —
    **RESOLVED 2026-09-20 (verification log #27).** Both pages render on
    re-read. AMP [src](https://aws.amazon.com/prometheus/pricing/): ingest
    **$0.90 per 10 M samples** for the first 2 B samples/month (tiered above),
    storage **$0.03 per GB-month**, query **$0.10 per billion query samples
    processed**, a populated native-histogram bucket metered at **0.25 of a
    sample** and empty buckets not metered; free tier 40 M samples ingested,
    200 B query samples, 10 GB storage per month. CloudWatch
    [src](https://aws.amazon.com/cloudwatch/pricing/): OTel/Prometheus ingest
    **$0.50/GB** with **15 months** storage included and no per-series charge,
    PromQL **$0.01 per million samples scanned**, console and dashboard queries
    free, Synthetics free tier **100 canary runs/month**. The §1.5 decision rule
    is unchanged: at ~1.6k series and one box, Grafana Cloud Free still wins,
    and AMP's own free tier (40 M samples/month ≈ 1.6k series scraped at 15 s
    for ~4 days) is *not* enough to run on.
11. **Product question, not technical:** can customers be required to upload
    clips to a presigned S3 URL instead of supplying arbitrary URLs? If yes, the
    entire SSRF class (§4.1), the duplicate download (§3.7), the unbounded-body
    risk (§4.2) and most of the egress security-group surface disappear at once.
    This is the single highest-leverage decision in this document and it is not
    ours to make.

## Sources

All read 2026-09-20 unless stated.

**vLLM**
- [Production metrics](https://docs.vllm.ai/en/latest/usage/metrics.html) — the metric catalogue, `/metrics`, deprecation policy
- [Online serving](https://docs.vllm.ai/en/latest/serving/online_serving/) — `/health`, `/ping`, `/load`, `/version`, `/metrics` endpoints
- [`vllm/entrypoints/serve/instrumentator/health.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/instrumentator/health.py) — `/health` returns 503 only on `EngineDeadError`
- [`vllm/entrypoints/serve/instrumentator/basic.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/instrumentator/basic.py) — `/load`, `/version`
- [`vllm/envs.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/envs.py) — engine/media timeouts, media cache, redirect default
- [`vllm/entrypoints/serve/middleware/register.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/middleware/register.py) — `--api-key`, request-id headers, response-logging warning
- [`vllm/entrypoints/serve/__init__.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/__init__.py) — dev-mode security warning
- [`docs/usage/metrics.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/usage/metrics.md) — source of the rendered metrics page

**AWS — load balancing**
- [Health checks for ALB target groups](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html) — defaults, ranges, fail-open, reason codes
- [Target groups for ALBs](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html) — attributes, deregistration delay, slow start, algorithms, target group health
- [Register targets](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-register-targets.html) — Target Optimizer agent, env vars, drain ordering
- [Application Load Balancers](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html) — LB attributes, idle timeout, subnet sizing, states
- [ALB CloudWatch metrics](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html) — metric list, target-optimizer metrics, statistic guidance
- [ALB quotas](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-limits.html)
- [Elastic Load Balancing pricing](https://aws.amazon.com/elasticloadbalancing/pricing/) — LCU billing model

**AWS — compute and scaling**
- [Instance refresh](https://docs.aws.amazon.com/autoscaling/ec2/userguide/asg-instance-refresh.html) and [its defaults](https://docs.aws.amazon.com/autoscaling/ec2/userguide/understand-instance-refresh-default-values.html)
- [ASG health checks](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-health-checks.html), [health check grace period](https://docs.aws.amazon.com/autoscaling/ec2/userguide/health-check-grace-period.html)
- [Lifecycle hooks](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) — heartbeat and global timeouts, best-effort termination hooks
- [Warm pools](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html) — states, EBS-root requirement, instance reuse, user-data caveat
- [On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html)
- [EC2 on-demand price sheet, us-east-1, Linux](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) — g6e.xlarge $1.861, g6e.2xlarge $2.24208, g6e.4xlarge $3.00424, g6e.16xlarge $7.57719, g6e.24xlarge $15.06559 per hour
- [IMDSv2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html) — session tokens, IPv4 and IPv6 endpoints

**AWS — queues, security, ops**
- [SQS visibility timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html), [dead-letter queues](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html), [FIFO queues](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-fifo-queues.html), [message quotas](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.html)
- [WAF rate-based rules](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based.html) and [their settings](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based-high-level-settings.html)
- [AWS Shield Standard vs Advanced](https://docs.aws.amazon.com/waf/latest/developerguide/ddos-overview.html)
- [SSM Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html) — tiers, and the recommendation to use Secrets Manager for API keys and tokens
- [AWS Fault Injection Service](https://docs.aws.amazon.com/fis/latest/userguide/what-is.html) and [actions reference](https://docs.aws.amazon.com/fis/latest/userguide/fis-actions-reference.html)
- [CloudWatch Synthetics canaries](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Synthetics_Canaries.html)
- [CloudWatch agent Prometheus scraping on EC2](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-PrometheusEC2.html)
- [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/) — OTel ingest, PromQL queries, Synthetics free tier
- [Amazon Managed Service for Prometheus pricing](https://aws.amazon.com/prometheus/pricing/)
- [Amazon Managed Grafana pricing](https://aws.amazon.com/grafana/pricing/)

**Non-AWS**
- [Google SRE Workbook, Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/) — burn rates 14.4 / 6 / 1 and the 1/12 short window
- [Grafana Cloud pricing](https://grafana.com/pricing/) — Free 10k series / 14 days; Pro $19 + $6.50 per 1k series
- [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https) — renewal, ACME staging, rate limits
- [Supabase SLA](https://supabase.com/sla) — 99.9 % for Enterprise only, third-party exclusions
- [Supabase going into production](https://supabase.com/docs/guides/platform/going-into-prod) — disk durability, PITR
- [NVIDIA Xid Errors](https://docs.nvidia.com/deploy/xid-errors/)
- [NVIDIA DCGM Exporter](https://github.com/NVIDIA/dcgm-exporter) and its [metrics reference](https://docs.nvidia.com/datacenter/dcgm/latest/reference/dcgm-exporter-metrics.html)

**In this repo**
- [`apps/README.md`](../../apps/README.md), [`apps/infrx-api/README.md`](../../apps/infrx-api/README.md), [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py), [`apps/infrx-api/deploy/`](../../apps/infrx-api/deploy/)
- [`models/marlin2b/README.md`](../../models/marlin2b/README.md), [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md), [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh)
- [`research/scaling/03`](../scaling/03-concurrency-and-admission-control.md), [`05`](../scaling/05-autoscaling-and-predictive-scaling.md), [`06`](../scaling/06-cold-start.md), [`08`](../scaling/08-reliability-and-operations.md), [`10`](../scaling/10-blueprint.md)
- [`research/cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md), [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md)
- [`research/METHODOLOGY.md`](../METHODOLOGY.md)

---

## Verification log (2026-09-20)

Adversarial re-check of the 25 most consequential claims in this document:
AWS quotas/defaults/prices, vLLM flags and metric names, the Google SRE burn
rates, every derived number, and every statement about code and measurements in
this repo (checked by opening the files, not by trusting the prose). Each row
names the primary source actually opened and the verdict. The 25 target claims
expanded to **38 checkable rows** once claim-clusters (e.g. "the ALB defaults")
were opened one setting at a time. **38 checked: 30 CONFIRMED, 7 CORRECTED,
1 UNVERIFIABLE.** The corrections are rows 23, 24, 27, 30, 31, 37 and 38; the
four that change a number someone would act on are **23** (a burn-rate alert
that could never fire), **31** (a cap justified by a ceiling that does not
exist), **37** and **38** (arithmetic).

| # | claim (§) | verdict | evidence |
|---|---|---|---|
| 1 | `g6e.2xlarge` on-demand us-east-1 Linux **$2.24208/hr** (§0, §3.2, §3.4) | **CONFIRMED** | price sheet parsed, not summarised: `"2.2420800000"`, 8 vCPU, 64 GiB [src](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) |
| 2 | `g6e.4xlarge` **$3.00424/hr**, 16 vCPU, "1.34× the price" (§5.4) | **CONFIRMED** | `"3.0042400000"`, `vCPU 16`; 3.00424 / 2.24208 = **1.3399** `calc` |
| 3 | Sources-list g6e prices: xlarge $1.861, 16xlarge $7.57719, 24xlarge $15.06559 | **CONFIRMED** | same sheet: `1.8610000000`, `7.5771900000`, `15.0655900000` |
| 4 | N+1 targeted ODCR spare ≈ **$1,636/month** (§3.4) | **CONFIRMED** | 2.24208 × 730 h = **$1,636.72** `calc` (720 h → $1,614.30; both round to the stated figure) |
| 5 | ALB health-check defaults/ranges: interval 30 (5–300), timeout 5 (2–120), healthy 5 (2–10), unhealthy 2 (2–10), matcher 200 (§2.3) | **CONFIRMED** | all five rows verbatim in the settings table [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html) |
| 6 | ALB **fails open** when every target is unhealthy (§2.2, §2.3) | **CONFIRMED** | "if all targets fail health checks at the same time in all enabled Availability Zones, the load balancer fails open" [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html); `target_group_health.unhealthy_state_routing.minimum_healthy_targets.count` default **1** confirmed separately |
| 7 | A new target serves after **one** passing check "irrespective of the configured threshold" — so `HealthyThresholdCount` does not protect a cold box (§2.3) | **CONFIRMED** | "as soon as the registration process completes and the target passes the first initial health check, irrespective of the configured threshold" [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html) |
| 8 | `deregistration_delay` default 300 / range 0–3600; `slow_start` range 30–900 default 0; `least_outstanding_requests` vs default `round_robin` (§2.3) | **CONFIRMED** | target-group attribute list, all three verbatim [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html) |
| 9 | `idle_timeout.timeout_seconds` default **60 s** (the fact §2.3's whole heartbeat design rests on); `waf.fail_open.enabled` default `false`; subnet `/27` + 8 free IPs | **CONFIRMED** | LB attribute list [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html). Subnet-exhaustion *wording* tightened in §2.3 — AWS says "run with insufficient capacity … might cause 5xx errors or timeouts"; `active_impaired` is named on that page only for the Outpost case |
| 10 | Target Optimizer: `TARGET_CONTROL_MAX_CONCURRENCY` **0–1000, default 1**, agent at `public.ecr.aws/aws-elb/target-optimizer/target-control-agent:latest`, enable at target-group creation only, control port immutable; `TargetControlRequestRejectCount` = "Number of requests **rejected** by ALB due to no targets being ready to receive requests" (§2.3) | **CONFIRMED** | env-var list and the note "Target optimizer can only be enabled during target group creation" [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-register-targets.html); metric description verbatim [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html). §2.3's conclusion — it replaces `MAX_INFLIGHT`'s 429, it does not replace the queue — stands |
| 11 | ALB CloudWatch metrics are emitted **60 s** and **only while requests flow**, so `HTTPCode_ELB_5XX_Count > 0` cannot fire in a total outage (§1.2) | **CONFIRMED** | "Elastic Load Balancing reports metrics to CloudWatch only when requests are flowing… measures and sends its metrics in 60-second intervals… If there are no requests flowing through the load balancer or no data for a metric, the metric is not reported" [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html). The synthetic-canary pairing in §6.3 is therefore load-bearing, not belt-and-braces |
| 12 | AWS recommends alarming on the **`Minimum`** statistic of `UnHealthyHostCount`, non-zero over more than one datapoint (§1.3 #12) | **CONFIRMED** | "We recommend you monitor for non-zero `UnHealthyHostCount` in the `Minimum` statistic, and alarm on non-zero value for more than one data point" [src](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html). `LowReputationPacketsDropped` / `LowReputationRequestsDenied` (§4.4) also exist as documented |
| 13 | Instance-refresh defaults: MinHealthy **90 %**, MaxHealthy **100 % (null)**, checkpoints disabled, `CheckpointDelay` **3600 s**, AutoRollback **disabled**, bake time **zero**, SkipMatching **disabled on CLI / enabled in console**, InstanceWarmup falls back to default warmup or the grace period; alarms in `AlarmSpecification` can fail and roll back the operation (§2.5) | **CONFIRMED** | every cell matches the defaults table and the `AlarmSpecification` description ("fail the operation if an alarm goes into the `ALARM` state") [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/understand-instance-refresh-default-values.html) |
| 14 | Health-check grace period: console default **300 s**, CLI/SDK default **0**, and 0 turns it off (§2.6) | **CONFIRMED** | "the health check grace period is 300 seconds when you create an Auto Scaling group… Its default value is 0 seconds when you create an Auto Scaling group using the AWS CLI or an SDK. A value of 0 turns off the health check grace period" [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/health-check-grace-period.html) — §2.6's "will kill every box during its 2–3 minute vLLM boot, forever" is correct |
| 15 | Lifecycle hooks: heartbeat default **1 hour**, global timeout **48 h or 100× the heartbeat, whichever is smaller**, termination hooks best-effort (§2.6) | **CONFIRMED** | "The global timeout is 48 hours or 100 times the heartbeat timeout, whichever is smaller"; "If a termination lifecycle hook times out, or is abandoned, Amazon EC2 Auto Scaling proceeds with terminating the instance immediately" [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html) |
| 16 | Warm pools need an **EBS root device** to stop/hibernate, and EC2 Auto Scaling "does not wait for user data to finish running" (§2.6) | **CONFIRMED** | both limitations verbatim [src](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html). §2.6's conclusion — the DLAMI root is EBS, so warm pools are *possible*, but the NVMe weights are not — survives |
| 17 | SQS: retention default **4 d**, min 60 s, max 14 d; visibility default **30 s**, max **12 h**; payload max **1 MiB**; FIFO **300 TPS/partition**, **3,000 msg/s** batched, **70,000 TPS** high-throughput in us-east-1 (§3.6) | **CONFIRMED** | every figure in the message-quota table, incl. "The maximum is 1,048,576 bytes (1 MiB)" and "US East (N. Virginia), US West (Oregon), and Europe (Ireland): Up to 70,000 transactions per second" [src](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.html). §3.6's "1 MiB is below our 64 MB `MAX_VIDEO_MB`" therefore holds by 64× |
| 18 | WAF rate-based rules: windows **60/120/300/600**, default **300**; minimum limit **10**; "does not guarantee an exact limit match" (§4.3) | **CONFIRMED** | "Valid settings are 60 (1 minute), 120 (2 minutes), 300 (5 minutes), and 600 (10 minutes), and 300 (5 minutes) is the default"; "The lowest limit setting allowed is 10"; "AWS WAF applies rate limiting near the limit that you set, but does not guarantee an exact limit match" [src](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based-high-level-settings.html) — §4.3's "never build billing or quota enforcement on it" is AWS's own position |
| 19 | SSM Parameter Store: use Secrets Manager for API keys/tokens; standard tier **free, 10,000 parameters, 4 KB values** (§4.5) | **CONFIRMED** | "For secrets such as database credentials, API keys, or tokens, we recommend AWS Secrets Manager, which provides purpose built security controls including automatic rotation and cross-region replication"; tier table: Standard 10,000 / 4 KB / "No additional charge" [src](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html) |
| 20 | Every vLLM metric name used here exists, incl. `vllm:corrupted_requests`, `vllm:mm_cache_hits/_queries`, `vllm:iteration_tokens_total`; deprecation hides at X.Y+1 and removes at X.Y+2 with `--show-hidden-metrics-for-version` (§1.2, §3.3, §6.4) | **CONFIRMED** | all 14 names present; "deprecated in version `X.Y`… hidden in version `X.Y+1`… removed in version `X.Y+2`" [src](https://docs.vllm.ai/en/latest/usage/metrics.html). §6.4 step 3's metric-existence gate is the right response to a `:nightly` pin |
| 21 | vLLM env defaults: `ITERATION_TIMEOUT_S` 60, `ENGINE_READY_TIMEOUT_S` 600, `EXECUTE_MODEL_TIMEOUT_SECONDS` 300, `WORKER_SHUTDOWN_TIMEOUT_SECONDS` 5, `VIDEO_FETCH_TIMEOUT` 30, `MEDIA_FETCH_MAX_RETRIES` 3, `MEDIA_LOADING_THREAD_COUNT` 8, `MEDIA_URL_ALLOW_REDIRECTS` **1** (§3.3, §4.1) | **CONFIRMED** | all eight read from [`vllm/envs.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/envs.py). The SSRF argument in §4.1 rests on the redirect default being on, and it is |
| 22 | `/health` returns 503 **only** on `EngineDeadError` — so it is a liveness probe, never a readiness probe (§2.1) | **CONFIRMED** | the quoted block is byte-identical to the file, including the render-only-server branch [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/instrumentator/health.py). This is the load-bearing fact of §2 and it holds |
| 23 | Google SRE multiwindow burn rates **14.4 / 6 / 1** over 1 h / 6 h / 3 d for 2 % / 5 % / 10 % of budget, short window **1/12** of the long (§1.3, §1.4, §7) | **CONFIRMED**, but §1.4's YAML was broken | Table 5-8 matches exactly, incl. short windows 5 m / 30 m / **6 h** [src](https://sre.google/workbook/alerting-on-slos/). Recomputed: 0.02 × 30 × 24 / 1 = **14.4**, 0.05 × 30 × 24 / 6 = **6.0**, 0.10 × 30 × 24 / 72 = **1.0** `calc`. **Defect found and fixed:** `ErrorBudgetBurnTicket` alerted on `slo:errors:ratio_rate3d`, which **no recording rule defined** — the alert evaluated to "no data" forever, i.e. the 3-day ticket never fired. §1.4 now records `slo:errors:ratio_rate3d` and pairs it with the workbook's 6 h short window |
| 24 | **Grafana Cloud pricing** (§1.5) | **CORRECTED** | Free tier "Limited to 10k active series per month", "14 days retention", community support — confirmed. Pro is **$19/mo** including 10k series and 13-month retention, then "**starts at** $6.50 per 1,000 series with automatic volume discounts" [src](https://grafana.com/pricing/). The document's "**$6.50 per 1k series in the 10k–100k band**" invents a band the page does not publish; §1.5 now states $6.50/1k as the worst-case rate with the discount schedule marked ⚠️ |
| 25 | Amazon Managed Grafana **$9 per editor/admin, $5 per viewer, per workspace per month, minimum one editor** (§1.5) | **CONFIRMED** | verbatim, incl. "Each workspace requires a minimum of one Amazon Managed Grafana Editor license" [src](https://aws.amazon.com/grafana/pricing/) — §1.5's "the $9 minimum editor seat … buys nothing at one box" is right |
| 26 | CloudWatch: OTel ingest **$0.50/GB** with 15 months storage, PromQL **$0.01 per million samples scanned**, console/dashboard queries free, Synthetics free tier **100 runs/month** (§1.5, §5.8, §6.3) | **CONFIRMED** | all four render on re-read [src](https://aws.amazon.com/cloudwatch/pricing/). The canary arithmetic checks out: 60 × 24 × 30 = **43,200 runs/month** `calc` against a 100-run free tier — §6.3's "is a real line item" is correct |
| 27 | Open question 10, "AMP and CloudWatch pricing pages did not render" | **CORRECTED — question resolved** | both render. AMP: **$0.90 per 10 M samples** (first 2 B/month), **$0.03/GB-month** storage, **$0.10 per billion** query samples, native-histogram populated bucket = **0.25 of a sample**, free tier 40 M samples / 200 B query samples / 10 GB [src](https://aws.amazon.com/prometheus/pricing/). Open question 10 rewritten as resolved; the Grafana-Cloud-Free decision rule is unchanged |
| 28 | Supabase commits **99.9 %** monthly uptime **to Enterprise only**, with AWS and other cloud providers excluded (§3.5, §5.7) | **CONFIRMED** | "ninety-nine and nine tenths percent (99.9%)"; Enterprise-only; "Issues attributable to external vendors or cloud providers, including AWS, Cloudflare, GCP, Azure, GitHub" excluded [src](https://supabase.com/sla). §3.5's "At our tier there is no contractual uptime at all" is accurate |
| 29 | Caddy "keeps all managed certificates renewed"; Let's Encrypt rate limits "can block your access to HTTPS for up to a week"; `caddy_data` must persist (§5.5) | **CONFIRMED** | both quotes verbatim; the docs require the data directory be "writeable and persistent" [src](https://caddyserver.com/docs/automatic-https). `install.sh` does mount a named `caddy_data` volume — checked, §5.5's parenthetical "(it is)" is true |
| 30 | §0 attributes `--max-num-seqs 32` to `models/marlin2b/serve.sh` | **CORRECTED** | `serve.sh` passes `--served-model-name`, `--hf-overrides`, `--max-model-len`, `--gpu-memory-utilization`, `--limit-mm-per-prompt`, `--dtype` and then `"$@"`. **`--max-num-seqs 32` appears only on the `ExecStart` line of `apps/infrx-api/deploy/marlin2b-vllm.service`.** It matters operationally: running `serve.sh` by hand — which §6.4's weekly re-validation script does — gives vLLM's *default* `max_num_seqs`, so the benchmark would not reproduce production. §1.3 metric 7 already had it right. §0 table fixed |
| 31 | §4.2/§3.3: "2048 … the model's ceiling per `models/marlin2b/README.md`" | **CORRECTED** | that README names no `max_tokens` ceiling; its own example uses `"max_tokens": 512`. 2048 is the **helper default**: `smoke.py --max-tokens` 2048, `reference.py --max-new-tokens` 2048, `.caption(max_new_tokens=2048)`. The only hard limit on the box is `--max-model-len 32768`. Rewritten as a *policy* cap with the real provenance — the recommendation is unchanged, its justification was wrong |
| 32 | The `inflight` **double-decrement** (§Implications 1) | **CONFIRMED — real bug** | in `gateway.py`'s non-streaming path `inflight -= 1` runs immediately after `await client.post(...)`; a raise in the following `r.json()` or `log(...)` reaches the outer `except`, which does `inflight -= 1` again. `inflight` is a module-level `int`, so it drifts negative and `if inflight >= MAX_INFLIGHT` stops binding. The streaming path is already correct (`finally: inflight -= 1`). Ranking this first in §Implications is right |
| 33 | The other four `gateway.py` claims: unbounded `_keys`/`_last_used`; `token == LEGACY_KEY` not constant-time; `r.content` buffers before the size check; `ffprobe … timeout=30` in the default executor with `min(32, 8+4) = 12` threads (§3.5, §3.7, §4.6) | **CONFIRMED** | all four read in source. `_keys[h] = (…, None)` is inserted on a **miss**, so unauthenticated traffic grows the dict — the memory-exhaustion claim is exact. `min(32, os.cpu_count()+4)` = **12** on 8 vCPUs `calc`. Also confirmed: `follow_redirects=True`, `tempfile.NamedTemporaryFile(delete=False)` with `os.unlink` in `finally`, and `f"upstream error: {e}"` returned in the response body |
| 34 | `usage_events` degradation: bounded queue **10,000**, retries at **1/3/9 s**, then spill (§3.5) | **CONFIRMED** | `_usage_q = asyncio.Queue(maxsize=10000)`; `RETRY_DELAYS = (1, 3, 9, 0)` with `0` = give up and `spill()`. `KEY_TTL, MISS_TTL = 60, 10` and `httpx.Timeout(5, connect=2)` also confirm §3.5's 60 s/10 s/5 s figures |
| 35 | Measured numbers: 0.50 clips/s at c=1 with TTFT **0.77 s**; **1.57** vs **3.58** clips/s; TTFT p50 **3.35 s**; TPOT **6–8 ms**; ~**18 s** first-kwargs (§0, §1.1, §2.2, §3.3, §3.7, §5.1) | **CONFIRMED** | every figure in the results table of [`models/marlin2b/README.md`](../../models/marlin2b/README.md) and findings 4/5/7 of [`results/notes.md`](../../models/marlin2b/results/notes.md). Derived claims recomputed: 3.58 / 1.57 = **2.28×** ("2.3×" ✓); one `/warm` probe = 1 / 3.58 = **0.279 s** ("~0.3 s" ✓) = **0.47 %** of a GPU-minute ("0.5 %" ✓); 2048 tokens × 8 ms = **16.4 s** ("~16 s" ✓, at the top of the TPOT range); 64 MB × 4/3 + 64 KB = **85.4 MB** ("≈ 86 MB" ✓, rounded up); 4 × `MAX_INFLIGHT` = **64** ✓ |
| 36 | "vLLM boots in ~2–3 min including `torch.compile`" (§0, and everything sized against it) | **UNVERIFIABLE** | the figure appears in **no** repo file. `models/marlin2b/README.md`, `results/notes.md` and `bench.jsonl` record no boot time; the only adjacent datum is `TimeoutStartSec=900`. Marked ⚠️ in §0, with the four settings that depend on it named. This *is* open question 5 — it is not a nice-to-have measurement, it is the input to `InstanceWarmup`, the grace period and the lifecycle-hook heartbeat |
| 37 | "≈ 1.6k series per box, so Free holds ~5 boxes" (§1.5) | **CORRECTED** | 600 + 800 + 60 + 120 = **1,580**; 10,000 / 1,580 = **6.3 boxes** `calc`, not 5. Changed to ~6 with the note that the move should still be planned at 5 (one box of headroom). The node_exporter ~800 remains ⚠️ unsourced, so the true figure is soft in the other direction too — open question 6 |
| 38 | "$0.02–0.04 per video-hour" cross-check (§5.8) | **CORRECTED — the source it quotes is wrong** | [`results/notes.md`](../../models/marlin2b/results/notes.md)'s cost sketch converts 16–36 video-seconds/second to "57–129 video-hours per GPU-hour" by dividing by **1,000** instead of **3,600**. Correct: 15.7–35.8 video-s/s × 3,600 = 56,520–128,880 video-**seconds** = **15.7–35.8 video-hours** per GPU-hour, so $2.24208 / 35.8 … / 15.7 = **$0.0626–$0.1428 per video-hour** `calc` — **3.6× higher** than printed. §5.8 now carries the corrected range and flags the stale figure; `notes.md` itself still needs the same fix (out of scope for this document). [`08` §1.2](08-cost-model-and-unit-economics.md) reached the same two figures independently from the same rows, so §5.8 cites it rather than re-deriving — the tree is now consistent on this number |

**Method.** Prices read from the machine-readable AWS sheet and parsed, not from
a rendered page. AWS defaults read from the service's own user-guide page, never
from a blog or a summary. vLLM claims read from `main` in the repository, not
from release notes. Every repo claim checked by opening the file named. Every
derivation recomputed with `python3`; the arithmetic checks that passed are
recorded inline in rows 4, 26 and 35 rather than asserted.

**Cross-document conflicts surfaced while checking** (flagged inline, not
silently resolved — this document does not own either number):
`idle_timeout.timeout_seconds` is **600** here, **180** in
[`01` §F12](01-requirements-and-traffic-model.md) and **900** in
[`02`](02-aws-architecture-options.md); and the published TTFT SLO is
**p95 ≤ 8 s** here against **p95 ≤ 6 s** in
[`01` §2.7](01-requirements-and-traffic-model.md), which owns the SLO table.
Also noted: §3.6 and §4.3 size the queue and the per-key cap as multiples of
`MAX_INFLIGHT = 16`, while [`02`](02-aws-architecture-options.md) and
[`03`](03-request-handling-and-queueing.md) **remove** `MAX_INFLIGHT` in favour
of a measured `MAX_CONCURRENCY` (proposed 8) — so "64" and "4" here are
arithmetic on a constant that is scheduled to disappear.

**Not re-checked and still open** (each is an entry in §Open questions, none was
closable from a primary source on this date): whether vLLM's HTTP server binds
before the engine is ready (#1); the flag that enables `/load` server-load
tracking (#2); `--allowed-local-media-path` on the pinned nightly (#3) — the
web-search budget was exhausted before this could be settled, and it decides
whether §3.7 fix 3 is a path hand-off or a `data:` URL; transcode parity (#4);
measured cold start (#5); real series count (#6); WAF body-inspection limit (#7);
ElastiCache durability per engine version (#8); paging provider (#9); and the
presigned-S3 product question (#11), which remains the highest-leverage decision
in the document.
