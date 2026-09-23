#!/usr/bin/env python3
"""M2: preparation, the processing cache and the seam into W1's engine adapter.

    uv run --frozen pytest -q tests/m/test_prepare.py

Nothing here touches the network, the wall clock or a real decoder. The containers are
built byte by byte (`support.mp4`/`support.webm`), the fetches are M1's injected transport,
the cache lives in `tmp_path`, and the engine is W1's real adapter behind a mock transport.
"""
from __future__ import annotations

import asyncio
import base64
import os

import httpx
import pytest
from infrx.config import Settings
from infrx.contracts import errors
from infrx.contracts.conformance import SUITES, Harness, MissingHook
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FakeClock, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import Budgets, ExecutionMode, MediaKind, MediaRef, Work
from infrx.media import fetch, prepare, probe, store
from infrx.worker import engine as worker_engine

from . import support

CLIP = support.mp4(seconds=10.0)
WEBM = support.webm(seconds=30.0)
LONG = support.mp4(seconds=121.0)
URL = "https://example.com/clips/v.mp4?token=secretvalue"
OTHER_URL = "https://example.com/clips/second.mp4"
SOURCE_KEY_PARTS = 5


def data_url(body: bytes = CLIP, mime: str = "video/mp4") -> str:
    return f"data:{mime};base64," + base64.b64encode(body).decode()


class Clock:
    """A float clock the cache reads; tests move it by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Counting(store.InMemoryObjectStore):
    """An object store that says how often it was really read, so a test can prove that a
    cheap check ran *instead of* a 64 MiB download and not merely as well as one."""

    def __init__(self) -> None:
        super().__init__()
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        return await super().get(key)


def preparation(tmp_path=None, *, limits=DEFAULTS, transport=None, bodies=(),
                probe_fn=probe.probe, clock=None, objects=None, jobs=None,
                ttl_s=DEFAULTS.processing_cache_ttl_s, profile=None):
    """The real adapter with M1's injected collaborators and a cache under `tmp_path`."""
    jobs = {} if jobs is None else jobs
    if transport is None and bodies:
        transport = support.Transport(*[support.response(body=body) for body in bodies])
    fetcher = fetch.MediaFetcher(limits, resolve=support.resolver([support.PUBLIC]),
                                 transport=transport.transport if transport else None,
                                 monotonic=support.Ticker(), log=support.Records())
    cache = prepare.ProcessingCache(str(tmp_path) if tmp_path else "", ttl_s=ttl_s,
                                    clock=clock or Clock())
    adapter = prepare.MediaPreparation(
        objects or store.InMemoryObjectStore(), cache=cache, probe=probe_fn, profile=profile,
        limits=limits, fetcher=fetcher, job_org=lambda job_id: _org_of(jobs, job_id))
    adapter.jobs = jobs
    adapter.transport = transport
    adapter.harness = Harness(port=adapter, clock=FakeClock(), ids=SequentialIds())
    return adapter


def _org_of(jobs, job_id):
    org_id = jobs.get(job_id)
    if org_id is None:
        raise errors.NotFound(f"no job {job_id}")
    return org_id


def request_with(adapter, *sources, org_id=b.ORG_A, media=(), text="Describe this clip."):
    """A normalized request shaped as G1's validator leaves it: the caller's own urls
    still in the message parts, and `media` empty."""
    base = b.request(adapter.harness, org_id=org_id, refs=media)
    parts = [{"type": "text", "text": text}]
    parts += [{"type": "video_url", "video_url": {"url": source}} for source in sources]
    return base.model_copy(update={"messages": ({"role": "user", "content": parts},)})


def run(coroutine):
    return asyncio.run(coroutine)


# --- what preparation establishes ---------------------------------------------
def test_preparation_measures_the_clip_and_fills_the_record(tmp_path):
    """MEDIA-PARITY: the prepared record carries one ref per video part, in order, with the
    duration, type and size read from the stored bytes - and the customer's url is gone."""
    adapter = preparation(tmp_path, bodies=[CLIP])
    prepared = run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL)))
    assert len(prepared.media) == 1
    ref = prepared.media[0]
    assert ref.duration_s == pytest.approx(10.0)
    assert ref.mime == "video/mp4" and ref.bytes == len(CLIP)
    assert ref.digest == fetch.digest_of(CLIP)
    assert ref.storage_ref == f"media/{b.ORG_A}/v1/{ref.digest.split(':')[1][:16]}/source"
    part = prepared.messages[0]["content"][1]
    assert part == {"type": "video_url", "video_url": {"ref": ref.handle}}
    for leak in (URL, "token", "secretvalue", "example.com"):
        assert leak not in str(prepared.messages)


def test_an_inline_payload_is_measured_the_same_way(tmp_path):
    """A `data:` url is bytes like any other: the same probe, the same refusals, and the
    64 MiB of base64 does not survive into the staged payload."""
    adapter = preparation(tmp_path)
    prepared = run(adapter.prepare_request(b.ORG_A, request_with(adapter, data_url(WEBM,
                                                                                  "video/webm"))))
    ref = prepared.media[0]
    assert ref.kind is MediaKind.inline and ref.duration_s == pytest.approx(30.0)
    assert ref.mime == "video/webm"
    assert "base64" not in str(prepared.messages)


def test_a_ref_the_caller_put_in_the_record_is_discarded(tmp_path):
    """S2M D2. `NormalizedRequest.media` is an argument, so it is a claim: preparation
    rebuilds the tuple from the parts it actually materialized. A forged ref carrying a
    convenient duration, size and key must not survive into the record a job executes on."""
    adapter = preparation(tmp_path, bodies=[CLIP])
    forged = MediaRef(org_id=b.ORG_A, handle="med_forged", kind=MediaKind.url,
                      digest=fetch.digest_of(LONG), bytes=1, mime="video/mp4",
                      storage_ref=f"media/{b.ORG_A}/v1/0123456789abcdef/source",
                      duration_s=1.0)
    prepared = run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL, media=(forged,))))
    assert [ref.handle for ref in prepared.media] != [forged.handle]
    assert len(prepared.media) == 1 and prepared.media[0].duration_s == pytest.approx(10.0)
    assert prepared.media[0].digest == fetch.digest_of(CLIP)


