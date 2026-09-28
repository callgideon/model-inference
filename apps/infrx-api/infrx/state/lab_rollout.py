"""D9: release policies and stable experiment assignment over the named RPCs of
`0033_lab_rollout.sql` (ROLLOUT-PIN, ROLLOUT-RECOVER).

A policy revision is an immutable `lab.rollout_policy.1` record (`PgLabDataStore.publish`).
`start` runs one; every later move is a compare-and-swap on the rollout's `fence` (read it
from `rollout`/the last answer): a stale publisher gets `StateConflict` and must re-read. An
expansion names a later version of the same policy and the provider's evaluation runs as its
evidence; a rollback is a new decision, never an edit. `assign` is stable per request: the
first answer is stored and every retry gets it back; an explicit serving pin is honoured as
given. The subject (account or session) is hashed into the cohort digest and never stored.

`provider_org_id` and `decided_by` are the server's (the L2 port, the session), never request
fields. Nothing composes this store yet (R1-R3 wire it).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..contracts.lab import records
from .jobstore import Connect
from .lab_data import PgLabDataStore


class PgLabRolloutStore:
    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def start(self, policy_ref: str, *, provider_org_id: str, decided_by: str,
                    reason: str) -> dict[str, Any]:
        return await self._call("lab_rollout_start", {
            "provider_org_id": provider_org_id, "policy_ref": policy_ref,
            "decided_by": decided_by, "reason": reason})

    async def transition(self, policy_id: str, action: str, *, fence: int,
                         provider_org_id: str, decided_by: str, reason: str,
                         policy_ref: str | None = None,
                         evidence_refs: list[str] | None = None) -> dict[str, Any]:
        """pause | resume | expand | stop | rollback at `fence`; `expand` takes the later
        `policy_ref` and its `evidence_refs`."""
        args = {"provider_org_id": provider_org_id, "policy_id": policy_id, "fence": fence,
                "action": action, "decided_by": decided_by, "reason": reason}
        if action == "expand":
            args.update(policy_ref=policy_ref, evidence_refs=list(evidence_refs or ()))
        return await self._call("lab_rollout_transition", args)

    async def assign(self, policy_id: str, request_id: str, subject_key: str, *,
                     provider_org_id: str,
                     explicit_serving_ref: str | None = None) -> dict[str, Any]:
        args = {"provider_org_id": provider_org_id, "policy_id": policy_id,
                "request_id": request_id, "subject_key": subject_key}
        if explicit_serving_ref is not None:
            args["explicit_serving_ref"] = explicit_serving_ref
        return await self._call("lab_rollout_assign", args)

    async def rollout(self, policy_id: str, *, provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_rollout", {"provider_org_id": provider_org_id,
                                                "policy_id": policy_id})


# --- WR-R2-1: R2's ReleaseStore (`0039_lab_release.sql`) -----------------------------------
@dataclass(frozen=True)
class Release:
    """D9's row for one policy revision, field for field R2's `Release`."""

    state: str                       # running | approved | rolled_back (| paused | stopped)
    fence: int
    plan_digest: str
    started_at: datetime


def _release(doc: dict[str, Any]) -> Release:
    return Release(state=doc["state"], fence=doc["fence"], plan_digest=doc["plan_digest"],
                   started_at=datetime.fromisoformat(doc["started_at"]))


class PgReleaseStore:
    """R2's `ReleaseStore` over D9's rollout rows: `release` by revision, `transition` a CAS
    on the rollout's fence (a stale fence or an undeclared move is `StateConflict`), each
    decision a validated `lab.rollout_decision.1` stored append-only with its reasons.
    `start` launches a revision with its plan digest frozen (the launcher's, not R2's)."""

    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def start(self, policy_ref: str, *, provider_org_id: str, plan_digest: str,
                    decided_by: str, reason: str) -> Release:
        return _release(await self._call("lab_release_start", {
            "provider_org_id": provider_org_id, "policy_ref": policy_ref,
            "plan_digest": plan_digest, "decided_by": decided_by, "reason": reason}))

    async def release(self, policy_ref: str) -> Release:
        return _release(await self._call("lab_release", {"policy_ref": policy_ref}))

    async def transition(self, policy_ref: str, *, fence: int, to: str,
                         decision: dict[str, Any], reasons: tuple[str, ...]) -> int:
        records.parse(decision)                 # the contract refuses first (LabRejected)
        return (await self._call("lab_release_transition", {
            "policy_ref": policy_ref, "fence": fence, "to": to, "decision": decision,
            "reasons": list(reasons)}))["fence"]
