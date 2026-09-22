#!/usr/bin/env python3
"""`sop-synth-v1` manifest, step-script and recipe tests (task E1B.a).

No media, no ffmpeg and no network: these read
models/marlin2b/corpus-synth/manifest.json and re-derive the plan in memory.

    python -m pytest models/marlin2b/tests -q
    python models/marlin2b/tests/test_corpus_synth.py
"""
import copy, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SYNTH = os.path.join(os.path.dirname(HERE), "corpus-synth")
sys.path[:0] = [HERE, SYNTH]

import synth

MANIFEST = json.load(open(os.path.join(SYNTH, "manifest.json"), encoding="utf-8"))


def case_clip(case, m=MANIFEST):
    return next(c for c in m["clips"] if c["case"] == case)


def plain_clip(m=MANIFEST):
    return next(c for c in m["clips"] if not c["case"])


# ------------------------------------------------------------------ manifest


def test_the_committed_manifest_is_exactly_what_plan_derives():
    """The generator is the source of truth; the manifest is its output, not hand-edited."""
    planned = {c["id"]: c for c in synth.plan()["clips"]}
    assert set(planned) == {c["id"] for c in MANIFEST["clips"]}
    for clip in MANIFEST["clips"]:
        fresh = planned[clip["id"]]
        for field in ("case", "duration_s", "width", "height", "fps", "steps", "absent_steps",
                      "segment_boundary_s", "boundary_spanning_step_ids", "frames_profile_v1",
                      "longest_edge_profile_v1", "recipe", "file", "prompt"):
            assert clip[field] == fresh[field], f"{clip['id']}.{field} drifted from plan()"
    assert MANIFEST["ffmpeg"] == synth.corpus.FFMPEG_PIN, "the ffmpeg pin drifted"
    assert MANIFEST["fixture_version"] == "sop-synth-v1"
    assert "not an accuracy claim" in MANIFEST["purpose"]


def test_the_manifest_validates_and_declares_all_four_cases():
    assert synth.validate_manifest(MANIFEST) == []
    assert len(MANIFEST["clips"]) == 12
    assert sorted(c["case"] for c in MANIFEST["clips"] if c["case"]) == sorted(
        ("steps_within_one_second", "steps_out_of_order", "step_absent",
         "step_spans_segment_boundary"))
    assert sorted({c["duration_s"] for c in MANIFEST["clips"]}) == [8, 30, 60, 120]
    assert max(c["duration_s"] for c in MANIFEST["clips"]) <= \
        MANIFEST["api_limits"]["max_clip_duration_s"]
    assert len({(c["width"], c["height"]) for c in MANIFEST["clips"]}) == 3
    steps = [s for c in MANIFEST["clips"] for s in c["steps"]]
    assert len({s["step_id"] for s in steps}) == len(steps), "step ids must be globally unique"
    assert all(2 <= len(c["steps"]) <= 7 for c in MANIFEST["clips"])


def test_each_case_clip_really_carries_its_property():
    near = case_clip("steps_within_one_second")
    gaps = [round(b["start_s"] - a["end_s"], 3) for a, b in zip(near["steps"], near["steps"][1:])]
    assert min(gaps) <= 1.0 and min(gaps) < 1.0 / MANIFEST["profile"]["fps"], \
        "the resolution probe must sit inside one profile-v1 frame period (0.5 s at 2 fps)"

    unordered = case_clip("steps_out_of_order")
    order = [s["canonical_index"] for s in unordered["steps"]]
    assert len(order) == 2 and order != sorted(order)
    assert unordered["steps"][0]["start_s"] < unordered["steps"][1]["start_s"], \
        "the steps are still listed in TIME order; only the canonical order is violated"

    absent = case_clip("step_absent")
    assert len(absent["absent_steps"]) == 1
    missing = absent["absent_steps"][0]
    assert missing["canonical_index"] not in [s["canonical_index"] for s in absent["steps"]]
    assert missing["label"] == synth.STEP_LABELS[missing["canonical_index"]]

    spanning = case_clip("step_spans_segment_boundary")
    edge = spanning["segment_boundary_s"]
    crossing = [s for s in spanning["steps"] if s["start_s"] < edge < s["end_s"]]
    assert len(crossing) == 1 and spanning["boundary_spanning_step_ids"] == \
        [crossing[0]["step_id"]]
    # Every other clip that happens to cross is recorded too, never silently.
    for clip in MANIFEST["clips"]:
        crossings = [s["step_id"] for s in clip["steps"]
                     if clip["segment_boundary_s"]
                     and s["start_s"] < clip["segment_boundary_s"] < s["end_s"]]
        assert clip["boundary_spanning_step_ids"] == crossings, clip["id"]


