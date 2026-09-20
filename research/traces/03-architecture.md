# Deep traces — architecture

Design date **2026-09-20**. Implements the requirements in
[`01-requirements.md`](01-requirements.md) under decisions D1–D8. Field
definitions are in [`02-metrics-catalogue.md`](02-metrics-catalogue.md); DDL
and object layouts in [`04-data-model.md`](04-data-model.md); the gateway,
judge and console specs in `05`–`07`; the build order in
[`08-phases-and-test-plan.md`](08-phases-and-test-plan.md).

Legend: [`../METHODOLOGY.md`](../METHODOLOGY.md#legend).

---

## 1. The system in one diagram

```
developer ──HTTPS──▶ Caddy/ALB ─▶ gateway.py ─────────────────────▶ vLLM (:8000)
                                   │  auth cache (+trace_level, judge_enabled)      X-Request-Id: <Inference-Id>
                                   │  media stage (sha256 ∥ ffprobe)               --enable-prompt-tokens-details
                                   │  stream relay (accumulate deltas, strip <think>)
                                   │
                                   ├─ put_nowait(trace) ──▶ trace worker (same process, background)
                                   │                          │ 1. append traces.jsonl      (NVMe, durability)
                                   │                          │ 2. batch ≤200 rows / ≤2 s
                                   │                          │ 3. content → S3  infrx-traces/content/{org}/…   (only trace_level=full)
                                   │                          │ 4. rows    → ClickHouse HTTP  INSERT … FORMAT JSONEachRow
                                   │                          │ 5. on failure: retry 1/3/9 s → traces_failed.jsonl → replay_traces.py
                                   └─ usage_events (unchanged) ─▶ Supabase

                     ┌──────────────── infrx-obs (small EC2, docker compose) ────────────────┐
                     │ clickhouse-server :8123 (HTTP, private SG)   nightly BACKUP → S3     │
                     │ caddy :443  bearer-token proxy for the console (read-only CH user)    │
                     │ judge.py  systemd timer: select → frames → Message Batch → scores     │
                     └───────────────────────────────────────────────────────────────────────┘
                                   ▲                                    │
      console (Vercel, Next.js) ───┘ server components: SELECT … WHERE org_id = {session}
                                     presigned S3 GET for content blobs (after the row check)
      developer app ─▶ gateway  POST /v1/feedback ──▶ trace worker ──▶ ClickHouse scores
```

Three planes, one id:

| Plane | Store | Owner | Keyed by |
|---|---|---|---|
| Billing / console tiles (today) | Supabase `usage_events` | gateway | `Inference-Id` |
| Ops metrics (production-api Phase 3) | Prometheus | gateway + vLLM | series labels |
| **Content / quality (this design)** | ClickHouse `traces`, `scores`; S3 blobs | gateway worker, judge, console | `Inference-Id` |

## 2. Request path — what the hot path does and does not do

The rule: **the request path performs no network I/O and no blocking I/O for
tracing.** It builds one dict and calls `Queue.put_nowait`. Everything else is
the worker's job.

### 2.1 Non-streaming

```
t0   request received; rid = uuid4 (later: job id)                       [exists]
     authenticate() → row {id, org_id, revoked_at, trace_level, judge_enabled}   [+2 columns in the same select]
     level = min(key.trace_level, header X-Infrx-Trace)                  [pure function]
     requested_model = body["model"]; body["model"] = "marlin2b"         [capture before rewrite]
t1   media stage: fetch → one executor hop: sha256 then ffprobe → budget_kwargs
     media = {sha256, bytes, duration_s, frames, fps, source, mime}      [+sha256, off the loop, same thread as the probe]
t2   upstream POST with X-Request-Id: rid                                [+1 header]
t3   r.json() → data; raw_choices = [copy of message.content strings]    [reference, not copy: str is immutable]
     THINK.sub on the client copy                                        [exists]
     trace = assemble(level, body_for_trace, data, raw_choices, media, timings)   [dict build]
     put_nowait(trace)                                                   [O(1)]
     return JSONResponse                                                 [exists]
```

`body_for_trace` is the received body with the video part's `url` replaced by
`infrx://media/sha256:<hex>` — the 30 MB base64 string is **never** copied into
the trace; it is referenced through the hash. At `metadata` level
`body_for_trace` is not built at all; only counts and hashes are.

### 2.2 Streaming

The generator already parses every SSE chunk to rewrite `model` and strip
`<think>`. Capture adds, per chunk, one list append of the delta string
(`deltas.append(c)`) and one counter. In the `finally:` (which already runs
`log()`), `"".join(deltas)` is the raw output; the `<think>` prefix is split
off with the same regex; `usage` is the final chunk's usage (vLLM emits it
because the gateway sets `stream_options.include_usage`). `client_aborted` is
`True` when the generator is closed by the client before `[DONE]` (FastAPI
cancels the generator; `finally` still runs).

