"""WR-C6-CAPTURE: trace capture behind `TRACE_PUMPS` (off by default: nothing here is built).

* **Consent (a).** `ConsentSource.policy(auth, now)`: the key's own opt-in
  (`api_keys.trace_mode`, null = off, D3) under its organization's consent head
  (`infrx.consent_history`, the highest version; revoked or not yet effective = off), the
  lower of the two. Read at request time, cached per process per key for `CONSENT_TTL_S`
  (the key cache's TTL: a console change is live within a minute), bounded, fail closed -
  a read that fails is `off_mode_policy`, never an error into the request.
* **The request-path hook (b).** `GatewayCapture.response(...)` wraps a sync or SSE answer
  (`Captured`): one record per request in the gateway's spool - the request, redacted
  (`redacted`: inline media by digest; the caller's bearer token never, in either half),
  then a newline, then the body exactly as it was sent - finished on every way the answer
  ends. `off` wraps nothing; `minimal` is metadata only (the sink's no-op capture); an async
  request is answered 202 here and its output is the worker's (c). A trace failure is
  counted by the sink and never becomes the request's.
* **Async output and shipping (c, proposed ruling).** The worker writes an async job's record
  (the request line, then its output) into a spool of its own, `TRACE_SPOOL_DIR/jobs/<job
  id>-<n>` - hidden (`.`-prefixed) while it writes, renamed once sealed and unlocked: one
  writer per directory, the sink's lock. Only the gateway ships: every `SHIP_S` its lifespan
  seals and ships its own spool, then each finished job spool it can lock, removed once
  every segment is acked. One gateway process per `TRACE_SPOOL_DIR` (its sink's lock refuses
  a second). The worker's `trace_pumps` keeps T3's retention and T2F's projection only.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import Response

from ..contracts import errors, wire
from ..contracts.records import ConsentSnapshot, ExecutionMode, TraceEnvelope, TraceMode
from .routes.validate import MEDIA_TOKEN, off_mode_policy

log = logging.getLogger("infrx.gateway.capture")

#: A consent change (a console toggle, a revocation) reaches this process within this.
CONSENT_TTL_S = 60.0
#: Key ids are the tenant's to mint: the cache is bounded, oldest out.
CONSENT_CACHE_MAX = 4096
ORDER = (TraceMode.off, TraceMode.minimal, TraceMode.full)
#: The gateway's ship cadence (the worker's former `TRACE_SHIP_S`): seals a quiet host's tail.
SHIP_S = 10.0
#: Under `TRACE_SPOOL_DIR`: the worker's per-job spools (c).
JOBS_DIR = "jobs"
#: Async attempts the worker remembers between `load_work` and `complete`.
REMEMBER = 1024
#: What the caller's credential becomes wherever it appears in a trace.
REDACTED = b"[credential]"
#: Every credential this platform mints (`operations.service.new_secret`, the console's
#: lib/keys.ts: `sk-infrx-` + 40 base62). The worker never holds the caller's token, so a
#: job record is scrubbed of the whole family (lens R8), wherever it appears.
KEY_SHAPE = re.compile(rb"sk-infrx-[A-Za-z0-9_-]+")


class Wall:
    """The sinks' clock (T1 ruling 7: required)."""

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

#: The key's opt-in and its organization's consent head (the highest version, revoked or
#: not: a revoked head is off, never a fall-back to an older consent), for a key of that org,
#: through 0057's RPC: the dedicated runtime login (0021) reads neither table, and where the
#: RPC is absent (hosted before its window) the read fails and capture is off.
CONSENT_SQL = "select * from infrx.trace_consent(%s, %s)"


class ConsentSource:
    """(a): the trace policy a request is admitted with, from PostgreSQL (see the module)."""

    def __init__(self, connect, *, clock=time.monotonic, ttl_s: float = CONSENT_TTL_S,
                 max_entries: int = CONSENT_CACHE_MAX) -> None:
        self.connect, self.clock, self.ttl_s, self.max_entries = connect, clock, ttl_s, max_entries
        self.cache: OrderedDict = OrderedDict()      # (org, key) -> (expires, row or None)
        from ..traces.ship.pins import pg_rows
        self.rows = pg_rows

    async def read(self, org_id: str, key_id: str):
        rows = await self.rows(self.connect, CONSENT_SQL, (org_id, key_id))
        return rows[0] if rows else None

    async def policy(self, auth, now) -> ConsentSnapshot:
        cached = (auth.org_id, auth.key_id)
        hit = self.cache.get(cached)
        if hit is None or hit[0] <= self.clock():
            try:
                answer = await self.read(*cached)
            except Exception:                    # noqa: BLE001 - fail closed, never raise
                log.warning("consent read failed; capture is off for this key", exc_info=True)
                answer = None
            hit = (self.clock() + self.ttl_s, answer)
            self.cache[cached] = hit
            self.cache.move_to_end(cached)
            while len(self.cache) > self.max_entries:
                self.cache.popitem(last=False)
        return effective(hit[1], auth.org_id, now)


