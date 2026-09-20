# Handoff — state of the platform on 2026-09-20

For the next session, which implements the production API from
[`research/production-api/09-blueprint.md`](research/production-api/09-blueprint.md)
and [`10-implementation-spec.md`](research/production-api/10-implementation-spec.md).
Everything below is committed on `main` (head `8f1d2f6` at the time of
writing); nothing is pending in any working tree. Read `CLAUDE.md` first for
conventions, then this file, then the two documents above.

## 1. What is live right now

| thing | where | notes |
|---|---|---|
| Public inference API | `https://marlin2b.callbill.ai` (OpenAI-compatible) | Caddy (TLS, docker) → `apps/infrx-api/gateway.py` on `127.0.0.1:8001` → vLLM nightly on `127.0.0.1:8000` serving `NemoStation/Marlin-2B` with `--hf-overrides` (see `models/marlin2b/serve.sh`). systemd units `marlin2b-vllm`, `marlin2b-gateway`; installer `apps/infrx-api/deploy/install.sh`. |
| GPU dev box | EC2 `i-0e8449a4ffca29bab`, `g6e.2xlarge` (1× L40S 48 GB, 8 vCPU), us-east-1d, Deep Learning AMI (Ubuntu 24.04, driver 595, PyTorch env at `/opt/pytorch`, NVMe at `/opt/dlami/nvme`) | Elastic IP `100.57.145.167` (`eipalloc-037e19cc644820961`). Security groups: original + `marlin2b-gateway` (80/443). **Running ≈ $2.24/h.** Weights at `/opt/dlami/nvme/marlin2b`, logs at `/opt/dlami/nvme/logs/` (`serve.log`, `usage.jsonl`, `usage_failed.jsonl`), repo clone at `/home/ubuntu/model-inference` on `main`. |
| Customer console | `https://app.callbill.ai` | Next.js 16 (App Router, Tailwind 4, shadcn base-nova) in `apps/app`, Vercel project `infrx-app` (`prj_W8JNBx71exKW6iEPBaALx1R9IxKn`) in **gideon@callgideon.com**'s personal Vercel scope, Git-linked to `callgideon/model-inference`, root `apps/app`, auto-deploys on push to `main`. |
| Database + auth | Supabase project `fcbnscgsymzdykendbrc` (**us-east-2**) | Schema `apps/app/supabase/migrations/0001_init.sql` (+ seed `0002`) applied. Email/password auth, **public signup disabled**, password min 6, Google provider disabled (its redirect URI was never added to the Google client). Auth email goes through **AWS SES** from `login@callbill.ai` (domain `callbill.ai` verified with DKIM; send-only address). |
| Accounts | `dev@callbill.ai` / `devdev` (owner of org "dev"); `e2e-test@callbill.ai` (test user with a minted key, safe to delete) | Create more with the Supabase admin API (`POST /auth/v1/admin/users` with the secret key) — see `apps/app/supabase/README.md`. |
| DNS | Route 53 zone `callbill.ai` (`Z03431581IYCMMS6JWE78`) | `marlin2b.callbill.ai` A → EIP; `app.callbill.ai` CNAME → `cname.vercel-dns.com`; DKIM CNAMEs; `_dmarc` (p=none). `callgideon.com` zone `Z0255467Y94CH1V482H6` is unused by this project. |
| Weights mirror | `s3://llm-bootcamp-641134885443/weights/` | only `deepseek-v41` mirrored; Marlin comes from Hugging Face (gated; account approved). |

Verified end to end on 2026-09-20: console sign-in → key created in `api_keys`
→ request to `marlin2b.callbill.ai` with that key → `usage_events` row with
tokens, timings, `cost_usd`, key `last_used_at`. Unknown keys get 401; internal
addresses in `video_url` get 400 `blocked-address`.

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

## 3. Repository map

```
CLAUDE.md, README.md, HANDOFF.md (this file)
models/common/            download.sh + env.sh (shared)
models/<exp>/             deepseek41f, deepseek41fnvfp4, qwen3827b, kimik3, marlin2b (model.env, download.sh; marlin2b also serve.sh, smoke.py, bench.py, reference.py, tokens.py, results/)
apps/README.md            console + gateway spec (requirements F1–F10, data model, deployment)
apps/app/                 Next.js console; supabase/migrations; README with run/deploy notes
apps/infrx-api/           gateway.py, tests/ (23 tests, pytest or plain python), deploy/ (systemd units, Caddyfile, install.sh, replay_usage.py), openrouter/ (provider document + listing plan), client_example.py
research/                 METHODOLOGY.md (formulas, units, pinned inputs) and the trees below, each with a README index
  gpus/, cross-cutting/, models/<exp>/, matrix/   per-GPU × per-model sizing, costs, recommendations
  scaling/                bare-metal cluster serving research + blueprint (10) + playbook (11) + providers (12)
  platform/               closed-loop distillation platform research (goal 00 … thesis memo 11)
  inference-platform/     product/market framing for the model-to-API platform (user-written)
  production-api/         THIS IMPLEMENTATION'S SOURCE: 01–08 research, 09 blueprint, 10 implementation spec
```

