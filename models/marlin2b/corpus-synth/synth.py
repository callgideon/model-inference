#!/usr/bin/env python3
"""Build and verify `sop-synth-v1`: the owned synthetic SOP fixture (task E1B.a).

    python models/marlin2b/corpus-synth/synth.py plan      # rewrite recipes (status unbuilt)
    python models/marlin2b/corpus-synth/synth.py validate  # schema/step logic only, no media
    python models/marlin2b/corpus-synth/synth.py build     # render, probe, fill hashes
    python models/marlin2b/corpus-synth/synth.py verify    # recompute hashes and compare

Why it exists (research/workloads/marlin-sop.md §3.8): the SOP journey and the
temporal-event plumbing have to be exercisable with no copyrighted, customer or
robotics-vendor footage, and with ground truth that exists *by construction*.

**The step script is the renderer's INPUT.** `{step_id, canonical_index, label,
start_s, end_s}` is what the `drawbox` windows are built from, so the labels are not
annotations anybody has to trust — they are the numbers the pixels were made from.

Rendered by the same pinned `ffmpeg 7.0.2-amd64-static` that
`models/marlin2b/corpus/build.py` fetches and sha256-verifies, with the same
bit-exact flags and no font or other host input, so each clip's sha256 is a real
pin. **No media is committed**; clips live under $CORPUS_CACHE next to the
licensed corpus. See REQUIRED_FILTERS for the one documented deviation from §3.8.

A score on `sop-synth-v1` is NEVER an accuracy claim about real robotics video
(marlin-sop.md §5): it has no scene richness, no camera motion and no real
procedure. P-07 lists what a real accuracy claim would need.
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
CORPUS = HERE.parent / "corpus"
sys.path.insert(0, str(CORPUS))

import build as corpus                      # noqa: E402  the ffmpeg pin, probe and hashing

SCHEMA_VERSION = 1
FIXTURE_VERSION = "sop-synth-v1"
MEDIA_SUBDIR = "sop-synth-v1"
API_MAX_DURATION_S = corpus.API_MAX_DURATION_S

# The pinned preprocessing profile (marlin-sop.md §1.5): every clip records the frame
# count and whole-clip pixel budget this profile derives from its duration, so a token
# expectation is in the manifest instead of being recomputed by each reader.
PROFILE = {"version": "v1", "fps": 2.0, "min_frames": 4, "max_frames": 240,
           "px_per_frame": 200704, "shortest_edge": 4096}

# Every filter the recipe uses, checked against `ffmpeg -filters` before rendering so a
# missing one is named instead of surfacing as "Filter not found" at output-open time.
#
# DEVIATION from marlin-sop.md §3.8, with its reason: §3.8 describes "a text overlay naming
# the current step" via `drawtext`, and the pinned ffmpeg 7.0.2-amd64-static build does not
# ship drawtext at all (`ffmpeg -filters` lists 494 filters and none of them is drawtext;
# ffmpeg 7.0 made libharfbuzz a hard requirement for it and this static build has only
# libfreetype/fontconfig). Re-pinning the encoder to gain a cosmetic overlay would throw
# away the corpus's verified sha256 pin, so the step label is encoded in the PIXELS instead:
# a per-step part colour plus a tally of `canonical_index + 1` marks. The ground truth is
# unaffected — it was never the overlay, it is the script the renderer is given — and the
# fixture needs no font, so it reproduces on any host with the pinned binary.
REQUIRED_FILTERS = ("color", "drawbox")

# One procedure, eight canonical steps. A clip performs a prefix of it.
STEP_LABELS = ("fetch tray", "place housing", "align bracket", "drive screw",
               "apply seal", "close lid", "scan label", "stow tray")
# Part colours, one per canonical index: the "part" a step works on is a coloured box.
PART_COLOURS = ("0xE05A4E", "0x4EA8E0", "0x8FD14F", "0xE0C34E",
                "0xB06AE0", "0x4EE0C3", "0xE07FB0", "0x9AA0A6")

# 12 clips: durations 8/30/60/115 s, three each (marlin-sop.md §3.8). 115 s is inside the
# 120 s API cap and is the worst case for the frame budget in this fixture.
DURATIONS_S = (8, 30, 60, 115)
GEOMETRIES = ((640, 360), (854, 480), (1280, 720))
FPS = (10, 15, 30)
# Steps per clip: 3..7, except the declared out-of-order clip, which §3.8 specifies as a
# 2-step clip. validate_manifest() holds every other clip to 3..7.
STEPS_N = (3, 4, 3, 4, 2, 5, 5, 6, 6, 7, 7, 4)
# The client's own segmentation edge for a long recording (§3.2). Declared, not guessed:
# the boundary-spanning case is only meaningful against a stated convention.
SEGMENT_BOUNDARY_S = 60.0
# index -> the one property that clip exists to exercise.
CASES = {1: "steps_within_one_second", 4: "steps_out_of_order",
         7: "step_absent", 10: "step_spans_segment_boundary"}
NEAR_GAP_S = 0.4        # below the 0.5 s frame period of profile v1: a resolution probe
ABSENT_CANONICAL_INDEX = 2
BOUNDARY_HALF_S = 1.5

ENCODE = {"codec": "libx264", "preset": "veryfast", "crf": 26, "pix_fmt": "yuv420p", "gop": 48,
          "threads": 1, "bitexact": True, "audio": "none (synthetic source has none)"}

PROMPTS = [
    {"id": "sop00", "kind": "steps",
     "text": "List every step of the procedure you can see, in the order they happen, "
             "with start and end times in seconds."},
    {"id": "sop01", "kind": "find",
     "text": "From when to when is the operator working on the bracket?"},
    {"id": "sop02", "kind": "absence",
     "text": "Which of these steps does NOT happen in this clip: place housing, align "
             "bracket, drive screw, apply seal?"},
]


# ---------------------------------------------------------------- plan


def profile_frames(duration_s):
    """Frames profile v1 samples from a clip of this duration, rounded up to even."""
    frames = int(min(PROFILE["max_frames"],
                     max(PROFILE["min_frames"], round(duration_s * PROFILE["fps"]))))
    return frames + frames % 2


def step_windows(duration_s, n):
    """n evenly spaced [start, end] windows inside the clip, each step filling half of
    its window. Half a window of silence between steps keeps every ordinary clip's gaps
    comfortably above the 0.5 s frame period, so the `steps_within_one_second` clip is
    the only one that probes timestamp resolution."""
    usable = duration_s - 0.8
    window = usable / n
    out = []
    for i in range(n):
        start = round(0.4 + i * window, 1)
        out.append([start, round(start + max(1.0, window * 0.5), 1)])
    return out


def clip_steps(index, clip_id, duration_s, n, case):
    """The step script: the renderer's input, and therefore the ground truth."""
    windows = step_windows(duration_s, n)
    canonical = list(range(n))
    absent = []
    if case == "steps_out_of_order":
        # Same two windows, canonical order reversed in time: the procedure's second step
        # is performed first. A model that only lists steps in canonical order is wrong here.
        canonical = list(reversed(canonical))
    if case == "steps_within_one_second":
        # Pull step 1 back until it starts NEAR_GAP_S after step 0 ends.
        length = round(windows[1][1] - windows[1][0], 1)
        windows[1] = [round(windows[0][1] + NEAR_GAP_S, 1),
                      round(windows[0][1] + NEAR_GAP_S + length, 1)]
    if case == "step_spans_segment_boundary":
        # The first step that starts after the boundary is moved to straddle it, without
        # closing the gap to its predecessor to within the 1 s resolution probe: only the
        # steps_within_one_second clip may sit that close.
        for i, (start, _) in enumerate(windows):
            if start > SEGMENT_BOUNDARY_S:
                floor = windows[i - 1][1] + 2.0 if i else 0.4
                windows[i] = [round(max(SEGMENT_BOUNDARY_S - BOUNDARY_HALF_S, floor), 1),
                              round(SEGMENT_BOUNDARY_S + BOUNDARY_HALF_S, 1)]
                break
    steps = []
    for position, (canon, (start, end)) in enumerate(zip(canonical, windows)):
        steps.append({"step_id": f"{clip_id}-s{canon}", "canonical_index": canon,
                      "label": STEP_LABELS[canon], "part_colour": PART_COLOURS[canon],
                      "start_s": start, "end_s": end, "position": position})
    if case == "step_absent":
        # Declared missing, not rendered: a rubric can be shown to punish a hallucinated
        # span, because `.find` "always emits some span" (architecture.md §12 #11).
        absent = [{"step_id": f"{clip_id}-s{ABSENT_CANONICAL_INDEX}",
                   "canonical_index": ABSENT_CANONICAL_INDEX,
                   "label": STEP_LABELS[ABSENT_CANONICAL_INDEX],
                   "why": "never performed in this clip; no span exists for it"}]
        steps = [s for s in steps if s["canonical_index"] != ABSENT_CANONICAL_INDEX]
        for position, step in enumerate(steps):
            step["position"] = position
    return steps, absent


