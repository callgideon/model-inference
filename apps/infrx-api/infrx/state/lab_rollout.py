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
from typing import Any, Sequence

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


@dataclass(frozen=True)
class Decision:
    """One `lab.rollout_decision.1` R2 recorded (`lab_rollout_events`'s stored move)."""

    decision: str                    # expand | hold | rollback
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    decided_by: str
    at: datetime


@dataclass(frozen=True)
class ReleaseListing:
    """WR-R4-1/WR-R2-3: one row of `lab_releases_in` - a `Release` plus the identity a
    listing (never known ahead of time by a page or a pass loop) needs, and R2's latest
    verdict, if a decision has been made yet."""

    policy_id: str
    provider_org_id: str
    endpoint_id: str
    policy_ref: str
    release: Release
    latest_decision: Decision | None


def _listing(doc: dict[str, Any]) -> ReleaseListing:
    d = doc.get("latest_decision")
    return ReleaseListing(
        policy_id=doc["policy_id"], provider_org_id=doc["provider_org_id"],
        endpoint_id=doc["endpoint_id"], policy_ref=doc["policy_ref"],
        release=_release(doc), latest_decision=None if d is None else Decision(
            decision=d["decision"], reasons=tuple(d["reasons"]),
            evidence_refs=tuple(d["evidence_refs"]), decided_by=d["decided_by"],
            at=datetime.fromisoformat(d["at"])))


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

    async def releases_in(self, states: Sequence[str] = (), *,
                          provider_org_id: str) -> list[ReleaseListing]:
        """WR-R4-1/WR-R2-3: the provider's releases (newest-started first), each with R2's
        latest verdict; `states` narrows to those `lab_rollouts.state` values (empty: every
        state). Never `NotFound` - a provider with none gets `[]`."""
        return [_listing(doc) for doc in await self._call("lab_releases_in", {
            "provider_org_id": provider_org_id, "states": list(states)})]

    async def providers_in(self, states: Sequence[str]) -> list[str]:
        """WR-C5-PROVIDERS (0053): the providers with a release in one of `states`."""
        return await self._call("lab_providers_with", {"work": "release",
                                                       "states": list(states)})

    async def decisions(self, *, provider_org_id: str) -> list[dict[str, Any]]:
        """WR-R4-2 (0053): every D9 decision of the provider's releases, oldest first -
        `{policy_ref, decision, reasons, evidence_refs, decided_by, decided_at}`."""
        return await self._call("lab_release_decisions", {"provider_org_id": provider_org_id})

    async def live(self, policy_ref: str):
        """WR-C6-LIVE (0054, R244): R2's `Live` of the revision, per arm from R1's assignments
        and the admitted jobs they name, or None when no assignment names an admitted job yet
        (nothing observed: the caller holds, never evaluates zeros). The spend is the
        candidate arm's - the release's own traffic - in the unit its jobs settled in; legacy
        USD is no Lab unit and is refused by name, never converted."""
        from ..contracts import errors
        from ..rollouts.control import Arm, Live
        arms = {r["arm"]: r for r in await self._call("lab_release_live",
                                                      {"policy_ref": policy_ref})}
        if not arms:
            return None
        cand = arms["candidate"]
        if cand["spent"]["unit"] not in records.LAB_UNITS:
            raise errors.InvalidRequest(f"this release's jobs settled in {cand['spent']['unit']}, "
                                        "no Lab unit: never converted")

        def arm(row: dict[str, Any]) -> Arm:
            return Arm(requests=row["requests"], errors=row["errors"], p99_ms=row["p99_ms"])
        return Live(observed_until=datetime.fromisoformat(cand["observed_until"]),
                    baseline=arm(arms["baseline"]), candidate=arm(cand),
                    quality_covered=cand["quality_covered"],
                    spent=records.Amount(**cand["spent"]),
                    candidate_healthy=cand["candidate_healthy"])

    async def transition(self, policy_ref: str, *, fence: int, to: str,
                         decision: dict[str, Any], reasons: tuple[str, ...]) -> int:
        records.parse(decision)                 # the contract refuses first (LabRejected)
        return (await self._call("lab_release_transition", {
            "policy_ref": policy_ref, "fence": fence, "to": to, "decision": decision,
            "reasons": list(reasons)}))["fence"]


# --- WR-R4-2: release proposals (`0043_lab_reads_and_proposals.sql`) ------------------------
class PgReleaseProposals:
    """The Lab's proposals (R4 / WR-R4-1's route): one pending per release revision, filed at
    the fence the page showed; an operator's approval is 0039's CAS at that fence."""

    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def propose(self, policy_ref: str, *, provider_org_id: str, proposal_id: str,
                      kind: str, fence: int, proposed_by: str) -> dict[str, Any]:
        return await self._call("lab_propose_release", {
            "provider_org_id": provider_org_id, "proposal_id": proposal_id,
            "policy_ref": policy_ref, "kind": kind, "fence": fence, "proposed_by": proposed_by})

    async def decide(self, proposal_id: str, *, approve: bool, decided_by: str,
                     decision: dict[str, Any] | None = None,
                     reasons: tuple[str, ...] = ()) -> dict[str, Any]:
        if approve:
            records.parse(decision)             # the contract refuses first (LabRejected)
        return await self._call("lab_decide_release_proposal", {
            "proposal_id": proposal_id, "approve": approve, "decided_by": decided_by,
            "decision": decision, "reasons": list(reasons)})

    async def proposals(self, *, provider_org_id: str) -> list[dict[str, Any]]:
        return await self._call("lab_release_proposals", {"provider_org_id": provider_org_id})


# --- SR-R1-1: R1's routing ReleaseStore (0043, infrx_runtime only) ---------------------------
class PgRoutingReleases:
    """R1's `ReleaseStore` (`infrx.rollouts.routing`) over D9: the running head of an alias
    with its candidates' R62 pins, eligibility read now, and one assignment per admitted
    request. `connect` is the runtime's own login (infrx_runtime); nothing else may call it."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def _row(self, sql: str, params: tuple):
        from psycopg import Error

        from .jobstore import domain_error
        conn = await self._connect()
        try:
            return await (await conn.execute(sql, params)).fetchone()
        except Error as failed:
            raise domain_error(failed) from None
        finally:
            await conn.close()

    async def active(self, requested_model: str):
        row = await self._row("select record, policy_ref, revisions, shadow_limit from "
                              "infrx.release_active(%s)", (requested_model,))
        if row is None:
            return None
        from ..rollouts.routing import Release           # R1's (merged beside this)
        return Release(policy=records.parse(row[0]), policy_ref=row[1], revisions=row[2],
                       shadow_limit=row[3])

    async def eligible(self, policy_id: str, auth: Any) -> bool:
        return (await self._row("select infrx.release_eligible(%s, %s)",
                                (policy_id, auth.org_id)))[0]

    async def record(self, assignment) -> None:
        from psycopg.types.json import Jsonb
        await self._row("select infrx.record_rollout_assignment(%s)",
                        (Jsonb(assignment.model_dump(mode="json", by_alias=True)),))
