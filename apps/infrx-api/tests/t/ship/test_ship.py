#!/usr/bin/env python3
"""T2I / TRACE-RECOVER + TRACE-TENANT: the shipper from the T1 spool to the projection.

Every scenario drives the **real** spool (T1's `DrillSpool`, with its crash drill) and runs
twice: against an in-memory projection that reads like `ReplacingMergeTree ... FINAL`, and
against the real stack - the ClickHouse image pinned in `tests/integration/compose.yaml`
plus MinIO - on the lane's tasklocal ports (t2i: ClickHouse 57540, S3 57542). The stack half
skips visibly unless `INFRX_T2I_STACK=1`:

    uv run --frozen pytest -q tests/t/ship
    INFRX_T2I_STACK=1 uv run --frozen pytest -q tests/t/ship     # with infrx-t2i-* running

Each stack case gets a database of its own (merges stopped, so a duplicate stays a physical
duplicate and only FINAL can hide it) and an object prefix `test/t2i/<uuid>/`.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from infrx.contracts import errors, tasklocal
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FailurePlan, FakeClock
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import TraceLossReason, TraceMode
from infrx.contracts.v2.records import AdmissionPins
from infrx.media.store import InMemoryObjectStore
from infrx.traces import ship
from infrx.traces.spool import (FRAME, HEADER, SEGMENT_MAGIC, frame_checksum, pack_frame,
                                segment_header)

from ..test_trace_spool import DrillIO, DrillSpool, _dir

ID_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ID_B = "bbbbbbbb-0000-4000-8000-00000000000b"
ID_C = "cccccccc-0000-4000-8000-00000000000c"
PINS = AdmissionPins(model_id="d0000001-0000-4000-8000-000000000001",
                     requested_model="marlin-2b",
                     deployment_revision_id="d0000002-0000-4000-8000-000000000002",
                     serving_version_id="d0000003-0000-4000-8000-000000000003",
                     rate_card_version="rc_test", policy_version="dap_test")
STACK = os.environ.get("INFRX_T2I_STACK") == "1"
# The stack's local literals (the containers are task-local; nothing here is a secret).
CH_USER, CH_PASSWORD = "infrx_t2i", "infrx-t2i-local"
S3_KEY, S3_SECRET, S3_BUCKET = "infrxe2minio", "infrx-e2-local-secret", "infrx-t2i"


def run(coroutine):
    return asyncio.run(coroutine)


# --------------------------------------------------------------------------------------
# the two backends
# --------------------------------------------------------------------------------------
class MemoryProjection:
    """`ReplacingMergeTree(content_stored)` read with FINAL: per `(org_id, trace_id)` the
    row with the highest version, the last inserted among equals."""

    def __init__(self) -> None:
        self.inserted: list[ship.TraceRow] = []

    async def insert(self, rows) -> None:
        self.inserted.extend(rows)

    async def find(self, org_id: str, request_id: str) -> list[ship.TraceRow]:
        final: dict[tuple[str, str], ship.TraceRow] = {}
        for row in self.inserted:
            key = (row.org_id, row.trace_id)
            if key not in final or row.content_stored >= final[key].content_stored:
                final[key] = row
        return sorted((row for row in final.values()
                       if row.org_id == org_id and row.request_id == request_id),
                      key=lambda row: row.trace_id)


class Objects(InMemoryObjectStore):
    """The object store with a switch and a log of reads, over either backend."""

    def __init__(self, inner=None) -> None:
        super().__init__()
        self.inner, self.down, self.reads = inner, False, []

    async def put_if_absent(self, key, data, content_type):
        if self.down:
            raise errors.DependencyUnavailable("the trace object store did not answer")
        if self.inner is not None:
            return await self.inner.put_if_absent(key, data, content_type)
        return await super().put_if_absent(key, data, content_type)

    async def get(self, key):
        self.reads.append(key)
        return await (self.inner.get(key) if self.inner is not None else super().get(key))

    async def delete(self, key):
        return await (self.inner.delete(key) if self.inner is not None else super().delete(key))

    async def keys(self, prefix):
        return await (self.inner.keys(prefix) if self.inner is not None else super().keys(prefix))


class Projection:
    """Either projection, with an outage switch."""

    def __init__(self, inner) -> None:
        self.inner, self.down = inner, False

    async def insert(self, rows) -> None:
        if self.down:
            raise errors.DependencyUnavailable("clickhouse did not answer")
        await self.inner.insert(rows)

    async def find(self, org_id, request_id):
        return await self.inner.find(org_id, request_id)


def _stack():
    import botocore.session
    import clickhouse_connect
    from botocore.config import Config

    from infrx.media.s3 import S3ObjectStore

    services = tasklocal.local_services("t2i")
    ch = dict(host="127.0.0.1", port=services["clickhouse"].host_port, username=CH_USER,
              password=CH_PASSWORD)
    admin = clickhouse_connect.get_client(**ch, database=services["clickhouse"].database)
    database = f"t2i_{uuid.uuid4().hex}"
    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(**ch, database=database)
    projection = ship.ClickHouseProjection(client)
    run(projection.apply_schema())
    client.command("SYSTEM STOP MERGES trace_envelopes")
    s3 = botocore.session.get_session().create_client(
        "s3", endpoint_url=f"http://127.0.0.1:{services['s3'].host_port}",
        aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}))
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
    except Exception:                          # noqa: BLE001 - absent: this stack is ours
        s3.create_bucket(Bucket=S3_BUCKET)
    prefix = f"{services['s3'].object_prefix}{uuid.uuid4().hex}/"
    objects = S3ObjectStore(s3, S3_BUCKET, prefix)

    def cleanup() -> None:
        admin.command(f"DROP DATABASE IF EXISTS {database}")
        listed = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix).get("Contents", ())
        if listed:
            s3.delete_objects(Bucket=S3_BUCKET, Delete={
                "Objects": [{"Key": item["Key"]} for item in listed], "Quiet": True})
    return projection, objects, cleanup


@pytest.fixture(params=["memory", "stack"])
def backend(request):
    if request.param == "memory":
        yield Projection(MemoryProjection()), Objects()
        return
    if not STACK:
        pytest.skip("T2I (owner: T): no real stack - start infrx-t2i-clickhouse/-s3 on the "
                    "tasklocal t2i ports and export INFRX_T2I_STACK=1")
    projection, objects, cleanup = _stack()
    try:
        yield Projection(projection), Objects(objects)
    finally:
        cleanup()


# --------------------------------------------------------------------------------------
# the spool side
# --------------------------------------------------------------------------------------
def spool(directory: Path | None = None, io=None) -> DrillSpool:
    clock = FakeClock()
    clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
    return DrillSpool(clock, limits=DEFAULTS, failures=FailurePlan(),
                      spool_dir=directory or _dir("ship"), io=io or DrillIO())


async def capture(sink: DrillSpool, request_id: str, content: bytes, org_id: str = b.ORG_A):
    with sink.open(request_id, org_id, TraceMode.full, sink.clock.at(600)) as open_capture:
        assert open_capture.add(content) is True
        return await open_capture.finish(b.trace(request_id, org_id=org_id,
                                                 content_bytes=len(content)))


async def durable(sink: DrillSpool) -> None:
    """Flush with the fsync interval elapsed, then seal the segment for the shipper."""
    sink.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
    await sink.flush(sink.clock.now())
    await sink.rotate()


def die(sink: DrillSpool) -> Path:
    """The host dies: T1's crash drill (unsynced bytes truncated away), then the process is
    gone - its writer thread and its directory lock with it."""
    sink.crash()
    writer, sink._writer = sink._writer, None
    if writer is not None:
        writer.shutdown(wait=True)
    if sink._dir_lock is not None:
        sink.io.unlock_dir(sink._dir_lock)
        sink._dir_lock = None
    return sink.spool_dir


async def one(projection, org_id: str, request_id: str) -> ship.TraceRow:
    """The one logical row: an assertion, so a missing or duplicated row is a failure."""
    rows = await projection.find(org_id, request_id)
    assert len(rows) == 1, rows
    return rows[0]


def recorded(lookup):
    """A pins lookup that also records what it was asked."""
    calls = []

    async def asked(org_id: str, request_id: str):
        calls.append((org_id, request_id))
        return await lookup(org_id, request_id)
    asked.calls = calls
    return asked


def pins_for(table: dict):
    async def lookup(org_id: str, request_id: str):
        return table.get((org_id, request_id))
    return recorded(lookup)


# ======================================================================================
# flag: capture and shipping default OFF
# ======================================================================================
def test_shipping_is_off_unless_the_spool_the_projection_and_the_bucket_are_all_set():
    """Anything reachable from the launched App/API defaults OFF: the defaults ship
    nothing, and so does any one of the three left unset."""
    assert ship.shipping_enabled(DEFAULTS) is False
    full = DEFAULTS.replace(trace_spool_dir="/var/spool/infrx", clickhouse_url="http://ch:8123",
                            s3_trace_bucket="infrx-traces")
    assert ship.shipping_enabled(full) is True
    for unset in ("trace_spool_dir", "clickhouse_url", "s3_trace_bucket"):
        assert ship.shipping_enabled(full.replace(**{unset: " "})) is False, unset


def test_no_shipper_is_built_unless_shipping_is_enabled(monkeypatch):
    """T2I WR-3's factory: nothing is built (nothing connects) while the flag is OFF;
    enabled, the shipper reads D5's pins from PostgreSQL and writes to the trace bucket."""
    import clickhouse_connect
    asked = []
    monkeypatch.setattr(clickhouse_connect, "get_client",
                        lambda **kw: asked.append(kw) or "a clickhouse client")
    full = DEFAULTS.replace(trace_spool_dir="/var/spool/infrx", clickhouse_url="http://ch:8123",
                            s3_trace_bucket="infrx-traces", database_url="postgresql://db/x")
    for limits in (DEFAULTS, full.replace(s3_trace_bucket="")):
        assert ship.build_shipper(limits, spool=None) is None
    assert asked == []
    built = ship.build_shipper(full, spool="the spool", endpoint_url="http://127.0.0.1:1")
    assert asked == [{"dsn": "http://ch:8123"}]
    assert (built.spool, built.projection.client) == ("the spool", "a clickhouse client")
    assert isinstance(built.pins, ship.PgPins) and built.objects.bucket == "infrx-traces"
    kept = built.retention                    # T3: the replay consults the tombstones
    assert kept is not None, "the production shipper would resurrect deleted traces"
    assert (kept.traces, kept.objects, kept.store.client) == \
        (built.projection, built.objects, "a clickhouse client")
    assert (kept.content_days, kept.metadata_months) == (90, 13)