def effective(row, org_id: str, now) -> ConsentSnapshot:
    """The lower of the key's opt-in and the org's consent head, in force at `now`; else off."""
    if row is None or row[1] is None:
        return off_mode_policy(org_id, now)
    key_mode, version, org_mode, days, evaluation, effective_at, revoked_at = row
    mode = min(TraceMode(key_mode or TraceMode.off), TraceMode(org_mode), key=ORDER.index)
    snapshot = ConsentSnapshot(org_id=org_id, consent_version=version, trace_mode=mode,
                               content_retention_days=days,
                               evaluation_consent=bool(evaluation) and mode is TraceMode.full,
                               effective_at=effective_at, revoked_at=revoked_at)
    if mode is TraceMode.off or not snapshot.is_current(now):
        return off_mode_policy(org_id, now)
    return snapshot


# --- (b) the request-path hook ---------------------------------------------------------
def redacted(value, name=None):
    """`value` with every inline `data:` URL replaced by its digest token (the ingress's own
    `data-sha256:` form, `validate.payload_digest`), and every media part's remote `url`
    cut to scheme://host/path: a signed query string or `user:pass@` is a credential
    (media/fetch.py), never in a durable record (S2M D1). Text is left as the caller wrote it."""
    if isinstance(value, dict):
        return {key: redacted(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redacted(item) for item in value]
    if isinstance(value, str) and value.startswith("data:"):
        return MEDIA_TOKEN + hashlib.sha256(value.encode()).hexdigest()
    if isinstance(value, str) and name == "url":
        parts = urlsplit(value)
        return f"{parts.scheme}://{parts.hostname or ''}{parts.path}"
    return value


def scrub(data: bytes, token: bytes) -> bytes:
    """The credential out of one part. ponytail: a token split across two SSE frames is not
    matched; the relay's frames are whole JSON chunks, so an echoed token lies inside one."""
    return data.replace(token, REDACTED) if token else data


def scrub_keys(data: bytes) -> bytes:
    """Every minted credential out of one part of a job record (R8): the worker's scrub."""
    return KEY_SHAPE.sub(REDACTED, data)


def request_line(request, token: bytes = b"") -> bytes:
    """The request half of a record: what the caller asked, redacted, one line of JSON.
    Callers on an event loop run it in a thread (lens R9: inline media up to 96 MiB is
    hashed here). ponytail: every request line takes the thread hop, not only one over
    1 MiB; a size check first when a measured hop cost matters."""
    document = {"request_id": request.request_id, "model": request.model_revision,
                "messages": redacted(list(request.messages)),
                "parameters": redacted(dict(request.parameters))}
    return scrub(json.dumps(document, sort_keys=True, separators=(",", ":"),
                            default=str).encode(), token) + b"\n"


def envelope(request, capture, limits, finished: bool) -> TraceEnvelope:
    """The record for `request` as `capture` holds it (its mode, its charged bytes)."""
    held = capture.content_bytes
    return TraceEnvelope(
        request_id=request.request_id, org_id=request.org_id, key_id=request.key_id,
        mode=capture.mode, started_at=request.created_at, completed_at=Wall.now(),
        content_complete=bool(held) and finished,
        content_ref=f"spool:{request.request_id}" if held else None, content_bytes=held,
        model_revision=request.model_revision,
        price_version=limits.active_rate_card_version or "unpriced")


class Captured(Response):
    """An accepted answer with its record: every message it sends is teed into the capture,
    and the capture is finished however the answer ends (sent, failed, the client gone)."""

    def __init__(self, inner, sink, request, token: bytes, limits) -> None:
        self.inner, self.sink, self.request, self.token, self.limits = \
            inner, sink, request, token, limits
        self.status_code, self.background = inner.status_code, None
        self.raw_headers = inner.raw_headers

    async def __call__(self, scope, receive, send) -> None:
        request, finished = self.request, False
        try:
            capture = self.sink.open(request.request_id, request.org_id,
                                     request.trace_policy.trace_mode, request.deadline_at)
            capture.add(await asyncio.to_thread(request_line, request, self.token))
        except Exception:                        # noqa: BLE001 - never the request's error
            log.warning("trace capture of %s failed", request.request_id, exc_info=True)
            return await self.inner(scope, receive, send)

        async def tee(message) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                capture.add(scrub(message["body"], self.token))
            await send(message)
        try:
            await self.inner(scope, receive, tee)
            finished = True
        finally:
            try:
                await capture.finish(envelope(request, capture, self.limits, finished))
            except Exception:                    # noqa: BLE001 - never the request's error
                log.warning("trace capture of %s failed", request.request_id, exc_info=True)


class GatewayCapture:
    """What `TRACE_PUMPS` composes into the gateway: the consent source, the gateway's spool
    (one gateway process per `TRACE_SPOOL_DIR`: the sink's directory lock), and the shipper
    its lifespan runs (c)."""

    def __init__(self, consent, sink, shipper, root, limits=None) -> None:
        from ..contracts.limits import DEFAULTS
        self.consent, self.sink, self.shipper, self.root = consent, sink, shipper, root
        self.limits = limits or DEFAULTS

    async def policy(self, auth, now) -> ConsentSnapshot:
        return await self.consent.policy(auth, now)

    def response(self, accepted, request, headers):
        """(b): the accepted answer, capturing when the request's policy says so. `headers`
        are the caller's: only the bearer token is read from them, to keep it out."""
        if request.trace_policy.trace_mode is TraceMode.off \
                or request.execution_mode is ExecutionMode.async_:
            return accepted
        if accepted.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED):
            return accepted                      # the original request's record stands
        token = headers.get("authorization", "").removeprefix("Bearer ").strip().encode()
        return Captured(accepted, self.sink, request, token, self.limits)

    async def ship_once(self) -> list:
        """(c): seal and ship the gateway's own spool, then every finished job spool."""
        await self.sink.flush()
        await self.sink.rotate()
        reports = [await self.shipper.ship()]
        return reports + await ship_jobs(self.shipper, self.root / JOBS_DIR, self.limits)

    async def pump(self, stop: asyncio.Event, every_s: float = SHIP_S) -> None:
        """The lifespan's ship loop until `stop`; a failed pass is logged and retried."""
        while not stop.is_set():
            try:
                await self.ship_once()
            except Exception:                    # noqa: BLE001 - the next pass retries
                log.exception("trace ship failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), every_s)

    async def close(self) -> None:
        """Shutdown: what is held in memory is written, the tail sealed, the lock released."""
        await self.sink.flush()
        await self.sink.close(drop_queued=False)


async def ship_jobs(shipper, root: Path, limits) -> list:
    """Each finished job spool under `root` that this process can lock, shipped with the
    gateway's projection, objects, pins and retention; removed once nothing is left. A
    directory still held by its writer, or still hidden, is the next pass's."""
    from ..traces.ship import Shipper
    from ..traces.spool import SpoolTraceSink
    names = await asyncio.to_thread(
        lambda: sorted(p.name for p in root.iterdir() if p.is_dir()
                       and not p.name.startswith(".")) if root.is_dir() else [])
    reports = []
    for name in names:
        try:
            sink = await asyncio.to_thread(SpoolTraceSink, Wall, limits=limits,
                                           spool_dir=root / name)
        except RuntimeError:                     # its writer still holds it
            continue
        except Exception:                        # noqa: BLE001 - one bad spool, not all
            log.warning("job spool %s could not be opened", name, exc_info=True)
            continue
        try:
            reports.append(await Shipper(sink, shipper.projection, shipper.objects,
                                         pins=shipper.pins, retention=shipper.retention).ship())
            await asyncio.to_thread(_remove, root / name)
        finally:
            await sink.close()
    return reports


def _remove(path: Path) -> None:
    """A shipped job spool, while its lock is still held (nothing else can be writing). A
    segment that did not ship keeps the directory: `rmdir` refuses a non-empty one."""
    with contextlib.suppress(OSError):
        (path / ".writer.lock").unlink()
        path.rmdir()


# --- (c) the worker's half: async job output ---------------------------------------------
def capture_jobs(runner, root, clock, *, limits=None, remember: int = REMEMBER) -> None:
    """Compose (c) into W's `AttemptRunner`: its `jobs` and `put_result` go through a
    `JobCapture` over the spools under `root` (`TRACE_SPOOL_DIR`)."""
    captured = JobCapture(runner.jobs, runner.put_result, root, clock, limits, remember)
    runner.jobs, runner.put_result = captured, captured.put_result


class JobCapture:
    """The runner's job store, remembering each consented async attempt from `load_work` to
    `complete` and spooling its record after a completion the store accepted. Everything
    else is the store's; a trace failure is logged, never the job's."""

    def __init__(self, jobs, put_result, root, clock, limits=None,
                 remember: int = REMEMBER) -> None:
        from ..contracts.limits import DEFAULTS
        self.jobs, self._put_result, self.clock = jobs, put_result, clock
        self.root, self.limits, self.remember = Path(root) / JOBS_DIR, limits or DEFAULTS, remember
        self.open: OrderedDict = OrderedDict()           # job id -> [request, output]

    def __getattr__(self, name):
        return getattr(self.jobs, name)

    async def load_work(self, lease):
        work = await self.jobs.load_work(lease)
        request = work.request
        if request.execution_mode is ExecutionMode.async_ \
                and request.trace_policy.trace_mode is not TraceMode.off:
            self.open[lease.job_id] = [request, None]
            self.open.move_to_end(lease.job_id)
            while len(self.open) > self.remember:
                self.open.popitem(last=False)
        return work

    async def put_result(self, job_id, text, lease=None):
        ref = await self._put_result(job_id, text, lease)
        held = self.open.get(job_id)
        if held is not None:
            held[1] = text
        return ref

    async def complete(self, lease, outcome):
        """Forgets the attempt on a settlement or a refusal only: a lost ack (any other
        failure) keeps it for the runner's identical retry, which may commit (lens R7)."""
        try:
            settled = await self.jobs.complete(lease, outcome)
        except errors.DomainError:
            self.open.pop(lease.job_id, None)
            raise
        held = self.open.pop(lease.job_id, None)
        if held is not None:
            try:
                await self.spool(*held)
            except Exception:                    # noqa: BLE001 - never the job's error
                log.warning("trace capture of job %s failed", lease.job_id, exc_info=True)
        return settled

    async def spool(self, request, text) -> None:
        """One record in a spool of its own, sealed, then renamed visible for the gateway.
        ponytail: a sink (and a writer thread) per job; one per worker with a handoff
        directory when a measured job rate makes that matter."""
        from ..traces.spool import SpoolTraceSink
        hidden = self.root / f".{request.request_id}-{uuid.uuid4().hex[:8]}"
        sink = await asyncio.to_thread(SpoolTraceSink, self.clock, limits=self.limits,
                                       spool_dir=hidden)
        try:
            capture = sink.open(request.request_id, request.org_id,
                                request.trace_policy.trace_mode, request.deadline_at)
            capture.add(scrub_keys(await asyncio.to_thread(request_line, request)))
            if text:
                capture.add(scrub_keys(text.encode()))
            await capture.finish(envelope(request, capture, self.limits, text is not None))
            await sink.flush()
        finally:
            await sink.close(drop_queued=False)
        await asyncio.to_thread(os.rename, hidden, hidden.with_name(hidden.name[1:]))


# --- the composition (`TRACE_PUMPS`) ------------------------------------------------------
def adapters(settings, connect) -> dict:
    """`pilot.adapters_from_env`'s hook: nothing unless the deployment enables TRACE_PUMPS."""
    return {"capture": build(settings, connect)} if settings.deployment.trace_pumps else {}


def build(settings, connect) -> GatewayCapture:
    """The gateway's capture: the consent source on the job store's pool, the spool on
    `TRACE_SPOOL_DIR` and T2I's shipper (C2's holds, as the worker's retention). Refuses to
    start, naming the setting, without the three settings, when another process holds the
    spool, or when ClickHouse does not answer."""
    from ..config import RuntimeMisconfigured, runtime_mode
    from ..traces import ship
    from ..traces.spool import SpoolTraceSink
    from ..worker.__main__ import content_holds
    mode, limits = runtime_mode(settings), settings.pilot
    missing = [name for name, value in (("TRACE_SPOOL_DIR", limits.trace_spool_dir),
                                        ("CLICKHOUSE_URL", limits.clickhouse_url),
                                        ("S3_TRACE_BUCKET", limits.s3_trace_bucket))
               if not value.strip()]
    if missing:
        raise RuntimeMisconfigured(mode, missing)
    holds = content_holds(mode, connect)
    try:
        shipper = ship.build_shipper(limits, None, holds=holds,
                                     endpoint_url=settings.deployment.s3_endpoint_url)
    except Exception as failure:          # noqa: BLE001 - every failure refuses startup
        raise RuntimeMisconfigured(mode, detail="TRACE_PUMPS: CLICKHOUSE_URL did not answer "
                                   f"({type(failure).__name__})") from None
    try:
        shipper.spool = SpoolTraceSink(Wall, limits=limits)
    except RuntimeError:
        raise RuntimeMisconfigured(mode, ("TRACE_SPOOL_DIR",), detail="another process "
                                   "spools there: one gateway process per TRACE_SPOOL_DIR") \
            from None
    return GatewayCapture(ConsentSource(connect), shipper.spool, shipper,
                          Path(limits.trace_spool_dir), limits)
