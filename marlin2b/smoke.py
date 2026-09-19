#!/usr/bin/env python3
"""Smoke-test the vLLM endpoint: one video, caption or find mode, streamed, timed.

    python marlin2b/smoke.py video.mp4                          # caption mode
    python marlin2b/smoke.py video.mp4 --find "a man walks in"  # find mode
    python marlin2b/smoke.py video.mp4 --prompt "custom text"

Prompts default to the canonical ones from the checkpoint's modeling_marlin.py
(the same text .caption()/.find() send), so outputs are comparable with
reference.py. Local files are sent inline as a base64 data URL; http(s) URLs
are passed through.
"""
import argparse, base64, json, mimetypes, os, re, sys, time

from openai import OpenAI

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000/v1"))
ap.add_argument("--model", default="marlin2b")
ap.add_argument("--weights", default=os.environ.get("WEIGHTS", "/opt/dlami/nvme/marlin2b"), help="dir holding modeling_marlin.py, for the canonical prompts")
ap.add_argument("--find", metavar="EVENT")
ap.add_argument("--prompt")
ap.add_argument("--max-tokens", type=int, default=2048)
# Marlin's training-time video budget (model card): 2 fps, 4-240 frames, 200,704 px/frame.
# vLLM's Qwen3.5 processor does not apply these by default; pass them per request.
ap.add_argument("--mm-kwargs", default=os.environ.get("MM_KWARGS", '{"fps": 2.0, "min_frames": 4, "max_frames": 240, "size": {"shortest_edge": 65536, "longest_edge": 200704}, "cap_pixels_per_frame": true}'),
                help="JSON for vLLM mm_processor_kwargs; '' to send none")
a = ap.parse_args()


def canonical_prompt(weights, mode, event=None):
    """Pull the prompt string the vendor helpers use out of modeling_marlin.py.
    Looks for a module-level constant whose name contains PROMPT and the mode
    name; falls back to the largest string literal in that method's body."""
    src = open(os.path.join(weights, "modeling_marlin.py"), encoding="utf-8").read()
    aliases = {"caption": ("CAPTION",), "find": ("FIND", "GROUNDING")}[mode]
    # values are plain string literals, possibly with escaped quotes; decode escapes
    for name, val in re.findall(r'^([A-Z_]*PROMPT[A-Z_]*)\s*=\s*("""[\s\S]*?"""|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\')', src, re.M):
        if any(k in name.upper() for k in aliases):
            text = val.strip('"\'').encode().decode("unicode_escape")
            return text.format(event=event) if event and "{" in text else text
    body = src[src.find(f"def {mode}(") :]
    body = body[: body.find("\n    def ")] if "\n    def " in body[1:] else body
    lits = re.findall(r'("""[\s\S]*?"""|"[^"\n]{20,}"|\'[^\'\n]{20,}\')', body)
    if not lits:
        sys.exit(f"could not find a {mode} prompt in modeling_marlin.py; pass --prompt")
    text = max(lits, key=len).strip('"\'')
    return text.format(event=event) if event and "{" in text else text


mode = "find" if a.find else "caption"
prompt = a.prompt or canonical_prompt(a.weights, mode, a.find)

if a.video.startswith(("http://", "https://")):
    url = a.video
else:
    mime = mimetypes.guess_type(a.video)[0] or "video/mp4"
    url = f"data:{mime};base64," + base64.b64encode(open(a.video, "rb").read()).decode()

client = OpenAI(base_url=a.base_url, api_key="none")
t0 = time.time()
first = None
out = []
stream = client.chat.completions.create(
    model=a.model,
    messages=[{"role": "user", "content": [{"type": "video_url", "video_url": {"url": url}}, {"type": "text", "text": prompt}]}],
    max_tokens=a.max_tokens,
    temperature=0,
    stream=True,
    stream_options={"include_usage": True},
    extra_body={"mm_processor_kwargs": json.loads(a.mm_kwargs)} if a.mm_kwargs else {},
)
usage = None
for chunk in stream:
    if chunk.usage:
        usage = chunk.usage
    if chunk.choices and chunk.choices[0].delta.content:
        if first is None:
            first = time.time()
        out.append(chunk.choices[0].delta.content)
dt = time.time() - t0
text = re.sub(r"^\s*<think>.*?(</think>|$)", "", "".join(out), count=1, flags=re.S).strip()

print(text)
n_out = usage.completion_tokens if usage else len(out)
print(json.dumps({
    "mode": mode, "mm_kwargs": bool(a.mm_kwargs), "ttft_s": round((first or t0) - t0, 3), "wall_s": round(dt, 3),
    "prompt_tokens": usage.prompt_tokens if usage else None, "completion_tokens": n_out,
    "decode_tok_s": round(n_out / max(dt - ((first or t0) - t0), 1e-6), 1),
}), file=sys.stderr)
