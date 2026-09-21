#!/usr/bin/env python3
"""MEDIA-SEC / F-CONTRACT: the MediaStore adapter M1 owns, and the shared suite.

    uv run --frozen pytest -q tests/m/test_store.py

The exported conformance suite runs against this adapter exactly as it runs against
the fake. The cases that need `create_upload` (M3) or `prepare` (M2) are **skipped,
naming the operation**, and the skip report is printed so evidence can quote it
instead of claiming it (r1 R32).
"""
from __future__ import annotations

import asyncio
import base64
import inspect

import pytest
from infrx.contracts import codec, errors, ports
from infrx.contracts.conformance import (SUITES, Harness, MissingHook, hook, run_cases)
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FakeClock, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import MediaKind, MediaRef
from infrx.media import fetch, store

from . import support

MP4 = b"\x00\x00\x00 ftypmp42" + b"\x00" * 16
DATA_URL = "data:video/mp4;base64," + base64.b64encode(MP4).decode()
URL = "https://example.com/clips/v.mp4?token=secretvalue"
SMALL = DEFAULTS.replace(max_media_bytes=64)

# Which conformance case needs which operation another M task owns. A skip is never a
# pass, so the suite has to say exactly what it did not run.
OTHER_TASKS = {
    "media_sec__an_upload_is_owned_verified_and_immutable": "create_upload",
    "media_sec__another_org_cannot_resolve_or_finalize": "create_upload",
    "media_sec__oversize_and_unsupported_uploads_are_refused": "create_upload",
    "media_sec__staging_never_replaces_an_existing_object": "create_upload",
    "media_sec__a_refused_upload_stays_refused": "create_upload",
    "media_sec__an_expired_upload_window_says_so": "create_upload",
    "media_parity__staging_is_content_addressed_and_tenant_namespaced": "prepare",
}
OWNED_BY_M1 = ("media_sec__a_foreign_media_reference_is_not_staged",
               "media_sec__a_partial_request_stages_nothing")


class Deferred:
    """The operations M2 and M3 own, reported as missing hooks rather than answered.

    `NotImplementedError` would fail a conformance case, and a passthrough would be
    worse: `prepare` returning source refs would hand a worker media nobody prepared.
    """

    OWNERS = {"prepare": "M2", "create_upload": "M3", "finalize_upload": "M3"}

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def __getattr__(self, name):
        owner = self.OWNERS.get(name)
        if owner is None:
            return getattr(self.adapter, name)

        async def deferred(*args, **kwargs):
            raise MissingHook(f"{name} ({owner} owns it)")

        return deferred


def staging(*, limits=DEFAULTS, transport=None, resolve=None, jobs=None, objects=None):
    """The adapter with its collaborators injected: an in-memory object store, a
    fetcher with no socket, and the job rows `attach` reads a tenant from."""
    jobs = {} if jobs is None else jobs

    def job_org(job_id):
        org_id = jobs.get(job_id)
        if org_id is None:
            raise errors.NotFound(f"no job {job_id}")
        return org_id

    fetcher = fetch.MediaFetcher(limits, resolve=resolve or support.resolver([support.PUBLIC]),
                                 transport=transport.transport if transport else None,
                                 monotonic=support.Ticker(), log=support.Records())
    adapter = store.MediaStaging(objects or store.InMemoryObjectStore(), limits=limits,
                                 fetcher=fetcher, job_org=job_org)
    adapter.jobs = jobs
    return adapter


def factory(limits=None, **_kw) -> Harness:
    adapter = staging(limits=limits or DEFAULTS)
    return Harness(port=Deferred(adapter), clock=FakeClock(), ids=SequentialIds(),
                   extra={"admitted": adapter.jobs.__setitem__})


def request(adapter, *, org_id=b.ORG_A, refs=()):
    return b.request(Harness(port=adapter, clock=FakeClock(), ids=SequentialIds()),
                     org_id=org_id, refs=refs)


