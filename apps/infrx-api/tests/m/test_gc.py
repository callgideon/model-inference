#!/usr/bin/env python3
"""M3: media collection - GC never removes a live job's input and eventually removes
everything else.

    uv run --frozen pytest -q tests/m/test_gc.py

Time is the harness `FakeClock` (objects, uploads) and a float clock (the processing
cache); both are moved by hand. `sweep()` is called directly: no background task.
"""
from __future__ import annotations

import asyncio
import logging
import os

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import UploadState
from infrx.media import gc

from .test_uploads import CLIP, TTL, adapter_for, arrive, created, finalized, run

GRACE = TTL


class Jobs:
    """The job store's liveness answer, as `is_live` reads it."""

    def __init__(self) -> None:
        self.live: set[str] = set()
        self.fail = False

    async def __call__(self, job_id: str) -> bool:
        if self.fail:
            raise errors.DependencyUnavailable("job store unreachable")
        return job_id in self.live


def collector(adapter, jobs=None, **options):
    return gc.MediaCollector(adapter, is_live=jobs or Jobs(), **options)


def staged_job(adapter, jobs, ref, *, live=True):
    staged = run(adapter.stage(b.ORG_A, b.request(adapter.harness, refs=(ref,))))
    job_id = adapter.harness.ids.uuid()
    adapter.jobs[job_id] = b.ORG_A
    run(adapter.attach(job_id, staged))
    if live:
        jobs.live.add(job_id)
    return job_id


def media_keys(adapter):
    return sorted(k for k in adapter.objects.objects if k.startswith("media/"))


# --- live references --------------------------------------------------------------------
def test_a_live_jobs_input_is_never_collected(tmp_path):
    """Acceptance: GC cannot remove live referenced input - not its source, not its
    prepared artifact, however long the job has been waiting."""
    adapter, jobs = adapter_for(tmp_path), Jobs()
    _, ref = finalized(adapter)
    job_id = staged_job(adapter, jobs, ref)
    prepared = run(adapter.prepare(job_id, "v1"))
    sweeper = collector(adapter, jobs)
    for _ in range(3):
        adapter.clock.advance(GRACE * 2)
        swept = run(sweeper.sweep())
        assert [k for k in swept.deleted if k.startswith("media/")] == []
    assert ref.storage_ref in adapter.objects.objects
    assert prepared[0].storage_ref in adapter.objects.objects
    assert run(adapter.resolve_owned(b.ORG_A, ref.handle)) == ref


def test_input_is_collected_after_the_job_ends_and_the_grace_passes():
    adapter, jobs = adapter_for(), Jobs()
    _, ref = finalized(adapter)
    job_id = staged_job(adapter, jobs, ref)
    sweeper = collector(adapter, jobs)
    run(sweeper.sweep())
    jobs.live.discard(job_id)
    adapter.clock.advance(GRACE - 1)
    run(sweeper.sweep())
    assert ref.storage_ref in adapter.objects.objects
    adapter.clock.advance(1)
    assert ref.storage_ref in run(sweeper.sweep()).deleted
    assert media_keys(adapter) == []
    assert job_id not in adapter.by_job
    # nothing names the collected object any more
    with pytest.raises(errors.NotFound):
        run(adapter.resolve_owned(b.ORG_A, ref.handle))
    with pytest.raises(errors.NotFound):
        run(adapter.finalize_upload(b.ORG_A, ref.handle))


def test_a_lookup_that_fails_collects_nothing():
    """Fail closed: a pass that cannot tell whether a job is live deletes nothing."""
    adapter, jobs = adapter_for(), Jobs()
    _, ref = finalized(adapter)
    staged_job(adapter, jobs, ref)
    adapter.objects.seed(f"media/{b.ORG_B}/v1/{'0' * 16}/source", b"orphan")
    handle = created(adapter)
    sweeper = collector(adapter, jobs)
    run(sweeper.sweep())
    adapter.clock.advance(GRACE * 2)
    before = dict(adapter.objects.objects)
    jobs.fail = True
    with pytest.raises(errors.DependencyUnavailable):
        run(sweeper.sweep())
    assert adapter.objects.objects == before
    assert adapter.uploads[handle].state is UploadState.created