### 2.3 What the hot path costs, per request (`est.`)

| Step | Where | Cost | Note |
|---|---|---|---|
| `min(level, header)` | event loop | ~1 µs | |
| sha256 of the clip | probe executor thread, **same hop as ffprobe** (hash, then probe; off the event loop) | 5.5 MB ≈ 10–15 ms of CPU on one core, added to that request's media stage, invisible to other requests | ⚠️ measured in H1; if H1 shows it, run the two as `gather` on two threads ([`05`](05-gateway-capture-spec.md) §4); after production-api Phase 1 the hash exists anyway (`MediaRef.sha256`) |
| list append per chunk | event loop | ~0.1 µs × chunks | |
| `assemble()` dict | event loop | ~50–200 µs for a 20 KB trace | no serialisation here |
| `put_nowait` | event loop | ~1 µs | bounded queue, 10k; on full → counter + drop to `traces_failed.jsonl` by the worker, never block |
| `json.dumps` + file append | **worker task** | ~100–300 µs | off the request path; same event loop, so it is CPU the loop spends between requests — at 100 req/s that is ≤ 3 % of one core |

Today `log()` already does the `json.dumps` + `open/append` for `usage.jsonl`
synchronously on the request path; the trace design moves *its* serialisation
into the worker and leaves the usage line unchanged (production-api Phase 2
moves that too). NF1's ≤ 1 ms p50 budget is conservative by an order of
magnitude; H1 measures it.

### 2.4 What is captured at each level

| | `off` | `metadata` | `full` |
|---|---|---|---|
| `usage_events` row (Supabase) | ✅ | ✅ | ✅ |
| `traces` row: identity, params, counts, hashes, media metadata, tokens, timings, status, cost | — | ✅ | ✅ |
| `content` blob: messages, output, `<think>`, tool calls, raw usage, error body | — | — | ✅ |
| media copy (`media/{org}/{sha}.mp4`) | — | — | only if `org.media_copy` |
| eligible for judge | — | — | if `key.judge_enabled` ∧ `org.judge_egress_ok` |

`off` is a real level: a key at `off` writes nothing to ClickHouse at all, so a
customer can prove a route is untraced by reading the console.

## 3. Trace worker (in the gateway process)

One background task, started lazily like `ingest()` today, owning three
things: the spool file, the batch, and the failure file.

```
loop:
  item = await q.get()                       # trace row (+content) | score row
  append(traces.jsonl, item)                 # durability first; fsync every N or 1 s
  batch.add(item)
  if len(batch) >= 200 or age(batch) >= 2 s or q.empty():
      ship(batch)

ship(batch):
  for t in batch with content:               # S3 first, then the row that references it
      put_object(content/{org}/{yyyy}/{mm}/{id}.json.gz, tag ttl=<org bucket>)   # httpx, SigV4
  insert(clickhouse, rows)                   # one HTTP POST, JSONEachRow, ≤ 1 MB
  insert(clickhouse, scores)
  on any failure after 1/3/9 s retries → append items to traces_failed.jsonl, continue
```

Why this order: an S3 blob without a row is an orphan the lifecycle rule
collects; a row without its blob is a broken link in the console. Blob first.

Fields the worker fills at ship rather than the request path: `content_ref`,
`content_bytes`, `content_sha256`, `ingested_at`, and the cost group
(`cost_usd`, prices, `price_table_version`) — priced from the same cached
`models` row `usage_events` uses, so the request path never waits on a cold
price cache ([`04`](04-data-model.md) §4).

Idempotency: `traces` is a `ReplacingMergeTree` keyed on `(org_id, ts, id)`
with `ingested_at` as the version; a replayed batch produces a duplicate that
the next merge collapses and reads use `FINAL` or `argMax`. S3 keys are
deterministic, so a re-put is a no-op. `replay_traces.py` is `replay_usage.py`
with the ship function.

Shutdown: on SIGTERM the worker drains the queue to the spool (bounded by
`TimeoutStopSec`), then ships what it can. SIGKILL loses at most what was in
the queue but not yet appended — milliseconds of traffic. Accepted (NF2 is
about store outages and graceful restarts; the spool append is the durability
point).

Two workers, not one, would only matter above ~500 req/s on one box (`est.`);
not designed for.

## 4. Store

### 4.1 ClickHouse

