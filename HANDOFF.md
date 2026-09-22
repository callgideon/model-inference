# Handoff — product amendment 2026-09-21; historical operations below

**Latest handoff:** [ready-to-copy implementation prompt](research/plan/16-fresh-session-handoff.md), [complete plan](research/plan/12-complete-build-plan.md) and [pending work](research/plan/15-pending-inputs.md). The immediate objective is launching `apps/app` for Marlin2B inference supporting SOP verification over large robotics datasets. Lab and later extensions have detailed follow-on plans; do not dispatch them ahead of the accepted App candidate.

**Start with [the wave-2 audit](research/plan/10-wave2-platform-audit.md), [two-platform architecture](research/platforms/README.md) and [the continuation handoff](research/plan/PLATFORM-SPLIT-HANDOFF.md).** Main was pulled at `271add9`; all eleven wave-2 modules are preserved. F2R/F2P and additive D1R revisions precede product-v2 integration. Manifest v4 preserves original completion statuses separately from amendment requirements. Wave 3 feature implementation has not started.

Read `CLAUDE.md` first, then the new package and your assigned module. The package supersedes conflicting scope, architecture, sequencing and acceptance instructions in this original handoff and the older research specs. Historical measurements/access inventory below are retained for context; they are not newly verified live state. Old branch/worktree claims must be checked, not assumed.

## 0. Current objective and accepted changes

Deliver `apps/app` as the **free consumer inference platform**: public verified signup, 10,000 promotional credits once per individual user, catalog, API keys, exact reservations/settlement, durable inference and own usage. Build `apps/lab` separately for provider models, dev/prod endpoints, authorized traces, evaluation and later data/improvement workflows. Shared runtime remains durable; App launch does not wait for Lab judge or provider trace UI. Payments/OpenRouter remain later decisions; fleet follows measured pilot evidence.

Requests remain synchronous unless async is explicitly requested. PostgreSQL owns acceptance, leases, stream journal and accounting; Valkey is rebuildable scheduling state. Inference continues when optional trace capture drops, with loss metrics and fsync-defined durability. Retention is24h results,7d processing cache, up to90d full trace content and13mo metadata. New CREDIT grants are unique by individual user, not organization. Historical USD balances are preserved separately; no implicit conversion or retrocharge. See [credit policy](research/platforms/02-credits.md).

The [contracts](research/plan/01-contracts.md), [durable protocols](research/plan/02-durable-protocols.md) and [release gates](research/plan/04-verification.md) are authoritative. Sections4–10 below describe the original research/planning input, not the current execution backlog. Do not implement superseded examples or repeat already-completed fixes.

## 1. Historical live inventory (2026-09-20; revalidate before operations)

| thing | where | notes |
|---|---|---|
| Public inference API | `https://marlin2b.callbill.ai` (OpenAI-compatible) | Caddy (TLS, docker) → `apps/infrx-api/gateway.py` on `127.0.0.1:8001` → vLLM nightly on `127.0.0.1:8000` serving `NemoStation/Marlin-2B` with `--hf-overrides` (see `models/marlin2b/serve.sh`). systemd units `marlin2b-vllm`, `marlin2b-gateway`; installer `apps/infrx-api/deploy/install.sh`. |
| GPU dev box | EC2 `i-0e8449a4ffca29bab`, `g6e.2xlarge` (1× L40S 48 GB, 8 vCPU), us-east-1d, Deep Learning AMI (Ubuntu 24.04, driver 595, PyTorch env at `/opt/pytorch`, NVMe at `/opt/dlami/nvme`) | Elastic IP `100.57.145.167` (`eipalloc-037e19cc644820961`). Security groups: original + `marlin2b-gateway` (80/443). **Running ≈ $2.24/h.** Weights at `/opt/dlami/nvme/marlin2b`, logs at `/opt/dlami/nvme/logs/` (`serve.log`, `usage.jsonl`, `usage_failed.jsonl`), repo clone at `/home/ubuntu/model-inference` on `main`. |
| Customer console | `https://app.callbill.ai` | Next.js 16 (App Router, Tailwind 4, shadcn base-nova) in `apps/app`, Vercel project `infrx-app` (`prj_W8JNBx71exKW6iEPBaALx1R9IxKn`) in **gideon@callgideon.com**'s personal Vercel scope, Git-linked to `callgideon/model-inference`, root `apps/app`, auto-deploys on push to `main`. |
| Database + auth | Supabase project `fcbnscgsymzdykendbrc` (**us-east-2**) | Schema `apps/app/supabase/migrations/0001_init.sql` (+ seed `0002`) applied. Email/password auth, **public signup disabled**, password min 6, Google provider disabled (its redirect URI was never added to the Google client). Auth email goes through **AWS SES** from `login@callbill.ai` (domain `callbill.ai` verified with DKIM; send-only address). |
| Accounts | `dev@callbill.ai` (owner of org "dev"); `e2e-test@callbill.ai` (historical test account) | Obtain credentials through the authorized secret/admin flow; no password values are stored in this handoff. See `apps/app/supabase/README.md`. |
| DNS | Route 53 zone `callbill.ai` (`Z03431581IYCMMS6JWE78`) | `marlin2b.callbill.ai` A → EIP; `app.callbill.ai` CNAME → `cname.vercel-dns.com`; DKIM CNAMEs; `_dmarc` (p=none). `callgideon.com` zone `Z0255467Y94CH1V482H6` is unused by this project. |
| Weights mirror | `s3://llm-bootcamp-641134885443/weights/` | only `deepseek-v41` mirrored; Marlin comes from Hugging Face (gated; account approved). |