def test_the_container_wins_over_the_declared_type(tmp_path):
    """Never the caller's word: a WebM served as `video/mp4` is recorded, stored and cached
    as the WebM it is, so the object's content type and the file the engine opens agree with
    its bytes rather than with a header."""
    adapter = preparation(tmp_path,
                          transport=support.Transport(support.response(body=WEBM,
                                                                      mime="video/mp4")))
    prepared = run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL)))
    ref = prepared.media[0]
    assert ref.mime == "video/webm"
    assert adapter.objects.objects[ref.storage_ref][2] == "video/webm"
    refs = run(adapter.stage(b.ORG_A, prepared))
    adapter.jobs[prepared.request_id] = b.ORG_A
    run(adapter.attach(prepared.request_id, refs))
    assert adapter.local_uri(run(adapter.prepare(prepared.request_id, "v1"))[0]) \
        .endswith("source.webm")


def test_a_tightened_profile_refuses_media_the_probe_can_read(tmp_path):
    """One constants table: S2M may tighten the pin, and every bound preparation enforces is
    read from the profile - not from what the parser happens to understand."""
    webm = data_url(WEBM, "video/webm")
    for name, profile in (
            ("container", prepare.MediaProfile(allowed_mime=frozenset({"video/mp4"}))),
            ("codec", prepare.MediaProfile(allowed_codecs=frozenset({"h264"}))),
            ("bytes", prepare.MediaProfile(max_bytes=len(WEBM) - 1)),
            ("duration", prepare.MediaProfile(max_duration_s=5.0))):
        adapter = preparation(tmp_path / name, profile=profile)
        with pytest.raises((errors.UnsupportedMedia, errors.RequestTooLarge)):
            run(adapter.prepare_request(b.ORG_A, request_with(adapter, webm)))
        assert adapter.objects.objects == {}
    # and the same media passes the pinned profile, so the cases above are not vacuous
    passing = preparation(tmp_path / "pinned")
    assert run(passing.prepare_request(b.ORG_A, request_with(passing, webm))).media


def test_the_parts_and_the_refs_stay_in_order(tmp_path):
    """R58 pairs the n-th media part with the n-th ref, and W1 rebuilds the message from
    that pairing - so an out-of-order rewrite answers about the wrong clip."""
    clips = [support.mp4(seconds=n + 2.0) for n in range(3)]
    adapter = preparation(tmp_path, limits=DEFAULTS.replace(max_media_bytes=sum(map(len, clips))),
                          profile=prepare.MediaProfile(max_parts=3,
                                                       max_bytes=sum(map(len, clips))))
    prepared = run(adapter.prepare_request(b.ORG_A,
                                           request_with(adapter, *[data_url(c) for c in clips])))
    assert [ref.duration_s for ref in prepared.media] == [2.0, 3.0, 4.0]
    named = [part["video_url"]["ref"] for part in prepared.messages[0]["content"][1:]]
    assert named == [ref.handle for ref in prepared.media]
    assert len(set(named)) == 3


def test_a_video_part_that_is_not_exactly_a_url_is_refused(tmp_path):
    """The port takes a record, so the shape G1 validated is still checked here: a part with
    no url, or a url that is not a string, is a typed refusal naming the part - not a
    silently skipped part that puts the parts and the refs out of step."""
    adapter = preparation(tmp_path, bodies=[CLIP])
    base = request_with(adapter, URL)
    for broken in ({}, {"ref": "med_x"}, {"url": 7}, {"url": ""}, {"url": None}):
        request = base.model_copy(update={"messages": (
            {"role": "user", "content": [{"type": "video_url", "video_url": broken}]},)})
        with pytest.raises(errors.InvalidRequest) as raised:
            run(adapter.prepare_request(b.ORG_A, request))
        assert "carries exactly {url}" in str(raised.value)


@pytest.mark.parametrize("cap, seconds", [(120.0, 121.0), (5.0, 10.0), (30.0, 30.5)])
def test_a_clip_over_the_duration_cap_is_refused_before_it_is_stored(cap, seconds, tmp_path):
    """S2M D9: `MAX_VIDEO_SECONDS` is what keeps every accepted clip at the trained 2 fps,
    and it is enforced on the measurement, before an object exists.

    Three caps, because one is indistinguishable from the literal 120.0: the bound has to
    come from the settings this deployment is running with (review R7)."""
    limits = DEFAULTS.replace(max_video_seconds=cap)
    adapter = preparation(tmp_path / str(cap), limits=limits,
                          bodies=[support.mp4(seconds=seconds)])
    with pytest.raises(errors.UnsupportedMedia) as raised:
        run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL)))
    assert f"{cap:.0f}s" in str(raised.value)
    assert adapter.objects.objects == {}


@pytest.mark.parametrize("cap", [120.0, 5.0])
def test_a_clip_exactly_at_the_cap_is_accepted(cap, tmp_path):
    """The bound is inclusive: a 120.000 s clip is 240 frames at the trained 2 fps, which is
    the worst case the profile is sized for, not one frame past it (review R24)."""
    limits = DEFAULTS.replace(max_video_seconds=cap)
    adapter = preparation(tmp_path / str(cap), limits=limits,
                          bodies=[support.mp4(seconds=cap)])
    prepared = run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL)))
    assert prepared.media[0].duration_s == pytest.approx(cap)


@pytest.mark.parametrize("name, body", [
    ("a truncated container", CLIP[:20]),
    ("a corrupt container", b"\x00" * 64),
    ("a container that loops", support.box(b"ftyp", b"isom" + b"\x00" * 8) + b"\x00\x00\x00\x04moov"),
    ("an unservable codec", support.mp4(codec=b"apcn")),
    ("an audio-only file", support.webm(codec=b"A_OPUS", track_type=2)),
    ("a 65535-square declaration", support.webm(width=65_535, height=65_535)),
])
def test_media_the_profile_refuses_is_never_stored(name, body, tmp_path):
    """The probe runs before the write, so a refusal costs no object, no cache file and no
    index entry - a client hammering a corrupt clip fills nothing up."""
    adapter = preparation(tmp_path, bodies=[body])
    with pytest.raises(errors.UnsupportedMedia):
        run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL)))
    assert adapter.objects.objects == {}
    assert adapter.refs == {}
    assert os.listdir(tmp_path) == []


def test_an_oversize_body_never_reaches_the_probe(tmp_path):
    """M1's byte cap still runs first: 64 MiB is the bound on what is decoded, so the probe
    is never handed a body the fetcher should have abandoned."""
    seen = []
    adapter = preparation(tmp_path, limits=DEFAULTS.replace(max_media_bytes=64), bodies=[CLIP],
                          probe_fn=lambda data: seen.append(data) or probe.probe(data))
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL)))
    assert seen == []


