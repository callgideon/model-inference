#!/usr/bin/env python3
"""M4 MEDIA-OPT: measure the media path before anything in it is tuned.

    uv run --frozen python -m tests.m.perf.harness measure --label baseline \
        --out ../../models/marlin2b/results/M4-baseline.jsonl [--ffmpeg <pinned ffmpeg>]
    uv run --frozen python -m tests.m.perf.harness report before.jsonl after.jsonl

What one `measure` run does, per clip, against the **real** M1/M2 code (`MediaFetcher`,
`MediaPreparation`, `probe`):

* retrieval over a real loopback TCP socket: a `http.server` subprocess serves the clips,
  the fetcher resolves the name to a public documentation address that passes its policy,
  and the transport connects that pinned request to 127.0.0.1 - so every byte goes through
  httpx, h11 and the fetcher's cap, digest and copy exactly as it would from a remote host,
  without the remote network. Time to first byte, wall time, MiB/s, bytes delivered.
* the probe, materialization (URL and `data:` forms), preparation with the processing cache
  cold, warm and after expiry, each as the p50 and max of `--repeats` runs (p50 needs >= 6
  samples, `bench.py`'s rule), plus the event loop's worst stall during the operation - a
  preparation that computes on the loop delays every other stream the process is serving.
* allocation high-water (`tracemalloc` peak above the pre-operation level) per operation,
  in a separate pass so its overhead never touches a timing.
* MEDIA-PARITY facts (`support.prepared_facts`): identical across two runs, across the
  in-memory and filesystem object stores and across a cache expiry, and agreeing with the
  corpus manifest's ffprobe-derived duration, geometry and codec.
* refused media (the corpus negatives, a clip over the byte cap, clips over the duration
  cap): the refusal, how long it took, and how many bytes were read first.

The object store during timing is the in-memory double with its digest computed off the
loop (`QuietStore`): an S3 `PutObject` is network I/O, so a stall caused by the double's
own hashing would be charged to code that does not do it. Every row carries host and
version labels: **this host is a development box, not the pilot GPU host (P-04)**.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import hashlib
import json
import os
import pathlib
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc

import httpx
from infrx.contracts.limits import DEFAULTS
from infrx.media import fetch, prepare, probe, store

from .. import support

ORG = "1a1a1a1a-0000-4000-8000-00000000000a"
HOST = "clips.example"
REPO = pathlib.Path(__file__).resolve().parents[5]
MANIFESTS = {"e1": REPO / "models/marlin2b/corpus/manifest.json",
             "sop": REPO / "models/marlin2b/corpus-synth/manifest.json"}
MIB = 1 << 20
# Synthetic containers for the size axis the corpus does not reach (its largest clip is
# 35 MB): the probe reads only the header, and fetch/hash/copy cost depends on size only.
LADDER = (16 * MIB, 32 * MIB, DEFAULTS.max_media_bytes, DEFAULTS.max_media_bytes + 1)
# Real encoder output over the duration cap, in both layouts a phone or ffmpeg writes: the
# header before the media (+faststart) and after it (ffmpeg's default).
OVER_CAP_RECIPE = ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "150",
                   "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "3M", "-pix_fmt",
                   "yuv420p", "-g", "48", "-threads", "1", "-fflags", "+bitexact",
                   "-flags:v", "+bitexact", "-an"]


def default_corpus() -> pathlib.Path:
    """`$CORPUS_CACHE`, else `<main checkout>/.claude/corpus-cache` (E1's convention)."""
    if os.environ.get("CORPUS_CACHE"):
        return pathlib.Path(os.environ["CORPUS_CACHE"])
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            cwd=REPO, text=True, capture_output=True, check=True).stdout
    return pathlib.Path(common.strip()).parent / ".claude/corpus-cache"


def layout(data: bytes) -> str:
    """The top-level ISO boxes in order ("ftyp,moov,mdat" is header-first), or ""."""
    kinds, at = [], 0
    while at + 8 <= len(data) and len(kinds) < 16:
        size = int.from_bytes(data[at:at + 4], "big")
        if size == 1:
            size = int.from_bytes(data[at + 8:at + 16], "big")
        kinds.append(data[at + 4:at + 8].decode("latin-1"))
        if size < 8:
            break
        at += size
    return ",".join(kinds) if kinds[:1] == ["ftyp"] else ""


