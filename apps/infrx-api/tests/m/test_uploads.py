#!/usr/bin/env python3
"""M3: owned uploads - create, the bytes, finalize, and an upload's use by a job.

    uv run --frozen pytest -q tests/m/test_uploads.py

No network, no wall clock, no decoder: the object store is in memory, the clock is the
harness's `FakeClock`, and the clips are `support.mp4`/`support.webm`.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import Harness
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FakeClock, SequentialIds
from infrx.contracts.ids import UPLOAD_HANDLE_RE
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import MediaKind, UploadState
from infrx.media import fetch, prepare, probe, store, uploads

from . import support

CLIP = support.mp4(seconds=10.0)
WEBM = support.webm(seconds=30.0)
TTL = DEFAULTS.processing_cache_ttl_s


class Counting(store.InMemoryObjectStore):
    """Says how often an object was really downloaded."""

    def __init__(self) -> None:
        super().__init__()
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        return await super().get(key)


class CacheClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def adapter_for(tmp_path=None, *, objects=None, probe_fn=probe.probe, limits=DEFAULTS,
                jobs=None, cls=uploads.MediaUploads):
    """The real adapter: an in-memory object store, the harness clock, no socket."""
    clock = FakeClock()
    jobs = {} if jobs is None else jobs

    def job_org(job_id):
        if job_id not in jobs:
            raise errors.NotFound(f"no job {job_id}")
        return jobs[job_id]

    fetcher = fetch.MediaFetcher(limits, resolve=support.resolver([support.PUBLIC]),
                                 monotonic=support.Ticker(), log=support.Records())
    cache = prepare.ProcessingCache(str(tmp_path) if tmp_path else "", clock=CacheClock())
    adapter = cls(objects or store.InMemoryObjectStore(), now=clock.now, cache=cache,
                  probe=probe_fn, limits=limits, fetcher=fetcher, job_org=job_org)
    adapter.clock, adapter.jobs = clock, jobs
    adapter.harness = Harness(port=adapter, clock=clock, ids=SequentialIds())
    return adapter


def run(coroutine):
    return asyncio.run(coroutine)


def arrive(adapter, handle, data, mime="video/mp4"):
    """The client's bytes landing at the destination, behind the store's back - the
    strongest client: it can overwrite the destination at any time."""
    upload = adapter.uploads[handle]
    adapter.objects.seed(adapter.upload_key(upload.org_id, handle), data, mime)


def created(adapter, org_id=b.ORG_A, **constraints):
    return run(adapter.create_upload(org_id, constraints))["upload_handle"]


def finalized(adapter, data=CLIP, org_id=b.ORG_A, **constraints):
    handle = created(adapter, org_id, **constraints)
    arrive(adapter, handle, data)
    return handle, run(adapter.finalize_upload(org_id, handle))


def source_key(ref):
    return f"media/{ref.org_id}/v1/{ref.digest.split(':')[1][:16]}/source"


# --- create -------------------------------------------------------------------------
def test_create_issues_an_opaque_handle_and_a_constrained_destination():
    """MEDIA-SEC / R61(1): `infrx-upload:upl_<id>` exactly - no org qualifier, no path,
    no signed URL - and the window is the retention window."""
    adapter = adapter_for()
    ticket = run(adapter.create_upload(b.ORG_A, {"max_bytes": 1024,
                                                 "accepted_mime": ["video/mp4"]}))
    handle = ticket["upload_handle"]
    assert UPLOAD_HANDLE_RE.fullmatch(handle)
    assert ticket["destination_ref"] == "infrx-upload:" + handle
    assert ticket["max_bytes"] == 1024 and ticket["accepted_mime"] == ("video/mp4",)
    assert ticket["state"] is UploadState.created
    upload = adapter.uploads[handle]
    assert upload.org_id == b.ORG_A
    assert (upload.expires_at - adapter.clock.now()).total_seconds() == TTL
    assert adapter.upload_key(b.ORG_A, handle) == f"uploads/{b.ORG_A}/{handle}"


def test_two_creates_are_two_handles():
    adapter = adapter_for()
    assert created(adapter) != created(adapter)


def test_a_handle_source_that_repeats_itself_is_refused():
    """A duplicate handle would hand one tenant's upload record to another create."""
    adapter = adapter_for()
    adapter.new_handle = lambda: "upl_" + "a" * 43
    created(adapter)
    with pytest.raises(errors.InternalError):
        created(adapter, b.ORG_B)
    assert adapter.uploads["upl_" + "a" * 43].org_id == b.ORG_A


