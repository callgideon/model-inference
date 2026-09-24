#!/usr/bin/env python3
"""Load test for the Marlin-2B endpoint: closed-loop concurrency (historical mode)
or open-loop Poisson arrivals (optionally bursty) over a corpus of distinct clips.

    python models/marlin2b/bench.py video.mp4 --concurrency 8 --requests 32
    python models/marlin2b/bench.py video.mp4 -c 1 -n 5 --max-tokens 256        # latency floor
    python models/marlin2b/bench.py --corpus models/marlin2b/corpus/manifest.json \
        --subset fast --rate 2 --requests 120 --seed 7 --target gateway --forms video_b64,text
    python models/marlin2b/bench.py --corpus … --resume results/raw/<previous>.jsonl
    python models/marlin2b/bench.py --report models/marlin2b/results/bench.jsonl

The run protocol, the frozen profile and the provisional acceptance criteria are
predeclared in models/marlin2b/results/E1B-protocol.md; this client only measures.

Writes one summary JSON line to --out (default models/marlin2b/results/bench.jsonl)
and one raw line per attempt to --raw (default <out dir>/raw/<run>.jsonl); resource
samples go to the same raw file as {"kind": "resource_sample", …} lines.

Every scheduled item carries the SOP dataset identity of research/workloads/marlin-sop.md
§3.1 — an `item_key` over (dataset_version, source_id, episode_id, segment_index,
start-end, prompt_version, profile_version) and `Idempotency-Key: sop1.<item_key>`.
Because the schedule is a pure function of the seed, `--resume <previous raw file>`
re-derives the same keys and the same payloads, which is what makes the MARLIN-SOP
"no second accepted item after an interruption" property testable at all.

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
# A rejection a resume must NOT repeat: marlin-sop.md §3.6 quarantines these and retrying
# them unchanged is pointless. 402 and 429 are explicitly resumable (fund, or back off).
TERMINAL_REJECT_STATUS = {400, 401, 403, 404, 409, 410, 413, 415, 422}
# R106, a cancelled job's replay is terminal for that key: a replay (the gateway's
# Idempotency-Replayed) whose stream answered `state_conflict` is a job the client's
# interruption cancelled - a client that left is a committed cancel (R21), and a replay
# answers the committed result (R91). Re-issuing the item takes a new key. Only the
# allowlisted code is read, never the text. A fresh request's `state_conflict`, and any
# non-stream answer, stay what they were: the sync replay is a 409, terminal by its status.
CANCELLED_REPLAY = "cancelled_by_interruption"
MIN_TAIL = 3            # a reported quantile needs this many samples strictly beyond it
PCTS = (50, 90, 95, 99)
# R61(1) / marlin-sop.md §3.3: the customer-facing upload reference is `infrx-upload:upl_…`
# and nothing else. `upload://` (this client's previous spelling) is refused at ingress by
# validate.check_video_ref, so the `upload` form could never have passed it (S2M D11).
UPLOAD_REF_SCHEME = "infrx-upload:"
# marlin-sop.md §3.1: `Idempotency-Key = sop1.<item_key>`, scoped org + operation + key.
IDEMPOTENCY_PREFIX = "sop1."
ITEM_KEY_HEX = 32
# A failed attempt this client can ATTRIBUTE to the platform: a 5xx, or a 200 whose stream
# the server broke. A transport error, a local file error or an upload failure may or may not
# be the platform's fault and this client cannot tell, so they are counted separately rather
# than folded into the <1 % criterion (R21 makes platform-caused failures free, which is
# exactly why the count may not be inflated).
PLATFORM_ERROR_CLASSES = frozenset({"stream_error_event", "truncated_stream", "no_content_delta",
                                    "malformed_sse_chunk"})


def is_platform_failure(row):
    status = row.get("http_status")
    return row.get("outcome") == "failed" and (
        (isinstance(status, int) and status >= 500)
        or row.get("error_class") in PLATFORM_ERROR_CLASSES)


# The phases a run wants timed end to end (18-marlin-backend-first.md E1B.b). The gateway
# publishes them as Server-Timing metrics; a name that is absent is reported as absent,
# never inferred from the wall clock.
PHASES = ("retrieval", "decode", "prepare", "queue", "prefill", "generate",
          "journal", "persist", "settle")
# Only a standalone CLI run may leave SIGINT ignored after writing its summary; an
# in-process caller (the tests, any future harness) keeps its own signal disposition.
CLI_PROCESS = False

# Injected so the open-loop driver can be driven by a virtual clock. A wall-clock bound on
# scheduling lag ("lag < 0.1 s") is a bound on how busy the HOST is, and it made `make check`
# flaky on a loaded box (measured: 0.234 s at load average 45 on 16 cores). With these two
# hooks a test can advance time itself, so "sends track the schedule" becomes an exact
# statement about the driver instead of a race with whatever else is running. Production runs
# keep the real clock and the real sleep; nothing but a test replaces them.
CLOCK = time.perf_counter
SLEEP = asyncio.sleep


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
    otherwise: a key with no known prefix contributes all of its windows.

    The folded form of a prefix must keep its SEPARATOR (`fold(prefix) + "-"`). `fold()`
    strips the trailing '-', so the bare folded form `sk-marlin` also prefix-matched a key
    spelled `sk-marlin2b…`: 'marlin2b' was then treated as public boilerplate and the
    windows that straddle it were dropped, i.e. a label carrying 8 characters of the secret
    body passed the argv gate (measured: 5 windows starting at index 7 instead of 11
    starting at index 1, so `rlin2bzz` was allowed). Fail-open, and the wrong direction.
    Keeping the separator is what makes the folded form useful at all - it exists for a
    prefix whose separator is not already '-' (`sk_infrx_` folds to `sk-infrx-`)."""
    n = KEY_MIN_SUBSTRING
    if len(shape) < n:
        return set()
    body_at = 0
    for prefix in KEY_PUBLIC_PREFIXES:
        for form in (prefix, fold(prefix) + "-"):
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
    over a caller-supplied string. Used both at the argv gate and inside allow().

    `key` may be several keys (mixed tenants): EVERY tenant's key is secret, so a value
    carrying any of them is refused. Checking only the first would make tenant 2's key the
    one field a hostile gateway could echo back into a row."""
    if isinstance(key, (list, tuple, set, frozenset)):
        return any(carries_key(value, one) for one in key)
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
# An upload handle is server-controlled and it goes into the request body AND into the
# resume state, so it is allowlisted like every other server string: contracts/ids.py
# spells it `upl_` + 22..64 of [A-Za-z0-9_-] and nothing else may be sent back as a ref.
HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{22,64}")
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
    for one in (key if isinstance(key, (list, tuple, set, frozenset)) else [key]):
        if not one:
            continue
        text = text.replace(one, KEY_MARK)              # the whole key reads better as one mark
        if len(one) >= KEY_MIN_SUBSTRING:
            # Any longer run of key characters contains an 8-window, so nothing >= 8 survives;
            # what is left over around a replaced window is shorter than that by construction.
            text = MARK_RUN.sub(KEY_MARK, _key_grams(one).sub(KEY_MARK, text))
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
    ap.add_argument("--max-tokens", default="512",
                    help="output-length budget; a comma list is a declared distribution "
                         "assigned round-robin (e.g. 128,512,1024)")
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
    ap.add_argument("--burst", type=int, default=1,
                    help="open-loop only: arrivals land in bursts of this size; the gap between "
                         "bursts is drawn at rate/burst so the mean arrival rate is unchanged")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dataset-version", default=None,
                    help="SOP dataset identity (marlin-sop.md §3.1); defaults to the corpus version")
    ap.add_argument("--profile-version", default="v1",
                    help="preprocessing profile in the item key: a different profile is a "
                         "different question about the same clip, so it must change the key")
    ap.add_argument("--tenant-keys", default="",
                    help="comma list of environment variable NAMES holding one API key each, "
                         "assigned round-robin (mixed tenants). Never a key value.")
    ap.add_argument("--resume", default=None,
                    help="a previous run's raw JSONL: items with a terminal outcome are not "
                         "re-sent, the rest keep their Idempotency-Key, payload and upload handle")
    ap.add_argument("--cancel-after", type=float, default=None,
                    help="cancel a selected request this many seconds after it was sent "
                         "(client disconnect)")
    ap.add_argument("--cancel-fraction", type=float, default=0.0,
                    help="fraction of scheduled items selected for cancellation, by seed")
    ap.add_argument("--sampler", default=None,
                    help="import path 'module:callable' returning a zero-argument resource "
                         "sampler; the default samples host RSS/CPU/load only")
    ap.add_argument("--sample-interval", type=float, default=0.0,
                    help="seconds between resource samples; 0 disables sampling")
    ap.add_argument("--engine-state", choices=["restarted", "warm", "unknown"], default="unknown",
                    help="declared cache state of the target at t0. A cold/warm claim needs "
                         "'restarted' (or genuinely unseen clips); 'unknown' is reported as such")
    ap.add_argument("--report", default=None,
                    help="read a summary JSONL (--out) and print the sweep report; runs nothing")
    ap.add_argument("--retries", type=int, default=0, help="retry 429/503 this many times, honouring Retry-After")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--dry-run-transport", default=None,
                    help="import path 'module:callable' returning an httpx transport (tests/dry runs, no network)")
    a = ap.parse_args(argv)
    if a.report:
        return a
    if not a.video and not a.corpus:
        ap.error("pass a video or --corpus")
    try:
        a.max_tokens_mix = [int(t) for t in str(a.max_tokens).split(",") if t.strip()]
    except ValueError:
        a.max_tokens_mix = []
    if not a.max_tokens_mix or any(t <= 0 for t in a.max_tokens_mix):
        ap.error("--max-tokens takes one positive integer or a comma list of them")
    a.max_tokens = a.max_tokens_mix[0]          # the historical scalar, still the first value
    if a.burst < 1:
        ap.error("--burst is at least 1")
    if not 0.0 <= a.cancel_fraction <= 1.0:
        ap.error("--cancel-fraction is a fraction of the schedule, 0..1")
    if a.cancel_fraction and a.cancel_after is None:
        ap.error("--cancel-fraction needs --cancel-after")
    a.tenant_env = [t.strip() for t in a.tenant_keys.split(",") if t.strip()]
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
                      # codec / source fps / frame count are profile axes PERF-ENVELOPE asks
                      # to be declared with any throughput number, not decoration.
                      "codec": c["derived"].get("codec"), "fps": c["derived"].get("fps"),
                      "frames": c["derived"].get("frames"),
                      "sha256": c["derived"]["sha256"], "prompt_id": c["prompt"],
                      "prompt": prompts[c["prompt"]]["text"], "prompt_kind": prompts[c["prompt"]]["kind"]})
    if not clips:
        sys.exit(f"no built clips in {path} subset {subset}; run corpus/build.py build")
    return clips, prompts, manifest


# ---------------------------------------------------------------- schedule (pure)


def item_key(dataset_version, source_id, episode_id, segment_index, start_s, end_s,
             prompt_version, profile_version):
    """marlin-sop.md §3.1, exactly: sha256 of the seven identity fields joined by US,
    first 32 hex. It is a function of the PAYLOAD, never of the attempt, which is the
    whole point: a client that regenerates keys per attempt defeats the no-duplicate
    property the idempotency scope provides."""
    parts = (dataset_version, source_id, episode_id, str(segment_index),
             f"{start_s}-{end_s}", prompt_version, profile_version)
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:ITEM_KEY_HEX]


def profile_frames(duration_s, fps=2.0, min_frames=4, max_frames=240):
    """Frames the pinned profile v1 samples from a clip of this duration (marlin-sop.md
    §1.5): the frame count a throughput number has to be quoted against."""
    if duration_s is None:
        return None
    frames = int(min(max_frames, max(min_frames, round(duration_s * fps))))
    return frames + frames % 2


def build_schedule(n, clips, forms, prompt=None, rate=None, seed=0, video=None, burst=1,
                   max_tokens_mix=(512,), tenants=1, dataset_version="adhoc",
                   profile_version="v1", cancel_fraction=0.0, cancel_after=None):
    """Deterministic arrival schedule. Pure function of its arguments: same seed ->
    same clips, prompts, forms, tenants, item keys and arrival times, whatever the
    server does. `--resume` depends on that: a re-derived schedule must produce the
    same Idempotency-Key and the same payload for every item."""
    rng = random.Random(seed)
    order = list(range(len(clips)))
    rng.shuffle(order)
    out, seen, occurrences, t = [], set(), {}, 0.0
    for i in range(n):
        form = forms[i % len(forms)]
        clip = clips[order[i % len(order)]] if clips else None
        if rate:
            # A burst is `burst` arrivals at one instant; the gap before each burst is drawn
            # at rate/burst, so the MEAN arrival rate is the one that was asked for and only
            # its shape changed. burst=1 is the plain Poisson stream, unchanged.
            if i % burst == 0:
                t += rng.expovariate(rate / burst)
        cold = None
        if clip is not None and form != "text":
            cold = clip["id"] not in seen
            seen.add(clip["id"])
        source_id = clip["id"] if clip else (video_label(video) if video else "adhoc")
        # segment_index distinguishes the repeats of one clip inside a sweep: reusing a key
        # for the second scheduled copy would replay the first answer and measure nothing.
        # Keyed by the source alone, not by (source, form): the form changes the PAYLOAD but
        # not the key, so two forms of one clip sharing a segment index would be a
        # 409 idempotency_conflict — a client bug, per §3.6.
        segment = occurrences.get(source_id, 0)
        occurrences[source_id] = segment + 1
        duration = (clip or {}).get("duration_s")
        # episode_id carries the content digest §3.1 recommends, so re-cutting a clip
        # changes the key instead of replaying the old clip's answer under a new payload.
        episode = ((clip or {}).get("sha256") or "nomedia")[:16]
        prompt_version = "override" if prompt else ((clip or {}).get("prompt_id") or "p-none")
        key = item_key(dataset_version, source_id, episode, segment, 0,
                       duration if duration is not None else 0, prompt_version, profile_version)
        out.append({"seq": i, "arrival_s": round(t, 6), "form": form, "cold": cold,
                    "clip_id": source_id if (clip or video) else None,
                    "clip": clip, "prompt": (prompt or (clip or {}).get("prompt") or ""),
                    "prompt_kind": (clip or {}).get("prompt_kind") if not prompt else "override",
                    "duration_s": duration,
                    "max_tokens": max_tokens_mix[i % len(max_tokens_mix)],
                    "tenant": i % max(tenants, 1),
                    "segment_index": segment, "item_key": key,
                    "idempotency_key": IDEMPOTENCY_PREFIX + key,
                    "upload_handle": None,
                    # Short-circuited on purpose: with the knob off the rng stream is
                    # untouched, so every seed keeps the arrival times it always had.
                    "cancel_at_s": (cancel_after if cancel_fraction
                                    and rng.random() < cancel_fraction else None)})
    return out


# ---------------------------------------------------------------- resume (pure)


def run_fingerprint(cfg):
    """The identity of a run, written as the raw file's FIRST line.

    These are exactly the knobs that decide an item's key and its payload, so a `--resume`
    against a raw file written under different ones would re-send items under keys that do
    not match their payloads — 409 idempotency_conflict, i.e. a client bug (§3.6). The
    fingerprint makes that a refusal instead.
    """
    a = cfg["args"]
    return {"kind": "run_profile", "dataset_version": cfg["dataset_version"],
            "profile_version": a.profile_version, "seed": a.seed, "forms": cfg["forms"],
            "max_tokens_mix": list(a.max_tokens_mix), "tenants": cfg["tenants"],
            "requests": a.requests}


FINGERPRINT_FIELDS = ("dataset_version", "profile_version", "seed", "forms", "max_tokens_mix")


def read_fingerprint(path):
    """The run_profile line of a previous raw file, or None for a file written before it."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                return None
            if isinstance(row, dict) and row.get("kind") == "run_profile":
                return row
            return None            # the first line is already an attempt: an older file
    return None


