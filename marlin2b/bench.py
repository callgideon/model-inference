#!/usr/bin/env python3
"""Closed-loop load test against the vLLM endpoint: N concurrent clients each send
the same video request back-to-back until `--requests` complete. Reports request
throughput, output token throughput, and TTFT / TPOT / latency percentiles —
the numbers research/models/marlin2b/*.md estimated and never measured.

    python marlin2b/bench.py video.mp4 --concurrency 8 --requests 32
    python marlin2b/bench.py video.mp4 -c 1 -n 5 --max-tokens 256      # latency floor
Writes a JSON line to --out (default marlin2b/results/bench.jsonl) per run.
"""
import argparse, asyncio, base64, json, mimetypes, os, re, statistics, sys, time

from openai import AsyncOpenAI

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("-c", "--concurrency", type=int, default=4)
ap.add_argument("-n", "--requests", type=int, default=16)
ap.add_argument("--max-tokens", type=int, default=512)
ap.add_argument("--prompt", default=None, help="defaults to the canonical caption prompt via smoke.py's finder")
ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000/v1"))
ap.add_argument("--weights", default=os.environ.get("WEIGHTS", "/opt/dlami/nvme/marlin2b"))
ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results", "bench.jsonl"))
ap.add_argument("--label", default="")
ap.add_argument("--mm-kwargs", default=os.environ.get("MM_KWARGS", "auto"), help="JSON, 'auto' (training budget from clip duration) or '' (processor default)")
a = ap.parse_args()

sys.path.insert(0, os.path.dirname(__file__))
if a.prompt is None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("smoke", os.path.join(os.path.dirname(__file__), "smoke.py"))
    # smoke.py runs on import; reuse only its prompt finder by exec'ing the function source.
    src = open(spec.origin, encoding="utf-8").read()
    ns = {}
    exec(src[src.index("def canonical_prompt") : src.index("mode = ")], {"os": os, "re": re, "sys": sys}, ns)
    a.prompt = ns["canonical_prompt"](a.weights, "caption")
else:
    src = open(os.path.join(os.path.dirname(__file__), "smoke.py"), encoding="utf-8").read()
    ns = {}
    exec(src[src.index("def canonical_prompt") : src.index("mode = ")], {"os": os, "re": re, "sys": sys}, ns)
mm_kwargs = None if not a.mm_kwargs else (ns["training_budget_kwargs"](a.video) if a.mm_kwargs == "auto" else json.loads(a.mm_kwargs))

if a.video.startswith(("http://", "https://")):
    url = a.video
else:
    url = f"data:{mimetypes.guess_type(a.video)[0] or 'video/mp4'};base64," + base64.b64encode(open(a.video, "rb").read()).decode()
messages = [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": url}}, {"type": "text", "text": a.prompt}]}]

client = AsyncOpenAI(base_url=a.base_url, api_key="none")
lock = asyncio.Lock()
remaining = a.requests
rows = []


async def one():
    t0 = time.perf_counter()
    first = None
    n = 0
    usage = None
    async for ch in await client.chat.completions.create(model="marlin2b", messages=messages, max_tokens=a.max_tokens, temperature=0, stream=True, stream_options={"include_usage": True}, extra_body={"mm_processor_kwargs": mm_kwargs} if mm_kwargs else {}):
        if ch.usage:
            usage = ch.usage
        if ch.choices and ch.choices[0].delta.content:
            first = first or time.perf_counter()
            n += 1
    t1 = time.perf_counter()
    n = usage.completion_tokens if usage else n
    rows.append({"ttft": (first or t1) - t0, "latency": t1 - t0, "out": n, "in": usage.prompt_tokens if usage else None,
                 "tpot": ((t1 - (first or t1)) / max(n - 1, 1))})


async def worker():
    global remaining
    while True:
        async with lock:
            if remaining <= 0:
                return
            remaining -= 1
        await one()


async def main():
    await one()  # warm-up, not counted
    rows.clear()
    t0 = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(a.concurrency)))
    return time.perf_counter() - t0


wall = asyncio.run(main())
p = lambda k, q: round(statistics.quantiles([r[k] for r in rows], n=100)[q - 1], 3) if len(rows) > 1 else round(rows[0][k], 3)
res = {
    "label": a.label, "mm_kwargs": mm_kwargs, "video": os.path.basename(a.video), "concurrency": a.concurrency, "requests": len(rows), "max_tokens": a.max_tokens,
    "prompt_tokens": rows[0]["in"], "wall_s": round(wall, 2),
    "req_per_s": round(len(rows) / wall, 3), "out_tok_per_s": round(sum(r["out"] for r in rows) / wall, 1),
    "ttft_p50": p("ttft", 50), "ttft_p95": p("ttft", 95), "tpot_p50_ms": round(p("tpot", 50) * 1000, 1), "tpot_p95_ms": round(p("tpot", 95) * 1000, 1),
    "latency_p50": p("latency", 50), "latency_p95": p("latency", 95), "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
print(json.dumps(res, indent=2))
os.makedirs(os.path.dirname(a.out), exist_ok=True)
with open(a.out, "a") as f:
    f.write(json.dumps(res) + "\n")