@pytest.mark.parametrize("constraints", [
    {"max_bytes": DEFAULTS.max_media_bytes + 1}, {"max_bytes": 0}, {"max_bytes": -1},
    {"max_bytes": "4096"}, {"max_bytes": 1.5}, {"max_bytes": True},
    {"bytes": 0}, {"bytes": "10"}, {"max_bytes": 8, "bytes": 9},
    {"accepted_mime": "video/mp4"}, {"accepted_mime": []}, {"accepted_mime": None},
    {"accepted_mime": 5}, {"accepted_mime": [5]}, {"accepted_mime": ["application/zip"]},
    {"accepted_mime": ["video/mp4", "text/html"]},
    {"digest": "deadbeef"}, {"digest": "sha256:" + "A" * 64},
    {"maxbytes": 1024}, {"sha256": "0" * 64}, ["max_bytes"],
], ids=repr)
def test_create_refuses_constraints_the_caller_shapes(constraints):
    """MEDIA-SEC: every constraint is validated, and one nobody enforces is refused."""
    adapter = adapter_for()
    with pytest.raises(errors.InvalidRequest):
        run(adapter.create_upload(b.ORG_A, constraints))
    assert adapter.uploads == {}


def test_create_refuses_a_malformed_org():
    adapter = adapter_for()
    with pytest.raises(errors.InvalidRequest):
        run(adapter.create_upload("../" + b.ORG_A, {}))


# --- the bytes ------------------------------------------------------------------------
def test_put_upload_is_bounded_and_write_once():
    """The bytes G4U hands over: capped before they are stored, the same bytes twice is
    a no-op, other bytes are a conflict."""
    adapter = adapter_for()
    handle = created(adapter, max_bytes=len(CLIP))
    key = adapter.upload_key(b.ORG_A, handle)
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.put_upload(b.ORG_A, handle, CLIP + b"x", "video/mp4"))
    assert key not in adapter.objects.objects
    run(adapter.put_upload(b.ORG_A, handle, CLIP, "video/mp4"))
    run(adapter.put_upload(b.ORG_A, handle, CLIP, "video/mp4"))
    with pytest.raises(errors.Conflict):
        run(adapter.put_upload(b.ORG_A, handle, CLIP[:-1] + b"\x01", "video/mp4"))
    assert adapter.objects.objects[key][1] == CLIP


def test_a_completed_upload_accepts_no_more_bytes():
    adapter = adapter_for()
    handle, _ = finalized(adapter)
    with pytest.raises(errors.Conflict):
        run(adapter.put_upload(b.ORG_A, handle, CLIP, "video/mp4"))


# --- finalize -----------------------------------------------------------------------
def test_finalize_measures_the_bytes_that_arrived():
    """MEDIA-SEC: size, digest, type and duration come from the stored bytes, and the
    verified bytes land at the tenant's content-addressed source key."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    assert ref.org_id == b.ORG_A and ref.handle == handle and ref.kind is MediaKind.upload
    assert ref.bytes == len(CLIP) and ref.digest == fetch.digest_of(CLIP)
    assert ref.mime == "video/mp4" and ref.duration_s == pytest.approx(10.0)
    assert ref.storage_ref == source_key(ref)
    assert adapter.objects.objects[ref.storage_ref][1] == CLIP
    assert adapter.uploads[handle].state is UploadState.finalized
    assert run(adapter.resolve_owned(b.ORG_A, handle)) == ref


def test_the_container_wins_over_the_declared_content_type():
    """A WebM uploaded as `video/mp4` to an mp4-only upload is refused by the probe."""
    adapter = adapter_for()
    handle = created(adapter, accepted_mime=["video/mp4"])
    arrive(adapter, handle, WEBM, "video/mp4")
    with pytest.raises(errors.UnsupportedMedia):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].state is UploadState.aborted


def test_bytes_the_probe_refuses_abort_the_upload():
    adapter = adapter_for()
    handle = created(adapter)
    arrive(adapter, handle, b"PK\x03\x04 not a video", "video/mp4")
    with pytest.raises(errors.UnsupportedMedia):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].state is UploadState.aborted
    assert [k for k in adapter.objects.objects if k.startswith("media/")] == []


def test_a_retry_returns_the_same_completed_handle():
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    before = dict(adapter.objects.objects)
    assert run(adapter.finalize_upload(b.ORG_A, handle)) == ref
    assert adapter.objects.objects == before


def test_concurrent_finalizes_verify_and_copy_once():
    """Two completions racing: one verification, one download, one ref."""
    objects = Counting()
    adapter = adapter_for(objects=objects)
    handle = created(adapter)
    arrive(adapter, handle, CLIP)

    async def both():
        return await asyncio.gather(adapter.finalize_upload(b.ORG_A, handle),
                                    adapter.finalize_upload(b.ORG_A, handle))

    first, second = run(both())
    assert first == second and objects.gets == 1


def test_a_second_finalize_with_other_bytes_is_a_conflict():
    """The destination overwritten after completion: a conflict, and the completed ref
    and its object stay exactly what they were."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    arrive(adapter, handle, support.mp4(seconds=20.0))
    with pytest.raises(errors.Conflict):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].ref == ref
    assert run(adapter.resolve_owned(b.ORG_A, handle)) == ref
    assert adapter.objects.objects[ref.storage_ref][1] == CLIP


def test_an_oversize_object_is_refused_without_being_downloaded():
    objects = Counting()
    adapter = adapter_for(objects=objects)
    handle = created(adapter, max_bytes=len(CLIP) - 1)
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert objects.gets == 0
    assert adapter.uploads[handle].state is UploadState.aborted


