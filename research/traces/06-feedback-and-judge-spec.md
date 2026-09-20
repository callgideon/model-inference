# Deep traces — feedback and LLM-as-judge spec

> **Implementation amendment — 2026-09-20.** The current implementation authority is [the unified plan](../plan/README.md), especially [contracts](../plan/01-contracts.md), [durable protocols](../plan/02-durable-protocols.md) and [verification](../plan/04-verification.md). The text below is historical research where it conflicts. Trace loss does not stop inference; capture has active-byte, queue, spool and disk limits, and local durability begins at fsync on persistent storage. Off-mode requests have no CH trace row and are excluded from trace-coverage denominators. Canonical logical content is preserved, not raw HTTP wire bytes after normalization. Feedback 201 requires PG/outbox durability and tenant ownership independent of CH lag. Channel and author role are separate; customer console feedback is not an operator calibration label. Judge requires current consent, hard worst-case budget reservations and ambiguous-submit quarantine. CH schema/version/dedup must run against the pinned server; logical expiry is enforced before physical TTL deletion. Retention: 24h results, 7d processing cache, <=90d optional full content, 13mo metadata.


Design date **2026-09-20**. The quality loop over the traces of
[`03-architecture.md`](03-architecture.md): the score model, the three ways a
score gets written, and `judge.py`, the asynchronous LLM-as-judge job.
Implements [`01-requirements.md`](01-requirements.md) F1–F4, J1–J7, D8, NF5,
NF8 over the tables in [`04-data-model.md`](04-data-model.md) §2.2–§2.3 and
the S3 layouts in `04` §3. Gateway-side endpoint mechanics are in
[`05-gateway-capture-spec.md`](05-gateway-capture-spec.md); console surfaces in
[`07-console-spec.md`](07-console-spec.md).

