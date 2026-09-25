"""M6's worlds: the content-lifecycle port the collector drives, as the runtime drives it.

    INFRX_D_TASK=m6 uv run --frozen pytest -q tests/m/test_retention.py

* `f2c` - F2C-L's reference adapter (`contracts.fakes.lifecycle.FakeLifecycle`), in memory.
  The mutant runs use it: no container per mutant.
* `d10` - D10's `state.lifecycle.PgLifecycle` on the task-local PostgreSQL
  (`INFRX_D_TASK=m6` -> infrx-m6-postgres :55444, a fresh `infrx_m6*` database per case):
  the SQL that ships, its row locks, triggers and database clock. Only this world means
  integrated.

Both are built by the same factory interface (`lifecycle_factory` /
`pgtesting.make_lifecycle_factory`), so a job is admitted by `admit_ready`, succeeds through
the real lease/settlement path (`conformance.lifecycle._succeeded`) and ends by
cancellation. Phase 1's stand-ins (`DraftLifecycle`, `PgLifecycleStandIn`) are gone: every
case they served runs here.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass, field

import pytest
from infrx.contracts.conformance import builders
from infrx.contracts.conformance import lifecycle as cases
from infrx.contracts.conformance.harness import hook
from infrx.contracts.records import MediaKind, MediaRef
from infrx.contracts.v2.lifecycle import (ContentIdentity, ContentKind, ContentLocation,
                                          ContentObject, ContentOrigin, UploadConstraints,
                                          read_outcome)
from infrx.media import store
from infrx.media.fetch import digest_of

GRACE_S = 600.0
CLAIM_TTL_S = 60.0
RETENTION_S = 3_600.0            # P-25 pending: a fixture value, never a product claim
UPLOAD_TTL_S = GRACE_S * 2       # the ticket window (grace < window, as F2C.a requires)
ORG = cases.ORG
WINDOWS = dict(upload_ttl_s=UPLOAD_TTL_S, grace_s=GRACE_S, claim_ttl_s=CLAIM_TTL_S,
               retention_s=RETENTION_S)


class Objects(store.InMemoryObjectStore):
    """The object store, counting deletes per key (a key deleted twice is visible)."""

    def __init__(self) -> None:
        super().__init__()
        self.deletes: dict[str, int] = {}

    async def delete(self, key: str) -> None:
        self.deletes[key] = self.deletes.get(key, 0) + 1
        await super().delete(key)


@dataclass
class Job:
    request_id: str
    handle: str


@dataclass
class World:
    harness: object
    objects: Objects
    kind: str
    port: object = None
    refs: dict = field(default_factory=dict)          # content_id -> the MediaRef it stages

    def __post_init__(self) -> None:
        self.port = self.port or self.harness.port

    @property
    def clock(self):
        return self.harness.clock

    @property
    def jobs(self):
        return hook(self.harness, "jobs")

    def restart(self) -> World:
        """A new runtime over the same database and bucket: a new adapter, nothing kept."""
        return World(self.harness, self.objects, self.kind, hook(self.harness, "reopen")(),
                     self.refs)

    # --- writers: the row FIRST, then the bytes (F2C; M5/M6 writers do the same) -----------
    async def write(self, kind: str, key: str, data: bytes = b"content", *,
                    job: str | None = None) -> ContentObject:
        row = await self.port.register(ContentIdentity(
            org_id=ORG, kind=ContentKind(kind), location=ContentLocation.object_store,
            object_key=key, digest=digest_of(data), bytes=len(data), job_id=job,
            origin=ContentOrigin.written))
        await self.objects.put_if_absent(key, data, "application/octet-stream")
        if kind == "source":
            self.refs[row.content_id] = MediaRef(
                org_id=ORG, kind=MediaKind.url, digest=digest_of(data), bytes=len(data),
                handle="med_" + hashlib.sha256(key.encode()).hexdigest()[:40],
                mime="video/mp4", duration_s=4.0, storage_ref=key)
        return row

    async def destination(self, data: bytes = b"half a cl") -> ContentObject:
        """An upload's destination under an OPEN ticket (the window protects it)."""
        ticket = await self.port.create(ORG, UploadConstraints.parse(
            {}, max_media_bytes=1 << 26, allowed_mime=cases.MP4))
        await self.port.acknowledge_put(ORG, ticket.upload_handle, bytes=len(data),
                                        digest=digest_of(data))
        key = f"uploads/{ORG}/{ticket.upload_handle}"
        row = await self.port.register(ContentIdentity(
            org_id=ORG, kind=ContentKind.upload_destination,
            location=ContentLocation.object_store, object_key=key, digest=digest_of(data),
            bytes=len(data), upload_handle=ticket.upload_handle,
            origin=ContentOrigin.written))
        await self.objects.put_if_absent(key, data, "application/octet-stream")
        return row

    async def database(self, kind: str, job_id: str) -> ContentObject:
        """A content-bearing row's content row: D10's triggers registered it when the row
        was written (`jobs/<id>` at admission, `job_results/<id>` with the result); the
        fake has no triggers, so it is registered here the way they would."""
        key = ("job_results/" if kind == "result" else "jobs/") + job_id
        if self.kind == "d10":
            return await self._content_where("location = 'database' and object_key = %s", key)
        return await self.port.register(ContentIdentity(
            org_id=ORG, kind=ContentKind(kind), location=ContentLocation.database,
            object_key=key, digest=digest_of(key.encode()), bytes=len(key), job_id=job_id,
            origin=ContentOrigin.written))

    # --- jobs ---------------------------------------------------------------------------
    async def admit(self, *rows: ContentObject, key: str | None = None) -> Job:
        """One admission (`admit_ready`) whose manifest names `rows`' sources."""
        request = cases._request(self.harness, tuple(self.refs[r.content_id] for r in rows))
        admission, _ = await self.port.admit_ready(
            request, cases._idem(request, key or request.request_id), cases.CARD)
        return Job(admission.request_id, admission.job_handle)

    async def finish(self, job: Job) -> None:
        await self.jobs.cancel(ORG, job.handle)

    async def succeed(self, *rows: ContentObject, before_complete=None):
        """A job over `rows`' sources, admitted ready, prepared, claimed and settled with a
        stored result (`conformance.lifecycle._succeeded`, with a manifest)."""
        request = cases._request(self.harness, tuple(self.refs[r.content_id] for r in rows))
        admission, _ = await self.port.admit_ready(
            request, cases._idem(request, request.request_id), cases.CARD)
        job = Job(admission.request_id, admission.job_handle)
        await self.jobs.prepared(await self.port.claim_preparation(request.request_id, "p"))
        lease = await self.jobs.claim(request.request_id, "worker-a")
        if before_complete is not None:
            await before_complete(job)
        outcome, _ = await self.jobs.complete_credit(
            lease, builders.outcome(request.request_id, self.harness))
        return job, outcome

    async def job_live(self, job: Job) -> bool:
        return (await self.jobs.get_owned_credit(ORG, job.handle))[1] is None

    async def result_read(self, job: Job) -> str:
        """The owner's result read class (F2C.b), on the store clock."""
        outcome = (await self.jobs.get_owned_credit(ORG, job.handle))[1]
        return read_outcome(outcome, self.clock.now()).value

    # --- what the store holds -------------------------------------------------------------
    async def row(self, content_id: str) -> ContentObject:
        if self.kind == "d10":
            return await self._content_where("content_id = %s", content_id)
        return self.port.d.content[content_id]

    async def _content_where(self, where: str, value: str) -> ContentObject:
        (doc,) = self.harness.extra["credit_conn"].execute(
            f"select infrx.content_doc(c) from infrx.content_objects c where {where}",
            (value,)).fetchone()
        return ContentObject.model_validate(doc)

    async def present(self, row: ContentObject) -> bool:
        """Readable: a live row whose bytes are still where the writer put them."""
        now = await self.row(row.content_id)
        return now.state == "live" and row.identity.object_key in self.objects.objects

    def sql(self, statement: str, *args):
        """d10 only: one row of the owner's view (a scrubbed body, a job's columns)."""
        return self.harness.extra["credit_conn"].execute(statement, args).fetchone()


