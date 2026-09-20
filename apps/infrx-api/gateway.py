#!/usr/bin/env python3
"""OpenAI-compatible gateway in front of vLLM for Marlin-2B.

What it adds on top of vLLM's own server (which stays bound to localhost):
  * bearer API-key check against Supabase `api_keys` (sha256 hex of the key),
    cached 60 s (10 s for misses); revoked keys get 401, and if Supabase is
    unreachable cached keys keep working while unknown keys get 503, not 401.
    $GATEWAY_API_KEY still works as a legacy single key when set.
  * per-request video budget: reads the clip's duration and sets vLLM's
    mm_processor_kwargs so the model sees its training grid (2 fps, 200,704 px
    per frame) instead of the processor default that costs 6x the tokens
    (see results/notes.md); rejects clips over MAX_VIDEO_SECONDS / MAX_VIDEO_MB
  * the media fetch, once, safely (see README "Security"). A caller-supplied
    `video_url` is fetched here and handed to vLLM inline as a base64 `data:`
    URL, so the engine never fetches from the internet and the bytes cross the
    wire once. The fetch itself is SSRF-hardened: http(s) only, every hostname
    resolved and every resolved address checked against loopback / private /
    link-local (169.254.0.0/16, the EC2 metadata endpoint) / multicast /
    reserved, redirects followed by hand (MAX_REDIRECTS hops, re-validated
    every hop), a total FETCH_TIMEOUT_S budget, a streaming byte counter that
    aborts at MAX_VIDEO_MB, and an ALLOWED_VIDEO_MIME content-type/extension
    allowlist. Upstream exception text never reaches the caller: it goes to
    the journal and the client gets a generic 400 plus a reason class
    (dns, blocked-address, too-large, timeout, unsupported-type, http-<status>).
  * `Inference-Id` response header + one JSON line per request in $USAGE_LOG
    (id, tokens, video seconds, TTFT, status), plus one `usage_events` row in
    Supabase per authenticated request, posted from a background queue with
    retries; rows that never land go to usage_failed.jsonl for deploy/replay_usage.py.
    cost_usd uses the `models` prices, re-read every 5 minutes.
  * early 429 above MAX_INFLIGHT instead of queueing
  * strips the leading `<think>` token Marlin emits (non-streaming and streaming)

Env (see deploy/install.sh, which writes /etc/marlin2b-gateway.env):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, MODEL_ID, GATEWAY_API_KEY (legacy,
  optional), UPSTREAM, MAX_INFLIGHT, MAX_VIDEO_SECONDS, MAX_VIDEO_MB,
  FETCH_TIMEOUT_S, MAX_REDIRECTS, ALLOWED_VIDEO_MIME,
  USAGE_LOG, USAGE_FAILED_LOG, MODELS_DOC. With none of them set the gateway
  imports and runs unauthenticated, which is what the tests use.

Run:  uvicorn gateway:app --host 127.0.0.1 --port 8001
Then put TLS in front (Caddyfile) and expose only 443.
"""
import asyncio, base64, hashlib, ipaddress, json, os, re, socket, subprocess, tempfile, time, uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:8000")
LEGACY_KEY = os.environ.get("GATEWAY_API_KEY", "")
MODEL_ID = os.environ.get("MODEL_ID", "nemostation/marlin-2b")
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "16"))
MAX_VIDEO_SECONDS = float(os.environ.get("MAX_VIDEO_SECONDS", "120"))
MAX_VIDEO_MB = float(os.environ.get("MAX_VIDEO_MB", "64"))
FETCH_TIMEOUT_S = float(os.environ.get("FETCH_TIMEOUT_S", "30"))
MAX_REDIRECTS = int(os.environ.get("MAX_REDIRECTS", "3"))
ALLOWED_VIDEO_MIME = {m.strip().lower() for m in os.environ.get(
    "ALLOWED_VIDEO_MIME", "video/mp4,video/webm,video/quicktime,video/mpeg").split(",") if m.strip()}
EXT_MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
            ".mov": "video/quicktime", ".mpeg": "video/mpeg", ".mpg": "video/mpeg"}