def test_a_head_that_understates_the_size_is_caught_by_the_bytes():
    """The HEAD is a hint that saves a download; the bytes are the authority."""
    class Understating(store.InMemoryObjectStore):
        async def describe(self, key):
            described = await super().describe(key)
            return described and (1, described[1])

    adapter = adapter_for(objects=Understating())
    handle = created(adapter, max_bytes=len(CLIP) - 1)
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].state is UploadState.aborted


def test_a_declared_size_is_verified():
    adapter = adapter_for()
    handle = created(adapter, bytes=len(CLIP) + 1)
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.InvalidRequest):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].state is UploadState.aborted
    _, ref = finalized(adapter, bytes=len(CLIP))
    assert ref.bytes == len(CLIP)


def test_a_declared_digest_is_verified():
    adapter = adapter_for()
    handle = created(adapter, digest=fetch.digest_of(b"something else"))
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.UnsupportedMedia):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].state is UploadState.aborted
    _, ref = finalized(adapter, digest=fetch.digest_of(CLIP))
    assert ref.digest == fetch.digest_of(CLIP)


def test_an_unaccepted_type_is_refused():
    adapter = adapter_for()
    handle = created(adapter, accepted_mime=["video/webm"])
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.UnsupportedMedia):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].state is UploadState.aborted


def test_a_refused_upload_stays_refused():
    adapter = adapter_for()
    handle = created(adapter, max_bytes=8)
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.finalize_upload(b.ORG_A, handle))
    arrive(adapter, handle, b"tiny")
    with pytest.raises(errors.Conflict):
        run(adapter.finalize_upload(b.ORG_A, handle))


def test_an_expired_window_is_upload_expired_and_stays_closed():
    """R22: `upload_expired`, and an expired upload takes no more bytes and no retry."""
    adapter = adapter_for()
    handle = created(adapter)
    arrive(adapter, handle, CLIP)
    adapter.clock.advance(TTL)
    with pytest.raises(errors.UploadExpired):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].state is UploadState.expired
    with pytest.raises(errors.Conflict):
        run(adapter.finalize_upload(b.ORG_A, handle))
    with pytest.raises(errors.Conflict):
        run(adapter.put_upload(b.ORG_A, handle, CLIP, "video/mp4"))


def test_the_window_is_open_until_it_closes():
    adapter = adapter_for()
    handle = created(adapter)
    arrive(adapter, handle, CLIP)
    adapter.clock.advance(TTL - 1)
    assert run(adapter.finalize_upload(b.ORG_A, handle)).bytes == len(CLIP)


def test_another_org_cannot_put_finalize_or_resolve():
    """MEDIA-SEC: another tenant's handle is a 404 on every operation, never a 403."""
    adapter = adapter_for()
    handle = created(adapter)
    for call in (adapter.put_upload(b.ORG_B, handle, CLIP, "video/mp4"),
                 adapter.finalize_upload(b.ORG_B, handle)):
        with pytest.raises(errors.NotFound):
            run(call)
    assert adapter.uploads[handle].state is UploadState.created
    arrive(adapter, handle, CLIP)
    run(adapter.finalize_upload(b.ORG_A, handle))
    for call in (adapter.finalize_upload(b.ORG_B, handle), adapter.resolve_owned(b.ORG_B, handle)):
        with pytest.raises(errors.NotFound):
            run(call)


@pytest.mark.parametrize("handle", ["upl_unknown0000000000000000000", "../x", 5, None])
def test_an_unknown_handle_is_not_found(handle):
    adapter = adapter_for()
    with pytest.raises(errors.NotFound):
        run(adapter.finalize_upload(b.ORG_A, handle))


def test_nothing_uploaded_yet_is_not_a_refusal():
    adapter = adapter_for()
    handle = created(adapter)
    with pytest.raises(errors.InvalidRequest):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.uploads[handle].state is UploadState.created
    arrive(adapter, handle, CLIP)
    assert run(adapter.finalize_upload(b.ORG_A, handle)).bytes == len(CLIP)


def test_finalizing_never_replaces_what_the_handle_already_names():
    """A handle the tenant already staged other content under keeps that content."""
    adapter = adapter_for()
    handle = created(adapter)
    squat = b.media(b.ORG_A, handle=handle, kind=MediaKind.inline)
    staged = run(adapter.stage(b.ORG_A, b.request(adapter.harness, refs=(squat,))))
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.Conflict):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.refs[(b.ORG_A, handle)] == staged[0]
    assert adapter.uploads[handle].state is UploadState.aborted


def test_a_probe_that_times_out_leaves_the_upload_open():
    """Platform-side (R21): the customer's upload is not refused for our slowness."""
    async def hangs(data):
        await asyncio.Event().wait()

    adapter = adapter_for(probe_fn=hangs, limits=DEFAULTS.replace(probe_timeout_s=0.01))
    handle = created(adapter)
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.DeadlineExceeded):
        run(asyncio.wait_for(adapter.finalize_upload(b.ORG_A, handle), 2))
    assert adapter.uploads[handle].state is UploadState.created
    adapter.probe = probe.probe
    assert run(adapter.finalize_upload(b.ORG_A, handle)).duration_s == pytest.approx(10.0)