def environment(label: str, ffmpeg: str | None = None) -> dict:
    cpu = next((line.split(":", 1)[1].strip() for line in open("/proc/cpuinfo")
                if line.startswith("model name")), "?")
    mem = next((line.split()[1] for line in open("/proc/meminfo")
                if line.startswith("MemTotal")), "0")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True,
                         capture_output=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "apps/infrx-api/infrx"],
                           cwd=REPO, text=True, capture_output=True).stdout.strip()
    return {"kind": "env", "label": label, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "code_sha": sha + ("+dirty" if dirty else ""),
            "host_class": "development worktree host - NOT the pilot GPU host (P-04)",
            "platform": platform.platform(), "cpu": cpu, "cpus": os.cpu_count(),
            "mem_gib": round(int(mem) / MIB, 1), "python": platform.python_version(),
            "httpx": httpx.__version__, "transport": "loopback TCP (127.0.0.1), http.server subprocess",
            "store": "in-memory double, digest off-loop (QuietStore)",
            "preparation_concurrency": DEFAULTS.preparation_concurrency,
            "max_media_bytes": DEFAULTS.max_media_bytes,
            "max_video_seconds": DEFAULTS.max_video_seconds,
            "ffmpeg_sha256": hashlib.sha256(open(ffmpeg, "rb").read()).hexdigest()
            if ffmpeg else None}


# --- the clips -----------------------------------------------------------------
def padded_mp4(size: int, seconds: float = 60.0) -> bytes:
    """A probeable MP4 of exactly `size` bytes: header first, zero-filled `mdat`."""
    head = support.box(b"ftyp", b"isom\x00\x00\x02\x00isom") + support.box(
        b"moov", support.mvhd(round(seconds * 1000)), support.trak(width=1920, height=1080))
    return head + (size - len(head)).to_bytes(4, "big") + b"mdat" + bytes(size - len(head) - 8)


def over_cap_clips(ffmpeg: str, work: pathlib.Path) -> list[pathlib.Path]:
    """Render the two over-cap clips once (deterministic: one thread, bit-exact)."""
    last, first = work / "overcap-150s-720p-moovlast.mp4", work / "overcap-150s-720p-faststart.mp4"
    if not last.exists():
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *OVER_CAP_RECIPE,
                        str(last)], check=True)
    if not first.exists():
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(last),
                        "-c", "copy", "-movflags", "+faststart", "-fflags", "+bitexact",
                        str(first)], check=True)
    return [last, first]


def clip_set(corpus: pathlib.Path, work: pathlib.Path, ffmpeg: str | None) -> list[dict]:
    """Every clip this run measures, with what the corpus manifest says it is."""
    clips = []
    for name, path in MANIFESTS.items():
        manifest = json.loads(path.read_text())
        for clip in manifest["clips"]:
            derived = clip["derived"]
            clips.append({"id": clip["id"], "set": name, "file": clip["file"],
                          "expect": {"duration_s": derived["duration_s"],
                                     "width": derived["width"], "height": derived["height"],
                                     "codec": derived["codec"], "sha256": derived["sha256"]}})
        for negative in manifest.get("negatives", ()):
            clips.append({"id": negative["id"], "set": name + "-negative",
                          "file": negative["file"], "expect": None})
    for size in LADDER:
        name = f"ladder-{size}.mp4"
        (work / name).write_bytes(padded_mp4(size))
        clips.append({"id": f"ladder-{size}", "set": "synthetic-size", "file": name,
                      "expect": None})
    if ffmpeg:
        for path in over_cap_clips(ffmpeg, work):
            clips.append({"id": path.stem, "set": "over-cap", "file": path.name, "expect": None})
    for clip in clips:                  # one served tree: the corpus beside the generated files
        target = work / clip["file"]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(corpus / clip["file"])
    return clips


