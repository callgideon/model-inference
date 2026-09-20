# Offering Marlin-2B on OpenRouter — end-to-end plan

Researched 2026-09-20 from OpenRouter's provider docs, its provider
model-document schema (v2.4), its video-input guide and its public models
API. What OpenRouter does not publish (revenue share, approval time, minimum
volume) is marked ⚠️.

## What OpenRouter requires of a provider (sourced)

| Requirement | Detail | Source |
|---|---|---|
| Apply | "fill out our form to get started" at openrouter.ai/how-to-list; the FAQ says email is the best contact. | [for-providers](https://openrouter.ai/docs/use-cases/for-providers), [FAQ](https://openrouter.ai/docs/faq) |
| `/v1/models` | Must return a provider model document per model (schema v2.4): id, name, modalities with pricing + capacity per modality, supported parameters, `streaming`, `is_ready`, `compliance`, `datacenters`. Video is a legal input modality. | [schema](https://openrouter.ai/docs/assets/provider-monitor-schema-v2.openapi.json) |
| Chat API | OpenAI-style `/v1/chat/completions`; text output "may declare `streaming: true`" (SSE). | for-providers |
| Video input | OpenRouter clients send `{"type":"video_url","video_url":{"url": "<https or data:video/mp4;base64,...>"}}`; formats mp4, mpeg, mov, webm. OpenRouter "only sends video URLs to providers that explicitly support them", so declare `video` in `input_modalities`. | [video guide](https://openrouter.ai/docs/guides/overview/multimodal/videos) |
| Reliability | Uptime = successful ÷ total requests (user errors excluded): ≥95 % normal routing, 80–94 % degraded, <80 % fallback-only. TTFT and throughput are tracked publicly. "Return early 429s if under load, rather than queueing requests"; stream tokens immediately; send SSE keep-alives. | for-providers |
| Payment | "For OpenRouter to use the provider we must be able to pay for inference automatically. This can be done via auto top up or invoicing." | for-providers |
| Data policy | Providers that log prompts, or whose policy cannot be confirmed, are not routed to unless the user opts in; declare `compliance.zdr`. | FAQ, schema |
| Pricing | Provider-set, passed through to users ("you get the same pricing you'd get from the provider directly"). | FAQ |
| ⚠️ Not published | Revenue share/fees, approval timeline, minimum capacity, whether a brand-new model page (Marlin is not on OpenRouter today) is created from the provider document alone or needs a partnership conversation. Ask in the application. | — |

Market context from the public models API (2026-09-20): 447 models, 80
accept video. Cheapest paid video-capable models: qwen3.7-flash
$0.03/$0.13 per 1M in/out, ling-3.0-flash-vl $0.06/$0.18, qwen3.5-9b
$0.10/$0.15, gemini-2.5-flash-lite $0.10/$0.40, qwen3.8-27b $0.42/$3.00.
A 2B specialist has to price at or below the flash tier and win on
timestamped captions and grounding quality, not on generality.

## Architecture

```
OpenRouter ──HTTPS──▶ gateway (this repo, new)            ──▶ vLLM (marlin2b/serve.sh)
                      • API key check (OpenRouter's key)       --hf-overrides Qwen3.5
                      • /v1/models = provider-models.json      --max-model-len 32768
                      • video: fetch URL / decode base64,      --limit-mm-per-prompt video=1
                        ffprobe duration, reject >120 s,
                        transcode to ≤448 px @ 2 fps (CPU),
                        inject mm_processor_kwargs
                        (size.longest_edge = frames×200704)
                      • strip leading <think>, keep SSE flowing
                      • Inference-Id header, usage → metering DB
                      • 429 when in-flight ≥ N, keep-alives
                      • Prometheus metrics, /health
```

The gateway is the only new code. Everything measured on the L40S dev box
(`../results/notes.md`) shapes it: without the pixel-budget injection a 10 s
clip costs 12K tokens instead of 2K, and per-request video decode of 1080p
sources is the throughput bottleneck, so transcoding at the gateway (on
CPU, before vLLM) is the first optimization, not an afterthought.

## Steps

### 1. Product decisions (half a day)
1. Model id on OpenRouter: `nemostation/marlin-2b` (HF id `NemoStation/Marlin-2B`, Apache-2.0 — commercial serving allowed; no MTP, no tools).
2. Modes are prompts, not endpoints: publish the two canonical prompts in the model description (already in `provider-models.json`) and in our own docs.
3. Limits: 1 video per request, ≤120 s (240 frames at 2 fps), ≤64 MB upload, `max_tokens` ≤2048, context 32K.
4. Launch price (edit `provider-models.json`): $0.10 per 1M input tokens, $0.30 per 1M output. At the measured 3.6 clips/s on 360p inputs a g6e.2xlarge ($2.24/h) earns ≈ $2.9/h at that price (≈2K in + 200 out tokens per clip); at 1.6 clips/s on 1080p inputs it earns ≈ $1.3/h and loses money, which is why transcoding at the gateway is required. Re-price after a week of real traffic.
5. Data policy: zero retention of prompts, videos and outputs (declare `compliance.zdr: true`); keep only request id, token counts, timings for billing.

### 2. Production endpoint on AWS (1–2 days)
1. Instance: start with `g6e.4xlarge` (1× L40S, 16 vCPU) so video decode has CPU headroom; `g6e.12xlarge` (4× L40S) when traffic needs more than one replica. Reuse the dev-box layout (`/opt/dlami/nvme`, weights via `marlin2b/download.sh`, vLLM nightly pinned by digest).
2. Serve: `./marlin2b/serve.sh --max-num-seqs 32` under a systemd unit with restart-on-failure; pin the docker image digest that passed `smoke.py`; keep `torch.compile` cache on NVMe so restarts take seconds.
3. Gateway (new, ~300 lines Python/FastAPI or Go): the responsibilities in the diagram; run beside vLLM on the same box; expose only the gateway.
4. Ingress: a domain (e.g. `api.<yourdomain>`) with TLS via Caddy or an ALB; put the instance in a private subnet and allow inbound only from the load balancer; close SSH.
5. Health and readiness: `/health` returns 200 only when vLLM answers `/v1/models` and a cached warm request succeeds.

### 3. Metering and billing (1–2 days)
1. Every response carries `Inference-Id: <uuid>`; the gateway records id, model, prompt tokens, completion tokens, video seconds, TTFT, status, and cost at the published price in a small Postgres/SQLite table.
2. Give OpenRouter an account on our side that they can pay automatically: either prepaid credits with **auto top-up** (Stripe card on file, top up when balance < threshold) or **invoicing** (monthly statement from the metering table). State which in the application.
3. A monthly usage export (CSV of the metering table) is enough for reconciliation with OpenRouter's statements.

### 4. Reliability before applying (1 day)
1. Load test with `marlin2b/bench.py` against the gateway (not vLLM directly) at concurrency 1, 8, 16 with mixed 360p/1080p clips and 10 s/60 s/120 s durations; record TTFT p50/p95 and clips/s; set the gateway's in-flight cap where p95 TTFT stays under ~5 s and return 429 above it.
2. Alerting on: error rate, TTFT p95, in-flight count, GPU memory, disk. Prometheus + Grafana from `research/scaling/08-reliability-and-operations.md`'s dashboard list.
3. Failure drill: kill vLLM; confirm the gateway returns 503 quickly (not timeouts) and systemd restarts it within a minute.

### 5. Apply (30 minutes, then wait ⚠️)
1. Submit https://openrouter.ai/how-to-list and email their team. Include: company, model (HF link, licence, what it does, the two prompts), the public `/v1/models` URL returning `provider-models.json`, the chat endpoint URL and API key, pricing, capacity (concurrency, requests/minute), datacenter (us-east-1), ZDR statement, the payment method (auto top-up or invoicing), a status/contact email.
2. Ask explicitly: (a) whether they will create the `nemostation/marlin-2b` model page from our document, (b) their revenue share and payout schedule, (c) whether they forward video **URLs** or only base64 to new providers.
3. While waiting, make the endpoint public under our own brand (docs page with curl examples, the prompts, pricing) so the OpenRouter listing is not the only channel.

### 6. Integration test and go-live (1 day after approval)
1. OpenRouter's provider monitor polls `/v1/models`; keep `is_ready: true` only when the endpoint is healthy.
2. Test through OpenRouter with the provider pinned (`provider: {"order": ["<our provider name>"], "allow_fallbacks": false}` in the request body) for: caption on a 10 s clip via URL and via base64, grounding, a 120 s clip, an over-limit clip (expect 400), streaming, and `max_tokens`.
3. Watch OpenRouter's public uptime/TTFT for the model for a week; keep uptime ≥95 % or traffic is deprioritised.

### 7. Second channel (optional, later): Hugging Face Inference Providers
HF lists providers on model pages, but registration needs PRs to
`huggingface.js` and `huggingface_hub`, a Team/Enterprise Hub org, a model
mapping API call, and a billing endpoint that returns per-request cost in
nano-USD ([register-as-a-provider](https://huggingface.co/docs/inference-providers/en/register-as-a-provider)).
Because Marlin's `pipeline_tag` is `video-text-to-text`, which the JS client
does not map to `chatCompletion` today, this needs a conversation with HF
first. Do it after OpenRouter is live.

## Open questions ⚠️
- OpenRouter revenue share, payout terms, approval time (not published).
- Whether OpenRouter will create a page for a model that is not yet on the
  platform purely from a provider document.
- Whether OpenRouter's clients strip or forward `mm_processor_kwargs`
  (assume not; the gateway must set the video budget itself).
- Video URL fetching policy: size limits, allowed hosts, timeouts on our side.
- Whether NemoStation wants attribution or a heads-up; Apache-2.0 does not
  require it, courtesy does.
