"""R1 (ROLLOUT-PIN): stable assignment at admission, bounded canary, suppressed shadow.

The release store is D9's port, faked here (`Releases`); the shadow runner is the
provider-funded duplicate path, faked (`Shadows`). The relay cases compose the hook over
G2's real `Relay` on the contract fakes (`tests/g/relay_support.World`, CREDIT regime) and
prove the duplicate admits, holds, settles and journals nothing: the store's whole money
and journal state is the same as the same request with no hook at all.

    cd apps/infrx-api && uv run --frozen pytest -q tests/r/routing
"""
from __future__ import annotations

import asyncio
import types

import pytest
from starlette.responses import Response

from infrx.contracts import errors, wire
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FakeClock, SequentialIds
from infrx.contracts.lab import records as lab
from infrx.contracts.records import Usage
from infrx.contracts.v2 import records as v2
from infrx.gateway.routes.relay import CREDIT
from infrx.rollouts import routing

from tests.g import relay_support as rs, support

PROVIDER = "11111111-1111-4111-8111-111111111111"
# F3's accepted `rollout_policy` fixture (packages/shared/contracts/lab/fixtures.json), inline:
# the mutant runner's copy carries the API package and its tests only.
POLICY = {
    "schema": "lab.rollout_policy.1", "provider_org_id": PROVIDER,
    "policy_id": "0000006e-0000-4000-8000-00000000006e", "version": 3,
    "created_at": "2026-09-27T10:00:00Z", "endpoint_id": "0000006f-0000-4000-8000-00000000006f",
    "baseline_ref": f"lab:serving:{PROVIDER}:00000028-0000-4000-8000-000000000028@sha256:"
                    + "f" * 64,
    "mode": "canary", "cohort": "account",
    "candidates": [{"serving_ref": f"lab:serving:{PROVIDER}:00000029-0000-4000-8000-"
                                   "000000000029@sha256:" + "b" * 64, "weight_bp": 500}]}
CANDIDATE = POLICY["candidates"][0]["serving_ref"]
CANDIDATE_PIN = "nemostation/marlin-2b@candidate-1"
ALIAS = support.PUBLIC_MODEL


def release(*, weight=500, mode="canary", cohort="account", shadow_limit=0, pin=CANDIDATE_PIN,
            version=3):
    payload = {**POLICY, "mode": mode, "cohort": cohort, "version": version,
               "candidates": [{"serving_ref": CANDIDATE,
                               "weight_bp": weight if mode == "canary" else 0}]}
    return routing.Release(policy=lab.parse(payload), policy_ref=lab.ref_of(payload),
                           revisions={CANDIDATE: pin}, shadow_limit=shadow_limit)


class Releases:
    """D9's port: the active release for an alias, current eligibility, assignment rows."""

    def __init__(self, current=None, *, ineligible=(), down=False):
        self.current, self.ineligible, self.down = current, set(ineligible), down
        self.asked, self.recorded = [], []

    async def active(self, requested_model):
        self.asked.append(requested_model)
        if self.down:
            raise ConnectionError("release store unreachable")
        return self.current if requested_model == ALIAS else None

    async def eligible(self, policy_id, auth):
        return auth.org_id not in self.ineligible

    async def record(self, assignment):
        self.recorded.append(assignment)


class Shadows:
    def __init__(self, *, gate=None, fail=False):
        self.gate, self.fail, self.runs = gate, fail, []

    async def run(self, release, serving_ref, request):
        self.runs.append((serving_ref, request))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("candidate engine down")
        return "candidate output nobody may see"


def auth(org="0000aaaa-0000-4000-8000-%012d" % 1):
    return v2.AuthContextV2(audience=v2.CredentialAudience.consumer, org_id=org,
                            key_id=support.KEY, principal="user", role="owner",
                            entitlement_version=1, user_id=support.USER)