# ======================================================================================
# TRACE-RECOVER
# ======================================================================================
def test_a_crash_before_fsync_ships_only_the_promised_records(backend):
    """Crash between append and fsync, restart, ship: the fsynced record arrives with its
    content, the unsynced one never does, a torn tail after the promised prefix is counted
    rather than fatal, and the shipped segment is acked away whole."""
    projection, objects = backend

    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"promised content")
        sink.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await sink.flush(sink.clock.now())                     # A fsynced
        await capture(sink, ID_B, b"never promised")
        await sink.flush(sink.clock.now())                     # B appended, interval not due
        [segment] = sink.segments()
        with open(die(sink) / segment.name, "ab") as handle:
            handle.write(FRAME.pack(4_096, 0, 0)[:7])          # half a frame header
        restarted = spool(sink.spool_dir)
        report = await ship.Shipper(restarted, projection, objects).ship()
        assert (report.shipped, report.rows, report.held) == (1, 1, {}), report
        assert report.torn == 1
        row = await one(projection, b.ORG_A, ID_A)
        assert row.content_stored and row.content_bytes == len(b"promised content")
        assert await objects.get(row.content_key) == b"promised content"
        assert await projection.find(b.ORG_A, ID_B) == []
        assert restarted.segments() == ()
        await restarted.close()
    run(scenario())