def check_resume_profile(a, cfg, previous):
    """Refuse a resume whose identity knobs differ from the run being resumed."""
    if previous is None:
        print("note: the resumed raw file carries no run_profile line; its identity cannot "
              "be checked (written before this client recorded one)", file=sys.stderr)
        return
    current = run_fingerprint(cfg)
    differing = [name for name in FINGERPRINT_FIELDS if previous.get(name) != current[name]]
    if differing:
        sys.exit("refusing to resume: " + "; ".join(
            f"{name} was {previous.get(name)!r}, now {current[name]!r}" for name in differing)
            + ". These decide every item key and payload, so resuming across them would "
              "re-send items under keys that no longer match their payloads (409).")


def read_attempts(path):
    """The attempt rows of a previous raw file; resource samples and junk lines are skipped."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue                        # a half-written final line after a crash
            if isinstance(row, dict) and row.get("kind") is None and row.get("item_key"):
                rows.append(row)
    return rows


def is_terminal(row):
    """marlin-sop.md §3.5/§3.6: an item is done when it was accepted or quarantined, or its
    key answers that the interruption cancelled its job (CANCELLED_REPLAY).

    A failure, a 429, a 402 and a cancelled request are all NOT terminal, so a resume
    re-sends them — with the same key, which is what makes the re-send safe."""
    if row.get("outcome") in ("accepted", CANCELLED_REPLAY):
        return True
    return row.get("outcome") == "rejected" and row.get("http_status") in TERMINAL_REJECT_STATUS


def forget_cold(schedule):
    """A resumed run cannot say which clips are cold.

    `cold` is the FIRST occurrence of a clip in the schedule, and the interrupted run
    already sent some of them to the same target, so its caches are warm in a way this run
    cannot observe. Every resumed item is therefore `cold = None` (unknown) rather than
    carrying a flag computed before the filtering — a cold/warm split out of a resumed run
    would be a claim about a cache state nobody measured.
    """
    for item in schedule:
        item["cold"] = None
    return schedule


def apply_resume(schedule, previous):
    """Fold a previous run's rows into this schedule: (remaining items, skipped count).

    Only the LAST attempt of an item decides it, and an item the previous file never
    reached is simply still due. The upload handle is carried over so the resumed payload
    is byte-identical to the interrupted one (§3.3); the key is already identical because
    build_schedule is a pure function of the seed."""
    last = {}
    for row in previous:
        last[row["item_key"]] = row
    remaining, skipped = [], 0
    for item in schedule:
        row = last.get(item["item_key"])
        if row is not None and is_terminal(row):
            skipped += 1
            continue
        if row is not None and row.get("upload_handle"):
            item["upload_handle"] = row["upload_handle"]
        remaining.append(item)
    return remaining, skipped


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
        # A resumed item re-uses the handle the interrupted run staged (§3.3: the object is
        # staged once and a retry re-uses it). Staging again would change the payload under
        # the same Idempotency-Key, which is a 409, not a retry.
        handle = item.get("upload_handle")
        if handle is None:
            t = CLOCK()
            handle = await upload(client, cfg, path, row, item["tenant"])
            row["upload_s"] = round(CLOCK() - t, 4)
        row["upload_handle"] = handle
        return UPLOAD_REF_SCHEME + handle
    raise ValueError(f"unknown form {form}")


def read_and_digest(path):
    with open(path, "rb") as f:
        body = f.read()
    return body, sha256(body).hexdigest()


class UploadFailed(RuntimeError):
    """Carries no text: the status lands in row["upload_status"], the class in error_class.
    httpx's raise_for_status() message would quote the signed destination URL."""