def orgs(n):
    return ["0000aaaa-0000-4000-8000-%012d" % i for i in range(n)]


def request(model=ALIAS, request_id="4d4d4d4d-0000-4000-8000-000000000004"):
    harness = types.SimpleNamespace(clock=FakeClock(), ids=SequentialIds())
    return b.request(harness, model_revision=model).model_copy(
        update={"request_id": request_id})


def route(router, who, model=ALIAS, request_id="4d4d4d4d-0000-4000-8000-000000000004"):
    return asyncio.run(router.route(who, request(model, request_id)))


# --- R1.a: stable assignment, explicit pins, eligibility ----------------------------------
def test_an_explicit_pin_is_served_as_asked_and_never_routed():
    releases = Releases(release(weight=10_000))
    router = routing.Router(releases, Shadows())
    asked = request(support.MODEL_REVISION)
    routed, shadows = asyncio.run(router.route(auth(), asked))
    assert routed is asked and shadows == ()
    assert releases.asked == [] and releases.recorded == []


def test_no_active_policy_or_mode_off_is_todays_request():
    for current in (None, release(mode="off")):
        releases = Releases(current)
        router = routing.Router(releases, Shadows())
        asked = request()
        assert asyncio.run(router.route(auth(), asked)) == (asked, ())
        assert releases.recorded == []


def test_a_repeat_request_keeps_its_cohort_and_each_admission_records_it():
    current = release(weight=5000)
    releases = Releases(current)
    router = routing.Router(releases, Shadows())
    arms = {}
    for org in orgs(40):
        seen = {route(router, auth(org), request_id=f"4d4d4d4d-0000-4000-8000-{n:012d}")[0]
                .model_revision for n in range(3)}
        assert len(seen) == 1, f"{org} drifted between retries: {seen}"
        arms[org] = seen.pop()
    assert set(arms.values()) == {ALIAS, CANDIDATE_PIN}      # both arms, as the policy says
    assert len(releases.recorded) == 120
    assert {a.request_id for a in releases.recorded} == {
        f"4d4d4d4d-0000-4000-8000-{n:012d}" for n in range(3)}
    for assignment in releases.recorded:
        assert assignment.policy_ref == current.policy_ref and assignment.pinned_by == "cohort"
        assert assignment.cohort_digest.startswith("sha256:")
        assert (assignment.serving_ref == CANDIDATE) == (arms_of(arms, assignment) == CANDIDATE_PIN)


def arms_of(arms, assignment):
    """The revision the router served the subject whose digest this assignment carries."""
    import hashlib
    for org, revision in arms.items():
        digest = hashlib.sha256(f"{POLICY['policy_id']}\n{org}".encode()).hexdigest()
        if assignment.cohort_digest == f"sha256:{digest}":
            return revision
    raise AssertionError("an assignment for a subject the router never saw")


def test_raising_a_weight_mid_session_keeps_every_candidate_subject():
    subjects = orgs(400)
    releases = Releases(release(weight=2000))
    router = routing.Router(releases, Shadows())
    before = {org: route(router, auth(org))[0].model_revision for org in subjects}
    releases.current = release(weight=6000, version=4)
    after = {org: route(router, auth(org))[0].model_revision for org in subjects}
    promised = [org for org in subjects if before[org] == CANDIDATE_PIN]
    assert promised and all(after[org] == CANDIDATE_PIN for org in promised)
    assert sum(r == CANDIDATE_PIN for r in after.values()) > len(promised)


def test_candidate_traffic_is_bounded_by_the_policy_weight():
    subjects = orgs(2000)
    router = routing.Router(Releases(release(weight=500)), Shadows())
    share = sum(route(router, auth(org))[0].model_revision == CANDIDATE_PIN
                for org in subjects) / len(subjects)
    assert 0.03 < share < 0.07
    router = routing.Router(Releases(release(weight=0)), Shadows())
    assert all(route(router, auth(org))[0].model_revision == ALIAS for org in subjects[:200])


