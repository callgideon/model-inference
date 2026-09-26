#!/usr/bin/env bash
# W5 item 4 - warmup, readiness and the cold-cache path, on the PINNED engine (the pilot box).
# For the coordinator, AFTER certification run3 has ended: it restarts the engine unit (the
# worker restarts with it, PartOf=). Scratch-style: exact commands, no secrets, nothing
# changed but that restart. The qualified configuration is untouched (serve.sh as installed,
# ENGINE_MAX_NUM_SEQS=8, the pinned vLLM image, no --max-num-batched-tokens change).
#
# Each fact on its own line, then one JSON summary line (`w5_warmup=`):
#   model_load_s        unit restart -> engine GET /health 200 (weights loaded, graphs captured)
#   worker_readyz_s     unit restart -> worker GET /readyz 200 (engine up + pool running; this
#                       is NOT end-to-end readiness and the script never reads it as that)
#   processor_warmup_s  OPERATOR warmup, engine-direct: POST /tokenize with WARM_CLIP at the
#                       pinned budget (the video processor's first use). No gateway, no key, no
#                       job, no wallet: it cannot consume anyone's grant.
#   engine_warmup_s     OPERATOR warmup, engine-direct: chat, max_tokens 1, the same clip.
#   first_request_s     the FIRST valid request after the restart through the gateway, with the
#                       OPERATOR's own test key (never a customer's), on bytes never seen before
#                       (FRESH_SOURCE plus a trailing `free` box of random bytes: a new digest,
#                       so a processing-cache miss and a TOKCOST memo miss - the cold path).
#   second_request_s    the same bytes under a new idempotency key: cache and memo hits.
# Checks (each `check=<name> result=pass|fail ...`): the worker claimed nothing during the
# warmup (`claimed` and `prepare_claimed` 0 in /readyz - run it in a quiet window), the fresh
# digest's cache file exists only after the first request, the worker logs `engine /tokenize`
# for the first request and `memo of engine /tokenize` for the second.
#
#   sudo -E env OPERATOR_KEY_FILE=/root/w5-operator.key \
#     WARM_CLIP=/opt/dlami/nvme/processing/w5-warmup/clip.mp4 \
#     FRESH_SOURCE=/home/ubuntu/model-inference/models/marlin2b/corpus/<short clip>.mp4 \
#     OUT=/home/ubuntu/w5-warmup-$(date -u +%Y%m%dT%H%MZ) \
#     bash research/plan/evidence/w/W5-box-warmup.sh
#   DRY_RUN=1 bash research/plan/evidence/w/W5-box-warmup.sh      # the plan + its self-check
#
# OPERATOR_KEY_FILE: a mode-600 file holding the operator's test API key (G8's headless issue),
# read by the Python below and never printed, logged or passed in argv. WARM_CLIP must lie
# under PROCESSING_CACHE_DIR (the engine's --allowed-local-media-path); it is operator media,
# never a customer's. FRESH_SOURCE: a short (<= 30 s) mp4 the gateway accepts inline.
set -euo pipefail
export ENGINE=${ENGINE:-http://127.0.0.1:8000} WORKER=${WORKER:-http://127.0.0.1:8002}
export GATEWAY=${GATEWAY:-http://127.0.0.1:8001} MODEL=${MODEL:-marlin2b}
export PUBLIC_MODEL=${PUBLIC_MODEL:-nemostation/marlin-2b} UNIT=${UNIT:-marlin2b-vllm}
export WORKER_CONTAINER=${WORKER_CONTAINER:-infrx-worker}
export CACHE_ROOT=${CACHE_ROOT:-/opt/dlami/nvme/processing} DRY_RUN=${DRY_RUN:-0}
export OPERATOR_KEY_FILE=${OPERATOR_KEY_FILE:-} WARM_CLIP=${WARM_CLIP:-}
export FRESH_SOURCE=${FRESH_SOURCE:-} OUT=${OUT:-/tmp/w5-warmup}
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) engine=$ENGINE worker=$WORKER gateway=$GATEWAY dry_run=$DRY_RUN"
exec python3 - <<'PY'
import base64, datetime, hashlib, http.client, json, os, subprocess, sys, time, urllib.parse, uuid

E = os.environ
ENGINE, WORKER, GATEWAY = E["ENGINE"], E["WORKER"], E["GATEWAY"]
DRY = E["DRY_RUN"] == "1"
# The pinned budget (infrx/media/video.py budget_kwargs; infrx/config.py): 2 fps, 4..240 frames,
# 200,704 px per frame, the frame count rounded up to even.
FPS, MIN_FRAMES, MAX_FRAMES, PX_PER_FRAME = 2.0, 4, 240, 200_704

# --- the plan: every request this script can make, and who it goes to ------------------------
WARMUP = ("processor_warmup", "engine_warmup")
PLAN = [
    {"step": "restart", "target": "systemd", "what": f"systemctl restart {E['UNIT']}"},
    {"step": "model_load", "target": ENGINE, "method": "GET", "path": "/health", "auth": False},
    {"step": "worker_readyz", "target": WORKER, "method": "GET", "path": "/readyz", "auth": False},
    {"step": "processor_warmup", "target": ENGINE, "method": "POST", "path": "/tokenize",
     "auth": False},
    {"step": "engine_warmup", "target": ENGINE, "method": "POST",
     "path": "/v1/chat/completions", "auth": False},
    {"step": "first_request", "target": GATEWAY, "method": "POST",
     "path": "/v1/chat/completions", "auth": True},
    {"step": "second_request", "target": GATEWAY, "method": "POST",
     "path": "/v1/chat/completions", "auth": True},
]
# Self-check: warmup is the operator's, engine-direct, keyless - it never reaches the gateway,
# so it cannot create a job or touch a wallet; only the two request steps carry the key.
for step in PLAN:
    if step["step"] in WARMUP:
        assert step["target"] == ENGINE and not step["auth"], step
    if step.get("auth"):
        assert step["target"] == GATEWAY and step["step"].endswith("_request"), step
if DRY:
    print(json.dumps({"w5_warmup_plan": PLAN}, indent=1))
    sys.exit(0)

missing = [name for name in ("OPERATOR_KEY_FILE", "WARM_CLIP", "FRESH_SOURCE") if not E[name]]
if missing:
    sys.exit(f"refusing: set {', '.join(missing)} (see the header)")
if not os.path.realpath(E["WARM_CLIP"]).startswith(os.path.realpath(E["CACHE_ROOT"]) + os.sep):
    sys.exit("refusing: WARM_CLIP must lie under CACHE_ROOT (the engine's allowed media path)")
os.makedirs(E["OUT"], exist_ok=True)
with open(E["OPERATOR_KEY_FILE"]) as handle:
    KEY = handle.read().strip()          # never printed


def call(base, method, path, body=None, *, auth=False, headers=(), timeout=600):
    url = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
    sent = {"content-type": "application/json", **dict(headers)}
    if auth:
        sent["authorization"] = f"Bearer {KEY}"
    began = time.monotonic()
    conn.request(method, path, None if body is None else json.dumps(body), sent)
    answer = conn.getresponse()
    raw = answer.read()
    took = time.monotonic() - began
    timing = answer.getheader("server-timing")
    conn.close()
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = {"raw": raw[:200].decode(errors="replace")}
    return answer.status, doc, took, timing


def until_ok(base, path, bound_s):
    began = time.monotonic()
    while time.monotonic() - began < bound_s:
        try:
            status, doc, _, _ = call(base, "GET", path, timeout=5)
            if status == 200:
                return time.monotonic() - began, doc
        except OSError:
            pass
        time.sleep(1)
    return None, None


def budget(seconds):
    frames = int(min(MAX_FRAMES, max(MIN_FRAMES, round(seconds * FPS))))
    frames += frames % 2
    return {"fps": FPS, "min_frames": MIN_FRAMES, "max_frames": MAX_FRAMES,
            "size": {"shortest_edge": 4096, "longest_edge": frames * PX_PER_FRAME}}


def probe_seconds(path):
    """The clip's duration as M's own probe measures it, inside the worker's container (it
    mounts the processing cache and carries `infrx`)."""
    out = subprocess.run(["docker", "exec", E["WORKER_CONTAINER"], "python", "-c",
                          "import sys; from infrx.media import probe; "
                          "print(probe.probe(open(sys.argv[1], 'rb').read()).duration_s)", path],
                         capture_output=True, text=True, timeout=60, check=True)
    return float(out.stdout.strip())


def cache_files(digest):
    return subprocess.run(["find", E["CACHE_ROOT"], "-path", f"*/{digest[:16]}/*", "-type", "f"],
                          capture_output=True, text=True).stdout.split()


def worker_log(since):
    logs = subprocess.run(["docker", "logs", "--since", since, E["WORKER_CONTAINER"]],
                          capture_output=True, text=True)
    return logs.stdout + logs.stderr


summary = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "checks": {}}