# --- the plumbing ---------------------------------------------------------------
class Counted(httpx.AsyncByteStream):
    """The response body as the fetcher sees it: when the first byte came, how many came."""

    def __init__(self, inner, stats: dict) -> None:
        self.inner, self.stats = inner, stats

    async def __aiter__(self):
        async for chunk in self.inner:
            self.stats.setdefault("first_byte", time.perf_counter())
            self.stats["delivered"] = self.stats.get("delivered", 0) + len(chunk)
            yield chunk

    async def aclose(self) -> None:
        await self.inner.aclose()


class Loopback(httpx.AsyncBaseTransport):
    """Sends the fetcher's pinned request to the local server, whatever address it pinned."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.inner = httpx.AsyncHTTPTransport()
        self.stats: dict = {}

    async def handle_async_request(self, request):
        request.url = request.url.copy_with(host="127.0.0.1", port=self.port)
        response = await self.inner.handle_async_request(request)
        response.stream = Counted(response.stream, self.stats)
        return response


class QuietStore(store.InMemoryObjectStore):
    """The in-memory double with its own digest computed off the loop (module docstring)."""

    async def put_if_absent(self, key, data, content_type):
        if key in self.objects:
            return False
        self.objects[key] = (await asyncio.to_thread(store.digest_of, data), data, content_type)
        return True


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def adapter(transport, cache_root: str, objects=None, clock=None) -> prepare.MediaPreparation:
    fetcher = fetch.MediaFetcher(DEFAULTS, resolve=support.resolver([support.PUBLIC]),
                                 transport=transport, log=support.Records())
    return prepare.MediaPreparation(
        objects if objects is not None else QuietStore(),
        cache=prepare.ProcessingCache(cache_root, clock=clock or Clock()),
        limits=DEFAULTS, fetcher=fetcher, job_org=lambda job_id: ORG)


async def stalled(work) -> tuple[object, float, float]:
    """(result or the exception, wall seconds, the loop's worst stall in seconds)."""
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    worst = 0.0

    async def watch():
        nonlocal worst
        last = loop.time()
        while not done.is_set():
            await asyncio.sleep(0.001)
            now = loop.time()
            worst = max(worst, now - last - 0.001)
            last = now

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0)
    start = time.perf_counter()
    try:
        result = await work
    except Exception as exc:            # a refusal is a measured outcome, not a harness error
        result = exc
    wall = time.perf_counter() - start
    done.set()
    await watcher
    return result, wall, max(worst, 0.0)


def summary(values: list[float], scale: float = 1.0, digits: int = 3) -> dict:
    ordered = sorted(values)
    return {"p50": round(statistics.median(ordered) * scale, digits),
            "max": round(ordered[-1] * scale, digits), "n": len(ordered)}


def outcome(result) -> str:
    """`ok`, or `<error code>:<reason>:<detail>` for a refusal - both, so an early refusal's
    message can be compared with the whole-object one's in the rows (review H3)."""
    if isinstance(result, Exception):
        return (f"{getattr(result, 'code', type(result).__name__)}:"
                f"{getattr(result, 'reason', '')}:{getattr(result, 'detail', '')}")
    return "ok"


