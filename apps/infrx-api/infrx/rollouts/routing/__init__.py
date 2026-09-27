"""R1 (ROLLOUT-PIN): bounded shadow, canary and A/B routing at admission.

`hook(accept, router)` wraps the relay's `accept(auth, request, idem)`; the coordinator
composes it only when the deployment turns `ROLLOUT_ROUTING` on (off by default, so the
launched API is today's). Per request, before admission:

* **An explicit R62 pin wins.** A requested `<model>@<revision>` is served as asked and the
  release store is not even asked; only an alias is routed.
* **Stable cohort.** The subject (the account; a `session` cohort has no declared session
  on the wire yet, so it serves the baseline) is bucketed by F3's `assign` over the policy
  id, so a retry lands the same and raising a weight only moves baseline subjects over.
  Eligibility is read *now* from the release store: a revoked subject is back on the
  baseline at its next request and is not assigned.
* **Canary / A/B bounded by the policy.** A candidate subject's request is admitted on the
  candidate's pinned revision, so admission resolves, prices and pins *that* revision (the
  rate is frozen in the admission as for any request). A baseline subject's request is
  untouched. Every eligible assignment is recorded with its request id (D9's rows).
* **Shadow is suppressed and never paid by the user.** In `shadow` mode the user's request
  is admitted unchanged; after a fresh (not replayed, not refused) admission each candidate
  gets a duplicate through the provider-funded `ShadowRunner`, whose result is discarded.
  The router holds no job store, journal or wallet: a duplicate cannot admit, hold, settle
  or charge (proved against G2's relay in `tests/r/routing`). Duplicates have their own
  bound, `Release.shadow_limit` in flight per policy; beyond it one is skipped, never queued.
  A tool call in a shadow answer is discarded with the answer (tools run client-side).

Telemetry is `Router.counts[(policy_id, arm)]`: no org, user or key, so a provider reading
coverage learns nothing about who. A release-store failure is a retryable 503, never a
silent move to the baseline (that would drift a promised cohort).
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ...contracts import errors, wire
from ...contracts.lab.records import RolloutAssignment, RolloutPolicy, assign

log = logging.getLogger("infrx.rollouts")


@dataclass(frozen=True)
class Release:
    """What D9's store answers for an alias: the current policy revision, its ref, the R62
    pin each candidate serving ref is admitted as (L3's published revisions), and the bound
    on provider-funded shadow duplicates in flight."""

    policy: RolloutPolicy
    policy_ref: str
    revisions: Mapping[str, str]
    shadow_limit: int = 0

    def __post_init__(self) -> None:
        for candidate in self.policy.candidates:
            pin = self.revisions.get(candidate.serving_ref, "")
            if "@" not in pin:
                raise ValueError(f"{candidate.serving_ref} needs a pinned revision, not {pin!r}")


class ReleaseStore(Protocol):
    """D9's port (schema request SR-R1-1)."""

    async def active(self, requested_model: str) -> Release | None: ...

    async def eligible(self, policy_id: str, auth: Any) -> bool: ...

    async def record(self, assignment: RolloutAssignment) -> None: ...


class ShadowRunner(Protocol):
    """Provider-funded duplicate execution; whatever it returns is discarded."""

    async def run(self, release: Release, serving_ref: str, request: Any) -> Any: ...


class Router:
    def __init__(self, releases: ReleaseStore, shadows: ShadowRunner) -> None:
        self.releases, self.shadows = releases, shadows
        self.counts: Counter = Counter()
        self.inflight: Counter = Counter()
        self.tasks: set[asyncio.Task] = set()

    async def route(self, auth, request):
        """(the request to admit, the shadow duplicates to run once it is admitted)."""
        if "@" in request.model_revision:
            return request, ()
        try:
            release = await self.releases.active(request.model_revision)
            if release is None or release.policy.mode == "off":
                return request, ()
            policy = release.policy
            if policy.cohort != "account" or not await self.releases.eligible(policy.policy_id,
                                                                              auth):
                self.counts[(policy.policy_id, "ineligible")] += 1
                return request, ()
            assignment = assign(policy, release.policy_ref, auth.org_id, request.request_id)
            await self.releases.record(assignment)
        except Exception:
            log.exception("release store failed routing %s", request.request_id)
            raise errors.DependencyUnavailable("the release store did not answer") from None
        if assignment.serving_ref != policy.baseline_ref:
            self.counts[(policy.policy_id, "candidate")] += 1
            pin = release.revisions[assignment.serving_ref]
            return request.model_copy(update={"model_revision": pin}), ()
        self.counts[(policy.policy_id, "baseline")] += 1
        if policy.mode == "shadow":
            return request, tuple((release, c.serving_ref) for c in policy.candidates)
        return request, ()

    def shadow(self, pending, request) -> None:
        """Start each duplicate within its policy's bound; never awaited by the caller."""
        for release, serving_ref in pending:
            policy_id = release.policy.policy_id
            if self.inflight[policy_id] >= release.shadow_limit:
                self.counts[(policy_id, "shadow_skipped")] += 1
                continue
            self.inflight[policy_id] += 1
            duplicate = request.model_copy(
                update={"model_revision": release.revisions[serving_ref]})
            task = asyncio.create_task(self._run(release, serving_ref, duplicate))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _run(self, release, serving_ref, duplicate) -> None:
        policy_id = release.policy.policy_id
        try:
            await self.shadows.run(release, serving_ref, duplicate)   # the output is dropped
            self.counts[(policy_id, "shadow")] += 1
        except Exception:
            log.exception("shadow duplicate of %s failed", duplicate.request_id)
            self.counts[(policy_id, "shadow_failed")] += 1
        finally:
            self.inflight[policy_id] -= 1


def hook(accept, router: Router):
    """The relay's `accept`, routed. Shadows start only after a fresh admission."""

    async def routed(auth, request, idem):
        admitted, pending = await router.route(auth, request)
        answer = await accept(auth, admitted, idem)
        if pending and answer.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) != "true":
            router.shadow(pending, request)
        return answer

    return routed
