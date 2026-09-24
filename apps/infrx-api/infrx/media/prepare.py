"""M2: preparation - the phase between "the customer named some media" and "a worker may
run it", and the only place a fact about a clip is established.

Three things happen here and nowhere else.

**Materialization with a probe.** `prepare_request` takes the validated
`NormalizedRequest` G1 built, materializes every `video_url` through M1's store, and
measures the clip *from the stored bytes* (`probe.py`). The caller's `Content-Type`, file
extension and JSON are claims; `duration_s`, the frame size and the codec are measurements.
The engine's frame budget is computed from `duration_s` (`worker/engine.py`
`budget_kwargs`), so a duration nobody measured is a frame budget the customer chose - and
`MAX_VIDEO_SECONDS` (120 s), which is what keeps every accepted clip at the trained 2 fps
(`research/workloads/marlin-sop.md` §1.5), would be unenforceable. S2M D2 and D9.

**The rewrite.** The prepared record's messages carry `{"video_url": {"ref": <handle>}}`
in place of the customer's URL or inline payload, one media part per staged ref, in order
(R58). The accepted payload that `stage` makes durable therefore holds our reference and
not the customer's URL (S2M D1), and `PreparedRequest` built from it satisfies W1's
`messages_for` pairing rule.

**The processing cache.** `prepare(job_id, profile)` is the port operation: it re-reads the
durable object, re-probes it, persists the prepared artifact and materializes the bytes
into a versioned, tenant-scoped local cache with a 7-day life (`PROCESSING_CACHE_TTL_S`),
so the engine has something it can actually open (S2M D3: a bare object key is not a file).
`local_uri(ref)` is the `file://` form a worker passes to vLLM started with
`--allowed-local-media-path <root>`.

What is deliberately *not* here: transcoding. This host has no ffmpeg (CLAUDE.md) and the
pinned environment has no media dependency, so profile `v1` re-encodes nothing; the frame
and pixel budget is applied by the engine's `mm_processor_kwargs` from the measured
duration, which is the pinned Path A of the launch profile. When a transcode arrives it
belongs behind `_prepared_bytes`, which is the one place that decides what the prepared
artifact contains.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from dataclasses import dataclass

from ..contracts import errors
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import MediaRef, NormalizedRequest
from . import probe as probing
from .fetch import digest_of
from .store import MediaStaging, valid_digest, valid_org, valid_profile

VIDEO_PART = "video_url"
# The rewritten reference. A part stays exactly `{type, video_url}` (R58, and W1's
# `_part` checks that key set), but what the value names is ours: an opaque tenant-scoped
# handle rather than the customer's URL or a 64 MiB inline payload.
REF_KEY = "ref"
SOURCE_FILENAME = "source"


@dataclass(frozen=True)
class MediaProfile:
    """The pinned preprocessing profile, in one table.

    Every limit preparation enforces is read from here, so tightening the profile (S2M may)
    is one object and not a search through the module. The numbers come from
    `PilotSettings` where one exists and from the launch profile
    (`research/workloads/marlin-sop.md` §1.5, §2.5) where it does not.

    `max_duration_s` is not a tunable: raising it above 120 s takes accepted clips below the
    2 fps the model was trained at (240 frames ÷ duration), which is a new serving version
    rather than a configuration change.
    """

    version: str = "v1"
    max_duration_s: float = DEFAULTS.max_video_seconds
    max_bytes: int = DEFAULTS.max_media_bytes
    # One clip per request: the 120 s worst case is ~23,560 prompt tokens against a 32,768
    # context, so a second clip does not fit (`validate.MAX_VIDEO_PARTS`, `--limit-mm-per-
    # prompt video:1`). Restated here because `media/` must not import the gateway.
    max_parts: int = 1
    # Containers with a parser in `probe.py`, and the codecs the pilot will serve. Both are
    # allow-lists: an unprobeable container or an unknown codec is refused, never guessed.
    allowed_mime: frozenset[str] = frozenset({probing.MP4_MIME, probing.QUICKTIME_MIME,
                                              probing.WEBM_MIME})
    allowed_codecs: frozenset[str] = frozenset({"h264", "h265", "vp8", "vp9", "av1"})

    @classmethod
    def pinned(cls, limits: PilotSettings = DEFAULTS, *, version: str = "v1") -> MediaProfile:
        """The profile as this deployment's settings state it."""
        return cls(version=valid_profile(version), max_duration_s=limits.max_video_seconds,
                   max_bytes=limits.max_media_bytes)

    def check(self, probed: probing.Probed, nbytes: int) -> None:
        """The measured clip against the profile, or a typed refusal naming the bound."""
        if nbytes > self.max_bytes:
            raise errors.RequestTooLarge(f"{nbytes} bytes exceeds MAX_MEDIA_BYTES "
                                         f"{self.max_bytes}")
        if probed.mime not in self.allowed_mime:
            raise errors.UnsupportedMedia("the media type is not a supported video",
                                          param="messages")
        if probed.codec not in self.allowed_codecs:
            raise errors.UnsupportedMedia("the video codec is not supported", param="messages")
        if probed.duration_s > self.max_duration_s:
            # The customer's number is never the one compared: this is the probe's.
            raise errors.UnsupportedMedia(
                f"the video is longer than {self.max_duration_s:.0f}s", param="messages")