async def upload(client, cfg, path, row, tenant=0):
    """POST /v1/uploads -> PUT to the returned constrained destination ->
    POST /v1/uploads/{handle}/complete. Contracts v1 shape; unverified until M3/G4."""
    body, digest = await asyncio.to_thread(read_and_digest, path)   # 35 MB read + sha off the loop
    mime = mimetypes.guess_type(path)[0] or "video/mp4"
    cfg = {**cfg, "headers": cfg["headers_for"](tenant)}   # the staging tenant owns the object
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
    # The handle goes back out in the request body and into the resume state, so it is
    # allowlisted like any other server string: `upl_` + 22..64 (contracts/ids.py).
    handle = allow((done.json() or {}).get("handle", created["handle"]), HANDLE_OK, cfg["key"],
                   fallback=None)
    if handle is None:
        raise UploadFailed()
    return handle


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
           # SOP dataset identity: ours, derived, and the same on every attempt of this item.
           "item_key": item["item_key"], "idempotency_key": item["idempotency_key"],
           "segment_index": item["segment_index"], "tenant": item["tenant"],
           "max_tokens": item["max_tokens"], "idempotency_replayed": None,
           "upload_handle": item.get("upload_handle"), "cancel_at_s": item.get("cancel_at_s"),
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
    now = lambda: round(CLOCK() - t0, 6)
    try:
        await _send(client, cfg, item, row, now)
    except Exception as e:                               # transport, file, upload or protocol failure
        # The exception TYPE, never its text: httpx quotes the full request URL and any
        # header it was given, and an OSError quotes the path it was handed.
        row["outcome"] = row["outcome"] or "failed"
        row["error_class"] = row["error_class"] or type(e).__name__
        row["end_s"] = row["end_s"] or now()
    if (row["outcome"], row["error_class"], row["error_code"], row["idempotency_replayed"]) == (
            "failed", "stream_error_event", "state_conflict", True):
        row["outcome"] = CANCELLED_REPLAY
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
    payload = {"model": cfg["model"], "messages": messages_for(item, ref),
               "max_tokens": item["max_tokens"], "temperature": 0, "stream": True}
    # vLLM streams usage only when asked. The infrx gateway always sends its usage frame, and
    # its parameter set is closed (01): `stream_options` there is 400 unsupported_parameter
    # (measured against the mounted gateway, E3B phase 3).
    if cfg["args"].target == "direct":
        payload["stream_options"] = {"include_usage": True}
    # A corpus clip knows its duration; the single-video path does not, and 'auto' there
    # means an ffprobe subprocess, so make_config() resolved it once instead of per attempt.
    mm = (resolve_mm_kwargs(cfg["args"], duration=item["duration_s"])
          if item["duration_s"] is not None else cfg["mm_fixed"])
    if mm:
        payload["mm_processor_kwargs"] = mm
    row["send_s"] = now()
    row["media_sent"] = ref is not None    # this clip's bytes/handle really went out
    headers = {**cfg["headers_for"](item["tenant"]),
               "idempotency-key": item["idempotency_key"]}
    async with client.stream("POST", cfg["base"] + "/chat/completions", json=payload,
                             headers=headers) as resp:
        row["first_byte_s"] = now()
        row["http_status"] = resp.status_code
        # Headers are server-controlled: allowlist or fixed literal, never verbatim.
        row["idempotency_replayed"] = resp.headers.get("idempotency-replayed") == "true"
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
        cancel_at = item.get("cancel_at_s")
        async for line in resp.aiter_lines():
            # A client disconnect, not a timeout: leaving the `stream` block closes the
            # connection. R21 makes client_cancelled billable with authoritative usage, so
            # it is its own outcome and never counted as an accepted or a failed request.
            if cancel_at is not None and now() - row["send_s"] >= cancel_at:
                row["outcome"], row["error_class"] = "cancelled", "client_cancelled"
                row["end_s"] = now()
                return
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
        await SLEEP(min(max(wait, 0.0), 30.0))