Branch convention: `main` carries everything; experiment branches
(`marlin2b`, `kimik3`, …) exist but `marlin2b` was merged via PR #1 and all
branches are at or behind `main`. Work on `main` unless diverging serving
configs per experiment again.

## 4. Measured facts (L40S, vLLM nightly pulled 2026-09-19)

From `models/marlin2b/results/notes.md` and `bench.jsonl` (all four rows committed):

| clip | concurrency | prompt tokens | TTFT p50 | TPOT | clips/s |
|---|---|---|---|---|---|
| sample-10s (1080p, 5.5 MB) | 1 | 2,061 | 0.77 s | 6 ms | 0.50 |
| sample-10s | 8 | 2,061 | 3.35 s | 8 ms | 1.57 |
| sample-10s, processor default budget | 8 | 12,221 | 3.70 s | 8 ms | 1.47 |
| Big Buck Bunny (360p, 1 MB) | 8 | 1,928 | 0.66 s | 7 ms | 3.58 |

What they mean: per-request CPU video decode/download is the bottleneck, not
the LM; the model's training video budget must be set per request
(`size.longest_edge = frames × 200,704`, `--mm-kwargs auto` in the clients,
done server-side by the gateway); cost ≈ **$0.06–0.14 per video-hour** on
g6e.2xlarge (an earlier note said $0.02–0.04; that was a ÷1000 error, fixed).
**Caveat:** every row reused one clip with vLLM's multimodal cache on, and rows
were taken against vLLM directly, not through the gateway — Phase 0 re-measures.

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

Known gaps carried into the plan (blueprint §6.6 / §2.8): limiter placement,
in-process (non-durable) admission, `/health` echoes upstream error text,
`asyncio.create_task` results not retained, no `MAX_REQUEST_BYTES` before
`req.json()`, unbounded ffprobe executor, a streaming slot leak if the client
disconnects before the body is iterated.

## 6. The plan to implement (blueprint §8, spec 10)

Seven phases; each exit criterion is a measurement. Do them in order.

**Phase 0 — measure and fix what is broken (no new infra).**
Extend `bench.py` (`videos nargs=+`, `--distinct`, open-loop `--rate`, `--form b64|url`, record `BASE_URL` per row); run B0.1 (32 distinct clips, 1080p and 480p, through the gateway), B0.2 (concurrency sweep to find the knee → set `--max-num-seqs` and `WORKER_CONCURRENCY`), B0.3 (120 s and mixed-length clips → `WORKER_BUDGET_VIDEO_SECONDS`), B0.4 (measured cold start with/without a seeded compile cache), B0.5 (L40S usable memory + KV blocks → `research/gpus/l40s.md` stub), B0.6 (EC2 quotas `L-DB2E81BA`, `L-3819A6DF` and g6e AZ availability). Also: pin the vLLM image digest, mount the compile cache on the EBS root, `--max-num-seqs 8`, `ExecStop=docker stop -t 120`, `TimeoutStopSec` 180/930. The **g6e.4xlarge** test (16 vCPU, +34 % price) is the highest-value single experiment.

**Phase 1 — media path (target ≥1.8× on 1080p).** New `media.py`: fetch → probe → sha256 → transcode to ≤448 px @ 2 fps → NVMe LRU + S3 (`infrx-media` bucket, VPC endpoint) → hand vLLM a `file://` path; `serve.sh` gains `--mm-processor-cache-gb 8`, `--max-num-batched-tokens 16384`. Gate: caption parity with `reference.py` on both sample clips; exit: TTFT p50 on a URL clip < 1.5 s.

**Phase 2 — no-drop on one box.** New `queue.py` (bounded queue, weighted fair dispatch, leases, ETA estimator, token buckets; in-process when `REDIS_URL` unset), `usage.py`, `config.py`; remove `MAX_INFLIGHT`, admission per §2.3 with `X-Queue-*` headers, jittered `Retry-After`, SSE keepalives, cancellation, per-phase timeouts, `402` on exhausted credits; migrations add queue/ETA/outcome columns to `usage_events` and `limits` to `organizations`; console Usage/Docs updated. Exit: acceptance tests A8 (50 req/s × 300 s), A19, A6 (restart mid-flight), A9 (idempotency): every request gets a status or durable job id within 2 s, every `preparing` reaches a terminal state, ETA ±30 % p50 and never understating at p95, `usage_events + rejections == arrivals`.

**Phase 3 — observability and async.** `/metrics`, `/livez`, `/readyz`, `/warm`, `Server-Timing` stage spans; `POST /v1/jobs`, `GET /v1/jobs/{id}`, `/events`, `DELETE`, `POST /v1/uploads` (presigned PUT); watchdog unit; logrotate; auto-replay timer; Prometheus → Grafana Cloud with the 15 alerts and burn-rate rules. Exit: a `docker pause` of vLLM detected and recovered within 2.5 min.

