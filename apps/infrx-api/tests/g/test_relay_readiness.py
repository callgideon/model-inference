#!/usr/bin/env python3
"""W5 wiring 4 (gateway side): every admission goes through `ReadinessStore.admit_ready`.

    uv run --frozen pytest -q tests/g/test_relay_readiness.py

The relay composed as the pilot composes it (`World(readiness=True)`: D10's fake as the
readiness store, the ticket authority and the content lifecycle), and the worker's side
claimed behind the marker (`World.prepare` claims through the readiness store, as W5's
barrier does). The failure oracle throughout: the pre-D10 door (`jobs.admit`) admits with
no marker, so the claim is `not_ready` and nothing is prepared; a refusal after that door
leaves a job (cancelled) and a hold that was taken.
"""
from __future__ import annotations

import pytest

from infrx.config import RuntimeMisconfigured
from infrx.contracts.v2.lifecycle import (AdmissionExpectation, LifecycleRefusal as R, refuse)
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import JobState, SettlementState, Usage
from infrx.contracts.v2.money_units import CREDIT as CREDIT_UNIT
from infrx.gateway.routes.relay import CREDIT, ERROR_EVENT, LEGACY, Relay
from infrx.state.jobstore import PgJobStore

from . import relay_support as rs, support

REGIMES = [LEGACY, CREDIT]


def no_hold(world) -> bool:
    if world.regime == CREDIT:
        return all(world.jobs.credit_wallet(w).reserved_total == seeded
                   for w, seeded in world.seeded.items())
    return world.jobs.wallet(world.org).reserved_total == 0


async def uploaded(world) -> list:
    """A finalized upload of the scripted clip, named in a request as the product does."""
    handle = (await world.media.create_upload(world.org, {}))["upload_handle"]
    await world.media.put_upload(world.org, handle, rs.CLIP, "video/mp4")
    await world.media.finalize_upload(world.org, handle)
    return handle, [{"role": "user", "content": [
        {"type": "text", "text": "What happens?"},
        {"type": "video_url", "video_url": {"url": "infrx-upload:" + handle}}]}]


def prepared_then_bound(world, box: list):
    """While the gateway waits: W5's worker prepares the job behind its marker (`box` gets
    its id), then the clock passes the bound - so the wait ends in both regimes (504)."""
    async def step():
        box.append(await world.prepare())
        world.clock.advance(3_600)
    return step


def spy(world) -> list:
    """Record every expectation the relay hands `admit_ready`."""
    seen, admit_ready = [], world.lifecycle.admit_ready

    async def recording(request, idem, expectation):
        seen.append(expectation)
        return await admit_ready(request, idem, expectation)

    world.lifecycle.admit_ready = recording
    return seen


@pytest.mark.parametrize("regime", REGIMES)
@pytest.mark.parametrize("kind", ["text", "video"])
def test_w5_admit__an_admission_writes_the_marker_the_worker_claims_on(regime, kind):
    """Text and media alike, both regimes: the admitted job has its committed marker (a
    manifest naming the upload for video, an empty one for text), and the preparation claim
    behind it succeeds. Oracle: through `jobs.admit` there is no marker, the claim is
    `not_ready` and nothing is prepared."""
    world = rs.World(regime=regime, readiness=True)
    seen = spy(world)

    async def scenario():
        handle, messages = await uploaded(world) if kind == "video" else (None, rs.TEXT)
        world.during.append(prepared_then_bound(world, prepared))
        reply = await rs.call(world.app, rs.body(messages))
        job = world.only_job()
        return handle, reply, job, await world.lifecycle.readiness(job.id)

    prepared: list = []
    handle, reply, job, ready = rs.run(scenario())
    assert reply.status == 504 and prepared == [job.id], reply.body
    assert ready is not None and ready.job_id == job.id, ready
    assert [s.ref.handle for s in ready.sources] == ([handle] if handle else [])
    assert len(seen) == 1 and seen[0].accounting_regime.value == regime


def test_w5_admit__a_credit_expectation_names_the_approved_card():
    """R69: the CREDIT expectation carries `ACTIVE_RATE_CARD_VERSION`; legacy names none."""
    for regime, card in ((CREDIT, True), (LEGACY, False)):
        world = rs.World(regime=regime, readiness=True)
        seen = spy(world)
        world.during.append(lambda: world.clock.advance(3_600))
        assert rs.run(rs.call(world.app, rs.body())).status == 504
        assert len(seen) == 1, seen
        (expectation,) = seen
        assert expectation.rate_card_version == (world.card if card else None), expectation


def test_w5_admit__a_card_this_runtime_did_not_approve_admits_nothing():
    """`expectation_mismatch` inside the one transaction: 400 `invalid_request`, and no job,
    no hold. Oracle: the pre-D10 door admitted, took the hold, then cancelled the job."""
    world = rs.World(regime=CREDIT, readiness=True)
    world.card = "rc_not_approved_here"
    world.restart()
    world.during.append(lambda: world.clock.advance(3_600))    # a wait would end, not hang
    reply = rs.run(rs.call(world.app, rs.body()))
    assert (reply.status, reply.json()["error"]["code"]) == (400, "invalid_request"), reply.body
    assert world.jobs.jobs == {} and no_hold(world)