def test_two_identical_parts_are_two_refs_over_one_object(tmp_path):
    """R58 pairs parts with refs one to one, so a request naming the same clip twice has
    two parts and two refs - over one content-addressed object, not two."""
    adapter = preparation(tmp_path, limits=DEFAULTS.replace(max_media_bytes=len(CLIP) * 2),
                          bodies=[CLIP, CLIP],
                          profile=prepare.MediaProfile(max_parts=2, max_bytes=len(CLIP) * 2))
    prepared = run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL, OTHER_URL)))
    assert len(prepared.media) == 2
    assert prepared.media[0] == prepared.media[1]
    assert len(adapter.objects.objects) == 1
    refs = [part["video_url"]["ref"] for part in prepared.messages[0]["content"][1:]]
    assert refs == [prepared.media[0].handle] * 2


def test_more_parts_than_the_profile_allows_are_refused(tmp_path):
    """One clip per request is a capacity fact (a 120 s clip is ~23,560 of 32,768 tokens),
    so it is the profile's number and it is checked before anything is fetched."""
    adapter = preparation(tmp_path, bodies=[CLIP, CLIP])
    with pytest.raises(errors.UnsupportedMedia):
        run(adapter.prepare_request(b.ORG_A, request_with(adapter, URL, OTHER_URL)))
    assert adapter.transport.requests == []


def test_a_request_may_only_be_prepared_for_its_own_org(tmp_path):
    """R10: two tenant-bearing arguments that disagree are a refusal, never a preparation
    into whichever tenant the caller named."""
    adapter = preparation(tmp_path, bodies=[CLIP])
    with pytest.raises(errors.Forbidden):
        run(adapter.prepare_request(b.ORG_B, request_with(adapter, URL, org_id=b.ORG_A)))


# --- bounded work -------------------------------------------------------------
def test_preparation_runs_at_most_the_pool_width_at_once(tmp_path):
    """r1 R1: `PREPARATION_CONCURRENCY` bounds how many clips are in memory at once. With
    64 MiB each, an unbounded gather over a request's parts is the whole process."""
    live, peak = [0], [0]

    async def slow(data):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0)
        live[0] -= 1
        return probe.probe(data)

    limits = DEFAULTS.replace(preparation_concurrency=2, max_media_bytes=len(CLIP) * 8)
    adapter = preparation(tmp_path, limits=limits, probe_fn=slow,
                          bodies=[CLIP] * 5,
                          profile=prepare.MediaProfile(max_parts=5, max_bytes=len(CLIP) * 8))
    sources = [data_url(support.mp4(seconds=n + 1.0)) for n in range(5)]
    prepared = run(adapter.prepare_request(b.ORG_A, request_with(adapter, *sources)))
    assert len(prepared.media) == 5
    assert peak[0] <= 2, f"{peak[0]} probes ran at once"


def test_one_request_cannot_exceed_the_media_budget(tmp_path):
    """Per request, not per part: three clips inside the per-object cap can still be more
    memory than one request may have."""
    limits = DEFAULTS.replace(max_media_bytes=len(CLIP) * 3)
    adapter = preparation(tmp_path, limits=limits, bodies=[CLIP] * 3,
                          profile=prepare.MediaProfile(max_parts=3, max_bytes=len(CLIP) * 2))
    sources = [data_url(support.mp4(seconds=n + 1.0)) for n in range(3)]
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.prepare_request(b.ORG_A, request_with(adapter, *sources)))


def test_a_probe_that_never_returns_is_bounded_by_its_deadline(tmp_path):
    """A hung decoder is the classic preparation failure. `PROBE_TIMEOUT_S` is the bound,
    and the refusal is platform-side (R21) rather than the customer's fault."""
    async def never(data):
        await asyncio.Future()

    adapter = preparation(tmp_path, limits=DEFAULTS.replace(probe_timeout_s=0.01),
                          probe_fn=never, bodies=[CLIP])

    async def drive():
        with pytest.raises(errors.DeadlineExceeded):
            await adapter.prepare_request(b.ORG_A, request_with(adapter, URL))
        # and nothing was stored on the way out
        assert adapter.objects.objects == {}

    run(asyncio.wait_for(drive(), 2.0))


def test_a_store_failure_mid_batch_attaches_nothing(tmp_path):
    """02: a staging failure creates no job or hold. The second object fails to write, so
    the request is refused, no job is attached and nothing downstream sees half a request."""
    class Failing(store.InMemoryObjectStore):
        def __init__(self) -> None:
            super().__init__()
            self.writes = 0

        async def put_if_absent(self, key, data, content_type):
            self.writes += 1
            if self.writes == 2:
                raise errors.DependencyUnavailable("object store is unavailable")
            return await super().put_if_absent(key, data, content_type)

    objects = Failing()
    adapter = preparation(tmp_path, objects=objects,
                          limits=DEFAULTS.replace(max_media_bytes=len(CLIP) * 4),
                          profile=prepare.MediaProfile(max_parts=2, max_bytes=len(CLIP) * 4))
    sources = [data_url(CLIP), data_url(support.mp4(seconds=4.0))]
    with pytest.raises(errors.DependencyUnavailable):
        run(adapter.prepare_request(b.ORG_A, request_with(adapter, *sources)))
    assert adapter.by_job == {} and adapter.prepared_by_job == {}
    assert len(objects.objects) <= 1


# --- the port operation and the processing cache -------------------------------
def staged_job(adapter, *, org_id=b.ORG_A, body=CLIP, source=None):
    """Materialize, stage, admit and attach - the state `prepare` runs against."""
    prepared = run(adapter.prepare_request(org_id, request_with(adapter, source or data_url(body),
                                                                org_id=org_id)))
    refs = run(adapter.stage(org_id, prepared))
    adapter.jobs[prepared.request_id] = org_id
    run(adapter.attach(prepared.request_id, refs))
    return prepared.request_id, refs


def test_prepare_persists_the_artifact_and_a_file_the_engine_can_open(tmp_path):
    """S2M D3: a bare object key is not something a decoder can open. Preparation persists
    the prepared artifact durably *and* materializes it into the tenant's processing cache,
    which is the `file://` path the worker hands vLLM."""
    adapter = preparation(tmp_path)
    job_id, _ = staged_job(adapter)
    prepared = run(adapter.prepare(job_id, "v1"))
    ref = prepared[0]
    assert ref.storage_ref == f"media/{b.ORG_A}/v1/{ref.digest.split(':')[1][:16]}/prepared"
    assert ref.duration_s == pytest.approx(10.0)
    assert adapter.objects.objects.get(ref.storage_ref, (None, None))[1] == CLIP
    uri = adapter.local_uri(ref)
    assert uri.startswith("file://")
    path = uri[len("file://"):]
    assert path.startswith(str(tmp_path)) and b.ORG_A in path and path.endswith("source.mp4")
    with open(path, "rb") as handle:
        assert handle.read() == CLIP