def test_a_revoked_subject_returns_to_the_baseline_at_once():
    who = auth()
    releases = Releases(release(weight=10_000))
    router = routing.Router(releases, Shadows())
    assert route(router, who)[0].model_revision == CANDIDATE_PIN
    releases.ineligible.add(who.org_id)
    asked = request()
    assert asyncio.run(router.route(who, asked)) == (asked, ())
    assert len(releases.recorded) == 1                  # an ineligible subject is not assigned
    assert router.counts[(POLICY["policy_id"], "ineligible")] == 1


def test_a_session_cohort_without_a_declared_session_serves_the_baseline():
    releases = Releases(release(weight=10_000, cohort="session"))
    router = routing.Router(releases, Shadows())
    asked = request()
    assert asyncio.run(router.route(auth(), asked)) == (asked, ())
    assert releases.recorded == []


def test_a_release_store_failure_is_a_retryable_outage_never_a_silent_move():
    router = routing.Router(Releases(release(weight=10_000), down=True), Shadows())
    with pytest.raises(errors.DependencyUnavailable):
        route(router, auth())


def test_a_release_names_a_pinned_revision_for_every_candidate():
    with pytest.raises(ValueError):
        release(pin="nemostation/marlin-2b")                       # an alias moves; a pin does not
    with pytest.raises(ValueError):
        routing.Release(policy=release().policy, policy_ref=release().policy_ref, revisions={})


# --- R1.b/c: the hook, shadow suppression and its own limit --------------------------------
def answer(replayed=False):
    headers = {wire.HEADER_IDEMPOTENCY_REPLAYED: "true"} if replayed else {}
    return Response(b"baseline", headers=headers)


def hooked(router, answered=None, *, refuse=None):
    seen = []

    async def accept(who, asked, idem):
        seen.append(asked)
        if refuse is not None:
            raise refuse
        return answered or answer()

    return routing.hook(accept, router), seen


async def drain(router):
    while router.tasks:
        await asyncio.gather(*router.tasks)


def test_shadow_answers_the_baseline_and_runs_the_candidate_on_the_side():
    shadows = Shadows()
    router = routing.Router(Releases(release(mode="shadow", shadow_limit=4)), shadows)
    accept, seen = hooked(router)
    asked = request()

    async def go():
        reply = await accept(auth(), asked, None)
        await drain(router)
        return reply

    reply = asyncio.run(go())
    assert reply.body == b"baseline" and seen == [asked]
    assert [(ref, r.model_revision) for ref, r in shadows.runs] == [(CANDIDATE, CANDIDATE_PIN)]
    assert router.counts[(POLICY["policy_id"], "shadow")] == 1


def test_a_canary_is_never_shadowed():
    for weight, admitted in ((10_000, CANDIDATE_PIN), (0, ALIAS)):     # either arm
        shadows = Shadows()
        router = routing.Router(Releases(release(weight=weight, shadow_limit=4)), shadows)
        accept, seen = hooked(router)

        async def go():
            await accept(auth(), request(), None)
            await drain(router)

        asyncio.run(go())
        assert seen[0].model_revision == admitted and shadows.runs == []


def test_a_replayed_or_refused_admission_is_not_shadowed_again():
    shadows = Shadows()
    router = routing.Router(Releases(release(mode="shadow", shadow_limit=4)), shadows)
    replay, _ = hooked(router, answer(replayed=True))
    refused, _ = hooked(router, refuse=errors.InsufficientCredit())

    async def go():
        await replay(auth(), request(), None)
        with pytest.raises(errors.InsufficientCredit):
            await refused(auth(), request(), None)
        await drain(router)

    asyncio.run(go())
    assert shadows.runs == []


