"""M3: media collection - what nothing needs any more leaves the object store and the disk.

One `sweep()` is one pass; `run()` is the schedule hook the composition root starts (tests
call `sweep()` directly on an injected clock, so no background thread exists there).

What a pass removes:

* **upload destinations** of uploads that are no longer open (finalized - the verified
  bytes were copied to the source key -, refused or expired), and any destination with no
  upload record at all. Immediately: nothing reads a closed destination.
* **media objects** (`media/<org>/<profile>/<digest16>/{source,prepared}`) that no live
  job references and nothing has used for the grace period (`PROCESSING_CACHE_TTL_S`,
  the serving-media retention). That includes the blobs a failed staging wrote before it
  refused (the object was written, the ref never indexed) and media staged for a request
  that was never admitted.
* **processing-cache entries**: logical expiry (`ProcessingCache.sweep`), a byte cap that
  evicts the oldest entries no live job is using, and files on disk no index entry names
  (a previous process's cache, a crash's `.part`) once they have been stray for the grace.

What it never removes: anything a live job references. Liveness is asked of the job store
for every attached job **before** anything is deleted, and a lookup that fails aborts the
pass - a collector that cannot tell whether a job is live keeps its media.

**M6: do not schedule this collector's object deletion** (RV-03). Its liveness view is
this process's maps: a restarted process knows no job and deletes a live job's source
after the grace (`tests/m/test_retention.py` keeps that failure executable). Durable
deletion is `retention.RetentionCollector`; this module keeps the local processing-cache
hygiene until M6 phase 2 reduces it to that.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..contracts.records import UploadState
from .uploads import UPLOAD_KEY_PREFIX, MediaUploads

MEDIA_PREFIX = "media/"
log = logging.getLogger("infrx.media.gc")


@dataclass
class Swept:
    """What one pass did, for the log line and the tests."""

    deleted: list[str] = field(default_factory=list)
    uploads_expired: int = 0
    cache_expired: int = 0
    cache_evicted: int = 0
    stray_files: int = 0
    cache_bytes: int = 0


class MediaCollector:
    """Collects `store`'s unreferenced media. `is_live(job_id)` is the job store's answer
    to "is this job non-terminal" (async: it is a database read); an unknown job is not
    live."""

    def __init__(self, store: MediaUploads, *, is_live, grace_s: float | None = None,
                 max_cache_bytes: int | None = None) -> None:
        self.store = store
        self.is_live = is_live
        self.grace = timedelta(seconds=store.limits.processing_cache_ttl_s
                               if grace_s is None else grace_s)
        self.max_cache_bytes = max_cache_bytes
        self.stray_since: dict[str, datetime] = {}

    async def sweep(self) -> Swept:
        store, swept = self.store, Swept()
        now = store.now()
        # 1. Every attached job's liveness, before any deletion (fail closed).
        jobs = set(store.by_job) | set(store.prepared_by_job)
        live = {job for job in jobs if await self.is_live(job)}
        protected: set[str] = set()
        for job in jobs:
            refs = store.by_job.get(job, ()) + store.prepared_by_job.get(job, ())
            if job in live:
                protected.update(ref.storage_ref for ref in refs)
            else:
                store.by_job.pop(job, None)
                store.prepared_by_job.pop(job, None)
        for key in protected:
            store.idle_since[key] = now        # grace counts from the last pass that saw it live

        # 2. Uploads: close lapsed windows; only an open upload's destination is kept.
        # Listed first: an upload created while the listing is in flight must be in the
        # open set, or its destination is deleted as record-less (review B1).
        listed = await store.objects.keys(UPLOAD_KEY_PREFIX)
        # M5: this reads a process-local ticket authority's records. A durable one keeps
        # none in process (`store.uploads` raises), so the pass stops here, before any
        # delete: its destinations are M6's to sweep over the durable content rows.
        uploads = store.uploads
        swept.uploads_expired = await store.tickets.expire(max(1, len(uploads)))
        open_destinations = set()
        for handle, upload in list(uploads.items()):
            if upload.state is UploadState.created:
                open_destinations.add(store.upload_key(upload.org_id, handle))
            elif upload.state is not UploadState.finalized and now >= upload.expires_at + self.grace:
                del uploads[handle]            # a refused record is not kept forever
        for key in listed:
            if key not in open_destinations:
                await store.objects.delete(key)
                swept.deleted.append(key)

        # 3. Media objects: unreferenced and unused for the grace period.
        for key in await store.objects.keys(MEDIA_PREFIX):
            if key in protected:
                continue
            # Read after every await above, so a stage or attach during this pass counts.
            if now - store.idle_since.setdefault(key, now) >= self.grace:
                # Forgotten first: a stage during the delete's round trip is not_found
                # rather than admitted on an object that is about to vanish (review).
                self._forget(key)
                await store.objects.delete(key)
                swept.deleted.append(key)

        # 4. The processing cache.
        cache = store.cache
        if cache.enabled:
            swept.cache_expired = cache.sweep()
            in_use = {(ref.org_id, ref.digest, ref.profile_version)
                      for job in live for ref in store.prepared_by_job.get(job, ())}
            total = sum(entry.bytes for entry in cache.entries.values())
            if self.max_cache_bytes is not None:
                for key, entry in sorted(cache.entries.items(), key=lambda kv: kv[1].stored_at):
                    if total <= self.max_cache_bytes:
                        break
                    if key in in_use:
                        continue
                    cache.evict(key)
                    total -= entry.bytes
                    swept.cache_evicted += 1
            swept.cache_bytes = total
            swept.stray_files = self._stray_files(cache, now)
        return swept

    def _forget(self, key: str) -> None:
        """A deleted object is named by nothing: no ref resolves to it. (An upload's ticket
        is the repository's; its use re-checks the object, so a collected one is not_found.)"""
        store = self.store
        store.idle_since.pop(key, None)
        for index, ref in list(store.refs.items()):
            if ref.storage_ref == key:
                del store.refs[index]

    def _stray_files(self, cache, now: datetime) -> int:
        indexed = {entry.local_path for entry in cache.entries.values()}
        seen, removed = set(), 0
        for directory, _, names in os.walk(cache.root):
            for name in names:
                path = os.path.join(directory, name)
                if path in indexed:
                    continue
                seen.add(path)
                if now - self.stray_since.setdefault(path, now) >= self.grace:
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError:
                        pass
        self.stray_since = {p: t for p, t in self.stray_since.items()
                            if p in seen and os.path.exists(p)}
        return removed

    async def run(self, interval_s: float, *, sleep=asyncio.sleep) -> None:
        """The schedule hook: one pass every `interval_s`, forever. A failed pass is
        logged and the next one runs; nothing here decides to stop collecting."""
        while True:
            try:
                swept = await self.sweep()
                log.info("media sweep: %d objects, %d cache entries expired, %d evicted",
                         len(swept.deleted), swept.cache_expired, swept.cache_evicted)
            except Exception:
                log.exception("media sweep failed")
            await sleep(interval_s)
