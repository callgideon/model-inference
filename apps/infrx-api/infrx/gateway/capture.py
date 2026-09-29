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
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone

from starlette.responses import Response

from ..contracts.records import ConsentSnapshot, ExecutionMode, TraceEnvelope, TraceMode
from .routes.validate import MEDIA_TOKEN, off_mode_policy

log = logging.getLogger("infrx.gateway.capture")

#: A consent change (a console toggle, a revocation) reaches this process within this.
CONSENT_TTL_S = 60.0
#: Key ids are the tenant's to mint: the cache is bounded, oldest out.
CONSENT_CACHE_MAX = 4096
ORDER = (TraceMode.off, TraceMode.minimal, TraceMode.full)
#: What the caller's credential becomes wherever it appears in a trace.
REDACTED = b"[credential]"


class Wall:
    """The sinks' clock (T1 ruling 7: required)."""

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

#: The key's opt-in and its organization's consent head. The head is the highest version,
#: revoked or not: a revoked head is off, never a fall-back to an older consent.
CONSENT_SQL = """
select k.trace_mode, c.consent_version, c.trace_mode, c.content_retention_days,
       c.evaluation_consent, c.effective_at, c.revoked_at
from public.api_keys k
left join lateral (select h.consent_version, h.trace_mode, h.content_retention_days,
                          h.evaluation_consent, h.effective_at, h.revoked_at
                   from infrx.consent_history h where h.org_id = k.org_id
                   order by h.consent_version desc limit 1) c on true
where k.id = %s and k.org_id = %s"""


class ConsentSource:
    """(a): the trace policy a request is admitted with, from PostgreSQL (see the module)."""

    def __init__(self, connect, *, clock=time.monotonic, ttl_s: float = CONSENT_TTL_S,
                 max_entries: int = CONSENT_CACHE_MAX) -> None:
        self.connect, self.clock, self.ttl_s, self.max_entries = connect, clock, ttl_s, max_entries
        self.cache: OrderedDict = OrderedDict()      # (org, key) -> (expires, row or None)
        from ..traces.ship.pins import pg_rows
        self.rows = pg_rows

    async def read(self, org_id: str, key_id: str):
        rows = await self.rows(self.connect, CONSENT_SQL, (key_id, org_id))
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
def redacted(value):
    """`value` with every inline `data:` URL replaced by its digest token (the ingress's own
    `data-sha256:` form, `validate.payload_digest`)."""
    if isinstance(value, dict):
        return {name: redacted(item) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redacted(item) for item in value]
    if isinstance(value, str) and value.startswith("data:"):
        return MEDIA_TOKEN + hashlib.sha256(value.encode()).hexdigest()
    return value


def scrub(data: bytes, token: bytes) -> bytes:
    """The credential out of one part. ponytail: a token split across two SSE frames is not
    matched; the relay's frames are whole JSON chunks, so an echoed token lies inside one."""
    return data.replace(token, REDACTED) if token else data


def request_line(request, token: bytes = b"") -> bytes:
    """The request half of a record: what the caller asked, redacted, one line of JSON."""
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
            capture.add(request_line(request, self.token))
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
        token = headers.get("authorization", "").removeprefix("Bearer ").strip().encode()
        return Captured(accepted, self.sink, request, token, self.limits)

    async def close(self) -> None:
        """Shutdown: what is held in memory is written, the tail sealed, the lock released."""
        await self.sink.flush()
        await self.sink.close(drop_queued=False)