def test_frames_and_pixel_budget_follow_the_pinned_profile():
    assert synth.profile_frames(8) == 16 and synth.profile_frames(115) == 230
    assert synth.profile_frames(0.5) == 4, "the floor is min_frames, not zero"
    assert synth.profile_frames(200) == 240, "the ceiling is max_frames"
    # The 240-frame ceiling is first reached just above 119.25 s, and round-half-to-even
    # makes exactly 119.25 s give 238 — which is why the long clip is 120.0 s, not 115 s
    # (marlin-sop.md §3.8, corrected at f9853cc).
    assert synth.profile_frames(119.25) == 238 and synth.profile_frames(119.26) == 240
    assert synth.profile_frames(120) == 240 == synth.PROFILE["max_frames"]
    for clip in MANIFEST["clips"]:
        assert clip["frames_profile_v1"] == synth.profile_frames(clip["duration_s"])
        assert clip["longest_edge_profile_v1"] == \
            clip["frames_profile_v1"] * MANIFEST["profile"]["px_per_frame"]
    longest = max(MANIFEST["clips"], key=lambda c: c["duration_s"])
    assert longest["duration_s"] == 120 == MANIFEST["api_limits"]["max_clip_duration_s"]
    assert longest["frames_profile_v1"] == 240 == MANIFEST["profile"]["max_frames"], \
        "the long clip is the real 240-frame worst case, not a 230-frame near miss"


# ------------------------------------------------------------------ the validator itself


def broken(mutate):
    m = copy.deepcopy(MANIFEST)
    mutate(m)
    return synth.validate_manifest(m)


def test_the_validator_rejects_every_way_the_script_can_go_wrong():
    def overlap(m):
        clip = plain_clip(m)
        clip["steps"][1]["start_s"] = clip["steps"][0]["end_s"] - 0.1
    assert any("overlap in time" in e for e in broken(overlap))

    def undeclared_near_gap(m):
        clip = plain_clip(m)
        clip["steps"][1]["start_s"] = round(clip["steps"][0]["end_s"] + 0.3, 1)
    assert any("frame period: only the declared" in e for e in broken(undeclared_near_gap))

    def no_near_gap(m):
        # the same clip with ordinary, evenly spaced windows: the case stops being one
        clip = case_clip("steps_within_one_second", m)
        clip["steps"], _ = synth.clip_steps(0, clip["id"], clip["duration_s"],
                                            len(clip["steps"]), None)
    assert any("not <= 1s and inside one" in e for e in broken(no_near_gap))

    def undeclared_disorder(m):
        clip = plain_clip(m)
        clip["steps"][0]["canonical_index"], clip["steps"][1]["canonical_index"] = \
            clip["steps"][1]["canonical_index"], clip["steps"][0]["canonical_index"]
        for s in clip["steps"]:
            s["label"] = synth.STEP_LABELS[s["canonical_index"]]
    assert any("out of canonical order without declaring" in e for e in broken(undeclared_disorder))

    def ordered_disorder_case(m):
        clip = case_clip("steps_out_of_order", m)
        clip["steps"][0]["canonical_index"], clip["steps"][1]["canonical_index"] = 0, 1
        for s in clip["steps"]:
            s["label"] = synth.STEP_LABELS[s["canonical_index"]]
    assert any("runs in canonical order" in e or "run in canonical order" in e
               for e in broken(ordered_disorder_case))

    def absent_but_rendered(m):
        clip = case_clip("step_absent", m)
        clip["absent_steps"][0]["canonical_index"] = clip["steps"][0]["canonical_index"]
    assert any("absent but also renders it" in e for e in broken(absent_but_rendered))

    def hidden_crossing(m):
        case_clip("step_spans_segment_boundary", m)["boundary_spanning_step_ids"] = []
    assert any("boundary_spanning_step_ids" in e for e in broken(hidden_crossing))

    def wrong_frames(m):
        plain_clip(m)["frames_profile_v1"] += 2
    assert any("frames_profile_v1" in e for e in broken(wrong_frames))

    def over_cap(m):
        plain_clip(m)["duration_s"] = 121
    assert any("exceeds the API cap" in e for e in broken(over_cap))

    def relabelled(m):
        plain_clip(m)["steps"][0]["label"] = "sweep floor"
    assert any("does not match" in e for e in broken(relabelled))

    def duplicate_content(m):
        m["clips"][1]["derived"]["sha256"] = m["clips"][0]["derived"]["sha256"]
    assert any("same content as" in e for e in broken(duplicate_content))

    def built_without_hash(m):
        plain_clip(m)["derived"]["sha256"] = None
    assert any("built without a derived sha256" in e for e in broken(built_without_hash))

    def probed_geometry_drift(m):
        plain_clip(m)["derived"]["width"] += 2
    assert any("!= declared" in e for e in broken(probed_geometry_drift))

    def unpinned_encoder(m):
        m["ffmpeg"] = {**m["ffmpeg"], "ffmpeg_sha256": "0" * 64}
    assert any("drifted from the corpus pin" in e for e in broken(unpinned_encoder))

    def too_few_clips(m):
        m["clips"] = m["clips"][:11]
    errors = broken(too_few_clips)
    assert any("need exactly 12" in e for e in errors)

    def unbuilt_with_metadata(m):
        clip = plain_clip(m)
        clip["status"] = "unbuilt"
    assert any("carries derived metadata" in e for e in broken(unbuilt_with_metadata))