Single node, `clickhouse/clickhouse-server` at a pinned digest, docker compose
on `infrx-obs`, data on a gp3 EBS volume. Three tables (`traces`, `scores`,
`judge_runs`/`judge_items`) in database `infrx` — DDL in
[`04`](04-data-model.md). Design choices:

| Choice | Why |
|---|---|
| `ORDER BY (org_id, ts, id)` | every console query is "this org, this time range"; tenancy and the primary index coincide [src](https://clickhouse.com/docs/engines/table-engines/mergetree-family/mergetree) |
| `PARTITION BY toYYYYMM(ts)` | month drops are free; 13-month TTL deletes whole parts (`ttl_only_drop_parts=1`) |
| `TTL toDateTime(ts) + INTERVAL 13 MONTH` on `traces`, `scores` | [`01`](01-requirements.md) O2; TTL is evaluated at merge time, so expiry is within ~a day, not instant [src](https://clickhouse.com/docs/engines/table-engines/mergetree-family/mergetree) |
| `ReplacingMergeTree(ingested_at)` for `traces` | replay-safe (NF6) |
| content **not** in ClickHouse | keeps the table narrow and fast; content is served by S3 presigned GET; the OTel spec's own production pattern ("store content externally and record references") [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) |
| `Map(String, String)` for client `metadata` + bloom-filter skip index on keys | filterable custom properties without schema changes |
| HTTP interface only (`:8123`), users: `gateway` (INSERT on `infrx.*`), `console` (SELECT on `infrx.*`, `readonly=1`), `judge` (SELECT + INSERT `scores`, `judge_*`) | least privilege; header auth `X-ClickHouse-User` / `X-ClickHouse-Key` [src](https://clickhouse.com/docs/interfaces/http) |

Backups: nightly `BACKUP DATABASE infrx TO S3('…/backups/clickhouse/<date>', …) SETTINGS base_backup = …` (weekly full, daily incremental); restore drill is O1's acceptance [src](https://clickhouse.com/docs/operations/backup).

### 4.2 S3 (`infrx-traces`, us-east-1)

| Prefix | Object | Written by | Lifecycle |
|---|---|---|---|
| `content/{org_id}/{yyyy}/{mm}/{trace_id}.json.gz` | the content blob (schema in [`04`](04-data-model.md) §3) | gateway worker | by object tag `ttl` ∈ {7, 30, 90, 180, 365} days; org retention is quantised to the nearest value at write |
| `media/{org_id}/{sha256}.{ext}` | source clip copy, only when `org.media_copy` | gateway worker | tag `ttl`, same buckets |
| `frames/{org_id}/{sha256}/{k}.jpg` | judge frames | judge | 30 d |
| `backups/clickhouse/…` | | ClickHouse | 35 d |

Bucket policy: SSE-S3, public access blocked, gateway instance role
`PutObject` on `content/*` and `media/*`, a console-only IAM user `GetObject`
on `content/*` (header-signed, server-side) and `frames/*` (presigned for
`<img>`), judge role read `content/*` + `media/*`, write `frames/*`. Per-org
deletion = `DeleteObjects` by prefix (O3).

Why one object per trace and not packed batches: presigned GET of exactly one
trace is the console's read pattern; at pilot the PUT cost is ~$5/month (§7).
At 50M/month it is ~$250/month, which [`../platform/01`](../platform/01-observability-and-tracing.md) §7.2 already accepted.

## 5. Read path (console)

The console is Next.js on Vercel with no fixed egress IP. ClickHouse is never
exposed; the console talks to **Caddy on `infrx-obs`** over HTTPS with a bearer
token, and Caddy forwards to `:8123` as the read-only `console` user. Server
components send parameterised queries (`{org:UUID}` bound from the session, never
interpolated) and get JSON back. Content blobs: the server component first
confirms the row belongs to the session's org, then fetches the object
**server-side** with a SigV4 header-signed GET from the console's own AWS
credentials (server-only env vars), inflates the gzip, validates the shape and
renders it — the browser never talks to S3 for content, so the bucket needs no
CORS and the app no `connect-src` change. Only judge **frames** are handed to
the browser as 60-second presigned `<img>` URLs. Detail in [`07`](07-console-spec.md) §1.

Operators (`profiles.is_operator`) get an org selector; the same queries run
with the chosen `org_id`.

## 6. Feedback and judge (summary; [`06`](06-feedback-and-judge-spec.md) has the detail)

- `POST /v1/feedback` on the gateway validates the body, checks ownership by a
  cached `(trace_id → org_id)` lookup against ClickHouse (read-only user, 5 s
  timeout; on failure `503`, never a silent accept), and `put_nowait`s a score
  row onto the same worker queue. Console feedback is a server action writing
  through the Caddy proxy with an `INSERT`-capable token on `scores` only.
- `judge.py` runs on `infrx-obs` every 30 min: selects eligible unjudged traces
  (J1) within the org's daily budget (J6), fetches content blobs and frames,
  submits **one Message Batch per run** to `claude-opus-5` with structured
  output, records a `judge_runs` row, then a second timer polls batches and
  writes `scores` (`source='judge'`) and `judge_items`. It never runs on the GPU
  box and never touches the request path.

## 7. Sizing and cost

Two workloads: **pilot** (1M requests/month, Marlin video captioning) and the
[`../platform/01`](../platform/01-observability-and-tracing.md) §7 **platform**
workload (50M/month). Arithmetic is `est.`, recomputed with `python3`.

Marlin request shape (`meas.` from `usage.jsonl` samples, ⚠️ small sample):
prompt text ~120 chars, 1,928–2,061 prompt tokens of which ~95 % are video
tokens, ~200–400 output tokens, `<think>` ~0–500 tokens. So a **content blob is
~3–6 KB**, not the 20 KB text-chat figure of the platform doc.

| Quantity | Pilot (1M/mo) | Platform (50M/mo) |
|---|---:|---:|
| `traces` row, raw JSON | ~2.5 KB | ~2.5 KB |
| rows/month raw | 2.5 GB | 125 GB |
| rows/month in ClickHouse at 5× (⚠️ conservative; vendor says up to 14×) | 0.5 GB | 25 GB |
| 13-month resident rows | 6.5 GB | 325 GB |
| content blobs (at `full`, 100 % of keys) | 5 GB/mo | 250 GB/mo (Marlin shape) / 1 TB (text-chat shape) |
| S3 PUTs at $0.005/1,000 | **$5/mo** | **$250/mo** |
| S3 storage, 90-day content window, $0.023/GB-mo | $0.35/mo | $17/mo |
| `infrx-obs` EC2: 2 vCPU / 8 GB class (m6i.large or t3.large) | ⚠️ **≈ $60–70/mo** on-demand — price **TO BE VERIFIED** and added to [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md); 8 GB is ClickHouse's floor for a comfortable single node | needs 4 vCPU / 16–32 GB (⚠️ ≈ $140–280/mo) |
| EBS gp3 100 GB | ⚠️ ≈ $8/mo | 500 GB ≈ $40/mo |
| backups (35 d, incremental) | < $1/mo | ~$10/mo |
| **Infra subtotal** | **≈ $75–85/mo** | **≈ $470–600/mo** |

Judge cost (Opus 5, batch = 50 % of $5 / $25 per MTok; K = 8 frames at
1000 px long edge — a 16:9 frame is 1000×563 ≈ 756 visual tokens
(`⌈1000/28⌉ × ⌈563/28⌉`) [src](https://platform.claude.com/docs/en/build-with-claude/vision);
plus ~1,500 text tokens in, and ~400 output **plus ~500 adaptive-thinking
tokens** billed as output ⚠️ unmeasured): the full table is
[`06`](06-feedback-and-judge-spec.md) §3.8, whose recommended default comes
to **≈ $0.030 per assessment in batch → ≈ $1,480/mo at 1M req/mo × 5 %
sampled** (≈ $1,050/mo once the production-api media stage supplies 448 px
transcodes as the frame source; prompt caching on the shared system prompt
cuts further). At 1 %: ≈ $300/mo. That is the dominant line item and the
reason J6 is a hard per-org budget. K = 4 frames roughly halves it; a Sonnet
5 judge ($2 / $10) cuts it ~2.5× — model choice is config (J7), decided by
the judge-validity study in [`06`](06-feedback-and-judge-spec.md) §3.9.

NF8's ≤ $150/mo infra target holds at pilot; judge spend is a product setting.

## 8. Security and tenancy

| Concern | Mechanism |
|---|---|
| Cross-org read | `org_id` in every `ORDER BY` and every `WHERE`, bound as a query parameter server-side; content fetched server-side only after the row check; frame presigns minted only after the row check; operators explicitly flagged |
| ClickHouse exposure | private SG (`:8123` from the gateway SG only); console via Caddy + bearer token + read-only user; no `default` user password-less |
| Content at rest | S3 SSE-S3; EBS encrypted; ClickHouse has no content |
| Secrets | SSM/Secrets Manager → env files, as today |
| Judge egress | double opt-in (D8, J5) + `judge_runs` audit; Anthropic does not train on API inputs (their terms; cite in the console text) |
| Prompt injection via `metadata`/headers | stored as strings, rendered escaped; never interpolated into queries |
| Deletion | tenant-scoped `ALTER TABLE … DELETE WHERE org_id = …` (lightweight delete) + S3 prefix delete + audit row (O3) |
| Spool on the GPU box | `traces.jsonl` holds content for `full` keys on the instance store; **logrotate + ship + delete within 24 h**; the box is already the place that sees the bytes. ⚠️ If a customer requires "content never at rest on the GPU host", the spool can be `metadata`-only with content shipped straight from memory — a config flag, not a redesign |

## 9. Failure modes

| # | Failure | Detection | Behaviour | Loss |
|---|---|---|---|---|
| 1 | ClickHouse down | insert non-2xx / timeout | retry 1/3/9 s → `traces_failed.jsonl`; requests unaffected | none after `replay_traces.py` |
| 2 | S3 down | put error | same; row shipped **without** `content_ref` is *not* done — the whole item parks, so a row never points nowhere | none after replay |
| 3 | queue full (10k) | `QueueFull` | item written to `traces_failed.jsonl` directly by the request path? **No** — the request path must not touch disk; it increments a counter and drops; the worker logs the drop count. One exception: once the worker has entered **drain** (SIGTERM received), `emit()` parks new items to `traces_failed.jsonl` directly, because the queue will never be serviced again ([`05`](05-gateway-capture-spec.md) §6) | ≤ dropped items; alert on counter |
| 4 | gateway SIGTERM | drain | queue flushed to spool within `TimeoutStopSec` | none |
| 5 | gateway SIGKILL / OOM | — | items in queue not yet appended lost | ms of traffic |
| 6 | instance store wiped (stop/terminate) | — | unshipped spool lost; **ship interval 2 s** bounds it; `traces_failed.jsonl` should be on EBS (config) | seconds of traffic |
| 7 | vLLM omits `usage` / `prompt_tokens_details` | null fields | row written with nulls; `multimodal_tokens` null flags the flag being off | none |
| 8 | client disconnect mid-stream | generator close | `client_aborted=true`, partial output captured, `finish_reasons=[]` | none |
| 9 | judge batch expired / errored | result type | `judge_items.status`, retried next run once; then `failed` | none |
| 10 | judge budget exhausted | selector | stops; surfaced in console | intended |
| 11 | ClickHouse disk full | insert error | as #1; alert on free disk < 20 % | none |
| 12 | presigned frame URL leaked | — | 60 s expiry; one JPEG frame; content blobs are never presigned | one frame |

## 10. Evolution (named so v1 does not preclude them)

1. **Engine spans** (D5): vLLM `--otlp-traces-endpoint` → OTel Collector →
   ClickHouse `engine_spans(request_id, queue_ms, prefill_ms, decode_ms, …)`,
   joined on `id` because T9 sets `X-Request-Id`. No change to `traces`.
2. **PII**: a `redaction_status` column exists from day one (`raw`); a Presidio
   pass on the obs box flips it and writes a redacted blob beside the raw one.
3. **Datasets**: `SELECT … INTO OUTFILE … FORMAT Parquet` from ClickHouse +
   the blobs; the `scores` table is the label source.
4. **Usage page on ClickHouse**: the tiles can be computed from `traces`;
   `usage_events` stays for billing.
5. **Production-api refactor**: `assemble()` and the worker move into
   `shared/usage.py` (spec 10 §1); the `Job` dataclass carries `media`, timings
   and `result` — the trace is a projection of `Job`.
6. **Scale**: ClickHouse to a bigger instance, then to ClickHouse Cloud or a
   2-node keeper setup; S3 and the schema do not change.

---

## Verification log

- 2026-09-20 — ClickHouse claims (TTL syntax, ORDER BY as primary key, `ttl_only_drop_parts`, `BACKUP … TO S3 … SETTINGS base_backup`, HTTP header auth, port 8123) fetched from the cited docs pages this session. OTel "store content externally" pattern quoted in [`../platform/01`](../platform/01-observability-and-tracing.md) §1.3 from the semconv repo. Anthropic image token formula and the 1000×1000 = 1,296 tokens row from the vision page fetched this session. S3 prices ($0.023/GB-mo, $0.005/1,000 PUT) are the values [`../platform/01`](../platform/01-observability-and-tracing.md) §3.2 verified on 2026-09-19. **⚠️ EC2 and EBS prices for `infrx-obs` are not sourced** and are marked. Hot-path costs in §2.3 are estimates pending H1.