# ---------------------------------------------------------------- resource sampling


def _rss_kb():
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            return int(f.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
    except (OSError, ValueError, IndexError):       # not Linux, or /proc unavailable
        return None


def default_sampler():
    """Host CPU/memory only, stdlib only. GPU utilisation, GPU memory and device I/O need
    a device library that is not a dependency of this client and is not installed on the
    dev box, so they plug in via `--sampler module:callable` on the measurement host —
    the series are simply absent here rather than invented."""
    import resource as _resource

    def sample():
        usage = _resource.getrusage(_resource.RUSAGE_SELF)
        out = {"client_cpu_user_s": round(usage.ru_utime, 3),
               "client_cpu_sys_s": round(usage.ru_stime, 3),
               "client_read_blocks": usage.ru_inblock, "client_write_blocks": usage.ru_oublock}
        rss = _rss_kb()
        if rss is not None:
            out["client_rss_kb"] = rss              # current, not the ru_maxrss high-water mark
        try:
            out["load1"] = round(os.getloadavg()[0], 2)
        except OSError:
            pass
        return out
    return sample


async def sample_resources(cfg, t0, samples):
    """Sample on the injected clock, write each sample to the raw file as it is taken.

    Cancelled by the caller when the run ends; the final sample is taken then, so a soak
    always has a first and a last point to compare (the 'flat RSS' criterion of P-18)."""
    interval, sampler = cfg["sample_interval"], cfg["sampler"]
    try:
        while True:
            row = {"kind": "resource_sample", "t_s": round(CLOCK() - t0, 6), **sampler()}
            samples.append(row)
            write_row(cfg, row)
            await SLEEP(interval)
    except asyncio.CancelledError:
        row = {"kind": "resource_sample", "t_s": round(CLOCK() - t0, 6), "final": True, **sampler()}
        samples.append(row)
        write_row(cfg, row)
        raise


def resource_summary(samples):
    """first/last/min/max/growth per numeric series, so growth is visible without the raw file."""
    series = {}
    for row in samples:
        for name, value in row.items():
            if name in ("kind", "t_s", "final") or not isinstance(value, (int, float)) \
                    or isinstance(value, bool):
                continue
            series.setdefault(name, []).append(value)
    out = {"samples": len(samples), "series": {}}
    for name, values in sorted(series.items()):
        out["series"][name] = {"first": values[0], "last": values[-1], "min": min(values),
                               "max": max(values), "growth": round(values[-1] - values[0], 3)}
    return out


# ---------------------------------------------------------------- drivers


async def run_open_loop(client, cfg, schedule, rows):
    t0 = CLOCK()
    tasks = []
    for item in schedule:
        delay = item["arrival_s"] - (CLOCK() - t0)
        if delay > 0:
            await SLEEP(delay)                  # arrivals never wait for completions
        tasks.append(asyncio.create_task(run_one(client, cfg, item, t0, rows)))
    await asyncio.gather(*tasks)
    return CLOCK() - t0


async def run_closed_loop(client, cfg, schedule, rows):
    queue = asyncio.Queue()
    for item in schedule:
        queue.put_nowait(item)
    t0 = CLOCK()

    async def worker():
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await run_one(client, cfg, item, t0, rows)

    await asyncio.gather(*(worker() for _ in range(cfg["concurrency"])))
    return CLOCK() - t0


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


def percentile_block(rows, field, scale=1.0, get=None, label=None):
    """`get` reads a nested value (a Server-Timing phase); `field` names it in the
    suppression message, which is what a reader is told when a pN is refused."""
    get = get or (lambda r: r.get(field))
    vals = [get(r) * scale for r in rows if get(r) is not None]
    field = label or field
    block, suppressed = {"samples": len(vals)}, []
    for q in PCTS:
        v, n, why = percentile(vals, q)
        block[f"p{q}"] = v
        if why:
            suppressed.append(f"{field} {why}")
    return block, suppressed


def phase_blocks(accepted):
    """Per-phase percentiles in milliseconds, plus the declared phases nothing reported.

    E1B.b wants retrieval/decode/preparation, queue, prefill, decode, journal, persistence
    and settlement timed. They come from the gateway's Server-Timing header; a phase the
    server does not publish is listed as MISSING, never reconstructed from the wall clock.
    """
    seen = sorted({name for r in accepted for name in (r.get("server_timing") or {})
                   if name != "dropped_metrics"})
    blocks = {}
    for name in seen:
        block, _ = percentile_block(accepted, f"phase {name} (ms)",
                                    get=lambda r, n=name: (r.get("server_timing") or {}).get(n))
        blocks[name] = block
    return {"unit": "ms", "source": "Server-Timing response header",
            "observed": blocks, "declared_missing": [p for p in PHASES if p not in seen],
            "undeclared_observed": [n for n in seen if n not in PHASES]}


def profile_block(cfg):
    """The declared workload profile: every axis a throughput or latency number has to be
    quoted with (PERF-ENVELOPE; marlin-sop.md §5.2 "Representative workload").

    Declared from the SCHEDULE, not from what came back: this block says what the run set
    out to send, which is the thing a later run has to match to be comparable."""
    def spread(values):
        clean = sorted({v for v in values if v is not None})
        return {"distinct": len(clean), "min": clean[0] if clean else None,
                "max": clean[-1] if clean else None}
    a = cfg["args"]
    clips = {item["clip"]["id"]: item["clip"] for item in cfg.get("schedule") or []
             if item.get("clip")}.values()
    durations = [c.get("duration_s") for c in clips]
    return {
        "dataset_version": cfg["dataset_version"], "profile_version": a.profile_version,
        "seed": a.seed, "forms": cfg["forms"], "tenants": cfg["tenants"],
        "engine_state": a.engine_state,
        "max_tokens_mix": list(a.max_tokens_mix),
        "arrival": {"mode": "open-loop" if a.rate else "closed-loop", "rate_per_s": a.rate,
                    "burst": a.burst, "concurrency": None if a.rate else cfg["concurrency"]},
        "clips_scheduled": len(clips),
        "clip_duration_s": spread(durations),
        "clip_frames_profile_v1": spread([profile_frames(d) for d in durations]),
        "clip_resolutions": sorted({f"{c.get('width')}x{c.get('height')}" for c in clips
                                    if c.get("width")}),
        "clip_codecs": sorted({c.get("codec") for c in clips if c.get("codec")}),
        "clip_source_fps": spread([c.get("fps") for c in clips]),
        "cancellation": {"fraction": a.cancel_fraction, "after_s": a.cancel_after},
    }


def summarize(rows, wall, cfg):
    finals = {}
    for r in rows:                                        # last attempt per request decides its outcome
        finals[r["seq"]] = r
    finals = list(finals.values())
    accepted = [r for r in finals if r["outcome"] == "accepted"]
    rejected = [r for r in finals if r["outcome"] == "rejected"]
    failed = [r for r in finals if r["outcome"] == "failed"]
    cancelled = [r for r in finals if r["outcome"] == "cancelled"]
    cancelled_replays = [r for r in finals if r["outcome"] == CANCELLED_REPLAY]
    out_tokens = sum(r["completion_tokens"] or 0 for r in accepted)
    # The throughput unit P-18 requires: successful VIDEO-SECONDS per second, never clips
    # per second on its own (a clips/s number without the duration mix means nothing).
    video_seconds = sum(r["duration_s"] or 0 for r in accepted if r["media_sent"])
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
        "profile": profile_block(cfg),
        "accepted": len(accepted), "rejected": len(rejected), "failed": len(failed),
        "cancelled": len(cancelled), CANCELLED_REPLAY: len(cancelled_replays),
        "accepted_without_usage": sum(1 for r in accepted if r["usage_missing"]),
        # Retries collapse a request to its final attempt, so every rejected or failed
        # ATTEMPT is reported too: a 429 that a retry papered over stays visible.
        "denominators": {"latency_samples": len(accepted), "rejected_excluded": len(rejected),
                         "failed_excluded": len(failed), "cancelled_excluded": len(cancelled),
                         "cancelled_replay_excluded": len(cancelled_replays),
                         "scheduled": len(finals),
                         "skipped_terminal_on_resume": cfg.get("skipped_terminal", 0),
                         "attempts": len(rows),
                         "rejected_attempts": sum(1 for r in rows if r["outcome"] == "rejected"),
                         "failed_attempts": sum(1 for r in rows if r["outcome"] == "failed"),
                         # Attributed, so the provisional <1 % platform criterion is not fed
                         # by failures this client cannot blame on the platform.
                         "platform_failed_attempts": sum(1 for r in rows if is_platform_failure(r)),
                         "unattributed_failed_attempts": sum(
                             1 for r in rows if r["outcome"] == "failed" and not is_platform_failure(r)),
                         "cancelled_attempts": sum(1 for r in rows if r["outcome"] == "cancelled"),
                         "retried_requests": sum(1 for r in finals if r.get("retries"))},
        # MARLIN-SOP: one accepted item per distinct key, and a replay is not a second item.
        "idempotency": {"distinct_item_keys": len({r["item_key"] for r in rows if r.get("item_key")}),
                        "accepted_distinct_keys": len({r["item_key"] for r in accepted
                                                       if r.get("item_key")}),
                        "replayed": sum(1 for r in finals if r.get("idempotency_replayed")),
                        "conflicts": sum(1 for r in rows if r.get("error_code") ==
                                         "idempotency_conflict"),
                        "resumed_from": cfg.get("resumed_from")},
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
        # Successful work, in the unit P-18 asks for, always beside its profile block.
        "video_seconds_accepted": round(video_seconds, 3),
        "video_s_per_s": round(video_seconds / wall, 3) if wall else None,
        "phases": phase_blocks(accepted),
        "resources": resource_summary(cfg.get("samples") or []),
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


def tenant_keys(a):
    """One API key per declared tenant, read from the NAMED environment variables only.

    Mixed tenants are a profile axis (PERF-ENVELOPE) and also the only way to show that the
    idempotency scope is org + operation + key: two tenants may use identical item keys and
    must both be accepted. A name that is unset is refused here rather than silently
    collapsing the run onto one tenant."""
    keys = []
    for name in a.tenant_env:
        value = os.environ.get(name, "").strip()
        if not value:
            sys.exit(f"--tenant-keys names {name}, which is unset or empty in the environment")
        keys.append(value)
    return keys or [api_key()]


def make_config(a, clips=None, manifest=None):
    key = api_key()
    keys = tenant_keys(a)
    if a.target == "gateway" and not all(keys):
        sys.exit(f"--target gateway needs {KEY_ENV[0]} or {KEY_ENV[1]} in the environment")
    headers = {"content-type": "application/json", "accept": "text/event-stream"}
    if key:
        headers["authorization"] = f"Bearer {key}"

    def headers_for(tenant):
        value = keys[tenant % len(keys)] if keys else ""
        return {**headers, "authorization": f"Bearer {value}"} if value else dict(headers)

    forms = [f.strip() for f in a.forms.split(",") if f.strip()]
    if a.target == "gateway" and a.mm_kwargs and a.mm_kwargs != "":
        print("note: --target gateway does not send mm_processor_kwargs (server-side budget)",
              file=sys.stderr)
    mm_fixed = resolve_mm_kwargs(a, video=a.video) if not a.corpus else None   # probes once
    return {"args": a, "key": tuple(dict.fromkeys([key, *keys])), "headers": headers,
            "headers_for": headers_for, "tenants": len(keys),
            "base": a.base_url.rstrip("/"),
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
    cfg["dataset_version"] = a.dataset_version or (manifest or {}).get("corpus_version") or "adhoc"
    prompt = a.prompt
    if not a.corpus and prompt is None:
        prompt = smoke_namespace()["canonical_prompt"](a.weights, "caption")
    make = functools.partial(build_schedule, clips=clips, forms=cfg["forms"], prompt=prompt,
                             seed=a.seed, video=a.video, burst=a.burst,
                             max_tokens_mix=a.max_tokens_mix, tenants=cfg["tenants"],
                             dataset_version=cfg["dataset_version"],
                             profile_version=a.profile_version,
                             cancel_fraction=a.cancel_fraction, cancel_after=a.cancel_after)
    schedule = make(a.requests, rate=a.rate)
    cfg["schedule"] = schedule              # the DECLARED profile, before any resume filtering
    if a.resume:
        # The keys and payloads are re-derived, not remembered: only the outcomes come from
        # the previous file. A key that is a function of the attempt would defeat this.
        check_resume_profile(a, cfg, read_fingerprint(a.resume))
        schedule, cfg["skipped_terminal"] = apply_resume(schedule, read_attempts(a.resume))
        forget_cold(schedule)
        cfg["resumed_from"] = os.path.basename(a.resume)
    write_row(cfg, run_fingerprint(cfg))     # the raw file's first line, before any attempt
    rows = state["rows"]
    # asyncio's default handler prints the exception MESSAGE of an unretrieved task.
    asyncio.get_running_loop().set_exception_handler(
        lambda loop, ctx: print(f"loop: {type(ctx.get('exception')).__name__} "
                                f"({len(ctx)} context keys withheld)", file=sys.stderr))
    transport = load_transport(a.dry_run_transport) if a.dry_run_transport else None
    cfg["sampler"] = load_transport(a.sampler) if a.sampler else default_sampler()
    cfg["sample_interval"] = a.sample_interval
    cfg["samples"] = state["samples"] = []
    async with httpx.AsyncClient(timeout=a.timeout, transport=transport) as client:
        if not a.rate and not a.corpus and not a.no_warmup:
            # Its own dataset identity: sharing item 0's key would make the first MEASURED
            # request an idempotent replay of the warm-up instead of a request.
            warm = make(1, rate=None, dataset_version=cfg["dataset_version"] + ".warmup")
            await run_one(client, cfg, warm[0], CLOCK(), [])   # warm-up, not counted
        state["t0"] = CLOCK()
        sampler_task = (asyncio.create_task(sample_resources(cfg, state["t0"], cfg["samples"]))
                        if a.sample_interval > 0 else None)
        try:
            state["wall"] = await (run_open_loop(client, cfg, schedule, rows) if a.rate
                                   else run_closed_loop(client, cfg, schedule, rows))
        finally:
            if sampler_task is not None:
                sampler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sampler_task


# ---------------------------------------------------------------- sweep report


def cell_warnings(s):
    """Everything that makes a cell's headline numbers unquotable, in one place.

    The E1B protocol's rule is that tuning may not move the goalposts, so a cell states
    what it cannot support instead of quietly reporting a smaller number."""
    out = list(s.get("suppressed_percentiles") or [])
    d = s.get("denominators") or {}
    attempts = d.get("attempts") or 0
    platform = d.get("platform_failed_attempts")
    if platform is None:
        out.append("platform-caused failure rate unavailable: this cell predates the "
                   "attributed denominators")
    elif attempts and platform / attempts > 0.01:
        out.append(f"platform-attributed failures {platform}/{attempts} exceed the provisional "
                   f"1 % criterion (P-18, provisional)")
    unattributed = d.get("unattributed_failed_attempts") or 0
    if unattributed:
        out.append(f"{unattributed}/{attempts} failed attempt(s) this client cannot attribute "
                   f"(transport, local file or upload): neither counted as platform-caused nor "
                   f"dismissed")
    if s.get("interrupted"):
        out.append("run was interrupted: partial")
    profile = s.get("profile") or {}
    if s.get("cold_requests") and profile.get("engine_state") != "restarted":
        out.append(f"cold/warm split reported with engine_state="
                   f"{profile.get('engine_state', 'unknown')!r}: a cold claim needs a restarted "
                   f"engine or clips the target has never seen")
    missing = (s.get("phases") or {}).get("declared_missing")
    if missing:
        out.append("phases not published by the target: " + ",".join(missing))
    if not s.get("accepted"):
        out.append("no accepted request: nothing in this row is a measurement")
    return out


def is_legacy(cell):
    """A summary written before this schema existed (the four 2026-09-19 L40S rows have no
    denominators, no profile block and no per-outcome counts).

    They are real measurements and must not be printed as "no accepted request", but they
    are also not cells of this protocol, so they are listed apart with what they do carry.
    """
    return "denominators" not in cell or "profile" not in cell


def report(path, stream=sys.stdout):
    """Read a summary JSONL (--out) and print the sweep table. Reads only; runs nothing."""
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    legacy = [c for c in rows if is_legacy(c)]
    cells = [c for c in rows if not is_legacy(c)]
    if not rows:
        print(f"no summaries in {path}", file=stream)
        return 1
    if not cells:
        print(f"# Marlin-2B sweep report — {os.path.basename(path)}\n", file=stream)
        print_legacy(legacy, stream)
        return 0
    fingerprints = {json.dumps({k: (c.get("profile") or {}).get(k) for k in
                                ("dataset_version", "profile_version", "seed", "forms",
                                 "max_tokens_mix", "tenants")}, sort_keys=True) for c in cells}
    print(f"# Marlin-2B sweep report — {os.path.basename(path)}", file=stream)
    print(f"\n{len(cells)} cell(s); {len(fingerprints)} distinct workload profile(s)", file=stream)
    if len(fingerprints) > 1:
        print("\n**Cells span more than one profile: the rows below are not comparable with "
              "each other.**", file=stream)
    print("\n| label | mode | rate/conc | acc | rej | fail | canc | video-s/s | req/s | "
          "TTFT p50 | TTFT p95 | latency p50 | latency p95 |", file=stream)
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|", file=stream)
    for c in cells:
        a = (c.get("profile") or {}).get("arrival") or {}
        load = a.get("rate_per_s") if c.get("mode") == "open-loop" else a.get("concurrency")
        pct = c.get("percentiles") or {}
        cell = lambda block, q: ("—" if (pct.get(block) or {}).get(f"p{q}") is None
                                 else (pct[block][f"p{q}"]))
        print(f"| {c.get('label') or '(none)'} | {c.get('mode')} | {load} | {c.get('accepted')} | "
              f"{c.get('rejected')} | {c.get('failed')} | {c.get('cancelled')} | "
              f"{c.get('video_s_per_s')} | {c.get('req_per_s')} | {cell('ttft_s', 50)} | "
              f"{cell('ttft_s', 95)} | {cell('latency_s', 50)} | {cell('latency_s', 95)} |",
              file=stream)
    print("\n`—` is a percentile the sample count cannot support; it is never replaced by a "
          "smaller quantile or by the maximum.", file=stream)
    print("\n## Per-cell limits\n", file=stream)
    for c in cells:
        warnings = cell_warnings(c)
        print(f"- **{c.get('label') or '(none)'}** ({c.get('ts')}): "
              + ("; ".join(warnings) if warnings else "no reported limit"), file=stream)
    print_legacy(legacy, stream)
    print("\nProvisional criteria (marlin-sop.md §5.2, P-18) are **provisional**: quoting a "
          "row of this report without its label promotes it to a target, which it is not.",
          file=stream)
    return 0


def print_legacy(legacy, stream):
    if not legacy:
        return
    print(f"\n## {len(legacy)} pre-E1B row(s), listed apart\n", file=stream)
    print("Written before this schema: no denominators, no profile block, no rejected/failed "
          "split, so they are NOT comparable with the cells above and cannot be read as an "
          "envelope. What they carry:\n", file=stream)
    for c in legacy:
        print(f"- **{c.get('label') or '(none)'}** ({c.get('ts')}): conc "
              f"{c.get('concurrency')}, {c.get('requests')} requests, TTFT p50 "
              f"{c.get('ttft_p50')} s, latency p50 {c.get('latency_p50')} s, "
              f"{c.get('req_per_s')} req/s — p50-grade, no tail", file=stream)


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
    if a.report:
        return report(a.report)
    refuse_key_in_args(a, tuple(dict.fromkeys([api_key(), *tenant_keys(a)])))
    raw = raw_path(a)                        # exit 2 before anything is opened or printed
    for path in (raw, a.out):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    state = {"rows": [], "cfg": None, "wall": None, "t0": None, "raw_file": None,
             "samples": [], "interrupted": False}
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
            (CLOCK() - state["t0"] if state["t0"] else 0.0)
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
        if state["interrupted"]:
            return 130
        # A resume whose items were all already terminal has nothing to send and is a
        # success, not the "no accepted request" failure an empty run is.
        return 0 if res["accepted"] or res["denominators"]["skipped_terminal_on_resume"] else 1


if __name__ == "__main__":
    CLI_PROCESS = True
    sys.exit(main())
