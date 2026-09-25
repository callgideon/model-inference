#!/usr/bin/env python3
"""M6 slice 3: bounded process maps and a local processing cache that survives replacement.

    uv run --frozen pytest -q tests/m/test_cache_bounds.py

The disk is the cache's index: a new `ProcessingCache` over the same root finds, expires
and evicts what a previous process wrote. `max_bytes` is the high water; a `pin` (a shared
`flock`, held by whoever hands the file to the engine) is never evicted, by any process; a
cache full of pinned media refuses the put retryably, and the durable artifact - the
record - is written either way.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import textwrap

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.media import prepare, probe, store
from infrx.media.prepare import PART_GRACE_S, ProcessingCache

from . import support
from .test_uploads import CacheClock, adapter_for, run

ORG = b.ORG_A
PROBED = probe.probe(support.mp4(seconds=4.0))
assert prepare.LOW_WATER == 0.8          # the eviction cases below are sized to it
DAY = 86_400.0


def clip(n: int) -> bytes:
    """A distinct valid clip per n (the duration is in the header)."""
    return support.mp4(seconds=1.0 + n / 1000)


def data_url(data: bytes) -> str:
    return "data:video/mp4;base64," + base64.b64encode(data).decode()


def digest(data: bytes) -> str:
    from infrx.media.fetch import digest_of
    return digest_of(data)


def cache_at(root, clock=None, **options) -> ProcessingCache:
    return ProcessingCache(str(root), ttl_s=DAY, clock=clock or CacheClock(), **options)


def put(cache, n: int, *, size: int = 1000):
    data = (clip(n) + bytes(size))[:size] if size else clip(n)      # exactly `size` bytes
    return cache.put(ORG, digest(data), "v1", data, PROBED)


# --- process maps ---------------------------------------------------------------------------
def test_every_process_map_stays_bounded_under_many_requests(monkeypatch):
    """Many materializations, stages and attaches: `refs`, `payloads`, `by_job` and
    `prepared_by_job` each keep at most the bound (their durable records are elsewhere:
    content rows, the staged payload object, D2's attachments), and the newest entry is
    always there - a request's own hand-off is never the one evicted."""
    monkeypatch.setattr(store, "MAX_PROCESS_ENTRIES", 16)
    adapter = adapter_for()
    for n in range(60):
        ref = run(adapter.materialize(ORG, data_url(clip(n))))
        request = b.request(adapter.harness, org_id=ORG, refs=(ref,))
        (staged,) = run(adapter.stage(ORG, request))
        job = f"00000000-0000-4000-8000-{n:012x}"
        adapter.jobs[job] = ORG
        run(adapter.attach(job, (staged,)))
        adapter.prepared_by_job[job] = (staged,)
        assert adapter.refs[(ORG, ref.handle)] == ref and adapter.by_job[job] == (staged,)
        assert adapter.staged_payload(request.request_id).ref.endswith(".json")
    sizes = [len(m) for m in (adapter.refs, adapter.payloads, adapter.by_job,
                              adapter.prepared_by_job)]
    assert sizes == [16, 16, 16, 16], sizes


# --- the local cache across a replacement ------------------------------------------------
def test_a_replaced_process_finds_and_expires_what_the_old_one_cached(tmp_path):
    """The old process's entry is found by the new one (content-hash checked, its life
    from the file's mtime), and the new one's sweep - with an empty index - removes it at
    its end of life, a crashed put's `.part` after `PART_GRACE_S`, and a file dated in the
    future (a touched or restored mtime cannot extend the retention)."""
    clock = CacheClock()
    old = cache_at(tmp_path, clock)
    entry = put(old, 1, size=0)
    part = os.path.join(os.path.dirname(entry.local_path), "source.mp4.99.part")
    with open(part, "wb") as handle:
        handle.write(b"half")
    os.utime(part, (clock.now, clock.now))
    future = put(old, 2, size=0).local_path
    os.utime(future, (clock.now + DAY, clock.now + DAY))
    new = cache_at(tmp_path, clock)
    found = new.get(ORG, digest(clip(1)), "v1", "video/mp4")
    assert found is not None and found.local_path == entry.local_path
    new.entries.clear()
    clock.now += PART_GRACE_S - 1
    assert new.sweep() == 1 and not os.path.exists(future)        # only the future-dated
    clock.now += 1
    assert new.sweep() == 1 and not os.path.exists(part)
    assert os.path.exists(entry.local_path)
    clock.now += DAY - PART_GRACE_S
    assert new.sweep() == 1 and not os.path.exists(entry.local_path)
    assert new.get(ORG, digest(clip(1)), "v1", "video/mp4") is None


def test_another_preparation_version_is_a_miss_not_the_old_bytes(tmp_path):
    """The key is (tenant, source digest, profile/preparer version) and the path carries
    all three: an entry prepared under another version is not served."""
    cache = cache_at(tmp_path)
    entry = put(cache, 1, size=0)
    assert cache.get(ORG, digest(clip(1)), "v2", "video/mp4") is None
    assert cache.get(ORG, digest(clip(1)), "v1", "video/mp4") == entry


# --- the high water, pins, admission under pressure ---------------------------------------
def test_above_the_high_water_the_oldest_unpinned_go_down_to_the_low_water(tmp_path):
    clock = CacheClock()
    cache = cache_at(tmp_path, clock, max_bytes=4000)
    entries = []
    for n in range(4):
        entries.append(put(cache, n))
        clock.now += 1
    with cache.pin(entries[0].local_path):
        entries.append(put(cache, 4))                 # 5000 > 4000: evict to <= 3200
    kept = [os.path.exists(e.local_path) for e in entries]
    assert kept == [True, False, False, True, True], kept
    assert sum(size for _, size, _ in cache._files()) <= 3200


def test_a_cache_full_of_pinned_media_refuses_the_put_and_writes_nothing(tmp_path):
    cache = cache_at(tmp_path, max_bytes=2000)
    held = [put(cache, n) for n in range(2)]
    with cache.pin(held[0].local_path), cache.pin(held[1].local_path):
        with pytest.raises(errors.DependencyUnavailable) as refused:
            put(cache, 2)
        assert refused.value.retry_after_s == 30
    names = sorted(name for _, _, files in os.walk(tmp_path) for name in files)
    assert names == ["source.mp4", "source.mp4"]
    assert [os.path.exists(e.local_path) for e in held] == [True, True]
    put(cache, 2)                                     # unpinned: room is made
    assert sum(size for _, size, _ in cache._files()) <= 2000


def test_the_sweep_never_removes_a_pinned_file(tmp_path):
    clock = CacheClock()
    cache = cache_at(tmp_path, clock)
    entry = put(cache, 1)
    clock.now += DAY
    with cache.pin(entry.local_path):
        assert cache.sweep() == 0 and os.path.exists(entry.local_path)
    assert cache.sweep() == 1 and not os.path.exists(entry.local_path)


def test_a_pin_on_a_file_already_gone_is_not_found(tmp_path):
    with pytest.raises(errors.NotFound):
        with cache_at(tmp_path).pin(str(tmp_path / "gone.mp4")):
            pass


def test_another_process_pin_protects_the_file(tmp_path):
    """Cross-process: the worker's pin (another OS process holding a shared flock) keeps
    the file through the gateway's eviction; once that process exits, it can go."""
    clock = CacheClock()
    cache = cache_at(tmp_path, clock, max_bytes=2000)
    first = put(cache, 1)
    clock.now += 1
    second = put(cache, 2)
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import fcntl, os, sys
        fd = os.open({first.local_path!r}, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_SH)
        print("pinned", flush=True)
        sys.stdin.readline()
        """)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "pinned"
        put(cache, 3)                                  # 3000 > 2000: evict to <= 1600
        assert os.path.exists(first.local_path) and not os.path.exists(second.local_path)
    finally:
        holder.communicate("")
    clock.now += 1
    put(cache, 4)
    assert not os.path.exists(first.local_path)


def test_a_full_cache_still_writes_the_durable_artifact_and_prepare_retries(tmp_path):
    """Correctness never depends on the cache: with it full of pinned media `prepare`
    writes the durable prepared artifact (the record) and then refuses retryably - the
    job is prepared again later - and once there is room the same call succeeds."""
    adapter = adapter_for(tmp_path)
    adapter.cache.max_bytes = len(clip(1)) + 10
    blocker = adapter.cache.put(ORG, digest(clip(1)), "v1", clip(1), PROBED)
    ref = run(adapter.materialize(ORG, data_url(clip(2))))
    job = "00000000-0000-4000-8000-00000000abcd"
    adapter.jobs[job] = ORG
    run(adapter.attach(job, (ref,)))
    prepared_key = adapter._key(ORG, ref.digest, "v1", "prepared")
    with adapter.cache.pin(blocker.local_path):
        with pytest.raises(errors.DependencyUnavailable):
            run(adapter.prepare(job, "v1"))
        assert run(adapter.objects.head(prepared_key)) == ref.digest
    (prepared,) = run(adapter.prepare(job, "v1"))
    assert adapter.local_uri(prepared).startswith("file://")
    assert not os.path.exists(blocker.local_path)


def test_no_cache_configured_changes_nothing_about_correctness():
    adapter = adapter_for()
    assert not adapter.cache.enabled and adapter.cache.sweep() == 0
    ref = run(adapter.materialize(ORG, data_url(clip(5))))
    job = "00000000-0000-4000-8000-00000000abce"
    adapter.jobs[job] = ORG
    run(adapter.attach(job, (ref,)))
    (prepared,) = run(adapter.prepare(job, "v1"))
    assert run(adapter.objects.head(prepared.storage_ref)) == ref.digest



def test_a_put_in_flight_is_never_evicted(tmp_path):
    """Another put's `.part` (being written this moment) is not the high water's to take:
    only the sweep removes a `.part`, and only after `PART_GRACE_S`."""
    clock = CacheClock()
    cache = cache_at(tmp_path, clock, max_bytes=2000)
    old = put(cache, 1)
    part = os.path.join(os.path.dirname(old.local_path), "source.mp4.4242.part")
    with open(part, "wb") as handle:
        handle.write(bytes(1000))
    os.utime(part, (clock.now - 1, clock.now - 1))           # older than every entry
    put(cache, 2)
    put(cache, 3)
    assert os.path.exists(part)


def test_an_expired_pinned_file_is_a_miss_and_is_not_removed(tmp_path):
    """Fix round R3: `get` (and so `prepare` and the worker's `local_uri`) past the life
    of a pinned file answers a miss but leaves the file to its pin; the next look after
    the pin is released removes it."""
    clock = CacheClock()
    cache = cache_at(tmp_path, clock)
    data = clip(1)
    entry = cache.put(ORG, digest(data), "v1", data, PROBED)
    clock.now += DAY
    with cache.pin(entry.local_path):
        assert cache.get(ORG, digest(data), "v1") is None
        assert os.path.exists(entry.local_path), "a pinned file was removed by get()"
    assert cache.get(ORG, digest(data), "v1") is None
    assert not os.path.exists(entry.local_path)


def test_a_pin_that_loses_the_race_with_an_eviction_is_not_found(tmp_path, monkeypatch):
    """Fix round R4: the sweep removes the file between `pin`'s open and its lock. The
    pin then holds an unlinked inode, so it answers `not_found` (prepare again) rather
    than yielding a path that is gone - or a later file at that path it does not hold."""
    clock = CacheClock()
    cache = cache_at(tmp_path, clock)
    entry = put(cache, 1)
    clock.now += DAY
    flock = prepare.fcntl.flock

    def evict_first(fd, operation):
        if operation == prepare.fcntl.LOCK_SH:          # the pin, before its lock lands
            assert cache.sweep() == 1
        return flock(fd, operation)
    monkeypatch.setattr(prepare.fcntl, "flock", evict_first)
    with pytest.raises(errors.NotFound), cache.pin(entry.local_path):
        pytest.fail("pin() succeeded on a file the sweep removed")


def test_an_eviction_that_loses_the_race_with_a_put_keeps_the_new_file(tmp_path, monkeypatch):
    """Fix round R4, the evictor's side: a `put` replaces the expired file between the
    sweep's open and its lock. The sweep holds the old inode, so the fresh file at that
    path - which someone may pin next - is not its to remove."""
    clock = CacheClock()
    cache = cache_at(tmp_path, clock)
    data = clip(1)
    entry = cache.put(ORG, digest(data), "v1", data, PROBED)
    clock.now += DAY
    flock = prepare.fcntl.flock

    def put_first(fd, operation):
        if operation & prepare.fcntl.LOCK_EX:           # the sweep, before its lock lands
            monkeypatch.setattr(prepare.fcntl, "flock", flock)
            cache.put(ORG, digest(data), "v1", data, PROBED)
        return flock(fd, operation)
    monkeypatch.setattr(prepare.fcntl, "flock", put_first)
    assert cache.sweep() == 0
    assert os.path.exists(entry.local_path)
    assert cache.get(ORG, digest(data), "v1") is not None


def test_the_cache_counts_evictions_expiries_and_refusals(tmp_path):
    """WR-I8-M6-1: each high-water eviction, each expiry the sweep removes and each refused
    put is counted by reason on the registry the worker hands the cache."""
    from infrx.observe.metrics import Registry
    reg, clock = Registry("worker"), CacheClock()
    cache = cache_at(tmp_path, clock, max_bytes=2000, metrics=reg)
    for n in range(2):
        put(cache, n)
        clock.now += 1
    kept = put(cache, 2)                          # 3000 > 2000: both old ones go (low water)
    with cache.pin(kept.local_path):
        with pytest.raises(errors.DependencyUnavailable):
            put(cache, 3, size=1500)              # 2500, and the rest is pinned
    clock.now += DAY
    assert cache.sweep() == 1
    counts = (reg.value("infrx_processing_cache_evicted_total", reason="high_water"),
              reg.value("infrx_processing_cache_evicted_total", reason="expired"),
              reg.value("infrx_processing_cache_refused_total"))
    assert counts == (2, 1, 1), counts


# --- M6 phase-2 verification (R6 X6/X7/X11/X17/X19): the guards no case pinned ------------
# Failure oracles, one per surviving mutant: a sweep that takes a `.part` a writer is still
# writing (X6), a file dated inside the clock-step slack removed or missed (X7), a re-put
# that counts the file it replaces against the high water (X11), a map that evicts an entry
# just re-written (X17), a prepared artifact registered as another kind or for no job (X19).
def test_a_part_being_written_now_is_not_swept(tmp_path):
    clock = CacheClock()
    cache = cache_at(tmp_path, clock)
    entry = put(cache, 1)
    part = entry.local_path + ".4242.part"
    with open(part, "wb") as handle:
        handle.write(b"half")
    os.utime(part, (clock.now, clock.now))
    clock.now += 1
    assert cache.sweep() == 0 and os.path.exists(part)


def test_a_file_dated_inside_the_clock_step_slack_is_believed(tmp_path):
    clock = CacheClock()
    cache = cache_at(tmp_path, clock)
    entry = put(cache, 1, size=0)
    ahead = clock.now + 30                        # a clock step inside the 60 s slack
    os.utime(entry.local_path, (ahead, ahead))
    fresh = cache_at(tmp_path, clock)             # another process: finds it on disk
    assert fresh.get(ORG, digest(clip(1)), "v1", "video/mp4") is not None
    assert fresh.sweep() == 0 and os.path.exists(entry.local_path)


def test_putting_a_file_again_does_not_count_it_against_the_high_water(tmp_path):
    clock = CacheClock()
    cache = cache_at(tmp_path, clock, max_bytes=2000)
    first = put(cache, 1)
    clock.now += 1
    second = put(cache, 2)                        # exactly at the high water
    clock.now += 1
    put(cache, 1)                                 # the same path, replaced: no room needed
    assert os.path.exists(first.local_path) and os.path.exists(second.local_path)


def test_a_rewritten_map_entry_is_the_newest(monkeypatch):
    recent = store.Recent(limit=2)
    recent["a"], recent["b"] = 1, 2
    recent["a"] = 3                               # rewritten: now the newest
    recent["c"] = 4
    assert dict(recent) == {"a": 3, "c": 4}


def test_the_prepared_artifact_is_registered_as_prepared_for_its_job(tmp_path):
    registered = []

    class Content:
        async def register(self, identity):
            registered.append(identity)
    adapter = adapter_for(tmp_path, content=Content())
    ref = run(adapter.materialize(ORG, data_url(clip(3))))
    job = "00000000-0000-4000-8000-00000000abcf"
    adapter.jobs[job] = ORG
    run(adapter.attach(job, (ref,)))
    (prepared,) = run(adapter.prepare(job, "v1"))
    (row,) = [i for i in registered if i.object_key == prepared.storage_ref]
    assert (row.kind.value, row.job_id, row.digest, row.bytes) == \
        ("prepared", job, ref.digest, prepared.bytes)
