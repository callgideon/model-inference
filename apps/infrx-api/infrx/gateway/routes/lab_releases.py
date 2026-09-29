"""WR-R4-1: `/lab/v1/releases` and `/lab/v1/optimizations`, the Lab's read of controlled
releases (D9, R1, R2) and optimization variants (R3), and its only write: a proposal.
Records are the Lab's `apps/lab/lib/services/rollouts/port.ts` in snake_case.

    GET  /lab/v1/releases?provider_org_id=            -> {data: {releases, decisions, proposals}}
    GET  /lab/v1/optimizations?provider_org_id=       -> {data: [variant]}
    POST /lab/v1/releases/proposals?provider_org_id=  {kind, policy_ref, fence} -> 201 proposal

As `/lab/v1/control` (`lab_auth`): the actor is re-derived per call before a body is read;
every role reads (`read_aggregate_health`); only an administrator proposes
(`propose_publication`), checked once the policy is found in the provider's listing, so an
unknown or foreign policy is a 404 whatever the role (the Lab fake's order).
A proposal is the Lab's whole say: an operator decides it through
R2 (`Controller.approve` / `emergency_rollback`), outside the Lab, under D9's CAS. So a
proposal is refused (409) unless it names the revision D9 holds NOW (`ReleaseStore.release`:
the fence the page showed is still D9's), an expansion is proposed only on a running release
whose latest R2 verdict is `expand` (an inconclusive B2 report holds, so it can never be
proposed), nothing is proposed on a rolled-back release, and one proposal per policy
revision is pending at a time (the proposal store's own constraint). Another provider's
policy is a 404: D9's row is read only for a release in the provider's own listing.

A port without its table yet answers 503: the read models (D9 0033/0039 listings with R1's
aggregates and R2's latest verdict; R3's variants and comparisons - WR-R4-1's lab-sql half),
the proposal store (WR-R4-2) and D9's `ReleaseStore` (`PgReleaseStore`, lab-sql-lw2).
Mounted only when the composition put a `LabReleases` on `rt.lab_releases` (LAB_RELEASES, off).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence

from fastapi import Request
from pydantic import Field

from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.v2.records import ProviderCapability as Cap
from .. import lab_auth
from .lab_evaluations import lab_actor, lab_body, require

RELEASES_PATH, OPTIMIZATIONS_PATH = "/lab/v1/releases", "/lab/v1/optimizations"


class ProposalRequest(lab.LabModel):
    kind: Literal["expand", "rollback"]
    policy_ref: lab.RefOf("policy")
    fence: int = Field(ge=0)


class ReleaseRecords(Protocol):
    """WR-R4-1's read models (lab-sql), provider-scoped, in port.ts's snake_case."""

    async def releases(self, provider_org_id: str) -> Sequence[dict[str, Any]]:
        """D9's rows with `plan`, R1's `progress` (counts only) and R2's latest `verdict`."""

    async def decisions(self, provider_org_id: str) -> Sequence[dict[str, Any]]: ...

    async def variants(self, provider_org_id: str) -> Sequence[dict[str, Any]]:
        """R3's variants with both identities and their `infrx.variant_comparison.1`."""


class Proposals(Protocol):
    """WR-R4-2 (lab-sql): one pending proposal per policy revision."""

    async def proposals(self, provider_org_id: str) -> Sequence[dict[str, Any]]: ...

    async def add(self, provider_org_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """The stored proposal; `StateConflict` while another is proposed for its policy."""


@dataclass(frozen=True)
class LabReleases:
    sessions: lab_auth.Sessions
    access: object                          # infrx.lab.access.LabAccess
    records: ReleaseRecords | None = None
    proposals: Proposals | None = None
    store: object | None = None             # D9: R2's ReleaseStore (PgReleaseStore)

    def port(self, name: str):
        value = getattr(self, name)
        if value is None:                   # expected until its table merges: a 503
            raise errors.DependencyUnavailable(f"{name} is not wired")
        return value


async def releases(x: LabReleases, who) -> dict[str, Any]:
    records, provider = x.port("records"), who.provider_org_id
    return {"releases": list(await records.releases(provider)),
            "decisions": list(await records.decisions(provider)),
            "proposals": list(await x.port("proposals").proposals(provider))}


async def variants(x: LabReleases, who) -> list[dict[str, Any]]:
    return list(await x.port("records").variants(who.provider_org_id))


async def propose(x: LabReleases, who, wanted: ProposalRequest) -> dict[str, Any]:
    provider = who.provider_org_id
    shown = next((r for r in await x.port("records").releases(provider)
                  if r["policy_ref"] == wanted.policy_ref), None)
    if shown is None:
        raise errors.NotFound("no such release for this provider")
    require(who, Cap.propose_publication)
    live = await x.port("store").release(wanted.policy_ref)
    if live.fence != wanted.fence:
        raise errors.StateConflict("the release moved since the page was read")
    if live.state == "rolled_back":
        raise errors.StateConflict("a rolled-back release stays rolled back")
    if wanted.kind == "expand" and (live.state != "running"
                                    or (shown["verdict"] or {}).get("action") != "expand"):
        raise errors.StateConflict("an expansion needs a running release and an expand verdict")
    now = await x.access.store.db_now()
    return await x.port("proposals").add(provider, {
        "proposal_id": str(uuid.uuid4()), "kind": wanted.kind, "policy_ref": wanted.policy_ref,
        "fence": wanted.fence, "state": "proposed",
        "proposed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "decided_at": None,
        "proposed_by": who.user_id})             # 0043 records the proposer (WR-R4-2)


def register(app, rt, lab_releases: LabReleases | None = None):
    """Mount the release routes over `lab_releases` (default `rt.lab_releases`); without one
    nothing is mounted and `None` is returned."""
    x = lab_releases if lab_releases is not None else getattr(rt, "lab_releases", None)
    if x is None:
        return None

    def listing(read):
        @lab_auth.guarded
        async def handler(request: Request):
            who = await lab_actor(request, x.sessions, x.access, Cap.read_aggregate_health)
            return lab_auth.ok({"data": await read(x, who)})
        return handler

    app.add_api_route(RELEASES_PATH, listing(releases), methods=["GET"])
    app.add_api_route(OPTIMIZATIONS_PATH, listing(variants), methods=["GET"])

    @app.post(f"{RELEASES_PATH}/proposals")
    @lab_auth.guarded
    async def add_proposal(request: Request):
        who = await lab_actor(request, x.sessions, x.access,
                              Cap.read_aggregate_health)          # the role: `propose`
        wanted = await lab_body(request, rt, ProposalRequest)
        return lab_auth.ok(await propose(x, who, wanted), 201)

    return x
