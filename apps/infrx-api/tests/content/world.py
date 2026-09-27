"""The C2 world: two providers, two grantor organizations, their jobs, T2I projection rows and
content objects, T3 retention - all in memory, on ONE clock (the access store's, R7).

C1 grants provider A {request,response}_content of MODEL_A for provider_sharing, 30 days of
retention, no expiry; C2 grants provider B the same for MODEL_B. REQ is C1's job with stored
content; REQ_B is C2's. DEV_A and DEV_A2 are A's developers, VIEWER_A its viewer; DEV_B and DEV_A are B's.
"""
from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

from infrx.content import ContentAccess
from infrx.content.fakes import FakeContentRefs
from infrx.contracts import wire
from infrx.contracts.v2 import records as v2
from infrx.lab.access.fakes import FakeAccessStore
from infrx.media.store import InMemoryObjectStore
from infrx.state.lab_data import grant_ref
from infrx.traces import ship
from infrx.traces.retention import Retention

T0 = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
A, B = "a0000000-0000-4000-8000-00000000000a", "b0000000-0000-4000-8000-00000000000b"
C1, C2 = "c1000000-0000-4000-8000-0000000000c1", "c2000000-0000-4000-8000-0000000000c2"
DEV_A, DEV_A2 = "d0000000-0000-4000-8000-00000000000a", "d0000000-0000-4000-8000-0000000000a2"
VIEWER_A, DEV_B = "e0000000-0000-4000-8000-00000000000a", "d0000000-0000-4000-8000-00000000000b"
MODEL_A, MODEL_B = "m0000000-0000-4000-8000-00000000000a", "m0000000-0000-4000-8000-00000000000b"
KEY = "3c3c3c3c-0000-4000-8000-000000000003"
REQ, REQ_B = "4d4d4d4d-0000-4000-8000-000000000001", "4d4d4d4d-0000-4000-8000-000000000002"
S3_KEY, S3_SECRET, S3_BUCKET = "infrxe2minio", "infrx-e2-local-secret", "infrx-lab-c2"
BOTH = (v2.DataCategory.request_content, v2.DataCategory.response_content)
SHARING = v2.DataPurpose.provider_sharing
BODY = wire.TraceContentBody(
    request=wire.TraceContentRequest(model="nemostation/marlin-2b",
                                     messages=({"role": "user", "content": "hi"},)),
    response=wire.TraceContentResponse(status=200, choices=(
        wire.TraceContentChoice(index=0, finish_reason="stop", content="hello"),)))


class Traces:
    """T2I's projection read with FINAL (one row per org/trace), and G4T's `TracePages`."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ship.TraceRow] = {}

    async def find(self, org_id, request_id):
        return sorted((r for r in self.rows.values()
                       if (r.org_id, r.request_id) == (org_id, request_id)),
                      key=lambda r: r.trace_id)

    async def page(self, org_id, *, after, since, until, limit):
        rows = sorted((r for r in self.rows.values() if r.org_id == org_id
                       and (after is None or (r.started_at, r.trace_id) > after)
                       and (since is None or r.started_at >= since)
                       and (until is None or r.started_at < until)),
                      key=lambda r: (r.started_at, r.trace_id))
        return rows[:limit]


class Tombstones:
    """`trace_deletions` FINAL: the latest tombstone per (org, request, scope)."""

    def __init__(self) -> None:
        self.stones: dict = {}

    async def put(self, stones) -> None:
        for s in stones:
            self.stones[(s.org_id, s.request_id, s.scope)] = s

    async def get(self, pairs) -> dict:
        found: dict = {}
        for (org, request, scope), stone in self.stones.items():
            if (org, request) in set(pairs):
                found.setdefault((org, request), {})[scope] = stone
        return found

    async def pending(self, limit, after=None):
        return [s for s in self.stones.values() if s.cleaned_at is None][:limit]

    async def purge(self, org_id, request_id) -> None:
        """The rows go in ClickHouse; the in-memory rows stay, as the case only reads objects."""


class Objects(InMemoryObjectStore):
    """The trace object store with a log of reads: in memory, or over `inner` (the real M3
    `S3ObjectStore` on MinIO)."""

    def __init__(self, inner=None) -> None:
        super().__init__()
        self.inner, self.reads = inner, []

    async def get(self, key):
        self.reads.append(key)
        return await (self.inner.get(key) if self.inner else super().get(key))

    async def delete(self, key):
        return await (self.inner.delete(key) if self.inner else super().delete(key))

    def seed(self, key, data, content_type="application/json") -> None:
        if self.inner:
            asyncio.run(self.inner.put_if_absent(key, data, content_type))
        else:
            super().seed(key, data, content_type)


def s3_store(prefix: str):
    """M3's `S3ObjectStore` on the lane's MinIO (tasklocal `lab-c2`: s3 57506), local
    literals only - never botocore's own chain."""
    import botocore.session
    from botocore.config import Config

    from infrx.contracts import tasklocal
    from infrx.media.s3 import S3ObjectStore
    port = tasklocal.local_services("lab-c2")["s3"].host_port
    client = botocore.session.get_session().create_client(
        "s3", endpoint_url=f"http://127.0.0.1:{port}", region_name="us-east-1",
        aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET,
        config=Config(s3={"addressing_style": "path"}, retries={"total_max_attempts": 1}))
    try:
        client.create_bucket(Bucket=S3_BUCKET)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    return S3ObjectStore(client, S3_BUCKET, prefix)