Verified end to end on 2026-09-20: console sign-in → key created in `api_keys`
→ request to `marlin2b.callbill.ai` with that key → `usage_events` row with
tokens, timings, `cost_usd`, key `last_used_at`. Unknown keys get 401; internal
addresses in `video_url` get 400 `blocked-address`.

**Not live:** any queue (429 above `MAX_INFLIGHT=16`), any second GPU, any
trace with content, any feedback or judge, any payment path, `/metrics`, an
ALB, an AMI. The GPU box is a single point of failure and costs ~$54/day;
stop it (`aws ec2 stop-instances --instance-ids i-0e8449a4ffca29bab`) when
idle — the EIP and DNS survive a stop/start, the instance-store NVMe
(weights, logs, `usage_failed.jsonl`) does **not**.

## 2. Secrets and access (names only; values in AWS SSM Parameter Store, us-east-1)

| parameter | use |
|---|---|
| `/model-inference/hf_token` | Hugging Face token approved for `NemoStation/Marlin-2B` |
| `/model-inference/marlin2b_api_key` | legacy gateway key (still accepted; `GATEWAY_API_KEY`) |
| `/model-inference/supabase_url`, `/model-inference/supabase_service_role_key` | what `install.sh` writes into `/etc/marlin2b-gateway.env` |
| `/INFRX-SUPABASE-PROD/url`, `publishable_key`, `secret_key`, `db_password`, `access_token` | Supabase: anon/publishable key (Vercel env `NEXT_PUBLIC_SUPABASE_ANON_KEY`), secret key (`SUPABASE_SERVICE_ROLE_KEY`), Postgres password, personal access token for the Management API |
| `/INFRX-SUPABASE-PROD/smtp_user`, `smtp_pass` | SES SMTP credentials (IAM user `infrx-supabase-smtp`, send-only) configured on the Supabase project |
| `/callgideon/prod/GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | the callgideon Google OAuth client (currently unused by this project) |

Access quirks that cost time before:
- The shell on the admin host exports **stale** `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`. Always run
  `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws …` (default profile = `sofia-admin`, account `641134885443`).
- No `session-manager-plugin`; run remote commands with `aws ssm send-command --cli-input-json file://…` (write the JSON with python to avoid shorthand-parser breakage) and read results with `get-command-invocation`. Detach long jobs with `setsid nohup … &`.
- The Supabase CLI login on the admin host belongs to a different account and cannot see this project. Apply migrations with `supabase db push --db-url "postgresql://postgres.fcbnscgsymzdykendbrc:<url-encoded password>@aws-0-us-east-2.pooler.supabase.com:5432/postgres"` (the direct host is IPv6-only). Auth settings: `PATCH https://api.supabase.com/v1/projects/fcbnscgsymzdykendbrc/config/auth` with the access token.
- Vercel must be operated from the **Gideon** account (the Claude Vercel connector must be authorized for the `callgideon` scope; the Sofia account cannot create projects in the CallSofia team).
- Web search budget in Claude Code sessions runs out around 200 calls; research agents fell back to direct fetches.
- New AWS resources the plans call for and that do **not** exist yet: S3 buckets `infrx-media` and `infrx-traces`, the `infrx-obs` EC2 (ClickHouse + judge), ElastiCache Valkey, ALB/ACM/WAF, ODCR, the baked AMI, an Anthropic API key in SSM for the judge.

## 3. Repository map

```
CLAUDE.md, README.md, HANDOFF.md (this file)
models/common/            download.sh + env.sh (shared)
models/<exp>/             deepseek41f, deepseek41fnvfp4, qwen3827b, kimik3, marlin2b (model.env, download.sh; marlin2b also serve.sh, smoke.py, bench.py, reference.py, tokens.py, results/)
apps/README.md            console + gateway spec (requirements F1–F10, data model, deployment)
apps/app/                 consumer App; shared migrations remain here initially
apps/lab/                 provider Lab README scaffold; actual app not yet created by this plan
research/platforms/       current two-product architecture, specs and roadmaps
apps/infrx-api/           gateway.py (compatibility entry point; the gateway code was extracted to infrx/ by task F1), infrx/ (auth, media, usage, gateway app factory, contracts v1), tests/ (legacy gateway tests + tests/contracts + per-track suites), deploy/ (systemd units, Caddyfile, install.sh, replay_usage.py), openrouter/ (provider document + listing plan), client_example.py
research/                 METHODOLOGY.md (formulas, units, pinned inputs) and the trees below, each with a README index
  gpus/, cross-cutting/, models/<exp>/, matrix/   per-GPU × per-model sizing, costs, recommendations (the 8×B300 destination)
  scaling/                bare-metal cluster serving research + blueprint (10) + playbook (11) + providers (12)
  platform/               closed-loop distillation platform research (goal 00 … thesis memo 11) — the product thesis
  inference-platform/     product/market framing and P0/P1/P2 priorities for the model-to-API platform (user-written)
  production-api/         PROGRAM A — robust inference on AWS: 01–08 research, 09 blueprint, 10 implementation spec
  traces/                 PROGRAM B — deep traces: 01 requirements … 08 phases and test plan
```

