#!/usr/bin/env python3
"""Build and verify the distinct-clip benchmark corpus (task E1).

    python models/marlin2b/corpus/build.py plan      # rewrite recipes (status unbuilt)
    python models/marlin2b/corpus/build.py build     # fetch sources, derive clips, fill hashes
    python models/marlin2b/corpus/build.py verify     # recompute hashes, compare, validate
    python models/marlin2b/corpus/build.py validate  # schema/distinctness only, no media

Recipes are deterministic: the same pinned ffmpeg, source bytes and recipe give
byte-identical clips (`-threads 1 -fflags +bitexact -flags:v +bitexact`), so the
sha256 in manifest.json is a real, checkable pin. Media never enters git; it
lives under $CORPUS_CACHE (default .claude/corpus-cache, git-ignored).

Licences are per source, verified on the page recorded as license_evidence_url.
Nothing here invents a hash, a duration or a licence: unfetched or unbuilt items
keep sha256 null and status "unbuilt".
"""
import argparse, json, math, os, shutil, subprocess, sys, tarfile, tempfile, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
REPO = HERE.parents[2]
CACHE = Path(os.environ.get("CORPUS_CACHE", REPO / ".claude" / "corpus-cache"))
SCHEMA_VERSION = 1
CORPUS_VERSION = "e1-2026-09-20"
API_MAX_DURATION_S = 120  # research/plan/01-contracts.md preparation budget / video cap

# Pinned static ffmpeg: this host has no ffmpeg. sha256 recorded from the download
# actually used to build this corpus; ensure_ffmpeg() refuses anything else.
FFMPEG_PIN = {
    "version": "7.0.2-static",
    "build": "ffmpeg-7.0.2-amd64-static (johnvansickle.com static build, gcc 8/Debian)",
    "tarball_url": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
    "tarball_sha256": "abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67",
    "ffmpeg_sha256": "e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
    "ffprobe_sha256": "4f231a1960d83e403d08f7971e271707bec278a9ae18e21b8b5b03186668450d",
    "dir": "ffmpeg-7.0.2-amd64-static",
}

# Long, clearly licensed films; 64 non-overlapping segments are cut from them.
# usable_start/end keep us inside the film body and pin what plan() may allocate.
SOURCES = [
    {"id": "bbb1080p30", "title": "Big Buck Bunny (Sunflower, 1080p 30fps)", "file": "bbb1080p30.mp4",
     "url": "https://download.blender.org/demo/movies/BBB/bbb_sunflower_1080p_30fps_normal.mp4.zip",
     "archive_member": "bbb_sunflower_1080p_30fps_normal.mp4",
     "license": "CC-BY-3.0", "license_url": "https://creativecommons.org/licenses/by/3.0/",
     "license_evidence_url": "https://peach.blender.org/about/",
     "license_evidence_quote": "Creative Commons Attribution 3.0",
     "attribution": "(CC) Blender Foundation | peach.blender.org",
     "expected": {"duration_s": 634.6, "width": 1920, "height": 1080, "fps": 30.0, "codec": "h264"},
     "usable_start_s": 10.0, "usable_end_s": 610.0},
    {"id": "tos720p", "title": "Tears of Steel (720p)", "file": "tos720p.mov",
     "url": "https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov",
     "license": "CC-BY-3.0", "license_url": "https://creativecommons.org/licenses/by/3.0/",
     "license_evidence_url": "https://mango.blender.org/about/",
     "license_evidence_quote": "Creative Commons Attribution 3.0, which allows you to share and show the film freely",
     "attribution": "(CC) Blender Foundation | mango.blender.org",
     "note": "download.blender.org/demo/movies/ToS/copyright.txt puts the soundtrack under CC-BY-ND-3.0; "
             "derived clips are video-only (-an), so no soundtrack is redistributed.",
     "expected": {"duration_s": 734.166667, "width": 1280, "height": 534, "fps": 24.0, "codec": "h264"},
     "usable_start_s": 20.0, "usable_end_s": 720.0},
    {"id": "sintel1080p", "title": "Sintel (2010, 1080p)", "file": "sintel1080p.mkv",
     "url": "https://download.blender.org/demo/movies/Sintel.2010.1080p.mkv",
     "license": "CC-BY-3.0", "license_url": "https://creativecommons.org/licenses/by/3.0/",
     "license_evidence_url": "https://durian.blender.org/sharing/",
     "license_evidence_quote": "Creative Commons Attribution 3.0",
     "attribution": "(CC) Blender Foundation | durian.blender.org",
     "expected": {"duration_s": 888.032, "width": 1920, "height": 818, "fps": 24.0, "codec": "h264"},
     "usable_start_s": 20.0, "usable_end_s": 860.0},
    {"id": "nasasteve", "title": "The Aurora Named STEVE (NASA Goddard)", "file": "nasasteve.mp4",
     "url": "https://images-assets.nasa.gov/video/GSFC_20180314_Aurora_m12865_Steve/"
            "GSFC_20180314_Aurora_m12865_Steve~orig.mp4",
     "license": "public-domain-usgov",
     "license_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
     "license_evidence_url": "https://images.nasa.gov/docs/images.nasa.gov_Guide_v1.0.pdf",
     "license_evidence_quote": "NASA content is generally not copyrighted and may be used for educational or "
                               "informational purposes without needing explicit permissions",
     "attribution": "NASA's Goddard Space Flight Center (nasa_id GSFC_20180314_Aurora_m12865_Steve)",
     "expected": {"duration_s": 132.885333, "width": 1920, "height": 1080, "fps": 59.94, "codec": "h264"},
     "usable_start_s": 2.0, "usable_end_s": 130.0},
]

