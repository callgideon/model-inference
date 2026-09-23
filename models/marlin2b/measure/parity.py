#!/usr/bin/env python3
"""W4 item 5: the parity client (MEDIA-PARITY; the output/usage-drift disqualifier).

Sends the predeclared parity set (measure/W4-protocol.md §4) to the engine one clip at a
time (c = 1): temperature 0, a fixed max_tokens, streamed, both EOS ids as stop_token_ids,
the clip's manifest prompt and profile v1's mm_processor_kwargs as the worker computes them.
One JSON line per clip: prompt/completion tokens, finish reason, the content's sha256 and
its caption events, and - when the engine refuses - the engine's own error text, so a
refusal is evidence rather than an error code. `decide.py` pairs a candidate's lines with
the baseline's on the same clip bytes (`sha256`).

    python3 parity.py --engine http://127.0.0.1:8000 --out parity.jsonl   # CORPUS_CACHE set

Stdlib only. A clip absent from the cache is a `missing` line, never skipped silently.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import pathlib
import sys
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from decide import EVENT, PX_PER_FRAME, frames  # noqa: E402  one frame rule, not two

MAX_TOKENS = 256
EOS = [248044, 248046]
PARITY_SET = (
    "c039-bbb1080p30-1080-square",
    "c024-bbb1080p30-512-square",
    "c012-bbb1080p30-1024x768-4x3",
    "c025-tos720p-2560x1080-ultrawide-rot180",
    "c038-sintel1080p-480x854-portrait",
    "c051-tos720p-360p-16x9",
    "sop09-120s-640x360",
    "sop10-120s-854x480-step_spans_segment_boundary",
    "sop11-120s-1280x720",
)
MANIFESTS = (HERE.parent / "corpus" / "manifest.json",
             HERE.parent / "corpus-synth" / "manifest.json")


def budget_kwargs(duration_s: float) -> dict:
    """Profile v1's mm_processor_kwargs, the worker's `budget_kwargs` (infrx/media/video.py)."""
    return {"fps": 2.0, "min_frames": 4, "max_frames": 240,
            "size": {"shortest_edge": 4096, "longest_edge": frames(duration_s) * PX_PER_FRAME}}


def clips(manifests=MANIFESTS) -> dict[str, dict]:
    found = {}
    for path in manifests:
        data = json.loads(path.read_text())
        prompts = {p["id"]: p["text"] for p in data["prompts"]}
        for clip in data["clips"]:
            found[clip["id"]] = {"file": clip["file"], "prompt": prompts[clip["prompt"]],
                                 **{k: clip["derived"][k] for k in ("duration_s", "width", "height")}}
    return found


def ask(engine: str, clip: dict, data: bytes, timeout: float = 600.0) -> dict:
    url = urllib.parse.urlsplit(engine)
    body = {"model": "marlin2b", "temperature": 0, "max_tokens": MAX_TOKENS, "stream": True,
            "stream_options": {"include_usage": True}, "stop_token_ids": EOS,
            "mm_processor_kwargs": budget_kwargs(clip["duration_s"]),
            "messages": [{"role": "user", "content": [
                {"type": "video_url", "video_url": {
                    "url": "data:video/mp4;base64," + base64.b64encode(data).decode()}},
                {"type": "text", "text": clip["prompt"]}]}]}
    conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
    started = time.monotonic()
    conn.request("POST", "/v1/chat/completions", json.dumps(body),
                 {"content-type": "application/json"})
    response = conn.getresponse()
    out = {"http_status": response.status, "outcome": "failed", "error_message": None,
           "prompt_tokens": None, "completion_tokens": None, "finish_reason": None}
    text, done = [], False
    if response.status != 200:
        out["error_message"] = response.read(2000).decode(errors="replace")[:300]
    for line in (response if response.status == 200 else ()):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload == b"[DONE]":
            done = True
            break
        event = json.loads(payload)
        if event.get("error") is not None:
            error = event["error"]
            out["error_message"] = str(error.get("message") if isinstance(error, dict)
                                       else error)[:300]
            break
        for choice in event.get("choices") or ():
            text.append((choice.get("delta") or {}).get("content") or "")
            out["finish_reason"] = choice.get("finish_reason") or out["finish_reason"]
        if event.get("usage"):
            out["prompt_tokens"] = event["usage"].get("prompt_tokens")
            out["completion_tokens"] = event["usage"].get("completion_tokens")
    conn.close()
    content = "".join(text)
    if done and out["error_message"] is None and out["finish_reason"] and out["prompt_tokens"]:
        out["outcome"] = "accepted"
    out.update(latency_s=round(time.monotonic() - started, 3),
               content_sha256=hashlib.sha256(content.encode()).hexdigest(),
               events=[list(span) for span in EVENT.findall(content)])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--engine", default=os.environ.get("ENGINE", "http://127.0.0.1:8000"))
    ap.add_argument("--cache", default=os.environ.get("CORPUS_CACHE"),
                    help="the corpus cache (CORPUS_CACHE): clips/ and sop-synth-v1/")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not a.cache:
        ap.error("--cache or CORPUS_CACHE is required")
    known = clips()
    with open(a.out, "a") as out:
        for clip_id in PARITY_SET:
            clip = known[clip_id]
            path = pathlib.Path(a.cache) / clip["file"]
            row = {"clip_id": clip_id, **{k: clip[k] for k in ("duration_s", "width", "height")},
                   "frames": frames(clip["duration_s"]), "max_tokens": MAX_TOKENS}
            if not path.is_file():
                row.update(outcome="missing", sha256=None)
            else:
                data = path.read_bytes()
                row["sha256"] = hashlib.sha256(data).hexdigest()
                try:
                    row.update(ask(a.engine, clip, data))
                except (OSError, http.client.HTTPException, ValueError) as failure:
                    # the type only: a transport error's text can quote the request
                    row.update(outcome="failed", error_message=type(failure).__name__)
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"parity clip={clip_id} outcome={row['outcome']} "
                  f"prompt_tokens={row.get('prompt_tokens')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
