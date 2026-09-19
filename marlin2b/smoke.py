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
ap.add_argument("--show-prompt", action="store_true")
ap.add_argument("--mm-kwargs", default=os.environ.get("MM_KWARGS", "auto"),
                help="vLLM mm_processor_kwargs as JSON; 'auto' (default) reproduces Marlin's training video budget from the clip duration; '' sends none (processor default, ~6x more tokens)")
a = ap.parse_args()


def canonical_prompt(weights, mode, event=None):
    """The exact prompt the vendor helpers use. Prefer importing modeling_marlin.py
    (it defines CAPTION_PROMPT and GROUNDING_PROMPT_TEMPLATE); fall back to a
    regex over the source when torch/transformers are not installed here."""
    path = os.path.join(weights, "modeling_marlin.py")
    consts = {}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("modeling_marlin", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        consts = {k: v for k, v in vars(mod).items() if "PROMPT" in k and isinstance(v, str)}
    except Exception as e:  # no torch here: parse the source instead
        print(f"note: import failed ({type(e).__name__}); parsing source", file=sys.stderr)
        src = open(path, encoding="utf-8").read()
        for name, val in re.findall(r'^([A-Z_]*PROMPT[A-Z_]*)\s*=\s*\(?\s*("""[\s\S]*?"""|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\')', src, re.M):
            inner = val[3:-3] if val.startswith(('"""', "'''")) else val[1:-1]
            consts[name] = inner.encode().decode("unicode_escape")
    aliases = {"caption": ("CAPTION",), "find": ("FIND", "GROUNDING")}[mode]
    for name, text in consts.items():
        if any(k in name.upper() for k in aliases):
            return text.format(event=event) if event and "{" in text else text
    sys.exit(f"no {mode} prompt constant found in {path} (have {list(consts)}); pass --prompt")


def training_budget_kwargs(video_path, fps=2.0, min_frames=4, max_frames=240, px_per_frame=200704):
    """mm_processor_kwargs that reproduce Marlin's training-time video budget.
    transformers' Qwen3VL video processor treats size.longest_edge as the pixel
    budget for the WHOLE sampled clip, not per frame, so scale it by the number
    of frames it will sample (fps x duration, clamped). Measured on vLLM nightly
    2026-09-19: a 10 s clip -> 20 frames -> grid [10,28,28] -> 1,960 video tokens,
    versus 11,960 with the processor default (marlin2b/results/notes.md)."""
    try:
        import av
        with av.open(video_path) as c:
            duration = float(c.duration) / av.time_base if c.duration else float(c.streams.video[0].duration * c.streams.video[0].time_base)
    except Exception as e:  # no av or unreadable container: fall back to the 240-frame cap
        print(f"warning: could not read duration ({e}); assuming max frames", file=sys.stderr)
        duration = max_frames / fps
    frames = int(min(max_frames, max(min_frames, round(duration * fps))))
    frames += frames % 2  # temporal patch of 2
    return {"fps": fps, "min_frames": min_frames, "max_frames": max_frames,
            "size": {"shortest_edge": 4096, "longest_edge": frames * px_per_frame}}


mode = "find" if a.find else "caption"
prompt = a.prompt or canonical_prompt(a.weights, mode, a.find)
if a.show_prompt:
    print("PROMPT:", repr(prompt), file=sys.stderr)

if a.video.startswith(("http://", "https://")):
    url = a.video
else:
    mime = mimetypes.guess_type(a.video)[0] or "video/mp4"
    url = f"data:{mime};base64," + base64.b64encode(open(a.video, "rb").read()).decode()

mm_kwargs = None if not a.mm_kwargs else (training_budget_kwargs(a.video) if a.mm_kwargs == "auto" and not a.video.startswith(("http://", "https://")) else json.loads(a.mm_kwargs) if a.mm_kwargs != "auto" else None)
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
    extra_body={"mm_processor_kwargs": mm_kwargs} if mm_kwargs else {},
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
    "mode": mode, "mm_kwargs": mm_kwargs, "ttft_s": round((first or t0) - t0, 3), "wall_s": round(dt, 3),
    "prompt_tokens": usage.prompt_tokens if usage else None, "completion_tokens": n_out,
    "decode_tok_s": round(n_out / max(dt - ((first or t0) - t0), 1e-6), 1),
}), file=sys.stderr)
