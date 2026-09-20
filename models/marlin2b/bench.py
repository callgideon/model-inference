#!/usr/bin/env python3
"""Load test for the Marlin-2B endpoint: closed-loop concurrency (historical mode)
or open-loop Poisson arrivals over a corpus of distinct clips.

    python models/marlin2b/bench.py video.mp4 --concurrency 8 --requests 32
    python models/marlin2b/bench.py video.mp4 -c 1 -n 5 --max-tokens 256        # latency floor
    python models/marlin2b/bench.py --corpus models/marlin2b/corpus/manifest.json \
        --subset fast --rate 2 --requests 120 --seed 7 --target gateway --forms video_b64,text

Writes one summary JSON line to --out (default models/marlin2b/results/bench.jsonl)
and one raw line per attempt to --raw (default <out dir>/raw/<run>.jsonl).

Auth comes from $MARLIN_API_KEY or $INFRX_API_KEY only, sent as a Bearer header;
the value is never printed, logged or written to any output file. Passing a key on
the command line is refused.

Percentiles are suppressed, not guessed, when the accepted-sample count cannot
support them (research/plan/04-verification.md: 32 samples cannot establish a p99).

httpx + stdlib rather than the openai SDK: this client needs response headers
(`Inference-Id`, `Retry-After`, `Server-Timing`), the raw SSE line boundaries for
honest first-token timing, rejected-status bodies, and an in-process fake gateway
(httpx.MockTransport) for tests without a server.
"""
import argparse, asyncio, base64, json, math, mimetypes, os, random, re, statistics, sys, time
from hashlib import sha256

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_ENV = ("MARLIN_API_KEY", "INFRX_API_KEY")
DEFAULT_OUT = os.path.join(HERE, "results", "bench.jsonl")
REJECT_STATUS = {400, 401, 402, 403, 404, 409, 410, 413, 415, 422, 429}
MIN_TAIL = 3            # a reported quantile needs this many samples strictly beyond it
PCTS = (50, 90, 95, 99)
# Upload-flow and handle-reference shapes follow research/plan/01-contracts.md §HTTP
# behaviour. Nothing has implemented them yet: unverifiable until M3/G4 land.
UPLOAD_REF_SCHEME = "upload://"


# ---------------------------------------------------------------- config / auth


def refuse_embedded_key(argv):
    """Keys belong in the environment. Refuse anything that looks like one on argv."""
    for tok in argv:
        low = tok.lower()
        if low.startswith(("sk-", "sk_", "--api-key", "--apikey", "--key=", "--token=")) or \
                re.match(r"^--(api[-_]?key|token|bearer)$", low):
            sys.exit(f"refusing key on the command line; export {KEY_ENV[0]} or {KEY_ENV[1]} instead")


def api_key():
    for name in KEY_ENV:
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


def scrub(text, key):
    """Never let the key reach stdout, the JSONL, the summary or an exception message."""
    text = str(text)
    return text.replace(key, "[redacted]") if key else text


def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    refuse_embedded_key(argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", help="local file or http(s) URL; omit when --corpus is used")
    ap.add_argument("-c", "--concurrency", type=int, default=4)
    ap.add_argument("-n", "--requests", type=int, default=16)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--prompt", default=None, help="defaults to the canonical caption prompt via smoke.py's finder")
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000/v1"))
    ap.add_argument("--weights", default=os.environ.get("WEIGHTS", "/opt/dlami/nvme/marlin2b"))
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--raw", default=None, help="raw per-attempt JSONL (default <out dir>/raw/<run>.jsonl)")
    ap.add_argument("--label", default="")
    ap.add_argument("--mm-kwargs", default=os.environ.get("MM_KWARGS", "auto"),
                    help="JSON, 'auto' (training budget from clip duration) or '' (processor default); "
                         "direct target only, the gateway sets the budget server-side")
    ap.add_argument("--model", default=os.environ.get("MODEL_ID", "marlin2b"))
    ap.add_argument("--target", choices=["direct", "gateway"], default="direct",
                    help="direct vLLM (sends mm_processor_kwargs) or the infrx gateway (does not)")
    ap.add_argument("--corpus", default=None, help="corpus manifest.json")
    ap.add_argument("--subset", choices=["fast", "full"], default="fast")
    ap.add_argument("--forms", default="video_b64",
                    help="comma list of text,video_url,video_b64,upload assigned round-robin")
    ap.add_argument("--media-base-url", default=os.environ.get("MEDIA_BASE_URL", ""),
                    help="public prefix for the video_url form: <prefix>/<clip file>")
    ap.add_argument("--rate", type=float, default=None,
                    help="open-loop arrivals per second (Poisson); ignores completions")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--retries", type=int, default=0, help="retry 429/503 this many times, honouring Retry-After")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--dry-run-transport", default=None,
                    help="import path 'module:callable' returning an httpx transport (tests/dry runs, no network)")
    a = ap.parse_args(argv)
    if not a.video and not a.corpus:
        ap.error("pass a video or --corpus")
    return a