def test_a_prepared_artifact_that_is_gone_is_written_again(tmp_path):
    """The durable artifact is the record and the cache file is a copy of it, so a cache hit
    must not stand in for an object that is not there any more."""
    adapter = preparation(tmp_path)
    job_id, _ = staged_job(adapter)
    ref = run(adapter.prepare(job_id, "v1"))[0]
    adapter.objects.objects.pop(ref.storage_ref)
    assert adapter.cache.get(ref.org_id, ref.digest, "v1") is not None
    assert run(adapter.prepare(job_id, "v1"))[0] == ref
    assert adapter.objects.objects.get(ref.storage_ref, (None, None))[1] == CLIP


def test_the_profile_version_namespaces_the_prepared_artifact(tmp_path):
    """01: the tenant, the source digest and the profile version namespace the cache. Two
    profiles are two artifacts and two cache entries over one source."""
    adapter = preparation(tmp_path)
    job_id, _ = staged_job(adapter)
    first = run(adapter.prepare(job_id, "v1"))[0]
    second = run(adapter.prepare(job_id, "profile-2"))[0]
    assert first.storage_ref != second.storage_ref
    assert "profile-2" in second.storage_ref and second.profile_version == "profile-2"
    assert first.digest == second.digest
    assert len(adapter.cache.entries) == 2


def test_two_profiles_of_one_object_are_two_local_files(tmp_path):
    """R61 as amended (review B2): the profile version is a path segment, not only an index
    key. Sharing one file made the second `put` overwrite the first, and made expiring v1
    delete the bytes v2's live entry pointed at."""
    clock = Clock()
    adapter = preparation(tmp_path, clock=clock)
    job_id, _ = staged_job(adapter)
    first = run(adapter.prepare(job_id, "v1"))[0]
    clock.advance(DEFAULTS.processing_cache_ttl_s - 1)
    second = run(adapter.prepare(job_id, "profile-2"))[0]
    one, two = adapter.local_uri(first)[len("file://"):], adapter.local_uri(second)[len("file://"):]
    assert one != two
    assert f"{os.sep}v1{os.sep}" in one and f"{os.sep}profile-2{os.sep}" in two
    assert os.path.exists(one) and os.path.exists(two)
    # v1 is now past its life and v2 is not: expiring one must not take the other's bytes
    clock.advance(2)
    assert adapter.cache.sweep() == 1
    assert not os.path.exists(one)
    assert os.path.exists(two) and adapter.local_uri(second).endswith(two)


def test_preparing_twice_is_the_same_answer(tmp_path):
    """R46 allows bounded preparation retries, so a second attempt must re-derive the same
    refs - not prepare the first attempt's output under a second profile hop."""
    adapter = preparation(tmp_path)
    job_id, _ = staged_job(adapter)
    # A profile other than the source's, because that is where a store that replaced the
    # attached sources with its own output would prepare the previous attempt's artifact.
    assert run(adapter.prepare(job_id, "profile-2")) == run(adapter.prepare(job_id, "profile-2"))
    assert run(adapter.prepare(job_id, "v1")) == run(adapter.prepare(job_id, "v1"))


def test_the_cache_is_read_instead_of_probing_again(tmp_path):
    """The processing cache exists so the second job on the same clip does not re-read,
    re-probe and re-write 64 MiB."""
    probes = []
    adapter = preparation(tmp_path, probe_fn=lambda data: probes.append(1) or probe.probe(data))
    job_id, _ = staged_job(adapter)
    before = len(probes)
    run(adapter.prepare(job_id, "v1"))
    run(adapter.prepare(job_id, "v1"))
    assert len(probes) == before + 1


def test_one_tenants_cache_entry_is_not_another_tenants(tmp_path):
    """MEDIA-SEC: identical bytes in two organizations are two cache entries under two
    paths, and neither org's handle reaches the other's file."""
    clock = Clock()
    adapter = preparation(tmp_path, clock=clock,
                          transport=support.Transport(support.response(body=CLIP)))
    mine_job, _ = staged_job(adapter, org_id=b.ORG_A)
    theirs_job, _ = staged_job(adapter, org_id=b.ORG_B)
    mine = run(adapter.prepare(mine_job, "v1"))[0]
    theirs = run(adapter.prepare(theirs_job, "v1"))[0]
    assert mine.digest == theirs.digest
    assert adapter.local_uri(mine) != adapter.local_uri(theirs)
    assert b.ORG_A in adapter.local_uri(mine) and b.ORG_B in adapter.local_uri(theirs)
    # the same handle in the other tenant resolves to that tenant's object or to nothing
    assert adapter.cache.get(b.KEY_A, mine.digest, "v1") is None


def test_a_cache_entry_expires_and_its_file_goes_with_it(tmp_path):
    """7 days is a retention obligation, not an eviction preference: past it the entry is
    unreadable and the bytes are actually gone from the disk."""
    clock = Clock()
    adapter = preparation(tmp_path, clock=clock, ttl_s=DEFAULTS.processing_cache_ttl_s)
    job_id, _ = staged_job(adapter)
    ref = run(adapter.prepare(job_id, "v1"))[0]
    path = adapter.local_uri(ref)[len("file://"):]
    clock.advance(DEFAULTS.processing_cache_ttl_s + 1)
    assert adapter.cache.get(ref.org_id, ref.digest, "v1") is None
    assert not os.path.exists(path)
    with pytest.raises(errors.NotFound):
        adapter.local_uri(ref)


def test_a_sweep_removes_expired_entries_and_leaves_live_ones(tmp_path):
    clock = Clock()
    adapter = preparation(tmp_path, clock=clock)
    old_job, _ = staged_job(adapter)
    old = run(adapter.prepare(old_job, "v1"))[0]
    clock.advance(DEFAULTS.processing_cache_ttl_s - 1)
    new_job, _ = staged_job(adapter, body=support.mp4(seconds=4.0))
    new = run(adapter.prepare(new_job, "v1"))[0]
    old_path = adapter.cache.entries[(old.org_id, old.digest, "v1")].local_path
    clock.advance(2)
    assert adapter.cache.sweep() == 1
    assert not os.path.exists(old_path)
    assert (new.org_id, new.digest, "v1") in adapter.cache.entries
    assert adapter.local_uri(new)