# --- one clip ---------------------------------------------------------------------
async def measure_clip(clip: dict, path: pathlib.Path, transport: Loopback, repeats: int,
                       scratch: pathlib.Path) -> dict:
    data = path.read_bytes()
    url = f"http://{HOST}/{clip['file']}"
    inline = f"data:video/mp4;base64,{base64.b64encode(data).decode()}"
    row = {"kind": "clip", "id": clip["id"], "set": clip["set"], "bytes": len(data),
           "sha256": hashlib.sha256(data).hexdigest(), "layout": layout(data),
           # the host is shared: its 1-minute load when this clip started, for the reader
           "loadavg_1m": os.getloadavg()[0]}
    try:
        probed = probe.probe(data)
        row["probe"] = {"duration_s": probed.duration_s, "width": probed.width,
                        "height": probed.height, "codec": probed.codec, "mime": probed.mime}
    except Exception as exc:
        row["probe"] = outcome(exc)
    samples: dict[str, list] = {}

    def keep(name, wall, stall):
        samples.setdefault(name, []).append((wall, stall))

    for attempt in range(repeats):
        gc.collect()
        fetcher = adapter(transport, "").fetcher
        transport.stats = {}
        start = time.perf_counter()
        fetched, fetch_wall, fetch_stall = await stalled(fetcher.fetch(url))
        keep("fetch", fetch_wall, fetch_stall)
        if attempt == 0:
            row["fetch_outcome"] = outcome(fetched)
            row["bytes_read"] = transport.stats.get("delivered", 0)
        if "first_byte" in transport.stats:
            samples.setdefault("ttfb", []).append((transport.stats["first_byte"] - start, 0.0))
        with tempfile.TemporaryDirectory(dir=scratch) as cache_root:
            clock = Clock()
            media = adapter(transport, cache_root, clock=clock)
            transport.stats = {}
            ref, wall, stall = await stalled(media.materialize(ORG, url))
            keep("materialize_url", wall, stall)
            if attempt == 0:
                row["outcome"] = outcome(ref)
                row["bytes_read_materialize"] = transport.stats.get("delivered", 0)
            inline_ref, wall, stall = await stalled(
                adapter(transport, "").materialize(ORG, inline))
            keep("materialize_inline", wall, stall)
            if attempt == 0:
                row["inline_outcome"] = outcome(inline_ref)
            if isinstance(ref, Exception):
                continue
            await media.attach("job-1", (ref,))
            for name in ("prepare_cold", "prepare_warm"):
                result, wall, stall = await stalled(media.prepare("job-1", "v1"))
                assert not isinstance(result, Exception), result
                keep(name, wall, stall)
            clock.now += DEFAULTS.processing_cache_ttl_s
            result, wall, stall = await stalled(media.prepare("job-1", "v1"))
            assert not isinstance(result, Exception), result
            keep("prepare_expired", wall, stall)
    started = time.perf_counter()
    for _ in range(repeats):
        try:
            probe.probe(data)
        except Exception:
            pass
    row["probe_s"] = round((time.perf_counter() - started) / repeats, 6)
    for name, pairs in samples.items():
        row[f"{name}_ms"] = summary([wall for wall, _ in pairs], 1e3)
        if name != "ttfb":
            row[f"{name}_stall_ms"] = summary([stall for _, stall in pairs], 1e3)
    if "fetch" in samples and isinstance(fetched, fetch.Fetched):
        row["fetch_mib_s"] = round(len(data) / MIB / statistics.median(
            wall for wall, _ in samples["fetch"]), 1)
    row["peak_mib"] = await peaks(url, inline, transport, scratch, row["outcome"] == "ok")
    if row["outcome"] == "ok":
        row["parity"] = await parity(clip, url, inline, transport, scratch)
    return row


async def peaks(url: str, inline: str, transport, scratch, accepted: bool) -> dict:
    """tracemalloc high-water above the level before each operation, in MiB."""
    out = {}
    tracemalloc.start()
    try:
        with tempfile.TemporaryDirectory(dir=scratch) as cache_root:
            media = adapter(transport, cache_root)
            operations = [("materialize_url", lambda: media.materialize(ORG, url)),
                          ("materialize_inline", lambda: adapter(transport, "").materialize(ORG, inline))]
            if accepted:
                operations.append(("prepare_cold", lambda: prepare_once(media)))
            for name, make in operations:
                gc.collect()
                tracemalloc.reset_peak()
                base = tracemalloc.get_traced_memory()[0]
                try:
                    await make()
                except Exception:
                    pass
                out[name] = round((tracemalloc.get_traced_memory()[1] - base) / MIB, 2)
    finally:
        tracemalloc.stop()
    return out


async def prepare_once(media):
    await media.attach("job-1", (next(iter(media.refs.values())),))
    return await media.prepare("job-1", "v1")