# --- materialization ---------------------------------------------------------------
def test_a_fetched_source_becomes_a_tenant_scoped_content_addressed_object():
    """MEDIA-SEC: the caller never names a path. The key comes from the tenant, the
    source digest and the profile version, and the object is really written."""
    adapter = staging(transport=support.Transport(support.response(body=MP4)))
    ref = asyncio.run(adapter.materialize(b.ORG_A, URL))
    assert ref.kind is MediaKind.url and ref.org_id == b.ORG_A
    assert ref.digest == fetch.digest_of(MP4) and ref.bytes == len(MP4)
    assert ref.mime == "video/mp4" and ref.handle.startswith("med_")
    assert ref.storage_ref == f"media/{b.ORG_A}/v1/{ref.digest.split(':')[1][:16]}/source"
    assert adapter.objects.objects[ref.storage_ref][0] == ref.digest
    assert adapter.objects.objects[ref.storage_ref][1] == MP4
    # nothing a caller sent appears in the key
    for leak in ("token", "secretvalue", "example.com", "clips"):
        assert leak not in ref.storage_ref
    assert asyncio.run(adapter.resolve_owned(b.ORG_A, ref.handle)) == ref


def test_a_data_url_source_is_materialized_inline():
    adapter = staging()
    ref = asyncio.run(adapter.materialize(b.ORG_A, DATA_URL))
    assert ref.kind is MediaKind.inline and ref.bytes == len(MP4)
    assert adapter.objects.objects[ref.storage_ref][1] == MP4


def test_two_organizations_never_share_an_object():
    """MEDIA-SEC: identical bytes from two tenants are two objects under two keys."""
    adapter = staging(transport=support.Transport(support.response(body=MP4),
                                                 support.response(body=MP4)))
    mine = asyncio.run(adapter.materialize(b.ORG_A, URL))
    theirs = asyncio.run(adapter.materialize(b.ORG_B, URL))
    assert mine.storage_ref != theirs.storage_ref
    assert b.ORG_A in mine.storage_ref and b.ORG_B in theirs.storage_ref
    assert mine.digest == theirs.digest
    # The handle is derived from the content, so both tenants use the same string for
    # the same bytes - and each one resolves it to its **own** object, never the other's.
    assert mine.handle == theirs.handle
    assert asyncio.run(adapter.resolve_owned(b.ORG_A, mine.handle)).storage_ref \
        == mine.storage_ref
    assert asyncio.run(adapter.resolve_owned(b.ORG_B, mine.handle)).storage_ref \
        == theirs.storage_ref
    # a tenant that never staged those bytes cannot resolve them by guessing the handle
    with pytest.raises(errors.NotFound):
        asyncio.run(adapter.resolve_owned(b.KEY_A, mine.handle))


def test_the_same_source_twice_is_one_object():
    """Content addressed: restaging identical bytes writes once and returns the same
    ref, so a retrying client does not double the storage or the handles."""
    adapter = staging(transport=support.Transport(support.response(body=MP4),
                                                 support.response(body=MP4)))
    first = asyncio.run(adapter.materialize(b.ORG_A, URL))
    second = asyncio.run(adapter.materialize(b.ORG_A, URL))
    assert first == second and len(adapter.objects.objects) == 1


def test_an_object_is_never_replaced_by_different_content():
    """MEDIA-SEC: staged content is immutable. A key that already holds other bytes is a
    conflict, never an overwrite - that is how an owner's video disappears."""
    adapter = staging(transport=support.Transport(support.response(body=MP4)))
    key = store.MediaStaging(adapter.objects)._key(b.ORG_A, fetch.digest_of(MP4), "v1", "source")
    asyncio.run(adapter.objects.put(key, b"squatted", "video/mp4"))
    with pytest.raises(errors.Conflict):
        asyncio.run(adapter.materialize(b.ORG_A, URL))
    assert adapter.objects.objects[key][1] == b"squatted"


def test_an_unsupported_source_is_refused_before_anything_is_stored():
    adapter = staging()
    for source in ("file:///etc/passwd", "s3://bucket/key", "", "javascript:alert(1)"):
        with pytest.raises(errors.InvalidRequest):
            asyncio.run(adapter.materialize(b.ORG_A, source))
    assert adapter.objects.objects == {} and adapter.refs == {}


def test_an_oversize_source_never_reaches_the_object_store():
    adapter = staging(limits=SMALL,
                      transport=support.Transport(support.response(body=b"x" * 400)))
    with pytest.raises(errors.RequestTooLarge):
        asyncio.run(adapter.materialize(b.ORG_A, URL))
    assert adapter.objects.objects == {}