def test_a_failed_durable_write_leaves_no_local_copy(tmp_path):
    """Durable before local, and provably in that order: if the prepared artifact cannot be
    persisted there must be no cache entry claiming it was, or the next attempt hits the
    cache and reports a job prepared against an object that does not exist (review R12)."""
    class FailsPrepared(store.InMemoryObjectStore):
        async def put_if_absent(self, key, data, content_type):
            if key.endswith("/prepared"):
                raise errors.DependencyUnavailable("object store is unavailable")
            return await super().put_if_absent(key, data, content_type)

    adapter = preparation(tmp_path, objects=FailsPrepared())
    job_id, _ = staged_job(adapter)
    with pytest.raises(errors.DependencyUnavailable):
        run(adapter.prepare(job_id, "v1"))
    assert adapter.cache.entries == {}
    assert os.listdir(tmp_path) == []
    assert adapter.prepared_by_job == {}


def test_a_cache_file_deleted_behind_the_index_is_a_miss(tmp_path):
    """The index is a hint about the disk, not the truth: an operator, a reboot or a sweep
    can remove the file, and a worker must never be handed a path to nothing."""
    adapter = preparation(tmp_path)
    job_id, _ = staged_job(adapter)
    ref = run(adapter.prepare(job_id, "v1"))[0]
    os.remove(adapter.local_uri(ref)[len("file://"):])
    with pytest.raises(errors.NotFound):
        adapter.local_uri(ref)
    # and the next preparation re-materializes it rather than trusting the stale entry
    assert adapter.local_uri(run(adapter.prepare(job_id, "v1"))[0])


def test_the_prepared_ref_carries_the_measurement_not_the_attached_record(tmp_path):
    """The attached record is data, not testimony. `attach` stores this store's own refs, so
    the way caller-shaped facts reach `prepare` is a job row that returns them - which is
    exactly what D2 will do once the attachment lives in PostgreSQL. Every field the engine
    acts on is therefore re-measured from the bytes that are really there, never copied from
    the ref (review R1/R23)."""
    adapter = preparation(tmp_path)
    job_id, refs = staged_job(adapter)
    # What a job row holding the customer's numbers would hand back: the same object, with
    # a convenient duration, the wrong container and a nonsense size.
    adapter.by_job[job_id] = (refs[0].model_copy(update={"duration_s": 1.0,
                                                         "mime": "video/quicktime",
                                                         "bytes": 7}),)
    prepared = run(adapter.prepare(job_id, "v1"))[0]
    assert prepared.duration_s == pytest.approx(10.0)
    assert prepared.mime == "video/mp4"
    assert prepared.bytes == len(CLIP)
    # and the local copy is named for the container it really is, not the claimed one
    assert adapter.local_uri(prepared).endswith("source.mp4")


def test_an_object_that_vanished_between_attach_and_prepare_is_not_found(tmp_path):
    """The expiry drill on the durable side: preparation reads the object it was told
    about, so an object that is gone is a typed `not_found` rather than a worker failing on
    a file nobody can open."""
    adapter = preparation(tmp_path, objects=Counting())
    job_id, refs = staged_job(adapter)
    adapter.objects.objects.pop(refs[0].storage_ref)
    with pytest.raises(errors.NotFound):
        run(adapter.prepare(job_id, "v1"))
    # and the cheap check is what answered: HEAD said no, so no 64 MiB was ever read
    assert adapter.objects.gets == 0


def test_an_object_whose_content_changed_is_not_prepared(tmp_path):
    """HEAD and digest, not HEAD alone: a store that says "yes" and then hands over other
    bytes must not have them prepared, cached and answered about."""
    adapter = preparation(tmp_path)
    job_id, refs = staged_job(adapter)
    key = refs[0].storage_ref
    adapter.objects.objects[key] = (refs[0].digest, support.mp4(seconds=99.0), "video/mp4")
    with pytest.raises(errors.NotFound):
        run(adapter.prepare(job_id, "v1"))
    assert adapter.cache.entries == {}


def test_prepare_refuses_a_job_it_knows_nothing_about(tmp_path):
    adapter = preparation(tmp_path)
    with pytest.raises(errors.NotFound):
        run(adapter.prepare(adapter.harness.ids.uuid(), "v1"))


def test_a_profile_version_cannot_escape_the_cache_root(tmp_path):
    """The profile version reaches an object key and the cache index, so it is an
    identifier and nothing else."""
    adapter = preparation(tmp_path, objects=Counting())
    job_id, _ = staged_job(adapter)
    for version in ("../../etc", "v1/../../..", "/absolute", "v1\nx", ""):
        with pytest.raises(errors.InvalidRequest):
            run(adapter.prepare(job_id, version))
    # refused before anything is read, and nothing landed outside the cache root
    assert adapter.objects.gets == 0
    assert adapter.cache.entries == {}
    # including for a text-only job, where no key is built and nothing else would look at
    # the version at all
    empty = "00000042-0000-4000-8000-000000000042"
    adapter.jobs[empty] = b.ORG_A
    run(adapter.attach(empty, ()))
    assert run(adapter.prepare(empty, "v1")) == ()
    with pytest.raises(errors.InvalidRequest):
        run(adapter.prepare(empty, "../../etc"))


def test_a_cross_tenant_handle_cannot_reach_another_tenants_media(tmp_path):
    """MEDIA-SEC: the same content-addressed handle exists in both tenants, and each one
    resolves to its own object. A job may only be attached its own org's refs (R52/R55)."""
    adapter = preparation(tmp_path, transport=support.Transport(support.response(body=CLIP)))
    mine_job, mine_refs = staged_job(adapter, org_id=b.ORG_A)
    theirs_job, theirs_refs = staged_job(adapter, org_id=b.ORG_B)
    assert mine_refs[0].handle == theirs_refs[0].handle
    with pytest.raises(errors.NotFound):
        run(adapter.attach(mine_job, theirs_refs))
    mine = run(adapter.prepare(mine_job, "v1"))
    assert [ref.org_id for ref in mine] == [b.ORG_A]
    assert all(b.ORG_B not in ref.storage_ref for ref in mine)
    theirs = run(adapter.prepare(theirs_job, "v1"))
    assert [ref.org_id for ref in theirs] == [b.ORG_B]


