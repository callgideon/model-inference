# Deep traces — gateway capture spec

Spec date **2026-09-20**, written against `apps/infrx-api/gateway.py` at `main`
`5210c67` (500 lines; line numbers below are that file's). Implements
[`01-requirements.md`](01-requirements.md) T1–T12, F1–F4, NF1, NF2, NF6, NF9,
NF10, O4, NF11 and [`03-architecture.md`](03-architecture.md) §2–§3, writing the
row and blob of [`04-data-model.md`](04-data-model.md). No code here is
implemented; every snippet is a sketch of the diff to make.

Legend: [`../METHODOLOGY.md`](../METHODOLOGY.md#legend).

---

## 1. Where capture lives

### 1.1 Today

Everything a trace needs is already in one function's scope, `chat()`
(`gateway.py:382–499`), or in helpers it calls:

| Fact | Where it exists today | Line |
|---|---|---|
| `Inference-Id` (`rid`), `t0` | `chat()` locals | 395–396 |
| auth row `{id, org_id}` | `authenticate()` → `key` | 96–126, 385 |
| requested model (before the `"marlin2b"` rewrite) | `body["model"]` — **destroyed** at 397 | 397 |
| clip seconds, the bytes (in a temp file), ffprobe | `prepare_video()` | 311–359 |
| `mm_processor_kwargs` | `budget_kwargs(secs)` | 214–218, 415 |
| `stream`, `include_usage` | `chat()` | 416–418 |
| `inflight` at admission | global, read at 391 | 391, 420 |
| raw non-stream choices, before `<think>` strip | `data` at 441, mutated in place at 443–445 | 441–446 |
| raw stream deltas, before strip | `c` at 487, mutated at 494; **not accumulated** | 486–495 |
| final usage (stream) | `u` at 485 | 484–485 |
| TTFT (`first`), wall, status | `log()` closure | 423–433 |
| the one place every path ends | `log(...)` is called on 447, 450 (non-stream), 498 (stream `finally`) | |
| the async fan-out pattern | `enqueue()`/`ingest()`/`spill()` | 171–205 |

**The seam is `log()`.** It is the only function every outcome of an *admitted*
request passes through, it already owns `usage`, `first`, `u`, `status`, and it
runs in a `finally:` on the streaming path. T1 additionally needs the
*pre-admission* failures (401/503 at 386–390, 429 at 391–393, 400 at 406/409)
to produce rows; those return before `log()` exists, so §4.6 adds one early
return helper.

### 1.2 The new module: `apps/infrx-api/traces.py`

One file, imported by `gateway.py`, no FastAPI import at module scope (so it
survives the move into `shared/`):

```
traces.py
  effective_level(key_row, header)          §3
  emit(item)                                §4.6  put_nowait + lazy worker start; the ONLY function the request path calls
  assemble(...)                             §5    in-memory objects -> {"row": ..., "content": ...}
  worker()                                  §6    spool -> batch -> ship
  ship(items)                               §6    S3 blob then ClickHouse row; raises on failure
  park(items)                               §6    traces_failed.jsonl
  drain()                                   §6    called from the lifespan shutdown
  COUNTERS                                  §9
```

`gateway.py` gains ~40 lines: the two auth columns, `X-Request-Id`, the
`requested_model` capture, `deltas`/`sse_chunks`, and `log()` → `finish()`
calling `traces.emit(traces.assemble(...))`. `/v1/feedback` (§7) is a separate
~60-line block in `gateway.py` that also calls `traces.emit`.

### 1.3 After the production-api refactor

[`../production-api/10-implementation-spec.md`](../production-api/10-implementation-spec.md) §1
puts `usage_events` queue + spill in `shared/usage.py` and the vLLM relay
(`<think>` strip, usage extract) in `worker/vllm_client.py`. `traces.py` moves
to `shared/traces.py` as a `git mv`; `assemble()` then takes the `Job`
dataclass (§2 there: `id, org_id, api_key_id, body, media: MediaRef, stream,
enqueued_at, started_at, finished_at, result, error, tokens_delivered`) instead
of `chat()`'s locals — every field it needs is on `Job` or `MediaRef`
(`sha256, duration_s, frames, s3_key, bytes_out, mm_kwargs, hit`). The one
call site becomes the worker loop's "complete" step (§7.3 there), which is also
where the single `usage_events` row is written, so "exactly one trace per job
id" and "exactly one usage row per job id" are the same invariant.

### 1.4 Dependencies (NF10)

| Need | Choice | Why |
|---|---|---|
| ClickHouse insert | `httpx` (already a dependency) — `POST /?query=INSERT … FORMAT JSONEachRow` [src](https://clickhouse.com/docs/interfaces/http) | one HTTP call, no client library |
| S3 put with tags | **`boto3`, imported lazily inside `ship()`**, `put_object` run in the default executor thread | SigV4 + instance-role credential fetch/refresh from IMDS + retries is ~80 lines of stdlib that must be exactly right; boto3 is on the DLAMI system image and is a one-word addition to `install.sh`'s pip line. ⚠️ **TO BE VERIFIED** that `/opt/pytorch/bin/python -c "import boto3"` succeeds on the box; if not, `pip install boto3` there. The request path never imports it (`emit()` is pure Python) |
| gzip, hashlib, json, hmac | stdlib | |

Tests run with none of the env set: `traces.py` must import without boto3
present (`import boto3` is inside `ship()`), and `ship()` is swapped for a
`MockTransport`-backed fake in tests (§11).

## 2. Configuration

All optional; with none set the gateway behaves exactly as today (no trace
rows are shipped; the spool is still written so a box without ClickHouse
still has a durable local record at `metadata` level).

| Env | Default | Meaning |
|---|---|---|
| `TRACE_SPOOL` | `$(dirname USAGE_LOG)/traces.jsonl` | durability append (§6.1); instance store is fine — it is rotated daily |
| `TRACE_FAILED_LOG` | `/var/lib/marlin2b/traces_failed.jsonl` | **EBS root**, not the instance store ([`03`](03-architecture.md) §9 #6); `install.sh` creates the dir `0700 ubuntu` |
| `TRACE_QUEUE_MAX` | `10000` | `asyncio.Queue(maxsize)`; same as `_usage_q` |
| `TRACE_BATCH_ROWS` | `200` | ship when the batch reaches this |
| `TRACE_BATCH_S` | `2` | … or is this old |
| `TRACE_RETRY_DELAYS` | `1,3,9` | like `RETRY_DELAYS` |
| `CLICKHOUSE_URL` | `""` | e.g. `http://10.0.1.20:8123`; empty ⇒ rows are spooled and counted as `parked` (not shipped) |
| `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` | `gateway` / `""` | sent as `X-ClickHouse-User` / `X-ClickHouse-Key` [src](https://clickhouse.com/docs/interfaces/http) |
| `TRACES_S3_BUCKET` | `""` | `infrx-traces`; empty ⇒ `full` items degrade to `metadata` at ship and are counted `content_dropped` |
| `TRACES_S3_PREFIX` | `""` | optional key prefix |
| `AWS_REGION` | `us-east-1` | boto3 |
| `ARTIFACT_ID` | `""` | written by `install.sh` from `serve.sh`'s `IMAGE` digest + weights revision ([`04`](04-data-model.md) §1) |
| `GATEWAY_VERSION` | `git rev-parse --short=12 HEAD` at install, else `"dev"` | |
| `ORG_USER_SALT` | `""` | for `user_hash`; empty ⇒ `user_hash` is null and `X-Infrx-User-Id` is ignored + counted |
| `TRACE_HEADER_ALLOWLIST` | `x-infrx-session-id,x-infrx-trace,user-agent,x-request-id,traceparent` | request headers copied into `content.request.headers` |
| `MEDIA_COPY_DIR` | `$(dirname USAGE_LOG)/media` | where a `media_copy` clip waits for the shipper (§4.3) |

`serve.sh` (`models/marlin2b/serve.sh:36–44`) gains two flags, both off in
vLLM by default [src](https://docs.vllm.ai/en/latest/cli/serve/):

```
  --enable-prompt-tokens-details \     # usage.prompt_tokens_details.{cached_tokens,multimodal_tokens}
  --enable-request-id-headers \        # X-Request-Id echoed on the response
```

The chat completion `id` becomes `chatcmpl-<X-Request-Id>` regardless of the
second flag — `_base_request_id()` reads the header whenever present
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/engine/serving.py);
the flag only adds the response header. Both are additive; `--enable-prompt-tokens-details`
changes the `usage` object's shape (adds a key) for every client, which the
OpenAI SDKs ignore.

## 3. Level resolution

```python
LEVELS = ("off", "metadata", "full")

def effective_level(key_row, header):
    """min(key, header). The header can lower, never raise. Unknown header -> ignored."""
    key = key_row.get("trace_level", "metadata") if key_row else "metadata"   # legacy key: metadata, spool only (T12)
    if key not in LEVELS: key = "metadata"                                     # bad DB value: safe default
    h = (header or "").strip().lower()
    if h and h not in LEVELS:
        COUNTERS["bad_trace_header"] += 1
        h = ""
    return min(key, h or key, key=LEVELS.index)
```

| `api_keys.trace_level` | `X-Infrx-Trace` | effective | writes |
|---|---|---|---|
| `off` | any / none | `off` | `usage_events` only; **no** spool line, no row |
| `metadata` | none / `metadata` / `full` | `metadata` | row, no content |
| `metadata` | `off` | `off` | nothing |
| `full` | none / `full` | `full` | row + content (+ media copy if `org.media_copy`) |
| `full` | `metadata` | `metadata` | row |
| `full` | `off` | `off` | nothing |
| any | `bogus` | as if absent | + `bad_trace_header` counter |
| legacy `GATEWAY_API_KEY` (no row) | any | `metadata` | spool line with `org_id=null`; the worker **never ships** `org_id=null` (T12) |

The resolved level is stored on the row as `trace_level` (only `metadata` or
`full` ever reach ClickHouse).

## 4. Request-path changes

Each step names the insertion point in today's file and sketches the diff.

### 4.1 Auth row carries the flags (T2, NF9)

`gateway.py:110`, the one `select`:

```python
params={"key_hash": f"eq.{h}", "select": "id,org_id,revoked_at,trace_level,judge_enabled,organizations(media_copy,trace_retention_days)"}
```

PostgREST embeds the FK row as `row["organizations"]` — the same pattern
`apps/app/lib/session.ts` uses for `org_members → organizations(name)`.
Cached for `KEY_TTL` = 60 s like everything else on the row, so a console
toggle is live within a minute (C3). `test_gateway_auth.py`'s fake returns
rows without these keys; every reader uses `.get()` with the §3 defaults.

### 4.2 Capture before mutation (T4)

`gateway.py:394–397`:

```python
body = await req.json()
rid = str(uuid.uuid4())
t0 = time.time()
level = traces.effective_level(key, req.headers.get("x-infrx-trace"))
requested_model = body.get("model")                 # NEW: before the rewrite on the next line
body["model"] = "marlin2b"
adm = inflight                                       # NEW: concurrency_at_admission, read before the += at 420
meta = traces.client_metadata(body.pop("metadata", None))   # NEW: validated (§5.4); popped so vLLM never sees it
```

`body.pop("metadata")` is deliberate: vLLM does not know the field and would
reject or ignore it; the OpenAI contract stores it server-side, which is what
we do. ⚠️ **TO BE VERIFIED**: whether vLLM's nightly rejects unknown top-level
fields (it has historically ignored them); popping makes the question moot.

### 4.3 Media: sha256 concurrent with ffprobe (T6, NF1)

`prepare_video()` `gateway.py:346–354`. Today: `f.flush()`, then ffprobe in the
default executor, then (URL case) re-read the file to build the `data:` URL.
Change: hash in the same executor call as the probe so its CPU is hidden behind
ffprobe's, and return a media dict instead of just `secs`:

```python
def probe_and_hash(path):
    """One executor hop: ffprobe (subprocess, ~100 ms+) and sha256 (~10 ms per 5 MB)."""
    h = hashlib.sha256()
    with open(path, "rb") as g:
        for chunk in iter(lambda: g.read(1 << 20), b""):
            h.update(chunk)
    return probe_seconds(path), h.hexdigest(), os.path.getsize(path)

secs, sha, nbytes = await asyncio.get_event_loop().run_in_executor(None, probe_and_hash, f.name)
media = {"sha256": sha, "bytes": nbytes, "mime": mime if data_url is None else url[5:url.index(";")],
         "duration_s": secs, "source": "data" if url.startswith("data:") else "url"}
if copy_dir:                                   # org.media_copy: keep the bytes for the shipper (§6.4)
    os.replace(f.name, os.path.join(copy_dir, sha + ext)); keep = True
```

Sequential, not parallel, inside one thread: ffprobe is a subprocess whose
wall time is I/O + decode; the hash is CPU. Running them as two executor
tasks with `asyncio.gather` would truly overlap them but costs a second
thread from a pool that [`../production-api/09-blueprint.md`](../production-api/09-blueprint.md)
Phase 0 already wants bounded. **Decision: one hop, hash first (it warms the
page cache for ffprobe), measure in H1; switch to `gather` only if H1 shows
the hash on the critical path.** `frames`/`fps` come from `budget_kwargs(secs)`
(`gateway.py:415`): `frames = size.longest_edge / PX_PER_FRAME`, `fps = FPS`.
`prepare_video` returns `(media, data_url, err)`; the two call sites (407,
tests) change accordingly. The error string on the `err` path is also the
`error_message` of the T1 row for a 400.

The `data:` URL string (`gateway.py:354`, up to 85 MB for a 64 MB clip) is
**never** referenced by the trace: `body_for_trace` (§5.1) replaces the part
before `assemble()` looks at it.

### 4.4 `X-Request-Id` upstream (T9)

Both upstream calls, `gateway.py:440` and `:463`:

```python
r = await client.post("/v1/chat/completions", json=body, headers={"X-Request-Id": rid})
async with client.stream("POST", "/v1/chat/completions", json=body, headers={"X-Request-Id": rid}) as r:
```

vLLM's `id` is then `chatcmpl-<rid>` [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/openai/chat_completion/serving.py);
`assemble()` stores it as `request_id_upstream` and asserts the suffix
matches (a mismatch is counted, not fatal — a proxy in between could rewrite
it).

### 4.5 Non-stream and stream capture (T5, T7)

Non-stream, `gateway.py:441–446` — keep references to the raw strings before
the in-place strip (Python strings are immutable; this is a pointer copy):

```python
data = r.json()
raw = [ch.get("message", {}).get("content") for ch in data.get("choices", [])]   # NEW
if r.status_code == 200:
    for ch in data.get("choices", []):
        if ch.get("message", {}).get("content"):
            ch["message"]["content"] = THINK.sub("", ch["message"]["content"], count=1)
    data["model"] = MODEL_ID
first_byte = t_first_byte                       # NEW: captured right after `await client.post` returns (headers in)
finish(r.status_code, None, data.get("usage"), data=data, raw=raw)
```

Stream, `gen()` `gateway.py:456–498`:

```python
deltas, chunks, aborted = [], 0, False              # NEW
try:
    async with client.stream(...) as r:
        t_first_byte = time.time()                  # NEW: response headers arrived
        ...
        async for line in r.aiter_lines():
            ...
            obj = json.loads(payload)
            chunks += 1                             # NEW: every parsed data: frame
            ...
            for ch in obj.get("choices", []):
                c = ch.get("delta", {}).get("content")
                if c:
                    deltas.append(c)                # NEW: raw, before the strip below
                    ...
except (GeneratorExit, asyncio.CancelledError):     # NEW: client went away before [DONE]
    aborted = True
    raise
finally:
    inflight -= 1
    finish(status, first, u, deltas=deltas, chunks=chunks, aborted=aborted, first_byte=t_first_byte)
```

How the abort is detected: Starlette closes the async generator when the
client disconnects, which raises `GeneratorExit` at the suspended `yield`
(or `CancelledError` if cancelled while awaiting `aiter_lines`); the `finally`
runs either way and today's `inflight -= 1` already depends on that. Two
caveats: (a) an abort that lands *after* `[DONE]` was yielded is not an abort
— `finish()` checks `aborted and not done`; (b) `req.is_disconnected()` is
**not** used — it needs an `await` on the request path and races with the
generator's own close. `finish_reasons` on an aborted stream is whatever
arrived (usually `[]`), and `u` is `None`, so the row carries
`completion_tokens = null`, `output_chars = len("".join(deltas))`.

The joined `"".join(deltas)` equals the client's concatenated output *before*
the strip; `assemble()` applies the same `THINK` regex once to split
`reasoning_content` from `content`, which reproduces what the client saw
because the streaming strip (`gateway.py:491–494`) is a prefix operation on
the first non-empty deltas and the regex is anchored at `^`. ⚠️ One edge:
if `<think>` spans the first *two* deltas the streaming strip's `stripped`
flag logic (`gateway.py:493`) may leave a fragment for the client that the
one-shot regex removes; G7 asserts equality and, if it fails on real chunking,
`assemble()` must replay the streaming algorithm chunk by chunk instead. Store
whichever the client got — that is the contract.

### 4.6 `log()` → `finish()`, and every path emits (T1)

`gateway.py:423–433` becomes:

```python
def finish(status, first=None, u=None, **outcome):
    usage.update({...})                                  # unchanged usage.jsonl line + usage_events enqueue
    ...
    if level != "off":
        traces.emit(traces.assemble(level=level, rid=rid, t0=t0, key=key, requested_model=requested_model,
                                    body=body, media=media, mm_kwargs=body.get("mm_processor_kwargs"),
                                    status=status, first=first, u=u, adm=adm, meta=meta,
                                    headers=req.headers, prices=_prices[1], **outcome))
```

Pre-admission returns (`gateway.py:386–393, 406, 409`) do not have `usage`
yet. One helper covers them:

```python
def refuse(status, err_type, message, **kw):
    if key is not None or LEGACY_KEY:                    # 401 with no key at all: nothing to attribute -> no row
        traces.emit(traces.assemble_refusal(rid, t0, key, status, err_type, message, adm=inflight, level=level))
    return JSONResponse({"error": {"message": message, "type": err_type}}, status_code=status, **kw)
```

| Path | Line | Row? | `error_type` |
|---|---|---|---|
| bad/absent key → 401 | 386 | **no** — no org to attribute, and it is attacker-suppliable volume | — |
| Supabase down, unknown key → 503 | 388 | no (same reason) | — |
| capacity → 429 | 391 | yes (`key` known) | `capacity` |
| two videos → 400 | 406 | yes | `invalid_request` |
| media error → 400 | 409 | yes, with `media_source` and the reason class in `error_code` | `media` |
| upstream exception → 502 | 449–452 | yes (`finish(502)`) | `upstream` |
| upstream non-200 (stream) | 465–467 | yes | `upstream`, `error_message` = first 512 bytes of the upstream body |
| 200 | 447 / 498 | yes | null |
| client abort | 498 | yes | `client_abort` |

T1's parity check therefore reads: `count(traces) == count(usage_events) +
count(refusals with a key)`; the two unattributable 4xx/5xx are counted in
`COUNTERS["unattributed_refusals"]` so the equation still closes.

`emit()`:

```python
def emit(item):
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(worker())
    try:
        _q.put_nowait(item)
        COUNTERS["spooled"] += 1          # renamed to "queued" in the log line; "spooled" increments on append
    except asyncio.QueueFull:
        COUNTERS["dropped"] += 1          # count and drop: never disk on the request path
```

That is the whole request-path surface of `traces.py`: `effective_level`,
`client_metadata`, `assemble`, `emit`. None of them `await`.

## 5. `assemble()`

Signature: `assemble(level, rid, t0, key, requested_model, body, media, mm_kwargs,
status, first, u, adm, meta, headers, prices, data=None, raw=None, deltas=None,
chunks=0, aborted=False, first_byte=None, error=None) -> {"kind": "trace", "row": {...}, "content": {...}|None, "media": {...}|None}`.
Pure: no I/O, no clock reads except `time.time()` once for `wall`.

### 5.1 `body_for_trace` and the two hashes

```python
def canon(obj):                       # one canonical form everywhere
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def messages_for_trace(messages, media):
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = [({"type": p["type"], "video_url": {"url": f"infrx://media/sha256:{media['sha256']}", "source": media["source"]}}
                  if p.get("type") in ("video_url", "input_video") and media else p) for p in c]
        out.append({**m, "content": c})
    return out
```

`image_url` parts with `data:` URLs (Marlin allows `image: 4`) are treated the
same way with their own sha256 computed **in the worker** (they are small, ≤ a
few MB; ⚠️ if H1 shows image-heavy requests, move the hash to the executor
like video). Until then `n_image` is counted and the part is replaced by
`infrx://media/sha256:<hex>` at ship time.

| Field | Computation |
|---|---|
| `prompt_hash` | `sha256(canon(messages_for_trace(body["messages"], media)))` |
| `prompt_stack_hash` | `sha256(canon({"system": [m["content"] for m in messages if m["role"]=="system"], "tools": body.get("tools"), "params": params}))` — what a customer changes when they "edit the prompt"; user turns excluded |
| `params` | `canon({k: v for k, v in body.items() if k not in ("messages", "mm_processor_kwargs")})` — `stream_options` included (we set it), `model` is the rewritten name; `requested_model` is separate |

### 5.2 Row, field by field ([`04`](04-data-model.md) §2.1)

| Column | Source |
|---|---|
| `id`, `org_id`, `api_key_id` | `rid`, `key["org_id"]`, `key["id"]` (legacy: `org_id=None`) |
| `ts` | `t0`, ms precision, UTC ISO with `Z` (ClickHouse parses `DateTime64` from ISO strings in `JSONEachRow`) |
| `ingested_at` | **set by the worker at ship**, not here |
| `model_id`, `served_model` | `MODEL_ID`, `"marlin2b"` |
| `artifact_id`, `gateway_version`, `endpoint_role` | env, env, `"main"` |
| `trace_level` | `level` |
| `session_id` | `headers.get("x-infrx-session-id")[:256]` |
| `user_hash` | `sha256(ORG_USER_SALT + org_id + X-Infrx-User-Id)` hex, else null; **also** OpenAI's body `user` field if the header is absent |
| `metadata` | `meta` (§5.4) |
| `request_id_upstream` | `data["id"]` / first chunk's `id` |
| `stream`, `requested_model`, `params`, `temperature`, `top_p`, `max_tokens`, `seed`, `n`, `response_format_type` | body |
| `n_messages`, `n_system`, `n_video`, `n_image`, `n_tools`, `prompt_chars` | counted over `body["messages"]`, `body.get("tools", [])`; `prompt_chars` sums text parts only |
| `prompt_hash`, `prompt_stack_hash` | §5.1 |
| `request_bytes` | `int(headers.get("content-length") or 0)` — the wire size, includes the base64 clip |
| `media_*` | `media` dict; `media_frames`, `media_fps` from `mm_kwargs`; `media_s3_key` null until Phase 1 / media copy; `media_cache_hit` null until Phase 1 |
| `mm_kwargs` | `canon(mm_kwargs)` or `"{}"` |
| `status`, `error_type`, `error_code`, `error_message` | §4.6 table; `error_message` ≤ 512 chars |
| `finish_reasons` | non-stream: `[ch["finish_reason"] for ch in choices]`; stream: collected from chunks where present |
| `n_choices` | `len(choices)` / `body.get("n", 1)` |
| `schema_valid` | §5.5 |
| `client_aborted` | `aborted and not done` |
| `prompt_tokens`, `completion_tokens` | `u` |
| `cached_tokens`, `multimodal_tokens` | `u.get("prompt_tokens_details", {}).get(...)` |
| `think_chars` | `len(raw) - len(stripped)` for choice 0 |
| `output_chars` | `len(stripped content of choice 0)` |
| `t_auth_ms` | `int((t_auth - t0)*1000)` where `t_auth` is read after `authenticate()` returns (`gateway.py:385`) |
| `t_media_ms` | after the video loop (`:413`), null when `n_video == 0` |
| `t_admit_ms` | `= t_media_ms` (or `t_auth_ms` without video) until Phase 2 |
| `t_first_byte_ms` | `first_byte` |
| `ttft_ms`, `wall_ms` | exactly `usage["ttft_s"]*1000`, `usage["wall_s"]*1000` (same numbers as `usage_events`) |
| `tpot_ms` | `(wall - ttft) / (completion_tokens - 1)` when `completion_tokens > 1 and ttft is not None`, else null |
| `sse_chunks` | `chunks` |
| `concurrency_at_admission` | `adm` |
| `cost_usd`, `input_usd_per_m`, `output_usd_per_m` | `cost(pt, ct, prices)` — **the existing function** — and the two price fields from `prices` (`0` when unknown, like `cost()`) |
| `price_table_version` | `prices["updated_at"]`; `get_prices()` (`gateway.py:158`) `select` gains `,updated_at` — the column exists with an `on update` trigger (`0001_init.sql:54,115`) |
| `content_ref`, `content_bytes`, `content_sha256` | **set by the worker** after the gzip is built (§6.3); `assemble()` leaves them null / 0 |
| `redaction_status` | `"raw"` |

⚠️ `cost_usd` today is computed in `ingest()` *after* dequeue (`gateway.py:182`)
because `get_prices()` is async. `assemble()` is sync and uses the cached
`_prices[1]`; if the cache is cold (first request after boot) the row's
`cost_usd` is `0` while `usage_events` gets the real number one hop later. Two
options: (a) accept the drift on the first ≤ 300 s after boot, (b) let the
worker fill `cost_*` at ship like `ingest()` does. **(b)**, for parity with the
billing row; `assemble()` passes `prompt_tokens`/`completion_tokens` and the
worker calls `cost(…, await get_prices())`.

### 5.3 Content object ([`04`](04-data-model.md) §3.1)

Built only when `level == "full"`:

```python
content = {
  "v": 1, "id": rid, "org_id": org_id, "ts": ts_iso,
  "request": {"model": requested_model, "messages": messages_for_trace(...), "params": json.loads(params),
              "tools": body.get("tools"), "headers": {h: headers[h] for h in ALLOWLIST if h in headers},
              "metadata": meta},
  "upstream": {"mm_processor_kwargs": mm_kwargs, "id": upstream_id, "usage": u},
  "response": {"status": status, "choices": choices_for_trace(...), "sse_chunks": chunks,
               "client_aborted": aborted, "error": error},
}
```

`choices_for_trace`: per choice `{index, finish_reason, content (post-strip, what the client got),
reasoning_content (the stripped prefix or null), tool_calls_raw, tool_calls}` where
`tool_calls_raw = [tc["function"]["arguments"] for tc in message.tool_calls]` (strings as emitted) and
`tool_calls = [json.loads(a) or {"_parse_error": str(e)} ...]` — both, because escaping
differences are the bug being hunted ([`../platform/01`](../platform/01-observability-and-tracing.md) §1.2c).
Streaming tool-call deltas are concatenated per `index` before parsing.
Non-200 responses put the upstream body (≤ 64 KB) in `response.error.upstream_body`.

### 5.4 `client_metadata()` (T10)

```python
def client_metadata(m):
    if not isinstance(m, dict) or len(m) > 16: COUNTERS["bad_metadata"] += bool(m); return {}
    out = {}
    for k, v in m.items():
        if not (isinstance(k, str) and isinstance(v, str) and len(k) <= 64 and len(v) <= 512):
            COUNTERS["bad_metadata"] += 1; return {}     # all-or-nothing, like OpenAI's 400 — but we never 400 over telemetry
        out[k] = v
    return out
```

Limits are OpenAI's (16 pairs, 64/512 chars) [src](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/update).
Invalid metadata is dropped and counted, never rejected: tracing must not
change the inference contract.

### 5.5 `schema_valid`

`response_format_type = body.get("response_format", {}).get("type")`. When it is
`json_object` or `json_schema` and `status == 200`: `schema_valid = all(json.loads(content) ok for each choice)`;
for `json_schema` additionally ⚠️ *no schema validation in v1* (would need
`jsonschema`, a new dependency) — `schema_valid` means "parses as JSON";
`schema_id = sha256(canon(response_format))[:16]` is stored inside `params`.
Else `schema_valid = null`.

## 6. Worker

### 6.1 Loop

```python
async def worker():
    batch, deadline = [], None
    while True:
        try:
            item = await asyncio.wait_for(_q.get(), timeout=max(0.05, (deadline or time.time() + BATCH_S) - time.time()))
            append_spool(item)                                  # §6.2, durability point
            if item["row"].get("org_id"): batch.append(item)    # T12: legacy rows stay in the spool
            deadline = deadline or time.time() + BATCH_S
        except asyncio.TimeoutError:
            pass
        if batch and (len(batch) >= BATCH_ROWS or time.time() >= deadline or _q.empty()):
            await ship_with_retry(batch); batch, deadline = [], None
```

Started lazily by `emit()` exactly like `_worker` at `gateway.py:198–200`;
one task, one process (`--workers 1` in the unit).

### 6.2 Spool append

`open(TRACE_SPOOL, "a")` per batch (not per item): one `write()` of the joined
lines, `flush()`, and `os.fsync()` **once per batch**. Per-item fsync would be
~1 ms each on gp3 and buys nothing: the loss window is the batch (≤ 2 s) either
way because the ship happens right after. Spool line = [`04`](04-data-model.md) §4.
`logrotate` (production-api Phase 3) rotates daily, keeps 1, `copytruncate`.

### 6.3 Ship — blob first, then row

```python
async def ship(items):
    loop = asyncio.get_event_loop()
    rows = []
    for it in items:
        if it["content"] is not None:
            blob = gzip.compress(canon(it["content"]).encode())
            key = f"{PREFIX}content/{org}/{ts:%Y/%m}/{id}.json.gz"
            if BUCKET:
                await loop.run_in_executor(None, s3_put, key, blob, "application/json", "gzip", f"ttl={ttl_bucket(retention_days)}")
                it["row"].update(content_ref=key, content_bytes=len(blob), content_sha256=sha256(canon(content)))
            else:
                COUNTERS["content_dropped"] += 1
        if it["media"] and BUCKET:
            await loop.run_in_executor(None, s3_put_file, f"{PREFIX}media/{org}/{sha}{ext}", it["media"]["path"], ...); os.unlink(path)
        it["row"]["ingested_at"] = now_iso(); it["row"]["cost_usd"] = cost(...)   # §5.2 option (b)
        rows.append(it["row"])
    r = await ch.post("/", params={"query": "INSERT INTO infrx.traces FORMAT JSONEachRow"},
                      content="\n".join(json.dumps(x) for x in rows).encode())
    r.raise_for_status()
```

- `s3_put` = `boto3.client("s3").put_object(Bucket, Key, Body, ContentType, ContentEncoding, Tagging="ttl=90")`; the client is created once, lazily; `run_in_executor` because boto3 is synchronous. ⚠️ The default executor is the same pool ffprobe uses; Phase 0 of production-api bounds it — until then, at 100 req/s the shipper's ~1 put/10 ms is a small share.
- `ttl_bucket(d) = min(b for b in (7, 30, 90, 180, 365) if b >= d)`; the bucket lifecycle rules match those five tag values ([`03`](03-architecture.md) §4.2).
- ClickHouse client: `httpx.AsyncClient(base_url=CLICKHOUSE_URL, timeout=httpx.Timeout(30, connect=3), headers={"X-ClickHouse-User": …, "X-ClickHouse-Key": …})`. `Content-Type` irrelevant for the HTTP interface. Batch body ≤ ~1 MB at 200 rows × ~2.5 KB — under any default limit. `async_insert` is not needed: we already batch.
- `scores` items (§7) go to a second `INSERT INTO infrx.scores` in the same `ship()`.
- Order matters: an S3 failure raises before the ClickHouse insert, so a row never references a blob that does not exist; a ClickHouse failure after the puts leaves orphan blobs the `ttl` tag will collect and the retry re-puts idempotently (same key, same bytes).

### 6.4 Retry, park, replay, drain

```python
async def ship_with_retry(batch):
    t = time.time()
    for delay in RETRY_DELAYS + (0,):
        try:
            await ship(batch); COUNTERS["shipped"] += len(batch); OBS("ship_seconds", time.time() - t); return
        except Exception as e:
            if not delay:
                COUNTERS["parked"] += len(batch); park(batch, e); return
            COUNTERS["failed"] += 1; await asyncio.sleep(delay)
```

`park()` appends the raw items (row + content + media path) to
`TRACE_FAILED_LOG` — the same shape as the spool, so `replay_traces.py` is
`ship()` over lines. Media copies referenced by parked items stay in
`MEDIA_COPY_DIR` until replayed (they are on the instance store; ⚠️ a
stop/terminate before replay loses them — accepted; the row and content still
replay).

Drain: `gateway.py` gains a lifespan handler (`@asynccontextmanager` on the
`FastAPI(lifespan=…)`) whose shutdown branch awaits `traces.drain()`: stop
accepting (`emit()` parks directly once `_draining` is set — the only time the
request path touches disk, and only during shutdown), ship the current batch
and everything still in the queue, bounded by `TRACE_DRAIN_S` = 20 (well under
the unit's `TimeoutStopSec`). The same handler drains `_usage_q`, which today
has no drain at all (HANDOFF §5 lists "asyncio.create_task results not
retained" as a known gap; this closes half of it).

## 7. Feedback and export endpoints

### 7.1 `POST /v1/feedback` (F1, F2, F4)

Auth: same `authenticate()`; legacy key → 401 (no org, nothing to attach to).

```jsonc
// request
{"trace_id": "3f0c…", "name": "thumb", "value": true, "comment": "wrong bus colour", "expected": "A red bus …"}
// 201
{"id": "…", "trace_id": "3f0c…", "name": "thumb", "kind": "boolean", "source": "user", "ts": "…"}
```

| Field | Rule | Error |
|---|---|---|
| `trace_id` | UUID string | 400 `invalid_request_error` |
| `name` | `^[a-z][a-z0-9_]{0,63}$` | 400 |
| `value` | `bool` → `boolean`; `int`/`float` (finite) → `numeric`; `str` ≤ 64 → `label`; else | 400 |
| `comment` | str ≤ 4,000 | 400 |
| `expected` | str ≤ 64 KB; when present a second row `name="correction", kind="text", value_text=expected` is written alongside (F1 "expected") | 400 |
| ownership | `SELECT org_id FROM infrx.traces WHERE id = {id:UUID} LIMIT 1` via the ClickHouse `gateway` user; result cached 60 s in a bounded `OrderedDict` like `_keys`; **not found or other org → 404** `not_found_error`; ClickHouse unreachable → **503** `server_error` + `Retry-After: 5` (never a silent 201) | |
| rate | token bucket per `api_key_id`, 60/min, in-memory (`ponytail:` process-local, one box; Phase 2's Redis buckets replace it) | 429 `rate_limit_error` |

The row is `traces.emit({"kind": "score", "row": {...}})` with
`source="user"`, `author=api_key_id`, `ts=now`. The trace need not have been
shipped yet for the *write* — but the ownership check requires it in
ClickHouse; a feedback call within ~2 s of the inference can 404. Documented
in C6 ("wait for the response to finish"); ⚠️ if it bites, fall back to the
`usage_events` row (Supabase, `id → org_id`) as a second ownership source.

### 7.2 `GET /v1/feedback?trace_id=` and `GET /v1/traces` (export)

Minimal, read-only, same auth, same ClickHouse `gateway` user (grant
`SELECT` on `scores` and `traces`, [`04`](04-data-model.md) §2.4 — the user
currently has `SELECT org_id` only; widen to `SELECT` on both tables, still no
content access, which is S3):

- `GET /v1/feedback?trace_id=<uuid>` → `{"data": [score rows]}` for the org.
- `GET /v1/traces?from=<iso>&to=<iso>&key=<uuid>&cursor=<ts,id>&limit=≤1000`
  → `application/x-ndjson`, one **row** per line (no content; content is
  `content_ref` + a presigned GET the console mints — the API does not presign
  in v1). Cursor = `(ts, id)` of the last row; query `ORDER BY ts, id LIMIT`.

## 8. `replay_traces.py`

`apps/infrx-api/deploy/replay_traces.py`, a copy of `replay_usage.py`'s
shape: move `TRACE_FAILED_LOG` aside, read lines, `asyncio.run(traces.ship([item]))`
per line (imports `traces` from the sibling directory, so the ship code is
not duplicated), append what still fails, exit 1 if anything is left.
Idempotent: `ReplacingMergeTree` collapses re-inserted rows; S3 puts are
same-key same-bytes. The systemd timer production-api Phase 3 adds for
`replay_usage.py` runs both.

## 9. Observability of the worker (O4, NF11)

`COUNTERS` is a `collections.Counter`; until `/metrics` exists (production-api
Phase 3) the worker prints one JSON line per minute when anything changed:

```
{"t": "…", "traces": {"queued": 1200, "spooled": 1200, "shipped": 1198, "failed": 1, "parked": 0, "dropped": 0,
 "replayed": 0, "content_dropped": 0, "bad_trace_header": 0, "bad_metadata": 2, "unattributed_refusals": 7,
 "queue_depth": 3, "ship_seconds_p50": 0.041, "ship_seconds_max": 0.9}}
```

Names map 1:1 onto the Phase 3 series: `infrx_traces_{queued,spooled,shipped,failed,parked,dropped,replayed,content_dropped}_total`,
`infrx_traces_queue_depth`, `infrx_traces_ship_seconds` (histogram). Alert
worth paging on later: `parked > 0 for 10 m` or `dropped > 0`.

## 10. Hot-path budget (NF1)

| Added operation | Where | `est.` cost | Note |
|---|---|---|---|
| `effective_level()` | loop | ~1 µs | |
| `body.pop("metadata")` + `client_metadata()` | loop | ~5 µs | ≤ 16 pairs |
| `requested_model`, `adm` | loop | ~0 | |
| sha256 of the clip | probe executor thread | **10–15 ms CPU / 5.5 MB**, hidden behind ffprobe (≥ 100 ms) on a different core | ⚠️ measured by H1; the box has 8 vCPU and ffprobe is single-threaded |
| `headers={"X-Request-Id": rid}` | httpx | ~2 µs | |
| `raw = [...]` (non-stream) | loop | ~1 µs | pointer copies |
| `deltas.append`, `chunks += 1` per SSE frame | loop | ~0.2 µs × ~200 | |
| `assemble()` | loop | **50–200 µs** | dict building + two `canon()` of a ~1 KB messages list + sha256; the content object references existing strings |
| `emit()` | loop | ~1 µs | |
| **request-path total** | | **< 0.3 ms**, plus nothing on TTFT | vs NF1's 1 ms p50 / 3 ms p99 |
| `json.dumps` + spool append + fsync per batch | worker | 100–300 µs / item + 1 ms / batch | same event loop; at 100 req/s ≈ 3 % of one core |
| gzip of a ~5 KB content | worker | ~0.2 ms | |
| boto3 `put_object` | executor thread | 10–40 ms wall, ~1 ms CPU | off-loop |

**Benchmark H1** (`models/marlin2b/bench.py`, read at `main`): the script sends
one clip repeatedly and takes `--base-url`; the multi-clip `--distinct` flag is
production-api Phase 0's B0.1 deliverable, so H1 **depends on B0.1 landing**
(or runs on one clip with vLLM's mm cache accepting the bias, labelled so).
Procedure:

1. Two console keys on one org, `k_off` (`trace_level=off`) and `k_full`
   (`full`, `media_copy=false`). `bench.py` gains `--api-key` (today it hard-codes
   `api_key="none"`, `bench.py:52`) — a one-line change.
2. `BASE_URL=https://marlin2b.callbill.ai/v1`, `-c 8 -n 64`, 32 distinct 1080p
   clips, `--label trace-off` then `--label trace-full`, alternating **three
   rounds each** (A B A B A B) to average out vLLM warm state.
3. Compare in `bench.jsonl`: `ttft_p50`, `ttft_p95`, `latency_p50`, `latency_p95`,
   `tpot_p50_ms`. Pass: `Δlatency_p50 ≤ 1 ms`, `Δlatency_p95 ≤ 3 ms`
   (`bench.py` reports p95 not p99; ⚠️ NF1 says p99 — either add a p99 column
   to `bench.py` or accept p95 as the measured proxy and say so in the results
   note), `Δttft_p50 ≤ 0` within noise (± 1 %).
4. Same run with `n_video=0` text-only requests (`smoke.py` prompt without the
   clip) to isolate the sha256 from the assemble cost.
5. Record all six rows in `models/marlin2b/results/bench.jsonl` and the
   verdict in `research/models/marlin2b/README.md` "Measured".

Also assert during the run: `COUNTERS["dropped"] == 0`, `ship_seconds_p50 < 0.5`.

## 11. Test plan

Unit files run with no env, no network, `python3 file.py` or `pytest`, like
`test_gateway_auth.py`; S3 and ClickHouse are `httpx.MockTransport`s (the
boto3 path is stubbed by replacing `traces.s3_put` with a recorder, the same
way `test_media.py` replaces `gateway.fetch_client`). Integration uses
`test_inflight.py`'s `fake_vllm()` + `TestClient`.

`tests/test_traces.py` (unit) and `tests/test_traces_integration.py`:

| id | Assertion | Req |
|---|---|---|
| G1 | `effective_level` truth table (§3): all 8 rows, plus `bogus` header counted once | T2, T3 |
| G2 | Exactly one `emit()` per request on each path: 200 non-stream, 200 stream, 429, 400 two-videos, 400 media, 502 upstream exception, stream non-200, stream client abort; **zero** for 401 without a key (and `unattributed_refusals == 1`) | T1 |
| G3 | `off` key → `emit` never called; `usage.jsonl` line still written | T2 |
| G4 | Content invariants: `sha256(canon(content.request.messages)) == row.prompt_hash`; after `ship()`, `row.content_bytes == len(gzip)` and `row.content_sha256 == sha256(canon(content))` | T5, NF3 |
| G5 | A 3 MB `data:` clip → `canon(content)` < 8 KB and contains `infrx://media/sha256:` and not `base64,`; `row.media_sha256 == sha256(decoded bytes)`; `media_bytes` matches | T6 |
| G6 | Stream: `content.response.choices[0].content == "".join(client deltas)` for a chunked fake (`<think>` split across two deltas, then text) | T5 |
| G7 | `reasoning_content` equals the stripped prefix and `content` equals what the client received, both stream and non-stream; `think_chars == len(raw) - len(stripped)` | T5 |
| G8 | `X-Request-Id: <rid>` present on the upstream request (both paths); `request_id_upstream` stored | T9 |
| G9 | `client_metadata`: 17 pairs → `{}` + counter; 65-char key → `{}`; non-string value → `{}`; valid 3 pairs → stored; vLLM never receives `metadata` | T10 |
| G10 | `schema_valid`: `json_object` + valid → `true`; invalid → `false`; no `response_format` → `null` | T11 |
| G11 | Legacy key: spool line has `org_id=null`; worker ships nothing (fake ClickHouse sees no POST) | T12 |
| G12 | QueueFull: with `TRACE_QUEUE_MAX=2` and the worker paused, third `emit` increments `dropped`, does not raise, does not open any file | T7, §4.6 |
| G13 | Ship order: fake S3 and fake ClickHouse record call order — every `put_object` precedes the `INSERT`; on S3 failure no `INSERT` happens | §6.3 |
| G14 | Retry then park: ClickHouse fake fails 4× → `failed == 3`, `parked == n`, `traces_failed.jsonl` has the items with content inline | T8, NF2 |
| G15 | Replay idempotency: `replay_traces.py` on the parked file → INSERT with the same `id`s; file empty after; second run is a no-op | T8, NF6 |
| G16 | `ttl_bucket`: 7→7, 8→30, 90→90, 91→180, 365→365; tag string `ttl=90` on the put | §6.3 |
| G17 | `tpot_ms` null when `completion_tokens ≤ 1`; `t_media_ms` null without video; `concurrency_at_admission == inflight before increment` (send 3 concurrent, values 0,1,2) | T4 |
| G18 | Feedback: own trace → 201 + score item emitted with `source=user`; other org's → 404; unknown → 404; ClickHouse down → 503 with `Retry-After`; 61st call in a minute → 429; `expected` → two rows | F1, F2, F4 |
| G19 | Drain: 50 items queued, worker slow, lifespan shutdown → all 50 in the spool and shipped or parked within `TRACE_DRAIN_S`; `emit` after `_draining` parks | §6.4, NF2 |
| G20 | `assemble()` never awaits and never opens a file: run it under a patched `open`/`asyncio.sleep` that raises | T7, NF10 |
| G21 | Integration (fake vLLM, `TestClient`): a streamed video request produces one spool line whose row has every T4 column non-null, `finish_reasons == ["stop"]`, `sse_chunks == fake's frame count`, `cached_tokens`/`multimodal_tokens` from the fake's `prompt_tokens_details` | T4 |
| G22 | `prompt_stack_hash` unchanged when only the user turn changes; changed when the system prompt changes | §5.1 |
| H1 | The §10 benchmark; pass per NF1 | NF1 |
| H2 | On the box: stop ClickHouse 10 min under `bench.py` load, restart, run replay → `count(traces) == count(usage_events)` for the window | NF2 |
| H3 | `systemctl restart marlin2b-gateway` under load → no duplicate `id` in `traces FINAL`, no gap vs `usage_events` beyond the ms window | NF2, NF6 |

Existing tests that change: `test_media.py` call sites of `prepare_video`
(return arity), `test_gateway_auth.py`'s `select` string assertion if any (it
asserts on `key_hash=eq.` only — unaffected).

## 12. Open questions

1. ⚠️ **boto3 availability in `/opt/pytorch`** (§1.4). One command to answer.
2. ⚠️ **`multimodal_tokens` for video on the pinned nightly** ([`01`](01-requirements.md) OQ 2); G21's fake asserts the plumbing, not vLLM.
3. ⚠️ **Streaming `<think>` split edge** (§4.5): G6/G7 decide whether `assemble()` replays the chunked strip.
4. ⚠️ **Feedback-before-ship 404** (§7.1): measure how often the first feedback arrives < 2 s after the completion; add the `usage_events` fallback if > 1 %.
5. ⚠️ **`bench.py` p99** (§10): add the column or accept p95.
6. ⚠️ **Unknown top-level fields at vLLM** (§4.2): `metadata` is popped, so only matters if other OpenAI fields (`store`, `user`) start being sent — `user` is read (§5.2) and left in place; ⚠️ verify vLLM accepts it (it is in the OpenAI schema vLLM mirrors).
7. `prompt_stack_hash` excludes user turns by role; a customer who puts instructions in the first user turn gets a hash that changes per request — document in C6 rather than guess.

---

## Verification log

- 2026-09-20 — every `gateway.py` line number above was read from `main` `5210c67` in this session (auth select 110, prices select 158, `enqueue` 197–205, `prepare_video` 311–359 with ffprobe at 348 and the `data:` rebuild at 352–354, `chat()` 382–499: auth 385, capacity 391, body 394, model rewrite 397, video loop 400–413, budget 415, `log()` 423–433, upstream calls 440/463, strip 443–445/491–494, `finally` 453–454/496–498). `models.updated_at` + trigger confirmed at `0001_init.sql:54,115`. `bench.py` confirmed single-video, `api_key="none"` at line 52, percentiles p50/p95 only. vLLM `_base_request_id()` reads `X-Request-Id`; `chatcmpl-` prefix and `_make_prompt_tokens_details(enable_prompt_tokens_details, num_cached_tokens, num_cache_creation_tokens, mm_token_counts)` read from the cited source files; flag help text from the vLLM CLI reference. ClickHouse HTTP insert/auth forms from the HTTP-interface doc. Hot-path numbers in §10 are `est.` until H1.
