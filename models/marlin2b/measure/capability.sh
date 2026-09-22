#!/usr/bin/env bash
# W3 measurement 2 of 3 - capability probes against the running Marlin engine.
#
# Answers, one `probe=<name> result=<...>` line each, for the request fields the worker's
# adapter (apps/infrx-api/infrx/worker/engine.py) sends and the ruling it relies on:
#   stop_token_ids   accepted with both EOS ids [248044, 248046] (R61 (3)); and HONOURED -
#                    a greedy run's 3rd output token passed as the stop id must end the
#                    output there (finish_reason stop, stop_reason = that id)
#   cache_salt       accepted, and KNOWN to the engine: a wrong type is refused (400) and
#                    the engine log does not list it as "present in the request but ignored"
#   mm_uuids         the same two checks, top-level (what the adapter sends) and per part
#   file://          a clip under the allowed path is served; a path outside it, and a
#                    `..` escape, are refused (R61 (2); needs --allowed-local-media-path)
#   cancellation     a client that disconnects mid-stream stops the generation: running
#                    requests return to 0 and the log records the abort
#
# Inference requests only: no restart, no configuration change, no file written.
#   ENGINE=http://127.0.0.1:8000 CONTAINER=marlin2b-8000 \
#   MEDIA_ROOT=/opt/dlami/nvme/processing CLIP=/opt/dlami/nvme/processing/w3-probe/clip.mp4 \
#     bash capability.sh
# CLIP must be a short mp4 (a few seconds); under MEDIA_ROOT for the file:// probe. Without
# MEDIA_ROOT/CLIP the media probes report `not_run` with the reason.
set -uo pipefail
export ENGINE=${ENGINE:-http://127.0.0.1:8000} CONTAINER=${CONTAINER:-marlin2b-8000}
export MODEL=${MODEL:-marlin2b} MEDIA_ROOT=${MEDIA_ROOT:-} CLIP=${CLIP:-}
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) engine=$ENGINE model=$MODEL"
exec python3 - <<'PY'
import base64, datetime, http.client, json, os, subprocess, time, urllib.parse

ENGINE, MODEL = os.environ["ENGINE"], os.environ["MODEL"]
CONTAINER, ROOT, CLIP = os.environ["CONTAINER"], os.environ["MEDIA_ROOT"], os.environ["CLIP"]
EOS = [248044, 248046]
url = urllib.parse.urlsplit(ENGINE)


def call(body, timeout=120):
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
    conn.request("POST", "/v1/chat/completions", json.dumps(body),
                 {"content-type": "application/json"})
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, {"raw": raw[:300].decode(errors="replace")}


def text(prompt, **extra):
    return {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 32, **extra}


def video(url_, **extra):
    part = {"type": "video_url", "video_url": {"url": url_}}
    part.update(extra.pop("part", {}))
    return {"model": MODEL, "max_tokens": 16, "temperature": 0,
            "mm_processor_kwargs": {"fps": 2.0, "min_frames": 4, "max_frames": 8,
                                    "size": {"shortest_edge": 4096, "longest_edge": 8 * 200704}},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Describe this clip in one sentence."}, part]}],
            **extra}


def ignored_since(started):
    """The engine's own list of request fields it accepted and did not use."""
    done = subprocess.run(["docker", "logs", "--since", started, CONTAINER],
                          capture_output=True, text=True)
    return [line.split("ignored:", 1)[1].strip()[:200]
            for line in (done.stdout + done.stderr).splitlines() if "but ignored" in line]


def since():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def report(name, result, **facts):
    print(f"probe={name} result={result} " + " ".join(f"{k}={json.dumps(v)}" for k, v in facts.items()),
          flush=True)


def choice(answer):
    first = (answer.get("choices") or [{}])[0]
    return first.get("finish_reason"), first.get("stop_reason"), (answer.get("usage") or {})


# --- stop_token_ids -------------------------------------------------------------------
status, answer = call(text("Say hello.", stop_token_ids=EOS, max_tokens=256))
finish, stop_reason, usage = choice(answer)
report("stop_token_ids_accepted", "pass" if status == 200 and finish == "stop" else "fail",
       status=status, finish_reason=finish, stop_reason=stop_reason,
       completion_tokens=usage.get("completion_tokens"))

status, answer = call(text("Count from one to twenty in words.", max_tokens=8, logprobs=True,
                           return_tokens_as_token_ids=True))
tokens = [entry.get("token", "") for entry in
          (((answer.get("choices") or [{}])[0].get("logprobs") or {}).get("content") or [])]
ids = [int(t.split(":", 1)[1]) for t in tokens if t.startswith("token_id:")]
if status != 200 or len(ids) < 3:
    report("stop_token_ids_honoured", "inconclusive", status=status, token_ids=ids[:8])
else:
    target = ids[2]
    status, answer = call(text("Count from one to twenty in words.", max_tokens=8,
                               stop_token_ids=[target]))
    finish, stop_reason, usage = choice(answer)
    honoured = status == 200 and finish == "stop" and stop_reason == target \
        and (usage.get("completion_tokens") or 99) <= 3
    report("stop_token_ids_honoured", "pass" if honoured else "fail", status=status,
           greedy_ids=ids[:8], stop_on=target, finish_reason=finish, stop_reason=stop_reason,
           completion_tokens=usage.get("completion_tokens"))

