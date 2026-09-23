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

Write-once, like the store it records: a handle already recorded with other content, or a
job already bound to other refs, is a conflict and nothing is written.

What 0003 cannot say is "attached with no media": a text job has no row, so `get` answers
None for it, exactly as for a job never attached (`MediaStaging.attached`).
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
_BIND = ("insert into infrx.job_media (job_id, org_id, handle, role, position) "
         "values (%s, %s, %s, 'source', %s) on conflict do nothing")
_BOUND = (f"select {', '.join('s.' + c for c in _COLUMNS)} from infrx.job_media m "
          "join infrx.staged_media s on s.org_id = m.org_id and s.handle = m.handle "
          "where m.job_id = %s and m.role = 'source' order by m.position")


class PgAttachments:
    """`put`/`get` over one `Connect` (`state.jobstore.Connect`: autocommit, service_role)."""

    def __init__(self, connect) -> None:
        self._connect = connect

    async def put(self, job_id: str, refs: tuple[MediaRef, ...]) -> None:
        """Record `refs` as the job's sources, all or nothing (one transaction)."""
        if not refs:
            return
        from psycopg import errors as pg
        conn = await self._connect()
        try:
            await conn.execute("begin")
            for position, ref in enumerate(refs):
                row = ref.model_dump(mode="json")
                await conn.execute(_STAGE, tuple(row[c] for c in _COLUMNS))
                (recorded,) = await (await conn.execute(
                    _RECORDED, (ref.org_id, ref.handle))).fetchone()
                if recorded != ref.digest:
                    raise errors.Conflict(f"handle {ref.handle} already names other content")
                await conn.execute(_BIND, (job_id, ref.org_id, ref.handle, position))
            bound = await (await conn.execute(_BOUND, (job_id,))).fetchall()
            if [row[1] for row in bound] != [ref.handle for ref in refs]:
                raise errors.Conflict(f"job {job_id} is already attached to other media")
            await conn.execute("commit")
        except pg.ForeignKeyViolation:
            await conn.execute("rollback")
            # R55: the job is unknown, or of another organization than the refs.
            raise errors.NotFound(f"no job {job_id} of the media's organization") from None
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