def test_the_cache_path_is_built_from_validated_parts_only(tmp_path):
    """Nothing caller-shaped reaches a path: a malformed tenant or digest is a typed
    refusal where the path is built, not a directory somewhere else."""
    cache = prepare.ProcessingCache(str(tmp_path))
    digest = "sha256:" + "ab" * 32
    good = cache.path_for(b.ORG_A, "v1", digest, "video/mp4")
    # The exact shape, not "starts with the root and ends with .mp4": R61 as amended is
    # root/org/profile/16-hex/source.ext, and a path that merely looks plausible is how two
    # profiles came to share one file (review B2). Asserting the segments also pins the
    # digest width and keeps the root check honest.
    assert os.path.relpath(good, str(tmp_path)).split(os.sep) == [
        b.ORG_A, "v1", "ab" * 8, "source.mp4"]
    assert os.path.isabs(good)
    for org in ("../../etc", "", "ORG", b.ORG_A + "/..", "not-a-uuid"):
        with pytest.raises(errors.InvalidRequest):
            cache.path_for(org, "v1", digest, "video/mp4")
    for version in ("../../etc", "v1/../..", "/absolute", "V1", "", "v1\nx"):
        with pytest.raises(errors.InvalidRequest):
            cache.path_for(b.ORG_A, version, digest, "video/mp4")
    for bad in ("../../etc/passwd", "sha256:zz", "", "sha256:" + "ab" * 31):
        with pytest.raises(errors.InvalidRequest):
            cache.path_for(b.ORG_A, "v1", bad, "video/mp4")
    with pytest.raises(errors.UnsupportedMedia):
        cache.path_for(b.ORG_A, "v1", digest, "application/zip")


# --- the seam into W1 -----------------------------------------------------------
def engine(root: str, local_uri) -> worker_engine.VllmEngine:
    """W1's real adapter behind a transport that never answers: `upstream_body` is built
    and refused (or not) entirely before anything is sent. Since W2 the adapter takes M2's
    `local_uri` resolver and the shared media root (R61 (2)); the coordinator wired the
    seam at the M3/W2 merge."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={})), base_url="http://engine.invalid")
    return worker_engine.VllmEngine(client, served_model="marlin2b", clock=lambda: 0.0,
                                    media_settings=Settings(), local_media_root=root,
                                    local_uri=local_uri)


def work_for(request, refs):
    return Work(request=request, media_refs=refs, prepared_refs=refs,
                price_snapshot=b.DEFAULT_PRICE,
                budgets=Budgets.of(DEFAULTS, ExecutionMode.stream))


def test_what_preparation_produces_is_what_the_engine_adapter_accepts(tmp_path):
    """The M -> W seam, end to end on the real adapter: the prepared record pairs one media
    part with one ref in order (R58), its storage key passes W1's own grammar check, and the
    measured duration becomes the pinned frame budget - the request M1 alone produced could
    not reach the engine at all, because it carried no duration (S2M D2)."""
    adapter = preparation(tmp_path)
    prepared_request = run(adapter.prepare_request(b.ORG_A, request_with(adapter,
                                                                         data_url(CLIP))))
    refs = run(adapter.stage(b.ORG_A, prepared_request))
    adapter.jobs[prepared_request.request_id] = b.ORG_A
    run(adapter.attach(prepared_request.request_id, refs))
    prepared_refs = run(adapter.prepare(prepared_request.request_id, "v1"))

    body = engine(str(tmp_path), adapter.local_uri).upstream_body(
        worker_engine.prepared_request(work_for(prepared_request, prepared_refs), 2_061))
    parts = body["messages"][0]["content"]
    # the engine receives the pilot `file://` form under the shared root (R61 (2)), never
    # the durable object key
    assert parts[1] == {"type": "video_url",
                        "video_url": {"url": adapter.local_uri(prepared_refs[0])}}
    assert adapter.local_uri(prepared_refs[0]).startswith(f"file://{tmp_path}/")
    # the pinned profile v1 budget, computed from the measured 10 s at 2 fps
    assert body["mm_processor_kwargs"] == {"fps": 2.0, "min_frames": 4, "max_frames": 240,
                                           "size": {"shortest_edge": 4096,
                                                    "longest_edge": 20 * 200_704}}
    assert body["cache_salt"].startswith("salt_") and len(body["mm_uuids"]) == 1
    assert prepared_refs[0].duration_s == pytest.approx(10.0)


def test_the_engine_adapter_refuses_the_record_m1_alone_produced(tmp_path):
    """The other half of the seam, so the case above is not vacuous: without the probe the
    ref carries no duration and W1's guard is what fires (S2M D2, `engine.py` 570-578)."""
    adapter = preparation(tmp_path)
    prepared_request = run(adapter.prepare_request(b.ORG_A, request_with(adapter,
                                                                         data_url(CLIP))))
    refs = run(adapter.stage(b.ORG_A, prepared_request))
    undated = tuple(ref.model_copy(update={"duration_s": None,
                                           "storage_ref": ref.storage_ref.replace("source",
                                                                                  "prepared")})
                    for ref in refs)
    # a resolver that answers a well-formed local path, so the only guard left is W1's
    shaped = (lambda ref: f"file://{tmp_path}/{ref.org_id}/{ref.profile_version}/"
              f"{ref.digest[len('sha256:'):][:16]}/source.mp4")
    with pytest.raises(errors.UnsupportedMedia):
        engine(str(tmp_path), shaped).upstream_body(
            worker_engine.prepared_request(work_for(prepared_request, undated), 2_061))


def test_a_prepared_ref_satisfies_the_engines_storage_ref_grammar(tmp_path):
    """W1 checks the shape and the tenant of the key it is handed, because a
    `PreparedRequest` can be hand-built. M's keys must pass it for every profile version."""
    adapter = preparation(tmp_path)
    job_id, _ = staged_job(adapter)
    for version in ("v1", "profile-2", "marlin2b.video.v1"):
        for ref in run(adapter.prepare(job_id, version)):
            worker_engine.check_storage_ref(ref)
            assert len(ref.storage_ref.split("/")) == SOURCE_KEY_PARTS


# --- the exported conformance suite ---------------------------------------------
class Deferred:
    """M3's operations, reported as missing hooks rather than answered. Preparation is no
    longer among them, which is the point of this task."""

    OWNERS = {"create_upload": "M3", "finalize_upload": "M3"}

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def __getattr__(self, name):
        owner = self.OWNERS.get(name)
        if owner is None:
            return getattr(self.adapter, name)

        async def deferred(*args, **kwargs):
            raise MissingHook(f"{name} ({owner} owns it)")

        return deferred