Branch convention: future module implementations use isolated `codex/<task>-<slug>` branches from a coordinator-recorded committed base. Follow [worktree rules](research/plan/03-execution-protocol.md). Never merge an experiment branch into `main`, auto-sync unrelated experiment branches or alter another session's worktree.

## 4. Measured facts (L40S, vLLM nightly pulled 2026-09-19)

From `models/marlin2b/results/notes.md` and `bench.jsonl` (all four rows committed):

| clip | concurrency | prompt tokens | TTFT p50 | TPOT | clips/s |
|---|---|---|---|---|---|
| sample-10s (1080p, 5.5 MB) | 1 | 2,061 | 0.77 s | 6 ms | 0.50 |
| sample-10s | 8 | 2,061 | 3.35 s | 8 ms | 1.57 |
| sample-10s, processor default budget | 8 | 12,221 | 3.70 s | 8 ms | 1.47 |
| Big Buck Bunny (360p, 1 MB) | 8 | 1,928 | 0.66 s | 7 ms | 3.58 |

What they mean: per-request CPU video decode/download is the bottleneck, not
the LM ("a CPU-I/O-bound multimodal service wearing an LLM costume"); the
model's training video budget must be set per request
(`size.longest_edge = frames × 200,704`, done server-side by the gateway);
cost ≈ **$0.06–0.14 per video-hour** on g6e.2xlarge. **Caveat:** every row
reused one clip with vLLM's multimodal cache on, and rows were taken against
vLLM directly, not through the gateway — Program A's Phase 0 re-measures on
distinct clips through the gateway, and **every capacity number in both
programs is provisional until it does.** No end-to-end cold start has ever
been measured.

## 5. What the gateway does today (`apps/infrx-api/gateway.py`)

Bearer key → SHA-256 → Supabase `api_keys` lookup (60 s positive / 10 s negative
cache, bounded; legacy key via `hmac.compare_digest`; Supabase down → cached
keys served, unknown keys 503) · video fetched **once** with SSRF guards
(private/loopback/link-local/metadata blocked, redirects re-validated ≤3 hops,
streaming size cap 64 MB, content-type allowlist, 30 s budget), probed with
ffprobe, ≤120 s, and handed to vLLM as an inline `data:` URL with the
training-budget `mm_processor_kwargs` · `429` above `MAX_INFLIGHT=16`
(process-local, checked before the media stage — a known weakness) · leading
`<think>` stripped · `Inference-Id` header · `usage_events` row per request
(async queue, retry, spill to `usage_failed.jsonl`, `replay_usage.py`) ·
`/v1/models` serves the OpenRouter provider document · `/health`.

Known gaps carried into the plans (Program A blueprint §6.6 / §2.8): limiter
placement, in-process (non-durable) admission, `/health` echoes upstream error
text, `asyncio.create_task` results not retained, no `MAX_REQUEST_BYTES`
before `req.json()`, unbounded ffprobe executor, a streaming slot leak if the
client disconnects before the body is iterated, no row for a 429 (a rejection
is invisible), no `/metrics`. Both programs modify this file; §7.3 lists the
shared seams.

## 6. Objectives and gates (from `research/inference-platform/02-priorities.md`)

### 6.1 P0 — a trustworthy paid Marlin API

Twenty capabilities with acceptance evidence, of which the two programs cover:

| P0 capability | Covered by |
|---|---|
| Serving recipe (pinned digests, parity suite, rollback) | A: Phase 0 (pin), Phase 5 (AMI, Replace-Root-Volume) |
| API contract, video input, capability honesty | live today + A: Phase 2 (`max_tokens` enforced, typed refusals, `402`), Phase 3 (jobs, uploads) |
| Accounts, API keys, entitlements | live (console) — entitlements = model allowlist per org is **not** specced anywhere |
| Rate and capacity limits | A: Phase 2 token buckets per org (requests/min, video-seconds/min); per-key limits ⚠️ not specced |
| Queue/admission, per-tenant fairness | A: Phase 2 (bounded WFQ queue, honest ETA, `Retry-After`) |
| Pricing and metering (versioned rates, exact usage) | live (`cost_usd` at request time) + B (`price_table_version`); **pricing change itself** (A §0.1 pt 9: no profitable utilisation at $0.10/$0.30) is a decision nobody has taken |
| Credits and payments, hard spend protection | **not specced** (Stripe, holds, settlement) — console shows placeholders |
| Customer usage | live (Usage page) |
| Request observability (id, timings, tokens, version, status) | live (metadata) + B: Phase 1 (`traces` rows, `artifact_id`) |
| Input/output inspection (project-controlled capture, retention, deletion, audit) | B: Phases 2–3, 6 |
| Operational visibility (error rate, queue, TTFT, GPU, readiness, alerts) | A: Phase 3 (`/metrics`, 15 alerts, dashboards) |
| Cost tracking (AWS cost allocated per model) | A: `08-cost-model` numbers; no tooling specced |
| Developer onboarding | live (Docs, snippets) |
| Operator controls (suspend, adjust credits, replay) | partly live (`/admin`); suspend-tenant ⚠️ not specced |
| Production basics (private workers, secrets, backups, health gates, incident contact) | A: Phases 3–5; B: Phase 6 |

