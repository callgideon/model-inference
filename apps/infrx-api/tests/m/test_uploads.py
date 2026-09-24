#!/usr/bin/env python3
"""M3: owned uploads - create, the bytes, finalize, and an upload's use by a job.

    uv run --frozen pytest -q tests/m/test_uploads.py

No network, no wall clock, no decoder: the object store is in memory, the clock is the
harness's `FakeClock`, and the clips are `support.mp4`/`support.webm`.
"""
from __future__ import annotations

import asyncio
import base64
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
    # the grammar bench.py allowlists: upl_ + 22..64, and nothing either side of it
    assert UPLOAD_HANDLE_RE.fullmatch("upl_" + "a" * 22)
    assert UPLOAD_HANDLE_RE.fullmatch("upl_" + "a" * 64)
    assert not UPLOAD_HANDLE_RE.fullmatch("upl_" + "a" * 21)
    assert not UPLOAD_HANDLE_RE.fullmatch("upl_" + "a" * 65)
    # R47: the ticket carries exactly the contract's fields - no storage key, no org
    assert set(ticket) == {"upload_handle", "destination_ref", "max_bytes", "accepted_mime",
                           "state", "expires_at"}
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


def flip_last(digest: str) -> str:
    return digest[:-1] + ("0" if digest[-1] != "0" else "1")


@pytest.mark.parametrize("declared", [
    fetch.digest_of(b"something else"),
    flip_last(fetch.digest_of(CLIP)),              # only the last hex digit differs
], ids=["other", "last-digit"])
def test_a_declared_digest_is_verified(declared):
    """The whole digest is compared, exactly: not a prefix, not case-folded (an
    uppercased declaration is refused at create by the digest grammar)."""
    adapter = adapter_for()
    with pytest.raises(errors.InvalidRequest):
        created(adapter, digest=fetch.digest_of(CLIP).upper().replace("SHA256:", "sha256:"))
    handle = created(adapter, digest=declared)
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
    """A handle that already names other content keeps that content. R82: `stage` takes
    only store-produced refs and this store's handles are content-addressed, so the squat
    is seeded directly - only something outside the store can reach this state."""
    adapter = adapter_for()
    handle = created(adapter)
    squat = b.media(b.ORG_A, handle=handle, kind=MediaKind.inline)
    adapter.refs[(b.ORG_A, handle)] = squat
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.Conflict):
        run(adapter.finalize_upload(b.ORG_A, handle))
    assert adapter.refs[(b.ORG_A, handle)] == squat
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


# --- use by a job ---------------------------------------------------------------------
def upload_request(adapter, ref, org_id=b.ORG_A):
    return b.request(adapter.harness, org_id=org_id, refs=(ref,))


def test_stage_uses_the_finalized_record_not_the_callers_copy():
    """A request naming an upload carries claims; the job gets the store's record."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    claim = ref.model_copy(update={"bytes": 1, "duration_s": 1.0, "mime": "video/webm",
                                   "storage_ref": "media/elsewhere"})
    assert run(adapter.stage(b.ORG_A, upload_request(adapter, claim))) == (ref,)


def test_stage_refuses_an_unfinalized_upload():
    """MEDIA-SEC: an upload is usable only once finalized, and a refusal stages nothing."""
    adapter = adapter_for()
    handle = created(adapter)
    arrive(adapter, handle, CLIP)
    claim = b.media(b.ORG_A, handle=handle, kind=MediaKind.upload)
    request = upload_request(adapter, claim)
    with pytest.raises(errors.DomainError) as refused:
        run(adapter.stage(b.ORG_A, request))
    assert errors.http_status(refused.value.code) in (400, 404)
    assert request.request_id not in adapter.payloads


def test_resolve_refuses_an_upload_that_is_not_finalized_even_if_its_handle_is_indexed():
    """A handle squatted by an indexed ref (seeded directly, R82) is not a finalized
    upload."""
    adapter = adapter_for()
    handle = created(adapter)
    adapter.refs[(b.ORG_A, handle)] = b.media(b.ORG_A, handle=handle, kind=MediaKind.inline)
    with pytest.raises(errors.InvalidRequest):
        run(adapter.resolve_owned(b.ORG_A, handle))


def test_stage_refuses_another_orgs_upload():
    """MEDIA-SEC: neither by naming the owner nor by claiming the handle as one's own."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    for claim in (ref, ref.model_copy(update={"org_id": b.ORG_B})):
        request = upload_request(adapter, claim, org_id=b.ORG_B)
        with pytest.raises(errors.NotFound):
            run(adapter.stage(b.ORG_B, request))
        assert request.request_id not in adapter.payloads