def check(name, ok, detail=""):
    summary["checks"][name] = bool(ok)
    print(f"check={name} result={'pass' if ok else 'fail'} {detail}".rstrip(), flush=True)


# --- identity (no environment values: they carry secrets) -------------------------------------
for name, argv in (("engine_image", ["docker", "inspect", "-f", "{{.Image}}", "marlin2b-8000"]),
                   ("engine_args", ["docker", "inspect", "-f", "{{json .Args}}", "marlin2b-8000"]),
                   ("worker_image", ["docker", "inspect", "-f", "{{.Image}}",
                                     E["WORKER_CONTAINER"]])):
    value = subprocess.run(argv, capture_output=True, text=True).stdout.strip()
    summary[name] = value
    print(f"{name}={value}", flush=True)

# --- 1. cold engine: model load, then the worker's readiness (not end to end) ----------------
restarted = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
subprocess.run(["systemctl", "restart", E["UNIT"]], check=True)
summary["model_load_s"], _ = until_ok(ENGINE, "/health", 900)
summary["worker_readyz_s"], ready = until_ok(WORKER, "/readyz", 900)
print(f"model_load_s={summary['model_load_s']} worker_readyz_s={summary['worker_readyz_s']}",
      flush=True)

# --- 2. operator warmup, engine-direct ---------------------------------------------------------
warm = E["WARM_CLIP"]
kwargs = budget(probe_seconds(warm))
messages = [{"role": "user", "content": [
    {"type": "text", "text": "Describe this clip."},
    {"type": "video_url", "video_url": {"url": "file://" + warm}}]}]