def test_an_unsynced_record_in_the_active_segment_is_never_shipped(backend):
    """Only a sealed segment is shipped: the active one may hold appended-but-unsynced
    records, which the spool has not promised and a crash may still take away."""
    projection, objects = backend

    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"appended only")
        await sink.flush(sink.clock.now())                     # appended, not fsynced
        report = await ship.Shipper(sink, projection, objects).ship()
        assert (report.shipped, report.rows) == (0, 0)
        assert await projection.find(b.ORG_A, ID_A) == []
        await durable(sink)
        assert (await ship.Shipper(sink, projection, objects).ship()).shipped == 1
        assert len(await projection.find(b.ORG_A, ID_A)) == 1
        await sink.close()
    run(scenario())


class LostAck(DrillIO):
    """The shipper dies after the projection insert and before the ack lands."""

    def unlink(self, path):
        raise OSError(5, "Input/output error")


def test_duplicate_shipping_after_a_lost_ack_is_one_logical_trace(backend):
    """The replay after a lost ack re-inserts every row and re-puts every object: still one
    logical trace per record, one object, and the segment goes on the second pass."""
    projection, objects = backend

    async def scenario():
        sink = spool(io=LostAck())
        for request_id in (ID_A, ID_B):
            await capture(sink, request_id, f"content {request_id}".encode())
        await durable(sink)
        first = await ship.Shipper(sink, projection, objects).ship()
        assert first.shipped == 0 and first.rows == 2 and len(first.held) == 1, first
        directory = die(sink)
        restarted = spool(directory)
        second = await ship.Shipper(restarted, projection, objects).ship()
        assert (second.shipped, second.rows, second.held) == (1, 2, {}), second
        for request_id in (ID_A, ID_B):
            row = await one(projection, b.ORG_A, request_id)
            assert row.content_stored
            assert await objects.get(row.content_key) == f"content {request_id}".encode()
        if isinstance(projection.inner, MemoryProjection):
            assert len(projection.inner.inserted) == 4          # two physical copies each
        assert restarted.segments() == ()
        await restarted.close()
    run(scenario())