class World:
    def __init__(self, objects=None) -> None:
        self.store = FakeAccessStore(now=T0)
        for provider, user, role in ((A, DEV_A, v2.ProviderRole.developer),
                                     (A, DEV_A2, v2.ProviderRole.developer),
                                     (A, VIEWER_A, v2.ProviderRole.viewer),
                                     (B, DEV_B, v2.ProviderRole.developer),
                                     (B, DEV_A, v2.ProviderRole.developer)):
            self.store.memberships[(provider, user)] = v2.ProviderMembership(
                provider_org_id=provider, user_id=user, role=role, granted_by="ops",
                granted_at=T0 - timedelta(days=1))
        self.grant(C1, A, MODEL_A)
        self.grant(C2, B, MODEL_B)
        self.refs = FakeContentRefs(self.store)
        self.traces, self.tombstones, self.objects = Traces(), Tombstones(), Objects(objects)
        self.retention = Retention(self.tombstones, self.traces, None, self.objects,
                                   clock=lambda: self.store.now)
        self.access = ContentAccess(self.refs, self.retention)
        self.job(C1, REQ, MODEL_A)
        self.job(C2, REQ_B, MODEL_B)
        self.trace(C1, REQ, BODY.model_dump_json().encode())
        self.trace(C2, REQ_B, BODY.model_dump_json().encode())
        self.advance(60)

    # --- the clock ---------------------------------------------------------------
    def advance(self, seconds: float) -> None:
        self.store.now += timedelta(seconds=seconds)

    # --- grants, jobs and traces -------------------------------------------------------
    def grant(self, grantor, provider, model, *, categories=BOTH, purposes=(SHARING,),
              expires_in: timedelta | None = None, retention_days: int = 30) -> v2.AccessGrant:
        """The next version of the pair's grant, effective now (lab_put_access_grant)."""
        prev = self.store.grants.get((grantor, provider))
        grant = v2.AccessGrant(
            grant_id=prev.grant_id if prev else str(uuid.uuid4()),
            version=prev.version + 1 if prev else 1, grantor_org_id=grantor,
            recipient_provider_org_id=provider, model_ids=(model,), categories=categories,
            purposes=purposes, retention_days=retention_days, effective_at=self.store.now,
            expires_at=self.store.now + expires_in if expires_in else None)
        self.store.put_grant(grant)
        return grant

    def ref(self, grantor, provider) -> str:
        return grant_ref(self.store.grants[(grantor, provider)])

    def job(self, org, request, model, at: datetime | None = None) -> None:
        self.refs.jobs[(org, request)] = (model, at or self.store.now)

    def trace(self, org, request, content: bytes | None, *, stored: bool = True,
              started_at: datetime | None = None, trace_id: str | None = None) -> ship.TraceRow:
        trace_id = trace_id or f"seg-{len(self.traces.rows):04d}:0"
        key = ship.content_key(org, trace_id) if content is not None else None
        row = ship.TraceRow(
            org_id=org, trace_id=trace_id, request_id=request, key_id=KEY,
            mode="full" if content is not None else "minimal",
            started_at=started_at or self.store.now, completed_at=None, loss_reason="none",
            content_complete=content is not None, content_bytes=len(content or b""),
            content_key=key, content_stored=stored and content is not None,
            request_schema_version=1, model_revision="nemostation/marlin-2b@2026-09-01",
            price_version="pv_2026_09_01", serving_version_id=None, rate_card_version=None,
            policy_version=None)
        self.traces.rows[(org, trace_id)] = row
        if key and stored:
            self.objects.seed(key, content, "application/json")
        return row

    def edit(self, org, trace_id, **changes) -> None:
        self.traces.rows[(org, trace_id)] = dataclasses.replace(
            self.traces.rows[(org, trace_id)], **changes)

    # --- the calls ---------------------------------------------------------------------
    async def issue(self, user=DEV_A, provider=A, grantor=C1, request=REQ, purpose=SHARING,
                    grant: str | None = None):
        return await self.access.issue(user_id=user, provider_org_id=provider,
                                       grant_ref=grant or self.ref(grantor, provider),
                                       request_id=request, purpose=purpose)

    async def read(self, handle, user=DEV_A, provider=A):
        return await self.access.read(handle=handle, user_id=user, provider_org_id=provider)
