# Deep traces — data model

> **Implementation amendment — 2026-09-20.** The current implementation authority is [the unified plan](../plan/README.md), especially [contracts](../plan/01-contracts.md), [durable protocols](../plan/02-durable-protocols.md) and [verification](../plan/04-verification.md). The text below is historical research where it conflicts. Trace loss does not stop inference; capture has active-byte, queue, spool and disk limits, and local durability begins at fsync on persistent storage. Off-mode requests have no CH trace row and are excluded from trace-coverage denominators. Canonical logical content is preserved, not raw HTTP wire bytes after normalization. Feedback 201 requires PG/outbox durability and tenant ownership independent of CH lag. Channel and author role are separate; customer console feedback is not an operator calibration label. Judge requires current consent, hard worst-case budget reservations and ambiguous-submit quarantine. CH schema/version/dedup must run against the pinned server; logical expiry is enforced before physical TTL deletion. Retention: 24h results, 7d processing cache, <=90d optional full content, 13mo metadata.


Design date **2026-09-20**. The storage contract for
[`03-architecture.md`](03-architecture.md): ClickHouse DDL, the S3 object
schemas, the spool line format, and the Supabase migration that carries the
opt-in flags. Field semantics are in [`02-metrics-catalogue.md`](02-metrics-catalogue.md);
this file says where each one lives and in what type.

Two rules inherited from [`../platform/01-observability-and-tracing.md`](../platform/01-observability-and-tracing.md) §1.5:
**trace rows never mutate** (later signals are `scores` rows), and **content is
addressed by reference** (`content_ref`), never stored in the row.

---

## 1. Identifiers

| Id | Type | Minted by | Notes |
|---|---|---|---|
| `id` (trace id) | `UUID` | gateway, = `Inference-Id` | today `uuid4()`; after production-api Phase 2, the uuid half of `job_…` ([`../production-api/10-implementation-spec.md`](../production-api/10-implementation-spec.md) §2). Same value in `usage_events.id`, the response header, vLLM's `chatcmpl-<id>`, and S3 keys |
| `org_id`, `api_key_id` | `UUID` | Supabase | from the auth row |
| `media_sha256`, `content_sha256`, `prompt_hash`, `prompt_stack_hash` | `FixedString(64)` hex | gateway | content addressing |
| `score.id`, `judge_run.id` | `UUID` | writer | |
| `artifact_id` | `String` | deploy | `"<model_id>@<hf_revision>+vllm@<image_digest[:12]>+<serve_flags_hash[:8]>"`, written into `/etc/marlin2b-gateway.env` by `install.sh` from `serve.sh`; the "which build produced this token" key |

## 2. ClickHouse

Database `infrx`. All timestamps UTC. Nullable columns are those a `metadata`
level row or an error row may legitimately lack.

### 2.1 `traces`

