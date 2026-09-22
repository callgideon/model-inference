#!/usr/bin/env python3
"""Headless quickstart and per-item dataset recipe for the Marlin endpoint.

research/workloads/marlin-sop.md §3 made runnable. No App or Lab process is involved:
an operator provisions the key (`python -m infrx.operations.cli issue-key ...
--secret-file sweep.key`), the client reads it from the environment, and everything
else is the public API.

    export INFRX_API_KEY="$(cat sweep.key)"          # never on argv; refused there
    python client_example.py quickstart --base https://<host>/v1 \\
        --video https://example.com/clip.mp4                        # or --find "event"
    python client_example.py sweep --base https://<host>/v1 \\
        --manifest items.jsonl --state sweep-state.jsonl --concurrency 8

Served today: sync JSON `POST /v1/chat/completions` with the `http(s)` or `data:`
media form. **Specified, not served** until G3 and G4U/M3 mount their routes:
`--form upload` (the `infrx-upload:` handshake) and `--respond-async` (202 + job
polling). Against today's endpoint those answer 404 and are recorded as quarantined.

A manifest line is one item (§3.1-3.2): `dataset_version, source_id, episode_id,
segment_index, start_s, end_s, prompt, prompt_version, profile_version, video`, where
`video` is a local file (data form) or an http(s) URL of at most 120 s. The
`Idempotency-Key` is `sop1.<item_key>`, a pure function of those fields, so a resumed
item re-sends the same key and the same payload: the server replays the original
outcome instead of accepting a second, billable item.

Item identity, key handling, the upload handshake and the server-string allowlists
are E1B's client (`models/marlin2b/bench.py`), imported rather than copied.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "models" / "marlin2b"))
import bench  # noqa: E402

MODEL = "nemostation/marlin-2b@2026-09-01"
# The two prompts Marlin was trained on; anything else works but is off-distribution.
CAPTION_PROMPT = ("Provide a spatial description of this clip followed by time-ranged events.\n"
                  "For each event, give the time range as <start - end> and a short description.")
FIND_PROMPT = ('Identify the timestamps during which "{event}" takes place. '
               'Output the time range as "From <start> to <end>." (numbers in seconds).')
MAX_PER_KEY = 8                       # limits.py: 8 active jobs per key (marlin-sop §3.4)
MAX_TOKENS = 512
TERMINAL = frozenset({"done", "quarantined", "rerun_required"})
RETRYABLE = frozenset({429, 500, 502, 503, 504})
STOP = {401: "stopped_credential", 403: "stopped_credential", 402: "paused_wallet"}
MAX_RETRY_AFTER_S = 60.0
JOB_OK = re.compile(r"job_[A-Za-z0-9_-]{16,64}")
SLEEP = asyncio.sleep                 # a test replaces it; production keeps the real one


def load_items(path: str) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            it = json.loads(line)
            key = bench.item_key(it["dataset_version"], it["source_id"], it["episode_id"],
                                 it["segment_index"], it["start_s"], it["end_s"],
                                 it["prompt_version"], it["profile_version"])
            items.append({**it, "item_key": key,
                          "idempotency_key": bench.IDEMPOTENCY_PREFIX + key})
    if len({i["item_key"] for i in items}) != len(items):
        raise SystemExit("two manifest lines share an item identity; the key must name one item")
    return items


def load_state(path: str) -> dict[str, dict]:
    """The last row per item. Append-only on disk, so a crash loses at most one line."""
    last = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    last[row["item_key"]] = row
    return last


async def media_ref(client, cfg, item, row):
    form, video = cfg["form"], item["video"]
    if form == "url":
        if not bench.is_url(video):
            raise ValueError("the url form needs an http(s) video")
        return video
    if form == "data":
        return await asyncio.to_thread(bench.data_url, os.path.expanduser(video))
    handle = row.get("upload_handle") or await bench.upload(client, cfg, video, row)
    row["upload_handle"] = handle                                    # re-used on resume
    return bench.UPLOAD_REF_SCHEME + handle


def _error_code(resp, key):
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        err = None
    return bench.allow(err.get("code") if isinstance(err, dict) else None, bench.CODE_OK, key)


def _completion(row, body):
    usage = body.get("usage") if isinstance(body, dict) else None
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(usage, dict) or not choices:
        row["status"] = "failed"          # a 200 without usage or content is not a result
        return
    row["prompt_tokens"] = bench.as_int(usage.get("prompt_tokens"))
    row["completion_tokens"] = bench.as_int(usage.get("completion_tokens"))
    row["content"] = (choices[0].get("message") or {}).get("content")
    row["status"] = "done"


async def poll(client, cfg, row):
    """Explicit async (specified, not served): status until terminal, then the result."""
    headers = cfg["headers"]
    for _ in range(cfg["poll_limit"]):
        r = await client.get(f"{cfg['base']}/jobs/{row['job_handle']}", headers=headers)
        status = r.json() if r.status_code == 200 else {}
        if status.get("cause"):
            if not status.get("result_available"):
                row["status"] = "failed"
                row["error_code"] = bench.allow(status["cause"], bench.CODE_OK, cfg["key"])
                return
            res = await client.get(f"{cfg['base']}/jobs/{row['job_handle']}/result",
                                   headers=headers)
            if res.status_code == 410:
                row["status"] = "rerun_required"
                return
            _completion(row, (res.json() or {}).get("response"))
            return
        await SLEEP(cfg["poll_s"])
    # still running: stays "accepted"; a resume re-sends the same key and replays it


async def process(client, cfg, item, prior, record):
    """One item to an explicit outcome (§3.6). Retries reuse the key and the payload."""
    row = {"item_key": item["item_key"], "idempotency_key": item["idempotency_key"],
           "status": None, "http_status": None, "error_code": None, "inference_id": None,
           "replayed": None, "attempts": 0, "upload_handle": (prior or {}).get("upload_handle")}
    headers = dict(cfg["headers"])
    if item["idempotency_key"]:
        headers["idempotency-key"] = item["idempotency_key"]
    if cfg["respond_async"]:
        headers["prefer"] = "respond-async"
    for attempt in range(1, cfg["max_attempts"] + 1):
        row["attempts"] = attempt
        try:
            ref = await media_ref(client, cfg, item, row)
            body = {"model": cfg["model"], "max_tokens": cfg["max_tokens"], "temperature": 0,
                    "messages": bench.messages_for({"form": "video", "prompt": item["prompt"]},
                                                   ref)}
            resp = await client.post(cfg["base"] + "/chat/completions", json=body,
                                     headers=headers)
        except (httpx.TransportError, bench.UploadFailed) as e:
            row["status"], row["error_code"] = "retry_later", type(e).__name__
            await SLEEP(min(2 ** attempt, MAX_RETRY_AFTER_S))
            continue
        row["http_status"] = resp.status_code
        row["inference_id"] = bench.allow(resp.headers.get("inference-id"), bench.ID_OK,
                                          cfg["key"])
        row["replayed"] = resp.headers.get("idempotency-replayed") == "true"
        if resp.status_code == 200:
            _completion(row, resp.json())
            break
        if resp.status_code == 202:
            row["job_handle"] = bench.allow(resp.json().get("job_handle"), JOB_OK, cfg["key"],
                                            fallback=None)
            row["status"] = "accepted" if row["job_handle"] else "failed"
            record(dict(row))             # persist the handle before polling (§3.5 step 2)
            if row["job_handle"]:
                await poll(client, cfg, row)
            break
        row["error_code"] = _error_code(resp, cfg["key"])
        if resp.status_code in STOP:
            row["status"] = STOP[resp.status_code]
            cfg["stop"].set()             # do not burn the dataset against 401/402/403
            break
        if resp.status_code == 410:
            row["status"] = "rerun_required"
            break
        if resp.status_code in RETRYABLE:
            row["status"] = "retry_later"
            wait = bench.as_float(resp.headers.get("retry-after"))
            await SLEEP(min(wait if wait is not None else 2 ** attempt, MAX_RETRY_AFTER_S))
            continue
        row["status"] = "quarantined"     # 400/404/409/413/415/422: retrying cannot help
        break
    record(row)
    return row


async def sweep(cfg, items, state_path):
    prior = load_state(state_path)
    todo = [i for i in items if prior.get(i["item_key"], {}).get("status") not in TERMINAL]
    gate = asyncio.Semaphore(cfg["concurrency"])
    counts: dict[str, int] = {}

    def record(row):
        with open(state_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    async def one(client, item):
        async with gate:
            if cfg["stop"].is_set():
                return
            row = await process(client, cfg, item, prior.get(item["item_key"]), record)
            counts[row["status"]] = counts.get(row["status"], 0) + 1

    async with httpx.AsyncClient(timeout=cfg["timeout"], transport=cfg["transport"]) as client:
        await asyncio.gather(*(one(client, i) for i in todo))
    return {"items": len(items), "skipped_terminal": len(items) - len(todo),
            "not_sent": len(todo) - sum(counts.values()), "outcomes": counts}


def config(a, key, transport=None):
    if not 1 <= a.concurrency <= MAX_PER_KEY:
        raise SystemExit(f"--concurrency is 1..{MAX_PER_KEY} per key (marlin-sop §3.4)")
    headers = {"authorization": f"Bearer {key}"}
    return {"base": a.base.rstrip("/"), "key": key, "model": a.model, "form": a.form,
            "max_tokens": a.max_tokens, "concurrency": a.concurrency,
            "respond_async": a.respond_async, "max_attempts": a.max_attempts,
            "poll_s": 5.0, "poll_limit": 720, "timeout": 330.0, "transport": transport,
            "headers": headers, "headers_for": lambda _tenant: headers,  # bench.upload's shape
            "stop": asyncio.Event()}


def parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("quickstart", "sweep"):
        c = sub.add_parser(name)
        c.add_argument("--base", required=True, help="https://<host>/v1")
        c.add_argument("--model", default=MODEL)
        c.add_argument("--form", choices=("url", "data", "upload"),
                       default="url" if name == "quickstart" else "data")
        c.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
        c.add_argument("--concurrency", type=int, default=1 if name == "quickstart" else MAX_PER_KEY)
        c.add_argument("--max-attempts", type=int, default=5)
        c.add_argument("--respond-async", action="store_true",
                       help="specified, not served until G3 mounts /v1/jobs")
    q = sub.choices["quickstart"]
    q.add_argument("--video", required=True, help="http(s) URL, or a local file with --form data")
    q.add_argument("--prompt", default=CAPTION_PROMPT)
    q.add_argument("--find", metavar="EVENT", help="temporal grounding instead of captioning")
    s = sub.choices["sweep"]
    s.add_argument("--manifest", required=True)
    s.add_argument("--state", required=True)
    return p


def main(argv=None, transport=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    bench.refuse_embedded_key(argv)
    a = parser().parse_args(argv)
    key = bench.api_key()
    if not key:
        raise SystemExit(f"export {bench.KEY_ENV[1]} (or {bench.KEY_ENV[0]}); keys are never argv")
    bench.refuse_key_in_args(a, key)
    cfg = config(a, key, transport)
    if a.cmd == "quickstart":
        prompt = FIND_PROMPT.format(event=a.find) if a.find else a.prompt
        item = {"item_key": "quickstart", "idempotency_key": None, "video": a.video,
                "prompt": prompt}

        async def one():
            async with httpx.AsyncClient(timeout=cfg["timeout"], transport=transport) as client:
                return await process(client, cfg, item, None, lambda row: None)
        row = asyncio.run(one())
        print(json.dumps({k: row.get(k) for k in ("status", "http_status", "error_code",
                                                   "inference_id", "prompt_tokens",
                                                   "completion_tokens", "content")}, indent=2))
        return 0 if row["status"] == "done" else 1
    summary = asyncio.run(sweep(cfg, load_items(a.manifest), a.state))
    print(json.dumps(summary, sort_keys=True))
    return 0 if not summary["not_sent"] and set(summary["outcomes"]) <= TERMINAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