Legend: [`../METHODOLOGY.md`](../METHODOLOGY.md#legend). Anthropic API shapes
below follow the official Python SDK (`anthropic`), Message Batches
[src](https://platform.claude.com/docs/en/build-with-claude/batch-processing),
vision limits [src](https://platform.claude.com/docs/en/build-with-claude/vision).

---

## 1. Score model

One `scores` row per signal, append-only, keyed by `trace_id`
([`04`](04-data-model.md) §2.2). `name` fixes `kind` and which `value_*`
column is set:

| `name` | `kind` | column | range | `source` | meaning |
|---|---|---|---|---|---|
| `thumb` | boolean | `value_bool` | true = up | user, operator | explicit like/dislike |
| `rating` | numeric | `value_num` | 1–5 | user, operator | explicit quality |
| `correction` | text | `value_text` | any | user, operator | what the answer *should* have been (the future SFT target) |
| `comment` | text | `value_text` | any | user, operator | free text, never aggregated |
| `judge.relevance` | numeric | `value_num` | 1–5 | judge | answers the question that was asked |
| `judge.groundedness` | numeric | `value_num` | 1–5 | judge | claims are supported by the frames |
| `judge.completeness` | numeric | `value_num` | 1–5 | judge | covers what the frames show that the question needs |
| `judge.format` | numeric | `value_num` | 1–5 | judge | obeys length/format/language instructions; JSON valid if requested |
| `judge.refusal` | numeric | `value_num` | 1–5 | judge | 5 = answered when it should, or refused when it should; 1 = wrong call |
| `judge.overall_pass` | boolean | `value_bool` | | judge | the rubric's pass rule (§3.3) |
| `derived.regenerate`, `derived.abandon` | boolean | `value_bool` | | derived | §2c, later |

Judge rows carry `rationale`, `judge_run_id`, `judge_model`, `rubric_id`,
`rubric_version`; a new rubric version is a new series, never an overwrite (J4).

**Latest wins**, per `(trace_id, name, source)`:

```sql
SELECT trace_id, name, source,
       argMax(value_num,  ts) AS value_num,
       argMax(value_bool, ts) AS value_bool,
       argMax(value_text, ts) AS value_text
FROM infrx.scores
WHERE org_id = {org:UUID} AND trace_id IN ({ids:Array(UUID)})
GROUP BY trace_id, name, source
```

**Per key / per day** (the console's score tiles, [`07`](07-console-spec.md)):

```sql
WITH latest AS (
  SELECT trace_id, name, source, argMax(value_num, ts) AS v, argMax(value_bool, ts) AS b
  FROM infrx.scores
  WHERE org_id = {org:UUID} AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)}
  GROUP BY trace_id, name, source)
SELECT t.api_key_id, toDate(t.ts) AS day, l.name, l.source,
       count() AS n, avg(l.v) AS mean, quantile(0.5)(l.v) AS p50,
       countIf(l.b) / nullIf(countIf(l.b IS NOT NULL), 0) AS pass_ratio
FROM latest l
JOIN infrx.traces t FINAL ON t.id = l.trace_id AND t.org_id = {org:UUID}
GROUP BY t.api_key_id, day, l.name, l.source
```

`rubric_version` is a filter on the tiles: mixing versions in one mean is the
error J4 exists to prevent.

## 2. Feedback surfaces

### (a) API — `POST /v1/feedback`

Contract only; the gateway mechanics (auth, ownership check, queue) are
[`05`](05-gateway-capture-spec.md).

| | |
|---|---|
| Auth | `Authorization: Bearer <api key>`; the trace must belong to the key's org (F1) |
| Body | `{"trace_id": "<Inference-Id>", "name": "thumb"\|"rating"\|"correction"\|"comment", "value": true\|1..5\|"text", "comment"?: "…"}` |
| Response | `201 {"id": "<score id>"}`; `404` unknown/foreign trace; `400` bad `name`/`value` type or out of range; `503` ownership check unavailable (never a silent accept) |
| Row | `source='user'`, `author=api_key_id`, `kind` from the table in §1 |
| Read | `GET /v1/feedback?trace_id=` → latest-wins rows for that trace (F3 acceptance) |

### (b) Console

Server action in the trace detail page (`C2`, `F3`): thumbs, 1–5 rating,
correction text. It POSTs one JSON row to the Caddy proxy on `infrx-obs` with
the `console_writer` token (`INSERT ON infrx.scores` only, [`04`](04-data-model.md)
§2.4), `source='operator'` when `is_operator`, else `'user'` with
`author=profile id`. The action first re-reads the trace row with the session's
`org_id` bound (C7); a foreign id is a 404 before any insert.

### (c) Implicit signals — *later, design only*

A scheduled ClickHouse query (hourly, on `infrx-obs`) writing `source='derived'`:

- `derived.regenerate`: same `session_id`, same `prompt_hash`, second request
  within 120 s of the first → `true` on the **first** trace.
- `derived.abandon`: last trace of a session (no request in the following
  30 min) whose output was `client_aborted` or had `thumb=false`.

Both are cheap window-function queries over `traces`; neither is built in v1
because `session_id` is optional client context and its coverage is unknown.

## 3. `judge.py`

Runs on `infrx-obs` ([`03`](03-architecture.md) §6), Python 3.12, two systemd
timers: `judge-submit` (every `JUDGE_RUN_INTERVAL_MIN`) and `judge-collect`
(every 10 min). Dependencies: `anthropic` (official SDK), `boto3` (S3 GET/PUT,
not on any hot path), `httpx` for ClickHouse over HTTP with `FORMAT
JSONEachRow` — the same client shape the gateway shipper uses
([`05`](05-gateway-capture-spec.md)), so there is one ClickHouse idiom in the
repo, not two; `clickhouse-connect` is skipped until a query needs it. `ffmpeg`
binary for frames. Supabase via httpx with the service-role key, as the
gateway does.

### 3.1 Selection (J1, J5, J6)

Step 1 — **eligibility from Supabase** (the opt-ins live there, not in ClickHouse):

```
orgs = GET /organizations?judge_egress_ok=eq.true&judge_daily_budget_usd=gt.0
       &select=id,judge_sample_pct,judge_daily_budget_usd,judge_rubric,judge_rubric_version,judge_frames_k,media_copy
keys = GET /api_keys?judge_enabled=eq.true&revoked_at=is.null&org_id=in.(…)&select=id,org_id
```

Step 2 — **candidates from ClickHouse**, per org, one query:

```sql
WITH judged AS (
  SELECT trace_id FROM infrx.judge_items
  WHERE org_id = {org:UUID} AND rubric_version = {rv:UInt16}
    AND status IN ('succeeded', 'submitted')),
user_scored AS (
  SELECT DISTINCT trace_id FROM infrx.scores
  WHERE org_id = {org:UUID} AND source = 'user' AND ts >= now() - INTERVAL {lookback:UInt8} DAY)
SELECT t.id, t.ts, t.api_key_id, t.content_ref, t.media_sha256, t.media_s3_key, t.status,
       multiIf(has(t.finish_reasons, 'length'), 'always',
               t.status >= 500,                  'always',
               t.schema_valid = false,           'always',
               t.id IN user_scored,              'feedback',
               cityHash64(t.id) % 10000 < toUInt64({pct:Float32} * 100), 'sample',
               '') AS reason
FROM infrx.traces t FINAL
LEFT ANTI JOIN judged j ON j.trace_id = t.id
WHERE t.org_id = {org:UUID}
  AND t.trace_level = 'full'
  AND t.api_key_id IN ({keys:Array(UUID)})
  AND t.ts >= now() - INTERVAL {lookback:UInt8} DAY
  AND reason != ''
ORDER BY reason = 'always' DESC, reason = 'feedback' DESC, t.ts DESC
```

`cityHash64(id) % 10000 < pct×100` is deterministic: the same trace is in or
out of the sample on every run, so a run that dies mid-way does not re-roll the
dice. ⚠️ `judge_items.rubric_version` is not a column in [`04`](04-data-model.md)
§2.3 — see Open question 1; until added, `judged` filters by `run_id IN (SELECT
id FROM judge_runs WHERE rubric_version = …)`.

Step 3 — **budget cut-off** (J6), in Python:

```
spent_today = SELECT sum(cost_usd_est) FROM judge_runs WHERE org_id=… AND started_at >= today()   -- est, not actual: actual lands hours later
remaining   = org.judge_daily_budget_usd - spent_today
for c in candidates (already ordered always → feedback → sample, newest first):
    if c.status >= 500 or c.content_ref is None:  item(c, status='skipped_no_output'); continue
    est = estimate(K, px, model)                                   # §3.8
    if est > remaining:                            item(c, status='skipped_budget');    continue   # keep scanning: a text-only item is cheaper
    remaining -= est; selected.append(c)
    if len(selected) == JUDGE_BATCH_MAX_ITEMS: break
```

Every candidate gets a `judge_items` row with its `reason` and a `status`, so
"why wasn't this judged" is a query, not a guess. `skipped_*` rows are not in
the `judged` set, so they are reconsidered next run.

### 3.2 Inputs

Content: `GET s3://{bucket}/{content_ref}` → the [`04`](04-data-model.md) §3.1
blob; the question(s) are the text parts of the last `user` message, the
answer is `response.choices[0].content`, the think block is
`reasoning_content`.

Media, first hit wins:

| Source | Key | Available when |
|---|---|---|
| media copy | `media/{org}/{media_sha256}.{ext}` | `org.media_copy` was true at capture |
| transcoded clip | `traces.media_s3_key` | production-api Phase 1 landed |
| none | — | text-only judge: `frames_sent = 0`, `judge.groundedness` written as `NULL` with rationale `"no frames available"`, item `status='succeeded'` but flagged in the console |

Frames — one ffmpeg call per clip, cached at `frames/{org}/{sha}/{k:02d}.jpg`
+ `index.json` (`[{k, t_s}]`), reused across runs and rubric versions:

```
t_k = (k + 0.5) × duration_s / K,  k = 0…K-1                       -- mid-bin, never frame 0 (often black)
ffmpeg -hide_banner -loglevel error -i clip.mp4 \
  -vf "select='eq(n\,0)+…'"  … # simpler and exact:
for each t_k: ffmpeg -ss {t_k} -i clip.mp4 -frames:v 1 -vf "scale='min({PX},iw)':-2" -q:v 4 {k:02d}.jpg
```

Eight `-ss` seeks on a ≤120 s clip is < 1 s of CPU; `scale='min(PX,iw)':-2`
never upscales and keeps even dimensions; `-q:v 4` ≈ JPEG quality 85. If
`index.json` exists with the same `K` and `PX`, skip ffmpeg.

### 3.3 Prompt

**System** (constant per rubric version — cacheable prefix; put the rubric
text first, per-run text after):

> You are grading one answer produced by a video-understanding model. You see K
> frames sampled uniformly from the clip, the user's question, and the model's
> answer. Score only against what is visible in the frames. The frames are a
> sample of the clip: if the answer mentions something plausible that could
> have happened between frames, penalise it gently (−1); if it describes an
> event, object, text or person that contradicts the frames or clearly is not
> there, penalise it harshly (score ≤ 2 on groundedness). Do not reward length.
> Do not identify people. Output only the JSON object described.
>
> RUBRIC (id `{rubric_id}` v`{rubric_version}`): `{org.judge_rubric}`
>
> SCORING: each criterion is an integer 1–5 with a one-sentence rationale that
> cites a frame number or quotes the answer. `overall_pass` is true iff
> relevance ≥ 4 and groundedness ≥ 4 and format ≥ 3 and refusal ≥ 3.

**User content**, in order (images before text, per the vision guidance):

```
[ {text: "Frame 1 of 8 at t=3.7s"}, {image: base64 jpeg}, … × K,
  {text: "QUESTION:\n<user text parts, joined>"},
  {text: "ANSWER:\n<response.choices[0].content>"},
  {text: "Grade the ANSWER against the frames and the rubric. Return the JSON object."} ]
```

The `<think>` block is **excluded by default** (`JUDGE_INCLUDE_THINK=false`):
the customer never saw it, so grading it would grade something other than the
product; and it can carry claims the final answer dropped, which would leak
into groundedness. Include it only for an operator "reasoning audit" rubric.

**Structured output schema** (`output_config.format`, `type: json_schema`):

```json
{"type": "object", "additionalProperties": false,
 "required": ["relevance", "groundedness", "completeness", "format", "refusal", "overall_pass", "notes"],
 "properties": {
   "relevance":    {"$ref": "#/$defs/c"}, "groundedness": {"$ref": "#/$defs/c"},
   "completeness": {"$ref": "#/$defs/c"}, "format":       {"$ref": "#/$defs/c"},
   "refusal":      {"$ref": "#/$defs/c"},
   "overall_pass": {"type": "boolean"},
   "notes":        {"type": "string", "maxLength": 500}},
 "$defs": {"c": {"type": "object", "additionalProperties": false,
                 "required": ["score", "rationale"],
                 "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 5},
                                "rationale": {"type": "string", "maxLength": 300}}}}}
```

⚠️ **TO BE VERIFIED** that `$ref`/`$defs` are accepted by the structured-output
schema validator; if not, inline the five criterion objects (mechanical).

**First-draft rubric for Marlin video captioning / QA** (`rubric_id =
marlin-video-v1`) — ⚠️ **draft until validated on ~50 human-labelled traces (§3.9)**:

> Relevance: 5 = directly answers the question asked (or, for "describe the
> video", describes the main subject, setting and action); 3 = partially
> answers or answers a neighbouring question; 1 = off-topic or generic.
> Groundedness: 5 = every stated object, action, count, colour, on-screen text
> and temporal order is consistent with the frames; 4 = one minor unverifiable
> detail; 2 = one clear contradiction; 1 = mostly invented.
> Completeness: 5 = nothing important visible in the frames is missing for the
> question; 3 = misses a secondary element; 1 = misses the main subject.
> Format: 5 = obeys requested language, length, list/JSON shape; 3 = minor
> deviation; 1 = wrong shape or truncated mid-sentence.
> Refusal: 5 = answered a legitimate question, or correctly declined an
> impossible one (e.g. identity of a person, audio content, events outside the
> clip) with a reason; 3 = hedged unnecessarily; 1 = refused a legitimate
> question or answered one it should have declined.

### 3.4 Batch assembly

```python
from anthropic import Anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

requests = [Request(
    custom_id=str(c.id),                                   # trace id → results are keyed by it
    params=MessageCreateParamsNonStreaming(
        model=JUDGE_MODEL,                                 # "claude-opus-5" default (J7)
        max_tokens=2048,
        thinking={"type": "adaptive"},
        output_config={"effort": JUDGE_EFFORT,             # "medium" default
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content(c)}],
    )) for c in selected]
batch = client.messages.batches.create(requests=requests)
```

⚠️ **TO BE VERIFIED**: `output_config.format` inside batch `params` — it is a
Messages API request parameter and the Batches API accepts "all Messages API
features", but the first run must assert `succeeded` results parse. No
`fallbacks` (rejected on the Batches API). Prompt caching of the shared system
block works inside a batch and is worth ~90 % on the rubric tokens.

Bytes: 8 JPEG frames at ≤1000 px long edge ≈ 8 × 100 KB ≈ 0.8 MB, base64
≈ 1.07 MB per request (`est.`); 256 MB ÷ 1.07 MB ≈ 239 → **`JUDGE_BATCH_MAX_ITEMS = 200`**.
At 640 px (~40 KB/frame) the ceiling is ~590; keep 200 unless measured
otherwise.

Then: `judge_runs` row (`status='submitted'`, `batch_id`, `n_selected`,
`cost_usd_est` = Σ estimates), `judge_items` rows `status='submitted'`.

### 3.5 Result collection (`judge-collect`)

```
for run in judge_runs WHERE status = 'submitted':
    b = client.messages.batches.retrieve(run.batch_id)
    if b.processing_status != "ended": continue
    for r in client.messages.batches.results(run.batch_id):
        item = (run.org_id, UUID(r.custom_id), run.id)
        match r.result.type:
          "succeeded": text = first text block; data = json.loads(text); validate(SCHEMA)
                       → 6 scores rows (5 numeric + overall_pass, rationale per row), item succeeded,
                         input/output tokens from r.result.message.usage
                       on JSONDecodeError/validation → item errored, error = text[:2000]
          "errored":   invalid_request → item errored (never retried: same input, same error)
                       other           → item status 'errored', reason kept, resubmitted once by the next submit run
                                          (selection sees it: not in judged set; a second failure is final)
          "expired":   item expired → resubmit once, same rule
          "canceled":  item errored
    run: n_succeeded/n_errored/n_expired, cost_usd_actual from summed usage × price × 0.5, finished_at, status 'ended'
```

Idempotency: `judge_items` is `ReplacingMergeTree(ts)` ordered by
`(org_id, trace_id, run_id)`; re-processing a batch rewrites the same keys.
`scores` rows carry `judge_run_id`; a re-collect writes duplicates that
latest-wins hides — acceptable, and the collector marks the run `ended` first
so it does not run twice under normal operation.

### 3.6 Egress gate

The predicate, evaluated at selection (§3.1 step 1–2) **and** again
immediately before `batches.create` from fresh Supabase reads:

```python
def may_egress(trace, key, org) -> bool:
    return (trace.trace_level == "full"
            and trace.content_ref is not None
            and key is not None and key.judge_enabled and key.revoked_at is None
            and org.judge_egress_ok and org.judge_daily_budget_usd > 0)
```

A trace failing the re-check between steps is dropped from `selected` with
`judge_items.status='skipped_consent'`. `judge_runs` + `judge_items` are the
audit (J5): every trace id that left, when, to which model, with which rubric.

**Consent text shown to the org owner** (C4 checkbox; stored with `_by`/`_at`):

> **Enable automated quality scoring.** For API keys where you turn on
> *Judge*, a sample of fully-traced requests will be sent to Anthropic's Claude
> API to be scored against your rubric. What leaves: the text of the request
> messages, up to {K} still frames sampled from the video, the model's answer,
> and the rubric. What does not leave: the full video, your API keys, other
> requests. Anthropic's API terms state that API inputs are not used to train
> their models. Sampling and a daily spending cap are set below; you can turn
> this off at any time and nothing further is sent. Scored requests are listed
> under Traces → Judge runs.

### 3.7 Failure handling

| Failure | Detection | Behaviour |
|---|---|---|
| content blob missing in S3 | `NoSuchKey` | item `errored`, error `content_missing`; not retried (the blob will not appear) |
| media missing / not copied | no key resolves | text-only judge (§3.2), `frames_sent=0` |
| ffmpeg non-zero exit | return code | one retry with `-err_detect ignore_err`; then text-only, error noted |
| `batches.create` 400 | `BadRequestError` | run `failed`, items `errored`; alert — it is a code/schema bug, not data |
| `batches.create` 429 / 5xx / connection | typed SDK errors | run `failed` with error; items **not** written as submitted, so the next run re-selects them; backoff via `max_retries` (SDK default 2) |
| `retrieve` unreachable | | run stays `submitted`; collect again in 10 min; after `JUDGE_MAX_TRACE_AGE` (48 h) mark `failed` |
| budget exhausted mid-scan | §3.1 step 3 | `skipped_budget` items; console shows "budget reached at HH:MM" |
| rubric edited between submit and collect | run row pins `rubric_id`/`version` | scores carry the run's version; the new version starts a new series (J4) |
| clock skew between boxes | `now()` in ClickHouse vs Python `today()` | budget window uses ClickHouse `today()` in the sum and Python UTC for `started_at`; both UTC; skew < 1 min is harmless |
| org disables judge while a batch is in flight | re-check happens only at submit | already-submitted items complete (they left before the change); the audit row shows the timing |

### 3.8 Cost model

Prices (first-party API, from the current model table in this session's
Claude API reference): Opus 5 $5 / $25 per MTok, Sonnet 5 $2 / $10, Haiku 4.5
$1 / $5; Batches ×0.5. Image tokens `⌈w/28⌉ × ⌈h/28⌉` [src](https://platform.claude.com/docs/en/build-with-claude/vision).
Video frames are 16:9, so a 1000 px long edge is 1000×563 → 36 × 21 = **756**
tokens; 640 px is 640×360 → 23 × 13 = **299** (square frames would be 1,296 /
529 — the ceiling). Text: system + rubric ≈ 800, question + answer + frame
labels ≈ 500 → **1,300 input**; output ≈ 400 JSON + ⚠️ ≈ 500 adaptive-thinking
tokens (billed as output; unmeasured) → **900 output**. All `est.`, recomputed
with `python3`.

| K | px | image tok | input tok | Opus 5 std | **Opus 5 batch** | Sonnet 5 batch | Haiku 4.5 batch |
|---|---|---:|---:|---:|---:|---:|---:|
| 4 | 640 | 1,196 | 2,496 | $0.0350 | **$0.0175** | $0.0070 | $0.0035 |
| 4 | 1000 | 3,024 | 4,324 | $0.0441 | **$0.0221** | $0.0088 | $0.0044 |
| 8 | 640 | 2,392 | 3,692 | $0.0410 | **$0.0205** | $0.0082 | $0.0041 |
| 8 | 1000 | 6,048 | 7,348 | $0.0592 | **$0.0296** | $0.0118 | $0.0059 |

Monthly at 1M requests/month:

| Config | 1 % judged (10k) | 5 % judged (50k) |
|---|---:|---:|
| Opus 5, K=8, 1000 px | $296 | **$1,480** |
| Opus 5, K=8, 640 px | $205 | $1,025 |
| Opus 5, K=4, 1000 px | $221 | $1,105 |
| Sonnet 5, K=8, 1000 px | $118 | $590 |

Two things move these numbers more than the knobs: (a) after production-api
Phase 1 the only clip we hold is the **≤448 px transcode**, so frames are
448×252 → 16 × 9 = **144 tokens** each, and K=8 costs ≈ $0.021 batch on Opus
(≈ $1,050/mo at 5 %) regardless of `JUDGE_FRAME_PX`; (b) prompt caching of the
system block cuts ~700 of the 1,300 text tokens to 10 % price.

**Recommended defaults**: `JUDGE_MODEL=claude-opus-5`, `JUDGE_EFFORT=medium`,
`K=8`, `PX=1000` (a no-op cap once clips are 448 px), `judge_sample_pct=5` →
≈ **$1,480/month at 1M req/mo** before caching and before the 448 px effect;
≈ $1,050 after Phase 1. The always-set (truncations, schema failures,
feedback-bearing) is on top and is usually < 1 % of traffic. Switch to Sonnet 5
only if §3.9 shows equal agreement with humans — that is a measured decision,
not a default.

**Budget knob semantics** (`judge_daily_budget_usd`): a hard cap on
*estimated* spend per org per UTC day, checked before submission; `0` means
off. The estimate is intentionally ~10 % high (thinking tokens assumed);
`cost_usd_actual` is reconciled at collect time and shown beside the cap.

### 3.9 Judge validity

Scores are not trusted until the rubric is calibrated, per
[`../platform/04-evals-and-ab-testing.md`](../platform/04-evals-and-ab-testing.md)
(judge validity is a measured property, not an assumption). Plan:

1. Operators label **~50 traces** (stratified: 25 uniform, 15 always-set, 10
   feedback-bearing) on the same five criteria via the console's operator
   scoring (§2b, `source='operator'`).
2. Run the judge on the same 50 with each candidate `(model, K, px)`.
3. Agreement per criterion: Spearman ρ on the 1–5 scores, Cohen's κ on
   `overall_pass`, plus pass/fail accuracy against the operator label. Stored
   as a one-row-per-config table in `research/traces/results/` (this tree's
   `results/` convention).
4. **Trusted** = κ ≥ 0.6 on `overall_pass` and ρ ≥ 0.6 on groundedness and
   relevance (⚠️ thresholds are a starting convention, not sourced). Until a
   `(rubric_version, model)` pair is trusted, the console shows its scores with
   a **"calibration pending"** badge and excludes them from the default tiles.

## 4. Configuration (`/etc/infrx-judge.env`)

| Var | Default | Meaning |
|---|---|---|
| `CLICKHOUSE_URL` | `http://127.0.0.1:8123` | HTTP interface, same box |
| `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` | `judge` / SSM | [`04`](04-data-model.md) §2.4 grants |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | SSM | opt-in reads (§3.1 step 1) |
| `TRACES_S3_BUCKET` | `infrx-traces` | content, media, frames |
| `ANTHROPIC_API_KEY` | SSM `/model-inference/anthropic_api_key` | judge only; never on the GPU box |
| `JUDGE_MODEL` | `claude-opus-5` | J7; exact id, no date suffix |
| `JUDGE_EFFORT` | `medium` | `output_config.effort` |
| `JUDGE_FRAMES_K` | `8` | overridden per org by `judge_frames_k` |
| `JUDGE_FRAME_PX` | `1000` | long-edge cap; never upscales |
| `JUDGE_INCLUDE_THINK` | `false` | §3.3 |
| `JUDGE_RUN_INTERVAL_MIN` | `30` | submit timer |
| `JUDGE_BATCH_MAX_ITEMS` | `200` | §3.4 bytes arithmetic |
| `JUDGE_LOOKBACK_DAYS` | `7` | candidates newer than this |
| `JUDGE_MAX_TRACE_AGE_H` | `48` | a `submitted` run older than this is `failed` |
| `JUDGE_PRICE_IN_PER_M` / `JUDGE_PRICE_OUT_PER_M` | `5` / `25` | estimator inputs; change with the model |

## 5. Test plan

Unit tests, no network (`pytest` and plain `python3`, the repo convention),
against a synthetic day of 1,000 traces in fixture JSON and a fake ClickHouse
that answers the two queries from the fixture:

| id | Assertion | Req |
|---|---|---|
| Q1 | Selection on the synthetic day: every `length`/5xx/`schema_valid=false`/user-scored trace is `always`/`feedback`; the sample set is exactly `{id: cityHash64(id) % 10000 < 500}` at 5 %; nothing at `trace_level != 'full'` or on a non-judge key is selected | J1, J5 |
| Q2 | Deterministic sampling: two runs over the same fixture select the same ids; changing `pct` 5 → 6 only adds ids | J1 |
| Q3 | Budget cut-off: with `remaining = $1.00` and est $0.0296 the selected count is 33; `skipped_budget` rows exist for the rest; a text-only item still fits after the first over-budget skip | J6 |
| Q4 | Frame timestamps: K=8 on 120 s → `[7.5, 22.5, …, 112.5]`; K=1 → `[60.0]`; never 0 or `duration` | §3.2 |
| Q5 | Prompt assembly is deterministic and images precede text; `<think>` absent unless `JUDGE_INCLUDE_THINK` | §3.3 |
| Q6 | Schema validation: a valid object → 6 score rows with the right `kind`/columns; a score of 6, a missing criterion, or non-JSON → item `errored`, zero score rows | J3 |
| Q7 | Result state machine: `succeeded`/`errored(invalid_request)`/`errored(other)`/`expired`/`canceled` → the statuses in §3.5; `other` and `expired` are re-selected exactly once | §3.5 |
| Q8 | Egress predicate: the 32-row truth table of (`trace_level`, `content_ref`, `judge_enabled`, `revoked_at`, `judge_egress_ok`) → only the one all-true row passes; a re-check flip yields `skipped_consent` | J5, D8 |
| Q9 | Idempotent re-collect: processing the same recorded batch twice leaves one `judge_items` row per trace (after `FINAL`) and the run `ended` once | §3.5 |
| Q10 | Cost estimator: the four rows of §3.8 to the cent; `cost_usd_est` on the run equals the sum over selected items | J6, NF8 |
| Q11 | `/v1/feedback` contract table of §2a (lives in [`05`](05-gateway-capture-spec.md)'s suite; referenced here) | F1–F3 |
| QI1 | Integration: a recorded `batches.results` fixture (JSONL of 5 results: 3 succeeded, 1 invalid JSON, 1 expired) replayed through collect → 18 score rows, statuses as Q7, `cost_usd_actual` = summed usage × prices × 0.5 | J2, J3 |

## 6. Open questions

1. ⚠️ **`judge_items` needs `rubric_version`** (or the `judged` CTE joins
   through `judge_runs`). The join works; the column is cleaner. *Owner: [`04`](04-data-model.md).*
2. ⚠️ **5xx traces in the always-set (J1)** have no answer to grade. This spec
   records them as `skipped_no_output` so they are counted, not judged. If the
   intent was "judge the error", say so; otherwise J1's wording should say
   "selected and recorded".
3. ⚠️ **`output_config.format` in batch params** and **`$ref` in the schema** —
   both verified on the first real run (§3.4).
4. ⚠️ **Thinking tokens per assessment** are unmeasured; the estimator assumes
   500. Reconcile from `cost_usd_actual` after the first 1,000 items and
   re-pin the estimator.
5. ⚠️ **Frame source before Phase 1** — [`01`](01-requirements.md) OQ 1: without
   `media_copy` the judge is text-only for `url` sources. Recommendation stands:
   `media_copy=true` for judge-enabled orgs, capped at `MAX_VIDEO_MB`.
6. ⚠️ **Validity thresholds** (κ ≥ 0.6, ρ ≥ 0.6) are conventions to be
   revisited after the first calibration set.

---

## Verification log

- 2026-09-20 — SDK shapes (`Request`, `MessageCreateParamsNonStreaming`,
  `batches.create/retrieve/results`, result types `succeeded|errored|canceled|expired`,
  `output_config.format` with `json_schema`, `thinking={"type":"adaptive"}`,
  `cache_control` inside `system` blocks in a batch) taken from the bundled
  Python SDK reference in this session; not executed. Vision limits and the
  `⌈w/28⌉×⌈h/28⌉` formula from the fetched vision page (1000×1000 = 1,296
  confirmed against the page's table). Prices from the session's model table.
  Batch limits (100k requests / 256 MB / 24 h / 50 %) from `batches.md`.
  `cityHash64` and `LEFT ANTI JOIN`/`multiIf` are standard ClickHouse; the
  selection query is **not yet run** — [`08`](08-phases-and-test-plan.md) runs
  it in its judge phase. Cost table recomputed: e.g. K=8/1000: 7,348 × 5e-6 +
  900 × 25e-6 = 0.03674 + 0.0225 = 0.05924, ×0.5 = 0.02962 ✓; 50k × 0.02962 =
  $1,481 ✓. **Conflict with [`03`](03-architecture.md) §7**: it estimates
  ≈ $875/mo at 1M × 5 % using 1,296 tokens/frame, 12k input, 400 output and no
  thinking; this document's $1,480 includes ~500 thinking tokens and 16:9
  frames; without thinking it is ≈ $1,030. `03` §7 should adopt this table.

### Implementation-plan amendment log — 2026-09-20

Documentation reconciliation only: incorporated the unified plan and review corrections above. Historical measurements and previous verification entries remain unchanged; new implementation/live tests are pending.