def f2c_world(limits=None, **windows) -> World:
    from infrx.contracts.fakes.factories import lifecycle_factory
    return World(lifecycle_factory(limits, **{**WINDOWS, **windows}), Objects(), "f2c")


_PG: dict = {}


def d10_world(limits=None, **windows) -> World:
    """A fresh database with every migration and the seed, or a visible skip."""
    from ..d import pgharness, pgstore
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    if "factory" not in _PG:
        from infrx.state import migrations, pgtesting
        _PG["factory"] = pgtesting.make_lifecycle_factory(
            pgstore.fresh_database, pgharness.dsn, migrations.SEED_MARLIN.read_text())
    return World(_PG["factory"](limits, **{**WINDOWS, **windows}), Objects(), "d10")


WORLDS = {"f2c": f2c_world, "d10": d10_world}
#: `INFRX_M6_WORLDS=f2c` narrows the worlds (the mutant runs; see test_retention_mutants).
SELECTED = tuple(name for name in os.environ.get("INFRX_M6_WORLDS", ",".join(WORLDS)).split(",")
                 if name in WORLDS)


@pytest.fixture(params=SELECTED)
def make_world(request):
    return WORLDS[request.param]


@pytest.fixture(params=[name for name in SELECTED if name == "d10"] or
                [pytest.param("d10", marks=pytest.mark.skip(reason="INFRX_M6_WORLDS omits d10"))])
def make_d10_world(request):
    return WORLDS[request.param]


def run(coroutine):
    return asyncio.run(coroutine)


def key(n: int, part: str = "source", org: str = ORG) -> str:
    return f"media/{org}/v1/{n:016x}/{part}"
