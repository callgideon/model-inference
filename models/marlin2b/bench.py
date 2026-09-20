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
the command line is refused, and so is any argv value or --out path carrying 8 or
more characters of it.

Output is an ALLOWLIST, not a filter. Three rounds of review broke the previous
scrub-what-we-emit design, so this client does not emit untrusted text at all:

  * no server-controlled string is recorded verbatim. An error response contributes
    its status, our own error_class, and `error.code`/`error.type` only when they
    fullmatch [a-z0-9_]{1,64} AND carry no 8 characters of the key (otherwise the
    literal "unrecognized"); the body itself is recorded as sha256 + byte count for
    correlation, never as text. Retry-After must be numeric, Inference-Id must
    fullmatch [A-Za-z0-9-]{1,64}, Server-Timing names [a-z0-9_-]{1,32}, finish_reason
    [a-z0-9_]{1,64}, token counts must be ints — each under the same key check.
  * exceptions contribute type(e).__name__ and our own class, never str(e), and a
    top-level failure prints frames as file:line:function with no message and no locals.
  * asyncio and httpx/httpcore logging is muted to name+level only: their handlers
    print exception messages and request URLs, and they are not our sinks.
  * NO text from a URL is recorded. A URL input is labelled
    "url-<12 hex of sha256(whole URL)>" plus a container extension from a fixed list:
    a capability URL can hide its secret in the query, a path segment, the FILE NAME
    or the HOST, and the digest reveals none of them while staying per-URL distinct.

redact() survives only as defense in depth on the final serialised line (it removes
any key substring of 8 or more characters, and best-effort reduces URL query strings);
it is no longer the guarantee, and no claim here depends on a regex matching a URL.

Percentiles are suppressed, not guessed, when the accepted-sample count cannot
support them (research/plan/04-verification.md: 32 samples cannot establish a p99).