@pytest.mark.parametrize("change", ["replaced", "deleted"])
def test_an_upload_whose_object_changed_is_not_staged(change):
    """MEDIA-SEC: the object behind a finalized upload is re-checked at use."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    if change == "replaced":
        adapter.objects.seed(ref.storage_ref, b"other bytes")
    else:
        run(adapter.objects.delete(ref.storage_ref))
    with pytest.raises(errors.NotFound):
        run(adapter.stage(b.ORG_A, upload_request(adapter, ref)))
    with pytest.raises(errors.NotFound):
        run(adapter.resolve_owned(b.ORG_A, handle))


@pytest.mark.parametrize("tamper", [flip_last, lambda digest: "sha256:" + digest[7:].upper()],
                         ids=["last-digit", "uppercase"])
def test_the_use_time_recheck_compares_the_whole_digest(tamper):
    """`resolve_owned`'s re-check is exact: a HEAD digest one hex digit off, or
    differing only in case, is not the finalized object."""
    class Tampered(store.InMemoryObjectStore):
        target = None

        async def head(self, key):
            digest = await super().head(key)
            return tamper(digest) if key == self.target and digest else digest

    objects = Tampered()
    adapter = adapter_for(objects=objects)
    handle, ref = finalized(adapter)
    objects.target = ref.storage_ref
    with pytest.raises(errors.NotFound):
        run(adapter.resolve_owned(b.ORG_A, handle))


def test_another_orgs_open_upload_state_does_not_leak():
    """An upload's state is answered only to its owner: a foreign org whose own ref
    happens to carry the same handle resolves to its own ref, not "not finalized"."""
    adapter = adapter_for()
    handle = created(adapter)                                   # org A's, still open
    foreign = b.media(b.ORG_B, handle=handle, kind=MediaKind.inline)
    adapter.refs[(b.ORG_B, handle)] = foreign                   # seeded directly (R82)
    assert run(adapter.resolve_owned(b.ORG_B, handle)) == foreign


def test_an_uploaded_clip_is_prepared_like_any_source(tmp_path):
    """M2 limit 10: `prepare` finds a finalized upload at the source key it rebuilds, and
    the prepared artifact and the local file follow."""
    adapter = adapter_for(tmp_path)
    handle, ref = finalized(adapter)
    staged = run(adapter.stage(b.ORG_A, upload_request(adapter, ref)))
    job_id = adapter.harness.ids.uuid()
    adapter.jobs[job_id] = b.ORG_A
    run(adapter.attach(job_id, staged))
    prepared = run(adapter.prepare(job_id, "v1"))
    assert prepared[0].handle == handle and prepared[0].duration_s == pytest.approx(10.0)
    assert prepared[0].storage_ref == ref.storage_ref.replace("/source", "/prepared")
    local = adapter.local_uri(prepared[0]).removeprefix("file://")
    assert open(local, "rb").read() == CLIP
    assert os.path.commonpath([str(tmp_path), local]) == str(tmp_path)


# --- the exported conformance suite ---------------------------------------------------
class DeclaredFacts(uploads.MediaUploads):
    """The conformance cases upload `b"0123456789"` and `b"tiny"` as `video/mp4` and
    `application/zip`: labels, not containers, so M2's probe would refuse every one of
    them for a reason the case is not about. This adapter takes M1's `facts` (the declared
    type, no duration) and is otherwise the shipped class; that finalize consults the
    probe is proven above (`test_the_container_wins_over_the_declared_content_type`)."""

    facts = store.MediaStaging.facts


def conformance_factory(limits=None, **_kw):
    ids = SequentialIds()
    adapter = adapter_for(limits=limits or DEFAULTS, cls=DeclaredFacts)
    adapter.new_handle = ids.upload_handle
    adapter.attachments = support.Durable()

    def reopened():
        """MPILOT: the same object store and attach record, nothing in memory."""
        other = adapter_for(limits=limits or DEFAULTS, objects=adapter.objects,
                            cls=DeclaredFacts)
        other.attachments = adapter.attachments
        return other

    def put_object(handle, data, mime="video/mp4"):
        arrive(adapter, handle, data, mime)

    async def materialized(org_id, ref):
        # distinct clip per handle: equal content would give equal content-derived handles
        seconds = 5.0 + int(fetch.digest_of(ref.handle.encode())[7:15], 16) % 97 / 10
        body = base64.b64encode(support.mp4(seconds=seconds)).decode()
        return await adapter.materialize(org_id, f"data:video/mp4;base64,{body}")

    return Harness(port=adapter, clock=adapter.clock, ids=ids,
                   extra={"put_object": put_object, "admitted": adapter.jobs.__setitem__,
                          "materialized": materialized, "reopened": reopened})


def test_the_exported_conformance_suite_runs_every_upload_case():
    """F-CONTRACT / r1 R32: the six upload cases M2 skipped naming `create_upload` now
    run and pass; the two M1 cases and the parity case pass - every case passes, MPILOT's
    two (the window at use, the durable attach) included."""
    from infrx.contracts.conformance import SUITES, MissingHook

    cases, _runner = SUITES["mediastore"]
    outcomes: dict[str, str] = {}
    for case in cases():
        try:
            asyncio.run(case(conformance_factory))
            outcomes[case.__name__] = "pass"
        except MissingHook as missing:                   # never a pass (R32)
            outcomes[case.__name__] = f"skip: needs {missing.hook}"
        except errors.DomainError as refusal:
            outcomes[case.__name__] = f"blocked: {refusal.code}: {refusal}"
    print("\nmediastore conformance against infrx.media.uploads.MediaUploads:")
    for name, outcome in sorted(outcomes.items()):
        print(f"  {outcome:<34} {name}")
    assert len(outcomes) == len(cases()) == 11
    assert [name for name, out in outcomes.items() if out != "pass"] == []