```sql
CREATE DATABASE IF NOT EXISTS infrx;

CREATE TABLE infrx.traces
(
    -- identity
    id                       UUID,
    org_id                   UUID,
    api_key_id               Nullable(UUID),
    ts                       DateTime64(3, 'UTC'),               -- request receipt (t0)
    ingested_at              DateTime64(3, 'UTC'),               -- shipper insert time; Replacing version
    model_id                 LowCardinality(String),             -- 'nemostation/marlin-2b'
    served_model             LowCardinality(String),             -- 'marlin2b' (vLLM served name)
    artifact_id              LowCardinality(String),
    endpoint_role            LowCardinality(String) DEFAULT 'main',
    gateway_version          LowCardinality(String),             -- git sha[:12]
    trace_level              LowCardinality(String),             -- effective: 'metadata' | 'full'
    -- client context
    session_id               Nullable(String),
    user_hash                Nullable(FixedString(64)),          -- sha256(org_salt || user id)
    metadata                 Map(LowCardinality(String), String),
    request_id_upstream      Nullable(String),                   -- vLLM's 'chatcmpl-<id>'
    -- request
    stream                   Bool,
    requested_model          LowCardinality(String),             -- body.model as sent
    params                   String,                             -- JSON: every sampling param present in the body
    temperature              Nullable(Float32),
    top_p                    Nullable(Float32),
    max_tokens               Nullable(UInt32),
    seed                     Nullable(Int64),
    n                        UInt8 DEFAULT 1,
    response_format_type     LowCardinality(Nullable(String)),   -- text | json_object | json_schema
    n_messages               UInt16,
    n_system                 UInt8,
    n_video                  UInt8,
    n_image                  UInt8,
    n_tools                  UInt16,
    prompt_chars             UInt32,                             -- text parts only
    prompt_hash              FixedString(64),                    -- sha256(canonical messages, media by ref)
    prompt_stack_hash        FixedString(64),                    -- sha256(system text + tools + params)
    request_bytes            UInt32,
    -- media
    media_sha256             Nullable(FixedString(64)),
    media_bytes              Nullable(UInt32),
    media_mime               LowCardinality(Nullable(String)),
    media_duration_s         Nullable(Float32),
    media_frames             Nullable(UInt16),                   -- frames the model saw
    media_fps                Nullable(Float32),
    media_source             LowCardinality(Nullable(String)),   -- url | data | upload
    media_s3_key             Nullable(String),                   -- transcoded clip (Phase 1) or media copy
    media_cache_hit          LowCardinality(Nullable(String)),   -- miss | l1 | l2 | index  (Phase 1)
    mm_kwargs                String,                             -- JSON of mm_processor_kwargs sent
    -- outcome
    status                   UInt16,
    error_type               LowCardinality(Nullable(String)),   -- auth | capacity | invalid_request | media | upstream | timeout | client_abort
    error_code               LowCardinality(Nullable(String)),
    error_message            Nullable(String),
    finish_reasons           Array(LowCardinality(String)),
    n_choices                UInt8,
    schema_valid             Nullable(Bool),
    client_aborted           Bool DEFAULT false,
    -- tokens
    prompt_tokens            Nullable(UInt32),
    completion_tokens        Nullable(UInt32),
    cached_tokens            Nullable(UInt32),                   -- prompt_tokens_details.cached_tokens
    multimodal_tokens        Nullable(UInt32),                   -- prompt_tokens_details.multimodal_tokens
    think_chars              UInt32 DEFAULT 0,                   -- length of the stripped <think> block
    output_chars             UInt32 DEFAULT 0,
    -- timing, ms from t0
    t_auth_ms                Nullable(UInt32),
    t_media_ms               Nullable(UInt32),                   -- media stage end
    t_admit_ms               Nullable(UInt32),                   -- queue admission (Phase 2); = t_media_ms before
    t_first_byte_ms          Nullable(UInt32),                   -- first byte from vLLM (headers)
    ttft_ms                  Nullable(UInt32),                   -- first content delta after <think> strip (== usage_events.ttft_ms)
    wall_ms                  UInt32,
    tpot_ms                  Nullable(Float32),                  -- (wall - ttft) / (completion_tokens - 1)
    sse_chunks               UInt32 DEFAULT 0,
    concurrency_at_admission UInt16,
    -- cost
    cost_usd                 Decimal(14, 8),
    input_usd_per_m          Decimal(12, 6),
    output_usd_per_m         Decimal(12, 6),
    price_table_version      LowCardinality(String),             -- models.updated_at ISO of the price row used
    -- content
    content_ref              Nullable(String),                   -- s3 key
    content_bytes            UInt32 DEFAULT 0,
    content_sha256           Nullable(FixedString(64)),
    redaction_status         LowCardinality(String) DEFAULT 'raw',

    INDEX idx_session   session_id          TYPE bloom_filter GRANULARITY 4,
    INDEX idx_meta_keys mapKeys(metadata)   TYPE bloom_filter GRANULARITY 4,
    INDEX idx_meta_vals mapValues(metadata) TYPE bloom_filter GRANULARITY 4,
    INDEX idx_status    status              TYPE set(16)       GRANULARITY 4,
    INDEX idx_finish    finish_reasons      TYPE bloom_filter GRANULARITY 4
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(ts)
ORDER BY (org_id, ts, id)
TTL toDateTime(ts) + INTERVAL 13 MONTH DELETE
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1;
```

Read rule: every query is `FROM infrx.traces FINAL WHERE org_id = {org:UUID}
AND ts BETWEEN …` (or `argMax(col, ingested_at)` grouped by `id` for wide
aggregates). `FINAL` on an `org_id, ts` range is cheap at these sizes.