httpx + stdlib rather than the openai SDK: this client needs response headers
(`Inference-Id`, `Retry-After`, `Server-Timing`), the raw SSE line boundaries for
honest first-token timing, rejected-status bodies, and an in-process fake gateway
(httpx.MockTransport) for tests without a server.
"""
import argparse, asyncio, base64, contextlib, functools, json, logging, math, mimetypes, os, posixpath
import random, re, signal, statistics, subprocess, sys, time, traceback, urllib.parse
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
# Only a standalone CLI run may leave SIGINT ignored after writing its summary; an
# in-process caller (the tests, any future harness) keeps its own signal disposition.
CLI_PROCESS = False


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


def secret_grams(shape):
    """The 8-grams of one shape of the key that are evidence of the SECRET.

    `sk-infrx-` is public product boilerplate that appears in paths, labels and URLs on
    purpose (`apps/infrx-api/...`), so a window lying mostly inside it said nothing about
    the key and refused roughly one key in thirty at random. A window must take at least
    KEY_GRAM_FROM_BODY of its 8 characters from the body after the prefix. Fail-closed
    otherwise: a key with no known prefix contributes all of its windows."""
    n = KEY_MIN_SUBSTRING
    if len(shape) < n:
        return set()
    body_at = 0
    for prefix in KEY_PUBLIC_PREFIXES:
        for form in (prefix, fold(prefix)):
            if form and shape.startswith(form):
                body_at = max(body_at, len(form))
    first = max(0, body_at - (n - KEY_GRAM_FROM_BODY))
    return {shape[i:i + n] for i in range(first, len(shape) - n + 1)}


def carries_key(value, key):
    """True when `value` contains any 8-character window of the key's secret body,
    case-insensitively and after the same slug folding raw_path() applies (a key in
    --label reappears in a file name and in the summary's "raw" field with its
    punctuation rewritten).

    Membership of the value's own 8-grams in a set of the key's: O(len(value)), no regex
    over a caller-supplied string. Used both at the argv gate and inside allow()."""
    if not key or len(key) < KEY_MIN_SUBSTRING:
        return False
    n = KEY_MIN_SUBSTRING
    for shape in (key.lower(), fold(key)):
        grams = secret_grams(shape)
        if not grams:
            continue
        for cand in (str(value).lower(), fold(value)):
            if any(cand[i:i + n] in grams for i in range(len(cand) - n + 1)):
                return True
    return False


def refuse_key_in_args(a, key):
    """Exit 2 naming the FLAG only, never the value, when an argument carries the key.
    --label and --out end up in file names and in the summary; argparse would echo them
    on any later error. Refusing beats redacting: the run never starts."""
    for name, value in sorted(vars(a).items()):
        if isinstance(value, str) and carries_key(value, key):
            flag = "positional video argument" if name == "video" else f"--{name.replace('_', '-')}"
            print(f"refusing to run: the {flag} contains 8 or more characters of the API key; "
                  f"pass a value that does not embed it (the value is not echoed here)",
                  file=sys.stderr)
            raise SystemExit(2)


# ---------------------------------------------------------------- allowlists
#
# The guarantee is here, not in redact(): a value a server or a URL controls is either
# recognised by one of these patterns and recorded as-is, or replaced by a fixed
# literal. Nothing server-controlled is ever recorded verbatim, so there is no query
# string, no signature, no echoed key and no exception text to scrub in the first place.

CODE_OK = re.compile(r"[a-z0-9_]{1,64}")            # error.code / error.type / finish_reason
ID_OK = re.compile(r"[A-Za-z0-9-]{1,64}")           # Inference-Id (a UUID passes)
TIMING_NAME_OK = re.compile(r"[a-z0-9_-]{1,32}")    # Server-Timing metric names
LABEL_OK = re.compile(r"[a-z0-9-]{1,64}")           # our own slugged --label
# A URL label is a digest, never text from the URL: a capability URL can carry its secret
# in the FILE NAME (cdn.invalid/v/<token>.mp4) or in the HOST (a tunnel subdomain), so
# neither may be recorded. Only a container extension from this fixed list survives.
VIDEO_EXT_OK = {".mp4", ".m4v", ".webm", ".mov", ".mpeg", ".mpg"}
URL_LABEL_PREFIX = "url-"
URL_LABEL_DIGEST = 12                               # 48 bits: distinct per URL, reveals none of it
UNKNOWN = "unrecognized"
KEY_MARK = "[redacted-key]"
QUERY_MARK = "[redacted-query]"
KEY_MIN_SUBSTRING = 8       # no run of 8+ key characters may appear anywhere, ever
# Everything before this is public product boilerplate, not secret, so an 8-gram lying
# (mostly) inside it is not evidence that a path or label carries the key.
KEY_PUBLIC_PREFIXES = ("sk-infrx-", "sk-marlin-", "sk-")
KEY_GRAM_FROM_BODY = 6      # an 8-gram must take >= 6 characters from the secret body


def allow(value, pattern, key="", fallback=UNKNOWN):
    """A server-controlled string, or `fallback`. None when the server sent nothing.

    Two gates, not one: the value must fullmatch its allowlist AND must not carry 8 or
    more characters of the API key. A gateway that echoes a lower-cased key body as
    `error.code` would otherwise satisfy [a-z0-9_]{1,64} and be recorded as-is."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value) or carries_key(value, key):
        return fallback
    return value


