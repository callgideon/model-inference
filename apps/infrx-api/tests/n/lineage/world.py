"""The N3 fake world: L2 (`FakeAccessStore` behind the real `LabAccess`), T3 (the real
`Retention` over in-memory projections), a C2 content port shaped by the C2 brief (the
content lane builds the real one; `FakeContent` is its stand-in), D6F feedback rows, and
D7's rules in memory (`FakeLabStore`, whose grants mirror L2's: one table in PostgreSQL).

GRANTOR's organization grants NEMO provider_sharing + training over request, response and
feedback content of MODEL; GRANTOR_2 grants the same without feedback. DEV is a NEMO
developer, VIEWER a NEMO viewer, CONSUMER holds no provider membership.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from infrx.contracts import errors
from infrx.contracts.records import Feedback
from infrx.contracts.v2 import records as v2
from infrx.lab.access import LabAccess
from infrx.lab.access.fakes import FakeAccessStore
from infrx.media.store import InMemoryObjectStore
from infrx.traces import ship
from infrx.traces.retention import REQUEST, Retention, Tombstone

from ..imports.world import NEMO, OTHER, FakeLabStore, grant_ref

GRANTOR = "c1000000-0000-4000-8000-0000000000c1"
GRANTOR_2 = "c2000000-0000-4000-8000-0000000000c2"
DEV = "d0000000-0000-4000-8000-00000000000a"
VIEWER = "e0000000-0000-4000-8000-00000000000a"
CONSUMER = "f1000000-0000-4000-8000-0000000000f1"
MODEL = "marlin-2b"
T0 = datetime(2026, 9, 27, 12, tzinfo=UTC)
CATEGORIES = (v2.DataCategory.request_content, v2.DataCategory.response_content)
SIGNING_KEY = b"n3-test-only-signing-key"            # generated for tests; never a secret


def rid(n: int) -> str:
    return f"7e000000-0000-4000-8000-{n:012x}"


@dataclass
class Traces:
    """T2I's projection as `Retention` reads it (`find`)."""

    rows: list = field(default_factory=list)

    async def find(self, org_id: str, request_id: str):
        return [r for r in self.rows if (r.org_id, r.request_id) == (org_id, request_id)]


@dataclass
class Stones:
    """`trace_deletions` in memory (the part `Retention.delete` and its reads use)."""

    stones: list = field(default_factory=list)

    async def put(self, stones) -> None:
        self.stones.extend(stones)

    async def get(self, pairs) -> dict:
        found: dict = {}
        for s in self.stones:
            if (s.org_id, s.request_id) in set(pairs):
                found.setdefault((s.org_id, s.request_id), {})[s.scope] = s
        return found


class FakeContent:
    """C2 as its brief shapes it (WR-N3-1 files the signature): `sign` binds a short-lived
    ref to the recipient provider, the grantor's request and the grant; `read` fails closed -
    a forged, altered or foreign ref is `NotFound`, an expired ref `Gone`, a grant that is no
    longer current (or another grant) `Forbidden`, content T3 no longer serves `Gone` - even
    for a ref issued before. `calls` records every request it was asked about."""

    def __init__(self, directory, retention: Retention, ttl_s: int = 60) -> None:
        self.directory, self.retention, self.ttl = directory, retention, timedelta(seconds=ttl_s)
        self.calls: list[str] = []
        self.down = False

    def _mac(self, payload: bytes) -> str:
        return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()

    async def sign(self, *, provider_org_id: str, grantor_org_id: str, request_id: str,
                   grant_id: str) -> str:
        self.calls.append(request_id)
        expires = (await self.directory.db_now()) + self.ttl
        payload = json.dumps([provider_org_id, grantor_org_id, request_id, grant_id,
                              expires.isoformat()]).encode()
        return base64.urlsafe_b64encode(payload).decode() + "." + self._mac(payload)

    async def read(self, ref: str, *, provider_org_id: str) -> dict:
        if self.down:
            raise errors.DependencyUnavailable("content service down")
        try:
            body, mac = ref.split(".")
            payload = base64.urlsafe_b64decode(body)
            recipient, grantor, request, grant_id, expires = json.loads(payload)
        except ValueError:
            raise errors.NotFound("no such content reference") from None
        if not hmac.compare_digest(mac, self._mac(payload)) or recipient != provider_org_id:
            raise errors.NotFound("no such content reference")
        now = await self.directory.db_now()
        if now >= datetime.fromisoformat(expires):
            raise errors.Gone("the content reference expired")
        grant = await self.directory.current_grant(grantor, provider_org_id)
        if grant is None or grant.grant_id != grant_id or not grant.is_current(now):
            raise errors.Forbidden("the grant is no longer current")
        data = await self.retention.read_content(grantor, request)
        if data is None:
            raise errors.Gone("the content is no longer served")
        return json.loads(data)