# ------------------------------------------------------------------ the recipe


def test_the_render_command_is_bit_exact_and_derives_only_from_the_script():
    clip = case_clip("step_absent")
    args = synth.ffmpeg_args("/x/ffmpeg", clip, "/tmp/out.mp4")
    line = " ".join(args)
    assert args[:4] == ["/x/ffmpeg", "-hide_banner", "-nostdin", "-y"]
    for flag in ("-fflags +bitexact", "-flags:v +bitexact", "-threads 1", "-an",
                 "-preset veryfast", "-crf 26", "-pix_fmt yuv420p"):
        assert flag in line, f"{flag} missing: the sha256 in the manifest would not be a pin"
    assert f"d={clip['duration_s']}" in line and f"r={clip['fps']}" in line
    assert f"s={clip['width']}x{clip['height']}" in line
    # The ground truth IS the input: every rendered step's exact window is in the command,
    # and the step declared absent appears nowhere in it.
    for step in clip["steps"]:
        assert f"between(t\\,{step['start_s']}\\,{step['end_s']})" in line, step["step_id"]
        assert step["part_colour"] in line
    missing = clip["absent_steps"][0]
    assert synth.PART_COLOURS[missing["canonical_index"]] not in line, \
        "a step declared absent must not be drawn"
    assert "drawtext" not in line, \
        "the pinned build has no drawtext; the recipe may not depend on one"
    assert set(synth.REQUIRED_FILTERS) == {"color", "drawbox"}
    for name in synth.REQUIRED_FILTERS:
        assert name in line


def test_a_missing_prerequisite_names_the_exact_tool_and_never_reaches_the_network():
    old = os.environ.get("CORPUS_CACHE")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORPUS_CACHE"] = tmp                   # an empty cache: no pinned ffmpeg
        try:
            synth.ensure_tools()
            raise AssertionError("ensure_tools() rendered without the pinned ffmpeg")
        except SystemExit as e:
            message = str(e)
        finally:
            os.environ.pop("CORPUS_CACHE", None) if old is None else \
                os.environ.__setitem__("CORPUS_CACHE", old)
    assert synth.corpus.FFMPEG_PIN["ffmpeg_sha256"] in message
    assert synth.corpus.FFMPEG_PIN["version"] in message
    assert "models/marlin2b/corpus/build.py build" in message
    assert "--allow-download" in message
    assert tmp in message, "the message must name the path it looked in"


def test_committed_clip_pins_are_real_and_distinct():
    """Not a media test: it holds the committed manifest to its own claims."""
    built = [c for c in MANIFEST["clips"] if c["status"] == "built"]
    assert len(built) == 12, f"{len(built)} built clips; run synth.py build"
    hashes = [c["derived"]["sha256"] for c in built]
    assert len({h for h in hashes}) == 12 and all(len(h) == 64 for h in hashes)
    for clip in built:
        d = clip["derived"]
        assert (d["width"], d["height"]) == (clip["width"], clip["height"])
        assert abs(d["duration_s"] - clip["duration_s"]) <= 0.2
        assert d["codec"] == "h264" and d["bytes"] > 0
        assert d["frames"] == clip["duration_s"] * clip["fps"], \
            "the rendered frame count is duration x source fps, not the profile budget"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
