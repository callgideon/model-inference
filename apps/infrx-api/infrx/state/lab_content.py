"""C2-RPC: C2's `ContentRefs` port (`infrx.content`) over `0041_lab_content_refs.sql`, plus
N3's sample tombstones and content bounds.

Every grant, expiry and retention rule lives in the RPCs, on the database clock (R7); this
adapter only carries arguments and turns refusals into C2's typed errors (`NotFound`,
`Forbidden`, `Gone`, `InvalidRequest`, `Conflict`). `user_id` and `provider_org_id` are the
server's (the L2 port / the session), never request fields. Nothing composes this yet
(WR-C2-2: T3's `Retention(holds=ContentAccess(PgContentRefs(connect), ...).holds)`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from .jobstore import Connect
from .lab_data import PgLabDataStore


def _binding(doc: dict[str, Any]):
    from ..content import RefBinding          # the content lane's model (merged beside this)
    return RefBinding.model_validate(doc)


class PgContentRefs:
    """`infrx.content.ContentRefs`, answer for answer the content lane's FakeContentRefs."""

    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def issue(self, *, handle_sha256: str, user_id: str, provider_org_id: str,
                    grant_ref: str, request_id: str, purpose: str,
                    categories: Sequence[str], ttl_s: int):
        return _binding(await self._call("lab_content_ref_issue", {
            "handle_sha256": handle_sha256, "user_id": user_id,
            "provider_org_id": provider_org_id, "grant_ref": grant_ref,
            "request_id": request_id, "purpose": str(purpose),
            "categories": [str(c) for c in categories], "ttl_s": ttl_s}))

    async def redeem(self, *, handle_sha256: str, user_id: str, provider_org_id: str):
        return _binding(await self._call("lab_content_ref_redeem", {
            "handle_sha256": handle_sha256, "user_id": user_id,
            "provider_org_id": provider_org_id}))

    async def held(self, org_id: str, request_id: str) -> bool:
        return (await self._call("lab_content_ref_held", {
            "org_id": org_id, "request_id": request_id}))["held"]


class PgSampleRestrictions:
    """N3's tombstones and content bounds in D7 (the per-read object listing's replacement):
    a tombstone is permanent and its first reason stands; a bound is write-once."""

    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def tombstone(self, sample_ids: Sequence[str], *, provider_org_id: str,
                        reason: str) -> list[str]:
        """The ids this call stoned (already stoned ones keep their first reason)."""
        return (await self._call("lab_tombstone_samples", {
            "provider_org_id": provider_org_id, "sample_ids": list(sample_ids),
            "reason": reason}))["tombstoned"]

    async def bound(self, bounds: dict[str, datetime], *, provider_org_id: str) -> None:
        await self._call("lab_bound_samples", {
            "provider_org_id": provider_org_id,
            "bounds": [{"sample_id": s, "content_until": at.isoformat()}
                       for s, at in bounds.items()]})

    async def blocked(self, dataset_ref: str, *, provider_org_id: str) -> dict[str, str]:
        """sample id -> why it is denied now (tombstone reason, or `content_expired`)."""
        return await self._call("lab_blocked_samples", {
            "provider_org_id": provider_org_id, "dataset_ref": dataset_ref})

    async def permitted(self, dataset_ref: str, *, provider_org_id: str,
                        purpose: str = "provider_sharing") -> list[str]:
        return await self._call("lab_permitted_samples", {
            "provider_org_id": provider_org_id, "dataset_ref": dataset_ref,
            "purpose": purpose})