### 2.2 `scores`

Append-only; one row per signal. `value_*` columns are mutually exclusive by
`kind`.

```sql
CREATE TABLE infrx.scores
(
    id              UUID,
    trace_id        UUID,
    org_id          UUID,
    ts              DateTime64(3, 'UTC'),
    name            LowCardinality(String),         -- 'thumb' | 'rating' | 'correction' | judge criteria names …
    source          LowCardinality(String),         -- user | operator | judge
    kind            LowCardinality(String),         -- numeric | boolean | label | text
    value_num       Nullable(Float64),
    value_bool      Nullable(Bool),
    value_label     LowCardinality(Nullable(String)),
    value_text      Nullable(String),               -- 'correction' payload (expected answer)
    comment         Nullable(String),
    author          Nullable(String),               -- api_key_id (user) | profile id (operator) | judge run id
    judge_run_id    Nullable(UUID),
    judge_model     LowCardinality(Nullable(String)),
    rubric_id       LowCardinality(Nullable(String)),
    rubric_version  Nullable(UInt16),
    rationale       Nullable(String),

    INDEX idx_trace trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (org_id, trace_id, ts)
TTL toDateTime(ts) + INTERVAL 13 MONTH DELETE
SETTINGS ttl_only_drop_parts = 1;
```

"Latest wins" for the console: `argMax(value_num, ts) … GROUP BY trace_id, name, source`.

### 2.3 `judge_runs`, `judge_items`

```sql
CREATE TABLE infrx.judge_runs
(
    id              UUID,
    org_id          UUID,
    started_at      DateTime64(3, 'UTC'),
    finished_at     Nullable(DateTime64(3, 'UTC')),
    status          LowCardinality(String),         -- selecting | submitted | ended | failed
    batch_id        Nullable(String),               -- Anthropic msgbatch_…
    judge_model     LowCardinality(String),
    rubric_id       LowCardinality(String),
    rubric_version  UInt16,
    frames_k        UInt8,
    n_selected      UInt32,
    n_succeeded     UInt32 DEFAULT 0,
    n_errored       UInt32 DEFAULT 0,
    n_expired       UInt32 DEFAULT 0,
    input_tokens    UInt64 DEFAULT 0,
    output_tokens   UInt64 DEFAULT 0,
    cost_usd_est    Decimal(12, 6),
    cost_usd_actual Decimal(12, 6) DEFAULT 0,
    error           Nullable(String)
)
ENGINE = ReplacingMergeTree(finished_at)
ORDER BY (org_id, started_at, id);

CREATE TABLE infrx.judge_items
(
    run_id          UUID,
    org_id          UUID,
    trace_id        UUID,
    ts              DateTime64(3, 'UTC'),
    rubric_id       LowCardinality(String),
    rubric_version  UInt16,                         -- "unjudged" is per rubric version (J4); denormalised from judge_runs
    status          LowCardinality(String),         -- dry_run | submitted | succeeded | errored | expired | skipped_budget | skipped_no_media | skipped_no_output
    reason          LowCardinality(String),         -- always | sample | feedback  (why it was selected, J1)
    frames_sent     UInt8,
    input_tokens    Nullable(UInt32),
    output_tokens   Nullable(UInt32),
    error           Nullable(String)
)
ENGINE = ReplacingMergeTree(ts)
ORDER BY (org_id, trace_id, run_id);
```

"Not yet judged for this rubric version" = `traces` rows with no `judge_items`
row in `(succeeded, submitted)` with the current `(rubric_id, rubric_version)`,
via `LEFT ANTI JOIN` — which is why those two columns are on `judge_items`
and not only on `judge_runs`.

### 2.4 Users

| User | Grants | Used by |
|---|---|---|
| `gateway` | `INSERT ON infrx.traces, infrx.scores`; `SELECT ON infrx.traces, infrx.scores` (feedback ownership check, `GET /v1/feedback`, `GET /v1/traces` export — every such query is org-bound by the API key) | gateway worker + `/v1/feedback` + export |
| `console` | `SELECT ON infrx.*`, `readonly = 1`, `max_execution_time = 10`, `max_result_rows = 10000` | Caddy proxy for Vercel |
| `console_writer` | `INSERT ON infrx.scores` | console feedback server action (separate token) |
| `judge` | `SELECT ON infrx.*`; `INSERT ON infrx.scores, infrx.judge_runs, infrx.judge_items` | `judge.py` |
| `admin` | all; `ALTER … DELETE` for O3 | operators, deletion script |