# --- staging -----------------------------------------------------------------------
def test_staging_makes_the_canonical_payload_durable_with_a_digest_and_a_size():
    """02: "the original request payload is durable before acceptance". The payload is
    the canonical record, so every source's digest, size and type is durable with it,
    and the key is derived - `payload_ref` from the request is not read at all."""
    adapter = staging(transport=support.Transport(support.response(body=MP4)))
    ref = asyncio.run(adapter.materialize(b.ORG_A, URL))
    hostile = request(adapter, refs=(ref,)).model_copy(
        update={"payload_ref": "../../etc/passwd"})
    staged = asyncio.run(adapter.stage(b.ORG_A, hostile))
    payload = adapter.staged_payload(hostile.request_id)
    assert staged == (ref,)
    assert payload.ref == f"payloads/{b.ORG_A}/{hostile.request_id}.json"
    assert "etc/passwd" not in payload.ref
    body = adapter.objects.objects[payload.ref][1]
    assert body == codec.canonical_bytes(hostile)
    assert payload.digest == fetch.digest_of(body) and payload.bytes == len(body)
    # and the source metadata travelled with it
    assert ref.digest.encode() in body and str(ref.bytes).encode() in body


def test_a_request_may_only_be_staged_for_its_own_org():
    adapter = staging()
    with pytest.raises(errors.Forbidden):
        asyncio.run(adapter.stage(b.ORG_B, request(adapter, org_id=b.ORG_A)))
    assert adapter.payloads == {}


def test_a_foreign_or_oversize_reference_is_not_staged():
    """MEDIA-SEC: a JSON body cannot name another tenant's object, and a source over
    MAX_MEDIA_BYTES is refused before it is used."""
    adapter = staging()
    with pytest.raises(errors.NotFound):
        asyncio.run(adapter.stage(b.ORG_A, request(adapter, refs=(b.media(b.ORG_B),))))
    big = b.media(b.ORG_A, nbytes=DEFAULTS.max_media_bytes + 1)
    with pytest.raises(errors.RequestTooLarge):
        asyncio.run(adapter.stage(b.ORG_A, request(adapter, refs=(big,))))
    assert adapter.refs == {} and adapter.payloads == {}


def test_one_handle_cannot_carry_two_different_objects_in_one_request():
    adapter = staging()
    good = b.media(b.ORG_A)
    twin = good.model_copy(update={"digest": b.digest("other content"), "bytes": good.bytes + 1})
    with pytest.raises(errors.InvalidRequest):
        asyncio.run(adapter.stage(b.ORG_A, request(adapter, refs=(good, twin))))
    assert adapter.refs == {}


def test_a_staged_handle_keeps_the_content_it_has():
    adapter = staging()
    good = b.media(b.ORG_A)
    asyncio.run(adapter.stage(b.ORG_A, request(adapter, refs=(good,))))
    changed = good.model_copy(update={"digest": b.digest("different")})
    with pytest.raises(errors.Conflict):
        asyncio.run(adapter.stage(b.ORG_A, request(adapter, refs=(changed,))))
    assert asyncio.run(adapter.resolve_owned(b.ORG_A, good.handle)).digest == good.digest


def test_a_refused_request_stages_nothing_at_all():
    """02: "a staging failure creates no job or hold". The first reference must not be
    left behind either, or a client correcting the bad one finds half its request stored
    under a handle it can no longer change."""
    adapter = staging()
    good = b.media(b.ORG_A, handle="upl_first00000000000000000000000000000001")
    for bad in (b.media(b.ORG_A, handle="upl_second0000000000000000000000000000002",
                        nbytes=DEFAULTS.max_media_bytes + 1),
                b.media(b.ORG_B, handle="upl_third00000000000000000000000000000003"),
                b.media(b.ORG_A, handle="upl_fourth0000000000000000000000000000004",
                        kind=MediaKind.upload)):
        with pytest.raises(errors.DomainError):
            asyncio.run(adapter.stage(b.ORG_A, request(adapter, refs=(good, bad))))
        with pytest.raises(errors.NotFound):
            asyncio.run(adapter.resolve_owned(b.ORG_A, good.handle))
        assert adapter.payloads == {} and adapter.objects.objects == {}
    assert asyncio.run(adapter.stage(b.ORG_A, request(adapter, refs=(good,))))[0].handle \
        == good.handle


def test_an_upload_reference_is_resolved_not_trusted():
    """MEDIA-SEC: `kind=upload` means "an object I already own": an unknown handle is a
    404 rather than a staged reference to bytes that do not exist. M3 owns creating them;
    what M1 must never do is take the client's word for one."""
    adapter = staging()
    with pytest.raises(errors.NotFound):
        asyncio.run(adapter.stage(b.ORG_A, request(
            adapter, refs=(b.media(b.ORG_A, kind=MediaKind.upload),))))