def as_int(value):
    """Token counts are numbers; a server that sends a string there gets None, not a row
    field it controls the bytes of."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_float(value):
    """Retry-After, numeric only (seconds). A date form or anything else -> None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fold(text):
    """The folding a label goes through on its way into a file name: lowercase, every
    run of non-alphanumerics to a single '-'."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def slug(text, limit=64):
    """Our own label, folded into LABEL_OK. Also what raw_path() names a file with."""
    s = fold(text)[:limit]
    return s if s and LABEL_OK.fullmatch(s) else ""


def is_url(s):
    return bool(s) and s.lower().startswith(("http://", "https://"))


def video_label(video):
    """Label for the positional `video` argument. NO text from a URL is ever recorded.

    A capability URL can hide its secret anywhere — in the query, in a path segment, in
    the file name (`cdn.invalid/v/<token>.mp4`) or in the host (a tunnel subdomain) — so
    the label is `url-<first 12 hex of sha256(whole URL)>` plus a container extension,
    and only when that extension is one of VIDEO_EXT_OK. The digest is stable, so E4 gets
    per-URL distinctness (and can match a label to a URL it already holds) without the
    client writing any part of the URL down. Local files keep their basename, as this
    script always reported them."""
    if not is_url(video):
        return os.path.basename(video)
    digest = sha256(video.encode("utf-8", "replace")).hexdigest()[:URL_LABEL_DIGEST]
    try:
        ext = posixpath.splitext(urllib.parse.urlsplit(video).path or "")[1].lower()
    except ValueError:                                  # an unparseable URL tells us nothing
        ext = ""
    return URL_LABEL_PREFIX + digest + (ext if ext in VIDEO_EXT_OK else "")


# ---------------------------------------------------------------- defense in depth

# Best effort only, and no longer load-bearing: a query string is never recorded, so
# nothing depends on this pattern finding one.
QUERY_ISH = re.compile(r"\?[^\s\"'\\]{0,4000}")
MARK_RUN = re.compile(r"(?:" + re.escape(KEY_MARK) + r"){2,}")   # adjacent windows read as one


@functools.lru_cache(maxsize=4)
def _key_grams(key):
    """One alternation of every 8-character window of the key. Any longer run of key
    characters contains such a window, so re.sub() with this cannot leave one behind —
    a substring anywhere in the key, not just a prefix (an echoed key[9:] used to pass)."""
    grams = {key[i:i + KEY_MIN_SUBSTRING] for i in range(len(key) - KEY_MIN_SUBSTRING + 1)}
    return re.compile("|".join(re.escape(g) for g in sorted(grams))) if grams else None


def redact(text, key):
    """LAST line of defense on an already-built line, not the guarantee any more.

    Removes every run of >= 8 key characters and blanks anything that still looks like a
    URL query string. The allowlists above are what make the output safe; this exists so
    that a field added later without thinking still cannot carry a key through, and it is
    idempotent so applying it twice is free.
    """
    text = str(text)
    if key:
        text = text.replace(key, KEY_MARK)              # the whole key reads better as one mark
        if len(key) >= KEY_MIN_SUBSTRING:
            # Any longer run of key characters contains an 8-window, so nothing >= 8 survives;
            # what is left over around a replaced window is shorter than that by construction.
            text = MARK_RUN.sub(KEY_MARK, _key_grams(key).sub(KEY_MARK, text))
    return QUERY_ISH.sub("?" + QUERY_MARK, text)


def dump_line(obj, key, **kw):
    """The only way a row or summary becomes text: serialise, then redact."""
    return redact(json.dumps(obj, **kw), key)


def mute_library_logging():
    """asyncio's default handler prints 'Task exception was never retrieved' WITH the
    exception message, and httpx logs request URLs; neither passes through our sinks.
    Their loggers may say that they spoke, and nothing else."""
    class NameOnly(logging.Handler):
        def emit(self, record):
            print(f"log: {record.name} {record.levelname}", file=sys.stderr)

    for name in ("asyncio", "httpx", "httpcore", "hpack"):
        log = logging.getLogger(name)
        log.handlers = [NameOnly()]
        log.propagate = False
        log.setLevel(logging.WARNING)


def frame_list(exc):
    """A traceback as file:line:function only — no message, no locals, no source line."""
    return [f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}"
            for f in traceback.extract_tb(exc.__traceback__)]


class Parser(argparse.ArgumentParser):
    """argparse writes usage and errors straight to stderr, echoing the offending argv
    token — and the positional `video` argument may be a signed URL. Same choke point."""

    def _print_message(self, message, file=None):
        super()._print_message(redact(message, api_key()) if message else message, file)


def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    refuse_embedded_key(argv)
    ap = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    if video and not is_url(video):
        return training_budget_kwargs(video)
    return None


# ---------------------------------------------------------------- corpus


def shared_repo_root(start):
    """The MAIN checkout's root, so every worktree shares one media cache instead of
    filling up with its own 483 MB copy (N7). `git rev-parse --git-common-dir` points at
    the main .git even from a linked worktree; without git we fall back to the tree we
    are in. Mirrors corpus/build.py's default_cache_root() — the two must agree."""
    try:
        r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=start, text=True,
                           capture_output=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return os.path.dirname(os.path.abspath(os.path.join(start, r.stdout.strip())))
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def corpus_cache_root(manifest, path):
    env = os.environ.get(manifest.get("cache_root_env", "CORPUS_CACHE"))
    if env:
        return env
    here = os.path.dirname(os.path.abspath(path))
    repo = shared_repo_root(here) or os.path.dirname(os.path.dirname(here))
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
                    "clip_id": clip["id"] if clip else (video_label(video) if video else None),
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
        if path and is_url(path):
            return path
        raise ValueError("video_url form needs --media-base-url or an http(s) video")
    if form == "video_b64":
        if is_url(path):
            return path
        # base64 of a 35 MB clip is ~47 MB of CPU work: off the event loop, or one
        # arrival delays every other arrival and the open-loop rate is a fiction.
        return await asyncio.to_thread(data_url, path)
    if form == "upload":
        t = time.perf_counter()
        handle = await upload(client, cfg, path, row)
        row["upload_s"] = round(time.perf_counter() - t, 4)
        return UPLOAD_REF_SCHEME + handle
    raise ValueError(f"unknown form {form}")