## 3. S3 object schemas

### 3.1 Content blob — `content/{org_id}/{yyyy}/{mm}/{trace_id}.json.gz`

gzip-compressed UTF-8 JSON, `Content-Type: application/json`,
`Content-Encoding: gzip`, tag `ttl=<days>`. Schema version `v: 1`.

```jsonc
{
  "v": 1,
  "id": "3f0c…",                          // trace id
  "org_id": "…",
  "ts": "2026-09-20T18:04:11.212Z",
  "request": {
    "model": "nemostation/marlin-2b",      // as sent by the client
    "messages": [                          // exact order and roles; media parts replaced by references
      {"role": "system", "content": "…"},
      {"role": "user", "content": [
        {"type": "video_url", "video_url": {"url": "infrx://media/sha256:ab12…", "source": "data"}},
        {"type": "text", "text": "Describe the video."}
      ]}
    ],
    "params": {"stream": true, "max_tokens": 2048, "temperature": 0.2},   // every non-message top-level field except 'messages'
    "tools": [],                           // as sent, or absent
    "headers": {"x-infrx-session-id": "…", "x-infrx-trace": "full", "user-agent": "…"},   // allowlisted headers only
    "metadata": {"route": "qa"}
  },
  "upstream": {
    "mm_processor_kwargs": {"fps": 2.0, "min_frames": 4, "max_frames": 240, "size": {"shortest_edge": 4096, "longest_edge": 4014080}},
    "id": "chatcmpl-3f0c…",
    "usage": {"prompt_tokens": 2061, "completion_tokens": 197, "total_tokens": 2258,
              "prompt_tokens_details": {"cached_tokens": 0, "multimodal_tokens": 1940}}
  },
  "response": {
    "status": 200,
    "choices": [
      {"index": 0,
       "finish_reason": "stop",
       "content": "A white bus drives past …",          // exactly what the client received (post-strip)
       "reasoning_content": "<think>The clip shows…</think>",   // stripped from the client; null if none
       "tool_calls_raw": null,                         // raw JSON string(s) as emitted
       "tool_calls": null}                             // json.loads of the above, or the parse error
    ],
    "sse_chunks": 214,
    "client_aborted": false,
    "error": null                                      // {"type","code","message","upstream_body"?} on non-200
  }
}
```

Invariants: `sha256(request.messages)` with media by reference equals
`traces.prompt_hash`; `len(gzip)` equals `traces.content_bytes`;
`sha256(uncompressed)` equals `traces.content_sha256`.

### 3.2 Media copy — `media/{org_id}/{sha256}.{mp4|webm|mov}`

The source bytes as received, only when `organizations.media_copy = true`
(or, later, the transcoded clip from the production-api media stage, keyed by
the *source* sha256 as that spec requires). Tag `ttl`. Metadata headers:
`x-amz-meta-duration-s`, `x-amz-meta-frames`.

### 3.3 Judge frames — `frames/{org_id}/{sha256}/{k:02d}.jpg`