### 6.2 P1 (later) — repeatable platform: second private model, model-owner onboarding, stable endpoint/version model, async jobs, autoscaling maturity, OpenRouter listing (`apps/infrx-api/openrouter/`), better observability. Program A's Phases 3–5 and Program B together deliver most of "async jobs", "autoscaling", "better observability".

### 6.3 P2 — expansion; includes "model improvement: feedback datasets, evaluations, A/B tests, fine-tuning and distillation" — the closed loop of `research/platform/`, for which Program B lays the S2 (traces) foundation deliberately.

### 6.4 Release gates — the definition of "platform live"

1. **Serving gate:** unique mixed-duration clips, cold and warm paths, sustained concurrency, cancelled streams, invalid videos, long-context boundaries tested; quality parity with `reference.py`.
2. **Paid pilot gate:** payment → credit → authenticated call → usage → charge → trace, end to end; duplicate webhook/event, overspend race, cross-tenant and recovery drills pass.
3. **Platform gate:** a second owner deploys a supported model without new gateway or billing code.
4. **Distribution gate:** partner (OpenRouter) validation and reconciliation pass.

The plan must state which gate each phase advances and which gate-2 items
(payments, holds) are still unspecced and need a design spec of their own.

## 7. The two specification programs

Read in this order: `research/production-api/README.md` → `09-blueprint.md`
→ `10-implementation-spec.md`; then `research/traces/README.md` →
`01-requirements.md` → `03-architecture.md` → `08-phases-and-test-plan.md`;
then the detail documents as needed. Both trees are fact-checked with
per-document verification logs and `⚠️ TO BE VERIFIED` markers; treat every
⚠️ as a task to close, early.

### 7.1 Program A — robust inference on AWS (`research/production-api/`, 2026-09-20)

**Thesis** (blueprint §0.1): the service is CPU/I-O bound, so the architecture
is ordered by how much CPU work it removes from the request path; shape is
**ALB → stateless gateway fleet → Valkey queue → queue-pull GPU workers (ASG,
multi-AZ, mixed g6e sizes, ODCR floor)**; "no dropped requests" = durable
admission before acknowledgement + a terminal state for everything admitted +
a typed refusal with an honest `Retry-After`; scale-out takes 5–9 min today
(100–200 s with a baked AMI) so bursts are absorbed by the bounded queue and
headroom, never by scaling; transcode to ≤480p/2 fps at ingest is the largest
throughput lever (up to 2.28×, paying at 0 % cache hit); NAT topology is the
largest cost lever; nine of the ten highest-value actions are code in
`gateway.py` and flags in `serve.sh`.

**Decisions already made — do not relitigate without new data** (blueprint §0.2):
ALB idle timeout 180 s + SSE keepalives + 202-upgrade; TTFT p95 SLO 6 s (clips
≤30 s at ≤720p); fetch connect 3 s / total 20 s / ≤3 revalidated redirects;
**no warm pool** (g6e cannot hibernate; buy an ODCR, run the spare warm); one
ElastiCache Valkey replication group holds queue, leases, job records, token
buckets and media-cache index — **no SQS on the interactive path**;
`async_upgrade_enabled` defaults on; `min-size 1`, N+1 only at N ≥ 3;
`WORKER_BUDGET_VIDEO_SECONDS = 120`; ALB deregistration delay 900 s;
`ENGINE_MAX_NUM_SEQS=8` / `WORKER_CONCURRENCY=10` until B0.2 re-pins them;
`REDIS_URL` unset ⇒ in-process queue (single-box mode keeps working).

**Target module layout** (spec 10 §1): `gateway/` (app, auth, admission,
endpoints_chat/jobs/meta/uploads, sse, dispatcher, reaper), `worker/` (loop,
vllm_client, canary), `queue/` (protocol, redis_queue, memory_queue, estimate,
buckets, idempotency, Lua scripts), `media/` (fetch, probe, transcode, cache,
budget), `shared/` (config, ids, models, metrics, usage, errors, log). Working
code is lifted verbatim (`authenticate`, `enqueue/ingest/spill`, `THINK`,
`budget_kwargs`, the SSRF fetch), not rewritten.

**Phases** (blueprint §8; each exit is a measurement):