**Phase 4 — front door.** ALB across ≥2 AZs (idle timeout 180 s, target group on `/readyz`, WAF, access logs), ACM cert, delete Caddy, gateway binds `0.0.0.0:8001` in the SG, `marlin2b.callbill.ai` → ALB alias (decide the Elastic IP question with callers first). Exit: A15 (120 s clip with 90 s queue wait, streaming and not), zero 5xx during a drain-restart at 6 req/s.

**Phase 5 — fleet.** Baked AMI (Packer/Image Builder: vLLM digest, weights, compile cache, ffmpeg, pinned deps on the EBS root), `WEIGHTS_ROOT`/`USAGE_LOG` off the instance store, launch template + ASG + lifecycle hooks + four scaling policies + scheduled floor, targeted ODCR for floor+1 across two AZs, ElastiCache Valkey, Secrets Manager for the service-role key, `worker.py` pull loop as `marlin2b-worker.service`. Exit: A7 (kill one of two replicas at 6 req/s: zero client 5xx, re-dispatch < 30 s), cold start p95 < 200 s, `ReplaceRootVolume` refresh with auto-rollback.

**Phase 6 — economics.** Pricing-unit migration, response cache (after a content-retention decision), 1-year Compute Savings Plan sized to the floor, price rows for g6e/g6/g5/c7i in `cloud-pricing.md`, and B6.1: benchmark `g6.2xlarge` (L4) and `g5.2xlarge` (A10G) — break-even 1.56 / 1.94 clips/s, possible −55 % per GPU dollar. Deferred: EKS/Karpenter/KEDA, SageMaker async (batch lane only), llm-d/Dynamo.

Decisions already made (blueprint §0.2, do not relitigate without new data):
ALB idle 180 s + keepalives and 202-upgrade; TTFT p95 SLO 6 s; fetch 3 s
connect / 20 s total / ≤3 revalidated redirects; **no warm pool** (g6e cannot
hibernate; buy an ODCR and keep the spare warm); one Valkey queue, no SQS on the
interactive path; N=2 replicas minimum for any interactive SLO; bursts are
absorbed by the bounded queue + headroom, not by scale-up (≈7 min lead time).

## 7. Other open items (not blocking the API work)

- **Deep traces (specified 2026-09-20, not started):** opt-in per-key
  request tracing (input/output/config/timings/tokens/cost) shipped
  asynchronously to ClickHouse + S3, a console Traces page, `POST /v1/feedback`,
  and an async LLM-as-judge. Requirements, architecture, data model, gateway /
  judge / console specs and a seven-phase plan with measured exit criteria are
  in [`research/traces/`](research/traces/README.md). It shares seams with §6:
  it reads `MediaRef` after Phase 1, moves into `shared/usage.py` at Phase 2,
  and registers its counters on Phase 3's `/metrics`. Start with
  `research/traces/08-phases-and-test-plan.md` Phase 0.
- OpenRouter listing: plan and provider document in `apps/infrx-api/openrouter/`; apply once Phase 2–4 make uptime and pricing defensible.
- Google sign-in: add `https://fcbnscgsymzdykendbrc.supabase.co/auth/v1/callback` to the callgideon OAuth client, re-enable the provider, restore the button (git history has it: commit `ad575a8`).
- Console: `NEXT_PUBLIC_APP_URL` is set on Vercel but no longer read; Billing/Dedicated/Teams are display-only; Stripe not integrated.
- `login@callbill.ai` is send-only; SES reputation is fresh (first mails may land in spam).
- Other models (`qwen3827b`, `deepseek41f`, `kimik3`) have research and download scripts only; `research/matrix/recommendations.md` lists the benchmark plan for the 8×B300 nodes.
- The GPU box is a single point of failure and costs ~$54/day; stop it (`aws ec2 stop-instances --instance-ids i-0e8449a4ffca29bab`) when idle — the EIP and DNS survive a stop/start, the instance-store NVMe (weights, logs, `usage_failed.jsonl`) does **not**.

## 8. Command cheat sheet

```bash
A="env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws"
# run something on the box
python3 -c 'import json;json.dump({"InstanceIds":["i-0e8449a4ffca29bab"],"DocumentName":"AWS-RunShellScript","Parameters":{"commands":["cd /home/ubuntu/model-inference && sudo -u ubuntu git pull -q origin main && ./apps/infrx-api/deploy/install.sh"]}},open("/tmp/ssm.json","w"))'
CMD=$($A ssm send-command --cli-input-json file:///tmp/ssm.json --query Command.CommandId --output text); sleep 20
$A ssm get-command-invocation --command-id $CMD --instance-id i-0e8449a4ffca29bab --query '[Status,StandardOutputContent]' --output text
# call the API
KEY=$($A ssm get-parameter --name /model-inference/marlin2b_api_key --with-decryption --query Parameter.Value --output text)
MARLIN_API_KEY=$KEY python3 apps/infrx-api/client_example.py --raw --find "a white bus drives past"
# gateway tests
python3 -m venv /tmp/gw && /tmp/gw/bin/pip install -q fastapi uvicorn httpx pytest && /tmp/gw/bin/python -m pytest apps/infrx-api/tests -q
# console
cd apps/app && pnpm install && cp .env.example .env.local && pnpm dev   # fill keys from SSM
```