# MPILOT (review PAR-4): how far in the future a cache file's mtime may be and still be
# believed. Writer and reader share one host clock; this only absorbs a step adjustment.
FUTURE_MTIME_SLACK_S = 60.0

# What a probed container is called on disk. The engine opens the file by path, and a
# decoder that sniffs is one more thing to be wrong about.
EXTENSIONS = {probing.MP4_MIME: "mp4", probing.QUICKTIME_MIME: "mov", probing.WEBM_MIME: "webm"}


@dataclass(frozen=True)
class CacheEntry:
    local_path: str
    probed: probing.Probed | None          # None: found on disk, not measured here (MPILOT)
    bytes: int
    stored_at: float


class ProcessingCache:
    """The versioned, tenant-scoped preprocessing cache: bytes a worker can open, keyed by
    (organization, source digest, profile version) and gone after `PROCESSING_CACHE_TTL_S`.

    Three properties it exists for:

    * **never cross-tenant.** The organization is in the index key *and* in the path, and
      both come from `valid_org`, so one tenant's entry is not reachable from another's -
      not by reusing a handle, not by guessing a digest, not by a `..` in anything, because
      nothing caller-shaped reaches the path at all.
    * **versioned.** The profile version is a segment of the **path** as well as part of
      the index key (R61, amended). With it in the key alone, two profiles of one source
      shared one file: the second `put` overwrote the first, and expiring v1 deleted the
      bytes v2's live entry pointed at (review B2). The path now mirrors `_key`'s layout
      exactly - tenant, profile, digest - so the same three things namespace the durable
      object and the local copy.
    * **expiring.** 7 days is a retention obligation, not a cache-eviction preference
      (01 "Privacy and retention"): an expired entry is unreadable *and* the file is
      removed, so `sweep()` on a cold cache still deletes.

    MPILOT gap 2: the index is process-local, and the pilot's worker is another process
    over the same `PROCESSING_CACHE_DIR` (read-only there). So a lookup that knows the
    media type (`local_uri`) finds a miss on disk: the path its key builds, the file's
    bytes checked against the key's content hash, its life counted from the file's mtime -
    which `put` sets to the entry's `stored_at`, so the disk says what the index said.
    """

    def __init__(self, root: str, *, ttl_s: float = DEFAULTS.processing_cache_ttl_s,
                 clock=time.time) -> None:
        self.root = os.path.abspath(root) if root else ""
        self.ttl_s = ttl_s
        self.clock = clock
        self.entries: dict[tuple[str, str, str], CacheEntry] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.root)

    def path_for(self, org_id: str, profile: str, digest: str, mime: str) -> str:
        """`<root>/<org uuid>/<profile>/<16 hex of digest>/source.<ext>`, from validated parts.

        R61 as amended: the same three segments `_key` builds a durable key from, in the
        same order. All three are validated here rather than by whoever calls: a path is the
        one place where "the caller cannot name it" has to be true of every caller,
        including a later one nobody has written yet.
        """
        if not self.enabled:
            raise errors.DependencyUnavailable("no processing cache is configured")
        extension = EXTENSIONS.get(mime)
        if extension is None:
            raise errors.UnsupportedMedia("the media type is not a supported video",
                                          param="messages")
        path = os.path.join(self.root, valid_org(org_id), valid_profile(profile),
                            valid_digest(digest), f"{SOURCE_FILENAME}.{extension}")
        # Defence in depth: the parts above cannot contain a separator, and if a later
        # change lets one through, the path does not leave the cache root.
        if os.path.commonpath([self.root, os.path.abspath(path)]) != self.root:
            raise errors.InvalidRequest("a processing cache path must stay under its root")
        return path

    def get(self, org_id: str, digest: str, profile: str,
            mime: str | None = None) -> CacheEntry | None:
        """The live entry, or None. An expired one is deleted rather than returned. With
        `mime`, an entry another process wrote is found on disk and indexed (MPILOT)."""
        key = (org_id, digest, profile)
        entry = self.entries.get(key) or (self._load(key, mime) if mime else None)
        if entry is None:
            return None
        if self.clock() - entry.stored_at >= self.ttl_s:
            self._remove(key, entry)
            return None
        if not os.path.exists(entry.local_path):
            # The object vanished under us (an operator, a tmpfs reboot, another sweep).
            # A stale index entry pointing at nothing is worse than a miss.
            self.entries.pop(key, None)
            return None
        return entry

    def put(self, org_id: str, digest: str, profile: str, data: bytes,
            probed: probing.Probed) -> CacheEntry:
        """Write the prepared bytes and index them. Same content twice is one file."""
        path = self.path_for(org_id, profile, digest, probed.mime)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Written beside the target and renamed: a worker never opens a half-written clip,
        # and a crash leaves a temporary file rather than a plausible-looking short one.
        temporary = f"{path}.{os.getpid()}.part"
        with open(temporary, "wb") as handle:
            handle.write(data)
        stored_at = self.clock()
        os.utime(temporary, (stored_at, stored_at))       # the life `_load` reads back
        os.replace(temporary, path)
        entry = CacheEntry(local_path=path, probed=probed, bytes=len(data),
                           stored_at=stored_at)
        self.entries[(org_id, digest, profile)] = entry
        return entry

    def _load(self, key: tuple[str, str, str], mime: str) -> CacheEntry | None:
        """MPILOT: an entry another process `put` - the path the key builds, verified by
        content hash, with the file's own life. None if absent, not those bytes, a symlink
        (the file itself - a directory above it is `path_for`'s), or dated in the future: a
        touched, restored or skewed mtime would otherwise extend the 7-day retention.

        ponytail: the hash is the source digest because profile `v1`'s prepared artifact is
        the source bytes (`_prepared_bytes`); a transcoding profile needs the prepared
        digest beside the file. One read and one hash per process per entry, then indexed.
        """
        if not self.enabled:
            return None
        org_id, digest, profile = key
        path = self.path_for(org_id, profile, digest, mime)
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
                data = handle.read()
                stored_at = os.fstat(handle.fileno()).st_mtime
        except OSError:
            return None
        if digest_of(data) != digest or stored_at > self.clock() + FUTURE_MTIME_SLACK_S:
            return None
        entry = CacheEntry(local_path=path, probed=None, bytes=len(data), stored_at=stored_at)
        self.entries[key] = entry
        return entry

    def sweep(self) -> int:
        """Delete every entry past its life. Returns how many were removed."""
        now = self.clock()
        expired = [(key, entry) for key, entry in self.entries.items()
                   if now - entry.stored_at >= self.ttl_s]
        for key, entry in expired:
            self._remove(key, entry)
        return len(expired)

    def evict(self, key: tuple[str, str, str]) -> None:
        """Remove one entry and its file now (M3's capacity bound)."""
        self._remove(key, self.entries[key])

    def _remove(self, key: tuple[str, str, str], entry: CacheEntry) -> None:
        self.entries.pop(key, None)
        try:
            os.remove(entry.local_path)
        except OSError:
            pass
        try:            # the digest directory, when nothing else lives in it
            os.rmdir(os.path.dirname(entry.local_path))
        except OSError:
            pass