# --- orphans --------------------------------------------------------------------------
def test_a_failed_stage_blob_is_eventually_removed():
    """Acceptance: an object written by a staging that then refused (no ref indexed, no
    job) survives the grace and is removed after it."""
    adapter = adapter_for()
    orphan = f"media/{b.ORG_A}/v1/{'a' * 16}/source"
    adapter.objects.seed(orphan, b"half a request")
    sweeper = collector(adapter)
    assert run(sweeper.sweep()).deleted == []
    adapter.clock.advance(GRACE - 1)
    assert run(sweeper.sweep()).deleted == []
    adapter.clock.advance(1)
    assert run(sweeper.sweep()).deleted == [orphan]


def test_media_staged_for_a_request_never_admitted_is_removed():
    adapter = adapter_for()
    _, ref = finalized(adapter)
    run(adapter.stage(b.ORG_A, b.request(adapter.harness, refs=(ref,))))
    sweeper = collector(adapter)
    adapter.clock.advance(GRACE)
    assert ref.storage_ref in run(sweeper.sweep()).deleted


def test_recent_use_renews_the_grace():
    """A stage (or attach) is a use: media restaged just now is not collected because it
    was first staged long ago."""
    adapter = adapter_for()
    _, ref = finalized(adapter)
    sweeper = collector(adapter)
    run(sweeper.sweep())
    adapter.clock.advance(GRACE - 1)
    run(adapter.stage(b.ORG_A, b.request(adapter.harness, refs=(ref,))))
    adapter.clock.advance(2)
    assert run(sweeper.sweep()).deleted == []
    assert ref.storage_ref in adapter.objects.objects


def test_attach_is_a_use():
    adapter, jobs = adapter_for(), Jobs()
    _, ref = finalized(adapter)
    staged = run(adapter.stage(b.ORG_A, b.request(adapter.harness, refs=(ref,))))
    sweeper = collector(adapter, jobs)
    run(sweeper.sweep())
    adapter.clock.advance(GRACE - 1)
    job_id = adapter.harness.ids.uuid()
    adapter.jobs[job_id] = b.ORG_A
    run(adapter.attach(job_id, staged))              # attached to a job that already ended
    adapter.clock.advance(2)
    assert run(sweeper.sweep()).deleted == []


# --- uploads ----------------------------------------------------------------------------
def test_upload_destinations_live_exactly_as_long_as_the_upload_is_open():
    adapter = adapter_for()
    open_handle = created(adapter)
    arrive(adapter, open_handle, CLIP)
    done, ref = finalized(adapter)
    refused = created(adapter, max_bytes=8)
    arrive(adapter, refused, CLIP)
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.finalize_upload(b.ORG_A, refused))
    stray = f"uploads/{b.ORG_B}/upl_{'s' * 30}"
    adapter.objects.seed(stray, b"no record")
    deleted = set(run(collector(adapter).sweep()).deleted)
    key = adapter.upload_key
    assert deleted == {key(b.ORG_A, done), key(b.ORG_A, refused), stray}
    assert key(b.ORG_A, open_handle) in adapter.objects.objects
    assert ref.storage_ref in adapter.objects.objects        # the copy stays
    assert run(adapter.resolve_owned(b.ORG_A, done)) == ref


def test_a_lapsed_upload_window_is_closed_and_its_bytes_removed():
    adapter = adapter_for()
    handle = created(adapter)
    arrive(adapter, handle, CLIP)
    sweeper = collector(adapter)
    adapter.clock.advance(TTL)
    swept = run(sweeper.sweep())
    assert swept.uploads_expired == 1
    assert adapter.uploads[handle].state is UploadState.expired
    assert swept.deleted == [adapter.upload_key(b.ORG_A, handle)]


def test_a_refused_upload_record_is_dropped_after_the_grace():
    adapter = adapter_for()
    handle = created(adapter, max_bytes=8)
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.RequestTooLarge):
        run(adapter.finalize_upload(b.ORG_A, handle))
    sweeper = collector(adapter)
    adapter.clock.advance(TTL + GRACE - 1)
    run(sweeper.sweep())
    assert handle in adapter.uploads
    adapter.clock.advance(1)
    run(sweeper.sweep())
    assert handle not in adapter.uploads