def conformance_factory(tmp_path):
    def factory(limits=None, **_kw):
        adapter = preparation(tmp_path / str(len(os.listdir(tmp_path))),
                              limits=limits or DEFAULTS)
        return Harness(port=Deferred(adapter), clock=FakeClock(), ids=SequentialIds(),
                       extra={"admitted": adapter.jobs.__setitem__,
                              "materialized": materialized(adapter)})
    return factory


def materialized(adapter):
    """F2R item 4's hook: a real clip this store materializes. The length is derived from
    the builder's handle, so distinct refs are distinct content (equal content would give
    equal digest-derived handles, and a relabelled foreign ref would then resolve to the
    tenant's own object)."""
    async def hook(org_id, ref):
        seconds = 5.0 + int(fetch.digest_of(ref.handle.encode())[7:15], 16) % 97 / 10
        return await adapter.materialize(org_id, data_url(support.mp4(seconds=seconds)))
    return hook


M3_CASES = {"media_sec__an_upload_is_owned_verified_and_immutable": "create_upload",
            "media_sec__another_org_cannot_resolve_or_finalize": "create_upload",
            "media_sec__oversize_and_unsupported_uploads_are_refused": "create_upload",
            "media_sec__staging_never_replaces_an_existing_object": "create_upload",
            "media_sec__a_refused_upload_stays_refused": "create_upload",
            "media_sec__an_expired_upload_window_says_so": "create_upload"}
M1_CASES = ("media_sec__a_foreign_media_reference_is_not_staged",
            "media_sec__a_partial_request_stages_nothing")
# F2R item 4 made this case runnable here: `stage` takes only store-produced refs, seeded
# through the `materialized` hook above.
PARITY = "media_parity__staging_is_content_addressed_and_tenant_namespaced"


def test_the_exported_conformance_suite_runs_against_the_real_adapter(tmp_path, capsys):
    """F-CONTRACT / r1 R32: the same cases the fake passes, against this adapter, with the
    partition asserted - what ran and what is another task's; nothing is blocked (the
    parity case runs on media this store materialized)."""
    cases, _runner = SUITES["mediastore"]
    factory = conformance_factory(tmp_path)
    outcomes: dict[str, str] = {}
    for case in cases():
        try:
            asyncio.run(case(factory))
            outcomes[case.__name__] = "pass"
        except MissingHook as missing:
            outcomes[case.__name__] = f"skip: needs {missing.hook}"
        except errors.DomainError as refusal:
            outcomes[case.__name__] = f"blocked: {refusal.code}: {refusal}"
    print("\nmediastore conformance against infrx.media.prepare.MediaPreparation:")
    for name, outcome in sorted(outcomes.items()):
        print(f"  {outcome:<34} {name}")
    assert {name for name, out in outcomes.items() if out == "pass"} == {*M1_CASES, PARITY}
    assert {name: out.split("needs ")[1].split(" ")[0]
            for name, out in outcomes.items() if out.startswith("skip")} == M3_CASES
    assert not [name for name, out in outcomes.items() if out.startswith("blocked")], outcomes
    assert len(outcomes) == len(cases()) == 9


def test_the_invariants_of_the_blocked_case_hold_on_materialized_media(tmp_path):
    """The parity case's assertions about `prepare`, made directly against media this store
    really materialized."""
    adapter = preparation(tmp_path, transport=support.Transport(support.response(body=CLIP)))
    mine_job, staged = staged_job(adapter, org_id=b.ORG_A)
    theirs_job, theirs = staged_job(adapter, org_id=b.ORG_B)
    prepared = run(adapter.prepare(mine_job, "profile-2"))
    assert prepared[0].profile_version == "profile-2"
    assert prepared[0].storage_ref != staged[0].storage_ref
    assert "profile-2" in prepared[0].storage_ref and b.ORG_A in prepared[0].storage_ref
    assert prepared[0].digest == staged[0].digest          # same source content
    with pytest.raises(errors.NotFound):                   # an unknown job invents nothing
        run(adapter.prepare(adapter.harness.ids.uuid(), "profile-2"))
    foreign = run(adapter.prepare(theirs_job, "profile-2"))
    assert [ref.org_id for ref in foreign] == [b.ORG_B]
    assert all(b.ORG_B in ref.storage_ref for ref in foreign)
    # a refused attach leaves `prepare` with nothing, not a half-written set
    adapter.jobs["00000099-0000-4000-8000-000000000099"] = b.ORG_A
    with pytest.raises(errors.NotFound):
        run(adapter.attach("00000099-0000-4000-8000-000000000099", theirs))
    with pytest.raises(errors.NotFound):
        run(adapter.prepare("00000099-0000-4000-8000-000000000099", "profile-2"))


# --- M4: full-body work off the event loop --------------------------------------------
def test_no_full_body_digest_runs_on_the_event_loop(tmp_path, monkeypatch):
    """M4: measured before this change, the digests in `materialize`, `prepare` and the
    `data:` URL decoder held the event loop for ~0.8 ms per MiB each (~53 ms at the 64 MiB
    cap on the measurement host), delaying every other request the process was serving. They
    run in a worker thread now: a digest that finds a running loop in its thread is on it."""
    on_loop = []

    def watched(real):
        def digest(data):
            try:
                asyncio.get_running_loop()
                on_loop.append(len(data))
            except RuntimeError:
                pass
            return real(data)
        return digest

    monkeypatch.setattr(store, "digest_of", watched(store.digest_of))
    monkeypatch.setattr(prepare, "digest_of", watched(prepare.digest_of))
    monkeypatch.setattr(fetch, "digest_of", watched(fetch.digest_of))      # data: URLs
    # A store double whose own digest is not `store.digest_of`, so only the adapter's count.
    adapter = preparation(tmp_path / "cache", bodies=[CLIP],
                          objects=support.FileObjectStore(tmp_path / "objects"),
                          jobs={"job-1": b.ORG_A})
    ref = asyncio.run(adapter.materialize(b.ORG_A, URL))
    asyncio.run(adapter.attach("job-1", (ref,)))
    asyncio.run(adapter.prepare("job-1", "v1"))
    asyncio.run(adapter.materialize(b.ORG_A, data_url(WEBM, "video/webm")))
    assert on_loop == []


# --- M4: refusing from the header of a download still in progress -------------------
FTYP = support.box(b"ftyp", b"isom\x00\x00\x02\x00isom")
CHUNK = 64 << 10


def _moov(seconds: float, pad: int = 0) -> bytes:
    extra = (support.box(b"udta", bytes(pad)),) if pad else ()
    return support.box(b"moov", support.mvhd(round(seconds * 1000)), support.trak(), *extra)