def test_a_projection_outage_leaves_the_segment_for_the_retry(backend):
    """ClickHouse unavailable: nothing is acked, and the retry ships exactly once."""
    projection, objects = backend

    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"content")
        await durable(sink)
        projection.down = True
        report = await ship.Shipper(sink, projection, objects).ship()
        assert report.shipped == 0 and list(report.held.values()) == ["DependencyUnavailable"]
        assert len(sink.segments()) == 1
        projection.down = False
        assert (await ship.Shipper(sink, projection, objects).ship()).shipped == 1
        assert len(await projection.find(b.ORG_A, ID_A)) == 1
        assert sink.segments() == ()
        await sink.close()
    run(scenario())


def test_metadata_ships_while_the_content_store_is_down_and_content_follows(backend):
    """The row arrives without its content (never claiming it), the segment is held, and
    the retry that stores the content supersedes the metadata-only row."""
    projection, objects = backend

    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"late content")
        await durable(sink)
        objects.down = True
        report = await ship.Shipper(sink, projection, objects).ship()
        assert report.shipped == 0 and report.rows == 1 and len(report.held) == 1
        row = await one(projection, b.ORG_A, ID_A)
        assert not row.content_stored
        objects.reads.clear()
        assert await ship.read_content(projection, objects, b.ORG_A, ID_A) is None
        assert objects.reads == [], "a row that does not claim its content is not followed"
        objects.down = False
        assert (await ship.Shipper(sink, projection, objects).ship()).shipped == 1
        row = await one(projection, b.ORG_A, ID_A)
        assert row.content_stored
        assert await ship.read_content(projection, objects, b.ORG_A, ID_A) == b"late content"
        await sink.close()
    run(scenario())


async def versions_scenario(projection, objects, lookup, credit, pins, lost) -> None:
    """The versions case for any lookup (`test_pins_pg.py` reruns it with D5's real one):
    `credit` = the (org, request) admitted at `pins`; `lost` = the (org, request) of a lossy,
    metadata-only envelope with no pins; and another organization's envelope claiming the
    credit request's id gets none either."""
    org_id, request_id = credit
    sink = spool()
    await capture(sink, request_id, b"content", org_id=org_id)
    lossy = b.trace(lost[1], org_id=lost[0], content_bytes=0).model_copy(
        update={"loss_reason": TraceLossReason.memory_budget})
    await sink.offer(lossy)
    await capture(sink, request_id, b"another org's claim", org_id=b.ORG_B)
    await durable(sink)
    report = await ship.Shipper(sink, projection, objects, pins=lookup).ship()
    assert (report.shipped, report.rows, report.held) == (1, 3, {}), report
    a = await one(projection, org_id, request_id)
    assert (a.serving_version_id, a.rate_card_version, a.policy_version) == \
        (pins.serving_version_id, pins.rate_card_version, pins.policy_version)
    assert (a.model_revision, a.price_version, a.loss_reason, a.mode) == \
        (b.MODEL, "pv_test", "none", "full")
    assert a.content_complete
    for other in (lost, (b.ORG_B, request_id)):
        row = await one(projection, *other)
        assert (row.serving_version_id, row.rate_card_version, row.policy_version) == \
            (None, None, None), other
    row = await one(projection, *lost)
    assert row.loss_reason == "memory_budget" and not row.content_complete
    assert row.content_key is None and not row.content_stored
    assert sorted(lookup.calls) == sorted([credit, lost, (b.ORG_B, request_id)])
    await sink.close()