status, doc, took, _ = call(ENGINE, "POST", "/tokenize", {
    "model": E["MODEL"], "messages": messages, "add_generation_prompt": True,
    "mm_processor_kwargs": kwargs})
summary["processor_warmup_s"] = took
print(f"processor_warmup_s={took:.3f} status={status} count={doc.get('count')}", flush=True)
status, doc, took, _ = call(ENGINE, "POST", "/v1/chat/completions", {
    "model": E["MODEL"], "messages": messages, "max_tokens": 1, "temperature": 0,
    "mm_processor_kwargs": kwargs})
summary["engine_warmup_s"] = took
print(f"engine_warmup_s={took:.3f} status={status}", flush=True)
_, after_warmup, _, _ = call(WORKER, "GET", "/readyz", timeout=5)
check("warmup_claimed_nothing",
      after_warmup.get("claimed") == 0 and after_warmup.get("prepare_claimed") == 0,
      f"claimed={after_warmup.get('claimed')} prepare_claimed={after_warmup.get('prepare_claimed')}")

# --- 3. the first valid request on never-seen bytes (cold cache), then the same bytes ---------
source = open(E["FRESH_SOURCE"], "rb").read()
fresh = source + (24).to_bytes(4, "big") + b"free" + os.urandom(16)
digest = hashlib.sha256(fresh).hexdigest()
open(os.path.join(E["OUT"], "fresh.mp4"), "wb").write(fresh)
check("fresh_digest_not_cached", cache_files(digest) == [], f"digest16={digest[:16]}")
body = {"model": E["PUBLIC_MODEL"], "max_tokens": 64, "stream": False, "messages": [
    {"role": "user", "content": [
        {"type": "text", "text": "Describe this clip."},
        {"type": "video_url", "video_url": {
            "url": "data:video/mp4;base64," + base64.b64encode(fresh).decode()}}]}]}
for step in ("first_request", "second_request"):
    since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, doc, took, timing = call(GATEWAY, "POST", "/v1/chat/completions", body, auth=True,
                                     headers={"idempotency-key": f"w5-{uuid.uuid4()}"})
    summary[f"{step}_s"] = took
    summary[f"{step}_server_timing"] = timing
    summary[f"{step}_status"] = status
    print(f"{step}_s={took:.3f} status={status} server_timing={timing}", flush=True)
    logged = [line for line in worker_log(since).splitlines() if " prepared " in line]
    summary[f"{step}_prepared_log"] = logged[-1:] if logged else []
    wanted = "(engine /tokenize," if step == "first_request" else "(memo of engine /tokenize,"
    check(f"{step}_count_source", any(wanted in line for line in logged), wanted)
    if step == "first_request":
        check("cold_cache_filled", cache_files(digest) != [], f"digest16={digest[:16]}")

path = os.path.join(E["OUT"], "w5-warmup.json")
open(path, "w").write(json.dumps(summary, indent=1))
print("w5_warmup=" + json.dumps(summary), flush=True)
sys.exit(0 if all(summary["checks"].values()) else 1)
PY