async def parity(clip: dict, url: str, inline: str, transport, scratch) -> dict:
    """MEDIA-PARITY for one clip; `support.prepared_facts` is the oracle."""
    runs = {}
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        tmp = pathlib.Path(tmp)
        for name, objects, source in (("memory-1", QuietStore(), url),
                                      ("memory-2", store.InMemoryObjectStore(), url),
                                      ("fs", support.FileObjectStore(tmp / "objects"), url),
                                      ("inline", store.InMemoryObjectStore(), inline)):
            media = adapter(transport, str(tmp / name), objects)
            runs[name] = await support.prepared_facts(media, ORG, source, "job-1")
            if name == "memory-1":
                # The same store after the cache's life: the local copy is gone and the
                # second preparation rebuilds it from the durable object.
                media.cache.clock.now += DEFAULTS.processing_cache_ttl_s
                runs["after-expiry"] = await support.prepared_facts(media, ORG, source, "job-2")
    reference = runs["memory-1"]
    facts = {"identical": sorted(name for name, run in runs.items() if run == reference),
             "differs": sorted(name for name, run in runs.items() if run != reference),
             "facts_sha256": hashlib.sha256(json.dumps(reference, sort_keys=True)
                                            .encode()).hexdigest(),
             "v1_identity": reference["prepared_digest"] == reference["local_digest"]
             == reference["source_digest"],
             "budget": reference["budget"]}
    expect = clip.get("expect")
    if expect:
        facts["manifest"] = {
            "sha256": reference["source_digest"] == "sha256:" + expect["sha256"],
            "duration_delta_s": round(reference["ref"]["duration_s"] - expect["duration_s"], 6),
            "budget_equal": support.BUDGET.budget_kwargs(expect["duration_s"])
            == reference["budget"]}
    return facts


def disagrees(row: dict) -> bool:
    """Review P4: a clip whose preparations differ or whose facts disagree with its manifest
    (the same 1 ms duration tolerance as test_parity), which fails the `measure` run."""
    facts = row.get("parity", {})
    manifest = facts.get("manifest", {"sha256": True, "budget_equal": True,
                                      "duration_delta_s": 0.0})
    return bool(facts.get("differs")) or not (
        manifest["sha256"] and manifest["budget_equal"]
        and abs(manifest["duration_delta_s"]) < 1e-3)


# --- the run ------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def run(args) -> list[str]:
    """Measure every clip into `args.out`; the ids of the clips that `disagrees`."""
    corpus = pathlib.Path(args.corpus) if args.corpus else default_corpus()
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    clips = clip_set(corpus, work, args.ffmpeg)
    if args.only:
        clips = [clip for clip in clips if any(key in clip["id"] for key in args.only)]
    port = free_port()
    bad = []
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind",
                               "127.0.0.1", "--directory", str(work)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.05)
        transport = Loopback(port)
        scratch = work / "tmp"
        scratch.mkdir(exist_ok=True)
        with open(args.out, "w") as out:
            out.write(json.dumps(environment(args.label, args.ffmpeg)) + "\n")
            for clip in clips:
                row = await measure_clip(clip, work / clip["file"], transport, args.repeats,
                                         scratch)
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
                if disagrees(row):
                    bad.append(clip["id"])
                print(f"{clip['id']:<58} {row['outcome']:<34} "
                      f"mat {row['materialize_url_ms']['p50']:>9} ms", flush=True)
        shutil.rmtree(scratch, ignore_errors=True)
    finally:
        server.terminate()
        server.wait()
    return bad


# --- the report -----------------------------------------------------------------
BUCKETS = ((0, MIB, "<1 MiB"), (MIB, 8 * MIB, "1-8 MiB"), (8 * MIB, 32 * MIB, "8-32 MiB"),
           (32 * MIB, DEFAULTS.max_media_bytes + 1, "32-64 MiB"))
METRICS = ("fetch_ms", "materialize_url_ms", "materialize_url_stall_ms",
           "materialize_inline_ms", "materialize_inline_stall_ms", "prepare_cold_ms",
           "prepare_cold_stall_ms", "prepare_warm_ms", "prepare_expired_ms")


def load(path: str) -> tuple[dict, list[dict]]:
    rows = [json.loads(line) for line in open(path)]
    return rows[0], rows[1:]