# ---------------------------------------------------------------- prompts / media budget


def smoke_namespace():
    """smoke.py parses argv at import time, so lift its helper block the way this
    script always has. F1 should hoist these helpers into a shared module."""
    src = open(os.path.join(HERE, "smoke.py"), encoding="utf-8").read()
    ns = {}
    exec(src[src.index("def canonical_prompt"): src.index("mode = ")], {"os": os, "re": re, "sys": sys}, ns)
    return ns


def training_budget_kwargs(video_path=None, duration=None, fps=2.0, min_frames=4, max_frames=240,
                           px_per_frame=200704):
    """mm_processor_kwargs reproducing Marlin's training-time video budget (same
    formula as smoke.py; `duration` skips probing when the corpus already knows it)."""
    if duration is None:
        return smoke_namespace()["training_budget_kwargs"](video_path, fps, min_frames, max_frames, px_per_frame)
    frames = int(min(max_frames, max(min_frames, round(duration * fps))))
    frames += frames % 2
    return {"fps": fps, "min_frames": min_frames, "max_frames": max_frames,
            "size": {"shortest_edge": 4096, "longest_edge": frames * px_per_frame}}


def resolve_mm_kwargs(a, duration=None, video=None):
    if a.target == "gateway" or not a.mm_kwargs:
        return None
    if a.mm_kwargs != "auto":
        return json.loads(a.mm_kwargs)
    if duration is not None:
        return training_budget_kwargs(duration=duration)
    if video and not video.startswith(("http://", "https://")):
        return training_budget_kwargs(video)
    return None


# ---------------------------------------------------------------- corpus


def corpus_cache_root(manifest, path):
    env = os.environ.get(manifest.get("cache_root_env", "CORPUS_CACHE"))
    if env:
        return env
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(path))))
    return os.path.join(repo, manifest.get("cache_root_default", ".claude/corpus-cache"))


def load_corpus(path, subset="fast"):
    """Return (clips, prompts_by_id, manifest). Only built clips are usable."""
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    root = corpus_cache_root(manifest, path)
    prompts = {p["id"]: p for p in manifest["prompts"]}
    clips = []
    for c in manifest["clips"]:
        if c["status"] != "built" or subset not in c["subset"]:
            continue
        clips.append({"id": c["id"], "path": os.path.join(root, c["file"]), "file": c["file"],
                      "duration_s": c["derived"]["duration_s"], "width": c["derived"]["width"],
                      "height": c["derived"]["height"], "aspect": c["derived"]["aspect"],
                      "sha256": c["derived"]["sha256"], "prompt_id": c["prompt"],
                      "prompt": prompts[c["prompt"]]["text"], "prompt_kind": prompts[c["prompt"]]["kind"]})
    if not clips:
        sys.exit(f"no built clips in {path} subset {subset}; run corpus/build.py build")
    return clips, prompts, manifest


# ---------------------------------------------------------------- schedule (pure)


def build_schedule(n, clips, forms, prompt=None, rate=None, seed=0, video=None):
    """Deterministic arrival schedule. Pure function of its arguments: same seed ->
    same clips, prompts, forms and arrival times, whatever the server does."""
    rng = random.Random(seed)
    order = list(range(len(clips)))
    rng.shuffle(order)
    out, seen, t = [], set(), 0.0
    for i in range(n):
        form = forms[i % len(forms)]
        clip = clips[order[i % len(order)]] if clips else None
        if rate:
            t += rng.expovariate(rate)
        cold = None
        if clip is not None and form != "text":
            cold = clip["id"] not in seen
            seen.add(clip["id"])
        out.append({"seq": i, "arrival_s": round(t, 6), "form": form, "cold": cold,
                    "clip_id": clip["id"] if clip else (os.path.basename(video) if video else None),
                    "clip": clip, "prompt": (prompt or (clip or {}).get("prompt") or ""),
                    "prompt_kind": (clip or {}).get("prompt_kind") if not prompt else "override",
                    "duration_s": (clip or {}).get("duration_s")})
    return out