@pytest.mark.parametrize("regime", REGIMES)
def test_w5_admit__an_upload_past_its_window_at_admission_admits_nothing(regime):
    """The ticket is resolved inside `admit_ready`: an upload that expires between staging
    and admission is `upload_expired` (410) with no job and no hold."""
    world = rs.World(regime=regime, readiness=True)
    stage = world.media.stage

    async def late(org_id, request):
        refs = await stage(org_id, request)
        world.clock.advance(world.lifecycle.upload_ttl_s + 1)
        return refs

    world.media.stage = late
    world.during.append(lambda: world.clock.advance(3_600))    # a wait would end, not hang

    async def scenario():
        _, messages = await uploaded(world)
        return await rs.call(world.app, rs.body(messages))

    reply = rs.run(scenario())
    assert (reply.status, reply.json()["error"]["code"]) == (410, "upload_expired"), reply.body
    assert world.jobs.jobs == {} and no_hold(world)


@pytest.mark.parametrize("reason", [R.not_found, R.upload_expired, R.content_retiring,
                                    R.expectation_mismatch])
def test_w5_admit__a_refusal_is_its_wire_error_and_admits_nothing(reason):
    """Each `admit_ready` refusal is answered as its typed error (the lifecycle table) and
    nothing is admitted or held."""
    world = rs.World(regime=CREDIT, readiness=True)

    async def refusing(request, idem, expectation):
        raise refuse(reason, "refused by the store")

    world.lifecycle.admit_ready = refusing
    world.during.append(lambda: world.clock.advance(3_600))    # a wait would end, not hang
    reply = rs.run(rs.call(world.app, rs.body()))
    assert reply.json()["error"]["code"] == refuse(reason, "").code, reply.body
    assert world.jobs.jobs == {} and no_hold(world)


@pytest.mark.parametrize("regime", REGIMES)
def test_w5_admit__a_replay_admit_ready_answers_is_the_recorded_job(regime):
    """A replay the relay's lookup could not see yet (a concurrent first acceptance) is
    answered by `admit_ready` with the recorded pair: the same job, `Idempotency-Replayed`,
    no second job, the marker unchanged."""
    world = rs.World(regime=regime, readiness=True)
    world.during.append(prepared_then_bound(world, []))
    first = rs.run(rs.call(world.app, rs.body(), key="k-replay"))
    assert first.status == 504, first.body
    job = world.only_job()
    marker = world.lifecycle.d.readiness[job.id]
    lookup, blind = world.jobs.lookup, [True]

    async def once_blind(org_id, idem):                   # the relay's R91 lookup only
        if blind:
            blind.clear()
            return None
        return await lookup(org_id, idem)

    world.jobs.lookup = once_blind
    again = rs.run(rs.call(world.app, rs.body(), key="k-replay"))
    assert again.status == 504 and not blind, again.body
    assert again.headers["idempotency-replayed"] == "true"
    assert again.headers["inference-id"] == first.headers["inference-id"] == job.id
    assert list(world.jobs.jobs) == [job.id] and world.lifecycle.d.readiness[job.id] is marker


# --- the composition root ------------------------------------------------------------
class Store:
    """Shaped like a `ReadinessStore` (the protocol's three methods); never called here."""

    async def admit_ready(self, request, idem, expectation): ...

    async def readiness(self, job_id): ...

    async def claim_preparation(self, job_id, worker_id): ...


def composed(regime, *, card="", **kw):
    from .test_composition import composed as compose
    return compose(regime, card=card, **kw)


@pytest.mark.parametrize("regime", REGIMES)
def test_w5_compose__the_relay_gets_the_readiness_store_and_this_runtimes_expectation(regime):
    """`build_ingress_deps(readiness=)`: the relay admits through it, expecting this
    deployment's regime and (CREDIT) its `ACTIVE_RATE_CARD_VERSION`."""
    card = support.catalog().rate_cards[support.IDS.prod_deployment].rate_card_version
    marker = Store()
    rt, _ = composed(regime, card=card if regime == CREDIT else "", readiness=marker)
    assert rt.relay.readiness is marker
    assert rt.relay.expectation == AdmissionExpectation(
        accounting_regime=regime, rate_card_version=card if regime == CREDIT else None)


def test_w5_compose__a_postgresql_job_store_never_admits_without_a_readiness_store():
    """Fail closed: a PgJobStore composed with no readiness store (or a CREDIT one with no
    card, or a store that is not a ReadinessStore) refuses to start - it never falls back
    to `jobs.admit`, whose jobs W5's worker would never prepare."""
    with pytest.raises(RuntimeMisconfigured, match="ReadinessStore"):
        composed(LEGACY, jobs=PgJobStore(lambda: None))
    with pytest.raises(RuntimeMisconfigured, match="ReadinessStore"):
        composed(LEGACY, readiness=object())


