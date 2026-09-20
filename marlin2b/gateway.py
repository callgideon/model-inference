#!/usr/bin/env python3
"""OpenAI-compatible gateway in front of vLLM for Marlin-2B.

What it adds on top of vLLM's own server (which stays bound to localhost):
  * bearer API-key check (key from $GATEWAY_API_KEY)
  * per-request video budget: reads the clip's duration and sets vLLM's
    mm_processor_kwargs so the model sees its training grid (2 fps, 200,704 px
    per frame) instead of the processor default that costs 6x the tokens
    (see results/notes.md); rejects clips over MAX_VIDEO_SECONDS / MAX_VIDEO_MB
  * `Inference-Id` response header + one JSON line per request in $USAGE_LOG
    (id, tokens, video seconds, TTFT, status) for billing
  * early 429 above MAX_INFLIGHT instead of queueing
  * strips the leading `<think>` token Marlin emits (non-streaming and streaming)

Run:  GATEWAY_API_KEY=... uvicorn gateway:app --host 127.0.0.1 --port 8001
Then put TLS in front (Caddyfile) and expose only 443.
"""
import asyncio, base64, json, os, re, subprocess, tempfile, time, uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:8000")
API_KEY = os.environ.get("GATEWAY_API_KEY", "")
MODEL_ID = os.environ.get("MODEL_ID", "nemostation/marlin-2b")
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "16"))
MAX_VIDEO_SECONDS = float(os.environ.get("MAX_VIDEO_SECONDS", "120"))
MAX_VIDEO_MB = float(os.environ.get("MAX_VIDEO_MB", "64"))
USAGE_LOG = os.environ.get("USAGE_LOG", "/opt/dlami/nvme/logs/usage.jsonl")
MODELS_DOC = os.environ.get("MODELS_DOC", os.path.join(os.path.dirname(__file__), "openrouter", "provider-models.json"))
FPS, MIN_FRAMES, MAX_FRAMES, PX_PER_FRAME = 2.0, 4, 240, 200704

app = FastAPI()
client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(600, connect=10))
inflight = 0
THINK = re.compile(r"^\s*<think>(?:.*?</think>)?\s*", re.S)


def unauthorized(req):
    return API_KEY and req.headers.get("authorization", "") != f"Bearer {API_KEY}"


def probe_seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    return float(out)


def budget_kwargs(seconds):
    frames = int(min(MAX_FRAMES, max(MIN_FRAMES, round(seconds * FPS))))
    frames += frames % 2
    return {"fps": FPS, "min_frames": MIN_FRAMES, "max_frames": MAX_FRAMES,
            "size": {"shortest_edge": 4096, "longest_edge": frames * PX_PER_FRAME}}


async def video_seconds(part):
    """Duration of the request's video (data URL or http URL). Downloads URLs so
    vLLM's own fetch is not the only one that sees them; returns (seconds, error)."""
    url = part.get("video_url", {}).get("url", "") if isinstance(part.get("video_url"), dict) else part.get("video_url", "")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        try:
            if url.startswith("data:"):
                data = base64.b64decode(url.split(",", 1)[1])
            elif url.startswith(("http://", "https://")):
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    data = r.content
            else:
                return None, "video_url must be an http(s) URL or a data: URL"
            if len(data) > MAX_VIDEO_MB * 2**20:
                return None, f"video larger than {MAX_VIDEO_MB} MB"
            f.write(data)
            f.flush()
            secs = await asyncio.get_event_loop().run_in_executor(None, probe_seconds, f.name)
        except Exception as e:
            return None, f"could not read video: {type(e).__name__}: {e}"
        finally:
            os.unlink(f.name)
    if secs > MAX_VIDEO_SECONDS:
        return None, f"video is {secs:.0f}s; max is {MAX_VIDEO_SECONDS:.0f}s"
    return secs, None