# (width, height, label) BEFORE any rotation. 16 entries -> 4 clips per geometry.
GEOMETRIES = [
    (1920, 1080, "1080p-16x9"), (1280, 720, "720p-16x9"), (854, 480, "480p-16x9"), (640, 360, "360p-16x9"),
    (1080, 1920, "1080x1920-portrait"), (720, 1280, "720x1280-portrait"), (480, 854, "480x854-portrait"),
    (1080, 1080, "1080-square"), (512, 512, "512-square"),
    (2560, 1080, "2560x1080-ultrawide"), (1920, 800, "1920x800-scope"),
    (640, 480, "640x480-4x3"), (1024, 768, "1024x768-4x3"),
    (1920, 480, "1920x480-extreme-wide"), (480, 1920, "480x1920-extreme-tall"), (426, 240, "240p-tiny"),
]
# 13 durations (coprime with 16 geometries, so duration does not track geometry), all <= the 120 s API cap.
DURATIONS_S = [2, 3, 5, 7, 9, 12, 15, 20, 26, 34, 48, 72, 112]
FPS = [30, 24, 15, 10, 60]
# Rotation on a few geometries: real rotated pixels. Container display-matrix
# rotation is NOT covered: ffmpeg 7's mp4 muxer silently drops `-metadata rotate=`
# (checked, the probed rotation stayed null), so no recipe claims it.
ROTATE_AT = {4: "90cw", 9: "180", 14: "90ccw"}      # geometry index -> transpose

PROMPTS = [
    ("p00", "caption", "Describe every event in this clip, in order, with start and end times in seconds."),
    ("p01", "find", "From when to when is the main subject moving across the frame?"),
    ("p02", "count", "How many distinct people, animals or vehicles appear? List them."),
    ("p03", "motion", "Describe the camera motion: static, pan, tilt, zoom or handheld?"),
    ("p04", "scene", "How many scene changes are there, and at which timestamps?"),
    ("p05", "objects", "List the objects in the foreground and the objects in the background."),
    ("p06", "text", "Is any on-screen text, logo or caption visible? Quote it exactly if so."),
    ("p07", "summary", "Summarise this clip in one sentence for a video search index."),
    ("p08", "temporal", "What happens first and what happens last? Answer in two short sentences."),
    ("p09", "colour", "Describe the dominant colours and the lighting conditions."),
    ("p10", "setting", "Where does this take place: indoors or outdoors, natural or built, day or night?"),
    ("p11", "grounding", "Give the time range of the single most visually salient action."),
    ("p12", "anomaly", "Is anything unusual, damaged, unsafe or out of place? Describe it or say none."),
    ("p13", "frames", "Describe the first visible frame, then the last visible frame."),
    ("p14", "orientation", "Is this footage portrait, landscape or square, and is it rotated?"),
    ("p15", "detail", "Describe the smallest detail you can reliably identify and when it is visible."),
]

