"""MPILOT gap 2: the durable attach - which staged refs an admitted job runs on - so a
process other than the one that attached (the pilot's worker, a restarted gateway) reads
them from PostgreSQL instead of `MediaStaging.by_job`.

No migration: D2's 0003 relations are this record. `infrx.staged_media` holds each ref as
the store described it (tenant, handle, kind, digest, size, type, object key, profile,
duration) and `infrx.job_media` binds it to the job, `role = 'source'`, in order. Their
composite foreign keys are R52/R55 in the database: `(job_id, org_id) -> infrx.jobs` binds
a ref only to a job of its own organization, `(org_id, handle) -> staged_media` only a ref
that was recorded. Both are `service_role` tables (0004: RLS on, no browser policy); the
pool's connections `set role service_role`.

Write-once, like the store it records (MPILOT review PAR-1): attaches of one job are
serialized on its `infrx.jobs` row (`for update`); a job already bound answers the exact same
refs - handles and digests, in order - as a no-op and anything else (other refs, a superset, a
subset, a reorder) as `conflict`, with nothing written. A handle already recorded with other
content is a conflict too. `staged_media` keeps the FIRST description of a handle: the same
bytes staged later as another kind (`url` after `inline`) read back as the first kind.

What 0003 cannot say is "attached with no media": a text job has no row, so `get` answers
None for it, exactly as for a job never attached (`MediaStaging.attached`), and a later put
of media for it is not refused here (the in-process check in `MediaStaging.attach` is).
"""
from __future__ import annotations

from ..contracts import errors, ids
from ..contracts.records import MediaRef

_COLUMNS = ("org_id", "handle", "kind", "digest", "bytes", "mime", "storage_ref",
            "profile_version", "duration_s")
_STAGE = ("insert into infrx.staged_media (org_id, handle, kind, state, digest, bytes, mime, "
          "storage_ref, profile_version, duration_s, finalized_at) values (%s, %s, %s, "
          "'finalized', %s, %s, %s, %s, %s, %s, infrx.now()) "
          "on conflict (org_id, handle) do nothing")
_RECORDED = "select digest from infrx.staged_media where org_id = %s and handle = %s"
# R55 and the serialization point: the job row of the refs' organization, locked.
_LOCK = "select 1 from infrx.jobs where request_id = %s and org_id = %s for update"
_BIND = ("insert into infrx.job_media (job_id, org_id, handle, role, position) "
         "values (%s, %s, %s, 'source', %s)")
_BOUND = (f"select {', '.join('s.' + c for c in _COLUMNS)} from infrx.job_media m "
          "join infrx.staged_media s on s.org_id = m.org_id and s.handle = m.handle "
          "where m.job_id = %s and m.role = 'source' order by m.position")


class PgAttachments:
    """`put`/`get` over one `Connect` (`state.jobstore.Connect`: autocommit, service_role)."""

    def __init__(self, connect) -> None:
        self._connect = connect

    async def put(self, job_id: str, refs: tuple[MediaRef, ...]) -> None:
        """Record `refs` as the job's sources once, all or nothing (one transaction)."""
        if not refs:
            return
        conn = await self._connect()
        try:
            await conn.execute("begin")
            if await (await conn.execute(_LOCK, (job_id, refs[0].org_id))).fetchone() is None:
                # R55: the job is unknown, or of another organization than the refs.
                raise errors.NotFound(f"no job {job_id} of the media's organization")
            bound = [(row[1], row[3]) for row in
                     await (await conn.execute(_BOUND, (job_id,))).fetchall()]
            given = [(ref.handle, ref.digest) for ref in refs]
            if bound:
                if bound != given:
                    raise errors.Conflict(f"job {job_id} is already attached to other media")
            else:
                for position, ref in enumerate(refs):
                    row = ref.model_dump(mode="json")
                    await conn.execute(_STAGE, tuple(row[c] for c in _COLUMNS))
                    (recorded,) = await (await conn.execute(
                        _RECORDED, (ref.org_id, ref.handle))).fetchone()
                    if recorded != ref.digest:
                        raise errors.Conflict(f"handle {ref.handle} already names other content")
                    await conn.execute(_BIND, (job_id, ref.org_id, ref.handle, position))
            await conn.execute("commit")
        except BaseException:
            await conn.execute("rollback")
            raise
        finally:
            await conn.close()

    async def get(self, job_id: str) -> tuple[MediaRef, ...] | None:
        """The job's sources in order, or None when it has none recorded."""
        if not ids.is_request_id(job_id):
            return None
        conn = await self._connect()
        try:
            rows = await (await conn.execute(_BOUND, (job_id,))).fetchall()
        finally:
            await conn.close()
        return tuple(MediaRef.model_validate(dict(zip(_COLUMNS, (str(row[0]), *row[1:]))))
                     for row in rows) or None
