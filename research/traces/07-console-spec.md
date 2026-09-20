# Deep traces — console spec

> **Implementation amendment — 2026-09-20.** The current implementation authority is [the unified plan](../plan/README.md), especially [contracts](../plan/01-contracts.md), [durable protocols](../plan/02-durable-protocols.md) and [verification](../plan/04-verification.md). The text below is historical research where it conflicts. Trace loss does not stop inference; capture has active-byte, queue, spool and disk limits, and local durability begins at fsync on persistent storage. Off-mode requests have no CH trace row and are excluded from trace-coverage denominators. Canonical logical content is preserved, not raw HTTP wire bytes after normalization. Feedback 201 requires PG/outbox durability and tenant ownership independent of CH lag. Channel and author role are separate; customer console feedback is not an operator calibration label. Judge requires current consent, hard worst-case budget reservations and ambiguous-submit quarantine. CH schema/version/dedup must run against the pinned server; logical expiry is enforced before physical TTL deletion. Retention: 24h results, 7d processing cache, <=90d optional full content, 13mo metadata.


Design date **2026-09-20**. The console half of
[`01-requirements.md`](01-requirements.md): C1–C7, the key toggles behind
T2/T3, console feedback F3, and the Vercel → ClickHouse access path of O5 and
[`03-architecture.md`](03-architecture.md) §5. Written against `apps/app` as it
is on `main` (Next.js 16 App Router, React 19, Tailwind 4, shadcn base-nova,
`@supabase/ssr`, tests via `node --test lib/*.test.ts`): every new page copies
an existing one — Usage for list + controls, API Keys for the table + owner
actions, Admin for the operator gate.