def read_and_digest(path):
    with open(path, "rb") as f:
        body = f.read()
    return body, sha256(body).hexdigest()


class UploadFailed(RuntimeError):
    """Carries no text: the status lands in row["upload_status"], the class in error_class.
    httpx's raise_for_status() message would quote the signed destination URL."""


async def upload(client, cfg, path, row):
    """POST /v1/uploads -> PUT to the returned constrained destination ->
    POST /v1/uploads/{handle}/complete. Contracts v1 shape; unverified until M3/G4."""
    body, digest = await asyncio.to_thread(read_and_digest, path)   # 35 MB read + sha off the loop
    mime = mimetypes.guess_type(path)[0] or "video/mp4"
    r = await client.post(cfg["base"] + "/uploads", headers=cfg["headers"],
                          json={"purpose": "video", "filename": os.path.basename(path),
                                "bytes": len(body), "sha256": digest, "content_type": mime})
    row["upload_status"] = r.status_code
    if r.status_code >= 400:
        raise UploadFailed()
    created = r.json()
    dest = created.get("upload") or created
    put = await client.request(dest.get("method", "PUT"), dest["url"], content=body,
                              headers={"content-type": mime, **(dest.get("headers") or {})})
    row["upload_status"] = put.status_code
    if put.status_code >= 400:
        raise UploadFailed()
    done = await client.post(f"{cfg['base']}/uploads/{created['handle']}/complete", headers=cfg["headers"],
                             json={"sha256": digest, "bytes": len(body)})
    row["upload_status"] = done.status_code
    if done.status_code >= 400:
        raise UploadFailed()
    return (done.json() or {}).get("handle", created["handle"])


def parse_server_timing(value, key=""):
    """`Server-Timing: queue;dur=12.3, prep;dur=400` -> {"queue": 12.3, "prep": 400.0}.

    Names are allowlisted (TIMING_NAME_OK) and durations must parse as floats, so a
    header is turned into numbers we chose the shape of, never echoed as text."""
    out, dropped = {}, 0
    for part in (value or "").split(","):
        name, _, rest = part.strip().partition(";")
        m = re.search(r"dur\s*=\s*([0-9.]+)", rest)
        name, dur = name.strip(), as_float(m.group(1)) if m else None
        if TIMING_NAME_OK.fullmatch(name) and dur is not None and not carries_key(name, key):
            out[name] = dur
        elif name or dur is not None:
            dropped += 1
    if dropped:
        out["dropped_metrics"] = dropped        # a count, never the name we refused
    return out or None


# ---------------------------------------------------------------- one attempt


async def attempt(client, cfg, item, t0, attempt_no):
    """One HTTP attempt. Every path fills the same row schema, rejections included."""
    row = {"seq": item["seq"], "attempt": attempt_no, "clip_id": item["clip_id"], "form": item["form"],
           "prompt_kind": item["prompt_kind"], "cold": item["cold"], "duration_s": item["duration_s"],
           "scheduled_s": item["arrival_s"], "send_s": None, "first_byte_s": None, "first_token_s": None,
           "last_token_s": None, "end_s": None, "http_status": None, "outcome": None, "error_class": None,
           # error_code/error_type are allowlisted server strings; the body itself is a
           # digest and a length, never text (round 4: no verbatim server bytes at all)
           "error_code": None, "error_type": None, "body_sha256": None, "body_bytes": None,
           "inference_id": None, "retry_after": None,
           "server_timing": None, "prompt_tokens": None, "completion_tokens": None, "usage_missing": None,
           "content_chars": 0, "upload_s": None, "upload_status": None, "media_sent": False,
           "finish_reason": None,
           "stream_complete": None, "schedule_lag_s": None,
           # request-level fields, filled by run_one() on the attempt that ends the request
           "request_send_s": None, "request_latency_s": None, "latency_from_scheduled_s": None}
    now = lambda: round(time.perf_counter() - t0, 6)
    try:
        await _send(client, cfg, item, row, now)
    except Exception as e:                               # transport, file, upload or protocol failure
        # The exception TYPE, never its text: httpx quotes the full request URL and any
        # header it was given, and an OSError quotes the path it was handed.
        row["outcome"] = row["outcome"] or "failed"
        row["error_class"] = row["error_class"] or type(e).__name__
        row["end_s"] = row["end_s"] or now()
    ttft = None if row["first_token_s"] is None or row["send_s"] is None else \
        round(row["first_token_s"] - row["send_s"], 6)
    row["ttft_s"] = ttft
    row["latency_s"] = None if row["end_s"] is None or row["send_s"] is None else \
        round(row["end_s"] - row["send_s"], 6)
    if cfg["open_loop"] and row["send_s"] is not None:    # coordinated omission: how late we sent
        row["schedule_lag_s"] = round(row["send_s"] - row["scheduled_s"], 6)
    n = row["completion_tokens"] or 0
    row["tpot_s"] = (round((row["last_token_s"] - row["first_token_s"]) / max(n - 1, 1), 6)
                     if n and row["first_token_s"] is not None else None)
    return row


