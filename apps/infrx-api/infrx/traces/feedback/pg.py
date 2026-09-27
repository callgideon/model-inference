"""T2F: the relay half on PostgreSQL - D6F's `feedback_projection` outbox events, claimed.

`pending` claims a batch for one relay (0012's dispatch pattern: `for update skip locked`,
redelivered once the claim is older than `redelivery_s` on the database clock) and returns
each event with the feedback row it names **for the event's own organization**, read from
`infrx.feedback` - the durable row, never the event's payload (which a label's event also
carries: a payload is not authority). `acknowledge` lands only for the relay that holds the
claim. `lag` is the projection-lag gauge. Runs as `service_role`, which owns this DML (0004);
no function is added (no migration).
"""
from __future__ import annotations

from ...contracts.records import Feedback
from ..ship.pins import pg_rows
from .projector import Event

PENDING = """
update infrx.outbox o
   set claimed_at = infrx.now(), claimed_by = %(worker)s, attempts = o.attempts + 1
 where o.event_id in (
   select x.event_id from infrx.outbox x
    where x.kind = 'feedback_projection' and x.acknowledged_at is null
      and x.available_at <= infrx.now()
      and (x.claimed_at is null
           or x.claimed_at <= infrx.now() - make_interval(secs => %(redelivery)s))
    order by x.available_at, x.event_id
    limit %(limit)s
    for update skip locked)
returning o.event_id, o.org_id,
  (select infrx.feedback_json(f) from infrx.feedback f
    where f.feedback_id = o.payload->>'feedback_id' and f.org_id = o.org_id)
"""
ACK = ("update infrx.outbox set acknowledged_at = infrx.now() "
       "where event_id = any(%s::uuid[]) and kind = 'feedback_projection' "
       "and acknowledged_at is null and claimed_by = %s")
LAG = ("select count(*), coalesce(extract(epoch from infrx.now() - min(available_at)), 0) "
       "from infrx.outbox where kind = 'feedback_projection' and acknowledged_at is null")


class PgFeedbackOutbox:
    def __init__(self, connect) -> None:
        self.connect = connect

    async def pending(self, *, worker_id: str, limit: int, redelivery_s: float) -> list[Event]:
        rows = await pg_rows(self.connect, PENDING, {"worker": worker_id, "limit": limit,
                                                     "redelivery": redelivery_s})
        return [Event(event_id=str(event_id), org_id=str(org_id),
                      feedback=None if doc is None else Feedback.model_validate(doc))
                for event_id, org_id, doc in rows]

    async def acknowledge(self, event_ids, *, worker_id: str) -> int:
        return await pg_rows(self.connect, ACK, (list(event_ids), worker_id))

    async def lag(self) -> dict[str, float]:
        [(pending, oldest)] = await pg_rows(self.connect, LAG, ())
        return {"pending": pending, "oldest_s": float(oldest)}