def spanning_ids(steps, duration_s):
    """The steps that cross the declared segmentation edge, if this clip is long enough
    to be segmented at all."""
    if duration_s <= SEGMENT_BOUNDARY_S:
        return []
    return [s["step_id"] for s in steps
            if s["start_s"] < SEGMENT_BOUNDARY_S < s["end_s"]]


def plan():
    clips = []
    for i in range(12):
        duration = DURATIONS_S[i // 3]
        width, height = GEOMETRIES[i % 3]
        fps = FPS[i % 3]
        case = CASES.get(i)
        cid = f"sop{i:02d}-{duration}s-{width}x{height}" + (f"-{case}" if case else "")
        steps, absent = clip_steps(i, cid, duration, STEPS_N[i], case)
        clips.append({
            "id": cid, "case": case, "duration_s": duration, "width": width, "height": height,
            "fps": fps, "prompt": PROMPTS[i % len(PROMPTS)]["id"],
            "segment_boundary_s": SEGMENT_BOUNDARY_S if duration > SEGMENT_BOUNDARY_S else None,
            "steps": steps, "absent_steps": absent,
            # A fact about the script, recorded rather than left to a reader to notice: a
            # step that straddles the declared segmentation edge is seen twice or truncated
            # (§3.2), and correlated segments are not independent samples.
            "boundary_spanning_step_ids": spanning_ids(steps, duration),
            "frames_profile_v1": profile_frames(duration),
            "longest_edge_profile_v1": profile_frames(duration) * PROFILE["px_per_frame"],
            "recipe": {"source": "lavfi color + drawbox + drawtext", "encode": dict(ENCODE)},
            "file": f"{MEDIA_SUBDIR}/{cid}.mp4",
            "derived": None, "status": "unbuilt"})
    return {
        "schema_version": SCHEMA_VERSION, "fixture_version": FIXTURE_VERSION,
        "generated_at_utc": None, "built_at_utc": None,
        "cache_root_env": corpus.CACHE_ROOT_ENV if hasattr(corpus, "CACHE_ROOT_ENV")
        else "CORPUS_CACHE",
        "cache_root_default": ".claude/corpus-cache", "media_subdir": MEDIA_SUBDIR,
        "purpose": "SOP journey, temporal-event plumbing and evaluator-harness fixture. "
                   "NOT a quality benchmark: a score here is not an accuracy claim "
                   "(marlin-sop.md §5).",
        "ground_truth": "the step script is the renderer's input; labels and spans are "
                        "inputs, not annotations",
        "rights": "wholly generated from lavfi sources: no people, no logos, no real "
                  "footage, no customer or vendor data",
        "api_limits": {"max_clip_duration_s": API_MAX_DURATION_S,
                       "max_pixels_per_frame": PROFILE["px_per_frame"]},
        "profile": dict(PROFILE), "ffmpeg": corpus.FFMPEG_PIN,
        "required_filters": list(REQUIRED_FILTERS),
        "label_encoding": "no text overlay: the pinned ffmpeg build ships no drawtext filter "
                          "(ffmpeg 7.0 needs libharfbuzz), so a step is identified in the "
                          "pixels by its part colour and a tally of canonical_index + 1 marks",
        "encode_defaults": dict(ENCODE), "segment_boundary_s": SEGMENT_BOUNDARY_S,
        "prompts": PROMPTS, "clips": clips,
    }


# ---------------------------------------------------------------- render


def cache_root():
    return Path(os.environ.get("CORPUS_CACHE") or corpus.default_cache_root())


def available_filters(ffmpeg):
    r = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True,
                       errors="replace")
    return {line.split()[1] for line in r.stdout.splitlines()
            if len(line.split()) > 2 and "->" in line}