USAGE_LOG = os.environ.get("USAGE_LOG", "/opt/dlami/nvme/logs/usage.jsonl")
USAGE_FAILED_LOG = os.environ.get("USAGE_FAILED_LOG", os.path.join(os.path.dirname(USAGE_LOG), "usage_failed.jsonl"))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
MODELS_DOC = os.environ.get("MODELS_DOC", os.path.join(os.path.dirname(__file__), "openrouter", "provider-models.json"))
FPS, MIN_FRAMES, MAX_FRAMES, PX_PER_FRAME = 2.0, 4, 240, 200704
KEY_TTL, MISS_TTL, PRICE_TTL, LAST_USED_TTL = 60, 10, 300, 60
RETRY_DELAYS = (1, 3, 9, 0)  # usage_events insert backoff; 0 = give up and spill to disk

app = FastAPI()
client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(600, connect=10))
sb = httpx.AsyncClient(base_url=f"{SUPABASE_URL}/rest/v1", timeout=httpx.Timeout(5, connect=2),
                       headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                                "Content-Type": "application/json"})
inflight = 0
THINK = re.compile(r"^\s*<think>(?:.*?</think>)?\s*", re.S)

# ---- auth: sha256(key) -> api_keys row, cached ------------------------------
_keys = {}        # key_hash -> (expires_at, row or None)
_last_used = {}   # key_hash -> ts of the last last_used_at PATCH


async def authenticate(req):
    """Returns (api_keys row, error status). The row is None both for the legacy
    key and when nothing is configured; the status is None when the call is allowed."""
    token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if LEGACY_KEY and token == LEGACY_KEY:
        return None, None
    if not SUPABASE_URL:
        return None, 401 if LEGACY_KEY else None
    if not token:
        return None, 401
    h = hashlib.sha256(token.encode()).hexdigest()
    hit = _keys.get(h)
    if hit is None or hit[0] < time.time():
        try:
            r = await sb.get("/api_keys", params={"key_hash": f"eq.{h}", "select": "id,org_id,revoked_at"})
            r.raise_for_status()
            rows = r.json()
            hit = _keys[h] = (time.time() + (KEY_TTL if rows else MISS_TTL), rows[0] if rows else None)
        except Exception as e:
            if hit is None:  # never seen this key and Supabase is down: fail closed, but retryable
                print(f"gateway: api_keys lookup failed ({type(e).__name__}: {e})", flush=True)
                return None, 503
    row = hit[1]
    if row is None or row.get("revoked_at"):
        return None, 401
    if time.time() - _last_used.get(h, 0) > LAST_USED_TTL:
        _last_used[h] = time.time()
        asyncio.create_task(touch(h))
    return row, None