def test_shadow_work_is_bounded_by_its_own_limit():
    async def go():
        gate = asyncio.Event()
        shadows = Shadows(gate=gate)
        router = routing.Router(Releases(release(mode="shadow", shadow_limit=1)), shadows)
        accept, seen = hooked(router)
        for _ in range(3):
            await accept(auth(), request(), None)
        await asyncio.sleep(0)
        running = len(shadows.runs)
        gate.set()
        await drain(router)
        await accept(auth(), request(), None)            # the slot is free again
        await drain(router)
        return running, router, seen, shadows

    running, router, seen, shadows = asyncio.run(go())
    assert running == 1 and len(seen) == 4 and len(shadows.runs) == 2
    assert router.counts[(POLICY["policy_id"], "shadow_skipped")] == 2


def test_a_failing_shadow_never_reaches_the_caller():
    shadows = Shadows(fail=True)
    router = routing.Router(Releases(release(mode="shadow", shadow_limit=1)), shadows)
    accept, _ = hooked(router)

    async def go():
        first = await accept(auth(), request(), None)
        await drain(router)
        second = await accept(auth(), request(), None)
        await drain(router)
        return first, second

    first, second = asyncio.run(go())
    assert first.body == second.body == b"baseline" and len(shadows.runs) == 2
    assert router.counts[(POLICY["policy_id"], "shadow_failed")] == 2


# --- through G2's real relay: shadow never admits, holds, settles or journals ------------
def fingerprint(world):
    jobs = world.jobs
    return (len(jobs.jobs), len(jobs.holds), len(jobs.outbox), jobs.journal.total(),
            sorted((w.ledger_total, w.reserved_total) for w in jobs.credit_wallets.values()),
            sorted(len(chunks) for chunks in world.stream.chunks.values()),
            sorted(job.state.value for job in jobs.jobs.values()))


def served(router=None, *, model=ALIAS):
    """One sync request, worked to its end, through the relay (hooked when `router`)."""
    world = rs.World(regime=CREDIT)
    if router is not None:
        world.relay.accept = routing.hook(world.relay.accept, router)
        world.app, _ = support.cutover_app(
            world.config, clock=world.now_s, sb=support.supabase(rows=(world.row,)),
            ingress_deps=support.deps(accept=world.relay.accept, catalog=world.catalog))

    async def settle():
        """W2's CREDIT completion by hand: the lease, the journal, the result, one settle."""
        lease = await world.lease()
        await world.commit(lease, "Two people")
        ref = await world.put_result(lease.job_id, "Two people")
        await world.jobs.complete_credit(lease, b.outcome(
            lease.job_id, world, tokens=Usage.of(1200, 5), result_ref=ref))

    world.during += [settle, lambda: world.clock.advance(3_600)]   # a wait ends, never hangs

    async def go():
        reply = await rs.call(world.app, rs.body(model=model))
        if router is not None:
            await drain(router)
        return reply

    return world, asyncio.run(go())


def test_shadow_through_the_relay_admits_holds_and_settles_one_job_only():
    today, plain = served()
    shadows = Shadows()
    router = routing.Router(Releases(release(mode="shadow", shadow_limit=4)), shadows)
    world, reply = served(router)
    assert reply.status == plain.status == 200
    assert reply.json()["choices"] == plain.json()["choices"]          # the user's own output
    assert fingerprint(world) == fingerprint(today)
    assert world.only_job().settlement.charged == today.only_job().settlement.charged
    assert world.only_job().request.model_revision == ALIAS
    assert [r.model_revision for _, r in shadows.runs] == [CANDIDATE_PIN]


def test_a_canary_subject_is_admitted_and_priced_on_the_candidate_revision():
    router = routing.Router(Releases(release(weight=10_000, pin=support.MODEL_REVISION)),
                            Shadows())
    world, reply = served(router)
    assert reply.status == 200
    job = world.only_job()
    assert job.request.model_revision == support.MODEL_REVISION
    assert fingerprint(world)[:4] == fingerprint(served()[0])[:4]      # one job, one hold
