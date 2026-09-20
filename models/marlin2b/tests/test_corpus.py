#!/usr/bin/env python3
"""Manifest schema and distinctness tests. No media and no network needed: they
read models/marlin2b/corpus/manifest.json only.

    python -m pytest models/marlin2b/tests -q
    python models/marlin2b/tests/test_corpus.py
"""
import copy, json, os, sys

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


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