def report(paths: list[str], clips: list[str] = ()) -> None:
    """Per size bucket, the median over accepted clips of each clip's p50 (and the worst
    clip's max for stalls), one column per run; then every refused clip, then `clips` one by
    one. Runs are compared on the clips they all measured, and the table says how many."""
    runs = [load(path) for path in paths]
    common = set.intersection(*({row["id"] for row in rows} for _, rows in runs))
    runs = [(env, [row for row in rows if row["id"] in common]) for env, rows in runs]
    for env, _ in runs:
        print(f"# {env['label']}: code {env['code_sha']}, {env['utc']}, {env['cpu']}, "
              f"{env['cpus']} CPUs, Python {env['python']}, {env['host_class']}")
    print("| bucket | clips | metric | " + " | ".join(env["label"] for env, _ in runs) + " |")
    print("|---|---:|---|" + "---:|" * len(runs))
    for low, high, name in BUCKETS:
        per_run = [[r for r in rows if r["outcome"] == "ok" and low <= r["bytes"] < high]
                   for _, rows in runs]
        if not per_run[0]:
            continue
        for metric in METRICS + ("peak_materialize_url", "peak_materialize_inline",
                                 "peak_prepare_cold"):
            cells = []
            for rows in per_run:
                if metric.startswith("peak_"):
                    values = [r["peak_mib"][metric[5:]] / (r["bytes"] / MIB) for r in rows]
                    cells.append(f"{statistics.median(values):.2f}x")
                elif metric.endswith("stall_ms"):
                    cells.append(f"{statistics.median(r[metric]['p50'] for r in rows):.1f} "
                                 f"(max {max(r[metric]['max'] for r in rows):.1f})")
                else:
                    cells.append(f"{statistics.median(r[metric]['p50'] for r in rows):.2f}")
            print(f"| {name} | {len(per_run[0])} | {metric} | " + " | ".join(cells) + " |")
    print()
    print("| refused clip | bytes | " + " | ".join(
        f"{env['label']}: outcome / bytes read / ms" for env, _ in runs) + " |")
    print("|---|---:|" + "---|" * len(runs))
    refused = [r["id"] for r in runs[0][1] if r["outcome"] != "ok"]
    for clip_id in refused:
        cells, size = [], 0
        for _, rows in runs:
            row = next(r for r in rows if r["id"] == clip_id)
            size = row["bytes"]
            cells.append(f"{row['outcome']} / {row['bytes_read_materialize']} / "
                         f"{row['materialize_url_ms']['p50']}")
        print(f"| {clip_id} | {size} | " + " | ".join(cells) + " |")
    for clip_id in clips:
        print(f"\n| {clip_id} | " + " | ".join(env["label"] for env, _ in runs) + " |")
        print("|---|" + "---:|" * len(runs))
        rows = [next(r for r in rows if r["id"] == clip_id) for _, rows in runs]
        for metric in [m for m in METRICS + ("ttfb_ms", "fetch_stall_ms") if m in rows[0]]:
            print(f"| {metric} p50 (max) | " + " | ".join(
                f"{r[metric]['p50']} ({r[metric]['max']})" for r in rows) + " |")
        print("| peak MiB url / inline / prepare | " + " | ".join(
            " / ".join(str(r["peak_mib"].get(k, "-")) for k in
                       ("materialize_url", "materialize_inline", "prepare_cold"))
            for r in rows) + " |")
        print("| bytes read before the outcome | " + " | ".join(
            f"{r['bytes_read_materialize']} ({r['outcome']})" for r in rows) + " |")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    measure = sub.add_parser("measure")
    measure.add_argument("--label", required=True)
    measure.add_argument("--out", required=True)
    measure.add_argument("--corpus", help="default: $CORPUS_CACHE or <main>/.claude/corpus-cache")
    measure.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "m4-perf"),
                         help="served tree + generated clips (kept between runs)")
    measure.add_argument("--ffmpeg", help="the pinned ffmpeg, to render the over-cap clips")
    measure.add_argument("--repeats", type=int, default=7)
    measure.add_argument("--only", nargs="*", help="clip id substrings")
    rep = sub.add_parser("report")
    rep.add_argument("paths", nargs="+")
    rep.add_argument("--clips", nargs="*", default=(), help="clip ids to print one by one")
    args = parser.parse_args(argv)
    if args.command == "report":
        report(args.paths, args.clips)
    else:
        bad = asyncio.run(run(args))
        if bad:
            print(f"MEDIA-PARITY disagreement: {bad}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