# --- cache_salt ------------------------------------------------------------------------
started = since()
time.sleep(1.1)
status_ok, _ = call(text("Say hello.", cache_salt="w3-capability-probe"))
status_bad, bad = call(text("Say hello.", cache_salt=12345))
time.sleep(1.0)
unused = [line for line in ignored_since(started) if "cache_salt" in line]
report("cache_salt", "known" if status_ok == 200 and status_bad == 400 and not unused
       else "accepted_but_ignored" if status_ok == 200 else "refused",
       status=status_ok, wrong_type_status=status_bad, ignored_log=unused,
       wrong_type_error=str((bad.get("error") or {}).get("message", ""))[:160])

# --- media: a clip to send ----------------------------------------------------------------
if not CLIP or not os.path.isfile(CLIP):
    for name in ("mm_uuids_top_level", "mm_uuids_per_part", "file_url_under_allowed_path",
                 "file_url_outside_refused"):
        report(name, "not_run", reason="CLIP unset or not a file")
else:
    data_url = "data:video/mp4;base64," + base64.b64encode(open(CLIP, "rb").read()).decode()
    inside = ROOT and os.path.realpath(CLIP).startswith(os.path.realpath(ROOT) + os.sep)
    media = f"file://{os.path.realpath(CLIP)}" if inside else data_url
    for name, good, wrong in (
            ("mm_uuids_top_level", {"mm_uuids": ["w3-probe-0001"]}, {"mm_uuids": [12345]}),
            ("mm_uuids_per_part", {"part": {"uuid": "w3-probe-0002"}}, {"part": {"uuid": 12345}})):
        started = since()
        time.sleep(1.1)
        status_ok, answer = call(video(media, **good))
        status_bad, _ = call(video(media, **wrong))
        time.sleep(1.0)
        unused = [line for line in ignored_since(started) if "uuid" in line]
        report(name, "known" if status_ok == 200 and status_bad == 400 and not unused
               else "accepted_but_ignored" if status_ok == 200 else "refused",
               status=status_ok, wrong_type_status=status_bad, ignored_log=unused,
               media="file" if inside else "data_url",
               error=str((answer.get("error") or {}).get("message", ""))[:160])
    if not inside:
        for name in ("file_url_under_allowed_path", "file_url_outside_refused"):
            report(name, "not_run", reason="MEDIA_ROOT unset or CLIP not under it")
    else:
        status, answer = call(video(media))
        finish, _, usage = choice(answer)
        report("file_url_under_allowed_path", "pass" if status == 200 and finish else "fail",
               status=status, finish_reason=finish, prompt_tokens=usage.get("prompt_tokens"),
               error=str((answer.get("error") or {}).get("message", ""))[:160])
        outcomes = {}
        for label, target in (("outside", "file:///etc/hostname"),
                              ("dotdot", f"file://{os.path.realpath(ROOT)}/../../etc/hostname")):
            outcomes[label] = call(video(target))[0]
        report("file_url_outside_refused",
               "pass" if all(400 <= s < 500 for s in outcomes.values()) else "fail", **outcomes)

# --- cancellation ------------------------------------------------------------------------
def running():
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=10)
    conn.request("GET", "/metrics")
    body = conn.getresponse().read().decode(errors="replace")
    conn.close()
    values = [float(line.rsplit(" ", 1)[1]) for line in body.splitlines()
              if line.startswith("vllm:num_requests_running")]
    return sum(values) if values else None


started = since()
time.sleep(1.1)
conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=60)
conn.request("POST", "/v1/chat/completions", json.dumps(text(
    "Write a very long story about a lighthouse keeper.", max_tokens=2048, stream=True,
    temperature=0.7)), {"content-type": "application/json"})
response = conn.getresponse()
chunks = 0
while chunks < 5:
    line = response.fp.readline()
    if not line:
        break
    chunks += line.startswith(b"data:")
during = running()
conn.sock.close()                     # the client goes away mid-stream
conn.close()
t0, after = time.monotonic(), None
while time.monotonic() - t0 < 10:
    after = running()
    if after == 0:
        break
    time.sleep(0.1)
elapsed = round(time.monotonic() - t0, 2)
time.sleep(1.0)
logs = subprocess.run(["docker", "logs", "--since", started, CONTAINER],
                      capture_output=True, text=True)
aborted = [l for l in (logs.stdout + logs.stderr).splitlines() if "bort" in l][:3]
# A 2,048-token answer takes >10 s at the measured 6-8 ms/token, so reaching zero within
# 2 s of the disconnect is the abort, not the answer finishing.
report("cancellation", "pass" if after == 0 and elapsed <= 2.0 and chunks == 5 else "fail",
       chunks_read=chunks, running_during=during, running_after=after,
       seconds_to_zero=elapsed, abort_log=[a[-160:] for a in aborted])
PY