def _streamed(body: bytes) -> support.Chunks:
    return support.Chunks([body[at:at + CHUNK] for at in range(0, len(body), CHUNK)])


def _served(tmp_path, stream):
    return preparation(tmp_path, transport=support.Transport(support.response(stream=stream)))


def test_a_header_first_clip_over_the_cap_is_refused_before_the_rest_arrives(tmp_path):
    """M4: measured before this, a 56 MB header-first clip of 150 s was read to its last
    byte and then refused. Its `moov` says 121 s in the first look (1 MiB), and the other
    3 MiB are never read; nothing is stored, and the log line says why."""
    body = FTYP + _moov(121.0) + support.box(b"mdat", bytes(4 << 20))
    stream = _streamed(body)
    adapter = _served(tmp_path, stream)
    with pytest.raises(errors.UnsupportedMedia) as caught:
        run(adapter.materialize(b.ORG_A, URL))
    assert "longer than 120s" in caught.value.detail
    assert getattr(caught.value, "reason", None) == "header"
    assert stream.read < fetch.EARLY_LOOK_BYTES + CHUNK < len(body)
    assert adapter.objects.objects == {} and adapter.refs == {}


def test_a_header_that_is_not_complete_yet_is_looked_at_again(tmp_path):
    """The first look sees half a `moov` (1.5 MB of `udta`); its probe refuses, which must
    stay inside the look. The second look settles it: at the cap, so accepted."""
    body = FTYP + _moov(DEFAULTS.max_video_seconds, pad=1_500_000) \
        + support.box(b"mdat", bytes(2 << 20))
    stream = _streamed(body)
    ref = run(_served(tmp_path, stream).materialize(b.ORG_A, URL))
    assert ref.duration_s == DEFAULTS.max_video_seconds and stream.read == len(body)


def test_a_media_first_clip_over_the_cap_is_refused_once_it_has_arrived(tmp_path):
    """No gain and no change where the header comes last: nothing can be read early, and
    the whole-object probe is still the one that refuses."""
    body = FTYP + support.box(b"mdat", bytes(4 << 20)) + _moov(121.0)
    stream = _streamed(body)
    adapter = _served(tmp_path, stream)
    with pytest.raises(errors.UnsupportedMedia) as caught:
        run(adapter.materialize(b.ORG_A, URL))
    assert "longer than 120s" in caught.value.detail and stream.read == len(body)
    assert adapter.objects.objects == {}


def test_a_media_first_clip_within_the_cap_is_read_to_the_end_and_accepted(tmp_path):
    """Every look at a media-first download ends inside a box header (the `mdat` runs past
    the prefix); that truncation is "nothing yet", never a refusal of a good clip."""
    body = FTYP + support.box(b"mdat", bytes(4 << 20)) + _moov(60.0)
    stream = _streamed(body)
    ref = run(_served(tmp_path, stream).materialize(b.ORG_A, URL))
    assert ref.duration_s == 60.0 and stream.read == len(body)


def test_the_header_scan_stops_where_the_probe_would(monkeypatch):
    """The early walk reads at most MAX_ELEMENTS box headers, like the probe. The answer
    would be None either way (the probe refuses a file with that many boxes); the bound is
    what keeps a prefix of 8-byte boxes from costing ~130k header reads per MiB per look."""
    moov = _moov(121.0)
    assert probe.probe_header(FTYP + moov) is not None                  # non-vacuous
    reads = []
    real = probe._u
    monkeypatch.setattr(probe, "_u", lambda data, at, size: reads.append(at) or real(data, at, size))
    many = FTYP + support.box(b"free") * (4 * probe.MAX_ELEMENTS) + moov
    assert probe.probe_header(many) is None
    assert len(reads) <= probe.MAX_ELEMENTS


def test_a_header_still_arriving_is_not_probed(monkeypatch):
    """A `moov` not yet complete is answered from its box header alone: the prefix is not
    copied and walked at every look for a `moov` that declares tens of MiB."""
    probed = []
    monkeypatch.setattr(probe, "probe", lambda data: probed.append(len(data)))
    moov = _moov(121.0)
    assert probe.probe_header(FTYP + moov[:-1]) is None and probed == []
    probe.probe_header(FTYP + moov)
    assert probed == [len(FTYP + moov)]                                  # non-vacuous


def test_a_moov_too_large_for_a_look_is_left_to_the_whole_object(monkeypatch):
    """Review S1: a complete `moov` ending past HEADER_PROBE_MAX is not copied and probed
    at a look (a 60 MiB one held the loop 43.8 ms per look); the whole object decides."""
    probed = []
    monkeypatch.setattr(probe, "probe", lambda data: probed.append(len(data)))
    probe.probe_header(FTYP + _moov(121.0))
    assert probed == [len(FTYP + _moov(121.0))]                         # non-vacuous
    probed.clear()
    assert probe.probe_header(FTYP + _moov(121.0, pad=probe.HEADER_PROBE_MAX)) is None
    assert probed == []


def test_a_settled_header_is_not_looked_at_again(tmp_path):
    """Review S1: the first complete `moov` is the only one the walk reads, so once it
    passed the profile the download is not looked at again (an 8 MiB body: one look, not
    four)."""
    body = FTYP + _moov(60.0) + support.box(b"mdat", bytes(8 << 20))
    stream = _streamed(body)
    adapter = _served(tmp_path, stream)
    answers = []
    real = adapter.refuse_early
    adapter.refuse_early = lambda head: answers.append(real(head)) or answers[-1]
    ref = run(adapter.materialize(b.ORG_A, URL))
    assert ref.duration_s == 60.0 and stream.read == len(body)
    assert answers == [True]


def test_the_head_is_looked_at_when_it_doubles_not_on_every_chunk():
    """At most seven looks up to the 64 MiB cap: an 8 MiB body in 64 KiB chunks is looked
    at four times, at 1, 2, 4 and 8 MiB."""
    body = FTYP + support.box(b"mdat", bytes((8 << 20) - len(FTYP) - 8))
    looks = []
    fetcher = fetch.MediaFetcher(DEFAULTS, resolve=support.resolver([support.PUBLIC]),
                                 transport=support.Transport(
                                     support.response(stream=_streamed(body))).transport,
                                 monotonic=support.Ticker(), log=support.Records())
    run(fetcher.fetch(URL, early=lambda head: looks.append(len(head))))
    assert looks == [1 << 20, 2 << 20, 4 << 20, 8 << 20]