# Corrupt / invalid-media fixtures for the MEDIA-PARITY failure cases. Derived from
# built clips or fabricated locally, so their sha256 is real, not invented.
NEGATIVES = [
    {"id": "neg-truncated-mp4", "ext": ".mp4", "recipe": {"op": "truncate", "from": None, "keep_bytes": 65536},
     "expected_failure": "truncated_container_probe_failure"},
    {"id": "neg-wrong-container", "ext": ".mkv", "recipe": {"op": "rename_extension", "from": None},
     "expected_failure": "container_extension_mismatch"},
    {"id": "neg-zero-length", "ext": ".mp4", "recipe": {"op": "zero_length"},
     "expected_failure": "empty_payload"},
    {"id": "neg-not-video", "ext": ".mp4", "recipe": {"op": "text_payload", "text": "this is not a video\n"},
     "expected_failure": "unsupported_media_type"},
]

ENCODE = {"codec": "libx264", "preset": "veryfast", "crf": 26, "pix_fmt": "yuv420p", "gop": 48,
          "threads": 1, "bitexact": True, "audio": "dropped"}


# ---------------------------------------------------------------- tools


def sha256_file(path):
    h = sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ensure_ffmpeg(download=True):
    """Return (ffmpeg, ffprobe) paths for the pinned static build, fetching it once."""
    base = CACHE / "tools" / FFMPEG_PIN["dir"]
    ffmpeg, ffprobe = base / "ffmpeg", base / "ffprobe"
    if not (ffmpeg.exists() and ffprobe.exists()):
        if not download:
            sys.exit(f"pinned ffmpeg missing under {base}; run 'build' first")
        tar = CACHE / "tools" / "ffmpeg-release-amd64-static.tar.xz"
        tar.parent.mkdir(parents=True, exist_ok=True)
        if not tar.exists() or sha256_file(tar) != FFMPEG_PIN["tarball_sha256"]:
            print(f"fetching {FFMPEG_PIN['tarball_url']}", file=sys.stderr)
            fetch(FFMPEG_PIN["tarball_url"], tar)
        got = sha256_file(tar)
        if got != FFMPEG_PIN["tarball_sha256"]:
            sys.exit(f"ffmpeg tarball sha256 {got} != pinned {FFMPEG_PIN['tarball_sha256']}")
        with tarfile.open(tar) as t:
            t.extractall(CACHE / "tools", filter="data")
    for path, pin in ((ffmpeg, "ffmpeg_sha256"), (ffprobe, "ffprobe_sha256")):
        got = sha256_file(path)
        if got != FFMPEG_PIN[pin]:
            sys.exit(f"{path.name} sha256 {got} != pinned {FFMPEG_PIN[pin]}")
    return str(ffmpeg), str(ffprobe)


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    # download.blender.org is behind Cloudflare and 403s the default urllib agent.
    req = urllib.request.Request(url, headers={"User-Agent": "marlin2b-corpus-builder/1 (+curl-compatible)"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.replace(dest)


def probe(ffprobe, path):
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,width,height,avg_frame_rate,pix_fmt,nb_frames,"
                          "display_aspect_ratio:stream_side_data=rotation:format=duration,size,format_name",
                          "-of", "json", str(path)], capture_output=True, text=True, errors="replace")
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {out.stderr.strip()[:200]}")
    d = json.loads(out.stdout)
    if not d.get("streams"):
        raise RuntimeError(f"no video stream in {path}")
    s, fmt = d["streams"][0], d["format"]
    num, _, den = s.get("avg_frame_rate", "0/1").partition("/")
    fps = round(int(num) / int(den or 1), 3) if int(den or 1) else None
    w, h = s.get("width"), s.get("height")
    rot = None
    for sd in s.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd["rotation"]
    return {"duration_s": round(float(fmt["duration"]), 3), "bytes": int(fmt["size"]),
            "container": fmt["format_name"], "codec": s.get("codec_name"), "width": w, "height": h,
            "fps": fps, "pix_fmt": s.get("pix_fmt"), "frames": int(s["nb_frames"]) if s.get("nb_frames") else None,
            "aspect": aspect(w, h), "display_aspect_ratio": s.get("display_aspect_ratio"),
            "rotation_metadata": rot}


