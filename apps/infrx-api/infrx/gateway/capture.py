"""WR-C6-CAPTURE: trace capture behind `TRACE_PUMPS` (off by default: nothing here is built).

* **Consent (a).** `ConsentSource.policy(auth, now)`: the key's own opt-in
  (`api_keys.trace_mode`, null = off, D3) under its organization's consent head
  (`infrx.consent_history`, the highest version; revoked or not yet effective = off), the
  lower of the two. Read at request time, cached per process per key for `CONSENT_TTL_S`
  (the key cache's TTL: a console change is live within a minute), bounded, fail closed -
  a read that fails is `off_mode_policy`, never an error into the request.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict

from ..contracts.records import ConsentSnapshot, TraceMode
from .routes.validate import off_mode_policy

log = logging.getLogger("infrx.gateway.capture")

#: A consent change (a console toggle, a revocation) reaches this process within this.
CONSENT_TTL_S = 60.0
#: Key ids are the tenant's to mint: the cache is bounded, oldest out.
CONSENT_CACHE_MAX = 4096
ORDER = (TraceMode.off, TraceMode.minimal, TraceMode.full)

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
