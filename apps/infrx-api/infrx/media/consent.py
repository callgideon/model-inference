"""M3: the owned resolver trace capture (T) and the judge (J) read media through.

Serving media and trace content have independent consent: a request's media being
stored to run the request says nothing about whether the organization agreed to keep it
in a trace, or to send it to a third-party judge. So reuse is a separate, narrower read
than `resolve_owned`, gated by the organization's **current** `ConsentSnapshot` (R74: no
lookup takes an "as of" time) - the same record T and J already decide by:

* **tenant from the trusted operation, never widened.** `org_id` is the caller's
  server-derived organization; a consent record or a ref of another organization is
  `not_found`, the same answer as a handle that does not exist (TRACE-TENANT).
* **trace** reuse needs `trace_mode == full` (`minimal` keeps no content) and a
  current record; **judge** reuse needs `allows_evaluation` and a capture inside the
  consent window (R56, `judge.sampling`), so consent granted today is not retroactive.
* **logical expiry.** Past the organization's `content_retention_days` from capture the
  content is `result_expired`, whether or not the bytes still exist.
* **the bytes are checked**, not the record: what is returned hashes to the ref's
  digest, so a replaced or collected object is `not_found`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from ..contracts import errors
from ..contracts.records import ConsentSnapshot, MediaRef, TraceMode
from ..judge.sampling import check_consent, within_consent_window
from .fetch import digest_of
from .store import valid_org

Purpose = Literal["trace", "judge"]


async def read_for_reuse(store, org_id: str, handle: str, consent: ConsentSnapshot, *,
                         purpose: Purpose, captured_at: datetime,
                         now: datetime) -> tuple[MediaRef, bytes]:
    """The ref and its verified bytes, or a typed refusal. `store` is the MediaStore
    adapter (`MediaUploads`); `captured_at` is when the trace captured the request."""
    org_id = valid_org(org_id)
    if consent.org_id != org_id:
        raise errors.NotFound(f"no consent of org {org_id} was supplied")
    if purpose == "judge":
        check_consent(org_id, consent, now)
        if not within_consent_window(captured_at, consent):
            raise errors.ConsentMissing("captured outside the evaluation consent window")
    elif purpose == "trace":
        if not consent.is_current(now) or consent.trace_mode is not TraceMode.full:
            raise errors.ConsentMissing(f"org {org_id} has no current full-trace consent")
    else:
        raise errors.InvalidRequest(f"unknown reuse purpose {purpose!r}")
    if now >= captured_at + timedelta(days=consent.content_retention_days):
        raise errors.ResultExpired("the trace content is past its retention")
    ref = await store.resolve_owned(org_id, handle)          # another org's: not_found
    data = await store.objects.get(ref.storage_ref)
    if data is None or digest_of(data) != ref.digest:
        raise errors.NotFound(f"the object for media {handle} is gone")
    return ref, data