def aspect(w, h):
    if not w or not h:
        return None
    g = math.gcd(w, h)
    return f"{w // g}:{h // g}"


# ---------------------------------------------------------------- plan


def ffmpeg_args(ffmpeg, src, dst, recipe):
    """Deterministic derivation command. Identical input bytes + args -> identical output bytes."""
    vf = [f"scale={recipe['scale'][0]}:{recipe['scale'][1]}:force_original_aspect_ratio=increase",
          f"crop={recipe['scale'][0]}:{recipe['scale'][1]}", "setsar=1"]
    if recipe.get("transpose"):
        vf.append({"90cw": "transpose=1", "90ccw": "transpose=2", "180": "transpose=1,transpose=1"}
                  [recipe["transpose"]])
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", str(recipe["start_s"]), "-i", str(src),
            "-t", str(recipe["duration_s"]), "-an", "-vf", ",".join(vf), "-r", str(recipe["fps"]),
            "-c:v", ENCODE["codec"], "-preset", ENCODE["preset"], "-crf", str(ENCODE["crf"]),
            "-pix_fmt", ENCODE["pix_fmt"], "-g", str(ENCODE["gop"]), "-threads", str(ENCODE["threads"]),
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-movflags", "+faststart"]
    return args + ["-f", "mp4", str(dst)]