async def touch(h):
    """Fire-and-forget api_keys.last_used_at update (at most once a minute per key)."""
    try:
        await sb.patch("/api_keys", params={"key_hash": f"eq.{h}"},
                       json={"last_used_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                       headers={"Prefer": "return=minimal"})
    except Exception as e:
        print(f"gateway: last_used_at update failed ({type(e).__name__}: {e})", flush=True)


# ---- usage: bounded background queue -> usage_events ------------------------
_usage_q = asyncio.Queue(maxsize=10000)
_worker = None
_prices = (0.0, None)  # (expires_at, {input_usd_per_m, output_usd_per_m} or None)


def cost(prompt_tokens, completion_tokens, prices):
    """0 when prices are unknown or unusable: never drop a usage row over a price."""
    try:
        return round((prompt_tokens or 0) * float(prices["input_usd_per_m"]) / 1e6
                     + (completion_tokens or 0) * float(prices["output_usd_per_m"]) / 1e6, 8)
    except Exception:
        return 0.0


async def get_prices():
    global _prices
    if _prices[0] < time.time():
        try:
            r = await sb.get("/models", params={"id": f"eq.{MODEL_ID}", "select": "input_usd_per_m,output_usd_per_m"})
            r.raise_for_status()
            rows = r.json()
            if not rows:
                print(f"gateway: no prices for {MODEL_ID}; cost_usd=0", flush=True)
            _prices = (time.time() + PRICE_TTL, rows[0] if rows else None)
        except Exception as e:
            _prices = (time.time() + PRICE_TTL, _prices[1])  # keep the last known prices
            if _prices[1] is None:
                print(f"gateway: price fetch failed ({type(e).__name__}: {e}); cost_usd=0", flush=True)
    return _prices[1]


def spill(row):
    try:
        with open(USAGE_FAILED_LOG, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        print(f"gateway: cannot write {USAGE_FAILED_LOG} ({e}); lost {row.get('id')}", flush=True)


async def ingest():
    while True:
        row = await _usage_q.get()
        row["cost_usd"] = cost(row["prompt_tokens"], row["completion_tokens"], await get_prices())
        for delay in RETRY_DELAYS:  # three retries, then spill to disk
            try:
                r = await sb.post("/usage_events", json=row, headers={"Prefer": "return=minimal"})
                if r.status_code < 300 or r.status_code == 409:  # 409 = already inserted
                    break
                raise RuntimeError(f"{r.status_code} {r.text[:200]}")
            except Exception as e:
                if not delay:
                    print(f"gateway: usage_events insert failed ({e}) -> {USAGE_FAILED_LOG}", flush=True)
                    spill(row)
                    break
                await asyncio.sleep(delay)


def enqueue(row):
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(ingest())
    try:
        _usage_q.put_nowait(row)
    except asyncio.QueueFull:
        print("gateway: usage queue full", flush=True)
        spill(row)


def probe_seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    return float(out)


def budget_kwargs(seconds):
    frames = int(min(MAX_FRAMES, max(MIN_FRAMES, round(seconds * FPS))))
    frames += frames % 2
    return {"fps": FPS, "min_frames": MIN_FRAMES, "max_frames": MAX_FRAMES,
            "size": {"shortest_edge": 4096, "longest_edge": frames * PX_PER_FRAME}}


# ---- media fetch: SSRF-hardened, once, then inline to vLLM ------------------
def address_allowed(ip):
    """False for anything not a routable public address: loopback, private,
    link-local (169.254.0.0/16 and fe80::/10, so the EC2 metadata endpoint),
    CGNAT, multicast, reserved, unspecified — v4 and v6, and v4-mapped v6."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified:
        return False
    return a.is_global  # also drops 100.64.0.0/10 and the v6 special-purpose ranges


async def resolve_public(host):
    """All A/AAAA records for host, or an error class. Every record must pass:
    one private answer among many is a rebinding attempt, not a fallback."""
    if not host:
        return None, "dns"
    try:
        infos = await asyncio.get_event_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except Exception:
        return None, "dns"
    addrs = [i[4][0] for i in infos]
    if not addrs:
        return None, "dns"
    if not all(address_allowed(a) for a in addrs):
        return None, "blocked-address"
    return addrs, None


def fetch_client():
    """Separate from `client`: no base_url, no redirects, short connect. Tests swap it."""
    return httpx.AsyncClient(timeout=httpx.Timeout(FETCH_TIMEOUT_S, connect=5), follow_redirects=False)


def video_mime(content_type, url):
    """Allowlisted mime for the response, or None. Falls back to the URL's
    extension only when the server declines to say (octet-stream/empty)."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ALLOWED_VIDEO_MIME:
        return ct
    if ct in ("", "application/octet-stream", "binary/octet-stream"):
        ext = os.path.splitext(httpx.URL(url).path)[1].lower()
        m = EXT_MIME.get(ext)
        return m if m in ALLOWED_VIDEO_MIME else None
    return None


async def fetch_video(url, fh):
    """Stream a caller-supplied URL into fh. Returns (mime, error class).

    Redirects are followed by hand so every hop is re-validated; the size cap is
    enforced on the stream, not after buffering the body.
    ponytail: validation and connection are separate steps, so a DNS rebind
    between them is still possible; pin the resolved IP (Host + sni_hostname on
    an IP URL) if that threat becomes real."""
    cap = int(MAX_VIDEO_MB * 2**20)
    async with fetch_client() as c:
        for _ in range(MAX_REDIRECTS + 1):
            u = httpx.URL(url)
            if u.scheme not in ("http", "https"):
                return None, "unsupported-scheme"
            _, err = await resolve_public(u.host)
            if err:
                return None, err
            async with c.stream("GET", u) as r:
                if r.is_redirect:  # httpx: 3xx *with* a Location header
                    url = str(u.join(r.headers["location"]))
                    continue
                if r.status_code != 200:
                    return None, f"http-{r.status_code}"
                mime = video_mime(r.headers.get("content-type"), str(u))
                if mime is None:
                    return None, "unsupported-type"
                cl = r.headers.get("content-length", "")
                if cl.isdigit() and int(cl) > cap:
                    return None, "too-large"
                n = 0
                async for chunk in r.aiter_bytes():
                    n += len(chunk)
                    if n > cap:
                        return None, "too-large"   # abort mid-stream; the body is never fully read
                    fh.write(chunk)
                return mime, None
    return None, "too-many-redirects"


async def prepare_video(part):
    """Duration of the request's video, plus the `data:` URL to send to vLLM in
    place of the caller's URL, so the engine never fetches anything itself.
    Returns (seconds, data_url, client-safe error)."""
    url = part.get("video_url", {}).get("url", "") if isinstance(part.get("video_url"), dict) else part.get("video_url", "")
    if not isinstance(url, str) or not url:
        return None, None, "video_url must be an http(s) URL or a data: URL"
    data_url = url if url.startswith("data:") else None
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        try:
            if url.startswith("data:"):
                try:
                    data = base64.b64decode(url.split(",", 1)[1], validate=False)
                except Exception:
                    return None, None, "could not read video (bad data: URL)"
                if len(data) > MAX_VIDEO_MB * 2**20:
                    return None, None, f"video larger than {MAX_VIDEO_MB} MB"
                f.write(data)
                del data
            elif url.startswith(("http://", "https://")):
                try:
                    mime, why = await asyncio.wait_for(fetch_video(url, f), FETCH_TIMEOUT_S)
                except asyncio.TimeoutError:
                    mime, why = None, "timeout"
                except httpx.TimeoutException:
                    mime, why = None, "timeout"
                except Exception as e:  # never echoed: the blind-SSRF oracle
                    print(f"gateway: video fetch failed ({type(e).__name__}: {e})", flush=True)
                    mime, why = None, "fetch-failed"
                if why:
                    if why != "fetch-failed":
                        print(f"gateway: video fetch rejected ({why})", flush=True)
                    return None, None, f"could not fetch video ({why})"
            else:
                return None, None, "video_url must be an http(s) URL or a data: URL"
            f.flush()
            try:
                secs = await asyncio.get_event_loop().run_in_executor(None, probe_seconds, f.name)
            except Exception as e:
                print(f"gateway: ffprobe failed ({type(e).__name__}: {e})", flush=True)
                return None, None, "could not read video (not a decodable video file)"
            if data_url is None:
                with open(f.name, "rb") as g:  # raw bytes are gone by now; only the base64 copy lives on
                    data_url = f"data:{mime};base64,{base64.b64encode(g.read()).decode()}"
        finally:
            os.unlink(f.name)
    if secs > MAX_VIDEO_SECONDS:
        return None, None, f"video is {secs:.0f}s; max is {MAX_VIDEO_SECONDS:.0f}s"
    return secs, data_url, None


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
    key, err = await authenticate(req)
    if err == 401:
        return JSONResponse({"error": {"message": "invalid api key", "type": "authentication_error"}}, status_code=401)
    if err:
        return JSONResponse({"error": {"message": "cannot verify api key, retry", "type": "server_error"}},
                            status_code=err, headers={"Retry-After": "5"})
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
                    secs, data_url, err = await prepare_video(part)
                    if err:
                        return JSONResponse({"error": {"message": err, "type": "invalid_request_error"}}, status_code=400)
                    if isinstance(part.get("video_url"), dict):   # hand vLLM the bytes, not the URL
                        part["video_url"]["url"] = data_url
                    else:
                        part["video_url"] = data_url
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
        if key:  # legacy-key requests have no org, so they stay in usage.jsonl only
            enqueue({"id": rid, "org_id": key["org_id"], "api_key_id": key["id"], "model_id": MODEL_ID,
                     "status": status, "stream": stream, "prompt_tokens": usage["prompt_tokens"],
                     "completion_tokens": usage["completion_tokens"], "video_seconds": round(secs, 3),
                     "ttft_ms": int(usage["ttft_s"] * 1000), "latency_ms": int(usage["wall_s"] * 1000),
                     "cached": False, "cost_usd": 0})

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