class MediaPreparation(MediaStaging):
    """`ports.MediaStore` with preparation: M1's staging plus the probe, the rewrite and
    the processing cache.

    A subclass rather than a second adapter, because preparation is not a different store:
    it writes into the same tenant-scoped key space, under the same write-once rule, and
    `prepare` has to read what `stage` wrote.
    """

    def __init__(self, objects, *, cache: ProcessingCache | None = None,
                 profile: MediaProfile | None = None, probe=probing.probe,
                 **staging) -> None:
        super().__init__(objects, **staging)
        # The profile decides the version every key carries, so a `profile_version` passed
        # to the staging half and a profile passed here cannot drift apart.
        self.profile = profile or MediaProfile.pinned(self.limits, version=self.profile_version)
        self.cache = cache or ProcessingCache("")
        self.probe = probe
        # r1 R1: the host's preparation pool size. Two materializations of 64 MiB is the
        # memory this bounds; the per-request budget below bounds one request's share.
        self.gate = asyncio.Semaphore(max(1, self.limits.preparation_concurrency))
        self.profile_version = self.profile.version

    # --- the probe ------------------------------------------------------------
    async def probed(self, data: bytes) -> probing.Probed:
        """The clip's own facts, under `PROBE_TIMEOUT_S`.

        A synchronous parser runs in a thread, so a 64 MiB container does not hold the
        event loop while it is walked; an injected asynchronous one is awaited directly,
        which is how the "probe never returns" drill gets a typed refusal instead of a
        leaked thread.
        """
        if inspect.iscoroutinefunction(self.probe):
            work = self.probe(data)
        else:
            work = asyncio.to_thread(self.probe, data)
        try:
            return await asyncio.wait_for(work, self.limits.probe_timeout_s)
        except (TimeoutError, asyncio.TimeoutError):
            # Platform-side (R21): a probe that ran out of time did not tell us the media
            # is bad, so it is never charged and never reported as the customer's mistake.
            raise errors.DeadlineExceeded("preparation could not read the media in time") \
                from None

    async def facts(self, data: bytes, mime: str) -> tuple[str, float | None]:
        """M1's hook: what `materialize` records about the bytes it is about to store.

        The declared `mime` is **dropped** - the sniffed container wins - and the duration
        is the probe's. Both are established before the object is written, so media the
        profile refuses is never stored at all.
        """
        probed = await self.probed(data)
        self.profile.check(probed, len(data))
        return probed.mime, probed.duration_s

    def refuse_early(self, head) -> bool:
        """M4: the fetcher's look at a download still in progress (`MediaFetcher.fetch`).

        A header-first clip the profile refuses - most often one over the duration cap - is
        refused from its `moov`, and the rest of the body is never read. Measured before
        this, a 56 MB clip of 150 s was downloaded to its last byte and only then refused.
        Anything the header does not settle is left to `facts`, which still probes the
        whole object: this only ever refuses sooner, never accepts. True once a complete
        `moov` passed: the walk stops at the first one, so no later look can change it.
        An injected probe is the only one that decides (M2's timeout drills), so the
        built-in header walk - on the loop, one bounded walk per look - stays out of it.
        """
        if self.probe is not probing.probe:
            return False
        probed = probing.probe_header(head)
        if probed is None:
            return False
        try:
            self.profile.check(probed, 0)           # bytes are the fetcher's own cap
        except errors.DomainError as refusal:
            refusal.reason = "header"               # for the operator's log line
            raise
        return True                                 # settled

    # --- admission-time preparation -------------------------------------------
    async def prepare_request(self, org_id: str, request: NormalizedRequest) -> NormalizedRequest:
        """The validated request with its media materialized, measured and referenced.

        Returns a `NormalizedRequest` whose `media` holds one ref per video part in order,
        each carrying the measured duration, and whose `messages` name those refs instead of
        the customer's URLs. `stage` then makes exactly that record durable, and
        `worker.engine.prepared_request` pairs its parts with its refs one to one.

        All or nothing: a source that cannot be fetched, read or served refuses the whole
        request, so no half-prepared record reaches admission (02: "a staging failure
        creates no job or hold").

        `payload_digest` is deliberately **not** recomputed. It identifies the customer's
        body for idempotency (`IdempotencyRef.payload_hash`, and `validate.payload_digest`
        already tokenises inline media), so a rewrite that changed it would make every
        retried request a `409` against its own first attempt. What `stage` makes durable is
        this record; what the digest names is the request that asked for it.
        """
        org_id = valid_org(org_id)
        if request.org_id != org_id:
            raise errors.Forbidden("a request may only be prepared for its own org")
        sources = _video_sources(request.messages)
        if len(sources) > self.profile.max_parts:
            raise errors.UnsupportedMedia(f"at most {self.profile.max_parts} video per request",
                                          param="messages")
        refs = await self._materialize_all(org_id, sources)
        return request.model_copy(update={"messages": _rewrite(request.messages, refs),
                                          "media": refs})

    async def _materialize_all(self, org_id: str, sources: list[str]) -> tuple[MediaRef, ...]:
        """Every source, in order, under the pool gate and one shared byte budget.

        `return_exceptions=True` and one raise at the end rather than a bare `gather`: a
        failure that propagated while its siblings were still running would leave the
        request refused *and* fetches in flight, which is the shape a client turns into an
        amplifier by retrying.
        """
        budget = _Budget(self.profile.max_bytes)
        results = await asyncio.gather(
            *(self._materialize_one(org_id, source, budget) for source in sources),
            return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return tuple(results)

    async def _materialize_one(self, org_id: str, source: str, budget: _Budget) -> MediaRef:
        async with self.gate:
            ref = await self.materialize(org_id, source)
        budget.spend(ref.bytes)
        return ref

    # --- the port --------------------------------------------------------------
    async def prepare(self, job_id: str, profile: str) -> tuple[MediaRef, ...]:
        """r1 R46: produce the immutable prepared refs for this job's attached media.

        Idempotent by construction, because R46 allows bounded preparation retries: every
        step is content-addressed and write-once, and the attached source refs are not
        replaced by the prepared ones, so a second attempt re-derives the same answer
        instead of preparing the previous attempt's output.

        Nothing here trusts the attached record. The object is looked up under a key this
        store rebuilds, its digest must still match, and the duration that reaches the
        engine is re-measured from the bytes that are actually there - which is also what
        makes an object that expired between `attach` and `prepare` a `not_found` rather
        than a worker failing on a file nobody can open.
        """
        version = valid_profile(profile)
        sources = await self.attached(job_id)
        if sources is None:
            raise errors.NotFound(f"no staged media for job {job_id}")
        prepared = []
        for ref in sources:
            key = self._key(ref.org_id, ref.digest, ref.profile_version, "source")
            if await self.objects.head(key) != ref.digest:
                raise errors.NotFound(f"the staged object for media {ref.handle} is gone")
            prepared_key = self._key(ref.org_id, ref.digest, version, "prepared")
            entry = self.cache.get(ref.org_id, ref.digest, version) if self.cache.enabled else None
            # A cache hit is a hit on the *local copy*, and the durable artifact is the
            # record. If it is gone the whole path runs again, so "persisted before the job
            # is told it is prepared" holds on the second attempt as well as the first.
            if entry is None or entry.probed is None \
                    or await self.objects.head(prepared_key) is None:
                data = await self.objects.get(key)
                # M4: the full-body digest runs in a worker thread, off the event loop.
                if data is None or await asyncio.to_thread(digest_of, data) != ref.digest:
                    # Between the HEAD and the read: an object store that answered "yes"
                    # and then handed over other bytes must not become a prepared artifact.
                    raise errors.NotFound(f"the staged object for media {ref.handle} is gone")
                probed = await self.probed(data)
                self.profile.check(probed, len(data))
                body = self._prepared_bytes(data, probed)
                # Durable before local: the prepared artifact exists in the object store
                # before anything downstream can be told the job is prepared.
                await self._write_once(prepared_key, body, probed.mime)
                entry = (self.cache.put(ref.org_id, ref.digest, version, body, probed)
                         if self.cache.enabled
                         else CacheEntry("", probed, len(body), 0.0))
            prepared.append(ref.model_copy(update={
                "profile_version": version, "storage_ref": prepared_key,
                "mime": entry.probed.mime, "bytes": entry.bytes,
                "duration_s": entry.probed.duration_s}))
        self.prepared_by_job[job_id] = tuple(prepared)
        return tuple(prepared)

    @staticmethod
    def _prepared_bytes(data: bytes, probed: probing.Probed) -> bytes:
        """The prepared artifact for profile `v1`: the source bytes.

        Profile `v1` applies its frame and pixel budget in the engine's
        `mm_processor_kwargs` from the measured duration, so there is nothing to re-encode
        and re-encoding anyway would cost quality for nothing. A profile that does
        transcode replaces this one method (and takes a new `version`, which is already in
        every key and every cache entry).
        """
        return data

    def local_uri(self, ref: MediaRef) -> str:
        """The `file://` form a worker hands vLLM for a prepared ref.

        The coordinator's single-host decision (S2M D3): the engine runs with
        `--allowed-local-media-path <cache root>` and opens the file, because a bare object
        key is not something any decoder can read. The path is rebuilt here from the ref's
        tenant and digest - never carried in the record, never taken from one - so until the
        frozen `MediaRef` gains an optional `local_path` (integration request 1) there is
        nothing to forge, and when it does this method is what fills it.
        """
        # With the ref's type, so a process that did not prepare it (the worker) finds the
        # file another one wrote (MPILOT gap 2).
        entry = self.cache.get(ref.org_id, ref.digest, ref.profile_version, ref.mime)
        if entry is None:
            raise errors.NotFound(f"media {ref.handle} is not in the processing cache")
        return "file://" + entry.local_path


class _Budget:
    """One request's share of process memory, spent as each source lands."""

    def __init__(self, allowed: int) -> None:
        self.allowed = allowed
        self.spent = 0

    def spend(self, nbytes: int) -> None:
        self.spent += nbytes
        if self.spent > self.allowed:
            raise errors.RequestTooLarge(
                f"{self.spent} bytes of media exceeds MAX_MEDIA_BYTES {self.allowed}")


def _video_sources(messages) -> list[str]:
    """Every `video_url` source in the request, in message and part order.

    The order is the contract (R58: one media part per ref, in order), so this is the same
    walk the rewrite does and they cannot disagree.
    """
    sources = []
    for _, part in _video_parts(messages):
        reference = part.get(VIDEO_PART)
        source = reference.get("url") if isinstance(reference, dict) else None
        if not isinstance(source, str) or not source:
            raise errors.InvalidRequest("a video part carries exactly {url}", param="messages")
        sources.append(source)
    return sources


def _video_parts(messages):
    """(message index, part) for every video part, in order."""
    for index, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, (list, tuple)):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == VIDEO_PART:
                yield index, part


def _rewrite(messages, refs: tuple[MediaRef, ...]):
    """The canonical message list: the customer's URLs replaced by our handles, in order.

    Everything else is copied through untouched - this is not a second validator, and
    G1's allow-list already decided what a message may contain.
    """
    consumed = 0
    rewritten = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, (list, tuple)):
            rewritten.append(message)
            continue
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == VIDEO_PART:
                parts.append({"type": VIDEO_PART, VIDEO_PART: {REF_KEY: refs[consumed].handle}})
                consumed += 1
            else:
                parts.append(part)
        rewritten.append({**message, "content": parts})
    if consumed != len(refs):
        raise errors.InvalidRequest(f"{consumed} media parts but {len(refs)} references",
                                    param="messages")
    return tuple(rewritten)