def ensure_tools(allow_download=False):
    """(ffmpeg, ffprobe) or exit naming the EXACT missing prerequisite.

    Nothing is downloaded unless asked: the pinned tarball is 41.9 MB from a third-party
    host, and a test or a dry run must fail loudly instead of reaching the network.
    """
    base = cache_root() / "tools" / corpus.FFMPEG_PIN["dir"]
    ffmpeg, ffprobe = base / "ffmpeg", base / "ffprobe"
    if not (ffmpeg.exists() and ffprobe.exists()) and not allow_download:
        sys.exit(
            f"sop-synth-v1 cannot render: pinned ffmpeg {corpus.FFMPEG_PIN['version']} "
            f"(ffmpeg sha256 {corpus.FFMPEG_PIN['ffmpeg_sha256']}) is not at {base}. Fetch it "
            f"once with `python models/marlin2b/corpus/build.py build` on a host with network "
            f"access, or re-run this with --allow-download. Nothing else is missing.")
    ffmpeg, ffprobe = corpus.ensure_ffmpeg(download=allow_download)   # verifies both digests
    have = available_filters(ffmpeg)
    absent = [name for name in REQUIRED_FILTERS if name not in have]
    if absent:
        sys.exit(f"sop-synth-v1 cannot render: the pinned ffmpeg build has no "
                 f"{', '.join(absent)} filter ({len(have)} filters available). The recipe and "
                 f"the pin have to agree; do not re-pin the encoder to gain a filter without "
                 f"re-deriving models/marlin2b/corpus/manifest.json as well.")
    return ffmpeg, ffprobe