| Phase | Content | Exit |
|---|---|---|
| **0 Measure & fix** | `bench.py` gains `--distinct`, `--rate`, `--form`, `BASE_URL` per row; B0.1 32 distinct clips through the gateway (1080p, 480p); B0.2 concurrency sweep → pin `--max-num-seqs`/`WORKER_CONCURRENCY`; B0.3 120 s + mixed clips → `WORKER_BUDGET_VIDEO_SECONDS`; B0.4 measured cold start ± seeded compile cache; B0.5 L40S usable memory → `research/gpus/l40s.md`; B0.6 EC2 quotas (`L-DB2E81BA`, `L-3819A6DF`) and g6e AZs; gateway fixes (inflight drift, bounded probe executor, `MAX_REQUEST_BYTES`, generic 424); pin vLLM digest; compile cache on EBS root; `--max-num-seqs 8`; `ExecStop=docker stop -t 120`; the **g6e.4xlarge** test (16 vCPU) is the highest-value single experiment | clips/s on distinct clips within 20 % of single-clip numbers or tables revised; knee identified; cold start has a number |
| **1 Media path** | `media.py`: fetch → probe → sha256 → transcode ≤448 px @ 2 fps → NVMe LRU + S3 (`infrx-media`, VPC endpoint, 7-day lifecycle) → `file://` to vLLM (`--allowed-local-media-path`; base64 fallback ⚠️); `serve.sh` `--mm-processor-cache-gb 8`, `--max-num-batched-tokens 16384`; single-flight per hash; cache keyed `(org_id, sha256)`; `cache_salt = org_id` | caption parity with `reference.py`; ≥1.8× the Phase-0 1080p number; TTFT p50 on a URL clip < 1.5 s |
| **2 No-drop on one box** | `queue.py` (bounded, WFQ, leases, ETA, token buckets; in-process without Redis), `usage.py`, `config.py`; `MAX_INFLIGHT` removed; admission per §2.3 with `X-Infrx-*` headers, jittered `Retry-After`, keepalives, cancellation, per-phase timeouts, `402` on exhausted credits; `usage_events` += queue/ETA/outcome columns, `organizations.limits`; console Usage/Docs updated | A8 (50 req/s × 300 s), A19, A6 (restart mid-flight), A9 (idempotency): every request gets a status or durable job id within 2 s; every `preparing` reaches a terminal state; ETA ±30 % p50, never understating at p95; `usage_events + rejections == arrivals` |
| **3 Observability & async** | `/metrics`, `/livez`, `/readyz`, `/warm`, `Server-Timing`; `POST /v1/jobs`, `GET /v1/jobs/{id}`, `/events` (resumable SSE), `DELETE`, `POST /v1/uploads` (presigned PUT); watchdog; logrotate; auto-replay timer; Prometheus → Grafana Cloud, 15 alerts, burn-rate rules | four-row dashboard live; `docker pause` of vLLM detected and recovered within 2.5 min |
| **4 Front door** | ALB across ≥2 AZs (idle 180 s, `/readyz` target group, WAF, access logs), ACM, delete Caddy, gateway on `0.0.0.0:8001` in the SG, DNS alias (decide the Elastic IP question with callers first) | A15 (120 s clip with 90 s queue wait, streaming and not); zero 5xx during a drain-restart at 6 req/s |
| **5 Fleet** | baked AMI (vLLM digest, weights, compile cache, ffmpeg, pinned deps on EBS root), launch template + ASG + lifecycle hooks + four scaling policies + scheduled floor, targeted ODCR floor+1 across two AZs, ElastiCache Valkey, Secrets Manager, `worker.py` as `marlin2b-worker.service` | A7 (kill one of two replicas at 6 req/s: zero client 5xx, re-dispatch < 30 s); cold start p95 < 200 s; `ReplaceRootVolume` refresh with auto-rollback |
| **6 Economics** | pricing-unit migration, response cache (after a content-retention decision — now made by Program B), 1-year Compute Savings Plan sized to the floor, price rows for g6e/g6/g5/c7i in `cloud-pricing.md`, B6.1 benchmark `g6.2xlarge` (L4) and `g5.2xlarge` (A10G) — break-even 1.56 / 1.94 clips/s | margin positive at the pilot scenario |