JPEG ≤ 1000 px long edge (1,296 visual tokens each at 1000×1000 [src](https://platform.claude.com/docs/en/build-with-claude/vision)), `k` = 0…K-1 sampled uniformly over the clip's duration; `frames/{org_id}/{sha256}/index.json` records `{k, t_s}`. Written once per clip and reused across judge runs.

## 4. Spool line format (`traces.jsonl`, `traces_failed.jsonl`)

One JSON object per line, written by the worker:

```jsonc
{"kind": "trace", "row": { …every traces column, ingested_at omitted… },
 "content": { …the §3.1 object… } | null,
 "media": {"sha256": "…", "path": "/opt/dlami/nvme/cache/ab/ab12….mp4"} | null}
{"kind": "score", "row": { …every scores column… }}
```

`replay_traces.py` reads lines, re-runs `ship()` per line. The spool is
rotated daily and deleted after 24 h (logrotate); `traces_failed.jsonl` is on
the EBS root, not the instance store.

Worker-set fields are **null in the spool line and filled at ship**:
`content_ref`, `content_bytes`, `content_sha256` (after the S3 put),
`ingested_at`, and the `cost_usd` / `*_usd_per_m` / `price_table_version`
group (priced at ship from the cached `models` row, for parity with
`usage_events` and so a cold price cache never delays a request). The §3.1
invariants therefore hold for shipped rows, not for spool lines.

## 5. Supabase migration — `apps/app/supabase/migrations/0003_traces.sql`

```sql
-- per-key opt-in (D3, D8)
alter table public.api_keys
  add column trace_level   text    not null default 'metadata'
    check (trace_level in ('off', 'metadata', 'full')),
  add column judge_enabled boolean not null default false;

-- per-org trace settings (C4)
alter table public.organizations
  add column trace_retention_days   int           not null default 90
    check (trace_retention_days between 7 and 365),
  add column media_copy             boolean       not null default false,
  add column judge_sample_pct       numeric(5, 2) not null default 5
    check (judge_sample_pct between 0 and 100),
  add column judge_daily_budget_usd numeric(10, 2) not null default 0,
  add column judge_egress_ok        boolean       not null default false,
  add column judge_egress_ok_by     uuid references public.profiles(id),
  add column judge_egress_ok_at     timestamptz,
  add column judge_rubric           text,
  add column judge_rubric_version   int           not null default 1,
  add column judge_frames_k         int           not null default 8
    check (judge_frames_k between 1 and 20);

-- audit of tenant-scoped deletions (O3)
create table public.trace_deletions (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null references public.organizations(id) on delete cascade,
  requested_by uuid references public.profiles(id),
  requested_at timestamptz not null default now(),
  completed_at timestamptz,
  rows_deleted bigint, objects_deleted bigint, note text
);
alter table public.trace_deletions enable row level security;
create policy trace_deletions_select on public.trace_deletions for select to authenticated
  using (public.is_org_member(org_id) or public.is_operator());
revoke insert, update, delete on public.trace_deletions from anon, authenticated;
```

RLS: the existing `api_keys_update_owner` and `organizations_update_owner`
policies already let owners change these columns; `judge_egress_ok` is set by
a server action that also stamps `_by`/`_at` (C4). The gateway's auth `select`
becomes `id,org_id,revoked_at,trace_level,judge_enabled,organizations(media_copy,trace_retention_days)`
(PostgREST FK embedding) so opt-in costs no extra request-path I/O.

## 6. Field ownership matrix

| Column group | Written by | When |
|---|---|---|
| identity, request, media, outcome, tokens, timing, cost, content refs | gateway worker | once, at ship |
| `redaction_status` | (future) redaction job | rewrite by re-insert with newer `ingested_at` — the one sanctioned "mutation", because it is a new version of the same fact |
| `scores` | gateway (`/v1/feedback`), console action, judge | append |
| `judge_runs`, `judge_items` | judge | append / version by `finished_at` |
| `api_keys.trace_level`, `judge_enabled` | console (owner) | user action |
| `organizations.judge_*`, `trace_retention_days`, `media_copy` | console (owner) | user action |

---

## Verification log

- 2026-09-20 — DDL written against ClickHouse MergeTree docs fetched this session (`TTL … DELETE`, `ttl_only_drop_parts`, `ORDER BY` = primary key when unspecified). ⚠️ **Not yet executed** against a server: the `INDEX … TYPE set(16)` and `bloom_filter` on `Array(LowCardinality(String))` / `mapKeys()` forms are standard but must be run once in [`08`](08-phases-and-test-plan.md) Phase 0 (S1) before anything depends on them. `prompt_tokens_details.multimodal_tokens` naming from vLLM's `_make_prompt_tokens_details` (fetched); ⚠️ populated-for-video is an open question ([`01`](01-requirements.md) OQ 2). PostgREST FK embedding syntax `organizations(media_copy)` is how the console already reads `org_members → organizations(name)` (`apps/app/lib/session.ts`).

### Implementation-plan amendment log — 2026-09-20

Documentation reconciliation only: incorporated the unified plan and review corrections above. Historical measurements and previous verification entries remain unchanged; new implementation/live tests are pending.