class World:
    def __init__(self) -> None:
        self.directory = FakeAccessStore(now=T0, provider_names={NEMO: "Nemo", OTHER: "Other"})
        for user, role in ((DEV, "developer"), (VIEWER, "viewer")):
            self.directory.memberships[(NEMO, user)] = v2.ProviderMembership(
                provider_org_id=NEMO, user_id=user, role=role, granted_by="ops",
                granted_at=T0 - timedelta(days=1))
        self.access = LabAccess(self.directory)
        self.lab = FakeLabStore()
        self.traces, self.stones = Traces(), Stones()
        self.trace_objects, self.objects = InMemoryObjectStore(), InMemoryObjectStore()
        self.retention = Retention(self.stones, self.traces, None, self.trace_objects,
                                   clock=lambda: self.directory.now)
        self.content = FakeContent(self.directory, self.retention)
        self.feedback_rows: dict[tuple[str, str], list[Feedback]] = {}
        self.grant(GRANTOR, 0x91, v2.DataCategory.feedback)
        self.grant(GRANTOR_2, 0x92)

    @property
    def now(self) -> datetime:
        return self.directory.now

    def advance(self, **delta) -> None:
        self.directory.now += timedelta(**delta)

    def grant(self, grantor: str, tag: int, *extra: v2.DataCategory) -> None:
        """A new current version of the pair's grant (0027's put), mirrored into D7's view."""
        prev = self.directory.grants.get((grantor, NEMO))
        grant = v2.AccessGrant(
            grant_id=prev.grant_id if prev else f"{tag:08x}-0000-4000-8000-000000000001",
            version=prev.version + 1 if prev else 1, grantor_org_id=grantor,
            recipient_provider_org_id=NEMO, model_ids=(MODEL,), categories=CATEGORIES + extra,
            purposes=(v2.DataPurpose.provider_sharing, v2.DataPurpose.training),
            retention_days=30, effective_at=self.now)
        self.directory.grants[(grantor, NEMO)] = grant
        for version in range(1, grant.version + 1):  # D7 reads every ref's CURRENT version
            self.lab.grants[grant_ref(grant.grant_id, NEMO, version)] = {
                "provider": NEMO, "purposes": {"provider_sharing", "training"}, "current": True}

    def revoke(self, grantor: str) -> None:
        grant = self.directory.grants[(grantor, NEMO)]
        self.directory.revoke(grantor, NEMO, self.now)
        for ref, row in self.lab.grants.items():       # D7 reads the current (revoked) version
            if f":{grant.grant_id}@" in ref:
                row["current"] = False

    def trace(self, n: int, grantor: str = GRANTOR, *, started: datetime | None = None,
              stored: bool = True, feedback: tuple = ()) -> str:
        """Request `n` of `grantor`: one projection row, its content object, and D6F rows."""
        request = rid(n)
        started = started or self.now - timedelta(hours=1)
        key = ship.content_key(grantor, f"77000000-0000-4000-8000-{n:012x}")
        self.traces.rows.append(SimpleNamespace(
            org_id=grantor, request_id=request, started_at=started,
            completed_at=started + timedelta(seconds=3), content_stored=stored,
            content_key=key if stored else None))
        if stored:
            self.trace_objects.seed(key, json.dumps({
                "request": {"messages": [{"role": "user", "content": f"question {n}"}]},
                "output": {"content": f"answer {n}"}}).encode(), "application/json")
        self.feedback_rows[(grantor, request)] = [Feedback(
            feedback_id=f"fb-{n}-{i}", request_id=request, org_id=grantor,
            author_principal=f"user:{grantor}", author_role="customer", channel="api",
            name=name, value=value, created_at=started + timedelta(minutes=i + 1))
            for i, (name, value) in enumerate(feedback)]
        return request

    async def feedback(self, org_id: str, request_id: str):
        """D6F's `request_feedback` for the grantor's own request (0028), oldest first."""
        return self.feedback_rows.get((org_id, request_id), [])

    async def delete(self, grantor: str, request_id: str) -> None:
        """T3's logical deletion (the receipt), as the owner's delete route calls it."""
        await self.retention.delete(grantor, request_id, "owner request")


__all__ = ["CONSUMER", "DEV", "GRANTOR", "GRANTOR_2", "MODEL", "NEMO", "OTHER", "REQUEST",
           "T0", "VIEWER", "FakeContent", "Tombstone", "World", "rid"]