async def _send(client, cfg, item, row, now):
    """Issue the request and fill `row`. Returning early is fine: attempt() finalises."""
    ref = await media_ref_for(item, cfg, client, row)
    payload = {"model": cfg["model"], "messages": messages_for(item, ref), "max_tokens": cfg["max_tokens"],
               "temperature": 0, "stream": True, "stream_options": {"include_usage": True}}
    # A corpus clip knows its duration; the single-video path does not, and 'auto' there
    # means an ffprobe subprocess, so make_config() resolved it once instead of per attempt.
    mm = (resolve_mm_kwargs(cfg["args"], duration=item["duration_s"])
          if item["duration_s"] is not None else cfg["mm_fixed"])
    if mm:
        payload["mm_processor_kwargs"] = mm
    row["send_s"] = now()
    row["media_sent"] = ref is not None    # this clip's bytes/handle really went out
    async with client.stream("POST", cfg["base"] + "/chat/completions", json=payload,
                             headers=cfg["headers"]) as resp:
        row["first_byte_s"] = now()
        row["http_status"] = resp.status_code
        # Headers are server-controlled: allowlist or fixed literal, never verbatim.
        row["inference_id"] = allow(resp.headers.get("inference-id"), ID_OK, cfg["key"])
        row["retry_after"] = as_float(resp.headers.get("retry-after"))
        row["server_timing"] = parse_server_timing(resp.headers.get("server-timing"), cfg["key"])
        if resp.status_code != 200:
            # The body is never recorded as text. It is hashed for correlation (an
            # operator can match it against the gateway's own log) and counted, and only
            # `code`/`type` may contribute, only when they fullmatch CODE_OK. There is
            # nothing left to truncate, so no cut can strand half a secret.
            raw = await resp.aread()
            row["body_sha256"] = sha256(raw).hexdigest()
            row["body_bytes"] = len(raw)
            try:
                parsed = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                parsed = None
            err = parsed.get("error") if isinstance(parsed, dict) else None
            if not isinstance(err, dict):   # a string, a list, null: nothing allowlistable
                err = {}
            row["outcome"] = "rejected" if resp.status_code in REJECT_STATUS else "failed"
            row["error_class"] = f"http_{resp.status_code}"
            row["error_code"] = allow(err.get("code"), CODE_OK, cfg["key"])
            row["error_type"] = allow(err.get("type"), CODE_OK, cfg["key"])
            row["end_s"] = now()
            return
        usage, saw_done = None, False
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
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
                e = chunk["error"] if isinstance(chunk["error"], dict) else {}
                row["error_code"] = allow(e.get("code"), CODE_OK, cfg["key"])   # same rule as HTTP
                row["error_type"] = allow(e.get("type"), CODE_OK, cfg["key"])
                row["end_s"] = now()
                return
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    row["finish_reason"] = allow(choice["finish_reason"], CODE_OK, cfg["key"])
                text = (choice.get("delta") or {}).get("content")
                if text:
                    row["first_token_s"] = row["first_token_s"] or now()
                    row["last_token_s"] = now()
                    row["content_chars"] += len(text)
        row["end_s"] = now()
        row["usage_missing"] = usage is None
        if isinstance(usage, dict):   # authoritative usage only, never chunk counting; ints only
            row["prompt_tokens"] = as_int(usage.get("prompt_tokens"))
            row["completion_tokens"] = as_int(usage.get("completion_tokens"))
        # A 200 whose stream just stops — no [DONE], no finish_reason, no usage — is a
        # truncated response, not a success; E2's abrupt-exit drill needs them apart.
        row["stream_complete"] = bool(saw_done or row["finish_reason"] or usage)
        if row["first_token_s"] is None:
            row["outcome"], row["error_class"] = "failed", "no_content_delta"
        elif not row["stream_complete"]:
            row["outcome"], row["error_class"] = "failed", "truncated_stream"
        else:
            row["outcome"] = "accepted"