def filters_for(clip):
    """The filtergraph: a static work cell, one coloured part box per step, a tally naming
    the step's canonical index, and a gripper that travels across the frame. Every `enable`
    window is that step's own start/end, which is what makes the script the ground truth."""
    w, h = clip["width"], clip["height"]
    box = max(24, w // 12)
    mark = max(6, box // 6)
    chain = [f"drawbox=x=0:y={h - h // 6}:w={w}:h={h // 6}:color=0x2A2E33:t=fill"]
    for step in clip["steps"]:
        start, end = step["start_s"], step["end_s"]
        window = f"between(t\\,{start}\\,{end})"
        chain.append(f"drawbox=x={w // 8 + (step['canonical_index'] % 4) * box * 2}:"
                     f"y={h // 3}:w={box}:h={box}:color={step['part_colour']}:t=fill:"
                     f"enable='{window}'")
        # The label, in pixels: canonical_index + 1 marks along the bottom bar.
        for n in range(step["canonical_index"] + 1):
            chain.append(f"drawbox=x={w // 16 + n * mark * 2}:y={h - h // 8}:"
                         f"w={mark}:h={mark * 2}:color=0xFFFFFF:t=fill:enable='{window}'")
        # The gripper travels left to right across the step's own window.
        chain.append(f"drawbox=x='{w // 10}+({w * 3 // 4})*(t-{start})/{max(end - start, 0.1)}':"
                     f"y={h // 8}:w={box // 2}:h={box // 2}:color=0xFFFFFF:t=fill:"
                     f"enable='{window}'")
    return ",".join(chain)


def ffmpeg_args(ffmpeg, clip, dst):
    """Deterministic: the same ffmpeg and recipe give byte-identical output."""
    return [ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i",
            f"color=c=0x14181C:s={clip['width']}x{clip['height']}:r={clip['fps']}:"
            f"d={clip['duration_s']}",
            "-vf", filters_for(clip), "-an",
            "-c:v", ENCODE["codec"], "-preset", ENCODE["preset"], "-crf", str(ENCODE["crf"]),
            "-pix_fmt", ENCODE["pix_fmt"], "-g", str(ENCODE["gop"]),
            "-threads", str(ENCODE["threads"]),
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-movflags", "+faststart",
            "-f", "mp4", str(dst)]


def item_path(clip):
    return cache_root() / clip["file"]


def build(manifest, force=False, allow_download=False):
    ffmpeg, ffprobe = ensure_tools(allow_download)
    for clip in manifest["clips"]:
        dst = item_path(clip)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if force or not dst.exists() or clip["status"] != "built":
            r = subprocess.run(ffmpeg_args(ffmpeg, clip, dst),
                               capture_output=True, text=True, errors="replace")
            if r.returncode != 0:
                clip["status"], clip["derived"] = "unbuilt", None
                clip["error"] = f"ffmpeg: {r.stderr.strip()[-200:]}"
                print(f"  clip {clip['id']} unbuilt: {clip['error']}", file=sys.stderr)
                continue
        derived = corpus.probe(ffprobe, dst)
        derived["sha256"] = corpus.sha256_file(dst)
        pinned = (clip.get("derived") or {}).get("sha256")
        if pinned and not force and derived["sha256"] != pinned:
            clip["status"] = "sha256_mismatch"                 # never silently repin
            clip["error"] = f"rendered sha256 {derived['sha256']} != pinned {pinned}"
            print(f"  clip {clip['id']}: {clip['error']}", file=sys.stderr)
            continue
        clip.pop("error", None)
        clip["status"], clip["derived"] = "built", derived
    manifest["built_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return manifest


# ---------------------------------------------------------------- validate / verify


def validate_manifest(m):
    """Schema, step-script and case checks. Returns human-readable errors; needs no media."""
    e = []
    if m.get("schema_version") != SCHEMA_VERSION:
        e.append(f"schema_version {m.get('schema_version')} != {SCHEMA_VERSION}")
    for key in ("fixture_version", "ffmpeg", "profile", "clips", "prompts",
                "api_limits", "ground_truth", "rights"):
        if not m.get(key):
            e.append(f"missing {key}")
    if e:
        return e
    if m["ffmpeg"] != corpus.FFMPEG_PIN:
        e.append("ffmpeg identity drifted from the corpus pin")
    prompts = {p["id"] for p in m["prompts"]}
    clips = m["clips"]
    if len(clips) != 12:
        e.append(f"{len(clips)} clips, need exactly 12")
    if len({c["id"] for c in clips}) != len(clips):
        e.append("duplicate clip ids")
    if sorted({c["duration_s"] for c in clips}) != sorted(DURATIONS_S):
        e.append(f"durations {sorted({c['duration_s'] for c in clips})} != {sorted(DURATIONS_S)}")
    cases, hashes = {}, {}
    for c in clips:
        if c["duration_s"] > m["api_limits"]["max_clip_duration_s"]:
            e.append(f"{c['id']} duration {c['duration_s']}s exceeds the API cap")
        if c["prompt"] not in prompts:
            e.append(f"{c['id']} references unknown prompt {c['prompt']}")
        if c["frames_profile_v1"] != profile_frames(c["duration_s"]):
            e.append(f"{c['id']} frames_profile_v1 {c['frames_profile_v1']} != "
                     f"{profile_frames(c['duration_s'])} for {c['duration_s']}s at "
                     f"{m['profile']['fps']} fps")
        if c["longest_edge_profile_v1"] != c["frames_profile_v1"] * m["profile"]["px_per_frame"]:
            e.append(f"{c['id']} longest_edge_profile_v1 is not frames x "
                     f"{m['profile']['px_per_frame']}")
        if c["case"]:
            cases[c["case"]] = c["id"]
        steps = c["steps"]
        expected = 2 if c["case"] == "steps_out_of_order" else None
        if expected and len(steps) != expected:
            e.append(f"{c['id']} is the out-of-order case and needs exactly 2 steps")
        if not expected and not 3 <= len(steps) <= 7:
            e.append(f"{c['id']} has {len(steps)} steps, need 3..7")
        if len({s["step_id"] for s in steps}) != len(steps):
            e.append(f"{c['id']} has duplicate step ids")
        for s in steps:
            if not 0 <= s["start_s"] < s["end_s"] <= c["duration_s"]:
                e.append(f"{c['id']} step {s['step_id']} window {s['start_s']}-{s['end_s']} "
                         f"is not inside 0..{c['duration_s']}")
            if s["label"] != STEP_LABELS[s["canonical_index"]]:
                e.append(f"{c['id']} step {s['step_id']} label {s['label']!r} does not match "
                         f"canonical index {s['canonical_index']}")
        for a, b in zip(steps, steps[1:]):
            if b["start_s"] < a["end_s"]:
                e.append(f"{c['id']} steps {a['step_id']} and {b['step_id']} overlap in time")
        gaps = [round(b["start_s"] - a["end_s"], 3) for a, b in zip(steps, steps[1:])]
        # The threshold that means something is the profile's own frame period: at 2 fps a
        # gap below 0.5 s can fall entirely between two sampled frames. "<= 1 s" (§3.8's
        # wording) is the looser reading, and ordinary clips satisfy it by plain spacing.
        period = 1.0 / m["profile"]["fps"]
        inside = [g for g in gaps if g < period]
        if c["case"] == "steps_within_one_second":
            if not [g for g in gaps if g <= 1.0] or not inside:
                e.append(f"{c['id']} is the timestamp-resolution case but its closest steps "
                         f"are {min(gaps, default=None)}s apart, not <= 1s and inside one "
                         f"{period}s frame period")
        elif inside:
            e.append(f"{c['id']} has steps {inside}s apart, inside one {period}s frame "
                     f"period: only the declared steps_within_one_second clip may")
        order = [s["canonical_index"] for s in steps]
        if c["case"] == "steps_out_of_order":
            if order == sorted(order):
                e.append(f"{c['id']} is the out-of-order case but its steps run in "
                         f"canonical order")
        elif order != sorted(order):
            e.append(f"{c['id']} steps run out of canonical order without declaring the case")
        if c["case"] == "step_absent":
            if len(c["absent_steps"]) != 1:
                e.append(f"{c['id']} is the absent-step case and needs exactly one absent step")
            for a in c["absent_steps"]:
                if a["canonical_index"] in order:
                    e.append(f"{c['id']} declares {a['step_id']} absent but also renders it")
        elif c["absent_steps"]:
            e.append(f"{c['id']} declares absent steps without being the step_absent case")
        boundary = c["segment_boundary_s"]
        spanning = spanning_ids(steps, c["duration_s"])
        # Every crossing is recorded, declared case or not: a crossing nobody wrote down is
        # how correlated segments end up counted as independent samples (§3.2).
        if c.get("boundary_spanning_step_ids") != spanning:
            e.append(f"{c['id']} lists boundary_spanning_step_ids "
                     f"{c.get('boundary_spanning_step_ids')} but its script crosses "
                     f"{boundary}s at {spanning}")
        if c["case"] == "step_spans_segment_boundary" and not spanning:
            e.append(f"{c['id']} is the boundary case but no step crosses {boundary}s")
        if c["status"] == "built":
            d = c.get("derived") or {}
            if not d.get("sha256"):
                e.append(f"{c['id']} marked built without a derived sha256")
                continue
            if d["sha256"] in hashes:
                e.append(f"{c['id']} has the same content as {hashes[d['sha256']]}")
            hashes[d["sha256"]] = c["id"]
            if (d.get("width"), d.get("height")) != (c["width"], c["height"]):
                e.append(f"{c['id']} probed {d.get('width')}x{d.get('height')} != declared "
                         f"{c['width']}x{c['height']}")
            if abs((d.get("duration_s") or 0) - c["duration_s"]) > 0.2:
                e.append(f"{c['id']} probed duration {d.get('duration_s')} != declared "
                         f"{c['duration_s']}")
            if d.get("fps") and abs(d["fps"] - c["fps"]) > 0.01:
                e.append(f"{c['id']} probed fps {d['fps']} != declared {c['fps']}")
        elif c.get("derived"):
            e.append(f"{c['id']} is {c['status']} but carries derived metadata")
    for case in CASES.values():
        if case not in cases:
            e.append(f"no clip exercises the {case} case")
    if not [c for c in clips if c["duration_s"] == max(DURATIONS_S)]:
        e.append("no worst-case clip at the longest declared duration")
    if sorted(m.get("required_filters") or []) != sorted(REQUIRED_FILTERS):
        e.append("required_filters drifted from the recipe")
    return e


def verify(manifest, allow_missing=False):
    _, ffprobe = ensure_tools(allow_download=False)
    errors = validate_manifest(manifest)
    checked = missing = 0
    for clip in manifest["clips"]:
        if clip["status"] != "built":
            continue
        path = item_path(clip)
        if not path.exists():
            missing += 1
            print(f"{clip['id']}: not in cache", file=sys.stderr)
            continue
        checked += 1
        got = corpus.sha256_file(path)
        if got != clip["derived"]["sha256"]:
            errors.append(f"{clip['id']} sha256 {got} != manifest {clip['derived']['sha256']}")
            continue
        d = corpus.probe(ffprobe, path)
        for key in ("width", "height", "codec"):
            if d[key] != clip["derived"][key]:
                errors.append(f"{clip['id']} {key} {d[key]} != {clip['derived'][key]}")
    if missing and not allow_missing:
        errors.append(f"{missing} pinned file(s) absent from {cache_root()}; run 'build' or "
                      f"pass --allow-missing")
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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "build", "validate", "verify"])
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--force", action="store_true", help="re-render clips already in the cache")
    ap.add_argument("--allow-download", action="store_true",
                    help="permit fetching the pinned ffmpeg tarball (41.9 MB, third-party host)")
    ap.add_argument("--allow-missing", action="store_true",
                    help="verify: absent cache files are not errors")
    a = ap.parse_args(argv)
    path = Path(a.manifest)
    if a.command == "plan":
        m = plan()
        if path.exists():                  # keep the hashes of clips whose recipe is unchanged
            old = {c["id"]: c for c in load(path)["clips"]}
            for clip in m["clips"]:
                prev = old.get(clip["id"])
                if prev and prev["status"] == "built" and prev.get("steps") == clip["steps"] \
                        and prev.get("recipe") == clip["recipe"]:
                    clip["derived"], clip["status"] = prev["derived"], prev["status"]
            previous = load(path)
            m["built_at_utc"] = previous.get("built_at_utc")
        save(m, path)
        print(f"planned {len(m['clips'])} clips "
              f"({sum(len(c['steps']) for c in m['clips'])} steps, "
              f"{sum(len(c['absent_steps']) for c in m['clips'])} declared absent) -> {path}")
        return 0
    if a.command == "build":
        m = build(load(path), force=a.force, allow_download=a.allow_download)
        save(m, path)
        built = sum(c["status"] == "built" for c in m["clips"])
        print(f"built {built}/{len(m['clips'])} clips")
        errors = validate_manifest(m)
        for msg in errors:
            print(f"  ERROR {msg}")
        return 0 if not errors else 1
    if a.command == "validate":
        errors = validate_manifest(load(path))
        print(f"{len(errors)} error(s)")
        for msg in errors:
            print(f"  ERROR {msg}")
        return 1 if errors else 0
    return 1 if verify(load(path), allow_missing=a.allow_missing) else 0


if __name__ == "__main__":
    sys.exit(main())