# ---------------------------------------------------------------- request bodies


def data_url(path):
    mime = mimetypes.guess_type(path)[0] or "video/mp4"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def messages_for(item, media_ref=None):
    if item["form"] == "text" or media_ref is None:
        return [{"role": "user", "content": item["prompt"]}]
    return [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": media_ref}},
                                         {"type": "text", "text": item["prompt"]}]}]


async def media_ref_for(item, cfg, client, row):
    """The media reference for this attempt, doing the upload handshake when asked."""
    form, clip = item["form"], item["clip"]
    path = clip["path"] if clip else cfg["video"]
    if form == "text":
        return None
    if form == "video_url":
        if clip and cfg["media_base_url"]:
            return cfg["media_base_url"].rstrip("/") + "/" + os.path.basename(clip["file"])
        if path and path.startswith(("http://", "https://")):
            return path
        raise ValueError("video_url form needs --media-base-url or an http(s) video")
    if form == "video_b64":
        return path if path.startswith(("http://", "https://")) else data_url(path)
    if form == "upload":
        t = time.perf_counter()
        handle = await upload(client, cfg, path)
        row["upload_s"] = round(time.perf_counter() - t, 4)
        return UPLOAD_REF_SCHEME + handle
    raise ValueError(f"unknown form {form}")


async def upload(client, cfg, path):
    """POST /v1/uploads -> PUT to the returned constrained destination ->
    POST /v1/uploads/{handle}/complete. Contracts v1 shape; unverified until M3/G4."""
    with open(path, "rb") as f:
        body = f.read()
    digest = sha256(body).hexdigest()
    mime = mimetypes.guess_type(path)[0] or "video/mp4"
    r = await client.post(cfg["base"] + "/uploads", headers=cfg["headers"],
                          json={"purpose": "video", "filename": os.path.basename(path),
                                "bytes": len(body), "sha256": digest, "content_type": mime})
    r.raise_for_status()
    created = r.json()
    dest = created.get("upload") or created
    put = await client.request(dest.get("method", "PUT"), dest["url"], content=body,
                              headers={"content-type": mime, **(dest.get("headers") or {})})
    put.raise_for_status()
    done = await client.post(f"{cfg['base']}/uploads/{created['handle']}/complete", headers=cfg["headers"],
                             json={"sha256": digest, "bytes": len(body)})
    done.raise_for_status()
    return (done.json() or {}).get("handle", created["handle"])


def parse_server_timing(value):
    """`Server-Timing: queue;dur=12.3, prep;dur=400` -> {"queue": 12.3, "prep": 400.0}."""
    out = {}
    for part in (value or "").split(","):
        name, _, rest = part.strip().partition(";")
        m = re.search(r"dur\s*=\s*([0-9.]+)", rest)
        if name and m:
            out[name.strip()] = float(m.group(1))
    return out or None


# ---------------------------------------------------------------- one attempt


async def attempt(client, cfg, item, t0, attempt_no):
    """One HTTP attempt. Every path fills the same row schema, rejections included."""
    row = {"seq": item["seq"], "attempt": attempt_no, "clip_id": item["clip_id"], "form": item["form"],
           "prompt_kind": item["prompt_kind"], "cold": item["cold"], "duration_s": item["duration_s"],
           "scheduled_s": item["arrival_s"], "send_s": None, "first_byte_s": None, "first_token_s": None,
           "last_token_s": None, "end_s": None, "http_status": None, "outcome": None, "error_class": None,
           "error_code": None, "error_message": None, "inference_id": None, "retry_after": None,
           "server_timing": None, "prompt_tokens": None, "completion_tokens": None, "usage_missing": None,
           "content_chars": 0, "upload_s": None}
    now = lambda: round(time.perf_counter() - t0, 6)
    try:
        await _send(client, cfg, item, row, now)
    except Exception as e:                               # transport, file, upload or protocol failure
        row["outcome"] = row["outcome"] or "failed"
        row["error_class"] = row["error_class"] or type(e).__name__
        row["error_message"] = scrub(e, cfg["key"])[:200]
        row["end_s"] = row["end_s"] or now()
    ttft = None if row["first_token_s"] is None or row["send_s"] is None else \
        round(row["first_token_s"] - row["send_s"], 6)
    row["ttft_s"] = ttft
    row["latency_s"] = None if row["end_s"] is None or row["send_s"] is None else \
        round(row["end_s"] - row["send_s"], 6)
    n = row["completion_tokens"] or 0
    row["tpot_s"] = (round((row["last_token_s"] - row["first_token_s"]) / max(n - 1, 1), 6)
                     if n and row["first_token_s"] is not None else None)
    return row