def test_the_projection_carries_the_versions_and_the_loss_state(backend):
    """Serving, rate and source-policy versions come from the durable request record
    (looked up by the envelope's own organization); model revision, price version and the
    loss state come from the envelope. The lookup here is a table; D5's real one on
    PostgreSQL reruns the same scenario in `test_pins_pg.py`."""
    projection, objects = backend
    run(versions_scenario(projection, objects, pins_for({(b.ORG_A, ID_A): PINS}),
                          (b.ORG_A, ID_A), PINS, (b.ORG_A, ID_B)))


def test_a_pins_lookup_outage_holds_the_segment(backend):
    """A row without its versions because PostgreSQL did not answer would be a wrong row
    for ever (a replay inserts the same version): the segment waits instead."""
    projection, objects = backend

    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"content")
        await durable(sink)

        async def down(org_id, request_id):
            raise errors.DependencyUnavailable("postgres did not answer")
        report = await ship.Shipper(sink, projection, objects, pins=down).ship()
        assert report.shipped == 0 and len(report.held) == 1
        assert await projection.find(b.ORG_A, ID_A) == []
        await sink.close()
    run(scenario())


def _adopted(directory: Path, name: str, data: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)


def test_a_segment_this_reader_cannot_read_is_never_acked():
    """An unknown format version, or a checksum-valid frame that is no envelope, is kept
    for a reader that understands it and reported - never deleted as shipped."""
    async def scenario():
        directory = _dir("ship")
        _adopted(directory, "trace-0000000000000001-000000.seg",
                 HEADER.pack(SEGMENT_MAGIC, 99) + b"a future format")
        payload = b'{"not": "an envelope"}'
        _adopted(directory, "trace-0000000000000001-000001.seg",
                 segment_header() + pack_frame(payload, frame_checksum(payload, (), 0, 0), 0))
        sink = spool(directory)
        report = await ship.Shipper(sink, Projection(MemoryProjection()), Objects()).ship()
        assert report.shipped == 0 and len(report.held) == 2, report
        assert (report.unreadable, report.poison) == (1, 1)
        assert len(sink.segments()) == 2 and len(list(directory.glob("*.seg"))) == 2
        await sink.close()
    run(scenario())


# ======================================================================================
# TRACE-TENANT
# ======================================================================================
def test_a_tenant_reads_only_its_own_content(backend):
    """The organization is the caller's trusted identity, bound server-side: another
    tenant's request id finds no row, and no object is ever fetched for it."""
    projection, objects = backend

    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"org A's content")
        await capture(sink, ID_C, b"org B's content", org_id=b.ORG_B)
        await durable(sink)
        await ship.Shipper(sink, projection, objects).ship()
        assert await ship.read_content(projection, objects, b.ORG_A, ID_A) == b"org A's content"
        assert await ship.read_content(projection, objects, b.ORG_B, ID_C) == b"org B's content"
        objects.reads.clear()
        assert await projection.find(b.ORG_B, ID_A) == []
        assert await ship.read_content(projection, objects, b.ORG_B, ID_A) is None
        assert await ship.read_content(projection, objects, b.ORG_A, ID_C) is None
        assert objects.reads == []
        row = await one(projection, b.ORG_A, ID_A)
        assert row.content_key.startswith(f"trace/{b.ORG_A}/")
        await sink.close()
    run(scenario())


def test_a_row_pointing_at_another_tenants_object_is_never_followed(backend):
    """A cross-org object ref planted in the projection is refused before any read."""
    projection, objects = backend

    async def scenario():
        await objects.put_if_absent(f"trace/{b.ORG_A}/x:0", b"org A's secret", "application/json")
        row = ship.TraceRow(
            org_id=b.ORG_B, trace_id="forged:0", request_id=ID_C, key_id=b.KEY_B,
            mode="full", started_at=b.trace(ID_C).started_at, completed_at=None,
            loss_reason="none", content_complete=True, content_bytes=14,
            content_key=f"trace/{b.ORG_A}/x:0", content_stored=True,
            request_schema_version=1, model_revision=b.MODEL, price_version="pv_test",
            serving_version_id=None, rate_card_version=None, policy_version=None)
        await projection.insert([row])
        objects.reads.clear()
        assert await ship.read_content(projection, objects, b.ORG_B, ID_C) is None
        assert objects.reads == []
    run(scenario())