Test plan (spec 10 §12): unit suites `test_queue_contract.py` (Q1–Q11,
parametrised over both queue implementations), `test_admission.py` (A1–A8),
`test_media.py` (M1–M13, several exist), `test_state_machine.py`; integration
against `fake_vllm.py` (I1–I15 — **I9 "zero connections closed without a
response" defines the product**); open-loop load tests (L-series).

### 7.2 Program B — deep traces (`research/traces/`, 2026-09-20)

**Decisions already made** (`01` §2, D1–D8): platform trace store from day
one — **own ClickHouse (self-hosted in docker on a small EC2 `infrx-obs`) + S3
`infrx-traces`**, no Langfuse/SaaS; own Traces page in the console; opt-in
**per API key** `trace_level ∈ {off, metadata, full}` (new keys `metadata`)
with a per-request `X-Infrx-Trace` header that can only lower; v1 includes
`POST /v1/feedback` and an **async LLM-as-judge** (`claude-opus-5` via the
Message Batches API, structured output, sampled frames as images) under a
per-org daily USD budget and a **separate egress consent** (`judge_enabled`
per key ∧ `judge_egress_ok` per org); capture approach C = spool-and-ship
from the gateway (same pattern as `usage_events`: `put_nowait` → worker →
local spool → S3 blob → ClickHouse row → retry → parked file → replay), with
vLLM OTLP engine spans joined later through `X-Request-Id`; documents first,
then small verifiable phases.

**Hard requirements:** NF1 hot path ≤ 1 ms p50 / ≤ 3 ms p99 / 0 ms TTFT,
measured by A/B with `bench.py` (drill H1); NF2 no trace lost across a ≤24 h
store outage or a graceful restart; NF3 content byte-exact, never truncated,
media by sha256 reference never inline; NF4 `org_id` in every key and every
query; NF10 no new package on the request path.

**Key facts found:** vLLM `--enable-prompt-tokens-details` returns
`prompt_tokens_details.{cached_tokens, created_cache_tokens, multimodal_tokens}`
(the cheap detector for the 1.9× video token-budget fork; ⚠️ verify populated
for video on the pinned nightly); `_base_request_id` honours `X-Request-Id`,
so vLLM's response id becomes `chatcmpl-<Inference-Id>`; `--enable-request-id-headers`
echoes it. Neither flag is on today.

**Data model** (`04`): ClickHouse `infrx.traces` (ReplacingMergeTree, `ORDER BY
(org_id, ts, id)`, monthly partitions, 13-month TTL), `scores` (append-only,
keyed by trace id — traces never mutate), `judge_runs`, `judge_items`; five
least-privilege users; S3 `content/{org}/{yyyy}/{mm}/{id}.json.gz` (per-org
retention by object tag), `media/`, `frames/`, `backups/`; Supabase migration
`0003_traces.sql` adds `api_keys.trace_level/judge_enabled` and the org's
retention/judge/consent columns.

**Phases** (`08`; each with tests, a measured exit and a rollback; ≈ 29 engineering days `est.`):

| Phase | Content | Exit |
|---|---|---|
| **0 Foundations** | vLLM flags + `ARTIFACT_ID`; migration `0003`; `infrx-obs` (docker compose, DDL, users, backup/restore drill); `infrx-traces` bucket + IAM; baseline bench | one curl shows `chatcmpl-<id>` and `multimodal_tokens > 0`; DDL applies; restore drill passes; IAM denies what it should |
| **1 Metadata rows** | `traces.py` (row only): level resolution, `assemble_row`, worker, ship, replay; gateway wiring so **every** status path emits; tests G-series | `count(traces) == count(usage_events)` after an outage + replay (H2); H1 within NF1; no duplicate ids after `kill -9` + merge (H3) |
| **2 Full content** | content blob (messages with media refs, output, `<think>` captured before the strip, tool calls raw+parsed, `schema_valid`), S3 put, `metadata` validation, `media_copy`; console key toggle (C3) | byte-exactness invariants on 100 real traces; H1 at `full`; H4 QueueFull drops counted, no disk on the request path |
| **3 Viewer** | Caddy token proxy → read-only ClickHouse user; `lib/clickhouse.ts` (org-bound), server-side SigV4 blob fetch, presigned frames; Traces list, detail (waterfall, tokens, prompt, output, labelled reasoning, cURL), docs, settings (part) | turn on → request → see it in 60 s; 10k-row org lists < 1 s p95; crafted cross-org id → 404 (H6) |
| **4 Feedback** | `POST/GET /v1/feedback`, `GET /v1/traces` export; console thumbs/rating/correction via a writer token; score tiles | a console thumb visible via the API and vice versa |
| **5 Judge** | `judge.py` on `infrx-obs`: selection (100 % of `length`/schema-invalid/user-scored, 5 % uniform), frames via ffmpeg, rubric, batch submit/collect, egress gate, budget; dry-run first; consent UI; admin Judge tab; calibration on ~50 human labels | dry run selects exactly what J1 predicts with no egress; live run ≥ 95 % success, cost within 25 % of estimate; every judged trace joins to a consenting key+org (H7) |
| **6 Operations** | TTL/lifecycle drills, tenant deletion script + audit, counters/alerts, logrotate, replay timer, runbooks | O1–O5 |

Test series: G1–G22 (`05` §11), Q1–Q11 + QI1 (`06` §5), U1–U4 + E1 (`07` §9), H1–H7 live drills (`08` §9).

**Cost** (`03` §7, `est.`): infra ≈ $75–85/mo at pilot (⚠️ EC2/EBS unsourced);
judge ≈ $1,480/mo at 1M req/mo × 5 % sampled with Opus 5, 8 frames, batch —
the dominant line, hence the per-org budget knob.

### 7.3 Seams between the programs — the plan must sequence these explicitly

| Seam | Program A owns | Program B owns | Rule |
|---|---|---|---|
| `gateway.py` hot path | Phase 0 fixes, Phase 2 admission | Phase 1 `finish()` emit, `X-Request-Id`, sha256 | A's Phase 0 lands first if both are open; B is additive and must not add a hot-path `await` |
| Media stage | Phase 1 `media.py` / `MediaRef{sha256, s3_key, hit}` | trace `media_*` columns | whichever lands second adopts the other's seam; B computes sha256 itself until `MediaRef` exists |
| Record seam / job id | Phase 2 `shared/usage.py`, `Inference-Id` = uuid half of `job_…` | `traces.py` | `traces.py` moves under `shared/` as a `git mv`; the trace is a projection of `Job`; UUID column unchanged |
| `/metrics` | Phase 3 registry, 15 alerts | `infrx_traces_*` counters, 4 alerts | B registers on A's registry; before it exists, JSON log lines |
| `usage_events` schema | Phase 2 adds queue/ETA/outcome columns; "every arrival gets a row" | `traces` row per attributable request | both must hold `rows + refusals == arrivals`; one migration file each, ordered |
| Response cache (A Phase 6) | needs a content-retention decision | `request_hash`, `prompt_hash` on every trace, retention policy per org | B's decision unblocks A's Phase 6; the cache reads B's columns |
| S3 / IAM / VPC endpoint | `infrx-media`, VPC endpoint, instance-role policy | `infrx-traces`, console IAM user, obs role | one IAM change set, one VPC endpoint |
| Engine spans (later) | Phase 3 collector (if any) | `engine_spans` table joined on id | B's T9 lays the key now |
| Console | Usage/Docs changes (queue, headers, 429 semantics) | Traces, Settings, API-keys toggles, admin Judge tab, docs "Traces" | one sidebar, one docs page, one migrations sequence |

### 7.4 Research behind the programs (read only when a decision needs its reasoning)

- `research/platform/` (2026-09-19) — the closed-loop thesis: S1 traffic → S2 traces → S3 annotation → S4 datasets/evals → S5 training → S6 checkpoint → S7 gate → S8 hardware optimisation → S9 online A/B; invariants I1–I7; `01-observability-and-tracing.md` is the trace-schema source Program B adopts (its §6.3 "adopt Langfuse" is superseded by D2); `04` judge validity; `10` MVP and its ten exit criteria; `11` thesis memo.
- `research/inference-platform/` — market, competitors, and the P0/P1/P2 priorities of §6.
- `research/scaling/` — the 8×B300 bare-metal destination (blueprint `10`, playbook `11`, providers `12`); `est.` Marlin numbers, superseded by measurements where they disagree.
- `research/matrix/`, `gpus/`, `models/`, `cross-cutting/` — per-model × per-GPU fit/cost; `METHODOLOGY.md` is the single source of formulas and pinned prices; prices come only from `cross-cutting/cloud-pricing.md`.

## 8. What the planning session must produce

One document set (suggested home: `research/plan/` with a README, or a
top-level `PLAN.md` plus per-task files — the planner decides and records
why) that a series of execution sessions can implement **without re-deriving
anything**. It must contain:

1. **A unified phase sequence** merging A0–A6 and B0–B6 into one ordering
   that respects §7.3, front-loads measurements (A0, B0) and ⚠️ closures, and
   states for each phase which §6.4 gate it advances. Nothing decided in §7.1
   or §7.2 is reopened without new data; an open question is closed by a
   named task, not by assumption.
2. **Parallel tracks** with an explicit dependency graph. The natural tracks
   are: *gateway hot path* (one file, serialised), *media/transcode*,
   *queue/worker*, *AWS infrastructure* (buckets, IAM, VPC endpoint, obs box,
   Valkey, ALB, AMI, ODCR), *console* (Next.js), *trace store + judge* (obs
   box), *benchmarks and drills*, *docs and runbooks*. Two tracks may run in
   parallel only if they touch disjoint files or the plan names the merge
   order.
3. **Per-task briefs**, each self-contained for a fresh execution session:
   objective; the spec sections it implements (document + § + requirement
   ids); files to create/change with the exact seams; config/env/flags;
   the tests to write first (ids from the spec series) and the command that
   runs them; the measured exit criterion; the deploy/verify procedure on the
   live box (SSM commands, what to look at); rollback; what to record where
   (`results/notes.md` "Measured" notes, verification logs, this file's §1).
   Task size: one session, one PR, green tests, one measurement.
4. **Closed-loop verification** as a standing rule: every task ends with the
   unit suite green, the integration suite against `fake_vllm.py` green where
   applicable, the drill or benchmark that is its exit criterion run and
   recorded, and H1 re-run whenever the request path changed.
5. **The unspecced gate-2 items** (payments/credits/holds, per-key rate
   limits, entitlements, suspend-tenant, pricing change, cost allocation)
   listed with a recommendation for each: specify now (and where), or defer
   past "platform live" with the reason.
6. **A "platform live" checklist** derived from §6.4 gates 1 and 2 plus
   Program A's I9 and Program B's success criteria (`traces/01` §8), with the
   drill that proves each line.
7. **Risk register**: the ⚠️ items of both trees (`production-api/README.md`
   §6 open questions 1–13, `traces/README.md` §6), the single-box SPOF, GPU
   capacity scarcity in us-east-1, the vLLM nightly pin, the unsourced prices,
   and the judge's unvalidated rubric — each with the task that retires it.
8. **Execution protocol** for the handoff to implementation sessions: which
   branch/worktree per task, how `main` is updated (fast-forward or PR),
   where a session reports outcomes (this file §1 for live state, the spec's
   verification log for measured facts, `results/notes.md` for numbers), how
   a session stops when a measurement contradicts a spec (record, do not
   patch the spec silently), and the attribution/commit conventions of
   `CLAUDE.md`.

Constraints on the plan itself: no implementation in the planning session;
every number quoted carries its source document; `est.` stays `est.` until a
drill replaces it; effort estimates are labelled as such; the plan's own
document ends with a verification log listing what it cross-checked.

## 9. Known gaps and risks carried in (consolidated)

- **Every capacity number is cache-inflated until A-B0.1** (distinct clips through the gateway).
- **No measured cold start** — sizes `InstanceWarmup`, health-check grace, lifecycle heartbeats (A-B0.4).
- **Single box, single AZ, instance-store logs** — any stop loses `usage_failed.jsonl` and any unshipped spool; `traces_failed.jsonl` must live on EBS (B).
- **vLLM nightly pin**: metrics and flags can vanish between pulls; pin the digest (A-P0) and gate metric names (A `07` §6.4).
- **`multimodal_tokens` for video** unverified (B-S0.1); **`file://` media under `--hf-overrides`** unverified (A-P1); **`output_config.format` inside batch params** unverified (B `06` §3.4).
- **Pricing**: no profitable utilisation at $0.10/$0.30 per 1M on the 1080p path (A `08` §4.2) — a product decision, not an engineering task.
- **Product question with the largest leverage** (A `09` §10.2 item 13): require presigned-S3 uploads instead of arbitrary URLs — removes most of the SSRF and egress surface at once. Unresolved.
- **Payments/credits/holds**: not specced; gate 2 needs them.
- **Vercel → ClickHouse** goes through a bearer-token proxy in v1 (B `07` §1); a private path is later.
- **Judge egress** sends customer prompts and frames to Anthropic; consent, budget and audit are designed (B `06` §3.6) but the rubric is a draft until calibrated on ~50 human labels.
- **EC2/EBS prices** for `infrx-obs` and the g6e family variants are unsourced or missing from `cloud-pricing.md`.
- OpenRouter listing (`apps/infrx-api/openrouter/`), Google sign-in (commit `ad575a8` has the button), `NEXT_PUBLIC_APP_URL` unused, Stripe not integrated, SES reputation fresh — open items from before, not blocking.

## 10. Conventions the plan and the execution sessions inherit

- `CLAUDE.md` is authoritative: `research/` rules (capacities as deployed, `⚠️ TO BE VERIFIED` with method, `est.`/`meas.`, every doc ends with a verification log you append to, prices only from `cloud-pricing.md`), branch convention, commands.
- Tests: `pytest` files that also run as plain `python3 <file>` with asserts, no network (`httpx.MockTransport` fakes), matching `apps/infrx-api/tests/test_gateway_auth.py`; console tests are `*.test.ts` run by the existing `apps/app` runner.
- No new dependency on the gateway's request path; working code is lifted (`git mv`), not rewritten.
- One migration file per change, numbered, applied through the pooler URL.
- Measured facts go to `models/marlin2b/results/notes.md` and the relevant spec's verification log; live-state changes go to §1 of this file.

## 11. Command cheat sheet

```bash
A="env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws"
# run something on the box
python3 -c 'import json;json.dump({"InstanceIds":["i-0e8449a4ffca29bab"],"DocumentName":"AWS-RunShellScript","Parameters":{"commands":["cd /home/ubuntu/model-inference && sudo -u ubuntu git pull -q origin main && ./apps/infrx-api/deploy/install.sh"]}},open("/tmp/ssm.json","w"))'
CMD=$($A ssm send-command --cli-input-json file:///tmp/ssm.json --query Command.CommandId --output text); sleep 20
$A ssm get-command-invocation --command-id $CMD --instance-id i-0e8449a4ffca29bab --query '[Status,StandardOutputContent]' --output text
# call the API
KEY=$($A ssm get-parameter --name /model-inference/marlin2b_api_key --with-decryption --query Parameter.Value --output text)
MARLIN_API_KEY=$KEY python3 apps/infrx-api/client_example.py --raw --find "a white bus drives past"
# gateway + contract tests (pinned environment; see the root Makefile)
make api-env && make api-test        # `make check` runs every canonical target
# console
cd apps/app && pnpm install && cp .env.example .env.local && pnpm dev   # fill keys from SSM
# benchmark through the gateway (Phase 0 of both programs)
BASE_URL=https://marlin2b.callbill.ai/v1 MARLIN_API_KEY=$KEY python3 models/marlin2b/bench.py -c 8 -n 32   # add --distinct once A-P0 lands it
```

## Documentation update log

- 2026-09-20: Added authoritative implementation package, incorporated review decisions, removed plaintext account password from this file and retained historical source context. No live infrastructure or application implementation changed.
- 2026-09-21: Repository map and test command refreshed after F1/F2 integration on `claude/infrx-impl`; live-state sections above remain historical and were not re-verified by this edit. The I1 inventory (`research/plan/evidence/i/`) and `infra/README.md` hold the re-observed state, including the installer fail-open hazard (`O-FAILOPEN`).