async def _send(client, cfg, item, row, now):
    """Issue the request and fill `row`. Returning early is fine: attempt() finalises."""
    ref = await media_ref_for(item, cfg, client, row)
    payload = {"model": cfg["model"], "messages": messages_for(item, ref), "max_tokens": cfg["max_tokens"],
               "temperature": 0, "stream": True, "stream_options": {"include_usage": True}}
    mm = resolve_mm_kwargs(cfg["args"], duration=item["duration_s"], video=cfg["video"])
    if mm:
        payload["mm_processor_kwargs"] = mm
    row["send_s"] = now()
    async with client.stream("POST", cfg["base"] + "/chat/completions", json=payload,
                             headers=cfg["headers"]) as resp:
        row["first_byte_s"] = now()
        row["http_status"] = resp.status_code
        row["inference_id"] = resp.headers.get("inference-id")
        row["retry_after"] = resp.headers.get("retry-after")
        row["server_timing"] = parse_server_timing(resp.headers.get("server-timing"))
        if resp.status_code != 200:
            body = (await resp.aread()).decode("utf-8", "replace")[:600]
            err = {}
            try:
                err = (json.loads(body) or {}).get("error") or {}
            except ValueError:
                pass
            row["outcome"] = "rejected" if resp.status_code in REJECT_STATUS else "failed"
            row["error_class"] = f"http_{resp.status_code}"
            row["error_code"] = err.get("code") or err.get("type")
            row["error_message"] = scrub(err.get("message") or body[:200], cfg["key"])
            row["end_s"] = now()
            return
        usage = None
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                row["error_class"] = "malformed_sse_chunk"
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            if chunk.get("error"):
                row["outcome"] = "failed"
                row["error_class"] = "stream_error_event"
                row["error_code"] = (chunk["error"] or {}).get("code")
                row["end_s"] = now()
                return
            for choice in chunk.get("choices") or []:
                text = (choice.get("delta") or {}).get("content")
                if text:
                    row["first_token_s"] = row["first_token_s"] or now()
                    row["last_token_s"] = now()
                    row["content_chars"] += len(text)
        row["end_s"] = now()
        row["usage_missing"] = usage is None
        if usage:                                    # authoritative usage only, never chunk counting
            row["prompt_tokens"] = usage.get("prompt_tokens")
            row["completion_tokens"] = usage.get("completion_tokens")
        row["outcome"] = "accepted" if row["first_token_s"] is not None else "failed"
        if row["first_token_s"] is None:
            row["error_class"] = "no_content_delta"


async def run_one(client, cfg, item, t0, rows):
    for k in range(cfg["retries"] + 1):
        row = await attempt(client, cfg, item, t0, k)
        row["retries"] = k
        rows.append(row)
        if row["http_status"] not in (429, 503) or k == cfg["retries"]:
            return row
        wait = float(row["retry_after"] or 1) if str(row["retry_after"] or "1").isdigit() else 1.0
        await asyncio.sleep(min(wait, 30.0))


# ---------------------------------------------------------------- drivers


async def run_open_loop(client, cfg, schedule, rows):
    t0 = time.perf_counter()
    tasks = []
    for item in schedule:
        delay = item["arrival_s"] - (time.perf_counter() - t0)
        if delay > 0:
            await asyncio.sleep(delay)          # arrivals never wait for completions
        tasks.append(asyncio.create_task(run_one(client, cfg, item, t0, rows)))
    await asyncio.gather(*tasks)
    return time.perf_counter() - t0


async def run_closed_loop(client, cfg, schedule, rows):
    queue = asyncio.Queue()
    for item in schedule:
        queue.put_nowait(item)
    t0 = time.perf_counter()

    async def worker():
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await run_one(client, cfg, item, t0, rows)

    await asyncio.gather(*(worker() for _ in range(cfg["concurrency"])))
    return time.perf_counter() - t0