# --- the processing cache ------------------------------------------------------------
def prepared_job(adapter, jobs, data, *, live):
    handle = created(adapter)
    arrive(adapter, handle, data)
    ref = run(adapter.finalize_upload(b.ORG_A, handle))
    job_id = staged_job(adapter, jobs, ref, live=live)
    return job_id, run(adapter.prepare(job_id, "v1"))[0]


def test_the_sweep_runs_the_processing_cache_expiry(tmp_path):
    """M2's `ProcessingCache.sweep`, which nothing called, now runs on every pass."""
    adapter, jobs = adapter_for(tmp_path), Jobs()
    _, prepared = prepared_job(adapter, jobs, CLIP, live=False)
    path = adapter.local_uri(prepared).removeprefix("file://")
    sweeper = collector(adapter, jobs)
    assert run(sweeper.sweep()).cache_expired == 0
    adapter.cache.clock.now += TTL
    assert run(sweeper.sweep()).cache_expired == 1
    assert not os.path.exists(path)


def test_the_cache_cap_evicts_idle_entries_and_never_a_live_jobs(tmp_path):
    """M2 limit 8: the cache is bounded. Oldest idle entries go first; a live job's entry
    stays even when the cap cannot be met without it."""
    from . import support
    adapter, jobs = adapter_for(tmp_path), Jobs()
    _, old = prepared_job(adapter, jobs, CLIP, live=True)
    adapter.cache.clock.now += 1
    _, idle = prepared_job(adapter, jobs, support.mp4(seconds=20.0), live=False)
    adapter.cache.clock.now += 1
    _, new = prepared_job(adapter, jobs, support.mp4(seconds=30.0), live=False)
    swept = run(collector(adapter, jobs, max_cache_bytes=len(CLIP) * 2).sweep())
    assert swept.cache_evicted == 1 and swept.cache_bytes <= len(CLIP) * 2
    adapter.local_uri(old)
    adapter.local_uri(new)
    with pytest.raises(errors.NotFound):
        adapter.local_uri(idle)
    swept = run(collector(adapter, jobs, max_cache_bytes=1).sweep())
    adapter.local_uri(old)                                  # over the cap, but live
    assert swept.cache_bytes == len(CLIP)


def test_stray_cache_files_are_removed_after_the_grace(tmp_path):
    """M2 limit 7: a previous process's files (or a crash's `.part`) are not in this
    process's index; they are removed once they have been stray for the grace."""
    adapter, jobs = adapter_for(tmp_path), Jobs()
    _, prepared = prepared_job(adapter, jobs, CLIP, live=False)
    indexed = adapter.local_uri(prepared).removeprefix("file://")
    stray = tmp_path / b.ORG_B / "v1" / ("b" * 16) / "source.mp4.123.part"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"left behind")
    sweeper = collector(adapter, jobs)
    assert run(sweeper.sweep()).stray_files == 0
    adapter.clock.advance(GRACE)
    assert run(sweeper.sweep()).stray_files == 1
    assert not stray.exists() and os.path.exists(indexed)


# --- the schedule hook -----------------------------------------------------------------
def test_run_sweeps_on_a_schedule_and_survives_a_failed_pass(caplog):
    adapter, jobs = adapter_for(), Jobs()
    _, ref = finalized(adapter)
    job_id = staged_job(adapter, jobs, ref)
    sweeper = collector(adapter, jobs)
    passes = []

    async def sleep(seconds):
        passes.append(seconds)
        jobs.fail = len(passes) == 1               # the second pass fails, the third runs
        if len(passes) == 2:
            jobs.live.discard(job_id)              # the job ends; the third pass collects
        adapter.clock.advance(GRACE)
        if len(passes) == 3:
            raise asyncio.CancelledError

    with caplog.at_level(logging.INFO, logger="infrx.media.gc"):
        with pytest.raises(asyncio.CancelledError):
            run(sweeper.run(60.0, sleep=sleep))
    assert passes == [60.0, 60.0, 60.0]
    assert "media sweep failed" in caplog.text
    assert ref.storage_ref not in adapter.objects.objects
