"""AP-07 (for AP-08's start job): the traces of one grantor's model a judge run may sample.

Published protocol (`infrx.judge.start.Eligible`):

    eligible = TraceEligible(connect, retention)
    await eligible(grantor_org_id: str, model_id: str, limit: int) -> list[tuple[str, bool]]

`(request_id, has_video)` pairs, unique, newest first, at most `limit` (1..200, the judge's
scan bound `judge.dryrun.MAX_CANDIDATES`; outside it `InvalidRequest`). `connect` is a
PostgreSQL connector (`state.jobstore.connector`), `retention` T3's `Retention` whose `traces`
is the ClickHouse projection (`ClickHouseProjection`) - the judge role's own `_traces` pair.
A PostgreSQL failure is `DependencyUnavailable`; nothing is cached between calls.

A trace is offered only when, on this call:

1. the grantor's CURRENT grant to the model's provider permits `external_judging` on both
   request and response content for that model (`AccessGrant.permits` on the database clock;
   revoked, expired or narrower: nothing);
2. it is the grantor's own row on one of the model's serving versions (`infrx.serving_versions`);
3. its mode is `full` and its content was stored, within T3's content bound
   (`Retention.content_live`), and neither the request nor its content carries a T3 tombstone.

`has_video`: the request was admitted with a video medium (0003 `job_media` over a finalized
`staged_media` row whose mime is `video/*`).

This is a candidate list, not a permission: J2's `submit` re-checks the grant and reads the
content through T3 again immediately before anything leaves.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from ..contracts.v2.records import DataCategory, DataPurpose
from ..judge.dryrun import scan_bound
from ..state.lab_access import PgAccessStore
from .retention import CONTENT, REQUEST
from .ship.pins import pg_rows
from .ship.shipper import TABLE

CATEGORIES = (DataCategory.request_content, DataCategory.response_content)
SERVING_SQL = ("select provider_org_id::text, serving_version_id::text from "
               "infrx.serving_versions where model_id::text = %s")
#: A request id is its job's primary key (0003), so the id alone names the grantor's job.
VIDEO_SQL = ("select distinct m.job_id::text from infrx.job_media m join infrx.staged_media s "
             "using (org_id, handle) where m.job_id::text = any(%s) "
             "and s.mime like 'video/%%'")
TRACES_SQL = (f"SELECT request_id FROM {TABLE} FINAL WHERE org_id = {{org:UUID}} "
              "AND serving_version_id IN {serving:Array(UUID)} AND mode = 'full' "
              "AND content_stored = 1 AND started_at > toDateTime64({since:String}, 6, 'UTC') "
              "ORDER BY started_at DESC, trace_id DESC LIMIT {limit:UInt32}")


async def _rows(connect, sql: str, params: tuple) -> list[tuple]:
    rows = await pg_rows(connect, sql, params)
    return rows if isinstance(rows, list) else []


class TraceEligible:
    def __init__(self, connect, retention) -> None:
        self.connect, self.retention = connect, retention
        self.store = PgAccessStore(connect)

    async def _serving(self, grantor: str, model: str) -> list[str]:
        """The model's serving versions whose provider holds a current judging grant."""
        by_provider: dict[str, list[str]] = {}
        for provider, version in await _rows(self.connect, SERVING_SQL, (model,)):
            by_provider.setdefault(provider, []).append(version)
        now = await self.store.db_now()
        open_: list[str] = []
        for provider, versions in by_provider.items():
            grant = await self.store.current_grant(grantor, provider)
            if grant is not None and all(grant.permits(
                    now=now, provider_org_id=provider, model_id=model, category=category,
                    purpose=DataPurpose.external_judging) for category in CATEGORIES):
                open_ += versions
        return open_

    async def __call__(self, grantor: str, model: str, limit: int) -> list[tuple[str, bool]]:
        limit = scan_bound(limit)
        serving = await self._serving(grantor, model)
        if not serving:
            return []
        t3 = self.retention
        since = t3.clock() - timedelta(days=t3.content_days)
        result = await asyncio.to_thread(
            t3.traces.client.query, TRACES_SQL, parameters={
                "org": grantor, "serving": serving, "limit": limit,
                "since": since.strftime("%Y-%m-%d %H:%M:%S.%f")})
        ids = list(dict.fromkeys(str(r) for (r,) in result.result_rows))
        # ponytail: tombstones filter after the LIMIT (a deleted request shortens the page);
        # push them into the query when deletions are common enough to starve a sample.
        stones = await t3.store.get({(grantor, r) for r in ids})
        ids = [r for r in ids if not {REQUEST, CONTENT} & set(stones.get((grantor, r), {}))]
        video = {r for (r,) in await _rows(self.connect, VIDEO_SQL, (ids,))} \
            if ids else set()
        return [(r, r in video) for r in ids]