# ---------------------------------------------------------------- summary


def min_samples(q):
    """A quantile needs MIN_TAIL samples beyond it to mean anything: p95 -> 60, p99 -> 300."""
    return max(6, math.ceil(MIN_TAIL * 100 / (100 - q)))   # integer maths: p90 -> 30, not 31


def percentile(values, q):
    """(value, n, reason). Refuses the quantile instead of reporting the maximum."""
    n = len(values)
    if n < min_samples(q):
        return None, n, f"p{q} needs >= {min_samples(q)} samples, have {n}"
    return round(statistics.quantiles(sorted(values), n=100, method="inclusive")[q - 1], 4), n, None


def percentile_block(rows, field, scale=1.0):
    vals = [r[field] * scale for r in rows if r.get(field) is not None]
    block, suppressed = {"samples": len(vals)}, []
    for q in PCTS:
        v, n, why = percentile(vals, q)
        block[f"p{q}"] = v
        if why:
            suppressed.append(f"{field} {why}")
    return block, suppressed


def summarize(rows, wall, cfg):
    finals = {}
    for r in rows:                                        # last attempt per request decides its outcome
        finals[r["seq"]] = r
    finals = list(finals.values())
    accepted = [r for r in finals if r["outcome"] == "accepted"]
    rejected = [r for r in finals if r["outcome"] == "rejected"]
    failed = [r for r in finals if r["outcome"] == "failed"]
    out_tokens = sum(r["completion_tokens"] or 0 for r in accepted)
    suppressed = []
    pct = {}
    for field, scale in (("ttft_s", 1.0), ("latency_s", 1.0), ("tpot_ms", 1000.0)):
        src = field if field != "tpot_ms" else "tpot_s"
        block, sup = percentile_block(accepted, src, scale)
        pct[field] = block
        suppressed += sup
    cold = [r for r in accepted if r["cold"] is True]
    warm = [r for r in accepted if r["cold"] is False]
    cold_ttft, _ = percentile_block(cold, "ttft_s")
    warm_ttft, _ = percentile_block(warm, "ttft_s")
    prompt_tokens = [r["prompt_tokens"] for r in accepted if r["prompt_tokens"] is not None]
    res = {
        "label": cfg["args"].label, "target": cfg["args"].target, "model": cfg["model"],
        "mode": "open-loop" if cfg["args"].rate else "closed-loop",
        "mm_kwargs": cfg["summary_mm_kwargs"], "video": cfg["video_label"],
        "corpus": cfg["corpus_label"], "subset": cfg["args"].subset if cfg["args"].corpus else None,
        "forms": cfg["forms"], "seed": cfg["args"].seed, "rate_per_s": cfg["args"].rate,
        "concurrency": None if cfg["args"].rate else cfg["concurrency"],
        "requests": len(finals), "attempts": len(rows),
        "max_tokens": cfg["max_tokens"],
        "accepted": len(accepted), "rejected": len(rejected), "failed": len(failed),
        "accepted_without_usage": sum(1 for r in accepted if r["usage_missing"]),
        "denominators": {"latency_samples": len(accepted), "rejected_excluded": len(rejected),
                         "failed_excluded": len(failed), "scheduled": len(finals)},
        "status_counts": _counts(finals, "http_status"), "error_classes": _counts(finals, "error_class"),
        "error_codes": _counts(finals, "error_code"),
        "distinct_clips": len({r["clip_id"] for r in finals if r["clip_id"]}),
        "cold_requests": len(cold), "warm_requests": len(warm),
        "cold_ttft_s": cold_ttft, "warm_ttft_s": warm_ttft,
        "prompt_tokens": prompt_tokens[0] if prompt_tokens else None,
        "prompt_tokens_median": round(statistics.median(prompt_tokens), 1) if prompt_tokens else None,
        "wall_s": round(wall, 2),
        "req_per_s": round(len(accepted) / wall, 3) if wall else None,
        "out_tok_per_s": round(out_tokens / wall, 1) if wall else None,
        "percentiles": pct, "suppressed_percentiles": sorted(set(suppressed)),
        "percentile_rule": f"a reported pN needs >= {MIN_TAIL} accepted samples beyond it "
                           f"(p50>=6, p95>=60, p99>=300)",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for legacy, (field, q) in {"ttft_p50": ("ttft_s", 50), "ttft_p95": ("ttft_s", 95),
                               "latency_p50": ("latency_s", 50), "latency_p95": ("latency_s", 95),
                               "tpot_p50_ms": ("tpot_ms", 50), "tpot_p95_ms": ("tpot_ms", 95)}.items():
        res[legacy] = pct[field][f"p{q}"]                 # null when the sample count cannot support it
    return res


def _counts(rows, field):
    out = {}
    for r in rows:
        v = r.get(field)
        if v is not None:
            out[str(v)] = out.get(str(v), 0) + 1
    return out


# ---------------------------------------------------------------- wiring


def load_transport(spec):
    mod, _, fn = spec.partition(":")
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.join(HERE, "tests"))
    obj = __import__(mod, fromlist=["*"])
    return getattr(obj, fn or "transport")()


