#!/usr/bin/env python3
"""M4 MEDIA-PARITY: a prepared clip is a function of its bytes and the profile, nothing else.

    uv run --frozen pytest -q tests/m/test_parity.py

For each clip, `support.prepared_facts` - the prepared ref, the digests of the durable
prepared object and of the local file the engine opens, that file's place under the cache
root, and the frame/pixel budget the engine derives from the measured duration - must be
equal across two runs, across the in-memory and filesystem object stores, across the URL
and `data:` forms, and between a preparation and a re-preparation after the processing
cache expired. Profile v1 re-encodes nothing, so the prepared bytes are the source bytes.

The synthetic cases always run (containers built in-process). The corpus case runs the
same oracle over the real encoder output of E1's 64-clip corpus and `sop-synth-v1`, and
compares the probe with the ffprobe-derived facts each manifest records; without the
corpus cache it is skipped with the path it looked for, which is not a pass.
"""
from __future__ import annotations

import asyncio
import base64
import json
import pathlib

import pytest
from infrx.contracts.limits import DEFAULTS
from infrx.media import fetch, prepare, store

from . import support
from .perf import harness

ORG = "1a1a1a1a-0000-4000-8000-00000000000a"
PX_PER_FRAME = 200_704


def _adapter(tmp: pathlib.Path, name: str, objects, body: bytes, clock):
    transport = support.Transport(lambda: support.response(body=body))
    fetcher = fetch.MediaFetcher(DEFAULTS, resolve=support.resolver([support.PUBLIC]),
                                 transport=transport.transport, log=support.Records())
    return prepare.MediaPreparation(
        objects, cache=prepare.ProcessingCache(str(tmp / name), clock=clock),
        limits=DEFAULTS, fetcher=fetcher, job_org=lambda job_id: ORG)


async def _runs(tmp: pathlib.Path, body: bytes, mime: str) -> dict[str, dict]:
    """The facts of one clip, prepared five ways."""
    url = "https://example.com/clips/v" + (".webm" if mime == "video/webm" else ".mp4")
    inline = f"data:{mime};base64,{base64.b64encode(body).decode()}"
    runs = {}
    for name, objects, source in (("memory-1", store.InMemoryObjectStore(), url),
                                  ("memory-2", store.InMemoryObjectStore(), url),
                                  ("fs", support.FileObjectStore(tmp / "objects"), url),
                                  ("inline", store.InMemoryObjectStore(), inline)):
        clock = harness.Clock()
        media = _adapter(tmp, name, objects, body, clock)
        runs[name] = await support.prepared_facts(media, ORG, source, "job-1")
        if name == "memory-1":
            clock.now += DEFAULTS.processing_cache_ttl_s       # the local copy expires
            runs["after-expiry"] = await support.prepared_facts(media, ORG, source, "job-2")
    return runs


def _assert_parity(runs: dict[str, dict]) -> dict:
    first = runs["memory-1"]
    for name, facts in runs.items():
        assert facts == first, f"{name} prepared differently from memory-1"
    # Profile v1: the durable prepared object and the file the engine opens are the source.
    assert first["prepared_digest"] == first["local_digest"] == first["source_digest"]
    budget = first["budget"]
    frames = budget["size"]["longest_edge"] // PX_PER_FRAME
    assert budget["size"]["longest_edge"] == frames * PX_PER_FRAME     # <= 200,704 px a frame
    assert 4 <= frames <= 240 and frames % 2 == 0
    return first


# (container, seconds, the engine's frame budget at profile v1)
CLIPS = {
    "mp4-10s": (support.mp4(seconds=10.0), "video/mp4", 20),
    # 10.5 s is 21 frames at 2 fps, rounded up to 22: one temporal patch is two frames.
    "mp4-10.5s-odd-frames": (support.mp4(seconds=10.5), "video/mp4", 22),
    "mp4-at-the-cap": (support.mp4(seconds=DEFAULTS.max_video_seconds, width=1920,
                                   height=480), "video/mp4", 240),
    "mov-1s-min-frames": (support.mp4(seconds=1.0, brand=b"qt  "), "video/quicktime", 4),
    "webm-30s-portrait": (support.webm(seconds=30.0, width=480, height=1920), "video/webm", 60),
}


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_a_prepared_clip_is_the_same_across_runs_stores_forms_and_expiry(name, tmp_path):
    body, mime, frames = CLIPS[name]
    first = _assert_parity(asyncio.run(_runs(tmp_path, body, mime)))
    assert first["ref"]["mime"] == mime and first["ref"]["bytes"] == len(body)
    assert first["budget"]["size"]["longest_edge"] == frames * PX_PER_FRAME
    assert first["local_path"].split("/")[:2] == [ORG, "v1"]


def _corpus() -> pathlib.Path | None:
    try:
        root = harness.default_corpus()
    except Exception:
        return None
    return root if root.is_dir() else None


def test_the_real_corpora_prepare_identically_and_agree_with_ffprobe(tmp_path):
    """E1's 64 clips and sop-synth-v1's 12, the real encoder output of the pinned ffmpeg."""
    root = _corpus()
    if root is None:
        pytest.skip("corpus cache not found ($CORPUS_CACHE or <main>/.claude/corpus-cache)")
    checked = []
    for manifest_path in harness.MANIFESTS.values():
        for clip in json.loads(manifest_path.read_text())["clips"]:
            path, derived = root / clip["file"], clip["derived"]
            if not path.exists():
                pytest.fail(f"{clip['id']} is in the manifest but not in {root}")
            body = path.read_bytes()
            first = _assert_parity(asyncio.run(_runs(tmp_path / clip["id"], body, "video/mp4")))
            assert first["source_digest"] == "sha256:" + derived["sha256"], clip["id"]
            # ffprobe (at corpus build time) and the header probe agree to the millisecond,
            # so the engine's budget is the one a decoder's duration would give.
            assert abs(first["ref"]["duration_s"] - derived["duration_s"]) < 1e-3, clip["id"]
            assert first["budget"] == support.BUDGET.budget_kwargs(derived["duration_s"])
            assert first["probed"] == {key: derived[key] for key in ("width", "height", "codec")}
            if "frames_profile_v1" in clip:
                assert first["budget"]["size"]["longest_edge"] == clip["longest_edge_profile_v1"]
            checked.append(clip["id"])
    assert len(checked) == 64 + 12