def test_a_second_payload_for_one_request_id_cannot_replace_the_first():
    """The payload is immutable too: the same request id with a different body is a
    conflict, so an accepted request's durable body is the one it was accepted with."""
    adapter = staging()
    first = request(adapter)
    asyncio.run(adapter.stage(b.ORG_A, first))
    changed = first.model_copy(update={"model_revision": "other/model@2026-09-01"})
    with pytest.raises(errors.Conflict):
        asyncio.run(adapter.stage(b.ORG_A, changed))
    stored = adapter.objects.objects[adapter.staged_payload(first.request_id).ref][1]
    assert stored == codec.canonical_bytes(first)


def test_an_unstaged_payload_is_not_found():
    with pytest.raises(errors.NotFound):
        staging().staged_payload("00000000-0000-4000-8000-000000000001")


# --- attach ------------------------------------------------------------------------
def test_attach_takes_the_tenant_from_the_job_row():
    """r1 R52/R55: every ref must belong to the job's organization, and that organization
    is read from the job row - with an `org_id` argument the caller simply named the
    tenant its own refs belonged to."""
    jobs = {}
    adapter = staging(jobs=jobs)
    mine = asyncio.run(adapter.stage(b.ORG_A, request(adapter, refs=(b.media(b.ORG_A),))))
    theirs = asyncio.run(adapter.stage(b.ORG_B, request(adapter, org_id=b.ORG_B,
                                                        refs=(b.media(b.ORG_B),))))
    job = "11111111-0000-4000-8000-000000000001"
    jobs[job] = b.ORG_A
    with pytest.raises(errors.NotFound):
        asyncio.run(adapter.attach(job, theirs))
    assert adapter.by_job == {}, "a refused attach stored something"
    # an unknown job has no row to read a tenant from, with refs or without
    for refs in (mine, ()):
        with pytest.raises(errors.NotFound):
            asyncio.run(adapter.attach("22222222-0000-4000-8000-000000000002", refs))
    assert asyncio.run(adapter.attach(job, mine)) is None
    assert adapter.by_job[job] == mine


# --- the contract ------------------------------------------------------------------
def test_the_adapter_satisfies_the_media_store_protocol():
    adapter = staging()
    assert isinstance(adapter, ports.MediaStore)
    for name in ports.MediaStore.__protocol_attrs__:
        assert inspect.iscoroutinefunction(getattr(adapter, name)), name


def test_the_operations_another_task_owns_are_not_answered():
    """M2's `prepare` and M3's uploads raise rather than returning something plausible:
    refs to a prepared object nobody prepared would be caught by nothing."""
    adapter = staging()
    for call in (adapter.prepare("job", "v1"), adapter.create_upload(b.ORG_A, {}),
                 adapter.finalize_upload(b.ORG_A, "upl_x")):
        with pytest.raises(NotImplementedError):
            asyncio.run(call)


def test_the_exported_conformance_suite_runs_the_cases_m1_owns():
    """F-CONTRACT / r1 R32: the same cases the fake passes, against this adapter. A case
    needing an operation another M task owns is skipped naming it, never counted."""
    cases, _runner = SUITES["mediastore"]
    skipped: list[MissingHook] = []
    ran = run_cases(cases(), factory, skipped=skipped)
    report = {missing.case: missing.hook for missing in skipped}
    print("\nmediastore conformance against infrx.media.store.MediaStaging:")
    print(f"  ran {ran} of {len(cases())}: {sorted(OWNED_BY_M1)}")
    for case, missing in sorted(report.items()):
        print(f"  skipped {case}: needs {missing}")
    assert ran + len(skipped) == len(cases())
    assert {case: missing.split(" ")[0] for case, missing in report.items()} == OTHER_TASKS
    assert ran == len(OWNED_BY_M1) == 2
    # and the cases that ran are the ones M1 owns, not whichever happened not to skip
    assert set(OWNED_BY_M1) & set(report) == set()


def test_a_deferred_operation_is_a_skip_and_never_a_silent_pass():
    """The mechanism itself: `Deferred` must raise `MissingHook`, which `run_cases`
    refuses to treat as a pass unless the caller collects it."""
    cases, runner = SUITES["mediastore"]
    with pytest.raises(MissingHook) as caught:
        runner(factory)
    assert caught.value.hook.startswith(("create_upload", "prepare"))
    harness = factory()
    assert hook(harness, "admitted") is not None
    with pytest.raises(MissingHook):
        asyncio.run(harness.port.prepare("job", "v1"))