@contextlib.contextmanager
def sigint_deferred(state, ignore_after=False):
    """Hold SIGINT for the length of one short write.

    A Ctrl-C landing between `write()` and `flush()`, or in the middle of the final
    summary, would lose exactly the data the interrupt handling exists to keep — and a
    second, impatient Ctrl-C used to do just that. The signal is recorded in `state` and
    acted on afterwards; the exit code is still 130. Not the main thread (a test harness,
    say): nothing to defer, the writes are still short.

    `ignore_after` leaves SIGINT *ignored* on the way out instead of restoring the default
    handler, and the final summary needs it: CPython coalesces signals into one flag and
    runs the Python-level callback at the next bytecode check, so a second SIGINT could
    still be pending when the handler is restored and would then kill the process
    (measured: exit -2 in 3 of 10 runs) after the summary was already safely written. By
    then there is nothing left to interrupt but the return of an exit code."""
    try:
        previous = signal.signal(signal.SIGINT, lambda *_: state.__setitem__("interrupted", True))
    except ValueError:
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN if ignore_after else previous)


def write_row(cfg, row):
    """One raw line per attempt, flushed as it completes (N3: a Ctrl-C used to lose the
    whole run's rows, since they were only written after the last request)."""
    f = cfg.get("raw_file")
    if f is None or f.closed:
        return
    state = cfg.get("state") or {}
    with sigint_deferred(state):
        f.write(dump_line(row, cfg["key"]) + "\n")
        f.flush()
    if state.get("interrupted"):      # arrived during the write: honour it now, row intact
        raise KeyboardInterrupt