def plan():
    """Deterministic recipes: 64 clips (32 in the fast subset) plus negative fixtures."""
    cursors = {s["id"]: s["usable_start_s"] for s in SOURCES}
    clips = []
    for i in range(64):
        w, h, label = GEOMETRIES[i % 16]
        duration = DURATIONS_S[i % 13]
        src = None
        for k in range(len(SOURCES)):  # round-robin, skipping sources with no room left
            cand = SOURCES[(i + k) % len(SOURCES)]
            if cursors[cand["id"]] + duration <= cand["usable_end_s"]:
                src = cand
                break
        if src is None:
            raise RuntimeError(f"no source has {duration}s of unused footage left for clip {i}")
        start = cursors[src["id"]]
        cursors[src["id"]] = start + duration + 1.0  # 1 s guard so segments never overlap
        recipe = {"start_s": round(start, 3), "duration_s": duration, "scale": [w, h],
                  "fps": FPS[i % 5], "transpose": ROTATE_AT.get(i % 16), "encode": dict(ENCODE)}
        clips.append({"id": f"c{i:03d}-{src['id']}-{label}", "kind": "clip", "source": src["id"],
                      "geometry_label": label, "recipe": recipe, "prompt": PROMPTS[(i + i // 16) % 16][0],
                      "subset": ["full", "fast"] if i < 32 else ["full"],
                      "file": f"clips/c{i:03d}-{src['id']}-{label}.mp4",
                      "derived": None, "status": "unbuilt"})
    negatives = []
    for n, neg in enumerate(NEGATIVES):
        recipe = dict(neg["recipe"])
        if "from" in recipe:
            recipe["from"] = clips[n]["id"]  # distinct parent clips keep the fixtures distinct
        negatives.append({"id": neg["id"], "kind": "negative", "recipe": recipe,
                          "expected_failure": neg["expected_failure"],
                          "file": f"negatives/{neg['id']}{neg['ext']}", "derived": None, "status": "unbuilt"})
    return {
        "schema_version": SCHEMA_VERSION, "corpus_version": CORPUS_VERSION,
        "generated_at_utc": None, "built_at_utc": None,
        "cache_root_env": "CORPUS_CACHE", "cache_root_default": ".claude/corpus-cache",
        "api_limits": {"max_clip_duration_s": API_MAX_DURATION_S, "max_pixels_per_frame": 200704},
        "ffmpeg": FFMPEG_PIN, "encode_defaults": ENCODE,
        "ordering": "manifest order is stable; benchmark clients reorder with their own --seed",
        "sources": [{k: v for k, v in s.items() if k != "usable_end_s"} | {"usable_end_s": s["usable_end_s"],
                    "sha256": None, "bytes": None, "probed": None, "status": "unfetched"} for s in SOURCES],
        "prompts": [{"id": p, "kind": k, "text": t} for p, k, t in PROMPTS],
        "clips": clips, "negatives": negatives,
    }


# ---------------------------------------------------------------- build


def source_path(src):
    return CACHE / "sources" / src["file"]


def item_path(item):
    return CACHE / item["file"]


def build(manifest, jobs=4, force=False):
    ffmpeg, ffprobe = ensure_ffmpeg()
    by_id = {c["id"]: c for c in manifest["clips"]}
    for src in manifest["sources"]:
        path = source_path(src)
        if not path.exists():
            try:
                print(f"fetching source {src['id']}", file=sys.stderr)
                if src.get("archive_member"):
                    import zipfile
                    tmp = CACHE / "sources" / (src["id"] + ".zip")
                    fetch(src["url"], tmp)
                    with zipfile.ZipFile(tmp) as z, z.open(src["archive_member"]) as s, open(path, "wb") as d:
                        shutil.copyfileobj(s, d, 1 << 20)
                else:
                    fetch(src["url"], path)
            except Exception as e:                      # keep an honest unfetched record
                src["status"], src["error"] = "unfetched", f"{type(e).__name__}: {e}"[:200]
                print(f"  source {src['id']} unavailable: {src['error']}", file=sys.stderr)
                continue
        src["sha256"], src["bytes"] = sha256_file(path), path.stat().st_size
        src["probed"] = probe(ffprobe, path)
        src["status"] = "fetched"
        if abs(src["probed"]["duration_s"] - src["expected"]["duration_s"]) > 1.0:
            src["status"] = "mismatch"
            print(f"  WARNING {src['id']} duration {src['probed']['duration_s']} != expected "
                  f"{src['expected']['duration_s']}", file=sys.stderr)
    fetched = {s["id"]: s for s in manifest["sources"] if s["status"] in ("fetched", "mismatch")}

    def derive(clip):
        src = fetched.get(clip["source"])
        if src is None:
            return clip, "unbuilt", None, "source unavailable"
        dst = item_path(clip)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if force or not dst.exists():
            r = subprocess.run(ffmpeg_args(ffmpeg, source_path(src), dst, clip["recipe"]),
                               capture_output=True, text=True, errors="replace")
            if r.returncode != 0:
                return clip, "unbuilt", None, f"ffmpeg: {r.stderr.strip()[-200:]}"
        d = probe(ffprobe, dst)
        d["sha256"] = sha256_file(dst)
        d["source_upscaled"] = bool(src["probed"]["height"] and d["height"] > src["probed"]["height"])
        return clip, "built", d, None

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for clip, status, derived, err in pool.map(derive, manifest["clips"]):
            clip["status"], clip["derived"] = status, derived
            if err:
                clip["error"] = err
                print(f"  clip {clip['id']} unbuilt: {err}", file=sys.stderr)

    for neg in manifest["negatives"]:
        dst, op = item_path(neg), neg["recipe"]["op"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        parent = by_id.get(neg["recipe"].get("from"))
        if op in ("truncate", "rename_extension"):
            if parent is None or parent["status"] != "built":
                neg["status"], neg["derived"] = "unbuilt", None
                neg["error"] = f"parent clip {neg['recipe'].get('from')} not built"
                continue
            data = item_path(parent).read_bytes()
            dst.write_bytes(data[: neg["recipe"]["keep_bytes"]] if op == "truncate" else data)
        elif op == "zero_length":
            dst.write_bytes(b"")
        elif op == "text_payload":
            dst.write_text(neg["recipe"]["text"])
        else:
            raise RuntimeError(f"unknown negative op {op}")
        neg["status"] = "built"
        neg["derived"] = {"sha256": sha256_file(dst), "bytes": dst.stat().st_size, "probe": "expected to fail"}
    manifest["built_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return manifest


# ---------------------------------------------------------------- verify / validate


def validate_manifest(manifest):
    """Schema and distinctness checks. Returns a list of human-readable errors."""
    e = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        e.append(f"schema_version {manifest.get('schema_version')} != {SCHEMA_VERSION}")
    for key in ("corpus_version", "ffmpeg", "sources", "prompts", "clips", "negatives", "api_limits"):
        if not manifest.get(key):
            e.append(f"missing {key}")
    if e:
        return e
    for key in ("version", "tarball_sha256", "ffmpeg_sha256", "ffprobe_sha256"):
        if not manifest["ffmpeg"].get(key):
            e.append(f"ffmpeg identity missing {key}")
    for s in manifest["sources"]:
        for key in ("license", "license_url", "license_evidence_url", "attribution", "title", "url"):
            if not s.get(key):
                e.append(f"source {s.get('id')} missing {key}")
        if s["status"] in ("fetched", "mismatch") and not s.get("sha256"):
            e.append(f"source {s['id']} fetched without sha256")
    prompts = {p["id"] for p in manifest["prompts"]}
    clips = manifest["clips"]
    built = [c for c in clips if c["status"] == "built"]
    if len(built) < 64:
        e.append(f"only {len(built)} built clips, need >= 64")
    fast = [c for c in built if "fast" in c["subset"]]
    if len(fast) < 32:
        e.append(f"fast subset has {len(fast)} built clips, need >= 32")
    if len({c["id"] for c in clips}) != len(clips):
        e.append("duplicate clip ids")
    hashes, recipes = {}, {}
    for c in clips:
        if c["recipe"]["duration_s"] > manifest["api_limits"]["max_clip_duration_s"]:
            e.append(f"{c['id']} duration {c['recipe']['duration_s']}s exceeds the API cap")
        if c["prompt"] not in prompts:
            e.append(f"{c['id']} references unknown prompt {c['prompt']}")
        key = (c["source"], c["recipe"]["start_s"], c["recipe"]["duration_s"], tuple(c["recipe"]["scale"]),
               c["recipe"]["fps"], c["recipe"]["transpose"])
        if key in recipes:
            e.append(f"{c['id']} duplicates the recipe of {recipes[key]}")
        recipes[key] = c["id"]
        if c["status"] == "built":
            d = c.get("derived") or {}
            if not d.get("sha256"):
                e.append(f"{c['id']} marked built without a derived sha256")
                continue
            if d["sha256"] in hashes:
                e.append(f"{c['id']} has the same content as {hashes[d['sha256']]}")
            hashes[d["sha256"]] = c["id"]
        elif c.get("derived"):
            e.append(f"{c['id']} is {c['status']} but carries derived metadata")
    for n in manifest["negatives"]:
        if not n.get("expected_failure"):
            e.append(f"negative {n['id']} missing expected_failure")
        if n["status"] == "built" and not (n.get("derived") or {}).get("sha256"):
            e.append(f"negative {n['id']} marked built without a sha256")
    # Diversity the perf oracle depends on (04-verification.md "Performance evidence").
    if len({tuple(c["recipe"]["scale"]) for c in built}) < 8:
        e.append("fewer than 8 distinct resolutions among built clips")
    if len({(c.get("derived") or {}).get("aspect") for c in built}) < 6:
        e.append("fewer than 6 distinct derived aspect ratios among built clips")
    if len({c["recipe"]["duration_s"] for c in built}) < 8:
        e.append("fewer than 8 distinct durations among built clips")
    if not [c for c in built if 1080 in tuple(c["recipe"]["scale"])]:
        e.append("no 1080-line clip: 1080p decode is the measured bottleneck")
    if len({c["prompt"] for c in built}) < 8:
        e.append("fewer than 8 distinct prompts among built clips")
    return e


def verify(manifest, rederive=False, allow_missing=False):
    ffmpeg, ffprobe = ensure_ffmpeg(download=False)
    errors = validate_manifest(manifest)
    checked = missing = 0
    for src in manifest["sources"]:
        path = source_path(src)
        if src["status"] == "unfetched":
            continue
        if not path.exists():
            missing += 1
            print(f"source {src['id']}: not in cache", file=sys.stderr)
            continue
        got = sha256_file(path)
        checked += 1
        if got != src["sha256"]:
            errors.append(f"source {src['id']} sha256 {got} != manifest {src['sha256']}")
    for item in manifest["clips"] + manifest["negatives"]:
        if item["status"] != "built":
            continue
        path, expect = item_path(item), item["derived"]["sha256"]
        tmp = None
        if not path.exists():
            if not (rederive and item["kind"] == "clip"):
                missing += 1
                print(f"{item['id']}: not in cache", file=sys.stderr)
                continue
            src = next(s for s in manifest["sources"] if s["id"] == item["source"])
            tmp = Path(tempfile.mkdtemp()) / "rederived.mp4"
            r = subprocess.run(ffmpeg_args(ffmpeg, source_path(src), tmp, item["recipe"]),
                               capture_output=True, text=True, errors="replace")
            if r.returncode != 0:
                errors.append(f"{item['id']} re-derivation failed: {r.stderr.strip()[-160:]}")
                continue
            path = tmp
        got = sha256_file(path)
        checked += 1
        if got != expect:
            errors.append(f"{item['id']} sha256 {got} != manifest {expect}")
        elif item["kind"] == "clip":
            d = probe(ffprobe, path)
            for key in ("duration_s", "width", "height", "codec"):
                if key == "duration_s":
                    if abs(d[key] - item["derived"][key]) > 0.05:
                        errors.append(f"{item['id']} {key} {d[key]} != {item['derived'][key]}")
                elif d[key] != item["derived"][key]:
                    errors.append(f"{item['id']} {key} {d[key]} != {item['derived'][key]}")
        if tmp:
            shutil.rmtree(tmp.parent, ignore_errors=True)
    if missing and not allow_missing:
        errors.append(f"{missing} pinned file(s) absent from {CACHE}; run 'build' or pass --allow-missing")
    print(f"verified {checked} file(s), {missing} missing, {len(errors)} error(s)")
    for msg in errors:
        print(f"  ERROR {msg}")
    return errors


def load(path=MANIFEST):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(manifest, path=MANIFEST):
    manifest["generated_at_utc"] = manifest.get("generated_at_utc") or time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=False)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "build", "verify", "validate"])
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--jobs", type=int, default=4, help="parallel ffmpeg derivations (output is unaffected)")
    ap.add_argument("--force", action="store_true", help="re-derive clips already in the cache")
    ap.add_argument("--rederive", action="store_true", help="verify: re-derive clips missing from the cache")
    ap.add_argument("--allow-missing", action="store_true", help="verify: absent cache files are not errors")
    a = ap.parse_args(argv)
    path = Path(a.manifest)
    if a.command == "plan":
        m = plan()
        if path.exists():                      # keep measured fields for unchanged recipes
            old = {c["id"]: c for c in load(path)["clips"] + load(path)["negatives"]}
            for item in m["clips"] + m["negatives"]:
                prev = old.get(item["id"])
                if prev and prev.get("recipe") == item.get("recipe") and prev["status"] == "built":
                    item["derived"], item["status"] = prev["derived"], prev["status"]
        save(m, path)
        print(f"planned {len(m['clips'])} clips ({sum('fast' in c['subset'] for c in m['clips'])} fast), "
              f"{len(m['negatives'])} negatives -> {path}")
        return 0
    if a.command == "build":
        m = build(load(path), jobs=a.jobs, force=a.force)
        save(m, path)
        built = sum(c["status"] == "built" for c in m["clips"])
        print(f"built {built}/{len(m['clips'])} clips, "
              f"{sum(n['status'] == 'built' for n in m['negatives'])}/{len(m['negatives'])} negatives")
        return 0 if not validate_manifest(m) else 1
    if a.command == "validate":
        errors = validate_manifest(load(path))
        print(f"{len(errors)} error(s)")
        for msg in errors:
            print(f"  ERROR {msg}")
        return 1 if errors else 0
    return 1 if verify(load(path), rederive=a.rederive, allow_missing=a.allow_missing) else 0


if __name__ == "__main__":
    sys.exit(main())
