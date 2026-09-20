#!/usr/bin/env python3
"""Test the Marlin-2B endpoint from your laptop.

The endpoint is OpenAI-compatible, so the only dependency is the `openai`
package (or nothing at all: see --raw, which uses urllib from the standard
library). Videos can be a public URL or a local file; local files are sent
inline as base64, which is also faster on the server because it skips the
download.

Setup (once):
    pip install openai
    export MARLIN_API_KEY=sk-marlin-...        # from AWS SSM /model-inference/marlin2b_api_key
    export MARLIN_BASE_URL=https://marlin2b.callbill.ai/v1   # default, override if the host changes

Examples:
    python client_example.py                                   # caption a public sample clip
    python client_example.py --video ~/clips/demo.mp4          # caption a local file
    python client_example.py --video demo.mp4 --find "a person opens the door"
    python client_example.py --video demo.mp4 --prompt "Custom question about the clip?"
    python client_example.py --no-stream                       # one JSON response instead of tokens as they arrive
    python client_example.py --raw                             # same request with only the standard library

Two prompts are what Marlin was trained on; anything else works but is off-distribution:
  caption: "Provide a spatial description of this clip followed by time-ranged events.
            For each event, give the time range as <start - end> and a short description."
  find:    'Identify the timestamps during which "<event>" takes place.
            Output the time range as "From <start> to <end>." (numbers in seconds).'

Limits enforced by the server: one video per request, ≤ 120 s, ≤ 64 MB
(mp4/webm/mov), max_tokens ≤ 2048. Responses carry an `Inference-Id` header
you can quote when reporting a problem.
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import time

CAPTION_PROMPT = (
    "Provide a spatial description of this clip followed by time-ranged events.\n"
    "For each event, give the time range as <start - end> and a short description."
)
FIND_PROMPT = (
    'Identify the timestamps during which "{event}" takes place. '
    'Output the time range as "From <start> to <end>." (numbers in seconds).'
)
SAMPLE_VIDEO = "https://download.samplelib.com/mp4/sample-10s.mp4"
MODEL = "nemostation/marlin-2b"


def video_part(video: str) -> dict:
    """Build the OpenAI-style content part: pass URLs through, inline local files."""
    if video.startswith(("http://", "https://")):
        url = video
    else:
        path = os.path.expanduser(video)
        mime = mimetypes.guess_type(path)[0] or "video/mp4"
        with open(path, "rb") as f:
            url = f"data:{mime};base64," + base64.b64encode(f.read()).decode()
    return {"type": "video_url", "video_url": {"url": url}}


def build_messages(video: str, prompt: str) -> list:
    return [{"role": "user", "content": [video_part(video), {"type": "text", "text": prompt}]}]


def run_openai(base_url, api_key, messages, max_tokens, stream):
    """Preferred path: the official openai client, streaming tokens as they arrive."""
    from openai import OpenAI  # pip install openai

    client = OpenAI(base_url=base_url, api_key=api_key)
    t0 = time.time()
    if not stream:
        r = client.chat.completions.create(model=MODEL, messages=messages, max_tokens=max_tokens, temperature=0)
        print(r.choices[0].message.content)
        return r.usage, time.time() - t0, None
    first = None
    usage = None
    for chunk in client.chat.completions.create(
        model=MODEL, messages=messages, max_tokens=max_tokens, temperature=0,
        stream=True, stream_options={"include_usage": True},
    ):
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta.content:
            first = first or time.time()
            print(chunk.choices[0].delta.content, end="", flush=True)
    print()
    return usage, time.time() - t0, (first - t0) if first else None


def run_raw(base_url, api_key, messages, max_tokens):
    """Standard-library only, non-streaming, so it works on any machine with Python 3."""
    import urllib.request

    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
        print(data["choices"][0]["message"]["content"])
        print(f"(Inference-Id: {resp.headers.get('Inference-Id')})", file=sys.stderr)
    return data.get("usage"), time.time() - t0, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", default=SAMPLE_VIDEO, help="public URL or local file (default: a 10 s sample clip)")
    ap.add_argument("--find", metavar="EVENT", help="temporal grounding instead of captioning")
    ap.add_argument("--prompt", help="custom prompt (overrides caption/find)")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--raw", action="store_true", help="use only the standard library (no openai package)")
    ap.add_argument("--base-url", default=os.environ.get("MARLIN_BASE_URL", "https://marlin2b.callbill.ai/v1"))
    ap.add_argument("--api-key", default=os.environ.get("MARLIN_API_KEY"))
    a = ap.parse_args()

    if not a.api_key:
        sys.exit("set MARLIN_API_KEY (or pass --api-key); the value lives in AWS SSM /model-inference/marlin2b_api_key")
    prompt = a.prompt or (FIND_PROMPT.format(event=a.find) if a.find else CAPTION_PROMPT)
    messages = build_messages(a.video, prompt)
    print(f"→ {a.base_url}  video={a.video}  mode={'custom' if a.prompt else 'find' if a.find else 'caption'}", file=sys.stderr)

    try:
        if a.raw:
            usage, wall, ttft = run_raw(a.base_url, a.api_key, messages, a.max_tokens)
        else:
            usage, wall, ttft = run_openai(a.base_url, a.api_key, messages, a.max_tokens, not a.no_stream)
    except ImportError:
        sys.exit("pip install openai   (or rerun with --raw)")
    except Exception as e:  # show the server's error body when there is one
        detail = getattr(e, "body", None) or getattr(e, "reason", None) or ""
        sys.exit(f"request failed: {type(e).__name__}: {e} {detail}")

    if usage:
        u = usage if isinstance(usage, dict) else usage.model_dump()
        print(f"\nprompt_tokens={u.get('prompt_tokens')} completion_tokens={u.get('completion_tokens')}"
              f" wall={wall:.2f}s" + (f" ttft={ttft:.2f}s" if ttft else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
