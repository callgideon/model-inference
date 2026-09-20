#!/usr/bin/env python3
"""Manifest schema and distinctness tests. No media and no network needed: they
read models/marlin2b/corpus/manifest.json only.

    python -m pytest models/marlin2b/tests -q
    python models/marlin2b/tests/test_corpus.py
"""
import copy, json, os, sys, tempfile
from hashlib import sha256
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "corpus")
sys.path[:0] = [HERE, CORPUS]

import build as corpus

MANIFEST = json.load(open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8"))


def built(m=MANIFEST):
    return [c for c in m["clips"] if c["status"] == "built"]


def test_manifest_validates_and_has_the_required_scale():
    assert corpus.validate_manifest(MANIFEST) == []
    assert len(built()) >= 64
    assert len([c for c in built() if "fast" in c["subset"]]) >= 32
    assert MANIFEST["schema_version"] == corpus.SCHEMA_VERSION and MANIFEST["corpus_version"]
    for key in ("version", "tarball_sha256", "ffmpeg_sha256", "ffprobe_sha256"):
        assert MANIFEST["ffmpeg"][key], f"ffmpeg identity missing {key}"


def test_clips_are_distinct_by_content_and_by_recipe():
    hashes = [c["derived"]["sha256"] for c in built()]
    assert len(set(hashes)) == len(hashes), "duplicate clip content"
    recipes = [(c["source"], c["recipe"]["start_s"], c["recipe"]["duration_s"], tuple(c["recipe"]["scale"]),
                c["recipe"]["fps"], c["recipe"]["transpose"]) for c in built()]
    assert len(set(recipes)) == len(recipes), "duplicate derivation recipe"
    spans = {}
    for c in built():                      # segments from one source must not overlap
        s, d = c["recipe"]["start_s"], c["recipe"]["duration_s"]
        for other_s, other_d in spans.setdefault(c["source"], []):
            assert s >= other_s + other_d or s + d <= other_s, f"{c['id']} overlaps another segment"
        spans[c["source"]].append((s, d))


def test_diversity_the_perf_oracle_needs():
    b = built()
    assert len({(c["derived"]["width"], c["derived"]["height"]) for c in b}) >= 10
    assert len({c["derived"]["aspect"] for c in b}) >= 6
    assert len({c["recipe"]["duration_s"] for c in b}) >= 8
    assert len({c["prompt"] for c in b}) >= 8
    assert len({c["source"] for c in b}) >= 3
    assert [c for c in b if c["derived"]["height"] == 1080 and c["derived"]["width"] >= 1920], \
        "1080p decode is the measured bottleneck; the corpus must contain 1080p"
    portrait = [c for c in b if c["derived"]["height"] > c["derived"]["width"]]
    square = [c for c in b if c["derived"]["height"] == c["derived"]["width"]]
    ultrawide = [c for c in b if c["derived"]["width"] >= 2 * c["derived"]["height"]]
    assert portrait and square and ultrawide
    durations = sorted(c["derived"]["duration_s"] for c in b)
    assert durations[0] <= 5 and durations[-1] >= 60
    assert durations[-1] <= MANIFEST["api_limits"]["max_clip_duration_s"], "clip exceeds the API cap"
    assert len({c["recipe"]["fps"] for c in b}) >= 3


def test_clip_ids_and_labels_match_the_probed_pixels():
    """The review found every `1080x1920-portrait` clip was really 1920x1080 and every
    `480x1920-extreme-tall` really 1920x480: rotation was keyed by geometry index, so it
    hit all four clips of a geometry and the label became a lie."""
    for c in built():
        w, h = c["derived"]["width"], c["derived"]["height"]
        label_wh = corpus.LABEL_GEOMETRY[c["geometry_label"]]
        assert tuple(c["recipe"]["scale"]) == label_wh, f"{c['id']} label != recipe scale"
        rot = c["recipe"]["transpose"]
        want = (label_wh[1], label_wh[0]) if rot in ("90cw", "90ccw") else label_wh
        assert (w, h) == want, f"{c['id']} is really {w}x{h}"
        assert c["derived"]["aspect"] == corpus.aspect(*want), f"{c['id']} aspect"
        assert c["id"].endswith(c["geometry_label"] + (f"-rot{rot}" if rot else "")), \
            f"{c['id']} does not name the geometry it has"

    have = {(c["derived"]["width"], c["derived"]["height"]) for c in built()}
    assert (1080, 1920) in have, "no real 1080x1920 portrait clip"
    assert (480, 1920) in have, "no real 1:4 extreme-tall clip"
    assert (1920, 480) in have, "no real 4:1 extreme-wide clip"
    ratios = [c["derived"]["width"] / c["derived"]["height"] for c in built()]
    assert min(ratios) <= 0.25 and max(ratios) >= 4.0, "orientation extremes are the MEDIA-PARITY case"
    assert [c for c in built() if c["recipe"]["transpose"]], "rotated pixels must still be covered"
    assert sorted({c["derived"]["fps"] for c in built()}) == [10.0, 15.0, 24.0, 30.0, 60.0], \
        "the README quotes this exact frame-rate set"


def test_source_upscaled_is_true_for_every_upscaled_axis():
    """The flag compared post-rotation height only, so 12 clips upscaled 1.5x-2.35x on
    the other axis were recorded as native resolution."""
    probed = {s["id"]: s["probed"] for s in MANIFEST["sources"]}
    upscaled = 0
    for c in built():
        src = probed[c["source"]]
        tw, th = c["recipe"]["scale"]                       # pre-rotation target
        factor = max(tw / src["width"], th / src["height"])  # what force_original_aspect_ratio=increase does
        assert c["derived"]["source_upscaled"] is (factor > 1.0001), \
            f"{c['id']} upscales {factor:.2f}x but source_upscaled={c['derived']['source_upscaled']}"
        upscaled += bool(c["derived"]["source_upscaled"])
    assert 0 < upscaled < len(built()), "the corpus must have both native and upscaled clips"


def test_validator_rejects_a_mislabelled_or_mis_flagged_clip():
    m = copy.deepcopy(MANIFEST)
    m["clips"][0]["derived"]["height"] = 999               # probed pixels no longer match the label
    assert any("!= recipe scale/transpose" in e for e in corpus.validate_manifest(m))

    m = copy.deepcopy(MANIFEST)
    m["clips"][0]["geometry_label"] = "480p-16x9"           # label lies about the recipe
    errors = corpus.validate_manifest(m)
    assert any("means 854x480" in e for e in errors), errors
    assert any("does not end with the geometry it claims" in e for e in errors), errors

    m = copy.deepcopy(MANIFEST)
    rot = [c for c in m["clips"] if c["recipe"]["transpose"]][0]
    rot["id"] = rot["id"].split("-rot")[0]                  # rotated clip hiding its rotation
    assert any("does not end with the geometry it claims" in e for e in corpus.validate_manifest(m))

    m = copy.deepcopy(MANIFEST)
    up = [c for c in m["clips"] if c["derived"]["source_upscaled"]][0]
    up["derived"]["source_upscaled"] = False
    assert any("source_upscaled False != True" in e for e in corpus.validate_manifest(m))

    m = copy.deepcopy(MANIFEST)
    m["sources"][0]["sha256"] = None                        # built clips from an unpinned source
    assert any("unpinned source" in e for e in corpus.validate_manifest(m))

    m = copy.deepcopy(MANIFEST)
    m["clips"][0]["status"] = "sha256_mismatch"             # build refused to repin drifted bytes
    m["clips"][0]["error"] = "cached bytes sha256 aa != pinned bb"
    assert any("cached bytes differ from the pinned sha256" in e
               for e in corpus.validate_manifest(m))


def test_every_source_carries_verified_licence_fields():
    for s in MANIFEST["sources"]:
        for key in ("title", "url", "license", "license_url", "license_evidence_url",
                    "license_evidence_quote", "attribution"):
            assert s.get(key), f"source {s['id']} missing {key}"
        assert s["license"] in ("CC-BY-3.0", "public-domain-usgov"), s["license"]
        if s["status"] == "fetched":
            assert len(s["sha256"]) == 64 and s["bytes"] > 0 and s["probed"]["duration_s"] > 0


def test_negative_fixtures_exist_for_media_failure_cases():
    negs = {n["id"]: n for n in MANIFEST["negatives"]}
    assert len(negs) >= 4
    for n in negs.values():
        assert n["expected_failure"] and n["status"] in ("built", "unbuilt")
        if n["status"] == "built":
            assert len(n["derived"]["sha256"]) == 64
    assert negs["neg-zero-length"]["derived"]["bytes"] == 0
    assert negs["neg-truncated-mp4"]["derived"]["bytes"] == \
        negs["neg-truncated-mp4"]["recipe"]["keep_bytes"]


def test_unbuilt_clips_are_never_counted_as_built():
    m = copy.deepcopy(MANIFEST)
    m["clips"][0]["status"], m["clips"][0]["derived"] = "unbuilt", None
    errors = corpus.validate_manifest(m)
    assert any("63 built clips" in e for e in errors), errors

    m = copy.deepcopy(MANIFEST)
    m["clips"][1]["status"] = "unbuilt"                 # keeps derived metadata: a lie about state
    assert any("carries derived metadata" in e for e in corpus.validate_manifest(m))

    m = copy.deepcopy(MANIFEST)
    m["clips"][2]["derived"]["sha256"] = m["clips"][3]["derived"]["sha256"]
    assert any("same content as" in e for e in corpus.validate_manifest(m))

    m = copy.deepcopy(MANIFEST)
    m["sources"][0]["license_evidence_url"] = ""
    assert any("missing license_evidence_url" in e for e in corpus.validate_manifest(m))

    m = copy.deepcopy(MANIFEST)
    m["clips"][4]["recipe"]["duration_s"] = 300
    assert any("exceeds the API cap" in e for e in corpus.validate_manifest(m))


def tiny_manifest(tmp):
    """One pinned source and one pinned clip in a scratch cache, both already 'built'."""
    src_bytes, clip_bytes = b"source-bytes", b"clip-bytes"
    (tmp / "sources").mkdir(parents=True)
    (tmp / "clips").mkdir(parents=True)
    (tmp / "sources" / "s.mp4").write_bytes(src_bytes)
    (tmp / "clips" / "c.mp4").write_bytes(clip_bytes)
    h = lambda b: sha256(b).hexdigest()
    return {
        "sources": [{"id": "s", "file": "s.mp4", "url": "https://example.invalid/s.mp4",
                     "sha256": h(src_bytes), "bytes": len(src_bytes), "status": "fetched",
                     "expected": {"duration_s": 10.0},
                     "probed": {"width": 1920, "height": 1080, "duration_s": 10.0}}],
        "clips": [{"id": "c", "kind": "clip", "source": "s", "file": "clips/c.mp4",
                   "geometry_label": "1080p-16x9", "prompt": "p00", "subset": ["full"],
                   "recipe": {"start_s": 0.0, "duration_s": 5, "scale": [1920, 1080], "fps": 30,
                              "transpose": None, "encode": {}},
                   "status": "built", "derived": {"sha256": h(clip_bytes), "width": 1920,
                                                  "height": 1080, "source_upscaled": False}}],
        "negatives": [],
    }


def test_build_never_silently_repins_drifted_bytes():
    """Cache bytes that no longer match the pin must be reported, not hashed into the
    manifest as the new truth -- for a clip as well as for a source."""
    saved = (corpus.CACHE, corpus.ensure_ffmpeg, corpus.probe)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        m = tiny_manifest(tmp)
        corpus.CACHE = tmp
        corpus.ensure_ffmpeg = lambda download=True: ("ffmpeg", "ffprobe")   # never invoked below
        corpus.probe = lambda ffprobe, path: {"width": 1920, "height": 1080, "duration_s": 5.0}
        try:
            pin = m["clips"][0]["derived"]["sha256"]
            out = corpus.build(copy.deepcopy(m))                 # unchanged bytes: still built
            assert out["clips"][0]["status"] == "built"
            assert out["clips"][0]["derived"]["sha256"] == pin

            (tmp / "clips" / "c.mp4").write_bytes(b"tampered")   # drifted clip
            out = corpus.build(copy.deepcopy(m))
            clip = out["clips"][0]
            assert clip["status"] == "sha256_mismatch", clip["status"]
            assert clip["derived"]["sha256"] == pin, "the pin must survive the drift"

            (tmp / "sources" / "s.mp4").write_bytes(b"tampered") # drifted source
            out = corpus.build(copy.deepcopy(m))
            assert out["sources"][0]["status"] == "sha256_mismatch"
            assert out["sources"][0]["sha256"] == m["sources"][0]["sha256"]
            assert out["clips"][0]["status"] == "unbuilt", "no clip may be built from it"
        finally:
            corpus.CACHE, corpus.ensure_ffmpeg, corpus.probe = saved


def test_plan_keeps_the_pins_and_the_build_time_of_a_built_manifest():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "manifest.json"
        corpus.save(copy.deepcopy(MANIFEST), path)
        assert corpus.main(["plan", "--manifest", str(path)]) == 0
        after = corpus.load(path)
        assert after["built_at_utc"] == MANIFEST["built_at_utc"], "plan must not unbuild the corpus"
        assert [s["sha256"] for s in after["sources"]] == [s["sha256"] for s in MANIFEST["sources"]]
        assert [(c["id"], c["status"], (c["derived"] or {}).get("sha256")) for c in after["clips"]] == \
               [(c["id"], c["status"], (c["derived"] or {}).get("sha256")) for c in MANIFEST["clips"]]
        assert corpus.validate_manifest(after) == []


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