Legend: [`../METHODOLOGY.md`](../METHODOLOGY.md#legend). Tables here are
ClickHouse `infrx.*` per [`04-data-model.md`](04-data-model.md).

---

## 1. Access path: Vercel → ClickHouse and S3 (O5)

### 1.1 `lib/clickhouse.ts` — server-only fetch wrapper, no new dependency

`@clickhouse/client-web` is not used: the console never talks to ClickHouse
directly, it talks to **Caddy** on `infrx-obs`, which adds the ClickHouse
credentials. What the client library would add (sessions, compression, typed
settings) is not needed for four parameterised `SELECT`s and one `INSERT`; the
wrapper is ~40 lines on `fetch`, which already exists. Same reasoning as
`lib/supabase/admin.ts`: a thin server-only module, `throw` if imported in the
browser.

ClickHouse HTTP takes query parameters as `param_<name>` form fields and
`{name:Type}` placeholders in the SQL [src](https://clickhouse.com/docs/interfaces/http)
— that is the entire injection defence: **values never touch the SQL string.**

```ts
// lib/clickhouse.ts  (server only)
import "server-only";
import type { Session } from "@/lib/session";

type Param = string | number | boolean | null;
type Params = Record<string, Param>;

const URL_ = process.env.CLICKHOUSE_PROXY_URL!;          // https://obs.callbill.ai/ch
const READ = process.env.CLICKHOUSE_PROXY_TOKEN!;         // Caddy → user `console`   (readonly)
const WRITE = process.env.CLICKHOUSE_PROXY_WRITER_TOKEN!; // Caddy → user `console_writer` (INSERT scores)

async function post(token: string, body: FormData, timeoutMs = 10_000) {
  const r = await fetch(URL_, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body,
    signal: AbortSignal.timeout(timeoutMs),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`clickhouse ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r;
}

/**
 * Org-bound query. `{org:UUID}` is ALWAYS bound to the session's org, and the SQL
 * must reference it: a query without `{org:UUID}` is rejected at runtime, so a
 * page cannot forget the tenant filter (C7). Operators pass `orgId` explicitly.
 */
export async function chQuery<T>(
  session: Session,
  sql: string,
  params: Params = {},
  orgId: string = session.orgId,
): Promise<T[]> {
  if (!sql.includes("{org:UUID}")) throw new Error("query is not org-bound");
  if (orgId !== session.orgId && !session.isOperator) throw new Error("forbidden");
  const body = new FormData();
  body.set("query", sql + " FORMAT JSONEachRow");
  body.set("param_org", orgId);
  for (const [k, v] of Object.entries(params)) body.set(`param_${k}`, v === null ? "\\N" : String(v));
  const text = await (await post(READ, body)).text();
  return text ? text.trimEnd().split("\n").map((l) => JSON.parse(l) as T) : [];
}

/** Operator-only, cross-org (the admin Judge tab). No `{org}` binding by design. */
export async function chQueryAllOrgs<T>(session: Session, sql: string, params: Params = {}) {
  if (!session.isOperator) throw new Error("forbidden");
  /* same as above without the org check */
}

/** Append-only INSERT of JSON rows; the body is data, never SQL. */
export async function chInsert(table: "infrx.scores", rows: object[]) {
  const body = new FormData();
  body.set("query", `INSERT INTO ${table} FORMAT JSONEachRow`);
  body.set("data", rows.map((r) => JSON.stringify(r)).join("\n"));
  await post(WRITE, body);
}
```

`DateTime64` parameters are passed as ISO strings (`param_from=2026-09-20 18:00:00.000`);
ClickHouse parses them for `{from:DateTime64(3)}`. `LIMIT` is a literal in the
SQL (always 51, §3.1), never a parameter.

### 1.2 Caddy on `infrx-obs` (the only thing between Vercel and `:8123`)

```
obs.callbill.ai {
    # Two bearer tokens, two ClickHouse users. Everything else is 401.
    @read   { path /ch*  header Authorization "Bearer {env.CONSOLE_TOKEN}" }
    @write  { path /ch*  header Authorization "Bearer {env.WRITER_TOKEN}" }

    handle @read {
        request_header -Authorization
        request_header -X-ClickHouse-User
        request_header -X-ClickHouse-Key
        request_header X-ClickHouse-User console
        request_header X-ClickHouse-Key  {env.CH_CONSOLE_PASSWORD}
        uri strip_prefix /ch
        reverse_proxy 127.0.0.1:8123
    }
    handle @write {
        request_header -Authorization
        request_header -X-ClickHouse-User
        request_header -X-ClickHouse-Key
        request_header X-ClickHouse-User console_writer
        request_header X-ClickHouse-Key  {env.CH_WRITER_PASSWORD}
        uri strip_prefix /ch
        reverse_proxy 127.0.0.1:8123
    }
    respond 401
}
```

Client-supplied `X-ClickHouse-*` headers are stripped before ours are set, so a
leaked read token cannot impersonate another user. Rate limiting: Caddy has no
built-in limiter (it is a plugin) — ⚠️ use ClickHouse's own quota instead,
which is one statement: `CREATE QUOTA console_q FOR INTERVAL 1 minute MAX
queries = 600, MAX execution_time = 300 TO console` plus the user's
`readonly=1`, `max_execution_time=10`, `max_result_rows=10000` from
[`04`](04-data-model.md) §2.4. TLS from Let's Encrypt via Caddy, as the gateway
does today.

### 1.3 S3: content blob server-side, frames presigned — `lib/s3.ts`

Decision: **hand-rolled SigV4 with Web Crypto, no `@aws-sdk/*`.** The presigner
package drags `@aws-sdk/client-s3` (several MB, dozens of transitive packages)
for one `GET`. SigV4 is ~60 lines and AWS publishes a complete worked example
with a known signature for the query-string form
[src](https://docs.aws.amazon.com/AmazonS3/latest/API/sigv4-query-string-auth.html),
so correctness is a unit test against a vector, not a belief (U2). Not flimsy:
same algorithm, same vector.

Two entry points sharing one signing core:

| Function | Form | Used for | Why |
|---|---|---|---|
| `s3Get(key): Promise<Uint8Array>` | header-signed GET, server side | the **content blob** (`content/{org}/…json.gz`, ≤ 64 KB) | fetched in the server component after the row check; no bucket CORS, no CSP change, no client fetch. `DecompressionStream("gzip")` (Web API, in Node 18+) inflates it |
| `s3PresignGet(key, 60): string` | query-string presign | **judge frames** in `<img src>` | images need a URL; `<img>` needs neither CORS nor `connect-src`, only `img-src` |

Server-side is one fewer moving part than a presigned browser fetch (no CORS
rule on `infrx-traces`, no `connect-src`, no client component) at the cost of
~30 ms of S3 latency inside the page render, hidden by Suspense (§8).
[`03`](03-architecture.md) §5 and §4.2 and [`01`](01-requirements.md) C7 were
aligned to this on 2026-09-20: content is header-signed server-side, frames are
presigned.

Env (server only, Vercel sensitive vars; same values in SSM under
`/INFRX-OBS-PROD/*`):

| var | value |
|---|---|
| `CLICKHOUSE_PROXY_URL` | `https://obs.callbill.ai/ch` |
| `CLICKHOUSE_PROXY_TOKEN` | Caddy read token |
| `CLICKHOUSE_PROXY_WRITER_TOKEN` | Caddy writer token |
| `TRACES_S3_BUCKET`, `TRACES_S3_REGION` | `infrx-traces`, `us-east-1` |
| `TRACES_AWS_ACCESS_KEY_ID`, `TRACES_AWS_SECRET_ACCESS_KEY` | IAM user `infrx-console`, policy `s3:GetObject` on `content/*` and `frames/*` only — prefixed names so nothing else in the Vercel project picks them up |

## 2. Routes and files

```
app/(console)/
  traces/
    page.tsx                 list: tiles + filters + table + cursor (C1)
    controls.tsx             client: range/key (copied from usage-controls.tsx) + status/video/finish/latency/score/metadata
    table.tsx                server: the rows; a <Link> per row to /traces/[id]
    [id]/
      page.tsx               detail (C2): row + scores + judge items; Suspense around content
      content.tsx            server: s3Get → inflate → render request / output / think / error / raw
      waterfall.tsx          server: bars from t_*_ms (pure CSS widths)
      feedback.tsx           client: thumbs / rating / correction, optimistic, calls actions.ts (F3)
      actions.ts             "use server": postFeedback
  api-keys/
    trace-level-select.tsx   client: Select off|metadata|full + judge switch (owners) (C3)
    actions.ts               += setTraceLevel, setJudgeEnabled
  settings/
    page.tsx                 org settings (C4): retention, media_copy, judge budget/sample/frames/rubric, egress consent
    form.tsx                 client form → actions.ts
    actions.ts               "use server": updateTraceSettings
  admin/
    page.tsx                 += org selector (?org=) that scopes a Traces link, and a Judge tab (C5)
    judge-tab.tsx            server: judge_runs per day + cost
  docs/page.tsx              += <Section title="Traces"> (C6, copy in §4a)
components/sidebar.tsx       NAV += { href: "/traces", label: "Traces", icon: ScrollText }  (lucide)
                             NAV += { href: "/settings", label: "Settings", icon: Settings2 }
lib/clickhouse.ts            §1.1
lib/s3.ts                    §1.3
lib/types.ts                 += TraceRow, ScoreRow, TraceContent (§5)
lib/clickhouse.test.ts, lib/s3.test.ts   (§9)
```

No new npm dependency. `Tabs`, `Select`, `Table`, `Badge`, `Card`, `Skeleton`
already exist in `components/ui`.

## 3. Queries

All ClickHouse SQL, parameters in `{}`; `{org:UUID}` is bound by
`chQuery` (§1.1). `FINAL` on `traces` because it is a `ReplacingMergeTree`
([`04`](04-data-model.md) §2.1); `scores` is plain `MergeTree`, no `FINAL`.

### 3.1 List page

```sql
SELECT id, ts, api_key_id, status, error_type, model_id, stream, trace_level,
       n_video, media_duration_s, prompt_tokens, completion_tokens, multimodal_tokens,
       ttft_ms, wall_ms, finish_reasons, cost_usd, content_ref IS NOT NULL AS has_content
FROM infrx.traces FINAL
WHERE org_id = {org:UUID}
  AND ts >= {from:DateTime64(3)} AND ts < {to:DateTime64(3)}
  AND ({key:Nullable(UUID)} IS NULL OR api_key_id = {key:Nullable(UUID)})
  AND ({cls:UInt8} = 0 OR intDiv(status, 100) = {cls:UInt8})          -- 2 | 4 | 5
  AND ({video:UInt8} = 0 OR n_video > 0)
  AND ({fr:String} = '' OR has(finish_reasons, {fr:String}))
  AND wall_ms >= {min_ms:UInt32}
  AND ({mk:String} = '' OR metadata[{mk:String}] = {mv:String})
  AND ({scored:UInt8} = 0 OR id IN (
        SELECT trace_id FROM infrx.scores
        WHERE org_id = {org:UUID} AND ts >= {from:DateTime64(3)} - INTERVAL 30 DAY))
  AND (ts, id) < ({cur_ts:DateTime64(3)}, {cur_id:UUID})               -- cursor; first page passes now()+1s, max UUID
ORDER BY ts DESC, id DESC
LIMIT 51
```

51 rows: 50 to show, one to know there is a next page; the cursor is the 50th
row's `(ts, id)`, encoded in `?after=<ts>_<id>`. Filters are `?`-params like
Usage (`range`, `key`, plus `status`, `video`, `finish`, `min_ms`, `meta`,
`scored`), so links are shareable and the page stays a server component.

Score badges for the 50 rows come from one second query
(`argMax(value_bool, ts)` for `judge.overall_pass` and `argMax(value_num, ts)` for
`rating`/`thumb` per `trace_id IN ({ids:Array(UUID)})`) rather than a join
inside the paged query — two cheap queries, no `FINAL` + `JOIN` planning.

Why each filter is cheap against [`04`](04-data-model.md) §2.1: `org_id, ts`
are the primary key prefix (range scan, not a scan); `status` has a `set(16)`
skip index; `finish_reasons` and `mapKeys/mapValues(metadata)` have bloom
filters, so `has()` and `metadata[k] = v` skip granules; `n_video` and
`wall_ms` are plain columns filtered after the key prune — within a 30-day org
range that is ≤ a few hundred thousand rows at pilot, milliseconds. The
`has_score` subquery is bounded by `(org_id, ts)` on `scores`' own key.

### 3.2 Header tiles

```sql
SELECT count() AS requests,
       countIf(status >= 400) / count()                        AS error_rate,
       countIf(has(finish_reasons, 'length')) / count()        AS length_rate,
       quantile(0.5)(wall_ms)                                  AS latency_p50_ms,
       countIf(content_ref IS NOT NULL) / count()              AS full_share
FROM infrx.traces FINAL
WHERE org_id = {org:UUID} AND ts >= {from:DateTime64(3)} AND ts < {to:DateTime64(3)}
  AND ({key:Nullable(UUID)} IS NULL OR api_key_id = {key:Nullable(UUID)});

SELECT avgIf(value_bool, name = 'judge.overall_pass')           AS judge_pass_rate,
       quantileIf(0.5)(value_num, name = 'judge.groundedness')  AS groundedness_p50,
       count()                                                  AS judge_scores
FROM infrx.scores
WHERE org_id = {org:UUID} AND source = 'judge'
  AND ts >= {from:DateTime64(3)} AND ts < {to:DateTime64(3)};
```

The criterion names are the `scores.name` values fixed in
[`06`](06-feedback-and-judge-spec.md) §1: `judge.relevance`,
`judge.groundedness`, `judge.completeness`, `judge.format`, `judge.refusal`
(numeric 1–5) and `judge.overall_pass` (boolean); human scores are `thumb`,
`rating`, `correction`. Tiles:
Requests · Error rate · `length` rate · Latency p50 · Judge pass · Groundedness
p50 (the last two show "—" with hint "no judge scores" when `judge_scores = 0`).

### 3.3 Detail page

```sql
SELECT * FROM infrx.traces FINAL WHERE org_id = {org:UUID} AND id = {id:UUID} LIMIT 1;

SELECT name, source, argMax(kind, ts) AS kind, argMax(value_num, ts) AS value_num,
       argMax(value_bool, ts) AS value_bool, argMax(value_label, ts) AS value_label,
       argMax(value_text, ts) AS value_text, argMax(comment, ts) AS comment,
       argMax(rationale, ts) AS rationale, argMax(judge_model, ts) AS judge_model,
       argMax(rubric_version, ts) AS rubric_version, max(ts) AS ts, count() AS n
FROM infrx.scores
WHERE org_id = {org:UUID} AND trace_id = {id:UUID}
GROUP BY name, source ORDER BY source, name;

SELECT run_id, ts, status, reason, frames_sent, input_tokens, output_tokens, error
FROM infrx.judge_items FINAL
WHERE org_id = {org:UUID} AND trace_id = {id:UUID} ORDER BY ts DESC;
```

The first query is the **ownership check**: no row → `notFound()` before any
S3 access (C7). Both `scores` and `judge_items` are keyed `(org_id, trace_id, …)`,
so these are point lookups.

### 3.4 API key stats (API Keys page, per row)

```sql
SELECT api_key_id, count() AS requests_7d, countIf(content_ref IS NOT NULL) AS full_7d
FROM infrx.traces FINAL
WHERE org_id = {org:UUID} AND ts >= now() - INTERVAL 7 DAY
GROUP BY api_key_id
```

Shown as a muted "n traced · m with content" hint under the level selector, so
a developer sees the toggle take effect (C3 acceptance).

### 3.5 Admin Judge tab (`chQueryAllOrgs`, operators only)

```sql
SELECT toDate(started_at) AS day, org_id, count() AS runs,
       sum(n_selected) AS selected, sum(n_succeeded) AS ok, sum(n_errored + n_expired) AS failed,
       sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
       sum(cost_usd_est) AS cost_est, sum(cost_usd_actual) AS cost_actual
FROM infrx.judge_runs FINAL
WHERE started_at >= {from:DateTime64(3)}
GROUP BY day, org_id ORDER BY day DESC, cost_actual DESC
```

Joined to org names from Supabase (`createAdminClient()`, as the admin page
already does) in JS.

## 4. Pages

### 4.1 `/traces` (C1)

`PageHeader` title "Traces", subtitle "Per-request traces for keys with
tracing on. Last {range}." Action slot: `<TraceControls keys=… />`.

Tiles (§3.2) in the same `grid gap-3 sm:grid-cols-2 lg:grid-cols-3` as Usage.

Table columns, in this order (C1's list): **Time** (`dateTime`), **Key**
(name from the org's `api_keys` fetched once, else prefix of the id), **Status**
(`Badge`: 2xx default, 4xx secondary, 5xx destructive), **Model**, **Video s**
(`media_duration_s` or "—"), **Prompt / output tokens** (`num`; `multimodal_tokens`
as hint on hover), **TTFT** (`ms`), **Latency** (`ms`), **Finish**
(`finish_reasons.join(",")`, `length` in destructive colour), **Scores**
(👍/👎 from `thumb`, `rating` as `4/5`, judge `pass`/`fail` badge). Row click →
`/traces/[id]`. Footer: "Next" link carrying the cursor; "Newer" is the browser
back button (no bidirectional cursor in v1).

Empty states:

| condition | message |
|---|---|
| org has no key above `off` | "Tracing is off for every key. Turn it on per key under API Keys." (link) |
| keys at `metadata` only, rows exist | table shows rows; **Scores** column hint "content is not captured for this key — set it to *full* to enable judge scores" |
| filters match nothing | "No traces match." + "Clear filters" link |

Acceptance (C1): 10k rows in the range → first page renders < 1 s p95; every
filter combination is a URL; the Usage range/key selection carries over via the
same param names.

### 4.2 `/traces/[id]` (C2)

Layout, top to bottom:

1. **Header** — `PageHeader` title = short id (`3f0c…`) with a copy button for
   the full `Inference-Id`; subtitle line: key name · status badge · `model_id`
   · `artifact_id` (mono, truncated, full on hover) · `dateTime(ts)` · level badge.
2. **Waterfall** — one row per stage with a bar whose left/width are
   percentages of `wall_ms`: auth `0→t_auth_ms`, media `t_auth→t_media`, admit
   `t_media→t_admit`, upstream first byte `t_admit→t_first_byte`, TTFT
   `t_first_byte→ttft_ms`, generation `ttft→wall`. Null stages are omitted (a
   pre-Phase-2 trace has `t_admit = t_media`; an error trace stops at the last
   non-null). Pure CSS, no chart lib.
3. **Tokens** — six `StatTile`s: Text prompt (`prompt_tokens − multimodal_tokens`,
   "—" if either null), Multimodal, Cached, Output, Reasoning chars
   (`think_chars`), TPOT (`ms(tpot_ms)`); hint on the first: "text = prompt −
   multimodal".
4. **Request** (`content.tsx`, Suspense) — messages rendered by role in
   `Card`s; text parts as `<pre class="whitespace-pre-wrap">`; a video part renders
   a metadata block (`sha256` mono, `media_duration_s`, `media_frames` @
   `media_fps`, `media_source`, `media_bytes`) and, if `frames/{org}/{sha}/index.json`
   exists, a strip of `<img src={s3PresignGet(…)} />` thumbnails with `t_s`
   captions. `params` as a small `dl`. Client `metadata` as `Badge`s.
5. **Output** — one card per choice: `content` as pre-wrap text; `finish_reason`
   badge; `tool_calls` (parsed) as JSON, with the raw string behind a toggle
   when parse failed.
6. **Reasoning (not sent to the client)** — collapsed by default (`<details>`),
   the `reasoning_content`; the label is exactly that string, because the
   customer's users never saw it.
7. **Error** — only when `status ≥ 400`: `error_type` / `error_code` /
   `error_message`, and `response.error.upstream_body` if present.
8. **Scores + feedback** (`feedback.tsx`) — a table of §3.3's latest-per-name
   rows grouped by `source` (user · operator · judge); judge rows show
   `rationale` inline and `judge_model`/`rubric_version` as hint. Controls
   (F3): 👍 / 👎 (`name='thumb'`, `kind='boolean'`), a 1–5 `Select`
   (`name='rating'`, `kind='numeric'`), a textarea "What should the answer have
   been?" (`name='correction'`, `kind='text'`), optional comment. Optimistic:
   the row appears immediately with `source='operator'`; on action error,
   `toast.error` and revert (same pattern as `revoke-button.tsx`).
9. **Raw JSON** — `<details>` with the `traces` row and the blob, pretty-printed.
10. **Copy as cURL** — a `Snippet`-style button that rebuilds
    `POST {base_url}/chat/completions` from `content.request` with
    `Authorization: Bearer {{KEY}}`; the video part is emitted as the original
    `http(s)` URL when `media_source = 'url'`, and as
    `"url": "infrx://media/sha256:…"` with a comment line
    `# data: clip not replayable — enable media_copy in Settings to keep clips`
    when `media_source = 'data'` and there is no `media_s3_key`. State this
    limitation in the tooltip; do not pretend a `data:` request is replayable.

`metadata`-level trace: sections 4–6, 9 (blob half) and 10 are replaced by one
notice card: "Content was not captured for this request (key level:
*metadata*). Set the key to *full* under API Keys to store prompts, outputs and
reasoning." Sections 1–3, 7, 8 render as normal — scores can still be posted
against a metadata-level trace.

### 4.3 API Keys (C3)

Two new columns between **Last used** and the revoke button: **Tracing**
(`trace-level-select.tsx`: `Select` off / metadata / full; owners only, others
see a `Badge`) and **Judge** (a checkbox-styled `Button` toggle; disabled with
tooltip "requires *full*" when level ≠ full, and "org has not consented to
judge egress — Settings" when `organizations.judge_egress_ok = false`). Under
the level, the §3.4 hint. `PageHeader` subtitle gains: "Tracing takes effect
within 60 s (the gateway's key cache)."

### 4.4 `/settings` (C4)

Owner-only form (members see values read-only), one `Card` per group:

| Group | Fields | Control |
|---|---|---|
| Retention | `trace_retention_days` (7–365) | `Input type=number` |
| Media | `media_copy` | toggle; helper text: "keeps a copy of each clip sent as data: so traces can be replayed and judged (storage billed per GB)" |
| Judge | `judge_sample_pct` (0–100), `judge_daily_budget_usd` (≥ 0), `judge_frames_k` (1–20), `judge_rubric` (textarea, prefilled with the default from [`06`](06-feedback-and-judge-spec.md)), `judge_rubric_version` (read-only; the action bumps it when the rubric text changes) | inputs |
| Consent | `judge_egress_ok` | checkbox with the statement from §4a; saving with it checked stamps `_by`/`_at`; unchecking clears them |

Save → `updateTraceSettings` → `toast.success`; the gateway sees `media_copy`
via the auth row embed within 60 s; the judge reads the org row per run.

### 4.5 Admin (C5)

`?org=<id>` selector (`Select` over all orgs) above the existing table; the
selected org gets a "View traces" link to `/traces?org=<id>` — the Traces page
accepts `?org=` **only** when `session.isOperator` (passed as the fourth
argument of `chQuery`), otherwise ignored. New `Tabs`: "Organizations"
(existing) · "Judge" (§3.5 table + a daily cost line as a plain table; no chart).

## 4a. Docs copy (C6) — `<Section title="Traces">`

> **Traces.** Tracing is off by default for content and on for metadata: every
> request already produces a metadata row (tokens, timings, status, cost) that
> you see on Usage. Set a key's level on the API Keys page:
>
> - `off` — nothing beyond the usage row is stored.
> - `metadata` (default) — one trace row per request: parameters, token
>   breakdown (text / video / cached / output), timings, finish reason, video
>   duration and frame count, and the clip's SHA-256 — **never the prompt,
>   the clip or the answer.**
> - `full` — the trace row plus the exact request messages, the answer as you
>   received it, and the model's reasoning block that the gateway strips
>   before responding. Clips sent as `data:` URLs are stored by hash only
>   unless *Keep clip copies* is on in Settings.
>
> Override per request with `X-Infrx-Trace: off | metadata | full`. The
> header can lower a key's level, never raise it. Optional context headers:
> `X-Infrx-Session-Id` (groups turns of one conversation) and
> `X-Infrx-User-Id` (hashed before storage). You can also send OpenAI's
> `metadata` field — up to 16 string pairs — and filter by them on Traces.
>
> **Feedback.** Attach a score to any request with its `Inference-Id`:
>
> ```
> POST /v1/feedback
> Authorization: Bearer sk-infrx-…
> {"trace_id": "<Inference-Id>", "name": "thumb", "value": true, "comment": "…"}
> ```
>
> `name` is `thumb` (boolean), `rating` (1–5), `correction` (the answer you
> expected, as text), or any label of your own. Scores show on the trace page
> and in the Scores column.
>
> **Retention.** Trace rows are kept 13 months; captured content 90 days by
> default (7–365, Settings); clip copies follow the same setting. Deleting
> your organization's traces is a request to support and completes within
> 24 hours.
>
> **Automatic judging.** If you enable *Judge* on a key **and** an owner has
> accepted the consent statement in Settings, a sample of that key's `full`
> traces (all failures, plus the percentage you set) is scored by an
> Anthropic Claude model against your rubric. This sends the request text,
> up to 8 sampled frames of the clip, and the model's answer to Anthropic's
> API, which does not train on API inputs. Judging is asynchronous, capped by
> your daily budget, never in the request path, and the exact set of traces
> sent is visible on each trace page under Scores → judge.

## 5. Types (`lib/types.ts`)

```ts
export type TraceLevel = "off" | "metadata" | "full";

/** One infrx.traces row as returned by JSONEachRow (04 §2.1). Nullable → `| null`. */
export type TraceRow = {
  id: string; org_id: string; api_key_id: string | null; ts: string; ingested_at: string;
  model_id: string; served_model: string; artifact_id: string; endpoint_role: string;
  gateway_version: string; trace_level: Exclude<TraceLevel, "off">;
  session_id: string | null; user_hash: string | null; metadata: Record<string, string>;
  request_id_upstream: string | null;
  stream: boolean; requested_model: string; params: string;
  temperature: number | null; top_p: number | null; max_tokens: number | null; seed: number | null;
  n: number; response_format_type: string | null; n_messages: number; n_system: number;
  n_video: number; n_image: number; n_tools: number; prompt_chars: number;
  prompt_hash: string; prompt_stack_hash: string; request_bytes: number;
  media_sha256: string | null; media_bytes: number | null; media_mime: string | null;
  media_duration_s: number | null; media_frames: number | null; media_fps: number | null;
  media_source: "url" | "data" | "upload" | null; media_s3_key: string | null;
  media_cache_hit: string | null; mm_kwargs: string;
  status: number; error_type: string | null; error_code: string | null; error_message: string | null;
  finish_reasons: string[]; n_choices: number; schema_valid: boolean | null; client_aborted: boolean;
  prompt_tokens: number | null; completion_tokens: number | null; cached_tokens: number | null;
  multimodal_tokens: number | null; think_chars: number; output_chars: number;
  t_auth_ms: number | null; t_media_ms: number | null; t_admit_ms: number | null;
  t_first_byte_ms: number | null; ttft_ms: number | null; wall_ms: number; tpot_ms: number | null;
  sse_chunks: number; concurrency_at_admission: number;
  cost_usd: string; input_usd_per_m: string; output_usd_per_m: string; price_table_version: string;
  content_ref: string | null; content_bytes: number; content_sha256: string | null;
  redaction_status: string;
};

/** infrx.scores (04 §2.2); the list page and detail use the argMax-collapsed shape. */
export type ScoreRow = {
  name: string; source: "user" | "operator" | "judge"; kind: "numeric" | "boolean" | "label" | "text";
  value_num: number | null; value_bool: boolean | null; value_label: string | null; value_text: string | null;
  comment: string | null; rationale: string | null; judge_model: string | null;
  rubric_version: number | null; ts: string; n: number;
};

/** The content blob (04 §3.1). */
export type TraceContent = {
  v: 1; id: string; org_id: string; ts: string;
  request: { model: string; messages: TraceMessage[]; params: Record<string, unknown>;
             tools?: unknown[]; headers: Record<string, string>; metadata?: Record<string, string> };
  upstream: { mm_processor_kwargs: Record<string, unknown> | null; id: string | null; usage: Record<string, unknown> | null };
  response: { status: number; choices: TraceChoice[]; sse_chunks: number; client_aborted: boolean;
              error: { type: string; code?: string; message: string; upstream_body?: string } | null };
};
export type TraceMessage = { role: string; content: string | TracePart[] };
export type TracePart =
  | { type: "text"; text: string }
  | { type: "video_url"; video_url: { url: string; source?: "url" | "data" | "upload" } }
  | { type: string; [k: string]: unknown };
export type TraceChoice = { index: number; finish_reason: string | null; content: string | null;
  reasoning_content: string | null; tool_calls_raw: string | null; tool_calls: unknown | null };
```

Validation: ClickHouse returns exactly the columns selected, and the blob is
written by our own gateway, so v1 uses one hand-written guard,
`isTraceContent(x): x is TraceContent` (checks `v === 1`, `request.messages`
is an array, `response.choices` is an array) and renders "unreadable trace
content" on failure. Zod is not in `dependencies` and is not added; note it as
the upgrade if the blob schema gets a `v: 2`. `Decimal` columns arrive as
strings — `money(Number(row.cost_usd))`, same as Supabase numerics today.

## 6. Server actions

All follow `api-keys/actions.ts`: `"use server"`, `getSession()`, role check,
Supabase with the user's JWT (RLS), `revalidatePath`.

| Action | File | Guard | Body |
|---|---|---|---|
| `setTraceLevel(keyId, level)` | `api-keys/actions.ts` | `role === "owner"`; `level ∈ TraceLevel` | `update api_keys set trace_level where id, org_id`; if `level !== "full"` also `judge_enabled = false` (J5 precondition); `revalidatePath("/api-keys")` |
| `setJudgeEnabled(keyId, on)` | same | owner; if `on`: the key's `trace_level === "full"` **and** `organizations.judge_egress_ok` (re-read, not trusted from the client) | `update api_keys set judge_enabled` |
| `updateTraceSettings(fields)` | `settings/actions.ts` | owner; range checks mirroring the SQL `check`s (7–365, 0–100, ≥ 0, 1–20) so the error is a sentence not a Postgres code | `update organizations set …`; if `judge_rubric` changed → `judge_rubric_version + 1`; if `judge_egress_ok` turned on → `judge_egress_ok_by = session.userId, judge_egress_ok_at = now()`; turned off → both `null` **and** `update api_keys set judge_enabled = false where org_id` |
| `postFeedback(traceId, name, value, comment?)` | `traces/[id]/actions.ts` | any member; `traceId` is a UUID; `name` ∈ {thumb, rating, correction} ∪ `/^[a-z][a-z0-9_]{0,31}$/`; ownership by `chQuery(… WHERE org_id={org} AND id={id})` **before** writing | `chInsert("infrx.scores", [{ id: crypto.randomUUID(), trace_id, org_id: session.orgId, ts, name, source: "operator", kind, value_*, comment, author: session.userId }])`; `revalidatePath("/traces/" + traceId)` |

`source` is `operator` for anything posted from the console (the gateway's
`/v1/feedback` writes `user`), matching F4.

## 7. Security checklist (C7)

- [ ] Every ClickHouse read goes through `chQuery`, which refuses SQL without `{org:UUID}` and binds it from `getSession()`; `?org=` is honoured only for `session.isOperator` (U1).
- [ ] No ClickHouse call from a client component: `lib/clickhouse.ts` and `lib/s3.ts` import `server-only`.
- [ ] Content and frames are fetched/presigned **only after** the §3.3 ownership query returned a row; the S3 key is taken from that row's `content_ref`/`media_sha256`, never from the URL.
- [ ] Presigned frame URLs expire in 60 s; the blob is never exposed as a URL.
- [ ] All prompt/output/metadata text is rendered as React text nodes (auto-escaped); no `dangerouslySetInnerHTML`; `<pre>` for whitespace.
- [ ] `next.config.ts` gains a CSP header with `img-src 'self' data: https://infrx-traces.s3.us-east-1.amazonaws.com` (frames); no `connect-src` change because the blob is read server-side. ⚠️ The app has no CSP today — adding one is a small standalone change to verify against every page first.
- [ ] Operator gating: `notFound()` when `!session.isOperator` (as `admin/page.tsx`); the Judge tab and `chQueryAllOrgs` share that check.
- [ ] Tokens and IAM keys are Vercel sensitive vars; the IAM user can only `GetObject` under `content/*` and `frames/*`; ClickHouse `console` user is `readonly=1`.
- [ ] `postFeedback` validates `name`, `kind`/value shape and comment length (≤ 2,000) before the INSERT; the INSERT body is JSON data, not SQL.

## 8. Performance

| Page | Work | Expected (`est.`) |
|---|---|---|
| `/traces` | 3 ClickHouse queries (tiles, judge tiles, page) in `Promise.all`, 1 scores query for badges, 1 Supabase key-name query | ClickHouse: primary-key range on ≤ 10⁵ rows, < 50 ms each on `infrx-obs`; Vercel ↔ obs round trip ~20–40 ms (both us-east-1) → **< 300 ms** server time; C1's < 1 s p95 has 3× headroom |
| `/traces/[id]` | 3 point queries + 1 S3 GET (≤ 64 KB) + inflate + optional frame index GET | ~150 ms + S3 ~30 ms; `content.tsx` is inside `<Suspense fallback={<Skeleton/>}>` so the header, waterfall, tokens and scores paint first |
| `/api-keys` | + one grouped query (§3.4) | negligible |
| `/settings` | Supabase only | as today |

The console layout is already `force-dynamic`; no caching layer is added. If
the tiles get slow at scale, the fix is a ClickHouse materialized view per
`(org_id, toStartOfHour(ts))`, not a console cache — noted, not built.

## 9. Test plan

Unit tests run with the existing `pnpm test` (`node --test lib/*.test.ts`);
no framework added.

| id | file | asserts | covers |
|---|---|---|---|
| U1 | `lib/clickhouse.test.ts` | `chQuery` throws on SQL lacking `{org:UUID}`; binds `param_org` = session org; a non-operator passing another `orgId` throws; params are sent as `param_<k>` form fields, never spliced into `query` (inspect a stubbed `fetch`'s `FormData`); `FORMAT JSONEachRow` appended once | C7 |
| U2 | `lib/s3.test.ts` | the query-string presign for AWS's documented example (`examplebucket`, `test.txt`, `20130524T000000Z`, expires 86400, region `us-east-1`, key `AKIAIOSFODNN7EXAMPLE`) reproduces the signature printed on the doc page [src](https://docs.aws.amazon.com/AmazonS3/latest/API/sigv4-query-string-auth.html) — ⚠️ copy the vector from the page into the test, do not transcribe it from memory; header-signed GET produces a `Authorization` with the same credential scope; `X-Amz-Expires=60` | O5 |
| U3 | `lib/s3.test.ts` | `inflate()` round-trips a gzip of a known JSON; `isTraceContent` rejects `{v: 2}` and non-array `choices` | C2 |
| U4 | `lib/types.test.ts` (or inline) | `parseFilters(searchParams)` (the pure function behind `controls.tsx`) clamps `min_ms ≥ 0`, maps `status=4` → `cls=4`, ignores `?org=` unless `isOperator` | C1, C7 |

Server-action permission tests would need a Supabase stub the repo does not
have; they are covered by the manual checklist and by RLS itself (a member's
`update api_keys` returns zero rows under `api_keys_update_owner`).

**E1 — manual end-to-end (release gate for Phase 4 of [`08`](08-phases-and-test-plan.md))**

1. As owner: create key K, level shows `metadata` by default (T2). Call the API with K; within 60 s `/traces` lists the request, Scores hint says content not captured (C1, C2 notice).
2. Set K to `full`; call again; the new row shows the video seconds, and its detail page renders request, output, reasoning (collapsed, correctly labelled), waterfall and tokens (C2, C3).
3. Send `X-Infrx-Trace: off` with K; no new row appears (T3). Send `metadata`; a row without content appears.
4. Post 👍 and a correction from the detail page; both appear under *operator* immediately; reload persists them (F3). `GET`-equivalent check: the gateway's `/v1/feedback` listing shows them.
5. As a member (non-owner): the level `Select` is a badge, Settings is read-only, feedback still works.
6. Settings: set `judge_sample_pct=100`, budget `$1`, accept consent (stamps by/at); API Keys: enable Judge on K (enabled only now). After the next judge run, the trace shows judge rows with rationale; the list badge shows pass/fail; the admin Judge tab shows the run and cost (C4, C5, J-rows).
7. Uncheck consent → K's Judge toggle is off and disabled (J5 invariant).
8. As operator: `/traces?org=<other org>` works; as a member of another org the same URL shows only their own rows (C7).
9. Open a trace URL from another org directly → 404 (C7).

## 10. Open questions

1. ~~Server-side blob fetch vs presigned browser fetch~~ — **resolved 2026-09-20**: server-side for content, presigned for frames; `03` §4.2/§5 and `01` C7 aligned.
2. ~~Score criterion names~~ — **resolved**: taken from `06` §1 (`judge.*` prefix), see §3.2.
3. ⚠️ **CSP** — the app has none; adding one for `img-src` needs a pass over the login/auth pages (Supabase redirects) before it ships.
4. ⚠️ **Operator cross-org Traces via `?org=`** vs a dedicated `/admin/traces` route: `?org=` is fewer files; revisit if operators need it often.
5. ⚠️ **`Decimal` → string** in JSONEachRow: confirm ClickHouse's default (`output_format_json_quote_decimals`) on the pinned image so `money()` gets a number.
6. ⚠️ Vercel egress has no fixed IP, so the Caddy token is the whole perimeter for reads; if a static egress (Vercel Secure Compute) is bought later, add an IP allowlist in Caddy — one matcher line.

---

## Verification log

- 2026-09-20 — Written against `apps/app` on `main` (`5210c67`): `usage/page.tsx`, `ranges.ts`, `usage-controls.tsx`, `api-keys/{page,actions,revoke-button}.tsx`, `admin/page.tsx`, `components/sidebar.tsx`, `lib/{session,format,types}.ts`, `lib/supabase/{server,admin}.ts`, `lib/*.test.ts`, `package.json` (`"test": "node --test lib/*.test.ts"`; deps have no Zod, no aws-sdk, no ClickHouse client), `components/ui/*` (Tabs, Select, Table, Badge, Card, Skeleton present), `next.config.ts` (empty — no CSP). ClickHouse parameterised-query form (`param_<name>`, `{name:Type}`) and header auth from the HTTP interface doc fetched this session. SigV4 query-string doc cited for U2; ⚠️ the example signature is **not** reproduced here on purpose. Column names checked against [`04`](04-data-model.md) §2.1–2.3 and §3.1 as written today; judge criterion names against [`01`](01-requirements.md) J3 only (06 not yet written). Nothing here has been executed.

### Implementation-plan amendment log — 2026-09-20

Documentation reconciliation only: incorporated the unified plan and review corrections above. Historical measurements and previous verification entries remain unchanged; new implementation/live tests are pending.