async def run_one(client, cfg, item, t0, rows):
    """Every attempt is kept in `rows`; the last one carries the request-level timings."""
    first_send = None
    for k in range(cfg["retries"] + 1):
        row = await attempt(client, cfg, item, t0, k)
        row["retries"] = k
        rows.append(row)
        first_send = row["send_s"] if first_send is None else first_send
        if row["http_status"] not in (429, 503) or k == cfg["retries"]:
            # Request latency spans every rejected attempt and every retry wait: a
            # retried request must never look as fast as a first-try success.
            row["request_send_s"] = first_send
            if row["end_s"] is not None and first_send is not None:
                row["request_latency_s"] = round(row["end_s"] - first_send, 6)
            if cfg["open_loop"] and row["end_s"] is not None:   # coordinated omission
                row["latency_from_scheduled_s"] = round(row["end_s"] - row["scheduled_s"], 6)
            write_row(cfg, row)          # written once complete, request-level fields included
            return row
        write_row(cfg, row)              # a retried attempt is already final as it stands
        wait = row["retry_after"] if row["retry_after"] is not None else 1.0   # numeric already
        await asyncio.sleep(min(max(wait, 0.0), 30.0))


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
    for field, src, scale in (("ttft_s", "ttft_s", 1.0), ("latency_s", "request_latency_s", 1.0),
                              ("tpot_ms", "tpot_s", 1000.0)):
        block, sup = percentile_block(accepted, src, scale)
        pct[field] = block
        suppressed += sup
    # Coordinated omission: when the driver cannot keep up, latency from the SEND time
    # hides the wait it caused. Reported from the scheduled arrival as well (open loop).
    pct["latency_from_scheduled_s"], _ = percentile_block(accepted, "latency_from_scheduled_s")
    # First attempts only: a retried attempt's send - scheduled includes the previous
    # attempt and the Retry-After wait, which is not driver lag and must not invalidate
    # an open-loop cell. Per-attempt lag stays in the raw rows.
    firsts = [r for r in rows if r["attempt"] == 0]
    lag_block, _ = percentile_block(firsts, "schedule_lag_s")
    lags = [r["schedule_lag_s"] for r in firsts if r.get("schedule_lag_s") is not None]
    lag_block["max"] = round(max(lags), 6) if lags else None
    cold = [r for r in accepted if r["cold"] is True]
    warm = [r for r in accepted if r["cold"] is False]
    cold_ttft, _ = percentile_block(cold, "ttft_s")
    warm_ttft, _ = percentile_block(warm, "ttft_s")
    prompt_tokens = [r["prompt_tokens"] for r in accepted if r["prompt_tokens"] is not None]
    res = {
        # our own label, slugged into LABEL_OK (it also names the raw file)
        "label": slug(cfg["args"].label), "target": cfg["args"].target, "model": cfg["model"],
        "mode": "open-loop" if cfg["args"].rate else "closed-loop",
        "mm_kwargs": cfg["summary_mm_kwargs"], "video": cfg["video_label"],
        "corpus": cfg["corpus_label"], "subset": cfg["args"].subset if cfg["args"].corpus else None,
        "forms": cfg["forms"], "seed": cfg["args"].seed, "rate_per_s": cfg["args"].rate,
        "concurrency": None if cfg["args"].rate else cfg["concurrency"],
        "requests": len(finals), "attempts": len(rows),
        "max_tokens": cfg["max_tokens"],
        "accepted": len(accepted), "rejected": len(rejected), "failed": len(failed),
        "accepted_without_usage": sum(1 for r in accepted if r["usage_missing"]),
        # Retries collapse a request to its final attempt, so every rejected or failed
        # ATTEMPT is reported too: a 429 that a retry papered over stays visible.
        "denominators": {"latency_samples": len(accepted), "rejected_excluded": len(rejected),
                         "failed_excluded": len(failed), "scheduled": len(finals),
                         "attempts": len(rows),
                         "rejected_attempts": sum(1 for r in rows if r["outcome"] == "rejected"),
                         "failed_attempts": sum(1 for r in rows if r["outcome"] == "failed"),
                         "retried_requests": sum(1 for r in finals if r.get("retries"))},
        "status_counts": _counts(finals, "http_status"), "error_classes": _counts(finals, "error_class"),
        "error_codes": _counts(finals, "error_code"),
        "attempt_status_counts": _counts(rows, "http_status"), "attempt_outcomes": _counts(rows, "outcome"),
        # Only clips whose media actually went out: a `text` slot uses the clip's prompt
        # and sends no media, so counting it would overstate cold-path coverage.
        "distinct_clips": len({r["clip_id"] for r in rows if r["clip_id"] and r["media_sent"]}),
        "distinct_clips_scheduled": len({r["clip_id"] for r in finals if r["clip_id"]}),
        "cold_requests": len(cold), "warm_requests": len(warm),
        "cold_ttft_s": cold_ttft, "warm_ttft_s": warm_ttft,
        "prompt_tokens": prompt_tokens[0] if prompt_tokens else None,
        "prompt_tokens_median": round(statistics.median(prompt_tokens), 1) if prompt_tokens else None,
        "interrupted": False,        # _run() sets this true for a partial, Ctrl-C run
        "wall_s": round(wall, 2),
        "req_per_s": round(len(accepted) / wall, 3) if wall else None,
        "out_tok_per_s": round(out_tokens / wall, 1) if wall else None,
        "percentiles": pct, "schedule_lag_s": lag_block,
        "suppressed_percentiles": sorted(set(suppressed)),
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
    mm_fixed = resolve_mm_kwargs(a, video=a.video) if not a.corpus else None   # probes once
    return {"args": a, "key": key, "headers": headers, "base": a.base_url.rstrip("/"),
            "model": a.model, "max_tokens": a.max_tokens, "concurrency": a.concurrency,
            "retries": a.retries, "video": a.video, "forms": forms, "open_loop": bool(a.rate),
            "media_base_url": a.media_base_url, "mm_fixed": mm_fixed,
            "video_label": (video_label(a.video) if a.video else f"corpus:{a.subset}"),
            "corpus_label": (manifest or {}).get("corpus_version") if a.corpus else None,
            "summary_mm_kwargs": mm_fixed if not a.corpus else
                                 ("per-clip auto" if a.mm_kwargs == "auto" and a.target == "direct"
                                  else resolve_mm_kwargs(a))}


def raw_path(a):
    if a.raw:
        return a.raw
    return os.path.join(os.path.dirname(a.out) or ".", "raw",
                        f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{slug(a.label) or 'run'}.jsonl")


async def execute(a, state):
    """`state` carries rows, cfg and the start time OUT of here, so a Ctrl-C mid-run
    still has everything completed so far (rows are already on disk as well)."""
    clips, manifest = [], None
    if a.corpus:
        clips, _, manifest = load_corpus(a.corpus, a.subset)
    cfg = state["cfg"] = make_config(a, clips, manifest)
    cfg["raw_file"], cfg["state"] = state["raw_file"], state
    prompt = a.prompt
    if not a.corpus and prompt is None:
        prompt = smoke_namespace()["canonical_prompt"](a.weights, "caption")
    schedule = build_schedule(a.requests, clips, cfg["forms"], prompt=prompt, rate=a.rate,
                              seed=a.seed, video=a.video)
    rows = state["rows"]
    # asyncio's default handler prints the exception MESSAGE of an unretrieved task.
    asyncio.get_running_loop().set_exception_handler(
        lambda loop, ctx: print(f"loop: {type(ctx.get('exception')).__name__} "
                                f"({len(ctx)} context keys withheld)", file=sys.stderr))
    transport = load_transport(a.dry_run_transport) if a.dry_run_transport else None
    async with httpx.AsyncClient(timeout=a.timeout, transport=transport) as client:
        if not a.rate and not a.corpus and not a.no_warmup:
            warm = build_schedule(1, clips, cfg["forms"], prompt=prompt, seed=a.seed, video=a.video)
            await run_one(client, cfg, warm[0], time.perf_counter(), [])   # warm-up, not counted
        state["t0"] = time.perf_counter()
        state["wall"] = await (run_open_loop(client, cfg, schedule, rows) if a.rate
                               else run_closed_loop(client, cfg, schedule, rows))


def main(argv=None):
    """Wrapper only: nothing may reach stderr around _run() either. A traceback would
    carry the exception message; only its type and its frames may be printed."""
    try:
        return _run(argv)
    except SystemExit as e:
        raise SystemExit(redact(e.code, api_key()) if isinstance(e.code, str) else e.code)
    except BaseException as e:
        sys.exit(f"bench failed: {type(e).__name__} (message withheld) at " +
                 " <- ".join(reversed(frame_list(e))))


def _run(argv=None):
    mute_library_logging()
    a = parse_args(argv)
    refuse_key_in_args(a, api_key())         # exit 2 before anything is opened or printed
    raw = raw_path(a)
    for path in (raw, a.out):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    state = {"rows": [], "cfg": None, "wall": None, "t0": None, "raw_file": None,
             "interrupted": False}
    # Rows are appended and flushed as each attempt finishes: a Ctrl-C or a crash keeps
    # every completed row instead of losing the whole run's raw data.
    with open(raw, "w", encoding="utf-8") as raw_file:
        state["raw_file"] = raw_file
        try:
            asyncio.run(execute(a, state))
        except KeyboardInterrupt:
            state["interrupted"] = True
    # SIGINT is deferred for the WHOLE tail, not just the write calls: an impatient second
    # Ctrl-C landing between summarize() and the append would have killed the process
    # (observed: exit -2, killed by the signal) with the summary half written. The tail is
    # bounded work on data already in memory, so holding the signal costs nothing, and
    # ignore_after keeps a coalesced second signal from landing once we are done.
    with sigint_deferred(state, ignore_after=CLI_PROCESS):
        cfg = state["cfg"]
        if cfg is None:                      # interrupted before the run could start
            print("interrupted before the first request; nothing to summarise", file=sys.stderr)
            return 130
        wall = state["wall"] if state["wall"] is not None else \
            (time.perf_counter() - state["t0"] if state["t0"] else 0.0)
        res = summarize(state["rows"], wall, cfg)
        res["interrupted"] = state["interrupted"]   # a partial run must never read as complete
        res["raw"] = os.path.relpath(raw, os.path.dirname(a.out) or ".")
        key = cfg["key"]                     # every sink below serialises, then redacts
        print(dump_line(res, key, indent=2))
        if res["suppressed_percentiles"]:
            print("suppressed (sample count too small): " +
                  redact("; ".join(res["suppressed_percentiles"]), key), file=sys.stderr)
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(dump_line(res, key) + "\n")
        return 130 if state["interrupted"] else (0 if res["accepted"] else 1)


if __name__ == "__main__":
    CLI_PROCESS = True
    sys.exit(main())
