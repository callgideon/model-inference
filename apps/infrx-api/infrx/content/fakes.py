"""C2-RPC's contract in memory: `ContentRefs` over L2's `FakeAccessStore` (memberships, every
grant version, the store clock R7). The PostgreSQL RPC lab-sql writes answers exactly this."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Any

from ..contracts import errors
from ..contracts.v2.ports import authorize_content_read
from ..lab.access.fakes import FakeAccessStore
from ..state.lab_data import grant_ref as ref_of   # the parameter below is `grant_ref`
from . import MAX_TTL_S, RefBinding


@dataclasses.dataclass
class FakeContentRefs:
    access: FakeAccessStore
    #: infrx.jobs: (org_id, request_id) -> (model_id, created_at)
    jobs: dict[tuple[str, str], tuple[str, datetime]] = dataclasses.field(default_factory=dict)
    #: sha256(handle) -> the ref row (the handle itself is never stored)
    refs: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)

    def _current(self, row: dict[str, Any], now: datetime) -> tuple[Any, datetime]:
        """Every rule on the CURRENT rows: the pair's latest grant version (one grant per
        pair, R166), the user's membership, the job's model, each category, the purpose;
        then the retention bound from the job's creation. Default deny."""
        grant = self.access.grants.get((row["grantor_org_id"], row["provider_org_id"]))
        model_id, created_at = self.jobs.get((row["grantor_org_id"], row["request_id"]),
                                             (None, None))
        membership = self.access.memberships.get((row["provider_org_id"], row["user_id"]))
        for category in row["categories"]:
            authorize_content_read(membership=membership, grant=grant, now=now,
                                   provider_org_id=row["provider_org_id"], model_id=model_id,
                                   category=category, purpose=row["purpose"])
        return grant, created_at + timedelta(days=grant.retention_days)

    @staticmethod
    def _binding(row, grant, expires_at) -> RefBinding:
        return RefBinding(grant_id=grant.grant_id, grant_version=grant.version,
                          grantor_org_id=row["grantor_org_id"], request_id=row["request_id"],
                          purpose=row["purpose"], expires_at=expires_at)

    async def issue(self, *, handle_sha256, user_id, provider_org_id, grant_ref: str,
                    request_id, purpose, categories, ttl_s) -> RefBinding:
        if not 1 <= ttl_s <= MAX_TTL_S or not categories:
            raise errors.InvalidRequest("a content ref needs a bounded ttl and a category")
        pinned = next((g for g in self.access.history if g.recipient_provider_org_id
                       == provider_org_id and ref_of(g) == grant_ref), None)
        if pinned is None or (pinned.grantor_org_id, request_id) not in self.jobs:
            raise errors.NotFound("no such request under a grant to this provider")
        now = self.access.now
        row = {"grant_id": pinned.grant_id, "grantor_org_id": pinned.grantor_org_id,
               "provider_org_id": provider_org_id, "user_id": user_id,
               "request_id": request_id, "purpose": purpose, "categories": tuple(categories)}
        grant, retained_until = self._current(row, now)
        expires_at = min(now + timedelta(seconds=ttl_s), retained_until,
                         grant.expires_at or retained_until)
        if expires_at <= now:
            raise errors.Gone("the content is past its grant's retention")
        if handle_sha256 in self.refs:
            raise errors.Conflict("this handle is already issued")
        self.refs[handle_sha256] = {**row, "expires_at": expires_at}
        return self._binding(row, grant, expires_at)

    async def redeem(self, *, handle_sha256, user_id, provider_org_id) -> RefBinding:
        row = self.refs.get(handle_sha256)
        if row is None or row["provider_org_id"] != provider_org_id \
                or row["user_id"] != user_id:
            raise errors.NotFound("no such content reference")
        now = self.access.now
        if now >= row["expires_at"]:
            raise errors.Gone("the content reference has expired")
        grant, retained_until = self._current(row, now)
        if now >= retained_until:
            raise errors.Gone("the content is past its grant's retention")
        return self._binding(row, grant, min(row["expires_at"], retained_until))

    async def held(self, org_id: str, request_id: str) -> bool:
        """A ref still redeemable by its recipient holds the request's content (T3)."""
        now = self.access.now
        for row in self.refs.values():
            if (row["grantor_org_id"], row["request_id"]) != (org_id, request_id) \
                    or now >= row["expires_at"]:
                continue
            try:
                if now < self._current(row, now)[1]:
                    return True
            except errors.DomainError:
                continue
        return False