@app.get("/health")
async def health():
    try:
        r = await client.get("/health", timeout=5)
        return JSONResponse({"ok": r.status_code == 200, "inflight": inflight}, status_code=200 if r.status_code == 200 else 503)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.get("/v1/models")
async def models(req: Request):
    doc = json.load(open(MODELS_DOC))
    # OpenAI-shaped list for ordinary clients; OpenRouter's provider document fields ride along.
    for m in doc["data"]:
        m.setdefault("object", "model")
        m.setdefault("owned_by", "nemostation")
    return doc


@app.post("/v1/chat/completions")
async def chat(req: Request):
    global inflight
    if unauthorized(req):
        return JSONResponse({"error": {"message": "invalid api key", "type": "authentication_error"}}, status_code=401)
    if inflight >= MAX_INFLIGHT:
        return JSONResponse({"error": {"message": "at capacity, retry", "type": "rate_limit_error"}}, status_code=429,
                            headers={"Retry-After": "2"})
    body = await req.json()
    rid = str(uuid.uuid4())
    t0 = time.time()
    body["model"] = "marlin2b"  # vLLM's served name
    secs = 0.0
    n_video = 0
    for msg in body.get("messages", []):
        if isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if part.get("type") in ("video_url", "input_video"):
                    n_video += 1
                    if n_video > 1:
                        return JSONResponse({"error": {"message": "one video per request", "type": "invalid_request_error"}}, status_code=400)
                    secs, err = await video_seconds(part)
                    if err:
                        return JSONResponse({"error": {"message": err, "type": "invalid_request_error"}}, status_code=400)
    if n_video:
        body["mm_processor_kwargs"] = budget_kwargs(secs)
    stream = bool(body.get("stream"))
    if stream:
        body.setdefault("stream_options", {})["include_usage"] = True

    inflight += 1
    usage = {"id": rid, "ts": t0, "video_seconds": secs, "stream": stream}

    def log(status, first=None, u=None):
        usage.update({"status": status, "ttft_s": round((first or time.time()) - t0, 3), "wall_s": round(time.time() - t0, 3),
                      "prompt_tokens": (u or {}).get("prompt_tokens"), "completion_tokens": (u or {}).get("completion_tokens")})
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps(usage) + "\n")

    headers = {"Inference-Id": rid}
    try:
        if not stream:
            r = await client.post("/v1/chat/completions", json=body)
            inflight -= 1
            data = r.json()
            if r.status_code == 200:
                for ch in data.get("choices", []):
                    if ch.get("message", {}).get("content"):
                        ch["message"]["content"] = THINK.sub("", ch["message"]["content"], count=1)
                data["model"] = MODEL_ID
            log(r.status_code, None, data.get("usage"))
            return JSONResponse(data, status_code=r.status_code, headers=headers)

        async def gen():
            global inflight
            first = None
            u = None
            status = 200
            stripped = False
            try:
                async with client.stream("POST", "/v1/chat/completions", json=body) as r:
                    status = r.status_code
                    if status != 200:
                        yield (await r.aread())
                        return
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            if line == "":
                                continue
                            yield line + "\n\n"
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            yield "data: [DONE]\n\n"
                            continue
                        try:
                            obj = json.loads(payload)
                        except Exception:
                            yield line + "\n\n"
                            continue
                        obj["model"] = MODEL_ID
                        if obj.get("usage"):
                            u = obj["usage"]
                        for ch in obj.get("choices", []):
                            c = ch.get("delta", {}).get("content")
                            if c:
                                if first is None:
                                    first = time.time()
                                if not stripped:
                                    c2 = THINK.sub("", c, count=1)
                                    stripped = not c.lstrip().startswith("<think>") or c2 != c
                                    ch["delta"]["content"] = c2
                        yield "data: " + json.dumps(obj) + "\n\n"
            finally:
                inflight -= 1
                log(status, first, u)

        return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
    except Exception as e:
        inflight -= 1
        log(502)
        return JSONResponse({"error": {"message": f"upstream error: {e}", "type": "server_error"}}, status_code=502, headers=headers)