def test_w5_compose__a_relay_refuses_an_expectation_that_is_not_its_own():
    """The relay itself: a readiness store with no expectation, another regime's, or another
    card than the approved one is a construction error, not an admission."""
    wrong = [None, AdmissionExpectation(accounting_regime=LEGACY),
             AdmissionExpectation(accounting_regime=CREDIT, rate_card_version="rc_other")]
    for expectation in wrong:
        with pytest.raises(ValueError, match="expectation"):
            Relay(jobs=object(), stream=None, media=None, regime=CREDIT,
                  active_rate_card_version="rc_approved", readiness=object(),
                  expectation=expectation)


# --- E3C F-5: past the point of no return ------------------------------------------------
def withdraw_text(world) -> None:
    """The pinned serving revision stops accepting text (E3C s04 `late`: the operator
    withdraws a modality after the admission transaction checked it)."""
    serving = world.catalog.servings[support.IDS.serving_version]
    capability = serving.capability.model_copy(update={"input_modalities": ["video"]})
    world.catalog.servings[serving.serving_version_id] = serving.model_copy(
        update={"capability": capability})


@pytest.mark.parametrize("mode", ["sync", "stream"])
def test_w5_f5__a_ready_job_is_answered_its_committed_outcome_never_a_late_refusal(mode):
    """E3C F-5. `admit_ready` checked the pinned card and capability inside the admission
    transaction (0019 `check_pinned_capability`) and wrote the marker: from then on the
    worker may claim, run and settle the job before the relay takes its next step. A
    capability withdrawn after that commit is not this job's refusal: the client is told the
    committed outcome, and the job is charged exactly once. Oracle: the legacy
    post-admission recheck (`_admitted` -> `check_capability`) answered 415
    `unsupported_media` for a job that had succeeded and settled its debit (E3C f61b82d3)."""
    world = rs.World(regime=CREDIT, readiness=True)
    admit_ready, ran = world.lifecycle.admit_ready, []

    async def ran_then_withdrawn(request, idem, expectation):
        admitted = await admit_ready(request, idem, expectation)
        # The worker reads the manifest the transaction committed (a text job's is empty),
        # never the gateway's attach - so it can run before the relay's next step.
        world.media.by_job[admitted[0].request_id] = ()
        lease = await world.lease()             # the worker got there first: ran, settled
        await world.commit(lease, "Two people")
        ref = await world.put_result(lease.job_id, "Two people")
        ran.append(await world.jobs.complete_credit(lease, b.outcome(
            lease.job_id, world, tokens=Usage.of(1200, 5), result_ref=ref)))
        withdraw_text(world)
        return admitted

    world.lifecycle.admit_ready = ran_then_withdrawn
    world.during.append(lambda: world.clock.advance(3_600))    # a wait would end, not hang
    ledgers = {w: world.jobs.credit_wallet(w).ledger_total for w in world.seeded}
    reply = rs.run(rs.call(world.app, rs.body(stream=mode == "stream")))
    job = world.only_job()
    assert ran and job.state is JobState.succeeded, (job.state, reply.body)
    assert reply.status == 200, reply.body
    if mode == "stream":
        assert ERROR_EVENT not in reply.events() and reply.data()[-1] == "[DONE]"
        assert reply.text() == "Two people", reply.body
    else:
        assert reply.json()["choices"][0]["message"]["content"] == "Two people"
    # Charged exactly once, in CREDIT: the one settlement at the admitted card, the hold off
    # the wallet and the ledger down by exactly that debit - never a refund or a second one.
    after = {w: world.jobs.credit_wallet(w).ledger_total for w in world.seeded}
    charged = job.settlement.charged.raw(CREDIT_UNIT)
    assert job.outcome.settlement_state is SettlementState.settled and world.released(job)
    assert {w: ledgers[w] - after[w] for w in ledgers} == \
        {w: charged if w == job.credit.wallet_id else 0 for w in ledgers} and charged > 0


def test_w5_f5__the_pre_d10_door_still_rechecks_after_admission():
    """The legacy door (`jobs.admit_credit`, no marker, nothing claimable until the refs
    are bound) keeps G1R Limit 2's recheck: a revision that stopped accepting text by
    admission time cancels the job unbilled and answers the refusal."""
    world = rs.World(regime=CREDIT)
    admit = world.jobs.admit_credit

    async def withdrawn(request, idem):
        withdraw_text(world)
        return await admit(request, idem)

    world.jobs.admit_credit = withdrawn
    world.during.append(lambda: world.clock.advance(3_600))
    reply = rs.run(rs.call(world.app, rs.body()))
    assert (reply.status, reply.json()["error"]["code"]) == (400, "unsupported_media"), \
        reply.body
    job = world.only_job()
    assert job.state is JobState.cancelled and world.released(job)