def make_config(a, clips=None, manifest=None):
    key = api_key()
    if a.target == "gateway" and not key:
        sys.exit(f"--target gateway needs {KEY_ENV[0]} or {KEY_ENV[1]} in the environment")
    headers = {"content-type": "application/json", "accept": "text/event-stream"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    forms = [f.strip() for f in a.forms.split(",") if f.strip()]
    if a.target == "gateway" and a.mm_kwargs and a.mm_kwargs != "":
        print("note: --target gateway does not send mm_processor_kwargs (server-side budget)",
              file=sys.stderr)
    return {"args": a, "key": key, "headers": headers, "base": a.base_url.rstrip("/"),
            "model": a.model, "max_tokens": a.max_tokens, "concurrency": a.concurrency,
            "retries": a.retries, "video": a.video, "forms": forms,
            "media_base_url": a.media_base_url,
            "video_label": (os.path.basename(a.video) if a.video else f"corpus:{a.subset}"),
            "corpus_label": (manifest or {}).get("corpus_version") if a.corpus else None,
            "summary_mm_kwargs": resolve_mm_kwargs(a, video=a.video) if not a.corpus else
                                 ("per-clip auto" if a.mm_kwargs == "auto" and a.target == "direct"
                                  else resolve_mm_kwargs(a))}


def raw_path(a):
    if a.raw:
        return a.raw
    slug = re.sub(r"[^a-z0-9]+", "-", (a.label or "run").lower()).strip("-") or "run"
    return os.path.join(os.path.dirname(a.out) or ".", "raw",
                        f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{slug}.jsonl")


async def execute(a):
    clips, manifest = [], None
    if a.corpus:
        clips, _, manifest = load_corpus(a.corpus, a.subset)
    cfg = make_config(a, clips, manifest)
    prompt = a.prompt
    if not a.corpus and prompt is None:
        prompt = smoke_namespace()["canonical_prompt"](a.weights, "caption")
    schedule = build_schedule(a.requests, clips, cfg["forms"], prompt=prompt, rate=a.rate,
                              seed=a.seed, video=a.video)
    rows = []
    transport = load_transport(a.dry_run_transport) if a.dry_run_transport else None
    async with httpx.AsyncClient(timeout=a.timeout, transport=transport) as client:
        if not a.rate and not a.corpus and not a.no_warmup:
            warm = build_schedule(1, clips, cfg["forms"], prompt=prompt, seed=a.seed, video=a.video)
            await run_one(client, cfg, warm[0], time.perf_counter(), [])   # warm-up, not counted
        wall = await (run_open_loop(client, cfg, schedule, rows) if a.rate
                      else run_closed_loop(client, cfg, schedule, rows))
    return rows, wall, cfg


def main(argv=None):
    a = parse_args(argv)
    rows, wall, cfg = asyncio.run(execute(a))
    res = summarize(rows, wall, cfg)
    raw = raw_path(a)
    os.makedirs(os.path.dirname(raw) or ".", exist_ok=True)
    with open(raw, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    res["raw"] = os.path.relpath(raw, os.path.dirname(a.out) or ".")
    print(json.dumps(res, indent=2))
    if res["suppressed_percentiles"]:
        print("suppressed (sample count too small): " + "; ".join(res["suppressed_percentiles"]),
              file=sys.stderr)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "a", encoding="utf-8") as f:
        f.write(json.dumps(res) + "\n")
    return 0 if res["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
